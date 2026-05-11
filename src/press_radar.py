"""
Press-Release Radar — Phase 2c of the daily pipeline.

Scans today's analyzed articles for original, defensible, reporter-worthy
findings that could become a press release, research alert, or media pitch.
Only considers articles from primary or near-primary sources (government
websites, official gazettes, central banks, sanctions regulators, multilateral
institutions, serious trade publications). Automatically excludes aggregators
such as Google News and GDELT.

Qualifying candidates (press_score >= 7) are emailed to the configured
recipient via Resend.
"""

from __future__ import annotations

import json
import logging
import textwrap
from datetime import date, timedelta

import httpx
from openai import OpenAI

from src.config import settings
from src.models import (
    AssemblyNewsEntry,
    ExternalArticleEntry,
    GazetteEntry,
    GazetteStatus,
    SessionLocal,
    SourceType,
)

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

MIN_RELEVANCE_FOR_RADAR = 6      # Only evaluate articles the pipeline already scored 6+
MIN_PRESS_SCORE_TO_EMAIL = 7     # Only email if the press-radar LLM scores 7+
LOOKBACK_DAYS = 2                # Rolling window: today + yesterday

# Sources accepted as primary / near-primary intelligence
PRIMARY_SOURCES: frozenset[SourceType] = frozenset({
    SourceType.FEDERAL_REGISTER,    # U.S. Federal Register — government
    SourceType.OFAC_SDN,            # OFAC SDN List — sanctions regulator
    SourceType.GACETA_OFICIAL,      # Venezuela Official Gazette
    SourceType.TU_GACETA,           # Official Gazette mirror / re-publisher
    SourceType.ASAMBLEA_NACIONAL,   # National Assembly — state
    SourceType.TSJ,                 # Supreme Court — official
    SourceType.BCV_RATES,           # Banco Central de Venezuela — central bank
    SourceType.TRAVEL_ADVISORY,     # U.S. State Dept Travel Advisory — government
    SourceType.ITA_TRADE,           # ITA / U.S. Trade Administration — government trade
    SourceType.ANSA_LATINA,         # ANSA Latina — serious trade news wire
    SourceType.EIA,                 # U.S. Energy Information Administration — government
})

# Human-readable labels for the email body
SOURCE_LABELS: dict[SourceType, str] = {
    SourceType.FEDERAL_REGISTER:  "Federal Register (U.S. Government)",
    SourceType.OFAC_SDN:          "OFAC SDN List (U.S. Treasury / Sanctions Regulator)",
    SourceType.GACETA_OFICIAL:    "Gaceta Oficial de Venezuela (Official Gazette)",
    SourceType.TU_GACETA:         "Tu Gaceta Oficial (Official Gazette Mirror)",
    SourceType.ASAMBLEA_NACIONAL: "Asamblea Nacional de Venezuela (National Assembly)",
    SourceType.TSJ:               "Tribunal Supremo de Justicia (Supreme Court)",
    SourceType.BCV_RATES:         "Banco Central de Venezuela (Central Bank)",
    SourceType.TRAVEL_ADVISORY:   "U.S. State Department Travel Advisory",
    SourceType.ITA_TRADE:         "ITA / U.S. Trade Administration (Trade Data)",
    SourceType.ANSA_LATINA:       "ANSA Latina (Trade News Wire)",
    SourceType.EIA:               "U.S. Energy Information Administration",
}

# ── LLM Prompts ───────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a senior media strategist and intelligence analyst specializing in Venezuela,
Latin America sanctions, and emerging-market investment.

Your task: evaluate whether a scraped article from a primary or near-primary source
contains a finding that could become a press release, research alert, or media pitch —
something that would be genuinely ORIGINAL intelligence, not a rewrite of already-viral news.

STRICT EXCLUSION RULES:
1. Do NOT recommend items that are already widely published across Reuters, AP, Bloomberg,
   BBC, major newspapers, or general news aggregators.
2. Do NOT recommend generic political commentary, opinion pieces without new facts,
   unsupported claims, or routine government statements with no market relevance.
