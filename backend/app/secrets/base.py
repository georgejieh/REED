from __future__ import annotations

from typing import Protocol

from app.config.configuration import ProviderName


class SecretStore(Protocol):
    def get_credential(self, provider: ProviderName) -> str | None: ...

    def has_credential(self, provider: ProviderName) -> bool: ...

    def set_credential(self, provider: ProviderName, credential: str) -> None: ...

    def delete_credential(self, provider: ProviderName) -> None: ...
