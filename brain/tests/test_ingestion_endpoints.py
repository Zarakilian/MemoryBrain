# tests/test_ingestion_endpoints.py
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from app.main import app
from app.models import MemoryEntry

client = TestClient(app)


def test_ingest_note_returns_201_with_id(mock_ollama):
    with patch("app.ingestion.manual.ingest", new_callable=AsyncMock) as mock_ingest:
        mock_ingest.return_value = MemoryEntry(id="new-123", content="x", type="note", project="p")
        resp = client.post("/ingest/note", json={
            "content": "clickhouse is slow",
            "project": "monitoring",
            "tags": ["clickhouse"],
        })
    assert resp.status_code == 201
    assert resp.json()["id"] == "new-123"


def test_ingest_note_missing_content_returns_422():
    resp = client.post("/ingest/note", json={"project": "monitoring"})
    assert resp.status_code == 422


def test_ingest_session_returns_201(mock_ollama):
    with patch("app.ingestion.session.ingest", new_callable=AsyncMock) as mock_ingest:
        mock_ingest.return_value = MemoryEntry(id="sess-1", content="x", type="session", project="monitoring")
        resp = client.post("/ingest/session", json={
            "content": "# Handover\nWorked on alerts today.",
            "project": "monitoring",
        })
    assert resp.status_code == 201
    assert "id" in resp.json()


def test_startup_summary_returns_summary_key():
    with patch("app.main.handle_get_startup_summary", new_callable=AsyncMock) as mock_sum:
        mock_sum.return_value = "# MemoryBrain\n- monitoring: last 2026-03-27"
        resp = client.get("/startup-summary")
    assert resp.status_code == 200
    assert "summary" in resp.json()
    assert "monitoring" in resp.json()["summary"]
