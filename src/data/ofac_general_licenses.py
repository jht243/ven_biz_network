"""
Catalogue of currently-relevant OFAC general licenses (GLs) authorising
specific transactions involving Venezuela.

The page reads the live scraper cache first. This curated list remains as
the fallback and as analyst context merged onto live OFAC rows when the
number matches.

Authoritative source for everything below:
  https://ofac.treasury.gov/recent-actions
  https://ofac.treasury.gov/sanctions-programs-and-country-information/venezuela-related-sanctions

The site UI must always link readers back to the OFAC primary text;
this list is a navigation aid, not a legal substitute.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from src.scraper.ofac_general_licenses import load_cached_general_licenses

_LIVE_CACHE_MAX_AGE = timedelta(hours=12)


GENERAL_LICENSES: list[dict] = [
    {
        "number": "GL 5T",
        "title": "Authorising Certain Transactions Involving the PdVSA 2020 8.5% Bond",
        "summary": "Conditionally authorises holders of the PdVSA 2020 bond to engage in transactions related to the bond's collateral on or after a specified effective date, subject to ongoing OFAC interpretation.",
        "expires": "Conditional / extended",
        "scope": ["debt", "PDVSA"],
        "ofac_url": "https://ofac.treasury.gov/media/932451/download?inline",
        "context": "Critical for Venezuelan sovereign-debt holders — keeps the CITGO collateral question alive without forcing immediate enforcement.",
    },
    {
        "number": "GL 8M",
        "title": "Authorising Transactions Involving Petróleos de Venezuela, S.A. (PdVSA) Necessary for the Limited Maintenance of Essential Operations in Venezuela",
        "summary": "Permits Chevron, Halliburton, Schlumberger, Baker Hughes, and Weatherford to engage in essential maintenance operations involving PdVSA, with strict reporting requirements.",
        "expires": "Periodically renewed (typically 6-12 month terms)",
        "scope": ["energy", "oil-gas", "PDVSA"],
        "ofac_url": "https://ofac.treasury.gov/sanctions-programs-and-country-information/venezuela-related-sanctions",
        "context": "Underpins the limited continued presence of US oilfield service majors in Venezuela. Each renewal cycle is closely watched.",
    },
    {
        "number": "GL 41",
        "title": "Authorising Certain Transactions Related to Chevron Corporation's Joint Ventures in Venezuela",
        "summary": "Permits Chevron to lift, sell, and import Venezuelan-origin crude oil and petroleum products into the United States subject to specific conditions, including no payment of taxes or royalties to the Government of Venezuela.",
        "expires": "Subject to OFAC modification",
        "scope": ["energy", "oil-gas", "Chevron", "PDVSA"],
        "ofac_url": "https://ofac.treasury.gov/media/930516/download?inline",
        "context": "The single most important GL for the Venezuelan oil sector — restored Chevron's ability to physically lift crude after years of dormancy.",
    },
    {
        "number": "GL 42",
        "title": "Authorising Certain Transactions Necessary for Negotiation of Settlement Agreements Related to Certain US Court Cases",
        "summary": "Allows discrete legal-settlement-related transactions involving the Government of Venezuela that would otherwise be blocked.",
        "expires": "Open-ended / settlement-conditional",
        "scope": ["legal", "settlement"],
        "ofac_url": "https://ofac.treasury.gov/recent-actions/20221126",
        "context": "Used in legacy expropriation and ICSID-award contexts — narrow but important for arbitration practitioners.",
    },
    {
        "number": "GL 43",
        "title": "Authorising Certain Transactions Necessary to the Wind Down of the National Gas Company of Trinidad and Tobago Limited's Activities Involving the Dragon Gas Field",
        "summary": "Permits NGC and Shell to continue planning, drilling, financing, and offtake activities for the cross-border Dragon Gas Field project subject to specified conditions.",
        "expires": "Subject to renewal — most recent extension published 2024",
        "scope": ["energy", "natural-gas", "Trinidad", "PDVSA"],
        "ofac_url": "https://ofac.treasury.gov/media/932531/download?inline",
        "context": "Key for the Dragon Field development and broader cross-border gas integration with Trinidad. Signals a US tolerance for limited regional energy cooperation.",
    },
    {
        "number": "GL 44A",
        "title": "Authorising Certain Transactions Related to Oil or Gas Sector Operations in Venezuela",
        "summary": "A broader licence covering oil and gas sector transactions, periodically reissued with attached terms reflecting the political context (e.g. electoral conditionality).",
        "expires": "Subject to reissuance — terms have shifted with the political environment",
        "scope": ["energy", "oil-gas", "PDVSA"],
        "ofac_url": "https://ofac.treasury.gov/recent-actions/20240417",
        "context": "When in force, this is the most consequential general licence in the Venezuelan energy space. Its renewal/expiration is directly tied to milestones agreed in the Barbados political dialogue.",
    },
    {
        "number": "GL 45",
        "title": "Authorising the Wind Down of Transactions Involving Venezuelan Gold-Sector Entities",
        "summary": "Permits a limited wind-down period for transactions involving newly designated gold-sector entities, ensuring orderly counterparty exit rather than an immediate freeze.",
        "expires": "Time-limited (typically 30-90 days from designation)",
        "scope": ["mining", "gold", "wind-down"],
        "ofac_url": "https://ofac.treasury.gov/recent-actions",
        "context": "Compliance teams should diary any GL 45 expiration date as a hard cut-off for blocked-counterparty exposure.",
    },
    {
        "number": "GL 9G",
        "title": "Authorising Transactions Related to Dealings in Certain Securities",
        "summary": "Permits transactions in certain Venezuelan securities (debt or equity) that were issued prior to specified Executive Orders, subject to secondary market trading conditions.",
        "expires": "Periodically reissued",
        "scope": ["debt", "securities", "secondary-market"],
        "ofac_url": "https://ofac.treasury.gov/sanctions-programs-and-country-information/venezuela-related-sanctions",
        "context": "Foundational GL for distressed-debt secondary trading. Essential reading for any fund holding legacy Venezuelan paper.",
    },
    {
        "number": "GL 13H",
        "title": "Authorising Certain Administrative Transactions Involving Nynas AB",
        "summary": "Permits ongoing administrative transactions necessary for the operation of Nynas, a Swedish refining group historically linked to PdVSA.",
        "expires": "Periodically reissued",
        "scope": ["energy", "Europe", "wind-down"],
        "ofac_url": "https://ofac.treasury.gov/sanctions-programs-and-country-information/venezuela-related-sanctions",
        "context": "The European compliance reference point — shows how OFAC treats EU entities with PdVSA legacy ownership.",
    },
    {
        "number": "GL 7C",
        "title": "Authorising Certain Activities Involving PdVSA Subsidiaries (CITGO and PDV Holding)",
        "summary": "Permits transactions involving CITGO Petroleum, CITGO Holding, and PDV Holding, which are blocked by virtue of their PdVSA ownership.",
        "expires": "Periodically reissued",
        "scope": ["energy", "CITGO", "downstream"],
        "ofac_url": "https://ofac.treasury.gov/sanctions-programs-and-country-information/venezuela-related-sanctions",
        "context": "Why CITGO can keep operating in Texas and Louisiana refineries despite its parent being on the SDN list. Watch for any GL 7 modification — it directly affects the CITGO valuation in any restructuring scenario.",
    },
]


def list_general_licenses() -> list[dict]:
    return get_general_license_payload()["licenses"]


def license_slug(number: str) -> str:
    clean = re.sub(r"[^0-9A-Za-z]+", "-", number or "").strip("-").lower()
    return clean or "general-license"


def get_license_by_slug(slug: str) -> dict | None:
    for row in list_general_licenses():
        if license_slug(row.get("number", "")) == slug:
            return row
    return None


def enrich_license_for_page(row: dict) -> dict:
    """Add presentation fields for an internal license-analysis page."""
    enriched = dict(row)
    number = enriched.get("number") or "OFAC General License"
    title = enriched.get("title") or f"Venezuela {number}"
    scopes = [s for s in (enriched.get("scope") or []) if s != "general"]
    scope_text = ", ".join(scopes) if scopes else "Venezuela sanctions"

    enriched["slug"] = license_slug(number)
    enriched["detail_url"] = f"/tools/ofac-venezuela-general-licenses/{enriched['slug']}"
    enriched["seo_title"] = f"OFAC {number} Venezuela General License: Scope & Analysis"
    enriched["seo_description"] = (
        f"Plain-English analysis of OFAC {number} for Venezuela: what the license "
        f"covers, why it matters, source links, and related sanctions context."
    )
    enriched["analysis"] = _analysis_for_license(number, title, scopes, enriched)
    enriched["detail_rows"] = [
        ("License", number),
        ("OFAC title", title),
        ("Coverage", scope_text),
        ("Status", enriched.get("expires") or "Check current OFAC text"),
        ("Source", "Live OFAC scrape" if enriched.get("source") == "live" else "Curated Caracas Research entry"),
    ]
    return enriched


def get_general_license_payload() -> dict:
    """Return live cached OFAC GL data, falling back to the curated seed list."""
    cached = load_cached_general_licenses()
    if _cache_is_stale(cached):
        try:
            from src.scraper.ofac_general_licenses import OFACGeneralLicensesScraper

            scraper = OFACGeneralLicensesScraper()
            try:
                result = scraper.scrape()
            finally:
                scraper.close()
            if result.success:
                cached = load_cached_general_licenses()
        except Exception:
            # Keep page rendering deterministic. If OFAC or the network is
            # unavailable, the curated fallback below still gives readers a
            # useful compliance navigation aid.
            cached = cached or {}

    live_rows = cached.get("licenses") or []
    if live_rows:
        merged = _merge_live_with_curated(live_rows)
        payload = dict(cached)
        payload["licenses"] = merged
        payload["source"] = "live"
        return payload

    return {
        "source": "curated_fallback",
        "program": "venezuela",
        "scraped_at": None,
        "source_urls": [
            "https://ofac.treasury.gov/recent-actions",
            "https://ofac.treasury.gov/sanctions-programs-and-country-information/venezuela-related-sanctions",
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "licenses": [_curated_row(item) for item in GENERAL_LICENSES],
    }


def _merge_live_with_curated(live_rows: list[dict]) -> list[dict]:
    curated_by_number = {
        (item.get("number") or "").strip().upper(): item
        for item in GENERAL_LICENSES
    }
    merged: list[dict] = []
    seen: set[str] = set()

    for live in live_rows:
        number = (live.get("number") or "").strip().upper()
        curated = curated_by_number.get(number, {})
        row = dict(live)
        _strip_placeholder_fields(row)
        # Preserve official title/URL from live scrape, but keep analyst
        # summaries/context when the live OFAC page only exposes link text.
        for field in ("summary", "expires", "scope", "context"):
            if curated.get(field) and (
                not row.get(field)
                or str(row.get(field)).startswith("Live OFAC listing")
                or row.get(field) in ("See OFAC text", ["general"])
            ):
                row[field] = curated[field]
        if curated.get("title") and len(row.get("title", "")) < 12:
            row["title"] = curated["title"]
        _complete_license_metadata(row, curated)
        row["source"] = "live"
        row["slug"] = license_slug(number)
        row["detail_url"] = f"/tools/ofac-venezuela-general-licenses/{row['slug']}"
        merged.append(row)
        seen.add(number)

    # Keep curated licenses that OFAC did not expose in the latest scrape,
    # marked clearly so readers know they are fallback entries.
    for curated in GENERAL_LICENSES:
        number = (curated.get("number") or "").strip().upper()
        if number not in seen:
            row = dict(curated)
            row["source"] = "curated_fallback"
            row["slug"] = license_slug(number)
            row["detail_url"] = f"/tools/ofac-venezuela-general-licenses/{row['slug']}"
            merged.append(row)

    return sorted(merged, key=lambda item: _license_sort_key(item.get("number", "")))


def _curated_row(item: dict) -> dict:
    row = dict(item)
    number = (row.get("number") or "").strip().upper()
    row["source"] = "curated"
    row["slug"] = license_slug(number)
    row["detail_url"] = f"/tools/ofac-venezuela-general-licenses/{row['slug']}"
    return row


def _strip_placeholder_fields(row: dict) -> None:
    if row.get("summary") == "Live OFAC listing. Open the official text for scope, conditions, and expiration details.":
        row["summary"] = ""
    if row.get("context") == "Detected from OFAC's public Venezuela sanctions and recent-actions pages.":
        row["context"] = ""
    if row.get("expires") == "See OFAC text":
        row["expires"] = ""


def _complete_license_metadata(row: dict, curated: dict | None = None) -> None:
    """Give every live OFAC row enough public-facing context for cards/SEO."""
    curated = curated or {}
    number = (row.get("number") or curated.get("number") or "GL").strip().upper()
    scope = _normalized_scope(row.get("scope") or curated.get("scope") or [])
    profile = _metadata_profile(number, scope)

    title = (row.get("title") or "").strip()
    generic_titles = {
        "",
        number,
        f"Venezuela {number}",
        f"Venezuela General License {number.replace('GL ', '')}",
    }
    if title in generic_titles:
        row["title"] = curated.get("title") or profile["title"]
    else:
        row["title"] = title

    row["summary"] = row.get("summary") or curated.get("summary") or profile["summary"]
    row["context"] = row.get("context") or curated.get("context") or profile["context"]
    row["expires"] = row.get("expires") or curated.get("expires") or "Check current OFAC text"
    row["scope"] = scope


def _normalized_scope(scope: list[str]) -> list[str]:
    values: list[str] = []
    for item in scope or []:
        clean = str(item).strip().lower()
        if clean and clean not in values and clean != "general":
            values.append(clean)
    return values or ["venezuela", "ofac"]


def _metadata_profile(number: str, scope: list[str]) -> dict:
    scope_text = " ".join(scope).lower()
    suffix = number.replace("GL ", "")

    if any(term in scope_text for term in ("oil-gas", "energy")):
        return {
            "title": f"Venezuela General License {suffix}: Energy and PdVSA-Related Transactions",
            "summary": "Tracks OFAC authorization for Venezuela energy, oil-and-gas, or PdVSA-related activity. The official license text controls the exact counterparties, payments, and limits.",
            "context": "Key for investors and operators assessing whether Venezuela energy exposure can proceed under a current OFAC authorization.",
        }
    if any(term in scope_text for term in ("debt", "securities")):
        return {
            "title": f"Venezuela General License {suffix}: Debt and Securities Transactions",
            "summary": "Tracks OFAC authorization affecting Venezuelan debt, securities, bonds, or related settlement activity. Review the license text before trading, custody, or payment decisions.",
            "context": "Useful for funds, banks, and counsel mapping Venezuela securities exposure to OFAC's current permissions.",
        }
    if any(term in scope_text for term in ("mining", "gold")):
        return {
            "title": f"Venezuela General License {suffix}: Mining and Gold-Sector Transactions",
            "summary": "Tracks OFAC authorization connected to Venezuela mining or gold-sector activity. Check the official license for eligible parties, wind-down dates, and prohibited flows.",
            "context": "Important for counterparties screening exposure to Venezuela's gold and mining sanctions perimeter.",
        }
    if any(term in scope_text for term in ("legal", "settlement")):
        return {
            "title": f"Venezuela General License {suffix}: Legal and Settlement Transactions",
            "summary": "Tracks OFAC authorization for legal, court, arbitration, or settlement-related activity involving Venezuela sanctions restrictions.",
            "context": "Relevant for creditors, litigants, and advisers handling Venezuela claims or settlement mechanics.",
        }
    if "wind-down" in scope_text:
        return {
            "title": f"Venezuela General License {suffix}: Wind-Down Authorization",
            "summary": "Tracks OFAC authorization for limited wind-down activity tied to Venezuela sanctions. Timing, scope, and payment restrictions should be checked against the source text.",
            "context": "Use this as a timing flag for compliance teams managing exit activity or newly restricted counterparties.",
        }
    return {
        "title": f"Venezuela General License {suffix}: OFAC Authorization for Venezuela-Related Transactions",
        "summary": "Tracks an OFAC general license for Venezuela-related transactions. Use the internal analysis to understand the likely context, then confirm details in the official OFAC text.",
        "context": "A live OFAC-sourced license entry with Caracas Research context for investors, operators, and compliance teams.",
    }


def _cache_is_stale(payload: dict) -> bool:
    scraped_at = (payload or {}).get("scraped_at")
    if not scraped_at:
        return True
    try:
        checked = datetime.fromisoformat(scraped_at)
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True
    return datetime.now(timezone.utc) - checked > _LIVE_CACHE_MAX_AGE


def _license_sort_key(number: str) -> tuple[int, str]:
    match = re.search(r"(\d+)([A-Z]?)", number or "")
    if not match:
        return (9999, number or "")
    return (int(match.group(1)), match.group(2))


def _analysis_for_license(number: str, title: str, scopes: list[str], row: dict) -> dict:
    lowered = " ".join([title, " ".join(scopes), row.get("summary") or "", row.get("context") or ""]).lower()

    if any(term in lowered for term in ("oil", "gas", "pdvsa", "chevron", "energy", "petroleum")):
        importance = (
            f"{number} is part of the Venezuela energy-sanctions framework. "
            "Investors should treat it as a permissions boundary for oil, gas, PDVSA, "
            "or service-company exposure rather than as broad sanctions relief."
        )
    elif any(term in lowered for term in ("debt", "bond", "securities")):
        importance = (
            f"{number} matters most for Venezuelan sovereign, PDVSA, or related "
            "securities exposure. Funds and banks should map the license text to "
            "trade date, custody, settlement, and beneficial-owner controls."
        )
    elif any(term in lowered for term in ("citgo", "pdv holding")):
        importance = (
            f"{number} is relevant to the CITGO / PDV Holding structure and the "
            "sanctions perimeter around blocked PdVSA ownership. It should be read "
            "alongside any court, creditor, or restructuring developments."
        )
    elif any(term in lowered for term in ("wind", "gold", "mining")):
        importance = (
            f"{number} appears tied to a narrow wind-down or sector-specific sanctions "
            "permission. The practical issue is usually timing: what activity remains "
            "authorized, and when the authorization ends."
        )
    else:
        importance = (
            f"{number} is a Venezuela-related OFAC authorization. The live OFAC listing "
            "confirms the license exists, while the official text controls the exact "
            "conditions, exclusions, and expiration."
        )

    if row.get("summary"):
        plain_english = row["summary"]
    else:
        plain_english = (
            "OFAC's public listing provides the license title and source document. "
            "This page tracks the license and routes readers to the official text for the operative terms."
        )

    return {
        "plain_english": plain_english,
        "why_it_matters": importance,
        "watch_items": [
            "Amendments, extensions, or replacement license numbers",
            "Counterparty limits involving PdVSA, Venezuelan state entities, or blocked persons",
            "Wind-down dates, reporting requirements, and payment restrictions",
        ],
    }
