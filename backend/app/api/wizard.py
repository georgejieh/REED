from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.auth import require_mutation_auth, require_session
from app.api.deps import (
    get_runtime_settings,
    get_secret_store,
    get_settings_store,
    get_source_catalog,
    get_url_policy,
)
from app.config.configuration import (
    MarketWindow,
    ProviderConfiguration,
    ProviderName,
    RuntimeConfiguration,
)
from app.config.models import RuntimeMode, Settings
from app.config.settings_store import SettingsStore
from app.config.source_catalog import (
    InvalidSourceSelection,
    SourceCatalog,
)
from app.intake.policy import OutboundUrlPolicy, UnsafeOutboundUrl
from app.providers.openai_compatible import validate_provider_endpoint
from app.secrets.base import SecretStore
from app.secrets.keyring_store import SecretStoreUnavailable


router = APIRouter(prefix="/api/wizard", tags=["wizard"])
MutationAuth = Annotated[None, Depends(require_mutation_auth)]
SessionAuth = Annotated[None, Depends(require_session)]


class WizardState(BaseModel):
    provider: ProviderName | None
    model: str | None
    endpoint: str | None
    credential_present: bool
    market_windows: list[MarketWindow]
    rss_source_ids: list[str]
    catalog_version: str
    complete: bool


class CatalogSource(BaseModel):
    id: str
    name: str
    url: str


class CatalogResponse(BaseModel):
    version: str
    sources: list[CatalogSource]


class CredentialSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential: str = Field(max_length=16384)

    @field_validator("credential")
    @classmethod
    def validate_credential(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("credential must not be empty")
        return value


class MarketWindowSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market_windows: tuple[MarketWindow, ...]

    @field_validator("market_windows")
    @classmethod
    def reject_duplicates(
        cls,
        value: tuple[MarketWindow, ...],
    ) -> tuple[MarketWindow, ...]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate market windows are not allowed")
        return value


class RssSourceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_ids: tuple[str, ...]


def _credential_present(
    secrets: SecretStore,
    provider: ProviderName | None,
) -> bool:
    if provider is None:
        return False
    try:
        return secrets.has_credential(provider)
    except SecretStoreUnavailable as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error


def _is_ready(
    configuration: RuntimeConfiguration,
    credential_present: bool,
) -> bool:
    provider = configuration.provider
    if provider is None:
        return False
    return bool(
        configuration.market_windows
        and configuration.rss_source_ids
        and (credential_present or not provider.provider.requires_credential)
    )


def _state(
    configuration: RuntimeConfiguration,
    secrets: SecretStore,
    catalog: SourceCatalog,
) -> WizardState:
    provider = configuration.provider
    credential_present = _credential_present(
        secrets,
        provider.provider if provider is not None else None,
    )
    return WizardState(
        provider=provider.provider if provider is not None else None,
        model=provider.model if provider is not None else None,
        endpoint=provider.endpoint if provider is not None else None,
        credential_present=credential_present,
        market_windows=list(configuration.market_windows),
        rss_source_ids=list(configuration.rss_source_ids),
        catalog_version=catalog.version,
        complete=configuration.setup_complete
        and _is_ready(configuration, credential_present),
    )


def _save_incomplete(
    store: SettingsStore,
    configuration: RuntimeConfiguration,
) -> RuntimeConfiguration:
    changed = configuration.model_copy(update={"setup_complete": False})
    store.save(changed)
    return changed


@router.get("/state", response_model=WizardState)
def get_state(
    _session: SessionAuth,
    store: SettingsStore = Depends(get_settings_store),
    secrets: SecretStore = Depends(get_secret_store),
    catalog: SourceCatalog = Depends(get_source_catalog),
) -> WizardState:
    return _state(store.load(), secrets, catalog)


@router.get("/rss-catalog", response_model=CatalogResponse)
def get_rss_catalog(
    _session: SessionAuth,
    catalog: SourceCatalog = Depends(get_source_catalog),
) -> CatalogResponse:
    return CatalogResponse(
        version=catalog.version,
        sources=[
            CatalogSource(id=source.id, name=source.name, url=source.url)
            for source in catalog.sources
        ],
    )


@router.put("/provider", response_model=WizardState)
def configure_provider(
    request: ProviderConfiguration,
    _auth: MutationAuth,
    store: SettingsStore = Depends(get_settings_store),
    secrets: SecretStore = Depends(get_secret_store),
    catalog: SourceCatalog = Depends(get_source_catalog),
    policy: OutboundUrlPolicy = Depends(get_url_policy),
    settings: Settings = Depends(get_runtime_settings),
) -> WizardState:
    try:
        validate_provider_endpoint(
            request,
            allow_local_endpoint=request.provider is ProviderName.OLLAMA,
            allowed_remote_hosts=tuple(
                item.strip()
                for item in settings.provider_allowed_hosts.split(",")
                if item.strip()
            ),
            policy=policy,
        )
    except UnsafeOutboundUrl as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    configuration = _save_incomplete(
        store,
        store.load().model_copy(update={"provider": request}),
    )
    return _state(configuration, secrets, catalog)


@router.put("/credential", status_code=status.HTTP_204_NO_CONTENT)
def submit_credential(
    submission: CredentialSubmission,
    _auth: MutationAuth,
    settings: Settings = Depends(get_runtime_settings),
    store: SettingsStore = Depends(get_settings_store),
    secrets: SecretStore = Depends(get_secret_store),
) -> Response:
    if settings.runtime_mode is RuntimeMode.HOSTED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="hosted credentials are managed by the deployment environment",
        )
    configuration = store.load()
    if configuration.provider is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="configure a provider before submitting a credential",
        )
    try:
        secrets.set_credential(
            configuration.provider.provider,
            submission.credential,
        )
    except SecretStoreUnavailable as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    _save_incomplete(store, configuration)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/market-windows", response_model=WizardState)
