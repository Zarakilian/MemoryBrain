#!/usr/bin/env bash
# MemoryBrain session-start hook
# Injects this project's brief into the session context on startup.
# Called by Claude Code session-start hook. CWD = project directory.

set -euo pipefail

BRAIN_URL="${MEMORYBRAIN_URL:-http://localhost:7741}"
MEMORYBRAIN_DIR="${MEMORYBRAIN_DIR:-}"
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$(command -v python3 || command -v python || echo python3)"
# Windows Python reads and writes pipes as cp1252 unless told otherwise, which
# garbles every non-ASCII note. The brain speaks UTF-8, so the hook does too.
export PYTHONIOENCODING=utf-8
CWD="${1:-}"
# Claude Code does not expand template arguments such as {{cwd}}. The hook
# runs with the project folder as its working directory and Claude Code
# exports CLAUDE_PROJECT_DIR, so trust $1 only when it is a real directory.
if [ -z "$CWD" ] || [ ! -d "$CWD" ]; then
    CWD="${CLAUDE_PROJECT_DIR:-$(pwd)}"
fi
SEARCH_DIR="$CWD"
# Git Bash reports /c/... paths; the brain compares against C:\... roots.
case "$CWD" in
    /*) if command -v cygpath >/dev/null 2>&1; then
            CWD="$(cygpath -w "$CWD" 2>/dev/null || printf '%s' "$CWD")"
        fi ;;
esac

# Validate BRAIN_URL is localhost-only (prevent SSRF via env manipulation)
case "$BRAIN_URL" in
    http://localhost:*|http://127.0.0.1:*|http://\[::1\]:*) ;;
    *) echo "[memorybrain] BRAIN_URL must be localhost — refusing to connect to ${BRAIN_URL}" >&2; exit 0 ;;
esac

# Project slug: .brainproject in this folder or up to 4 parents (confidence 1.0),
# else the folder name (a guess, bound with confidence 0.5 for Doctor to confirm).
PROJECT_SLUG=""
_dir="$SEARCH_DIR"
for _ in 0 1 2 3 4; do
    if [ -f "${_dir}/.brainproject" ]; then
        PROJECT_SLUG=$(tr -cd '[:alnum:]_-' < "${_dir}/.brainproject" | tr 'A-Z' 'a-z')
        # an empty marker names nothing: keep climbing, as the pre-compact hook does
        [ -n "$PROJECT_SLUG" ] && break
    fi
    _parent="$(dirname "$_dir")"
    [ "$_parent" = "$_dir" ] && break
    _dir="$_parent"
done
BIND_CONF=""
if [ -z "$PROJECT_SLUG" ]; then
    PROJECT_SLUG=$(basename "$SEARCH_DIR" | tr 'A-Z' 'a-z' | tr -c 'a-z0-9\n' '-' | sed 's/^-*//; s/-*$//; s/--*/-/g') || PROJECT_SLUG=""
    BIND_CONF=',"confidence":0.5'
fi

# Every request: 3-second cap, identifies itself, carries the key when set.
CURL=(curl -sf -m 3 -H "X-Brain-Client: hook")
if [ -n "${BRAIN_API_KEY:-}" ]; then
    CURL+=(-H "X-Brain-Key: ${BRAIN_API_KEY}")
fi

# ── Container health check ────────────────────────────────────────────────────

if ! "${CURL[@]}" "${BRAIN_URL}/health" > /dev/null 2>&1; then
    echo ""
    echo "## MemoryBrain — NOT RUNNING"
    echo ""
    echo "No session context available. Start the container to restore memory."
    echo ""
    if [ -n "$MEMORYBRAIN_DIR" ] && [ -d "$MEMORYBRAIN_DIR" ]; then
        echo "  cd \"${MEMORYBRAIN_DIR}\" && docker compose up -d"
    else
        echo "  docker compose -f ~/memorybrain/docker-compose.yml up -d"
        echo ""
        echo "  (Set MEMORYBRAIN_DIR in your shell profile to use the exact path)"
    fi
    echo ""
    # Fall back to legacy MEMORY.md if present
    if [ -f "${CWD}/memory/MEMORY.md" ]; then
        echo "## Context (from MEMORY.md — MemoryBrain not running)"
        head -100 "${CWD}/memory/MEMORY.md"
    fi
    exit 0
