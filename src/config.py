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

    # Buttondown (subscriber signup)
    buttondown_api_key: str = ""

    # Supabase Storage (used to share report.html between cron + web on Render)
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_report_bucket: str = "reports"

    # Server
    server_port: int = 8080

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


settings = Settings()

# Ensure directories exist
settings.storage_dir.mkdir(parents=True, exist_ok=True)
(settings.storage_dir / "pdfs").mkdir(exist_ok=True)
(settings.storage_dir / "ocr_output").mkdir(exist_ok=True)
settings.output_dir.mkdir(parents=True, exist_ok=True)
