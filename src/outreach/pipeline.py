"""Backlink outreach pipeline orchestration."""

from __future__ import annotations

import csv
import io
import logging
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import or_

from src.config import settings
from src.models import (
    EmailStatus,
    OutreachEmail,
    OutreachStatus,
    Prospect,
    ProspectCategory,
    SessionLocal,
    init_db,
)
from src.outreach.classifier import choose_target_url, classify_prospect
from src.outreach.contacts import find_contact_email
from src.outreach.crawler import crawl_source_page
from src.outreach.emailgen import send_email, upsert_email_sequence
from src.outreach.linkcheck import run_weekly_check
from src.outreach.scorer import score_prospect
from src.seo.semrush import SemrushClient

logger = logging.getLogger(__name__)

DEFAULT_COMPETITORS = [
    # Legacy news/analysis competitors
    "venezuelaanalysis.com",
    "caracaschronicles.com",
    "venezuelanews.com",
    "latinnews.com",
    "americasquarterly.org",
    "thedialogue.org",
    "csis.org",
    # Compliance / sanctions
    "controlrisks.com",
    "globalcompliancenews.com",
    "sanctions.io",
    # Travel
    "worldnomads.com",
    "travel.state.gov",
    # 2026 entrants: investment-focused competitors
    "invest.com.ve",
    "latamfdi.com",
    "guacamayave.com",
    "ecosistemag.com",
    "buildsandbuys.com",
    "expatlife.ai",
    "latinamericainvestments.com",
]


def _row_get(row: dict, *names: str) -> str:
    lowered = {str(k).lower().replace(" ", "_"): v for k, v in row.items()}
    for name in names:
        key = name.lower().replace(" ", "_")
        if key in lowered and lowered[key] is not None:
            return str(lowered[key])
    return ""


def _normalize_domain(value: str) -> str:
    raw = (value or "").strip()
    if raw.startswith("http"):
        raw = urlparse(raw).netloc
    return raw.lower().removeprefix("www.").strip("/")


def _domain_from_url(url: str) -> str:
    return _normalize_domain(urlparse(url).netloc)


def get_competitor_backlinks(competitor_domain: str, *, limit: int = 500) -> list[dict]:
    client = SemrushClient()
    rows = client.get_backlinks(competitor_domain, limit=limit)
    for row in rows:
        row["_competitor"] = competitor_domain
    return rows


def get_referring_domains(competitor_domain: str, *, limit: int = 500) -> list[dict]:
    return SemrushClient().get_referring_domains(competitor_domain, limit=limit)


def get_caracasresearch_backlinks(*, limit: int = 500) -> list[dict]:
    return SemrushClient().get_referring_domains("caracasresearch.com", limit=limit)


def dedupe_domains(source_domains: set[str], existing_linkers: set[str]) -> set[str]:
    return {
        domain
        for domain in source_domains
        if domain and domain not in existing_linkers and not domain.endswith("caracasresearch.com")
    }


