#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import settings
from src.newsletter import send_email
from src.seo.google_reporting import build_seo_email_html, fetch_google_reporting_data


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch GA4/GSC data, build SEO email HTML, and send it."
    )
    parser.add_argument("--key-file", help="Service account JSON key file path")
    parser.add_argument("--ga4-property-id", default=settings.google_reporting_ga4_property_id)
    parser.add_argument("--gsc-site-url", default=settings.google_reporting_gsc_site_url)
    parser.add_argument("--to", default=settings.seo_email_recipient)
    parser.add_argument("--subject", default=settings.seo_email_subject)
    parser.add_argument("--provider", default=settings.seo_email_provider)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parser().parse_args()

    if not args.to:
        logging.getLogger(__name__).error("Missing recipient. Set --to or SEO_EMAIL_RECIPIENT.")
        return 1
    if not args.subject:
        logging.getLogger(__name__).error("Missing subject. Set --subject or SEO_EMAIL_SUBJECT.")
        return 1

    try:
        run = fetch_google_reporting_data(
            key_file=args.key_file,
            ga4_property_id=args.ga4_property_id,
            gsc_site_url=args.gsc_site_url,
        )
        html = build_seo_email_html(run.artifacts)
    except Exception as exc:
        logging.getLogger(__name__).error("Data fetch/build failed: %s", exc, exc_info=True)
        return 1

    result = send_email(
        to=args.to,
        subject=args.subject,
        html_body=html,
        provider_name=args.provider,
        dry_run=args.dry_run,
    )
    if not result.get("success"):
        logging.getLogger(__name__).error("Email send failed: %s", result)
        return 1

    print(f"Email send succeeded via {result.get('provider')} to {result.get('to')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
