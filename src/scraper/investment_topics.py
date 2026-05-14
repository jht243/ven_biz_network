"""Targeted investment fact-signal scraper.

This is deliberately separate from the broad Google News feed: these queries
exist to keep volatile investment-page facts fresh, not to populate the
homepage with general Venezuela news.
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime
from typing import Optional

import httpx

from src.data.investment_facts import INVESTMENT_TOPIC_QUERIES
from src.scraper.base import BaseScraper, ScrapedArticle, ScrapeResult

logger = logging.getLogger(__name__)

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
MAX_ITEMS_PER_TOPIC = 8


class InvestmentTopicsScraper(BaseScraper):
    """Focused RSS monitor for ETF, bonds, OFAC, oil, and related metrics."""

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
                "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
            },
        )

    def get_source_id(self) -> str:
        return "investment_facts"

    def scrape(self, target_date: Optional[date] = None) -> ScrapeResult:
        start = time.monotonic()
        target_date = target_date or date.today()
        seen_urls: set[str] = set()
        articles: list[ScrapedArticle] = []

        for spec in INVESTMENT_TOPIC_QUERIES:
            for article in self._query_topic(spec["topic"], spec["query"], target_date):
                if article.source_url in seen_urls:
                    continue
                seen_urls.add(article.source_url)
                articles.append(article)

        return ScrapeResult(
            source=self.get_source_id(),
            success=True,
            articles=articles,
            duration_seconds=int(time.monotonic() - start),
        )

    def _query_topic(self, topic: str, query: str, target_date: date) -> list[ScrapedArticle]:
        try:
            resp = self.client.get(
                GOOGLE_NEWS_RSS,
                params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
            )
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
        except Exception as exc:
            logger.warning("Investment topic RSS query failed for %s: %s", topic, exc)
            return []

        out: list[ScrapedArticle] = []
        for item in root.findall(".//item")[:MAX_ITEMS_PER_TOPIC]:
            parsed = self._parse_item(item, topic, query, target_date)
            if parsed is not None:
                out.append(parsed)
        return out

    def _parse_item(self, item, topic: str, query: str, target_date: date) -> Optional[ScrapedArticle]:
        title_full = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_str = (item.findtext("pubDate") or "").strip()
        desc = self._strip_html(item.findtext("description") or "")
        if not title_full or not link:
            return None

        title = title_full
        publisher = ""
        if " - " in title_full:
            title, publisher = title_full.rsplit(" - ", 1)
            title = title.strip()
            publisher = publisher.strip()

        return ScrapedArticle(
            headline=title,
            published_date=self._parse_rfc822(pub_str) or target_date,
            source_url=link,
            body_text=desc[:700] or None,
            source_name="Investment Facts",
            source_credibility=self._infer_credibility(publisher),
            article_type="investment_fact_signal",
            extra_metadata={
                "kind": "investment_fact_signal",
                "topic": topic,
                "query": query,
                "publisher": publisher,
                "snippet": desc[:350],
            },
        )

    @staticmethod
    def _parse_rfc822(s: str) -> Optional[date]:
        if not s:
            return None
        for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S"):
            try:
                return datetime.strptime(s.strip()[:31], fmt).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _infer_credibility(publisher: str) -> str:
        p = (publisher or "").lower()
        if any(name in p for name in ("reuters", "bloomberg", "financial times", "wall street journal")):
            return "tier1"
        if any(name in p for name in ("sec", "ofac", "federal register", "treasury")):
            return "official"
        return "tier2"

    @staticmethod
    def _strip_html(s: str) -> str:
        text = re.sub(r"<[^>]+>", "", s or "")
        return re.sub(r"\s+", " ", text).strip()
