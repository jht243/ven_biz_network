"""Deterministic scoring for backlink outreach prospects."""

from __future__ import annotations

import re
from urllib.parse import urlparse

TOPIC_KEYWORDS = (
    "venezuela",
    "caracas",
    "latam",
    "latin america",
    "travel",
    "tourism",
    "sanction",
    "ofac",
    "compliance",
    "investment",
    "business",
    "geopolitics",
    "policy",
    "risk",
    "emerging market",
)

SPAM_TERMS = (
    "casino",
    "adult",
    "porn",
    "viagra",
    "cialis",
    "pharma",
    "malware",
    "pbn",
    "guest post marketplace",
    "write for us casino",
)

HIGH_RELEVANCE_CATEGORIES = {
    "travel",
    "sanctions_compliance",
    "investment_business",
    "corporate_exposure",
}


def _authority_score(raw: object) -> int:
    try:
        value = int(float(str(raw or "0").replace(",", "")))
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, value))


def _spam_penalty(text: str, links: list[dict] | None = None) -> int:
    lowered = text.lower()
    penalty = 0
    penalty += min(24, sum(8 for term in SPAM_TERMS if term in lowered))
    words = len(text.split()) or 1
    link_count = len(links or [])
    if link_count > 150 and link_count / words > 0.15:
        penalty += 10
    if re.search(r"\b(buy|cheap|discount|coupon)\b", lowered) and link_count > 80:
        penalty += 8
    return min(30, penalty)


def _same_domain(url: str, domain: str) -> bool:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return host == domain.lower().removeprefix("www.")


def score_prospect(prospect: dict) -> int:
    """Score a prospect from 0 to 100 using the MVP scoring rubric."""
    page_text = prospect.get("page_text") or prospect.get("text") or ""
    category = str(prospect.get("link_opportunity") or prospect.get("category") or "").lower()
    links = prospect.get("links") or []

    if category == "reject" and not prospect.get("contact_email"):
        return 0

    relevance = 0
    if category in HIGH_RELEVANCE_CATEGORIES:
        relevance += 28
    elif category == "general_venezuela":
        relevance += 20
    lowered = page_text.lower()
    keyword_hits = sum(1 for term in TOPIC_KEYWORDS if term in lowered)
    relevance += min(12, keyword_hits * 2)
    relevance = min(40, relevance)

    competitor_count = int(prospect.get("competitor_count") or 1)
    competitor_score = min(25, 10 + max(0, competitor_count - 1) * 5)
    if prospect.get("anchor_text"):
        competitor_score = min(25, competitor_score + 5)

    authority = _authority_score(prospect.get("authority_score"))
    authority_score = round(authority / 100 * 15)

    contact_score = 10 if prospect.get("contact_email") else 0
    resource_score = 10 if prospect.get("is_resource_page") else 0
    if not prospect.get("is_resource_page"):
        url = prospect.get("source_url", "")
        if re.search(r"resources?|links?|guide|directory|references?", url.lower()):
            resource_score = 7

    penalty = _spam_penalty(page_text, links)
    if prospect.get("source_url") and prospect.get("domain"):
        if not _same_domain(prospect["source_url"], prospect["domain"]):
            penalty += 3

    return max(0, min(100, relevance + competitor_score + authority_score + contact_score + resource_score - penalty))

