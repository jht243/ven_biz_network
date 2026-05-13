"""Semrush SEO API client for keyword research and domain analytics.

Wraps the Semrush REST/CSV API to answer:
  1. What keywords does our domain already rank for? (domain_organic)
  2. What is the domain overview / authority? (domain_rank)
  3. For a given keyword, what are related opportunities? (phrase_related)
  4. Keyword gap: what do competitors rank for that we don't? (domain_organic on competitor)

API docs: https://developer.semrush.com/api/seo/overview/
Pricing: each API call costs 10 API units per line returned.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

API_BASE = "https://api.semrush.com/"
BACKLINK_API_BASE = "https://api.semrush.com/analytics/v1/"


@dataclass
class SemrushClient:
    api_key: str = ""
    database: str = "us"
    timeout: int = 60

    def __post_init__(self) -> None:
        if not self.api_key:
            self.api_key = settings.semrush_api_key
        if not self.database:
            self.database = settings.semrush_database

    def _get(
        self,
        params: dict[str, Any],
        *,
        base_url: str = API_BASE,
    ) -> list[dict[str, str]]:
        params["key"] = self.api_key
        if not self.api_key:
            raise ValueError(
                "SEMRUSH_API_KEY is not set. Get yours at "
                "https://www.semrush.com/accounts/subscription-info/api-units/"
            )
        resp = httpx.get(base_url, params=params, timeout=self.timeout)
        text = resp.text.strip()
        if resp.status_code != 200 or text.startswith("ERROR"):
            raise RuntimeError(f"Semrush API error: {text}")
        reader = csv.DictReader(io.StringIO(text), delimiter=";")
        return list(reader)

    # ── Domain-level reports ────────────────────────────────────────

    def domain_overview(self, domain: str) -> list[dict[str, str]]:
        """High-level organic/paid summary for a domain."""
        return self._get({
            "type": "domain_rank",
            "domain": domain,
            "database": self.database,
            "export_columns": "Db,Dn,Rk,Or,Ot,Oc,Ad,At,Ac",
        })

    def domain_organic_keywords(
        self,
        domain: str,
        *,
        limit: int = 100,
        sort: str = "tr_desc",
        intent_filter: str | None = None,
        position_lte: int | None = None,
    ) -> list[dict[str, str]]:
        """Keywords a domain ranks for in organic search.

        Returns: Ph (keyword), Po (position), Pp (previous position),
        Nq (volume), Cp (CPC), Ur (URL), Tr (traffic %), Tc (traffic cost),
        Co (competition), In (intent).
        """
        params: dict[str, Any] = {
            "type": "domain_organic",
            "domain": domain,
            "database": self.database,
            "display_limit": limit,
            "display_sort": sort,
            "export_columns": "Ph,Po,Pp,Nq,Cp,Ur,Tr,Tc,Co,In",
        }
        filters: list[str] = []
        if intent_filter:
            # 0=informational, 1=navigational, 2=commercial, 3=transactional
            filters.append(f"+|In|Eq|{intent_filter}")
        if position_lte:
            filters.append(f"+|Po|Lt|{position_lte + 1}")
        if filters:
            params["display_filter"] = "|".join(filters)
        return self._get(params)

    def domain_competitors(
        self, domain: str, *, limit: int = 20
    ) -> list[dict[str, str]]:
        """Organic competitors for a domain."""
        return self._get({
            "type": "domain_organic_organic",
            "domain": domain,
            "database": self.database,
            "display_limit": limit,
            "export_columns": "Dn,Cr,Np,Or,Ot,Oc,Ad",
        })

    def get_domain_authority(self, domain: str) -> int | None:
        """Return Semrush Authority Score from the Backlinks overview report."""
        overview = self._get(
            {
                "type": "backlinks_overview",
                "target": domain,
                "target_type": "root_domain",
                "export_columns": "ascore",
            },
            base_url=BACKLINK_API_BASE,
        )
        if not overview:
            return None
        row = overview[0]
        raw = (
            row.get("ascore")
            or row.get("Authority Score")
            or row.get("AuthorityScore")
            or row.get("AS")
        )
        try:
            return int(float(str(raw).replace(",", "")))
        except (TypeError, ValueError):
            return None

    # ── Backlink reports ─────────────────────────────────────────────

    def get_backlinks(
        self,
        target: str,
        *,
        limit: int = 500,
        target_type: str = "root_domain",
    ) -> list[dict[str, str]]:
        """Backlinks pointing to a domain or URL via the Semrush Backlinks API."""
        return self._get(
            {
                "type": "backlinks",
                "target": target,
                "target_type": target_type,
                "display_limit": limit,
                "export_columns": (
                    "page_ascore,source_title,source_url,target_url,anchor,"
                    "external_num,internal_num,first_seen,last_seen,nofollow"
                ),
            },
            base_url=BACKLINK_API_BASE,
        )

    def get_referring_domains(
        self,
        target: str,
        *,
        limit: int = 500,
        target_type: str = "root_domain",
    ) -> list[dict[str, str]]:
        """Referring domains pointing to a domain via the Semrush Backlinks API."""
        return self._get(
            {
                "type": "backlinks_refdomains",
                "target": target,
                "target_type": target_type,
                "display_limit": limit,
                "export_columns": (
                    "domain_ascore,domain,backlinks_num,ip,country,first_seen,last_seen"
                ),
            },
            base_url=BACKLINK_API_BASE,
        )

    # ── Keyword-level reports ───────────────────────────────────────

    def keyword_overview(self, phrase: str) -> list[dict[str, str]]:
        """Volume, CPC, competition, difficulty for a single keyword."""
        return self._get({
            "type": "phrase_this",
            "phrase": phrase,
            "database": self.database,
            "export_columns": "Ph,Nq,Cp,Co,Nr,Td,In,Kd",
        })

    def keyword_overview_batch(self, phrases: list[str]) -> list[dict[str, str]]:
        """Bulk keyword metrics (up to 100 keywords per call)."""
        if len(phrases) > 100:
            raise ValueError("Semrush batch limit is 100 keywords per call")
        return self._get({
            "type": "phrase_these",
            "phrase": ";".join(phrases),
            "database": self.database,
            "export_columns": "Ph,Nq,Cp,Co,Nr,Td,In,Kd",
        })

    def related_keywords(
        self,
        phrase: str,
        *,
        limit: int = 50,
        sort: str = "nq_desc",
    ) -> list[dict[str, str]]:
        """Keywords semantically related to the seed phrase."""
        return self._get({
            "type": "phrase_related",
            "phrase": phrase,
            "database": self.database,
            "display_limit": limit,
            "display_sort": sort,
            "export_columns": "Ph,Nq,Cp,Co,Nr,Td,Rr,In,Kd",
        })

    def phrase_questions(
        self,
        phrase: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, str]]:
        """Question-form keywords containing the seed phrase."""
        return self._get({
            "type": "phrase_questions",
            "phrase": phrase,
            "database": self.database,
            "display_limit": limit,
            "display_sort": "nq_desc",
            "export_columns": "Ph,Nq,Cp,Co,Nr,Td,In,Kd",
        })

    # ── SERP features analysis ───────────────────────────────────────

    def domain_organic_with_serp_features(
        self,
        domain: str,
        *,
        limit: int = 200,
        sort: str = "tr_desc",
        positions_type: str = "all",
    ) -> list[dict[str, str]]:
        """Keywords a domain ranks for, including SERP feature data.

        Returns standard organic columns plus:
        - Fk: all SERP features triggered for this keyword (comma-separated codes)
        - Fp: SERP features where this domain appears (comma-separated codes)

        positions_type: 'organic' | 'all' | 'serp_features'
        """
        return self._get({
            "type": "domain_organic",
            "domain": domain,
            "database": self.database,
            "display_limit": limit,
            "display_sort": sort,
            "export_columns": "Ph,Po,Nq,Cp,Ur,Tr,Tc,Co,Kd,In,Fk,Fp",
            "display_positions_type": positions_type,
        })

    def keyword_serp_features(self, phrase: str) -> list[dict[str, str]]:
        """Get SERP feature data for a single keyword.

        Returns volume, KD, and the SERP features triggered by this keyword.
        The Fk column contains comma-separated feature codes.
        """
        return self._get({
            "type": "phrase_this",
            "phrase": phrase,
            "database": self.database,
            "export_columns": "Ph,Nq,Cp,Co,Kd,In,Fk",
        })

    # ── Gap analysis helpers ────────────────────────────────────────

    def competitor_keywords_we_miss(
        self,
        our_domain: str,
        competitor_domain: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, str]]:
        """Keywords the competitor ranks for that we don't.

        Fetches competitor's top organic keywords and filters out any
        that our domain also ranks for. This is a client-side gap
        since the Semrush API doesn't have a single-call gap endpoint
        in the basic tier.
        """
        comp_kws = self.domain_organic_keywords(
            competitor_domain, limit=limit, sort="tr_desc"
        )
        our_kws = self.domain_organic_keywords(
            our_domain, limit=500, sort="tr_desc"
        )
        our_phrases = {row["Keyword"].lower() for row in our_kws}
        return [
            row for row in comp_kws
            if row["Keyword"].lower() not in our_phrases
        ]


@dataclass
class KeywordOpportunity:
    keyword: str
    volume: int
    cpc: float
    competition: float
    our_position: int | None
    our_url: str | None
    intent: str
    difficulty: int | None = None
    source: str = ""

    @property
    def priority_score(self) -> float:
        """Higher = better opportunity. Favours high volume, low competition,
        and keywords where we're close to page 1."""
        vol_score = min(self.volume / 1000, 10)
        comp_penalty = self.competition * 3
        position_bonus = 0.0
        if self.our_position:
            if 4 <= self.our_position <= 20:
                position_bonus = 5.0
            elif 21 <= self.our_position <= 50:
                position_bonus = 2.0
        return vol_score - comp_penalty + position_bonus


