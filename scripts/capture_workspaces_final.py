# ruff: noqa: E501  (the notes in the captures are sentences)
"""Drive the recipes-in-workspaces routes over real HTTP and save every exchange for the frontend
guide (workspaces final integration, W5).

    PYTHONPATH=. python scripts/capture_workspaces_final.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres

Same method as `capture_workspaces_lifecycle.py`: a scratch database with the real migrations, the
real app under uvicorn on a free local port, httpx for every call, every exchange saved to
`docs/fe-captures/workspaces-final/NN-name.json`. `FEATURE_GATES_MODE=enforce` is set for THIS run only.

What it shows: the drafts list, the recipe library and the team brief follow the active workspace;
sharing a draft; a free member making a draft from a workspace whose owner has a plan, and the
refusals; the meeting picker narrowed to the workspace; what leaving a workspace does to a shared
draft. The cast (placeholders): Ada owns team X ("Platform team") on the 20-hour plan; Pat is a
member of X on Free; Quinn is a stranger. The drafts are written straight into the database as
finished (no model is called, and the draft worker is not running, so a draft Pat asks for stays
`writing`). No mail leaves this machine.

Not done live: a draft actually written by the model for a member of a paid workspace (the gate is
shown by the 202 the request answers; the worker is the recipes release's, proved by its own tests).
"""

import json
import os
import shutil
import socket
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "fe-captures" / "workspaces-final"
DB_NAME = "notetaker_capture_workspaces_final"
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
os.environ["FEATURE_GATES_MODE"] = "enforce"
# The delete route builds the bot provider (a team may hold a meeting whose recording must go); no
# meeting of this cast needs it, so a placeholder key is never used.
os.environ["SKRIBBY_API_KEY"] = "capture-placeholder-not-a-key"

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
    Document,
    DocumentMeeting,
    Meeting,
    PlanPrice,
    Recipe,
    Subscription,
    Team,
    TeamBrief,
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


def set_plan_status(engine, user_id: int, status: str) -> None:
    """What the renewal pass does to a plan that was not paid: `lapsed` (or `active` again)."""
    with Session(engine) as session:
        sub = session.scalars(select(Subscription).where(Subscription.user_id == user_id)).one()
        sub.status = status
        session.commit()


def seed(engine) -> dict:
    with Session(engine) as session:
        ada = session.scalars(select(User).where(User.email == ADA)).one()
        ada.name = "Ada Example"
        # The account migration 0006 makes is an admin, and an admin always passes the gates.
        ada.is_admin = False
        session.commit()
        teams.ensure_personal_team(session, ada)
        session.commit()
        pat = make_person(session, PAT, "Pat Example")
        make_person(session, QUINN, "Quinn Example")
        x = make_team(session, ada, "Platform team", pat)
        now = datetime.now(UTC)
        price = session.scalars(select(PlanPrice).where(PlanPrice.plan_code == "plan_20h")).first()
        session.add(
            Subscription(
                user_id=ada.id,
                payer_user_id=ada.id,
                plan_code="plan_20h",
                price_id=price.id,
                status="active",
                currency=price.currency,
                provider="flutterwave",
                current_period_start=now - timedelta(days=1),
                current_period_end=now + timedelta(days=29),
            )
        )
        general = session.scalars(
            select(Template).where(Template.owner_user_id == ada.id, Template.name == "General")
        ).one()
        pat_general = session.scalars(
            select(Template).where(Template.owner_user_id == pat.id, Template.name == "General")
        ).one()

        def meeting(owner: User, template: Template, **fields) -> Meeting:
            row = Meeting(
                owner_user_id=owner.id,
                url=MEET,
                platform="google_meet",
                native_id="abc-defg-hij",
                template_id=template.id,
                status="ready",
                step="done",
                notes=NOTES,
                transcript=TRANSCRIPT,
                speaker_transcript=TRANSCRIPT,
                recorded_seconds=1260,
                **fields,
            )
            session.add(row)
            session.flush()
            return row

        ada_home = teams.personal_team(session, ada.id)
        pat_home = teams.personal_team(session, pat.id)
        in_x = meeting(
            ada,
            general,
            title="Platform planning call",
            shared_team_id=x.id,
            workspace_team_id=x.id,
        )
        ada_private = meeting(
            ada, general, title="Ada's private call", workspace_team_id=ada_home.id
        )
        pat_private = meeting(
            pat, pat_general, title="Pat's own call", workspace_team_id=pat_home.id
        )
        session.add(
            TeamBrief(team_id=x.id, text="Our prices: audit 50,000 naira, retainer 120,000 naira.")
        )
        session.add(
            Recipe(
                owner_user_id=ada.id,
                team_id=x.id,
                name="Platform weekly recap",
                instruction="Write the recap the platform team sends on Friday.",
                output_type="document",
            )
        )
        session.flush()

        def draft(title: str, team: Team, source: Meeting) -> Document:
            doc = Document(
                owner_user_id=ada.id,
                team_id=team.id,
                title=title,
                output_type="document",
                status="ready",
                recipe_key="follow_up_email",
                blocks=[
                    {
                        "type": "paragraph",
                        "text": "Thanks for the call. We agreed to ship the checkout on Friday.",
                        "sources": [],
                    }
                ],
            )
            session.add(doc)
            session.flush()
            session.add(DocumentMeeting(document_id=doc.id, meeting_id=source.id))
            return doc

        x_draft = draft("Platform follow-up", x, in_x)
        home_draft = draft("Ada's own follow-up", ada_home, ada_private)
        session.commit()
        return {
            "x": x.id,
            "ada_id": ada.id,
            "pat_id": pat.id,
            "ada_home": ada_home.id,
            "pat_home": pat_home.id,
            "in_x": in_x.id,
            "pat_private": pat_private.id,
            "x_draft": x_draft.id,
            "home_draft": home_draft.id,
        }


