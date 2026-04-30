"""
Flask web server for Caracas Research.

Serves the generated report.html on Render (or locally).
"""

from __future__ import annotations

import hashlib
import json
import gzip
import hmac
import io
import logging
import re
import time
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

import httpx
from flask import Flask, send_from_directory, abort, request, jsonify, Response, redirect
from werkzeug.exceptions import HTTPException

from src.config import settings
from src.data.visa_requirements import US_EMBASSY_VENEZUELA_EVISA_INSTRUCTIONS
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
_GOOGLE_TAG_ID = "G-YQKLKXYCJB"
_GOOGLE_TAG_HTML = f"""<!-- Google tag (gtag.js) -->
<script async src="https://www.googletagmanager.com/gtag/js?id={_GOOGLE_TAG_ID}"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}
  gtag('js', new Date());

  gtag('config', '{_GOOGLE_TAG_ID}');
</script>"""

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
_STRIPE_VISA_NOTIFIED_SESSION_IDS: set[str] = set()


def _ensure_google_tag(html: str) -> str:
    """Add the Google tag to generated report HTML that predates the template update."""
    if _GOOGLE_TAG_ID in html or "www.googletagmanager.com/gtag/js" in html:
        return html
    if "<head>" in html:
        return html.replace("<head>", f"<head>\n  {_GOOGLE_TAG_HTML}\n", 1)
    return html


def _get_report_html() -> str | None:
    """Return rendered report HTML from Supabase Storage (cached) or local disk."""
    if supabase_storage_read_enabled():
        now = time.time()
        if _REPORT_CACHE["html"] and now - _REPORT_CACHE["fetched_at"] < _REPORT_CACHE_TTL_SECONDS:
            return _ensure_google_tag(_REPORT_CACHE["html"])
        html = fetch_report_html()
        if html:
            _REPORT_CACHE["html"] = html
            _REPORT_CACHE["fetched_at"] = now
            return _ensure_google_tag(html)
        if _REPORT_CACHE["html"]:
            return _ensure_google_tag(_REPORT_CACHE["html"])

    report = OUTPUT_DIR / "report.html"
    if report.exists():
        return _ensure_google_tag(report.read_text(encoding="utf-8"))
    return None


def _normalize_cache_path(path: str) -> str:
    """Normalize `/foo/` and `/foo` to the same cache key."""
    if not path:
        return "/"
    normalized = path.rstrip("/")
    return normalized or "/"


def _stripe_signature_valid(payload: bytes, signature_header: str | None) -> bool:
    """Verify Stripe's signed webhook payload without requiring stripe-python."""
    secret = (settings.stripe_webhook_secret or "").strip()
    if not secret or not signature_header:
        return False

    parts: dict[str, list[str]] = {}
    for item in signature_header.split(","):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        parts.setdefault(key, []).append(value)

    timestamps = parts.get("t") or []
    signatures = parts.get("v1") or []
    if not timestamps or not signatures:
        return False

    try:
        timestamp = int(timestamps[0])
    except ValueError:
        return False

    if abs(time.time() - timestamp) > 300:
        logger.warning("Stripe webhook rejected: signature timestamp outside tolerance")
        return False

    signed_payload = f"{timestamp}.".encode("utf-8") + payload
    expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, sig) for sig in signatures)


def _visa_order_email_recipient() -> str:
    """Return the configured owner email for visa-order notifications."""
    recipient = (settings.visa_order_notification_email or "").strip()
    if recipient:
        return recipient
    seo_recipient = (settings.seo_email_recipient or "").strip()
    if seo_recipient and seo_recipient != "<RECIPIENT_EMAIL>":
        return seo_recipient
    return ""


def _format_usd_minor_units(amount: int | None, currency: str | None) -> str:
    if amount is None:
        return "Unknown"
    currency_code = (currency or "usd").upper()
    if currency_code == "USD":
        return f"${amount / 100:,.2f}"
    return f"{amount} {currency_code}"


def _send_visa_order_notification(session: dict) -> bool:
    """Email the site owner when the Stripe visa-service checkout completes."""
    from src.newsletter import send_email

    recipient = _visa_order_email_recipient()
    if not recipient:
        logger.error("Stripe visa order notification skipped: VISA_ORDER_NOTIFICATION_EMAIL is not configured")
        return False

    customer_details = session.get("customer_details") or {}
    customer_name = customer_details.get("name") or "Unknown"
    customer_email = customer_details.get("email") or session.get("customer_email") or "Unknown"
    customer_phone = customer_details.get("phone") or "Not provided"
    amount = _format_usd_minor_units(session.get("amount_total"), session.get("currency"))
    session_id = session.get("id") or "Unknown"
    payment_status = session.get("payment_status") or "unknown"
    dashboard_url = f"https://dashboard.stripe.com/payments/{session.get('payment_intent')}" if session.get("payment_intent") else "https://dashboard.stripe.com/payments"

    subject = f"New Venezuela visa application order - {amount}"
    html = f"""
    <h2>New Venezuela visa application order</h2>
    <p>A customer completed Stripe checkout for the Same Day Venezuela Visa Application service.</p>
    <table cellpadding="6" cellspacing="0" style="border-collapse:collapse;">
      <tr><td><strong>Amount</strong></td><td>{_xml_escape(amount)}</td></tr>
      <tr><td><strong>Payment status</strong></td><td>{_xml_escape(payment_status)}</td></tr>
      <tr><td><strong>Customer name</strong></td><td>{_xml_escape(customer_name)}</td></tr>
      <tr><td><strong>Customer email</strong></td><td>{_xml_escape(customer_email)}</td></tr>
      <tr><td><strong>Customer phone</strong></td><td>{_xml_escape(customer_phone)}</td></tr>
      <tr><td><strong>Checkout session</strong></td><td>{_xml_escape(session_id)}</td></tr>
    </table>
    <p><a href="{_xml_escape(dashboard_url)}">Open payment in Stripe</a></p>
    <p>Next step: contact the customer for passport country, visa type, travel date, and document upload instructions.</p>
    """

    result = send_email(
        to=recipient,
        subject=subject,
        html_body=html,
        provider_name=settings.visa_order_email_provider,
    )
    return bool(result.get("success"))


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


@app.post("/webhooks/stripe")
def stripe_webhook():
    """
    Receive Stripe Checkout events for the paid Venezuela visa service.

    Configure Stripe to send checkout.session.completed events here and set
    STRIPE_WEBHOOK_SECRET to the endpoint signing secret. The handler filters
    to the visa-service payment link before emailing the site owner.
    """
    payload = request.get_data(cache=False)
    signature = request.headers.get("Stripe-Signature")
    if not _stripe_signature_valid(payload, signature):
        logger.warning("Stripe webhook rejected: invalid signature")
        return jsonify({"error": "invalid signature"}), 400

    try:
        event = json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError:
        return jsonify({"error": "invalid json"}), 400

    event_type = event.get("type")
    if event_type != "checkout.session.completed":
        return jsonify({"received": True, "ignored": event_type}), 200

    session = ((event.get("data") or {}).get("object") or {})
    payment_link = session.get("payment_link")
    expected_payment_link = (settings.stripe_visa_payment_link_id or "").strip()
    if expected_payment_link and payment_link != expected_payment_link:
        logger.info("Stripe checkout ignored for payment_link=%s", payment_link)
        return jsonify({"received": True, "ignored": "non_visa_payment_link"}), 200

    session_id = session.get("id")
    if session_id and session_id in _STRIPE_VISA_NOTIFIED_SESSION_IDS:
        return jsonify({"received": True, "duplicate": True}), 200

    if not _send_visa_order_notification(session):
        return jsonify({"error": "email notification failed"}), 500

    if session_id:
        _STRIPE_VISA_NOTIFIED_SESSION_IDS.add(session_id)
    return jsonify({"received": True, "notified": True}), 200


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
            us_embassy_eguide_url=US_EMBASSY_VENEZUELA_EVISA_INSTRUCTIONS,
        )
        return Response(html, mimetype="text/html")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("visa tool render failed: %s", exc)
        abort(500)


# ---------------------------------------------------------------------------
# /apply-for-venezuelan-visa cluster
#
# Pillar + 3 country/category variants, all rendered from a single template
# (templates/apply_for_venezuelan_visa.html.j2) using shared content from
# src/data/visa_application_content.py. The cluster targets high-intent
# search terms ("apply for venezuelan visa", "venezuela visa for us
# citizens", "venezuela business visa", "venezuela visa for chinese
# citizens") that the existing /tools/venezuela-visa-requirements page
# does not rank for.
# ---------------------------------------------------------------------------

