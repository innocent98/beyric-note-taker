"""Drive the workspace foundation over real HTTP and save every exchange for the frontend guide.

    python scripts/capture_workspaces.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres

Same method as `capture_team_scoped_lists.py`: a scratch database with the real migrations, the real
app under uvicorn on a free local port, httpx for every call, every exchange saved to
`docs/fe-captures/workspaces/NN-name.json`.

Shows what the web builds against (docs/fe-integration-guide-workspaces.md): the active workspace
comes from `X-Team-Id` (no header is the person's default workspace: their only team, else their personal one), the lists, Ask, notifications and the
sidebar badges follow it, `GET /api/session` names the active workspace and the unread count of each
other one, and a meeting opened by id from another workspace answers with its own workspace so the
web can switch. The cast (placeholders): Ada owns team X ("Platform team"); Pat is in X and in Y
("Client Y", owned by Quinn) and has a personal workspace. No model is called by the tasks path; the
one model answer is a FAKE reply from this script (the route and its shape are real, the sentence is
not) and is labelled so in its capture. No mail leaves this machine.
"""

import json
import os
import re
import shutil
import socket
import sys
import threading
import time
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "fe-captures" / "workspaces"
DB_NAME = "notetaker_capture_workspaces"
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

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import services, teams  # noqa: E402
from app.accounts import create_user_defaults  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import (  # noqa: E402
    Decision,
    Meeting,
    Notification,
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


class FakeModel:
    """Stands in for OpenAI on `POST /api/ask`: cites the first passage it is sent. The route, the
    retrieval and the cites are real; the sentence is this script's, not a model's."""

    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    @staticmethod
    def _create(**kwargs):
        message = kwargs["messages"][1]["content"]
        ids = re.findall(r'<passage id="(P\d+)">', message)
        reply = {
            "lead": "Here is what was said (a fake model's sentence).",
            "found": bool(ids),
            "points": [{"text": "Pricing was discussed.", "passages": ids[:1]}] if ids else [],
        }
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(reply)), finish_reason="stop"
                )
            ],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
        )


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

        def meeting(owner: User, title: str, topic: str, **fields) -> Meeting:
            row = Meeting(
                owner_user_id=owner.id,
                url=MEET,
                platform="google_meet",
                native_id="abc-defg-hij",
                template_id=general[owner.id].id,
                status="ready",
                step="done",
                notes=f"## Summary\nWe talked about {topic} pricing.\n\n## Decisions\n- Decide {topic}.\n",
                transcript=f"Pat: We spoke about {topic} pricing.\nAda: Yes, {topic} pricing.\n",
                speaker_transcript=f"[Pat]: We spoke about {topic} pricing.\n[Ada]: Yes, {topic} pricing.",
                recorded_seconds=1260,
                title=title,
                **fields,
            )
            session.add(row)
            session.flush()
            session.add(
                Decision(meeting_id=row.id, text=f"Decide {topic}", position=0, fingerprint=title)
            )
            return row

        rows = {
            "pat_private": meeting(pat, "Pat private", "personal"),
            "pat_in_x": meeting(pat, "Pat shared to X", "platform", shared_team_id=x.id),
            "ada_in_x": meeting(ada, "Ada shared to X", "platform", shared_team_id=x.id),
            "pat_in_y": meeting(pat, "Pat shared to Y", "client", shared_team_id=y.id),
            "quinn_in_y": meeting(quinn, "Quinn shared to Y", "client", shared_team_id=y.id),
            "ada_private": meeting(ada, "Ada private", "secret"),
        }
        for key in ("pat_private", "pat_in_x", "pat_in_y"):  # the recorder's own review items
            session.add(
                ReviewItem(
                    meeting_id=rows[key].id,
                    owner_user_id=pat.id,
                    text=f"Check the figures in {key}",
                    position=0,
                    fingerprint=key,
                )
            )
            session.add(
                Notification(
                    user_id=pat.id,
                    kind="meeting.ready",
                    title=f"Notes ready: {key}",
                    meeting_id=rows[key].id,
                    dedupe_key=f"ready-{key}",
                )
            )
        session.add(
            Notification(
                user_id=pat.id,
                kind="usage.warning",
                severity="warning",
                title="You have used 80 percent of your hours",
                dedupe_key="hours-80",
            )
        )
        for team, title in ((x, "Book the room"), (x, "Order the cake"), (y, "Pay the invoice")):
            session.add(
                Task(
                    team_id=team.id,
                    title=title,
                    source="manual",
                    assigned_on=date(2026, 11, 3),
                    assignee_user_id=pat.id,
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
    pat, quinn = login(base, "Pat", PAT), login(base, "Quinn", QUINN)
    workspaces = (("personal", home), ("x", x), ("y", y))

    # ---- the session: the active workspace, and the unread count of every other one ----
    for name, team in workspaces:
        pat.call(
            f"session-{name}",
            "GET",
            "/api/session",
            team=team,
            note="`active_workspace` is the one named by the header; `other_workspaces` lists "
            "the rest with their unread count (the switcher's dot); `badges` and "
            "`unread_notifications` count the active one only.",
        )
    pat.call(
        "session-no-header",
        "GET",
        "/api/session",
        note="No header: the default workspace (Pat is in two teams: the personal one).",
    )
    quinn.call(
        "session-stale-team",
        "GET",
        "/api/session",
        team=x,
        note="Quinn is not in X: the session does not fail over it, it answers Quinn's personal "
        "workspace (every page reads this first).",
    )

    # ---- the lists follow the header ----
    for name, team in workspaces:
        pat.call(f"meetings-{name}", "GET", "/api/meetings", team=team)
    pat.call(
        "meetings-no-header",
        "GET",
        "/api/meetings",
        note="No header: the default workspace (Pat is in two teams: the personal one).",
    )
    pat.call(
        "meetings-team-not-mine",
        "GET",
        "/api/meetings",
        team=99999,
        note="A team the caller is not in (or that does not exist): 404, the same answer.",
    )
    for name, team in workspaces:
        pat.call(f"decisions-{name}", "GET", "/api/decisions", team=team)
        pat.call(f"review-{name}", "GET", "/api/review", team=team)
        pat.call(f"tasks-{name}", "GET", "/api/tasks", team=team)
        pat.call(f"notifications-{name}", "GET", "/api/notifications", team=team)

    # ---- a meeting opened by id from another workspace answers with its own ----
    pat.call(
        "meeting-from-another-workspace",
        "GET",
        f"/api/meetings/{ids['pat_in_x']}",
        team=y,
        note="Y is the active workspace but the meeting lives in X. It still opens (the header "
        "never turns a readable meeting into a 404) and names its workspace: switch to it and say "
        '"Switched to Platform team".',
    )
    pat.call(
        "meeting-in-active-workspace",
        "GET",
        f"/api/meetings/{ids['pat_private']}",
        team=home,
        note="`workspace` is the same one that is active here: nothing to switch.",
    )
    pat.call(
        "meeting-not-readable",
        "GET",
        f"/api/meetings/{ids['ada_private']}",
        team=x,
        note="Ada's private meeting: 404 whatever the header names.",
    )

    # ---- Ask searches the active workspace only ----
    for name, team in workspaces:
        pat.call(f"ask-scope-{name}", "GET", "/api/ask/scope", team=team)
    pat.call(
        "ask-tasks-x",
        "POST",
        "/api/ask",
        team=x,
        body={"question": "What are my open tasks?"},
        note="The tasks path (no model): only X's tasks assigned to Pat.",
    )
    pat.call(
        "ask-tasks-personal",
        "POST",
        "/api/ask",
        team=home,
        body={"question": "What are my open tasks?"},
        note="The personal workspace has none.",
    )
    for name, team in (("x", x), ("y", y)):
        pat.call(
            f"ask-answer-{name}",
            "POST",
            "/api/ask",
            team=team,
            body={"question": "What was said about pricing?"},
            note="The retrieval is real and searches this workspace's meetings only; the model "
            "is a FAKE (see this script's doc), so the sentence is not a model's.",
        )

    # ---- notifications: reading one workspace's leaves the others unread ----
    pat.call(
        "notifications-read-all-x",
        "POST",
        "/api/notifications/read-all",
        team=x,
        note="Marks X's notifications read only; the other workspaces stay unread.",
    )
    pat.call("session-after-read-x", "GET", "/api/session", team=home)


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
    app = create_app(settings)
    app.dependency_overrides[services.get_openai_client] = lambda: FakeModel()
    server, base = start_server(app)
    engine = create_engine(scratch_url)
    try:
        run(base, seed(engine), engine)
    finally:
        server.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    main()
