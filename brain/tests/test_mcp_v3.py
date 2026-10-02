"""v3 agent surface: a small core tool list, brain_admin for the rest, the
writer named by the transport, explicit refs, and a size budget for the list."""
import json
from types import SimpleNamespace

import pytest
from mcp.server.lowlevel.server import request_ctx

from app.db import connect
from app.mcp import tools as T
from app.workspace import store as ws

CORE = {"search_memory", "get_memory", "add_memory",
        "get_project_brief", "get_startup_summary", "get_recent_context",
        "pin_memory", "set_project_info", "get_file_context",
        "get_agent_inbox", "post_task", "reply_to_thread", "get_thread",
        "record_correction", "brain_admin"}


@pytest.fixture
def mcp_db(tmp_db, fake_provider, monkeypatch):
    monkeypatch.setattr("app.ingest_pipeline.DB_PATH", tmp_db)
    monkeypatch.setattr("app.mcp.tools.DB_PATH", tmp_db)
    return tmp_db


async def _call(name, args):
    return json.loads((await T.call_tool(name, args))[0].text)


def _ctx(client=None, headers=None):
    """A stand-in for the SDK's request context: the session's initialize
    params (clientInfo) and the HTTP request, when the transport has them."""
    params = SimpleNamespace(clientInfo=SimpleNamespace(name=client)) if client else None
    request = SimpleNamespace(headers=headers) if headers is not None else None
    return SimpleNamespace(session=SimpleNamespace(client_params=params), request=request)


# ------------------------------------------------------------- profiles

async def test_the_core_profile_lists_exactly_the_15_tools(monkeypatch):
    monkeypatch.delenv("MEMORYBRAIN_TOOLS", raising=False)
    tools = await T.list_tools()
    assert {t.name for t in tools} == CORE and len(tools) == 15
    too_long = {t.name: len(t.description) for t in tools if len(t.description) > 200}
    assert too_long == {}


async def test_the_full_profile_lists_every_tool(monkeypatch):
    monkeypatch.setenv("MEMORYBRAIN_TOOLS", "full")
    names = [t.name for t in await T.list_tools()]
    assert set(names) == set(T.TOOL_NAMES) and len(names) == len(T.TOOL_NAMES)
    assert CORE < set(names) and "brain_admin" in T.TOOL_NAMES


async def test_a_hidden_tool_still_works_in_core_mode(mcp_db, monkeypatch):
    monkeypatch.delenv("MEMORYBRAIN_TOOLS", raising=False)
    assert "list_projects" not in {t.name for t in await T.list_tools()}
    reply = (await T.call_tool("list_projects", {}))[0].text
    assert not reply.startswith("Unknown tool")


async def test_the_core_list_fits_in_8000_characters(monkeypatch):
    monkeypatch.delenv("MEMORYBRAIN_TOOLS", raising=False)
    payload = [t.model_dump(exclude_none=True, by_alias=True) for t in await T.list_tools()]
    assert len(json.dumps(payload, separators=(",", ":"))) < 8000


# ------------------------------------------------------------- brain_admin

ADMIN_ROUTES = [
    ("delete_memory", "handle_delete_memory", {"memory_id": "m1"}),
    ("restore_memory", "handle_restore_memory", {"memory_id": "m1"}),
    ("get_related", "handle_get_related_memories", {"memory_id": "m1"}),
    ("get_graph", "handle_get_memory_graph", {}),
    ("get_timeline", "handle_get_timeline", {}),
    ("get_entities", "handle_get_entities", {}),
    ("record_retrieval", "handle_record_retrieval", {"query": "export"}),
    ("list_conflicts", "handle_list_conflicts", {}),
    ("resolve_conflict", "handle_resolve_conflict", {"winner_id": "a", "loser_id": "b"}),
    ("dismiss_conflict", "handle_dismiss_conflict", {"a_id": "a", "b_id": "b"}),
    ("list_pins", "handle_list_pins", {"project": "acme"}),
    ("unpin_memory", "handle_unpin_memory", {"project": "acme", "memory_id": "m1"}),
    ("consolidate", "handle_consolidate_memory", {}),
    ("rebuild_graph", "handle_rebuild_graph", {}),
    ("rebuild_file_links", "handle_rebuild_file_links", {}),
    ("reembed", "handle_reembed", {}),
    ("get_policy", "handle_get_project_policy", {"project": "acme"}),
    ("set_policy", "handle_set_project_policy", {"project": "acme"}),
    ("list_threads", "handle_list_threads", {}),
    ("update_task_status", "handle_update_task_status", {"thread_id": "t1", "status": "done"}),
    ("get_agent_stats", "handle_get_agent_stats", {}),
    ("get_workspace_map", "handle_get_workspace_map", {}),
    ("get_project_files", "handle_get_project_files", {"project": "acme"}),
    ("list_projects", "handle_list_projects", {}),
]


