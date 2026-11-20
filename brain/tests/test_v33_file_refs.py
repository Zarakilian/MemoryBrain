"""3.3 file references: source files are recognised, product names are not, a
bare name is resolved by what else the memory names, and a ref can say where
in the file and at which commit."""
import json

import pytest

from app.mcp import tools as T
from app.models import MemoryEntry
from app.storage import add_memory
from app.workspace import paths
from app.workspace import resolve as rs
from app.workspace import store as ws


def _seed(db):
    ws.apply_manifest({"machine": "m", "root_id": "git", "abs_path": r"C:\work\repos", "full": True,
                       "files": [
                           {"rel_path": "Support/TICKET-1 - Late export/investigation-findings.md", "sha256": "a"},
                           {"rel_path": "Support/TICKET-1 - Late export/sql/check.sql", "sha256": "b"},
                           {"rel_path": "Support/TICKET-2 - Missing rows/investigation-findings.md", "sha256": "c"},
                           {"rel_path": "Support/TICKET-2 - Missing rows/draft-reply.md", "sha256": "d"},
                           {"rel_path": "Game/src/hook.ts", "sha256": "e"},
                           {"rel_path": "Game/src/net/server.ts", "sha256": "f"},
                           {"rel_path": "Api/Deposit.Api/Cache.cs", "sha256": "g"},
                       ], "markers": []}, db_path=db)
    ws.bind_folder("git", "Support", "support", "tool", db_path=db)
    ws.bind_folder("git", "Game", "game", "tool", db_path=db)


def _fid(db, rel):
    return ws.get_file(db_path=db, root_id="git", rel_path=rel)["file_id"]


def _edges(db, text, project="support"):
    return {e["meta"]["token"]: e for e in rs.file_ref_edges("m1", project, text, db)}


# ------------------------------------------------------------------- tokens

def test_source_and_config_files_are_path_tokens():
    text = ("Changed src/net/server.ts, Api/Deposit.Api/Cache.cs and build/app.config, "
            "then read C:\\work\\logs\\run.log and docs/spec.pdf.")
    assert paths.extract_path_tokens(text) == [
        "src/net/server.ts", "Api/Deposit.Api/Cache.cs", "build/app.config",
        "C:\\work\\logs\\run.log", "docs/spec.pdf"]


def test_the_longest_extension_wins():
    assert paths.extract_path_tokens("see data/rows.json and src/a.cs and src/b.c") == [
        "data/rows.json", "src/a.cs", "src/b.c"]


def test_product_names_are_not_files():
    assert paths.extract_path_tokens("Built with Node.js, Three.js and Next.js on the server") == []


def test_a_bare_code_name_is_told_apart_from_a_document():
    assert paths.is_bare_code_name("server.ts") and paths.is_bare_code_name("error.log")
    assert not paths.is_bare_code_name("notes.md")
    assert not paths.is_bare_code_name("src/server.ts")


# ----------------------------------------------------------- derived edges

def test_a_bare_code_name_links_only_when_it_resolves(tmp_db):
    _seed(tmp_db)
    got = _edges(tmp_db, "Fixed hook.ts and ghost.ts, see error.log", project="game")
    assert set(got) == {"hook.ts"}
    assert got["hook.ts"]["dst_id"] == _fid(tmp_db, "Game/src/hook.ts")


def test_an_unresolved_document_name_still_dangles(tmp_db):
    _seed(tmp_db)
    got = _edges(tmp_db, "Wrote notes-for-later.md")
    assert got["notes-for-later.md"]["dst_kind"] == "dangling"


def test_a_bare_name_takes_the_copy_beside_another_named_file(tmp_db):
    _seed(tmp_db)
    got = _edges(tmp_db, "Ran Support/TICKET-1 - Late export/sql/check.sql and wrote "
                         "investigation-findings.md")
    edge = got["investigation-findings.md"]
    assert edge["dst_kind"] == "file" and edge["meta"]["how"] == "sibling"
    assert edge["dst_id"] == _fid(tmp_db, "Support/TICKET-1 - Late export/investigation-findings.md")
    assert edge["weight"] == 0.7 and edge["meta"]["candidates"] == []