3. The finding must meet at LEAST 3 of these 8 criteria:
   (a) Contains a new number, data point, policy change, license, approval, sanction,
       enforcement action, or regulatory shift
   (b) Affects investors, companies, banks, exporters, insurers, compliance teams, or policymakers
   (c) Has a clear "why now" reason
   (d) Reveals a trend not yet widely covered
   (e) Connects Venezuela or Cuba to broader Latin America, U.S. policy, sanctions, energy,
       migration, trade, or capital flows
   (f) Can support a clear headline
   (g) Can be verified with a link to the source
   (h) Creates a reason for journalists to contact us for explanation, quote, or follow-up data

Return a JSON object with EXACTLY these fields:
{
  "press_score": <int 1-10; 7+ means genuinely newsworthy as original intelligence>,
  "one_sentence_finding": "<single sentence capturing the core finding>",
  "not_commoditized_because": "<why this is NOT already published mainstream news>",
  "reporter_interest": "<why a reporter covering Venezuela/sanctions/LatAm investment would care>",
  "investor_business_relevance": "<what this means for investors, companies, or compliance teams>",
  "suggested_headline": "<a punchy, factual press release headline>",
  "executive_quote_angle": "<the angle for a branded executive quote — what would a Venezuela analyst say?>",
  "factcheck_risks": "<any fact-check risks, legal concerns, or compliance flags before publishing>",
  "recommendation": "<either 'Use as press release' or 'Use as research alert' — never both>",
  "meets_criteria_count": <int, how many of the 8 press-release criteria (a)-(h) this meets>
}

SCORE CALIBRATION:
- 1-4: Routine data or widely-known fact. Not newsworthy as original intel.
- 5-6: Interesting background; 'Use as research alert' if it is primary-source data not yet synthesized.
- 7-8: Genuinely differentiated finding. A reporter covering the region would consider it.
- 9-10: Major scoop — new enforcement, new number, new policy not yet public news.
- Only mark 'Use as press release' if score is 7+ AND the finding is defensible, sourced, and timely.
- Default to 'Use as research alert' for scores 5-6.

Return ONLY the JSON object, no markdown fences or explanation.\
"""

_USER_TEMPLATE = """\
Evaluate this article for press-release potential:

SOURCE TYPE: {source_type}
SOURCE NAME: {source_name}
SOURCE CREDIBILITY: {credibility}
DATE: {published_date}
HEADLINE: {headline}
URL: {source_url}

EXISTING INVESTOR ANALYSIS (from our LLM pipeline):
  Relevance Score : {relevance_score}/10
  Sectors        : {sectors}
  Takeaway       : {takeaway}
  Status         : {status_label}
  Is Breaking    : {is_breaking}

