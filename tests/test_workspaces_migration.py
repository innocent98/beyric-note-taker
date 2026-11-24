"""Migration 0070 (workspaces core): the cutover rehearsal on a seeded, production-shaped dataset.

Runs the real migration on the scratch database, like the other migration tests. The dataset has
several people, personal and ordinary teams, shared, moved and private meetings, calendar
connections, templates, vocabulary, notifications and joins. The rules under test are spec
sections 8 and 12:

* no row is lost, and no meeting changes who may read it (`shared_team_id`, `owner_team_id` are
  never touched);
* a meeting's workspace is its owning team, else the team it is shared to, else its recorder's
  personal team (made when the recorder has none);
* a calendar connection moves to the person's default workspace (the one team they are in, else
  the one ordinary team, else the personal one);
* templates and vocabulary stay with the owner's personal workspace AND are copied once into the
  connection's workspace when that is not the personal one;
* notifications follow their meeting's workspace, else the person's personal one;
* `Plan.max_owned_teams` is seeded: Starter 3, Pro 10, Scale unlimited.
"""

from datetime import UTC, datetime, timedelta

from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from tests import test_migrations as migrations

alembic_config = migrations.alembic_config
owner_env = migrations.owner_env
scratch_url = migrations.scratch_url

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def test_0070_is_the_only_head_and_follows_0056(scratch_url):
    script = ScriptDirectory.from_config(alembic_config(scratch_url))
    assert len(script.get_heads()) == 1
    assert script.get_revision("0070").down_revision == "0056"


def one(conn, sql, **params):
    return conn.execute(text(sql), params).scalar()


def user(conn, email, status="active"):
    uid = one(
        conn,
        "insert into users (email, password_hash, status, bot_name) "
        "values (:e, 'x', :s, :b) returning id",
        e=email,
        s=status,
        b=f"{email.split('@')[0]} bot",
    )
    conn.execute(
        text(
            "insert into user_settings "
            "(user_id, auto_join_rule, join_unanswered, skip_one_on_ones) "
            "values (:u, 'organizer', true, true)"
        ),
        {"u": uid},
    )
    return uid


def team(conn, owner, name, personal=False):
    tid = one(
        conn,
        "insert into teams (owner_user_id, name, is_personal) values (:o, :n, :p) returning id",
        o=owner,
        n=name,
        p=personal,
    )
    conn.execute(
        text("insert into team_members (team_id, user_id, role) values (:t, :u, 'owner')"),
        {"t": tid, "u": owner},
    )
    return tid


def member(conn, tid, uid):
    conn.execute(
        text("insert into team_members (team_id, user_id, role) values (:t, :u, 'member')"),
        {"t": tid, "u": uid},
    )


def template(conn, owner, name, deleted=False):
    return one(
        conn,
        "insert into templates (owner_user_id, name, instructions, deleted_at) "
        "values (:o, :n, 'x', :d) returning id",
        o=owner,
        n=name,
        d=NOW if deleted else None,
    )


def meeting(conn, owner, code, template_id, shared=None, owned=None):
    return one(
        conn,
        "insert into meetings (owner_user_id, url, platform, native_id, template_id, "
        "shared_team_id, owner_team_id) values (:o, 'https://meet.example.test/x', "
        "'google_meet', :c, :t, :s, :w) returning id",
        o=owner,
        c=code,
        t=template_id,
        s=shared,
        w=owned,
    )


