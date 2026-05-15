"""
Live scraper for OFAC Venezuela general licenses.

OFAC does not expose a tidy JSON endpoint for country-program general
licenses, so this scraper reads the public Venezuela sanctions page and
extracts links that look like Venezuela general-license documents. The
structured cache it writes is what powers /tools/ofac-venezuela-general-licenses.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.config import settings
from src.scraper.base import BaseScraper, ScrapedArticle, ScrapeResult

logger = logging.getLogger(__name__)

OFAC_VENEZUELA_URL = (
    "https://ofac.treasury.gov/sanctions-programs-and-country-information/"
    "venezuela-related-sanctions"
)
OFAC_RECENT_ACTIONS_URL = "https://ofac.treasury.gov/recent-actions"
CACHE_DIR = settings.storage_dir / "ofac_general_licenses"
LATEST_CACHE_PATH = CACHE_DIR / "latest.json"

_GL_RE = re.compile(
    r"(?:general\s+license|gl)\s*(?:no\.\s*)?([0-9]{1,3}[A-Z]?)",
    re.IGNORECASE,
)
_VENEZUELA_RE = re.compile(r"venezuela|venezuelan|pdvsa|citgo|chevron", re.IGNORECASE)


class OFACGeneralLicensesScraper(BaseScraper):
    """Scrape and cache OFAC's Venezuela general-license links."""

    def get_source_id(self) -> str:
        return "ofac_general_licenses"

    def scrape(self, target_date: Optional[date] = None) -> ScrapeResult:
        start = time.time()
        target_date = target_date or date.today()

        try:
            live = self.fetch_live_licenses()
            previous = load_cached_general_licenses()
            previous_by_number = {
                _license_key(item): item for item in previous.get("licenses", [])
            }
            new_or_changed = [
                item
                for item in live["licenses"]
                if previous_by_number.get(_license_key(item), {}).get("fingerprint")
                != item.get("fingerprint")
            ]

            write_general_license_cache(live)

            articles = [
                ScrapedArticle(
                    headline=f"OFAC Venezuela General License updated: {item['number']}",
                    published_date=target_date,
                    source_url=item["ofac_url"],
                    body_text=(
                        f"{item['number']}: {item.get('title', '')}\n"
                        f"Source: {item.get('source_page', OFAC_VENEZUELA_URL)}"
                    ),
                    source_name="OFAC",
                    source_credibility="official",
                    article_type="OFAC General License",
                    extra_metadata=item,
                )
                for item in new_or_changed
            ]

            logger.info(
                "OFAC GL: cached %d live Venezuela general licenses (%d new/changed)",
                len(live["licenses"]),
                len(new_or_changed),
            )
            return ScrapeResult(
                source=self.get_source_id(),
                success=True,
                articles=articles,
                duration_seconds=int(time.time() - start),
            )
        except Exception as exc:
            logger.error("OFAC general-license scrape failed: %s", exc, exc_info=True)
            return ScrapeResult(
                source=self.get_source_id(),
                success=False,
                error=str(exc),
                duration_seconds=int(time.time() - start),
            )

    def fetch_live_licenses(self) -> dict:
        """Return a normalized cache payload from OFAC public pages."""
        collected: dict[str, dict] = {}
        for source_url in (OFAC_VENEZUELA_URL, OFAC_RECENT_ACTIONS_URL):
            resp = self._fetch(source_url)
            for item in _extract_license_links(resp.text, source_url):
                key = _license_key(item)
                existing = collected.get(key)
                if existing is None or item.get("specificity", 0) > existing.get("specificity", 0):
                    collected[key] = item

        licenses = sorted(
            (_finalize_license(item) for item in collected.values()),
            key=lambda item: _license_sort_key(item["number"]),
        )
        fingerprint = _payload_fingerprint(licenses)
        return {
            "source": "live",
            "program": "venezuela",
            "source_urls": [OFAC_VENEZUELA_URL, OFAC_RECENT_ACTIONS_URL],
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "fingerprint": fingerprint,
            "licenses": licenses,
        }


