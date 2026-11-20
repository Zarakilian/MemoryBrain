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
    # environment on every request.
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


# ----------------------------------------------- 3.3 in Atlas: standing, brief

def _seed_33(db):
    """One project with a little history: an old fact, a loop gone stale, a
    fresh loop, a fact that was corrected, one that was replaced, a how-to."""
    from datetime import datetime, timedelta, timezone
    from app import lifecycle
    upsert_project(Project(slug="proj-s", name="Proj S"), db_path=db)
    old = datetime.now(timezone.utc) - timedelta(days=45)
    now = datetime.now(timezone.utc)
    rows = [
        ("aaaaaaaa-0001", "fact", "the port is 7741", old, []),
        ("aaaaaaaa-0002", "open_loop", "rotate the key", old, []),
        ("aaaaaaaa-0003", "open_loop", "write the runbook", now, []),
        ("aaaaaaaa-0004", "fact", "the timeout is 30 seconds", old, []),
        ("aaaaaaaa-0005", "fact", "the timeout is 60 seconds", now, []),
        ("aaaaaaaa-0006", "fact", "the old host name", old, []),
        ("aaaaaaaa-0007", "fact", "the new host name", now, []),
        ("aaaaaaaa-0008", "note", "how to restart it", now, ["howto"]),
        ("aaaaaaaa-0009", "session", "session: fixed the timeout", now, []),
    ]
    for id_, type_, text, ts, tags in rows:
        add_memory(MemoryEntry(id=id_, content=f"{text} (full text)", summary=text, type=type_,
                               project="proj-s", tags=tags, timestamp=ts), db_path=db)
    lifecycle.correct("aaaaaaaa-0004", "aaaaaaaa-0005", actor="test", db_path=db)
    lifecycle.supersede("aaaaaaaa-0006", "aaaaaaaa-0007", actor="test", db_path=db)


@pytest.fixture
def client_33(_patched):
    _seed_33(_patched)
    return TestClient(app)


def test_stream_rows_carry_standing_and_both_importances(client_33):
    data = client_33.get("/api/ui/stream", params={"project": "proj-s"}).json()
    rows = {m["id"]: m for d in data["days"] for m in d["items"]}
    assert rows["aaaaaaaa-0001"]["standing"] == "proven"
    assert rows["aaaaaaaa-0002"]["standing"] == "stale"
    assert rows["aaaaaaaa-0003"]["standing"] == "open"
    assert rows["aaaaaaaa-0007"]["standing"] == "changed"
    disputed = rows["aaaaaaaa-0004"]
    assert disputed["standing"] == "disputed" and disputed["corrected_by"] == "aaaaaaaa-0005"
    # importance stays the stored one; importance_now is what it counts for today
    assert disputed["importance_now"] < disputed["importance"]
    assert rows["aaaaaaaa-0007"]["importance_now"] > rows["aaaaaaaa-0007"]["importance"]
    # the replaced fact is archived: it has left the stream
    assert "aaaaaaaa-0006" not in rows


def test_stream_page_shows_the_badges_and_the_moved_importance(client_33):
    html = client_33.get("/ui", params={"project": "proj-s"}).text
    for badge in ("s-proven", "s-stale", "s-disputed", "s-changed"):
        assert f'class="standing {badge}"' in html
    assert "today, stored as" in html
    assert 'class="imp down"' in html and 'class="imp up"' in html
    # an ordinary memory gets no badge at all
    assert 's-current' not in html and 's-open"' not in html


def test_graph_nodes_carry_standing_for_the_constellation(client_33):
    g = client_33.get("/api/ui/graph", params={"project": "proj-s", "min_weight": 0}).json()
    by_id = {n["id"]: n for n in g["nodes"]}
    assert by_id["aaaaaaaa-0004"]["standing"] == "disputed"
    assert by_id["aaaaaaaa-0002"]["standing"] == "stale"
    assert "importance_now" in by_id["aaaaaaaa-0001"]


