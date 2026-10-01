#!/usr/bin/env python3
"""
MemoryBrain CLI — brain add / brain import / brain seed / brain status / brain setup / brain update

Usage:
    brain setup [--auto-detect]
    brain add "note text" [--project SLUG] [--tags tag1,tag2]
    brain import <path> [--project SLUG]
    brain seed [--project SLUG]
    brain status
    brain update
    brain upgrade [--backup-dir DIR]
    brain scan [--root PATH --label ID] [--full] [--dry-run] [--init] [--apply FILE]
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
from datetime import datetime, timezone
from urllib.parse import urlparse

MEMORYBRAIN_DIR = Path(__file__).parent.parent.resolve()
BRAIN_URL = os.getenv("MEMORYBRAIN_URL", "http://localhost:7741")
_HTTP_TIMEOUT = int(os.getenv("MEMORYBRAIN_HTTP_TIMEOUT", "180"))
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

# Repo hook -> the name Claude Code's settings point at, under ~/.claude/hooks/.
# brain setup, brain update and brain upgrade all install through this map.
HOOK_INSTALL_MAP = {
    "session-ingest.sh": "session-start-memory.sh",
    "pre-compact-ingest.py": "pre-compact-auto-handover.py",
    "render_brief.py": "render_brief.py",
}


def _brain_key() -> str:
    """API key from the environment, else the live install's .env. Never print it."""
    key = os.getenv("BRAIN_API_KEY", "").strip()
    if key:
        return key
    env_path = MEMORYBRAIN_DIR / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("BRAIN_API_KEY=") and not s.startswith("#"):
                return s.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _env_settings(env_path: Path) -> dict:
    """KEY=value lines of an env file (comments skipped, quotes stripped).
    Values are for checks only: never print them."""
    settings = {}
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            key, value = s.split("=", 1)
            settings[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return settings


def _brain_headers(content_type: bool = False) -> dict:
    headers = {"X-Brain-Client": "cli"}
    if content_type:
        headers["Content-Type"] = "application/json"
    key = _brain_key()
    if key:
        if urlparse(BRAIN_URL).hostname not in LOCAL_HOSTS:
            raise SystemExit(
                f"Refusing to send BRAIN_API_KEY to {BRAIN_URL}: MEMORYBRAIN_URL is not "
                "localhost. The brain only listens on this machine; unset MEMORYBRAIN_URL "
                "or point it at http://localhost:7741.")
        headers["X-Brain-Key"] = key
    return headers


# ── Project detection ────────────────────────────────────────────────────────

def detect_project(cwd: Path = None) -> str:
    cwd = cwd or Path.cwd()
    bp = cwd / ".brainproject"
    if bp.exists():
        return bp.read_text().strip()
    parts = [p for p in cwd.parts if p not in ("", "/", "mnt", "c", "git", "repos", "src")]
    return parts[-1].lower() if parts else "unknown"


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def _post(path: str, body: dict) -> dict:
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BRAIN_URL}{path}",
        data=payload,
        headers=_brain_headers(content_type=True),
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"Brain HTTP {e.code} on {path}")
        sys.exit(1)
    except urllib.error.URLError:
        print(f"Brain is not running. Start with:\n  docker compose -f {MEMORYBRAIN_DIR}/docker-compose.yml up -d")
        sys.exit(1)


def _get(path: str) -> dict:
    req = urllib.request.Request(f"{BRAIN_URL}{path}", headers=_brain_headers())
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"Brain HTTP {e.code} on {path}")
        sys.exit(1)
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


def cmd_beliefs(approve: str = None, reject: str = None, project: str = None) -> int:
    """List proposed beliefs with their cited sources, or approve / reject one."""
    from urllib.parse import quote, urlencode
    if approve or reject:
        verdict, memory_id = ("approve", approve) if approve else ("reject", reject)
        reply = _post(f"/api/ui/edit/beliefs/{quote(memory_id)}/{verdict}", {})
        print(f"{memory_id}: {reply.get('status', 'unchanged')}")
        return 0
    params = {"status": "proposed", **({"project": project} if project else {})}
    beliefs = _get("/api/ui/beliefs?" + urlencode(params)).get("beliefs", [])
    if not beliefs:
        print("No beliefs are waiting for approval.")
    for b in beliefs:
        print(f"{b['id']}  [{b['project']}]  {b['content'][:200]}")
        for source in b.get("sources", []):
            print(f"    cites {source['id'][:8]}  {(source.get('summary') or '')[:120]}")
    if beliefs:
        print("Approve with: brain beliefs --approve <id>   Reject with: --reject <id>")
    return 0


