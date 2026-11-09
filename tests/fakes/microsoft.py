"""Fakes for Microsoft's HTTP APIs, served through `httpx.MockTransport` (Outlook phase O).

`FakeMicrosoft` is Microsoft's side of the OAuth connect flow plus the few Graph calls the callback
makes: the consent screen, the token endpoint (authorization-code and refresh grants, PKCE, ID
token), `GET /me/calendarView` (the probe), `GET /me` and `GET /organization`. It wraps the REAL
`MicrosoftOAuth` client, so the consent URL, the ID token checks and the token parsing are
production code.

**SYNTHETIC.** Every body is built from Microsoft's DOCUMENTED shapes (Outlook spec section 4), not
captured from a live account; the M0 run (`docs/spike/outlook-m0/`) replaces them. Everything is a
placeholder, never a real token. The fake is strict where Microsoft is: a code works once, for the
client and redirect URI it was issued to, and only with the PKCE verifier whose S256 challenge came
with the consent request; a Graph call needs the access token the token endpoint issued.
"""

import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse

import httpx

from app.config import Settings
from app.microsoft_oauth import CONSUMER_TENANT, MicrosoftOAuth

LOGIN_HOST = "login.microsoftonline.com"
GRAPH_HOST = "graph.microsoft.com"


def _part(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


class FakeMicrosoft:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.fail_token_endpoint = False
        self.codes: dict[str, dict] = {}
        self.token_requests: list[dict[str, str]] = []
        self.token_urls: list[str] = []
        self.requests: list[httpx.Request] = []  # every request of any host, in order
        self.graph_requests: list[httpx.Request] = []
        self.access_tokens: dict[str, str] = {}  # issued access token to its code
        self.refresh_tokens: list[str] = []
        # What Graph answers; override per test.
        self.probe: httpx.Response | Exception = httpx.Response(200, json={"value": []})
        self.me: httpx.Response | Exception | None = None  # None: built from the consent
        self.organization: httpx.Response | Exception | None = None
        self._counter = 0
        self.oauth = MicrosoftOAuth(settings, transport=httpx.MockTransport(self._handle))

    # ---- the consent screen ----

    def consent(
        self,
        authorize_url: str,
        *,
        tid: str = CONSUMER_TENANT,
        oid: str | None = "00000000-0000-0000-0000-000000000001",
        sub: str = "SUB-PLACEHOLDER",
        preferred_username: str = "ada@outlook.example.test",
        mail: str | None = "ada@outlook.example.test",
        user_principal_name: str | None = None,
        name: str = "Ada Example",
        grant: set[str] | None = None,
        issue_refresh_token: bool = True,
        nonce: str | None = None,
        claims: dict | None = None,
        scope_style: str = "short",
    ) -> str:
        """The person clicks Accept for `authorize_url`; returns the code Microsoft would put in
        the redirect. `grant` is the set of scopes left ticked (default: all asked for)."""
        parsed = urlparse(authorize_url)
        assert parsed.netloc == LOGIN_HOST and parsed.path.endswith("/oauth2/v2.0/authorize")
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        asked = set(query["scope"].split())
        ticked = asked if grant is None else asked & grant
        self._counter += 1
        code = f"CODE-PLACEHOLDER-{self._counter}"
        self.codes[code] = {
            "authority": parsed.path.split("/")[1],
            "client_id": query["client_id"],
            "redirect_uri": query["redirect_uri"],
            "challenge": query["code_challenge"],
            "nonce": nonce or query["nonce"],
            "scopes": ticked,
            "tid": tid,
            "oid": oid,
            "sub": sub,
            "preferred_username": preferred_username,
            "name": name,
            "refresh": issue_refresh_token,
            "claims": claims or {},
            "scope_style": scope_style,
            "mail": mail,
            "upn": user_principal_name,
        }
        return code

    # ---- Microsoft's servers ----

    def _scope_text(self, scopes: set[str], style: str) -> str:
        """The `scope` string of an answer. Documented: personal accounts get short names (and
        `openid profile email` without `offline_access`), work accounts full Graph URLs."""
        out = []
        for scope in sorted(scopes):
            if scope == "offline_access":
                continue
            if style == "short" and scope.startswith("https://graph.microsoft.com/"):
                scope = scope.removeprefix("https://graph.microsoft.com/")
            out.append(scope)
        return " ".join(out)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == LOGIN_HOST:
            return self._token(request)
        assert request.url.host == GRAPH_HOST, request.url
        self.graph_requests.append(request)
        return self._graph(request)

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.token_requests.append(form)
        self.token_urls.append(str(request.url))
        if self.fail_token_endpoint:
            return httpx.Response(503, json={"error": "temporarily_unavailable"})
        if form.get("grant_type") == "refresh_token":
            self.refresh_tokens.append(form["refresh_token"])
            return httpx.Response(
                200,
                json={
                    "token_type": "Bearer",
                    "scope": "Calendars.Read User.Read",
                    "expires_in": 3599,
                    "access_token": "ACCESS-PLACEHOLDER-refresh",
                    "refresh_token": "REFRESH-PLACEHOLDER-rotated",
                },
            )
        issued = self.codes.pop(form.get("code", ""), None)  # a code works once
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(form.get("code_verifier", "").encode()).digest()
        )
        ok = (
            issued is not None
            and form.get("grant_type") == "authorization_code"
            and form.get("client_id") == issued["client_id"] == self.settings.microsoft_client_id
            and form.get("client_secret") == self.settings.microsoft_client_secret
            and form.get("redirect_uri") == issued["redirect_uri"]
            and challenge.decode().rstrip("=") == issued["challenge"]
            and request.url.path == f"/{issued['authority']}/oauth2/v2.0/token"
        )
        if not ok:
            return httpx.Response(
                400,
                json={
                    "error": "invalid_grant",
                    "error_description": "AADSTS70000: ERROR-DESCRIPTION-SENTINEL",
                    "error_codes": [70000],
                },
            )
        now = int(time.time())
        tid = issued["tid"]
        claims = {
            "ver": "2.0",
            "iss": f"https://login.microsoftonline.com/{tid}/v2.0",
            "aud": issued["client_id"],
            "iat": now,
            "exp": now + 3600,
            "nonce": issued["nonce"],
            "tid": tid,
            "oid": issued["oid"],
            "sub": issued["sub"],
            "preferred_username": issued["preferred_username"],
            "name": issued["name"],
            **issued["claims"],
        }
        claims = {k: v for k, v in claims.items() if v is not None}
        id_token = ".".join((_part({"alg": "RS256"}), _part(claims), "signature-not-checked"))
        access = f"ACCESS-PLACEHOLDER-{len(self.access_tokens) + 1}"
        self.access_tokens[access] = form["code"]
        self._last = issued
        body = {
            "token_type": "Bearer",
            "scope": self._scope_text(issued["scopes"], issued["scope_style"]),
            "expires_in": 3599,
            "ext_expires_in": 3599,
            "access_token": access,
            "id_token": id_token,
        }
        if issued["refresh"] and "offline_access" in issued["scopes"]:
            body["refresh_token"] = f"REFRESH-PLACEHOLDER-{len(self.access_tokens)}"
        return httpx.Response(200, json=body)

    def _graph(self, request: httpx.Request) -> httpx.Response:
        bearer = request.headers.get("authorization", "")
        token = bearer.removeprefix("Bearer ")
        if token not in self.access_tokens:
            return httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken"}})
        path = request.url.path
        answer: httpx.Response | Exception | None
        if path == "/v1.0/me/calendarView":
            answer = self.probe
        elif path == "/v1.0/me":
            answer = self.me
            if answer is None:
                info = self._last
                answer = httpx.Response(
                    200,
                    json={
                        "mail": info["mail"],
                        "userPrincipalName": info["upn"] or info["preferred_username"],
                    },
                )
        elif path == "/v1.0/organization":
            answer = self.organization or httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "verifiedDomains": [
                                {"name": "company.test", "isDefault": True, "type": "Managed"},
                                {"name": "COMPANY.onmicrosoft.com", "isDefault": False},
                            ]
                        }
                    ]
                },
            )
        else:  # pragma: no cover - a call the fake does not know is a test bug
            raise AssertionError(f"unexpected Graph call {request.url}")
        if isinstance(answer, Exception):
            raise answer
        return answer
