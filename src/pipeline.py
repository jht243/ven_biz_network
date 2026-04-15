"""
Daily scraping pipeline orchestrator.

Runs all scrapers, persists results to the database, downloads PDFs,
and runs OCR on any downloaded gazette PDFs.

Usage:
    from src.pipeline import run_daily_scrape
    run_daily_scrape()                    # today
    run_daily_scrape(date(2026, 3, 27))   # specific date
"""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Optional

from sqlalchemy.exc import IntegrityError

from src.config import settings
from src.models import (
    SessionLocal, init_db,
    GazetteEntry, AssemblyNewsEntry, ExternalArticleEntry, ScrapeLog,
    SourceType, CredibilityTier, GazetteStatus, GazetteType,
)
from src.scraper.base import ScrapedGazette, ScrapedNews, ScrapedArticle, ScrapeResult
from src.scraper.gazette import TuGacetaScraper, OfficialGazetteScraper
from src.scraper.assembly import AssemblyNewsScraper
from src.scraper.federal_register import FederalRegisterScraper
from src.scraper.ofac_sdn import OFACSdnScraper
from src.scraper.gdelt import GDELTScraper
from src.scraper.bcv import BCVScraper
from src.scraper.travel_advisory import TravelAdvisoryScraper
from src.ocr.engine import ocr_pdf

logger = logging.getLogger(__name__)


def run_daily_scrape(target_date: Optional[date] = None) -> dict:
    """
    Run the full daily scraping pipeline:
      1. Scrape all sources
      2. Persist new entries to DB
      3. Download PDFs where available
      4. Run OCR on downloaded PDFs
      5. Log results

    Returns a summary dict with counts.
    """
    target_date = target_date or date.today()
    init_db()

    logger.info("=" * 60)
    logger.info("Starting daily scrape for %s", target_date)
    logger.info("=" * 60)

    summary = {
        "date": str(target_date),
        "gazettes_found": 0,
        "gazettes_new": 0,
        "news_found": 0,
        "news_new": 0,
        "articles_found": 0,
        "articles_new": 0,
        "pdfs_downloaded": 0,
        "ocr_completed": 0,
        "errors": [],
    }

    # --- Phase 1: Scrape all sources ---
    scrape_results: list[ScrapeResult] = []

    scrapers = [
        TuGacetaScraper(),
        OfficialGazetteScraper(),
        AssemblyNewsScraper(),
        FederalRegisterScraper(),
        OFACSdnScraper(),
        GDELTScraper(),
        BCVScraper(),
        TravelAdvisoryScraper(),
    ]

    for scraper in scrapers:
        try:
            logger.info("Running scraper: %s", scraper.get_source_id())
            result = scraper.scrape(target_date)
            scrape_results.append(result)
            _log_scrape(result, target_date)

            if not result.success:
                summary["errors"].append(f"{scraper.get_source_id()}: {result.error}")
        except Exception as e:
            logger.error("Scraper %s crashed: %s", scraper.get_source_id(), e, exc_info=True)
            summary["errors"].append(f"{scraper.get_source_id()}: {e}")
        finally:
            scraper.close()

    # --- Phase 2: Persist gazette entries ---
    all_gazettes = []
    for r in scrape_results:
        all_gazettes.extend(r.gazettes)
    summary["gazettes_found"] = len(all_gazettes)

    new_gazettes = _persist_gazettes(all_gazettes)
    summary["gazettes_new"] = len(new_gazettes)

    # --- Phase 3: Persist assembly news ---
    all_news = []
    for r in scrape_results:
        all_news.extend(r.news)
    summary["news_found"] = len(all_news)

    new_news = _persist_news(all_news)
    summary["news_new"] = len(new_news)

    # --- Phase 3b: Persist external articles ---
    all_articles = []
    for r in scrape_results:
        all_articles.extend(r.articles)
    summary["articles_found"] = len(all_articles)

    new_articles = _persist_articles(all_articles)
    summary["articles_new"] = len(new_articles)

    # --- Phase 4: Download PDFs and run OCR ---
    for gazette_id, pdf_url in new_gazettes:
        if not pdf_url:
            continue

        # Skip MEGA links for now (require special handling)
        if "mega.nz" in pdf_url:
            logger.info(
                "Skipping MEGA download for gazette %d — manual download required: %s",
                gazette_id, pdf_url,
            )
            continue

        try:
            db = SessionLocal()
            entry = db.query(GazetteEntry).get(gazette_id)
            if not entry:
                continue

            scraper = TuGacetaScraper()
            pdf_path, pdf_hash = scraper._download_pdf(pdf_url, entry.gazette_number or str(gazette_id))
            scraper.close()

            entry.pdf_path = str(pdf_path)
            entry.pdf_hash = pdf_hash
            db.commit()
            summary["pdfs_downloaded"] += 1

            # Run OCR
            ocr_result = ocr_pdf(pdf_path)
            entry.ocr_text = ocr_result.text
            entry.ocr_confidence = ocr_result.avg_confidence
            entry.status = GazetteStatus.OCR_COMPLETE
            db.commit()
            summary["ocr_completed"] += 1

            logger.info(
                "OCR complete for gazette %d: confidence=%d%%, pages=%d",
                gazette_id, ocr_result.avg_confidence, ocr_result.page_count,
            )

        except Exception as e:
            logger.error("PDF/OCR failed for gazette %d: %s", gazette_id, e, exc_info=True)
            summary["errors"].append(f"ocr_gazette_{gazette_id}: {e}")
        finally:
            db.close()

    logger.info("=" * 60)
    logger.info("Scrape complete: %s", summary)
    logger.info("=" * 60)

    return summary


