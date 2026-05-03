"""Scrape public pages for one usable outreach email.

RULE: Contact discovery ALWAYS runs for every prospect, regardless of whether
the main backlink source page could be crawled. We probe /contact, /about,
/team, /editorial, etc. on both the source URL's host and the apex domain.
A failed article crawl (403, timeout, DNS) must never prevent us from
finding a contact email on other pages of the same site.
"""

from __future__ import annotations

import html
import logging
import re
import socket
from urllib.parse import urlparse

import httpx

from src.outreach.crawler import USER_AGENTS

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+\s*(?:@|\s+\[at\]\s+|\s+at\s+)\s*[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
SKIP_PREFIXES = ("noreply", "no-reply", "donotreply", "privacy", "abuse", "support")
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


def find_contact_email(
    domain: str,
    *,
    source_url: str | None = None,
    timeout: int = 10,
) -> str | None:
    """Find one public contact email by scraping likely contact pages.

    Tries the backlink source URL's host first (same subdomain/CDN as the page),
    then the normalized prospect domain. Uses the main crawler's User-Agent.
    """
    domain = _normalize_domain(domain)
    origins = _merge_origins(
        _origins_from_source_url(source_url),
        _origins_for_domain(domain),
    )
    if not origins or not _any_host_resolves(origins):
        return None

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
    return None