def cmd_procedures(confirm: str = None, reject: str = None) -> int:
    """List the rules agents proposed from your corrections, or confirm / reject
    one. Only a person can confirm a rule; no MCP tool can."""
    from urllib.parse import quote
    if confirm or reject:
        verdict, memory_id = ("confirm", confirm) if confirm else ("reject", reject)
        reply = _post(f"/api/ui/edit/procedures/{quote(memory_id)}/{verdict}", {})
        print(f"{memory_id}: {reply.get('status', 'unchanged')}")
        return 0
    rules = _get("/api/ui/procedures?status=proposed").get("procedures", [])
    if not rules:
        print("No rules are waiting for confirmation.")
    for r in rules:
        print(f"{r['id']}  [{r['project']}]  {r['summary']}  (proposed by {r.get('writer') or 'an agent'})")
    if rules:
        print("Confirm with: brain procedures --confirm <id>   Reject with: --reject <id>")
    return 0


def looks_like_google_key(key: str) -> bool:
    """Google AI Studio keys start with 'AIza'."""
    return key.startswith("AIza")


def install_hooks(repo: Path, hooks_dir: Path, now: datetime = None) -> list:
    """Copy each repo hook to its installed name (HOOK_INSTALL_MAP). A file that
    is replaced keeps a .bak-<YYYYMMDD-HHMMSS> copy. Returns installed names."""
    hooks_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    changed = []
    for src_name, dst_name in HOOK_INSTALL_MAP.items():
        src, dst = repo / "hooks" / src_name, hooks_dir / dst_name
        if not src.exists():
            print(f"⚠️  Hook source not found: {src}")
            continue
        if dst.exists():
            if _file_hash(dst) == _file_hash(src):
                continue
            shutil.copy2(dst, dst.with_name(f"{dst.name}.bak-{stamp}"))
        shutil.copy2(src, dst)
        dst.chmod(dst.stat().st_mode | 0o755)
        changed.append(dst_name)
    return changed


def _text_hash(data: bytes) -> str:
    """md5 of a text file, line endings normalised (a CRLF checkout is the same file)."""
    return hashlib.md5(data.replace(b"\r\n", b"\n")).hexdigest()


def _stock_skill_hashes(repo: Path, rel: str, run=subprocess.run) -> set:
    """Hashes of every version of a repo file in its git history. An installed
    copy that matches one is stock; anything else was edited by hand. Empty
    when git is unavailable, which makes every differing copy count as edited."""
    try:
        log = run(["git", "log", "--format=%H", "--", rel], cwd=repo,
                  capture_output=True, text=True)
        hashes = set()
        for sha in (log.stdout or "").split():
            blob = run(["git", "show", f"{sha}:{rel}"], cwd=repo, capture_output=True)
            if blob.returncode == 0:
                hashes.add(_text_hash(blob.stdout))
        return hashes
    except (OSError, ValueError):
        return set()


def install_skills(repo: Path, skills_dir: Path, now: datetime = None,
                   stock_hashes=None) -> dict:
    """Install each skills/<name>/SKILL.md that differs from the installed copy.
    A stock copy (any version this repo ever shipped) is replaced and keeps a
    .bak-<YYYYMMDD-HHMMSS> copy. A copy you edited is never replaced: the new
    version is saved beside it as SKILL.md.new. Returns {"updated", "kept"}."""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    stock = stock_hashes or (lambda rel: _stock_skill_hashes(repo, rel))
    result = {"updated": [], "kept": []}
    src_root = repo / "skills"
    if not src_root.exists():
        return result
    for skill_dir in sorted(src_root.iterdir()):
        skill_file = skill_dir / "SKILL.md"
        if not skill_dir.is_dir() or not skill_file.exists():
            continue
        dst_file = skills_dir / skill_dir.name / "SKILL.md"
        if _file_hash(dst_file) == _file_hash(skill_file):
            continue
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        if dst_file.exists():
            if _text_hash(dst_file.read_bytes()) not in stock(f"skills/{skill_dir.name}/SKILL.md"):
                shutil.copy2(skill_file, dst_file.with_name("SKILL.md.new"))
                result["kept"].append(skill_dir.name)
                continue
            shutil.copy2(dst_file, dst_file.with_name(f"{dst_file.name}.bak-{stamp}"))
        shutil.copy2(skill_file, dst_file)
        result["updated"].append(skill_dir.name)
    return result


