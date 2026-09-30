import json
from datetime import datetime, timezone
from types import SimpleNamespace

from app.models import MemoryEntry, Project
from app.storage import add_memory, upsert_project, _connect
from app.workspace import store as ws
from app.workspace import resolve as rs


def _seed(tmp_db):
    ws.apply_manifest({"machine": "m", "root_id": "git", "abs_path": r"C:\work\repos", "full": True,
                       "files": [
                           {"rel_path": "Daily Reports/TODO-DailyReports.md", "sha256": "a"},
                           {"rel_path": "Daily Reports/docs/open-questions.md", "sha256": "b"},
                           {"rel_path": "Daily Reports/Tools/ReportFlow/README.md", "sha256": "c"},
                           {"rel_path": "Daily Reports/README.md", "sha256": "d"},
                           {"rel_path": "MemoryBrain/README.md", "sha256": "e"},
                           {"rel_path": "Metrics/alerts.md", "sha256": "f"},
                           {"rel_path": "Other/check.ps1", "sha256": "g"},
                       ], "markers": []}, db_path=tmp_db)
    ws.bind_folder("git", "Daily Reports", "daily-reports", "init", db_path=tmp_db)
    ws.bind_folder("git", "MemoryBrain", "memorybrain", "marker", db_path=tmp_db)


def _fid(tmp_db, rel):
    return ws.get_file(db_path=tmp_db, root_id="git", rel_path=rel)["file_id"]


def test_resolution_order(tmp_db):
    _seed(tmp_db)
    r = rs.resolve_token("Daily Reports/TODO-DailyReports.md", "daily-reports", db_path=tmp_db)
    assert (r["how"], r["file_id"]) == ("exact", _fid(tmp_db, "Daily Reports/TODO-DailyReports.md"))
    r = rs.resolve_token("Metrics/alerts.md", "daily-reports", db_path=tmp_db)
    assert r["how"] == "exact_global"
    r = rs.resolve_token("docs/open-questions.md", "daily-reports", db_path=tmp_db)
    assert (r["how"], r["weight"]) == ("suffix", 0.9)
    r = rs.resolve_token("TODO-DailyReports.md", "daily-reports", db_path=tmp_db)
    assert (r["how"], r["weight"]) == ("basename", 0.8)
    r = rs.resolve_token("check.ps1", "daily-reports", db_path=tmp_db)
    assert (r["how"], r["weight"]) == ("basename_global", 0.6)
    # README.md is ambiguous inside daily-reports (two) -> dangling with candidates
    r = rs.resolve_token("README.md", "daily-reports", db_path=tmp_db)
    assert r["how"] == "dangling" and r["file_id"] is None
    assert set(r["candidates"]) == {"Daily Reports/Tools/ReportFlow/README.md", "Daily Reports/README.md"}
    # qualified, it resolves
    r = rs.resolve_token("Tools/ReportFlow/README.md", "daily-reports", db_path=tmp_db)
    assert r["how"] == "suffix"
    r = rs.resolve_token("Strategy/gone.md", "daily-reports", db_path=tmp_db)
    assert r["how"] == "dangling" and r["candidates"] == []


def test_file_ref_edges_from_text_and_cap(tmp_db):
    _seed(tmp_db)
    text = (r"see C:\work\repos\Daily Reports\TODO-DailyReports.md and docs/open-questions.md, "
            "also Strategy/gone.md and https://x/y.md")
    edges = rs.file_ref_edges("mem-1", "daily-reports", text, db_path=tmp_db)
    hows = {e["meta"]["how"]: e for e in edges}
    assert hows["exact"]["dst_kind"] == "file" and hows["exact"]["weight"] == 1.0
    assert hows["suffix"]["dst_id"] == _fid(tmp_db, "Daily Reports/docs/open-questions.md")
    assert hows["dangling"]["dst_kind"] == "dangling" and hows["dangling"]["dst_id"] == "Strategy/gone.md"
    assert [e["weight"] for e in edges] == sorted([e["weight"] for e in edges], reverse=True)
    many = " ".join(f"Daily Reports/docs/f{i}.md" for i in range(40))
    assert len(rs.file_ref_edges("mem-2", "daily-reports", many, db_path=tmp_db)) == rs.MAX_FILE_EDGES


