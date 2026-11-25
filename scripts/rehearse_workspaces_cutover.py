"""Rehearse the workspaces cutover (migrations 0062 to 0064) on production-shaped data.

    TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/notetaker_test \\
        python scripts/rehearse_workspaces_cutover.py

Makes a scratch database on that server, migrates it to 0061 (what production runs today), fills it
with a few hundred people shaped like the real data (personal teams, ordinary teams, shared, moved
and private meetings, calendar connections of two providers with events and joins, templates,
vocabulary, notifications, recipes, drafts, team briefs, deleted accounts), upgrades to head, and
checks the rules of the workspaces design (sections 8 and 12):

* no row is lost, and no meeting changes who may read it (a checksum over the visibility columns);
* every person who can still sign in has exactly one personal workspace they own;
* every meeting, notification, template, vocabulary term, connection and join has a workspace;
* every calendar connection sits in the person's default workspace (computed here, independently);
* it then downgrades to 0061, checks nothing was lost, and upgrades again.

Prints a markdown table of the row counts (for the SOP) and exits 1 on any broken rule. The
database is dropped at the end. The seed is deterministic: the same run gives the same counts.
"""

import hashlib
import os
import random
import sys
import time
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alembic import command  # noqa: E402

from tests.test_migrations import alembic_config  # noqa: E402

URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/notetaker_test"
)
SCRATCH = "notetaker_cutover_rehearsal"
PEOPLE, DELETED, TEAMS = 400, 15, 40
COUNTED = (
    "users",
    "teams",
    "team_members",
    "meetings",
    "notifications",
    "templates",
    "vocabulary_terms",
    "calendar_connections",
    "calendar_events",
    "scheduled_joins",
    "tasks",
    "recipes",
    "documents",
    "document_shares",
    "team_briefs",
)


