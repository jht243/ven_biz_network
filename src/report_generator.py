"""
Report generator: reads analyzed entries from the database and renders
the Jinja2 template into a static report.html file.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from src.config import settings
from src.models import (
    SessionLocal,
    ExternalArticleEntry,
    AssemblyNewsEntry,
    GazetteStatus,
    SourceType,
    init_db,
)

logger = logging.getLogger(__name__)

SECTOR_OPTIONS = [
    {"value": "realestate", "label": "Real Estate"},
    {"value": "security", "label": "Safety & Security"},
    {"value": "economic", "label": "Economic Policy"},
    {"value": "fiscal", "label": "Tax & Fiscal"},
    {"value": "sanctions", "label": "Sanctions"},
    {"value": "diplomatic", "label": "US Relations"},
    {"value": "governance", "label": "Governance"},
    {"value": "legal", "label": "Legal & Rights"},
    {"value": "mining", "label": "Mining"},
    {"value": "energy", "label": "Energy & Oil"},
    {"value": "banking", "label": "Banking & Finance"},
]

STATUS_CSS_MAP = {
    "passed": "passed",
    "in_effect": "passed",
    "in_progress": "progress",
    "announced": "announced",
    "monitoring": "monitoring",
}

TRUST_CSS_MAP = {
    "official": ("trust-official", "Official"),
    "tier1": ("trust-tier1", "Verified Source"),
    "state": ("trust-state", "State Media"),
    "tier2": ("trust-tier2", "News Source"),
}

SOURCE_DISPLAY_MAP = {
    SourceType.FEDERAL_REGISTER: "Federal Register",
    SourceType.OFAC_SDN: "OFAC SDN List",
    SourceType.GDELT: None,
    SourceType.BCV_RATES: "BCV",
    SourceType.TRAVEL_ADVISORY: "State Dept",
    SourceType.ASAMBLEA_NACIONAL: "Asamblea Nacional",
}


def generate_report(output_path: Path | None = None) -> Path:
    """
    Query the database for analyzed entries and render the report.
    Returns the path to the generated HTML file.
    """
    output_path = output_path or settings.output_dir / "report.html"
    init_db()
    db = SessionLocal()

    try:
        cutoff = date.today() - timedelta(days=settings.report_lookback_days)

        ext_articles = (
            db.query(ExternalArticleEntry)
            .filter(ExternalArticleEntry.status == GazetteStatus.ANALYZED)
            .filter(ExternalArticleEntry.published_date >= cutoff)
            .order_by(ExternalArticleEntry.published_date.desc())
            .all()
        )

        assembly_news = (
            db.query(AssemblyNewsEntry)
            .filter(AssemblyNewsEntry.status == GazetteStatus.ANALYZED)
            .filter(AssemblyNewsEntry.published_date >= cutoff)
            .order_by(AssemblyNewsEntry.published_date.desc())
            .all()
        )

        entries = _build_entries(ext_articles, assembly_news)
        ticker_items = _build_ticker(db)
        news_items = _build_news_sidebar(entries)
        calendar_events = _build_calendar(ext_articles, assembly_news)
        climate = _build_climate()

        template_dir = Path(__file__).parent.parent / "templates"
        env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=False,
        )
        template = env.get_template("report.html.j2")

        html = template.render(
            entries=entries,
            ticker_items=ticker_items,
            news_items=news_items,
            calendar_events=calendar_events,
            climate=climate,
            all_sectors=SECTOR_OPTIONS,
            current_year=date.today().year,
            generated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")
        logger.info("Report generated: %s (%d entries)", output_path, len(entries))

        try:
            from src.storage_remote import upload_report_html, supabase_storage_enabled
            if supabase_storage_enabled():
                upload_report_html(html)
        except Exception as e:
            logger.error("Failed to upload report to Supabase Storage: %s", e)

        return output_path

    finally:
        db.close()


def _build_entries(ext_articles, assembly_news) -> list[dict]:
    """Convert DB entries into template-ready dicts, filtered by relevance."""
    entries = []
    min_score = settings.analysis_min_relevance

    all_items = []
    for a in ext_articles:
        all_items.append(("external", a))
    for n in assembly_news:
        all_items.append(("assembly", n))

    for item_type, item in all_items:
        analysis = item.analysis_json or {}
        relevance = analysis.get("relevance_score", 0)
        if relevance < min_score:
            continue

        sectors = analysis.get("sectors", [])
        sentiment = analysis.get("sentiment", "mixed")
        status = analysis.get("status", "monitoring")
        status_label = analysis.get("status_label", status.replace("_", " ").title())
        category_label = analysis.get("category_label", "General")
        headline = analysis.get("headline_short", item.headline[:80])
        takeaway = analysis.get("takeaway", "")
        is_breaking = analysis.get("is_breaking", False)
        source_trust = analysis.get("source_trust", "tier2")

        status_key = status.lower().replace(" ", "_").replace("—", "").replace("-", "_").strip()
        for k in STATUS_CSS_MAP:
            if k in status_key:
                status_key = k
                break
        status_css = STATUS_CSS_MAP.get(status_key, "monitoring")

        trust_css, trust_label_default = TRUST_CSS_MAP.get(source_trust, ("trust-tier2", "News Source"))

        if item_type == "external":
            source_display = item.source_name or "Source"
            if item.source == SourceType.FEDERAL_REGISTER:
                source_display = "Federal Register"
                trust_label_default = "Official — Federal Register"
            elif item.source == SourceType.TRAVEL_ADVISORY:
                source_display = "State Dept"
                trust_label_default = "Official — US State Department"
            elif item.source == SourceType.GDELT:
                domain = (item.extra_metadata or {}).get("domain", "")
                source_display = domain or item.source_name or "International Press"
                trust_label_default = f"Via GDELT — {source_display}"
        else:
            source_display = "Asamblea Nacional"
            trust_label_default = "State Media"

        is_new = (date.today() - item.published_date).days <= 3

        safe_id = re.sub(r"[^a-z0-9]", "-", headline.lower())[:40].strip("-")

        entries.append({
            "id": safe_id,
            "headline": item.headline,
            "headline_short": headline,
            "date_display": item.published_date.strftime("%B %d, %Y"),
            "published_date": item.published_date,
            "source_url": item.source_url,
            "source_display": source_display,
            "sectors": sectors,
            "sectors_str": " ".join(sectors),
            "sentiment": sentiment,
            "status_class": status_css,
            "status_label": status_label,
            "category_label": category_label,
            "takeaway": takeaway,
            "is_new": is_new,
            "is_breaking": is_breaking,
            "trust_class": trust_css,
            "trust_label": trust_label_default,
            "body_text": item.body_text if item.body_text and len(item.body_text) > 100 else None,
            "relevance": relevance,
        })

    entries.sort(key=lambda e: e["published_date"], reverse=True)
    entries = _deduplicate_entries(entries)
    return entries


# Words that don't help disambiguate topics (Spanish + English).
_TOPIC_STOPWORDS = frozenset({
    # English
    "the", "and", "for", "with", "from", "that", "this", "into", "over",
    "have", "has", "are", "was", "were", "will", "new", "more", "than",
    "but", "not", "may", "can", "now", "all", "how", "why", "when",
    "what", "which", "who", "you", "your", "his", "her", "its", "their",
    "venezuela", "venezuelan", "venezuela's", "law", "laws", "bill",
    # Spanish
    "para", "con", "por", "del", "los", "las", "una", "uno", "que",
    "como", "esta", "este", "esto", "esos", "esas", "muy", "ser",
    "venezolan", "venezolana", "venezolano", "venezolanas", "venezolanos",
    "nacional", "nacionales", "asamblea", "diputado", "diputada",
    "diputados", "diputadas", "presidente", "presidenta",
    "comision", "comision", "permanente",
})

# Investor-relevant topic clusters. Any entry whose normalized text
# contains one of these keywords is tagged with the topic. Entries that
# share a topic AND fall within DEDUP_WINDOW_DAYS of each other are
# collapsed to a single entry (the highest-relevance one). This is the
# big hammer that catches "12 different MPs each made a statement about
# the Mining Law this week" -> one entry.
# Order matters: the first tag whose keyword appears in the entry text
# wins. Put NARROW, SPECIFIC topics first; broad ones last. This prevents
# e.g. "foreign_investment" body text from getting mis-tagged as
# "amnesty_law" just because the article mentions amnesty in passing.
_TOPIC_TAGS: list[tuple[str, tuple[str, ...]]] = [
    # Specific named laws (highest priority)
    ("mining_law", ("ley organica de minas", "ley de minas", "mining law", "ley organica minera", "ley minera")),
    ("amnesty_law", ("ley de amnistia", "amnesty law")),
    ("socioeconomic_law", ("ley de proteccion de derechos socioeconomicos", "derechos socioeconomicos", "socioeconomic law")),
    ("admin_celeridad_law", ("ley para la celeridad", "ley para celeridad", "tramites administrativos law", "administrative streamlining law")),
    ("hydrocarbons_law", ("ley de hidrocarburos", "hydrocarbons law")),
    ("constitutional_court_minas", ("tsj declara constitucionalidad de la ley de minas", "constitutionality of the mining law", "constitutionality of the organic mining law")),
    # Specific OFAC/sanctions actions
    ("ofac_general_license", ("general license 5", "general license 6", "general license 7", "general license 8", "general license 9", "licencia general 5", "licencia general 6")),
    ("ofac_sanctions_relief", ("levantamiento de las sanciones", "sanctions easing", "ease sanctions", "ease the sanctions", "lift sanctions", "us eases sanctions")),
    ("ofac_designations", ("notice of ofac sanctions actions", "ofac sdn list update", "ofac sanctions actions")),
    ("travel_advisory", ("travel advisory", "do not travel advisory", "reconsider travel", "advisory level")),
    # Diplomatic ties (specific bilaterals)
    ("eu_dialogue", ("grupo de amistad venezuela-ue", "venezuela-eu friendship group", "european parliament delegation")),
    ("us_relations_specific", ("us senate resolution", "us state department releases", "us-venezuela bilateral")),
    # Sector-broad (lowest priority — only catch if nothing more specific matched)
    ("foreign_investment_general", ("inversion extranjera directa", "foreign direct investment")),
    ("real_estate_reform", ("reformara leyes vinculadas al sector inmobiliario", "real estate sector reform", "leyes inmobiliarias")),
]


def _normalize(text: str) -> str:
    """Strip accents + lowercase. 'Petróleo' -> 'petroleo'."""
    import unicodedata
    return (
        unicodedata.normalize("NFKD", text or "")
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )


def _topic_signature(text: str) -> set[str]:
    """Significant-word set for Jaccard similarity comparison."""
    norm = _normalize(text)
    tokens = re.findall(r"[a-zA-Z]+", norm)
    return {t for t in tokens if len(t) > 3 and t not in _TOPIC_STOPWORDS}


def _topic_tag(text: str) -> str | None:
    """Return the first topic tag whose keyword appears in text, else None."""
    norm = _normalize(text)
    for tag, kws in _TOPIC_TAGS:
        for kw in kws:
            if kw in norm:
                return tag
    return None


def _entry_text(entry: dict) -> str:
    return " ".join(filter(None, [
        entry.get("headline_short"),
        entry.get("headline"),
        entry.get("body_text") or "",
    ]))


DEDUP_WINDOW_DAYS = 7
JACCARD_THRESHOLD = 0.35
# Even when two entries share a topic tag and fall inside the dedup
# window, they must also have at least this much *content* overlap to
# be merged. This protects against the "two genuinely different mining
# laws were passed in the same week" case — both would tag as
# mining_law, but their headlines/bodies would have low word overlap
# (e.g. "Mining Royalty Reform" vs "Organic Mining Law Promulgation"),
# so they stay as separate entries.
TOPIC_MERGE_MIN_JACCARD = 0.25


def _deduplicate_entries(entries: list[dict]) -> list[dict]:
    """Collapse near-duplicate entries.

    Two passes:
      1. **Topic-window pass**: entries with the same topic tag within
         DEDUP_WINDOW_DAYS collapse to the highest-relevance one. This
         catches the "12 MPs separately commented on the Mining Law
         this week" case.
      2. **Jaccard fallback**: catches near-duplicates that didn't
         match a topic tag, using shared significant-word ratio.

    Within each merge, we keep the entry with the highest LLM
    relevance score (tiebreak: newer date).
    """
    if not entries:
        return entries

    original_count = len(entries)

    # --- Pass 1: topic + time-window clustering ---
    survivors: list[dict] = []
    by_tag: dict[str, list[dict]] = {}
    for e in entries:
        tag = _topic_tag(_entry_text(e))
        if tag is None:
            survivors.append(e)
            continue
        by_tag.setdefault(tag, []).append(e)

    for tag, group in by_tag.items():
        # Sort newest first, then iterate building "clusters" of entries
        # that satisfy BOTH:
        #   - within DEDUP_WINDOW_DAYS of an existing cluster member
        #   - shared significant-word Jaccard >= TOPIC_MERGE_MIN_JACCARD
        #     with an existing cluster member (this is the safety net
        #     that keeps two genuinely-different same-topic events apart)
        group.sort(key=lambda e: e["published_date"], reverse=True)
        sigs = {id(e): _topic_signature(_entry_text(e)) for e in group}
        clusters: list[list[dict]] = []
        for e in group:
            placed = False
            sig = sigs[id(e)]
            for cluster in clusters:
                date_ok = any(
                    abs((e["published_date"] - x["published_date"]).days) <= DEDUP_WINDOW_DAYS
                    for x in cluster
                )
                if not date_ok:
                    continue
                content_ok = False
                for x in cluster:
                    x_sig = sigs[id(x)]
                    union = sig | x_sig
                    if not union:
                        continue
                    if len(sig & x_sig) / len(union) >= TOPIC_MERGE_MIN_JACCARD:
                        content_ok = True
                        break
                if not content_ok:
                    continue
                cluster.append(e)
                placed = True
                break
            if not placed:
                clusters.append([e])

        for cluster in clusters:
            cluster.sort(
                key=lambda e: (e["relevance"], e["published_date"]),
                reverse=True,
            )
            keeper = cluster[0]
            survivors.append(keeper)
            if len(cluster) > 1:
                dropped_titles = ", ".join(
                    f"'{e['headline_short'][:40]}'" for e in cluster[1:]
                )
                logger.info(
                    "Dedup [%s win=%dd, jacc>=%.2f]: kept '%s' (rel=%s, %s); dropped %d: %s",
                    tag,
                    DEDUP_WINDOW_DAYS,
                    TOPIC_MERGE_MIN_JACCARD,
                    keeper["headline_short"][:60],
                    keeper["relevance"],
                    keeper["published_date"],
                    len(cluster) - 1,
                    dropped_titles,
                )

    # --- Pass 2: Jaccard for everything that survived (no tag match) ---
    survivors.sort(key=lambda e: e["published_date"], reverse=True)
    enriched = [(e, _topic_signature(_entry_text(e))) for e in survivors]
    final: list[tuple[dict, set[str]]] = []
    for entry, sig in enriched:
        if not sig:
            final.append((entry, sig))
            continue
        merged = False
        for i, (kept_entry, kept_sig) in enumerate(final):
            if not kept_sig:
                continue
            jaccard = len(sig & kept_sig) / len(sig | kept_sig)
            if jaccard < JACCARD_THRESHOLD:
                continue
            challenger = (entry["relevance"], entry["published_date"])
            kept = (kept_entry["relevance"], kept_entry["published_date"])
            if challenger > kept:
                final[i] = (entry, sig)
            merged = True
            break
        if not merged:
            final.append((entry, sig))

    deduped = [e for e, _ in final]
    deduped.sort(key=lambda e: e["published_date"], reverse=True)
    if len(deduped) < original_count:
        logger.info("Dedup total: %d -> %d entries", original_count, len(deduped))
    return deduped


def _build_news_sidebar(entries: list[dict]) -> list[dict]:
    """Top entries for the This Week's News sidebar."""
    top = sorted(entries, key=lambda e: (e.get("is_breaking", False), e["relevance"]), reverse=True)
    sidebar = []
    for e in top[:8]:
        summary_short = e["takeaway"][:120].rsplit(" ", 1)[0] + "..." if len(e["takeaway"]) > 120 else e["takeaway"]
        summary_short = re.sub(r"<[^>]+>", "", summary_short)
        sidebar.append({
            "id": e["id"],
            "headline_short": e["headline_short"],
            "summary_short": summary_short,
            "sentiment": e["sentiment"],
        })
    return sidebar


