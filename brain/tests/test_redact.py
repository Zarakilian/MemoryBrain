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


# ------------------------------------------------------------- review follow-ups

def test_a_token_inside_a_private_key_never_saves_the_key():
    body = "MIIEow" + "Bz" * 30
    text = BEGIN + "RSA PRIVATE KEY-----\n" + body + f"\nnote: pushed with {GH}"
    red, rules = redact(text)
    assert body not in red and GH not in red
    assert red == tag("private-key") and rules == ["private-key"]


def test_a_key_block_with_an_aws_id_comment_is_one_block():
    block = BEGIN + "RSA PRIVATE KEY-----\n# " + AWS + "\nMIIE" + "q" * 20 + "\n" + END + \
        "RSA PRIVATE KEY-----"
    assert redact(block) == (tag("private-key"), ["private-key"])


def test_bare_password_with_a_quoted_passphrase():
    for name in ("PASSWORD", "password", "Pwd"):
        line = f"{name}='" + "correct horse battery staple" + "'"
        assert out(line) == f"{name}='{tag('env-secret')}'", name


def test_real_secrets_that_start_like_code_are_still_redacted():
    for value in ('"$Tr0ngPass!"', "%4kd9sLz2!xQ", "(Jx8!kqLmN2vB", "Summer(2024)x!",
                  "winter.is.coming", "abcd1234.efgh5678"):
        line = "DB_PASSWORD=" + value
        assert "env-secret" in names(line), line


def test_a_quoted_value_may_hold_the_other_quote():
    line = 'DB_PASSWORD="' + "it's-a-secret-value-123" + '"'
    assert out(line) == 'DB_PASSWORD="' + tag("env-secret") + '"'


def test_hashes_next_to_markers_stay_idempotent():
    for text in (f"Used {GH} to push {SHA40}", f"key {GH} {HEX64}"):
        once, _ = redact(text)
        assert redact(once) == (once, [])


def test_redis_and_at_sign_passwords():
    r = redact("cache redis://" + ":" + "s3cr3tp4ss" + "@cache.example.com:6379/0")[0]
    assert "s3cr3tp4ss" not in r
    p = redact("db postgres://admin:" + "P@ssw0rd99" + "@db.example.com/app")[0]
    assert "P@ssw0rd99" not in p and "ssw0rd99" not in p and "db.example.com/app" in p
    for kept in ("https://github.com/@user/repo", "example.com:8080/@user"):
        assert out(kept) == kept


def test_tokens_glued_to_an_escaped_newline_are_caught():
    assert GH not in out('{"log": "line\\n' + GH + '"}')


def test_keyword_hex_needs_a_whole_keyword():
    for text in (f'{{"author": "jane", "sha": "{SHA40}"}}', f"keyword {HEX64}",
                 f"tokenizer {HEX64}"):
        assert out(text) == text, text


def test_paths_and_flags_are_not_connection_passwords():
    for text in ("PWD=/home/user/project", "pwd=$(pwd)", "--require-password=true"):
        assert out(text) == text, text


def test_bearer_leaves_a_sentence_full_stop():
    value = "Qw3" * 8
    assert out(f"use Bearer {value}.") == f"use Bearer {tag('bearer')}."


def test_idempotent_on_generated_text():
    import random
    rng = random.Random(20260930)
    pieces = [GH, AWS, SK, SLACK, JWT, HEX64, SHA40, "key", "token:", "secret", "Bearer",
              "API_KEY=" + "abcdefgh123", "password=" + "hunter22x", "https://u:" + "pw123456" + "@" + "h.example.com/x",
              "the", "car", "export", "=", ":", '"', "'", "\n", "author", "tokenizer"]
    for _ in range(2000):
        text = " ".join(rng.choice(pieces) for _ in range(rng.randint(1, 12)))
        once, _rules = redact(text)
        assert redact(once) == (once, []), text


def test_many_secrets_stay_fast():
    import time
    text = " ".join(f"{AWS[:-4]}{i:04d}" for i in range(4000))
    t = time.perf_counter()
    red, rules = redact(text)
    assert time.perf_counter() - t < 1.0 and rules == ["aws-key-id"]



def test_threads_descriptions_and_policy_notes_are_redacted(tmp_db):
    from app.db import connect
    from app.exchange import post_task, reply_to_thread
    from app.models import Project
    from app.policy import get_policy, set_policy
    from app.storage import get_project, upsert_project
    from app.workspace.identity import set_identity
    token = "ghp_" + "b" * 36
    thread = post_task(project="acme", title=f"Mirror {token}", body=f"use {token} to push",
                       from_agent="claude", db_path=tmp_db)
    reply_to_thread(thread["thread_id"], body=f"done with {token}", from_agent="grok",
                    db_path=tmp_db)
    conn = connect(tmp_db)
    try:
        stored = " ".join(r[0] for r in conn.execute("SELECT body FROM agent_messages"))
        stored += " ".join(r[0] for r in conn.execute("SELECT title FROM agent_threads"))
    finally:
        conn.close()
    assert token not in stored and "[REDACTED:github-token]" in stored
    upsert_project(Project(slug="acme", name="Acme"), db_path=tmp_db)
    set_identity("acme", tmp_db, description=f"Acme billing, deploy key {token}")
    assert token not in get_project("acme", db_path=tmp_db).description
    set_policy("acme", notes=f"push with {token}", db_path=tmp_db)
    assert token not in get_policy("acme", db_path=tmp_db)["notes"]


def test_an_identifier_called_a_key_is_not_a_secret():
    """A dedup, cache or partition key is an id that happens to be hex; the
    word "key" in front of it does not make it a credential."""
    for text in (f"so the dedup key `{HEX64}` did not change",
                 f"Alert dedupe key: {HEX64}", f"the idempotency key {HEX64}",
                 f"cache key={HEX64}", f"partition key {HEX64}", f"primary key {HEX64}"):
        assert out(text) == text, text


def test_a_real_key_next_to_hex_is_still_redacted():
    for text in (f"api key {HEX64}", f"signing key: {HEX64}", f"the key is {HEX64}"):
        assert "[REDACTED:" in out(text), text
