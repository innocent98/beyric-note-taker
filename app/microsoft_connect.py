"""Connecting a Microsoft (Outlook) calendar: the Microsoft side of `app/calendar_connect.py`
(Outlook spec 4.1, 4.4, 4.5, 6.3, 8.1).

`start_connect` writes the round trip's state to `oauth_flows` (provider `microsoft`; only the hash
of the state is kept, the PKCE verifier and nonce are needed again at the callback) and returns the
Microsoft consent URL. `finish_connect` uses the flow up exactly once and, only if every check
passes, stores the connection. The checks, in order, each of which leaves nothing stored:

1. the state is known, unused, unexpired, a calendar flow and started for **Microsoft** (a Google
   state never finishes here and a Microsoft state never finishes at the Google callback; the row
   is deleted whatever the answer);
2. someone is signed in, and it is the user the flow was started for (a victim lured to the
   callback with an attacker's code gets `wrong_account`);
3. the browser holds the `calendar_flow_ms` cookie set when the flow began;
4. Microsoft did not report an error (`access_denied` is `microsoft_denied`, any other
   `microsoft_failed`; `error_description` is never read);
5. the code redeems first thing (it lives about a minute) and the ID token checks out
   (`app/microsoft_oauth.py`: audience, nonce, `iss` equal to the `tid` it names, tenant rules);
6. `Calendars.Read` is among the scopes Microsoft SAYS were granted, and a refresh token came;
7. the probe: `GET /me/calendarView` for the next hour with the new access token. A mailbox-less
   account (404) is `no_mailbox`, a refused token (401, 403) `scope_missing`, anything else
   `microsoft_failed`. Nothing is stored for an account that could never sync.

Identity is the tenant id plus the object id, never an email. The address shown on Settings comes
from `GET /me` (`mail`, else `userPrincipalName`) and is a label only. The refresh token is stored
only through `app/vault.py` (`purpose="ms_refresh_token"`, owner the user id). Results come back as
a short code in the query string (`?calendar=connected`, `?calendar_error=no_mailbox`): no code,
token, state or Microsoft text is ever logged or put in a URL we build.

One calendar at a time (spec 6.3): the check runs after locking the user's own row, the same lock
the Google path takes, so two tabs finishing two providers queue and exactly one wins.

A reconnect updates the user's one Microsoft row. A **different** Microsoft identity while the old
connection still works is refused (`different_account`); over a dead one it drops the old
identity's events and the joins that had not started. Microsoft has no revoke call an app can use
for its own grant, so nothing is revoked at Microsoft (disconnect and replacement only forget it
locally; `calendar_connect.disconnect` says so).

`tenant_domains` (the organisation's verified domains, used only for the same-organisation test)
is **not read unless `MICROSOFT_ORGANIZER_DOMAIN_TRUST` is on**: an organiser address inside the
tenant's domain is only as trustworthy as Exchange Online's anti-spoofing, which the M0 run has
not confirmed (spec 8.1 #8), so by default it stays empty and an unanswered invite waits.
"""

import logging
import re
import secrets
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import (
    calendar_states,
    google_oauth,
    google_signin,
    graph_http,
    tokens,
    vault,
)
from app.calendar_connect import (
    LIVE_STATUSES,
    MAX_EMAIL_CHARS,
    PURPOSES,
    AlreadyConnected,
    ConnectResult,
    ConnectUnavailable,
    ReleasedConnection,
    _commit,
    _drop_old_account,
    _failure,
    _result,
    _safe_return_to,
    _take_flow,
    other_live_calendar,
    run_disconnect_hooks,
)
from app.calendar_source import TransientError
from app.config import Settings
from app.microsoft_oauth import (
    ClientMisconfigured,
    Grant,
    MicrosoftError,
    MicrosoftOAuth,
    TenantNotAllowed,
    has_calendar_scope,
)
from app.models import CalendarConnection, OAuthFlow, User
from app.vault import Secret

logger = logging.getLogger(__name__)

