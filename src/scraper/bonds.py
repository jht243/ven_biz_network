"""
Venezuela bond-market tracker feed.

This scraper adds a dedicated, finance-focused source to the daily pipeline.
It stores one structured market snapshot per run plus recent public news from
Google News RSS for Venezuela/PDVSA restructuring queries. Price references are
publicly reported and indicative, not executable quotes.
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime
from typing import Optional

import httpx

from src.data.venezuela_bonds import INSTRUMENTS, KEYWORDS, MILESTONES
from src.scraper.base import BaseScraper, ScrapedArticle, ScrapeResult

logger = logging.getLogger(__name__)

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
RSS_QUERIES: tuple[str, ...] = (
    '"Venezuela bonds" OR "Venezuelan bonds" restructuring',
    '"PDVSA bonds" OR "PDVSA debt" Venezuela',
    '"Venezuela sovereign debt" OR "Venezuela debt restructuring"',
    '"CITGO" "PDVSA bonds" OR "Venezuela creditors"',
)
MAX_ITEMS_PER_QUERY = 12


class VenezuelaBondsScraper(BaseScraper):
    """Dedicated bond tracker source for the daily pipeline."""

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
        return "venezuela_bonds"

    def scrape(self, target_date: Optional[date] = None) -> ScrapeResult:
        start = time.monotonic()
        target_date = target_date or date.today()

        articles = [self._snapshot_article(target_date)]
        seen_urls = {articles[0].source_url}

        for query in RSS_QUERIES:
            for article in self._query_news(query):
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

    def _snapshot_article(self, target_date: date) -> ScrapedArticle:
        priced = [i for i in INSTRUMENTS if i.get("price_reference_cents") is not None]
        avg_price = (
            round(sum(float(i["price_reference_cents"]) for i in priced) / len(priced), 2)
            if priced
            else None
        )
        latest_milestone = sorted(MILESTONES, key=lambda m: m["date"])[-1]

        body = (
            "Daily Venezuela bond tracker snapshot. "
            f"Watchlist instruments: {len(INSTRUMENTS)}. "
            f"Public price references available: {len(priced)}. "
            f"Latest milestone: {latest_milestone['date']} - {latest_milestone['title']}."
        )

        return ScrapedArticle(
            headline=f"Venezuela Bond Tracker Snapshot - {target_date.isoformat()}",
            published_date=target_date,
            source_url=f"https://www.caracasresearch.com/data/venezuela-bonds-snapshot/{target_date.isoformat()}",
            body_text=body,
            source_name="Venezuela Bond Market",
            source_credibility="tier1",
            article_type="bond_market_snapshot",
            extra_metadata={
                "kind": "market_snapshot",
                "instrument_count": len(INSTRUMENTS),
                "priced_reference_count": len(priced),
                "avg_public_reference_cents": avg_price,
                "instruments": INSTRUMENTS,
                "milestones": MILESTONES,
                "keywords": list(KEYWORDS),
                "source_note": "Public reference data plus bond-market news feed; not executable quotes.",
            },
        )

    def _query_news(self, query: str) -> list[ScrapedArticle]:
        try:
            resp = self.client.get(
                GOOGLE_NEWS_RSS,
                params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
            )
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
        except Exception as exc:
            logger.warning("Venezuela bonds RSS query failed for %r: %s", query, exc)
            return []

        out: list[ScrapedArticle] = []
        for item in root.findall(".//item")[:MAX_ITEMS_PER_QUERY]:
            parsed = self._parse_item(item, query)
            if parsed is not None:
                out.append(parsed)
        return out

    def _parse_item(self, item, query: str) -> Optional[ScrapedArticle]:
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
            published_date=self._parse_rfc822(pub_str) or date.today(),
            source_url=link,
            body_text=desc[:500] or None,
            source_name="Venezuela Bond Market",
            source_credibility=self._infer_credibility(publisher),
            article_type="bond_market_news",
            extra_metadata={
                "kind": "bond_news",
                "publisher": publisher,
                "snippet": desc[:300],
                "query": query,
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
        return "tier2"

    @staticmethod
    def _strip_html(s: str) -> str:
        text = re.sub(r"<[^>]+>", "", s or "")
        return re.sub(r"\s+", " ", text).strip()
