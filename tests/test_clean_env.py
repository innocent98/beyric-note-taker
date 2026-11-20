"""`scripts/clean_env.py` (rewrites the server's .env into the .env.example layout) and the files
it must stay in step with: `.env.example`, `deploy/README.md` and `app/config.py`.

Every secret below is a unique fake string, so a test can assert it never reaches stdout or stderr.
"""

import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import Settings
from scripts import clean_env

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "clean_env.py"

PG = "pg-FAKE-4f1d9a"
SESSION = "session-FAKE-" + "x" * 40
OPENAI = "sk-FAKE-openai-7e2b"
VEXA_DB = "vexadb-FAKE-31c8"
VEXA_STORE = "vexastore-FAKE-90aa"
SECRETS = [PG, SESSION, OPENAI, VEXA_DB, VEXA_STORE]

REQUIRED_TEXT = (
    f"POSTGRES_PASSWORD={PG}\n"
    f"SESSION_SECRET={SESSION}\n"
    f"OPENAI_API_KEY={OPENAI}\n"
    f"VEXA_DB_PASSWORD={VEXA_DB}\n"
    f"VEXA_STORAGE_SECRET={VEXA_STORE}\n"
)


def write_env(tmp_path, text, mode=0o600, binary=False):
    path = tmp_path / ".env"
    path.write_bytes(text if binary else text.encode())
    path.chmod(mode)
    return path


def run(path, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(path), *args], capture_output=True, text=True
    )


def assert_no_secret(result, *secrets):
    for secret in secrets or SECRETS:
        assert secret not in result.stdout
        assert secret not in result.stderr


def backups(tmp_path):
    return sorted(tmp_path.glob(".env.bak-*"))


# ---- the dry run ----


def test_a_dry_run_prints_key_names_only_and_changes_nothing(tmp_path):
    text = REQUIRED_TEXT + "AUDIO_RETENTION_DAYS=7\nMULTI_TEAM_ENABLED=true\n# a comment\n\n"
    path = write_env(tmp_path, text)
    result = run(path)
    assert result.returncode == 0
    assert path.read_text() == text
    assert backups(tmp_path) == []
    assert "dry run" in result.stdout
    assert "AUDIO_RETENTION_DAYS" in result.stdout  # named as removable, with no value
    assert_no_secret(result)


def test_the_dry_run_report_lists_every_kind_of_key_by_name(tmp_path):
    text = (
        REQUIRED_TEXT
        + "POLL_INTERVAL_SECONDS=30\nPAYSTACK_WEBHOOK_SECRET=\nMADE_UP_KEY=hidden-FAKE-value\n"
        + "BOT_PROVIDER=skribby\nBOT_PROVIDER=skribby\n"
    )
    result = run(write_env(tmp_path, text))
    out = result.stdout
    assert "removed, same as the code default (1): POLL_INTERVAL_SECONDS" in out
    assert "empty line that means the same as none (1): PAYSTACK_WEBHOOK_SECRET" in out
    assert "not recognised (1): MADE_UP_KEY" in out
    assert "(1): BOT_PROVIDER" in out  # the duplicate
    assert "hidden-FAKE-value" not in out + result.stderr


# ---- the rewrite ----


