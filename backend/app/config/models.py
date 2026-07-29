from __future__ import annotations

from enum import StrEnum
import ipaddress
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_DASHBOARD_PATH = (
    Path(__file__).resolve().parents[3] / "dashboard" / "dist"
)


class RuntimeMode(StrEnum):
    LOCAL = "local"
    HOSTED = "hosted"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="REED_",
        case_sensitive=False,
        extra="ignore",
    )

    runtime_mode: RuntimeMode = RuntimeMode.LOCAL
    database_path: Path = Path("./data/reed.db")
    dashboard_path: Path = DEFAULT_DASHBOARD_PATH
    local_profile_id: str = "default"
    scheduler_enabled: bool = True
    scheduler_enabled_replicas: int = Field(default=1, ge=1, le=1)
    bind_host: str = "127.0.0.1"
    bind_port: int = Field(default=8000, ge=1, le=65535)
    allowed_hosts: str = "127.0.0.1:8000,localhost:8000,[::1]:8000"
    local_allowed_origins: str = (
        "http://127.0.0.1:8000,http://localhost:8000,http://[::1]:8000"
    )
    hosted_allowed_origins: str = ""
    hosted_backend_origin: str = ""
    hosted_operator_secret: SecretStr = SecretStr("")
    provider_allowed_hosts: str = ""
    bootstrap_ttl_seconds: int = Field(default=60, ge=5, le=300)
    session_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    auth_rate_limit_attempts: int = Field(default=5, ge=1, le=20)
    auth_rate_limit_window_seconds: int = Field(default=300, ge=10, le=3600)
    validate_rss_catalog_on_startup: bool = False

    @model_validator(mode="after")
    def validate_delivery_security(self) -> Settings:
        hosts = [
            item.strip()
            for item in self.allowed_hosts.split(",")
            if item.strip()
        ]
        if not hosts or any(
            host == "*"
            or "://" in host
            or "/" in host.replace("[::1]", "")
            for host in hosts
        ):
            raise ValueError("Host allowlist requires exact host values")
        local_origins = [
            item.strip()
            for item in self.local_allowed_origins.split(",")
            if item.strip()
        ]
        hosted_origins = [
            item.strip()
            for item in self.hosted_allowed_origins.split(",")
            if item.strip()
        ]
        origin_values = [*local_origins, *hosted_origins]
        for origin in origin_values:
            parsed = urlsplit(origin)
            if (
                origin == "*"
                or parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or origin != f"{parsed.scheme}://{parsed.netloc}"
            ):
                raise ValueError(
                    "CORS and mutation allowlists require exact origins"
                )
        provider_hosts = [
            item.strip()
            for item in self.provider_allowed_hosts.split(",")
            if item.strip()
        ]
        for host in provider_hosts:
            parsed = urlsplit(f"//{host}")
            if (
                host == "*"
                or parsed.hostname is None
                or parsed.username is not None
                or parsed.password is not None
                or parsed.port is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "provider host allowlist requires exact hostnames"
                )
        if self.runtime_mode is RuntimeMode.LOCAL:
            host = self.bind_host.strip().lower()
            if host != "localhost":
                try:
                    is_loopback = ipaddress.ip_address(host).is_loopback
                except ValueError:
                    is_loopback = False
                if not is_loopback:
                    raise ValueError(
                        "local runtime bind host must be loopback"
                    )
        if self.runtime_mode is RuntimeMode.HOSTED:
            if not hosted_origins:
                raise ValueError(
                    "hosted CORS origin allowlist must not be empty"
                )
            backend = urlsplit(self.hosted_backend_origin)
            if (
                backend.scheme != "https"
                or not backend.netloc
                or self.hosted_backend_origin
                != f"{backend.scheme}://{backend.netloc}"
            ):
                raise ValueError(
                    "hosted backend origin must be exact HTTPS origin"
                )
            if not self.hosted_operator_secret.get_secret_value():
                raise ValueError("hosted operator secret is required")
        return self