def test_link_memory_files_writes_edges_degrees_and_infers_binding(tmp_db):
    _seed(tmp_db)
    upsert_project(Project(slug="metrics", name="Metrics",
                           last_activity=datetime.now(timezone.utc)), db_path=tmp_db)
    entry = MemoryEntry(id="mem-9", content=r"Chart rules live in Metrics\alerts.md now",
                        summary="alert rules", type="fact", project="metrics", importance=5)
    add_memory(entry, db_path=tmp_db)
    edges = rs.link_memory_files(entry, db_path=tmp_db)
    assert len(edges) == 1 and edges[0]["meta"]["how"] == "exact_global"
    f = ws.get_file(db_path=tmp_db, root_id="git", rel_path="Metrics/alerts.md")
    assert f["ref_degree"] == 1.0  # weight 1.0 * importance 5/5
    # 'Metrics' top folder was unbound -> inferred for project metrics
    owner = ws.owner_project("git", "Metrics/alerts.md", db_path=tmp_db)
    assert owner["project"] == "metrics" and owner["how"] == "memory" and owner["confidence"] == 0.6
    # a memory in another project naming MemoryBrain never steals the marker binding
    e2 = MemoryEntry(id="mem-10", content="read MemoryBrain/README.md", type="note", project="metrics")
    add_memory(e2, db_path=tmp_db)
    rs.link_memory_files(e2, db_path=tmp_db)
    assert ws.owner_project("git", "MemoryBrain/README.md", db_path=tmp_db)["project"] == "memorybrain"


def test_rebuild_is_idempotent_and_resolves_previously_dangling(tmp_db):
    _seed(tmp_db)
    e = MemoryEntry(id="mem-3", content="plan in Strategy/new-plan.md", type="note", project="daily-reports")
    add_memory(e, db_path=tmp_db)
    rs.link_memory_files(e, db_path=tmp_db)
    with _connect(tmp_db) as conn:
        assert conn.execute("SELECT dst_kind FROM file_links WHERE src_id = 'mem-3'").fetchone()[0] == "dangling"
    ws.apply_manifest({"machine": "m", "root_id": "git", "abs_path": r"C:\work\repos", "full": False,
                       "files": [{"rel_path": "Daily Reports/Strategy/new-plan.md", "sha256": "z"}],
                       "markers": []}, db_path=tmp_db)
    rep1 = rs.rebuild_file_links(db_path=tmp_db)
    rep2 = rs.rebuild_file_links(db_path=tmp_db)
    assert rep1 == rep2 and rep1["memories"] == 1 and rep1["dangling"] == 0
    with _connect(tmp_db) as conn:
        rows = conn.execute("SELECT dst_kind, dst_id FROM file_links WHERE src_id = 'mem-3'").fetchall()
    assert len(rows) == 1 and rows[0][0] == "file"


def test_strongest_reference_wins_when_two_tokens_hit_one_file(tmp_db):
    _seed(tmp_db)
    # the weaker suffix token comes first in the text, the exact absolute path later
    text = r"see docs/open-questions.md, that is C:\work\repos\Daily Reports\docs\open-questions.md in full"
    edges = rs.file_ref_edges("mem-5", "daily-reports", text, db_path=tmp_db)
    assert len(edges) == 1
    assert edges[0]["meta"]["how"] == "exact" and edges[0]["weight"] == 1.0


