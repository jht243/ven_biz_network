from __future__ import annotations

import csv
import html
import json
import logging
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account

from src.config import settings

logger = logging.getLogger(__name__)

GA_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
SCOPES = [GA_SCOPE, GSC_SCOPE]

GA_API_BASE = "https://analyticsdata.googleapis.com/v1beta"
GSC_API_BASE = "https://www.googleapis.com/webmasters/v3"
_DECISION_CACHE: dict[int, dict[str, Any]] = {}


@dataclass
class ReportArtifact:
    name: str
    source: str
    rows: list[dict[str, Any]]
    row_count: int
    json_path: Path
    csv_path: Path
    latest_json_path: Path
    latest_csv_path: Path


@dataclass
class ReportingRun:
    artifacts: list[ReportArtifact]
    dated_output_dir: Path
    latest_output_dir: Path
    manifest_path: Path
    summary_path: Path
    seo_decisions_path: Path


GA_REPORT_SPECS: list[dict[str, Any]] = [
    {
        "name": "ga_site_summary",
        "dimensions": [],
        "metrics": [
            "sessions",
            "totalUsers",
            "screenPageViews",
            "engagedSessions",
            "engagementRate",
            "averageSessionDuration",
        ],
        "limit": 1,
    },
    {
        "name": "ga_landing_pages",
        "dimensions": ["landingPagePlusQueryString"],
        "metrics": [
            "sessions",
            "totalUsers",
            "screenPageViews",
            "engagementRate",
            "averageSessionDuration",
        ],
        "limit": 1000,
    },
    {
        "name": "ga_landing_pages_by_source",
        "dimensions": ["landingPagePlusQueryString", "sessionSourceMedium"],
        "metrics": ["sessions", "totalUsers", "screenPageViews", "engagementRate"],
        "limit": 1000,
    },
    {
        "name": "ga_content_pages",
        "dimensions": ["pagePathPlusQueryString", "pageTitle"],
        "metrics": [
            "screenPageViews",
            "totalUsers",
            "sessions",
            "engagementRate",
            "eventCount",
        ],
        "limit": 1000,
    },
    {
        "name": "ga_source_medium",
        "dimensions": ["sessionSourceMedium"],
        "metrics": ["sessions", "totalUsers", "screenPageViews", "engagementRate"],
        "limit": 200,
    },
    {
        "name": "ga_channel_group",
        "dimensions": ["sessionDefaultChannelGroup"],
        "metrics": ["sessions", "totalUsers", "screenPageViews", "engagementRate"],
        "limit": 200,
    },
    {
        "name": "ga_device",
        "dimensions": ["deviceCategory"],
        "metrics": ["sessions", "totalUsers", "screenPageViews", "engagementRate"],
        "limit": 50,
    },
    {
        "name": "ga_country",
        "dimensions": ["country"],
        "metrics": ["sessions", "totalUsers", "screenPageViews", "engagementRate"],
        "limit": 200,
    },
    {
        "name": "ga_city",
        "dimensions": ["country", "city"],
        "metrics": ["sessions", "totalUsers", "screenPageViews", "engagementRate"],
        "limit": 500,
    },
    {
        "name": "ga_events",
        "dimensions": ["eventName"],
        "metrics": ["eventCount", "totalUsers"],
        "limit": 200,
    },
    {
        "name": "ga_daily_traffic",
        "dimensions": ["date"],
        "metrics": ["sessions", "totalUsers", "screenPageViews", "engagedSessions"],
        "limit": 120,
    },
]

GSC_REPORT_SPECS: list[dict[str, Any]] = [
    {"name": "gsc_pages", "dimensions": ["page"]},
    {"name": "gsc_queries", "dimensions": ["query"]},
    {"name": "gsc_page_queries", "dimensions": ["page", "query"]},
    {"name": "gsc_daily", "dimensions": ["date"]},
    {"name": "gsc_page_daily", "dimensions": ["page", "date"]},
    {"name": "gsc_query_daily", "dimensions": ["query", "date"]},
    {"name": "gsc_device", "dimensions": ["device"]},
    {"name": "gsc_country", "dimensions": ["country"]},
    {"name": "gsc_page_device", "dimensions": ["page", "device"]},
    {"name": "gsc_query_device", "dimensions": ["query", "device"]},
    {"name": "gsc_page_country", "dimensions": ["page", "country"]},
    {"name": "gsc_query_country", "dimensions": ["query", "country"]},
    {"name": "gsc_search_appearance", "dimensions": ["searchAppearance"]},
]


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _pct(value: Any) -> float:
    return _to_float(value) * 100.0


def _fmt_num(value: Any) -> str:
    return f"{_to_int(value):,}"


def _fmt_pct(value: Any) -> str:
    return f"{_to_float(value):.2f}%"


def _fmt_pos(value: Any) -> str:
    return f"{_to_float(value):.1f}"


def _date_range(lookback_days: int) -> tuple[str, str]:
    end_date = date.today()
    start_date = end_date - timedelta(days=max(1, lookback_days))
    return start_date.isoformat(), end_date.isoformat()


def load_service_account_info(key_file: str | None = None) -> dict[str, Any]:
    if key_file:
        path = Path(key_file).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"Service account key file not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    if settings.google_reporting_sa_json:
        try:
            return json.loads(settings.google_reporting_sa_json)
        except json.JSONDecodeError as exc:
            raise ValueError("GOOGLE_REPORTING_SA_JSON is not valid JSON") from exc

    if settings.google_reporting_sa_file:
        path = Path(settings.google_reporting_sa_file).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"GOOGLE_REPORTING_SA_FILE not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    raise ValueError(
        "Google service account credentials missing. "
        "Set --key-file, GOOGLE_REPORTING_SA_JSON, or GOOGLE_REPORTING_SA_FILE."
    )


