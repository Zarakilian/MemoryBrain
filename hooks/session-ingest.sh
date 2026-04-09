#!/usr/bin/env bash
# MemoryBrain session-start hook
# Injects a compact project summary into session context on startup.
# Called by Claude Code session-start hook. CWD = project directory.

set -euo pipefail

BRAIN_URL="${MEMORYBRAIN_URL:-http://localhost:7741}"
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

# Check if brain is running
if ! curl -sf "${CURL_AUTH_ARGS[@]}" "${BRAIN_URL}/health" > /dev/null 2>&1; then
    # Brain not running — fall back to legacy MEMORY.md if present
    if [ -f "${CWD}/memory/MEMORY.md" ]; then
        echo "# Context (from MEMORY.md — MemoryBrain not running)"
        head -100 "${CWD}/memory/MEMORY.md"
    fi
    exit 0
fi

# Fetch startup summary
SUMMARY=$(curl -sf "${CURL_AUTH_ARGS[@]}" "${BRAIN_URL}/startup-summary" | python3 -c "import sys,json; print(json.load(sys.stdin)['summary'])" 2>/dev/null || echo "")

if [ -n "$SUMMARY" ]; then
    echo "$SUMMARY"
fi

# Inject next-session plan if a project is detected
if [ -n "$PROJECT_SLUG" ]; then
    NEXT_NOTES=$(curl -sf "${CURL_AUTH_ARGS[@]}" "${BRAIN_URL}/next-session?project=${PROJECT_SLUG}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('notes',''))" 2>/dev/null || echo "")
    if [ -n "$NEXT_NOTES" ]; then
        echo ""
        echo "## Next Session Plan — ${PROJECT_SLUG}"
        echo "$NEXT_NOTES"
    fi
fi

# Inject available MCP tools (public endpoint — no auth header needed)
MCP_TOOLS=$(curl -sf "${BRAIN_URL}/mcp-tools" | python3 -c "
import sys, json
data = json.load(sys.stdin)
tools = data.get('tools', [])
if tools:
    print('## Available MCP Tools')
    for t in tools:
        print(f'- {t}')
    print()
    print('MemoryBrain will store what you retrieve with these tools.')
" 2>/dev/null || echo "")

if [ -n "$MCP_TOOLS" ]; then
    echo ""
    echo "$MCP_TOOLS"
fi
