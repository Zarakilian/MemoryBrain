"""The repo hygiene guard keeps machine data, secrets and brain data out of git.

Every "bad" value below is assembled at runtime, so this file never contains
the literal strings the guard looks for and stays clean under its own scan.
"""
import re
import sys
from pathlib import Path

import pytest

for _cand in (Path(__file__).parent.parent.parent / "cli", Path(__file__).parent.parent / "cli"):
    if (_cand / "check_repo_hygiene.py").exists():
        sys.path.insert(0, str(_cand))
        break

import check_repo_hygiene as hy  # noqa: E402

BS = "\\"


def rules(findings):
    return sorted({f.rule for f in findings})


def scan(path, text, private=()):
    return hy.scan_text(path, text, private)


# ---------------------------------------------------------------- clean input

def test_clean_text_has_no_findings():
    text = f"Run: brain scan --root C:{BS}work{BS}repos --label git\nSee http://localhost:7741/ui\n"
    assert scan("docs/GETTING_STARTED.md", text) == []


# --------------------------------------------------------------------- secrets

def test_github_token_is_flagged_and_masked():
    token = "ghp_" + "A1b2" * 9
    findings = scan("README.md", f"export GH={token}\n")
    assert rules(findings) == ["secret:github-token"]
    assert token not in findings[0].excerpt


def test_openai_style_key_is_flagged():
    assert rules(scan("x.md", "key sk-" + "proj-" + "Z9" * 12 + "\n")) == ["secret:api-key"]


def test_64_hex_token_is_flagged():
    assert rules(scan("PROGRESS.md", "Bearer-less token " + "ab12" * 16 + "\n")) == ["secret:hex-token"]


def test_bearer_header_is_flagged():
    assert rules(scan("x.md", "Authorization: Bearer " + "Qw3" * 8 + "\n")) == ["secret:bearer"]


def test_private_key_block_is_flagged():
    assert rules(scan("x.pem.txt", "-----BEGIN " + "RSA PRIVATE KEY-----\n")) == ["secret:private-key"]


def test_literal_secret_assignment_is_flagged_outside_tests():
    line = "BRAIN_API_KEY = " + '"' + "s3cr3t-" + "v4lue9" + '"' + "\n"
    assert rules(scan("brain/app/settings.py", line)) == ["secret:assignment"]
    assert scan("brain/tests/test_auth.py", line) == []


def test_key_name_inside_a_string_literal_is_not_an_assignment():
    code = ('has_key = "GOOGLE_API_KEY=" in env and not '
            'env.split("GOOGLE_API_KEY=")[1].split("' + BS + 'n")[0].strip() == ""\n')
    assert scan("cli/brain.py", code) == []


# ----------------------------------------------------------- machine identity

def test_real_windows_profile_path_is_flagged():
    text = f"notes in C:{BS}Users{BS}" + "alice" + f"{BS}memorybrain\n"
    assert rules(scan("AGENTS.md", text)) == ["path:user-profile"]


def test_placeholder_windows_profile_path_is_allowed():
    assert scan("AGENTS.md", f"notes in C:{BS}Users{BS}you{BS}memorybrain\n") == []


def test_real_home_path_is_flagged_and_placeholder_allowed():
    assert rules(scan("x.py", "root = '/home/" + "alice" + "/work'\n")) == ["path:user-profile"]
    assert scan("x.py", "root = '/home/you/work'\n") == []


def test_email_is_flagged_but_example_and_noreply_addresses_are_allowed():
    real = "jane.doe" + "@" + "corp-mail.io"
    assert rules(scan("README.md", f"contact {real}\n")) == ["email"]
    assert scan("README.md", "contact dev@example.com\n") == []
    assert scan("README.md", "Co-authored-by: Claude <noreply" + "@" + "anthropic.com>\n") == []
    assert scan("README.md", "git clone git" + "@" + "github.com:owner/repo.git\n") == []


def test_private_ip_is_flagged_but_loopback_and_documentation_ranges_are_allowed():
    assert rules(scan("x.md", "db at 10." + "20.30.40\n")) == ["ip:private"]
    assert rules(scan("x.md", "nas at 192." + "168.1.5\n")) == ["ip:private"]
    assert scan("x.md", "bind 127.0.0.1 and example 192.0.2.10\n") == []


