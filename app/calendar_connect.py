"""Connecting a Google account's calendar (and, later, Meet): incremental consent, then an
encrypted refresh token (spec 7.2, 12.1).

`start_connect` writes the round trip's state to `oauth_flows` (only the hash of the state is kept;
the PKCE verifier and nonce are needed again at the callback) and returns Google's consent URL.
`finish_connect` uses the flow up exactly once and, only if every check passes, stores the
connection. The checks, in order, each of which leaves nothing stored:

1. the state is known, unused and unexpired, and belongs to a calendar flow (a sign-in flow's
   state is never accepted here, and the row is deleted whatever the answer);
2. someone is signed in, and it is the user the flow was started for (a victim lured to the
   callback with an attacker's code gets `wrong_account`);
3. the browser holds the `calendar_flow` cookie set when the flow began;
4. Google did not report an error (the person pressed Cancel);
5. the code exchanges (PKCE verifier and client secret) for an ID token whose nonce and audience
   check out;
6. the scope the feature needs is among the scopes Google SAYS it granted (the person can untick
   boxes on the consent screen; `granted_scopes` stores Google's list, never ours);
7. a refresh token came with it, or the old one still covers this same account and these scopes.

The refresh token is stored only through `app/vault.py` (`purpose="refresh_token"`, owner the
user id), so a ciphertext copied into another user's row opens for nobody. No code, token or state
is ever logged or put in a URL we build: results come back as a short code in the query string
(`?calendar=connected`, `?calendar_error=scope_missing`).

A reconnect updates the user's one Google row rather than adding another. A different Google
account than before also drops the old account's events and the joins that had not started,
because they belong to a calendar that is no longer connected. Setting `last_sync_at` to null asks
the sync loop for a first sync.
"""

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import google_oauth, google_signin, tokens, vault
from app.config import Settings
from app.google_oauth import CALENDAR_SCOPE, MEET_SCOPE, GoogleError, GoogleOAuth
from app.models import (
    LIVE_JOIN_STATES,
    CalendarConnection,
    CalendarEvent,
    OAuthFlow,
    ScheduledJoin,
    User,
)

logger = logging.getLogger(__name__)

FLOW_TTL = timedelta(minutes=10)
FLOW_COOKIE = "calendar_flow"
FLOW_COOKIE_PATH = "/api/calendar/google"
DEFAULT_RETURN_TO = "/settings/calendar"
LOGIN_ERROR = "/login?error=google_failed"
TESTING_EXPIRY = timedelta(days=7)
LIVE_STATUSES = ("connected", "degraded")  # a connection that still works
MAX_EMAIL_CHARS = 254  # the `google_account_email` column
MAX_DOMAIN_CHARS = 253

# The features a connect can ask for, and the Google scope each one needs. A flow's `purpose` says
# which feature it was started for, and that feature's scope is the one the callback requires.
FEATURE_SCOPES = {"calendar": CALENDAR_SCOPE, "meet": MEET_SCOPE}
PURPOSES = {"calendar": "calendar", "meet": "meet_scope"}
REQUIRED_SCOPE = {"calendar": CALENDAR_SCOPE, "meet_scope": MEET_SCOPE}


class ConnectUnavailable(Exception):
    """Connecting cannot work on this server (no encryption key): refused before Google is
    involved, so nobody consents to something we could not keep."""


@dataclass(frozen=True)
class ConnectResult:
    """Where to send the browser, and the error code if the connect failed. `redirect_to` is
    always a relative path: the stored `return_to`, or the login page."""

    redirect_to: str
    error: str | None = None


def granted_features(conn: CalendarConnection) -> set[str]:
    """The features this connection can use, from the scopes Google granted: `calendar` and/or
    `meet`. Exact scope strings only: a broader or look-alike scope is not ours to rely on."""
    scopes = set((conn.granted_scopes or "").split())
    return {feature for feature, scope in FEATURE_SCOPES.items() if scope in scopes}


def _result(path: str, **query: str) -> str:
    """`path` with `query` added after any query it already has."""
    return path + ("&" if "?" in path else "?") + urlencode(query)


def _safe_return_to(value: str | None) -> str:
    """The calendar settings page unless `value` is a relative path with one leading slash (the
    pattern sign-in uses: never `//host`, a scheme or a backslash)."""
    ok = value and google_signin.RETURN_TO_PATTERN.match(value)
    return value if ok else DEFAULT_RETURN_TO


