"""Drive Suggested tasks over real HTTP and save every exchange for the frontend guide.

    python scripts/capture_suggested_tasks_api.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres

The argument is any Postgres server the script may create a scratch database on. It runs the real
migrations there, starts the real app under uvicorn and signs in as placeholder users. Each exchange
is written to `docs/fe-captures/suggested-tasks/NN-name.json`, so every claim in
`docs/fe-integration-guide-suggested-tasks.md` can be re-derived by running this again.

What is real: the app, auth, CSRF, validation, roles and visibility, the database, migrations,
serialisation, every error, **the worker's extraction step** (`workspace.process_next`, the very
function the worker calls, with the real checks on the model's answer and the real reconcile), the
rendering and queueing of the assignee mail (`mail.deliver_pending`, the real builder, the real
outbox).

What is NOT real, and is labelled where it matters:
* **The model call.** This session had no OpenAI key. The reply is a **stand-in written for this
  capture** (`MODEL_REPLY` below), shaped as `extract-v2` asks (a `confidence` per action item and a
  `reason` for a medium or low one) and served by a stand-in client; so *which* items the real
  model rates unclear, and the words of its reasons, are not captured. Everything done with the
  reply is real. One reason in it carries a web address on purpose, to show that the check drops it.
* **The mail provider.** The message is built and handed to a capturing mailer instead of Resend.
* **Team membership.** The teammates are placed on the owner's team by the script, in the database.
* The recording, transcription and speaker steps and the bot: the meeting rows are inserted as the
  pipeline leaves them (`status ready`, notes and speaker transcript in place).
Nobody's real meeting or address is involved; every name and address is a placeholder.

Secrets in the files are masked: CSRF tokens become `<csrf-A>`, `<csrf-B>` in order of first
appearance and cookies `<cookie>`. The passwords are placeholders built at run time.
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
from types import SimpleNamespace
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "fe-captures" / "suggested-tasks"
DB_NAME = "notetaker_capture_suggested"
ADMIN_EMAIL = "admin@example.test"  # created by migration 0006 from these (placeholders)
ADMIN_PASSWORD = "-".join(["capture", "admin", "placeholder"])
PEOPLE = {  # email -> (name, role on Dan's team, password); all placeholders
    "dan@example.test": ("Dan Okoro", "owner", "-".join(["dan", "chosen", "password", "1"])),
    "chidi@example.test": ("Chidi Eze", "member", "-".join(["chidi", "chosen", "password", "2"])),
    "amina@example.test": ("Amina Bello", "admin", "-".join(["amina", "chosen", "password", "3"])),
    "eve@example.test": ("Eve Outsider", None, "-".join(["eve", "chosen", "password", "4"])),
}

TRANSCRIPT = (
    "[Dan]: Okay, the checkout page first. Chidi, can you finish it before Friday?\n"
    "[Chidi]: Yes, I'll finish the checkout page before Friday.\n"
    "[Amina]: We should look into the analytics setup at some point, there is no rush.\n"
    "[Chidi]: I can help with the demo if you need it.\n"
    "[Dan]: Someone should look into the invoice export, it keeps failing.\n"
    "[Dan]: I'll book the venue myself this week."
)
NOTES = """\
## Summary
Checkout first, a few loose ends.

## Decisions
- Checkout ships before Friday.

## Action items
- Chidi: finish the checkout page (by Friday)
- Amina: look into the analytics setup
- Chidi: help with the demo
- Unassigned: look into the invoice export
- Dan: book the venue

## Open questions
None noted

