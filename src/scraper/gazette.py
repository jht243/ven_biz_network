"""
Scrapers for Venezuelan Gaceta Oficial from two sources:

1. TuGacetaOficial.com — unofficial WordPress aggregator (primary, most reliable)
   - Lists gazettes with sumario text already in HTML
   - PDF download links (hosted on MEGA)

2. GacetaOficial.gob.ve — official government portal (backup)
   - Calendar-based navigation
   - Direct PDF access (when available)
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.config import settings
from src.scraper.base import BaseScraper, ScrapedGazette, ScrapeResult

logger = logging.getLogger(__name__)

# Spanish month names for date parsing
SPANISH_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def _parse_spanish_date(text: str) -> Optional[date]:
    """Parse dates like '27 de marzo de 2026' or '27/03/2026'."""
    text = text.strip().lower()

    # Try DD/MM/YYYY
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))

    # Try "27 de marzo de 2026"
    m = re.search(r"(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})", text)
    if m:
        day = int(m.group(1))
        month = SPANISH_MONTHS.get(m.group(2))
        year = int(m.group(3))
        if month:
            return date(year, month, day)

    return None


def _parse_gazette_number_and_type(title: str) -> tuple[Optional[str], str]:
    """
    Extract gazette number and type from titles like:
      'Gaceta Oficial #43344 27/03/2026'
      'Gaceta Oficial Extraordinaria #7016 30/03/2026'
    """
    gazette_type = "extraordinaria" if "extraordinaria" in title.lower() else "ordinaria"

    m = re.search(r"#?(\d{4,6})", title)
    number = m.group(1) if m else None

    return number, gazette_type


class TuGacetaScraper(BaseScraper):
    """
    Scrapes tugacetaoficial.com — an unofficial WordPress site that mirrors
    the Gaceta Oficial with full sumario text and MEGA download links.

    This is our primary source because:
    - The sumario HTML gives us structured text without OCR
    - The site is more reliable than government portals
    - URL patterns are predictable
    """

    BASE_URL = settings.gazette_tugaceta_url

    def get_source_id(self) -> str:
        return "tu_gaceta"

    def scrape(self, target_date: Optional[date] = None) -> ScrapeResult:
        import time

        start = time.time()
        target_date = target_date or date.today()

        try:
            gazette_urls = self._get_listing_urls(target_date)

            if not gazette_urls:
                logger.info("No gazettes found for %s on TuGacetaOficial", target_date)
                return ScrapeResult(
                    source=self.get_source_id(),
                    success=True,
                    gazettes=[],
                    duration_seconds=int(time.time() - start),
                )

            gazettes = []
            for url in gazette_urls:
                try:
                    gazette = self._scrape_gazette_page(url, target_date)
                    if gazette:
                        gazettes.append(gazette)
                except Exception as e:
                    logger.error("Failed to scrape gazette page %s: %s", url, e)

            return ScrapeResult(
                source=self.get_source_id(),
                success=True,
                gazettes=gazettes,
                duration_seconds=int(time.time() - start),
            )

        except Exception as e:
            logger.error("TuGaceta scrape failed: %s", e, exc_info=True)
            return ScrapeResult(
                source=self.get_source_id(),
                success=False,
                error=str(e),
                duration_seconds=int(time.time() - start),
            )

    def _get_listing_urls(self, target_date: date) -> list[str]:
        """
        Fetch the yearly listing page and find gazette links
        whose date matches the target date.
        """
        listing_url = f"{self.BASE_URL}/gaceta-oficial-de-venezuela-{target_date.year}/"
        resp = self._fetch(listing_url)
        soup = BeautifulSoup(resp.text, "lxml")

        results = []

        for article in soup.select("article"):
            link_el = article.select_one("a[href*='gaceta-oficial']")
            if not link_el:
                continue

            href = link_el.get("href", "")
            title = link_el.get_text(strip=True)

            # Try parsing date from the title (e.g. "Gaceta Oficial #43344 27/03/2026")
            parsed_date = _parse_gazette_number_and_date_from_url(href, target_date.year)
            if parsed_date and parsed_date == target_date:
                results.append(href)
                continue

            # Also check for date text in the article
            date_el = article.select_one("time, .entry-date, .post-date")
            if date_el:
                date_text = date_el.get("datetime", date_el.get_text())
                parsed = _parse_spanish_date(date_text)
                if parsed == target_date:
                    results.append(href)

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for url in results:
            if url not in seen:
                seen.add(url)
                unique.append(url)

        logger.info("Found %d gazette URLs for %s", len(unique), target_date)
        return unique

    def _scrape_gazette_page(self, url: str, fallback_date: date) -> Optional[ScrapedGazette]:
        """Parse a single gazette detail page for sumario and PDF link."""
        resp = self._fetch(url)
        soup = BeautifulSoup(resp.text, "lxml")

        title_el = soup.select_one("h1.entry-title, h1, .post-title")
        title = title_el.get_text(strip=True) if title_el else ""

        number, gazette_type = _parse_gazette_number_and_type(title)

        # Extract published date from title or page
        pub_date = _parse_spanish_date(title) or fallback_date

        # Extract sumario — the main content area
        content_el = soup.select_one(".entry-content, .post-content, article")
        sumario = ""
        if content_el:
            # Remove social sharing widgets, scripts, nav chrome
            for tag in content_el.select(
                "script, style, nav, .sharedaddy, .share-buttons, "
                ".sd-sharing, .jp-relatedposts, .post-navigation, "
                ".yarpp-related, .addtoany_share_save_container"
            ):
                tag.decompose()
            raw = content_el.get_text(separator="\n", strip=True)
            # Strip social share noise from start and end
            share_words = {
                "compartir en", "comparte esta entrada", "comparte esta entrada:",
                "share this", "share this:",
                "whatsapp", "x (twitter)", "twitter", "telegram",
                "linkedin", "facebook", "pinterest", "email",
                "gaceta oficial",
            }
            lines = raw.split("\n")
            cleaned = []
            in_content = False
            for line in lines:
                stripped = line.strip().lower().rstrip(":")
                if not in_content:
                    if stripped in share_words or stripped == "" or stripped.startswith("compartir en"):
                        continue
                    if stripped == "sumario":
                        continue
                    in_content = True
                cleaned.append(line)
            while cleaned and cleaned[-1].strip().lower().rstrip(":") in share_words | {""}:
                cleaned.pop()
            sumario = "\n".join(cleaned).strip()

        # Find PDF download link (usually a MEGA link)
        pdf_url = None
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if "mega.nz" in href or href.endswith(".pdf"):
                pdf_url = href
                break

        logger.info(
            "Scraped gazette: number=%s type=%s date=%s pdf=%s sumario_len=%d",
            number, gazette_type, pub_date, bool(pdf_url), len(sumario),
        )

        return ScrapedGazette(
            gazette_number=number,
            gazette_type=gazette_type,
            published_date=pub_date,
            source="tu_gaceta",
            source_url=url,
            title=title,
            sumario_text=sumario,
            pdf_download_url=pdf_url,
        )


class OfficialGazetteScraper(BaseScraper):
    """
    Scrapes gacetaoficial.gob.ve — the official government portal.

    The portal uses a calendar-based UI. Each date cell links to gazette
    listings for that day. Individual gazette pages provide direct PDF links.

    This is our backup source. Government portals have poor uptime and the
    calendar UI may require JavaScript rendering (Playwright) for full access.
    """

    BASE_URL = settings.gazette_official_url

    def get_source_id(self) -> str:
        return "gaceta_oficial"

    def scrape(self, target_date: Optional[date] = None) -> ScrapeResult:
        import time

        start = time.time()
        target_date = target_date or date.today()

        try:
            gazettes = self._scrape_calendar_day(target_date)

            return ScrapeResult(
                source=self.get_source_id(),
                success=True,
                gazettes=gazettes,
                duration_seconds=int(time.time() - start),
            )

        except Exception as e:
            logger.error("Official gazette scrape failed: %s", e, exc_info=True)
            return ScrapeResult(
                source=self.get_source_id(),
                success=False,
                error=str(e),
                duration_seconds=int(time.time() - start),
            )

    def _scrape_calendar_day(self, target_date: date) -> list[ScrapedGazette]:
        """
        The official portal exposes a search API. We query by date range
        (single day) to find that day's gazettes.
        """
        date_str = target_date.strftime("%Y-%m-%d")
        search_url = (
            f"{self.BASE_URL}/buscar?"
            f"fecha_desde={date_str}&fecha_hasta={date_str}"
        )

        try:
            resp = self._fetch(search_url)
        except Exception:
            # Fallback: try the calendar page and parse HTML
            return self._scrape_calendar_html(target_date)

        soup = BeautifulSoup(resp.text, "lxml")
        gazettes = []

        for row in soup.select("table tr, .resultado, .gaceta-item, li"):
            link = row.select_one("a[href]")
            if not link:
                continue

            href = link.get("href", "")
            if not href or "gaceta" not in href.lower():
                continue

            full_url = urljoin(self.BASE_URL, href)
            title_text = link.get_text(strip=True)
            number, gazette_type = _parse_gazette_number_and_type(title_text)

            # Try to find a direct PDF link in the row
            pdf_link = row.select_one("a[href$='.pdf']")
            pdf_url = urljoin(self.BASE_URL, pdf_link["href"]) if pdf_link else None

            gazettes.append(ScrapedGazette(
                gazette_number=number,
                gazette_type=gazette_type,
                published_date=target_date,
                source="gaceta_oficial",
                source_url=full_url,
                title=title_text,
                pdf_download_url=pdf_url,
            ))

        logger.info("Official portal: found %d gazettes for %s", len(gazettes), target_date)
        return gazettes

    def _scrape_calendar_html(self, target_date: date) -> list[ScrapedGazette]:
        """
        Fallback: parse the calendar HTML directly.
        The calendar page shows the full year with clickable date cells.
        """
        resp = self._fetch(self.BASE_URL)
        soup = BeautifulSoup(resp.text, "lxml")

        day = target_date.day
        gazettes = []

        # Look for links containing the target day in calendar cells
        for cell in soup.select("td a, .calendar a"):
            text = cell.get_text(strip=True)
            href = cell.get("href", "")

            if text.isdigit() and int(text) == day and href:
                full_url = urljoin(self.BASE_URL, href)
                detail_gazettes = self._scrape_day_detail_page(full_url, target_date)
                gazettes.extend(detail_gazettes)

        return gazettes

    def _scrape_day_detail_page(self, url: str, pub_date: date) -> list[ScrapedGazette]:
        """Parse a day's detail page listing individual gazettes."""
        try:
            resp = self._fetch(url)
        except Exception as e:
            logger.warning("Could not fetch day detail page %s: %s", url, e)
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        gazettes = []

        for link in soup.select("a[href]"):
            href = link.get("href", "")
            if not href.endswith(".pdf"):
                continue

            title_text = link.get_text(strip=True)
            number, gazette_type = _parse_gazette_number_and_type(title_text)
            full_url = urljoin(url, href)

            gazettes.append(ScrapedGazette(
                gazette_number=number,
                gazette_type=gazette_type,
                published_date=pub_date,
                source="gaceta_oficial",
                source_url=url,
                title=title_text,
                pdf_download_url=full_url,
            ))

        return gazettes


def _parse_gazette_number_and_date_from_url(url: str, year: int) -> Optional[date]:
    """
    Parse date from URL patterns like:
      /gaceta-oficial-43344-27-03-2026/
      /gaceta-oficial-extraordinaria-7016-30-03-2026/
    """
    m = re.search(r"(\d{1,2})-(\d{1,2})-(\d{4})/?$", url.rstrip("/"))
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None
