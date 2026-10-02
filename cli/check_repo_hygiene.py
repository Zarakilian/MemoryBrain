#!/usr/bin/env python3
"""Repo hygiene guard: keep machine data, secrets and brain data out of git.

This repo holds only the MemoryBrain application. The check fails when a
tracked or staged file contains:

  - a brain database, backup tarball, git bundle, or a .env file
  - something shaped like a secret: tokens, API keys, private keys
  - a real user-profile path, an email address, or a private IP address
  - a UUID inside a document (memory ids belong in the brain, not in docs)
  - any pattern from your private list: machine names, company domains,
    project names. Keep that list in .local/hygiene-patterns.txt, which is
    git-ignored, one regular expression per line. Lines starting with # are
    comments. MEMORYBRAIN_HYGIENE_PATTERNS or --patterns can point elsewhere.

A line containing "hygiene: allow" is skipped.

Usage:
  python cli/check_repo_hygiene.py            every tracked file, as staged
  python cli/check_repo_hygiene.py --staged   only files added or changed in the index
  python cli/check_repo_hygiene.py --push     read git's pre-push lines on stdin and scan
                                              every commit being pushed: each file it adds
                                              or changes, and its message
Exit status: 0 clean, 1 findings, 2 git or pattern-file error.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator, Sequence

ALLOW_MARKER = "hygiene: allow"

# Content is not scanned for these (path rules still apply).
SKIP_PREFIXES = ("brain/app/static/vendor/",)
SKIP_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".ico", ".webp", ".woff", ".woff2", ".ttf")

FORBIDDEN_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".db-journal",
                      ".tar.gz", ".tgz", ".bundle")
FORBIDDEN_NAMES = (".env",)
FORBIDDEN_DIRS = ("chroma",)      # a directory with this name, at any depth
FORBIDDEN_TOP_DIRS = ("data",)    # the brain's runtime data folder

DOC_SUFFIXES = (".md", ".html", ".txt")

ALLOWED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", "anthropic.com",
                         "users.noreply.github.com")
ALLOWED_EMAILS = ("git@github.com",)

_PLACEHOLDER_USER = (r"(?:you|user|me|username|runner|public|default|shared"
                     r"|<[^>]*>|%username%|\$user)(?![\w.-])")


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern
    only_suffixes: tuple = ()
    skip_prefixes: tuple = ()


RULES: tuple = (
    Rule("secret:github-token",
         re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
    Rule("secret:api-key", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}")),
    Rule("secret:aws-key-id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    Rule("secret:slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    Rule("secret:private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    Rule("secret:bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-_.=]{20,}")),
    Rule("secret:hex-token", re.compile(r"\b[a-f0-9]{64}\b")),
    Rule("secret:assignment",
         re.compile(r"""(?i)\b[A-Za-z0-9_]*(?:password|passwd|pwd|secret|api[_-]?key|token)[A-Za-z0-9_]*"""
                    r"""\s*[:=]\s*["'][A-Za-z0-9][^"'\s]{7,}["']"""),
         skip_prefixes=("brain/tests/",)),
    Rule("path:user-profile",
         re.compile(r"(?i)\b[a-z]:\\+users\\+(?!" + _PLACEHOLDER_USER + r")[a-z0-9._-]+"
                    r"|(?<![\w.])/home/(?!" + _PLACEHOLDER_USER + r")[a-z0-9._-]+"
                    r"|(?<![\w.])/users/(?!" + _PLACEHOLDER_USER + r")[a-z0-9._-]+")),
    Rule("email",
         re.compile(r"(?<![\w.+%-])[A-Za-z0-9._%+-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})\b")),
    Rule("ip:private",
         re.compile(r"(?<![\d.])(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}"
                    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?!\d)")),
    Rule("uuid-in-docs",
         re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),
         only_suffixes=DOC_SUFFIXES),
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    excerpt: str


def load_private_patterns(path: Path | None) -> list:
    """Compile one regex per non-empty, non-comment line. Missing file = none."""
    if path is None or not Path(path).is_file():
        return []
    patterns = []
    for n, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            patterns.append(re.compile(line))
        except re.error as exc:
            raise ValueError(f"{path}:{n}: bad pattern: {exc}") from exc
    return patterns


def _mask(value: str) -> str:
    value = value.strip()
    head = value[:2] if len(value) > 4 else ""
    return f"{head}***({len(value)})"


def _excerpt(line: str, match: re.Match) -> str:
    masked = line[:match.start()] + _mask(match.group(0)) + line[match.end():]
    return masked.strip()[:160]


def _email_allowed(match: re.Match) -> bool:
    address = match.group(0).lower()
    domain = match.group(1).lower()
    if address in ALLOWED_EMAILS:
        return True
    return any(domain == d or domain.endswith("." + d) for d in ALLOWED_EMAIL_DOMAINS)


def _suffix(path: str) -> str:
    return PurePosixPath(path.lower()).suffix


def _content_skipped(path: str) -> bool:
    low = path.lower()
    return path.startswith(SKIP_PREFIXES) or low.endswith(SKIP_SUFFIXES)


def scan_path_name(path: str) -> list:
    """Flag file types and locations that only brain data or secrets use."""
    low = path.lower()
    parts = PurePosixPath(low).parts
    dirs = parts[:-1]
    bad = (
        low.endswith(FORBIDDEN_SUFFIXES)
        or (parts and parts[-1] in FORBIDDEN_NAMES)
        or any(d in FORBIDDEN_DIRS for d in dirs)
        or (bool(dirs) and dirs[0] in FORBIDDEN_TOP_DIRS)
    )
    return [Finding(path, 0, "forbidden-file", path)] if bad else []


def scan_text(path: str, text: str, private: Sequence = ()) -> list:
    """Scan one file's text. Returns findings with the matched value masked."""
    if _content_skipped(path) or "\x00" in text:
        return []
    suffix = _suffix(path)
    findings = []
    for n, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARKER in line:
            continue
        for rule in RULES:
            if rule.only_suffixes and suffix not in rule.only_suffixes:
                continue
            if rule.skip_prefixes and path.startswith(rule.skip_prefixes):
                continue
            for m in rule.pattern.finditer(line):
                if rule.name == "email" and _email_allowed(m):
                    continue
                findings.append(Finding(path, n, rule.name, _excerpt(line, m)))
        for i, pattern in enumerate(private, 1):
            for m in pattern.finditer(line):
                findings.append(Finding(path, n, f"private:{i}", _excerpt(line, m)))
    return findings


