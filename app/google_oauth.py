"""Sign in with Google: the authorization-code flow with PKCE, for the scopes `openid email
profile` only (non-sensitive: Google shows no "unverified app" warning for them).

`GoogleOAuth` builds the consent URL and exchanges the returned code for the person's identity.
The flow's state, PKCE verifier and nonce live in `oauth_flows` (`app/google_signin.py`).

The calendar connect flow (`app/calendar_connect.py`) shares this client: `connect_url` asks for
offline access to the calendar (or Meet) scope, and `exchange_connect` returns the refresh token
and the scopes Google says were granted, with the same ID-token checks.

ID-token checks. The token is received straight from Google's token endpoint over TLS, in answer
to a request that carried our client secret and PKCE verifier. OpenID Connect Core 3.1.3.7 lets a
client rely on that TLS channel instead of verifying the token's signature, and Google's own
guidance says the same for tokens obtained this way, so no signature is checked (and no JWT
library is needed). Everything else is: issuer, audience, expiry, issued-at, nonce, a subject, and
a boolean `email_verified` that is true. A token that arrives any other way must never be passed
to `exchange`.
"""

import base64
import hashlib
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlencode

import httpx
from fastapi import Request

from app.config import Settings
from app.vault import Secret

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"  # fixed: no part of it comes from a request
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
SCOPES = "openid email profile"
CALLBACK_PATH = "/api/auth/google/callback"
CALENDAR_CALLBACK_PATH = "/api/calendar/google/callback"
# The only paths ever sent to Google as a redirect URI; both are registered in the Google console.
ALLOWED_CALLBACK_PATHS = (CALLBACK_PATH, CALENDAR_CALLBACK_PATH)
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"
MEET_SCOPE = "https://www.googleapis.com/auth/meetings.space.readonly"
MAX_REFRESH_TOKEN_CHARS = 4096
TIMEOUT_SECONDS = 15.0
CLOCK_SKEW_SECONDS = 60
MAX_SUB_CHARS = 64  # the `users.google_sub` column; Google's real ones are about 21 digits


class GoogleError(Exception):
    """The Google round trip failed or the token did not check out. Carries no detail: the
    reason could hold the code, the token or the address."""


@dataclass(frozen=True)
class Identity:
    sub: str
    email: str
    name: str | None
    # The ID token's `hd` claim: set for a Workspace account, absent for a personal Gmail one.
    hosted_domain: str | None = None


@dataclass(frozen=True)
class Grant:
    """What the token endpoint gave for a calendar connect. `scopes` is what Google SAYS was
    granted (the person may have unticked boxes), never what was asked. `refresh_token` is None
    when Google did not issue one. `Secret` keeps it out of any repr or log line."""

    identity: Identity
    scopes: frozenset[str]
    refresh_token: Secret | None


def enabled(settings: Settings) -> bool:
    """Sign in with Google exists only when both halves of the OAuth client are configured."""
    return bool(settings.google_client_id and settings.google_client_secret)


def redirect_uri(settings: Settings, path: str = CALLBACK_PATH) -> str:
    """The redirect URI for one of the allow-listed callback paths (nothing else is accepted)."""
    if path not in ALLOWED_CALLBACK_PATHS:
        raise ValueError("not an allow-listed callback path")
    return settings.public_base_url.rstrip("/") + path


