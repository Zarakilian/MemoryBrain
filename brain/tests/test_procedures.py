"""v3 learning: an agent records how the user wants things done; only the user
makes a rule official."""
import json

import pytest

from app.db import connect
from app.procedures import (active_procedures, confirm_procedure, proposed_procedures,
                            record_correction, reject_procedure)


def _audit(db, memory_id):
    conn = connect(db)
    try:
        return [r["action"] for r in conn.execute(
            "SELECT action FROM memory_audit WHERE memory_id = ? ORDER BY at", (memory_id,))]
    finally:
        conn.close()


def test_a_correction_is_a_proposed_procedure_until_confirmed(tmp_db):
    out = record_correction("Always open a pull request; never push to master.",
                            evidence="user: do not push straight to master", project="acme",
                            writer="claude", db_path=tmp_db)
    assert out["status"] == "proposed" and out["duplicate"] is False
    assert [p["id"] for p in proposed_procedures(db_path=tmp_db)] == [out["id"]]
    assert active_procedures("acme", db_path=tmp_db) == []
    assert confirm_procedure(out["id"], actor="user", db_path=tmp_db)
    (rule,) = active_procedures("acme", db_path=tmp_db)
    assert rule["id"] == out["id"] and rule["trust"] == "user"
    assert _audit(tmp_db, out["id"]) == ["confirm"]


def test_system_rules_apply_to_every_project(tmp_db):
    out = record_correction("Reply in plain English, short sentences.", db_path=tmp_db)
    confirm_procedure(out["id"], actor="user", db_path=tmp_db)
    assert [p["id"] for p in active_procedures("any-project", db_path=tmp_db)] == [out["id"]]


def test_rejecting_archives_and_audits(tmp_db):
    out = record_correction("Use tabs for indentation.", project="acme", db_path=tmp_db)
    assert reject_procedure(out["id"], actor="user", db_path=tmp_db)
    assert proposed_procedures(db_path=tmp_db) == []
    assert active_procedures("acme", db_path=tmp_db) == []
    assert _audit(tmp_db, out["id"]) == ["reject"]


def test_the_same_rule_twice_is_a_duplicate(tmp_db):
    first = record_correction("Keep answers short.", evidence="one", project="acme", db_path=tmp_db)
    again = record_correction("Keep  answers short.", evidence="two", project="acme", db_path=tmp_db)
    assert again["duplicate"] is True and again["id"] == first["id"]
    assert len(proposed_procedures(db_path=tmp_db)) == 1


def test_limits(tmp_db):
    with pytest.raises(ValueError):
        record_correction("x" * 501, db_path=tmp_db)
    with pytest.raises(ValueError):
        record_correction("fine rule", evidence="y" * 1001, db_path=tmp_db)
    with pytest.raises(ValueError):
        record_correction("   ", db_path=tmp_db)


@pytest.mark.asyncio
async def test_mcp_can_record_but_never_confirm(tmp_db, monkeypatch):
    from app.mcp.tools import TOOL_NAMES, handle_record_correction

    monkeypatch.setattr("app.mcp.tools.DB_PATH", tmp_db)
    reply = json.loads(await handle_record_correction("Name branches feature/<topic>.",
                                                      project="acme"))
    assert reply["status"] == "proposed"
    assert "record_correction" in TOOL_NAMES
    assert not any(("confirm" in n or "approve" in n) and "procedure" in n for n in TOOL_NAMES)


def test_the_ui_confirms_and_rejects_rules(tmp_db, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.delenv("BRAIN_API_KEY", raising=False)
    monkeypatch.setattr("app.ui.editor.DB_PATH", tmp_db)
    monkeypatch.setattr("app.ui.queries.DB_PATH", tmp_db)
    keep = record_correction("Write commit messages in the imperative.", db_path=tmp_db)["id"]
    drop = record_correction("Use emoji in commit titles.", db_path=tmp_db)["id"]
    client = TestClient(app, headers={"X-Brain-Client": "atlas"})
    listed = client.get("/api/ui/procedures", params={"status": "proposed"}).json()
    assert {p["id"] for p in listed["procedures"]} == {keep, drop}
    assert client.post(f"/api/ui/edit/procedures/{keep}/confirm").status_code == 200
    assert client.post(f"/api/ui/edit/procedures/{drop}/reject").status_code == 200
    assert [p["id"] for p in active_procedures("acme", db_path=tmp_db)] == [keep]
