"""Version store and article-level change detection tests (offline, tmp_path only)."""

from __future__ import annotations

import hashlib
import json


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_check_version_first_ingest(tmp_path):
    from backend.ingestion.versioning import VersionStore

    store = VersionStore(tmp_path)
    assert store.check_version("doc-1", "hash-a") == (1, True)


def test_commit_then_check_unchanged_and_changed(tmp_path):
    from backend.ingestion.versioning import VersionStore

    store = VersionStore(tmp_path)
    store.commit_version("doc-1", "hash-a", 1)
    assert store.check_version("doc-1", "hash-a") == (1, False)
    assert store.check_version("doc-1", "hash-b") == (2, True)


def test_remove_existing_and_missing(tmp_path):
    from backend.ingestion.versioning import VersionStore

    store = VersionStore(tmp_path)
    store.commit_version("doc-1", "hash-a", 1)
    assert store.remove("doc-1") is True
    assert store.list_document_ids() == []
    # After removal the document is ingested as new again.
    assert store.check_version("doc-1", "hash-a") == (1, True)
    assert store.remove("doc-1") is False


def test_corrupt_store_loads_empty(tmp_path):
    from backend.ingestion.versioning import VersionStore

    (tmp_path / "versions.json").write_text("{not json", encoding="utf-8")
    store = VersionStore(tmp_path)
    assert store.list_document_ids() == []
    assert store.check_version("doc-1", "hash-a") == (1, True)


def test_diff_articles_added_modified_deleted(tmp_path):
    from backend.ingestion.versioning import VersionStore

    store = VersionStore(tmp_path)
    store.commit_version(
        "doc-1",
        "hash-a",
        1,
        article_hashes={"1": _hash("art1-v1"), "2": _hash("art2"), "3": _hash("art3")},
    )
    diff = store.diff_articles(
        "doc-1",
        {"1": _hash("art1-v2"), "2": _hash("art2"), "4": _hash("art4")},
    )
    assert diff.added_articles == ["4"]
    assert diff.modified_articles == ["1"]
    assert diff.deleted_articles == ["3"]
    assert diff.to_dict() == {
        "added_articles": ["4"],
        "modified_articles": ["1"],
        "deleted_articles": ["3"],
    }


def test_diff_articles_no_changes(tmp_path):
    from backend.ingestion.versioning import VersionStore

    store = VersionStore(tmp_path)
    hashes = {"1": _hash("art1"), "2": _hash("art2")}
    store.commit_version("doc-1", "hash-a", 1, article_hashes=hashes)
    diff = store.diff_articles("doc-1", hashes)
    assert diff.added_articles == []
    assert diff.modified_articles == []
    assert diff.deleted_articles == []


def test_diff_articles_legacy_record_without_article_map(tmp_path):
    """Records written before article maps existed load fine; first diff reports all as added."""
    from backend.ingestion.versioning import VersionStore

    (tmp_path / "versions.json").write_text(
        json.dumps({"doc-1": {"hash": "hash-a", "version": 3}}), encoding="utf-8"
    )
    store = VersionStore(tmp_path)
    # Legacy fields still drive the whole-document hash check.
    assert store.check_version("doc-1", "hash-a") == (3, False)
    assert store.check_version("doc-1", "hash-b") == (4, True)
    diff = store.diff_articles("doc-1", {"1": _hash("art1"), "2": _hash("art2")})
    assert diff.added_articles == ["1", "2"]
    assert diff.modified_articles == []
    assert diff.deleted_articles == []


def test_commit_version_preserves_article_map_when_not_supplied(tmp_path):
    from backend.ingestion.versioning import VersionStore

    store = VersionStore(tmp_path)
    hashes = {"1": _hash("art1")}
    store.commit_version("doc-1", "hash-a", 1, article_hashes=hashes)
    store.commit_version("doc-1", "hash-b", 2)
    diff = store.diff_articles("doc-1", hashes)
    assert diff.added_articles == []
    assert diff.modified_articles == []


def test_article_hashes_fallback_keys():
    from backend.core.models import EvidenceChunk
    from backend.ingestion.pipeline import _NO_ARTICLE_KEY, _article_hashes

    chunks = [
        EvidenceChunk(document_id="d", content="alpha", article="1"),
        EvidenceChunk(document_id="d", content="beta", article="1"),
        EvidenceChunk(document_id="d", content="gamma", section="Titre I"),
        EvidenceChunk(document_id="d", content="delta"),
    ]
    hashes = _article_hashes(chunks)
    assert set(hashes) == {"1", "section:Titre I", _NO_ARTICLE_KEY}
    assert hashes["1"] == _hash("alpha\n\nbeta")
    assert hashes["section:Titre I"] == _hash("gamma")
    assert hashes[_NO_ARTICLE_KEY] == _hash("delta")
