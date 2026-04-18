"""
IndexNow client.

The IndexNow protocol (https://www.indexnow.org/) is a single HTTP POST
that notifies every participating search engine — Bing, Yandex, Seznam,
Naver, Mojeek, and any future participants — about a URL change at once.
Submission to indexnow.org is forwarded to all participants.

Authentication is by domain ownership, proven by hosting a key file at
the site root (/{key}.txt). No OAuth, no service accounts. The key is
deliberately not a secret — the protocol publicly references the key
URL on every API call. A constant value in code is the intended use.

Quotas are generous (10,000 URLs/day per host as of 2024) and well above
anything we'll generate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

import httpx

from src.config import settings


logger = logging.getLogger(__name__)


_ENDPOINT = "https://api.indexnow.org/indexnow"

INDEXNOW_KEY = "0b2fff2a4cb56ba2c10382745f51cdd8"


@dataclass
class IndexNowResult:
    success: bool
    status_code: int | None
    response_snippet: str
    submitted: int


def _host() -> str:
    """Bare host (no scheme, no path) for the IndexNow payload."""
    base = settings.site_url.rstrip("/")
    if "://" in base:
        base = base.split("://", 1)[1]
    return base.split("/", 1)[0]


def _key_location() -> str:
    return f"{settings.site_url.rstrip('/')}/{INDEXNOW_KEY}.txt"


def submit_urls(urls: Iterable[str]) -> IndexNowResult:
    """Submit one or more URLs in a single POST. The protocol accepts up
    to 10,000 URLs per call but we typically pass a handful per cron."""
    url_list = [u for u in urls if u]
    if not url_list:
        return IndexNowResult(success=True, status_code=None, response_snippet="no urls", submitted=0)

    payload = {
        "host": _host(),
        "key": INDEXNOW_KEY,
        "keyLocation": _key_location(),
        "urlList": url_list,
    }

    try:
        resp = httpx.post(
            _ENDPOINT,
            json=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=15,
        )
    except Exception as exc:
        logger.warning("indexnow: HTTP error: %s", exc)
        return IndexNowResult(
            success=False,
            status_code=None,
            response_snippet=f"http error: {exc}"[:500],
            submitted=0,
        )

    snippet = (resp.text or "")[:500]
    if resp.status_code in (200, 202):
        logger.info("indexnow: %d for %d URLs", resp.status_code, len(url_list))
        return IndexNowResult(
            success=True,
            status_code=resp.status_code,
            response_snippet=snippet or "ok",
            submitted=len(url_list),
        )

    logger.warning("indexnow: %d -- %s", resp.status_code, snippet)
    return IndexNowResult(
        success=False,
        status_code=resp.status_code,
        response_snippet=snippet,
        submitted=0,
    )
