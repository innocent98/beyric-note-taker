import os

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from tests.conftest import TEST_DATABASE_URL

SCRATCH_DB = "notetaker_migration_scratch"
OWNER_EMAIL = "owner@example.test"  # placeholders: migration 0006 creates the admin from these
OWNER_PASSWORD = "-".join(["migration", "test", "placeholder"])


@pytest.fixture(autouse=True)
def owner_env(monkeypatch):
    monkeypatch.setenv("OWNER_EMAIL", OWNER_EMAIL)
    monkeypatch.setenv("OWNER_PASSWORD", OWNER_PASSWORD)
    monkeypatch.setenv("APP_ENV", "production")


@pytest.fixture
def scratch_url():
    base = make_url(TEST_DATABASE_URL)
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"drop database if exists {SCRATCH_DB}"))
        conn.execute(text(f"create database {SCRATCH_DB}"))
    yield base.set(database=SCRATCH_DB).render_as_string(hide_password=False)
    with admin.connect() as conn:
        conn.execute(text(f"drop database if exists {SCRATCH_DB}"))
    admin.dispose()


def alembic_config(url: str) -> Config:
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def test_single_head():
    heads = ScriptDirectory.from_config(alembic_config(TEST_DATABASE_URL)).get_heads()
    assert len(heads) == 1


def test_upgrade_seeds_templates_and_downgrade_removes_tables(scratch_url):
    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "head")
    engine = create_engine(scratch_url)
    with engine.connect() as conn:
        names = [r[0] for r in conn.execute(text("select name from templates order by id"))]
    assert names == ["General", "Client call", "Team update", "Interview"]
    tables = set(inspect(engine).get_table_names())
    assert {"meetings", "templates", "vocabulary_terms"} <= tables

    command.downgrade(cfg, "base")
    assert not {"meetings", "templates", "vocabulary_terms"} & set(
        inspect(engine).get_table_names()
    )
    engine.dispose()


def test_session_epoch_table_is_created_seeded_and_dropped(scratch_url):
    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "head")
    engine = create_engine(scratch_url)
    with engine.connect() as conn:
        rows = conn.execute(text("select id, session_epoch from app_state")).all()
    assert [tuple(r) for r in rows] == [(1, 0)]

    command.downgrade(cfg, "0001")
    assert "app_state" not in inspect(engine).get_table_names()
    assert "meetings" in inspect(engine).get_table_names()
    command.upgrade(cfg, "head")  # and back up again
    assert "app_state" in inspect(engine).get_table_names()
    engine.dispose()


def test_final_fix_columns_are_added_and_dropped(scratch_url):
    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "head")
    engine = create_engine(scratch_url)
    meeting_cols = {c["name"] for c in inspect(engine).get_columns("meetings")}
    state_cols = {c["name"] for c in inspect(engine).get_columns("app_state")}
    assert {
        "vexa_recording_id",
        "speakers_incomplete",
        "completion_reason",
        "terminal_seen_at",
    } <= meeting_cols
    assert "worker_heartbeat_at" in state_cols

    command.downgrade(cfg, "0002")
    meeting_cols = {c["name"] for c in inspect(engine).get_columns("meetings")}
    assert not {"vexa_recording_id", "speakers_incomplete", "terminal_seen_at"} & meeting_cols
    assert "worker_heartbeat_at" not in {
        c["name"] for c in inspect(engine).get_columns("app_state")
    }
    command.upgrade(cfg, "head")
    engine.dispose()


def test_recording_id_widens_to_bigint_and_back(scratch_url):
    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "head")
    engine = create_engine(scratch_url)
    col = {c["name"]: c for c in inspect(engine).get_columns("meetings")}["vexa_recording_id"]
    assert isinstance(col["type"], sa.BigInteger)
    command.downgrade(cfg, "0003")
    col = {c["name"]: c for c in inspect(engine).get_columns("meetings")}["vexa_recording_id"]
    assert not isinstance(col["type"], sa.BigInteger)
    command.upgrade(cfg, "head")
    engine.dispose()


