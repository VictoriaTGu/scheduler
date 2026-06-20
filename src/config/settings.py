"""Application configuration from environment variables."""

from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    """Application settings loaded from .env or environment."""

    # Email Configuration
    email_to: str
    email_from: str
    email_provider: str = "sendgrid"  # sendgrid or smtp

    # SendGrid
    sendgrid_api_key: str | None = None

    # SMTP
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None

    # Event Sources
    sources_csv_path: str | None = "./sources.csv"

    # Event Extraction
    event_lookahead_days: int = 60
    llm_fallback_enabled: bool = False

    # Apify Integration
    apify_api_token: str | None = None
    apify_timeout_seconds: int = 300
    apify_max_retries: int = 3
    discovery_sources_path: str | None = "./config/discovery_sources.yaml"

    # Cost Controls
    apify_max_runs_per_week: int = 100
    apify_max_events_per_source: int = 100

    # Storage
    database_url: str = "sqlite:///./events.db"

    # Logging
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        case_sensitive = False


def get_settings() -> Settings:
    """Load and return application settings."""
    return Settings()  # type: ignore
