"""Drive the team-scoped lists over real HTTP and save every exchange for the frontend guide.

    python scripts/capture_team_scoped_lists.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres

Same method as `capture_teams_api.py`: a scratch database with the real migrations, the real app
under uvicorn on a free local port, httpx for every call, every exchange saved to
`docs/fe-captures/team-scoped-lists/NN-name.json`. `MULTI_TEAM_ENABLED=true` is set for THIS run only.

Shows that the Meetings list, the Decisions log, the Review list and the sidebar badges follow the
active team (the `X-Team-Id` header), that a meeting opened by its link still opens, and that a team
can be renamed (not the personal one). The cast (placeholders): Ada owns team X ("Platform team"), Pat
is in X and in Y ("Client Y", owned by Quinn); everyone also has a personal team. No model is called
and no mail leaves this machine: the meetings, decisions, review items and tasks are rows made here.
"""

import json
import os
import shutil
import socket
import sys
import threading
import time
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "fe-captures" / "team-scoped-lists"
DB_NAME = "notetaker_capture_team_lists"
ADA = "owner@example.test"  # the account migration 0006 creates (a placeholder)
PAT, QUINN = "pat@example.test", "quinn@example.test"
PASSWORD = "-".join(["capture", "password", "placeholder"])
MEET = "https://meet.google.com/abc-defg-hij"
NOTES = "## Summary\nWe agreed to ship the checkout on Friday.\n\n## Action items\n- Pat: send the deck\n- Ada: book the room\n"
TRANSCRIPT = (
    "Ada: Let's ship the checkout on Friday.\nPat: I'll send the deck.\nAda: I'll book the room.\n"
)


def database_urls(admin_url: str) -> tuple[str, str]:
    parsed = urlparse(admin_url)
    return admin_url, parsed._replace(path=f"/{DB_NAME}").geturl()


admin_url, scratch_url = database_urls(sys.argv[1] if len(sys.argv) > 1 else "")
os.environ["DATABASE_URL"] = scratch_url
os.environ["OWNER_EMAIL"] = ADA
os.environ["OWNER_PASSWORD"] = PASSWORD
os.environ["MULTI_TEAM_ENABLED"] = "true"

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import teams  # noqa: E402
from app.accounts import create_user_defaults  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import (  # noqa: E402
    Decision,
    Meeting,
    ReviewItem,
    Task,
    Team,
    TeamMember,
    Template,
    User,
)
from app.passwords import hash_password  # noqa: E402


class Recorder:
    count = 0

    def __init__(self, base_url: str, who: str) -> None:
        self.http = httpx.Client(base_url=base_url, follow_redirects=False, timeout=120)
        self.csrf: str | None = None
        self.who = who
        self.masks: dict[str, str] = {}

    def mask(self, token: str) -> str:
        return self.masks.setdefault(token, f"<csrf-{chr(ord('A') + len(self.masks))}>")

    def call(self, name, method, path, *, body=None, note="", team=None):
        headers = {"X-CSRF-Token": self.csrf} if self.csrf and method != "GET" else {}
        if team is not None:
            headers["X-Team-Id"] = str(team)
        had_cookie = "session" in self.http.cookies
        response = self.http.request(method, path, json=body, headers=headers)
        data = response.json() if "json" in response.headers.get("content-type", "") else None
        if isinstance(data, dict) and "csrf_token" in data:
            self.csrf = data["csrf_token"]
        Recorder.count += 1
        sent = {k: self.mask(v) if k == "X-CSRF-Token" else v for k, v in headers.items()}
        if had_cookie:
            sent["Cookie"] = "session=<cookie>"
        shown = json.loads(json.dumps(data))
        if isinstance(shown, dict) and "csrf_token" in shown:
            shown["csrf_token"] = self.mask(shown["csrf_token"])
        record = {
            "note": note,
            "as": self.who,
            "request": {"method": method, "path": path, "headers": sent, "body": body},
            "response": {"status": response.status_code, "body": shown},
        }
        file = OUT / f"{Recorder.count:02d}-{name}.json"
        file.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        print(f"{file.name:58} {self.who:6} {method:6} {path:44} -> {response.status_code}")
        return response


def start_server(app) -> tuple[uvicorn.Server, str]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return server, f"http://127.0.0.1:{port}"


def prepare_database() -> None:
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f"drop database if exists {DB_NAME} with (force)"))
        connection.execute(text(f"create database {DB_NAME}"))
    admin.dispose()
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")


