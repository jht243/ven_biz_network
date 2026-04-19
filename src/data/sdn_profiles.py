"""
Per-SDN-entity profile data layer.

Powers the auto-generated /sanctions/{individuals,entities,vessels,aircraft}/<slug>
pages. Each one of OFAC's ~410 Venezuela-program designations becomes its own
SEO-optimized URL, indexed by Google + Bing, so when a compliance officer
searches "vicente carretero" or "saab halabi" their first organic result is
our profile page (titled with the person's name verbatim) instead of a
generic tracker.

Why a dedicated data module:
  • The /sanctions-tracker page already loads all 410 entries for the search
    table — we don't want to re-query the DB on every profile page render
    (that's 410× the load), and we don't want every caller to re-implement
    the same `remarks` blob parsing.
  • Family-cluster + "Linked To" graphs need to be precomputed once across
    the whole list — those relationships are what justifies a dedicated page
    per individual (a profile that says "see also: 3 other Carretero
    Napolitano family members" is the kind of value-add nobody else
    publishes).
  • Slug stability matters for SEO: once a URL is indexed, changing it
    forfeits the rank. The slug logic here is a single source of truth
    that future code MUST not modify silently.

The whole module is a pure transformation of ExternalArticleEntry rows
where source = OFAC_SDN. No external API calls, no LLM, no side effects.
Cached in-process for the lifetime of the Flask worker.
"""
from __future__ import annotations

import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Optional


# Map OFAC program codes → readable labels and the executive order URL.
# These are the four Venezuela-related programs OFAC has issued under;
# anything else has been filtered out upstream by src/scraper/ofac_sdn.py.
PROGRAM_LABELS: dict[str, str] = {
    "VENEZUELA": "Venezuela (Specially Designated Nationals)",
    "VENEZUELA-EO13692": "Venezuela — EO 13692 (Human rights / corruption)",
    "VENEZUELA-EO13850": "Venezuela — EO 13850 (Gold sector / public officials)",
    "VENEZUELA-EO13884": "Venezuela — EO 13884 (Government of Venezuela block)",
}

PROGRAM_EXEC_ORDERS: dict[str, str] = {
    "VENEZUELA-EO13692": "https://ofac.treasury.gov/media/12126/download?inline",
    "VENEZUELA-EO13850": "https://ofac.treasury.gov/media/13311/download?inline",
    "VENEZUELA-EO13884": "https://ofac.treasury.gov/media/13351/download?inline",
}

# Map raw OFAC `type` field (which uses "individual", "vessel", "aircraft",
# and "-0-" for everything else) to our URL bucket. "entity" is the catch-all
# for organisations + companies + holding vehicles + anything OFAC didn't
# put in one of the three named categories.
ENTITY_BUCKETS: tuple[str, ...] = ("individuals", "entities", "vessels", "aircraft")
_TYPE_TO_BUCKET: dict[str, str] = {
    "individual": "individuals",
    "vessel": "vessels",
    "aircraft": "aircraft",
    "entity": "entities",
    "-0-": "entities",
    "": "entities",
}

# Singular labels used in titles, breadcrumbs, structured data.
_BUCKET_SINGULAR: dict[str, str] = {
    "individuals": "individual",
    "entities": "entity",
    "vessels": "vessel",
    "aircraft": "aircraft",
}


# ──────────────────────────────────────────────────────────────────────
# Slug + name helpers
# ──────────────────────────────────────────────────────────────────────


def _slugify(value: str) -> str:
    """Strip accents, lowercase, hyphenate. URL-safe and stable.

    Stability is the contract here — once a URL is indexed by Google,
    changing the slug breaks every backlink. If you want to change
    slug behavior, add 301 redirects from the old slug to the new one
    in server.py.
    """
    if not value:
        return "unknown"
    norm = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    norm = norm.lower()
    norm = re.sub(r"[^a-z0-9]+", "-", norm).strip("-")
    return norm[:120] or "unknown"