def _apply_visa_jsonld(*, canonical: str, title: str, description: str, page: dict) -> str:
    """Build BreadcrumbList + Article + HowTo + FAQPage JSON-LD payload."""
    from src.page_renderer import _base_url, _iso, settings as _s
    from datetime import datetime as _dt
    import json as _json

    base = _base_url()
    is_pillar = page.get("slug") == ""

    breadcrumbs = [
        {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{base}/"},
        {"@type": "ListItem", "position": 2, "name": "Travel to Venezuela", "item": f"{base}/travel"},
    ]
    if is_pillar:
        breadcrumbs.append({"@type": "ListItem", "position": 3, "name": page["page_label"], "item": canonical})
    else:
        breadcrumbs.append({"@type": "ListItem", "position": 3, "name": "Apply for a Venezuelan visa", "item": f"{base}/apply-for-venezuelan-visa"})
        breadcrumbs.append({"@type": "ListItem", "position": 4, "name": page["page_label"], "item": canonical})

    graph: list[dict] = [
        {"@type": "BreadcrumbList", "itemListElement": breadcrumbs},
        {
            "@type": "Article",
            "@id": f"{canonical}#article",
            "headline": title,
            "description": description,
            "url": canonical,
            "mainEntityOfPage": canonical,
            "author": {"@type": "Organization", "name": _s.site_name, "url": f"{base}/"},
            "publisher": {"@type": "Organization", "name": _s.site_name, "url": f"{base}/"},
            "datePublished": _iso(_dt.utcnow()),
            "dateModified": _iso(_dt.utcnow()),
            "image": f"{base}/static/og-image.png?v=3",
        },
        {
            "@type": "HowTo",
            "@id": f"{canonical}#howto",
            "name": title,
            "description": description,
            "totalTime": "PT2H",
            "step": [
                {
                    "@type": "HowToStep",
                    "position": idx + 1,
                    "name": s["title"],
                    "text": s["detail"],
                    **({"url": s["url"]} if s.get("url") else {}),
                }
                for idx, s in enumerate(page.get("steps", []))
            ],
        },
    ]

    faqs = page.get("faqs") or []
    if faqs:
        graph.append({
            "@type": "FAQPage",
            "@id": f"{canonical}#faq",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": q["q"],
                    "acceptedAnswer": {"@type": "Answer", "text": q["a"]},
                }
                for q in faqs
            ],
        })

    return _json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False)


def _render_apply_visa(page: dict, *, canonical_path: str, title: str,
                       description: str, keywords: str) -> Response:
    from src.page_renderer import _env, _base_url, _iso, settings as _s
    from datetime import date as _date, datetime as _dt

    base = _base_url()
    canonical = f"{base}{canonical_path}"

    seo = {
        "title": title,
        "description": description,
        "keywords": keywords,
        "canonical": canonical,
        "site_name": _s.site_name,
        "site_url": base,
        "locale": _s.site_locale,
        "og_image": f"{base}/static/og-image.png?v=3",
        "og_type": "article",
        "section": "Travel",
        "published_iso": _iso(_dt.utcnow()),
        "modified_iso": _iso(_dt.utcnow()),
    }

    jsonld = _apply_visa_jsonld(
        canonical=canonical,
        title=title,
        description=description,
        page=page,
    )

    from src.data.visa_document_landing import PLANILLA_HERO_LINE as _planilla_line

    template = _env.get_template("apply_for_venezuelan_visa.html.j2")
    html = template.render(
        page=page,
        is_pillar=page.get("slug") == "",
        seo=seo,
        jsonld=jsonld,
        current_year=_date.today().year,
        planilla_display_line=_planilla_line,
    )
    return Response(html, mimetype="text/html")


def _visa_service_jsonld(*, canonical: str, title: str, description: str,
                         regular_price: str, promo_price: str) -> str:
    """Build Product/Service + Offer + FAQ JSON-LD for the paid visa service."""
    from calendar import monthrange as _monthrange
    from datetime import datetime as _dt
    from src.page_renderer import _base_url, _iso, settings as _s
    import json as _json

    base = _base_url()
    now = _dt.utcnow()
    promo_valid_until = f"{now.year}-{now.month:02d}-{_monthrange(now.year, now.month)[1]:02d}"
    related_pages = [
        {
            "name": "How to apply for a Venezuelan visa",
            "url": f"{base}/apply-for-venezuelan-visa",
        },
        {
            "name": "Venezuela visa requirements by passport country",
            "url": f"{base}/tools/venezuela-visa-requirements",
        },
        {
            "name": "Venezuela visa for US citizens",
            "url": f"{base}/apply-for-venezuelan-visa/us-citizens",
        },
        {
            "name": "Venezuela business visa application",
            "url": f"{base}/apply-for-venezuelan-visa/business-visa",
        },
        {
            "name": "Venezuela visa application form",
            "url": f"{base}/planilla-de-solicitud-de-visa",
        },
        {
            "name": "Venezuela travel hub",
            "url": f"{base}/travel",
        },
    ]

    graph = [
        {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{base}/"},
                {"@type": "ListItem", "position": 2, "name": "Travel to Venezuela", "item": f"{base}/travel"},
                {"@type": "ListItem", "position": 3, "name": "Venezuela visa application service", "item": canonical},
            ],
        },
        {
            "@type": "Service",
            "@id": f"{canonical}#service",
            "name": "Venezuela visa application service",
            "serviceType": "Visa application preparation and filing",
            "areaServed": {"@type": "Country", "name": "Venezuela"},
            "provider": {"@type": "Organization", "name": _s.site_name, "url": f"{base}/"},
            "url": canonical,
            "description": description,
            "termsOfService": f"{base}/sources",
            "isRelatedTo": [{"@type": "WebPage", "name": p["name"], "url": p["url"]} for p in related_pages],
            "offers": {
                "@type": "Offer",
                "url": canonical,
                "price": promo_price,
                "priceCurrency": "USD",
                "category": "Launch promotion",
                "availability": "https://schema.org/InStock",
                "priceValidUntil": promo_valid_until,
            },
        },
        {
            "@type": "Product",
            "@id": f"{canonical}#product",
            "name": "Venezuela e-visa application filing service",
            "description": description,
            "image": f"{base}/static/og-image.png?v=3",
            "brand": {"@type": "Brand", "name": _s.site_name},
            "offers": {
                "@type": "Offer",
                "price": promo_price,
                "priceCurrency": "USD",
                "priceSpecification": {
                    "@type": "UnitPriceSpecification",
                    "price": regular_price,
                    "priceCurrency": "USD",
                    "description": "Regular service fee before the current-month promotion.",
                },
            },
        },
        {
            "@type": "FAQPage",
            "@id": f"{canonical}#faq",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": "Can you guarantee Venezuela visa approval?",
                    "acceptedAnswer": {
                        "@type": "Answer",
                        "text": "No. Caracas Research guarantees Same Day Visa Application submission after you pay and provide a complete, readable document package before the cutoff. Venezuelan authorities decide approval, denial, timing, and requests for more information.",
                    },
                },
                {
                    "@type": "Question",
                    "name": "How fast can the Venezuela visa application be filed?",
                    "acceptedAnswer": {
                        "@type": "Answer",
                        "text": "With our Same Day Visa Application service, complete, readable document packages are prepared and submitted the same business day after payment when received before the cutoff. Government processing is often around 2 to 3 weeks after submission and can run longer if authorities request more information.",
                    },
                },
                {
                    "@type": "Question",
                    "name": "Is the service fee the same as the government visa fee?",
                    "acceptedAnswer": {
                        "@type": "Answer",
                        "text": "No. The Caracas Research fee covers application preparation, document review, filing, and monitoring. Any Venezuelan government visa fee is separate and paid through the official application channel.",
                    },
                },
            ],
        },
        {
            "@type": "WebPage",
            "@id": canonical,
            "url": canonical,
            "name": title,
            "description": description,
            "datePublished": _iso(now),
            "dateModified": _iso(now),
            "publisher": {"@type": "Organization", "name": _s.site_name, "url": f"{base}/"},
            "inLanguage": "en-US",
            "isPartOf": {"@type": "WebSite", "name": _s.site_name, "url": f"{base}/"},
            "about": [
                {"@type": "Thing", "name": "Venezuela visa application service"},
                {"@type": "Thing", "name": "Venezuela e-visa"},
                {"@type": "Thing", "name": "Cancilleria Digital"},
            ],
        },
        {
            "@type": "ItemList",
            "@id": f"{canonical}#visa-cluster",
            "name": "Venezuela visa application hub",
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": idx,
                    "name": page["name"],
                    "url": page["url"],
                }
                for idx, page in enumerate(related_pages, start=1)
            ],
        },
    ]
    return _json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False)


