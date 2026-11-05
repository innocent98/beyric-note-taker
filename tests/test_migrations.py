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


def test_0008_adds_the_pending_password_to_auth_tokens_and_drops_it(scratch_url):
    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "head")
    engine = create_engine(scratch_url)
    assert "pending_password_hash" in {
        c["name"] for c in inspect(engine).get_columns("auth_tokens")
    }
    command.downgrade(cfg, "0007")
    assert "pending_password_hash" not in {
        c["name"] for c in inspect(engine).get_columns("auth_tokens")
    }
    command.upgrade(cfg, "head")
    engine.dispose()


def test_0008_adds_deleted_at_to_users_and_drops_it(scratch_url):
    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "head")
    engine = create_engine(scratch_url)
    assert "deleted_at" in {c["name"] for c in inspect(engine).get_columns("users")}
    command.downgrade(cfg, "0007")
    assert "deleted_at" not in {c["name"] for c in inspect(engine).get_columns("users")}
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
    command.downgrade(cfg, "0008")
    assert "bot_state_seen_at" not in {c["name"] for c in inspect(engine).get_columns("meetings")}
    command.upgrade(cfg, "head")
    assert "bot_state_seen_at" in {c["name"] for c in inspect(engine).get_columns("meetings")}
    engine.dispose()


# ---- 0010 calendar ----

CALENDAR_TABLES = {"calendar_connections", "calendar_events", "scheduled_joins"}


def test_calendar_migration_round_trip_with_seeded_owner_data(scratch_url):
    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "0009")
    engine = create_engine(scratch_url)
    assert not CALENDAR_TABLES & set(inspect(engine).get_table_names())
    with engine.begin() as conn:  # an admin (made by 0006) and two meetings, as on the live DB
        conn.execute(
            text(
                "insert into meetings (owner_user_id, url, platform, native_id, template_id) "
                "select u.id, 'https://meet.google.com/' || n, 'google_meet', n, t.id "
                "from users u, templates t, (values ('abc-defg-hij'), ('klm-nopq-rst')) v(n) "
                "where u.is_admin and t.name = 'General'"
            )
        )

    command.upgrade(cfg, "head")
    assert CALENDAR_TABLES <= set(inspect(engine).get_table_names())
    assert _rows(engine, "select count(*) from meetings") == [(2,)]
    index_names = {i["name"] for i in inspect(engine).get_indexes("scheduled_joins")}
    assert {"ix_scheduled_joins_state_run_at", "uq_scheduled_joins_live_code"} <= index_names

    command.downgrade(cfg, "0009")
    assert not CALENDAR_TABLES & set(inspect(engine).get_table_names())
    assert _rows(engine, "select count(*) from meetings") == [(2,)]
    assert _rows(engine, "select count(*) from users where is_admin") == [(1,)]

    command.upgrade(cfg, "head")  # and up again, with the data still there
    assert CALENDAR_TABLES <= set(inspect(engine).get_table_names())
    engine.dispose()


def test_calendar_migration_is_the_revision_after_0009():
    script = ScriptDirectory.from_config(alembic_config(TEST_DATABASE_URL))
    assert script.get_revision("0010").down_revision == "0009"


# ---- 0012 last full sync ----


def test_last_full_sync_column_round_trip_keeps_connections(scratch_url):
    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "0011")
    engine = create_engine(scratch_url)
    with engine.begin() as conn:
        conn.execute(text("insert into users (id, email) values (9, 'c@example.test')"))
        conn.execute(
            text(
                "insert into calendar_connections (user_id, provider, sync_token)"
                " values (9, 'google', 'tok')"
            )
        )
    assert "last_full_sync_at" not in {
        c["name"] for c in inspect(engine).get_columns("calendar_connections")
    }

    command.upgrade(cfg, "head")
    assert "last_full_sync_at" in {
        c["name"] for c in inspect(engine).get_columns("calendar_connections")
    }
    # An existing connection has never been marked fully synced: its next sync is a full one.
    assert _rows(engine, "select sync_token, last_full_sync_at from calendar_connections") == [
        ("tok", None)
    ]

    command.downgrade(cfg, "0011")
    assert _rows(engine, "select sync_token from calendar_connections") == [("tok",)]
    command.upgrade(cfg, "head")
    engine.dispose()


