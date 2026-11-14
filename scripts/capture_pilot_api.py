"""Drive the pilot-accounts API over real HTTP and save every exchange for the frontend guide.

    python scripts/capture_pilot_api.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

The argument is any Postgres server the script may create a scratch database on. It runs the real
migrations there (migration 0006 creates the admin account from OWNER_EMAIL and OWNER_PASSWORD,
set below to placeholders), starts the real app under uvicorn on a free local port, and walks the
sign-up, verification, reset, waitlist, Google, admin and notification journeys with httpx. Each
exchange is written to `docs/fe-captures/pilot-accounts/NN-name.json`, so every claim in
`docs/fe-integration-guide-pilot-accounts.md` can be re-derived by running this again.

What is real: the app, auth, CSRF, validation, rate limits, database, migrations, serialisation,
every error, the mail the app queues (read back from `mail_outbox`, which is what the worker would
send) and the Google redirect URL the app builds. What is not: Resend (nothing is sent; the
outbox row is what a mail would contain), Google's own servers (the consent screen and the token
exchange are replaced by a stand-in that returns a chosen identity: see `StandInGoogle`), Vexa and
the worker. The files say so wherever it matters.

Secrets in the files are masked: CSRF tokens become `<csrf-A>`, `<csrf-B>` in order of first
appearance, every cookie value `<cookie>`, and mail tokens `<token-1>`, `<token-2>` and so on.
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
OUT = ROOT / "docs" / "fe-captures" / "pilot-accounts"
DB_NAME = "notetaker_capture_pilot"
ADMIN_EMAIL = "admin@example.test"  # the admin account migration 0006 creates (a placeholder)
ADMIN_PASSWORD = "-".join(["capture", "admin", "placeholder"])
ADA = "ada@example.test"  # placeholders, not real people
ADA_PASSWORD = "-".join(["ada", "chosen", "password", "1"])
BEN = "ben@example.test"
BEN_PASSWORD = "-".join(["ben", "chosen", "password", "2"])
NEW_PASSWORD = "-".join(["ada", "new", "password", "3"])
WAIT = "wendy@example.test"
WAIT_PASSWORD = "-".join(["wendy", "chosen", "password", "4"])
GOOGLE_ADA = "grace@example.test"


def database_urls(admin_url: str) -> tuple[str, str]:
    parsed = urlparse(admin_url)
    return admin_url, parsed._replace(path=f"/{DB_NAME}").geturl()


admin_url, scratch_url = database_urls(sys.argv[1] if len(sys.argv) > 1 else "")
os.environ["DATABASE_URL"] = scratch_url  # read by app.db when it is imported below
os.environ["OWNER_EMAIL"] = ADMIN_EMAIL  # read by migration 0006
os.environ["OWNER_PASSWORD"] = ADMIN_PASSWORD

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import google_oauth  # noqa: E402
from app.config import Settings  # noqa: E402
from app.limits import Limit, PublicLimits  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import MailOutbox  # noqa: E402
from app.services import get_vexa  # noqa: E402


class StandInGoogle(google_oauth.GoogleOAuth):
    """The real client, so the consent URL is the one the app really builds, except that the code
    exchange (a call to Google's servers) answers with `identity` instead of going out."""

    identity = google_oauth.Identity("g-capture-1", GOOGLE_ADA, "Grace Example")

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
    # Every call comes from one address, so the sign-up address limit (5 an hour) would stop the
    # walk-through; it is raised here and the real one is shown at the very end.
    app.state.public_limits.signup_address = Limit(10_000, 3600)
    server, base = start_server(app)
    engine = create_engine(scratch_url)
    try:
        run(app, base, engine, settings)
    finally:
        server.should_exit = True
        engine.dispose()


def outbox(engine, template: str, to: str) -> dict:
    """The newest queued mail of a kind for an address, as the worker would read it."""
    with Session(engine) as session:
        row = session.scalars(
            select(MailOutbox)
            .where(MailOutbox.template == template, MailOutbox.to_email == to)
            .order_by(MailOutbox.id.desc())
        ).first()
        return {"to_email": row.to_email, "template": row.template, "payload": dict(row.payload)}


def mail_count(engine, template: str, to: str) -> int:
    with Session(engine) as session:
        return len(
            session.scalars(
                select(MailOutbox.id).where(
                    MailOutbox.template == template, MailOutbox.to_email == to
                )
            ).all()
        )


def token_of(mail: dict) -> str:
    return re.search(r"(?:token|invite)=([\w-]+)", mail["payload"]["link"])[1]


def login(client: Recorder, name: str, email: str, password: str, note="") -> httpx.Response:
    return client.call(
        name, "POST", "/api/session", body={"email": email, "password": password}, note=note
    )


def run(app, base, engine, settings) -> None:
    visitor = Recorder(base, "visitor")
    admin = Recorder(base, "admin")
    visitor.call(
        "session-anonymous",
        "GET",
        "/api/session",
        note="A visitor: note `unread_notifications` is null.",
    )
    # Google switched off (no client configured): what the frontend sees when it is not set up.
    live_settings = app.state.settings
    stand_in = app.dependency_overrides.pop(google_oauth.get_google)
    app.state.settings = live_settings.model_copy(
        update={"google_client_id": "", "google_client_secret": ""}
    )
    visitor.call(
        "pilot-google-off",
        "GET",
        "/api/pilot",
        note="No Google client configured: `google_signin` is false.",
    )
    visitor.call(
        "google-start-unavailable",
        "GET",
        "/api/auth/google/start",
        note="And the routes answer 404.",
    )
    app.state.settings = live_settings
    app.dependency_overrides[google_oauth.get_google] = stand_in
    visitor.call(
        "pilot-closed",
        "GET",
        "/api/pilot",
        note="The pilot starts closed; Google is on because the client is configured.",
    )
    visitor.call(
        "signup-while-closed",
        "POST",
        "/api/signup",
        body={
            "name": "Ada Example",
            "email": ADA,
            "password": ADA_PASSWORD,
            "accept_terms": True,
            "accept_recording": True,
        },
        note="Sign-up before the admin opens the pilot.",
    )

    # --- the admin opens the pilot ---
    admin.call("admin-session-anonymous", "GET", "/api/session")
    login(admin, "admin-login", ADMIN_EMAIL, ADMIN_PASSWORD, "The admin signs in like anyone.")
    admin.call(
        "admin-pilot-read",
        "GET",
        "/api/admin/pilot",
        note="Default settings: closed, 10 accounts, 5 hours.",
    )
    admin.call(
        "admin-pilot-invalid",
        "PUT",
        "/api/admin/pilot",
        body={
            "signups_open": True,
            "max_accounts": -1,
            "default_hours_cap_seconds": 18000,
            "password_signup_enabled": True,
        },
    )
    admin.call(
        "admin-pilot-open",
        "PUT",
        "/api/admin/pilot",
        body={
            "signups_open": True,
            "max_accounts": 3,
            "default_hours_cap_seconds": 18000,
            "password_signup_enabled": True,
        },
        note="Open, 3 accounts so that 'full' can be shown, password sign-up on.",
    )
    visitor.call("pilot-open", "GET", "/api/pilot")

    # --- sign-up ---
    form = {
        "name": "Ada Example",
        "email": ADA,
        "password": ADA_PASSWORD,
        "accept_terms": True,
        "accept_recording": True,
    }
    visitor.call(
        "signup-weak-password", "POST", "/api/signup", body={**form, "password": "password"}
    )
    visitor.call(
        "signup-terms-not-accepted", "POST", "/api/signup", body={**form, "accept_terms": False}
    )
    visitor.call("signup-bad-email", "POST", "/api/signup", body={**form, "email": "not-an-email"})
    visitor.call("signup-missing-field", "POST", "/api/signup", body={"email": ADA})
    visitor.call("signup-extra-field", "POST", "/api/signup", body={**form, "is_admin": True})
    visitor.call("signup-without-csrf", "POST", "/api/signup", body=form, csrf=None)
    visitor.call("signup-ok", "POST", "/api/signup", body=form, note="202 for a new address.")
    visitor.call(
        "signup-again-same-minute",
        "POST",
        "/api/signup",
        body=form,
        note="The same address within a minute: the same 202, and nothing is done (no second mail).",
    )
    assert mail_count(engine, "verify_email", ADA) == 1
    note_file(
        "mail-verify-email",
        "What the worker would send (the `mail_outbox` row). The link is built from PUBLIC_BASE_URL; the page it opens belongs to the frontend.",
        outbox(engine, "verify_email", ADA),
    )
    verify_token = token_of(outbox(engine, "verify_email", ADA))
    visitor.call(
        "login-before-verifying",
        "POST",
        "/api/session",
        body={"email": ADA, "password": ADA_PASSWORD},
        note="A correct password for an unconfirmed account: the same 401 as a wrong password or an unknown email (it must not reveal the account), no session.",
    )
    visitor.call(
        "verify-wrong-password",
        "POST",
        "/api/verify-email",
        body={"token": verify_token, "password": "-".join(["not", "it", "at", "all"])},
        note="The link stays usable.",
    )
    visitor.call(
        "verify-unknown-token",
        "POST",
        "/api/verify-email",
        body={"token": "nonsense", "password": ADA_PASSWORD},
    )
    visitor.call(
        "verify-ok",
        "POST",
        "/api/verify-email",
        body={"token": verify_token, "password": ADA_PASSWORD},
        note="Does not sign in.",
    )
    visitor.call(
        "verify-used-token",
        "POST",
        "/api/verify-email",
        body={"token": verify_token, "password": ADA_PASSWORD},
    )
    visitor.call("session-after-verify", "GET", "/api/session", note="Still anonymous.")
    ada = Recorder(base, "ada")
    ada.call("ada-session-anonymous", "GET", "/api/session")
    login(ada, "ada-login", ADA, ADA_PASSWORD)
    ada.call(
        "ada-session",
        "GET",
        "/api/session",
        note="`unread_notifications` is 0; usage has the 5 h cap.",
    )

    # an address that already has an account
    visitor.http.cookies.clear()
    visitor.call("session-again", "GET", "/api/session")
    app.state.public_limits.signup_email.__init__(
        1, 60.0
    )  # a minute has passed since Ada signed up
    visitor.call(
        "signup-existing-account",
        "POST",
        "/api/signup",
        body={**form, "email": ADA},
        note="Same body and status as for a new address; the mail queued is `already_registered`.",
    )
    note_file(
        "mail-already-registered",
        "Queued instead of a verification mail.",
        outbox(engine, "already_registered", ADA),
    )

    # --- resend ---
    ben = Recorder(base, "ben")
    ben.call("ben-session", "GET", "/api/session")
    ben.call(
        "ben-signup",
        "POST",
        "/api/signup",
        body={
            "name": "Ben Example",
            "email": BEN,
            "password": BEN_PASSWORD,
            "accept_terms": True,
            "accept_recording": True,
        },
    )
    ben.call(
        "resend-unverified",
        "POST",
        "/api/signup/resend",
        body={"email": BEN},
        note="202; a new link is queued for the same password.",
    )
    assert mail_count(engine, "verify_email", BEN) == 2
    ben.call(
        "resend-unverified-again",
        "POST",
        "/api/signup/resend",
        body={"email": BEN},
        note="Within the minute: the same 202, and no new mail (1 per minute per email).",
    )
    assert mail_count(engine, "verify_email", BEN) == 2
    ben.call(
        "resend-unknown-email",
        "POST",
        "/api/signup/resend",
        body={"email": "nobody@example.test"},
        note="Identical to the answers above.",
    )
    assert mail_count(engine, "verify_email", "nobody@example.test") == 0
    ben.call("resend-malformed-email", "POST", "/api/signup/resend", body={"email": "x"})

    # --- password reset ---
    visitor.call("forgot-known", "POST", "/api/password/forgot", body={"email": ADA})
    visitor.call(
        "forgot-unknown",
        "POST",
        "/api/password/forgot",
        body={"email": "nobody@example.test"},
        note="Identical to the answer above.",
    )
    assert (
        mail_count(engine, "reset_password", ADA) == 1
        and mail_count(engine, "reset_password", "nobody@example.test") == 0
    )
    note_file("mail-reset-password", "30-minute link.", outbox(engine, "reset_password", ADA))
    reset_token = token_of(outbox(engine, "reset_password", ADA))
    visitor.call(
        "reset-weak-password",
        "POST",
        "/api/password/reset",
        body={"token": reset_token, "password": "password"},
        note="The token is not used up.",
    )
    visitor.call(
        "reset-bad-token",
        "POST",
        "/api/password/reset",
        body={"token": "nonsense", "password": NEW_PASSWORD},
    )
    visitor.call(
        "reset-ok",
        "POST",
        "/api/password/reset",
        body={"token": reset_token, "password": NEW_PASSWORD},
        note="Signs Ada out everywhere; does not sign anyone in.",
    )
    visitor.call(
        "reset-used-token",
        "POST",
        "/api/password/reset",
        body={"token": reset_token, "password": NEW_PASSWORD},
    )
    ada.call("ada-session-after-reset", "GET", "/api/session", note="Ada's old session is dead.")
    login(ada, "ada-login-old-password", ADA, ADA_PASSWORD)
    login(ada, "ada-login-new-password", ADA, NEW_PASSWORD)

    # --- waitlist ---
    visitor.call(
        "waitlist-join", "POST", "/api/waitlist", body={"email": WAIT, "name": "Wendy Example"}
    )
    visitor.call(
        "waitlist-join-again",
        "POST",
        "/api/waitlist",
        body={"email": WAIT},
        note="Identical answer; nothing changes.",
    )
    visitor.call("waitlist-bad-email", "POST", "/api/waitlist", body={"email": "x"})

    # --- the pilot fills up ---
    visitor.call(
        "signup-another",
        "POST",
        "/api/signup",
        body={
            "name": "Third",
            "email": "third@example.test",
            "password": ADA_PASSWORD,
            "accept_terms": True,
            "accept_recording": True,
        },
        note="Unconfirmed accounts hold no place: the pilot (3 places) is still open.",
    )
    visitor.call(
        "pilot-still-open-with-unconfirmed-accounts",
        "GET",
        "/api/pilot",
        note="Ben, Third and Late are unconfirmed: none holds a place.",
    )
    visitor.call(
        "signup-late",
        "POST",
        "/api/signup",
        body={
            "name": "Late Example",
            "email": "late@example.test",
            "password": ADA_PASSWORD,
            "accept_terms": True,
            "accept_recording": True,
        },
    )
    ben_link = token_of(outbox(engine, "verify_email", BEN))
    third_link = token_of(outbox(engine, "verify_email", "third@example.test"))
    late_link = token_of(outbox(engine, "verify_email", "late@example.test"))
    ben.call(
        "ben-verify",
        "POST",
        "/api/verify-email",
        body={"token": ben_link, "password": BEN_PASSWORD},
        note="Confirming takes a place: Ada and Ben.",
    )
    visitor.call(
        "third-verify",
        "POST",
        "/api/verify-email",
        body={"token": third_link, "password": ADA_PASSWORD},
        note="The third place: the pilot is now full.",
    )
    visitor.call("pilot-full", "GET", "/api/pilot")
    visitor.call(
        "verify-pilot-filled-up",
        "POST",
        "/api/verify-email",
        body={"token": late_link, "password": ADA_PASSWORD},
        note="Late signed up while there was room but confirms after the pilot filled up: 409, and the link is not used up.",
    )
    visitor.call(
        "signup-pilot-full",
        "POST",
        "/api/signup",
        body={
            "name": "Later Example",
            "email": "later@example.test",
            "password": ADA_PASSWORD,
            "accept_terms": True,
            "accept_recording": True,
        },
    )

    # --- admin ---
    admin.call("admin-users", "GET", "/api/admin/users")
    admin.call("admin-users-page", "GET", "/api/admin/users?limit=1")
    admin.call("admin-waitlist", "GET", "/api/admin/waitlist")
    with Session(engine) as session:
        ada_id = session.scalar(text("select id from users where email = :e"), {"e": ADA})
        ben_id = session.scalar(text("select id from users where email = :e"), {"e": BEN})
        admin_id = session.scalar(text("select id from users where email = :e"), {"e": ADMIN_EMAIL})
        wait_id = session.scalar(
            text("select id from waitlist_entries where email = :e"), {"e": WAIT}
        )
    admin.call(
        "admin-invite-full",
        "POST",
        f"/api/admin/waitlist/{wait_id}/invite",
        note="No free place: 409.",
    )
    admin.call(
        "admin-patch-suspend", "PATCH", f"/api/admin/users/{ben_id}", body={"status": "suspended"}
    )
    admin.call(
        "admin-patch-hours",
        "PATCH",
        f"/api/admin/users/{ada_id}",
        body={"hours_cap_seconds": 36000},
    )
    admin.call(
        "admin-patch-clear-hours",
        "PATCH",
        f"/api/admin/users/{ada_id}",
        body={"hours_cap_seconds": None},
    )
    admin.call("admin-patch-empty", "PATCH", f"/api/admin/users/{ada_id}", body={})
    admin.call(
        "admin-patch-is-admin", "PATCH", f"/api/admin/users/{ada_id}", body={"is_admin": True}
    )
    admin.call(
        "admin-patch-last-admin",
        "PATCH",
        f"/api/admin/users/{admin_id}",
        body={"status": "suspended"},
    )
    admin.call("admin-delete-last-admin", "DELETE", f"/api/admin/users/{admin_id}")
    admin.call("admin-patch-unknown", "PATCH", "/api/admin/users/99999", body={"status": "active"})
    admin.call(
        "admin-patch-reactivate", "PATCH", f"/api/admin/users/{ben_id}", body={"status": "active"}
    )
    ada.call(
        "ada-admin-route-forbidden",
        "GET",
        "/api/admin/users",
        note="An ordinary user: 403 on every admin route.",
    )
    visitor.call("admin-route-anonymous", "GET", "/api/admin/users", note="Not signed in: 401.")
    admin.call(
        "admin-reset-link",
        "POST",
        f"/api/admin/users/{ada_id}/reset-link",
        note="Shown once, never mailed.",
    )
    admin.call("admin-reset-link-admin", "POST", f"/api/admin/users/{admin_id}/reset-link")
    admin.call(
        "admin-pilot-more-room",
        "PUT",
        "/api/admin/pilot",
        body={
            "signups_open": True,
            "max_accounts": 4,
            "default_hours_cap_seconds": 18000,
            "password_signup_enabled": True,
        },
    )
    admin.call(
        "admin-invite",
        "POST",
        f"/api/admin/waitlist/{wait_id}/invite",
        note="Mail is configured (the console mailer stands in for it), so the link is queued as a mail, not returned.",
    )
    invite_mail = outbox(engine, "waitlist_invite", WAIT)
    note_file("mail-waitlist-invite", "7 days; reserves the place.", invite_mail)
    invite_token = token_of(invite_mail)
    admin.call(
        "admin-pilot-after-invite",
        "GET",
        "/api/admin/pilot",
        note="`slots_taken` counts the reservation.",
    )
    other = Recorder(base, "someone-else")
    other.call("other-session", "GET", "/api/session")
    other.call(
        "signup-with-invite-wrong-address",
        "POST",
        "/api/signup",
        body={
            "name": "Mallory",
            "email": "mallory@example.test",
            "password": ADA_PASSWORD,
            "accept_terms": True,
            "accept_recording": True,
            "invite_token": invite_token,
        },
    )
    other.call(
        "signup-with-invite",
        "POST",
        "/api/signup",
        body={
            "name": "Wendy Example",
            "email": WAIT,
            "password": WAIT_PASSWORD,
            "accept_terms": True,
            "accept_recording": True,
            "invite_token": invite_token,
        },
        note="Works for the invited address only, even if the pilot is full or closed.",
    )
    other.call(
        "signup-with-invite-used",
        "POST",
        "/api/signup",
        body={
            "name": "Wendy Example",
            "email": WAIT,
            "password": WAIT_PASSWORD,
            "accept_terms": True,
            "accept_recording": True,
            "invite_token": invite_token,
        },
    )
    admin.call("admin-waitlist-after", "GET", "/api/admin/waitlist", note="Wendy is `joined`.")

    # invite_url without a mail provider: the one capture that changes the app's mail settings
    with Session(engine) as session:
        session.execute(
            text(
                "insert into waitlist_entries (email, status) values ('quiet@example.test', 'waiting')"
            )
        )
        session.commit()
        quiet = session.scalar(
            text("select id from waitlist_entries where email = 'quiet@example.test'")
        )
    admin.call(
        "admin-pilot-room-for-quiet",
        "PUT",
        "/api/admin/pilot",
        body={
            "signups_open": True,
            "max_accounts": 6,
            "default_hours_cap_seconds": 18000,
            "password_signup_enabled": True,
        },
    )
    real_settings = app.state.settings
    app.state.settings = Settings(
        _env_file=None,
        app_env="production",
        session_secret="capture-secret-" + "x" * 30,
        cookie_secure=False,
        database_url=scratch_url,
        public_base_url="https://notes.example.test",
    )  # production with no mail key: no mailer
    admin.call(
        "admin-invite-no-mail",
        "POST",
        f"/api/admin/waitlist/{quiet}/invite",
        note="No mail provider: the link is returned for the admin to hand over.",
    )
    app.state.settings = real_settings
    admin.call("admin-waitlist-remove", "DELETE", f"/api/admin/waitlist/{quiet}")
    admin.call(
        "admin-mail-failures",
        "GET",
        "/api/admin/mail-failures",
        note="Empty: the worker is not running here, so nothing has failed.",
    )
    admin.call(
        "admin-delete-user",
        "DELETE",
        f"/api/admin/users/{ben_id}",
        note="The user is signed out and the place is free at once; the worker removes their data.",
    )
    ben.call("ben-session-after-delete", "GET", "/api/session")
    login(ben, "ben-login-after-delete", BEN, BEN_PASSWORD, "A deleted account looks unknown.")

    # --- two users, one bot ---
    with Session(engine) as session:
        general = session.scalar(
            text("select id from templates where owner_user_id = :u order by id limit 1"),
            {"u": ada_id},
        )
    ada.call(
        "ada-create-meeting",
        "POST",
        "/api/meetings",
        body={"url": "https://meet.google.com/abc-defg-hij", "template_id": general},
    )
    ada.call(
        "ada-create-meeting-again",
        "POST",
        "/api/meetings",
        body={"url": "https://meet.google.com/abc-defg-hik", "template_id": general},
        note="Her own running meeting: named as hers.",
    )
    wendy = Recorder(base, "wendy")
    wendy.call("wendy-session", "GET", "/api/session")
    verify_wendy = token_of(outbox(engine, "verify_email", WAIT))
    wendy.call(
        "wendy-verify",
        "POST",
        "/api/verify-email",
        body={"token": verify_wendy, "password": WAIT_PASSWORD},
    )
    login(wendy, "wendy-login", WAIT, WAIT_PASSWORD)
    with Session(engine) as session:
        wendy_general = session.scalar(
            text(
                "select t.id from templates t join users u on u.id = t.owner_user_id where u.email = :e order by t.id limit 1"
            ),
            {"e": WAIT},
        )
    wendy.call(
        "wendy-create-meeting-busy",
        "POST",
        "/api/meetings",
        body={"url": "https://meet.google.com/xyz-wxyz-wxy", "template_id": wendy_general},
        note="The note taker is busy with someone else's meeting: neutral, says nothing about whose.",
    )

    # --- notifications ---
    with Session(engine) as session:
        for n in (1, 2):
            session.execute(
                text(
                    "insert into notifications (user_id, kind, severity, title, body, dedupe_key) values (:u, 'usage.hours_low', 'info', :t, 'Most of your recorded hours are used.', :k)"
                ),
                {"u": ada_id, "t": f"Example notice {n}", "k": f"example-{n}"},
            )
        session.commit()
    ada.call(
        "ada-session-unread",
        "GET",
        "/api/session",
        note="`unread_notifications` is 3: two examples and the admin's reset-link notice.",
    )
    ada.call(
        "notifications-list",
        "GET",
        "/api/notifications",
        note=(
            "Ids 2 and 3 were inserted by SQL in the shape the app gives `usage.hours_low` (the "
            "app writes it at 80 % of the hours); id 1 is the real notice the admin's reset "
            "link made."
        ),
    )
    ada.call("notifications-page", "GET", "/api/notifications?limit=1")
    ada.call("notifications-limit-invalid", "GET", "/api/notifications?limit=0")
    with Session(engine) as session:
        first = session.scalar(
            text("select id from notifications where user_id = :u order by id limit 1"),
            {"u": ada_id},
        )
    ada.call("notifications-read", "POST", f"/api/notifications/{first}/read")
    ada.call(
        "notifications-read-again",
        "POST",
        f"/api/notifications/{first}/read",
        note="Repeating keeps the first read time.",
    )
    wendy.call(
        "notifications-read-foreign",
        "POST",
        f"/api/notifications/{first}/read",
        note="Someone else's: 404.",
    )
    ada.call("notifications-read-all", "POST", "/api/notifications/read-all")

    # --- Google sign-in (Google's side stood in) ---
    google = Recorder(base, "google-visitor")
    google.call("google-session", "GET", "/api/session")
    google.call(
        "google-start",
        "GET",
        "/api/auth/google/start?accept_terms=true&accept_recording=true&return_to=/meetings",
        note="302 to Google's consent screen (the real URL the app builds); sets the flow cookie.",
    )
    start = google.http.get(
        "/api/auth/google/start?accept_terms=true&accept_recording=true&return_to=/meetings"
    )
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    admin.call(
        "admin-pilot-closed-again",
        "PUT",
        "/api/admin/pilot",
        body={
            "signups_open": False,
            "max_accounts": 4,
            "default_hours_cap_seconds": 18000,
            "password_signup_enabled": True,
        },
    )
    google.http.cookies.clear()
    google.call(
        "google-callback-no-cookie",
        "GET",
        f"/api/auth/google/callback?code=abc&state={state}",
        note="A flow finished in a browser that did not start it: refused.",
    )
    start = google.http.get("/api/auth/google/start?accept_terms=true&accept_recording=true")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    google.call(
        "google-callback-pilot-closed",
        "GET",
        f"/api/auth/google/callback?code=abc&state={state}",
        note="A NEW Google user while the pilot is closed.",
    )
    admin.call(
        "admin-pilot-open-google",
        "PUT",
        "/api/admin/pilot",
        body={
            "signups_open": True,
            "max_accounts": 5,
            "default_hours_cap_seconds": 18000,
            "password_signup_enabled": True,
        },
    )
    start = google.http.get("/api/auth/google/start")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    google.call(
        "google-callback-terms-not-accepted",
        "GET",
        f"/api/auth/google/callback?code=abc&state={state}",
        note="`accept_terms` was not sent at the start.",
    )
    google.call(
        "google-callback-unknown-state", "GET", "/api/auth/google/callback?code=abc&state=nonsense"
    )
    start = google.http.get("/api/auth/google/start?accept_terms=true&accept_recording=true")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    google.call(
        "google-callback-denied",
        "GET",
        f"/api/auth/google/callback?error=access_denied&state={state}",
        note="The person pressed Cancel at Google.",
    )
    start = google.http.get(
        "/api/auth/google/start?accept_terms=true&accept_recording=true&return_to=/meetings"
    )
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    google.call(
        "google-callback-ok",
        "GET",
        f"/api/auth/google/callback?code=abc&state={state}",
        note="A new Google user: created verified, no password; 302 to `return_to`; the session cookie is set.",
    )
    google.call("google-session-after", "GET", "/api/session")
    google.call(
        "google-start-unsafe-return-to",
        "GET",
        "/api/auth/google/start?return_to=//evil.example",
        note="Falls back to `/` (see the stored flow, not shown).",
    )
    # a full pilot for a Google visitor
    admin.call(
        "admin-pilot-full-for-google",
        "PUT",
        "/api/admin/pilot",
        body={
            "signups_open": True,
            "max_accounts": 1,
            "default_hours_cap_seconds": 18000,
            "password_signup_enabled": True,
        },
    )
    full = Recorder(base, "google-visitor-2")
    full.call("google-full-session", "GET", "/api/session")
    StandInGoogle.identity = google_oauth.Identity(
        "g-capture-2", "latecomer@example.test", "Late Comer"
    )
    start = full.http.get("/api/auth/google/start?accept_terms=true&accept_recording=true")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    full.call(
        "google-callback-pilot-full",
        "GET",
        f"/api/auth/google/callback?code=abc&state={state}",
        note="302 to `/pilot-full`, no address in the URL; sets a 15-minute cookie.",
    )
    full.call(
        "google-pilot-prefill", "GET", "/api/pilot", note="`prefill_email` comes from that cookie."
    )

    # --- the real sign-up address limit (it was raised above: every call here is one address) ---
    app.state.public_limits = PublicLimits()
    rate = Recorder(base, "rate-limited-visitor")
    rate.call("rate-session", "GET", "/api/session")
    weak = {
        "name": "R",
        "email": "rate@example.test",
        "password": "password",
        "accept_terms": True,
        "accept_recording": True,
    }
    for _ in range(5):
        rate.http.post("/api/signup", json=weak, headers={"X-CSRF-Token": rate.csrf})
    rate.call(
        "signup-rate-limited",
        "POST",
        "/api/signup",
        body=weak,
        note="The 6th sign-up call in an hour from one address (5 allowed; counted before anything else).",
    )

    print(f"\nwrote {Recorder.count} files to {OUT}")


if __name__ == "__main__":
    main()