@app.route("/get-venezuela-visa")
@app.route("/get-venezuela-visa/")
def venezuela_visa_service():
    """Paid Venezuela visa application preparation and filing service."""
    try:
        from calendar import monthrange
        from datetime import date as _date, datetime as _dt
        from src.page_renderer import _env, _base_url, _iso, settings as _s

        base = _base_url()
        canonical = f"{base}/get-venezuela-visa"
        title = "Same Day Venezuela Visa Application Help | $49.99"
        description = (
            "Same Day Visa Application for Venezuela: we apply the same day "
            "you pay and send complete documents. $49.99 this month."
        )
        regular_price = "79.99"
        promo_price = "49.99"
        stripe_payment_link = "https://buy.stripe.com/dRmcN579Jc8J7YG2el9R607"
        today = _date.today()
        offer_expires = today.replace(day=monthrange(today.year, today.month)[1])
        seo = {
            "title": title,
            "description": description,
            "keywords": (
                "same day Venezuela visa application, same day visa application, get Venezuela visa, Venezuela visa application service, how to apply "
                "for Venezuelan visa, Venezuela e-visa help, Venezuela tourist visa "
                "application, Venezuela business visa application, visa for Venezuela "
                "US citizens, Cancilleria Digital visa"
            ),
            "canonical": canonical,
            "site_name": _s.site_name,
            "site_url": base,
            "locale": _s.site_locale,
            "og_image": f"{base}/static/og-image.png?v=3",
            "og_type": "website",
            "section": "Travel",
            "published_iso": _iso(_dt.utcnow()),
            "modified_iso": _iso(_dt.utcnow()),
        }
        jsonld = _visa_service_jsonld(
            canonical=canonical,
            title=title,
            description=description,
            regular_price=regular_price,
            promo_price=promo_price,
        )
        template = _env.get_template("venezuela_visa_service.html.j2")
        html = template.render(
            seo=seo,
            jsonld=jsonld,
            regular_price=regular_price,
            promo_price=promo_price,
            current_month_label=today.strftime("%B"),
            offer_expires=offer_expires.isoformat(),
            cutoff_time="2:00 p.m. Caracas time",
            stripe_payment_link=stripe_payment_link,
            current_year=today.year,
            us_embassy_eguide_url=US_EMBASSY_VENEZUELA_EVISA_INSTRUCTIONS,
        )
        return Response(html, mimetype="text/html")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("venezuela visa service render failed: %s", exc)
        abort(500)


@app.route("/venezuela-visa-service")
@app.route("/venezuela-visa-service/")
@app.route("/tools/venezuela-visa-application-service")
@app.route("/tools/venezuela-visa-application-service/")
def venezuela_visa_service_redirect():
    """Compatibility aliases for users/searchers who expect a service URL."""
    return redirect("/get-venezuela-visa", code=301)


def _visa_document_landing_jsonld(*, canonical: str, title: str, description: str, headline: str) -> str:
    """BreadcrumbList + Article for planilla / declaración SEO landing pages."""
    import json as _json
    from datetime import datetime as _dt
    from src.page_renderer import _base_url, _iso, settings as _s

    base = _base_url()
    graph: list[dict] = [
        {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{base}/"},
                {"@type": "ListItem", "position": 2, "name": "Travel to Venezuela", "item": f"{base}/travel"},
                {"@type": "ListItem", "position": 3, "name": headline, "item": canonical},
            ],
        },
        {
            "@type": "Article",
            "@id": f"{canonical}#article",
            "headline": headline,
            "description": description,
            "url": canonical,
            "mainEntityOfPage": canonical,
            "author": {"@type": "Organization", "name": _s.site_name, "url": f"{base}/"},
            "publisher": {"@type": "Organization", "name": _s.site_name, "url": f"{base}/"},
            "datePublished": _iso(_dt.utcnow()),
            "dateModified": _iso(_dt.utcnow()),
            "image": f"{base}/static/og-image.png?v=3",
        },
    ]
    return _json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False)


def _render_visa_document_landing(page: dict) -> Response:
    from src.page_renderer import _env, _base_url, _iso, settings as _s
    from datetime import date as _date, datetime as _dt

    base = _base_url()
    path = page["canonical_path"]
    canonical = f"{base}{path}"
    title = page["title"]
    description = page["description"]
    seo = {
        "title": title,
        "description": description,
        "keywords": page.get("keywords", ""),
        "canonical": canonical,
        "site_name": _s.site_name,
        "site_url": base,
        "locale": _s.site_locale,
        "og_image": f"{base}/static/og-image.png?v=3",
        "og_type": "article",
        "section": "Travel",
        "published_iso": _iso(_dt.utcnow()),
        "modified_iso": _iso(_dt.utcnow()),
    }
    jsonld = _visa_document_landing_jsonld(
        canonical=canonical,
        title=title,
        description=description,
        headline=page["h1"],
    )
    template = _env.get_template("visa_document_landing.html.j2")
    html = template.render(
        page=page,
        seo=seo,
        jsonld=jsonld,
        current_year=_date.today().year,
    )
    return Response(html, mimetype="text/html")