def test_exact_ambiguity_across_roots_dangles_with_candidates(tmp_db):
    _seed(tmp_db)
    ws.apply_manifest({"machine": "m", "root_id": "home", "abs_path": r"D:\work", "full": True,
                       "files": [{"rel_path": "Daily Reports/TODO-DailyReports.md", "sha256": "zz"}],
                       "markers": []}, db_path=tmp_db)
    ws.bind_folder("home", "Daily Reports", "daily-reports", "init", db_path=tmp_db)
    r = rs.resolve_token("Daily Reports/TODO-DailyReports.md", "daily-reports", db_path=tmp_db)
    assert r["how"] == "dangling" and r["file_id"] is None and len(r["candidates"]) == 2
    assert r["weight"] == rs.WEIGHTS["dangling"] == 0.1
    edges = rs.file_ref_edges("mem-6", "daily-reports", "Daily Reports/TODO-DailyReports.md", db_path=tmp_db)
    assert edges[0]["dst_kind"] == "dangling" and edges[0]["weight"] == 0.1


async def test_ingest_writes_file_ref_edges(tmp_db, mock_ollama, monkeypatch):
    import app.ingest_pipeline as ip
    import app.linker as lk
    import app.vector as vec
    monkeypatch.setattr(ip, "DB_PATH", tmp_db)
    monkeypatch.setattr(lk, "DB_PATH", tmp_db)
    monkeypatch.setattr(vec, "DB_PATH", tmp_db, raising=False)
    _seed(tmp_db)
    entry = MemoryEntry(content="Standing list lives in TODO-DailyReports.md, add every gap there.",
                        type="fact", project="daily-reports")
    out = await ip.ingest(entry)
    with _connect(tmp_db) as conn:
        row = conn.execute(
            "SELECT dst_kind, meta FROM file_links WHERE src_id = ? AND kind = 'file_ref'", (out.id,)).fetchone()
    assert row is not None and row[0] == "file"
    assert json.loads(row[1])["how"] == "basename"


def test_file_linking_runs_even_when_graph_is_disabled(tmp_db, monkeypatch):
    import app.linker as lk
    _seed(tmp_db)
    monkeypatch.setattr(lk, "graph_enabled", lambda: False)
    entry = MemoryEntry(id="mem-nograph", content="Notes in Metrics/alerts.md", type="note",
                        project="daily-reports")
    add_memory(entry, db_path=tmp_db)
    assert lk.link_new_memory(entry, [0.1] * 768, db_path=tmp_db) == []
    with _connect(tmp_db) as conn:
        row = conn.execute(
            "SELECT dst_kind FROM file_links WHERE src_id = 'mem-nograph' AND kind = 'file_ref'").fetchone()
    assert row is not None and row[0] == "file"


def test_bare_filename_never_uses_the_exact_tiers(tmp_db):
    _seed(tmp_db)
    # a root-level README.md exists; the token "README.md" is bare and ambiguous, so it must dangle
    ws.apply_manifest({"machine": "m", "root_id": "git", "abs_path": r"C:\work\repos", "full": False,
                       "files": [{"rel_path": "README.md", "sha256": "root"}], "markers": []}, db_path=tmp_db)
    r = rs.resolve_token("README.md", "daily-reports", db_path=tmp_db)
    assert r["how"] == "dangling" and r["file_id"] is None
    assert "README.md" in r["candidates"] or len(r["candidates"]) >= 2
    # a bare token that IS unique in the project still resolves by basename
    r = rs.resolve_token("open-questions.md", "daily-reports", db_path=tmp_db)
    assert r["how"] == "basename"


