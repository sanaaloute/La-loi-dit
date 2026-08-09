"""Document version tracking persisted as JSON under ``settings.data_dir``.

A single ``versions.json`` maps
``document_id -> {"hash": ..., "version": ..., "articles": {...}}`` where
``articles`` (optional, absent on legacy records) maps an article key to the
SHA256 of that article's text for fine-grained change detection (spec §26).
Writes are atomic-ish: serialized to a temp file then ``os.replace``-d, so a
crash mid-write cannot corrupt the store.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Union


@dataclass(frozen=True)
class ArticleDiff:
    """Article-level changes between the stored record and a new ingest."""

    added_articles: list[str] = field(default_factory=list)
    modified_articles: list[str] = field(default_factory=list)
    deleted_articles: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "added_articles": list(self.added_articles),
            "modified_articles": list(self.modified_articles),
            "deleted_articles": list(self.deleted_articles),
        }


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

    def check_version(self, document_id: str, content_hash: str) -> tuple[int, bool]:
        """Read-only ``(version, is_new_or_changed)`` — persists nothing.

        Use together with :meth:`commit_version` so a FAILED ingest does not
        mark the content hash as seen (which would later skip it as a
        duplicate).
        """
        state = self._load()
        entry = state.get(document_id)
        if entry is None:
            return 1, True
        if entry.get("hash") == content_hash:
            return int(entry.get("version", 1)), False
        return int(entry.get("version", 1)) + 1, True

    def commit_version(
        self,
        document_id: str,
        content_hash: str,
        version: int,
        article_hashes: Optional[Mapping[str, str]] = None,
    ) -> None:
        """Persist the content hash AFTER a successful ingest.

        ``article_hashes`` maps ``article_key -> sha256 of the article text``
        and enables :meth:`diff_articles`. When omitted, any previously stored
        article map is preserved untouched.
        """
        state = self._load()
        entry: dict = {"hash": content_hash, "version": version}
        if article_hashes is not None:
            entry["articles"] = dict(article_hashes)
        else:
            old = state.get(document_id)
            if old and old.get("articles"):
                entry["articles"] = dict(old["articles"])
        state[document_id] = entry
        self._save(state)

    def diff_articles(
        self, document_id: str, new_article_hashes: Mapping[str, str]
    ) -> ArticleDiff:
        """Compare stored article hashes against a new ingest (read-only).

        Legacy records without an article map are treated as empty, so the
        first diff after the upgrade reports every article as added.
        """
        state = self._load()
        old = (state.get(document_id) or {}).get("articles") or {}
        added = sorted(k for k in new_article_hashes if k not in old)
        modified = sorted(
            k for k in new_article_hashes if k in old and old[k] != new_article_hashes[k]
        )
        deleted = sorted(k for k in old if k not in new_article_hashes)
        return ArticleDiff(
            added_articles=added,
            modified_articles=modified,
            deleted_articles=deleted,
        )

    def list_document_ids(self) -> list[str]:
        """Return all known document ids."""
        return list(self._load().keys())

    def remove(self, document_id: str) -> bool:
        """Remove a document entry from the version store. Returns True if removed."""
        state = self._load()
        if document_id not in state:
            return False
        del state[document_id]
        self._save(state)
        return True
