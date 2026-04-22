"""
Flask web server for Caracas Research.

Serves the generated report.html on Render (or locally).
"""

from __future__ import annotations

import gzip
import hmac
import io
import logging
import time
from pathlib import Path

import httpx
from flask import Flask, send_from_directory, abort, request, jsonify, Response, redirect
from werkzeug.exceptions import HTTPException

from src.config import settings
from src.storage_remote import (
    fetch_report_html,
    supabase_storage_enabled,
    supabase_storage_read_enabled,
)

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_STATIC_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(
    __name__,
    static_folder=str(_STATIC_DIR),
    static_url_path="/static",
)


GZIP_MIME_PREFIXES = (
    "text/",
    "application/json",
    "application/xml",
    "application/javascript",
    "application/ld+json",
    "image/svg+xml",
)
GZIP_MIN_BYTES = 500


@app.after_request
def _gzip_response(response: Response) -> Response:
    """
    Gzip-compress eligible responses when the client advertises support.
    Skips small bodies, already-encoded responses, and non-text content.
    """
    try:
        if response.direct_passthrough:
            return response
        if response.status_code < 200 or response.status_code >= 300:
            return response
        if "Content-Encoding" in response.headers:
            return response
        if "gzip" not in (request.headers.get("Accept-Encoding", "") or "").lower():
            return response

        mimetype = (response.mimetype or "").lower()
        if not any(mimetype.startswith(p) for p in GZIP_MIME_PREFIXES):
            return response

        data = response.get_data()
        if len(data) < GZIP_MIN_BYTES:
            return response

        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6) as gz:
            gz.write(data)
        compressed = buf.getvalue()

        response.set_data(compressed)
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Content-Length"] = str(len(compressed))
        existing_vary = response.headers.get("Vary", "")
        if "Accept-Encoding" not in existing_vary:
            response.headers["Vary"] = (existing_vary + ", Accept-Encoding").lstrip(", ")
    except Exception as exc:
        logger.warning("gzip middleware skipped due to error: %s", exc)
    return response



logger = logging.getLogger(__name__)

OUTPUT_DIR = settings.output_dir

BUTTONDOWN_API_URL = "https://api.buttondown.com/v1/subscribers"

# Tiny in-memory cache so we don't hit Supabase Storage on every page view.
_REPORT_CACHE: dict = {"html": None, "fetched_at": 0.0}
_REPORT_CACHE_TTL_SECONDS = 60

# Small in-memory cache for top-nav pages that are expensive to re-render on
# every click (DB reads + template render). This keeps header navigation
# feeling instant while still refreshing frequently.
_NAV_CACHE_PATHS = frozenset({
    "/briefing",
    "/invest-in-venezuela",
    "/sanctions-tracker",
    "/tools",
    "/explainers",
    "/calendar",
    "/travel",
    "/sources",
})
_NAV_PAGE_CACHE: dict[str, dict] = {}
_NAV_PAGE_CACHE_TTL_SECONDS = 90


def _get_report_html() -> str | None:
    """Return rendered report HTML from Supabase Storage (cached) or local disk."""
    if supabase_storage_read_enabled():
        now = time.time()
        if _REPORT_CACHE["html"] and now - _REPORT_CACHE["fetched_at"] < _REPORT_CACHE_TTL_SECONDS:
            return _REPORT_CACHE["html"]
        html = fetch_report_html()
        if html:
            _REPORT_CACHE["html"] = html
            _REPORT_CACHE["fetched_at"] = now
            return html
        if _REPORT_CACHE["html"]:
            return _REPORT_CACHE["html"]

    report = OUTPUT_DIR / "report.html"
    if report.exists():
        return report.read_text(encoding="utf-8")
    return None


def _normalize_cache_path(path: str) -> str:
    """Normalize `/foo/` and `/foo` to the same cache key."""
    if not path:
        return "/"
    normalized = path.rstrip("/")
    return normalized or "/"


@app.before_request
def _serve_nav_page_cache():
    """Return cached HTML for top-nav pages when still fresh."""
    if request.method != "GET":
        return None
    if request.query_string:
        return None
    path = _normalize_cache_path(request.path or "/")
    if path not in _NAV_CACHE_PATHS:
        return None
    cached = _NAV_PAGE_CACHE.get(path)
    if not cached:
        return None
    if time.time() - cached.get("cached_at", 0.0) > _NAV_PAGE_CACHE_TTL_SECONDS:
        return None
    response = Response(cached["body"], mimetype=cached.get("mimetype", "text/html"))
    response.headers["X-Page-Cache"] = "HIT"
    return response


@app.after_request
def _store_nav_page_cache(response: Response) -> Response:
    """Cache successful HTML responses for top-nav pages."""
    try:
        if request.method != "GET":
            return response
        if request.query_string:
            return response
        if response.status_code != 200:
            return response
        if response.mimetype != "text/html":
            return response

        path = _normalize_cache_path(request.path or "/")
        if path not in _NAV_CACHE_PATHS:
            return response

        _NAV_PAGE_CACHE[path] = {
            "body": response.get_data(),
            "mimetype": response.mimetype,
            "cached_at": time.time(),
        }
        response.headers["X-Page-Cache"] = "MISS"
    except Exception as exc:
        logger.warning("nav page cache skipped due to error: %s", exc)
    return response


@app.route("/")
def index():
    html = _get_report_html()
    if not html:
        abort(503, description="Report not yet generated. Run the daily pipeline first.")
    return Response(html, mimetype="text/html")


@app.post("/admin/regen-report")
def admin_regen_report():
    """
    Re-render the static homepage report.html in-place using current
    code + current DB content. Skips the scrape, LLM analysis, and
    newsletter phases of the daily pipeline — pure template render
    against existing data, ~1-5 seconds, $0 in API costs.

    Use case: deploying SEO-only changes (titles, meta descriptions,
    JSON-LD schema, template tweaks) and getting them live on the
    pre-rendered homepage without waiting for the next cron tick or
    paying for an unnecessary full pipeline run.

    Auth: bearer token via `?token=` query arg or `X-Admin-Token`
    header. Token must match settings.admin_token (env: ADMIN_TOKEN).
    Unset token → endpoint returns 503 (disabled).
    """
    if not settings.admin_token:
        return jsonify({"ok": False, "error": "ADMIN_TOKEN not configured"}), 503

    supplied = request.args.get("token") or request.headers.get("X-Admin-Token", "")
    if not hmac.compare_digest(supplied, settings.admin_token):
        return jsonify({"ok": False, "error": "Invalid token"}), 403

    try:
        from src.report_generator import generate_report
        from src.storage_remote import (
            supabase_storage_enabled,
            supabase_storage_read_enabled,
            upload_report_html,
        )

        t0 = time.time()
        out_path = generate_report()
        elapsed_ms = int((time.time() - t0) * 1000)

        # generate_report() will only push to Supabase if the WRITE side is
        # configured (URL + service key). On the web service the service
        # key is often absent (cron-only by design). Re-attempt the upload
        # explicitly here so we can surface the actual outcome to the
        # caller — silent skip is the worst possible failure mode for
        # this endpoint, since the web reads from Supabase first.
        upload_status: str
        if supabase_storage_enabled():
            try:
                fresh_html = out_path.read_text(encoding="utf-8")
                upload_report_html(fresh_html)
                upload_status = "uploaded"
            except Exception as upload_exc:
                logger.exception("admin: supabase upload failed")
                upload_status = f"failed: {upload_exc}"
        else:
            upload_status = "skipped: SUPABASE_SERVICE_KEY not set on this service"

        # Bust the in-memory cache so the next "/" request re-fetches
        # the freshly-uploaded HTML from Supabase Storage instead of
        # serving the stale cached copy for up to TTL seconds.
        _REPORT_CACHE["html"] = None
        _REPORT_CACHE["fetched_at"] = 0.0

        size = out_path.stat().st_size if out_path.exists() else 0
        logger.info(
            "admin: report regenerated (%d bytes, %d ms, supabase=%s)",
            size, elapsed_ms, upload_status,
        )
        return jsonify({
            "ok": True,
            "output_path": str(out_path),
            "bytes": size,
            "elapsed_ms": elapsed_ms,
            "supabase_write_enabled": supabase_storage_enabled(),
            "supabase_read_enabled": supabase_storage_read_enabled(),
            "supabase_upload": upload_status,
        })
    except Exception as exc:
        logger.exception("admin: regen-report failed")
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/subscribe", methods=["POST"])
def subscribe():
    data = request.get_json(silent=True) or {}
    email = data.get("email", "").strip()

    if not email or "@" not in email:
        return jsonify({"ok": False, "error": "Valid email required"}), 400

    api_key = settings.buttondown_api_key
    if not api_key:
        logger.error("BUTTONDOWN_API_KEY not configured")
        return jsonify({"ok": False, "error": "Newsletter signup is not configured"}), 503

    subscriber_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    if subscriber_ip and "," in subscriber_ip:
        subscriber_ip = subscriber_ip.split(",")[0].strip()

    try:
        resp = httpx.post(
            BUTTONDOWN_API_URL,
            json={
                "email_address": email,
                "type": "regular",
                "ip_address": subscriber_ip,
            },
            headers={
                "Authorization": f"Token {api_key}",
            },
            timeout=15,
        )

        if resp.status_code in (200, 201):
            logger.info("Buttondown subscriber added: %s", email)
            return jsonify({"ok": True})

        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        code = body.get("code", "")

        # Buttondown returns HTTP 400 with code=email_already_exists for
        # duplicate emails (not 409). Treat that as a successful
        # subscribe so the user UX is "you're in" either way.
        if resp.status_code == 409 or code == "email_already_exists":
            return jsonify({"ok": True, "note": "Already subscribed"})

        if code == "email_invalid":
            return jsonify({"ok": False, "error": "Please enter a valid email address"}), 400

        if code == "subscriber_blocked":
            logger.warning("Buttondown firewall blocked %s, retrying with bypass", email)
            resp2 = httpx.post(
                BUTTONDOWN_API_URL,
                json={"email_address": email, "type": "regular"},
                headers={
                    "Authorization": f"Token {api_key}",
                    "X-Buttondown-Bypass-Firewall": "true",
                },
                timeout=15,
            )
            body2 = resp2.json() if resp2.headers.get("content-type", "").startswith("application/json") else {}
            code2 = body2.get("code", "")
            if resp2.status_code in (200, 201):
                logger.info("Buttondown subscriber added (bypass): %s", email)
                return jsonify({"ok": True})
            if resp2.status_code == 409 or code2 == "email_already_exists":
                return jsonify({"ok": True, "note": "Already subscribed"})
            logger.error("Buttondown bypass also failed %d: %s", resp2.status_code, resp2.text)

        logger.error("Buttondown API error %d (code=%s): %s", resp.status_code, code, resp.text)
        return jsonify({"ok": False, "error": "Subscription failed, please try again"}), 502

    except Exception as e:
        logger.error("Buttondown request failed: %s", e)
        return jsonify({"ok": False, "error": "Service unavailable"}), 503


def _fetch_recent_briefings(limit: int = 5):
    """Pull the N most-recent BlogPost rows for "Latest analysis" rails.

    Used by tool pages and other steady-traffic surfaces to feed
    crawl signal into individual /briefing/<slug> pages. Returns an
    empty list on any DB hiccup so the caller can pass the result
    unconditionally to the template (the rail partial no-ops on empty).
    """
    try:
        from src.models import BlogPost, SessionLocal, init_db
        init_db()
        db = SessionLocal()
        try:
            return (
                db.query(BlogPost)
                .order_by(BlogPost.published_date.desc())
                .limit(limit)
                .all()
            )
        finally:
            db.close()
    except Exception as exc:
        logger.warning("recent_briefings fetch failed: %s", exc)
        return []


def _tool_seo_jsonld(*, slug: str, title: str, description: str, keywords: str, faq: list[dict] | None = None, dataset: dict | None = None):
    """Build standard SEO + JSON-LD payload for a /tools/* page."""
    from src.page_renderer import _base_url, _iso, settings as _s
    from datetime import datetime as _dt
    import json as _json

    base = _base_url()
    canonical = f"{base}/tools/{slug}"
    seo = {
        "title": title,
        "description": description,
        "keywords": keywords,
        "canonical": canonical,
        "site_name": _s.site_name,
        "site_url": base,
        "locale": _s.site_locale,
        "og_image": f"{base}/static/og-image.png?v=3",
        "og_type": "website",
        "published_iso": _iso(_dt.utcnow()),
        "modified_iso": _iso(_dt.utcnow()),
    }

    graph = [
        {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{base}/"},
                {"@type": "ListItem", "position": 2, "name": "Tools", "item": f"{base}/tools"},
                {"@type": "ListItem", "position": 3, "name": title, "item": canonical},
            ],
        },
        {
            "@type": "WebApplication",
            "@id": f"{canonical}#app",
            "name": title,
            "url": canonical,
            "description": description,
            "applicationCategory": "BusinessApplication",
            "operatingSystem": "Any (browser-based)",
            "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
            "publisher": {"@type": "Organization", "name": _s.site_name, "url": f"{base}/"},
        },
    ]
    if faq:
        graph.append({
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": q["q"],
                    "acceptedAnswer": {"@type": "Answer", "text": q["a"]},
                }
                for q in faq
            ],
        })
    if dataset:
        graph.append({"@type": "Dataset", "@id": f"{canonical}#dataset", **dataset})

    return seo, _json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False)


@app.route("/tools/caracas-safety-by-neighborhood")
@app.route("/tools/caracas-safety-by-neighborhood/")
def tool_caracas_safety():
    """Curated Caracas neighborhood safety reference."""
    try:
        from src.data.caracas_neighborhoods import list_caracas_neighborhoods
        from src.data.caracas_landmarks import list_caracas_landmarks
        from src.page_renderer import _env
        from datetime import date as _date

        neighborhoods = list_caracas_neighborhoods()
        landmarks = list_caracas_landmarks()

        seo, jsonld = _tool_seo_jsonld(
            slug="caracas-safety-by-neighborhood",
            title="Caracas Safety by Neighborhood — Investor & Traveller Guide",
            description=(
                "Caracas neighborhood safety scores for foreign investors and "
                "business travellers. 1–5 safety rating, business-use guidance, "
                "and risks to avoid for Las Mercedes, Altamira, Chacao, Petare, "
                "and other major Caracas districts."
            ),
            keywords="Caracas safety, safe neighborhoods Caracas, Las Mercedes Caracas, Altamira Caracas, Petare safety, Caracas business district, where to stay in Caracas",
            faq=[
                {
                    "q": "What is the safest neighborhood in Caracas for foreign business travellers?",
                    "a": "Las Mercedes, Altamira, La Castellana, and the wider Chacao municipality are the most operationally functional districts and host most foreign-investor meetings, embassies, banks, and business-class hotels.",
                },
                {
                    "q": "Are areas like Petare, Catia, or 23 de Enero safe to visit?",
                    "a": "No. These districts are not safe for foreign visitors at any time. Do not enter — including by metro or taxi pass-through.",
                },
                {
                    "q": "Is the Caracas airport road safe?",
                    "a": "The Maiquetía / Catia La Mar corridor between Simón Bolívar International Airport and Caracas carries elevated highway-robbery risk, particularly at night. Always pre-arrange a vetted driver and travel during daylight when possible.",
                },
            ],
        )

        template = _env.get_template("tools/safety_map.html.j2")
        html = template.render(
            neighborhoods=neighborhoods,
            landmarks=landmarks,
            seo=seo,
            jsonld=jsonld,
            current_year=_date.today().year,
            recent_briefings=_fetch_recent_briefings(),
        )
        return Response(html, mimetype="text/html")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("safety map render failed: %s", exc)
        abort(500)


@app.route("/tools/venezuela-visa-requirements")
@app.route("/tools/venezuela-visa-requirements/")
def tool_visa_requirements():
    """Venezuela visa & travel-advisory checker by passport country."""
    try:
        from src.data.visa_requirements import list_visa_requirements
        from src.models import (
            ExternalArticleEntry, SessionLocal, SourceType, init_db,
        )
        from src.page_renderer import _env
        from datetime import date as _date
        import copy as _copy

        # Start from the static curated list, then override the US row's
        # advisory level/summary with the most recent successful
        # TravelAdvisoryScraper result. This keeps the page in sync with
        # the live State Department advisory without manual edits.
        visas = [_copy.copy(v) for v in list_visa_requirements()]

        try:
            init_db()
            db = SessionLocal()
            try:
                latest = (
                    db.query(ExternalArticleEntry)
                    .filter(ExternalArticleEntry.source == SourceType.TRAVEL_ADVISORY)
                    .order_by(ExternalArticleEntry.published_date.desc())
                    .first()
                )
            finally:
                db.close()
        except Exception as exc:
            logger.warning("travel advisory live fetch failed, using static fallback: %s", exc)
            latest = None

        if latest is not None:
            meta = latest.extra_metadata or {}
            level = meta.get("level")
            level_text = (meta.get("level_text") or "").strip()
            level_label_map = {
                1: "Exercise Normal Precautions",
                2: "Exercise Increased Caution",
                3: "Reconsider Travel",
                4: "Do Not Travel",
            }
            if isinstance(level, int) and 1 <= level <= 4:
                label = level_text or level_label_map.get(level, "")
                advisory_summary = (
                    f"{label} — current US State Department designation "
                    f"(updated {latest.published_date.isoformat()}). "
                    "See the full advisory for region-specific Level 4 "
                    "designations and detailed risk indicators."
                )
                for v in visas:
                    if v.get("code") == "US":
                        v["advisory_level"] = level
                        v["advisory_summary"] = advisory_summary

        seo, jsonld = _tool_seo_jsonld(
            slug="venezuela-visa-requirements",
            title="Venezuela Visa Requirements & Travel Advisory by Country",
            description=(
                "Free Venezuela visa requirements checker. See whether you "
                "need a visa for Venezuela based on your passport country, "
                "the maximum stay, the current travel-advisory level, and "
                "what investors should know before booking a trip to Caracas."
            ),
            keywords="Venezuela visa, do I need a visa for Venezuela, Venezuela travel advisory, Venezuela tourist visa, Venezuela business visa, Caracas travel requirements",
            faq=[
                {
                    "q": "Do US citizens need a visa to travel to Venezuela?",
                    "a": "Yes. US citizens require a tourist (TR-V) or business (TR-N) visa issued in advance by the Venezuelan diplomatic mission — visas are not available on arrival. As of March 19, 2026 the US State Department rates Venezuela at travel advisory Level 3 (Reconsider Travel), with Level 4 (Do Not Travel) still applying to the Colombia border region and several specific states.",
                },
                {
                    "q": "Do UK and Canadian citizens need a visa to travel to Venezuela?",
                    "a": "No. Both UK and Canadian citizens can enter visa-free for tourist stays of up to 90 days. However, both governments currently advise against non-essential travel.",
                },
                {
                    "q": "Is Venezuela safe for business travel?",
                    "a": "Most Western governments rate Venezuela as a high-risk destination. Sophisticated investors typically conduct primary meetings in third-country jurisdictions (Bogotá, Panama, Madrid, Dubai) and use local counsel for in-country execution.",
                },
            ],
        )

        template = _env.get_template("tools/visa_requirements.html.j2")
        html = template.render(
            visas=visas,
            seo=seo,
            jsonld=jsonld,
            current_year=_date.today().year,
            recent_briefings=_fetch_recent_briefings(),
        )
        return Response(html, mimetype="text/html")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("visa tool render failed: %s", exc)
        abort(500)