def _extract_license_links(html: str, source_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    found: list[dict] = []

    for a in soup.select("a[href]"):
        text = " ".join(a.get_text(" ", strip=True).split())
        href = a.get("href") or ""
        surrounding = _surrounding_text(a)
        haystack = " ".join([text, href, surrounding])

        number = _extract_gl_number(haystack)
        if not number:
            continue
        if not _is_venezuela_license(haystack, source_url):
            continue

        ofac_url = urljoin(source_url, href)
        title = text or _title_from_haystack(haystack, number)
        if _looks_like_federal_register_notice(title):
            continue
        found.append(
            {
                "number": number,
                "title": title,
                "summary": "",
                "expires": "See OFAC text",
                "scope": _infer_scope(haystack),
                "ofac_url": ofac_url,
                "context": "",
                "source_page": source_url,
                "specificity": _specificity_score(haystack, ofac_url),
            }
        )

    return found


def _surrounding_text(a) -> str:
    parent = a.find_parent(["li", "p", "tr", "div"])
    if not parent:
        return ""
    return " ".join(parent.get_text(" ", strip=True).split())


def _extract_gl_number(text: str) -> str | None:
    match = _GL_RE.search(text or "")
    if not match:
        return None
    return f"GL {match.group(1).upper()}"


def _is_venezuela_license(text: str, source_url: str) -> bool:
    lowered = (text or "").lower()
    if "cuba" in lowered and not _VENEZUELA_RE.search(text or ""):
        return False
    if "venezuela-related-sanctions" in source_url:
        return True
    return bool(_VENEZUELA_RE.search(text or ""))


def _title_from_haystack(text: str, number: str) -> str:
    compact = " ".join((text or "").split())
    if compact:
        return compact[:180]
    return f"OFAC Venezuela {number}"


def _looks_like_federal_register_notice(title: str) -> bool:
    return bool(re.match(r"^\s*\d+\s+FR\s+\d+", title or "", re.IGNORECASE))


def _infer_scope(text: str) -> list[str]:
    lowered = (text or "").lower()
    scopes: list[str] = []
    checks = [
        ("oil-gas", ("oil", "gas", "petroleum", "pdvsa", "chevron")),
        ("energy", ("energy", "pdvsa", "citgo", "chevron", "petroleum")),
        ("debt", ("bond", "debt", "securities", "pdvsa 2020")),
        ("mining", ("gold", "mining", "minero")),
        ("legal", ("settlement", "court", "litigation", "judgment")),
        ("wind-down", ("wind down", "wind-down")),
    ]
    for scope, needles in checks:
        if any(needle in lowered for needle in needles):
            scopes.append(scope)
    return scopes or ["general"]


def _specificity_score(text: str, ofac_url: str) -> int:
    lowered = (text + " " + ofac_url).lower()
    score = 0
    if "download" in lowered or "/media/" in lowered:
        score += 3
    if "general license" in lowered:
        score += 2
    if "venezuela" in lowered or "pdvsa" in lowered:
        score += 2
    return score


def _finalize_license(item: dict) -> dict:
    normalized = {
        "number": item["number"],
        "title": item.get("title") or f"OFAC Venezuela {item['number']}",
        "summary": item.get("summary") or "Live OFAC listing. Open the official text for scope, conditions, and expiration details.",
        "expires": item.get("expires") or "See OFAC text",
        "scope": item.get("scope") or ["general"],
        "ofac_url": item["ofac_url"],
        "context": item.get("context") or "Detected from OFAC's public Venezuela sanctions and recent-actions pages.",
        "source_page": item.get("source_page") or OFAC_VENEZUELA_URL,
        "source": "live",
    }
    normalized["fingerprint"] = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return normalized


def _license_key(item: dict) -> str:
    return (item.get("number") or "").strip().upper()


def _license_sort_key(number: str) -> tuple[int, str]:
    match = re.search(r"(\d+)([A-Z]?)", number or "")
    if not match:
        return (9999, number or "")
    return (int(match.group(1)), match.group(2))


def _payload_fingerprint(licenses: list[dict]) -> str:
    stable = [
        {k: item.get(k) for k in ("number", "title", "ofac_url", "fingerprint")}
        for item in licenses
    ]
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def write_general_license_cache(payload: dict) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dated = CACHE_DIR / f"ofac_venezuela_gl_{date.today().isoformat()}.json"
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    dated.write_text(text, encoding="utf-8")
    LATEST_CACHE_PATH.write_text(text, encoding="utf-8")
    return LATEST_CACHE_PATH


def load_cached_general_licenses() -> dict:
    if not LATEST_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(LATEST_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not load OFAC GL cache: %s", exc)
        return {}
