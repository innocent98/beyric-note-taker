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
# Failed logins: per client address, and per account so that guessing spread over many addresses
# is capped too. Both are in-memory sliding windows.
MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 5 * 60
ACCOUNT_MAX_FAILURES = 10
ACCOUNT_FAILURE_WINDOW_SECONDS = 60 * 60
MAX_ACCOUNT_KEY = 254
MAX_LIMITER_KEYS = 50_000  # per window; see _Window
DEVICE_COOKIE_NAME = "device"
DEVICE_SECONDS = 90 * 24 * 3600


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


class _Window:
    """Timestamps of recent attempts per key; entries older than `window` seconds expire.

    Memory is bounded: expired keys are dropped as they are touched and by a sweep every
    `SWEEP_SECONDS`, and past `max_keys` the key idle longest is forgotten (a flood of distinct
    keys can then weaken the limit for the forgotten ones, but cannot grow memory or lock anyone
    out)."""

    SWEEP_SECONDS = 30.0

    def __init__(self, limit: int, window: float, max_keys: int = MAX_LIMITER_KEYS) -> None:
        self.limit = limit
        self.window = window
        self.max_keys = max_keys
        self.attempts: dict[str, deque[float]] = {}
        self._swept = float("-inf")

    def _trim(self, key: str, now: float) -> deque[float] | None:
        attempts = self.attempts.get(key)
        if attempts is None:
            return None
        while attempts and now - attempts[0] > self.window:
            attempts.popleft()
        if not attempts:
            del self.attempts[key]
            return None
        return attempts

    def sweep(self, now: float) -> None:
        if now - self._swept < self.SWEEP_SECONDS:
            return
        self._swept = now
        for key in list(self.attempts):
            self._trim(key, now)

    def full(self, key: str, now: float) -> bool:
        attempts = self._trim(key, now)
        return attempts is not None and len(attempts) >= self.limit

    def add(self, key: str, now: float) -> None:
        if key not in self.attempts and len(self.attempts) >= self.max_keys:
            del self.attempts[next(iter(self.attempts))]  # insertion order: the oldest key
        self.attempts.setdefault(key, deque()).append(now)

    def refund(self, key: str) -> None:
        attempts = self.attempts.get(key)
        if attempts:
            attempts.pop()
            if not attempts:
                del self.attempts[key]


class LoginLimiter:
    """Login attempts in sliding windows: `limit` per client address in `window` seconds, and
    `account_limit` per account in `account_window` seconds. In memory: a restart resets it.

    An attempt is counted *before* the password is checked (under a lock, so parallel requests
    cannot all slip through) and is only counted when neither limit is already full: a locked
    client that keeps hammering does not extend its wait, and an address over its limit does not
    spend the account's budget. On success `succeeded` gives this client's attempt back and
    clears the account's failures. It must not clear the client's other failures: a visitor with
    an account of their own could otherwise log in to it between guesses at someone else's.

    A request from a *known device* (it carries a valid device cookie, see `sign_device`) skips
    the account window: it is neither refused by it nor counted in it. Without that, anyone who
    can reach the login could keep the owner out of new logins with a few requests an hour. The
    address window still applies to it.

    Unknown accounts are limited exactly like known ones: the key is only the name given.
    """

    def __init__(
        self,
        limit: int = MAX_FAILURES,
        window: float = FAILURE_WINDOW_SECONDS,
        account_limit: int = ACCOUNT_MAX_FAILURES,
        account_window: float = ACCOUNT_FAILURE_WINDOW_SECONDS,
        max_keys: int = MAX_LIMITER_KEYS,
    ) -> None:
        self._clients = _Window(limit, window, max_keys)
        self._accounts = _Window(account_limit, account_window, max_keys)
        self._lock = threading.Lock()

    @staticmethod
    def _account_key(account: str) -> str:
        return account.strip().lower()[:MAX_ACCOUNT_KEY]

    def check(
        self, client: str, account: str, now: float | None = None, known_device: bool = False
    ) -> str | None:
        """Count an attempt. None means go ahead; otherwise "address" or "account": the limit
        that refused it (nothing is counted then)."""
        now = time.monotonic() if now is None else now
        account = self._account_key(account)
        with self._lock:
            self._clients.sweep(now)
            self._accounts.sweep(now)
            if self._clients.full(client, now):
                return "address"
            if not known_device and self._accounts.full(account, now):
                return "account"
            self._clients.add(client, now)
            if not known_device:
                self._accounts.add(account, now)
            return None

    def try_acquire(
        self, client: str, account: str, now: float | None = None, known_device: bool = False
    ) -> bool:
        return self.check(client, account, now, known_device) is None

    def succeeded(self, client: str, account: str) -> None:
        with self._lock:
            self._clients.refund(client)
            self._accounts.attempts.pop(self._account_key(account), None)

    def __len__(self) -> int:
        return len(self._clients.attempts) + len(self._accounts.attempts)


# ---- known-device cookie ----


def device_key(settings: Settings) -> bytes:
    """Key for device cookies. Derived like the session key but with its own label, so a device
    cookie is never a valid session cookie. A new owner password or session secret changes it,
    which retires every device cookie."""
    return hmac.new(
        settings.session_secret.encode(), b"device|" + settings.owner_password.encode(), "sha256"
    ).digest()


def sign_device(key: bytes, now: float | None = None) -> str:
    body = _b64(json.dumps({"dev": 1, "iat": int(now or time.time())}).encode())
    return f"{body}.{_signature(key, body)}"


def device_is_known(key: bytes, cookie: str | None, now: float | None = None) -> bool:
    """True for a device cookie this app signed, less than DEVICE_SECONDS old."""
    if not cookie or cookie.count(".") != 1:
        return False
    body, signature = cookie.split(".")
    if not hmac.compare_digest(signature.encode(), _signature(key, body).encode()):
        return False
    try:
        data = json.loads(_unb64(body))
        issued = data["iat"]
        known = data["dev"] == 1
    except (ValueError, KeyError, TypeError):
        return False
    if not (known and isinstance(issued, int) and not isinstance(issued, bool)):
        return False
    age = (now or time.time()) - issued
    return 0 <= age <= DEVICE_SECONDS


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
