"""3.3 truth maintenance: a write can replace, correct or finish older memories,
named by full id or by the first 8 characters; the same moves exist for stored
memories through brain_admin; a session that reports a loop done closes it."""
import json

import pytest

from app import lifecycle
from app.db import connect
from app.ingest_pipeline import ingest
from app.mcp import tools as T
from app.models import MemoryEntry
from app.storage import add_memory, get_memory


@pytest.fixture
def db(tmp_db, fake_provider, monkeypatch):
    monkeypatch.setattr("app.ingest_pipeline.DB_PATH", tmp_db)
    monkeypatch.setattr("app.mcp.tools.DB_PATH", tmp_db)
    monkeypatch.delenv("MEMORYBRAIN_SUMMARY_MODE", raising=False)
    return tmp_db


async def _call(name, args):
    return json.loads((await T.call_tool(name, args))[0].text)


def _mem(db, content, type="fact", project="acme", trust="agent", memory_id=None, **kw):
    entry = MemoryEntry(content=content, summary=content[:80], type=type, project=project,
                        trust=trust, importance=4, **kw)
    if memory_id:
        entry.id = memory_id
    add_memory(entry, db_path=db)
    return entry.id


def _audit(db, memory_id):
    conn = connect(db)
    try:
        return [(r["action"], r["actor"], json.loads(r["detail"])) for r in conn.execute(
            "SELECT action, actor, detail FROM memory_audit WHERE memory_id = ? ORDER BY rowid",
            (memory_id,))]
    finally:
        conn.close()


# ------------------------------------------------------------- resolve_id

def test_a_full_id_and_a_unique_prefix_resolve(db):
    mid = _mem(db, "The API listens on port 8443")
    assert lifecycle.resolve_id(mid, db) == mid
    assert lifecycle.resolve_id(mid[:8], db) == mid
    assert lifecycle.resolve_id(mid[:8].upper(), db) == mid


def test_a_prefix_that_fits_two_memories_is_refused(db):
    _mem(db, "one", memory_id="abcd1234-0000-4000-8000-000000000001")
    _mem(db, "two", memory_id="abcd1234-0000-4000-8000-000000000002")
    with pytest.raises(lifecycle.LifecycleError, match="more than one"):
        lifecycle.resolve_id("abcd1234", db)


@pytest.mark.parametrize("bad", ["", "abc", "not-an-id!", "zzzzzzzz", "abcd123%", None])
def test_what_is_not_an_id_is_refused(db, bad):
    with pytest.raises(lifecycle.LifecycleError):
        lifecycle.resolve_id(bad, db)


def test_an_unknown_prefix_is_refused(db):
    with pytest.raises(lifecycle.LifecycleError, match="no memory id starts with"):
        lifecycle.resolve_id("deadbeef", db)


# ----------------------------------------------------- add_memory: supersedes

async def test_supersedes_archives_the_old_fact_and_points_it_at_the_new_one(db):
    old = _mem(db, "The API base URL is http://api.example.internal")
    report = await _call("add_memory", {
        "content": "The API base URL is https://api.example.internal:7725",
        "type": "fact", "project": "acme", "summary": "API base URL is https on 7725",
        "supersedes": [old[:8]]})
    assert report["superseded"] == [old]
    stored = get_memory(old, db_path=db)
    assert stored.status == "archived" and stored.superseded_by == report["id"]
    assert stored.valid_to
    assert get_memory(report["id"], db_path=db).supersedes == old
    assert _audit(db, old)[-1] == ("supersede", "mcp", {"by": report["id"]})


async def test_a_bad_id_fails_the_whole_write(db):
    reply = await _call("add_memory", {"content": "new truth", "type": "fact",
                                       "project": "acme", "supersedes": ["deadbeef"]})
    assert "no memory id starts with deadbeef" in reply["error"]
    conn = connect(db)
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
    conn.close()


