"""Drive the multi-team routes over real HTTP and save every exchange for the frontend guide.

    python scripts/capture_teams_api.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres

Same method as `capture_share_api.py`: a scratch database with the real migrations, the real app
under uvicorn on a free local port, httpx for every call, every exchange saved to
`docs/fe-captures/teams/NN-name.json`. `MULTI_TEAM_ENABLED=true` is set for THIS run only (it is
read from the environment before the app is imported); nothing here changes a default.

The cast (placeholders): Ada owns team X ("Platform team"), Pat is in X and in Y ("Client Y"),
Quinn owns Y and is not in X. Everyone also has a personal team. No model is called and no mail
leaves this machine (the meetings and their tasks are rows made by the script).

Not done over HTTP, because the app has no route for it: the purge of a deleted account (the worker
runs `deletion.purge_user`; the script calls it directly with no provider, then reads the meeting
again over HTTP), and joining a second team (stage 3's invitation flow; the script adds the
memberships to the database, as an accepted invitation would with the switch on).
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
OUT = ROOT / "docs" / "fe-captures" / "teams"
DB_NAME = "notetaker_capture_teams"
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

from app import deletion, teams  # noqa: E402
from app.accounts import create_user_defaults  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Meeting, Task, Team, TeamMember, Template, User  # noqa: E402
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

    def call(self, name, method, path, *, body=None, note=""):
        headers = {"X-CSRF-Token": self.csrf} if self.csrf and method != "GET" else {}
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
        general = session.scalars(
            select(Template).where(Template.owner_user_id == ada.id, Template.name == "General")
        ).one()
        ready = {"status": "ready", "step": "done"}

        def meeting(**fields) -> Meeting:
            row = Meeting(
                owner_user_id=ada.id,
                url=MEET,
                platform="google_meet",
                native_id="abc-defg-hij",
                template_id=general.id,
                **fields,
            )
            session.add(row)
            session.flush()
            return row

        def body(**extra) -> dict:
            return {
                **ready,
                "notes": NOTES,
                "transcript": TRANSCRIPT,
                "speaker_transcript": TRANSCRIPT,
                "recorded_seconds": 1260,
                **extra,
            }

        home = teams.personal_team(session, ada.id)
        meetings = {
            "private": meeting(**body()),
            "pats": meeting(**body(), title="Pat's own call"),
            "waiting": meeting(status="processing", step="notes"),
        }
        meetings["pats"].owner_user_id = pat.id
        meetings["pats"].template_id = session.scalars(
            select(Template.id).where(Template.owner_user_id == pat.id, Template.name == "General")
        ).one()
        for text_, who in (("Send the deck", pat), ("Book the room", ada)):
            session.add(
                Task(
                    team_id=home.id,
                    meeting_id=meetings["private"].id,
                    title=text_,
                    assignee_user_id=who.id,
                    source="ai",
                    ai_fingerprint=f"fp-{text_}",
                    quote=text_,
                    assigned_on=date(2026, 11, 3),
                    is_new=True,
                )
            )
        session.commit()
        return {
            "ids": {k: v.id for k, v in meetings.items()},
            "x": x.id,
            "y": y.id,
            "ada_home": home.id,
            "pat_home": teams.personal_team(session, pat.id).id,
            "ada_id": ada.id,
        }


def login(base: str, who: str, email: str) -> Recorder:
    handle = Recorder(base, who)
    handle.call(f"session-{who.lower()}", "GET", "/api/session")
    handle.call(
        f"login-{who.lower()}", "POST", "/api/session", body={"email": email, "password": PASSWORD}
    )
    return handle


def run(base: str, seeded: dict, engine) -> None:
    ids = seeded["ids"]
    private, pats, waiting = ids["private"], ids["pats"], ids["waiting"]
    x, y = seeded["x"], seeded["y"]
    anonymous = Recorder(base, "nobody")
    anonymous.call("session-anonymous", "GET", "/api/session")
    ada, pat, quinn = login(base, "Ada", ADA), login(base, "Pat", PAT), login(base, "Quinn", QUINN)

    # ---- the meeting before anything: private, no team ----
    ada.call(
        "detail-private",
        "GET",
        f"/api/meetings/{private}",
        note="A private meeting: `team` is null, `owned_by_team` false, `can_move` true (the switch is on for this run).",
    )
    ada.call(
        "tasks-before",
        "GET",
        "/api/tasks",
        note="Ada's own tasks: the two the meeting produced, in her personal team.",
    )
    pat.call(
        "tasks-pat-before",
        "GET",
        "/api/tasks",
        note="Pat sees none of them: the meeting is private.",
    )

    # ---- share: the team is named in the request ----
    ada.call(
        "share-old-client",
        "POST",
        f"/api/meetings/{private}/share",
        note="No body: what today's frontend sends. Ada has exactly one ordinary team (X), so it is the default.",
    )
    ada.call("unshare", "DELETE", f"/api/meetings/{private}/share")
    ada.call(
        "share-named",
        "POST",
        f"/api/meetings/{private}/share",
        body={"team_id": x},
        note="The team named in the request.",
    )
    pat.call(
        "tasks-pat-shared",
        "GET",
        "/api/tasks",
        note="After the share the tasks follow the meeting into X: Pat (a member of X) now sees both.",
    )
    ada.call(
        "share-not-my-team",
        "POST",
        f"/api/meetings/{private}/share",
        body={"team_id": y},
        note="Ada is not in Y: 404, the same answer as for a team that does not exist.",
    )
    ada.call(
        "share-personal-team",
        "POST",
        f"/api/meetings/{private}/share",
        body={"team_id": seeded["ada_home"]},
        note="Her personal team is not a place to share to.",
    )
    pat.call(
        "share-ambiguous",
        "POST",
        f"/api/meetings/{pats}/share",
        note="Pat is on two ordinary teams and sent no team: 422, never a guess. (Pat's own meeting.)",
    )
    pat.call("share-pat-to-y", "POST", f"/api/meetings/{pats}/share", body={"team_id": y})
    quinn.call(
        "quinn-reads-pats",
        "GET",
        f"/api/meetings/{pats}",
        note="Quinn (team Y only) reads Pat's meeting shared to Y.",
    )
    quinn.call(
        "quinn-cannot-read-adas", "GET", f"/api/meetings/{private}", note="Quinn is not in X: 404."
    )
    ada.call("unshare-for-move", "DELETE", f"/api/meetings/{private}/share")

    # ---- move ----
    ada.call(
        "move-not-ready",
        "POST",
        f"/api/meetings/{waiting}/move",
        body={"team_id": x},
        note="Only a finished meeting can be moved.",
    )
    ada.call(
        "move-not-my-team",
        "POST",
        f"/api/meetings/{private}/move",
        body={"team_id": y},
        note="Not a member of Y: 404.",
    )
    ada.call(
        "move-personal",
        "POST",
        f"/api/meetings/{private}/move",
        body={"team_id": seeded["ada_home"]},
    )
    ada.call(
        "move",
        "POST",
        f"/api/meetings/{private}/move",
        body={"team_id": x},
        note="Moved: the team owns it. `owned_by_team` true, `can_move` false, `can_move_back` true because Ada also owns X.",
    )
    ada.call(
        "move-again-same-team",
        "POST",
        f"/api/meetings/{private}/move",
        body={"team_id": x},
        note="The same team again: fine.",
    )
    with Session(engine) as session:
        other = make_team(session, session.get(User, seeded["ada_id"]), "Second team")
        other_id = other.id
    ada.call(
        "move-already-moved",
        "POST",
        f"/api/meetings/{private}/move",
        body={"team_id": other_id},
        note="Already the team's: another team is refused until it is moved back.",
    )
    ada.call(
        "share-moved",
        "POST",
        f"/api/meetings/{private}/share",
        body={"team_id": x},
        note="A moved meeting is not shared or unshared by hand.",
    )
    ada.call("unshare-moved", "DELETE", f"/api/meetings/{private}/share")
    pat.call(
        "moved-seen-by-member",
        "GET",
        f"/api/meetings/{private}",
        note="Pat (a plain member of X) reads it: `shared_by` names the recorder, every `can_*` is false, `can_move_back` false.",
    )
    pat.call("tasks-pat-moved", "GET", "/api/tasks", note="Its tasks are the team's.")
    pat.call(
        "moved-delete-by-member",
        "DELETE",
        f"/api/meetings/{private}",
        note="A plain member cannot change it: 404, like any meeting that is not theirs to change.",
    )
    pat.call(
        "move-back-by-member",
        "POST",
        f"/api/meetings/{private}/move-back",
        note="A reader who is not an owner or admin: 403.",
    )
    quinn.call("moved-seen-by-outsider", "GET", f"/api/meetings/{private}", note="Not in X: 404.")
    quinn.call("move-back-by-outsider", "POST", f"/api/meetings/{private}/move-back")
    with Session(engine) as session:
        session.add(
            TeamMember(
                team_id=x,
                user_id=session.scalars(select(User.id).where(User.email == QUINN)).one(),
                role="admin",
            )
        )
        session.commit()
    quinn.call(
        "moved-seen-by-new-admin",
        "GET",
        f"/api/meetings/{private}",
        note="After Quinn is made an admin of X by hand: they read it and `can_delete`/`can_regenerate` are true (the team's owners and admins change it), `can_move_back` true.",
    )
    with Session(engine) as session:
        session.execute(
            TeamMember.__table__.delete().where(
                TeamMember.team_id == x,
                TeamMember.user_id
                == session.scalars(select(User.id).where(User.email == QUINN)).one(),
            )
        )
        session.commit()

    # ---- move back ----
    ada.call(
        "move-back",
        "POST",
        f"/api/meetings/{private}/move-back",
        note="The owner of X gives it back: private to Ada again, tasks go to her personal team.",
    )
    ada.call(
        "move-back-never-moved",
        "POST",
        f"/api/meetings/{private}/move-back",
        note="Not moved now: 409.",
    )
    pat.call(
        "tasks-pat-after-move-back",
        "GET",
        "/api/tasks",
        note="Pat's tasks from this meeting are gone from X; nothing of it is exposed.",
    )

    # ---- the recorder leaves, and later deletes their account ----
    ada.call("move-again", "POST", f"/api/meetings/{private}/move", body={"team_id": x})
    with Session(engine) as session:
        ada_row = session.get(User, seeded["ada_id"])
        teams.transfer_owner(
            session, x, session.scalars(select(User.id).where(User.email == PAT)).one()
        )
        session.commit()
        teams.leave(session, ada_row, datetime.now(UTC), team_id=x)
        session.commit()
    ada.call(
        "left-team-detail",
        "GET",
        f"/api/meetings/{private}",
        note="Ada left X: she can no longer read the meeting she recorded.",
    )
    ada.call("left-team-delete", "DELETE", f"/api/meetings/{private}", note="...nor delete it.")
    pat.call(
        "left-team-still-the-teams",
        "GET",
        f"/api/meetings/{private}",
        note="Pat, now the owner of X, still has it; Ada's name is still shown (her account exists).",
    )
    with Session(engine) as session:
        deletion.purge_user(session, session.get(User, seeded["ada_id"]), object(), force=True)
    pat.call(
        "recorder-deleted-account",
        "GET",
        f"/api/meetings/{private}",
        note='Ada deleted her account: the team keeps the meeting, `shared_by` is "Former member", and no name or email of hers appears.',
    )
    pat.call(
        "recorder-deleted-move-back",
        "POST",
        f"/api/meetings/{private}/move-back",
        note="Nobody to give it back to: 409.",
    )


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
