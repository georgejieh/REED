from __future__ import annotations

from fastapi import Request

from app.config.models import Settings
from app.config.settings_store import SettingsStore
from app.config.source_catalog import SourceCatalog
from app.digests.repository import DigestRepository
from app.intake.policy import OutboundUrlPolicy
from app.runtime.pipeline import RuntimePipeline
from app.runtime.service import RuntimeService
from app.secrets.base import SecretStore


def get_repository(request: Request) -> DigestRepository:
    return request.app.state.runtime.repository


def get_runtime_settings(request: Request) -> Settings:
    return request.app.state.runtime.settings


def get_settings_store(request: Request) -> SettingsStore:
    return request.app.state.runtime.settings_store


def get_secret_store(request: Request) -> SecretStore:
    return request.app.state.runtime.secret_store


def get_source_catalog(request: Request) -> SourceCatalog:
    return request.app.state.runtime.source_catalog


def get_url_policy(request: Request) -> OutboundUrlPolicy:
    return request.app.state.runtime.url_policy


def get_pipeline(request: Request) -> RuntimePipeline:
    return request.app.state.runtime.pipeline


def get_runtime(request: Request) -> RuntimeService:
    return request.app.state.runtime
