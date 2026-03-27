from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import uuid


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
    timestamp: datetime = field(default_factory=datetime.utcnow)
    chroma_id: str = ""


@dataclass
class Project:
    slug: str
    name: str
    last_activity: datetime = field(default_factory=datetime.utcnow)
    one_liner: str = ""