def run_keyword_research(
    domain: str = "caracasresearch.com",
    seed_keywords: list[str] | None = None,
    *,
    competitor_domains: list[str] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Run a full keyword research pass and return structured results.

    Returns a dict with keys:
      - domain_overview: high-level stats
      - current_rankings: keywords we already rank for
      - opportunities: sorted list of KeywordOpportunity
      - related: related keyword ideas from seed phrases
      - questions: question-form keyword ideas
      - competitor_gaps: keywords competitors have that we don't
    """
    if seed_keywords is None:
        seed_keywords = [
            # Core investment / business
            "venezuela investment",
            "invest in venezuela",
            "venezuela business",
            "venezuela foreign investment",
            # Oil & energy (post-reform)
            "venezuela oil",
            "PDVSA",
            "venezuela oil investment 2026",
            "venezuela hydrocarbons law",
            "venezuela oil production",
            "chevron venezuela",
            # Sanctions & compliance
            "venezuela sanctions",
            "OFAC sanctions venezuela",
            "general license 46 venezuela",
            "venezuela sanctions lifted 2026",
            # Debt & financial markets
            "venezuela bonds",
            "venezuela bond restructuring",
            "venezuela etf",
            "PDVSA bonds",
            "venezuela sovereign debt",
            # Real estate
            "caracas real estate",
            "venezuela real estate investment",
            # Visa & travel
            "venezuela visa",
            "is venezuela safe",
            "venezuela travel advisory",
            # Economic reform
            "venezuela economic reform 2026",
            "venezuela gdp growth 2026",
            "venezuela open for business",
        ]

    client = SemrushClient()
    results: dict[str, Any] = {}

    # 1. Domain overview
    logger.info("Fetching domain overview for %s", domain)
    try:
        results["domain_overview"] = client.domain_overview(domain)
    except Exception as exc:
        logger.warning("Domain overview failed: %s", exc)
        results["domain_overview"] = []

    # 2. Current organic rankings
    logger.info("Fetching current organic rankings (top %d)", limit)
    try:
        results["current_rankings"] = client.domain_organic_keywords(
            domain, limit=limit
        )
    except Exception as exc:
        logger.warning("Organic keywords failed: %s", exc)
        results["current_rankings"] = []

    # 3. Related keywords for each seed
    all_related: list[dict[str, str]] = []
    all_questions: list[dict[str, str]] = []
    for seed in seed_keywords:
        logger.info("Fetching related keywords for '%s'", seed)
        try:
            all_related.extend(client.related_keywords(seed, limit=30))
        except Exception as exc:
            logger.warning("Related keywords for '%s' failed: %s", seed, exc)
        try:
            all_questions.extend(client.phrase_questions(seed, limit=20))
        except Exception as exc:
            logger.warning("Questions for '%s' failed: %s", seed, exc)

    seen: set[str] = set()
    results["related"] = []
    for row in all_related:
        kw = row.get("Keyword", "").lower()
        if kw and kw not in seen:
            seen.add(kw)
            results["related"].append(row)

    seen_q: set[str] = set()
    results["questions"] = []
    for row in all_questions:
        kw = row.get("Keyword", "").lower()
        if kw and kw not in seen_q:
            seen_q.add(kw)
            results["questions"].append(row)

    # 4. Competitor gap analysis
    results["competitor_gaps"] = []
    if competitor_domains:
        for comp in competitor_domains:
            logger.info("Running gap analysis vs %s", comp)
            try:
                gaps = client.competitor_keywords_we_miss(domain, comp, limit=50)
                for row in gaps:
                    row["_competitor"] = comp
                results["competitor_gaps"].extend(gaps)
            except Exception as exc:
                logger.warning("Gap analysis vs %s failed: %s", comp, exc)

    # 5. Build opportunity list
    our_kw_map: dict[str, dict[str, str]] = {}
    for row in results["current_rankings"]:
        our_kw_map[row.get("Keyword", "").lower()] = row

    opportunities: list[KeywordOpportunity] = []

    def _add_opportunity(row: dict[str, str], source: str) -> None:
        kw = row.get("Keyword", "")
        if not kw:
            return
        ours = our_kw_map.get(kw.lower())
        try:
            vol = int(row.get("Search Volume", 0))
        except (ValueError, TypeError):
            vol = 0
        try:
            cpc = float(row.get("CPC", 0))
        except (ValueError, TypeError):
            cpc = 0.0
        try:
            comp = float(row.get("Competition", 0))
        except (ValueError, TypeError):
            comp = 0.0
        try:
            diff = int(row.get("Keyword Difficulty", 0))
        except (ValueError, TypeError):
            diff = None

        intent_raw = row.get("Intent", "")
        intent_map = {"0": "informational", "1": "navigational",
                      "2": "commercial", "3": "transactional"}
        intent = intent_map.get(str(intent_raw), str(intent_raw))

        opp = KeywordOpportunity(
            keyword=kw,
            volume=vol,
            cpc=cpc,
            competition=comp,
            our_position=int(ours["Position"]) if ours and ours.get("Position") else None,
            our_url=ours.get("Url") if ours else None,
            intent=intent,
            difficulty=diff,
            source=source,
        )
        opportunities.append(opp)

    for row in results["related"]:
        _add_opportunity(row, "related")
    for row in results["questions"]:
        _add_opportunity(row, "question")
    for row in results["competitor_gaps"]:
        _add_opportunity(row, f"gap:{row.get('_competitor', '?')}")

    opportunities.sort(key=lambda o: o.priority_score, reverse=True)
    results["opportunities"] = opportunities

    return results


def format_report(results: dict[str, Any]) -> str:
    """Format keyword research results as a readable text report."""
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("SEMRUSH KEYWORD RESEARCH REPORT")
    lines.append("=" * 72)

    # Domain overview
    if results.get("domain_overview"):
        ov = results["domain_overview"][0]
        lines.append("\n── Domain Overview ──")
        lines.append(f"  Domain:           {ov.get('Domain', 'N/A')}")
        lines.append(f"  Semrush Rank:     {ov.get('Rank', 'N/A')}")
        lines.append(f"  Organic Keywords: {ov.get('Organic Keywords', 'N/A')}")
        lines.append(f"  Organic Traffic:  {ov.get('Organic Traffic', 'N/A')}")
        lines.append(f"  Organic Cost:     ${ov.get('Organic Cost', 'N/A')}")

    # Current top rankings
    rankings = results.get("current_rankings", [])
    if rankings:
        lines.append(f"\n── Current Rankings (top {len(rankings)}) ──")
        lines.append(f"  {'Keyword':<45} {'Pos':>4} {'Vol':>8} {'Traffic%':>9} {'URL'}")
        lines.append("  " + "-" * 100)
        for row in rankings[:30]:
            lines.append(
                f"  {row.get('Keyword', ''):<45} "
                f"{row.get('Position', ''):>4} "
                f"{row.get('Search Volume', ''):>8} "
                f"{row.get('Traffic (%)', ''):>9} "
                f"{row.get('Url', '')}"
            )
        if len(rankings) > 30:
            lines.append(f"  ... and {len(rankings) - 30} more")

    # Top opportunities
    opps: list[KeywordOpportunity] = results.get("opportunities", [])
    if opps:
        lines.append(f"\n── Top Keyword Opportunities ({len(opps)} total) ──")
        lines.append(
            f"  {'Keyword':<45} {'Vol':>8} {'CPC':>6} {'Comp':>5} "
            f"{'Our Pos':>7} {'Intent':<14} {'Score':>6} {'Source'}"
        )
        lines.append("  " + "-" * 115)
        for opp in opps[:50]:
            pos_str = str(opp.our_position) if opp.our_position else "—"
            lines.append(
                f"  {opp.keyword:<45} "
                f"{opp.volume:>8} "
                f"{opp.cpc:>6.2f} "
                f"{opp.competition:>5.2f} "
                f"{pos_str:>7} "
                f"{opp.intent:<14} "
                f"{opp.priority_score:>6.1f} "
                f"{opp.source}"
            )

    # Questions
    questions = results.get("questions", [])
    if questions:
        lines.append(f"\n── Question Keywords ({len(questions)}) ──")
        for row in questions[:25]:
            vol = row.get("Search Volume", "?")
            lines.append(f"  [{vol:>6}] {row.get('Keyword', '')}")

    # Competitor gaps
    gaps = results.get("competitor_gaps", [])
    if gaps:
        lines.append(f"\n── Competitor Gap Keywords ({len(gaps)}) ──")
        lines.append(f"  {'Keyword':<45} {'Vol':>8} {'Competitor Pos':>14} {'Competitor'}")
        lines.append("  " + "-" * 90)
        for row in gaps[:30]:
            lines.append(
                f"  {row.get('Keyword', ''):<45} "
                f"{row.get('Search Volume', ''):>8} "
                f"{row.get('Position', ''):>14} "
                f"{row.get('_competitor', '')}"
            )

    lines.append("\n" + "=" * 72)
    return "\n".join(lines)
