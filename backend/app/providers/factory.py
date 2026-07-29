from __future__ import annotations

from app.config.configuration import ProviderConfiguration, ProviderName
from app.config.models import RuntimeMode
from app.providers.base import Provider
from app.providers.openai_compatible import (
    OpenAiCompatibleProvider,
    is_loopback_http_endpoint,
)
from app.secrets.base import SecretStore


def build_provider(
    configuration: ProviderConfiguration,
    *,
    secret_store: SecretStore,
    transport: object,
    runtime_mode: RuntimeMode,
    allowed_remote_hosts: tuple[str, ...] = (),
) -> Provider:
    endpoint = configuration.endpoint
    local_endpoint = (
        configuration.provider is ProviderName.OLLAMA
        and is_loopback_http_endpoint(endpoint)
    )
    if local_endpoint and runtime_mode is not RuntimeMode.LOCAL:
        raise ValueError("loopback provider endpoints require local runtime mode")
    if configuration.provider is ProviderName.OLLAMA and not local_endpoint:
        raise ValueError("local provider must use an explicit loopback endpoint")
    return OpenAiCompatibleProvider(
        configuration=configuration,
        secret_store=secret_store,
        transport=transport,
        allow_local_endpoint=local_endpoint,
        allowed_remote_hosts=allowed_remote_hosts,
    )