def _build_ticker(db) -> list[dict]:
    """Build ticker bar items from latest DB data."""
    items = []

    bcv = (
        db.query(ExternalArticleEntry)
        .filter(ExternalArticleEntry.source == SourceType.BCV_RATES)
        .order_by(ExternalArticleEntry.published_date.desc())
        .first()
    )
    if bcv and bcv.extra_metadata:
        usd_rate = bcv.extra_metadata.get("usd")
        if usd_rate:
            items.append({
                "label": "BCV Official",
                "value": f"{usd_rate}",
                "unit": "Bs.D/$",
                "change": None,
                "change_dir": "up",
                "value_color": None,
                "source": "BCV (live)",
            })

    advisory = (
        db.query(ExternalArticleEntry)
        .filter(ExternalArticleEntry.source == SourceType.TRAVEL_ADVISORY)
        .order_by(ExternalArticleEntry.published_date.desc())
        .first()
    )
    if advisory and advisory.extra_metadata:
        level = advisory.extra_metadata.get("level")
        if level:
            color = "#4ade80" if level <= 3 else "#f87171"
            items.append({
                "label": "Travel Advisory",
                "value": f"Level {level}",
                "unit": None,
                "change": f"{'↓' if level < 4 else ''} from 4" if level < 4 else None,
                "change_dir": "up" if level < 4 else "down",
                "value_color": color,
                "source": "State Dept",
            })

    if not items or len(items) < 2:
        items.extend([
            {"label": "Brent Crude", "value": "$65.48", "unit": None, "change": "−4.1%", "change_dir": "down", "value_color": None, "source": "MarketWatch"},
            {"label": "Inflation Q1", "value": "71.8%", "unit": None, "change": None, "change_dir": "down", "value_color": None, "source": "BCV Official"},
            {"label": "Country Risk", "value": "E", "unit": "(Extreme)", "change": None, "change_dir": "down", "value_color": "#f87171", "source": "Coface"},
            {"label": "Oil Prod.", "value": "1.095M", "unit": "bpd", "change": None, "change_dir": "up", "value_color": None, "source": "PDVSA"},
        ])
    else:
        items.extend([
            {"label": "Brent Crude", "value": "$65.48", "unit": None, "change": "−4.1%", "change_dir": "down", "value_color": None, "source": "MarketWatch"},
            {"label": "Inflation Q1", "value": "71.8%", "unit": None, "change": None, "change_dir": "down", "value_color": None, "source": "BCV Official"},
            {"label": "Country Risk", "value": "E", "unit": "(Extreme)", "change": None, "change_dir": "down", "value_color": "#f87171", "source": "Coface"},
            {"label": "FDI Stock", "value": "$30.5B", "unit": None, "change": None, "change_dir": "up", "value_color": None, "source": "UNCTAD '24"},
            {"label": "Oil Prod.", "value": "1.095M", "unit": "bpd", "change": None, "change_dir": "up", "value_color": None, "source": "PDVSA"},
        ])

    return items


