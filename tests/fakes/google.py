"""Fakes for Google's HTTP APIs, served through `httpx.MockTransport`.

One section per task, so tasks that add to this file merge cleanly:

- C3: `FakeGoogle`, Google's side of the OAuth connect flow: the consent screen, the token
  endpoint (authorization-code grant, PKCE, ID token) and revoke. It wraps the REAL `GoogleOAuth`
  client, so the consent URL and the token parsing are production code.
- C5: `FakeGoogleCalendar`, the token endpoint's refresh grant and the Calendar API's
  `events.list` and `events.get`.

The C5 response bodies are SYNTHETIC: built from Google's documented shapes (Calendar API v3
Events reference, the OAuth 2.0 web-server flow), not captured from a live account. The live
capture (plan task C5, step "Live step") replaces them. Everything is a placeholder, never a real
token.

`FakeGoogle`: `FakeGoogle(settings).consent(authorize_url, ...)` is the person clicking Allow;
`consent(..., grant=...)` models unticking boxes (only those scopes are granted), and
`issue_refresh_token=False` a reply without a refresh token. The fake is strict where Google is: a
code works once, for the client and redirect URI it was issued to, and only with the PKCE verifier
whose S256 challenge came with the consent request.
"""

import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from app.config import Settings
from app.google_oauth import GoogleOAuth

# ---- C3: OAuth consent, token exchange, revoke ----

TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


