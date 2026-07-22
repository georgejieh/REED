"""REED application configuration loaded from env vars and settings.yaml."""

from __future__ import annotations

import logging
from enum import StrEnum
from pathlib import Path

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
    provider: SearchProviderName = SearchProviderName.DDGS
    rate_limit_per_minute: int = 12


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
    reed_trigger_token: str | None = None
    reed_settings_path: Path = Path("./settings.yaml")
    reed_data_dir: Path = Path("../data/digests")
    reed_store: str = "local"
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

    Precedence: env vars override settings.yaml where both apply.
    """
    env = EnvSettings()
    settings = load_settings_yaml(env.reed_settings_path)

    if settings.provider is None or settings.model is None:
        raise RuntimeError(
            "settings.yaml is missing provider or model. "
            "Run `python cli_setup.py` to configure."
        )

    api_keys = _collect_api_keys(env)

    return AppConfig(
        provider=settings.provider,
        model=settings.model,
        base_url=settings.base_url,
        sessions=settings.sessions,
        search=SearchConfig(
            provider=SearchProviderName(env.reed_search_provider),
            rate_limit_per_minute=settings.search.rate_limit_per_minute,
        ),
        market_data=settings.market_data,
        data_dir=env.reed_data_dir,
        scheduler=SchedulerConfig(
            enabled=env.reed_scheduler_enabled,
            timezone=settings.scheduler.timezone,
            skip_holidays=env.reed_skip_holidays,
        ),
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
    }
    for name, value in mapping.items():
        if value:
            keys[name] = value
    return keys
