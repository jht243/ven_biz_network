"""Render the SEO email HTML with sample data so you can preview the template."""
from __future__ import annotations

import os
import sys
import webbrowser
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("OPENAI_API_KEY", "")

from src.seo.google_reporting import ReportArtifact, build_seo_email_html  # noqa: E402


def _artifact(name: str, source: str, rows: list[dict]) -> ReportArtifact:
    placeholder = Path("/tmp") / f"{name}.json"
    return ReportArtifact(
        name=name,
        source=source,
        rows=rows,
        row_count=len(rows),
        json_path=placeholder,
        csv_path=placeholder,
        latest_json_path=placeholder,
        latest_csv_path=placeholder,
    )


SAMPLE_GSC_PAGES = [
    {"page": "https://example.com/posts/foreign-direct-investment-venezuela",
     "clicks": 14, "impressions": 612, "ctr": 0.0229, "position": 12.3},
    {"page": "https://example.com/posts/oil-sector-permits-2026",
     "clicks": 8, "impressions": 401, "ctr": 0.0199, "position": 9.8},
    {"page": "https://example.com/posts/caracas-fintech-roundup",
     "clicks": 4, "impressions": 188, "ctr": 0.0213, "position": 15.4},
]
SAMPLE_GSC_QUERIES = [
    {"query": "venezuela investment risk", "clicks": 6, "impressions": 245, "ctr": 0.0245, "position": 11.2},
    {"query": "caracas fintech startups", "clicks": 3, "impressions": 142, "ctr": 0.0211, "position": 14.7},
]
SAMPLE_GA_CONTENT = [
    {"pagePathPlusQueryString": "/posts/foreign-direct-investment-venezuela",
     "pageTitle": "Foreign Direct Investment in Venezuela: 2026 Outlook",
     "screenPageViews": 178, "sessions": 132, "engagedSessions": 96,
     "averageSessionDuration": 138.4, "engagementRate": 0.727},
]
SAMPLE_GA_SOURCE = [
    {"sessionSourceMedium": "google / organic", "sessions": 412, "engagedSessions": 301,
     "engagementRate": 0.731, "averageSessionDuration": 122.5},
    {"sessionSourceMedium": "(direct) / (none)", "sessions": 98, "engagedSessions": 64,
     "engagementRate": 0.653, "averageSessionDuration": 88.1},
]
SAMPLE_GA_DEVICE = [
    {"deviceCategory": "mobile", "sessions": 318, "engagedSessions": 221,
     "engagementRate": 0.695, "averageSessionDuration": 102.7},
    {"deviceCategory": "desktop", "sessions": 189, "engagedSessions": 143,
     "engagementRate": 0.756, "averageSessionDuration": 144.2},
]


def main() -> int:
    artifacts = [
        _artifact("gsc_pages", "gsc", SAMPLE_GSC_PAGES),
        _artifact("gsc_queries", "gsc", SAMPLE_GSC_QUERIES),
        _artifact("ga_content_pages", "ga4", SAMPLE_GA_CONTENT),
        _artifact("ga_source_medium", "ga4", SAMPLE_GA_SOURCE),
        _artifact("ga_device", "ga4", SAMPLE_GA_DEVICE),
    ]
    html = build_seo_email_html(artifacts)
    out = Path(ROOT) / "output" / "seo_email_preview.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"Wrote preview to {out}")
    try:
        webbrowser.open(out.as_uri())
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
