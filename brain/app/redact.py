"""Secret redaction for text on its way into the brain.

redact() swaps secret-shaped values for a marker such as [REDACTED:github-token]
and reports which rules fired. It is pure and dependency-free, so every write
path can call it.

How a pass works: markers already in the text are masked (their words, such
as "token" in [REDACTED:github-token], never count) and claimed. Rules run in
table order over the masked text. A match that overlaps a claimed span is
skipped, unless its rule absorbs (private-key, url-credentials) and it fully
contains the earlier hits it overlaps: then the wider match wins, so a token
inside a private-key block can never save the rest of the block. Passes repeat until one fires nothing, which makes
redact() idempotent by construction.
"""
from __future__ import annotations

import re
from typing import Callable, NamedTuple, Optional, Union

REDACTION_FORMAT = "[REDACTED:{rule}]"
MAX_PASSES = 4

_MARKER = re.compile(r"\[REDACTED:[a-z-]+\]")
# Before a distinctive prefix: start, a non-alphanumeric, or a JSON escape (\n \t \r).
_START = r"(?:(?<![A-Za-z0-9])|(?<=\\[nrt]))"

# Whole values that are references or code, not literal secrets.
_WHOLE_REFERENCE = re.compile(
    r"\$\{[^}]*\}|\$[A-Za-z_]\w*|\$\(.*\)|%[A-Za-z_]\w*%|<[^<>\s]+>|\{\{[^}]*\}\}"
    r"|[A-Za-z_][\w.]*\(.*\)|[A-Za-z_][\w.]*\[.*\]"
    r"|(?:os|settings|config|self|env|process\.env|app\.config)\.[\w.]+"
    r"|/[\w./-]*|[A-Za-z]:\\[\w.\\-]*"
)


class _Rule(NamedTuple):
    name: str
    pattern: re.Pattern[str]
    group: Union[str, int, tuple] = 0  # the part replaced; a tuple = first that matched
    accept: Optional[Callable[[re.Match], bool]] = None
    absorbs: bool = False  # may replace earlier hits it fully contains


def _group_of(match: re.Match, group) -> Optional[str]:
    if isinstance(group, tuple):
        return next((g for g in group if match.group(g) is not None), None)
    return group


def _env_value_is_literal(match: re.Match) -> bool:
    value = match.group(_group_of(match, ("dq", "sq", "bare")))
    if _WHOLE_REFERENCE.fullmatch(value.strip()):
        return False
    # os.environ["X"] or get("X"): a bare value cut at a quote right after [ or (
    if match.group("bare") is not None and value[-1:] in "[(":
        if match.string[match.end():match.end() + 1] in ("'", '"'):
            return False
    return True


_RULES: tuple[_Rule, ...] = (
    _Rule("github-token", re.compile(
        _START + r"(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})")),
    _Rule("api-key", re.compile(_START + r"sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}")),
    _Rule("aws-key-id", re.compile(_START + r"(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Za-z])")),
    _Rule("slack-token", re.compile(_START + r"xox[abprs]-[A-Za-z0-9-]{10,}")),
    _Rule("private-key", re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----"
        r".*?(?:-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|\Z)", re.S), absorbs=True),
    # RFC 6750 b64token characters; only the value goes, "Bearer" stays, and a
    # sentence's full stop is not part of the token.
    _Rule("bearer", re.compile(
        r"(?i)\bbearer[ \t]+(?P<v>[A-Za-z0-9._~+/=-]{19,}[A-Za-z0-9_~+/=-])"), "v"),
    _Rule("jwt", re.compile(
        _START + r"eyJ[A-Za-z0-9_-]{7,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    # user may be empty (redis://:pass@host); a password may hold a raw "@".
    _Rule("url-credentials", re.compile(
        r"\b[A-Za-z][A-Za-z0-9+.-]{0,30}://(?P<v>[^\s:/?#@\[\]]*:[^\s/?#\[\]]+@)"), "v",
        absorbs=True),
    # Connection strings: Password=x; / Pwd=x. Quoted values are env-secret's;
    # paths, $(...) and --flag=value are not passwords.
    _Rule("connection-password", re.compile(
        r"(?i)(?<![A-Za-z0-9_-])(?:password|pwd)=(?P<v>(?![\"'/$])[^;\s]{4,})"), "v"),
    # NAME=value or NAME: value, JSON keys included. A quoted value may hold
    # spaces and the other quote; quotes stay, the value goes.
    _Rule("env-secret", re.compile(
        r"(?i)(?:token|secret|passw(?:or)?d|(?<![a-z])pwd|api[_-]?key|access[_-]?key)"
        r"[A-Za-z0-9_.-]{0,40}[\"']?[ \t]*[:=][ \t]*"
        r"(?:\"(?P<dq>[^\"\r\n]{8,})\"?|'(?P<sq>[^'\r\n]{8,})'?|(?P<bare>(?![\"'])[^\s\"']{8,}))"),
        ("dq", "sq", "bare"), _env_value_is_literal),
    _Rule("keyword-hex", re.compile(
        r"(?i)(?<![a-z])(?:tokens?|bearer|secrets?|keys?|passwords?|auth(?:orization)?)(?![a-z])"
        r"[^\n]{0,30}?(?<![0-9a-f])(?P<v>[0-9a-f]{32,})(?![0-9a-f])"), "v"),
)


def _pass(text: str) -> tuple[str, list[str]]:
    """One redaction pass. Returns (new text, rule names in order of appearance)."""
    markers = bytearray(len(text))
    for m in _MARKER.finditer(text):
        markers[m.start():m.end()] = b"\x01" * (m.end() - m.start())
    scan = _MARKER.sub(lambda m: "\x00" * len(m.group()), text)
    taken = bytearray(markers)
    hits: list[tuple[int, int, str]] = []
    for rule in _RULES:
        for match in rule.pattern.finditer(scan):
            group = _group_of(match, rule.group)
            start, end = match.span(group)
            if start >= end or (rule.accept is not None and not rule.accept(match)):
                continue
            if taken.find(1, start, end) != -1:
                inner = [h for h in hits if start < h[1] and h[0] < end]
                wider = (rule.absorbs and inner and markers.find(1, start, end) == -1
                         and all(start <= h[0] and h[1] <= end for h in inner))
                if not wider:
                    continue
                hits = [h for h in hits if h not in inner]
            taken[start:end] = b"\x01" * (end - start)
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
    return "".join(parts), [name for _, _, name in hits]


def redact(text: str) -> tuple[str, list[str]]:
    """Return (text with secrets replaced, rule names that fired).

    Rule names are deduplicated, in order of first appearance. Idempotent:
    redact(redact(t)[0]) returns the same text and no rules."""
    if not text:
        return text, []
    fired: list[str] = []
    for _ in range(MAX_PASSES):
        text, names = _pass(text)
        if not names:
            break
        fired += names
    return text, list(dict.fromkeys(fired))


_URL_USERINFO = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]{0,30}://)[^/?#\s]*@")


def strip_url_userinfo(url: str) -> str:
    """Drop 'user:password@' from a URL: 'https://u:t@host/x' -> 'https://host/x'.
    SSH remotes such as 'git@host:owner/repo.git' have no scheme and are kept."""
    return _URL_USERINFO.sub(r"\1", (url or "").strip())
