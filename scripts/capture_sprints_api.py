"""Drive the Sprints API (and the sprint fields of tasks, projects and the team) over real HTTP and save every exchange for the frontend guide.

    python scripts/capture_sprints_api.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres

The argument is any Postgres server the script may create a scratch database on. It runs the real
migrations there, starts the real app under uvicorn and signs in as placeholder users. Each exchange
is written to `docs/fe-captures/sprints-w9/NN-name.json`, so every claim in
`docs/fe-integration-guide-sprints.md` can be re-derived by running this again.

What is real: the app, auth, CSRF, validation, roles and visibility, the database, migrations,
serialisation, every error, the task filing and the milestone proposal (the real
`workspace.run_extraction`, which files tasks by keyword and stores proposals).

What is NOT real, and is labelled where it matters:
* **The model.** `run_extraction` is handed a scripted client that returns a fixed answer (two
  action items and one stated date) instead of calling OpenAI; the real checks still run on it (the
  quote must be in the transcript).
* **The clock for the rollover.** The scheduler's hourly job is the real `sprints.rollover_team`,
  called in-process with a `now` forty days ahead instead of waiting; the HTTP calls around it are real.
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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "fe-captures" / "sprints-w9"
DB_NAME = "notetaker_capture_sprints"
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

from app import accounts, sprints, teams, workspace  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Meeting, Sprint, Template, User  # noqa: E402
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
        chidi_id = session.scalars(select(User.id).where(User.email == "chidi@example.test")).one()
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
        rec.call("login", "POST", "/api/session", body={"email": email, "password": PEOPLE[email][1]})  # fmt: skip

    # --- reading ---
    first = dan.call("sprints-first", "GET", "/api/sprints", note="The first call makes the team's current and next sprint (the default is 1 week, 5 working days, Monday to Friday). `today` is in the viewer's zone; the dates depend on the day this was captured.").json()  # fmt: skip
    cur, nxt = first["current_id"], first["next_id"]
    dan.call("team", "GET", "/api/team", note="The sprint settings are on the team: `sprint_length`, `sprint_custom_days`, `sprint_align_weekdays`.")  # fmt: skip
    eve_board = eve.call("outsider-sprints", "GET", "/api/sprints", note="Another team has its own sprints; ids never overlap.").json()  # fmt: skip
    dan.call(
        "bad-project-filter", "GET", "/api/sprints?project=abc", note="422 `validation_error`."
    )
    dan.call(
        "unknown-project-filter", "GET", "/api/sprints?project=999999", note="404 `not_found`."
    )

    # --- filing ---
    dan.call("create-portal", "POST", "/api/projects", body={"name": "Client Portal", "keywords": ["portal", "checkout"]})  # fmt: skip
    dan.call("create-offsite", "POST", "/api/projects", body={"name": "Offsite", "keywords": ["offsite", "venue"]})  # fmt: skip
    projects = dan.call("projects-list", "GET", "/api/projects", note="`sprint_length` null follows the team's; `in_current_sprint` and `in_backlog` are the two stats.").json()["projects"]  # fmt: skip
    portal, offsite = projects[0]["id"], projects[1]["id"]
    with Session(engine) as session:
        meeting = session.get(Meeting, meeting_id)
        workspace.mark_pending(meeting)
        session.commit()
        workspace.run_extraction(session, meeting, client=ScriptedModel(), now=datetime(2026, 11, 3, 12, 0, tzinfo=UTC))  # fmt: skip
    tasks = dan.call("tasks-filed", "GET", "/api/tasks", note="Filed into a sprint by the due date: the first task has a due date, so it is in the current sprint (past due or inside it) or the next (later); the other two have none, so `sprint_id` is null (Backlog).").json()["tasks"]  # fmt: skip
    by_title = {t["title"]: t["id"] for t in tasks}
    checkout, venue, copy = by_title["Finish the portal checkout"], by_title["Book the venue for the offsite"], by_title["Review the copy on the portal"]  # fmt: skip
    dan.call("tasks-current", "GET", "/api/tasks?sprint=current", note="`current` and `next` stand for every series' current and next sprint.")  # fmt: skip
    dan.call("tasks-backlog", "GET", "/api/tasks?sprint=none", note="`none` is the Backlog.")
    dan.call("tasks-by-id", "GET", f"/api/tasks?sprint={nxt}")
    dan.call("tasks-other-teams-id", "GET", f"/api/tasks?sprint={eve_board['current_id']}", note="Another team's sprint id matches nothing.")  # fmt: skip
    dan.call("tasks-bad-sprint", "GET", "/api/tasks?sprint=soon", note="422 `validation_error`.")

    dan.call("create-default", "POST", "/api/tasks", body={"title": "Send the invoice", "due_date": "2099-01-01"}, note="No `sprint_id` given: a due date after the current sprint files it in the next sprint.")  # fmt: skip
    dan.call("create-no-due", "POST", "/api/tasks", body={"title": "Tidy the shared drive"}, note="No `sprint_id` and no due date: Backlog (`sprint_id` null).")  # fmt: skip
    dan.call("create-forced", "POST", "/api/tasks", body={"title": "Plan the demo", "sprint_id": cur, "due_date": "2099-01-01"}, note="An explicit sprint wins over the due date.")  # fmt: skip
    dan.call("create-backlog", "POST", "/api/tasks", body={"title": "Someday", "sprint_id": None, "due_date": "2000-01-01"}, note="An explicit `null` is the Backlog, whatever the due date.")  # fmt: skip
    dan.call("create-other-team-sprint", "POST", "/api/tasks", body={"title": "x", "sprint_id": eve_board["current_id"]}, note="422 `sprint_not_on_team`: the same answer for another team's sprint, a closed one or one that does not exist.")  # fmt: skip
    dan.call("create-bad-id", "POST", "/api/tasks", body={"title": "x", "sprint_id": 0}, note="422 `validation_error`.")  # fmt: skip

    # --- who may plan a task ---
    dan.call("assign-chidi", "PATCH", f"/api/tasks/{venue}", body={"assignee_user_id": chidi_id})
    chidi.call("member-ticks", "PATCH", f"/api/tasks/{venue}", body={"status": "doing"}, note="A member given a task changes its status.")  # fmt: skip
    chidi.call("member-plans", "PATCH", f"/api/tasks/{venue}", body={"sprint_id": nxt}, note="403 `team_forbidden`: filing (project, milestone, sprint) is the owner's and admin's; Adebayo confirmed 2026-11-06 that a member given a task cannot re-file it.")  # fmt: skip
    chidi.call("member-backlogs", "PATCH", f"/api/tasks/{venue}", body={"sprint_id": None}, note="200, nothing changed: the task is already in the Backlog, and a field given its current value is not a change.")  # fmt: skip
    chidi.call("member-makes", "POST", "/api/tasks", body={"title": "Chidi's own", "due_date": "2099-01-01"})  # fmt: skip
    amara.call("admin-plans", "PATCH", f"/api/tasks/{copy}", body={"sprint_id": cur}, note="An admin may; `carried` is cleared by a person placing it.")  # fmt: skip
    dan.call("owner-plans-next", "PATCH", f"/api/tasks/{checkout}", body={"sprint_id": nxt})
    dan.call("owner-plans-backlog", "PATCH", f"/api/tasks/{checkout}", body={"sprint_id": None})
    dan.call("plan-other-team-sprint", "PATCH", f"/api/tasks/{checkout}", body={"sprint_id": eve_board["current_id"]}, note="422 `sprint_not_on_team`, nothing changed.")  # fmt: skip
    dan.call("owner-plans-back", "PATCH", f"/api/tasks/{checkout}", body={"sprint_id": cur})
    eve.call("outsider-plans", "PATCH", f"/api/tasks/{checkout}", body={"sprint_id": eve_board["current_id"]}, note="404: not their task.")  # fmt: skip

    # --- settings ---
    chidi.call("member-sets-length", "PATCH", "/api/team", body={"sprint_length": "week14"}, note="403 `team_forbidden`.")  # fmt: skip
    dan.call("bad-length", "PATCH", "/api/team", body={"sprint_length": "fortnight"}, note="422 `validation_error`.")  # fmt: skip
    dan.call("bad-custom-days", "PATCH", "/api/team", body={"sprint_custom_days": 366}, note="422 `validation_error` (1 to 365).")  # fmt: skip
    dan.call("align-needs-week5", "PATCH", "/api/team", body={"sprint_length": "week7", "sprint_align_weekdays": True}, note="422 `align_needs_week5`: the Monday-to-Friday switch is only for 1 week, 5 working days. Nothing is changed.")  # fmt: skip
    amara.call("admin-sets-custom", "PATCH", "/api/team", body={"sprint_length": "custom", "sprint_custom_days": 12}, note="An admin may. The open sprints are redrawn from their start days.")  # fmt: skip
    dan.call("sprints-after-settings", "GET", "/api/sprints", note="Current and next are 12 days each now.")  # fmt: skip
    dan.call("back-to-week5", "PATCH", "/api/team", body={"sprint_length": "week5", "sprint_align_weekdays": True})  # fmt: skip

    # --- a project with its own length ---
    chidi.call("member-sets-project-length", "PATCH", f"/api/projects/{offsite}", body={"sprint_length": "week14"}, note="403 `team_forbidden`.")  # fmt: skip
    dan.call("project-custom-refused", "PATCH", f"/api/projects/{offsite}", body={"sprint_length": "custom"}, note="422 `validation_error`: a project cannot be `custom`.")  # fmt: skip
    dan.call("project-own-length", "PATCH", f"/api/projects/{offsite}", body={"sprint_length": "week14"}, note="A series of its own is made; the project's tasks in the team's current and next sprint move into it.")  # fmt: skip
    own = dan.call("sprints-project", "GET", f"/api/sprints?project={offsite}", note="`own_series` true, `project_id` set, counts are this project's tasks.").json()  # fmt: skip
    dan.call("sprints-project-without-own", "GET", f"/api/sprints?project={portal}", note="A project without its own length: the team's sprints, counting only this project's tasks.")  # fmt: skip
    dan.call("task-into-own-series", "PATCH", f"/api/tasks/{venue}", body={"sprint_id": own["next_id"]}, note="Offsite's task can only be planned into Offsite's own sprints.")  # fmt: skip
    dan.call("task-wrong-series", "PATCH", f"/api/tasks/{venue}", body={"sprint_id": cur}, note="422 `sprint_not_on_team`: that is the team's sprint, not Offsite's.")  # fmt: skip
    dan.call("move-project-keeps-time", "PATCH", f"/api/tasks/{venue}", body={"project_id": portal}, note="Moving it to a project on the team's series keeps its place in time: Offsite's next becomes the team's next.")  # fmt: skip
    dan.call("project-back-to-team", "PATCH", f"/api/projects/{offsite}", body={"sprint_length": None}, note="`null` is allowed here: follow the team's length again. Its tasks move back; its open sprints are dropped.")  # fmt: skip
    dan.call("project-own-again", "PATCH", f"/api/projects/{offsite}", body={"sprint_length": "week7"})  # fmt: skip

    # --- closing ---
    chidi.call("member-closes", "POST", f"/api/sprints/{cur}/close", note="403 `team_forbidden`.")
    eve.call("outsider-closes", "POST", f"/api/sprints/{cur}/close", note="404 `not_found`, never 403.")  # fmt: skip
    dan.call("close-next", "POST", f"/api/sprints/{nxt}/close", note="409 `sprint_not_current`.")
    dan.call("close-missing", "POST", "/api/sprints/999999/close", note="404 `not_found`.")
    with (
        Session(engine) as session
    ):  # setup, not an API call: make today fall inside the sprint so closing it is early
        row = session.get(Sprint, cur)
        row.start_date, row.end_date = datetime.now(UTC).date() - timedelta(days=1), datetime.now(UTC).date() + timedelta(days=4)  # fmt: skip
        session.commit()
    dan.call("close-current", "POST", f"/api/sprints/{cur}/close", note="Unfinished tasks move to the next sprint and are `carried`; the next sprint starts today and keeps its end date; a new next is made. Closed before its end date, so `closed_early` is true (setup: the sprint's dates were moved in the database first so that today falls inside it). `moved` counts the unfinished tasks the caller may see (\"3 unfinished tasks moved to Sprint 13\").")  # fmt: skip
    dan.call("close-again", "POST", f"/api/sprints/{cur}/close", note="409 `sprint_not_current`.")
    dan.call("sprints-after-close", "GET", "/api/sprints", note="The closed sprint is listed first, `closed_early` true.")  # fmt: skip
    dan.call("tasks-carried", "GET", "/api/tasks?sprint=current", note="`carried` true on the tasks that moved.")  # fmt: skip

    # --- the scheduler's hourly rollover ---
    with Session(engine) as session:
        team = teams.team_of(session, dan_id)
        closed = sprints.rollover_team(session, team, datetime.now(UTC) + timedelta(days=40))
        print("rollover closed", closed)
    dan.call("sprints-after-rollover", "GET", "/api/sprints", note="The scheduler's hourly job (real `sprints.rollover_team`, run here with a clock 40 days ahead) closed every sprint whose end date had passed, `closed_early` false, and kept a current and a next. Unfinished tasks went with it.")  # fmt: skip
    dan.call("tasks-after-rollover", "GET", "/api/tasks?sprint=current")

    # --- delete ---
    dan.call("delete-project-with-own-length", "DELETE", f"/api/projects/{offsite}", note="204. Its tasks stay and now sit in the team's sprints.")  # fmt: skip
    dan.call("sprints-after-delete", "GET", f"/api/sprints?project={offsite}", note="404 `not_found`.")  # fmt: skip


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
