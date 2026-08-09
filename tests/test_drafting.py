"""Phase 3 tests: legal document drafting (templates, tier gating, grounding).

Fully offline (mock LLM, tmp SQLite). Fresh app per test with a tmp database.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

os.environ["LEGAL_AI_LLM_PROVIDER"] = "mock"
os.environ["LEGAL_AI_LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LEGAL_AI_LANGFUSE_SECRET_KEY"] = ""

from backend.core.config import get_settings  # noqa: E402

PASSWORD = "motdepasse1"

CDI_FIELDS = {
    "employeur": "Faso Agro SARL",
    "salarie": "Awa Compaoré",
    "poste": "Comptable",
    "date_debut": "2026-08-01",
    "salaire": "150000",
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Fresh app + TestClient with a tmp user database (offline)."""
    monkeypatch.setenv("LEGAL_AI_ENV", "development")
    monkeypatch.setenv("LEGAL_AI_LLM_PROVIDER", "mock")
    monkeypatch.setenv("LEGAL_AI_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/draft_test.db")
    monkeypatch.setenv("LEGAL_AI_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    from fastapi.testclient import TestClient

    from backend.api.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


def _register(client, name: str = "Awa") -> tuple[str, str]:
    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "name": name},
    )
    assert response.status_code == 201, response.text
    return email, response.json()["access_token"]


def _admin_token(client) -> str:
    response = client.post("/api/v1/auth/token", json={"username": "admin", "password": "admin123"})
    assert response.status_code == 200
    return response.json()["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _set_tier(client, token: str, tier: str) -> None:
    me = client.get("/api/v1/auth/me", headers=_headers(token)).json()
    asyncio.run(client.app.state.ctx.user_store.set_tier(me["id"], tier))


# ---------------------------------------------------------------------------
# Templates listing & tier gate
# ---------------------------------------------------------------------------


def test_templates_listed_for_privileged_tier(client):
    token = _admin_token(client)  # dev admin => cabinet tier
    response = client.get("/api/v1/draft/templates", headers=_headers(token))
    assert response.status_code == 200
    templates = response.json()["templates"]
    assert len(templates) == 6
    by_id = {t["id"]: t for t in templates}
    assert set(by_id) == {
        "contrat_travail_cdi",
        "bail_commercial",
        "accord_confidentialite",
        "contrat_prestation",
        "requete_instance",
        "plainte",
    }
    assert by_id["contrat_travail_cdi"]["category"] == "contract"
    assert by_id["requete_instance"]["category"] == "case"
    assert by_id["plainte"]["category"] == "case"
    # metadata only: no skeletons leaked
    assert "skeleton" not in by_id["contrat_travail_cdi"]
    # fields carry the UI contract
    field_names = {f["name"] for f in by_id["contrat_travail_cdi"]["fields"]}
    assert {"employeur", "salarie", "poste", "salaire"} <= field_names
    required = {f["name"] for f in by_id["contrat_travail_cdi"]["fields"] if f["required"]}
    assert "employeur" in required


def test_gratuit_user_can_access_drafting_in_dev_mode(client):
    """Dev mode: all tiers share every feature, drafting included."""
    _, token = _register(client)
    response = client.get("/api/v1/draft/templates", headers=_headers(token))
    assert response.status_code == 200

    response = client.post(
        "/api/v1/draft",
        json={"template_id": "contrat_travail_cdi", "fields": CDI_FIELDS},
        headers=_headers(token),
    )
    assert response.status_code == 200


def test_drafting_requires_auth(client):
    assert client.get("/api/v1/draft/templates").status_code == 401
    assert client.post("/api/v1/draft", json={"template_id": "x", "fields": {}}).status_code == 401


# ---------------------------------------------------------------------------
# Draft generation (offline)
# ---------------------------------------------------------------------------


def test_generate_draft_offline_returns_filled_skeleton(client):
    _, token = _register(client)
    _set_tier(client, token, "pro")

    response = client.post(
        "/api/v1/draft",
        json={"template_id": "contrat_travail_cdi", "fields": CDI_FIELDS},
        headers=_headers(token),
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["template_id"] == "contrat_travail_cdi"
    draft = data["draft_markdown"]
    assert "Faso Agro SARL" in draft
    assert "Awa Compaoré" in draft
    assert "Comptable" in draft
    assert "150000" in draft
    assert "## Article 1" in draft  # skeleton structure kept

    # offline empty store: no evidence -> generic warning + human review
    assert data["requires_human_review"] is True
    assert any("Aucune source juridique" in w for w in data["warnings"])
    assert data["citations"] == []
    assert "[1]" not in draft  # no fabricated citations
    assert data["latency_ms"] >= 0


def test_unknown_template_returns_404(client):
    token = _admin_token(client)
    response = client.post(
        "/api/v1/draft",
        json={"template_id": "inexistant", "fields": {}},
        headers=_headers(token),
    )
    assert response.status_code == 404


def test_missing_required_field_returns_422(client):
    token = _admin_token(client)
    fields = {k: v for k, v in CDI_FIELDS.items() if k != "salarie"}
    response = client.post(
        "/api/v1/draft",
        json={"template_id": "contrat_travail_cdi", "fields": fields},
        headers=_headers(token),
    )
    assert response.status_code == 422
    assert "Champs requis manquants" in response.json()["detail"]


def test_grounded_draft_cites_verified_provision(client):
    """With evidence in the store, the references section yields verified citations."""
    from backend.core.models import EvidenceChunk

    ctx = client.app.state.ctx
    chunk = EvidenceChunk(
        document_name="Code du travail du Burkina Faso",
        article="18",
        content=(
            "Le contrat de travail à durée indéterminée peut toujours cesser sur "
            "la volonté des parties. L'employeur doit verser la rémunération du "
            "salarié. La résiliation du contrat de travail exige un préavis "
            "conformément au code du travail du Burkina Faso."
        ),
    )
    vectors = asyncio.run(ctx.embedder.embed([chunk.content]))
    asyncio.run(ctx.vector_store.upsert([chunk], vectors))

    token = _admin_token(client)
    response = client.post(
        "/api/v1/draft",
        json={"template_id": "contrat_travail_cdi", "fields": CDI_FIELDS},
        headers=_headers(token),
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["requires_human_review"] is False
    assert data["citations"], "expected at least one verified citation"
    citation = data["citations"][0]
    assert citation["verified"] is True
    assert citation["document_name"] == "Code du travail du Burkina Faso"
    assert citation["article"] == "18"
    assert "[1]" in data["draft_markdown"]
    assert "## Références légales" in data["draft_markdown"]


# ---------------------------------------------------------------------------
# /auth/me features
# ---------------------------------------------------------------------------


def test_me_includes_tier_features(client):
    _, token = _register(client)
    me = client.get("/api/v1/auth/me", headers=_headers(token)).json()
    assert me["tier"] == "gratuit"
    # Dev mode: every tier exposes the full feature set.
    assert me["features"]["drafting"] is True
    assert "csv" in me["features"]["export"]

    _set_tier(client, token, "pro")
    me = client.get("/api/v1/auth/me", headers=_headers(token)).json()
    assert me["tier"] == "pro"
    assert me["features"]["drafting"] is True
    assert "pdf" in me["features"]["export"]
