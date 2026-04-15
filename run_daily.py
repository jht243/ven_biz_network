#!/usr/bin/env python3
"""
Daily orchestrator for the Venezuelan Business Network.

Chains: scrape -> analyze -> generate report -> send newsletter

Usage:
    python run_daily.py                    # Full pipeline
    python run_daily.py --skip-scrape      # Skip scraping, use existing DB data
    python run_daily.py --skip-email       # Generate report but don't send emails
    python run_daily.py --dry-run          # Full pipeline but no actual sends
    python run_daily.py --report-only      # Only generate the HTML report
"""

from __future__ import annotations

import logging
import sys
import time

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.config import settings

console = Console()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s  %(name)-30s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_daily")


@click.command()
@click.option("--skip-scrape", is_flag=True, help="Skip the scraping phase")
@click.option("--skip-email", is_flag=True, help="Skip newsletter distribution")
@click.option("--dry-run", is_flag=True, help="Run everything but don't send real emails")
@click.option("--report-only", is_flag=True, help="Only generate the report, skip scrape and email")
def main(skip_scrape: bool, skip_email: bool, dry_run: bool, report_only: bool):
    """Venezuelan Business Network — Daily Pipeline"""

    console.print(Panel("[bold]Venezuelan Business Network — Daily Pipeline[/bold]", style="blue"))

    results = {}
    start = time.time()

    if report_only:
        skip_scrape = True
        skip_email = True

    # Phase 1: Scrape
    if not skip_scrape:
        console.print("\n[bold cyan]Phase 1:[/bold cyan] Scraping sources...")
        try:
            from src.pipeline import run_daily_scrape
            scrape_result = run_daily_scrape()
            results["scrape"] = scrape_result
            console.print(f"  [green]✓[/green] Scraping complete: {scrape_result}")
        except Exception as e:
            logger.error("Scraping failed: %s", e, exc_info=True)
            results["scrape"] = {"error": str(e)}
            console.print(f"  [red]✗[/red] Scraping failed: {e}")
    else:
        console.print("\n[dim]Phase 1: Scraping — SKIPPED[/dim]")

    # Phase 2: LLM Analysis
    if not report_only:
        console.print("\n[bold cyan]Phase 2:[/bold cyan] Running LLM analysis...")
        try:
            from src.analyzer import run_analysis
            analysis_result = run_analysis()
            results["analysis"] = analysis_result
            console.print(f"  [green]✓[/green] Analysis complete: {analysis_result}")
        except Exception as e:
            logger.error("Analysis failed: %s", e, exc_info=True)
            results["analysis"] = {"error": str(e)}
            console.print(f"  [red]✗[/red] Analysis failed: {e}")
    else:
        console.print("\n[dim]Phase 2: Analysis — SKIPPED (report-only)[/dim]")

    # Phase 3: Generate Report
    console.print("\n[bold cyan]Phase 3:[/bold cyan] Generating report...")
    try:
        from src.report_generator import generate_report
        report_path = generate_report()
        results["report"] = {"path": str(report_path)}
        console.print(f"  [green]✓[/green] Report generated: {report_path}")
    except Exception as e:
        logger.error("Report generation failed: %s", e, exc_info=True)
        results["report"] = {"error": str(e)}
        console.print(f"  [red]✗[/red] Report generation failed: {e}")
        if skip_email:
            _print_summary(results, start)
            sys.exit(1)

    # Phase 4: Newsletter
    if not skip_email:
        console.print("\n[bold cyan]Phase 4:[/bold cyan] Sending newsletter...")
        try:
            from src.newsletter import send_newsletter
            report_html = report_path.read_text(encoding="utf-8")
            email_result = send_newsletter(report_html, dry_run=dry_run)
            results["newsletter"] = email_result
            console.print(f"  [green]✓[/green] Newsletter: {email_result}")
        except Exception as e:
            logger.error("Newsletter failed: %s", e, exc_info=True)
            results["newsletter"] = {"error": str(e)}
            console.print(f"  [red]✗[/red] Newsletter failed: {e}")
    else:
        console.print("\n[dim]Phase 4: Newsletter — SKIPPED[/dim]")

    _print_summary(results, start)


def _print_summary(results: dict, start: float):
    elapsed = time.time() - start

    table = Table(title="Pipeline Summary")
    table.add_column("Phase", style="bold")
    table.add_column("Result")

    for phase, result in results.items():
        if isinstance(result, dict) and "error" in result:
            table.add_row(phase.title(), f"[red]Error: {result['error'][:80]}[/red]")
        else:
            table.add_row(phase.title(), f"[green]{result}[/green]")

    table.add_row("Duration", f"{elapsed:.1f}s")
    console.print("\n")
    console.print(table)


if __name__ == "__main__":
    main()
