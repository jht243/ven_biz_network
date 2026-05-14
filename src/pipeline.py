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

import json
import logging
import re
import time
import unicodedata
from datetime import date, timedelta
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
from src.scraper.google_news import GoogleNewsScraper
from src.scraper.ansa_latina import AnsaLatinaScraper
from src.scraper.bcv import BCVScraper
from src.scraper.ita import ITATradeScraper
from src.scraper.bonds import VenezuelaBondsScraper
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
        AnsaLatinaScraper(),
        # Parallel feed to GDELT — see src/scraper/google_news.py for
        # the rationale. The DB unique-constraint is (source, source_url)
        # so a Reuters article showing up in BOTH GDELT and Google News
        # WILL be persisted twice — that is intentional, downstream
        # dedupe by headline-similarity belongs in the report layer.
        GoogleNewsScraper(),
        BCVScraper(),
        ITATradeScraper(),
        VenezuelaBondsScraper(),
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

    # Cap-and-rank pass for Google News BEFORE persistence. This keeps
    # the homepage / blog-gen pipeline focused on the highest-signal
    # articles each day rather than letting a busy news week bury
    # quieter sources under syndicated wire-story noise. Other sources
    # pass through untouched — gazettes, OFAC, Federal Register, etc.
    # are intentionally always-on regardless of volume.
    all_articles = _apply_google_news_daily_cap(all_articles)

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
            nested = db.begin_nested()
            try:
                db.add(entry)
                db.flush()
                nested.commit()
                new_entries.append((entry.id, g.pdf_download_url))
                logger.info("Persisted gazette: %s (%s)", g.gazette_number, g.source)
            except IntegrityError:
                nested.rollback()
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
            nested = db.begin_nested()
            try:
                db.add(entry)
                db.flush()
                nested.commit()
                new_ids.append(entry.id)
                logger.info("Persisted news: %s", n.headline[:80])
            except IntegrityError:
                nested.rollback()
                logger.debug("Duplicate news skipped: %s", n.source_url)

        db.commit()
    finally:
        db.close()

    return new_ids


def _persist_articles(articles: list[ScrapedArticle]) -> list[int]:
    """Insert external articles into the DB.

    Two layers of dedup:
      1. (source, source_url) UNIQUE constraint at the DB level —
         catches the same Google News URL fetched twice.
      2. Cross-source headline match (Google News only) — catches the
         same wire story landing via different URLs (e.g. a Reuters
         piece we already have via GDELT, or the same MSN syndication
         surfaced through two different RSS queries).

    Special case — mutable-page sources (ITA_TRADE):
      These sources publish a single stable URL whose page content changes
      over time (e.g. trade.gov/venezuela-trade-leads adds new leads in
      place rather than publishing a new URL per lead). On a URL collision
      we compare a lightweight content fingerprint; if the fingerprint
      differs we update body_text, extra_metadata, published_date, and
      reset status to SCRAPED so the analysis pipeline re-evaluates the
      refreshed content.
    """
    # Sources whose page content changes in-place. On a URL collision,
    # upsert instead of silently skipping.
    MUTABLE_PAGE_SOURCES: frozenset[SourceType] = frozenset({
        SourceType.ITA_TRADE,
    })

    new_ids = []
    db = SessionLocal()

    credibility_map = {
        "official": CredibilityTier.OFFICIAL,
        "tier1": CredibilityTier.TIER1,
        "tier2": CredibilityTier.TIER2,
        "state": CredibilityTier.STATE,
    }

    # Snapshot of recent headlines before the loop. Loaded once so we
    # don't re-query per article. As we insert new rows below we also
    # extend this in-memory index, which is what makes within-batch
    # dedup work (e.g. GDELT-then-Google-News in the same call).
    recent_headlines = _load_recent_headline_index(db)

    try:
        for a in articles:
            source_type = _resolve_source_type(a.source_name)
            cred = credibility_map.get(a.source_credibility, CredibilityTier.TIER2)
            tone = a.extra_metadata.get("tone") if a.extra_metadata else None

            # Cross-source headline dedup, scoped to Google News.
            # Other scrapers either have stable canonical URLs (Federal
            # Register, OFAC) or are intentionally allowed to overlap
            # (GDELT vs Federal Register tracking the same OFAC action
            # from different angles), so widening this would cause more
            # harm than good.
            if source_type == SourceType.GOOGLE_NEWS:
                match = _match_existing_headline(a.headline, recent_headlines)
                if match is not None:
                    logger.info(
                        "Google News dedupe: skipping %r (matches existing %r)",
                        (a.headline or "")[:80], match[:80],
                    )
                    continue

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
            nested = db.begin_nested()
            try:
                db.add(entry)
                db.flush()
                nested.commit()
                new_ids.append(entry.id)
                logger.info("Persisted article: %s [%s]", a.headline[:80], a.source_name)
                # Keep the in-memory index hot for within-batch dedup.
                _index_headline(a.headline, recent_headlines)
            except IntegrityError:
                nested.rollback()
                # For mutable-page sources, check whether content has changed
                # and upsert if it has.
                if source_type in MUTABLE_PAGE_SOURCES:
                    _upsert_mutable_article(db, a, source_type, cred, new_ids)
                else:
                    logger.debug("Duplicate article skipped: %s", a.source_url)

        db.commit()
    finally:
        db.close()

    return new_ids


