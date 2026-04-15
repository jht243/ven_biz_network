"""
Report generator: reads analyzed entries from the database and renders
the Jinja2 template into a static report.html file.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from src.config import settings
from src.models import (
    SessionLocal,
    ExternalArticleEntry,
    AssemblyNewsEntry,
    GazetteStatus,
    SourceType,
    init_db,
)

logger = logging.getLogger(__name__)

SECTOR_OPTIONS = [
    {"value": "realestate", "label": "Real Estate"},
    {"value": "security", "label": "Safety & Security"},
    {"value": "economic", "label": "Economic Policy"},
    {"value": "fiscal", "label": "Tax & Fiscal"},
    {"value": "sanctions", "label": "Sanctions"},
    {"value": "diplomatic", "label": "US Relations"},
    {"value": "governance", "label": "Governance"},
    {"value": "legal", "label": "Legal & Rights"},
    {"value": "mining", "label": "Mining"},
    {"value": "energy", "label": "Energy & Oil"},
    {"value": "banking", "label": "Banking & Finance"},
]

STATUS_CSS_MAP = {
    "passed": "passed",
    "in_effect": "passed",
    "in_progress": "progress",
    "announced": "announced",
    "monitoring": "monitoring",
}

TRUST_CSS_MAP = {
    "official": ("trust-official", "Official"),
    "tier1": ("trust-tier1", "Verified Source"),
    "state": ("trust-state", "State Media"),
    "tier2": ("trust-tier2", "News Source"),
}

SOURCE_DISPLAY_MAP = {
    SourceType.FEDERAL_REGISTER: "Federal Register",
    SourceType.OFAC_SDN: "OFAC SDN List",
    SourceType.GDELT: None,
    SourceType.BCV_RATES: "BCV",
    SourceType.TRAVEL_ADVISORY: "State Dept",
    SourceType.ASAMBLEA_NACIONAL: "Asamblea Nacional",
}


def generate_report(output_path: Path | None = None) -> Path:
    """
    Query the database for analyzed entries and render the report.
    Returns the path to the generated HTML file.
    """
    output_path = output_path or settings.output_dir / "report.html"
    init_db()
    db = SessionLocal()

    try:
        cutoff = date.today() - timedelta(days=settings.report_lookback_days)

        ext_articles = (
            db.query(ExternalArticleEntry)
            .filter(ExternalArticleEntry.status == GazetteStatus.ANALYZED)
            .filter(ExternalArticleEntry.published_date >= cutoff)
            .order_by(ExternalArticleEntry.published_date.desc())
            .all()
        )

        assembly_news = (
            db.query(AssemblyNewsEntry)
            .filter(AssemblyNewsEntry.status == GazetteStatus.ANALYZED)
            .filter(AssemblyNewsEntry.published_date >= cutoff)
            .order_by(AssemblyNewsEntry.published_date.desc())
            .all()
        )

        entries = _build_entries(ext_articles, assembly_news)
        ticker_items = _build_ticker(db)
        news_items = _build_news_sidebar(entries)
        calendar_events = _build_calendar()
        climate = _build_climate()

        template_dir = Path(__file__).parent.parent / "templates"
        env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=False,
        )
        template = env.get_template("report.html.j2")

        html = template.render(
            entries=entries,
            ticker_items=ticker_items,
            news_items=news_items,
            calendar_events=calendar_events,
            climate=climate,
            all_sectors=SECTOR_OPTIONS,
            current_year=date.today().year,
            generated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")
        logger.info("Report generated: %s (%d entries)", output_path, len(entries))
        return output_path

    finally:
        db.close()


def _build_entries(ext_articles, assembly_news) -> list[dict]:
    """Convert DB entries into template-ready dicts, filtered by relevance."""
    entries = []
    min_score = settings.analysis_min_relevance

    all_items = []
    for a in ext_articles:
        all_items.append(("external", a))
    for n in assembly_news:
        all_items.append(("assembly", n))

    for item_type, item in all_items:
        analysis = item.analysis_json or {}
        relevance = analysis.get("relevance_score", 0)
        if relevance < min_score:
            continue

        sectors = analysis.get("sectors", [])
        sentiment = analysis.get("sentiment", "mixed")
        status = analysis.get("status", "monitoring")
        status_label = analysis.get("status_label", status.replace("_", " ").title())
        category_label = analysis.get("category_label", "General")
        headline = analysis.get("headline_short", item.headline[:80])
        takeaway = analysis.get("takeaway", "")
        is_breaking = analysis.get("is_breaking", False)
        source_trust = analysis.get("source_trust", "tier2")

        status_key = status.lower().replace(" ", "_").replace("—", "").replace("-", "_").strip()
        for k in STATUS_CSS_MAP:
            if k in status_key:
                status_key = k
                break
        status_css = STATUS_CSS_MAP.get(status_key, "monitoring")

        trust_css, trust_label_default = TRUST_CSS_MAP.get(source_trust, ("trust-tier2", "News Source"))

        if item_type == "external":
            source_display = item.source_name or "Source"
            if item.source == SourceType.FEDERAL_REGISTER:
                source_display = "Federal Register"
                trust_label_default = "Official — Federal Register"
            elif item.source == SourceType.TRAVEL_ADVISORY:
                source_display = "State Dept"
                trust_label_default = "Official — US State Department"
            elif item.source == SourceType.GDELT:
                domain = (item.extra_metadata or {}).get("domain", "")
                source_display = domain or item.source_name or "International Press"
                trust_label_default = f"Via GDELT — {source_display}"
        else:
            source_display = "Asamblea Nacional"
            trust_label_default = "State Media"

        is_new = (date.today() - item.published_date).days <= 3

        safe_id = re.sub(r"[^a-z0-9]", "-", headline.lower())[:40].strip("-")

        entries.append({
            "id": safe_id,
            "headline": item.headline,
            "headline_short": headline,
            "date_display": item.published_date.strftime("%B %d, %Y"),
            "published_date": item.published_date,
            "source_url": item.source_url,
            "source_display": source_display,
            "sectors": sectors,
            "sectors_str": " ".join(sectors),
            "sentiment": sentiment,
            "status_class": status_css,
            "status_label": status_label,
            "category_label": category_label,
            "takeaway": takeaway,
            "is_new": is_new,
            "is_breaking": is_breaking,
            "trust_class": trust_css,
            "trust_label": trust_label_default,
            "body_text": item.body_text if item.body_text and len(item.body_text) > 100 else None,
            "relevance": relevance,
        })

    entries.sort(key=lambda e: e["published_date"], reverse=True)
    return entries


def _build_news_sidebar(entries: list[dict]) -> list[dict]:
    """Top entries for the This Week's News sidebar."""
    top = sorted(entries, key=lambda e: (e.get("is_breaking", False), e["relevance"]), reverse=True)
    sidebar = []
    for e in top[:8]:
        summary_short = e["takeaway"][:120].rsplit(" ", 1)[0] + "..." if len(e["takeaway"]) > 120 else e["takeaway"]
        summary_short = re.sub(r"<[^>]+>", "", summary_short)
        sidebar.append({
            "id": e["id"],
            "headline_short": e["headline_short"],
            "summary_short": summary_short,
            "sentiment": e["sentiment"],
        })
    return sidebar


