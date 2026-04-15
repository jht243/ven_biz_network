"""
Flask web server for Venezuelan Business Network.

Serves the generated report.html on Render (or locally).
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
from flask import Flask, send_from_directory, abort, request, jsonify

from src.config import settings

app = Flask(__name__)
logger = logging.getLogger(__name__)

OUTPUT_DIR = settings.output_dir

BUTTONDOWN_API_URL = "https://api.buttondown.com/v1/subscribers"


@app.route("/")
def index():
    report = OUTPUT_DIR / "report.html"
    if not report.exists():
        abort(503, description="Report not yet generated. Run the daily pipeline first.")
    return send_from_directory(str(OUTPUT_DIR), "report.html")


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

        if resp.status_code == 409:
            return jsonify({"ok": True, "note": "Already subscribed"})

        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        code = body.get("code", "")

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
            if resp2.status_code in (200, 201):
                logger.info("Buttondown subscriber added (bypass): %s", email)
                return jsonify({"ok": True})
            if resp2.status_code == 409:
                return jsonify({"ok": True, "note": "Already subscribed"})
            logger.error("Buttondown bypass also failed %d: %s", resp2.status_code, resp2.text)

        logger.error("Buttondown API error %d: %s", resp.status_code, resp.text)
        return jsonify({"ok": False, "error": "Subscription failed, please try again"}), 502

    except Exception as e:
        logger.error("Buttondown request failed: %s", e)
        return jsonify({"ok": False, "error": "Service unavailable"}), 503


@app.route("/health")
def health():
    report = OUTPUT_DIR / "report.html"
    return {"status": "ok", "report_exists": report.exists()}, 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=settings.server_port, debug=True)
