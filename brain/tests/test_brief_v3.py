"""v3 brief: an envelope, the user's own truths first, provenance on every item,
a cap on agent-written text, and a timeline that can look back."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.brief import AGENT_SHARE, ENVELOPE, build_project_brief
from app.db import connect
from app.models import MemoryEntry
from app.procedures import confirm_procedure, record_correction
from app.storage import add_memory
from app.timeline import get_timeline


def _mem(db, content, type_="fact", project="acme", trust="agent", writer="claude", **kw):
    entry = MemoryEntry(content=content, type=type_, project=project, importance=4,
                        trust=trust, writer=writer, summary=content[:200], **kw)
    add_memory(entry, db_path=db)
    return entry.id


@pytest.mark.asyncio
async def test_the_envelope_comes_first_and_sections_keep_their_order(tmp_db):
    rule = record_correction("Always open a pull request.", project="acme", db_path=tmp_db)
    confirm_procedure(rule["id"], actor="user", db_path=tmp_db)
    _mem(tmp_db, "The export runs nightly.", trust="user", writer="ui")
    pack = await build_project_brief("acme", db_path=tmp_db)
    keys = list(pack)
    assert keys[0] == "envelope" and pack["envelope"] == ENVELOPE
    order = ["pins", "procedures", "facts_and_decisions", "open_loops", "next_session",
             "beliefs", "intent_hits", "conflicts", "recent", "system_ops"]
    assert [k for k in keys if k in order] == order
    (proc,) = pack["procedures"]
    assert proc["id"] == rule["id"] and proc["trust"] == "user"
    fact = pack["facts_and_decisions"][0]
    assert (fact["trust"], fact["writer"]) == ("user", "ui")


@pytest.mark.asyncio
async def test_closed_facts_are_not_current(tmp_db):
    old = _mem(tmp_db, "The gateway uses port 8080.")
    conn = connect(tmp_db)
    try:
        with conn:
            conn.execute("UPDATE memories SET valid_to = ? WHERE id = ?",
                         (datetime.now(timezone.utc).isoformat(), old))
    finally:
        conn.close()
    pack = await build_project_brief("acme", db_path=tmp_db)
    assert old not in [f["id"] for f in pack["facts_and_decisions"]]


def _agent_chars(pack):
    total = 0
    for key, value in pack.items():
        if isinstance(value, list):
            total += sum(len(json.dumps(i, default=str)) for i in value
                         if isinstance(i, dict) and i.get("trust") in ("agent", "derived", "imported"))
    return total


@pytest.mark.asyncio
async def test_agent_text_stays_within_its_share_and_truncation_is_named(tmp_db):
    for i in range(30):
        _mem(tmp_db, f"Agent fact number {i}: " + "detail " * 30)
    _mem(tmp_db, "User truth: invoices go out on the 1st.", trust="user", writer="ui")
    pack = await build_project_brief("acme", max_chars=2000, db_path=tmp_db)
    assert _agent_chars(pack) <= AGENT_SHARE * pack["char_budget"]
    assert "facts_and_decisions" in pack["truncated"]
    assert any(f["trust"] == "user" for f in pack["facts_and_decisions"])


@pytest.mark.asyncio
async def test_intent_hits_outlast_recent(tmp_db, fake_provider, monkeypatch):
    monkeypatch.setattr("app.search.DB_PATH", tmp_db)
    for i in range(12):
        _mem(tmp_db, f"Session log {i} about the car " + "words " * 40, type_="session")
    pack = await build_project_brief("acme", intent="car", max_chars=1500, db_path=tmp_db)
    assert pack["intent_hits"], pack["truncated"]
    assert "recent" in pack["truncated"] or pack["recent"] == []


def test_the_timeline_can_look_back_to_a_superseded_fact(tmp_db):
    then = datetime.now(timezone.utc) - timedelta(days=20)
    old = _mem(tmp_db, "The gateway uses port 8080.", timestamp=then)
    conn = connect(tmp_db)
    try:
        with conn:
            conn.execute("UPDATE memories SET status = 'archived', valid_from = ?, valid_to = ? "
                         "WHERE id = ?", (then.isoformat(),
                                          (then + timedelta(days=10)).isoformat(), old))
    finally:
        conn.close()
    _mem(tmp_db, "The gateway uses port 9090.")
    now_ids = [e["id"] for e in get_timeline(project="acme", days=60, db_path=tmp_db)["events"]]
    assert old not in now_ids
    as_of = (then + timedelta(days=5)).isoformat()
    past = get_timeline(project="acme", days=60, as_of=as_of, db_path=tmp_db)
    assert [e["id"] for e in past["events"]] == [old]