def new_pkce() -> tuple[str, str]:
    """A PKCE verifier and its S256 challenge."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _claims(id_token: str) -> dict:
    parts = id_token.split(".")
    if len(parts) != 3:
        raise GoogleError
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except ValueError:
        raise GoogleError from None
    if not isinstance(claims, dict):
        raise GoogleError
    return claims


class GoogleOAuth:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    def authorize_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        query = urlencode(
            {
                "client_id": self._settings.google_client_id,
                "redirect_uri": redirect_uri(self._settings),
                "response_type": "code",
                "scope": SCOPES,
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )
        return f"{AUTH_URL}?{query}"

    def connect_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
        scopes: list[str],
        login_hint: str,
    ) -> str:
        """The consent URL for connecting a calendar: offline access, a consent screen every time
        (so a refresh token is always issued), the scopes already granted carried along, the
        person's own address as the hint, and the identity scopes needed to record which Google
        account was connected."""
        query = urlencode(
            {
                "client_id": self._settings.google_client_id,
                "redirect_uri": redirect_uri(self._settings, CALENDAR_CALLBACK_PATH),
                "response_type": "code",
                "scope": " ".join(["openid", "email", *scopes]),
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true",
                "login_hint": login_hint,
            }
        )
        return f"{AUTH_URL}?{query}"

    def _token_request(self, *, code: str, code_verifier: str, callback_path: str) -> dict:
        """Post the authorization code to the token endpoint; the JSON answer or `GoogleError`."""
        try:
            with httpx.Client(timeout=TIMEOUT_SECONDS, transport=self._transport) as client:
                response = client.post(
                    TOKEN_URL,
                    data={
                        "code": code,
                        "client_id": self._settings.google_client_id,
                        "client_secret": self._settings.google_client_secret,
                        "redirect_uri": redirect_uri(self._settings, callback_path),
                        "grant_type": "authorization_code",
                        "code_verifier": code_verifier,
                    },
                )
            body = response.json() if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("google token exchange failed (%s)", type(exc).__name__)
            raise GoogleError from None
        if not isinstance(body, dict) or not isinstance(body.get("id_token"), str):
            logger.warning("google token exchange refused (HTTP %s)", response.status_code)
            raise GoogleError
        return body

    def exchange(self, *, code: str, code_verifier: str, nonce: str, now: datetime) -> Identity:
        """Trade the authorization code for the person's identity, or raise `GoogleError`."""
        body = self._token_request(
            code=code, code_verifier=code_verifier, callback_path=CALLBACK_PATH
        )
        return self.check_id_token(body["id_token"], nonce=nonce, now=now)

    def exchange_connect(
        self, *, code: str, code_verifier: str, nonce: str, now: datetime
    ) -> Grant:
        """Trade the code of a calendar connect for the account's identity, the scopes Google
        granted and the refresh token (if one was issued), or raise `GoogleError`. The access
        token in the answer is dropped here: it is never stored."""
        body = self._token_request(
            code=code, code_verifier=code_verifier, callback_path=CALENDAR_CALLBACK_PATH
        )
        identity = self.check_id_token(body["id_token"], nonce=nonce, now=now)
        scope, refresh = body.get("scope"), body.get("refresh_token")
        if not isinstance(scope, str):
            logger.warning("google token answer had no scope list")
            raise GoogleError
        if refresh is not None and not (
            isinstance(refresh, str) and 0 < len(refresh) <= MAX_REFRESH_TOKEN_CHARS
        ):
            logger.warning("google token answer had a malformed refresh token")
            raise GoogleError
        return Grant(
            identity=identity,
            scopes=frozenset(scope.split()),
            refresh_token=Secret(refresh) if refresh else None,
        )

    def revoke(self, token: Secret) -> None:
        """Tell Google to stop honouring a refresh token (and the access tokens made from it), or
        raise `GoogleError`. A token Google already considers dead (HTTP 400 `invalid_token`:
        revoked at myaccount.google.com, expired) counts as done. The URL is fixed; the token goes
        in the form body only, never in a URL, a log line or an error."""
        try:
            with httpx.Client(timeout=TIMEOUT_SECONDS, transport=self._transport) as client:
                response = client.post(REVOKE_URL, data={"token": token.reveal()})
            body = response.json() if response.status_code == 400 else None
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("google token revoke failed (%s)", type(exc).__name__)
            raise GoogleError from None
        if response.status_code == 200:
            return
        if isinstance(body, dict) and body.get("error") == "invalid_token":
            return
        logger.warning("google token revoke refused (HTTP %s)", response.status_code)
        raise GoogleError

    def check_id_token(self, id_token: str, *, nonce: str, now: datetime) -> Identity:
        claims = _claims(id_token)
        issued, expires = claims.get("iat"), claims.get("exp")
        stamps = all(isinstance(v, int) and not isinstance(v, bool) for v in (issued, expires))
        email, sub, name = claims.get("email"), claims.get("sub"), claims.get("name")
        seen_nonce = claims.get("nonce")
        checks = (
            claims.get("iss") in ISSUERS,
            claims.get("aud") == self._settings.google_client_id,
            stamps and expires > now.timestamp() - CLOCK_SKEW_SECONDS,
            stamps and issued < now.timestamp() + CLOCK_SKEW_SECONDS,
            isinstance(seen_nonce, str)
            and secrets.compare_digest(seen_nonce.encode(), nonce.encode()),
            isinstance(sub, str) and 0 < len(sub) <= MAX_SUB_CHARS,
            isinstance(email, str) and bool(email),
            claims.get("email_verified") is True,
        )
        if not all(checks):
            logger.warning("google id token refused (check %s failed)", checks.index(False) + 1)
            raise GoogleError
        hosted = claims.get("hd")
        return Identity(
            sub=sub,
            email=email,
            name=name if isinstance(name, str) else None,
            hosted_domain=hosted if isinstance(hosted, str) and hosted else None,
        )


def get_google(request: Request) -> GoogleOAuth | None:
    """The Google client for this app, or None when sign in with Google is not configured. A
    dependency, so tests swap in a fake."""
    settings = request.app.state.settings
    return GoogleOAuth(settings) if enabled(settings) else None
