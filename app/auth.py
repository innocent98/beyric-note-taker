"""Accounts: a signed session cookie, a CSRF token for every POST, and failed-login limits.

The session cookie holds `{"uid": int | None, "csrf": token, "iat": seconds, "epoch": n,
"gepoch": n}` signed with HMAC-SHA256. The signing key is derived from `session_secret` alone, so
changing it signs everyone out. `guard` loads the user (`uid`) on every request and drops the
session unless the user is active, `epoch` equals `users.session_epoch` (logout and a password
change bump it: that user only) and `gepoch` equals `app_state.session_epoch` (bumped to sign
everyone out). Anonymous visitors get a session too (so the login form has a CSRF token), with
`uid` null; logging in issues a fresh one, so a cookie from before login is never promoted.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time

from fastapi import Depends, Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import vault
from app.config import Settings
from app.db import get_session
from app.models import AppState, User
from app.ratelimit import MAX_LIMITER_KEYS, Gate, MemoryStore, Store

COOKIE_NAME = "session"
SESSION_SECONDS = 30 * 24 * 3600
# Failed logins: per client address, and per account so that guessing spread over many addresses
# is capped too. Sliding windows kept in the database (`app/ratelimit.py`).
MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 5 * 60
ACCOUNT_MAX_FAILURES = 10
ACCOUNT_FAILURE_WINDOW_SECONDS = 60 * 60
MAX_ACCOUNT_KEY = 254
DEVICE_COOKIE_NAME = "device"
HOST_DEVICE_COOKIE_NAME = "__Host-device"
DEVICE_SECONDS = 90 * 24 * 3600
DEVICE_MAX_FAILURES = 10  # per device cookie, so a stolen one still has a cap
DEVICE_FAILURE_WINDOW_SECONDS = 60 * 60
MAX_DEVICE_ID = 64


def device_cookie_name(settings: Settings) -> str:
    """`__Host-device` when cookies are Secure (the browser then also insists on Path=/ and no
    Domain, so no sibling subdomain can plant one); the plain name over http, so local tests and
    a dev server still work."""
    return HOST_DEVICE_COOKIE_NAME if settings.cookie_secure else DEVICE_COOKIE_NAME


class NotLoggedIn(Exception):
    pass


class CsrfRejected(Exception):
    pass


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def signing_key(settings: Settings) -> bytes:
    """Key for session cookies: from `session_secret` only. No password is part of it (passwords
    are per user now; a password change bumps that user's `session_epoch` instead)."""
    return hmac.new(settings.session_secret.encode(), b"session", "sha256").digest()


def _signature(key: bytes, body: str) -> str:
    return _b64(hmac.new(key, body.encode(), hashlib.sha256).digest())


def new_session(
    *, uid: int | None = None, epoch: int = 0, gepoch: int = 0, now: float | None = None
) -> dict:
    return {
        "uid": uid,
        "csrf": secrets.token_urlsafe(32),
        "iat": int(now or time.time()),
        "epoch": epoch,
        "gepoch": gepoch,
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
        uid, csrf, iat = data["uid"], data["csrf"], data["iat"]
        epoch, gepoch = data["epoch"], data["gepoch"]
    except (ValueError, KeyError, TypeError):
        return None
    # bool is an int in Python; a cookie holding true where a number belongs is not ours.
    if not (isinstance(csrf, str) and all(_is_int(n) for n in (iat, epoch, gepoch))):
        return None
    if not (uid is None or (_is_int(uid) and uid > 0)):
        return None
    if (now or time.time()) - iat > SESSION_SECONDS:
        return None
    return {"uid": uid, "csrf": csrf, "iat": iat, "epoch": epoch, "gepoch": gepoch}


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def current_epoch(db: Session) -> int:
    return db.scalars(select(AppState.session_epoch).where(AppState.id == 1)).first() or 0


def bump_user_epoch(db: Session, user_id: int) -> None:
    """Sign one user out everywhere: logout, and a password change."""
    db.execute(update(User).where(User.id == user_id).values(session_epoch=User.session_epoch + 1))
    db.commit()


def bump_epoch(db: Session) -> None:
    """Sign every session out."""
    updated = db.execute(
        update(AppState).where(AppState.id == 1).values(session_epoch=AppState.session_epoch + 1)
    )
    if updated.rowcount == 0:
        db.add(AppState(id=1, session_epoch=1))
    db.commit()


def account_key(account: str) -> str:
    """How an account name is compared: trimmed, lower-cased, capped."""
    return account.strip().lower()[:MAX_ACCOUNT_KEY]


class LoginLimiter:
    """Login attempts in sliding windows: `limit` per client address in `window` seconds, and
    `account_limit` per account in `account_window` seconds. The counters live in a `Store`
    (`app/ratelimit.py`): in the database for the app, so a restart or a second process does not
    reset them.

    An attempt is counted *before* the password is checked (under a lock, so parallel requests
    cannot all slip through) and is only counted when no limit is already full: a locked client
    that keeps hammering does not extend its wait, and an address over its limit does not spend
    the account's budget. On success `succeeded` gives this client's attempt back; it must not
    clear the client's other failures (a visitor with an account of their own could otherwise log
    in to it between guesses at someone else's).

    A request from a *known device* (it carries a valid device cookie, see `sign_device`; the
    caller passes the cookie's random `device` id) skips the account window: it is neither
    refused by it nor counted in it. Without that, anyone who can reach the login could keep the
    owner out of new logins with a few requests an hour. It is still held to the address window,
    and to a window of its own per device id, so a stolen cookie keeps a cap. A known-device
    success gives back its own attempts and does NOT clear the account's failures: those belong
    to whoever was guessing.

    Unknown accounts are limited exactly like known ones: the key is only the name given.
    """

    def __init__(
        self,
        limit: int = MAX_FAILURES,
        window: float = FAILURE_WINDOW_SECONDS,
        account_limit: int = ACCOUNT_MAX_FAILURES,
        account_window: float = ACCOUNT_FAILURE_WINDOW_SECONDS,
        max_keys: int = MAX_LIMITER_KEYS,
        device_limit: int = DEVICE_MAX_FAILURES,
        device_window: float = DEVICE_FAILURE_WINDOW_SECONDS,
        store: Store | None = None,
    ) -> None:
        # The app passes a `DbStore`; with none (unit tests) the counters are in this process.
        self._store: Store = store if store is not None else MemoryStore(max_keys)
        self._limit, self._window = limit, window
        self._account_limit, self._account_window = account_limit, account_window
        self._device_limit, self._device_window = device_limit, device_window

    @staticmethod
    def _account_key(account: str) -> str:
        return account_key(account)

    def _client(self, client: str) -> Gate:
        return Gate("address", "login_client", client, self._limit, self._window)

    def _account(self, account: str) -> Gate:
        key = self._account_key(account)
        return Gate("account", "login_account", key, self._account_limit, self._account_window)

    def _device(self, device: str) -> Gate:
        return Gate("device", "login_device", device, self._device_limit, self._device_window)

    def check(
        self, client: str, account: str, now: float | None = None, device: str | None = None
    ) -> str | None:
        """Count an attempt. None means go ahead; otherwise "address", "device" or "account":
        the limit that refused it (nothing is counted then)."""
        second = self._device(device) if device is not None else self._account(account)
        return self._store.acquire([self._client(client), second], now)

    def try_acquire(
        self, client: str, account: str, now: float | None = None, device: str | None = None
    ) -> bool:
        return self.check(client, account, now, device) is None

    def succeeded(self, client: str, account: str, device: str | None = None) -> None:
        self._store.refund("login_client", client)
        if device is not None:
            self._store.refund("login_device", device)
        else:
            self._store.clear("login_account", self._account_key(account))

    def refund(self, client: str, account: str, device: str | None = None) -> None:
        """Give back only this attempt (a login that never got to check the password, because
        the server was busy). Unlike `succeeded` it leaves the account's other failures alone."""
        self._store.refund("login_client", client)
        if device is not None:
            self._store.refund("login_device", device)
        else:
            self._store.refund("login_account", self._account_key(account))

    def unlock(self, account: str) -> int:
        """Forget the failed logins counted against this account (a successful password reset, or
        the operator's script): the lockout ends. Other accounts, addresses and devices keep
        theirs. Returns how many hits were forgotten."""
        return self._store.clear("login_account", self._account_key(account))

    def __len__(self) -> int:
        return self._store.size()


# ---- known-device cookie ----


def device_key(settings: Settings) -> bytes:
    """Key for device cookies. Derived like the session key but with its own label, so a device
    cookie is never a valid session cookie. A new session secret changes it, which retires every
    device cookie."""
    return hmac.new(settings.session_secret.encode(), b"device", "sha256").digest()


def sign_device(
    key: bytes, account: str, now: float | None = None, device_id: str | None = None
) -> str:
    """A device cookie for one account: it only helps when logging in to that account, so one
    user's cookie cannot be used to skip the account window of another user's. It carries a
    random device id (kept when a known device logs in again) that the limiter counts failures
    on, so a stolen cookie is capped too."""
    payload = {
        "dev": 1,
        "acct": account_key(account),
        "did": device_id or secrets.token_urlsafe(16),
        "iat": int(now or time.time()),
    }
    body = _b64(json.dumps(payload).encode())
    return f"{body}.{_signature(key, body)}"


def device_id(key: bytes, cookie: str | None, account: str, now: float | None = None) -> str | None:
    """The device id in a device cookie this app signed for this account, less than
    DEVICE_SECONDS old; None for anything else."""
    if not cookie or cookie.count(".") != 1:
        return None
    body, signature = cookie.split(".")
    if not hmac.compare_digest(signature.encode(), _signature(key, body).encode()):
        return None
    try:
        data = json.loads(_unb64(body))
        issued, known, bound_to, did = data["iat"], data["dev"] == 1, data["acct"], data["did"]
    except (ValueError, KeyError, TypeError):
        return None
    if not (known and isinstance(issued, int) and not isinstance(issued, bool)):
        return None
    if not (isinstance(did, str) and 0 < len(did) <= MAX_DEVICE_ID):
        return None
    if not (
        isinstance(bound_to, str)
        and hmac.compare_digest(bound_to.encode(), account_key(account).encode())
    ):
        return None
    age = (now or time.time()) - issued
    return did if 0 <= age <= DEVICE_SECONDS else None


KNOWN_ENVS = ("development", "test", "production")
MIN_SESSION_SECRET = 32


def check_start(settings: Settings) -> None:
    """Fail closed. `app_env` must be production, development or test (a typo such as "prod"
    does not start). Outside `test`, `session_secret` must be at least MIN_SESSION_SECRET
    characters: it is the only key behind every session cookie, so an empty or short one lets
    anyone forge an admin session, in development as much as in production."""
    if settings.app_env not in KNOWN_ENVS:
        raise RuntimeError(
            f"app_env must be production, development or test (got {settings.app_env!r})"
        )
    if settings.app_env != "test" and len(settings.session_secret) < MIN_SESSION_SECRET:
        raise RuntimeError(
            f"session_secret must be at least {MIN_SESSION_SECRET} characters "
            f"(generate one with: openssl rand -hex 48); it signs every session cookie"
        )
    vault.check_settings(settings)


PUBLIC_PATHS = frozenset({"/login", "/privacy", "/terms"})
# The JSON API needs a login except these exact public routes: the two calls that start a
# session, and the account routes a visitor needs before they have one (sign-up, email
# verification, password reset, the waitlist, Google sign-in). Everything else under `/api/`
# needs a login, and every call that is not a read still sends the CSRF header, public or not.
API_PREFIX = "/api/"
PUBLIC_API = frozenset(
    {
        ("GET", "/api/session"),
        ("POST", "/api/session"),
        ("GET", "/api/pilot"),
        ("POST", "/api/signup"),
        ("POST", "/api/signup/resend"),
        ("POST", "/api/verify-email"),
        ("POST", "/api/password/forgot"),
        ("POST", "/api/password/reset"),
        ("POST", "/api/waitlist"),
        # What a team invitation link says (who invited you, to which address), for the page that
        # asks a visitor to sign in or sign up: the 256-bit token in the query is the credential.
        ("GET", "/api/team/invites/preview"),
        ("GET", "/api/auth/google/start"),
        ("GET", "/api/auth/google/callback"),
        # Answers a signed-out visitor itself (a redirect, not JSON). The flow is bound to the
        # signed-in user that started it and to the browser's `calendar_flow` cookie.
        ("GET", "/api/calendar/google/callback"),
    }
)
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSRF_HEADER = "X-CSRF-Token"


def request_path(request: Request) -> str:
    """The path the client asked for. `request.url` is rebuilt from the Host header too, which a
    client controls; the ASGI scope's path is what the router matched."""
    return request.scope["path"]


def _csrf_matches(given: object, session: dict) -> bool:
    return isinstance(given, str) and hmac.compare_digest(given.encode(), session["csrf"].encode())


def load_user(db: Session, session: dict) -> User | None:
    """The active user a session cookie belongs to, or None when it is anonymous or revoked:
    no such user, not active (suspended or deleted), signed out since (`epoch`), or everyone was
    signed out since (`gepoch`)."""
    uid = session["uid"]
    if uid is None:
        return None
    user = db.get(User, uid)
    if user is None or user.status != "active" or session["epoch"] != user.session_epoch:
        return None
    return user if session["gepoch"] == current_epoch(db) else None


def current_user(request: Request) -> User:
    """The signed-in user of this request. Every route that is not public takes this, so it is
    the only way any service learns who is calling."""
    user = getattr(request.state, "user", None)
    if user is None:
        raise NotLoggedIn
    return user


async def guard(request: Request, db: Session = Depends(get_session)) -> None:
    """App-wide check: pages need a login, and every POST needs a valid CSRF token.

    Pages send the token as the `csrf_token` form field. `/api/` calls send it in the
    `X-CSRF-Token` header (no body is read here) on every method except GET, HEAD and OPTIONS.
    Sets `request.state.user` (None for a visitor).
    """
    path = request_path(request)
    request.state.user = None
    if path == "/health":
        return
    session = request.state.session
    request.state.user = load_user(db, session)
    if session["uid"] is not None and request.state.user is None:
        session = request.state.session = new_session()  # revoked, suspended, or signed out
        request.state.session_changed = True
    api = path.startswith(API_PREFIX)
    if api:
        public = (request.method, path) in PUBLIC_API
    else:
        # The landing page (logged-out "/") and the legal pages must be readable without a login:
        # Google's app review checks them. POSTs to them still need the CSRF token below.
        public = path in PUBLIC_PATHS or (path == "/" and request.method in ("GET", "HEAD"))
    if not public and request.state.user is None:
        raise NotLoggedIn
    if api:
        if request.method not in SAFE_METHODS:
            if not _csrf_matches(request.headers.get(CSRF_HEADER), session):
                raise CsrfRejected
    elif request.method == "POST":
        form = await request.form()
        if not _csrf_matches(form.get("csrf_token"), session):
            raise CsrfRejected
