from __future__ import annotations

from typing import Protocol

import keyring
from keyring.errors import KeyringError

from app.config.configuration import ProviderName


class KeyringBackend(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, value: str) -> None: ...

    def delete_password(self, service: str, username: str) -> None: ...


class SecretStoreUnavailable(RuntimeError):
    pass


class KeyringSecretStore:
    def __init__(
        self,
        profile_id: str,
        backend: KeyringBackend | None = None,
    ):
        profile = profile_id.strip()
        if not profile or "/" in profile or "\\" in profile:
            raise ValueError("profile identifier is invalid")
        self._service = f"reed/{profile}"
        self._backend = backend or keyring

    @staticmethod
    def _username(provider: ProviderName) -> str:
        return f"provider/{provider.value}"

    def get_credential(self, provider: ProviderName) -> str | None:
        try:
            return self._backend.get_password(
                self._service,
                self._username(provider),
            )
        except KeyringError as error:
            raise SecretStoreUnavailable(
                "secure operating-system credential storage is unavailable"
            ) from error

    def has_credential(self, provider: ProviderName) -> bool:
        return self.get_credential(provider) is not None

    def set_credential(self, provider: ProviderName, credential: str) -> None:
        if not credential:
            raise ValueError("credential must not be empty")
        try:
            self._backend.set_password(
                self._service,
                self._username(provider),
                credential,
            )
        except KeyringError as error:
            raise SecretStoreUnavailable(
                "secure operating-system credential storage is unavailable"
            ) from error

    def delete_credential(self, provider: ProviderName) -> None:
        try:
            self._backend.delete_password(
                self._service,
                self._username(provider),
            )
        except KeyringError as error:
            raise SecretStoreUnavailable(
                "secure operating-system credential storage is unavailable"
            ) from error
