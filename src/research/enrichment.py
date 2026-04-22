"""
src/research/enrichment.py — pulls the data the dossier needs that
lives outside the OFAC SDN row itself: press release, news, corporate
affiliations, narrative summary.

Design rules:
  * Every fetch has a graceful fallback (a deep search link the user
    can click), so the page is *always* useful even if the network
    call fails or returns nothing.
  * 24-hour disk cache per (kind, slug) so we don't hammer upstreams
    on every page render and so repeat lookups feel instant.
  * Pure-stdlib HTTP via urllib so this module has no new deps.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).parent / "_cache"
_CACHE_TTL_SECONDS = 24 * 60 * 60

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 CaracasResearch/1.0"
)


# ──────────────────────────────────────────────────────────────────────
# Cache layer (file-per-key JSON)
# ──────────────────────────────────────────────────────────────────────


def _cache_path(kind: str, key: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", key)[:128]
    return _CACHE_DIR / f"{kind}__{safe}.json"


def _cache_get(kind: str, key: str, ttl: int = _CACHE_TTL_SECONDS) -> Optional[Any]:
    p = _cache_path(kind, key)
    if not p.exists():
        return None
    age = time.time() - p.stat().st_mtime
    if age > ttl:
        return None
    try:
        with p.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _cache_set(kind: str, key: str, value: Any) -> None:
    p = _cache_path(kind, key)
    try:
        with p.open("w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        logger.warning("enrichment cache write failed for %s/%s: %s", kind, key, exc)


def _http_get(url: str, *, timeout: float = 6.0) -> Optional[str]:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")
    except Exception as exc:
        logger.warning("enrichment http_get failed for %s: %s", url, exc)
        return None


# ──────────────────────────────────────────────────────────────────────
# 1) OFAC press release lookup
# ──────────────────────────────────────────────────────────────────────


_TREASURY_BASE = "https://home.treasury.gov"


def treasury_press_release_search_url(designation_date: Optional[str], topic: str = "venezuela") -> str:
    """Deep link to Treasury's press-release search filtered to a small
    date window around the designation. Always works, even if we have
    no curated URL — it lands Maria one click from the actual release."""
    if not designation_date:
        return f"{_TREASURY_BASE}/news/press-releases?combine={urllib.parse.quote(topic)}"
    try:
        d = datetime.fromisoformat(designation_date)
    except ValueError:
        return f"{_TREASURY_BASE}/news/press-releases?combine={urllib.parse.quote(topic)}"
    start = (d - timedelta(days=2)).strftime("%m/%d/%Y")
    end = (d + timedelta(days=2)).strftime("%m/%d/%Y")
    qs = urllib.parse.urlencode({
        "combine": topic,
        "start_date": start,
        "end_date": end,
    })
    return f"{_TREASURY_BASE}/news/press-releases?{qs}"


def resolve_press_release(curated_url: Optional[str], designation_date: Optional[str]) -> dict[str, Any]:
    """Returns {url, label, source, is_curated} — always populated."""
    if curated_url:
        return {
            "url": curated_url,
            "label": "OFAC press release for this designation",
            "source": "US Treasury",
            "is_curated": True,
        }
    return {
        "url": treasury_press_release_search_url(designation_date),
        "label": (
            f"Treasury press releases on Venezuela ({designation_date or 'all dates'}) — "
            f"find the official announcement"
        ),
        "source": "US Treasury (search)",
        "is_curated": False,
    }


# ──────────────────────────────────────────────────────────────────────
# 2) Templated narrative
# ──────────────────────────────────────────────────────────────────────


_EO_EXPLAINERS = {
    "VENEZUELA-EO13692": (
        "Executive Order 13692 (2015) authorizes sanctions against persons "
        "responsible for human-rights abuses, public-sector corruption, or "
        "actions undermining democratic processes in Venezuela."
    ),
    "VENEZUELA-EO13850": (
        "Executive Order 13850 (2018) authorizes sanctions on persons "
        "operating in Venezuela's gold sector, on current or former senior "
        "Venezuelan government officials, and on persons who have materially "
        "assisted, sponsored, or provided support to such activities."
    ),
    "VENEZUELA-EO13884": (
        "Executive Order 13884 (2019) blocks all property and interests in "
        "property of the Government of Venezuela that are in the United States, "
        "effectively expanding the sanctions to a comprehensive blocking program."
    ),
    "VENEZUELA": (
        "The omnibus VENEZUELA sanctions program covers a range of US Treasury "
        "actions targeting persons connected to the Maduro regime, illicit "
        "financial flows, and sanctions evasion networks."
    ),
}


def compose_narrative(
    *,
    display_name: str,
    raw_name: str,
    program: str,
    program_label: str,
    designation_date: Optional[str],
    parsed: dict[str, str],
    siblings: list[dict],
    related: list[dict],
    override: Optional[str] = None,
) -> str:
    """The 'WHY paragraph' Maria pastes into her EDD memo. Templated for
    deterministic output; can be overridden by curator-supplied text."""
    if override:
        return override.strip()

    bits: list[str] = []

    date_h = ""
    if designation_date:
        try:
            date_h = datetime.fromisoformat(designation_date).strftime("%B %-d, %Y")
        except ValueError:
            date_h = designation_date

    co_designated_today = [
        r["name"] for r in (related or [])
        if r.get("designation_date") == designation_date and r.get("name")
    ]

    lead = f"On {date_h or 'an undisclosed date'}, the US Treasury OFAC designated {display_name} as a Specially Designated National (SDN) under {program_label}."
    if co_designated_today:
        lead += (
            f" The designation was made simultaneously with "
            f"{_oxford(co_designated_today)}, indicating a coordinated action."
        )
    bits.append(lead)

    eo_explainer = _EO_EXPLAINERS.get(program, "")
    if eo_explainer:
        bits.append(eo_explainer)

    ident_parts: list[str] = []
    if parsed.get("nationality"):
        ident_parts.append(f"a {parsed['nationality']} national")
    birth_phrase = ""
    if parsed.get("dob") and parsed.get("pob"):
        birth_phrase = f"born {parsed['dob']} in {parsed['pob']}"
    elif parsed.get("dob"):
        birth_phrase = f"born {parsed['dob']}"
    elif parsed.get("pob"):
        birth_phrase = f"born in {parsed['pob']}"
    if birth_phrase:
        ident_parts.append(birth_phrase)

    id_clauses = []
    for label, k in (("Cédula", "cedula"), ("Passport", "passport"), ("RIF", "rif")):
        if parsed.get(k):
            id_clauses.append(f"{label} {parsed[k]}")

    if ident_parts or id_clauses:
        s = display_name
        if ident_parts:
            s += " is " + _oxford(ident_parts)
        if id_clauses:
            connector = ". Identifiers on the OFAC record include " if ident_parts else " is identified by "
            s += connector + _oxford(id_clauses)
        s += "."
        bits.append(s)

    bits.append(
        "The SDN listing freezes any of the subject's assets within US "
        "jurisdiction and prohibits US persons from engaging in transactions "
        "with the subject without an OFAC license. Any entity 50% or more "
        "owned by the subject is also considered blocked under OFAC's 50% Rule."
    )

    return " ".join(bits)


def _oxford(items: list[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + ", and " + items[-1]


# ──────────────────────────────────────────────────────────────────────
# 2b) LLM-backed narrative (with grounded facts + content-fp cache)
# ──────────────────────────────────────────────────────────────────────

_LLM_SYSTEM_PROMPT = """You are a senior sanctions/AML compliance analyst writing the "WHY" paragraph that opens an Enhanced Due Diligence (EDD) memo about an OFAC SDN-listed individual.

