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
because they belong to a calendar that is no longer connected, and revokes the old refresh token
at Google. Setting `last_sync_at` to null asks the sync loop for a first sync.

`disconnect` is the user's own "Disconnect Google" (spec 7.9, 12.2). Under a row lock it deletes
the user's events, every scheduled join in every state and the calendar-kind notifications, and
empties the connection row (status `disconnected`, no token, no Google identity), then COMMITS.
Only after the commit, with no transaction or lock open, does it revoke the refresh token at
Google (best effort) and run the `ON_DISCONNECT` hooks, so a slow Google never holds a database
connection or blocks a concurrent connect. Meetings, usage and settings stay.
"""

import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import calendar_providers, calendar_states, google_oauth, google_signin, tokens, vault
from app.config import Settings
from app.gcal_client import AccessTokenCache
from app.google_oauth import CALENDAR_SCOPE, MEET_SCOPE, GoogleError, GoogleOAuth
from app.models import (
    LIVE_JOIN_STATES,
    CalendarConnection,
    CalendarEvent,
    Notification,
    OAuthFlow,
    ScheduledJoin,
    User,
)
from app.vault import Secret

logger = logging.getLogger(__name__)

FLOW_TTL = timedelta(minutes=10)
FLOW_COOKIE = "calendar_flow"
FLOW_COOKIE_PATH = "/api/calendar/google"
DEFAULT_RETURN_TO = "/settings"
LOGIN_ERROR = "/login?error=google_failed"
TESTING_EXPIRY = timedelta(days=7)
LIVE_STATUSES = ("connected", "degraded")  # a connection that still works
MAX_EMAIL_CHARS = 254  # the `account_email` column
MAX_DOMAIN_CHARS = 253

# The features a connect can ask for, and the Google scope each one needs. A flow's `purpose` says
# which feature it was started for, and that feature's scope is the one the callback requires.
FEATURE_SCOPES = {"calendar": CALENDAR_SCOPE, "meet": MEET_SCOPE}
PURPOSES = {"calendar": "calendar", "meet": "meet_scope"}
REQUIRED_SCOPE = {"calendar": CALENDAR_SCOPE, "meet_scope": MEET_SCOPE}

# A notification whose `kind` starts with this belongs to the calendar and is deleted on
# disconnect (spec 12.2). Features that notify about calendar events or joins must use it.
CALENDAR_NOTIFICATION_PREFIX = "calendar."


@dataclass(frozen=True)
class ReleasedConnection:
    """What a disconnect (or the replacement of a dead connection) took out of the database, for
    the Google-side clean-up that runs after the commit: the old row's id and Meet subscription
    and the decrypted refresh token (a `Secret`: its repr is `<redacted>`). `token` is None when
    the row held none or could not be decrypted (`had_token` says which of the two situations)."""

    id: int
    user_id: int
    meet_subscription_name: str | None
    token: Secret | None
    had_token: bool


# Called after a disconnect, or after a dead connection was replaced by another Google account, as
# `hook(session, released, google)` with a `ReleasedConnection`. The deletion is already committed,
# no transaction or row lock is open, and `session` is a fresh transaction. A feature uses it to
# undo what it set up at Google (I3 deletes the Events subscription with `released.token`) or to
# drop memory (`register_token_cache`). `google` may be None. A hook that raises is logged by
# exception type only, its transaction is rolled back, and it never stops the others.
ON_DISCONNECT: list[Callable[[Session, ReleasedConnection, GoogleOAuth | None], None]] = []


def register_token_cache(cache: AccessTokenCache) -> None:
    """Make every disconnect drop `cache`'s access token for the connection. Whoever builds the
    process's `AccessTokenCache` (the sync loop) calls this once; the cache is per process."""
    ON_DISCONNECT.append(lambda session, released, google: cache.forget(released.id))


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
    """The features this connection can use, by its provider's rules (`calendar_providers`): for
    Google, `calendar` and/or `meet` from the scopes Google granted, exact strings only."""
    return calendar_providers.features_of(conn)


class AlreadyConnected(Exception):
    """The user already has another provider's calendar connected: one live calendar at a time
    (Outlook spec 6.3)."""


def other_live_calendar(session: Session, user_id: int, provider: str) -> CalendarConnection | None:
    """The user's live calendar of a provider other than `provider`, if any: a connection that
    still works or only needs reconnecting, and that gives the calendar feature. A Meet-only Google
    row and a `disconnected` row of any provider do not count."""
    rows = session.scalars(
        select(CalendarConnection).where(
            CalendarConnection.user_id == user_id,
            CalendarConnection.provider != provider,
            CalendarConnection.status.in_((*LIVE_STATUSES, "needs_reconnect")),
        )
    )
    return next((row for row in rows if "calendar" in granted_features(row)), None)


