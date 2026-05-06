#!/usr/bin/env python3
"""
Nightly sitemap audit and sync.

What it does
------------
1. Fetches the live production sitemap and normalises every <loc> to a path.
2. Parses server.py to find every non-parametric public @app.route that isn't
   internal/noindex (admin, api, webhook, health, OG, tearsheet, …).
3. Diffs the two sets:
   - Routes in code but NOT in the live sitemap → auto-add to static_urls.
   - Paths in the live sitemap returning 4xx → logged as dead links (manual
     fix required — most come from DB LandingPage records, not hardcoded code).
4. Spot-checks 25 random live sitemap URLs for HTTP errors.
5. If static_urls was patched, commits server.py and pushes.

Run manually:
    python scripts/sync_sitemap.py

Dry-run (no file edits, no push):
    python scripts/sync_sitemap.py --dry-run

Skip the live HTTP checks (faster, offline):
    python scripts/sync_sitemap.py --no-spot-check

Exit codes: 0 = clean or changes pushed, 1 = unrecoverable error.
"""

from __future__ import annotations

import argparse
import logging
import random
import re
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
import xml.etree.ElementTree as ET

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("sync_sitemap")

ROOT = Path(__file__).parent.parent
SERVER_PY = ROOT / "server.py"

LIVE_SITEMAP_URL = "https://www.caracasresearch.com/sitemap.xml"
CANONICAL_BASE = "https://caracasresearch.com"   # no www — matches what the server emits

# ---------------------------------------------------------------------------
# Routes that must NEVER appear in the sitemap
# ---------------------------------------------------------------------------
EXCLUDE_PREFIXES = (
    "/admin",
    "/api/",
    "/webhook",
    "/health",
    "/og/",
    "/static",
    "/visa-intake",
    "/tearsheet",
    "/subscribe",
    "/_",
    "/ws",
    "/real-estate/thanks",
)
EXCLUDE_SUFFIXES = (".txt", ".xml", ".pdf")
EXCLUDE_EXACT = {
    "/robots.txt",
    "/sitemap.xml",
    "/news-sitemap.xml",
    # Noindex printable form pages — intentionally excluded from the sitemap
    # (see comment in sitemap_xml() around the visa cluster walk).
    "/planilla-de-solicitud-de-visa",
    "/declaracion-jurada-visa-venezolana",
    "/apply-for-venezuelan-visa/planilla",
    "/apply-for-venezuelan-visa/declaracion-jurada",
    # 301 redirect aliases — canonical is /get-venezuela-visa; only the
    # canonical URL belongs in the sitemap.
    "/venezuela-visa-service",
    "/tools/venezuela-visa-application-service",
}
EXCLUDE_CONTAINS = ("indexnow", "noindex")

# changefreq / priority heuristics for auto-detected new routes
def _guess_attrs(path: str) -> dict[str, str]:
    if path.startswith("/tools/"):
        return {"changefreq": "weekly", "priority": "0.7"}
    if path.startswith("/explainers/"):
        return {"changefreq": "weekly", "priority": "0.75"}
    if path.startswith("/sectors/"):
        return {"changefreq": "weekly", "priority": "0.6"}
    if path.startswith("/sanctions/"):
        return {"changefreq": "daily", "priority": "0.85"}
    if path.startswith("/people/"):
        return {"changefreq": "weekly", "priority": "0.7"}
    if "real-estate" in path or "property" in path:
        return {"changefreq": "weekly", "priority": "0.75"}
    return {"changefreq": "weekly", "priority": "0.75"}


# ---------------------------------------------------------------------------
# 1. Fetch live sitemap
# ---------------------------------------------------------------------------

