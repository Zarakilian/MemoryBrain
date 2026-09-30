"""Secret redaction on the write path.

Every secret-shaped value is assembled at runtime, so this file holds no
literal secret and stays clean under the repo hygiene guard.
"""
from app.redact import REDACTION_FORMAT, redact

GH = "ghp_" + "A1b2" * 9
PAT = "github_pat_" + "Z9y8_" * 5
SK = "sk-" + "proj-" + "Q7w6" * 6
AWS = "AKIA" + "ABCD1234EFGH5678"
SLACK = "xoxb-" + "1234567890-abcdefghij"
JWT = "eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0" + "." + "abcDEF123456ghiJKL789"
HEX64 = "3f" + "a9" * 31
SHA40 = "a1b2c3d4" * 5
BEGIN = "-----BEGIN "
END = "-----END "


def names(text):
    return redact(text)[1]


def out(text):
    return redact(text)[0]


def tag(rule):
    return REDACTION_FORMAT.format(rule=rule)


# ---------------------------------------------------------------- each rule

def test_github_token():
    assert out(f"export GH={GH} now") == f"export GH={tag('github-token')} now"
    assert names(f"x {GH}") == ["github-token"]


def test_github_fine_grained_pat():
    assert out(f"token {PAT}") == f"token {tag('github-token')}"


def test_api_key():
    text, rules = redact(f"key is {SK}.")
    assert text == f"key is {tag('api-key')}." and rules == ["api-key"]


def test_aws_key_id():
    text, rules = redact(f"id {AWS} here")
    assert text == f"id {tag('aws-key-id')} here" and rules == ["aws-key-id"]


def test_slack_token():
    assert out(f"slack {SLACK}") == f"slack {tag('slack-token')}"


def test_private_key_block_with_end():
    block = BEGIN + "RSA PRIVATE KEY-----\nMIIEow" + "AAAA\nBBBB\n" + END + "RSA PRIVATE KEY-----"
    text, rules = redact(f"before\n{block}\nafter")
    assert text == f"before\n{tag('private-key')}\nafter" and rules == ["private-key"]


def test_private_key_block_without_end_runs_to_end_of_text():
    text = out("key:\n" + BEGIN + "OPENSSH PRIVATE KEY-----\nb3BlbnNzaA" + "tail")
    assert text == f"key:\n{tag('private-key')}"


def test_pgp_private_key_block():
    block = BEGIN + "PGP PRIVATE KEY BLOCK-----\nlQOYBG" + "xyz\n" + END + "PGP PRIVATE KEY BLOCK-----"
    assert out(f"{block}\nnext") == f"{tag('private-key')}\nnext"


def test_bearer_keeps_the_word_and_hides_the_value():
    value = "Qw3" * 8
    assert out(f"Authorization: Bearer {value}") == f"Authorization: Bearer {tag('bearer')}"


def test_bearer_value_may_hold_base64_characters():
    value = "ab+cd/ef~gh" * 3 + "=="
    assert out(f"bearer {value} end") == f"bearer {tag('bearer')} end"


def test_jwt():
    assert out(f"cookie {JWT} set") == f"cookie {tag('jwt')} set"


def test_url_credentials_keep_scheme_and_host():
    url = "https://" + "deploy" + ":" + "s3cretpass" + "@git.example.com/acme/app.git"
    text, rules = redact(f"remote {url}")
    assert text == f"remote https://{tag('url-credentials')}git.example.com/acme/app.git"
    assert rules == ["url-credentials"]


def test_connection_string_password():
    conn = "Server=db.example.com;Database=app;User Id=svc;" + "Password=" + "Hunter2x!" + ";"
    text, rules = redact(conn)
    assert text == (
        "Server=db.example.com;Database=app;User Id=svc;"
        f"Password={tag('connection-password')};"
    )
    assert rules == ["connection-password"]


def test_env_style_secret_keeps_quotes():
    line = "BRAIN_API_KEY=" + '"' + "abc123def456ghi" + '"'
    assert out(line) == "BRAIN_API_KEY=" + '"' + tag("env-secret") + '"'


def test_env_style_secret_colon_form():
    assert out("DB_PASSWORD: " + "correct-horse-battery") == f"DB_PASSWORD: {tag('env-secret')}"


def test_env_secret_quoted_value_may_contain_spaces():
    line = "ADMIN_PASSWORD='" + "correct horse battery" + "'"
    assert out(line) == "ADMIN_PASSWORD='" + tag("env-secret") + "'"


def test_env_secret_json_style():
    line = '{"api_key": "' + "abcd1234efgh5678" + '", "user": "svc"}'
    assert out(line) == '{"api_key": "' + tag("env-secret") + '", "user": "svc"}'


def test_keyword_hex_near_token_word():
    text, rules = redact(f"Bearer-less shared token (shared): {HEX64}")
    assert text == f"Bearer-less shared token (shared): {tag('keyword-hex')}"
    assert rules == ["keyword-hex"]


# ------------------------------------------------------------- must not touch

def test_commit_sha_alone_is_kept():
    assert out(f"merged {SHA40} into master") == f"merged {SHA40} into master"


def test_file_hash_without_keyword_is_kept():
    text = f"sha256 of the file is {HEX64}"
    assert out(text) == text


def test_uuid_is_kept():
    text = "memory 12345678-1234-4234-8234-123456789abc updated"
    assert out(text) == text


def test_prose_about_secrets_is_kept():
    for text in ("the api key is set in .env", "token count 5", "rotate the password monthly"):
        assert out(text) == text and names(text) == []


def test_plain_urls_and_ssh_remotes_are_kept():
    for text in ("see https://example.com/docs?page=2", "git clone git" + "@" + "github.com:acme/app.git"):
        assert out(text) == text


def test_existing_redaction_marker_is_not_touched():
    for text in ("API_KEY=" + tag("env-secret"), "Password=" + tag("connection-password") + ";"):
        assert out(text) == text and names(text) == []


def test_env_secret_skips_code_references_and_placeholders():
    for text in (
        'api_key = os.environ["API_KEY"]',
        "token = get_token_from_vault()",
        "SECRET_KEY = settings.SECRET_KEY",
        "API_TOKEN=${API_TOKEN}",
        "password: <your-password-here>",
    ):
        assert out(text) == text, text


# ------------------------------------------------------------- whole-text rules

def test_idempotent():
    once, _ = redact(f"a {GH} b {AWS} c BRAIN_API_KEY=abcdefghij123")
    twice, rules = redact(once)
    assert twice == once and rules == []


def test_rule_names_are_deduplicated():
    assert names(f"{AWS} then {AWS} again") == ["aws-key-id"]


def test_rule_names_follow_first_appearance_in_the_text():
    # The rule table runs github-token before aws-key-id; the text order wins.
    assert names(f"{AWS} then {GH} then {AWS}") == ["aws-key-id", "github-token"]


def test_multiple_secrets_in_one_text():
    text = f"gh {GH}\naws {AWS}\nslack {SLACK}\n"
    assert out(text) == (
        f"gh {tag('github-token')}\naws {tag('aws-key-id')}\nslack {tag('slack-token')}\n"
    )


def test_empty_text():
    assert redact("") == ("", [])
