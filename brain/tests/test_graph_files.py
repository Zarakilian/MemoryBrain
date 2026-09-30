from datetime import datetime, timezone

from app.models import MemoryEntry, Project
from app.storage import add_memory, upsert_project
from app.linker import _write_edges
from app.workspace import store as ws
from app.workspace.resolve import write_file_links
from app.graph_queries import get_graph, FOLDER_PULL_CAP


def _seed(db):
    upsert_project(Project(slug="daily-reports", name="Daily Reports",
                           last_activity=datetime.now(timezone.utc)), db_path=db)
    for mid in ("m1", "m2", "m3"):
        add_memory(MemoryEntry(id=mid, content=f"content {mid}", summary=f"summary {mid}",
                               type="fact", project="daily-reports", importance=4), db_path=db)
    _write_edges([{"src": "m1", "dst": "m2", "kind": "semantic", "weight": 0.7, "directed": 0, "meta": {}}], db)
    ws.apply_manifest({"machine": "m", "root_id": "git", "abs_path": r"C:\work\repos", "full": True,
                       "files": [{"rel_path": "Daily Reports/TODO.md", "sha256": "a", "mtime": "2026-09-05T00:00:00Z",
                                  "title": "To-do"},
                                 {"rel_path": "Daily Reports/docs/glossary.md", "sha256": "b"},
                                 {"rel_path": "Daily Reports/docs/quiet.md", "sha256": "c"},
                                 {"rel_path": "Other/loose.txt", "sha256": "d"}], "markers": []}, db_path=db)
    ws.bind_folder("git", "Daily Reports", "daily-reports", "marker", db_path=db)
    todo = ws.get_file(db, root_id="git", rel_path="Daily Reports/TODO.md")["file_id"]
    glos = ws.get_file(db, root_id="git", rel_path="Daily Reports/docs/glossary.md")["file_id"]
    write_file_links([
        {"src_kind": "memory", "src_id": "m1", "dst_kind": "file", "dst_id": todo, "kind": "file_ref",
         "weight": 1.0, "meta": {"how": "exact"}},
        {"src_kind": "memory", "src_id": "m2", "dst_kind": "file", "dst_id": todo, "kind": "file_ref",
         "weight": 0.8, "meta": {"how": "basename"}},
        {"src_kind": "memory", "src_id": "m3", "dst_kind": "file", "dst_id": glos, "kind": "file_ref",
         "weight": 0.6, "meta": {"how": "basename_global"}},
    ], db)
    ws.update_ref_degrees({todo, glos}, db)
    return todo, glos


def test_default_graph_has_no_file_layer_and_tags_memories(tmp_db):
    _seed(tmp_db)
    g = get_graph(db_path=tmp_db, min_weight=0.0)
    assert set(g) == {"nodes", "edges", "truncated"}
    assert {n["kind"] for n in g["nodes"]} == {"memory"}
    assert all(not any(k in ("file_ref", "in_folder") for k in e["kinds"]) for e in g["edges"])
    assert {"id", "label", "type", "project", "importance", "timestamp", "degree"} <= set(g["nodes"][0])


def test_include_files_adds_referenced_files_folders_and_edges(tmp_db):
    todo, glos = _seed(tmp_db)
    g = get_graph(db_path=tmp_db, min_weight=0.9, include_files=True)
    kinds = {}
    for n in g["nodes"]:
        kinds.setdefault(n["kind"], []).append(n)
    assert len(kinds["memory"]) == 3
    files = {n["path"]: n for n in kinds["file"]}
    assert set(files) == {"Daily Reports/TODO.md", "Daily Reports/docs/glossary.md"}  # quiet.md is not referenced
    assert files["Daily Reports/TODO.md"]["id"] == "file:" + todo
    assert files["Daily Reports/TODO.md"]["label"] == "TODO.md"
    assert files["Daily Reports/TODO.md"]["project"] == "daily-reports"
    assert files["Daily Reports/TODO.md"]["degree"] > files["Daily Reports/docs/glossary.md"]["degree"]
    folders = {n["path"]: n for n in kinds["folder"]}
    assert set(folders) == {"Daily Reports"}
    fnode = folders["Daily Reports"]
    assert fnode["id"] == "folder:git/daily reports" and fnode["file_count"] == 3 and fnode["role"] == "primary"
    assert round(fnode["degree"], 3) == round(files["Daily Reports/TODO.md"]["degree"]
                                              + files["Daily Reports/docs/glossary.md"]["degree"], 3)
    refs = [e for e in g["edges"] if e["kinds"] == ["file_ref"]]
    assert len(refs) == 3 and any(e["w"] == 0.6 for e in refs)          # min_weight never filters file edges
    contain = [e for e in g["edges"] if e["kinds"] == ["in_folder"]]
    assert {e["dst"] for e in contain} == {fnode["id"]} and len(contain) == 2
    assert g["files_truncated"] is False
    # the memory to memory edge at 0.7 IS filtered by min_weight 0.9
    assert not any("semantic" in e["kinds"] for e in g["edges"])


def test_folder_pull_adds_unreferenced_files_with_cap(tmp_db):
    _seed(tmp_db)
    g = get_graph(db_path=tmp_db, include_files=True, folders=["Daily Reports/docs"])
    paths = {n["path"] for n in g["nodes"] if n["kind"] == "file"}
    assert "Daily Reports/docs/quiet.md" in paths and "Other/loose.txt" not in paths
    ws.apply_manifest({"machine": "m", "root_id": "git", "abs_path": r"C:\work\repos", "full": False,
                       "files": [{"rel_path": f"Daily Reports/bulk/f{i:03d}.md"} for i in range(FOLDER_PULL_CAP + 20)],
                       "markers": []}, db_path=tmp_db)
    g = get_graph(db_path=tmp_db, include_files=True, folders=["Daily Reports/bulk"])
    bulk = [n for n in g["nodes"] if n["kind"] == "file" and n["path"].startswith("Daily Reports/bulk/")]
    assert len(bulk) == FOLDER_PULL_CAP and g["files_truncated"] is True


def test_project_filter_keeps_folders_of_that_project_only(tmp_db):
    _seed(tmp_db)
    upsert_project(Project(slug="other", name="Other", last_activity=datetime.now(timezone.utc)), db_path=tmp_db)
    ws.bind_folder("git", "Other", "other", "marker", db_path=tmp_db)
    g = get_graph(db_path=tmp_db, project="daily-reports", include_files=True)
    assert {n["project"] for n in g["nodes"] if n["kind"] == "folder"} == {"daily-reports"}
