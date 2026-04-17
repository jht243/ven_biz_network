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
    """Render a single BlogPost row to HTML with full BlogPosting JSON-LD."""
    base = _base_url()
    canonical = f"{base}/briefing/{post.slug}"
    og_image = f"{base}/static/og-image.png?v=2"

    keywords = post.keywords_json or []
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]

    seo = {
        "title": (post.title or "")[:110],
        "description": (post.summary or post.subtitle or "")[:300],
        "keywords": ", ".join(keywords) if keywords else "",
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

    blog_posting = {
        "@type": "BlogPosting",
        "@id": f"{canonical}#article",
        "mainEntityOfPage": {"@type": "WebPage", "@id": canonical},
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
        blog_posting["citation"] = post.canonical_source_url

    jsonld = json.dumps(
        {"@context": "https://schema.org", "@graph": [breadcrumbs, blog_posting]},
        ensure_ascii=False,
    )

    takeaways: list[str] = []

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
        "og_image": f"{base}/static/og-image.png?v=2",
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


def render_landing_page(page, *, recent_briefings: list | None = None) -> str:
    """Render a LandingPage row (pillar / sector / explainer) to HTML."""
    base = _base_url()
    canonical = f"{base}{page.canonical_path}"
    og_image = f"{base}/static/og-image.png?v=2"

    keywords = page.keywords_json or []
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]

    seo = {
        "title": (page.title or "")[:110],
        "description": (page.summary or page.subtitle or "")[:300],
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

    jsonld = json.dumps(
        {"@context": "https://schema.org", "@graph": [breadcrumbs, main_obj]},
        ensure_ascii=False,
    )

    template = _env.get_template("landing.html.j2")
    return template.render(
        page=page,
        recent_briefings=recent_briefings or [],
        seo=seo,
        jsonld=jsonld,
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
