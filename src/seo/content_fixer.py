"""
SEO content auto-fixer — takes an AuditReport, identifies fixable issues
on database-backed content pages (LandingPage model — sectors,
explainers), and fixes them via web search + LLM.

Fixable issues:
  - Missing H1 on sector/explainer pages → generates H1 + opening paragraph
  - Thin content (< 300 words) on sector/explainer pages → expands body

Constraints:
  - Only operates on page_type in ('sector', 'explainer')
  - Skips pages updated in the last 7 days (fresh from landing_generator)
  - Budget-capped at 5 fixes per run (configurable)
  - Idempotent — only fixes pages that still fail the check
  - After fixing, pings Google Indexing API for the fixed URLs
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser

import httpx
from openai import OpenAI

from src.config import settings
from src.models import LandingPage, SessionLocal, init_db
from src.seo.audit import AuditReport, Severity

logger = logging.getLogger(__name__)

_FIXABLE_PAGE_TYPES = ("sector", "explainer")
_THIN_CONTENT_THRESHOLD = 300  # words
_EXPANSION_TARGET = "500–700"
_FRESHNESS_DAYS = 7
_DEFAULT_MAX_FIXES = 5

_ANY_TAG_RE = re.compile(r"<[^>]+>")
_ALLOWED_TAGS_RE = re.compile(
    r"<\s*/?\s*(h1|h2|h3|h4|p|ul|ol|li|strong|em|b|i|blockquote|a|table|thead|tbody|tr|th|td)(\s+[^>]*)?\s*/?\s*>",
    re.IGNORECASE,
)


def _sanitize_body_html(html: str) -> str:
    """Strip disallowed HTML tags, keeping only content-safe elements."""
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


# ---------------------------------------------------------------------------
# Web search (best-effort, no API key needed)
# ---------------------------------------------------------------------------

def _web_search(query: str, max_results: int = 3) -> list[dict]:
    """Lightweight DuckDuckGo HTML search. Returns up to max_results
    dicts with 'title', 'url', 'snippet'. Fails silently."""
    try:
        resp = httpx.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0 (compatible; SEOAuditBot/1.0)"},
            timeout=10,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.debug("Web search failed for %r: %s", query, exc)
        return []

    results: list[dict] = []
    html = resp.text

    # Simple regex extraction from DDG HTML results page
    for m in re.finditer(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'class="result__snippet"[^>]*>(.*?)</(?:td|div)',
        html, re.DOTALL,
    ):
        if len(results) >= max_results:
            break
        url = m.group(1)
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        snippet = re.sub(r"<[^>]+>", "", m.group(3)).strip()
        if title and snippet:
            results.append({"title": title, "url": url, "snippet": snippet})

    return results


# ---------------------------------------------------------------------------
# LLM call (reuses the landing_generator pattern)
# ---------------------------------------------------------------------------

def _premium_call(
    client: OpenAI, *, system: str, user: str, max_tokens: int = 3000,
) -> tuple[str, dict]:
    """Single premium-model call. Returns (raw_json_string, usage_dict)."""
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
        "model": model,
    }


# ---------------------------------------------------------------------------
# Fix: missing H1
# ---------------------------------------------------------------------------

_H1_SYSTEM = """You are a senior emerging-markets analyst. Generate an H1 headline and
a brief opening paragraph for a landing page about the given topic.

Return JSON with:
- h1: The H1 headline (50-80 chars, compelling, keyword-rich)
- opening_paragraph: 2-3 sentences (HTML <p> tag) introducing the topic

