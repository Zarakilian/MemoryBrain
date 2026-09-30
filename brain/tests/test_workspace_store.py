import hashlib
import json
import pytest
from app.workspace import store as ws


def test_upsert_root_and_resolve_abs_path(tmp_db):
    ws.upsert_root("git", "WORK-PC", r"C:\work\repos", db_path=tmp_db)
    roots = ws.list_roots(db_path=tmp_db)
    assert roots[0]["root_id"] == "git" and roots[0]["abs_path"] == r"C:\work\repos"
    assert ws.resolve_abs_path(r"C:\work\repos\Daily Reports\Tools\ReportFlow", db_path=tmp_db) == \
        ("git", "Daily Reports/Tools/ReportFlow")
    assert ws.resolve_abs_path("c:/WORK/repos/Daily Reports", db_path=tmp_db) == ("git", "Daily Reports")
    # relative path with exactly one root resolves against it
    assert ws.resolve_abs_path("Daily Reports/Tools", db_path=tmp_db) == ("git", "Daily Reports/Tools")
    assert ws.resolve_abs_path(r"D:\elsewhere\x", db_path=tmp_db) is None


def test_bind_folder_insert_and_owner_longest_prefix(tmp_db):
    ws.upsert_root("git", "m", r"C:\work\repos", db_path=tmp_db)
    r = ws.bind_folder("git", "Daily Reports", "daily-reports", "init", db_path=tmp_db)
    assert r["written"] and r["row"]["confidence"] == 0.8 and r["row"]["confirmed"] == 0
    r2 = ws.bind_folder("git", "Daily Reports/Tools/ReportFlow", "reportflow", "tool",
                        db_path=tmp_db, label="ReportFlow working folder", role="area")
    assert r2["written"] and r2["row"]["confirmed"] == 1 and r2["row"]["depth"] == 3
    assert ws.owner_project("git", "Daily Reports/docs/x.md", db_path=tmp_db)["project"] == "daily-reports"
    assert ws.owner_project("git", "Daily Reports/Tools/ReportFlow/sweep.ps1", db_path=tmp_db)["project"] == "reportflow"
    assert ws.owner_project("git", "Other/y.md", db_path=tmp_db) is None


def test_bind_precedence_lower_rank_never_overwrites(tmp_db):
    ws.upsert_root("git", "m", r"C:\work\repos", db_path=tmp_db)
    ws.bind_folder("git", "MemoryBrain", "memorybrain", "marker", db_path=tmp_db)
    r = ws.bind_folder("git", "MemoryBrain", "other-project", "memory", db_path=tmp_db)
    assert r["written"] is False and r["reason"] == "kept_higher_ranked"
    assert r["conflict_with"] == "other-project"
    assert ws.owner_project("git", "MemoryBrain/README.md", db_path=tmp_db)["project"] == "memorybrain"
    # a higher ranked source does overwrite
    r = ws.bind_folder("git", "Shared-DailyReports-Map", "map-guess", "memory", db_path=tmp_db)
    assert r["row"]["confidence"] == 0.6
    r = ws.bind_folder("git", "Shared-DailyReports-Map", "daily-reports", "tool", db_path=tmp_db)
    assert r["written"] and r["row"]["project"] == "daily-reports" and r["row"]["how"] == "tool"


def test_bind_same_project_reinforces_memory_confidence(tmp_db):
    ws.upsert_root("git", "m", r"C:\work\repos", db_path=tmp_db)
    ws.bind_folder("git", "Metrics", "metrics", "memory", db_path=tmp_db)
    r = ws.bind_folder("git", "Metrics", "metrics", "memory", db_path=tmp_db)
    assert r["reason"] == "reinforced" and r["row"]["evidence_count"] == 2
    assert r["row"]["confidence"] == 0.8
    r = ws.bind_folder("git", "Metrics", "metrics", "memory", db_path=tmp_db)
    assert r["row"]["confidence"] == 0.8  # capped


def test_cwd_heuristic_confidence_override_and_unmapped_rows(tmp_db):
    ws.upsert_root("git", "m", r"C:\work\repos", db_path=tmp_db)
    r = ws.bind_folder("git", "side-project", "side-project", "cwd", db_path=tmp_db, confidence=0.5)
    assert r["row"]["confidence"] == 0.5 and r["row"]["confirmed"] == 0
    ws.record_folder_seen("git", "SideProject", "", db_path=tmp_db)
    ws.record_folder_seen("git", "SideProject", "", db_path=tmp_db)  # idempotent
    rows = ws.list_folders(db_path=tmp_db)
    unmapped = [f for f in rows if f["project"] == ""]
    assert [f["rel_path"] for f in unmapped] == ["SideProject"]
    assert ws.owner_project("git", "SideProject/a.md", db_path=tmp_db) is None


