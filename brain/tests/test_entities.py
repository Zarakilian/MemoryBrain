"""v3 entities: hosts, tickets, ids, paths, products and env vars a memory mentions."""
import pytest

from app.entities import extract_entities, index_entities, top_entities
from app.models import MemoryEntry
from app.storage import add_memory

SAMPLE = ("Moved the ReportFlow export to wiki.example.com under ticket INC-4821. "
          "The job id is 12345678-1234-4234-8234-123456789abc and it reads "
          "C:\\work\\repos\\Daily Reports\\export.py with BRAIN_API_KEY set. "
          "Also pinged db01.internal about CHG-1177. UTF-8 is just an encoding.")


def test_each_kind_is_found():
    found = set(extract_entities(SAMPLE))
    assert ("host", "wiki.example.com") in found
    assert ("host", "db01.internal") in found
    assert ("ticket", "INC-4821") in found and ("ticket", "CHG-1177") in found
    assert ("uuid", "12345678-1234-4234-8234-123456789abc") in found
    assert ("product", "ReportFlow") in found
    assert ("env_var", "BRAIN_API_KEY") in found
    assert any(kind == "path" and name.endswith("export.py") for kind, name in found)


def test_ordinary_words_are_not_entities():
    found = extract_entities("The Quick brown fox jumped over the lazy dog. Hello World. UTF-8.")
    assert found == []


def _mem(db, content, project="acme"):
    entry = MemoryEntry(content=content, type="note", project=project, importance=3)
    add_memory(entry, db_path=db)
    return entry.id


def test_index_counts_mentions_and_top_entities_groups_per_project(tmp_db):
    a = _mem(tmp_db, "INC-4821 again: INC-4821 is back on wiki.example.com")
    b = _mem(tmp_db, "Closed INC-4821")
    other = _mem(tmp_db, "INC-9999 in another project", project="side-project")
    for mid, text in ((a, "INC-4821 again: INC-4821 is back on wiki.example.com"),
                      (b, "Closed INC-4821"), (other, "INC-9999 in another project")):
        assert index_entities(mid, text, db_path=tmp_db) >= 1
    top = top_entities("acme", db_path=tmp_db)
    first = top[0]
    assert (first["kind"], first["name"], first["mentions"]) == ("ticket", "INC-4821", 3)
    assert set(first["memory_ids"]) == {a, b} and len(first["memory_ids"]) <= 3
    assert "INC-9999" not in [e["name"] for e in top]


def test_reindexing_replaces_old_mentions(tmp_db):
    mid = _mem(tmp_db, "INC-1 then INC-2")
    index_entities(mid, "INC-1 then INC-2", db_path=tmp_db)
    index_entities(mid, "only INC-2 now", db_path=tmp_db)
    names = [e["name"] for e in top_entities("acme", db_path=tmp_db)]
    assert names == ["INC-2"]


@pytest.mark.asyncio
async def test_ingest_indexes_entities_after_the_write(tmp_db, fake_provider, monkeypatch):
    from app.ingest_pipeline import ingest
    from app.timeline import get_entities

    monkeypatch.setattr("app.ingest_pipeline.DB_PATH", tmp_db)
    await ingest(MemoryEntry(content="Fixed CHG-1177 on db01.internal", type="note",
                             project="acme"))
    names = {e["name"] for e in get_entities(project="acme", db_path=tmp_db)["entities"]}
    assert {"CHG-1177", "db01.internal"} <= names



def test_entities_are_backfilled_once_for_an_upgraded_brain(tmp_db):
    from app.entities import backfill_entities, top_entities
    from app.models import MemoryEntry
    from app.storage import add_memory
    add_memory(MemoryEntry(content="The deploy to wiki.example.com failed, see INC-4821.", type="note",
                           project="acme"), db_path=tmp_db)  # stored by 2.x: no entity index
    assert backfill_entities(db_path=tmp_db) == 1
    names = {e["name"].lower() for e in top_entities("acme", db_path=tmp_db)}
    assert "wiki.example.com" in names and "inc-4821" in names
    assert backfill_entities(db_path=tmp_db) == 0  # once only
