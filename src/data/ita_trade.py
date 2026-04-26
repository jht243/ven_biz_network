"""Helpers for ITA Venezuela trade-lead and market-entry pages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from src.models import ExternalArticleEntry, SessionLocal, SourceType, init_db

TRADE_LEADS_URL = "https://www.trade.gov/venezuela-trade-leads"
VENEZUELA_HUB_URL = "https://www.trade.gov/venezuela"
CONTACT_EMAIL = "tradevenezuela@trade.gov"


@dataclass(frozen=True)
class ITATradeLead:
    sector: str
    equipment: str
    units_requested: int | None
    hs_code: str
    hs_description: str
    source_url: str = TRADE_LEADS_URL


@dataclass(frozen=True)
class ITAResource:
    title: str
    url: str
    article_type: str
    summary: str
    published_date: date | None


_FALLBACK_LEADS = [
    ITATradeLead("Health Care", "Neonatal Volume Mechanical Ventilator", 159, "9019.20.10", "Mechanical ventilation apparatus capable of providing invasive ventilation"),
    ITATradeLead("Health Care", "Pediatric/Adult Volume Mechanical Ventilator", 200, "9019.20.10", "Mechanical ventilation apparatus capable of providing invasive ventilation"),
    ITATradeLead("Health Care", "Digital Anesthesia Machine", 102, "9018.90.60", "Anesthetic apparatus and instruments"),
    ITATradeLead("Health Care", "Remote-Controlled Radiology Equipment with Digitizer", 40, "9022.14", "Other X-ray apparatus for medical, surgical or veterinary uses"),
    ITATradeLead("Health Care", "Multiparameter Vital Signs Monitor with Capnograph", 813, "9018.19", "Other electro-diagnostic apparatus"),
    ITATradeLead("Health Care", "High-End CT Scanner (128 slices+)", 50, "9022.12", "Computed tomography apparatus"),
    ITATradeLead("Health Care", "Sterilization Equipment for Heat-Sensitive Materials", 10, "8419.2", "Medical, surgical or laboratory sterilizers"),
    ITATradeLead("Health Care", "External Defibrillator with Paddles & Pacemaker Cable", 22, "9018.9", "Other instruments and appliances used in medical or surgical sciences"),
]


def _lead_from_dict(raw: dict[str, Any]) -> ITATradeLead | None:
    equipment = str(raw.get("equipment") or "").strip()
    hs_code = str(raw.get("hs_code") or "").strip()
    if not equipment or not hs_code:
        return None
    units = raw.get("units_requested")
    try:
        units = int(units) if units not in ("", None) else None
    except (TypeError, ValueError):
        units = None
    return ITATradeLead(
        sector=str(raw.get("sector") or "General").strip(),
        equipment=equipment,
        units_requested=units,
        hs_code=hs_code,
        hs_description=str(raw.get("hs_description") or "").strip(),
        source_url=str(raw.get("source_url") or TRADE_LEADS_URL),
    )


def latest_trade_leads() -> tuple[list[ITATradeLead], ExternalArticleEntry | None]:
    """Return parsed ITA trade leads plus the source DB row if available."""
    try:
        init_db()
        db = SessionLocal()
        try:
            row = (
                db.query(ExternalArticleEntry)
                .filter(ExternalArticleEntry.source == SourceType.ITA_TRADE)
                .filter(ExternalArticleEntry.source_url == TRADE_LEADS_URL)
                .order_by(ExternalArticleEntry.created_at.desc())
                .first()
            )
            raw_leads = []
            if row and row.extra_metadata:
                raw_leads = row.extra_metadata.get("trade_leads") or []
            leads = [lead for lead in (_lead_from_dict(r) for r in raw_leads) if lead]
            return (leads or _FALLBACK_LEADS, row)
        finally:
            db.close()
    except Exception:
        return (_FALLBACK_LEADS, None)


def latest_ita_resources(limit: int = 8) -> list[ITAResource]:
    """Return recent ITA Venezuela pages captured by the scraper."""
    try:
        init_db()
        db = SessionLocal()
        try:
            rows = (
                db.query(ExternalArticleEntry)
                .filter(ExternalArticleEntry.source == SourceType.ITA_TRADE)
                .order_by(ExternalArticleEntry.created_at.desc())
                .limit(limit)
                .all()
            )
            resources = []
            for row in rows:
                body = " ".join((row.body_text or "").split())
                body = body.replace(
                    "Office Page Menu (Country) Venezuela Business Information Center "
                    "Frequently Asked Questions Trade Leads Country Contacts ",
                    "",
                )
                resources.append(ITAResource(
                    title=row.headline,
                    url=row.source_url,
                    article_type=row.article_type or "resource",
                    summary=body[:220] + ("..." if len(body) > 220 else ""),
                    published_date=row.published_date,
                ))
            return resources
        finally:
            db.close()
    except Exception:
        return []


def trade_lead_stats(leads: list[ITATradeLead]) -> dict[str, Any]:
    sectors = sorted({l.sector for l in leads if l.sector})
    total_units = sum(l.units_requested or 0 for l in leads)
    hs_codes = sorted({l.hs_code for l in leads if l.hs_code})
    return {
        "lead_count": len(leads),
        "sector_count": len(sectors),
        "sectors": sectors,
        "total_units": total_units,
        "hs_code_count": len(hs_codes),
    }
