"""Drive the sign-up and login limits over real HTTP and save every exchange for
`docs/fe-integration-guide-pilot-accounts.md` (section 14b).

    python scripts/capture_flood_defence_api.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres

The argument is any Postgres server the script may create a scratch database on. It runs the real
migrations there, starts the real app under uvicorn, opens the pilot as the placeholder admin, and
signs people up. Each exchange is written to
`docs/fe-captures/prelaunch-flood-defence/NN-name.json`.

What is real: the app, auth, CSRF, validation, the database, migrations, serialisation, every
error, the per-address cap on unconfirmed accounts and the login lockout kept in the database,
and **a restart**: the app is stopped and a second one started on the same database in the middle
of a lockout. What is not: the mail provider (a key that is never used, nothing is sent) and the
client addresses (every call comes from this machine, so they share one address; the cap per
address and the hourly counters are therefore shown for one address, and the many-address attack is
only in `tests/test_signup_flood.py`). Everyone in the files is a placeholder. Secrets are masked:
CSRF tokens become `<csrf-A>`, cookies `<cookie>`.
"""

import json
import os
import shutil
import socket
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "fe-captures" / "prelaunch-flood-defence"
DB_NAME = "notetaker_capture_flood_defence"
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
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import AppState  # noqa: E402


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


def form(email: str, n: int) -> dict:
    password = "-".join(["capture", "password", str(n), "ok"])
    return {"name": "Ada Example", "email": email, "password": password, "accept_terms": True}


def run(base: str, engine, settings: Settings) -> None:
    with Session(engine) as session:
        state = session.get(AppState, 1)  # made by the migrations
        state.signups_open, state.max_accounts, state.password_signup_enabled = True, None, True
        session.commit()
    anon = Recorder(base, "visitor")
    anon.call("session-anonymous", "GET", "/api/session")

    # --- sign-up: one address may hold only a few unconfirmed accounts at once ---
    for n in range(3):
        anon.call(
            f"signup-{n + 1}", "POST", "/api/signup",
            body=form(f"ada{n}@example.test", n),
            note="One of the three sign-ups this address may make in 48 hours: 202.",
        )  # fmt: skip
    anon.call(
        "signup-fourth-from-the-same-address", "POST", "/api/signup",
        body=form("ada3@example.test", 3),
        note="A fourth from the same address: 429 `too_many_requests`, nothing created, no mail.",
    )  # fmt: skip
    anon.call(
        "signup-existing-email-at-the-cap", "POST", "/api/signup",
        body=form("ada0@example.test", 0),
        note="An email this address already holds gets the SAME answer: the cap says nothing "
        "about who has an account.",
    )  # fmt: skip

    # --- login: a lockout survives a restart ---
    for n in range(5):
        anon.call(
            f"login-wrong-{n + 1}", "POST", "/api/session",
            body={"email": ADMIN_EMAIL, "password": "wrong-guess-" + str(n)},
            note="A wrong password: 401, counted in the database.",
        )  # fmt: skip
    anon.call(
        "login-locked", "POST", "/api/session",
        body={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        note="The sixth attempt from this address is refused: 429 `too_many_attempts`, even with "
        "the right password.",
    )  # fmt: skip


def after_restart(base: str) -> None:
    again = Recorder(base, "visitor-after-restart")
    again.call("session-after-restart", "GET", "/api/session")
    again.call(
        "login-locked-after-restart", "POST", "/api/session",
        body={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        note="A NEW process on the same database: the lockout is still on (429 "
        "`too_many_attempts`), with the right password.",
    )  # fmt: skip
    again.call(
        "signup-fourth-after-restart", "POST", "/api/signup",
        body=form("ada4@example.test", 4),
        note=(
            "429 `too_many_requests` again: by now this address has also used its 5 sign-ups "
            "for the hour, and that count survived the restart too (it is in the database)."
        ),
    )  # fmt: skip


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
        resend_api_key="re_capture_never_used",  # makes password sign-up possible; nothing is sent
    )
    engine = create_engine(scratch_url)
    server, base = start_server(create_app(settings))
    try:
        run(base, engine, settings)
    finally:
        server.should_exit = True
    time.sleep(0.5)
    second, base2 = start_server(create_app(settings))  # a restart: a new app, the same database
    try:
        after_restart(base2)
    finally:
        second.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    main()
