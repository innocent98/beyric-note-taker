"""Drive the email-preferences API and the two new in-app notices over real HTTP and save every
exchange for `docs/fe-integration-guide-pilot-accounts.md` (section 12).

    python scripts/capture_prelaunch_mail_api.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres

The argument is any Postgres server the script may create a scratch database on. It runs the real
migrations there, starts the real app under uvicorn, and signs in as a placeholder user. Each
exchange is written to `docs/fe-captures/prelaunch-mail-prefs/NN-name.json`.

What is real: the app, auth, CSRF, validation, the database, migrations, serialisation, every
error, the hourly sweep (`maintenance.run_hourly`, which makes the privacy notice) and the mail
send loop (`mail.deliver_pending`, which skips a mail over the cap and tells the owner). What is
not: the mailer (a recorder, no mail provider is called), and the meetings (inserted as the
pipeline leaves them). Everyone in the files is a placeholder. Secrets are masked: CSRF tokens
become `<csrf-A>`, cookies `<cookie>`.
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
OUT = ROOT / "docs" / "fe-captures" / "prelaunch-mail-prefs"
DB_NAME = "notetaker_capture_prelaunch_mail"
ADMIN_EMAIL = "admin@example.test"  # created by migration 0006 from these (placeholders)
ADMIN_PASSWORD = "-".join(["capture", "admin", "placeholder"])
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
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import accounts, mail, maintenance  # noqa: E402
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
        # An account made before the privacy update (no terms version recorded), with the
        # settings row and templates every real account gets at sign-up.
        user = User(
            email=ADA,
            name="Ada Example",
            password_hash=hash_password(ADA_PASSWORD),
            email_verified_at=datetime.now(UTC),
        )
        session.add(user)
        session.flush()
        template = accounts.add_user_defaults(session, user)[0]
        for native_id, minutes in (("81234567890", 90), ("81234567891", 60)):
            session.add(
                Meeting(
                    owner_user_id=user.id,
                    url="https://example.test/meeting",
                    platform="zoom",
                    native_id=native_id,
                    template_id=template.id,
                    status="ready",
                    step="done",
                    notes=NOTES,
                    created_at=datetime.now(UTC) - timedelta(minutes=minutes),
                )
            )
        session.commit()
        meeting_ids = [m.id for m in session.scalars(select(Meeting).order_by(Meeting.id))]

    anon = Recorder(base, "visitor")
    ada = Recorder(base, "ada")
    anon.call("session-anonymous", "GET", "/api/session")
    anon.call("settings-logged-out", "GET", "/api/settings/email", note="No login: 401.")
    anon.call(
        "settings-write-logged-out", "PUT", "/api/settings/email",
        body={"notify_email": False}, note="No login: 401, before anything is read.",
    )  # fmt: skip
    ada.call("session-anonymous-ada", "GET", "/api/session")
    ada.call("login", "POST", "/api/session", body={"email": ADA, "password": ADA_PASSWORD})

    # --- email preferences ---
    ada.call("settings-read-default", "GET", "/api/settings/email", note="On by default.")
    ada.call(
        "settings-turn-off", "PUT", "/api/settings/email", body={"notify_email": False},
        note="The answer is the new value.",
    )  # fmt: skip
    ada.call("settings-read-off", "GET", "/api/settings/email")
    ada.call("settings-turn-on", "PUT", "/api/settings/email", body={"notify_email": True})
    ada.call("settings-read-on", "GET", "/api/settings/email")
    ada.call(
        "settings-string-refused", "PUT", "/api/settings/email", body={"notify_email": "false"},
        note="A string is not a boolean: 422, nothing changes.",
    )  # fmt: skip
    ada.call(
        "settings-extra-field-refused", "PUT", "/api/settings/email",
        body={"notify_email": False, "auto_join_rule": "none"},
        note="No other setting can ride along: 422.",
    )  # fmt: skip
    ada.call(
        "settings-empty-refused", "PUT", "/api/settings/email", body={},
        note="A missing field is 422, never read as off.",
    )  # fmt: skip
    ada.call(
        "settings-no-csrf", "PUT", "/api/settings/email", body={"notify_email": False},
        csrf=None, note="A write without the token: 403.",
    )  # fmt: skip
    ada.call(
        "settings-read-still-on", "GET", "/api/settings/email", note="Unchanged by the refusals."
    )

    # --- the privacy notice, made by the hourly sweep ---
    ada.call("notifications-before", "GET", "/api/notifications", note="Nothing yet.")
    with Session(engine) as session:
        maintenance.run_hourly(session, datetime.now(UTC))
    ada.call(
        "notifications-privacy-notice", "GET", "/api/notifications",
        note="After the worker's hourly sweep: one notice, `kind` `legal.updated`.",
    )  # fmt: skip
    ada.call("session-unread-one", "GET", "/api/session", note="`unread_notifications` is 1.")
    with Session(engine) as session:
        maintenance.run_hourly(session, datetime.now(UTC) + timedelta(hours=1))
    ada.call(
        "notifications-after-second-sweep", "GET", "/api/notifications",
        note="The next sweep adds nothing: still one.",
    )  # fmt: skip

    # --- a notes mail over the per-recipient cap ---
    mailer = RecordingMailer()
    with Session(engine) as session:
        for meeting_id in meeting_ids:
            mail.enqueue(session, ADA, "notes_ready", {"meeting_id": meeting_id})
        session.commit()
        # The mails were queued a moment ago: send as of a minute from now, with a cap of 1.
        capped = settings.model_copy(update={"mail_recipient_daily_cap": 1})
        sent = mail.deliver_pending(
            session, mailer, capped, datetime.now(UTC) + timedelta(minutes=1)
        )
    note_file(
        "mail-send-pass",
        "`mail.deliver_pending` with `MAIL_RECIPIENT_DAILY_CAP=1`: the first notes mail goes, the "
        "second is skipped (`recipient_cap`) and the meeting's owner is told.",
        {"sent": sent, "subjects": [m.subject for m in mailer.sent]},
    )
    note_file(
        "mail-notes-ready-text",
        "The plain-text part of the mail that went. The footer ends with the Email preferences "
        "link: the same address for everyone, with no id or token in it.",
        {"to": mailer.sent[0].to, "text": mailer.sent[0].text},
    )
    ada.call(
        "notifications-cap-notice", "GET", "/api/notifications",
        note="The skipped mail is shown: `kind` `mail.recipient_cap`, `meeting_id` set.",
    )  # fmt: skip
    ada.call("session-unread-two", "GET", "/api/session", note="`unread_notifications` is 2.")
    ada.call("notifications-read-all", "POST", "/api/notifications/read-all")


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