@app.route("/tools/venezuela-investment-roi-calculator")
@app.route("/tools/venezuela-investment-roi-calculator/")
def tool_roi_calculator():
    """Sector ROI / IRR / NPV calculator with Venezuela risk premium overlays."""
    try:
        from src.page_renderer import _env
        from datetime import date as _date

        seo, jsonld = _tool_seo_jsonld(
            slug="venezuela-investment-roi-calculator",
            title="Venezuela Investment ROI Calculator — IRR, NPV, Cash Flow Tool",
            description=(
                "Free Venezuela investment ROI calculator. Estimate IRR, NPV, "
                "and multi-year cash flow for oil & gas, mining, real estate, "
                "banking, agriculture, telecom, and tourism — with sector-specific "
                "Venezuela risk premiums built in."
            ),
            keywords="Venezuela investment calculator, Venezuela IRR calculator, Venezuela NPV, Venezuela ROI, mining investment Venezuela, oil gas Venezuela ROI, sector risk premium Venezuela",
            faq=[
                {
                    "q": "How is the Venezuela risk premium calculated?",
                    "a": "Sector-specific premiums are anchored to traded Venezuelan sovereign-debt spreads (where available) and adjusted by sector based on sanctions exposure, foreign-investor dispute history, and FX repatriation friction. Defaults range from approximately 6% (tourism) to 12% (oil & gas).",
                },
                {
                    "q": "What's a reasonable discount rate for a Venezuelan investment?",
                    "a": "Most institutional investors use a USD-denominated WACC of 10-15% as the base, then add the sector-specific Venezuela risk premium of 6-12%, for an all-in discount rate of 16-27%.",
                },
                {
                    "q": "Is this calculator a substitute for a fully diligenced model?",
                    "a": "No. The calculator is a first-round filter that surfaces order-of-magnitude returns. A real investment decision requires a fully diligenced model with country-of-origin tax structure, FX repatriation friction, OFAC compliance overlay, and project-finance terms.",
                },
            ],
        )

        template = _env.get_template("tools/roi_calculator.html.j2")
        html = template.render(
            seo=seo,
            jsonld=jsonld,
            current_year=_date.today().year,
            recent_briefings=_fetch_recent_briefings(),
        )
        return Response(html, mimetype="text/html")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("ROI calculator render failed: %s", exc)
        abort(500)


@app.route("/tools/bolivar-usd-exchange-rate")
@app.route("/tools/bolivar-usd-exchange-rate/")
def tool_bolivar_usd():
    """Live BCV rate widget + free converter."""
    try:
        from src.models import ExternalArticleEntry, SessionLocal, SourceType, init_db
        from src.scraper.bcv import BCVScraper
        from src.page_renderer import _env
        from datetime import date as _date

        rate_usd: float | None = None
        rate_eur: float | None = None
        rate_date: str = ""

        init_db()
        db = SessionLocal()
        try:
            cached = (
                db.query(ExternalArticleEntry)
                .filter(ExternalArticleEntry.source == SourceType.BCV_RATES)
                .order_by(ExternalArticleEntry.published_date.desc())
                .first()
            )
            if cached and cached.extra_metadata:
                meta = cached.extra_metadata or {}
                rate_usd = meta.get("usd")
                rate_eur = meta.get("eur")
                rate_date = cached.published_date.isoformat()
        finally:
            db.close()

        if rate_usd is None:
            try:
                scraper = BCVScraper()
                result = scraper.scrape()
                if result.success and result.articles:
                    meta = result.articles[0].extra_metadata or {}
                    rate_usd = meta.get("usd")
                    rate_eur = meta.get("eur")
                    rate_date = _date.today().isoformat()
            except Exception as exc:
                logger.warning("live BCV scrape failed for tool: %s", exc)

        seo, jsonld = _tool_seo_jsonld(
            slug="bolivar-usd-exchange-rate",
            title=(
                f"Bolívar to USD Exchange Rate Today — Bs. {rate_usd:.4f}/US$1"
                if rate_usd else
                "Venezuelan Bolívar to USD Exchange Rate — Live BCV Rate"
            ),
            description=(
                f"Today's official Banco Central de Venezuela USD/VES rate is "
                f"Bs. {rate_usd:.4f} per US$1. Free Bolivar/Dollar converter, "
                f"Euro cross-rate, and analysis of why the parallel rate diverges."
                if rate_usd else
                "Live Banco Central de Venezuela USD/VES exchange rate, free "
                "Bolivar/Dollar converter, Euro cross-rate, and parallel-market context."
            ),
            keywords="bolivar to dollar, BCV exchange rate, VES USD, Venezuelan bolivar exchange rate, dolar BCV, bolivar converter",
            faq=[
                {
                    "q": "What is the current official Venezuelan Bolívar to US Dollar rate?",
                    "a": (
                        f"The official Banco Central de Venezuela (BCV) rate is currently Bs. "
                        f"{rate_usd:.4f} per US$1 as of {rate_date}."
                        if rate_usd else
                        "The official rate is published daily by the Banco Central de Venezuela on bcv.org.ve. The live value is displayed at the top of this page when the BCV homepage is reachable."
                    ),
                },
                {
                    "q": "Why does the parallel exchange rate differ from the BCV rate?",
                    "a": "Venezuela operates under a managed float. The official BCV rate is used for taxes, customs, and public-sector transactions, while a parallel rate emerges from informal trading. Divergence widens in periods of currency stress and reflects unmet hard-currency demand.",
                },
                {
                    "q": "Can foreign investors freely convert bolívars to USD?",
                    "a": "Capital repatriation in foreign currency requires registration with the BCV and approval against the prevailing exchange-control regulations. FX availability remains the single largest operational risk for foreign investors.",
                },
            ],
        )

        template = _env.get_template("tools/bolivar_usd.html.j2")
        html = template.render(
            rate_usd=rate_usd,
            rate_eur=rate_eur,
            rate_date=rate_date or _date.today().isoformat(),
            seo=seo,
            jsonld=jsonld,
            current_year=_date.today().year,
            recent_briefings=_fetch_recent_briefings(),
        )
        return Response(html, mimetype="text/html")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("bolivar tool render failed: %s", exc)
        abort(500)


@app.route("/tools/ofac-venezuela-sanctions-checker")
@app.route("/tools/ofac-venezuela-sanctions-checker/")
def tool_ofac_sanctions_checker():
    """Search the cached OFAC SDN data for fuzzy matches against a query."""
    try:
        from src.models import ExternalArticleEntry, SessionLocal, SourceType, init_db
        from src.page_renderer import _env
        from datetime import date as _date
        from difflib import SequenceMatcher
        import re as _re

        query = (request.args.get("q") or "").strip()
        matches: list[dict] = []
        total_sdn = 0

        init_db()
        db = SessionLocal()
        try:
            rows = (
                db.query(ExternalArticleEntry)
                .filter(ExternalArticleEntry.source == SourceType.OFAC_SDN)
                .all()
            )
            total_sdn = len(rows)

            if query:
                q_low = query.lower()
                q_norm = _re.sub(r"[^a-z0-9]+", "", q_low)

                for r in rows:
                    meta = r.extra_metadata or {}
                    name = (meta.get("name") or r.headline or "").strip()
                    program = (meta.get("program") or "").strip()
                    remarks = (meta.get("remarks") or "").strip()
                    ent_type = (meta.get("type") or "entity").lower()

                    haystack = " ".join([name, program, remarks]).lower()
                    haystack_norm = _re.sub(r"[^a-z0-9]+", "", haystack)

                    score = 0.0
                    if q_low in haystack:
                        score = max(score, 0.95)
                    elif q_norm and q_norm in haystack_norm:
                        score = max(score, 0.85)
                    else:
                        ratio = SequenceMatcher(None, q_low, name.lower()).ratio()
                        if ratio >= 0.7:
                            score = max(score, ratio)

                    if score >= 0.7:
                        matches.append({
                            "name": name,
                            "type": ent_type,
                            "program": program,
                            "remarks": remarks,
                            "score": int(round(score * 100)),
                        })

                matches.sort(key=lambda m: m["score"], reverse=True)
                matches = matches[:30]
        finally:
            db.close()

        seo, jsonld = _tool_seo_jsonld(
            slug="ofac-venezuela-sanctions-checker",
            title="OFAC Venezuela Sanctions Exposure Checker — Free Screening Tool",
            description=(
                f"Free OFAC sanctions screening tool: check any name, company, "
                f"vessel IMO, aircraft tail number, or Venezuelan cédula against "
                f"all {total_sdn} active Venezuela-related SDN designations."
            ),
            keywords="OFAC sanctions checker Venezuela, SDN screening, PDVSA sanctions check, Venezuela sanctions compliance, OFAC fuzzy match",
            faq=[
                {
                    "q": "How accurate is this OFAC sanctions check?",
                    "a": "This tool uses fuzzy matching against the OFAC SDN list filtered for Venezuela-related programs. It surfaces likely matches but does not perform full ownership-chain analysis (OFAC 50% Rule) or check non-SDN sectoral lists. Always verify with the official OFAC source and consider qualified sanctions counsel for high-stakes counterparties.",
                },
                {
                    "q": "What data is checked?",
                    "a": f"All {total_sdn} entries on the OFAC consolidated SDN list filtered for Venezuela programs (VENEZUELA, VENEZUELA-EO13850, VENEZUELA-EO13884), refreshed twice daily. The tool searches names, aliases, IMO numbers, aircraft tail numbers, Venezuelan cédulas, and SDN remarks fields.",
                },
                {
                    "q": "Is this tool free?",
                    "a": "Yes. The OFAC sanctions exposure checker is completely free to use, with no registration required.",
                },
            ],
        )

        from src.seo.cluster_topology import build_cluster_ctx
        cluster_ctx = build_cluster_ctx("/tools/ofac-venezuela-sanctions-checker")

        template = _env.get_template("tools/ofac_sanctions_checker.html.j2")
        html = template.render(
            query=query,
            matches=matches,
            total_sdn=total_sdn,
            seo=seo,
            jsonld=jsonld,
            cluster_ctx=cluster_ctx,
            current_year=_date.today().year,
            recent_briefings=_fetch_recent_briefings(),
        )
        return Response(html, mimetype="text/html")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("sanctions checker render failed: %s", exc)
        abort(500)


@app.route("/tools/ofac-venezuela-general-licenses")
@app.route("/tools/ofac-venezuela-general-licenses/")
def tool_ofac_general_licenses():
    """Searchable lookup of OFAC Venezuela general licenses."""
    try:
        from src.data.ofac_general_licenses import list_general_licenses
        from src.page_renderer import _env
        from datetime import date as _date

        licenses = list_general_licenses()

        seo, jsonld = _tool_seo_jsonld(
            slug="ofac-venezuela-general-licenses",
            title="OFAC Venezuela General License Lookup — Free Compliance Tool",
            description=(
                "Free searchable directory of the active OFAC general licenses "
                "authorising transactions involving PdVSA, Chevron, CITGO, "
                "Venezuelan sovereign debt, and Venezuelan gold-sector entities. "
                "Updated whenever OFAC publishes new actions."
            ),
            keywords="OFAC general license, GL 5T Venezuela, GL 8M PDVSA, GL 41 Chevron Venezuela, GL 44A oil, OFAC Venezuela compliance",
            faq=[
                {
                    "q": "What is an OFAC general license?",
                    "a": "An OFAC general license is a published authorisation that permits a defined category of transaction that would otherwise be prohibited by US sanctions, without each party having to apply for an individual specific license.",
                },
                {
                    "q": "Which OFAC general license covers Chevron's Venezuelan operations?",
                    "a": "General License 41 authorises Chevron Corporation to lift, sell, and import Venezuelan-origin crude oil and petroleum products into the United States subject to specific conditions, including no payment of taxes or royalties to the Government of Venezuela.",
                },
                {
                    "q": "Are OFAC general licenses permanent?",
                    "a": "No. Most Venezuela-related general licenses are subject to periodic renewal, modification, or revocation by OFAC. Always confirm the current text and expiration on the OFAC website before relying on a general license.",
                },
            ],
        )

        from src.seo.cluster_topology import build_cluster_ctx
        cluster_ctx = build_cluster_ctx("/tools/ofac-venezuela-general-licenses")

        template = _env.get_template("tools/ofac_general_licenses.html.j2")
        html = template.render(
            licenses=licenses,
            seo=seo,
            jsonld=jsonld,
            cluster_ctx=cluster_ctx,
            current_year=_date.today().year,
            recent_briefings=_fetch_recent_briefings(),
        )
        return Response(html, mimetype="text/html")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("tool render failed: %s", exc)
        abort(500)


@app.route("/tools/sec-edgar-venezuela-impairment-search")
@app.route("/tools/sec-edgar-venezuela-impairment-search/")
def tool_sec_edgar_venezuela_search():
    """Pre-canned SEC EDGAR full-text search presets for Venezuela /
    PDVSA / impairment / contingent-liability research, plus a curated
    quick-jump table of S&P 500 companies known to disclose Venezuela
    items in their filings.

    This route is a wrapper around two existing surfaces:
      - The preset deeplinks live entirely client-side: each card opens
        a pre-filled efts.sec.gov search in a new tab. We do not make
        the EDGAR call from our server here — that's what the per-
        company /companies/<slug>/venezuela-exposure pages do.
      - The curated table is sourced from
        src/data/curated_venezuela_exposure.py (single source of truth
        for any "known disclosers" list across the site).
    """
    try:
        from src.data.edgar_search_presets import list_presets, list_curated_disclosers
        from src.page_renderer import _env
        from datetime import date as _date

        presets = list_presets()
        disclosers = list_curated_disclosers(max_n=30)
        today_human = _date.today().strftime("%B %Y")

        faq = [
            {
                "q": "Which S&P 500 companies disclose Venezuela exposure to the SEC?",
                "a": (
                    "As of " + today_human + ", Chevron (CVX) is the most operationally Venezuela-"
                    "exposed S&P 500 company through its OFAC GL 41-authorised PDVSA joint "
                    "ventures. Halliburton (HAL), Schlumberger (SLB), and Baker Hughes (BKR) "
                    "all disclose historical write-downs and residual exposure. ConocoPhillips "
                    "(COP) and ExxonMobil (XOM) carry contingent assets from ICSID arbitration. "
                    "Use the curated table on this page for the full list of S&P 500 disclosers."
                ),
            },
            {
                "q": "How do I search SEC EDGAR for Venezuela-related disclosures?",
                "a": (
                    "Open https://efts.sec.gov/LATEST/search-index/ and enter a query like "
                    "'\"Venezuela\" OR \"PdVSA\"' constrained to forms 10-K, 20-F, 10-Q, and 8-K "
                    "over a 24-month window. The seven preset cards on this page each open EDGAR "
                    "with that work already done — including impairment, contingent-liability, "
                    "OFAC compliance, and CITGO collateral queries."
                ),
            },
            {
                "q": "Why combine Venezuela, impairment, and contingent-liability search terms?",
                "a": (
                    "Venezuela exposure rarely shows up as a standalone disclosure. Most S&P 500 "
                    "companies that operated in Venezuela during the 2015-2020 expropriation cycle "
                    "now reference it indirectly — via impairment charges (write-downs of plant "
                    "and equipment), deconsolidation footnotes, or contingent liabilities for "
                    "ongoing ICSID arbitration. Searching for those terms alongside 'Venezuela' "
                    "or 'PDVSA' is the most reliable way to find substantive disclosure."
                ),
            },
        ]

        seo, jsonld = _tool_seo_jsonld(
            slug="sec-edgar-venezuela-impairment-search",
            title=(
                "SEC EDGAR Venezuela / PDVSA / Impairment Search — "
                f"S&P 500 Disclosures ({today_human})"
            ),
            description=(
                "Free, pre-canned SEC EDGAR full-text search for Venezuela, PDVSA, "
                "CITGO, impairment, and contingent-liability disclosures across "
                "S&P 500 10-K, 20-F, 10-Q, and 8-K filings. Includes a curated "
                f"table of S&P 500 companies known to disclose Venezuela items, "
                f"updated {today_human}."
            ),
            keywords=(
                "sec edgar venezuela, sec edgar pdvsa, venezuela impairment search, "
                "venezuela contingent liability, citgo edgar search, "
                "sec filings venezuela exposure, ofac venezuela 10-K, "
                "sp500 venezuela disclosures"
            ),
            faq=faq,
        )

        from src.seo.cluster_topology import build_cluster_ctx
        cluster_ctx = build_cluster_ctx("/tools/sec-edgar-venezuela-impairment-search")

        template = _env.get_template("tools/sec_edgar_venezuela_search.html.j2")
        html = template.render(
            presets=presets,
            disclosers=disclosers,
            faq=faq,
            seo=seo,
            jsonld=jsonld,
            cluster_ctx=cluster_ctx,
            current_year=_date.today().year,
            today_human=today_human,
            recent_briefings=_fetch_recent_briefings(),
        )
        return Response(html, mimetype="text/html")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("tool render failed: %s", exc)
        abort(500)