def pull_backlink_prospects(
    competitors: list[str] | None = None,
    *,
    limit_per_competitor: int = 500,
) -> dict:
    """Import Semrush competitor backlinks into outreach_prospects."""
    init_db()
    competitors = competitors or DEFAULT_COMPETITORS
    client = SemrushClient()
    db = SessionLocal()
    summary = {"competitors": len(competitors), "backlinks": 0, "created": 0, "updated": 0, "skipped": 0}
    try:
        caracas_refdomains = client.get_referring_domains("caracasresearch.com", limit=limit_per_competitor)
        existing_linkers = {
            _normalize_domain(_row_get(row, "Domain", "domain", "Referring Domain"))
            for row in caracas_refdomains
        }

        all_rows: list[dict] = []
        for competitor in competitors:
            logger.info("Pulling Semrush backlinks for %s", competitor)
            try:
                rows = client.get_backlinks(competitor, limit=limit_per_competitor)
            except Exception as exc:
                logger.warning("Backlinks failed for %s: %s", competitor, exc)
                continue
            for row in rows:
                row["_competitor"] = competitor
            all_rows.extend(rows)
            summary["backlinks"] += len(rows)

        source_domains = {_domain_from_url(_row_get(row, "source_url", "Source URL")) for row in all_rows}
        prospect_domains = dedupe_domains(source_domains, existing_linkers)
        competitor_counts: dict[str, set[str]] = defaultdict(set)
        best_by_domain: dict[str, dict] = {}
        for row in all_rows:
            source_url = _row_get(row, "source_url", "Source URL")
            source_domain = _domain_from_url(source_url)
            if source_domain not in prospect_domains:
                summary["skipped"] += 1
                continue
            competitor_counts[source_domain].add(row["_competitor"])
            best_by_domain.setdefault(source_domain, row)

        existing = {
            (p.domain, p.source_url): p
            for p in db.query(Prospect)
            .filter(Prospect.source_url.in_([
                _row_get(row, "source_url", "Source URL")
                for row in best_by_domain.values()
            ]))
            .all()
        }

        for domain, row in best_by_domain.items():
            source_url = _row_get(row, "source_url", "Source URL")
            prospect = existing.get((domain, source_url))
            if prospect is None:
                prospect = Prospect(domain=domain, source_url=source_url)
                db.add(prospect)
                summary["created"] += 1
            else:
                summary["updated"] += 1

            prospect.competitor_linked_to = row.get("_competitor")
            prospect.competitor_target_url = _row_get(row, "target_url", "Target URL")
            prospect.anchor_text = _row_get(row, "anchor", "Anchor")
            prospect.source_page_title = _row_get(row, "source_title", "Source Title")
            prospect.competitor_count = len(competitor_counts[domain])
            raw_ascore = _row_get(row, "page_ascore", "page_score")
            if raw_ascore:
                try:
                    prospect.authority_score = int(float(raw_ascore))
                except ValueError:
                    prospect.authority_score = None

        db.commit()
        return summary
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def process_prospects(
    *,
    limit: int | None = None,
    unprocessed_only: bool = False,
    reprocess_scraped: bool = False,
) -> dict:
    """Crawl, classify, score, and find contact emails for imported prospects."""
    if unprocessed_only and reprocess_scraped:
        raise ValueError("Use only one of unprocessed_only or reprocess_scraped")
    init_db()
    client = SemrushClient()
    db = SessionLocal()
    summary = {
        "processed": 0,
        "qualified": 0,
        "contact_lookup_hit": 0,
        "contact_new": 0,
        "contact_updated": 0,
    }
    try:
        query = db.query(Prospect).order_by(Prospect.created_at.asc())
        if unprocessed_only:
            query = query.filter(
                or_(
                    Prospect.page_text_snippet.is_(None),
                    Prospect.page_text_snippet == "",
                )
            )
        if reprocess_scraped:
            query = query.filter(Prospect.page_text_snippet.isnot(None))
        if limit:
            query = query.limit(limit)
        prospects = query.all()
        for prospect in prospects:
            crawl = crawl_source_page(prospect.source_url)
            classification = (
                {
                    "category": "reject",
                    "site_type": "unreachable" if crawl.get("error") else "spam_reject",
                    "link_opportunity": "reject",
                    "email_template_key": "none",
                    "email_angle": "no outreach",
                    "reason_to_link": "Page could not be crawled",
                    "source_page_topic": "",
                    "is_resource_page": False,
                    "reject_reason": crawl.get("error") or "Page could not be crawled",
                }
                if crawl.get("hard_reject")
                else classify_prospect(crawl.get("text", ""), prospect.source_url, prospect.competitor_linked_to or "")
            )
            link_opportunity = classification.get("link_opportunity") or classification.get("category", "reject")
            prospect.category = ProspectCategory(link_opportunity)
            prospect.site_type = classification.get("site_type") or "other"
            prospect.link_opportunity = link_opportunity
            prospect.email_template_key = classification.get("email_template_key") or "none"
            prospect.email_angle = classification.get("email_angle") or ""
            prospect.reject_reason = classification.get("reject_reason")
            prospect.source_page_topic = classification.get("source_page_topic") or ""
            prospect.is_resource_page = bool(classification.get("is_resource_page"))
            prospect.site_language = crawl.get("language") or "en"
            prospect.source_page_title = crawl.get("title") or prospect.source_page_title
            prospect.reason_to_link = classification.get("reason_to_link") or ""
            prospect.recommended_target_url = choose_target_url(link_opportunity, crawl.get("text", ""))
            prospect.page_text_snippet = (crawl.get("text") or "")[:2000]

            # RULE: ALWAYS attempt contact discovery regardless of crawl outcome.
            prior_email = prospect.contact_email
            contact_email = find_contact_email(prospect.domain, source_url=prospect.source_url)
            if contact_email:
                summary["contact_lookup_hit"] += 1
                if not prior_email:
                    summary["contact_new"] += 1
                elif prior_email != contact_email:
                    summary["contact_updated"] += 1
                prospect.contact_email = contact_email
                prospect.email_status = EmailStatus.FOUND

            if prospect.contact_email and link_opportunity == "reject":
                link_opportunity = "general_venezuela"
                prospect.category = ProspectCategory(link_opportunity)
                prospect.link_opportunity = link_opportunity
                template_key, email_angle = "general_research_reference", "updated Venezuela research reference"
                prospect.email_template_key = template_key
                prospect.email_angle = email_angle
                prospect.reject_reason = None
                prospect.recommended_target_url = choose_target_url(link_opportunity, crawl.get("text", ""))

            authority = prospect.authority_score
            if authority is None:
                authority = client.get_domain_authority(prospect.domain) or 0
                prospect.authority_score = authority
            score = score_prospect({
                "domain": prospect.domain,
                "source_url": prospect.source_url,
                "link_opportunity": link_opportunity,
                "page_text": crawl.get("text") or "",
                "links": crawl.get("links") or [],
                "anchor_text": prospect.anchor_text or "",
                "authority_score": authority,
                "contact_email": prospect.contact_email,
                "is_resource_page": prospect.is_resource_page,
                "competitor_count": prospect.competitor_count or 1,
            })
            prospect.score = score
            summary["processed"] += 1
            if score >= settings.outreach_min_score:
                summary["qualified"] += 1
            db.commit()
            time.sleep(0.2)
        return summary
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def generate_pending_emails(*, limit: int | None = None) -> dict:
    """Generate outreach sequences for qualified prospects with contacts."""
    init_db()
    db = SessionLocal()
    summary = {"prospects": 0, "emails": 0}
    try:
        query = (
            db.query(Prospect)
            .filter(Prospect.contact_email.isnot(None))
            .order_by(Prospect.score.desc())
        )
        if limit:
            query = query.limit(limit)
        for prospect in query.all():
            rows = upsert_email_sequence(db, prospect)
            prospect.outreach_status = OutreachStatus.QUEUED
            summary["prospects"] += 1
            summary["emails"] += len(rows)
        db.commit()
        return summary
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _daily_limit() -> int:
    """Calculate today's send limit based on warmup schedule."""
    base = settings.outreach_daily_limit or 5
    start = settings.outreach_start_date
    if not start:
        return base
    try:
        start_date = datetime.strptime(start, "%Y-%m-%d").date()
    except ValueError:
        return base
    days_active = (date.today() - start_date).days
    if days_active < 7:
        return base  # Week 1: 5/day
    elif days_active < 14:
        return min(15, base * 3)  # Week 2: 15/day
    elif days_active < 21:
        return min(30, base * 6)  # Week 3: 30/day
    else:
        return min(50, base * 10)  # Week 4+: 50/day


