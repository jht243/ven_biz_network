"""
Google News RSS scraper for Venezuela investment topics.

Why this exists: GDELT is the only general-news source in the daily
pipeline and it has been intermittently failing — SSL handshake
timeouts, non-JSON responses, etc. Adding Google News RSS as a
parallel feed means a single-source outage no longer blanks out the
homepage article list.

Scope: topical queries only. Per-entity adverse-media searches still
live in src/research/enrichment.py and are deliberately not imported from
here (different cache layer, different UI assumptions, different
rate-limit budget).

Failure contract: this scraper MUST NOT block the daily cron. Every
network call has an explicit timeout, every query has a bounded
retry count, and the scraper as a whole has a hard wall-clock
budget. If anything goes wrong we return whatever we have collected
so far (possibly an empty list) with success=True, so the rest of
the pipeline keeps running.
"""

from __future__ import annotations

import logging
import re
import ssl
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime
from typing import Optional

import httpx

from src.scraper.base import BaseScraper, ScrapedArticle, ScrapeResult

logger = logging.getLogger(__name__)

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"

# Topical queries — mix of English and Spanish, weighted toward
# investor-relevant policy/economic vocabulary. Keep this list short
# and high-yield: every query costs one HTTP round-trip and our
# wall-clock budget is fixed.
QUERIES: tuple[str, ...] = (
    'Venezuela sanctions OFAC',
    'Venezuela investment OR Chevron OR PDVSA',
    'Venezuela "exchange rate" OR economy OR inflation',
    'Venezuela mining OR gold OR oil law',
    # Spanish — Venezuelan press
    'Venezuela "tipo de cambio" OR BCV',
    'Venezuela "ley orgánica" OR decreto',
    'Venezuela "Asamblea Nacional" OR TSJ',
)

MAX_ITEMS_PER_QUERY = 25
MAX_TOTAL_WALLCLOCK_SECONDS = 90
PER_QUERY_BACKOFF_SECONDS = 5
PER_QUERY_MAX_ATTEMPTS = 2

# Soft credibility tiering — same shape as gdelt._infer_credibility.
# We do NOT hard-filter; the relevance scorer in src/analyzer.py decides
# which articles surface on the homepage. Tiering only affects the
# trust badge in the UI and the prompt context handed to the scorer.
HIGH_CREDIBILITY = {
    "reuters.com", "apnews.com", "bbc.com", "bbc.co.uk",
    "bloomberg.com", "ft.com", "economist.com", "wsj.com",
    "nytimes.com", "washingtonpost.com", "aljazeera.com",
    "theguardian.com", "france24.com", "dw.com",
    # Venezuelan independent / diaspora press — generally well-sourced
    "elnacional.com", "talcualdigital.com", "efectococuyo.com",
    "prodavinci.com", "runrun.es", "cronicauno.com", "armando.info",
}
STATE_MEDIA = {
    "telesurtv.net", "vtv.gob.ve", "correodelcaroni.com",
    "ultimasnoticias.com.ve", "avn.info.ve", "albaciudad.org",
}

# Best-effort mapping from the publisher name Google embeds in RSS
# titles ("Headline - Reuters") to a canonical domain we can grade
# against the HIGH_CREDIBILITY / STATE_MEDIA sets. Google does not
# return the publisher's actual domain in the RSS feed, so this is a
# heuristic — unknown publishers fall through to tier2, which is the
# same default GDELT uses.
_PUBLISHER_DOMAIN_ALIASES: dict[str, str] = {
    "reuters": "reuters.com",
    "ap news": "apnews.com",
    "associated press": "apnews.com",
    "bloomberg": "bloomberg.com",
    "financial times": "ft.com",
    "the wall street journal": "wsj.com",
    "wall street journal": "wsj.com",
    "the new york times": "nytimes.com",
    "new york times": "nytimes.com",
    "the washington post": "washingtonpost.com",
    "washington post": "washingtonpost.com",
    "bbc": "bbc.com",
    "bbc news": "bbc.com",
    "the bbc": "bbc.com",
    "the economist": "economist.com",
    "al jazeera": "aljazeera.com",
    "the guardian": "theguardian.com",
    "france 24": "france24.com",
    "dw": "dw.com",
    "deutsche welle": "dw.com",
    "el nacional": "elnacional.com",
    "tal cual": "talcualdigital.com",
    "efecto cocuyo": "efectococuyo.com",
    "prodavinci": "prodavinci.com",
    "runrunes": "runrun.es",
    "crónica uno": "cronicauno.com",
    "cronica uno": "cronicauno.com",
    "armando.info": "armando.info",
    "telesur": "telesurtv.net",
    "telesur english": "telesurtv.net",
    "vtv": "vtv.gob.ve",
    "ultimas noticias": "ultimasnoticias.com.ve",
    "últimas noticias": "ultimasnoticias.com.ve",
}