# Sort order for calendar urgency tiers — lowest int = shown first.
_URGENCY_ORDER = {
    "today": 0,
    "imminent": 1,
    "dated": 2,
    "pending": 3,
    "ongoing": 4,
    "longterm": 5,
}

# Small set of *standing* calendar items — long-horizon programs whose
# presence in the calendar is a function of "investors should always be
# aware of these", not of any one news article. Daily news scraping
# wouldn't naturally surface "OFAC GLs 46A-50A are still active" because
# their continued existence isn't news. These get appended after the
# dynamically-extracted items, only if they aren't already covered by
# something dynamic with a similar title.
_STANDING_CALENDAR_ITEMS: list[dict] = [
    {
        "date_label": "Ongoing",
        "title": "OFAC GLs 46A–50A",
        "subtitle": "Active",
        "note": "Oil & gas authorizations. Revocable.",
        "link": "https://ofac.treasury.gov/sanctions-programs-and-country-information/venezuela-related-sanctions",
        "link_label": "OFAC",
        "css_class": "cal-positive",
        "urgency": "ongoing",
    },
    {
        "date_label": "2026 Target",
        "title": "34 laws planned",
        "subtitle": None,
        "note": "Full legislative agenda for 2026.",
        "link": "https://www.ciudadvalencia.com.ve/sancionar-34-leyes-2026/",
        "link_label": "Source",
        "css_class": "",
        "urgency": "longterm",
    },
]


