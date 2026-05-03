"""Source-page crawling for backlink outreach prospects."""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENTS = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
)

SPAM_REJECT_TERMS = (
    "casino",
    "porn",
    "adult",
    "viagra",
    "cialis",
    "pharma",
    "payday loan",
    "malware",
    "torrent",
    "pbn",
)


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _looks_spammy(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in SPAM_REJECT_TERMS)


LANG_INDICATORS: list[tuple[str, tuple[str, ...]]] = [
    ("es", ("también", "según", "gobierno", "países", "información", "política", "través", "años")),
    ("pt", ("também", "governo", "países", "informação", "através", "política", "então")),
    ("fr", ("également", "gouvernement", "informations", "politique", "années", "très", "être")),
    ("de", ("auch", "regierung", "informationen", "jahre", "über", "können", "werden")),
    ("it", ("anche", "governo", "informazioni", "politica", "anni", "attraverso", "essere")),
    ("nl", ("ook", "regering", "informatie", "jaren", "politiek", "kunnen", "worden")),
    ("id", ("juga", "pemerintah", "tahun", "dalam", "dengan", "untuk", "yang")),
    ("cs", ("také", "vláda", "politika", "informace", "který", "byla", "jsou")),
    ("sv", ("också", "regering", "politik", "information", "genom", "efter", "hade")),
]

TLD_LANG_MAP = {
    ".es": "es", ".mx": "es", ".ar": "es", ".co": "es", ".ve": "es",
    ".cl": "es", ".pe": "es", ".ec": "es",
    ".br": "pt", ".pt": "pt",
    ".fr": "fr", ".de": "de", ".it": "it", ".nl": "nl",
    ".se": "sv", ".cz": "cs", ".id": "id",
}


def _detect_language(text: str, html_lang: str, url: str) -> str:
    """Best-effort language detection from html lang, text, and TLD."""
    if html_lang:
        code = html_lang.split("-")[0].strip().lower()
        if len(code) == 2:
            return code

    lowered = text[:3000].lower()
    best_lang, best_hits = "en", 0
    for lang, markers in LANG_INDICATORS:
        hits = sum(1 for m in markers if m in lowered)
        if hits > best_hits:
            best_lang, best_hits = lang, hits
    if best_hits >= 3:
        return best_lang

    host = urlparse(url).netloc.lower()
    for tld, lang in TLD_LANG_MAP.items():
        if host.endswith(tld):
            return lang

    return "en"


def crawl_source_page(url: str, *, timeout: int = 15) -> dict:
    """Fetch a prospect source page and extract text/link context."""
    headers = {"User-Agent": USER_AGENTS[0]}
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
            resp = client.get(url)
            resp.raise_for_status()
    except Exception as exc:
        logger.warning("Could not crawl %s: %s", url, exc)
        return {
            "url": url,
            "title": "",
            "text": "",
            "meta_description": "",
            "links": [],
            "language": "",
            "hard_reject": True,
            "error": str(exc),
        }

    content_type = resp.headers.get("content-type", "")
    if "html" not in content_type.lower():
        return {
            "url": str(resp.url),
            "title": "",
            "text": "",
            "meta_description": "",
            "links": [],
            "language": "",
            "hard_reject": True,
            "error": f"Unsupported content type: {content_type}",
        }

    soup = BeautifulSoup(resp.text, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()

    title = _normalize_space(soup.title.get_text(" ")) if soup.title else ""
    meta_tag = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    meta_description = _normalize_space(meta_tag.get("content", "")) if meta_tag else ""
    html_lang = (soup.html.get("lang", "") if soup.html else "").lower().strip()
    text = _normalize_space(soup.get_text(" "))
    language = _detect_language(text, html_lang, str(resp.url))
    links = []
    for a in soup.find_all("a", href=True):
        href = urljoin(str(resp.url), a.get("href", ""))
        label = _normalize_space(a.get_text(" "))
        links.append({"href": href, "text": label, "rel": " ".join(a.get("rel", []))})

    return {
        "url": str(resp.url),
        "title": title,
        "text": text,
        "meta_description": meta_description,
        "links": links,
        "language": language,
        "hard_reject": _looks_spammy(text),
        "error": "",
    }

