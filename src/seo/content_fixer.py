"""
SEO content auto-fixer.

Takes an AuditReport from src.seo.audit, identifies LandingPage-backed
pages with missing H1 or thin content (< 200 words), and fixes them
using web search for current context + the premium LLM model.

Only operates on LandingPage rows (sectors, explainers, pillar pages).
Tool pages and hub pages are excluded — their content is interactive or
index-style, and thin content is by design.

Usage (programmatic, called from run_daily.py Phase 6b):
    from src.seo.audit import run_audit
    from src.seo.content_fixer import fix_content_issues
    report = run_audit()
    result = fix_content_issues(report)
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime

import httpx
from openai import OpenAI

from src.config import settings
from src.models import LandingPage, SessionLocal, init_db
from src.seo.audit import AuditReport

logger = logging.getLogger(__name__)

_MAX_FIXES_PER_RUN = 5

_ANY_TAG_RE = re.compile(r"<[^>]+>")
_ALLOWED_TAGS_RE = re.compile(
    r"<\s*/?\s*(h1|h2|h3|h4|p|ul|ol|li|strong|em|b|i|blockquote|a|table|thead|tbody|tr|th|td)(\s+[^>]*)?\s*/?\s*>",
    re.IGNORECASE,
)


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


def _web_search(query: str, *, max_results: int = 5) -> list[dict]:
    """Lightweight web search via DuckDuckGo HTML. Returns a list of
    {title, snippet, url} dicts. Best-effort — returns [] on failure."""
    try:
        resp = httpx.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "CaracasResearch-SEOFixer/1.0"},
            timeout=10,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            logger.warning("web_search: DDG returned %d for %r", resp.status_code, query)
            return []

        results: list[dict] = []
        html = resp.text

        for m in re.finditer(
            r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
            r'class="result__snippet"[^>]*>(.*?)</(?:a|td|div)',
            html,
            re.DOTALL,
        ):
            url = m.group(1)
            title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            snippet = re.sub(r"<[^>]+>", "", m.group(3)).strip()
            if title and snippet:
                results.append({"title": title, "snippet": snippet, "url": url})
                if len(results) >= max_results:
                    break

        return results
    except Exception as exc:
        logger.warning("web_search failed for %r: %s", query, exc)
        return []


def _premium_call(client: OpenAI, *, system: str, user: str, max_tokens: int = 3000) -> tuple[str, dict]:
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


_SYSTEM_PROMPT = """You are a senior emerging-markets analyst at Caracas Research. You are fixing SEO issues on existing landing pages. Your writing is:
- Concise, authoritative, and backed by the web search results provided
- Plain English, no jargon clichés, no filler
- Structured with HTML tags: h1, h2, h3, p, ul, ol, li, strong, em, a (with real href paths)
- Focused on Venezuela — sanctions (OFAC), investment, oil & gas, mining, trade, travel, or the specific topic of the page

You MUST return a single JSON object. The exact fields depend on the task described in the user prompt."""


def _fix_missing_h1(
    client: OpenAI,
    page: LandingPage,
    search_results: list[dict],
) -> dict | None:
    """Generate an H1 for a page that's missing one."""
    search_ctx = "\n".join(
        f"- {r['title']}: {r['snippet']}" for r in search_results
    ) or "No search results available."

    user_prompt = f"""This page needs an H1 heading. Generate one based on the page context and current web search results.

PAGE TITLE: {page.title}
PAGE SUMMARY: {page.summary or '(none)'}
PAGE PATH: {page.canonical_path}
PAGE TYPE: {page.page_type}

CURRENT WEB SEARCH RESULTS for this topic:
{search_ctx}

Return JSON with:
- h1 (string, 40-80 chars, keyword-rich, descriptive, matches the page's topic)
- body_prefix (string, 1-2 HTML paragraphs with <h1> and a strong opening <p> to prepend to the existing body)"""

    try:
        raw, usage = _premium_call(client, system=_SYSTEM_PROMPT, user=user_prompt, max_tokens=500)
        data = json.loads(raw)
        return {
            "h1": data.get("h1", ""),
            "body_prefix": _sanitize_body_html(data.get("body_prefix", "")),
            "usage": usage,
        }
    except Exception as exc:
        logger.warning("fix_missing_h1 failed for %s: %s", page.canonical_path, exc)
        return None


