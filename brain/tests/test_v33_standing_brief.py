"""3.3 standing and the brief: importance that moves with what happened to a
memory, and a brief any agent can pick a project up from."""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import lifecycle
from app.brief import build_project_brief
from app.db import connect
from app.mcp import tools as T
from app.models import MemoryEntry
from app.pins import pin_memory
from app.procedures import record_correction
from app.search import search_with_status
from app.standing import standing_of, standings
from app.storage import add_memory, record_recall

NOW = datetime.now(timezone.utc)


def _ago(days):
    return NOW - timedelta(days=days)


def _mem(db, content, type="fact", project="acme", trust="agent", days=0, importance=4,
         tags=None, writer="claude", **kw):
    entry = MemoryEntry(content=content, summary=content[:120], type=type, project=project,
                        trust=trust, importance=importance, timestamp=_ago(days),
                        tags=tags or [], writer=writer, **kw)
    add_memory(entry, db_path=db)
    return entry.id


def _one(db, memory_id):
    return standings([memory_id], db)[memory_id]


# ------------------------------------------------------------------ standing

def test_a_new_fact_is_current_at_its_stored_importance(tmp_db):
    info = _one(tmp_db, _mem(tmp_db, "The API listens on 8443"))
    assert (info["standing"], info["importance"], info["base_importance"]) == ("current", 4, 4)


def test_an_old_fact_nobody_corrected_is_proven_and_keeps_its_importance(tmp_db):
    info = _one(tmp_db, _mem(tmp_db, "The archive lives on the second instance", days=400))
    assert (info["standing"], info["importance"]) == ("proven", 4)


def test_a_fix_that_replaced_an_older_memory_rises(tmp_db):
    old = _mem(tmp_db, "Base URL is http", days=10)
    new = _mem(tmp_db, "Base URL is https on 7725")
    lifecycle.supersede(old, new, "test", db_path=tmp_db)
    assert (_one(tmp_db, new)["standing"], _one(tmp_db, new)["importance"]) == ("changed", 5)
    gone = _one(tmp_db, old)
    assert (gone["standing"], gone["importance"], gone["superseded_by"]) == ("superseded", 1, new)


def test_a_fix_older_than_thirty_days_settles_back_to_proven(tmp_db):
    old = _mem(tmp_db, "Base URL is http", days=90)
    new = _mem(tmp_db, "Base URL is https on 7725", days=60)
    lifecycle.supersede(old, new, "test", db_path=tmp_db)
    assert (_one(tmp_db, new)["standing"], _one(tmp_db, new)["importance"]) == ("proven", 4)


def test_a_corrected_memory_is_disputed_until_the_correction_goes_away(tmp_db):
    old = _mem(tmp_db, "The cache clear worked everywhere", days=5)
    fix = _mem(tmp_db, "The cache clear failed on one site")
    lifecycle.correct(old, fix, "test", db_path=tmp_db)
    info = _one(tmp_db, old)
    assert (info["standing"], info["importance"], info["corrected_by"]) == ("disputed", 3, fix)
    conn = connect(tmp_db)
    conn.execute("UPDATE memories SET status = 'archived' WHERE id = ?", (fix,))
    conn.commit()
    conn.close()
    assert _one(tmp_db, old)["standing"] == "current"


def test_an_open_contradiction_disputes_both_sides(tmp_db):
    a, b = _mem(tmp_db, "Port is 8080"), _mem(tmp_db, "Port is 9090")
    conn = connect(tmp_db)
    conn.execute("""INSERT INTO memory_links (src_id, dst_id, kind, weight, directed, meta, created_at)
                    VALUES (?, ?, 'conflicts_with', 0.9, 0, '{}', ?)""",
                 (min(a, b), max(a, b), NOW.isoformat()))
    conn.commit()
    conn.close()
    assert {_one(tmp_db, a)["standing"], _one(tmp_db, b)["standing"]} == {"disputed"}


