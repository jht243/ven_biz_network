"""
Curated public reference data for the Venezuela bonds tracker.

Executable prices for defaulted Venezuela and PDVSA bonds mostly live behind
professional terminals (LSEG, Bloomberg, Tradeweb). This module keeps the
public-facing tracker honest by separating durable instrument metadata and
publicly reported price references from the live news/milestone feed.
"""

from __future__ import annotations


INSTRUMENTS: list[dict] = [
    {
        "issuer": "Republic of Venezuela",
        "name": "Global 2031",
        "short_name": "VENZ 2031",
        "instrument_type": "Sovereign",
        "status": "Defaulted",
        "sanctions_note": "Secondary-market trading generally requires sanctions review; terms and counterparties matter.",
        "price_reference_cents": 50.25,
        "price_reference_date": "2026-03-18",
        "price_reference_label": "Reuters/LSEG public report",
        "price_reference_url": "https://www.investing.com/news/stock-market-news/venezuela-pdvsa-bonds-jump-after-us-waives-certain-sanctions-4569442",
        "trend_note": "Rose after U.S. sanctions waivers for PDVSA-related business were announced.",
    },
    {
        "issuer": "Petroleos de Venezuela SA",
        "name": "PDVSA 2027",
        "short_name": "PDVSA 2027",
        "instrument_type": "PDVSA",
        "status": "Defaulted",
        "sanctions_note": "PDVSA securities remain a higher-risk sanctions category; U.S.-person activity needs counsel review.",
        "price_reference_cents": 35.35,
        "price_reference_date": "2026-03-18",
        "price_reference_label": "Reuters/LSEG public report",
        "price_reference_url": "https://www.investing.com/news/stock-market-news/venezuela-pdvsa-bonds-jump-after-us-waives-certain-sanctions-4569442",
        "trend_note": "Gained alongside sovereign issues as restructuring optionality improved.",
    },
    {
        "issuer": "Republic of Venezuela",
        "name": "Global 2034",
        "short_name": "VENZ 2034",
        "instrument_type": "Sovereign",
        "status": "Defaulted",
        "sanctions_note": "Defaulted sovereign credit; liquidity and settlement access can be uneven.",
        "price_reference_cents": None,
        "price_reference_date": "2026-05-13",
        "price_reference_label": "Bloomberg Law public report",
        "price_reference_url": "https://news.bloomberglaw.com/bankruptcy-law/venezuela-government-announces-debt-restructuring-process",
        "trend_note": "Public reporting said the issue reached its highest level since 2014 after the restructuring announcement.",
    },
    {
        "issuer": "Petroleos de Venezuela SA",
        "name": "PDVSA 2020 / CITGO-linked claim",
        "short_name": "PDVSA 2020",
        "instrument_type": "PDVSA/CITGO",
        "status": "Litigation-sensitive",
        "sanctions_note": "The CITGO share pledge and U.S. court process make this a special-case diligence item.",
        "price_reference_cents": None,
        "price_reference_date": "2026-01-04",
        "price_reference_label": "Reuters public explainer",
        "price_reference_url": "https://www.investing.com/news/economy-news/explainervenezuelas-billions-in-distressed-debt-who-is-in-line-to-collect-4428572",
        "trend_note": "Recovery value depends heavily on CITGO litigation, OFAC licensing, and restructuring priority treatment.",
    },
]


MILESTONES: list[dict] = [
    {
        "date": "2017-11-13",
        "title": "Venezuela enters default period",
        "category": "Default",
        "summary": "Sovereign and PDVSA debt falls into default, creating the distressed-debt overhang tracked here.",
    },
    {
        "date": "2026-01-05",
        "title": "Sovereign and PDVSA bonds rally sharply",
        "category": "Market",
        "summary": "Public Reuters reporting said defaulted government bonds moved toward 40 cents and PDVSA debt toward roughly 30 cents after political change revived restructuring expectations.",
        "source_url": "https://www.investing.com/news/stock-market-news/us-capture-of-maduro-could-lift-venezuela-pdvsa-bonds-by-up-to-10-point-jpmorgan-says-4428868",
    },
    {
        "date": "2026-01-09",
        "title": "Bondholder group seeks authorization for talks",
        "category": "Creditors",
        "summary": "A global investor group said it was ready to begin talks over roughly $60 billion of defaulted bonds.",
        "source_url": "https://www.investing.com/news/economy-news/venezuela-bondholder-group-eyes-authorisation-to-start-debt-restructuring-talks-4440020",
    },
    {
        "date": "2026-03-18",
        "title": "Bonds jump after U.S. sanctions waiver",
        "category": "Sanctions",
        "summary": "Reuters/LSEG data cited VENZ 2031 at 50.25 cents and PDVSA 2027 at 35.35 cents after a U.S. general license expanded permissible PDVSA-related business.",
        "source_url": "https://www.investing.com/news/stock-market-news/venezuela-pdvsa-bonds-jump-after-us-waives-certain-sanctions-4569442",
    },
    {
        "date": "2026-05-13",
        "title": "Government announces formal restructuring process",
        "category": "Restructuring",
        "summary": "Public reporting said Venezuela launched an integrated external-debt restructuring process covering sovereign and PDVSA obligations.",
        "source_url": "https://news.bloomberglaw.com/bankruptcy-law/venezuela-government-announces-debt-restructuring-process",
    },
]


KEYWORDS: tuple[str, ...] = (
    "venezuela bonds",
    "pdvsa bonds",
    "venezuela bond restructuring",
    "venezuela sovereign debt",
    "venezuela debt restructuring",
    "citgo pdvsa bonds",
)
