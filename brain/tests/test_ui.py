# tests/test_ui.py
"""MemoryBrain Atlas UI: pages render, JSON endpoint shapes, redirects,
diagnostics, read-only guarantee, auth bypass, empty-DB behaviour."""
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
    rows = [("a1", "proj-a", "note"), ("b1", "proj-a", "session"),
            ("c1", "proj-b", "fact"), ("d1", "proj-a", "session"),
            ("e1", "proj-a", "handover")]
    for id_, proj, type_ in rows:
        add_memory(MemoryEntry(id=id_, content=f"full content {id_} about hybrid search",
                               summary=f"summary {id_}", type=type_, project=proj,
                               tags=["fts5"]), db_path=db)
    _write_edges([
        {"src": "a1", "dst": "b1", "kind": "semantic", "weight": 0.7, "directed": 0, "meta": {}},
        {"src": "c1", "dst": "a1", "kind": "reference", "weight": 1.0, "directed": 1,
         "meta": {"reason": "supersedes"}},
        {"src": "b1", "dst": "d1", "kind": "session_chain", "weight": 1.0, "directed": 1, "meta": {}},
        {"src": "d1", "dst": "e1", "kind": "session_chain", "weight": 1.0, "directed": 1, "meta": {}},
    ], db)


@pytest.fixture
def _patched(tmp_db, monkeypatch):
    monkeypatch.setenv("MEMORYBRAIN_VECTOR_BACKEND", "sqlite_vec")
    patches = [
        patch("app.ui.queries.DB_PATH", tmp_db),
        patch("app.graph_queries.DB_PATH", tmp_db),
        patch("app.storage.DB_PATH", tmp_db),
        patch("app.main.DB_PATH", tmp_db),
        patch("app.search.DB_PATH", tmp_db),
    ]
    for p in patches:
        p.start()
    yield tmp_db
    for p in patches:
        p.stop()


@pytest.fixture
def ui_client(_patched):
    _seed(_patched)
    return TestClient(app)


@pytest.fixture
def empty_client(_patched):
    """Fresh schema, zero rows: every page must still render sensibly."""
    return TestClient(app)


# ---------------------------------------------------------------- pages

def test_atlas_shell_renders(ui_client):
    r = ui_client.get("/ui")
    assert r.status_code == 200
    # rail + lens tabs + stream rows + footer stamp
    assert "Proj A" in r.text and "Proj B" in r.text
    assert "Constellation" in r.text and "Chronicle" in r.text
    assert "summary a1" in r.text
    assert "buildfoot" in r.text


def test_atlas_project_filter_and_404(ui_client):
    r = ui_client.get("/ui?project=proj-a")
    assert r.status_code == 200
    assert "summary a1" in r.text and "summary c1" not in r.text
    assert ui_client.get("/ui?project=nope").status_code == 404


def test_atlas_type_and_importance_filters(ui_client):
    r = ui_client.get("/ui?type=session")
    assert r.status_code == 200
    assert "summary b1" in r.text and "summary a1" not in r.text


def test_memory_page_shows_related_and_backlinks(ui_client):
    r = ui_client.get("/ui/memory/a1")
    assert r.status_code == 200
    assert "summary c1" in r.text          # c1 -> a1 backlink
    assert "Backlinks" in r.text
    assert ui_client.get("/ui/memory/nope").status_code == 404


def test_search_page_renders(ui_client):
    assert ui_client.get("/ui/search?q=hybrid").status_code == 200
    assert ui_client.get("/ui/search").status_code == 200


