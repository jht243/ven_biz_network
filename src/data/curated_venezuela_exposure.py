"""
Curated, hand-maintained map of well-known Venezuela exposures for S&P 500
companies. This is the high-precision layer of the exposure engine — it
lets us assert "Chevron has direct exposure via PdVSA joint ventures
authorised by OFAC GL 41" instead of relying on string-match heuristics
alone.

For each ticker we list:
  - exposure_level: "direct" | "indirect" | "historical" | "none"
  - summary: a 1-2 sentence analyst note
  - subsidiaries: known Venezuela-related operating entities, brands, or
    counterparties associated with the parent (used as additional
    fuzzy-match terms against the OFAC SDN list and EDGAR / FR / our
    corpus)
  - ofac_licenses: relevant OFAC general licenses (GL number)
  - notes: internal extra context (not always rendered)

When a ticker is NOT in this map, the engine falls back to algorithmic
signals only and the page reads "no direct exposure on the public record"
— which is the answer most analysts come for.

This map is small on purpose. We keep it artisanal because false
positives (claiming a company is Venezuela-exposed when it isn't) are
much more harmful than false negatives. Add entries as research surfaces
them, not speculatively.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CuratedExposure:
    ticker: str
    exposure_level: str  # "direct" | "indirect" | "historical" | "none"
    summary: str
    subsidiaries: tuple[str, ...] = field(default_factory=tuple)
    ofac_licenses: tuple[str, ...] = field(default_factory=tuple)
    notes: str = ""


_CURATED: dict[str, CuratedExposure] = {
    "CVX": CuratedExposure(
        ticker="CVX",
        exposure_level="direct",
        summary=(
            "Chevron is the most operationally Venezuela-exposed S&P 500 company. "
            "It operates four joint ventures with PdVSA (Petroboscan, Petroindependiente, "
            "Petroindependencia, Petropiar) under OFAC General License 41, which authorises "
            "lifting and importation of Venezuelan-origin crude into the United States."
        ),
        subsidiaries=(
            "Chevron Venezuela",
            "Petroboscan",
            "Petroindependiente",
            "Petroindependencia",
            "Petropiar",
        ),
        ofac_licenses=("GL 41",),
        notes="GL 41 was issued November 2022 and renewed since. Subject to revocation.",
    ),
    "PSX": CuratedExposure(
        ticker="PSX",
        exposure_level="historical",
        summary=(
            "Phillips 66 is the largest US refiner of heavy sour crude, the grade Venezuela "
            "produces. It has historically been a major buyer of Venezuelan crude and remains "
            "a counterparty whenever OFAC general licenses authorise lifts."
        ),
        subsidiaries=("Phillips 66 Refining",),
        ofac_licenses=(),
        notes="Not an SDN counterparty; exposure is commercial/supply-chain.",
    ),
    "VLO": CuratedExposure(
        ticker="VLO",
        exposure_level="historical",
        summary=(
            "Valero is one of the few US Gulf Coast refiners configured for Venezuelan heavy "
            "crude. Pre-2019 sanctions tightening, Valero was a regular Venezuelan-crude "
            "buyer; sporadic lifts have resumed under OFAC general licenses."
        ),
        subsidiaries=("Valero Energy",),
        ofac_licenses=(),
    ),
    "MPC": CuratedExposure(
        ticker="MPC",
        exposure_level="historical",
        summary=(
            "Marathon Petroleum operates US Gulf Coast refineries with the cokers needed to "
            "process Venezuelan heavy crude. Past buyer; current activity tracks OFAC license "
            "issuance."
        ),
    ),
    "REPYY": CuratedExposure(  # Repsol ADR — not S&P 500 but commonly searched.
        ticker="REPYY",
        exposure_level="direct",
        summary=(
            "Repsol operates Petroquiriquire JV with PdVSA under a US-issued specific license. "
            "Listed here for completeness — not an S&P 500 constituent."
        ),
        subsidiaries=("Petroquiriquire", "Repsol Venezuela"),
    ),
    "MO": CuratedExposure(
        ticker="MO",
        exposure_level="none",
        summary=(
            "Altria's Venezuelan tobacco operations were divested in 2008 when its then-"
            "subsidiary Philip Morris exited the country following expropriations. No current "
            "operating exposure."
        ),
        notes="Historical only; included to pre-empt the 'Philip Morris Venezuela' query.",
    ),
    "KO": CuratedExposure(
        ticker="KO",
        exposure_level="indirect",
        summary=(
            "Coca-Cola maintains Venezuelan operations through its bottler Coca-Cola FEMSA "
            "(KOF), which has continued limited bottling and distribution despite repeated "
            "supply-chain interruptions. Reputational rather than sanctions exposure."
        ),
        subsidiaries=("Coca-Cola FEMSA Venezuela",),
    ),
    "PEP": CuratedExposure(
        ticker="PEP",
        exposure_level="indirect",
        summary=(
            "PepsiCo operates in Venezuela through Empresas Polar bottling and snacks "
            "partnerships. Operations have been disrupted by hyperinflation and currency "
            "controls but have not been fully exited."
        ),
        subsidiaries=("Empresas Polar Venezuela",),
    ),
    "PG": CuratedExposure(
        ticker="PG",
        exposure_level="historical",
        summary=(
            "Procter & Gamble suspended manufacturing in Venezuela in 2018 amid hyperinflation "
            "and currency controls. The Venezuelan subsidiary has been written down to zero on "
            "the balance sheet."
        ),
        subsidiaries=("Procter & Gamble de Venezuela",),
    ),
    "CL": CuratedExposure(
        ticker="CL",
        exposure_level="historical",
        summary=(
            "Colgate-Palmolive disclosed full deconsolidation of its Venezuelan subsidiary in "
            "2015 due to currency-control regulations preventing meaningful US-dollar control "
            "of operations."
        ),
        subsidiaries=("Colgate-Palmolive de Venezuela",),
    ),
    "JNJ": CuratedExposure(
        ticker="JNJ",
        exposure_level="historical",
        summary=(
            "Johnson & Johnson significantly reduced its Venezuelan footprint after 2015. "
            "Limited pharmaceutical distribution remains under humanitarian general licenses."
        ),
    ),
    "MMM": CuratedExposure(
        ticker="MMM",
        exposure_level="historical",
        summary="3M historically operated a Venezuelan subsidiary serving industrial and consumer markets; activity sharply curtailed post-2017.",
    ),
    "F": CuratedExposure(
        ticker="F",
        exposure_level="historical",
        summary=(
            "Ford's Venezuelan assembly plant in Valencia operated for decades but production "
            "fell to near-zero under hyperinflation. Ford has taken material writedowns on the "
            "Venezuelan operation."
        ),
        subsidiaries=("Ford Motor de Venezuela",),
    ),
    "GM": CuratedExposure(
        ticker="GM",
        exposure_level="historical",
        summary=(
            "General Motors' Venezuelan plant was seized by the Venezuelan government in 2017. "
            "GM took a full writedown and exited operationally."
        ),
        subsidiaries=("General Motors Venezolana",),
    ),
    "GE": CuratedExposure(
        ticker="GE",
        exposure_level="historical",
        summary=(
            "GE's power-systems and oilfield-services divisions historically supplied PdVSA "
            "and the Venezuelan power grid. Current activity is limited and license-dependent."
        ),
    ),
    "HAL": CuratedExposure(
        ticker="HAL",
        exposure_level="direct",
        summary=(
            "Halliburton has historically been one of the largest oilfield-services providers "
            "to PdVSA. Operations are subject to OFAC licensing; the company has reported "
            "material writedowns on the Venezuelan business."
        ),
        ofac_licenses=("GL 8M",),
    ),
    "SLB": CuratedExposure(
        ticker="SLB",
        exposure_level="direct",
        summary=(
            "Schlumberger (now SLB) provides oilfield services to PdVSA-operated fields under "
            "OFAC General License 8 series. Material historical impairments disclosed."
        ),
        ofac_licenses=("GL 8M",),
    ),
    "BKR": CuratedExposure(
        ticker="BKR",
        exposure_level="direct",
        summary=(
            "Baker Hughes provides drilling and pressure-pumping services to PdVSA under OFAC "
            "General License 8, similar in structure to Halliburton's and SLB's authorizations."
        ),
        ofac_licenses=("GL 8M",),
    ),
    "C": CuratedExposure(
        ticker="C",
        exposure_level="historical",
        summary=(
            "Citigroup historically held Venezuelan sovereign and PdVSA bonds in trading "
            "books. Closed PdVSA correspondent banking relationships post-2017 sanctions."
        ),
    ),
    "JPM": CuratedExposure(
        ticker="JPM",
        exposure_level="historical",
        summary=(
            "JPMorgan's emerging-markets bond indices historically included Venezuelan "
            "sovereign and PdVSA paper. The bonds were removed from EMBI Global Diversified "
            "after 2019. JPM also acted as historical custodian on Venezuelan transactions."
        ),
    ),
    "GS": CuratedExposure(
        ticker="GS",
        exposure_level="historical",
        summary=(
            "Goldman Sachs Asset Management drew controversy in 2017 for purchasing PdVSA "
            "2022 bonds at a steep discount. The position was reduced and the precedent is "
            "frequently cited in sanctions-compliance literature."
        ),
    ),
    "BLK": CuratedExposure(
        ticker="BLK",
        exposure_level="historical",
        summary=(
            "BlackRock funds historically held Venezuelan sovereign and PdVSA debt across "
            "passive emerging-markets products. Holdings shrank materially after the 2019 "
            "OFAC executive orders barred secondary-market trading."
        ),
    ),
    "AAPL": CuratedExposure(
        ticker="AAPL",
        exposure_level="none",
        summary=(
            "Apple has no manufacturing or retail presence in Venezuela. Products are "
            "available through grey-market and third-party importers. No SDN exposure."
        ),
    ),
    "MSFT": CuratedExposure(
        ticker="MSFT",
        exposure_level="indirect",
        summary=(
            "Microsoft maintains regional sales coverage for Venezuela via its Latin America "
            "operations but has no direct local subsidiary of consequence. Cloud and "
            "enterprise services are available subject to US export-control compliance."
        ),
    ),
    "GOOGL": CuratedExposure(
        ticker="GOOGL",
        exposure_level="none",
        summary=(
            "Alphabet has no operating subsidiary in Venezuela. Some Google services have "
            "experienced government-imposed throttling or blocks during political events."
        ),
    ),
    "META": CuratedExposure(
        ticker="META",
        exposure_level="none",
        summary=(
            "Meta has no operating presence in Venezuela. Facebook and Instagram have been "
            "subject to intermittent ISP-level blocks during political events."
        ),
    ),
    "AMZN": CuratedExposure(
        ticker="AMZN",
        exposure_level="none",
        summary=(
            "Amazon does not ship to Venezuela directly and has no Venezuelan operations. AWS "
            "services are subject to US export-control review for Venezuelan customers."
        ),
    ),
    "T": CuratedExposure(
        ticker="T",
        exposure_level="historical",
        summary=(
            "AT&T's DirecTV Latin America subsidiary was seized by the Venezuelan government "
            "in 2020 after the company suspended service to comply with US sanctions. AT&T "
            "took a full writedown. Operations were transferred to a Venezuelan operator."
        ),
        subsidiaries=("DirecTV Venezuela",),
    ),
    "VZ": CuratedExposure(
        ticker="VZ",
        exposure_level="none",
        summary="Verizon has no operating presence in Venezuela.",
    ),
    "WMT": CuratedExposure(
        ticker="WMT",
        exposure_level="none",
        summary="Walmart has no operating subsidiary in Venezuela.",
    ),
}


def get_curated(ticker: str) -> CuratedExposure | None:
    if not ticker:
        return None
    return _CURATED.get(ticker.upper())


def all_curated_tickers() -> list[str]:
    return sorted(_CURATED.keys())


def known_subsidiary_terms(ticker: str) -> list[str]:
    """Return the list of company-specific subsidiary / brand strings to
    fuzzy-match against the OFAC SDN list and our text corpora."""
    entry = get_curated(ticker)
    if not entry:
        return []
    return list(entry.subsidiaries)