@app.route("/tools")
@app.route("/tools/")
def tools_index():
    """Index of all free Venezuela investor tools."""
    try:
        from src.page_renderer import _env, _base_url, _iso, settings as _s
        from datetime import date as _date, datetime as _dt
        import json as _json

        tools = [
            {
                "url": "/travel/emergency-card",
                "name": "Caracas Emergency Card — Printable Pocket Sheet",
                "category": "Travel",
                "summary": "Print a single A4 sheet for your passport: bilingual hospital and embassy addresses a taxi driver can read, big phone numbers a stranger can dial, your blood type and home contact, and a throwaway pre-departure checklist. Pick your embassy and the card auto-personalizes.",
            },
            {
                "url": "/tools/ofac-venezuela-sanctions-checker",
                "name": "OFAC Venezuela Sanctions Exposure Checker",
                "category": "Compliance",
                "summary": "Search any name, company, vessel IMO, aircraft tail number, or Venezuelan cédula against every active Venezuela-related OFAC SDN designation, with fuzzy matching and a clean compliance disclaimer.",
            },
            {
                "url": "/tools/public-company-venezuela-exposure-check",
                "name": "Public Company Venezuela Exposure Check",
                "category": "Compliance",
                "summary": "Type any S&P 500 company name or ticker — instantly see whether the company has Venezuela exposure on the OFAC SDN list, in its recent SEC filings, or in our Federal Register / news corpus. Backed by 500+ per-ticker landing pages.",
            },
            {
                "url": "/tools/sec-edgar-venezuela-impairment-search",
                "name": "SEC EDGAR Venezuela / PDVSA / Impairment Search",
                "category": "Compliance",
                "summary": "Pre-canned SEC EDGAR full-text searches for Venezuela, PDVSA, CITGO, impairment, contingent-liability, and OFAC sanctions disclosures across 10-K, 20-F, 10-Q, and 8-K filings — plus a curated quick-jump table of S&P 500 companies known to disclose Venezuela items.",
            },
            {
                "url": "/tools/ofac-venezuela-general-licenses",
                "name": "OFAC Venezuela General License Lookup",
                "category": "Compliance",
                "summary": "Searchable directory of the active OFAC general licenses authorising transactions involving PdVSA, Chevron, CITGO, Venezuelan sovereign debt, and gold-sector entities.",
            },
            {
                "url": "/tools/bolivar-usd-exchange-rate",
                "name": "Bolívar / USD Exchange Rate & Converter",
                "category": "Markets",
                "summary": "Live BCV USD/VES rate, EUR cross-rate, and a free converter pulled from the Banco Central de Venezuela homepage. Falls back to cached values when the BCV site is unreachable.",
            },
            {
                "url": "/tools/venezuela-investment-roi-calculator",
                "name": "Venezuela Investment ROI Calculator",
                "category": "Modelling",
                "summary": "Estimate IRR, NPV, and multi-year cash flow across oil & gas, mining, real estate, banking, agriculture, telecom, and tourism — with sector-specific Venezuela risk premiums baked in.",
            },
            {
                "url": "/tools/caracas-safety-by-neighborhood",
                "name": "Caracas Safety Score by Neighborhood",
                "category": "Travel",
                "summary": "Interactive map of Caracas with a curated 1–5 safety rating for every major neighborhood (Las Mercedes, Altamira, Petare, Catia, and more), plus toggleable overlays for embassies, hospitals, police, and the international airport — with business-use guidance and specific risks to avoid.",
            },
            {
                "url": "/tools/venezuela-visa-requirements",
                "name": "Venezuela Visa & Travel Requirements",
                "category": "Travel",
                "summary": "Pick your passport country to see whether you need a visa for Venezuela, the maximum stay, the current US/UK travel-advisory level, and what investors should know before flying.",
            },
        ]

        base = _base_url()
        canonical = f"{base}/tools"
        seo = {
            "title": "Free Venezuela Investor Tools — Sanctions, BCV, ROI Calculator",
            "description": "Free toolkit for evaluating Venezuelan exposure: OFAC sanctions screening, OFAC general license lookup, live BCV USD rate, sector ROI calculator, Caracas safety map, and visa requirements.",
            "keywords": "Venezuela investor tools, OFAC checker, BCV rate, Venezuela ROI calculator, Caracas safety, Venezuela visa",
            "canonical": canonical,
            "site_name": _s.site_name,
            "site_url": base,
            "locale": _s.site_locale,
            "og_image": f"{base}/static/og-image.png?v=3",
            "og_type": "website",
            "published_iso": _iso(_dt.utcnow()),
            "modified_iso": _iso(_dt.utcnow()),
        }
        jsonld = _json.dumps({
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "BreadcrumbList",
                    "itemListElement": [
                        {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{base}/"},
                        {"@type": "ListItem", "position": 2, "name": "Tools", "item": canonical},
                    ],
                },
                {
                    "@type": "ItemList",
                    "@id": f"{canonical}#tools",
                    "name": "Free Venezuela Investor Tools",
                    "itemListElement": [
                        {
                            "@type": "ListItem",
                            "position": i + 1,
                            "url": f"{base}{t['url']}",
                            "name": t["name"],
                        }
                        for i, t in enumerate(tools)
                    ],
                },
            ],
        }, ensure_ascii=False)

        template = _env.get_template("tools_index.html.j2")
        html = template.render(
            tools=tools,
            seo=seo,
            jsonld=jsonld,
            current_year=_date.today().year,
            recent_briefings=_fetch_recent_briefings(),
        )
        return Response(html, mimetype="text/html")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("tools index render failed: %s", exc)
        abort(500)


@app.route("/explainers")
@app.route("/explainers/")
def explainers_index():
    """Index of evergreen explainers."""
    try:
        from src.models import LandingPage, SessionLocal, init_db
        from src.page_renderer import _env, _base_url, _iso, settings as _s
        from datetime import date as _date, datetime as _dt
        import json as _json

        init_db()
        db = SessionLocal()
        try:
            explainers = (
                db.query(LandingPage)
                .filter(LandingPage.page_type == "explainer")
                .order_by(LandingPage.last_generated_at.desc())
                .all()
            )
        finally:
            db.close()

        base = _base_url()
        canonical = f"{base}/explainers"
        seo = {
            "title": "Venezuela Investor Explainers — Plain-English Guides",
            "description": "Evergreen plain-English explainers covering OFAC sanctions on Venezuela, the Banco Central de Venezuela (BCV), the bolívar, how to buy Venezuelan bonds, and doing business in Caracas.",
            "keywords": "Venezuela explainer, OFAC Venezuela explained, BCV explained, bolivar history, Venezuelan bonds, doing business in Caracas",
            "canonical": canonical,
            "site_name": _s.site_name,
            "site_url": base,
            "locale": _s.site_locale,
            "og_image": f"{base}/static/og-image.png?v=3",
            "og_type": "website",
            "published_iso": _iso(_dt.utcnow()),
            "modified_iso": _iso(_dt.utcnow()),
        }
        jsonld = _json.dumps({
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "BreadcrumbList",
                    "itemListElement": [
                        {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{base}/"},
                        {"@type": "ListItem", "position": 2, "name": "Explainers", "item": canonical},
                    ],
                },
                {
                    "@type": "ItemList",
                    "@id": f"{canonical}#list",
                    "name": "Venezuela Investor Explainers",
                    "itemListElement": [
                        {
                            "@type": "ListItem",
                            "position": i + 1,
                            "url": f"{base}{e.canonical_path}",
                            "name": e.title,
                        }
                        for i, e in enumerate(explainers)
                    ],
                },
            ],
        }, ensure_ascii=False)

        template = _env.get_template("explainers_index.html.j2")
        html = template.render(explainers=explainers, seo=seo, jsonld=jsonld, current_year=_date.today().year)
        return Response(html, mimetype="text/html")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("explainers index render failed: %s", exc)
        abort(500)


@app.route("/explainers/<slug>")
def explainer_page(slug: str):
    """Evergreen explainer landing page."""
    try:
        from src.models import BlogPost, LandingPage, SessionLocal, init_db
        from src.page_renderer import render_landing_page

        init_db()
        db = SessionLocal()
        try:
            page = (
                db.query(LandingPage)
                .filter(LandingPage.page_key == f"explainer:{slug}")
                .first()
            )
            if not page:
                abort(404)
            recent = (
                db.query(BlogPost)
                .order_by(BlogPost.published_date.desc())
                .limit(6)
                .all()
            )
            html = render_landing_page(page, recent_briefings=recent)
            return Response(html, mimetype="text/html")
        finally:
            db.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("explainer page render failed for slug=%s: %s", slug, exc)
        abort(500)