fi

# ── Update MemoryBrain Last Active timestamp ─────────────────────────────────
# Stamps this project's MEMORY.md so Claude knows MemoryBrain is active and
# should not fall back to reading project files like PROGRESS_LOG.md.
_mb_stamp_memory() {
    local cwd="$1"
    local ts
    ts=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
    local hash
    hash=$(printf '%s' "$cwd" | tr -c '[:alnum:]' '-')
    local mem_file="$HOME/.claude/projects/${hash}/memory/MEMORY.md"
    if [ -f "$mem_file" ]; then
        if grep -q '^\*\*MemoryBrain Last Active' "$mem_file"; then
            sed -i "s|^\*\*MemoryBrain Last Active:.*|**MemoryBrain Last Active:** ${ts}|" "$mem_file"
        else
            sed -i "1s|^|**MemoryBrain Last Active:** ${ts}\n\n|" "$mem_file"
        fi
    fi
}
_mb_stamp_memory "$CWD"

# ── Workspace layer: bind this folder to the project ─────────────────────────
_mb_json_escape() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
if [ -n "${MEMORYBRAIN_DEBUG:-}" ]; then echo "[memorybrain] cwd=${CWD} slug=${PROJECT_SLUG}" >&2; fi
if [ -n "$PROJECT_SLUG" ]; then
    "${CURL[@]}" -X POST "${BRAIN_URL}/workspace/bind" \
        -H "Content-Type: application/json" \
        -d "{\"project\":\"$(_mb_json_escape "$PROJECT_SLUG")\",\"how\":\"cwd\",\"abs_path\":\"$(_mb_json_escape "$CWD")\"${BIND_CONF}}" \
        > /dev/null 2>&1 || true
fi

# ── Version check ─────────────────────────────────────────────────────────────
# Compare repo VERSION file against running container. Warns if git pull happened
# but docker compose up -d --build has not been run yet.

if [ -n "$MEMORYBRAIN_DIR" ] && [ -f "${MEMORYBRAIN_DIR}/VERSION" ]; then
    REPO_VERSION=$(tr -d '[:space:]' < "${MEMORYBRAIN_DIR}/VERSION")
    RUNNING_VERSION=$("${CURL[@]}" "${BRAIN_URL}/status" \
        | "$PY" -c "import sys,json; print(json.load(sys.stdin).get('version','unknown'))" 2>/dev/null \
        || echo "unknown")
    if [ -n "$REPO_VERSION" ] && [ "$RUNNING_VERSION" != "unknown" ] && [ "$REPO_VERSION" != "$RUNNING_VERSION" ]; then
        echo ""
        echo "## MemoryBrain — UPDATE AVAILABLE"
        echo ""
        echo "  Running: v${RUNNING_VERSION}   Repo: v${REPO_VERSION}"
        echo ""
        echo "  Upgrade safely (backup, rebuild, count check):"
        echo "    cd \"${MEMORYBRAIN_DIR}\" && python3 cli/brain.py upgrade"
        echo ""
    fi
fi

# ── Subsystem readiness check ────────────────────────────────────────────────
# On full success: silent. On degraded: what is broken, what still works, how to fix it.

