import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import uuid


def utcnow() -> datetime:
    return datetime.now(timezone.utc)

VALID_TYPES = {
    "note", "fact", "session", "handover", "file", "reference",
    "belief",      # synthesised by the consolidation cycle
    "decision",    # durable choice / policy (v2.2 write policy)
    "open_loop",   # unfinished actionable item (v2.2)
    "procedure",   # learned rule for how the user wants things done (v3)
}
VALID_TRUST = {"user", "agent", "derived", "imported"}
VALID_STATUSES = {"active", "archived", "proposed", "done"}
MAX_CONTENT_LENGTH = 100_000
MAX_TAGS = 20
MAX_TAG_LENGTH = 100
PROJECT_SLUG_RE = re.compile(r"^[a-z0-9_-]{1,64}$")


class ValidationError(ValueError):
    pass


@dataclass
class MemoryEntry:
    content: str
    type: str
    project: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    summary: str = ""
    tags: list = field(default_factory=list)
    source: str = ""
    importance: Optional[int] = None   # None: score it at ingest (write_policy, ingest)
    timestamp: datetime = field(default_factory=utcnow)
    # Lifecycle fields (persisted)
    status: str = "active"
    superseded_by: Optional[str] = None
    supersedes: Optional[str] = None
    # Provenance and validity (persisted, v3)
    writer: str = ""               # who wrote it, e.g. "claude@WORK-PC"
    trust: str = "agent"           # one of VALID_TRUST
    embedded: bool = True          # False when the vector is missing and must be re-embedded
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    invalidated_by: Optional[str] = None
    # Who wrote the summary (persisted, v3.3): "writer" (the caller gave it),
    # "verbatim" (a short body is its own summary), "extract" (the opening of
    # the text), "model" (the summary model), "" (unknown: stored before 3.3)
    summary_source: str = ""
    verified_at: Optional[str] = None    # last time someone checked it and found it true
    corrected_by: Optional[str] = None   # an active memory that says part of this is wrong
    # Transient fields (returned from ingest, never stored)
    superseded: list = field(default_factory=list)
    closed: list = field(default_factory=list)       # open loops this write finished
    corrected: list = field(default_factory=list)    # memories this write disputes
    potential_supersessions: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    chunks: int = 0
    duplicate: bool = False


@dataclass
class Project:
    slug: str
    name: str
    last_activity: datetime = field(default_factory=utcnow)
    one_liner: str = ""
    description: str = ""          # v2.5 workspace layer
    description_source: str = ""   # 'user' | 'tool' | 'auto' | ''


def validate_entry(entry: MemoryEntry) -> None:
    if not entry.content or not entry.content.strip():
        raise ValidationError("content must not be empty")
    if len(entry.content) > MAX_CONTENT_LENGTH:
        raise ValidationError(f"content exceeds {MAX_CONTENT_LENGTH} character limit")
    if entry.type not in VALID_TYPES:
        raise ValidationError(f"type must be one of: {', '.join(sorted(VALID_TYPES))}")
    if entry.trust not in VALID_TRUST:
        raise ValidationError(f"trust must be one of: {', '.join(sorted(VALID_TRUST))}")
    if entry.status not in VALID_STATUSES:
        raise ValidationError(f"status must be one of: {', '.join(sorted(VALID_STATUSES))}")
    if not PROJECT_SLUG_RE.match(entry.project):
        raise ValidationError("project must match ^[a-z0-9_-]{1,64}$")
    if entry.importance is not None:
        entry.importance = max(1, min(5, int(entry.importance)))
    if len(entry.tags) > MAX_TAGS:
        raise ValidationError(f"too many tags (max {MAX_TAGS})")
    for tag in entry.tags:
        if len(tag) > MAX_TAG_LENGTH:
            raise ValidationError(f"tag exceeds {MAX_TAG_LENGTH} character limit")
