"""Tests for the citation/source lookup endpoints (spec §48), the admin
router (spec §49) and the admin-only trace gating on chat responses.

Fully offline: mock LLM, in-memory adapters, tmp data dir — same fixture
pattern as tests/test_legal_api.py.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import contextmanager

import pytest

os.environ["LEGAL_AI_LLM_PROVIDER"] = "mock"
os.environ["LEGAL_AI_LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LEGAL_AI_LANGFUSE_SECRET_KEY"] = ""

from backend.core.config import get_settings  # noqa: E402
from backend.core.models import Role  # noqa: E402
from backend.security.jwt import create_access_token  # noqa: E402

ADMIN_PATHS = [
    "/api/v1/admin/audit-log",
    "/api/v1/admin/ingestion/status",
    "/api/v1/admin/evaluation/latest",
    "/api/v1/admin/retrieval/analytics",
]


@contextmanager
def _make_client(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGAL_AI_ENV", "development")
    monkeypatch.setenv("LEGAL_AI_LLM_PROVIDER", "mock")
    monkeypatch.setenv("LEGAL_AI_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/admin_api.db")
    monkeypatch.setenv("LEGAL_AI_DATA_DIR", str(tmp_path))
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


def _headers(role: Role) -> dict[str, str]:
    token = create_access_token(f"test-{role.value}", role, get_settings())
    return {"Authorization": f"Bearer {token}"}


def _seed_chunk(client):
    from backend.core.models import AuthorityLevel, DocumentType, EvidenceChunk

    ctx = client.app.state.ctx
    chunk = EvidenceChunk(
        document_id="doc-test",
        document_name="Code du travail",
        content="Le préavis de licenciement est de un mois.",
        article="12",
        section="Section 2",
        url="https://example.com/code-travail",
        authority=AuthorityLevel.LAW,
        document_type=DocumentType.CODE,
        law_number="028-2008/AN",
        status="active",
        hierarchy={"livre": "I", "titre": "II"},
        metadata={"legal_domains": ["travail"]},
    )

    async def seed():
        vectors = await ctx.embedder.embed([chunk.content])
        await ctx.vector_store.upsert([chunk], vectors)

    asyncio.run(seed())
    return chunk


# ---------------------------------------------------------------------------
# GET /api/v1/citations/{chunk_id}
# ---------------------------------------------------------------------------


def test_citation_lookup_returns_evidence_record(client):
    chunk = _seed_chunk(client)
    response = client.get(f"/api/v1/citations/{chunk.chunk_id}")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["chunk_id"] == chunk.chunk_id
    assert data["document_id"] == "doc-test"
    assert data["document_name"] == "Code du travail"
    assert "préavis" in data["content"]
    assert data["article"] == "12"
    assert data["section"] == "Section 2"
    assert data["url"] == "https://example.com/code-travail"
    assert data["authority"] == "law"
    assert data["law_number"] == "028-2008/AN"
    assert data["status"] == "active"
    assert data["hierarchy"] == {"livre": "I", "titre": "II"}
    assert data["metadata"]["legal_domains"] == ["travail"]


def test_citation_lookup_404_on_unknown_chunk(client):
    assert client.get("/api/v1/citations/chunk-inconnu").status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/sources/{document_id}
# ---------------------------------------------------------------------------


def test_source_lookup_combines_version_store_and_chunks(client):
    chunk = _seed_chunk(client)
    from backend.ingestion.versioning import VersionStore

    data_dir = client.app.state.ctx.settings.data_dir
    VersionStore(data_dir).commit_version("doc-test", "deadbeef", 2, {"1": "h1", "12": "h12"})

    response = client.get(f"/api/v1/sources/{chunk.document_id}")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["document_id"] == "doc-test"
    assert data["document_name"] == "Code du travail"
    assert data["version"] == 2
    assert data["content_hash"] == "deadbeef"
    assert data["article_count"] == 2
    assert data["chunk_count"] == 1
    assert data["authority"] == "law"
    assert data["document_type"] == "code"
    assert data["law_number"] == "028-2008/AN"
    assert data["status"] == "active"
    assert data["url"] == "https://example.com/code-travail"


def test_source_lookup_404_on_unknown_document(client):
    assert client.get("/api/v1/sources/doc-inconnu").status_code == 404


# ---------------------------------------------------------------------------
# Admin router: role gating (ADMIN only, spec §49)
# ---------------------------------------------------------------------------


def test_admin_endpoints_reject_non_admin_roles(client):
    for path in ADMIN_PATHS:
        # Dev-mode anonymous callers map to Role.USER: below ADMIN.
        assert client.get(path).status_code == 403, path
        for role in (Role.USER, Role.VIEWER, Role.LEGAL_EXPERT):
            response = client.get(path, headers=_headers(role))
            assert response.status_code == 403, (path, role)


def test_admin_audit_log_returns_recent_entries(client):
    client.get("/health")  # one request guaranteed to be in the log
    response = client.get("/api/v1/admin/audit-log", headers=_headers(Role.ADMIN))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["source"] == "in_memory_ring_buffer"
    assert data["cap"] > 0
    assert data["count"] >= 1
    paths = [e["path"] for e in data["entries"]]
    assert "/health" in paths
    entry = data["entries"][0]
    assert {"ts", "method", "path", "status", "latency_ms", "user"} <= set(entry)


def test_admin_audit_log_honours_limit(client):
    for _ in range(3):
        client.get("/health")
    response = client.get("/api/v1/admin/audit-log?limit=2", headers=_headers(Role.ADMIN))
    assert response.status_code == 200
    assert response.json()["count"] == 2


def test_admin_ingestion_status_lists_versioned_documents(client):
    from backend.ingestion.versioning import VersionStore

    data_dir = client.app.state.ctx.settings.data_dir
    VersionStore(data_dir).commit_version("doc-a", "hash-a", 3, {"1": "h"})

    response = client.get("/api/v1/admin/ingestion/status", headers=_headers(Role.ADMIN))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total_documents"] == 1
    doc = data["documents"][0]
    assert doc["document_id"] == "doc-a"
    assert doc["version"] == 3
    assert doc["content_hash"] == "hash-a"
    assert doc["article_count"] == 1
    assert data["store_updated_at"]
    # No ingest has failed: the failure list stays empty and the note
    # discloses the persistence limits (latest record per document only).
    assert data["failed_documents"] == []
    assert "ingestion_results.json" in data["note"]


def test_admin_ingestion_status_surfaces_failed_documents(client, tmp_path):
    from backend.ingestion.pipeline import IngestionPipeline

    ctx = client.app.state.ctx
    pipeline = IngestionPipeline(ctx)
    missing = tmp_path / "unreadable.txt"  # never created: the loader fails
    result = asyncio.run(pipeline.ingest_path(missing))
    assert result.status == "failed"

    response = client.get("/api/v1/admin/ingestion/status", headers=_headers(Role.ADMIN))
    assert response.status_code == 200, response.text
    failed = response.json()["failed_documents"]
    match = [r for r in failed if r["document_id"] == result.document_id]
    assert match, failed
    record = match[0]
    assert record["status"] == "failed"
    assert record["error"]
    assert record["path"] == str(missing)
    assert record["timestamp"]


def test_admin_ingestion_status_omits_recovered_documents(client, tmp_path):
    """A document whose latest ingest succeeded must not stay listed as failed."""
    from backend.ingestion.pipeline import IngestionPipeline

    ctx = client.app.state.ctx
    pipeline = IngestionPipeline(ctx)
    missing = tmp_path / "recovered.txt"
    failed_result = asyncio.run(pipeline.ingest_path(missing))
    assert failed_result.status == "failed"

    # Same logical document id (derived from the name), now ingested fine.
    ok_result = asyncio.run(
        pipeline.ingest_text(
            str(missing.name),
            "Article 1: Tout travailleur a droit à un salaire équitable.",
        )
    )
    assert ok_result.status == "indexed"
    assert ok_result.document_id == failed_result.document_id

    response = client.get("/api/v1/admin/ingestion/status", headers=_headers(Role.ADMIN))
    assert response.status_code == 200, response.text
    failed_ids = [r["document_id"] for r in response.json()["failed_documents"]]
    assert ok_result.document_id not in failed_ids


def test_admin_evaluation_latest_404_without_report(client):
    response = client.get("/api/v1/admin/evaluation/latest", headers=_headers(Role.ADMIN))
    assert response.status_code == 404


def test_admin_evaluation_latest_returns_written_report(client):
    data_dir = client.app.state.ctx.settings.data_dir
    report_dir = data_dir / "eval"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "eval_report.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-08-09T00:00:00+00:00",
                "dataset": "golden_dataset.json",
                "total_cases": 25,
                "pass_rate": 0.84,
                "results": [],
            }
        ),
        encoding="utf-8",
    )

    response = client.get("/api/v1/admin/evaluation/latest", headers=_headers(Role.ADMIN))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total_cases"] == 25
    assert data["pass_rate"] == 0.84
    assert data["report"]["dataset"] == "golden_dataset.json"


def test_admin_retrieval_analytics_aggregates_audit_log(client):
    client.get("/health")
    # The current request is only appended to the audit log after its
    # response completes, so the admin identity must come from a prior call.
    client.get("/api/v1/admin/audit-log", headers=_headers(Role.ADMIN))
    response = client.get("/api/v1/admin/retrieval/analytics", headers=_headers(Role.ADMIN))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["source"] == "in_memory_audit_log"
    assert data["total_requests"] >= 1
    health = next(s for s in data["by_path"] if s["path"] == "/health")
    assert health["requests"] >= 1
    assert health["avg_latency_ms"] >= 0.0
    assert any(u["user"] == "test-admin" for u in data["by_user"])


# ---------------------------------------------------------------------------
# GET /api/v1/documents/{document_id}
# ---------------------------------------------------------------------------


def test_document_status_endpoint_after_ingest(client):
    from backend.ingestion.pipeline import IngestionPipeline

    ctx = client.app.state.ctx
    pipeline = IngestionPipeline(ctx)
    result = asyncio.run(
        pipeline.ingest_text(
            "code-travail-status.txt",
            "Article 1: Tout travailleur a droit à un salaire équitable.\n\n"
            "Article 2: Le salaire est fixé par convention collective.",
        )
    )
    assert result.status == "indexed"

    response = client.get(f"/api/v1/documents/{result.document_id}")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["document_id"] == result.document_id
    assert data["document_name"] == result.document_name
    assert data["version"] == 1
    assert data["content_hash"]
    assert data["article_count"] >= 1
    assert data["chunk_count"] == result.chunks_created > 0
    # Latest persisted ingestion record rides along when available.
    assert data["ingestion"]["status"] == "indexed"
    assert data["ingestion"]["chunks_created"] == result.chunks_created


def test_document_status_404_on_unknown_document(client):
    assert client.get("/api/v1/documents/doc-inconnu").status_code == 404


# ---------------------------------------------------------------------------
# Trace gating (spec §48): full trace for ADMIN only
# ---------------------------------------------------------------------------


def test_chat_trace_visible_to_admin(client):
    response = client.post(
        "/api/v1/chat",
        json={"query": "Quel est le préavis de licenciement au Burkina Faso ?"},
        headers=_headers(Role.ADMIN),
    )
    assert response.status_code == 200, response.text
    assert response.json()["trace"], "admin should see the internal trace"


def test_chat_trace_hidden_from_plain_user(client):
    response = client.post(
        "/api/v1/chat",
        json={"query": "Quelle est la durée du congé annuel ?"},
        headers=_headers(Role.USER),
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["trace"] == []
    assert data["answer"]["answer"]  # the answer itself is unaffected


def test_chat_trace_hidden_from_anonymous_dev_caller(client):
    # Development mode without a token degrades to an anonymous USER payload.
    response = client.post(
        "/api/v1/chat",
        json={"query": "Quel est le salaire minimum au Burkina Faso ?"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["trace"] == []