READINESS_MSG=$("${CURL[@]}" "${BRAIN_URL}/readiness" | "$PY" -c "
import sys, json
data = json.load(sys.stdin)
if data.get('ready', True):
    sys.exit(0)  # all OK — print nothing

checks = data.get('checks', {})
lines = ['', '## MemoryBrain — PARTIAL SERVICE', '']

for name, status in checks.items():
    if status != 'ok':
        lines.append(f'  \u2717 {name}: {status}')

lines.append('')

ollama_ok = all(checks.get(k) == 'ok' for k in ('ollama', 'embedding_model', 'summary_model'))
vector_ok = checks.get('vector_store', checks.get('chromadb')) == 'ok'

if not ollama_ok:
    lines.append('  Available:    read, keyword search, add_memory (stored, embedded later)')
    lines.append('  Unavailable:  semantic search')
    lines.append('')
    lines.append('  Fix Ollama:')
elif not vector_ok:
    lines.append('  Available:    read + keyword search + add_memory')
    lines.append('  Unavailable:  semantic search')

print('\n'.join(lines))
" 2>/dev/null || echo "")

if [ -n "$READINESS_MSG" ]; then
    echo "$READINESS_MSG"
    if [ -n "$MEMORYBRAIN_DIR" ]; then
        echo "    cd \"${MEMORYBRAIN_DIR}\" && docker compose up -d"
        echo "    docker compose -f \"${MEMORYBRAIN_DIR}/docker-compose.yml\" exec ollama ollama pull embeddinggemma"
        echo "    docker compose -f \"${MEMORYBRAIN_DIR}/docker-compose.yml\" exec ollama ollama pull llama3.2:3b"
    else
        echo "    docker compose up -d"
        echo "    docker compose exec ollama ollama pull embeddinggemma"
        echo "    docker compose exec ollama ollama pull llama3.2:3b"
    fi
    echo ""
fi

# ── This project's brief ──────────────────────────────────────────────────────
# Pins, procedures, facts, open loops and beliefs of THIS project, rendered as
# data. A project with nothing stored gets one line, never other projects' notes.

BRIEF=""
if [ -n "$PROJECT_SLUG" ] && [ -f "${HOOK_DIR}/render_brief.py" ]; then
    BRIEF=$("${CURL[@]}" "${BRAIN_URL}/project-brief?project=${PROJECT_SLUG}&max_chars=3500" \
        | "$PY" "${HOOK_DIR}/render_brief.py" 2>/dev/null || echo "")
fi
if [ "$(printf '%s\n' "$BRIEF" | grep -c . || true)" -gt 1 ]; then
    echo ""
    echo "$BRIEF"
elif [ -n "$PROJECT_SLUG" ]; then
    echo ""
    echo "MemoryBrain has no stored notes for ${PROJECT_SLUG} yet."
fi

# ── Next-session note ─────────────────────────────────────────────────────────
# Only THIS project's note, labelled with who wrote it and when.

if [ -n "$PROJECT_SLUG" ]; then
    NEXT_NOTE=$("${CURL[@]}" "${BRAIN_URL}/next-session?project=${PROJECT_SLUG}" | "$PY" -c "
import sys, json
data = json.load(sys.stdin)
notes = (data.get('notes') or '').strip()
if notes:
    writer = data.get('writer') or 'unknown'
    day = (data.get('timestamp') or '')[:10] or 'unknown date'
    print(f'## Next-session note from {writer}, {day}')
    print()
    print(notes)
" 2>/dev/null || echo "")
    if [ -n "$NEXT_NOTE" ]; then
        echo ""
        echo "$NEXT_NOTE"
    fi
fi

# ── Available MCP tools ───────────────────────────────────────────────────────
# Read ~/.claude.json directly on the host — never routed through Docker
# (the file contains credentials and must never be mounted into a container)

MCP_TOOLS=$("$PY" -c "
import json, os
path = os.path.expanduser('~/.claude.json')
try:
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    servers = data.get('mcpServers', {})
    tools = sorted(servers.keys()) if isinstance(servers, dict) else []
    if tools:
        print('## Available MCP Tools')
        for t in tools:
            print(f'- {t}')
        print()
        print('MemoryBrain will store what you retrieve with these tools.')
except Exception:
    pass
" 2>/dev/null || echo "")

if [ -n "$MCP_TOOLS" ]; then
    echo ""
    echo "$MCP_TOOLS"
fi

# ── Session start reminder ─────────────────────────────────────────────────────
# Reinforces the CLAUDE.md rule: MemoryBrain MCP tools take priority over files.

echo ""
echo "## MANDATORY: MemoryBrain-first protocol"
echo ""
echo "MemoryBrain is running. You MUST call these MCP tools BEFORE reading any project files:"
echo "  1. mcp__memorybrain__get_startup_summary"
echo "  2. mcp__memorybrain__get_recent_context (days=14)"
echo "  3. When the user says where a project or piece of work lives on disk, call"
echo "     mcp__memorybrain__set_project_info with home_path. Never ask them to map folders by hand."
echo ""
echo "Do NOT read MEMORY.md, PROGRESS_LOG.md, or other files until after checking MemoryBrain."