def test_a_bare_name_with_no_context_keeps_its_candidates(tmp_db):
    _seed(tmp_db)
    edge = _edges(tmp_db, "Wrote investigation-findings.md")["investigation-findings.md"]
    assert edge["dst_kind"] == "dangling" and len(edge["meta"]["candidates"]) == 2


def test_context_that_fits_both_copies_resolves_nothing(tmp_db):
    _seed(tmp_db)
    got = _edges(tmp_db, "Ran Support/TICKET-1 - Late export/sql/check.sql and "
                         "Support/TICKET-2 - Missing rows/draft-reply.md, then "
                         "investigation-findings.md")
    assert got["investigation-findings.md"]["dst_kind"] == "dangling"


def test_link_memory_files_stores_the_sibling_edge(tmp_db):
    _seed(tmp_db)
    entry = MemoryEntry(content="Ran Support/TICKET-2 - Missing rows/draft-reply.md and "
                                "investigation-findings.md", type="note", project="support")
    add_memory(entry, db_path=tmp_db)
    rs.link_memory_files(entry, tmp_db)
    ctx = ws.file_context(
        _fid(tmp_db, "Support/TICKET-2 - Missing rows/investigation-findings.md"), tmp_db)
    assert [(m["id"], m["how"]) for m in ctx["memories"]] == [(entry.id, "sibling")]


# ------------------------------------------------------------ explicit refs

def test_a_folder_ref_says_which_copy_of_a_bare_name_is_meant(tmp_db):
    _seed(tmp_db)
    refs = rs.clean_refs([{"path": "Support/TICKET-2 - Missing rows", "kind": "directory"},
                          {"path": "investigation-findings.md", "kind": "file"}])
    edges = rs.explicit_ref_edges("m1", "support", refs, tmp_db)
    by_kind = {e["meta"]["ref_kind"]: e for e in edges}
    assert by_kind["file"]["dst_id"] == _fid(
        tmp_db, "Support/TICKET-2 - Missing rows/investigation-findings.md")
    assert by_kind["file"]["meta"]["how"] == "sibling" and by_kind["file"]["meta"]["explicit"]
    assert by_kind["directory"]["dst_kind"] == "dangling"


def test_a_ref_can_carry_a_line_and_a_commit(tmp_db):
    assert rs.clean_refs([{"path": "src/a.py", "kind": "file", "line": "42-60",
                           "commit": "3F9A2C1D"}]) == [
        {"path": "src/a.py", "kind": "file", "line": "42-60", "commit": "3f9a2c1d"}]


@pytest.mark.parametrize("bad", [{"line": "around the top"}, {"line": "4-"},
                                 {"commit": "not-a-hash"}, {"commit": "abc"}])
def test_a_bad_line_or_commit_is_refused(bad):
    with pytest.raises(ValueError):
        rs.clean_refs([{"path": "src/a.py", "kind": "file", **bad}])


async def test_add_memory_stores_the_line_and_commit_and_file_context_shows_them(
        tmp_db, fake_provider, monkeypatch):
    monkeypatch.setattr("app.ingest_pipeline.DB_PATH", tmp_db)
    monkeypatch.setattr("app.mcp.tools.DB_PATH", tmp_db)
    _seed(tmp_db)
    reply = json.loads((await T.call_tool("add_memory", {
        "content": "The reconnect loop lives in the hook", "type": "fact", "project": "game",
        "refs": [{"path": "Game/src/hook.ts", "kind": "file", "line": "120-148",
                  "commit": "3f9a2c1d"}]}))[0].text)
    assert reply["refs"] == {"file": 1, "dangling": 0}
    ctx = json.loads(await T.handle_get_file_context(path="Game/src/hook.ts"))
    (memory,) = ctx["memories"]
    assert (memory["id"], memory["line"], memory["commit"]) == (reply["id"], "120-148", "3f9a2c1d")
