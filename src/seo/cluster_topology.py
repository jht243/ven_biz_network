"""
Internal-linking topic-cluster topology — the single source of truth for
which page belongs to which cluster, who the pillar is, and the exact
anchor text every backlink should use.

Why a dedicated module:
  • Modern SEO ("topical authority", post-Helpful-Content / SGE-aware
    Google) rewards comprehensive topic coverage with a clear pillar +
    a mesh of cluster pages that link to the pillar AND to each other
    AND down to deeper child pages — with descriptive anchor text.
    The hub-and-spoke version is dated; mesh is current.
  • Hardcoding cluster lists in five different templates produces drift
    (one template gets a new link, the others don't, Google sees an
    inconsistent signal). Centralising here keeps every backlink
    coherent — and lets us audit programmatically (e.g. "how many
    pages link to /tools/ofac-venezuela-general-licenses?").
  • Anchor text matters for SEO. We canonicalise it here so every
    inbound link to a cluster member uses the same searchable phrase.

Public API (kept tiny on purpose):
    cluster_for(path)           -> Cluster | None
    other_members(path)         -> list[ClusterLink]
    pillar_link_for(path)       -> ClusterLink | None
    sector_for_program(program) -> str | None  (path)
    program_to_sector_links()   -> dict[str, ClusterLink]

Templates use these via _cluster_nav.html.j2 so the rendered nav is
always in lockstep with the topology defined here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# Map OFAC program code → most-relevant sector landing page slug.
# These are the four Venezuela-related programs OFAC issues under,
# matched to the closest sector page in our /sectors/<slug> family.
# When a profile's program isn't mapped (e.g. plain "VENEZUELA" with
# no executive-order suffix), we don't surface a sector backlink on
# that profile — better no link than a wrong link.
_PROGRAM_TO_SECTOR_SLUG: dict[str, str] = {
    "VENEZUELA-EO13850": "mining",      # Gold-sector / public officials EO
    "VENEZUELA-EO13884": "governance",  # Government-of-Venezuela block EO
    "VENEZUELA-EO13692": "governance",  # Human-rights / corruption EO
    # Plain "VENEZUELA" intentionally unmapped — too broad to map cleanly.
}

# Canonical anchor-text phrases for high-traffic pages. Every inbound
# link from any cluster nav uses these exact strings so Google sees a
# consistent topical signal (instead of "click here" / "learn more").
_ANCHOR: dict[str, str] = {
    "/sanctions-tracker": "OFAC Venezuela SDN list — full searchable US Treasury tracker",
    "/sanctions/individuals": "All sanctioned individuals on the Venezuela SDN list",
    "/sanctions/entities": "All sanctioned entities (companies and orgs) on the Venezuela SDN list",
    "/sanctions/vessels": "All sanctioned vessels under Venezuela-related programs",
    "/sanctions/aircraft": "All sanctioned aircraft under Venezuela-related programs",
    "/sanctions/by-sector": "OFAC Venezuela SDN list by sector (military, economic, diplomatic, governance)",
    "/sanctions/sector/military": "Currently sanctioned Venezuelan military officials (FANB, GNB, DGCIM, SEBIN)",
    "/sanctions/sector/economic": "Sanctioned Venezuelan economic & financial actors (PDVSA, BCV, banks, gold sector)",
    "/sanctions/sector/diplomatic": "Sanctioned Venezuelan diplomatic officials (foreign-ministry, ambassadors)",
    "/sanctions/sector/governance": "Sanctioned Venezuelan government & political officials (TSJ, CNE, ministers)",
    "/tools/ofac-venezuela-sanctions-checker": "OFAC Venezuela sanctions checker (search any name)",
    "/tools/ofac-venezuela-general-licenses": "OFAC General Licenses for Venezuela (full list)",
    "/tools/public-company-venezuela-exposure-check": "Public company Venezuela exposure check (S&P 500)",
    "/tools/sec-edgar-venezuela-impairment-search": "SEC EDGAR Venezuela / PDVSA / impairment / contingent-liability search (S&P 500)",
    "/companies": "S&P 500 Venezuela exposure register (every ticker, A-Z)",
    "/explainers/what-are-ofac-sanctions-on-venezuela": "OFAC Venezuela: sanctions programs & General Licenses (2026 guide)",

    "/invest-in-venezuela": "How to invest in Venezuela (2026 sanctions-safe guide)",
    "/sectors/oil-gas": "Venezuela oil & gas sector — regulation, sanctions, deals",
    "/sectors/mining": "Venezuela mining sector — gold, sanctions, legal framework",
    "/sectors/banking": "Venezuela banking sector — regulation and OFAC access",
    "/sectors/energy": "Venezuela energy sector — regulation and deal flow",
    "/sectors/telecom": "Venezuela telecom sector — regulation and deals",
    "/sectors/agriculture": "Venezuela agriculture sector — regulation and deals",
    "/sectors/real-estate": "Venezuela real estate sector — regulation and deals",
    "/sectors/legal": "Venezuela legal sector — regulatory framework",
    "/sectors/governance": "Venezuela governance sector — regulatory framework, risks",
    "/sectors/economic": "Venezuela economic outlook — rules, deal flow, risks",
    "/sectors/tourism": "Venezuela tourism sector — regulatory framework",
    "/sectors/diplomatic": "Venezuela diplomatic sector — sanctions and protocol",
    "/sectors/sanctions": "Venezuela sanctions sector — OFAC licenses and deal flow",
    "/tools/venezuela-investment-roi-calculator": "Venezuela investment ROI calculator (sector-by-sector)",
    "/explainers/how-to-buy-venezuelan-bonds": "How to buy Venezuelan sovereign and PDVSA bonds (2026)",
    "/explainers/doing-business-in-caracas": "Doing business in Caracas — operating manual for foreign investors",

    "/travel": "Venezuela & Caracas travel — US advisory, safety, hotels, visa",
    "/travel/emergency-card": "Caracas emergency contact card (printable PDF)",
    "/tools/venezuela-visa-requirements": "Venezuela visa requirements by passport (2026)",
    "/tools/caracas-safety-by-neighborhood": "Caracas safety by neighborhood (interactive map)",

    "/tools/bolivar-usd-exchange-rate": "Bolívar to USD exchange rate (live BCV + parallel)",
    "/explainers/venezuelan-bolivar-explained": "The Venezuelan bolívar explained — history and devaluations",
    "/explainers/what-is-the-banco-central-de-venezuela": "What is the Banco Central de Venezuela (BCV)? — 2026 guide",
}


@dataclass(frozen=True)
class ClusterLink:
    """One link in a cluster nav block. Path + anchor text + a short
    description sentence rendered as supporting copy in the nav UI.
    """
    path: str
    anchor: str
    description: str = ""


@dataclass(frozen=True)
class Cluster:
    """A topic cluster: one pillar + N cluster members.

    `members` does NOT include the pillar — templates render the pillar
    distinctly (sticky, top-of-block) and other members alongside.
    """
    key: str            # internal id (e.g. "sanctions")
    name: str           # human label for the cluster nav title
    pillar: ClusterLink
    members: tuple[ClusterLink, ...]
    summary: str = ""   # One-sentence elevator pitch for the topic

    def all_paths(self) -> tuple[str, ...]:
        return (self.pillar.path,) + tuple(m.path for m in self.members)


def _ck(path: str, description: str = "") -> ClusterLink:
    """Construct a ClusterLink from a path using the canonical anchor."""
    return ClusterLink(
        path=path,
        anchor=_ANCHOR.get(path, path),
        description=description,
    )


# ──────────────────────────────────────────────────────────────────────
# The four clusters
# ──────────────────────────────────────────────────────────────────────

CLUSTERS: dict[str, Cluster] = {
    "sanctions": Cluster(
        key="sanctions",
        name="OFAC Venezuela Sanctions",
        summary=(
            "The full Caracas Research coverage of US Treasury OFAC "
            "Venezuela-related sanctions — live SDN tracker, per-name profile "
            "pages for every individual / entity / vessel / aircraft, the active "
            "general licenses, and a plain-English explainer."
        ),
        pillar=_ck(
            "/sanctions-tracker",
            "Live tracker of all 410 active OFAC Venezuela-program designations.",
        ),
        members=(
            _ck("/sanctions/by-sector", "Pivot the SDN list by sector: military, economic, diplomatic, governance."),
            _ck("/sanctions/sector/military",   "All sanctioned Venezuelan military officials (FANB, GNB, DGCIM, SEBIN)."),
            _ck("/sanctions/sector/economic",   "All sanctioned banks, oil-sector entities, and financial actors."),
            _ck("/sanctions/sector/diplomatic", "All sanctioned ambassadors and foreign-ministry officials."),
            _ck("/sanctions/sector/governance", "All sanctioned political and judicial officials."),
            _ck("/sanctions/individuals", "Browse the 190 sanctioned individuals A-Z, each with a full profile."),
            _ck("/sanctions/entities",    "Browse the 103 sanctioned companies and organisations A-Z."),
            _ck("/sanctions/vessels",     "Every blocked vessel — IMO, MMSI, year of build, parent company."),
            _ck("/sanctions/aircraft",    "Every blocked aircraft — model, MSN, tail number, registered owner."),
            _ck("/tools/ofac-venezuela-sanctions-checker", "Paste any name to instantly check it against the live SDN list."),
            _ck("/tools/ofac-venezuela-general-licenses",  "All active OFAC GLs that authorize otherwise-prohibited transactions."),
            _ck("/tools/public-company-venezuela-exposure-check", "Type any S&P 500 name or ticker to surface OFAC + EDGAR + news exposure."),
            _ck("/tools/sec-edgar-venezuela-impairment-search",   "Run a pre-canned EDGAR full-text search for Venezuela, PDVSA, CITGO, impairment, or contingent-liability disclosures across any S&P 500 ticker."),
            _ck("/companies", "A-Z directory of every S&P 500 company with a Venezuela-exposure profile."),
            _ck("/explainers/what-are-ofac-sanctions-on-venezuela", "Plain-English overview of how Venezuela-related OFAC sanctions work."),
        ),
    ),

    "investment": Cluster(
        key="investment",
        name="Investing in Venezuela",
        summary=(
            "How institutional investors can take sanctions-safe exposure to "
            "Venezuela — sector landing pages, ROI math, the bond market, and "
            "an operating manual for doing business in Caracas."
        ),
        pillar=_ck(
            "/invest-in-venezuela",
            "The 2026 sanctions-safe guide to taking exposure to Venezuela.",
        ),
        members=(
            _ck("/sectors/oil-gas",      "Energy majors, PDVSA exposure, OFAC pathways for oil-sector deals."),
            _ck("/sectors/mining",       "Gold mining and OFAC EO 13850 — licenses, blocked actors, deal flow."),
            _ck("/sectors/banking",      "Banking-sector regulation, OFAC SDN exposure, correspondent access."),
            _ck("/sectors/energy",       "Power-generation and energy-infrastructure regulation in Venezuela."),
            _ck("/sectors/telecom",      "Telecom-sector regulation, sanctions-relevant operators, deal flow."),
            _ck("/sectors/agriculture",  "Venezuela agriculture sector — regulation, exporter rules, deals."),
            _ck("/sectors/real-estate",  "Real-estate transactions in Venezuela — title, currency, FX risk."),
            _ck("/sectors/sanctions",    "Sanctions-as-a-sector — OFAC licenses + the compliance ecosystem."),
            _ck("/tools/venezuela-investment-roi-calculator", "Calculate ROI for any Venezuela sector — currency, country-risk premia."),
            _ck("/explainers/how-to-buy-venezuelan-bonds",     "How institutional investors access Venezuela sovereign and PDVSA bonds."),
            _ck("/explainers/doing-business-in-caracas",       "On-the-ground operating manual for foreign-investor teams in Caracas."),
        ),
    ),

    "travel": Cluster(
        key="travel",
        name="Venezuela Travel & Logistics",
        summary=(
            "Travel hub for investors, journalists, and diaspora — embassies, "
            "vetted hotels, vetted drivers, security, plus visa and "
            "neighborhood-safety tools."
        ),
        pillar=_ck(
            "/travel",
            "The Caracas Research travel hub — embassies, hotels, drivers, safety.",
        ),
        members=(
            _ck("/travel/emergency-card",                      "Printable single-page emergency contact card for Caracas trips."),
            _ck("/tools/venezuela-visa-requirements",          "Visa requirements for Venezuela by passport (live, 2026)."),
            _ck("/tools/caracas-safety-by-neighborhood",       "Interactive Caracas safety map by neighborhood."),
        ),
    ),

    "fx": Cluster(
        key="fx",
        name="Bolívar / USD & BCV",
        summary=(
            "Venezuela's currency and central-bank coverage — the daily BCV "
            "and parallel rate, the bolívar's history, and a 2026 explainer "
            "of how the BCV operates."
        ),
        pillar=_ck(
            "/tools/bolivar-usd-exchange-rate",
            "The bolívar-to-USD rate, live from BCV plus parallel-market sources.",
        ),
        members=(
            _ck("/explainers/venezuelan-bolivar-explained",     "History of the bolívar — devaluations, redenominations, dollarization."),
            _ck("/explainers/what-is-the-banco-central-de-venezuela", "What the BCV does, who runs it, and why it matters for investors."),
        ),
    ),
}


# Path-prefix → cluster key. Order matters — most-specific prefix first.
_PATH_TO_CLUSTER: tuple[tuple[str, str], ...] = (
    ("/sanctions-tracker",     "sanctions"),
    ("/sanctions/by-sector",   "sanctions"),
    ("/sanctions/sector/",     "sanctions"),
    ("/sanctions/",            "sanctions"),
    ("/tools/ofac-venezuela-sanctions-checker", "sanctions"),
    ("/tools/ofac-venezuela-general-licenses",  "sanctions"),
    ("/tools/public-company-venezuela-exposure-check", "sanctions"),
    ("/tools/sec-edgar-venezuela-impairment-search",   "sanctions"),
    ("/companies",             "sanctions"),
    ("/explainers/what-are-ofac-sanctions-on-venezuela", "sanctions"),

    ("/invest-in-venezuela",   "investment"),
    ("/sectors/",              "investment"),
    ("/tools/venezuela-investment-roi-calculator", "investment"),
    ("/explainers/how-to-buy-venezuelan-bonds",     "investment"),
    ("/explainers/doing-business-in-caracas",       "investment"),

    ("/travel",                "travel"),
    ("/tools/venezuela-visa-requirements",   "travel"),
    ("/tools/caracas-safety-by-neighborhood", "travel"),

    ("/tools/bolivar-usd-exchange-rate",     "fx"),
    ("/explainers/venezuelan-bolivar-explained",   "fx"),
    ("/explainers/what-is-the-banco-central-de-venezuela", "fx"),
)


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────


def cluster_for(path: str) -> Optional[Cluster]:
    """Return the Cluster a given URL path belongs to, or None.

    Strips trailing slash and treats prefix matches as the most-specific
    match per the _PATH_TO_CLUSTER order.
    """
    if not path:
        return None
    norm = "/" + path.lstrip("/").rstrip("/")
    if norm == "":
        norm = "/"
    for prefix, key in _PATH_TO_CLUSTER:
        if norm == prefix.rstrip("/") or norm.startswith(prefix):
            return CLUSTERS.get(key)
    return None


def other_members(path: str, *, limit: int = 12) -> list[ClusterLink]:
    """Return the cluster's other members (excluding `path` itself).

    Used to render "Continue exploring this topic →" lists. Caps at
    `limit` so the nav block stays scannable on mobile.
    """
    cluster = cluster_for(path)
    if cluster is None:
        return []
    norm = "/" + path.lstrip("/").rstrip("/")
    out: list[ClusterLink] = []
    for m in cluster.members:
        if m.path == norm:
            continue
        out.append(m)
        if len(out) >= limit:
            break
    return out


def pillar_link_for(path: str) -> Optional[ClusterLink]:
    """Return the pillar link for the given page's cluster, or None.

    If `path` IS the pillar, returns None (templates use this to decide
    whether to render the "back to pillar" callout).
    """
    cluster = cluster_for(path)
    if cluster is None:
        return None
    norm = "/" + path.lstrip("/").rstrip("/")
    if cluster.pillar.path == norm:
        return None
    return cluster.pillar


def sector_for_program(program: str) -> Optional[ClusterLink]:
    """Map an OFAC program code to its most-relevant sector landing
    page, returned as a ClusterLink (so templates get the canonical
    anchor text for free).

    Used by the SDN profile page to surface a "this {entity} operates
    in {sector}" backlink — which both serves the reader (one click to
    sector context) and serves SEO (descriptive anchor + reciprocal
    cluster signal between the sanctions and investment clusters).
    """
    if not program:
        return None
    slug = _PROGRAM_TO_SECTOR_SLUG.get(program.upper())
    if not slug:
        return None
    path = f"/sectors/{slug}"
    return ClusterLink(
        path=path,
        anchor=_ANCHOR.get(path, path),
        description="",
    )


def program_to_sector_links() -> dict[str, ClusterLink]:
    """Programmatic access to the full mapping (for tests / audits)."""
    out: dict[str, ClusterLink] = {}
    for prog, slug in _PROGRAM_TO_SECTOR_SLUG.items():
        path = f"/sectors/{slug}"
        out[prog] = ClusterLink(path=path, anchor=_ANCHOR.get(path, path))
    return out


def build_cluster_ctx(path: str, *, limit: int = 12) -> dict:
    """One-shot helper: returns the dict every template needs to render
    `_cluster_nav.html.j2`'s cluster_nav() macro.

    Returning a plain dict (rather than a dataclass) is intentional —
    Jinja's autoescape + attribute access work uniformly on dicts, and
    we don't need Python-side typing for a pure render-time payload.

    Returns empty-ish ctx (cluster=None) when the path is not in any
    registered cluster, which causes the macro to render nothing.
    Templates can therefore unconditionally `{{ cluster_nav(ctx) }}`
    without guards.
    """
    cluster = cluster_for(path)
    if cluster is None:
        return {"cluster": None, "pillar": None, "others": [], "is_pillar": False}
    pillar = pillar_link_for(path)
    return {
        "cluster": cluster,
        "pillar": pillar,
        "others": other_members(path, limit=limit),
        "is_pillar": pillar is None,
    }
