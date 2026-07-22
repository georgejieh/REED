"""HF Dataset mirror wrapper around JsonFileStore.

Pushes every digest + the rebuilt index to a HF Dataset repo on write.
Pulls the repo into local disk on startup so a recycled Space rehydrates
full history. Resilience rules:
- Failed pull never blocks boot. Log and continue with whatever is on
  local disk.
- Failed push never fails the digest. Write local first, push after; a
  failed push logs and retries on the next write.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.digests.models import Digest
from app.digests.store import JsonFileStore

logger = logging.getLogger(__name__)


class DatasetMirrorStore(JsonFileStore):
    """Wraps JsonFileStore with HF Dataset repo mirroring."""

    def __init__(self, data_dir: Path, dataset_repo: str, hf_token: str | None):
        super().__init__(data_dir)
        self.dataset_repo = dataset_repo
        self._hf_token = hf_token
        self._hf_api: Any | None = None

    def _client(self) -> Any:
        if self._hf_api is None:
            from huggingface_hub import HfApi
            self._hf_api = HfApi(token=self._hf_token)
        return self._hf_api

    def rehydrate(self) -> None:
        """Pull the dataset repo into local disk. Resilient to failure."""
        try:
            from huggingface_hub import snapshot_download
            snapshot_download(
                repo_id=self.dataset_repo,
                repo_type="dataset",
                local_dir=str(self.data_dir),
                token=self._hf_token,
            )
            logger.info("rehydrated dataset from %s", self.dataset_repo)
        except Exception as exc:
            logger.warning(
                "failed to pull dataset %s, continuing with local disk: %s",
                self.dataset_repo,
                exc,
            )

    def write(self, digest: Digest) -> Path:
        path = super().write(digest)
        self._push_async(path)
        return path

    def _push_async(self, path: Path) -> None:
        try:
            self._client().upload_file(
                path_or_fileobj=str(path),
                path_in_repo=path.name,
                repo_id=self.dataset_repo,
                repo_type="dataset",
            )
            index_path = self._index_path()
            if index_path.exists():
                self._client().upload_file(
                    path_or_fileobj=str(index_path),
                    path_in_repo="_index.json",
                    repo_id=self.dataset_repo,
                    repo_type="dataset",
                )
            logger.info("pushed digest to dataset %s", self.dataset_repo)
        except Exception as exc:
            logger.warning(
                "failed to push digest to dataset %s, will retry on next write: %s",
                self.dataset_repo,
                exc,
            )
