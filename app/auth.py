"""Owner login: a signed session cookie, a CSRF token for every POST, and a failed-login limit.

The session cookie holds `{"auth": bool, "csrf": token, "iat": seconds}` signed with HMAC-SHA256
using `session_secret`. Anonymous visitors get a session too (so the login form has a CSRF token);
logging in issues a fresh one, so a cookie from before login is never promoted.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict, deque

from fastapi import Request

from app.config import Settings

COOKIE_NAME = "session"
SESSION_SECONDS = 30 * 24 * 3600
MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 15 * 60


class NotLoggedIn(Exception):
    pass


class CsrfRejected(Exception):
    pass


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _signature(secret: str, body: str) -> str:
    return _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())


def new_session(*, auth: bool = False, now: float | None = None) -> dict:
    return {"auth": auth, "csrf": secrets.token_urlsafe(32), "iat": int(now or time.time())}


def sign_session(secret: str, session: dict) -> str:
    body = _b64(json.dumps(session, separators=(",", ":")).encode())
    return f"{body}.{_signature(secret, body)}"


def read_session(secret: str, cookie: str | None, now: float | None = None) -> dict | None:
    """The session in a cookie, or None if it is missing, forged, malformed or expired."""
    if not cookie or cookie.count(".") != 1:
        return None
    body, signature = cookie.split(".")
    if not hmac.compare_digest(signature, _signature(secret, body)):
        return None
    try:
        data = json.loads(_unb64(body))
        csrf, iat, auth = data["csrf"], data["iat"], data["auth"]
    except (ValueError, KeyError, TypeError):
        return None
    if not (isinstance(csrf, str) and isinstance(iat, int) and isinstance(auth, bool)):
        return None
    if (now or time.time()) - iat > SESSION_SECONDS:
        return None
    return {"auth": auth, "csrf": csrf, "iat": iat}


def password_matches(settings: Settings, given: str) -> bool:
    expected = settings.owner_password.encode()
    return bool(expected) and hmac.compare_digest(given.encode(), expected)


class LoginLimiter:
    """Failed logins per client in a sliding window. In memory: a restart resets it."""

    def __init__(self, limit: int = MAX_FAILURES, window: float = FAILURE_WINDOW_SECONDS) -> None:
        self.limit = limit
        self.window = window
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _recent(self, client: str, now: float) -> deque[float]:
        failures = self._failures[client]
        while failures and now - failures[0] > self.window:
            failures.popleft()
        if not failures:
            self._failures.pop(client, None)
        return failures

    def blocked(self, client: str, now: float | None = None) -> bool:
        return len(self._recent(client, now or time.monotonic())) >= self.limit

    def record_failure(self, client: str, now: float | None = None) -> None:
        now = now or time.monotonic()
        self._recent(client, now)
        self._failures[client].append(now)

    def reset(self, client: str) -> None:
        self._failures.pop(client, None)


def check_start(settings: Settings) -> None:
    """Refuse to run in production with no password or no signing secret."""
    if settings.app_env != "production":
        return
    for name in ("owner_password", "session_secret"):
        if not getattr(settings, name):
            raise RuntimeError(f"{name} must be set before the app can start in production")


async def guard(request: Request) -> None:
    """App-wide check: owner pages need a login, and every POST needs a valid CSRF token."""
    path = request.url.path
    if path == "/health":
        return
    session = request.state.session
    public = path == "/login"
    if not public and not session["auth"]:
        raise NotLoggedIn
    if request.method == "POST":
        form = await request.form()
        given = form.get("csrf_token")
        if not isinstance(given, str) or not hmac.compare_digest(
            given.encode(), session["csrf"].encode()
        ):
            raise CsrfRejected
