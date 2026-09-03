"""Corpus browser (sources list/articles), freshness feed, bookmarks,
public share links, preferences and memories — endpoint tests.

Fully offline: TestClient with mock LLM, tmp data dir, in-memory stores.
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager

import pytest

from backend.core.config import get_settings  # noqa: E402

PASSWORD = "motdepasse1"


@contextmanager
def _make_client(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGAL_AI_ENV", "development")
    monkeypatch.setenv("LEGAL_AI_LLM_PROVIDER", "mock")
    monkeypatch.setenv("LEGAL_AI_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/library.db")
    monkeypatch.setenv("LEGAL_AI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LEGAL_AI_RATE_LIMIT_PER_MINUTE", "1000000")
    monkeypatch.setenv("LEGAL_AI_RATE_LIMIT_PER_SECOND", "1000000")
    get_settings.cache_clear()
    from fastapi.testclient import TestClient

    from backend.api.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


@pytest.fixture
def client(tmp_path, monkeypatch):
    with _make_client(tmp_path, monkeypatch) as test_client:
        yield test_client


def _register(client) -> str:
    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "name": "Awa"},
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_corpus_files(data_dir):
    """Minimal versions/journal/manifest trio for the sources list."""
    (data_dir / "versions.json").write_text(json.dumps({
        "doc-abc": {"hash": "deadbeef", "version": 2, "articles": {"1": "x"}},
    }))
    (data_dir / "ingestion_results.json").write_text(json.dumps({
        "doc-abc": {
            "document_id": "doc-abc",
            "document_name": "Code minier du Burkina Faso",
            "status": "indexed",
            "version": 2,
            "chunks_created": 908,
            "timestamp": "2026-08-30T08:51:03+00:00",
            "path": "/app/data/legal_docs/bf/code-minier.pdf",
        },
    }))
    (data_dir / "legal_sources.json").write_text(json.dumps({
        "document_metadata": {
            "code-minier.pdf": {
                "document_name": "Code minier du Burkina Faso",
                "authority": "law",
                "document_type": "code",
                "law_number": "016-2024/ALT",
                "publication_date": "2024-07-18",
                "legal_domains": ["land_law"],
            },
        },
    }))


# ---------------------------------------------------------------------------
# Corpus browser
# ---------------------------------------------------------------------------


def test_sources_list_reads_journal_and_manifest(tmp_path, monkeypatch):
    _seed_corpus_files(tmp_path)
    with _make_client(tmp_path, monkeypatch) as client:
        response = client.get("/api/v1/sources", headers=_headers(_register(client)))
        assert response.status_code == 200, response.text
        items = response.json()
        assert len(items) == 1
        item = items[0]
        assert item["document_id"] == "doc-abc"
        assert item["document_name"] == "Code minier du Burkina Faso"
        assert item["chunk_count"] == 908
        assert item["folder"] == "bf"
        assert item["publication_date"] == "2024-07-18"
        assert item["version"] == 2


def test_sources_articles_index(client):
    from backend.core.models import EvidenceChunk

    ctx = client.app.state.ctx
    chunks = [
        EvidenceChunk(
            chunk_id="p1",
            document_id="doc-x",
            document_name="Code X",
            content="Article 1 — Le préavis est d'un mois complet.",
            article="1",
            section="Titre I",
            page=3,
        ),
        EvidenceChunk(
            chunk_id="c1",
            document_id="doc-x",
            document_name="Code X",
            content="…alinéa 2…",
            article="1",
            parent_chunk_id="p1",
        ),
        EvidenceChunk(
            chunk_id="p2",
            document_id="doc-x",
            document_name="Code X",
            content="Article 2 — La résiliation est écrite.",
            article="2",
        ),
        EvidenceChunk(
            chunk_id="intro", document_id="doc-x", document_name="Code X", content="Préambule"
        ),
    ]
    vectors = [[0.0] * ctx.settings.embedding_dimension for _ in chunks]
    client.portal.call(ctx.vector_store.upsert, chunks, vectors)

    response = client.get("/api/v1/sources/doc-x/articles", headers=_headers(_register(client)))
    assert response.status_code == 200, response.text
    entries = response.json()
    assert [e["article"] for e in entries] == ["1", "2"]  # sorted, no preamble
    assert entries[0]["section"] == "Titre I"
    assert "préavis" in entries[0]["preview"]

    assert client.get("/api/v1/sources/doc-inconnu/articles", headers=_headers(_register(client))).status_code == 404


# ---------------------------------------------------------------------------
# Freshness feed
# ---------------------------------------------------------------------------


def test_freshness_events_newest_first(tmp_path, monkeypatch):
    from backend.ingestion.freshness import ChangeEvent, append_event

    with _make_client(tmp_path, monkeypatch) as client:
        append_event(tmp_path, ChangeEvent(source_name="JO", url="https://jo.example/1", kind="rss", detail="old"))
        append_event(tmp_path, ChangeEvent(source_name="OHADA", url="https://ohada.example/2", kind="rss", detail="new"))
        response = client.get("/api/v1/freshness/events", headers=_headers(_register(client)))
        assert response.status_code == 200, response.text
        events = response.json()
        assert [e["source_name"] for e in events] == ["OHADA", "JO"]
        assert events[0]["detail"] == "new"


# ---------------------------------------------------------------------------
# Bookmarks
# ---------------------------------------------------------------------------


def test_bookmarks_crud_owner_scoped(client):
    token = _register(client)
    created = client.post(
        "/api/v1/bookmarks",
        json={"query": "Préavis ?", "answer": "Un mois [1].", "confidence": 0.9, "session_id": "s1"},
        headers=_headers(token),
    )
    assert created.status_code == 201, created.text
    bookmark_id = created.json()["id"]

    listed = client.get("/api/v1/bookmarks", headers=_headers(token))
    assert [b["id"] for b in listed.json()] == [bookmark_id]

    # A second user cannot delete it.
    other = _register(client)
    assert client.delete(f"/api/v1/bookmarks/{bookmark_id}", headers=_headers(other)).status_code == 404

    assert client.delete(f"/api/v1/bookmarks/{bookmark_id}", headers=_headers(token)).status_code == 204
    assert client.get("/api/v1/bookmarks", headers=_headers(token)).json() == []


def test_bookmarks_require_auth(client):
    # dev anonymous -> require_user rejects
    assert client.get("/api/v1/bookmarks").status_code == 401


# ---------------------------------------------------------------------------
# Public share links
# ---------------------------------------------------------------------------


def test_share_public_flow(client):
    token = _register(client)
    created = client.post(
        "/api/v1/share",
        json={
            "query": "Préavis ?",
            "answer": "Un mois [1].",
            "citations": [{"label": "Code du travail, art. 39", "verified": True}],
            "confidence": 0.9,
        },
        headers=_headers(token),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["url_path"] == f"/partage/{body['token']}"

    # Public read: NO Authorization header at all.
    public = client.get(f"/api/v1/share/{body['token']}")
    assert public.status_code == 200, public.text
    assert public.json()["answer"] == "Un mois [1]."
    assert public.json()["citations"][0]["label"] == "Code du travail, art. 39"

    assert client.get("/api/v1/share/does-not-exist").status_code == 404


def test_share_dies_with_the_author_account(client):
    token = _register(client)
    token_share = client.post(
        "/api/v1/share",
        json={"query": "q", "answer": "a"},
        headers=_headers(token),
    ).json()["token"]
    assert client.delete("/api/v1/auth/me", headers=_headers(token)).status_code == 204
    assert client.get(f"/api/v1/share/{token_share}").status_code == 404


# ---------------------------------------------------------------------------
# Preferences & memories
# ---------------------------------------------------------------------------


def test_preferences_roundtrip(client):
    token = _register(client)
    put = client.put(
        "/api/v1/auth/me/preferences",
        json={"preferences": {"persona": "etudiant"}},
        headers=_headers(token),
    )
    assert put.status_code == 200, put.text
    got = client.get("/api/v1/auth/me/preferences", headers=_headers(token))
    assert got.json()["preferences"]["persona"] == "etudiant"


def test_memories_list_and_erase(client):
    from backend.core.models import MemoryRecord

    token = _register(client)
    me = client.get("/api/v1/auth/me", headers=_headers(token)).json()
    ctx = client.app.state.ctx
    client.portal.call(
        ctx.memory.remember,
        MemoryRecord(user_id=me["id"], kind="semantic", content="Awa prépare le barreau."),
    )

    listed = client.get("/api/v1/auth/me/memories", headers=_headers(token))
    assert listed.status_code == 200, listed.text
    memories = listed.json()["memories"]
    assert len(memories) == 1 and "barreau" in memories[0]["content"]

    assert client.delete("/api/v1/auth/me/memories", headers=_headers(token)).status_code == 204
    assert client.get("/api/v1/auth/me/memories", headers=_headers(token)).json()["memories"] == []


# ---------------------------------------------------------------------------
# Scenario date on the SSE stream (P1 "loi en vigueur au …")
# ---------------------------------------------------------------------------


def test_sse_stream_accepts_scenario_date(client):
    token = _register(client)
    ok = client.get(
        "/api/v1/chat/stream",
        params={"query": "C'est quoi un bail ?", "language": "fr", "scenario_date": "2020-01-01"},
        headers=_headers(token),
    )
    assert ok.status_code == 200, ok.text
    assert "text/event-stream" in ok.headers["content-type"]
    assert '"final"' in ok.text

    bad = client.get(
        "/api/v1/chat/stream",
        params={"query": "C'est quoi un bail ?", "scenario_date": "1er janvier"},
        headers=_headers(token),
    )
    assert bad.status_code == 422  # not an ISO date


# ---------------------------------------------------------------------------
# Push tokens & delivery
# ---------------------------------------------------------------------------

_TOKEN_A = "ExponentPushToken[aaaaaaaaaaaaaaaaaaaaaa]"
_TOKEN_B = "ExponentPushToken[bbbbbbbbbbbbbbbbbbbbbb]"


def test_push_token_register_and_delete(client):
    token = _register(client)
    ok = client.post(
        "/api/v1/push/token",
        json={"token": _TOKEN_A, "device_id": "phone-A"},
        headers=_headers(token),
    )
    assert ok.status_code == 200, ok.text

    # Idempotent re-register (same token, same user).
    assert (
        client.post(
            "/api/v1/push/token", json={"token": _TOKEN_A}, headers=_headers(token)
        ).status_code
        == 200
    )
    # Not an Expo token.
    assert (
        client.post(
            "/api/v1/push/token", json={"token": "random-string"}, headers=_headers(token)
        ).status_code
        == 422
    )
    # Foreign token cannot be deleted.
    other = _register(client)
    assert (
        client.request(
            "DELETE", "/api/v1/push/token", json={"token": _TOKEN_A}, headers=_headers(other)
        ).status_code
        == 404
    )
    assert (
        client.request(
            "DELETE", "/api/v1/push/token", json={"token": _TOKEN_A}, headers=_headers(token)
        ).status_code
        == 204
    )


async def test_send_push_delivers_and_prunes(client, monkeypatch):
    from backend.core import push as push_module

    token = _register(client)
    client.post("/api/v1/push/token", json={"token": _TOKEN_A}, headers=_headers(token))
    client.post("/api/v1/push/token", json={"token": _TOKEN_B}, headers=_headers(token))

    ctx = client.app.state.ctx
    ctx.settings.push_notifications_enabled = True

    async def fake_post(messages):
        return {
            "data": [
                {"status": "ok"},
                {"status": "error", "details": {"error": "DeviceNotRegistered"}},
            ]
        }

    monkeypatch.setattr(push_module, "_post", fake_post)
    delivered = await push_module.send_push(ctx, "Nouveauté", "Nouvelle loi publiée")
    assert delivered == 1
    # The dead token was pruned; the live one remains.
    assert await ctx.user_store.list_push_tokens() == [_TOKEN_A]


async def test_send_push_disabled_is_noop(client, monkeypatch):
    from backend.core import push as push_module

    token = _register(client)
    client.post("/api/v1/push/token", json={"token": _TOKEN_A}, headers=_headers(token))
    ctx = client.app.state.ctx  # push_notifications_enabled defaults to False

    async def boom(messages):
        raise AssertionError("must not be called when disabled")

    monkeypatch.setattr(push_module, "_post", boom)
    assert await push_module.send_push(ctx, "t", "b") == 0