def make_person(session, email: str, name: str) -> User:
    user = User(
        email=email,
        name=name,
        password_hash=hash_password(PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    session.add(user)
    session.commit()
    create_user_defaults(session, user)
    teams.ensure_personal_team(session, user)
    session.commit()
    return user


def make_team(session, boss: User, name: str, *members: User) -> Team:
    team = Team(owner_user_id=boss.id, name=name, is_personal=False)
    session.add(team)
    session.flush()
    session.add(TeamMember(team_id=team.id, user_id=boss.id, role="owner"))
    for person in members:
        session.add(TeamMember(team_id=team.id, user_id=person.id, role="member"))
    session.commit()
    return team


def seed(engine) -> dict:
    with Session(engine) as session:
        ada = session.scalars(select(User).where(User.email == ADA)).one()
        ada.name = "Ada Example"
        session.commit()
        teams.ensure_personal_team(session, ada)
        session.commit()
        pat = make_person(session, PAT, "Pat Example")
        quinn = make_person(session, QUINN, "Quinn Example")
        x = make_team(session, ada, "Platform team", pat)
        y = make_team(session, quinn, "Client Y", pat)
        general = {
            u.id: session.scalars(
                select(Template).where(Template.owner_user_id == u.id, Template.name == "General")
            ).one()
            for u in (ada, pat, quinn)
        }

        def meeting(owner: User, title: str, **fields) -> Meeting:
            row = Meeting(
                owner_user_id=owner.id,
                url=MEET,
                platform="google_meet",
                native_id="abc-defg-hij",
                template_id=general[owner.id].id,
                status="ready",
                step="done",
                notes=NOTES,
                transcript=TRANSCRIPT,
                speaker_transcript=TRANSCRIPT,
                recorded_seconds=1260,
                title=title,
                **fields,
            )
            session.add(row)
            session.flush()
            session.add(
                Decision(
                    meeting_id=row.id, text=f"Decision of {title}", position=0, fingerprint=title
                )
            )
            return row

        rows = {
            "pat_private": meeting(pat, "Pat private"),
            "pat_in_x": meeting(pat, "Pat shared to X", shared_team_id=x.id),
            "pat_moved_y": meeting(pat, "Pat moved to Y", owner_team_id=y.id),
            "ada_in_x": meeting(ada, "Ada shared to X", shared_team_id=x.id),
            "quinn_in_y": meeting(quinn, "Quinn shared to Y", shared_team_id=y.id),
            "ada_private": meeting(ada, "Ada private"),
        }
        for key in ("pat_private", "pat_in_x", "pat_moved_y"):  # the recorder's own review items
            session.add(
                ReviewItem(
                    meeting_id=rows[key].id,
                    owner_user_id=pat.id,
                    text=f"Check the figures in {key}",
                    position=0,
                    fingerprint=key,
                )
            )
        for team, title in ((x, "Book the room"), (x, "Order the cake"), (y, "Pay the invoice")):
            session.add(
                Task(
                    team_id=team.id,
                    title=title,
                    source="manual",
                    assigned_on=date(2026, 11, 3),
                    is_new=True,
                )
            )
        session.commit()
        return {
            "ids": {k: v.id for k, v in rows.items()},
            "x": x.id,
            "y": y.id,
            "pat_home": teams.personal_team(session, pat.id).id,
        }


def login(base: str, who: str, email: str) -> Recorder:
    handle = Recorder(base, who)
    handle.call(f"session-{who.lower()}", "GET", "/api/session")
    handle.call(
        f"login-{who.lower()}", "POST", "/api/session", body={"email": email, "password": PASSWORD}
    )
    return handle


def run(base: str, seeded: dict, engine) -> None:
    ids, x, y, home = seeded["ids"], seeded["x"], seeded["y"], seeded["pat_home"]
    ada, pat, quinn = login(base, "Ada", ADA), login(base, "Pat", PAT), login(base, "Quinn", QUINN)
    names = {v: k for k, v in ids.items()}

    def shown(response) -> None:
        listed = response.json().get("meetings") or response.json().get("groups")
        print("   ->", [names.get((m.get("meeting") or m)["id"]) for m in listed or []])

    # ---- the Meetings list (the dashboard at /) ----
    shown(
        pat.call(
            "meetings-no-header",
            "GET",
            "/api/meetings",
            note="No header: Pat's DEFAULT team, the personal space (two ordinary teams), so only the meeting nobody has.",
        )
    )
    shown(
        pat.call(
            "meetings-personal",
            "GET",
            "/api/meetings",
            team=home,
            note="The personal team: only meetings Pat recorded that are neither shared nor moved.",
        )
    )
    shown(
        pat.call(
            "meetings-x",
            "GET",
            "/api/meetings",
            team=x,
            note="Team X: meetings shared to X, whoever recorded them (Pat's and Ada's).",
        )
    )
    shown(
        pat.call(
            "meetings-y",
            "GET",
            "/api/meetings",
            team=y,
            note="Team Y: the meeting Pat MOVED to Y and Quinn's shared one.",
        )
    )
    ada_default = ada.call(
        "meetings-ada-no-header",
        "GET",
        "/api/meetings",
        note="Ada has one ordinary team: X is her default, so a list with no header is X's.",
    )
    shown(ada_default)
    pat.call(
        "meetings-not-my-team",
        "GET",
        "/api/meetings",
        team=seeded["y"] + 100,
        note="A team Pat is not in (or that does not exist): 404, the same answer for both.",
    )
    pat.call(
        "meeting-by-link-while-personal",
        "GET",
        f"/api/meetings/{ids['ada_in_x']}",
        team=home,
        note="A meeting opened by its link opens whatever team is active: the header does not narrow a direct read.",
    )
    pat.call(
        "meeting-private-of-ada",
        "GET",
        f"/api/meetings/{ids['ada_private']}",
        team=x,
        note="Ada's private meeting is nobody else's, whatever team is named.",
    )

    # ---- decisions ----
    for name, team, note in (
        ("decisions-personal", home, "A decision belongs to its meeting's team."),
        ("decisions-x", x, ""),
        ("decisions-y", y, ""),
    ):
        shown(pat.call(name, "GET", "/api/decisions", team=team, note=note))

    # ---- review and the badges ----
    pat.call(
        "review-x",
        "GET",
        "/api/review",
        team=x,
        note="Review items follow the meeting's team; `open_count` counts the same team's items.",
    )
    pat.call("review-personal", "GET", "/api/review", team=home)
    for name, team in (("badges-personal", home), ("badges-x", x), ("badges-y", y)):
        pat.call(name, "GET", "/api/session", team=team, note="`badges` count the named team only.")
    pat.call(
        "tasks-x",
        "GET",
        "/api/tasks",
        team=x,
        note="The board of X: two New tasks, so `badges.tasks_new` is 2 for X.",
    )
    pat.call(
        "tasks-personal",
        "GET",
        "/api/tasks",
        team=home,
        note="The personal board is empty, so its badge is 0 (the bug was 41 beside Tasks and an empty list).",
    )
    quinn.call(
        "session-stale-team",
        "GET",
        "/api/session",
        team=x,
        note="Quinn is not in X: the session does not fail over it, it counts Quinn's default team.",
    )
    pat.call(
        "badges-no-header", "GET", "/api/session", note="No header: the default team's badges."
    )

    # ---- the switch off: nothing narrows ----
    switch = teams.multi_team_enabled
    teams.multi_team_enabled = lambda: False
    try:
        shown(
            pat.call(
                "meetings-switch-off",
                "GET",
                "/api/meetings",
                note="With the switch OFF and no header the list is what it always was: everything Pat may read.",
            )
        )
    finally:
        teams.multi_team_enabled = switch

    # ---- renaming a team ----
    ada.call(
        "team-rename",
        "PATCH",
        "/api/team",
        team=x,
        body={"name": "Platform"},
        note="An owner or admin renames an ordinary team.",
    )
    ada.call("team-rename-empty", "PATCH", "/api/team", team=x, body={"name": "   "})
    pat.call("team-rename-as-member", "PATCH", "/api/team", team=x, body={"name": "Mine now"})
    pat.call(
        "team-rename-personal",
        "PATCH",
        "/api/team",
        team=home,
        body={"name": "Renamed"},
        note="A personal space keeps its name while multi-team is on.",
    )
    pat.call("teams-after-rename", "GET", "/api/teams")


def main() -> None:
    if not sys.argv[1:]:
        sys.exit(__doc__)
    prepare_database()
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    settings = Settings(
        _env_file=None,
        app_env="test",
        session_secret="capture-secret-" + "x" * 30,
        cookie_secure=False,
        database_url=scratch_url,
        public_base_url="https://notes.example.test",
    )
    server, base = start_server(create_app(settings))
    engine = create_engine(scratch_url)
    try:
        run(base, seed(engine), engine)
    finally:
        server.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    main()
