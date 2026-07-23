"""Shared dependencies for the API layer.

`get_store` returns the configured DigestStore for the request's
lifecycle. `get_config` reads AppConfig (cached per process; refresh
when settings.yaml changes).
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.config import AppConfig, EnvSettings, load_config
from app.digests.dataset_mirror import DatasetMirrorStore
from app.digests.store import DigestStore, JsonFileStore

logger = logging.getLogger(__name__)

CONFIG_CACHE: dict[str, AppConfig] = {}
STORE_CACHE: dict[str, DigestStore] = {}


def get_config() -> AppConfig:
    """Return the current AppConfig, caching between calls.

    The cache survives until process restart; for tests, callers
    should call `load_config()` directly.
    """
    key = "_default"
    if key not in CONFIG_CACHE:
        CONFIG_CACHE[key] = load_config()
    return CONFIG_CACHE[key]


def reset_config_cache() -> None:
    """Clear the config and store caches (used by tests and after settings.yaml rewrite)."""
    CONFIG_CACHE.clear()
    STORE_CACHE.clear()


def _env_settings() -> EnvSettings:
    """Return EnvSettings read from OS env without a dotenv file."""
    return EnvSettings(_env_file=None)


def get_store() -> DigestStore:
    """Return the DigestStore configured in the environment.

    REED_STORE=mirror returns a DatasetMirrorStore, otherwise a
    JsonFileStore rooted at config.data_dir.
    """
    key = "_default"
    if key in STORE_CACHE:
        return STORE_CACHE[key]

    cfg = get_config()
    env = _env_settings()
    store = _build_store(env, cfg.data_dir)
    STORE_CACHE[key] = store
    return store


def _build_store(env: EnvSettings, data_dir: Path) -> DigestStore:
    if env.reed_store == "mirror":
        if not env.hf_dataset_repo or not env.hf_token:
            raise RuntimeError("mirror storage requires HF_DATASET_REPO and HF_TOKEN")
        return DatasetMirrorStore(
            data_dir=data_dir,
            dataset_repo=env.hf_dataset_repo,
            hf_token=env.hf_token,
        )
    return JsonFileStore(data_dir=data_dir)
