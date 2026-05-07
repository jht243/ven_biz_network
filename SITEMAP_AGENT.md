# Sitemap Sync Agent — Handoff Note

> When the user says **"continue the sitemap tool"**, this is the file to read first.

---

## What exists

### 1. Dynamic sitemap routes (server.py)

**`/sitemap.xml`** — `sitemap_xml()` at ~line 9187. Combines:
- `static_urls` — hardcoded list of ~50 evergreen pages (tools, sanctions, visa, people, etc.)
- Dynamic walks — visa variants, people profiles, real estate, SDN dossiers
- DB queries — `BlogPost` slugs, `LandingPage` records, sector slugs from analysis JSON
- `_HIGH_DEMAND_PROFILE_SLUGS` — hand-curated whitelist of leaf URLs with proven GSC demand

**`/news-sitemap.xml`** — `news_sitemap_xml()` at ~line 9667. Google News-spec feed with only posts from the last 48 hours.

**`/robots.txt`** — `robots_txt()` at ~line 9170. Points at both sitemaps.

### 2. LandingPage route-existence guard (server.py)

LandingPages from the DB are validated against Flask's URL map before inclusion in the sitemap. If a `LandingPage.canonical_path` has no matching route handler, it's silently skipped with a log warning. This prevents orphaned DB records (like the old `/law` page) from advertising 404s to Google.

### 3. Nightly sync script

**`scripts/sync_sitemap.py`** — runs nightly at 02:00 UTC via Render cron.

What it does on each run:
1. Fetches the live sitemap at `https://caracasresearch.com/sitemap.xml`
2. Parses `server.py` for all non-parametric public `@app.route(...)` declarations
3. Diffs: routes in code but absent from the live sitemap → auto-inserted into `static_urls`
4. Spot-checks 25 random live sitemap URLs for HTTP 4xx/5xx (dead link detection)
5. Commits and pushes `server.py` if anything was added

### 4. Render cron job

**`render.yaml`** — `vij-nightly-sitemap-sync` runs daily at 02:00 UTC.

Required env vars (set via Render dashboard, not committed):
- `GITHUB_TOKEN` — GitHub fine-grained PAT with **Contents: Read & Write** on `jht243/ven_biz_network`
- `GITHUB_REPO` — already set to `jht243/ven_biz_network` in render.yaml

### 5. Related distribution scripts

- `scripts/indexnow_submit.py` — one-shot bulk IndexNow submission to Bing/Yandex/Seznam/Naver
- `src/distribution/google_indexing.py` — Google Indexing API v3 client (URL_UPDATED pings)
- `src/distribution/runner.py` — orchestrates Google Indexing + IndexNow on each cron run

### 6. GSC / Bing status

Both sitemaps are already submitted to Google Search Console:
- `https://caracasresearch.com/sitemap.xml` — 313 URLs, 0 errors
- `https://caracasresearch.com/news-sitemap.xml` — 12 URLs, 0 errors
- `https://www.caracasresearch.com/sitemap.xml` — 160 URLs, 160 errors (stale www submission)

The `www.` sitemap should be deleted from GSC — the canonical is non-www.

---

## Insertion anchor (for sync_sitemap.py)

The script looks for this exact text in `server.py` to know where to splice new static entries:

```python
        {"loc": f"{base}/tools/venezuela-visa-requirements", "lastmod": today_iso, "changefreq": "monthly", "priority": "0.6"},
    ]
```

If `server.py` changes and this line moves, update `INSERTION_ANCHOR` in the script.

---

## Known issues / next steps

### 1. ✅ `/law` dead link — FIXED
Stale `LandingPage` DB record (`pillar:law-and-policy`, canonical_path `/law`) with no matching route. Fixed by adding URL adapter validation in `sitemap_xml()` that skips LandingPages whose `canonical_path` doesn't resolve to a Flask route.

### 2. More dead links from DB (runtime 404s)
The spot-check also found:
- `/psychedelic-research-landscape` — another orphaned LandingPage (no route, now filtered by URL adapter guard)
- `/sectors/realestate` — matches the `sector_page` route pattern but 404s at runtime because no sector content exists for slug `realestate`. This is a **data issue** in the sector slug generation (analysis JSON produces "Real Estate" → slugified to "realestate" but no sector page content exists). The URL adapter guard does NOT catch this — it only checks route pattern matching, not content existence.

**Fix options for `/sectors/realestate`**:
- Add a sector redirect from `/sectors/realestate` → `/real-estate`
- Or clean up the analysis_json sector names to produce consistent slugs

### 3. `/travel/emergency-card` missing from sitemap
Route exists in server.py but isn't in the live sitemap. The nightly sync script will auto-add it on the next full run.

### 4. GITHUB_TOKEN not yet set on Render
The Render cron job is configured in render.yaml but the `GITHUB_TOKEN` env var needs to be added manually in the Render dashboard before the push step will work. Without it, the script audits and detects changes but can't commit/push.

**How to create the token:**
1. GitHub → Settings → Developer settings → Fine-grained personal access tokens
2. Repository: `jht243/ven_biz_network`
3. Permissions: Contents → Read & Write
4. Copy the token and paste it as `GITHUB_TOKEN` in Render dashboard for the `vij-nightly-sitemap-sync` cron job

### 5. Stale `www.` sitemap in GSC
`https://www.caracasresearch.com/sitemap.xml` has 160 errors in GSC. It should be deleted since the canonical domain is `caracasresearch.com` (no www). Use GSC → Sitemaps → select the www version → Remove.

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
- Routes with `<param>` segments are always skipped (parametric = handled by DB walks)

---

## Repo
- GitHub: `https://github.com/jht243/ven_biz_network`
- Main branch: `main`
