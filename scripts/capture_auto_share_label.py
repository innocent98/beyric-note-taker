"""Drive the saved auto-share rule and the meeting detail's `shared_by_auto_share` flag over real
HTTP and save every exchange for the frontend guide.

    PYTHONPATH=. python scripts/capture_auto_share_label.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres

Same method as `capture_workspaces_settings.py`: a scratch database with the real migrations, the
real app under uvicorn on a free local port, httpx for every call, every exchange saved to
`docs/fe-captures/workspaces/auto-share-NN-name.json`.

What is NOT real: the bot provider (a stand-in that takes the link and sends nothing) and the
notes. `POST /api/meetings` is the real route; the notes step is stood in for by writing a ready
meeting and then calling the one line the pipeline calls when notes are first written
(`teams.apply_share_default`, app/pipeline.py), so the rule runs exactly as it does there. Nothing
calls a model and no mail leaves this machine. Every person and address is a placeholder.

The cast: Ada owns team X ("Platform team"); Pat is a plain member of X and has chosen X as her
auto-share team; Pat's meetings are recorded into her Personal workspace.
"""

import json
import os
import socket
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "fe-captures" / "workspaces"
DB_NAME = "notetaker_capture_auto_share_label"
ADA = "owner@example.test"  # the account migration 0006 creates (a placeholder)
PAT, QUINN = "pat@example.test", "quinn@example.test"
PASSWORD = "-".join(["capture", "password", "placeholder"])
MEET = "https://meet.google.com/abc-defg-hij"


def database_urls(admin_url: str) -> tuple[str, str]:
    parsed = urlparse(admin_url)
    return admin_url, parsed._replace(path=f"/{DB_NAME}").geturl()


admin_url, scratch_url = database_urls(sys.argv[1] if len(sys.argv) > 1 else "")
os.environ["DATABASE_URL"] = scratch_url
os.environ["OWNER_EMAIL"] = ADA
os.environ["OWNER_PASSWORD"] = PASSWORD
os.environ["SKRIBBY_API_KEY"] = "capture-placeholder-key"  # the stand-in provider replaces it

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
from app.models import Meeting, TeamMember, User  # noqa: E402
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
        file = OUT / f"auto-share-{Recorder.count:02d}-{name}.json"
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


class StandInProvider:
    """Takes a link and sends nothing: no bot joins anything."""

    def send_bot(self, url, **options):
        return 4242

    def stop_bot(self, platform, native_id):
        return None


def seed(engine) -> dict:
    with Session(engine) as session:
        ada = session.scalars(select(User).where(User.email == ADA)).one()
        ada.name = "Ada Example"
        session.commit()
        teams.ensure_personal_team(session, ada)
        session.commit()
        pat = make_person(session, PAT, "Pat Example")
        quinn = make_person(session, QUINN, "Quinn Example")
        x = teams.create_owned_team(session, ada, "Platform team")
        session.add_all(
            [
                TeamMember(team_id=x.id, user_id=pat.id, role="member"),
                TeamMember(team_id=x.id, user_id=quinn.id, role="admin"),
            ]
        )
        session.commit()
        return {
            "x": x.id,
            "ada_home": teams.personal_team(session, ada.id).id,
            "pat_home": teams.personal_team(session, pat.id).id,
            "quinn_home": teams.personal_team(session, quinn.id).id,
        }


def login(base: str, who: str, email: str) -> Recorder:
    handle = Recorder(base, who)
    handle.call(f"session-{who.lower()}", "GET", "/api/session")
    handle.call(
        f"login-{who.lower()}", "POST", "/api/session", body={"email": email, "password": PASSWORD}
    )
    return handle


def finish_notes(engine, meeting_id: int) -> None:
    """What the pipeline does when notes are first written, minus the model: the meeting becomes
    ready with notes, then the one call it makes for the share default."""
    with Session(engine) as session:
        meeting = session.get(Meeting, meeting_id)
        meeting.notes = "## Summary\nA short planning call.\n"
        meeting.status, meeting.step = "ready", "done"
        teams.apply_share_default(session, meeting)
        session.commit()


