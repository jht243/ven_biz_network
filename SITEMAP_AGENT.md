# Sitemap Sync Agent — Handoff Note

> When the user says **"continue the sitemap tool"**, this is the file to read first.

---

## What exists

### Script
**`scripts/sync_sitemap.py`** — the entire agent lives here.

What it does on each run:
1. Fetches the live sitemap at `https://www.caracasresearch.com/sitemap.xml`
2. Parses `server.py` for all non-parametric public `@app.route(...)` declarations
3. Diffs: routes in code but absent from the live sitemap → auto-inserted into `static_urls` in `sitemap_xml()`
4. Spot-checks 25 random live sitemap URLs for HTTP 4xx/5xx (dead link detection)
5. Commits and pushes `server.py` if anything was added

### Where sitemap entries live in `server.py`
`sitemap_xml()` starts at **line ~9198**. Inside it:
- `static_urls` — the hardcoded list (lines ~9211–9260). New entries are auto-inserted just before the closing `]` on line ~9260.
- Dynamic walks follow: real estate paths, people profiles, blog posts, landing pages from DB, sector slugs, SDN dossiers — these don't need touching.

**Insertion anchor** (what the script searches for to know where to splice):
```python
        {"loc": f"{base}/tools/venezuela-visa-requirements", "lastmod": today_iso, "changefreq": "monthly", "priority": "0.6"},
    ]
```
If `server.py` ever changes and this line moves, update `INSERTION_ANCHOR` in the script.

### Render cron
**`render.yaml`** — `vij-nightly-sitemap-sync` runs daily at **02:00 UTC**.

Required env var in Render dashboard (not in render.yaml):
- `GITHUB_TOKEN` — GitHub fine-grained PAT with **Contents: Read & Write** on `jht243/ven_biz_network`
- `GITHUB_REPO` — already set to `jht243/ven_biz_network` in render.yaml

---

## Known issues / next steps

### 1. `/law` is a dead link in the live sitemap
`https://caracasresearch.com/law` returns 404 but appears in the sitemap.
It's coming from a stale `LandingPage` DB record (not hardcoded code — searching `server.py` for `/law` finds nothing).
**Fix**: delete the row from the `LandingPage` table where `canonical_path = '/law'`.

### 2. `/travel/emergency-card` is missing from the sitemap
The script will auto-add it on the next live run (it passed dry-run verification).
This is the only genuine gap as of 2026-05-06.

### 3. GITHUB_TOKEN not yet set on Render
The Render cron job is deployed but the `GITHUB_TOKEN` env var needs to be added
manually in the Render dashboard before the push step will work.
Without it, the script audits and detects changes but can't push.

---

## How to run manually

```bash
# Safe audit — no file changes, no push
python scripts/sync_sitemap.py --dry-run

# Full run — patches server.py and pushes to git
python scripts/sync_sitemap.py

# Audit without hitting live URLs (fast, offline)
python scripts/sync_sitemap.py --dry-run --no-spot-check
```

---

## Exclusion rules (routes the script intentionally ignores)

Defined at the top of `scripts/sync_sitemap.py`:
- `EXCLUDE_PREFIXES` — admin, api/, webhook, health, og/, static, visa-intake, tearsheet, subscribe
- `EXCLUDE_SUFFIXES` — .txt, .xml, .pdf
- `EXCLUDE_EXACT` — sitemap.xml, robots.txt, printable noindex form pages, 301 redirect aliases
- `EXCLUDE_CONTAINS` — indexnow, noindex
- Routes with `<param>` segments are always skipped (parametric = dynamic = handled by DB walks)

To add a new permanent exclusion (e.g. a new redirect alias), append to `EXCLUDE_EXACT` in the script.

---

## Repo
`https://github.com/jht243/ven_biz_network`
Main branch: `main`
Last commit touching this tool: `0950bce` — "Add nightly sitemap audit and sync agent"
