"""
Long-form blog post generator.

For each high-relevance briefing entry (ExternalArticle / AssemblyNews)
that doesn't yet have a corresponding BlogPost row, runs a single LLM
call that produces an investor-grade analysis post (700-900 words),
ready to render at /briefing/{slug}.

This is on its own LLM budget (settings.blog_gen_budget_per_run) so
the daily report can stay cheap; blog posts are nice-to-have for SEO
but not blocking.

Costs:
    ~2.5k input tokens + ~1.8k output tokens per post
    -> ~ $0.025 input + $0.018 output = ~$0.04/post
    -> default budget 6/run = ~$0.25/run
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Iterable

from openai import OpenAI

from src.analyzer import _LLM_USAGE
from src.config import settings
from src.models import (
    AssemblyNewsEntry,
    BlogPost,
    ExternalArticleEntry,
    GazetteStatus,
    SessionLocal,
    SourceType,
    init_db,
)


logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a senior emerging-markets analyst writing an investor-grade long-form blog post about Venezuelan business, investment, and sanctions news. Your audience is global institutional investors, family offices, sanctions compliance officers, and corporate development teams considering or already exposed to Venezuela.

Your writing is:
- Plain English, journalistic, no jargon clichés
- Concrete: cite specific OFAC general license numbers, decree numbers, dates, USD amounts, sectors, agencies
- Balanced: acknowledge both opportunity and risk; never cheerlead
- 700-900 words total in the body
- Structured with HTML <h2> subheadings (3-5 of them) and short <p> paragraphs (2-4 sentences each)

You MUST return a single JSON object with these fields:
- title (string, STRICT 45-58 chars, English, optimized for "invest in Venezuela / OFAC / sector" search intent — Google SERPs cut titles around 60 chars, so any title above 58 loses its most search-relevant words. Front-load specific nouns: company names, OFAC EO numbers, license numbers, sector keywords. Drop any "for Investors", "Implications for Investors", or "Amid Sanctions" suffix — investor focus is implicit on this site and those suffixes waste SERP budget.)
- subtitle (string, 80-130 chars, English, expands the title with the second-most-important angle)
- summary (string, STRICT 120-150 chars, plain text, used as the meta description — Google SERPs cut snippets around 155 chars on desktop. Lead with the concrete fact, not "This article discusses…" framing. No vague hedges like "potentially" or "could impact" in the first 80 chars.)
- body_html (string, the full post body — ONLY <h2>, <p>, <ul>, <li>, <strong>, <em>, <blockquote>, and <a href> tags allowed)
- keywords (array of 6-10 lowercase phrases, English, mix of head terms and long-tail)
- primary_sector (string, one of: mining, energy, oil_gas, real_estate, banking, sanctions, governance, fiscal, diplomatic, legal, agriculture, telecom, other)
- key_takeaways (array of 3-5 short bullet sentences, plain text)
- investor_implications (string, 80-160 chars, plain text, "what this means for capital deployment")
- social_hook (string, 180-250 chars, plain text — the OPENING LINE of a social-media post about this story. Voice: one analyst messaging another over Slack. Surfaces the tension, the surprise, or the "why this matters" in a single beat. NEVER restate the title verbatim. NEVER use hashtags, emoji, exclamation marks, or marketing clichés like "game-changing", "groundbreaking", "must-read". Conversational but precise. Examples of the right register: "Caracas just gave the assembly an unusual seat at the table on the OFAC talks — first time since 2022.", "PDVSA quietly let the Eulen waiver lapse last week. Most of the desk hasn't noticed yet.")

Do NOT use markdown. Do NOT wrap output in code fences. Output only the JSON object."""


USER_PROMPT_TEMPLATE = """Write a long-form analysis post about the following Venezuelan business / investment / sanctions development.

SOURCE: {source_name} ({credibility})
PUBLISHED: {published_date}
URL: {source_url}
HEADLINE (original language): {headline}
ENGLISH HEADLINE (analyst summary): {headline_short}

ANALYST SUMMARY:
{takeaway}

DETECTED SECTORS: {sectors}
SENTIMENT: {sentiment}
RELEVANCE SCORE: {relevance}/10

SOURCE BODY (truncated):
{body_text}

Write the post now. Open with the news in the lead paragraph (do not bury the lede), then provide context, then concrete investor implications, then risk factors, then a forward-looking close. Use <h2> subheadings to break up the body."""


