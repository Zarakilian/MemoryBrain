# tests/test_vector_sqlite.py
"""v2.0.0 — sqlite-vec backend: CRUD, filtered KNN, Chroma parity, backfill."""
import math
import pytest

from app.vector import (
    vec_add, vec_search, vec_delete, vec_count, vec_ready,
    startup_backfill, get_backend, _serialize,
)
from app.storage import add_memory
from app.models import MemoryEntry


def _mem(id_, project="proj-a", type_="note", content="hello world"):
    return MemoryEntry(id=id_, content=content, type=type_, project=project)


def _vec(seed: float, dim: int = 8) -> list[float]:
    v = [math.sin(seed * (i + 1)) for i in range(dim)]
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v]


@pytest.fixture
def vec_db(tmp_db, monkeypatch):
    monkeypatch.setenv("MEMORYBRAIN_VECTOR_BACKEND", "sqlite_vec")
    return tmp_db


def test_default_backend_is_sqlite_vec(monkeypatch):
    monkeypatch.delenv("MEMORYBRAIN_VECTOR_BACKEND", raising=False)
    assert get_backend() == "sqlite_vec"


def test_add_count_delete_roundtrip(vec_db):
    add_memory(_mem("m1"), db_path=vec_db)
    vec_add("m1", _vec(1.0), {}, db_path=vec_db)
    assert vec_count(db_path=vec_db) == 1
    vec_delete("m1", db_path=vec_db)
    assert vec_count(db_path=vec_db) == 0


def test_upsert_replaces(vec_db):
    add_memory(_mem("m1"), db_path=vec_db)
    vec_add("m1", _vec(1.0), {}, db_path=vec_db)
    vec_add("m1", _vec(2.0), {}, db_path=vec_db)
    assert vec_count(db_path=vec_db) == 1


def test_knn_orders_by_cosine_distance(vec_db):
    for i, seed in enumerate([1.0, 1.01, 5.0], start=1):
        add_memory(_mem(f"m{i}"), db_path=vec_db)
        vec_add(f"m{i}", _vec(seed), {}, db_path=vec_db)
    results = vec_search(_vec(1.0), n_results=3, db_path=vec_db)
    assert [r["id"] for r in results][:2] == ["m1", "m2"]
    assert results[0]["distance"] < 1e-6          # exact self-match
    assert results[0]["distance"] <= results[1]["distance"] <= results[2]["distance"]


def test_search_filters_project_type_status(vec_db):
    add_memory(_mem("m1", project="proj-a", type_="note"), db_path=vec_db)
    add_memory(_mem("m2", project="proj-b", type_="fact"), db_path=vec_db)
    vec_add("m1", _vec(1.0), {}, db_path=vec_db)
    vec_add("m2", _vec(1.0), {}, db_path=vec_db)

    only_a = vec_search(_vec(1.0), filters={"project": "proj-a"}, db_path=vec_db)
    assert [r["id"] for r in only_a] == ["m1"]
    only_fact = vec_search(_vec(1.0), filters={"type": "fact"}, db_path=vec_db)
    assert [r["id"] for r in only_fact] == ["m2"]
    active = vec_search(_vec(1.0), filters={"status": "active"}, db_path=vec_db)
    assert {r["id"] for r in active} == {"m1", "m2"}
    assert active[0]["metadata"]["status"] == "active"


def test_search_result_shape_matches_chroma_contract(vec_db):
    """search.py and ingest_pipeline rely on id/metadata/distance keys."""
    add_memory(_mem("m1"), db_path=vec_db)
    vec_add("m1", _vec(1.0), {}, db_path=vec_db)
    r = vec_search(_vec(1.0), n_results=1, db_path=vec_db)[0]
    assert set(r) == {"id", "metadata", "distance"}
    assert {"project", "type", "status", "timestamp"} <= set(r["metadata"])


def test_dim_mismatch_rows_are_excluded(vec_db):
    add_memory(_mem("m1"), db_path=vec_db)
    add_memory(_mem("m2"), db_path=vec_db)
    vec_add("m1", _vec(1.0, dim=8), {}, db_path=vec_db)
    vec_add("m2", _vec(1.0, dim=16), {}, db_path=vec_db)
    results = vec_search(_vec(1.0, dim=8), n_results=10, db_path=vec_db)
    assert [r["id"] for r in results] == ["m1"]


def test_vec_ready(vec_db):
    assert vec_ready(db_path=vec_db) is True


def test_serialize_is_float32_little_endian():
    assert _serialize([1.0]) == b"\x00\x00\x80?"


def test_startup_backfill_copies_from_chroma(vec_db, tmp_path):
    """Full migration path: memories + embeddings in Chroma, none in SQLite."""
    chromadb = pytest.importorskip("chromadb")
    chroma_dir = tmp_path / "chroma"
    col = chromadb.PersistentClient(path=str(chroma_dir)).get_or_create_collection(
        "memories", metadata={"hnsw:space": "cosine"})
    for i in range(1, 4):
        add_memory(_mem(f"m{i}"), db_path=vec_db)
        col.upsert(ids=[f"m{i}"], embeddings=[_vec(float(i), dim=8)],
                   metadatas=[{"status": "active"}])

    report = startup_backfill(db_path=vec_db, chroma_path=chroma_dir)
    assert report["missing_before"] == 3
    assert report["copied_from_chroma"] == 3
    assert report["still_missing"] == 0
    assert vec_count(db_path=vec_db) == 3
    # parity: KNN(self) returns self at ~zero distance
    top = vec_search(_vec(2.0, dim=8), n_results=1, db_path=vec_db)[0]
    assert top["id"] == "m2" and top["distance"] < 1e-5
    # idempotent: second run is a no-op
    report2 = startup_backfill(db_path=vec_db, chroma_path=chroma_dir)
    assert report2["missing_before"] == 0


def test_startup_backfill_reports_unrecoverable_missing(vec_db, tmp_path):
    add_memory(_mem("m1"), db_path=vec_db)
    report = startup_backfill(db_path=vec_db, chroma_path=tmp_path / "nope")
    assert report["missing_before"] == 1
    assert report["copied_from_chroma"] == 0
    assert report["still_missing"] == 1


def test_startup_backfill_skipped_on_chroma_backend(vec_db, monkeypatch):
    monkeypatch.setenv("MEMORYBRAIN_VECTOR_BACKEND", "chroma")
    assert startup_backfill(db_path=vec_db)["skipped"] is True


@pytest.mark.asyncio
async def test_reembed_missing_uses_provider(vec_db, mock_ollama):
    from app.vector import reembed_missing
    add_memory(_mem("m1"), db_path=vec_db)
    report = await reembed_missing(db_path=vec_db)
    assert report["reembedded"] == 1
    assert vec_count(db_path=vec_db) == 1


@pytest.mark.asyncio
async def test_ingest_end_to_end_on_sqlite_vec(vec_db, mock_ollama, monkeypatch):
    """No mocks on the vector layer: real ingest writes brain.db + vec_memories."""
    monkeypatch.setattr("app.ingest_pipeline.DB_PATH", vec_db)
    from app.ingest_pipeline import ingest
    entry = _mem(None, content="the sqlite-vec migration works end to end")
    entry.id = entry.id or "e2e-1"
    result = await ingest(entry)
    assert vec_count(db_path=vec_db) == 1
    results = vec_search([0.1] * 768, n_results=1, db_path=vec_db)
    assert results[0]["id"] == result.id