def _print_skills(result: dict) -> None:
    for name in result["updated"]:
        print(f"✅ Updated skill: {name}")
    for name in result["kept"]:
        print(f"ℹ️  Kept your edited skill: {name} (the new version is beside it as SKILL.md.new)")


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
    try:
        # On Windows, we might need claude.cmd or the full path from PATH
        claude_cmd = "claude"
        if sys.platform == "win32":
            claude_cmd = shutil.which("claude") or "claude.cmd"

        mcp_list = _run([claude_cmd, "mcp", "list"])
        if "memorybrain" not in mcp_list.stdout:
            _run([claude_cmd, "mcp", "add", "-s", "user", "--transport", "sse",
                  "memorybrain", f"{BRAIN_URL}/sse"])
            print("\u2705 MCP server registered for Claude")
        else:
            print("\u23ed\ufe0f  MCP server for Claude \u2014 already registered")
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("\u23ed\ufe0f  Claude CLI not found or error \u2014 skipping Claude MCP registration")

    # 5a. Remind about Grok MCP (configured in ~/.grok/config.toml — not via CLI)
    grok_cfg = Path.home() / ".grok" / "config.toml"
    if grok_cfg.exists():
        try:
            text = grok_cfg.read_text(encoding="utf-8")
            if "mcp_servers.memorybrain" in text or "[mcp_servers.memorybrain]" in text:
                print("\u23ed\ufe0f  Grok MCP memorybrain \u2014 already present in ~/.grok/config.toml")
            else:
                print("\u26a0\ufe0f  Grok config found but no [mcp_servers.memorybrain], see docs/CONNECTING_ASSISTANTS.md")
        except OSError:
            pass
    else:
        print("\u23ed\ufe0f  ~/.grok/config.toml not found \u2014 skipping Grok MCP check")

    # 5b. Register MCP server with Gemini (Antigravity)
    gemini_config_path = Path.home() / ".gemini" / "antigravity" / "mcp_config.json"
    if gemini_config_path.parent.exists():
        try:
            if gemini_config_path.exists():
                with open(gemini_config_path, "r", encoding="utf-8") as f:
                    try:
                        g_data = json.load(f)
                    except json.JSONDecodeError:
                        g_data = {"mcpServers": {}}
            else:
                g_data = {"mcpServers": {}}
                
            if "mcpServers" not in g_data:
                g_data["mcpServers"] = {}
                
            g_data["mcpServers"]["memorybrain"] = {
                "command": "docker",
                "args": [
                    "exec",
                    "-i",
                    "memorybrain-brain-1",
                    "python",
                    "/app/stdio_server.py"
                ],
                "env": {}
            }
            with open(gemini_config_path, "w", encoding="utf-8") as f:
                json.dump(g_data, f, indent=2)
            print("\u2705 MCP server registered for Gemini")
        except Exception as e:
            print(f"\u26a0\ufe0f  Failed to register Gemini MCP: {e}")

    # 6. Install hooks
    hooks_dir = Path.home() / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    hooks_installed = bool(install_hooks(MEMORYBRAIN_DIR, hooks_dir))
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

    # 10. Optional Gemini setup
    print()
    _setup_gemini_optional()


