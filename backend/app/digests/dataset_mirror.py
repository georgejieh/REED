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

import json
import logging
import os
import shutil
import tempfile
from collections.abc import Iterable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from app.digests.models import Digest
from app.digests.store import JsonFileStore

logger = logging.getLogger(__name__)


class DatasetMirrorStore(JsonFileStore):
    """Wraps JsonFileStore with HF Dataset repo mirroring."""

    def __init__(self, data_dir: Path, dataset_repo: str, hf_token: str | None):
        if not dataset_repo or not hf_token:
            raise RuntimeError("mirror storage requires HF_DATASET_REPO and HF_TOKEN")
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
        """Pull the dataset repo into a temp directory, then merge into local disk."""
        try:
            with TemporaryDirectory() as temp_dir:
                self._download_repo(Path(temp_dir))
                candidates = self._collect_candidates(Path(temp_dir))
                selected = self._select_candidates(candidates)
                self._stage_and_replace(selected)
                self._rebuild_index()
                logger.info("rehydrated dataset from %s", self.dataset_repo)
        except Exception as exc:
            logger.warning(
                "failed to pull dataset %s, continuing with local disk: %s",
                self.dataset_repo,
                exc,
            )

    def _download_repo(self, temp_dir: Path) -> None:
        from huggingface_hub import snapshot_download
        snapshot_download(
            repo_id=self.dataset_repo,
            repo_type="dataset",
            local_dir=str(temp_dir),
            token=self._hf_token,
        )

    def _collect_candidates(self, temp_dir: Path) -> dict[Path, Digest]:
        candidates: dict[Path, Digest] = {}
        for path in temp_dir.glob("*.json"):
            if path.name == "_index.json":
                continue
            if not path.is_file():
                continue
            try:
                digest = Digest.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise RuntimeError(f"invalid digest in mirror: {path.name}") from exc
            candidates[path] = digest
        return candidates

    def _select_candidates(self, candidates: dict[Path, Digest]) -> dict[Path, Digest]:
        selected: dict[Path, Digest] = {}
        for remote_path, remote_digest in candidates.items():
            local_path = self.data_dir / remote_path.name
            if not local_path.exists():
                selected[remote_path] = remote_digest
                continue
            try:
                local_digest = Digest.model_validate_json(
                    local_path.read_text(encoding="utf-8")
                )
            except Exception:
                selected[remote_path] = remote_digest
                continue
            if remote_digest.as_of > local_digest.as_of:
                selected[remote_path] = remote_digest
        return selected

    def _stage_and_replace(self, selected: dict[Path, Digest]) -> None:
        backups: dict[Path, Path] = {}
        staged: dict[Path, Path] = {}
        replaced: list[Path] = []
        try:
            for remote_path, digest in selected.items():
                local_path = self.data_dir / remote_path.name
                text = json.dumps(
                    digest.model_dump(mode="json"),
                    indent=2,
                    ensure_ascii=False,
                )
                fd, tmp_local = tempfile.mkstemp(
                    dir=str(self.data_dir),
                    suffix=".tmp",
                )
                try:
                    with open(fd, "w", encoding="utf-8") as f:
                        f.write(text)
                except Exception:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                    raise
                staged[local_path] = Path(tmp_local)
                if local_path.exists():
                    backup_fd, backup_path = tempfile.mkstemp(
                        dir=str(self.data_dir),
                        suffix=".bak",
                    )
                    os.close(backup_fd)
                    backup = Path(backup_path)
                    shutil.copy2(str(local_path), str(backup))
                    backups[local_path] = backup

            for local_path, tmp_path in staged.items():
                tmp_path.replace(local_path)
                replaced.append(local_path)

        except Exception:
            self._restore_backups(backups, replaced)
            self._cleanup_paths(staged.values())
            raise

        finally:
            self._cleanup_paths(backups.values())
            self._cleanup_paths(staged.values())

    @staticmethod
    def _restore_backups(backups: dict[Path, Path], replaced: list[Path]) -> None:
        for local_path in replaced:
            backup = backups.get(local_path)
            if backup is not None and backup.exists():
                try:
                    backup.replace(local_path)
                except OSError:
                    pass

    @staticmethod
    def _cleanup_paths(paths: Iterable[Path]) -> None:
        for path in paths:
            if path.exists():
                try:
                    path.unlink()
                except OSError:
                    pass

    def write(self, digest: Digest) -> Path:
        path = super().write(digest)
        self._push_mirror()
        return path

    def _push_mirror(self) -> None:
        try:
            operations = self._build_commit_operations()
            if not operations:
                return
            self._client().create_commit(
                repo_id=self.dataset_repo,
                repo_type="dataset",
                operations=operations,
                commit_message="Update digest archive",
            )
            logger.info("pushed digest archive to dataset %s", self.dataset_repo)
        except Exception as exc:
            logger.warning(
                "failed to push digest to dataset %s, will retry on next write: %s",
                self.dataset_repo,
                exc,
            )

    def _build_commit_operations(self) -> list[Any]:
        from huggingface_hub import CommitOperationAdd
        operations: list[Any] = []
        for path in sorted(self.data_dir.glob("*.json")):
            if (
                path.name.startswith(".")
                or path.name.endswith(".tmp")
                or path.name.endswith(".bak")
            ):
                continue
            operations.append(
                CommitOperationAdd(
                    path_in_repo=path.name,
                    path_or_fileobj=str(path),
                )
            )
        return operations
