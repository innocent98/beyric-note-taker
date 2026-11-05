"""Drive the time-zone API and the notes mail over real HTTP and save every exchange for
`docs/fe-integration-guide-settings-time-zone.md`.

    python scripts/capture_time_zone.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres

The argument is any Postgres server the script may create a scratch database on. It runs the real
migrations there, starts the real app under uvicorn, and signs in as a placeholder user. Each
exchange is written to `docs/fe-captures/time-zone/NN-name.json`.

What is real: the app, auth, CSRF, validation, the database, migrations, serialisation, every
error and the mail send loop (`mail.deliver_pending`). What is not: the mailer (a recorder,
no mail provider is called), and the meetings (inserted as the pipeline leaves
them). Everyone in the files is a placeholder.
Secrets are masked: CSRF tokens become `<csrf-A>`, cookies `<cookie>`.
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
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "fe-captures" / "time-zone"
DB_NAME = "notetaker_capture_time_zone"
ADMIN_EMAIL = "admin@example.test"  # created by migration 0006 from these (placeholders)
ADMIN_PASSWORD = "-".join(["capture", "admin", "placeholder"])
TZ_URL = "/api/settings/time-zone"
ADA = "ada@example.test"  # a placeholder, not a real person
ADA_PASSWORD = "-".join(["ada", "chosen", "password", "1"])

admin_url = sys.argv[1] if len(sys.argv) > 1 else ""
scratch_url = urlparse(admin_url)._replace(path=f"/{DB_NAME}").geturl()
os.environ["DATABASE_URL"] = scratch_url  # read by app.db when it is imported below
os.environ["OWNER_EMAIL"] = ADMIN_EMAIL
os.environ["OWNER_PASSWORD"] = ADMIN_PASSWORD
os.environ.setdefault("APP_ENV", "development")

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import accounts, mail  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Meeting, User  # noqa: E402
from app.passwords import hash_password  # noqa: E402

NOTES = """\
## Summary
The team agreed to ship on Friday.

## Decisions
- Ship on Friday.

## Action items
- Ada to send the release note.

## Open questions
- Who pays for the pilot?
"""


class Recorder:
    """A logical client: a cookie jar, its CSRF token, and a numbered file for each call."""

    count = 0
    masks: dict[str, str] = {}

    def __init__(self, base_url: str, who: str) -> None:
        self.http = httpx.Client(base_url=base_url, follow_redirects=False, timeout=60)
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
        kept = {k: response.headers[k] for k in ("content-type",) if k in response.headers}
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


class RecordingMailer:
    """Stands in for the mail provider: keeps what would have been sent."""

    def __init__(self) -> None:
        self.sent = []

    def send(self, message, key) -> None:
        self.sent.append(message)


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


def run(base: str, engine, settings: Settings) -> None:
    with Session(engine) as session:
        user = User(
            email=ADA,
            name="Ada Example",
            password_hash=hash_password(ADA_PASSWORD),
            email_verified_at=datetime.now(UTC),
        )
        session.add(user)
        session.flush()
        template = accounts.add_user_defaults(session, user)[0]
        # Started 23:30 UTC on 5 Nov 2026: already the next day in Lagos (the mail shows it). One
        # meeting per mail: the outbox allows one notes mail for each meeting.
        meeting_ids = []
        for native_id in ("81234567890", "81234567891", "81234567892"):
            meeting = Meeting(
                owner_user_id=user.id,
                url="https://example.test/meeting",
                platform="zoom",
                native_id=native_id,
                template_id=template.id,
                status="ready",
                step="done",
                notes=NOTES,
                created_at=datetime(2026, 11, 5, 23, 30, tzinfo=UTC),
            )
            session.add(meeting)
            session.flush()
            meeting_ids.append(meeting.id)
        session.commit()

    anon = Recorder(base, "visitor")
    ada = Recorder(base, "ada")
    anon.call("settings-logged-out", "GET", TZ_URL, note="No login: 401.")
    ada.call("session-anonymous-ada", "GET", "/api/session")
    ada.call("login", "POST", "/api/session", body={"email": ADA, "password": ADA_PASSWORD})
    ada.call(
        "session-before", "GET", "/api/session",
        note="`user.time_zone` is null until a zone is chosen.",
    )  # fmt: skip
    ada.call(
        "time-zone-default", "GET", TZ_URL,
        note="Nothing chosen: UTC, `is_set` false.",
    )  # fmt: skip

    mailer = RecordingMailer()

    def send_notes_mail(label: str, meeting_id: int) -> None:
        with Session(engine) as session:
            mail.enqueue(session, ADA, "notes_ready", {"meeting_id": meeting_id})
            session.commit()
            sent = mail.deliver_pending(
                session, mailer, settings, datetime(2026, 11, 6, 0, 0, tzinfo=UTC)
            )
        note_file(
            label,
            "`mail.deliver_pending`: the notes-ready mail names the meeting's start.",
            {"sent": sent, "first_lines": mailer.sent[-1].text.splitlines()[:3]},
        )

    send_notes_mail("mail-before-utc", meeting_ids[0])

    ada.call(
        "time-zone-set-lagos", "PUT", TZ_URL, body={"time_zone": "Africa/Lagos"},
        note="The answer is the stored zone.",
    )  # fmt: skip
    ada.call("time-zone-read-lagos", "GET", TZ_URL)
    ada.call(
        "session-after", "GET", "/api/session",
        note="`user.time_zone` now carries it: the pages write every time in it.",
    )  # fmt: skip
    send_notes_mail("mail-after-lagos", meeting_ids[1])
    ada.call(
        "time-zone-set-kolkata", "PUT", TZ_URL, body={"time_zone": "Asia/Kolkata"},
        note="A half-hour zone.",
    )  # fmt: skip
    send_notes_mail("mail-after-kolkata", meeting_ids[2])
    ada.call("time-zone-set-utc", "PUT", TZ_URL, body={"time_zone": "UTC"})
    ada.call(
        "time-zone-read-utc", "GET", TZ_URL,
        note="Chosen on purpose: `is_set` is true although the zone is UTC.",
    )  # fmt: skip
    ada.call(
        "time-zone-unknown-refused", "PUT", TZ_URL, body={"time_zone": "Mars/Olympus"},
        note="422 `time_zone_unsupported`; nothing changes (the pages show a general line).",
    )  # fmt: skip
    ada.call(
        "time-zone-path-refused", "PUT", TZ_URL, body={"time_zone": "../etc/passwd"},
        note="A path is not a zone: the same 422.",
    )  # fmt: skip
    ada.call(
        "time-zone-empty-refused", "PUT", TZ_URL, body={"time_zone": ""},
        note="An empty name: the same 422.",
    )  # fmt: skip
    ada.call(
        "time-zone-missing-refused", "PUT", TZ_URL, body={},
        note="A missing field is a 422 validation error.",
    )  # fmt: skip
    ada.call(
        "time-zone-no-csrf", "PUT", TZ_URL, body={"time_zone": "Asia/Tokyo"},
        csrf=None, note="A write without the token: 403.",
    )  # fmt: skip
    ada.call("time-zone-read-still-utc", "GET", TZ_URL, note="Unchanged.")


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
    server, base = start_server(app)
    engine = create_engine(scratch_url)
    try:
        run(base, engine, settings)
    finally:
        server.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    main()