def test_apply_writes_the_layout_and_keeps_every_value_byte_for_byte(tmp_path):
    text = (
        "# old comment about proxies\r\n"
        'SKRIBBY_BOT_NAME="Beyric Note Taker"\r\n'
        f"POSTGRES_PASSWORD={PG}\r\n"
        'MAIL_FROM="Beyric Note Taker <no-reply@notes.beyrictech.com>"\r\n'
        f"SESSION_SECRET={SESSION}\r\n"
        "\r\n\r\n"
        f"OPENAI_API_KEY={OPENAI}\r\n"
        "TOKEN_ENC_KEY=ab#cd=ef==\r\n"
        "OWNER_EMAIL=a@example.com\r\n"
        f"VEXA_STORAGE_SECRET={VEXA_STORE}\r\n"
        f"VEXA_DB_PASSWORD={VEXA_DB}\r\n"
        "GOOGLE_CLIENT_ID=\r\n"
        "BILLING_ALLOWED_EMAILS=a@example.com,b@example.com"  # no final newline
    )
    path = write_env(tmp_path, text, binary=False)
    result = run(path, "--apply")
    assert result.returncode == 0, result.stderr
    assert path.read_text() == (
        "# ---- Core ----\n"
        f"SESSION_SECRET={SESSION}\n"
        "OWNER_EMAIL=a@example.com\n"
        "\n"
        "# ---- Database ----\n"
        f"POSTGRES_PASSWORD={PG}\n"
        "\n"
        "# ---- Mail ----\n"
        'MAIL_FROM="Beyric Note Taker <no-reply@notes.beyrictech.com>"\n'
        "\n"
        "# ---- Google sign-in and calendar ----\n"
        "GOOGLE_CLIENT_ID=\n"
        "TOKEN_ENC_KEY=ab#cd=ef==\n"
        "\n"
        "# ---- Bot provider (Skribby) ----\n"
        'SKRIBBY_BOT_NAME="Beyric Note Taker"\n'
        "\n"
        "# ---- Transcription and notes ----\n"
        f"OPENAI_API_KEY={OPENAI}\n"
        "\n"
        "# ---- Billing ----\n"
        "BILLING_ALLOWED_EMAILS=a@example.com,b@example.com\n"
        "\n"
        "# ---- Vexa (to remove once its cleanup is done) ----\n"
        f"VEXA_DB_PASSWORD={VEXA_DB}\n"
        f"VEXA_STORAGE_SECRET={VEXA_STORE}\n"
    )
    assert_no_secret(result)


def test_apply_makes_a_mode_600_backup_first_and_a_mode_600_file(tmp_path):
    text = "# noise\n" + REQUIRED_TEXT + "\n\n"
    path = write_env(tmp_path, text, mode=0o644)
    result = run(path, "--apply")
    assert result.returncode == 0, result.stderr
    (backup,) = backups(tmp_path)
    assert backup.read_text() == text
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert re.fullmatch(r"\.env\.bak-\d{8}T\d{6}Z", backup.name)
    assert list(tmp_path.glob(".env.*.tmp")) == []  # the temp file is gone
    assert "docker compose up -d --force-recreate --no-deps web worker scheduler" in result.stdout
    assert backup.name in result.stdout
    assert_no_secret(result)


def test_apply_twice_changes_nothing_the_second_time(tmp_path):
    path = write_env(tmp_path, "# noise\n" + REQUIRED_TEXT + "MULTI_TEAM_ENABLED=true\n")
    assert run(path, "--apply").returncode == 0
    first = path.read_bytes()
    again = run(path, "--apply")
    assert again.returncode == 0
    assert "already clean" in again.stdout
    assert path.read_bytes() == first
    assert len(backups(tmp_path)) == 1  # no second backup


def test_the_last_of_two_duplicate_keys_is_the_value_kept(tmp_path):
    text = REQUIRED_TEXT + "BOT_PROVIDER=vexa\nMULTI_TEAM_ENABLED=true\nBOT_PROVIDER=skribby\n"
    path = write_env(tmp_path, text)
    assert run(path, "--apply").returncode == 0
    new = path.read_text()
    assert new.count("BOT_PROVIDER=") == 1
    assert "BOT_PROVIDER=skribby\n" in new
    assert "BOT_PROVIDER=vexa" not in new


def test_quoted_values_with_spaces_hashes_and_equals_survive(tmp_path):
    text = (
        REQUIRED_TEXT
        + "BOT_NAME='Beyric (recording) #1 = ok'\n"
        + 'MAIL_FROM="A # B <a@example.com>"   # a note\n'
        + 'BILLING_SELLER_ADDRESS="Lagos,\nNigeria"\n'
    )
    path = write_env(tmp_path, text)
    assert run(path, "--apply").returncode == 0
    new = path.read_text()
    assert "BOT_NAME='Beyric (recording) #1 = ok'\n" in new
    assert 'MAIL_FROM="A # B <a@example.com>"   # a note\n' in new
    assert 'BILLING_SELLER_ADDRESS="Lagos,\nNigeria"\n' in new


# ---- what is removed, and what never is ----


