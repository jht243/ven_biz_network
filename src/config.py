from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

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
    report_lookback_days: int = 7

    # Newsletter
    newsletter_provider: str = "console"
    newsletter_from_email: str = "briefing@venezuelanbusiness.net"
    newsletter_api_key: str = ""
    subscriber_list_path: str = "subscribers.json"

    # Buttondown (subscriber signup)
    buttondown_api_key: str = ""

    # Server
    server_port: int = 8080


settings = Settings()

# Ensure directories exist
settings.storage_dir.mkdir(parents=True, exist_ok=True)
(settings.storage_dir / "pdfs").mkdir(exist_ok=True)
(settings.storage_dir / "ocr_output").mkdir(exist_ok=True)
settings.output_dir.mkdir(parents=True, exist_ok=True)
