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

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.config import settings


logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml", "j2"]),
)


def _base_url() -> str:
    return settings.site_url.rstrip("/")


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

    seo = {
        "title": (post.title or "")[:110],
        "description": (post.summary or post.subtitle or "")[:300],
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

    news_article = {
        "@type": "NewsArticle",
        "@id": f"{canonical}#article",
        "url": canonical,
        "mainEntityOfPage": {"@type": "WebPage", "@id": canonical, "name": post.title},
        "headline": (post.title or "")[:110],
        "description": (post.summary or "")[:300],
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

    template = _env.get_template("blog_post.html.j2")
    return template.render(
        post=post,
        related=related or [],
        takeaways=takeaways,
        seo=seo,
        jsonld=jsonld,
        current_year=date.today().year,
    )


def render_blog_index(posts: Iterable) -> str:
    base = _base_url()
    canonical = f"{base}/briefing"

    posts_list = list(posts)

    seo = {
        "title": "Venezuelan investment & sanctions analysis — long-form briefings",
        "description": (
            "Long-form analysis of OFAC sanctions, Asamblea Nacional legislation, "
            "Gaceta Oficial decrees, and sector capital flows. Published twice daily."
        ),
        "keywords": "invest in Venezuela, OFAC Venezuela analysis, Caracas investment briefing, Venezuelan sectors",
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
    "/explainers/what-are-ofac-sanctions-on-venezuela": {
        "title": "OFAC Venezuela Sanctions Explained (2026): EOs 13692, 13850 & 13884",
        "description": (
            "Plain-English guide to all four US Treasury OFAC programs "
            "targeting Venezuela: who's blocked, what General Licenses "
            "allow, who must comply (2026)."
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
        "title": "Venezuela Mining 2026: Gold, Coltan & Diamond Deals Under OFAC",
        "description": (
            "Where Venezuela's gold, coltan, and diamond opportunities "
            "still exist in 2026 under the Organic Mining Law and OFAC "
            "sanctions. Investor diligence guide."
        ),
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
