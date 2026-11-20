"""3.3 entities: ticket ids without a hyphen, pull request refs, field names no
longer taken for products, patterns each install adds for its own names, and
one lookup for everything stored about a name."""
import json

import pytest

from app import entities, lifecycle
from app.entities import extract_entities, index_entities
from app.mcp import tools as T
from app.models import MemoryEntry
from app.storage import add_memory
from app.workspace import store as ws


@pytest.fixture
def db(tmp_db, fake_provider, monkeypatch):
    monkeypatch.setattr("app.ingest_pipeline.DB_PATH", tmp_db)
    monkeypatch.setattr("app.mcp.tools.DB_PATH", tmp_db)
    return tmp_db


async def _call(name, args):
    return json.loads((await T.call_tool(name, args))[0].text)


def _mem(db, content, type="fact", project="acme", **kw):
    entry = MemoryEntry(content=content, summary=content[:100], type=type, project=project,
                        importance=4, **kw)
    add_memory(entry, db_path=db)
    index_entities(entry.id, content, db_path=db)
    return entry.id


# ------------------------------------------------------------ built-in kinds

def test_ticket_ids_without_a_hyphen_are_tickets():
    found = set(extract_entities("Closed INC0012345 under CHG0031234 and TASK000123456789."))
    assert {("ticket", "INC0012345"), ("ticket", "CHG0031234"),
            ("ticket", "TASK000123456789")} <= found


def test_a_cve_is_one_whole_ticket():
    assert ("ticket", "CVE-2026-12345") in extract_entities("Patched CVE-2026-12345 today")
    assert not [n for k, n in extract_entities("Patched CVE-2026-12345") if n == "CVE-2026"]


@pytest.mark.parametrize("text", ["16 GB of DDR4-3200", "GPT-6 wrote it", "an RTX-4090 card",
                                  "ISO27001 audit", "SHA25600", "UTF-8 text"])
def test_hardware_and_standards_are_not_tickets(text):
    assert [n for k, n in extract_entities(text) if k == "ticket"] == []


def test_pull_request_refs_are_found():
    found = extract_entities("Merged acme-co/report-flow#35 after octo/app.web#1204 landed.")
    assert [n for k, n in found if k == "pr"] == ["acme-co/report-flow#35", "octo/app.web#1204"]


def test_field_and_call_names_are_not_products():
    text = "Join on UserId and DateKey where IsActive, then call GetRowsAsync in ReportFlow."
    assert [n for k, n in extract_entities(text) if k == "product"] == ["ReportFlow"]


def test_a_file_stem_is_not_an_env_var():
    found = extract_entities("Updated PROGRESS_LOG.md and set BRAIN_API_KEY")
    assert [n for k, n in found if k == "env_var"] == ["BRAIN_API_KEY"]


# ------------------------------------------------------- patterns per install

def test_a_custom_pattern_names_its_own_kind(tmp_db):
    entities.add_pattern("server", r"\bsrv-[a-z]+-\d{2}\b", db_path=tmp_db)
    mid = _mem(tmp_db, "Ran the check on srv-db-01 and again on srv-arc-06.")
    top = {(e["kind"], e["name"]) for e in entities.top_entities("acme", db_path=tmp_db)}
    assert {("server", "srv-db-01"), ("server", "srv-arc-06")} <= top
    assert mid


def test_a_capture_group_names_the_entity():
    rx = entities.check_pattern("page", r"doc/(\d{4,8})")
    assert extract_entities("see the wiki at doc/40721 for it", [("page", rx)]) == [
        ("page", "40721")]


@pytest.mark.parametrize("kind,pattern,why", [
    ("Server", r"\d+", "kind must be"),
    ("server", "", "must not be empty"),
    ("server", "x" * 201, "exceeds 200"),
    ("server", r"(a+)+b", "can take forever"),
    ("server", r"[unclosed", "not a valid regular expression"),
    ("server", r"\d*", "matches empty text"),
])
def test_bad_patterns_are_refused(kind, pattern, why):
    with pytest.raises(ValueError, match=why):
        entities.check_pattern(kind, pattern)


def test_at_most_forty_patterns(tmp_db):
    for i in range(entities.MAX_PATTERNS):
        entities.add_pattern("thing", rf"T{i}-\d+", db_path=tmp_db)
    with pytest.raises(ValueError, match="at most 40"):
        entities.add_pattern("thing", r"OVER-\d+", db_path=tmp_db)
    assert entities.add_pattern("thing", r"T0-\d+", db_path=tmp_db)["added"] is False


