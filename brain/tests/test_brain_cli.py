# brain/tests/test_brain_cli.py
import pytest
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add cli/ to path for import
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "cli"))


def _make_response(status_code: int = 200, body: dict = None):
    mock = MagicMock()
    mock.status = status_code
    mock.read = MagicMock(return_value=json.dumps(body or {}).encode())
    mock.__enter__ = MagicMock(return_value=mock)
    mock.__exit__ = MagicMock(return_value=None)
    return mock


def test_brain_add_calls_ingest_note(monkeypatch, capsys):
    response_body = {"id": "abc-123", "summary": "Test note stored.", "importance": 3}

    with patch("urllib.request.urlopen", return_value=_make_response(201, response_body)):
        import brain as brain_cli
        brain_cli.cmd_add("test note content", project="monitoring", tags=[])

    captured = capsys.readouterr()
    assert "abc-123" in captured.out


def test_brain_status_shows_running(monkeypatch, capsys):
    health_resp = _make_response(200, {"status": "ok"})
    status_resp = _make_response(200, {
        "project_count": 3,
        "version": "0.4.0",
    })

    with patch("urllib.request.urlopen", side_effect=[health_resp, status_resp]):
        import brain as brain_cli
        brain_cli.cmd_status()

    captured = capsys.readouterr()
    assert "running" in captured.out
    assert "3" in captured.out


def test_brain_status_shows_not_running(monkeypatch, capsys):
    import urllib.error

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
        import brain as brain_cli
        with pytest.raises(SystemExit) as exc:
            brain_cli.cmd_status()
        assert exc.value.code == 1

    captured = capsys.readouterr()
    assert "not running" in captured.out.lower() or "not running" in captured.err.lower()


def test_detect_project_from_brainproject_file(tmp_path):
    (tmp_path / ".brainproject").write_text("monitoring\n")
    import brain as brain_cli
    result = brain_cli.detect_project(tmp_path)
    assert result == "monitoring"


def test_detect_project_heuristic(tmp_path):
    project_dir = tmp_path / "mnt" / "c" / "git" / "_git" / "Monitoring"
    project_dir.mkdir(parents=True)
    import brain as brain_cli
    result = brain_cli.detect_project(project_dir)
    assert result == "monitoring"
