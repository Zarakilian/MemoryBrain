"""brain eval: measure search quality on hand-labelled questions.

  brain eval --export-log OUT.jsonl    past searches, one line per question, with
                                       ids you chose pre-filled as "relevant"
  brain eval --run LABELS.jsonl        recall@5, recall@10, MRR, worst 5 questions
             [--json] [--out FILE]

Labels hold real memory ids and questions, so every output path must be
outside the repo.
"""
import asyncio
import json
import sys
import urllib.parse
from pathlib import Path

for _root in (Path(__file__).resolve().parents[1] / "brain", Path(__file__).resolve().parents[1]):
    if (_root / "app" / "evaluation.py").exists():
        sys.path.insert(0, str(_root))
        break

from app.evaluation import run_labels  # noqa: E402  (stdlib only)


def _outside_repo(path: Path, repo: Path) -> bool:
    path, repo = Path(path).resolve(), Path(repo).resolve()
    if path == repo or repo in path.parents:
        print(f"Refusing to write {path}: it is inside the repo. Eval files hold real "
              "questions and memory ids; keep them outside it.")
        return False
    return True


def export_log(out: Path, get, repo: Path) -> int:
    if not _outside_repo(out, repo):
        return 1
    rows = get("/admin/retrieval-log?limit=5000").get("queries", [])
    lines = [json.dumps({"query": r["query"], "project": r.get("project"),
                         "relevant": r.get("relevant", [])}) for r in rows]
    Path(out).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    print(f"Wrote {len(lines)} questions to {out}. Fill in the relevant memory ids, then run:")
    print(f"  brain eval --run {out}")
    return 0


def run(labels: Path, get, repo: Path, as_json: bool = False, out: Path = None) -> int:
    if out is not None and not _outside_repo(out, repo):
        return 1
    def search(query, project):
        params = {"q": query, "limit": 10}
        if project:
            params["project"] = project
        return get("/search?" + urllib.parse.urlencode(params))

    report = asyncio.run(run_labels(labels, search))
    if not report["queries"]:
        print(f"No labelled questions in {labels}: fill in the relevant memory ids first "
              f"({report['skipped']} without any).")
        return 1
    if out is not None:
        Path(out).write_text(json.dumps(report, indent=1), encoding="utf-8")
    if as_json:
        print(json.dumps({k: v for k, v in report.items() if k != "per_query"}))
        return 0
    print(f"questions: {report['queries']}   recall@5: {report['recall@5']:.3f}   "
          f"recall@10: {report['recall@10']:.3f}   MRR: {report['mrr']:.3f}")
    worst = sorted(report["per_query"], key=lambda r: (r["rr"], r["recall@10"]))[:5]
    if worst:
        print("worst questions:")
        for row in worst:
            print(f"  rr={row['rr']:.2f} r@10={row['recall@10']:.2f}  {row['query']}")
    return 0


def cmd_eval(args, get, repo: Path) -> int:
    if getattr(args, "export_log", None):
        return export_log(Path(args.export_log), get, repo)
    if getattr(args, "run", None):
        out = Path(args.out) if getattr(args, "out", None) else None
        return run(Path(args.run), get, repo, as_json=getattr(args, "json", False), out=out)
    print("Use --export-log OUT.jsonl or --run LABELS.jsonl (see brain eval --help)")
    return 1
