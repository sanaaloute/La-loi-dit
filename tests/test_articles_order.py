"""Article reader ordering (GET /articles/{doc}/{article}).

Milvus returns a document's chunks in arbitrary order. Amending laws quote an
article's old wording then its new one under the same number, so the reader
must sort by ingestion order (``metadata["seq"]``; page as legacy fallback).
Child chunks are whitespace-normalized retrieval excerpts of their parent, so
the reader hides them whenever the parent is part of the result.
"""

import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.core.config import get_settings
from backend.core.models import EvidenceChunk


class _FakeVectorStore:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def get_by_document_id(self, document_id):
        return [c for c in self._chunks if c.document_id == document_id]


def _chunk(content, *, doc="doc1", seq=None, page=None, article="33", role="parent", parent=None):
    return EvidenceChunk(
        document_id=doc,
        document_name="Loi test",
        content=content,
        article=article,
        page=page,
        parent_chunk_id=parent,
        metadata={"role": role, **({"seq": seq} if seq is not None else {})},
    )


@pytest.fixture
def articles_client(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGAL_AI_ENV", "development")
    monkeypatch.setenv("LEGAL_AI_REDIS_ENABLED", "false")
    monkeypatch.setenv("LEGAL_AI_MILVUS_ENABLED", "false")
    monkeypatch.setenv("LEGAL_AI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LEGAL_AI_SECRET_KEY", "test-secret-key-for-jwt-signing")
    monkeypatch.setenv("LEGAL_AI_LLM_PROVIDER", "mock")
    monkeypatch.setenv("LEGAL_AI_DEV_USERS", "viewer:test:viewer")
    monkeypatch.setenv("LEGAL_AI_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/legal_ai.db")
    get_settings.cache_clear()

    excerpt = _chunk("Article 33 :\n\nextrait enfant.", seq=24, page=2)
    chunks = [
        # Unordered store rows: the new wording (seq 25) arrives first.
        _chunk("Article 33 :\n\nNouveau texte.", seq=25, page=3),
        _chunk("Article 33 :\n\nAncien texte.", seq=23, page=2),
        excerpt,
        # Retrieval excerpt of the seq-24 parent: must not reach the reader.
        _chunk("extrait enfant.", seq=26, page=2, role="child", parent=excerpt.chunk_id),
        # Orphan child (parent not in the match set): still shown.
        _chunk("Article 34 :\n\nextrait seul.", seq=27, page=3, article="34", role="child", parent="absent"),
    ]
    app = create_app()
    with TestClient(app) as client:
        app.state.ctx.vector_store = _FakeVectorStore(chunks)
        res = client.post("/api/v1/auth/token", json={"username": "viewer", "password": "test"})
        assert res.status_code == 200, res.text
        client.headers.update({"Authorization": f"Bearer {res.json()['access_token']}"})
        yield client


def test_article_chunks_returned_in_seq_order(articles_client):
    res = articles_client.get("/api/v1/articles/doc1/33")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["count"] == 3
    assert [c["content"] for c in body["chunks"]] == [
        "Article 33 :\n\nAncien texte.",
        "Article 33 :\n\nextrait enfant.",
        "Article 33 :\n\nNouveau texte.",
    ]


def test_article_unknown_is_404(articles_client):
    res = articles_client.get("/api/v1/articles/doc1/99")
    assert res.status_code == 404


def test_child_chunk_hidden_when_parent_present(articles_client):
    res = articles_client.get("/api/v1/articles/doc1/33")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["count"] == 3
    assert all((c["metadata"] or {}).get("role") != "child" for c in body["chunks"])


def test_orphan_child_chunk_still_shown(articles_client):
    res = articles_client.get("/api/v1/articles/doc1/34")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["count"] == 1
    assert body["chunks"][0]["content"] == "Article 34 :\n\nextrait seul."
