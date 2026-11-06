"""Drive the Series API over real HTTP and save every exchange for the frontend guide.

    python scripts/capture_series_api.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres

The argument is any Postgres server the script may create a scratch database on. It runs the real
migrations there, starts the real app under uvicorn and signs in as placeholder users. Each exchange
is written to `docs/fe-captures/series-w8/NN-name.json`, so every claim in
`docs/fe-integration-guide-series.md` can be re-derived by running this again.

What is real: the app, auth, CSRF, validation, team roles and visibility, the database, migrations,
serialisation, every error, the linking of a calendar meeting to its recurring meeting
(`series.link_from_calendar`), the reminder queueing (`series_mail.queue_due_reminders`) and the
rendering of the agenda and reminder mails by the real builders (`mail.deliver_pending`).

What is NOT real, and is labelled where it matters:
* **The mail provider.** Messages are built and handed to a capturing mailer instead of Resend.
* **Time.** The "occurrence in 55 minutes" is a calendar-event row made for that moment; nothing waits.
* **Google.** The calendar events and joins are rows as the sync and the scheduler leave them; the
  Google round-trip itself is covered by the calendar guides.
* The recording, transcription and speaker steps and the bot: meetings are inserted as the
  pipeline leaves them (`status ready`).
Nobody's real meeting or address is involved; every name and address is a placeholder.

Secrets in the files are masked: CSRF tokens become `<csrf-A>`, `<csrf-B>` in order of first
appearance, cookies `<cookie>`, and the token inside an invitation link `<invite-token-N>`.
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
OUT = ROOT / "docs" / "fe-captures" / "series-w8"
DB_NAME = "notetaker_capture_series"
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

from app import accounts, mail, series, series_mail  # noqa: E402  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import (  # noqa: E402
    AppState,
    CalendarConnection,
    CalendarEvent,
    Decision,
    MailOutbox,
    Meeting,
    OpenQuestion,
    ScheduledJoin,
    Task,
    TeamMember,
    Template,
    User,
)
from app.passwords import hash_password  # noqa: E402
from app.services import get_vexa  # noqa: E402


def hide_tokens(value):
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
        from app import teams

        team = teams.ensure_team(session, session.get(User, ids["ada@example.test"]))
        # Dan joins Ada's team (invites are covered by the team guide)
        dan_team = teams.team_of(session, ids["dan@example.test"])
        member = session.scalars(
            select(TeamMember).where(TeamMember.user_id == ids["dan@example.test"])
        ).one()
        member.team_id, member.role = team.id, "member"
        session.flush()
        if dan_team is not None:
            session.delete(dan_team)
        session.commit()
        # Ada's calendar: two recurring events, each with a recorded past meeting
        conn = CalendarConnection(
            user_id=ids["ada@example.test"],
            granted_scopes="https://www.googleapis.com/auth/calendar.events.readonly",
        )
        session.add(conn)
        session.commit()
        monday = datetime(now.year, now.month, now.day, 14, 0, tzinfo=UTC)
        while monday.weekday() != 0:
            monday += timedelta(days=1)
        monday += timedelta(days=7)

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

        # Monday planning: weekly, three occurrences (two past, one ahead), recorded twice
        planning = []
        for n, weeks in enumerate((-2, -1, 0, 1)):
            planning.append(
                event("rid-planning", "Beyric Monday planning", monday + timedelta(weeks=weeks), n)
            )
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
        # Friday review: the occurrence that is 55 minutes away
        soon = event("rid-review", "Beyric Friday review", now + timedelta(minutes=55), 0)
        review_past = add_meeting(
            session,
            ids["ada@example.test"],
            template_id,
            now - timedelta(days=7),
            "Beyric Friday review",
        )
        session.add(ScheduledJoin(
            user_id=ids["ada@example.test"], calendar_event_id=soon.id, platform="google_meet",
            native_id="abc-defg-hij", meeting_url=soon.meeting_url, starts_at=soon.starts_at,
            ends_at=soon.ends_at, run_at=soon.starts_at, deadline_at=soon.ends_at, state="scheduled",
            meeting_id=review_past.id,
        ))  # fmt: skip
        session.commit()
        series.link_from_calendar(session, review_past)
        session.commit()
        for meeting in (*meetings, review_past):
            for position, text in enumerate(
                ["Which pricing model will the team choose?", "Who covers support in December?"]
            ):
                from app.extraction import fingerprint

                session.add(
                    OpenQuestion(
                        meeting_id=meeting.id,
                        text=text,
                        position=position,
                        fingerprint=fingerprint(text),
                    )
                )
            session.add(
                Decision(
                    meeting_id=meeting.id,
                    text="Finish checkout this week.",
                    position=0,
                    fingerprint="d1",
                )
            )
            for title, who, status in (
                ("Finish payment checkout", ids["dan@example.test"], "todo"),
                ("Log design refinements on the tracker", None, "todo"),
            ):
                session.add(Task(team_id=team.id, meeting_id=meeting.id, title=title, source="ai", ai_fingerprint=f"{meeting.id}-{title}", assignee_user_id=who, status=status, assigned_on=meeting.created_at.date()))  # fmt: skip
        session.add(Task(team_id=team.id, meeting_id=meetings[0].id, title="Send the pricing draft", source="ai", ai_fingerprint="done-1", assignee_user_id=ids["ada@example.test"], status="done", done_at=meetings[0].created_at + timedelta(days=1), assigned_on=meetings[0].created_at.date()))  # fmt: skip
        session.commit()
        # Ada's second meeting is shared with the team; her first stays private
        meetings[1].shared_team_id = team.id
        review_past.shared_team_id = team.id
        session.commit()
        planning_series = meetings[0].series_id
        review_series = review_past.series_id
        last_id = meetings[1].id
        open_question = session.scalars(select(OpenQuestion.id).where(OpenQuestion.meeting_id == last_id).order_by(OpenQuestion.position)).first()  # fmt: skip
        lone = add_meeting(
            session,
            ids["ada@example.test"],
            template_id,
            now - timedelta(days=2),
            "A one-off call",
            source="manual",
        )
        lone_id = lone.id
    for email, rec in clients.items():
        rec.call("session", "GET", "/api/session")
        rec.call(
            "login", "POST", "/api/session", body={"email": email, "password": PEOPLE[email][1]}
        )
    anon = Recorder(base, "visitor")
    anon.call("series-logged-out", "GET", "/api/series", note="No login: 401.")

    # --- the cards and the page ---
    ada.call("cards-owner", "GET", "/api/series", note="The Recurring cards. Ada sees her private meeting's tasks and questions too.")  # fmt: skip
    dan.call("cards-teammate", "GET", "/api/series", note="Dan sees only what he may read: the counts differ from Ada's.")  # fmt: skip
    eve.call("cards-outsider", "GET", "/api/series", note="Another team: empty.")
    ada.call("page-owner", "GET", f"/api/series/{planning_series}", note="The Series page as the owner of the meetings sees it.")  # fmt: skip
    dan.call("page-teammate", "GET", f"/api/series/{planning_series}", note="Same page for a teammate: the private meeting is not listed and its tasks and questions are not counted.")  # fmt: skip
    eve.call("page-outsider", "GET", f"/api/series/{planning_series}", note="404 for another team.")
    ada.call("page-review", "GET", f"/api/series/{review_series}")

    # --- the meeting page ---
    ada.call("meeting-detail", "GET", f"/api/meetings/{last_id}", note="`series` and `open_questions` are new on the meeting.")  # fmt: skip
    ada.call("meeting-detail-lone", "GET", f"/api/meetings/{lone_id}", note="A meeting in no series: `series` null, `carriable` false.")  # fmt: skip
    dan.call("meeting-detail-teammate", "GET", f"/api/meetings/{last_id}", note="A teammate reading the shared meeting: `can_update` false.")  # fmt: skip

    # --- questions ---
    ada.call("question-off", "PATCH", f"/api/questions/{open_question}", body={"carry": False}, note="\"Add to next agenda\" state.")  # fmt: skip
    ada.call("question-on", "PATCH", f"/api/questions/{open_question}", body={"carry": True}, note="\"On next agenda\".")  # fmt: skip
    dan.call("question-teammate", "PATCH", f"/api/questions/{open_question}", body={"resolved": True}, note="A member who can only read: 403 `team_forbidden`.")  # fmt: skip
    eve.call("question-outsider", "PATCH", f"/api/questions/{open_question}", body={"resolved": True}, note="404.")  # fmt: skip
    ada.call("question-empty", "PATCH", f"/api/questions/{open_question}", body={}, note="422.")
    ada.call("question-resolve", "PATCH", f"/api/questions/{open_question}", body={"resolved": True}, note="\"Mark resolved\".")  # fmt: skip
    ada.call("page-after-resolve", "GET", f"/api/series/{planning_series}", note="The resolved question is gone from the carried list.")  # fmt: skip

    # --- the agenda ---
    agenda = f"/api/series/{planning_series}/agenda"
    ada.call("agenda-add", "POST", agenda, body={"text": "Budget review"})
    dan.call(
        "agenda-add-teammate",
        "POST",
        agenda,
        body={"text": "Hiring update"},
        note="Any member may add.",
    )
    ada.call("agenda-link", "POST", agenda, body={"text": "See https://example.test/x"}, note="A web address is refused: 422 `invalid_agenda_item`.")  # fmt: skip
    ada.call("agenda-empty", "POST", agenda, body={"text": "   "}, note="422.")
    listed = dan.call("agenda-list", "GET", agenda, note="`can_remove` is per line: its author or a manager.")  # fmt: skip
    ada_line = next(i["id"] for i in listed.json() if i["text"] == "Budget review")
    dan.call("agenda-remove-not-mine", "DELETE", f"{agenda}/{ada_line}", note="403.")
    ada.call("agenda-remove", "DELETE", f"{agenda}/{ada_line}", note="204.")
    ada.call("agenda-remove-again", "DELETE", f"{agenda}/{ada_line}", note="404.")
    ada.call("agenda-add-again", "POST", agenda, body={"text": "Budget review"})

    # --- send the agenda ---
    eve.call("send-outsider", "POST", f"{agenda}/send", note="404.")
    ada.call("send-agenda", "POST", f"{agenda}/send", note="Goes to teammates who were in the last three meetings, not the sender.")  # fmt: skip
    dan.call("send-agenda-teammate", "POST", f"{agenda}/send", note="Dan is a teammate too; he sends to Ada, who attended.")  # fmt: skip
    with Session(engine) as session:
        rows = list(
            session.scalars(
                select(MailOutbox)
                .where(MailOutbox.template == "series_agenda")
                .order_by(MailOutbox.id)
            )
        )
        note_file("agenda-outbox", "Queued agenda mails: ids only, no text.", [dict(to=r.to_email, template=r.template, payload=r.payload, status=r.status) for r in rows])  # fmt: skip
        mailer = CapturingMailer()
        later = datetime.now(UTC) + timedelta(minutes=1)
        mail.deliver_pending(
            session,
            mailer,
            Settings(_env_file=None, public_base_url="https://notes.example.test"),
            later,
        )
        note_file("agenda-rendered", "What the mail provider would be handed (CAPTURING MAILER, not Resend).", [dict(to=m.to, subject=m.subject, text=m.text) for m in mailer.sent])  # fmt: skip

    # --- hand attach and make ---
    ada.call("series-create", "POST", "/api/series", body={"title": "Design sync"}, note="Made by hand: no calendar, no next date.")  # fmt: skip
    ada.call("series-create-empty", "POST", "/api/series", body={"title": "  "}, note="422.")
    created = ada.call("series-create-2", "POST", "/api/series", body={"title": "Hand made"}).json()
    ada.call("attach", "PUT", f"/api/meetings/{lone_id}/series", body={"series_id": created["id"]}, note="Only the meeting's owner may.")  # fmt: skip
    dan.call("attach-teammate", "PUT", f"/api/meetings/{lone_id}/series", body={"series_id": created["id"]}, note="404 for a teammate.")  # fmt: skip
    eve.call("attach-foreign-series", "PUT", f"/api/meetings/{lone_id}/series", body={"series_id": created["id"]}, note="404.")  # fmt: skip
    ada.call("detach", "PUT", f"/api/meetings/{lone_id}/series", body={"series_id": None})
    ada.call(
        "rename", "PATCH", f"/api/series/{created['id']}", body={"title": "Design sync (weekly)"}
    )
    dan.call("rename-teammate", "PATCH", f"/api/series/{created['id']}", body={"title": "No"}, note="403: not the creator and not a manager.")  # fmt: skip

    # --- reminders ---
    ada.call("prefs-default", "GET", "/api/settings/reminders")
    dan.call("team-reminders-member", "PATCH", "/api/team", body={"reminders_on": True}, note="403 for a member.")  # fmt: skip
    ada.call("team-before", "GET", "/api/team", note="`reminders_on` is false until a manager switches it on.")  # fmt: skip
    ada.call("team-reminders-on", "PATCH", "/api/team", body={"reminders_on": True, "reminder_timing": "both"})  # fmt: skip
    ada.call("team-bad-timing", "PATCH", "/api/team", body={"reminder_timing": "2h"}, note="422.")
    with Session(engine) as session:
        queued = series_mail.queue_due_reminders(session, datetime.now(UTC))
        rows = list(
            session.scalars(
                select(MailOutbox)
                .where(MailOutbox.template == "series_reminder")
                .order_by(MailOutbox.id)
            )
        )
        note_file("reminder-queued", f"queue_due_reminders returned {queued}: one 1-hour reminder per attendee. Ids only.", [dict(to=r.to_email, payload=r.payload) for r in rows])  # fmt: skip
        mailer = CapturingMailer()
        mail.deliver_pending(
            session,
            mailer,
            Settings(_env_file=None, public_base_url="https://notes.example.test"),
            datetime.now(UTC) + timedelta(minutes=1),
        )
        note_file("reminder-1h-rendered", "The 1-hour reminder as built (CAPTURING MAILER).", [dict(to=m.to, subject=m.subject, text=m.text) for m in mailer.sent if "1 hour" in m.subject])  # fmt: skip
        again = series_mail.queue_due_reminders(session, datetime.now(UTC))
        note_file("reminder-requeue", f"Running the queue again returns {again}: once per person and occurrence.", {"queued": again})  # fmt: skip
        later = datetime.now(UTC) + timedelta(minutes=26)
        thirty = series_mail.queue_due_reminders(session, later)
        mailer = CapturingMailer()
        session.execute(MailOutbox.__table__.update().where(MailOutbox.status == "pending").values(created_at=later - timedelta(minutes=2)))  # fmt: skip
        session.commit()
        mail.deliver_pending(
            session,
            mailer,
            Settings(_env_file=None, public_base_url="https://notes.example.test"),
            later,
        )
        note_file("reminder-30m-rendered", f"26 minutes later {thirty} more are queued: the 30-minute mail (CAPTURING MAILER).", [dict(to=m.to, subject=m.subject, text=m.text) for m in mailer.sent if "30 minutes" in m.subject])  # fmt: skip
    dan.call("prefs-off", "PUT", "/api/settings/reminders", body={"notify_reminders": False}, note="Dan opts out of agenda and reminder mail.")  # fmt: skip
    dan.call("prefs-after", "GET", "/api/settings/reminders")
    ada.call("send-after-optout", "POST", f"{agenda}/send", note="Dan no longer counts as a recipient: nobody left, 409 `no_attendees`.")  # fmt: skip


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
