"""
Flask web server for Venezuelan Business Network.

Serves the generated report.html on Render (or locally).
"""

from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask, send_from_directory, abort

from src.config import settings

app = Flask(__name__)
logger = logging.getLogger(__name__)

OUTPUT_DIR = settings.output_dir


@app.route("/")
def index():
    report = OUTPUT_DIR / "report.html"
    if not report.exists():
        abort(503, description="Report not yet generated. Run the daily pipeline first.")
    return send_from_directory(str(OUTPUT_DIR), "report.html")


@app.route("/health")
def health():
    report = OUTPUT_DIR / "report.html"
    return {"status": "ok", "report_exists": report.exists()}, 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=settings.server_port, debug=True)