def _get_access_token(service_account_info: dict[str, Any]) -> str:
    creds = service_account.Credentials.from_service_account_info(
        service_account_info, scopes=SCOPES
    )
    creds.refresh(GoogleAuthRequest())
    if not creds.token:
        raise RuntimeError("Failed to obtain Google OAuth access token.")
    return str(creds.token)


def _ga_report_body(spec: dict[str, Any], start_date: str, end_date: str) -> dict[str, Any]:
    return {
        "dateRanges": [{"startDate": start_date, "endDate": end_date}],
        "dimensions": [{"name": d} for d in spec["dimensions"]],
        "metrics": [{"name": m} for m in spec["metrics"]],
        "limit": str(spec.get("limit", 1000)),
        "keepEmptyRows": False,
    }


def _parse_ga_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    dimension_headers = [h.get("name", "") for h in payload.get("dimensionHeaders", [])]
    metric_headers = [h.get("name", "") for h in payload.get("metricHeaders", [])]
    rows = []
    for row in payload.get("rows", []):
        out: dict[str, Any] = {}
        dim_values = row.get("dimensionValues", [])
        metric_values = row.get("metricValues", [])
        for i, header in enumerate(dimension_headers):
            out[header] = dim_values[i].get("value", "") if i < len(dim_values) else ""
        for i, header in enumerate(metric_headers):
            out[header] = metric_values[i].get("value", "") if i < len(metric_values) else ""
        rows.append(out)
    return rows


def _gsc_report_body(spec: dict[str, Any], start_date: str, end_date: str) -> dict[str, Any]:
    return {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": spec["dimensions"],
        "rowLimit": 25000,
        "startRow": 0,
    }