def test_a_value_equal_to_the_code_default_goes_and_a_different_one_stays(tmp_path):
    text = (
        REQUIRED_TEXT
        + "AUDIO_RETENTION_DAYS=7\nPOLL_INTERVAL_SECONDS=45\nGOOGLE_OAUTH_TESTING=false\n"
        + "NOTES_MODEL=gpt-6-luna\nTRANSCRIPTION_MODEL=other-model\nMICROSOFT_AUTHORITY=common\n"
        + 'SKRIBBY_REGION="EU"\nTRANSCRIPT_SHADOW_PERCENT=0\n'
    )
    path = write_env(tmp_path, text)
    assert run(path, "--apply").returncode == 0
    new = path.read_text()
    for gone in (
        "AUDIO_RETENTION_DAYS",
        "GOOGLE_OAUTH_TESTING",
        "NOTES_MODEL",
        "SKRIBBY_REGION",
        "TRANSCRIPT_SHADOW_PERCENT",
    ):
        assert f"{gone}=" not in new
    for kept in (
        "POLL_INTERVAL_SECONDS=45",
        "TRANSCRIPTION_MODEL=other-model",
        "MICROSOFT_AUTHORITY=common",
    ):
        assert kept in new


def test_keys_whose_default_is_not_a_plain_constant_are_never_removed(tmp_path):
    text = (
        REQUIRED_TEXT
        + "BOT_MAX_LOCAL=1\nTEAMS_JOIN_ENABLED=true\nBILLING_LIVE=false\nTOKEN_ENC_KEY_VERSION=1\n"
        + "BOT_PROVIDER=skribby\nAPP_ENV=production\nCOOKIE_SECURE=true\nHOURS_MODE=off\n"
        + 'VEXA_IMAGE_TAG=v012\nBOT_NAME="Beyric Note Taker (recording)"\n'
    )
    path = write_env(tmp_path, text)
    assert run(path, "--apply").returncode == 0
    new = path.read_text()
    for kept in (
        "BOT_MAX_LOCAL=1",
        "TEAMS_JOIN_ENABLED=true",
        "BILLING_LIVE=false",
        "TOKEN_ENC_KEY_VERSION=1",
        "BOT_PROVIDER=skribby",
        "APP_ENV=production",
        "COOKIE_SECURE=true",
        "HOURS_MODE=off",
        "VEXA_IMAGE_TAG=v012",
        'BOT_NAME="Beyric Note Taker (recording)"',
    ):
        assert kept in new


def test_a_kept_value_with_a_dollar_blocks_apply_and_is_named_not_shown(tmp_path):
    text = REQUIRED_TEXT + 'AUDIO_RETENTION_DAYS=$DAYS\nNOTES_MODEL="gpt-6-luna$FAKE_X_5521"\n'
    path = write_env(tmp_path, text)
    dry = run(path)
    assert dry.returncode == 0  # a dry run only warns
    assert "contain $" in dry.stdout and "AUDIO_RETENTION_DAYS, NOTES_MODEL" in dry.stdout
    result = run(path, "--apply")
    assert result.returncode != 0
    assert "contains $" in result.stderr
    assert "FAKE_X_5521" not in dry.stdout + result.stdout + result.stderr
    untouched(path, tmp_path, text)


def test_a_dollar_inside_single_quotes_is_literal_and_does_not_block(tmp_path):
    text = REQUIRED_TEXT + "MAIL_PROVIDER=''\nADMIN_ALERT_EMAIL='a$b@example.com'\n"
    path = write_env(tmp_path, text)
    assert run(path, "--apply").returncode == 0
    assert "ADMIN_ALERT_EMAIL='a$b@example.com'\n" in path.read_text()


def test_a_dollar_in_an_unrecognised_kept_value_blocks_apply_too(tmp_path):
    text = REQUIRED_TEXT + "SOMETHING_ELSE=a$B\n"
    path = write_env(tmp_path, text)
    assert run(path, "--apply").returncode != 0
    untouched(path, tmp_path, text)


