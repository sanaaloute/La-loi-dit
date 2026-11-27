import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.core.config import get_settings


@pytest.fixture
def dossier_client(tmp_path, monkeypatch):
    """A TestClient with a fresh in-memory app and one dev user."""
    monkeypatch.setenv("LEGAL_AI_ENV", "development")
    monkeypatch.setenv("LEGAL_AI_REDIS_ENABLED", "false")
    monkeypatch.setenv("LEGAL_AI_MILVUS_ENABLED", "false")
    monkeypatch.setenv("LEGAL_AI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LEGAL_AI_SECRET_KEY", "test-secret-key-for-jwt-signing")
    monkeypatch.setenv("LEGAL_AI_LLM_PROVIDER", "mock")
    monkeypatch.setenv("LEGAL_AI_DEV_USERS", "admin:test:admin")
    monkeypatch.setenv(
        "LEGAL_AI_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/legal_ai.db"
    )
    get_settings.cache_clear()

    app = create_app()
    with TestClient(app) as client:
        yield client


def _token(client: TestClient) -> str:
    res = client.post("/api/v1/auth/token", json={"username": "admin", "password": "test"})
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def _upload(client: TestClient, token: str) -> dict:
    res = client.post(
        "/api/v1/dossiers",
        files={"file": ("contrat_bail.txt", "Bail commercial. Article 1: le loyer est dû.".encode(), "text/plain")},
        data={"title": "Contrat de bail"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_upload_list_text_and_delete_dossier(dossier_client):
    token = _token(dossier_client)
    headers = {"Authorization": f"Bearer {token}"}

    dossier = _upload(dossier_client, token)
    assert dossier["status"] == "ready"
    assert dossier["name"] == "Contrat de bail"
    assert dossier["char_count"] > 0

    res = dossier_client.get("/api/v1/dossiers", headers=headers)
    assert res.status_code == 200
    assert [d["id"] for d in res.json()] == [dossier["id"]]

    res = dossier_client.get(f"/api/v1/dossiers/{dossier['id']}/text", headers=headers)
    assert res.status_code == 200
    assert "le loyer est dû" in res.json()["text"]

    res = dossier_client.delete(f"/api/v1/dossiers/{dossier['id']}", headers=headers)
    assert res.status_code == 204
    assert dossier_client.get("/api/v1/dossiers", headers=headers).json() == []


def test_upload_rejects_unsupported_extension(dossier_client):
    token = _token(dossier_client)
    res = dossier_client.post(
        "/api/v1/dossiers",
        files={"file": ("virus.exe", b"MZ", "application/octet-stream")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 415


def test_analyze_dossier_returns_answer(dossier_client):
    token = _token(dossier_client)
    dossier = _upload(dossier_client, token)
    res = dossier_client.post(
        f"/api/v1/dossiers/{dossier['id']}/analyze",
        json={"question": "Que dit l'article 1 ?"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["answer"].strip()
    assert data["truncated"] is False
    assert data["chars_used"] > 0


def test_dossiers_require_auth(dossier_client):
    assert dossier_client.get("/api/v1/dossiers").status_code in (401, 403)
    res = dossier_client.post(
        "/api/v1/dossiers",
        files={"file": ("a.txt", b"hello", "text/plain")},
    )
    assert res.status_code in (401, 403)


def test_foreign_dossier_is_not_found(dossier_client):
    token = _token(dossier_client)
    headers = {"Authorization": f"Bearer {token}"}
    assert dossier_client.get("/api/v1/dossiers/inexistant/text", headers=headers).status_code == 404
    assert dossier_client.delete("/api/v1/dossiers/inexistant", headers=headers).status_code == 404


def _create_folder(client: TestClient, token: str, name: str = "Affaire Kaboré") -> dict:
    res = client.post(
        "/api/v1/dossiers/folders",
        json={"name": name, "description": "Litige bail commercial"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_folder_crud_and_document_grouping(dossier_client):
    token = _token(dossier_client)
    headers = {"Authorization": f"Bearer {token}"}

    folder = _create_folder(dossier_client, token)
    assert folder["name"] == "Affaire Kaboré"
    assert folder["document_count"] == 0

    dossier = _upload(dossier_client, token)
    assert dossier["folder_id"] == ""

    # Move the document into the folder.
    res = dossier_client.patch(
        f"/api/v1/dossiers/{dossier['id']}", json={"folder_id": folder["id"]}, headers=headers
    )
    assert res.status_code == 200, res.text
    assert res.json()["folder_id"] == folder["id"]

    # Folder detail lists the document; the folder list counts it.
    res = dossier_client.get(f"/api/v1/dossiers/folders/{folder['id']}", headers=headers)
    assert res.status_code == 200
    detail = res.json()
    assert detail["folder"]["document_count"] == 1
    assert [d["id"] for d in detail["documents"]] == [dossier["id"]]
    res = dossier_client.get("/api/v1/dossiers/folders", headers=headers)
    assert res.json()[0]["document_count"] == 1

    # folder_id filter: folder docs vs unfiled docs.
    res = dossier_client.get(f"/api/v1/dossiers?folder_id={folder['id']}", headers=headers)
    assert [d["id"] for d in res.json()] == [dossier["id"]]
    assert dossier_client.get("/api/v1/dossiers?folder_id=", headers=headers).json() == []

    # Rename, then delete the folder: the document becomes unfiled, not deleted.
    res = dossier_client.patch(
        f"/api/v1/dossiers/folders/{folder['id']}", json={"name": "Affaire Kaboré (renommé)"}, headers=headers
    )
    assert res.status_code == 200
    assert res.json()["name"] == "Affaire Kaboré (renommé)"
    assert dossier_client.delete(f"/api/v1/dossiers/folders/{folder['id']}", headers=headers).status_code == 204
    res = dossier_client.get("/api/v1/dossiers", headers=headers)
    assert res.json()[0]["folder_id"] == ""


def test_folder_delete_with_documents_removes_files(dossier_client):
    token = _token(dossier_client)
    headers = {"Authorization": f"Bearer {token}"}
    folder = _create_folder(dossier_client, token)

    res = dossier_client.post(
        "/api/v1/dossiers",
        files={"file": ("pv.txt", "Proces-verbal de constat.".encode(), "text/plain")},
        data={"folder_id": folder["id"]},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    dossier = res.json()
    assert dossier["folder_id"] == folder["id"]

    res = dossier_client.delete(
        f"/api/v1/dossiers/folders/{folder['id']}?delete_documents=true", headers=headers
    )
    assert res.status_code == 204
    assert dossier_client.get("/api/v1/dossiers", headers=headers).json() == []


def test_upload_into_unknown_folder_fails(dossier_client):
    token = _token(dossier_client)
    res = dossier_client.post(
        "/api/v1/dossiers",
        files={"file": ("a.txt", b"hello", "text/plain")},
        data={"folder_id": "inexistant"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 404


def test_folder_analyze_documents_mode(dossier_client):
    token = _token(dossier_client)
    headers = {"Authorization": f"Bearer {token}"}
    folder = _create_folder(dossier_client, token)
    dossier = _upload(dossier_client, token)
    dossier_client.patch(
        f"/api/v1/dossiers/{dossier['id']}", json={"folder_id": folder["id"]}, headers=headers
    )

    res = dossier_client.post(
        f"/api/v1/dossiers/folders/{folder['id']}/analyze",
        json={"question": "Que dit le contrat sur le loyer ?", "mode": "documents"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["mode"] == "documents"
    assert data["answer"].strip()
    assert data["documents_used"] == ["Contrat de bail"]


def test_folder_analyze_case_mode_runs_legal_pipeline(dossier_client):
    token = _token(dossier_client)
    headers = {"Authorization": f"Bearer {token}"}
    folder = _create_folder(dossier_client, token)
    dossier = _upload(dossier_client, token)
    dossier_client.patch(
        f"/api/v1/dossiers/{dossier['id']}", json={"folder_id": folder["id"]}, headers=headers
    )

    res = dossier_client.post(
        f"/api/v1/dossiers/folders/{folder['id']}/analyze",
        json={"question": "Quels sont mes recours ?", "mode": "case"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["mode"] == "case"
    assert data["answer"].strip()
    assert "confidence" in data


def test_folder_analyze_empty_folder_fails(dossier_client):
    token = _token(dossier_client)
    folder = _create_folder(dossier_client, token)
    res = dossier_client.post(
        f"/api/v1/dossiers/folders/{folder['id']}/analyze",
        json={"question": "Analyse mon affaire"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 422


def _wait_job(client: TestClient, url: str, headers: dict, timeout: float = 10.0) -> dict:
    """Poll an analysis job until it leaves ``running`` (or fail the test)."""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        res = client.get(url, headers=headers)
        assert res.status_code == 200, res.text
        data = res.json()
        if data["status"] != "running":
            return data
        time.sleep(0.1)
    raise AssertionError("analysis job did not complete in time")


def test_dossier_analyze_job_completes(dossier_client):
    token = _token(dossier_client)
    headers = {"Authorization": f"Bearer {token}"}
    dossier = _upload(dossier_client, token)

    res = dossier_client.post(
        f"/api/v1/dossiers/{dossier['id']}/analyze/jobs",
        json={"question": "Que dit l'article 1 ?"},
        headers=headers,
    )
    assert res.status_code == 202, res.text
    job_id = res.json()["job_id"]

    done = _wait_job(
        dossier_client, f"/api/v1/dossiers/{dossier['id']}/analyze/jobs/{job_id}", headers
    )
    assert done["status"] == "done"
    assert done["result"]["answer"].strip()
    assert done["result"]["chars_used"] > 0


def test_folder_analyze_job_completes(dossier_client):
    token = _token(dossier_client)
    headers = {"Authorization": f"Bearer {token}"}
    folder = _create_folder(dossier_client, token)
    dossier = _upload(dossier_client, token)
    dossier_client.patch(
        f"/api/v1/dossiers/{dossier['id']}", json={"folder_id": folder["id"]}, headers=headers
    )

    res = dossier_client.post(
        f"/api/v1/dossiers/folders/{folder['id']}/analyze/jobs",
        json={"question": "Que dit le contrat sur le loyer ?", "mode": "documents"},
        headers=headers,
    )
    assert res.status_code == 202, res.text
    job_id = res.json()["job_id"]

    done = _wait_job(
        dossier_client, f"/api/v1/dossiers/folders/{folder['id']}/analyze/jobs/{job_id}", headers
    )
    assert done["status"] == "done"
    assert done["result"]["mode"] == "documents"
    assert done["result"]["documents_used"] == ["Contrat de bail"]


def test_analyze_job_unknown_is_404(dossier_client):
    import uuid

    token = _token(dossier_client)
    headers = {"Authorization": f"Bearer {token}"}
    dossier = _upload(dossier_client, token)
    res = dossier_client.get(
        f"/api/v1/dossiers/{dossier['id']}/analyze/jobs/{uuid.uuid4().hex}",
        headers=headers,
    )
    assert res.status_code == 404


def test_analyze_job_requires_auth(dossier_client):
    res = dossier_client.post(
        "/api/v1/dossiers/some-id/analyze/jobs", json={"question": "Q ?"}
    )
    assert res.status_code in (401, 403)
