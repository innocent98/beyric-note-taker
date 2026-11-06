"""Drive the Slack integration API over real HTTP and save every exchange for the frontend guide.

    python scripts/capture_integrations_api.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres

Creates a scratch database on the given server, runs the real migrations, starts the real app under
uvicorn and signs in as placeholder users. Each exchange is written to
`docs/fe-captures/integrations-w10/NN-name.json`, so every claim in
`docs/fe-integration-guide-integrations.md` can be re-derived by running this again.

What is real: the app, auth, CSRF, validation, roles, the database, migrations, the vault
(a throwaway key made here), serialisation, every error, and the post that follows a real
`workspace.run_extraction` (the message text is captured as built by `app/slack.py`).

What is NOT real, labelled in the files: **Slack itself.** `slack.post` (the one function that makes
the outbound call) is replaced by a recorder that answers like Slack would, or fails on request;
the HTTP layer of that call (pinned address, no redirect, status mapping) is covered by
`tests/test_integrations.py`, not by a capture. The webhook addresses are obviously fake
placeholders. The model that reads the notes is replaced by a canned reply.
"""

import base64
import json
import os
import socket
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "fe-captures" / "integrations-w10"
DB_NAME = "notetaker_capture_integrations"
ADMIN_EMAIL = "admin@example.test"
ADMIN_PASSWORD = "-".join(["capture", "admin", "placeholder"])
PEOPLE = {
    "dan@example.test": ("Dan Okoro", "-".join(["dan", "chosen", "password", "1"])),
    "chidi@example.test": ("Chidi Eze", "-".join(["chidi", "chosen", "password", "2"])),
}
HOOK = "https://hooks.slack.com/" + "/".join(
    ["services", "TFAKEFAKE", "BFAKEFAKE", "FAKEFAKEFAKEFAKEFAKEFAKE"]
)
SHOWN = "<fake-slack-webhook-url>"  # captures never hold even a placeholder-shaped webhook address

admin_url = sys.argv[1] if len(sys.argv) > 1 else ""
scratch_url = urlparse(admin_url)._replace(path=f"/{DB_NAME}").geturl()
os.environ["DATABASE_URL"] = scratch_url
os.environ["OWNER_EMAIL"] = ADMIN_EMAIL
os.environ["OWNER_PASSWORD"] = ADMIN_PASSWORD
os.environ["TOKEN_ENC_KEY"] = base64.b64encode(b"capture-throwaway-key-0123456789!"[:32]).decode()
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("SESSION_SECRET", "capture-" + "s" * 48)

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import accounts, slack, teams, workspace  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import AppState, Meeting, TeamMember, Template, User  # noqa: E402
from app.passwords import hash_password  # noqa: E402
from app.services import get_vexa  # noqa: E402

posted: list[dict] = []
behaviour = {"fail": None}


def fake_post(url, text_, **_):
    """Stands in for the outbound call to Slack: records it, or fails like Slack would."""
    posted.append({"url_is_the_fake_placeholder": url == HOOK, "text": text_})
    if behaviour["fail"]:
        raise slack.SlackError(behaviour["fail"])


slack.post = fake_post


class Recorder:
    count = 0
    masks: dict[str, str] = {}

    def __init__(self, base_url: str, who: str) -> None:
        self.http = httpx.Client(base_url=base_url, follow_redirects=False, timeout=300)
        self.csrf: str | None = None
        self.who = who

    @classmethod
    def mask(cls, token: str) -> str:
        return cls.masks.setdefault(token, f"<csrf-{chr(ord('A') + len(cls.masks))}>")

    def call(self, name, method, path, *, body=None, note=""):
        headers = {"X-CSRF-Token": self.csrf} if method != "GET" and self.csrf else {}
        response = self.http.request(method, path, json=body, headers=headers)
        data = response.json() if "json" in response.headers.get("content-type", "") else None
        if isinstance(data, dict) and "csrf_token" in data:
            self.csrf = data["csrf_token"]
        Recorder.count += 1
        shown = json.loads(json.dumps(data))
        if isinstance(shown, dict) and "csrf_token" in shown:
            shown["csrf_token"] = self.mask(shown["csrf_token"])
        record = {
            "note": note,
            "client": self.who,
            "request": {
                "method": method,
                "path": path,
                "headers": {k: self.mask(v) for k, v in headers.items()},
                "body": body,
            },
            "response": {"status": response.status_code, "body": shown},
        }
        (OUT / f"{Recorder.count:02d}-{name}.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False).replace(HOOK, SHOWN) + "\n"
        )
        print(f"{Recorder.count:02d}-{name:44} {method:6} {path:30} -> {response.status_code}")
        return response


def note_file(name, note, content):
    Recorder.count += 1
    (OUT / f"{Recorder.count:02d}-{name}.json").write_text(
        json.dumps({"note": note, "content": content}, indent=2, ensure_ascii=False, default=str)
        + "\n"
    )
    print(f"{Recorder.count:02d}-{name}")


def start_server(app):
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


class CannedModel:
    """Answers the extraction call with no refinements (the notes parser's tasks stand)."""

    def __init__(self):
        reply = json.dumps({"action_items": [], "review": [], "milestones": []})
        message = SimpleNamespace(content=reply, finish_reason="stop")
        usage = SimpleNamespace(prompt_tokens=700, completion_tokens=90)
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_: SimpleNamespace(
                    choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=usage
                )
            )
        )


NOTES = (
    "## Summary\nA short call.\n\n## Decisions\nNone noted\n\n"
    "## Action items\n- Dan: send the contract (by Friday)\n- Someone should book the venue\n\n"
    "## Open questions\nNone noted\n\n## Please check\nNone noted\n"
)