def _setup_gemini_optional():
    """Optionally guide user through Gemini API key setup."""
    env_path = MEMORYBRAIN_DIR / ".env"
    env_content = env_path.read_text()

    # Check if GOOGLE_API_KEY is already set
    has_key = "GOOGLE_API_KEY=" in env_content and not env_content.split("GOOGLE_API_KEY=")[1].split("\n")[0].strip() == ""

    if has_key:
        print("\u2705 Gemini API key already configured")
        return

    print("AI Provider Setup")
    print("\u2500" * 45)
    print("MemoryBrain defaults to Ollama (local, private, free).")
    print()
    print("Optional: Use Gemini (Google AI) for faster, cloud-based processing.")
    print("  \u2022 Free tier: 15,000 requests/month")
    print("  \u2022 No credit card needed")
    print("  \u2022 Higher quality embeddings")
    print()

    response = input("Would you like to set up Gemini? (y/N): ").strip().lower()

    if response != "y":
        print("\u23ed\ufe0f  Skipping Gemini setup (you can add it later)")
        return

    print()
    print("Getting your Gemini API Key (1 minute)...")
    print("\u2500" * 45)
    print()
    print("1\ufe0f\u20e3  Opening: https://aistudio.google.com/app/apikey")
    print("   (A new browser tab will open)")
    print()

    # Try to open the URL
    try:
        import webbrowser
        webbrowser.open("https://aistudio.google.com/app/apikey")
        print("\u2705 Browser opened")
    except Exception:
        print("\u26a0\ufe0f  Could not auto-open browser. Visit manually:")
        print("   https://aistudio.google.com/app/apikey")

    print()
    print("2\ufe0f\u20e3  In the browser:")
    print("   \u2022 Click 'Create API Key'")
    print("   \u2022 Click 'Create new API key in new project'")
    print("   \u2022 Copy the key (starts with 'AIza')")
    print()

    api_key = input("3\ufe0f\u20e3  Paste your API key here: ").strip()

    if not api_key:
        print("\u274c No key provided. Skipping Gemini setup.")
        return

    if not looks_like_google_key(api_key):
        print("\u26a0\ufe0f  Warning: Key doesn't look like a Google AI Studio key (they start with 'AIza')")
        confirm = input("Continue anyway? (y/N): ").strip().lower()
        if confirm != "y":
            return

    # Update .env with the API key
    new_content = env_content.replace(
        "GOOGLE_API_KEY=",
        f"GOOGLE_API_KEY={api_key}"
    )
    # v3: the provider is chosen only by MEMORYBRAIN_PROVIDER, never by a key.
    if "MEMORYBRAIN_PROVIDER=" in new_content:
        new_content = "\n".join(
            "MEMORYBRAIN_PROVIDER=gemini" if line.startswith("MEMORYBRAIN_PROVIDER=") else line
            for line in new_content.split("\n"))
    else:
        new_content = new_content.rstrip("\n") + "\nMEMORYBRAIN_PROVIDER=gemini\n"
    env_path.write_text(new_content)
    print()
    print("\u2705 API key saved to .env")

    # Restart brain container
    print()
    print("Testing connection...")
    compose_cmd = ["docker", "compose", "-f", str(MEMORYBRAIN_DIR / "docker-compose.yml")]
    _run(compose_cmd + ["restart", "brain"], check=False)

    import time
    time.sleep(3)  # Wait for container to restart

    # Check /readiness
    try:
        result = _get_url(f"{BRAIN_URL}/readiness")
        if result.get("ready"):
            print("\u2705 Gemini connection verified \u2014 ready to use!")
        elif result.get("checks", {}).get("gemini_client") == "ok":
            print("\u2705 Gemini API key is valid and working")
        else:
            error = result.get("checks", {}).get("gemini_client", "unknown error")
            print(f"\u26a0\ufe0f  Gemini verification failed: {error}")
            print("   Check your API key at: https://aistudio.google.com/app/apikey")
    except Exception as e:
        print(f"\u26a0\ufe0f  Could not verify (container may still be starting): {e}")
        print("   Try again in 10 seconds: curl http://localhost:7741/readiness | jq")

    print()
    print("Gemini setup complete!")
    print("MemoryBrain will now use Gemini for embeddings & summaries.")
    print()
    print("To learn more: docs/GEMINI_SETUP_GUIDE.md")


