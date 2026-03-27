#!/usr/bin/env bash
# MemoryBrain session-start hook
# Injects a compact project summary into session context on startup.
# Called by Claude Code session-start hook. CWD = project directory.

set -euo pipefail

BRAIN_URL="${MEMORYBRAIN_URL:-http://localhost:7741}"
CWD="${1:-$(pwd)}"

# Detect project slug: check for .brainproject file first, then heuristic
PROJECT_SLUG=""
if [ -f "${CWD}/.brainproject" ]; then
    PROJECT_SLUG=$(cat "${CWD}/.brainproject" | tr -d '[:space:]')
fi

# Check if brain is running
if ! curl -sf "${BRAIN_URL}/health" > /dev/null 2>&1; then
    # Brain not running — fall back to legacy MEMORY.md if present
    if [ -f "${CWD}/memory/MEMORY.md" ]; then
        echo "# Context (from MEMORY.md — MemoryBrain not running)"
        head -100 "${CWD}/memory/MEMORY.md"
    fi
    exit 0
fi

# Fetch startup summary
SUMMARY=$(curl -sf "${BRAIN_URL}/startup-summary" | python3 -c "import sys,json; print(json.load(sys.stdin)['summary'])" 2>/dev/null || echo "")

if [ -n "$SUMMARY" ]; then
    echo "$SUMMARY"
fi