async def test_a_target_in_another_project_is_refused(db):
    other = _mem(db, "belongs elsewhere", project="other")
    reply = await _call("add_memory", {"content": "new truth", "type": "fact",
                                       "project": "acme", "supersedes": [other]})
    assert "belongs to project other" in reply["error"]
    assert get_memory(other, db_path=db).status == "active"


async def test_what_the_user_wrote_is_not_retired_by_an_agent(db):
    mine = _mem(db, "My own rule about deploys", trust="user")
    for move in ("supersedes", "corrects"):
        reply = await _call("add_memory", {"content": f"agent text {move}", "type": "fact",
                                           "project": "acme", move: [mine]})
        assert "written by the user" in reply["error"]
    assert get_memory(mine, db_path=db).status == "active"


async def test_the_same_id_under_two_moves_is_refused(db):
    old = _mem(db, "An old fact")
    reply = await _call("add_memory", {"content": "new", "type": "fact", "project": "acme",
                                       "supersedes": [old], "corrects": [old]})
    assert "pick one" in reply["error"]


async def test_lists_must_be_lists_of_ids(db):
    reply = await _call("add_memory", {"content": "new", "type": "fact", "project": "acme",
                                       "supersedes": "abcd1234"})
    assert "error" in reply


# ------------------------------------------------------- add_memory: corrects

async def test_corrects_keeps_the_old_memory_and_marks_it_disputed(db):
    old = _mem(db, "The cache clear worked on every site")
    report = await _call("add_memory", {
        "content": "The cache clear failed on the second site with a 400",
        "type": "fact", "project": "acme", "corrects": [old]})
    assert report["corrected"] == [old] and report["superseded"] == []
    stored = get_memory(old, db_path=db)
    assert stored.status == "active" and stored.corrected_by == report["id"]
    assert _audit(db, old)[-1][0] == "correct"


# --------------------------------------------------------- add_memory: closes

async def test_closes_marks_an_open_loop_done(db):
    loop = _mem(db, "Run the post-deploy check on the export job", type="open_loop")
    report = await _call("add_memory", {
        "content": "Post-deploy check ran clean at 14:40", "type": "fact", "project": "acme",
        "closes": [loop[:8]]})
    assert report["closed"] == [loop]
    stored = get_memory(loop, db_path=db)
    assert stored.status == "done" and stored.invalidated_by == report["id"]


async def test_closes_refuses_a_memory_that_is_not_a_loop(db):
    fact = _mem(db, "A fact, not a loop")
    reply = await _call("add_memory", {"content": "x", "type": "note", "project": "acme",
                                       "closes": [fact]})
    assert "closes takes open loops" in reply["error"]


async def test_a_session_that_reports_a_loop_done_closes_it_on_write(db):
    loop = _mem(db, "Open loop: rebuild the release notes page", type="open_loop")
    other = _mem(db, "Open loop: ask the supplier about the shared mailbox", type="open_loop")
    report = await _call("add_memory", {
        "content": "Session log. The release notes page rebuild is done. "
                   "The supplier question about the shared mailbox is not done yet.",
        "type": "session", "project": "acme", "summary": "Rebuilt the release notes page"})
    assert report["closed"] == [loop]
    assert get_memory(loop, db_path=db).status == "done"
    assert get_memory(other, db_path=db).status == "active"
    assert _audit(db, loop)[-1][:2] == ("close", "ingest")


async def test_a_fact_never_closes_loops_by_its_wording(db):
    loop = _mem(db, "Open loop: rebuild the release notes page", type="open_loop")
    report = await _call("add_memory", {
        "content": "The release notes page rebuild is done.", "type": "fact",
        "project": "acme"})
    assert report["closed"] == [] and get_memory(loop, db_path=db).status == "active"


# ------------------------------------------------------ brain_admin: the moves

