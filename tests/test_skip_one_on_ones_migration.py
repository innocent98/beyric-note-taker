"""Migration 0056: `user_settings.skip_one_on_ones`. Runs the real migration on the scratch
database, like the other migration tests."""

from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from tests import test_migrations as migrations

alembic_config = migrations.alembic_config
owner_env = migrations.owner_env
scratch_url = migrations.scratch_url


def column(engine):
    columns = {c["name"]: c for c in inspect(engine).get_columns("user_settings")}
    return columns.get("skip_one_on_ones")


def test_0056_is_the_only_head_and_follows_0055(scratch_url):
    script = ScriptDirectory.from_config(alembic_config(scratch_url))
    assert len(script.get_heads()) == 1
    assert script.get_revision("0056").down_revision == "0055"


def test_0056_adds_the_column_false_for_everyone_and_the_round_trip_removes_it(scratch_url):
    cfg = alembic_config(scratch_url)
    engine = create_engine(scratch_url)
    command.upgrade(cfg, "0055")
    assert column(engine) is None
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into users (email, password_hash, status) "
                "values ('old@example.test', 'x', 'active')"
            )
        )
        uid = conn.execute(text("select id from users where email = 'old@example.test'")).scalar()
        conn.execute(text("insert into user_settings (user_id) values (:u)"), {"u": uid})

    command.upgrade(cfg, "0056")
    col = column(engine)
    assert col is not None and col["nullable"] is False
    with engine.connect() as conn:
        # Nothing changes for a person who was there before ...
        values = conn.execute(text("select skip_one_on_ones from user_settings")).scalars().all()
        assert values and set(values) == {False}
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into users (email, password_hash, status) "
                "values ('new@example.test', 'x', 'active')"
            )
        )
        uid2 = conn.execute(text("select id from users where email = 'new@example.test'")).scalar()
        conn.execute(text("insert into user_settings (user_id) values (:u)"), {"u": uid2})
    with engine.connect() as conn:
        # ... and a new row starts unticked too.
        values = conn.execute(text("select skip_one_on_ones from user_settings")).scalars().all()
        assert len(values) >= 2 and set(values) == {False}

    command.downgrade(cfg, "0055")
    assert column(engine) is None
    command.upgrade(cfg, "head")
    assert column(engine) is not None
    engine.dispose()
