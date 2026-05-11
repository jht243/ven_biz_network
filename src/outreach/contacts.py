"""Scrape public pages for one usable outreach email.

RULE: Contact discovery ALWAYS runs for every prospect, regardless of whether
the main backlink source page could be crawled. We probe /contact, /about,
/team, /editorial, etc. on both the source URL's host and the apex domain.
A failed article crawl (403, timeout, DNS) must never prevent us from
finding a contact email on other pages of the same site.

FALLBACK: If no email is found on the website, we query public WHOIS/RDAP
records for the domain registrant email.
"""

from __future__ import annotations

import html
import json
import logging
import re
import socket
from urllib.parse import urlparse

import httpx

from src.outreach.crawler import USER_AGENTS

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+\s*(?:@|\s+\[at\]\s+|\s+at\s+)\s*[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
SKIP_PREFIXES = ("noreply", "no-reply", "donotreply", "privacy", "abuse", "support")
WHOIS_SKIP_DOMAINS = (
    "whoisguard.com", "contactprivacy.com", "domainsbyproxy.com",
    "withheldforprivacy.com", "privacyprotect.org", "whoisprotectservice.com",
    "whoisprivacyprotect.com", "privacyguardian.org", "identity-protect.org",
    "1and1-private-registration.com", "registryprivacy.com",
    "web.com", "networksolutions.com", "godaddy.com", "namecheap.com",
    "tucows.com", "enom.com", "register.com", "name.com", "dynadot.com",
    "gandi.net", "hover.com", "porkbun.com", "cloudflare.com",
    "amazon.com", "digitalregistra.co.id", "nexigen.digital",
)
WHOIS_SKIP_LOCAL_EXACT = frozenset(
    ("registrar", "hostmaster", "dnsadmin", "whois", "registry-abuse", "domain.operations"),
)
WHOIS_SKIP_LOCAL_PREFIX = ("registry-", "dns-",)
PREFERRED_PREFIXES = ("editor", "news", "contact", "hello", "info", "team", "press")
CONTACT_PATHS = ("", "/contact", "/contact-us", "/about", "/about-us", "/team", "/editorial")


def _normalize_domain(domain: str) -> str:
    raw = domain.strip()
    if raw.startswith("http"):
        raw = urlparse(raw).netloc
    return raw.lower().removeprefix("www.").strip("/")


def _email_candidates(text: str, domain: str) -> list[str]:
    decoded = html.unescape(text or "")
    decoded = decoded.replace("[at]", "@").replace(" at ", "@")
    emails = []
    for match in EMAIL_RE.findall(decoded):
        email = re.sub(r"\s+", "", match).lower()
        if "@" not in email:
            continue
        local, host = email.split("@", 1)
        if any(local.startswith(prefix) for prefix in SKIP_PREFIXES):
            continue
        if _is_disallowed_registry_or_privacy_email(email):
            continue
        if _is_bogus_scraped_email(email):
            continue
        if "." not in host:
            continue
        emails.append(email)

    seen = set()
    unique = []
    for email in emails:
        if email not in seen:
            seen.add(email)
            unique.append(email)

    domain = _normalize_domain(domain)
    unique.sort(key=lambda e: (
        not e.endswith("@" + domain),
        not e.split("@", 1)[0].startswith(PREFERRED_PREFIXES),
        len(e),
    ))
    return unique


def _domain_resolves(domain: str) -> bool:
    try:
        socket.getaddrinfo(domain, 443)
        return True
    except OSError:
        return False


def _origins_from_source_url(source_url: str | None) -> list[str]:
    """Origins under the same host as the backlink (priority when article URL 403s)."""
    if not source_url:
        return []
    p = urlparse(source_url)
    if not p.netloc:
        return []
    scheme = p.scheme if p.scheme in ("http", "https") else "https"
    primary = f"{scheme}://{p.netloc}"
    out = [primary]
    if scheme == "http":
        out.append(f"https://{p.netloc}")
    return out


def _origins_for_domain(domain: str) -> list[str]:
    if not domain:
        return []
    return [
        f"https://{domain}",
        f"https://www.{domain}",
        f"http://{domain}",
    ]


def _merge_origins(*lists: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for lst in lists:
        for item in lst:
            key = item.rstrip("/")
            if key not in seen:
                seen.add(key)
                out.append(item)
    return out


def _any_host_resolves(origins: list[str]) -> bool:
    for o in origins:
        parsed = urlparse(o)
        host = parsed.hostname
        if host and _domain_resolves(host):
            return True
    return False


def _is_disallowed_registry_or_privacy_email(email: str) -> bool:
    """Block privacy forwarders, registrar contacts, and registry ops addresses."""
    if not email or "@" not in email:
        return True
    local, _, host = email.partition("@")
    local_l = local.lower()
    host_l = host.lower()
    if local_l in WHOIS_SKIP_LOCAL_EXACT:
        return True
    if any(local_l.startswith(p) for p in WHOIS_SKIP_LOCAL_PREFIX):
        return True
    if any(host_l == d or host_l.endswith("." + d) for d in WHOIS_SKIP_DOMAINS):
        return True
    return False


# TLDs that are almost never real email hosts (asset extensions mistaken for domains).
_BOGUS_EMAIL_TLDS = frozenset({
    "png", "jpg", "jpeg", "gif", "svg", "webp", "ico", "pdf", "avif", "mp4",
    "css", "js", "json", "xml", "html", "woff", "woff2", "ttf",
})


def _is_bogus_scraped_email(email: str) -> bool:
    """Reject filenames, sentry DSN-shaped locals, and other junk as 'emails'."""
    if not email or "@" not in email:
        return True
    local, _, host = email.partition("@")
    if "." in host:
        tld = host.rsplit(".", 1)[-1].lower()
        if tld in _BOGUS_EMAIL_TLDS:
            return True
    if host.lower() == "sentry.io" and re.fullmatch(r"[a-f0-9]{20,}", local, re.I):
        return True
    if re.search(r"\.(png|jpg|jpeg|gif|svg|webp|avif|ico|pdf)$", local, re.I):
        return True
    return False


def _whois_email_rdap(domain: str, *, timeout: int = 10) -> str | None:
    """Query RDAP (the modern WHOIS replacement) for a registrant email.

    RDAP is free, structured JSON, and doesn't require parsing raw text.
    We try the domain's TLD RDAP server via the IANA bootstrap, then
    fall back to rdap.org as a universal proxy.
    """
    urls = [
        f"https://rdap.org/domain/{domain}",
    ]
    headers = {"Accept": "application/rdap+json"}
    for url in urls:
        try:
            resp = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
            if resp.status_code >= 400:
                continue
            data = resp.json()
        except Exception:
            continue

        for entity in data.get("entities", []):
            vcard = entity.get("vcardArray")
            if not vcard or len(vcard) < 2:
                continue
            for field in vcard[1]:
                if len(field) >= 4 and field[0] == "email":
                    email = str(field[3]).lower().strip()
                    if "@" in email and not _is_disallowed_registry_or_privacy_email(email):
                        local = email.split("@")[0]
                        if not any(local.startswith(p) for p in SKIP_PREFIXES):
                            return email

            for sub in entity.get("entities", []):
                sub_vcard = sub.get("vcardArray")
                if not sub_vcard or len(sub_vcard) < 2:
                    continue
                for field in sub_vcard[1]:
                    if len(field) >= 4 and field[0] == "email":
                        email = str(field[3]).lower().strip()
                        if "@" in email and not _is_disallowed_registry_or_privacy_email(email):
                            local = email.split("@")[0]
                            if not any(local.startswith(p) for p in SKIP_PREFIXES):
                                return email
    return None


def _whois_email_text(domain: str, *, timeout: int = 10) -> str | None:
    """Fallback: scrape a web-based WHOIS service for an email address."""
    url = f"https://www.whois.com/whois/{domain}"
    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": USER_AGENTS[0]},
            timeout=timeout,
            follow_redirects=True,
        )
        if resp.status_code >= 400:
            return None
    except Exception:
        return None

    candidates = _email_candidates(resp.text, domain)
    for c in candidates:
        if not _is_disallowed_registry_or_privacy_email(c):
            return c
    return None


def _whois_lookup(domain: str) -> str | None:
    """Try RDAP first, then web WHOIS scrape. Returns one usable email or None."""
    domain = _normalize_domain(domain)
    if not domain:
        return None
    email = _whois_email_rdap(domain)
    if email:
        logger.info("WHOIS/RDAP email for %s: %s", domain, email)
        return email
    email = _whois_email_text(domain)
    if email:
        logger.info("WHOIS web email for %s: %s", domain, email)
        return email
    return None


def find_contact_email(
    domain: str,
    *,
    source_url: str | None = None,
    timeout: int = 10,
) -> str | None:
    """Find one public contact email by scraping likely contact pages.

    Strategy (in order):
    1. Scrape /contact, /about, /team, etc. on the source URL's host
    2. Scrape the same paths on the apex prospect domain
    3. Query RDAP (modern WHOIS) for registrant email
    4. Scrape web-based WHOIS for registrant email

    Uses the main crawler's User-Agent to reduce 403s.
    """
    domain = _normalize_domain(domain)
    origins = _merge_origins(
        _origins_from_source_url(source_url),
        _origins_for_domain(domain),
    )

    # Step 1-2: Website scrape
    if origins and _any_host_resolves(origins):
        headers = {"User-Agent": USER_AGENTS[0]}
        seen_urls: set[str] = set()
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
            for base in origins:
                for path in CONTACT_PATHS:
                    url = base.rstrip("/") + (path or "")
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    try:
                        resp = client.get(url)
                    except Exception as exc:
                        logger.debug("Contact scrape failed for %s: %s", url, exc)
                        continue
                    if resp.status_code >= 400 or "html" not in resp.headers.get("content-type", "").lower():
                        continue
                    candidates = _email_candidates(resp.text, domain)
                    if candidates:
                        return candidates[0]

    # Step 3-4: WHOIS/RDAP fallback
    whois_email = _whois_lookup(domain)
    if whois_email:
        return whois_email

    return None

