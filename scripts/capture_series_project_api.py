"""Drive the series-to-project link and the Series page's sprint line over real HTTP and save every
exchange for the frontend guide.

    python scripts/capture_series_project_api.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres

The argument is any Postgres server the script may create a scratch database on. It runs the real
migrations there, starts the real app under uvicorn and signs in as placeholder users. Each exchange
is written to `docs/fe-captures/series-project-link/NN-name.json`, so every claim in
`docs/fe-integration-guide-series-project-link.md` can be re-derived by running this again.

What is real: the app, auth, CSRF, validation, team roles and visibility, the database, migrations,
serialisation, every error, the sprint rows (made by `sprints.ensure_series`, the code that makes
them when a team first uses sprints) and the one-hour reminder mail built by the real builder
(`series_mail.queue_due_reminders`, `mail.deliver_pending`) to prove the page and the mail agree.

What is NOT real, and is labelled where it matters:
* **The mail provider.** The message is built and handed to a capturing mailer instead of Resend.
* **Time.** The reminder is queued "as if" it were 55 minutes before the next Monday meeting.
* **Google.** The calendar events and joins are rows as the sync leaves them.
* **Filing a task into the linked project** happens inside the model run (`workspace.run_extraction`),
  which needs the model: it is covered by `tests/test_projects_filing.py`, not captured here.
* The recordings, transcripts and notes: meetings are inserted as the pipeline leaves them.
Nobody's real meeting or address is involved; every name and address is a placeholder.

Secrets are masked: CSRF tokens become `<csrf-A>`, `<csrf-B>` in order of first appearance, cookies
`<cookie>`.
"""


# ruff: noqa: E501  (the notes are prose for the guide, one long line each)

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
OUT = ROOT / "docs" / "fe-captures" / "series-project-link"
DB_NAME = "notetaker_capture_series_project"
ADMIN_EMAIL = "admin@example.test"
ADMIN_PASSWORD = "-".join(["capture", "admin", "placeholder"])
PEOPLE = {  # email -> (name, password); all placeholders
    "ada@example.test": ("Ada Lovelace", "-".join(["ada", "chosen", "password", "1"])),
    "dan@example.test": ("Dan Okoro", "-".join(["dan", "chosen", "password", "2"])),
    "eve@example.test": ("Eve Outsider", "-".join(["eve", "chosen", "password", "3"])),
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

from app import accounts, mail, series, series_mail, sprints, teams  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import (  # noqa: E402
    AppState,
    CalendarConnection,
    CalendarEvent,
    MailOutbox,
    Meeting,
    ScheduledJoin,
    Series,
    TeamMember,
    Template,
    User,
)
from app.passwords import hash_password  # noqa: E402
from app.services import get_vexa  # noqa: E402


def hide_tokens(value):
    """Passwords never reach a capture: the sign-in body shows a placeholder."""
    if isinstance(value, dict) and "password" in value:
        return {**value, "password": "<password placeholder>"}
    return value


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

    def call(self, name, method, path, *, body=None, csrf="session", note=""):
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
            "request": {
                "method": method,
                "path": hide_tokens(path),
                "headers": sent,
                "body": hide_tokens(body),
            },
            "response": {"status": response.status_code, "headers": kept, "body": shown},
        }
        (OUT / f"{Recorder.count:02d}-{name}.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False) + "\n"
        )
        print(f"{Recorder.count:02d}-{name:46} {method:6} {path:40} -> {response.status_code}")
        return response


def note_file(name: str, note: str, content) -> None:
    Recorder.count += 1
    record = {"note": note, "content": hide_tokens(content)}
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


class CapturingMailer:
    def __init__(self):
        self.sent = []

    def send(self, message, key):
        self.sent.append(message)


NOTES = (
    "## Summary\nThe team reviewed Client Portal progress ahead of the launch. Payment checkout is "
    "the main risk. A second sentence that makes the summary a little longer.\n\n"
    "## Decisions\n- Use Slack for work updates.\n- Finish checkout this week.\n\n"
    "## Action items\n- Dan to finish payment checkout by Friday.\n- Someone to log design refinements.\n\n"
    "## Open questions\n- Which pricing model will the team choose?\n- Who covers support in December?\n\n"
    "## Please check\nNone noted\n"
)
SPEAKERS = "[Ada Lovelace]: Let us start.\n[Dan Okoro]: I will finish checkout.\n[Guest Visitor]: Hello all."


