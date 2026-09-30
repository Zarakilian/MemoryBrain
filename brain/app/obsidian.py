"""Obsidian vault export / import bridge.

Export writes a human-readable Markdown folder you can open as an Obsidian
vault. Import reads Markdown files back into MemoryBrain as typed memories.
Agents keep writing MemoryBrain; humans may mirror into Obsidian.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .models import MemoryEntry, PROJECT_SLUG_RE
from .storage import DB_PATH, _connect, add_memory, get_memory_by_content_hash

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_name(s: str) -> str:
    s = re.sub(r"[^\w\s.-]", "", s or "memory").strip() or "memory"
    return s[:80]


def export_project_markdown(
    project: str,
    out_dir: Path,
    include_archived: bool = False,
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """Write one .md per active memory under out_dir/project/."""
    if not PROJECT_SLUG_RE.match(project):
        return {"error": "invalid project slug"}
    root = Path(out_dir) / project
    root.mkdir(parents=True, exist_ok=True)
    sql = """SELECT id, content, summary, type, tags, source, importance,
                    timestamp, status
             FROM memories WHERE project = ?"""
    if not include_archived:
        sql += " AND status = 'active'"
    sql += " ORDER BY timestamp ASC"
    with _connect(db_path) as conn:
        rows = conn.execute(sql, (project,)).fetchall()
    written = []
    for r in rows:
        try:
            tags = json.loads(r["tags"] or "[]")
        except (ValueError, TypeError):
            tags = []
        title = (r["summary"] or r["content"][:60] or r["id"]).strip().split("\n")[0]
        fname = f"{r['timestamp'][:10]}_{_safe_name(title)}_{r['id'][:8]}.md"
        # YAML frontmatter Obsidian understands
        fm = [
            "---",
            f'id: "{r["id"]}"',
            f'type: {r["type"]}',
            f'project: {project}',
            f'importance: {r["importance"]}',
            f'status: {r["status"]}',
            f'timestamp: "{r["timestamp"]}"',
            f'source: "{(r["source"] or "").replace(chr(34), "")}"',
            f'tags: [{", ".join(json.dumps(t) for t in tags)}]',
            "---",
            "",
            f"# {title}",
            "",
            r["content"] or "",
            "",
        ]
        path = root / fname
        path.write_text("\n".join(fm), encoding="utf-8")
        written.append(str(path.name))
    # project index
    index = root / "INDEX.md"
    index.write_text(
        f"# {project}\n\nExported from MemoryBrain at {_now()}\n\n"
        + "\n".join(f"- [[{w}]]" for w in written),
        encoding="utf-8",
    )
    return {
        "project": project,
        "path": str(root),
        "files": len(written),
        "index": str(index),
    }


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    meta: dict[str, Any] = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k == "tags" and v.startswith("["):
            try:
                meta[k] = json.loads(v.replace("'", '"'))
            except json.JSONDecodeError:
                meta[k] = []
        elif k == "importance":
            try:
                meta[k] = int(v)
            except ValueError:
                meta[k] = 3
        else:
            meta[k] = v
    body = text[m.end():]
    return meta, body


async def import_markdown_dir(
    directory: Path,
    project: Optional[str] = None,
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """Import all .md files under directory into MemoryBrain.

    project slug defaults to the directory name when not provided.
    """
    from .ingest_pipeline import ingest

    directory = Path(directory)
    if not directory.is_dir():
        return {"error": f"not a directory: {directory}"}
    project = project or directory.name
    if not PROJECT_SLUG_RE.match(project):
        # sanitize
        project = re.sub(r"[^a-z0-9_-]+", "-", project.lower()).strip("-")[:64]
    imported, skipped, errors = [], [], []
    for path in sorted(directory.rglob("*.md")):
        if path.name.upper() == "INDEX.MD":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as e:
            errors.append({"file": str(path), "error": str(e)})
            continue
        meta, body = _parse_frontmatter(text)
        content = (body or text).strip()
        if not content:
            skipped.append(str(path.name))
            continue
        # strip leading markdown title
        content = re.sub(r"^#\s+.+\n+", "", content).strip() or content
        existing = get_memory_by_content_hash(content, project, db_path=db_path)
        if existing:
            skipped.append(str(path.name))
            continue
        mtype = meta.get("type") or "note"
        if mtype not in {
            "note", "fact", "session", "handover", "file", "reference",
            "decision", "open_loop", "belief",
        }:
            mtype = "note"
        tags = meta.get("tags") if isinstance(meta.get("tags"), list) else []
        tags = list(tags) + ["obsidian-import"]
        entry = MemoryEntry(
            content=content,
            type=mtype,
            project=project,
            tags=tags,
            source=meta.get("source") or f"obsidian:{path.name}",
            summary=meta.get("summary") or "",
        )
        if meta.get("importance"):
            entry.importance = int(meta["importance"])
        try:
            result = await ingest(entry)
            imported.append({"file": path.name, "id": result.id})
        except Exception as e:
            errors.append({"file": path.name, "error": str(e)})
    return {
        "project": project,
        "imported": len(imported),
        "skipped": len(skipped),
        "errors": errors,
        "items": imported[:50],
    }
