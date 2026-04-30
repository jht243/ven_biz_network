#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import settings
from src.seo.google_reporting import fetch_google_reporting_data


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch GA4 + GSC data and write reporting artifacts."
    )
    parser.add_argument("--key-file", help="Service account JSON key file path")
    parser.add_argument("--ga4-property-id", default=settings.google_reporting_ga4_property_id)
    parser.add_argument("--gsc-site-url", default=settings.google_reporting_gsc_site_url)
    parser.add_argument(
        "--ga-lookback-days",
        type=int,
        default=settings.google_reporting_ga_lookback_days,
    )
    parser.add_argument(
        "--gsc-lookback-days",
        type=int,
        default=settings.google_reporting_gsc_lookback_days,
    )
    parser.add_argument(
        "--output-dir",
        default=str(settings.google_reporting_output_dir),
        help="Base output directory for report exports",
    )
    parser.add_argument("--skip-ga", action="store_true")
    parser.add_argument("--skip-gsc", action="store_true")
    return parser


def main() -> int:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parser().parse_args()
    try:
        run = fetch_google_reporting_data(
            key_file=args.key_file,
            ga4_property_id=args.ga4_property_id,
            gsc_site_url=args.gsc_site_url,
            ga_lookback_days=args.ga_lookback_days,
            gsc_lookback_days=args.gsc_lookback_days,
            output_dir=Path(args.output_dir),
            skip_ga=args.skip_ga,
            skip_gsc=args.skip_gsc,
        )
    except Exception as exc:
        logging.getLogger(__name__).error("Fetch failed: %s", exc, exc_info=True)
        return 1

    print(f"Wrote {len(run.artifacts)} report artifacts")
    print(f"Date folder: {run.dated_output_dir}")
    print(f"Latest folder: {run.latest_output_dir}")
    print(f"Manifest: {run.manifest_path}")
    print(f"Summary: {run.summary_path}")
    print(f"SEO decisions: {run.seo_decisions_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
