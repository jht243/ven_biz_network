"""
SEO audit engine — BFS-crawls the site via Flask's test client (zero
network, zero external deps), checks every page for SEO hygiene, and
produces a structured AuditReport.

Two layers of checks:
  1. Per-page: title, meta description, canonical, OG tags, H1 count,
     heading hierarchy, JSON-LD, body word count, cluster nav presence.
  2. Cross-page: cluster-topology coverage, sitemap reachability,
     hub-page orphan detection.

Usage:
    from src.seo.audit import run_audit
    report = run_audit(max_pages=200)
    print(report.summary())
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class Finding:
    path: str
    severity: Severity
    category: str
    message: str

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "severity": self.severity.value,
            "category": self.category,
            "message": self.message,
        }


@dataclass
class PageAudit:
    path: str
    status_code: int
    title: str = ""
    title_length: int = 0
    meta_description: str = ""
    meta_description_length: int = 0
    canonical: str = ""
    og_title: str = ""
    og_description: str = ""
    og_image: str = ""
    robots: str = ""
    h1_count: int = 0
    h1_texts: list[str] = field(default_factory=list)
    heading_levels: list[int] = field(default_factory=list)
    jsonld_blocks: list[dict] = field(default_factory=list)
    jsonld_types: list[str] = field(default_factory=list)
    internal_links: list[tuple[str, str]] = field(default_factory=list)  # (href, anchor)
    body_word_count: int = 0
    has_cluster_nav: bool = False
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "status_code": self.status_code,
            "title": self.title,
            "title_length": self.title_length,
            "meta_description_length": self.meta_description_length,
            "canonical": self.canonical,
            "og_title": self.og_title,
            "og_image": self.og_image,
            "h1_count": self.h1_count,
            "heading_levels": self.heading_levels,
            "jsonld_types": self.jsonld_types,
            "body_word_count": self.body_word_count,
            "has_cluster_nav": self.has_cluster_nav,
            "internal_link_count": len(self.internal_links),
            "findings": [f.to_dict() for f in self.findings],
        }


@dataclass
class AuditReport:
    pages_crawled: int = 0
    pages_ok: int = 0
    page_audits: dict[str, PageAudit] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.WARNING]

    @property
    def info(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.INFO]

    def summary(self) -> str:
        lines = [
            f"SEO Audit: {self.pages_crawled} pages crawled, {self.pages_ok} clean",
            f"  Errors:   {len(self.errors)}",
            f"  Warnings: {len(self.warnings)}",
            f"  Info:     {len(self.info)}",
        ]
        if self.errors:
            lines.append("")
            lines.append("Errors:")
            for f in self.errors[:20]:
                lines.append(f"  [{f.category}] {f.path}: {f.message}")
        if self.warnings:
            lines.append("")
            lines.append("Warnings (first 20):")
            for f in self.warnings[:20]:
                lines.append(f"  [{f.category}] {f.path}: {f.message}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "pages_crawled": self.pages_crawled,
            "pages_ok": self.pages_ok,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "info_count": len(self.info),
            "findings": [f.to_dict() for f in self.findings],
            "page_audits": {k: v.to_dict() for k, v in self.page_audits.items()},
        }


# ---------------------------------------------------------------------------
# HTML parser — single-pass extraction of SEO-relevant signals
# ---------------------------------------------------------------------------

_SKIP_EXTENSIONS = frozenset((
    ".pdf", ".xml", ".txt", ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".ico", ".css", ".js", ".woff", ".woff2", ".ttf", ".eot", ".map",
    ".webp", ".avif", ".mp4", ".webm",
))


class _SEOParser(HTMLParser):
    """Single-pass HTML parser that extracts all SEO-relevant signals."""

    def __init__(self):
        super().__init__()
        self.title = ""
        self.meta_description = ""
        self.canonical = ""
        self.og_title = ""
        self.og_description = ""
        self.og_image = ""
        self.robots = ""
        self.h1_texts: list[str] = []
        self.heading_levels: list[int] = []
        self.jsonld_blocks: list[dict] = []
        self.internal_links: list[tuple[str, str]] = []  # (href, anchor_text)
        self.has_cluster_nav = False

        self._in_title = False
        self._title_parts: list[str] = []
        self._in_h1 = False
        self._h1_parts: list[str] = []
        self._in_script_jsonld = False
        self._jsonld_parts: list[str] = []
        self._in_a = False
        self._a_href = ""
        self._a_parts: list[str] = []
        self._in_body = False
        self._body_text_parts: list[str] = []
        self._heading_re = re.compile(r"^h([1-6])$", re.IGNORECASE)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        tag_lower = tag.lower()
        attr_dict = {k.lower(): (v or "") for k, v in attrs}

        if tag_lower == "title":
            self._in_title = True
            self._title_parts = []
        elif tag_lower == "meta":
            name = attr_dict.get("name", "").lower()
            prop = attr_dict.get("property", "").lower()
            content = attr_dict.get("content", "")
            if name == "description":
                self.meta_description = content
            elif name == "robots":
                self.robots = content
            elif prop == "og:title":
                self.og_title = content
            elif prop == "og:description":
                self.og_description = content
            elif prop == "og:image":
                self.og_image = content
        elif tag_lower == "link":
            rel = attr_dict.get("rel", "").lower()
            if rel == "canonical":
                self.canonical = attr_dict.get("href", "")
        elif tag_lower == "script":
            stype = attr_dict.get("type", "").lower()
            if stype == "application/ld+json":
                self._in_script_jsonld = True
                self._jsonld_parts = []
        elif tag_lower == "body":
            self._in_body = True
        elif tag_lower == "h1":
            self._in_h1 = True
            self._h1_parts = []
            self.heading_levels.append(1)
        elif m := self._heading_re.match(tag_lower):
            level = int(m.group(1))
            self.heading_levels.append(level)
        elif tag_lower == "a":
            href = attr_dict.get("href", "")
            if href:
                self._in_a = True
                self._a_href = href
                self._a_parts = []
        elif tag_lower in ("nav", "div", "section"):
            cls = attr_dict.get("class", "")
            if "cluster-nav" in cls:
                self.has_cluster_nav = True

    def handle_endtag(self, tag: str):
        tag_lower = tag.lower()
        if tag_lower == "title":
            self._in_title = False
            self.title = "".join(self._title_parts).strip()
        elif tag_lower == "h1":
            self._in_h1 = False
            self.h1_texts.append("".join(self._h1_parts).strip())
        elif tag_lower == "script" and self._in_script_jsonld:
            self._in_script_jsonld = False
            raw = "".join(self._jsonld_parts).strip()
            if raw:
                try:
                    parsed = json.loads(raw)
                    self.jsonld_blocks.append(parsed)
                except json.JSONDecodeError:
                    pass
        elif tag_lower == "a" and self._in_a:
            self._in_a = False
            anchor = "".join(self._a_parts).strip()
            self.internal_links.append((self._a_href, anchor))
        elif tag_lower == "body":
            self._in_body = False

    def handle_data(self, data: str):
        if self._in_title:
            self._title_parts.append(data)
        if self._in_h1:
            self._h1_parts.append(data)
        if self._in_script_jsonld:
            self._jsonld_parts.append(data)
        if self._in_a:
            self._a_parts.append(data)
        if self._in_body:
            self._body_text_parts.append(data)

    @property
    def body_word_count(self) -> int:
        text = " ".join(self._body_text_parts)
        return len([w for w in text.split() if w.strip()])


# ---------------------------------------------------------------------------
# Per-page checks
# ---------------------------------------------------------------------------

def _check_page(path: str, parsed: _SEOParser, status_code: int) -> PageAudit:
    """Run all per-page SEO checks and return a PageAudit."""
    audit = PageAudit(
        path=path,
        status_code=status_code,
        title=parsed.title,
        title_length=len(parsed.title),
        meta_description=parsed.meta_description,
        meta_description_length=len(parsed.meta_description),
        canonical=parsed.canonical,
        og_title=parsed.og_title,
        og_description=parsed.og_description,
        og_image=parsed.og_image,
        robots=parsed.robots,
        h1_count=len(parsed.h1_texts),
        h1_texts=parsed.h1_texts,
        heading_levels=parsed.heading_levels,
        jsonld_blocks=parsed.jsonld_blocks,
        jsonld_types=_extract_jsonld_types(parsed.jsonld_blocks),
        internal_links=parsed.internal_links,
        body_word_count=parsed.body_word_count,
        has_cluster_nav=parsed.has_cluster_nav,
    )

    def _add(severity: Severity, category: str, message: str):
        f = Finding(path=path, severity=severity, category=category, message=message)
        audit.findings.append(f)

    # Title
    if not parsed.title:
        _add(Severity.ERROR, "title", "Missing <title> tag")
    elif len(parsed.title) < 20:
        _add(Severity.WARNING, "title", f"Title too short ({len(parsed.title)} chars): '{parsed.title}'")
    elif len(parsed.title) > 70:
        _add(Severity.WARNING, "title", f"Title too long ({len(parsed.title)} chars)")

    # Meta description
    if not parsed.meta_description:
        _add(Severity.WARNING, "meta_description", "Missing meta description")
    elif len(parsed.meta_description) < 50:
        _add(Severity.WARNING, "meta_description", f"Meta description too short ({len(parsed.meta_description)} chars)")
    elif len(parsed.meta_description) > 160:
        _add(Severity.WARNING, "meta_description", f"Meta description too long ({len(parsed.meta_description)} chars)")

    # Canonical
    if not parsed.canonical or not parsed.canonical.strip():
        _add(Severity.WARNING, "canonical", "Missing or empty canonical URL")

    # OG tags
    if not parsed.og_title:
        _add(Severity.WARNING, "og:title", "Missing og:title")
    if not parsed.og_image or not parsed.og_image.strip():
        _add(Severity.WARNING, "og:image", "Missing or empty og:image")

    # H1
    if len(parsed.h1_texts) == 0:
        _add(Severity.ERROR, "h1", "No H1 tag found")
    elif len(parsed.h1_texts) > 1:
        _add(Severity.WARNING, "h1", f"Multiple H1 tags ({len(parsed.h1_texts)})")

    # Heading hierarchy
    if parsed.heading_levels:
        for i in range(1, len(parsed.heading_levels)):
            prev = parsed.heading_levels[i - 1]
            curr = parsed.heading_levels[i]
            if curr > prev + 1:
                _add(
                    Severity.WARNING,
                    "heading_hierarchy",
                    f"Skipped heading level: H{prev} → H{curr}",
                )
                break  # one warning per page is enough

    # JSON-LD
    if not parsed.jsonld_blocks:
        _add(Severity.WARNING, "jsonld", "No JSON-LD structured data block found")

    # Body word count
    if parsed.body_word_count < 100:
        _add(Severity.INFO, "thin_content", f"Thin content: {parsed.body_word_count} words")

    return audit


def _extract_jsonld_types(blocks: list[dict]) -> list[str]:
    types: list[str] = []
    for block in blocks:
        if "@type" in block:
            t = block["@type"]
            types.append(t if isinstance(t, str) else str(t))
        if "@graph" in block and isinstance(block["@graph"], list):
            for item in block["@graph"]:
                if isinstance(item, dict) and "@type" in item:
                    t = item["@type"]
                    types.append(t if isinstance(t, str) else str(t))
    return types


# ---------------------------------------------------------------------------
# Cross-page checks
# ---------------------------------------------------------------------------

_HUB_PATHS = frozenset((
    "/",
    "/tools",
    "/sanctions-tracker",
    "/companies",
    "/invest-in-venezuela",
    "/briefing",
    "/explainers",
    "/travel",
    "/people",
    "/research/sdn/",
))


def _cross_page_checks(
    report: AuditReport,
    crawled_paths: set[str],
    inbound_links: dict[str, int],
) -> None:
    """Run cross-page checks and append findings to the report."""
    from src.seo.cluster_topology import CLUSTERS

    # 1. Every cluster topology path must be reachable
    for cluster in CLUSTERS.values():
        for path in cluster.all_paths():
            norm = path.rstrip("/")
            if norm not in crawled_paths and path not in crawled_paths:
                report.findings.append(Finding(
                    path=path,
                    severity=Severity.ERROR,
                    category="cluster_reachability",
                    message=f"Cluster '{cluster.key}' path not reachable during crawl",
                ))

    # 2. Cluster pages should have cluster nav
    for path, audit in report.page_audits.items():
        from src.seo.cluster_topology import cluster_for
        if cluster_for(path) and not audit.has_cluster_nav:
            report.findings.append(Finding(
                path=path,
                severity=Severity.WARNING,
                category="cluster_nav",
                message="Page belongs to a topic cluster but missing cluster nav block",
            ))

    # 3. Hub pages need at least 2 inbound internal links
    for hub in _HUB_PATHS:
        norm = hub.rstrip("/") or "/"
        count = inbound_links.get(norm, 0)
        if count < 2 and norm in crawled_paths:
            report.findings.append(Finding(
                path=norm,
                severity=Severity.WARNING,
                category="orphan_hub",
                message=f"Hub page has only {count} inbound internal link(s) (need ≥ 2)",
            ))


# ---------------------------------------------------------------------------
# BFS crawler
# ---------------------------------------------------------------------------

def _normalize_path(href: str) -> str | None:
    """Normalize an href to a path suitable for the crawl queue.
    Returns None for external URLs or static assets."""
    if not href:
        return None
    parsed = urlparse(href)
    if parsed.scheme and parsed.scheme not in ("http", "https", ""):
        return None
    if parsed.netloc and parsed.netloc not in (
        "localhost", "127.0.0.1", "caracasresearch.com",
        "www.caracasresearch.com",
    ):
        return None
    path = parsed.path or "/"
    if not path.startswith("/"):
        return None
    # Skip static assets
    lower = path.lower()
    if any(lower.endswith(ext) for ext in _SKIP_EXTENSIONS):
        return None
    # Skip /static/, /api/, /health, /admin
    if lower.startswith(("/static/", "/api/", "/health", "/admin")):
        return None
    return path


def _gather_seed_urls() -> list[str]:
    """Build the seed URL list from cluster topology + known hubs."""
    seeds: set[str] = set()

    from src.seo.cluster_topology import CLUSTERS
    for cluster in CLUSTERS.values():
        for path in cluster.all_paths():
            seeds.add(path)

    seeds.update(_HUB_PATHS)

    # Add some key pages not in clusters
    seeds.update((
        "/briefing",
        "/explainers",
        "/real-estate/",
        "/tools",
    ))

    return sorted(seeds)


def run_audit(
    *,
    max_pages: int = 200,
    follow_links: bool = True,
    seed_urls: list[str] | None = None,
) -> AuditReport:
    """Crawl the site via Flask test client and produce an AuditReport.

    Args:
        max_pages: Maximum number of pages to crawl.
        follow_links: If False, only crawl seed pages (no BFS).
        seed_urls: Override the default seed list.
    """
    from server import app
    from src.models import init_db

    init_db()

    report = AuditReport()
    crawled: set[str] = set()
    inbound_links: dict[str, int] = defaultdict(int)

    seeds = seed_urls or _gather_seed_urls()
    queue: deque[str] = deque(seeds)

    with app.test_client() as client:
        while queue and len(crawled) < max_pages:
            path = queue.popleft()
            norm = path.rstrip("/") or "/"

            if norm in crawled:
                continue
            crawled.add(norm)

            try:
                resp = client.get(path, follow_redirects=True)
            except Exception as exc:
                logger.warning("Crawl error on %s: %s", path, exc)
                report.findings.append(Finding(
                    path=path,
                    severity=Severity.ERROR,
                    category="crawl_error",
                    message=f"Failed to fetch: {exc}",
                ))
                continue

            if resp.status_code >= 400:
                report.findings.append(Finding(
                    path=path,
                    severity=Severity.ERROR,
                    category="http_error",
                    message=f"HTTP {resp.status_code}",
                ))
                continue

            html = resp.data.decode("utf-8", errors="replace")

            parser = _SEOParser()
            try:
                parser.feed(html)
            except Exception as exc:
                logger.warning("Parse error on %s: %s", path, exc)
                continue

            page_audit = _check_page(path, parser, resp.status_code)
            report.page_audits[norm] = page_audit
            report.findings.extend(page_audit.findings)

            # Track inbound links and queue new pages
            for href, _anchor in parser.internal_links:
                target = _normalize_path(href)
                if target:
                    target_norm = target.rstrip("/") or "/"
                    inbound_links[target_norm] += 1
                    if follow_links and target_norm not in crawled:
                        queue.append(target)

    report.pages_crawled = len(crawled)
    report.pages_ok = sum(
        1 for pa in report.page_audits.values() if not pa.findings
    )

    _cross_page_checks(report, crawled, inbound_links)

    logger.info(
        "SEO audit complete: %d pages, %d errors, %d warnings",
        report.pages_crawled, len(report.errors), len(report.warnings),
    )
    return report
