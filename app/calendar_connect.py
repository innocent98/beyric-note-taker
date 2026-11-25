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

A connection belongs to ONE workspace (a team; workspaces design, section 4): a person holds at
most one connection per provider in each workspace and may hold the same provider, even the same
account, in several. The flow remembers the workspace it was started from (`oauth_flows.team_id`,
looked up by the unforgeable state, so the callback needs no header), files the connection there and
sends the browser back to it (`?team=<id>` unless that is the person's default workspace anyway).
The refusals below apply per workspace.

A reconnect updates the person's Google row IN THAT WORKSPACE rather than adding another. A
different Google account than before also drops the old account's events and the joins that had not
started, because they belong to a calendar that is no longer connected, and revokes the old refresh
token at Google. Setting `last_sync_at` to null asks the sync loop for a first sync.

`disconnect` is the user's own "Disconnect Google" for one workspace (spec 7.9, 12.2). Under a row
lock it deletes that connection's events, the scheduled joins filed in that workspace and its
calendar-kind notices, and empties the connection row (status `disconnected`, no token, no Google
identity), then COMMITS. Only after the commit, with no transaction or lock open, does it revoke the
refresh token at Google (best effort, and ONLY when no other connection of the person still uses the
same account: the grant is reference counted, workspaces design 12.3) and run the `ON_DISCONNECT`
hooks, so a slow Google never holds a database connection or blocks a concurrent connect. Meetings,
usage and settings stay.
"""

import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from urllib.parse import urlencode

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import (
    calendar_providers,
    calendar_states,
    google_oauth,
    google_signin,
    teams,
    tokens,
    vault,
)
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
    Team,
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
    # Another workspace of the person stands on the same provider account: the grant is not ours to
    # revoke (`grant_still_used`); the hooks of this connection still run.
    keep_grant: bool = False


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


def other_live_calendar(
    session: Session, user_id: int, provider: str, team_id: int
) -> CalendarConnection | None:
    """The user's live calendar IN THIS WORKSPACE of a provider other than `provider`, if any: a
    connection that still works or only needs reconnecting, and that gives the calendar feature. A
    Meet-only Google row and a `disconnected` row of any provider do not count. Another workspace's
    calendar never counts: Google in one and Outlook in another is allowed."""
    rows = session.scalars(
        select(CalendarConnection).where(
            CalendarConnection.user_id == user_id,
            CalendarConnection.team_id == team_id,
            CalendarConnection.provider != provider,
            CalendarConnection.status.in_((*LIVE_STATUSES, "needs_reconnect")),
        )
    )
    return next((row for row in rows if "calendar" in granted_features(row)), None)


def live_calendar_connection(
    session: Session, user: User, team_id: int
) -> CalendarConnection | None:
    """The user's calendar connection IN THIS WORKSPACE if it gives the calendar feature (one made
    only for Meet is not a connected calendar), else None. Only providers with a registry row are
    considered."""
    rows = session.scalars(
        select(CalendarConnection)
        .where(
            CalendarConnection.user_id == user.id,
            CalendarConnection.team_id == team_id,
            CalendarConnection.provider.in_(tuple(calendar_providers.PROVIDERS)),
            CalendarConnection.status != "disconnected",
        )
        .order_by(CalendarConnection.id)
    )
    return next((row for row in rows if "calendar" in granted_features(row)), None)


def default_workspace_id(session: Session, user: User) -> int:
    """The person's default workspace (`teams.default_membership`), a personal one made if they
    somehow have none: where a connect that names no workspace files its connection."""
    return teams.ensure_team(session, user).id


def _result(path: str, **query: str) -> str:
    """`path` with `query` added after any query it already has."""
    return path + ("&" if "?" in path else "?") + urlencode(query)


def landing_team(session: Session, user_id: int, team_id: int | None) -> dict[str, str]:
    """`team=<id>` for the page the browser returns to, unless that is the workspace the page picks
    on its own (the person's default one), so nothing about a one-workspace person changes."""
    if team_id is None:
        return {}
    default = teams.default_membership(session, user_id)
    return {} if default is not None and default.team_id == team_id else {"team": str(team_id)}


def _safe_return_to(value: str | None) -> str:
    """The Settings page unless `value` is a relative path with one leading slash (the
    pattern sign-in uses: never `//host`, a scheme or a backslash)."""
    ok = value and google_signin.RETURN_TO_PATTERN.match(value)
    return value if ok else DEFAULT_RETURN_TO


def _failure(
    code: str, path: str = DEFAULT_RETURN_TO, team: dict[str, str] | None = None
) -> ConnectResult:
    return ConnectResult(_result(path, calendar_error=code, **(team or {})), code)


# ---- starting ----


def start_connect(
    session: Session,
    user: User,
    features: list[str],
    return_to: str | None,
    *,
    google: GoogleOAuth,
    now: datetime,
    team_id: int | None = None,
) -> tuple[str, str]:
    """Store a new flow for `user` and return (Google's consent URL, state). Commits.

    `team_id` is the workspace the connection will belong to (the caller has checked the person is
    in it); none means their default workspace. `features` are `calendar` and/or `meet`. The flow's
    purpose is `calendar` whenever the calendar is asked for (Meet then rides along and counts only
    if it is granted), else `meet_scope`. Raises `ValueError` for no features or an unknown one,
    `AlreadyConnected` when asking for the calendar while another provider's calendar is connected
    in this workspace, and `ConnectUnavailable` when no encryption key can be used."""
    wanted = list(dict.fromkeys(features))
    if not wanted or any(feature not in FEATURE_SCOPES for feature in wanted):
        raise ValueError("features must be calendar and/or meet")
    team_id = default_workspace_id(session, user) if team_id is None else team_id
    if (
        "calendar" in wanted
        and other_live_calendar(session, user.id, "google", team_id) is not None
    ):
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
            team_id=team_id,
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
    # The workspace the connect was started from; a flow begun before the column existed has none
    # and files into the person's default workspace.
    team_id = flow.team_id if flow.team_id is not None else default_workspace_id(session, user)
    team = landing_team(session, user.id, team_id)
    if error is not None:
        return _failure("google_denied", back, team)
    if not code:
        return _failure("google_failed", back, team)
    if teams.membership(session, user.id, team_id) is None:
        # They left (or were removed from) the workspace while the consent screen was open: nothing
        # is exchanged and nothing is filed in a workspace they are not in.
        return _failure("workspace_unavailable", back)
    try:
        grant = google.exchange_connect(
            code=code, code_verifier=flow.code_verifier, nonce=flow.nonce, now=now
        )
    except GoogleError:
        return _failure("google_failed", back, team)
    if REQUIRED_SCOPE[flow.purpose] not in grant.scopes:
        return _failure("scope_missing", back, team)
    released: list[ReleasedConnection] = []
    problem = _store(session, user, team_id, grant, flow.purpose, settings, now, released)
    if problem is not None:
        session.rollback()
        return _failure(problem, back, team)
    for old in released:  # the old account's access, now that the new connection is committed
        _release_at_google(session, old, google)
    logger.info("calendar connected for user %s", user.id)
    return ConnectResult(_result(back, calendar="connected", **team))


def _store(
    session: Session,
    user: User,
    team_id: int,
    grant: google_oauth.Grant,
    purpose: str,
    settings: Settings,
    now: datetime,
    released: list[ReleasedConnection],
) -> str | None:
    """Write the connection of `user` in workspace `team_id`. None on success, else the error code
    (nothing is committed then). An old account's connection that was replaced is appended to
    `released` for the caller to revoke after the commit."""
    identity = grant.identity
    email = identity.email.strip().lower()
    domain = identity.hosted_domain.strip().lower() if identity.hosted_domain else None
    if len(email) > MAX_EMAIL_CHARS or (domain and len(domain) > MAX_DOMAIN_CHARS):
        return "google_failed"
    # The user's own row is the lock for "one live calendar at a time in a workspace": two tabs
    # finishing two providers at once queue here, and the second sees the first one's committed
    # connection.
    session.execute(select(User.id).where(User.id == user.id).with_for_update())
    # After the user's row (the order every other writer takes them in; the foreign key of a
    # competing insert shares the team row, so an exclusive lock here would deadlock with it): the
    # workspace is locked SHARED, which waits for a `teams.leave` in flight (it locks the team
    # exclusively), and the membership is read again. A leave that committed while the code was
    # being exchanged must not leave a connection in a workspace the person is no longer in.
    session.execute(select(Team.id).where(Team.id == team_id).with_for_update(read=True))
    if teams.membership(session, user.id, team_id) is None:
        return "workspace_unavailable"
    if (
        purpose == "calendar"
        and other_live_calendar(session, user.id, "google", team_id) is not None
    ):
        return "already_connected"
    conn = session.scalars(
        select(CalendarConnection)
        .where(
            CalendarConnection.user_id == user.id,
            CalendarConnection.team_id == team_id,
            CalendarConnection.provider == "google",
        )
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
        conn = CalendarConnection(user_id=user.id, team_id=team_id, provider="google")
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


def _commit(session: Session, failure: str = "google_failed") -> str | None:
    """Commit; the error code to redirect with (the provider's own) if two connects for one user
    finish at the same moment."""
    try:
        session.commit()
    except IntegrityError:
        return failure
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
    if released.keep_grant:
        run_disconnect_hooks(session, released, None)
        return True
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
    released = replace(_release(conn), keep_grant=grant_still_used(session, conn))
    _forget_meet_subscription(conn)
    own_events = select(CalendarEvent.id).where(CalendarEvent.connection_id == conn.id)
    # Only this connection's joins: another provider's connection keeps its own. A join with no
    # event (its event row aged out) cannot be told apart from another connection's of the same
    # workspace, so it goes only when no other calendar is live in this workspace (a join of
    # another workspace is never this connection's).
    mine = ScheduledJoin.calendar_event_id.in_(own_events)
    if other_live_calendar(session, conn.user_id, conn.provider, conn.team_id) is None:
        mine = or_(
            mine,
            (ScheduledJoin.calendar_event_id.is_(None)) & (ScheduledJoin.team_id == conn.team_id),
        )
    session.execute(
        delete(ScheduledJoin).where(
            ScheduledJoin.user_id == conn.user_id,
            ScheduledJoin.source == "calendar",
            ScheduledJoin.state.not_in(LIVE_JOIN_STATES),
            mine,
        )
    )
    session.execute(delete(CalendarEvent).where(CalendarEvent.connection_id == conn.id))
    return released


# ---- disconnecting ----


def grant_still_used(session: Session, conn: CalendarConnection) -> bool:
    """Whether another live connection of the same person uses the same provider account as
    `conn` (in another workspace): disconnecting `conn` then must not revoke the provider's grant,
    because revoking one token takes the whole grant of that account with it (workspaces design
    12.3: the grant is reference counted). A connection that is `disconnected`, holds no token or
    names no account (a fresh row) counts for nothing. Call it BEFORE `conn` is emptied."""
    if conn.account_subject is None:
        return False
    other = session.scalars(
        select(CalendarConnection.id).where(
            CalendarConnection.user_id == conn.user_id,
            CalendarConnection.provider == conn.provider,
            CalendarConnection.id != conn.id,
            CalendarConnection.status != "disconnected",
            CalendarConnection.refresh_token_enc.is_not(None),
            CalendarConnection.account_subject == conn.account_subject,
            CalendarConnection.tenant_id.is_not_distinct_from(conn.tenant_id),
        )
    ).first()
    return other is not None


def disconnect(
    session: Session,
    user: User,
    google: GoogleOAuth | None,
    provider: str = "google",
    team_id: int | None = None,
) -> bool:
    """The user's own "Disconnect Google" in one workspace (`team_id`; none means their default
    workspace). Commits. Idempotent: with no connection there, or one that is already disconnected,
    it only repeats the deletions (nothing is created).

    Order: lock the row, copy the token into memory, delete the connection's events, every
    scheduled join filed in this workspace in every state and this workspace's calendar-kind
    notices, empty the row, COMMIT; only then revoke the token at Google and run the hooks (see
    `_release_at_google`). A running meeting is a `meetings` row and is never touched. Every
    statement is scoped to `user` AND the workspace: nobody else's rows, and none of the person's
    other workspaces, are reachable. `google` is None when no Google client is configured; the local
    deletion still happens.

    The grant at the provider is revoked only when no other connection of the person uses the same
    account (`grant_still_used`); then `revoked` is True without a call, because nothing is left to
    remove until the last workspace using the account lets go.

    Returns False when Google could not be asked to revoke a token that existed, so the person
    can be told to remove the app at myaccount.google.com/permissions; True otherwise (including
    when nothing was left to revoke: a repeat cannot re-check an earlier failure)."""
    if team_id is None:
        team_id = default_workspace_id(session, user)
    conn = session.scalars(
        select(CalendarConnection)
        .where(
            CalendarConnection.user_id == user.id,
            CalendarConnection.team_id == team_id,
            CalendarConnection.provider == provider,
        )
        .with_for_update()
    ).first()
    released = _release(conn) if conn is not None else None
    keep_grant = conn is not None and grant_still_used(session, conn)
    # With another calendar of the user still live in this workspace (Outlook while Google is being
    # disconnected for Meet, or a disconnect aimed at a provider that is not the connected one),
    # only THIS connection's events and their joins go: the other calendar's rows, joins and
    # notices stay, or its auto-join would silently stop while its row still says `connected`.
    other_live = other_live_calendar(session, user.id, provider, team_id) is not None
    if conn is not None:
        own_events = select(CalendarEvent.id).where(CalendarEvent.connection_id == conn.id)
        if other_live:
            session.execute(
                delete(ScheduledJoin).where(
                    ScheduledJoin.user_id == user.id,
                    ScheduledJoin.calendar_event_id.in_(own_events),
                )
            )
        session.execute(delete(CalendarEvent).where(CalendarEvent.connection_id == conn.id))
    if not other_live:
        session.execute(
            delete(ScheduledJoin).where(
                ScheduledJoin.user_id == user.id, ScheduledJoin.team_id == team_id
            )
        )
        session.execute(
            delete(Notification).where(
                Notification.user_id == user.id,
                Notification.team_id == team_id,
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
    if keep_grant:
        # Another workspace still stands on this account: its grant is not ours to revoke yet. The
        # hooks still run (they undo what THIS connection set up, such as its Meet subscription).
        run_disconnect_hooks(session, released, None)
        return True
    if provider == "microsoft":
        # Microsoft has no revoke call an app can use for its own grant (spec 4.6): only the hooks
        # run, and a token that existed is reported as not revoked so the person is told where to
        # remove the app at Microsoft.
        run_disconnect_hooks(session, released, None)
        return not released.had_token
    return _release_at_google(session, released, google)


# ---- leaving a workspace ----

# `session.info` key: what `detach_workspace` released and the route must settle after its commit.
RELEASED_KEY = "calendar_released"


def detach_workspace(session: Session, user_id: int, team_id: int) -> None:
    """The person leaves workspace `team_id` (or is removed from it): THEIR calendar connection
    there is deleted with its events, the scheduled joins filed there are removed in every state
    (a meeting already recorded or running is a `meetings` row and stays), and the calendar notices
    of that workspace go. Their connections in other workspaces are untouched. In the caller's
    transaction, no commit; the provider-side clean-up (the grant revoked only when no other
    connection of theirs uses the account, and the `ON_DISCONNECT` hooks) is parked on
    `session.info` for `settle_released`, which the route calls after it has committed."""
    conns = list(
        session.scalars(
            select(CalendarConnection)
            .where(CalendarConnection.user_id == user_id, CalendarConnection.team_id == team_id)
            .with_for_update()
        )
    )
    parked = session.info.setdefault(RELEASED_KEY, [])
    for conn in conns:
        parked.append((_release(conn), grant_still_used(session, conn), conn.provider))
        session.execute(delete(CalendarEvent).where(CalendarEvent.connection_id == conn.id))
    session.execute(
        delete(ScheduledJoin).where(
            ScheduledJoin.user_id == user_id, ScheduledJoin.team_id == team_id
        )
    )
    session.execute(
        delete(Notification).where(
            Notification.user_id == user_id,
            Notification.team_id == team_id,
            Notification.kind.startswith(CALENDAR_NOTIFICATION_PREFIX, autoescape=True),
        )
    )
    for conn in conns:
        session.delete(conn)
    session.flush()


def settle_released(session: Session, google: GoogleOAuth | None) -> None:
    """After the commit of a leave or removal: for each connection `detach_workspace` released,
    revoke the provider's grant (Google only; Microsoft has no revoke) unless another connection of
    the person still uses the account, and run the hooks. Best effort, as `disconnect`: the local
    deletion is what was asked for. A no-op when nothing was parked."""
    for released, keep_grant, provider in session.info.pop(RELEASED_KEY, []):
        if keep_grant or provider == "microsoft":
            run_disconnect_hooks(session, released, None)
        else:
            _release_at_google(session, released, google)