ARTICLE BODY (truncated to 2 000 chars):
{body_text}\
"""


# ── Data helpers ───────────────────────────────────────────────────────────────

def _as_article_dict(row, source_type: SourceType) -> dict:
    """Normalise a DB row to a flat dict the evaluator can consume."""
    analysis = row.analysis_json or {}
    return {
        "id": row.id,
        "table": row.__tablename__,
        "source": source_type,
        "source_label": SOURCE_LABELS.get(source_type, str(source_type)),
        "source_name": getattr(row, "source_name", None) or SOURCE_LABELS.get(source_type, ""),
        "credibility": getattr(row, "credibility", None) or "official",
        "headline": getattr(row, "headline", None) or getattr(row, "title", "") or "",
        "published_date": str(getattr(row, "published_date", date.today())),
        "source_url": getattr(row, "source_url", "") or "",
        "body_text": (getattr(row, "body_text", None) or getattr(row, "ocr_text", None) or "")[:2000],
        "analysis_json": analysis,
    }


def _fetch_primary_articles(db, cutoff: date) -> list[dict]:
    """
    Return all ANALYZED articles from primary sources within the lookback window.
    Queries ExternalArticleEntry, AssemblyNewsEntry, and GazetteEntry separately
    and merges into a single list.
    """
    results: list[dict] = []

    # 1. ExternalArticleEntry — only primary source types
    primary_source_values = [s.value for s in PRIMARY_SOURCES]
    ext_rows = (
        db.query(ExternalArticleEntry)
        .filter(ExternalArticleEntry.status == GazetteStatus.ANALYZED)
        .filter(ExternalArticleEntry.published_date >= cutoff)
        .filter(ExternalArticleEntry.source.in_(primary_source_values))
        .all()
    )
    for row in ext_rows:
        results.append(_as_article_dict(row, row.source))

    # 2. AssemblyNewsEntry (always primary: national assembly)
    if SourceType.ASAMBLEA_NACIONAL in PRIMARY_SOURCES:
        assembly_rows = (
            db.query(AssemblyNewsEntry)
            .filter(AssemblyNewsEntry.status == GazetteStatus.ANALYZED)
            .filter(AssemblyNewsEntry.published_date >= cutoff)
            .all()
        )
        for row in assembly_rows:
            results.append(_as_article_dict(row, SourceType.ASAMBLEA_NACIONAL))

    # 3. GazetteEntry (Gaceta Oficial, TSJ) — official government documents
    gazette_primary = [
        s.value for s in (SourceType.GACETA_OFICIAL, SourceType.TU_GACETA, SourceType.TSJ)
        if s in PRIMARY_SOURCES
    ]
    if gazette_primary:
        gazette_rows = (
            db.query(GazetteEntry)
            .filter(GazetteEntry.status == GazetteStatus.ANALYZED)
            .filter(GazetteEntry.published_date >= cutoff)
            .filter(GazetteEntry.source.in_(gazette_primary))
            .all()
        )
        for row in gazette_rows:
            results.append(_as_article_dict(row, row.source))

    return results


# ── LLM evaluation ─────────────────────────────────────────────────────────────

def _evaluate_press_potential(client: OpenAI, art: dict, analysis: dict) -> dict:
    """Call the LLM and return the structured press-radar evaluation dict."""
    sectors = analysis.get("sectors") or []
    user_msg = _USER_TEMPLATE.format(
        source_type=art["source_label"],
        source_name=art["source_name"],
        credibility=str(art.get("credibility", "official")),
        published_date=art["published_date"],
        headline=art["headline"],
        source_url=art["source_url"],
        relevance_score=analysis.get("relevance_score", "?"),
        sectors=", ".join(sectors) if sectors else "N/A",
        takeaway=analysis.get("takeaway", "")[:400],
        status_label=analysis.get("status_label", ""),
        is_breaking=str(analysis.get("is_breaking", False)),
        body_text=art["body_text"] or "(no body text available)",
    )

    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.2,
        max_tokens=700,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content
    return json.loads(raw)


# ── Email builder ──────────────────────────────────────────────────────────────

_SCORE_COLORS = {
    range(1, 5):  ("#6b7280", "Low"),
    range(5, 7):  ("#d97706", "Moderate"),
    range(7, 9):  ("#16a34a", "High"),
    range(9, 11): ("#dc2626", "Very High"),
}


def _score_style(score: int) -> tuple[str, str]:
    for r, (color, label) in _SCORE_COLORS.items():
        if score in r:
            return color, label
    return "#6b7280", "Unknown"


def _badge(score: int) -> str:
    color, label = _score_style(score)
    return (
        f'<span style="display:inline-block;padding:3px 10px;border-radius:12px;'
        f'background:{color};color:#fff;font-size:13px;font-weight:700;">'
        f'Score {score}/10 — {label}</span>'
    )


def _recommendation_pill(rec: str) -> str:
    if "press release" in rec.lower():
        bg = "#dc2626"
    else:
        bg = "#2563eb"
    return (
        f'<span style="display:inline-block;padding:3px 12px;border-radius:12px;'
        f'background:{bg};color:#fff;font-size:13px;font-weight:700;">'
        f'{rec}</span>'
    )


def _row(label: str, value: str) -> str:
    escaped = str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        f'<tr>'
        f'<td style="padding:8px 12px;font-weight:600;color:#374151;'
        f'white-space:nowrap;vertical-align:top;width:200px;">{label}</td>'
        f'<td style="padding:8px 12px;color:#111827;">{escaped}</td>'
        f'</tr>'
    )


def _candidate_block(art: dict, analysis: dict, evaluation: dict, index: int) -> str:
    score = evaluation.get("press_score", 0)
    rec = evaluation.get("recommendation", "Use as research alert")
    headline = art.get("headline") or ""
    source_url = art.get("source_url") or ""
    source_label = art.get("source_label") or str(art.get("source", ""))
    pub_date = art.get("published_date", "")
    rel_score = (analysis.get("relevance_score") or 0)
    criteria_count = evaluation.get("meets_criteria_count", "?")

    return f"""