def live_calendar_connection(session: Session, user: User) -> CalendarConnection | None:
    """The user's calendar connection if it gives the calendar feature (one made only for Meet is
    not a connected calendar), else None. Only providers with a registry row are considered."""
    rows = session.scalars(
        select(CalendarConnection)
        .where(
            CalendarConnection.user_id == user.id,
            CalendarConnection.provider.in_(tuple(calendar_providers.PROVIDERS)),
            CalendarConnection.status != "disconnected",
        )
        .order_by(CalendarConnection.id)
    )
    return next((row for row in rows if "calendar" in granted_features(row)), None)


def _result(path: str, **query: str) -> str:
    """`path` with `query` added after any query it already has."""
    return path + ("&" if "?" in path else "?") + urlencode(query)


def _safe_return_to(value: str | None) -> str:
    """The Settings page unless `value` is a relative path with one leading slash (the
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
    `meet_scope`. Raises `ValueError` for no features or an unknown one, `AlreadyConnected` when
    asking for the calendar while another provider's calendar is connected, and
    `ConnectUnavailable` when no encryption key can be used."""
    wanted = list(dict.fromkeys(features))
    if not wanted or any(feature not in FEATURE_SCOPES for feature in wanted):
        raise ValueError("features must be calendar and/or meet")
    if "calendar" in wanted and other_live_calendar(session, user.id, "google") is not None:
        raise AlreadyConnected
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
            provider="google",
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


def _take_flow(
    session: Session, state: str | None, now: datetime, provider: str = "google"
) -> OAuthFlow | None:
    """Use up the calendar flow for `state`: its row, or None when unknown, used before, expired,
    not a calendar flow or started for another provider (a Microsoft state never finishes at the
    Google callback). A calendar flow's row is deleted whatever the answer, so a state can never
    be tried twice. Commits."""
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
    if flow is None or flow.provider != provider:
        return None
    return flow if flow.expires_at > now else None


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
    released: list[ReleasedConnection] = []
    problem = _store(session, user, grant, flow.purpose, settings, now, released)
    if problem is not None:
        session.rollback()
        return _failure(problem, back)
    for old in released:  # the old account's access, now that the new connection is committed
        _release_at_google(session, old, google)
    logger.info("calendar connected for user %s", user.id)
    return ConnectResult(_result(back, calendar="connected"))


def _store(
    session: Session,
    user: User,
    grant: google_oauth.Grant,
    purpose: str,
    settings: Settings,
    now: datetime,
    released: list[ReleasedConnection],
) -> str | None:
    """Write the connection. None on success, else the error code (nothing is committed then).
    An old account's connection that was replaced is appended to `released` for the caller to
    revoke after the commit."""
    identity = grant.identity
    email = identity.email.strip().lower()
    domain = identity.hosted_domain.strip().lower() if identity.hosted_domain else None
    if len(email) > MAX_EMAIL_CHARS or (domain and len(domain) > MAX_DOMAIN_CHARS):
        return "google_failed"
    # The user's own row is the lock for "one live calendar at a time": two tabs finishing two
    # providers at once queue here, and the second sees the first one's committed connection.
    session.execute(select(User.id).where(User.id == user.id).with_for_update())
    if purpose == "calendar" and other_live_calendar(session, user.id, "google") is not None:
        return "already_connected"
    conn = session.scalars(
        select(CalendarConnection)
        .where(CalendarConnection.user_id == user.id, CalendarConnection.provider == "google")
        .with_for_update()
    ).first()
    same_account = conn is not None and conn.account_subject == identity.sub
    live = conn is not None and conn.status in LIVE_STATUSES
    if live and not same_account and conn.account_subject is not None:
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
        conn.error_since = None
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
    elif not same_account and conn.account_subject is not None:
        released.append(_drop_old_account(session, conn))
    conn.account_email = email
    conn.account_subject = identity.sub
    conn.account_domain = domain
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
    session.flush()
    calendar_states.revive_on_reconnect(session, conn, now)
    return _commit(session)


def _commit(session: Session) -> str | None:
    try:
        session.commit()
    except IntegrityError:  # two connects for one user finishing at the same moment
        return "google_failed"
    return None


def _release(conn: CalendarConnection) -> ReleasedConnection:
    """Copy out of `conn`, in memory, what the Google-side clean-up needs, before the row is
    wiped or overwritten. An unreadable token (lost key) gives `token=None, had_token=True`."""
    token = None
    purpose = "ms_refresh_token" if conn.provider == "microsoft" else "refresh_token"
    if conn.refresh_token_enc is not None and conn.key_version is not None:
        try:
            token = vault.decrypt(
                conn.refresh_token_enc,
                conn.key_version,
                purpose=purpose,
                owner=conn.user_id,
            )
        except vault.VaultError:
            pass
    return ReleasedConnection(
        id=conn.id,
        user_id=conn.user_id,
        meet_subscription_name=conn.meet_subscription_name,
        token=token,
        had_token=conn.refresh_token_enc is not None,
    )