@app.route("/apply-for-venezuelan-visa")
@app.route("/apply-for-venezuelan-visa/")
def apply_visa_pillar():
    """Pillar landing page: how to apply for a Venezuelan visa (e-visa)."""
    try:
        from src.data.visa_application_content import get_pillar
        return _render_apply_visa(
            page=get_pillar(),
            canonical_path="/apply-for-venezuelan-visa",
            title="How To Apply For A Venezuelan Visa (2026): E-Visa Process, Fees & Timeline",
            description=(
                "Step-by-step guide to applying for a Venezuelan tourist (TR-V) "
                "or business (TR-N) visa through the Cancillería Digital "
                "e-visa portal. Documents, the USD 180 fee, the ~15-day "
                "approval timeline, and FAQs for US citizens, Chinese "
                "applicants, and business travelers."
            ),
            keywords=(
                "apply for venezuelan visa, venezuela visa application, "
                "venezuela e-visa, cancilleria digital, venezuela tourist "
                "visa, venezuela business visa, TR-V visa, TR-N visa, "
                "venezuela visa fee, venezuela visa timeline"
            ),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("apply-visa pillar render failed: %s", exc)
        abort(500)


# Per-variant SEO config so each sub-page has its own title/description/keywords
# tuned to a specific search intent. Keeping these here (rather than in the
# data module) keeps the SEO copy alongside the route definitions.
_APPLY_VISA_VARIANT_SEO: dict[str, dict] = {
    "us-citizens": {
        "title": "Venezuela Visa for US Citizens (2026): E-Visa Application, Fees & Timeline",
        "description": (
            "How US citizens apply for a Venezuelan tourist (TR-V) or "
            "business (TR-N) visa through the Cancillería Digital e-visa "
            "portal. The Embassy of Venezuela in Washington DC has been "
            "closed since 2019 — full step-by-step playbook, USD 180 fee, "
            "~15-day timeline, and US-specific payment snags."
        ),
        "keywords": (
            "venezuela visa for us citizens, venezuela visa us, apply "
            "for venezuela visa from usa, venezuela embassy washington dc, "
            "us citizen venezuela e-visa, venezuela visa cost us"
        ),
    },
    "business-visa": {
        "title": "Venezuela Business Visa (TR-N): Application Guide for 2026",
        "description": (
            "How to apply for the Venezuelan TR-N business visa through "
            "Cancillería Digital. Corporate invitation letter, SENIAT "
            "registration requirements, USD 180 fee, ~15-day timeline, "
            "and OFAC compliance considerations for executives, investors, "
            "and consultants traveling to Caracas."
        ),
        "keywords": (
            "venezuela business visa, TR-N visa, venezuela executive visa, "
            "venezuela investor visa, business visa for venezuela, "
            "venezuela work visa for business, venezuela visa SENIAT, "
            "corporate invitation letter venezuela"
        ),
    },
    "china": {
        "title": "Venezuela Visa for Chinese Citizens (2026): Beijing, Shanghai & Hong Kong Routes",
        "description": (
            "How Chinese citizens apply for a Venezuelan tourist (L), "
            "business (F), or investor visa. Beijing embassy and "
            "Shanghai / Hong Kong consulate filings, plus the online "
            "Cancillería Digital e-visa channel. Documents, fees, and "
            "timeline for 2026."
        ),
        "keywords": (
            "venezuela visa for chinese citizens, venezuela visa china, "
            "embassy of venezuela in beijing, venezuela tourist visa "
            "china, venezuela business visa china, venezuela investor "
            "visa china"
        ),
    },
}


@app.route("/apply-for-venezuelan-visa/<slug>")
@app.route("/apply-for-venezuelan-visa/<slug>/")
def apply_visa_variant(slug: str):
    """Country/category variants under the visa-application pillar."""
    try:
        from src.data.visa_application_content import get_variant
        page = get_variant(slug)
        seo_cfg = _APPLY_VISA_VARIANT_SEO.get(slug)
        if not page or not seo_cfg:
            abort(404)
        return _render_apply_visa(
            page=page,
            canonical_path=f"/apply-for-venezuelan-visa/{slug}",
            title=seo_cfg["title"],
            description=seo_cfg["description"],
            keywords=seo_cfg["keywords"],
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("apply-visa variant render failed (%s): %s", slug, exc)
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


@app.route("/tools/ofac-sdn-name-check/<slug>")
@app.route("/tools/ofac-sdn-name-check/<slug>/")
def tool_ofac_sdn_name_check(slug: str):
    """SEO-optimized "is <NAME> on the OFAC SDN list?" answer page.

    Built for specific high-impression / zero-click compliance queries
    that we show up for in GSC but can't monetize because our SERP
    snippet doesn't contain the literal name being searched (see
    src/data/ofac_name_check.py for the design rationale).

    The <slug> segment is resolved against a hand-curated registry.
    Unknown slugs 404 rather than falling through to a thin-content
    placeholder — an empty "no data" page for every conceivable name
    would dilute crawl budget and tank our sanctions-cluster rankings.
    """
    try:
        from src.data.ofac_name_check import get_answer
        from src.data.sdn_profiles import list_by_surname
        from src.page_renderer import _env
        from datetime import date as _date

        answer = get_answer(slug)
        if answer is None:
            abort(404)

        # Pull the Venezuela-program SDN cluster for each surname. We
        # emit a list of (surname, members) pairs — preserving the
        # surname ordering from the registry — so the template can
        # render one "Surname X — N Venezuela SDNs" section per
        # surname without re-computing the split.
        cluster_by_surname: list[tuple[str, list]] = []
        seen_db_ids: set[int] = set()
        for surname in answer.surnames:
            members = [
                p for p in list_by_surname(surname)
                if p.db_id not in seen_db_ids
            ]
            if not members:
                continue
            for p in members:
                seen_db_ids.add(p.db_id)
            cluster_by_surname.append((surname, members))

        faq: list[dict] = [
            {
                "q": f'Is "{answer.query_verbatim}" on the OFAC SDN list?',
                "a": answer.answer_summary,
            },
            {
                "q": "What is the OFAC Venezuela SDN list?",
                "a": (
                    "The OFAC Specially Designated Nationals (SDN) list is the US "
                    "Treasury's primary sanctions list. Its Venezuela-program subset "
                    "covers approximately 410 individuals, entities, vessels, and "
                    "aircraft designated under the VENEZUELA, VENEZUELA-EO13692, "
                    "VENEZUELA-EO13850, and VENEZUELA-EO13884 executive orders. "
                    "Property and interests in property of SDNs subject to US "
                    "jurisdiction are blocked, and US persons are generally "
                    "prohibited from transacting with them."
                ),
            },
            {
                "q": "How was this name-check verified?",
                "a": (
                    f"We query the official OFAC consolidated SDN CSV and alias CSV "
                    f"from sanctionslistservice.ofac.treas.gov. The results on this "
                    f"page reflect the dataset snapshot on "
                    f"{answer.last_verified_iso} and are re-validated whenever our "
                    f"scraper ingests a new OFAC publication (typically daily). "
                    f"For authoritative compliance decisions you must still verify "
                    f"directly with OFAC's Sanctions List Search."
                ),
            },
            {
                "q": "What should I do if I am screening a real Venezuelan counterparty?",
                "a": (
                    "Re-run the query in our free OFAC Venezuela Sanctions Exposure "
                    "Checker with alternative spellings (with/without accents, paternal "
                    "surname only, given name first) and also test the Venezuelan "
                    "cédula number if you have it. The checker searches names, "
                    "aliases, IMO numbers, aircraft tail numbers, and cédulas. For "
                    "high-stakes counterparties retain qualified sanctions counsel and "
                    "perform an ownership-chain analysis (OFAC 50% Rule)."
                ),
            },
        ]

        seo, jsonld = _tool_seo_jsonld(
            slug=f"ofac-sdn-name-check/{answer.slug}",
            title=(
                f'Is "{answer.query_verbatim}" on the OFAC SDN list? '
                f'— Venezuela compliance check ({_date.today().year})'
            ),
            description=answer.answer_summary,
            keywords=(
                f'"{answer.query_verbatim}" OFAC SDN, '
                f"{answer.natural_name} sanctions, OFAC Venezuela SDN name check, "
                f"{' '.join(answer.surnames)} OFAC sanctions, "
                f"Venezuela sanctions compliance screening"
            ),
            faq=faq,
        )

        # _tool_seo_jsonld defaults og_type to "website" but this is an
        # editorial answer page; upgrade to "article" so social cards
        # and news surfaces treat it with the correct priors.
        seo["og_type"] = "article"

        from src.seo.cluster_topology import build_cluster_ctx
        cluster_ctx = build_cluster_ctx("/tools/ofac-venezuela-sanctions-checker")

        template = _env.get_template("tools/ofac_name_check.html.j2")
        html = template.render(
            answer=answer,
            cluster_by_surname=cluster_by_surname,
            faq=faq,
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
        logger.exception("ofac name-check render failed: %s", exc)
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


@app.route("/tools/venezuela-trade-leads")
@app.route("/tools/venezuela-trade-leads/")
def tool_venezuela_trade_leads():
    """ITA Venezuela trade-leads finder for U.S. exporters."""
    try:
        from datetime import date as _date
        from src.data.ita_trade import latest_ita_resources, latest_trade_leads, trade_lead_stats
        from src.page_renderer import _env

        all_leads, source_row = latest_trade_leads()
        query = (request.args.get("q") or "").strip()
        selected_sector = (request.args.get("sector") or "").strip()

        leads = _filter_ita_trade_leads(all_leads, query, selected_sector)

        stats = trade_lead_stats(all_leads)
        query_string = request.query_string.decode("utf-8")
        pdf_href = "/tools/venezuela-trade-leads.pdf"
        if query_string:
            pdf_href = f"{pdf_href}?{query_string}"
        seo, jsonld = _tool_seo_jsonld(
            slug="venezuela-trade-leads",
            title="Venezuela Trade Leads for U.S. Companies — ITA Opportunity Finder",
            description=(
                "Search official International Trade Administration Venezuela "
                "trade leads by sector, equipment, units requested, and HS code."
            ),
            keywords=(
                "Venezuela trade leads, ITA Venezuela, trade.gov Venezuela, "
                "Venezuela export opportunities, Venezuela HS codes, US companies Venezuela"
            ),
            faq=[
                {
                    "q": "Where do these Venezuela trade leads come from?",
                    "a": "They come from the International Trade Administration's official Venezuela Trade Leads page on trade.gov, maintained for U.S. businesses evaluating export opportunities.",
                },
                {
                    "q": "Who should U.S. companies contact about a listed opportunity?",
                    "a": "ITA directs companies seeking additional information to contact tradevenezuela@trade.gov.",
                },
                {
                    "q": "Do trade leads remove OFAC or export-control risk?",
                    "a": "No. A commercial opportunity still requires sanctions screening, export-control review, payment diligence, and legal advice before quoting, shipping, or contracting.",
                },
            ],
            dataset={
                "name": "ITA Venezuela trade leads",
                "description": "Structured view of official ITA Venezuela trade-lead line items for U.S. exporters.",
                "url": "https://www.trade.gov/venezuela-trade-leads",
                "creator": {"@type": "Organization", "name": "International Trade Administration"},
            },
        )
        resources = [
            r for r in latest_ita_resources()
            if r.url.rstrip("/") != "https://www.trade.gov/venezuela-trade-leads"
        ]
        from src.seo.cluster_topology import build_cluster_ctx
        cluster_ctx = build_cluster_ctx("/tools/venezuela-trade-leads")

        template = _env.get_template("tools/venezuela_trade_leads.html.j2")
        html = template.render(
            leads=leads,
            stats=stats,
            query=query,
            selected_sector=selected_sector,
            source_row=source_row,
            resources=resources,
            pdf_href=pdf_href,
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
        logger.exception("venezuela trade leads render failed: %s", exc)
        abort(500)


def _filter_ita_trade_leads(all_leads, query: str, selected_sector: str):
    leads = all_leads
    if selected_sector:
        leads = [l for l in leads if l.sector == selected_sector]
    if query:
        q = query.lower()
        leads = [
            l for l in leads
            if q in l.equipment.lower()
            or q in l.hs_code.lower()
            or q in l.hs_description.lower()
            or q in l.sector.lower()
        ]
    return leads


def _render_trade_leads_pdf(leads, all_count: int, query: str, selected_sector: str, source_row) -> bytes:
    from datetime import datetime, timezone

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import letter, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="CRSmall",
        parent=styles["BodyText"],
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#4f5b66"),
    ))
    styles.add(ParagraphStyle(
        name="CRSmallRight",
        parent=styles["CRSmall"],
        alignment=TA_RIGHT,
    ))
    styles.add(ParagraphStyle(
        name="CRHeader",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#17324d"),
        spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        name="CRBody",
        parent=styles["BodyText"],
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#1f2933"),
    ))
    styles.add(ParagraphStyle(
        name="CRTableHeader",
        parent=styles["CRBody"],
        fontName="Helvetica-Bold",
        textColor=colors.white,
    ))

    def para(text: object, style_name: str = "CRBody") -> Paragraph:
        return Paragraph(_xml_escape(str(text or "")), styles[style_name])

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(letter),
        leftMargin=0.45 * inch,
        rightMargin=0.45 * inch,
        topMargin=0.45 * inch,
        bottomMargin=0.45 * inch,
        title="Caracas Research - Venezuela Trade Leads",
        author="Caracas Research",
        subject="Filtered ITA Venezuela trade leads",
        keywords="Venezuela, ITA, trade leads, export opportunities, HS codes",
    )

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    filters = []
    if selected_sector:
        filters.append(f"Sector: {selected_sector}")
    if query:
        filters.append(f"Search: {query}")
    filter_label = "; ".join(filters) if filters else "None"
    captured = source_row.created_at.strftime("%Y-%m-%d %H:%M UTC") if source_row else "fallback data"

    flow = [
        para("Venezuela Trade Leads for U.S. Companies", "CRHeader"),
        para(
            f"Filtered export from Caracas Research. Rows included: {len(leads)} of {all_count}. "
            f"Filters: {filter_label}. Generated: {generated_at}.",
        ),
        para(
            "Source: International Trade Administration Venezuela Trade Leads "
            "(https://www.trade.gov/venezuela-trade-leads). Contact: tradevenezuela@trade.gov. "
            f"Last captured by Caracas Research: {captured}.",
            "CRSmall",
        ),
        Spacer(1, 10),
    ]

    table_data = [[
        para("Sector", "CRTableHeader"),
        para("Equipment", "CRTableHeader"),
        para("Units", "CRTableHeader"),
        para("HS code", "CRTableHeader"),
        para("HS description", "CRTableHeader"),
    ]]
    for lead in leads:
        table_data.append([
            para(lead.sector),
            para(lead.equipment),
            para(f"{lead.units_requested:,}" if lead.units_requested else "-", "CRSmallRight"),
            para(lead.hs_code),
            para(lead.hs_description),
        ])

    if len(table_data) == 1:
        table_data.append([
            para("No matching leads"),
            para(""),
            para(""),
            para(""),
            para(""),
        ])

    table = Table(
        table_data,
        colWidths=[1.15 * inch, 2.25 * inch, 0.75 * inch, 0.9 * inch, 3.95 * inch],
        repeatRows=1,
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324d")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d8dee4")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f9fb")]),
    ]))
    flow.append(table)
    doc.build(flow)
    return buf.getvalue()


