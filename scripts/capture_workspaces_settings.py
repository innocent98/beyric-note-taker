"""Drive the workspace settings (templates, vocabulary, per-workspace prefs, born-shared meetings)
over real HTTP and save every exchange for the frontend guide.

    python scripts/capture_workspaces_settings.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres

Same method as `capture_workspaces.py`: a scratch database with the real migrations, the real app
under uvicorn on a free local port, httpx for every call, every exchange saved to
`docs/fe-captures/workspaces/settings-NN-name.json`.

What is NOT real: the bot provider. `POST /api/meetings` is answered with a stand-in provider that
takes the link and sends nothing, so no bot joins anything; the route, the placement and the
sharing rule are real. Nothing here calls a model and no mail leaves this machine. Every person and
address is a placeholder.

The cast: Ada owns team X ("Platform team", made through `teams.create_owned_team`, so it starts
with the seeded templates and "share new meetings" on); Pat is a member and Quinn an admin; each
has a personal workspace.
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
DB_NAME = "notetaker_capture_workspaces_settings"
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

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import services, teams  # noqa: E402
from app.accounts import create_user_defaults  # noqa: E402
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
        file = OUT / f"settings-{Recorder.count:02d}-{name}.json"
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


def run(base: str, seeded: dict, engine) -> None:
    x, ada_home, pat_home, quinn_home = (
        seeded["x"],
        seeded["ada_home"],
        seeded["pat_home"],
        seeded["quinn_home"],
    )
    ada, pat, quinn = login(base, "Ada", ADA), login(base, "Pat", PAT), login(base, "Quinn", QUINN)

    # ---- templates ----
    listed = pat.call(
        "templates-x-member",
        "GET",
        "/api/templates",
        team=x,
        note="A member reads the workspace's templates: the seeded defaults a new workspace "
        "starts with.",
    ).json()["templates"]
    pat.call(
        "templates-x-member-create-refused",
        "POST",
        "/api/templates",
        team=x,
        body={"name": "Mine", "instructions": "Do it."},
        note="A member cannot write: 403 `team_forbidden`.",
    )
    pat.call(
        "templates-x-member-delete-refused",
        "DELETE",
        f"/api/templates/{listed[0]['id']}",
        team=x,
        note="Nor delete.",
    )
    made = ada.call(
        "templates-x-owner-create",
        "POST",
        "/api/templates",
        team=x,
        body={"name": "Board meeting", "instructions": "List the votes and who proposed each."},
        note="The owner creates one: 201.",
    ).json()
    quinn.call(
        "templates-x-admin-update",
        "PUT",
        f"/api/templates/{made['id']}",
        team=x,
        body={
            "name": "Board meeting",
            "instructions": "List the votes, who proposed each, and why.",
        },
        note="An admin may edit it.",
    )
    quinn.call(
        "templates-x-duplicate-name",
        "POST",
        "/api/templates",
        team=x,
        body={"name": "Board meeting", "instructions": "again"},
        note="A name is unique in the workspace whoever wrote it: 400 `duplicate_name`.",
    )
    pat_personal = pat.call(
        "templates-personal-create",
        "POST",
        "/api/templates",
        team=pat_home,
        body={"name": "Pat only", "instructions": "Short notes."},
        note="In their own personal workspace a person writes freely.",
    ).json()
    pat.call(
        "templates-personal-list",
        "GET",
        "/api/templates",
        team=pat_home,
        note="The personal workspace's list is its own: it does not show X's.",
    )
    pat.call(
        "templates-other-workspace-404",
        "PUT",
        f"/api/templates/{pat_personal['id']}",
        team=x,
        body={"name": "Pat only", "instructions": "x"},
        note="A personal template addressed through X's header is not X's: 404, never 403.",
    )
    pat.call(
        "templates-not-my-team-404",
        "GET",
        "/api/templates",
        team=quinn_home,
        note="A workspace the caller is not in: 404 `not_found`.",
    )
    ada.call(
        "templates-copy-personal-into-x",
        "POST",
        "/api/templates/copy-from-personal",
        team=x,
        note="Owner copies their personal templates into X. Names already in X are skipped "
        "(`skipped`), never overwritten.",
    )
    ada.call(
        "templates-copy-again",
        "POST",
        "/api/templates/copy-from-personal",
        team=x,
        note="Again: nothing new, `copied` is 0.",
    )
    pat.call(
        "templates-copy-member-refused",
        "POST",
        "/api/templates/copy-from-personal",
        team=x,
        note="A member cannot copy into the workspace: 403.",
    )
    pat.call(
        "templates-copy-into-personal-refused",
        "POST",
        "/api/templates/copy-from-personal",
        team=pat_home,
        note="Nothing to copy into the personal workspace: 409 `personal_team`.",
    )

    # ---- vocabulary ----
    ada.call(
        "vocabulary-personal-add",
        "POST",
        "/api/vocabulary",
        team=ada_home,
        body={"term": "Beyric"},
        note="A term in Ada's personal workspace.",
    )
    term = ada.call(
        "vocabulary-x-add",
        "POST",
        "/api/vocabulary",
        team=x,
        body={"term": "Luran"},
        note="Owner adds a term to X's vocabulary.",
    ).json()
    pat.call(
        "vocabulary-x-member-list",
        "GET",
        "/api/vocabulary",
        team=x,
        note="A member reads it: it is used for every meeting recorded in X.",
    )
    pat.call(
        "vocabulary-x-member-add-refused",
        "POST",
        "/api/vocabulary",
        team=x,
        body={"term": "Quarterly"},
        note="A member cannot add: 403 `team_forbidden`.",
    )
    pat.call(
        "vocabulary-x-member-delete-refused",
        "DELETE",
        f"/api/vocabulary/{term['id']}",
        team=x,
        note="Nor remove.",
    )
    ada.call(
        "vocabulary-copy-personal-into-x",
        "POST",
        "/api/vocabulary/copy-from-personal",
        team=x,
        note="Owner copies the personal terms into X.",
    )
    ada.call(
        "vocabulary-copy-again",
        "POST",
        "/api/vocabulary/copy-from-personal",
        team=x,
        note="Again: `copied` 0, `skipped` 2.",
    )

    # ---- per-person-per-workspace prefs ----
    pat.call(
        "prefs-x-defaults",
        "GET",
        "/api/workspace-prefs",
        team=x,
        note="Pat never changed anything in X: their own defaults. `bot_name` null means none set "
        "here; `bot_name_default` is what then applies.",
    )
    pat.call(
        "prefs-x-put",
        "PUT",
        "/api/workspace-prefs",
        team=x,
        body={"auto_join_rule": "none", "bot_name": "Pat from Platform"},
        note="Change the rule and the note taker name for X only.",
    )
    pat.call(
        "prefs-personal-put",
        "PUT",
        "/api/workspace-prefs",
        team=pat_home,
        body={"auto_join_rule": "organizer", "skip_one_on_ones": True},
        note="A different rule in the personal workspace.",
    )
    pat.call("prefs-x-after", "GET", "/api/workspace-prefs", team=x, note="X is untouched by that.")
    pat.call(
        "prefs-personal-after", "GET", "/api/workspace-prefs", team=pat_home, note="Personal's own."
    )
    pat.call(
        "prefs-clear-bot-name",
        "PUT",
        "/api/workspace-prefs",
        team=x,
        body={"bot_name": None},
        note="null clears the workspace's own name; the rule stays.",
    )
    pat.call(
        "prefs-bad-name",
        "PUT",
        "/api/workspace-prefs",
        team=x,
        body={"bot_name": "see https://example.test"},
        note="A name with a web address: 422 `bot_name_invalid`.",
    )
    pat.call(
        "prefs-other-workspaces-template",
        "PUT",
        "/api/workspace-prefs",
        team=x,
        body={"calendar_template_id": pat_personal["id"]},
        note="A template of another workspace: 404 `not_found`.",
    )

    # ---- born shared (D4) ----
    template_x = ada.call("templates-x-for-meetings", "GET", "/api/templates", team=x).json()[
        "templates"
    ][0]["id"]
    mine = pat.call(
        "meeting-born-in-x",
        "POST",
        "/api/meetings",
        team=x,
        body={"url": MEET, "template_id": template_x},
        note="Pat (a member) records into X: born in X and shared with it from the first second "
        "(STAND-IN bot provider: no bot was sent).",
    ).json()
    ada.call(
        "meeting-born-in-x-ada-reads",
        "GET",
        f"/api/meetings/{mine['id']}",
        note="Ada reads Pat's meeting at once: 200.",
    )
    pat.call(
        "meeting-keep-private",
        "DELETE",
        f"/api/meetings/{mine['id']}/share",
        note="Pat presses 'keep private': the existing unshare. Still filed in X.",
    )
    ada.call(
        "meeting-born-in-x-ada-after-private",
        "GET",
        f"/api/meetings/{mine['id']}",
        note="Ada no longer reads it: 404.",
    )
    personal_template = pat.call(
        "templates-personal-for-meeting", "GET", "/api/templates", team=pat_home
    ).json()["templates"][0]["id"]
    pat.call(
        "meeting-born-personal",
        "POST",
        "/api/meetings",
        team=pat_home,
        body={"url": MEET.replace("hij", "klm"), "template_id": personal_template},
        note="A meeting born in the personal workspace stays private (no share).",
    )
    pat.call(
        "meeting-wrong-workspace-template",
        "POST",
        "/api/meetings",
        team=x,
        body={"url": MEET.replace("hij", "nop"), "template_id": personal_template},
        note="A personal template is not a choice in X: 400 `template_required`.",
    )
    with Session(engine) as session:
        rows = session.scalars(select(Meeting).order_by(Meeting.id)).all()
        for row in rows:
            print(
                "meeting", row.id, "workspace", row.workspace_team_id, "shared", row.shared_team_id
            )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("settings-*.json"):
        old.unlink()
    prepare_database()
    engine = create_engine(scratch_url)
    seeded = seed(engine)
    app = create_app()
    app.dependency_overrides[services.get_bot_provider] = lambda: StandInProvider()
    server, base = start_server(app)
    try:
        run(base, seeded, engine)
    finally:
        server.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    main()