def _sent_today_count(db) -> int:
    """Count outreach emails sent today."""
    today_start = datetime.combine(date.today(), datetime.min.time())
    return (
        db.query(OutreachEmail)
        .filter(OutreachEmail.sent_at >= today_start)
        .count()
    )


def send_pending_emails(
    *,
    limit: int | None = None,
    dry_run: bool = False,
    ignore_daily_limit: bool = False,
) -> dict:
    """Send initial emails for queued qualified prospects."""
    init_db()
    db = SessionLocal()
    try:
        daily_cap = _daily_limit()
        already_sent = _sent_today_count(db)
        if ignore_daily_limit:
            if limit is None:
                raise ValueError("limit is required when ignore_daily_limit is true")
            effective_limit = limit
        else:
            remaining = max(0, daily_cap - already_sent)
            if remaining == 0 and not dry_run:
                logger.info("Daily send limit reached (%d/%d). Skipping.", already_sent, daily_cap)
                return {"attempted": 0, "sent": 0, "failed": 0, "daily_limit": daily_cap, "sent_today": already_sent}

            effective_limit = min(remaining, limit) if limit else remaining
        query = (
            db.query(Prospect)
            .filter(Prospect.outreach_status == OutreachStatus.QUEUED)
            .filter(Prospect.contact_email.isnot(None))
            .order_by(Prospect.score.desc())
            .limit(effective_limit)
        )
        prospect_ids = [p.id for p in query.all()]
    finally:
        db.close()

    summary = {
        "attempted": 0,
        "sent": 0,
        "failed": 0,
        "daily_limit": daily_cap,
        "sent_today": already_sent,
        "ignore_daily_limit": ignore_daily_limit,
    }
    for prospect_id in prospect_ids:
        summary["attempted"] += 1
        ok = send_email(prospect_id, 1, dry_run=dry_run)
        summary["sent" if ok else "failed"] += 1
        summary["sent_today"] += 1 if ok else 0
        if not dry_run:
            time.sleep(max(0, settings.outreach_delay_seconds))
    return summary


