"""Tests for API key authentication (A1)."""
import os
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient


def _make_client():
    """Fresh import of app to pick up env changes."""
    from app.main import app
    return TestClient(app)


def test_health_always_public():
    """GET /health should work even when auth is enabled."""
    with patch.dict("os.environ", {"BRAIN_API_KEY": "secret123"}):
        from app import auth
        auth._API_KEY = "secret123"  # force reload
        client = _make_client()
        resp = client.get("/health")
        assert resp.status_code == 200
        auth._API_KEY = None  # reset


def test_auth_disabled_when_no_key():
    """When BRAIN_API_KEY is not set, all endpoints should work without auth."""
    from app import auth
    auth._API_KEY = None
    with patch("app.ingestion.manual.get_memory_by_content_hash", return_value=None), \
         patch("app.ingestion.manual.ingest", new_callable=AsyncMock) as mock_ingest:
        from app.models import MemoryEntry
        mock_ingest.return_value = MemoryEntry(id="x", content="c", type="note", project="p")
        client = _make_client()
        resp = client.post("/ingest/note", json={
            "content": "test", "project": "proj"
        })
    assert resp.status_code == 201


def test_auth_rejects_missing_key():
    """When BRAIN_API_KEY is set, requests without the header should get 401."""
    from app import auth
    auth._API_KEY = "secret123"
    try:
        client = _make_client()
        resp = client.post("/ingest/note", json={
            "content": "test", "project": "proj"
        })
        assert resp.status_code == 401
    finally:
        auth._API_KEY = None


def test_auth_rejects_wrong_key():
    """Requests with wrong key should get 401."""
    from app import auth
    auth._API_KEY = "secret123"
    try:
        client = _make_client()
        resp = client.post("/ingest/note",
                           json={"content": "test", "project": "proj"},
                           headers={"X-Brain-Key": "wrong"})
        assert resp.status_code == 401
    finally:
        auth._API_KEY = None


def test_auth_accepts_correct_key():
    """Requests with correct key should pass through."""
    from app import auth
    auth._API_KEY = "secret123"
    try:
        with patch("app.ingestion.manual.get_memory_by_content_hash", return_value=None), \
             patch("app.ingestion.manual.ingest", new_callable=AsyncMock) as mock_ingest:
            from app.models import MemoryEntry
            mock_ingest.return_value = MemoryEntry(id="x", content="c", type="note", project="p")
            client = _make_client()
            resp = client.post("/ingest/note",
                               json={"content": "test", "project": "proj"},
                               headers={"X-Brain-Key": "secret123"})
        assert resp.status_code == 201
    finally:
        auth._API_KEY = None


def test_mcp_tools_always_public():
    with patch.dict(os.environ, {"BRAIN_API_KEY": "secret-key"}):
        from app.main import app
        client = TestClient(app)
        resp = client.get("/mcp-tools")
    assert resp.status_code == 200