def _upsert_mutable_article(
    db,
    a: "ScrapedArticle",
    source_type: "SourceType",
    cred: "CredibilityTier",
    new_ids: list[int],
) -> None:
    """
    Update an existing mutable-page article row when its content has changed.

    Computes a full-content fingerprint (entire body_text + entire trade_leads
    list) to detect any addition anywhere on the page. When the fingerprint
    differs, computes the exact lead-level diff so the LLM analyzer and press
    radar receive precisely which rows are new rather than re-reading the whole
    page and guessing. The diff is stored in extra_metadata["new_trade_leads"]
    and the headline is updated to surface the count of new leads.
    """
    import hashlib, json as _json

    existing = (
        db.query(ExternalArticleEntry)
        .filter(
            ExternalArticleEntry.source == source_type,
            ExternalArticleEntry.source_url == a.source_url,
        )
        .first()
    )
    if existing is None:
        return

    def _lead_key(lead: dict) -> tuple:
        return (lead.get("equipment", ""), lead.get("hs_code", ""), lead.get("units_requested"))

    def _fp(body: str | None, meta: dict | None) -> str:
        # Hash the ENTIRE body text and the full trade-leads list so a new
        # row appended anywhere — including the bottom of a 30-row table —
        # changes the digest. Stable sort so harmless reordering is ignored.
        leads_key = ""
        if meta and "trade_leads" in meta:
            leads_key = _json.dumps(
                sorted(meta["trade_leads"], key=_lead_key),
                sort_keys=True,
            )
        return hashlib.md5(((body or "") + leads_key).encode()).hexdigest()

    fresh_fp = _fp(a.body_text, a.extra_metadata)
    stored_fp = _fp(existing.body_text, existing.extra_metadata or {})

    if fresh_fp == stored_fp:
        logger.debug("Mutable-page article unchanged, skipping update: %s", a.source_url)
        return

    # For ITA pages that carry no trade leads at all (FAQ, hub, contacts),
    # a body-text change is cosmetic — a nav-menu tweak or copy edit. There
    # is no structured investment signal to re-analyze, so skip the upsert
    # entirely rather than flooding the analysis queue with noise.
    has_fresh_leads = bool(fresh_leads)
    has_stored_leads = bool(stored_leads)
    if not has_fresh_leads and not has_stored_leads:
        logger.debug(
            "Mutable-page article body changed but no trade leads present — skipping upsert: %s",
            a.source_url,
        )
        return

    # Compute the lead-level diff so downstream consumers (analyzer, press
    # radar) see exactly what is new rather than the full page.
    fresh_leads: list[dict] = (a.extra_metadata or {}).get("trade_leads", [])
    stored_leads: list[dict] = (existing.extra_metadata or {}).get("trade_leads", [])
    stored_keys: set[tuple] = {_lead_key(l) for l in stored_leads}
    new_leads: list[dict] = [l for l in fresh_leads if _lead_key(l) not in stored_keys]

    removed_keys: set[tuple] = {_lead_key(l) for l in fresh_leads}
    removed_leads: list[dict] = [l for l in stored_leads if _lead_key(l) not in removed_keys]

    # Annotate the article so the LLM sees a focused change summary.
    updated_meta = dict(a.extra_metadata or {})
    updated_meta["new_trade_leads"] = new_leads
    updated_meta["removed_trade_leads"] = removed_leads
    updated_meta["previous_lead_count"] = len(stored_leads)
    updated_meta["current_lead_count"] = len(fresh_leads)

    # Build a headline that makes the delta immediately legible to the LLM.
    if new_leads:
        sectors = sorted({l.get("sector", "General") for l in new_leads})
        sector_str = ", ".join(sectors)
        headline = (
            f"trade.gov Venezuela Trade Leads — {len(new_leads)} new lead"
            f"{'s' if len(new_leads) != 1 else ''} added"
            f" ({sector_str}): {len(fresh_leads)} total"
        )
    else:
        headline = (
            f"trade.gov Venezuela Trade Leads — content updated "
            f"({len(fresh_leads)} leads; {len(removed_leads)} removed)"
        )

    existing.body_text = a.body_text
    existing.extra_metadata = updated_meta
    existing.headline = headline
    existing.published_date = a.published_date
    existing.status = GazetteStatus.SCRAPED
    db.flush()
    new_ids.append(existing.id)
    logger.info(
        "ITA trade leads updated: +%d new, -%d removed, %d total [%s]",
        len(new_leads),
        len(removed_leads),
        len(fresh_leads),
        source_type,
    )


