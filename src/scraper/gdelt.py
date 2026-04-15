"""
Client for the GDELT DOC 2.0 API — global news monitoring for Venezuela.

GDELT monitors news in 100+ languages, updates every 15 minutes, and
provides article metadata with tone/sentiment scores. No API key required.

API docs: https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
"""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Optional
from urllib.parse import quote

from src.scraper.base import BaseScraper, ScrapedArticle, ScrapeResult

logger = logging.getLogger(__name__)

GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"

INVESTMENT_QUERY = (
    "Venezuela investment OR sanctions OR oil OR economy "
    "sourcelang:english"
)

SPANISH_QUERY = (
    "Venezuela inversión OR sanciones OR petróleo OR economía "
    "sourcelang:spanish"
)


class GDELTScraper(BaseScraper):
    """
    Queries the GDELT DOC 2.0 API for English and Spanish articles
    about Venezuela relevant to investment analysis.
    """

    def get_source_id(self) -> str:
        return "gdelt"

    def scrape(self, target_date: Optional[date] = None) -> ScrapeResult:
        start = time.time()
        target_date = target_date or date.today()

        try:
            import time as _time
            en_articles = self._query_articles(INVESTMENT_QUERY, timespan="7days")
            _time.sleep(5)
            es_articles = self._query_articles(SPANISH_QUERY, timespan="7days")

            seen_urls: set[str] = set()
            deduped: list[ScrapedArticle] = []
            for a in en_articles + es_articles:
                if a.source_url not in seen_urls:
                    seen_urls.add(a.source_url)
                    deduped.append(a)

            logger.info(
                "GDELT: %d EN + %d ES = %d unique articles",
                len(en_articles), len(es_articles), len(deduped),
            )

            return ScrapeResult(
                source=self.get_source_id(),
                success=True,
                articles=deduped,
                duration_seconds=int(time.time() - start),
            )

        except Exception as e:
            logger.error("GDELT scrape failed: %s", e, exc_info=True)
            return ScrapeResult(
                source=self.get_source_id(),
                success=False,
                error=str(e),
                duration_seconds=int(time.time() - start),
            )

    def _query_articles(
        self, query: str, timespan: str = "7days", max_records: int = 75
    ) -> list[ScrapedArticle]:
        import time as _time

        params = {
            "query": query,
            "mode": "ArtList",
            "maxrecords": str(max_records),
            "format": "json",
            "timespan": timespan,
            "sort": "DateDesc",
        }

        logger.info("GDELT query: %s", query[:80])

        for attempt in range(4):
            resp = self.client.get(GDELT_DOC_API, params=params)
            if resp.status_code == 429:
                wait = 15 * (attempt + 1)
                logger.warning("GDELT rate-limited, waiting %ds (attempt %d)...", wait, attempt + 1)
                _time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        else:
            logger.error("GDELT rate-limited after 4 attempts")
            return []

        content_type = resp.headers.get("content-type", "")
        if "json" not in content_type:
            logger.warning("GDELT returned non-JSON response (%s), skipping", content_type)
            return []

        try:
            data = resp.json()
        except Exception:
            logger.warning("GDELT response not valid JSON, skipping")
            return []

        raw_articles = data.get("articles", [])
        articles: list[ScrapedArticle] = []

        for item in raw_articles:
            pub_date = self._parse_gdelt_date(item.get("seendate", ""))
            tone = item.get("tone", 0.0)
            domain = item.get("domain", "")
            source_country = item.get("sourcecountry", "")

            articles.append(
                ScrapedArticle(
                    headline=item.get("title", ""),
                    published_date=pub_date or date.today(),
                    source_url=item.get("url", ""),
                    body_text=None,
                    source_name=domain,
                    source_credibility=self._infer_credibility(domain),
                    article_type="news",
                    extra_metadata={
                        "tone": tone,
                        "domain": domain,
                        "source_country": source_country,
                        "language": item.get("language", ""),
                        "image_url": item.get("socialimage", ""),
                    },
                )
            )

        return articles

    @staticmethod
    def _parse_gdelt_date(datestr: str) -> Optional[date]:
        """Parse GDELT date format: '20260414T120000Z'."""
        if not datestr or len(datestr) < 8:
            return None
        try:
            return date(int(datestr[:4]), int(datestr[4:6]), int(datestr[6:8]))
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _infer_credibility(domain: str) -> str:
        high_credibility = {
            "reuters.com", "apnews.com", "bbc.com", "bbc.co.uk",
            "bloomberg.com", "ft.com", "economist.com", "wsj.com",
            "nytimes.com", "washingtonpost.com", "aljazeera.com",
            "theguardian.com", "france24.com", "dw.com",
        }
        state_media = {
            "telesurtv.net", "vtv.gob.ve", "correodelcaroni.com",
            "ultimasnoticias.com.ve", "avn.info.ve",
        }
        domain_lower = domain.lower()
        if any(d in domain_lower for d in high_credibility):
            return "tier1"
        if any(d in domain_lower for d in state_media):
            return "state"
        return "tier2"