def seed(conn, rnd: random.Random) -> None:
    """Fills the database with the people, teams and rows described above."""
    users = []
    for i in range(1, PEOPLE + 1):
        status = "deleted" if i > PEOPLE - DELETED else "active"
        uid = conn.execute(
            text(
                "insert into users (email, password_hash, status, bot_name) "
                "values (:e, 'x', :s, :b) returning id"
            ),
            {"e": f"p{i}@example.test", "s": status, "b": f"P{i} notes"},
        ).scalar()
        conn.execute(
            text(
                "insert into user_settings (user_id, auto_join_rule, join_unanswered, "
                "skip_one_on_ones) values (:u, :r, :j, :k)"
            ),
            {
                "u": uid,
                "r": rnd.choice(["all", "organizer", "none"]),
                "j": i % 3 == 0,
                "k": i % 2 == 1,
            },
        )
        users.append(uid)
    member_of: dict[int, list[tuple[int, bool]]] = {u: [] for u in users}  # (team, personal)

    def make_team(owner: int, name: str, personal: bool) -> int:
        tid = conn.execute(
            text(
                "insert into teams (owner_user_id, name, is_personal) values (:o, :n, :p) "
                "returning id"
            ),
            {"o": owner, "n": name, "p": personal},
        ).scalar()
        conn.execute(
            text("insert into team_members (team_id, user_id, role) values (:t, :u, 'owner')"),
            {"t": tid, "u": owner},
        )
        member_of[owner].append((tid, personal))
        return tid

    live = users[: PEOPLE - DELETED]
    # Production had a personal team for most people; a flag-off join dissolved some of them.
    for uid in live:
        if rnd.random() < 0.7:
            make_team(uid, f"personal {uid}", True)
    ordinary = []
    for k in range(TEAMS):
        owner = rnd.choice(live)
        tid = make_team(owner, f"Team {k}", False)
        ordinary.append(tid)
        for uid in rnd.sample(live, rnd.randint(1, 6)):
            if all(t != tid for t, _ in member_of[uid]):
                conn.execute(
                    text("insert into team_members (team_id, user_id, role) values (:t, :u, :r)"),
                    {"t": tid, "u": uid, "r": rnd.choice(["admin", "member", "member"])},
                )
                member_of[uid].append((tid, False))

    for uid in users:
        template_ids = [
            conn.execute(
                text(
                    "insert into templates (owner_user_id, name, instructions) "
                    "values (:o, :n, 'x') returning id"
                ),
                {"o": uid, "n": n},
            ).scalar()
            for n in ("General", "Client call")
        ]
        for term in rnd.sample(["Luran", "Beyric", "Kubernetes", "Zoom", "Paystack"], 3):
            conn.execute(
                text("insert into vocabulary_terms (owner_user_id, term) values (:u, :t)"),
                {"u": uid, "t": term},
            )
        mids = []
        for n in range(12):
            teams_in = [t for t, _ in member_of[uid]]
            shared = owned = None
            roll = rnd.random()
            if teams_in and roll < 0.33:
                shared = rnd.choice(teams_in)
            elif teams_in and roll < 0.5:
                owned = rnd.choice(teams_in)
            mids.append(
                conn.execute(
                    text(
                        "insert into meetings (owner_user_id, url, platform, native_id, "
                        "template_id, shared_team_id, owner_team_id) values (:o, "
                        "'https://meet.example.test/x', 'google_meet', :c, :t, :s, :w) "
                        "returning id"
                    ),
                    {"o": uid, "c": f"{uid}-{n}", "t": template_ids[0], "s": shared, "w": owned},
                ).scalar()
            )
        for n in range(5):
            conn.execute(
                text(
                    "insert into notifications (user_id, kind, title, meeting_id, dedupe_key) "
                    "values (:u, 'k', 't', :m, :d)"
                ),
                {"u": uid, "m": mids[n] if n % 2 else None, "d": f"n{uid}-{n}"},
            )
        for provider, chance in (("google", 0.6), ("microsoft", 0.15)):
            if rnd.random() >= chance:
                continue
            cid = conn.execute(
                text(
                    "insert into calendar_connections (user_id, provider) values (:u, :p) "
                    "returning id"
                ),
                {"u": uid, "p": provider},
            ).scalar()
            eid = conn.execute(
                text(
                    "insert into calendar_events (user_id, connection_id, source_event_id, "
                    "title, starts_at, ends_at) values (:u, :c, 'e1', 'Standup', now(), "
                    "now() + interval '1 hour') returning id"
                ),
                {"u": uid, "c": cid},
            ).scalar()
            conn.execute(
                text(
                    "insert into scheduled_joins (user_id, source, calendar_event_id, platform, "
                    "native_id, meeting_url, starts_at, ends_at, run_at, deadline_at) values "
                    "(:u, 'calendar', :e, 'google_meet', :n, 'https://meet.example.test/y', "
                    "now(), now() + interval '1 hour', now(), now() + interval '1 hour')"
                ),
                {"u": uid, "e": eid, "n": f"j{cid}"},
            )
        if rnd.random() < 0.3:
            conn.execute(
                text(
                    "insert into recipes (owner_user_id, team_id, name, instruction, output_type)"
                    " values (:u, :t, 'R', 'x', 'email')"
                ),
                {"u": uid, "t": rnd.choice(ordinary) if rnd.random() < 0.4 else None},
            )
        if rnd.random() < 0.3:
            did = conn.execute(
                text(
                    "insert into documents (owner_user_id, team_id, title, output_type) "
                    "values (:u, :t, 'D', 'email') returning id"
                ),
                {"u": uid, "t": ordinary[0]},
            ).scalar()
            if rnd.random() < 0.5:
                conn.execute(
                    text(
                        "insert into document_shares (document_id, team_id, shared_by_user_id) "
                        "values (:d, :t, :u)"
                    ),
                    {"d": did, "t": ordinary[0], "u": uid},
                )
    for tid in ordinary:
        conn.execute(
            text("insert into team_briefs (team_id, text) values (:t, 'Our services')"),
            {"t": tid},
        )


def counts(conn) -> dict[str, int]:
    return {t: conn.execute(text(f"select count(*) from {t}")).scalar() for t in COUNTED}


def visibility_checksum(conn) -> str:
    rows = conn.execute(
        text("select id, owner_user_id, shared_team_id, owner_team_id from meetings order by id")
    ).all()
    return hashlib.sha256(repr(rows).encode()).hexdigest()[:16]


def expected_default(memberships: list[tuple[int, bool]]) -> int | None:
    """`teams.default_membership`: the only team, else the only ordinary one, else personal."""
    if len(memberships) == 1:
        return memberships[0][0]
    ordinary = [t for t, personal in memberships if not personal]
    if len(ordinary) == 1:
        return ordinary[0]
    personal = [t for t, p in memberships if p]
    return personal[0] if personal else (memberships[0][0] if memberships else None)


