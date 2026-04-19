"""
Distribution runner — Phase 5 of the daily pipeline.

For each enabled channel, finds entities (BlogPost rows, the homepage,
key landing pages) that haven't been distributed yet and distributes
them. Records every attempt in `distribution_logs` so we never re-ping
the same URL on the same channel within the cooldown window.

Today this only handles the Google Indexing channel; Bluesky, Mastodon,
Telegram, LinkedIn, Threads, Medium will be added as additional helpers
in this same file (or split out as the count grows).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Iterable

from src.config import settings
from src.distribution import (
    bluesky,
    google_indexing,
    indexnow,
    internet_archive,
    osf,
    zenodo,
)
from src.models import BlogPost, DistributionLog, SessionLocal, init_db


logger = logging.getLogger(__name__)


CHANNEL_GOOGLE_INDEXING = "google_indexing"
CHANNEL_INDEXNOW = "indexnow"
CHANNEL_BLUESKY = "bluesky"
CHANNEL_INTERNET_ARCHIVE = "internet_archive"
CHANNEL_ZENODO = "zenodo"
CHANNEL_OSF = "osf"

# Don't re-ping the same URL on the same channel within this window.
# Google's docs say frequent re-notifications for unchanged URLs are
# discouraged. 24h covers the realistic re-ping cases (updated content)
# while leaving room for our twice-daily cron without doubling up.
_REPING_COOLDOWN = timedelta(hours=23)

# Static URLs that change every cron run (the report regenerates) and
# are worth pinging to encourage Google to re-crawl. We deliberately
# keep this list short to stay well inside the 200/day quota.
_STATIC_URLS_TO_PING_DAILY = (
    "/",
    "/sanctions-tracker",
    "/calendar",
    "/sanctions/individuals",
    "/sanctions/entities",
    "/sanctions/vessels",
    "/sanctions/aircraft",
)


def _site_base() -> str:
    return settings.site_url.rstrip("/")


def _blog_url(post: BlogPost) -> str:
    return f"{_site_base()}/briefing/{post.slug}"


def _recent_pinged_urls(db, channel: str, lookback: timedelta) -> set[str]:
    """Return the set of URLs that have a SUCCESSFUL ping on this channel
    within the cooldown window. Used to avoid re-pinging."""
    cutoff = datetime.utcnow() - lookback
    rows = (
        db.query(DistributionLog.url)
        .filter(DistributionLog.channel == channel)
        .filter(DistributionLog.success.is_(True))
        .filter(DistributionLog.created_at >= cutoff)
        .all()
    )
    return {r[0] for r in rows}


def _record(
    db,
    *,
    channel: str,
    url: str,
    success: bool,
    response_code: int | None,
    response_snippet: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
) -> None:
    db.add(
        DistributionLog(
            channel=channel,
            url=url,
            entity_type=entity_type,
            entity_id=entity_id,
            success=success,
            response_code=response_code,
            response_snippet=response_snippet,
        )
    )


def run_google_indexing() -> dict:
    """Ping the Google Indexing API for new BlogPost URLs + a small set
    of high-priority static pages that change every cron run.

    Returns a small summary dict suitable for logging by the orchestrator.
    """
    if not google_indexing.is_enabled():
        return {"status": "skipped", "reason": "not configured"}

    client = google_indexing.get_client()
    if client is None:
        return {"status": "skipped", "reason": "credentials invalid"}

    init_db()
    db = SessionLocal()
    try:
        already_pinged = _recent_pinged_urls(db, CHANNEL_GOOGLE_INDEXING, _REPING_COOLDOWN)

        # 1. New blog posts created in the lookback window
        cutoff = datetime.utcnow() - timedelta(days=settings.google_indexing_lookback_days)
        new_posts = (
            db.query(BlogPost)
            .filter(BlogPost.created_at >= cutoff)
            .order_by(BlogPost.created_at.desc())
            .limit(settings.google_indexing_max_per_run)
            .all()
        )

        candidates: list[tuple[str, str, int]] = []  # (url, entity_type, entity_id)
        for post in new_posts:
            url = _blog_url(post)
            if url in already_pinged:
                continue
            candidates.append((url, "blog_post", post.id))

        # 2. High-signal static URLs (homepage, sanctions tracker, calendar)
        for path in _STATIC_URLS_TO_PING_DAILY:
            url = _site_base() + path
            if url in already_pinged:
                continue
            candidates.append((url, "static", None))

        # Cap total quota use per run
        if len(candidates) > settings.google_indexing_max_per_run:
            candidates = candidates[: settings.google_indexing_max_per_run]

        if not candidates:
            return {"status": "ok", "pinged": 0, "succeeded": 0, "failed": 0, "reason": "nothing new"}

        succeeded = 0
        failed = 0
        for url, entity_type, entity_id in candidates:
            result = client.publish_url_updated(url)
            _record(
                db,
                channel=CHANNEL_GOOGLE_INDEXING,
                url=url,
                success=result.success,
                response_code=result.status_code,
                response_snippet=result.response_snippet,
                entity_type=entity_type,
                entity_id=entity_id,
            )
            if result.success:
                succeeded += 1
            else:
                failed += 1

        db.commit()
        return {
            "status": "ok",
            "pinged": len(candidates),
            "succeeded": succeeded,
            "failed": failed,
        }
    except Exception as exc:
        logger.exception("google indexing runner failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return {"status": "error", "error": str(exc)}
    finally:
        db.close()


def run_indexnow() -> dict:
    """Submit recent BlogPost URLs + the standard daily-changing static
    pages to IndexNow in a single batched POST. IndexNow forwards to all
    participating engines (Bing, Yandex, Seznam, Naver, Mojeek, ...).

    No credentials required — the protocol authenticates by domain
    ownership via /{key}.txt, which Flask serves automatically.
    """
    init_db()
    db = SessionLocal()
    try:
        already_submitted = _recent_pinged_urls(db, CHANNEL_INDEXNOW, _REPING_COOLDOWN)

        # Same lookback window as Google Indexing so the two channels
        # operate on a coherent slice of new content.
        cutoff = datetime.utcnow() - timedelta(days=settings.google_indexing_lookback_days)
        new_posts = (
            db.query(BlogPost)
            .filter(BlogPost.created_at >= cutoff)
            .order_by(BlogPost.created_at.desc())
            .limit(500)  # IndexNow accepts up to 10k per call; 500 is plenty.
            .all()
        )

        candidates: list[tuple[str, str, int | None]] = []
        for post in new_posts:
            url = _blog_url(post)
            if url in already_submitted:
                continue
            candidates.append((url, "blog_post", post.id))

        for path in _STATIC_URLS_TO_PING_DAILY:
            url = _site_base() + path
            if url in already_submitted:
                continue
            candidates.append((url, "static", None))

        # Newly added SDN profile pages — when the OFAC scraper picks up
        # a fresh designation, its profile URL won't have been pinged
        # yet. Submit it now so Bing/Yandex index the name within hours.
        try:
            from src.data.sdn_profiles import list_all_profiles
            for p in list_all_profiles():
                url = _site_base() + p.url_path
                if url in already_submitted:
                    continue
                candidates.append((url, "sdn_profile", p.db_id))
        except Exception as exc:
            logger.warning("indexnow: could not enumerate SDN profiles: %s", exc)

        if not candidates:
            return {"status": "ok", "submitted": 0, "reason": "nothing new"}

        # IndexNow takes the entire batch in one POST, so we record one
        # DistributionLog row per URL after a single network call.
        urls_only = [c[0] for c in candidates]
        result = indexnow.submit_urls(urls_only)

        for url, entity_type, entity_id in candidates:
            _record(
                db,
                channel=CHANNEL_INDEXNOW,
                url=url,
                success=result.success,
                response_code=result.status_code,
                response_snippet=result.response_snippet,
                entity_type=entity_type,
                entity_id=entity_id,
            )

        db.commit()
        return {
            "status": "ok" if result.success else "error",
            "submitted": result.submitted,
            "response_code": result.status_code,
        }
    except Exception as exc:
        logger.exception("indexnow runner failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return {"status": "error", "error": str(exc)}
    finally:
        db.close()


def run_bluesky() -> dict:
    """Post recent BlogPost briefings to Bluesky.

    Unlike search-engine pings, social posting is one-and-done — once a
    URL is posted to Bluesky we never repost it (the cooldown for this
    channel is effectively forever, enforced by checking DistributionLog
    for ANY successful past post on this URL, not just within 23h).

    Only briefings created within bluesky_lookback_days are eligible, so
    the historical backlog never gets posted (would look like spam).
    """
    if not bluesky.is_enabled():
        return {"status": "skipped", "reason": "no credentials"}

    init_db()
    db = SessionLocal()
    try:
        # For social channels, "already posted" is permanent — not a
        # 23h cooldown. We check for any successful past attempt.
        already_posted_rows = (
            db.query(DistributionLog.url)
            .filter(DistributionLog.channel == CHANNEL_BLUESKY)
            .filter(DistributionLog.success.is_(True))
            .all()
        )
        already_posted = {r[0] for r in already_posted_rows}

        cutoff = datetime.utcnow() - timedelta(days=settings.bluesky_lookback_days)
        new_posts = (
            db.query(BlogPost)
            .filter(BlogPost.created_at >= cutoff)
            .order_by(BlogPost.created_at.asc())  # oldest-first so backlog drains in order
            .all()
        )

        candidates: list[BlogPost] = []
        for post in new_posts:
            url = _blog_url(post)
            if url in already_posted:
                continue
            candidates.append(post)
            if len(candidates) >= settings.bluesky_max_per_run:
                break

        if not candidates:
            return {"status": "ok", "posted": 0, "reason": "nothing new"}

        client = bluesky.get_client()
        if client is None:
            return {"status": "skipped", "reason": "client init failed"}

        # Per-post OG cards: each briefing has its own pre-rendered
        # 1200x630 PNG persisted on `BlogPost.og_image_bytes`. We
        # upload that blob fresh per post (Bluesky stores blobs by
        # content hash, so identical bytes would dedupe anyway, but
        # ours are unique per briefing). For any older post that
        # hasn't been backfilled yet, fall back to the static tile —
        # uploaded lazily and cached for the rest of this run.
        static_thumb_blob: dict | None = None
        static_thumb_attempted = False

        posted = 0
        failed = 0
        first_post_url: str | None = None
        for post in candidates:
            url = _blog_url(post)

            text = bluesky.compose_post(
                social_hook=post.social_hook,
                title=post.title or "",
            )

            thumb_blob: dict | None = None
            if post.og_image_bytes:
                thumb_blob = client.upload_blob(post.og_image_bytes, mime_type="image/png")
                if thumb_blob is None:
                    logger.warning(
                        "bluesky: per-post og upload failed for slug=%s; falling back",
                        post.slug,
                    )
            if thumb_blob is None:
                if not static_thumb_attempted:
                    static_thumb_attempted = True
                    static_thumb_blob = client.upload_image_from_url(
                        f"{_site_base()}/static/og-image.png?v=3"
                    )
                thumb_blob = static_thumb_blob

            link_card = bluesky.LinkCard(
                uri=url,
                title=(post.title or "")[:300],
                description=(post.summary or post.subtitle or "")[:300],
                thumb_blob=thumb_blob,
            )
            result = client.post(text=text, link_card=link_card)
            _record(
                db,
                channel=CHANNEL_BLUESKY,
                url=url,
                success=result.success,
                response_code=result.status_code,
                response_snippet=(result.post_url or result.response_snippet)[:500],
                entity_type="blog_post",
                entity_id=post.id,
            )
            if result.success:
                posted += 1
                if not first_post_url:
                    first_post_url = result.post_url
            else:
                failed += 1

        db.commit()
        return {
            "status": "ok",
            "posted": posted,
            "failed": failed,
            "first_post_url": first_post_url,
        }
    except Exception as exc:
        logger.exception("bluesky runner failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return {"status": "error", "error": str(exc)}
    finally:
        db.close()


def run_internet_archive() -> dict:
    """Upload today's daily Investor Tearsheet PDF to Internet Archive.

    Identifier (`caracas-research-daily-tearsheet-YYYY-MM-DD`) is
    deterministic per-day, and IA's upload semantics are
    upsert-in-place — so re-running on the same day is safe and
    overwrites the existing file rather than creating a duplicate item.

    Cooldown: we still record one DistributionLog row per
    successful upload and skip same-day re-uploads to avoid wasting
    network on cron re-runs.
    """
    if not internet_archive.is_enabled():
        return {"status": "skipped", "reason": "no credentials"}

    # Time-gate: same rule as Phase 3b — IA only uploads on the
    # evening cron, so we don't push a half-stale morning PDF.
    from src.distribution.tearsheet import (
        get_or_build_tearsheet,
        should_publish_today,
    )
    if not should_publish_today():
        return {"status": "skipped", "reason": "not the evening cron"}

    init_db()
    db = SessionLocal()
    try:
        already = _recent_pinged_urls(db, CHANNEL_INTERNET_ARCHIVE, _REPING_COOLDOWN)

        data, pdf_bytes = get_or_build_tearsheet()
        today = data["generated_at"].date()
        identifier = internet_archive.identifier_for_date(today)
        details_url = f"https://archive.org/details/{identifier}"

        if details_url in already:
            return {"status": "ok", "uploaded": 0, "reason": "already uploaded today"}

        result = internet_archive.upload_tearsheet(pdf_bytes, today)

        _record(
            db,
            channel=CHANNEL_INTERNET_ARCHIVE,
            url=result.details_url or details_url,
            success=result.success,
            response_code=result.response_code,
            response_snippet=result.response_snippet,
            entity_type="tearsheet",
            entity_id=None,
        )
        db.commit()

        return {
            "status": "ok" if result.success else "error",
            "uploaded": 1 if result.success else 0,
            "identifier": result.identifier,
            "details_url": result.details_url,
            "download_url": result.download_url,
            "response_code": result.response_code,
        }
    except Exception as exc:
        logger.exception("internet archive runner failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return {"status": "error", "error": str(exc)}
    finally:
        db.close()


def run_zenodo() -> dict:
    """Upload today's daily Investor Tearsheet PDF to Zenodo.

    Each successful run mints a NEW DOI (Zenodo records are immutable
    once published; new versions get new DOIs). The 23h cooldown +
    DistributionLog dedup keys on the per-day record URL ensure a
    same-day cron re-fire is a no-op.

    Time-gated identically to IA: evening cron only, so we only mint
    one DOI per day reflecting the full day's intelligence."""
    if not zenodo.is_enabled():
        return {"status": "skipped", "reason": "no credentials"}

    from src.distribution.tearsheet import (
        get_or_build_tearsheet,
        should_publish_today,
    )
    if not should_publish_today():
        return {"status": "skipped", "reason": "not the evening cron"}

    init_db()
    db = SessionLocal()
    try:
        data, pdf_bytes = get_or_build_tearsheet()
        today = data["generated_at"].date()

        # Cooldown key = the date-stamped /tearsheet/<date>.pdf URL on
        # our own site. Stable per-day so the cooldown actually dedups
        # (Zenodo DOIs are not knowable until after publish).
        cooldown_key = f"{settings.site_url.rstrip('/')}/tearsheet/{today.isoformat()}.pdf"
        already = _recent_pinged_urls(db, CHANNEL_ZENODO, _REPING_COOLDOWN)
        if cooldown_key in already:
            return {"status": "ok", "uploaded": 0, "reason": "already uploaded today"}

        result = zenodo.upload_tearsheet(pdf_bytes, today)

        _record(
            db,
            channel=CHANNEL_ZENODO,
            url=cooldown_key,
            success=result.success,
            response_code=result.response_code,
            response_snippet=result.response_snippet,
            entity_type="tearsheet",
            entity_id=None,
        )
        db.commit()

        return {
            "status": "ok" if result.success else "error",
            "uploaded": 1 if result.success else 0,
            "deposition_id": result.deposition_id,
            "doi": result.doi,
            "record_url": result.record_url,
            "download_url": result.download_url,
            "response_code": result.response_code,
        }
    except Exception as exc:
        logger.exception("zenodo runner failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return {"status": "error", "error": str(exc)}
    finally:
        db.close()


def run_osf() -> dict:
    """Upload today's daily Investor Tearsheet PDF to OSF as a preprint.

    OSF Preprints IS indexed by Google Scholar (this is the channel's
    raison d'être versus Zenodo + Internet Archive). Same time-gate +
    23h cooldown semantics as the other tearsheet channels.

    Note: OSF preprints are not strictly idempotent per-day — each
    successful publish creates a new preprint record. We rely on the
    cooldown to prevent same-day duplicates."""
    if not osf.is_enabled():
        return {
            "status": "skipped",
            "reason": "no credentials (token or project node id missing)",
        }

    from src.distribution.tearsheet import (
        get_or_build_tearsheet,
        should_publish_today,
    )
    if not should_publish_today():
        return {"status": "skipped", "reason": "not the evening cron"}

    init_db()
    db = SessionLocal()
    try:
        data, pdf_bytes = get_or_build_tearsheet()
        today = data["generated_at"].date()

        cooldown_key = f"{settings.site_url.rstrip('/')}/tearsheet/{today.isoformat()}.pdf#osf"
        already = _recent_pinged_urls(db, CHANNEL_OSF, _REPING_COOLDOWN)
        if cooldown_key in already:
            return {"status": "ok", "uploaded": 0, "reason": "already uploaded today"}

        result = osf.upload_tearsheet(pdf_bytes, today)

        _record(
            db,
            channel=CHANNEL_OSF,
            url=cooldown_key,
            success=result.success,
            response_code=result.response_code,
            response_snippet=result.response_snippet,
            entity_type="tearsheet",
            entity_id=None,
        )
        db.commit()

        return {
            "status": "ok" if result.success else "error",
            "uploaded": 1 if result.success else 0,
            "preprint_id": result.preprint_id,
            "file_guid": result.file_guid,
            "record_url": result.record_url,
            "download_url": result.download_url,
            "response_code": result.response_code,
        }
    except Exception as exc:
        logger.exception("osf runner failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return {"status": "error", "error": str(exc)}
    finally:
        db.close()


def run_all() -> dict:
    """Run every enabled distribution channel. Returns per-channel summary."""
    return {
        CHANNEL_GOOGLE_INDEXING: run_google_indexing(),
        CHANNEL_INDEXNOW: run_indexnow(),
        CHANNEL_BLUESKY: run_bluesky(),
        CHANNEL_INTERNET_ARCHIVE: run_internet_archive(),
        CHANNEL_ZENODO: run_zenodo(),
        CHANNEL_OSF: run_osf(),
        # Future: mastodon, telegram, linkedin, threads, medium
    }