Use only these HTML tags: h1, p, strong, em. No markdown."""


def _fix_missing_h1(
    client: OpenAI,
    page: LandingPage,
    search_results: list[dict],
) -> tuple[str, dict] | None:
    """Generate an H1 + opening paragraph and prepend to body_html."""
    context = ""
    if search_results:
        context = "\n\nRecent web context:\n" + "\n".join(
            f"- {r['title']}: {r['snippet']}" for r in search_results
        )

    user_msg = (
        f"Topic: {page.title}\n"
        f"Page type: {page.page_type}\n"
        f"Current URL: {page.canonical_path}\n"
        f"{context}\n\n"
        f"Generate the H1 and opening paragraph."
    )

    try:
        raw, usage = _premium_call(client, system=_H1_SYSTEM, user=user_msg, max_tokens=500)
        data = json.loads(raw)
    except Exception as exc:
        logger.error("LLM call failed for H1 fix on %s: %s", page.page_key, exc)
        return None

    h1 = data.get("h1", "").strip()
    opening = data.get("opening_paragraph", "").strip()
    if not h1:
        return None

    prepend = f"<h1>{h1}</h1>\n{opening}\n"
    page.body_html = _sanitize_body_html(prepend + (page.body_html or ""))
    page.word_count = _count_words(page.body_html)

    return f"Added H1: '{h1}'", usage


# ---------------------------------------------------------------------------
# Fix: thin content
# ---------------------------------------------------------------------------

_EXPAND_SYSTEM = """You are a senior emerging-markets analyst expanding a thin landing page
about Venezuela. The page currently has too few words to rank well.