@app.route("/sources")
@app.route("/sources/")
def sources_page():
    """Methodology + primary sources we monitor — authority signal page."""
    try:
        from src.models import (
            AssemblyNewsEntry,
            ExternalArticleEntry,
            GazetteEntry,
            SessionLocal,
            SourceType,
            init_db,
        )
        from src.page_renderer import _env, _base_url, _iso, settings as _s
        from datetime import date as _date, datetime as _dt
        import json as _json

        init_db()
        db = SessionLocal()
        try:
            def _count_ext(src: SourceType) -> int:
                try:
                    return db.query(ExternalArticleEntry).filter(ExternalArticleEntry.source == src).count()
                except Exception:
                    return 0

            sources = [
                {
                    "name": "OFAC Specially Designated Nationals (SDN) list",
                    "kind": "US Treasury", "tier": "Primary",
                    "url": "https://www.treasury.gov/ofac/downloads/sdn.csv",
                    "description": "The complete US Treasury OFAC consolidated SDN list, filtered for Venezuela-related programs (VENEZUELA, VENEZUELA-EO13850, VENEZUELA-EO13884). Tracks every individual, entity, vessel, and aircraft sanctioned in connection with Venezuela.",
                    "cadence": "Twice daily (10am, 5pm)",
                    "entries_count": _count_ext(SourceType.OFAC_SDN),
                },
                {
                    "name": "US Federal Register — Venezuela",
                    "kind": "US Government", "tier": "Primary",
                    "url": "https://www.federalregister.gov/documents/search?conditions[term]=venezuela",
                    "description": "Final rules, proposed rules, executive orders, and notices published by federal agencies. Source of truth for OFAC general licenses, sanctions actions, and trade rule changes.",
                    "cadence": "Twice daily",
                    "entries_count": _count_ext(SourceType.FEDERAL_REGISTER),
                },
                {
                    "name": "Asamblea Nacional de Venezuela",
                    "kind": "Venezuelan Government", "tier": "Primary",
                    "url": "https://www.asambleanacional.gob.ve",
                    "description": "Official news feed of the Venezuelan National Assembly: bills introduced, laws passed, committee work, and parliamentary diplomacy. Translated into English by our analyzer.",
                    "cadence": "Twice daily",
                    "entries_count": db.query(AssemblyNewsEntry).count(),
                },
                {
                    "name": "Gaceta Oficial de la República Bolivariana de Venezuela",
                    "kind": "Venezuelan Government", "tier": "Primary",
                    "url": "https://tugacetaoficial.com",
                    "description": "The official gazette publishing every Venezuelan law, decree, and government resolution. We OCR scanned PDFs and persist the underlying text so each item is searchable and analyzable.",
                    "cadence": "Twice daily",
                    "entries_count": db.query(GazetteEntry).count(),
                },
                {
                    "name": "Banco Central de Venezuela (BCV)",
                    "kind": "Venezuelan Government", "tier": "Primary",
                    "url": "https://www.bcv.org.ve",
                    "description": "Official daily exchange rate of the bolivar against the US dollar, plus monetary policy announcements. Used as a baseline for all Venezuela-USD conversions on this site.",
                    "cadence": "Daily",
                    "entries_count": None,
                },
                {
                    "name": "US State Department — Venezuela travel advisory",
                    "kind": "US Government", "tier": "Primary",
                    "url": "https://travel.state.gov/content/travel/en/traveladvisories/traveladvisories/venezuela-travel-advisory.html",
                    "description": "Official US State Department travel advisory level for Venezuela. Used in the security and operating-environment sections of the pillar guide and travel-related tools.",
                    "cadence": "Daily check, alerts on level change",
                    "entries_count": None,
                },
                {
                    "name": "GDELT Project (global event database)",
                    "kind": "Open data", "tier": "Secondary",
                    "url": "https://www.gdeltproject.org",
                    "description": "Global news event database used as a tone signal — we use the GDELT V2 GKG tone score as one of the inputs that decides which items get the more expensive LLM analysis treatment.",
                    "cadence": "Twice daily",
                    "entries_count": _count_ext(SourceType.GDELT),
                },
            ]

            base = _base_url()
            canonical = f"{base}/sources"
            seo = {
                "title": "Sources & Methodology — Caracas Research",
                "description": (
                    "How Caracas Research produces its investor briefings: "
                    "primary Venezuelan and US government sources we monitor, refresh "
                    "cadence, LLM filtering pipeline, and editorial standards."
                ),
                "keywords": "Venezuela investment sources, OFAC monitoring, Asamblea Nacional, Gaceta Oficial, BCV, methodology",
                "canonical": canonical,
                "site_name": _s.site_name,
                "site_url": base,
                "locale": _s.site_locale,
                "og_image": f"{base}/static/og-image.png?v=3",
                "og_type": "website",
                "published_iso": _iso(_dt.utcnow()),
                "modified_iso": _iso(_dt.utcnow()),
            }
            jsonld = _json.dumps({
                "@context": "https://schema.org",
                "@graph": [
                    {
                        "@type": "BreadcrumbList",
                        "itemListElement": [
                            {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{base}/"},
                            {"@type": "ListItem", "position": 2, "name": "Sources & Methodology", "item": canonical},
                        ],
                    },
                    {
                        "@type": "AboutPage",
                        "@id": f"{canonical}#about",
                        "url": canonical,
                        "name": seo["title"],
                        "description": seo["description"],
                        "publisher": {"@type": "Organization", "name": _s.site_name, "url": f"{base}/"},
                    },
                ],
            }, ensure_ascii=False)

            template = _env.get_template("sources.html.j2")
            html = template.render(
                sources=sources,
                seo=seo,
                jsonld=jsonld,
                current_year=_date.today().year,
            )
            return Response(html, mimetype="text/html")
        finally:
            db.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("sources page render failed: %s", exc)
        abort(500)


@app.route("/sanctions-tracker")
@app.route("/sanctions-tracker/")
def sanctions_tracker():
    """OFAC SDN tracker — searchable / filterable table of all designations."""
    try:
        from src.models import ExternalArticleEntry, ScrapeLog, SessionLocal, SourceType, init_db
        from src.page_renderer import _env, _base_url, _iso, settings as _s
        from datetime import date as _date, datetime as _dt, timedelta as _td, timezone as _tz
        import json as _json

        init_db()
        db = SessionLocal()
        try:
            rows = (
                db.query(ExternalArticleEntry)
                .filter(ExternalArticleEntry.source == SourceType.OFAC_SDN)
                .order_by(ExternalArticleEntry.published_date.desc())
                .all()
            )

            # Pull the most recent successful OFAC SDN scrape so we can show
            # the user a verifiable "last refreshed" timestamp instead of just
            # claiming "updated daily" with no proof.
            last_scrape_row = (
                db.query(ScrapeLog)
                .filter(
                    ScrapeLog.source == SourceType.OFAC_SDN,
                    ScrapeLog.success.is_(True),
                    ScrapeLog.entries_found > 0,
                )
                .order_by(ScrapeLog.created_at.desc())
                .first()
            )

            # Render cron schedule: "0 15,22 * * *" UTC → 10:00 and 17:00 in
            # America/Bogota (UTC-5, same as Medellín). Compute the next slot
            # so the page tells the reader exactly when the data refreshes next.
            now_utc = _dt.now(_tz.utc)
            cron_hours_utc = (15, 22)
            next_run_utc = None
            for hh in cron_hours_utc:
                candidate = now_utc.replace(hour=hh, minute=0, second=0, microsecond=0)
                if candidate > now_utc:
                    next_run_utc = candidate
                    break
            if next_run_utc is None:
                next_run_utc = (now_utc + _td(days=1)).replace(
                    hour=cron_hours_utc[0], minute=0, second=0, microsecond=0
                )

            medellin = _tz(_td(hours=-5))  # America/Bogota / Medellín, no DST
            last_refreshed_local = None
            last_refreshed_relative = None
            if last_scrape_row and last_scrape_row.created_at is not None:
                # ScrapeLog.created_at is stored naive (UTC); pin it to UTC.
                last_utc = last_scrape_row.created_at.replace(tzinfo=_tz.utc)
                last_local = last_utc.astimezone(medellin)
                last_refreshed_local = last_local.strftime("%b %d, %Y · %-I:%M %p") + " (Medellín)"
                delta = now_utc - last_utc
                hours = int(delta.total_seconds() // 3600)
                minutes = int((delta.total_seconds() % 3600) // 60)
                if hours >= 24:
                    last_refreshed_relative = f"{hours // 24}d ago"
                elif hours >= 1:
                    last_refreshed_relative = f"{hours}h ago"
                else:
                    last_refreshed_relative = f"{max(minutes, 1)}m ago"

            next_refresh_local = next_run_utc.astimezone(medellin).strftime(
                "%b %d · %-I:%M %p"
            ) + " (Medellín)"

            sdn_entries = []
            stats = {
                "total": 0, "individuals": 0, "entities": 0,
                "vessels": 0, "aircraft": 0,
            }
            for r in rows:
                meta = r.extra_metadata or {}
                ent_type = (meta.get("type") or "").lower()
                if ent_type not in ("individual", "vessel", "aircraft", "entity"):
                    ent_type = "entity"
                sdn_entries.append({
                    "name": meta.get("name") or r.headline,
                    "type": ent_type,
                    "program": meta.get("program") or "",
                    "remarks": meta.get("remarks") or "",
                })
                stats["total"] += 1
                stats[
                    "individuals" if ent_type == "individual"
                    else "vessels" if ent_type == "vessel"
                    else "aircraft" if ent_type == "aircraft"
                    else "entities"
                ] += 1

            base = _base_url()
            canonical = f"{base}/sanctions-tracker"
            today_human = _date.today().strftime("%B %Y")
            # Title trimmed April 2026 (round 2) after GSC showed 183
            # impressions / 2 clicks (1.1% CTR) at avg position 6.8.
            # Round-1 title (96 chars: "OFAC SDN List — Venezuela
            # Sanctions: N Current Military, Economic & Diplomatic
            # Designations") truncated badly in SERPs, killing the
            # month-year freshness signal at the end. Round-2 keeps
            # the high-value tokens ("OFAC", "Venezuela SDN List",
            # count, "US Sanctions", current month-year) inside the
            # ~60-char SERP display window. Sector words ("military,
            # economic, diplomatic") move to the description, where
            # Google still matches them but they don't burn title
            # budget. "US Sanctions" carries the US-authority signal
            # that GSC shows we're missing — 356 US impressions / 0
            # US clicks over 3 months suggests US compliance officers
            # don't recognise us as a US-Treasury-grade source.
            seo = {
                "title": (
                    f"OFAC Venezuela SDN List — {stats['total']} Active "
                    f"US Sanctions ({today_human})"
                ),
                "description": (
                    f"Official US Treasury OFAC SDN list for Venezuela — "
                    f"{stats['total']} active sanctions across military, "
                    "economic, and diplomatic programs. Refreshed twice daily."
                ),
                "keywords": (
                    "OFAC SDN list Venezuela, OFAC Venezuela sanctions, "
                    "US Treasury Venezuela sanctions, Venezuela military "
                    "sanctions, Venezuela economic sanctions, Venezuela "
                    "diplomatic designations, PDVSA sanctions, Venezuela "
                    "vessel sanctions, OFAC SDN search current"
                ),
                "canonical": canonical,
                "site_name": _s.site_name,
                "site_url": base,
                "locale": _s.site_locale,
                "og_image": f"{base}/static/og-image.png?v=3",
                "og_type": "website",
                "published_iso": _iso(_dt.utcnow()),
                "modified_iso": _iso(_dt.utcnow()),
            }

            jsonld = _json.dumps({
                "@context": "https://schema.org",
                "@graph": [
                    {
                        "@type": "BreadcrumbList",
                        "itemListElement": [
                            {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{base}/"},
                            {"@type": "ListItem", "position": 2, "name": "Invest in Venezuela", "item": f"{base}/invest-in-venezuela"},
                            {"@type": "ListItem", "position": 3, "name": "OFAC Sanctions Tracker", "item": canonical},
                        ],
                    },
                    {
                        "@type": "Dataset",
                        "@id": f"{canonical}#dataset",
                        "name": "OFAC Venezuela SDN Tracker",
                        "description": seo["description"],
                        "url": canonical,
                        "creator": {"@type": "Organization", "name": _s.site_name, "url": f"{base}/"},
                        "license": "https://www.usa.gov/government-works",
                        "isAccessibleForFree": True,
                        "variableMeasured": ["name", "type", "program", "remarks"],
                        "distribution": [{
                            "@type": "DataDownload",
                            "encodingFormat": "text/csv",
                            "contentUrl": "https://www.treasury.gov/ofac/downloads/sdn.csv",
                        }],
                    },
                ],
            }, ensure_ascii=False)

            from src.seo.cluster_topology import build_cluster_ctx
            cluster_ctx = build_cluster_ctx("/sanctions-tracker")

            # Sector-pivot counts power the "Browse by sector" callout
            # on the tracker page. Loaded lazily so a brief sdn_profiles
            # cache miss doesn't 500 the tracker — fall back to None and
            # the callout renders as empty (the template guards on it).
            try:
                from src.data.sdn_profiles import sector_stats as _sector_stats
                sector_stats_payload = _sector_stats()
            except Exception as exc:
                logger.warning("sanctions tracker: sector_stats lookup failed: %s", exc)
                sector_stats_payload = None

            # "Latest sanctions analysis" rail — surfaces 5 most-recent
            # briefings that mention sanctions / OFAC / Venezuela-program
            # actions so the tracker page provides crawl signal into
            # individual /briefing/<slug> pages. Without this, briefings
            # only have inbound links from the chronological /briefing
            # index, which Google deprioritises as the index ages.
            from src.models import BlogPost as _BlogPost
            recent_sanctions_briefings = (
                db.query(_BlogPost)
                .filter(
                    (_BlogPost.primary_sector == "governance")
                    | (_BlogPost.primary_sector == "sanctions")
                    | (_BlogPost.primary_sector == "energy")
                    | (_BlogPost.title.ilike("%sanction%"))
                    | (_BlogPost.title.ilike("%OFAC%"))
                    | (_BlogPost.title.ilike("%PDVSA%"))
                )
                .order_by(_BlogPost.published_date.desc())
                .limit(5)
                .all()
            )

            template = _env.get_template("sanctions_tracker.html.j2")
            html = template.render(
                sdn_entries=sdn_entries,
                stats=stats,
                sector_stats=sector_stats_payload,
                recent_briefings=recent_sanctions_briefings,
                seo=seo,
                jsonld=jsonld,
                cluster_ctx=cluster_ctx,
                current_year=_date.today().year,
                last_refreshed_local=last_refreshed_local,
                last_refreshed_relative=last_refreshed_relative,
                next_refresh_local=next_refresh_local,
            )
            return Response(html, mimetype="text/html")
        finally:
            db.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("sanctions tracker render failed: %s", exc)
        abort(500)


# ──────────────────────────────────────────────────────────────────────
# Per-SDN profile pages — /sanctions/<bucket>/<slug> and the bucket
# index pages /sanctions/<bucket>. Each of OFAC's ~410 Venezuela-program
# designations gets its own permanent, indexable URL so that a search
# like "vicente carretero sanctions" lands directly on the matching
# profile (with the entity's name in the title and H1) instead of on
# our generic /sanctions-tracker table.
#
# Why two routes instead of one collapsed route:
#   • The bucket arg is a fixed enum (4 values) — keeping it in the URL
#     gives Google a clean breadcrumb hierarchy (Home → Sanctions →
#     Individuals → Person) which surfaces in SERP rich results.
#   • The slug arg is generated deterministically by data/sdn_profiles.py
#     so collisions are handled there, not here.
# ──────────────────────────────────────────────────────────────────────
# ──────────────────────────────────────────────────────────────────────
# Sector-pivoted SDN views
# ──────────────────────────────────────────────────────────────────────
#
# /sanctions/by-sector            → pillar landing page, 4 sector cards
# /sanctions/sector/<slug>        → A-Z directory for one sector
#
# Why these exist (alongside the bucket routes /sanctions/<bucket>):
#   GSC's #1 organic query for the sanctions corpus is literally
#   "ofac sdn list current military, economic, diplomatic" — i.e. users
#   want a SECTOR-pivoted view, not the entity-type pivot OFAC offers.
#   These two routes serve that intent and provide a second internal-link
#   path into every /sanctions/<bucket>/<slug> profile, which compounds
#   indexing speed for the whole 410-page corpus.
#
# Sector classification is done at SDN-load time in
# src/data/sdn_profiles.py — see _classify_sector for the priority-
# ordered keyword rules and the editorial overrides table.
# ──────────────────────────────────────────────────────────────────────
@app.route("/sanctions/by-sector")
@app.route("/sanctions/by-sector/")
def sanctions_by_sector_index():
    """Pillar landing page that pivots the SDN list by sector."""
    from src.data.sdn_profiles import (
        SECTOR_KEYS, SECTOR_LABELS, SECTOR_DESCRIPTIONS, SECTOR_SLUGS,
        list_by_sector, sector_stats, stats as sdn_stats,
    )
    from src.page_renderer import _env, _base_url, _iso, settings as _s
    from datetime import date as _date, datetime as _dt
    import json as _json

    try:
        s_counts = sector_stats()
        bucket_stats = sdn_stats()

        # Build the per-sector card payload. Top-names is capped at 6 —
        # that's enough to give every card real content above the fold
        # and dense crawlable links, but not so many that one card
        # dominates the SERP snippet preview.
        sectors_payload: list[dict] = []
        for key in SECTOR_KEYS:
            profs = list_by_sector(key)
            sectors_payload.append({
                "key": key,
                "label": SECTOR_LABELS.get(key, key.title()),
                "description": SECTOR_DESCRIPTIONS.get(key, ""),
                "url_path": f"/sanctions/sector/{SECTOR_SLUGS.get(key, key)}",
                "count": len(profs),
                "top_names": profs[:6],
            })

        base = _base_url()
        canonical = f"{base}/sanctions/by-sector"
        today_human = _date.today().strftime("%B %-d, %Y")

        # SEO copy intentionally mirrors the GSC query language the page
        # is built to capture. "Currently" in the title is the freshness
        # signal that "current military, economic, diplomatic" searchers
        # are asking for.
        title = (
            "OFAC Venezuela SDN List by Sector — Currently Sanctioned "
            "Military, Economic, Diplomatic & Governance Officials"
        )[:120]
        description = (
            f"All {bucket_stats['total']} active OFAC Venezuela-program SDN "
            f"designations grouped by sector: {s_counts.get('military', 0)} military "
            f"officials, {s_counts.get('economic', 0)} economic & financial actors, "
            f"{s_counts.get('diplomatic', 0)} diplomatic officials, and "
            f"{s_counts.get('governance', 0)} government & political figures. Updated {today_human}."
        )[:300]

        seo = {
            "title": title,
            "description": description,
            "keywords": (
                "OFAC Venezuela SDN list, OFAC sanctions by sector, "
                "Venezuela military sanctions, Venezuela economic sanctions, "
                "Venezuela diplomatic sanctions, OFAC governance sanctions, "
                "current OFAC Venezuela list"
            ),
            "canonical": canonical,
            "site_name": _s.site_name,
            "site_url": base,
            "locale": _s.site_locale,
            "og_image": f"{base}/static/og-image.png?v=3",
            "og_type": "website",
            "published_iso": _iso(_dt.utcnow()),
            "modified_iso": _iso(_dt.utcnow()),
        }

        jsonld = _json.dumps({
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "BreadcrumbList",
                    "itemListElement": [
                        {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{base}/"},
                        {"@type": "ListItem", "position": 2, "name": "OFAC Venezuela Sanctions", "item": f"{base}/sanctions-tracker"},
                        {"@type": "ListItem", "position": 3, "name": "By sector", "item": canonical},
                    ],
                },
                {
                    "@type": "CollectionPage",
                    "@id": f"{canonical}#collection",
                    "name": title,
                    "description": description,
                    "url": canonical,
                    "isPartOf": {"@type": "WebSite", "url": f"{base}/", "name": _s.site_name},
                    "hasPart": [
                        {
                            "@type": "ItemList",
                            "name": item["label"],
                            "url": f"{base}{item['url_path']}",
                            "numberOfItems": item["count"],
                            "description": item["description"],
                        }
                        for item in sectors_payload
                    ],
                },
            ],
        }, ensure_ascii=False)

        from src.seo.cluster_topology import build_cluster_ctx
        cluster_ctx = build_cluster_ctx("/sanctions/by-sector")

        template = _env.get_template("sanctions/by_sector_index.html.j2")
        html = template.render(
            sectors=sectors_payload,
            stats=bucket_stats | s_counts,
            today_human=today_human,
            seo=seo,
            jsonld=jsonld,
            cluster_ctx=cluster_ctx,
            current_year=_date.today().year,
        )
        return Response(html, mimetype="text/html")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("sanctions by-sector index render failed: %s", exc)
        abort(500)


@app.route("/sanctions/sector/<slug>")
@app.route("/sanctions/sector/<slug>/")
def sanctions_by_sector_detail(slug: str):
    """A-Z directory of every SDN designation in one sector."""
    from src.data.sdn_profiles import (
        SECTOR_KEYS, SECTOR_LABELS, SECTOR_DESCRIPTIONS, SECTOR_SLUGS,
        list_by_sector, sector_stats,
    )
    from src.page_renderer import _env, _base_url, _iso, settings as _s
    from datetime import date as _date, datetime as _dt
    import json as _json

    if slug not in SECTOR_KEYS:
        abort(404)

    try:
        profiles = list_by_sector(slug)
        sector_label = SECTOR_LABELS.get(slug, slug.title())
        sector_description = SECTOR_DESCRIPTIONS.get(slug, "")
        s_counts = sector_stats()

        # A-Z grouping (same shape the bucket index uses, so the
        # template treatment is consistent).
        grouped: list[tuple[str, list]] = []
        current_letter = None
        current_items: list = []
        for p in profiles:
            letter = (p.raw_name[:1] or "#").upper()
            if not letter.isalpha():
                letter = "#"
            if letter != current_letter:
                if current_items:
                    grouped.append((current_letter, current_items))
                current_letter = letter
                current_items = []
            current_items.append(p)
        if current_items:
            grouped.append((current_letter, current_items))

        # Sector-switch nav: every other sector + "current view" callout
        # for `slug`. Renders as a one-line list at the top of the body
        # so users can pivot without going back to the pillar.
        sectors_nav = [
            {
                "key": k,
                "label": SECTOR_LABELS.get(k, k.title()),
                "url_path": f"/sanctions/sector/{SECTOR_SLUGS.get(k, k)}",
                "count": s_counts.get(k, 0),
            }
            for k in SECTOR_KEYS
        ]

        base = _base_url()
        canonical = f"{base}/sanctions/sector/{slug}"
        today_human = _date.today().strftime("%B %-d, %Y")

        # Title bakes "currently sanctioned" + the sector label + count
        # — three of the four signals the GSC query is asking for. The
        # fourth (recency date) is in the meta description.
        title = (
            f"OFAC Venezuela SDN — Currently Sanctioned {sector_label} "
            f"({len(profiles)})"
        )[:120]
        description = (
            f"Complete list of {len(profiles)} {sector_label.lower()} currently on the "
            f"OFAC Venezuela SDN list as of {today_human}. Includes program code, "
            f"designation date, and a permanent profile page for every name. "
            f"Updated twice daily from US Treasury."
        )[:300]

        seo = {
            "title": title,
            "description": description,
            "keywords": (
                f"OFAC Venezuela {sector_label.lower()}, "
                f"sanctioned {sector_label.lower()} Venezuela, "
                f"OFAC SDN {slug} Venezuela, "
                f"current OFAC Venezuela {slug} list"
            ),
            "canonical": canonical,
            "site_name": _s.site_name,
            "site_url": base,
            "locale": _s.site_locale,
            "og_image": f"{base}/static/og-image.png?v=3",
            "og_type": "website",
            "published_iso": _iso(_dt.utcnow()),
            "modified_iso": _iso(_dt.utcnow()),
        }

        jsonld = _json.dumps({
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "BreadcrumbList",
                    "itemListElement": [
                        {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{base}/"},
                        {"@type": "ListItem", "position": 2, "name": "OFAC Venezuela Sanctions", "item": f"{base}/sanctions-tracker"},
                        {"@type": "ListItem", "position": 3, "name": "By sector", "item": f"{base}/sanctions/by-sector"},
                        {"@type": "ListItem", "position": 4, "name": sector_label, "item": canonical},
                    ],
                },
                {
                    "@type": "ItemList",
                    "@id": f"{canonical}#list",
                    "name": title,
                    "description": description,
                    "numberOfItems": len(profiles),
                    "itemListElement": [
                        {
                            "@type": "ListItem",
                            "position": idx + 1,
                            "url": f"{base}{p.url_path}",
                            "name": p.display_name,
                        }
                        for idx, p in enumerate(profiles[:200])
                    ],
                },
            ],
        }, ensure_ascii=False)

        from src.seo.cluster_topology import build_cluster_ctx
        cluster_ctx = build_cluster_ctx(f"/sanctions/sector/{slug}")

        template = _env.get_template("sanctions/by_sector.html.j2")
        html = template.render(
            active_key=slug,
            sector_label=sector_label,
            sector_description=sector_description,
            profiles=profiles,
            grouped=grouped,
            sectors_nav=sectors_nav,
            today_human=today_human,
            seo=seo,
            jsonld=jsonld,
            cluster_ctx=cluster_ctx,
            current_year=_date.today().year,
        )
        return Response(html, mimetype="text/html")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("sanctions by-sector detail render failed for slug=%s: %s", slug, exc)
        abort(500)


@app.route("/sanctions/<bucket>")
@app.route("/sanctions/<bucket>/")
def sanctions_index_page(bucket: str):
    """A-Z directory of every SDN entry in one bucket
    (individuals / entities / vessels / aircraft)."""
    from src.data.sdn_profiles import (
        ENTITY_BUCKETS, _BUCKET_SINGULAR, list_profiles, stats as sdn_stats,
    )
    from src.page_renderer import _env, _base_url, _iso, settings as _s
    from datetime import date as _date, datetime as _dt
    import json as _json

    if bucket not in ENTITY_BUCKETS:
        abort(404)

    try:
        profiles = list_profiles(bucket)
        s = sdn_stats()
        singular = _BUCKET_SINGULAR.get(bucket, bucket)

        grouped: list[tuple[str, list]] = []
        current_letter = None
        current_items: list = []
        for p in profiles:
            letter = (p.raw_name[:1] or "#").upper()
            if not letter.isalpha():
                letter = "#"
            if letter != current_letter:
                if current_items:
                    grouped.append((current_letter, current_items))
                current_letter = letter
                current_items = []
            current_items.append(p)
        if current_items:
            grouped.append((current_letter, current_items))

        base = _base_url()
        canonical = f"{base}/sanctions/{bucket}"
        today_human = _date.today().strftime("%B %Y")
        today_iso = _date.today().isoformat()

        # Title trimmed April 2026 (round 2) after GSC showed 84
        # impressions / 0 clicks on /sanctions/individuals over 3
        # months. Round-1 title (84 chars: "Currently Sanctioned
        # Venezuela Individuals — OFAC SDN List (190 Active, April
        # 2026)") truncated in SERPs, dropping the freshness signal.
        # Round-2 leads with "OFAC SDN" (the literal compliance
        # search vocabulary), keeps the count + bucket noun + month
        # tag inside ~60 chars, and pushes the longer descriptive
        # frame into the description. "US Treasury OFAC" in the
        # description carries the US-authority signal we're missing.
        # Bucket-specific noun in two casings so we don't hit the
        # naive "{singular}s" pluralisation bug ("entitys", "aircrafts").
        # Aircraft is uninflected; entity → entities.
        bucket_noun_title = {
            "individuals": "Venezuelan Individuals",
            "entities":    "Venezuelan Entities",
            "vessels":     "Venezuelan Vessels",
            "aircraft":    "Venezuelan Aircraft",
        }.get(bucket, f"Venezuelan {bucket.capitalize()}")
        bucket_noun_lower = {
            "individuals": "Venezuelan individuals",
            "entities":    "Venezuelan entities",
            "vessels":     "Venezuelan vessels",
            "aircraft":    "Venezuelan aircraft",
        }.get(bucket, f"Venezuelan {bucket}")
        seo = {
            "title": (
                f"OFAC SDN: {len(profiles)} Sanctioned {bucket_noun_title} ({today_human})"
            )[:120],
            "description": (
                f"All {len(profiles)} {bucket_noun_lower} on the US Treasury "
                f"OFAC SDN list ({today_human}). A–Z directory with program "
                f"codes, executive orders, and direct OFAC source links."
            )[:300],
            "keywords": (
                f"sanctioned Venezuela {bucket}, OFAC Venezuela {bucket} list, "
                f"US Treasury Venezuela {bucket}, Venezuela SDN {bucket} "
                f"{today_human.split()[-1]}, OFAC Venezuela sanctions list, "
                f"OFAC SDN search"
            ),
            "canonical": canonical,
            "site_name": _s.site_name,
            "site_url": base,
            "locale": _s.site_locale,
            "og_image": f"{base}/static/og-image.png?v=3",
            "og_type": "website",
            "published_iso": _iso(_dt.utcnow()),
            "modified_iso": _iso(_dt.utcnow()),
        }

        jsonld = _json.dumps({
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "BreadcrumbList",
                    "itemListElement": [
                        {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{base}/"},
                        {"@type": "ListItem", "position": 2, "name": "OFAC Venezuela Sanctions", "item": f"{base}/sanctions-tracker"},
                        {"@type": "ListItem", "position": 3, "name": bucket.capitalize(), "item": canonical},
                    ],
                },
                {
                    "@type": "ItemList",
                    "@id": f"{canonical}#list",
                    "name": f"OFAC Venezuela SDN — {bucket.capitalize()}",
                    "numberOfItems": len(profiles),
                    "itemListElement": [
                        {
                            "@type": "ListItem",
                            "position": idx + 1,
                            "url": f"{base}{p.url_path}",
                            "name": p.display_name,
                        }
                        for idx, p in enumerate(profiles[:200])
                    ],
                },
            ],
        }, ensure_ascii=False)

        from src.seo.cluster_topology import build_cluster_ctx
        cluster_ctx = build_cluster_ctx(f"/sanctions/{bucket}")

        template = _env.get_template("sanctions/index.html.j2")
        html = template.render(
            bucket=bucket,
            singular=singular,
            profiles=profiles,
            grouped=grouped,
            stats=s,
            seo=seo,
            jsonld=jsonld,
            cluster_ctx=cluster_ctx,
            current_year=_date.today().year,
            today_human=today_human,
            today_iso=today_iso,
        )
        return Response(html, mimetype="text/html")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("sanctions index render failed for bucket=%s: %s", bucket, exc)
        abort(500)


@app.route("/sanctions/<bucket>/<slug>")
@app.route("/sanctions/<bucket>/<slug>/")
def sanctions_profile_page(bucket: str, slug: str):
    """One OFAC SDN entry's permanent, indexable profile page."""
    from src.data.sdn_profiles import (
        ENTITY_BUCKETS, family_members, find_related_news, get_profile,
        list_profiles, resolve_linked_to, stats as sdn_stats,
    )
    from src.page_renderer import _env, _base_url, _iso, settings as _s
    from datetime import date as _date, datetime as _dt
    import json as _json

    if bucket not in ENTITY_BUCKETS:
        abort(404)
    profile = get_profile(bucket, slug)
    if profile is None:
        abort(404)

    try:
        family = family_members(profile)
        linked_to = resolve_linked_to(profile)
        related_news = find_related_news(profile)
        s = sdn_stats()

        # Up to 6 alphabetical neighbors of the same bucket — gives Googlebot
        # 6 fresh outbound links from every profile page, which dramatically
        # accelerates how fast the whole 410-page corpus gets crawled.
        all_in_bucket = list_profiles(bucket)
        siblings: list = []
        try:
            idx = next(i for i, p in enumerate(all_in_bucket) if p.db_id == profile.db_id)
            for i in range(max(0, idx - 3), min(len(all_in_bucket), idx + 4)):
                if all_in_bucket[i].db_id == profile.db_id:
                    continue
                siblings.append(all_in_bucket[i])
                if len(siblings) >= 6:
                    break
        except StopIteration:
            siblings = []

        base = _base_url()
        canonical = f"{base}{profile.url_path}"
        today_human = _date.today().strftime("%B %Y")
        today_iso = _date.today().isoformat()

        # ── SEO copy ──────────────────────────────────────────────────
        # Title: short name + binary status ("Sanctioned by OFAC") +
        # date marker ("Active 2026"). The date marker drives CTR even
        # at position 6+ — Google has shown that "fresh" results
        # outclick stale ones for currently-active queries like
        # "vicente carretero sanction" or "vladimir padrino lopez ofac".
        title = (
            f"{profile.display_name} — Sanctioned by OFAC "
            f"(Active {_date.today().year})"
        )[:120]

        # Description: open with binary "Yes — actively sanctioned",
        # follow with the most-clickable identifying detail (DOB /
        # nationality / program), close with a click-trigger CTA so the
        # SERP snippet ends on action verbs, not throat-clearing.
        ident_bits: list[str] = []
        if profile.parsed.get("nationality"):
            ident_bits.append(profile.parsed["nationality"])
        if profile.parsed.get("dob"):
            ident_bits.append(f"born {profile.parsed['dob']}")
        if profile.parsed.get("imo"):
            ident_bits.append(f"IMO {profile.parsed['imo']}")
        if profile.parsed.get("aircraft_tail"):
            ident_bits.append(f"tail {profile.parsed['aircraft_tail']}")
        ident_phrase = (" (" + ", ".join(ident_bits) + ")") if ident_bits else ""
        program_phrase = profile.program or "Venezuela-related sanctions"

        description = (
            f"{profile.display_name}{ident_phrase} is actively sanctioned by "
            f"OFAC under {program_phrase} as of {today_human}. View the live "
            f"SDN entry, linked entities, and the executive order under which "
            f"the designation was made."
        ).strip()[:300]

        seo = {
            "title": title,
            "description": description,
            "keywords": (
                f"is {profile.display_name} sanctioned, "
                f"{profile.display_name} OFAC, {profile.display_name} sanctions, "
                f"{profile.raw_name}, OFAC Venezuela {profile.category_singular}, "
                f"OFAC SDN {profile.category_singular}, {profile.program}"
            ),
            "canonical": canonical,
            "site_name": _s.site_name,
            "site_url": base,
            "locale": _s.site_locale,
            "og_image": f"{base}/static/og-image.png?v=3",
            "og_type": "profile",
            "published_iso": _iso(_dt.utcnow()),
            "modified_iso": _iso(_dt.utcnow()),
        }

        # Build the schema.org @graph: BreadcrumbList + the bucket-specific
        # entity type. We use Person for individuals (Knowledge-Graph eligible),
        # Organization for entities (B2B compliance crawlers respect it),
        # and Thing for vessels and aircraft. We deliberately avoid Vehicle
        # here because schema.org/Vehicle is a subtype of Product, which makes
        # Google's Product Rich Results validator demand offers/review/
        # aggregateRating — none of which apply to a sanctioned asset. Thing
        # keeps the identifier/description fields without triggering the
        # commerce-oriented validator.
        breadcrumb = {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{base}/"},
                {"@type": "ListItem", "position": 2, "name": "OFAC Venezuela Sanctions", "item": f"{base}/sanctions-tracker"},
                {"@type": "ListItem", "position": 3, "name": profile.bucket.capitalize(), "item": f"{base}/sanctions/{profile.bucket}"},
                {"@type": "ListItem", "position": 4, "name": profile.display_name, "item": canonical},
            ],
        }

        identifiers: list = []
        if profile.parsed.get("cedula"):
            identifiers.append({"@type": "PropertyValue", "propertyID": "Cedula", "value": profile.parsed["cedula"]})
        if profile.parsed.get("passport"):
            identifiers.append({"@type": "PropertyValue", "propertyID": "Passport", "value": profile.parsed["passport"]})
        if profile.parsed.get("national_id"):
            identifiers.append({"@type": "PropertyValue", "propertyID": "NationalID", "value": profile.parsed["national_id"]})
        if profile.parsed.get("imo"):
            identifiers.append({"@type": "PropertyValue", "propertyID": "IMO", "value": profile.parsed["imo"]})
        if profile.parsed.get("mmsi"):
            identifiers.append({"@type": "PropertyValue", "propertyID": "MMSI", "value": profile.parsed["mmsi"]})
        if profile.parsed.get("aircraft_tail"):
            identifiers.append({"@type": "PropertyValue", "propertyID": "AircraftTailNumber", "value": profile.parsed["aircraft_tail"]})
        if profile.parsed.get("aircraft_serial"):
            identifiers.append({"@type": "PropertyValue", "propertyID": "AircraftSerialNumber", "value": profile.parsed["aircraft_serial"]})

        if profile.bucket == "individuals":
            entity_node = {
                "@type": "Person",
                "@id": f"{canonical}#person",
                "name": profile.display_name,
                "alternateName": profile.raw_name,
                "url": canonical,
                "description": description,
                "subjectOf": {
                    "@type": "GovernmentService",
                    "name": profile.program_label,
                    "provider": {"@type": "GovernmentOrganization", "name": "US Treasury Office of Foreign Assets Control (OFAC)"},
                },
            }
            if profile.parsed.get("dob"):
                entity_node["birthDate"] = profile.parsed["dob"]
            if profile.parsed.get("pob"):
                entity_node["birthPlace"] = profile.parsed["pob"]
            if profile.parsed.get("nationality"):
                entity_node["nationality"] = profile.parsed["nationality"]
            if profile.parsed.get("gender"):
                entity_node["gender"] = profile.parsed["gender"]
            if identifiers:
                entity_node["identifier"] = identifiers
        elif profile.bucket == "entities":
            entity_node = {
                "@type": "Organization",
                "@id": f"{canonical}#org",
                "name": profile.display_name,
                "alternateName": profile.raw_name,
                "url": canonical,
                "description": description,
            }
            if identifiers:
                entity_node["identifier"] = identifiers
        else:
            asset_kind = "vessel" if profile.bucket == "vessels" else "aircraft"
            entity_node = {
                "@type": "Thing",
                "@id": f"{canonical}#asset",
                "name": profile.display_name,
                "alternateName": profile.raw_name,
                "url": canonical,
                "description": description,
            }
            extra_props: list = [
                {"@type": "PropertyValue", "propertyID": "AssetType", "value": asset_kind},
            ]
            if profile.parsed.get("aircraft_model"):
                extra_props.append({"@type": "PropertyValue", "propertyID": "AircraftModel", "value": profile.parsed["aircraft_model"]})
            if profile.parsed.get("vessel_year"):
                extra_props.append({"@type": "PropertyValue", "propertyID": "YearOfBuild", "value": profile.parsed["vessel_year"]})
            if profile.parsed.get("vessel_flag"):
                extra_props.append({"@type": "PropertyValue", "propertyID": "Flag", "value": profile.parsed["vessel_flag"]})
            entity_node["additionalProperty"] = extra_props
            if identifiers:
                entity_node["identifier"] = identifiers

        # ── FAQPage Q&As ──────────────────────────────────────────────
        # Three binary-answer questions matching how compliance, media,
        # and counterparty researchers search ("is X sanctioned?",
        # "what program?", "when was X added?"). FAQPage rich results
        # are the single biggest CTR lever on these long-tail
        # individual-name pages — they double the SERP real estate.
        program_label = profile.program_label or "Venezuela-related OFAC sanctions"
        added_human = profile.designation_date or "the date OFAC first published the designation"

        is_sanctioned_q = f"Is {profile.display_name} currently sanctioned by OFAC?"
        is_sanctioned_a = (
            f"Yes. As of {today_human}, {profile.display_name} is on the active US Treasury "
            f"Office of Foreign Assets Control (OFAC) Specially Designated Nationals (SDN) "
            f"list under the {program_label} program. All assets under US jurisdiction are "
            f"blocked and US persons are generally prohibited from dealing with them."
        )

        program_q = f"What OFAC program is {profile.display_name} sanctioned under?"
        program_a = (
            f"{profile.display_name} is designated under {program_label}. "
            "This is one of four Venezuela-related OFAC programs: VENEZUELA "
            "(omnibus), EO 13692 (human-rights / corruption, 2015), EO 13850 "
            "(gold sector and individual officials, 2018), and EO 13884 "
            "(government-of-Venezuela block, 2019)."
        )

        added_q = f"When was {profile.display_name} added to the OFAC SDN list?"
        added_a = (
            f"{profile.display_name} was added to the OFAC SDN list on {added_human}. "
            "OFAC publishes designations as part of broader Venezuela-program actions; "
            "the linked OFAC source page records the original press release. The "
            "designation remains active until OFAC removes it via a delisting action."
        )

        faq_block = [
            {"q": is_sanctioned_q, "a": is_sanctioned_a},
            {"q": program_q,       "a": program_a},
            {"q": added_q,         "a": added_a},
        ]

        faq_node = {
            "@type": "FAQPage",
            "@id": f"{canonical}#faq",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": f["q"],
                    "acceptedAnswer": {"@type": "Answer", "text": f["a"][:500]},
                }
                for f in faq_block
            ],
        }

        jsonld = _json.dumps({
            "@context": "https://schema.org",
            "@graph": [breadcrumb, entity_node, faq_node],
        }, ensure_ascii=False)

        from src.seo.cluster_topology import build_cluster_ctx, sector_for_program
        cluster_ctx = build_cluster_ctx(profile.url_path)
        sector_link = sector_for_program(profile.program)

        template = _env.get_template("sanctions/profile.html.j2")
        html = template.render(
            profile=profile,
            family=family,
            linked_to=linked_to,
            related_news=related_news,
            siblings=siblings,
            stats=s,
            sector_link=sector_link,
            cluster_ctx=cluster_ctx,
            seo=seo,
            jsonld=jsonld,
            current_year=_date.today().year,
            today_human=today_human,
            today_iso=today_iso,
            faq_block=faq_block,
        )
        return Response(html, mimetype="text/html")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "sanctions profile render failed for bucket=%s slug=%s: %s",
            bucket, slug, exc,
        )
        abort(500)


