"""
Landing-page generator. Produces evergreen, long-form HTML for:
  - the pillar page  (/invest-in-venezuela)
  - sector pages    (/sectors/{slug})
  - explainers      (/explainers/{slug})

These pages target high-intent SEO queries ("invest in Venezuela",
"Venezuela mining sector", "OFAC general license 49") and are regenerated
weekly (or on demand), not on every request. Each generation uses the
premium model (settings.openai_premium_model) so the language reads like
a senior emerging-markets analyst — different cost/quality trade-off
from the daily news churn handled by analyzer.py and blog_generator.py.

Output is persisted to the LandingPage table; the Flask route reads
from there so the request path stays cheap.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Iterable

from openai import OpenAI

from src.config import settings
from src.models import (
    AssemblyNewsEntry,
    BlogPost,
    ExternalArticleEntry,
    GazetteStatus,
    LandingPage,
    SessionLocal,
    SourceType,
    init_db,
)


logger = logging.getLogger(__name__)


_ALLOWED_TAGS_RE = re.compile(
    r"<\s*/?\s*(h2|h3|h4|p|ul|ol|li|strong|em|b|i|blockquote|a|table|thead|tbody|tr|th|td)(\s+[^>]*)?\s*/?\s*>",
    re.IGNORECASE,
)
_ANY_TAG_RE = re.compile(r"<[^>]+>")


def _sanitize_body_html(html: str) -> str:
    if not html:
        return ""

    def _replace(match: re.Match) -> str:
        if _ALLOWED_TAGS_RE.fullmatch(match.group(0)):
            return match.group(0)
        return ""

    return _ANY_TAG_RE.sub(_replace, html)


def _count_words(html: str) -> int:
    text = _ANY_TAG_RE.sub(" ", html or "")
    return len([w for w in text.split() if w])


def _premium_call(client: OpenAI, *, system: str, user: str, max_tokens: int = 4500) -> tuple[str, dict]:
    """
    Single premium-model call. Returns (raw_json_string, usage_dict).

    Handles the GPT-5-family parameter differences (max_completion_tokens,
    no temperature override) with a graceful fallback to the legacy
    chat-completions parameter names for older models.
    """
    model = settings.openai_premium_model
    is_gpt5 = model.startswith("gpt-5") or model.startswith("o1") or model.startswith("o3")

    base_kwargs = dict(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
    )

    if is_gpt5:
        base_kwargs["max_completion_tokens"] = max_tokens
    else:
        base_kwargs["max_tokens"] = max_tokens
        base_kwargs["temperature"] = 0.4

    response = client.chat.completions.create(**base_kwargs)
    raw = response.choices[0].message.content
    usage = getattr(response, "usage", None)
    in_tok = getattr(usage, "prompt_tokens", 0) if usage else 0
    out_tok = getattr(usage, "completion_tokens", 0) if usage else 0
    cost = (
        (in_tok or 0) / 1_000_000 * settings.llm_premium_input_price_per_mtok
        + (out_tok or 0) / 1_000_000 * settings.llm_premium_output_price_per_mtok
    )
    return raw, {
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cost_usd": round(cost, 4),
        "model": settings.openai_premium_model,
    }


def _gather_recent_signal(db, *, sectors_filter: list[str] | None = None, limit: int = 25) -> dict:
    """
    Pull the freshest 25-or-so high-relevance briefing entries to feed
    the LLM as live context. The premium prompt should reference real,
    recent events — not invent generic copy. Optionally filter by sector.
    """
    cutoff = date.today() - timedelta(days=90)

    ext = (
        db.query(ExternalArticleEntry)
        .filter(ExternalArticleEntry.status == GazetteStatus.ANALYZED)
        .filter(ExternalArticleEntry.published_date >= cutoff)
        .order_by(ExternalArticleEntry.published_date.desc())
        .limit(150)
        .all()
    )
    asm = (
        db.query(AssemblyNewsEntry)
        .filter(AssemblyNewsEntry.status == GazetteStatus.ANALYZED)
        .filter(AssemblyNewsEntry.published_date >= cutoff)
        .order_by(AssemblyNewsEntry.published_date.desc())
        .limit(150)
        .all()
    )

    items = []
    for r in list(ext) + list(asm):
        analysis = r.analysis_json or {}
        if analysis.get("relevance_score", 0) < settings.analysis_min_relevance:
            continue
        sectors = analysis.get("sectors", []) or []
        if sectors_filter and not any(s in sectors_filter for s in sectors):
            continue
        items.append({
            "date": r.published_date.isoformat(),
            "headline": analysis.get("headline_short") or r.headline,
            "takeaway": (analysis.get("takeaway") or "").replace("<strong>", "").replace("</strong>", ""),
            "sectors": sectors,
            "relevance": analysis.get("relevance_score", 0),
            "source": getattr(r, "source", None) and r.source.value or "asamblea_nacional",
        })
    items.sort(key=lambda x: (x["relevance"], x["date"]), reverse=True)
    return {"recent_items": items[:limit], "total_considered": len(items)}


def _gather_recent_blog_posts(db, *, sector: str | None = None, limit: int = 8) -> list[BlogPost]:
    q = db.query(BlogPost).order_by(BlogPost.published_date.desc())
    if sector:
        q = q.filter(BlogPost.primary_sector == sector)
    return q.limit(limit).all()


PILLAR_SYSTEM_PROMPT = """You are a managing director at a global emerging-markets advisory firm writing the definitive evergreen guide titled "How to Invest in Venezuela: 2026 Investor Guide" for the Caracas Research.

