"""
S&P 500 company list — sourced from the public Wikipedia table at
https://en.wikipedia.org/wiki/List_of_S%26P_500_companies and cached to
disk so we don't re-scrape on every page generation.

Used by the /companies/<slug>/venezuela-exposure landing pages and the
/tools/public-company-venezuela-exposure-check tool. We deliberately
keep this list in JSON rather than the database so the build is
deterministic from a single git commit and refreshing the list is a
one-line `python -m src.data.sp500_companies --refresh` away.

The Wikipedia table changes ~quarterly when index constituents are
added or removed. The scraper is forgiving: any failure falls back to
the vendored snapshot. A "good" snapshot has ~500 rows.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import httpx
from bs4 import BeautifulSoup


logger = logging.getLogger(__name__)

WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# Cached snapshot lives next to this module so it ships with the repo.
_CACHE_PATH = Path(__file__).resolve().parent / "sp500_snapshot.json"

# Generic legal-entity suffixes we strip when generating slugs / search
# queries. These add nothing to the search intent ("Apple Inc Venezuela
# exposure" vs "Apple Venezuela exposure") and create slug noise.
_LEGAL_SUFFIXES = (
    "incorporated", "inc", "corporation", "corp", "company", "co",
    "ltd", "limited", "llc", "lp", "plc", "holdings", "holding",
    "group", "the", "n.v.", "nv", "sa", "ag",
)

# A couple of generic / non-distinct surnames that, if we sluggified by
# them, would collide with countless other companies. Use the full name
# for these.
_KEEP_SUFFIX_TICKERS: set[str] = set()


@dataclass(frozen=True)
class SP500Company:
    ticker: str
    name: str
    sector: str
    sub_industry: str
    headquarters: str
    cik: str | None = None  # SEC Central Index Key — optional, used for EDGAR lookups.

    @property
    def slug(self) -> str:
        return slugify_company(self.name, self.ticker)

    @property
    def short_name(self) -> str:
        """Name without the legal suffix (Apple Inc. -> Apple). Useful as
        a search term against EDGAR / Federal Register / our own corpus."""
        return _strip_legal_suffix(self.name)


def slugify_company(name: str, ticker: str) -> str:
    """Generate a stable URL slug for a company. Format: shortname-ticker
    so collisions across re-listed names are impossible (e.g. there are
    multiple "Newmont"s historically).

    Example: ("Apple Inc.", "AAPL") -> "apple-aapl"
    """
    short = _strip_legal_suffix(name)
    base = re.sub(r"[^a-z0-9]+", "-", short.lower()).strip("-")
    if not base:
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "company"
    return f"{base}-{ticker.lower()}"


def _strip_legal_suffix(name: str) -> str:
    cleaned = re.sub(r"[\.,]", "", name).strip()
    parts = cleaned.split()
    while parts and parts[-1].lower().rstrip(".") in _LEGAL_SUFFIXES:
        parts.pop()
    return " ".join(parts) or name


def list_sp500_companies() -> list[SP500Company]:
    """Return the cached S&P 500 list. Falls back to an empty list only
    if both the cache file is missing and the Wikipedia scrape fails."""
    if _CACHE_PATH.exists():
        try:
            data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
            return [SP500Company(**row) for row in data.get("companies", [])]
        except Exception as exc:
            logger.warning("sp500 cache read failed (%s); falling back to scrape", exc)

    try:
        return refresh_sp500_snapshot()
    except Exception as exc:
        logger.error("sp500 wikipedia scrape failed: %s", exc, exc_info=True)
        return []


def find_company(query: str) -> SP500Company | None:
    """Resolve a free-text query (ticker, slug, or partial name) to one
    S&P 500 company. Used by the dynamic tool route. Returns None when
    no confident match is found."""
    if not query:
        return None
    q = query.strip().lower()
    companies = list_sp500_companies()
    if not companies:
        return None

    for c in companies:
        if c.ticker.lower() == q or c.slug == q:
            return c

    for c in companies:
        if c.name.lower() == q or c.short_name.lower() == q:
            return c

    contains_matches = [c for c in companies if q in c.name.lower() or q in c.short_name.lower()]
    if len(contains_matches) == 1:
        return contains_matches[0]
    if contains_matches:
        contains_matches.sort(key=lambda c: len(c.name))
        return contains_matches[0]

    return None


def refresh_sp500_snapshot(*, persist: bool = True) -> list[SP500Company]:
    """Scrape the live Wikipedia table and (by default) overwrite the
    on-disk JSON snapshot. Returns the freshly-parsed list.

    The Wikipedia table id is "constituents". Columns we want:
      0  Symbol            -> ticker
      1  Security          -> name
      2  GICS Sector
      3  GICS Sub-Industry
      4  Headquarters Location
      5  Date added (skip)
      6  CIK               -> SEC Central Index Key
    """
    headers = {
        "User-Agent": (
            "CaracasResearchBot/1.0 (+https://caracasresearch.com/about) "
            "python-httpx (sp500 list refresh)"
        )
    }
    with httpx.Client(timeout=30.0, headers=headers, follow_redirects=True) as client:
        resp = client.get(WIKIPEDIA_URL)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

    table = soup.find("table", {"id": "constituents"})
    if table is None:
        # Some snapshots don't carry the id; fall back to the first
        # wikitable on the page (the constituents table is always first).
        table = soup.find("table", class_=re.compile(r"wikitable"))
    if table is None:
        raise RuntimeError("constituents table not found in Wikipedia HTML")

    rows: list[SP500Company] = []
    body = table.find("tbody") or table
    for tr in body.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) < 5:
            continue
        # Skip header row (its cells are <th>, not <td>).
        if all(c.name == "th" for c in cells):
            continue

        ticker = cells[0].get_text(strip=True).replace("\xa0", " ")
        name = cells[1].get_text(" ", strip=True)
        sector = cells[2].get_text(" ", strip=True)
        sub_industry = cells[3].get_text(" ", strip=True)
        hq = cells[4].get_text(" ", strip=True)
        cik = cells[6].get_text(strip=True) if len(cells) > 6 else None
        cik = cik or None

        if not ticker or not name:
            continue
        # Wikipedia uses a "." in some tickers (e.g. BRK.B) — keep as-is
        # for display; SEC and most data providers accept either form.

        rows.append(SP500Company(
            ticker=ticker,
            name=name,
            sector=sector,
            sub_industry=sub_industry,
            headquarters=hq,
            cik=cik,
        ))

    if len(rows) < 400:
        raise RuntimeError(f"sp500 scrape returned only {len(rows)} rows; refusing to overwrite cache")

    if persist:
        _CACHE_PATH.write_text(
            json.dumps(
                {"source": WIKIPEDIA_URL, "count": len(rows), "companies": [asdict(c) for c in rows]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info("sp500 snapshot refreshed: %d companies -> %s", len(rows), _CACHE_PATH)

    return rows


def iter_chunked(seq: Iterable[SP500Company], n: int) -> Iterable[list[SP500Company]]:
    chunk: list[SP500Company] = []
    for item in seq:
        chunk.append(item)
        if len(chunk) >= n:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="Re-scrape Wikipedia and overwrite the snapshot")
    parser.add_argument("--print", action="store_true", help="Print the loaded list (head 10)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s")

    if args.refresh:
        rows = refresh_sp500_snapshot()
        print(f"refreshed: {len(rows)} companies")
    if args.print:
        rows = list_sp500_companies()
        print(f"loaded {len(rows)} companies; first 10:")
        for c in rows[:10]:
            print(f"  {c.ticker:8s}  {c.name:40s}  {c.sector:25s}  slug={c.slug}")
