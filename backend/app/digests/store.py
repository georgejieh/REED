"""JSON-file storage for digests.

The DigestStore protocol lets a future HF Dataset mirror wrap this
without changing call sites. The default implementation writes one
JSON file per digest under data_dir and rebuilds a small _index.json
on every write.
"""

from __future__ import annotations

import json
import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Protocol

from app.digests.models import Digest

logger = logging.getLogger(__name__)


class DigestStore(Protocol):
    def write(self, digest: Digest) -> Path: ...
    def get(self, digest_id: str) -> Digest | None: ...
    def list(self, limit: int | None = None) -> list[Digest]: ...
    def latest(self, session: str | None = None) -> Digest | None: ...


class JsonFileStore:
    """Writes digests as one JSON file per digest plus a rebuilt index.

    The index file (data_dir/_index.json) is rebuilt on every write so
    a corrupted index recovers on the next run. Writes use atomic
    rename (tempfile + os.replace) so a crashed write cannot leave a
    half-written digest file.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, digest: Digest) -> Path:
        assert digest.id is not None, "digest.id must be set before write"
        return self.data_dir / f"{digest.id}.json"

    def _index_path(self) -> Path:
        return self.data_dir / "_index.json"

    def _make_id(self, session: str, as_of: datetime) -> str:
        return f"{as_of.strftime('%Y-%m-%d')}-{session}"

    def write(self, digest: Digest) -> Path:
        if digest.id is None:
            digest.id = self._make_id(digest.session, digest.as_of)
        path = self._path_for(digest)
        self._atomic_write_json(path, digest.model_dump(mode="json"))
        self._rebuild_index()
        logger.info("wrote digest %s to %s", digest.id, path)
        return path

    def get(self, digest_id: str) -> Digest | None:
        path = self.data_dir / f"{digest_id}.json"
        if not path.exists():
            return None
        return Digest.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self, limit: int | None = None) -> list[Digest]:
        index_path = self._index_path()
        if not index_path.exists():
            self._rebuild_index()
        index = json.loads(index_path.read_text(encoding="utf-8"))
        ids = [entry["id"] for entry in index]
        if limit is not None:
            ids = ids[:limit]
        digests: list[Digest] = []
        for digest_id in ids:
            d = self.get(digest_id)
            if d is not None:
                digests.append(d)
        return digests

    def latest(self, session: str | None = None) -> Digest | None:
        digests = self.list(limit=50)
        if session is not None:
            digests = [d for d in digests if d.session == session]
        if not digests:
            return None
        return max(digests, key=lambda d: d.as_of)

    def _rebuild_index(self) -> None:
        entries: list[dict[str, str]] = []
        for path in sorted(self.data_dir.glob("*.json")):
            if path.name == "_index.json":
                continue
            try:
                digest = Digest.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("skipping unreadable digest %s: %s", path, exc)
                continue
            entries.append({"id": digest.id or path.stem, "as_of": digest.as_of.isoformat()})
        entries.sort(key=lambda e: e["as_of"], reverse=True)
        self._atomic_write_json(self._index_path(), entries)

    @staticmethod
    def _atomic_write_json(path: Path, payload) -> None:
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            delete=False,
            suffix=".tmp",
        ) as f:
            tmp_path = Path(f.name)
            f.write(text)
        tmp_path.replace(path)