def test_memory_detail_names_who_corrected_and_replaced_it(client_33):
    m = client_33.get("/api/ui/memories/aaaaaaaa-0004").json()
    assert m["standing"] == "disputed"
    assert m["lineage"]["corrected_by"]["id"] == "aaaaaaaa-0005"
    fixer = client_33.get("/api/ui/memories/aaaaaaaa-0005").json()
    assert [o["id"] for o in fixer["lineage"]["corrects"]] == ["aaaaaaaa-0004"]
    gone = client_33.get("/api/ui/memories/aaaaaaaa-0006").json()
    assert gone["standing"] == "superseded"
    assert gone["lineage"]["superseded_by"]["id"] == "aaaaaaaa-0007"
    new = client_33.get("/api/ui/memories/aaaaaaaa-0007").json()
    assert [o["id"] for o in new["lineage"]["replaces"]] == ["aaaaaaaa-0006"]


def test_memory_page_links_a_disputed_memory_to_its_correction(client_33):
    html = client_33.get("/ui/memory/aaaaaaaa-0004").text
    assert 'class="standing s-disputed"' in html
    assert "Corrected by" in html and 'href="/ui/memory/aaaaaaaa-0005"' in html
    html = client_33.get("/ui/memory/aaaaaaaa-0006").text
    assert "Replaced by" in html and 'href="/ui/memory/aaaaaaaa-0007"' in html
    assert 'class="standing s-superseded"' in html


def test_related_rows_carry_standing(ui_client):
    rel = ui_client.get("/api/ui/memories/a1/related", params={"min_weight": 0}).json()
    assert rel["related"] and all("standing" in r for r in rel["related"])


def test_project_brief_json(client_33):
    b = client_33.get("/api/ui/brief", params={"project": "proj-s"}).json()
    assert [m["id"] for m in b["where_we_left_off"]] == ["aaaaaaaa-0009"]
    kinds = {c["kind"]: c for c in b["changes"]}
    assert kinds["replaced"]["id"] == "aaaaaaaa-0007"
    assert kinds["replaced"]["replaced"]["id"] == "aaaaaaaa-0006"
    assert kinds["corrected"]["disputes"]["id"] == "aaaaaaaa-0004"
    loops = b["open_loops"]
    # fresh loops first, stale ones last and marked
    assert [m["id"] for m in loops] == ["aaaaaaaa-0003", "aaaaaaaa-0002"]
    assert loops[1]["stale"] is True and loops[1]["standing"] == "stale"
    assert b["open_loop_count"] == 2
    assert [m["id"] for m in b["howtos"]] == ["aaaaaaaa-0008"]
    assert b["waiting_on_you"] == {"rules": 0, "beliefs": 0, "conflicts": 0, "stale_loops": 1}
    assert b["dormant"] is False and b["last_activity"]["days_ago"] == 0


def test_project_brief_unknown_project_is_404(client_33):
    assert client_33.get("/api/ui/brief", params={"project": "nope"}).status_code == 404


def test_project_page_opens_with_the_brief(client_33):
    html = client_33.get("/ui", params={"project": "proj-s"}).text
    assert "Where Proj S stands" in html
    for heading in ("Where we left off", "What changed", "Open loops", "Facts and decisions",
                    "How-tos", "Waiting on you"):
        assert heading in html
    assert "1 stale loop" in html
    assert "session: fixed the timeout" in html
    # the loop gone stale is marked; the correction is named with what it disputes
    assert "corrects part of:" in html and "replaced:" in html


def test_all_memories_page_has_no_brief(client_33):
    html = client_33.get("/ui").text
    assert 'class="brief"' not in html


def test_brief_is_left_out_of_older_pages_of_the_stream(client_33):
    html = client_33.get("/ui", params={"project": "proj-s", "before": "2999-01-01"}).text
    assert 'class="brief"' not in html


def test_empty_project_brief_says_so(_patched):
    upsert_project(Project(slug="bare", name="Bare"), db_path=_patched)
    c = TestClient(app)
    html = c.get("/ui", params={"project": "bare"}).text
    assert "Where Bare stands" in html
    assert "No session log yet." in html and "No open loops." in html
    assert "Waiting on you" not in html