# ── headline dedup ─────────────────────────────────────────────────────
# Cross-source dedup against existing rows. Used by _persist_articles
# to keep Google News from re-importing wire stories we already have
# from GDELT (or from a different Google News query in the same run).

# Words too common to be a useful similarity signal — without this,
# any two Venezuela headlines would share enough tokens to false-match.
_HEADLINE_STOPWORDS = frozenset({
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "and", "or",
    "is", "are", "was", "were", "be", "been", "by", "with", "as", "from",
    "that", "this", "it", "its", "but", "not", "have", "has", "had",
    "will", "would", "should", "could", "may", "might", "can", "do",
    "does", "did", "say", "says", "said", "new", "more", "than",
    # Spanish equivalents — Google News returns Spanish-language items
    # too and they go through the same normaliser.
    "el", "la", "los", "las", "un", "una", "unos", "unas",
    "de", "del", "y", "o", "en", "con", "se", "que", "por", "para",
    "ser", "es", "son", "fue", "fueron", "sera", "seran",
    "lo", "le", "su", "sus", "al", "como", "mas", "pero", "si", "no",
})

_HEADLINE_DEDUP_LOOKBACK_DAYS = 14
# How much of two normalised token sets must overlap to count as a
# duplicate. 0.85 was chosen because:
#   - "Reuters: Venezuela hikes oil exports" vs "Venezuela hikes oil
#     exports - Reuters" → 1.0 (after publisher strip)
#   - "Repsol agrees to new conditions to increase its oil production
#     in Venezuela" vs "Repsol expands Venezuela oil output under
#     revised PDVSA pact" → ~0.3 (correctly NOT a dup; both worth showing)
#   - "IMF, World Bank say they are restoring ties with Venezuela" vs
#     "IMF, World Bank say they are resuming dealings with Venezuela"
#     → ~0.78 (NOT a dup — different wire stories, both worth showing)
_HEADLINE_JACCARD_THRESHOLD = 0.85


def _normalize_headline(headline: str) -> str:
    """Canonical form of a headline for cross-source dedup.

    Lowercases, strips Google News' " - Publisher" suffix, removes
    diacritics and punctuation, collapses whitespace.
    """
    if not headline:
        return ""
    text = headline
    # Drop the "...Headline... - Publisher Name" tail Google News
    # appends in its RSS feed. Without this, the same Reuters story
    # ingested via GDELT (clean headline) and via Google News (with
    # publisher suffix) would not normalise to the same form.
    if " - " in text:
        text = text.rsplit(" - ", 1)[0]
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _headline_token_set(normalized: str) -> frozenset[str]:
    return frozenset(
        t for t in normalized.split()
        if len(t) > 2 and t not in _HEADLINE_STOPWORDS
    )


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _load_recent_headline_index(db) -> list[tuple[str, frozenset[str]]]:
    """Snapshot of recent external-article headlines for dedup.

    Returns a list of (normalized_headline, token_set) tuples. Loaded
    once per pipeline run to avoid a per-article query."""
    cutoff = date.today() - timedelta(days=_HEADLINE_DEDUP_LOOKBACK_DAYS)
    rows = (
        db.query(ExternalArticleEntry.headline)
        .filter(ExternalArticleEntry.published_date >= cutoff)
        .all()
    )
    index: list[tuple[str, frozenset[str]]] = []
    for (headline,) in rows:
        normalized = _normalize_headline(headline)
        if not normalized:
            continue
        index.append((normalized, _headline_token_set(normalized)))
    return index


