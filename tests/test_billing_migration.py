"""Migration 0050 (billing stage S4): the three tables, the round trip and the downgrade guard.

Runs the real migration on the scratch database, like `test_hours_grants.py` does for 0049."""

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from tests import test_migrations as migrations

# The scratch database, the owner environment (an autouse fixture) and the alembic config are the
# migration tests' own, bound here by assignment so pytest finds the fixtures in this module.
alembic_config = migrations.alembic_config
owner_env = migrations.owner_env
scratch_url = migrations.scratch_url

TABLES = {"payments", "payment_methods", "billing_events"}


def test_0050_follows_0049(scratch_url):
    script = ScriptDirectory.from_config(alembic_config(scratch_url))
    assert script.get_revision("0050").down_revision == "0049"


def test_the_round_trip_creates_and_drops_the_three_tables(scratch_url):
    cfg = alembic_config(scratch_url)
    engine = create_engine(scratch_url)
    command.upgrade(cfg, "0050")
    assert TABLES <= set(inspect(engine).get_table_names())
    command.downgrade(cfg, "0049")
    assert not TABLES & set(inspect(engine).get_table_names())
    command.upgrade(cfg, "0050")
    assert TABLES <= set(inspect(engine).get_table_names())
    engine.dispose()


def test_the_downgrade_refuses_while_a_payment_exists_and_changes_nothing(scratch_url):
    cfg = alembic_config(scratch_url)
    engine = create_engine(scratch_url)
    command.upgrade(cfg, "0050")
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into payments (provider, reference, kind, currency, amount_minor) "
                "values ('fake', 'bn-1-x', 'plan', 'USD', 100)"
            )
        )
    with pytest.raises(RuntimeError, match=r"Cannot downgrade 0050: 1 payment"):
        command.downgrade(cfg, "0049")
    with engine.connect() as conn:
        assert conn.execute(text("select version_num from alembic_version")).all() == [("0050",)]
    assert TABLES <= set(inspect(engine).get_table_names())
    with engine.begin() as conn:
        conn.execute(text("delete from payments"))
    command.downgrade(cfg, "0049")  # once it is gone the migration goes down
    command.upgrade(cfg, "head")
    engine.dispose()
