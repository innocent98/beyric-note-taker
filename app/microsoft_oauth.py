"""The Microsoft OAuth client for the Outlook calendar connect (Outlook spec section 4): the
authorization-code flow with PKCE against the v2.0 endpoints, the checks on the ID token, and the
refresh call whose answer carries a NEW refresh token every time.

`MicrosoftOAuth` builds the consent URL, redeems the code (`exchange_connect`) and refreshes an
access token (`refresh`). The flow's state, PKCE verifier and nonce live in `oauth_flows`
(`app/calendar_connect.py`, M3); this module only does the Microsoft round trips.

Tokens. Everything secret comes back as a `vault.Secret` (its repr is `<redacted>`): the access
token, the refresh token. The access token is never decoded (Microsoft says not to read tokens of
an API you do not own, and personal-account tokens may be encrypted), never stored, never logged.
The client secret goes only into the form body of a server-side POST. No exception message, log
line or repr holds a token, a code, a URL or Microsoft's `error_description` (it carries free text,
a trace id and a correlation id): failures carry an HTTP status, the short `error` string when it
looks like one, and the numeric `error_codes`.

ID token checks (spec 4.4). The token is the one in the answer to the code redemption: it came
straight from Microsoft's token endpoint over TLS, in reply to a request that carried our client
secret and the PKCE verifier. OpenID Connect Core 3.1.3.7 lets a client rely on that channel
instead of verifying the signature, and Microsoft's documentation says confidential clients may
use it that way, so no signature is checked (no JWT library). **This decision is recorded for the
review and must be revisited if Microsoft sign-in is ever added** (spec 8.4). Everything else is
checked: audience, expiry, issued-at, nonce, a tenant id that is a GUID, an issuer that is exactly
`https://login.microsoftonline.com/{tid}/v2.0` for THAT tenant id, an `oid` (else `sub`) and, when
the server lists allowed tenants (or only serves personal accounts), the tenant. A token that
arrives any other way must never be passed to `exchange_connect`. The `id_token` in a refresh
answer is never read.

Identity is the tenant id plus the object id, never an email, `preferred_username` or UPN (they are
mutable and can be controlled by tenant admins or users). `name` and `preferred_username` are
labels only.
"""

import base64
import json
import logging
import re
import secrets
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

import httpx
from fastapi import Request

from app import calendar_http
from app.calendar_source import AuthRevoked, TransientError
from app.config import Settings
from app.vault import Secret

logger = logging.getLogger(__name__)

LOGIN_BASE = "https://login.microsoftonline.com"
CALLBACK_PATH = "/api/calendar/microsoft/callback"
GRAPH_SCOPE_PREFIX = "https://graph.microsoft.com/"
# What the connect asks for (spec 4.2). Nothing else: a new scope later is a new consent for every
# user. `openid` and `profile` only give the ID token its `tid` and `oid`; they expose no data.
SCOPES = (
    "openid",
    "profile",
    "offline_access",
    GRAPH_SCOPE_PREFIX + "User.Read",
    GRAPH_SCOPE_PREFIX + "Calendars.Read",
)
CALENDAR_SCOPE = "Calendars.Read"
# The personal-account tenant: `consumers` is a nickname for it, and a personal sign-in's ID token
# has this `tid` (spec 3.1).
CONSUMER_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"
AUTHORITIES = ("consumers", "common")
CLOCK_SKEW_SECONDS = 60
MAX_REFRESH_TOKEN_CHARS = 8192
MAX_ACCESS_TOKEN_CHARS = 16384
MAX_SUBJECT_CHARS = 64  # the `account_subject` column; an `oid` is a 36-character GUID
DEFAULT_LIFETIME = timedelta(hours=1)
# AADSTS error numbers read from `error_codes` (documented; the M0 run confirms the real answers).
CODE_REFRESH_TOKEN_IDLE = 700082  # the refresh token expired from inactivity
CODE_SECRET_EXPIRED = 7000222  # our client secret has expired
SECRET_REMINDER_DAYS = 30

_GUID = re.compile(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}")
_ERROR_NAME = re.compile(r"[a-z_]{1,64}")