def _fix_thin_content(
    client: OpenAI,
    page: LandingPage,
    current_word_count: int,
    search_results: list[dict],
) -> dict | None:
    """Expand a page with thin content to 400+ words using web search context."""
    search_ctx = "\n".join(
        f"- {r['title']}: {r['snippet']}" for r in search_results
    ) or "No search results available."

    current_body_preview = (page.body_html or "")[:2000]

    user_prompt = f"""This page has thin content ({current_word_count} words). Expand it to 400-600 words using the current web search results as grounding.

PAGE TITLE: {page.title}
PAGE SUMMARY: {page.summary or '(none)'}
PAGE PATH: {page.canonical_path}
PAGE TYPE: {page.page_type}
CURRENT WORD COUNT: {current_word_count}

CURRENT BODY (first 2000 chars):
{current_body_preview}

CURRENT WEB SEARCH RESULTS for this topic:
{search_ctx}

Return JSON with:
- body_html (string, the COMPLETE expanded body — keep existing good content, add new sections/paragraphs grounded in the search results. Use h2, h3, p, ul, li, strong, em, a tags. Target 400-600 words.)
- word_count (integer, the word count of the new body)"""

    try:
        raw, usage = _premium_call(client, system=_SYSTEM_PROMPT, user=user_prompt, max_tokens=3000)
        data = json.loads(raw)
        body = _sanitize_body_html(data.get("body_html", ""))
        wc = _count_words(body)
        if wc < current_word_count:
            logger.warning(
                "fix_thin_content for %s produced fewer words (%d vs %d); skipping",
                page.canonical_path, wc, current_word_count,
            )
            return None
        return {
            "body_html": body,
            "word_count": wc,
            "usage": usage,
        }
    except Exception as exc:
        logger.warning("fix_thin_content failed for %s: %s", page.canonical_path, exc)
        return None


def _find_landing_page(db, path: str) -> LandingPage | None:
    """Look up a LandingPage by its canonical_path."""
    norm = "/" + path.lstrip("/").rstrip("/")
    return (
        db.query(LandingPage)
        .filter(LandingPage.canonical_path == norm)
        .first()
    )


def fix_content_issues(
    report: AuditReport,
    *,
    max_fixes: int = _MAX_FIXES_PER_RUN,
) -> dict:
    """Scan the audit report for fixable issues on LandingPage-backed
    pages and apply LLM-generated fixes.

    Returns a summary dict for the pipeline log."""
    if not settings.openai_api_key:
        return {"status": "skipped", "reason": "no OpenAI API key"}

    missing_h1_paths: list[str] = []
    thin_content_paths: list[tuple[str, int]] = []  # (path, word_count)

    for pa_key, pa in report.page_audits.items():
        if pa.status_code != 200:
            continue
        for f in pa.findings:
            if f.severity.value == "error" and f.category == "h1" and "No H1" in f.message:
                missing_h1_paths.append(pa.path)
            if f.category == "thin_content" and "Thin content" in f.message:
                thin_content_paths.append((pa.path, pa.body_word_count))

    if not missing_h1_paths and not thin_content_paths:
        return {"status": "ok", "fixed": 0, "reason": "no fixable issues"}

    init_db()
    db = SessionLocal()
    client = OpenAI(api_key=settings.openai_api_key)

    fixed = 0
    skipped = 0
    total_cost = 0.0
    details: list[dict] = []

    try:
        for path in missing_h1_paths:
            if fixed >= max_fixes:
                break

            page = _find_landing_page(db, path)
            if page is None:
                skipped += 1
                continue

            search_query = f"Venezuela {page.title or page.page_key} 2026"
            search_results = _web_search(search_query)

            result = _fix_missing_h1(client, page, search_results)
            if result is None:
                skipped += 1
                continue

            body_prefix = result["body_prefix"]
            if body_prefix and page.body_html:
                page.body_html = body_prefix + "\n" + page.body_html
                page.word_count = _count_words(page.body_html)
            elif body_prefix:
                page.body_html = body_prefix
                page.word_count = _count_words(page.body_html)

            page.updated_at = datetime.utcnow()
            db.commit()

            cost = result["usage"]["cost_usd"]
            total_cost += cost
            fixed += 1
            details.append({
                "path": path,
                "fix": "missing_h1",
                "h1": result["h1"],
                "cost_usd": cost,
            })
            logger.info("Fixed missing H1 on %s: %r", path, result["h1"])

        for path, word_count in thin_content_paths:
            if fixed >= max_fixes:
                break

            page = _find_landing_page(db, path)
            if page is None:
                skipped += 1
                continue

            if word_count >= 200:
                skipped += 1
                continue

            search_query = f"Venezuela {page.title or page.page_key} latest 2026"
            search_results = _web_search(search_query)

            result = _fix_thin_content(client, page, word_count, search_results)
            if result is None:
                skipped += 1
                continue

            page.body_html = result["body_html"]
            page.word_count = result["word_count"]
            page.updated_at = datetime.utcnow()
            db.commit()

            cost = result["usage"]["cost_usd"]
            total_cost += cost
            fixed += 1
            details.append({
                "path": path,
                "fix": "thin_content",
                "old_words": word_count,
                "new_words": result["word_count"],
                "cost_usd": cost,
            })
            logger.info(
                "Fixed thin content on %s: %d -> %d words",
                path, word_count, result["word_count"],
            )

        return {
            "status": "ok",
            "fixed": fixed,
            "skipped": skipped,
            "total_cost_usd": round(total_cost, 4),
            "details": details,
        }
    except Exception as exc:
        logger.exception("content fixer failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return {"status": "error", "error": str(exc), "fixed": fixed}
    finally:
        db.close()