# ---- 0011 AI workspace W1 ----


def test_ai_workspace_migration_round_trip(scratch_url):
    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "0010")
    engine = create_engine(scratch_url)
    with engine.begin() as conn:
        conn.execute(text("insert into users (id, email) values (7, 'w@example.test')"))
        conn.execute(
            text(
                "insert into meetings (id, owner_user_id, url, platform, native_id, template_id,"
                " status) values (5, 7, 'u', 'zoom', '1', 1, 'ready')"
            )
        )
    command.upgrade(cfg, "head")
    tables = set(inspect(engine).get_table_names())
    assert {"ai_runs", "decisions", "open_questions", "review_items"} <= tables
    with engine.connect() as conn:
        row = conn.execute(
            text("select extraction_status, extraction_attempts from meetings where id = 5")
        ).one()
    assert tuple(row) == (None, 0)  # nothing is backfilled by the migration

    with engine.begin() as conn:  # the checks and the cascade are the database's
        conn.execute(
            text(
                "insert into decisions (meeting_id, text, position, fingerprint)"
                " values (5, 'x', 0, 'f')"
            )
        )
        conn.execute(
            text("insert into ai_runs (meeting_id, kind, model) values (5, 'extract', 'm')")
        )
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.execute(text("update meetings set extraction_status = 'bogus' where id = 5"))
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "insert into decisions (meeting_id, text, position, fingerprint)"
                " values (5, 'again', 1, 'f')"
            )
        )
    with engine.begin() as conn:
        conn.execute(text("delete from meetings where id = 5"))
    with engine.connect() as conn:
        assert conn.execute(text("select count(*) from decisions")).scalar() == 0
        assert conn.execute(text("select meeting_id from ai_runs")).scalar() is None

    command.downgrade(cfg, "0010")
    after = set(inspect(engine).get_table_names())
    assert not {"ai_runs", "decisions", "open_questions", "review_items"} & after
    assert "extraction_status" not in {c["name"] for c in inspect(engine).get_columns("meetings")}
    command.upgrade(cfg, "head")  # and up again
    assert "review_items" in inspect(engine).get_table_names()
    engine.dispose()


def test_ai_workspace_migration_follows_0010():
    script = ScriptDirectory.from_config(alembic_config(TEST_DATABASE_URL))
    assert script.get_revision("0011").down_revision == "0010"


def test_0013_adds_suppressions_and_the_skipped_status_and_downgrades_cleanly(scratch_url):
    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "head")
    engine = create_engine(scratch_url)
    columns = {c["name"] for c in inspect(engine).get_columns("mail_suppressions")}
    assert columns == {"email_hash", "reason", "created_at"}
    with engine.begin() as conn:
        conn.execute(
            text("insert into mail_suppressions (email_hash, reason) values (:h, 'bounce')"),
            {"h": "a" * 64},
        )
        conn.execute(
            text(
                "insert into mail_outbox (to_email, template, status) "
                "values ('a@example.test', 'notes_ready', 'skipped')"
            )
        )
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.execute(
            text("insert into mail_suppressions (email_hash, reason) values ('b', 'spam')")
        )
    notes_ready = (
        "insert into mail_outbox (to_email, template, payload) "
        "values ('a@example.test', 'notes_ready', '{\"meeting_id\": 5}')"
    )
    with engine.begin() as conn:
        conn.execute(text(notes_ready))
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.execute(text(notes_ready))  # one notes_ready mail per meeting
    command.downgrade(cfg, "0012")
    assert "mail_suppressions" not in inspect(engine).get_table_names()
    statuses = _rows(engine, "select status from mail_outbox")
    assert sorted(statuses) == [("failed",), ("pending",)]  # the skipped row became failed
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "insert into mail_outbox (to_email, template, status) "
                "values ('b@example.test', 'x', 'skipped')"
            )
        )
    command.upgrade(cfg, "head")  # and up again
    engine.dispose()


