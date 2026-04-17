"""
Newsletter distribution module.

Provider-agnostic email sender. Supports:
  - console: prints to stdout (development)
  - sendgrid: sends via SendGrid API (production)

Add new providers by subclassing EmailProvider and registering in PROVIDERS.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path

import httpx

from src.config import settings

logger = logging.getLogger(__name__)


class EmailProvider(ABC):
    @abstractmethod
    def send(self, to: str, subject: str, html_body: str) -> bool:
        ...


class ConsoleProvider(EmailProvider):
    def send(self, to: str, subject: str, html_body: str) -> bool:
        logger.info("=== EMAIL PREVIEW ===")
        logger.info("To: %s", to)
        logger.info("Subject: %s", subject)
        logger.info("Body length: %d chars", len(html_body))
        logger.info("First 200 chars: %s", html_body[:200])
        logger.info("=== END PREVIEW ===")
        return True


class SendGridProvider(EmailProvider):
    API_URL = "https://api.sendgrid.com/v3/mail/send"

    def __init__(self, api_key: str, from_email: str):
        self.api_key = api_key
        self.from_email = from_email

    def send(self, to: str, subject: str, html_body: str) -> bool:
        payload = {
            "personalizations": [{"to": [{"email": to}]}],
            "from": {"email": self.from_email, "name": "Caracas Research"},
            "subject": subject,
            "content": [{"type": "text/html", "value": html_body}],
        }

        resp = httpx.post(
            self.API_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )

        if resp.status_code in (200, 201, 202):
            logger.info("Email sent to %s via SendGrid", to)
            return True
        else:
            logger.error("SendGrid error %d: %s", resp.status_code, resp.text)
            return False


PROVIDERS = {
    "console": lambda: ConsoleProvider(),
    "sendgrid": lambda: SendGridProvider(settings.newsletter_api_key, settings.newsletter_from_email),
}


def _load_subscribers() -> list[str]:
    path = Path(settings.subscriber_list_path)
    if not path.exists():
        logger.warning("Subscriber list not found at %s", path)
        return []
    data = json.loads(path.read_text())
    if isinstance(data, list):
        return data
    return data.get("subscribers", [])


def send_newsletter(report_html: str, dry_run: bool = False) -> dict:
    """
    Send the report as a newsletter to all subscribers.
    Returns a summary dict with counts.
    """
    provider_name = settings.newsletter_provider
    if provider_name not in PROVIDERS:
        logger.error("Unknown newsletter provider: %s", provider_name)
        return {"sent": 0, "failed": 0, "provider": provider_name}

    provider = PROVIDERS[provider_name]()
    subscribers = _load_subscribers()

    if not subscribers:
        logger.warning("No subscribers — skipping newsletter send")
        return {"sent": 0, "failed": 0, "provider": provider_name}

    from datetime import date
    subject = f"Caracas Research — {date.today().strftime('%B %d, %Y')}"

    if dry_run:
        logger.info("DRY RUN: would send to %d subscribers via %s", len(subscribers), provider_name)
        return {"sent": 0, "failed": 0, "would_send": len(subscribers), "provider": provider_name, "dry_run": True}

    sent = 0
    failed = 0

    for email in subscribers:
        try:
            ok = provider.send(to=email, subject=subject, html_body=report_html)
            if ok:
                sent += 1
            else:
                failed += 1
        except Exception as e:
            logger.error("Failed to send to %s: %s", email, e)
            failed += 1

    summary = {"sent": sent, "failed": failed, "total": len(subscribers), "provider": provider_name}
    logger.info("Newsletter complete: %s", summary)
    return summary