def run(base: str, seeded: dict, engine) -> None:
    x, pat_home = seeded["x"], seeded["pat_home"]
    ada, pat = login(base, "Ada", ADA), login(base, "Pat", PAT)
    template = pat.call("templates-personal", "GET", "/api/templates", team=pat_home).json()[
        "templates"
    ][0]["id"]
    ada_template = ada.call("templates-x", "GET", "/api/templates", team=x).json()["templates"][0][
        "id"
    ]

    pat.call(
        "setting-before",
        "GET",
        "/api/settings/auto-share",
        note="Pat has not chosen a team: `team_id` null.",
    )
    pat.call(
        "setting-set",
        "PUT",
        "/api/settings/auto-share",
        body={"team_id": x},
        note="Pat chooses Platform team (X): the saved rule.",
    )

    # A meeting recorded into the Personal workspace; the rule runs when its notes are written.
    personal = pat.call(
        "meeting-personal-created",
        "POST",
        "/api/meetings",
        team=pat_home,
        body={"url": MEET, "template_id": template},
        note="Pat records into her Personal workspace (STAND-IN bot): private, filed in Personal.",
    ).json()
    pat.call(
        "meeting-personal-before-notes",
        "GET",
        f"/api/meetings/{personal['id']}",
        note="Before the notes are written: nothing shared, `shared_by_auto_share` false.",
    )
    finish_notes(engine, personal["id"])
    pat.call(
        "meeting-personal-after-rule-recorder",
        "GET",
        f"/api/meetings/{personal['id']}",
        note="Notes written, the rule ran: shared with X, filed in X, and the recorder is told "
        "why: `shared_by_auto_share` true. `workspace` is X: the meeting left Personal.",
    )
    ada.call(
        "meeting-personal-after-rule-teammate",
        "GET",
        f"/api/meetings/{personal['id']}",
        note="Ada (a teammate) reads it: `shared_by_auto_share` is false for anyone but the "
        "recorder; `shared_by` names Pat.",
    )
    pat.call(
        "meeting-list-after-rule",
        "GET",
        "/api/meetings",
        team=x,
        note="The Meetings list of X now holds it. The list rows carry no `shared_by_auto_share` "
        "(it is on the detail only).",
    )
    pat.call(
        "meeting-unshare",
        "DELETE",
        f"/api/meetings/{personal['id']}/share",
        note="Pat presses Stop sharing (the existing unshare): private, flag false.",
    )
    pat.call(
        "meeting-share-by-hand",
        "POST",
        f"/api/meetings/{personal['id']}/share",
        body={"team_id": x},
        note="Pat shares it again by hand: shared, but `shared_by_auto_share` false: her act now, "
        "not the rule's.",
    )

    # Born shared by the workspace's own default: the rule did not do it.
    born = ada.call(
        "meeting-born-shared-created",
        "POST",
        "/api/meetings",
        team=x,
        body={"url": MEET.replace("hij", "klm"), "template_id": ada_template},
        note="Ada records into X, which shares by default: born shared by the workspace.",
    ).json()
    ada.call(
        "meeting-born-shared-recorder",
        "GET",
        f"/api/meetings/{born['id']}",
        note="Shared, `shared_by_auto_share` false (Ada has no rule; the workspace did it).",
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("auto-share-*.json"):
        old.unlink()
    prepare_database()
    engine = create_engine(scratch_url)
    seeded = seed(engine)
    settings = Settings(
        _env_file=None,
        app_env="test",
        session_secret="capture-secret-" + "x" * 30,
        cookie_secure=False,
        database_url=scratch_url,
        public_base_url="https://notes.example.test",
        skribby_api_key="capture-placeholder-key",
    )
    app = create_app(settings)
    app.dependency_overrides[services.get_bot_provider] = lambda: StandInProvider()
    server, base = start_server(app)
    try:
        run(base, seeded, engine)
    finally:
        server.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    main()