def test_the_admin_table_covers_every_action():
    assert set(T.ADMIN_ACTIONS) == {a for a, _, _ in ADMIN_ROUTES}


@pytest.mark.parametrize("action,handler,args", ADMIN_ROUTES)
async def test_each_admin_action_reaches_its_handler(monkeypatch, action, handler, args):
    seen = []

    async def recorder(*a, **kw):
        seen.append(kw)
        return json.dumps({"routed": handler})

    monkeypatch.setattr(T, handler, recorder)
    assert await _call("brain_admin", {"action": action, "args": args}) == {"routed": handler}
    assert seen == [args] or (seen == [{}] and args == {})


async def test_admin_args_are_validated_like_the_full_tool():
    reply = await _call("brain_admin", {"action": "resolve_conflict", "args": {"winner_id": "a"}})
    assert reply == {"error": "Missing required argument(s): loser_id"}


async def test_an_unknown_admin_action_lists_the_valid_ones():
    reply = await _call("brain_admin", {"action": "drop_everything"})
    assert "drop_everything" in reply["error"]
    assert reply["valid_actions"] == sorted(T.ADMIN_ACTIONS)


async def test_admin_args_must_be_an_object():
    reply = await _call("brain_admin", {"action": "list_pins", "args": ["acme"]})
    assert "args must be an object" in reply["error"]


async def test_admin_restore_brings_an_archived_memory_back(mcp_db):
    stored = await _call("add_memory", {"content": "The export runs nightly at two.",
                                        "type": "fact", "project": "acme"})
    assert (await _call("brain_admin", {"action": "delete_memory",
                                        "args": {"memory_id": stored["id"]}}))["archived"]
    back = await _call("brain_admin", {"action": "restore_memory",
                                       "args": {"memory_id": stored["id"], "reason": "wrong one"}})
    assert back == {"restored": True, "id": stored["id"], "status": "active"}
    conn = connect(mcp_db)
    try:
        status = conn.execute("SELECT status FROM memories WHERE id = ?",
                              (stored["id"],)).fetchone()[0]
    finally:
        conn.close()
    assert status == "active"


# ------------------------------------------------------------- writer identity

def _writer(db, memory_id):
    conn = connect(db)
    try:
        return conn.execute("SELECT writer FROM memories WHERE id = ?", (memory_id,)).fetchone()[0]
    finally:
        conn.close()


@pytest.mark.parametrize("client,headers,source,expected", [
    ("Claude Code", {"x-brain-agent": "grok"}, "codex-cli", "claude-code"),
    (None, {"x-brain-agent": "Grok"}, "codex-cli", "grok"),
    (None, {}, " Codex CLI ", "codex-cli"),
    (None, None, "", "unknown"),
])
async def test_the_writer_comes_from_the_transport_first(mcp_db, client, headers, source, expected):
    token = request_ctx.set(_ctx(client, headers))
    try:
        reply = await _call("add_memory", {"content": f"The export job note for {expected}.",
                                           "type": "note", "project": "acme", "source": source})
    finally:
        request_ctx.reset(token)
    assert reply["writer"] == expected and _writer(mcp_db, reply["id"]) == expected


async def test_outside_a_request_the_writer_is_the_source_or_unknown(mcp_db):
    assert (await _call("add_memory", {"content": "plain note", "type": "note",
                                       "project": "acme"}))["writer"] == "unknown"