# ──────────────────────────────────────────────────────────────────────
# /lab — local-only experimental routes. NOT linked from the public
# site, NOT enumerated in any sitemap, and gated behind a hard
# allowlist. Safe to add new routes under /lab/* without touching any
# production behavior. See src/lab/ for the data + template layer.
# ──────────────────────────────────────────────────────────────────────


@app.route("/lab/entity/<slug>")
@app.route("/lab/entity/<slug>/")
def lab_entity_page(slug: str):
    """Maria MVP — disambiguation-first SDN entity page. Local experiment."""
    from src.lab import ALLOWED_ENTITIES
    from src.lab.entity_mvp import assemble, compute_fingerprint
    from src.page_renderer import _env

    if slug not in ALLOWED_ENTITIES:
        abort(404)

    ctx = assemble(slug)
    if ctx is None:
        abort(404)

    dossier_mode = request.args.get("dossier") == "1"
    fingerprint = compute_fingerprint(ctx)
    canonical_url = f"{request.host_url.rstrip('/')}/lab/entity/{slug}"

    try:
        template = _env.get_template("lab/entity.html.j2")
        html = template.render(
            seo={
                "title": f"[LAB] {ctx['identity_card']['display_name']} — Maria MVP",
                "description": "Local experiment; not for public consumption.",
                "canonical": "",
                "site_name": "Caracas Research (Lab)",
                "site_url": "",
                "locale": "en_US",
                "og_image": "",
                "og_type": "article",
            },
            current_year=__import__("datetime").date.today().year,
            dossier_mode=dossier_mode,
            fingerprint=fingerprint,
            canonical_url=canonical_url,
            **ctx,
        )
        return Response(html, mimetype="text/html")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("lab entity render failed for slug=%s: %s", slug, exc)
        abort(500)


@app.route("/lab/entity/<slug>/dossier.pdf")
def lab_entity_dossier_pdf(slug: str):
    """Render the lab entity page to PDF via Playwright. Spawns Chromium
    per request — slow (~3s) but fine for local MVP. Cache later."""
    from src.lab import ALLOWED_ENTITIES
    from src.lab.entity_mvp import pdf_filename_for, render_pdf

    if slug not in ALLOWED_ENTITIES:
        abort(404)

    base = request.host_url.rstrip("/")
    try:
        pdf_bytes = render_pdf(slug, base_url=base)
    except Exception as exc:
        logger.exception("lab dossier PDF render failed for slug=%s: %s", slug, exc)
        abort(500)

    filename = pdf_filename_for(slug)
    resp = Response(pdf_bytes, mimetype="application/pdf")
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ──────────────────────────────────────────────────────────────────────
# Public-company Venezuela exposure pages — a per-S&P-500-ticker
# landing-page corpus and the interactive lookup tool that funnels
# searches into it. See src/data/company_exposure.py for the engine.
#
# Routes:
#   /companies                                       → A-Z directory
#   /companies/<slug>/venezuela-exposure             → per-company page
#   /companies/<slug>                                → 301 → above
#   /tools/public-company-venezuela-exposure-check   → interactive tool
#
# The slug format is `{shortname}-{ticker}` (see slugify_company in
# src/data/sp500_companies.py) so collisions are impossible. Pages are
# rendered live (cheap; the engine caches both SDN scans and EDGAR
# results), and every URL is enumerated in /sitemap.xml so Google can
# crawl the full ~500-page corpus on first encounter.
# ──────────────────────────────────────────────────────────────────────


def _company_index_letter(name: str) -> str:
    letter = (name[:1] or "#").upper()
    return letter if letter.isalpha() else "#"


@app.route("/companies")
@app.route("/companies/")
def companies_index_page():
    """A-Z directory of every S&P 500 ticker with a Venezuela-exposure page."""
    try:
        from src.data.company_exposure import list_company_index_rows
        from src.page_renderer import _env, _base_url, _iso, settings as _s
        from datetime import date as _date, datetime as _dt
        import json as _json

        rows = list_company_index_rows(include_sdn_scan=True)

        # Group A-Z; counts power the summary strip.
        grouped: list[tuple[str, list]] = []
        current_letter: str | None = None
        current_items: list = []
        counts = {"direct": 0, "indirect": 0, "historical": 0, "none": 0, "unknown": 0}
        for r in rows:
            counts[r.classification] = counts.get(r.classification, 0) + 1
            letter = _company_index_letter(r.name)
            if letter != current_letter:
                if current_items:
                    grouped.append((current_letter, current_items))
                current_letter = letter
                current_items = []
            current_items.append(r)
        if current_items:
            grouped.append((current_letter, current_items))

        base = _base_url()
        canonical = f"{base}/companies"
        seo = {
            "title": (
                f"S&P 500 Venezuela Exposure Register — {len(rows)} companies audited"
            ),
            "description": (
                f"Free Venezuela-exposure audit for every S&P 500 company. OFAC SDN "
                f"matches, SEC filing disclosures, and Caracas Research analyst notes "
                f"for {len(rows)} tickers. Refreshed daily."
            ),
            "keywords": (
                "S&P 500 Venezuela exposure, public company Venezuela exposure, "
                "OFAC sanctions S&P 500, Venezuela exposure check, EDGAR Venezuela filings"
            ),
            "canonical": canonical,
            "site_name": _s.site_name,
            "site_url": base,
            "locale": _s.site_locale,
            "og_image": f"{base}/static/og-image.png?v=3",
            "og_type": "website",
            "published_iso": _iso(_dt.utcnow()),
            "modified_iso": _iso(_dt.utcnow()),
        }
        jsonld = _json.dumps({
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "BreadcrumbList",
                    "itemListElement": [
                        {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{base}/"},
                        {"@type": "ListItem", "position": 2, "name": "S&P 500 Venezuela Exposure", "item": canonical},
                    ],
                },
                {
                    "@type": "ItemList",
                    "@id": f"{canonical}#list",
                    "name": "S&P 500 Venezuela Exposure Register",
                    "numberOfItems": len(rows),
                    "itemListElement": [
                        {
                            "@type": "ListItem",
                            "position": idx + 1,
                            "url": f"{base}{r.url_path}",
                            "name": r.name,
                        }
                        for idx, r in enumerate(rows[:200])
                    ],
                },
            ],
        }, ensure_ascii=False)

        from src.seo.cluster_topology import build_cluster_ctx
        cluster_ctx = build_cluster_ctx("/companies")

        template = _env.get_template("companies/index.html.j2")
        html = template.render(
            rows=rows,
            grouped=grouped,
            counts=counts,
            seo=seo,
            jsonld=jsonld,
            cluster_ctx=cluster_ctx,
            current_year=_date.today().year,
        )
        return Response(html, mimetype="text/html")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("companies index render failed: %s", exc)
        abort(500)


