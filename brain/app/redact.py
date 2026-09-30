"""Secret redaction for text on its way into the brain.

redact() swaps secret-shaped values for a marker such as [REDACTED:github-token]
and reports which rules fired. It is pure and dependency-free, so every write
path can call it. Rules run in table order. A span that an earlier rule
replaced, or a marker already in the text, is never touched again, which keeps
redact() idempotent.
"""
from __future__ import annotations

import re
from typing import Callable, NamedTuple

REDACTION_FORMAT = "[REDACTED:{rule}]"

_MARKER = re.compile(r"\[REDACTED:[a-z-]+\]")

# A value that reads like code or a placeholder rather than a literal secret:
# ${VAR}, <your-key>, %VAR%, os.environ["X"], get_token(), settings.SECRET_KEY
_CODE_REFERENCE = re.compile(
    r"[$<{%(\[]"
    r"|[A-Za-z_]\w*(?:\.\w+)*[(\[]"
    r"|[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+[;,]?$"
)


class _Rule(NamedTuple):
    name: str
    pattern: re.Pattern[str]
    group: str | int = 0  # the part of the match that gets replaced
    accept: Callable[[str], bool] | None = None


_RULES: tuple[_Rule, ...] = (
    _Rule("github-token", re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})")),
    _Rule("api-key", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}")),
    _Rule("aws-key-id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    _Rule("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    _Rule("private-key", re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----"
        r".*?(?:-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|\Z)", re.S)),
    # RFC 6750 b64token characters; only the value goes, "Bearer" stays.
    _Rule("bearer", re.compile(r"(?i)\bbearer[ \t]+(?P<v>[A-Za-z0-9._~+/=-]{20,})"), "v"),
    _Rule("jwt", re.compile(
        r"\beyJ[A-Za-z0-9_-]{7,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    # Scheme length is capped so a long dotted run cannot backtrack badly.
    _Rule("url-credentials", re.compile(
        r"\b[A-Za-z][A-Za-z0-9+.-]{0,30}://(?P<v>[^\s:/?#@\[\]]+:[^\s/?#@\[\]]+@)"), "v"),
    _Rule("connection-password", re.compile(
        r"(?i)(?<![A-Za-z0-9_])(?:password|pwd)=(?P<v>[^;\s]{4,})"), "v"),
    # NAME=value or NAME: value, JSON keys included. A quoted value may hold
    # spaces; a bare one ends at whitespace. Quotes stay, the value goes.
    _Rule("env-secret", re.compile(
        r"(?i)(?<![A-Za-z0-9_.-])[A-Za-z0-9_.-]{0,40}?"
        r"(?:token|secret|passw(?:or)?d|api[_-]?key|access[_-]?key)[A-Za-z0-9_.-]{0,40}"
        r"[\"']?[ \t]*[:=][ \t]*"
        r"(?P<q>[\"'])?(?P<v>(?(q)[^\"'\r\n]{8,}|[^\s\"']{8,}))"), "v",
        lambda value: not _CODE_REFERENCE.match(value)),
    _Rule("keyword-hex", re.compile(
        r"(?i)(?<![a-z])(?:token|bearer|secret|key|password|auth)"
        r"[^\n]{0,30}?(?<![0-9a-f])(?P<v>[0-9a-f]{32,})(?![0-9a-f])"), "v"),
)


def redact(text: str) -> tuple[str, list[str]]:
    """Return (text with secrets replaced, rule names that fired).

    Rule names are deduplicated, in order of first appearance in the text.
    Idempotent: redact(redact(t)[0]) returns the same text and no rules.
    """
    if not text:
        return text, []
    taken = [m.span() for m in _MARKER.finditer(text)]
    hits: list[tuple[int, int, str]] = []
    for rule in _RULES:
        for match in rule.pattern.finditer(text):
            start, end = match.span(rule.group)
            if any(start < t_end and t_start < end for t_start, t_end in taken):
                continue
            if rule.accept is not None and not rule.accept(match.group(rule.group)):
                continue
            taken.append((start, end))
            hits.append((start, end, rule.name))
    if not hits:
        return text, []
    hits.sort()
    parts: list[str] = []
    pos = 0
    for start, end, name in hits:
        parts += (text[pos:start], REDACTION_FORMAT.format(rule=name))
        pos = end
    parts.append(text[pos:])
    return "".join(parts), list(dict.fromkeys(name for _, _, name in hits))
