# ruff: noqa: E501  (the notes in the captures are sentences)
"""Drive the workspace limit, lifecycle and gate routes over real HTTP and save every exchange for
the frontend guide (workspaces slice W4).

    python scripts/capture_workspaces_lifecycle.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres

Same method as `capture_teams_api.py`: a scratch database with the real migrations, the real app
under uvicorn on a free local port, httpx for every call, every exchange saved to
`docs/fe-captures/workspaces/NN-name.json`. `FEATURE_GATES_MODE=enforce` is set for THIS run only.

The cast (placeholders): Ada owns team X ("Platform team") and is on the 20-hour plan (the seeded
`plan_20h`, a subscription row the script writes); Pat is a member of X on Free and has recorded a
meeting he moved to X; Quinn is a stranger. No model is called: the Ask captured is the free "my
tasks" answer, which sits behind the same gate. No mail leaves this machine.

Not done live: the `make` gate (the recipes release is not on this branch), a plan lapsing by the
clock (the script sets the subscription's status the way the renewal pass would), and the
delete of a workspace holding a meeting whose recorder's account is gone (covered by
`tests/test_workspace_lifecycle.py`).
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
os.environ["FEATURE_GATES_MODE"] = "enforce"

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
    Meeting,
    PlanPrice,
    Subscription,
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

        adas = meeting(ada, general, title="Ada's planning call", shared_team_id=x.id)
        pats = meeting(pat, pat_general, title="Pat's client call", owner_team_id=x.id)
        session.commit()
        return {
            "x": x.id,
            "ada_id": ada.id,
            "pat_id": pat.id,
            "adas": adas.id,
            "pats": pats.id,
            "ada_home": teams.personal_team(session, ada.id).id,
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
    x = seeded["x"]
    ada, pat, quinn = login(base, "Ada", ADA), login(base, "Pat", PAT), login(base, "Quinn", QUINN)

    # ---- the plan limit ----
    pat.call(
        "pat-teams-free",
        "GET",
        "/api/teams",
        note="Pat is on Free: `max_owned_teams` 0, `can_create_more` 0, `limit_message` names the plan to upgrade to.",
    )
    pat.call(
        "pat-create-team-free",
        "POST",
        "/api/teams",
        body={"name": "Mine"},
        note="Free cannot own a team: 409 `team_limit`, the same sentence as `limit_message`.",
    )
    ada.call(
        "ada-teams-starter",
        "GET",
        "/api/teams",
        note="Ada is on the 20-hour plan: she owns 1 (X) of 3; `limit_message` is null while she may make more.",
    )
    made = ada.call(
        "ada-create-class",
        "POST",
        "/api/teams",
        body={"name": "Maths 101", "kind": "class"},
        note="`kind` `class`: the same workspace in a school's words. It counts toward the limit.",
    )
    ada.call(
        "ada-class-team",
        "GET",
        "/api/team",
        team=made.json()["id"],
        note="A class shares new meetings with its members by default (`share_new_meetings` true) and says `kind: class`.",
    )
    ada.call("ada-create-team", "POST", "/api/teams", body={"name": "Reading group"})
    ada.call(
        "ada-teams-at-limit",
        "GET",
        "/api/teams",
        note="Three owned of three: `can_create_more` 0 and the plain message, naming the plan above.",
    )
    ada.call(
        "ada-create-over-limit",
        "POST",
        "/api/teams",
        body={"name": "One too many"},
        note="Over the limit: 409 `team_limit` naming the plan to upgrade to.",
    )
    ada.call(
        "ada-create-bad-kind",
        "POST",
        "/api/teams",
        body={"name": "Odd", "kind": "school"},
        note="An unknown `kind` is a 422 validation error.",
    )

    # ---- the feature gate: a free member on a paid owner's workspace ----
    pat.call(
        "pat-ask-in-paid-workspace",
        "POST",
        "/api/ask",
        team=x,
        body={"question": "What are my open tasks?"},
        note="Pat is on Free but Ada, who owns X, has a plan: Ask passes (the free 'my tasks' answer sits behind the same gate, so no model is called).",
    )
    pat.call(
        "pat-ask-in-personal-workspace",
        "POST",
        "/api/ask",
        team=seeded["pat_home"],
        body={"question": "What are my open tasks?"},
        note="The same person in their own personal workspace: refused, 403 `plan_inactive`.",
    )
    set_plan_status(engine, seeded["ada_id"], "lapsed")
    pat.call(
        "pat-ask-owner-lapsed",
        "POST",
        "/api/ask",
        team=x,
        body={"question": "What are my open tasks?"},
        note="Ada's plan lapsed: Pat is refused again, 403 `plan_inactive`.",
    )
    ada.call(
        "ada-teams-lapsed",
        "GET",
        "/api/teams",
        note="A lapsed plan keeps every workspace: she still owns 3, `max_owned_teams` is 0 now, nothing is locked or deleted.",
    )
    ada.call(
        "ada-create-lapsed",
        "POST",
        "/api/teams",
        body={"name": "Another"},
        note="Only making more is refused: 409 `team_limit` (Free).",
    )
    set_plan_status(engine, seeded["ada_id"], "active")

    # ---- leaving, removing, deleting ----
    ada.call(
        "ada-delete-with-members",
        "DELETE",
        f"/api/teams/{x}",
        note="Pat is still in X: 409 `team_has_members`, nothing is deleted.",
    )
    pat.call(
        "pat-delete-not-owner",
        "DELETE",
        f"/api/teams/{x}",
        note="A member is not the owner: 403 `team_forbidden`.",
    )
    quinn.call(
        "quinn-delete-stranger",
        "DELETE",
        f"/api/teams/{x}",
        note="Not in X: 404, the same answer as for a team that does not exist.",
    )
    ada.call(
        "ada-delete-personal",
        "DELETE",
        f"/api/teams/{seeded['ada_home']}",
        note="The personal workspace cannot be deleted: 409 `personal_team`.",
    )
    pat.call(
        "pat-leave-personal",
        "DELETE",
        f"/api/team/members/{seeded['pat_id']}",
        team=seeded["pat_home"],
        note="...or left: 409 `personal_team`.",
    )
    ada.call(
        "ada-owner-leave",
        "DELETE",
        f"/api/team/members/{seeded['ada_id']}",
        team=x,
        note="An owner cannot leave: 409 `owner_cannot_leave` (the message now says she may delete it once she is alone).",
    )
    ada.call(
        "ada-remove-pat",
        "DELETE",
        f"/api/team/members/{seeded['pat_id']}",
        team=x,
        note="Removed: Pat loses access at once.",
    )
    pat.call(
        "pat-team-after-removal",
        "GET",
        "/api/team",
        team=x,
        note="404, as for any team he is not in.",
    )
    pat.call(
        "pat-meetings-after-removal",
        "GET",
        "/api/meetings",
        team=x,
        note="404 too: nothing of X is listed to him.",
    )
    pat.call(
        "pat-adas-meeting-after-removal",
        "GET",
        f"/api/meetings/{seeded['adas']}",
        note="Ada's meeting shared to X is not readable to him any more: 404.",
    )
    ada.call(
        "ada-pats-meeting-stays",
        "GET",
        f"/api/meetings/{seeded['pats']}",
        note="The meeting Pat MOVED to X belongs to X and stays with it, whoever recorded it.",
    )
    ada.call(
        "ada-delete-team-alone",
        "DELETE",
        f"/api/teams/{x}",
        note="Ada is alone in X now: 204. Its tasks, projects, series and integrations go with it.",
    )
    ada.call(
        "ada-adas-meeting-home",
        "GET",
        f"/api/meetings/{seeded['adas']}",
        note="Her own meeting went back to her personal workspace, private (`workspace_team_id` is her personal team).",
    )
    pat.call(
        "pat-pats-meeting-home",
        "GET",
        f"/api/meetings/{seeded['pats']}",
        note="The meeting Pat recorded went back to HIM, private, in his personal workspace: never deleted while its recorder has an account.",
    )
    ada.call(
        "ada-team-deleted",
        "GET",
        "/api/team",
        team=x,
        note="The deleted team is no longer anyone's: 404.",
    )
    ada.call("ada-teams-after", "GET", "/api/teams")


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
