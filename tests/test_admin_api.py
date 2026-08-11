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


# ---------------------------------------------------------------------------
# Admin dashboard endpoints: role gating
# ---------------------------------------------------------------------------

NEW_ADMIN_ROUTES = [
    ("get", "/api/v1/admin/users"),
    ("get", "/api/v1/admin/usage"),
    ("get", "/api/v1/admin/providers"),
    ("get", "/api/v1/admin/documents/folders"),
    ("post", "/api/v1/admin/documents/folders"),
    ("post", "/api/v1/admin/documents/metadata-suggestion"),
    ("post", "/api/v1/admin/documents/upload"),
    ("patch", "/api/v1/admin/users/some-user-id"),
    ("delete", "/api/v1/admin/documents/some-doc-id"),
]


def _route_kwargs(method: str, path: str) -> dict:
    """Minimal valid bodies so role gating (not validation) decides."""
    if path.endswith(("metadata-suggestion", "upload")):
        return {"files": {"file": ("note.txt", b"bonjour le monde", "text/plain")}}
    if method == "post" and path.endswith("/folders"):
        return {"json": {"name": "travail"}}
    if method == "patch":
        return {"json": {"tier": "pro"}}
    return {}


def test_dashboard_endpoints_reject_non_admin_roles(client):
    for method, path in NEW_ADMIN_ROUTES:
        kwargs = _route_kwargs(method, path)
        # Dev-mode anonymous callers map to Role.USER: below ADMIN.
        assert client.request(method, path, **kwargs).status_code == 403, (method, path)
        for role in (Role.USER, Role.VIEWER, Role.LEGAL_EXPERT):
            response = client.request(method, path, headers=_headers(role), **kwargs)
            assert response.status_code == 403, (method, path, role)


# ---------------------------------------------------------------------------
# GET/PATCH /api/v1/admin/users
# ---------------------------------------------------------------------------


def _create_db_user(client, email: str = "jurist@example.com"):
    ctx = client.app.state.ctx
    return asyncio.run(ctx.user_store.create_user(email, "password123", "Jurist"))


def test_admin_users_list_includes_db_users(client):
    record = _create_db_user(client)
    response = client.get("/api/v1/admin/users", headers=_headers(Role.ADMIN))
    assert response.status_code == 200, response.text
    entry = next(u for u in response.json()["users"] if u["id"] == record.id)
    assert entry["email"] == "jurist@example.com"
    assert entry["name"] == "Jurist"
    assert entry["role"] == "user"
    assert entry["tier"] == "gratuit"
    assert entry["created_at"]
    assert entry["today_tokens_in"] == 0
    assert entry["today_tokens_out"] == 0
    assert entry["today_requests"] == 0


def test_admin_users_patch_updates_tier_and_role(client):
    record = _create_db_user(client)
    response = client.patch(
        f"/api/v1/admin/users/{record.id}",
        json={"tier": "pro", "role": "legal_expert"},
        headers=_headers(Role.ADMIN),
    )
    assert response.status_code == 200, response.text
    assert response.json()["tier"] == "pro"
    assert response.json()["role"] == "legal_expert"


def test_admin_users_patch_rejects_invalid_values(client):
    record = _create_db_user(client)
    headers = _headers(Role.ADMIN)
    assert client.patch(
        f"/api/v1/admin/users/{record.id}", json={"tier": "gold"}, headers=headers
    ).status_code == 400
    assert client.patch(
        f"/api/v1/admin/users/{record.id}", json={"role": "superadmin"}, headers=headers
    ).status_code == 400
    assert client.patch(
        f"/api/v1/admin/users/{record.id}", json={}, headers=headers
    ).status_code == 400
    assert client.patch(
        "/api/v1/admin/users/unknown-id", json={"tier": "pro"}, headers=headers
    ).status_code == 404


