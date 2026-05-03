"""Scrape public pages for one usable outreach email."""

from __future__ import annotations

import html
import logging
import re
import socket
from urllib.parse import urlparse

import httpx

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


def find_contact_email(domain: str, *, timeout: int = 10) -> str | None:
    """Find one public contact email by scraping likely contact pages."""
    domain = _normalize_domain(domain)
    if not domain or not _domain_resolves(domain):
        return None

    bases = [f"https://{domain}", f"https://www.{domain}", f"http://{domain}"]
    headers = {"User-Agent": "CaracasResearchBot/1.0 (+https://www.caracasresearch.com)"}
    seen_urls = set()
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
        for base in bases:
            for path in CONTACT_PATHS:
                url = base.rstrip("/") + path
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

