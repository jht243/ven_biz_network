# Venezuela Investment Journal

Daily investor briefing for Venezuela. Scrapes official government sources, runs an LLM analyst pass, and publishes a single static `report.html` to a public site.

> **Live site:** the Render web service serves the latest generated `report.html` from Supabase Storage.
> **Refresh schedule:** every day at 10:00 UTC via a Render cron job.

---

## 1. The Big Picture (read this first, future agent)

There are **three layers** that frequently get confused — keep them straight:

| Layer | What it is | Where it lives |
|-------|------------|----------------|
| **Static demo HTML** | A *hand-written* `report.html` at the repo root with hardcoded headlines (UPI links, "Sanctions pilgrimage", etc.). Used as a design reference / fallback. | `report.html` (root) |
| **Jinja template** | The dynamic template that the pipeline renders against real DB rows. | `templates/report.html.j2` |
| **Generated report** | The output of `src/report_generator.py` — written to `output/report.html` and uploaded to Supabase Storage. **This is what the live site serves.** | `output/report.html` + Supabase `reports` bucket |

If the live site looks empty or sparse, it is **almost always** because the scrapers found 0 rows for the current `report_lookback_days` window. The static `report.html` at the root is **not** automatically served — Supabase Storage is checked first, then `output/report.html`.

The previous "rich" page the user saw on April 15 was the **static demo HTML** that happened to be the local `output/report.html` from a prior local generation. Once Supabase Storage was wired up, the live site started serving the *real* generated report — which was nearly empty because:

- GDELT is rate-limiting Render's IP range (HTTP 429).
- BCV's site (`bcv.org.ve`) is unreachable from Render's network (DNS / ASN block).
- Federal Register, Asamblea Nacional, and Gaceta Oficial scrapers all only fetch **today's** date by default — so the DB only ever holds ~1 day of content.

The fix is to **backfill** historical data. See §6.

---

## 2. Data Sources

| Source | Type | Scraper | Status (Apr 2026) | Notes |
|--------|------|---------|-------------------|-------|
| **US Federal Register** (OFAC docs) | Official | `federal_register.py` | ✅ Working | Free API, no key, supports date ranges. |
| **Asamblea Nacional de Venezuela** | Official (govt) | `assembly.py` | ✅ Working | Date-range query via `?inicio=...&fin=...`. |
| **Gaceta Oficial (TuGacetaOficial mirror)** | Official (govt) | `gazette.py::TuGacetaScraper` | ✅ Working | Yearly listing page filtered by date. |
| **Gaceta Oficial (gov portal)** | Official (govt) | `gazette.py::OfficialGazetteScraper` | ⚠️  Frequently DNS-blocked from Render | Used as a redundant source. |
| **OFAC SDN List** | Official (US Treasury) | `ofac_sdn.py` | ✅ Working | Snapshot-diff approach. **High volume:** 400+ entities. Routed through rule-based analysis (no LLM cost). |
| **US State Dept Travel Advisory** | Official | `travel_advisory.py` | ✅ Working | Single record per scrape. |
| **GDELT** (international press wire) | News aggregator | `gdelt.py` | ❌ Rate-limited from Render IPs | Need a proxy or alternative news API. |
| **BCV (Banco Central)** exchange rate | Govt | `bcv.py` | ❌ DNS unreachable from Render | Need a proxy or alternative rates source. |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Render Cron (vij-daily-pipeline) — runs `python run_daily.py`  │
│   1. Scrape today's data from all sources (src/pipeline.py)     │
│   2. Persist to Supabase Postgres                               │
│   3. LLM analysis pass (src/analyzer.py)                        │
│      - Rule-based templating for OFAC SDN (no LLM call)         │
│      - LLM budget cap: 30 calls/run                             │
│      - Pre-filter: keyword + GDELT tone score                   │
│   4. Generate report.html (src/report_generator.py)             │
│   5. Upload report.html → Supabase Storage `reports/` bucket    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Render Web (venezuela-investment-journal) — gunicorn server.py │
│   GET /          → serves report.html from Supabase (60s cache) │
│   POST /api/subscribe → Buttondown signup                       │
│   GET /health    → status JSON                                  │
└─────────────────────────────────────────────────────────────────┘
```

**Why Supabase Storage in the middle?** Render web and Render cron are *separate ephemeral services* — they don't share a filesystem. The cron writes the report; the web reads it. Supabase Storage is the bridge.

---

## 4. Local Development

```bash
# 1. Install deps
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Fill in: DATABASE_URL (Supabase pooler), OPENAI_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY, BUTTONDOWN_API_KEY

# 3. Apply migrations
alembic upgrade head

# 4. Run the daily pipeline (today)
python run_daily.py

# 5. Backfill historical dates (see §6)
python run_backfill.py --start-date 2026-01-01

# 6. Just regenerate the report from existing DB rows
python run_daily.py --report-only

