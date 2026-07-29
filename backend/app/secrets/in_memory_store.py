from __future__ import annotations

from app.config.configuration import ProviderName


class InMemorySecretStore:
    def __init__(self) -> None:
        self._credentials: dict[ProviderName, str] = {}

    def get_credential(self, provider: ProviderName) -> str | None:
        return self._credentials.get(provider)

    def has_credential(self, provider: ProviderName) -> bool:
        return provider in self._credentials

    def set_credential(self, provider: ProviderName, credential: str) -> None:
        if not credential:
            raise ValueError("credential must not be empty")
        self._credentials[provider] = credential

    def delete_credential(self, provider: ProviderName) -> None:
        self._credentials.pop(provider, None)
