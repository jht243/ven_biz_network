"""
Direct scraper for ANSA Latina Venezuela-relevant business news.

ANSA's section pages expose recent article links, while the AMP article
pages expose clean article metadata/body without the heavier desktop
chrome. This scraper discovers candidates from a small set of high-signal
sections, filters them for Venezuela/investor relevance, and parses the
AMP page for body text.
"""

from __future__ import annotations

import json
import logging
import re
import time
from html import unescape
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from src.config import settings
from src.scraper.base import BaseScraper, ScrapedArticle, ScrapeResult

logger = logging.getLogger(__name__)

BASE_URL = "https://www.ansalatina.com"

SECTION_PATHS: tuple[str, ...] = (
    "/americalatina/noticia/economia/index.shtml",
    "/americalatina/noticia/economia/index_2.shtml",
    "/americalatina/noticia/economia/index_3.shtml",
    "/americalatina/noticia/energia/index.shtml",
    "/americalatina/noticia/empresas/index.shtml",
    "/americalatina/noticia/politica/index.shtml",
    "/americalatina/noticia/ultimo_momento/index.shtml",
)

ARTICLE_PATH_RE = re.compile(
    r"^/americalatina/noticia/(?:economia|energia|empresas|politica|ultimo_momento)/"
    r"\d{4}/\d{2}/\d{2}/[^?#]+\.html$"
)

VENEZUELA_TERMS = (
    "venezuela",
    "venezolana",
    "venezolano",
    "venezolanos",
    "venezolanas",
    "caracas",
    "maduro",
    "delcy",
    "pdvsa",
    "chavista",
    "chavismo",
    "bcv",
)

INVESTOR_TERMS = (
    "activo",
    "activos",
    "banco",
    "bcv",
    "bono",
    "chevron",
    "combustible",
    "comercio",
    "deuda",
    "divisas",
    "dolar",
    "dólar",
    "econom",
    "empresa",
    "export",
    "financ",
    "fiscal",
    "gas",
    "invers",
    "ley",
    "licencia",
    "minera",
    "oro",
    "petrol",
    "privat",
    "sancion",
    "sanción",
    "tasas",
)

MAX_ARTICLES_PER_RUN = 25


