#!/usr/bin/env python3
"""Run Semrush keyword research for caracasresearch.com.

Usage:
    python scripts/semrush_keyword_research.py
    python scripts/semrush_keyword_research.py --domain caracasresearch.com --competitors reuters.com
    python scripts/semrush_keyword_research.py --seeds "venezuela bonds,pdvsa stock"
    python scripts/semrush_keyword_research.py --output output/semrush_report.txt

Requires SEMRUSH_API_KEY env var (or set in .env).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import settings
from src.seo.semrush import format_report, run_keyword_research


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Semrush keyword research for Caracas Research")
    p.add_argument("--domain", default="caracasresearch.com", help="Target domain")
    p.add_argument(
        "--competitors",
        default="",
        help="Comma-separated competitor domains for gap analysis",
    )
    p.add_argument(
        "--seeds",
        default="",
        help="Comma-separated seed keywords (overrides defaults)",
    )
    p.add_argument("--limit", type=int, default=100, help="Max organic keywords to pull")
    p.add_argument(
        "--output",
        default="",
        help="Write report to this file (default: stdout + output/semrush/)",
    )
    p.add_argument("--json", action="store_true", help="Also save raw JSON data")
    return p


def main() -> int:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parser().parse_args()

    if not settings.semrush_api_key:
        print(
            "ERROR: SEMRUSH_API_KEY is not set.\n"
            "  1. Get your key at https://www.semrush.com/accounts/subscription-info/api-units/\n"
            "  2. Add it to .env:  SEMRUSH_API_KEY=your_key_here\n"
            "  3. Or export it:   export SEMRUSH_API_KEY=your_key_here",
            file=sys.stderr,
        )
        return 1

    seeds = [s.strip() for s in args.seeds.split(",") if s.strip()] or None
    competitors = [c.strip() for c in args.competitors.split(",") if c.strip()] or None

    print(f"Running keyword research for {args.domain}...")
    if seeds:
        print(f"  Seed keywords: {seeds}")
    if competitors:
        print(f"  Competitors: {competitors}")
    print()

    results = run_keyword_research(
        domain=args.domain,
        seed_keywords=seeds,
        competitor_domains=competitors,
        limit=args.limit,
    )

    report = format_report(results)
    print(report)

    # Save to file
    dated = datetime.now().strftime("%Y-%m-%d")
    out_dir = Path(ROOT) / "output" / "semrush"
    out_dir.mkdir(parents=True, exist_ok=True)

    report_path = Path(args.output) if args.output else out_dir / f"keyword-research-{dated}.txt"
    report_path.write_text(report, encoding="utf-8")
    print(f"\nReport saved to {report_path}")

    if args.json:
        json_data = {
            "domain": args.domain,
            "date": dated,
            "domain_overview": results.get("domain_overview", []),
            "current_rankings": results.get("current_rankings", []),
            "related": results.get("related", []),
            "questions": results.get("questions", []),
            "competitor_gaps": results.get("competitor_gaps", []),
            "opportunities": [asdict(o) for o in results.get("opportunities", [])],
        }
        json_path = out_dir / f"keyword-research-{dated}.json"
        json_path.write_text(json.dumps(json_data, indent=2), encoding="utf-8")
        print(f"JSON data saved to {json_path}")

    # Summary stats
    opps = results.get("opportunities", [])
    rankings = results.get("current_rankings", [])
    print(f"\n── Summary ──")
    print(f"  Current organic keywords found: {len(rankings)}")
    print(f"  New keyword opportunities:      {len(opps)}")
    print(f"  Question keywords found:        {len(results.get('questions', []))}")
    print(f"  Competitor gap keywords:        {len(results.get('competitor_gaps', []))}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
