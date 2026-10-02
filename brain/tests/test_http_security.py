"""v3 HTTP surface: loopback Host only, no drive-by writes, no GET side effects,
and every existing client keeps working."""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from app.db import connect
from app.main import app
from app.models import MemoryEntry
from app.storage import add_memory, get_memory


@pytest.fixture
def client(tmp_db, fake_provider, monkeypatch):
    monkeypatch.delenv("BRAIN_API_KEY", raising=False)
    monkeypatch.setattr("app.ingest_pipeline.DB_PATH", tmp_db)
    monkeypatch.setattr("app.main.DB_PATH", tmp_db)
    monkeypatch.setattr("app.ui.editor.DB_PATH", tmp_db)
    return TestClient(app, base_url="http://localhost:7741")


def _count(db):
    conn = connect(db)
    try:
        return conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    finally:
        conn.close()


# ------------------------------------------------------------- Host check (S4)

@pytest.mark.parametrize("method,path", [("get", "/api/ui/projects"), ("post", "/ingest/note"),
                                         ("get", "/sse"), ("get", "/health")])
def test_a_foreign_host_is_refused(client, method, path):
    kwargs = {"json": {"content": "x", "project": "acme"}} if method == "post" else {}
    r = getattr(client, method)(path, headers={"Host": "evil.example"}, **kwargs)
    assert r.status_code == 421


@pytest.mark.parametrize("host", ["localhost:7741", "127.0.0.1:7741", "[::1]:7741", "localhost"])
def test_loopback_hosts_pass(client, host):
    assert client.get("/health", headers={"Host": host}).status_code == 200


def test_extra_hosts_come_from_the_environment(client, monkeypatch):
    monkeypatch.setenv("MEMORYBRAIN_ALLOWED_HOSTS", "brain.internal, testserver")
    assert client.get("/health", headers={"Host": "brain.internal:7741"}).status_code == 200


def test_host_names_parse_with_ports_and_ipv6():
    from app.security import host_name
    assert host_name("LocalHost:7741") == "localhost"
    assert host_name("[::1]:7741") == "[::1]"
    assert host_name("[::1]") == "[::1]"
    assert host_name("127.0.0.1") == "127.0.0.1"


@pytest.mark.parametrize("header", ["[::1]evil", "[::1]:", "[::1]:77x1"])
def test_junk_after_an_ipv6_bracket_is_not_loopback(header):
    from app.security import allowed_hosts, host_name
    assert host_name(header) not in allowed_hosts()


def test_a_bare_ipv6_extra_host_matches_its_bracketed_header(client, monkeypatch):
    monkeypatch.setenv("MEMORYBRAIN_ALLOWED_HOSTS", "fe80::1")
    assert client.get("/health", headers={"Host": "[fe80::1]:7741"}).status_code == 200


def test_extra_hosts_reach_the_mcp_transports_too(monkeypatch):
    from app.main import _streamable_security
    from app.security import mcp_transport_security
    assert "testserver:*" in _streamable_security.allowed_hosts  # built from allowed_hosts()
    monkeypatch.setenv("MEMORYBRAIN_ALLOWED_HOSTS", "brain.internal, fe80::1")
    settings = mcp_transport_security()
    assert {"localhost:*", "[::1]:*", "brain.internal", "brain.internal:*",
            "[fe80::1]", "[fe80::1]:*"} <= set(settings.allowed_hosts)
    assert {"http://localhost:*", "http://brain.internal:*"} <= set(settings.allowed_origins)
    assert settings.enable_dns_rebinding_protection


@pytest.mark.asyncio
async def test_a_model_validation_error_is_a_422_not_a_500():
    import json as _json
    from app.main import validation_error_handler
    from app.models import ValidationError
    assert app.exception_handlers[ValidationError] is validation_error_handler
    resp = await validation_error_handler(None, ValidationError("type must be one of: fact"))
    assert resp.status_code == 422
    assert _json.loads(resp.body) == {"detail": "type must be one of: fact"}


# ------------------------------------------------------------- write guard (W1)

def test_plain_text_write_is_refused_and_stores_nothing(client, tmp_db):
    r = client.post("/ingest/note", content=b"content=x&project=acme",
                    headers={"Content-Type": "text/plain"})
    assert r.status_code == 403
    assert r.json() == {"detail": "write needs Content-Type application/json or an "
                                  "X-Brain-Client header"}
    assert _count(tmp_db) == 0


def test_a_write_without_content_type_is_refused(client, tmp_db):
    r = client.post("/ingest/note", content=b'{"content": "x", "project": "acme"}')
    assert r.status_code == 403 and _count(tmp_db) == 0


