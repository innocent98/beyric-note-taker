"""Sign in once and save a token, so create_space.py can act as you.

Run:  python3 auth.py        (or  SPIKE_ACCOUNT=ws python3 auth.py  for a second account)

Opens Google's own page in your browser. You type your password there, never here. Asks for one
Meet permission (create meetings) plus your sign-in identity (used only to tell Gmail from
Workspace). Prints which permissions Google granted, never a token.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import threading
import time
import urllib.parse
import webbrowser

import instant_common as ic


def main() -> None:
    client = ic.load_client()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode()
    state = secrets.token_urlsafe(16)
    answer: dict[str, str] = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
            if "code" in query or "error" in query:
                answer.update(query)
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Done. You can close this tab and go back to the terminal.")
                done.set()
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: object) -> None:  # keep the code out of the terminal
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    url = (
        ic.AUTH_URL
        + "?"
        + urllib.parse.urlencode(
            {
                "client_id": client["client_id"],
                "redirect_uri": redirect,
                "response_type": "code",
                "scope": " ".join(ic.SCOPES),
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
                "access_type": "offline",
                "prompt": "consent",
            }
        )
    )
    print("Opening Google's sign-in page. If nothing opens, paste this into Chrome:\n")
    print(url + "\n")
    webbrowser.open(url)
    if not done.wait(timeout=300):
        raise ic.SpikeError("Nothing came back within 5 minutes. Run python3 auth.py again.")
    server.shutdown()

    if answer.get("state") != state:
        raise ic.SpikeError("The answer did not match this sign-in attempt. Run it again.")
    if "error" in answer:
        raise ic.SpikeError(
            f"Google reported: {ic.redact(answer['error'])}. "
            "If it is access_denied, your Gmail is probably not in 'Test users' (runbook step 3)."
        )

    status, token = ic.post_form(
        ic.TOKEN_URL,
        {
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "code": answer["code"],
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect,
        },
    )
    if status != 200 or "refresh_token" not in token:
        raise ic.SpikeError(
            f"Google would not issue a token ({token.get('error')}: "
            f"{ic.redact(str(token.get('error_description')))})."
        )
    ic.save_private(
        ic.TOKEN_FILE,
        {
            "access_token": token["access_token"],
            "refresh_token": token["refresh_token"],
            "expires_at": time.time() + int(token.get("expires_in", 3600)),
            "scope": token.get("scope", ""),
        },
    )
    granted = token.get("scope", "").split()
    print(f"Saved the token for account '{ic.ACCOUNT}' (readable only by you, git-ignored).")
    print("Permissions Google granted:")
    for scope in granted:
        print("  ", scope)
    print("Account type:", ic.account_kind(token.get("id_token", "")))
    if ic.SCOPE_CREATED not in granted:
        print("WARNING: the meeting-creation permission was NOT granted. Run this again and")
        print("leave every box ticked on the permissions page.")
    print(json.dumps({"granted_scopes": granted}))


if __name__ == "__main__":
    try:
        main()
    except ic.SpikeError as exc:
        raise SystemExit(f"\nSTOP: {exc}") from None
