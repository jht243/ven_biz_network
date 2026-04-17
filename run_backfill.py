#!/usr/bin/env python3
"""
Backfill historical data from official sources.

By default, scrapes from 2026-01-01 to today across:
  - Federal Register (single API call covering the full range)
  - Asamblea Nacional (per-day loop)
  - Gaceta Oficial — TuGaceta + official portal (per-day loop)
  - OFAC SDN (current snapshot — historical state is not recoverable)
  - State Dept Travel Advisory (current state)

After scraping, runs the analyzer and regenerates the report.

Usage:
    python run_backfill.py
    python run_backfill.py --start-date 2026-01-01 --end-date 2026-04-15
    python run_backfill.py --sources federal_register,asamblea_nacional
    python run_backfill.py --skip-analyze --skip-report
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import date, datetime, timedelta

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn

from src.config import settings
from src.models import init_db
from src.pipeline import (
    _persist_articles,
    _persist_gazettes,
    _persist_news,
    _log_scrape,
)
from src.scraper.assembly import AssemblyNewsScraper
from src.scraper.federal_register import FederalRegisterScraper
from src.scraper.gazette import OfficialGazetteScraper, TuGacetaScraper
from src.scraper.ofac_sdn import OFACSdnScraper
from src.scraper.travel_advisory import TravelAdvisoryScraper

console = Console()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s  %(name)-30s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_backfill")


PER_DAY_SCRAPERS = {
    "asamblea_nacional": AssemblyNewsScraper,
    "tu_gaceta": TuGacetaScraper,
    "gaceta_oficial": OfficialGazetteScraper,
}

ONE_SHOT_SCRAPERS = {
    "federal_register",
    "ofac_sdn",
    "travel_advisory",
}

ALL_SOURCES = sorted(set(PER_DAY_SCRAPERS) | ONE_SHOT_SCRAPERS)
DEFAULT_SOURCES = ",".join(ALL_SOURCES)


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _date_range(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def _persist_result(result, target_date: date) -> dict:
    """Persist a ScrapeResult and return per-bucket counts."""
    counts = {"gazettes_new": 0, "news_new": 0, "articles_new": 0}
    if result.gazettes:
        counts["gazettes_new"] = len(_persist_gazettes(result.gazettes))
    if result.news:
        counts["news_new"] = len(_persist_news(result.news))
    if result.articles:
        counts["articles_new"] = len(_persist_articles(result.articles))
    _log_scrape(result, target_date)
    return counts


def _backfill_federal_register(start: date, end: date) -> dict:
    """Federal Register supports a date range natively — one API call."""
    console.print(f"[cyan]→ Federal Register[/cyan] ({start} → {end})")
    scraper = FederalRegisterScraper()
    summary = {"articles_new": 0, "errors": []}
    try:
        articles = scraper._search_ofac_venezuela(start, end)
        if articles:
            new_ids = _persist_articles(articles)
            summary["articles_new"] = len(new_ids)
            console.print(f"  [green]✓[/green] {len(articles)} fetched, {len(new_ids)} new")
        else:
            console.print("  [dim]no documents in range[/dim]")
    except Exception as e:
        logger.error("Federal Register backfill failed: %s", e, exc_info=True)
        summary["errors"].append(str(e))
        console.print(f"  [red]✗[/red] {e}")
    finally:
        scraper.close()
    return summary


def _scrape_one_shot(source: str, target_date: date) -> dict:
    """Snapshot-style scrapers: just run for `target_date` once."""
    console.print(f"[cyan]→ {source}[/cyan]")
    summary = {"articles_new": 0, "errors": []}

    if source == "ofac_sdn":
        scraper = OFACSdnScraper()
    elif source == "travel_advisory":
        scraper = TravelAdvisoryScraper()
    else:
        return summary

    try:
        result = scraper.scrape(target_date)
        if result.success:
            counts = _persist_result(result, target_date)
            summary["articles_new"] = counts.get("articles_new", 0)
            console.print(f"  [green]✓[/green] {summary['articles_new']} new articles")
        else:
            summary["errors"].append(result.error)
            console.print(f"  [red]✗[/red] {result.error}")
    except Exception as e:
        logger.error("%s one-shot failed: %s", source, e, exc_info=True)
        summary["errors"].append(str(e))
        console.print(f"  [red]✗[/red] {e}")
    finally:
        scraper.close()
    return summary


def _backfill_per_day(source: str, start: date, end: date) -> dict:
    """Loop one day at a time for date-keyed scrapers."""
    scraper_cls = PER_DAY_SCRAPERS[source]
    summary = {"gazettes_new": 0, "news_new": 0, "articles_new": 0, "errors": 0}

    days = list(_date_range(start, end))
    console.print(f"[cyan]→ {source}[/cyan] ({len(days)} days)")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task(f"  scraping {source}", total=len(days))

        for d in days:
            scraper = scraper_cls()
            try:
                result = scraper.scrape(d)
                if result.success:
                    counts = _persist_result(result, d)
                    summary["gazettes_new"] += counts.get("gazettes_new", 0)
                    summary["news_new"] += counts.get("news_new", 0)
                    summary["articles_new"] += counts.get("articles_new", 0)
                else:
                    summary["errors"] += 1
                    logger.warning("%s %s failed: %s", source, d, result.error)
            except Exception as e:
                summary["errors"] += 1
                logger.error("%s %s crashed: %s", source, d, e)
            finally:
                scraper.close()
            progress.advance(task)
            # Be polite to government servers.
            time.sleep(0.5)

    total_new = (
        summary["gazettes_new"] + summary["news_new"] + summary["articles_new"]
    )
    console.print(
        f"  [green]✓[/green] {total_new} new "
        f"(gazettes={summary['gazettes_new']}, news={summary['news_new']}, "
        f"articles={summary['articles_new']}, errors={summary['errors']})"
    )
    return summary


@click.command()
@click.option(
    "--start-date",
    default="2026-01-01",
    help="ISO date (YYYY-MM-DD). Default: 2026-01-01.",
)
@click.option(
    "--end-date",
    default=None,
    help="ISO date (YYYY-MM-DD). Default: today.",
)
@click.option(
    "--sources",
    default=DEFAULT_SOURCES,
    help=f"Comma-separated source IDs. Default: {DEFAULT_SOURCES}",
)
@click.option("--skip-analyze", is_flag=True, help="Skip the LLM analyzer pass.")
@click.option("--skip-report", is_flag=True, help="Skip report regeneration.")
def main(start_date: str, end_date: str | None, sources: str, skip_analyze: bool, skip_report: bool):
    """Venezuela Investment Journal — historical backfill"""

    start = _parse_date(start_date)
    end = _parse_date(end_date) if end_date else date.today()

    if start > end:
        console.print(f"[red]start_date {start} is after end_date {end}[/red]")
        sys.exit(1)

    selected = [s.strip() for s in sources.split(",") if s.strip()]
    unknown = [s for s in selected if s not in PER_DAY_SCRAPERS and s not in ONE_SHOT_SCRAPERS]
    if unknown:
        console.print(f"[red]Unknown sources: {unknown}[/red]")
        console.print(f"Valid: {ALL_SOURCES}")
        sys.exit(1)

    console.print(
        Panel(
            f"[bold]Backfill[/bold] {start} → {end}\nSources: {', '.join(selected)}",
            style="blue",
        )
    )
    init_db()

    started = time.time()
    grand = {"gazettes_new": 0, "news_new": 0, "articles_new": 0}

    if "federal_register" in selected:
        s = _backfill_federal_register(start, end)
        grand["articles_new"] += s.get("articles_new", 0)

    for one_shot in ("ofac_sdn", "travel_advisory"):
        if one_shot in selected:
            s = _scrape_one_shot(one_shot, end)
            grand["articles_new"] += s.get("articles_new", 0)

    for per_day in PER_DAY_SCRAPERS:
        if per_day in selected:
            s = _backfill_per_day(per_day, start, end)
            grand["gazettes_new"] += s.get("gazettes_new", 0)
            grand["news_new"] += s.get("news_new", 0)
            grand["articles_new"] += s.get("articles_new", 0)

    console.print(
        f"\n[bold green]Scrape complete[/bold green] — "
        f"gazettes={grand['gazettes_new']}, news={grand['news_new']}, "
        f"articles={grand['articles_new']} "
        f"({time.time() - started:.1f}s)"
    )

    if not skip_analyze:
        console.print("\n[bold cyan]Analyzer[/bold cyan]")
        try:
            from src.analyzer import run_analysis
            result = run_analysis()
            console.print(f"  [green]✓[/green] {result}")
        except Exception as e:
            logger.error("Analyzer failed: %s", e, exc_info=True)
            console.print(f"  [red]✗[/red] {e}")

    if not skip_report:
        console.print("\n[bold cyan]Report generator[/bold cyan]")
        try:
            from src.report_generator import generate_report
            path = generate_report()
            console.print(f"  [green]✓[/green] Wrote {path}")
        except Exception as e:
            logger.error("Report failed: %s", e, exc_info=True)
            console.print(f"  [red]✗[/red] {e}")

    console.print(f"\n[dim]Total elapsed: {time.time() - started:.1f}s[/dim]")


if __name__ == "__main__":
    main()
