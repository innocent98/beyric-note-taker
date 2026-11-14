"""Drive the recording-consent box over real HTTP and save every exchange for the frontend guide.

    python scripts/capture_recording_ack.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

The argument is any Postgres server the script may create a scratch database on. It runs the real
migrations there, starts the real app under uvicorn on a free local port, and walks the three
places the box matters with httpx: password sign-up, Google sign-up (Google's own servers stood in,
see `StandInGoogle`), and an account older than the box (made directly in the database with no
acknowledgement, the way every pilot account is today) meeting the one-time card's route. Each
exchange is written to `docs/fe-captures/recording-ack/NN-name.json`, so every claim in
`docs/fe-integration-guide-recording-ack.md` can be re-derived by running this again.

What is real: the app, auth, CSRF, validation, rate limits, database, migrations, serialisation and
every error. What is not: Resend (nothing is sent), Google's servers, the bot provider (a fake that
takes the meeting link) and the worker. Secrets are masked as in `capture_pilot_api.py`.
"""

import json
import os
import re
import shutil
import socket
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "fe-captures" / "recording-ack"
DB_NAME = "notetaker_capture_recording_ack"
ADMIN_EMAIL = "admin@example.test"  # created by migration 0006 (a placeholder)
ADMIN_PASSWORD = "-".join(["capture", "admin", "placeholder"])
ADA = "ada@example.test"  # placeholders, not real people
ADA_PASSWORD = "-".join(["ada", "chosen", "password", "1"])
OLD = "olu@example.test"  # an account from before the box
OLD_PASSWORD = "-".join(["olu", "chosen", "password", "2"])
GOOGLE_NEW = "grace@example.test"
MEET = "https://meet.google.com/abc-defg-hij"


def scratch_urls(admin_url: str) -> tuple[str, str]:
    parsed = urlparse(admin_url)
    return admin_url, parsed._replace(path=f"/{DB_NAME}").geturl()


admin_url, scratch_url = scratch_urls(sys.argv[1] if len(sys.argv) > 1 else "")
os.environ["DATABASE_URL"] = scratch_url  # read by app.db when it is imported below
os.environ["OWNER_EMAIL"] = ADMIN_EMAIL  # read by migration 0006
os.environ["OWNER_PASSWORD"] = ADMIN_PASSWORD

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import accounts, google_oauth, passwords  # noqa: E402
from app.config import Settings  # noqa: E402
from app.limits import Limit  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import AppState, MailOutbox, User  # noqa: E402
from app.services import get_vexa  # noqa: E402


class StandInGoogle(google_oauth.GoogleOAuth):
    """The real client, so the consent URL is the one the app really builds, except that the code
    exchange (a call to Google's servers) answers with `identity` instead of going out."""

    identity = google_oauth.Identity("g-capture-1", GOOGLE_NEW, "Grace Example")

    def exchange(self, *, code, code_verifier, nonce, now):
        return self.identity


class FakeVexa:
    def send_bot(self, url):
        return 4242

    def stop_bot(self, platform, native_id):
        pass


class Recorder:
    """A logical client: a cookie jar, its CSRF token, and a numbered file for each call."""

    count = 0
    masks: dict[str, str] = {}
    tokens: dict[str, str] = {}

    def __init__(self, base_url: str, who: str = "visitor") -> None:
        self.http = httpx.Client(base_url=base_url, follow_redirects=False)
        self.csrf: str | None = None
        self.who = who

    @classmethod
    def mask(cls, token: str) -> str:
        return cls.masks.setdefault(token, f"<csrf-{chr(ord('A') + len(cls.masks))}>")

    @classmethod
    def mask_mail_token(cls, text_: str) -> str:
        def swap(match: re.Match) -> str:
            raw = match.group(2)
            return match.group(1) + cls.tokens.setdefault(raw, f"<token-{len(cls.tokens) + 1}>")

        text_ = re.sub(r"((?:token|invite)=)([\w-]{20,})", swap, text_)
        return re.sub(r"\b(state|nonce|code_challenge)=[\w-]{20,}", r"\1=<\1>", text_)

    def call(self, name, method, path, *, body=None, csrf="session", note=""):
        """`csrf`: "session" sends the current token, None sends none, any other string is sent."""
        headers = {}
        token = (None if method == "GET" else self.csrf) if csrf == "session" else csrf
        if token:
            headers["X-CSRF-Token"] = token
        had_cookie = "session" in self.http.cookies
        response = self.http.request(method, path, json=body, headers=headers)
        content_type = response.headers.get("content-type", "")
        data = response.json() if "json" in content_type else response.text
        if isinstance(data, dict) and "csrf_token" in data:
            self.csrf = data["csrf_token"]
        self._save(name, method, path, headers, body, had_cookie, response, data, note)
        return response

    def _save(self, name, method, path, headers, body, had_cookie, response, data, note):
        Recorder.count += 1
        sent = {k: (self.mask(v) if k == "X-CSRF-Token" else v) for k, v in headers.items()}
        if had_cookie:
            sent["Cookie"] = "session=<cookie>"
        kept = {}
        for key in ("content-type", "cache-control", "location", "allow"):
            if key in response.headers:
                kept[key] = self.mask_mail_token(response.headers[key])
        cookies = []
        for cookie in response.headers.get_list("set-cookie"):
            first, _, attributes = cookie.partition(";")
            cookies.append(f"{first.split('=', 1)[0]}=<cookie>;{attributes}")
        if cookies:
            kept["set-cookie"] = cookies if len(cookies) > 1 else cookies[0]
        shown = json.loads(json.dumps(data))
        if isinstance(shown, dict) and "csrf_token" in shown:
            shown["csrf_token"] = self.mask(shown["csrf_token"])
        record = {
            "note": note,
            "client": self.who,
            "request": {"method": method, "path": path, "headers": sent, "body": self._body(body)},
            "response": {"status": response.status_code, "headers": kept, "body": shown},
        }
        text_ = self.mask_mail_token(json.dumps(record, indent=2, ensure_ascii=False)) + "\n"
        (OUT / f"{Recorder.count:02d}-{name}.json").write_text(text_)
        print(f"{Recorder.count:02d}-{name:50} {method:6} {path:34} -> {response.status_code}")

    @classmethod
    def _body(cls, body):
        if not isinstance(body, dict):
            return body
        shown = {}
        for key, value in body.items():
            if key == "password":
                value = "<password>"
            elif key in ("token", "invite_token") and isinstance(value, str) and len(value) >= 20:
                value = cls.tokens.setdefault(value, f"<token-{len(cls.tokens) + 1}>")
            shown[key] = value
        return shown


