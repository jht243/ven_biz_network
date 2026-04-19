"""
Public-company → Venezuela-exposure assessment engine.

Powers two surfaces:
  1.  Static-style landing pages at /companies/<slug>/venezuela-exposure
      (one per S&P 500 ticker — long-tail SEO bet).
  2.  An interactive lookup tool at
      /tools/public-company-venezuela-exposure-check that resolves a
      free-text query to one of those pages and surfaces a preview.

For each company we synthesize an `ExposureReport` from four signals:

  - **Curated overlay** (`src/data/curated_venezuela_exposure.py`) —
    hand-maintained ground truth for the ~30 companies with non-trivial
    exposure. When a curated row exists, it dominates the headline
    classification and is the source of the analyst-style summary.

  - **OFAC SDN matches** — fuzzy match the company's short name AND
    every curated subsidiary string against the live OFAC SDN list (via
    `src/data/sdn_profiles.py`). A match here is the strongest possible
    hit — it means the entity itself, or one of its subsidiaries, is
    Treasury-blocked.

  - **Internal corpus mentions** — Federal Register notices and analyzed
    news articles in our own DB that namedrop the company alongside
    Venezuela-relevant context. Cheap and high-precision; we already
    rank these for relevance.

  - **EDGAR full-text mentions** — recent SEC filings (10-K, 10-Q, 8-K,
    20-F, 6-K) where the company itself disclosed a Venezuela / PdVSA /
    CITGO mention. This is the killer signal for "did the company tell
    its shareholders it has exposure?". Cached to disk for 30 days
    because EDGAR is rate-limited and we don't want a cold page render
    to round-trip to sec.gov.

The final classification is one of:
    "direct"      — operating presence, JVs, services, current OFAC license
    "indirect"    — subsidiary, bottler, distributor, equity stake
    "historical"  — prior writedown / divestiture / expropriation
    "none"        — confirmed no exposure (curated)
    "unknown"     — no signals (most S&P 500 will land here)

We deliberately surface "unknown" as a positive answer on the page —
"no exposure on the public record" is exactly what most analysts want.

Caching:
  - The fully-built ExposureReport is cached in-process for 1 hour
    (curated + SDN data don't change request-to-request).
  - EDGAR responses are persisted to `storage/edgar_cache/{cik}.json`
    for 30 days so the first cold render is the only one that pays the
    network cost.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from src.config import settings
from src.data.curated_venezuela_exposure import CuratedExposure, get_curated, known_subsidiary_terms
from src.data.sp500_companies import SP500Company, find_company, list_sp500_companies, slugify_company

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SDNMatch:
    """One OFAC SDN entry that matched the company or one of its
    subsidiary terms. Light wrapper so the template doesn't have to
    know about SDNProfile."""
    name: str
    bucket: str
    program: str
    profile_url: str  # /sanctions/<bucket>/<slug>
    matched_term: str  # what string we matched on
    score: int  # 0-100


@dataclass(frozen=True)
class CorpusMention:
    """A namedrop of the company in our analyzed-article corpus."""
    headline: str
    source: str
    url: Optional[str]
    date: Optional[str]
    snippet: str = ""


@dataclass(frozen=True)
class EdgarMention:
    """A Venezuela-keyword hit in one of the company's SEC filings."""
    form: str
    filed: str
    accession_no: str
    snippet: str
    url: str


@dataclass
class ExposureReport:
    company: SP500Company
    classification: str  # see module docstring
    headline: str  # short SERP-grade summary
    summary: str  # 1-3 sentence analyst note
    curated: Optional[CuratedExposure]
    sdn_matches: list[SDNMatch] = field(default_factory=list)
    corpus_mentions: list[CorpusMention] = field(default_factory=list)
    edgar_mentions: list[EdgarMention] = field(default_factory=list)
    edgar_attempted: bool = False
    edgar_total_hits: int = 0
    generated_at: str = ""

    @property
    def has_any_signal(self) -> bool:
        return bool(
            self.curated
            or self.sdn_matches
            or self.corpus_mentions
            or self.edgar_mentions
        )

    @property
    def is_definitive_no(self) -> bool:
        """True only when curated says explicitly 'none' AND no other
        signal contradicts it. Anything else stays in 'unknown' / a
        positive level."""
        return (
            self.curated is not None
            and self.curated.exposure_level == "none"
            and not self.sdn_matches
            and not self.edgar_mentions
        )