def _failure(code: str, path: str = DEFAULT_RETURN_TO) -> ConnectResult:
    return ConnectResult(_result(path, calendar_error=code), code)


# ---- starting ----


def start_connect(
    session: Session,
    user: User,
    features: list[str],
    return_to: str | None,
    *,
    google: GoogleOAuth,
    now: datetime,
) -> tuple[str, str]:
    """Store a new flow for `user` and return (Google's consent URL, state). Commits.

    `features` are `calendar` and/or `meet`. The flow's purpose is `calendar` whenever the
    calendar is asked for (Meet then rides along and counts only if it is granted), else
    `meet_scope`. Raises `ValueError` for no features or an unknown one and `ConnectUnavailable`
    when no encryption key can be used."""
    wanted = list(dict.fromkeys(features))
    if not wanted or any(feature not in FEATURE_SCOPES for feature in wanted):
        raise ValueError("features must be calendar and/or meet")
    try:
        vault.encrypt("probe", purpose="refresh_token", owner=user.id)
    except vault.VaultError:
        raise ConnectUnavailable from None
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    verifier, challenge = google_oauth.new_pkce()
    session.add(
        OAuthFlow(
            state_hash=tokens.digest(state),
            purpose=PURPOSES["calendar" if "calendar" in wanted else "meet"],
            user_id=user.id,
            code_verifier=verifier,
            nonce=nonce,
            return_to=_safe_return_to(return_to),
            expires_at=now + FLOW_TTL,
        )
    )
    session.commit()
    url = google.connect_url(
        state=state,
        nonce=nonce,
        code_challenge=challenge,
        scopes=[FEATURE_SCOPES[feature] for feature in wanted],
        login_hint=user.email,
    )
    return url, state


def _take_flow(session: Session, state: str | None, now: datetime) -> OAuthFlow | None:
    """Use up the calendar flow for `state`: its row, or None when unknown, used before, expired
    or not a calendar flow. A calendar flow's row is deleted whatever the answer, so a state can
    never be tried twice. Commits."""
    if not state or len(state) > 200:
        return None
    flow = session.scalars(
        delete(OAuthFlow)
        .where(
            OAuthFlow.state_hash == tokens.digest(state),
            OAuthFlow.purpose.in_(tuple(REQUIRED_SCOPE)),
        )
        .returning(OAuthFlow)
    ).first()
    session.commit()
    return flow if flow is not None and flow.expires_at > now else None


# ---- finishing ----


def finish_connect(
    session: Session,
    user: User | None,
    *,
    state: str | None,
    code: str | None,
    google: GoogleOAuth,
    settings: Settings,
    now: datetime,
    flow_cookie: str | None = None,
    error: str | None = None,
) -> ConnectResult:
    """Check the callback and store the connection (see the module docstring for the order).
    `user` is whoever is signed in (None for nobody), `flow_cookie` the `calendar_flow` cookie the
    browser sent, `error` Google's own `error` query parameter. Commits on success only."""
    flow = _take_flow(session, state, now)
    if user is None:
        return ConnectResult(LOGIN_ERROR, "google_failed")
    if flow is None or state is None:
        return _failure("google_failed")
    if flow.user_id != user.id:
        return _failure("wrong_account")
    if not google_signin.flow_cookie_matches(flow_cookie, state):
        return _failure("google_failed")
    back = flow.return_to or DEFAULT_RETURN_TO
    if error is not None:
        return _failure("google_denied", back)
    if not code:
        return _failure("google_failed", back)
    try:
        grant = google.exchange_connect(
            code=code, code_verifier=flow.code_verifier, nonce=flow.nonce, now=now
        )
    except GoogleError:
        return _failure("google_failed", back)
    if REQUIRED_SCOPE[flow.purpose] not in grant.scopes:
        return _failure("scope_missing", back)
    problem = _store(session, user, grant, flow.purpose, settings, now)
    if problem is not None:
        session.rollback()
        return _failure(problem, back)
    logger.info("calendar connected for user %s", user.id)
    return ConnectResult(_result(back, calendar="connected"))


