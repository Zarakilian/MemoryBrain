"""Write-time guardrails for multi-AI memory quality.

Facts and decisions should be short, crisp truths. Long narrative belongs in
session/handover. Open loops should be actionable one-liners.
"""
from __future__ import annotations

from .models import MemoryEntry, ValidationError

# Soft guidance thresholds (characters)
FACT_DECISION_MAX = 4_000
OPEN_LOOP_MAX = 800
NOTE_WARN = 12_000
SESSION_OK = 100_000  # still hard-capped by models.MAX_CONTENT_LENGTH

# Tags auto-applied for structured types
TYPE_AUTO_TAGS = {
    "decision": ["decision"],
    "open_loop": ["open_loop"],
}


def apply_write_policy(entry: MemoryEntry) -> list[str]:
    """Mutate entry in place; return list of soft warnings (never secrets).

    Raises ValidationError for hard violations.
    """
    warnings: list[str] = []
    content_len = len(entry.content or "")

    if entry.type in ("fact", "decision") and content_len > FACT_DECISION_MAX:
        raise ValidationError(
            f"{entry.type} content exceeds {FACT_DECISION_MAX} characters. "
            "Store durable truths as short fact/decision entries; put long "
            "narrative in type=session (or type=note under 12k)."
        )

    if entry.type == "open_loop" and content_len > OPEN_LOOP_MAX:
        raise ValidationError(
            f"open_loop content exceeds {OPEN_LOOP_MAX} characters. "
            "Keep open loops as one actionable line."
        )

    if entry.type == "note" and content_len > NOTE_WARN:
        warnings.append(
            "Large note: consider type=session for narrative dumps so "
            "fact/decision search stays clean."
        )

    # Auto-tags for structured types (deduped, case-sensitive as stored)
    auto = TYPE_AUTO_TAGS.get(entry.type, [])
    if auto:
        existing = list(entry.tags or [])
        lower = {t.lower() for t in existing}
        for tag in auto:
            if tag.lower() not in lower:
                existing.append(tag)
        entry.tags = existing

    # Default importance nudges (only when still at default 3)
    if entry.importance == 3:
        if entry.type == "decision":
            entry.importance = 4
        elif entry.type == "open_loop":
            entry.importance = 3
        elif entry.type == "fact":
            entry.importance = 4

    return warnings