class MicrosoftError(Exception):
    """The Microsoft round trip failed or the token did not check out. Carries no detail: the
    reason could hold the code, a token or an address. The callback shows `microsoft_failed`."""


class TenantNotAllowed(MicrosoftError):
    """The token is genuine but from a tenant this server does not accept. The callback shows
    `tenant_not_allowed`."""


class ClientMisconfigured(MicrosoftError):
    """Microsoft refused OUR client when redeeming a code (an expired or wrong client secret):
    the person did nothing wrong. The callback shows `microsoft_failed`; `secret_expired` says
    Microsoft named the expiry (AADSTS7000222), which the caller turns into an admin alert."""

    def __init__(self, *, secret_expired: bool = False) -> None:
        super().__init__()
        self.secret_expired = secret_expired


class ClientRejected(TransientError):
    """Microsoft refused OUR client (an expired or wrong client secret). Reconnecting would not
    help the user, so it is transient for the connection; the operator must act. `secret_expired`
    says Microsoft named the expiry (AADSTS7000222), which the caller turns into an admin alert."""

    def __init__(self, message: str, *, secret_expired: bool = False) -> None:
        super().__init__(message)
        self.secret_expired = secret_expired


@dataclass(frozen=True)
class Identity:
    """Who the Microsoft account is. `tenant_id` and `subject` (the `oid`, else the `sub`) are the
    key; `name` and `preferred_username` are display labels and decide nothing."""

    tenant_id: str
    subject: str
    account_kind: str  # personal or work
    # Labels only, and an address is personal data: kept out of `repr`.
    name: str | None = field(default=None, repr=False)
    preferred_username: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class Grant:
    """What the code redemption gave. `scopes` is what Microsoft SAYS was granted (the person may
    untick boxes, and a work tenant may narrow them), never what was asked. `refresh_token` is None
    when Microsoft did not issue one. The access token is held for the one call that needs it
    (the probe, spec 4.5) and is never stored."""

    identity: Identity
    scopes: frozenset[str]
    refresh_token: Secret | None
    access_token: Secret
    access_lifetime: timedelta


@dataclass(frozen=True)
class Refreshed:
    """The answer to a refresh. `refresh_token` is the NEW token (Microsoft rotates on every use)
    or None if none came; the caller saves it with a compare-and-swap write and discards the old
    one. `scopes` is what the refreshed access token covers."""

    access_token: Secret
    lifetime: timedelta
    refresh_token: Secret | None
    scopes: frozenset[str]


def enabled(settings: Settings) -> bool:
    """The client exists only when both halves of it are configured."""
    return bool(settings.microsoft_client_id and settings.microsoft_client_secret)


def calendar_enabled(settings: Settings) -> bool:
    """Whether the Microsoft calendar is switched on for this server: the calendar features, the
    Microsoft flag and a full OAuth client. The registry row's `enabled` is this."""
    return bool(
        settings.calendar_enabled and settings.microsoft_calendar_enabled and enabled(settings)
    )


def redirect_uri(settings: Settings) -> str:
    """The one redirect URI we send, exactly the string registered in Azure (HTTPS, case-sensitive,
    no trailing slash)."""
    return settings.public_base_url.rstrip("/") + CALLBACK_PATH


def normalize_scope(scope: str) -> str:
    """A scope as Microsoft may print it (`https://graph.microsoft.com/Calendars.Read` or
    `Calendars.Read`), compared case-insensitively. `Calendars.ReadBasic` stays another scope."""
    scope = scope.strip()
    if scope.lower().startswith(GRAPH_SCOPE_PREFIX):
        scope = scope[len(GRAPH_SCOPE_PREFIX) :]
    return scope.lower()


def has_calendar_scope(scopes: "frozenset[str] | set[str] | str | None") -> bool:
    """True when `Calendars.Read` is among the scopes (a space separated string or a set)."""
    if not scopes:
        return False
    parts = scopes.split() if isinstance(scopes, str) else scopes
    return any(normalize_scope(part) == CALENDAR_SCOPE.lower() for part in parts)


def secret_expiry(settings: Settings) -> date | None:
    """The date our client secret expires (`MICROSOFT_SECRET_EXPIRES_ON`), or None when unset."""
    value = settings.microsoft_secret_expires_on
    return date.fromisoformat(value) if value else None


