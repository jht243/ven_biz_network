"""Classify backlink prospects and map them to Caracas Research URLs."""

from __future__ import annotations

import json
import logging
import re

from openai import OpenAI

from src.config import settings

logger = logging.getLogger(__name__)

LINK_OPPORTUNITIES = {
    "travel",
    "sanctions_compliance",
    "investment_business",
    "general_venezuela",
    "corporate_exposure",
    "reject",
}

SITE_TYPES = {
    "news_media",
    "blog",
    "university_academic",
    "government",
    "ngo_think_tank",
    "law_firm",
    "compliance_vendor",
    "travel_site",
    "business_directory",
    "company_website",
    "reference_site",
    "unreachable",
    "spam_reject",
    "other",
}

EMAIL_TEMPLATE_BY_OPPORTUNITY = {
    "travel": ("travel_resource", "additional Venezuela travel resource"),
    "sanctions_compliance": ("sanctions_reference", "OFAC/sanctions reference"),
    "investment_business": ("investment_resource", "investment and business risk resource"),
    "corporate_exposure": ("company_data_resource", "company exposure/data resource"),
    "general_venezuela": ("general_research_reference", "updated Venezuela research reference"),
    "reject": ("none", "no outreach"),
}

TARGET_URLS = {
    "travel": "https://www.caracasresearch.com/travel",
    "sanctions_compliance": "https://www.caracasresearch.com/sanctions-tracker",
    "investment_business": "https://www.caracasresearch.com/invest-in-venezuela",
    "corporate_exposure": "https://www.caracasresearch.com/companies",
    "general_venezuela": None,
    "reject": None,
}

GENERAL_TARGET_HINTS = (
    ("sanction", "https://www.caracasresearch.com/sanctions-tracker"),
    ("ofac", "https://www.caracasresearch.com/tools/ofac-venezuela-sanctions-checker"),
    ("travel", "https://www.caracasresearch.com/travel"),
    ("visa", "https://www.caracasresearch.com/travel"),
    ("investment", "https://www.caracasresearch.com/invest-in-venezuela"),
    ("business", "https://www.caracasresearch.com/invest-in-venezuela"),
    ("company", "https://www.caracasresearch.com/companies"),
    ("corporate", "https://www.caracasresearch.com/companies"),
)

SYSTEM_PROMPT = """You classify backlink outreach prospects for Caracas Research.
Return only JSON with these keys:
{
  "site_type": "news_media | blog | university_academic | government | ngo_think_tank | law_firm | compliance_vendor | travel_site | business_directory | company_website | reference_site | unreachable | spam_reject | other",
  "link_opportunity": "travel | sanctions_compliance | investment_business | general_venezuela | corporate_exposure | reject",
  "email_template_key": "travel_resource | sanctions_reference | investment_resource | company_data_resource | general_research_reference | none",
  "email_angle": "short label for the outreach angle",
  "reason_to_link": "one concise sentence explaining why Caracas Research is relevant",
  "source_page_topic": "short phrase describing the source page topic",
  "is_resource_page": true or false,
  "reject_reason": null or "short reason"
}

Only use link_opportunity "reject" for hard spam: casino, adult, pharma, malware,
scraped content farms, or obvious PBNs. When in doubt, classify as general_venezuela
rather than reject. Any site with a plausible Venezuela/LatAm connection should
NOT be rejected.
Classify site_type separately from link_opportunity. For example, a think-tank
site can still have a sanctions_compliance link opportunity if its page is about OFAC.
Use sanctions_compliance for OFAC/legal/compliance pages.
Use investment_business for emerging markets, business, investing, ROI, FDI,
policy, geopolitics, and think-tank pages about Venezuela.
Use corporate_exposure for company/entity/database exposure pages.
"""


