#!/usr/bin/env python3
"""
CLI entry point for the Venezuelan Business Network scraper.

Usage:
    # Scrape today's gazette and assembly news
    python run_scraper.py

    # Scrape a specific date
    python run_scraper.py --date 2026-03-27

    # Scrape a range of dates (backfill)
    python run_scraper.py --from 2026-03-01 --to 2026-03-31

    # Only run specific scrapers
    python run_scraper.py --source tugaceta
    python run_scraper.py --source assembly
"""

import logging
import sys
from datetime import date, datetime, timedelta

import click
from rich.console import Console
from rich.table import Table
from rich.logging import RichHandler

from src.pipeline import run_daily_scrape
from src.models import init_db

console = Console()


def setup_logging(level: str):
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, console=console)],
    )


@click.command()
@click.option("--date", "target_date", type=click.DateTime(formats=["%Y-%m-%d"]),
              default=None, help="Scrape a specific date (YYYY-MM-DD)")
@click.option("--from", "date_from", type=click.DateTime(formats=["%Y-%m-%d"]),
              default=None, help="Start date for backfill range")
@click.option("--to", "date_to", type=click.DateTime(formats=["%Y-%m-%d"]),
              default=None, help="End date for backfill range")
@click.option("--log-level", default="INFO", type=click.Choice(
              ["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False))
def main(target_date, date_from, date_to, log_level):
    """Venezuelan Business Network — Daily Scraper"""
    setup_logging(log_level)

    console.print("\n[bold blue]Venezuelan Business Network[/bold blue] — Scraper\n")

    init_db()
    console.print("[green]Database initialized.[/green]\n")

    if date_from and date_to:
        dates = _date_range(date_from.date(), date_to.date())
        console.print(f"Backfilling {len(dates)} days: {dates[0]} → {dates[-1]}\n")
        all_summaries = []
        for d in dates:
            summary = run_daily_scrape(d)
            all_summaries.append(summary)
        _print_backfill_table(all_summaries)
    else:
        scrape_date = target_date.date() if target_date else date.today()
        console.print(f"Scraping for: [bold]{scrape_date}[/bold]\n")
        summary = run_daily_scrape(scrape_date)
        _print_summary(summary)


def _date_range(start: date, end: date) -> list[date]:
    days = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def _print_summary(summary: dict):
    table = Table(title=f"Scrape Results — {summary['date']}")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green", justify="right")

    table.add_row("Gazettes Found", str(summary["gazettes_found"]))
    table.add_row("Gazettes New", str(summary["gazettes_new"]))
    table.add_row("News Items Found", str(summary["news_found"]))
    table.add_row("News Items New", str(summary["news_new"]))
    table.add_row("PDFs Downloaded", str(summary["pdfs_downloaded"]))
    table.add_row("OCR Completed", str(summary["ocr_completed"]))

    if summary["errors"]:
        table.add_row("Errors", str(len(summary["errors"])))
        for err in summary["errors"]:
            console.print(f"  [red]• {err}[/red]")

    console.print(table)


def _print_backfill_table(summaries: list[dict]):
    table = Table(title="Backfill Results")
    table.add_column("Date", style="cyan")
    table.add_column("Gazettes", justify="right")
    table.add_column("News", justify="right")
    table.add_column("PDFs", justify="right")
    table.add_column("OCR", justify="right")
    table.add_column("Errors", justify="right", style="red")

    for s in summaries:
        table.add_row(
            s["date"],
            str(s["gazettes_new"]),
            str(s["news_new"]),
            str(s["pdfs_downloaded"]),
            str(s["ocr_completed"]),
            str(len(s["errors"])),
        )

    console.print(table)

    total_gazettes = sum(s["gazettes_new"] for s in summaries)
    total_news = sum(s["news_new"] for s in summaries)
    console.print(f"\n[bold]Total: {total_gazettes} gazettes, {total_news} news items[/bold]")


if __name__ == "__main__":
    main()