CRITICAL — anti-hallucination rules:
- Use ONLY the facts in the JSON payload. Do not invent or infer ANY dates, identifiers, relationships, allegations, biographical details, employer history, financial activity, or political affiliations.
- When describing the legal authority, quote the `executive_order_explainer` field as-is or paraphrase it conservatively. Do NOT cross-reference other Executive Orders. Do NOT add reasons that are not present in that explainer (e.g. do NOT add "undermining democratic institutions" if the explainer is about gold sector; do NOT add "human rights abuses" unless the explainer says so).
- Do NOT state or imply that the subject personally engaged in any specific conduct (mining gold, laundering money, corruption, etc.). The SDN listing alone does not establish individual conduct — only that OFAC designated them under a program with a particular scope. Phrase carefully: "designated under [program], which authorises sanctions on [scope from explainer]" — NOT "designated for [conduct]".
- Do not editorialize or use loaded language ("notorious", "infamous", "regime cronies", etc.). Neutral, evidentiary tone.
- Mention the OFAC 50% Rule's implication for owned entities exactly once, in plain English.
- If `co_designated_same_day` is non-empty, mention that the action was simultaneous with those named persons; do NOT speculate about the nature of the relationship beyond what's in `known_family_links`.
- 3 to 5 sentences total. ~110-170 words. No bullet points, no headers, no markdown.
- Write in prose suitable for direct paste into a compliance memo.

Output a JSON object with exactly two keys:
  "narrative": the paragraph (string)
  "tldr": a single sentence (max 25 words) stating which OFAC program designated the subject and the immediate compliance implication. Do NOT include alleged conduct in the TL;DR.