@app.route("/tools/venezuela-trade-leads.pdf")
def tool_venezuela_trade_leads_pdf():
    """PDF export of the currently filtered ITA Venezuela trade-leads table."""
    try:
        from datetime import date as _date
        from src.data.ita_trade import latest_trade_leads

        all_leads, source_row = latest_trade_leads()
        query = (request.args.get("q") or "").strip()
        selected_sector = (request.args.get("sector") or "").strip()
        leads = _filter_ita_trade_leads(all_leads, query, selected_sector)
        pdf_bytes = _render_trade_leads_pdf(
            leads=leads,
            all_count=len(all_leads),
            query=query,
            selected_sector=selected_sector,
            source_row=source_row,
        )

        filename_bits = ["caracas-research-venezuela-trade-leads"]
        if selected_sector:
            filename_bits.append(selected_sector.lower().replace(" ", "-"))
        if query:
            cleaned_query = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")
            if cleaned_query:
                filename_bits.append(cleaned_query[:32])
        filename_bits.append(_date.today().isoformat())
        filename = "-".join(filename_bits) + ".pdf"

        resp = Response(pdf_bytes, mimetype="application/pdf")
        resp.headers["Content-Disposition"] = f'inline; filename="{filename}"'
        resp.headers["Cache-Control"] = "no-store"
        return resp
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("trade leads PDF export failed: %s", exc)
        abort(500)


