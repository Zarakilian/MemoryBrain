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

def _post(path: str, body: dict, status_ok: int = 201) -> dict:
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
    active = "  ".join(f"{p} \u2705" for p in data.get("active_plugins", [])) or "none"
    inactive = "  ".join(f"{p} \u274c" for p in data.get("inactive_plugins", [])) or ""
    print(f"Brain:    \u2705 running ({BRAIN_URL})")
    print(f"Projects: {data.get('project_count', 0)}")
    print(f"Plugins:  {active}" + (f"  {inactive}" if inactive else ""))


def _run(cmd: list, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def _file_hash(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.md5(path.read_bytes()).hexdigest()


def _step(label: str, done: bool, action_done: str = "", action_skip: str = "already done"):
    if done:
        print(f"\u2705 {label} \u2014 {action_done}")
    else:
        print(f"\u23ed\ufe0f  {label} \u2014 {action_skip}")


def cmd_setup(auto_detect: bool = False):
    print("MemoryBrain setup")
    print("\u2500" * 45)

    # 1. Docker running?
    r = _run(["docker", "ps"])
    if r.returncode != 0:
        print("\u274c Docker is not running. Start Docker Desktop / Rancher Desktop first.")
        sys.exit(1)
    print("\u2705 Docker running")

    # 2. Auto-detect credentials from ~/.claude.json
    env_path = MEMORYBRAIN_DIR / ".env"
    env_updates: dict[str, str] = {}

    if auto_detect:
        claude_json = Path.home() / ".claude.json"
        if claude_json.exists():
            try:
                config = json.loads(claude_json.read_text())
                mcp_servers = config.get("mcpServers", {})

                # Confluence from mcp-atlassian
                if "mcp-atlassian" in mcp_servers:
                    args = mcp_servers["mcp-atlassian"].get("args", [])
                    for i, arg in enumerate(args):
                        if arg == "-e" and i + 1 < len(args):
                            kv = args[i + 1]
                            if "=" in kv:
                                k, v = kv.split("=", 1)
                                if k == "CONFLUENCE_URL":
                                    env_updates["CONFLUENCE_URL"] = v
                                elif k in ("CONFLUENCE_PERSONAL_TOKEN", "CONFLUENCE_TOKEN"):
                                    env_updates["CONFLUENCE_TOKEN"] = v

                # PagerDuty token
                for server_name, server_cfg in mcp_servers.items():
                    if "pagerduty" in server_name.lower():
                        for arg in server_cfg.get("args", []):
                            if "PAGERDUTY" in arg and "=" in arg:
                                k, v = arg.split("=", 1)
                                env_updates["PAGERDUTY_TOKEN"] = v
            except Exception as e:
                print(f"\u26a0\ufe0f  Could not parse ~/.claude.json: {e}")

        detected = list(env_updates.keys())
        if detected:
            print(f"\u2705 Auto-detected credentials: {', '.join(detected)}")
        else:
            print("\u26a0\ufe0f  No credentials auto-detected from ~/.claude.json")

    # 3. Write/update .env
    if not env_path.exists():
        example = MEMORYBRAIN_DIR / ".env.example"
        env_path.write_text(example.read_text() if example.exists() else "")

    if env_updates:
        lines = env_path.read_text().splitlines()
        existing_keys = {l.split("=")[0] for l in lines if "=" in l and not l.startswith("#")}
        with open(env_path, "a") as f:
            for k, v in env_updates.items():
                if k not in existing_keys:
                    f.write(f"\n{k}={v}")
        print(f"\u2705 .env updated")
    else:
        print(f"\u23ed\ufe0f  .env \u2014 no changes")

    # 4. Start Docker containers
    compose_cmd = ["docker", "compose", "-f", str(MEMORYBRAIN_DIR / "docker-compose.yml")]
    ps = _run(compose_cmd + ["ps", "--status=running"])
    brain_running = "brain" in ps.stdout

    if not brain_running:
        _run(compose_cmd + ["up", "-d"], check=False)
        print("\u2705 Docker containers started")
    else:
        print("\u23ed\ufe0f  Docker containers \u2014 already running")

    # 5. Pull Ollama models
    models_out = _run(compose_cmd + ["exec", "ollama", "ollama", "list"]).stdout
    for model in ["embeddinggemma", "llama3.2:3b"]:
        if model not in models_out:
            print(f"\u23f3 Pulling Ollama model: {model} (this may take a few minutes)...")
            _run(compose_cmd + ["exec", "ollama", "ollama", "pull", model])
            print(f"\u2705 {model} pulled")
        else:
            print(f"\u23ed\ufe0f  {model} \u2014 already present")

    # 6. Register MCP server with Claude Code
    mcp_list = _run(["claude", "mcp", "list"])
    if "memorybrain" not in mcp_list.stdout:
        _run(["claude", "mcp", "add", "-s", "user", "--transport", "sse",
              "memorybrain", f"{BRAIN_URL}/sse"])
        print("\u2705 MCP server registered")
    else:
        print("\u23ed\ufe0f  MCP server \u2014 already registered")

    # 7. Install hooks
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
            import shutil
            shutil.copy2(src, dst)
            dst.chmod(0o755)
            hooks_installed = True
    print("\u2705 Hooks installed" if hooks_installed else "\u23ed\ufe0f  Hooks \u2014 already up to date")

    # 8. Install shell alias
    alias_line = f"alias brain='python3 {MEMORYBRAIN_DIR}/cli/brain.py'"
    alias_added = False
    for rc in [Path.home() / ".bashrc", Path.home() / ".zshrc"]:
        if rc.exists():
            content = rc.read_text()
            if "alias brain=" not in content:
                rc.write_text(content + f"\n# MemoryBrain CLI\n{alias_line}\n")
                alias_added = True

    if alias_added:
        print("\u2705 Shell alias added (run: source ~/.bashrc)")
    else:
        print("\u23ed\ufe0f  Shell alias \u2014 already present")

    # Check for missing manual config
    env_text = env_path.read_text() if env_path.exists() else ""
    warnings = []
    if not any(f"PAGERDUTY_TOKEN=" in l and l.split("=", 1)[1].strip()
               for l in env_text.splitlines() if not l.startswith("#")):
        warnings.append("PAGERDUTY_TOKEN not set \u2014 add to .env to enable PagerDuty plugin")

    print()
    print(f"Brain is running at {BRAIN_URL}")
    for w in warnings:
        print(f"\u26a0\ufe0f  {w}")


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(prog="brain", description="MemoryBrain CLI")
    sub = parser.add_subparsers(dest="command")

    # setup
    p_setup = sub.add_parser("setup", help="Idempotent full setup")
    p_setup.add_argument("--auto-detect", action="store_true",
                         help="Read ~/.claude.json to pre-fill credentials")

    # add
    p_add = sub.add_parser("add", help="Store a quick note")
    p_add.add_argument("content", help="Note text")
    p_add.add_argument("--project", default=None)
    p_add.add_argument("--tags", default="")

    # import
    p_import = sub.add_parser("import", help="Import a file")
    p_import.add_argument("path", help="Path to file")
    p_import.add_argument("--project", default=None)

    # seed
    p_seed = sub.add_parser("seed", help="Bulk import MEMORY.md + HANDOVER files from CWD")
    p_seed.add_argument("--project", default=None)

    # status
    sub.add_parser("status", help="Show brain status")

    args = parser.parse_args()

    if args.command == "setup":
        cmd_setup(auto_detect=args.auto_detect)
    elif args.command == "add":
        tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
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
