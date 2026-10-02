from app.workspace.paths import (extract_path_tokens, normalise_rel,
                                 is_path_shaped, ci, split_ext)


def test_extracts_windows_relative_path():
    toks = extract_path_tokens(r"full write-up in Tools\ReportFlow\report-review.md, done")
    assert toks == [r"Tools\ReportFlow\report-review.md"]


def test_extracts_posix_relative_and_absolute_with_spaces():
    text = (r"see docs/open-questions.md and C:\work\repos\Daily Reports\TODO-DailyReports.md "
            r"plus Plans/backlog.md")
    toks = extract_path_tokens(text)
    assert "docs/open-questions.md" in toks
    assert r"C:\work\repos\Daily Reports\TODO-DailyReports.md" in toks
    assert "Plans/backlog.md" in toks


def test_bare_filename_only_when_not_part_of_a_path():
    toks = extract_path_tokens("edit TODO-DailyReports.md then run check.ps1 and docs/x.md")
    assert toks == ["docs/x.md", "TODO-DailyReports.md", "check.ps1"]


def test_urls_confluence_ids_hostnames_are_ignored():
    text = ("https://wiki.example.com/pages/viewpage.action?pageId=12345 "
            "on SRVDB01 with ticket 1000000002 and https://x.y/a/b.md")
    assert extract_path_tokens(text) == []


def test_two_paths_in_one_sentence_do_not_merge():
    toks = extract_path_tokens(r"moved Knowledge\a.md and Other\b.md today")
    assert toks == [r"Knowledge\a.md", r"Other\b.md"]


def test_backticks_and_trailing_punctuation_are_stripped():
    toks = extract_path_tokens("(`Migration\\migration-plan.md`).")
    assert toks == [r"Migration\migration-plan.md"]


def test_normalise_strips_known_root_and_slashes():
    roots = [r"C:\work\repos", "/home/you/work/repos"]
    assert normalise_rel(r"C:\work\repos\Daily Reports\TODO.md", roots) == "Daily Reports/TODO.md"
    assert normalise_rel(r"c:/WORK/repos/Daily Reports/TODO.md", roots) == "Daily Reports/TODO.md"
    assert normalise_rel("./docs/x.md", roots) == "docs/x.md"
    assert normalise_rel(r"Tools\ReportFlow\a.md", roots) == "Tools/ReportFlow/a.md"


def test_helpers():
    assert is_path_shaped("docs/x.md") and not is_path_shaped("x.md")
    assert ci(r"Daily Reports\Docs\X.MD") == "daily reports/docs/x.md"
    assert split_ext("a/b/report.XLSX") == "xlsx" and split_ext("a/b/LICENSE") == ""


def test_leading_bracket_directly_against_path_is_not_swallowed():
    assert extract_path_tokens("see (Tools/ReportFlow/a.md) for detail") == ["Tools/ReportFlow/a.md"]
    assert extract_path_tokens("(docs/foo.md)") == ["docs/foo.md"]
    # a bracket that belongs to the real first segment is kept
    assert extract_path_tokens("kept in (archive)/report.md") == ["(archive)/report.md"]


def test_two_backticked_spans_never_merge():
    toks = extract_path_tokens("run `git log master..feature/workspace-layer` and `.superpowers/sdd/progress.md` next")
    assert toks == [".superpowers/sdd/progress.md"]
    toks = extract_path_tokens("see `Postgres/Redis` then `PROGRESS_LOG.md`")
    assert toks == ["PROGRESS_LOG.md"]


def test_tilde_and_posix_absolute_paths_are_extracted_whole():
    text = ("the global ~/.claude/CLAUDE.md and "
            "/src/etl/Northwind/extract/Northwind_Sales Report.sql")
    assert extract_path_tokens(text) == [
        "~/.claude/CLAUDE.md",
        "/src/etl/Northwind/extract/Northwind_Sales Report.sql",
    ]
    assert extract_path_tokens(r"see ~\.claude\CLAUDE.md") == [r"~\.claude\CLAUDE.md"]


def test_unc_path_is_kept_whole():
    assert extract_path_tokens(r"copy \\fileserver.example\data\Finance\report.xlsx") == \
        [r"\\fileserver.example\data\Finance\report.xlsx"]


def test_token_never_starts_after_a_dot():
    # a %VAR% path is outside the workspace: better nothing than 'claude\settings.json'
    assert extract_path_tokens(r"edit %USERPROFILE%\.claude\settings.json now") == []
    assert extract_path_tokens(r"config ~\.claude.json") == []


# W5: path-dense text must cost about linear time, never quadratic
def _timed(text):
    import time
    start = time.perf_counter()
    toks = extract_path_tokens(text)
    return toks, time.perf_counter() - start


def test_short_slash_tokens_without_extensions_stay_fast():
    text = "a/b c/d " * 12_500                    # 100,000 characters
    toks, took = _timed(text)
    assert toks == []
    assert took < 3, f"took {took:.1f}s"


def test_many_real_paths_stay_fast_and_are_all_found():
    text = "".join(f"see docs/note{i}.md and src/mod{i}.py then " for i in range(2_500))
    toks, took = _timed(text)
    assert took < 3, f"took {took:.1f}s"
    assert len(toks) == 5_000 and toks[0] == "docs/note0.md" and toks[-1] == "src/mod2499.py"


def test_a_long_path_with_spaced_folders_is_still_found():
    path = r"C:\work\repos\Daily Reports\Archive 2026\Q3 Reviews\TODO-DailyReports.md"
    assert extract_path_tokens(f"open {path} now") == [path]


def test_a_folder_name_longer_than_80_characters_is_kept_whole():
    folder = "Quarterly Reports And Reviews For The Platform Delivery Team Archive Folder 2026 Q3"
    sep = chr(92)  # a Windows backslash
    path = "C:" + sep + "work" + sep + folder + sep + "notes.md"
    assert extract_path_tokens(f"open {path} now") == [path]
