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
    yield TestClient(app, headers={"X-Brain-Client": "test"}), tmp_db
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


def test_a_note_written_in_atlas_is_the_users_own(edit_client):
    """Atlas is the person's door: what they write there is trust=user, so the
    brief never counts it against the cap on agent-written text."""
    from app.storage import get_memory
    c, db = edit_client
    r = c.post("/api/ui/edit/notes", json={"content": "Invoices go out on the first.",
                                            "project": "proj-a", "type": "fact"})
    stored = get_memory(r.json()["id"], db_path=db)
    assert (stored.trust, stored.writer) == ("user", "ui")


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
    # Pure ASGI auth middleware reads BRAIN_API_KEY from the environment
    # on each request.
    monkeypatch.setenv("BRAIN_API_KEY", "sekrit")
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



def test_an_atlas_edit_leaves_an_audit_row(edit_client):
    from app.db import connect
    c, db = edit_client
    assert c.patch("/api/ui/edit/memories/m1", json={"summary": "better summary"}).status_code == 200
    conn = connect(db)
    try:
        rows = conn.execute("SELECT action, actor, reason FROM memory_audit WHERE memory_id = 'm1'"
                            ).fetchall()
    finally:
        conn.close()
    assert [(r["action"], r["actor"]) for r in rows] == [("edit", "ui")]
    assert "summary" in rows[0]["reason"]


# ------------------------------------------ 3.3 the user's verdicts (lifecycle)

def _audit_rows(db, memory_id):
    from app.storage import _connect
    with _connect(db) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT action, actor FROM memory_audit WHERE memory_id = ? ORDER BY rowid",
            (memory_id,)).fetchall()]


@pytest.fixture
def verdict_client(edit_client):
    c, db = edit_client
    rows = [("cccccccc-0001", "fact", "the port is 7741", "agent"),
            ("cccccccc-0002", "fact", "the port is 7742", "agent"),
            ("cccccccc-0003", "open_loop", "rotate the key", "agent"),
            ("cccccccc-0004", "fact", "written in Atlas by the user", "user"),
            ("cccccccc-0005", "session", "session: rotated the key", "agent")]
    for id_, type_, text, trust in rows:
        add_memory(MemoryEntry(id=id_, content=f"{text} (full text)", summary=text, type=type_,
                               project="proj-a", trust=trust), db_path=db)
    upsert_project(Project(slug="proj-b", name="Proj B"), db_path=db)
    add_memory(MemoryEntry(id="dddddddd-0001", content="another project", summary="elsewhere",
                           type="fact", project="proj-b"), db_path=db)
    return c, db


def test_verify_marks_a_memory_as_checked(verdict_client):
    c, db = verdict_client
    r = c.post("/api/ui/edit/memories/cccccccc-0001/verify", json={"note": "checked the config"})
    assert r.status_code == 200 and r.json()["verified_by"] == "ui"
    m = c.get("/api/ui/memories/cccccccc-0001").json()
    assert m["standing"] == "verified" and m["verified_at"]
    assert m["importance_now"] == m["importance"] + 1
    assert {"action": "verify", "actor": "ui"} in _audit_rows(db, "cccccccc-0001")
    # no body at all is fine too (the button sends an empty one)
    assert c.post("/api/ui/edit/memories/cccccccc-0002/verify").status_code == 200


def test_close_loop_finishes_it_and_reopen_brings_it_back(verdict_client):
    c, db = verdict_client
    r = c.post("/api/ui/edit/memories/cccccccc-0003/close", json={"by": "cccccccc-0005"})
    assert r.status_code == 200 and r.json() == {"id": "cccccccc-0003", "closed": True,
                                                 "status": "done"}
    assert get_memory("cccccccc-0003", db_path=db).status == "done"
    assert c.get("/api/ui/memories/cccccccc-0003").json()["standing"] == "done"
    assert {"action": "close", "actor": "ui"} in _audit_rows(db, "cccccccc-0003")
    # the loop has left the brief's open list and shows under what changed
    brief = c.get("/api/ui/brief", params={"project": "proj-a"}).json()
    assert brief["open_loop_count"] == 0
    assert [ch["kind"] for ch in brief["changes"]] == ["finished"]
    # Reopen is the existing restore
    r = c.patch("/api/ui/edit/memories/cccccccc-0003", json={"status": "active"})
    assert r.status_code == 200
    assert get_memory("cccccccc-0003", db_path=db).status == "active"


def test_close_refuses_what_is_not_an_open_loop(verdict_client):
    c, _ = verdict_client
    r = c.post("/api/ui/edit/memories/cccccccc-0001/close")
    assert r.status_code == 422 and "open loops" in r.json()["detail"]


def test_supersede_archives_the_old_one_and_points_it_at_the_new(verdict_client):
    c, db = verdict_client
    r = c.post("/api/ui/edit/memories/cccccccc-0001/supersede",
               json={"by": "cccccccc-0002", "reason": "the port moved"})
    assert r.status_code == 200 and r.json()["superseded"] == ["cccccccc-0001"]
    old = get_memory("cccccccc-0001", db_path=db)
    assert old.status == "archived" and old.superseded_by == "cccccccc-0002"
    assert c.get("/api/ui/memories/cccccccc-0002").json()["standing"] == "changed"
    assert {"action": "supersede", "actor": "ui"} in _audit_rows(db, "cccccccc-0001")
    # a full id or its first 8 characters both name a memory; a prefix that
    # fits several is refused, and nothing changes
    r = c.post("/api/ui/edit/memories/cccccccc-0004/supersede", json={"by": "cccccccc"})
    assert r.status_code == 422 and "more than one" in r.json()["detail"]
    assert get_memory("cccccccc-0004", db_path=db).status == "active"


