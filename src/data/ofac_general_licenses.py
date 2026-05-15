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

OFFICIAL_OFAC_LICENSE_META: dict[str, dict[str, str]] = {
    "GL 2A": {
        "title": "Authorizing Certain New Debt, New Equity, and Securities Transactions Involving PDV Holding, Inc. and CITGO Holding, Inc.",
        "date": "August 05, 2019",
    },
    "GL 3I": {
        "title": "Authorizing Transactions Related to, Provision of Financing for, and Other Dealings in Certain Bonds",
        "date": "October 18, 2023",
    },
    "GL 4C": {
        "title": "Authorizing Certain New Debt Transactions and Other Transactions Involving Certain Blocked Persons Related to the Exportation or Reexportation of Agricultural Commodities, Medicine, Medical Devices, Replacement Parts and Components, or Software Updates",
        "date": "August 05, 2019",
    },
    "GL 5W": {
        "title": "Authorizing Certain Transactions Related to the Petróleos de Venezuela, S.A. 2020 8.5 Percent Bond on or After June 19, 2026",
        "date": "May 04, 2026",
    },
    "GL 7C": {
        "title": "Authorizing Certain Activities Involving PDV Holding, Inc. and CITGO Holding, Inc.",
        "date": "August 05, 2019",
    },
    "GL 9H": {
        "title": "Authorizing Transactions Related to Dealings in Certain Securities",
        "date": "October 18, 2023",
    },
    "GL 10A": {
        "title": "Authorizing the Purchase in Venezuela of Refined Petroleum Products from Petróleos de Venezuela, S.A.",
        "date": "August 05, 2019",
    },
    "GL 15C": {"title": "Authorizing Transactions Involving Certain Banks for Certain Entities", "date": "May 12, 2020"},
    "GL 16C": {
        "title": "Authorizing Maintenance of U.S. Person Accounts and Noncommercial, Personal Remittances involving Certain Banks",
        "date": "March 12, 2020",
    },
    "GL 18A": {
        "title": "Authorizing Certain Transactions Involving Integración Administradora de Fondos de Ahorro Previsional, S.A.",
        "date": "August 05, 2019",
    },
    "GL 21": {
        "title": "Entries in Certain Accounts for Normal Service Charges and Payments and Transfers to Blocked Accounts in U.S. Financial Institutions Authorized",
        "date": "August 05, 2019",
    },
    "GL 22": {"title": "Venezuela's Mission to the United Nations", "date": "August 05, 2019"},
    "GL 23": {"title": "Third-Country Diplomatic and Consular Funds Transfers Authorized", "date": "August 05, 2019"},
    "GL 24": {
        "title": "Certain Transactions Involving the Government of Venezuela Related to Telecommunications and Mail Authorized",
        "date": "August 05, 2019",
    },
    "GL 25": {
        "title": "Exportation of Certain Services, Software, Hardware, and Technology Incident to the Exchange of Communications over the Internet Authorized",
        "date": "August 05, 2019",
    },
    "GL 26": {"title": "Emergency and Certain Other Medical Services Authorized", "date": "August 05, 2019"},
    "GL 27": {"title": "Certain Transactions Related to Patents, Trademarks, and Copyrights Authorized", "date": "August 05, 2019"},
    "GL 29": {
        "title": "Certain Transactions Involving the Government of Venezuela in Support of Certain Nongovernmental Organizations' Activities Authorized",
        "date": "August 05, 2019",
    },
    "GL 30B": {"title": "Authorizing Certain Transactions Necessary to Port and Airport Operations", "date": "February 10, 2026"},
    "GL 31B": {
        "title": "Certain Transactions Involving the IV Venezuelan National Assembly and Certain Other Persons",
        "date": "January 09, 2023",
    },
    "GL 32": {
        "title": "Authorizing Certain Transactions Related to Personal Maintenance of Individuals who are U.S. Persons Residing in Venezuela",
        "date": "August 05, 2019",
    },
    "GL 33": {"title": "Authorizing Overflight Payments, Emergency Landings, and Air Ambulance Services", "date": "August 05, 2019"},
    "GL 34A": {"title": "Authorizing Transactions Involving Certain Government of Venezuela Persons", "date": "November 05, 2019"},
    "GL 35": {"title": "Authorizing Certain Administrative Transactions with the Government of Venezuela", "date": "November 05, 2019"},
    "GL 40D": {"title": "Authorizing the Offloading of Liquefied Petroleum Gas in Venezuela", "date": "July 07, 2025"},
    "GL 42": {
        "title": "Authorizing Certain Transactions Related to the Negotiation of Certain Settlement Agreements with the IV Venezuelan National Assembly and Certain Other Persons",
        "date": "May 01, 2023",
    },
    "GL 45B": {
        "title": "Authorizing Certain Repatriation Transactions Involving Consorcio Venezolano de Industrias Aeronáuticas y Servicios Aéreos, S.A.",
        "date": "February 29, 2024",
    },
    "GL 46B": {"title": "Authorizing Certain Activities Involving Venezuelan-Origin Oil or Petrochemical Products", "date": "March 13, 2026"},
    "GL 47": {"title": "Authorizing the Sale of U.S.-Origin Diluents to Venezuela", "date": "February 03, 2026"},
    "GL 48A": {"title": "Authorizing the Supply of Certain Items and Services to Venezuela", "date": "March 13, 2026"},
    "GL 49A": {
        "title": "Authorizing Negotiations of and Entry Into Contingent Contracts for Certain Investment in Venezuela",
        "date": "March 13, 2026",
    },
    "GL 50A": {
        "title": "Authorizing Transactions Related to Oil or Gas Sector Operations in Venezuela of Certain Entities",
        "date": "February 18, 2026",
    },
    "GL 51A": {"title": "Authorizing Certain Activities Involving Venezuelan-Origin Minerals, Including Gold", "date": "March 27, 2026"},
    "GL 52": {"title": "Authorizing Certain Transactions Involving Petróleos de Venezuela, S.A.", "date": "March 18, 2026"},
    "GL 53": {"title": "Official Missions of the Government of Venezuela to the United States", "date": "March 24, 2026"},
    "GL 54": {"title": "Authorizing the Supply of Certain Items and Services for Minerals Operations in Venezuela", "date": "March 27, 2026"},
    "GL 55": {
        "title": "Authorizing Negotiations of and Entry Into Contingent Contracts for Certain Investment in Venezuela's Minerals Sector",
        "date": "March 27, 2026",
    },
    "GL 56": {
        "title": "Authorizing Commercial-Related Negotiations of Contingent Contracts with the Government of Venezuela",
        "date": "April 14, 2026",
    },
    "GL 57": {
        "title": "Authorizing Financial Services Transactions Involving Certain Venezuelan Banks and Government of Venezuela Individuals",
        "date": "April 14, 2026",
    },
    "GL 58": {
        "title": "Authorizing Certain Services to the Government of Venezuela in Connection with Potential Debt Restructuring",
        "date": "May 05, 2026",
    },
}


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
        ("OFAC listing date", enriched.get("ofac_listing_date") or "Check current OFAC text"),
        ("Last checked", _display_checked_at(enriched.get("scraped_at"))),
        ("Source", "OFAC source" if enriched.get("source") == "live" else "Caracas Research entry"),
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
        for row in merged:
            row["scraped_at"] = cached.get("scraped_at")
            row["source_urls"] = cached.get("source_urls") or []
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
    official = OFFICIAL_OFAC_LICENSE_META.get(number, {})

    title = (row.get("title") or "").strip()
    generic_titles = {
        "",
        number,
        f"Venezuela {number}",
        f"Venezuela General License {number.replace('GL ', '')}",
    }
    if title in generic_titles:
        row["title"] = official.get("title") or curated.get("title") or title
    else:
        row["title"] = official.get("title") or title

    scope = _normalized_scope(
        list(row.get("scope") or [])
        + list(curated.get("scope") or [])
        + _scope_from_text(" ".join([row.get("title") or "", row.get("summary") or "", row.get("context") or ""]))
    )
    profile = _metadata_profile(number, scope)
    if (row.get("title") or "").strip() in generic_titles:
        row["title"] = profile["title"]

    row["summary"] = row.get("summary") or curated.get("summary") or profile["summary"]
    row["context"] = row.get("context") or curated.get("context") or profile["context"]
    row["expires"] = row.get("expires") or curated.get("expires") or "Check current OFAC text"
    row["scope"] = scope
    if official.get("date"):
        row["ofac_listing_date"] = official["date"]