def test_uuid_is_flagged_in_markdown_but_not_in_code():
    uid = "12345678-1234-4234-8234-" + "123456789abc"
    assert rules(scan("docs/TODO.md", f"see memory {uid}\n")) == ["uuid-in-docs"]
    assert scan("brain/tests/test_x.py", f"MID = '{uid}'\n") == []


# ------------------------------------------------------------ private patterns

def test_private_patterns_file_flags_real_names(tmp_path):
    pfile = tmp_path / "hygiene-patterns.txt"
    pfile.write_text("# comment\n\n(?i)acme-" + "internal\n", encoding="utf-8")
    private = hy.load_private_patterns(pfile)
    findings = scan("docs/x.md", "host build01.ACME-" + "INTERNAL.net\n", private)
    assert rules(findings) == ["private:1"]
    assert "INTERNAL" not in findings[0].excerpt


def test_missing_private_patterns_file_means_generic_rules_only(tmp_path):
    assert hy.load_private_patterns(tmp_path / "absent.txt") == []
    assert hy.load_private_patterns(None) == []


def test_allow_marker_skips_the_line():
    token = "ghp_" + "A1b2" * 9
    assert scan("x.md", f"fixture {token}  <!-- hygiene: allow -->\n") == []


def test_finding_reports_the_line_number():
    token = "ghp_" + "A1b2" * 9
    findings = scan("x.md", f"ok\nok\nbad {token}\n")
    assert [f.line for f in findings] == [3]


# ------------------------------------------------------------ forbidden files

@pytest.mark.parametrize("path", [
    "brain-backup.tar.gz", "backups/x.tgz", "data/brain.db", "brain.sqlite3",
    "x.db-wal", "history.bundle", ".env", "brain/.env", "data/chroma/header.bin",
])
def test_forbidden_file_types_are_flagged(path):
    assert rules(hy.scan_path_name(path)) == ["forbidden-file"]


@pytest.mark.parametrize("path", [
    ".env.example", "brain/app/migrations/008_workspace.sql", "docs/assets/memorybrain-logo.jpg",
    "brain/app/data_models.py", "brain/app/chroma.py",
])
def test_ordinary_files_are_allowed(path):
    assert hy.scan_path_name(path) == []


# ------------------------------------------------------------- file iteration

def test_scan_files_skips_vendor_bundles_and_binary_content():
    token = "ghp_" + "A1b2" * 9
    items = [
        ("brain/app/static/vendor/lib.min.js", f"var t='{token}';"),
        ("docs/assets/logo.bin", "\x00\x01" + token),
        ("docs/ok.md", "fine\n"),
    ]
    assert hy.scan_files(items) == []


def test_scan_files_combines_path_and_content_findings():
    token = "ghp_" + "A1b2" * 9
    items = [("brain.db", "x"), ("docs/a.md", f"t {token}\n")]
    got = {(f.path, f.rule) for f in hy.scan_files(items)}
    assert got == {("brain.db", "forbidden-file"), ("docs/a.md", "secret:github-token")}


# ------------------------------------------------------------- pre-push input

ZERO = "0" * 40
A = "a" * 40
B = "b" * 40


def test_push_lines_are_parsed_into_rev_ranges():
    lines = (f"refs/heads/master {A} refs/heads/master {B}\n"
             f"refs/heads/new {A} refs/heads/new {ZERO}\n"
             f"(delete) {ZERO} refs/heads/gone {B}\n\n")
    assert hy.push_rev_args(lines) == [[f"{B}..{A}"], [A]]


def test_commit_message_is_scanned_like_a_document():
    token = "ghp_" + "A1b2" * 9
    findings = hy.scan_commit_message("abc1234", f"fix thing\n\nused {token}\n")
    assert rules(findings) == ["secret:github-token"]
    assert findings[0].path == "commit abc1234 message"


def test_report_prints_on_a_console_that_cannot_encode_it():
    import io
    buf = io.BytesIO()
    stream = io.TextIOWrapper(buf, encoding="cp1252", errors="strict")
    hy.emit("docs/a.md:1: private:3: goes → there", stream)
    stream.flush()
    assert b"docs/a.md:1: private:3: goes" in buf.getvalue()


def test_format_report_names_file_line_and_rule_without_the_value():
    token = "ghp_" + "A1b2" * 9
    report = hy.format_report(hy.scan_files([("docs/a.md", f"t {token}\n")]))
    assert "docs/a.md:1" in report and "secret:github-token" in report
    assert token not in report
    assert re.search(r"1 finding", report)
