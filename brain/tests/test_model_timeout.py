"""A model server that accepts a connection and never answers must not hold a
write, a search or /readiness forever: every Ollama call has a timeout."""
import asyncio
import socket
import threading
import time

import pytest

import app.summarise as s
from app.ingest_pipeline import ingest, write_report
from app.models import MemoryEntry
from app.storage import get_memory


@pytest.fixture
def silent_server():
    """127.0.0.1:<port> accepts connections and never sends a byte."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(16)
    held, stop = [], threading.Event()

    def accept():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
                held.append(conn)
            except OSError:
                continue

    t = threading.Thread(target=accept, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.getsockname()[1]}"
    stop.set()
    t.join(1)
    for c in held:
        c.close()
    srv.close()


@pytest.fixture
def hung_provider(silent_server, monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", silent_server)
    monkeypatch.setenv("MEMORYBRAIN_MODEL_TIMEOUT", "1")
    provider = s.OllamaProvider()
    original = s._provider
    s._provider = provider
    yield provider
    s._provider = original


def test_the_timeout_setting_defaults_to_two_minutes(monkeypatch):
    monkeypatch.delenv("MEMORYBRAIN_MODEL_TIMEOUT", raising=False)
    assert s.model_timeout() == 120.0
    monkeypatch.setenv("MEMORYBRAIN_MODEL_TIMEOUT", "lots")
    assert s.model_timeout() == 120.0
    monkeypatch.setenv("MEMORYBRAIN_MODEL_TIMEOUT", "0")
    assert s.model_timeout() == 120.0
    monkeypatch.setenv("MEMORYBRAIN_MODEL_TIMEOUT", "7.5")
    assert s.model_timeout() == 7.5


@pytest.mark.asyncio
async def test_an_embed_against_a_silent_server_gives_up(hung_provider):
    start = time.monotonic()
    with pytest.raises(Exception):
        await asyncio.wait_for(hung_provider.embed("hello"), timeout=10)
    assert time.monotonic() - start < 5


def test_readiness_answers_quickly_while_the_model_server_hangs(hung_provider, monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as m
    monkeypatch.setenv("MEMORYBRAIN_MODEL_TIMEOUT", "60")
    monkeypatch.setattr(m, "ollama_client", s.OllamaProvider()._client)
    start = time.monotonic()
    body = TestClient(m.app).get("/readiness").json()
    assert time.monotonic() - start < 10
    assert body["checks"]["ollama"] == "error"


@pytest.mark.asyncio
async def test_a_write_is_stored_while_the_model_server_hangs(tmp_db, monkeypatch, hung_provider):
    monkeypatch.setattr("app.ingest_pipeline.DB_PATH", tmp_db)
    body = "The export job moved to the new runner. " * 30
    result = await asyncio.wait_for(
        ingest(MemoryEntry(content=body, type="note", project="acme")), timeout=20)
    report = write_report(result)
    assert report["embedded"] is False
    assert get_memory(result.id, db_path=tmp_db).content == body
