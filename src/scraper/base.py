from __future__ import annotations

import hashlib
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ScrapedGazette:
    """Intermediate representation of a gazette found by a scraper."""

    gazette_number: Optional[str]
    gazette_type: str  # "ordinaria" or "extraordinaria"
    published_date: date
    source: str
    source_url: str
    title: Optional[str] = None
    sumario_text: Optional[str] = None
    pdf_download_url: Optional[str] = None
    pdf_path: Optional[str] = None
    pdf_hash: Optional[str] = None


@dataclass
class ScrapedNews:
    """Intermediate representation of a National Assembly news item."""

    headline: str
    published_date: date
    source_url: str
    body_text: Optional[str] = None
    commission: Optional[str] = None


@dataclass
class ScrapedArticle:
    """Generic article from any external source (Federal Register, GDELT, etc.)."""

    headline: str
    published_date: date
    source_url: str
    body_text: Optional[str] = None
    source_name: str = ""
    source_credibility: str = "tier2"  # official, tier1, tier2, state
    article_type: str = "news"
    extra_metadata: dict = field(default_factory=dict)


@dataclass
class ScrapeResult:
    source: str
    success: bool
    gazettes: list[ScrapedGazette] = field(default_factory=list)
    news: list[ScrapedNews] = field(default_factory=list)
    articles: list[ScrapedArticle] = field(default_factory=list)
    error: Optional[str] = None
    duration_seconds: int = 0


class BaseScraper(ABC):
    """Base class for all gazette/news scrapers."""

    def __init__(self):
        self.client = httpx.Client(
            timeout=settings.scraper_timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "es-VE,es;q=0.9,en;q=0.8",
            },
        )

    @abstractmethod
    def get_source_id(self) -> str:
        ...

    @abstractmethod
    def scrape(self, target_date: Optional[date] = None) -> ScrapeResult:
        ...

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @retry(
        stop=stop_after_attempt(settings.scraper_max_retries),
        wait=wait_exponential(multiplier=1, min=4, max=60),
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException)),
    )
    def _fetch(self, url: str) -> httpx.Response:
        logger.info("Fetching %s", url)
        resp = self.client.get(url)
        resp.raise_for_status()
        return resp

    def _download_pdf(self, url: str, gazette_number: str) -> tuple[Path, str]:
        """Download a PDF and return (local_path, sha256_hash)."""
        pdf_dir = settings.storage_dir / "pdfs"
        filename = f"{gazette_number}.pdf"
        filepath = pdf_dir / filename

        if filepath.exists():
            logger.info("PDF already exists: %s", filepath)
            sha = hashlib.sha256(filepath.read_bytes()).hexdigest()
            return filepath, sha

        logger.info("Downloading PDF: %s -> %s", url, filepath)
        with self.client.stream("GET", url) as resp:
            resp.raise_for_status()
            hasher = hashlib.sha256()
            with open(filepath, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=8192):
                    f.write(chunk)
                    hasher.update(chunk)

        return filepath, hasher.hexdigest()