PROVIDER = "microsoft"
FLOW_TTL = timedelta(minutes=10)
FLOW_COOKIE = "calendar_flow_ms"
FLOW_COOKIE_PATH = "/api/calendar/microsoft"
LOGIN_ERROR = "/login?error=microsoft_failed"
# What the probe treats as "this account has no calendar to read" besides a plain 404. These
# strings are documented by experience, not by Microsoft's pages: the M0 run (S6) confirms them.
NO_MAILBOX_CODES = frozenset(
    {"MailboxNotEnabledForRESTAPI", "MailboxNotSupportedForRESTAPI", "MailboxNotFound"}
)
MAX_DOMAINS = 100
_HOST = re.compile(r"(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
_ERROR_CODE = re.compile(r"[A-Za-z0-9_]{1,80}")


def other_providers_calendar(session: Session, user: User) -> CalendarConnection | None:
    return other_live_calendar(session, user.id, PROVIDER)


# ---- starting ----


def start_connect(
    session: Session,
    user: User,
    return_to: str | None,
    *,
    microsoft: MicrosoftOAuth,
    now: datetime,
) -> tuple[str, str]:
    """Store a new flow for `user` and return (Microsoft's consent URL, state). Commits. Raises
    `AlreadyConnected` when another provider's calendar is connected (one calendar at a time) and
    `ConnectUnavailable` when no encryption key can be used (refused before Microsoft is
    involved, so nobody consents to something we could not keep)."""
    if other_providers_calendar(session, user) is not None:
        raise AlreadyConnected
    try:
        vault.encrypt("probe", purpose="ms_refresh_token", owner=user.id)
    except vault.VaultError:
        raise ConnectUnavailable from None
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    verifier, challenge = google_oauth.new_pkce()
    session.add(
        OAuthFlow(
            state_hash=tokens.digest(state),
            purpose=PURPOSES["calendar"],
            provider=PROVIDER,
            user_id=user.id,
            code_verifier=verifier,
            nonce=nonce,
            return_to=_safe_return_to(return_to),
            expires_at=now + FLOW_TTL,
        )
    )
    session.commit()
    return microsoft.authorize_url(state=state, nonce=nonce, code_challenge=challenge), state


# ---- the Graph calls of the callback ----


def _error_code(response: httpx.Response) -> str | None:
    """Graph's `error.code` when it is a plain identifier, else None."""
    try:
        code = response.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return None
    return code if isinstance(code, str) and _ERROR_CODE.fullmatch(code) else None


def probe_calendar(access_token: Secret, now: datetime, transport) -> str | None:
    """None when a calendar can be read, else the callback's error code (spec 4.5)."""
    try:
        response = graph_http.get(
            access_token.reveal(),
            graph_http.graph_url("/me/calendarView"),
            params={
                "startDateTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "endDateTime": (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "$top": "1",
            },
            transport=transport,
        )
    except TransientError:
        return "microsoft_failed"
    if response.status_code == 200:
        return None
    if response.status_code in (401, 403):
        return "scope_missing"
    if response.status_code == 404 or _error_code(response) in NO_MAILBOX_CODES:
        return "no_mailbox"
    logger.warning("microsoft calendar probe refused (HTTP %s)", response.status_code)
    return "microsoft_failed"


def _address(value: object) -> str | None:
    """A mail-like label (one `@`, no spaces, short), lower-cased, else None."""
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    if not value or value.count("@") != 1 or any(ch.isspace() for ch in value):
        return None
    return value if len(value) <= MAX_EMAIL_CHARS else None


def account_label(access_token: Secret, grant: Grant, transport) -> str:
    """The text shown as the connected account: `GET /me` `mail`, else `userPrincipalName`, else
    the ID token's own `preferred_username` or name. A label only: it decides nothing."""
    found = None
    try:
        response = graph_http.get(
            access_token.reveal(),
            graph_http.graph_url("/me"),
            params={"$select": "mail,userPrincipalName"},
            transport=transport,
        )
        if response.status_code == 200:
            body = response.json()
            if isinstance(body, dict):
                found = _address(body.get("mail")) or _address(body.get("userPrincipalName"))
    except (TransientError, ValueError):
        pass
    identity = grant.identity
    label = found or _address(identity.preferred_username) or identity.name or "Microsoft account"
    return label[:MAX_EMAIL_CHARS]


def tenant_domains(access_token: Secret, transport) -> str | None:
    """The organisation's verified domains from `GET /organization` (work accounts), as one
    space-separated text, or None when Graph would not say (ASSUMED to work with `User.Read`
    alone: the M0 run, S4, confirms it)."""
    try:
        response = graph_http.get(
            access_token.reveal(),
            graph_http.graph_url("/organization"),
            params={"$select": "verifiedDomains"},
            transport=transport,
        )
        if response.status_code != 200:
            return None
        organizations = response.json().get("value")
        domains = organizations[0].get("verifiedDomains")
    except (TransientError, ValueError, AttributeError, IndexError, KeyError, TypeError):
        return None
    if not isinstance(domains, list):
        return None
    names = {
        d["name"].strip().lower()
        for d in domains[:MAX_DOMAINS]
        if isinstance(d, dict) and isinstance(d.get("name"), str)
    }
    good = sorted(name for name in names if _HOST.fullmatch(name))
    return " ".join(good) or None


# ---- finishing ----


def finish_connect(
    session: Session,
    user: User | None,
    *,
    state: str | None,
    code: str | None,
    microsoft: MicrosoftOAuth,
    settings: Settings,
    now: datetime,
    flow_cookie: str | None = None,
    error: str | None = None,
) -> ConnectResult:
    """Check the callback and store the connection (see the module docstring for the order).
    `user` is whoever is signed in (None for nobody), `flow_cookie` the `calendar_flow_ms` cookie
    the browser sent, `error` Microsoft's own `error` query parameter. Commits on success only."""
    flow = _take_flow(session, state, now, PROVIDER)
    if user is None:
        return ConnectResult(LOGIN_ERROR, "microsoft_failed")
    if flow is None or state is None:
        return _failure("microsoft_failed")
    if flow.user_id != user.id:
        return _failure("wrong_account")
    if not google_signin.flow_cookie_matches(flow_cookie, state):
        return _failure("microsoft_failed")
    back = flow.return_to or "/settings"
    if error is not None:
        return _failure(
            "microsoft_denied" if error == "access_denied" else "microsoft_failed", back
        )
    if not code:
        return _failure("microsoft_failed", back)
    try:
        grant = microsoft.exchange_connect(
            code=code, code_verifier=flow.code_verifier, nonce=flow.nonce, now=now
        )
    except TenantNotAllowed:
        return _failure("tenant_not_allowed", back)
    except ClientMisconfigured:
        return _failure("microsoft_failed", back)
    except MicrosoftError:
        return _failure("microsoft_failed", back)
    if not has_calendar_scope(grant.scopes) or grant.refresh_token is None:
        # No refresh token means `offline_access` was not granted: the connection could not last.
        return _failure("scope_missing", back)
    transport = microsoft.transport
    problem = probe_calendar(grant.access_token, now, transport)
    if problem is not None:
        return _failure(problem, back)
    label = account_label(grant.access_token, grant, transport)
    domains = (
        tenant_domains(grant.access_token, transport)
        if grant.identity.account_kind == "work" and settings.microsoft_organizer_domain_trust
        else None
    )
    released: list[ReleasedConnection] = []
    problem = _store(session, user, grant, label, domains, now, released)
    if problem is not None:
        session.rollback()
        return _failure(problem, back)
    for old in released:  # the old identity's access: nothing to revoke at Microsoft, only hooks
        run_disconnect_hooks(session, old, None)
    logger.info("calendar connected for user %s (microsoft)", user.id)
    return ConnectResult(_result(back, calendar="connected"))


def _store(
    session: Session,
    user: User,
    grant: Grant,
    label: str,
    domains: str | None,
    now: datetime,
    released: list[ReleasedConnection],
) -> str | None:
    """Write the connection. None on success, else the error code (nothing is committed then)."""
    identity = grant.identity
    # The user's own row is the lock for "one live calendar at a time": two tabs finishing two
    # providers at once queue here, and the second sees the first one's committed connection.
    session.execute(select(User.id).where(User.id == user.id).with_for_update())
    if other_providers_calendar(session, user) is not None:
        return "already_connected"
    conn = session.scalars(
        select(CalendarConnection)
        .where(CalendarConnection.user_id == user.id, CalendarConnection.provider == PROVIDER)
        .with_for_update()
    ).first()
    same_account = (
        conn is not None
        and conn.account_subject == identity.subject
        and conn.tenant_id == identity.tenant_id
    )
    live = conn is not None and conn.status in LIVE_STATUSES
    if live and not same_account and conn.account_subject is not None:
        # A working connection is never replaced by another Microsoft identity's: that would delete
        # the first one's events and joins. A switch needs the old connection gone first
        # (disconnect) or dead (`needs_reconnect`).
        return "different_account"
    try:
        blob, key_version = vault.encrypt(
            grant.refresh_token.reveal(), purpose="ms_refresh_token", owner=user.id
        )
    except vault.VaultError:
        return "microsoft_failed"
    if conn is None:
        conn = CalendarConnection(user_id=user.id, provider=PROVIDER)
        session.add(conn)
    elif not same_account and conn.account_subject is not None:
        released.append(_drop_old_account(session, conn))
    conn.account_email = label
    conn.account_subject = identity.subject
    conn.account_domain = None
    conn.tenant_id = identity.tenant_id
    conn.account_kind = identity.account_kind
    conn.tenant_domains = domains
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
    conn.expected_expiry_at = None  # Microsoft has no testing-mode expiry
    session.flush()
    calendar_states.revive_on_reconnect(session, conn, now)
    return _commit(session, "microsoft_failed")