def note_file(name: str, note: str, content: dict) -> None:
    """Save something that is not an HTTP exchange (a mail the worker would send)."""
    Recorder.count += 1
    record = {"note": note, "content": content}
    text_ = Recorder.mask_mail_token(json.dumps(record, indent=2, ensure_ascii=False)) + "\n"
    (OUT / f"{Recorder.count:02d}-{name}.json").write_text(text_)
    print(f"{Recorder.count:02d}-{name}")


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
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")


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
        google_client_id="capture-client-id.apps.example.test",
        google_client_secret="capture-client-secret",
    )
    app = create_app(settings)
    app.dependency_overrides[get_vexa] = lambda: FakeVexa()
    app.dependency_overrides[google_oauth.get_google] = lambda: StandInGoogle(settings)
    limits = app.state.public_limits
    limits.signup_address = Limit(limits.store, "signup_address", 10_000, 3600)  # one address calls
    server, base = start_server(app)
    engine = create_engine(scratch_url)
    try:
        run(base, engine)
    finally:
        server.should_exit = True
        engine.dispose()


def outbox(engine, template: str, to: str) -> dict:
    with Session(engine) as session:
        row = session.scalars(
            select(MailOutbox)
            .where(MailOutbox.template == template, MailOutbox.to_email == to)
            .order_by(MailOutbox.id.desc())
        ).first()
        return {"to_email": row.to_email, "template": row.template, "payload": dict(row.payload)}


def token_of(mail: dict) -> str:
    return re.search(r"(?:token|invite)=([\w-]+)", mail["payload"]["link"])[1]


def acks(engine, email: str) -> dict:
    """What the account row holds (read straight from the database)."""
    with Session(engine) as session:
        user = session.scalars(select(User).where(User.email == email)).first()
        if user is None:
            return {"account": None}
        return {
            "recording_ack_at": "<a time>" if user.recording_ack_at else None,
            "recording_ack_version": user.recording_ack_version,
        }


def login(client: Recorder, name: str, email: str, password: str, note="") -> httpx.Response:
    return client.call(
        name, "POST", "/api/session", body={"email": email, "password": password}, note=note
    )


def callback(client: Recorder, name: str, query: str, note: str, **kw) -> httpx.Response:
    start = client.http.get(f"/api/auth/google/start{query}")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    return client.call(
        name, "GET", f"/api/auth/google/callback?code=abc&state={state}", note=note, **kw
    )


