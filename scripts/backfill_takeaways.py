"""
Backfill `takeaways_json` for every existing BlogPost that doesn't
have one yet. Self-contained LLM call per post — uses only the
post's own title / summary / body (no need to reach back to the
source row).

Why this exists:
    src/blog_generator.py already asks the LLM to emit
    `key_takeaways` on every NEW briefing, and persists the result
    on `BlogPost.takeaways_json`. The template at
    templates/blog_post.html.j2 has always rendered a "Key
    takeaways" aside when the list is non-empty. But posts
    generated before the column existed have takeaways_json=NULL,
    so they render without the aside. This script regenerates the
    bullets for every legacy post from its persisted body so
    every /briefing/<slug> gets the scannable on-page takeaways
    rail — a known-strong signal for CTR and dwell time, and the
    kind of content Google's quality filters reward when deciding
    whether to promote a "crawled - not indexed" URL into the
    index.

Cost envelope:
    ~1,200 input tokens + ~180 output tokens per post
    -> ~ $0.006 / post at gpt-4o pricing
    -> 60 posts ≈ $0.36, 1,000 posts ≈ $6

Usage:
    # On Render (web shell — has OPENAI_API_KEY + DATABASE_URL):
    python scripts/backfill_takeaways.py

    # Limit to 50 and only process posts missing takeaways:
    python scripts/backfill_takeaways.py --limit 50

    # Force-overwrite even if takeaways already exist:
    python scripts/backfill_takeaways.py --overwrite

    # Inspect without writing:
    python scripts/backfill_takeaways.py --dry-run --limit 5
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from openai import OpenAI

from src.config import settings
from src.models import BlogPost, SessionLocal, init_db


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("backfill_takeaways")


_TAG_RE = re.compile(r"<[^>]+>")


SYSTEM_PROMPT = """You write "Key takeaways" bullets for a Venezuela-focused investment-research publication.

Voice: concrete, calm, information-dense. One analyst writing for another. NEVER marketing. NEVER emoji, hashtags, or exclamation marks. NEVER vague phrasing ("may impact", "could lead to"). Prefer specific nouns, named actors, numbers, dates.

Each bullet:
- 12-28 words
- Plain text, no markdown or HTML
- Declarative sentence
- Surfaces ONE fact or implication per bullet — no stacking
- Does NOT restate the headline

Return a single JSON object: {"key_takeaways": ["<bullet 1>", "<bullet 2>", "<bullet 3>", "<bullet 4>"]}.

Produce 3 to 5 bullets. Order them by investor importance, most decision-relevant first.

Examples of the right register:
- "Chevron's GL 41A remains the only OFAC carve-out currently allowing US-based oil liftings from Venezuela."
- "The decree strips BCV of its authority to set intervention FX bands — a tool it used three times in 2025."
- "Caracas must certify compliance with the November 2025 electoral roadmap by Jan 15, 2026 or lose waiver eligibility."
"""


USER_PROMPT_TEMPLATE = """TITLE: {title}

SUMMARY: {summary}

BODY (truncated):
{body}

Write the key_takeaways array now. JSON only."""


def _strip_html(html: str | None) -> str:
    if not html:
        return ""
    return _TAG_RE.sub(" ", html)


def _generate_takeaways(client: OpenAI, post: BlogPost) -> list[str] | None:
    body_text = _strip_html(post.body_html)[:4500]
    user_msg = USER_PROMPT_TEMPLATE.format(
        title=post.title or "",
        summary=post.summary or post.subtitle or "",
        body=body_text or "(no body)",
    )
    try:
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.3,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        logger.warning("post id=%s: LLM call failed: %s", post.id, exc)
        return None

    try:
        payload = json.loads(response.choices[0].message.content)
    except Exception as exc:
        logger.warning("post id=%s: JSON parse failed: %s", post.id, exc)
        return None

    raw = payload.get("key_takeaways") or []
    if not isinstance(raw, list):
        return None

    cleaned: list[str] = []
    for t in raw:
        if not isinstance(t, str):
            continue
        s = _TAG_RE.sub("", t).strip()
        if not s:
            continue
        if len(s) > 300:
            s = s[:300].rstrip()
        cleaned.append(s)
        if len(cleaned) >= 5:
            break

    if len(cleaned) < 3:
        logger.warning(
            "post id=%s: LLM returned only %d valid bullets, skipping",
            post.id,
            len(cleaned),
        )
        return None
    return cleaned


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Max posts to process (0 = all).")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Regenerate even if takeaways_json already exists.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate takeaways but don't write.",
    )
    args = parser.parse_args()

    if not settings.openai_api_key:
        logger.error("OPENAI_API_KEY not set; cannot generate takeaways.")
        return 2

    init_db()
    db = SessionLocal()
    try:
        q = db.query(BlogPost).order_by(BlogPost.published_date.desc())
        if not args.overwrite:
            # Empty list [] is technically "already set" so we also
            # treat zero-length arrays as missing. Most DBs expose
            # JSON NULL as Python None; empty lists we filter in-app.
            q = q.filter(BlogPost.takeaways_json.is_(None))
        if args.limit > 0:
            q = q.limit(args.limit)
        posts = q.all()

        # In-app empty-list filter (belt-and-braces for the JSONB
        # case where IS NULL wouldn't catch `[]`).
        if not args.overwrite:
            posts = [p for p in posts if not (p.takeaways_json or [])]

        if not posts:
            logger.info("Nothing to backfill — every post already has takeaways.")
            return 0

        logger.info("Backfilling takeaways for %d post(s)…", len(posts))

        client = OpenAI(api_key=settings.openai_api_key)

        ok = 0
        skipped = 0
        for post in posts:
            takeaways = _generate_takeaways(client, post)
            if not takeaways:
                skipped += 1
                continue
            logger.info(
                "post id=%s slug=%s\n  -> %s",
                post.id,
                post.slug,
                " | ".join(t[:60] + ("…" if len(t) > 60 else "") for t in takeaways),
            )
            if not args.dry_run:
                post.takeaways_json = takeaways
                db.add(post)
                ok += 1

        if not args.dry_run:
            db.commit()

        logger.info(
            "Done. updated=%d skipped=%d dry_run=%s",
            ok,
            skipped,
            args.dry_run,
        )
        return 0
    except Exception as exc:
        logger.exception("Backfill failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return 3
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