def secret_expiry_due(settings: Settings, today: date) -> bool:
    """True from 30 days before the secret expires (and after), the reminder window."""
    expires = secret_expiry(settings)
    return expires is not None and (expires - today).days <= SECRET_REMINDER_DAYS


def check_settings(settings: Settings) -> None:
    """Refuse to start in production on a half-configured Microsoft client (spec 9.2): an id with
    no secret, or the flag on with no client. The missing-key rule lives in `vault.check_settings`.
    Outside production nothing is refused: development runs without the client."""
    if settings.app_env != "production":
        return
    if settings.microsoft_client_id and not settings.microsoft_client_secret:
        raise RuntimeError("MICROSOFT_CLIENT_ID is set but MICROSOFT_CLIENT_SECRET is not")
    if settings.microsoft_calendar_enabled and not enabled(settings):
        raise RuntimeError(
            "MICROSOFT_CALENDAR_ENABLED is true but MICROSOFT_CLIENT_ID and "
            "MICROSOFT_CLIENT_SECRET are not both set"
        )


def _claims(id_token: str) -> dict:
    parts = id_token.split(".")
    if len(parts) != 3:
        raise MicrosoftError
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except ValueError:
        raise MicrosoftError from None
    if not isinstance(claims, dict):
        raise MicrosoftError
    return claims


def _error_info(response: httpx.Response) -> tuple[str | None, list[int]]:
    """The short `error` string and the numeric `error_codes` of a token-endpoint error. Both are
    filtered to what is safe to log; `error_description` is never read."""
    try:
        body = response.json()
    except ValueError:
        return None, []
    if not isinstance(body, dict):
        return None, []
    error = body.get("error")
    error = error if isinstance(error, str) and _ERROR_NAME.fullmatch(error) else None
    raw_codes = body.get("error_codes")
    codes = (
        [c for c in raw_codes[:20] if isinstance(c, int) and not isinstance(c, bool)]
        if isinstance(raw_codes, list)
        else []
    )
    return error, codes


def _lifetime(body: dict) -> timedelta:
    expires_in = body.get("expires_in")
    if isinstance(expires_in, str) and expires_in.isdigit():
        expires_in = int(expires_in)
    usable = isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool)
    return timedelta(seconds=expires_in) if usable and expires_in > 0 else DEFAULT_LIFETIME


def _token(body: dict, name: str, limit: int) -> Secret | None:
    """A token field as a `Secret`: None when absent, `MicrosoftError` when malformed."""
    value = body.get(name)
    if value is None:
        return None
    if not (isinstance(value, str) and 0 < len(value) <= limit):
        raise MicrosoftError
    return Secret(value)


