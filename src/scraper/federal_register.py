"""
Scraper for the US Federal Register API — OFAC / Venezuela documents.

The Federal Register REST API is free, requires no API key, and publishes
every OFAC rule, general license, and sanctions notice as a legal requirement.

Endpoint docs: https://www.federalregister.gov/developers/documentation/api/v1
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Optional

from src.config import settings
from src.scraper.base import BaseScraper, ScrapedArticle, ScrapeResult

logger = logging.getLogger(__name__)

FR_API_BASE = "https://www.federalregister.gov/api/v1"

VENEZUELA_TERMS = [
    "venezuela",
    "venezuelan",
    "PDVSA",
    "Maduro",
    "Caracas",
]

OFAC_AGENCY_SLUG = "foreign-assets-control-office"


class FederalRegisterScraper(BaseScraper):
    """
    Queries the Federal Register API for OFAC documents mentioning Venezuela.
    Returns structured article data with direct links to PDFs and HTML.
    """

    def get_source_id(self) -> str:
        return "federal_register"

    def scrape(self, target_date: Optional[date] = None) -> ScrapeResult:
        start = time.time()
        target_date = target_date or date.today()
        lookback = target_date - timedelta(days=settings.scraper_lookback_days)

        try:
            articles = self._search_ofac_venezuela(lookback, target_date)

            return ScrapeResult(
                source=self.get_source_id(),
                success=True,
                articles=articles,
                duration_seconds=int(time.time() - start),
            )

        except Exception as e:
            logger.error("Federal Register scrape failed: %s", e, exc_info=True)
            return ScrapeResult(
                source=self.get_source_id(),
                success=False,
                error=str(e),
                duration_seconds=int(time.time() - start),
            )

    def _search_ofac_venezuela(
        self, date_from: date, date_to: date
    ) -> list[ScrapedArticle]:
        params = {
            "conditions[agencies][]": OFAC_AGENCY_SLUG,
            "conditions[term]": "venezuela",
            "conditions[publication_date][gte]": date_from.isoformat(),
            "conditions[publication_date][lte]": date_to.isoformat(),
            "per_page": 50,
            "order": "newest",
            "fields[]": [
                "title",
                "abstract",
                "document_number",
                "publication_date",
                "type",
                "html_url",
                "pdf_url",
                "agencies",
            ],
        }

        url = f"{FR_API_BASE}/documents.json"
        resp = self._fetch_json(url, params=params)
        results = resp.get("results", [])

        articles: list[ScrapedArticle] = []
        for doc in results:
            pub_date = date.fromisoformat(doc["publication_date"])
            agencies = ", ".join(
                a.get("name", "") for a in doc.get("agencies", [])
            )

            articles.append(
                ScrapedArticle(
                    headline=doc.get("title", ""),
                    published_date=pub_date,
                    source_url=doc.get("html_url", ""),
                    body_text=doc.get("abstract", ""),
                    source_name="Federal Register",
                    source_credibility="official",
                    article_type=doc.get("type", "Notice"),
                    extra_metadata={
                        "document_number": doc.get("document_number"),
                        "pdf_url": doc.get("pdf_url"),
                        "agencies": agencies,
                    },
                )
            )

        logger.info(
            "Federal Register: found %d OFAC/Venezuela docs (%s to %s)",
            len(articles), date_from, date_to,
        )
        return articles

    def _fetch_json(self, url: str, params: dict | None = None) -> dict:
        """GET a JSON endpoint with query params."""
        logger.info("Fetching %s", url)
        resp = self.client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()
