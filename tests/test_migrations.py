import os

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from tests.conftest import TEST_DATABASE_URL

SCRATCH_DB = "notetaker_migration_scratch"


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