def test_a_summary_the_brain_made_is_marked(_patched):
    from app.storage import _connect
    upsert_project(Project(slug="proj-m", name="Proj M"), db_path=_patched)
    add_memory(MemoryEntry(id="bbbbbbbb-0001", content="text one", summary="made by the brain",
                           type="note", project="proj-m", summary_source="extract"),
               db_path=_patched)
    add_memory(MemoryEntry(id="bbbbbbbb-0002", content="text two", summary="written by its author",
                           type="note", project="proj-m", summary_source="writer"),
               db_path=_patched)
    c = TestClient(app)
    rows = {m["id"]: m for d in c.get("/api/ui/stream").json()["days"] for m in d["items"]}
    assert rows["bbbbbbbb-0001"]["summary_source"] == "extract"
    html = c.get("/ui", params={"project": "proj-m"}).text
    assert html.count('class="auto-sum"') == 1
    assert "not written by the author" in c.get("/ui/memory/bbbbbbbb-0001").text
    assert "not written by the author" not in c.get("/ui/memory/bbbbbbbb-0002").text


def test_search_results_carry_standing(client_33):
    with patch("app.ui.routes.hybrid_search", side_effect=Exception("provider down")):
        data = client_33.get("/api/ui/search", params={"q": "timeout"}).json()
    by_id = {m["id"]: m for m in data["results"]}
    assert by_id["aaaaaaaa-0004"]["standing"] == "disputed"


# ------------------------------- 3.3 in Atlas: cleanup lists, names, the Doctor

LONG_BODY = ("Moved the nightly export to the new queue. " * 12).strip()        # over 400 characters
MODEL_SUMMARY = ("The team moved the export to another queue on March 2, 2026, with several "
                 "follow-up steps that were completed afterwards by everyone involved. " * 2).strip()


def _seed_cleanup(db):
    """What the three cleanup lists read: a summary the model wrote, a note
    that says in prose it supersedes another, and a loop gone stale. Each id
    has its own first 8 characters, the way prose quotes an id."""
    from datetime import datetime, timedelta, timezone
    upsert_project(Project(slug="proj-c", name="Proj C"), db_path=db)
    old = datetime.now(timezone.utc) - timedelta(days=45)
    now = datetime.now(timezone.utc)
    rows = [
        ("e1e1e1e1-0001", "session", LONG_BODY, MODEL_SUMMARY, "model", old),
        ("e2e2e2e2-0001", "session", LONG_BODY + " Second run.", "Export moved; queue renamed",
         "writer", old),
        ("e3e3e3e3-0001", "fact", "the port is 7741", "the port is 7741", "writer", old),
        ("e4e4e4e4-0001", "fact", "Supersedes e3e3e3e3: the port is 7742 now", "the port is 7742",
         "writer", now),
        ("e5e5e5e5-0001", "open_loop", "rotate the key", "rotate the key", "writer", old),
        ("e6e6e6e6-0001", "open_loop", "write the runbook", "write the runbook", "writer", now),
    ]
    for id_, type_, content, summary, source, ts in rows:
        add_memory(MemoryEntry(id=id_, content=content, summary=summary, type=type_,
                               project="proj-c", summary_source=source, timestamp=ts), db_path=db)


@pytest.fixture
def cleanup_client(_patched, monkeypatch):
    monkeypatch.setattr("app.mcp.tools.DB_PATH", _patched)     # the open-loops list reads it
    _seed_cleanup(_patched)
    return TestClient(app)


def test_cleanup_summaries_is_a_dry_run_that_changes_nothing(cleanup_client, _patched):
    from app.storage import get_memory
    data = cleanup_client.get("/api/ui/cleanup/summaries").json()
    assert data["dry_run"] is True and data["would_change"] == 1
    example = data["examples"][0]
    assert example["id"] == "e1e1e1e1-0001" and example["old"] == MODEL_SUMMARY
    assert example["new"].startswith("Moved the nightly export")
    # the writer's own line is left alone, and nothing was written
    assert get_memory("e1e1e1e1-0001", db_path=_patched).summary == MODEL_SUMMARY
    # one project only
    assert cleanup_client.get("/api/ui/cleanup/summaries",
                              params={"project": "proj-a"}).json()["would_change"] == 0
    assert cleanup_client.get("/api/ui/cleanup/summaries",
                              params={"project": "proj-c"}).json()["would_change"] == 1


def test_cleanup_corrections_lists_the_pairs_stated_in_prose(cleanup_client):
    data = cleanup_client.get("/api/ui/cleanup/corrections").json()
    assert data["total"] == 1
    pair = data["pairs"][0]
    assert (pair["new_id"], pair["old_id"]) == ("e4e4e4e4-0001", "e3e3e3e3-0001")
    assert pair["says"] == "supersedes" and pair["old_type"] == "fact"
    assert pair["project"] == "proj-c" and pair["partial"] is False
    assert cleanup_client.get("/api/ui/cleanup/corrections",
                              params={"project": "proj-a"}).json()["total"] == 0
    assert cleanup_client.get("/api/ui/cleanup/corrections",
                              params={"limit": 0}).status_code == 422


