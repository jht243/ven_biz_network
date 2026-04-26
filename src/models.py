import enum
from datetime import datetime, date
from threading import Lock

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Text,
    Float,
    Date,
    DateTime,
    Enum,
    Boolean,
    JSON,
    LargeBinary,
    UniqueConstraint,
)
from sqlalchemy import inspect as sa_inspect, text as sa_text
from sqlalchemy.orm import declarative_base, sessionmaker

from src.config import settings

Base = declarative_base()


def _snake_case(name: str) -> str:
    """SourceType -> source_type"""
    out = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0 and not name[i - 1].isupper():
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def _enum_values(enum_cls):
    """Tell SQLAlchemy to use enum .value (lowercase) instead of .name (uppercase)
    when serializing to Postgres, and bind to the snake_case Postgres enum type
    name (e.g. SourceType -> source_type). Without values_callable, inserts send
    the uppercase Python identifier (e.g. "GDELT") which doesn't match the
    lowercase Postgres enum values (e.g. "gdelt").
    """
    return Enum(
        enum_cls,
        values_callable=lambda x: [e.value for e in x],
        name=_snake_case(enum_cls.__name__),
    )


class SourceType(str, enum.Enum):
    GACETA_OFICIAL = "gaceta_oficial"
    TU_GACETA = "tu_gaceta"
    ASAMBLEA_NACIONAL = "asamblea_nacional"
    TSJ = "tsj"
    FEDERAL_REGISTER = "federal_register"
    OFAC_SDN = "ofac_sdn"
    GDELT = "gdelt"
    BCV_RATES = "bcv_rates"
    TRAVEL_ADVISORY = "travel_advisory"
    NEWSDATA = "newsdata"
    EIA = "eia"
    GOOGLE_NEWS = "google_news"
    ITA_TRADE = "ita_trade"
    # Cross-project pollution recovery (April 2026): the shared
    # Postgres enum had `openalex` added by a sister project that
    # was misconfigured to point at this database. We declare it
    # here so SQLAlchemy can decode the existing rows without
    # crashing the report renderer; downstream queries should
    # filter these out (see report_generator / blog_generator).
    OPENALEX = "openalex"


class CredibilityTier(str, enum.Enum):
    OFFICIAL = "official"
    TIER1 = "tier1"
    TIER2 = "tier2"
    STATE = "state"


class GazetteStatus(str, enum.Enum):
    SCRAPED = "scraped"
    OCR_COMPLETE = "ocr_complete"
    OCR_FAILED = "ocr_failed"
    ANALYZED = "analyzed"
    APPROVED = "approved"
    SENT = "sent"


class GazetteType(str, enum.Enum):
    ORDINARIA = "ordinaria"
    EXTRAORDINARIA = "extraordinaria"


