#!/usr/bin/env python3
"""
CLI entry point for the SEO audit engine.

Usage:
    python scripts/seo_audit.py                     # Full audit, human-readable
    python scripts/seo_audit.py --no-follow          # Seed pages only
    python scripts/seo_audit.py --json               # Machine-readable JSON
    python scripts/seo_audit.py --verbose            # Include info-level findings
    python scripts/seo_audit.py --fail-on-error      # Exit 1 on any errors (CI gate)
    python scripts/seo_audit.py --max-pages 50       # Limit crawl size
"""

from __future__ import annotations

import json as json_mod
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Ensure the project root is on sys.path so `from src.…` works when
# invoked as `python scripts/seo_audit.py` from the repo root.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

console = Console()


@click.command()
@click.option("--json", "json_output", is_flag=True, help="Machine-readable JSON output")
@click.option("--verbose", is_flag=True, help="Show info-level findings (not just errors/warnings)")
@click.option("--fail-on-error", is_flag=True, help="Exit code 1 if any errors found (CI gating)")
@click.option("--max-pages", default=200, type=int, help="Max pages to crawl")
@click.option("--no-follow", is_flag=True, help="Only crawl seed pages, don't follow internal links")
def main(
    json_output: bool,
    verbose: bool,
    fail_on_error: bool,
    max_pages: int,
    no_follow: bool,
):
    """Caracas Research — SEO Audit"""
    from src.seo.audit import run_audit

    if not json_output:
        console.print(Panel("[bold]Caracas Research — SEO Audit[/bold]", style="blue"))
        console.print(f"  Max pages: {max_pages}, follow links: {not no_follow}\n")

    report = run_audit(
        max_pages=max_pages,
        follow_links=not no_follow,
    )

    if json_output:
        click.echo(json_mod.dumps(report.to_dict(), indent=2))
    else:
        _print_report(report, verbose=verbose)

    if fail_on_error and report.errors:
        sys.exit(1)


def _print_report(report, *, verbose: bool = False):
    # Summary panel
    summary_table = Table(title="Audit Summary", show_header=False)
    summary_table.add_column("Metric", style="bold")
    summary_table.add_column("Value")
    summary_table.add_row("Pages crawled", str(report.pages_crawled))
    summary_table.add_row("Pages clean", str(report.pages_ok))
    summary_table.add_row("Errors", f"[red]{len(report.errors)}[/red]" if report.errors else "[green]0[/green]")
    summary_table.add_row("Warnings", f"[yellow]{len(report.warnings)}[/yellow]" if report.warnings else "[green]0[/green]")
    summary_table.add_row("Info", str(len(report.info)))
    console.print(summary_table)

    # Errors
    if report.errors:
        console.print("\n[bold red]Errors:[/bold red]")
        err_table = Table(show_header=True)
        err_table.add_column("Path", style="cyan", max_width=50)
        err_table.add_column("Category", style="bold")
        err_table.add_column("Message")
        for f in report.errors:
            err_table.add_row(f.path, f.category, f.message)
        console.print(err_table)

    # Warnings
    if report.warnings:
        console.print("\n[bold yellow]Warnings:[/bold yellow]")
        warn_table = Table(show_header=True)
        warn_table.add_column("Path", style="cyan", max_width=50)
        warn_table.add_column("Category", style="bold")
        warn_table.add_column("Message")
        for f in report.warnings:
            warn_table.add_row(f.path, f.category, f.message)
        console.print(warn_table)

    # Info (only in verbose mode)
    if verbose and report.info:
        console.print("\n[bold]Info:[/bold]")
        info_table = Table(show_header=True)
        info_table.add_column("Path", style="cyan", max_width=50)
        info_table.add_column("Category", style="bold")
        info_table.add_column("Message")
        for f in report.info:
            info_table.add_row(f.path, f.category, f.message)
        console.print(info_table)


if __name__ == "__main__":
    main()