def test_json_with_parameters_passes(client):
    r = client.post("/ingest/note", content=b'{"content": "hello there", "project": "acme"}',
                    headers={"Content-Type": "application/json; charset=utf-8"})
    assert r.status_code == 201


def test_a_bodyless_admin_post_needs_the_client_header(client):
    with patch("app.scheduler.run_auto_consolidate", AsyncMock(return_value={"ran": False})):
        assert client.post("/admin/auto-consolidate").status_code == 403
        assert client.post("/admin/auto-consolidate",
                           headers={"X-Brain-Client": "cli"}).status_code == 200


def test_with_a_key_set_the_key_rules_apply(client, monkeypatch):
    monkeypatch.setenv("BRAIN_API_KEY", "k" * 12)
    assert client.post("/ingest/note", json={"content": "x", "project": "acme"}).status_code == 401
    ok = client.post("/ingest/note", json={"content": "keyed note", "project": "acme"},
                     headers={"X-Brain-Key": "k" * 12})
    assert ok.status_code == 201


# ------------------------------------------------------------- replayed clients

def test_the_2x_session_hook_bind_request_still_works(client):
    r = client.post("/workspace/bind", json={"project": "acme", "how": "cwd",
                                             "abs_path": "/home/you/work/acme"})
    assert r.status_code == 200


def test_the_2x_pre_compact_hook_and_grok_fallback_still_work(client):
    for source in ("pre-compact-auto", "grok"):
        r = client.post("/ingest/session", json={"content": f"session from {source}",
                                                 "project": "acme", "source": source})
        assert r.status_code == 201, source


def test_the_cli_note_request_still_works(client):
    r = client.post("/ingest/note", json={"content": "cli note", "project": "acme",
                                          "tags": ["cli"], "source": "brain-cli"})
    assert r.status_code == 201


def test_the_atlas_bodyless_archive_still_works(client, tmp_db):
    entry = MemoryEntry(content="archive me", type="note", project="acme", importance=3)
    add_memory(entry, db_path=tmp_db)
    r = client.post(f"/api/ui/edit/memories/{entry.id}/archive",
                    headers={"X-Brain-Client": "atlas"})
    assert r.status_code == 200
    assert get_memory(entry.id, db_path=tmp_db).status == "archived"


def test_the_atlas_fetch_wrapper_sends_the_client_header():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "app/static/js/editor.js").read_text(encoding="utf-8")
    assert js.count('"X-Brain-Client"') >= 2  # the shared wrapper and the recall ping


# ------------------------------------------------------------- no GET side effects

def test_rest_inbox_does_not_mark_messages_read(client):
    created = client.post("/exchange/threads", json={
        "project": "acme", "title": "Check the export", "body": "please review",
        "from_agent": "claude", "to_agent": "grok"})
    assert created.status_code == 201
    first = client.get("/exchange/inbox", params={"agent": "grok"}).json()
    again = client.get("/exchange/inbox", params={"agent": "grok"}).json()
    assert first["total"] == 1 and again["total"] == 1


def test_a_get_inbox_cannot_mark_messages_read_but_a_post_can(client):
    client.post("/exchange/threads", json={
        "project": "acme", "title": "Check the export", "body": "please review",
        "from_agent": "claude", "to_agent": "grok"})
    refused = client.get("/exchange/inbox", params={"agent": "grok", "mark_read": "true"})
    assert refused.status_code == 422 and "POST" in refused.json()["detail"]
    assert client.get("/exchange/inbox", params={"agent": "grok"}).json()["total"] == 1
    read = client.post("/exchange/inbox", json={"agent": "grok"}).json()
    assert read["total"] == 1
    assert client.post("/exchange/inbox", json={"agent": "grok"}).json()["total"] == 0


def test_messages_after_the_first_twenty_arrive_next_time(tmp_db):
    from app.exchange import get_inbox, post_task, reply_to_thread

    t = post_task(project="acme", title="Long thread", body="m1", from_agent="claude",
                  to_agent="grok", db_path=tmp_db)
    for i in range(2, 22):
        reply_to_thread(t["thread_id"], f"m{i}", from_agent="claude", to_agent="grok",
                        db_path=tmp_db)
    first = get_inbox("grok", mark_read=True, db_path=tmp_db)
    assert first["threads"][0]["unread_count"] == 20
    second = get_inbox("grok", mark_read=True, db_path=tmp_db)
    assert [m["body"] for m in second["threads"][0]["unread_messages"]] == ["m21"]


def test_obsidian_export_is_a_post(client):
    assert client.get("/admin/export/obsidian", params={"project": "acme"}).status_code == 405


