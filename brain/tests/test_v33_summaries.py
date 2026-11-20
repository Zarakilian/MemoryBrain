"""3.3 summaries: the writer owns the summary. A missing one is the opening of
the text, never a small model's rewrite, unless MEMORYBRAIN_SUMMARY_MODE=model."""
import pytest

import app.summarise as s
from app.db import connect
from app.ingest_pipeline import ingest, write_report
from app.mcp import tools as T
from app.models import MemoryEntry

LONG = ("Export job moved to the reporting server on 2 March. "
        "It now runs at 02:00 and writes to the archive share. " + "More detail follows. " * 40)


@pytest.fixture
def ing_db(tmp_db, fake_provider, monkeypatch):
    monkeypatch.setattr("app.ingest_pipeline.DB_PATH", tmp_db)
    monkeypatch.setattr("app.mcp.tools.DB_PATH", tmp_db)
    monkeypatch.delenv("MEMORYBRAIN_SUMMARY_MODE", raising=False)
    return tmp_db


def _source(db, memory_id):
    conn = connect(db)
    try:
        return conn.execute("SELECT summary_source FROM memories WHERE id = ?",
                            (memory_id,)).fetchone()[0]
    finally:
        conn.close()


# ------------------------------------------------------------ the opening

def test_the_opening_is_the_first_sentences_cut_at_a_sentence_end():
    out = s.opening_summary(LONG)
    assert out.startswith("Export job moved to the reporting server on 2 March.")
    assert len(out) <= 280 and out.endswith(".")


def test_the_opening_drops_markdown_marks_and_joins_a_short_title():
    body = "## Session 3 May\n\n- Fixed the export.\n- Moved the job.\n" + "x " * 300
    out = s.opening_summary(body)
    assert out.startswith("Session 3 May Fixed the export.")
    assert "#" not in out and not out.startswith("-")


def test_the_opening_skips_section_labels_and_bold_marks():
    body = ("**Session: editor planning (3 May)**\n\nWhat we worked on\n\n"
            "- Planned the editor as its own app.\n- Priced the calculator.\n" + "x " * 300)
    out = s.opening_summary(body)
    assert out.startswith("Session: editor planning (3 May) Planned the editor as its own app.")
    assert "*" not in out and "What we worked on" not in out


def test_a_title_ending_in_a_colon_takes_the_next_line():
    body = "Key files:\nProject root is the tools folder, with one script per audit.\n" + "y " * 300
    assert s.opening_summary(body).startswith(
        "Key files: Project root is the tools folder, with one script per audit.")


def test_the_opening_of_one_long_sentence_ends_with_an_ellipsis():
    out = s.opening_summary("word " * 200)
    assert len(out) <= 281 and out.endswith("…")


def test_the_opening_keeps_dotted_names_whole():
    body = "Changed export.py and the v2.5 config on wiki.example.com today. " + "pad " * 100
    assert s.opening_summary(body).startswith(
        "Changed export.py and the v2.5 config on wiki.example.com today.")


def test_the_opening_of_nothing_is_empty():
    assert s.opening_summary("") == "" and s.opening_summary("\n \n") == ""


# ------------------------------------------------------------- the write path

async def test_a_long_body_without_a_summary_gets_its_opening_and_no_model_call(ing_db,
                                                                                fake_provider):
    async def never(content, max_sentences=3):
        raise AssertionError("the summary model must not be called")
    fake_provider.summarise = never
    fake_provider.score_importance = never
    result = await ingest(MemoryEntry(content=LONG, type="note", project="acme"))
    assert result.summary == s.opening_summary(LONG)
    assert result.summary_source == "extract" and _source(ing_db, result.id) == "extract"
    report = write_report(result)
    assert report["summary_source"] == "extract"
    assert any("no summary given" in w for w in report["warnings"])
    assert result.importance == 3


async def test_the_writers_summary_is_kept_word_for_word(ing_db):
    entry = MemoryEntry(content=LONG, type="fact", project="acme",
                        summary="Export job runs on the reporting server at 02:00")
    result = await ingest(entry)
    assert result.summary == "Export job runs on the reporting server at 02:00"
    assert _source(ing_db, result.id) == "writer"
    assert write_report(result)["warnings"] == []


