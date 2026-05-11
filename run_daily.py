#!/usr/bin/env python3
"""
Daily orchestrator for the Caracas Research.

Chains: scrape -> analyze -> generate report -> Google Indexing API
-> send newsletter -> IndexNow / social / archive -> SEO audit -> sitemap sync

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
    """Caracas Research — Daily Pipeline"""

    console.print(Panel("[bold]Caracas Research — Daily Pipeline[/bold]", style="blue"))

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

    # Phase 2b: Long-form blog generation (capped budget; safe to fail)
    if not report_only:
        console.print("\n[bold cyan]Phase 2b:[/bold cyan] Writing long-form analysis posts...")
        try:
            from src.blog_generator import run_blog_generation
            blog_result = run_blog_generation()
            results["blog_generation"] = blog_result
            console.print(f"  [green]✓[/green] Blog generation: {blog_result}")
        except Exception as e:
            logger.error("Blog generation failed: %s", e, exc_info=True)
            results["blog_generation"] = {"error": str(e)}
            console.print(f"  [yellow]![/yellow] Blog generation failed (non-fatal): {e}")
    else:
        console.print("\n[dim]Phase 2b: Blog generation — SKIPPED (report-only)[/dim]")

    # Phase 2c: Press-Release Radar. Scans primary-source articles analyzed in
    # Phase 2 for original, reporter-worthy findings. Only fires if at least one
    # article scores >= 7/10 on press-release potential. Always non-fatal.
    if not report_only:
        console.print("\n[bold cyan]Phase 2c:[/bold cyan] Scanning for press-release candidates...")
        try:
            from src.press_radar import run_press_radar
            radar_result = run_press_radar(dry_run=dry_run)
            results["press_radar"] = radar_result
            scanned   = radar_result.get("scanned", 0)
            evaluated = radar_result.get("evaluated", 0)
            candidates = radar_result.get("candidates", 0)
            emailed   = radar_result.get("emailed", 0)
            if candidates:
                console.print(
                    f"  [green]✓[/green] Press radar: {candidates} candidate(s) found "
                    f"(scanned {scanned}, evaluated {evaluated}) — "
                    f"{'email sent' if emailed else 'dry-run / send skipped'}"
                )
            else:
                console.print(
                    f"  [dim]·[/dim] Press radar: no qualifying candidates "
                    f"(scanned {scanned}, evaluated {evaluated})"
                )
        except Exception as e:
            logger.error("Press radar failed: %s", e, exc_info=True)
            results["press_radar"] = {"error": str(e)}
            console.print(f"  [yellow]![/yellow] Press radar failed (non-fatal): {e}")
    else:
        console.print("\n[dim]Phase 2c: Press radar — SKIPPED (report-only)[/dim]")

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

    # Phase 3a: Google Indexing API — right after the report is written so
    # Google can re-crawl key URLs (homepage, SDN tracker, explainers) before
    # the newsletter and other channels. Non-fatal; see run_google_indexing.
    # (The distribution Phase 5 bundle only runs indexnow, bluesky, archive,
    # etc. — not a second Google ping.)
    console.print("\n[bold cyan]Phase 3a:[/bold cyan] Google Indexing API (URL notifications)...")
    try:
        from src.distribution.runner import run_google_indexing
        gidx = run_google_indexing()
        results["google_indexing"] = gidx
        if gidx.get("status") == "ok":
            console.print(
                f"  [green]✓[/green] pinged {gidx.get('pinged', 0)} "
                f"({gidx.get('succeeded', 0)} ok, {gidx.get('failed', 0)} failed)"
            )
        else:
            console.print(f"  [yellow]·[/yellow] {gidx}")
    except Exception as e:
        logger.error("Google Indexing API failed: %s", e, exc_info=True)
        results["google_indexing"] = {"error": str(e)}
        console.print(f"  [yellow]![/yellow] Google Indexing (non-fatal): {e}")

    # Phase 3b: Daily Tearsheet (PDF). Only runs on the evening cron
    # (5 PM Medellín / 22:00 UTC) — the morning cron skips it so we
    # publish exactly one tearsheet per day, reflecting the full
    # day's intelligence. Always non-fatal — if PDF generation or
    # upload fails the rest of the pipeline must continue.
    from src.distribution.tearsheet import (
        publish_daily_tearsheet,
        should_publish_today,
    )
    if should_publish_today():
        console.print("\n[bold cyan]Phase 3b:[/bold cyan] Generating daily tearsheet PDF...")
        try:
            tearsheet_result = publish_daily_tearsheet()
            results["tearsheet"] = tearsheet_result
            if tearsheet_result.get("status") == "ok":
                console.print(
                    f"  [green]✓[/green] Tearsheet: {tearsheet_result.get('size_bytes')} bytes "
                    f"→ {tearsheet_result.get('latest_url')}"
                )
            elif tearsheet_result.get("status") == "skipped":
                console.print(f"  [yellow]·[/yellow] Tearsheet skipped: {tearsheet_result.get('reason')}")
            else:
                console.print(f"  [yellow]![/yellow] Tearsheet error: {tearsheet_result}")
        except Exception as e:
            logger.error("Tearsheet generation failed: %s", e, exc_info=True)
            results["tearsheet"] = {"error": str(e)}
            console.print(f"  [yellow]![/yellow] Tearsheet generation failed (non-fatal): {e}")
    else:
        console.print("\n[dim]Phase 3b: Tearsheet — SKIPPED (morning cron; publishes on evening cron only)[/dim]")
        results["tearsheet"] = {"status": "skipped", "reason": "not the evening cron"}

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

    # Phase 5: Distribution (IndexNow, Bluesky, archive, Zenodo, OSF).
    # Google URL_UPDATED is Phase 3a (immediately after the report) so
    # crawlers are not delayed behind later phases.
    console.print("\n[bold cyan]Phase 5:[/bold cyan] Distributing to discovery channels...")
    try:
        from src.distribution.runner import run_all as run_distribution_all
        dist_result = run_distribution_all()
        results["distribution"] = dist_result
        for channel, summary in dist_result.items():
            console.print(f"  [green]✓[/green] {channel}: {summary}")
    except Exception as e:
        logger.error("Distribution failed: %s", e, exc_info=True)
        results["distribution"] = {"error": str(e)}
        console.print(f"  [yellow]![/yellow] Distribution failed (non-fatal): {e}")

    # Phase 6: SEO audit. Runs last so distribution is never delayed.
    # Crawls the local Flask app via test_client and checks meta tags,
    # structured data, headings, cluster-nav coverage, internal linking,
    # and sitemap alignment. Always non-fatal.
    if not report_only:
        console.print("\n[bold cyan]Phase 6:[/bold cyan] Running SEO audit...")
        try:
            from src.seo.audit import run_audit
            seo_report = run_audit(max_pages=200)
            results["seo_audit"] = {
                "pages_crawled": seo_report.pages_crawled,
                "errors": len(seo_report.errors),
                "warnings": len(seo_report.warnings),
                "warning_details": seo_report.warnings,
                "error_details": seo_report.errors,
            }
            if seo_report.errors:
                console.print(f"  [yellow]![/yellow] SEO audit: {seo_report.pages_crawled} pages, {len(seo_report.errors)} errors, {len(seo_report.warnings)} warnings")
                for f in seo_report.errors:
                    console.print(f"        [red]error:[/red] {f}")
            else:
                console.print(f"  [green]✓[/green] SEO audit: {seo_report.pages_crawled} pages, 0 errors, {len(seo_report.warnings)} warnings")
            for w in seo_report.warnings:
                console.print(f"        [dim]warn:[/dim] {w}")

            # Phase 6b: auto-fix SEO issues on LandingPage-backed pages.
            # Covers: missing H1, thin content, title too long/short,
            # missing meta description, heading hierarchy skips, and low
            # inbound links. Uses web search + premium LLM. Capped at
            # 5 fixes/run. Issues not auto-fixable: missing canonical/
            # og:image (template-derived), missing JSON-LD (always
            # generated), cluster nav (requires template changes).
            console.print("\n[bold cyan]Phase 6b:[/bold cyan] Auto-fixing SEO content issues...")
            try:
                from src.seo.content_fixer import fix_content_issues
                fix_result = fix_content_issues(seo_report)
                results["seo_autofix"] = fix_result
                if fix_result.get("fixed", 0) > 0:
                    console.print(
                        f"  [green]✓[/green] SEO auto-fix: {fix_result['fixed']} pages fixed, "
                        f"${fix_result.get('total_cost_usd', 0):.3f} LLM cost"
                    )
                    for d in fix_result.get("details", []):
                        console.print(f"        {d['fix']}: {d['path']}")
                else:
                    reason = fix_result.get("reason", fix_result.get("status", ""))
                    console.print(f"  [dim]·[/dim] SEO auto-fix: nothing to fix ({reason})")
            except Exception as e:
                logger.error("SEO auto-fix failed: %s", e, exc_info=True)
                results["seo_autofix"] = {"error": str(e)}
                console.print(f"  [yellow]![/yellow] SEO auto-fix failed (non-fatal): {e}")

        except Exception as e:
            logger.error("SEO audit failed: %s", e, exc_info=True)
            results["seo_audit"] = {"error": str(e)}
            console.print(f"  [yellow]![/yellow] SEO audit failed (non-fatal): {e}")

    # Phase 7: Sitemap sync — fetch the live sitemap, diff against
    # declared routes, spot-check for dead links, auto-patch and push
    # if anything is missing. Always non-fatal.
    if not report_only:
        console.print("\n[bold cyan]Phase 7:[/bold cyan] Sitemap audit & sync...")
        try:
            from scripts.sync_sitemap import run_sync
            sync_result = run_sync(dry_run=dry_run)
            results["sitemap_sync"] = sync_result
            missing = sync_result.get("missing_routes", [])
            dead = sync_result.get("dead_urls", [])
            patched = sync_result.get("patched", 0)
            pushed = sync_result.get("pushed", False)

            parts = [f"{sync_result.get('live_urls', 0)} live URLs"]
            if missing:
                parts.append(f"{len(missing)} missing route(s)")
            if dead:
                parts.append(f"{len(dead)} dead link(s)")
            if patched:
                parts.append(f"{patched} auto-added" + (" & pushed" if pushed else " (push failed)"))

            if dead:
                console.print(f"  [yellow]![/yellow] Sitemap sync: {', '.join(parts)}")
                for d in dead:
                    console.print(f"        [dim]dead:[/dim] {d}")
            elif missing:
                console.print(f"  [green]✓[/green] Sitemap sync: {', '.join(parts)}")
            else:
                console.print(f"  [green]✓[/green] Sitemap sync: {', '.join(parts)} — up to date")
        except Exception as e:
            logger.error("Sitemap sync failed: %s", e, exc_info=True)
            results["sitemap_sync"] = {"error": str(e)}
            console.print(f"  [yellow]![/yellow] Sitemap sync failed (non-fatal): {e}")
    else:
        console.print("\n[dim]Phase 7: Sitemap sync — SKIPPED (report-only)[/dim]")

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
