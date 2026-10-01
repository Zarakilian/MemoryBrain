"""brain eval CLI: exports and scores through REST, never writes inside the repo."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

for _cand in (Path(__file__).parent.parent.parent / "cli", Path(__file__).parent.parent / "cli"):
    if (_cand / "brain_eval.py").exists():
        sys.path.insert(0, str(_cand))
        break

import brain_eval  # noqa: E402


def _get(routes):
    def get(path):
        for prefix, body in routes.items():
            if path.startswith(prefix):
                return body(path) if callable(body) else body
        raise AssertionError(path)
    return get


def test_run_prints_scores_and_the_worst_questions(tmp_path, capsys):
    labels = tmp_path / "labels.jsonl"
    labels.write_text(json.dumps({"query": "db host", "project": "acme",
                                  "relevant": ["m1"]}) + "\n", encoding="utf-8")
    seen = []

    def search(path):
        seen.append(path)
        return [{"id": "m2"}, {"id": "m1"}]
    code = brain_eval.cmd_eval(SimpleNamespace(run=str(labels), export_log=None, json=False,
                                               out=None), _get({"/search": search}),
                               repo=tmp_path / "repo")
    out = capsys.readouterr().out
    assert code == 0 and "recall@5: 1.000" in out and "MRR: 0.500" in out
    assert "project=acme" in seen[0] and "q=db+host" in seen[0]


def test_a_file_with_no_labelled_questions_is_refused(tmp_path, capsys):
    labels = tmp_path / "labels.jsonl"
    labels.write_text(json.dumps({"query": "db host", "relevant": []}) + "\n", encoding="utf-8")
    code = brain_eval.run(labels, lambda path: [{"id": "m1"}], repo=tmp_path / "repo")
    assert code == 1 and "no labelled questions" in capsys.readouterr().out.lower()


def test_outputs_inside_the_repo_are_refused(tmp_path, capsys):
    repo = tmp_path / "repo"
    (repo / "eval").mkdir(parents=True)
    labels = tmp_path / "labels.jsonl"
    labels.write_text("", encoding="utf-8")
    args = SimpleNamespace(run=str(labels), export_log=None, json=False,
                           out=str(repo / "eval" / "report.json"))
    assert brain_eval.cmd_eval(args, _get({}), repo=repo) == 1
    args = SimpleNamespace(run=None, export_log=str(repo / "log.jsonl"), json=False, out=None)
    assert brain_eval.cmd_eval(args, _get({}), repo=repo) == 1
    assert "inside the repo" in capsys.readouterr().out


def test_export_log_writes_one_line_per_question(tmp_path):
    out = tmp_path / "log.jsonl"
    rows = {"queries": [{"query": "db host", "project": None, "relevant": ["m1"]},
                        {"query": "export", "project": "acme", "relevant": []}]}
    args = SimpleNamespace(run=None, export_log=str(out), json=False, out=None)
    assert brain_eval.cmd_eval(args, _get({"/admin/retrieval-log": rows}),
                               repo=tmp_path / "repo") == 0
    lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert lines == [{"query": "db host", "project": None, "relevant": ["m1"]},
                     {"query": "export", "project": "acme", "relevant": []}]