def login(base: str, who: str, email: str) -> Recorder:
    handle = Recorder(base, who)
    handle.call(f"session-{who.lower()}", "GET", "/api/session")
    handle.call(
        f"login-{who.lower()}", "POST", "/api/session", body={"email": email, "password": PASSWORD}
    )
    return handle


def run(base: str, seeded: dict, engine) -> None:
    x = seeded["x"]
    ada, pat, quinn = login(base, "Ada", ADA), login(base, "Pat", PAT), login(base, "Quinn", QUINN)

    # ---- the drafts list follows the active workspace ----
    ada.call(
        "ada-documents-x",
        "GET",
        "/api/documents",
        team=x,
        note="Her own drafts made in X only: the follow-up of the platform call. Her personal draft is not here.",
    )
    ada.call(
        "ada-documents-personal",
        "GET",
        "/api/documents",
        team=seeded["ada_home"],
        note="In her personal workspace: the other draft. Nothing of X.",
    )
    ada.call(
        "ada-documents-no-header",
        "GET",
        "/api/documents",
        note="No header: her default workspace. She is in her personal workspace and in one ordinary team (X), so the default is X (the same list as the first call).",
    )
    ada.call(
        "ada-documents-by-meeting",
        "GET",
        f"/api/documents?meeting_id={seeded['in_x']}",
        team=seeded["ada_home"],
        note="Asking for the drafts of ONE meeting is about that meeting, not a workspace: the draft made from the X meeting is listed even with the personal header.",
    )
    quinn.call(
        "quinn-documents-x",
        "GET",
        "/api/documents",
        team=x,
        note="A stranger naming X: 404 `not_found`, the same answer as a team that does not exist.",
    )

    # ---- sharing ----
    ada.call(
        "ada-share-x-draft",
        "POST",
        f"/api/documents/{seeded['x_draft']}/share",
        team=seeded["ada_home"],
        note="Shared with the team it was MADE in (X), whatever header is sent: `shared_with` and `team` name X.",
    )
    ada.call(
        "ada-share-personal-draft",
        "POST",
        f"/api/documents/{seeded['home_draft']}/share",
        note="Made in her personal workspace: nobody to share with, 409 `personal_team`.",
    )
    pat.call(
        "pat-documents-shared-x",
        "GET",
        "/api/documents?shared=true",
        team=x,
        note="Pat sees the shared draft in X: `access` is `shared`, `can_edit` false, the creator's name is shown.",
    )
    pat.call(
        "pat-documents-shared-personal",
        "GET",
        "/api/documents?shared=true",
        team=seeded["pat_home"],
        note="In his personal workspace the same call lists nothing: a share belongs to the workspace it was made for.",
    )
    pat.call(
        "pat-documents-own-x",
        "GET",
        "/api/documents",
        team=x,
        note="His own drafts in X: none yet.",
    )
    pat.call(
        "pat-open-shared-draft",
        "GET",
        f"/api/documents/{seeded['x_draft']}",
        team=seeded["pat_home"],
        note="Opening ONE draft is by who may read it: Pat reads the shared draft whichever workspace is active.",
    )

    # ---- recipes and the brief are the workspace's ----
    pat.call(
        "pat-recipes-x",
        "GET",
        "/api/recipes",
        team=x,
        note="The library in X: the starters and X's own recipe ('Platform weekly recap').",
    )
    pat.call(
        "pat-recipes-personal",
        "GET",
        "/api/recipes",
        team=seeded["pat_home"],
        note="In his personal workspace: the starters, and none of X's recipes.",
    )
    pat.call("pat-brief-x", "GET", "/api/briefs", team=x, note="Members read the team brief.")
    pat.call(
        "pat-brief-x-write",
        "PUT",
        "/api/briefs",
        team=x,
        body={"text": "Pat's edit"},
        note="A member may not write it: 403 `team_forbidden`.",
    )
    ada.call(
        "ada-brief-x-write",
        "PUT",
        "/api/briefs",
        team=x,
        body={"text": "Our prices: audit 55,000 naira, retainer 120,000 naira."},
        note="An owner may.",
    )
    pat.call(
        "pat-brief-personal",
        "GET",
        "/api/briefs",
        team=seeded["pat_home"],
        note="His personal workspace has a brief of its own (empty): not X's.",
    )

    # ---- the meeting picker ----
    pat.call(
        "pat-picker-x",
        "POST",
        "/api/recipes/picker/resolve",
        team=x,
        body={"from_day": "2020-01-01", "to_day": "2099-12-31"},
        note="A date range is the Meetings list's own rule: in X, the meeting shared to X.",
    )
    pat.call(
        "pat-picker-personal",
        "POST",
        "/api/recipes/picker/resolve",
        team=seeded["pat_home"],
        body={"from_day": "2020-01-01", "to_day": "2099-12-31"},
        note="In his personal workspace: only his own call, none of X's.",
    )

    # ---- Make: the gate follows the workspace the meetings live in ----
    pat.call(
        "pat-make-in-paid-workspace",
        "POST",
        "/api/documents",
        team=x,
        body={"recipe_key": "follow_up_email", "meeting_ids": [seeded["in_x"]]},
        note="Pat is on Free, but the meeting is in X and Ada, who owns X, has a plan: the request is accepted, 202 `writing`.",
    )
    pat.call(
        "pat-make-own-meeting-free",
        "POST",
        "/api/documents",
        team=seeded["pat_home"],
        body={"recipe_key": "follow_up_email", "meeting_ids": [seeded["pat_private"]]},
        note="His own meeting in his own workspace: refused, 403 `plan_inactive`.",
    )
    set_plan_status(engine, seeded["ada_id"], "lapsed")
    pat.call(
        "pat-make-owner-lapsed",
        "POST",
        "/api/documents",
        team=x,
        body={"recipe_key": "follow_up_email", "meeting_ids": [seeded["in_x"]]},
        note="Ada's plan lapsed: refused again, 403 `plan_inactive`.",
    )
    set_plan_status(engine, seeded["ada_id"], "active")

    # ---- leaving ends a share at once ----
    ada.call(
        "ada-remove-pat",
        "DELETE",
        f"/api/team/members/{seeded['pat_id']}",
        team=x,
        note="Pat is removed from X.",
    )
    pat.call(
        "pat-documents-shared-after-removal",
        "GET",
        "/api/documents?shared=true",
        team=x,
        note="404 `not_found`: X is no longer his.",
    )
    pat.call(
        "pat-open-shared-draft-after-removal",
        "GET",
        f"/api/documents/{seeded['x_draft']}",
        note="The shared draft is gone for him at once: 404.",
    )
    pat.call(
        "pat-recipes-x-after-removal",
        "GET",
        "/api/recipes",
        team=x,
        note="X's recipes too: 404.",
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