def _store(
    session: Session,
    user: User,
    grant: google_oauth.Grant,
    purpose: str,
    settings: Settings,
    now: datetime,
) -> str | None:
    """Write the connection. None on success, else the error code (nothing is committed then)."""
    identity = grant.identity
    email = identity.email.strip().lower()
    domain = identity.hosted_domain.strip().lower() if identity.hosted_domain else None
    if len(email) > MAX_EMAIL_CHARS or (domain and len(domain) > MAX_DOMAIN_CHARS):
        return "google_failed"
    conn = session.scalars(
        select(CalendarConnection)
        .where(CalendarConnection.user_id == user.id, CalendarConnection.provider == "google")
        .with_for_update()
    ).first()
    same_account = conn is not None and conn.google_sub == identity.sub
    live = conn is not None and conn.status in LIVE_STATUSES
    if live and not same_account and conn.google_sub is not None:
        # The chooser lets the person pick any Google account whatever the login hint says. A
        # working connection is never replaced by another account's: that would delete the first
        # account's events and joins and could drop its permissions. A switch needs the old
        # connection gone first (disconnect, C4) or dead (`needs_reconnect`).
        return "different_account"
    if grant.refresh_token is None:
        # Google omits it when it believes one was already issued. The old token is only a
        # stand-in when it is alive, belongs to this same Google account and already covers
        # what this connect asked for; anything else needs a new one.
        covered = (
            conn is not None
            and conn.refresh_token_enc is not None
            and conn.status != "needs_reconnect"
            and same_account
            and REQUIRED_SCOPE[purpose] in (conn.granted_scopes or "").split()
        )
        if not covered:
            return "no_refresh_token"
        # Same token, same account: record what Google granted now (the old scopes stay, the
        # new ones are added), clear the trouble flags, and release the row lock.
        conn.granted_scopes = " ".join(sorted(set(conn.granted_scopes.split()) | grant.scopes))
        conn.status = "connected"
        conn.needs_reconnect_reason = None
        conn.last_error = None
        conn.last_error_at = None
        # The token is the old one, so its age (and any 7-day expiry already noted) is unchanged.
        if not settings.google_oauth_testing:
            conn.expected_expiry_at = None
        elif conn.expected_expiry_at is None:
            conn.expected_expiry_at = (conn.connected_at or now) + TESTING_EXPIRY
        return _commit(session)
    try:
        blob, key_version = vault.encrypt(
            grant.refresh_token.reveal(), purpose="refresh_token", owner=user.id
        )
    except vault.VaultError:
        return "google_failed"
    if conn is None:
        conn = CalendarConnection(user_id=user.id, provider="google")
        session.add(conn)
    elif not same_account and conn.google_sub is not None:
        _drop_old_account(session, conn)
    conn.google_account_email = email
    conn.google_sub = identity.sub
    conn.google_hosted_domain = domain
    conn.status = "connected"
    conn.needs_reconnect_reason = None
    conn.granted_scopes = " ".join(sorted(grant.scopes))
    conn.refresh_token_enc = blob
    conn.key_version = key_version
    conn.connected_at = now
    conn.last_sync_at = None  # null asks the sync loop for a first sync
    conn.sync_token = None
    conn.last_error = None
    conn.last_error_at = None
    conn.expected_expiry_at = now + TESTING_EXPIRY if settings.google_oauth_testing else None
    return _commit(session)


def _commit(session: Session) -> str | None:
    try:
        session.commit()
    except IntegrityError:  # two connects for one user finishing at the same moment
        return "google_failed"
    return None


def _drop_old_account(session: Session, conn: CalendarConnection) -> None:
    """A dead connection is being replaced by a different Google account: the old account's
    events go, and so do the joins that had not started (a join already live keeps going), and
    the Meet subscription of the old account is forgotten. The old refresh token is overwritten
    unrevoked: it belonged to a connection that was already dead (revoking waits for C4)."""
    conn.meet_subscription_name = None
    conn.meet_subscription_expires_at = None
    conn.meet_subscription_state = "none"
    session.execute(delete(CalendarEvent).where(CalendarEvent.connection_id == conn.id))
    session.execute(
        delete(ScheduledJoin).where(
            ScheduledJoin.user_id == conn.user_id,
            ScheduledJoin.source == "calendar",
            ScheduledJoin.state.not_in(LIVE_JOIN_STATES),
        )
    )