def _fallback_classify(text: str) -> dict:
    lowered = text.lower()
    if any(term in lowered for term in ("university", ".edu", "journal", "working paper")):
        site_type = "university_academic"
    elif any(term in lowered for term in ("government", "embassy", "department of state", ".gov")):
        site_type = "government"
    elif any(term in lowered for term in ("think tank", "policy institute", "dialogue", "council")):
        site_type = "ngo_think_tank"
    elif any(term in lowered for term in ("law firm", "attorney", "legal update")):
        site_type = "law_firm"
    elif any(term in lowered for term in ("compliance", "screening", "sanctions list")):
        site_type = "compliance_vendor"
    elif any(term in lowered for term in ("hotel", "tour", "travel", "trip", "visa")):
        site_type = "travel_site"
    elif any(term in lowered for term in ("blogspot", "substack", "wordpress")):
        site_type = "blog"
    elif any(term in lowered for term in ("news", "media", "press")):
        site_type = "news_media"
    else:
        site_type = "other"

    if any(term in lowered for term in ("casino", "viagra", "porn", "adult", "pharma")):
        link_opportunity = "reject"
        site_type = "spam_reject"
    elif any(term in lowered for term in ("ofac", "sanction", "compliance", "legal")):
        link_opportunity = "sanctions_compliance"
    elif any(term in lowered for term in ("travel", "tourism", "visa", "safety")):
        link_opportunity = "travel"
    elif any(term in lowered for term in ("investment", "business", "market", "roi", "fdi", "policy", "geopolitics", "diplomacy")):
        link_opportunity = "investment_business"
    elif any(term in lowered for term in ("company", "corporate", "database", "exposure")):
        link_opportunity = "corporate_exposure"
    elif "venezuela" in lowered or "latam" in lowered or "latin america" in lowered:
        link_opportunity = "general_venezuela"
    else:
        link_opportunity = "general_venezuela"
    template_key, email_angle = EMAIL_TEMPLATE_BY_OPPORTUNITY[link_opportunity]
    return {
        "category": link_opportunity,
        "site_type": site_type,
        "link_opportunity": link_opportunity,
        "email_template_key": template_key,
        "email_angle": email_angle,
        "reason_to_link": "Caracas Research is a relevant Venezuela-focused reference for this page.",
        "source_page_topic": "Venezuela resources",
        "is_resource_page": bool(re.search(r"resource|links|guide|references|directory", lowered)),
        "reject_reason": "No clear topical connection" if link_opportunity == "reject" else None,
    }


def classify_prospect(
    page_text: str,
    source_url: str,
    competitor_name: str = "",
) -> dict:
    """Classify a prospect page using OpenAI, with a deterministic fallback."""
    if not page_text:
        return _fallback_classify("")
    if not settings.openai_api_key:
        return _fallback_classify(page_text)

    client = OpenAI(api_key=settings.openai_api_key)
    user_prompt = f"""SOURCE URL: {source_url}
COMPETITOR REFERENCED: {competitor_name or "unknown"}

PAGE TEXT:
{page_text[:6000]}"""

    try:
        response = client.chat.completions.create(
            model=settings.openai_narrative_model or settings.openai_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=350,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "{}"
        data = json.loads(raw)
    except Exception as exc:
        logger.warning("OpenAI classification failed for %s: %s", source_url, exc)
        data = _fallback_classify(page_text)

    link_opportunity = str(
        data.get("link_opportunity") or data.get("category") or "reject"
    ).strip().lower()
    if link_opportunity not in LINK_OPPORTUNITIES:
        link_opportunity = "reject"
    site_type = str(data.get("site_type") or "other").strip().lower()
    if site_type not in SITE_TYPES:
        site_type = "other"
    template_key, email_angle = EMAIL_TEMPLATE_BY_OPPORTUNITY[link_opportunity]
    data["category"] = link_opportunity
    data["site_type"] = site_type
    data["link_opportunity"] = link_opportunity
    data["email_template_key"] = template_key
    data["email_angle"] = data.get("email_angle") or email_angle
    data.setdefault("reason_to_link", "")
    data.setdefault("source_page_topic", "")
    data.setdefault("is_resource_page", False)
    data.setdefault("reject_reason", None)
    return data


def choose_target_url(link_opportunity: str, page_text: str = "") -> str | None:
    """Select the best Caracas Research target URL for a link opportunity."""
    link_opportunity = (link_opportunity or "").strip().lower()
    if link_opportunity == "reject":
        return None
    if link_opportunity == "sanctions_compliance":
        lowered = page_text.lower()
        if "ofac" in lowered or "screen" in lowered or "checker" in lowered:
            return "https://www.caracasresearch.com/tools/ofac-venezuela-sanctions-checker"
    if link_opportunity == "investment_business":
        lowered = page_text.lower()
        if "roi" in lowered or "calculator" in lowered or "return" in lowered:
            return "https://www.caracasresearch.com/tools/venezuela-investment-roi-calculator"
    if link_opportunity in TARGET_URLS and TARGET_URLS[link_opportunity]:
        return TARGET_URLS[link_opportunity]

    lowered = page_text.lower()
    for needle, url in GENERAL_TARGET_HINTS:
        if needle in lowered:
            return url
    return "https://www.caracasresearch.com/invest-in-venezuela"