# 7. Serve locally
python server.py   # http://localhost:8080
```

---

## 5. Environment Variables

See `.env.example` for the full list. The non-obvious ones:

| Var | Purpose | Required for |
|-----|---------|--------------|
| `DATABASE_URL` | Supabase Postgres **session pooler** URL (`aws-1-us-east-1.pooler.supabase.com:5432`). The transaction pooler does not work with SQLAlchemy's connection model. | cron + web |
| `SUPABASE_URL` | `https://<project>.supabase.co` — used to read & write the report bucket. | cron (write) + web (read) |
| `SUPABASE_SERVICE_KEY` | `service_role` JWT. **Cron only** — needed to upload to Storage. | cron |
| `SUPABASE_REPORT_BUCKET` | Defaults to `reports`. Bucket must be **public** and allow `text/html` MIME. | cron + web |
| `REPORT_LOOKBACK_DAYS` | How many days back the report includes. Default `120` (covers Jan 1 → mid-April). | cron (report gen) |
| `SCRAPER_LOOKBACK_DAYS` | How far back each scraper looks per run (only used by Federal Register today). | cron |
| `OPENAI_MODEL` | `gpt-4o` recommended. The analyzer caps total LLM calls per run via `LLM_CALL_BUDGET_PER_RUN` in `src/analyzer.py`. | cron |

---

## 6. Backfilling Historical Data

When the DB is empty (e.g., after a reset, or after a long outage of a source), use the backfill script:

```bash
# Default: backfill Federal Register, Asamblea Nacional, both Gaceta scrapers,
# OFAC SDN, and Travel Advisory from 2026-01-01 to today.
python run_backfill.py

# Custom range
python run_backfill.py --start-date 2026-01-01 --end-date 2026-04-15

# Pick specific sources
python run_backfill.py --sources federal_register,asamblea_nacional

# Skip the analyzer + report generation (just scrape)
python run_backfill.py --skip-analyze --skip-report
```

Notes:
- **Federal Register** is fetched in a single API call covering the whole range.
- **Asamblea Nacional** and **Gaceta Oficial** loop one day at a time (~100 HTTP requests each for a 100-day backfill). Be patient.
- **OFAC SDN** is snapshot-diff based — backfilling only captures the *current* SDN state, not historical changes.
- The script reuses `src/pipeline.py`'s `_persist_*` functions, so duplicates are silently dropped.

---

## 7. LLM Cost Management

The OFAC SDN list contains ~400 Venezuela-program entities. Sending all of them to GPT-4o costs ~$4 per run. To prevent runaway cost, `src/analyzer.py` enforces:

1. **Rule-based templating** for OFAC SDN entries — no LLM call. Fixed `relevance_score=4` so they don't clutter the main report. (Use the bundled "12 new designations today" summary card instead.)
2. **Pre-filter** for everything else: must contain a Venezuela-relevant keyword (`RELEVANCE_KEYWORDS`) and, for GDELT, exceed `GDELT_TONE_THRESHOLD` (3.0).
3. **Hard cap** of `LLM_CALL_BUDGET_PER_RUN = 30` calls per run, prioritized by source authority (Federal Register > Travel Advisory > GDELT) and tone magnitude.

If you change these constants, expect cost to scale linearly.

---

## 8. Deployment (Render)

`render.yaml` defines two services:

- `venezuela-investment-journal` (web) — `gunicorn server:app`, health check at `/health`.
- `vij-daily-pipeline` (cron) — `python run_daily.py`, schedule `0 10 * * *` (10 UTC daily).

Both services share env vars including `DATABASE_URL`, `SUPABASE_*`, `OPENAI_API_KEY`, `BUTTONDOWN_API_KEY`. **`SUPABASE_SERVICE_KEY` is only needed by the cron** (web only reads the public bucket).

To trigger an out-of-cycle run: use the Render dashboard "Trigger Run" button on the cron service, or call the REST API:

```bash
curl -X POST "https://api.render.com/v1/services/$CRON_ID/jobs" \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"startCommand": "python run_daily.py"}'
```

---

## 9. Common Pitfalls (lessons learned)

1. **Wrong Supabase pooler hostname.** New projects are on `aws-1-us-east-1.pooler.supabase.com`, not `aws-0-`. Symptom: `FATAL: Tenant or user not found`.
2. **SQLAlchemy enum mismatch.** Postgres enums are lowercase (`gdelt`); SQLAlchemy was sending `GDELT`. `src/models.py` patches this with `_enum_values()` — do not bypass.
3. **Bucket MIME types.** Supabase Storage rejects `text/html; charset=utf-8` by default. The `reports` bucket must explicitly allow `text/html` and `text/html; charset=utf-8`.
4. **Render health check.** Default `/` returns 503 when the report has not been generated yet, breaking deploys. `healthCheckPath` is set to `/health`.
5. **`db.rollback()` is per-transaction, not per-row.** A single duplicate insert was wiping out an entire batch. `_persist_*` functions in `src/pipeline.py` now wrap each insert in `db.begin_nested()` (savepoint).
6. **OFAC SDN entries had identical `source_url`.** Scraper now appends `#sdn-{uid}-{change_type}` to make them unique.
7. **Static `report.html` at the root is a *fallback fixture*, not the live page.** See §1.

---

## 10. File Map

```
src/
  pipeline.py            # Orchestrates per-day scrape + persist
  analyzer.py            # LLM analysis with budget + pre-filter
  report_generator.py    # DB rows → templates/report.html.j2 → output/report.html
  storage_remote.py      # Supabase Storage upload/fetch
  models.py              # SQLAlchemy models + Enum value-callable patch
  config.py              # Pydantic settings (env-driven)
  scraper/               # One module per source (see §2)
templates/report.html.j2 # Jinja template (the *real* live design)
report.html              # Hand-written demo (fallback / design reference)
run_daily.py             # Cron entrypoint
run_backfill.py          # Backfill historical dates (see §6)
server.py                # Flask web entrypoint
render.yaml              # Render service definitions
alembic/                 # DB migrations
```