@app.route("/companies/<slug>")
@app.route("/companies/<slug>/")
def companies_slug_redirect(slug: str):
    """Send /companies/<slug> → /companies/<slug>/venezuela-exposure.

    The "venezuela-exposure" suffix is the SEO-bearing keyword in the
    URL, so we want the canonical page to live at the longer path.
    Bare /companies/<slug> exists only to catch backlinks people might
    paste without the suffix."""
    return redirect(f"/companies/{slug}/venezuela-exposure", code=301)


@app.route("/companies/<slug>/venezuela-exposure")
@app.route("/companies/<slug>/venezuela-exposure/")
def companies_profile_page(slug: str):
    """Per-company Venezuela-exposure landing page."""
    try:
        from src.data.company_exposure import (
            build_exposure_report, find_company_by_slug, list_company_index_rows,
        )
        from src.page_renderer import _env, _base_url, _iso, settings as _s
        from datetime import date as _date, datetime as _dt
        import json as _json

        company = find_company_by_slug(slug)
        if company is None:
            abort(404)

        # If the resolver matched a different canonical slug than the URL
        # (e.g. user typed a ticker alone), 301 to the canonical so we
        # don't spawn duplicate-content variants in Google's index.
        if company.slug != slug:
            return redirect(f"/companies/{company.slug}/venezuela-exposure", code=301)

        report = build_exposure_report(company)

        # Sibling cohort: up to 6 other companies in the same sector with
        # a curated/SDN signal (interesting clicks come first; if there
        # aren't enough, fill with alphabetically-adjacent rows).
        all_rows = list_company_index_rows(include_sdn_scan=False)
        same_sector = [
            r for r in all_rows
            if r.sector == company.sector and r.ticker != company.ticker
        ]
        siblings = [r for r in same_sector if r.has_curated][:6]
        if len(siblings) < 6:
            for r in same_sector:
                if r in siblings:
                    continue
                siblings.append(r)
                if len(siblings) >= 6:
                    break

        base = _base_url()
        canonical = f"{base}/companies/{company.slug}/venezuela-exposure"
        today_human = _date.today().strftime("%B %Y")
        today_iso = _date.today().isoformat()

        # ── SEO copy ──────────────────────────────────────────────────
        # Title is a binary question. From GSC: queries like
        # "jacobs solutions inc. sanctions" are intent-binary ("am I
        # exposed?") — the title that wins clicks at position 6+ is the
        # one whose snippet immediately answers the question. We bake
        # the answer into both the title format and the meta description
        # so the SERP snippet does the persuasion before the click.
        title = (
            f"Is {company.short_name} ({company.ticker}) Sanctioned? "
            f"Venezuela & OFAC Exposure ({today_human})"
        )[:120]

        # Binary answers per classification. Drives both the description
        # opener and the FAQPage answer body. Keeping the mapping in one
        # place avoids drift between the SERP snippet, the visible page
        # banner, and the structured-data answers.
        binary_yes_no = {
            "direct":     ("Yes",   "has direct Venezuela exposure on the public record"),
            "indirect":   ("Partly", "has indirect Venezuela exposure via subsidiaries or counterparties"),
            "historical": ("No (resolved)", "has only historical Venezuela exposure (wound down or written off)"),
            "none":       ("No",   "has no current Venezuela exposure on the public record"),
            "unknown":    ("No",   "has no Venezuela exposure on the public record"),
        }
        yes_no, binary_phrase = binary_yes_no.get(
            report.classification, ("Unknown", "exposure to Venezuela has not been determined")
        )

        # Open the description with the binary answer using ${ticker} so
        # it matches how analysts type the query, then add the
        # methodology and freshness in one breath. Cap at 300 to avoid
        # SERP truncation while still carrying the click-driver.
        description = (
            f"{company.short_name} (${company.ticker}) {binary_phrase} as of "
            f"{today_human}. Independent check across OFAC SDN list, SEC EDGAR "
            f"10-K/10-Q/20-F filings, and the Caracas Research news corpus."
        ).strip()[:300]

        seo = {
            "title": title,
            "description": description,
            "keywords": (
                f"is {company.short_name} sanctioned, {company.short_name} Venezuela, "
                f"{company.ticker} Venezuela exposure, {company.short_name} OFAC, "
                f"{company.short_name} PDVSA, {company.ticker} sanctions, "
                f"public company Venezuela exposure"
            ),
            "canonical": canonical,
            "site_name": _s.site_name,
            "site_url": base,
            "locale": _s.site_locale,
            "og_image": f"{base}/static/og-image.png?v=3",
            "og_type": "article",
            "published_iso": _iso(_dt.utcnow()),
            "modified_iso": _iso(_dt.utcnow()),
        }

        # JSON-LD: BreadcrumbList + Article + Organization + FAQPage.
        # The FAQPage block is the CTR lever — Google often renders the
        # collapsible FAQ rich result for questions matching {brand} +
        # "sanctioned" / "Venezuela exposure", which roughly doubles
        # click-through versus a plain blue-link result.
        breadcrumb = {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{base}/"},
                {"@type": "ListItem", "position": 2, "name": "S&P 500 Venezuela Exposure", "item": f"{base}/companies"},
                {"@type": "ListItem", "position": 3, "name": company.short_name, "item": canonical},
            ],
        }
        article_node = {
            "@type": "Article",
            "@id": f"{canonical}#article",
            "url": canonical,
            "headline": title,
            "description": description,
            "datePublished": _iso(_dt.utcnow()),
            "dateModified": _iso(_dt.utcnow()),
            "inLanguage": "en-US",
            "isAccessibleForFree": True,
            "author": {"@type": "Organization", "name": _s.site_name, "url": f"{base}/"},
            "publisher": {
                "@type": "Organization",
                "name": _s.site_name,
                "url": f"{base}/",
                "logo": {"@type": "ImageObject", "url": f"{base}/static/og-image.png?v=3"},
            },
            "about": {
                "@type": "Organization",
                "name": company.name,
                "alternateName": company.ticker,
            },
        }

        # ── FAQPage Q&As ──────────────────────────────────────────────
        # Three questions matching how compliance / IR / M&A analysts
        # actually search, each with a one-paragraph answer ≤300 chars
        # so Google renders them cleanly in the rich result.
        is_sanctioned_a = (
            f"As of {today_human}, {company.short_name} ({company.ticker}) is "
            + ("listed on, or directly connected to entities on, the OFAC Venezuela SDN list."
               if report.sdn_matches else
               "not listed on the OFAC Venezuela SDN list. No direct or subsidiary entity match was found in our scan against the live US Treasury SDN feed.")
            + " Always re-verify against the official OFAC Sanctions Search before relying on this for a compliance decision."
        )

        edgar_n = len(report.edgar_mentions)
        sec_disclosure_a = (
            f"{company.short_name} has filed {edgar_n} recent SEC document"
            f"{'s' if edgar_n != 1 else ''} containing Venezuela-related references "
            f"(searched across 10-K, 10-Q, 8-K, 20-F, and 6-K filings on EDGAR over the last 24 months). "
            "See the SEC filings section on the page for the matched excerpts and links to each filing."
        ) if edgar_n else (
            f"No recent SEC filings by {company.short_name} ({company.ticker}) contain Venezuela, "
            "PDVSA, CITGO, or Caracas references in our EDGAR search across 10-K, 10-Q, 8-K, "
            "20-F, and 6-K forms over the last 24 months. Use SEC EDGAR's full-text search to verify."
        )

        revenue_exposure_a = (
            f"{report.headline} {report.summary[:200]}".strip()
        )[:300]

        faq_node = {
            "@type": "FAQPage",
            "@id": f"{canonical}#faq",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": f"Is {company.short_name} ({company.ticker}) sanctioned by OFAC?",
                    "acceptedAnswer": {"@type": "Answer", "text": is_sanctioned_a[:400]},
                },
                {
                    "@type": "Question",
                    "name": f"Does {company.short_name} have Venezuela revenue exposure?",
                    "acceptedAnswer": {"@type": "Answer", "text": revenue_exposure_a[:400]},
                },
                {
                    "@type": "Question",
                    "name": f"Has {company.short_name} disclosed Venezuela in its SEC filings?",
                    "acceptedAnswer": {"@type": "Answer", "text": sec_disclosure_a[:400]},
                },
            ],
        }

        jsonld = _json.dumps(
            {"@context": "https://schema.org", "@graph": [breadcrumb, article_node, faq_node]},
            ensure_ascii=False,
        )

        from src.seo.cluster_topology import build_cluster_ctx
        cluster_ctx = build_cluster_ctx(f"/companies/{company.slug}/venezuela-exposure")

        # FAQ-style copy ALSO needs to be visible on the page — Google
        # only honors FAQPage structured data when the same Q&As appear
        # in the rendered HTML. We pass the trio through to the
        # template so the on-page FAQ block stays in lockstep.
        faq_block = [
            {
                "q": f"Is {company.short_name} ({company.ticker}) sanctioned by OFAC?",
                "a": is_sanctioned_a,
            },
            {
                "q": f"Does {company.short_name} have Venezuela revenue exposure?",
                "a": revenue_exposure_a,
            },
            {
                "q": f"Has {company.short_name} disclosed Venezuela in its SEC filings?",
                "a": sec_disclosure_a,
            },
        ]

        template = _env.get_template("companies/profile.html.j2")
        html = template.render(
            report=report,
            siblings=siblings,
            seo=seo,
            jsonld=jsonld,
            cluster_ctx=cluster_ctx,
            current_year=_date.today().year,
            today_human=today_human,
            today_iso=today_iso,
            faq_block=faq_block,
        )
        return Response(html, mimetype="text/html")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("company profile render failed for slug=%s: %s", slug, exc)
        abort(500)


@app.route("/tools/public-company-venezuela-exposure-check")
@app.route("/tools/public-company-venezuela-exposure-check/")
def tool_public_company_exposure_check():
    """Interactive lookup tool that resolves a free-text query to one
    of the per-company landing pages."""
    try:
        from src.data.company_exposure import (
            build_exposure_report, list_company_index_rows,
        )
        from src.data.sp500_companies import find_company
        from src.page_renderer import _env
        from datetime import date as _date

        query = (request.args.get("q") or "").strip()
        report = None
        if query:
            company = find_company(query)
            if company is not None:
                # Don't run the EDGAR network path on the tool surface —
                # we want this responsive even when EDGAR is slow. The
                # full /companies/<slug>/venezuela-exposure page handles
                # the live fetch and caches it for 30 days.
                report = build_exposure_report(company, use_edgar=True, network=False)

        # Pre-baked "popular" list for the empty state. Pull from the
        # curated registry first (those have the richest answers), then
        # pad with two well-known names.
        popular_tickers = [
            ("CVX", "Direct (Chevron PdVSA JVs)"),
            ("HAL", "Direct (oilfield services)"),
            ("SLB", "Direct (oilfield services)"),
            ("BKR", "Direct (oilfield services)"),
            ("PSX", "Historical (heavy crude refining)"),
            ("VLO", "Historical (heavy crude refining)"),
            ("MPC", "Historical (heavy crude refining)"),
            ("KO",  "Indirect (FEMSA bottling)"),
            ("PEP", "Indirect (Polar bottling)"),
            ("PG",  "Historical (manufacturing exit)"),
            ("F",   "Historical (Valencia plant)"),
            ("GM",  "Historical (plant seized 2017)"),
            ("T",   "Historical (DirecTV seized 2020)"),
            ("JPM", "Historical (EMBI bond holdings)"),
            ("GS",  "Historical (PdVSA 2017 bond purchase)"),
            ("BLK", "Historical (passive EM holdings)"),
        ]
        popular_lookup = {t: lbl for t, lbl in popular_tickers}
        popular: list[dict] = []
        for r in list_company_index_rows(include_sdn_scan=False):
            if r.ticker in popular_lookup:
                popular.append({
                    "ticker": r.ticker,
                    "short_name": r.short_name,
                    "url_path": r.url_path,
                    "label": popular_lookup[r.ticker],
                })
        # Stable order matching popular_tickers, not alphabetical.
        order = {t: i for i, (t, _) in enumerate(popular_tickers)}
        popular.sort(key=lambda p: order.get(p["ticker"], 999))

        seo, jsonld = _tool_seo_jsonld(
            slug="public-company-venezuela-exposure-check",
            title="Public Company Venezuela Exposure Check — Free OFAC + SEC Tool",
            description=(
                "Free tool: type any S&P 500 company name or ticker and instantly "
                "see whether the company has Venezuela exposure on the OFAC SDN "
                "list, in its recent SEC filings, or in our Federal Register / "
                "news corpus. Backed by 500+ per-ticker landing pages."
            ),
            keywords=(
                "public company Venezuela exposure, S&P 500 Venezuela check, "
                "OFAC company screening, Venezuela exposure search, "
                "PDVSA exposure check, SEC filings Venezuela"
            ),
            faq=[
                {
                    "q": "How do I check if a public company has Venezuela exposure?",
                    "a": "Type the company name or its ticker into the search box above. The tool resolves the query against the S&P 500 list, runs an OFAC SDN scan, checks recent SEC filings (10-K, 10-Q, 8-K, 20-F, 6-K) for Venezuela-related disclosures, and surfaces matching Federal Register notices and news articles from the Caracas Research corpus.",
                },
                {
                    "q": "Which companies are covered?",
                    "a": "Every S&P 500 constituent (about 500 tickers) has a dedicated profile page at /companies/<slug>/venezuela-exposure. About 30 of those have a hand-curated analyst note with subsidiary names and OFAC general-license context; the rest rely on algorithmic signals (OFAC SDN match, EDGAR full-text search, news corpus scan).",
                },
                {
                    "q": "What does \"no exposure on the public record\" mean?",
                    "a": "It means there is no entry on the OFAC Venezuela SDN list matching the company or any of its known subsidiaries, no Venezuela-related disclosure in the company's recent SEC filings that we have indexed, and no analyzed news article in our corpus naming the company alongside Venezuelan context. This is the answer most analysts come to verify.",
                },
                {
                    "q": "Is this tool a substitute for sanctions counsel?",
                    "a": "No. The tool surfaces signals that justify deeper diligence; it does not perform full ownership-chain analysis (the OFAC 50% Rule), check non-SDN sectoral lists, or verify enforcement context. For high-stakes counterparties, retain qualified sanctions counsel.",
                },
            ],
        )

        from src.seo.cluster_topology import build_cluster_ctx
        cluster_ctx = build_cluster_ctx("/tools/public-company-venezuela-exposure-check")

        template = _env.get_template("tools/public_company_exposure_check.html.j2")
        html = template.render(
            query=query,
            report=report,
            popular=popular,
            seo=seo,
            jsonld=jsonld,
            cluster_ctx=cluster_ctx,
            current_year=_date.today().year,
            recent_briefings=_fetch_recent_briefings(),
        )
        return Response(html, mimetype="text/html")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("public company exposure tool render failed: %s", exc)
        abort(500)