# ──────────────────────────────────────────────────────────────────────
# Slug helpers (re-exported so the route layer stays decoupled)
# ──────────────────────────────────────────────────────────────────────


def find_company_by_slug(slug: str) -> Optional[SP500Company]:
    """Resolve a /companies/<slug> URL slug to its SP500Company row."""
    if not slug:
        return None
    s = slug.strip().lower()
    for c in list_sp500_companies():
        if c.slug == s:
            return c
    # Tolerate visitors landing on slug variants (no trailing -ticker,
    # ticker-only, etc.) by routing through find_company.
    return find_company(s)


# ──────────────────────────────────────────────────────────────────────
# OFAC SDN fuzzy match
# ──────────────────────────────────────────────────────────────────────


_NON_ALPHA = re.compile(r"[^a-z0-9]+")


def _normalize(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return _NON_ALPHA.sub(" ", s.lower()).strip()


# Single-word strings that, on their own, would generate false positives
# against OFAC names from any sector (Venezuelan towns, common surnames,
# generic corporate words). When a company's short name reduces to one
# of these, we skip the OFAC pass to avoid garbage matches.
_SDN_BLOCK_TERMS: frozenset[str] = frozenset({
    "general", "international", "company", "corp", "venezuela",
    "national", "global", "industries", "americas", "petroleum",
    "energy", "bank", "capital", "communications", "service",
    "services", "group", "holdings", "trust", "first", "united",
    "american", "western", "eastern", "pacific", "atlantic", "south",
    "north",
})


def _scan_sdn_for_term(term: str, *, source_label: str, min_chars: int = 4) -> list[SDNMatch]:
    """Substring-scan all SDN profiles for `term`. Used per company name
    AND per curated-subsidiary string."""
    norm = _normalize(term)
    if not norm or len(norm) < min_chars:
        return []
    if norm in _SDN_BLOCK_TERMS:
        return []

    # Only import inside the function: sdn_profiles touches the DB on
    # first use, and the company-list rendering paths can run before
    # init_db() in some scripts.
    try:
        from src.data.sdn_profiles import list_all_profiles
    except Exception as exc:
        logger.warning("sdn_profiles unavailable for exposure scan: %s", exc)
        return []

    out: list[SDNMatch] = []
    try:
        profiles = list_all_profiles()
    except Exception as exc:
        logger.warning("SDN profile load failed: %s", exc)
        return []

    for p in profiles:
        haystack = _normalize(p.raw_name) + " " + _normalize(p.raw_remarks)
        if norm in haystack:
            # Score: full token-boundary match scores higher than mid-token.
            boundary = re.search(rf"\b{re.escape(norm)}\b", haystack)
            score = 95 if boundary else 78
            out.append(SDNMatch(
                name=p.display_name,
                bucket=p.bucket,
                program=p.program or "Venezuela-related",
                profile_url=p.url_path,
                matched_term=source_label,
                score=score,
            ))
    return out


def _collect_sdn_matches(company: SP500Company, curated: Optional[CuratedExposure]) -> list[SDNMatch]:
    """Run the SDN scan against the company's short name + every curated
    subsidiary term. Dedupe by profile URL, keep the highest score."""
    candidates: dict[str, SDNMatch] = {}

    # 1. The company's short name itself.
    for m in _scan_sdn_for_term(company.short_name, source_label=company.short_name, min_chars=5):
        existing = candidates.get(m.profile_url)
        if existing is None or m.score > existing.score:
            candidates[m.profile_url] = m

    # 2. Every curated subsidiary string.
    for term in known_subsidiary_terms(company.ticker):
        for m in _scan_sdn_for_term(term, source_label=term):
            existing = candidates.get(m.profile_url)
            if existing is None or m.score > existing.score:
                candidates[m.profile_url] = m

    # Stable ordering: highest score first, then by name.
    return sorted(candidates.values(), key=lambda x: (-x.score, x.name))


# ──────────────────────────────────────────────────────────────────────
# Internal corpus mentions (Federal Register + analyzed news)
# ──────────────────────────────────────────────────────────────────────


def _collect_corpus_mentions(company: SP500Company, *, limit: int = 6) -> list[CorpusMention]:
    """Search our analyzed-article DB for the company's short name."""
    try:
        from sqlalchemy import or_, func as _func
        from src.models import (
            AssemblyNewsEntry, ExternalArticleEntry, GazetteStatus,
            SessionLocal, init_db,
        )
    except Exception as exc:
        logger.warning("models import failed for corpus scan: %s", exc)
        return []

    needles = [n for n in {company.short_name, company.name} if n and len(n) >= 4]
    if not needles:
        return []

    init_db()
    db = SessionLocal()
    try:
        results: list[CorpusMention] = []
        for model in (ExternalArticleEntry, AssemblyNewsEntry):
            ors = []
            for n in needles:
                ors.append(_func.lower(model.headline).contains(n.lower()))
                ors.append(_func.lower(model.body_text).contains(n.lower()))
            try:
                rows = (
                    db.query(model)
                    .filter(or_(*ors))
                    .filter(model.status == GazetteStatus.ANALYZED)
                    .order_by(model.published_date.desc())
                    .limit(limit)
                    .all()
                )
            except Exception as exc:
                logger.warning("corpus scan query failed for %s: %s", model.__name__, exc)
                continue

            for row in rows:
                analysis = row.analysis_json or {}
                headline = analysis.get("headline_short") or row.headline
                snippet = (analysis.get("summary") or "")[:240]
                source = getattr(row, "source_name", None) or model.__name__
                if hasattr(row, "source") and row.source:
                    source = str(row.source).split(".")[-1].replace("_", " ").title()
                results.append(CorpusMention(
                    headline=headline,
                    source=source,
                    url=getattr(row, "source_url", None),
                    date=row.published_date.isoformat() if row.published_date else None,
                    snippet=snippet,
                ))

        seen: set[str] = set()
        uniq: list[CorpusMention] = []
        for r in sorted(results, key=lambda x: x.date or "", reverse=True):
            key = (r.url or r.headline)[:200]
            if key in seen:
                continue
            seen.add(key)
            uniq.append(r)
            if len(uniq) >= limit:
                break
        return uniq
    finally:
        db.close()


# ──────────────────────────────────────────────────────────────────────
# EDGAR full-text mentions (cached to disk)
# ──────────────────────────────────────────────────────────────────────


_EDGAR_CACHE_DIR = settings.storage_dir / "edgar_cache"
_EDGAR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_EDGAR_TTL_DAYS = 30


def _edgar_cache_path(cik: str) -> Path:
    safe = re.sub(r"[^0-9A-Za-z]", "", cik or "noecik")
    return _EDGAR_CACHE_DIR / f"{safe or 'noecik'}.json"


def _read_edgar_cache(cik: str) -> Optional[list[EdgarMention]]:
    path = _edgar_cache_path(cik)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    cached_at = payload.get("cached_at", 0)
    if time.time() - cached_at > _EDGAR_TTL_DAYS * 86400:
        return None
    return [EdgarMention(**m) for m in payload.get("hits", [])]


def _write_edgar_cache(cik: str, hits: list[EdgarMention], total: int) -> None:
    path = _edgar_cache_path(cik)
    try:
        path.write_text(
            json.dumps({
                "cached_at": time.time(),
                "total": total,
                "hits": [asdict(h) for h in hits],
            }),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("EDGAR cache write failed for %s: %s", cik, exc)


def _fetch_edgar_mentions(company: SP500Company, *, network: bool, limit: int = 6) -> tuple[list[EdgarMention], int, bool]:
    """Return (hits, total_hits, attempted). When `network` is False we
    only consult the on-disk cache — useful for the sitemap pre-warm
    path or for cheap homepage rendering."""
    cik = company.cik or ""
    cached = _read_edgar_cache(cik or company.ticker) if cik else None
    if cached is not None:
        return cached, len(cached), True
    if not network:
        return [], 0, False

    try:
        from src.analysis.edgar_search import search_company_venezuela_filings
    except Exception as exc:
        logger.warning("edgar_search import failed: %s", exc)
        return [], 0, False

    try:
        raw_hits = search_company_venezuela_filings(
            company_name=company.short_name,
            cik=cik or None,
            limit=limit,
        )
    except Exception as exc:
        logger.warning("EDGAR search exception for %s: %s", company.ticker, exc)
        return [], 0, True

    hits = [
        EdgarMention(
            form=h.form,
            filed=h.filed,
            accession_no=h.accession_no,
            snippet=h.snippet,
            url=h.url,
        )
        for h in raw_hits
    ]
    if cik:
        _write_edgar_cache(cik or company.ticker, hits, len(hits))
    return hits, len(hits), True


# ──────────────────────────────────────────────────────────────────────
# Classification + headline synthesis
# ──────────────────────────────────────────────────────────────────────


def _classify(
    company: SP500Company,
    curated: Optional[CuratedExposure],
    sdn_matches: list[SDNMatch],
    corpus_mentions: list[CorpusMention],
    edgar_mentions: list[EdgarMention],
) -> tuple[str, str, str]:
    """Return (classification, headline, summary)."""
    name = company.short_name or company.name

    # Curated rows are ground truth — but we still upgrade them when an
    # SDN hit on a subsidiary appears that the curated row didn't know
    # about (e.g. a brand-new sanction).
    if curated:
        level = curated.exposure_level
        if level == "none" and sdn_matches:
            # New SDN match against a subsidiary post-curation: don't
            # contradict the analyst note, but flag it.
            level = "indirect"
        headline = _curated_headline(name, level)
        summary = curated.summary
        if level != curated.exposure_level and sdn_matches:
            summary = (
                summary
                + f" (Updated: a new OFAC match for '{sdn_matches[0].matched_term}' "
                "now appears on the SDN list — see the OFAC matches below.)"
            )
        return level, headline, summary

    # No curated entry. Derive a level from algorithmic signals.
    if sdn_matches:
        level = "direct"
        first = sdn_matches[0]
        headline = (
            f"{name} is connected to an OFAC-blocked entity ({first.name}) "
            "on the Venezuela SDN list."
        )
        summary = (
            f"Caracas Research detected {len(sdn_matches)} match"
            f"{'es' if len(sdn_matches) != 1 else ''} between {name} or its "
            "operating subsidiaries and the US Treasury Office of Foreign Assets "
            "Control (OFAC) Specially Designated Nationals (SDN) list under the "
            "Venezuela sanctions program. Match is by name overlap and may be "
            "coincidental; verify before relying on it for compliance decisions."
        )
        return level, headline, summary

    if edgar_mentions:
        # Filing disclosure is medium-strong; default to "indirect"
        # unless they're filing 10-Ks talking about Venezuela operations.
        forms = {m.form for m in edgar_mentions}
        level = "indirect" if forms & {"10-K", "20-F"} else "historical"
        headline = (
            f"{name} has disclosed Venezuela-related items in {len(edgar_mentions)} "
            f"recent SEC filing{'s' if len(edgar_mentions) != 1 else ''}."
        )
        summary = (
            f"{name} has filed {len(edgar_mentions)} recent SEC document"
            f"{'s' if len(edgar_mentions) != 1 else ''} ({', '.join(sorted(forms))}) "
            "containing Venezuela / PdVSA / CITGO references. This is the company's "
            "own disclosure and is the strongest evidence of material exposure short "
            "of an OFAC designation. See the SEC filings section below for excerpts."
        )
        return level, headline, summary

    if corpus_mentions:
        level = "historical"
        headline = (
            f"No active sanctions or filings link {name} to Venezuela today, "
            "but the company appears in our news corpus."
        )
        summary = (
            f"Caracas Research has indexed {len(corpus_mentions)} analyzed news "
            "article" + ("s" if len(corpus_mentions) != 1 else "") +
            f" or Federal Register notice mentioning {name} alongside Venezuelan context. "
            "Most S&P 500 companies in this bucket have only incidental exposure; review "
            "the citations below to judge materiality."
        )
        return level, headline, summary

    # No signals at all — the most common, and the answer the analyst
    # actually wants. We frame it as a positive "no exposure on the
    # public record" so the page still answers the search query.
    level = "unknown"
    headline = f"{name} has no Venezuela exposure on the public record."
    summary = (
        f"As of this scan, {name} (NYSE/NASDAQ: {company.ticker}) has no entries "
        "on the OFAC Venezuela SDN list, no Venezuela-related disclosures we have "
        "indexed in its recent SEC filings, and no Caracas-corpus news mentions. "
        "This is consistent with no operational, financial, or compliance exposure "
        "to Venezuela. Always re-verify against primary sources before relying on "
        "this as a compliance answer."
    )
    return level, headline, summary


def _curated_headline(name: str, level: str) -> str:
    table = {
        "direct":     f"{name} has direct Venezuela exposure documented on the public record.",
        "indirect":   f"{name} has indirect Venezuela exposure via subsidiaries or counterparties.",
        "historical": f"{name} has historical Venezuela exposure that has been wound down or written off.",
        "none":       f"{name} has no current Venezuela exposure on the public record.",
    }
    return table.get(level, f"{name} appears on the Caracas Research Venezuela exposure register.")


# ──────────────────────────────────────────────────────────────────────
# Orchestrator + cache
# ──────────────────────────────────────────────────────────────────────


_REPORT_CACHE: dict[str, tuple[float, ExposureReport]] = {}
_REPORT_CACHE_LOCK = threading.Lock()
_REPORT_TTL_SECONDS = 3600  # 1 hour — SDN + curated change slowly


def build_exposure_report(
    company: SP500Company,
    *,
    use_edgar: bool = True,
    network: bool = True,
) -> ExposureReport:
    """Synthesize an ExposureReport for one company. Cached for 1h."""
    cache_key = f"{company.ticker}:{int(use_edgar)}:{int(network)}"
    now = time.time()
    with _REPORT_CACHE_LOCK:
        cached = _REPORT_CACHE.get(cache_key)
        if cached and (now - cached[0]) < _REPORT_TTL_SECONDS:
            return cached[1]

    curated = get_curated(company.ticker)
    sdn_matches = _collect_sdn_matches(company, curated)
    corpus_mentions = _collect_corpus_mentions(company)
    edgar_mentions: list[EdgarMention] = []
    edgar_total = 0
    edgar_attempted = False
    if use_edgar:
        edgar_mentions, edgar_total, edgar_attempted = _fetch_edgar_mentions(
            company, network=network
        )

    classification, headline, summary = _classify(
        company, curated, sdn_matches, corpus_mentions, edgar_mentions
    )

    report = ExposureReport(
        company=company,
        classification=classification,
        headline=headline,
        summary=summary,
        curated=curated,
        sdn_matches=sdn_matches,
        corpus_mentions=corpus_mentions,
        edgar_mentions=edgar_mentions,
        edgar_attempted=edgar_attempted,
        edgar_total_hits=edgar_total,
        generated_at=datetime.utcnow().isoformat(timespec="seconds") + "Z",
    )
    with _REPORT_CACHE_LOCK:
        _REPORT_CACHE[cache_key] = (now, report)
    return report


# ──────────────────────────────────────────────────────────────────────
# Aggregate helpers (used by the /companies index + sitemap)
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CompanyIndexRow:
    """Lightweight record for the A-Z directory page. Avoids triggering
    the EDGAR network path for 500 companies on a single render."""
    ticker: str
    name: str
    short_name: str
    sector: str
    slug: str
    classification: str  # "curated" if curated_level present, else "unknown"
    curated_level: Optional[str]
    sdn_match_count: int
    has_curated: bool

    @property
    def url_path(self) -> str:
        return f"/companies/{self.slug}/venezuela-exposure"


def list_company_index_rows(*, include_sdn_scan: bool = True) -> list[CompanyIndexRow]:
    """Cheap one-row-per-company summary for the index page. We only
    run the SDN scan when explicitly asked, and never EDGAR — those
    are reserved for the per-page render."""
    rows: list[CompanyIndexRow] = []
    for c in list_sp500_companies():
        curated = get_curated(c.ticker)
        sdn_count = 0
        if include_sdn_scan:
            try:
                sdn_count = len(_collect_sdn_matches(c, curated))
            except Exception:
                sdn_count = 0
        rows.append(CompanyIndexRow(
            ticker=c.ticker,
            name=c.name,
            short_name=c.short_name,
            sector=c.sector,
            slug=c.slug,
            classification=(curated.exposure_level if curated else "unknown"),
            curated_level=(curated.exposure_level if curated else None),
            sdn_match_count=sdn_count,
            has_curated=bool(curated),
        ))
    rows.sort(key=lambda r: r.name.lower())
    return rows


def companies_for_sitemap() -> list[dict]:
    """One sitemap row per S&P 500 company.

    We deliberately enumerate every company even when the report would
    say "no exposure" — those pages are the long-tail bet (option 2b).
    """
    out: list[dict] = []
    for c in list_sp500_companies():
        out.append({
            "ticker": c.ticker,
            "slug": c.slug,
            "url_path": f"/companies/{c.slug}/venezuela-exposure",
        })
    return out


# Re-export so callers don't have to know which module slugs live in.
__all__ = [
    "ExposureReport",
    "SDNMatch",
    "CorpusMention",
    "EdgarMention",
    "CompanyIndexRow",
    "build_exposure_report",
    "find_company_by_slug",
    "list_company_index_rows",
    "companies_for_sitemap",
    "slugify_company",
]