<div style="margin:24px 0;padding:20px 24px;border:1px solid #e5e7eb;border-radius:8px;background:#fff;">
  <div style="margin-bottom:12px;">
    {_badge(score)}&nbsp;&nbsp;{_recommendation_pill(rec)}
  </div>
  <h2 style="font-size:17px;font-weight:700;color:#111827;margin:10px 0 4px;">
    #{index} — {headline[:120]}
  </h2>
  <p style="font-size:12px;color:#6b7280;margin:0 0 16px;">
    {source_label} &nbsp;·&nbsp; {pub_date} &nbsp;·&nbsp;
    Pipeline relevance: {rel_score}/10 &nbsp;·&nbsp;
    Criteria met: {criteria_count}/8
  </p>
  <p style="margin:0 0 16px;">
    <a href="{source_url}" style="color:#2563eb;font-size:13px;word-break:break-all;">{source_url}</a>
  </p>
  <table style="width:100%;border-collapse:collapse;font-size:14px;line-height:1.5;">
    {_row("A. Press Score", f"{score}/10")}
    {_row("B. Source URL", source_url)}
    {_row("C. Source Type", source_label)}
    {_row("D. Finding", evaluation.get("one_sentence_finding", ""))}
    {_row("E. Not Commoditized Because", evaluation.get("not_commoditized_because", ""))}
    {_row("F. Reporter Interest", evaluation.get("reporter_interest", ""))}
    {_row("G. Investor / Business Relevance", evaluation.get("investor_business_relevance", ""))}
    {_row("H. Suggested Headline", evaluation.get("suggested_headline", ""))}
    {_row("I. Executive Quote Angle", evaluation.get("executive_quote_angle", ""))}
    {_row("J. Fact-Check / Compliance Risks", evaluation.get("factcheck_risks", ""))}
    {_row("K. Recommendation", rec)}
  </table>
</div>
"""


def _build_email_html(candidates: list[tuple[dict, dict, dict]]) -> str:
    today_str = date.today().strftime("%B %d, %Y")
    n = len(candidates)
    blocks = "".join(
        _candidate_block(art, analysis, evaluation, i + 1)
        for i, (art, analysis, evaluation) in enumerate(candidates)
    )

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Press Radar — {today_str}</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <div style="max-width:680px;margin:32px auto;padding:0 16px;">

    <!-- Header -->
    <div style="background:#1e3a5f;border-radius:8px 8px 0 0;padding:24px 28px;">
      <h1 style="margin:0;color:#fff;font-size:22px;font-weight:800;letter-spacing:-0.3px;">
        📡 Press-Release Radar
      </h1>
      <p style="margin:6px 0 0;color:#93c5fd;font-size:14px;">
        {today_str} &nbsp;·&nbsp; {n} qualifying candidate{"s" if n != 1 else ""}
      </p>
    </div>

    <!-- Summary note -->
    <div style="background:#eff6ff;border:1px solid #bfdbfe;border-top:none;
                padding:14px 24px;font-size:13px;color:#1e40af;">
      These findings were identified from <strong>primary or near-primary sources</strong>
      only (government, regulatory, central bank, official gazette, or serious trade wire).
      Aggregators (Google News, GDELT) have been excluded. Each item scored
      <strong>{MIN_PRESS_SCORE_TO_EMAIL}+/10</strong> on press-release potential
      and met at least 3 of 8 criteria for original, reporter-worthy intelligence.
    </div>

    <!-- Candidates -->
    <div style="background:#f9fafb;padding:0 4px;">
      {blocks}
    </div>

    <!-- Footer -->
    <div style="background:#f3f4f6;border-top:1px solid #e5e7eb;
                padding:16px 24px;font-size:12px;color:#6b7280;border-radius:0 0 8px 8px;">
      Generated by the Caracas Research daily pipeline (press_radar.py) &nbsp;·&nbsp;
      Articles scored using GPT-4o with editorial judgment criteria &nbsp;·&nbsp;
      Always verify source URLs before publishing
    </div>

  </div>
</body>
</html>
"""


# ── Email dispatch ─────────────────────────────────────────────────────────────