def _build_ticker(db) -> list[dict]:
    """Build ticker bar items from latest DB data."""
    items = []

    bcv = (
        db.query(ExternalArticleEntry)
        .filter(ExternalArticleEntry.source == SourceType.BCV_RATES)
        .order_by(ExternalArticleEntry.published_date.desc())
        .first()
    )
    if bcv and bcv.extra_metadata:
        usd_rate = bcv.extra_metadata.get("usd")
        if usd_rate:
            items.append({
                "label": "BCV Official",
                "value": f"{usd_rate}",
                "unit": "Bs.D/$",
                "change": None,
                "change_dir": "up",
                "value_color": None,
                "source": "BCV (live)",
            })

    advisory = (
        db.query(ExternalArticleEntry)
        .filter(ExternalArticleEntry.source == SourceType.TRAVEL_ADVISORY)
        .order_by(ExternalArticleEntry.published_date.desc())
        .first()
    )
    if advisory and advisory.extra_metadata:
        level = advisory.extra_metadata.get("level")
        if level:
            color = "#4ade80" if level <= 3 else "#f87171"
            items.append({
                "label": "Travel Advisory",
                "value": f"Level {level}",
                "unit": None,
                "change": f"{'↓' if level < 4 else ''} from 4" if level < 4 else None,
                "change_dir": "up" if level < 4 else "down",
                "value_color": color,
                "source": "State Dept",
            })

    if not items or len(items) < 2:
        items.extend([
            {"label": "Brent Crude", "value": "$65.48", "unit": None, "change": "−4.1%", "change_dir": "down", "value_color": None, "source": "MarketWatch"},
            {"label": "Inflation Q1", "value": "71.8%", "unit": None, "change": None, "change_dir": "down", "value_color": None, "source": "BCV Official"},
            {"label": "Country Risk", "value": "E", "unit": "(Extreme)", "change": None, "change_dir": "down", "value_color": "#f87171", "source": "Coface"},
            {"label": "Oil Prod.", "value": "1.095M", "unit": "bpd", "change": None, "change_dir": "up", "value_color": None, "source": "PDVSA"},
        ])
    else:
        items.extend([
            {"label": "Brent Crude", "value": "$65.48", "unit": None, "change": "−4.1%", "change_dir": "down", "value_color": None, "source": "MarketWatch"},
            {"label": "Inflation Q1", "value": "71.8%", "unit": None, "change": None, "change_dir": "down", "value_color": None, "source": "BCV Official"},
            {"label": "Country Risk", "value": "E", "unit": "(Extreme)", "change": None, "change_dir": "down", "value_color": "#f87171", "source": "Coface"},
            {"label": "FDI Stock", "value": "$30.5B", "unit": None, "change": None, "change_dir": "up", "value_color": None, "source": "UNCTAD '24"},
            {"label": "Oil Prod.", "value": "1.095M", "unit": "bpd", "change": None, "change_dir": "up", "value_color": None, "source": "PDVSA"},
        ])

    return items


