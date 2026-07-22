"""Shared dependencies for the API layer.

`get_store` returns the configured DigestStore for the request's
lifecycle. `get_config` reads AppConfig (cached per process; refresh
when settings.yaml changes).
"""

from __future__ import annotations

import logging
import os

from app.config import AppConfig, load_config
from app.digests.dataset_mirror import DatasetMirrorStore
from app.digests.store import DigestStore, JsonFileStore

logger = logging.getLogger(__name__)

CONFIG_CACHE: dict[str, AppConfig] = {}


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
    """Clear the config cache (used by tests and after settings.yaml rewrite)."""
    CONFIG_CACHE.clear()


def get_store() -> DigestStore:
    """Return the DigestStore configured in the environment.

    REED_STORE=mirror returns a DatasetMirrorStore, otherwise a
    JsonFileStore rooted at config.data_dir.
    """
    cfg = get_config()
    data_dir = cfg.data_dir
    if os.environ.get("REED_STORE") == "mirror":
        return DatasetMirrorStore(
            data_dir=data_dir,
            dataset_repo=os.environ.get("HF_DATASET_REPO", ""),
            hf_token=os.environ.get("HF_TOKEN", ""),
        )
    return JsonFileStore(data_dir=data_dir)
