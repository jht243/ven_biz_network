"""
Page renderer for the secondary site pages — blog posts, blog index,
pillar / sector / sanctions / sources / tools — all of which share a
slim Jinja2 base layout (templates/_base.html.j2) and need their own
SEO + JSON-LD blocks.

Kept separate from src/report_generator.py because the home report is
written to disk + Supabase Storage on a cron schedule, while these
pages are server-rendered on every request from live DB rows.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.config import settings


# Map of known publisher hostnames to their canonical display name.
# Used by _source_display_name() to convert the raw canonical_source_url
# stored on a BlogPost row into a short, human-readable anchor label
# ("Google News" / "Reuters") for the "Primary source:" citation on
# the briefing page. Anything not in this map falls back to a
# capitalised bare-domain label ("bloomberg.com" → "Bloomberg"). See
# templates/blog_post.html.j2 — fixing the rendering of raw Google
# News RSS URLs was the driver for this helper.
_KNOWN_SOURCE_DOMAINS: dict[str, str] = {
    "news.google.com": "Google News",
    "reuters.com": "Reuters",
    "bloomberg.com": "Bloomberg",
    "ft.com": "Financial Times",
    "wsj.com": "The Wall Street Journal",
    "nytimes.com": "The New York Times",
    "washingtonpost.com": "The Washington Post",
    "economist.com": "The Economist",
    "bbc.com": "BBC",
    "bbc.co.uk": "BBC",
    "apnews.com": "Associated Press",
    "ap.org": "Associated Press",
    "aljazeera.com": "Al Jazeera",
    "cnbc.com": "CNBC",
    "forbes.com": "Forbes",
    "caracaschronicles.com": "Caracas Chronicles",
    "elpais.com": "El País",
    "eluniversal.com": "El Universal",
    "efe.com": "EFE",
    "efecto-cocuyo.com": "Efecto Cocuyo",
    "runrun.es": "Runrun.es",
    "tal-cual.com": "Tal Cual",
    "talcualdigital.com": "Tal Cual",
    "bancaynegocios.com": "Banca y Negocios",
    "elnacional.com": "El Nacional",
    "lapatilla.com": "La Patilla",
    "venezuelanalysis.com": "Venezuelanalysis",
    "ansalatina.com": "ANSA Latina",
    "treasury.gov": "US Treasury",
    "ofac.treasury.gov": "OFAC",
    "state.gov": "US State Department",
    "federalregister.gov": "US Federal Register",
    "gdelt.org": "GDELT",
    "sec.gov": "SEC EDGAR",
}


def _source_display_name(url: str | None) -> str:
    """Map a canonical source URL to a short, human-readable publisher label.

    Used as the visible anchor text for the "Primary source:" citation
    on briefing pages so the reader sees "Google News" instead of a
    400-character Google News RSS redirect URL. The underlying URL is
    preserved verbatim in the href and in the page's JSON-LD citation.
    """
    if not url:
        return "source"
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return "source"
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return "source"
    if host in _KNOWN_SOURCE_DOMAINS:
        return _KNOWN_SOURCE_DOMAINS[host]
    # Try stripping one subdomain (m.reuters.com → reuters.com).
    parts = host.split(".")
    if len(parts) >= 3:
        tail = ".".join(parts[-2:])
        if tail in _KNOWN_SOURCE_DOMAINS:
            return _KNOWN_SOURCE_DOMAINS[tail]
    # Fallback: capitalise the registrable-domain stem.
    if len(parts) >= 2:
        stem = parts[-2]
    else:
        stem = parts[0]
    return stem.replace("-", " ").title()


logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml", "j2"]),
)

# Jinja filter that walks an HTML fragment and links the first mention
# of each known Venezuelan power figure to /people/<slug>, opening in
# a new tab. Registered globally so it composes with `| safe` on any
# user-generated HTML field — primarily blog_posts.body_html on
# /briefing/<slug> pages. See src/data/people.py for the algorithm.
def _link_people_filter(html: str) -> str:
    if not html:
        return html
    from src.data.people import link_people_in_html
    return link_people_in_html(html)


_env.filters["link_people"] = _link_people_filter


def _seo_title_filter(s: str, max_len: int = 70) -> str:
    """Clamp a title at word boundary within max_len chars. No ellipsis
    — Google adds its own and a clean word ending reads better."""
    if not s:
        return s
    s = " ".join(str(s).split())
    if len(s) <= max_len:
        return s
    return s[:max_len].rsplit(" ", 1)[0].rstrip(" ,;:—-")


def _seo_desc_filter(s: str, max_len: int = 160) -> str:
    """Clamp a description at sentence/word boundary within max_len
    chars. Appends '...' if truncated to signal continuation."""
    if not s:
        return s
    s = " ".join(str(s).split())
    if len(s) <= max_len:
        return s
    budget = max_len - 3  # room for "..."
    # Try to cut at a sentence boundary first
    for sep in (". ", "— ", "; ", ", "):
        idx = s[:budget].rfind(sep)
        if idx > budget // 2:
            return s[: idx + len(sep)].rstrip() + "..."
    # Fall back to word boundary
    cut = s[:budget].rsplit(" ", 1)[0].rstrip(" ,;:—-")
    return cut + "..."


_env.filters["seo_title"] = _seo_title_filter
_env.filters["seo_desc"] = _seo_desc_filter


def _base_url() -> str:
    url = (settings.canonical_site_url or "").strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


# Google's SERP truncates titles around 60 chars (varies by font width)
# and meta descriptions around 155-160 chars on desktop, 120 on mobile.
# Going over those budgets means the most search-relevant suffix
# (year, "for Investors", sector qualifier) gets cut. We trim slightly
# below the desktop max to leave headroom for the favicon + brand
# Google sometimes appends, and to stay safe on the mobile description
# budget when the snippet is short.
_SERP_TITLE_MAX = 60
_SERP_DESC_MAX = 155


def _serp_truncate(s: str | None, limit: int) -> str:
    """
    Hard cap a string at `limit` chars, cutting at the last word
    boundary inside the budget so we never publish a half-word like
    "Investme…". No ellipsis appended — Google does not visually
    reward "…" and a clean word ending reads as the canonical
    short form rather than a truncation artifact.
    """
    if not s:
        return ""
    s = " ".join(str(s).split())  # collapse internal whitespace
    if len(s) <= limit:
        return s
    cut = s[:limit].rsplit(" ", 1)[0]
    # Strip trailing punctuation that looks weird mid-sentence.
    return cut.rstrip(" ,;:—-")


def _iso(d: date | datetime | None) -> str:
    if d is None:
        return ""
    if isinstance(d, datetime):
        return d.replace(tzinfo=timezone.utc).isoformat()
    return datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc).isoformat()


def render_blog_post(post, *, related: list | None = None) -> str:
    """Render a single BlogPost row to HTML with full NewsArticle JSON-LD.

    Uses NewsArticle (not BlogPosting) so briefings are eligible for the
    Google News Top Stories carousel. NewsArticle is a strict subtype of
    Article that Google specifically scans for time-sensitive news content.
    """
    base = _base_url()
    canonical = f"{base}/briefing/{post.slug}"
    # Prefer the per-briefing OG card (rendered at creation time and
    # served from /og/briefing/<slug>.png). Fall back to the generic
    # site-wide tile for any briefing that hasn't been rendered yet.
    has_og_bytes = bool(getattr(post, "og_image_bytes", None))
    og_image = (
        f"{base}/og/briefing/{post.slug}.png"
        if has_og_bytes
        else f"{base}/static/og-image.png?v=3"
    )

    keywords = post.keywords_json or []
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]

    # SERP-budget enforcement: blog posts pre-2026-04 were shipped with
    # raw post.title (up to 110 chars) and raw post.summary (up to 300
    # chars) into <title> and meta description. The April 2026 SEO
    # audit found 100% of sampled posts truncated in SERPs, with the
    # most search-relevant suffix being the part Google cut (year,
    # sector qualifier, "for Investors"). _serp_truncate caps at the
    # desktop SERP budgets and cuts at a word boundary so we never
    # publish a mid-word truncation.
    seo_title = _serp_truncate(post.title, _SERP_TITLE_MAX)
    seo_description = _serp_truncate(post.summary or post.subtitle, _SERP_DESC_MAX)

    seo = {
        "title": seo_title,
        "description": seo_description,
        "keywords": ", ".join(keywords) if keywords else "",
        "news_keywords": ", ".join(keywords[:10]) if keywords else "",
        "canonical": canonical,
        "site_name": settings.site_name,
        "site_url": base,
        "locale": settings.site_locale,
        "og_image": og_image,
        "og_type": "article",
        "published_iso": _iso(post.published_date),
        "modified_iso": _iso(post.updated_at or post.created_at or post.published_date),
        "section": (post.primary_sector or "Venezuela investment").replace("_", " ").title(),
        "article_tags": keywords[:10],
    }

    breadcrumbs = {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{base}/"},
            {"@type": "ListItem", "position": 2, "name": "Analysis", "item": f"{base}/briefing"},
            {"@type": "ListItem", "position": 3, "name": post.title, "item": canonical},
        ],
    }

    # JSON-LD `headline` has a hard Google policy cap of 110 chars for
    # NewsArticle eligibility. We use the FULL post.title here (capped
    # cleanly at 110 with word-boundary truncation), not the 60-char
    # SERP title — Google reads headline as a structured-data signal
    # for Top Stories carousel ranking, separate from the <title> tag
    # it shows in SERPs.
    news_article = {
        "@type": "NewsArticle",
        "@id": f"{canonical}#article",
        "url": canonical,
        "mainEntityOfPage": {"@type": "WebPage", "@id": canonical, "name": post.title},
        "headline": _serp_truncate(post.title, 110),
        "description": _serp_truncate(post.summary, 250),
        "image": [og_image],
        "datePublished": _iso(post.published_date),
        "dateModified": _iso(post.updated_at or post.created_at or post.published_date),
        "wordCount": post.word_count or 0,
        "author": {"@type": "Organization", "name": settings.site_name, "url": f"{base}/"},
        "publisher": {
            "@type": "Organization",
            "name": settings.site_name,
            "url": f"{base}/",
            "logo": {"@type": "ImageObject", "url": og_image, "width": 1200, "height": 630},
        },
        "keywords": keywords,
        "articleSection": seo["section"],
        "inLanguage": "en-US",
        "isAccessibleForFree": True,
    }
    if post.canonical_source_url:
        news_article["citation"] = post.canonical_source_url

    jsonld = json.dumps(
        {"@context": "https://schema.org", "@graph": [breadcrumbs, news_article]},
        ensure_ascii=False,
    )

    # Pull the stored "Key takeaways" bullets. Persisted in
    # blog_posts.takeaways_json at generation time by
    # src/blog_generator.py, and backfilled for legacy posts by
    # scripts/backfill_takeaways.py. Defensive: accept either a
    # plain list or a JSON-encoded string (some older DB engines
    # stored JSON columns as strings), and coerce every element
    # to a trimmed plain-text bullet before it hits the template.
    raw_takeaways = getattr(post, "takeaways_json", None) or []
    if isinstance(raw_takeaways, str):
        try:
            raw_takeaways = json.loads(raw_takeaways)
        except Exception:
            raw_takeaways = [raw_takeaways]
    takeaways: list[str] = []
    if isinstance(raw_takeaways, list):
        for t in raw_takeaways:
            if not isinstance(t, str):
                continue
            s = t.strip()
            if s:
                takeaways.append(s)

    # Resolve a short, human-readable anchor label for the "Primary
    # source:" citation on the rendered page. Keeps the gnarly Google
    # News RSS URLs (and every other direct publisher URL) behind a
    # clean "Google News" / "Reuters" / etc. anchor instead of dumping
    # 400 characters of opaque query string into the citation box.
    source_label = _source_display_name(getattr(post, "canonical_source_url", None))

    template = _env.get_template("blog_post.html.j2")
    return template.render(
        post=post,
        related=related or [],
        takeaways=takeaways,
        source_label=source_label,
        seo=seo,
        jsonld=jsonld,
        current_year=date.today().year,
    )


def render_blog_index(posts: Iterable) -> str:
    base = _base_url()
    canonical = f"{base}/briefing"

    posts_list = list(posts)

    seo = {
        "title": "Venezuela News Today: Sanctions & Economy (2026)",
        "description": (
            "Venezuela news — OFAC sanctions, Asamblea Nacional, Gaceta "
            "Oficial decrees, economic data, and capital flows. Published "
            "twice daily."
        ),
        "keywords": (
            "Venezuela news, Venezuela news today, Venezuela latest news, "
            "Venezuela sanctions news, Venezuela economy news, "
            "invest in Venezuela, OFAC Venezuela analysis, "
            "Caracas investment briefing, Venezuelan sectors"
        ),
        "canonical": canonical,
        "site_name": settings.site_name,
        "site_url": base,
        "locale": settings.site_locale,
        "og_image": f"{base}/static/og-image.png?v=3",
        "og_type": "website",
        "published_iso": _iso(datetime.utcnow()),
        "modified_iso": _iso(datetime.utcnow()),
    }

    item_list = {
        "@type": "ItemList",
        "name": "Venezuelan investment briefings",
        "itemListOrder": "https://schema.org/ItemListOrderDescending",
        "numberOfItems": len(posts_list),
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": idx,
                "name": p.title,
                "url": f"{base}/briefing/{p.slug}",
            }
            for idx, p in enumerate(posts_list[:50], start=1)
        ],
    }
    breadcrumbs = {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{base}/"},
            {"@type": "ListItem", "position": 2, "name": "Analysis", "item": canonical},
        ],
    }
    jsonld = json.dumps(
        {"@context": "https://schema.org", "@graph": [breadcrumbs, item_list]},
        ensure_ascii=False,
    )

    template = _env.get_template("blog_index.html.j2")
    return template.render(
        posts=posts_list,
        seo=seo,
        jsonld=jsonld,
        current_year=date.today().year,
    )


def _sdn_actors_for_sector(sector_slug: str, *, limit: int = 10) -> list:
    """Best-effort list of OFAC SDN profiles relevant to a sector page.

    We use the program-to-sector mapping from cluster_topology to flip
    the relationship: for any sector that is the canonical sector for
    one or more OFAC programs, return the SDN profiles designated under
    those programs (capped at `limit`, prioritising individuals).

    Returns an empty list for sectors with no program mapping (e.g.
    /sectors/agriculture isn't bound to a Venezuela-program EO), in
    which case the template skips the section. This means we only
    surface the cross-cluster section when it carries real signal.
    """
    from src.data.sdn_profiles import list_all_profiles
    from src.seo.cluster_topology import program_to_sector_links

    target_path = f"/sectors/{sector_slug}"
    relevant_programs = {
        prog for prog, link in program_to_sector_links().items()
        if link.path == target_path
    }
    if not relevant_programs:
        return []

    # Sort by bucket priority (individuals first — they're the searchable
    # name queries from GSC), then alphabetically.
    bucket_order = {"individuals": 0, "entities": 1, "vessels": 2, "aircraft": 3}
    candidates = [
        p for p in list_all_profiles()
        if (p.program or "").upper() in relevant_programs
    ]
    candidates.sort(key=lambda p: (bucket_order.get(p.bucket, 9), p.raw_name.upper()))
    return candidates[:limit]


# Path-keyed SEO overrides for high-impression landing pages whose H1
# (page.title) is editorially rich but truncates badly in SERPs. Each
# entry can carry a tighter SERP `title` (≤65 chars), a higher-CTR
# `description` (≤160 chars), and an optional `faq` list emitted as
# both visible HTML and FAQPage JSON-LD. Adding a path here lets us
# tune SERP copy independently of the on-page H1 / body content.
#
# Why overrides instead of editing LandingPage rows directly:
#   - SEO copy is reviewable / version-controlled in code, so changes
#     can ride a normal PR cycle and a/b iterations are diffable.
#   - The H1 stays descriptive (good for on-page UX) while the SERP
#     title competes on CTR vocabulary (count, year-month freshness,
#     query-matching keywords, US-authority signal).
#   - DB stays the source of truth for body content.
_LANDING_PAGE_SEO_OVERRIDES: dict[str, dict] = {
    # GSC April 2026: 102 impressions, 0 clicks, position ~7. Round-1
    # title = H1 ("What Are OFAC Sanctions on Venezuela? A Plain-
    # English Guide") — wasted SERP real estate. Round-2 leads with
    # the EO numbers compliance officers literally search for, plus
    # year-tag freshness. FAQ block addresses the four sub-questions
    # we see clustered in adjacent GSC queries (definition, who is
    # sanctioned, General Licenses, who must comply).
    # Round 3 (Apr 2026): pivot back toward natural-language queries.
    # 28d of GSC data shows the live impressions are coming from
    # "ofac sanctions on venezuela" (pos 18), "ofac venezuela licenses"
    # (pos 10), and "ofac venezuela sanctions program summary" (pos 10)
    # — none of them include the EO numbers Round 2 optimised for. We
    # exact-match the dominant query in the title and keep the EO/GL
    # numerics in the description (Google still scores them) plus the
    # body, so compliance officers searching technically still convert.
    # Round 4 (Apr 2026): 28d GSC shows clicks on "ofac venezuela licenses",
    # "ofac venezuela sanctions program summary", and the broad head
    # "ofac sanctions on venezuela" (positions 5–10). We front-load the exact
    # query tokens ("Venezuela", "General Licenses", "programs") in the
    # title; EO numbers and SDN count stay in the description + body.
    "/explainers/what-are-ofac-sanctions-on-venezuela": {
        "title": "OFAC & Venezuela: Sanctions Programs & General Licenses (2026)",
        "description": (
            "U.S. Treasury OFAC programs for Venezuela, executive orders, "
            "VENEZUELA + EO 13692/13850/13884, General Licenses, who must "
            "comply, and live SDN counts. Independent guide — 2026."
        ),
        "faq": [
            (
                "What are OFAC sanctions on Venezuela?",
                "OFAC (the Office of Foreign Assets Control, a unit of the "
                "US Treasury) administers four overlapping programs targeting "
                "Venezuelan officials, state companies, and assets: the "
                "VENEZUELA omnibus program, EO 13692 (human rights and "
                "corruption, 2015), EO 13850 (gold sector and individual "
                "officials, 2018), and EO 13884 (Government of Venezuela "
                "block, 2019). Together they currently designate over 400 "
                "individuals, entities, vessels, and aircraft."
            ),
            (
                "Who is currently sanctioned by OFAC under the Venezuela programs?",
                "As of 2026, OFAC has roughly 410 active Venezuela-program "
                "designations: ~190 individuals (mainly current and former "
                "regime officials, military leaders, and judges), ~100 "
                "entities (state-owned companies, holding companies, and "
                "shell entities), ~30 vessels, and ~87 aircraft. Browse the "
                "live A–Z list at /sanctions/individuals or /sanctions-tracker."
            ),
            (
                "What is a General License under OFAC's Venezuela sanctions?",
                "A General License (GL) is a standing OFAC authorization that "
                "lets US persons engage in specific transactions that would "
                "otherwise be prohibited. For Venezuela, the most-used GLs "
                "cover personal remittances, agricultural and medical "
                "exports, telecommunications and internet services, NGO "
                "humanitarian work, and certain wind-down activities. Each "
                "GL has detailed scope limits — see OFAC's General Licenses "
                "page for current text."
            ),
            (
                "Who has to comply with OFAC Venezuela sanctions?",
                "All US persons (US citizens, US permanent residents, "
                "US-incorporated entities, and anyone physically in the "
                "United States) must comply, regardless of where the "
                "transaction occurs. Foreign companies that use US dollars, "
                "US banks, or US persons in their transaction chain face "
                "secondary sanctions risk. Banks, investment advisors, and "
                "exporters must screen counterparties against the SDN list "
                "at every transaction."
            ),
            (
                "How often is the OFAC Venezuela sanctions list updated?",
                "The OFAC SDN list is updated continuously by the US "
                "Treasury — sometimes daily — as new designations and "
                "delistings are published. Caracas Research refreshes its "
                "live tracker twice daily from the official OFAC SDN feed, "
                "so the counts and profiles you see reflect the live list "
                "as of the date stamp on each page."
            ),
        ],
    },
    # GSC April 2026: 91 impressions, 0 clicks. Original title leads
    # with "Legal Framework" — investor-intent searchers want
    # commodities (gold/coltan/diamonds) and ROI signals first. New
    # title front-loads the three commodities Venezuela is actually
    # mined for, with year-tag and the OFAC overlay every investor
    # asks about.
    "/sectors/mining": {
        "title": "Venezuela Mining 2026: Gold, Coltan & Diamond Under OFAC",
        "description": (
            "Where Venezuela's gold, coltan, and diamond opportunities "
            "still exist in 2026 under the Organic Mining Law and OFAC "
            "sanctions. Investor diligence guide."
        ),
    },

    # ─────────────────────────────────────────────────────────────────
    # April 2026 sector + explainer SEO pass.
    #
    # Every sector page was previously generated by the LLM with the
    # same template ("Venezuela X Sector: Regulatory Framework, Sanctions,
    # Deal Flow, Risks"). Result: titles 66-73 chars (truncated at ~60
    # in SERPs, losing the most search-relevant suffix), descriptions
    # 175-192 chars (truncated at ~155), and 13 pages reading nearly
    # identical to Google's quality classifier — diluting per-page
    # ranking authority. Each override below:
    #   - Drops the redundant "Sector" word ("Banking" already implies
    #     a sector, frees ~7 chars of title budget).
    #   - Front-loads the highest-intent search vocabulary for that
    #     specific industry (PDVSA + Chevron for oil&gas, BCV for
    #     banking, CONATEL for telecom, etc.) instead of generic
    #     "regulation" / "deal flow" boilerplate.
    #   - Adds a year-tag for freshness without burning daily
    #     authority signal.
    #   - Stays inside _SERP_TITLE_MAX (60) and _SERP_DESC_MAX (155).
    # ─────────────────────────────────────────────────────────────────

    "/sectors/oil-gas": {
        "title": "Venezuela Oil & Gas 2026: PDVSA, Chevron License & OFAC",
        "description": (
            "Venezuela's oil & gas sector under OFAC sanctions: PDVSA "
            "structure, Chevron general license, hydrocarbons law reform, "
            "and investor due diligence (2026)."
        ),
    },
    "/sectors/banking": {
        "title": "Venezuela Banking 2026: BCV, OFAC & Correspondent Risk",
        "description": (
            "Venezuela's banking sector under US Treasury OFAC: BCV "
            "oversight, compliance pathways, correspondent risk, and "
            "investor due diligence (2026)."
        ),
    },
    "/sectors/energy": {
        "title": "Venezuela Energy 2026: Power Grid, OFAC & PDVSA Reform",
        "description": (
            "Venezuela's energy sector in 2026: power grid status, "
            "hydrocarbons law reform, PDVSA, OFAC licensing exposure, "
            "and investor due diligence."
        ),
    },
    "/sectors/real-estate": {
        "title": "Venezuela Real Estate 2026: Caracas Property & FX Risk",
        "description": (
            "Venezuela real estate market in 2026: Caracas property law, "
            "tenancy rules, foreign-exchange risk, OFAC exposure, and "
            "investor due diligence."
        ),
    },
    "/sectors/sanctions": {
        "title": "Venezuela Sanctions 2026: OFAC Licenses & Risk Map",
        "description": (
            "Venezuela sanctions landscape (2026): OFAC licensing "
            "pathways, EU dynamics, local legal reforms, and "
            "compliance-focused investor due diligence."
        ),
    },
    "/sectors/telecom": {
        "title": "Venezuela Telecom 2026: CONATEL, OFAC & Investor Risk",
        "description": (
            "Venezuela's telecom sector in 2026: CONATEL rules, market "
            "structure, OFAC sanctions exposure, and an investor due "
            "diligence guide."
        ),
    },
    "/sectors/agriculture": {
        "title": "Venezuela Agriculture 2026: Food Production & Sanctions",
        "description": (
            "Venezuela agriculture in 2026: food production policy, land "
            "tenure, OFAC exposure, and investor due diligence on "
            "agribusiness deals."
        ),
    },
    "/sectors/diplomatic": {
        "title": "Venezuela Diplomacy 2026: US, EU & Sanctions Engagement",
        "description": (
            "Venezuela's diplomatic landscape in 2026: US, EU, and Latin "
            "American engagement under OFAC sanctions, plus investor "
            "access considerations."
        ),
    },
    "/sectors/economic": {
        "title": "Venezuela Economic Sector: Investor Risk & OFAC (2026)",
        "description": (
            "Economic sector analysis for Venezuela investors — FX policy, "
            "inflation risk, OFAC sanctions exposure, and deal flow "
            "signals to track monthly."
        ),
    },
    "/sectors/governance": {
        "title": "Venezuela Governance 2026: Government & Investor Risk",
        "description": (
            "Venezuela governance in 2026: institutional structure, "
            "administrative reforms, OFAC exposure, and political risk "
            "for foreign investors."
        ),
    },
    "/sectors/legal": {
        "title": "Venezuela Legal System 2026: Courts, Reform & Risk",
        "description": (
            "Venezuela's legal system in 2026: court structure, civil-law "
            "reforms, sanctions interactions, and an investor due "
            "diligence guide for foreign deals."
        ),
    },
    "/sectors/tourism": {
        "title": "Venezuela Tourism 2026: Travel Status & Sanctions Risk",
        "description": (
            "Venezuela tourism in 2026: travel advisory status, OFAC "
            "sanctions exposure for hospitality operators, and investor "
            "due diligence on tourism deals."
        ),
    },

    # Explainers — 4 of 5 had truncated <title> or <meta description>
    # in the April 2026 audit. (The 5th, what-are-ofac-sanctions-on-
    # venezuela, is overridden above.)

    "/explainers": {
        "title": "Venezuela Investor Explainers — Plain-English Guides",
        "description": (
            "Plain-English guides for foreign investors on Venezuela: "
            "OFAC sanctions, the BCV, the bolívar, buying bonds, and "
            "operating in Caracas."
        ),
    },
    "/explainers/doing-business-in-caracas": {
        "title": "Doing Business in Caracas 2026: Foreign Investor Guide",
        "description": (
            "Practical 2026 guide for foreign investors operating in "
            "Caracas: legal setup, banking, OFAC compliance, hiring, "
            "contracts, and day-to-day risk."
        ),
    },
    "/explainers/how-to-buy-venezuelan-bonds": {
        "title": "How to Buy Venezuelan Sovereign & PDVSA Bonds (2026)",
        "description": (
            "How investors buy Venezuelan sovereign and PDVSA bonds in "
            "2026: trading venues, custody, OFAC constraints, pricing, "
            "and key risks."
        ),
    },
    "/explainers/venezuelan-bolivar-explained": {
        "title": "Venezuelan Bolívar 2026: History, Devaluations & FX Rate",
        "description": (
            "What the Venezuelan bolívar is, why it has devalued "
            "repeatedly, and how official vs market exchange rates "
            "work today — with a 2026 monitoring guide."
        ),
    },
    "/explainers/what-is-the-banco-central-de-venezuela": {
        "title": "Banco Central de Venezuela (BCV): A 2026 Investor Guide",
        "description": (
            "Plain-English 2026 guide to the Banco Central de Venezuela "
            "(BCV): what it does, how it sets FX rates, and why it "
            "matters for foreign investors."
        ),
    },
    "/invest-in-venezuela": {
        "title": "Invest in Venezuela 2026: Stock Market, Bonds & OFAC",
        "description": (
            "How to invest in Venezuela in 2026 — Caracas stock exchange "
            "(BVC), PDVSA bonds, oil & gas, real estate, mining. OFAC "
            "compliance guide."
        ),
        "faq": [
            (
                "Does Venezuela have a stock market?",
                "Yes. The Bolsa de Valores de Caracas (BVC) is Venezuela's "
                "stock exchange, operating since 1947. It lists approximately "
                "30 companies, mainly in banking, telecoms, and manufacturing. "
                "Trading volumes are very thin by international standards, and "
                "foreign participation requires a local brokerage account and "
                "BCV registration. Most institutional foreign investment in "
                "Venezuela occurs through direct deals, JVs, or bond markets "
                "rather than the equity exchange."
            ),
            (
                "How can foreigners invest in Venezuela in 2026?",
                "Foreign investment is possible through several channels: "
                "OFAC-licensed oil and gas joint ventures (GL 49A–52), "
                "distressed sovereign and PDVSA debt on secondary markets, "
                "direct real estate purchases in bolívars or USD, and minority "
                "stakes in local companies. All US persons must verify OFAC "
                "compliance before any transaction. The January 2026 Foreign "
                "Investment Promotion Law streamlines registration for new "
                "entrants."
            ),
            (
                "What are the biggest risks of investing in Venezuela?",
                "Key risks include OFAC sanctions compliance (fines up to "
                "$20M per violation), foreign exchange controls and repatriation "
                "restrictions, legal uncertainty and weak rule of law, political "
                "instability during the transition period, and operational "
                "challenges including infrastructure gaps and skilled labor "
                "shortages."
            ),
            (
                "Can you buy Venezuelan bonds?",
                "Venezuelan sovereign bonds and PDVSA bonds trade on secondary "
                "distressed-debt markets at 5–15 cents on the dollar. US persons "
                "face OFAC restrictions on bonds issued after August 2017. "
                "Pre-2017 bonds are tradeable subject to standard compliance "
                "screening. A comprehensive debt restructuring is expected but "
                "has not yet been formally announced."
            ),
        ],
    },
}


def render_landing_page(page, *, recent_briefings: list | None = None) -> str:
    """Render a LandingPage row (pillar / sector / explainer) to HTML."""
    base = _base_url()
    canonical = f"{base}{page.canonical_path}"
    og_image = f"{base}/static/og-image.png?v=3"

    keywords = page.keywords_json or []
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]

    override = _LANDING_PAGE_SEO_OVERRIDES.get(page.canonical_path, {})
    seo_title = override.get("title") or (page.title or "")[:110]
    seo_description = (
        override.get("description")
        or (page.summary or page.subtitle or "")[:300]
    )
    faq_block = override.get("faq") or []

    seo = {
        "title": seo_title,
        "description": seo_description,
        "keywords": ", ".join(keywords) if keywords else "",
        "canonical": canonical,
        "site_name": settings.site_name,
        "site_url": base,
        "locale": settings.site_locale,
        "og_image": og_image,
        "og_type": "article",
        "published_iso": _iso(page.created_at or page.last_generated_at),
        "modified_iso": _iso(page.last_generated_at or page.updated_at),
        "section": page.page_type.title(),
        "article_tags": keywords[:10],
    }

    schema_type = "WebPage"
    if page.page_type == "sector":
        schema_type = "CollectionPage"
    elif page.page_type == "pillar":
        schema_type = "Article"
    elif page.page_type == "explainer":
        schema_type = "Article"

    breadcrumbs_items = [
        {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{base}/"},
    ]
    if page.page_type == "sector":
        breadcrumbs_items.append(
            {"@type": "ListItem", "position": 2, "name": "Invest in Venezuela", "item": f"{base}/invest-in-venezuela"}
        )
        breadcrumbs_items.append(
            {"@type": "ListItem", "position": 3, "name": page.title, "item": canonical}
        )
    else:
        breadcrumbs_items.append(
            {"@type": "ListItem", "position": 2, "name": page.title, "item": canonical}
        )

    breadcrumbs = {"@type": "BreadcrumbList", "itemListElement": breadcrumbs_items}

    main_obj = {
        "@type": schema_type,
        "@id": f"{canonical}#main",
        "url": canonical,
        "name": page.title,
        "headline": (page.title or "")[:110],
        "description": (page.summary or "")[:300],
        "image": [og_image],
        "inLanguage": "en-US",
        "datePublished": _iso(page.created_at or page.last_generated_at),
        "dateModified": _iso(page.last_generated_at or page.updated_at),
        "wordCount": page.word_count or 0,
        "author": {"@type": "Organization", "name": settings.site_name, "url": f"{base}/"},
        "publisher": {
            "@type": "Organization",
            "name": settings.site_name,
            "url": f"{base}/",
            "logo": {"@type": "ImageObject", "url": og_image, "width": 1200, "height": 630},
        },
        "keywords": keywords,
        "isAccessibleForFree": True,
    }

    graph_nodes: list = [breadcrumbs, main_obj]
    # Emit FAQPage JSON-LD whenever the override carries an FAQ list.
    # The same list is rendered as visible HTML in the template so the
    # rich result is honored (Google requires the structured data to
    # match user-visible content).
    if faq_block:
        graph_nodes.append({
            "@type": "FAQPage",
            "@id": f"{canonical}#faq",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": q,
                    "acceptedAnswer": {"@type": "Answer", "text": a[:500]},
                }
                for q, a in faq_block
            ],
        })

    jsonld = json.dumps(
        {"@context": "https://schema.org", "@graph": graph_nodes},
        ensure_ascii=False,
    )

    from src.seo.cluster_topology import build_cluster_ctx
    cluster_ctx = build_cluster_ctx(page.canonical_path)

    # For sector landing pages, surface a "Sanctioned actors in this
    # sector" section pulling profiles from the new SDN cluster. This
    # is the cross-cluster bridge from /sectors/<slug> back into the
    # sanctions cluster — the second half of the reciprocal link the
    # SDN profile pages already make to /sectors/<slug>.
    sector_sdn_actors: list = []
    if page.page_type == "sector":
        sector_slug = page.canonical_path.rsplit("/", 1)[-1]
        sector_sdn_actors = _sdn_actors_for_sector(sector_slug)

    template = _env.get_template("landing.html.j2")
    return template.render(
        page=page,
        recent_briefings=recent_briefings or [],
        sector_sdn_actors=sector_sdn_actors,
        cluster_ctx=cluster_ctx,
        seo=seo,
        jsonld=jsonld,
        faq_block=faq_block,
        current_year=date.today().year,
    )


def render_blog_feed_xml(posts: Iterable) -> str:
    """Atom 1.0 feed for the /briefing/feed.xml route."""
    from xml.sax.saxutils import escape as _x

    base = _base_url()
    posts_list = list(posts)
    updated_iso = _iso(posts_list[0].updated_at or posts_list[0].created_at) if posts_list else _iso(datetime.utcnow())

    parts = ['<?xml version="1.0" encoding="UTF-8"?>']
    parts.append('<feed xmlns="http://www.w3.org/2005/Atom">')
    parts.append(f"<title>{_x(settings.site_name)} — Venezuelan investment analysis</title>")
    parts.append(f'<link href="{base}/briefing/feed.xml" rel="self" type="application/atom+xml"/>')
    parts.append(f'<link href="{base}/briefing" rel="alternate" type="text/html"/>')
    parts.append(f"<id>{base}/briefing</id>")
    parts.append(f"<updated>{updated_iso}</updated>")
    parts.append(
        "<subtitle>OFAC sanctions, Asamblea Nacional legislation, sector capital "
        "flows — twice-daily Venezuelan investment briefings.</subtitle>"
    )
    parts.append(
        "<author><name>{name}</name><uri>{base}/</uri></author>".format(
            name=_x(settings.site_name), base=base
        )
    )

    for p in posts_list[:50]:
        url = f"{base}/briefing/{p.slug}"
        parts.append("<entry>")
        parts.append(f"<title>{_x(p.title or '')}</title>")
        parts.append(f'<link href="{url}"/>')
        parts.append(f"<id>{url}</id>")
        parts.append(f"<published>{_iso(p.published_date)}</published>")
        parts.append(f"<updated>{_iso(p.updated_at or p.created_at or p.published_date)}</updated>")
        if p.summary:
            parts.append(f"<summary>{_x(p.summary)}</summary>")
        if p.body_html:
            parts.append(
                f'<content type="html"><![CDATA[{p.body_html}]]></content>'
            )
        parts.append("</entry>")
    parts.append("</feed>")
    return "".join(parts)
