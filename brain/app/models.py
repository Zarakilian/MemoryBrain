import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import uuid


def utcnow() -> datetime:
    """Timezone-aware UTC now, without deprecation warning."""
    return datetime.now(timezone.utc)

VALID_TYPES = {"note", "fact", "session", "handover", "file"}
MAX_CONTENT_LENGTH = 100_000
MAX_TAGS = 20
MAX_TAG_LENGTH = 100
PROJECT_SLUG_RE = re.compile(r"^[a-z0-9_-]{1,64}$")


class ValidationError(ValueError):
    """Raised when a MemoryEntry fails validation."""
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
    importance: int = 3
    timestamp: datetime = field(default_factory=utcnow)


@dataclass
class Project:
    slug: str
    name: str
    last_activity: datetime = field(default_factory=utcnow)
    one_liner: str = ""


def validate_entry(entry: MemoryEntry) -> None:
    """Validate and clamp fields on a MemoryEntry. Raises ValidationError on bad input."""
    # M5: content length
    if not entry.content or not entry.content.strip():
        raise ValidationError("content must not be empty")
    if len(entry.content) > MAX_CONTENT_LENGTH:
        raise ValidationError(f"content exceeds {MAX_CONTENT_LENGTH} character limit")

    # M1: type enum
    if entry.type not in VALID_TYPES:
        raise ValidationError(f"type must be one of: {', '.join(sorted(VALID_TYPES))}")

    # M3: project slug
    if not PROJECT_SLUG_RE.match(entry.project):
        raise ValidationError("project must match ^[a-z0-9_-]{1,64}$")

    # M2: importance clamp (silently clamp rather than reject)
    entry.importance = max(1, min(5, entry.importance))

    # M4: tags bounds
    if len(entry.tags) > MAX_TAGS:
        raise ValidationError(f"too many tags (max {MAX_TAGS})")
    for tag in entry.tags:
        if len(tag) > MAX_TAG_LENGTH:
            raise ValidationError(f"tag exceeds {MAX_TAG_LENGTH} character limit")