def test_cleanup_loops_lists_stale_loops_oldest_first(cleanup_client):
    data = cleanup_client.get("/api/ui/cleanup/loops").json()
    assert data["total"] == 1 and "close_with" not in data
    loop = data["open_loops"][0]
    assert loop["id"] == "e5e5e5e5-0001" and loop["stale"] is True and loop["age_days"] >= 44
    every = cleanup_client.get("/api/ui/cleanup/loops", params={"stale_only": "false"}).json()
    assert [item["id"] for item in every["open_loops"]] == ["e5e5e5e5-0001", "e6e6e6e6-0001"]
    assert cleanup_client.get("/api/ui/cleanup/loops",
                              params={"project": "proj-a"}).json()["total"] == 0
    assert cleanup_client.get("/api/ui/cleanup/loops", params={"limit": 500}).status_code == 422


def test_cleanup_dry_runs_stay_open_when_a_key_is_set(cleanup_client, monkeypatch):
    monkeypatch.setenv("BRAIN_API_KEY", "sekrit")
    for path in ("summaries", "corrections", "loops"):
        assert cleanup_client.get(f"/api/ui/cleanup/{path}").status_code == 200


def test_cleanup_lists_are_empty_on_an_empty_brain(empty_client, _patched, monkeypatch):
    monkeypatch.setattr("app.mcp.tools.DB_PATH", _patched)
    assert empty_client.get("/api/ui/cleanup/summaries").json()["would_change"] == 0
    assert empty_client.get("/api/ui/cleanup/corrections").json() == {"total": 0, "pairs": []}
    assert empty_client.get("/api/ui/cleanup/loops").json() == {"total": 0, "open_loops": []}


def test_doctor_shows_the_file_index_age_and_the_cleanup_lists(ui_client):
    html = ui_client.get("/ui/doctor").text
    # the age of the file index comes from /status, and says what to run
    assert '"/status"' in html and "scan_age_hours" in html and "brain scan" in html
    # three dry runs that read, one apply that writes, the verdicts for the rest
    for url in ("/api/ui/cleanup/summaries", "/api/ui/cleanup/corrections",
                "/api/ui/cleanup/loops", "/api/ui/edit/cleanup/resummarise"):
        assert url in html
    assert '"supersede"' in html and '"correct"' in html and '"close"' in html
    assert "Nothing changes until you" in html
    # the name pages are linked from here
    assert 'href="/ui/names"' in html and 'action="/ui/names"' in html
    assert "/static/" not in html and "<link" not in html          # still no external assets


def _seed_names(db):
    """A made-up ticket three memories mention: a fact that has held, an open
    loop, and a note that was replaced. They are in the name index; a fourth
    memory holds a word the index does not know."""
    from datetime import datetime, timedelta, timezone
    from app import lifecycle
    from app.entities import index_entities
    upsert_project(Project(slug="proj-n", name="Proj N"), db_path=db)
    old = datetime.now(timezone.utc) - timedelta(days=45)
    now = datetime.now(timezone.utc)
    rows = [
        ("f1f1f1f1-0001", "fact", "INC0012345 was caused by a full disk", old),
        ("f2f2f2f2-0001", "open_loop", "close INC0012345 with the vendor", now),
        ("f3f3f3f3-0001", "note", "INC0012345 looked like a network fault", old),
        ("f4f4f4f4-0001", "note", "the widget frobnicator needs oil", now),
    ]
    for id_, type_, text, ts in rows:
        add_memory(MemoryEntry(id=id_, content=text, summary=text, type=type_,
                               project="proj-n", timestamp=ts), db_path=db)
        if "INC" in text:
            index_entities(id_, text, db_path=db)
    lifecycle.supersede("f3f3f3f3-0001", "f1f1f1f1-0001", actor="test", db_path=db)


@pytest.fixture
def names_client(_patched):
    _seed(_patched)
    _seed_names(_patched)
    return TestClient(app)