class AnsaLatinaScraper(BaseScraper):
    """ANSA Latina article intake for Venezuela investment monitoring."""

    def __init__(self) -> None:
        super().__init__()
        self.client.close()
        self.client = httpx.Client(
            timeout=httpx.Timeout(15.0, connect=8.0),
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

    def get_source_id(self) -> str:
        return "ansa_latina"

    def scrape(self, target_date: Optional[date] = None) -> ScrapeResult:
        start = time.monotonic()
        target_date = target_date or date.today()
        cutoff = target_date - timedelta(days=settings.scraper_lookback_days)

        try:
            candidates = self._discover_candidates()
            articles: list[ScrapedArticle] = []
            seen_urls: set[str] = set()

            for url in candidates:
                if len(articles) >= MAX_ARTICLES_PER_RUN:
                    break
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                article = self._parse_article(url)
                if article is None:
                    continue
                if article.published_date < cutoff or article.published_date > target_date:
                    continue
                if not self._is_relevant(article):
                    continue
                articles.append(article)

            elapsed = int(time.monotonic() - start)
            logger.info(
                "ANSA Latina: %d relevant articles from %d candidates in %ds",
                len(articles), len(candidates), elapsed,
            )
            return ScrapeResult(
                source=self.get_source_id(),
                success=True,
                articles=articles,
                duration_seconds=elapsed,
            )
        except Exception as exc:
            logger.warning("ANSA Latina scrape failed: %s", exc, exc_info=True)
            return ScrapeResult(
                source=self.get_source_id(),
                success=False,
                error=str(exc),
                duration_seconds=int(time.monotonic() - start),
            )

    def _discover_candidates(self) -> list[str]:
        candidates: list[str] = []
        seen: set[str] = set()

        for path in SECTION_PATHS:
            try:
                resp = self.client.get(urljoin(BASE_URL, path))
                resp.raise_for_status()
            except (
                httpx.ConnectError,
                httpx.TimeoutException,
                httpx.RemoteProtocolError,
                httpx.HTTPStatusError,
            ) as exc:
                logger.warning("ANSA Latina section fetch failed for %s: %s", path, type(exc).__name__)
                continue

            soup = BeautifulSoup(resp.text, "lxml")
            for a in soup.select("a[href]"):
                href = (a.get("href") or "").strip()
                path_only = urlparse(urljoin(BASE_URL, href)).path
                if not ARTICLE_PATH_RE.match(path_only):
                    continue

                title = " ".join(a.get_text(" ", strip=True).split())
                if title and not self._text_mentions_target(title):
                    continue

                canonical = urljoin(BASE_URL, path_only)
                if canonical in seen:
                    continue
                seen.add(canonical)
                candidates.append(canonical)

        return candidates

    def _parse_article(self, url: str) -> Optional[ScrapedArticle]:
        amp_url = self._to_amp_url(url)
        try:
            resp = self.client.get(amp_url)
            resp.raise_for_status()
        except (
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.RemoteProtocolError,
            httpx.HTTPStatusError,
        ) as exc:
            logger.warning("ANSA Latina article fetch failed for %s: %s", amp_url, type(exc).__name__)
            return None

        soup = BeautifulSoup(resp.text, "lxml")
        meta = self._json_ld(soup)

        headline = (
            meta.get("headline")
            or self._meta_content(soup, "meta[itemprop='headline']")
            or self._text(soup.select_one("h1"))
        )
        if not headline:
            return None

        published = self._parse_date(
            meta.get("datePublished")
            or self._meta_content(soup, "meta[itemprop='datePublished']")
            or self._meta_content(soup, "meta[property='article:published_time']")
        )

        canonical_path = meta.get("mainEntityOfPage")
        canonical_url = urljoin(BASE_URL, canonical_path) if canonical_path else url
        body = self._body_text(soup)
        description = (
            meta.get("description")
            or self._meta_content(soup, "meta[name='description']")
            or self._text(soup.select_one(".standfirst"))
        )

        return ScrapedArticle(
            headline=" ".join(headline.split()),
            published_date=published or date.today(),
            source_url=canonical_url,
            body_text=body or description,
            source_name="ANSA Latina",
            source_credibility="tier1",
            article_type="news",
            extra_metadata={
                "publisher": "ANSA Latina",
                "publisher_domain": "ansalatina.com",
                "section": (meta.get("articleSection") or "").strip(),
                "description": description,
                "amp_url": amp_url,
            },
        )

    @staticmethod
    def _to_amp_url(url: str) -> str:
        parsed = urlparse(url)
        if parsed.path.startswith("/amp/"):
            return url
        return urljoin(BASE_URL, "/amp" + parsed.path)

    @staticmethod
    def _json_ld(soup: BeautifulSoup) -> dict:
        for script in soup.select("script[type='application/ld+json']"):
            raw = script.string or script.get_text()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = AnsaLatinaScraper._loose_json_ld(raw)
            if isinstance(data, dict) and data.get("@type") == "NewsArticle":
                return data
        return {}

    @staticmethod
    def _loose_json_ld(raw: str) -> dict:
        """ANSA AMP JSON-LD sometimes embeds the author name across raw
        newlines, which makes the script invalid JSON. The fields we need
        are still simple string properties, so recover those defensively."""
        out: dict[str, str] = {}
        for key in (
            "@type",
            "mainEntityOfPage",
            "headline",
            "description",
            "articleSection",
            "datePublished",
            "dateModified",
        ):
            match = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]*)"', raw, re.S)
            if match:
                out[key] = unescape(re.sub(r"\s+", " ", match.group(1)).strip())
        return out

    @staticmethod
    def _body_text(soup: BeautifulSoup) -> str:
        body = soup.select_one(".article-body")
        if body is None:
            return ""
        for tag in body.select(
            "amp-ad, amp-embed, amp-img, figure, script, style, .adv-slot, .share, .social"
        ):
            tag.decompose()
        text = body.get_text("\n", strip=True)
        text = re.sub(r"\s+", " ", text)
        return text.replace(" (ANSA).", " (ANSA).").strip()

    @staticmethod
    def _parse_date(value: object) -> Optional[date]:
        if not value:
            return None
        s = str(value).strip()
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(s).date()
        except ValueError:
            return None

    @staticmethod
    def _meta_content(soup: BeautifulSoup, selector: str) -> str:
        tag = soup.select_one(selector)
        return (tag.get("content") or "").strip() if tag else ""

    @staticmethod
    def _text(tag) -> str:
        return tag.get_text(" ", strip=True) if tag else ""

    @staticmethod
    def _text_mentions_target(text: str) -> bool:
        haystack = (text or "").lower()
        return any(term in haystack for term in VENEZUELA_TERMS)

    @classmethod
    def _is_relevant(cls, article: ScrapedArticle) -> bool:
        text = f"{article.headline or ''} {article.body_text or ''}".lower()
        return (
            any(term in text for term in VENEZUELA_TERMS)
            and any(term in text for term in INVESTOR_TERMS)
        )