def test_template_soft_delete_frees_the_name_and_downgrades_cleanly(scratch_url):
    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "head")
    engine = create_engine(scratch_url)
    with engine.begin() as conn:
        owner = conn.execute(text("select id from users order by id limit 1")).scalar_one()
        # "General" is seeded live for the owner: a deleted row may share its name, a live may not
        conn.execute(
            text(
                "insert into templates (owner_user_id, name, instructions, deleted_at)"
                " values (:o, 'General', 'old', now())"
            ),
            {"o": owner},
        )
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "insert into templates (owner_user_id, name, instructions)"
                    " values (:o, 'General', 'again')"
                ),
                {"o": owner},
            )
    with engine.connect() as conn:
        live = conn.execute(
            text("select count(*) from templates where deleted_at is null and name = 'General'")
        ).scalar_one()
    assert live == 1

    command.downgrade(cfg, "0014")
    with engine.connect() as conn:
        names = [r[0] for r in conn.execute(text("select name from templates order by id"))]
        constraint = conn.execute(
            text("select count(*) from pg_constraint where conname = 'uq_templates_owner_name'")
        ).scalar_one()
    assert "General" in names and any(n.startswith("General (deleted ") for n in names)
    assert constraint == 1
    assert "deleted_at" not in {c["name"] for c in inspect(engine).get_columns("templates")}
    command.upgrade(cfg, "head")  # and back up again
    assert "deleted_at" in {c["name"] for c in inspect(engine).get_columns("templates")}
    engine.dispose()


def test_0014_adds_translations_and_the_default_language_and_downgrades_cleanly(scratch_url):
    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "head")
    engine = create_engine(scratch_url)
    columns = {c["name"] for c in inspect(engine).get_columns("meeting_translations")}
    assert columns == {
        "id",
        "meeting_id",
        "language",
        "status",
        "notes_md",
        "transcript",
        "source_hash",
        "model",
        "failure_code",
        "attempts",
        "started_at",
        "created_at",
        "updated_at",
    }
    with engine.begin() as conn:
        user = conn.execute(
            text("insert into users (email) values ('m@example.test') returning id")
        ).scalar()
        template = conn.execute(
            text(
                "insert into templates (owner_user_id, name, instructions) "
                "values (:u, 'T', 'x') returning id"
            ),
            {"u": user},
        ).scalar()
        meeting = conn.execute(
            text(
                "insert into meetings (owner_user_id, url, platform, native_id, template_id) "
                "values (:u, 'u', 'zoom', '1', :t) returning id"
            ),
            {"u": user, "t": template},
        ).scalar()
        insert = (
            "insert into meeting_translations (meeting_id, language, source_hash) "
            "values (:m, :l, 'h')"
        )
        conn.execute(text(insert), {"m": meeting, "l": "fr"})
        conn.execute(text("insert into user_settings (user_id) values (:u)"), {"u": user})
    row = _rows(engine, "select status, attempts from meeting_translations")
    assert row == [("translating", 0)]
    # the owner account migration 0006 creates and the one above both start on the original
    assert {r[0] for r in _rows(engine, "select default_language from user_settings")} == {"en"}
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.execute(text(insert), {"m": meeting, "l": "fr"})  # one per meeting and language
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.execute(text(insert), {"m": meeting, "l": "yo"})  # only the designed languages
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.execute(text("update user_settings set default_language = 'de'"))
    with engine.begin() as conn:
        conn.execute(text("delete from meetings where id = :m"), {"m": meeting})
    assert _rows(engine, "select count(*) from meeting_translations") == [(0,)]  # cascade
    command.downgrade(cfg, "0013")
    assert "meeting_translations" not in inspect(engine).get_table_names()
    assert "default_language" not in {
        c["name"] for c in inspect(engine).get_columns("user_settings")
    }
    command.upgrade(cfg, "head")  # and back up again
    assert "meeting_translations" in inspect(engine).get_table_names()
    assert "user_id" in {c["name"] for c in inspect(engine).get_columns("ai_runs")}
    engine.dispose()