def _get(url: str, timeout: int = 30) -> bytes:
    req = Request(url, headers={"User-Agent": "caracasresearch-sitemap-bot/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_live_sitemap() -> list[str]:
    """Return all <loc> values from the production sitemap."""
    try:
        data = _get(LIVE_SITEMAP_URL)
        root = ET.fromstring(data)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        return [u.find("sm:loc", ns).text for u in root.findall("sm:url", ns)]
    except Exception as exc:
        log.warning("Could not fetch live sitemap: %s", exc)
        return []


def normalise_locs(locs: list[str]) -> set[str]:
    """
    Strip the canonical base and trailing slash from every loc so we're
    comparing bare paths like /travel or /tools/bolivar-usd-exchange-rate.
    """
    paths: set[str] = set()
    for loc in locs:
        # Strip base (handle both www and non-www since the server may emit either)
        path = loc
        for base in (
            "https://www.caracasresearch.com",
            "https://caracasresearch.com",
            "http://www.caracasresearch.com",
            "http://caracasresearch.com",
        ):
            if path.startswith(base):
                path = path[len(base):]
                break
        path = path.rstrip("/") or "/"
        paths.add(path)
    return paths


# ---------------------------------------------------------------------------
# 2. Extract non-parametric public routes from server.py
# ---------------------------------------------------------------------------

def extract_routes_from_server(server_text: str) -> set[str]:
    """Return bare paths (no trailing slash, no params) from @app.route lines."""
    pattern = re.compile(r'@app\.route\(\s*"(/[^"]*)"')
    paths: set[str] = set()
    for m in pattern.finditer(server_text):
        path = m.group(1).rstrip("/") or "/"
        if "<" in path:          # parametric
            continue
        if path in EXCLUDE_EXACT:
            continue
        if any(path.startswith(p) for p in EXCLUDE_PREFIXES):
            continue
        if any(path.endswith(s) for s in EXCLUDE_SUFFIXES):
            continue
        if any(kw in path for kw in EXCLUDE_CONTAINS):
            continue
        paths.add(path)
    return paths


# ---------------------------------------------------------------------------
# 3. Spot-check a sample of live sitemap URLs
# ---------------------------------------------------------------------------

def spot_check_urls(locs: list[str], sample: int = 25) -> list[str]:
    """Return URLs that returned 4xx/5xx."""
    if not locs:
        return []
    checked = random.sample(locs, min(sample, len(locs)))
    dead: list[str] = []
    for url in sorted(checked):
        try:
            req = Request(url, headers={"User-Agent": "caracasresearch-sitemap-bot/1.0"})
            with urlopen(req, timeout=15) as resp:
                status = resp.status
            if status >= 400:
                dead.append(url)
                log.warning("DEAD URL (status %d): %s", status, url)
        except HTTPError as exc:
            if exc.code >= 400:
                dead.append(url)
                log.warning("DEAD URL (HTTP %d): %s", exc.code, url)
            else:
                log.info("Spot-check non-fatal %s: HTTP %d", url, exc.code)
        except URLError as exc:
            log.info("Spot-check connection error %s: %s", url, exc.reason)
        except Exception as exc:
            log.info("Spot-check error %s: %s", url, exc)
    return dead


# ---------------------------------------------------------------------------
# 4. Patch static_urls in server.py
# ---------------------------------------------------------------------------

# The anchor is the very last line of the static_urls list.  We insert new
# entries immediately before the closing `]`.
INSERTION_ANCHOR = (
    '        {"loc": f"{base}/tools/venezuela-visa-requirements", '
    '"lastmod": today_iso, "changefreq": "monthly", "priority": "0.6"},\n'
    '    ]\n'
)


def _build_entry(path: str) -> str:
    attrs = _guess_attrs(path)
    return (
        f'        {{"loc": f"{{base}}{path}", "lastmod": today_iso, '
        f'"changefreq": "{attrs["changefreq"]}", "priority": "{attrs["priority"]}"}},\n'
    )


def patch_server_py(server_text: str, missing_paths: list[str]) -> str:
    """Return updated server_text with new static_url entries spliced in."""
    if not missing_paths:
        return server_text
    if INSERTION_ANCHOR not in server_text:
        log.error(
            "Cannot find insertion anchor in server.py — the static_urls list "
            "may have changed. Update INSERTION_ANCHOR in this script or add "
            "the missing routes manually:\n  %s",
            "\n  ".join(sorted(missing_paths)),
        )
        return server_text   # return unchanged so caller can detect no-op
    new_lines = "".join(_build_entry(p) for p in sorted(missing_paths))
    return server_text.replace(INSERTION_ANCHOR, new_lines + INSERTION_ANCHOR, 1)


# ---------------------------------------------------------------------------
# 5. Git helpers
# ---------------------------------------------------------------------------

def _git(args: list[str], env: dict | None = None) -> str:
    import os
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    result = subprocess.run(
        ["git"] + args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=run_env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


def _configure_git_auth() -> None:
    """
    When running on Render (or any CI) set git author identity and wire up
    the GITHUB_TOKEN so `git push` can authenticate over HTTPS without
    interactive prompts.  Requires GITHUB_TOKEN and GITHUB_REPO env vars
    (e.g. GITHUB_REPO=jht243/ven_biz_network).
    """
    import os
    token = os.environ.get("GITHUB_TOKEN", "")
    repo  = os.environ.get("GITHUB_REPO", "")
    if not token or not repo:
        log.debug("GITHUB_TOKEN / GITHUB_REPO not set — skipping auth config")
        return
    # Set identity so git commit doesn't fail
    _git(["config", "user.email", "sitemap-bot@caracasresearch.com"])
    _git(["config", "user.name", "Sitemap Sync Bot"])
    # Rewrite the remote URL to embed the token
    remote_url = f"https://{token}@github.com/{repo}.git"
    _git(["remote", "set-url", "origin", remote_url])
    log.info("Git auth configured via GITHUB_TOKEN")


def commit_and_push(added_paths: list[str]) -> None:
    _configure_git_auth()
    _git(["pull", "--rebase", "origin", "main"])
    _git(["add", str(SERVER_PY.relative_to(ROOT))])
    summary = ", ".join(sorted(added_paths)[:5])
    if len(added_paths) > 5:
        summary += f" (+{len(added_paths) - 5} more)"
    msg = (
        f"sitemap: auto-add {len(added_paths)} missing static URL(s)\n\n"
        f"Routes: {summary}\n\n"
        "Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
    )
    _git(["commit", "-m", msg])
    _git(["push", "origin", "HEAD:main"])
    log.info("Pushed updated sitemap entries to origin/main")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Audit and sync sitemap.xml entries")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report differences but do not modify files or push")
    parser.add_argument("--no-spot-check", action="store_true",
                        help="Skip live HTTP spot-check (faster, offline)")
    args = parser.parse_args()

    # --- Fetch live sitemap ---
    log.info("Fetching live sitemap from %s …", LIVE_SITEMAP_URL)
    live_locs = fetch_live_sitemap()
    if not live_locs:
        log.error("Live sitemap returned 0 URLs — aborting to avoid false positives.")
        return 1
    log.info("Live sitemap: %d URLs", len(live_locs))
    live_paths = normalise_locs(live_locs)

    # --- Parse server.py routes ---
    server_text = SERVER_PY.read_text(encoding="utf-8")
    code_routes = extract_routes_from_server(server_text)
    log.info("server.py: %d non-parametric public routes found", len(code_routes))

    # --- Diff: in code but not in live sitemap ---
    missing = sorted(code_routes - live_paths)
    if missing:
        log.warning(
            "%d route(s) are declared in server.py but absent from the live sitemap:\n  %s",
            len(missing),
            "\n  ".join(missing),
        )
    else:
        log.info("All public code routes are present in the live sitemap.")

    # --- Spot-check live URLs ---
    dead_urls: list[str] = []
    if not args.no_spot_check:
        log.info("Spot-checking 25 random live sitemap URLs …")
        dead_urls = spot_check_urls(live_locs, sample=25)
        if dead_urls:
            log.warning(
                "%d dead URL(s) found in the live sitemap (manual fix required — "
                "these are likely stale DB LandingPage records):\n  %s",
                len(dead_urls),
                "\n  ".join(dead_urls),
            )
        else:
            log.info("Spot-check passed — all sampled URLs returned OK.")

    # --- Nothing to add ---
    if not missing:
        log.info("Sitemap is up to date. Done.")
        return 0

    if args.dry_run:
        log.info("--dry-run: skipping file edits and git push.")
        return 0

    # --- Patch server.py ---
    updated_text = patch_server_py(server_text, missing)
    if updated_text == server_text:
        # patch_server_py already logged the error
        return 1

    SERVER_PY.write_text(updated_text, encoding="utf-8")
    log.info(
        "Wrote %d new static_url entry/entries to server.py",
        len(missing),
    )

    # --- Commit + push ---
    try:
        commit_and_push(missing)
    except RuntimeError as exc:
        log.error("Git operation failed: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