def seed(engine):
    """The cast. Ada owns X and has the personal team PA; Bob is in X; Cy is in X and owns Y (two
    ordinary teams, so his default is personal); Dee is alone; Eve is in X and has no personal
    team (a person a flag-off join once dissolved it for)."""
    ids = {}
    with engine.begin() as conn:
        ada, bob, cy, dee, eve = (
            user(conn, f"{n}@example.test") for n in ("ada", "bob", "cy", "dee", "eve")
        )
        ids.update(ada=ada, bob=bob, cy=cy, dee=dee, eve=eve)
        ids["pa"] = team(conn, ada, "Ada", personal=True)
        ids["pb"] = team(conn, bob, "Bob", personal=True)
        ids["pc"] = team(conn, cy, "Cy", personal=True)
        ids["pd"] = team(conn, dee, "Dee", personal=True)
        ids["x"] = team(conn, ada, "Luran")
        ids["y"] = team(conn, cy, "Cy Co")
        for uid in (bob, cy, eve):
            member(conn, ids["x"], uid)

        ada_general = template(conn, ada, "General")
        ada_call = template(conn, ada, "Client call")
        template(conn, ada, "Old one", deleted=True)
        bob_general = template(conn, bob, "General")
        cy_general = template(conn, cy, "General")
        template(conn, dee, "General")
        eve_general = template(conn, eve, "General")
        ids.update(ada_general=ada_general, ada_call=ada_call)
        for uid, term in ((ada, "Luran"), (ada, "Kubernetes"), (bob, "Luran"), (dee, "Zoom")):
            conn.execute(
                text("insert into vocabulary_terms (owner_user_id, term) values (:u, :t)"),
                {"u": uid, "t": term},
            )

        # Meetings: every kind of visibility the old rule has.
        ids["m_private"] = meeting(conn, ada, "aaa-1", ada_general)
        ids["m_shared"] = meeting(conn, ada, "aaa-2", ada_general, shared=ids["x"])
        ids["m_moved"] = meeting(conn, bob, "bbb-1", bob_general, owned=ids["x"])
        ids["m_cy"] = meeting(conn, cy, "ccc-1", cy_general)
        ids["m_eve"] = meeting(conn, eve, "eee-1", eve_general)
        ids["m_own_personal"] = meeting(conn, ada, "aaa-3", ada_general, shared=ids["pa"])
        ids["m_cy_y"] = meeting(conn, cy, "ccc-2", cy_general, shared=ids["y"])
        conn.execute(
            text("update meetings set series_id = null"),  # the column exists and stays untouched
        )

        # Calendar connections: Ada's goes to X (her one ordinary team), Cy's stays personal (two
        # ordinary teams), Dee's is personal, Eve's goes to X (her only membership).
        for key, uid in (("ada", ada), ("cy", cy), ("dee", dee), ("eve", eve)):
            ids[f"cc_{key}"] = one(
                conn,
                "insert into calendar_connections (user_id, provider) values (:u, 'google') "
                "returning id",
                u=uid,
            )
        ids["event_ada"] = one(
            conn,
            "insert into calendar_events (user_id, connection_id, source_event_id, title, "
            "starts_at, ends_at) values (:u, :c, 'ev1', 'Standup', :s, :e) returning id",
            u=ada,
            c=ids["cc_ada"],
            s=NOW,
            e=NOW + timedelta(hours=1),
        )
        conn.execute(
            text("update user_settings set calendar_template_id = :t where user_id = :u"),
            {"t": ada_call, "u": ada},
        )

        # Joins: one from Ada's event, one instant-Meet join of Dee's (no event).
        for key, uid, event in (("ada", ada, ids["event_ada"]), ("dee", dee, None)):
            ids[f"join_{key}"] = one(
                conn,
                "insert into scheduled_joins (user_id, source, calendar_event_id, platform, "
                "native_id, meeting_url, starts_at, ends_at, run_at, deadline_at) values "
                "(:u, :src, :ev, 'google_meet', :nid, 'https://meet.example.test/y', "
                ":s, :e, :s, :e) "
                "returning id",
                u=uid,
                src="calendar" if event else "instant_meet",
                ev=event,
                nid=f"join-{key}",
                s=NOW,
                e=NOW + timedelta(hours=1),
            )

        # Notifications: about a shared meeting, about a private one, about nothing.
        for key, uid, mid in (
            ("n_shared", ada, ids["m_shared"]),
            ("n_private", ada, ids["m_private"]),
            ("n_none", ada, None),
            ("n_bob", bob, ids["m_moved"]),
            ("n_eve", eve, None),
        ):
            ids[key] = one(
                conn,
                "insert into notifications (user_id, kind, title, meeting_id, dedupe_key) "
                "values (:u, 'k', 't', :m, :d) returning id",
                u=uid,
                m=mid,
                d=key,
            )
    return ids


def snapshot(conn):
    return {
        table: one(conn, f"select count(*) from {table}")
        for table in (
            "users",
            "meetings",
            "notifications",
            "calendar_connections",
            "calendar_events",
            "scheduled_joins",
            "tasks",
        )
    }


def visibility_columns(conn):
    return conn.execute(
        text("select id, owner_user_id, shared_team_id, owner_team_id from meetings order by id")
    ).all()


