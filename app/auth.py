"""Owner login: a signed session cookie, a CSRF token for every POST, and a failed-login limit.

The session cookie holds `{"auth": bool, "csrf": token, "iat": seconds, "epoch": n}` signed with
HMAC-SHA256. The signing key is derived from `session_secret` and `owner_password` together, so
changing either signs everyone out. `epoch` is checked against `app_state.session_epoch` on every
logged-in request, and logout bumps it, so a copied cookie stops working once the owner logs
out. Anonymous visitors get a session too (so the login form has a CSRF token); logging in
issues a fresh one, so a cookie from before login is never promoted.
"""

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections import deque

from fastapi import Depends, Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_session
from app.models import AppState

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


def signing_key(settings: Settings) -> bytes:
    """Key for session cookies: depends on both secrets, so a password change revokes sessions."""
    return hmac.new(
        settings.session_secret.encode(), b"session|" + settings.owner_password.encode(), "sha256"
    ).digest()


def _signature(key: bytes, body: str) -> str:
    return _b64(hmac.new(key, body.encode(), hashlib.sha256).digest())


def new_session(*, auth: bool = False, epoch: int = 0, now: float | None = None) -> dict:
    return {
        "auth": auth,
        "csrf": secrets.token_urlsafe(32),
        "iat": int(now or time.time()),
        "epoch": epoch,
    }


def sign_session(key: bytes, session: dict) -> str:
    body = _b64(json.dumps(session, separators=(",", ":")).encode())
    return f"{body}.{_signature(key, body)}"


def read_session(key: bytes, cookie: str | None, now: float | None = None) -> dict | None:
    """The session in a cookie, or None if it is missing, forged, malformed or expired."""
    if not cookie or cookie.count(".") != 1:
        return None
    body, signature = cookie.split(".")
    # Compare bytes: compare_digest raises TypeError on non-ASCII str.
    if not hmac.compare_digest(signature.encode(), _signature(key, body).encode()):
        return None
    try:
        data = json.loads(_unb64(body))
        csrf, iat, auth, epoch = data["csrf"], data["iat"], data["auth"], data["epoch"]
    except (ValueError, KeyError, TypeError):
        return None
    if not (
        isinstance(csrf, str)
        and isinstance(iat, int)
        and isinstance(auth, bool)
        and isinstance(epoch, int)
    ):
        return None
    if (now or time.time()) - iat > SESSION_SECONDS:
        return None
    return {"auth": auth, "csrf": csrf, "iat": iat, "epoch": epoch}


def current_epoch(db: Session) -> int:
    return db.scalars(select(AppState.session_epoch).where(AppState.id == 1)).first() or 0


def bump_epoch(db: Session) -> None:
    """Sign every session out."""
    updated = db.execute(
        update(AppState).where(AppState.id == 1).values(session_epoch=AppState.session_epoch + 1)
    )
    if updated.rowcount == 0:
        db.add(AppState(id=1, session_epoch=1))
    db.commit()


def password_matches(settings: Settings, given: str) -> bool:
    expected = settings.owner_password.encode()
    return bool(expected) and hmac.compare_digest(given.encode(), expected)


class LoginLimiter:
    """Login attempts per client in a sliding window. In memory: a restart resets it.

    An attempt is counted *before* the password is checked (under a lock, so parallel requests
    cannot all slip through) and cleared again on success. After `limit` attempts without a
    success the client gets 429 until the oldest one leaves the window.
    """

    def __init__(self, limit: int = MAX_FAILURES, window: float = FAILURE_WINDOW_SECONDS) -> None:
        self.limit = limit
        self.window = window
        self._attempts: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        for client in list(self._attempts):
            attempts = self._attempts[client]
            while attempts and now - attempts[0] > self.window:
                attempts.popleft()
            if not attempts:
                del self._attempts[client]

    def try_acquire(self, client: str, now: float | None = None) -> bool:
        """Count an attempt. False means the client is over the limit and must wait."""
        now = time.monotonic() if now is None else now
        with self._lock:
            self._prune(now)
            attempts = self._attempts.setdefault(client, deque())
            if len(attempts) >= self.limit:
                return False
            attempts.append(now)
            return True

    def reset(self, client: str) -> None:
        with self._lock:
            self._attempts.pop(client, None)

    def __len__(self) -> int:
        return len(self._attempts)


DEV_ENVS = ("development", "test")


def check_start(settings: Settings) -> None:
    """Fail closed: only a named dev environment may run without a password and secret.

    Any other `app_env` value (including a typo such as "prod") must have both set.
    """
    if settings.app_env in DEV_ENVS:
        return
    if settings.app_env != "production":
        raise RuntimeError(
            f"app_env must be production, development or test (got {settings.app_env!r})"
        )
    for name in ("owner_password", "session_secret"):
        if not getattr(settings, name):
            raise RuntimeError(f"{name} must be set before the app can start in production")


PUBLIC_PATHS = frozenset({"/login", "/privacy", "/terms"})
# The JSON API is owner-only except the two calls that start a session. Everything else under
# `/api/` needs a login, and every call that is not a read needs the CSRF header.
API_PREFIX = "/api/"
PUBLIC_API = frozenset({("GET", "/api/session"), ("POST", "/api/session")})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSRF_HEADER = "X-CSRF-Token"


def request_path(request: Request) -> str:
    """The path the client asked for. `request.url` is rebuilt from the Host header too, which a
    client controls; the ASGI scope's path is what the router matched."""
    return request.scope["path"]


def _csrf_matches(given: object, session: dict) -> bool:
    return isinstance(given, str) and hmac.compare_digest(given.encode(), session["csrf"].encode())


async def guard(request: Request, db: Session = Depends(get_session)) -> None:
    """App-wide check: owner pages need a login, and every POST needs a valid CSRF token.

    Pages send the token as the `csrf_token` form field. `/api/` calls send it in the
    `X-CSRF-Token` header (no body is read here) on every method except GET, HEAD and OPTIONS.
    """
    path = request_path(request)
    if path == "/health":
        return
    session = request.state.session
    if session["auth"] and session["epoch"] != current_epoch(db):
        session = request.state.session = new_session()  # revoked by a logout
        request.state.session_changed = True
    api = path.startswith(API_PREFIX)
    if api:
        public = (request.method, path) in PUBLIC_API
    else:
        # The landing page (logged-out "/") and the legal pages must be readable without a login:
        # Google's app review checks them. POSTs to them still need the CSRF token below.
        public = path in PUBLIC_PATHS or (path == "/" and request.method in ("GET", "HEAD"))
    if not public and not session["auth"]:
        raise NotLoggedIn
    if api:
        if request.method not in SAFE_METHODS:
            if not _csrf_matches(request.headers.get(CSRF_HEADER), session):
                raise CsrfRejected
    elif request.method == "POST":
        form = await request.form()
        if not _csrf_matches(form.get("csrf_token"), session):
            raise CsrfRejected