async def test_patterns_are_managed_through_brain_admin_and_reindex_applies_them(db):
    before = _mem(db, "The archive for site 12 is on srv-arc-06.")
    first = await _call("brain_admin", {"action": "entity_patterns", "args": {}})
    assert first["patterns"] == [] and "ticket" in first["built_in_kinds"]
    added = await _call("brain_admin", {"action": "entity_patterns", "args": {
        "add": [{"kind": "server", "pattern": r"\bsrv-[a-z]+-\d{2}\b"}]}})
    assert added["added"] == 1 and "reindex_entities" in added["next"]
    assert [p["kind"] for p in added["patterns"]] == ["server"]
    assert ("server", "srv-arc-06") not in {
        (e["kind"], e["name"]) for e in entities.top_entities("acme", db_path=db)}
    done = await _call("brain_admin", {"action": "reindex_entities"})
    assert done["memories"] == 1
    assert ("server", "srv-arc-06") in {
        (e["kind"], e["name"]) for e in entities.top_entities("acme", db_path=db)}
    gone = await _call("brain_admin", {"action": "entity_patterns", "args": {
        "remove": [{"kind": "server", "pattern": r"\bsrv-[a-z]+-\d{2}\b"}]}})
    assert gone["removed"] == 1 and gone["patterns"] == []
    assert before


async def test_one_bad_pattern_stores_none_of_the_batch(db):
    reply = await _call("brain_admin", {"action": "entity_patterns", "args": {
        "add": [{"kind": "server", "pattern": r"SRV\d+"}, {"kind": "server", "pattern": "(a+)+"}]}})
    assert "can take forever" in reply["error"]
    assert entities.list_patterns(db) == []


# ------------------------------------------------------------- entity context

async def test_entity_context_gathers_everything_about_a_ticket(db):
    ws.apply_manifest({"machine": "m", "root_id": "git", "abs_path": r"C:\work", "full": True,
                       "files": [{"rel_path": "Deploy/CHG0031234/steps.md", "sha256": "a"}],
                       "markers": []}, db_path=db)
    old = _mem(db, "CHG0031234 deploy prepared, not run", type="open_loop")
    status = _mem(db, "CHG0031234 status: scripts done, QA left", type="open_loop")
    fact = _mem(db, "CHG0031234 moved the cache clear to https, see Deploy/CHG0031234/steps.md")
    from app.workspace.resolve import link_memory_files
    from app.storage import get_memory
    link_memory_files(get_memory(fact, db_path=db), db)
    _mem(db, "Unrelated INC0099999 in the same project")
    _mem(db, "CHG0031234 mentioned in another project", project="other")
    lifecycle.supersede(old, status, "test", db_path=db)

    got = await _call("brain_admin", {"action": "get_entity_context",
                                      "args": {"name": "chg0031234", "project": "acme"}})
    assert got["kinds"] == ["ticket"] and got["found_by"] == "index"
    assert [m["id"] for m in got["memories"]] == [fact]
    assert [m["id"] for m in got["open_loops"]] == [status]
    assert got["history"] == 1 and got["projects"] == ["acme"]
    assert got["files"] == [{"rel_path": "Deploy/CHG0031234/steps.md", "memories": 1}]
    assert got["open_loops"][0]["standing"] == "open" and got["memories"][0]["importance"] == 4

    everywhere = await _call("brain_admin", {"action": "get_entity_context",
                                             "args": {"name": "CHG0031234"}})
    assert everywhere["projects"] == ["acme", "other"]


async def test_entity_context_falls_back_to_the_text_for_an_unindexed_name(db):
    mid = _mem(db, "The late export was traced to the nightly vacuum window")
    got = await _call("brain_admin", {"action": "get_entity_context",
                                      "args": {"name": "nightly vacuum"}})
    assert got["found_by"] == "text" and [m["id"] for m in got["memories"]] == [mid]
    none = await _call("brain_admin", {"action": "get_entity_context",
                                       "args": {"name": "never mentioned anywhere"}})
    assert none["memories"] == [] and none["history"] == 0


# ------------------------------------------------------------ open loop list

async def test_list_open_loops_is_oldest_first_and_can_keep_only_the_stale(db):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    old = _mem(db, "Ask about the old thing", type="open_loop",
               timestamp=now - timedelta(days=50))
    new = _mem(db, "Send the email", type="open_loop", timestamp=now - timedelta(days=2))
    _mem(db, "Another project's loop", type="open_loop", project="other")
    all_loops = await _call("brain_admin", {"action": "list_open_loops",
                                            "args": {"project": "acme"}})
    assert [(i["id"], i["stale"]) for i in all_loops["open_loops"]] == [(old, True), (new, False)]
    stale = await _call("brain_admin", {"action": "list_open_loops",
                                        "args": {"project": "acme", "stale_only": True}})
    assert stale["total"] == 1 and stale["open_loops"][0]["age_days"] == 50
