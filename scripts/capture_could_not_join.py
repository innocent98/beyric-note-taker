"""Capture what the front end gets when the bot was NOT admitted, over real HTTP, for the guide.

    python scripts/capture_could_not_join.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

The argument is any Postgres server the script may create a scratch database on. It runs the real
migrations there, makes three Google Meet meetings the bot "was sent to" and lets the REAL pipeline
(`pipeline.advance_meeting`) see Vexa report: the waiting room timed out, the host declined, and an
ordinary bot failure (which must say nothing). Then it starts the real app under uvicorn, logs in
and fetches the bell. The queued "Could not join" mail is rendered by the real template and written
next to the responses. Each exchange goes to `docs/fe-captures/could-not-join/NN-name.json`.

What is real: the app, the pipeline, the alerts code, the mail template, CSRF and every response.
What is not: Vexa (a stub that answers with the three states). Cookie values and CSRF tokens are
masked; the e-mail address and password are placeholders.
"""

import json
import os
import re
import shutil
import socket
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "fe-captures" / "could-not-join"
DB_NAME = "notetaker_capture_not_joined"
ADMIN_EMAIL = "admin@example.test"  # the admin account migration 0006 creates (a placeholder)
ADMIN_PASSWORD = "-".join(["capture", "admin", "placeholder"])

ADMIN_URL = sys.argv[1] if len(sys.argv) > 1 else ""
SCRATCH_URL = urlparse(ADMIN_URL)._replace(path=f"/{DB_NAME}").geturl()
os.environ["DATABASE_URL"] = SCRATCH_URL
os.environ["OWNER_EMAIL"] = ADMIN_EMAIL
os.environ["OWNER_PASSWORD"] = ADMIN_PASSWORD
# The pipeline reads the cached environment settings for its alerts: mail goes to the (console)
# outbox outside production, as it does in the tests.
os.environ["APP_ENV"] = "test"
os.environ["SESSION_SECRET"] = "capture-secret-" + "x" * 30

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import mail_templates, pipeline  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import MailOutbox, Meeting, Template, User, UserSettings  # noqa: E402
from app.vexa import VexaMeeting  # noqa: E402

SEEN = datetime(2026, 11, 7, 11, 58, tzinfo=UTC)  # the poll that saw the failure
TIMED_OUT = VexaMeeting(status="failed", completion_reason="awaiting_admission_timeout")
DECLINED = VexaMeeting(status="failed", completion_reason="awaiting_admission_rejected")
CRASHED = VexaMeeting(status="failed", failure_reason="Chrome crashed")


class StubVexa:
    """Answers every lookup with one fixed state."""

    def __init__(self, answer: VexaMeeting) -> None:
        self.answer = answer

    def get_meeting(self, platform, native_id):
        return self.answer

    def get_meeting_by_id(self, vexa_meeting_id):
        return self.answer


class Recorder:
    count = 0
    csrf: str | None = None

    def __init__(self, base_url: str) -> None:
        self.http = httpx.Client(base_url=base_url, follow_redirects=False)

    def call(self, name: str, method: str, path: str, *, body=None, note: str = ""):
        headers = {}
        if method != "GET" and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        response = self.http.request(method, path, json=body, headers=headers)
        is_json = "json" in response.headers.get("content-type", "")
        data = response.json() if is_json else f"<{len(response.content)} bytes of the file>"
        if isinstance(data, dict) and "csrf_token" in data:
            self.csrf = data["csrf_token"]
            data = {**data, "csrf_token": "<csrf-A>"}
        kept = {
            k: response.headers[k]
            for k in ("content-type", "content-disposition", "cache-control")
            if k in response.headers
        }
        Recorder.count += 1
        record = {
            "note": note,
            "request": {"method": method, "path": path, "body": body},
            "response": {"status": response.status_code, "headers": kept, "body": data},
        }
        text_ = re.sub(r"\bsession=[\w.-]+", "session=<cookie>", json.dumps(record, indent=2))
        (OUT / f"{Recorder.count:02d}-{name}.json").write_text(
            json.dumps(json.loads(text_), indent=2, ensure_ascii=False) + "\n"
        )
        print(f"{Recorder.count:02d}-{name:34} {method:5} {path[:44]:44} -> {response.status_code}")
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
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f"drop database if exists {DB_NAME} with (force)"))
        connection.execute(text(f"create database {DB_NAME}"))
    admin.dispose()
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")


def seed(engine, settings) -> None:
    """Three meetings the bot was sent to, each seen failing by the real pipeline."""
    cases = [
        ("btp-tkio-gtb", TIMED_OUT),
        ("ddd-eeee-fff", DECLINED),
        ("ggg-hhhh-iii", CRASHED),
    ]
    with Session(engine) as session:
        user = session.scalars(select(User).where(User.email == ADMIN_EMAIL)).one()
        template = session.scalars(
            select(Template).where(Template.owner_user_id == user.id)
        ).first()
        existing = session.get(UserSettings, user.id)
        if existing is None:
            session.add(UserSettings(user_id=user.id, time_zone="Africa/Lagos"))
        else:
            existing.time_zone = "Africa/Lagos"
        meetings = []
        for code, _ in cases:
            meeting = Meeting(
                owner_user_id=user.id,
                url=f"https://meet.google.com/{code}",
                platform="google_meet",
                native_id=code,
                template_id=template.id,
                vexa_meeting_id="1",
                started_polling_at=SEEN - timedelta(minutes=15),
            )
            session.add(meeting)
            meetings.append(meeting)
        session.commit()
        for meeting, (_, answer) in zip(meetings, cases, strict=True):
            pipeline.advance_meeting(
                session, meeting.id, vexa=StubVexa(answer), openai_client=None, now=SEEN
            )
        # A second failure of the same meeting changes nothing (the dedupe).
        meetings[0].status, meetings[0].step = "bot_sent", "waiting"
        session.commit()
        pipeline.advance_meeting(
            session, meetings[0].id, vexa=StubVexa(TIMED_OUT), openai_client=None, now=SEEN
        )
        for meeting in meetings:
            session.refresh(meeting)
            print(meeting.native_id, meeting.status, "|", meeting.failure_reason)
        mails = list(session.scalars(select(MailOutbox).order_by(MailOutbox.id)))
        print("mails queued:", [(m.template, m.payload) for m in mails])
        rendered = []
        for row in mails:
            message = mail_templates.render_link(row.template, row.payload, row.to_email, settings)
            rendered.append(
                {
                    "template": row.template,
                    "payload": row.payload,
                    "subject": message.subject,
                    "text": message.text,
                }
            )
        (OUT / "mails.json").write_text(json.dumps(rendered, indent=2, ensure_ascii=False) + "\n")


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
        database_url=SCRATCH_URL,
        public_base_url="https://notes.example.test",
    )
    engine = create_engine(SCRATCH_URL)
    seed(engine, settings)
    app = create_app(settings)
    server, base = start_server(app)
    try:
        client = Recorder(base)
        client.call("session", "GET", "/api/session", note="Read the CSRF token.")
        client.call(
            "login",
            "POST",
            "/api/session",
            body={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            note="Log in.",
        )
        client.call(
            "notifications",
            "GET",
            "/api/notifications",
            note="The bell: two 'could not join' entries (waiting room timeout, host declined), "
            "none for the crashed bot, none for the repeated failure.",
        )
        client.call(
            "meetings-list", "GET", "/api/meetings", note="The meetings the bell points at."
        )
    finally:
        server.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    main()
