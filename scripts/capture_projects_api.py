"""Drive the Projects and Milestones API over real HTTP and save every exchange for the frontend guide.

    python scripts/capture_projects_api.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres

The argument is any Postgres server the script may create a scratch database on. It runs the real
migrations there, starts the real app under uvicorn and signs in as placeholder users. Each exchange
is written to `docs/fe-captures/projects-w4/NN-name.json`, so every claim in
`docs/fe-integration-guide-projects.md` can be re-derived by running this again.

What is real: the app, auth, CSRF, validation, roles and visibility, the database, migrations,
serialisation, every error, the task filing and the milestone proposal (the real
`workspace.run_extraction`, which files tasks by keyword and stores proposals).

What is NOT real, and is labelled where it matters:
* **The model.** `run_extraction` is handed a scripted client that returns a fixed answer (two
  action items and one stated date) instead of calling OpenAI; the real checks still run on it (the
  quote must be in the transcript).
* The recording, transcription and speaker steps and the bot: the meeting row is inserted as the
  pipeline leaves it (`status ready`, with notes and a speaker transcript).
Nobody's real meeting or address is involved; every name and address is a placeholder.

Secrets in the files are masked: CSRF tokens become `<csrf-A>`, `<csrf-B>` in order of first
appearance, cookies `<cookie>`.
"""

import json
import os
import shutil
import socket
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "fe-captures" / "projects-w4"
DB_NAME = "notetaker_capture_projects"
ADMIN_EMAIL = "admin@example.test"
ADMIN_PASSWORD = "-".join(["capture", "admin", "placeholder"])
PEOPLE = {  # email -> (name, password, role on Dan's team); all placeholders
    "dan@example.test": ("Dan Okoro", "-".join(["dan", "chosen", "password", "1"]), "owner"),
    "amara@example.test": ("Amara Nwosu", "-".join(["amara", "chosen", "password", "2"]), "admin"),
    "chidi@example.test": ("Chidi Eze", "-".join(["chidi", "chosen", "password", "3"]), "member"),
    "eve@example.test": ("Eve Outsider", "-".join(["eve", "chosen", "password", "4"]), None),
}

admin_url = sys.argv[1] if len(sys.argv) > 1 else ""
scratch_url = urlparse(admin_url)._replace(path=f"/{DB_NAME}").geturl()
os.environ["DATABASE_URL"] = scratch_url
os.environ["OWNER_EMAIL"] = ADMIN_EMAIL
os.environ["OWNER_PASSWORD"] = ADMIN_PASSWORD
os.environ.setdefault("APP_ENV", "development")

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import accounts, teams, workspace  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Meeting, Task, Template, User  # noqa: E402
from app.passwords import hash_password  # noqa: E402
from app.services import get_vexa  # noqa: E402

NOTES = """\
## Summary
Portal planning.

## Decisions
None noted

## Action items
- Dan: finish the portal checkout (by Friday)
- Chidi: book the venue for the offsite
- Amara: review the copy on the portal

## Open questions
None noted

## Please check
None noted
"""
TRANSCRIPT = (
    "[Dan]: I'll finish the portal checkout before Friday.\n"
    "[Chidi]: I'll book the venue for the offsite.\n"
    "[Amara]: I'll review the copy on the portal.\n"
    "[Dan]: Launch is December 8."
)
MODEL_ANSWER = {
    "action_items": [
        {
            "index": 0, "title": "Finish the portal checkout", "spoken_owner": "Dan",
            "due_phrase": "before Friday", "due_date": "2026-11-06",
            "quote": "I'll finish the portal checkout before Friday.", "confidence": "high",
        },
        {
            "index": 1, "title": "Book the venue for the offsite", "spoken_owner": "Chidi",
            "due_phrase": None, "due_date": None,
            "quote": "I'll book the venue for the offsite.", "confidence": "high",
        },
        {
            "index": 2, "title": "Review the copy on the portal", "spoken_owner": "Amara",
            "due_phrase": None, "due_date": None,
            "quote": "I'll review the copy on the portal.", "confidence": "high",
        },
    ],
    "review": [],
    "milestones": [
        {"name": "Client Portal launch", "date": "2026-12-08", "quote": "Launch is December 8."}
    ],
}  # fmt: skip


class ScriptedModel:
    """Stands in for OpenAI: returns MODEL_ANSWER."""

    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(MODEL_ANSWER)), finish_reason="stop"
                )
            ],
            usage=SimpleNamespace(prompt_tokens=900, completion_tokens=300),
        )


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
        headers = {}
        if method != "GET" and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
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
        print(f"{Recorder.count:02d}-{name:46} {method:6} {path:44} -> {response.status_code}")
        return response


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