@pytest.mark.parametrize("how", ["pin", "user", "verify", "recall"])
def test_reinforcement_adds_one(tmp_db, how):
    trust = "user" if how == "user" else "agent"
    mid = _mem(tmp_db, "Invoices go out on the first", trust=trust)
    if how == "pin":
        pin_memory(project="acme", memory_id=mid, db_path=tmp_db)
    elif how == "verify":
        lifecycle.verify(mid, "test", db_path=tmp_db)
    elif how == "recall":
        record_recall([mid], db_path=tmp_db)
        record_recall([mid], db_path=tmp_db)
    info = _one(tmp_db, mid)
    assert info["importance"] == 5 and info["reinforced"] is True
    assert info["standing"] == ("verified" if how == "verify" else "current")


def test_an_open_loop_goes_stale_after_thirty_days(tmp_db):
    fresh = _mem(tmp_db, "Send the email", type="open_loop", importance=3, days=3)
    old = _mem(tmp_db, "Ask about the old thing", type="open_loop", importance=3, days=45)
    assert (_one(tmp_db, fresh)["standing"], _one(tmp_db, fresh)["importance"]) == ("open", 3)
    assert (_one(tmp_db, old)["standing"], _one(tmp_db, old)["importance"]) == ("stale", 2)


def test_importance_stays_between_one_and_five():
    row = {"id": "x", "type": "fact", "status": "active", "importance": 5, "trust": "user",
           "timestamp": NOW.isoformat(), "strength": 3.0, "supersedes": "y"}
    assert standing_of(row, pinned=True, replaced_something=True)["importance"] == 5
    row = {**row, "importance": 1, "trust": "agent", "strength": 1.0}
    assert standing_of(row, corrector_active=True)["importance"] == 1


def test_reading_standing_changes_nothing(tmp_db):
    mid = _mem(tmp_db, "A stable fact", days=40)
    before = connect(tmp_db).execute("SELECT * FROM memories WHERE id = ?", (mid,)).fetchone()
    assert _one(tmp_db, mid) == _one(tmp_db, mid)
    after = connect(tmp_db).execute("SELECT * FROM memories WHERE id = ?", (mid,)).fetchone()
    assert tuple(before) == tuple(after)


# ------------------------------------------------------- search and get_memory

async def test_search_ranks_the_correction_above_the_memory_it_corrects(tmp_db, fake_provider):
    wrong = _mem(tmp_db, "The deploy invoice car release note says http", days=2)
    right = _mem(tmp_db, "The deploy invoice car release note says https", days=1)
    hits, _ = await search_with_status("deploy invoice car release note", db_path=tmp_db)
    lifecycle.correct(wrong, right, "test", db_path=tmp_db)
    after, _ = await search_with_status("deploy invoice car release note", db_path=tmp_db)
    by_id = {h["id"]: h for h in after}
    assert [h["id"] for h in after][0] == right
    assert by_id[wrong]["standing"] == "disputed" and by_id[wrong]["corrected_by"] == right
    assert by_id[wrong]["importance"] == 3 and by_id[right]["importance"] == 4
    assert {h["id"] for h in hits} == {wrong, right}


async def test_get_memory_reports_standing(tmp_db, monkeypatch):
    monkeypatch.setattr("app.mcp.tools.DB_PATH", tmp_db)
    old = _mem(tmp_db, "It worked", days=3)
    fix = _mem(tmp_db, "It failed")
    lifecycle.correct(old, fix, "test", db_path=tmp_db)
    got = json.loads(await T.handle_get_memory(old))
    assert (got["standing"], got["importance"], got["base_importance"]) == ("disputed", 3, 4)
    assert got["corrected_by"] == fix


# --------------------------------------------------------------------- brief

async def test_the_brief_opens_with_where_we_left_off(tmp_db):
    _mem(tmp_db, "Older session about the export", type="session", days=9, importance=3)
    last = _mem(tmp_db, "Session: moved the export job and closed the ticket",
                type="session", days=2, importance=3, writer="codex")
    _mem(tmp_db, "A fact written afterwards", days=1)
    pack = await build_project_brief("acme", db_path=tmp_db)
    keys = [k for k in pack if isinstance(pack[k], list)]
    assert keys[0] == "where_we_left_off"
    (item,) = pack["where_we_left_off"]
    assert (item["id"], item["writer"], item["age_days"]) == (last, "codex", 2)
    assert pack["last_activity"]["writer"] == "claude" and pack["dormant"] is False