_ALLOWED_TAGS_RE = re.compile(
    r"<\s*/?\s*(h2|h3|p|ul|ol|li|strong|em|b|i|blockquote|a)(\s+[^>]*)?\s*/?\s*>",
    re.IGNORECASE,
)
_ANY_TAG_RE = re.compile(r"<[^>]+>")


def _slugify(text: str, *, max_len: int = 80) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len] or "briefing"


def _count_words(html: str) -> int:
    text = _ANY_TAG_RE.sub(" ", html or "")
    return len([w for w in text.split() if w])


def _sanitize_body_html(html: str) -> str:
    """
    Drop any tags that aren't on our allow-list. Cheap defense against
    the LLM occasionally emitting <script>, <style>, raw <html> or
    other unwanted markup.
    """
    if not html:
        return ""

    def _replace(match: re.Match) -> str:
        if _ALLOWED_TAGS_RE.fullmatch(match.group(0)):
            return match.group(0)
        return ""

    return _ANY_TAG_RE.sub(_replace, html)


def _candidate_external(db) -> list[ExternalArticleEntry]:
    cutoff = date.today() - timedelta(days=settings.blog_gen_lookback_days)
    rows = (
        db.query(ExternalArticleEntry)
        .filter(ExternalArticleEntry.status == GazetteStatus.ANALYZED)
        .filter(ExternalArticleEntry.published_date >= cutoff)
        # Defend against the April 2026 cross-project Postgres contamination
        # (see src/models.py::SourceType.OPENALEX). A sister project's rows
        # must never graduate into our briefings pipeline even if they
        # somehow reach status=ANALYZED.
        .filter(ExternalArticleEntry.source != SourceType.OPENALEX)
        .order_by(ExternalArticleEntry.published_date.desc())
        .all()
    )
    out = []
    for r in rows:
        analysis = r.analysis_json or {}
        if analysis.get("relevance_score", 0) < settings.blog_gen_min_relevance:
            continue
        out.append(r)
    return out


def _candidate_assembly(db) -> list[AssemblyNewsEntry]:
    cutoff = date.today() - timedelta(days=settings.blog_gen_lookback_days)
    rows = (
        db.query(AssemblyNewsEntry)
        .filter(AssemblyNewsEntry.status == GazetteStatus.ANALYZED)
        .filter(AssemblyNewsEntry.published_date >= cutoff)
        .order_by(AssemblyNewsEntry.published_date.desc())
        .all()
    )
    out = []
    for r in rows:
        analysis = r.analysis_json or {}
        if analysis.get("relevance_score", 0) < settings.blog_gen_min_relevance:
            continue
        out.append(r)
    return out


def _existing_blog_keys(db) -> set[tuple[str, int]]:
    return {
        (row.source_table, row.source_id)
        for row in db.query(BlogPost.source_table, BlogPost.source_id).all()
    }


def _build_post_payload(
    client: OpenAI,
    *,
    source_name: str,
    credibility: str,
    published_date: str,
    source_url: str,
    headline: str,
    headline_short: str,
    takeaway: str,
    sectors: list[str],
    sentiment: str,
    relevance: int,
    body_text: str,
) -> tuple[dict, dict]:
    """Single LLM call. Returns (parsed_payload, usage_dict)."""
    body_truncated = (body_text or "")[:6000] or "(no body text available)"

    user_msg = USER_PROMPT_TEMPLATE.format(
        source_name=source_name,
        credibility=credibility,
        published_date=published_date,
        source_url=source_url,
        headline=headline,
        headline_short=headline_short or headline,
        takeaway=takeaway or "(none)",
        sectors=", ".join(sectors) if sectors else "(none)",
        sentiment=sentiment or "mixed",
        relevance=relevance,
        body_text=body_truncated,
    )

    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.4,
        max_tokens=2400,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content
    parsed = json.loads(raw)

    usage = getattr(response, "usage", None)
    in_tok = getattr(usage, "prompt_tokens", 0) if usage else 0
    out_tok = getattr(usage, "completion_tokens", 0) if usage else 0
    if usage is not None:
        _LLM_USAGE["calls"] += 1
        _LLM_USAGE["input_tokens"] += in_tok or 0
        _LLM_USAGE["output_tokens"] += out_tok or 0

    cost = (
        (in_tok or 0) / 1_000_000 * settings.llm_input_price_per_mtok
        + (out_tok or 0) / 1_000_000 * settings.llm_output_price_per_mtok
    )
    return parsed, {
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cost_usd": round(cost, 6),
        "model": settings.openai_model,
    }


