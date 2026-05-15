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
        "licenses": [dict(item, source="curated") for item in GENERAL_LICENSES],
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
        row["source"] = "live"
        merged.append(row)
        seen.add(number)

    # Keep curated licenses that OFAC did not expose in the latest scrape,
    # marked clearly so readers know they are fallback entries.
    for curated in GENERAL_LICENSES:
        number = (curated.get("number") or "").strip().upper()
        if number not in seen:
            row = dict(curated)
            row["source"] = "curated_fallback"
            merged.append(row)

    return sorted(merged, key=lambda item: _license_sort_key(item.get("number", "")))


def _strip_placeholder_fields(row: dict) -> None:
    if row.get("summary") == "Live OFAC listing. Open the official text for scope, conditions, and expiration details.":
        row["summary"] = ""
    if row.get("context") == "Detected from OFAC's public Venezuela sanctions and recent-actions pages.":
        row["context"] = ""
    if row.get("expires") == "See OFAC text":
        row["expires"] = ""


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
    import re

    match = re.search(r"(\d+)([A-Z]?)", number or "")
    if not match:
        return (9999, number or "")
    return (int(match.group(1)), match.group(2))