def _normalized_scope(scope: list[str]) -> list[str]:
    values: list[str] = []
    for item in scope or []:
        clean = str(item).strip().lower()
        if clean and clean not in values and clean != "general":
            values.append(clean)
    return values or ["venezuela", "ofac"]


def _scope_from_text(text: str) -> list[str]:
    lowered = (text or "").lower()
    checks = [
        ("oil-gas", ("oil", "gas", "petroleum", "pdvsa", "petrochemical", "diluents", "lpg")),
        ("energy", ("energy", "pdvsa", "citgo", "petroleum", "liquefied petroleum")),
        ("debt", ("bond", "debt", "securities", "financing", "restructuring")),
        ("banking", ("bank", "financial services", "funds transfers", "accounts", "remittances")),
        ("diplomatic", ("diplomatic", "consular", "mission", "united nations", "official missions")),
        ("telecom", ("telecommunications", "mail", "internet", "software", "hardware", "communications")),
        ("humanitarian", ("medical", "medicine", "agricultural", "nongovernmental", "ngo", "emergency")),
        ("aviation", ("airport", "overflight", "landing", "air ambulance", "aeronáuticas")),
        ("legal", ("settlement", "court", "litigation", "judgment", "patents", "trademarks", "copyrights")),
        ("mining", ("gold", "mining", "minerals")),
        ("investment", ("investment", "contingent contracts", "commercial-related negotiations")),
        ("government", ("government of venezuela", "national assembly", "government persons")),
    ]
    scopes: list[str] = []
    for scope, needles in checks:
        if any(needle in lowered for needle in needles):
            scopes.append(scope)
    return scopes


