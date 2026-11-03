"""Drive the Google Calendar connect routes over real HTTP and save every exchange for the guide.

    python scripts/capture_calendar_connect.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

The argument is any Postgres server the script may create a scratch database on. It runs the real
migrations there (migration 0006 creates the admin account from OWNER_EMAIL and OWNER_PASSWORD,
placeholders below), starts the real app under uvicorn on a free local port and walks the connect
journeys with httpx. Each exchange is written to `docs/fe-captures/calendar-connect/NN-name.json`,
so every claim in `docs/fe-integration-guide-calendar-connect.md` can be re-derived.

What is real: the app, the guard, CSRF, validation, cookies, the consent URL (built by the real
`GoogleOAuth`), the PKCE and token-exchange code, the vault and the database row. What is not:
Google's own servers. `tests/fakes/google.py` stands in for the consent screen and the token
endpoint (it checks the PKCE verifier, the redirect URI and the single use of a code, like Google),
so **a real Google consent has not been captured**: that needs a real Gmail account.

Secrets in the files are masked: CSRF tokens `<csrf-A>`, every cookie value `<cookie>`, and the
state, nonce and code challenge in the consent URL `<state>`, `<nonce>`, `<code_challenge>`.
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
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "fe-captures" / "calendar-connect"
DB_NAME = "notetaker_capture_calendar"
ADMIN_EMAIL = "admin@example.test"  # the admin account migration 0006 creates (a placeholder)
ADMIN_PASSWORD = "-".join(["capture", "admin", "placeholder"])
OTHER_EMAIL = "ben@example.test"
OTHER_PASSWORD = "-".join(["ben", "chosen", "password", "2"])
KEY = (
    "S" * 43 + "="
)  # a placeholder 32-byte base64 key, only ever used against the scratch database


def scratch_url_for(admin_url: str) -> str:
    return urlparse(admin_url)._replace(path=f"/{DB_NAME}").geturl()


ADMIN_URL = sys.argv[1] if len(sys.argv) > 1 else ""
SCRATCH_URL = scratch_url_for(ADMIN_URL)
os.environ["DATABASE_URL"] = SCRATCH_URL  # read by app.db when it is imported below
os.environ["OWNER_EMAIL"] = ADMIN_EMAIL  # read by migration 0006
os.environ["OWNER_PASSWORD"] = ADMIN_PASSWORD
os.environ["TOKEN_ENC_KEY"] = KEY  # read by app.vault through get_settings()

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import google_oauth  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import CalendarConnection, User  # noqa: E402
from app.passwords import hash_password  # noqa: E402
from app.services import get_vexa  # noqa: E402
from tests.fakes.google import FakeGoogle  # noqa: E402

CALENDAR_SCOPE = google_oauth.CALENDAR_SCOPE


class FakeVexa:
    def send_bot(self, url):
        return 4242

    def stop_bot(self, platform, native_id):
        pass


class Recorder:
    """A logical browser: a cookie jar, its CSRF token, and a numbered file for each call."""

    count = 0
    masks: dict[str, str] = {}

    def __init__(self, base_url: str, who: str) -> None:
        self.http = httpx.Client(base_url=base_url, follow_redirects=False)
        self.csrf: str | None = None
        self.who = who

    @classmethod
    def mask(cls, token: str) -> str:
        return cls.masks.setdefault(token, f"<csrf-{chr(ord('A') + len(cls.masks))}>")

    @staticmethod
    def scrub(text_: str) -> str:
        text_ = re.sub(r"\b(state|nonce|code_challenge)=[\w-]{20,}", r"\1=<\1>", text_)
        return re.sub(r"\bcode=[\w%./-]{10,}", "code=<code>", text_)

    def call(self, name, method, path, *, body=None, csrf="session", note=""):
        headers = {}
        token = (None if method == "GET" else self.csrf) if csrf == "session" else csrf
        if token:
            headers["X-CSRF-Token"] = token
        cookies_sent = sorted(self.http.cookies.keys()) if self.http.cookies else []
        response = self.http.request(method, path, json=body, headers=headers)
        is_json = "json" in response.headers.get("content-type", "")
        data = response.json() if is_json else response.text
        if isinstance(data, dict) and "csrf_token" in data:
            self.csrf = data["csrf_token"]
        Recorder.count += 1
        sent = {k: (self.mask(v) if k == "X-CSRF-Token" else v) for k, v in headers.items()}
        if cookies_sent:
            sent["Cookie"] = "; ".join(f"{c}=<cookie>" for c in cookies_sent)
        kept = {
            k: self.scrub(response.headers[k])
            for k in ("content-type", "cache-control", "location")
            if k in response.headers
        }
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
            "request": {"method": method, "path": self.scrub(path), "headers": sent, "body": body},
            "response": {"status": response.status_code, "headers": kept, "body": shown},
        }
        out = self.scrub(json.dumps(record, indent=2, ensure_ascii=False)) + "\n"
        (OUT / f"{Recorder.count:02d}-{name}.json").write_text(out)
        print(
            f"{Recorder.count:02d}-{name:44} {method:5} {self.scrub(path)[:46]:46} -> {response.status_code}"
        )
        return response


def note_file(name: str, note: str, content: dict) -> None:
    """Save something that is not an HTTP exchange (the stored row)."""
    Recorder.count += 1
    record = {"note": note, "content": content}
    (OUT / f"{Recorder.count:02d}-{name}.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    )
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
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f"drop database if exists {DB_NAME} with (force)"))
        connection.execute(text(f"create database {DB_NAME}"))
    admin.dispose()
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")


def row_summary(engine) -> list[dict]:
    """The stored connection rows. The token column is shown as its length: it is bytes, not text,
    and must never be readable here."""
    with Session(engine) as session:
        rows = []
        for conn in session.scalars(select(CalendarConnection).order_by(CalendarConnection.id)):
            blob = conn.refresh_token_enc
            rows.append(
                {
                    "user_id": conn.user_id,
                    "provider": conn.provider,
                    "google_account_email": conn.google_account_email,
                    "google_sub": conn.google_sub,
                    "google_hosted_domain": conn.google_hosted_domain,
                    "status": conn.status,
                    "granted_scopes": conn.granted_scopes,
                    "refresh_token_enc": f"<{type(blob).__name__}, {len(blob)} bytes>",
                    "key_version": conn.key_version,
                    "last_sync_at": conn.last_sync_at,
                    "sync_token": conn.sync_token,
                    "expected_expiry_at": conn.expected_expiry_at,
                }
            )
        return json.loads(json.dumps(rows, default=str))


def login(client: Recorder, name: str, email: str, password: str) -> None:
    client.call(f"{name}-session", "GET", "/api/session", note="Read the CSRF token.")
    client.call(
        name, "POST", "/api/session", body={"email": email, "password": password}, note="Log in."
    )


def consent_and_callback(
    client,
    google,
    name,
    *,
    features=None,
    return_to=None,
    grant=None,
    previously_granted=frozenset(),
    note="",
    **identity,
):
    """Connect, press Allow on the fake consent screen, follow the redirect to the callback."""
    body = {}
    if features is not None:
        body["features"] = features
    if return_to is not None:
        body["return_to"] = return_to
    started = client.call(f"{name}-connect", "POST", "/api/calendar/google/connect", body=body)
    url = started.json()["authorize_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    code = google.consent(url, grant=grant, previously_granted=previously_granted, **identity)
    return (
        client.call(
            f"{name}-callback",
            "GET",
            f"/api/calendar/google/callback?code={code}&state={state}",
            note=note,
        ),
        url,
        state,
        code,
    )


def run(app, base, engine, settings, google) -> None:
    user = Recorder(base, "signed-in user")
    login(user, "login", ADMIN_EMAIL, ADMIN_PASSWORD)
    Recorder(base, "visitor").call(
        "connect-without-login",
        "POST",
        "/api/calendar/google/connect",
        body={},
        csrf=None,
        note="No session cookie and no CSRF header.",
    )
    user.call(
        "connect-without-csrf",
        "POST",
        "/api/calendar/google/connect",
        body={},
        csrf=None,
        note="Signed in, but no X-CSRF-Token.",
    )
    user.call(
        "connect-bad-features",
        "POST",
        "/api/calendar/google/connect",
        body={"features": ["calendars"]},
        note="Only `calendar` and `meet` exist.",
    )
    # Feature off: the flag the server operator sets.
    live = app.state.settings
    app.state.settings = live.model_copy(update={"calendar_enabled": False})
    user.call(
        "connect-feature-off",
        "POST",
        "/api/calendar/google/connect",
        body={},
        note="`CALENDAR_ENABLED` is false: the whole feature answers 404.",
    )
    user.call("callback-feature-off", "GET", "/api/calendar/google/callback?code=x&state=y")
    app.state.settings = live

    # The happy path.
    response, url, state, code = consent_and_callback(
        user,
        google,
        "first-connect",
        return_to="/settings/calendar",
        note="Allow on the consent screen; the app stores the connection and redirects.",
    )
    note_file(
        "stored-row-after-first-connect",
        "What `calendar_connections` holds after the call above (read from the database).",
        {"rows": row_summary(engine)},
    )
    # Cancel on the consent screen.
    started = user.call(
        "cancel-connect",
        "POST",
        "/api/calendar/google/connect",
        body={"return_to": "/settings/calendar"},
    )
    cancel_state = parse_qs(urlparse(started.json()["authorize_url"]).query)["state"][0]
    user.call(
        "cancel-callback",
        "GET",
        f"/api/calendar/google/callback?error=access_denied&state={cancel_state}",
        note="Google sends `error=access_denied` when the person presses Cancel.",
    )
    # The calendar box unticked.
    consent_and_callback(
        user,
        google,
        "unticked",
        grant={"email"},
        return_to="/settings/calendar",
        note="The person unticked the calendar permission: refused, nothing is stored.",
    )
    # Reused state.
    user.call(
        "reused-state",
        "GET",
        f"/api/calendar/google/callback?code={code}&state={state}",
        note="The first connect's state again: it was used up, so `google_failed`.",
    )
    # No cookie: same user, other browser.
    other_browser = Recorder(base, "same user, other browser")
    login(other_browser, "other-browser-login", ADMIN_EMAIL, ADMIN_PASSWORD)
    started = user.call("elsewhere-connect", "POST", "/api/calendar/google/connect", body={})
    url2 = started.json()["authorize_url"]
    state2 = parse_qs(urlparse(url2).query)["state"][0]
    code2 = google.consent(url2)
    other_browser.call(
        "elsewhere-callback",
        "GET",
        f"/api/calendar/google/callback?code={code2}&state={state2}",
        note="The flow was started in another browser: no `calendar_flow` cookie here.",
    )
    # Another signed-in user, even holding the cookie.
    with Session(engine) as session:
        session.add(
            User(
                email=OTHER_EMAIL,
                password_hash=hash_password(OTHER_PASSWORD),
                email_verified_at=text("now()"),
            )
        )
        session.commit()
    ben = Recorder(base, "a different user")
    login(ben, "ben-login", OTHER_EMAIL, OTHER_PASSWORD)
    started = user.call("wrong-user-connect", "POST", "/api/calendar/google/connect", body={})
    url3 = started.json()["authorize_url"]
    state3 = parse_qs(urlparse(url3).query)["state"][0]
    code3 = google.consent(url3)
    ben.http.cookies.set("calendar_flow", state3, path="/api/calendar/google")
    ben.call(
        "wrong-user-callback",
        "GET",
        f"/api/calendar/google/callback?code={code3}&state={state3}",
        note="Ben is signed in and even holds the flow cookie, but the flow is the first user's.",
    )
    # Signed out at the callback.
    started = user.call("signed-out-connect", "POST", "/api/calendar/google/connect", body={})
    url4 = started.json()["authorize_url"]
    state4 = parse_qs(urlparse(url4).query)["state"][0]
    code4 = google.consent(url4)
    Recorder(base, "signed-out visitor").call(
        "signed-out-callback",
        "GET",
        f"/api/calendar/google/callback?code={code4}&state={state4}",
        note="A visitor with no session: sent to the login page.",
    )
    # Reconnect replaces.
    consent_and_callback(
        user,
        google,
        "reconnect",
        return_to="/settings/calendar",
        note="A second connect for the same user: one row, updated.",
    )
    note_file(
        "stored-row-after-reconnect", "Still one row for the user.", {"rows": row_summary(engine)}
    )
    # Meet later, incrementally.
    consent_and_callback(
        user,
        google,
        "meet-upgrade",
        features=["meet"],
        previously_granted=frozenset({CALENDAR_SCOPE}),
        note="Turning on Meet later asks for the Meet permission only; the calendar one comes back.",
    )
    note_file(
        "stored-row-after-meet", "`granted_scopes` now holds both.", {"rows": row_summary(engine)}
    )
    # Verified app: no notice.
    app.state.settings = live.model_copy(update={"google_app_verified": True})
    user.call(
        "connect-verified-app",
        "POST",
        "/api/calendar/google/connect",
        body={},
        note="Once `GOOGLE_APP_VERIFIED` is true the unverified-app notice is not needed.",
    )
    app.state.settings = live


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
        database_url=SCRATCH_URL,
        public_base_url="https://notes.example.test",
        google_client_id="capture-client-id.apps.example.test",
        google_client_secret="capture-client-secret",
        calendar_enabled=True,
        token_enc_key=KEY,
    )
    google = FakeGoogle(settings)
    app = create_app(settings)
    app.dependency_overrides[get_vexa] = lambda: FakeVexa()
    app.dependency_overrides[google_oauth.get_google] = lambda: google.oauth
    server, base = start_server(app)
    engine = create_engine(SCRATCH_URL)
    try:
        run(app, base, engine, settings, google)
    finally:
        server.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    main()