def _parse_gsc_rows(payload: dict[str, Any], dimensions: list[str]) -> list[dict[str, Any]]:
    rows = []
    for row in payload.get("rows", []):
        keys = row.get("keys", [])
        out: dict[str, Any] = {}
        for idx, dim in enumerate(dimensions):
            out[dim] = keys[idx] if idx < len(keys) else ""
        out["clicks"] = row.get("clicks", 0)
        out["impressions"] = row.get("impressions", 0)
        out["ctr"] = row.get("ctr", 0)
        out["position"] = row.get("position", 0)
        rows.append(out)
    return rows


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = sorted({k for row in rows for k in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _copy_to_latest(source_path: Path, latest_path: Path) -> None:
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, latest_path)


def _markdown_table(rows: list[dict[str, Any]], columns: list[str], limit: int = 10) -> str:
    if not rows:
        return "_No rows returned._"
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for row in rows[:limit]:
        lines.append("| " + " | ".join(str(row.get(col, "")) for col in columns) + " |")
    return "\n".join(lines)


def _analysis_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def _email_blurb(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts: list[str] = []
        for item in value.values():
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                for nested in item.values():
                    if isinstance(nested, str):
                        parts.append(nested)
        if parts:
            return " ".join(parts[:2])
    return "The LLM reviewed GSC and GA4 separately, then combined them into the recommendations below."


def _short_url(value: Any) -> str:
    text = str(value or "").strip()
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    if len(text) <= 72:
        return text
    return text[:45].rstrip("/") + "..." + text[-22:]


def _escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _artifact_map(artifacts: list[ReportArtifact]) -> dict[str, ReportArtifact]:
    return {a.name: a for a in artifacts}


def _top_rows(rows: list[dict[str, Any]], key: str, n: int = 10) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: _to_float(r.get(key)), reverse=True)[:n]


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in row.items():
        if key == "ctr":
            cleaned[key] = _fmt_pct(_pct(value))
        elif key == "position":
            cleaned[key] = _fmt_pos(value)
        elif key in {"sessions", "totalUsers", "screenPageViews", "engagedSessions", "eventCount", "clicks", "impressions"}:
            cleaned[key] = _to_int(value)
        elif key == "engagementRate":
            cleaned[key] = _fmt_pct(_pct(value))
        elif key == "averageSessionDuration":
            cleaned[key] = round(_to_float(value), 1)
        else:
            cleaned[key] = value
    return cleaned


def _opportunity_rows(rows: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    def score(row: dict[str, Any]) -> float:
        impressions = _to_float(row.get("impressions"))
        clicks = _to_float(row.get("clicks"))
        ctr = _to_float(row.get("ctr"))
        position = _to_float(row.get("position"))
        rank_bonus = max(0.0, 25.0 - position)
        ctr_gap = max(0.0, 0.10 - ctr)
        return impressions * (1.0 + ctr_gap * 20.0) + rank_bonus * 10.0 - clicks

    return [_clean_row(r) for r in sorted(rows, key=score, reverse=True)[:limit]]


def _analysis_payload(artifacts: list[ReportArtifact]) -> dict[str, Any]:
    amap = _artifact_map(artifacts)
    gsc_daily = amap.get("gsc_daily").rows if amap.get("gsc_daily") else []
    gsc_pages = amap.get("gsc_pages").rows if amap.get("gsc_pages") else []
    gsc_queries = amap.get("gsc_queries").rows if amap.get("gsc_queries") else []
    gsc_page_queries = (
        amap.get("gsc_page_queries").rows if amap.get("gsc_page_queries") else []
    )
    ga_site_summary = (
        amap.get("ga_site_summary").rows if amap.get("ga_site_summary") else []
    )
    ga_landing_pages = (
        amap.get("ga_landing_pages").rows if amap.get("ga_landing_pages") else []
    )
    ga_content_pages = (
        amap.get("ga_content_pages").rows if amap.get("ga_content_pages") else []
    )
    ga_source_medium = (
        amap.get("ga_source_medium").rows if amap.get("ga_source_medium") else []
    )
    ga_device = amap.get("ga_device").rows if amap.get("ga_device") else []
    ga_daily = amap.get("ga_daily_traffic").rows if amap.get("ga_daily_traffic") else []

    total_gsc_impressions = (
        sum(_to_float(r.get("impressions")) for r in gsc_daily)
        if gsc_daily
        else sum(_to_float(r.get("impressions")) for r in gsc_pages)
    )
    total_gsc_clicks = (
        sum(_to_float(r.get("clicks")) for r in gsc_daily)
        if gsc_daily
        else sum(_to_float(r.get("clicks")) for r in gsc_pages)
    )
    ga_sessions = (
        _to_float(ga_site_summary[0].get("sessions"))
        if ga_site_summary
        else 0.0
    )

    return {
        "site_name": settings.site_name,
        "data_check": {
            "total_gsc_impressions": _to_int(total_gsc_impressions),
            "total_gsc_clicks": _to_int(total_gsc_clicks),
            "ga4_sessions": _to_int(ga_sessions),
        },
        "gsc": {
            "top_pages_by_impressions": [_clean_row(r) for r in _top_rows(gsc_pages, "impressions", 15)],
            "top_queries_by_impressions": [_clean_row(r) for r in _top_rows(gsc_queries, "impressions", 15)],
            "top_page_query_pairs_by_impressions": [_clean_row(r) for r in _top_rows(gsc_page_queries, "impressions", 20)],
            "highest_potential_page_query_pairs": _opportunity_rows(gsc_page_queries, 20),
            "recent_daily_trend": [_clean_row(r) for r in sorted(gsc_daily, key=lambda x: str(x.get("date", "")))[-14:]],
        },
        "ga4": {
            "site_summary": [_clean_row(r) for r in ga_site_summary[:1]],
            "top_landing_pages": [_clean_row(r) for r in _top_rows(ga_landing_pages, "sessions", 15)],
            "top_content_pages": [_clean_row(r) for r in _top_rows(ga_content_pages, "screenPageViews", 15)],
            "traffic_sources": [_clean_row(r) for r in _top_rows(ga_source_medium, "sessions", 12)],
            "device_mix": [_clean_row(r) for r in _top_rows(ga_device, "sessions", 8)],
            "recent_daily_traffic": [_clean_row(r) for r in sorted(ga_daily, key=lambda x: str(x.get("date", "")))[-14:]],
        },
    }


def _fallback_llm_decision(payload: dict[str, Any]) -> dict[str, Any]:
    opportunities = payload["gsc"]["highest_potential_page_query_pairs"]
    top_pages = payload["gsc"]["top_pages_by_impressions"]
    updates = []
    for row in opportunities[:5]:
        updates.append(
            {
                "priority": "High" if len(updates) == 0 else "Medium",
                "source": "GSC",
                "page": row.get("page", ""),
                "query": row.get("query", ""),
                "evidence": (
                    f"{row.get('impressions', 0)} impressions, {row.get('clicks', 0)} clicks, "
                    f"{row.get('ctr', '0.00%')} CTR, avg position {row.get('position', '')}"
                ),
                "recommendation": "Rewrite the title/meta snippet and strengthen on-page copy for this query intent.",
            }
        )
    if not updates:
        target = top_pages[0].get("page", "top indexed page") if top_pages else "the site"
        updates.append(
            {
                "priority": "High",
                "source": "Combined",
                "page": target,
                "query": "",
                "evidence": "No strong query table rows were returned, so the first action is diagnostic.",
                "recommendation": "Verify GA4/GSC tracking, inspect indexed pages, and add clearer title/meta copy to the highest-impression page.",
            }
        )

    return {
        "decision": "SEO updates recommended.",
        "update_recommended": True,
        "data_check": payload["data_check"],
        "gsc_analysis": "GSC is the primary source for search opportunity discovery. The highest-potential page/query pairs show where impressions are already available but clicks can improve.",
        "ga4_analysis": "GA4 is used as engagement and landing-page context, not as a veto on SEO action.",
        "combined_analysis": "Prioritize search-snippet and on-page intent improvements where GSC already shows impressions, then use GA4 to watch whether landing engagement improves.",
        "recommended_updates": updates,
        "watchlist": [
            {
                "page": row.get("page", ""),
                "query": row.get("query", ""),
                "reason": f"{row.get('impressions', 0)} impressions at position {row.get('position', '')}",
            }
            for row in opportunities[5:15]
        ],
    }


def _llm_decision(artifacts: list[ReportArtifact]) -> dict[str, Any]:
    payload = _analysis_payload(artifacts)
    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY missing; using deterministic fallback SEO analysis.")
        return _fallback_llm_decision(payload)

    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)
        response = client.chat.completions.create(
            model=settings.openai_narrative_model or settings.openai_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a senior technical SEO strategist. Analyze imported Google Search Console "
                        "and GA4 data without hard minimum traffic thresholds. GSC and GA4 must be analyzed "
                        "separately first, then together. Always produce at least one practical recommendation. "
                        "GSC is allowed to drive recommendations even when GA4 traffic is low. Use GA4 as "
                        "supporting evidence for engagement, source mix, device mix, and landing-page behavior."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Return strict JSON with keys: decision, update_recommended, data_check, "
                        "gsc_analysis, ga4_analysis, combined_analysis, recommended_updates, watchlist. "
                        "gsc_analysis, ga4_analysis, and combined_analysis must be concise plain-English "
                        "strings of 1-3 sentences each, not nested objects. "
                        "decision should usually be 'SEO updates recommended.' because the report must "
                        "produce practical suggestions. recommended_updates must be a list of objects with "
                        "priority, source, page, query, evidence, recommendation. watchlist must be a list "
                        "of objects with page, query, reason. Here is the compacted data:\n\n"
                        + json.dumps(payload, ensure_ascii=False)
                    ),
                },
            ],
            temperature=0.2,
            max_tokens=1800,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "{}"
        parsed = json.loads(raw)
        fallback = _fallback_llm_decision(payload)
        parsed["decision"] = parsed.get("decision") or "SEO updates recommended."
        if "no seo updates recommended" in str(parsed["decision"]).lower():
            parsed["decision"] = "SEO updates recommended."
        parsed["update_recommended"] = True
        parsed.setdefault("data_check", payload["data_check"])
        parsed.setdefault("gsc_analysis", fallback["gsc_analysis"])
        parsed.setdefault("ga4_analysis", fallback["ga4_analysis"])
        parsed.setdefault("combined_analysis", fallback["combined_analysis"])
        parsed.setdefault("recommended_updates", fallback["recommended_updates"])
        parsed.setdefault("watchlist", fallback["watchlist"])
        return parsed
    except Exception as exc:
        logger.error("LLM SEO analysis failed; using fallback: %s", exc, exc_info=True)
        return _fallback_llm_decision(payload)