def _part(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


class FakeGoogle:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.issue_refresh_token = True
        self.fail_token_endpoint = False
        self.codes: dict[str, dict] = {}
        self.token_requests: list[dict[str, str]] = []
        self.revoked: list[str] = []
        self.refresh_tokens: list[str] = []
        self._counter = 0
        self.oauth = GoogleOAuth(settings, transport=httpx.MockTransport(self._handle))

    # ---- the consent screen ----

    def consent(
        self,
        authorize_url: str,
        *,
        email: str = "grace@example.test",
        sub: str = "g-grace-1",
        name: str = "Grace Example",
        hosted_domain: str | None = None,
        grant: set[str] | None = None,
        previously_granted: frozenset[str] = frozenset(),
        nonce: str | None = None,
    ) -> str:
        """The person clicks Allow on the consent screen for `authorize_url`; returns the code
        Google would put in the redirect. `grant` is the set of scopes left ticked (default: all
        that were asked for); `previously_granted` are scopes from an earlier consent, which
        Google adds back because the request said `include_granted_scopes=true`."""
        parsed = urlparse(authorize_url)
        assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == AUTH_URL
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        asked = set(query["scope"].split())
        ticked = asked if grant is None else asked & grant
        self._counter += 1
        code = f"4/fake-authorization-code-{self._counter}"
        self.codes[code] = {
            "client_id": query["client_id"],
            "redirect_uri": query["redirect_uri"],
            "challenge": query["code_challenge"],
            "nonce": nonce or query["nonce"],
            "scopes": ticked | set(previously_granted) | {"openid"},
            "email": email,
            "sub": sub,
            "name": name,
            "hd": hosted_domain,
        }
        return code

    # ---- Google's servers ----

    def _handle(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if str(request.url) == REVOKE_URL:
            self.revoked.append(form["token"])
            return httpx.Response(200, json={})
        assert str(request.url) == TOKEN_URL, request.url
        self.token_requests.append(form)
        if self.fail_token_endpoint:
            return httpx.Response(503, json={"error": "backend_error"})
        issued = self.codes.pop(form.get("code", ""), None)  # a code works once
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(form.get("code_verifier", "").encode()).digest()
        )
        ok = (
            issued is not None
            and form.get("grant_type") == "authorization_code"
            and form.get("client_id") == issued["client_id"] == self.settings.google_client_id
            and form.get("client_secret") == self.settings.google_client_secret
            and form.get("redirect_uri") == issued["redirect_uri"]
            and challenge.decode().rstrip("=") == issued["challenge"]
        )
        if not ok:
            return httpx.Response(400, json={"error": "invalid_grant"})
        now = int(time.time())
        id_token = ".".join(
            (
                _part({"alg": "RS256"}),
                _part(
                    {
                        "iss": "https://accounts.google.com",
                        "aud": self.settings.google_client_id,
                        "sub": issued["sub"],
                        "email": issued["email"],
                        "email_verified": True,
                        "name": issued["name"],
                        "nonce": issued["nonce"],
                        "iat": now - 5,
                        "exp": now + 3000,
                        **({"hd": issued["hd"]} if issued["hd"] else {}),
                    }
                ),
                "c2ln",
            )
        )
        body = {
            "access_token": f"ya29.fake-access-token-{self._counter}",
            "expires_in": 3599,
            "scope": " ".join(sorted(issued["scopes"])),
            "token_type": "Bearer",
            "id_token": id_token,
        }
        if self.issue_refresh_token:
            token = f"1//fake-refresh-token-{self._counter}"
            self.refresh_tokens.append(token)
            body["refresh_token"] = token
        return httpx.Response(200, json=body)


# ---- C5: Calendar API and token refresh ----

CALENDAR_HOST = "www.googleapis.com"
TOKEN_HOST = "oauth2.googleapis.com"
EVENTS_PATH = "/calendar/v3/calendars/primary/events"


class FakeGoogleCalendar:
    """The refresh-token grant plus `events.list` / `events.get` on the primary calendar.

    `pages` is a list of pages (each a list of raw events); a request for page N is answered with
    `nextPageToken` until the last page, which carries `nextSyncToken`. `events` answers
    `events.get`. A call can be made to misbehave: `override("list", index, response)` replaces the
    answer to the index-th list call (0-based); an `Exception` instance is raised instead.
    """

    REFRESH_TOKEN = "refresh-token-placeholder"
    CLIENT_ID = "test-client-id.apps.example.test"
    CLIENT_SECRET = "client-secret-placeholder"

    def __init__(self, *, expires_in: int = 3599) -> None:
        self.expires_in = expires_in
        self.pages: list[list[dict]] = [[]]
        self.sync_token = "sync-token-1"
        self.events: dict[str, dict] = {}
        self.valid_refresh_tokens = {self.REFRESH_TOKEN}
        self.minted: list[str] = []
        self.requests: list[httpx.Request] = []
        self._overrides: dict[tuple[str, int], httpx.Response | Exception] = {}
        self._refresh_override: httpx.Response | Exception | None = None
        self.token_calls = 0
        self.list_calls = 0
        self.get_calls = 0
        self.transport = httpx.MockTransport(self._handle)

    # ---- scripting ----

    def override(self, kind: str, index: int, response: httpx.Response | Exception) -> None:
        self._overrides[(kind, index)] = response

    def override_refresh(self, response: httpx.Response | Exception) -> None:
        self._refresh_override = response

    def expire_access_tokens(self) -> None:
        """Google stops accepting every access token minted so far (they expired early)."""
        self.minted.clear()

    @staticmethod
    def error(status: int, reason: str, message: str = "synthetic error") -> httpx.Response:
        body = {"error": {"code": status, "message": message, "errors": [{"reason": reason}]}}
        return httpx.Response(status, json=body)

    # ---- what the tests read back ----

    def calls(self, host: str, path_prefix: str = "") -> list[httpx.Request]:
        return [
            r for r in self.requests if r.url.host == host and r.url.path.startswith(path_prefix)
        ]

    def list_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == EVENTS_PATH]

    @staticmethod
    def query(request: httpx.Request) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}

    # ---- the server ----

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == TOKEN_HOST and request.url.path == "/token":
            return self._token(request)
        if request.url.host == CALENDAR_HOST and request.url.path.startswith(EVENTS_PATH):
            return self._events(request)
        return httpx.Response(404, json={"error": "not a Google endpoint this fake serves"})

    def _token(self, request: httpx.Request) -> httpx.Response:
        index, self.token_calls = self.token_calls, self.token_calls + 1
        if self._refresh_override is not None:
            return self._answer(self._refresh_override)
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if (
            form.get("grant_type") != "refresh_token"
            or form.get("client_id") != self.CLIENT_ID
            or form.get("client_secret") != self.CLIENT_SECRET
        ):
            return httpx.Response(401, json={"error": "invalid_client"})
        if form.get("refresh_token") not in self.valid_refresh_tokens:
            return httpx.Response(
                400,
                json={
                    "error": "invalid_grant",
                    "error_description": "Token has been expired or revoked.",
                },
            )
        token = f"access-token-placeholder-{index + 1}"
        self.minted.append(token)
        return httpx.Response(
            200,
            json={
                "access_token": token,
                "expires_in": self.expires_in,
                "scope": "https://www.googleapis.com/auth/calendar.events.readonly",
                "token_type": "Bearer",
            },
        )

    def _events(self, request: httpx.Request) -> httpx.Response:
        is_get = request.url.path != EVENTS_PATH
        kind = "get" if is_get else "list"
        index = self.get_calls if is_get else self.list_calls
        if is_get:
            self.get_calls += 1
        else:
            self.list_calls += 1
        if (kind, index) in self._overrides:
            return self._answer(self._overrides[(kind, index)])
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
        if bearer not in self.minted:
            return self.error(401, "authError", "Invalid Credentials")
        if is_get:
            event_id = unquote(request.url.raw_path.decode().split("?")[0][len(EVENTS_PATH) + 1 :])
            if event_id in self.events:
                return httpx.Response(200, json=self.events[event_id])
            return self.error(404, "notFound", "Not Found")
        return self._list(self.query(request))

    def _list(self, query: dict[str, str]) -> httpx.Response:
        page = 0
        if "pageToken" in query:
            token = query["pageToken"]
            page = int(token.removeprefix("page-token-")) if token.startswith("page-token-") else -1
            if not 0 < page < len(self.pages):
                return self.error(400, "invalid", "Invalid page token")
        body: dict = {
            "kind": "calendar#events",
            "summary": "ada@example.test",
            "timeZone": "Europe/Lagos",
            "accessRole": "owner",
            "items": self.pages[page],
        }
        if page + 1 < len(self.pages):
            body["nextPageToken"] = f"page-token-{page + 1}"
        else:
            body["nextSyncToken"] = self.sync_token
        return httpx.Response(200, json=body)

    @staticmethod
    def _answer(scripted: httpx.Response | Exception) -> httpx.Response:
        if isinstance(scripted, Exception):
            raise scripted
        return scripted


def gone() -> httpx.Response:
    """Google's answer to a sync token it no longer honours (410, reason `fullSyncRequired`)."""
    return FakeGoogleCalendar.error(
        410, "fullSyncRequired", "Sync token is no longer valid, a full sync is required."
    )


def not_json(status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=b"<html>not json</html>")
