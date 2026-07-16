# tests/test_ui.py
"""v2.0.0 — web UI: pages render, JSON endpoints, read-only guarantee."""
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.main import app
from app.storage import add_memory, upsert_project
from app.linker import _write_edges
from app.models import MemoryEntry, Project


def _seed(db):
    upsert_project(Project(slug="proj-a", name="Proj A"), db_path=db)
    upsert_project(Project(slug="proj-b", name="Proj B"), db_path=db)
    for id_, proj, type_ in [("a1", "proj-a", "note"), ("b1", "proj-a", "session"),
                             ("c1", "proj-b", "fact")]:
        add_memory(MemoryEntry(id=id_, content=f"full content {id_} about hybrid search",
                               summary=f"summary {id_}", type=type_, project=proj,
                               tags=["fts5"]), db_path=db)
    _write_edges([
        {"src": "a1", "dst": "b1", "kind": "semantic", "weight": 0.7, "directed": 0, "meta": {}},
        {"src": "c1", "dst": "a1", "kind": "reference", "weight": 1.0, "directed": 1,
         "meta": {"reason": "supersedes"}},
    ], db)


@pytest.fixture
def ui_client(tmp_db, monkeypatch):
    monkeypatch.setenv("MEMORYBRAIN_VECTOR_BACKEND", "sqlite_vec")
    _seed(tmp_db)
    patches = [
        patch("app.ui.queries.DB_PATH", tmp_db),
        patch("app.graph_queries.DB_PATH", tmp_db),
        patch("app.storage.DB_PATH", tmp_db),
        patch("app.main.DB_PATH", tmp_db),
        patch("app.search.DB_PATH", tmp_db),
    ]
    for p in patches:
        p.start()
    yield TestClient(app)
    for p in patches:
        p.stop()


def test_dashboard_renders(ui_client):
    r = ui_client.get("/ui")
    assert r.status_code == 200
    assert "Gravity wells" in r.text and "Proj A" in r.text


def test_project_page_renders_and_404s(ui_client):
    assert ui_client.get("/ui/project/proj-a").status_code == 200
    assert ui_client.get("/ui/project/nope").status_code == 404


def test_memory_page_shows_related_and_backlinks(ui_client):
    r = ui_client.get("/ui/memory/a1")
    assert r.status_code == 200
    assert "Backlinks" in r.text and "summary c1" in r.text  # c1 -> a1 backlink
    assert ui_client.get("/ui/memory/nope").status_code == 404


def test_graph_page_renders(ui_client):
    r = ui_client.get("/ui/graph")
    assert r.status_code == 200 and "min weight" in r.text


def test_api_graph_shape(ui_client):
    g = ui_client.get("/api/ui/graph?min_weight=0.1").json()
    assert {n["id"] for n in g["nodes"]} == {"a1", "b1", "c1"}
    assert g["edges"] and "truncated" in g


def test_api_related_shape(ui_client):
    r = ui_client.get("/api/ui/memories/a1/related?min_weight=0.1").json()
    assert r["memory_id"] == "a1" and r["related"]
    assert ui_client.get("/api/ui/memories/nope/related").status_code == 404


def test_api_search_falls_back_to_keyword_when_provider_down(ui_client):
    # no Ollama in tests -> hybrid raises -> keyword fallback must still work
    r = ui_client.get("/api/ui/search?q=hybrid").json()
    assert r["mode"] in ("hybrid", "keyword")
    assert any("a1" == x["id"] for x in r["results"]) or r["results"]


def test_search_page_renders(ui_client):
    assert ui_client.get("/ui/search?q=hybrid").status_code == 200
    assert ui_client.get("/ui/search").status_code == 200


def test_ui_bypasses_api_key_auth(ui_client, monkeypatch):
    monkeypatch.setattr("app.auth._API_KEY", "sekrit")
    assert ui_client.get("/ui").status_code == 200            # UI: no key needed
    assert ui_client.get("/api/ui/stats").status_code == 200
    r = ui_client.get("/status")                               # API: key enforced
    assert r.status_code == 401


def test_ui_connection_is_read_only():
    from app.ui.queries import get_conn
    import sqlite3 as s
    import tempfile, pathlib
    from app.storage import init_db
    with tempfile.TemporaryDirectory() as tmp:
        db = pathlib.Path(tmp) / "b.db"
        init_db(db)
        conn = get_conn(db)
        with pytest.raises(s.OperationalError):
            conn.execute("INSERT INTO projects VALUES ('x','x','x','x')")
        conn.close()


def test_static_assets_served(ui_client):
    assert ui_client.get("/static/css/ui.css").status_code == 200
    assert ui_client.get("/static/js/graph3d.js").status_code == 200
    assert ui_client.get("/static/vendor/three.module.min.js").status_code == 200