def test_0016_adds_rate_hits_and_the_signup_origin_and_downgrades_cleanly(scratch_url):
    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "0015")
    engine = create_engine(scratch_url)
    with engine.begin() as conn:
        conn.execute(
            text("insert into users (email, status) values ('old@example.test', 'active')")
        )
    command.upgrade(cfg, "0016")
    assert {c["name"] for c in inspect(engine).get_columns("rate_hits")} == {
        "id",
        "bucket",
        "key",
        "at",
    }
    assert "signup_origin" in {c["name"] for c in inspect(engine).get_columns("users")}
    with engine.begin() as conn:
        conn.execute(text("insert into rate_hits (bucket, key, at) values ('b', 'k', 1.5)"))
        conn.execute(
            text("update users set signup_origin = :o where email = 'old@example.test'"),
            {"o": "f" * 32},
        )
    assert _rows(engine, "select email from users where signup_origin is not null") == [
        ("old@example.test",)
    ]
    command.downgrade(cfg, "0015")
    assert "rate_hits" not in inspect(engine).get_table_names()
    assert "signup_origin" not in {c["name"] for c in inspect(engine).get_columns("users")}
    assert _rows(engine, "select email from users where email = 'old@example.test'") != []
    command.upgrade(cfg, "head")  # and up again
    engine.dispose()


# ---- 0017 auto-join markers ----


def _app_state_columns(engine) -> set[str]:
    rows = _rows(
        engine, "select column_name from information_schema.columns where table_name = 'app_state'"
    )
    return {row[0] for row in rows}


def test_autojoin_marker_columns_round_trip_and_start_unset(scratch_url):
    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "0016")
    engine = create_engine(scratch_url)
    assert "autojoin_started_at" not in _app_state_columns(engine)
    command.upgrade(cfg, "head")
    assert {"autojoin_started_at", "autojoin_paused_since"} <= _app_state_columns(engine)
    assert _rows(engine, "select autojoin_started_at, autojoin_paused_since from app_state") == [
        (None, None)
    ]
    command.downgrade(cfg, "0016")
    assert "autojoin_started_at" not in _app_state_columns(engine)
    engine.dispose()


# ---- 0018 time zone ----


def test_time_zone_column_round_trips_and_existing_rows_stay_null(scratch_url):
    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "0017")
    engine = create_engine(scratch_url)

    def columns() -> set[str]:
        return {c["name"] for c in inspect(engine).get_columns("user_settings")}

    assert "time_zone" not in columns()
    command.upgrade(cfg, "head")
    assert "time_zone" in columns()
    assert _rows(engine, "select count(*) from user_settings where time_zone is not null") == [(0,)]
    command.downgrade(cfg, "0017")
    assert "time_zone" not in columns()
    command.upgrade(cfg, "head")
    engine.dispose()


# ---- 0019 teams ----


