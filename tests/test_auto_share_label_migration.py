"""Migration 0065: `meetings.shared_by_auto_rule`. Runs the real migration on the scratch database,
like the other migration tests."""

from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from tests import test_migrations as migrations

alembic_config = migrations.alembic_config
owner_env = migrations.owner_env
scratch_url = migrations.scratch_url


def column(engine):
    return {c["name"]: c for c in inspect(engine).get_columns("meetings")}.get(
        "shared_by_auto_rule"
    )


def test_0065_follows_0064(scratch_url):
    script = ScriptDirectory.from_config(alembic_config(scratch_url))
    assert script.get_revision("0065").down_revision == "0064"


def test_0065_adds_the_column_false_and_the_round_trip_removes_it(scratch_url):
    cfg = alembic_config(scratch_url)
    engine = create_engine(scratch_url)
    command.upgrade(cfg, "0064")
    assert column(engine) is None
    command.upgrade(cfg, "0065")
    col = column(engine)
    assert col is not None and col["nullable"] is False
    command.downgrade(cfg, "0064")
    assert column(engine) is None
    command.upgrade(cfg, "head")
    assert column(engine) is not None
    engine.dispose()
