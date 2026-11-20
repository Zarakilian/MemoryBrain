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

MODEL_STYLE = ("The export job was moved by the team to the reporting server on March 2, 2026, and "
               "it now runs every night at two in the morning. The job writes its output to the "
               "archive share, which is then checked by the morning report for missing rows.")
# as long as the model's, but plainly a writer's: an id up front, semicolons, a tag in capitals
WRITER_STYLE = ("INC0012345 export job: moved to the reporting server on 2026-03-02; runs at 02:00; "
                "writes to the archive share; MEASURED on both sites, 0 missing rows in the "
                "morning report; the old job is disabled, not deleted; rollback is one config line")


@pytest.mark.parametrize("summary", [
    MODEL_STYLE,
    "Here is a summary of the session in three sentences: the job moved.",
    "Dana updated the export job and then checked the morning report for missing rows. "
    "The team later confirmed that the archive share held every file from the night run.",
])
def test_the_small_models_narrative_style_is_recognised(summary):
    from app.resummarise import looks_model_written
    assert looks_model_written(summary) is True


@pytest.mark.parametrize("summary", [
    WRITER_STYLE,
    "DEPLOYED 2026-03-02: export job on the reporting server. Verified on both sites. Nothing "
    "else changed, and the rollback is one config line.",
    "F8, new: deleting both resources in one apply deadlocks the delete forever. The first one "
    "takes the operator with it, so nothing is left to finish the second.",
    "Export tuning: the settings live in the build, not in the table. Two sites that differ "
    "must differ in the build, and nowhere else in the system.",
    "The export job runs at two in the morning on the reporting server every single night",
    "The unknown topic was the client pointed at both sites, not a missing topic — the "
    "findings file had picked the wrong cause. The second site needs the same fix as the first.",
    "Stopping the workloads does NOT return memory to the host. The process went from 6.6 to "
    "9.2 GB after the sandbox was torn down, so the only fix that works is a full shutdown.",
    "Achievement 36 cleanup moved from on hold to delivered (infrastructure) after the change "
    "completed 28 Sep, and the page was closed at version six. The rest was applied the day after.",
    "tb_export_log on the archive looks like the jackpot log, not the full one. The modules in "
    "it match the ticket exactly, and no other provider writes to it at all.",
    "",
])
def test_a_writers_line_is_never_taken_for_the_models(summary):
    from app.resummarise import looks_model_written
    assert looks_model_written(summary) is False


async def test_resummarise_replaces_model_summaries_and_keeps_the_writers(ing_db):
    conn = connect(ing_db)
    model = await ingest(MemoryEntry(content=LONG, type="note", project="acme",
                                     summary=MODEL_STYLE))
    mine = await ingest(MemoryEntry(content=LONG + " second", type="fact", project="acme",
                                    summary="Short writer line"))
    long_mine = await ingest(MemoryEntry(content=LONG + " third", type="fact", project="acme",
                                         summary=WRITER_STYLE))
    labelled = await ingest(MemoryEntry(content=LONG + " fourth", type="note", project="acme",
                                        summary=WRITER_STYLE.replace("INC0012345", "INC0099999")))
    for memory_id, source in ((model.id, ""), (mine.id, ""), (long_mine.id, ""),
                              (labelled.id, "model")):
        conn.execute("UPDATE memories SET summary_source = ? WHERE id = ?", (source, memory_id))
    conn.commit()
    conn.close()

    dry = await _call("brain_admin", {"action": "resummarise", "args": {"project": "acme"}})
    # the unlabelled model-style one, and the one the brain itself labelled "model"
    assert dry["dry_run"] is True and dry["would_change"] == 2
    assert {e["id"] for e in dry["examples"]} == {model.id, labelled.id}
    assert _source(ing_db, model.id) == ""

    done = await _call("brain_admin", {"action": "resummarise",
                                       "args": {"project": "acme", "dry_run": False}})
    assert done["changed"] == 2
    conn = connect(ing_db)
    rows = dict(conn.execute("SELECT id, summary FROM memories").fetchall())
    audit = conn.execute("SELECT action, detail FROM memory_audit WHERE memory_id = ?",
                         (model.id,)).fetchall()
    conn.close()
    assert rows[model.id] == s.opening_summary(LONG)
    assert rows[mine.id] == "Short writer line"
    assert rows[long_mine.id] == WRITER_STYLE          # long, but a writer's: left alone
    assert audit and audit[0][0] == "resummarise" and "moved by the team" in audit[0][1]
    assert _source(ing_db, model.id) == "extract"