class GoogleNewsScraper(BaseScraper):
    """Topical news from Google News RSS as a parallel feed to GDELT."""

    def __init__(self) -> None:
        super().__init__()
        # Replace the inherited 30s blanket timeout with stricter
        # per-phase bounds. Google News RSS sometimes hangs on TLS
        # handshake when their CDN throttles us; failing fast is
        # more important than maximising coverage because the daily
        # cron must never get stuck on a single source.
        self.client.close()
        self.client = httpx.Client(
            timeout=httpx.Timeout(15.0, connect=8.0),
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
            },
        )

    def get_source_id(self) -> str:
        return "google_news"

    def scrape(self, target_date: Optional[date] = None) -> ScrapeResult:
        start = time.monotonic()
        deadline = start + MAX_TOTAL_WALLCLOCK_SECONDS
        target_date = target_date or date.today()

        seen_urls: set[str] = set()
        articles: list[ScrapedArticle] = []
        per_query_summary: list[str] = []

        for query in QUERIES:
            if time.monotonic() >= deadline:
                logger.warning(
                    "Google News: hit %ds wall-clock budget before query "
                    "%r — returning %d articles collected so far",
                    MAX_TOTAL_WALLCLOCK_SECONDS, query[:40], len(articles),
                )
                break

            new_items = self._safely_query(query)

            added = 0
            for art in new_items:
                if not art.source_url or art.source_url in seen_urls:
                    continue
                seen_urls.add(art.source_url)
                articles.append(art)
                added += 1

            per_query_summary.append(f"'{query[:30]}'={added}")

        elapsed = int(time.monotonic() - start)
        logger.info(
            "Google News: %d unique articles across %d/%d queries in %ds (%s)",
            len(articles), len(per_query_summary), len(QUERIES),
            elapsed, " ".join(per_query_summary),
        )

        # success=True even with 0 articles. Partial / total network
        # failure of a third-party source is operationally normal and
        # must NOT bubble into ScrapeResult.success=False, because the
        # pipeline treats success=False as a logged error condition.
        return ScrapeResult(
            source=self.get_source_id(),
            success=True,
            articles=articles,
            duration_seconds=elapsed,
        )

    # ── internals ─────────────────────────────────────────────────────

    def _safely_query(self, query: str) -> list[ScrapedArticle]:
        """Defensive wrapper: catches every exception so a single bad
        query can never bring the whole scraper down."""
        try:
            return self._query_articles(query)
        except Exception as exc:
            logger.warning(
                "Google News query %r raised unexpectedly (%s) — skipping",
                query[:40], type(exc).__name__,
            )
            return []

    def _query_articles(self, query: str) -> list[ScrapedArticle]:
        params = {
            "q": query,
            "hl": "en-US",
            "gl": "US",
            "ceid": "US:en",
        }

        body: Optional[str] = None
        for attempt in range(PER_QUERY_MAX_ATTEMPTS):
            try:
                resp = self.client.get(GOOGLE_NEWS_RSS, params=params)
                resp.raise_for_status()
                body = resp.text
                break
            except (
                httpx.ConnectError,
                httpx.TimeoutException,
                httpx.RemoteProtocolError,
                httpx.HTTPStatusError,
                ssl.SSLError,
            ) as exc:
                logger.warning(
                    "Google News query %r attempt %d/%d failed (%s)",
                    query[:40], attempt + 1, PER_QUERY_MAX_ATTEMPTS,
                    type(exc).__name__,
                )
                if attempt + 1 < PER_QUERY_MAX_ATTEMPTS:
                    time.sleep(PER_QUERY_BACKOFF_SECONDS)

        if not body:
            return []

        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            logger.warning(
                "Google News query %r returned non-XML: %s", query[:40], exc
            )
            return []

        out: list[ScrapedArticle] = []
        for item in root.findall(".//item")[:MAX_ITEMS_PER_QUERY]:
            parsed = self._parse_item(item)
            if parsed is not None:
                out.append(parsed)
        return out

    def _parse_item(self, item) -> Optional[ScrapedArticle]:
        try:
            title_full = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_str = (item.findtext("pubDate") or "").strip()
            desc = item.findtext("description") or ""

            if not title_full or not link:
                return None

            # Google News titles are formatted "Headline - Publisher Name".
            title = title_full
            publisher = ""
            if " - " in title_full:
                title, publisher = title_full.rsplit(" - ", 1)
                title = title.strip()
                publisher = publisher.strip()

            pub_date = self._parse_rfc822(pub_str) or date.today()
            publisher_domain = self._publisher_to_domain(publisher)

            return ScrapedArticle(
                headline=title,
                published_date=pub_date,
                source_url=link,
                body_text=None,
                # Stable scraper tag so pipeline._resolve_source_type
                # maps every row to SourceType.GOOGLE_NEWS regardless
                # of which publisher Google attached. The actual
                # publisher rides in extra_metadata for the renderer.
                source_name="Google News",
                source_credibility=self._infer_credibility(publisher_domain),
                article_type="news",
                extra_metadata={
                    "publisher": publisher,
                    "publisher_domain": publisher_domain,
                    "snippet": self._strip_html(desc)[:240],
                    "query_via": "google_news_rss",
                },
            )
        except Exception as exc:
            logger.debug("Google News item parse error: %s", exc)
            return None

    @staticmethod
    def _parse_rfc822(s: str) -> Optional[date]:
        if not s:
            return None
        for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S"):
            try:
                return datetime.strptime(s.strip()[:31], fmt).date()
            except ValueError:
                continue
        try:
            return datetime.strptime(s[:25], "%a, %d %b %Y %H:%M:%S").date()
        except ValueError:
            return None

    @staticmethod
    def _publisher_to_domain(publisher: str) -> str:
        """Best-effort normalisation of Google's "Reuters" / "El Nacional"
        style publisher labels into a domain we can grade against the
        credibility tables. Unknown publishers fall through to the
        lowercased label, which is fine — the worst case is tier2."""
        if not publisher:
            return ""
        slug = publisher.lower().strip()
        return _PUBLISHER_DOMAIN_ALIASES.get(slug, slug)

    @staticmethod
    def _infer_credibility(domain: str) -> str:
        d = (domain or "").lower()
        if any(h in d for h in HIGH_CREDIBILITY):
            return "tier1"
        if any(s in d for s in STATE_MEDIA):
            return "state"
        return "tier2"

    @staticmethod
    def _strip_html(s: str) -> str:
        if not s:
            return ""
        text = re.sub(r"<[^>]+>", "", s)
        return re.sub(r"\s+", " ", text).strip()
