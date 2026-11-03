"""Drive the JSON API over real HTTP and save every exchange for the frontend guide.

    python scripts/capture_owner_api.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

The argument is any Postgres server the script may create a scratch database on. It runs the
real migrations there (migration 0006 creates the admin account from OWNER_EMAIL and
OWNER_PASSWORD, set below to placeholders), starts the real app under uvicorn on a free local
port with only Vexa and OpenAI replaced by fakes, and walks the owner journey with httpx. Each
exchange is written to `docs/fe-captures/owner-app/NN-name.json`, so every claim in the frontend
guide
(`docs/fe-integration-guide-owner-app.md`) can be re-derived by running this again.

What is real: the app, auth, CSRF, validation, database, migrations, serialisation and every
error. What is not: Vexa and OpenAI (fakes, below) and the worker, which never runs, so
meetings past `bot_sent` are inserted by SQL in the states the worker would leave them in.
Secrets in the files are masked: the CSRF tokens become `<csrf-A>`, `<csrf-B>` in order of first
appearance (so a token that changes still shows as different) and the cookie value `<cookie>`.
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
OUT = ROOT / "docs" / "fe-captures" / "owner-app"
DB_NAME = "notetaker_capture"
OWNER_EMAIL = "owner@example.test"  # the admin account migration 0006 creates (a placeholder)
PASSWORD = "-".join(["capture", "password", "placeholder"])
LOGIN = {"email": OWNER_EMAIL, "password": PASSWORD}
PILOT_EMAIL = "pilot@example.test"  # an ordinary pilot account (placeholders, not real people)
PILOT_PASSWORD = "-".join(["pilot", "password", "placeholder"])
MEET = "https://meet.google.com/abc-defg-hij"
ZOOM = "https://us02web.zoom.us/j/12345678901?pwd=PLACEHOLDERPWD"  # placeholder, not a real meeting


def database_urls(admin_url: str) -> tuple[str, str]:
    parsed = urlparse(admin_url)
    return admin_url, parsed._replace(path=f"/{DB_NAME}").geturl()


admin_url, scratch_url = database_urls(sys.argv[1] if len(sys.argv) > 1 else "")
os.environ["DATABASE_URL"] = scratch_url  # read by app.db when it is imported below
os.environ["OWNER_EMAIL"] = OWNER_EMAIL  # read by migration 0006
os.environ["OWNER_PASSWORD"] = PASSWORD

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import usage  # noqa: E402
from app.accounts import create_user_defaults  # noqa: E402
from app.auth import LoginLimiter  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import AppState, Meeting, UsageEntry, User  # noqa: E402
from app.passwords import hash_password  # noqa: E402
from app.pipeline import (  # noqa: E402
    REASON_DOWNLOAD,
    REASON_JOIN_REFUSED,
    REASON_NO_SPEECH,
    REASON_NOT_ADMITTED,
)
from app.services import get_openai_client, get_vexa  # noqa: E402
from app.vexa import VexaError, VexaMeeting  # noqa: E402

NOTES = "\n\n".join(
    [
        "## Summary\nAda Example and Ben Sample agreed to ship the **beta** on Friday.",
        "## Key points\n- The beta covers meetings and templates\n- Support rota starts Monday",
        "## Decisions\n- Ship the beta on Friday",
        "## Action items\n- Ada Example: write the release note by Thursday",
        "## Open questions\nNone noted",
        "## Please check\nNone noted",
    ]
)
SPEAKER_TRANSCRIPT = (
    "[Ada Example]: Hello everyone, thanks for joining. I can ship the beta on Friday.\n"
    "[Ben Sample]: Good. I will take the support rota from Monday."
)
PLAIN_TRANSCRIPT = (
    "Hello everyone, thanks for joining. I can ship the beta on Friday. "
    "Good. I will take the support rota from Monday."
)


class FakeVexa:
    fail = False
    stop_error = False

    def send_bot(self, url):
        if self.fail:
            raise VexaError("send_bot failed: internal detail that must not reach the client")
        return 4242

    def stop_bot(self, platform, native_id):
        if self.stop_error:
            raise VexaError("stop_bot failed: internal detail that must not reach the client")


class FakeOpenAI:
    notes = NOTES.replace("beta", "REWRITTEN beta", 1)

    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat))

    def _chat(self, **kwargs):
        message = SimpleNamespace(content=self.notes)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])


class Recorder:
    """A logical client: a cookie jar, its CSRF token, and a numbered file for each call."""

    count = 0
    masks: dict[str, str] = {}

    def __init__(self, base_url: str) -> None:
        self.http = httpx.Client(base_url=base_url, follow_redirects=False)
        self.csrf: str | None = None

    @classmethod
    def mask(cls, token: str) -> str:
        return cls.masks.setdefault(token, f"<csrf-{chr(ord('A') + len(cls.masks))}>")

    def call(self, name, method, path, *, body=None, csrf="session", note="", raw=None):
        """`csrf`: "session" sends the current token, None sends none, any other string is sent."""
        headers = {}
        if csrf == "session":
            token = None if method == "GET" else self.csrf  # reads need no token
        else:
            token = csrf
        if token:
            headers["X-CSRF-Token"] = token
        had_cookie = "session" in self.http.cookies
        kwargs = (
            {"content": raw, "headers": {**headers, "Content-Type": "application/json"}}
            if raw
            else {"json": body, "headers": headers}
        )
        response = self.http.request(method, path, **kwargs)
        data = (
            response.json() if "json" in response.headers.get("content-type", "") else response.text
        )
        if isinstance(data, dict) and "csrf_token" in data:
            self.csrf = data["csrf_token"]
        self._save(
            name,
            method,
            path,
            headers,
            body if raw is None else raw.decode(),
            had_cookie,
            response,
            data,
            note,
        )
        return response

    def _save(self, name, method, path, headers, body, had_cookie, response, data, note):
        Recorder.count += 1
        sent = {k: (self.mask(v) if k == "X-CSRF-Token" else v) for k, v in headers.items()}
        if had_cookie:
            sent["Cookie"] = "session=<cookie>"
        kept = {}
        for key in ("content-type", "cache-control", "location", "allow"):
            if key in response.headers:
                kept[key] = response.headers[key]
        for cookie in response.headers.get_list("set-cookie"):
            attributes = cookie.split(";", 1)[1] if ";" in cookie else ""
            kept["set-cookie"] = f"session=<cookie>;{attributes}"
        shown = json.loads(json.dumps(data))
        if isinstance(shown, dict) and "csrf_token" in shown:
            shown["csrf_token"] = self.mask(shown["csrf_token"])
        record = {
            "note": note,
            "request": {"method": method, "path": path, "headers": sent, "body": body},
            "response": {"status": response.status_code, "headers": kept, "body": shown},
        }
        file = OUT / f"{Recorder.count:02d}-{name}.json"
        file.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        print(f"{file.name:55} {method:6} {path:32} -> {response.status_code}")


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
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f"drop database if exists {DB_NAME} with (force)"))
        connection.execute(text(f"create database {DB_NAME}"))
    admin.dispose()
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")  # the real migrations and seeds


def owner_id_of(engine) -> int:
    with Session(engine) as session:
        return session.scalars(select(User.id).where(User.email == OWNER_EMAIL)).one()


def seed(engine, general: int, owner_id: int) -> dict[str, int]:
    """Meetings in the states the worker would leave them in (it is not running here)."""
    base = {
        "owner_user_id": owner_id,
        "url": MEET,
        "platform": "google_meet",
        "native_id": "abc-defg-hij",
        "template_id": general,
    }
    rows = {
        "processing": {"status": "processing", "step": "audio"},
        "processing_transcribing": {"status": "processing", "step": "transcribing"},
        "processing_speakers": {"status": "processing", "step": "speakers"},
        "processing_notes": {"status": "processing", "step": "notes"},
        "ready": {
            "status": "ready",
            "step": "done",
            "transcript": PLAIN_TRANSCRIPT,
            "speaker_transcript": SPEAKER_TRANSCRIPT,
            "notes": NOTES,
            "speakers_incomplete": True,
        },
        "ready_removed": {
            "status": "ready",
            "step": "done",
            "transcript": PLAIN_TRANSCRIPT,
            "notes": NOTES,
            "completion_reason": "evicted",
        },
        "failed_retryable": {
            "status": "failed",
            "step": "audio",
            "failure_reason": REASON_DOWNLOAD,
        },
        "failed_no_speech": {
            "status": "failed",
            "step": "transcribing",
            "failure_reason": REASON_NO_SPEECH,
        },
        "failed_audio_deleted": {
            "status": "failed",
            "step": "transcribing",
            "failure_reason": "The recording could not be transcribed. Please try again later.",
            "audio_deleted_at": datetime.now(UTC),
        },
        "failed_never_recorded": {
            "status": "failed",
            "step": "waiting",
            "failure_reason": REASON_NOT_ADMITTED,
        },
    }
    ids = {}
    with Session(engine) as session:
        for key, fields in rows.items():
            meeting = Meeting(**{**base, **fields})
            session.add(meeting)
            session.flush()
            ids[key] = meeting.id
        session.commit()
    return ids


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
    )
    vexa, openai_client = FakeVexa(), FakeOpenAI()
    app = create_app(settings)
    app.dependency_overrides[get_vexa] = lambda: vexa
    app.dependency_overrides[get_openai_client] = lambda: openai_client
    server, base = start_server(app)
    engine = create_engine(scratch_url)
    try:
        run(app, base, engine, vexa, openai_client)
    finally:
        server.should_exit = True
        engine.dispose()


def run(app, base, engine, vexa, openai_client) -> None:
    owner = Recorder(base)

    # --- session ---
    owner.call(
        "session-anonymous",
        "GET",
        "/api/session",
        note="First visit: no cookie yet. The response sets one.",
    )
    owner.call(
        "meetings-not-logged-in",
        "GET",
        "/api/meetings",
        note="Not logged in: 401 JSON, not a redirect.",
    )
    owner.call("login-without-csrf", "POST", "/api/session", body=LOGIN, csrf=None)
    owner.call(
        "login-wrong-csrf",
        "POST",
        "/api/session",
        body=LOGIN,
        csrf="not-the-token",
    )
    owner.call(
        "login-wrong-password",
        "POST",
        "/api/session",
        body={"email": OWNER_EMAIL, "password": "nope"},
    )
    owner.call("login-missing-password", "POST", "/api/session", body={})
    owner.call(
        "login-password-wrong-type",
        "POST",
        "/api/session",
        body={"email": OWNER_EMAIL, "password": 123456},
    )
    owner.call(
        "login-ok",
        "POST",
        "/api/session",
        body=LOGIN,
        note="The csrf_token here replaces the one from before login.",
    )
    owner.call("session-logged-in", "GET", "/api/session")

    # --- before any data ---
    owner.call(
        "meetings-empty",
        "GET",
        "/api/meetings",
        note="No worker heartbeat yet, so worker_stale is true.",
    )
    owner.call(
        "templates-seeded", "GET", "/api/templates", note="The four templates migration 0001 seeds."
    )
    owner.call("vocabulary-empty", "GET", "/api/vocabulary")

    # --- vocabulary ---
    owner.call("vocabulary-add", "POST", "/api/vocabulary", body={"term": "  Beyric  "})
    owner.call("vocabulary-add-second", "POST", "/api/vocabulary", body={"term": "Acme Example"})
    owner.call("vocabulary-duplicate", "POST", "/api/vocabulary", body={"term": "Beyric"})
    owner.call("vocabulary-empty-term", "POST", "/api/vocabulary", body={"term": "   "})
    owner.call("vocabulary-bad-characters", "POST", "/api/vocabulary", body={"term": "a<b"})
    owner.call("vocabulary-too-long", "POST", "/api/vocabulary", body={"term": "a" * 101})
    owner.call("vocabulary-list", "GET", "/api/vocabulary", note="Sorted alphabetically.")
    owner.call("vocabulary-delete", "DELETE", "/api/vocabulary/1", note="204: empty body.")
    owner.call(
        "vocabulary-delete-again",
        "DELETE",
        "/api/vocabulary/1",
        note="Unlike the page, an unknown id is a 404.",
    )

    # --- templates ---
    owner.call(
        "template-create",
        "POST",
        "/api/templates",
        body={"name": "Standup", "instructions": "One line per person."},
    )
    owner.call(
        "template-duplicate-name",
        "POST",
        "/api/templates",
        body={"name": "Standup", "instructions": "x"},
    )
    owner.call("template-empty", "POST", "/api/templates", body={"name": "", "instructions": "x"})
    owner.call(
        "template-name-too-long",
        "POST",
        "/api/templates",
        body={"name": "n" * 101, "instructions": "x"},
    )
    owner.call(
        "template-update",
        "PUT",
        "/api/templates/5",
        body={"name": "Standup", "instructions": "Two lines per person."},
    )
    owner.call(
        "template-update-unknown",
        "PUT",
        "/api/templates/999",
        body={"name": "a", "instructions": "b"},
    )
    owner.call("template-update-missing-field", "PUT", "/api/templates/5", body={"name": "a"})
    templates = owner.call(
        "templates-list", "GET", "/api/templates", note="Sorted by name."
    ).json()["templates"]
    general = next(t for t in templates if t["name"] == "General")

    # --- sending a bot ---
    owner.call(
        "meeting-create-bad-link",
        "POST",
        "/api/meetings",
        body={"url": "https://example.com/x", "template_id": general["id"]},
    )
    owner.call(
        "meeting-create-no-template",
        "POST",
        "/api/meetings",
        body={"url": MEET, "template_id": 9999},
    )
    owner.call(
        "meeting-create-wrong-types",
        "POST",
        "/api/meetings",
        body={"url": MEET, "template_id": str(general["id"])},
        note="template_id must be a JSON number, not a string.",
    )
    owner.call(
        "meeting-create", "POST", "/api/meetings", body={"url": MEET, "template_id": general["id"]}
    )
    owner.call(
        "meeting-create-while-active",
        "POST",
        "/api/meetings",
        body={"url": MEET, "template_id": general["id"]},
    )
    owner.call("meeting-detail-bot-sent", "GET", "/api/meetings/1")
    owner.call("meeting-cancel", "POST", "/api/meetings/1/cancel")
    owner.call("meeting-cancel-again", "POST", "/api/meetings/1/cancel")
    owner.call(
        "meeting-retry-cancelled",
        "POST",
        "/api/meetings/1/retry",
        note="A cancelled meeting never recorded anything, so it cannot be retried.",
    )
    vexa.fail = True
    owner.call(
        "meeting-create-vexa-down",
        "POST",
        "/api/meetings",
        body={"url": MEET, "template_id": general["id"]},
    )
    vexa.fail = False
    owner.call(
        "meeting-create-zoom",
        "POST",
        "/api/meetings",
        body={"url": ZOOM, "template_id": general["id"]},
        note="The Zoom link (it carries a password) is not in the response.",
    )
    with Session(engine) as session:  # what the worker does after Zoom turns a bot away
        session.get(Meeting, 2).join_attempts = 2
        session.commit()
    owner.call(
        "meeting-detail-zoom-second-attempt",
        "GET",
        "/api/meetings/2",
        note="join_attempts was set to 2 by SQL (the worker does this).",
    )
    vexa.stop_error = True
    owner.call("meeting-cancel-vexa-down", "POST", "/api/meetings/2/cancel")
    vexa.stop_error = False

    # --- states the worker leaves behind ---
    with Session(engine) as session:
        session.get(AppState, 1).worker_heartbeat_at = datetime.now(UTC) - timedelta(minutes=2)
        session.commit()
    with Session(engine) as session:
        admin_id = session.scalars(select(User.id).where(User.email == OWNER_EMAIL)).one()
    ids = seed(engine, general["id"], admin_id)
    for key in (
        "processing",
        "processing_transcribing",
        "processing_speakers",
        "processing_notes",
        "ready",
        "ready_removed",
        "failed_retryable",
        "failed_no_speech",
        "failed_audio_deleted",
        "failed_never_recorded",
    ):
        owner.call(f"meeting-detail-{key.replace('_', '-')}", "GET", f"/api/meetings/{ids[key]}")
    owner.call(
        "meetings-list",
        "GET",
        "/api/meetings",
        note="A heartbeat now exists, so worker_stale is false.",
    )
    owner.call("meetings-list-page-1", "GET", "/api/meetings?limit=3")
    owner.call(
        "meetings-list-page-2", "GET", f"/api/meetings?limit=3&before_id={ids['ready_removed']}"
    )
    owner.call("meetings-list-bad-limit", "GET", "/api/meetings?limit=0")

    # --- acting on them ---
    owner.call(
        "meeting-retry-blocked-no-speech", "POST", f"/api/meetings/{ids['failed_no_speech']}/retry"
    )
    owner.call("meeting-retry", "POST", f"/api/meetings/{ids['failed_retryable']}/retry")
    owner.call(
        "meeting-regenerate",
        "POST",
        f"/api/meetings/{ids['ready']}/regenerate",
        body={"template_id": templates[0]["id"]},
    )
    owner.call(
        "meeting-regenerate-not-ready",
        "POST",
        f"/api/meetings/{ids['processing']}/regenerate",
        body={"template_id": general["id"]},
    )
    owner.call(
        "meeting-regenerate-no-template",
        "POST",
        f"/api/meetings/{ids['ready']}/regenerate",
        body={"template_id": 9999},
    )
    openai_client.notes = ""
    owner.call(
        "meeting-regenerate-openai-down",
        "POST",
        f"/api/meetings/{ids['ready']}/regenerate",
        body={"template_id": general["id"]},
    )
    owner.call("meeting-unknown", "GET", "/api/meetings/999")
    owner.call("meeting-id-too-long", "GET", "/api/meetings/99999999999")
    owner.call("meeting-not-a-number", "GET", "/api/meetings/abc")

    # --- protocol edges ---
    owner.call(
        "write-without-csrf", "POST", f"/api/meetings/{ids['failed_retryable']}/retry", csrf=None
    )
    owner.call("write-wrong-csrf", "DELETE", "/api/vocabulary/2", csrf="not-the-token")
    owner.call("method-not-allowed", "PATCH", "/api/templates")
    owner.call("unknown-api-path", "GET", "/api/nope")
    owner.call("malformed-json", "POST", "/api/vocabulary", raw=b"{not json")
    owner.call("not-an-object", "POST", "/api/vocabulary", body=["Beyric"])

    # --- logout, revocation, rate limit ---
    other = Recorder(base)
    other.http.cookies.update(owner.http.cookies)
    other.csrf = owner.csrf
    owner.call(
        "logout",
        "DELETE",
        "/api/session",
        note="Logs out everywhere; the response carries a new anonymous token.",
    )
    other.call(
        "old-cookie-after-logout",
        "GET",
        "/api/templates",
        note="The same cookie, replayed after logout: 401.",
    )
    app.state.limiter = LoginLimiter()  # the failures captured above are not part of this count
    guest = Recorder(base)
    guest.call("limit-session", "GET", "/api/session")
    for attempt in range(1, 6):
        guest.call(
            f"limit-wrong-password-{attempt}",
            "POST",
            "/api/session",
            body={"email": OWNER_EMAIL, "password": "nope"},
        )
    guest.call(
        "limit-sixth-attempt",
        "POST",
        "/api/session",
        body={"email": OWNER_EMAIL, "password": "nope"},
    )
    guest.call(
        "limit-right-password-while-locked",
        "POST",
        "/api/session",
        body=LOGIN,
        note="While locked out even the right password gets 429.",
    )
    run_accounts(app, base, engine, ids, general["id"])
    run_refusal(owner, engine, general["id"])
    run_live_state(owner, engine, general["id"])
    run_end_controls(owner, engine, general["id"], vexa)


def run_accounts(app, base, engine, ids, admin_general_id) -> None:
    """A second, ordinary account: what login can end in, the hours cap, and that one user's ids
    mean nothing to another. Numbered after everything above so the earlier files keep their
    numbers."""
    app.state.limiter = LoginLimiter()  # the limit captures above used up this address
    now = datetime.now(UTC)
    with Session(engine) as session:
        pilot = User(
            email=PILOT_EMAIL,
            name="Pilot Example",
            password_hash=hash_password(PILOT_PASSWORD),
            email_verified_at=now,
        )
        session.add_all(
            [
                pilot,
                User(
                    email="unverified@example.test",
                    password_hash=hash_password(PILOT_PASSWORD),
                ),
                User(
                    email="suspended@example.test",
                    password_hash=hash_password(PILOT_PASSWORD),
                    email_verified_at=now,
                    status="suspended",
                ),
            ]
        )
        session.commit()
        create_user_defaults(session, pilot)
        pilot_id = pilot.id

    def body(email):
        return {"email": email, "password": PILOT_PASSWORD}

    guest = Recorder(base)
    guest.call("accounts-session-anonymous", "GET", "/api/session")
    guest.call("login-missing-email", "POST", "/api/session", body={"password": "x"})
    guest.call(
        "login-unknown-email",
        "POST",
        "/api/session",
        body=body("nobody@example.test"),
        note="Same status, code and message as a wrong password: no account is revealed.",
    )
    guest.call(
        "login-unverified",
        "POST",
        "/api/session",
        body=body("unverified@example.test"),
        note=(
            "Right password, but the email was never confirmed: the same 401 as a wrong "
            "password (the answer must not reveal the account), no session."
        ),
    )
    guest.call(
        "login-suspended",
        "POST",
        "/api/session",
        body=body("suspended@example.test"),
        note="Right password, suspended account: no session.",
    )

    pilot_client = Recorder(base)
    pilot_client.call("accounts-session-anonymous-2", "GET", "/api/session")
    pilot_client.call(
        "login-ok-pilot-user",
        "POST",
        "/api/session",
        body=body(PILOT_EMAIL),
        note="An ordinary account: a capped `usage`, not an admin.",
    )
    pilot_templates = pilot_client.call(
        "templates-pilot-user",
        "GET",
        "/api/templates",
        note="Their own four default templates, not the admin's.",
    ).json()["templates"]
    pilot_general = next(t for t in pilot_templates if t["name"] == "General")
    pilot_client.call("meetings-pilot-user-empty", "GET", "/api/meetings")
    other = ids["ready"]
    pilot_client.call(
        "meeting-of-another-user",
        "GET",
        f"/api/meetings/{other}",
        note="A meeting that exists but is someone else's: 404, as if it did not exist.",
    )
    pilot_client.call("meeting-cancel-of-another-user", "POST", f"/api/meetings/{other}/cancel")
    pilot_client.call(
        "template-of-another-user",
        "PUT",
        f"/api/templates/{admin_general_id}",
        body={"name": "Mine now", "instructions": "x"},
    )
    pilot_client.call("vocabulary-of-another-user", "DELETE", "/api/vocabulary/2")
    pilot_client.call(
        "meeting-create-with-another-users-template",
        "POST",
        "/api/meetings",
        body={"url": MEET, "template_id": admin_general_id},
        note="A template id that is someone else's looks like a missing template.",
    )
    with Session(engine) as session:
        session.add(
            UsageEntry(
                user_id=pilot_id,
                email_hash=usage.email_hash(PILOT_EMAIL),
                seconds=5 * 3600,
                counted_at=now,
            )
        )
        session.commit()
    pilot_client.call(
        "session-pilot-user-hours-used",
        "GET",
        "/api/session",
        note="Five recorded hours are in the ledger (inserted by SQL: the worker is not running).",
    )
    pilot_client.call(
        "meeting-create-hours-cap-reached",
        "POST",
        "/api/meetings",
        body={"url": MEET, "template_id": pilot_general["id"]},
        note="Checked before the bot is sent. The hours in the text follow the user's own cap.",
    )


def run_refusal(owner, engine, general_id) -> None:
    """A join Google refused: the wait and the send-again are paced. Numbered after the rest."""
    owner.call("session-login-after-logout", "POST", "/api/session", body=LOGIN)
    with Session(engine) as session:
        # Finish every other meeting by SQL, so no bot is active and only the pacing decides.
        session.execute(
            text(
                "update meetings set status = 'ready', step = 'done' where status in "
                "('bot_sent', 'processing')"
            )
        )
        refused = Meeting(
            owner_user_id=owner_id_of(engine),
            url=MEET,
            platform="google_meet",
            native_id="abc-defg-hij",
            template_id=general_id,
            status="failed",
            step="waiting",
            failure_reason=REASON_JOIN_REFUSED,
            completion_reason="google_meet_join_refused",
            terminal_seen_at=datetime.now(UTC) - timedelta(minutes=1),
            join_attempts=2,
            vexa_meeting_id="4241",
        )
        session.add(refused)
        session.commit()
        refused_id = refused.id
    owner.call(
        "meeting-detail-refused-waiting",
        "GET",
        f"/api/meetings/{refused_id}",
        note="Google refused the bot a minute ago. Same fields as any block: `retry_block` is "
        "`no_audio` (its existing value), only `retry_block_message` says how long is left.",
    )
    owner.call(
        "meeting-retry-refused-too-early",
        "POST",
        f"/api/meetings/{refused_id}/retry",
        note="A send-again inside the 5 minutes is the ordinary 409 `not_retryable`, with the "
        "wait in the message.",
    )
    with Session(engine) as session:
        session.execute(
            text("update meetings set terminal_seen_at = :t where id = :id"),
            {"t": datetime.now(UTC) - timedelta(minutes=6), "id": refused_id},
        )
        session.commit()
    owner.call(
        "meeting-detail-refused-can-send-again",
        "GET",
        f"/api/meetings/{refused_id}",
        note="Six minutes after the refusal: no block, `can_retry` is true.",
    )
    owner.call(
        "meeting-retry-refused-sends-a-new-bot",
        "POST",
        f"/api/meetings/{refused_id}/retry",
        note="Sends a NEW bot (fake Vexa here) and puts the meeting back to `bot_sent`.",
    )
    with Session(engine) as session:
        # The new bot is finished by SQL so only the paused meeting below holds a bot.
        session.execute(
            text("update meetings set status = 'ready', step = 'done' where id = :id"),
            {"id": refused_id},
        )
        paused = Meeting(
            owner_user_id=owner_id_of(engine),
            url=MEET,
            platform="google_meet",
            native_id="abc-defg-hij",
            template_id=general_id,
            status="bot_sent",
            step="waiting",
            completion_reason="google_meet_join_refused",
            terminal_seen_at=datetime.now(UTC) - timedelta(minutes=1),
            join_attempts=1,
            vexa_meeting_id="4243",
        )
        session.add(paused)
        session.commit()
        paused_id = paused.id
    owner.call(
        "meeting-detail-refused-paused-before-retry",
        "GET",
        f"/api/meetings/{paused_id}",
        note="Google refused the first bot a minute ago; the worker has stopped it and will send "
        "the second at `join_retry_at` (4 to 5 minutes after the refusal). Still `bot_sent`.",
    )


class BotAs:
    """A Vexa that answers every lookup with one state (or fails), for the real worker below."""

    def __init__(self, answer) -> None:
        self.answer = answer

    def get_meeting_by_id(self, vexa_meeting_id):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def run_live_state(owner, engine, general_id) -> None:
    """What the owner sees while the call runs. The worker is not running in this script, so each
    state is the real `pipeline.advance_meeting` fed one Vexa answer, then read over HTTP."""
    from app import pipeline

    with Session(engine) as session:
        session.execute(
            text(
                "update meetings set status = 'ready', step = 'done' where status in "
                "('bot_sent', 'processing')"
            )
        )
        meeting = Meeting(
            owner_user_id=owner_id_of(engine),
            url=MEET,
            platform="google_meet",
            native_id="abc-defg-hij",
            template_id=general_id,
            vexa_meeting_id="4250",
        )
        session.add(meeting)
        session.commit()
        meeting_id = meeting.id
    segment = {"speaker": "Ada Example", "text": "Hello everyone, thanks for joining."}
    steps = [
        (
            "bot-state-unknown",
            VexaError("down"),
            'Vexa could not be reached, so nothing is known: `status_text` is the old "Bot sent".',
        ),
        (
            "bot-joining",
            VexaMeeting(status="joining"),
            "Vexa reports `joining`. Same fields as before: only `status_text` changed.",
        ),
        (
            "bot-awaiting-admission",
            VexaMeeting(status="awaiting_admission"),
            "Vexa reports `awaiting_admission`: the bot is in the waiting room.",
        ),
        (
            "bot-in-meeting",
            VexaMeeting(status="active"),
            "Vexa reports `active` with no segments and no recording yet: the bot is in, and the "
            "text does not say it is recording.",
        ),
        (
            "bot-recording",
            VexaMeeting(status="active", segments=[segment]),
            "`active` with transcript segments: now it says `Recording`.",
        ),
        (
            "bot-needs-help",
            VexaMeeting(status="needs_help"),
            "Vexa reports `needs_help` (a consent or verification prompt). Still `bot_sent`: the "
            "worker only fails the join after 2 minutes of it.",
        ),
    ]
    for name, answer, note in steps:
        with Session(engine) as session:
            pipeline.advance_meeting(session, meeting_id, vexa=BotAs(answer), now=datetime.now(UTC))
        owner.call(f"meeting-detail-{name}", "GET", f"/api/meetings/{meeting_id}", note=note)
    owner.call(
        "meetings-list-bot-needs-help",
        "GET",
        "/api/meetings?limit=2",
        note="The list rows carry the same `status_text`.",
    )


def run_end_controls(owner, engine, general_id, vexa) -> None:
    """ "End now and write the notes", the words for the in-between states, a state the worker has
    not renewed, and a quiet worker. As above, each state is the real `pipeline.advance_meeting`
    fed one Vexa answer; the endpoint and the reads are real HTTP."""
    from app import pipeline

    def new_meeting(vexa_id: str) -> int:
        with Session(engine) as session:
            session.execute(
                text(
                    "update meetings set status = 'ready', step = 'done' where status in "
                    "('bot_sent', 'processing')"
                )
            )
            meeting = Meeting(
                owner_user_id=owner_id_of(engine),
                url=MEET,
                platform="google_meet",
                native_id="abc-defg-hij",
                template_id=general_id,
                vexa_meeting_id=vexa_id,
            )
            session.add(meeting)
            session.commit()
            return meeting.id

    def poll(meeting_id: int, answer, when: datetime | None = None) -> None:
        with Session(engine) as session:
            pipeline.advance_meeting(
                session, meeting_id, vexa=BotAs(answer), now=when or datetime.now(UTC)
            )

    def heartbeat(minutes_ago: int) -> None:
        with Session(engine) as session:
            state = session.get(AppState, 1)
            state.worker_heartbeat_at = datetime.now(UTC) - timedelta(minutes=minutes_ago)
            session.commit()

    segment = {"speaker": "Ada Example", "text": "Hello everyone, thanks for joining."}
    left = {"completion_reason": "stopped"}
    heartbeat(0)  # a worker that is alive; the captures above were made with none

    mid = new_meeting("4260")
    poll(mid, VexaMeeting(status="requested"))
    owner.call(
        "meeting-detail-bot-requested",
        "GET",
        f"/api/meetings/{mid}",
        note="Vexa reports `requested`: the bot has been asked for and is not in the call yet. "
        "`can_end` is false: nothing to end yet (Cancel is still offered).",
    )
    poll(mid, VexaMeeting(status="active", segments=[segment]))
    owner.call(
        "meeting-detail-bot-recording-can-end",
        "GET",
        f"/api/meetings/{mid}",
        note='The bot is recording. `can_end` is true: the page may offer "End now and write '
        'the notes". `worker_stale` is false: the worker checked in a moment ago.',
    )
    vexa.stop_error = True
    owner.call(
        "meeting-end-vexa-down",
        "POST",
        f"/api/meetings/{mid}/end",
        note="Vexa could not stop the bot. 502, nothing changed, and the owner may try again. "
        "The message never carries Vexa's own text.",
    )
    vexa.stop_error = False
    owner.call(
        "meeting-end",
        "POST",
        f"/api/meetings/{mid}/end",
        note="The bot is told to leave. The response is the full meeting (like Cancel): still "
        "`bot_sent`, `can_end` false, `status_text` says the bot is leaving. The worker carries "
        "on from here; nothing is discarded.",
    )
    owner.call(
        "meeting-end-again",
        "POST",
        f"/api/meetings/{mid}/end",
        note="A second press (a double click) is refused and stops nothing a second time.",
    )
    poll(mid, VexaMeeting(status="stopping"))
    owner.call(
        "meeting-detail-bot-leaving",
        "GET",
        f"/api/meetings/{mid}",
        note="Vexa reports `stopping`. The words stay the same until the bot has gone.",
    )
    poll(
        mid,
        VexaMeeting(
            status="completed",
            segments=[segment],
            recording_id=7,
            media_id=9,
            recording_status="in_progress",
            **left,
        ),
    )
    owner.call(
        "meeting-detail-call-ended-finishing-recording",
        "GET",
        f"/api/meetings/{mid}",
        note="The bot has gone (Vexa: `completed`, reason `stopped`) and its recording is still "
        "being finished. After this the worker downloads it and the meeting goes through "
        "`processing` to `ready` like any other.",
    )

    joining = new_meeting("4261")
    poll(joining, VexaMeeting(status="joining"))
    owner.call(
        "meeting-end-not-endable",
        "POST",
        f"/api/meetings/{joining}/end",
        note="The bot is still joining, so there is nothing to end: 409 `not_endable` (Cancel "
        "is the way out). The same 409 comes for a meeting that is processing, ready or failed.",
    )

    empty = new_meeting("4262")
    poll(empty, VexaMeeting(status="active"))
    owner.call("meeting-end-before-anything-recorded", "POST", f"/api/meetings/{empty}/end")
    for _ in range(pipeline.NO_AUDIO_GRACE_POLLS):  # the few polls a recording gets to appear
        poll(empty, VexaMeeting(status="completed", **left))
    owner.call(
        "meeting-detail-ended-nothing-recorded",
        "GET",
        f"/api/meetings/{empty}",
        note="Ended before anything was recorded: the usual failed meeting (`retry_block` "
        "`no_audio`, nothing to retry), with its own reason.",
    )

    kept = new_meeting("4264")
    poll(kept, VexaMeeting(status="active", segments=[segment]))
    owner.call("meeting-end-then-cancel-setup", "POST", f"/api/meetings/{kept}/end")
    owner.call(
        "meeting-cancel-after-end",
        "POST",
        f"/api/meetings/{kept}/cancel",
        note="Cancel after End is refused (409): it would throw away the recording the owner asked "
        "to keep. `can_cancel` is already false on the meeting returned by End.",
    )

    stale = new_meeting("4263")
    poll(stale, VexaMeeting(status="active"), when=datetime.now(UTC) - timedelta(minutes=5))
    owner.call(
        "meeting-detail-bot-state-stale",
        "GET",
        f"/api/meetings/{stale}",
        note="The worker last saw the bot 5 minutes ago, which is over 2 poll intervals, so the "
        "page does not claim to know: `Checking on the bot...`. `can_end` is still true.",
    )
    heartbeat(30)
    owner.call(
        "meeting-detail-worker-stale",
        "GET",
        f"/api/meetings/{stale}",
        note="The worker has not checked in for 30 minutes: `worker_stale` is true on the "
        "meeting (it is false once the meeting is ready or failed).",
    )


if __name__ == "__main__":
    main()
