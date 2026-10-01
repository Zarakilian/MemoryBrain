"""v3 CLI: client header, no key to remote URLs, hook install map, brain upgrade."""
import inspect
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

for _cand in (Path(__file__).parent.parent.parent / "cli", Path(__file__).parent.parent / "cli"):
    if (_cand / "brain.py").exists():
        sys.path.insert(0, str(_cand))
        break

import brain as cli  # noqa: E402


def _response(body: dict):
    mock = MagicMock()
    mock.read = MagicMock(return_value=json.dumps(body).encode())
    mock.__enter__ = MagicMock(return_value=mock)
    mock.__exit__ = MagicMock(return_value=None)
    return mock


# ------------------------------------------------------------- CLI hardening

def test_every_cli_request_says_it_is_the_cli(monkeypatch):
    monkeypatch.setattr(cli, "_brain_key", lambda: "")
    seen = []

    def capture(req, timeout=None):
        seen.append(req.get_header("X-brain-client"))
        return _response({"id": "x", "summary": "ok", "version": "3", "project_count": 0})

    with patch("urllib.request.urlopen", side_effect=capture):
        cli.cmd_add("a note", project="acme", tags=[])
        cli.cmd_status()
    assert len(seen) == 3 and all(v == "cli" for v in seen)  # add, then /health and /status


def test_a_key_is_never_sent_to_a_remote_url(monkeypatch):
    monkeypatch.setattr(cli, "BRAIN_URL", "http://brain.example:7741")
    monkeypatch.setattr(cli, "_brain_key", lambda: "k" * 12)
    with patch("urllib.request.urlopen") as opened, pytest.raises(SystemExit):
        cli.cmd_add("a note", project="acme", tags=[])
    opened.assert_not_called()


def test_gemini_keys_are_recognised_by_their_real_prefix():
    assert cli.looks_like_google_key("AIza" + "x" * 35)
    assert not cli.looks_like_google_key("sk-" + "proj-" + "x" * 20)


def test_brain_beliefs_lists_and_approves(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_brain_key", lambda: "")
    seen = []

    def fake(req, timeout=None):
        seen.append((req.get_method(), req.full_url))
        if "/api/ui/beliefs" in req.full_url:
            return _response({"beliefs": [{"id": "b1", "project": "acme",
                                            "content": "Exports run nightly [m:a1b2c3d4].",
                                            "sources": [{"id": "a1b2c3d4e5", "summary": "x"}]}]})
        return _response({"id": "b1", "status": "active"})

    with patch("urllib.request.urlopen", side_effect=fake):
        cli.cmd_beliefs()
        cli.cmd_beliefs(approve="b1")
    out = capsys.readouterr().out
    assert "Exports run nightly" in out and "cites a1b2c3d4" in out and "b1: active" in out
    assert seen[1] == ("POST", f"{cli.BRAIN_URL}/api/ui/edit/beliefs/b1/approve")


# ------------------------------------------------------------- hook install map

def _repo_with_hooks(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "hooks").mkdir(parents=True)
    for name in ("session-ingest.sh", "pre-compact-ingest.py", "render_brief.py"):
        (repo / "hooks" / name).write_text(f"# new {name}\n", encoding="utf-8")
    return repo


def test_hooks_install_under_their_installed_names_with_a_backup(tmp_path):
    repo, hooks = _repo_with_hooks(tmp_path), tmp_path / "home" / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "session-start-memory.sh").write_text("# old hook\n", encoding="utf-8")
    changed = cli.install_hooks(repo, hooks, now=datetime(2026, 9, 30, 12, 0, 0))
    assert set(changed) == {"session-start-memory.sh", "pre-compact-auto-handover.py",
                            "render_brief.py"}
    assert (hooks / "session-start-memory.sh").read_text(encoding="utf-8") == \
        "# new session-ingest.sh\n"
    backup = hooks / "session-start-memory.sh.bak-20260930-120000"
    assert backup.read_text(encoding="utf-8") == "# old hook\n"
    assert not (hooks / "session-ingest.sh").exists()
    assert cli.install_hooks(repo, hooks) == []  # already current: nothing changes


def test_setup_and_update_share_the_install_map():
    assert cli.HOOK_INSTALL_MAP == {"session-ingest.sh": "session-start-memory.sh",
                                    "pre-compact-ingest.py": "pre-compact-auto-handover.py",
                                    "render_brief.py": "render_brief.py"}
    for fn in (cli.cmd_setup, cli.cmd_update):
        assert "install_hooks(" in inspect.getsource(fn), fn.__name__


# ------------------------------------------------------------- brain upgrade

class FakeRun:
    """subprocess.run stand-in: records every command and answers the few
    that brain upgrade reads (root commit, compose project, volume, counts)."""

    def __init__(self, root_subject="MemoryBrain 2.5.0: application only, clean start",
                 counts=(120, 120), volume_exists=True, backup_written=True, fail_on=None):
        self.root_subject = root_subject
        self.counts = list(counts)
        self.volume_exists = volume_exists
        self.backup_written = backup_written
        self.fail_on = fail_on
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        text, code = "", 0
        if cmd[:2] == ["git", "log"]:
            text = self.root_subject + "\n"
        elif cmd[:3] == ["docker", "compose", "config"]:
            text = '{"name": "memorybrain"}'
        elif cmd[:3] == ["docker", "volume", "inspect"]:
            code = 0 if self.volume_exists else 1
        elif "SELECT COUNT(*) FROM memories" in " ".join(cmd):
            text = f"{self.counts.pop(0)}\n"
        elif "czf" in cmd and self.backup_written:
            # the tar container writes through the bind mount onto the host
            host_dir = next(a for a in cmd if a.endswith(":/backup"))[:-len(":/backup")]
            name = cmd[cmd.index("czf") + 1].split("/backup/", 1)[1]
            (Path(host_dir) / name).write_bytes(b"backup")
        if self.fail_on and self.fail_on in " ".join(cmd):
            code = 1
        return subprocess.CompletedProcess(cmd, code, stdout=text, stderr="")