def test_legacy_urls_redirect(ui_client):
    r = ui_client.get("/ui/graph", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/ui?lens=constellation"
    r = ui_client.get("/ui/project/proj-a", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/ui?project=proj-a"


# ------------------------------------------------------- JSON endpoints

def test_api_stats_shape(ui_client):
    s = ui_client.get("/api/ui/stats").json()
    assert {"total", "by_type", "projects", "edges"} <= set(s)
    assert s["total"] == 5


def test_api_graph_shape(ui_client):
    g = ui_client.get("/api/ui/graph?min_weight=0.1").json()
    assert {n["id"] for n in g["nodes"]} >= {"a1", "b1", "c1"}
    assert g["edges"] and "truncated" in g


def test_api_related_shape(ui_client):
    r = ui_client.get("/api/ui/memories/a1/related?min_weight=0.1").json()
    assert r["memory_id"] == "a1" and r["related"]
    assert ui_client.get("/api/ui/memories/nope/related").status_code == 404


def test_api_search_falls_back_to_keyword_when_provider_down(ui_client):
    # no Ollama in tests -> hybrid raises -> keyword fallback must still work
    r = ui_client.get("/api/ui/search?q=hybrid").json()
    assert r["mode"] in ("hybrid", "keyword")
    assert r["results"]


def test_api_memory_detail(ui_client):
    m = ui_client.get("/api/ui/memories/a1").json()
    assert m["id"] == "a1" and m["type"] == "note" and m["tags"] == ["fts5"]
    assert "content" in m
    assert ui_client.get("/api/ui/memories/nope").status_code == 404


def test_api_stream_shape_and_cursor(ui_client):
    s = ui_client.get("/api/ui/stream?limit=2").json()
    assert "days" in s and "next_before" in s
    got = [m["id"] for d in s["days"] for m in d["items"]]
    assert len(got) == 2 and s["next_before"]
    s2 = ui_client.get(f"/api/ui/stream?before={s['next_before']}&limit=10").json()
    got2 = [m["id"] for d in s2["days"] for m in d["items"]]
    assert not set(got) & set(got2)          # cursor never repeats rows
    assert len(got) + len(got2) == 5


def test_api_chronicle_shape(ui_client):
    c = ui_client.get("/api/ui/chronicle").json()
    assert "lanes" in c and "links" in c
    lane_a = next(l for l in c["lanes"] if l["project"] == "proj-a")
    ids = {m["id"] for m in lane_a["items"]}
    assert ids == {"b1", "d1", "e1"}          # sessions + handovers only
    assert {"src": "b1", "dst": "d1"} in c["links"]
    filtered = ui_client.get("/api/ui/chronicle?project=proj-b").json()
    assert all(l["project"] == "proj-b" for l in filtered["lanes"])


# ---------------------------------------------- diagnostics (rebuild protocol)

def test_version_endpoint(ui_client):
    r = ui_client.get("/api/ui/version")
    assert r.status_code == 200
    build = r.json()["build"]
    assert isinstance(build, str) and build          # "dev" outside a container
    assert "no-store" in r.headers["cache-control"]  # stamp must always be live


def test_doctor_page_dependency_free(ui_client):
    r = ui_client.get("/ui/doctor")
    assert r.status_code == 200
    assert "MemoryBrain Doctor" in r.text
    assert "no-store" in r.headers["cache-control"]
    build = ui_client.get("/api/ui/version").json()["build"]
    assert f"<b id=\"stamp\">{build}</b>" in r.text
    assert "/static/" not in r.text  # zero external assets, by design
    assert "__BUILD__" not in r.text


def test_doctor_has_workspace_checks_and_stays_dependency_free(ui_client):
    html = ui_client.get("/ui/doctor").text
    assert "/api/ui/workspace/map" in html and "workspace: last scan" in html
    assert "workspace: dangling references" in html
    assert "/static/" not in html and "<link" not in html          # still no external assets


def test_pages_carry_build_stamp(ui_client):
    r = ui_client.get("/ui")
    build = ui_client.get("/api/ui/version").json()["build"]
    assert f"?b={build}" in r.text     # assets cache-bust on the stamp
    assert "buildfoot" in r.text       # footer stamp visible


# ------------------------------------------------------ security posture

def test_ui_bypasses_api_key_auth(ui_client, monkeypatch):
    # PureASGIAuthMiddleware (app/main.py) reads BRAIN_API_KEY from the
    # environment on every request; it never reads app.auth._API_KEY.
    monkeypatch.setenv("BRAIN_API_KEY", "sekrit")
    assert ui_client.get("/ui").status_code == 200
    assert ui_client.get("/ui/doctor").status_code == 200
    assert ui_client.get("/api/ui/stats").status_code == 200
    assert ui_client.get("/api/ui/version").status_code == 200
    assert ui_client.get("/status").status_code == 401   # real API still guarded


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
    for path in ("/static/css/atlas.css", "/static/js/atlas.js",
                 "/static/js/nebula.js",
                 "/static/js/constellation.js", "/static/js/chronicle.js",
                 "/static/js/editor.js", "/static/js/familiar.js",
                 "/static/vendor/force-graph.min.js",
                 "/static/vendor/three.module.min.js",
                 "/static/vendor/OrbitControls.js",
                 "/static/vendor/d3-force-3d.bundle.min.js"):
        assert ui_client.get(path).status_code == 200, path


def test_world_and_importmap_present(ui_client):
    """The Nebula: one full-screen scene host + the import map that resolves
    the single modern three build (no build pipeline, no CDN)."""
    r = ui_client.get("/ui")
    assert 'id="world"' in r.text
    assert "importmap" in r.text
    assert "/static/vendor/three.module.min.js" in r.text
    assert "/static/js/nebula.js" in r.text
    # retired eras must stay retired
    assert "codex.js" not in r.text
    assert "3d-force-graph.min.js" not in r.text
    assert "parchment" not in r.text.lower()


# ------------------------------------------------------------- empty DB

def test_empty_db_pages_render(empty_client):
    r = empty_client.get("/ui")
    assert r.status_code == 200 and "Nothing here yet" in r.text
    assert empty_client.get("/ui/search?q=whatever").status_code == 200
    assert empty_client.get("/ui/doctor").status_code == 200


def test_empty_db_json_shapes(empty_client):
    assert empty_client.get("/api/ui/stream").json() == {"days": [], "next_before": None}
    assert empty_client.get("/api/ui/chronicle").json() == {"lanes": [], "links": []}
    g = empty_client.get("/api/ui/graph").json()
    assert g["nodes"] == [] and g["edges"] == []
    assert empty_client.get("/api/ui/stats").json()["total"] == 0


# ------------------------------------------------ v2.5 workspace layer in the UI

def _seed_ws(db):
    from app.workspace import store as ws
    from app.workspace.resolve import write_file_links
    ws.apply_manifest({"machine": "m", "root_id": "git", "abs_path": r"C:\work\repos", "full": True,
                       "files": [{"rel_path": "Proj A/TODO.md", "sha256": "a", "title": "To-do", "mtime": "2026-09-05T00:00:00Z"},
                                 {"rel_path": "Proj A/docs/glossary.md", "sha256": "b"},
                                 {"rel_path": "Proj A/docs/deep/x.md", "sha256": "c"},
                                 {"rel_path": "Loose/notes.txt", "sha256": "d"}], "markers": []}, db_path=db)
    ws.bind_folder("git", "Proj A", "proj-a", "marker", db_path=db)
    ws.record_folder_seen("git", "Loose", "", db_path=db)
    todo = ws.get_file(db, root_id="git", rel_path="Proj A/TODO.md")["file_id"]
    write_file_links([
        {"src_kind": "memory", "src_id": "a1", "dst_kind": "file", "dst_id": todo, "kind": "file_ref",
         "weight": 1.0, "meta": {"how": "exact", "token": "TODO.md"}},
        {"src_kind": "memory", "src_id": "b1", "dst_kind": "dangling", "dst_id": "gone/old.md", "kind": "file_ref",
         "weight": 0.1, "meta": {"how": "dangling", "token": "gone/old.md", "candidates": ["Proj A/old.md"]}},
    ], db)
    ws.update_ref_degrees({todo}, db)
    return todo


def test_stats_carry_file_counts_and_footer_renders_them(ui_client, tmp_db):
    _seed_ws(tmp_db)
    s = ui_client.get("/api/ui/stats").json()
    assert s["files"] == 4 and s["file_edges"] == 1
    html = ui_client.get("/ui").text
    assert "4 files" in html and "1 file links" in html


def test_workspace_json_routes(ui_client, tmp_db):
    todo = _seed_ws(tmp_db)
    m = ui_client.get("/api/ui/workspace/map").json()
    assert {"roots", "projects", "unmapped_folders", "last_scan"} <= set(m)
    t = ui_client.get("/api/ui/workspace/tree").json()
    pa = next(p for p in t["projects"] if p["slug"] == "proj-a")
    assert pa["file_count"] == 3
    f0 = pa["folders"][0]
    assert f0["rel_path"] == "Proj A" and f0["file_count"] == 3
    assert f0["children"] == [{"name": "docs", "rel_path": "Proj A/docs", "file_count": 2}]
    assert [u["rel_path"] for u in t["unmapped"]] == ["Loose"]
    fl = ui_client.get("/api/ui/workspace/files", params={"project": "proj-a", "sort": "ref_degree"}).json()
    assert fl["count"] == 3 and fl["files"][0]["rel_path"] == "Proj A/TODO.md" and fl["max_ref_degree"] > 0
    fl = ui_client.get("/api/ui/workspace/files", params={"project": "proj-a", "folder": "Proj A/docs"}).json()
    assert fl["count"] == 2
    fc = ui_client.get(f"/api/ui/workspace/file/{todo}").json()
    assert fc["project"] == "proj-a" and fc["memories"][0]["id"] == "a1" and fc["memories"][0]["how"] == "exact"
    assert ui_client.get("/api/ui/workspace/file/nope").status_code == 404
    d = ui_client.get("/api/ui/workspace/dangling", params={"project": "proj-a"}).json()
    assert d["count"] == 1 and d["items"][0] == {"token": "gone/old.md", "mentions": 1,
                                                 "candidates": ["Proj A/old.md"], "memory_ids": ["b1"]}
    assert ui_client.get("/api/ui/workspace/dangling", params={"project": "proj-b"}).json()["count"] == 0


def test_files_lens_is_a_valid_lens(ui_client):
    r = ui_client.get("/ui", params={"lens": "files", "project": "proj-a", "folder": "Proj A/docs"})
    assert r.status_code == 200
    assert 'id="pane-files"' in r.text and 'data-lens="files"' in r.text
    assert 'data-folder="Proj A/docs"' in r.text
    r = ui_client.get("/ui", params={"lens": "bogus"})
    assert r.status_code == 200 and 'id="pane-stream" class="pane on"' in r.text


def test_file_page_renders_and_404s(ui_client, tmp_db):
    todo = _seed_ws(tmp_db)
    r = ui_client.get(f"/ui/file/{todo}")
    assert r.status_code == 200
    assert "Proj A/TODO.md" in r.text and "To-do" in r.text
    assert 'href="/ui?project=proj-a"' in r.text                    # project crumb
    assert 'data-id="a1"' in r.text and "exact" in r.text            # referencing memory row + how
    assert "none yet" in r.text                                       # links / touched placeholders
    assert ui_client.get("/ui/file/does-not-exist").status_code == 404
