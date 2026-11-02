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