def _persist_gazettes(gazettes: list[ScrapedGazette]) -> list[tuple[int, Optional[str]]]:
    """
    Insert new gazette entries into the DB. Skips duplicates by source_url.
    Returns list of (id, pdf_download_url) for newly inserted entries.
    """
    new_entries = []
    db = SessionLocal()

    try:
        for g in gazettes:
            entry = GazetteEntry(
                gazette_number=g.gazette_number,
                gazette_type=GazetteType(g.gazette_type),
                published_date=g.published_date,
                source=SourceType(g.source),
                source_url=g.source_url,
                title=g.title,
                sumario_raw=g.sumario_text,
                pdf_download_url=g.pdf_download_url,
                status=GazetteStatus.SCRAPED,
            )
            db.add(entry)
            try:
                db.flush()
                new_entries.append((entry.id, g.pdf_download_url))
                logger.info("Persisted gazette: %s (%s)", g.gazette_number, g.source)
            except IntegrityError:
                db.rollback()
                logger.debug("Duplicate gazette skipped: %s", g.source_url)

        db.commit()
    finally:
        db.close()

    return new_entries


def _persist_news(news_items: list[ScrapedNews]) -> list[int]:
    """Insert new assembly news entries. Returns list of new IDs."""
    new_ids = []
    db = SessionLocal()

    try:
        for n in news_items:
            entry = AssemblyNewsEntry(
                headline=n.headline,
                published_date=n.published_date,
                source_url=n.source_url,
                body_text=n.body_text,
                commission=n.commission,
                status=GazetteStatus.SCRAPED,
            )
            db.add(entry)
            try:
                db.flush()
                new_ids.append(entry.id)
                logger.info("Persisted news: %s", n.headline[:80])
            except IntegrityError:
                db.rollback()
                logger.debug("Duplicate news skipped: %s", n.source_url)

        db.commit()
    finally:
        db.close()

    return new_ids


def _persist_articles(articles: list[ScrapedArticle]) -> list[int]:
    """Insert external articles into the DB. Skips duplicates by source+URL."""
    new_ids = []
    db = SessionLocal()

    credibility_map = {
        "official": CredibilityTier.OFFICIAL,
        "tier1": CredibilityTier.TIER1,
        "tier2": CredibilityTier.TIER2,
        "state": CredibilityTier.STATE,
    }

    try:
        for a in articles:
            source_type = _resolve_source_type(a.source_name)
            cred = credibility_map.get(a.source_credibility, CredibilityTier.TIER2)
            tone = a.extra_metadata.get("tone") if a.extra_metadata else None

            entry = ExternalArticleEntry(
                source=source_type,
                source_url=a.source_url,
                source_name=a.source_name,
                credibility=cred,
                headline=a.headline,
                published_date=a.published_date,
                body_text=a.body_text,
                article_type=a.article_type,
                tone_score=float(tone) if tone is not None else None,
                extra_metadata=a.extra_metadata,
                status=GazetteStatus.SCRAPED,
            )
            db.add(entry)
            try:
                db.flush()
                new_ids.append(entry.id)
                logger.info("Persisted article: %s [%s]", a.headline[:80], a.source_name)
            except IntegrityError:
                db.rollback()
                logger.debug("Duplicate article skipped: %s", a.source_url)

        db.commit()
    finally:
        db.close()

    return new_ids


def _resolve_source_type(source_name: str) -> SourceType:
    """Map a source name string to a SourceType enum value."""
    name_lower = (source_name or "").lower()
    mapping = {
        "federal register": SourceType.FEDERAL_REGISTER,
        "ofac": SourceType.OFAC_SDN,
        "ofac sdn": SourceType.OFAC_SDN,
        "gdelt": SourceType.GDELT,
        "banco central": SourceType.BCV_RATES,
        "bcv": SourceType.BCV_RATES,
        "state department": SourceType.TRAVEL_ADVISORY,
        "us state department": SourceType.TRAVEL_ADVISORY,
        "newsdata": SourceType.NEWSDATA,
        "eia": SourceType.EIA,
    }
    for key, val in mapping.items():
        if key in name_lower:
            return val
    return SourceType.GDELT


def _log_scrape(result: ScrapeResult, target_date: date) -> None:
    """Write a scrape log entry for diagnostics."""
    db = SessionLocal()
    try:
        try:
            source = SourceType(result.source)
        except ValueError:
            logger.warning("Unknown source type '%s', skipping log", result.source)
            return

        log = ScrapeLog(
            source=source,
            scrape_date=target_date,
            success=result.success,
            entries_found=len(result.gazettes) + len(result.news) + len(result.articles),
            error_message=result.error,
            duration_seconds=result.duration_seconds,
        )
        db.add(log)
        db.commit()
    finally:
        db.close()
