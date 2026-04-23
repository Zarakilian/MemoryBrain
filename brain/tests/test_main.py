from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock, MagicMock
from app.main import app

client = TestClient(app)


def test_health_returns_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_status_endpoint_returns_structure(tmp_db):
    with patch("app.main.DB_PATH", tmp_db):
        with patch("app.storage.DB_PATH", tmp_db):
            resp = client.get("/status")
            assert resp.status_code == 200
            data = resp.json()
            assert "project_count" in data
            assert "version" in data
            assert "active_plugins" not in data
            assert "inactive_plugins" not in data


# ── /readiness tests ──────────────────────────────────────────────────────────

def _make_ollama_list_response(model_names: list[str]):
    """Build a mock ollama list() response with the given model name strings."""
    models = []
    for name in model_names:
        m = MagicMock()
        m.model = name
        models.append(m)
    resp = MagicMock()
    resp.models = models
    return resp


def test_readiness_all_ok(tmp_db):
    mock_list = AsyncMock(return_value=_make_ollama_list_response(
        ["embeddinggemma:latest", "llama3.2:3b"]
    ))
    with patch("app.main.DB_PATH", tmp_db), \
         patch("app.storage.DB_PATH", tmp_db), \
         patch("app.main.ollama_client") as mock_oc, \
         patch("app.main.get_chroma_client") as mock_chroma:
        mock_oc.list = mock_list
        mock_chroma.return_value.get_or_create_collection.return_value = MagicMock()
        resp = client.get("/readiness")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is True
    assert data["checks"]["sqlite"] == "ok"
    assert data["checks"]["chromadb"] == "ok"
    assert data["checks"]["ollama"] == "ok"
    assert data["checks"]["embedding_model"] == "ok"
    assert data["checks"]["summary_model"] == "ok"


def test_readiness_ollama_down(tmp_db):
    mock_list = AsyncMock(side_effect=ConnectionError("refused"))
    with patch("app.main.DB_PATH", tmp_db), \
         patch("app.storage.DB_PATH", tmp_db), \
         patch("app.main.ollama_client") as mock_oc, \
         patch("app.main.get_chroma_client") as mock_chroma:
        mock_oc.list = mock_list
        mock_chroma.return_value.get_or_create_collection.return_value = MagicMock()
        resp = client.get("/readiness")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is False
    assert data["checks"]["ollama"] == "error"
    assert data["checks"]["embedding_model"] == "unknown"
    assert data["checks"]["summary_model"] == "unknown"
    assert data["checks"]["sqlite"] == "ok"


def test_readiness_models_missing(tmp_db):
    # Ollama is up but models not yet pulled
    mock_list = AsyncMock(return_value=_make_ollama_list_response([]))
    with patch("app.main.DB_PATH", tmp_db), \
         patch("app.storage.DB_PATH", tmp_db), \
         patch("app.main.ollama_client") as mock_oc, \
         patch("app.main.get_chroma_client") as mock_chroma:
        mock_oc.list = mock_list
        mock_chroma.return_value.get_or_create_collection.return_value = MagicMock()
        resp = client.get("/readiness")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is False
    assert data["checks"]["ollama"] == "ok"
    assert data["checks"]["embedding_model"] == "missing"
    assert data["checks"]["summary_model"] == "missing"


def test_readiness_chromadb_down(tmp_db):
    mock_list = AsyncMock(return_value=_make_ollama_list_response(
        ["embeddinggemma:latest", "llama3.2:3b"]
    ))
    with patch("app.main.DB_PATH", tmp_db), \
         patch("app.storage.DB_PATH", tmp_db), \
         patch("app.main.ollama_client") as mock_oc, \
         patch("app.main.get_chroma_client") as mock_chroma:
        mock_oc.list = mock_list
        mock_chroma.side_effect = Exception("ChromaDB unavailable")
        resp = client.get("/readiness")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is False
    assert data["checks"]["chromadb"] == "error"
    assert data["checks"]["ollama"] == "ok"


def test_next_session_no_project_falls_back_to_latest(tmp_db):
    """GET /next-session with no project param falls back to most recently active project."""
    from app.storage import upsert_project, add_memory as storage_add
    from app.models import Project, MemoryEntry
    import json
    with patch("app.main.DB_PATH", tmp_db), patch("app.storage.DB_PATH", tmp_db):
        p = Project(slug="testproj", name="Test Project")
        upsert_project(p, db_path=tmp_db)
        note = MemoryEntry(
            content="Next: remember to check the deploy logs",
            type="note",
            project="testproj",
            tags=["next_session"],
        )
        storage_add(note, db_path=tmp_db)
        resp = client.get("/next-session")
        assert resp.status_code == 200
        assert "deploy logs" in resp.json()["notes"]


def test_readiness_is_public_when_auth_enabled():
    """GET /readiness must work without API key even when auth is enabled."""
    from app import auth
    auth._API_KEY = "secret"
    try:
        resp = client.get("/readiness")
        assert resp.status_code == 200
    finally:
        auth._API_KEY = None


# ── OAuth discovery 404 format tests ─────────────────────────────────────────

def test_404_returns_oauth_error_format():
    """404 responses must use OAuth error format for Claude Code MCP compatibility.

    Claude Code's MCP client probes /.well-known/oauth-protected-resource before
    SSE connection. FastAPI's default 404 {"detail":"Not Found"} fails the client's
    Zod schema (expects "error" field), leaving it stuck in auth-required mode.
    """
    resp = client.get("/nonexistent-endpoint")
    assert resp.status_code == 404
    data = resp.json()
    assert data["error"] == "not_found"
    assert data["error_description"] == "Not found"
    assert "detail" not in data


def test_oauth_discovery_probe_returns_oauth_404():
    """The exact endpoint Claude Code probes must return OAuth-formatted 404."""
    resp = client.get("/.well-known/oauth-protected-resource")
    assert resp.status_code == 404
    data = resp.json()
    assert data["error"] == "not_found"