async def test_admin_supersede_links_two_stored_memories(db):
    old = _mem(db, "Old truth")
    new = _mem(db, "New truth")
    reply = await _call("brain_admin", {"action": "supersede",
                                        "args": {"old_id": old[:8], "new_id": new[:8],
                                                 "reason": "stated in the new note"}})
    assert reply["superseded"] == [old] and reply["new_id"] == new
    assert get_memory(old, db_path=db).superseded_by == new
    restored = await _call("brain_admin", {"action": "restore_memory",
                                           "args": {"memory_id": old}})
    assert restored["restored"] is True and get_memory(old, db_path=db).status == "active"


async def test_admin_supersede_refuses_itself_and_an_archived_replacement(db):
    old = _mem(db, "Old truth")
    gone = _mem(db, "Archived already", status="archived")
    same = await _call("brain_admin", {"action": "supersede",
                                       "args": {"old_id": old, "new_id": old}})
    assert "cannot replace itself" in same["error"]
    dead = await _call("brain_admin", {"action": "supersede",
                                       "args": {"old_id": old, "new_id": gone}})
    assert "only an active memory" in dead["error"]


async def test_admin_correct_memory_marks_the_dispute(db):
    old = _mem(db, "It worked everywhere")
    new = _mem(db, "It failed on one site")
    reply = await _call("brain_admin", {"action": "correct_memory",
                                        "args": {"old_id": old, "new_id": new}})
    assert reply["corrected"] == [old]
    assert get_memory(old, db_path=db).corrected_by == new


async def test_admin_close_loop_and_closing_twice(db):
    loop = _mem(db, "Send the completion email", type="open_loop")
    first = await _call("brain_admin", {"action": "close_loop",
                                        "args": {"memory_id": loop[:8], "reason": "sent"}})
    assert first == {"id": loop, "closed": True, "status": "done"}
    again = await _call("brain_admin", {"action": "close_loop", "args": {"memory_id": loop}})
    assert again == {"id": loop, "closed": False, "status": "done"}
    assert [a for a, _, _ in _audit(db, loop)] == ["close"]


async def test_admin_verify_memory_stamps_it(db):
    fact = _mem(db, "The archive copy for site 12 is on the second server")
    reply = await _call("brain_admin", {"action": "verify_memory",
                                        "args": {"memory_id": fact, "note": "checked live"}})
    assert reply["id"] == fact and reply["verified_at"]
    assert get_memory(fact, db_path=db).verified_at == reply["verified_at"]
    assert _audit(db, fact)[-1][0] == "verify"


# --------------------------------------------------- stated in prose, not applied

async def test_stated_corrections_lists_pairs_still_active(db):
    wrong = _mem(db, "The base URL is http on port 80", memory_id="aaaa1111-0000-4000-8000-000000000001")
    fix = _mem(db, "Supersedes memory aaaa1111 (said http). Verified: https on 7725 works.")
    partly = _mem(db, "Supersedes the \"it worked\" part of memory "
                      f"{fix[:8]}. The call returned HTTP 400.")
    loop = _mem(db, "Deploy prepared, not run", type="open_loop",
                memory_id="bbbb2222-0000-4000-8000-000000000001")
    status = _mem(db, "Supersedes open loop bbbb2222. Deploy done, QA left.", type="open_loop")
    _mem(db, "Built from commit 3f9a2c1d, which supersedes nothing here.")  # not a memory id
    reply = await _call("brain_admin", {"action": "list_stated_corrections",
                                        "args": {"project": "acme"}})
    got = {(p["new_id"], p["old_id"]): p for p in reply["pairs"]}
    assert set(got) == {(fix, wrong), (partly, fix), (status, loop)}
    assert got[(partly, fix)]["partial"] is True and got[(fix, wrong)]["partial"] is False
    assert got[(status, loop)]["old_type"] == "open_loop"

    await _call("brain_admin", {"action": "supersede", "args": {"old_id": wrong, "new_id": fix}})
    after = await _call("brain_admin", {"action": "list_stated_corrections", "args": {}})
    assert (fix, wrong) not in {(p["new_id"], p["old_id"]) for p in after["pairs"]}