Your audience: institutional investors, family offices, sovereign wealth funds, sanctions-compliance officers, and corporate development teams evaluating exposure to Venezuela. Most are NOT Venezuela specialists.

You MUST:
- Write 1500-2000 words of pure analyst-grade prose. No filler, no clichés, no marketing language.
- Structure with HTML <h2> sections (6-8 of them). Use <h3> sparingly within sections. Use short <p> paragraphs (2-4 sentences).
- Cite specific OFAC general license numbers, decree numbers, sectors, dates, and USD figures from the LIVE CONTEXT given by the user. Never invent statistics.
- Be balanced: name the opportunities AND the risks (sanctions exposure, currency convertibility, legal certainty, expropriation history, security).
- End with a clear "How to start" section: due diligence steps, OFAC licensing, local partner requirements, capital repatriation considerations.
- Insert internal links to /briefing, /sanctions-tracker, /sectors/{slug}, and /tools/* in the body where they fit naturally. Use the literal relative paths.
- Use only these HTML tags: h2, h3, h4, p, ul, ol, li, strong, em, blockquote, a, table, thead, tbody, tr, th, td. No <html>, <body>, <head>, <script>, <style>, or images.

Return ONE JSON object only, with these fields:
- title (60-80 chars, English, optimized for "invest in Venezuela" intent)
- subtitle (140-180 chars)
- meta_description (150-200 chars, plain text, ends with a period)
- body_html (the full 1500-2000 word body using only the allowed tags)
- key_takeaways (5-7 short bullet sentences)
- keywords (10-14 lowercase phrases — head terms + long-tail)
- table_of_contents (array of {anchor, label} objects matching your <h2> sections)

Do NOT wrap in code fences. Do NOT include markdown."""


PILLAR_USER_PROMPT_TEMPLATE = """Write the evergreen pillar page "How to Invest in Venezuela" for our institutional investor audience.

LIVE CONTEXT (the freshest {n_items} high-relevance briefings from our database — use these to ground your analysis in real, recent events. Cite by date and source where it strengthens the argument):

{context_json}

SECTORS WE COVER: {sectors_csv}

REGULAR PUBLICATION CADENCE: We publish a new investor briefing roughly twice daily based on OFAC, the US Federal Register, the Venezuelan Asamblea Nacional, the Gaceta Oficial, the BCV, and the US State Department. Mention this only ONCE near the end as a credibility signal — do not turn the article into a self-promotion piece.

Write the page now. Open with the strongest current case for capital deployment in Venezuela, follow with the sanctions framework an investor must understand, walk through each major sector with real recent examples, address risk and structuring, and close with concrete next steps. Reference the live context where it adds substance."""


SECTOR_SYSTEM_PROMPT = """You are a sector lead at an emerging-markets advisory firm writing the evergreen sector landing page for the Caracas Research.

Your audience: investors evaluating sector-specific exposure to Venezuela. They want the regulatory framework, the live deal flow, the risks, and the operating realities.

You MUST:
- Write 900-1300 words of analyst-grade prose. No filler.
- Structure with HTML <h2> sections (5-7 of them).
- Cite specific OFAC general licenses, Gaceta decrees, Asamblea laws, USD figures, dates from the LIVE CONTEXT. Never invent statistics.
- Insert internal links to /invest-in-venezuela (the parent pillar), /briefing, /sanctions-tracker, and /tools/* where they fit.
- Use only: h2, h3, h4, p, ul, ol, li, strong, em, blockquote, a, table, thead, tbody, tr, th, td.

Return ONE JSON object only, with these fields:
- title (60-80 chars, English, optimized for "Venezuela {sector_label} sector" intent)
- subtitle (140-180 chars)
- meta_description (150-200 chars, ends with a period)
- body_html
- key_takeaways (4-6 bullet sentences)
- keywords (8-12 lowercase phrases)
- table_of_contents (array of {{anchor, label}})"""


EXPLAINER_SYSTEM_PROMPT = """You are a senior emerging-markets analyst writing an evergreen explainer for the Caracas Research.

Your audience: investors, journalists, students, and the general business-curious reader who Googled the topic and wants the definitive plain-English answer.

You MUST:
- Write 800-1100 words of clear, accessible prose. Define every acronym on first use. Assume the reader knows what a bond and a sanction are, but not much more.
- Structure with HTML <h2> sections (4-6 of them). Use short <p> paragraphs.
- Be evergreen. Avoid week-of-publication news framing. Reference current LIVE CONTEXT only for illustration, not as the news hook.
- Insert internal links to /invest-in-venezuela, /briefing, /sanctions-tracker, /tools/*, and /sectors/* where they fit naturally. Use the literal relative paths.
- Use only: h2, h3, h4, p, ul, ol, li, strong, em, blockquote, a, table, thead, tbody, tr, th, td.

Return ONE JSON object only, with these fields:
- title (60-80 chars, English, optimized for the explainer's head term — e.g. "What Are OFAC Sanctions on Venezuela? A 2026 Plain-English Guide")
- subtitle (140-180 chars)
- meta_description (150-200 chars, ends with a period)
- body_html
- key_takeaways (4-6 bullet sentences)
- keywords (8-12 lowercase phrases, including head term + long-tail variations)
- table_of_contents (array of {{anchor, label}})"""


EXPLAINER_USER_PROMPT_TEMPLATE = """Write the evergreen explainer titled: "{topic_title}".

Target search intent: "{search_intent}".

LIVE CONTEXT (a small sample of the most recent {n_items} high-relevance briefings — use sparingly to ground a specific point or example. The explainer must NOT read as a current news write-up):

{context_json}

Open with the plain-English answer to the question in the title (the user came here to get one), then walk through the historical and structural context, address the most common related questions, and close with what to do next (linking to our pillar guide, briefing feed, or relevant tool). Avoid hyperbole. No marketing language."""


SECTOR_USER_PROMPT_TEMPLATE = """Write the evergreen sector landing page for the {sector_label} sector in Venezuela.

LIVE CONTEXT (the freshest {n_items} high-relevance briefings from our database tagged with this sector. Use these to ground analysis in real recent events):

{context_json}

Open with the regulatory framework in plain English, walk through current deal flow and capital flows, address sanctions exposure unique to this sector, list the operating risks, and close with how an investor should approach due diligence in this sector specifically. Reference real licensing regimes, decree numbers, and recent counterparties from the live context."""


def _payload_to_landing_row(
    payload: dict,
    *,
    page_key: str,
    page_type: str,
    canonical_path: str,
    sector_slug: str | None,
    usage: dict,
) -> dict:
    body_html = _sanitize_body_html(payload.get("body_html", ""))
    word_count = _count_words(body_html)

    keywords = payload.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]

    sections = payload.get("table_of_contents") or []
    if not isinstance(sections, list):
        sections = []

    extras = {
        "key_takeaways": payload.get("key_takeaways") or [],
        "table_of_contents": sections,
    }

    return {
        "page_key": page_key,
        "page_type": page_type,
        "title": (payload.get("title") or "")[:300],
        "subtitle": (payload.get("subtitle") or "")[:500],
        "summary": (payload.get("meta_description") or "")[:600],
        "body_html": body_html,
        "keywords_json": keywords,
        "sections_json": extras,
        "sector_slug": sector_slug,
        "canonical_path": canonical_path,
        "word_count": word_count,
        "llm_model": usage.get("model"),
        "llm_input_tokens": usage.get("input_tokens"),
        "llm_output_tokens": usage.get("output_tokens"),
        "llm_cost_usd": usage.get("cost_usd"),
        "last_generated_at": datetime.utcnow(),
    }


def _upsert_landing(db, fields: dict) -> LandingPage:
    existing = db.query(LandingPage).filter(LandingPage.page_key == fields["page_key"]).first()
    if existing:
        for k, v in fields.items():
            setattr(existing, k, v)
        db.commit()
        db.refresh(existing)
        return existing
    row = LandingPage(**fields)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def generate_pillar_page(
    *,
    sectors: list[str] | None = None,
    force: bool = False,
) -> LandingPage:
    """Generate (or regenerate) the /invest-in-venezuela pillar page."""
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY not set; cannot generate pillar page")

    init_db()
    db = SessionLocal()
    try:
        if not force:
            existing = (
                db.query(LandingPage)
                .filter(LandingPage.page_key == "pillar:invest-in-venezuela")
                .first()
            )
            if existing and existing.last_generated_at and (
                datetime.utcnow() - existing.last_generated_at < timedelta(days=6)
            ):
                logger.info("pillar page is fresh (regenerated %s); skipping", existing.last_generated_at)
                return existing

        signal = _gather_recent_signal(db, limit=30)
        sectors_csv = ", ".join(sectors or [
            "oil & gas", "mining", "real estate", "banking",
            "agriculture", "telecom", "tourism",
        ])
        user = PILLAR_USER_PROMPT_TEMPLATE.format(
            n_items=len(signal["recent_items"]),
            context_json=json.dumps(signal["recent_items"], ensure_ascii=False, indent=2),
            sectors_csv=sectors_csv,
        )

        client = OpenAI(api_key=settings.openai_api_key)
        raw, usage = _premium_call(client, system=PILLAR_SYSTEM_PROMPT, user=user, max_tokens=12000)
        payload = json.loads(raw)

        fields = _payload_to_landing_row(
            payload,
            page_key="pillar:invest-in-venezuela",
            page_type="pillar",
            canonical_path="/invest-in-venezuela",
            sector_slug=None,
            usage=usage,
        )
        row = _upsert_landing(db, fields)
        logger.info(
            "pillar page generated: %d words, model=%s, cost=$%.4f",
            row.word_count, row.llm_model, row.llm_cost_usd or 0.0,
        )
        return row
    finally:
        db.close()


def generate_sector_page(sector_slug: str, *, sector_label: str | None = None, force: bool = False) -> LandingPage:
    """Generate (or regenerate) a /sectors/{slug} landing page."""
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY not set; cannot generate sector page")

    label = (sector_label or sector_slug).replace("-", " ").replace("_", " ").title()
    init_db()
    db = SessionLocal()
    try:
        page_key = f"sector:{sector_slug}"
        if not force:
            existing = db.query(LandingPage).filter(LandingPage.page_key == page_key).first()
            if existing and existing.last_generated_at and (
                datetime.utcnow() - existing.last_generated_at < timedelta(days=6)
            ):
                logger.info("sector page %s is fresh; skipping", sector_slug)
                return existing

        sector_filters = [sector_slug.replace("-", " "), sector_slug.replace("-", "_"), sector_slug]
        signal = _gather_recent_signal(db, sectors_filter=sector_filters, limit=20)

        user = SECTOR_USER_PROMPT_TEMPLATE.format(
            sector_label=label,
            n_items=len(signal["recent_items"]),
            context_json=json.dumps(signal["recent_items"], ensure_ascii=False, indent=2),
        )
        system = SECTOR_SYSTEM_PROMPT.replace("{sector_label}", label)

        client = OpenAI(api_key=settings.openai_api_key)
        raw, usage = _premium_call(client, system=system, user=user, max_tokens=8000)
        payload = json.loads(raw)

        fields = _payload_to_landing_row(
            payload,
            page_key=page_key,
            page_type="sector",
            canonical_path=f"/sectors/{sector_slug}",
            sector_slug=sector_slug,
            usage=usage,
        )
        row = _upsert_landing(db, fields)
        logger.info(
            "sector page %s generated: %d words, model=%s, cost=$%.4f",
            sector_slug, row.word_count, row.llm_model, row.llm_cost_usd or 0.0,
        )
        return row
    finally:
        db.close()


def generate_explainer(slug: str, *, topic_title: str, search_intent: str, force: bool = False) -> LandingPage:
    """Generate (or regenerate) a /explainers/{slug} evergreen explainer."""
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY not set; cannot generate explainer")

    init_db()
    db = SessionLocal()
    try:
        page_key = f"explainer:{slug}"
        if not force:
            existing = db.query(LandingPage).filter(LandingPage.page_key == page_key).first()
            if existing and existing.last_generated_at and (
                datetime.utcnow() - existing.last_generated_at < timedelta(days=21)
            ):
                logger.info("explainer %s is fresh; skipping", slug)
                return existing

        signal = _gather_recent_signal(db, limit=15)

        user = EXPLAINER_USER_PROMPT_TEMPLATE.format(
            topic_title=topic_title,
            search_intent=search_intent,
            n_items=len(signal["recent_items"]),
            context_json=json.dumps(signal["recent_items"], ensure_ascii=False, indent=2),
        )

        client = OpenAI(api_key=settings.openai_api_key)
        raw, usage = _premium_call(client, system=EXPLAINER_SYSTEM_PROMPT, user=user, max_tokens=8000)
        payload = json.loads(raw)

        fields = _payload_to_landing_row(
            payload,
            page_key=page_key,
            page_type="explainer",
            canonical_path=f"/explainers/{slug}",
            sector_slug=None,
            usage=usage,
        )
        row = _upsert_landing(db, fields)
        logger.info(
            "explainer %s generated: %d words, model=%s, cost=$%.4f",
            slug, row.word_count, row.llm_model, row.llm_cost_usd or 0.0,
        )
        return row
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(name)-30s  %(levelname)-8s  %(message)s")
    page = generate_pillar_page(force=True)
    print({"slug": page.canonical_path, "words": page.word_count, "cost": page.llm_cost_usd})
