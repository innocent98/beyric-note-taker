"""A stand-in for Google's side of OAuth: the consent screen, the token endpoint and revoke.

`FakeGoogle.oauth` is the REAL `GoogleOAuth` client wired to an `httpx.MockTransport`, so the
consent URL the app builds, the form it posts to the token endpoint and the way it reads the
answer are all the production code; only Google's servers are replaced. The fake is strict where
Google is: a code works once, for the client and redirect URI it was issued to, and only with the
PKCE verifier whose S256 challenge came with the consent request.

    google = FakeGoogle(settings)
    code = google.consent(authorize_url, email="grace@example.test")   # the person clicks Allow
    ...                                                                # app exchanges the code

`consent(..., grant=...)` models the person unticking boxes: only those scopes are granted.
`issue_refresh_token=False` models a reply without a refresh token (Google omits it when it
believes one was already issued). Everything in the module is a placeholder, never a real token.
"""

import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse

import httpx

from app.config import Settings
from app.google_oauth import GoogleOAuth

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