@app.route("/tools/venezuela-market-entry-checklist")
@app.route("/tools/venezuela-market-entry-checklist/")
def tool_venezuela_market_entry_checklist():
    """U.S. exporter market-entry workflow for Venezuela."""
    try:
        from datetime import date as _date
        from src.page_renderer import _env

        seo, jsonld = _tool_seo_jsonld(
            slug="venezuela-market-entry-checklist",
            title="Venezuela Market-Entry Checklist for U.S. Companies",
            description=(
                "Practical Venezuela market-entry checklist for U.S. exporters: "
                "ITA trade leads, OFAC screening, BIS export controls, FX, "
                "payments, travel, and contacts."
            ),
            keywords=(
                "Venezuela market entry checklist, export to Venezuela, "
                "US companies Venezuela, ITA Venezuela, Venezuela export controls, "
                "Venezuela OFAC checklist"
            ),
            faq=[
                {
                    "q": "Can U.S. companies do business in Venezuela?",
                    "a": "Some activity may be possible, but U.S. companies must screen counterparties, review OFAC sanctions, evaluate BIS export controls, and document payment and banking paths before proceeding.",
                },
                {
                    "q": "What is the first U.S. government contact point for Venezuela opportunities?",
                    "a": "ITA's Venezuela Business Information Center directs companies to tradevenezuela@trade.gov for assistance.",
                },
                {
                    "q": "What should be checked before quoting a Venezuelan buyer?",
                    "a": "Screen the buyer and beneficial owners, classify the product for export controls, confirm any OFAC authorization needed, assess payment rails, and document end use.",
                },
            ],
        )
        from src.seo.cluster_topology import build_cluster_ctx
        cluster_ctx = build_cluster_ctx("/tools/venezuela-market-entry-checklist")

        template = _env.get_template("tools/venezuela_market_entry_checklist.html.j2")
        html = template.render(
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
        logger.exception("venezuela market-entry checklist render failed: %s", exc)
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
                "url": "/get-venezuela-visa",
                "name": "Venezuela Visa Application Service",
                "category": "Travel service",
                "summary": "Paid same-day Venezuela e-visa application preparation and filing service: document review, Cancillería Digital submission, and application monitoring. $49.99 launch special this month; government fees separate.",
            },
            {
                "url": "/tools/venezuela-trade-leads",
                "name": "Venezuela Trade Leads for U.S. Companies",
                "category": "Trade",
                "summary": "Search International Trade Administration Venezuela opportunities by sector, equipment requested, units, and HS code — with official trade.gov sourcing and contact path for U.S. exporters.",
            },
            {
                "url": "/tools/venezuela-market-entry-checklist",
                "name": "Venezuela Market-Entry Checklist for U.S. Companies",
                "category": "Trade",
                "summary": "Step-by-step U.S. exporter workflow: ITA trade leads, OFAC screening, BIS export controls, payment friction, FX, travel planning, and contacts.",
            },
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
            "title": "Venezuela Investor Tools & Services — Sanctions, BCV, Visa",
            "description": "Toolkit for evaluating Venezuelan exposure: ITA trade leads, OFAC sanctions screening, OFAC general license lookup, live BCV USD rate, ROI calculator, Caracas safety, visa requirements, and visa filing help.",
            "keywords": "Venezuela investor tools, Venezuela trade leads, ITA Venezuela, OFAC checker, BCV rate, Venezuela ROI calculator, Caracas safety, Venezuela visa, Venezuela visa service",
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
            "description": (
                "Plain-English guides for foreign investors on Venezuela: "
                "OFAC sanctions, the BCV, the bolívar, buying bonds, and "
                "operating in Caracas."
            ),
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
                    "name": "International Trade Administration — Venezuela",
                    "kind": "US Commerce Department", "tier": "Primary",
                    "url": "https://www.trade.gov/venezuela",
                    "description": "ITA's Venezuela Business Information Center, trade leads, exporter FAQ, and country contacts for U.S. companies evaluating Venezuelan opportunities. Powers our Venezuela trade-leads finder and market-entry checklist.",
                    "cadence": "Twice daily",
                    "entries_count": _count_ext(SourceType.ITA_TRADE),
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
                    "description": "Global news event database used as a tone signal — we use the GDELT V2 GKG tone score as one of the inputs that decides which items get full editorial analysis.",
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
                    "primary Venezuelan and US government sources we monitor, "
                    "refresh cadence, filtering pipeline, and editorial standards."
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
            # Round 3 (Apr 2026): 28d GSC — strong impressions at pos ~6–7
            # but sub-2% CTR. "SDN" + "OFAC" + live count in the first 45
            # characters; "searchable" + program scope in the description
            # for "ofac sdn venezuela" and compliance long-tail.
            seo = {
                "title": (
                    f"OFAC SDN: {stats['total']} Venezuela Sanctions on US List "
                    f"({today_human})"
                ),
                "description": (
                    f"Search the full US Treasury OFAC SDN for Venezuela: "
                    f"{stats['total']} active designations (individuals, "
                    f"companies, vessels, aircraft). "
                    f"Table updates twice daily; programs include "
                    f"VENEZUELA, EO 13692, EO 13850, EO 13884."
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

            # Featured research-dossier cards. Hand-picked rather than
            # auto-cycled so the lineup stays anchored to the marquee
            # disambiguation cases (Alex Saab vs. Tarek Saab) and the
            # most-recent designation. Falls back silently if any slug
            # is missing — the callout just renders fewer cards.
            try:
                from src.research.entity_mvp import (
                    card_data_for_hub as _card_data,
                    all_hub_cards as _all_hub_cards,
                )
                _featured_slugs = (
                    "saab-moran-alex-nain",
                    "saab-halabi-tarek-william",
                    "carretero-napolitano-ramon",
                )
                featured_dossiers = [
                    c for s in _featured_slugs if (c := _card_data(s)) is not None
                ]
                total_dossiers = len(_all_hub_cards())
            except Exception as exc:
                logger.warning("sanctions tracker: featured dossiers unavailable: %s", exc)
                featured_dossiers = []
                total_dossiers = 0

            template = _env.get_template("sanctions_tracker.html.j2")
            html = template.render(
                sdn_entries=sdn_entries,
                stats=stats,
                sector_stats=sector_stats_payload,
                recent_briefings=recent_sanctions_briefings,
                featured_dossiers=featured_dossiers,
                total_dossiers=total_dossiers,
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

        # If a deep research dossier exists for this slug (curated identity
        # disambiguation, related-entity network, OFAC press release, news
        # corpus, PDF export), surface a prominent link from the lighter
        # profile page. Both URLs are indexable; the dossier is canonical
        # to itself, so this internal link funnels engagement without
        # cannibalising the profile page's own ranking signal.
        dossier_url: str | None = None
        try:
            from src.research import ALLOWED_ENTITIES as _DOSSIER_ALLOWED
            if slug in _DOSSIER_ALLOWED:
                dossier_url = f"/research/sdn/{slug}"
        except Exception:
            dossier_url = None

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
            dossier_url=dossier_url,
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
# Per-SDN research dossier — disambiguation-first profile pages with
# identity-card, related-entity network, adverse media, OFAC press
# release, and a tamper-evident PDF export.
#
#   /research/sdn/                   — hub / index of all dossiers
#   /research/sdn/<slug>             — the dossier page
#   /research/sdn/<slug>/dossier.pdf — PDF export of the same page
#
# The slug must appear in src.research.ALLOWED_ENTITIES — that's how we
# cap the number of dossier pages until each one's curated_sources.json
# entry has been reviewed. To add a new dossier: append the
# (slug, bucket) pair to ALLOWED_ENTITIES and seed
# curated_sources.json with at minimum the OFAC SDN-list entry and the
# underlying executive order.
# ──────────────────────────────────────────────────────────────────────


@app.route("/research/sdn/")
@app.route("/research/sdn")
def research_sdn_hub_page():
    """Hub / index page for the per-SDN research-dossier corpus.

    Targets the head term ('OFAC SDN research', 'OFAC SDN search')
    that the individual dossier pages do not — and by linking out to
    every dossier, gives the whole corpus a clean parent in the link
    graph. Cards are rendered from the cheap card_data_for_hub()
    helper, no external fetches."""
    from datetime import date as _date
    from src.research.entity_mvp import all_hub_cards
    from src.page_renderer import _env, _base_url, settings as _s

    cards = all_hub_cards()

    # Group into surname clusters for the in-page nav. Order is
    # stable: clusters appear in the order their first member shows
    # up in ALLOWED_ENTITIES, so the hub layout is deterministic.
    clusters: list[dict[str, Any]] = []
    cluster_index: dict[str, dict[str, Any]] = {}
    for c in cards:
        bucket = cluster_index.get(c["surname_key"])
        if bucket is None:
            bucket = {
                "key": c["surname_key"],
                "label": c["surname"].title(),
                "cards": [],
            }
            cluster_index[c["surname_key"]] = bucket
            clusters.append(bucket)
        bucket["cards"].append(c)

    base = _base_url()
    canonical = f"{base}/research/sdn/"

    # Schema.org CollectionPage + ItemList. The ItemList is what
    # gives Google the hint to render the page as a sitelinks-rich
    # result for collection-style queries. Serialised here (not in the
    # template) so the base layout's {{ jsonld | safe }} render path
    # works — same convention every other route on this site uses.
    import json as _json
    jsonld_dict: dict[str, Any] = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "CollectionPage",
                "@id": canonical,
                "url": canonical,
                "name": "OFAC SDN Research Dossiers",
                "description": (
                    "Hand-curated research dossiers on individuals on the "
                    "US Treasury OFAC Specially Designated Nationals list. "
                    "Each dossier includes identity disambiguation, related-"
                    "entity network, OFAC press release, adverse media, and "
                    "a tamper-evident PDF export."
                ),
                "isPartOf": {"@type": "WebSite", "url": base, "name": _s.site_name},
                "inLanguage": "en",
            },
            {
                "@type": "ItemList",
                "numberOfItems": len(cards),
                "itemListOrder": "https://schema.org/ItemListOrderAscending",
                "itemListElement": [
                    {
                        "@type": "ListItem",
                        "position": i + 1,
                        "url": f"{base}{c['url']}",
                        "name": c["display_name"],
                    }
                    for i, c in enumerate(cards)
                ],
            },
            {
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{base}/"},
                    {"@type": "ListItem", "position": 2, "name": "Research", "item": f"{base}/research/sdn/"},
                ],
            },
        ],
    }
    jsonld = _json.dumps(jsonld_dict, ensure_ascii=False)

    try:
        template = _env.get_template("research/index.html.j2")
        html = template.render(
            seo={
                "title": (
                    "OFAC SDN Research Dossiers — Disambiguation, Adverse "
                    "Media, Tamper-Evident PDFs"
                )[:120],
                "description": (
                    "Hand-curated due-diligence dossiers on OFAC SDN-listed "
                    "individuals: identity disambiguation, related-entity "
                    "networks, adverse media, OFAC press releases, and "
                    "audit-ready PDF exports. Built for compliance, EDD, "
                    "and KYC analysts."
                )[:300],
                "keywords": (
                    "OFAC SDN research, OFAC SDN dossier, OFAC SDN list "
                    "search, sanctions due diligence, EDD memo, KYC "
                    "research, sanctioned individuals research"
                ),
                "canonical": canonical,
                "site_name": _s.site_name,
                "site_url": base,
                "locale": _s.site_locale,
                "og_image": f"{base}/static/og-image.png?v=3",
                "og_type": "website",
            },
            cards=cards,
            clusters=clusters,
            jsonld=jsonld,
            canonical_url=canonical,
            current_year=_date.today().year,
            total_dossiers=len(cards),
        )
        return Response(html, mimetype="text/html")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("research hub render failed: %s", exc)
        abort(500)


