"""Backlink monitoring for outreach prospects."""

from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from src.models import (
    BacklinkRecord,
    BacklinkStatus,
    OutreachStatus,
    Prospect,
    SessionLocal,
    init_db,
)

logger = logging.getLogger(__name__)


def check_backlink(source_url: str, target_domain: str = "caracasresearch.com") -> dict:
    """Return backlink details if a source page links to target_domain."""
    try:
        resp = httpx.get(
            source_url,
            follow_redirects=True,
            timeout=20,
            headers={"User-Agent": "CaracasResearchBot/1.0 (+https://www.caracasresearch.com)"},
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("Backlink check failed for %s: %s", source_url, exc)
        return {"found": False, "target_url": "", "anchor_text": "", "rel": "", "error": str(exc)}

    soup = BeautifulSoup(resp.text, "lxml")
    target_domain = target_domain.lower().removeprefix("www.")
    for a in soup.find_all("a", href=True):
        href = urljoin(str(resp.url), a.get("href", ""))
        host = urlparse(href).netloc.lower().removeprefix("www.")
        if host == target_domain or host.endswith("." + target_domain):
            return {
                "found": True,
                "target_url": href,
                "anchor_text": " ".join(a.get_text(" ").split()),
                "rel": " ".join(a.get("rel", [])),
                "error": "",
            }
    return {"found": False, "target_url": "", "anchor_text": "", "rel": "", "error": ""}


def run_weekly_check(target_domain: str = "caracasresearch.com") -> dict:
    """Check sent/replied prospects and mark conversions when links appear."""
    init_db()
    db = SessionLocal()
    summary = {"checked": 0, "converted": 0, "errors": 0}
    try:
        prospects = (
            db.query(Prospect)
            .filter(Prospect.outreach_status.in_([OutreachStatus.SENT, OutreachStatus.REPLIED]))
            .all()
        )
        for prospect in prospects:
            result = check_backlink(prospect.source_url, target_domain=target_domain)
            summary["checked"] += 1
            record = (
                db.query(BacklinkRecord)
                .filter(BacklinkRecord.prospect_id == prospect.id)
                .one_or_none()
            )
            if record is None:
                record = BacklinkRecord(prospect_id=prospect.id, source_url=prospect.source_url)
                db.add(record)
            record.last_checked_at = datetime.utcnow()
            if result.get("found"):
                record.status = BacklinkStatus.ACTIVE
                record.target_url = result.get("target_url")
                record.anchor_text = result.get("anchor_text")
                record.rel = result.get("rel")
                if record.first_seen is None:
                    record.first_seen = datetime.utcnow()
                prospect.outreach_status = OutreachStatus.CONVERTED
                summary["converted"] += 1
            else:
                record.status = BacklinkStatus.NOT_FOUND
                if result.get("error"):
                    summary["errors"] += 1
        db.commit()
        return summary
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