def scan_files(items: Iterable, private: Sequence = ()) -> list:
    """Scan (path, text) pairs: the path rules, then the content rules."""
    findings = []
    for path, text in items:
        findings.extend(scan_path_name(path))
        findings.extend(scan_text(path, text, private))
    return findings


def scan_commit_message(sha: str, message: str, private: Sequence = ()) -> list:
    """Commit messages are published too, so they get the same content rules."""
    return scan_text(f"commit {sha[:7]} message", message, private)


_ZERO_SHA = re.compile(r"^0+$")


def push_rev_args(stdin_text: str) -> list:
    """Turn git's pre-push lines into rev-list arguments, one list per pushed ref.

    Each line is "<local ref> <local sha> <remote ref> <remote sha>". A zero
    local sha is a delete (nothing to scan). A zero remote sha is a new ref on
    the remote, so everything reachable from the local sha is scanned.
    """
    groups = []
    for line in stdin_text.splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        _local_ref, local_sha, _remote_ref, remote_sha = parts
        if _ZERO_SHA.match(local_sha):
            continue
        if _ZERO_SHA.match(remote_sha):
            groups.append([local_sha])
        else:
            groups.append([f"{remote_sha}..{local_sha}"])
    return groups


def format_report(findings: Sequence, scanned: int | None = None,
                  private_count: int | None = None) -> str:
    if not findings:
        extra = []
        if scanned is not None:
            extra.append(f"{scanned} files")
        if private_count is not None:
            extra.append(f"{private_count} private patterns")
        return "hygiene: clean" + (f" ({', '.join(extra)})" if extra else "")
    lines = []
    for f in findings:
        where = f"{f.path}:{f.line}" if f.line else f.path
        lines.append(f"{where}: {f.rule}: {f.excerpt}")
    n = len(findings)
    lines.append(f"hygiene: {n} finding{'s' if n != 1 else ''}. Remove them, or mark a "
                 f"deliberate example with '{ALLOW_MARKER}'.")
    return "\n".join(lines)


def emit(text: str, stream=None) -> None:
    """Print the report even on a console that cannot encode every character
    (a Windows code page, say). Unencodable characters become '?'."""
    stream = stream or sys.stdout
    try:
        stream.write(text + "\n")
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "ascii"
        safe = (text + "\n").encode(encoding, "replace").decode(encoding)
        stream.write(safe)


# ------------------------------------------------------------------ git glue

def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True).stdout


