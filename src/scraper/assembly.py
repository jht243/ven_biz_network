"""
Scraper for the Venezuelan National Assembly (Asamblea Nacional) news portal.

The assembly publishes legislative news at:
  https://www.asambleanacional.gob.ve/noticias

News can be filtered by date range and commission. Articles are plain HTML
(no PDFs/OCR needed). We scrape headlines, dates, and article body text.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, timedelta
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.config import settings
from src.scraper.base import BaseScraper, ScrapedNews, ScrapeResult

logger = logging.getLogger(__name__)


class AssemblyNewsScraper(BaseScraper):
    """Scrapes legislative news from asambleanacional.gob.ve."""

    BASE_URL = settings.assembly_url

    def get_source_id(self) -> str:
        return "asamblea_nacional"

    def scrape(self, target_date: Optional[date] = None) -> ScrapeResult:
        start = time.time()
        target_date = target_date or date.today()

        try:
            news_items = self._scrape_news_for_date(target_date)

            return ScrapeResult(
                source=self.get_source_id(),
                success=True,
                news=news_items,
                duration_seconds=int(time.time() - start),
            )

        except Exception as e:
            logger.error("Assembly news scrape failed: %s", e, exc_info=True)
            return ScrapeResult(
                source=self.get_source_id(),
                success=False,
                error=str(e),
                duration_seconds=int(time.time() - start),
            )

    def _scrape_news_for_date(self, target_date: date) -> list[ScrapedNews]:
        """
        The AN portal supports date range filtering via query params:
          ?inicio=2026-04-14 00:00:00&fin=2026-04-14 23:59:59
        """
        date_start = f"{target_date} 00:00:00"
        date_end = f"{target_date} 23:59:59"
        url = f"{self.BASE_URL}/noticias?inicio={date_start}&fin={date_end}"

        resp = self._fetch(url)
        soup = BeautifulSoup(resp.text, "lxml")

        items = []

        # Skip UI chrome — only look at headings that are actual news items
        noise_phrases = {"filtrar por:", "noticias", "buscador", "comisiones", "ver más (+)"}

        for heading in soup.select("h3, h4"):
            headline = heading.get_text(strip=True)
            if not headline or len(headline) < 20:
                continue
            if headline.lower().strip(":") in noise_phrases:
                continue

            # Find the associated date (usually "Fecha: DD/MM/YYYY" nearby)
            pub_date = target_date
            date_el = heading.find_next(string=re.compile(r"Fecha:\s*\d{2}/\d{2}/\d{4}"))
            if date_el:
                m = re.search(r"(\d{2})/(\d{2})/(\d{4})", date_el)
                if m:
                    pub_date = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))

            # Find the link to the full article
            link = heading.find("a") or heading.find_parent("a")
            article_url = ""
            if link and link.get("href"):
                article_url = urljoin(self.BASE_URL, link["href"])

            body_text = None
            if article_url:
                try:
                    body_text = self._fetch_article_body(article_url)
                except Exception as e:
                    logger.warning("Could not fetch article body %s: %s", article_url, e)

            items.append(ScrapedNews(
                headline=headline,
                published_date=pub_date,
                source_url=article_url or url,
                body_text=body_text,
            ))

        logger.info("Assembly news: found %d items for %s", len(items), target_date)
        return items

    def _fetch_article_body(self, url: str) -> Optional[str]:
        """Fetch the full text of an individual news article."""
        resp = self._fetch(url)
        soup = BeautifulSoup(resp.text, "lxml")

        content = soup.select_one(
            ".detalle-noticia, .noticia-contenido, .entry-content, article .content, main"
        )
        if not content:
            return None

        for tag in content.select("script, style, nav, footer, .share"):
            tag.decompose()

        return content.get_text(separator="\n", strip=True)
