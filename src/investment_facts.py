"""Structured fact refresh and rendering helpers for investment pages."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Iterable

from src.data.investment_facts import INVESTMENT_FACTS
from src.models import (
    CredibilityTier,
    ExternalArticleEntry,
    GazetteStatus,
    InvestmentFact,
    SessionLocal,
    SourceType,
    init_db,
)

logger = logging.getLogger(__name__)


TIER1_PUBLISHERS = (
    "reuters",
    "bloomberg",
    "financial times",
    "wall street journal",
    "sec",
    "ofac",
    "federal register",
    "chevron",
)


def seed_investment_facts(db=None) -> int:
    """Insert registry defaults if missing. Existing rows are not downgraded."""
    owns_db = db is None
    if db is None:
        init_db()
        db = SessionLocal()
    seeded = 0
    try:
        existing = {row.fact_key for row in db.query(InvestmentFact.fact_key).all()}
        for fact_key, spec in INVESTMENT_FACTS.items():
            if fact_key in existing:
                continue
            fact = InvestmentFact(
                fact_key=fact_key,
                category=spec["category"],
                value_json=spec.get("value_json"),
                display_text=spec["display_text"],
                source_url=spec.get("source_url"),
                source_name=spec.get("source_name"),
                source_date=_parse_date(spec.get("source_date")),
                confidence=float(spec.get("confidence", 0.5)),
                status=spec.get("status", "seeded"),
                affected_pages=spec.get("affected_pages", []),
                notes=f"freshness_days={spec.get('freshness_days')}",
            )
            db.add(fact)
            seeded += 1
        if owns_db:
            db.commit()
        return seeded
    finally:
        if owns_db:
            db.close()


def load_investment_fact_map(*, db=None) -> dict[str, dict]:
    """Return fact_key -> renderable fact dict, falling back to registry defaults."""
    owns_db = db is None
    if db is None:
        try:
            init_db()
            db = SessionLocal()
        except Exception as exc:
            logger.warning("Investment facts DB unavailable; using registry defaults: %s", exc)
            return {k: _spec_to_renderable(k, v) for k, v in INVESTMENT_FACTS.items()}
    try:
        try:
            seed_investment_facts(db)
            rows = db.query(InvestmentFact).all()
            out = {row.fact_key: _row_to_renderable(row) for row in rows}
        except Exception as exc:
            logger.warning("Could not load investment facts; using defaults: %s", exc)
            out = {}
        for key, spec in INVESTMENT_FACTS.items():
            out.setdefault(key, _spec_to_renderable(key, spec))
        return out
    finally:
        if owns_db:
            db.close()


def refresh_investment_facts(target_date: date | None = None) -> dict:
    """Refresh structured facts from daily scraped source rows."""
    target_date = target_date or date.today()
    init_db()
    db = SessionLocal()
    try:
        seeded = seed_investment_facts(db)
        cutoff = target_date - timedelta(days=14)
        rows = (
            db.query(ExternalArticleEntry)
            .filter(ExternalArticleEntry.published_date >= cutoff)
            .filter(
                ExternalArticleEntry.source.in_(
                    [
                        SourceType.VENEZUELA_BONDS,
                        SourceType.INVESTMENT_FACTS,
                        SourceType.FEDERAL_REGISTER,
                        SourceType.GOOGLE_NEWS,
                    ]
                )
            )
            .order_by(ExternalArticleEntry.published_date.desc(), ExternalArticleEntry.id.desc())
            .all()
        )
        facts = {row.fact_key: row for row in db.query(InvestmentFact).all()}
        updates = []

        for row in rows:
            for candidate in _extract_candidates(row):
                fact = facts.get(candidate["fact_key"])
                if fact is None:
                    continue
                decision = _can_publish(row, candidate, fact)
                if decision == "publish":
                    _apply_candidate(fact, candidate, row)
                    updates.append(candidate["fact_key"])
                elif decision == "conflict":
                    fact.status = "needs_review"
                    fact.last_checked_at = datetime.utcnow()
                    fact.notes = _append_note(fact.notes, f"conflict from {row.source_url}")

        db.commit()
        return {
            "seeded": seeded,
            "sources_scanned": len(rows),
            "updated": len(set(updates)),
            "updated_fact_keys": sorted(set(updates)),
        }
    finally:
        db.close()


def fact_source_line(fact: dict | None) -> str:
    if not fact:
        return ""
    source = fact.get("source_name") or "source"
    as_of = fact.get("source_date") or fact.get("last_checked_date")
    if as_of:
        return f"{source}, as of {as_of}"
    return source


def _extract_candidates(row: ExternalArticleEntry) -> list[dict]:
    meta = row.extra_metadata or {}
    out: list[dict] = []

    if row.source == SourceType.VENEZUELA_BONDS and meta.get("kind") == "market_snapshot":
        for instrument in meta.get("instruments") or []:
            cents = instrument.get("price_reference_cents")
            if cents is None:
                continue
            key = _bond_fact_key(instrument.get("short_name") or "")
            if key:
                out.append(
                    {
                        "fact_key": key,
                        "category": "bond_price",
                        "display_text": f"{float(cents):.2f} cents",
                        "value_json": {
                            "cents": float(cents),
                            "instrument": instrument.get("short_name") or instrument.get("name"),
                        },
                        "source_url": instrument.get("price_reference_url") or row.source_url,
                        "source_name": instrument.get("price_reference_label") or row.source_name,
                        "source_date": _parse_date(instrument.get("price_reference_date")) or row.published_date,
                        "confidence": 0.82,
                    }
                )
        return out

    text = " ".join([row.headline or "", row.body_text or "", str(meta.get("snippet") or "")])
    lowered = text.lower()
    topic = (meta.get("topic") or "").lower()

    if "etf" in topic or "venezuela exposure etf" in lowered or "teucrium" in lowered:
        status = _extract_etf_status(lowered)
        if status:
            out.append(
                {
                    "fact_key": "venez_etf_status",
                    "category": "etf_status",
                    "display_text": status[0],
                    "value_json": {"status": status[1]},
                    "source_url": row.source_url,
                    "source_name": _source_label(row),
                    "source_date": row.published_date,
                    "confidence": _source_confidence(row),
                }
            )

    if row.source == SourceType.FEDERAL_REGISTER or "ofac" in topic or "general license" in lowered:
        if "general license 46" in lowered or "gl 46" in lowered:
            display = "authorizes certain Venezuela oil transactions"
            status = "active_scope_limited"
            if "revok" in lowered or "terminat" in lowered:
                display, status = "revoked or terminated", "revoked"
            elif "amend" in lowered:
                display, status = "amended by OFAC", "amended"
            out.append(
                {
                    "fact_key": "ofac_gl46_status",
                    "category": "legal",
                    "display_text": display,
                    "value_json": {"license": "GL 46", "status": status},
                    "source_url": row.source_url,
                    "source_name": _source_label(row),
                    "source_date": row.published_date,
                    "confidence": 0.9 if row.source in (SourceType.FEDERAL_REGISTER, SourceType.OFAC_SDN) else _source_confidence(row),
                }
            )

    bpd_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:million|m)\s*bpd", lowered)
    if ("oil_production" in topic or "oil production" in lowered or "exports" in lowered) and bpd_match:
        value = float(bpd_match.group(1))
        fact_key = "venezuela_oil_exports_current" if "export" in lowered else "venezuela_oil_production_current"
        out.append(
            {
                "fact_key": fact_key,
                "category": "oil",
                "display_text": f"{value:g} million bpd",
                "value_json": {"bpd": int(value * 1_000_000)},
                "source_url": row.source_url,
                "source_name": _source_label(row),
                "source_date": row.published_date,
                "confidence": _source_confidence(row),
            }
        )

    return out


def _can_publish(row: ExternalArticleEntry, candidate: dict, fact: InvestmentFact) -> str:
    confidence = float(candidate.get("confidence", 0.0))
    if row.source in (SourceType.FEDERAL_REGISTER, SourceType.VENEZUELA_BONDS):
        return "publish"
    if confidence < 0.75:
        return "skip"
    existing = fact.value_json or {}
    new_value = candidate.get("value_json") or {}
    if existing and _material_conflict(existing, new_value, fact.category):
        return "conflict"
    return "publish"


def _apply_candidate(fact: InvestmentFact, candidate: dict, row: ExternalArticleEntry) -> None:
    fact.previous_value_json = fact.value_json
    fact.category = candidate.get("category") or fact.category
    fact.value_json = candidate.get("value_json")
    fact.display_text = candidate["display_text"]
    fact.source_url = candidate.get("source_url") or row.source_url
    fact.source_name = candidate.get("source_name") or _source_label(row)
    fact.source_date = candidate.get("source_date") or row.published_date
    fact.last_checked_at = datetime.utcnow()
    fact.confidence = float(candidate.get("confidence", fact.confidence or 0.5))
    fact.status = "current"


def _material_conflict(existing: dict, new_value: dict, category: str) -> bool:
    if category in {"bond_price", "oil", "oil_company"}:
        old_num = existing.get("cents") or existing.get("bpd") or existing.get("usd_bn")
        new_num = new_value.get("cents") or new_value.get("bpd") or new_value.get("usd_bn")
        if old_num and new_num:
            old_num = float(old_num)
            new_num = float(new_num)
            return abs(new_num - old_num) / max(abs(old_num), 1.0) > 0.25
    return False


def _bond_fact_key(short_name: str) -> str | None:
    normalized = short_name.lower()
    if "venz" in normalized and "2031" in normalized:
        return "bond_price_venz_2031_reference"
    if "pdvsa" in normalized and "2027" in normalized:
        return "bond_price_pdvsa_2027_reference"
    return None


def _extract_etf_status(text: str) -> tuple[str, str] | None:
    if any(term in text for term in ("approved", "effective", "begins trading", "listed")):
        return ("approved or effective", "approved")
    if any(term in text for term in ("withdrawn", "withdrawal")):
        return ("withdrawn", "withdrawn")
    if any(term in text for term in ("rejected", "denied", "disapproved")):
        return ("rejected by SEC", "rejected")
    if any(term in text for term in ("delayed", "extended review", "extension")):
        return ("SEC review extended", "review_extended")
    if any(term in text for term in ("under review", "filed", "filing", "application")):
        return ("under SEC review", "under_sec_review")
    return None


def _source_confidence(row: ExternalArticleEntry) -> float:
    label = _source_label(row).lower()
    if row.credibility == CredibilityTier.OFFICIAL or any(pub in label for pub in ("sec", "ofac", "federal register")):
        return 0.9
    if row.credibility == CredibilityTier.TIER1 or any(pub in label for pub in TIER1_PUBLISHERS):
        return 0.8
    return 0.62


def _source_label(row: ExternalArticleEntry) -> str:
    meta = row.extra_metadata or {}
    return meta.get("publisher") or row.source_name or row.source.value


def _row_to_renderable(row: InvestmentFact) -> dict:
    return {
        "fact_key": row.fact_key,
        "category": row.category,
        "value_json": row.value_json or {},
        "display_text": row.display_text,
        "source_url": row.source_url,
        "source_name": row.source_name,
        "source_date": row.source_date.isoformat() if row.source_date else None,
        "last_checked_date": row.last_checked_at.date().isoformat() if row.last_checked_at else None,
        "confidence": row.confidence,
        "status": row.status,
        "is_stale": _is_stale(row.fact_key, row.source_date, row.last_checked_at),
    }


def _spec_to_renderable(fact_key: str, spec: dict) -> dict:
    source_date = spec.get("source_date")
    return {
        "fact_key": fact_key,
        "category": spec["category"],
        "value_json": spec.get("value_json") or {},
        "display_text": spec["display_text"],
        "source_url": spec.get("source_url"),
        "source_name": spec.get("source_name"),
        "source_date": source_date,
        "last_checked_date": source_date,
        "confidence": spec.get("confidence", 0.5),
        "status": spec.get("status", "seeded"),
        "is_stale": _is_stale(fact_key, _parse_date(source_date), None),
    }


def _is_stale(fact_key: str, source_date: date | None, checked_at: datetime | None) -> bool:
    spec = INVESTMENT_FACTS.get(fact_key) or {}
    freshness_days = int(spec.get("freshness_days") or 30)
    anchor = source_date or (checked_at.date() if checked_at else None)
    if anchor is None:
        return True
    return (date.today() - anchor).days > freshness_days


def _parse_date(value) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _append_note(existing: str | None, addition: str) -> str:
    if not existing:
        return addition
    if addition in existing:
        return existing
    return f"{existing}; {addition}"