def test_atlas_may_retire_what_the_user_wrote_and_an_agent_may_not(verdict_client):
    from app import lifecycle
    c, db = verdict_client
    with pytest.raises(lifecycle.LifecycleError, match="written by the user"):
        lifecycle.supersede("cccccccc-0004", "cccccccc-0002", actor="some-agent", db_path=db)
    assert get_memory("cccccccc-0004", db_path=db).status == "active"
    r = c.post("/api/ui/edit/memories/cccccccc-0004/supersede", json={"by": "cccccccc-0002"})
    assert r.status_code == 200
    assert get_memory("cccccccc-0004", db_path=db).status == "archived"


def test_correct_marks_it_disputed_and_uncorrect_takes_it_back(verdict_client):
    c, db = verdict_client
    r = c.post("/api/ui/edit/memories/cccccccc-0001/correct", json={"by": "cccccccc-0002"})
    assert r.status_code == 200 and r.json()["corrected"] == ["cccccccc-0001"]
    m = c.get("/api/ui/memories/cccccccc-0001").json()
    assert m["status"] == "active" and m["standing"] == "disputed"
    assert m["lineage"]["corrected_by"]["id"] == "cccccccc-0002"
    assert m["importance_now"] < m["importance"]
    r = c.post("/api/ui/edit/memories/cccccccc-0001/uncorrect")
    assert r.status_code == 200 and r.json() == {"id": "cccccccc-0001", "cleared": True,
                                                 "was": "cccccccc-0002"}
    m = c.get("/api/ui/memories/cccccccc-0001").json()
    assert m["standing"] != "disputed" and not m.get("corrected_by")
    actions = [a["action"] for a in _audit_rows(db, "cccccccc-0001")]
    assert actions[-2:] == ["correct", "uncorrect"]
    # taking back a correction that is not there changes nothing
    assert c.post("/api/ui/edit/memories/cccccccc-0001/uncorrect").json()["cleared"] is False


def test_verdicts_refuse_bad_targets(verdict_client):
    c, db = verdict_client
    # the other memory does not exist
    r = c.post("/api/ui/edit/memories/cccccccc-0001/supersede", json={"by": "eeeeeeee-9999"})
    assert r.status_code == 404
    # a memory cannot replace or correct itself
    r = c.post("/api/ui/edit/memories/cccccccc-0001/supersede", json={"by": "cccccccc-0001"})
    assert r.status_code == 422 and "itself" in r.json()["detail"]
    r = c.post("/api/ui/edit/memories/cccccccc-0001/correct", json={"by": "cccccccc-0001"})
    assert r.status_code == 422
    # not across projects
    r = c.post("/api/ui/edit/memories/cccccccc-0001/supersede", json={"by": "dddddddd-0001"})
    assert r.status_code == 422 and "belongs to project" in r.json()["detail"]
    # `by` is required, and must look like an id
    assert c.post("/api/ui/edit/memories/cccccccc-0001/supersede", json={}).status_code == 422
    assert c.post("/api/ui/edit/memories/cccccccc-0001/correct",
                  json={"by": "not an id!"}).status_code == 422
    # an id that is not one (the old test rows use short ids) is refused, not a crash
    assert c.post("/api/ui/edit/memories/m1/verify").status_code == 422
    assert c.post("/api/ui/edit/memories/ffffffff-0000/verify").status_code == 404
    # nothing above changed anything
    assert get_memory("cccccccc-0001", db_path=db).status == "active"
    assert not get_memory("cccccccc-0001", db_path=db).superseded_by


def test_only_an_active_memory_can_be_verified(verdict_client):
    c, db = verdict_client
    c.post("/api/ui/edit/memories/cccccccc-0001/archive")
    r = c.post("/api/ui/edit/memories/cccccccc-0001/verify")
    assert r.status_code == 422 and "archived" in r.json()["detail"]


def test_verdicts_need_the_api_key_when_one_is_set(verdict_client, monkeypatch):
    c, db = verdict_client
    monkeypatch.setenv("BRAIN_API_KEY", "sekrit")
    for path, body in (("verify", None), ("close", None), ("uncorrect", None),
                       ("supersede", {"by": "cccccccc-0002"}),
                       ("correct", {"by": "cccccccc-0002"})):
        assert c.post(f"/api/ui/edit/memories/cccccccc-0001/{path}", json=body).status_code == 401
    assert get_memory("cccccccc-0001", db_path=db).status == "active"
    r = c.post("/api/ui/edit/memories/cccccccc-0001/verify", headers={"X-Brain-Key": "sekrit"})
    assert r.status_code == 200
    # the reads that show standing stay open, like every Atlas read
    assert c.get("/api/ui/brief", params={"project": "proj-a"}).status_code == 200


def test_a_verdict_reason_is_scrubbed_of_secrets(verdict_client):
    from app.storage import _connect
    c, db = verdict_client
    c.post("/api/ui/edit/memories/cccccccc-0001/supersede",
           json={"by": "cccccccc-0002", "reason": "moved, token ghp_" + "A1b2" * 9})
    with _connect(db) as conn:
        row = conn.execute("""SELECT reason FROM memory_audit WHERE memory_id = ?
                              AND action = 'supersede'""", ("cccccccc-0001",)).fetchone()
    assert "A1b2A1b2" not in (row["reason"] or "")
