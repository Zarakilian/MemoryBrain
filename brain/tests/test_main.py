from fastapi.testclient import TestClient
from unittest.mock import patch
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