def _display_checked_at(value: str | None) -> str:
    if not value:
        return "Checked daily"
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return str(value)[:10]
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


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
    source_page = row.get("source_page") or "OFAC's Venezuela sanctions program page"
    listing_date = row.get("ofac_listing_date") or "the current OFAC listing"
    checked = _display_checked_at(row.get("scraped_at"))
    visible_scopes = [s for s in scopes if s not in ("general", "ofac")]
    scope_text = ", ".join(visible_scopes) if visible_scopes else "Venezuela sanctions"

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
            f"{number} is a Venezuela-related OFAC authorization. It should be read as "
            "a narrow permission for the transactions described in the official text, "
            "not as broad sanctions relief for dealings with blocked Venezuelan parties."
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
        "current_context": [
            (
                f"Our tracker currently classifies {number} under {scope_text}. The OFAC "
                f"program page lists this license as '{title}' with an OFAC listing date of {listing_date}."
            ),
            (
                f"The source document is linked from {source_page}. Caracas Research last "
                f"checked the OFAC cache for this page at {checked}, and the daily monitor "
                "will surface new or changed Venezuela general-license links when OFAC publishes them."
            ),
            (
                "For compliance work, treat this page as a research brief and the Treasury "
                "document as the operative authority. The practical question is whether the "
                "specific parties, payment path, timing, and transaction purpose fit the license text."
            ),
        ],
        "review_items": [
            "Covered parties and whether the license reaches the specific counterparty or only a named class of entities",
            "Permitted transaction types, including any limits on payments, services, exports, imports, custody, or settlement",
            "Effective dates, expiration dates, wind-down language, and whether a newer lettered version replaced the license",
            "Carve-outs for blocked persons, Government of Venezuela entities, PdVSA affiliates, or SDN-owned counterparties",
            "Reporting, recordkeeping, or notification language that may matter after the transaction closes",
        ],
        "monitoring_items": [
            "Replacement license numbers or letter updates on OFAC's Venezuela sanctions page",
            "Recent-actions notices that change the scope of Venezuela energy, debt, banking, or humanitarian permissions",
            "Related licenses with the same coverage tags in the Caracas Research directory",
        ],
    }