def _display_name(raw_name: str) -> str:
    """OFAC stores names as 'SURNAME, Given Names'. For headlines we want
    the natural-order form 'Given Names Surname' for readability — but
    only for individuals. Vessels/aircraft/entities keep the raw form.
    """
    if not raw_name or "," not in raw_name:
        return _titlecase_acronym_safe(raw_name or "")
    surname, _, given = raw_name.partition(",")
    given = given.strip()
    surname = surname.strip()
    if not given or not surname:
        return _titlecase_acronym_safe(raw_name)
    return f"{_titlecase_acronym_safe(given)} {_titlecase_acronym_safe(surname)}"


def _titlecase_acronym_safe(s: str) -> str:
    """Title-case while preserving short all-caps tokens (initials, IDs).

    OFAC names like 'PDVSA' or 'CITGO' or 'C.A.' must NOT become
    'Pdvsa' / 'Citgo' / 'C.a.'. Heuristic: tokens of <=4 chars that
    are all-uppercase and contain a letter stay as-is; everything
    else gets capwords-style title casing.
    """
    if not s:
        return s
    out = []
    for tok in s.split():
        bare = re.sub(r"[^A-Za-z0-9]", "", tok)
        if bare.isupper() and 1 < len(bare) <= 4:
            out.append(tok)
        elif bare.isdigit():
            out.append(tok)
        else:
            out.append(tok.capitalize())
    return " ".join(out)


def _surname(raw_name: str) -> str:
    """Surname (everything before the first comma) for individuals.
    Returns empty string for non-individuals so they don't get
    accidentally clustered with people."""
    if not raw_name or "," not in raw_name:
        return ""
    return raw_name.split(",", 1)[0].strip()


# ──────────────────────────────────────────────────────────────────────
# Remarks parser
# ──────────────────────────────────────────────────────────────────────

# Patterns we recognize inside the OFAC remarks blob. Each one extracts
# a single canonical field. We deliberately keep this list narrow: only
# fields with universal investor-relevance get surfaced on the profile
# page. Unmapped fragments still appear in the raw remarks fallback.
_REMARKS_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("dob",          re.compile(r"\bDOB\s+([^;]+)", re.I)),
    ("pob",          re.compile(r"\bPOB\s+([^;]+)", re.I)),
    ("nationality",  re.compile(r"\bnationality\s+([^;]+)", re.I)),
    ("citizenship",  re.compile(r"\bcitizen\s+([^;]+)", re.I)),
    ("gender",       re.compile(r"\bGender\s+([^;]+)", re.I)),
    ("cedula",       re.compile(r"\bCedula(?:\s+No\.?)?\s+([^;]+?)(?:\s*\([^)]*\))?(?=;|$)", re.I)),
    ("passport",     re.compile(r"\bPassport(?:\s+No\.?)?\s+([^;]+?)(?:\s*\([^)]*\))?(?=;|$)", re.I)),
    ("national_id",  re.compile(r"\bNational ID(?:\s+No\.?)?\s+([^;]+?)(?:\s*\([^)]*\))?(?=;|$)", re.I)),
    ("rif",          re.compile(r"\bRIF(?:\s+No\.?)?\s+([^;]+)", re.I)),
    ("imo",          re.compile(r"\bIMO\s+(\d+)", re.I)),
    ("mmsi",         re.compile(r"\bMMSI\s+(\d+)", re.I)),
    ("vessel_year",  re.compile(r"\bVessel Year of Build\s+(\d{4})", re.I)),
    ("vessel_flag",  re.compile(r"\bVessel Flag\s+([^;]+)", re.I)),
    ("aircraft_model",     re.compile(r"\bAircraft Model\s+([^;]+)", re.I)),
    ("aircraft_serial",    re.compile(r"\bAircraft Manufacturer'?s? Serial Number(?:\s*\(MSN\))?\s+([^;]+)", re.I)),
    ("aircraft_tail",      re.compile(r"\bAircraft Tail Number\s+([^;]+)", re.I)),
]

# `Linked To: NAME OF OTHER ENTITY` — the most useful relationship hint
# OFAC publishes. Often a vessel is linked to a parent shipping company,
# or a shell company is linked to its beneficial owner. We surface every
# such mention as an outbound profile link if the linked name resolves
# to another SDN profile we render.
_LINKED_TO_PATTERN = re.compile(r"\bLinked To:\s*([^;]+?)(?=;|$)", re.I)


