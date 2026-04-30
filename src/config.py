from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    database_url: str = "sqlite:///./venezuela_journal.db"
    storage_dir: Path = Path("./storage")
    output_dir: Path = Path("./output")

    log_level: str = "INFO"

    # Scraper
    scraper_timeout_seconds: int = 30
    scraper_max_retries: int = 3
    scraper_retry_delay_seconds: int = 60
    scraper_lookback_days: int = 30

    # Tesseract
    tesseract_cmd: str = "tesseract"
    tesseract_lang: str = "spa"

    # Source URLs
    gazette_official_url: str = "http://www.gacetaoficial.gob.ve"
    gazette_tugaceta_url: str = "https://tugacetaoficial.com"
    assembly_url: str = "https://www.asambleanacional.gob.ve"
    tsj_url: str = "https://www.tsj.gob.ve/gaceta-oficial"

    # LLM Analysis
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    # Cheaper model used for short, prose-only generation tasks where the
    # facts are already structured (e.g. /lab entity-page narratives).
    # Keeps cost trivial: ~$0.0002/page at gpt-4o-mini pricing, and we
    # cache by content fingerprint so reruns are free. Override via
    # OPENAI_NARRATIVE_MODEL env var.
    openai_narrative_model: str = "gpt-4o-mini"
    analysis_min_relevance: int = 5
    # Wide enough to cover a full year of backfilled official-source content
    # by default. Override via REPORT_LOOKBACK_DAYS in env if you want a
    # shorter rolling window.
    report_lookback_days: int = 120
    # Hard cap on LLM calls per pipeline run. Default 200 calls/run
    # ≈ ~$1.20 at current gpt-4o pricing (~$0.006/call). With the cron
    # firing twice a day that's ~$2.40/day worst case, well inside a
    # $5/day budget. Override via LLM_CALL_BUDGET_PER_RUN env var.
    llm_call_budget_per_run: int = 200
    # Approximate gpt-4o pricing for the cost-estimate log line. Update
    # if you switch models or pricing changes. Values are USD per 1M tokens.
    llm_input_price_per_mtok: float = 2.50
    llm_output_price_per_mtok: float = 10.00

    # Premium model — used ONLY for evergreen, high-traffic landing
    # content (pillar page, sector landing pages, evergreen explainers).
    # Keep gpt-4o for the daily news churn (analyzer + blog_generator)
    # because that runs hundreds of times/day; reserve the premium model
    # for the ~10 pages that need to read like a senior analyst wrote
    # them. Override via OPENAI_PREMIUM_MODEL env var.
    openai_premium_model: str = "gpt-5.2"
    llm_premium_input_price_per_mtok: float = 5.00
    llm_premium_output_price_per_mtok: float = 15.00

    # Newsletter
    newsletter_provider: str = "console"
    newsletter_from_email: str = "briefing@venezuelanbusiness.net"
    newsletter_api_key: str = ""
    subscriber_list_path: str = "subscribers.json"
    seo_email_provider: str = "resend"
    seo_email_recipient: str = "<RECIPIENT_EMAIL>"
    seo_email_subject: str = "SEO Updates <SITE_NAME>"
    resend_api_key: str = ""

    # Stripe checkout notifications for the paid Venezuela visa service.
    # Set STRIPE_WEBHOOK_SECRET to the endpoint signing secret from Stripe,
    # and VISA_ORDER_NOTIFICATION_EMAIL to the address that should receive
    # a notice when a checkout session completes.
    stripe_webhook_secret: str = ""
    stripe_visa_payment_link_id: str = "plink_1TS1T2GWYDLBQAio3Z6tN9N2"
    visa_order_notification_email: str = ""
    visa_order_email_provider: str = "resend"

    # Google reporting (GA4 + Search Console)
    google_reporting_sa_json: str = ""
    google_reporting_sa_file: str = ""
    google_reporting_ga4_property_id: str = ""
    google_reporting_gsc_site_url: str = ""
    google_reporting_output_dir: Path = Path("./output/google_reporting")
    google_reporting_ga_lookback_days: int = 30
    google_reporting_gsc_lookback_days: int = 90

    # Buttondown (subscriber signup)
    buttondown_api_key: str = ""

    # Supabase Storage (used to share report.html between cron + web on Render)
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_report_bucket: str = "reports"
    # Object key for the homepage report HTML inside the bucket. MUST be
    # unique per project when multiple projects share a Supabase bucket
    # (e.g. different sites running in the same Render workspace). The
    # legacy default "report.html" is the cross-project collision vector
    # we hit in April 2026 — production Caracas now uses
    # "caracas-report.html" (set via SUPABASE_REPORT_OBJECT_KEY in Render).
    supabase_report_object_key: str = "report.html"

    # Server
    server_port: int = 8080

    # ── Admin endpoints ────────────────────────────────────────────────
    # Bearer token for /admin/* routes (e.g. /admin/regen-report which
    # re-renders the static homepage report.html using current code +
    # current DB content, without paying for a full scrape/LLM/email
    # pipeline run). Leave blank to disable the endpoints entirely.
    # Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"
    admin_token: str = ""

    # SEO / canonical URL — base URL of the deployed site. Used for
    # canonical <link>, sitemap entries, JSON-LD identifiers, and OG
    # share URLs. Override via SITE_URL env var when a custom domain
    # is added (Tier 4).
    site_url: str = "https://caracasresearch.com"
    site_name: str = "Caracas Research"
    site_owner_org: str = "Caracas Research"
    site_locale: str = "en_US"

    # Long-form blog post generator. Each post is roughly 700-900 words and
    # uses ~2-3k completion tokens, so each call costs ~$0.04. The budget
    # caps total post generations per pipeline run.
    blog_gen_budget_per_run: int = 6
    blog_gen_min_relevance: int = 5
    blog_gen_lookback_days: int = 14
    blog_gen_max_words: int = 900

    # ── Google News intake cap ─────────────────────────────────────────
    # Maximum number of NEW Google News articles to persist per
    # calendar day. The pipeline ranks all candidates by an
    # investor-interest heuristic and persists only the top N. This
    # is intentionally aligned with blog_gen_budget_per_run so that
    # every persisted article has a 1:1 chance of becoming a blog
    # post on the same cron tick. Set to 0 to disable Google News
    # intake entirely without removing the scraper.
    google_news_daily_cap: int = 6

    # ── Distribution: IndexNow (Bing, Yandex, Seznam, Naver, Mojeek) ──
    # The IndexNow key — generated in Bing Webmaster Tools at
    # bing.com/indexnow/getstarted. The key is NOT a secret: it's
    # publicly hosted at /{key}.txt to prove domain ownership, and
    # included in every API call. The default below is a development
    # placeholder; production must override via INDEXNOW_KEY env var.
    indexnow_key: str = "0b2fff2a4cb56ba2c10382745f51cdd8"

    # ── Distribution: Google Indexing API ──────────────────────────────
    # Service-account JSON pasted as a single env var (the entire JSON
    # blob, including the curly braces and the embedded \n in
    # private_key). The runner uses this to ping the Indexing API on
    # every newly-published BlogPost URL and on the homepage when the
    # daily report regenerates. Leave blank to disable.
    google_indexing_sa_json: str = ""
    # Alternate: path to the JSON file on disk (used by Render "secret
    # files" mounts). Only consulted when google_indexing_sa_json is empty.
    google_indexing_sa_file: str = ""
    # Only ping URLs newer than this many days. Avoids burning quota on
    # the entire historical backlog the first time the feature ships;
    # Google's regular crawl already knows about old content.
    google_indexing_lookback_days: int = 7
    # Hard cap per pipeline run — Indexing API quota is 200 URLs/day per
    # GCP project. This is a runtime safety belt; the cron fires twice a
    # day so this is well within the daily quota.
    google_indexing_max_per_run: int = 50

    # ── Distribution: Internet Archive (archive.org) ───────────────────
    # S3-like access keys from https://archive.org/account/s3.php
    # Both must be set for the channel to activate; either blank → channel
    # is silently skipped.
    internet_archive_access_key: str = ""
    internet_archive_secret_key: str = ""
    # The IA collection to deposit into. 'opensource' is the catch-all
    # uncurated collection; getting into a curated one requires a manual
    # request to IA staff. Override only if/when we get accepted into
    # one (e.g. 'opensource_periodicals' for a serial of issues).
    internet_archive_collection: str = "opensource"
    # Hard cap per cron run — protects against runaway uploads if a bug
    # ever produces 1000 tearsheets in one go. Daily cron only ever
    # publishes one per run so 5 is plenty of headroom.
    internet_archive_max_per_run: int = 5

    # ── Distribution: Zenodo (CERN-operated open repository) ───────────
    # Zenodo gives every uploaded record a permanent DOI and is indexed by
    # Google Search + Google Dataset Search + OpenAIRE. Generate a token
    # at https://zenodo.org/account/settings/applications/tokens/new/
    # with scopes `deposit:write` and `deposit:actions`. Leave blank to
    # disable the channel.
    zenodo_access_token: str = ""
    # Set to "1" to publish to https://sandbox.zenodo.org instead of
    # production. Useful for first-run smoke tests; sandbox DOIs are not
    # real and records are wiped periodically.
    zenodo_use_sandbox: bool = False
    # Optional Zenodo "community" slug (e.g. "venezuela-research"). If
    # set, every deposit requests inclusion in that community — the
    # community owners then approve/reject. Leave blank to skip community
    # association.
    zenodo_community: str = ""
    # Hard cap per cron run — twice-daily cron × 1 tearsheet = 2 max,
    # this is a runtime safety belt.
    zenodo_max_per_run: int = 3

    # ── Distribution: OSF Preprints (Open Science Framework) ───────────
    # OSF Preprints IS indexed by Google Scholar (the main reason we use
    # it over plain Zenodo). Generate a Personal Access Token at
    # https://osf.io/settings/tokens/ with scope `osf.full_write`.
    # Leave blank to disable.
    osf_access_token: str = ""
    # The OSF "node" (project) GUID under which all daily tearsheets are
    # stored. Create one project manually at osf.io and paste its 5-char
    # GUID here (the part of the URL after osf.io/). Each daily PDF is
    # uploaded to this node and then registered as a child preprint.
    osf_project_node_id: str = ""
    # The OSF Preprints provider GUID. "osf" is the generic OSF provider
    # which accepts almost anything. Other options:
    #   "socarxiv"  → Social Sciences (good fit for investment research)
    #   "metaarxiv" → Metascience
    # Leave at "osf" unless you've contacted a specific provider's
    # moderators about ongoing daily submissions.
    osf_preprint_provider: str = "osf"
    # OSF requires a "subjects" taxonomy ID (BePress taxonomy). The
    # default below is "Social and Behavioral Sciences" → "Economics".
    # Override only if you change the preprint provider, since each
    # provider has its own subject whitelist.
    osf_subject_id: str = "584240da54be81056cecaab4"  # Economics
    # SPDX license ID for the deposit. CC-BY-4.0 is permissive +
    # attribution, matching the IA "free to share with attribution"
    # rights statement.
    osf_license_name: str = "CC-By Attribution 4.0 International"
    # Hard cap per cron run.
    osf_max_per_run: int = 3

    # ── Distribution: Bluesky (atproto) ────────────────────────────────
    # Bluesky handle (e.g. "caracasresearch.bsky.social") and an app
    # password (NOT the main account password) generated under Settings →
    # Privacy and Security → App Passwords. Leave either blank to disable.
    bluesky_handle: str = ""
    bluesky_app_password: str = ""
    # Only post briefings created within this many days. Avoids spamming
    # the historical backlog when the feature first ships, and keeps the
    # feed feeling like fresh news rather than a re-run.
    bluesky_lookback_days: int = 2
    # Hard cap per cron run. Twice-daily cron × 5 = 10 posts/day max.
    # Realistic new-briefing volume is ~3-6/run so this is a safety belt.
    bluesky_max_per_run: int = 5


settings = Settings()

# Ensure directories exist
settings.storage_dir.mkdir(parents=True, exist_ok=True)
(settings.storage_dir / "pdfs").mkdir(exist_ok=True)
(settings.storage_dir / "ocr_output").mkdir(exist_ok=True)
settings.output_dir.mkdir(parents=True, exist_ok=True)
settings.google_reporting_output_dir.mkdir(parents=True, exist_ok=True)
