#!/usr/bin/env python3
"""
One-off helper: re-run the LLM analyzer on already-analyzed entries that
are missing the new `calendar_event` field, so the dynamic calendar can
populate without waiting for tomorrow's cron.

Cost: ~$0.005 per entry (only re-touches entries with relevance >= 6
and no existing `calendar_event` key, so the typical run is < $0.50).

Usage:
    python -m scripts.backfill_calendar_events
    python -m scripts.backfill_calendar_events --min-relevance 5
    python -m scripts.backfill_calendar_events --dry-run
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta

import click
from openai import OpenAI

from src.analyzer import _analyze_article
from src.config import settings
from src.models import (
    AssemblyNewsEntry,
    ExternalArticleEntry,
    GazetteStatus,
    SessionLocal,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-30s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill_calendar")


@click.command()
@click.option("--min-relevance", default=6, help="Only touch entries with at least this relevance score.")
@click.option("--dry-run", is_flag=True, help="List what would be re-analyzed; don't call the LLM.")
def main(min_relevance: int, dry_run: bool):
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY is not set")

    client = OpenAI(api_key=settings.openai_api_key)
    db = SessionLocal()
    cutoff = date.today() - timedelta(days=settings.report_lookback_days)

    try:
        ext = (
            db.query(ExternalArticleEntry)
            .filter(ExternalArticleEntry.status == GazetteStatus.ANALYZED)
            .filter(ExternalArticleEntry.published_date >= cutoff)
            .all()
        )
        an = (
            db.query(AssemblyNewsEntry)
            .filter(AssemblyNewsEntry.status == GazetteStatus.ANALYZED)
            .filter(AssemblyNewsEntry.published_date >= cutoff)
            .all()
        )

        targets: list = []
        for entry in ext + an:
            analysis = entry.analysis_json or {}
            if analysis.get("relevance_score", 0) < min_relevance:
                continue
            if "calendar_event" in analysis:
                continue
            targets.append(entry)

        targets.sort(key=lambda e: e.published_date, reverse=True)
        logger.info(
            "Found %d entries (relevance >= %d, missing calendar_event)",
            len(targets), min_relevance,
        )

        if dry_run:
            for t in targets:
                logger.info("  would re-analyze: [%s] %s", t.published_date, (t.headline or "")[:80])
            return

        success = 0
        errors = 0
        for entry in targets:
            try:
                if isinstance(entry, AssemblyNewsEntry):
                    source_name = "Asamblea Nacional"
                    credibility = "state"
                else:
                    source_name = entry.source_name or "Unknown"
                    credibility = entry.credibility.value if entry.credibility else "tier2"

                analysis = _analyze_article(
                    client,
                    headline=entry.headline,
                    body_text=entry.body_text or "",
                    source_name=source_name,
                    credibility=credibility,
                    published_date=str(entry.published_date),
                    source_url=entry.source_url,
                )
                entry.analysis_json = analysis
                db.commit()
                success += 1
                ev = analysis.get("calendar_event")
                logger.info(
                    "Re-analyzed [%d/%d]: %s (rel=%s, cal=%s)",
                    success,
                    len(targets),
                    (entry.headline or "")[:60],
                    analysis.get("relevance_score"),
                    "yes" if ev else "no",
                )
            except Exception as e:
                errors += 1
                logger.error("Failed for entry %s: %s", entry.id, e)
                db.rollback()
            time.sleep(0.5)

        logger.info("Done. success=%d, errors=%d", success, errors)
    finally:
        db.close()


if __name__ == "__main__":
    main()
