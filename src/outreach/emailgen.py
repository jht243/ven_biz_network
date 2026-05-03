"""Generate and send backlink outreach emails."""

from __future__ import annotations

import html
import logging
from datetime import datetime

from src.config import settings
from src.models import (
    EmailStatus,
    OutreachEmail,
    OutreachStatus,
    Prospect,
    SessionLocal,
    init_db,
)
from src.newsletter import send_email as send_provider_email

logger = logging.getLogger(__name__)


def _site_name(domain: str) -> str:
    name = (domain or "your site").removeprefix("www.")
    return name.split(".")[0].replace("-", " ").title() or "your site"


def _topic(prospect: Prospect | dict) -> str:
    if isinstance(prospect, dict):
        return prospect.get("source_page_topic") or prospect.get("link_opportunity") or prospect.get("category") or "Venezuela"
    return (prospect.source_page_topic or prospect.link_opportunity or prospect.category.value).replace("_", " ")


def _get(obj: Prospect | dict, name: str, default: str = "") -> str:
    if isinstance(obj, dict):
        return str(obj.get(name) or default)
    value = getattr(obj, name, default)
    return str(value or default)


def generate_email(prospect: Prospect | dict) -> dict[str, str]:
    """Generate the initial email and two follow-ups from the MVP template."""
    domain = _get(prospect, "domain", "the site")
    competitor = _get(prospect, "competitor_linked_to", "a Venezuela-related source")
    target_url = _get(prospect, "recommended_target_url", "https://www.caracasresearch.com")
    email_angle = _get(prospect, "email_angle", "updated Venezuela research reference")
    site_type = _get(prospect, "site_type", "site").replace("_", " ")
    topic = _topic(prospect)
    first_name_or_team = f"{_site_name(domain)} team"

    subject = f"Venezuela resource for {_site_name(domain)}"
    body = f"""Hi {first_name_or_team},

I found your page on {topic} and noticed you reference {competitor} / Venezuela-related resources.

I run Caracas Research, which publishes Venezuela-focused research across travel, investment, sanctions, and business risk.

Given your {site_type} audience, this may be a useful {email_angle} for your readers:
{target_url}

Would you consider adding it to the page?

Best,
Jonathan"""
    followup_1 = f"""Hi {first_name_or_team},

Just following up. I thought the Caracas Research page could be useful because your readers already look for information on {topic}.

Resource angle: {email_angle}
{target_url}

Best,
Jonathan"""
    followup_2 = f"""Hi {first_name_or_team},

Last note from me. If useful, Caracas Research can serve as an updated Venezuela reference for your readers:
{target_url}

No issue if it is not a fit.

Best,
Jonathan"""
    return {"subject": subject, "body": body, "followup_1": followup_1, "followup_2": followup_2}


def _plain_to_html(body: str) -> str:
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    return "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in paragraphs)


def upsert_email_sequence(db, prospect: Prospect) -> list[OutreachEmail]:
    """Persist generated initial/follow-up emails for a prospect."""
    generated = generate_email(prospect)
    rows: list[OutreachEmail] = []
    payloads = [
        (1, generated["subject"], generated["body"]),
        (2, f"Re: {generated['subject']}", generated["followup_1"]),
        (3, f"Re: {generated['subject']}", generated["followup_2"]),
    ]
    for sequence_num, subject, body in payloads:
        row = (
            db.query(OutreachEmail)
            .filter(
                OutreachEmail.prospect_id == prospect.id,
                OutreachEmail.sequence_num == sequence_num,
            )
            .one_or_none()
        )
        if row is None:
            row = OutreachEmail(
                prospect_id=prospect.id,
                sequence_num=sequence_num,
                subject=subject,
                body=body,
            )
            db.add(row)
        else:
            row.subject = subject
            row.body = body
        rows.append(row)
    return rows


def send_email(prospect_id: str, sequence_num: int = 1, *, dry_run: bool = False) -> bool:
    """Send one outreach email through Resend and update database status."""
    init_db()
    db = SessionLocal()
    try:
        prospect = db.query(Prospect).filter(Prospect.id == prospect_id).one_or_none()
        if prospect is None or not prospect.contact_email:
            return False
        email = (
            db.query(OutreachEmail)
            .filter(
                OutreachEmail.prospect_id == prospect_id,
                OutreachEmail.sequence_num == sequence_num,
            )
            .one_or_none()
        )
        if email is None:
            upsert_email_sequence(db, prospect)
            db.flush()
            email = (
                db.query(OutreachEmail)
                .filter(
                    OutreachEmail.prospect_id == prospect_id,
                    OutreachEmail.sequence_num == sequence_num,
                )
                .one()
            )

        result = send_provider_email(
            to=prospect.contact_email,
            subject=email.subject,
            html_body=_plain_to_html(email.body),
            provider_name="resend",
            dry_run=dry_run,
            from_override=settings.resend_outreach_from,
        )
        ok = bool(result.get("success"))
        if ok:
            email.sent_at = datetime.utcnow()
            prospect.email_status = EmailStatus.VERIFIED
            prospect.outreach_status = OutreachStatus.SENT
            db.commit()
        else:
            db.rollback()
        return ok
    except Exception as exc:
        db.rollback()
        logger.exception("Outreach send failed for %s: %s", prospect_id, exc)
        return False
    finally:
        db.close()

