"""Migration 0064 (calendar per workspace): a person may hold one connection per provider in each
workspace, and an OAuth flow remembers the workspace that started it.

Runs the real migration on the scratch database like the other migration tests. The rules under test
(workspaces design, section 4):

* before it, one connection per (person, provider); after it, one per (person, workspace, provider);
* the same account in two workspaces is two rows, and a second row of the same provider in the SAME
  workspace is still refused by the database;
* a flow that began before the migration has no workspace (the callback then uses the connection's
  default, the person's default workspace);
* downgrade keeps the OLDEST connection of a person and provider (the one they had before) and takes
  the extra ones out, with their events and joins (they cascade).
"""

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests import test_migrations as migrations
from tests.test_workspaces_migration import member, one, team, user

alembic_config = migrations.alembic_config
owner_env = migrations.owner_env
scratch_url = migrations.scratch_url


def test_0064_follows_0063_and_is_the_only_head(scratch_url):
    script = ScriptDirectory.from_config(alembic_config(scratch_url))
    assert len(script.get_heads()) == 1
    assert script.get_revision("0064").down_revision == "0063"


def connection(conn, uid, tid, provider="google"):
    return one(
        conn,
        "insert into calendar_connections (user_id, team_id, provider) "
        "values (:u, :t, :p) returning id",
        u=uid,
        t=tid,
        p=provider,
    )


def test_0064_lets_one_person_hold_a_provider_in_several_workspaces(scratch_url):
    cfg = alembic_config(scratch_url)
    engine = create_engine(scratch_url)
    command.upgrade(cfg, "0063")
    with engine.begin() as conn:
        ada = user(conn, "ada@example.test")
        home = team(conn, ada, "Ada", personal=True)
        luran = team(conn, ada, "Luran")
        connection(conn, ada, home)
        with pytest.raises(IntegrityError):  # before: one per person and provider
            with conn.begin_nested():
                connection(conn, ada, luran)

    command.upgrade(cfg, "0064")

    with engine.begin() as conn:
        ada = one(conn, "select id from users where email = 'ada@example.test'")
        luran = one(conn, "select id from teams where name = 'Luran'")
        home = one(conn, "select id from teams where name = 'Ada'")
        first = one(conn, "select id from calendar_connections where team_id = :t", t=home)
        connection(conn, ada, luran)  # after: the same provider in another workspace
        connection(conn, ada, luran, provider="microsoft")  # and the other provider beside it
        with pytest.raises(IntegrityError):  # but not twice in one workspace
            with conn.begin_nested():
                connection(conn, ada, luran)
    insp = inspect(engine)
    names = {c["name"] for c in insp.get_unique_constraints("calendar_connections")}
    assert "uq_calendar_connections_user_team_provider" in names
    assert "uq_calendar_connections_user_provider" not in names
    flow_columns = {c["name"]: c for c in insp.get_columns("oauth_flows")}
    assert flow_columns["team_id"]["nullable"] is True
    assert first  # the connection that existed before is untouched
    engine.dispose()


def test_0064_downgrade_keeps_the_oldest_connection_and_round_trips(scratch_url):
    cfg = alembic_config(scratch_url)
    engine = create_engine(scratch_url)
    command.upgrade(cfg, "0064")
    with engine.begin() as conn:
        ada = user(conn, "ada@example.test")
        bob = user(conn, "bob@example.test")
        home = team(conn, ada, "Ada", personal=True)
        luran = team(conn, ada, "Luran")
        bob_home = team(conn, bob, "Bob", personal=True)
        member(conn, luran, bob)
        oldest = connection(conn, ada, home)
        extra = connection(conn, ada, luran)
        other = connection(conn, bob, bob_home)
        conn.execute(
            text(
                "insert into calendar_events (user_id, connection_id, source_event_id, title, "
                "starts_at, ends_at) values (:u, :c, 'e1', 't', now(), now())"
            ),
            {"u": ada, "c": extra},
        )
    command.downgrade(cfg, "0063")
    with engine.connect() as conn:
        rows = {r[0] for r in conn.execute(text("select id from calendar_connections"))}
        assert rows == {oldest, other}  # the extra one went
        assert one(conn, "select count(*) from calendar_events") == 0  # and its events with it
    assert "team_id" not in {c["name"] for c in inspect(engine).get_columns("oauth_flows")}
    command.upgrade(cfg, "head")
    engine.dispose()
