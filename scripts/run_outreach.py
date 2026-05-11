#!/usr/bin/env python3
"""Run the backlink outreach MVP pipeline."""

from __future__ import annotations

import json
import logging
import os
import sys

import click

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import settings
from src.outreach.pipeline import (
    DEFAULT_COMPETITORS,
    export_prospects,
    generate_pending_emails,
    process_prospects,
    pull_backlink_prospects,
    run_outreach_pipeline,
    run_weekly_check,
    send_pending_emails,
)


def _competitors(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()] or DEFAULT_COMPETITORS


def _print_summary(summary: dict) -> None:
    click.echo(json.dumps(summary, indent=2, default=str))


@click.group()
def cli() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


@cli.command()
@click.option("--competitors", default="", help="Comma-separated competitor domains")
@click.option("--limit", default=500, show_default=True, help="Backlink rows per competitor")
def pull(competitors: str, limit: int) -> None:
    """Pull competitor backlinks from Semrush."""
    _print_summary(pull_backlink_prospects(_competitors(competitors), limit_per_competitor=limit))


@cli.command("process")
@click.option("--limit", default=None, type=int, help="Max prospects to process")
@click.option(
    "--unprocessed-only",
    is_flag=True,
    help="Only prospects not yet crawled (empty page_text_snippet)",
)
@click.option(
    "--reprocess-scraped",
    is_flag=True,
    help="Re-crawl prospects that already have page_text_snippet (refresh contacts with new rules)",
)
def process_cmd(limit: int | None, unprocessed_only: bool, reprocess_scraped: bool) -> None:
    """Crawl, classify, score, and find contact emails."""
    _print_summary(
        process_prospects(
            limit=limit,
            unprocessed_only=unprocessed_only,
            reprocess_scraped=reprocess_scraped,
        )
    )


@cli.command("email")
@click.option("--limit", default=None, type=int, help="Max prospects to generate emails for")
def email_cmd(limit: int | None) -> None:
    """Generate email sequences for qualified prospects."""
    _print_summary(generate_pending_emails(limit=limit))


@cli.command("send")
@click.option("--limit", default=None, type=int, help="Max initial emails to send")
@click.option("--dry-run", is_flag=True, help="Log emails without sending through Resend")
@click.option(
    "--ignore-daily-limit",
    is_flag=True,
    help="Send up to --limit regardless of warmup daily cap (use with --limit)",
)
def send_cmd(limit: int | None, dry_run: bool, ignore_daily_limit: bool) -> None:
    """Send queued initial outreach emails through Resend."""
    _print_summary(
        send_pending_emails(limit=limit, dry_run=dry_run, ignore_daily_limit=ignore_daily_limit)
    )


@cli.command("check")
@click.option("--target-domain", default="caracasresearch.com", show_default=True)
def check_cmd(target_domain: str) -> None:
    """Check whether sent prospects now link to Caracas Research."""
    _print_summary(run_weekly_check(target_domain=target_domain))


@cli.command("export")
@click.option("--output", default="", help="Optional CSV output path")
def export_cmd(output: str) -> None:
    """Export prospects and statuses as CSV."""
    csv_text = export_prospects(output or None)
    if output:
        click.echo(f"Wrote {output}")
    else:
        click.echo(csv_text)


@cli.command("run")
@click.option("--competitors", default="", help="Comma-separated competitor domains")
@click.option("--limit", default=500, show_default=True, help="Backlink rows per competitor")
@click.option("--process-limit", default=None, type=int, help="Max prospects to crawl/process")
@click.option("--send/--no-send", default=False, show_default=True, help="Send queued initial emails")
@click.option("--dry-run", is_flag=True, help="Log emails without sending through Resend")
def run_cmd(
    competitors: str,
    limit: int,
    process_limit: int | None,
    send: bool,
    dry_run: bool,
) -> None:
    """Run the full MVP pipeline end-to-end."""
    _print_summary(
        run_outreach_pipeline(
            _competitors(competitors),
            limit_per_competitor=limit,
            process_limit=process_limit,
            send=send,
            dry_run=dry_run,
        )
    )


if __name__ == "__main__":
    cli()

