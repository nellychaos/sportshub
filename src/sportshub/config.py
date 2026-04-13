"""Application settings via pydantic-settings (environment variables)."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration is read from environment variables prefixed with SPORTSHUB_."""

    model_config = SettingsConfigDict(
        env_prefix="SPORTSHUB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = (
        "postgresql+asyncpg://sportshub:sportshub_dev@localhost:5432/sportshub"
    )
    db_pool_size: int = 10
    db_max_overflow: int = 5

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # API — comma-separated list of valid keys
    api_keys: str = "sh_dev_changeme_in_production"
    environment: Literal["development", "staging", "production"] = "development"

    # Source credentials
    balldontlie_api_key: str | None = None
    pandascore_token: str | None = None
    lolesports_api_key: str = "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"
    footballdata_api_key: str | None = None

    # LLM Resolution
    anthropic_api_key: str | None = None
    llm_resolution_enabled: bool = False
    llm_model: str = "claude-haiku-4-5-20251001"
    llm_acceptance_threshold: float = 0.40
    llm_cooldown_minutes: int = 30

    # External Confirmation (Odds API)
    odds_api_key: str | None = None
    odds_api_url: str = "https://api.the-odds-api.com/v4"

    # Mollybet (ingestion + confirmation)
    mollybet_username: str | None = None   # SPORTSHUB_MOLLYBET_USERNAME
    mollybet_password: str | None = None   # SPORTSHUB_MOLLYBET_PASSWORD
    mollybet_api_url: str = "https://api.mollybet.com"

    # Cloudbet (odds ingestion)
    cloudbet_api_key: str | None = None    # SPORTSHUB_CLOUDBET_API_KEY
    cloudbet_api_url: str = "https://sports-api.cloudbet.com/pub"

    # Logging
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Singleton settings instance for dependency injection."""
    return Settings()