async def test_a_dormant_project_still_shows_its_last_work(tmp_db):
    old = _mem(tmp_db, "Session: built the level loader", type="session", days=200,
               importance=3)
    fact = _mem(tmp_db, "The loader reads maps from the maps folder", days=200)
    pack = await build_project_brief("acme", db_path=tmp_db)
    assert pack["dormant"] is True and pack["last_activity"]["days_ago"] == 200
    assert pack["where_we_left_off"][0]["id"] == old
    assert {i["id"] for i in pack["recent"]} == {old, fact}
    assert pack["facts_and_decisions"][0]["standing"] == "proven"


async def test_facts_are_ordered_by_standing_and_a_disputed_one_points_at_its_fix(tmp_db):
    plain = _mem(tmp_db, "Plain fact about the gateway", days=3)
    wrong = _mem(tmp_db, "The gateway uses port 8080", days=3)
    fix = _mem(tmp_db, "The gateway uses port 8443 since the move", days=1)
    replaced = _mem(tmp_db, "Deploys go out on Friday", days=20)
    fresh = _mem(tmp_db, "Deploys go out on Tuesday")
    lifecycle.correct(wrong, fix, "test", db_path=tmp_db)
    lifecycle.supersede(replaced, fresh, "test", db_path=tmp_db)
    pack = await build_project_brief("acme", db_path=tmp_db)
    facts = pack["facts_and_decisions"]
    ids = [f["id"] for f in facts]
    assert ids[0] == fresh and ids[-1] == wrong and replaced not in ids
    by_id = {f["id"]: f for f in facts}
    assert by_id[fresh]["standing"] == "changed" and by_id[fresh]["importance"] == 5
    assert by_id[wrong]["standing"] == "disputed" and by_id[wrong]["corrected_by"] == fix
    assert "standing" not in by_id[plain]


async def test_changes_lists_replacements_corrections_and_finished_loops(tmp_db):
    old = _mem(tmp_db, "Deploys go out on Friday", days=20)
    new = _mem(tmp_db, "Deploys go out on Tuesday")
    wrong = _mem(tmp_db, "It worked on every site", days=3)
    fix = _mem(tmp_db, "It failed on the second site")
    loop = _mem(tmp_db, "Send the completion email", type="open_loop", importance=3, days=2)
    lifecycle.supersede(old, new, "test", db_path=tmp_db)
    lifecycle.correct(wrong, fix, "test", db_path=tmp_db)
    lifecycle.close_loop(loop, "test", db_path=tmp_db)
    pack = await build_project_brief("acme", db_path=tmp_db)
    got = {(c["kind"], c["id"]): c for c in pack["changes"]}
    assert set(got) == {("replaced", new), ("corrected", fix), ("finished", loop)}
    assert got[("replaced", new)]["replaced"] == {"id": old, "summary": "Deploys go out on Friday"}
    assert got[("corrected", fix)]["disputes"]["id"] == wrong
    assert loop not in [i["id"] for i in pack["open_loops"]]


async def test_old_changes_show_only_when_nothing_changed_lately(tmp_db):
    old = _mem(tmp_db, "Deploys go out on Friday", days=300)
    new = _mem(tmp_db, "Deploys go out on Tuesday", days=200)
    lifecycle.supersede(old, new, "test", db_path=tmp_db)
    conn = connect(tmp_db)
    conn.execute("UPDATE memories SET valid_to = ? WHERE id = ?", (_ago(200).isoformat(), old))
    conn.commit()
    conn.close()
    pack = await build_project_brief("acme", db_path=tmp_db)
    assert [c["id"] for c in pack["changes"]] == [new]


