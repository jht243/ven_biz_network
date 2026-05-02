#!/usr/bin/env python3
"""Analyze SERP features for target keywords via Semrush API.

For each keyword we're targeting, identifies which SERP features Google
shows (Featured Snippets, People Also Ask, AI Overviews, etc.) and
recommends content format optimizations to win those features.

Usage:
    python3 scripts/semrush_serp_features.py [--json]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("SITE_URL", "https://caracasresearch.com")

from src.seo.semrush import SemrushClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SERP_FEATURE_NAMES: dict[str, str] = {
    "0": "Featured snippet",
    "1": "Knowledge panel",
    "2": "Sitelinks",
    "3": "Reviews",
    "4": "Instant answer",
    "5": "Ads top",
    "6": "Ads bottom",
    "7": "Product listing ads",
    "8": "Local pack",
    "9": "Image pack",
    "10": "Video carousel",
    "11": "Video",
    "12": "Twitter/X",
    "13": "People also ask",
    "14": "Top stories",
    "15": "Carousel",
    "16": "Recipes",
    "17": "Flights",
    "18": "Hotels",
    "19": "Find results on",
    "20": "Calculator",
    "21": "Featured video",
    "22": "Scholarly articles",
    "23": "Podcasts",
    "24": "AI overview",
    "25": "Discussions and forums",
    "27": "Knowledge card",
    "29": "Related searches",
    "31": "Things to know",
    "35": "Short videos",
    "36": "Refine by",
    "39": "Perspectives",
    "44": "Popular products",
    "45": "AI overview (expandable)",
    "46": "Places",
    "47": "Buy results",
    "49": "Visual stories",
    "50": "Compare & buy",
    "51": "Product viewer",
    "52": "Mentioned in",
}

TARGET_KEYWORDS = [
    ("citgo", "/citgo", 40500),
    ("venezuela news", "/briefing", 135000),
    ("maduro", "/people/nicolas-maduro", 74000),
    ("venezuela currency", "/tools/bolivar-usd-exchange-rate", 14800),
    ("venezuela oil", "/venezuela-oil", 9900),
    ("tps venezuela", "/tps-venezuela", 6600),
    ("ofac sanctions list", "/ofac-sanctions-list", 5400),
    ("sdn list", "/ofac-sanctions-list", 3600),
    ("is venezuela safe", "/is-venezuela-safe", 2900),
    ("bolivar to usd", "/tools/bolivar-usd-exchange-rate", 2900),
    ("venezuela gdp", "/venezuela-economy", 2400),
    ("venezuela economy", "/venezuela-economy", 1900),
    ("chevron venezuela", "/venezuela-oil", 1900),
    ("venezuelan bolivar", "/tools/bolivar-usd-exchange-rate", 1000),
    ("citgo venezuela", "/citgo", 590),
    ("american airlines venezuela", "/travel", 590),
    ("caracas hotels", "/travel", 390),
    ("venezuela real estate", "/real-estate", 260),
    ("venezuela stock market", "/invest-in-venezuela", 140),
    ("pdvsa bonds", "/venezuela-oil", 140),
    ("why is venezuela sanctioned", "/why-is-venezuela-sanctioned", 90),
    ("invest in venezuela", "/invest-in-venezuela", 90),
    ("venezuela visa for us citizens", "/apply-for-venezuelan-visa/us-citizens", 90),
    ("pdvsa stock", "/venezuela-oil", 70),
    ("venezuela bonds", "/venezuela-oil", 70),
    ("venezuela default", "/venezuela-oil", 50),
]


@dataclass
class KeywordSerpData:
    keyword: str
    target_page: str
    volume: int
    kd: int | None
    cpc: float
    intent: str
    features: list[str]

    @property
    def feature_names(self) -> list[str]:
        return [SERP_FEATURE_NAMES.get(f, f"code:{f}") for f in self.features]

    @property
    def has_featured_snippet(self) -> bool:
        return "0" in self.features

    @property
    def has_paa(self) -> bool:
        return "13" in self.features

    @property
    def has_ai_overview(self) -> bool:
        return "24" in self.features or "45" in self.features

    @property
    def has_video(self) -> bool:
        return "10" in self.features or "11" in self.features or "35" in self.features

    @property
    def has_image_pack(self) -> bool:
        return "9" in self.features

    @property
    def has_knowledge_panel(self) -> bool:
        return "1" in self.features or "27" in self.features


def parse_feature_codes(raw: str) -> list[str]:
    if not raw or raw in ("—", "-", "0"):
        return []
    return [c.strip() for c in raw.split(",") if c.strip()]


def run_analysis() -> list[KeywordSerpData]:
    client = SemrushClient()
    results: list[KeywordSerpData] = []

    intent_map = {"0": "informational", "1": "navigational",
                  "2": "commercial", "3": "transactional"}

    # Batch in groups of 100 using phrase_these
    batch_size = 100
    all_keywords = [kw for kw, _, _ in TARGET_KEYWORDS]
    kw_map = {kw.lower(): (page, vol) for kw, page, vol in TARGET_KEYWORDS}

    for i in range(0, len(all_keywords), batch_size):
        batch = all_keywords[i:i + batch_size]
        logger.info("Fetching SERP features for batch %d-%d (%d keywords)...",
                     i + 1, i + len(batch), len(batch))
        try:
            rows = client._get({
                "type": "phrase_these",
                "phrase": ";".join(batch),
                "database": client.database,
                "export_columns": "Ph,Nq,Cp,Co,Kd,In,Fk",
            })

            for row in rows:
                kw = row.get("Keyword", "")
                page, vol = kw_map.get(kw.lower(), ("", 0))

                try:
                    kd = int(float(row.get("Keyword Difficulty Index", 0))) or None
                except (ValueError, TypeError):
                    kd = None
                try:
                    cpc = float(row.get("CPC", 0))
                except (ValueError, TypeError):
                    cpc = 0.0

                intent_raw = row.get("Intent", "")
                intent = intent_map.get(str(intent_raw), str(intent_raw))

                features = parse_feature_codes(
                    row.get("SERP Features by Keyword", "") or row.get("Keywords SERP Features", "")
                )

                results.append(KeywordSerpData(
                    keyword=kw,
                    target_page=page,
                    volume=vol or int(row.get("Search Volume", 0) or 0),
                    kd=kd,
                    cpc=cpc,
                    intent=intent,
                    features=features,
                ))
        except Exception as exc:
            logger.error("Batch fetch failed: %s", exc)

        if i + batch_size < len(all_keywords):
            time.sleep(0.5)

    results.sort(key=lambda r: r.volume, reverse=True)
    return results


def format_report(results: list[KeywordSerpData]) -> str:
    lines: list[str] = []
    lines.append("=" * 90)
    lines.append("SERP FEATURES ANALYSIS — Target Keywords for caracasresearch.com")
    lines.append("=" * 90)
    lines.append(f"\nAnalyzed {len(results)} keywords\n")

    # Summary table
    lines.append("── Full SERP Feature Map ──")
    lines.append(f"  {'Keyword':<40} {'Vol':>7} {'KD':>4} {'Features'}")
    lines.append("  " + "-" * 85)
    for kw in results:
        kd_str = str(kw.kd) if kw.kd else "—"
        feat_str = ", ".join(kw.feature_names) if kw.features else "(none detected)"
        lines.append(f"  {kw.keyword:<40} {kw.volume:>7} {kd_str:>4} {feat_str}")

    # Featured Snippet analysis
    fs = [kw for kw in results if kw.has_featured_snippet]
    if fs:
        lines.append(f"\n── Featured Snippet Opportunities ({len(fs)}) ──")
        lines.append("  Google shows a Featured Snippet for these queries.")
        lines.append("  To win it: provide a concise 40-60 word answer directly after the H2,")
        lines.append("  use definition paragraphs, numbered lists, or comparison tables.\n")
        for kw in fs:
            lines.append(f"  • {kw.keyword} ({kw.volume:,}/mo) → {kw.target_page}")

    # People Also Ask
    paa = [kw for kw in results if kw.has_paa]
    if paa:
        lines.append(f"\n── People Also Ask Opportunities ({len(paa)}) ──")
        lines.append("  Google shows PAA boxes for these queries.")
        lines.append("  To appear: add FAQ sections with question-form H3s + concise answers.")
        lines.append("  FAQPage schema markup helps (already implemented on most pages).\n")
        for kw in paa:
            lines.append(f"  • {kw.keyword} ({kw.volume:,}/mo) → {kw.target_page}")

    # AI Overview
    ai = [kw for kw in results if kw.has_ai_overview]
    if ai:
        lines.append(f"\n── AI Overview Keywords ({len(ai)}) ──")
        lines.append("  Google shows an AI-generated overview for these queries,")
        lines.append("  which pushes organic results below the fold and reduces CTR.")
        lines.append("  Strategy: provide authoritative, structured content to be cited")
        lines.append("  as a source. Use clear data points, tables, and expert framing.\n")
        for kw in ai:
            lines.append(f"  • {kw.keyword} ({kw.volume:,}/mo) → {kw.target_page}")

    # Video opportunities
    vid = [kw for kw in results if kw.has_video]
    if vid:
        lines.append(f"\n── Video SERP Keywords ({len(vid)}) ──")
        lines.append("  Google shows video results for these queries.")
        lines.append("  Opportunity: create short explainer videos and embed on these pages")
        lines.append("  with VideoObject schema markup.\n")
        for kw in vid:
            lines.append(f"  • {kw.keyword} ({kw.volume:,}/mo) → {kw.target_page}")

    # Image pack
    img = [kw for kw in results if kw.has_image_pack]
    if img:
        lines.append(f"\n── Image Pack Keywords ({len(img)}) ──")
        lines.append("  Google shows an image carousel for these queries.")
        lines.append("  Optimize: add infographics, charts, and maps with descriptive")
        lines.append("  alt text and filenames matching the keyword.\n")
        for kw in img:
            lines.append(f"  • {kw.keyword} ({kw.volume:,}/mo) → {kw.target_page}")

    # Aggregate stats
    lines.append(f"\n── Feature Frequency Across All Target Keywords ──")
    feature_freq: dict[str, int] = {}
    for kw in results:
        for f in kw.features:
            feature_freq[f] = feature_freq.get(f, 0) + 1
    for code, count in sorted(feature_freq.items(), key=lambda x: x[1], reverse=True):
        name = SERP_FEATURE_NAMES.get(code, f"code:{code}")
        pct = count / len(results) * 100 if results else 0
        lines.append(f"  {name:<35} {count:>3} / {len(results)} ({pct:.0f}%)")

    # Actionable recommendations
    lines.append(f"\n{'=' * 90}")
    lines.append("ACTIONABLE RECOMMENDATIONS")
    lines.append("=" * 90)

    if fs:
        lines.append("\n1. FEATURED SNIPPETS — Highest ROI optimization")
        lines.append("   Pages to prioritize:")
        for kw in sorted(fs, key=lambda k: k.volume, reverse=True)[:5]:
            lines.append(f"   → {kw.target_page} for \"{kw.keyword}\" ({kw.volume:,}/mo)")
        lines.append("   How: Add a concise paragraph answer (40-60 words) immediately")
        lines.append("   after the relevant H2. For list queries, use <ol>/<ul>. For")
        lines.append("   comparison queries, use <table>.")

    if paa:
        lines.append("\n2. PEOPLE ALSO ASK — Already well-positioned")
        lines.append("   All pages have FAQ sections with FAQPage schema.")
        lines.append("   Verify each FAQ answer is concise (2-3 sentences) and directly")
        lines.append("   answers the question in the first sentence.")

    if ai:
        lines.append("\n3. AI OVERVIEWS — Defensive strategy")
        lines.append("   These keywords have AI answers that reduce organic CTR.")
        lines.append("   Focus on being the cited source: provide unique data points,")
        lines.append("   original analysis, and structured content that AI systems reference.")

    lines.append("\n" + "=" * 90)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Semrush SERP Features Analyzer")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", default="output/semrush/serp-features.txt")
    args = parser.parse_args()

    results = run_analysis()
    report = format_report(results)
    print(report)

    outpath = Path(args.output)
    outpath.parent.mkdir(parents=True, exist_ok=True)
    outpath.write_text(report, encoding="utf-8")
    logger.info("Report saved to %s", outpath)

    if args.json:
        json_path = outpath.with_suffix(".json")
        json_data = {
            "keywords": [
                {
                    "keyword": kw.keyword,
                    "target_page": kw.target_page,
                    "volume": kw.volume,
                    "kd": kw.kd,
                    "cpc": kw.cpc,
                    "intent": kw.intent,
                    "features": kw.feature_names,
                    "feature_codes": kw.features,
                    "has_featured_snippet": kw.has_featured_snippet,
                    "has_paa": kw.has_paa,
                    "has_ai_overview": kw.has_ai_overview,
                    "has_video": kw.has_video,
                    "has_image_pack": kw.has_image_pack,
                }
                for kw in results
            ],
        }
        json_path.write_text(json.dumps(json_data, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("JSON saved to %s", json_path)


if __name__ == "__main__":
    main()