def test_root_level_binding_owns_everything_beneath_it(tmp_db):
    ws.upsert_root("git", "m", r"C:\work\repos", db_path=tmp_db)
    r = ws.bind_folder("git", "", "monorepo", "marker", db_path=tmp_db)
    assert r["written"] and r["row"]["depth"] == 0
    assert ws.owner_project("git", "src/app.py", db_path=tmp_db)["project"] == "monorepo"
    # a deeper binding still wins over the root binding
    ws.bind_folder("git", "Daily Reports", "daily-reports", "init", db_path=tmp_db)
    assert ws.owner_project("git", "Daily Reports/docs/x.md", db_path=tmp_db)["project"] == "daily-reports"
    assert ws.owner_project("git", "Other/y.md", db_path=tmp_db)["project"] == "monorepo"


def test_cross_project_overwrite_carries_remote_url_and_seen_depth_matches(tmp_db):
    ws.upsert_root("git", "m", r"C:\work\repos", db_path=tmp_db)
    ws.bind_folder("git", "Repo", "proj-a", "memory", db_path=tmp_db, remote_url="https://x/a.git")
    r = ws.bind_folder("git", "Repo", "proj-b", "tool", db_path=tmp_db, remote_url="https://x/b.git")
    assert r["reason"] == "overwrote_lower_ranked" and r["row"]["remote_url"] == "https://x/b.git"
    r = ws.bind_folder("git", "Repo", "proj-c", "marker", db_path=tmp_db)  # no remote given: keep the old one
    assert r["row"]["project"] == "proj-c" and r["row"]["remote_url"] == "https://x/b.git"
    ws.record_folder_seen("git", "A/B/C", "", db_path=tmp_db)
    seen = [f for f in ws.list_folders(db_path=tmp_db) if f["rel_path"] == "A/B/C"][0]
    assert seen["depth"] == 3
    # INSERT OR IGNORE must not disturb an existing bound root row
    ws.bind_folder("git", "", "monorepo", "marker", db_path=tmp_db)
    ws.record_folder_seen("git", "", "", db_path=tmp_db)
    root_rows = [f for f in ws.list_folders(db_path=tmp_db) if f["rel_path"] == ""]
    assert len(root_rows) == 1
    assert root_rows[0]["project"] == "monorepo" and root_rows[0]["how"] == "marker" and root_rows[0]["depth"] == 0


def _manifest(files, full=True, markers=None):
    return {"machine": "WORK-PC", "root_id": "git", "abs_path": r"C:\work\repos",
            "scanned_at": "2026-09-04T10:00:00Z", "full": full,
            "files": files, "markers": markers or []}


def _f(rel, sha="", size=10, mtime="2026-09-01T00:00:00Z", title=""):
    return {"rel_path": rel, "size": size, "mtime": mtime, "sha256": sha, "title": title}


def test_apply_manifest_add_update_delete_and_marker(tmp_db):
    rep = ws.apply_manifest(_manifest(
        [_f("Daily Reports/TODO-DailyReports.md", "h1", title="Daily Reports TO-DO"),
         _f("Daily Reports/docs/glossary.md", "h2"),
         _f("MemoryBrain/README.md", "h3")],
        markers=[{"rel_path": "MemoryBrain", "project": "memorybrain"}]), db_path=tmp_db)
    assert (rep["added"], rep["updated"], rep["deleted"], rep["markers_bound"]) == (3, 0, 0, 1)
    f = ws.get_file(db_path=tmp_db, root_id="git", rel_path="daily reports/TODO-DAILYREPORTS.md")
    assert f["ext"] == "md" and f["folder"] == "Daily Reports" and f["title"] == "Daily Reports TO-DO"
    assert json.loads(f["seen_on"]) == ["WORK-PC"]
    assert ws.owner_project("git", "MemoryBrain/x.py", db_path=tmp_db)["project"] == "memorybrain"
    # incremental push: update one, never delete
    rep = ws.apply_manifest(_manifest([_f("Daily Reports/docs/glossary.md", "h2b", size=99)], full=False),
                            db_path=tmp_db)
    assert (rep["added"], rep["updated"], rep["deleted"]) == (0, 1, 0)
    assert ws.get_file(db_path=tmp_db, root_id="git", rel_path="MemoryBrain/README.md")["status"] == "active"
    # full push without README -> deleted
    rep = ws.apply_manifest(_manifest(
        [_f("Daily Reports/TODO-DailyReports.md", "h1"), _f("Daily Reports/docs/glossary.md", "h2b")]),
        db_path=tmp_db)
    assert rep["deleted"] == 1
    assert ws.get_file(db_path=tmp_db, root_id="git", rel_path="MemoryBrain/README.md")["status"] == "deleted"


