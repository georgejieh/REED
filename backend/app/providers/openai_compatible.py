from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from app.config.configuration import ProviderConfiguration, ProviderName
from app.intake.policy import OutboundUrlPolicy, UnsafeOutboundUrl
from app.secrets.base import SecretStore


OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1"
BUILT_IN_REMOTE_PROVIDER_HOSTS = {
    ProviderName.OPENROUTER: frozenset({"openrouter.ai"}),
}
MAX_PROVIDER_RESPONSE_BYTES = 2 * 1024 * 1024
PROVIDER_TIMEOUT_SECONDS = 30


def is_loopback_endpoint(endpoint: str | None) -> bool:
    if endpoint is None:
        return False
    try:
        parsed = urlsplit(endpoint)
        hostname = parsed.hostname
    except ValueError:
        return False
    if hostname is None:
        return False
    if hostname.rstrip(".").lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def is_loopback_http_endpoint(endpoint: str | None) -> bool:
    if endpoint is None:
        return False
    try:
        return (
            urlsplit(endpoint).scheme.lower() == "http"
            and is_loopback_endpoint(endpoint)
        )
    except ValueError:
        return False


def validate_provider_endpoint(
    configuration: ProviderConfiguration,
    *,
    allow_local_endpoint: bool,
    allowed_remote_hosts: tuple[str, ...] = (),
    policy: OutboundUrlPolicy | None = None,
) -> None:
    endpoint = (
        OPENROUTER_ENDPOINT
        if configuration.provider is ProviderName.OPENROUTER
        else configuration.endpoint
    )
    if endpoint is None:
        raise ValueError("provider endpoint is required")
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as error:
        raise UnsafeOutboundUrl("URL is not parseable") from error
    if (
        allow_local_endpoint
        and configuration.provider is not ProviderName.OLLAMA
    ):
        raise UnsafeOutboundUrl(
            "only the ollama provider may use a loopback endpoint"
        )
    if parsed.query:
        raise UnsafeOutboundUrl("provider endpoint query strings are not allowed")
    if not allow_local_endpoint and parsed.scheme != "https":
        raise UnsafeOutboundUrl("remote provider endpoint must use HTTPS")
    if (
        not allow_local_endpoint
        and port is not None
        and port != 443
    ):
        raise UnsafeOutboundUrl("remote provider endpoint port is not allowed")
    endpoint_policy = (
        OutboundUrlPolicy(allow_loopback=True)
        if allow_local_endpoint
        else policy or OutboundUrlPolicy()
    )
    endpoint_policy.parse(endpoint)
    if allow_local_endpoint and not is_loopback_http_endpoint(endpoint):
        raise UnsafeOutboundUrl(
            "local provider endpoint must use loopback HTTP"
        )
    if not allow_local_endpoint and is_loopback_endpoint(endpoint):
        raise UnsafeOutboundUrl(
            "remote provider endpoint must not use a loopback host"
        )
    if not allow_local_endpoint:
        hostname = (parsed.hostname or "").rstrip(".").lower()
        approved_hosts = BUILT_IN_REMOTE_PROVIDER_HOSTS.get(
            configuration.provider,
            frozenset(
                item.rstrip(".").lower()
                for item in allowed_remote_hosts
                if item.strip()
            ),
        )
        if hostname not in approved_hosts:
            raise UnsafeOutboundUrl(
                "remote provider host is not in the configured allowlist"
            )


class MissingProviderCredential(RuntimeError):
    pass


class ProviderExecutionFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenAiCompatibleProvider:
    configuration: ProviderConfiguration
    secret_store: SecretStore = field(repr=False)
    transport: object = field(repr=False)
    allow_local_endpoint: bool = False
    allowed_remote_hosts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validate_provider_endpoint(
            self.configuration,
            allow_local_endpoint=self.allow_local_endpoint,
            allowed_remote_hosts=self.allowed_remote_hosts,
        )

    @property
    def endpoint(self) -> str:
        if self.configuration.provider is ProviderName.OPENROUTER:
            return OPENROUTER_ENDPOINT
        endpoint = self.configuration.endpoint
        if endpoint is None:
            raise ValueError("provider endpoint is required")
        return endpoint.rstrip("/")

    def generate(self, prompt: str) -> str:
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "user-agent": "REED/0.1",
        }
        if self.configuration.provider.requires_credential:
            credential = self.secret_store.get_credential(
                self.configuration.provider
            )
            if not credential:
                raise MissingProviderCredential(
                    "selected provider credential is unavailable"
                )
            headers["authorization"] = f"Bearer {credential}"
        payload_data: dict[str, object] = {
            "model": self.configuration.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "stream": False,
        }
        if self.configuration.provider is ProviderName.OPENROUTER:
            payload_data["response_format"] = {"type": "json_object"}
        payload = json.dumps(payload_data, separators=(",", ":")).encode()
        response = self.transport.request(
            "POST",
            urljoin(self.endpoint + "/", "chat/completions"),
            headers=headers,
            body=payload,
            timeout=PROVIDER_TIMEOUT_SECONDS,
            max_bytes=MAX_PROVIDER_RESPONSE_BYTES,
            follow_redirects=False,
            allow_loopback=self.allow_local_endpoint,
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise ProviderExecutionFailure(
                "provider returned a non-success status"
            )
        try:
            decoded = json.loads(response.body)
            content = decoded["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
            raise ProviderExecutionFailure(
                "provider response schema is malformed"
            ) from error
        if not isinstance(content, str) or not content.strip():
            raise ProviderExecutionFailure("provider response content is empty")
        return content.strip()