"""


def _llm_facts_payload(
    *,
    display_name: str,
    raw_name: str,
    program: str,
    program_label: str,
    designation_date: Optional[str],
    parsed: dict[str, str],
    siblings: list[dict],
    related: list[dict],
    public_role: Optional[str],
) -> dict[str, Any]:
    """Stable dict of grounded facts. Order matters because we hash
    this for cache keys — change order and you bust the cache."""
    co_designated_today = sorted({
        r.get("name", "") for r in (related or [])
        if r.get("designation_date") == designation_date and r.get("name")
    })
    family_links = sorted({
        r.get("name", "") for r in (related or [])
        if (r.get("relationship") or "").lower() in {"family", "sibling", "parent", "child"} and r.get("name")
    })
    return {
        "display_name": display_name,
        "raw_name": raw_name,
        "program_code": program,
        "program_label": program_label,
        "designation_date": designation_date,
        "public_role": public_role or None,
        "identifiers": {
            "nationality": parsed.get("nationality"),
            "dob": parsed.get("dob"),
            "pob": parsed.get("pob"),
            "cedula": parsed.get("cedula"),
            "passport": parsed.get("passport"),
            "rif": parsed.get("rif"),
        },
        "co_designated_same_day": co_designated_today,
        "known_family_links": family_links,
        "executive_order_explainer": _EO_EXPLAINERS.get(program, ""),
    }


def _facts_fingerprint(facts: dict[str, Any]) -> str:
    blob = json.dumps(facts, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def compose_narrative_llm(
    *,
    display_name: str,
    raw_name: str,
    program: str,
    program_label: str,
    designation_date: Optional[str],
    parsed: dict[str, str],
    siblings: list[dict],
    related: list[dict],
    public_role: Optional[str] = None,
    override: Optional[str] = None,
) -> dict[str, Any]:
    """Returns {"narrative": str, "tldr": str, "source": "override"|"llm"|"templated"}.

    Strategy:
      1. If curator override supplied, use it verbatim (source=override).
      2. Otherwise build a stable facts payload and check the disk cache
         keyed by SHA-256 of those facts. Same facts -> instant return,
         no LLM cost.
      3. Cache miss + API key set -> call LLM with strict system prompt,
         JSON response format. Cache the response.
      4. Any failure (no key, network error, malformed JSON) -> fall
         back to deterministic templated narrative.

    The cache file lives at src/research/_cache/narrative__<slug-or-fp>.json
    so wiping the cache directory triggers a clean regeneration."""

    if override:
        return {"narrative": override.strip(), "tldr": "", "source": "override"}

    facts = _llm_facts_payload(
        display_name=display_name,
        raw_name=raw_name,
        program=program,
        program_label=program_label,
        designation_date=designation_date,
        parsed=parsed,
        siblings=siblings,
        related=related,
        public_role=public_role,
    )
    fp = _facts_fingerprint(facts)
    cache_key = fp
    cached = _cache_get("narrative", cache_key, ttl=365 * 24 * 60 * 60)
    if cached and cached.get("narrative"):
        cached.setdefault("source", "llm")
        return cached

    fallback = {
        "narrative": compose_narrative(
            display_name=display_name,
            raw_name=raw_name,
            program=program,
            program_label=program_label,
            designation_date=designation_date,
            parsed=parsed,
            siblings=siblings,
            related=related,
            override=None,
        ),
        "tldr": "",
        "source": "templated",
    }

    # Lazy-import settings/OpenAI so this module never blocks at import
    # time when the openai package or env config is unavailable.
    try:
        from src.config import settings
    except Exception:
        return fallback

    api_key = (settings.openai_api_key or "").strip()
    if not api_key:
        return fallback

    try:
        from openai import OpenAI
    except Exception as exc:
        logger.warning("openai SDK unavailable for narrative LLM: %s", exc)
        return fallback

    model = getattr(settings, "openai_narrative_model", "gpt-4o-mini")
    user_msg = (
        "Write the WHY paragraph for this OFAC SDN designation. "
        "Use only the facts in the JSON below.\n\n"
        + json.dumps(facts, indent=2, ensure_ascii=False, default=str)
    )

    try:
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _LLM_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.2,
            max_tokens=400,
            response_format={"type": "json_object"},
            timeout=15,
        )
        raw = resp.choices[0].message.content or "{}"
        parsed_resp = json.loads(raw)
        narrative = (parsed_resp.get("narrative") or "").strip()
        tldr = (parsed_resp.get("tldr") or "").strip()
        if not narrative:
            raise ValueError("LLM returned empty narrative")

        result = {
            "narrative": narrative,
            "tldr": tldr,
            "source": "llm",
            "model": model,
            "facts_fingerprint": fp,
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }
        _cache_set("narrative", cache_key, result)
        return result
    except Exception as exc:
        logger.warning("LLM narrative generation failed for %s: %s", display_name, exc)
        return fallback


# ──────────────────────────────────────────────────────────────────────
# 3) Adverse media — Google News RSS
# ──────────────────────────────────────────────────────────────────────

# Scrubs Google News' redirect wrapper title format and tracking parameters.
_GOOGLE_REDIRECT_RE = re.compile(r"https?://news\.google\.com/.*?url=([^&]+)&", re.I)


def _strip_html(s: str) -> str:
    if not s:
        return ""
    text = re.sub(r"<[^>]+>", "", s)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _trim_snippet(snippet: str, source_name: str) -> str:
    """Google News descriptions repeat 'Title  Source' as the body text.
    Drop the trailing source-name copy and any duplicate of the title."""
    if not snippet:
        return ""
    s = snippet
    if source_name and s.endswith(source_name):
        s = s[: -len(source_name)].rstrip(" \xa0—-·|")
    return s.strip()


def fetch_news_google_rss(
    name: str,
    *,
    slug: str,
    max_items: int = 8,
    extra_query: str = "",
) -> list[dict[str, Any]]:
    """Returns up to `max_items` news items (title, source, date, url).
    Cached for 24h per slug. Returns empty list on failure."""
    cached = _cache_get("news", slug)
    if cached is not None:
        return cached

    query = f'"{name}"' + (f" {extra_query}" if extra_query else "")
    url = (
        "https://news.google.com/rss/search?"
        + urllib.parse.urlencode({"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    )
    body = _http_get(url, timeout=6.0)
    if not body:
        _cache_set("news", slug, [])
        return []

    items: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(body)
        for item in root.findall(".//item")[:max_items]:
            title_full = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub = (item.findtext("pubDate") or "").strip()
            desc = _strip_html(item.findtext("description") or "")

            # Google News titles are formatted "Headline - Publisher Name".
            source_name = ""
            title = title_full
            if " - " in title_full:
                title, source_name = title_full.rsplit(" - ", 1)

            iso_date = ""
            if pub:
                try:
                    iso_date = datetime.strptime(pub[:25], "%a, %d %b %Y %H:%M:%S").strftime("%Y-%m-%d")
                except ValueError:
                    iso_date = pub[:16]

            src_clean = source_name.strip() or "—"
            cleaned_snippet = _trim_snippet(desc, src_clean)
            # If the snippet is just the title repeated, suppress it so the UI
            # doesn't show redundant text below the headline.
            if cleaned_snippet.lower().startswith(title.strip().lower()):
                cleaned_snippet = ""
            items.append({
                "title": title.strip(),
                "url": link,
                "source": src_clean,
                "date": iso_date,
                "snippet": cleaned_snippet[:240],
            })
    except ET.ParseError as exc:
        logger.warning("google news RSS parse failed for %s: %s", slug, exc)

    _cache_set("news", slug, items)
    return items


# ──────────────────────────────────────────────────────────────────────
# 4) Corporate affiliations — deep links
# ──────────────────────────────────────────────────────────────────────


def corporate_affiliation_links(name: str, *, jurisdictions: Optional[list[str]] = None) -> list[dict[str, str]]:
    """One-click jumps into the corporate registries Maria already uses.
    Deliberately deep-link based: OpenCorporates closed their free
    officer-search API in 2018, and the alternatives (ICIJ, Sunbiz,
    LinkedIn) don't expose programmatic search either. The point is
    to consolidate the 5 separate searches Maria runs into one row."""
    q = urllib.parse.quote(name)
    out: list[dict[str, str]] = [
        {
            "label": "OpenCorporates — officer search",
            "url": f"https://opencorporates.com/officers?q={q}&utf8=%E2%9C%93",
            "why": "Officers/directors of any company globally indexed by OpenCorporates.",
            "covers": "Global, ~200M+ companies",
        },
        {
            "label": "ICIJ Offshore Leaks",
            "url": f"https://offshoreleaks.icij.org/search?q={q}&c=&j=&d=",
            "why": "Hits in Panama, Pandora, Paradise, Bahamas, and Offshore Leaks datasets.",
            "covers": "Offshore corporate structures",
        },
        {
            "label": "OpenSanctions — entity search",
            "url": f"https://www.opensanctions.org/search/?q={q}",
            "why": "Cross-reference against 200+ sanctions, PEP, and watchlist datasets.",
            "covers": "Sanctions + PEPs + leaks",
        },
        {
            "label": "Florida Sunbiz",
            "url": f"https://search.sunbiz.org/Inquiry/CorporationSearch/SearchResults?inquiryType=OfficerRegisteredAgentName&searchNameOrder={q}",
            "why": "Florida is the most common US state for LatAm-linked shell companies.",
            "covers": "Florida corporate registry",
        },
        {
            "label": "LinkedIn — people search",
            "url": f"https://www.linkedin.com/search/results/people/?keywords={q}",
            "why": "Identify any public-facing professional profile or affiliation.",
            "covers": "Professional profiles",
        },
    ]
    return out
