"""
Scraper for the US State Department Venezuela travel advisory.

Monitors https://travel.state.gov for advisory level changes.
A downgrade from Level 4 ("Do Not Travel") to Level 3 would be a
significant positive signal for investors.

No API key required — public HTML page.
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

ADVISORY_URL = (
    "https://travel.state.gov/content/travel/en/traveladvisories/"
    "traveladvisories/venezuela-travel-advisory.html"
)


class TravelAdvisoryScraper(BaseScraper):
    """
    Scrapes the State Department Venezuela travel advisory page and
    extracts the current advisory level, last update date, and summary.
    """

    def get_source_id(self) -> str:
        return "travel_advisory"

    def scrape(self, target_date: Optional[date] = None) -> ScrapeResult:
        start = time.time()
        target_date = target_date or date.today()

        try:
            advisory = self._scrape_advisory()
            if not advisory:
                return ScrapeResult(
                    source=self.get_source_id(),
                    success=False,
                    error="Could not parse travel advisory page",
                    duration_seconds=int(time.time() - start),
                )

            article = ScrapedArticle(
                headline=(
                    f"US Travel Advisory: Venezuela — Level {advisory['level']} "
                    f"({advisory['level_text']})"
                ),
                published_date=advisory.get("last_updated", target_date),
                source_url=ADVISORY_URL,
                body_text=advisory.get("summary", ""),
                source_name="US State Department",
                source_credibility="official",
                article_type="travel_advisory",
                extra_metadata=advisory,
            )

            return ScrapeResult(
                source=self.get_source_id(),
                success=True,
                articles=[article],
                duration_seconds=int(time.time() - start),
            )

        except Exception as e:
            logger.error("Travel advisory scrape failed: %s", e, exc_info=True)
            return ScrapeResult(
                source=self.get_source_id(),
                success=False,
                error=str(e),
                duration_seconds=int(time.time() - start),
            )

    def _scrape_advisory(self) -> Optional[dict]:
        resp = self._fetch(ADVISORY_URL)
        soup = BeautifulSoup(resp.text, "lxml")

        level = None
        level_text = ""
        last_updated = None
        summary = ""

        level_match = re.search(
            r"Level\s+(\d)\s*[-–:]\s*(.+?)(?:\s*$|\s*<)",
            soup.get_text(),
            re.IGNORECASE,
        )
        if level_match:
            level = int(level_match.group(1))
            level_text = level_match.group(2).strip().rstrip(".")

        for tag in soup.find_all(string=re.compile(r"(Updated|Last\s+Updated)", re.I)):
            date_match = re.search(
                r"(?:January|February|March|April|May|June|July|August|"
                r"September|October|November|December)\s+\d{1,2},?\s+\d{4}",
                tag.string or "",
                re.I,
            )
            if date_match:
                from datetime import datetime
                try:
                    last_updated = datetime.strptime(
                        date_match.group(0).replace(",", ""), "%B %d %Y"
                    ).date()
                except ValueError:
                    pass

        content = soup.select_one(
            ".tsg-rwd-emergency-702-702-content-background, "
            ".tsg-rwd-main-copy-702-702-body, "
            ".field--name-body, "
            "article, main"
        )
        if content:
            for tag in content.select("script, style, nav"):
                tag.decompose()
            summary = content.get_text(separator="\n", strip=True)[:2000]

        if level is None:
            level_el = soup.find(
                string=re.compile(r"Level\s+\d", re.I)
            )
            if level_el:
                m = re.search(r"Level\s+(\d)", level_el, re.I)
                if m:
                    level = int(m.group(1))

        if level is None:
            return None

        LEVEL_LABELS = {
            1: "Exercise Normal Precautions",
            2: "Exercise Increased Caution",
            3: "Reconsider Travel",
            4: "Do Not Travel",
        }
        if not level_text:
            level_text = LEVEL_LABELS.get(level, "Unknown")

        return {
            "level": level,
            "level_text": level_text,
            "last_updated": last_updated,
            "summary": summary,
        }