def _release_at_google(
    session: Session, released: ReleasedConnection, google: GoogleOAuth | None
) -> bool:
    """Undo, at Google, what a connection set up: revoke its refresh token and run the
    `ON_DISCONNECT` hooks. Call it only after the deletion is committed, with no transaction open
    (a slow Google must not hold a database connection or a lock). Best effort: every failure is
    logged without detail (the token, or a hook's message, could be in it) and the caller carries
    on, because the local deletion is what the user asked for and what the privacy policy
    promises. Returns False when a token existed and could not be revoked (the person should then
    remove the app at myaccount.google.com/permissions), else True. A crash between the commit and
    this call leaves the token live at Google with no copy here; that window is a few
    milliseconds and the same advice applies."""
    revoked = True
    if released.had_token:
        revoked = False
        if google is not None and released.token is not None:
            try:
                google.revoke(released.token)
                revoked = True
            except GoogleError:
                pass  # the client already logged the HTTP status or the error type
        if not revoked:
            logger.warning("google token for user %s was not revoked", released.user_id)
    run_disconnect_hooks(session, released, google)
    return revoked


def run_disconnect_hooks(
    session: Session, released: ReleasedConnection, google: GoogleOAuth | None
) -> None:
    """Run every `ON_DISCONNECT` hook for a released connection (any provider). A hook that
    raises is logged by exception type only, rolled back alone, and never stops the others."""
    for hook in list(ON_DISCONNECT):
        try:
            hook(session, released, google)
        except Exception as exc:
            session.rollback()  # a failed statement leaves the transaction unusable for the next
            logger.warning(
                "disconnect hook failed for user %s (%s)", released.user_id, type(exc).__name__
            )


def _forget_meet_subscription(conn: CalendarConnection) -> None:
    conn.meet_subscription_name = None
    conn.meet_subscription_expires_at = None
    conn.meet_subscription_state = "none"


def _drop_old_account(session: Session, conn: CalendarConnection) -> ReleasedConnection:
    """A dead connection is being replaced by a different Google account: the old account's
    events go, and so do the joins that had not started (a join already live keeps going), and
    the Meet subscription of the old account is forgotten. Returns what the caller must revoke
    at Google once the replacement is committed (the old token may still be alive there even
    though the connection died)."""
    released = _release(conn)
    _forget_meet_subscription(conn)
    session.execute(delete(CalendarEvent).where(CalendarEvent.connection_id == conn.id))
    session.execute(
        delete(ScheduledJoin).where(
            ScheduledJoin.user_id == conn.user_id,
            ScheduledJoin.source == "calendar",
            ScheduledJoin.state.not_in(LIVE_JOIN_STATES),
        )
    )
    return released


# ---- disconnecting ----


def disconnect(
    session: Session, user: User, google: GoogleOAuth | None, provider: str = "google"
) -> bool:
    """The user's own "Disconnect Google". Commits. Idempotent: with no connection, or one that
    is already disconnected, it only repeats the deletions (nothing is created).

    Order: lock the row, copy the token into memory, delete the user's events, every scheduled
    join in every state and the calendar-kind notifications, empty the row, COMMIT; only then
    revoke the token at Google and run the hooks (see `_release_at_google`). A running meeting
    is a `meetings` row and is never touched. Every statement is scoped to `user`: nobody
    else's rows are reachable. `google` is None when no Google client is configured; the local
    deletion still happens.

    Returns False when Google could not be asked to revoke a token that existed, so the person
    can be told to remove the app at myaccount.google.com/permissions; True otherwise (including
    when nothing was left to revoke: a repeat cannot re-check an earlier failure)."""
    conn = session.scalars(
        select(CalendarConnection)
        .where(CalendarConnection.user_id == user.id, CalendarConnection.provider == provider)
        .with_for_update()
    ).first()
    released = _release(conn) if conn is not None else None
    session.execute(delete(CalendarEvent).where(CalendarEvent.user_id == user.id))
    session.execute(delete(ScheduledJoin).where(ScheduledJoin.user_id == user.id))
    session.execute(
        delete(Notification).where(
            Notification.user_id == user.id,
            Notification.kind.startswith(CALENDAR_NOTIFICATION_PREFIX, autoescape=True),
        )
    )
    if conn is not None:
        conn.status = "disconnected"
        conn.needs_reconnect_reason = None
        conn.refresh_token_enc = None
        conn.key_version = None
        conn.granted_scopes = None
        conn.account_email = None
        conn.account_subject = None
        conn.account_domain = None
        conn.tenant_id = None
        conn.account_kind = None
        conn.tenant_domains = None
        conn.sync_token = None
        conn.last_sync_at = None
        conn.last_error = None
        conn.last_error_at = None
        conn.expected_expiry_at = None
        _forget_meet_subscription(conn)
    session.commit()
    if released is None:
        return True
    logger.info("calendar disconnected for user %s", user.id)
    if provider == "microsoft":
        # Microsoft has no revoke call an app can use for its own grant (spec 4.6): only the hooks
        # run, and a token that existed is reported as not revoked so the person is told where to
        # remove the app at Microsoft.
        run_disconnect_hooks(session, released, None)
        return not released.had_token
    return _release_at_google(session, released, google)