@dataclass
class SDNProfile:
    """One OFAC SDN entry, parsed and ready to render.

    Hashable on `slug + bucket` only; do not put unhashable fields in
    the dataclass. Equality is structural so two reads of the same DB
    row produce equal profiles.
    """
    db_id: int
    uid: str  # OFAC's permanent identifier — survives across SDN reissues
    raw_name: str
    display_name: str
    bucket: str  # one of ENTITY_BUCKETS
    slug: str
    program: str  # one of the VENEZUELA-* codes
    program_label: str
    program_eo_url: Optional[str]
    source_url: str  # OFAC's link to this specific SDN listing
    designation_date: Optional[str] = None  # ISO date when our scraper first saw the listing
    raw_remarks: str = ""
    parsed: dict[str, str] = field(default_factory=dict)
    linked_to: list[str] = field(default_factory=list)  # raw names — resolve to slugs at render

    @property
    def url_path(self) -> str:
        return f"/sanctions/{self.bucket}/{self.slug}"

    @property
    def category_singular(self) -> str:
        return _BUCKET_SINGULAR.get(self.bucket, self.bucket)

    @property
    def is_individual(self) -> bool:
        return self.bucket == "individuals"


# ──────────────────────────────────────────────────────────────────────
# In-process cache
# ──────────────────────────────────────────────────────────────────────
#
# Loading + parsing 410 SDN rows from Postgres on every profile-page
# render would be ~50–80ms per request for data that only changes when
# OFAC publishes a new SDN list (typically <1×/day). Cache the entire
# parsed corpus in-memory keyed by load timestamp; refresh after TTL.

_CACHE_TTL_SECONDS = 600  # 10 minutes — a fresh OFAC scrape will repopulate within one cron cycle
_CACHE_LOCK = threading.Lock()
_CACHE: dict = {
    "loaded_at": 0.0,
    "by_bucket_slug": {},  # {(bucket, slug): SDNProfile}
    "by_bucket": {},       # {bucket: list[SDNProfile]} (alpha sorted)
    "by_uid": {},          # {uid: SDNProfile} — for "Linked To" name resolution
    "family_clusters": {}, # {surname: list[SDNProfile]}
    "name_to_profiles": {},# normalised raw_name (no accents, lower) → list[SDNProfile]
}


def _normalize_for_match(name: str) -> str:
    """Aggressive normalization for fuzzy 'Linked To' name matching."""
    if not name:
        return ""
    norm = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    norm = re.sub(r"[^A-Za-z0-9]+", " ", norm).lower().strip()
    return norm


def _parse_remarks(blob: str) -> tuple[dict[str, str], list[str]]:
    parsed: dict[str, str] = {}
    if not blob:
        return parsed, []
    for key, pat in _REMARKS_PATTERNS:
        m = pat.search(blob)
        if m:
            parsed[key] = m.group(1).strip().rstrip(".")
    linked = [m.strip().rstrip(".") for m in _LINKED_TO_PATTERN.findall(blob)]
    return parsed, linked


