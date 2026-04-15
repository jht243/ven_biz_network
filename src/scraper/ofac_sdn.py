"""
Monitor OFAC SDN (Specially Designated Nationals) list for Venezuelan entities.

Downloads the consolidated XML list from OFAC's Sanctions List Service and
filters for Venezuela-program entries. Diffs against the previous snapshot
to detect additions and removals.

Data source: https://ofac.treasury.gov/sanctions-list-service
No API key required — public CSV/XML downloads.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import time
from datetime import date
from pathlib import Path
from typing import Optional

from src.config import settings
from src.scraper.base import BaseScraper, ScrapedArticle, ScrapeResult

logger = logging.getLogger(__name__)

SDN_CSV_URL = (
    "https://www.treasury.gov/ofac/downloads/sdn.csv"
)
CONS_CSV_URL = (
    "https://www.treasury.gov/ofac/downloads/consolidated/"
    "cons_prim.csv"
)
VENEZUELA_PROGRAMS = {"VENEZUELA", "VENEZUELA-EO13884", "VENEZUELA-EO13692", "VENEZUELA-EO13850"}

SNAPSHOT_DIR = settings.storage_dir / "ofac_snapshots"


class OFACSdnScraper(BaseScraper):
    """
    Downloads the OFAC SDN CSV, filters for Venezuela-program entries,
    and diffs against the last snapshot to surface additions/removals.
    """

    def get_source_id(self) -> str:
        return "ofac_sdn"

    def scrape(self, target_date: Optional[date] = None) -> ScrapeResult:
        start = time.time()
        target_date = target_date or date.today()
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

        try:
            current = self._download_and_filter()
            previous = self._load_previous_snapshot()
            changes = self._diff(previous, current)
            self._save_snapshot(current, target_date)

            articles: list[ScrapedArticle] = []
            for change_type, entries in changes.items():
                if not entries:
                    continue
                for entry in entries:
                    articles.append(
                        ScrapedArticle(
                            headline=f"OFAC SDN {change_type}: {entry['name']}",
                            published_date=target_date,
                            source_url="https://ofac.treasury.gov/specially-designated-nationals-and-blocked-persons-list-sdn-human-readable-lists",
                            body_text=(
                                f"Entity: {entry['name']}\n"
                                f"Type: {entry['type']}\n"
                                f"Program: {entry['program']}\n"
                                f"Action: {change_type}"
                            ),
                            source_name="OFAC SDN List",
                            source_credibility="official",
                            article_type=f"SDN {change_type}",
                            extra_metadata=entry,
                        )
                    )

            logger.info(
                "OFAC SDN: %d additions, %d removals",
                len(changes.get("addition", [])),
                len(changes.get("removal", [])),
            )

            return ScrapeResult(
                source=self.get_source_id(),
                success=True,
                articles=articles,
                duration_seconds=int(time.time() - start),
            )

        except Exception as e:
            logger.error("OFAC SDN scrape failed: %s", e, exc_info=True)
            return ScrapeResult(
                source=self.get_source_id(),
                success=False,
                error=str(e),
                duration_seconds=int(time.time() - start),
            )

    def _download_and_filter(self) -> dict[str, dict]:
        """Download SDN CSV and return Venezuela-program entries keyed by UID."""
        logger.info("Downloading OFAC SDN CSV...")
        resp = self._fetch(SDN_CSV_URL)
        text = resp.text

        entries: dict[str, dict] = {}
        reader = csv.reader(io.StringIO(text))

        for row in reader:
            if len(row) < 12:
                continue

            uid = row[0].strip()
            name = row[1].strip()
            entity_type = row[2].strip()
            program = row[3].strip()

            program_upper = program.upper().strip()
            if any(vp in program_upper for vp in VENEZUELA_PROGRAMS):
                entries[uid] = {
                    "uid": uid,
                    "name": name,
                    "type": entity_type,
                    "program": program,
                    "remarks": row[11].strip() if len(row) > 11 else "",
                }

        logger.info("Filtered %d Venezuela-program SDN entries", len(entries))
        return entries

    def _load_previous_snapshot(self) -> dict[str, dict]:
        """Load the most recent snapshot JSON."""
        import json

        snapshots = sorted(SNAPSHOT_DIR.glob("sdn_ve_*.json"), reverse=True)
        if not snapshots:
            return {}

        try:
            return json.loads(snapshots[0].read_text())
        except Exception as e:
            logger.warning("Could not load previous snapshot: %s", e)
            return {}

    def _save_snapshot(self, entries: dict[str, dict], snap_date: date) -> None:
        import json

        path = SNAPSHOT_DIR / f"sdn_ve_{snap_date.isoformat()}.json"
        path.write_text(json.dumps(entries, indent=2, ensure_ascii=False))
        logger.info("Saved OFAC snapshot: %s (%d entries)", path, len(entries))

    def _diff(
        self,
        previous: dict[str, dict],
        current: dict[str, dict],
    ) -> dict[str, list[dict]]:
        prev_keys = set(previous.keys())
        curr_keys = set(current.keys())

        additions = [current[k] for k in (curr_keys - prev_keys)]
        removals = [previous[k] for k in (prev_keys - curr_keys)]

        return {"addition": additions, "removal": removals}
