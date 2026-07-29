from __future__ import annotations

import os
from collections.abc import Mapping

from app.config.configuration import ProviderName


class EnvironmentSecretStore:
    def __init__(
        self,
        variable_names: Mapping[ProviderName, str],
        environment: Mapping[str, str] | None = None,
    ):
        self._variable_names = dict(variable_names)
        self._environment = environment if environment is not None else os.environ

    def get_credential(self, provider: ProviderName) -> str | None:
        variable_name = self._variable_names.get(provider)
        if variable_name is None:
            return None
        value = self._environment.get(variable_name)
        return value or None

    def has_credential(self, provider: ProviderName) -> bool:
        return self.get_credential(provider) is not None

    def set_credential(self, provider: ProviderName, credential: str) -> None:
        raise PermissionError(
            "hosted credentials must be changed through the deployment environment"
        )

    def delete_credential(self, provider: ProviderName) -> None:
        raise PermissionError(
            "hosted credentials must be changed through the deployment environment"
        )
