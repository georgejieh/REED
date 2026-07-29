from __future__ import annotations

import json

import pytest

from app.config.configuration import ProviderConfiguration, ProviderName
from app.intake.policy import OutboundResponse, UnsafeOutboundUrl
from app.providers.openai_compatible import (
    MissingProviderCredential,
    OpenAiCompatibleProvider,
)
from app.secrets.in_memory_store import InMemorySecretStore


class ProviderTransport:
    def __init__(self, response: OutboundResponse) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def request(self, method: str, url: str, **kwargs: object) -> OutboundResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.response


def provider_response(content: object) -> OutboundResponse:
    body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
    return OutboundResponse(
        status_code=200,
        headers={"content-type": "application/json"},
        body=body,
        final_url="https://provider.example.com/v1/chat/completions",
    )


def test_provider_reads_credential_only_at_execution_and_does_not_return_it() -> None:
    secret = "private-provider-value"
    secrets = InMemorySecretStore()
    secrets.set_credential(ProviderName.OPENAI_COMPATIBLE, secret)
    transport = ProviderTransport(provider_response('{"title":"Digest"}'))
    provider = OpenAiCompatibleProvider(
        configuration=ProviderConfiguration(
            provider=ProviderName.OPENAI_COMPATIBLE,
            model="model-a",
            endpoint="https://provider.example.com/v1",
        ),
        secret_store=secrets,
        transport=transport,
        allowed_remote_hosts=("provider.example.com",),
    )

    result = provider.generate("bounded request")

    assert result == '{"title":"Digest"}'
    assert transport.calls[0]["url"] == (
        "https://provider.example.com/v1/chat/completions"
    )
    headers = transport.calls[0]["headers"]
    assert isinstance(headers, dict)
    assert headers["authorization"] == f"Bearer {secret}"
    assert secret not in result
    assert secret not in repr(provider)


def test_openrouter_requests_json_object_response() -> None:
    secrets = InMemorySecretStore()
    secrets.set_credential(ProviderName.OPENROUTER, "private-provider-value")
    transport = ProviderTransport(provider_response('{"title":"Digest"}'))
    provider = OpenAiCompatibleProvider(
        configuration=ProviderConfiguration(
            provider=ProviderName.OPENROUTER,
            model="google/gemini-2.5-flash-lite",
        ),
        secret_store=secrets,
        transport=transport,
    )

    provider.generate("bounded request")

    body = transport.calls[0]["body"]
    assert isinstance(body, bytes)
    assert json.loads(body)["response_format"] == {"type": "json_object"}


def test_missing_required_provider_credential_fails_before_transport() -> None:
    transport = ProviderTransport(provider_response("{}"))
    provider = OpenAiCompatibleProvider(
        configuration=ProviderConfiguration(
            provider=ProviderName.OPENAI_COMPATIBLE,
            model="model-a",
            endpoint="https://provider.example.com/v1",
        ),
        secret_store=InMemorySecretStore(),
        transport=transport,
        allowed_remote_hosts=("provider.example.com",),
    )

    with pytest.raises(MissingProviderCredential):
        provider.generate("bounded request")

    assert transport.calls == []


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://169.254.169.254/v1",
        "http://provider.example.com/v1",
        "https://user:pass@provider.example.com/v1",
        "https://provider.example.com/v1#fragment",
        "https://provider.example.com:8443/v1",
    ],
)
def test_unsafe_custom_provider_endpoint_is_rejected(endpoint: str) -> None:
    with pytest.raises(UnsafeOutboundUrl):
        OpenAiCompatibleProvider(
            configuration=ProviderConfiguration(
                provider=ProviderName.OPENAI_COMPATIBLE,
                model="model-a",
                endpoint=endpoint,
            ),
            secret_store=InMemorySecretStore(),
            transport=ProviderTransport(provider_response("{}")),
            allowed_remote_hosts=(
                "provider.example.com",
                "169.254.169.254",
            ),
        )


def test_arbitrary_https_compatible_provider_host_is_rejected() -> None:
    with pytest.raises(UnsafeOutboundUrl, match="allowlist"):
        OpenAiCompatibleProvider(
            configuration=ProviderConfiguration(
                provider=ProviderName.OPENAI_COMPATIBLE,
                model="model-a",
                endpoint="https://arbitrary.example/v1",
            ),
            secret_store=InMemorySecretStore(),
            transport=ProviderTransport(provider_response("{}")),
        )


def test_explicitly_allowed_compatible_provider_host_is_accepted() -> None:
    provider = OpenAiCompatibleProvider(
        configuration=ProviderConfiguration(
            provider=ProviderName.OPENAI_COMPATIBLE,
            model="model-a",
            endpoint="https://provider.example.com/v1",
        ),
        secret_store=InMemorySecretStore(),
        transport=ProviderTransport(provider_response("{}")),
        allowed_remote_hosts=("provider.example.com",),
    )

    assert provider.endpoint == "https://provider.example.com/v1"


def test_explicit_local_ollama_endpoint_uses_same_provider_port_without_key() -> None:
    transport = ProviderTransport(provider_response('{"title":"Local"}'))
    provider = OpenAiCompatibleProvider(
        configuration=ProviderConfiguration(
            provider=ProviderName.OLLAMA,
            model="local-model",
            endpoint="http://127.0.0.1:11434/v1",
        ),
        secret_store=InMemorySecretStore(),
        transport=transport,
        allow_local_endpoint=True,
    )

    assert provider.generate("bounded request") == '{"title":"Local"}'
    headers = transport.calls[0]["headers"]
    assert isinstance(headers, dict)
    assert "authorization" not in headers
