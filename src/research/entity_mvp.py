"""
src/research/entity_mvp.py — assembles all data for the
/research/sdn/<slug> dossier page into a single dict the template can
consume.

Pure read-only. Reuses the production sdn_profiles cache; never writes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from src.data.sdn_profiles import (
    family_members,
    find_related_news,
    get_profile,
    list_profiles,
    resolve_linked_to,
)
from src.research.enrichment import (
    compose_narrative_llm,
    corporate_affiliation_links,
    fetch_news_google_rss,
    resolve_press_release,
)

logger = logging.getLogger(__name__)

_CURATED_PATH = Path(__file__).parent / "curated_sources.json"

OSINT_EXCLUSIONS = (
    "-site:reddit.com -site:twitter.com -site:x.com -site:wykop.pl "
    "-site:tripadvisor.com -site:youtube.com -site:yelp.com "
    "-site:booking.com -site:facebook.com -site:instagram.com -site:tiktok.com"
)


def _load_curated() -> dict[str, Any]:
    try:
        with _CURATED_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        logger.warning("curated_sources.json invalid: %s", exc)
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _google_news_link(name: str) -> str:
    q = urllib.parse.quote_plus(f'"{name}"')
    return f"https://news.google.com/search?q={q}&hl=en-US&gl=US&ceid=US%3Aen"


def _google_osint_link(name: str) -> str:
    q = urllib.parse.quote_plus(f'"{name}" {OSINT_EXCLUSIONS}')
    return f"https://www.google.com/search?q={q}"


def _google_news_link_recent(name: str) -> str:
    """Google News scoped to last year only — most useful for recent designations."""
    q = urllib.parse.quote_plus(f'"{name}"')
    return f"https://www.google.com/search?q={q}&tbm=nws&tbs=qdr:y"


def _archive_link(url: str) -> str:
    return f"https://web.archive.org/web/{urllib.parse.quote(url, safe='')}"


@dataclass
class Identifier:
    label: str
    value: str
    note: str = ""


def _identifiers_from_parsed(parsed: dict[str, str]) -> list[Identifier]:
    """Turn the SDN parsed-remarks dict into a clean ordered list of
    identifier rows. Order is the order Maria scans for: identity → IDs."""
    order = [
        ("dob", "Date of birth", ""),
        ("pob", "Place of birth", ""),
        ("nationality", "Nationality", ""),
        ("gender", "Gender", ""),
        ("cedula", "Cédula", "National ID"),
        ("passport", "Passport", ""),
        ("rif", "RIF", "Venezuelan tax ID"),
        ("imo", "IMO number", "Vessel"),
        ("aircraft_tail", "Aircraft tail no.", ""),
        ("aircraft_serial", "Aircraft MSN", ""),
    ]
    out: list[Identifier] = []
    for key, label, note in order:
        if key in parsed and parsed[key]:
            out.append(Identifier(label=label, value=parsed[key], note=note))
    return out


def _surname_siblings(profile, *, max_n: int = 12) -> list[dict[str, Any]]:
    """Find every other SDN entry sharing the same surname cluster.
    This powers the disambiguation strip — Maria's #1 question is
    'is this my Juan?', and seeing all siblings answers it."""
    if not profile.is_individual:
        return []
    surname = profile.raw_name.split(",", 1)[0].strip().upper()
    if not surname:
        return []
    out: list[dict[str, Any]] = []
    for p in list_profiles("individuals"):
        if p.db_id == profile.db_id:
            continue
        other_surname = p.raw_name.split(",", 1)[0].strip().upper()
        if other_surname != surname:
            continue
        view_url = (
            f"/research/sdn/{p.slug}"
            if p.slug in _allowed_dossier_slugs()
            else p.url_path
        )
        out.append({
            "name": p.display_name,
            "raw_name": p.raw_name,
            "dob": p.parsed.get("dob") or "—",
            "nationality": p.parsed.get("nationality") or "—",
            "view_url": view_url,
        })
        if len(out) >= max_n:
            break
    return out


def _timeline(profile, related_news: list[dict]) -> list[dict[str, Any]]:
    """Chronological event list: designation event + deduped related-news
    items, sorted oldest first."""
    events: list[dict[str, Any]] = []

    if profile.designation_date:
        events.append({
            "date": profile.designation_date,
            "type": "designation",
            "title": f"Added to OFAC SDN list under {profile.program_label}",
            "url": profile.source_url,
            "source": "US Treasury / OFAC",
        })

    seen_urls = {profile.source_url}
    for n in related_news:
        if n.get("url") in seen_urls:
            continue
        seen_urls.add(n.get("url"))
        events.append({
            "date": n.get("date") or "",
            "type": "related_news",
            "title": n.get("headline", ""),
            "url": n.get("url", ""),
            "source": n.get("source", ""),
        })

    events.sort(key=lambda e: e["date"] or "0000-00-00")
    return events


def _related_entities(profile) -> list[dict[str, Any]]:
    """Co-designated family + linked-to entities, merged into one table."""
    rows: list[dict[str, Any]] = []
    seen_uids: set[str] = set()

    for p in family_members(profile):
        if p.uid in seen_uids:
            continue
        seen_uids.add(p.uid)
        rows.append({
            "name": p.display_name,
            "relationship": "Likely family (shared surname)",
            "designation_date": p.designation_date or "—",
            "program": p.program_label,
            "view_url": (
                f"/research/sdn/{p.slug}"
                if p.slug in _allowed_dossier_slugs()
                else p.url_path
            ),
        })

    for raw_name, linked in resolve_linked_to(profile):
        if linked is None:
            rows.append({
                "name": raw_name,
                "relationship": "OFAC 'Linked To' (not in our database)",
                "designation_date": "—",
                "program": "—",
                "view_url": None,
            })
            continue
        if linked.uid in seen_uids:
            continue
        seen_uids.add(linked.uid)
        rows.append({
            "name": linked.display_name,
            "relationship": "OFAC 'Linked To'",
            "designation_date": linked.designation_date or "—",
            "program": linked.program_label,
            "view_url": (
                f"/research/sdn/{linked.slug}"
                if linked.slug in _allowed_dossier_slugs()
                else linked.url_path
            ),
        })

    return rows


def _allowed_dossier_slugs() -> set[str]:
    from src.research import ALLOWED_ENTITIES
    return set(ALLOWED_ENTITIES.keys())


def assemble(slug: str) -> Optional[dict[str, Any]]:
    """Top-level entry point. Returns None if slug isn't a valid SDN
    individual; the route should 404 in that case.

    Returns a dict with these keys, all template-ready:
      profile, identity_card, status, identifiers, disambiguator,
      siblings, related, timeline, sources, search_aids, generated_at.
    """
    bucket = _allowed_bucket_for(slug)
    if bucket is None:
        return None
    profile = get_profile(bucket, slug)
    if profile is None:
        return None

    curated = _load_curated().get(slug, {})
    primary_sources = curated.get("primary", []) or []
    curated_media = curated.get("adverse_media", []) or []
    curated_press_release = curated.get("press_release_url")
    curated_narrative_override = curated.get("narrative_override")
    public_role = curated.get("public_role")

    related_news = find_related_news(profile)
    siblings = _surname_siblings(profile)
    related = _related_entities(profile)
    timeline = _timeline(profile, related_news)
    identifiers = _identifiers_from_parsed(profile.parsed)

    # ── Enrichment: press release, narrative, news, corp affiliations ──
    press_release = resolve_press_release(
        curated_url=curated_press_release,
        designation_date=profile.designation_date,
    )

    narrative_result = compose_narrative_llm(
        display_name=profile.display_name,
        raw_name=profile.raw_name,
        program=profile.program,
        program_label=profile.program_label,
        designation_date=profile.designation_date,
        parsed=profile.parsed,
        siblings=siblings,
        related=related,
        public_role=public_role,
        override=curated_narrative_override,
    )
    narrative = narrative_result["narrative"]
    narrative_meta = {
        "tldr": narrative_result.get("tldr", ""),
        "source": narrative_result.get("source", "templated"),
        "model": narrative_result.get("model"),
        "generated_at": narrative_result.get("generated_at"),
    }

    live_news = fetch_news_google_rss(profile.display_name, slug=slug, max_items=8)
    # Curated adverse media takes precedence; live news is appended below it,
    # deduped by URL so we never show the same story twice.
    seen_urls = {item.get("url") for item in curated_media if item.get("url")}
    merged_news: list[dict[str, Any]] = list(curated_media)
    for n in live_news:
        if n.get("url") in seen_urls:
            continue
        seen_urls.add(n.get("url"))
        merged_news.append({
            "title": n.get("title", ""),
            "url": n.get("url", ""),
            "publisher": n.get("source", ""),
            "date": n.get("date", ""),
            "note": n.get("snippet", ""),
            "_is_curated": False,
        })

    corp_affiliations = corporate_affiliation_links(profile.display_name)

    # Search aids: pre-built investigator queries Maria can click instead
    # of having to type the OSINT exclusion incantation herself.
    search_aids = [
        {
            "label": "Google News (recent, last year)",
            "url": _google_news_link_recent(profile.display_name),
            "why": "Surfaces fresh coverage of the designation.",
        },
        {
            "label": "Google web search with OSINT exclusions",
            "url": _google_osint_link(profile.display_name),
            "why": "Strips social media + reviews so only news, registries, and official sources rank.",
        },
        {
            "label": "Google News (all time)",
            "url": _google_news_link(profile.display_name),
            "why": "Historical media coverage.",
        },
    ]

    # The headline status banner copy.
    designation_human = ""
    if profile.designation_date:
        try:
            designation_human = datetime.fromisoformat(profile.designation_date).strftime("%B %d, %Y")
        except ValueError:
            designation_human = profile.designation_date

    status = {
        "level": "active_sdn",
        "headline": f"ACTIVE SDN — designated {designation_human or 'date unknown'}",
        "program": profile.program_label,
        "program_eo_url": profile.program_eo_url,
        "source_url": profile.source_url,
    }

    disambiguator = {
        "surname_cluster_size": len(siblings) + 1,
        "verbatim_query_format": profile.raw_name,
        "this_one": {
            "name": profile.display_name,
            "dob": profile.parsed.get("dob"),
            "nationality": profile.parsed.get("nationality"),
        },
    }

    return {
        "profile": profile,
        "identity_card": {
            "display_name": profile.display_name,
            "raw_name": profile.raw_name,
            "category": profile.category_singular,
            "public_role": public_role,
        },
        "status": status,
        "narrative": narrative,
        "narrative_meta": narrative_meta,
        "press_release": press_release,
        "identifiers": identifiers,
        "disambiguator": disambiguator,
        "siblings": siblings,
        "related": related,
        "corp_affiliations": corp_affiliations,
        "timeline": timeline,
        "sources": {
            "primary": primary_sources,
            "adverse_media": merged_news,
            "archive_helpers": [
                {"label": "Wayback Machine — OFAC entry", "url": _archive_link(profile.source_url)},
            ],
        },
        "search_aids": search_aids,
        "generated_at": datetime.utcnow(),
    }


def _allowed_bucket_for(slug: str) -> Optional[str]:
    from src.research import ALLOWED_ENTITIES
    return ALLOWED_ENTITIES.get(slug)


# ──────────────────────────────────────────────────────────────────────
# Hub-page card data
#
# Used by /research/sdn/ to render the dossier index. Deliberately
# omits everything that would require a network call (LLM narrative,
# Google News RSS, OFAC press release HTTP probe). The hub renders
# in a few ms even cold; full assembly happens only on the per-slug
# dossier page.
# ──────────────────────────────────────────────────────────────────────


def card_data_for_hub(slug: str) -> Optional[dict[str, Any]]:
    """Lightweight per-dossier payload for the hub card grid.

    Returns None if the slug is not allowlisted or the underlying SDN
    profile is missing, so the hub silently skips broken entries
    instead of failing the whole page.
    """
    bucket = _allowed_bucket_for(slug)
    if bucket is None:
        return None
    profile = get_profile(bucket, slug)
    if profile is None:
        return None
    curated = _load_curated().get(slug, {})
    surname = profile.raw_name.split(",", 1)[0].strip()

    # One-line tagline. Curated public_role wins (e.g. "Attorney
    # General of Venezuela"), then the curated narrative_override's
    # first sentence, then a templated fallback. We never call the
    # LLM here — that would defeat the point of a fast hub render.
    tagline = curated.get("public_role")
    if not tagline:
        override = (curated.get("narrative_override") or "").strip()
        if override:
            first_sentence = override.split(". ", 1)[0].strip().rstrip(".")
            tagline = first_sentence or None
    if not tagline:
        if profile.designation_date:
            tagline = (
                f"Designated under {profile.program_label} on "
                f"{profile.designation_date}"
            )
        else:
            tagline = f"Designated under {profile.program_label}"

    return {
        "slug": slug,
        "display_name": profile.display_name,
        "raw_name": profile.raw_name,
        "surname": surname,
        "surname_key": surname.upper(),
        "category": profile.category_singular,
        "program": profile.program_label,
        "program_code": profile.program,
        "designation_date": profile.designation_date or "",
        "public_role": curated.get("public_role"),
        "tagline": tagline,
        "url": f"/research/sdn/{slug}",
        "profile_url": profile.url_path,
    }


def all_hub_cards() -> list[dict[str, Any]]:
    """Returns hub cards for every allowlisted slug, sorted into the
    canonical hub order: Carretero cluster first (smaller, easier to
    scan as the user enters the page), then Saab cluster, then any
    future clusters appended by surname-key."""
    from src.research import ALLOWED_ENTITIES
    cards = [c for slug in ALLOWED_ENTITIES if (c := card_data_for_hub(slug)) is not None]

    # Stable cluster ordering: declared order in ALLOWED_ENTITIES wins
    # within a surname; ALLOWED_ENTITIES itself is hand-ordered. This
    # gives the hub a predictable layout that doesn't rotate as we
    # add new dossiers.
    return cards


# ──────────────────────────────────────────────────────────────────────
# Tamper-evident fingerprinting
# ──────────────────────────────────────────────────────────────────────


def compute_fingerprint(ctx: dict[str, Any]) -> str:
    """SHA-256 over the canonical entity payload — embedded in PDF
    cover/footer so any later modification of the source entity (or of
    the PDF itself, if recomputed) is detectable.

    Includes only the data fields, NOT the generated_at timestamp,
    so the *same entity* on consecutive days produces the same hash
    until OFAC actually changes the underlying record. That's exactly
    the property we want for audit trails: 'this is the snapshot you
    saw on date X; here is its content fingerprint'."""
    profile = ctx["profile"]
    payload = {
        "uid": profile.uid,
        "raw_name": profile.raw_name,
        "program": profile.program,
        "designation_date": profile.designation_date,
        "remarks": profile.raw_remarks,
        "linked_to": sorted(profile.linked_to),
        "siblings": sorted(s["raw_name"] for s in ctx.get("siblings", [])),
        "related": sorted(r["name"] for r in ctx.get("related", [])),
        "primary_sources": sorted(s["url"] for s in ctx.get("sources", {}).get("primary", [])),
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ──────────────────────────────────────────────────────────────────────
# PDF rendering
# ──────────────────────────────────────────────────────────────────────


_PDF_HEADER_TEMPLATE = (
    '<div style="font-size:8px; width:100%; padding:0 12mm; color:#888;'
    ' display:flex; justify-content:space-between;">'
    '<span>Caracas Research · Sanctions Dossier</span>'
    '<span style="font-family:monospace;">{slug}</span>'
    '</div>'
)

_PDF_FOOTER_TEMPLATE = (
    '<div style="font-size:8px; width:100%; padding:0 12mm; color:#888;'
    ' display:flex; justify-content:space-between;">'
    '<span>Generated {gen} · Fingerprint <span style="font-family:monospace;">{fp_short}</span></span>'
    '<span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span>'
    '</div>'
)


def render_pdf(slug: str, base_url: str) -> bytes:
    """Render the dossier page to PDF via Playwright headless Chromium.

    `base_url` is e.g. 'http://127.0.0.1:5055' — the host the *server*
    is reachable at. We loopback through HTTP so Playwright loads all
    fonts and assets the way a real browser would, instead of needing
    to inline-replicate the asset graph.
    """
    from playwright.sync_api import sync_playwright

    ctx = assemble(slug)
    if ctx is None:
        raise ValueError(f"Unknown dossier slug: {slug}")
    fingerprint = compute_fingerprint(ctx)
    gen_human = ctx["generated_at"].strftime("%Y-%m-%d %H:%M UTC")

    target_url = f"{base_url.rstrip('/')}/research/sdn/{slug}?dossier=1"

    header = _PDF_HEADER_TEMPLATE.format(slug=slug)
    footer = _PDF_FOOTER_TEMPLATE.format(gen=gen_human, fp_short=fingerprint[:16])

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(target_url, wait_until="networkidle", timeout=20_000)
            # Belt-and-suspenders: media=print also activates @media print
            # rules (a clean way to pick up the @media print stylesheet
            # in the template; .dossier-mode handles the JS-driven path).
            page.emulate_media(media="print")
            pdf_bytes = page.pdf(
                format="Letter",
                print_background=True,
                margin={"top": "18mm", "right": "12mm", "bottom": "20mm", "left": "12mm"},
                display_header_footer=True,
                header_template=header,
                footer_template=footer,
            )
        finally:
            browser.close()
    return pdf_bytes


def pdf_filename_for(slug: str) -> str:
    """Suggested attachment filename: 'caracas-dossier-<slug>-<YYYYMMDD>.pdf'."""
    today = datetime.utcnow().strftime("%Y%m%d")
    return f"caracas-dossier-{slug}-{today}.pdf"