def _entry_metadata(item, source_table: str) -> dict:
    analysis = item.analysis_json or {}
    if source_table == "external_articles":
        if item.source == SourceType.FEDERAL_REGISTER:
            source_name = "Federal Register"
            credibility = "OFFICIAL"
        elif item.source == SourceType.OFAC_SDN:
            source_name = "OFAC SDN List"
            credibility = "OFFICIAL"
        elif item.source == SourceType.TRAVEL_ADVISORY:
            source_name = "US State Department"
            credibility = "OFFICIAL"
        elif item.source == SourceType.GDELT:
            source_name = (item.extra_metadata or {}).get("domain") or item.source_name or "International Press"
            credibility = "TIER2"
        elif item.source == SourceType.GOOGLE_NEWS:
            meta = item.extra_metadata or {}
            source_name = meta.get("publisher") or meta.get("publisher_domain") or "International Press"
            credibility = "TIER1" if (item.credibility and item.credibility.value == "tier1") else "TIER2"
        else:
            source_name = item.source_name or item.source.value
            credibility = "TIER1"
    else:
        source_name = "Asamblea Nacional"
        credibility = "STATE"

    return {
        "source_name": source_name,
        "credibility": credibility,
        "headline_short": analysis.get("headline_short", ""),
        "takeaway": analysis.get("takeaway", ""),
        "sectors": analysis.get("sectors", []) or [],
        "sentiment": analysis.get("sentiment", "mixed"),
        "relevance": analysis.get("relevance_score", 0),
    }


def _post_url_slug(headline: str, source_table: str, source_id: int, published: date) -> str:
    base = _slugify(headline)
    return f"{base}-{published.strftime('%Y%m%d')}-{source_id}"


def _persist_post(
    db,
    *,
    source_table: str,
    source_id: int,
    item,
    payload: dict,
    usage: dict,
) -> BlogPost:
    body_html = _sanitize_body_html(payload.get("body_html", ""))
    word_count = _count_words(body_html)
    reading_minutes = max(1, round(word_count / 220))

    title = (payload.get("title") or item.headline)[:300]
    slug_base = _slugify(title)
    slug = f"{slug_base}-{item.published_date.strftime('%Y%m%d')}-{source_id}"

    keywords = payload.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]

    sectors = payload.get("sectors") or item.analysis_json.get("sectors", []) or []
    primary_sector = (payload.get("primary_sector") or (sectors[0] if sectors else None))
    if isinstance(primary_sector, str):
        primary_sector = primary_sector[:80]

    social_hook = (payload.get("social_hook") or "").strip()
    if social_hook:
        social_hook = social_hook[:280]

    # Normalise the LLM's key_takeaways array into a clean list of
    # 3-5 short plain-text bullets. The prompt already asks for this,
    # but we defensively trim, strip tags, and cap length so a
    # malformed response can never break the template.
    raw_takeaways = payload.get("key_takeaways") or []
    if isinstance(raw_takeaways, str):
        raw_takeaways = [raw_takeaways]
    takeaways: list[str] = []
    for t in raw_takeaways:
        if not isinstance(t, str):
            continue
        cleaned = re.sub(r"<[^>]+>", "", t).strip()
        if not cleaned:
            continue
        if len(cleaned) > 300:
            cleaned = cleaned[:300].rstrip()
        takeaways.append(cleaned)
        if len(takeaways) >= 5:
            break

    post = BlogPost(
        source_table=source_table,
        source_id=source_id,
        slug=slug,
        title=title,
        subtitle=(payload.get("subtitle") or "")[:500],
        summary=(payload.get("summary") or "")[:600],
        body_html=body_html,
        social_hook=social_hook or None,
        primary_sector=primary_sector,
        sectors_json=sectors,
        keywords_json=keywords,
        related_slugs_json=[],
        takeaways_json=takeaways or None,
        word_count=word_count,
        reading_minutes=reading_minutes,
        published_date=item.published_date,
        canonical_source_url=item.source_url,
        llm_model=usage.get("model"),
        llm_input_tokens=usage.get("input_tokens"),
        llm_output_tokens=usage.get("output_tokens"),
        llm_cost_usd=usage.get("cost_usd"),
    )
    db.add(post)
    db.commit()
    db.refresh(post)

    # Render the per-briefing OG card and persist its bytes on the row.
    # Best-effort: a render failure should never block the blog itself
    # from being saved (a missing card just falls back to the generic
    # site-wide OG image at request time).
    try:
        from src.og_image import latest_bcv_usd, render_briefing_card

        png = render_briefing_card(
            title=post.title or "",
            category=post.primary_sector,
            published_date=post.published_date,
            bcv_usd=latest_bcv_usd(),
        )
        if png:
            post.og_image_bytes = png
            db.commit()
            db.refresh(post)
    except Exception as exc:
        logger.warning("blog_generator: og card render failed for slug=%s: %s", post.slug, exc)

    return post


