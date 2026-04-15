"""
Scraper for the Banco Central de Venezuela (BCV) official exchange rates.

The BCV does not provide a public API, so we scrape the homepage at
https://www.bcv.org.ve/ which displays the current official USD/EUR rates.

Falls back to community API projects if the main site is unreachable.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date
from typing import Optional

from bs4 import BeautifulSoup

from src.scraper.base import BaseScraper, ScrapedArticle, ScrapeResult

logger = logging.getLogger(__name__)

BCV_URL = "https://www.bcv.org.ve/"
COMMUNITY_API_URL = "https://pydolarve.org/api/v2/dollar?page=bcv"


class BCVScraper(BaseScraper):
    """
    Scrapes the BCV homepage for the official VES/USD and VES/EUR rates.
    Produces a single ScrapedArticle with the rate data in extra_metadata.
    """

    def get_source_id(self) -> str:
        return "bcv_rates"

    def scrape(self, target_date: Optional[date] = None) -> ScrapeResult:
        start = time.time()
        target_date = target_date or date.today()

        try:
            rates = self._scrape_bcv_homepage()
            if not rates:
                rates = self._try_community_api()

            if not rates:
                return ScrapeResult(
                    source=self.get_source_id(),
                    success=False,
                    error="Could not retrieve BCV rates from any source",
                    duration_seconds=int(time.time() - start),
                )

            article = ScrapedArticle(
                headline=f"BCV Official Exchange Rate: {rates.get('usd', 'N/A')} VES/USD",
                published_date=target_date,
                source_url=BCV_URL,
                body_text=(
                    f"USD: {rates.get('usd', 'N/A')} VES\n"
                    f"EUR: {rates.get('eur', 'N/A')} VES\n"
                    f"Date: {target_date.isoformat()}"
                ),
                source_name="Banco Central de Venezuela",
                source_credibility="official",
                article_type="exchange_rate",
                extra_metadata=rates,
            )

            return ScrapeResult(
                source=self.get_source_id(),
                success=True,
                articles=[article],
                duration_seconds=int(time.time() - start),
            )

        except Exception as e:
            logger.error("BCV scrape failed: %s", e, exc_info=True)
            return ScrapeResult(
                source=self.get_source_id(),
                success=False,
                error=str(e),
                duration_seconds=int(time.time() - start),
            )

    def _scrape_bcv_homepage(self) -> Optional[dict]:
        """Parse the BCV homepage for exchange rates displayed in the sidebar."""
        try:
            resp = self._fetch(BCV_URL)
        except Exception as e:
            logger.warning("BCV homepage unreachable: %s", e)
            return None

        soup = BeautifulSoup(resp.text, "lxml")
        rates: dict = {}

        for selector in [".field-content", ".pull-right", ".centrado"]:
            for el in soup.select(selector):
                self._extract_rate(el.get_text(strip=True), rates)

        usd_el = soup.find(id="702") or soup.select_one("[id*='dolar'], .col-sm-6.col-xs-6.centrado")
        if usd_el:
            self._extract_rate(usd_el.get_text(strip=True), rates)

        for el in soup.find_all(string=re.compile(r"\d+[,\.]\d{2,}")):
            parent = el.parent
            if parent:
                parent_text = parent.get_text(strip=True).lower()
                if "usd" in parent_text or "dólar" in parent_text or "dollar" in parent_text:
                    val = self._parse_ve_number(el.strip())
                    if val and 1 < val < 200:
                        rates["usd"] = val
                elif "eur" in parent_text or "euro" in parent_text:
                    val = self._parse_ve_number(el.strip())
                    if val and 1 < val < 200:
                        rates["eur"] = val

        if rates:
            logger.info("BCV rates scraped: %s", rates)
        return rates if rates else None

    def _try_community_api(self) -> Optional[dict]:
        """Fallback: try a community-maintained BCV API."""
        try:
            resp = self.client.get(COMMUNITY_API_URL, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            monitors = data.get("monitors", data)
            if isinstance(monitors, dict):
                usd_data = monitors.get("usd", {})
                usd_rate = usd_data.get("price")
                if usd_rate:
                    return {"usd": float(usd_rate), "source": "pydolarve"}

            return None
        except Exception as e:
            logger.warning("Community BCV API failed: %s", e)
            return None

    @staticmethod
    def _extract_rate(text: str, rates: dict) -> None:
        match = re.search(r"(\d+)[,.](\d{2,})", text)
        if match:
            val_str = f"{match.group(1)}.{match.group(2)}"
            try:
                val = float(val_str)
                if 1 < val < 200:
                    if "usd" not in rates:
                        rates["usd"] = val
            except ValueError:
                pass

    @staticmethod
    def _parse_ve_number(text: str) -> Optional[float]:
        """Parse Venezuelan number format (comma as decimal): '36,7206' -> 36.7206."""
        cleaned = text.strip().replace(".", "").replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return None