def run(base: str, engine) -> None:
    with Session(engine) as session:
        for email, (name, password) in PEOPLE.items():
            user = User(
                email=email, name=name, password_hash=hash_password(password),
                email_verified_at=datetime.now(UTC),
            )  # fmt: skip
            session.add(user)
            session.flush()
            accounts.add_user_defaults(session, user)
        state = session.get(AppState, 1) or AppState(id=1)
        state.password_signup_enabled, state.max_accounts = True, 10
        session.add(state)
        session.commit()
        dan = session.scalars(select(User).where(User.email == "dan@example.test")).one()
        chidi = session.scalars(select(User).where(User.email == "chidi@example.test")).one()
        team = teams.ensure_team(session, dan)
        # Chidi joins Dan's team as a plain member (his own one-person team is dropped).
        teams.ensure_team(session, chidi)
        mine = session.scalars(select(TeamMember).where(TeamMember.user_id == chidi.id)).one()
        mine.team_id = team.id
        mine.role = "member"
        session.commit()
        template_id = session.scalars(
            select(Template.id).where(Template.owner_user_id == dan.id, Template.name == "General")
        ).one()
        meeting = Meeting(
            owner_user_id=dan.id, url="https://example.test/m", platform="google_meet",
            native_id="nat-aaaa-bbb", template_id=template_id, status="ready", step="done",
            title="Planning call", transcript="[Dan]: Hello.", speaker_transcript="[Dan]: Hello.",
            notes=NOTES, shared_team_id=team.id,
        )  # fmt: skip
        team_id = team.id
        session.add(meeting)
        session.commit()
        meeting_id = meeting.id
    d, c = Recorder(base, "dan (owner)"), Recorder(base, "chidi (member)")
    for rec, email in ((d, "dan@example.test"), (c, "chidi@example.test")):
        rec.call("session", "GET", "/api/session")
        rec.call(
            "login", "POST", "/api/session", body={"email": email, "password": PEOPLE[email][1]}
        )
    path = "/api/integrations/slack"
    d.call("slack-not-connected", "GET", path, note="Nothing connected yet.")
    d.call(
        "slack-invalid-address",
        "PUT",
        path,
        body={"webhook_url": "https://example.test/not-slack"},
        note="422; nothing is posted or stored.",
    )
    behaviour["fail"] = "gone"
    d.call(
        "slack-refused-by-slack",
        "PUT",
        path,
        body={"webhook_url": HOOK},
        note="Slack answered 'no such webhook' (simulated): 502, nothing stored.",
    )
    behaviour["fail"] = None
    c.call(
        "slack-connect-as-member",
        "PUT",
        path,
        body={"webhook_url": HOOK},
        note="A member may not connect: 403.",
    )
    d.call(
        "slack-connect",
        "PUT",
        path,
        body={"webhook_url": HOOK},
        note="The address is sent once and never returned. One test message is posted (simulated Slack).",
    )
    d.call("slack-connected", "GET", path)
    c.call("slack-as-member", "GET", path, note="A member sees the state, `can_manage` false.")
    c.call("slack-disconnect-as-member", "DELETE", path, note="403.")
    with Session(engine) as session:
        meeting = session.get(Meeting, meeting_id)
        status = workspace.run_extraction(session, meeting, client=CannedModel())
    note_file(
        "post-after-extraction",
        f"workspace.run_extraction returned {status!r}; the post(s) Slack received (simulated), in order: the test messages from connecting (one attempt Slack refused, one it accepted), then the meeting's tasks.",
        posted,
    )
    d.call("slack-after-post", "GET", path, note="`last_posted_at` is now set.")
    behaviour["fail"] = "gone"
    with Session(engine) as session:
        meeting2 = Meeting(
            owner_user_id=session.scalars(select(User.id).where(User.email == "dan@example.test")).one(),
            url="https://example.test/m2", platform="google_meet", native_id="nat-cccc-ddd",
            template_id=template_id, status="ready", step="done", title="Second call",
            transcript="[Dan]: Hi.", speaker_transcript="[Dan]: Hi.", notes=NOTES, shared_team_id=team_id,
        )  # fmt: skip
        session.add(meeting2)
        session.commit()
        workspace.run_extraction(session, meeting2, client=CannedModel())
    behaviour["fail"] = None
    d.call(
        "slack-failing",
        "GET",
        path,
        note="A later post failed (simulated 'gone'): the card shows `status` failing and a fixed sentence. The notes and tasks were not affected.",
    )
    d.call("slack-disconnect", "DELETE", path, note="204; the stored address is deleted.")
    d.call("slack-disconnect-again", "DELETE", path, note="Safe to repeat: 204.")
    d.call("slack-after-disconnect", "GET", path)


def main() -> None:
    if not admin_url:
        sys.exit(__doc__)
    if OUT.exists():
        for old in OUT.glob("*.json"):
            old.unlink()
    OUT.mkdir(parents=True, exist_ok=True)
    prepare_database()
    settings = Settings(
        _env_file=None,
        app_env="test",
        session_secret="capture-secret-" + "x" * 30,
        cookie_secure=False,
        database_url=scratch_url,
        public_base_url="https://notes.example.test",
    )
    app = create_app(settings)
    app.dependency_overrides[get_vexa] = lambda: None
    server, base = start_server(app)
    engine = create_engine(scratch_url)
    try:
        run(base, engine)
    finally:
        server.should_exit = True


if __name__ == "__main__":
    main()