@app.route("/calendar")
@app.route("/calendar/")
def calendar_page():
    """Standalone investor calendar page — same data the home report uses."""
    try:
        from src.report_generator import _build_calendar
        from src.models import (
            AssemblyNewsEntry,
            ExternalArticleEntry,
            GazetteStatus,
            SessionLocal,
            init_db,
        )
        from src.page_renderer import _env, _base_url, _iso, settings as _s
        from datetime import date as _date, datetime as _dt, timedelta as _td
        import json as _json

        init_db()
        db = SessionLocal()
        try:
            cutoff = _date.today() - _td(days=settings.report_lookback_days)
            ext = (
                db.query(ExternalArticleEntry)
                .filter(ExternalArticleEntry.status == GazetteStatus.ANALYZED)
                .filter(ExternalArticleEntry.published_date >= cutoff)
                .all()
            )
            asm = (
                db.query(AssemblyNewsEntry)
                .filter(AssemblyNewsEntry.status == GazetteStatus.ANALYZED)
                .filter(AssemblyNewsEntry.published_date >= cutoff)
                .all()
            )
            calendar_events = _build_calendar(ext, asm)

            base = _base_url()
            canonical = f"{base}/calendar"
            seo = {
                "title": "Venezuela Investor Calendar — OFAC, BCV, Asamblea key dates",
                "description": (
                    "Upcoming OFAC license expirations, Asamblea Nacional sessions, BCV "
                    "announcements, and sovereign debt deadlines. Updated twice daily."
                ),
                "keywords": "Venezuela investor calendar, OFAC license expiration, Asamblea Nacional dates, BCV calendar",
                "canonical": canonical,
                "site_name": _s.site_name,
                "site_url": base,
                "locale": _s.site_locale,
                "og_image": f"{base}/static/og-image.png?v=3",
                "og_type": "website",
                "published_iso": _iso(_dt.utcnow()),
                "modified_iso": _iso(_dt.utcnow()),
            }
            jsonld = _json.dumps({
                "@context": "https://schema.org",
                "@graph": [{
                    "@type": "BreadcrumbList",
                    "itemListElement": [
                        {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{base}/"},
                        {"@type": "ListItem", "position": 2, "name": "Invest in Venezuela", "item": f"{base}/invest-in-venezuela"},
                        {"@type": "ListItem", "position": 3, "name": "Investor Calendar", "item": canonical},
                    ],
                }],
            }, ensure_ascii=False)

            template = _env.get_template("calendar.html.j2")
            html = template.render(
                calendar_events=calendar_events,
                seo=seo,
                jsonld=jsonld,
                current_year=_date.today().year,
                recent_briefings=_fetch_recent_briefings(),
            )
            return Response(html, mimetype="text/html")
        finally:
            db.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("calendar page render failed: %s", exc)
        abort(500)


@app.route("/travel")
@app.route("/travel/")
def travel_page():
    """
    Caracas travel hub — embassies, hotels, restaurants, hospitals,
    transport, security firms, money/comms, and the pre-trip + safety
    checklists. Static curated dataset; the travel-advisory banner
    is overridden live from the State Dept scraper when available.
    """
    try:
        from src.data import travel as travel_data
        from src.models import (
            ExternalArticleEntry, SessionLocal, SourceType, init_db,
        )
        from src.page_renderer import _env, _base_url, _iso, settings as _s
        from datetime import date as _date, datetime as _dt
        import copy as _copy
        import json as _json

        advisory = _copy.deepcopy(travel_data.TRAVEL_ADVISORY_SUMMARY)

        # Live override: pull the most recent State Dept scrape if present.
        try:
            init_db()
            db = SessionLocal()
            try:
                latest = (
                    db.query(ExternalArticleEntry)
                    .filter(ExternalArticleEntry.source == SourceType.TRAVEL_ADVISORY)
                    .order_by(ExternalArticleEntry.published_date.desc())
                    .first()
                )
            finally:
                db.close()
        except Exception as exc:
            logger.warning("travel advisory live fetch failed; using static fallback: %s", exc)
            latest = None

        if latest is not None:
            meta = latest.extra_metadata or {}
            level = meta.get("level")
            level_text = (meta.get("level_text") or "").strip()
            level_label_map = {
                1: "Exercise Normal Precautions",
                2: "Exercise Increased Caution",
                3: "Reconsider Travel",
                4: "Do Not Travel",
            }
            if isinstance(level, int) and 1 <= level <= 4:
                advisory["level"] = level
                advisory["label"] = level_text or level_label_map.get(level, advisory["label"])
                advisory["issued"] = latest.published_date.strftime("%B %-d, %Y")

        base = _base_url()
        canonical = f"{base}/travel"
        title = "Travel to Venezuela: Caracas Operational Briefing for Business Travellers"
        description = (
            "Embassies, hotels, restaurants, hospitals, ground transport, "
            "corporate security firms, SIM cards, money, pre-trip and safety "
            "checklists for foreign business travellers, journalists and NGO "
            "staff visiting Caracas. Compiled from US State Department, OSAC, "
            "MPPRE and embassy sources."
        )
        seo = {
            "title": title,
            "description": description,
            "keywords": (
                "travel to Venezuela, Caracas business travel, Caracas hotels, "
                "Caracas restaurants, Caracas safety, embassies in Caracas, "
                "Caracas airport transfer, Venezuela security firms, "
                "Caracas hospitals, Venezuela travel checklist"
            ),
            "canonical": canonical,
            "site_name": _s.site_name,
            "site_url": base,
            "locale": _s.site_locale,
            "og_image": f"{base}/static/og-image.png?v=3",
            "og_type": "article",
            "published_iso": _iso(_dt.utcnow()),
            "modified_iso": _iso(_dt.utcnow()),
        }

        faq = [
            {
                "q": "Is it safe to travel to Caracas right now?",
                "a": (
                    "The US State Department currently rates Venezuela at "
                    "Level 3 (Reconsider Travel), with Level 4 (Do Not Travel) "
                    "still applying to the Colombia border states (Apure, "
                    "Barinas, Táchira, Zulia). Caracas itself can be navigated "
                    "by experienced business travellers who stay in the safer "
                    "central-east corridor (Las Mercedes, Altamira, La Castellana, "
                    "El Rosal, Chacao), pre-arrange all transport, and engage "
                    "a corporate security advisory before travel."
                ),
            },
            {
                "q": "Where do business travellers stay in Caracas?",
                "a": (
                    "The most-used business hotels are the JW Marriott Caracas, "
                    "Renaissance Caracas La Castellana, Pestana Caracas, "
                    "Eurobuilding Hotel & Suites, Hotel Tamanaco InterContinental, "
                    "Hampton by Hilton Las Mercedes, and Embassy Suites Valle Arriba. "
                    "All are in safer neighbourhoods and have concierge desks "
                    "that arrange airport transfers."
                ),
            },
            {
                "q": "How do I get from Maiquetía airport (SVMI) to Caracas safely?",
                "a": (
                    "Always pre-arrange your airport transfer through your "
                    "hotel before flying — this is the single most important "
                    "logistics step. Never take a street taxi at Maiquetía. "
                    "Most major hotels in Caracas operate or contract marked "
                    "vehicles for the airport transfer when you quote your "
                    "flight number at booking."
                ),
            },
            {
                "q": "Is there a US embassy in Caracas?",
                "a": (
                    "Yes — the US Embassy in Caracas formally reopened on "
                    "March 30, 2026 after a seven-year closure, led by "
                    "Chargé d'Affaires Laura F. Dogu, at its original "
                    "location in Colinas de Valle Arriba. Emergency "
                    "consular support is available locally; however, the "
                    "consular section is still under restoration so routine "
                    "passport and visa services continue to be handled by "
                    "the Venezuela Affairs Unit at US Embassy Bogotá. "
                    "Emergency line for US citizens: 1-888-407-4747 toll-free "
                    "(US/Canada) or +1 202 501-4444 from outside the US."
                ),
            },
            {
                "q": "What currency should I bring to Venezuela?",
                "a": (
                    "US dollar cash in small undamaged denominations ($1, $5, "
                    "$10, $20) is the de-facto currency in Caracas — accepted "
                    "by hotels, restaurants, supermarkets and most taxis. Carry "
                    "a small amount of bolívar cash for street-level purchases. "
                    "Foreign credit cards work inconsistently. If you have a "
                    "US bank account, set up Zelle before travel — it functions "
                    "as the informal cashless rail."
                ),
            },
            {
                "q": "Do I need a visa to enter Venezuela?",
                "a": (
                    "Most Western nationalities (US, UK, EU) need a tourist or "
                    "business visa obtained in advance — there is no visa-on-arrival. "
                    "UK and Canadian citizens are an exception (visa-free up to 90 "
                    "days). Use our Venezuela visa requirements checker to confirm "
                    "current rules for your passport."
                ),
            },
            {
                "q": "Which corporate security firms operate in Venezuela?",
                "a": (
                    "Established international firms that cover Venezuela include "
                    "Control Risks, International SOS, Crisis24 (Garda World), "
                    "and Pinkerton. They can arrange protective services, vetted "
                    "drivers, and journey management. OSAC (US State Department) "
                    "is also a free public-private intelligence-sharing service "
                    "for US-incorporated companies."
                ),
            },
        ]

        graph = [
            {
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{base}/"},
                    {"@type": "ListItem", "position": 2, "name": "Invest in Venezuela", "item": f"{base}/invest-in-venezuela"},
                    {"@type": "ListItem", "position": 3, "name": "Travel to Venezuela", "item": canonical},
                ],
            },
            {
                "@type": "Article",
                "@id": f"{canonical}#article",
                "url": canonical,
                "headline": title,
                "description": description,
                "datePublished": seo["published_iso"],
                "dateModified": seo["modified_iso"],
                "author": {"@type": "Organization", "name": _s.site_name, "url": base + "/"},
                "publisher": {
                    "@type": "Organization",
                    "name": _s.site_name,
                    "url": base + "/",
                    "logo": {
                        "@type": "ImageObject",
                        "url": f"{base}/static/og-image.png?v=3",
                    },
                },
                "mainEntityOfPage": {"@type": "WebPage", "@id": canonical, "name": title},
            },
            {
                "@type": "FAQPage",
                "mainEntity": [
                    {
                        "@type": "Question",
                        "name": q["q"],
                        "acceptedAnswer": {"@type": "Answer", "text": q["a"]},
                    }
                    for q in faq
                ],
            },
        ]
        jsonld = _json.dumps(
            {"@context": "https://schema.org", "@graph": graph},
            ensure_ascii=False,
        )

        template = _env.get_template("travel.html.j2")
        html = template.render(
            seo=seo,
            jsonld=jsonld,
            advisory=advisory,
            registration_programs=travel_data.EMBASSY_REGISTRATION_PROGRAMS,
            embassies=travel_data.EMBASSIES,
            hotels=travel_data.HOTELS,
            restaurants=travel_data.RESTAURANTS,
            medical=travel_data.MEDICAL_PROVIDERS,
            transport=travel_data.GROUND_TRANSPORT,
            security=travel_data.SECURITY_FIRMS,
            communications=travel_data.COMMUNICATIONS,
            money=travel_data.MONEY_AND_BANKING,
            pre_trip=travel_data.PRE_TRIP_CHECKLIST,
            safety=travel_data.SAFETY_CHECKLIST,
            emergency=travel_data.EMERGENCY_NUMBERS,
            updated_label=_date.today().strftime("%B %-d, %Y"),
            current_year=_date.today().year,
            recent_briefings=_fetch_recent_briefings(),
        )
        return Response(html, mimetype="text/html")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("travel page render failed: %s", exc)
        abort(500)


@app.route("/travel/emergency-card")
@app.route("/travel/emergency-card/")
def travel_emergency_card():
    """
    Printable, two-page bilingual emergency card for visitors to Caracas.
    Front: Spanish-first "show this to a stranger" sheet (hospitals, embassies,
    big phone numbers, fillable medical + hotel info).
    Back: English "for me, when I'm rattled" reference (decision tree, safe
    corridor, six rules, money cheat-sheet, Spanish phrases).

    Designed to print double-sided on A4/Letter and fold into a passport.
    """
    try:
        from src.data import travel as travel_data
        from src.page_renderer import _env, _base_url
        from datetime import date as _date

        # The full embassy dataset uses long English labels and English country
        # names. For the front-of-card "show to a driver" panel we want short
        # Spanish country names and a 2-letter flag tag. Map only the missions
        # most relevant to typical English-speaking visitors; ordering matters
        # because the template slices the top N for the printed grid.
        country_es_map = {
            "United States": ("EE.UU.", "US"),
            "United Kingdom": ("Reino Unido", "UK"),
            "Canada": ("Canadá", "CA"),
            "Spain": ("España", "ES"),
            "France": ("Francia", "FR"),
            "Germany": ("Alemania", "DE"),
            "Italy": ("Italia", "IT"),
            "Netherlands": ("Países Bajos", "NL"),
            "Switzerland": ("Suiza", "CH"),
            "Brazil": ("Brasil", "BR"),
            "Colombia": ("Colombia", "CO"),
            "Mexico": ("México", "MX"),
        }
        embassies_top = []
        for e in travel_data.EMBASSIES:
            country_es, short = country_es_map.get(
                e["country"], (e["country"], e["country"][:2].upper())
            )
            # Short address label for the dropdown — first comma-separated
            # chunk of the address is usually the building / street, which
            # is enough for a user to recognise their embassy quickly.
            address = e.get("address", "")
            address_short = address.split(",")[0].strip() if address else ""
            embassies_top.append({
                "country_en": e["country"],
                "country_es": country_es,
                "short": short,
                "address": address,
                "address_short": address_short,
                "phone": e.get("phone", ""),
                "after_hours": e.get("after_hours", ""),
            })

        base = _base_url()
        seo = {
            "title": "Caracas Emergency Card — Printable Bilingual Pocket Sheet",
            "description": (
                "Two-page printable pocket card for visitors to Caracas. "
                "Spanish-first front shows hospitals, embassies and emergency "
                "numbers a taxi driver or stranger can act on; English back is "
                "a what-to-do reference if your phone is dead or stolen."
            ),
            "canonical": f"{base}/travel/emergency-card",
        }

        template = _env.get_template("emergency_card.html.j2")
        # Hotel pre-fill list for the dropdown — use the curated HOTELS
        # set as a starting point; the user always has an "Other" option.
        hotels_picker = []
        for h in travel_data.HOTELS:
            hotels_picker.append({
                "name": h.get("name", ""),
                "neighborhood": h.get("neighborhood", ""),
                "address": h.get("address", ""),
                "phone": h.get("phone", ""),
            })

        html = template.render(
            seo=seo,
            embassies_top=embassies_top,
            hotels_picker=hotels_picker,
            medical=travel_data.MEDICAL_PROVIDERS,
            emergency=travel_data.EMERGENCY_NUMBERS,
            updated_label=_date.today().strftime("%B %-d, %Y"),
        )
        return Response(html, mimetype="text/html")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("emergency card render failed: %s", exc)
        abort(500)


@app.route("/sectors/<slug>")
def sector_page(slug: str):
    """Evergreen sector landing page."""
    try:
        from src.models import BlogPost, LandingPage, SessionLocal, init_db
        from src.page_renderer import render_landing_page

        init_db()
        db = SessionLocal()
        try:
            page = (
                db.query(LandingPage)
                .filter(LandingPage.page_key == f"sector:{slug}")
                .first()
            )
            if not page:
                abort(404)

            normalized = slug.replace("-", "_")
            recent = (
                db.query(BlogPost)
                .filter(
                    (BlogPost.primary_sector == normalized)
                    | (BlogPost.primary_sector == slug)
                )
                .order_by(BlogPost.published_date.desc())
                .limit(8)
                .all()
            )
            html = render_landing_page(page, recent_briefings=recent)
            return Response(html, mimetype="text/html")
        finally:
            db.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("sector page render failed for slug=%s: %s", slug, exc)
        abort(500)


@app.route("/invest-in-venezuela")
@app.route("/invest-in-venezuela/")
def pillar_invest_in_venezuela():
    """Evergreen pillar landing page."""
    try:
        from src.models import BlogPost, LandingPage, SessionLocal, init_db
        from src.page_renderer import render_landing_page

        init_db()
        db = SessionLocal()
        try:
            page = (
                db.query(LandingPage)
                .filter(LandingPage.page_key == "pillar:invest-in-venezuela")
                .first()
            )
            if not page:
                abort(503, description="Pillar page not yet generated. Run `python scripts/generate_landing_pages.py --pillar`.")
            recent = (
                db.query(BlogPost)
                .order_by(BlogPost.published_date.desc())
                .limit(6)
                .all()
            )
            html = render_landing_page(page, recent_briefings=recent)
            return Response(html, mimetype="text/html")
        finally:
            db.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("pillar render failed: %s", exc)
        abort(500)


@app.route("/briefing")
@app.route("/briefing/")
def briefing_index():
    """List all long-form blog posts, newest first."""
    try:
        from src.models import BlogPost, SessionLocal, init_db
        from src.page_renderer import render_blog_index

        init_db()
        db = SessionLocal()
        try:
            posts = (
                db.query(BlogPost)
                .order_by(BlogPost.published_date.desc(), BlogPost.id.desc())
                .limit(200)
                .all()
            )
            html = render_blog_index(posts)
            return Response(html, mimetype="text/html")
        finally:
            db.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("briefing index render failed: %s", exc)
        abort(500)


@app.route("/briefing/feed.xml")
def briefing_feed():
    """Atom feed of the most recent blog posts."""
    try:
        from src.models import BlogPost, SessionLocal, init_db
        from src.page_renderer import render_blog_feed_xml

        init_db()
        db = SessionLocal()
        try:
            posts = (
                db.query(BlogPost)
                .order_by(BlogPost.published_date.desc(), BlogPost.id.desc())
                .limit(50)
                .all()
            )
            xml = render_blog_feed_xml(posts)
            return Response(xml, mimetype="application/atom+xml")
        finally:
            db.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("briefing feed render failed: %s", exc)
        abort(500)


# Per-slug in-memory cache for individual briefing analysis pages.
# Each post does 1-2 DB queries + a full template render (~700ms TTFB),
# but the content is essentially static once published — so cache the
# rendered HTML for 10 minutes per slug. Cap the cache at 200 entries
# so a crawler hitting every old briefing can't blow out memory.
_BRIEFING_POST_CACHE: dict[str, dict] = {}
_BRIEFING_POST_CACHE_TTL_SECONDS = 600
_BRIEFING_POST_CACHE_MAX_ENTRIES = 200


def _briefing_cache_get(slug: str) -> bytes | None:
    cached = _BRIEFING_POST_CACHE.get(slug)
    if not cached:
        return None
    if time.time() - cached.get("cached_at", 0.0) > _BRIEFING_POST_CACHE_TTL_SECONDS:
        return None
    return cached.get("body")


