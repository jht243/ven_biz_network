#!/usr/bin/env python3
"""One-time audit of stale-prone claims in investment pages.

This script is intentionally not wired into the daily cron. It helps migrate
hardcoded volatile facts into the structured investment_facts registry.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PAGES = (
    "templates/general_license_46.html.j2",
    "templates/venezuela_hydrocarbons_law.html.j2",
    "templates/investing_in_venezuelan_oil.html.j2",
    "templates/venezuela_bonds_restructuring.html.j2",
    "templates/venezuela_bonds_tracker.html.j2",
    "templates/venezuela_etf.html.j2",
    "templates/venezuela_vs_colombia.html.j2",
)

PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("money_amount", re.compile(r"(?:~?\$|USD\s*)\d+(?:\.\d+)?\s?(?:B|M|billion|million|trillion)?", re.I)),
    ("bond_price", re.compile(r"\b\d+(?:\.\d+)?\s?(?:cents|¢)\b", re.I)),
    ("production_volume", re.compile(r"\b\d+(?:\.\d+)?\s?(?:M|million)?\s?bpd\b", re.I)),
    ("percentage", re.compile(r"\b\d+(?:\.\d+)?\s?%\b")),
    ("legal_license", re.compile(r"\b(?:GL|General License)\s?\d+[A-Z]?\b", re.I)),
    ("dated_claim", re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+20\d{2}|\b20\d{2}\b", re.I)),
    ("status_claim", re.compile(r"\b(?:under SEC review|pending|approved|rejected|revoked|authorized|currently|no Venezuela-focused ETFs?|no Venezuelan ADRs?)\b", re.I)),
)


def _strip_tags(text: str) -> str:
    text = re.sub(r"\{#[\s\S]*?#\}", " ", text)
    text = re.sub(r"\{[%{][\s\S]*?[%}]\}", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)


def audit(paths: tuple[str, ...] = DEFAULT_PAGES) -> list[dict]:
    findings: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for rel in paths:
        path = ROOT / rel
        if not path.exists():
            continue
        raw = path.read_text(encoding="utf-8")
        text = _strip_tags(raw)
        for category, pattern in PATTERNS:
            for match in pattern.finditer(text):
                snippet = text[max(match.start() - 90, 0): match.end() + 90].strip()
                value = match.group(0).strip()
                key = (rel, category, value.lower())
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    {
                        "page_template": rel,
                        "category": category,
                        "claim_value": value,
                        "context": snippet,
                        "risk_level": _risk_level(category, value),
                        "suggested_fact_key": _suggest_fact_key(rel, category, value),
                    }
                )
    return findings


def _risk_level(category: str, value: str) -> str:
    if category in {"bond_price", "status_claim", "legal_license", "production_volume"}:
        return "high"
    if category in {"money_amount", "percentage"}:
        return "medium"
    if value.startswith("2026") or "2026" in value:
        return "medium"
    return "low"


def _suggest_fact_key(path: str, category: str, value: str) -> str:
    stem = Path(path).stem.replace("venezuela_", "").replace("_", "-")
    cleaned = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")[:40]
    return f"{stem}.{category}.{cleaned}"


def main() -> None:
    findings = audit()
    print(json.dumps({"count": len(findings), "findings": findings}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
