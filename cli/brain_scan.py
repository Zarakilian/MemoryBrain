#!/usr/bin/env python3
"""brain scan: walk workspace roots on the HOST and push a file manifest to the brain.

The brain runs in Docker and never sees the filesystem. Everything it knows
about files arrives through here. Standard library only.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import platform
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

STATE_PATH = Path.home() / ".memorybrain" / "scan-state.json"
IGNORE_FILE = Path.home() / ".memorybrain" / "scan-ignore"
MACHINE = platform.node()

IGNORE_DIRS = {".git", ".hg", ".svn", "node_modules", "venv", ".venv", "__pycache__",
               ".pytest_cache", ".mypy_cache", ".idea", ".vs", "bin", "obj"}
IGNORE_FILE_GLOBS = [".env*", "*.pem", "*.key", "*.pfx", "*.p12", "id_rsa*", "id_ed25519*",
                     "*.ppk", "*.kdbx", "credentials*", "*.tfstate", ".npmrc", ".netrc",
                     "*secret*", "*password*", "*.har", "*.tar.gz", "*.zip"]
# Inside a scan-ignored folder these are still indexed (to depth 2) so agents can
# learn what the folder is without the scan walking all of it.
LANDMARK_FILES = ("AGENTS.md", "CLAUDE.md", "GROK.md", "NEXT_SESSION_PROMPT.md", "README.md",
                  ".brainproject")
LANDMARK_DEPTH = 2
HASH_LIMIT = 2 * 1024 * 1024
SIZE_LIMIT = 200 * 1024 * 1024
TITLE_EXTS = {".md"}


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── state ──────────────────────────────────────────────────────────────────

def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {"roots": {}}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=1), encoding="utf-8")


def _extra_globs() -> list[str]:
    try:
        return [l.strip() for l in IGNORE_FILE.read_text(encoding="utf-8").splitlines()
                if l.strip() and not l.startswith("#")]
    except FileNotFoundError:
        return []


# ── walking ────────────────────────────────────────────────────────────────

def is_ignored(rel_parts: tuple[str, ...], name: str, size: int, extra_globs: list[str]) -> bool:
    if any(p in IGNORE_DIRS for p in rel_parts):
        return True
    if size > SIZE_LIMIT:
        return True
    low = name.lower()
    if any(fnmatch.fnmatch(low, g.lower()) for g in IGNORE_FILE_GLOBS):
        return True
    rel = "/".join(rel_parts + (name,))
    return any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(name, g) for g in extra_globs)


def _title(path: Path) -> str:
    if path.suffix.lower() not in TITLE_EXTS:
        return ""
    try:
        # utf-8-sig drops a leading BOM so it never hides the "# " heading; the
        # per-line cap keeps a single huge line (a minified .md) from being read whole.
        with path.open("r", encoding="utf-8-sig", errors="replace") as fh:
            for _ in range(40):
                line = fh.readline(4096)
                if not line:
                    break
                if line.startswith("# "):
                    return line[2:].strip()[:200]
    except OSError:
        pass
    return ""


def _sha(path: Path, size: int) -> str:
    if size > HASH_LIMIT:
        return ""
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _dir_ignored(rel_parts: tuple[str, ...], extra_globs: list[str]) -> bool:
    """True when a scan-ignore glob covers this directory or everything under it,
    so 'vendor/*' prunes 'vendor' and its whole tree (matched via 'vendor/x'); a file
    glob such as '*.har' matches neither the dir nor a child stem, so it prunes nothing."""
    if not extra_globs:
        return False
    rel = "/".join(rel_parts)
    if not rel:
        return False
    probe = rel + "/x"
    return any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(probe, g) for g in extra_globs)


def _file_entry(p: Path, rel: str, st, prev: dict) -> dict:
    mtime = _iso(st.st_mtime)
    old = prev.get(rel)
    if old and old.get("size") == st.st_size and old.get("mtime") == mtime and "sha256" in old:
        sha, title = old["sha256"], old.get("title", "")
    else:
        sha, title = _sha(p, st.st_size), _title(p)
    return {"rel_path": rel, "size": st.st_size, "mtime": mtime, "sha256": sha, "title": title}


def _landmarks(folder: Path, rel_parts: tuple[str, ...], prev: dict) -> list[dict]:
    """Landmark files in a scan-ignored folder and its direct subfolders, found
    without walking the tree. They are files only: an ignored tree never binds."""
    isjunction = getattr(os.path, "isjunction", None)
    places = [(folder, rel_parts)]
    try:
        for sub in sorted(folder.iterdir()):
            if (sub.is_dir() and not sub.is_symlink() and sub.name not in IGNORE_DIRS
                    and not (isjunction and isjunction(sub)) and LANDMARK_DEPTH >= 2):
                places.append((sub, rel_parts + (sub.name,)))
    except OSError:
        return []
    found = []
    for place, parts in places:
        for name in LANDMARK_FILES:
            p = place / name
            try:
                if p.is_symlink() or not p.is_file():
                    continue
                st = p.stat()
            except OSError:
                continue
            if st.st_size <= SIZE_LIMIT:
                found.append(_file_entry(p, "/".join(parts + (name,)), st, prev))
    return found


def walk_root(abs_path: Path, extra_globs: list[str], prev: dict | None = None) -> tuple[list[dict], list[dict]]:
    """Walk one root. Ignored directories, symbolic links and NTFS junctions are
    pruned in place, and scan-ignore globs prune whole trees, so os.walk never
    descends into .git, node_modules, a linked tree, or an ignored tree. Markers
    are read at any depth that is walked. When `prev` (the previous scan state for
    this root, keyed by rel_path with size/mtime/sha256/title) holds an unchanged
    entry, its stored hash and title are reused instead of re-reading the file."""
    prev = prev or {}
    isjunction = getattr(os.path, "isjunction", None)
    files: list[dict] = []
    markers: list[dict] = []
    abs_path = Path(abs_path)
    for dirpath, dirnames, filenames in os.walk(abs_path):
        rel_dir = Path(dirpath).relative_to(abs_path).parts
        kept = []
        for d in sorted(dirnames):
            if d in IGNORE_DIRS:
                continue
            child = os.path.join(dirpath, d)
            if os.path.islink(child) or (isjunction and isjunction(child)):
                continue
            if _dir_ignored(rel_dir + (d,), extra_globs):
                files.extend(_landmarks(Path(child), rel_dir + (d,), prev))
                continue
            kept.append(d)
        dirnames[:] = kept
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.is_symlink():  # a linked file may point anywhere outside the root
                continue
            if name == ".brainproject":
                try:
                    slug = re.sub(r"[^a-z0-9_-]", "", p.read_text(encoding="utf-8").strip().lower())
                except OSError:
                    slug = ""
                if slug:
                    markers.append({"rel_path": "/".join(rel_dir), "project": slug})
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            if is_ignored(rel_dir, name, st.st_size, extra_globs):
                continue
            files.append(_file_entry(p, "/".join(rel_dir + (name,)), st, prev))
    return files, markers


def diff_files(prev: dict[str, dict], files: list[dict]) -> list[dict]:
    out = []
    for f in files:
        old = prev.get(f["rel_path"])
        if old is None or old.get("size") != f["size"] or old.get("mtime") != f["mtime"]:
            out.append(f)
    return out


def build_manifest(root_id: str, abs_path: Path, files: list[dict], markers: list[dict], full: bool) -> dict:
    return {"machine": MACHINE, "root_id": root_id, "abs_path": str(abs_path),
            "scanned_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "full": full, "files": files, "markers": markers}


# ── project discovery ──────────────────────────────────────────────────────

def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:64] or "unknown"


def _remote_url(folder: Path) -> str:
    cfg = folder / ".git" / "config"
    try:
        text = cfg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    m = re.search(r'\[remote "origin"\][^\[]*?url\s*=\s*(\S+)', text)
    return strip_url_userinfo(m.group(1)) if m else ""


def strip_url_userinfo(url: str) -> str:
    """Drop 'user:token@' from a URL. SSH remotes (git@host:owner/repo) are kept."""
    # up to the LAST @ before the host: a password may hold an @ of its own
    return re.sub(r"^([A-Za-z][A-Za-z0-9+.-]{0,30}://)[^/?#\s]*@", r"\1", (url or "").strip())


def discover_folders(abs_path: Path, extra_globs: list[str] | None = None) -> list[dict]:
    out = []
    for d in sorted(Path(abs_path).iterdir()):
        if not d.is_dir() or d.name in IGNORE_DIRS or d.name.startswith("."):
            continue
        if _dir_ignored((d.name,), extra_globs or []):
            continue
        files, _ = walk_root(d, [])
        latest = max((f["mtime"] for f in files), default="")
        out.append({"rel_path": d.name, "is_repo": (d / ".git").exists(), "remote_url": _remote_url(d),
                    "files": len(files), "latest_mtime": latest, "proposed_slug": slugify(d.name)})
    return out


def propose_map(root_id: str, abs_path: Path, known_slugs: set[str],
                extra_globs: list[str] | None = None) -> dict:
    rows = []
    for f in discover_folders(abs_path, extra_globs):
        marker = Path(abs_path) / f["rel_path"] / ".brainproject"
        project = ""
        if marker.exists():
            project = marker.read_text(encoding="utf-8").strip().lower()
        elif f["proposed_slug"] in known_slugs:
            project = f["proposed_slug"]
        rows.append({"rel_path": f["rel_path"], "project": project,
                     "role": "repo" if f["is_repo"] else "primary", "label": "",
                     "remote_url": f["remote_url"], "confirmed": bool(project),
                     "_hint": {"proposed_slug": f["proposed_slug"], "files": f["files"],
                               "latest_mtime": f["latest_mtime"]}})
    return {"root_id": root_id, "abs_path": str(abs_path), "folders": rows,
            "_instructions": "Fill in `project` for each folder (blank = leave unmapped). "
                             "Then run: brain scan --apply <this file>"}


def apply_map(map_path: Path, post) -> dict:
    data = json.loads(Path(map_path).read_text(encoding="utf-8"))
    root = Path(data["abs_path"])
    written, conflicts, folders = 0, [], []
    missing: list[str] = []
    for r in data["folders"]:
        row = {k: r.get(k, "") for k in ("rel_path", "project", "role", "label", "remote_url")}
        row["confirmed"] = bool(r.get("project"))
        folders.append(row)
        if not r.get("project"):
            continue
        marker = root / r["rel_path"] / ".brainproject"
        if not marker.parent.is_dir():
            missing.append(r["rel_path"])
            continue
        if marker.exists():
            on_disk = marker.read_text(encoding="utf-8").strip().lower()
            if on_disk != r["project"]:
                conflicts.append({"rel_path": r["rel_path"], "on_disk": on_disk, "proposed": r["project"]})
                row["project"] = on_disk
            continue
        marker.write_text(r["project"] + "\n", encoding="utf-8")
        written += 1
    posted = post("/workspace/map", {"root_id": data["root_id"], "folders": folders})
    return {"markers_written": written, "conflicts": conflicts, "posted": posted, "missing_folders": missing}


def write_back_markers(root_abs: Path, folders: list[dict]) -> int:
    """Confirmed bindings the brain learned from cwd/tool/init become markers on disk."""
    n = 0
    for f in folders:
        if not f.get("project") or not f.get("confirmed") or f.get("how") == "marker":
            continue
        marker = Path(root_abs) / f["rel_path"] / ".brainproject"
        if marker.parent.is_dir() and not marker.exists():
            marker.write_text(f["project"] + "\n", encoding="utf-8")
            n += 1
    return n


# ── command ────────────────────────────────────────────────────────────────

def cmd_scan(args, post, get) -> int:
    if getattr(args, "apply", None):
        rep = apply_map(Path(args.apply), post)
        print(json.dumps(rep, indent=1))
        return 0
    state = load_state()
    roots: dict = state.setdefault("roots", {})
    if getattr(args, "root", None):
        label = getattr(args, "label", None) or "git"
        roots[label] = {"abs_path": str(Path(args.root).resolve()), "files": roots.get(label, {}).get("files", {})}
    if not roots:
        print("No workspace root configured. Run once with:  brain scan --root \"C:\\work\\repos\" --label git")
        return 1
    extra = _extra_globs()

    for root_id, info in roots.items():
        abs_path = Path(info["abs_path"])
        if not abs_path.is_dir():
            print(f"[{root_id}] root not found on this machine: {abs_path} (skipped)")
            continue
        if getattr(args, "init", False):
            known = set()
            try:
                known = {p["slug"] for p in get("/workspace/map").get("projects", [])}
            except Exception:
                pass
            prop = propose_map(root_id, abs_path, known, extra_globs=extra)
            out = Path.cwd() / "workspace-map.proposed.json"
            out.write_text(json.dumps(prop, indent=1), encoding="utf-8")
            print(f"[{root_id}] proposed map for {len(prop['folders'])} folders -> {out}")
            print("Edit the `project` values, then: brain scan --apply workspace-map.proposed.json")
            continue
        prev = info.get("files", {})
        files, markers = walk_root(abs_path, extra, prev=prev)
        full = bool(getattr(args, "full", False)) or not prev
        send = files if full else diff_files(prev, files)
        manifest = build_manifest(root_id, abs_path, send, markers, full)
        if getattr(args, "dry_run", False):
            print(f"[{root_id}] {'FULL' if full else 'incremental'}: {len(send)} file(s) would be sent, "
                  f"{len(markers)} marker(s)")
            for f in send[:50]:
                print("  " + f["rel_path"])
            continue
        rep = post("/workspace/scan", manifest)
        print(f"[{root_id}] {'full' if full else 'incremental'} scan on {MACHINE}: "
              f"+{rep.get('added', 0)} ~{rep.get('updated', 0)} -{rep.get('deleted', 0)} "
              f"moved {rep.get('moved', 0)}, markers {rep.get('markers_bound', 0)}")
        info["files"] = {f["rel_path"]: {"size": f["size"], "mtime": f["mtime"],
                                         "sha256": f["sha256"], "title": f["title"]} for f in files}
        try:
            wm = get(f"/workspace/map")
            n = write_back_markers(abs_path, [f for f in wm.get("folders", []) if f.get("root_id") == root_id])
            if n:
                print(f"[{root_id}] wrote {n} .brainproject marker(s) for confirmed bindings")
        except Exception:
            pass
    save_state(state)
    return 0
