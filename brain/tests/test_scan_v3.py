"""v3 scan hardening: scan-ignore at --init, no symlinked files, credential
files never indexed, landmarks inside ignored folders, no URL credentials."""
import os
import sys
from pathlib import Path

import pytest

for _cand in (Path(__file__).parent.parent.parent / "cli", Path(__file__).parent.parent / "cli"):
    if (_cand / "brain_scan.py").exists():
        sys.path.insert(0, str(_cand))
        break

import brain_scan as bs  # noqa: E402

CREDENTIAL_NAMES = ("credentials.json", "id_ed25519", "id_ed25519.pub", "vault.kdbx",
                    "prod.tfstate", ".npmrc", ".netrc", "server.ppk")


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "repos"
    (root / "Daily Reports").mkdir(parents=True)
    (root / "Daily Reports" / "TODO.md").write_text("# TO-DO\n", encoding="utf-8")
    return root


def test_init_honours_scan_ignore(tmp_path):
    root = _root(tmp_path)
    (root / "Private").mkdir()
    (root / "Private" / "a.md").write_text("# a\n", encoding="utf-8")
    prop = bs.propose_map("git", root, set(), extra_globs=["Private/*"])
    folders = {f["rel_path"] for f in prop["folders"]}
    assert "Daily Reports" in folders and "Private" not in folders


def test_file_symlinks_are_not_followed(tmp_path):
    root = _root(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("# outside the root\n", encoding="utf-8")
    try:
        os.symlink(outside, root / "Daily Reports" / "link.md")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    rels = {f["rel_path"] for f in bs.walk_root(root, [])[0]}
    assert "Daily Reports/TODO.md" in rels and "Daily Reports/link.md" not in rels


def test_credential_files_are_never_indexed(tmp_path):
    root = _root(tmp_path)
    for name in CREDENTIAL_NAMES:
        (root / "Daily Reports" / name).write_text("x", encoding="utf-8")
    names = {f["rel_path"].split("/")[-1] for f in bs.walk_root(root, [])[0]}
    assert names.isdisjoint(CREDENTIAL_NAMES)


def test_landmarks_inside_an_ignored_folder_are_still_indexed(tmp_path):
    root = _root(tmp_path)
    (root / "Big" / "tool" / "deep").mkdir(parents=True)
    (root / "Big" / "README.md").write_text("# Big data\n", encoding="utf-8")
    (root / "Big" / "tool" / "GROK.md").write_text("# Grok notes\n", encoding="utf-8")
    (root / "Big" / "tool" / "notes.md").write_text("# not a landmark\n", encoding="utf-8")
    (root / "Big" / "tool" / "deep" / "AGENTS.md").write_text("# too deep\n", encoding="utf-8")
    (root / "Big" / "tool" / ".brainproject").write_text("big-tool\n", encoding="utf-8")
    files, markers = bs.walk_root(root, ["Big/*"])
    rels = {f["rel_path"] for f in files}
    assert {"Big/README.md", "Big/tool/GROK.md", "Big/tool/.brainproject"} <= rels
    assert "Big/tool/notes.md" not in rels and "Big/tool/deep/AGENTS.md" not in rels
    assert not any(m["project"] == "big-tool" for m in markers)  # ignored trees never bind


def test_remote_urls_are_recorded_without_credentials(tmp_path):
    repo = tmp_path / "app"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "config").write_text(
        '[remote "origin"]\n\turl = https://user:tok3n@git.example.com/acme/app.git\n',
        encoding="utf-8")
    assert bs._remote_url(repo) == "https://git.example.com/acme/app.git"


def test_a_password_with_an_at_sign_is_stripped_whole():
    from brain_scan import strip_url_userinfo as cli_strip
    url = "https://u:p" + "@" + "ss" + "@" + "host.example/x.git"
    assert cli_strip(url) == "https://host.example/x.git"