def test_an_empty_paystack_webhook_secret_is_dropped_and_a_set_one_is_kept(tmp_path):
    path = write_env(tmp_path, REQUIRED_TEXT + "PAYSTACK_WEBHOOK_SECRET=\n")
    assert run(path, "--apply").returncode == 0
    assert "PAYSTACK_WEBHOOK_SECRET" not in path.read_text()

    other = tmp_path / "set"
    other.mkdir()
    path = write_env(other, REQUIRED_TEXT + "PAYSTACK_WEBHOOK_SECRET=whsec_FAKE_5521\n")
    result = run(path, "--apply")
    assert "PAYSTACK_WEBHOOK_SECRET=whsec_FAKE_5521\n" in path.read_text()
    assert_no_secret(result, "whsec_FAKE_5521")


def test_unrecognised_keys_are_kept_at_the_bottom_and_named(tmp_path):
    text = REQUIRED_TEXT + "SOMETHING_ELSE=keep-FAKE-me\nCOMPOSE_PROFILES=vexa\n"
    path = write_env(tmp_path, text)
    result = run(path, "--apply")
    new = path.read_text()
    assert new.endswith(
        "# ---- Not recognised (kept) ----\nSOMETHING_ELSE=keep-FAKE-me\nCOMPOSE_PROFILES=vexa\n"
    )
    assert "SOMETHING_ELSE, COMPOSE_PROFILES" in result.stdout
    assert_no_secret(result, "keep-FAKE-me")


def test_the_old_bot_limit_name_is_kept_beside_the_new_one(tmp_path):
    text = REQUIRED_TEXT + "MAX_ACTIVE_BOTS=3\n"
    path = write_env(tmp_path, text)
    assert run(path, "--apply").returncode == 0
    assert "MAX_ACTIVE_BOTS=3\n" in path.read_text()
    assert "Not recognised" not in path.read_text()


# ---- refusals: nothing is written ----


def untouched(path, tmp_path, text):
    assert path.read_text() == text
    assert backups(tmp_path) == []


@pytest.mark.parametrize(
    "missing",
    [
        "POSTGRES_PASSWORD",
        "SESSION_SECRET",
        "OPENAI_API_KEY",
        "VEXA_DB_PASSWORD",
        "VEXA_STORAGE_SECRET",
    ],
)
def test_a_missing_required_key_refuses_and_names_it(tmp_path, missing):
    text = "".join(line for line in REQUIRED_TEXT.splitlines(True) if not line.startswith(missing))
    path = write_env(tmp_path, text + "MULTI_TEAM_ENABLED=true\n")
    result = run(path, "--apply")
    assert result.returncode != 0
    assert f"MISSING REQUIRED (1): {missing}" in result.stdout
    untouched(path, tmp_path, text + "MULTI_TEAM_ENABLED=true\n")
    assert_no_secret(result)


def test_an_empty_required_key_counts_as_missing(tmp_path):
    text = REQUIRED_TEXT.replace(f"OPENAI_API_KEY={OPENAI}", "OPENAI_API_KEY=")
    path = write_env(tmp_path, text)
    assert run(path, "--apply").returncode != 0
    untouched(path, tmp_path, text)


def test_a_token_key_is_required_once_google_or_the_calendar_needs_it(tmp_path):
    text = REQUIRED_TEXT + "GOOGLE_CLIENT_ID=abc.apps.example\n"
    path = write_env(tmp_path, text)
    result = run(path, "--apply")
    assert result.returncode != 0
    assert "TOKEN_ENC_KEY" in result.stdout
    untouched(path, tmp_path, text)
    ok = tmp_path / "ok"
    ok.mkdir()
    path = write_env(ok, text + "TOKEN_ENC_KEY=ZmFrZQ==\n")
    assert run(path, "--apply").returncode == 0


def test_an_unparseable_line_refuses_without_quoting_it(tmp_path):
    text = REQUIRED_TEXT + "this line has FAKE-garbage-9931 and no equals sign\n"
    path = write_env(tmp_path, text)
    result = run(path, "--apply")
    assert result.returncode != 0
    assert "line 6" in result.stderr
    assert "FAKE-garbage-9931" not in result.stdout + result.stderr
    untouched(path, tmp_path, text)


def test_an_unclosed_quote_refuses(tmp_path):
    text = REQUIRED_TEXT + 'MAIL_FROM="never closed FAKE-open-1177\n'
    path = write_env(tmp_path, text)
    result = run(path, "--apply")
    assert result.returncode != 0
    assert "FAKE-open-1177" not in result.stdout + result.stderr
    untouched(path, tmp_path, text)