async def test_a_short_body_is_its_own_summary(ing_db):
    result = await ingest(MemoryEntry(content="short and sweet", type="note", project="acme"))
    assert result.summary == "short and sweet" and _source(ing_db, result.id) == "verbatim"


async def test_model_mode_still_asks_the_model(ing_db, fake_provider, monkeypatch):
    monkeypatch.setenv("MEMORYBRAIN_SUMMARY_MODE", "model")
    result = await ingest(MemoryEntry(content=LONG, type="note", project="acme"))
    assert result.summary == LONG[:100].strip()   # what the fake model returns
    assert _source(ing_db, result.id) == "model"


async def test_model_mode_falls_back_to_the_opening_when_the_model_fails(ing_db, fake_provider,
                                                                         monkeypatch):
    monkeypatch.setenv("MEMORYBRAIN_SUMMARY_MODE", "model")

    async def broken(content, max_sentences=3):
        raise RuntimeError("model gone")
    fake_provider.summarise = broken
    result = await ingest(MemoryEntry(content=LONG, type="note", project="acme"))
    assert result.summary == s.opening_summary(LONG)
    assert _source(ing_db, result.id) == "extract"
    assert "summary fallback" in write_report(result)["warnings"]


# ----------------------------------------------------------------- add_memory

async def _call(name, args):
    import json
    return json.loads((await T.call_tool(name, args))[0].text)


async def test_add_memory_takes_summary_and_importance(ing_db):
    report = await _call("add_memory", {
        "content": LONG, "type": "fact", "project": "acme",
        "summary": "Export job runs at 02:00 on the reporting server", "importance": 5})
    assert report["summary"] == "Export job runs at 02:00 on the reporting server"
    assert report["summary_source"] == "writer" and report["importance"] == 5


async def test_add_memory_still_accepts_description_as_the_summary(ing_db):
    report = await _call("add_memory", {"content": LONG, "type": "fact", "project": "acme",
                                        "description": "The old argument name"})
    assert report["summary"] == "The old argument name" and report["summary_source"] == "writer"


async def test_add_memory_importance_out_of_range_is_clamped(ing_db):
    report = await _call("add_memory", {"content": "tiny", "type": "note", "project": "acme",
                                        "importance": 9})
    assert report["importance"] == 5


# ---------------------------------------------------------------- resummarise

async def test_resummarise_replaces_model_summaries_and_keeps_the_writers(ing_db):
    conn = connect(ing_db)
    model = await ingest(MemoryEntry(content=LONG, type="note", project="acme",
                                     summary="A " + "wrong model summary that is long " * 8))
    mine = await ingest(MemoryEntry(content=LONG + " second", type="fact", project="acme",
                                    summary="Short writer line"))
    conn.execute("UPDATE memories SET summary_source = '' WHERE id = ?", (model.id,))
    conn.execute("UPDATE memories SET summary_source = '' WHERE id = ?", (mine.id,))
    conn.commit()
    conn.close()

    dry = await _call("brain_admin", {"action": "resummarise", "args": {"project": "acme"}})
    assert dry["dry_run"] is True and dry["would_change"] == 1
    assert dry["examples"][0]["id"] == model.id
    assert _source(ing_db, model.id) == ""

    done = await _call("brain_admin", {"action": "resummarise",
                                       "args": {"project": "acme", "dry_run": False}})
    assert done["changed"] == 1
    conn = connect(ing_db)
    rows = dict(conn.execute("SELECT id, summary FROM memories").fetchall())
    audit = conn.execute("SELECT action, detail FROM memory_audit WHERE memory_id = ?",
                         (model.id,)).fetchall()
    conn.close()
    assert rows[model.id] == s.opening_summary(LONG)
    assert rows[mine.id] == "Short writer line"
    assert audit and audit[0][0] == "resummarise" and "wrong model summary" in audit[0][1]
    assert _source(ing_db, model.id) == "extract"
