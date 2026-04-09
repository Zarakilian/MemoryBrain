#!/usr/bin/env bash
# MemoryBrain session-start hook
# Injects a compact project summary into session context on startup.
# Called by Claude Code session-start hook. CWD = project directory.

set -euo pipefail

BRAIN_URL="${MEMORYBRAIN_URL:-http://localhost:7741}"
MEMORYBRAIN_DIR="${MEMORYBRAIN_DIR:-}"
CWD="${1:-$(pwd)}"

# Validate BRAIN_URL is localhost-only (prevent SSRF via env manipulation)
case "$BRAIN_URL" in
    http://localhost:*|http://127.0.0.1:*|http://\[::1\]:*) ;;
    *) echo "[memorybrain] BRAIN_URL must be localhost — refusing to connect to ${BRAIN_URL}" >&2; exit 0 ;;
esac

# Detect project slug: check for .brainproject file first, then heuristic
PROJECT_SLUG=""
if [ -f "${CWD}/.brainproject" ]; then
    PROJECT_SLUG=$(cat "${CWD}/.brainproject" | tr -cd '[:alnum:]_-')
fi

# Build auth header if API key is set
CURL_AUTH_ARGS=()
if [ -n "${BRAIN_API_KEY:-}" ]; then
    CURL_AUTH_ARGS=(-H "X-Brain-Key: ${BRAIN_API_KEY}")
fi

# ── Container health check ────────────────────────────────────────────────────

if ! curl -sf "${CURL_AUTH_ARGS[@]}" "${BRAIN_URL}/health" > /dev/null 2>&1; then
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

# ── Version check ─────────────────────────────────────────────────────────────
# Compare repo VERSION file against running container. Warns if git pull happened
# but docker compose up -d --build has not been run yet.

if [ -n "$MEMORYBRAIN_DIR" ] && [ -f "${MEMORYBRAIN_DIR}/VERSION" ]; then
    REPO_VERSION=$(tr -d '[:space:]' < "${MEMORYBRAIN_DIR}/VERSION")
    RUNNING_VERSION=$(curl -sf "${CURL_AUTH_ARGS[@]}" "${BRAIN_URL}/status" \
        | python3 -c "import sys,json; print(json.load(sys.stdin).get('version','unknown'))" 2>/dev/null \
        || echo "unknown")
    if [ -n "$REPO_VERSION" ] && [ "$RUNNING_VERSION" != "unknown" ] && [ "$REPO_VERSION" != "$RUNNING_VERSION" ]; then
        echo ""
        echo "## MemoryBrain — UPDATE AVAILABLE"
        echo ""
        echo "  Running: v${RUNNING_VERSION}   Repo: v${REPO_VERSION}"
        echo ""
        echo "  Rebuild the container:"
        echo "    cd \"${MEMORYBRAIN_DIR}\" && docker compose up -d --build"
        echo ""
    fi
fi

# ── Subsystem readiness check ────────────────────────────────────────────────
# Checks SQLite, ChromaDB, Ollama, and both models. Always public — no auth needed.
# On full success: silent (no noise on a healthy system).
# On degraded: explains exactly what's broken, what still works, and how to fix it.

READINESS_MSG=$(curl -sf "${BRAIN_URL}/readiness" | python3 -c "
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
chroma_ok = checks.get('chromadb') == 'ok'

if not ollama_ok:
    lines.append('  Available:    read + keyword search (no Ollama needed)')
    lines.append('  Unavailable:  add_memory, semantic search')
    lines.append('')
    lines.append('  Fix Ollama:')
elif not chroma_ok:
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

# ── Startup summary ───────────────────────────────────────────────────────────

SUMMARY=$(curl -sf "${CURL_AUTH_ARGS[@]}" "${BRAIN_URL}/startup-summary" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['summary'])" 2>/dev/null \
    || echo "")

if [ -n "$SUMMARY" ]; then
    echo "$SUMMARY"
fi

# ── Next-session plan ─────────────────────────────────────────────────────────
# Always fetched — if no .brainproject in CWD, falls back to most recently active project.

NEXT_NOTES_URL="${BRAIN_URL}/next-session"
if [ -n "$PROJECT_SLUG" ]; then
    NEXT_NOTES_URL="${BRAIN_URL}/next-session?project=${PROJECT_SLUG}"
fi

NEXT_NOTES=$(curl -sf "${CURL_AUTH_ARGS[@]}" "${NEXT_NOTES_URL}" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('notes',''))" 2>/dev/null \
    || echo "")

if [ -n "$NEXT_NOTES" ]; then
    echo ""
    echo "## Your Note from Last Session"
    echo ""
    echo "At the end of your last session you left this note for yourself:"
    echo ""
    echo "$NEXT_NOTES"
fi

# ── Available MCP tools ───────────────────────────────────────────────────────
# Read ~/.claude.json directly on the host — never routed through Docker
# (the file contains credentials and must never be mounted into a container)

MCP_TOOLS=$(python3 -c "
import json, os
path = os.path.expanduser('~/.claude.json')
try:
    with open(path) as f:
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