def test_0019_gives_every_live_account_a_team_of_one_and_shares_nothing(scratch_url):
    import importlib.util
    from pathlib import Path

    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "0018")
    engine = create_engine(scratch_url)
    with engine.begin() as conn:
        for email, status in [
            ("a@example.test", "active"),
            ("b@example.test", "suspended"),
            ("gone@example.test", "deleted"),
        ]:
            conn.execute(
                text("insert into users (email, status) values (:e, :s)"), {"e": email, "s": status}
            )
        conn.execute(
            text(
                "insert into meetings (owner_user_id, url, platform, native_id, template_id) "
                "select u.id, 'u', 'google_meet', 'abc', t.id from users u, templates t "
                "where u.email = 'a@example.test' limit 1"
            )
        )
    command.upgrade(cfg, "head")
    members = _rows(
        engine,
        "select u.email, m.role, t.owner_user_id = u.id, t.name from team_members m "
        "join users u on u.id = m.user_id join teams t on t.id = m.team_id order by u.email",
    )
    assert members == [
        ("a@example.test", "owner", True, "My team"),
        ("b@example.test", "owner", True, "My team"),
        (OWNER_EMAIL, "owner", True, "My team"),  # migration 0006's admin is covered too
    ]  # the deleted account gets none
    assert _rows(engine, "select count(*) from meetings where shared_team_id is not null") == [(0,)]

    # Safe to run again: the same statements add nothing.
    path = Path(__file__).parent.parent / "alembic" / "versions" / "0019_teams.py"
    spec = importlib.util.spec_from_file_location("m0019", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as conn:
        conn.execute(text(module.BACKFILL_TEAMS))
        conn.execute(text(module.BACKFILL_MEMBERS))
    assert _rows(engine, "select count(*) from teams") == [(3,)]
    assert _rows(engine, "select count(*) from team_members") == [(3,)]

    # A half-finished earlier run (a team with no membership) is mended, not doubled.
    with engine.begin() as conn:
        conn.execute(
            text(
                "delete from team_members where user_id = "
                "(select id from users where email = 'a@example.test')"
            )
        )
        conn.execute(text(module.BACKFILL_TEAMS))
        conn.execute(text(module.BACKFILL_MEMBERS))
    assert _rows(engine, "select count(*) from teams") == [(3,)]
    assert _rows(engine, "select count(*) from team_members") == [(3,)]

    command.downgrade(cfg, "0018")
    tables = set(inspect(engine).get_table_names())
    assert not {"teams", "team_members"} & tables
    assert "shared_team_id" not in {c["name"] for c in inspect(engine).get_columns("meetings")}
    assert _rows(engine, "select count(*) from meetings") == [(1,)]
    command.upgrade(cfg, "head")
    engine.dispose()


# ---- 0024 tasks ----


def test_0024_adds_tasks_with_the_cascades_and_downgrades_cleanly(scratch_url):
    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "0023")
    engine = create_engine(scratch_url)
    with engine.begin() as conn:
        conn.execute(text("insert into users (email, status) values ('a@example.test', 'active')"))
        conn.execute(
            text(
                "insert into user_settings (user_id) "
                "select id from users where email = 'a@example.test'"
            )
        )
        conn.execute(
            text(
                "insert into meetings (owner_user_id, url, platform, native_id, template_id) "
                "select u.id, 'u', 'google_meet', 'abc', t.id from users u, templates t "
                "where u.email = 'a@example.test' limit 1"
            )
        )
    command.upgrade(cfg, "head")
    # Existing rows get the defaults: both switches on, no meeting quiet.
    flags = _rows(engine, "select auto_create_tasks, email_assignees from user_settings")
    assert flags and set(flags) == {(True, True)}
    assert _rows(engine, "select extraction_quiet from meetings") == [(False,)]
    with engine.begin() as conn:
        team = conn.execute(text("select id from teams limit 1")).scalar()
        meeting = conn.execute(text("select id from meetings")).scalar()
        for title in ("one", "two"):
            conn.execute(
                text(
                    "insert into tasks (team_id, meeting_id, title, source, ai_fingerprint) "
                    "values (:t, :m, :title, 'ai', :fp)"
                ),
                {"t": team, "m": meeting, "title": title, "fp": title},
            )
        conn.execute(
            text("insert into tasks (team_id, title, source) values (:t, 'by hand', 'manual')"),
            {"t": team},
        )
    # One task per bullet of a meeting; manual tasks (no meeting) are not constrained by it.
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "insert into tasks (team_id, meeting_id, title, source, ai_fingerprint) "
                "values (:t, :m, 'again', 'ai', 'one')"
            ),
            {"t": team, "m": meeting},
        )
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.execute(
            text("insert into tasks (team_id, title, status) values (:t, 'x', 'blocked')"),
            {"t": team},
        )
    # One "New tasks assigned" mail per person per meeting; another person or meeting is fine.
    insert = (
        "insert into mail_outbox (to_email, template, payload) "
        "values ('x@example.test', 'tasks_assigned', cast(:p as jsonb))"
    )
    with engine.begin() as conn:
        conn.execute(text(insert), {"p": '{"meeting_id": 1, "user_id": 2}'})
        conn.execute(text(insert), {"p": '{"meeting_id": 1, "user_id": 3}'})
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.execute(text(insert), {"p": '{"meeting_id": 1, "user_id": 2}'})
    # Deleting the meeting deletes its tasks (spec 3.3); the manual one stays.
    with engine.begin() as conn:
        conn.execute(text("delete from meetings where id = :m"), {"m": meeting})
    assert _rows(engine, "select title from tasks") == [("by hand",)]

    command.downgrade(cfg, "0023")
    assert "tasks" not in set(inspect(engine).get_table_names())
    assert "extraction_quiet" not in {c["name"] for c in inspect(engine).get_columns("meetings")}
    assert not {"auto_create_tasks", "email_assignees"} & {
        c["name"] for c in inspect(engine).get_columns("user_settings")
    }
    command.upgrade(cfg, "head")
    engine.dispose()


