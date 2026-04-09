#!/usr/bin/env python3
"""
MemoryBrain CLI — brain add / brain import / brain seed / brain status / brain setup

Usage:
    brain setup [--auto-detect]
    brain add "note text" [--project SLUG] [--tags tag1,tag2]
    brain import <path> [--project SLUG]
    brain seed [--project SLUG]
    brain status
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from datetime import datetime

MEMORYBRAIN_DIR = Path(__file__).parent.parent.resolve()
BRAIN_URL = os.getenv("MEMORYBRAIN_URL", "http://localhost:7741")


# ── Project detection ────────────────────────────────────────────────────────

def detect_project(cwd: Path = None) -> str:
    cwd = cwd or Path.cwd()
    bp = cwd / ".brainproject"
    if bp.exists():
        return bp.read_text().strip()
    parts = [p for p in cwd.parts if p not in ("", "/", "mnt", "c", "git", "_git")]
    return parts[-1].lower() if parts else "unknown"


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def _post(path: str, body: dict) -> dict:
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BRAIN_URL}{path}",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError:
        print(f"Brain is not running. Start with:\n  docker compose -f {MEMORYBRAIN_DIR}/docker-compose.yml up -d")
        sys.exit(1)


def _get(path: str) -> dict:
    req = urllib.request.Request(f"{BRAIN_URL}{path}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError:
        print(f"Brain is not running. Start with:\n  docker compose -f {MEMORYBRAIN_DIR}/docker-compose.yml up -d")
        sys.exit(1)


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_add(content: str, project: str = None, tags: list = None):
    project = project or detect_project()
    result = _post("/ingest/note", {
        "content": content,
        "project": project,
        "tags": tags or [],
    })
    print(f"Stored — id: {result['id']}")
    print(f"Summary: {result.get('summary', '')}")


def cmd_import(path: str, project: str = None):
    file_path = Path(path).expanduser().resolve()
    if not file_path.exists():
        print(f"File not found: {file_path}")
        sys.exit(1)
    project = project or detect_project(file_path.parent)
    content = file_path.read_text(encoding="utf-8", errors="replace")
    result = _post("/ingest/note", {
        "content": content,
        "project": project,
        "tags": [],
        "source": str(file_path),
    })
    print(f"Imported {file_path.name} — id: {result['id']}")
    print(f"Summary: {result.get('summary', '')}")


def cmd_seed(project: str = None):
    cwd = Path.cwd()
    project = project or detect_project(cwd)
    files = list(cwd.glob("MEMORY*.md")) + list(cwd.glob("HANDOVER-*.md")) + list(cwd.glob("memory/MEMORY*.md"))
    if not files:
        print("No MEMORY.md or HANDOVER-*.md files found in current directory.")
        return
    print(f"Seeding {len(files)} files into project '{project}'...")
    for f in sorted(files):
        content = f.read_text(encoding="utf-8", errors="replace")
        result = _post("/ingest/note", {"content": content, "project": project, "tags": [], "source": str(f)})
        print(f"  \u2705 {f.name} \u2192 {result['id']}")
    print(f"Done \u2014 {len(files)} files imported.")


def cmd_status():
    _get("/health")
    data = _get("/status")
    print(f"Brain:    \u2705 running ({BRAIN_URL})")
    print(f"Projects: {data.get('project_count', 0)}")
    print(f"Version:  {data.get('version', 'unknown')}")


def _run(cmd: list, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def _file_hash(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.md5(path.read_bytes()).hexdigest()


def cmd_setup(auto_detect: bool = False):
    print("MemoryBrain setup")
    print("\u2500" * 45)

    # 1. Docker running?
    r = _run(["docker", "ps"])
    if r.returncode != 0:
        print("\u274c Docker is not running. Start Docker Desktop / Rancher Desktop first.")
        sys.exit(1)
    print("\u2705 Docker running")

    # 2. Ensure .env exists
    env_path = MEMORYBRAIN_DIR / ".env"
    if not env_path.exists():
        example = MEMORYBRAIN_DIR / ".env.example"
        env_path.write_text(example.read_text() if example.exists() else "")
        print("\u2705 .env created from .env.example")
    else:
        print("\u23ed\ufe0f  .env \u2014 already exists")

    # 3. Start Docker containers
    compose_cmd = ["docker", "compose", "-f", str(MEMORYBRAIN_DIR / "docker-compose.yml")]
    ps = _run(compose_cmd + ["ps", "--status=running"])
    brain_running = "brain" in ps.stdout

    if not brain_running:
        _run(compose_cmd + ["up", "-d"], check=False)
        print("\u2705 Docker containers started")
    else:
        print("\u23ed\ufe0f  Docker containers \u2014 already running")

    # 4. Pull Ollama models
    models_out = _run(compose_cmd + ["exec", "ollama", "ollama", "list"]).stdout
    for model in ["embeddinggemma", "llama3.2:3b"]:
        if not any(line.startswith(model) for line in models_out.splitlines()):
            print(f"\u23f3 Pulling Ollama model: {model} (this may take a few minutes)...")
            _run(compose_cmd + ["exec", "ollama", "ollama", "pull", model])
            print(f"\u2705 {model} pulled")
        else:
            print(f"\u23ed\ufe0f  {model} \u2014 already present")

    # 5. Register MCP server with Claude Code
    mcp_list = _run(["claude", "mcp", "list"])
    if "memorybrain" not in mcp_list.stdout:
        _run(["claude", "mcp", "add", "-s", "user", "--transport", "sse",
              "memorybrain", f"{BRAIN_URL}/sse"])
        print("\u2705 MCP server registered")
    else:
        print("\u23ed\ufe0f  MCP server \u2014 already registered")

    # 6. Install hooks
    hooks_dir = Path.home() / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    hook_pairs = [
        (MEMORYBRAIN_DIR / "hooks" / "session-ingest.sh",
         hooks_dir / "session-start-memory.sh"),
        (MEMORYBRAIN_DIR / "hooks" / "pre-compact-ingest.py",
         hooks_dir / "pre-compact-auto-handover.py"),
    ]
    hooks_installed = False
    for src, dst in hook_pairs:
        if not src.exists():
            print(f"\u26a0\ufe0f  Hook source not found: {src}")
            continue
        if _file_hash(dst) != _file_hash(src):
            shutil.copy2(src, dst)
            dst.chmod(0o755)
            hooks_installed = True
    print("\u2705 Hooks installed" if hooks_installed else "\u23ed\ufe0f  Hooks \u2014 already up to date")

    # 7. Install Claude Code skills
    skills_src = MEMORYBRAIN_DIR / "skills"
    skills_dst = Path.home() / ".claude" / "skills"
    skills_installed = False
    if skills_src.exists():
        for skill_dir in skills_src.iterdir():
            if skill_dir.is_dir():
                skill_file = skill_dir / "SKILL.md"
                if skill_file.exists():
                    dst_skill_dir = skills_dst / skill_dir.name
                    dst_skill_dir.mkdir(parents=True, exist_ok=True)
                    dst_file = dst_skill_dir / "SKILL.md"
                    if _file_hash(dst_file) != _file_hash(skill_file):
                        shutil.copy2(skill_file, dst_file)
                        skills_installed = True
    print("\u2705 Skills installed" if skills_installed else "\u23ed\ufe0f  Skills \u2014 already up to date")

    # 8. Install shell alias + MEMORYBRAIN_DIR export
    # MEMORYBRAIN_DIR is read by the session hook for version checks and start instructions.
    alias_line = f"alias brain='python3 {MEMORYBRAIN_DIR}/cli/brain.py'"
    dir_line = f"export MEMORYBRAIN_DIR='{MEMORYBRAIN_DIR}'"
    shell_added = False
    for rc in [Path.home() / ".bashrc", Path.home() / ".zshrc"]:
        if rc.exists():
            content = rc.read_text()
            needs_alias = "alias brain=" not in content
            needs_dir = "MEMORYBRAIN_DIR=" not in content
            if needs_alias or needs_dir:
                block = "\n# MemoryBrain CLI\n"
                if needs_dir:
                    block += f"{dir_line}\n"
                if needs_alias:
                    block += f"{alias_line}\n"
                rc.write_text(content + block)
                shell_added = True

    if shell_added:
        print("\u2705 Shell config updated (run: source ~/.bashrc)")
    else:
        print("\u23ed\ufe0f  Shell config \u2014 already up to date")

    # 9. Show detected MCP tools from ~/.claude.json (read directly on host)
    print()
    try:
        claude_json = Path.home() / ".claude.json"
        with open(claude_json) as f:
            data = json.load(f)
        servers = data.get("mcpServers", {})
        tools = sorted(servers.keys()) if isinstance(servers, dict) else []
        if tools:
            print("Detected MCP servers in ~/.claude.json:")
            for t in tools:
                print(f"  \u2022 {t}")
            print()
            print("MemoryBrain will capture memories from whatever you retrieve with these tools.")
            print("No credentials needed \u2014 MemoryBrain is a passive store.")
        else:
            print("No MCP servers found in ~/.claude.json.")
            print("Add MCP servers to Claude Code and re-run setup to see them here.")
    except FileNotFoundError:
        print("~/.claude.json not found \u2014 add MCP servers to Claude Code and re-run setup.")
    except Exception:
        pass


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="MemoryBrain CLI")
    sub = parser.add_subparsers(dest="command")

    # setup
    p_setup = sub.add_parser("setup", help="Install MemoryBrain and register with Claude Code")
    p_setup.add_argument("--auto-detect", action="store_true",
                         help="Kept for backwards compatibility. MCP tool detection always runs.")

    # add
    p_add = sub.add_parser("add", help="Add a memory note")
    p_add.add_argument("content", help="Note text")
    p_add.add_argument("--project", help="Project slug")
    p_add.add_argument("--tags", help="Comma-separated tags")

    # import
    p_import = sub.add_parser("import", help="Import a file as a memory")
    p_import.add_argument("path", help="File path to import")
    p_import.add_argument("--project", help="Project slug")

    # seed
    p_seed = sub.add_parser("seed", help="Import all MEMORY*.md and HANDOVER-*.md from current directory")
    p_seed.add_argument("--project", help="Project slug")

    # status
    sub.add_parser("status", help="Show MemoryBrain status")

    args = parser.parse_args()

    if args.command == "setup":
        cmd_setup(auto_detect=getattr(args, "auto_detect", False))
    elif args.command == "add":
        tags = [t.strip() for t in args.tags.split(",")] if args.tags else []
        cmd_add(args.content, project=args.project, tags=tags)
    elif args.command == "import":
        cmd_import(args.path, project=args.project)
    elif args.command == "seed":
        cmd_seed(project=args.project)
    elif args.command == "status":
        cmd_status()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