# ------------------------------------------------------------- exchange (W6)

def test_agent_aliases_match_exactly():
    from app.exchange import normalize_agent
    assert normalize_agent("not-claude") == "not-claude"
    for name, canonical in [("Claude", "claude"), ("claude-code", "claude"),
                            ("anthropic", "claude"), ("xai", "grok"), ("ChatGPT", "codex"),
                            ("openai", "codex"), ("google", "gemini"),
                            ("antigravity", "gemini")]:
        assert normalize_agent(name) == canonical, name


def test_exchange_limits(tmp_db):
    from app.exchange import post_task
    ok = dict(project="acme", title="t", body="b", from_agent="claude", db_path=tmp_db)
    for bad in ({"title": "t" * 201}, {"body": "b" * 20_001}, {"refs": ["r"] * 51},
                {"refs": ["r" * 301]}, {"project": "Not A Slug"}):
        with pytest.raises(ValueError):
            post_task(**{**ok, **bad})


# ------------------------------------------------------------- obsidian import (W9)

def test_obsidian_import_must_stay_inside_the_import_folder(client, tmp_path, monkeypatch):
    (tmp_path / "imports").mkdir()
    monkeypatch.setenv("MEMORYBRAIN_IMPORT_DIR", str(tmp_path / "imports"))
    r = client.post("/admin/import/obsidian", params={"directory": str(tmp_path)},
                    headers={"X-Brain-Client": "cli"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_obsidian_import_skips_big_files_and_ignores_privileged_front_matter(
        tmp_db, fake_provider, tmp_path, monkeypatch):
    from app.obsidian import import_markdown_dir

    vault = tmp_path / "imports" / "vault"
    vault.mkdir(parents=True)
    (vault / "a.md").write_text("---\ntype: belief\nsource: someone-else\ntrust: user\n"
                                "writer: admin\n---\nThe car is red.\n", encoding="utf-8")
    (vault / "big.md").write_text("x" * (1_048_576 + 1), encoding="utf-8")
    monkeypatch.setenv("MEMORYBRAIN_IMPORT_DIR", str(tmp_path / "imports"))
    monkeypatch.setattr("app.ingest_pipeline.DB_PATH", tmp_db)
    report = await import_markdown_dir(vault, project="acme", db_path=tmp_db)
    assert report["imported"] == 1
    assert report["too_large"] == ["big.md"]
    entry = get_memory(report["items"][0]["id"], db_path=tmp_db)
    assert entry.type == "note"
    assert (entry.trust, entry.writer) == ("imported", "obsidian-import")
    assert entry.source == "obsidian:a.md"


# ------------------------------------------------------------- workspace (W7, W10)

def test_url_userinfo_is_stripped():
    from app.redact import strip_url_userinfo
    assert strip_url_userinfo("https://user:tok3n@git.example.com/acme/app.git") == \
        "https://git.example.com/acme/app.git"
    assert strip_url_userinfo("git@git.example.com:acme/app.git") == \
        "git@git.example.com:acme/app.git"
    assert strip_url_userinfo("") == ""


def test_remote_url_is_stored_and_returned_without_userinfo(tmp_db):
    import app.workspace.store as ws
    ws.bind_folder("root-1", "acme", "acme", "init", db_path=tmp_db,
                   remote_url="https://user:tok3n@git.example.com/acme/app.git")
    ws.record_folder_seen("root-1", "other", "https://u:p@git.example.com/x.git", db_path=tmp_db)
    text = str(ws.workspace_map(db_path=tmp_db))
    assert "tok3n" not in text and "u:p@" not in text
    assert "https://git.example.com/acme/app.git" in text


@pytest.mark.parametrize("body", [
    {"project": "acme", "how": "cwd", "root_id": "r", "rel_path": "../x"},
    {"project": "acme", "how": "cwd", "root_id": "r", "rel_path": "/etc"},
    {"project": "acme", "how": "marker", "root_id": "r", "rel_path": "acme"},
])
def test_workspace_bind_rejects_escapes_and_unknown_sources(client, body):
    assert client.post("/workspace/bind", json=body).status_code == 422


@pytest.mark.parametrize("how", ["cwd", "memory"])
def test_workspace_bind_rejects_dotdot_inside_an_absolute_path(client, tmp_db, how):
    from app.workspace import store as ws
    ws.upsert_root("root-1", "WORK-PC", "/work/repos", db_path=tmp_db)
    r = client.post("/workspace/bind", json={"project": "acme", "how": how,
                                             "abs_path": "/work/repos/../secrets"})
    assert r.status_code == 422
    assert ws.list_folders(tmp_db) == []


# ------------------------------------------------------------- version (O5)

def test_status_reports_the_version_file(client):
    from app.main import read_version
    assert client.get("/status").json()["version"] == read_version()
    assert read_version() != "unknown"


# ------------------------------------------------- MCP needs the key too (S2)

_MCP_HEADERS = {"Accept": "application/json, text/event-stream",
                "Content-Type": "application/json", "mcp-protocol-version": "2025-06-18"}


def _mcp_list(client, extra=None):
    """tools/list over /mcp. The session manager runs once per process, so
    these tests check the door (401 or let through), not the MCP answer."""
    c = TestClient(client.app, base_url="http://localhost:7741", raise_server_exceptions=False)
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    return c.post("/mcp/", json=body, headers={**_MCP_HEADERS, **(extra or {})})


@pytest.mark.parametrize("method,path", [("get", "/sse"), ("post", "/messages/"),
                                         ("post", "/mcp/"), ("post", "/mcp")])
def test_with_a_key_set_every_mcp_door_needs_it(client, monkeypatch, method, path):
    monkeypatch.setenv("BRAIN_API_KEY", "fake-test-key-123456")
    kwargs = {"json": {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
              "headers": _MCP_HEADERS} if method == "post" else {}
    c = TestClient(client.app, base_url="http://localhost:7741", raise_server_exceptions=False)
    r = getattr(c, method)(path, **kwargs)
    assert r.status_code == 401


def test_an_mcp_client_with_the_key_or_a_bearer_token_gets_in(client, monkeypatch):
    monkeypatch.setenv("BRAIN_API_KEY", "fake-test-key-123456")
    assert _mcp_list(client, {"X-Brain-Key": "fake-test-key-123456"}).status_code != 401
    assert _mcp_list(client, {"Authorization": "Bearer " + "fake-test-key-123456"}).status_code != 401
    assert _mcp_list(client, {"Authorization": "Bearer wrong"}).status_code == 401


def test_a_bearer_token_works_on_rest_too(client, monkeypatch):
    monkeypatch.setenv("BRAIN_API_KEY", "fake-test-key-123456")
    r = client.get("/status", headers={"Authorization": "Bearer " + "fake-test-key-123456"})
    assert r.status_code == 200


def test_mcp_can_be_left_open_on_purpose(client, monkeypatch):
    monkeypatch.setenv("BRAIN_API_KEY", "fake-test-key-123456")
    monkeypatch.setenv("MEMORYBRAIN_MCP_KEY", "off")
    assert _mcp_list(client).status_code != 401
    assert client.post("/ingest/note", json={"content": "x", "project": "acme"}).status_code == 401


def test_without_a_key_mcp_stays_open(client):
    assert _mcp_list(client).status_code != 401


def test_readiness_says_when_mcp_is_left_open_beside_a_key(client, monkeypatch):
    with patch("app.main.ollama_client", None):
        assert client.get("/readiness").json()["security_warning"] is None
        monkeypatch.setenv("BRAIN_API_KEY", "fake-test-key-123456")
        assert client.get("/readiness").json()["security_warning"] is None
        monkeypatch.setenv("MEMORYBRAIN_MCP_KEY", "off")
        warning = client.get("/readiness").json()["security_warning"]
    assert "MEMORYBRAIN_MCP_KEY" in warning and "fake-test-key" not in warning


@pytest.mark.asyncio
async def test_obsidian_import_never_follows_a_link_out_of_the_folder(
        tmp_db, fake_provider, tmp_path, monkeypatch):
    from app.obsidian import import_markdown_dir
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("Outside the import folder.\n", encoding="utf-8")
    vault = tmp_path / "imports" / "vault"
    vault.mkdir(parents=True)
    (vault / "ok.md").write_text("Inside the folder.\n", encoding="utf-8")
    try:
        (vault / "link.md").symlink_to(outside / "secret.md")
        (vault / "env.md").symlink_to("/proc/self/environ")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available here")
    monkeypatch.setenv("MEMORYBRAIN_IMPORT_DIR", str(tmp_path / "imports"))
    monkeypatch.setattr("app.ingest_pipeline.DB_PATH", tmp_db)
    report = await import_markdown_dir(vault, project="acme", db_path=tmp_db)
    assert report["imported"] == 1
    assert [i["file"] for i in report["items"]] == ["ok.md"]


@pytest.mark.asyncio
async def test_obsidian_front_matter_cannot_make_a_fact_or_set_importance(
        tmp_db, fake_provider, tmp_path, monkeypatch):
    from app.obsidian import import_markdown_dir
    vault = tmp_path / "imports" / "vault"
    vault.mkdir(parents=True)
    (vault / "f.md").write_text("---\ntype: fact\nimportance: 5\n---\nThe port is 9999.\n",
                                encoding="utf-8")
    (vault / "d.md").write_text("---\ntype: decision\n---\nWe deploy on Fridays.\n",
                                encoding="utf-8")
    monkeypatch.setenv("MEMORYBRAIN_IMPORT_DIR", str(tmp_path / "imports"))
    monkeypatch.setattr("app.ingest_pipeline.DB_PATH", tmp_db)
    report = await import_markdown_dir(vault, project="acme", db_path=tmp_db)
    entries = [get_memory(i["id"], db_path=tmp_db) for i in report["items"]]
    assert {e.type for e in entries} == {"note"}
    assert all(e.importance != 5 for e in entries)


# ------------------------------------------------- Origin on every door (S4 rest)

@pytest.mark.parametrize("origin", ["http://evil.example", "null", "http://127.0.0.1.nip.io:7741"])
def test_a_foreign_origin_is_refused_on_rest_and_atlas(client, origin):
    for method, path, kw in (("post", "/ingest/note", {"json": {"content": "x", "project": "acme"}}),
                             ("get", "/api/ui/stats", {}),
                             ("post", "/api/ui/edit/notes", {"json": {"content": "x", "project": "acme"}})):
        r = getattr(client, method)(path, headers={"Origin": origin}, **kw)
        assert r.status_code == 403, (path, origin, r.status_code)


@pytest.mark.parametrize("origin", [None, "http://localhost:7741", "http://127.0.0.1:7741",
                                    "http://[::1]:7741"])
def test_a_local_origin_or_none_still_works(client, origin):
    headers = {"Origin": origin} if origin else {}
    r = client.post("/api/ui/edit/notes", json={"content": "a note", "project": "acme"},
                    headers=headers)
    assert r.status_code in (200, 201), r.text


def test_the_sse_transport_keeps_its_rebinding_protection():
    """Taking the settings off SseServerTransport still passed every test."""
    from app.main import sse_transport
    settings = sse_transport._security.settings
    assert settings is not None and settings.enable_dns_rebinding_protection
    assert "localhost:*" in settings.allowed_hosts
    assert "http://localhost:*" in settings.allowed_origins


@pytest.mark.asyncio
async def test_an_sse_client_that_disconnects_leaves_no_error(monkeypatch):
    """The /sse route returned into FastAPI, which then sent a second response
    on a finished stream: one traceback per client disconnect."""
    import asyncio
    from app.main import sse_asgi
    sent, calls = [], 0

    async def receive():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return {"type": "http.disconnect"}

    async def send(message):
        if sent and sent[-1].get("type") == "http.response.body" and not sent[-1].get("more_body"):
            raise RuntimeError(f"sent {message['type']} after the response completed")
        sent.append(message)

    scope = {"type": "http", "method": "GET", "path": "/sse", "raw_path": b"/sse",
             "query_string": b"", "root_path": "", "scheme": "http", "server": ("localhost", 7741),
             "client": ("127.0.0.1", 5000), "headers": [(b"host", b"localhost:7741")],
             "http_version": "1.1"}
    await asyncio.wait_for(sse_asgi(scope, receive, send), timeout=10)
    assert sent and sent[0]["type"] == "http.response.start" and sent[0]["status"] == 200


@pytest.mark.asyncio
async def test_sse_answers_405_to_anything_but_get():
    from app.main import sse_asgi
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)
    scope = {"type": "http", "method": "POST", "path": "/sse", "headers": []}
    await sse_asgi(scope, receive, send)
    assert sent[0]["status"] == 405


def test_a_message_post_gets_exactly_one_response(client):
    """handle_post_message answers by itself; the FastAPI endpoint then sent
    a second response, which raised on every MCP message over SSE."""
    from app.main import messages_asgi
    import asyncio
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message):
        sent.append(message)
    scope = {"type": "http", "method": "POST", "path": "/messages/", "raw_path": b"/messages/",
             "query_string": b"session_id=" + b"0" * 32, "root_path": "", "scheme": "http",
             "server": ("localhost", 7741), "client": ("127.0.0.1", 5000),
             "headers": [(b"host", b"localhost:7741"), (b"content-type", b"application/json")],
             "http_version": "1.1"}
    asyncio.run(messages_asgi(scope, receive, send))
    assert [m["type"] for m in sent].count("http.response.start") == 1
    r = client.post("/messages/?session_id=" + "0" * 32, json={"jsonrpc": "2.0"})
    assert r.status_code in (400, 404)
