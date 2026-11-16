"""Migration 0051 (billing stage S5): the two tables, the round trip and the downgrade guard.

Runs the real migration on the scratch database, like `test_billing_migration.py` does for 0050."""

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests import test_migrations as migrations

alembic_config = migrations.alembic_config
owner_env = migrations.owner_env
scratch_url = migrations.scratch_url

TABLES = {"billing_profiles", "billing_country_changes"}


def test_0051_follows_0050(scratch_url):
    script = ScriptDirectory.from_config(alembic_config(scratch_url))
    assert script.get_revision("0051").down_revision == "0050"


def test_the_round_trip_creates_and_drops_the_two_tables(scratch_url):
    cfg = alembic_config(scratch_url)
    engine = create_engine(scratch_url)
    command.upgrade(cfg, "0051")
    assert TABLES <= set(inspect(engine).get_table_names())
    command.downgrade(cfg, "0050")
    assert not TABLES & set(inspect(engine).get_table_names())
    command.upgrade(cfg, "0051")
    assert TABLES <= set(inspect(engine).get_table_names())
    engine.dispose()


def user_id(conn) -> int:
    return conn.execute(
        text("select id from users order by id limit 1")  # the owner migration 0006 makes
    ).scalar()


def test_the_constraints_hold_in_the_database(scratch_url):
    cfg = alembic_config(scratch_url)
    engine = create_engine(scratch_url)
    command.upgrade(cfg, "0051")
    with engine.begin() as conn:
        uid = user_id(conn)
        conn.execute(
            text("insert into billing_profiles (user_id, billing_country) values (:u, 'NG')"),
            {"u": uid},
        )
    bad = [
        "insert into billing_profiles (user_id) values (:u)",  # one row per person
        "insert into billing_profiles (user_id, billing_country) values (-1, 'ng')",
        "insert into billing_profiles (user_id, pending_billing_country) values (-2, 'US')",
        "insert into billing_country_changes (user_id, new_country, effective_at) "
        "values (:u, 'ng', now())",
        "insert into billing_country_changes (user_id, old_country, new_country, effective_at) "
        "values (:u, '1G', 'US', now())",
    ]
    for statement in bad:
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(text(statement), {"u": uid})
    engine.dispose()


def test_the_downgrade_refuses_while_a_profile_or_a_change_exists_and_changes_nothing(
    scratch_url,
):
    cfg = alembic_config(scratch_url)
    engine = create_engine(scratch_url)
    command.upgrade(cfg, "0051")
    with engine.begin() as conn:
        uid = user_id(conn)
        conn.execute(text("insert into billing_profiles (user_id) values (:u)"), {"u": uid})
        conn.execute(
            text(
                "insert into billing_country_changes (user_id, new_country, effective_at) "
                "values (:u, 'NG', now())"
            ),
            {"u": uid},
        )
    with pytest.raises(RuntimeError, match=r"Cannot downgrade 0051: 1 billing profile\(s\) and 1"):
        command.downgrade(cfg, "0050")
    with engine.connect() as conn:
        assert conn.execute(text("select version_num from alembic_version")).all() == [("0051",)]
    assert TABLES <= set(inspect(engine).get_table_names())
    with engine.begin() as conn:
        conn.execute(text("delete from billing_country_changes"))
        conn.execute(text("delete from billing_profiles"))
    command.downgrade(cfg, "0050")  # once they are gone the migration goes down
    command.upgrade(cfg, "head")
    engine.dispose()


def test_deleting_the_account_removes_both_rows(scratch_url):
    cfg = alembic_config(scratch_url)
    engine = create_engine(scratch_url)
    command.upgrade(cfg, "0051")
    with engine.begin() as conn:
        conn.execute(
            text("insert into users (email, status) values ('gone@example.test', 'active')")
        )
        uid = conn.execute(text("select id from users where email = 'gone@example.test'")).scalar()
        conn.execute(text("insert into billing_profiles (user_id) values (:u)"), {"u": uid})
        conn.execute(
            text(
                "insert into billing_country_changes (user_id, new_country, effective_at) "
                "values (:u, 'NG', now())"
            ),
            {"u": uid},
        )
        conn.execute(text("delete from users where id = :u"), {"u": uid})
        for table in TABLES:
            assert conn.execute(text(f"select count(*) from {table}")).scalar() == 0
    engine.dispose()
