"""Capture how a calendar meeting's title reaches the front end, over real HTTP, for the guide.

    python scripts/capture_calendar_titles.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

The argument is any Postgres server the script may create a scratch database on. It runs the real
migrations there, makes one calendar meeting THROUGH THE REAL JOIN RUNNER (a synthetic calendar
event with a hostile title, the real `run_due_joins` and `place_bot`; only the Vexa host is
`tests/fakes/vexa_host.py`'s fake), marks it ready with placeholder notes, starts the real app under
uvicorn and fetches what the screens fetch. Each exchange goes to
`docs/fe-captures/calendar-titles/NN-name.json`, so every claim in
`docs/fe-integration-guide-calendar-join.md` can be re-derived.

What is real: the app, the guard, CSRF, the runner, the placement, the title cleaning and every
response. What is not: Google (the event is a synthetic body, not a captured one) and Vexa.
Cookie values and CSRF tokens are masked; the e-mail address and password are placeholders.
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
OUT = ROOT / "docs" / "fe-captures" / "calendar-titles"
DB_NAME = "notetaker_capture_titles"
ADMIN_EMAIL = "admin@example.test"  # the admin account migration 0006 creates (a placeholder)
ADMIN_PASSWORD = "-".join(["capture", "admin", "placeholder"])

ADMIN_URL = sys.argv[1] if len(sys.argv) > 1 else ""
SCRATCH_URL = urlparse(ADMIN_URL)._replace(path=f"/{DB_NAME}").geturl()
os.environ["DATABASE_URL"] = SCRATCH_URL
os.environ["OWNER_EMAIL"] = ADMIN_EMAIL
os.environ["OWNER_PASSWORD"] = ADMIN_PASSWORD

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import join_runner  # noqa: E402
from app.calendar_source import SyncBatch  # noqa: E402
from app.config import Settings  # noqa: E402
from app.gcal_sync import apply_batch  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import CalendarConnection, Meeting, Template, User, UserSettings  # noqa: E402
from tests.fakes.vexa_host import FakeVexaHost  # noqa: E402

START = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
# A hostile title, as an invite could carry it: a bidi override, a line break and a header, markup,
# and a link. The placeholders in it are obviously fake.
HOSTILE_TITLE = (
    "Q3 \u202egnirts\u202c review\r\nBcc: attacker@example.test "
    "<script>alert(1)</script> https://evil.example.test/login"
)


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


def raw_event(event_id: str, summary: str, code: str) -> dict:
    return {
        "kind": "calendar#event",
        "id": event_id,
        "status": "confirmed",
        "summary": summary,
        "start": {"dateTime": START.isoformat()},
        "end": {"dateTime": (START + timedelta(minutes=30)).isoformat()},
        "organizer": {"email": "me@example.test", "self": True},
        "attendees": [{"email": "me@example.test", "self": True, "responseStatus": "accepted"}],
        "hangoutLink": f"https://meet.google.com/{code}",
        "eventType": "default",
    }


def seed(engine, settings) -> None:
    """Two calendar events for the admin account, sent to the bot by the real runner: one with a
    hostile title, one with none. Then the notes are marked ready so the exports work."""
    host = FakeVexaHost()
    with Session(engine) as session:
        user = session.scalars(select(User).where(User.email == ADMIN_EMAIL)).one()
        template = session.scalars(
            select(Template).where(Template.owner_user_id == user.id)
        ).first()
        existing = session.get(UserSettings, user.id)
        if existing is None:
            session.add(UserSettings(user_id=user.id, calendar_template_id=template.id))
        else:
            existing.calendar_template_id = template.id
        conn = CalendarConnection(user_id=user.id, provider="google")
        session.add(conn)
        session.commit()
        settings_for_user = session.get(UserSettings, user.id)
        events = [
            raw_event("placeholder-1", HOSTILE_TITLE, "aaa-bbbb-ccc"),
            raw_event("placeholder-2", "", "ddd-eeee-fff"),
        ]
        apply_batch(
            session,
            conn,
            SyncBatch(raw_events=events, next_sync_token="t", complete=True, full=True),
            settings_for_user,
            now=START - timedelta(hours=1),
            settings=settings,
        )
        wanted = Settings(_env_file=None, session_secret="x" * 40, app_env="test", bot_max_local=2)
        outcomes = join_runner.run_due_joins(
            session, host, None, now=START - timedelta(minutes=1), settings=wanted
        )
        print("runner:", [(o.result, o.reason) for o in outcomes])
        for meeting in session.scalars(select(Meeting)):
            meeting.status, meeting.step = "ready", "done"
            meeting.notes = "## Summary\nPlaceholder notes.\n\n## Decisions\nNone noted"
            meeting.transcript = "Placeholder transcript."
        session.commit()


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
        autojoin_lead_seconds=60,
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
        client.call("meetings-list", "GET", "/api/meetings", note="The list: both titles.")
        listed = client.http.get("/api/meetings").json()["meetings"]
        for meeting in listed:
            client.call(
                f"meeting-{meeting['id']}",
                "GET",
                f"/api/meetings/{meeting['id']}",
                note="The detail carries the same title.",
            )
            client.call(
                f"export-docx-{meeting['id']}",
                "GET",
                f"/api/meetings/{meeting['id']}/export?format=docx",
                note="The Word file is named after the title (cleaned); body not shown.",
            )
    finally:
        server.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    main()
