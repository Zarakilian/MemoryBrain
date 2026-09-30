import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

for _cand in (Path(__file__).parent.parent.parent / "cli", Path(__file__).parent.parent / "cli"):
    if (_cand / "brain_scan.py").exists():
        sys.path.insert(0, str(_cand))
        break

import brain_scan as bs  # noqa: E402


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "repos"
    (root / "Daily Reports" / "docs").mkdir(parents=True)
    (root / "Daily Reports" / "TODO-DailyReports.md").write_text("# Daily Reports TO-DO\n\nitems", encoding="utf-8")
    (root / "Daily Reports" / "docs" / "glossary.md").write_text("# Glossary\n", encoding="utf-8")
    (root / "Daily Reports" / "Tools" / "ReportFlow").mkdir(parents=True)
    (root / "Daily Reports" / "Tools" / "ReportFlow" / ".brainproject").write_text("reportflow\n", encoding="utf-8")
    (root / "MemoryBrain" / ".git").mkdir(parents=True)
    (root / "MemoryBrain" / ".git" / "config").write_text(
        '[remote "origin"]\n\turl = https://github.com/Zarakilian/MemoryBrain.git\n', encoding="utf-8")
    (root / "MemoryBrain" / ".brainproject").write_text("memorybrain", encoding="utf-8")
    (root / "MemoryBrain" / "README.md").write_text("# MemoryBrain\n", encoding="utf-8")
    (root / "MemoryBrain" / "node_modules" / "x").mkdir(parents=True)
    (root / "MemoryBrain" / "node_modules" / "x" / "index.js").write_text("ignored", encoding="utf-8")
    (root / "MemoryBrain" / ".env").write_text("SECRET=1", encoding="utf-8")
    (root / "Other").mkdir()
    (root / "Other" / "notes.txt").write_text("n", encoding="utf-8")
    return root


def test_walk_root_files_markers_titles_and_exclusions(tmp_path):
    root = _tree(tmp_path)
    files, markers = bs.walk_root(root, [])
    rels = sorted(f["rel_path"] for f in files)
    assert rels == ["Daily Reports/TODO-DailyReports.md", "Daily Reports/docs/glossary.md",
                    "MemoryBrain/README.md", "Other/notes.txt"]
    todo = [f for f in files if f["rel_path"].endswith("TODO-DailyReports.md")][0]
    assert todo["title"] == "Daily Reports TO-DO" and len(todo["sha256"]) == 64 and todo["mtime"].endswith("Z")
    assert sorted((m["rel_path"], m["project"]) for m in markers) == \
        [("Daily Reports/Tools/ReportFlow", "reportflow"), ("MemoryBrain", "memorybrain")]


def test_extra_ignore_globs_and_diff(tmp_path):
    root = _tree(tmp_path)
    files, _ = bs.walk_root(root, ["Other/*"])
    assert not any(f["rel_path"].startswith("Other/") for f in files)
    prev = {f["rel_path"]: {"size": f["size"], "mtime": f["mtime"]} for f in files}
    assert bs.diff_files(prev, files) == []
    (root / "Daily Reports" / "docs" / "glossary.md").write_text("# Glossary\nmore\n", encoding="utf-8")
    files2, _ = bs.walk_root(root, ["Other/*"])
    changed = bs.diff_files(prev, files2)
    assert [c["rel_path"] for c in changed] == ["Daily Reports/docs/glossary.md"]


def test_discover_and_propose_map(tmp_path):
    root = _tree(tmp_path)
    folders = bs.discover_folders(root)
    by = {f["rel_path"]: f for f in folders}
    assert by["MemoryBrain"]["is_repo"] and by["MemoryBrain"]["remote_url"].endswith("MemoryBrain.git")
    assert by["Daily Reports"]["proposed_slug"] == "daily-reports" and by["Daily Reports"]["files"] == 2
    prop = bs.propose_map("git", root, known_slugs={"daily-reports", "memorybrain"})
    rows = {r["rel_path"]: r for r in prop["folders"]}
    assert rows["Daily Reports"]["project"] == "daily-reports"      # matched a known slug
    assert rows["MemoryBrain"]["project"] == "memorybrain"          # marker wins
    assert rows["Other"]["project"] == ""                            # left for the user
    assert bs.slugify("Some_Tool_Repo_Ansible") == "some-tool-repo-ansible"


def test_apply_map_writes_markers_and_reports_conflicts(tmp_path):
    root = _tree(tmp_path)
    prop = bs.propose_map("git", root, known_slugs=set())
    for r in prop["folders"]:
        if r["rel_path"] == "Other":
            r["project"] = "other-stuff"
        if r["rel_path"] == "MemoryBrain":
            r["project"] = "wrong-slug"  # conflicts with the marker on disk
    p = tmp_path / "workspace-map.proposed.json"
    p.write_text(json.dumps(prop), encoding="utf-8")
    posted = []
    rep = bs.apply_map(p, post=lambda path, body: posted.append((path, body)) or {"bound": 1, "seen": 0})
    assert (root / "Other" / ".brainproject").read_text(encoding="utf-8").strip() == "other-stuff"
    assert rep["markers_written"] == 1 and rep["conflicts"] == [{"rel_path": "MemoryBrain", "on_disk": "memorybrain", "proposed": "wrong-slug"}]
    assert posted[0][0] == "/workspace/map" and posted[0][1]["root_id"] == "git"