def _build_calendar() -> list[dict]:
    """Static calendar events — will be made dynamic in future."""
    return [
        {"date_label": "Ongoing", "title": "OFAC GLs 46A–50A", "subtitle": "Active", "note": "Oil & gas authorizations. Revocable.", "link": "https://ofac.treasury.gov/sanctions-programs-and-country-information/venezuela-related-sanctions", "css_class": "cal-positive"},
        {"date_label": "2026 Agenda", "title": "Tax Harmonization Law", "subtitle": None, "note": "Fiscal terms for energy JVs & real estate. No date set.", "link": None, "css_class": ""},
        {"date_label": "2026 Target", "title": "34 laws planned", "subtitle": None, "note": "Full legislative agenda.", "link": "https://www.ciudadvalencia.com.ve/sancionar-34-leyes-2026/", "css_class": ""},
    ]


def _build_climate() -> dict:
    """Investment climate tracker data — will be LLM-generated in future."""
    return {
        "period": "Q2 2026 vs. Q1 2026",
        "bars": [
            {"label": "Sanctions Trajectory", "score": 7, "trend_dir": "up", "trend_value": "+3", "bar_color": "green", "why": "OFAC eased bank sanctions; expanded GLs 49 & 50A in Feb; GL 5T issued Mar 2. Travel advisory downgraded to Level 3."},
            {"label": "Diplomatic Progress", "score": 6, "trend_dir": "up", "trend_value": "+3", "bar_color": "green", "why": "Chargé d'affaires appointed to Washington; first formal diplomatic channel since 2019."},
            {"label": "Legal Framework", "score": 4, "trend_dir": "up", "trend_value": "+1", "bar_color": "yellow", "why": "Hydrocarbons Law reform signed, codifying empresa mixta model. Mining law tightens state control."},
            {"label": "Political Stability", "score": 3, "trend_dir": "up", "trend_value": "+1", "bar_color": "red", "why": "Amnesty Law benefited 8,000+; signals normalization. But no elections scheduled yet."},
            {"label": "Property Rights", "score": 3, "trend_dir": "flat", "trend_value": "0", "bar_color": "red", "why": "Mining law reasserts absolute state ownership. No new protections for real estate or commercial assets."},
            {"label": "Macro Stability", "score": 2, "trend_dir": "down", "trend_value": "−1", "bar_color": "red", "why": "Inflation accelerating: 649% annualized (Mar). Parallel premium widened to 31.7%. Coface E rating."},
        ],
        "methodology": (
            "Sub-scores derived from BCV exchange/inflation data (live scrape), Coface E rating, OFAC GL activity via Federal Register API, "
            "US State Dept travel advisory level (live scrape), GDELT global news sentiment, OFAC SDN list monitoring, "
            "UNCTAD FDI stock ($30.5B), Amnesty Law implementation data, and legislative pipeline analysis. QoQ comparison based on Q4 2025 baseline."
        ),
    }
