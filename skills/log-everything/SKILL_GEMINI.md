---
description: Run the full 6-file auto-logging protocol — logs all work done in this session to DEV_LOG, PROJECT_LOG, MEMORY.md, session start prompt, dynamic content plan, and MemoryBrain
---

# LOG EVERYTHING — 6-File Auto-Logging Protocol

You are running the comprehensive logging protocol for the Baby Bee Blossom project. This logs all work from the current session to 6 different files/destinations.

## Instructions

Review everything accomplished in this conversation, then update ALL 6 items below. Do NOT skip any.

### File 1: DEV_LOG.md (APPEND)

**Path**: `Project Management/DEV_LOG.md`
**Action**: APPEND a new entry (never truncate/overwrite)

```markdown
### [YYYY-MM-DD] - Brief Title
**Category**: Bug Fix | Improvement | New Feature | Configuration | Idea
**Status**: Completed | In Progress | Deferred

**Description**:
What was done or discovered.

**Files Modified**:
- Full paths of all files created/modified

**Issues Encountered**:
- Root causes + solutions + prevention

**Architecture Decisions**:
- Any patterns or decisions made

**Build Verification**:
- Build status after changes

**Notes**:
Any additional context.
```

### File 2: PROJECT_LOG.md (APPEND)

**Path**: `Project Management/PROJECT_LOG.md`
**Action**: APPEND a new entry (never truncate/overwrite)

Include:
- Work done summary (bullet points)
- Decisions made
- Pending tasks (updated)
- Files touched count

### File 3: CLAUDE_HANDOVERS/MEMORY.md (APPEND)

**Path**: `CLAUDE_HANDOVERS/MEMORY.md`
**Action**: APPEND a session entry (10-15 lines max)

Include:
- What was done
- Key issues & solutions
- Remaining work

### File 4: Session Start Prompt (UPDATE)

**Path**: `CLAUDE_HANDOVERS/NEXT_SESSION_PROMPT.md`
**Action**: UPDATE in-place

- Summarize what was completed
- Update priorities for next session
- Add any new golden rules or known issues discovered
- Update environment variables or connections if changed

### File 5: Dynamic Content Plan (UPDATE)

**Path**: `Product Blueprint & Technical Specification/DYNAMIC_CONTENT_PLAN.md`
**Action**: UPDATE status markers on completed phases (if any dynamic content phases were worked on)

Skip this file if no dynamic content work was done in this session.

### File 6: MemoryBrain Auto-Memory (UPDATE)

**Action**: Use the `add_memory` MCP tool from MemoryBrain to store any new gotchas, patterns, or project status changes discovered in this session. Be sure to use the correct project slug (`baby-bee-blossom`).

## Quality Standards

- Be SPECIFIC with file paths and line numbers
- Capture the "why" behind decisions
- Include build/test verification results
- Note any new gotchas or patterns discovered
- Use the exact date format: [YYYY-MM-DD]
- For DEV_LOG: one entry per logical unit of work (not one mega-entry)

## Important

- NEVER truncate or overwrite DEV_LOG.md or PROJECT_LOG.md — APPEND ONLY
- Read each file BEFORE editing to verify current content
- If a file doesn't exist, create it with appropriate headers
- The user may also trigger this by saying "wrap up", "log everything", or "/wrapup"
