"""
Supabase Storage helpers — used so the cron job and the web service (which
run in different Render containers) can share the generated report.html.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

REPORT_OBJECT_KEY = "report.html"


def _supabase_base_url() -> Optional[str]:
    url = (settings.supabase_url or "").rstrip("/")
    return url or None


def supabase_storage_enabled() -> bool:
    return bool(_supabase_base_url() and settings.supabase_service_key)


def public_report_url() -> Optional[str]:
    base = _supabase_base_url()
    if not base:
        return None
    return f"{base}/storage/v1/object/public/{settings.supabase_report_bucket}/{REPORT_OBJECT_KEY}"


def upload_report_html(html: str) -> Optional[str]:
    """
    Upload the rendered report HTML to Supabase Storage.
    Returns the public URL on success, None if storage is not configured.
    Raises on hard failures.
    """
    if not supabase_storage_enabled():
        logger.info("Supabase Storage not configured; skipping remote upload")
        return None

    base = _supabase_base_url()
    bucket = settings.supabase_report_bucket
    upload_url = f"{base}/storage/v1/object/{bucket}/{REPORT_OBJECT_KEY}"

    headers = {
        "Authorization": f"Bearer {settings.supabase_service_key}",
        "Content-Type": "text/html; charset=utf-8",
        "x-upsert": "true",
        "cache-control": "max-age=60",
    }

    resp = httpx.post(upload_url, content=html.encode("utf-8"), headers=headers, timeout=30)
    if resp.status_code >= 400:
        logger.error("Supabase Storage upload failed %d: %s", resp.status_code, resp.text)
        resp.raise_for_status()

    public = public_report_url()
    logger.info("Uploaded report.html to Supabase Storage: %s", public)
    return public


def fetch_report_html() -> Optional[str]:
    """
    Fetch the latest report.html from Supabase Storage.
    Returns the HTML string, or None if not available / not configured.
    """
    url = public_report_url()
    if not url:
        return None

    try:
        resp = httpx.get(url, timeout=15)
    except httpx.HTTPError as e:
        logger.warning("Failed to fetch report from Supabase Storage: %s", e)
        return None

    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        logger.warning("Supabase Storage GET returned %d: %s", resp.status_code, resp.text[:200])
        return None
    return resp.text
