"""REED application configuration loaded from env vars and settings.yaml."""

from __future__ import annotations

import logging
from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class ProviderName(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    OPENROUTER = "openrouter"
    OLLAMA = "ollama"
    OPENAI_COMPATIBLE = "openai_compatible"


class SearchProviderName(StrEnum):
    DDGS = "ddgs"
    BRAVE = "brave"
    TAVILY = "tavily"
    FIRECRAWL = "firecrawl"


class MarketDataProviderName(StrEnum):
    STOOQ = "stooq"


class SessionsConfig(BaseModel):
    enabled: list[str] = Field(
        default_factory=lambda: [
            "pre_market",
            "early_market",
            "midday",
            "close",
            "weekend_recap",
        ]
    )


class SearchConfig(BaseModel):
    """News search configuration.
    Provider is the primary search backend. fallback_providers
    is an ordered list of additional backends to try when the
    primary returns a rate-limit or quota error (e.g., Firecrawl
    monthly credits exhausted; falls through to Brave). Empty
    list means no fall back.
    per_session_max_calls caps search calls within one agent
    run. The default of 5 keeps a REED session at 5-25 Firecrawl
    credits.
    """
    provider: SearchProviderName = SearchProviderName.DDGS
    fallback_providers: list[str] = Field(default_factory=list)
    rate_limit_per_minute: int = 12
    per_session_max_calls: int = 5  # legacy cap; superseded below
    per_session_max_queries: int = 3  # search_news calls per session
    per_session_max_scrapes: int = 2  # scrape_url calls per session


class MarketDataConfig(BaseModel):
    provider: MarketDataProviderName = MarketDataProviderName.STOOQ


class SchedulerConfig(BaseModel):
    enabled: bool = True
    timezone: str = "US/Eastern"
    skip_holidays: bool = True


class TriggerConfig(BaseModel):
    enabled: bool = False


class SettingsYaml(BaseModel):
    """Operator settings written by the setup wizard."""

    provider: ProviderName | None = None
    model: str | None = None
    base_url: str | None = None
    sessions: SessionsConfig = Field(default_factory=SessionsConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    market_data: MarketDataConfig = Field(default_factory=MarketDataConfig)
    data_dir: Path = Path("./data/digests")
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    trigger: TriggerConfig = Field(default_factory=TriggerConfig)


class EnvSettings(BaseSettings):
    """Env vars read directly via pydantic-settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    openrouter_api_key: str | None = None
    ollama_api_key: str | None = None
    ollama_host: str = "http://localhost:11434"
    brave_api_key: str | None = None
    tavily_api_key: str | None = None
    firecrawl_api_key: str | None = None
    reed_trigger_token: str | None = None
    reed_settings_path: Path = Path("./settings.yaml")
    reed_data_dir: Path = Path("../data/digests")
    reed_store: Literal["local", "mirror"] = "local"
    hf_dataset_repo: str | None = None
    hf_token: str | None = None
    reed_search_provider: str = "ddgs"
    reed_scheduler_enabled: bool = True
    reed_skip_holidays: bool = True


class AppConfig(BaseModel):
    """Merged runtime configuration consumed by every module."""

    provider: ProviderName
    model: str
    base_url: str | None = None
    sessions: SessionsConfig
    search: SearchConfig
    market_data: MarketDataConfig
    data_dir: Path
    scheduler: SchedulerConfig
    trigger: TriggerConfig
    api_keys: dict[str, str] = Field(default_factory=dict)


def load_settings_yaml(path: Path) -> SettingsYaml:
    if not path.exists():
        logger.info("settings.yaml not found at %s, using defaults", path)
        return SettingsYaml()
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return SettingsYaml.model_validate(data)


def load_config() -> AppConfig:
    """Build AppConfig from env vars and settings.yaml.

    settings.yaml is the source of truth for everything except the
    provider's API key and OLLAMA_HOST. The wizard writes those YAML
    values; env vars override only where the YAML has no value to set
    (api keys come from .env, OLLAMA_HOST can come from .env when the
    wizard was skipped).
    """
    env = EnvSettings()
    settings = load_settings_yaml(env.reed_settings_path)

    if settings.provider is None or settings.model is None:
        raise RuntimeError(
            "settings.yaml is missing provider or model. "
            "Run `python cli_setup.py` to configure."
        )

    api_keys = _collect_api_keys(env)

    base_url = settings.base_url
    if settings.provider == ProviderName.OLLAMA and base_url is None:
        base_url = env.ollama_host

    return AppConfig(
        provider=settings.provider,
        model=settings.model,
        base_url=base_url,
        sessions=settings.sessions,
        search=settings.search,
        market_data=settings.market_data,
        data_dir=settings.data_dir,
        scheduler=settings.scheduler,
        trigger=settings.trigger,
        api_keys=api_keys,
    )


def _collect_api_keys(env: EnvSettings) -> dict[str, str]:
    keys: dict[str, str] = {}
    mapping = {
        "openai": env.openai_api_key,
        "anthropic": env.anthropic_api_key,
        "openrouter": env.openrouter_api_key,
        "ollama": env.ollama_api_key,
        "brave": env.brave_api_key,
        "tavily": env.tavily_api_key,
        "firecrawl": env.firecrawl_api_key,
    }
    for name, value in mapping.items():
        if value:
            keys[name] = value
    return keys