def _index_headline(headline: str, index: list[tuple[str, frozenset[str]]]) -> None:
    """Append a freshly-inserted headline to the in-memory index so it
    participates in within-batch dedup for the rest of the run."""
    normalized = _normalize_headline(headline)
    if not normalized:
        return
    index.append((normalized, _headline_token_set(normalized)))


def _match_existing_headline(
    headline: str,
    index: list[tuple[str, frozenset[str]]],
) -> Optional[str]:
    """Return the existing headline if `headline` looks like a duplicate
    of something in the index, else None."""
    normalized = _normalize_headline(headline)
    if not normalized:
        return None
    tokens = _headline_token_set(normalized)
    if not tokens:
        # Headline was effectively all stopwords — refuse to dedup
        # rather than risk a false positive.
        return None
    for existing_norm, existing_tokens in index:
        if existing_norm == normalized:
            return existing_norm
        if _jaccard(tokens, existing_tokens) >= _HEADLINE_JACCARD_THRESHOLD:
            return existing_norm
    return None


# ── Google News daily cap + investor-interest ranking ─────────────────
# Google News can return 100+ items/day. We persist at most
# settings.google_news_daily_cap of them, picking the ones most likely
# to matter to a Venezuela investor reader.
#
# This intentionally does NOT call the LLM. The downstream analyzer
# already pays for an LLM relevance pass on every persisted article;
# running a second LLM call here just to triage what to persist would
# double the per-article cost. The heuristic below is a fast,
# deterministic proxy that's correlated enough with the LLM's relevance
# score that its top-6 picks usually overlap heavily with the LLM's.

# Keyword-to-weight table. Tuned for the journal's investor audience —
# OFAC actions and Chevron/PDVSA deals score highest because they
# directly move M&A and capital-flow decisions; general "oil" or "gas"
# scores low because the keyword alone doesn't carry investor signal
# (every Venezuela story mentions oil somewhere).
_INVESTOR_KEYWORDS: dict[str, float] = {
    # Sanctions / OFAC — the most-watched signal in this market
    "sanctions": 3.0, "sanction": 3.0, "ofac": 3.0,
    "treasury": 2.5, "general license": 3.0, "license": 1.5,
    "sancion": 3.0, "sanciones": 3.0, "levantamiento": 2.0,
    # Major operators / known IOCs
    "pdvsa": 2.5, "chevron": 2.5, "repsol": 2.0, "shell": 2.0,
    "eni": 2.0, "totalenergies": 2.0, "rosneft": 2.0, "maurel": 1.5,
    # Multilateral institutions
    "imf": 2.5, "world bank": 2.5, "fmi": 2.5, "banco mundial": 2.5,
    # Policy / regulatory law
    "mining law": 2.5, "ley de minas": 2.5, "ley organica": 2.0,
    "oil law": 2.0, "ley de hidrocarburos": 2.0,
    "decree": 1.5, "decreto": 1.5,
    # Investment vocabulary
    "investment": 2.0, "investor": 2.0, "investors": 2.0,
    "inversion": 2.0, "inversionista": 2.0, "fdi": 2.5,
    "expropriation": 2.5, "expropiacion": 2.5,
    "nationalization": 2.5, "nacionalizacion": 2.5,
    # Monetary / currency
    "central bank": 2.0, "bcv": 2.0, "banco central": 2.0,
    "exchange rate": 2.0, "tipo de cambio": 2.0,
    "inflation": 1.5, "inflacion": 1.5, "bolivar": 1.5,
    # Sector keywords (lower weight — broad)
    "mining": 1.5, "mineria": 1.5, "minas": 1.5, "gold": 1.5, "oro": 1.5,
    "oil": 1.0, "petroleo": 1.0, "gas": 1.0,
    "energy": 1.0, "energia": 1.0, "electricity": 1.0, "electric": 1.0,
    # Governance / legal
    "court": 1.5, "tsj": 1.5, "ruling": 1.5, "election": 1.5,
    "elecciones": 1.5, "machado": 1.5, "maduro": 1.0,
}

