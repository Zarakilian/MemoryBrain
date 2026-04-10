---
name: handover
description: Create a comprehensive manual session handover with full conversation context
version: 3.0.0
disable-model-invocation: false
---

# Manual Session Handover

Create a comprehensive handover document capturing the full context of this conversation.

## Your Task

Generate a detailed handover summary of our current session by analyzing the full conversation history you have in context.

### Handover Structure

Create a markdown document with these sections:

## Quick Summary
(2-3 paragraphs summarizing what we accomplished this session)

## Project Context
- What is this project about?
- What was the initial state when we started?
- What are the main goals?

## Work Completed
Detailed list organized by category:

### Features Implemented
- Feature 1: description
- Feature 2: description

### Bugs Fixed
- Bug 1: what was wrong, how we fixed it
- Bug 2: what was wrong, how we fixed it

### Files Created/Modified
- `/path/to/file1.ext` - what changed
- `/path/to/file2.ext` - what changed

### Commands Run
- `command 1` - purpose
- `command 2` - purpose

### Configuration Changes
- What settings were modified
- New hooks/skills installed

## Key Decisions & Rationale
Important choices made and why:
- **Decision 1:** Why we chose approach A over B
- **Decision 2:** Technical trade-offs accepted

## Problems Encountered & Solutions
- **Problem 1:**
  - What went wrong
  - Root cause discovered
  - How we fixed it
  - What we learned

## Technical Details

### System Information
- Working directory: (from context)
- Environment: (WSL/Linux/Mac/Windows)
- Tools used: (list relevant tools)

### Architecture Changes
- New components added
- How systems integrate
- Data flow

## Current State
- ✅ What's currently working
- 🔄 What's in progress
- ❌ What's blocked
- 📋 What's pending

## Next Steps
Prioritized recommended actions:
1. First thing to do
2. Second priority
3. Future considerations

## Important Files & Locations
Key files and their purposes:
- `/path/to/important/file` - what it does
- `/path/to/config` - configuration details

## Lessons Learned & Best Practices
- Important discoveries from this session
- Things to watch out for in the future
- Best practices identified

## Open Questions
- Unresolved issues to investigate
- Alternative approaches to consider

---

## Instructions for You

1. **Review the full conversation** - You have complete context in memory
2. **Be comprehensive** - Include all relevant details, file paths, commands
3. **Be specific** - Don't just say "fixed the issue", explain what and how
4. **Include context** - Future Claude should understand everything from reading this

After generating the handover, save it to MemoryBrain using the `add_memory` MCP tool:

```
add_memory(
  content=<your full handover document>,
  type="session",
  project=<current project slug from .brainproject or CWD>,
  tags=["handover", "session-log"],
  source="handover"
)
```

If MemoryBrain is not running, save to a file instead:

```bash
bash ~/.claude/hooks/save-handover.sh {{cwd}} << 'HANDOVER_END'
[Your complete handover content here]
HANDOVER_END
```

**Generate the handover content now, then save it using one of the methods above.**