def test_0024_follows_0023():
    script = ScriptDirectory.from_config(alembic_config(TEST_DATABASE_URL))
    assert script.get_revision("0024").down_revision == "0023"


# ---- 0021 notifications cascade with the meeting ----


def _notification_fk_rule(engine) -> str:
    return _rows(
        engine,
        "select confdeltype::text from pg_constraint "
        "where conname = 'notifications_meeting_id_fkey'",
    )[0][0]


def test_0021_makes_a_notification_go_with_its_meeting_and_downgrades(scratch_url):
    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "0019")
    engine = create_engine(scratch_url)
    assert _notification_fk_rule(engine) == "n"  # SET NULL
    command.upgrade(cfg, "0021")
    assert _notification_fk_rule(engine) == "c"  # CASCADE
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into meetings (owner_user_id, url, platform, native_id, template_id) "
                "select u.id, 'u', 'google_meet', 'abc', t.id from users u, templates t limit 1"
            )
        )
        conn.execute(
            text(
                "insert into notifications (user_id, kind, title, meeting_id, dedupe_key) "
                "select owner_user_id, 'k', 't', id, 'd' from meetings"
            )
        )
        conn.execute(text("delete from meetings"))
    assert _rows(engine, "select count(*) from notifications") == [(0,)]
    command.downgrade(cfg, "0019")
    assert _notification_fk_rule(engine) == "n"
    command.upgrade(cfg, "head")
    assert _notification_fk_rule(engine) == "c"
    engine.dispose()


def test_0023_lets_the_ledger_hold_notes_and_label_calls_and_downgrades_cleanly(scratch_url):
    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "0023")
    engine = create_engine(scratch_url)
    with engine.begin() as conn:
        for kind in ("extract", "notes", "label"):
            conn.execute(
                text("insert into ai_runs (kind, model) values (:k, 'gpt-6-luna')"), {"k": kind}
            )
    command.downgrade(cfg, "0021")
    assert _rows(engine, "select kind from ai_runs order by id") == [("extract",)]
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.execute(text("insert into ai_runs (kind, model) values ('notes', 'm')"))
    engine.dispose()


def test_0025_lets_the_ledger_hold_transcription_with_audio_seconds_and_downgrades_cleanly(
    scratch_url,
):
    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "0025")
    engine = create_engine(scratch_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into ai_runs (kind, model, audio_seconds) "
                "values ('transcribe', 'gpt-transcribe', 1800)"
            )
        )
        conn.execute(text("insert into ai_runs (kind, model) values ('notes', 'm')"))
    assert _rows(engine, "select kind, audio_seconds from ai_runs order by id") == [
        ("transcribe", 1800),
        ("notes", None),
    ]
    command.downgrade(cfg, "0024")
    assert _rows(engine, "select kind from ai_runs order by id") == [("notes",)]
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.execute(text("insert into ai_runs (kind, model) values ('transcribe', 'm')"))
    command.upgrade(cfg, "head")
    engine.dispose()


def test_0025_follows_0024():
    script = ScriptDirectory.from_config(alembic_config("postgresql://unused"))
    assert script.get_revision("0025").down_revision == "0024"
    assert script.get_heads() == ["0025"]