def test_text_after_a_closing_quote_refuses(tmp_path):
    text = REQUIRED_TEXT + 'MAIL_FROM="a"b\n'
    path = write_env(tmp_path, text)
    assert run(path, "--apply").returncode != 0
    untouched(path, tmp_path, text)


def test_a_symlink_is_refused(tmp_path):
    real = tmp_path / "real.env"
    real.write_text(REQUIRED_TEXT)
    link = tmp_path / ".env"
    link.symlink_to(real)
    result = run(link, "--apply")
    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert real.read_text() == REQUIRED_TEXT
    assert link.is_symlink()
    assert backups(tmp_path) == []


def test_a_file_owned_by_someone_else_is_refused(tmp_path, monkeypatch, capsys):
    path = write_env(tmp_path, REQUIRED_TEXT)
    monkeypatch.setattr(os, "geteuid", lambda: path.stat().st_uid + 1)
    assert clean_env.main([str(path), "--apply"]) == 1
    captured = capsys.readouterr()
    assert "another user" in captured.err
    untouched(path, tmp_path, REQUIRED_TEXT)


def test_a_missing_file_and_a_directory_are_refused(tmp_path):
    assert run(tmp_path / "nope").returncode != 0
    assert run(tmp_path).returncode != 0


def test_a_write_failure_never_prints_a_traceback_or_a_value(tmp_path, monkeypatch, capsys):
    path = write_env(tmp_path, "# n\n" + REQUIRED_TEXT)

    def boom(*_args, **_kwargs):
        raise RuntimeError(f"echoed {SESSION}")

    monkeypatch.setattr(clean_env, "write_private", boom)
    assert clean_env.main([str(path), "--apply"]) == 2
    captured = capsys.readouterr()
    assert SESSION not in captured.out + captured.err
    assert "RuntimeError" in captured.err


def test_the_new_file_would_never_drop_a_key_it_was_given():
    plan = clean_env.Plan(clean_env.parse(REQUIRED_TEXT + "MULTI_TEAM_ENABLED=true\n"))
    with pytest.raises(clean_env.EnvError):
        plan.check_nothing_lost(plan.render().replace("MULTI_TEAM_ENABLED=true\n", ""))
    with pytest.raises(clean_env.EnvError):
        plan.check_nothing_lost(
            plan.render().replace("MULTI_TEAM_ENABLED=true", "MULTI_TEAM_ENABLED=no")
        )


# ---- the files it must stay in step with ----


def example_layout():
    """[(section title, [keys, commented or not])] read from .env.example."""
    sections: list[tuple[str, list[str]]] = []
    for line in (ROOT / ".env.example").read_text().splitlines():
        header = re.fullmatch(r"# ---- (.+) ----", line)
        if header:
            sections.append((header.group(1), []))
            continue
        key = re.match(r"#? ?([A-Z][A-Z0-9_]*)=", line)
        if key:
            sections[-1][1].append(key.group(1))
        else:
            assert line == "", f"unexpected line in .env.example: {line!r}"
    return sections


def test_the_cleaner_layout_is_the_env_example_layout():
    assert clean_env.LAYOUT == example_layout()


def test_every_default_in_the_cleaner_is_the_code_default():
    for key, (kind, default) in clean_env.DEFAULTS.items():
        actual = Settings.model_fields[key.lower()].default
        if actual is None:
            actual = ""
        assert actual == default, key
        assert {"b": bool, "i": int, "s": str}[kind] is type(default), key


def test_the_optional_section_shows_the_defaults_the_cleaner_removes():
    optional = dict(
        (m.group(1), m.group(2))
        for m in (
            re.match(r"# ([A-Z0-9_]+)=(.*)", line)
            for line in (ROOT / ".env.example")
            .read_text()
            .split("# ---- Optional")[1]
            .split("# ---- Local")[0]
            .splitlines()
        )
        if m
    )
    for key, (kind, default) in clean_env.DEFAULTS.items():
        shown = optional[key]
        assert shown == (str(default).lower() if kind == "b" else str(default)), key


def test_compose_and_the_app_share_the_audio_retention_default():
    assert clean_env.DEFAULTS["AUDIO_RETENTION_DAYS"] == ("i", 7)
    assert "${AUDIO_RETENTION_DAYS:-7}" in (ROOT / "compose.yaml").read_text()