def _load_from_db() -> None:
    """Load + parse every Venezuela-program SDN row into the cache.

    Holds _CACHE_LOCK while writing — readers should call ensure_loaded()
    which acquires the same lock briefly to coordinate.
    """
    from src.models import ExternalArticleEntry, SessionLocal, SourceType, init_db

    init_db()
    db = SessionLocal()
    try:
        rows = (
            db.query(ExternalArticleEntry)
            .filter(ExternalArticleEntry.source == SourceType.OFAC_SDN)
            .order_by(ExternalArticleEntry.published_date.desc())
            .all()
        )

        by_bucket_slug: dict[tuple[str, str], SDNProfile] = {}
        by_bucket: dict[str, list[SDNProfile]] = {b: [] for b in ENTITY_BUCKETS}
        by_uid: dict[str, SDNProfile] = {}
        name_to_profiles: dict[str, list[SDNProfile]] = {}
        family_clusters: dict[str, list[SDNProfile]] = {}

        for r in rows:
            meta = r.extra_metadata or {}
            raw_type = (meta.get("type") or "").lower().strip()
            bucket = _TYPE_TO_BUCKET.get(raw_type, "entities")
            raw_name = (meta.get("name") or r.headline or "").strip()
            if not raw_name:
                continue

            slug = _slugify(raw_name)
            # Slug collisions are possible if two entries share the same
            # name post-normalization (e.g. two PDVSA subsidiaries called
            # "PDVSA"). De-collide deterministically by appending a short
            # uid suffix — preserves URL stability across reloads.
            key = (bucket, slug)
            if key in by_bucket_slug:
                slug = f"{slug}-{(meta.get('uid') or str(r.id))[-6:]}"
                key = (bucket, slug)

            program = (meta.get("program") or "").upper().strip()
            program_label = PROGRAM_LABELS.get(program, program or "Venezuela-related sanctions")
            parsed, linked = _parse_remarks(meta.get("remarks") or "")
            display = _display_name(raw_name) if bucket == "individuals" else _titlecase_acronym_safe(raw_name)

            profile = SDNProfile(
                db_id=r.id,
                uid=meta.get("uid") or str(r.id),
                raw_name=raw_name,
                display_name=display,
                bucket=bucket,
                slug=slug,
                program=program,
                program_label=program_label,
                program_eo_url=PROGRAM_EXEC_ORDERS.get(program),
                source_url=r.source_url or "https://ofac.treasury.gov/specially-designated-nationals-and-blocked-persons-list-sdn-human-readable-lists",
                designation_date=r.published_date.isoformat() if r.published_date else None,
                raw_remarks=(meta.get("remarks") or "").strip(),
                parsed=parsed,
                linked_to=linked,
            )

            by_bucket_slug[key] = profile
            by_bucket[bucket].append(profile)
            by_uid[profile.uid] = profile
            name_to_profiles.setdefault(_normalize_for_match(raw_name), []).append(profile)
            if profile.is_individual:
                surname = _surname(raw_name)
                if surname:
                    family_clusters.setdefault(surname.upper(), []).append(profile)

        # Alpha-sort each bucket (stable; safe to enumerate for index pages).
        for bucket in by_bucket:
            by_bucket[bucket].sort(key=lambda p: p.raw_name.upper())

        _CACHE.update({
            "loaded_at": time.time(),
            "by_bucket_slug": by_bucket_slug,
            "by_bucket": by_bucket,
            "by_uid": by_uid,
            "family_clusters": family_clusters,
            "name_to_profiles": name_to_profiles,
        })
    finally:
        db.close()


def ensure_loaded(force_refresh: bool = False) -> None:
    """Lazy-load the cache; refresh if older than TTL or `force_refresh`."""
    now = time.time()
    if (
        not force_refresh
        and _CACHE["by_bucket_slug"]
        and (now - _CACHE["loaded_at"]) < _CACHE_TTL_SECONDS
    ):
        return
    with _CACHE_LOCK:
        if (
            not force_refresh
            and _CACHE["by_bucket_slug"]
            and (time.time() - _CACHE["loaded_at"]) < _CACHE_TTL_SECONDS
        ):
            return
        _load_from_db()


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────


def get_profile(bucket: str, slug: str) -> Optional[SDNProfile]:
    """Resolve one profile by bucket + slug. Returns None for unknown
    bucket/slug combos so the route can 404 cleanly."""
    if bucket not in ENTITY_BUCKETS:
        return None
    ensure_loaded()
    return _CACHE["by_bucket_slug"].get((bucket, slug))


def list_profiles(bucket: str) -> list[SDNProfile]:
    """All profiles in a bucket, alpha-sorted by raw OFAC name."""
    if bucket not in ENTITY_BUCKETS:
        return []
    ensure_loaded()
    return list(_CACHE["by_bucket"].get(bucket, []))