def add_meeting(session, owner_id, template_id, when, title, **fields):
    meeting = Meeting(
        owner_user_id=owner_id, url="https://meet.google.com/abc-defg-hij", platform="google_meet",
        native_id="abc-defg-hij", template_id=template_id, status="ready", step="done",
        title=title, transcript=SPEAKERS, speaker_transcript=SPEAKERS, notes=NOTES,
        created_at=when, recorded_seconds=2760, **{"source": "calendar", **fields},
    )  # fmt: skip
    session.add(meeting)
    session.commit()
    return meeting


def run(base: str, engine) -> None:
    now = datetime.now(UTC)
    clients = {
        email: Recorder(base, name.split()[0].lower()) for email, (name, _) in PEOPLE.items()
    }
    ada, dan, eve = (clients[e] for e in PEOPLE)
    with Session(engine) as session:
        ids = {}
        for email, (name, password) in PEOPLE.items():
            user = User(
                email=email, name=name, password_hash=hash_password(password),
                email_verified_at=now,
            )  # fmt: skip
            session.add(user)
            session.flush()
            accounts.add_user_defaults(session, user)
            ids[email] = user.id
        state = session.get(AppState, 1) or AppState(id=1)
        state.max_accounts = 10
        session.add(state)
        session.commit()
        template_id = session.scalars(
            select(Template.id).where(Template.owner_user_id == ids["ada@example.test"], Template.name == "General")
        ).one()  # fmt: skip
        team = teams.ensure_team(session, session.get(User, ids["ada@example.test"]))
        team_id = team.id
        # Dan joins Ada's team as a plain member (invites are covered by the team guide)
        dan_team = teams.team_of(session, ids["dan@example.test"])
        member = session.scalars(
            select(TeamMember).where(TeamMember.user_id == ids["dan@example.test"])
        ).one()
        member.team_id, member.role = team.id, "member"
        session.flush()
        if dan_team is not None:
            session.delete(dan_team)
        team.reminders_on = True
        session.commit()
        conn = CalendarConnection(
            user_id=ids["ada@example.test"],
            granted_scopes="https://www.googleapis.com/auth/calendar.events.readonly",
        )
        session.add(conn)
        session.commit()
        # The next Monday 14:00 UTC that is still ahead: the "Next meeting" of Monday planning.
        monday = datetime(now.year, now.month, now.day, 14, 0, tzinfo=UTC)
        while monday.weekday() != 0 or monday <= now:
            monday += timedelta(days=1)

        def event(rid, title, starts, n):
            row = CalendarEvent(
                user_id=ids["ada@example.test"], connection_id=conn.id, source_event_id=f"{rid}-{n}",
                recurring_event_id=rid, title=title, starts_at=starts, ends_at=starts + timedelta(minutes=45),
                platform="google_meet", meeting_url="https://meet.google.com/abc-defg-hij",
                native_id="abc-defg-hij", status="confirmed",
            )  # fmt: skip
            session.add(row)
            session.commit()
            return row

        planning = [
            event("rid-planning", "Beyric Monday planning", monday + timedelta(weeks=w), n)
            for n, w in enumerate((-2, -1, 0, 1))
        ]
        meetings = []
        for row in planning[:2]:
            meeting = add_meeting(
                session,
                ids["ada@example.test"],
                template_id,
                row.starts_at,
                "Beyric Monday planning",
            )
            session.add(ScheduledJoin(
                user_id=ids["ada@example.test"], calendar_event_id=row.id, platform="google_meet",
                native_id="abc-defg-hij", meeting_url=row.meeting_url, starts_at=row.starts_at,
                ends_at=row.ends_at, run_at=row.starts_at, deadline_at=row.ends_at, state="completed",
                meeting_id=meeting.id,
            ))  # fmt: skip
            session.commit()
            series.link_from_calendar(session, meeting)
            session.commit()
            meetings.append(meeting)
        planning_series = meetings[0].series_id
        # Friday review: a second recurring meeting whose next occurrence is on a Friday
        friday = monday + timedelta(days=4)
        review_events = [
            event("rid-review", "Beyric Friday review", friday + timedelta(weeks=w), n)
            for n, w in enumerate((-2, -1, 0))
        ]
        review_meetings = []
        for row in review_events[:2]:
            past = add_meeting(
                session, ids["ada@example.test"], template_id, row.starts_at, "Beyric Friday review"
            )
            session.add(ScheduledJoin(
                user_id=ids["ada@example.test"], calendar_event_id=row.id, platform="google_meet",
                native_id="abc-defg-hij", meeting_url=row.meeting_url, starts_at=row.starts_at,
                ends_at=row.ends_at, run_at=row.starts_at, deadline_at=row.ends_at, state="completed",
                meeting_id=past.id,
            ))  # fmt: skip
            session.commit()
            series.link_from_calendar(session, past)
            session.commit()
            review_meetings.append(past)
        review_series = review_meetings[0].series_id
        # Ada shares one meeting of each with the team, so a teammate can see both recurring meetings
        meetings[1].shared_team_id = team_id
        review_meetings[1].shared_team_id = team_id
        session.commit()
        # Dan's own private recurring call: Ada (owner of the team) cannot see it
        private = Series(team_id=team_id, owner_user_id=ids["dan@example.test"], title="Dan 1:1")
        session.add(private)
        session.commit()
        private_id = private.id
        # The team's sprints, made by the code that makes them: sprint 1 is the Monday-to-Friday
        # week before `monday`, sprint 2 starts on `monday`, the day of the next planning meeting.
        sprints.ensure_series(session, session.get(type(team), team_id), None, today=(monday - timedelta(days=7)).date())  # fmt: skip
        session.commit()
        monday_at = monday
    for email, rec in clients.items():
        rec.call("session", "GET", "/api/session")
        rec.call(
            "login", "POST", "/api/session", body={"email": email, "password": PEOPLE[email][1]}
        )
    anon = Recorder(base, "visitor")
    link = f"/api/series/{planning_series}/project"

    anon.call("link-logged-out", "PUT", link, body={"project_id": 1}, note="No login: 401.")
    ada.call("cards-before", "GET", "/api/series", note="Every card has `project`: null until linked. Ada does not see Dan's private 1:1.")  # fmt: skip
    portal = ada.call("project-create-portal", "POST", "/api/projects", body={"name": "Client Portal", "keywords": ["portal", "checkout"]}).json()  # fmt: skip
    notetaker = ada.call("project-create-notetaker", "POST", "/api/projects", body={"name": "Beyric Note Taker"}).json()  # fmt: skip
    eve_project = eve.call("project-create-outsider", "POST", "/api/projects", body={"name": "Eve project"}, note="Eve is the owner of her own team of one: she may make projects there.").json()  # fmt: skip
    ada.call(
        "projects-before", "GET", "/api/projects", note="`recurring_meetings` is 0 everywhere."
    )
    ada.call("link-planning", "PUT", link, body={"project_id": portal["id"]}, note="Owner links Monday planning to Client Portal. The answer is the series' card.")  # fmt: skip
    ada.call("link-again", "PUT", link, body={"project_id": portal["id"]}, note="The same call again: same answer, nothing changes.")  # fmt: skip
    ada.call("link-review", "PUT", f"/api/series/{review_series}/project", body={"project_id": portal["id"]}, note="Friday review too.")  # fmt: skip
    ada.call("cards-linked", "GET", "/api/series", note="`project` on each card: this is what the checklist ticks, and \"Linked to {name}\" is its name when ticked for another project.")  # fmt: skip
    ada.call(
        "projects-linked", "GET", "/api/projects", note="Client Portal: `recurring_meetings` 2."
    )
    dan.call("projects-member", "GET", "/api/projects", note="A member reads the same count: Dan can see both recurring meetings (he is on the team; Ada shared the meetings).")  # fmt: skip
    dan.call("link-member", "PUT", link, body={"project_id": notetaker["id"]}, note="A plain member may not link: 403 `team_forbidden`.")  # fmt: skip
    dan.call("unlink-member", "PUT", link, body={"project_id": None}, note="Nor unlink: 403.")
    ada.call("link-move", "PUT", link, body={"project_id": notetaker["id"]}, note="Linking to another project MOVES the recurring meeting (a recurring meeting is in at most one project).")  # fmt: skip
    ada.call("projects-moved", "GET", "/api/projects", note="Client Portal 1, Beyric Note Taker 1.")
    ada.call("link-private-series", "PUT", f"/api/series/{private_id}/project", body={"project_id": portal["id"]}, note="Ada is the team owner but cannot see Dan's private 1:1: 404, as for reading it.")  # fmt: skip
    ada.call("link-foreign-project", "PUT", link, body={"project_id": eve_project["id"]}, note="A project of another team: 404 `not_found`.")  # fmt: skip
    ada.call("link-missing-project", "PUT", link, body={"project_id": 999999}, note="404.")
    ada.call("link-missing-series", "PUT", "/api/series/999999/project", body={"project_id": portal["id"]}, note="404.")  # fmt: skip
    eve.call("link-outsider", "PUT", link, body={"project_id": eve_project["id"]}, note="Eve is on another team: 404 for the series.")  # fmt: skip
    ada.call("link-bad-body", "PUT", link, body={}, note="`project_id` is required (null to unlink): 422.")  # fmt: skip
    ada.call("link-zero", "PUT", link, body={"project_id": 0}, note="422.")
    ada.call("link-no-csrf", "PUT", link, body={"project_id": None}, csrf=None, note="No CSRF token: 403 `csrf_rejected` (the same rule as every write).")  # fmt: skip
    ada.call("unlink", "PUT", link, body={"project_id": None}, note="Unlink: `project` null.")
    ada.call("link-planning-portal-again", "PUT", link, body={"project_id": portal["id"]})
    ada.call("project-delete", "DELETE", f"/api/projects/{portal['id']}", note="Deleting a project unlinks its recurring meetings (Friday review was linked to it).")  # fmt: skip
    ada.call("cards-after-delete", "GET", "/api/series", note="Both cards have `project` null (planning was linked to Client Portal again just before the delete).")  # fmt: skip

    # --- the sprint line ---
    ada.call("page-planning", "GET", f"/api/series/{planning_series}", note="`sprint_note`: the next meeting (Monday 14:00 UTC) is the day sprint 2 starts and nothing of the series has happened since the sprint began: \"Opens Sprint 2\".")  # fmt: skip
    dan.call("page-planning-teammate", "GET", f"/api/series/{planning_series}", note="The same rule for a teammate.")  # fmt: skip
    ada.call("page-review", "GET", f"/api/series/{review_series}", note="The next meeting is the Friday: no sprint starts that day, so `sprint_note` is null. There is no rule for \"Closes Sprint N\" yet, so it is never sent.")  # fmt: skip
    with Session(engine) as session:
        when = monday_at - timedelta(minutes=55)
        queued = series_mail.queue_due_reminders(session, when)
        rows = list(
            session.scalars(
                select(MailOutbox)
                .where(MailOutbox.template == "series_reminder")
                .order_by(MailOutbox.id)
            )
        )
        note_file("reminder-queued", f"queue_due_reminders at {when:%a %d %b %H:%M} UTC returned {queued}: the one-hour reminders for the Monday planning occurrence (and nothing for Friday). Ids only.", [dict(to=r.to_email, payload=r.payload) for r in rows])  # fmt: skip
        mailer = CapturingMailer()
        mail.deliver_pending(
            session,
            mailer,
            Settings(_env_file=None, public_base_url="https://notes.example.test"),
            when + timedelta(minutes=1),
        )
        note_file("reminder-1h-rendered", "The one-hour reminder for the same occurrence as built (CAPTURING MAILER): its meta line says \"Sprint 2 opens\", what the page says as \"Opens Sprint 2\".", [dict(to=m.to, subject=m.subject, text=m.text) for m in mailer.sent])  # fmt: skip


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