class MicrosoftOAuth:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    def __repr__(self) -> str:
        return "<MicrosoftOAuth>"  # the settings hold the client secret

    @property
    def transport(self) -> httpx.BaseTransport | None:
        """The HTTP transport this client was built with (None in production; a test's fake)."""
        return self._transport

    def _authority_url(self, authority: str, path: str) -> str:
        if authority not in AUTHORITIES and not _GUID.fullmatch(authority):
            raise ValueError("not a Microsoft authority")
        return f"{LOGIN_BASE}/{authority}/oauth2/v2.0/{path}"

    def authorize_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        """The consent URL (spec 4.1): the `consumers` or `common` endpoint from the settings,
        the code flow with PKCE (S256), the account chooser every time, no `login_hint` (the
        person's Beyric address is often not their Outlook one)."""
        query = urlencode(
            {
                "client_id": self._settings.microsoft_client_id,
                "response_type": "code",
                "redirect_uri": redirect_uri(self._settings),
                "response_mode": "query",
                "scope": " ".join(SCOPES),
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )
        return f"{self._authority_url(self._settings.microsoft_authority, 'authorize')}?{query}"

    # ---- the token endpoint ----

    def _post_token(self, authority: str, form: dict[str, str]) -> httpx.Response:
        with calendar_http.client(self._transport) as http:
            return calendar_http.send(
                http,
                "Microsoft",
                "POST",
                self._authority_url(authority, "token"),
                data={
                    "client_id": self._settings.microsoft_client_id,
                    "client_secret": self._settings.microsoft_client_secret,
                    "scope": " ".join(SCOPES),
                    **form,
                },
            )

    def exchange_connect(
        self, *, code: str, code_verifier: str, nonce: str, now: datetime
    ) -> Grant:
        """Trade the authorization code (valid for about a minute: call this first thing) for the
        account's identity, the scopes Microsoft granted, the access token (for the probe) and the
        refresh token (if one was issued), or raise `MicrosoftError` / `TenantNotAllowed`. The
        ID token is checked here; the redemption goes to the same authority path as the consent."""
        try:
            response = self._post_token(
                self._settings.microsoft_authority,
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri(self._settings),
                    "code_verifier": code_verifier,
                },
            )
        except TransientError as exc:
            logger.warning("microsoft token exchange failed (%s)", exc)
            raise MicrosoftError from None
        if response.status_code != 200:
            error, codes = _error_info(response)
            logger.warning(
                "microsoft token exchange refused (HTTP %s, %s, codes %s)",
                response.status_code,
                error,
                codes,
            )
            if error == "invalid_client":
                raise ClientMisconfigured(secret_expired=CODE_SECRET_EXPIRED in codes)
            raise MicrosoftError
        try:
            body = calendar_http.json_object(response, "Microsoft")
        except TransientError:
            logger.warning("microsoft token exchange answer was not a JSON object")
            raise MicrosoftError from None
        id_token = body.get("id_token")
        scope = body.get("scope")
        if not isinstance(id_token, str) or not isinstance(scope, str):
            logger.warning("microsoft token answer had no id token or scope list")
            raise MicrosoftError
        if str(body.get("token_type", "")).lower() != "bearer":
            logger.warning("microsoft token answer was not a bearer token")
            raise MicrosoftError
        try:
            access = _token(body, "access_token", MAX_ACCESS_TOKEN_CHARS)
            refresh = _token(body, "refresh_token", MAX_REFRESH_TOKEN_CHARS)
        except MicrosoftError:
            logger.warning("microsoft token answer had a malformed token")
            raise
        if access is None:
            logger.warning("microsoft token answer had no access token")
            raise MicrosoftError
        identity = self._check_id_token(id_token, nonce=nonce, now=now)
        return Grant(
            identity=identity,
            scopes=frozenset(scope.split()),
            refresh_token=refresh,
            access_token=access,
            access_lifetime=_lifetime(body),
        )

    def refresh(self, refresh_token: Secret, *, tenant_id: str) -> Refreshed:
        """Turn a stored refresh token into an access token (spec 4.3). Uses the connection's own
        tenant endpoint, never `common`. The answer has a NEW refresh token, which the caller
        must save (compare-and-swap) and use from then on; this method does not touch the
        database. The `id_token` in the answer is never read.

        Raises `AuthRevoked` (`invalid_grant`, `interaction_required`, `consent_required`: only a
        reconnect helps; `expired=True` when Microsoft says the token idled out, code 700082),
        `ClientRejected` (our own secret is expired or wrong) or `TransientError` (everything
        else: 429, 5xx, `temporarily_unavailable`, a malformed answer). Messages carry the status,
        the `error` string and the numeric codes only."""
        tenant = tenant_id.strip().lower()
        if not _GUID.fullmatch(tenant):
            raise ValueError("the tenant id must be a GUID")  # a bug, never `common`
        response = self._post_token(
            tenant, {"grant_type": "refresh_token", "refresh_token": refresh_token.reveal()}
        )
        if response.status_code != 200:
            raise self._refresh_failure(response)
        body = calendar_http.json_object(response, "Microsoft")
        try:
            access = _token(body, "access_token", MAX_ACCESS_TOKEN_CHARS)
            new_refresh = _token(body, "refresh_token", MAX_REFRESH_TOKEN_CHARS)
        except MicrosoftError:
            raise TransientError("the Microsoft token endpoint sent a malformed token") from None
        if access is None:
            raise TransientError("the Microsoft token endpoint sent no access token")
        scope = body.get("scope")
        scopes = frozenset(scope.split()) if isinstance(scope, str) else frozenset()
        return Refreshed(
            access_token=access,
            lifetime=_lifetime(body),
            refresh_token=new_refresh,
            scopes=scopes,
        )

    @staticmethod
    def _refresh_failure(response: httpx.Response) -> Exception:
        status = response.status_code
        error, codes = _error_info(response)
        detail = f"HTTP {status}, error {error}, codes {codes}"
        if status in (400, 401) and error in (
            "invalid_grant",
            "interaction_required",
            "consent_required",
        ):
            return AuthRevoked(
                f"Microsoft no longer honours the refresh token ({detail})",
                expired=CODE_REFRESH_TOKEN_IDLE in codes,
            )
        if error == "invalid_client":
            # Our own client secret is expired (7000222) or wrong: nothing the user can fix.
            return ClientRejected(
                f"Microsoft refused our client ({detail})",
                secret_expired=CODE_SECRET_EXPIRED in codes,
            )
        # `temporarily_unavailable`, 429, 5xx and anything unexpected: try again later.
        return TransientError(f"the Microsoft token endpoint answered ({detail})")

    # ---- the ID token ----

    def _check_id_token(self, id_token: str, *, nonce: str, now: datetime) -> Identity:
        """Check the ID token from the code redemption (module docstring) and return who it is,
        or raise `MicrosoftError` / `TenantNotAllowed`."""
        claims = _claims(id_token)
        issued, expires = claims.get("iat"), claims.get("exp")
        stamps = all(isinstance(v, int) and not isinstance(v, bool) for v in (issued, expires))
        raw_tid = claims.get("tid")
        tid = raw_tid.lower() if isinstance(raw_tid, str) else ""
        subject = self._subject(claims)
        seen_nonce = claims.get("nonce")
        checks = (
            claims.get("aud") == self._settings.microsoft_client_id,
            stamps and expires > now.timestamp() - CLOCK_SKEW_SECONDS,
            stamps and issued < now.timestamp() + CLOCK_SKEW_SECONDS,
            bool(nonce)  # an empty expected nonce can never match: fail closed
            and isinstance(seen_nonce, str)
            and secrets.compare_digest(seen_nonce.encode(), nonce.encode()),
            bool(_GUID.fullmatch(tid)),
            # The chain of trust of a tenant-independent endpoint: the issuer must be the
            # tenant the token itself names.
            isinstance(raw_tid, str) and claims.get("iss") == f"{LOGIN_BASE}/{raw_tid}/v2.0",
            subject is not None,
        )
        if not all(checks):
            logger.warning("microsoft id token refused (check %s failed)", checks.index(False) + 1)
            raise MicrosoftError
        allowed = self._settings.microsoft_allowed_tenant_ids
        only_personal = self._settings.microsoft_authority == "consumers"
        if (allowed and tid not in allowed) or (only_personal and tid != CONSUMER_TENANT):
            # Never log the tenant id of someone else's organisation.
            logger.warning("microsoft id token refused (tenant not allowed)")
            raise TenantNotAllowed
        name, username = claims.get("name"), claims.get("preferred_username")
        return Identity(
            tenant_id=tid,
            subject=subject,
            account_kind="personal" if tid == CONSUMER_TENANT else "work",
            name=name if isinstance(name, str) else None,
            preferred_username=username if isinstance(username, str) else None,
        )

    @staticmethod
    def _subject(claims: dict) -> str | None:
        """The stable object id: `oid`, else `sub` (spec 4.4; the M0 run confirms which a personal
        account carries). Never an email."""
        for key in ("oid", "sub"):
            value = claims.get(key)
            if isinstance(value, str) and 0 < len(value) <= MAX_SUBJECT_CHARS:
                return value
        return None


def get_microsoft(request: Request) -> MicrosoftOAuth | None:
    """The Microsoft client for this app, or None when it is not configured. A dependency, so
    tests swap in a fake. The `MICROSOFT_CALENDAR_ENABLED` flag is checked by the routes."""
    settings = request.app.state.settings
    return MicrosoftOAuth(settings) if enabled(settings) else None
