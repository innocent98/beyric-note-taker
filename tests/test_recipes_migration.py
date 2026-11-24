"""Migration 0060 (placeholder number, renumbered when the integration branch rebases onto main):
the recipes tables and the `recipe` ledger kind. Runs the real migration on the scratch database,
like the other migration tests."""

from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from tests import test_migrations as migrations

alembic_config = migrations.alembic_config
owner_env = migrations.owner_env
scratch_url = migrations.scratch_url

REVISION = "0060"
PREVIOUS = "0056"
TABLES = {"documents", "document_meetings", "document_shares", "recipes", "team_briefs"}


def test_0060_is_the_only_head_and_follows_0056(scratch_url):
    script = ScriptDirectory.from_config(alembic_config(scratch_url))
    assert len(script.get_heads()) == 1
    assert script.get_revision(REVISION).down_revision == PREVIOUS


def seed(conn):
    conn.execute(
        text(
            "insert into users (email, password_hash, status) "
            "values ('old@example.test', 'x', 'active')"
        )
    )
    uid = conn.execute(text("select id from users where email = 'old@example.test'")).scalar()
    conn.execute(
        text(
            "insert into meetings (owner_user_id, url, platform, native_id, template_id) "
            "values (:u, 'u', 'zoom', '1', (select min(id) from templates))"
        ),
        {"u": uid},
    )
    mid = conn.execute(text("select max(id) from meetings")).scalar()
    return uid, mid


def test_upgrade_adds_the_tables_and_the_kind_and_downgrade_removes_them(scratch_url):
    cfg = alembic_config(scratch_url)
    engine = create_engine(scratch_url)
    command.upgrade(cfg, PREVIOUS)
    assert not TABLES & set(inspect(engine).get_table_names())
    with engine.begin() as conn:
        uid, mid = seed(conn)

    command.upgrade(cfg, REVISION)
    assert TABLES <= set(inspect(engine).get_table_names())
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into ai_runs (meeting_id, user_id, kind, model, status) "
                "values (:m, :u, 'recipe', 'm', 'ok')"
            ),
            {"m": mid, "u": uid},
        )
        conn.execute(
            text(
                "insert into documents (owner_user_id, title, output_type) "
                "values (:u, 'Follow-up', 'email')"
            ),
            {"u": uid},
        )
        did = conn.execute(text("select max(id) from documents")).scalar()
        conn.execute(
            text("insert into document_meetings (document_id, meeting_id) values (:d, :m)"),
            {"d": did, "m": mid},
        )
    with engine.connect() as conn:
        row = conn.execute(
            text("select status, edited, attempts, blocks from documents where id = :d"),
            {"d": did},
        ).one()
        assert (row.status, row.edited, row.attempts, row.blocks) == ("writing", False, 0, [])

    # Deleting the meeting removes the link, never the draft (the service deletes single-meeting
    # drafts itself, S7).
    with engine.begin() as conn:
        conn.execute(text("delete from ai_runs where meeting_id = :m"), {"m": mid})
        conn.execute(text("delete from meetings where id = :m"), {"m": mid})
    with engine.connect() as conn:
        assert conn.execute(text("select count(*) from document_meetings")).scalar() == 0
        assert conn.execute(text("select count(*) from documents")).scalar() == 1

    # The downgrade deletes `recipe` ledger rows first: the old constraint would refuse them.
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into ai_runs (user_id, kind, model, status) "
                "values (:u, 'recipe', 'm', 'ok')"
            ),
            {"u": uid},
        )
    command.downgrade(cfg, PREVIOUS)
    assert not TABLES & set(inspect(engine).get_table_names())
    with engine.connect() as conn:
        assert (
            conn.execute(text("select count(*) from ai_runs where kind = 'recipe'")).scalar() == 0
        )
    command.upgrade(cfg, "head")
    assert TABLES <= set(inspect(engine).get_table_names())
    engine.dispose()


def test_the_checks_refuse_a_bad_status_type_scope_and_a_second_share(scratch_url):
    import pytest
    from sqlalchemy.exc import IntegrityError

    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "head")
    engine = create_engine(scratch_url)
    with engine.begin() as conn:
        uid, _ = seed(conn)
        conn.execute(text("insert into teams (owner_user_id) values (:u)"), {"u": uid})
        tid = conn.execute(text("select max(id) from teams")).scalar()
        conn.execute(
            text(
                "insert into documents (owner_user_id, title, output_type) "
                "values (:u, 't', 'email')"
            ),
            {"u": uid},
        )
        did = conn.execute(text("select max(id) from documents")).scalar()
        conn.execute(
            text(
                "insert into document_shares (document_id, team_id, shared_by_user_id) "
                "values (:d, :t, :u)"
            ),
            {"d": did, "t": tid, "u": uid},
        )
    bad = {
        "status": "insert into documents (owner_user_id, title, output_type, status) "
        "values (:u, 't', 'email', 'bogus')",
        "output_type": "insert into documents (owner_user_id, title, output_type) "
        "values (:u, 't', 'bogus')",
        "scope": "insert into recipes (owner_user_id, name, instruction, output_type, scope) "
        "values (:u, 'r', 'i', 'email', 'bogus')",
        "share": "insert into document_shares (document_id, team_id, shared_by_user_id) "
        f"values ({did}, {tid}, :u)",
    }
    for sql in bad.values():
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(text(sql), {"u": uid})
    engine.dispose()
