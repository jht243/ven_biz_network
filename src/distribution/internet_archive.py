"""
Internet Archive distribution channel.

Each daily Investor Tearsheet PDF is deposited as a public IA item at
archive.org/details/{identifier}. IA items get indexed by Google and
(after IA's own crawl cycle) by Google Scholar — over time this builds
a permanent, search-engine-discoverable corpus of dated research notes
that all link back to caracasresearch.com.

Authentication uses the S3-like API keys generated at
https://archive.org/account/s3.php; both INTERNET_ARCHIVE_ACCESS_KEY
and INTERNET_ARCHIVE_SECRET_KEY must be set or the channel is silently
skipped (consistent with how google_indexing and bluesky behave).

This module is intentionally thin: it accepts a PDF (bytes) + a date,
constructs IA-compliant metadata, calls the SDK, returns a result
record. Deduplication and cooldown logic live in the runner so the
behavior matches the other channels.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from src.config import settings

logger = logging.getLogger(__name__)


_ITEM_PREFIX = "caracas-research-daily-tearsheet"
_DETAILS_URL = "https://archive.org/details/{identifier}"
_DOWNLOAD_URL = "https://archive.org/download/{identifier}/{filename}"


@dataclass
class IAUploadResult:
    success: bool
    identifier: str
    details_url: Optional[str]
    download_url: Optional[str]
    response_code: Optional[int]
    response_snippet: str


def is_enabled() -> bool:
    return bool(
        (settings.internet_archive_access_key or "").strip()
        and (settings.internet_archive_secret_key or "").strip()
    )


def identifier_for_date(d: date) -> str:
    """IA item identifier for a given tearsheet date.

    IA identifiers must be unique site-wide, lowercase, alphanumeric
    plus hyphens/underscores/periods, 5-100 chars. Including the brand
    namespace (`caracas-research-…`) keeps it human-readable AND avoids
    collisions with other accounts."""
    return f"{_ITEM_PREFIX}-{d.strftime('%Y-%m-%d')}"


def _build_metadata(d: date) -> dict:
    """IA item metadata. Title/description/subject are what Google
    Scholar and IA search will index."""
    nice_date = d.strftime("%B %d, %Y")
    return {
        "collection": settings.internet_archive_collection,
        "mediatype": "texts",
        "title": f"Caracas Research — Daily Venezuela Investor Tearsheet — {nice_date}",
        "creator": "Caracas Research",
        "publisher": "Caracas Research",
        "date": d.isoformat(),
        "language": "eng",
        "subject": [
            "Venezuela",
            "investment",
            "sanctions",
            "OFAC",
            "BCV",
            "emerging markets",
            "Latin America",
            "country risk",
            "tearsheet",
            "research note",
        ],
        "description": (
            f"One-page research note for international investors covering "
            f"Venezuela on {nice_date}. Includes: BCV official + parallel "
            f"FX rates and parallel premium, US travel advisory level, "
            f"the day's top development with full investor takeaway, the "
            f"6-bar Investment Climate Scorecard (sanctions trajectory, "
            f"diplomatic progress, legal framework, political stability, "
            f"property rights, macro stability), and any high-relevance "
            f"calendar events in the next 14 days. Sources: BCV (live "
            f"scrape), OFAC SDN, US State Department, Federal Register, "
            f"GDELT, Asamblea Nacional. Full daily briefing and methodology "
            f"at https://caracasresearch.com."
        ),
        "rights": "Caracas Research — free to share with attribution.",
    }


def upload_tearsheet(pdf_bytes: bytes, d: date) -> IAUploadResult:
    """Upload a single tearsheet PDF to Internet Archive as a new item.
    If the identifier already exists, IA's behavior is to update files
    in-place — which is what we want for re-runs of the same day."""
    if not is_enabled():
        return IAUploadResult(
            success=False, identifier="",
            details_url=None, download_url=None,
            response_code=None,
            response_snippet="not configured",
        )

    # Lazy import — the lib is heavy and not needed unless this channel runs.
    try:
        from internetarchive import upload as ia_upload
    except Exception as exc:
        logger.warning("internetarchive lib import failed: %s", exc)
        return IAUploadResult(
            success=False, identifier="",
            details_url=None, download_url=None,
            response_code=None,
            response_snippet=f"sdk import error: {exc}"[:500],
        )

    identifier = identifier_for_date(d)
    filename = f"{identifier}.pdf"
    metadata = _build_metadata(d)

    try:
        # IA's SDK accepts either local file paths or file-like objects in
        # the `files` dict; passing raw bytes makes it try to interpret
        # them as a path ("File name too long" if the bytes are non-empty).
        # Wrap in BytesIO so the SDK uploads the in-memory bytes directly.
        pdf_stream = io.BytesIO(pdf_bytes)
        pdf_stream.name = filename  # SDK reads .name to set the remote filename
        responses = ia_upload(
            identifier,
            files={filename: pdf_stream},
            metadata=metadata,
            access_key=settings.internet_archive_access_key,
            secret_key=settings.internet_archive_secret_key,
            verbose=False,
            retries=2,
            retries_sleep=5,
        )
    except Exception as exc:
        logger.warning("IA upload exception for %s: %s", identifier, exc)
        return IAUploadResult(
            success=False, identifier=identifier,
            details_url=_DETAILS_URL.format(identifier=identifier),
            download_url=_DOWNLOAD_URL.format(identifier=identifier, filename=filename),
            response_code=None,
            response_snippet=f"upload exception: {exc}"[:500],
        )

    # ia_upload returns a list of requests.Response. Treat any non-2xx
    # as a failure but bubble back the identifier either way so the
    # caller can log/inspect.
    last_code = None
    last_text = ""
    for resp in responses or []:
        code = getattr(resp, "status_code", None)
        if code is not None:
            last_code = code
            last_text = (getattr(resp, "text", "") or "")[:300]
            if code >= 400:
                logger.warning("IA upload %d for %s: %s", code, identifier, last_text)
                return IAUploadResult(
                    success=False, identifier=identifier,
                    details_url=_DETAILS_URL.format(identifier=identifier),
                    download_url=_DOWNLOAD_URL.format(identifier=identifier, filename=filename),
                    response_code=code,
                    response_snippet=last_text,
                )

    logger.info("IA upload ok: %s (code=%s)", identifier, last_code)
    return IAUploadResult(
        success=True, identifier=identifier,
        details_url=_DETAILS_URL.format(identifier=identifier),
        download_url=_DOWNLOAD_URL.format(identifier=identifier, filename=filename),
        response_code=last_code,
        response_snippet=last_text or "ok",
    )