def _compute_decisions(artifacts: list[ReportArtifact]) -> dict[str, Any]:
    cache_key = id(artifacts)
    if cache_key not in _DECISION_CACHE:
        decision = _llm_decision(artifacts)
        data_check = decision.get("data_check", {})
        updates = decision.get("recommended_updates") or []
        watchlist = decision.get("watchlist") or []
        decision["total_gsc_impressions"] = _to_int(data_check.get("total_gsc_impressions"))
        decision["total_gsc_clicks"] = _to_int(data_check.get("total_gsc_clicks"))
        decision["ga_sessions"] = _to_int(data_check.get("ga4_sessions"))
        decision["page_query_candidates"] = updates
        decision["page_candidates"] = []
        decision["watchlist"] = watchlist
        _DECISION_CACHE[cache_key] = decision
    return _DECISION_CACHE[cache_key]


def _build_summary_markdown(artifacts: list[ReportArtifact]) -> str:
    amap = _artifact_map(artifacts)
    ga_summary_rows = amap.get("ga_site_summary").rows if amap.get("ga_site_summary") else []
    ga_landing_rows = amap.get("ga_landing_pages").rows if amap.get("ga_landing_pages") else []
    ga_content_rows = amap.get("ga_content_pages").rows if amap.get("ga_content_pages") else []
    ga_source_rows = amap.get("ga_source_medium").rows if amap.get("ga_source_medium") else []
    ga_device_rows = amap.get("ga_device").rows if amap.get("ga_device") else []
    gsc_pages_rows = amap.get("gsc_pages").rows if amap.get("gsc_pages") else []
    gsc_queries_rows = amap.get("gsc_queries").rows if amap.get("gsc_queries") else []
    gsc_pq_rows = amap.get("gsc_page_queries").rows if amap.get("gsc_page_queries") else []
    gsc_country_rows = amap.get("gsc_country").rows if amap.get("gsc_country") else []
    gsc_daily_rows = amap.get("gsc_daily").rows if amap.get("gsc_daily") else []
    decisions = _compute_decisions(artifacts)

    ga_summary_view = []
    if ga_summary_rows:
        row = ga_summary_rows[0]
        ga_summary_view = [
            {
                "sessions": _fmt_num(row.get("sessions")),
                "totalUsers": _fmt_num(row.get("totalUsers")),
                "screenPageViews": _fmt_num(row.get("screenPageViews")),
                "engagedSessions": _fmt_num(row.get("engagedSessions")),
                "engagementRate": _fmt_pct(_pct(row.get("engagementRate"))),
                "averageSessionDuration(s)": f"{_to_float(row.get('averageSessionDuration')):.1f}",
            }
        ]

    top_gsc_pages = []
    pages_with_clicks = []
    for r in _top_rows(gsc_pages_rows, "impressions", 12):
        row = {
            "page": r.get("page", ""),
            "impressions": _fmt_num(r.get("impressions")),
            "clicks": _fmt_num(r.get("clicks")),
            "ctr": _fmt_pct(_pct(r.get("ctr"))),
            "position": _fmt_pos(r.get("position")),
        }
        top_gsc_pages.append(row)
        if _to_float(r.get("clicks")) > 0:
            pages_with_clicks.append(row)

    page_opps = [
        {
            "priority": r.get("priority", ""),
            "source": r.get("source", ""),
            "page": r.get("page", ""),
            "query": r.get("query", ""),
            "recommendation": r.get("recommendation", ""),
        }
        for r in decisions["page_query_candidates"][:12]
    ]

    daily_trend = []
    for r in sorted(gsc_daily_rows, key=lambda x: str(x.get("date", "")))[-14:]:
        daily_trend.append(
            {
                "date": r.get("date", ""),
                "impressions": _fmt_num(r.get("impressions")),
                "clicks": _fmt_num(r.get("clicks")),
                "ctr": _fmt_pct(_pct(r.get("ctr"))),
                "position": _fmt_pos(r.get("position")),
            }
        )

    sections = [
        "# SEO Data Summary",
        "",
        "## GA4 Summary",
        _markdown_table(
            ga_summary_view,
            [
                "sessions",
                "totalUsers",
                "screenPageViews",
                "engagedSessions",
                "engagementRate",
                "averageSessionDuration(s)",
            ],
        ),
        "",
        "## Top Landing Pages",
        _markdown_table(
            _top_rows(ga_landing_rows, "sessions", 12),
            [
                "landingPagePlusQueryString",
                "sessions",
                "totalUsers",
                "screenPageViews",
                "engagementRate",
            ],
        ),
        "",
        "## Top Content Pages",
        _markdown_table(
            _top_rows(ga_content_rows, "screenPageViews", 12),
            [
                "pagePathPlusQueryString",
                "pageTitle",
                "screenPageViews",
                "totalUsers",
                "sessions",
                "engagementRate",
            ],
        ),
        "",
        "## Traffic Sources",
        _markdown_table(
            _top_rows(ga_source_rows, "sessions", 12),
            ["sessionSourceMedium", "sessions", "totalUsers", "screenPageViews", "engagementRate"],
        ),
        "",
        "## Device Mix",
        _markdown_table(
            _top_rows(ga_device_rows, "sessions", 12),
            ["deviceCategory", "sessions", "totalUsers", "screenPageViews", "engagementRate"],
        ),
        "",
        "## Top GSC Pages by Impressions",
        _markdown_table(top_gsc_pages, ["page", "impressions", "clicks", "ctr", "position"]),
        "",
        "## GSC Pages with Clicks",
        _markdown_table(pages_with_clicks[:12], ["page", "impressions", "clicks", "ctr", "position"]),
        "",
        "## Page Opportunities",
        _markdown_table(page_opps, ["priority", "source", "page", "query", "recommendation"]),
        "",
        "## Top GSC Queries",
        _markdown_table(
            [
                {
                    "query": r.get("query", ""),
                    "impressions": _fmt_num(r.get("impressions")),
                    "clicks": _fmt_num(r.get("clicks")),
                    "ctr": _fmt_pct(_pct(r.get("ctr"))),
                    "position": _fmt_pos(r.get("position")),
                }
                for r in _top_rows(gsc_queries_rows, "impressions", 12)
            ],
            ["query", "impressions", "clicks", "ctr", "position"],
        ),
        "",
        "## Top Page/Query Pairs",
        _markdown_table(
            [
                {
                    "page": r.get("page", ""),
                    "query": r.get("query", ""),
                    "impressions": _fmt_num(r.get("impressions")),
                    "clicks": _fmt_num(r.get("clicks")),
                    "ctr": _fmt_pct(_pct(r.get("ctr"))),
                    "position": _fmt_pos(r.get("position")),
                }
                for r in _top_rows(gsc_pq_rows, "impressions", 12)
            ],
            ["page", "query", "impressions", "clicks", "ctr", "position"],
        ),
        "",
        "## Country Mix",
        _markdown_table(
            [
                {
                    "country": r.get("country", ""),
                    "impressions": _fmt_num(r.get("impressions")),
                    "clicks": _fmt_num(r.get("clicks")),
                    "ctr": _fmt_pct(_pct(r.get("ctr"))),
                    "position": _fmt_pos(r.get("position")),
                }
                for r in _top_rows(gsc_country_rows, "impressions", 12)
            ],
            ["country", "impressions", "clicks", "ctr", "position"],
        ),
        "",
        "## Daily Trend",
        _markdown_table(daily_trend, ["date", "impressions", "clicks", "ctr", "position"]),
        "",
    ]
    return "\n".join(sections)