def _send_press_radar_email(subject: str, html_body: str, idempotency_key: str) -> bool:
    """Send via Resend using the approved intake.layer3labs.io domain."""
    api_key = settings.resend_api_key
    if not api_key:
        logger.warning("RESEND_API_KEY not configured — press radar email skipped")
        return False

    from_addr = settings.press_radar_from_email
    to_addr   = settings.press_radar_recipient_email

    payload = {
        "from":    from_addr,
        "to":      [to_addr],
        "subject": subject,
        "html":    html_body,
    }

    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            timeout=30,
        )
        if resp.status_code in (200, 201, 202):
            logger.info("Press radar email sent to %s (idempotency=%s)", to_addr, idempotency_key)
            return True
        logger.error(
            "Resend error %d sending press radar: %s", resp.status_code, resp.text[:300]
        )
        return False
    except Exception as exc:
        logger.error("Press radar email dispatch failed: %s", exc, exc_info=True)
        return False


# ── Public entry point ─────────────────────────────────────────────────────────

def run_press_radar(dry_run: bool = False) -> dict:
    """
    Scan today's analyzed articles for press-release-worthy findings.

    Only evaluates articles that:
      - Come from primary / near-primary sources (government, regulators, trade wire)
      - Have already been scored >= MIN_RELEVANCE_FOR_RADAR by the main LLM pipeline
      - Were published within the past LOOKBACK_DAYS days

    Sends a single digest email (via Resend) for all qualifying candidates
    (press_score >= MIN_PRESS_SCORE_TO_EMAIL).

    Returns a summary dict with counts.
    """
    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY not set — skipping press radar")
        return {"scanned": 0, "candidates": 0, "emailed": 0, "status": "skipped_no_openai_key"}

    client = OpenAI(api_key=settings.openai_api_key)
    db = SessionLocal()
    cutoff = date.today() - timedelta(days=LOOKBACK_DAYS)

    summary: dict = {
        "scanned": 0,
        "evaluated": 0,
        "candidates": 0,
        "emailed": 0,
        "errors": 0,
        "lookback_days": LOOKBACK_DAYS,
        "min_relevance": MIN_RELEVANCE_FOR_RADAR,
        "min_press_score": MIN_PRESS_SCORE_TO_EMAIL,
    }

    try:
        articles = _fetch_primary_articles(db, cutoff)
        logger.info(
            "Press radar: %d primary-source analyzed articles in %d-day window",
            len(articles), LOOKBACK_DAYS,
        )
    finally:
        db.close()

    qualifying: list[tuple[dict, dict, dict]] = []

    for art in articles:
        summary["scanned"] += 1
        analysis = art.get("analysis_json") or {}
        relevance = analysis.get("relevance_score", 0) or 0

        if relevance < MIN_RELEVANCE_FOR_RADAR:
            continue

        summary["evaluated"] += 1
        try:
            evaluation = _evaluate_press_potential(client, art, analysis)
            press_score = int(evaluation.get("press_score", 0) or 0)
            meets = evaluation.get("meets_criteria_count", 0) or 0
            logger.info(
                "Press radar [score=%d criteria=%s]: %s",
                press_score,
                meets,
                (art.get("headline") or "")[:70],
            )
            if press_score >= MIN_PRESS_SCORE_TO_EMAIL:
                qualifying.append((art, analysis, evaluation))
                summary["candidates"] += 1
        except Exception as exc:
            logger.error(
                "Press radar eval failed for article %s: %s",
                art.get("id"),
                exc,
                exc_info=True,
            )
            summary["errors"] += 1

    if qualifying:
        today_iso = date.today().isoformat()
        subject = (
            f"[Press Radar] {len(qualifying)} candidate"
            f"{'s' if len(qualifying) != 1 else ''} — "
            f"{date.today().strftime('%B %d, %Y')}"
        )
        html_body = _build_email_html(qualifying)
        idempotency_key = f"press-radar/{today_iso}"

        if dry_run:
            logger.info(
                "DRY RUN: would send press radar email with %d candidates (key=%s)",
                len(qualifying), idempotency_key,
            )
            summary["dry_run"] = True
        else:
            sent = _send_press_radar_email(subject, html_body, idempotency_key)
            summary["emailed"] = 1 if sent else 0
    else:
        logger.info("Press radar: no qualifying candidates today (scanned=%d evaluated=%d)",
                    summary["scanned"], summary["evaluated"])

    return summary