async def test_a_correction_records_the_client_that_made_it(mcp_db):
    token = request_ctx.set(_ctx("Claude Code"))
    try:
        rule = await _call("record_correction", {"rule": "Always open a pull request.",
                                                 "project": "acme"})
    finally:
        request_ctx.reset(token)
    assert _writer(mcp_db, rule["id"]) == "claude-code"


async def test_a_pin_is_audited_with_the_client_that_made_it(mcp_db):
    stored = await _call("add_memory", {"content": "Invoices go out on the first.",
                                        "type": "fact", "project": "acme"})
    token = request_ctx.set(_ctx(None, {"x-brain-agent": "codex"}))
    try:
        await _call("pin_memory", {"project": "acme", "memory_id": stored["id"]})
    finally:
        request_ctx.reset(token)
    conn = connect(mcp_db)
    try:
        row = conn.execute("SELECT actor FROM memory_audit WHERE memory_id = ? AND action = 'pin'",
                           (stored["id"],)).fetchone()
    finally:
        conn.close()
    assert row["actor"] == "codex"


async def test_a_thread_keeps_its_from_agent_and_fills_a_blank_one(mcp_db):
    token = request_ctx.set(_ctx("Claude Code"))
    try:
        named = await _call("post_task", {"project": "acme", "title": "Review the export",
                                          "body": "Please review.", "from_agent": "grok"})
        blank = await _call("post_task", {"project": "acme", "title": "Second look",
                                          "body": "Please look.", "from_agent": ""})
    finally:
        request_ctx.reset(token)
    conn = connect(mcp_db)
    try:
        by = dict(conn.execute("SELECT id, created_by FROM agent_threads").fetchall())
    finally:
        conn.close()
    assert by[named["thread_id"]] == "grok" and by[blank["thread_id"]] == "claude"


# ------------------------------------------------------------- explicit refs

def _index_export_file(db):
    ws.apply_manifest({"machine": "WORK-PC", "root_id": "git", "abs_path": "/work/repos",
                       "full": True, "files": [{"rel_path": "api-service/export.py"}],
                       "markers": []}, db_path=db)
    ws.bind_folder("git", "api-service", "acme", "init", db_path=db)
    return ws.get_file(db, root_id="git", rel_path="api-service/export.py")["file_id"]


def _links(db, memory_id):
    conn = connect(db)
    try:
        rows = conn.execute("SELECT dst_kind, dst_id, kind, meta FROM file_links WHERE src_id = ?",
                            (memory_id,)).fetchall()
    finally:
        conn.close()
    return {(r["dst_kind"], r["dst_id"], json.loads(r["meta"]).get("ref_kind")) for r in rows
            if json.loads(r["meta"]).get("explicit")}


async def test_refs_link_files_even_dangling_ones_and_keep_other_kinds(mcp_db):
    file_id = _index_export_file(mcp_db)
    reply = await _call("add_memory", {
        "content": "Fixed the retry loop.", "type": "note", "project": "acme",
        "refs": [{"path": "api-service/export.py", "kind": "file"},
                 {"path": "api-service/missing.py", "kind": "file"},
                 {"path": "https://wiki.example.com/export", "kind": "url"},
                 {"path": "TASK-12", "kind": "task"}]})
    assert reply["refs"] == {"file": 1, "dangling": 3}
    links = _links(mcp_db, reply["id"])
    assert ("file", file_id, "file") in links
    assert ("dangling", "api-service/missing.py", "file") in links
    assert ("dangling", "https://wiki.example.com/export", "url") in links
    assert ("dangling", "TASK-12", "task") in links
    context = ws.file_context(file_id, mcp_db)
    assert reply["id"] in [m["id"] for m in context["memories"]]


async def test_a_file_link_rebuild_keeps_explicit_refs(mcp_db):
    from app.workspace.resolve import rebuild_file_links
    file_id = _index_export_file(mcp_db)
    reply = await _call("add_memory", {"content": "Tuned the job.", "type": "note",
                                       "project": "acme",
                                       "refs": [{"path": "api-service/export.py", "kind": "file"}]})
    rebuild_file_links(db_path=mcp_db)
    assert ("file", file_id, "file") in _links(mcp_db, reply["id"])