async def test_stale_loops_come_last_and_are_counted(tmp_db):
    stale = [_mem(tmp_db, f"Old loop {i}", type="open_loop", importance=3, days=60 + i)
             for i in range(3)]
    fresh = [_mem(tmp_db, f"Fresh loop {i}", type="open_loop", importance=3, days=i)
             for i in range(2)]
    pack = await build_project_brief("acme", db_path=tmp_db)
    assert [i["id"] for i in pack["open_loops"]] == fresh + stale
    assert [bool(i.get("stale")) for i in pack["open_loops"]] == [False, False, True, True, True]
    assert pack["open_loop_count"] == 5 and pack["waiting_on_you"]["stale_loops"] == 3


async def test_howtos_are_listed_by_tag(tmp_db):
    how = _mem(tmp_db, "HOW-TO: rotate the export key in five steps", tags=["howto", "export"])
    book = _mem(tmp_db, "Runbook for the failed snapshot job", type="reference",
                tags=["Runbook"], importance=3)
    _mem(tmp_db, "Session that mentions a howto", type="session", tags=["howto"], importance=3)
    _mem(tmp_db, "A fact without the tag")
    pack = await build_project_brief("acme", db_path=tmp_db)
    assert {i["id"] for i in pack["howtos"]} == {how, book}


async def test_waiting_on_you_counts_unconfirmed_rules(tmp_db):
    record_correction("Always open a pull request.", project="acme", db_path=tmp_db)
    record_correction("Keep messages short.", db_path=tmp_db)            # applies everywhere
    record_correction("Rule for another project.", project="other", db_path=tmp_db)
    _mem(tmp_db, "A fact")
    pack = await build_project_brief("acme", db_path=tmp_db)
    assert pack["waiting_on_you"]["rules"] == 2 and pack["procedures"] == []


async def test_a_stale_file_index_is_a_warning(tmp_db):
    from app.workspace import store as ws
    _mem(tmp_db, "A fact")
    ws.upsert_root("git", "PC", "C:\\work", tmp_db)
    conn = connect(tmp_db)
    conn.execute("UPDATE workspace_roots SET last_seen = ?", (_ago(40).isoformat(),))
    conn.commit()
    conn.close()
    pack = await build_project_brief("acme", db_path=tmp_db)
    assert any("file index was last scanned 40 days ago" in w for w in pack["warnings"])


async def test_where_we_left_off_survives_a_tight_budget(tmp_db):
    last = _mem(tmp_db, "Session: the one thing to remember", type="session", days=1,
                importance=3)
    for i in range(40):
        _mem(tmp_db, f"Agent fact number {i}: " + "detail " * 30)
    pack = await build_project_brief("acme", max_chars=1200, db_path=tmp_db)
    assert pack["where_we_left_off"][0]["id"] == last
    assert pack["chars_used"] <= pack["char_budget"]


async def test_a_long_list_of_facts_does_not_push_out_the_open_loops(tmp_db):
    for i in range(30):
        _mem(tmp_db, f"Agent fact number {i}: " + "detail " * 40)
    loops = [_mem(tmp_db, f"Loop {i}: " + "something still to do " * 8, type="open_loop",
                  importance=3, days=i) for i in range(8)]
    for i in range(4):
        _mem(tmp_db, f"HOW-TO number {i}: " + "step " * 30, tags=["howto"], days=40)
    pack = await build_project_brief("acme", db_path=tmp_db)
    assert "facts_and_decisions" in pack["truncated"]
    assert [i["id"] for i in pack["open_loops"]][:5] == loops[:5]
    assert len(pack["open_loops"]) >= 5 and len(pack["facts_and_decisions"]) >= 6
    assert len(pack["howtos"]) >= 3 and pack["chars_used"] <= pack["char_budget"]


