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

# Legacy module-level constant retained for backward compat with any
# importer that still references it. Live code should call
# _report_object_key() so the value tracks the runtime setting (which
# may be overridden per-deployment via SUPABASE_REPORT_OBJECT_KEY to
# avoid cross-project bucket collisions — see config.py).
REPORT_OBJECT_KEY = "report.html"


def _report_object_key() -> str:
    return (settings.supabase_report_object_key or REPORT_OBJECT_KEY).strip() or REPORT_OBJECT_KEY


def _supabase_base_url() -> Optional[str]:
    url = (settings.supabase_url or "").rstrip("/")
    return url or None


def supabase_storage_enabled() -> bool:
    """Write-side: needs both URL + service key (used by cron)."""
    return bool(_supabase_base_url() and settings.supabase_service_key)


def supabase_storage_read_enabled() -> bool:
    """Read-side: only needs URL (public bucket; used by web)."""
    return bool(_supabase_base_url())


def public_report_url() -> Optional[str]:
    base = _supabase_base_url()
    if not base:
        return None
    return f"{base}/storage/v1/object/public/{settings.supabase_report_bucket}/{_report_object_key()}"


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
    object_key = _report_object_key()
    upload_url = f"{base}/storage/v1/object/{bucket}/{object_key}"

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
    logger.info("Uploaded %s to Supabase Storage: %s", object_key, public)
    return public


def upload_object(
    object_key: str,
    body: bytes,
    *,
    content_type: str = "application/octet-stream",
    cache_control: str = "max-age=3600",
    bucket: Optional[str] = None,
) -> Optional[str]:
    """
    Generic Supabase Storage upload — used by the tearsheet PDF pipeline
    and any future binary asset that needs a stable public URL.

    Returns the public URL on success, None if storage is not configured.
    Raises on hard failures.
    """
    if not supabase_storage_enabled():
        logger.info("Supabase Storage not configured; skipping upload of %s", object_key)
        return None

    base = _supabase_base_url()
    target_bucket = bucket or settings.supabase_report_bucket
    upload_url = f"{base}/storage/v1/object/{target_bucket}/{object_key}"

    headers = {
        "Authorization": f"Bearer {settings.supabase_service_key}",
        "Content-Type": content_type,
        "x-upsert": "true",
        "cache-control": cache_control,
    }

    resp = httpx.post(upload_url, content=body, headers=headers, timeout=60)
    if resp.status_code >= 400:
        logger.error(
            "Supabase Storage upload failed %d for %s: %s",
            resp.status_code, object_key, resp.text[:300],
        )
        resp.raise_for_status()

    public = f"{base}/storage/v1/object/public/{target_bucket}/{object_key}"
    logger.info("Uploaded %s to Supabase Storage: %s (%d bytes)",
                object_key, public, len(body))
    return public


def public_object_url(object_key: str, bucket: Optional[str] = None) -> Optional[str]:
    """Build the public-bucket URL for an object key (does not check existence)."""
    base = _supabase_base_url()
    if not base:
        return None
    target_bucket = bucket or settings.supabase_report_bucket
    return f"{base}/storage/v1/object/public/{target_bucket}/{object_key}"


def signed_url(object_key: str, *, bucket: Optional[str] = None, expires_in: int = 3600) -> Optional[str]:
    """Create a short-lived signed URL for a private-bucket object.

    Returns the signed URL string, or None if storage is not configured.
    """
    if not supabase_storage_enabled():
        return None

    base = _supabase_base_url()
    target_bucket = bucket or settings.supabase_report_bucket
    url = f"{base}/storage/v1/object/sign/{target_bucket}/{object_key}"
    headers = {
        "Authorization": f"Bearer {settings.supabase_service_key}",
        "Content-Type": "application/json",
    }
    resp = httpx.post(url, json={"expiresIn": expires_in}, headers=headers, timeout=15)
    if resp.status_code >= 400:
        logger.warning("Signed URL failed %d for %s/%s: %s",
                        resp.status_code, target_bucket, object_key, resp.text[:200])
        return None
    data = resp.json()
    signed_path = data.get("signedURL") or data.get("signedUrl") or ""
    if signed_path:
        return f"{base}/storage/v1{signed_path}" if signed_path.startswith("/") else signed_path
    return None


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