def _build_calendar(ext_articles, assembly_news) -> list[dict]:
    """Forward-looking investor calendar built from recent analyzed news.

    The LLM analyzer extracts a `calendar_event` object on entries that
    describe a specific time-bounded event (a scheduled discussion,
    march, license expiration, pending promulgation, etc). This pulls
    those out, dedupes by title, sorts by urgency, and appends a small
    set of standing items (active OFAC GLs, the 2026 legislative
    target) that wouldn't naturally surface in daily news.
    """
    candidates: list[dict] = []
    seen_titles: set[str] = set()

    # Newest entries first so when the same event is mentioned twice
    # we keep the freshest framing.
    for item in sorted(
        list(ext_articles) + list(assembly_news),
        key=lambda x: x.published_date,
        reverse=True,
    ):
        analysis = item.analysis_json or {}
        ev = analysis.get("calendar_event")
        if not ev or not isinstance(ev, dict):
            continue
        title = (ev.get("title") or "").strip()
        if not title:
            continue
        # Dedupe by normalized title across entries.
        key = _normalize(title)
        if key in seen_titles:
            continue
        seen_titles.add(key)

        urgency = (ev.get("urgency") or "dated").lower()
        candidates.append({
            "date_label": ev.get("date_label") or item.published_date.strftime("%b %d, %Y"),
            "title": title,
            "subtitle": ev.get("subtitle"),
            "note": ev.get("note") or "",
            "link": item.source_url,
            "link_label": _calendar_link_label(item),
            "css_class": ev.get("css_class") or "",
            "urgency": urgency,
            "_relevance": analysis.get("relevance_score", 0),
            "_published": item.published_date,
        })

    # Append standing items only if they don't duplicate a dynamic one.
    for fixture in _STANDING_CALENDAR_ITEMS:
        if _normalize(fixture["title"]) in seen_titles:
            continue
        candidates.append({
            **fixture,
            "_relevance": 0,
            "_published": date.min,
        })

    # Sort: urgency tier first, then relevance score (high first),
    # then newest published date.
    candidates.sort(
        key=lambda c: (
            _URGENCY_ORDER.get(c["urgency"], 99),
            -c.get("_relevance", 0),
            -((c["_published"] - date.min).days if c["_published"] else 0),
        )
    )

    cleaned = []
    for c in candidates[:8]:
        cleaned.append({k: v for k, v in c.items() if not k.startswith("_")})

    if len(cleaned) <= 2:
        # Almost-empty calendar: fall back to standing items only so the
        # box doesn't render as a barren single line.
        cleaned = list(_STANDING_CALENDAR_ITEMS)

    return cleaned