@pytest.mark.parametrize("refs,reason", [
    ([{"path": f"api-service/f{i}.py", "kind": "file"} for i in range(26)], "at most 25"),
    ([{"path": "api-service/a.py", "kind": "blob"}], "kind must be one of"),
    ([{"path": "", "kind": "file"}], "path must not be empty"),
    (["api-service/a.py"], "expected object"),  # the schema check speaks first
])
async def test_bad_refs_are_rejected_and_nothing_is_stored(mcp_db, refs, reason):
    reply = await _call("add_memory", {"content": "A note with refs.", "type": "note",
                                       "project": "acme", "refs": refs})
    assert reason in reply["error"]
    conn = connect(mcp_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
    finally:
        conn.close()


# ------------------------------------------------------------- review follow-ups

async def test_admin_args_are_type_checked_like_the_full_tool(mcp_db):
    reply = await _call("brain_admin", {"action": "set_policy",
                                        "args": {"project": "acme", "include_system": "false"}})
    assert "include_system" in reply["error"] and "boolean" in reply["error"]


async def test_a_hidden_tool_called_by_name_is_type_checked(mcp_db, monkeypatch):
    monkeypatch.delenv("MEMORYBRAIN_TOOLS", raising=False)
    reply = await _call("set_project_policy", {"project": "acme", "include_system": "false"})
    assert "boolean" in reply["error"]


async def test_unknown_admin_args_are_named_not_dropped(mcp_db):
    reply = await _call("brain_admin", {"action": "get_timeline", "args": {"proj": "acme"}})
    assert "proj" in reply["error"] and "project" in reply["error"]


def _sdk_request(name, args):
    import mcp.types as types
    return types.CallToolRequest(method="tools/call",
                                 params=types.CallToolRequestParams(name=name, arguments=args))


async def test_the_sdk_routes_a_hidden_tool_without_warning(mcp_db, monkeypatch, caplog):
    import logging
    import mcp.types as types
    monkeypatch.delenv("MEMORYBRAIN_TOOLS", raising=False)
    handler = T.server.request_handlers[types.CallToolRequest]
    with caplog.at_level(logging.WARNING, logger="mcp.server.lowlevel.server"):
        result = (await handler(_sdk_request("list_projects", {}))).root
    assert not result.isError and not result.content[0].text.startswith("Unknown tool")
    assert "not listed" not in caplog.text


async def test_the_sdk_rejects_an_unknown_admin_action_with_the_valid_list(mcp_db, monkeypatch):
    import mcp.types as types
    monkeypatch.delenv("MEMORYBRAIN_TOOLS", raising=False)
    handler = T.server.request_handlers[types.CallToolRequest]
    result = (await handler(_sdk_request("brain_admin", {"action": "drop_everything"}))).root
    assert result.isError and "list_conflicts" in result.content[0].text


async def test_a_reply_with_a_blank_sender_is_filled_from_the_client(mcp_db):
    thread = await _call("post_task", {"project": "acme", "title": "Review", "body": "Please.",
                                       "from_agent": "grok"})
    token = request_ctx.set(_ctx("Claude Code"))
    try:
        await _call("reply_to_thread", {"thread_id": thread["thread_id"], "body": "Done.",
                                        "from_agent": ""})
    finally:
        request_ctx.reset(token)
    conn = connect(mcp_db)
    try:
        senders = [r[0] for r in conn.execute(
            "SELECT from_agent FROM agent_messages WHERE thread_id = ? ORDER BY created_at",
            (thread["thread_id"],))]
    finally:
        conn.close()
    assert senders == ["grok", "claude"]


def _audit_rows(db, memory_id):
    conn = connect(db)
    try:
        return [(r["action"], r["actor"]) for r in conn.execute(
            "SELECT action, actor FROM memory_audit WHERE memory_id = ? ORDER BY at",
            (memory_id,))]
    finally:
        conn.close()


async def test_unpin_archive_and_restore_record_the_client(mcp_db):
    stored = await _call("add_memory", {"content": "Invoices go out on the first.",
                                        "type": "fact", "project": "acme"})
    token = request_ctx.set(_ctx("Claude Code"))
    try:
        await _call("pin_memory", {"project": "acme", "memory_id": stored["id"]})
        await _call("brain_admin", {"action": "unpin_memory",
                                    "args": {"project": "acme", "memory_id": stored["id"]}})
        await _call("brain_admin", {"action": "delete_memory", "args": {"memory_id": stored["id"]}})
        await _call("brain_admin", {"action": "restore_memory", "args": {"memory_id": stored["id"]}})
    finally:
        request_ctx.reset(token)
    rows = _audit_rows(mcp_db, stored["id"])
    assert ("pin", "claude-code") in rows and ("unpin", "claude-code") in rows
    assert ("archive", "mcp:claude-code") in rows and ("restore", "mcp:claude-code") in rows


async def test_prose_and_an_explicit_ref_to_the_same_file_make_one_explicit_link(mcp_db):
    file_id = _index_export_file(mcp_db)
    reply = await _call("add_memory", {
        "content": "Changed api-service/export.py to retry twice.", "type": "note",
        "project": "acme", "refs": [{"path": "api-service/export.py", "kind": "file"}]})
    conn = connect(mcp_db)
    try:
        rows = conn.execute("SELECT dst_id, meta FROM file_links WHERE src_id = ? AND dst_kind = 'file'",
                            (reply["id"],)).fetchall()
    finally:
        conn.close()
    assert [r["dst_id"] for r in rows] == [file_id]
    assert json.loads(rows[0]["meta"])["explicit"] is True


async def test_a_dangling_explicit_ref_resolves_once_the_file_is_indexed(mcp_db):
    from app.workspace.resolve import rebuild_file_links
    _index_export_file(mcp_db)
    reply = await _call("add_memory", {"content": "Started the new job.", "type": "note",
                                       "project": "acme",
                                       "refs": [{"path": "api-service/later.py", "kind": "file"}]})
    assert ("dangling", "api-service/later.py", "file") in _links(mcp_db, reply["id"])
    ws.apply_manifest({"machine": "WORK-PC", "root_id": "git", "abs_path": "/work/repos",
                       "full": False, "files": [{"rel_path": "api-service/later.py"}],
                       "markers": []}, db_path=mcp_db)
    later = ws.get_file(mcp_db, root_id="git", rel_path="api-service/later.py")["file_id"]
    report = rebuild_file_links(db_path=mcp_db)
    links = _links(mcp_db, reply["id"])
    assert ("file", later, "file") in links
    assert not any(kind == "dangling" for kind, _, _ in links)
    assert report["explicit_resolved"] == 1
    assert reply["id"] in [m["id"] for m in ws.file_context(later, mcp_db)["memories"]]


async def test_urls_that_differ_only_in_case_stay_two_refs(mcp_db):
    reply = await _call("add_memory", {
        "content": "Two wiki pages.", "type": "note", "project": "acme",
        "refs": [{"path": "https://wiki.example.com/Export", "kind": "url"},
                 {"path": "https://wiki.example.com/export", "kind": "url"}]})
    assert reply["refs"] == {"file": 0, "dangling": 2}



# ------------------------------------------------------------- final review

@pytest.mark.parametrize("kind", ["belief", "procedure"])
async def test_an_agent_cannot_write_a_belief_or_a_rule_directly(mcp_db, kind):
    reply = await _call("add_memory", {"content": "The export is fragile.", "type": kind,
                                       "project": "acme"})
    assert "error" in reply
    reply = json.loads(await T.handle_add_memory("The export is fragile.", kind, "acme"))
    assert kind in reply["error"]


async def test_a_client_cannot_take_an_internal_writer_name(mcp_db):
    token = request_ctx.set(_ctx("UI"))
    try:
        reply = await _call("add_memory", {"content": "A note signed ui.", "type": "note",
                                           "project": "acme"})
    finally:
        request_ctx.reset(token)
    assert reply["writer"] == "mcp:ui"
