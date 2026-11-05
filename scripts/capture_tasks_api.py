"""Drive the Tasks API over real HTTP and save every exchange for the frontend guide.

    python scripts/capture_tasks_api.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres

The argument is any Postgres server the script may create a scratch database on. It runs the real
migrations there, starts the real app under uvicorn and signs in as placeholder users. Each exchange
is written to `docs/fe-captures/tasks-w2/NN-name.json`, so every claim in
`docs/fe-integration-guide-tasks.md` can be re-derived by running this again.

What is real: the app, auth, CSRF, validation, roles and visibility, the database, migrations,
serialisation, every error, **the worker's extraction step** (`workspace.process_next`, the very
function the worker calls), the backfill, and the rendering and queueing of the assignee mail
(`mail.deliver_pending`, the real builder, the real outbox).

What is NOT real, and is labelled where it matters:
* **The model call.** This session had no OpenAI key, so the reply is the *recorded* `gpt-6-luna`
  reply from the AI1 eval (`docs/spike/ai-extraction-eval/names_and_terms.result.json`, field
  `raw`) for the same synthetic transcript and notes, served by a stand-in client. A failure is a
  stand-in that raises the error the OpenAI client raises for a dropped connection.
* **The mail provider.** The message is built and handed to a capturing mailer instead of Resend;
  what you read is what would be sent.
* **Team membership.** Invites are W3, so the teammates are placed on the owner's team by the
  script, in the database. **"Share with team"** has no route yet either, so the script sets
  `meetings.shared_team_id` directly where a capture says so.
* The recording, transcription and speaker steps and the bot: the meeting rows are inserted as
  the pipeline leaves them (`status ready`, notes and speaker transcript in place).
Nobody's real meeting or address is involved; every name and address is a placeholder.

Secrets in the files are masked: CSRF tokens become `<csrf-A>`, `<csrf-B>` in order of first
appearance and cookies `<cookie>`.
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
OUT = ROOT / "docs" / "fe-captures" / "tasks-w2"
EVAL = ROOT / "tests" / "eval" / "extraction"
SPIKE = ROOT / "docs" / "spike" / "ai-extraction-eval"
DB_NAME = "notetaker_capture_tasks"
ADMIN_EMAIL = "admin@example.test"  # created by migration 0006 from these (placeholders)
ADMIN_PASSWORD = "-".join(["capture", "admin", "placeholder"])
PEOPLE = {  # email -> (name, role on Dan's team, password); all placeholders
    "dan@example.test": ("Dan Okoro", "owner", "-".join(["dan", "chosen", "password", "1"])),
    "chidi@example.test": ("Chidi Eze", "member", "-".join(["chidi", "chosen", "password", "2"])),
    "amina@example.test": ("Amina Bello", "admin", "-".join(["amina", "chosen", "password", "3"])),
    "eve@example.test": ("Eve Outsider", None, "-".join(["eve", "chosen", "password", "4"])),
}

admin_url = sys.argv[1] if len(sys.argv) > 1 else ""
scratch_url = urlparse(admin_url)._replace(path=f"/{DB_NAME}").geturl()
os.environ["DATABASE_URL"] = scratch_url  # read by app.db when it is imported below
os.environ["OWNER_EMAIL"] = ADMIN_EMAIL
os.environ["OWNER_PASSWORD"] = ADMIN_PASSWORD
os.environ.setdefault("APP_ENV", "development")

import httpx  # noqa: E402
import openai  # noqa: E402
import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import accounts, ai_runs, mail, teams, workspace  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import AiRun, MailOutbox, Meeting, TeamMember, Template, User  # noqa: E402
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
        shown = json.loads(json.dumps(data))
        if isinstance(shown, dict) and "csrf_token" in shown:
            shown["csrf_token"] = self.mask(shown["csrf_token"])
        record = {
            "note": note,
            "client": self.who,
            "request": {"method": method, "path": path, "headers": sent, "body": body},
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


class RecordedLuna:
    """Serves the recorded `gpt-6-luna` reply (see the module docstring), or fails like the client."""

    def __init__(self, reply=None, fail=False):
        self.reply, self.fail = reply, fail
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        if self.fail:
            raise openai.APIConnectionError(
                request=httpx.Request("POST", "https://api.openai.test")
            )
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
    transcript = (EVAL / "names_and_terms.txt").read_text().strip()
    notes = (SPIKE / "names_and_terms.notes.md").read_text()
    values = dict(
        owner_user_id=owner.id, url="https://example.test/meeting", platform="google_meet",
        native_id="nat-aaaa-bbb", template_id=template_id, status="ready", step="done",
        transcript=transcript, speaker_transcript=transcript, notes=notes,
        created_at=datetime(2026, 11, 3, 10, 0, tzinfo=UTC),
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
    reply = json.loads((SPIKE / "names_and_terms.result.json").read_text())["raw"]
    luna = RecordedLuna(reply)
    with Session(engine) as session:
        template_id = make_people(session)
        meeting = add_meeting(session, template_id, "dan@example.test")
        meeting_id = meeting.id
    for email, rec in clients.items():
        rec.call("session", "GET", "/api/session")
        rec.call(
            "login", "POST", "/api/session", body={"email": email, "password": PEOPLE[email][2]}
        )
    anon = Recorder(base, "visitor")
    anon.call("tasks-logged-out", "GET", "/api/tasks", note="No login: 401.")

    dan.call(
        "session-before", "GET", "/api/session", note="`badges.tasks_new` is 0: nothing read yet."
    )
    dan.call(
        "meeting-before", "GET", f"/api/meetings/{meeting_id}",
        note="Notes are ready, the worker has not run: `extraction_status` `pending`, `tasks` empty.",
    )  # fmt: skip
    dan.call("meeting-tasks-before", "GET", f"/api/meetings/{meeting_id}/tasks")
    dan.call(
        "tasks-before", "GET", "/api/tasks", note="Nothing yet. `people` is the assignee list."
    )

    # --- the worker's step: a failed call first, then the recorded reply ---
    with Session(engine) as session:
        workspace.process_next(session, client=RecordedLuna(fail=True))
    dan.call(
        "meeting-after-failed-call", "GET", f"/api/meetings/{meeting_id}",
        note="STAND-IN failure (a dropped connection). The tasks the notes say are there already, "
        "`unverified` with no quote, and the meeting retries in 5 minutes (`pending`).",
    )  # fmt: skip
    with Session(engine) as session:
        workspace.process_next(session, datetime.now(UTC) + timedelta(hours=1), client=luna)
        runs = session.scalars(select(AiRun).order_by(AiRun.id)).all()
        note_file(
            "worker-ai-runs", "`ai_runs` after both attempts.",
            [dict(kind=r.kind, status=r.status, prompt_version=r.prompt_version,
                  input_tokens=r.input_tokens, output_tokens=r.output_tokens, error=r.error)
             for r in runs],
        )  # fmt: skip
    dan.call(
        "meeting-after", "GET", f"/api/meetings/{meeting_id}", note="Now `done`, tasks refined."
    )
    tasks = dan.call(
        "tasks",
        "GET",
        "/api/tasks",
        note="The owner's board. Chidi's and Amina's names matched real teammates by code; Dan's own sentence matched nobody.",
    ).json()
    dan.call("meeting-tasks", "GET", f"/api/meetings/{meeting_id}/tasks")
    dan.call("meetings-list", "GET", "/api/meetings", note="`tasks_count` on the list.")
    dan.call(
        "session-after", "GET", "/api/session", note="`badges.tasks_new` counts the New open tasks."
    )
    dan.call("tasks-filter-me", "GET", "/api/tasks?assignee=me")
    dan.call("tasks-filter-none", "GET", "/api/tasks?assignee=none")
    dan.call("tasks-filter-status", "GET", "/api/tasks?status=done")
    page = dan.call("tasks-page-1", "GET", "/api/tasks?limit=2").json()
    dan.call("tasks-page-2", "GET", f"/api/tasks?limit=2&before_id={page['next_before_id']}")
    dan.call("tasks-invalid-assignee", "GET", "/api/tasks?assignee=x")
    dan.call("tasks-invalid-limit", "GET", "/api/tasks?limit=501")

    chidi_task = next(
        t for t in tasks["tasks"] if t["assignee"] and t["assignee"]["name"] == "Chidi Eze"
    )
    amina_task = next(
        t for t in tasks["tasks"] if t["assignee"] and t["assignee"]["name"] == "Amina Bello"
    )
    other = next(
        t
        for t in tasks["tasks"]
        if t["assignee"] and t["assignee"]["name"] == "Amina Bello" and t["id"] != amina_task["id"]
    )
    note_file(
        "ids",
        "Task ids used below.",
        dict(chidi_task=chidi_task["id"], amina_task=amina_task["id"]),
    )

    # --- the assignee mail (rendered by the real builder, handed to a capturing mailer) ---
    with Session(engine) as session:
        rows = session.scalars(select(MailOutbox).order_by(MailOutbox.id)).all()
        note_file(
            "mail-outbox", "`mail_outbox` after the extraction: one row per assignee, ids only; "
            "none for Dan (the owner) and none for the task nobody owns.",
            [dict(to=r.to_email, template=r.template, payload=r.payload, status=r.status) for r in rows],
        )  # fmt: skip
        mailer = CapturingMailer()
        later = datetime.now(UTC) + timedelta(hours=1)
        for row in rows:
            row.created_at = later - timedelta(minutes=5)
        session.commit()
        mail.deliver_pending(
            session,
            mailer,
            Settings(_env_file=None, public_base_url="https://notes.example.test"),
            later,
        )
        note_file(
            "mail-rendered", "What the mail provider would be handed (CAPTURING MAILER, not Resend).",
            [dict(to=m.to, subject=m.subject, text=m.text, html=m.html) for m in mailer.sent],
        )  # fmt: skip
        again = mail.queue_tasks_assigned(
            session,
            session.get(Meeting, meeting_id),
            [session.scalars(select(User.id).where(User.email == "chidi@example.test")).one()],
        )
        session.commit()
        note_file(
            "mail-second-attempt",
            "Queueing the same person for the same meeting again.",
            dict(queued=again),
        )

    # --- visibility: the meeting is private ---
    chidi.call(
        "chidi-tasks",
        "GET",
        "/api/tasks",
        note="Chidi sees only the tasks given to him: the task and its quote, no meeting (`meeting` null, `from_meeting` true).",
    )
    chidi.call("chidi-task", "GET", f"/api/tasks/{chidi_task['id']}")
    chidi.call(
        "chidi-other-task",
        "GET",
        f"/api/tasks/{amina_task['id']}",
        note="A task of the private meeting that is not his: 404.",
    )
    chidi.call(
        "chidi-meeting",
        "GET",
        f"/api/meetings/{meeting_id}",
        note="The meeting itself stays private: 404.",
    )
    chidi.call("chidi-meeting-tasks", "GET", f"/api/meetings/{meeting_id}/tasks")
    eve.call("eve-tasks", "GET", "/api/tasks", note="Another team: nothing.")
    eve.call("eve-task", "GET", f"/api/tasks/{chidi_task['id']}", note="404, never 403.")
    eve.call("eve-patch", "PATCH", f"/api/tasks/{chidi_task['id']}", body={"status": "done"})
    eve.call("eve-delete", "DELETE", f"/api/tasks/{chidi_task['id']}")

    # --- roles ---
    chidi.call(
        "member-status",
        "PATCH",
        f"/api/tasks/{chidi_task['id']}",
        body={"status": "doing"},
        note="A member changes status of a task assigned to them.",
    )
    chidi.call(
        "member-due", "PATCH", f"/api/tasks/{chidi_task['id']}", body={"due_date": "2026-11-09"}
    )
    chidi.call(
        "member-title",
        "PATCH",
        f"/api/tasks/{chidi_task['id']}",
        body={"title": "Renamed"},
        note="403 `team_forbidden`; nothing changes.",
    )
    chidi.call(
        "member-reassign",
        "PATCH",
        f"/api/tasks/{chidi_task['id']}",
        body={"assignee_user_id": None},
    )
    chidi.call(
        "member-mixed",
        "PATCH",
        f"/api/tasks/{chidi_task['id']}",
        body={"status": "done", "title": "Sneaky"},
        note="One field not allowed refuses the whole request.",
    )
    chidi.call("member-delete", "DELETE", f"/api/tasks/{chidi_task['id']}")
    chidi.call(
        "member-task-after",
        "GET",
        f"/api/tasks/{chidi_task['id']}",
        note="Unchanged by the refused calls; `status` doing, `is_new` false after the first move.",
    )
    chidi.call("session-chidi", "GET", "/api/session", note="Chidi's `badges.tasks_new`.")

    # --- share the meeting (no route yet: set in the database) ---
    with Session(engine) as session:
        session.execute(
            text(
                "update meetings set shared_team_id = (select id from teams where owner_user_id = (select id from users where email = 'dan@example.test')) where id = :m"
            ),
            {"m": meeting_id},
        )
        session.commit()
    amina.call(
        "admin-tasks",
        "GET",
        "/api/tasks",
        note="MEETING SHARED BY SQL (no share route yet). The admin sees every task of it, with the meeting.",
    )
    amina.call("admin-meeting-tasks", "GET", f"/api/meetings/{meeting_id}/tasks")
    amina.call(
        "admin-reassign",
        "PATCH",
        f"/api/tasks/{other['id']}",
        body={"assignee_user_id": None},
        note="Owner/admin may change anything: here, back to Needs owner.",
    )
    chidi_id = chidi_task["assignee"]["user_id"]
    needs_owner = next(
        t for t in dan.call("tasks-needs-owner", "GET", "/api/tasks?assignee=none").json()["tasks"]
    )
    chidi.call(
        "member-claim",
        "PATCH",
        f"/api/tasks/{needs_owner['id']}",
        body={"assignee_user_id": chidi_id},
        note="A member may take a task nobody has.",
    )
    amina.call(
        "admin-assign",
        "PATCH",
        f"/api/tasks/{other['id']}",
        body={"assignee_user_id": chidi_id, "due_date": "2026-11-12"},
    )
    amina.call(
        "admin-assign-off-team",
        "PATCH",
        f"/api/tasks/{other['id']}",
        body={"assignee_user_id": 999999},
        note="422 `assignee_not_on_team`.",
    )
    chidi.call(
        "member-claim-taken",
        "PATCH",
        f"/api/tasks/{amina_task['id']}",
        body={"assignee_user_id": chidi_id},
        note="Not one somebody else has: 403.",
    )
    chidi.call(
        "member-sees-meeting",
        "GET",
        f"/api/tasks/{chidi_task['id']}",
        note="Now the meeting is shared, `meeting` is filled in.",
    )
    amina.call("patch-validation", "PATCH", f"/api/tasks/{other['id']}", body={"title": "x" * 201})
    amina.call("patch-bad-status", "PATCH", f"/api/tasks/{other['id']}", body={"status": "blocked"})
    amina.call("patch-null-status", "PATCH", f"/api/tasks/{other['id']}", body={"status": None})
    amina.call(
        "patch-no-csrf", "PATCH", f"/api/tasks/{other['id']}", body={"status": "done"}, csrf=None
    )
    amina.call(
        "patch-done",
        "PATCH",
        f"/api/tasks/{other['id']}",
        body={"status": "done"},
        note="`done_at` set.",
    )
    amina.call("patch-clear-due", "PATCH", f"/api/tasks/{other['id']}", body={"due_date": None})

    # --- manual tasks, delete ---
    made = chidi.call(
        "create",
        "POST",
        "/api/tasks",
        body={"title": "Order the cake", "assignee_user_id": chidi_id, "due_date": "2026-11-10"},
        note="Anyone on the team may.",
    ).json()
    chidi.call(
        "create-blank",
        "POST",
        "/api/tasks",
        body={},
        note='The drawer\'s blank task: `title` "" is allowed.',
    )
    chidi.call("create-off-team", "POST", "/api/tasks", body={"assignee_user_id": 999999})
    chidi.call("create-too-long", "POST", "/api/tasks", body={"title": "x" * 201})
    dan.call(
        "manual-seen-by-owner",
        "GET",
        f"/api/tasks/{made['id']}",
        note="A manual task belongs to the team: the owner sees it, `can_edit` false (Dan is the owner role so true).",
    )
    amina.call(
        "manual-delete",
        "DELETE",
        f"/api/tasks/{made['id']}",
        note="204, no body; a manual task is gone.",
    )
    amina.call("manual-after-delete", "GET", f"/api/tasks/{made['id']}")
    dan.call(
        "ai-delete",
        "DELETE",
        f"/api/tasks/{needs_owner['id']}",
        note="An AI task is kept as an invisible tombstone so the same bullet does not bring it back.",
    )
    dan.call("ai-after-delete", "GET", f"/api/tasks/{needs_owner['id']}")

    # --- re-run keeps what a person did ---
    with Session(engine) as session:
        workspace.mark_pending(session.get(Meeting, meeting_id))
        session.commit()
        workspace.process_next(session, datetime.now(UTC) + timedelta(hours=2), client=luna)
    dan.call(
        "tasks-after-rerun",
        "GET",
        "/api/tasks",
        note="After 'rewrite the notes': same ids; the moved and edited tasks are untouched; the deleted one did not come back.",
    )
    with Session(engine) as session:
        note_file(
            "mail-after-rerun",
            "The outbox after the re-run: still one mail per assignee.",
            [dict(to=r.to_email, status=r.status) for r in session.scalars(select(MailOutbox))],
        )

    # --- settings ---
    dan.call("settings-tasks", "GET", "/api/settings/tasks")
    dan.call(
        "settings-tasks-put",
        "PUT",
        "/api/settings/tasks",
        body={"auto_create_tasks": True, "email_assignees": False},
    )
    dan.call(
        "settings-tasks-invalid", "PUT", "/api/settings/tasks", body={"auto_create_tasks": "yes"}
    )

    # --- on-demand extraction ---
    chidi.call(
        "extract-not-owner",
        "POST",
        f"/api/meetings/{meeting_id}/tasks/extract",
        note="A teammate: 404 (owner only).",
    )
    dan.call(
        "extract-pending", "POST", f"/api/meetings/{meeting_id}/tasks/extract", note="First: 202."
    )
    dan.call(
        "extract-pending-again",
        "POST",
        f"/api/meetings/{meeting_id}/tasks/extract",
        note="While it waits: 409 `extraction_pending`.",
    )
    dan.put = None
    dan.call(
        "settings-tasks-off",
        "PUT",
        "/api/settings/tasks",
        body={"auto_create_tasks": False, "email_assignees": False},
    )
    with Session(engine) as session:
        workspace.process_next(session, datetime.now(UTC) + timedelta(hours=3), client=luna)
    dan.call(
        "extract-tasks-off",
        "POST",
        f"/api/meetings/{meeting_id}/tasks/extract",
        note="Tasks switched off: 409 `tasks_off`.",
    )
    dan.call(
        "settings-tasks-on",
        "PUT",
        "/api/settings/tasks",
        body={"auto_create_tasks": True, "email_assignees": True},
    )

    # --- backfill: a meeting read before tasks existed ---
    with Session(engine) as session:
        old = add_meeting(
            session,
            template_id,
            "dan@example.test",
            native_id="old-bbbb-ccc",
            created_at=datetime.now(UTC) - timedelta(days=2),
            extraction_status="done",
            extraction_version=ai_runs.EXTRACT_REVIEW_PROMPT_VERSION,
        )
        old.extraction_status = "done"
        old.extraction_version = ai_runs.EXTRACT_REVIEW_PROMPT_VERSION
        session.commit()
        old_id = old.id
        counts = workspace.backfill(session, datetime.now(UTC))
        note_file(
            "backfill", "`workspace.backfill` (what scripts/backfill_extraction.py runs).", counts
        )
        before = session.scalar(select(MailOutbox.id).order_by(MailOutbox.id.desc()).limit(1))
        workspace.process_next(session, datetime.now(UTC) + timedelta(hours=4), client=luna)
        after = session.scalar(select(MailOutbox.id).order_by(MailOutbox.id.desc()).limit(1))
        note_file(
            "backfill-mail",
            "Newest outbox id before and after the backfilled meeting was read: nobody is mailed.",
            dict(before=before, after=after),
        )
    dan.call(
        "backfilled-meeting-tasks",
        "GET",
        f"/api/meetings/{old_id}/tasks",
        note="Backfilled: `is_new` false on every task.",
    )
    chidi.call("final-session", "GET", "/api/session")


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