_CREDIBILITY_WEIGHTS: dict[str, float] = {
    "official": 2.5,
    "tier1": 2.0,
    "tier2": 1.0,
    "state": 0.5,
}

# Hard recency cutoff for the cap pass. Google News indexes evergreen
# law-firm explainers for months, and without this they outscore actual
# breaking news because they're keyword-dense. 7 days mirrors the
# homepage's display window.
_GOOGLE_NEWS_MAX_AGE_DAYS = 7

# At most this many accepted articles can share the same publisher.
# Without this, a busy publisher like Orinoco Tribune or a wave of
# law-firm OFAC explainers can monopolise the daily slots.
_GOOGLE_NEWS_MAX_PER_PUBLISHER = 2

# In-batch similarity threshold — applied between two candidates
# competing for a slot in the SAME daily run. Set tighter than the
# cross-DB dedup threshold (_HEADLINE_JACCARD_THRESHOLD = 0.85)
# because the cost of false-positives is much lower here: if we drop
# a "distinct but similar" candidate we still get ~5 other unique
# articles instead of 6, but if we keep both Reuters and Bloomberg's
# rewrites of the same news event we waste a slot. Empirically 0.40
# catches obvious co-coverage of the same event (Eni/Repsol Cardón
# headlines from two outlets) without suppressing genuinely different
# stories that happen to share investor vocabulary.
_GOOGLE_NEWS_IN_BATCH_DIVERSITY_THRESHOLD = 0.40


def _investor_interest_score(article: ScrapedArticle) -> float:
    """Heuristic 0-15ish score predicting how relevant an article is
    to a Venezuela-investment audience. Used to triage which Google
    News candidates to persist when the daily cap is hit.

    The score combines four signals:
      1. Keyword density — investor-vocabulary matches in headline+snippet
      2. Source credibility — tier1 publishers weighted higher than tier2
      3. Recency — same-day articles slightly favoured over week-old ones
      4. Headline length sanity — extremely short or empty headlines penalised
    """
    headline = (article.headline or "").lower()
    snippet = ""
    if article.extra_metadata:
        snippet = (article.extra_metadata.get("snippet") or "").lower()
    text = f"{headline} {snippet}"

    keyword_score = sum(
        weight for kw, weight in _INVESTOR_KEYWORDS.items() if kw in text
    )

    cred_score = _CREDIBILITY_WEIGHTS.get(article.source_credibility, 0.5)

    days_old = max(0, (date.today() - article.published_date).days)
    # +1.5 today, +1.0 yesterday, +0.5 day-before, 0 thereafter
    recency_bonus = max(0.0, 1.5 - 0.5 * days_old)

    # Penalty for headline stubs that survived parsing — these are
    # almost always low-quality items (e.g. "Venezuela", "...").
    length_penalty = -2.0 if len(headline.split()) < 4 else 0.0

    return keyword_score + cred_score + recency_bonus + length_penalty


def _count_google_news_persisted_today(db) -> int:
    """How many Google News articles have we already persisted today?
    Used to compute the remaining daily cap when the cron runs more
    than once per day (manual re-run, retry, etc)."""
    today = date.today()
    return (
        db.query(ExternalArticleEntry)
        .filter(ExternalArticleEntry.source == SourceType.GOOGLE_NEWS)
        .filter(ExternalArticleEntry.created_at >= today)
        .count()
    )


# LLM clustering prompt — gpt-4o-mini reads ~30 numbered headlines
# and groups the ones that report the same real-world event. The
# strict definition matters because "same topic" (Venezuela oil) and
# "same event" (the April 15 OFAC General License) are very different;
# we want to dedupe the latter while keeping the former diverse.
_CLUSTERING_SYSTEM_PROMPT = """You are an expert news editor for an investor newsletter focused on Venezuela.

You will receive a numbered list of news headlines with dates and publishers. Your job is to group headlines that report the SAME real-world event into clusters.

DEFINITION OF "SAME EVENT" (group together):
- Same OFAC announcement / general license, covered by multiple outlets
- Same M&A deal, contract signing, or partnership announcement
- Same court ruling, law signing, or government decree
- Same speech, press conference, or appointment
- Same wire story republished by syndicators (Reuters via MSN, AP via Yahoo, etc.)

NOT THE SAME EVENT (keep separate):
- Two different stories about the same company on the same day (e.g. Chevron earnings vs Chevron Venezuela contract)
- A weekly / monthly recap vs a single-event article
- Sector commentary vs a specific deal or policy
- An analysis or explainer published days/weeks after the underlying event
- A call for action or opinion piece vs the news it references

OUTPUT FORMAT (strict JSON, no prose, no markdown):
{
  "clusters": [
    [1, 4, 7],
    [2],
    [3, 5]
  ]
}

Every input ID must appear in exactly one cluster. Single-item clusters are expected and fine — most headlines are unique events.
"""