def test_apply_manifest_detects_move_by_hash_and_carries_links(tmp_db):
    ws.apply_manifest(_manifest([_f("Strategy/old-plan.md", "same")]), db_path=tmp_db)
    old = ws.get_file(db_path=tmp_db, root_id="git", rel_path="Strategy/old-plan.md")
    with ws._st._connect(tmp_db) as conn:
        conn.execute("""INSERT INTO file_links (src_kind, src_id, dst_kind, dst_id, kind, weight, meta, created_at)
                        VALUES ('memory', 'mem-1', 'file', ?, 'file_ref', 1.0, '{}', 'now')""", (old["file_id"],))
        conn.commit()
    rep = ws.apply_manifest(_manifest([_f("Strategy/archive/old-plan.md", "same")]), db_path=tmp_db)
    assert rep["moved"] == 1 and rep["deleted"] == 0
    new = ws.get_file(db_path=tmp_db, root_id="git", rel_path="Strategy/archive/old-plan.md")
    old = ws.get_file(db_path=tmp_db, file_id=old["file_id"])
    assert old["status"] == "moved" and old["moved_to"] == new["file_id"]
    with ws._st._connect(tmp_db) as conn:
        n = conn.execute("SELECT COUNT(*) FROM file_links WHERE dst_id = ?", (new["file_id"],)).fetchone()[0]
    assert n == 1
    ctx = ws.file_context(new["file_id"], db_path=tmp_db)
    assert ctx["file"]["file_id"] == new["file_id"] and ctx["memories"] == []  # mem-1 has no memories row


def test_list_find_and_map(tmp_db):
    ws.apply_manifest(_manifest(
        [_f("Daily Reports/TODO-DailyReports.md", "a", mtime="2026-09-03T00:00:00Z"),
         _f("Daily Reports/docs/glossary.md", "b"),
         _f("Daily Reports/Tools/ReportFlow/sweep.ps1", "c"),
         _f("Shared-DailyReports-Map/README.md", "d"),
         _f("Other/notes.txt", "e")]), db_path=tmp_db)
    from datetime import datetime, timezone
    from app.models import Project
    ws._st.upsert_project(Project(slug="daily-reports", name="Daily Reports",
                                  last_activity=datetime.now(timezone.utc)), db_path=tmp_db)
    ws.bind_folder("git", "Daily Reports", "daily-reports", "init", db_path=tmp_db)
    ws.bind_folder("git", "Shared-DailyReports-Map", "daily-reports", "tool", db_path=tmp_db, role="repo")
    ws.record_folder_seen("git", "Other", "", db_path=tmp_db)
    files = ws.list_files("daily-reports", db_path=tmp_db, sort="path")
    assert [f["rel_path"] for f in files] == [
        "Daily Reports/TODO-DailyReports.md", "Daily Reports/Tools/ReportFlow/sweep.ps1",
        "Daily Reports/docs/glossary.md", "Shared-DailyReports-Map/README.md"]
    assert [f["rel_path"] for f in ws.list_files("daily-reports", db_path=tmp_db, ext="ps1")] == \
        ["Daily Reports/Tools/ReportFlow/sweep.ps1"]
    assert ws.list_files("daily-reports", db_path=tmp_db, sort="recent")[0]["rel_path"] == \
        "Daily Reports/TODO-DailyReports.md"
    hits = ws.find_files("todo", db_path=tmp_db)
    assert hits[0]["rel_path"] == "Daily Reports/TODO-DailyReports.md" and hits[0]["project"] == "daily-reports"
    m = ws.workspace_map(db_path=tmp_db)
    assert m["roots"][0]["machine"] == "WORK-PC"
    assert [u["rel_path"] for u in m["unmapped_folders"]] == ["Other"]
    dd = [p for p in m["projects"] if p["slug"] == "daily-reports"][0]
    assert dd["file_count"] == 4 and len(dd["home_folders"]) == 2