def _upgrade(tmp_path, fake, backup_dir=None, env_file=True, ready=True):
    repo = _repo_with_hooks(tmp_path)
    (repo / "skills").mkdir()
    if env_file:
        (repo / ".env").write_text("MEMORYBRAIN_PROVIDER=ollama\n", encoding="utf-8")
    return cli.cmd_upgrade(repo=repo, backup_dir=backup_dir or tmp_path / "backups", run=fake,
                           get_json=lambda url: {"ready": ready, "reembed_pending": 7},
                           sleep=lambda s: None, home=tmp_path / "home")


def _joined(fake):
    return [" ".join(c) for c in fake.calls]


def test_upgrade_refuses_a_clone_older_than_the_clean_start(tmp_path, capsys):
    fake = FakeRun(root_subject="Initial commit")
    assert _upgrade(tmp_path, fake) == 1
    assert "predates the 2026-09-30 clean start" in capsys.readouterr().out
    assert not any("compose stop" in c for c in _joined(fake))


def test_upgrade_refuses_a_backup_folder_inside_the_repo(tmp_path, capsys):
    fake = FakeRun()
    assert _upgrade(tmp_path, fake, backup_dir=tmp_path / "repo" / "backups") == 1
    assert "inside the repo" in capsys.readouterr().out
    assert not any("compose stop" in c for c in _joined(fake))


def test_upgrade_refuses_when_this_folder_has_no_brain_volume(tmp_path, capsys):
    fake = FakeRun(volume_exists=False)
    assert _upgrade(tmp_path, fake) == 1
    assert "memorybrain_brain_data" in capsys.readouterr().out
    assert not any("compose stop" in c for c in _joined(fake))


def test_upgrade_fails_loudly_when_memories_go_missing(tmp_path, capsys):
    fake = FakeRun(counts=(120, 119))
    assert _upgrade(tmp_path, fake) == 1
    assert "119" in capsys.readouterr().out


def test_upgrade_backs_up_before_it_rebuilds(tmp_path, capsys):
    fake = FakeRun(counts=(120, 120))
    assert _upgrade(tmp_path, fake) == 0
    joined = _joined(fake)
    stop = next(i for i, c in enumerate(joined) if c.startswith("docker compose stop brain"))
    tar = next(i for i, c in enumerate(joined) if "czf" in c)
    build = next(i for i, c in enumerate(joined) if c.startswith("docker compose build brain"))
    assert stop < tar < build
    assert "memorybrain_brain_data" in joined[tar] and ":ro" in joined[tar]
    assert "reembed_pending: 7" in capsys.readouterr().out
    assert (tmp_path / "home" / ".claude" / "hooks" / "session-start-memory.sh").exists()


def test_upgrade_needs_the_env_file_before_it_touches_anything(tmp_path, capsys):
    fake = FakeRun()
    assert _upgrade(tmp_path, fake, env_file=False) == 1
    assert ".env" in capsys.readouterr().out
    assert not any("compose stop" in c for c in _joined(fake))


def test_upgrade_stops_when_the_backup_never_reached_this_machine(tmp_path, capsys):
    fake = FakeRun(backup_written=False)
    assert _upgrade(tmp_path, fake) == 1
    out = capsys.readouterr().out
    assert "did not reach" in out and "docker compose start brain" in out
    assert not any("compose build" in c for c in _joined(fake))


def test_a_failed_build_says_how_to_start_the_brain_again(tmp_path, capsys):
    fake = FakeRun(fail_on="compose build")
    assert _upgrade(tmp_path, fake) == 1
    out = capsys.readouterr().out
    assert "docker compose up -d brain" in out and "brain-backup-" in out


def test_upgrade_still_counts_when_readiness_never_comes(tmp_path, capsys):
    fake = FakeRun(counts=(120, 120))
    assert _upgrade(tmp_path, fake, ready=False) == 1
    out = capsys.readouterr().out
    assert "120 memories after the upgrade" in out and "not ready" in out


def test_skills_install_keeps_a_backup_of_a_skill_it_replaces(tmp_path):
    from datetime import datetime
    repo = tmp_path / "repo"
    (repo / "skills" / "handover").mkdir(parents=True)
    (repo / "skills" / "handover" / "SKILL.md").write_text("# stock v3\n", encoding="utf-8")
    skills = tmp_path / "skills"
    (skills / "handover").mkdir(parents=True)
    (skills / "handover" / "SKILL.md").write_text("# my own version\n", encoding="utf-8")
    changed = cli.install_skills(repo, skills, now=datetime(2026, 9, 30, 12, 0, 0))
    assert changed == ["handover"]
    assert (skills / "handover" / "SKILL.md").read_text(encoding="utf-8") == "# stock v3\n"
    backup = skills / "handover" / "SKILL.md.bak-20260930-120000"
    assert backup.read_text(encoding="utf-8") == "# my own version\n"