def test_every_env_example_name_is_known_to_the_app_or_compose():
    fields = {name.upper() for name in Settings.model_fields}
    compose = (ROOT / "compose.yaml").read_text()
    for _title, keys in example_layout():
        for key in keys:
            assert key in fields or f"${{{key}" in compose or key == "DATABASE_URL", key


def test_every_setting_has_a_line_in_env_example():
    listed = {key for _title, keys in example_layout() for key in keys}
    assert {name.upper() for name in Settings.model_fields} <= listed


def test_the_cleaner_recognises_every_active_line_of_env_example():
    active = (ROOT / ".env.example").read_text()
    active = "\n".join(line for line in active.splitlines() if not line.startswith("#"))
    plan = clean_env.Plan(clean_env.parse(active))
    assert plan.unrecognised == []
    assert set(plan.missing) == set(clean_env.REQUIRED)  # every required value starts empty


def test_env_example_holds_no_real_looking_value():
    text = (ROOT / ".env.example").read_text()
    for address in re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", text):
        assert address.endswith(("@example.com", "@notes.beyrictech.com")), address
    assert not re.search(r"sk-[A-Za-z0-9]{8,}|whsec_[A-Za-z0-9]{8,}|FLWSECK-[A-Za-z0-9]{8,}", text)


def test_deploy_readme_has_a_row_for_every_env_example_name():
    readme = (ROOT / "deploy" / "README.md").read_text()
    named = set(re.findall(r"`([A-Z][A-Z0-9_]+)`", readme))
    for _title, keys in example_layout():
        for key in keys:
            assert key in named, f"{key} is in .env.example but not in deploy/README.md"


@pytest.mark.parametrize("key", sorted(clean_env.DEFAULTS))
def test_settings_are_identical_with_a_removable_line_present_or_absent(key, monkeypatch):
    """The proof that removing an equals-default line cannot change behaviour."""
    kind, default = clean_env.DEFAULTS[key]
    without = Settings(_env_file=None).model_dump()
    monkeypatch.setenv(key, str(default).lower() if kind == "b" else str(default))
    assert Settings(_env_file=None).model_dump() == without


def test_an_empty_paystack_webhook_secret_behaves_like_none(monkeypatch):
    without = Settings(_env_file=None).model_dump()
    monkeypatch.setenv("PAYSTACK_WEBHOOK_SECRET", "")
    assert Settings(_env_file=None).model_dump() == without


# ---- the example must not start a server as it stands ----


def example_values():
    """The active lines of .env.example as {KEY: value}, quotes removed."""
    values = {}
    for line in (ROOT / ".env.example").read_text().splitlines():
        if line and not line.startswith("#"):
            key, rhs = line.split("=", 1)
            values[key] = clean_env.effective(rhs)[0]
    return values


def test_every_secret_and_password_in_env_example_is_empty():
    values = example_values()
    secrets = [
        *clean_env.REQUIRED,
        "OWNER_EMAIL",
        "OWNER_PASSWORD",
        "EDGE_SHARED_SECRET",
        "RESEND_API_KEY",
        "SKRIBBY_API_KEY",
        "VEXA_API_KEY",
        "TOKEN_ENC_KEY",
    ]
    for key in secrets:
        assert values[key] == "", f"{key} has a value in .env.example: copying it would start"
    for key, value in values.items():
        assert not re.search(r"password|secret|api_key|token|_key", key, re.I) or value == "", key


def _load_example_into_env(monkeypatch, tmp_path, drop=()):
    monkeypatch.chdir(tmp_path)  # no stray .env is read
    for key, value in example_values().items():
        if key in drop:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def test_the_app_refuses_to_start_on_the_example_as_it_stands(monkeypatch, tmp_path):
    from pydantic import ValidationError

    from app import auth

    _load_example_into_env(monkeypatch, tmp_path)
    with pytest.raises(ValidationError):  # skribby with an empty key
        Settings(_env_file=None)
    _load_example_into_env(monkeypatch, tmp_path, drop={"BOT_PROVIDER"})
    with pytest.raises(RuntimeError, match="session_secret"):  # check_start refuses an empty one
        auth.check_start(Settings(_env_file=None))


