"""Release checks: the version the brain reports is the VERSION file, and every
setting the app reads is documented in .env.example."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

APP = Path(__file__).resolve().parent.parent / "app"
# os.getenv("X"), os.environ.get("X"), the scheduler's _env_int("X") / _env_bool("X")
ENV_CALL = re.compile(r"""(?:getenv|environ\.get|_env_[a-z]+)\(\s*["']([A-Z][A-Z0-9_]{2,})["']""")
ENV_INDEX = re.compile(r"""environ\[\s*["']([A-Z][A-Z0-9_]{2,})["']\s*\]""")
# a name kept in a constant first: BACKEND_ENV = "MEMORYBRAIN_VECTOR_BACKEND"
ENV_CONST = re.compile(r"""^[A-Z_]*_ENV\s*=\s*["']([A-Z][A-Z0-9_]{2,})["']""", re.M)


def _repo_file(name: str) -> Path:
    """The file next to the app: the repo root in a clone, /app in the image."""
    for parent in Path(__file__).resolve().parents:
        if (parent / name).is_file():
            return parent / name
    raise FileNotFoundError(name)


def test_status_reports_the_version_file(tmp_db, monkeypatch):
    from app.main import app
    monkeypatch.delenv("BRAIN_API_KEY", raising=False)
    monkeypatch.setattr("app.main.DB_PATH", tmp_db)
    version = _repo_file("VERSION").read_text(encoding="utf-8").strip()
    status = TestClient(app, base_url="http://localhost:7741").get("/status").json()
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    assert status["version"] == version


def test_every_setting_the_app_reads_is_documented():
    read = set()
    for path in APP.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        read |= set(ENV_CALL.findall(text)) | set(ENV_INDEX.findall(text)) | set(ENV_CONST.findall(text))
    example = _repo_file(".env.example").read_text(encoding="utf-8")
    documented = set(re.findall(r"^([A-Z][A-Z0-9_]+)=", example, re.M))
    assert {"MEMORYBRAIN_PROVIDER", "MEMORYBRAIN_TOOLS"} <= read  # the scan works
    assert sorted(read - documented) == []