def _briefing_cache_put(slug: str, body: bytes) -> None:
    if len(_BRIEFING_POST_CACHE) >= _BRIEFING_POST_CACHE_MAX_ENTRIES:
        # Evict the oldest 25% of entries by cached_at timestamp.
        ordered = sorted(
            _BRIEFING_POST_CACHE.items(),
            key=lambda kv: kv[1].get("cached_at", 0.0),
        )
        for evict_slug, _ in ordered[: _BRIEFING_POST_CACHE_MAX_ENTRIES // 4]:
            _BRIEFING_POST_CACHE.pop(evict_slug, None)
    _BRIEFING_POST_CACHE[slug] = {"body": body, "cached_at": time.time()}


@app.route("/briefing/<slug>")
def briefing_post(slug: str):
    """Render a single blog post by slug."""
    # Serve from the per-slug cache first — these pages are essentially
    # static once published and the DB roundtrip + render dominates TTFB.
    cached_body = _briefing_cache_get(slug)
    if cached_body is not None:
        resp = Response(cached_body, mimetype="text/html")
        resp.headers["X-Page-Cache"] = "HIT"
        return resp

    try:
        from src.models import BlogPost, SessionLocal, init_db
        from src.page_renderer import render_blog_post

        init_db()
        db = SessionLocal()
        try:
            post = db.query(BlogPost).filter(BlogPost.slug == slug).first()
            if not post:
                abort(404)

            related_q = db.query(BlogPost).filter(BlogPost.id != post.id)
            if post.primary_sector:
                related_q = related_q.filter(BlogPost.primary_sector == post.primary_sector)
            related = (
                related_q.order_by(BlogPost.published_date.desc()).limit(5).all()
            )
            if len(related) < 3:
                fill = (
                    db.query(BlogPost)
                    .filter(BlogPost.id != post.id)
                    .filter(~BlogPost.id.in_([r.id for r in related]))
                    .order_by(BlogPost.published_date.desc())
                    .limit(5 - len(related))
                    .all()
                )
                related.extend(fill)

            html = render_blog_post(post, related=related)
            body = html.encode("utf-8") if isinstance(html, str) else html
            _briefing_cache_put(slug, body)
            resp = Response(body, mimetype="text/html")
            resp.headers["X-Page-Cache"] = "MISS"
            return resp
        finally:
            db.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("briefing post render failed for slug=%s: %s", slug, exc)
        abort(500)


@app.route("/robots.txt")
def robots_txt():
    """
    robots.txt — allow indexing of the public report and tools, point at
    the dynamic sitemap, and explicitly disallow API and health endpoints.
    """
    base = settings.site_url.rstrip("/")
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        "Disallow: /health\n"
        f"Sitemap: {base}/sitemap.xml\n"
        f"Sitemap: {base}/news-sitemap.xml\n"
    )
    return Response(body, mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap_xml():
    """
    Dynamic sitemap.xml. Reads recent analyzed entries from the DB and
    emits an entry per briefing alongside static pages (home, tools,
    sectors). Falls back to a minimal sitemap if the DB is unavailable.
    """
    from datetime import date as _date, datetime as _datetime, timezone as _tz, timedelta as _td
    from xml.sax.saxutils import escape as _xml_escape

    base = settings.site_url.rstrip("/")
    today_iso = _datetime.utcnow().replace(tzinfo=_tz.utc).date().isoformat()

    static_urls = [
        {"loc": f"{base}/", "lastmod": today_iso, "changefreq": "daily", "priority": "1.0"},
        {"loc": f"{base}/invest-in-venezuela", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.9"},
        {"loc": f"{base}/sanctions-tracker", "lastmod": today_iso, "changefreq": "daily", "priority": "0.9"},
        {"loc": f"{base}/sanctions/by-sector", "lastmod": today_iso, "changefreq": "daily", "priority": "0.9"},
        {"loc": f"{base}/sanctions/sector/military", "lastmod": today_iso, "changefreq": "daily", "priority": "0.85"},
        {"loc": f"{base}/sanctions/sector/economic", "lastmod": today_iso, "changefreq": "daily", "priority": "0.85"},
        {"loc": f"{base}/sanctions/sector/diplomatic", "lastmod": today_iso, "changefreq": "daily", "priority": "0.85"},
        {"loc": f"{base}/sanctions/sector/governance", "lastmod": today_iso, "changefreq": "daily", "priority": "0.85"},
        {"loc": f"{base}/sanctions/individuals", "lastmod": today_iso, "changefreq": "daily", "priority": "0.85"},
        {"loc": f"{base}/sanctions/entities", "lastmod": today_iso, "changefreq": "daily", "priority": "0.85"},
        {"loc": f"{base}/sanctions/vessels", "lastmod": today_iso, "changefreq": "daily", "priority": "0.8"},
        {"loc": f"{base}/sanctions/aircraft", "lastmod": today_iso, "changefreq": "daily", "priority": "0.8"},
        {"loc": f"{base}/calendar", "lastmod": today_iso, "changefreq": "daily", "priority": "0.7"},
        {"loc": f"{base}/travel", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.8"},
        {"loc": f"{base}/sources", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.6"},
        {"loc": f"{base}/briefing", "lastmod": today_iso, "changefreq": "daily", "priority": "0.9"},
        {"loc": f"{base}/tools", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.8"},
        {"loc": f"{base}/explainers", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.8"},
        {"loc": f"{base}/tools/bolivar-usd-exchange-rate", "lastmod": today_iso, "changefreq": "daily", "priority": "0.7"},
        {"loc": f"{base}/tools/ofac-venezuela-sanctions-checker", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.7"},
        {"loc": f"{base}/tools/public-company-venezuela-exposure-check", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.75"},
        {"loc": f"{base}/tools/sec-edgar-venezuela-impairment-search", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.75"},
        {"loc": f"{base}/companies", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.85"},
        {"loc": f"{base}/tools/ofac-venezuela-general-licenses", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.7"},
        {"loc": f"{base}/tools/caracas-safety-by-neighborhood", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.6"},
        {"loc": f"{base}/tools/venezuela-investment-roi-calculator", "lastmod": today_iso, "changefreq": "monthly", "priority": "0.6"},
        {"loc": f"{base}/tools/venezuela-visa-requirements", "lastmod": today_iso, "changefreq": "monthly", "priority": "0.6"},
    ]

    dynamic_urls: list[dict] = []
    sector_set: set[str] = set()
    try:
        from src.models import (
            SessionLocal,
            init_db,
            BlogPost,
            ExternalArticleEntry,
            AssemblyNewsEntry,
            GazetteStatus,
            LandingPage,
        )

        init_db()
        db = SessionLocal()
        try:
            cutoff = _date.today() - _td(days=settings.report_lookback_days)

            blog_posts = (
                db.query(BlogPost)
                .order_by(BlogPost.published_date.desc())
                .limit(500)
                .all()
            )
            for p in blog_posts:
                lastmod = (p.updated_at or p.created_at or p.published_date).strftime("%Y-%m-%d") if p.updated_at or p.created_at else p.published_date.isoformat()
                dynamic_urls.append({
                    "loc": f"{base}/briefing/{p.slug}",
                    "lastmod": lastmod,
                    "changefreq": "monthly",
                    "priority": "0.7",
                })

            landing_pages = db.query(LandingPage).all()
            for lp in landing_pages:
                lastmod = (lp.last_generated_at or lp.updated_at or lp.created_at)
                lastmod_iso = lastmod.strftime("%Y-%m-%d") if lastmod else today_iso
                priority = "0.9" if lp.page_type == "pillar" else "0.7"
                changefreq = "weekly" if lp.page_type == "pillar" else "monthly"
                dynamic_urls.append({
                    "loc": f"{base}{lp.canonical_path}",
                    "lastmod": lastmod_iso,
                    "changefreq": changefreq,
                    "priority": priority,
                })

            # NOTE: Per-SDN profile (/sanctions/<bucket>/<slug>) and per-
            # company exposure (/companies/<slug>/venezuela-exposure) URLs
            # are deliberately OMITTED from the sitemap. Both surfaces
            # together added ~917 long-tail templated URLs (414 SDN +
            # 503 S&P 500 companies) that diluted Google's crawl budget
            # — at the time of pruning the site had 1,014 URLs in the
            # sitemap and only ~10 indexed. The pages stay live and
            # crawlable; they're discoverable via the bucket index
            # pages (/sanctions/individuals etc.) and the /companies
            # index, so Google can still walk to them. We just stop
            # *advertising* every leaf to Google so crawl budget
            # concentrates on the briefings, sector pages, and pillar
            # surfaces. To re-add either bucket, restore the loops
            # below from git history (see commit pruning the sitemap).
            #
            # EXCEPTION — `_HIGH_DEMAND_PROFILE_SLUGS`: a tiny whitelist
            # of leaf URLs that have already proven they answer real
            # GSC queries. Adding them back to the sitemap is safe (it
            # doesn't undo the 917-URL prune) and tells Google "these
            # specific leaves matter — index them first". Only add a
            # slug here when GSC shows the URL has actually received
            # impressions; un-validated entries are a regression to
            # the pre-prune crawl-budget problem. To extend, append
            # the verified-live path; the loop below filters out any
            # entries whose page would 404, so a stale entry is a
            # silent no-op rather than a broken sitemap.

            _HIGH_DEMAND_PROFILE_SLUGS = (
                # Source: GSC last 90 days (April 2026 audit). Each URL
                # had non-zero impressions on a specific name/company
                # query but was dropped by the F prune. Slugs verified
                # live before commit — see the curl run in the audit
                # transcript dated 2026-04-20 if you need provenance.
                "/sanctions/individuals/carretero-napolitano-vicente-luis",
                "/companies/jacobs-solutions-j/venezuela-exposure",
                "/companies/simon-property-spg/venezuela-exposure",
                "/companies/citizens-financial-cfg/venezuela-exposure",
                "/companies/franklin-resources-ben/venezuela-exposure",
            )
            # Verify each whitelisted slug actually resolves to a live
            # page before advertising it. SDN profiles → looked up via
            # get_profile(bucket, slug); company exposure pages → looked
            # up via find_company(slug). A miss is logged + skipped, so
            # a stale slug silently drops out of the sitemap on the next
            # request instead of leaving a 404 advertised to Google.
            try:
                from src.data.sdn_profiles import get_profile as _get_sdn_profile
            except Exception as exc:
                logger.warning("sitemap whitelist: SDN module unavailable: %s", exc)
                _get_sdn_profile = None  # type: ignore

            try:
                from src.data.sp500_companies import find_company as _find_company
            except Exception as exc:
                logger.warning("sitemap whitelist: SP500 module unavailable: %s", exc)
                _find_company = None  # type: ignore

            for path in _HIGH_DEMAND_PROFILE_SLUGS:
                is_live = False
                if path.startswith("/sanctions/") and _get_sdn_profile is not None:
                    parts = path.strip("/").split("/")
                    if len(parts) == 3:
                        _, bucket, slug = parts
                        try:
                            is_live = _get_sdn_profile(bucket, slug) is not None
                        except Exception:
                            is_live = False
                elif path.startswith("/companies/") and _find_company is not None:
                    parts = path.strip("/").split("/")
                    if len(parts) == 3 and parts[2] == "venezuela-exposure":
                        try:
                            is_live = _find_company(parts[1]) is not None
                        except Exception:
                            is_live = False
                if not is_live:
                    logger.warning(
                        "sitemap whitelist: %s does not resolve, skipping",
                        path,
                    )
                    continue
                dynamic_urls.append({
                    "loc": f"{base}{path}",
                    "lastmod": today_iso,
                    "changefreq": "weekly",
                    "priority": "0.7",
                })

            ext_articles = (
                db.query(ExternalArticleEntry)
                .filter(ExternalArticleEntry.status == GazetteStatus.ANALYZED)
                .filter(ExternalArticleEntry.published_date >= cutoff)
                .order_by(ExternalArticleEntry.published_date.desc())
                .limit(500)
                .all()
            )
            assembly = (
                db.query(AssemblyNewsEntry)
                .filter(AssemblyNewsEntry.status == GazetteStatus.ANALYZED)
                .filter(AssemblyNewsEntry.published_date >= cutoff)
                .order_by(AssemblyNewsEntry.published_date.desc())
                .limit(500)
                .all()
            )

            import re as _re
            min_score = settings.analysis_min_relevance
            for item in list(ext_articles) + list(assembly):
                analysis = item.analysis_json or {}
                if analysis.get("relevance_score", 0) < min_score:
                    continue
                for sector in analysis.get("sectors", []) or []:
                    sector_slug = _re.sub(r"[^a-z0-9]+", "-", str(sector).lower()).strip("-")
                    if sector_slug:
                        sector_set.add(sector_slug)
        finally:
            db.close()
    except Exception as exc:
        logger.warning("sitemap dynamic generation failed, using static only: %s", exc)

    existing_urls = {u["loc"] for u in static_urls + dynamic_urls}
    for sector_slug in sorted(sector_set):
        url = f"{base}/sectors/{sector_slug}"
        if url not in existing_urls:
            static_urls.append({
                "loc": url,
                "lastmod": today_iso,
                "changefreq": "weekly",
                "priority": "0.6",
            })

    parts = ['<?xml version="1.0" encoding="UTF-8"?>']
    parts.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for u in static_urls + dynamic_urls:
        parts.append("<url>")
        parts.append(f"<loc>{_xml_escape(u['loc'])}</loc>")
        parts.append(f"<lastmod>{u['lastmod']}</lastmod>")
        parts.append(f"<changefreq>{u['changefreq']}</changefreq>")
        parts.append(f"<priority>{u['priority']}</priority>")
        parts.append("</url>")
    parts.append("</urlset>")
    return Response("".join(parts), mimetype="application/xml")


@app.route("/tearsheet/latest.pdf")
def tearsheet_latest():
    """
    Stable URL for today's Daily Venezuela Investor Tearsheet PDF.
    302-redirects to the Supabase Storage public URL where the cron
    just-in-time uploads it. Cached briefly so a single Supabase
    request fans out across many website visits.
    """
    from src.distribution.tearsheet import latest_tearsheet_public_url

    url = latest_tearsheet_public_url()
    if not url:
        abort(404)
    resp = redirect(url, code=302)
    resp.headers["Cache-Control"] = "public, max-age=300"
    return resp


@app.route("/tearsheet/<date_str>.pdf")
def tearsheet_dated(date_str: str):
    """Date-stamped permalink for a specific day's tearsheet (YYYY-MM-DD)."""
    from datetime import date as _date

    from src.distribution.tearsheet import tearsheet_url_for_date

    try:
        d = _date.fromisoformat(date_str)
    except ValueError:
        abort(404)
    url = tearsheet_url_for_date(d)
    if not url:
        abort(404)
    resp = redirect(url, code=302)
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


@app.route("/og/briefing/<slug>.png")
def briefing_og_image(slug: str):
    """Serve the per-briefing Open Graph card.

    Each BlogPost has its own 1200x630 PNG (rendered at creation time
    by src/og_image.py and persisted on `BlogPost.og_image_bytes`) so
    every share preview shows the briefing's actual headline rather
    than one generic site-wide tile.

    Cached aggressively — these never change once written. If a post
    is missing bytes (e.g. an older row not yet backfilled), we fall
    back to the static homepage OG image so previews still render.
    """
    try:
        from src.models import BlogPost, SessionLocal, init_db

        init_db()
        db = SessionLocal()
        try:
            row = (
                db.query(BlogPost.og_image_bytes)
                .filter(BlogPost.slug == slug)
                .first()
            )
            if row is None:
                abort(404)
            png_bytes = row[0]
            if not png_bytes:
                # No per-post bytes yet — redirect to the static fallback
                # so the share preview still renders something on-brand.
                fallback = f"{settings.site_url.rstrip('/')}/static/og-image.png?v=3"
                resp = redirect(fallback, code=302)
                resp.headers["Cache-Control"] = "public, max-age=300"
                return resp

            resp = Response(png_bytes, mimetype="image/png")
            # OG cards are content-addressed by slug and never mutate;
            # let CDNs and social-media link unfurlers cache forever.
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return resp
        finally:
            db.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("og card serve failed for slug=%s: %s", slug, exc)
        abort(500)


@app.route("/<key>.txt")
def indexnow_key_file(key: str):
    """
    Serve the IndexNow ownership-proof key file at the domain root.
    The IndexNow protocol requires that GET https://example.com/<KEY>.txt
    return the literal key as plain text — that's how Bing / Yandex /
    Seznam / etc. verify we own the host before accepting our pushed URLs.

    We only serve our one known key; any other /<thing>.txt 404s.
    """
    from src.config import settings

    configured = (settings.indexnow_key or "").strip()
    if configured and key == configured:
        return Response(configured, mimetype="text/plain")
    abort(404)


@app.route("/news-sitemap.xml")
def news_sitemap_xml():
    """
    Google News-spec sitemap. Per Google's documentation
    (https://developers.google.com/search/docs/crawling-indexing/sitemaps/news-sitemap)
    this must:

      - include only URLs published within the last 48 hours
      - cap at 1,000 URLs
      - use the news: XML namespace
      - emit <news:publication>, <news:publication_date>, <news:title>
        for every entry, plus optional <news:keywords>

    We feed the news-eligible BlogPost rows. The standard /sitemap.xml
    keeps the full backlog for general web search; this one is the fast,
    Top-Stories-eligible feed Google News auto-discovery polls.

    Falls back to an empty (but well-formed) news sitemap if the DB is
    unavailable — Google prefers an empty sitemap to a 500.
    """
    from datetime import datetime as _datetime, timezone as _tz, timedelta as _td
    from xml.sax.saxutils import escape as _xml_escape

    base = settings.site_url.rstrip("/")
    publication_name = settings.site_name
    publication_lang = (settings.site_locale or "en_US").split("_", 1)[0] or "en"

    cutoff = _datetime.now(_tz.utc) - _td(hours=48)

    items: list[dict] = []
    try:
        from src.models import SessionLocal, init_db, BlogPost

        init_db()
        db = SessionLocal()
        try:
            recent_posts = (
                db.query(BlogPost)
                .order_by(BlogPost.published_date.desc(), BlogPost.id.desc())
                .limit(1000)
                .all()
            )
            for p in recent_posts:
                pub_dt = p.created_at or p.updated_at
                if pub_dt is None:
                    pub_dt = _datetime.combine(
                        p.published_date, _datetime.min.time()
                    )
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=_tz.utc)
                if pub_dt < cutoff:
                    continue

                kws = p.keywords_json or []
                if isinstance(kws, str):
                    kws = [k.strip() for k in kws.split(",") if k.strip()]
                kws_str = ", ".join(kws[:10]) if kws else ""

                items.append({
                    "loc": f"{base}/briefing/{p.slug}",
                    "publication_date": pub_dt.isoformat(),
                    "title": (p.title or "")[:300],
                    "keywords": kws_str,
                })
        finally:
            db.close()
    except Exception as exc:
        logger.warning("news-sitemap dynamic generation failed, returning empty: %s", exc)

    parts = ['<?xml version="1.0" encoding="UTF-8"?>']
    parts.append(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">'
    )
    for it in items:
        parts.append("<url>")
        parts.append(f"<loc>{_xml_escape(it['loc'])}</loc>")
        parts.append("<news:news>")
        parts.append("<news:publication>")
        parts.append(f"<news:name>{_xml_escape(publication_name)}</news:name>")
        parts.append(f"<news:language>{_xml_escape(publication_lang)}</news:language>")
        parts.append("</news:publication>")
        parts.append(f"<news:publication_date>{_xml_escape(it['publication_date'])}</news:publication_date>")
        parts.append(f"<news:title>{_xml_escape(it['title'])}</news:title>")
        if it["keywords"]:
            parts.append(f"<news:keywords>{_xml_escape(it['keywords'])}</news:keywords>")
        parts.append("</news:news>")
        parts.append("</url>")
    parts.append("</urlset>")
    resp = Response("".join(parts), mimetype="application/xml")
    resp.headers["Cache-Control"] = "public, max-age=900"
    return resp


@app.route("/health")
def health():
    report = OUTPUT_DIR / "report.html"
    return {
        "status": "ok",
        "report_exists_local": report.exists(),
        "supabase_storage_read_enabled": supabase_storage_read_enabled(),
        "supabase_storage_write_enabled": supabase_storage_enabled(),
        "report_cached": _REPORT_CACHE["html"] is not None,
    }, 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=settings.server_port, debug=True)