def list_all_profiles() -> list[SDNProfile]:
    """Every profile across every bucket — used for sitemap + IndexNow."""
    ensure_loaded()
    out: list[SDNProfile] = []
    for bucket in ENTITY_BUCKETS:
        out.extend(_CACHE["by_bucket"].get(bucket, []))
    return out


def family_members(profile: SDNProfile, *, limit: int = 8) -> list[SDNProfile]:
    """Other individuals sharing the same surname (excluding `profile`).

    The Carretero Napolitano case in our GSC data is the canonical
    motivator — when a researcher lands on Vicente Luis Carretero
    Napolitano's profile, the most useful next click is to the other
    sanctioned family members. OFAC publishes them as separate listings
    but doesn't link them; we do.
    """
    if not profile.is_individual:
        return []
    ensure_loaded()
    surname = _surname(profile.raw_name)
    if not surname:
        return []
    cluster = _CACHE["family_clusters"].get(surname.upper(), [])
    return [p for p in cluster if p.db_id != profile.db_id][:limit]


def resolve_linked_to(profile: SDNProfile, *, limit: int = 6) -> list[tuple[str, Optional[SDNProfile]]]:
    """For each `Linked To: …` mention in the profile's remarks, return
    (raw_name, matched_profile_or_None). Renderer can show the name
    either as a plain string (when no profile match) or as a hyperlink
    to /sanctions/<bucket>/<slug>.
    """
    ensure_loaded()
    out: list[tuple[str, Optional[SDNProfile]]] = []
    for link_name in profile.linked_to[:limit]:
        norm = _normalize_for_match(link_name)
        candidates = _CACHE["name_to_profiles"].get(norm, [])
        out.append((link_name, candidates[0] if candidates else None))
    return out


def stats() -> dict[str, int]:
    """Aggregate counts for index pages and structured data."""
    ensure_loaded()
    return {
        bucket: len(_CACHE["by_bucket"].get(bucket, []))
        for bucket in ENTITY_BUCKETS
    } | {"total": sum(len(v) for v in _CACHE["by_bucket"].values())}


def find_related_news(profile: SDNProfile, *, limit: int = 5) -> list[dict]:
    """Find recent analyzed news articles that mention this entity by name.

    Uses a case-insensitive substring match on the raw OFAC name (the
    "SURNAME, Given Names" form) AND on the natural-order display name,
    because some news outlets format names one way and OFAC another.
    Returns analyzer-ready dicts so the template stays presentation-only.
    """
    from src.models import ExternalArticleEntry, AssemblyNewsEntry, SessionLocal, init_db
    from sqlalchemy import or_

    # Build search needles. Keep them >=4 chars to avoid false positives
    # on common short names.
    needles: list[str] = []
    surname = _surname(profile.raw_name)
    if surname and len(surname) >= 4:
        needles.append(surname.lower())
    if profile.bucket != "individuals" and len(profile.raw_name) >= 4:
        needles.append(profile.raw_name.lower())

    if not needles:
        return []

    init_db()
    db = SessionLocal()
    try:
        results: list[dict] = []
        for model in (ExternalArticleEntry, AssemblyNewsEntry):
            q = db.query(model)
            from sqlalchemy import func as _func
            ors = []
            for n in needles:
                ors.append(_func.lower(model.headline).contains(n))
                ors.append(_func.lower(model.body_text).contains(n))
            q = q.filter(or_(*ors)).order_by(model.published_date.desc()).limit(limit)
            for row in q.all():
                analysis = row.analysis_json or {}
                results.append({
                    "headline": analysis.get("headline_short") or row.headline,
                    "url": getattr(row, "source_url", None),
                    "date": row.published_date.isoformat() if row.published_date else None,
                    "source": getattr(row, "source_name", None) or "Source",
                })
        # Dedupe by URL, sort newest first, cap.
        seen: set[str] = set()
        uniq: list[dict] = []
        for r in sorted(results, key=lambda x: x["date"] or "", reverse=True):
            key = r.get("url") or r["headline"]
            if key in seen:
                continue
            seen.add(key)
            uniq.append(r)
            if len(uniq) >= limit:
                break
        return uniq
    finally:
        db.close()