def test_name_page_shows_everything_about_one_name(names_client):
    r = names_client.get("/ui/name/INC0012345")
    assert r.status_code == 200 and "no-store" in r.headers["cache-control"]
    html = r.text
    assert "INC0012345 was caused by a full disk" in html           # a current memory
    assert "close INC0012345 with the vendor" in html                # an open loop
    assert "looked like a network fault" not in html                 # replaced: it is history
    assert "1 older" in html and "memory sits" in html
    assert "the name index" in html and ">ticket<" in html
    # the rows are the shared ones, so standing shows
    assert 'class="standing s-proven"' in html
    assert 'href="/ui/memory/f1f1f1f1-0001"' in html
    assert 'href="/ui/names"' in html


def test_name_page_can_be_kept_to_one_project(names_client):
    html = names_client.get("/ui/name/INC0012345", params={"project": "proj-n"}).text
    assert "in proj-n only" in html and "INC0012345 was caused by a full disk" in html
    other = names_client.get("/ui/name/INC0012345", params={"project": "proj-a"}).text
    assert "Nothing stored mentions" in other and "in proj-a" in other
    assert names_client.get("/ui/name/INC0012345", params={"project": "nope"}).status_code == 404


def test_name_page_falls_back_to_the_text_and_says_when_nothing_is_stored(names_client):
    html = names_client.get("/ui/name/frobnicator").text
    assert "the widget frobnicator needs oil" in html
    assert "a search of the text" in html
    r = names_client.get("/ui/name/never-written-anywhere")
    assert r.status_code == 200 and "Nothing stored mentions" in r.text


def test_name_page_takes_names_with_slashes_and_odd_characters(names_client):
    r = names_client.get("/ui/name/owner%2Frepo%2312")
    assert r.status_code == 200 and "owner/repo#12" in r.text
    assert names_client.get("/ui/name/src/app/run.py").status_code == 200
    for odd in ("a%22b", "(", "*", "x%20AND%20y", "NEAR(", "%3Cb%3Ebold"):
        assert names_client.get("/ui/name/" + odd).status_code == 200
    # a name is text on the page, never markup
    html = names_client.get("/ui/name/%3Cb%3Ebold").text
    assert "<b>bold" not in html and "&lt;b&gt;bold" in html
    assert names_client.get("/ui/name/" + "x" * 201).status_code == 422
    r = names_client.get("/ui/name/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/ui/names"


def test_names_page_lists_patterns_and_the_most_mentioned_names(names_client, _patched):
    from app.entities import add_pattern
    html = names_client.get("/ui/names").text
    assert "No pattern yet." in html
    assert 'href="/ui/name/INC0012345"' in html          # a card from the entity cards
    assert "/static/js/names.js" in html
    add_pattern("server", r"\bsrv-[a-z]+-\d{2}\b", db_path=_patched)
    html = names_client.get("/ui/names").text
    assert r"<code>\bsrv-[a-z]+-\d{2}\b</code>" in html and "No pattern yet." not in html
    assert 'data-kind="server"' in html
    # one project's names, and an unknown project
    html = names_client.get("/ui/names", params={"project": "proj-n"}).text
    assert 'href="/ui/name/INC0012345?project=proj-n"' in html
    assert names_client.get("/ui/names", params={"project": "nope"}).status_code == 404


def test_names_look_up_box_goes_to_the_name_page(names_client):
    r = names_client.get("/ui/names", params={"name": "owner/repo#12"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/ui/name/owner%2Frepo%2312"
    r = names_client.get("/ui/names", params={"name": " INC0012345 ", "project": "proj-n"},
                         follow_redirects=False)
    assert r.headers["location"] == "/ui/name/INC0012345?project=proj-n"
    assert "INC0012345 was caused" in names_client.get("/ui/names",
                                                       params={"name": "INC0012345"}).text


def test_names_pages_render_on_an_empty_brain(empty_client):
    r = empty_client.get("/ui/names")
    assert r.status_code == 200 and "No name is indexed yet." in r.text
    r = empty_client.get("/ui/name/anything")
    assert r.status_code == 200 and "Nothing stored mentions" in r.text


def test_palette_and_footer_know_the_name_pages(ui_client):
    js = ui_client.get("/static/js/atlas.js").text
    assert '"/ui/names"' in js and '"/ui/name/"' in js
    assert 'href="/ui/names"' in ui_client.get("/ui").text