def test_migration_0006_refuses_the_example_owner_values(monkeypatch, tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "m0006", ROOT / "alembic" / "versions" / "0006_users_and_auth.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _load_example_into_env(monkeypatch, tmp_path, drop={"BOT_PROVIDER"})
    with pytest.raises(RuntimeError, match="OWNER_EMAIL and OWNER_PASSWORD"):
        module._owner()


def test_every_required_compose_variable_is_empty_in_the_example():
    compose = (ROOT / "compose.yaml").read_text()
    required = set(re.findall(r"\$\{([A-Z_]+):\?", compose))
    assert {"POSTGRES_PASSWORD", "VEXA_DB_PASSWORD", "VEXA_STORAGE_SECRET"} <= required
    for key in required:
        assert example_values()[key] == "", key


def _docker_compose_available() -> bool:
    try:
        return (
            subprocess.run(
                ["docker", "compose", "version"], capture_output=True, timeout=20
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


@pytest.mark.skipif(not _docker_compose_available(), reason="docker compose absent")
def test_compose_refuses_the_example_as_it_stands(tmp_path):
    (tmp_path / "compose.yaml").write_text((ROOT / "compose.yaml").read_text())
    (tmp_path / ".env").write_text((ROOT / ".env.example").read_text())
    (tmp_path / ".env.vexa").write_text("")
    env = {k: v for k, v in os.environ.items() if k != "COMPOSE_PROFILES"}

    def config():
        return subprocess.run(
            ["docker", "compose", "config", "-q"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    refused = config()
    assert refused.returncode != 0
    assert "POSTGRES_PASSWORD" in refused.stderr
    filled = (ROOT / ".env.example").read_text()
    for key in ("POSTGRES_PASSWORD", "VEXA_DB_PASSWORD", "VEXA_STORAGE_SECRET"):
        filled = filled.replace(f"\n{key}=\n", f"\n{key}=x\n")
    (tmp_path / ".env").write_text(filled)
    assert config().returncode == 0  # so the refusal above was really about those three


# ---- review fixes: case, partial backup, a file that changed under us ----


def test_the_token_key_rule_ignores_the_case_of_app_env(tmp_path):
    text = REQUIRED_TEXT + "APP_ENV=Production\nGOOGLE_CLIENT_ID=abc.apps.example\n"
    path = write_env(tmp_path, text)
    result = run(path, "--apply")
    assert result.returncode != 0
    assert "TOKEN_ENC_KEY" in result.stdout


def test_a_failed_backup_write_leaves_no_partial_backup(tmp_path, monkeypatch, capsys):
    text = "# n\n" + REQUIRED_TEXT
    path = write_env(tmp_path, text)

    def boom(_fd):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "fsync", boom)
    assert clean_env.main([str(path), "--apply"]) == 2
    assert backups(tmp_path) == []
    assert path.read_text() == text
    assert "No space left" in capsys.readouterr().err


def test_a_file_changed_while_it_is_rewritten_is_not_replaced(tmp_path, monkeypatch, capsys):
    text = "# n\n" + REQUIRED_TEXT
    path = write_env(tmp_path, text)
    real_mkstemp = clean_env.tempfile.mkstemp

    def mkstemp_then_someone_edits(*args, **kwargs):
        made = real_mkstemp(*args, **kwargs)
        path.write_text(text + "ADDED_BY_SOMEONE=1\n")
        return made

    monkeypatch.setattr(clean_env.tempfile, "mkstemp", mkstemp_then_someone_edits)
    assert clean_env.main([str(path), "--apply"]) == 1
    assert "changed while" in capsys.readouterr().err
    assert path.read_text() == text + "ADDED_BY_SOMEONE=1\n"
    assert list(tmp_path.glob(".env.*.tmp")) == []


def test_the_printed_recreate_command_names_the_services_and_the_gate(tmp_path):
    path = write_env(tmp_path, "# n\n" + REQUIRED_TEXT)
    out = run(path, "--apply").stdout
    assert "docker compose up -d --force-recreate --no-deps web worker scheduler" in out
    assert "ONLY when no meeting is being recorded and no join is due" in out
    assert ". deploy/gate.sh; join_due_soon" in out