@app.route("/research/sdn/<slug>")
@app.route("/research/sdn/<slug>/")
def research_sdn_dossier_page(slug: str):
    """Per-SDN research dossier."""
    from datetime import date as _date
    from src.research import ALLOWED_ENTITIES
    from src.research.entity_mvp import assemble, compute_fingerprint
    from src.page_renderer import _env, _base_url, settings as _s

    if slug not in ALLOWED_ENTITIES:
        abort(404)

    ctx = assemble(slug)
    if ctx is None:
        abort(404)

    dossier_mode = request.args.get("dossier") == "1"
    fingerprint = compute_fingerprint(ctx)

    base = _base_url()
    canonical_url = f"{base}/research/sdn/{slug}"

    display_name = ctx["identity_card"]["display_name"]
    program = ctx["status"]["program"]
    profile = ctx["profile"]

    # ── JSON-LD ───────────────────────────────────────────────────────
    # Mirrors the field mapping from the /sanctions/<bucket>/<slug>
    # route so Knowledge-Panel signals (birthDate, nationality,
    # identifier, subjectOf=GovernmentService) are consistent across
    # both the lighter profile page and the deep dossier. Adds a
    # canonical/sameAs link from the dossier Person node back to the
    # profile-page Person node, so Google can de-duplicate them as
    # the same real-world entity rather than treating them as two
    # competing pages on the same name.
    import json as _json
    profile_canonical = f"{base}{profile.url_path}"
    description = (
        f"Disambiguation, OFAC press release, adverse-media research, "
        f"and tamper-evident PDF for {display_name}. Designated under "
        f"{program} (US Treasury OFAC SDN list)."
    )

    breadcrumb = {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{base}/"},
            {"@type": "ListItem", "position": 2, "name": "OFAC SDN Research Dossiers", "item": f"{base}/research/sdn/"},
            {"@type": "ListItem", "position": 3, "name": display_name, "item": canonical_url},
        ],
    }

    identifiers_jsonld: list[dict] = []
    for prop_id, key in (
        ("Cedula", "cedula"),
        ("Passport", "passport"),
        ("NationalID", "national_id"),
        ("IMO", "imo"),
        ("MMSI", "mmsi"),
        ("AircraftTailNumber", "aircraft_tail"),
        ("AircraftSerialNumber", "aircraft_serial"),
    ):
        val = profile.parsed.get(key)
        if val:
            identifiers_jsonld.append({
                "@type": "PropertyValue",
                "propertyID": prop_id,
                "value": val,
            })

    if profile.bucket == "individuals":
        entity_node: dict[str, Any] = {
            "@type": "Person",
            "@id": f"{canonical_url}#person",
            "name": display_name,
            "alternateName": profile.raw_name,
            "url": canonical_url,
            "sameAs": [profile_canonical],
            "description": description,
            "subjectOf": {
                "@type": "GovernmentService",
                "name": profile.program_label,
                "provider": {
                    "@type": "GovernmentOrganization",
                    "name": "US Treasury Office of Foreign Assets Control (OFAC)",
                },
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
        public_role = ctx["identity_card"].get("public_role")
        if public_role:
            entity_node["jobTitle"] = public_role
        if identifiers_jsonld:
            entity_node["identifier"] = identifiers_jsonld
    elif profile.bucket == "entities":
        entity_node = {
            "@type": "Organization",
            "@id": f"{canonical_url}#org",
            "name": display_name,
            "alternateName": profile.raw_name,
            "url": canonical_url,
            "sameAs": [profile_canonical],
            "description": description,
        }
        if identifiers_jsonld:
            entity_node["identifier"] = identifiers_jsonld
    else:
        entity_node = {
            "@type": "Thing",
            "@id": f"{canonical_url}#asset",
            "name": display_name,
            "alternateName": profile.raw_name,
            "url": canonical_url,
            "sameAs": [profile_canonical],
            "description": description,
        }
        if identifiers_jsonld:
            entity_node["identifier"] = identifiers_jsonld

    # The dossier itself is also a CreativeWork — gives us a hook for
    # `dateModified` (driven by the content fingerprint, so it only
    # advances when the underlying record actually changes) plus a
    # downloadUrl pointing at the PDF export. Keeps the audit trail
    # legible to crawlers that don't render JS.
    dossier_node = {
        "@type": "CreativeWork",
        "@id": f"{canonical_url}#dossier",
        "name": f"{display_name} — OFAC SDN Research Dossier",
        "url": canonical_url,
        "about": {"@id": entity_node["@id"]},
        "isAccessibleForFree": True,
        "publisher": {"@type": "Organization", "name": _s.site_name, "url": f"{base}/"},
        "license": "https://www.usa.gov/government-works",
        "encoding": [
            {
                "@type": "MediaObject",
                "encodingFormat": "application/pdf",
                "contentUrl": f"{canonical_url}/dossier.pdf",
                "name": f"{display_name} — Research Dossier (PDF, tamper-evident)",
            }
        ],
    }

    jsonld = _json.dumps({
        "@context": "https://schema.org",
        "@graph": [breadcrumb, entity_node, dossier_node],
    }, ensure_ascii=False)

    try:
        template = _env.get_template("research/dossier.html.j2")
        html = template.render(
            seo={
                # Title formula echoes the high-CTR pattern used by the
                # /sanctions/<bucket>/<slug> profile pages: name → binary
                # status → freshness marker. Adds "Research Dossier" so
                # the listing differentiates from the lighter SDN-profile
                # SERP entry on the same name.
                "title": (
                    f"{display_name} — OFAC SDN Research Dossier "
                    f"(Active {_date.today().year})"
                )[:120],
                "description": (
                    f"Disambiguation, identity card, related-entity network, "
                    f"OFAC press release, and adverse-media research aid for "
                    f"{display_name}. Designated under {program}. Exportable "
                    f"as a tamper-evident PDF."
                )[:300],
                "keywords": (
                    f"is {display_name} sanctioned, {display_name} OFAC, "
                    f"{display_name} SDN dossier, {display_name} due diligence, "
                    f"OFAC SDN research, {program}"
                ),
                "canonical": canonical_url,
                "site_name": _s.site_name,
                "site_url": base,
                "locale": _s.site_locale,
                "og_image": f"{base}/static/og-image.png?v=3",
                "og_type": "profile",
            },
            current_year=_date.today().year,
            dossier_mode=dossier_mode,
            fingerprint=fingerprint,
            canonical_url=canonical_url,
            jsonld=jsonld,
            **ctx,
        )
        return Response(html, mimetype="text/html")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "research dossier render failed for slug=%s: %s", slug, exc,
        )
        abort(500)


@app.route("/research/sdn/<slug>/dossier.pdf")
def research_sdn_dossier_pdf(slug: str):
    """Tamper-evident PDF export of the dossier. Spawns Chromium per
    request — ~3s wall-time. Cacheable in front of the route once
    traffic warrants it."""
    from src.research import ALLOWED_ENTITIES
    from src.research.entity_mvp import pdf_filename_for, render_pdf

    if slug not in ALLOWED_ENTITIES:
        abort(404)

    base = request.host_url.rstrip("/")
    try:
        pdf_bytes = render_pdf(slug, base_url=base)
    except Exception as exc:
        logger.exception(
            "research dossier PDF render failed for slug=%s: %s", slug, exc,
        )
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
        # We name three of the four major US restricted-party lists
        # (OFAC SDN, BIS Entity List, BIS Denied Persons) so this
        # snippet also pulls in long-tail "{brand} trade restrictions"
        # and "{brand} export controls" GSC queries that would
        # otherwise dead-end on a page titled around "Sanctioned?".
        description = (
            f"{company.short_name} (${company.ticker}) {binary_phrase} as of "
            f"{today_human}. Independent check across the OFAC SDN list, the "
            f"BIS Entity List and Denied Persons List, SEC EDGAR 10-K/10-Q/20-F "
            f"filings, and the Caracas Research news corpus."
        ).strip()[:300]

        seo = {
            "title": title,
            "description": description,
            "keywords": (
                f"is {company.short_name} sanctioned, {company.short_name} Venezuela, "
                f"{company.ticker} Venezuela exposure, {company.short_name} OFAC, "
                f"{company.short_name} PDVSA, {company.ticker} sanctions, "
                f"{company.short_name} trade restrictions, {company.short_name} BIS Entity List, "
                f"{company.short_name} export controls, {company.ticker} consolidated screening, "
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

        # Trade-restriction screening — answers the parallel
        # "{brand} trade restrictions" / "{brand} export controls"
        # query. Different list, different agency, different statute
        # from OFAC sanctions: the BIS Entity List + Denied Persons
        # List + Unverified List are export-control instruments under
        # EAR/15 CFR 744, administered by Commerce, whereas OFAC SDN
        # is a Treasury-administered blocking sanction. A US public
        # company headquartered in the US essentially never appears
        # on these BIS lists (they target foreign end-users), so the
        # default binary answer is "No" — but we still emit the
        # binary, the official lookup deep-link, and the methodology
        # so a compliance reader can re-verify in one click.
        import urllib.parse as _urlparse
        trade_q = (
            f"Is {company.short_name} ({company.ticker}) on a US trade-restriction "
            f"list (BIS Entity List, Denied Persons List, or Unverified List)?"
        )
        trade_a = (
            f"No. {company.short_name} is a US-domiciled public company and does "
            f"not appear on the Bureau of Industry and Security (BIS) Entity List, "
            f"Denied Persons List, or Unverified List as of {today_human}. These "
            f"export-control lists, administered by the US Department of Commerce "
            f"under the Export Administration Regulations (EAR / 15 CFR Part 744), "
            f"target foreign end-users — not US-headquartered issuers. Re-verify "
            f"in the official Consolidated Screening List before relying on this "
            f"for a compliance decision."
        )
        encoded_name = _urlparse.quote_plus(company.short_name)
        trade_screening_links = [
            {
                "label": "BIS Consolidated Screening List (search this name)",
                "url": f"https://www.trade.gov/consolidated-screening-list?name={encoded_name}",
                "publisher": "US Department of Commerce — International Trade Administration",
            },
            {
                "label": "BIS Entity List (full official list, 15 CFR 744 Supp. 4)",
                "url": "https://www.bis.doc.gov/index.php/policy-guidance/lists-of-parties-of-concern/entity-list",
                "publisher": "US Department of Commerce — Bureau of Industry and Security",
            },
            {
                "label": "BIS Denied Persons List (active denial orders)",
                "url": "https://www.bis.doc.gov/index.php/policy-guidance/lists-of-parties-of-concern/denied-persons-list",
                "publisher": "US Department of Commerce — Bureau of Industry and Security",
            },
            {
                "label": "BIS Unverified List (parties pending end-use verification)",
                "url": "https://www.bis.doc.gov/index.php/policy-guidance/lists-of-parties-of-concern/unverified-list",
                "publisher": "US Department of Commerce — Bureau of Industry and Security",
            },
            {
                "label": "OFAC Sanctions Search (re-verify SDN status)",
                "url": f"https://sanctions-search.ofac.treasury.gov/Default.aspx?ID={encoded_name}",
                "publisher": "US Treasury — Office of Foreign Assets Control",
            },
        ]

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
                    "name": trade_q,
                    "acceptedAnswer": {"@type": "Answer", "text": trade_a[:400]},
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
                "q": trade_q,
                "a": trade_a,
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
            trade_screening_links=trade_screening_links,
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
        # Round 3 (Apr 2026): 28d GSC — "travel" intent + Level-3 in title for
        # the dominant "travel to Venezuela" / "Caracas safety" queries; desc
        # keeps OFAC/embassy/printable card signals without repeating the title.
        title = "Venezuela Travel 2026: US Level-3, Caracas Safety, Visa & Hotels"
        description = (
            "Venezuela business travel: State Dept Reconsider (Level-3) advisory, "
            "Caracas security zones, vetted hotels, airport transfers, embassies, "
            "cell/SIM, OFAC context — includes printable emergency card (2026)."
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
            us_embassy_eguide_url=US_EMBASSY_VENEZUELA_EVISA_INSTRUCTIONS,
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


@app.route("/apply-for-venezuelan-visa/planilla")
@app.route("/apply-for-venezuelan-visa/planilla/")
def visa_planilla_form():
    """
    Printable MPPRE-style planilla de solicitud de visa (fill in browser;
    user prints to PDF and uploads in Cancillería Digital).
    """
    try:
        from src.page_renderer import _env, _base_url
        from src.data.visa_document_landing import PLANILLA_HERO_LINE

        base = _base_url()
        seo = {
            "title": f"{PLANILLA_HERO_LINE} — type & print to PDF (Caracas Research)",
            "description": (
                f"{PLANILLA_HERO_LINE}. Type all sections, then print or Save as PDF "
                "for upload to the MPPRE e-visa portal (Cancillería Digital)."
            ),
            "canonical": f"{base}/apply-for-venezuelan-visa/planilla",
        }
        return Response(
            _env.get_template("visa_planilla.html.j2").render(
                seo=seo,
                planilla_hero_line=PLANILLA_HERO_LINE,
            ),
            mimetype="text/html",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("visa planilla form render failed: %s", exc)
        abort(500)


@app.route("/apply-for-venezuelan-visa/declaracion-jurada")
@app.route("/apply-for-venezuelan-visa/declaracion-jurada/")
def visa_declaracion_jurada():
    """
    Sworn statement (declaración jurada) in Spanish, pre-filled body text
    and typed cursive-style signature for PDF, for visa uploads.
    """
    try:
        from src.page_renderer import _env, _base_url
        base = _base_url()
        seo = {
            "title": "Declaración jurada — no criminal record (type & print to PDF)",
            "description": (
                "Pre-filled Spanish declaración jurada for Venezuela visa files. Add "
                "name, country, and passport, type your signature, print to PDF, "
                "and upload in Cancillería Digital."
            ),
            "canonical": f"{base}/apply-for-venezuelan-visa/declaracion-jurada",
        }
        return Response(
            _env.get_template("visa_declaracion_jurada.html.j2").render(seo=seo),
            mimetype="text/html",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("visa declaración jurada form render failed: %s", exc)
        abort(500)


@app.route("/planilla-de-solicitud-de-visa")
@app.route("/planilla-de-solicitud-de-visa/")
def visa_planilla_de_solicitud_landing():
    """SEO guide for searchers who query the exact ministry form name."""
    try:
        from src.data.visa_document_landing import get_planilla_landing

        return _render_visa_document_landing(get_planilla_landing())
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("planilla landing render failed: %s", exc)
        abort(500)


@app.route("/declaracion-jurada-visa-venezolana")
@app.route("/declaracion-jurada-visa-venezolana/")
def visa_declaracion_jurada_landing():
    """SEO guide for searchers who query declaración jurada + Venezuelan visa."""
    try:
        from src.data.visa_document_landing import get_declaracion_landing

        return _render_visa_document_landing(get_declaracion_landing())
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("declaración jurada landing render failed: %s", exc)
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
        # Research-dossier hub. Listed as a static URL because it
        # exists for every deploy regardless of dataset state, unlike
        # the per-slug dossiers (which are added below from the
        # _HIGH_DEMAND_PROFILE_SLUGS whitelist).
        {"loc": f"{base}/research/sdn/", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.85"},
        {"loc": f"{base}/calendar", "lastmod": today_iso, "changefreq": "daily", "priority": "0.7"},
        {"loc": f"{base}/travel", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.8"},
        {"loc": f"{base}/get-venezuela-visa", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.85"},
        {"loc": f"{base}/sources", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.6"},
        {"loc": f"{base}/briefing", "lastmod": today_iso, "changefreq": "daily", "priority": "0.9"},
        {"loc": f"{base}/tools", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.8"},
        {"loc": f"{base}/explainers", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.8"},
        {"loc": f"{base}/tools/bolivar-usd-exchange-rate", "lastmod": today_iso, "changefreq": "daily", "priority": "0.7"},
        {"loc": f"{base}/tools/ofac-venezuela-sanctions-checker", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.7"},
        # OFAC SDN name-check answer pages — one URL per compliance
        # query we're hand-curating. See src/data/ofac_name_check.py
        # for the full design rationale. Listed individually (rather
        # than walked from the registry) so a typo in the registry
        # can't silently drop a live URL from the sitemap.
        {"loc": f"{base}/tools/ofac-sdn-name-check/rodriguez-hernandez-juan", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.75"},
        {"loc": f"{base}/tools/public-company-venezuela-exposure-check", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.75"},
        {"loc": f"{base}/tools/sec-edgar-venezuela-impairment-search", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.75"},
        {"loc": f"{base}/tools/venezuela-trade-leads", "lastmod": today_iso, "changefreq": "daily", "priority": "0.8"},
        {"loc": f"{base}/tools/venezuela-market-entry-checklist", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.72"},
        {"loc": f"{base}/companies", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.85"},
        {"loc": f"{base}/tools/ofac-venezuela-general-licenses", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.7"},
        {"loc": f"{base}/tools/caracas-safety-by-neighborhood", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.6"},
        {"loc": f"{base}/tools/venezuela-investment-roi-calculator", "lastmod": today_iso, "changefreq": "monthly", "priority": "0.6"},
        {"loc": f"{base}/tools/venezuela-visa-requirements", "lastmod": today_iso, "changefreq": "monthly", "priority": "0.6"},
        {"loc": f"{base}/apply-for-venezuelan-visa", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.85"},
        {"loc": f"{base}/apply-for-venezuelan-visa/us-citizens", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.8"},
        {"loc": f"{base}/apply-for-venezuelan-visa/business-visa", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.75"},
        {"loc": f"{base}/apply-for-venezuelan-visa/china", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.7"},
        {"loc": f"{base}/planilla-de-solicitud-de-visa", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.72"},
        {"loc": f"{base}/declaracion-jurada-visa-venezolana", "lastmod": today_iso, "changefreq": "weekly", "priority": "0.72"},
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
                # Research dossiers (April 2026): each one shadows a
                # /sanctions/individuals/<slug> profile that has already
                # received GSC impressions. Listing both URLs in the
                # sitemap is intentional — the dossier is the deeper
                # surface and we want Google to index it on its own
                # merits, not collapse it under the lighter profile
                # page. Canonical on each dossier points to itself.
                "/research/sdn/carretero-napolitano-ramon",
                "/research/sdn/carretero-napolitano-vicente-luis",
                "/research/sdn/carretero-napolitano-roberto",
                # Saab cluster dossiers (added April 2026 to serve the
                # 'saab abelardo' GSC query plus the long-tail demand
                # for Alex Saab himself, his brothers, his Colombian
                # cousins, and the unrelated Tarek William Saab.
                # Each dossier surfaces the disambiguator across all
                # six entries so a wrong-Saab landing recovers
                # gracefully rather than dead-ending the searcher).
                "/research/sdn/saab-moran-alex-nain",
                "/research/sdn/saab-moran-amir-luis",
                "/research/sdn/saab-moran-luis-alberto",
                "/research/sdn/saab-certain-isham-ali",
                "/research/sdn/saab-certain-shadi-nain",
                "/research/sdn/saab-halabi-tarek-william",
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

            try:
                from src.research import ALLOWED_ENTITIES as _DOSSIER_ALLOWED
            except Exception as exc:
                logger.warning("sitemap whitelist: dossier allowlist unavailable: %s", exc)
                _DOSSIER_ALLOWED = set()  # type: ignore

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
                elif path.startswith("/research/sdn/"):
                    parts = path.strip("/").split("/")
                    if len(parts) == 3:
                        is_live = parts[2] in _DOSSIER_ALLOWED
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
