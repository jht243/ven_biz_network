import enum
from datetime import datetime, date

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
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, sessionmaker

from src.config import settings

Base = declarative_base()


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
    gazette_type = Column(Enum(GazetteType), default=GazetteType.ORDINARIA)
    published_date = Column(Date, nullable=False, index=True)
    source = Column(Enum(SourceType), nullable=False)
    source_url = Column(String(500), nullable=False)

    title = Column(Text, nullable=True)
    sumario_raw = Column(Text, nullable=True)

    pdf_path = Column(String(500), nullable=True)
    pdf_hash = Column(String(64), nullable=True, unique=True)
    pdf_download_url = Column(String(500), nullable=True)

    ocr_text = Column(Text, nullable=True)
    ocr_confidence = Column(Integer, nullable=True)

    analysis_json = Column(JSON, nullable=True)
    status = Column(Enum(GazetteStatus), default=GazetteStatus.SCRAPED)

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
    status = Column(Enum(GazetteStatus), default=GazetteStatus.SCRAPED)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ExternalArticleEntry(Base):
    """Articles from external sources (Federal Register, GDELT, OFAC, etc.)."""

    __tablename__ = "external_articles"
    __table_args__ = (UniqueConstraint("source", "source_url", name="uq_ext_source_url"),)

    id = Column(Integer, primary_key=True, autoincrement=True)

    source = Column(Enum(SourceType), nullable=False, index=True)
    source_url = Column(String(1000), nullable=False)
    source_name = Column(String(200), nullable=True)
    credibility = Column(Enum(CredibilityTier), default=CredibilityTier.TIER2)

    headline = Column(Text, nullable=False)
    published_date = Column(Date, nullable=False, index=True)
    body_text = Column(Text, nullable=True)
    article_type = Column(String(100), nullable=True)

    tone_score = Column(Float, nullable=True)
    extra_metadata = Column(JSON, nullable=True)

    analysis_json = Column(JSON, nullable=True)
    status = Column(Enum(GazetteStatus), default=GazetteStatus.SCRAPED)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ScrapeLog(Base):
    """Tracks every scrape attempt for diagnostics and retry logic."""

    __tablename__ = "scrape_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(Enum(SourceType), nullable=False)
    scrape_date = Column(Date, nullable=False)
    success = Column(Boolean, nullable=False)
    entries_found = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


engine = create_engine(settings.database_url, echo=False)
SessionLocal = sessionmaker(bind=engine)


def init_db():
    Base.metadata.create_all(engine)