def make_people(session) -> int:
    users = {}
    for email, (name, password, _role) in PEOPLE.items():
        user = User(
            email=email,
            name=name,
            password_hash=hash_password(password),
            email_verified_at=datetime.now(UTC),
        )
        session.add(user)
        session.flush()
        accounts.add_user_defaults(session, user)
        users[email] = user
    dan_team = teams.team_of(session, users["dan@example.test"].id)
    for email in ("amara@example.test", "chidi@example.test"):
        # the person's team of one is dissolved by joining, as `teams.join` does
        teams.join(session, users[email], dan_team.id, PEOPLE[email][2], None, datetime.now(UTC))
    session.commit()
    return session.scalars(
        select(Template.id).where(
            Template.owner_user_id == users["dan@example.test"].id, Template.name == "General"
        )
    ).one()


def run(base: str, engine) -> None:
    clients = {email: Recorder(base, p[0].split()[0].lower()) for email, p in PEOPLE.items()}
    dan, amara, chidi, eve = clients.values()
    with Session(engine) as session:
        template_id = make_people(session)
        dan_id = session.scalars(select(User.id).where(User.email == "dan@example.test")).one()
        meeting = Meeting(
            owner_user_id=dan_id, url="https://example.test/meeting", platform="google_meet",
            native_id="nat-aaaa-bbb", template_id=template_id, status="ready", step="done",
            title="Portal planning", transcript=TRANSCRIPT, speaker_transcript=TRANSCRIPT,
            notes=NOTES, created_at=datetime(2026, 11, 3, 10, 0, tzinfo=UTC),
        )  # fmt: skip
        session.add(meeting)
        session.commit()
        meeting_id = meeting.id
    for email, rec in clients.items():
        rec.call("session", "GET", "/api/session")
        rec.call(
            "login", "POST", "/api/session", body={"email": email, "password": PEOPLE[email][1]}
        )

    # --- projects ---
    dan.call("projects-empty", "GET", "/api/projects", note="A new team has none. `can_manage` is true for owner and admin.")  # fmt: skip
    dan.call("create-blank", "POST", "/api/projects", body={}, note='"New project" opens a blank one: `name` is "" and the colour is the next swatch.')  # fmt: skip
    dan.call("create-portal", "POST", "/api/projects", body={"name": "Client Portal", "keywords": ["portal", "checkout"]})  # fmt: skip
    dan.call("create-ops", "POST", "/api/projects", body={"name": "Offsite", "color": "#8A2E5C", "keywords": ["offsite", "venue"]})  # fmt: skip
    projects = dan.call("projects-list", "GET", "/api/projects").json()["projects"]
    blank, portal, offsite = (p["id"] for p in projects)
    dan.call("patch-name", "PATCH", f"/api/projects/{blank}", body={"name": "Scratch"}, note="Autosave: one field per request.")  # fmt: skip
    dan.call("patch-colour", "PATCH", f"/api/projects/{blank}", body={"color": "#1F6FB2"})
    dan.call("patch-keywords", "PATCH", f"/api/projects/{blank}", body={"keywords": ["Scratch", "scratch", " tmp "]}, note="Replaces the list; duplicates (any case) are dropped and words trimmed.")  # fmt: skip
    dan.call("patch-keyword-too-long", "PATCH", f"/api/projects/{blank}", body={"keywords": ["x" * 41]}, note="422 `keyword_too_long`.")  # fmt: skip
    dan.call("patch-bad-colour", "PATCH", f"/api/projects/{blank}", body={"color": "red"}, note="422 `validation_error` (pattern).")  # fmt: skip
    dan.call("patch-null-name", "PATCH", f"/api/projects/{blank}", body={"name": None}, note="422 `validation_error`.")  # fmt: skip
    dan.call("get-one", "GET", f"/api/projects/{blank}")

    # --- who may do what ---
    chidi.call("member-reads", "GET", "/api/projects", note="A member reads; `can_manage` false.")
    chidi.call("member-creates", "POST", "/api/projects", body={"name": "Nope"}, note="403 `team_forbidden`.")  # fmt: skip
    chidi.call("member-patches", "PATCH", f"/api/projects/{portal}", body={"name": "Nope"}, note="403 `team_forbidden`.")  # fmt: skip
    chidi.call("member-deletes", "DELETE", f"/api/projects/{portal}", note="403 `team_forbidden`.")
    amara.call("admin-patches", "PATCH", f"/api/projects/{offsite}", body={"keywords": ["offsite", "venue", "travel"]}, note="An admin may write.")  # fmt: skip
    eve.call("outsider-list", "GET", "/api/projects", note="Another team sees none of it.")
    eve.call("outsider-get", "GET", f"/api/projects/{portal}", note="404 `not_found`, never 403.")
    eve.call("outsider-patch", "PATCH", f"/api/projects/{portal}", body={"name": "x"}, note="404 `not_found`.")  # fmt: skip
    eve.call("outsider-delete", "DELETE", f"/api/projects/{portal}", note="404 `not_found`.")

    # --- filing + proposals, by the real extraction run (scripted model) ---
    with Session(engine) as session:
        meeting = session.get(Meeting, meeting_id)
        workspace.mark_pending(meeting)
        session.commit()
        workspace.run_extraction(session, meeting, client=ScriptedModel(), now=datetime(2026, 11, 3, 12, 0, tzinfo=UTC))  # fmt: skip
    dan.call("tasks-filed", "GET", "/api/tasks", note="Filed by keyword. Task 1 ('portal' + 'checkout', two hits) and task 2 ('venue' + 'offsite', two hits) are confident; task 3 matched 'portal' only (one hit) so it is filed there with `check_project` true. `attention.check_project` counts guesses.")  # fmt: skip
    with Session(engine) as session:
        weak_id = session.scalars(select(Task.id).where(Task.check_project.is_(True))).one()
    dan.call("tasks-with-a-guess", "GET", "/api/tasks?project=" + str(portal), note="The third task matched one keyword ('portal') only, so it is filed there with `check_project` true.")  # fmt: skip
    dan.call("tasks-no-project", "GET", "/api/tasks?project=none")
    dan.call("tasks-bad-filter", "GET", "/api/tasks?project=x", note="422 `validation_error`.")
    dan.call("confirm-check", "PATCH", f"/api/tasks/{weak_id}", body={"check_project": False}, note='The "Check project" Confirm button.')  # fmt: skip
    dan.call("confirm-check-true", "PATCH", f"/api/tasks/{weak_id}", body={"check_project": True}, note="422: only `false` is accepted.")  # fmt: skip
    dan.call("refile", "PATCH", f"/api/tasks/{weak_id}", body={"project_id": offsite}, note="Choosing a project clears `check_project`.")  # fmt: skip
    dan.call("bad-project", "PATCH", f"/api/tasks/{weak_id}", body={"project_id": 999999}, note="422 `project_not_on_team`.")  # fmt: skip

    # --- milestones ---
    dan.call("meeting-milestones", "GET", f"/api/meetings/{meeting_id}/milestones", note="The proposal made by the run: `confirmed_at` null, with the words said.")  # fmt: skip
    dan.call("milestones-before", "GET", "/api/milestones", note="Proposals are not listed here.")
    proposal = dan.call("meeting-milestones-id", "GET", f"/api/meetings/{meeting_id}/milestones").json()["milestones"][0]["id"]  # fmt: skip
    chidi.call("member-sees-proposal", "GET", f"/api/meetings/{meeting_id}/milestones", note="404: Dan's meeting is private (not shared with the team).")  # fmt: skip
    chidi.call("member-confirms", "POST", f"/api/milestones/{proposal}/confirm", note="403 `team_forbidden`.")  # fmt: skip
    eve.call("outsider-confirms", "POST", f"/api/milestones/{proposal}/confirm", note="404 `not_found`.")  # fmt: skip
    dan.call("confirm", "POST", f"/api/milestones/{proposal}/confirm", body={}, note='"Set milestone: Client Portal launch, 8 Dec?" then Confirm. `project_id` was filed by keyword when proposed.')  # fmt: skip
    dan.call("confirm-again", "POST", f"/api/milestones/{proposal}/confirm", body={}, note="Idempotent: same `confirmed_at`.")  # fmt: skip
    dan.call("milestones-after", "GET", "/api/milestones")
    task_ids = [t["id"] for t in dan.call("tasks-all", "GET", "/api/tasks").json()["tasks"]]
    dan.call(
        "task-milestone", "PATCH", f"/api/tasks/{task_ids[-1]}", body={"milestone_id": proposal}
    )
    dan.call("tasks-by-milestone", "GET", f"/api/tasks?milestone={proposal}")
    dan.call("milestones-progress", "GET", "/api/milestones", note="`task_count` / `done_count` for the milestone card.")  # fmt: skip
    dan.call("project-with-milestone", "GET", f"/api/projects/{portal}", note="`milestone` is the one line on the project.")  # fmt: skip
    dan.call("create-milestone", "POST", "/api/milestones", body={"name": "Beta", "due_date": "2026-11-20", "project_id": offsite}, note="By hand; confirmed at once.")  # fmt: skip
    dan.call("patch-milestone", "PATCH", f"/api/milestones/{proposal}", body={"due_date": "2026-12-09"})  # fmt: skip

    # --- delete ---
    dan.call("delete-project", "DELETE", f"/api/projects/{portal}", note="204. Its tasks stay with no project; its milestones go.")  # fmt: skip
    dan.call("tasks-after-delete", "GET", "/api/tasks", note="`project_id` and `milestone_id` are null on the tasks that were in it.")  # fmt: skip
    dan.call("project-gone", "GET", f"/api/projects/{portal}", note="404 `not_found`.")


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