def test_0070_cutover_rehearsal_keeps_every_row_and_every_visibility(scratch_url):
    cfg = alembic_config(scratch_url)
    engine = create_engine(scratch_url)
    command.upgrade(cfg, "0056")
    ids = seed(engine)
    with engine.connect() as conn:
        before = snapshot(conn)
        visible_before = visibility_columns(conn)
        members_before = one(conn, "select count(*) from team_members")
        templates_before = one(conn, "select count(*) from templates")
        vocab_before = one(conn, "select count(*) from vocabulary_terms")
        teams_before = {r[0] for r in conn.execute(text("select id from teams"))}

    command.upgrade(cfg, "0070")

    with engine.connect() as conn:
        after = snapshot(conn)
        # ---- nothing lost, nothing re-shared
        assert after == before
        assert visibility_columns(conn) == visible_before
        # the only new membership is the owner of the personal team made for Eve
        assert one(conn, "select count(*) from team_members") == members_before + 1

        # ---- meetings: owner team, else shared team, else the recorder's personal team
        ws = dict(conn.execute(text("select id, workspace_team_id from meetings")).all())
        eve_personal = one(
            conn, "select id from teams where owner_user_id = :u and is_personal", u=ids["eve"]
        )
        assert eve_personal is not None and eve_personal not in teams_before
        assert (
            one(conn, "select count(*) from team_members where team_id = :t", t=eve_personal) == 1
        )  # her personal team has her as its owner
        assert ws[ids["m_private"]] == ids["pa"]
        assert ws[ids["m_shared"]] == ids["x"]
        assert ws[ids["m_moved"]] == ids["x"]
        assert ws[ids["m_cy"]] == ids["pc"]
        assert ws[ids["m_eve"]] == eve_personal
        assert ws[ids["m_own_personal"]] == ids["pa"]
        assert ws[ids["m_cy_y"]] == ids["y"]
        col = {c["name"]: c for c in inspect(engine).get_columns("meetings")}["workspace_team_id"]
        assert col["nullable"] is False

        # ---- calendar connections: the person's default workspace
        cc = dict(conn.execute(text("select id, team_id from calendar_connections")).all())
        assert cc[ids["cc_ada"]] == ids["x"]
        assert cc[ids["cc_cy"]] == ids["pc"]
        assert cc[ids["cc_dee"]] == ids["pd"]
        assert cc[ids["cc_eve"]] == ids["x"]

        # ---- scheduled joins follow their connection, else the person's default workspace
        sj = dict(conn.execute(text("select id, team_id from scheduled_joins")).all())
        assert sj[ids["join_ada"]] == ids["x"]
        assert sj[ids["join_dee"]] == ids["pd"]

        # ---- notifications: the meeting's workspace, else the personal one
        nt = dict(conn.execute(text("select id, team_id from notifications")).all())
        assert nt[ids["n_shared"]] == ids["x"]
        assert nt[ids["n_private"]] == ids["pa"]
        assert nt[ids["n_none"]] == ids["pa"]
        assert nt[ids["n_bob"]] == ids["x"]
        assert nt[ids["n_eve"]] == eve_personal

        # ---- templates: every old row stays in the owner's personal workspace ...
        def templates(owner, team_id):
            return sorted(
                r[0]
                for r in conn.execute(
                    text(
                        "select name from templates where owner_user_id = :o and team_id = :t "
                        "and deleted_at is null"
                    ),
                    {"o": owner, "t": team_id},
                )
            )

        assert templates(ids["ada"], ids["pa"]) == ["Client call", "General"]
        assert templates(ids["bob"], ids["pb"]) == ["General"]
        # ... and Ada's (not the soft-deleted one) are copied once into X, where her calendar is
        assert templates(ids["ada"], ids["x"]) == ["Client call", "General"]
        assert templates(ids["eve"], ids["x"]) == ["General"]  # Eve's connection is in X too
        assert templates(ids["cy"], ids["x"]) == []  # Cy's connection is personal: no copy
        assert templates(ids["dee"], ids["x"]) == []
        assert one(conn, "select count(*) from templates where team_id is null") == 0
        # copies: Ada's 2 live templates and Eve's 1 (the migrations' own defaults belong to
        # the owner account, whose calendar is not connected)
        assert one(conn, "select count(*) from templates") == templates_before + 3

        # ---- vocabulary the same way
        def terms(owner, team_id):
            return sorted(
                r[0]
                for r in conn.execute(
                    text(
                        "select term from vocabulary_terms "
                        "where owner_user_id = :o and team_id = :t"
                    ),
                    {"o": owner, "t": team_id},
                )
            )

        assert terms(ids["ada"], ids["pa"]) == ["Kubernetes", "Luran"]
        assert terms(ids["ada"], ids["x"]) == ["Kubernetes", "Luran"]
        assert terms(ids["dee"], ids["pd"]) == ["Zoom"]
        assert one(conn, "select count(*) from vocabulary_terms") == vocab_before + 2  # Ada's copy

        # ---- per-workspace preferences from the person's settings: the personal workspace for
        # everyone, and the connection's workspace when it is another
        prefs = {
            (r[0], r[1]): r
            for r in conn.execute(
                text(
                    "select team_id, user_id, bot_name, auto_join_rule, join_unanswered, "
                    "skip_one_on_ones, calendar_template_id from workspace_prefs"
                )
            )
        }
        assert (ids["pa"], ids["ada"]) in prefs and (ids["x"], ids["ada"]) in prefs
        assert (ids["pb"], ids["bob"]) in prefs and (ids["x"], ids["bob"]) not in prefs
        assert (ids["pc"], ids["cy"]) in prefs and (ids["x"], ids["cy"]) not in prefs
        assert (ids["x"], ids["eve"]) in prefs and (eve_personal, ids["eve"]) in prefs
        personal, in_x = prefs[(ids["pa"], ids["ada"])], prefs[(ids["x"], ids["ada"])]
        assert personal[2] == in_x[2] == "ada bot"
        assert personal[3] == in_x[3] == "organizer" and personal[4] is True and personal[5] is True
        # Ada's calendar template: her own row in personal, the copy of the same name in X
        assert personal[6] == ids["ada_call"]
        copy = one(
            conn,
            "select id from templates where owner_user_id = :o and team_id = :t "
            "and name = 'Client call'",
            o=ids["ada"],
            t=ids["x"],
        )
        assert in_x[6] == copy and copy != ids["ada_call"]

        # ---- the new teams and plan columns
        assert {r[0] for r in conn.execute(text("select kind from teams"))} == {"team"}
        limits = dict(conn.execute(text("select code, max_owned_teams from plans")).all())
        assert limits["plan_20h"] == 3 and limits["plan_50h"] == 10
        assert limits["plan_100h"] is None
        assert one(conn, "select count(*) from meetings where workspace_team_id is null") == 0

    # ---- the round trip: the downgrade drops what 0070 made, the upgrade makes it again
    command.downgrade(cfg, "0056")
    insp = inspect(engine)
    assert "workspace_prefs" not in insp.get_table_names()
    assert "workspace_team_id" not in {c["name"] for c in insp.get_columns("meetings")}
    assert "team_id" not in {c["name"] for c in insp.get_columns("notifications")}
    with engine.connect() as conn:
        assert one(conn, "select count(*) from meetings") == before["meetings"]
        assert visibility_columns(conn) == visible_before
        assert one(conn, "select count(*) from notifications") == before["notifications"]
        # copies are gone or taken out of use: the old per-owner uniqueness holds again
        dupes = one(
            conn,
            "select count(*) from (select owner_user_id, name from templates "
            "where deleted_at is null group by 1, 2 having count(*) > 1) d",
        )
        assert dupes == 0
        dupes = one(
            conn,
            "select count(*) from (select owner_user_id, term from vocabulary_terms "
            "group by 1, 2 having count(*) > 1) d",
        )
        assert dupes == 0
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        assert snapshot(conn) == before
        assert one(conn, "select count(*) from meetings where workspace_team_id is null") == 0
    engine.dispose()


def test_0070_adds_nothing_to_an_empty_database_and_round_trips(scratch_url):
    cfg = alembic_config(scratch_url)
    engine = create_engine(scratch_url)
    command.upgrade(cfg, "head")
    insp = inspect(engine)
    assert {"workspace_prefs"} <= set(insp.get_table_names())
    team_columns = {c["name"]: c for c in insp.get_columns("teams")}
    assert team_columns["kind"]["nullable"] is False
    command.downgrade(cfg, "0056")
    command.upgrade(cfg, "head")
    engine.dispose()