def test_join_attempts_column_is_added_and_dropped(scratch_url):
    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "head")
    engine = create_engine(scratch_url)
    assert "join_attempts" in {c["name"] for c in inspect(engine).get_columns("meetings")}
    command.downgrade(cfg, "0004")
    assert "join_attempts" not in {c["name"] for c in inspect(engine).get_columns("meetings")}
    command.upgrade(cfg, "head")
    engine.dispose()


def test_migration_matches_models(scratch_url):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.models import Base

    command.upgrade(alembic_config(scratch_url), "head")
    engine = create_engine(scratch_url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []


# ---- 0006 users and auth, 0007 ownership and usage ----


def _rows(engine, sql, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).all()


def test_0006_creates_the_admin_from_the_environment(scratch_url):
    from argon2 import PasswordHasher

    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "0006")
    engine = create_engine(scratch_url)
    [admin] = _rows(
        engine,
        "select email, is_admin, status, email_verified_at is not null, password_hash from users",
    )
    assert admin[:4] == (OWNER_EMAIL, True, "active", True)
    assert admin[4].startswith("$argon2id$")
    assert PasswordHasher().verify(admin[4], OWNER_PASSWORD)
    assert _rows(engine, "select count(*) from user_settings") == [(1,)]
    state = _rows(
        engine,
        "select signups_open, max_accounts, default_hours_cap_seconds, password_signup_enabled "
        "from app_state",
    )
    assert state == [(False, 10, 18000, False)]
    engine.dispose()


def test_0006_lower_cases_the_owner_email(scratch_url, monkeypatch):
    monkeypatch.setenv("OWNER_EMAIL", "  Owner@Example.TEST ")
    command.upgrade(alembic_config(scratch_url), "0006")
    engine = create_engine(scratch_url)
    assert _rows(engine, "select email from users") == [(OWNER_EMAIL,)]
    engine.dispose()


@pytest.mark.parametrize("missing", ["OWNER_EMAIL", "OWNER_PASSWORD"])
def test_0006_fails_loudly_in_production_without_the_owner(scratch_url, monkeypatch, missing):
    monkeypatch.setenv(missing, "")
    with pytest.raises(RuntimeError, match="OWNER_EMAIL and OWNER_PASSWORD"):
        command.upgrade(alembic_config(scratch_url), "0006")
    engine = create_engine(scratch_url)
    assert "users" not in inspect(engine).get_table_names()  # rolled back, nothing half-made
    engine.dispose()


def test_0006_in_a_dev_environment_makes_a_placeholder_admin_that_cannot_log_in(
    scratch_url, monkeypatch
):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("OWNER_EMAIL", "")
    monkeypatch.setenv("OWNER_PASSWORD", "")
    command.upgrade(alembic_config(scratch_url), "0006")
    engine = create_engine(scratch_url)
    [(email, is_admin, password_hash)] = _rows(
        engine, "select email, is_admin, password_hash from users"
    )
    assert is_admin and password_hash is None and email.endswith(".invalid")
    engine.dispose()


def test_0006_downgrade_drops_the_account_tables_and_columns(scratch_url):
    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "0006")
    command.downgrade(cfg, "0005")
    engine = create_engine(scratch_url)
    tables = set(inspect(engine).get_table_names())
    assert not tables & {"users", "auth_tokens", "oauth_flows", "waitlist_entries"}
    assert not tables & {"notifications", "mail_outbox", "user_settings"}
    assert "signups_open" not in {c["name"] for c in inspect(engine).get_columns("app_state")}
    command.upgrade(cfg, "head")
    engine.dispose()