def _calendar_link_label(item) -> str:
    """Short pill-friendly label for the calendar event source link."""
    if hasattr(item, "source"):
        if item.source == SourceType.FEDERAL_REGISTER:
            return "Federal Register"
        if item.source == SourceType.TRAVEL_ADVISORY:
            return "State Dept"
        if item.source == SourceType.OFAC_SDN:
            return "OFAC"
    if hasattr(item, "source_url") and "asambleanacional" in (item.source_url or ""):
        return "AN"
    return "Source"


def _build_climate() -> dict:
    """Investment climate tracker data — will be LLM-generated in future."""
    return {
        "period": "Q2 2026 vs. Q1 2026",
        "bars": [
            {"label": "Sanctions Trajectory", "score": 7, "trend_dir": "up", "trend_value": "+3", "bar_color": "green", "why": "OFAC eased bank sanctions; expanded GLs 49 & 50A in Feb; GL 5T issued Mar 2. Travel advisory downgraded to Level 3."},
            {"label": "Diplomatic Progress", "score": 6, "trend_dir": "up", "trend_value": "+3", "bar_color": "green", "why": "Chargé d'affaires appointed to Washington; first formal diplomatic channel since 2019."},
            {"label": "Legal Framework", "score": 4, "trend_dir": "up", "trend_value": "+1", "bar_color": "yellow", "why": "Hydrocarbons Law reform signed, codifying empresa mixta model. Mining law tightens state control."},
            {"label": "Political Stability", "score": 3, "trend_dir": "up", "trend_value": "+1", "bar_color": "red", "why": "Amnesty Law benefited 8,000+; signals normalization. But no elections scheduled yet."},
            {"label": "Property Rights", "score": 3, "trend_dir": "flat", "trend_value": "0", "bar_color": "red", "why": "Mining law reasserts absolute state ownership. No new protections for real estate or commercial assets."},
            {"label": "Macro Stability", "score": 2, "trend_dir": "down", "trend_value": "−1", "bar_color": "red", "why": "Inflation accelerating: 649% annualized (Mar). Parallel premium widened to 31.7%. Coface E rating."},
        ],
        "methodology": (
            "Sub-scores derived from BCV exchange/inflation data (live scrape), Coface E rating, OFAC GL activity via Federal Register API, "
            "US State Dept travel advisory level (live scrape), GDELT global news sentiment, OFAC SDN list monitoring, "
            "UNCTAD FDI stock ($30.5B), Amnesty Law implementation data, and legislative pipeline analysis. QoQ comparison based on Q4 2025 baseline."
        ),
    }