def _build_seo_decisions_markdown(artifacts: list[ReportArtifact]) -> str:
    d = _compute_decisions(artifacts)
    lines = [
        "# SEO Decision",
        "",
        f"**Decision:** {d['decision']}",
        "",
        "## Data Check",
        f"- Total GSC impressions: {d['total_gsc_impressions']:,}",
        f"- Total GSC clicks: {d['total_gsc_clicks']:,}",
        f"- GA4 sessions: {d['ga_sessions']:,}",
        "",
        "## GSC Analysis",
        _analysis_text(d.get("gsc_analysis", "")),
        "",
        "## GA4 Analysis",
        _analysis_text(d.get("ga4_analysis", "")),
        "",
        "## Combined Analysis",
        _analysis_text(d.get("combined_analysis", "")),
        "",
    ]
    lines.extend(
        [
            "## Recommended Updates",
            _markdown_table(
                d["page_query_candidates"][:10],
                ["priority", "source", "page", "query", "evidence", "recommendation"],
            ),
            "",
        ]
    )
    lines.extend(
        [
            "## Watchlist",
            _markdown_table(
                d["watchlist"][:12],
                ["page", "query", "reason"],
            ),
            "",
            "## Operating Rule",
            "- GSC and GA4 are analyzed separately, then together. GA4 traffic volume does not veto GSC-driven SEO recommendations.",
            "",
        ]
    )
    return "\n".join(lines)


