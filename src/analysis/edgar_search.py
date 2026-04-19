"""
SEC EDGAR full-text search adapter.

EDGAR exposes a free, no-key full-text search API at
  https://efts.sec.gov/LATEST/search-index?q=...&forms=10-K,10-Q,8-K&dateRange=custom

We use it to find recent filings where a public company mentions
Venezuela / PdVSA / CITGO / Maduro. A handful of hits is strong evidence
the company has material Venezuela exposure even if it isn't on the
OFAC SDN list.

SEC enforces a ~10 req/sec rate limit per IP and requires a descriptive
User-Agent string with contact info. We comply with both.

Docs: https://www.sec.gov/os/accessing-edgar-data
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from typing import Iterable

import httpx


logger = logging.getLogger(__name__)


EDGAR_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
EDGAR_FILING_BASE = "https://www.sec.gov/Archives/edgar/data"

# Default forms we search. Annual + quarterly + current reports cover
# the vast majority of "the company disclosed Venezuela exposure"
# moments. 20-F covers foreign filers (e.g. ADRs).
DEFAULT_FORMS = ("10-K", "10-Q", "8-K", "20-F", "6-K")

# Search terms we OR together for the Venezuela exposure question. We
# wrap each term in quotes so EDGAR treats them as phrase matches.
VENEZUELA_TERMS = (
    "Venezuela",
    "PdVSA",
    "PDVSA",
    "Citgo",
    "CITGO",
    "Maduro",
    "Caracas",
)


@dataclass(frozen=True)
class EdgarHit:
    accession_no: str
    form: str
    filed: str  # YYYY-MM-DD
    company_name: str
    cik: str
    snippet: str
    url: str

    def to_dict(self) -> dict:
        return asdict(self)


class EdgarRateLimiter:
    """Trivial single-thread rate limiter. SEC's published limit is 10
    req/sec; we stay well below it."""

    def __init__(self, min_interval_s: float = 0.2) -> None:
        self.min_interval_s = min_interval_s
        self._last = 0.0

    def wait(self) -> None:
        now = time.time()
        elapsed = now - self._last
        if elapsed < self.min_interval_s:
            time.sleep(self.min_interval_s - elapsed)
        self._last = time.time()


_DEFAULT_LIMITER = EdgarRateLimiter()


def search_company_venezuela_filings(
    *,
    company_name: str,
    cik: str | None = None,
    forms: Iterable[str] = DEFAULT_FORMS,
    lookback_days: int = 730,
    limit: int = 8,
    rate_limiter: EdgarRateLimiter | None = None,
    user_agent: str = "Caracas Research compliance-bot contact@caracasresearch.com",
) -> list[EdgarHit]:
    """Return up to `limit` recent EDGAR filings by `company_name` that
    contain Venezuela-related terms.

    Strategy: query EDGAR with a constrained phrase search:
        ("Venezuela" OR "PdVSA" OR ...) AND <company filter>
    where <company filter> is the CIK if we have it (most precise) or the
    company name (fuzzy).

    EDGAR returns JSON with a `hits.hits[]` array. Each hit includes the
    accession number, form, filing date, and (when matched on phrase) a
    `_source.display_names` plus a `_source._snippets` highlight. We use
    those to render a citation-grade reference on the page.
    """
    rl = rate_limiter or _DEFAULT_LIMITER
    end = date.today()
    start = end - timedelta(days=lookback_days)

    q_terms = " OR ".join(f'"{t}"' for t in VENEZUELA_TERMS)
    params = {
        "q": q_terms,
        "dateRange": "custom",
        "startdt": start.isoformat(),
        "enddt": end.isoformat(),
        "forms": ",".join(forms),
    }
    if cik:
        cik_clean = str(cik).strip().lstrip("0") or "0"
        params["ciks"] = cik_clean.zfill(10)
    else:
        params["company"] = company_name

    headers = {
        "User-Agent": user_agent,
        "Accept": "application/json",
    }

    rl.wait()
    try:
        with httpx.Client(timeout=20.0, headers=headers) as client:
            resp = client.get(EDGAR_SEARCH_URL, params=params)
            if resp.status_code == 429:
                # Backoff and retry once.
                time.sleep(2.0)
                rl.wait()
                resp = client.get(EDGAR_SEARCH_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("EDGAR search failed for %s: %s", company_name, exc)
        return []

    hits_raw = (((data or {}).get("hits") or {}).get("hits")) or []
    out: list[EdgarHit] = []
    for h in hits_raw[:limit]:
        src = h.get("_source") or {}
        adsh = (src.get("adsh") or h.get("_id") or "").strip()
        form = (src.get("form") or src.get("type") or "").strip()
        filed = (src.get("file_date") or src.get("filed") or "").strip()[:10]
        display_names = src.get("display_names") or []
        cname = display_names[0] if display_names else (src.get("company") or company_name)
        if isinstance(cname, str) and "(" in cname:
            cname = cname.split("(")[0].strip()
        ciks_field = src.get("ciks") or []
        hit_cik = ciks_field[0] if ciks_field else (cik or "")
        snippets = src.get("_snippets") or h.get("_snippets") or []
        snippet = (snippets[0] if snippets else "").strip()
        if isinstance(snippet, list):
            snippet = " ".join(snippet)
        snippet = _clean_snippet(snippet)
        url = _build_filing_url(hit_cik, adsh)
        out.append(EdgarHit(
            accession_no=adsh,
            form=form,
            filed=filed,
            company_name=str(cname),
            cik=str(hit_cik),
            snippet=snippet,
            url=url,
        ))
    return out


def _clean_snippet(s: str) -> str:
    if not s:
        return ""
    # EDGAR wraps highlights in <em> tags; we keep them so the template
    # can show the matched phrase in bold, but we cap the length so it
    # doesn't blow out the layout.
    s = s.replace("\n", " ").strip()
    if len(s) > 320:
        s = s[:320].rsplit(" ", 1)[0] + "…"
    return s


def _build_filing_url(cik: str, accession_no: str) -> str:
    if not cik or not accession_no:
        return ""
    cik_clean = str(cik).lstrip("0") or "0"
    adsh_compact = accession_no.replace("-", "")
    return (
        f"{EDGAR_FILING_BASE}/{cik_clean}/{adsh_compact}/"
        f"{accession_no}-index.htm"
    )


if __name__ == "__main__":
    import argparse
    import json as _json

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("company", help="Company name to search")
    parser.add_argument("--cik", default=None, help="Optional CIK for precise filtering")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    hits = search_company_venezuela_filings(
        company_name=args.company, cik=args.cik, limit=args.limit
    )
    print(_json.dumps([h.to_dict() for h in hits], indent=2))