def iter_index_files(repo: Path, staged_only: bool = False) -> Iterator:
    """Yield (path, text) for files as they sit in the index."""
    if staged_only:
        raw = _git(repo, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    else:
        raw = _git(repo, "ls-files", "-z")
    for name in raw.split(b"\0"):
        if not name:
            continue
        path = name.decode("utf-8", "surrogateescape")
        if _content_skipped(path):
            yield path, ""
            continue
        try:
            blob = _git(repo, "show", f":{path}")
        except subprocess.CalledProcessError:
            continue
        yield path, blob.decode("utf-8", "replace")


def collect_push_items(repo: Path, rev_groups: Sequence) -> tuple:
    """Every blob a pushed commit adds or changes (each blob once), plus messages.

    Returns (items, messages): items are (label, path, text) and messages are
    (sha, text). The label names the first commit that introduced the blob.
    """
    commits: list = []
    seen_commits: set = set()
    for args in rev_groups:
        for sha in _git(repo, "rev-list", *args).decode().split():
            if sha not in seen_commits:
                seen_commits.add(sha)
                commits.append(sha)

    blobs: dict = {}      # blob sha -> (commit, path)
    messages = []
    for sha in commits:
        messages.append((sha, _git(repo, "log", "-1", "--format=%B", sha)
                         .decode("utf-8", "replace")))
        raw = _git(repo, "diff-tree", "-r", "--root", "--no-commit-id",
                   "--diff-filter=AM", "-z", sha).split(b"\0")
        # raw -z format: ":<modes> <old> <new> <status>" then the path, repeated
        for meta, name in zip(raw[0::2], raw[1::2]):
            fields = meta.decode().split()
            if len(fields) < 5:
                continue
            blob = fields[3]
            path = name.decode("utf-8", "surrogateescape")
            blobs.setdefault(blob, (sha, path))

    items = []
    proc = subprocess.Popen(["git", "-C", str(repo), "cat-file", "--batch"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        for blob, (sha, path) in blobs.items():
            label = f"{path} @ {sha[:7]}"
            if _content_skipped(path):
                items.append((label, path, ""))
                continue
            proc.stdin.write(blob.encode() + b"\n")
            proc.stdin.flush()
            header = proc.stdout.readline().split()
            if len(header) != 3:
                continue
            data = proc.stdout.read(int(header[2]))
            proc.stdout.read(1)
            items.append((label, path, data.decode("utf-8", "replace")))
    finally:
        proc.stdin.close()
        proc.wait()
    return items, messages


def scan_push(repo: Path, stdin_text: str, private: Sequence = ()) -> tuple:
    """Scan everything a push would publish. Returns (findings, blob_count)."""
    items, messages = collect_push_items(repo, push_rev_args(stdin_text))
    findings = []
    for label, path, text in items:
        for f in scan_path_name(path) + scan_text(path, text, private):
            findings.append(Finding(label, f.line, f.rule, f.excerpt))
    for sha, message in messages:
        findings.extend(scan_commit_message(sha, message, private))
    return findings, len(items)


def private_patterns_note(pfile: Path, private: Sequence):
    """A warning when no private pattern loaded: the generic rules still ran,
    but machine names, domains and project names were never checked."""
    if private:
        return None
    return (f"hygiene: WARNING no private patterns loaded (looked for {pfile}). Machine "
            "names and work details were not checked. Copy your hygiene-patterns.txt "
            "into .local/ or set MEMORYBRAIN_HYGIENE_PATTERNS.")


def main(argv: Sequence | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--staged", action="store_true",
                        help="scan only files added or changed in the index")
    parser.add_argument("--push", action="store_true",
                        help="scan the commits described by git's pre-push lines on stdin")
    parser.add_argument("--patterns", type=Path, help="private patterns file")
    parser.add_argument("--repo", type=Path, help="repo root (default: current repo)")
    args = parser.parse_args(argv)

    try:
        repo = args.repo or Path(_git(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip())
        env_patterns = os.environ.get("MEMORYBRAIN_HYGIENE_PATTERNS")
        pfile = args.patterns or (Path(env_patterns) if env_patterns
                                  else repo / ".local" / "hygiene-patterns.txt")
        private = load_private_patterns(pfile)
        if args.push:
            findings, scanned = scan_push(repo, sys.stdin.read(), private)
        else:
            items = list(iter_index_files(repo, staged_only=args.staged))
            findings, scanned = scan_files(items, private), len(items)
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError) as exc:
        print(f"hygiene: {exc}", file=sys.stderr)
        return 2

    emit(format_report(findings, scanned=scanned, private_count=len(private)))
    note = private_patterns_note(pfile, private)
    if note:
        print(note, file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