## Please check
None noted
"""
MODEL_REPLY = {  # STAND-IN written for this capture, not a recorded model answer
    "action_items": [
        {"index": 0, "title": "Finish the checkout page", "spoken_owner": "Chidi",
         "due_phrase": "before Friday", "due_date": "2026-11-06",
         "quote": "Yes, I'll finish the checkout page before Friday.",
         "confidence": "high", "reason": None},
        {"index": 1, "title": "Look into the analytics setup", "spoken_owner": "Amina",
         "due_phrase": None, "due_date": None,
         "quote": "We should look into the analytics setup at some point, there is no rush.",
         "confidence": "medium",
         "reason": 'Raised as something to do "at some point", with no commitment or date.'},
        {"index": 2, "title": "Help with the demo", "spoken_owner": "Chidi",
         "due_phrase": None, "due_date": None,
         "quote": "I can help with the demo if you need it.",
         "confidence": "low", "reason": 'Offered on a condition: "if you need it".'},
        {"index": 3, "title": "Look into the invoice export", "spoken_owner": None,
         "due_phrase": None, "due_date": None,
         "quote": "Someone should look into the invoice export, it keeps failing.",
         "confidence": "low",
         "reason": "No one took this on. See https://invoices.example.test/why for details."},
        {"index": 4, "title": "Book the venue", "spoken_owner": "Dan",
         "due_phrase": "this week", "due_date": "2026-11-06",
         "quote": "I'll book the venue myself this week.",
         "confidence": "high", "reason": None},
    ],
    "review": [],
    "milestones": [],
}  # fmt: skip

admin_url = sys.argv[1] if len(sys.argv) > 1 else ""
scratch_url = urlparse(admin_url)._replace(path=f"/{DB_NAME}").geturl()
os.environ["DATABASE_URL"] = scratch_url  # read by app.db when it is imported below
os.environ["OWNER_EMAIL"] = ADMIN_EMAIL
os.environ["OWNER_PASSWORD"] = ADMIN_PASSWORD
os.environ.setdefault("APP_ENV", "development")

import httpx  # noqa: E402
import openai  # noqa: E402,F401
import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import accounts, mail, teams, workspace  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import MailOutbox, Meeting, Task, TeamMember, Template, User  # noqa: E402
from app.passwords import hash_password  # noqa: E402
from app.services import get_vexa  # noqa: E402


class Recorder:
    """A logical client: a cookie jar, its CSRF token, and a numbered file for each call."""

    count = 0
    masks: dict[str, str] = {}

    def __init__(self, base_url: str, who: str) -> None:
        self.http = httpx.Client(base_url=base_url, follow_redirects=False, timeout=300)
        self.csrf: str | None = None
        self.who = who

    @classmethod
    def mask(cls, token: str) -> str:
        return cls.masks.setdefault(token, f"<csrf-{chr(ord('A') + len(cls.masks))}>")

    def call(self, name, method, path, *, body=None, csrf="session", note=""):
        """`csrf`: "session" sends the current token, None sends none, any other string is sent."""
        headers = {}
        token = (None if method == "GET" else self.csrf) if csrf == "session" else csrf
        if token:
            headers["X-CSRF-Token"] = token
        had_cookie = "session" in self.http.cookies
        response = self.http.request(method, path, json=body, headers=headers)
        content_type = response.headers.get("content-type", "")
        data = response.json() if "json" in content_type else (response.text or None)
        if isinstance(data, dict) and "csrf_token" in data:
            self.csrf = data["csrf_token"]
        Recorder.count += 1
        sent = {k: (self.mask(v) if k == "X-CSRF-Token" else v) for k, v in headers.items()}
        if had_cookie:
            sent["Cookie"] = "session=<cookie>"
        kept = {
            k: response.headers[k]
            for k in ("content-type", "cache-control", "allow")
            if k in response.headers
        }
        shown_body = (
            {**body, "password": "<password placeholder>"}
            if isinstance(body, dict) and "password" in body
            else body
        )
        shown = json.loads(json.dumps(data))
        if isinstance(shown, dict) and "csrf_token" in shown:
            shown["csrf_token"] = self.mask(shown["csrf_token"])
        record = {
            "note": note,
            "client": self.who,
            "request": {"method": method, "path": path, "headers": sent, "body": shown_body},
            "response": {"status": response.status_code, "headers": kept, "body": shown},
        }
        (OUT / f"{Recorder.count:02d}-{name}.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False) + "\n"
        )
        print(f"{Recorder.count:02d}-{name:46} {method:6} {path:40} -> {response.status_code}")
        return response


def note_file(name: str, note: str, content) -> None:
    """Save something that is not an HTTP exchange (a database row, a script step)."""
    Recorder.count += 1
    record = {"note": note, "content": content}
    (OUT / f"{Recorder.count:02d}-{name}.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False, default=str) + "\n"
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


class StandInModel:
    """Serves MODEL_REPLY (see the module docstring)."""

    def __init__(self, reply):
        self.reply = reply
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(self.reply)), finish_reason="stop"
                )
            ],
            usage=SimpleNamespace(prompt_tokens=1184, completion_tokens=702),
        )


class CapturingMailer:
    def __init__(self):
        self.sent = []

    def send(self, message, key):
        self.sent.append(message)


def make_people(session):
    for email, (name, _role, password) in PEOPLE.items():
        user = User(
            email=email,
            name=name,
            password_hash=hash_password(password),
            email_verified_at=datetime.now(UTC),
        )
        session.add(user)
        session.flush()
        accounts.add_user_defaults(session, user)
    session.commit()
    dan_team = teams.team_of(
        session, session.scalars(select(User.id).where(User.email == "dan@example.test")).one()
    )
    for email in ("chidi@example.test", "amina@example.test"):  # invites are W3
        uid = session.scalars(select(User.id).where(User.email == email)).one()
        session.execute(text("delete from team_members where user_id = :u"), {"u": uid})
        session.execute(text("delete from teams where owner_user_id = :u"), {"u": uid})
        session.add(TeamMember(team_id=dan_team.id, user_id=uid, role=PEOPLE[email][1]))
    session.commit()
    return session.scalars(
        select(Template.id)
        .join(User, User.id == Template.owner_user_id)
        .where(User.email == "dan@example.test", Template.name == "General")
    ).one()


def add_meeting(session, template_id, owner_email, **fields):
    owner = session.scalars(select(User).where(User.email == owner_email)).one()
    values = dict(
        owner_user_id=owner.id, url="https://example.test/meeting", platform="google_meet",
        native_id="nat-aaaa-bbb", template_id=template_id, status="ready", step="done",
        transcript=TRANSCRIPT, speaker_transcript=TRANSCRIPT, notes=NOTES,
        created_at=datetime.now(UTC) - timedelta(minutes=30),
    )  # fmt: skip
    meeting = Meeting(**{**values, **fields})
    session.add(meeting)
    session.flush()
    workspace.mark_pending(meeting, quiet=fields.get("extraction_quiet", False))
    session.commit()
    return meeting


def run(base: str, engine) -> None:
    clients = {
        email: Recorder(base, name.split()[0].lower()) for email, (name, *_r) in PEOPLE.items()
    }
    dan, chidi, amina, eve = (clients[e] for e in PEOPLE)
    model = StandInModel(MODEL_REPLY)
    with Session(engine) as session:
        template_id = make_people(session)
        meeting_id = add_meeting(session, template_id, "dan@example.test").id
    for email, rec in clients.items():
        rec.call("session", "GET", "/api/session")
        rec.call(
            "login", "POST", "/api/session", body={"email": email, "password": PEOPLE[email][2]}
        )

    dan.call(
        "settings-default", "GET", "/api/settings/tasks",
        note="Before anything is saved: all three switches are on (also with no settings row).",
    )  # fmt: skip

    # --- the worker's step ---
    with Session(engine) as session:
        workspace.process_next(session, client=model)
        note_file(
            "mail-outbox-after-extraction",
            "`mail_outbox` after the extraction: Chidi has one clear task, so he is mailed (ids "
            "only). Nothing for Amina (her task is only suggested), nothing for Dan (the owner).",
            [dict(to=r.to_email, template=r.template, payload=r.payload, status=r.status)
             for r in session.scalars(select(MailOutbox).order_by(MailOutbox.id))],
        )  # fmt: skip
        stored = session.scalars(select(Task).order_by(Task.position)).all()
        note_file(
            "task-rows-after-extraction",
            "The tasks table as the extraction left it (only the columns this feature adds, plus "
            "the filing columns): a suggested task is unfiled; the web address in one reason was "
            "dropped by the check (`reason` null on a suggested task is possible).",
            [dict(position=t.position, title=t.title, suggested=t.suggested,
                  confidence=t.confidence, reason=t.reason, project_id=t.project_id,
                  sprint_id=t.sprint_id, is_new=t.is_new) for t in stored],
        )  # fmt: skip
    board = dan.call(
        "board", "GET", "/api/tasks",
        note="The Board and Backlog list: the three suggested tasks are NOT here (default "
        "`suggested=false`). `attention` counts none of them either.",
    ).json()  # fmt: skip
    waiting = dan.call(
        "suggested-list", "GET", "/api/tasks?suggested=true",
        note="Only the suggested tasks: what the Suggested tasks section reads. Note "
        "`quote_speaker` and `quote_line` (the row of the transcript page).",
    ).json()  # fmt: skip
    dan.call(
        "suggested-list-board-filter", "GET", "/api/tasks?suggested=false&assignee=none",
        note="Filters act inside the list they are asked of.",
    )  # fmt: skip
    dan.call("meeting-tasks", "GET", f"/api/meetings/{meeting_id}/tasks",
             note="The meeting's own list has every task, each marked `suggested`; "
             "`to_confirm` is the count; `assigned_people` counts only tasks that were made.")  # fmt: skip
    dan.call("meeting-detail", "GET", f"/api/meetings/{meeting_id}",
             note="`tasks_count` is the number made (2), `tasks` still lists all 5.")  # fmt: skip
    dan.call("meetings-list", "GET", "/api/meetings", note="`tasks_count` on the list: 2.")
    dan.call("session-badges", "GET", "/api/session", note="`badges.tasks_new` counts made tasks only.")  # fmt: skip

    by_title = {t["title"]: t for t in waiting["tasks"]}
    amina_task = by_title["Look into the analytics setup"]
    chidi_task = by_title["Help with the demo"]
    nobody_task = by_title["Look into the invoice export"]
    clear_task = next(t for t in board["tasks"] if t["title"] == "Finish the checkout page")
    note_file("ids", "Task ids used below.", dict(
        amina_suggested=amina_task["id"], chidi_suggested=chidi_task["id"],
        nobody_suggested=nobody_task["id"], clear=clear_task["id"]))  # fmt: skip

    # --- who may see it and who may decide ---
    chidi.call(
        "chidi-sees-his-suggestion", "GET", f"/api/tasks/{chidi_task['id']}",
        note="Chidi is the assignee of a task from Dan's PRIVATE meeting: he gets the task, its "
        "quote and the suggestion fields, `meeting` null, and no `quote_speaker` / `quote_line` "
        "(nothing around the quote).",
    )  # fmt: skip
    chidi.call(
        "chidi-accept-403", "POST", f"/api/tasks/{chidi_task['id']}/accept",
        note="Chidi can see the task (it is his) but may not edit it: a member who is not an owner or admin cannot accept it. 403 `team_forbidden`, nothing changes.",
    )  # fmt: skip
    chidi.call("chidi-dismiss-403", "DELETE", f"/api/tasks/{chidi_task['id']}",
               note="...nor dismiss it.")  # fmt: skip
    eve.call("eve-accept-404", "POST", f"/api/tasks/{amina_task['id']}/accept",
             note="Someone outside the team: 404, as for any task they cannot see.")  # fmt: skip
    eve.call("eve-dismiss-404", "DELETE", f"/api/tasks/{amina_task['id']}")
    dan.call("accept-clear-409", "POST", f"/api/tasks/{clear_task['id']}/accept",
             note="A task that never was a suggestion: 409 `not_suggested`.")  # fmt: skip
    dan.call("accept-missing-404", "POST", "/api/tasks/999999/accept")
    dan.call("accept-no-csrf", "POST", f"/api/tasks/{amina_task['id']}/accept", csrf=None,
             note="No CSRF token: refused, nothing changes.")  # fmt: skip
    dan.call(
        "edit-suggested", "PATCH", f"/api/tasks/{chidi_task['id']}",
        body={"title": "Help with the demo prep"},
        note="Editing leaves it suggested (`suggested` still true).",
    )  # fmt: skip
    dan.call("patch-cannot-accept", "PATCH", f"/api/tasks/{chidi_task['id']}",
             body={"suggested": False},
             note="Accepting is `POST .../accept`, not a field of PATCH: 422.")  # fmt: skip

    # --- accept, dismiss ---
    amina_accept = dan.call(
        "accept-amina", "POST", f"/api/tasks/{amina_task['id']}/accept",
        note="Dan (owner of the team) accepts Amina's task. 200 with the task, `suggested` false; "
        "it is now filed as a new task is. Amina gets the assignee mail (see below).",
    )  # fmt: skip
    dan.call("accept-amina-again", "POST", f"/api/tasks/{amina_task['id']}/accept",
             note="A second click: 200, the same task, no second mail.")  # fmt: skip
    assert amina_accept.json()["suggested"] is False
    dan.call("board-after-accept", "GET", "/api/tasks",
             note="Amina's task is on the Board now; two suggestions are left.")  # fmt: skip
    dan.call("suggested-after-accept", "GET", "/api/tasks?suggested=true")
    amina.call("amina-accept-private-404", "POST", f"/api/tasks/{chidi_task['id']}/accept",
               note="Amina is an admin of the team, but the meeting is Dan's private one and she was not given this task: she cannot read it, so 404.")  # fmt: skip
    dan.call("dismiss-nobody", "DELETE", f"/api/tasks/{nobody_task['id']}",
             note="Dismiss is the existing DELETE: 204, no body.")  # fmt: skip
    dan.call("dismissed-get-404", "GET", f"/api/tasks/{nobody_task['id']}",
             note="A dismissed suggestion is gone (it stays as a tombstone so it is not brought back).")  # fmt: skip
    dan.call("dismissed-accept-404", "POST", f"/api/tasks/{nobody_task['id']}/accept")

    with Session(engine) as session:
        rows = session.scalars(select(MailOutbox).order_by(MailOutbox.id)).all()
        note_file(
            "mail-outbox-after-accept",
            "`mail_outbox` after Amina's task was accepted (twice): Chidi's from the extraction, "
            "and one for Amina. Accepting Chidi's suggestion (if it were accepted) would queue "
            "nothing: he already has a mail for this meeting.",
            [dict(to=r.to_email, template=r.template, payload=r.payload, status=r.status)
             for r in rows],
        )  # fmt: skip
        mailer = CapturingMailer()
        later = datetime.now(UTC) + timedelta(hours=1)
        for row in rows:
            row.created_at = later - timedelta(minutes=5)
        session.commit()
        mail.deliver_pending(
            session, mailer,
            Settings(_env_file=None, public_base_url="https://notes.example.test"), later,
        )  # fmt: skip
        note_file(
            "mail-rendered",
            "What the mail provider would be handed (CAPTURING MAILER, not Resend).",
            [dict(to=m.to, subject=m.subject, text=m.text) for m in mailer.sent],
        )  # fmt: skip

    # --- a re-run does not undo any of it ---
    with Session(engine) as session:
        meeting = session.get(Meeting, meeting_id)
        workspace.mark_pending(meeting)
        session.commit()
        workspace.process_next(session, datetime.now(UTC) + timedelta(hours=2), client=model)
    dan.call("board-after-rerun", "GET", "/api/tasks",
             note="After the notes were read again: Amina's accepted task is still on the board.")  # fmt: skip
    dan.call("suggested-after-rerun", "GET", "/api/tasks?suggested=true",
             note="Chidi's is still waiting; the one Dan dismissed is not back.")  # fmt: skip

    # --- the setting ---
    dan.call("settings-off", "PUT", "/api/settings/tasks",
             body={"auto_create_tasks": True, "email_assignees": True, "ask_before_unclear": False},
             note="Ask before adding unclear tasks: off.")  # fmt: skip
    dan.call("settings-after-off", "GET", "/api/settings/tasks")
    dan.call("settings-old-client", "PUT", "/api/settings/tasks",
             body={"auto_create_tasks": True, "email_assignees": False},
             note="A client that does not send the new field leaves it as it was (still false).")  # fmt: skip
    dan.call("settings-invalid", "PUT", "/api/settings/tasks",
             body={"auto_create_tasks": True, "email_assignees": True, "ask_before_unclear": "maybe"})  # fmt: skip
    dan.call("settings-on", "PUT", "/api/settings/tasks",
             body={"auto_create_tasks": True, "email_assignees": True, "ask_before_unclear": True})  # fmt: skip
    dan.call("suggested-after-setting-off", "GET", "/api/tasks?suggested=true",
             note="Turning the switch off did not move the tasks already waiting.")  # fmt: skip
    dan.call("suggested-not-a-boolean", "GET", "/api/tasks?suggested=maybe",
             note="A value that is not a boolean: 422.")  # fmt: skip


def main() -> None:
    if not sys.argv[1:]:
        sys.exit(__doc__)
    prepare_database()
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    settings = Settings(
        _env_file=None,
        app_env="test",  # lets cookies work over plain http; production adds the Secure flag
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
        engine.dispose()


if __name__ == "__main__":
    main()
