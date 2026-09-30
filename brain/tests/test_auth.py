"""Tests for API key authentication (A1).

The gate is `PureASGIAuthMiddleware` in app/main.py, which calls
`os.getenv("BRAIN_API_KEY")` on every request. It never reads the
`app.auth._API_KEY` module global — that is an import-time snapshot belonging to
the `require_api_key` helper, which is not wired to any route. So these tests
drive the environment variable, not the global; assigning the global simulates
nothing and lets a real middleware regression through.
"""
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient


def _make_client():
    from app.main import app
    return TestClient(app)


def test_health_always_public(monkeypatch):
    """GET /health should work even when auth is enabled."""
    monkeypatch.setenv("BRAIN_API_KEY", "secret123")
    client = _make_client()
    resp = client.get("/health")
    assert resp.status_code == 200


def test_auth_disabled_when_no_key(monkeypatch):
    """When BRAIN_API_KEY is not set, all endpoints should work without auth."""
    monkeypatch.delenv("BRAIN_API_KEY", raising=False)
    with patch("app.ingestion.manual.get_memory_by_content_hash", return_value=None), \
         patch("app.ingestion.manual.ingest", new_callable=AsyncMock) as mock_ingest:
        from app.models import MemoryEntry
        mock_ingest.return_value = MemoryEntry(id="x", content="c", type="note", project="p")
        client = _make_client()
        resp = client.post("/ingest/note", json={
            "content": "test", "project": "proj"
        })
    assert resp.status_code == 201


def test_auth_rejects_missing_key(monkeypatch):
    """When BRAIN_API_KEY is set, requests without the header should get 401."""
    monkeypatch.setenv("BRAIN_API_KEY", "secret123")
    client = _make_client()
    resp = client.post("/ingest/note", json={
        "content": "test", "project": "proj"
    })
    assert resp.status_code == 401


def test_auth_rejects_wrong_key(monkeypatch):
    """Requests with wrong key should get 401."""
    monkeypatch.setenv("BRAIN_API_KEY", "secret123")
    client = _make_client()
    resp = client.post("/ingest/note",
                       json={"content": "test", "project": "proj"},
                       headers={"X-Brain-Key": "wrong"})
    assert resp.status_code == 401


def test_auth_accepts_correct_key(monkeypatch):
    """Requests with correct key should pass through."""
    monkeypatch.setenv("BRAIN_API_KEY", "secret123")
    with patch("app.ingestion.manual.get_memory_by_content_hash", return_value=None), \
         patch("app.ingestion.manual.ingest", new_callable=AsyncMock) as mock_ingest:
        from app.models import MemoryEntry
        mock_ingest.return_value = MemoryEntry(id="x", content="c", type="note", project="p")
        client = _make_client()
        resp = client.post("/ingest/note",
                           json={"content": "test", "project": "proj"},
                           headers={"X-Brain-Key": "secret123"})
    assert resp.status_code == 201