def configure_market_windows(
    selection: MarketWindowSelection,
    _auth: MutationAuth,
    store: SettingsStore = Depends(get_settings_store),
    secrets: SecretStore = Depends(get_secret_store),
    catalog: SourceCatalog = Depends(get_source_catalog),
) -> WizardState:
    configuration = _save_incomplete(
        store,
        store.load().model_copy(
            update={"market_windows": selection.market_windows}
        ),
    )
    return _state(configuration, secrets, catalog)


@router.put("/rss-sources", response_model=WizardState)
def configure_rss_sources(
    selection: RssSourceSelection,
    _auth: MutationAuth,
    store: SettingsStore = Depends(get_settings_store),
    secrets: SecretStore = Depends(get_secret_store),
    catalog: SourceCatalog = Depends(get_source_catalog),
) -> WizardState:
    try:
        source_ids = catalog.validate_selection(selection.source_ids)
    except InvalidSourceSelection as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    configuration = _save_incomplete(
        store,
        store.load().model_copy(update={"rss_source_ids": source_ids}),
    )
    return _state(configuration, secrets, catalog)


@router.post("/complete", response_model=WizardState)
def complete_setup(
    _auth: MutationAuth,
    request: Request,
    response: Response,
    store: SettingsStore = Depends(get_settings_store),
    secrets: SecretStore = Depends(get_secret_store),
    catalog: SourceCatalog = Depends(get_source_catalog),
) -> WizardState:
    configuration = store.load()
    provider = configuration.provider
    credential_present = _credential_present(
        secrets,
        provider.provider if provider is not None else None,
    )
    if not _is_ready(configuration, credential_present):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "provider, model, credential when required, market window, "
                "and at least one RSS source must be configured"
            ),
        )
    completed = configuration.model_copy(update={"setup_complete": True})
    store.save(completed)
    request.app.state.security.invalidate_bootstrap()
    response.headers["Cache-Control"] = "no-store"
    return _state(completed, secrets, catalog)
