"""One-shot IndexNow backfill — submit every public URL on the site to
Bing/Yandex/Seznam/Naver/Mojeek in a single batched POST.

Use after first activating IndexNow (i.e. after setting INDEXNOW_KEY in
production env) so the engines have your full corpus immediately,
instead of waiting on twice-daily cron pings to drip-feed them.

Usage (run locally; does NOT need to run on Render):
    python scripts/indexnow_submit.py             # submit everything
    python scripts/indexnow_submit.py --dry-run   # list URLs only

Idempotent — IndexNow accepts re-submissions cheaply, so re-running is
safe. Records every submitted URL in distribution_logs so subsequent
cron runs respect the 23-hour cooldown.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

os.environ.setdefault("SITE_URL", "https://caracasresearch.com")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import settings  # noqa: E402
from src.distribution import indexnow  # noqa: E402
from src.distribution.runner import CHANNEL_INDEXNOW, _record  # noqa: E402
from src.models import BlogPost, LandingPage, SessionLocal, init_db  # noqa: E402


# Static, evergreen routes worth submitting — keep in sync with the
# sitemap_xml() route in server.py.
STATIC_PATHS: tuple[str, ...] = (
    "/",
    "/briefing",
    "/sanctions-tracker",
    "/invest-in-venezuela",
    "/tools",
    "/tools/caracas-safety-by-neighborhood",
    "/tools/venezuela-visa-requirements",
    "/tools/venezuela-investment-roi-calculator",
    "/tools/bolivar-usd-exchange-rate",
    "/tools/ofac-venezuela-sanctions-checker",
    "/tools/ofac-venezuela-general-licenses",
    "/explainers",
    "/travel",
    "/calendar",
    "/sources",
    "/sanctions/individuals",
    "/sanctions/entities",
    "/sanctions/vessels",
    "/sanctions/aircraft",
)


def _site_base() -> str:
    return settings.site_url.rstrip("/")


def collect_urls() -> list[tuple[str, str, int | None]]:
    """Return [(url, entity_type, entity_id), ...] for every public URL."""
    base = _site_base()
    out: list[tuple[str, str, int | None]] = []

    for path in STATIC_PATHS:
        out.append((f"{base}{path}", "static", None))

    init_db()
    db = SessionLocal()
    try:
        for post in db.query(BlogPost).order_by(BlogPost.created_at.desc()).all():
            if not post.slug:
                continue
            out.append((f"{base}/briefing/{post.slug}", "blog_post", post.id))

        for page in db.query(LandingPage).all():
            path = (page.canonical_path or "").strip()
            if not path or not path.startswith("/"):
                continue
            out.append((f"{base}{path}", "landing_page", page.id))
    finally:
        db.close()

    # Per-SDN profile pages — every OFAC Venezuela-program designation
    # is its own indexable URL. We submit them all so Bing/Yandex
    # discover the corpus immediately rather than waiting on link-walking.
    try:
        from src.data.sdn_profiles import list_all_profiles
        for p in list_all_profiles():
            out.append((f"{base}{p.url_path}", "sdn_profile", p.db_id))
    except Exception as exc:
        print(f"WARN: could not enumerate SDN profiles for IndexNow: {exc}")

    # Dedupe while preserving order.
    seen: set[str] = set()
    unique: list[tuple[str, str, int | None]] = []
    for u, t, i in out:
        if u in seen:
            continue
        seen.add(u)
        unique.append((u, t, i))
    return unique


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="List URLs without submitting.")
    ap.add_argument("--batch-size", type=int, default=500,
                    help="URLs per IndexNow POST. Protocol allows 10k; "
                         "smaller batches give more readable logs.")
    args = ap.parse_args()

    if not (settings.indexnow_key or "").strip():
        print("ERROR: INDEXNOW_KEY is not set in your environment.")
        print("       Set it in .env (and Render env vars), then re-run.")
        return 2

    urls = collect_urls()
    print(f"Collected {len(urls)} unique public URLs.")
    print(f"Site: {_site_base()}")
    print(f"Key file: {_site_base()}/{settings.indexnow_key}.txt")
    print()

    if args.dry_run:
        for u, t, _ in urls:
            print(f"  [{t:13}] {u}")
        print(f"\nDRY RUN: would submit {len(urls)} URL(s).")
        return 0

    init_db()
    db = SessionLocal()
    try:
        total_ok = 0
        total_fail = 0
        for i in range(0, len(urls), args.batch_size):
            chunk = urls[i : i + args.batch_size]
            urls_only = [u for u, _, _ in chunk]
            print(f"Submitting batch {i // args.batch_size + 1} "
                  f"({len(urls_only)} URLs)...")
            result = indexnow.submit_urls(urls_only)
            print(f"  -> status={result.status_code} "
                  f"submitted={result.submitted} success={result.success}")
            print(f"  -> response: {result.response_snippet[:200]}")

            for url, entity_type, entity_id in chunk:
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
            if result.success:
                total_ok += len(chunk)
            else:
                total_fail += len(chunk)
        db.commit()
        print()
        print(f"Done at {datetime.utcnow().isoformat()}Z — "
              f"{total_ok} submitted, {total_fail} failed.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