def test_suffix_falls_back_to_endswith_when_first_segment_lost_its_space(tmp_db):
    _seed(tmp_db)
    ws.apply_manifest({"machine": "m", "root_id": "git", "abs_path": r"C:\work\repos", "full": False,
                       "files": [{"rel_path": "Daily Reports/PROGRESS_LOG.md", "sha256": "pl"},
                                 {"rel_path": "Metrics/PROGRESS_LOG.md", "sha256": "pl2"}], "markers": []},
                      db_path=tmp_db)
    # the extractor turns "Daily Reports/PROGRESS_LOG.md" into "Reports/PROGRESS_LOG.md"
    toks = rs.extract_path_tokens("see Daily Reports/PROGRESS_LOG.md for the log")
    assert toks == ["Reports/PROGRESS_LOG.md"]
    r = rs.resolve_token(toks[0], "daily-reports", db_path=tmp_db)
    assert r["how"] == "suffix" and r["rel_path"] == "Daily Reports/PROGRESS_LOG.md"
    # a real slash-delimited suffix still takes priority and is unaffected
    r = rs.resolve_token("docs/open-questions.md", "daily-reports", db_path=tmp_db)
    assert r["how"] == "suffix" and r["rel_path"] == "Daily Reports/docs/open-questions.md"


def test_root_bound_project_gets_the_in_project_tiers(tmp_db):
    ws.apply_manifest({"machine": "m", "root_id": "home", "abs_path": "/home/you/src", "full": True,
                       "files": [{"rel_path": "docs/a.md"}, {"rel_path": "notes/b.md"}, {"rel_path": "notes/a.md"}],
                       "markers": []}, db_path=tmp_db)
    ws.bind_folder("home", "", "home-proj", "tool", db_path=tmp_db)
    r = rs.resolve_token("b.md", "home-proj", tmp_db)
    assert (r["how"], r["weight"], r["rel_path"]) == ("basename", 0.8, "notes/b.md")
    r = rs.resolve_token("docs/a.md", "home-proj", tmp_db)
    assert r["how"] == "exact"


def test_nested_folder_of_another_project_is_not_in_project(tmp_db):
    _seed(tmp_db)   # Daily Reports -> daily-reports; has Daily Reports/README.md and Daily Reports/Tools/ReportFlow/README.md
    ws.bind_folder("git", "Daily Reports/Tools/ReportFlow", "reportflow", "marker", db_path=tmp_db)
    r = rs.resolve_token("README.md", "daily-reports", tmp_db)
    assert r["how"] == "basename" and r["rel_path"] == "Daily Reports/README.md"
    r = rs.resolve_token("README.md", "reportflow", tmp_db)
    assert r["how"] == "basename" and r["rel_path"] == "Daily Reports/Tools/ReportFlow/README.md"


def test_prose_glued_to_a_path_is_rescued(tmp_db):
    _seed(tmp_db)
    r = rs.resolve_token("gap/to-do list now lives at TODO-DailyReports.md", "daily-reports", tmp_db)
    assert (r["how"], r["rel_path"], r["matched"]) == ("basename", "Daily Reports/TODO-DailyReports.md", "TODO-DailyReports.md")
    r = rs.resolve_token("east/west/north/south. This is the truth that docs/open-questions.md", "daily-reports", tmp_db)
    assert (r["how"], r["rel_path"], r["matched"]) == ("suffix", "Daily Reports/docs/open-questions.md", "docs/open-questions.md")
    r = rs.resolve_token("Entity). reference.md", "daily-reports", tmp_db)
    assert r["how"] == "dangling" and r["matched"] == "reference.md"
    edges = rs.file_ref_edges("m-1", "daily-reports", "the gap/to-do list now lives at TODO-DailyReports.md", tmp_db)
    assert edges[0]["dst_kind"] == "file"
    assert edges[0]["meta"]["token"] == "gap/to-do list now lives at TODO-DailyReports.md"
    assert edges[0]["meta"]["matched"] == "TODO-DailyReports.md"


def test_a_clean_dangling_path_is_not_rescued_and_meta_stays_small(tmp_db):
    _seed(tmp_db)
    r = rs.resolve_token("Vendor/widget/README.md", "daily-reports", tmp_db)
    assert r["how"] == "dangling" and r["candidates"] == []
    edges = rs.file_ref_edges("m-2", "daily-reports", "see docs/open-questions.md", tmp_db)
    assert set(edges[0]["meta"]) == {"token", "how", "candidates"}
