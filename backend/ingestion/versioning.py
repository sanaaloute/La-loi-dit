"""Document version tracking persisted as JSON under ``settings.data_dir``.

A single ``versions.json`` maps ``document_id -> {"hash": ..., "version": ...}``.
Writes are atomic-ish: serialized to a temp file then ``os.replace``-d, so a
crash mid-write cannot corrupt the store.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Union


class VersionStore:
    """Tracks per-document content hashes and monotonically increasing versions."""

    def __init__(self, data_dir: Union[str, Path]):
        self._path = Path(data_dir) / "versions.json"

    def _load(self) -> dict:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, state: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self._path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def get_version(self, document_id: str, content_hash: str) -> tuple[int, bool]:
        """Return ``(version, is_new_or_changed)`` for a document.

        - first ingest of a document -> ``(1, True)``
        - re-ingest of identical content -> ``(current_version, False)`` (skip)
        - changed content -> ``(version + 1, True)`` and the store is updated
        """
        state = self._load()
        entry = state.get(document_id)
        if entry is None:
            state[document_id] = {"hash": content_hash, "version": 1}
            self._save(state)
            return 1, True
        if entry.get("hash") == content_hash:
            return int(entry.get("version", 1)), False
        version = int(entry.get("version", 1)) + 1
        state[document_id] = {"hash": content_hash, "version": version}
        self._save(state)
        return version, True


def get_version(
    document_id: str,
    content_hash: str,
    data_dir: Union[str, Path] = "./data",
) -> tuple[int, bool]:
    """Convenience wrapper around :class:`VersionStore`."""
    return VersionStore(data_dir).get_version(document_id, content_hash)
