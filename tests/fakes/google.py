"""Fakes for Google's HTTP APIs, served through `httpx.MockTransport`.

One class per task, each in its own section, so tasks that add to this file merge cleanly:

- C5: `FakeGoogleCalendar`, the token endpoint's refresh grant and the Calendar API's
  `events.list` and `events.get`.

The response bodies are SYNTHETIC: built from Google's documented shapes (Calendar API v3 Events
reference, the OAuth 2.0 web-server flow), not captured from a live account. The live capture
(plan task C5, step "Live step") replaces them.
"""

from urllib.parse import parse_qs, unquote

import httpx

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
