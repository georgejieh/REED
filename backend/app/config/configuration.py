from __future__ import annotations

from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProviderName(StrEnum):
    OPENROUTER = "openrouter"
    OLLAMA = "ollama"
    OPENAI_COMPATIBLE = "openai_compatible"

    @property
    def requires_credential(self) -> bool:
        return self is not ProviderName.OLLAMA


class MarketWindow(StrEnum):
    PRE_MARKET = "pre_market"
    EARLY_MARKET = "early_market"
    MIDDAY = "midday"
    CLOSE = "close"
    WEEKEND_RECAP = "weekend_recap"


class ProviderConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: ProviderName
    model: str
    endpoint: str | None = None

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        model = value.strip()
        if not model:
            raise ValueError("model must not be empty")
        return model

    @model_validator(mode="after")
    def validate_endpoint_requirement(self) -> ProviderConfiguration:
        needs_endpoint = self.provider in {
            ProviderName.OLLAMA,
            ProviderName.OPENAI_COMPATIBLE,
        }
        if needs_endpoint and self.endpoint is None:
            raise ValueError("selected provider requires an explicit endpoint")
        if self.provider is ProviderName.OPENROUTER and self.endpoint is not None:
            raise ValueError("selected provider does not accept a custom endpoint")
        return self


class SearchConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    endpoint: str | None = None
    query_templates: tuple[str, ...] = ()
    max_queries_per_run: int = Field(default=3, ge=1, le=10)
    max_results_per_query: int = Field(default=10, ge=1, le=25)
    max_articles_to_parse: int = Field(default=5, ge=0, le=10)
    max_article_bytes: int = Field(
        default=2 * 1024 * 1024,
        ge=1,
        le=5 * 1024 * 1024,
    )
    request_timeout_seconds: float = Field(default=8, gt=0, le=30)
    total_search_budget_seconds: float = Field(default=30, gt=0, le=60)

    @model_validator(mode="after")
    def validate_enabled_configuration(self) -> SearchConfiguration:
        if self.enabled and (self.endpoint is None or not self.query_templates):
            raise ValueError(
                "enabled supplemental search requires an endpoint and query templates"
            )
        if not self.enabled and self.endpoint is not None:
            raise ValueError("disabled supplemental search cannot have an endpoint")
        return self


class SchedulerConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    timezone: str = "America/New_York"
    claim_ttl_seconds: int = Field(default=900, ge=30, le=3600)
    lease_ttl_seconds: int = Field(default=60, ge=15, le=300)
    lease_renewal_seconds: int = Field(default=20, ge=5, le=120)
    misfire_grace_seconds: int = Field(default=900, ge=1, le=86400)
    coalesce: bool = True

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("scheduler timezone must be an IANA timezone") from error
        return value

    @model_validator(mode="after")
    def validate_lease_renewal(self) -> SchedulerConfiguration:
        if self.lease_renewal_seconds >= self.lease_ttl_seconds:
            raise ValueError("scheduler lease renewal must precede lease expiry")
        return self


class RuntimeConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: ProviderConfiguration | None = None
    market_windows: tuple[MarketWindow, ...] = ()
    rss_source_ids: tuple[str, ...] = ()
    rss_minimum_items: int = Field(default=1, ge=1, le=25)
    max_future_skew_seconds: int = Field(default=60, ge=0, le=300)
    search: SearchConfiguration = Field(default_factory=SearchConfiguration)
    scheduler: SchedulerConfiguration = Field(default_factory=SchedulerConfiguration)
    setup_complete: bool = False

    @field_validator("market_windows", "rss_source_ids")
    @classmethod
    def reject_duplicates(cls, value: tuple[object, ...]) -> tuple[object, ...]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate selections are not allowed")
        return value
