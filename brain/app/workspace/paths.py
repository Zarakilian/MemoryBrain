"""Pure path helpers for the workspace layer. No database, no filesystem."""
from __future__ import annotations

import re

KNOWN_EXTS: tuple[str, ...] = (
    "md", "sql", "html", "htm", "xhtml", "ps1", "py", "sh", "json", "yaml", "yml",
    "csv", "xlsx", "docx", "pptx", "txt", "xml", "png", "svg", "mmd",
)
_EXT = "(?:" + "|".join(KNOWN_EXTS) + ")"
# Every piece is bounded by the NTFS limit of 255 characters per name.
# Unbounded, a run of short slash tokens ("a/b c/d ...") let each start scan
# to the end of the text: 100,000 characters took most of a minute.
_SEG = r"[\w.()&+\-]{1,255}"        # first segment: no spaces
_SEG_SP = r"[\w .()&+\-]{1,255}"    # later segments: spaces allowed ("Daily Reports")
_NAME = r"[\w()&+\-][\w .()&+\-]{0,254}?"
_MAX_SEGMENTS = 24

# A scheme is short; an unbounded one made a long dotted run ("a.a.a...")
# backtrack quadratically on every ingest.
_URL_RE = re.compile(r"[a-z][a-z0-9+.\-]{0,30}://\S+", re.I)
# prefix kept whole when present: drive letter, tilde, a single leading slash, or a UNC
# double backslash. A single lone backslash is not a listed case on its own (it shows up
# mid string after things like %USERPROFILE%) so it is left out on purpose.
_PREFIX = r"(?:[A-Za-z]:[\\/]|~[\\/]|\\\\|/)?"
_PATH_RE = re.compile(
    r"(?<![\w\\/.])"
    r"(" + _PREFIX + _SEG + r"(?:[\\/]" + _SEG_SP + r"){0," + str(_MAX_SEGMENTS) + r"}?[\\/]"
    + _NAME + r"\." + _EXT + r")"
    r"(?![\w])",
    re.I,
)
_BARE_RE = re.compile(r"(?<![\w\\/.\-])([A-Za-z][\w()&+\-]*\." + _EXT + r")(?![\w])", re.I)
_RUNAWAY_RE = re.compile(r"\." + _EXT + r"(?=\s)", re.I)

_WRAPPERS = {"(": ")", "[": "]", "{": "}", "<": ">"}


def _strip_wrapping(tok: str) -> str:
    """Drop a leading bracket only when its partner is absent from the token,
    so '(docs/foo.md' becomes 'docs/foo.md' but '(archive)/report.md' is kept."""
    while tok and tok[0] in _WRAPPERS and _WRAPPERS[tok[0]] not in tok[1:]:
        tok = tok[1:]
    return tok.rstrip(".,;:")


def _split_runaway(token: str) -> list[str]:
    """'Knowledge\\a.md and Other\\b.md' matched as one token: cut at the first
    extension followed by whitespace and re-scan the remainder."""
    m = _RUNAWAY_RE.search(token)
    if not m:
        return [token]
    head = token[: m.end()]
    rest = token[m.end():]
    return [head] + extract_path_tokens(rest)


def extract_path_tokens(text: str) -> list[str]:
    """Path shaped tokens first, then bare filenames not already inside one."""
    if not text:
        return []
    cleaned = _URL_RE.sub(" ", text).replace("`", "\n")
    found: list[str] = []
    seen: set[str] = set()
    pieces, last = [], 0                  # the text with every path blanked, built once
    for m in _PATH_RE.finditer(cleaned):
        for tok in _split_runaway(m.group(1)):
            tok = _strip_wrapping(tok)
            if tok not in seen:
                seen.add(tok)
                found.append(tok)
        pieces += [cleaned[last:m.start()], " " * (m.end() - m.start())]
        last = m.end()
    pieces.append(cleaned[last:])
    for m in _BARE_RE.finditer("".join(pieces)):
        tok = m.group(1)
        if tok not in seen:
            seen.add(tok)
            found.append(tok)
    return found


def is_path_shaped(token: str) -> bool:
    return "/" in token or "\\" in token


def ci(s: str) -> str:
    return s.replace("\\", "/").lower()


def split_ext(rel_path: str) -> str:
    name = rel_path.replace("\\", "/").rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()


def normalise_rel(token: str, roots_abs: list[str]) -> str:
    """Forward slashes, known root prefix removed, leading './' removed."""
    t = token.replace("\\", "/").strip()
    while t.startswith("./"):
        t = t[2:]
    low = t.lower()
    for root in roots_abs or []:
        r = root.replace("\\", "/").rstrip("/").lower()
        if r and low.startswith(r + "/"):
            return t[len(r) + 1:]
    return t
