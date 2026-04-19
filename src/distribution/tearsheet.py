"""
Daily Venezuela Investor Tearsheet — branded PDF generator.

Produces a 1-2 page PDF that condenses the day's intelligence into a
research-note-style document suitable for:
  - Embedding as a downloadable asset on every briefing page (UX + SEO)
  - Permanent archival on Internet Archive (indexed by Google)
  - Weekly upload to SSRN / Scribd / Academia.edu (long-tail authority)

Pure-Python (ReportLab) implementation — no native deps to manage on
Render. Output is a `bytes` blob, which the caller writes to Supabase
Storage and an Internet Archive upload helper.

Layout (single PDF, portrait, US Letter):
  ┌─────────────────────────────────────────────────────────┐
  │  CARACAS RESEARCH                       <date> · Vol N  │
  │  Daily Venezuela Investor Tearsheet                     │
  │ ─────────────────────────────────────────────────────── │
  │  KPI strip: BCV USD | Parallel | Premium | Advisory     │
  │ ─────────────────────────────────────────────────────── │
  │  TODAY'S TOP DEVELOPMENT                                │
  │  <headline> · <takeaway 2-3 sentences>                  │
  │ ─────────────────────────────────────────────────────── │
  │  OTHER NOTABLE ITEMS                                    │
  │  • <bullet> · <one-line>                                │
  │  • <bullet> · <one-line>                                │
  │ ─────────────────────────────────────────────────────── │
  │  INVESTMENT CLIMATE — Q2 2026 vs Q1 2026                │
  │  <6 score bars in 2 columns>                            │
  │ ─────────────────────────────────────────────────────── │
  │  UPCOMING CALENDAR (next 14 days)                       │
  │  <date> · <event>                                       │
  │ ─────────────────────────────────────────────────────── │
  │  Footer: methodology · disclaimer · caracasresearch.com │
  └─────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import io
import logging
import re
from datetime import date, datetime, timedelta
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from src.config import settings
from src.models import (
    AssemblyNewsEntry,
    ExternalArticleEntry,
    GazetteStatus,
    SessionLocal,
    init_db,
)
from src.storage_remote import (
    public_object_url,
    supabase_storage_enabled,
    upload_object,
)

logger = logging.getLogger(__name__)


# ── Brand palette (matches the website) ───────────────────────────────
BRAND_BLUE = colors.HexColor("#1e3a8a")    # navy primary
BRAND_ACCENT = colors.HexColor("#0ea5e9")  # cyan accent
BRAND_INK = colors.HexColor("#0f172a")     # near-black body
BRAND_MUTED = colors.HexColor("#64748b")   # muted gray
BRAND_BG = colors.HexColor("#f8fafc")      # very light fill
BRAND_RULE = colors.HexColor("#cbd5e1")    # rule lines
BRAND_GOOD = colors.HexColor("#16a34a")
BRAND_WARN = colors.HexColor("#f59e0b")
BRAND_BAD = colors.HexColor("#dc2626")

PAGE_W, PAGE_H = letter
MARGIN = 0.5 * inch


# ── Styles ────────────────────────────────────────────────────────────
def _styles():
    base = getSampleStyleSheet()
    return {
        "brand": ParagraphStyle(
            "brand", parent=base["Normal"],
            fontName="Helvetica-Bold", fontSize=14, leading=16,
            textColor=BRAND_BLUE, spaceAfter=2,
        ),
        "title": ParagraphStyle(
            "title", parent=base["Normal"],
            fontName="Helvetica-Bold", fontSize=18, leading=22,
            textColor=BRAND_INK, spaceAfter=2,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", parent=base["Normal"],
            fontName="Helvetica-Oblique", fontSize=9, leading=12,
            textColor=BRAND_MUTED, spaceAfter=8,
        ),
        "section": ParagraphStyle(
            "section", parent=base["Normal"],
            fontName="Helvetica-Bold", fontSize=10, leading=12,
            textColor=BRAND_BLUE, spaceBefore=6, spaceAfter=3,
        ),
        "lede_head": ParagraphStyle(
            "lede_head", parent=base["Normal"],
            fontName="Helvetica-Bold", fontSize=11, leading=14,
            textColor=BRAND_INK, spaceAfter=3,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"],
            fontName="Helvetica", fontSize=9, leading=12,
            textColor=BRAND_INK, spaceAfter=4,
        ),
        "body_sm": ParagraphStyle(
            "body_sm", parent=base["Normal"],
            fontName="Helvetica", fontSize=8, leading=11,
            textColor=BRAND_INK,
        ),
        "muted_sm": ParagraphStyle(
            "muted_sm", parent=base["Normal"],
            fontName="Helvetica", fontSize=7.5, leading=10,
            textColor=BRAND_MUTED,
        ),
        "bullet": ParagraphStyle(
            "bullet", parent=base["Normal"],
            fontName="Helvetica", fontSize=9, leading=12,
            textColor=BRAND_INK, leftIndent=0, bulletIndent=0, spaceAfter=4,
        ),
        "kpi_label": ParagraphStyle(
            "kpi_label", parent=base["Normal"],
            fontName="Helvetica", fontSize=7, leading=9,
            textColor=BRAND_MUTED, alignment=1,  # center
        ),
        "kpi_value": ParagraphStyle(
            "kpi_value", parent=base["Normal"],
            fontName="Helvetica-Bold", fontSize=13, leading=15,
            textColor=BRAND_INK, alignment=1,
        ),
        "kpi_change": ParagraphStyle(
            "kpi_change", parent=base["Normal"],
            fontName="Helvetica", fontSize=7, leading=9,
            textColor=BRAND_GOOD, alignment=1,
        ),
        "footer": ParagraphStyle(
            "footer", parent=base["Normal"],
            fontName="Helvetica", fontSize=7, leading=9,
            textColor=BRAND_MUTED, alignment=1,
        ),
    }


# ── Data assembly (reuses the report_generator builders) ──────────────
def _strip_html(text: Optional[str]) -> str:
    """Collapse HTML/Markdown emphasis markers and tags from a string."""
    if not text:
        return ""
    # ReportLab Paragraph has a tiny HTML subset, but the takeaways may
    # contain arbitrary tags/markdown. Normalize aggressively.
    s = re.sub(r"<[^>]+>", "", text)
    s = s.replace("**", "").replace("__", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def collect_tearsheet_data() -> dict:
    """Pull everything the tearsheet needs from the live DB. Returns a
    plain dict — no ReportLab objects — so the data layer is testable
    and reusable (e.g. for a future weekly tearsheet)."""
    # Lazy imports to avoid circular dependency at module load.
    from src.report_generator import (
        _build_calendar,
        _build_climate,
        _build_entries,
        _build_ticker,
    )

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
        ticker = _build_ticker(db)
        calendar_events = _build_calendar(ext_articles, assembly_news)
        climate = _build_climate()
    finally:
        db.close()

    # Top items: highest-relevance, freshest
    fresh = sorted(
        [e for e in entries if (date.today() - e["published_date"]).days <= 7],
        key=lambda e: (e.get("relevance", 0), e["published_date"]),
        reverse=True,
    )
    top = fresh[0] if fresh else None
    other = [e for e in fresh[1:6]]  # next 5 for the bullet list

    upcoming = []
    today = date.today()
    horizon = today + timedelta(days=14)
    for ev in calendar_events:
        ev_date_raw = ev.get("date")
        if isinstance(ev_date_raw, date):
            ev_date = ev_date_raw
        elif isinstance(ev_date_raw, str):
            try:
                ev_date = datetime.strptime(ev_date_raw[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
        else:
            continue
        if today <= ev_date <= horizon:
            upcoming.append({"date": ev_date, "title": ev.get("title") or ev.get("event") or ""})
    upcoming.sort(key=lambda x: x["date"])
    upcoming = upcoming[:8]

    return {
        "generated_at": datetime.utcnow(),
        "ticker": ticker,
        "climate": climate,
        "top": top,
        "other": other,
        "upcoming": upcoming,
        "total_entries_window": len(fresh),
    }


# ── Layout primitives ─────────────────────────────────────────────────
def _header_table(generated_at: datetime, styles) -> Table:
    """Brand banner row. Two cells: brand on left, date+volume on right."""
    today = generated_at.date()
    # Volume number = days since site launch (gives a stable monotonically-
    # increasing identifier without needing a DB counter).
    launch = date(2026, 4, 1)
    vol = max(1, (today - launch).days + 1)
    issue_label = today.strftime("%A, %B %-d, %Y") if hasattr(today, "strftime") else str(today)
    # Use %#d on Windows, %-d on Unix; fall back manually for safety.
    try:
        issue_label = today.strftime("%A, %B %-d, %Y")
    except ValueError:
        issue_label = today.strftime("%A, %B %d, %Y").replace(" 0", " ")

    left = [
        Paragraph("CARACAS RESEARCH", styles["brand"]),
        Paragraph("Daily Venezuela Investor Tearsheet", styles["title"]),
        Paragraph(
            "Independent investment intelligence on Venezuela — "
            "sanctions, FX, calendar, and policy",
            styles["subtitle"],
        ),
    ]
    right = [
        Paragraph(f"<para align='right'><b>{issue_label}</b></para>", styles["body"]),
        Paragraph(
            f"<para align='right'>Vol. {vol} · No. {today.strftime('%Y%m%d')}</para>",
            styles["muted_sm"],
        ),
        Paragraph(
            "<para align='right'>caracasresearch.com</para>",
            styles["muted_sm"],
        ),
    ]
    t = Table(
        [[left, right]],
        colWidths=[(PAGE_W - 2 * MARGIN) * 0.6, (PAGE_W - 2 * MARGIN) * 0.4],
        hAlign="LEFT",
    )
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 1.2, BRAND_BLUE),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _kpi_row(ticker_items: list[dict], styles) -> Table:
    """4-cell KPI strip: BCV, Parallel, Premium %, Travel Advisory."""
    cells = []
    bcv_official = next(
        (t for t in ticker_items if t.get("label") == "BCV Official"), None
    )
    bcv_parallel = next(
        (t for t in ticker_items if t.get("label") == "USD Parallel"), None
    )
    advisory = next(
        (t for t in ticker_items if t.get("label") == "Travel Advisory"), None
    )

    def kpi_cell(label: str, value: str, change: str = "", color=BRAND_INK):
        return [
            Paragraph(label.upper(), styles["kpi_label"]),
            Paragraph(value, ParagraphStyle(
                "k", parent=styles["kpi_value"], textColor=color,
            )),
            Paragraph(change or "&nbsp;", styles["kpi_change"]),
        ]

    cells.append(kpi_cell(
        "BCV Official Rate",
        f"Bs.D {bcv_official['value']}/$" if bcv_official else "n/a",
        "live BCV scrape" if bcv_official else "",
    ))
    cells.append(kpi_cell(
        "Parallel Rate",
        f"Bs.D {bcv_parallel['value']}/$" if bcv_parallel else "n/a",
        "Monitor (avg)" if bcv_parallel else "",
        color=BRAND_WARN if bcv_parallel else BRAND_INK,
    ))
    if bcv_official and bcv_parallel:
        try:
            premium = (float(bcv_parallel["value"]) / float(bcv_official["value"]) - 1) * 100
            premium_str = f"{premium:+.1f}%"
            premium_color = BRAND_BAD if premium > 20 else BRAND_WARN if premium > 10 else BRAND_GOOD
        except (ValueError, ZeroDivisionError):
            premium_str = "n/a"
            premium_color = BRAND_INK
    else:
        premium_str, premium_color = "n/a", BRAND_INK
    cells.append(kpi_cell("Parallel Premium", premium_str, "vs official", color=premium_color))

    if advisory:
        adv_value = advisory.get("value", "n/a")
        adv_color = BRAND_GOOD if "1" in adv_value or "2" in adv_value else (
            BRAND_WARN if "3" in adv_value else BRAND_BAD
        )
        cells.append(kpi_cell(
            "US Travel Advisory",
            adv_value,
            advisory.get("source", "State Dept"),
            color=adv_color,
        ))
    else:
        cells.append(kpi_cell("US Travel Advisory", "n/a", ""))

    col_w = (PAGE_W - 2 * MARGIN) / 4
    t = Table([cells], colWidths=[col_w] * 4, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BRAND_BG),
        ("BOX", (0, 0), (-1, -1), 0.5, BRAND_RULE),
        ("LINEBEFORE", (1, 0), (-1, -1), 0.5, BRAND_RULE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _top_dev_block(top: Optional[dict], styles) -> list:
    if not top:
        return [Paragraph("No high-relevance developments in the last 7 days.", styles["body"])]
    head = _strip_html(top.get("headline_short") or top.get("headline") or "")
    take = _strip_html(top.get("takeaway_plain") or "")
    src = _strip_html(top.get("source_display") or "")
    cat = _strip_html(top.get("category_label") or "")
    date_disp = top.get("date_display") or ""
    meta = " · ".join(filter(None, [date_disp, cat, src]))
    return [
        Paragraph("TODAY'S TOP DEVELOPMENT", styles["section"]),
        Paragraph(head, styles["lede_head"]),
        Paragraph(f"<i>{meta}</i>", styles["muted_sm"]),
        Spacer(1, 4),
        Paragraph(take, styles["body"]),
    ]


def _other_items_block(other: list[dict], styles) -> list:
    if not other:
        return []
    out = [Paragraph("OTHER NOTABLE ITEMS", styles["section"])]
    for e in other:
        head = _strip_html(e.get("headline_short") or e.get("headline") or "")
        take = _strip_html(e.get("takeaway_plain") or "")
        # Trim takeaway to ~140 chars for a tight bullet
        if len(take) > 140:
            take = take[:137].rsplit(" ", 1)[0] + "…"
        cat = _strip_html(e.get("category_label") or "")
        date_disp = e.get("date_display") or ""
        line = f"<b>{head}</b> &nbsp;<font color='#64748b'>· {date_disp} · {cat}</font><br/>{take}"
        out.append(Paragraph(line, styles["bullet"]))
    return out


def _climate_block(climate: dict, styles) -> list:
    """6-bar investment climate scorecard, 2-column grid."""
    if not climate:
        return []
    bars = climate.get("bars") or []
    if not bars:
        return []

    rows = []
    color_map = {
        "green": BRAND_GOOD,
        "yellow": BRAND_WARN,
        "red": BRAND_BAD,
    }

    def cell(bar):
        score = bar.get("score", 0)
        color = color_map.get(bar.get("bar_color", ""), BRAND_INK)
        trend = bar.get("trend_value") or ""
        trend_dir = bar.get("trend_dir") or "flat"
        arrow = "▲" if trend_dir == "up" else "▼" if trend_dir == "down" else "■"
        label = _strip_html(bar.get("label") or "")
        why = _strip_html(bar.get("why") or "")
        if len(why) > 110:
            why = why[:107].rsplit(" ", 1)[0] + "…"
        score_html = (
            f"<font color='{color.hexval()[:-2]}'><b>{score}/10</b></font> "
            f"<font color='{BRAND_MUTED.hexval()[:-2]}' size='7'>{arrow} {trend}</font>"
        )
        return [
            Paragraph(f"<b>{label}</b>", styles["body_sm"]),
            Paragraph(score_html, styles["body_sm"]),
            Paragraph(why, styles["muted_sm"]),
        ]

    for i in range(0, len(bars), 2):
        left = cell(bars[i])
        right = cell(bars[i + 1]) if i + 1 < len(bars) else [""]
        rows.append([left, right])

    col_w = (PAGE_W - 2 * MARGIN) / 2 - 4
    t = Table(rows, colWidths=[col_w, col_w], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (-1, -1), 0.5, BRAND_RULE),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, BRAND_RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    period = climate.get("period") or ""
    return [
        Paragraph("INVESTMENT CLIMATE SCORECARD", styles["section"]),
        Paragraph(f"<i>{period}</i>", styles["muted_sm"]),
        Spacer(1, 3),
        t,
    ]


def _calendar_block(upcoming: list[dict], styles) -> list:
    if not upcoming:
        return []
    rows = [
        [Paragraph("<b>Date</b>", styles["body_sm"]),
         Paragraph("<b>Event</b>", styles["body_sm"])]
    ]
    for ev in upcoming:
        date_str = ev["date"].strftime("%a %b %d")
        title = _strip_html(ev["title"])
        if len(title) > 95:
            title = title[:92].rsplit(" ", 1)[0] + "…"
        rows.append([
            Paragraph(date_str, styles["body_sm"]),
            Paragraph(title, styles["body_sm"]),
        ])
    t = Table(
        rows,
        colWidths=[1.0 * inch, PAGE_W - 2 * MARGIN - 1.0 * inch],
        hAlign="LEFT",
    )
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_BG),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, BRAND_RULE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, BRAND_RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return [
        Paragraph("UPCOMING CALENDAR (NEXT 14 DAYS)", styles["section"]),
        t,
    ]


def _footer(generated_at: datetime, styles) -> list:
    ts = generated_at.strftime("%Y-%m-%d %H:%M UTC")
    parts = [
        Table([[""]], colWidths=[PAGE_W - 2 * MARGIN], rowHeights=[0.5],
              style=TableStyle([("LINEABOVE", (0, 0), (-1, -1), 0.6, BRAND_BLUE)])),
        Spacer(1, 4),
        Paragraph(
            f"Generated {ts} · "
            f"Sources: BCV (live scrape), OFAC SDN, US State Dept, "
            f"Federal Register, GDELT, Asamblea Nacional. "
            f"Methodology: <font color='{BRAND_BLUE.hexval()[:-2]}'>"
            f"caracasresearch.com/methodology</font>.",
            styles["footer"],
        ),
        Paragraph(
            "<b>Disclaimer:</b> This document is for informational purposes only "
            "and does not constitute investment, legal, or tax advice. Sanctions "
            "regimes change frequently — always verify current OFAC guidance and "
            "consult qualified counsel before any transaction.",
            styles["footer"],
        ),
        Paragraph(
            "© Caracas Research · caracasresearch.com",
            styles["footer"],
        ),
    ]
    return [Spacer(1, 8), KeepTogether(parts)]


# ── Public API ────────────────────────────────────────────────────────
def render_daily_tearsheet_pdf(data: Optional[dict] = None) -> bytes:
    """Render the daily tearsheet to a PDF byte string. If `data` is
    omitted, pulls fresh data from the DB."""
    if data is None:
        data = collect_tearsheet_data()

    styles = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
        title="Caracas Research — Daily Venezuela Investor Tearsheet",
        author="Caracas Research",
        subject="Venezuela investment intelligence",
        keywords="Venezuela, investment, sanctions, OFAC, BCV, tearsheet",
    )

    flow = []
    flow.append(_header_table(data["generated_at"], styles))
    flow.append(Spacer(1, 8))
    flow.append(_kpi_row(data["ticker"], styles))
    flow.extend(_top_dev_block(data["top"], styles))
    flow.extend(_other_items_block(data["other"], styles))
    flow.extend(_climate_block(data["climate"], styles))
    flow.extend(_calendar_block(data["upcoming"], styles))
    flow.extend(_footer(data["generated_at"], styles))

    doc.build(flow)
    return buf.getvalue()


def _object_key_for_date(d: date) -> str:
    return f"tearsheets/daily/{d.strftime('%Y-%m-%d')}.pdf"


_LATEST_KEY = "tearsheets/daily/latest.pdf"


def publish_daily_tearsheet() -> dict:
    """Generate today's tearsheet, upload it to Supabase Storage twice
    (once under a date-stamped key for permanence, once as 'latest.pdf'
    for the homepage download button). Returns a small summary dict for
    the cron orchestrator."""
    if not supabase_storage_enabled():
        return {"status": "skipped", "reason": "supabase storage not configured"}

    try:
        data = collect_tearsheet_data()
        pdf_bytes = render_daily_tearsheet_pdf(data)
    except Exception as exc:
        logger.exception("tearsheet: render failed: %s", exc)
        return {"status": "error", "stage": "render", "error": str(exc)}

    today = data["generated_at"].date()
    dated_key = _object_key_for_date(today)

    try:
        dated_url = upload_object(
            dated_key, pdf_bytes,
            content_type="application/pdf",
            cache_control="public, max-age=31536000, immutable",
        )
        latest_url = upload_object(
            _LATEST_KEY, pdf_bytes,
            content_type="application/pdf",
            cache_control="public, max-age=300",
        )
    except Exception as exc:
        logger.exception("tearsheet: upload failed: %s", exc)
        return {"status": "error", "stage": "upload", "error": str(exc)}

    logger.info(
        "tearsheet: published %s (%d bytes) → %s",
        dated_key, len(pdf_bytes), dated_url,
    )
    return {
        "status": "ok",
        "size_bytes": len(pdf_bytes),
        "date": today.isoformat(),
        "dated_url": dated_url,
        "latest_url": latest_url,
    }


def latest_tearsheet_public_url() -> Optional[str]:
    """Stable public URL for today's tearsheet — used by the homepage
    download button. Returns None if Supabase isn't configured."""
    return public_object_url(_LATEST_KEY)


def tearsheet_url_for_date(d: date) -> Optional[str]:
    return public_object_url(_object_key_for_date(d))