class GazetteEntry(Base):
    __tablename__ = "gazette_entries"
    __table_args__ = (UniqueConstraint("source", "source_url", name="uq_source_url"),)

    id = Column(Integer, primary_key=True, autoincrement=True)

    gazette_number = Column(String(50), nullable=True, index=True)
    gazette_type = Column(_enum_values(GazetteType), default=GazetteType.ORDINARIA)
    published_date = Column(Date, nullable=False, index=True)
    source = Column(_enum_values(SourceType), nullable=False)
    source_url = Column(String(500), nullable=False)

    title = Column(Text, nullable=True)
    sumario_raw = Column(Text, nullable=True)

    pdf_path = Column(String(500), nullable=True)
    pdf_hash = Column(String(64), nullable=True, unique=True)
    pdf_download_url = Column(String(500), nullable=True)

    ocr_text = Column(Text, nullable=True)
    ocr_confidence = Column(Integer, nullable=True)

    analysis_json = Column(JSON, nullable=True)
    status = Column(_enum_values(GazetteStatus), default=GazetteStatus.SCRAPED)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AssemblyNewsEntry(Base):
    __tablename__ = "assembly_news"
    __table_args__ = (UniqueConstraint("source_url", name="uq_assembly_url"),)

    id = Column(Integer, primary_key=True, autoincrement=True)

    headline = Column(Text, nullable=False)
    published_date = Column(Date, nullable=False, index=True)
    source_url = Column(String(500), nullable=False)
    body_text = Column(Text, nullable=True)
    commission = Column(String(200), nullable=True)

    analysis_json = Column(JSON, nullable=True)
    status = Column(_enum_values(GazetteStatus), default=GazetteStatus.SCRAPED)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ExternalArticleEntry(Base):
    """Articles from external sources (Federal Register, GDELT, OFAC, etc.)."""

    __tablename__ = "external_articles"
    __table_args__ = (UniqueConstraint("source", "source_url", name="uq_ext_source_url"),)

    id = Column(Integer, primary_key=True, autoincrement=True)

    source = Column(_enum_values(SourceType), nullable=False, index=True)
    source_url = Column(String(1000), nullable=False)
    source_name = Column(String(200), nullable=True)
    credibility = Column(_enum_values(CredibilityTier), default=CredibilityTier.TIER2)

    headline = Column(Text, nullable=False)
    published_date = Column(Date, nullable=False, index=True)
    body_text = Column(Text, nullable=True)
    article_type = Column(String(100), nullable=True)

    tone_score = Column(Float, nullable=True)
    extra_metadata = Column(JSON, nullable=True)

    analysis_json = Column(JSON, nullable=True)
    status = Column(_enum_values(GazetteStatus), default=GazetteStatus.SCRAPED)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BlogPost(Base):
    """
    Long-form LLM-generated analysis post tied to a source entry.
    One blog post per ExternalArticle or AssemblyNews row that crosses the
    relevance threshold and has not yet been written about. Generated on
    a separate budget so the daily report run can stay cheap.
    """

    __tablename__ = "blog_posts"
    __table_args__ = (
        UniqueConstraint("source_table", "source_id", name="uq_blog_source"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)

    source_table = Column(String(50), nullable=False, index=True)
    source_id = Column(Integer, nullable=False, index=True)

    slug = Column(String(200), nullable=False, unique=True, index=True)
    title = Column(Text, nullable=False)
    subtitle = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    body_html = Column(Text, nullable=False)

    # Conversational, ~180-250 char "social hook" — written from one
    # analyst to another. Surfaces the tension or insight without
    # restating the title. Generated in the same LLM call as the post
    # body for new briefings; backfilled separately for old ones.
    # Used by social syndication (Bluesky etc.) so posts read like a
    # human wrote them rather than an RSS bot.
    social_hook = Column(Text, nullable=True)

    # Pre-rendered 1200x630 PNG bytes of the briefing's per-post Open
    # Graph card. Rendered once at blog-creation time (and backfilled
    # for old posts via scripts/backfill_og_images.py) so every share
    # preview shows the briefing's own headline rather than a generic
    # site-wide tile. Served by /og/briefing/<slug>.png. Typically
    # ~50-80 KB; well under any DB row limit.
    og_image_bytes = Column(LargeBinary, nullable=True)

    primary_sector = Column(String(80), nullable=True, index=True)
    sectors_json = Column(JSON, nullable=True)
    keywords_json = Column(JSON, nullable=True)
    related_slugs_json = Column(JSON, nullable=True)

    # 3-5 short "Key takeaways" bullets rendered as a scannable aside
    # at the top of /briefing/<slug>. Generated in the same LLM call
    # as the post body (src/blog_generator.py already emits a
    # `key_takeaways` array in its JSON schema) and backfilled for
    # legacy posts via scripts/backfill_takeaways.py. Surfaced on-
    # page by templates/blog_post.html.j2 and consumed by the
    # SEO/readability playbook: scannable bullets correlate with
    # better on-page CTR and time-on-page, both ranking signals
    # Google uses to decide whether to promote a "crawled - not
    # indexed" briefing into the index.
    takeaways_json = Column(JSON, nullable=True)

    word_count = Column(Integer, nullable=True)
    reading_minutes = Column(Integer, nullable=True)

    published_date = Column(Date, nullable=False, index=True)
    canonical_source_url = Column(String(1000), nullable=True)

    llm_model = Column(String(100), nullable=True)
    llm_input_tokens = Column(Integer, nullable=True)
    llm_output_tokens = Column(Integer, nullable=True)
    llm_cost_usd = Column(Float, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LandingPage(Base):
    """
    Evergreen landing pages — the pillar /invest-in-venezuela, the
    sector pages, the explainers. Generated less frequently than blog
    posts (e.g. weekly) and with the premium LLM model. Stored as
    pre-rendered HTML so the request path stays cheap.
    """

    __tablename__ = "landing_pages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    page_key = Column(String(120), nullable=False, unique=True, index=True)
    page_type = Column(String(40), nullable=False, index=True)

    title = Column(Text, nullable=False)
    subtitle = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    body_html = Column(Text, nullable=False)
    keywords_json = Column(JSON, nullable=True)
    sections_json = Column(JSON, nullable=True)

    sector_slug = Column(String(80), nullable=True, index=True)
    canonical_path = Column(String(200), nullable=False)
    word_count = Column(Integer, nullable=True)

    llm_model = Column(String(120), nullable=True)
    llm_input_tokens = Column(Integer, nullable=True)
    llm_output_tokens = Column(Integer, nullable=True)
    llm_cost_usd = Column(Float, nullable=True)

    last_generated_at = Column(DateTime, default=datetime.utcnow, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DistributionLog(Base):
    """
    Tracks every outbound distribution event (Google Indexing ping,
    Bluesky post, Mastodon post, Telegram broadcast, etc.). One row per
    (url, channel) attempt. Used both for idempotency (don't re-ping the
    same URL on the same channel within a cooldown window) and for
    operational diagnostics.

    Channels we plan to write into this table:
      - google_indexing      Google's Indexing API URL_UPDATED notification
      - bluesky              atproto post
      - mastodon             status post
      - telegram             channel broadcast
      - linkedin             company-page post
      - threads              Meta Threads post
      - medium               Medium import / canonical post
    """

    __tablename__ = "distribution_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)

    channel = Column(String(40), nullable=False, index=True)
    url = Column(String(1000), nullable=False, index=True)

    entity_type = Column(String(40), nullable=True)  # blog_post | landing_page | static
    entity_id = Column(Integer, nullable=True)

    success = Column(Boolean, nullable=False, default=False, index=True)
    response_code = Column(Integer, nullable=True)
    response_snippet = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class ClimateSnapshot(Base):
    """One row per calendar quarter. Stores the computed Investment
    Climate Tracker scorecard for that quarter plus the raw evidence
    used to derive it. Recomputed weekly by the climate runner; the row
    for the current quarter is upserted in place (keyed on quarter_label).
    Older rows are immutable and serve as the QoQ baseline for the next
    quarter.

    The report generator reads the most recent two rows: the latest is
    rendered as the current scorecard, and the one before it provides
    the deltas that produce the trend arrows on each bar.
    """

    __tablename__ = "climate_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)

    quarter_label = Column(String(16), nullable=False, unique=True, index=True)
    quarter_start = Column(Date, nullable=False, index=True)

    composite_score = Column(Float, nullable=True)
    period_label = Column(String(64), nullable=True)
    methodology = Column(Text, nullable=True)

    bars_json = Column(JSON, nullable=False)
    evidence_json = Column(JSON, nullable=True)

    computed_at = Column(DateTime, default=datetime.utcnow, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ScrapeLog(Base):
    """Tracks every scrape attempt for diagnostics and retry logic."""

    __tablename__ = "scrape_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(_enum_values(SourceType), nullable=False)
    scrape_date = Column(Date, nullable=False)
    success = Column(Boolean, nullable=False)
    entries_found = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


engine = create_engine(settings.database_url, echo=False)
SessionLocal = sessionmaker(bind=engine)
_init_lock = Lock()
_db_initialized = False


def init_db(*, force: bool = False):
    """Create tables once per process, not once per request.

    Also runs lightweight, idempotent column-additions for ALTERations
    that can't be expressed by `create_all` on a pre-existing table.
    We deliberately stop short of a full Alembic setup — for a single-
    writer schema this stays simpler and safer.
    """
    global _db_initialized
    if _db_initialized and not force:
        return
    with _init_lock:
        if _db_initialized and not force:
            return
        Base.metadata.create_all(engine)
        _ensure_columns()
        _ensure_enum_values()
        _db_initialized = True


def _ensure_columns() -> None:
    """Add columns that were introduced after the table was first
    created. Cross-DB (SQLite + Postgres) safe — uses the SQLAlchemy
    inspector to check for existence before issuing an ALTER.
    """
    insp = sa_inspect(engine)
    dialect = engine.dialect.name

    # Per-dialect column type. SQLite uses BLOB for binary, Postgres BYTEA.
    blob_type = "BYTEA" if dialect == "postgresql" else "BLOB"
    # SQLAlchemy's JSON type maps to JSONB on Postgres and TEXT on SQLite.
    # For idempotent ALTERs we mirror that ourselves.
    json_type = "JSONB" if dialect == "postgresql" else "TEXT"

    additions = [
        ("blog_posts", "social_hook", "TEXT"),
        ("blog_posts", "og_image_bytes", blob_type),
        ("blog_posts", "takeaways_json", json_type),
    ]

    for table_name, column_name, column_type in additions:
        if table_name not in insp.get_table_names():
            continue
        existing = {c["name"] for c in insp.get_columns(table_name)}
        if column_name in existing:
            continue
        with engine.begin() as conn:
            conn.execute(
                sa_text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
            )


# ── Enum value additions ───────────────────────────────────────────────
# Postgres enum types are immutable once created — SQLAlchemy's
# create_all() will create the enum with the values present at first
# run, but it WILL NOT add new values when the Python enum grows. We
# have to ALTER TYPE manually. SQLite stores enum columns as VARCHAR
# so this is a no-op there — the column already accepts any string.
#
# Idempotent via "ADD VALUE IF NOT EXISTS". The ALTER must run outside
# an explicit transaction on older PG versions, so we use AUTOCOMMIT.
# Failures are logged but never raise — a missing enum value will surface
# as a row-insert error downstream and is preferable to a crashed init.
_SOURCE_TYPE_ENUM_ADDITIONS: tuple[tuple[str, str], ...] = (
    ("source_type", "google_news"),
    ("source_type", "ita_trade"),
)


def _ensure_enum_values() -> None:
    if engine.dialect.name != "postgresql":
        return

    import logging
    log = logging.getLogger(__name__)

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for enum_name, value in _SOURCE_TYPE_ENUM_ADDITIONS:
            try:
                conn.execute(
                    sa_text(f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS '{value}'")
                )
            except Exception as exc:
                log.warning(
                    "Could not add enum value %r to %s (continuing anyway): %s",
                    value, enum_name, exc,
                )