def test_root_bound_project_lists_every_file_in_that_root(tmp_db):
    ws.apply_manifest(_manifest([_f("Daily Reports/TODO-DailyReports.md", "a"), _f("Other/notes.txt", "b")]),
                      db_path=tmp_db)
    from datetime import datetime, timezone
    from app.models import Project
    ws._st.upsert_project(Project(slug="monorepo", name="Monorepo",
                                  last_activity=datetime.now(timezone.utc)), db_path=tmp_db)
    ws.bind_folder("git", "", "monorepo", "marker", db_path=tmp_db)
    assert sorted(f["rel_path"] for f in ws.list_files("monorepo", db_path=tmp_db, sort="path")) == \
        ["Daily Reports/TODO-DailyReports.md", "Other/notes.txt"]
    m = ws.workspace_map(db_path=tmp_db, project="monorepo")
    assert m["projects"][0]["file_count"] == 2


def test_find_files_treats_underscore_and_percent_literally(tmp_db):
    ws.apply_manifest(_manifest([_f("A/PROGRESS_LOG.md", "1"), _f("A/PROGRESSxLOG.md", "2"), _f("A/100%.md", "3")]),
                      db_path=tmp_db)
    assert [f["rel_path"] for f in ws.find_files("PROGRESS_LOG", db_path=tmp_db)] == ["A/PROGRESS_LOG.md"]
    assert [f["rel_path"] for f in ws.find_files("100%", db_path=tmp_db)] == ["A/100%.md"]


def test_bind_folder_rejects_invalid_slugs_and_creates_the_missing_project_row(tmp_db):
    import pytest
    from app.storage import get_project
    for bad in ("Daily Reports", "Metrics", "", "cwd/x"):
        with pytest.raises(ValueError):
            ws.bind_folder("git", "Metrics", bad, "marker", db_path=tmp_db)
    assert ws.list_folders(tmp_db) == []
    ws.bind_folder("git", "Metrics", "metrics", "marker", db_path=tmp_db)
    p = get_project("metrics", db_path=tmp_db)
    assert p is not None and p.name == "Metrics"


def test_bind_folder_leaves_an_existing_project_row_alone(tmp_db):
    from datetime import datetime, timezone
    from app.models import Project
    from app.storage import get_project, upsert_project
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    upsert_project(Project(slug="daily-reports", name="Daily Reports", last_activity=old), db_path=tmp_db)
    ws.bind_folder("git", "Daily Reports", "daily-reports", "marker", db_path=tmp_db)
    p = get_project("daily-reports", db_path=tmp_db)
    assert p.name == "Daily Reports" and p.last_activity == old


def test_apply_manifest_skips_markers_with_invalid_slugs(tmp_db):
    rep = ws.apply_manifest(_manifest([_f("X/a.md", "h1")],
                                      markers=[{"rel_path": "X", "project": "Bad Slug"},
                                               {"rel_path": "X", "project": "good-slug"}]), db_path=tmp_db)
    assert rep["markers_bound"] == 1 and rep["markers_skipped"] == 1
    assert ws.owner_project("git", "X/a.md", db_path=tmp_db)["project"] == "good-slug"


def test_case_only_rename_updates_the_displayed_path(tmp_db):
    ws.apply_manifest(_manifest([_f("Notes/readme.md", "h1")]), db_path=tmp_db)
    ws.apply_manifest(_manifest([_f("notes/README.md", "h1")]), db_path=tmp_db)
    f = ws.get_file(db_path=tmp_db, root_id="git", rel_path="notes/readme.md")
    assert f["rel_path"] == "notes/README.md" and f["folder"] == "notes" and f["status"] == "active"
    with ws._st._connect(tmp_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM workspace_files").fetchone()[0] == 1


def test_empty_files_never_count_as_moves(tmp_db):
    empty = hashlib.sha256(b"").hexdigest()
    ws.apply_manifest(_manifest([_f("a/__init__.py", empty, size=0)]), db_path=tmp_db)
    rep = ws.apply_manifest(_manifest([_f("b/__init__.py", empty, size=0)]), db_path=tmp_db)
    assert (rep["added"], rep["deleted"], rep["moved"]) == (1, 1, 0)
