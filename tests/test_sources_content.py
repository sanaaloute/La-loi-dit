"""Full-text endpoint for article-less documents (GET /sources/{id}/content).

The explorer falls back to this view for parliamentary records and reports:
parents only (children are sub-splits), deduplicated, in reading order
(``metadata["seq"]``; legacy chunks fall back to page order).
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


def _chunk(content, *, doc="doc1", seq=None, page=None, parent=False, article=None):
    return EvidenceChunk(
        document_id=doc,
        document_name="Rapport test",
        content=content,
        article=article,
        page=page,
        parent_chunk_id=None if parent else "parent-id",
        metadata={"role": "parent" if parent else "child", **({"seq": seq} if seq is not None else {})},
    )


@pytest.fixture
def sources_client(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGAL_AI_ENV", "development")
    monkeypatch.setenv("LEGAL_AI_REDIS_ENABLED", "false")
    monkeypatch.setenv("LEGAL_AI_MILVUS_ENABLED", "false")
    monkeypatch.setenv("LEGAL_AI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LEGAL_AI_SECRET_KEY", "test-secret-key-for-jwt-signing")
    monkeypatch.setenv("LEGAL_AI_LLM_PROVIDER", "mock")
    monkeypatch.setenv("LEGAL_AI_DEV_USERS", "viewer:test:viewer")
    monkeypatch.setenv("LEGAL_AI_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/legal_ai.db")
    get_settings.cache_clear()

    chunks = [
        # Unordered store rows; seq defines the reading order.
        _chunk("Deuxième segment du rapport.", seq=1, page=2, parent=True),
        _chunk("Premier segment du rapport.", seq=0, page=1, parent=True),
        _chunk("Premier segment du rapport.", seq=0, page=1, parent=True),  # duplicate row
        _chunk("extrait enfant du premier segment", seq=2, page=1, parent=False),
        # A structured document (with an article) for the `structured` flag.
        _chunk("Article 1 : texte.", doc="doc2", seq=0, page=1, parent=True, article="1"),
    ]
    app = create_app()
    with TestClient(app) as client:
        app.state.ctx.vector_store = _FakeVectorStore(chunks)
        res = client.post("/api/v1/auth/token", json={"username": "viewer", "password": "test"})
        assert res.status_code == 200, res.text
        client.headers.update({"Authorization": f"Bearer {res.json()['access_token']}"})
        yield client


def test_content_returns_parents_deduped_in_seq_order(sources_client):
    res = sources_client.get("/api/v1/sources/doc1/content")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["document_id"] == "doc1"
    assert body["structured"] is False
    texts = [b["text"] for b in body["blocks"]]
    assert texts == ["Premier segment du rapport.", "Deuxième segment du rapport."]
    assert [b["page"] for b in body["blocks"]] == [1, 2]


def test_content_marks_structured_documents(sources_client):
    res = sources_client.get("/api/v1/sources/doc2/content")
    assert res.status_code == 200, res.text
    assert res.json()["structured"] is True


def test_content_unknown_document_is_404(sources_client):
    res = sources_client.get("/api/v1/sources/nope/content")
    assert res.status_code == 404