def run_blog_generation(*, budget: int | None = None) -> dict:
    """
    Find candidate entries with no blog post yet, write up to `budget`
    posts, persist, return a summary dict.
    """
    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY not set; skipping blog generation")
        return {"generated": 0, "skipped": "no_api_key"}

    init_db()
    db = SessionLocal()
    try:
        budget = budget if budget is not None else settings.blog_gen_budget_per_run
        if budget <= 0:
            return {"generated": 0, "skipped": "budget_zero"}

        existing = _existing_blog_keys(db)

        ext_candidates = [
            r for r in _candidate_external(db) if ("external_articles", r.id) not in existing
        ]
        asm_candidates = [
            r for r in _candidate_assembly(db) if ("assembly_news", r.id) not in existing
        ]

        ranked: list[tuple[int, str, object]] = []
        for r in ext_candidates:
            ranked.append((
                int((r.analysis_json or {}).get("relevance_score", 0)),
                "external_articles",
                r,
            ))
        for r in asm_candidates:
            ranked.append((
                int((r.analysis_json or {}).get("relevance_score", 0)),
                "assembly_news",
                r,
            ))
        ranked.sort(key=lambda t: (t[0], t[2].published_date), reverse=True)

        client = OpenAI(api_key=settings.openai_api_key)

        generated = 0
        failed = 0
        total_cost = 0.0
        slugs: list[str] = []

        for relevance, source_table, item in ranked[:budget]:
            meta = _entry_metadata(item, source_table)
            try:
                payload, usage = _build_post_payload(
                    client,
                    source_name=meta["source_name"],
                    credibility=meta["credibility"],
                    published_date=item.published_date.isoformat(),
                    source_url=item.source_url,
                    headline=item.headline,
                    headline_short=meta["headline_short"],
                    takeaway=meta["takeaway"],
                    sectors=meta["sectors"],
                    sentiment=meta["sentiment"],
                    relevance=meta["relevance"],
                    body_text=item.body_text or "",
                )
                post = _persist_post(
                    db,
                    source_table=source_table,
                    source_id=item.id,
                    item=item,
                    payload=payload,
                    usage=usage,
                )
                generated += 1
                total_cost += usage.get("cost_usd") or 0.0
                slugs.append(post.slug)
                logger.info(
                    "blog_generator: wrote %s (relevance=%d, %d words, $%.4f)",
                    post.slug, relevance, post.word_count, usage.get("cost_usd") or 0.0,
                )
            except Exception as exc:
                logger.exception("blog_generator failed on %s/%d: %s", source_table, item.id, exc)
                failed += 1
                db.rollback()

        return {
            "generated": generated,
            "failed": failed,
            "candidates": len(ranked),
            "budget": budget,
            "estimated_cost_usd": round(total_cost, 4),
            "slugs": slugs,
        }
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(name)-30s  %(levelname)-8s  %(message)s",
    )
    print(run_blog_generation())
