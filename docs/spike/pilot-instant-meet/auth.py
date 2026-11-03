"""Run once on the Mac (you click the consent). Saves local/token.json (mode 600).

    python3 auth.py                 # readonly Meet scope + Pub/Sub (what S1 to S9 need)
    python3 auth.py --with-created  # additionally ask for meetings.space.created (only if S1 says it is needed)

Loopback + PKCE, the same flow the production app's installed-app equivalent would use.
The token never prints; nobody but this script and Google sees it.
"""
import base64
import hashlib
import http.server
import json
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

import common as c


def main():
    scopes = list(c.SCOPE_BASE)
    if "--with-created" in sys.argv:
        scopes.append(c.SCOPE_CREATED)
    client = c.load_client()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    box = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in q or "error" in q:
                box.update({k: v[0] for k, v in q.items()})
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<h3>Done. You can close this tab and go back to the terminal.</h3>")
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    redirect = f"http://127.0.0.1:{srv.server_port}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    url = client["auth_uri"] + "?" + urllib.parse.urlencode({
        "client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code",
        "scope": " ".join(scopes), "access_type": "offline", "prompt": "consent",
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state})
    print("\nOpening the Google consent page. If no browser opens, paste this URL into Chrome\n"
          "(signed in as the TEST Gmail account):\n\n" + url + "\n")
    webbrowser.open(url)
    print("Waiting up to 5 minutes for you to finish the consent ...")
    for _ in range(300):
        if box:
            break
        time.sleep(1)
    srv.shutdown()
    if not box:
        c.die("no answer from the browser within 5 minutes; run again.")
    if "error" in box:
        c.die(f"Google returned error: {box['error']}")
    if box.get("state") != state:
        c.die("state mismatch; run again.")

    data = urllib.parse.urlencode({
        "client_id": client["client_id"], "client_secret": client["client_secret"], "code": box["code"],
        "code_verifier": verifier, "grant_type": "authorization_code", "redirect_uri": redirect}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(c.TOKEN_URL, data=data), timeout=30) as r:
            tok = json.loads(r.read())
    except urllib.error.HTTPError as e:
        c.die(f"token exchange failed HTTP {e.code}: {c.redact(e.read().decode(errors='replace'))}")
    if "refresh_token" not in tok:
        c.die("no refresh_token returned; remove the app at myaccount.google.com/permissions and run again.")
    granted = set(tok.get("scope", "").split())
    tok["expires_at"] = time.time() + tok.get("expires_in", 3600) - 60
    tok["obtained_at"] = c.now_iso()
    tok.pop("id_token", None)
    c.write_private(c.TOKEN_FILE, json.dumps(tok, indent=2))
    missing = [s for s in scopes if s not in granted and not s.startswith("openid")]
    print(f"\nSaved {c.TOKEN_FILE.name} (mode 600). Granted scopes:")
    for s in sorted(granted):
        print("  ", s)
    if missing:
        print("\nWARNING: you unticked some scopes on the consent page; the spike needs them:")
        for s in missing:
            print("  ", s)
        print("Run auth.py again and keep every box ticked.")
    c.save_state(token_obtained_at=tok["obtained_at"], granted_scopes=sorted(granted))


if __name__ == "__main__":
    main()
