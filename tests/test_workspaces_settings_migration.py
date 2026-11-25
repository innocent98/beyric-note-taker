"""Migration 0071 (workspaces settings, slice W2) on a seeded dataset.

The cutover (0070) copied several members' templates and terms into one workspace, so the same name
or term can be there more than once. 0071 makes a workspace's names and terms unique:

* of equal names the lowest id stays; a later one with the SAME instructions is retired (a meeting
  may name it) and what pointed at it moves to the one that stays; one with DIFFERENT instructions
  is renamed "Name (2)" so nothing a person wrote is lost or changed;
* a repeated term goes (same text);
* `templates.team_id` is NOT NULL; `meetings.kept_private` is false for every existing meeting;
* a team's stored `share_new_meetings` is not touched;
* the downgrade restores the per-author uniqueness and the upgrade runs again.
"""

from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from tests import test_migrations as migrations
from tests.test_workspaces_migration import member, one, team, user

alembic_config = migrations.alembic_config
owner_env = migrations.owner_env
scratch_url = migrations.scratch_url


def test_0071_follows_0070_and_is_an_ancestor_of_the_only_head(scratch_url):
    """Later slices chain on top of 0071, so it is not the head forever: it must exist, follow 0070
    and lie under the single current head."""
    script = ScriptDirectory.from_config(alembic_config(scratch_url))
    heads = script.get_heads()
    assert len(heads) == 1
    assert script.get_revision("0071").down_revision == "0070"
    assert "0071" in {rev.revision for rev in script.walk_revisions(base="base", head=heads[0])}


def test_0071_makes_names_and_terms_unique_per_workspace_without_losing_anything(scratch_url):
    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "0070")
    engine = create_engine(scratch_url)
    with engine.begin() as conn:
        seeded = one(conn, "select count(*) from templates")  # what the first migrations left
        ada, bob, cy = (user(conn, f"{n}@example.test") for n in ("ada", "bob", "cy"))
        for uid, name in ((ada, "Ada"), (bob, "Bob"), (cy, "Cy")):
            team(conn, uid, name, personal=True)
        x = team(conn, ada, "Luran")
        conn.execute(text("update teams set share_new_meetings = true where id = :t"), {"t": x})
        y = team(conn, cy, "Cy Co")  # stays off, as stored
        member(conn, x, bob)
        member(conn, x, cy)
        keep = one(
            conn,
            "insert into templates (owner_user_id, team_id, name, instructions) "
            "values (:o, :t, 'Board', 'same') returning id",
            o=ada,
            t=x,
        )
        same = one(
            conn,
            "insert into templates (owner_user_id, team_id, name, instructions) "
            "values (:o, :t, 'Board', 'same') returning id",
            o=bob,
            t=x,
        )
        different = one(
            conn,
            "insert into templates (owner_user_id, team_id, name, instructions) "
            "values (:o, :t, 'Board', 'other words') returning id",
            o=cy,
            t=x,
        )
        meeting = one(
            conn,
            "insert into meetings (owner_user_id, url, platform, native_id, template_id, "
            "shared_team_id, workspace_team_id) values (:o, 'https://meet.example.test/x', "
            "'google_meet', 'abc', :t, :x, :x) returning id",
            o=bob,
            t=same,
            x=x,
        )
        conn.execute(
            text(
                "insert into workspace_prefs (team_id, user_id, calendar_template_id) "
                "values (:t, :u, :c)"
            ),
            {"t": x, "u": bob, "c": same},
        )
        for uid, term in ((ada, "Luran"), (bob, "Luran"), (bob, "Beyric")):
            conn.execute(
                text(
                    "insert into vocabulary_terms (owner_user_id, team_id, term) "
                    "values (:o, :t, :m)"
                ),
                {"o": uid, "t": x, "m": term},
            )

    command.upgrade(cfg, "0071")

    with engine.connect() as conn:
        rows = {
            row[0]: row[1:]
            for row in conn.execute(
                text("select id, name, deleted_at is not null, instructions from templates")
            )
        }
        assert rows[keep][:2] == ("Board", False)
        assert rows[same][1] is True  # retired: same words, same name
        assert rows[different] == ("Board (2)", False, "other words")  # renamed, words intact
        # the meeting still names the retired template; the prefs moved to the one that stayed
        assert one(conn, "select template_id from meetings where id = :m", m=meeting) == same
        assert (
            one(conn, "select calendar_template_id from workspace_prefs where team_id = :t", t=x)
            == keep
        )
        terms = [
            r[0]
            for r in conn.execute(
                text("select term from vocabulary_terms where team_id = :t order by term"), {"t": x}
            )
        ]
        assert terms == ["Beyric", "Luran"]
        assert (
            one(conn, "select count(*) from templates where team_id is null") == 0
        )  # and the column is NOT NULL
        assert one(conn, "select share_new_meetings from teams where id = :t", t=x) is True
        assert one(conn, "select share_new_meetings from teams where id = :t", t=y) is False
        assert one(conn, "select kept_private from meetings where id = :m", m=meeting) is False
        nullable = one(
            conn,
            "select is_nullable from information_schema.columns "
            "where table_name = 'templates' and column_name = 'team_id'",
        )
        assert nullable == "NO"

    command.downgrade(cfg, "0070")
    with engine.connect() as conn:
        assert one(conn, "select count(*) from templates") == seeded + 3  # nothing lost
        nullable = one(
            conn,
            "select is_nullable from information_schema.columns "
            "where table_name = 'templates' and column_name = 'team_id'",
        )
        assert nullable == "YES"
    command.upgrade(cfg, "0071")
    engine.dispose()


def test_0071_adds_nothing_to_an_empty_database_and_round_trips(scratch_url):
    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0070")
    command.upgrade(cfg, "head")
