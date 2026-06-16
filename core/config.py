"""Application configuration, loaded once from env/.env and injected downstream.

Built on pydantic-settings so types are validated at load time. Pure modules never read
this directly — the composition root (`runner/`) constructs it and passes the pieces it
needs (e.g. a ``RiskParams``) into pure functions.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from risk.params import RiskParams

StrategyMode = Literal["rules-only", "llm"]
DataProviderName = Literal["tiingo"]


class DataConfig(BaseModel):
    """Historical data provider + local cache settings.

    Env vars: ``PAPERHANDS_DATA__PROVIDER``, ``PAPERHANDS_DATA__CACHE_DIR``,
    ``PAPERHANDS_DATA__TIINGO_BASE_URL``. Only Tiingo is implemented this slice; the
    ``provider`` field is the swappable seam for Polygon later.
    """

    model_config = {"frozen": True}

    provider: DataProviderName = "tiingo"
    cache_dir: str = "data_cache"
    tiingo_base_url: str = "https://api.tiingo.com"


class Settings(BaseSettings):
    """Top-level config. Env vars are prefixed ``PAPERHANDS_``; nested groups use ``__``.

    Example: ``PAPERHANDS_RISK__MAX_POSITION_PCT=0.2`` sets ``settings.risk.max_position_pct``.
    Secret keys (Tiingo, Anthropic) are read from their conventional env names without the
    prefix and are optional in this slice.
    """

    model_config = SettingsConfigDict(
        env_prefix="PAPERHANDS_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    log_level: str = "INFO"
    strategy_mode: StrategyMode = "rules-only"

    risk: RiskParams = Field(default_factory=RiskParams)
    data: DataConfig = Field(default_factory=DataConfig)

    # Secrets — optional here; required by later slices. Read from their standard env names.
    tiingo_api_key: str | None = Field(default=None, alias="TIINGO_API_KEY")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")


def load_settings() -> Settings:
    """Construct Settings from the environment / .env file."""
    return Settings()