def export_prospects(path: str | Path | None = None) -> str:
    """Export all prospects/statuses as CSV. Returns CSV text."""
    init_db()
    db = SessionLocal()
    output = io.StringIO()
    fields = [
        "id",
        "domain",
        "source_url",
        "competitor_linked_to",
        "competitor_target_url",
        "anchor_text",
        "source_page_title",
        "category",
        "site_type",
        "link_opportunity",
        "email_angle",
        "email_template_key",
        "reject_reason",
        "source_page_topic",
        "is_resource_page",
        "site_language",
        "authority_score",
        "competitor_count",
        "score",
        "recommended_target_url",
        "reason_to_link",
        "contact_email",
        "email_status",
        "outreach_status",
        "created_at",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    try:
        for p in db.query(Prospect).order_by(Prospect.score.desc(), Prospect.created_at.desc()).all():
            writer.writerow({
                "id": p.id,
                "domain": p.domain,
                "source_url": p.source_url,
                "competitor_linked_to": p.competitor_linked_to,
                "competitor_target_url": p.competitor_target_url,
                "anchor_text": p.anchor_text,
                "source_page_title": p.source_page_title,
                "category": p.category.value if p.category else "",
                "site_type": p.site_type,
                "link_opportunity": p.link_opportunity,
                "email_angle": p.email_angle,
                "email_template_key": p.email_template_key,
                "reject_reason": p.reject_reason,
                "source_page_topic": p.source_page_topic,
                "is_resource_page": p.is_resource_page,
                "site_language": p.site_language,
                "authority_score": p.authority_score,
                "competitor_count": p.competitor_count,
                "score": p.score,
                "recommended_target_url": p.recommended_target_url,
                "reason_to_link": p.reason_to_link,
                "contact_email": p.contact_email,
                "email_status": p.email_status.value if p.email_status else "",
                "outreach_status": p.outreach_status.value if p.outreach_status else "",
                "created_at": p.created_at.isoformat() if p.created_at else "",
            })
    finally:
        db.close()
    csv_text = output.getvalue()
    if path:
        Path(path).write_text(csv_text)
    return csv_text


def run_outreach_pipeline(
    competitors: list[str] | None = None,
    *,
    primary_domain: str = "caracasresearch.com",
    limit_per_competitor: int = 500,
    process_limit: int | None = None,
    send: bool = False,
    dry_run: bool = False,
) -> dict:
    """Run the simplest end-to-end MVP pipeline."""
    summary = {
        "pull": pull_backlink_prospects(competitors, limit_per_competitor=limit_per_competitor),
        "process": process_prospects(limit=process_limit),
        "email": generate_pending_emails(),
    }
    if send:
        summary["send"] = send_pending_emails(limit=settings.outreach_batch_size, dry_run=dry_run)
    summary["target_domain"] = primary_domain
    return summary


__all__ = [
    "DEFAULT_COMPETITORS",
    "dedupe_domains",
    "export_prospects",
    "generate_pending_emails",
    "get_caracasresearch_backlinks",
    "get_competitor_backlinks",
    "get_referring_domains",
    "process_prospects",
    "pull_backlink_prospects",
    "run_outreach_pipeline",
    "run_weekly_check",
    "send_pending_emails",
]