def main() -> int:
    base = make_url(URL)
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"drop database if exists {SCRATCH}"))
        conn.execute(text(f"create database {SCRATCH}"))
    scratch = base.set(database=SCRATCH).render_as_string(hide_password=False)
    cfg = alembic_config(scratch)
    engine = create_engine(scratch)
    failures: list[str] = []

    def check(ok: bool, what: str) -> None:
        print(("ok   " if ok else "FAIL ") + what)
        if not ok:
            failures.append(what)

    try:
        command.upgrade(cfg, "0061")
        with engine.begin() as conn:
            seed(conn, random.Random(20261125))
        with engine.connect() as conn:
            before = counts(conn)
            sums = visibility_checksum(conn)
            personal_before = conn.execute(
                text("select count(*) from teams where is_personal")
            ).scalar()
        started = time.monotonic()
        command.upgrade(cfg, "head")
        took = time.monotonic() - started
        with engine.connect() as conn:
            after = counts(conn)
            check(visibility_checksum(conn) == sums, "meeting visibility columns unchanged")
            for table in ("meetings", "notifications", "calendar_connections", "scheduled_joins"):
                column = "workspace_team_id" if table == "meetings" else "team_id"
                nulls = conn.execute(
                    text(f"select count(*) from {table} where {column} is null")
                ).scalar()
                check(nulls == 0, f"{table}.{column}: no row without a workspace")
            for table in ("templates", "vocabulary_terms"):
                nulls = conn.execute(
                    text(f"select count(*) from {table} where team_id is null")
                ).scalar()
                check(nulls == 0, f"{table}.team_id: no row without a workspace")
            for table in (
                "users",
                "meetings",
                "notifications",
                "calendar_connections",
                "calendar_events",
                "scheduled_joins",
                "tasks",
                "recipes",
                "documents",
                "document_shares",
                "team_briefs",
            ):
                check(after[table] == before[table], f"{table}: {before[table]} rows kept")
            # every person who can still sign in: exactly one personal workspace, theirs
            bad = conn.execute(
                text(
                    "select count(*) from users u where u.status <> 'deleted' and "
                    "(select count(*) from teams t join team_members tm on tm.team_id = t.id "
                    " and tm.user_id = u.id and tm.role = 'owner' "
                    " where t.owner_user_id = u.id and t.is_personal) <> 1"
                )
            ).scalar()
            check(bad == 0, "every active user owns exactly one personal workspace")
            # every connection is in the person's default workspace: the rule of
            # `teams.default_membership`, applied here in Python to the memberships AFTER the
            # migration (it adds the personal teams), read from the tables
            after_member: dict[int, list[tuple[int, bool]]] = {}
            for uid, tid, personal in conn.execute(
                text(
                    "select tm.user_id, tm.team_id, "
                    "(t.is_personal and t.owner_user_id = tm.user_id) "
                    "from team_members tm join teams t on t.id = tm.team_id order by tm.id"
                )
            ):
                after_member.setdefault(uid, []).append((tid, personal))
            wrong = 0
            for cid, uid, team_id in conn.execute(
                text("select id, user_id, team_id from calendar_connections")
            ):
                if expected_default(after_member[uid]) != team_id:
                    wrong += 1
                    print(
                        f"  connection {cid} of user {uid}: in {team_id}, "
                        f"memberships {after_member[uid]}"
                    )
            check(wrong == 0, "every calendar connection is in its person's default workspace")
            wrong_joins = conn.execute(
                text(
                    "select count(*) from scheduled_joins j join calendar_events e "
                    "on e.id = j.calendar_event_id join calendar_connections c "
                    "on c.id = e.connection_id where j.team_id <> c.team_id"
                )
            ).scalar()
            check(wrong_joins == 0, "every calendar join is in its connection's workspace")
        command.downgrade(cfg, "0061")
        with engine.connect() as conn:
            down = counts(conn)
            check(visibility_checksum(conn) == sums, "downgrade: visibility columns unchanged")
            for table in (
                "users",
                "meetings",
                "notifications",
                "calendar_connections",
                "calendar_events",
                "scheduled_joins",
                "tasks",
                "recipes",
                "documents",
            ):
                check(down[table] == before[table], f"downgrade: {table} {before[table]} rows kept")
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            check(
                visibility_checksum(conn) == sums
                and conn.execute(
                    text("select count(*) from meetings where workspace_team_id is null")
                ).scalar()
                == 0,
                "upgrade again after the downgrade: clean",
            )
        print()
        print("| Table | Before (0061) | After (head) |")
        print("|---|---:|---:|")
        for table in COUNTED:
            print(f"| `{table}` | {before[table]} | {after[table]} |")
        made_teams = after["teams"] - before["teams"]
        print(f"\npersonal teams: {personal_before} before, {personal_before + made_teams} after")
        print(f"upgrade 0061 to head: {took:.1f} s; meeting visibility checksum {sums}")
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f"drop database if exists {SCRATCH}"))
        admin.dispose()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