def run(base, engine) -> None:
    with Session(engine) as session:  # open the pilot (the admin screen does this in production)
        state = session.get(AppState, 1) or AppState(id=1)
        state.signups_open, state.max_accounts, state.password_signup_enabled = True, 10, True
        session.add(state)
        session.commit()

    visitor = Recorder(base, "visitor")
    visitor.call("session-anonymous", "GET", "/api/session", note="A visitor: `user` is null.")

    # --- password sign-up ---
    form = {"name": "Ada Example", "email": ADA, "password": ADA_PASSWORD, "accept_terms": True}
    visitor.call(
        "signup-box-not-ticked",
        "POST",
        "/api/signup",
        body={**form, "accept_recording": False},
        note="The box is not ticked: 422, and no account is made.",
    )
    visitor.call(
        "signup-box-field-missing",
        "POST",
        "/api/signup",
        body=form,
        note="`accept_recording` left out of the body: the same plain 422, not `validation_error`.",
    )
    visitor.call(
        "signup-terms-and-box-both-missing",
        "POST",
        "/api/signup",
        body={**form, "accept_terms": False, "accept_recording": False},
        note="The terms are asked for first.",
    )
    print("  database after the refusals:", acks(engine, ADA))
    visitor.call(
        "signup-ok",
        "POST",
        "/api/signup",
        body={**form, "accept_recording": True},
        note="Both boxes ticked: 202. The account row now holds the time and the version.",
    )
    note_file(
        "account-row-after-signup",
        "Read from the `users` row straight after the sign-up (the time is masked).",
        acks(engine, ADA),
    )
    mail = outbox(engine, "verify_email", ADA)
    visitor.call(
        "verify-email",
        "POST",
        "/api/verify-email",
        body={"token": token_of(mail), "password": ADA_PASSWORD},
    )
    ada = Recorder(base, "ada")
    ada.call("session-before-login", "GET", "/api/session")
    login(
        ada,
        "login-ada",
        ADA,
        ADA_PASSWORD,
        note="A sign-up that ticked the box: `recording_acknowledged` is true.",
    )

    # --- Google sign-up ---
    google = Recorder(base, "google-visitor")
    google.call("google-session", "GET", "/api/session")
    callback(
        google,
        "google-callback-box-not-ticked",
        "?accept_terms=true",
        "A NEW Google user, terms accepted but no `accept_recording` at the start: a redirect to "
        "/login with the error code, and no account.",
    )
    print("  database after the refusal:", acks(engine, GOOGLE_NEW))
    callback(
        google,
        "google-callback-terms-and-box-missing",
        "",
        "Neither box: the terms code comes first.",
    )
    callback(
        google,
        "google-callback-ok",
        "?accept_terms=true&accept_recording=true&return_to=/",
        "Both boxes sent at the start: a new account and a session; redirect to `return_to`.",
    )
    note_file(
        "account-row-after-google",
        "Read from the `users` row straight after the Google sign-up.",
        acks(engine, GOOGLE_NEW),
    )
    google.call(
        "google-session-after", "GET", "/api/session", note="`recording_acknowledged` is true."
    )
    google2 = Recorder(base, "google-returning")
    google2.call("google-returning-session", "GET", "/api/session")
    callback(
        google2,
        "google-callback-existing-account",
        "",
        "The same Google account again, no box sent: an existing account signs in without either box.",
    )

    # --- an account from before the box ---
    with Session(engine) as session:
        old = User(
            email=OLD,
            name="Olu Example",
            password_hash=passwords.hash_password(OLD_PASSWORD),
            email_verified_at=session.scalar(text("select now()")),
            terms_accepted_at=session.scalar(text("select now()")),
            terms_version="2026-11-04",
        )
        session.add(old)
        session.flush()
        accounts.add_user_defaults(session, old)
        session.commit()
    note_file(
        "account-row-before-box", "The older account as the database holds it.", acks(engine, OLD)
    )
    olu = Recorder(base, "olu")
    olu.call("olu-session-before-login", "GET", "/api/session")
    login(
        olu,
        "login-olu",
        OLD,
        OLD_PASSWORD,
        note="An older account: `recording_acknowledged` is false. The card is drawn.",
    )
    templates = olu.call("templates", "GET", "/api/templates").json()["templates"]
    olu.call(
        "meeting-create-before-ack",
        "POST",
        "/api/meetings",
        body={"url": MEET, "template_id": templates[0]["id"]},
        note="Nothing is blocked while `recording_acknowledged` is false: the bot is sent (201).",
    )
    olu.call(
        "ack-without-csrf",
        "POST",
        "/api/settings/recording-ack",
        csrf=None,
        note="A write needs the CSRF header.",
    )
    olu.call(
        "ack",
        "POST",
        "/api/settings/recording-ack",
        note="The card's button. 200; stores the time and the version.",
    )
    note_file("account-row-after-ack", "The older account after the click.", acks(engine, OLD))
    olu.call(
        "session-after-ack",
        "GET",
        "/api/session",
        note="`recording_acknowledged` is true: the card is gone.",
    )
    olu.call(
        "ack-again",
        "POST",
        "/api/settings/recording-ack",
        note="A second call (a double click): the same 200, the first time stays.",
    )
    stranger = Recorder(base, "visitor")
    stranger.call("session-stranger", "GET", "/api/session")
    stranger.call("ack-logged-out", "POST", "/api/settings/recording-ack", note="Logged out: 401.")


if __name__ == "__main__":
    main()
