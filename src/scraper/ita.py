"""
Scraper for International Trade Administration Venezuela resources.

Primary pages:
  - https://www.trade.gov/venezuela
  - https://www.trade.gov/venezuela-trade-leads

ITA is a U.S. Department of Commerce source aimed at U.S. companies. We
store its Venezuela hub, guidance pages, contacts, and trade-lead tables as
official external articles so the report/blog/tooling layer can cite them
alongside OFAC, Federal Register, BCV, and other primary sources.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.scraper.base import BaseScraper, ScrapedArticle, ScrapeResult

logger = logging.getLogger(__name__)

ITA_BASE = "https://www.trade.gov"
VENEZUELA_HUB_URL = f"{ITA_BASE}/venezuela"
VENEZUELA_TRADE_LEADS_URL = f"{ITA_BASE}/venezuela-trade-leads"


class ITATradeScraper(BaseScraper):
    """Scrape ITA's Venezuela business pages and trade-lead inventory."""

    def get_source_id(self) -> str:
        return "ita_trade"

    def scrape(self, target_date: Optional[date] = None) -> ScrapeResult:
        start = time.time()
        target_date = target_date or date.today()
        articles: list[ScrapedArticle] = []

        try:
            hub_html = self._fetch(VENEZUELA_HUB_URL).text
            hub_soup = BeautifulSoup(hub_html, "html.parser")
            articles.append(self._article_from_page(
                url=VENEZUELA_HUB_URL,
                soup=hub_soup,
                published_date=target_date,
                article_type="export_guidance",
                fallback_title="ITA Venezuela Business Information Center",
            ))

            for title, url, article_type in self._discover_venezuela_pages(hub_soup):
                try:
                    page_soup = BeautifulSoup(self._fetch(url).text, "html.parser")
                    articles.append(self._article_from_page(
                        url=url,
                        soup=page_soup,
                        published_date=target_date,
                        article_type=article_type,
                        fallback_title=title,
                    ))
                except Exception as exc:
                    logger.warning("ITA page scrape skipped for %s: %s", url, exc)

            # Always fetch the trade-leads page directly even if the hub link
            # label changes. This is the highest-value structured payload.
            if not any(a.source_url == VENEZUELA_TRADE_LEADS_URL for a in articles):
                leads_soup = BeautifulSoup(self._fetch(VENEZUELA_TRADE_LEADS_URL).text, "html.parser")
                articles.append(self._article_from_page(
                    url=VENEZUELA_TRADE_LEADS_URL,
                    soup=leads_soup,
                    published_date=target_date,
                    article_type="trade_lead",
                    fallback_title="Venezuela Trade Leads",
                ))

            # Enrich the trade-leads article with parsed line items.
            for article in articles:
                if article.source_url == VENEZUELA_TRADE_LEADS_URL:
                    article.extra_metadata["trade_leads"] = self._parse_trade_leads(
                        BeautifulSoup(self._fetch(VENEZUELA_TRADE_LEADS_URL).text, "html.parser")
                    )
                    article.extra_metadata["contact_email"] = "tradevenezuela@trade.gov"

            return ScrapeResult(
                source=self.get_source_id(),
                success=True,
                articles=articles,
                duration_seconds=int(time.time() - start),
            )
        except Exception as exc:
            logger.error("ITA trade scrape failed: %s", exc, exc_info=True)
            return ScrapeResult(
                source=self.get_source_id(),
                success=False,
                error=str(exc),
                duration_seconds=int(time.time() - start),
            )

    def _discover_venezuela_pages(self, soup: BeautifulSoup) -> list[tuple[str, str, str]]:
        wanted = {
            "frequently asked questions": "export_guidance",
            "trade leads": "trade_lead",
            "country contacts": "contact",
            "venezuela contacts": "contact",
            "process map": "export_controls",
            "sanctions & controls": "export_controls",
        }
        found: list[tuple[str, str, str]] = []
        seen: set[str] = {VENEZUELA_HUB_URL}
        for a in soup.find_all("a", href=True):
            label = " ".join(a.get_text(" ", strip=True).split())
            href = urljoin(ITA_BASE, a["href"])
            if "trade.gov" not in href or "/venezuela" not in href:
                continue
            label_l = label.lower()
            article_type = None
            for needle, type_ in wanted.items():
                if needle in label_l:
                    article_type = type_
                    break
            if not article_type or href in seen:
                continue
            seen.add(href)
            found.append((label or href.rsplit("/", 1)[-1], href, article_type))
        return found[:8]

    def _article_from_page(
        self,
        *,
        url: str,
        soup: BeautifulSoup,
        published_date: date,
        article_type: str,
        fallback_title: str,
    ) -> ScrapedArticle:
        title = self._title(soup) or fallback_title
        body = self._main_text(soup)
        metadata = {
            "ita_section": "Venezuela Business Information Center",
            "source_agency": "International Trade Administration",
            "contact_email": "tradevenezuela@trade.gov" if "venezuela" in url else None,
        }
        if url == VENEZUELA_TRADE_LEADS_URL:
            metadata["trade_leads"] = self._parse_trade_leads(soup)
        return ScrapedArticle(
            headline=title,
            published_date=published_date,
            source_url=url,
            body_text=body,
            source_name="International Trade Administration",
            source_credibility="official",
            article_type=article_type,
            extra_metadata={k: v for k, v in metadata.items() if v},
        )

    def _title(self, soup: BeautifulSoup) -> str:
        h1 = soup.find("h1")
        if h1:
            return " ".join(h1.get_text(" ", strip=True).split())
        if soup.title and soup.title.string:
            return soup.title.string.strip()
        return ""

    def _main_text(self, soup: BeautifulSoup) -> str:
        main = soup.find("main") or soup.find("article") or soup.body or soup
        for tag in main(["script", "style", "noscript", "svg"]):
            tag.decompose()
        text = " ".join(main.get_text(" ", strip=True).split())
        return text[:8000]

    def _parse_trade_leads(self, soup: BeautifulSoup) -> list[dict]:
        leads: list[dict] = []
        current_sector = "General"
        ignored_sector_labels = {
            "office page menu (country)",
            "expand all",
            "copy link",
            "global business navigator chatbot ^{beta}",
            "footer",
        }

        # Preferred path: real HTML tables.
        for heading in soup.find_all(["h2", "h3", "h4"]):
            label = " ".join(heading.get_text(" ", strip=True).split())
            if label and len(label) <= 80 and label.lower() not in ignored_sector_labels:
                current_sector = label
            table = heading.find_next("table")
            if not table:
                continue
            leads.extend(self._parse_table(table, self._normalize_trade_lead_sector(current_sector)))
        if leads:
            return self._dedupe_leads(leads)

        # Fallback for Drupal pages flattened into text by accessibility
        # wrappers: parse the repeated number/equipment/units/HS/description
        # pattern from visible text.
        text = self._main_text(soup)
        health_idx = text.lower().find("health care")
        if health_idx >= 0:
            current_sector = "Health Care"
            text = text[health_idx:]
        pattern = re.compile(
            r"(?:^|\s)(\d{1,3})\s+(.+?)\s+(\d[\d,]*)\s+"
            r"(\d{4}(?:\.\d{1,4})?)\s+(.+?)(?=\s+\d{1,3}\s+[A-Z0-9]|$)"
        )
        for match in pattern.finditer(text):
            equipment = re.sub(r"\s+", " ", match.group(2)).strip(" -")
            description = re.sub(r"\s+", " ", match.group(5)).strip(" -")
            if len(equipment) < 3:
                continue
            leads.append({
                "sector": current_sector,
                "equipment": equipment,
                "units_requested": int(match.group(3).replace(",", "")),
                "hs_code": match.group(4),
                "hs_description": description[:300],
                "source_url": VENEZUELA_TRADE_LEADS_URL,
            })
        return self._dedupe_leads(leads)

    def _normalize_trade_lead_sector(self, sector: str) -> str:
        label = (sector or "").strip()
        if not label or label.lower() in {"general", "office page menu (country)"}:
            return "Health Care"
        return label

    def _parse_table(self, table, sector: str) -> list[dict]:
        rows = []
        for tr in table.find_all("tr"):
            cells = [" ".join(td.get_text(" ", strip=True).split()) for td in tr.find_all(["td", "th"])]
            cells = [c for c in cells if c]
            if len(cells) < 4 or any("equipment" == c.lower() for c in cells):
                continue
            if cells[0].isdigit() and len(cells) >= 5:
                cells = cells[1:]
            equipment, units, hs_code, hs_desc = cells[:4]
            units_int = None
            if re.fullmatch(r"\d[\d,]*", units):
                units_int = int(units.replace(",", ""))
            rows.append({
                "sector": sector,
                "equipment": equipment,
                "units_requested": units_int,
                "hs_code": hs_code,
                "hs_description": hs_desc,
                "source_url": VENEZUELA_TRADE_LEADS_URL,
            })
        return rows

    def _dedupe_leads(self, leads: list[dict]) -> list[dict]:
        seen: set[tuple] = set()
        out: list[dict] = []
        for lead in leads:
            key = (lead.get("equipment"), lead.get("hs_code"), lead.get("units_requested"))
            if key in seen:
                continue
            seen.add(key)
            out.append(lead)
        return out