def _llm_cluster_candidates(
    candidates: list[ScrapedArticle],
) -> list[list[int]]:
    """Group candidates that report the same real-world news event.

    Returns clusters as 0-indexed lists. On any failure (no API key,
    network error, malformed JSON, model timeout) returns the identity
    clustering (one cluster per item) so the caller's downstream logic
    works either way.

    Cost: one gpt-4o-mini call per pipeline run with ~30 headlines.
    Rough budget: ~600 input tokens + ~200 output tokens ≈ $0.0002/run.
    """
    fallback = [[i] for i in range(len(candidates))]

    if not candidates:
        return []
    if len(candidates) <= 1:
        return fallback
    if not settings.openai_api_key:
        logger.info("LLM clustering: no OPENAI_API_KEY; using identity clusters")
        return fallback

    lines = []
    for i, c in enumerate(candidates, start=1):
        pub = (c.extra_metadata or {}).get("publisher") or ""
        snippet = (c.extra_metadata or {}).get("snippet") or ""
        # Snippet adds disambiguation signal beyond the headline alone
        # (e.g. distinguishing two "Chevron Venezuela" stories) at
        # near-zero token cost.
        tail = f' ({snippet[:120]})' if snippet else ""
        lines.append(
            f'{i}. [{c.published_date}] "{c.headline}" — {pub}{tail}'
        )
    user_msg = "Cluster these Venezuela news headlines:\n\n" + "\n".join(lines)

    try:
        # Local import keeps openai out of the import path of any
        # caller that doesn't run the daily pipeline (tests, scripts).
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)
        response = client.chat.completions.create(
            model=settings.openai_narrative_model,
            messages=[
                {"role": "system", "content": _CLUSTERING_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.0,
            max_tokens=600,
            response_format={"type": "json_object"},
            timeout=20.0,
        )
        raw = response.choices[0].message.content or "{}"
        parsed = json.loads(raw)
        raw_clusters = parsed.get("clusters", [])

        if not isinstance(raw_clusters, list):
            raise ValueError(f"clusters not a list: {type(raw_clusters).__name__}")

        clusters: list[list[int]] = []
        seen: set[int] = set()
        for cluster in raw_clusters:
            if not isinstance(cluster, list):
                continue
            valid = []
            for n in cluster:
                try:
                    idx = int(n) - 1
                except (TypeError, ValueError):
                    continue
                if 0 <= idx < len(candidates) and idx not in seen:
                    valid.append(idx)
                    seen.add(idx)
            if valid:
                clusters.append(valid)

        # Anything the LLM dropped becomes its own singleton cluster
        # — better to over-emit than to lose a candidate entirely.
        for i in range(len(candidates)):
            if i not in seen:
                clusters.append([i])

        merged_count = sum(1 for c in clusters if len(c) > 1)
        merged_total = sum(len(c) - 1 for c in clusters if len(c) > 1)
        logger.info(
            "LLM clustering: %d candidates -> %d clusters "
            "(%d multi-item clusters merged %d duplicates)",
            len(candidates), len(clusters), merged_count, merged_total,
        )
        return clusters

    except Exception as exc:
        logger.warning(
            "LLM clustering failed (%s: %s) — falling back to identity clusters",
            type(exc).__name__, exc,
        )
        return fallback


def _apply_google_news_daily_cap(
    articles: list[ScrapedArticle],
) -> list[ScrapedArticle]:
    """Prune the article list to enforce settings.google_news_daily_cap.

    Pipeline:
      1. Split GN candidates from the rest (rest passes through).
      2. Drop anything older than _GOOGLE_NEWS_MAX_AGE_DAYS.
      3. Drop anything we already have in the DB (token-jaccard match).
      4. LLM-cluster the survivors by underlying news event.
      5. Within each cluster, keep only the highest investor-score item.
      6. Rank cluster representatives, apply publisher diversity guard,
         in-batch token-jaccard guard (defence-in-depth for when LLM
         clustering fails or under-merges), and take top N.

    Returns the combined list ready for _persist_articles.
    """
    cap = settings.google_news_daily_cap
    if cap <= 0:
        # Cap of 0 → drop all GN articles. Used to disable Google News
        # without unwiring the scraper from the pipeline.
        non_gn = [a for a in articles if _resolve_source_type(a.source_name) != SourceType.GOOGLE_NEWS]
        gn_dropped = len(articles) - len(non_gn)
        if gn_dropped:
            logger.info("Google News intake disabled (cap=0); dropped %d articles", gn_dropped)
        return non_gn

    gn_candidates = [
        a for a in articles
        if _resolve_source_type(a.source_name) == SourceType.GOOGLE_NEWS
    ]
    non_gn = [
        a for a in articles
        if _resolve_source_type(a.source_name) != SourceType.GOOGLE_NEWS
    ]

    if not gn_candidates:
        return non_gn

    # Hard recency filter — ditch anything older than the homepage
    # display window. Google News indexes evergreen content (law-firm
    # explainers, supply-chain whitepapers) for months and they're
    # keyword-dense enough to outscore actual breaking news without
    # this gate.
    today = date.today()
    fresh_candidates = [
        a for a in gn_candidates
        if a.published_date and (today - a.published_date).days <= _GOOGLE_NEWS_MAX_AGE_DAYS
    ]
    stale_dropped = len(gn_candidates) - len(fresh_candidates)

    if not fresh_candidates:
        logger.info(
            "Google News cap pass: 0 candidates within %d-day window "
            "(dropped %d stale)",
            _GOOGLE_NEWS_MAX_AGE_DAYS, stale_dropped,
        )
        return non_gn

    db = SessionLocal()
    try:
        already_today = _count_google_news_persisted_today(db)
        remaining = max(0, cap - already_today)

        if remaining == 0:
            logger.info(
                "Google News daily cap (%d) already reached today (%d persisted) "
                "— dropping %d new candidates",
                cap, already_today, len(fresh_candidates),
            )
            return non_gn

        # Phase A: cross-DB dedup. Drop anything we already have stored.
        # This must happen BEFORE clustering — there's no point spending
        # an LLM call grouping articles that won't be persisted anyway.
        index = _load_recent_headline_index(db)
        new_candidates: list[ScrapedArticle] = []
        rejected_dup = 0
        for cand in fresh_candidates:
            match = _match_existing_headline(cand.headline, index)
            if match is not None:
                rejected_dup += 1
                logger.debug(
                    "Google News cap-pass cross-DB dedup: skipping %r ~ %r",
                    (cand.headline or "")[:70], match[:70],
                )
                continue
            new_candidates.append(cand)

        if not new_candidates:
            logger.info(
                "Google News cap pass: 0 candidates survived cross-DB dedup "
                "(%d in 7d, %d already in DB, %d stale)",
                len(fresh_candidates), rejected_dup, stale_dropped,
            )
            return non_gn

        # Phase B: LLM clustering for semantic dedup. Token-based dedup
        # only catches lexical near-duplicates; this catches cases like
        # Orinoco Tribune and BBC both covering the same OFAC general
        # license with completely different vocabulary. Falls back to
        # identity clusters on any LLM failure, in which case the
        # in-batch token-jaccard guard below still catches lexical dupes.
        clusters = _llm_cluster_candidates(new_candidates)

        # Phase C: pick best representative from each cluster (highest
        # investor-interest score). When the LLM merges two articles
        # about the same event, this is where we choose which one to
        # surface — typically the tier1 publisher's version.
        cluster_reps: list[tuple[float, ScrapedArticle, int]] = []
        for cluster in clusters:
            cluster_scored = [
                (_investor_interest_score(new_candidates[i]), new_candidates[i])
                for i in cluster
            ]
            cluster_scored.sort(key=lambda t: t[0], reverse=True)
            best_score, best_item = cluster_scored[0]
            cluster_reps.append((best_score, best_item, len(cluster)))

        cluster_reps.sort(key=lambda t: t[0], reverse=True)

        clusters_collapsed = sum(1 for _, _, sz in cluster_reps if sz > 1)
        rejected_clustering = len(new_candidates) - len(cluster_reps)

        # Phase D: take top N representatives subject to publisher
        # diversity + in-batch token-jaccard guard (defence-in-depth
        # for under-merging by the LLM).
        accepted: list[ScrapedArticle] = []
        accepted_token_sets: list[frozenset[str]] = []
        rejected_publisher = 0
        rejected_diversity = 0
        per_publisher_count: dict[str, int] = {}

        for score, cand, _cluster_size in cluster_reps:
            if len(accepted) >= remaining:
                break

            meta = cand.extra_metadata or {}
            publisher_key = (
                meta.get("publisher")
                or meta.get("publisher_domain")
                or ""
            ).strip().lower() or "(unknown)"
            if per_publisher_count.get(publisher_key, 0) >= _GOOGLE_NEWS_MAX_PER_PUBLISHER:
                rejected_publisher += 1
                logger.debug(
                    "Google News cap-pass publisher cap: skipping (score=%.1f, "
                    "publisher=%s) %r",
                    score, publisher_key, (cand.headline or "")[:70],
                )
                continue

            cand_tokens = _headline_token_set(_normalize_headline(cand.headline))
            most_similar = 0.0
            most_similar_picked = ""
            for picked, picked_tokens in zip(accepted, accepted_token_sets):
                sim = _jaccard(cand_tokens, picked_tokens)
                if sim > most_similar:
                    most_similar = sim
                    most_similar_picked = picked.headline or ""
            if most_similar >= _GOOGLE_NEWS_IN_BATCH_DIVERSITY_THRESHOLD:
                rejected_diversity += 1
                logger.debug(
                    "Google News cap-pass token diversity: skipping "
                    "(score=%.1f, sim=%.2f to %r) %r",
                    score, most_similar, most_similar_picked[:60],
                    (cand.headline or "")[:60],
                )
                continue

            accepted.append(cand)
            accepted_token_sets.append(cand_tokens)
            per_publisher_count[publisher_key] = per_publisher_count.get(publisher_key, 0) + 1
            _index_headline(cand.headline, index)

        over_cap = max(
            0,
            len(cluster_reps) - len(accepted) - rejected_publisher - rejected_diversity,
        )
        logger.info(
            "Google News cap pass: %d in 7d / %d total -> %d accepted "
            "(cap=%d, already_today=%d, dropped_stale=%d, dropped_db_dup=%d, "
            "merged_by_llm=%d (%d clusters collapsed), dropped_pub_limit=%d, "
            "dropped_token_diversity=%d, dropped_over_cap=%d)",
            len(fresh_candidates), len(gn_candidates), len(accepted), cap,
            already_today, stale_dropped, rejected_dup,
            rejected_clustering, clusters_collapsed,
            rejected_publisher, rejected_diversity, over_cap,
        )
        for score, cand, cluster_size in cluster_reps[:len(accepted) + 5]:
            picked = "*" if cand in accepted else " "
            pub = (cand.extra_metadata or {}).get("publisher", "?")
            cluster_marker = f"x{cluster_size}" if cluster_size > 1 else "  "
            logger.info(
                "  %s %s score=%5.2f %s [%s] %s -- %s",
                picked, cluster_marker, score, cand.published_date,
                cand.source_credibility, (cand.headline or "")[:70], pub[:30],
            )

        return non_gn + accepted
    finally:
        db.close()


def _resolve_source_type(source_name: str) -> SourceType:
    """Map a source name string to a SourceType enum value."""
    name_lower = (source_name or "").lower()
    mapping = {
        "federal register": SourceType.FEDERAL_REGISTER,
        "ofac": SourceType.OFAC_SDN,
        "ofac sdn": SourceType.OFAC_SDN,
        "gdelt": SourceType.GDELT,
        "google news": SourceType.GOOGLE_NEWS,
        "ansa latina": SourceType.ANSA_LATINA,
        "ansalatina": SourceType.ANSA_LATINA,
        "banco central": SourceType.BCV_RATES,
        "bcv": SourceType.BCV_RATES,
        "international trade administration": SourceType.ITA_TRADE,
        "ita": SourceType.ITA_TRADE,
        "trade.gov": SourceType.ITA_TRADE,
        "venezuela bond market": SourceType.VENEZUELA_BONDS,
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
