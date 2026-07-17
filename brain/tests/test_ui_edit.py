# tests/test_ui_edit.py
"""UI editing endpoints (/api/ui/edit/*): CRUD with guardrails, and the
auth posture — writes enforce X-Brain-Key when set, reads stay bypassed."""
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.main import app
from app.storage import add_memory, upsert_project, get_memory
from app.models import MemoryEntry, Project


@pytest.fixture
def edit_client(tmp_db, monkeypatch):
    monkeypatch.setenv("MEMORYBRAIN_VECTOR_BACKEND", "sqlite_vec")
    patches = [
        patch("app.ui.queries.DB_PATH", tmp_db),
        patch("app.ui.editor.DB_PATH", tmp_db),
        patch("app.graph_queries.DB_PATH", tmp_db),
        patch("app.storage.DB_PATH", tmp_db),
        patch("app.main.DB_PATH", tmp_db),
        patch("app.search.DB_PATH", tmp_db),
        patch("app.ingest_pipeline.DB_PATH", tmp_db),
    ]
    for p in patches:
        p.start()
    upsert_project(Project(slug="proj-a", name="Proj A"), db_path=tmp_db)
    add_memory(MemoryEntry(id="m1", content="original content", summary="sum m1",
                           type="note", project="proj-a", tags=["t1"]), db_path=tmp_db)
    yield TestClient(app), tmp_db
    for p in patches:
        p.stop()


# ------------------------------------------------------------- projects

def test_project_create_edit_and_delete_guardrail(edit_client):
    c, db = edit_client
    r = c.post("/api/ui/edit/projects",
               json={"slug": "fresh", "name": "Fresh", "one_liner": "new one"})
    assert r.status_code == 201 and r.json()["created"] is True
    # upsert = edit
    r = c.post("/api/ui/edit/projects", json={"slug": "fresh", "name": "Fresh 2"})
    assert r.json()["created"] is False
    # empty project deletes fine
    assert c.request("DELETE", "/api/ui/edit/projects/fresh").status_code == 200
    # a project holding memories refuses deletion
    r = c.request("DELETE", "/api/ui/edit/projects/proj-a")
    assert r.status_code == 409 and "guardrail" in r.json()["detail"]
    # bad slug rejected
    assert c.post("/api/ui/edit/projects",
                  json={"slug": "Bad Slug!", "name": "x"}).status_code == 422


# ------------------------------------------------------------- memories

def test_add_note_degraded_without_provider(edit_client):
    c, db = edit_client
    r = c.post("/api/ui/edit/notes",
               json={"content": "a fresh thought", "project": "proj-a",
                     "tags": ["x"], "importance": 4})
    assert r.status_code == 201
    body = r.json()
    assert body["id"] and body["summary"]
    # no Ollama in tests: stored anyway, flagged degraded
    assert body["degraded"] is True
    m = c.get(f"/api/ui/memories/{body['id']}").json()
    assert m["importance"] == 4 and m["status"] == "active"
    # duplicate content+project short-circuits
    r2 = c.post("/api/ui/edit/notes",
                json={"content": "a fresh thought", "project": "proj-a"})
    assert r2.json().get("duplicate") is True


def test_add_note_validations(edit_client):
    c, _ = edit_client
    assert c.post("/api/ui/edit/notes",
                  json={"content": "x", "project": "proj-a",
                        "type": "session"}).status_code == 422  # not editable type
    assert c.post("/api/ui/edit/notes",
                  json={"content": "", "project": "proj-a"}).status_code == 422


def test_patch_memory_fields(edit_client):
    c, db = edit_client
    r = c.patch("/api/ui/edit/memories/m1",
                json={"summary": "new summary", "importance": 5,
                      "tags": ["a", "b"], "type": "fact"})
    assert r.status_code == 200
    m = c.get("/api/ui/memories/m1").json()
    assert (m["summary"], m["importance"], m["type"]) == ("new summary", 5, "fact")
    assert m["tags"] == ["a", "b"]
    # guardrails
    assert c.patch("/api/ui/edit/memories/m1", json={}).status_code == 422
    assert c.patch("/api/ui/edit/memories/m1",
                   json={"type": "bogus"}).status_code == 422
    assert c.patch("/api/ui/edit/memories/m1",
                   json={"project": "ghost"}).status_code == 422
    assert c.patch("/api/ui/edit/memories/nope",
                   json={"summary": "x"}).status_code == 404


def test_archive_and_restore(edit_client):
    c, _ = edit_client
    assert c.post("/api/ui/edit/memories/m1/archive").status_code == 200
    assert c.get("/api/ui/memories/m1").json()["status"] == "archived"
    assert c.patch("/api/ui/edit/memories/m1",
                   json={"status": "active"}).status_code == 200
    assert c.get("/api/ui/memories/m1").json()["status"] == "active"


def test_hard_delete_requires_typed_confirmation(edit_client):
    c, db = edit_client
    r = c.request("DELETE", "/api/ui/edit/memories/m1", json={"confirm": "wrong"})
    assert r.status_code == 400
    assert get_memory("m1", db_path=db) is not None       # still there
    r = c.request("DELETE", "/api/ui/edit/memories/m1", json={"confirm": "m1"[:8]})
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert c.get("/api/ui/memories/m1").status_code == 404


# --------------------------------------------------------- auth posture

def test_edit_endpoints_enforce_api_key(edit_client, monkeypatch):
    c, _ = edit_client
    monkeypatch.setattr("app.auth._API_KEY", "sekrit")
    # writes: locked without the key
    assert c.post("/api/ui/edit/projects",
                  json={"slug": "x1", "name": "X"}).status_code == 401
    assert c.post("/api/ui/edit/memories/m1/archive").status_code == 401
    # writes: open with it
    r = c.post("/api/ui/edit/projects", json={"slug": "x1", "name": "X"},
               headers={"X-Brain-Key": "sekrit"})
    assert r.status_code == 201
    # reads: still bypassed, exactly as before
    assert c.get("/api/ui/stats").status_code == 200
    assert c.get("/ui").status_code == 200