def build_seo_email_html(artifacts: list[ReportArtifact]) -> str:
    amap = _artifact_map(artifacts)
    decisions = _compute_decisions(artifacts)
    gsc_pages = amap.get("gsc_pages").rows if amap.get("gsc_pages") else []
    gsc_queries = amap.get("gsc_queries").rows if amap.get("gsc_queries") else []
    ga_content = amap.get("ga_content_pages").rows if amap.get("ga_content_pages") else []
    ga_source = amap.get("ga_source_medium").rows if amap.get("ga_source_medium") else []
    ga_device = amap.get("ga_device").rows if amap.get("ga_device") else []

    def recommendation_cards(rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "<p style='color:#6b6357;margin:0;font-size:14px;'>No suggested updates returned.</p>"
        cards = []
        for idx, row in enumerate(rows, start=1):
            priority = _escape(row.get("priority", "Recommended")).upper()
            page = _escape(_short_url(row.get("page", "")))
            query = _escape(row.get("query", "") or "General page improvement")
            recommendation = _escape(row.get("recommendation", "Review this page for SEO improvements."))
            divider = "" if idx == len(rows) else (
                "<div style='border-top:1px solid #ece6dc;margin:18px 0 0;'></div>"
            )
            cards.append(
                "<div style='padding:0 0 18px;'>"
                "<table role='presentation' cellpadding='0' cellspacing='0' border='0' style='width:100%;'>"
                "<tr>"
                "<td style='vertical-align:top;width:30px;font-size:13px;font-weight:700;"
                "color:#1f2937;font-family:Georgia,\"Times New Roman\",serif;'>"
                f"{idx:02d}"
                "</td>"
                "<td style='vertical-align:top;'>"
                f"<div style='font-size:11px;font-weight:700;color:#92400e;letter-spacing:.12em;"
                f"text-transform:uppercase;margin-bottom:6px;'>{priority}</div>"
                f"<div style='font-size:16px;line-height:1.45;color:#1f2937;font-weight:600;"
                f"margin-bottom:10px;word-break:break-word;'>{recommendation}</div>"
                f"<div style='font-size:13px;color:#6b6357;line-height:1.5;word-break:break-word;'>"
                f"<span style='color:#9c9486;'>Query &middot;</span> {query}</div>"
                f"<div style='font-size:13px;color:#6b6357;line-height:1.5;word-break:break-word;'>"
                f"<span style='color:#9c9486;'>Page &middot;</span> {page}</div>"
                "</td>"
                "</tr>"
                "</table>"
                f"{divider}"
                "</div>"
            )
        return "".join(cards)

    def watch_cards(rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "<p style='color:#6b6357;margin:0;font-size:14px;'>No watchlist items returned.</p>"
        cards = []
        for idx, row in enumerate(rows, start=1):
            page = _escape(_short_url(row.get("page", "")))
            query = _escape(row.get("query", "") or "General")
            reason = _escape(row.get("reason", "Monitor this signal."))
            divider = "" if idx == len(rows) else (
                "<div style='border-top:1px solid #ece6dc;margin:14px 0 0;'></div>"
            )
            cards.append(
                "<div style='padding:0 0 14px;'>"
                f"<div style='font-size:14px;color:#1f2937;font-weight:600;line-height:1.4;"
                f"word-break:break-word;'>{query}</div>"
                f"<div style='font-size:12px;color:#9c9486;margin:4px 0 6px;word-break:break-word;'>{page}</div>"
                f"<div style='font-size:13px;color:#6b6357;line-height:1.5;'>{reason}</div>"
                f"{divider}"
                "</div>"
            )
        return "".join(cards)

    def metric_row(label: str, value: str, sub: str = "") -> str:
        sub_html = (
            f"<div style='font-size:12px;color:#9c9486;letter-spacing:.04em;'>{sub}</div>"
            if sub else ""
        )
        return (
            "<tr>"
            f"<td style='padding:14px 0;border-bottom:1px solid #ece6dc;'>"
            f"<div style='font-size:11px;color:#9c9486;text-transform:uppercase;"
            f"letter-spacing:.14em;margin-bottom:4px;'>{label}</div>"
            f"<div style='font-size:24px;font-weight:700;color:#1f2937;"
            f"font-family:Georgia,\"Times New Roman\",serif;'>{value}</div>"
            f"{sub_html}"
            "</td>"
            "</tr>"
        )

    suggested_rows = decisions["page_query_candidates"][:3]
    watch_rows = decisions["watchlist"][:3]
    top_page = _top_rows(gsc_pages, "impressions", 1)
    top_query = _top_rows(gsc_queries, "impressions", 1)
    top_content = _top_rows(ga_content, "screenPageViews", 1)
    organic = next(
        (
            r
            for r in ga_source
            if "google / organic" in str(r.get("sessionSourceMedium", "")).lower()
        ),
        None,
    )
    primary_device = _top_rows(ga_device, "sessions", 1)
    search_ctr = (
        decisions["total_gsc_clicks"] / decisions["total_gsc_impressions"] * 100
        if decisions["total_gsc_impressions"]
        else 0.0
    )
    top_page_label = _short_url(top_page[0].get("page", "No GSC page data")) if top_page else "No GSC page data"
    top_query_label = top_query[0].get("query", "No GSC query data") if top_query else "No GSC query data"
    top_content_label = (
        top_content[0].get("pageTitle")
        or top_content[0].get("pagePathPlusQueryString")
        if top_content
        else "No GA4 content data"
    )
    organic_sessions = _to_int(organic.get("sessions")) if organic else 0
    device_label = primary_device[0].get("deviceCategory", "unknown") if primary_device else "unknown"

    decision_text = (
        "SEO updates recommended"
        if decisions["update_recommended"]
        else "No SEO updates recommended today"
    )
    why_text = _escape(_email_blurb(decisions.get("combined_analysis")) or (
        "GSC and GA4 were analyzed separately, then combined into practical SEO recommendations."
    ))
    gsc_analysis_text = _escape(_email_blurb(decisions.get("gsc_analysis", "")))
    ga4_analysis_text = _escape(_email_blurb(decisions.get("ga4_analysis", "")))

    today_label = date.today().strftime("%A, %B %d, %Y")
    site_name = _escape(settings.site_name)
    decision_text_safe = _escape(decision_text)

    fact_rows = [
        ("Top GSC page", _escape(top_page_label)),
        ("Top GSC query", _escape(top_query_label)),
        ("Organic sessions", f"{organic_sessions:,}"),
        ("Top GA4 content", _escape(top_content_label)),
        ("Primary device", _escape(device_label)),
    ]
    fact_html = "".join(
        "<tr>"
        "<td style='padding:10px 0;border-bottom:1px solid #ece6dc;font-size:12px;"
        "color:#9c9486;text-transform:uppercase;letter-spacing:.12em;width:40%;"
        "vertical-align:top;'>"
        f"{label}"
        "</td>"
        "<td style='padding:10px 0;border-bottom:1px solid #ece6dc;font-size:14px;"
        "color:#1f2937;word-break:break-word;vertical-align:top;'>"
        f"{value}"
        "</td>"
        "</tr>"
        for label, value in fact_rows
    )

    metric_html = (
        metric_row("GSC Impressions", f"{decisions['total_gsc_impressions']:,}")
        + metric_row(
            "GSC Clicks",
            f"{decisions['total_gsc_clicks']:,}",
            sub=f"{search_ctr:.2f}% click-through rate",
        )
        + metric_row("GA4 Sessions", f"{decisions['ga_sessions']:,}")
    )

    return f"""
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width,initial-scale=1" />
    <title>{site_name} SEO memo</title>
  </head>
  <body style="margin:0;padding:0;background:#f6f3ef;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#1f2937;">
    <div style="max-width:600px;margin:0 auto;padding:24px 16px 32px;">
      <div style="background:#ffffff;border:1px solid #ece6dc;border-radius:18px;overflow:hidden;">
        <div style="padding:28px 28px 22px;border-bottom:1px solid #ece6dc;">
          <div style="font-size:11px;color:#9c9486;text-transform:uppercase;letter-spacing:.18em;margin-bottom:10px;">
            {today_label}
          </div>
          <div style="font-size:13px;color:#9c9486;text-transform:uppercase;letter-spacing:.12em;margin-bottom:6px;">
            {site_name} &middot; SEO memo
          </div>
          <h1 style="margin:0;font-size:26px;line-height:1.25;color:#1f2937;font-family:Georgia,'Times New Roman',serif;font-weight:700;">
            {decision_text_safe}
          </h1>
        </div>

        <div style="padding:8px 28px 18px;">
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;">
            {metric_html}
          </table>
        </div>

        <div style="padding:0 28px 22px;">
          <div style="font-size:12px;color:#9c9486;text-transform:uppercase;letter-spacing:.16em;margin:8px 0 8px;">
            Why this matters
          </div>
          <p style="margin:0;color:#3b3a36;line-height:1.6;font-size:15px;">
            {why_text}
          </p>
        </div>

        <div style="border-top:1px solid #ece6dc;"></div>

        <div style="padding:22px 28px 6px;">
          <div style="font-size:12px;color:#9c9486;text-transform:uppercase;letter-spacing:.16em;margin-bottom:14px;">
            Suggested updates
          </div>
          {recommendation_cards(suggested_rows)}
        </div>

        <div style="border-top:1px solid #ece6dc;"></div>

        <div style="padding:22px 28px 14px;">
          <div style="font-size:12px;color:#9c9486;text-transform:uppercase;letter-spacing:.16em;margin-bottom:6px;">
            At-a-glance data
          </div>
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;">
            {fact_html}
          </table>
        </div>

        <div style="border-top:1px solid #ece6dc;"></div>

        <div style="padding:22px 28px 14px;">
          <div style="font-size:12px;color:#9c9486;text-transform:uppercase;letter-spacing:.16em;margin-bottom:14px;">
            Watch next
          </div>
          {watch_cards(watch_rows)}
        </div>

        <div style="border-top:1px solid #ece6dc;"></div>

        <div style="padding:22px 28px 26px;">
          <div style="font-size:12px;color:#9c9486;text-transform:uppercase;letter-spacing:.16em;margin-bottom:10px;">
            Analyst notes
          </div>
          <p style="margin:0 0 12px;font-size:13px;line-height:1.6;color:#3b3a36;">
            <span style="color:#9c9486;">GSC &middot;</span> {gsc_analysis_text}
          </p>
          <p style="margin:0;font-size:13px;line-height:1.6;color:#3b3a36;">
            <span style="color:#9c9486;">GA4 &middot;</span> {ga4_analysis_text}
          </p>
        </div>
      </div>

      <p style="margin:18px 6px 0;font-size:11px;color:#9c9486;text-align:center;letter-spacing:.06em;">
        Generated by an automated GA4 + GSC analysis. Full data is stored alongside this run.
      </p>
    </div>
  </body>
</html>
""".strip()


def fetch_google_reporting_data(
    *,
    key_file: str | None = None,
    ga4_property_id: str | None = None,
    gsc_site_url: str | None = None,
    ga_lookback_days: int | None = None,
    gsc_lookback_days: int | None = None,
    output_dir: Path | None = None,
    skip_ga: bool = False,
    skip_gsc: bool = False,
) -> ReportingRun:
    ga_property = ga4_property_id or settings.google_reporting_ga4_property_id
    gsc_property = gsc_site_url or settings.google_reporting_gsc_site_url
    ga_days = ga_lookback_days or settings.google_reporting_ga_lookback_days
    gsc_days = gsc_lookback_days or settings.google_reporting_gsc_lookback_days
    base_output = output_dir or settings.google_reporting_output_dir

    if skip_ga and skip_gsc:
        raise ValueError("Cannot skip both GA and GSC.")
    if not skip_ga and not ga_property:
        raise ValueError("GA4 property ID is required.")
    if not skip_gsc and not gsc_property:
        raise ValueError("GSC site URL is required.")

    service_account_info = load_service_account_info(key_file)
    token = _get_access_token(service_account_info)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    today_label = date.today().isoformat()
    dated_output = base_output / today_label
    latest_output = base_output / "latest"
    dated_output.mkdir(parents=True, exist_ok=True)
    latest_output.mkdir(parents=True, exist_ok=True)

    artifacts: list[ReportArtifact] = []
    generated_at = datetime.now(timezone.utc).isoformat()

    ga_start, ga_end = _date_range(ga_days)
    gsc_start, gsc_end = _date_range(gsc_days)

    with httpx.Client(timeout=60) as client:
        if not skip_ga:
            for spec in GA_REPORT_SPECS:
                report_name = spec["name"]
                url = f"{GA_API_BASE}/properties/{ga_property}:runReport"
                payload = client.post(
                    url,
                    headers=headers,
                    json=_ga_report_body(spec, ga_start, ga_end),
                )
                payload.raise_for_status()
                response_json = payload.json()
                rows = _parse_ga_rows(response_json)
                json_path = dated_output / f"{report_name}.json"
                csv_path = dated_output / f"{report_name}.csv"
                latest_json = latest_output / f"{report_name}.json"
                latest_csv = latest_output / f"{report_name}.csv"
                _write_json(json_path, response_json)
                _write_csv(csv_path, rows)
                _copy_to_latest(json_path, latest_json)
                _copy_to_latest(csv_path, latest_csv)
                artifacts.append(
                    ReportArtifact(
                        name=report_name,
                        source="ga4",
                        rows=rows,
                        row_count=len(rows),
                        json_path=json_path,
                        csv_path=csv_path,
                        latest_json_path=latest_json,
                        latest_csv_path=latest_csv,
                    )
                )

        if not skip_gsc:
            encoded_site = quote(gsc_property, safe="")
            for spec in GSC_REPORT_SPECS:
                report_name = spec["name"]
                url = f"{GSC_API_BASE}/sites/{encoded_site}/searchAnalytics/query"
                payload = client.post(
                    url,
                    headers=headers,
                    json=_gsc_report_body(spec, gsc_start, gsc_end),
                )
                payload.raise_for_status()
                response_json = payload.json()
                rows = _parse_gsc_rows(response_json, spec["dimensions"])
                json_path = dated_output / f"{report_name}.json"
                csv_path = dated_output / f"{report_name}.csv"
                latest_json = latest_output / f"{report_name}.json"
                latest_csv = latest_output / f"{report_name}.csv"
                _write_json(json_path, response_json)
                _write_csv(csv_path, rows)
                _copy_to_latest(json_path, latest_json)
                _copy_to_latest(csv_path, latest_csv)
                artifacts.append(
                    ReportArtifact(
                        name=report_name,
                        source="gsc",
                        rows=rows,
                        row_count=len(rows),
                        json_path=json_path,
                        csv_path=csv_path,
                        latest_json_path=latest_json,
                        latest_csv_path=latest_csv,
                    )
                )

    summary_md = _build_summary_markdown(artifacts)
    decisions_md = _build_seo_decisions_markdown(artifacts)
    summary_path = dated_output / "summary.md"
    decisions_path = dated_output / "seo_decisions.md"
    latest_summary = latest_output / "summary.md"
    latest_decisions = latest_output / "seo_decisions.md"
    summary_path.write_text(summary_md, encoding="utf-8")
    decisions_path.write_text(decisions_md, encoding="utf-8")
    _copy_to_latest(summary_path, latest_summary)
    _copy_to_latest(decisions_path, latest_decisions)

    manifest = {
        "generated_at": generated_at,
        "ga4_property_id": ga_property,
        "gsc_property": gsc_property,
        "ga_lookback_days": ga_days,
        "gsc_lookback_days": gsc_days,
        "dated_output_dir": str(dated_output),
        "latest_output_dir": str(latest_output),
        "reports": [
            {
                "name": a.name,
                "source": a.source,
                "row_count": a.row_count,
                "json_path": str(a.json_path),
                "csv_path": str(a.csv_path),
                "latest_json_path": str(a.latest_json_path),
                "latest_csv_path": str(a.latest_csv_path),
            }
            for a in artifacts
        ],
        "summary_path": str(summary_path),
        "seo_decisions_path": str(decisions_path),
    }
    manifest_path = dated_output / "manifest.json"
    latest_manifest = latest_output / "manifest.json"
    _write_json(manifest_path, manifest)
    _copy_to_latest(manifest_path, latest_manifest)

    logger.info(
        "Google reporting complete: %d reports written to %s",
        len(artifacts),
        dated_output,
    )
    return ReportingRun(
        artifacts=artifacts,
        dated_output_dir=dated_output,
        latest_output_dir=latest_output,
        manifest_path=manifest_path,
        summary_path=summary_path,
        seo_decisions_path=decisions_path,
    )