def test_admin_users_patch_rejects_self_change(client):
    # The test admin token's subject is "test-admin" (no DB user_id claim).
    response = client.patch(
        "/api/v1/admin/users/test-admin",
        json={"role": "viewer"},
        headers=_headers(Role.ADMIN),
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/v1/admin/usage
# ---------------------------------------------------------------------------


def test_admin_usage_aggregates_window(client):
    ctx = client.app.state.ctx
    record = _create_db_user(client, "usage@example.com")
    asyncio.run(ctx.user_store.record_usage(record.id, 100, 50))
    asyncio.run(ctx.user_store.record_usage(record.id, 20, 10))

    response = client.get("/api/v1/admin/usage?days=30", headers=_headers(Role.ADMIN))
    assert response.status_code == 200, response.text
    data = response.json()
    row = next(r for r in data["per_user"] if r["user_id"] == record.id)
    assert row["email"] == "usage@example.com"
    assert row["tokens_in"] == 120
    assert row["tokens_out"] == 60
    assert row["requests"] == 2
    assert data["totals"]["tokens_in"] >= 120
    assert data["totals"]["tokens_out"] >= 60
    assert data["totals"]["requests"] >= 2


# ---------------------------------------------------------------------------
# GET /api/v1/admin/providers
# ---------------------------------------------------------------------------


def test_admin_providers_masks_api_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGAL_AI_LLM_API_KEY", "sk-supersecretkey1234")
    with _make_client(tmp_path, monkeypatch) as client:
        response = client.get("/api/v1/admin/providers", headers=_headers(Role.ADMIN))
        assert response.status_code == 200, response.text
        assert "sk-supersecretkey1234" not in response.text
        data = response.json()
        keyed = [p for p in data["providers"] if p["key_suffix"] is not None]
        assert keyed, "a configured key should be reported masked"
        for entry in keyed:
            assert entry["configured"] is True
            assert entry["key_suffix"] == "…1234"
        assert set(data["defaults"]) == {"gratuit", "pro", "cabinet"}
        assert all(data["defaults"].values())
        assert "infra" in data


def test_admin_providers_without_keys_report_unconfigured(client):
    response = client.get("/api/v1/admin/providers", headers=_headers(Role.ADMIN))
    assert response.status_code == 200, response.text
    data = response.json()
    names = [p["provider"] for p in data["providers"]]
    assert {"ollama", "tokenfree", "openrouter"} <= set(names)
    assert "openai" not in names
    assert "anthropic" not in names
    for entry in data["providers"]:
        assert entry["key_suffix"] is None or entry["key_suffix"].startswith("…")
        assert len(entry["key_suffix"] or "") <= 5


def test_admin_providers_ollama_inherits_main_key(tmp_path, monkeypatch):
    """ollama falls back to llm_api_key (documented single-key setup)."""
    monkeypatch.setenv("LEGAL_AI_LLM_API_KEY", "sk-ollama-cloud-key-4321")
    with _make_client(tmp_path, monkeypatch) as client:
        response = client.get("/api/v1/admin/providers", headers=_headers(Role.ADMIN))
        assert response.status_code == 200, response.text
        assert "sk-ollama-cloud-key-4321" not in response.text
        by_name = {p["provider"]: p for p in response.json()["providers"]}
        assert by_name["ollama"]["configured"] is True
        assert by_name["ollama"]["key_suffix"] == "…4321"
        assert by_name["ollama"]["api_base"] == "https://ollama.com"
        assert by_name["openrouter"]["configured"] is True
        assert by_name["tokenfree"]["configured"] is True


def test_admin_providers_models_grouped_per_provider(client):
    response = client.get("/api/v1/admin/providers", headers=_headers(Role.ADMIN))
    assert response.status_code == 200, response.text
    by_name = {p["provider"]: p for p in response.json()["providers"]}
    ollama_models = [m["id"] for m in by_name["ollama"]["models"]]
    openrouter_models = [m["id"] for m in by_name["openrouter"]["models"]]
    assert any(mid.startswith("ollama/") for mid in ollama_models)
    assert any(mid.startswith("openrouter/") for mid in openrouter_models)
    assert not any(mid.startswith("openrouter/") for mid in ollama_models)
    assert not any(mid.startswith("ollama/") for mid in openrouter_models)
    # Every grouped model carries its lowest unlocking tier.
    for provider in by_name.values():
        for model in provider["models"]:
            assert model["tier_required"] in {"gratuit", "pro", "cabinet"}
    # Per-provider default API bases, not one shared base.
    assert by_name["openrouter"]["api_base"] == "https://openrouter.ai/api/v1"
    assert by_name["tokenfree"]["api_base"] == "https://www.tokenfree.com/v1"
    assert by_name["ollama"]["api_base"] == "https://ollama.com"


# ---------------------------------------------------------------------------
# Legal-docs folder management
# ---------------------------------------------------------------------------


def test_admin_folders_create_and_list(client):
    headers = _headers(Role.ADMIN)
    response = client.post(
        "/api/v1/admin/documents/folders", json={"name": "Droit du Travail"}, headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"name": "droit_du_travail", "created": True}

    # Idempotent: an existing folder is reported, not an error.
    again = client.post(
        "/api/v1/admin/documents/folders", json={"name": "Droit du Travail"}, headers=headers
    )
    assert again.json() == {"name": "droit_du_travail", "created": False}

    # Only supported extensions count towards the file total.
    data_dir = client.app.state.ctx.settings.data_dir
    folder = data_dir / "legal_docs" / "droit_du_travail"
    (folder / "code-travail.txt").write_text("Article 1: ...", encoding="utf-8")
    (folder / "notes.xls").write_text("ignored", encoding="utf-8")

    response = client.get("/api/v1/admin/documents/folders", headers=headers)
    assert response.status_code == 200, response.text
    entry = next(f for f in response.json()["folders"] if f["name"] == "droit_du_travail")
    assert entry["files"] == 1


def test_admin_folders_reject_unsafe_names(client):
    headers = _headers(Role.ADMIN)
    for bad in ("", "   ", "..", "../etc", "a/b"):
        response = client.post(
            "/api/v1/admin/documents/folders", json={"name": bad}, headers=headers
        )
        assert response.status_code == 400, bad


# ---------------------------------------------------------------------------
# POST /api/v1/admin/documents/upload
# ---------------------------------------------------------------------------


def test_admin_upload_writes_file_and_ingests(client):
    headers = _headers(Role.ADMIN)
    client.post("/api/v1/admin/documents/folders", json={"name": "travail"}, headers=headers)

    response = client.post(
        "/api/v1/admin/documents/upload",
        files={
            "file": (
                "code-upload-test.txt",
                "Article 1: Tout travailleur a droit à un salaire équitable.\n\n"
                "Article 2: Le salaire est fixé par convention collective.",
                "text/plain",
            )
        },
        data={
            "folder": "travail",
            "metadata": json.dumps({"url": "https://example.com/loi", "authority": "law"}),
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "indexed"
    assert result["chunks_created"] > 0

    data_dir = client.app.state.ctx.settings.data_dir
    dest = data_dir / "legal_docs" / "travail" / "code-upload-test.txt"
    assert dest.exists()


def test_admin_upload_rejects_unknown_or_unsafe_folder(client):
    headers = _headers(Role.ADMIN)
    for folder in ("inconnu", "..", "../data"):
        response = client.post(
            "/api/v1/admin/documents/upload",
            files={"file": ("doc.txt", "Article 1: texte.", "text/plain")},
            data={"folder": folder},
            headers=headers,
        )
        assert response.status_code == 400, folder


def test_admin_upload_rejects_invalid_metadata_json(client):
    response = client.post(
        "/api/v1/admin/documents/upload",
        files={"file": ("doc.txt", "Article 1: texte.", "text/plain")},
        data={"metadata": "{not json"},
        headers=_headers(Role.ADMIN),
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/v1/admin/documents/metadata-suggestion
# ---------------------------------------------------------------------------

#: Neutral name/text that match no heuristic keyword (authority, domain, type).
_NEUTRAL_NAME = "note-horaires-xyz.txt"
_NEUTRAL_TEXT = (
    "Note d information relative aux horaires d ouverture des bureaux "
    "pendant la periode estivale."
)


class _FakeLLM:
    """Minimal async LLM stand-in with a non-mock provider."""

    provider = "openai"

    def __init__(self, response: str = "", exc: Exception | None = None):
        self.response = response
        self.exc = exc

    async def complete(self, system: str, user: str, **kwargs) -> str:
        if self.exc is not None:
            raise self.exc
        return self.response


def _post_suggestion(client, name: str = _NEUTRAL_NAME, text: str = _NEUTRAL_TEXT):
    return client.post(
        "/api/v1/admin/documents/metadata-suggestion",
        files={"file": (name, text, "text/plain")},
        headers=_headers(Role.ADMIN),
    )


def _assert_nothing_ingested(client):
    from backend.ingestion.versioning import VersionStore

    data_dir = client.app.state.ctx.settings.data_dir
    assert VersionStore(data_dir)._load() == {}


def test_metadata_suggestion_merges_llm_classification(client):
    ctx = client.app.state.ctx
    ctx.llm = _FakeLLM(
        response=json.dumps(
            {
                "document_title": "Note de service XYZ",
                "authority": "order",
                "document_type": "decision",
                "legal_domains": ["labor_code", "not_a_domain"],
            }
        )
    )
    response = _post_suggestion(client)
    assert response.status_code == 200, response.text
    data = response.json()
    suggestion = data["suggestion"]
    assert suggestion["document_name"] == "Note de service XYZ"
    assert suggestion["authority"] == "order"
    assert suggestion["document_type"] == "decision"
    # Domains are filtered to the known taxonomy.
    assert suggestion["legal_domains"] == ["labor_code"]
    assert "labor_code" in data["available_domains"]
    _assert_nothing_ingested(client)


def test_metadata_suggestion_llm_failure_falls_back_to_heuristics(client):
    ctx = client.app.state.ctx
    ctx.llm = _FakeLLM(exc=RuntimeError("provider down"))
    response = _post_suggestion(client)
    assert response.status_code == 200, response.text  # not an error
    suggestion = response.json()["suggestion"]
    # Neutral document: the heuristics found nothing either.
    assert suggestion["document_name"] == _NEUTRAL_NAME
    assert suggestion["authority"] == ""
    assert suggestion["legal_domains"] == []
    _assert_nothing_ingested(client)


def test_metadata_suggestion_heuristics_only_with_mock_llm(client):
    response = _post_suggestion(
        client,
        name="Loi 028-2008 portant code du travail.txt",
        text="Article 1: Tout travailleur a droit à un salaire équitable.",
    )
    assert response.status_code == 200, response.text
    suggestion = response.json()["suggestion"]
    assert suggestion["authority"] == "law"
    assert suggestion["document_type"] == "code"
    assert suggestion["law_number"] == "028-2008"
    assert "labor_code" in suggestion["legal_domains"]
    _assert_nothing_ingested(client)


# ---------------------------------------------------------------------------
# DELETE /api/v1/admin/documents/{document_id}
# ---------------------------------------------------------------------------


def test_admin_delete_document_forwards_to_pipeline(client):
    from backend.ingestion.pipeline import IngestionPipeline

    ctx = client.app.state.ctx
    pipeline = IngestionPipeline(ctx)
    result = asyncio.run(
        pipeline.ingest_text(
            "doc-a-supprimer.txt",
            "Article 1: Tout travailleur a droit à un salaire équitable.",
        )
    )
    assert result.status == "indexed"

    response = client.delete(
        f"/api/v1/admin/documents/{result.document_id}", headers=_headers(Role.ADMIN)
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "deleted"
    assert response.json()["document_id"] == result.document_id

    # The document is gone from the registry and the store.
    assert client.get(f"/api/v1/documents/{result.document_id}").status_code == 404