def _seed_single_owner_data(engine):
    """What the live database holds at 0005: seed templates, a meeting, vocabulary."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into meetings (url, platform, native_id, template_id) "
                "select 'https://meet.google.com/abc-defg-hij', 'google_meet', 'abc-defg-hij', id "
                "from templates where name = 'General'"
            )
        )
        conn.execute(text("insert into vocabulary_terms (term) values ('Beyric'), ('Vexa')"))


def test_0007_gives_every_existing_row_to_the_admin(scratch_url):
    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "0005")
    engine = create_engine(scratch_url)
    _seed_single_owner_data(engine)
    command.upgrade(cfg, "head")

    [(admin_id,)] = _rows(engine, "select id from users where is_admin")
    for table, count in (("templates", 4), ("meetings", 1), ("vocabulary_terms", 2)):
        owners = _rows(engine, f"select distinct owner_user_id from {table}")  # noqa: S608
        assert owners == [(admin_id,)], table
        assert _rows(engine, f"select count(*) from {table}") == [(count,)]  # noqa: S608
        column = {c["name"]: c for c in inspect(engine).get_columns(table)}["owner_user_id"]
        assert column["nullable"] is False, table
    [(source, title, seconds)] = _rows(
        engine, "select source, title, recorded_seconds from meetings"
    )
    assert (source, title, seconds) == ("manual", None, None)
    engine.dispose()


def test_0007_points_the_admins_calendar_template_at_their_general_copy(scratch_url):
    command.upgrade(alembic_config(scratch_url), "head")
    engine = create_engine(scratch_url)
    rows = _rows(
        engine,
        "select t.name from user_settings s join templates t on t.id = s.calendar_template_id",
    )
    assert rows == [("General",)]
    engine.dispose()


def test_0007_names_are_unique_per_owner_not_globally(scratch_url):
    command.upgrade(alembic_config(scratch_url), "head")
    engine = create_engine(scratch_url)
    with engine.begin() as conn:
        conn.execute(text("insert into users (email) values ('second@example.test')"))
        # The second user may have a "General" template and the same vocabulary term.
        conn.execute(
            text(
                "insert into templates (owner_user_id, name, instructions) "
                "select id, 'General', 'x' from users where email = 'second@example.test'"
            )
        )
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.execute(  # but not twice for one owner
            text(
                "insert into templates (owner_user_id, name, instructions) "
                "select id, 'General', 'y' from users where email = 'second@example.test'"
            )
        )
    engine.dispose()


def test_0007_creates_usage_entries(scratch_url):
    command.upgrade(alembic_config(scratch_url), "head")
    engine = create_engine(scratch_url)
    columns = {c["name"] for c in inspect(engine).get_columns("usage_entries")}
    assert {"user_id", "email_hash", "meeting_id", "seconds", "counted_at"} <= columns
    engine.dispose()


def test_0007_downgrade_restores_the_single_owner_schema_and_data_survives(scratch_url):
    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "0005")
    engine = create_engine(scratch_url)
    _seed_single_owner_data(engine)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0005")

    assert _rows(engine, "select count(*) from meetings") == [(1,)]
    assert _rows(engine, "select count(*) from templates") == [(4,)]
    assert "owner_user_id" not in {c["name"] for c in inspect(engine).get_columns("templates")}
    assert "usage_entries" not in inspect(engine).get_table_names()
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:  # global unique is back
        conn.execute(text("insert into templates (name, instructions) values ('General', 'x')"))
    command.upgrade(cfg, "head")  # and up again
    engine.dispose()


def test_0007_downgrade_fails_when_two_users_share_a_template_name(scratch_url):
    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "head")
    engine = create_engine(scratch_url)
    with engine.begin() as conn:
        conn.execute(text("insert into users (email) values ('second@example.test')"))
        conn.execute(
            text(
                "insert into templates (owner_user_id, name, instructions) "
                "select id, 'General', 'x' from users where email = 'second@example.test'"
            )
        )
    with pytest.raises(sa.exc.IntegrityError):
        command.downgrade(cfg, "0006")
    engine.dispose()


def test_bot_state_seen_at_is_added_and_dropped(scratch_url):
    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "head")
    engine = create_engine(scratch_url)
    assert "bot_state_seen_at" in {c["name"] for c in inspect(engine).get_columns("meetings")}
    command.downgrade(cfg, "0007")
    assert "bot_state_seen_at" not in {c["name"] for c in inspect(engine).get_columns("meetings")}
    command.upgrade(cfg, "head")
    assert "bot_state_seen_at" in {c["name"] for c in inspect(engine).get_columns("meetings")}
    engine.dispose()