async def test_heavy_pins_never_leave_a_brief_with_no_open_loops(tmp_db):
    """Six long pins and a long next-session note used to take the space, and
    the brief then showed a few facts and none of the open loops."""
    for i in range(6):
        pinned = _mem(tmp_db, f"Pinned truth {i}: " + "something the user pinned " * 10, days=40)
        pin_memory(project="acme", memory_id=pinned, db_path=tmp_db)
    _mem(tmp_db, "Next time: " + "check the export retries. " * 60, type="note",
         tags=["next_session"], importance=3)
    _mem(tmp_db, "Session: " + "what we did last time " * 12, type="session", importance=3, days=1)
    for i in range(12):
        _mem(tmp_db, f"Agent fact number {i}: " + "detail " * 40, days=2)
    loops = [_mem(tmp_db, f"Loop {i}: " + "something still to do " * 8, type="open_loop",
                  importance=3, days=i) for i in range(8)]
    pack = await build_project_brief("acme", db_path=tmp_db)
    assert len(pack["pins"]) == 6 and pack["where_we_left_off"] and pack["next_session"]
    assert [i["id"] for i in pack["open_loops"]][:2] == loops[:2]
    assert len(pack["facts_and_decisions"]) >= 2
    assert pack["chars_used"] <= pack["char_budget"]


async def test_floors_give_way_when_the_budget_is_very_small(tmp_db):
    for i in range(12):
        _mem(tmp_db, f"Agent fact number {i}: " + "detail " * 40)
    for i in range(8):
        _mem(tmp_db, f"Loop {i}: " + "something still to do " * 8, type="open_loop",
             importance=3)
    pack = await build_project_brief("acme", max_chars=800, db_path=tmp_db)
    assert pack["chars_used"] <= pack["char_budget"] == 800


# ------------------------------------------------------------ the hook's text

def _render():
    path = Path(__file__).resolve().parents[1] / "hooks" / "render_brief.py"
    if not path.exists():                         # running from the repo, not the image
        path = Path(__file__).resolve().parents[2] / "hooks" / "render_brief.py"
    spec = importlib.util.spec_from_file_location("render_brief", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.render


async def test_the_hook_renders_the_pickup_order(tmp_db):
    _mem(tmp_db, "Session: moved the export job", type="session", days=2, importance=3,
         writer="codex")
    wrong = _mem(tmp_db, "The gateway uses port 8080", days=3)
    fix = _mem(tmp_db, "The gateway uses port 8443")
    lifecycle.correct(wrong, fix, "test", db_path=tmp_db)
    _mem(tmp_db, "Ask the vendor", type="open_loop", importance=3, days=50)
    _mem(tmp_db, "Send the email", type="open_loop", importance=3, days=1)
    record_correction("Always open a pull request.", project="acme", db_path=tmp_db)
    text = _render()(await build_project_brief("acme", db_path=tmp_db))
    lines = text.splitlines()
    assert lines[0].startswith("Stored notes from MemoryBrain for acme.")
    titles = [ln for ln in lines if ln.startswith("## ")]
    assert titles[:4] == ["## Where we left off", "## Facts and decisions", "## What changed",
                          "## Open loops"]
    assert "## Waiting on you" in titles
    assert any(ln.endswith("(codex) Session: moved the export job") for ln in lines)
    assert f"- [disputed, see {fix[:8]}] The gateway uses port 8080" in lines
    assert any("(corrects part of: The gateway uses port 8080)" in ln for ln in lines)
    assert lines.index("- Send the email") < lines.index("- (stale, 50 days) Ask the vendor")
    assert any("1 rule(s) recorded from your corrections wait for your yes" in ln for ln in lines)


def test_the_hook_says_when_a_project_is_being_picked_up_again():
    text = _render()({"project": "acme", "dormant": True,
                      "last_activity": {"days_ago": 200, "writer": "grok"},
                      "where_we_left_off": [{"summary": "Built the loader",
                                             "timestamp": "2026-01-02T10:00:00", "writer": "grok"}]})
    assert "- 2026-01-02 (grok) Built the loader" in text
    assert "Nothing has been written here for 200 days (last by grok)." in text


async def test_the_startup_summary_leads_with_the_last_session(tmp_db, monkeypatch):
    from app.storage import Project, upsert_project
    monkeypatch.setattr("app.mcp.tools.DB_PATH", tmp_db)
    upsert_project(Project(slug="acme", name="Acme"), db_path=tmp_db)
    _mem(tmp_db, "Session: finished the export move", type="session", days=3, importance=3)
    _mem(tmp_db, "A small fact written later", days=1)
    text = await T.handle_get_startup_summary()
    assert "Session: finished the export move" in text.split("## Recent")[0]