def _get_url(url: str) -> dict:
    """GET a full URL, returning {} on any failure.

    Distinct from _get(path) above, which prefixes BRAIN_URL and exits
    on connection failure. This second helper was previously also named
    _get and shadowed the first, silently breaking `brain status`.
    """
    try:
        req = urllib.request.Request(url, headers={"X-Brain-Client": "cli"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return {}


# ── brain upgrade ────────────────────────────────────────────────────────────

CLEAN_START_MARK = "application only, clean start"
DEFAULT_BACKUP_DIR = Path.home() / "memorybrain-backups"
_COUNT_SQL = ("import sqlite3; print(sqlite3.connect('file:{db}?mode=ro{extra}', uri=True)"
              ".execute('SELECT COUNT(*) FROM memories').fetchone()[0])")


def _compose_project(repo: Path, run) -> str:
    """The compose project name, which prefixes the brain's volume name."""
    r = run(["docker", "compose", "config", "--format", "json"], cwd=repo,
            capture_output=True, text=True)
    try:
        return json.loads(r.stdout)["name"]
    except (ValueError, KeyError, TypeError):
        return os.getenv("COMPOSE_PROJECT_NAME") or repo.name.lower()


def _count_memories(cmd: list, run, cwd: Path = None) -> int:
    r = run(cmd, capture_output=True, text=True, cwd=cwd)
    try:
        return int(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise RuntimeError(f"could not count memories: {r.stderr.strip() or r.stdout.strip()}")


def cmd_upgrade(repo: Path = MEMORYBRAIN_DIR, backup_dir: Path = None, run=subprocess.run,
                get_json=None, sleep=None, home: Path = None) -> int:
    """Move an existing install to this version: check the clone, count, stop,
    back up the volume, rebuild, wait until ready, count again, reinstall hooks.
    Stops at the first failure. Returns a process exit code."""
    import time as _time
    get_json = get_json or _get_url
    sleep = sleep or _time.sleep
    home = home or Path.home()
    repo = Path(repo).resolve()

    def fail(msg: str) -> int:
        print(f"❌ {msg}")
        return 1

    # 1. Only a clone of the clean-start history may upgrade the live brain.
    roots = run(["git", "log", "--max-parents=0", "--format=%s"], cwd=repo,
                capture_output=True, text=True)
    if CLEAN_START_MARK not in (roots.stdout or ""):
        return fail("This clone predates the 2026-09-30 clean start. Rename this folder, "
                    "clone the repo again, and run brain upgrade from the new clone.")
    backup_dir = Path(backup_dir or DEFAULT_BACKUP_DIR).resolve()
    if backup_dir == repo or repo in backup_dir.parents:
        return fail(f"The backup folder {backup_dir} is inside the repo. Brain data must never "
                    "sit in the repo; use a folder outside it (default ~/memorybrain-backups).")

    if not (repo / ".env").is_file():
        return fail(f"No .env in {repo}. Copy the .env from your old install folder into this "
                    "one (it holds your settings and key), then run brain upgrade again.")
    settings = _env_settings(repo / ".env")
    cloud = [k for k in ("GOOGLE_API_KEY", "OPENAI_API_KEY") if settings.get(k)]
    if cloud and not settings.get("MEMORYBRAIN_PROVIDER"):
        return fail(f"Your .env sets {' and '.join(cloud)} but not MEMORYBRAIN_PROVIDER. 2.x used "
                    "such a key on its own; 3.0 uses only MEMORYBRAIN_PROVIDER, so the brain would "
                    "quietly move to local Ollama and compare your old cloud vectors with new "
                    "local ones. Add MEMORYBRAIN_PROVIDER=gemini (or openai) to keep the cloud "
                    "provider, or MEMORYBRAIN_PROVIDER=ollama to move to local models, then run "
                    "brain upgrade again.")

    # 2. The brain this folder's compose project owns must already exist.
    project = _compose_project(repo, run)
    volume, image = f"{project}_brain_data", f"{project}-brain"
    if run(["docker", "volume", "inspect", volume], capture_output=True, text=True).returncode:
        return fail(f"No volume {volume} for compose project '{project}'. Run brain upgrade "
                    "from the folder of your live install (or set COMPOSE_PROJECT_NAME), so the "
                    "upgrade cannot start an empty brain next to your real one.")
    # Count through the running brain when it is up: it holds the WAL. A stopped
    # brain is read from its volume instead; with no -shm file, which a read-only
    # mount cannot create, a WAL database only opens as immutable (exact, since
    # nothing is writing).
    try:
        before = _count_memories(["docker", "compose", "exec", "-T", "brain", "python", "-c",
                                  _COUNT_SQL.format(db="/app/data/brain.db", extra="")],
                                 run, cwd=repo)
    except RuntimeError:
        try:
            before = _count_memories(["docker", "run", "--rm", "--entrypoint", "python",
                                      "-v", f"{volume}:/data:ro", image, "-c",
                                      _COUNT_SQL.format(db="/data/brain.db",
                                                        extra="&immutable=1")], run)
        except RuntimeError as e:
            return fail(str(e))
    print(f"✅ {before} memories in {volume}")

    # 3. Stop, then back up the whole volume (brain.db and its WAL files).
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir.mkdir(parents=True, exist_ok=True)
    name = f"brain-backup-{stamp}.tar.gz"
    archive = backup_dir / name
    restart = {
        "backing up the volume": " The brain is stopped. Start it again with: "
                                 "docker compose start brain",
        "building the new image": f" The brain is stopped; the backup is {archive}. "
                                  "Start it again with: docker compose up -d brain",
        "starting the brain": f" The backup is {archive}. Start it again with: "
                              "docker compose up -d brain",
    }
    steps = [
        (["docker", "compose", "stop", "brain"], "stopping the brain"),
        (["docker", "run", "--rm", "--entrypoint", "tar", "-v", f"{volume}:/data:ro",
          "-v", f"{backup_dir}:/backup", image, "czf", f"/backup/{name}", "-C", "/data", "."],
         "backing up the volume"),
        (["docker", "compose", "build", "brain"], "building the new image"),
        (["docker", "compose", "up", "-d"], "starting the brain"),
    ]
    for cmd, what in steps:
        if run(cmd, cwd=repo).returncode:
            return fail(f"Failed while {what}: {' '.join(cmd)}.{restart.get(what, '')}")
        if what == "backing up the volume":
            # tar can exit 0 while writing inside the Docker VM instead of onto this
            # machine (a path the engine cannot see, another WSL distro, a remote context)
            if not archive.is_file() or archive.stat().st_size == 0:
                return fail(f"The backup did not reach {archive} on this machine, so nothing "
                            f"was rebuilt.{restart[what]}")
            print(f"✅ Backup: {archive}")

    # 4. Wait for readiness, then prove nothing was lost (count even if never ready).
    ready = {}
    for _ in range(36):
        ready = get_json(f"{BRAIN_URL}/readiness") or {}
        if ready.get("ready"):
            break
        sleep(5)
    try:
        after = _count_memories(["docker", "compose", "exec", "-T", "brain", "python", "-c",
                                 _COUNT_SQL.format(db="/app/data/brain.db", extra="")],
                                run, cwd=repo)
    except RuntimeError as e:
        return fail(f"{e}. The backup is {archive}.")
    if after < before:
        return fail(f"Memory count fell from {before} to {after}. Restore from "
                    f"{archive} before doing anything else.")
    print(f"✅ {after} memories after the upgrade (before: {before})")
    if ready.get("ready"):
        print(f"   reembed_pending: {ready.get('reembed_pending', 'unknown')} "
              "(old vectors are re-embedded in the background; search keeps working)")

    # 5. Hooks under their installed names, and skills.
    for hook in install_hooks(repo, home / ".claude" / "hooks"):
        print(f"✅ Updated hook: {hook}")
    _print_skills(install_skills(repo, home / ".claude" / "skills"))
    if not ready.get("ready"):
        broken = ", ".join(f"{k}: {v}" for k, v in (ready.get("checks") or {}).items()
                           if v != "ok")
        return fail(f"The brain is running but not ready after 180 seconds "
                    f"({broken or 'no answer from /readiness'}). Your memories are all there. "
                    "Fix what /readiness reports (often a missing Ollama model), then check: "
                    "curl -s http://localhost:7741/readiness")
    print("✅ Upgrade complete. Open a new session to use it.")
    return 0


def cmd_update():
    """Update MemoryBrain: git pull, rebuild Docker, reinstall hooks and skills."""
    import os

    # Locate repo directory
    repo_dir = os.getenv("MEMORYBRAIN_DIR")
    if not repo_dir:
        cwd = Path(os.getcwd())
        if (cwd / "brain").exists() and (cwd / "cli").exists():
            repo_dir = str(cwd)
        else:
            print("❌ Cannot find MemoryBrain repo.")
            print("   Set MEMORYBRAIN_DIR env var or run from the repo directory.")
            sys.exit(1)
            return

    repo_path = Path(repo_dir)

    # 1. git pull — try tracked remote first, fall back to origin master
    print("⬇️  Pulling latest changes...")
    result = subprocess.run(["git", "pull"], cwd=repo_path, capture_output=True, text=True)
    if result.returncode != 0:
        # No tracking remote — pull origin master explicitly
        result = subprocess.run(
            ["git", "pull", "origin", "master"],
            cwd=repo_path, capture_output=True, text=True,
        )
    if result.returncode != 0:
        print(f"❌ git pull failed:\n{result.stderr}")
        sys.exit(1)
        return
    print(result.stdout.strip() or "Already up to date.")

    # 2. Rebuild Docker (migrations run automatically at container startup)
    print("🔨 Rebuilding Docker image...")
    result = subprocess.run(
        ["docker", "compose", "up", "-d", "--build"],
        cwd=repo_path,
    )
    if result.returncode != 0:
        print("❌ Docker rebuild failed.")
        sys.exit(1)
    print("✅ Docker rebuilt — migrations applied automatically at startup.")

    # 3. Reinstall hooks (under their installed names) and skills if changed
    for name in install_hooks(repo_path, Path.home() / ".claude" / "hooks"):
        print(f"✅ Updated hook: {name}")
    _print_skills(install_skills(repo_path, Path.home() / ".claude" / "skills"))

    print("\n✅ MemoryBrain updated successfully.")
    print("   Open a new Claude Code session to use the updated tools.")


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

    # update
    sub.add_parser("update", help="Update MemoryBrain: git pull, rebuild Docker, reinstall hooks and skills")

    # procedures (v3)
    p_procs = sub.add_parser("procedures", help="Confirm or reject rules learned from corrections")
    p_procs.add_argument("--list", action="store_true", help="List proposed rules (default)")
    p_procs.add_argument("--confirm", metavar="ID", help="Make a proposed rule official")
    p_procs.add_argument("--reject", metavar="ID", help="Archive a proposed rule")

    # beliefs (v3)
    p_beliefs = sub.add_parser("beliefs", help="Review beliefs the sleep cycle proposed")
    p_beliefs.add_argument("--list", action="store_true", help="List proposed beliefs (default)")
    p_beliefs.add_argument("--approve", metavar="ID", help="Make a proposed belief active")
    p_beliefs.add_argument("--reject", metavar="ID", help="Archive a proposed belief")
    p_beliefs.add_argument("--project", help="Only this project")

    # eval (v3)
    p_eval = sub.add_parser("eval", help="Measure search quality on labelled questions")
    p_eval.add_argument("--export-log", help="Write past searches as JSONL, ready to label")
    p_eval.add_argument("--run", help="Score a labels JSONL file")
    p_eval.add_argument("--json", action="store_true", help="Print the scores as JSON")
    p_eval.add_argument("--out", help="Write the full report here (outside the repo)")

    # upgrade (v3)
    p_upgrade = sub.add_parser("upgrade", help="Back up the brain, rebuild to this version, verify counts")
    p_upgrade.add_argument("--backup-dir", help="Folder for the volume backup (default ~/memorybrain-backups)")

    # scan (v2.5 workspace layer)
    p_scan = sub.add_parser("scan", help="Push a file manifest of your workspace roots to the brain")
    p_scan.add_argument("--root", help='Add/replace a root, e.g. "C:\\work\\repos"')
    p_scan.add_argument("--label", help="Root id for --root (default: git)")
    p_scan.add_argument("--full", action="store_true", help="Send every file, not just changes")
    p_scan.add_argument("--dry-run", action="store_true", help="Show what would be sent, send nothing")
    p_scan.add_argument("--init", action="store_true", help="Write workspace-map.proposed.json for review")
    p_scan.add_argument("--apply", help="Apply an edited workspace-map.proposed.json")

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
    elif args.command == "update":
        cmd_update()
    elif args.command == "procedures":
        sys.exit(cmd_procedures(confirm=args.confirm, reject=args.reject))
    elif args.command == "beliefs":
        sys.exit(cmd_beliefs(approve=args.approve, reject=args.reject, project=args.project))
    elif args.command == "eval":
        from brain_eval import cmd_eval
        sys.exit(cmd_eval(args, _get, MEMORYBRAIN_DIR))
    elif args.command == "upgrade":
        sys.exit(cmd_upgrade(backup_dir=Path(args.backup_dir) if args.backup_dir else None))
    elif args.command == "scan":
        from brain_scan import cmd_scan
        sys.exit(cmd_scan(args, _post, _get))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