def test_cmd_scan_posts_full_manifest_then_incremental(tmp_path, monkeypatch, capsys):
    root = _tree(tmp_path)
    monkeypatch.setattr(bs, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(bs, "MACHINE", "TESTBOX")
    calls = []

    def post(path, body):
        calls.append((path, body))
        return {"added": len(body.get("files", [])), "updated": 0, "deleted": 0, "moved": 0, "markers_bound": 0}

    def get(path):
        return {"folders": [], "projects": []}

    args = SimpleNamespace(root=str(root), label="git", full=False, dry_run=False, init=False, apply=None)
    assert bs.cmd_scan(args, post, get) == 0
    assert calls[0][0] == "/workspace/scan" and calls[0][1]["full"] is True and calls[0][1]["machine"] == "TESTBOX"
    assert len(calls[0][1]["files"]) == 4 and len(calls[0][1]["markers"]) == 2
    calls.clear()
    args2 = SimpleNamespace(root=None, label=None, full=False, dry_run=False, init=False, apply=None)
    assert bs.cmd_scan(args2, post, get) == 0
    assert calls[0][1]["full"] is False and calls[0][1]["files"] == []  # nothing changed
    args3 = SimpleNamespace(root=None, label=None, full=False, dry_run=True, init=False, apply=None)
    calls.clear()
    (root / "Other" / "new.md").write_text("# New", encoding="utf-8")
    assert bs.cmd_scan(args3, post, get) == 0 and calls == []
    assert "Other/new.md" in capsys.readouterr().out


def test_walk_root_prunes_ignored_directories(tmp_path, monkeypatch):
    root = _tree(tmp_path)
    visited = []
    real_walk = bs.os.walk

    def spy(top, **kw):
        for dirpath, dirnames, filenames in real_walk(top, **kw):
            visited.append(Path(dirpath).name)
            yield dirpath, dirnames, filenames  # same list object, so pruning propagates

    monkeypatch.setattr(bs.os, "walk", spy)
    files, markers = bs.walk_root(root, [])
    assert "node_modules" not in visited and ".git" not in visited and "x" not in visited
    assert sorted(f["rel_path"] for f in files) == ["Daily Reports/TODO-DailyReports.md", "Daily Reports/docs/glossary.md",
                                                    "MemoryBrain/README.md", "Other/notes.txt"]
    assert len(markers) == 2


def test_cmd_scan_apply_works_without_a_registered_root(tmp_path, monkeypatch):
    root = _tree(tmp_path)
    monkeypatch.setattr(bs, "STATE_PATH", tmp_path / "state.json")  # no state, no roots
    prop = bs.propose_map("git", root, known_slugs=set())
    p = tmp_path / "map.json"
    p.write_text(json.dumps(prop), encoding="utf-8")
    posted = []
    args = SimpleNamespace(root=None, label=None, full=False, dry_run=False, init=False, apply=str(p))
    assert bs.cmd_scan(args, post=lambda path, body: posted.append(path) or {}, get=lambda path: {}) == 0
    assert posted == ["/workspace/map"]


def test_apply_map_skips_a_folder_that_no_longer_exists(tmp_path):
    root = _tree(tmp_path)
    prop = bs.propose_map("git", root, known_slugs=set())
    prop["folders"].append({"rel_path": "Gone", "project": "gone-proj", "role": "primary",
                            "label": "", "remote_url": "", "confirmed": True})
    p = tmp_path / "map.json"
    p.write_text(json.dumps(prop), encoding="utf-8")
    rep = bs.apply_map(p, post=lambda path, body: {})
    assert rep["missing_folders"] == ["Gone"] and not (root / "Gone").exists()


def test_build_and_dist_are_indexed_bin_and_obj_are_not(tmp_path):
    root = _tree(tmp_path)
    (root / "Site" / "build").mkdir(parents=True)
    (root / "Site" / "build" / "deploy.ps1").write_text("x", encoding="utf-8")
    (root / "Site" / "dist").mkdir()
    (root / "Site" / "dist" / "report.html").write_text("x", encoding="utf-8")
    (root / "Site" / "bin").mkdir()
    (root / "Site" / "bin" / "a.dll").write_bytes(b"\x00")
    (root / "Site" / "obj").mkdir()
    (root / "Site" / "obj" / "a.o").write_bytes(b"\x00")
    rels = {f["rel_path"] for f in bs.walk_root(root, [])[0]}
    assert {"Site/build/deploy.ps1", "Site/dist/report.html"} <= rels
    assert not any(r.startswith(("Site/bin/", "Site/obj/")) for r in rels)


def test_junctions_and_symlinked_directories_are_not_walked(tmp_path, monkeypatch):
    root = _tree(tmp_path)
    (root / "real" / "sub").mkdir(parents=True)
    (root / "real" / "sub" / "a.md").write_text("# a\n", encoding="utf-8")
    (root / "junc" / "sub").mkdir(parents=True)          # stands in for a junction to real/
    (root / "junc" / "sub" / "a.md").write_text("# a\n", encoding="utf-8")
    monkeypatch.setattr(bs.os.path, "isjunction", lambda p: Path(p).name == "junc", raising=False)
    try:
        os.symlink(root / "real", root / "slink", target_is_directory=True)
    except (OSError, NotImplementedError):
        pass
    rels = {f["rel_path"] for f in bs.walk_root(root, [])[0]}
    assert "real/sub/a.md" in rels
    assert not any(r.startswith(("junc/", "slink/")) for r in rels)


def test_ignore_globs_prune_whole_trees_and_their_markers(tmp_path, monkeypatch):
    root = _tree(tmp_path)
    (root / "Vendor" / "x" / "deep").mkdir(parents=True)
    (root / "Vendor" / "x" / "deep" / "a.md").write_text("# a\n", encoding="utf-8")
    (root / "Vendor" / "x" / ".brainproject").write_text("ignored-proj\n", encoding="utf-8")
    visited = []
    real_walk = bs.os.walk

    def spy(top, **kw):
        for dirpath, dirnames, filenames in real_walk(top, **kw):
            visited.append(Path(dirpath).name)
            yield dirpath, dirnames, filenames

    monkeypatch.setattr(bs.os, "walk", spy)
    files, markers = bs.walk_root(root, ["Vendor/*"])
    assert "Vendor" not in visited and "deep" not in visited
    # v3: landmark files at depth 2 are still indexed (as files), nothing else is,
    # and a marker inside an ignored tree never binds a project.
    vendor = {f["rel_path"] for f in files if f["rel_path"].startswith("Vendor/")}
    assert vendor == {"Vendor/x/.brainproject"}
    assert not any(m["project"] == "ignored-proj" for m in markers)


def test_walk_root_reuses_hashes_and_titles_for_unchanged_files(tmp_path, monkeypatch):
    root = _tree(tmp_path)
    calls = []
    real_sha = bs._sha
    monkeypatch.setattr(bs, "_sha", lambda p, s: calls.append(str(p)) or real_sha(p, s))
    files, _ = bs.walk_root(root, [])
    first = len(calls)
    assert first == len(files)
    prev = {f["rel_path"]: {"size": f["size"], "mtime": f["mtime"], "sha256": f["sha256"], "title": f["title"]}
            for f in files}
    calls.clear()
    files2, _ = bs.walk_root(root, [], prev=prev)
    assert calls == [] and files2 == files
    (root / "Daily Reports" / "docs" / "glossary.md").write_text("# Glossary\nchanged\n", encoding="utf-8")
    calls.clear()
    files3, _ = bs.walk_root(root, [], prev=prev)
    assert len(calls) == 1 and calls[0].endswith("glossary.md")
    changed = [f for f in files3 if f["rel_path"].endswith("glossary.md")][0]
    assert changed["sha256"] != prev["Daily Reports/docs/glossary.md"]["sha256"]
    # a state entry from an older scanner has no sha256: recompute once, do not crash
    old_state = {k: {"size": v["size"], "mtime": v["mtime"]} for k, v in prev.items()}
    calls.clear()
    bs.walk_root(root, [], prev=old_state)
    assert len(calls) == len(files)


def test_cmd_scan_state_keeps_hash_and_title(tmp_path, monkeypatch):
    root = _tree(tmp_path)
    monkeypatch.setattr(bs, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(bs, "IGNORE_FILE", tmp_path / "no-ignore")
    args = SimpleNamespace(root=str(root), label="git", full=False, dry_run=False, init=False, apply=None)
    assert bs.cmd_scan(args, post=lambda path, body: {"added": len(body["files"])}, get=lambda path: {"folders": []}) == 0
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    entry = state["roots"]["git"]["files"]["Daily Reports/TODO-DailyReports.md"]
    assert set(entry) >= {"size", "mtime", "sha256", "title"} and entry["title"] == "Daily Reports TO-DO"


def test_title_survives_a_bom_and_long_lines(tmp_path):
    p = tmp_path / "bom.md"
    p.write_text("\ufeff# With BOM\n", encoding="utf-8")
    assert bs._title(p) == "With BOM"
    q = tmp_path / "long.md"
    q.write_text("x" * 10000 + "\n# After a long line\n", encoding="utf-8")
    assert bs._title(q) == "After a long line"