You MUST:
- Expand the body to {target} words of analyst-grade prose
- Keep existing content intact — ADD new sections, don't rewrite
- Reference the web search results provided as grounding context
- Use only these HTML tags: h2, h3, p, ul, ol, li, strong, em, blockquote, a
- Return JSON with one field: body_html (the FULL expanded body, not just the new parts)"""


def _fix_thin_content(
    client: OpenAI,
    page: LandingPage,
    current_word_count: int,
    search_results: list[dict],
) -> tuple[str, dict] | None:
    """Expand thin body_html to target word count."""
    context = ""
    if search_results:
        context = "\n\nRecent web context:\n" + "\n".join(
            f"- {r['title']}: {r['snippet']}" for r in search_results
        )

    system = _EXPAND_SYSTEM.replace("{target}", _EXPANSION_TARGET)
    user_msg = (
        f"Topic: {page.title}\n"
        f"Page type: {page.page_type}\n"
        f"Current URL: {page.canonical_path}\n"
        f"Current word count: {current_word_count}\n"
        f"Current body:\n{page.body_html[:3000]}\n"
        f"{context}\n\n"
        f"Expand this page to {_EXPANSION_TARGET} words."
    )

    try:
        raw, usage = _premium_call(client, system=system, user=user_msg, max_tokens=4000)
        data = json.loads(raw)
    except Exception as exc:
        logger.error("LLM call failed for thin-content fix on %s: %s", page.page_key, exc)
        return None

    new_body = data.get("body_html", "").strip()
    if not new_body:
        return None

    new_count = _count_words(new_body)
    if new_count < current_word_count:
        logger.warning("LLM returned fewer words (%d) than original (%d) for %s",
                        new_count, current_word_count, page.page_key)
        return None

    page.body_html = _sanitize_body_html(new_body)
    page.word_count = _count_words(page.body_html)

    return f"Expanded from {current_word_count} to {page.word_count} words", usage


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def _should_run_seo_fixes() -> bool:
    """Only run on the evening cron (same window as tearsheet)."""
    import os
    if os.environ.get("SEO_FIX_FORCE", "").strip():
        return True
    if os.environ.get("SEO_FIX_SKIP", "").strip():
        return False
    hour_utc = datetime.utcnow().hour
    return hour_utc >= 21 or hour_utc <= 2


def fix_content_issues(
    report: AuditReport,
    *,
    max_fixes: int = _DEFAULT_MAX_FIXES,
) -> dict:
    """Take an AuditReport and fix what can be fixed on LandingPage rows.

    Returns a summary dict with fixes applied, total cost, etc.
    """
    if not _should_run_seo_fixes():
        return {"status": "skipped", "reason": "not the evening cron"}

    if not settings.openai_api_key:
        return {"status": "skipped", "reason": "no OpenAI API key configured"}

    init_db()
    db = SessionLocal()
    client = OpenAI(api_key=settings.openai_api_key)

    cutoff = datetime.now(timezone.utc) - timedelta(days=_FRESHNESS_DAYS)
    fixes_applied: list[dict] = []
    total_cost = 0.0
    fixed_urls: list[str] = []

    try:
        # Build a set of page_keys that have fixable issues
        fixable_paths: dict[str, list[str]] = {}  # path -> list of issue categories
        for finding in report.findings:
            if finding.severity not in (Severity.ERROR, Severity.WARNING):
                continue
            if finding.category in ("h1", "thin_content"):
                fixable_paths.setdefault(finding.path, []).append(finding.category)

        # Query candidate LandingPage rows
        candidates = (
            db.query(LandingPage)
            .filter(LandingPage.page_type.in_(_FIXABLE_PAGE_TYPES))
            .all()
        )

        for page in candidates:
            if len(fixes_applied) >= max_fixes:
                break

            path = page.canonical_path or ""
            norm = path.rstrip("/") or "/"
            issues = fixable_paths.get(norm) or fixable_paths.get(path)
            if not issues:
                continue

            # Skip recently updated pages
            updated = page.updated_at
            if updated and updated.replace(tzinfo=timezone.utc) > cutoff:
                logger.debug("Skipping %s — updated %s (< %d days ago)",
                             page.page_key, updated, _FRESHNESS_DAYS)
                continue

            # Re-verify the issue still exists
            current_words = _count_words(page.body_html or "")
            has_h1_issue = "h1" in issues and not _html_has_h1(page.body_html or "")
            has_thin_issue = "thin_content" in issues and current_words < _THIN_CONTENT_THRESHOLD

            if not has_h1_issue and not has_thin_issue:
                continue

            # Web search for grounding context
            search_query = f"{page.title} Venezuela {date.today().year}"
            search_results = _web_search(search_query)

            if has_h1_issue and len(fixes_applied) < max_fixes:
                result = _fix_missing_h1(client, page, search_results)
                if result:
                    msg, usage = result
                    total_cost += usage.get("cost_usd", 0)
                    fixes_applied.append({
                        "page_key": page.page_key,
                        "path": path,
                        "fix": "missing_h1",
                        "detail": msg,
                        "cost_usd": usage.get("cost_usd", 0),
                    })
                    fixed_urls.append(path)

            # Re-count after possible H1 fix
            current_words = _count_words(page.body_html or "")
            if has_thin_issue and current_words < _THIN_CONTENT_THRESHOLD and len(fixes_applied) < max_fixes:
                result = _fix_thin_content(client, page, current_words, search_results)
                if result:
                    msg, usage = result
                    total_cost += usage.get("cost_usd", 0)
                    fixes_applied.append({
                        "page_key": page.page_key,
                        "path": path,
                        "fix": "thin_content",
                        "detail": msg,
                        "cost_usd": usage.get("cost_usd", 0),
                    })
                    if path not in fixed_urls:
                        fixed_urls.append(path)

            page.updated_at = datetime.now(timezone.utc)

        db.commit()

        # Ping Google Indexing API for fixed URLs
        if fixed_urls:
            _reindex_fixed_urls(fixed_urls)

    except Exception as exc:
        logger.error("Content fixer failed: %s", exc, exc_info=True)
        db.rollback()
        return {"status": "error", "error": str(exc), "fixes_applied": len(fixes_applied)}
    finally:
        db.close()

    return {
        "status": "ok",
        "fixes_applied": len(fixes_applied),
        "total_cost_usd": round(total_cost, 4),
        "details": fixes_applied,
    }


def _html_has_h1(html: str) -> bool:
    return bool(re.search(r"<h1[\s>]", html, re.IGNORECASE))


def _reindex_fixed_urls(paths: list[str]) -> None:
    """Ping Google Indexing API for URLs that were just fixed."""
    try:
        from src.distribution import google_indexing

        if not google_indexing.is_enabled():
            logger.debug("Google Indexing not configured, skipping re-index")
            return

        client = google_indexing.get_client()
        if client is None:
            return

        base = settings.canonical_site_url
        urls = [f"{base}{p}" for p in paths]
        results = client.publish_urls(urls)
        succeeded = sum(1 for r in results if r.success)
        logger.info("Re-indexed %d/%d fixed URLs via Google Indexing API",
                     succeeded, len(urls))
    except Exception as exc:
        logger.warning("Re-indexing fixed URLs failed (non-fatal): %s", exc)
