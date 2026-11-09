#!/usr/bin/env python3
"""M0 throwaway: record REAL Microsoft sign-in and Graph calendar answers (Outlook spec section 11).

NOT part of the app (nothing under app/ imports it, and it is never deployed). It answers S1 to S7
of docs/superpowers/specs/2026-11-04-outlook-oauth-design.md by signing in a real account to YOUR
Azure app registration and recording what Microsoft really answers. It reads the calendar only
(Calendars.Read, User.Read), writes nothing at Microsoft, keeps the access token in memory, and
never prints or stores a secret or a token.

HOW TO RUN IT (Adebayo, on your Mac; about 15 minutes per account)

 1. Put your keys in ONE file outside the repo. In a terminal:
        mkdir -p ~/.config/beyric-outlook && chmod 700 ~/.config/beyric-outlook
        open -e ~/.config/beyric-outlook/keys.env       (creates an empty file if missing)
    and type these three lines (values from the Azure portal: Overview > Application (client) ID,
    Certificates & secrets > the secret VALUE; never paste them into a chat):
        MICROSOFT_CLIENT_ID=<application (client) id>
        MICROSOFT_CLIENT_SECRET=<secret value>
        MICROSOFT_AUTHORITY=consumers
    Save it, then lock it:  chmod 600 ~/.config/beyric-outlook/keys.env
    (The same names in the environment win over the file; an old docs/spike/outlook-m0/.env.m0
    with MS_M0_CLIENT_ID and MS_M0_CLIENT_SECRET still works as a last resort.)
 2. Check the script itself first, no network, no secret:
        .venv/bin/python docs/spike/outlook-m0/capture.py selftest
    It must end with "selftest ok".
 3. Which redirect does your registration have? In Azure: App registrations > your app >
    Authentication > Web redirect URIs.
      * It lists http://localhost:8000/api/calendar/microsoft/callback  -> use the default
        `--redirect local`: stop anything on port 8000, run the command, and the script catches the
        redirect by itself.
      * It lists only https://notes.beyrictech.com/api/calendar/microsoft/callback (the production
        registration) -> add `--redirect paste`. After you consent, Microsoft sends the browser to
        notes.beyrictech.com, which shows an error or "not available" page: THAT IS EXPECTED. Copy
        the whole address from the browser's address bar (it starts with https://notes.beyrictech.com/
        and holds `?code=...&state=...`), come back to the terminal, paste it at the prompt (it is
        hidden while you paste) and press Enter. Do it within a minute: the code expires quickly
        and works once. (It will also show in the production server's access log for that one
        request; it is single use and already spent by then.)
 4. Run it, once per account. From the repo root:
        .venv/bin/python docs/spike/outlook-m0/capture.py run --label personal --authority consumers
    A browser opens at Microsoft. Sign in with the TEST account (the spare Outlook.com one), and on
    the consent screen take a SCREENSHOT (Cmd+Shift+4) into docs/spike/outlook-m0/screens/ before
    you click Accept. Note whether it says "unverified" or "needs administrator approval".
 5. The script then calls Graph and prints "*" lines for each fact it read. At the end it asks two
    questions: what the consent screen said, and the URL of the page where this app's access can
    be removed. Type the answers (Enter skips one).
 6. The other runs, the cleanup and the commit steps are in README.md in this folder (sections C
    and "Cleanup"): personal-basic (compares Calendars.ReadBasic), work (a work account),
    work-nomailbox and after-revoke (remove the app at Microsoft first).

    python docs/spike/outlook-m0/capture.py run --label personal --authority consumers
    python docs/spike/outlook-m0/capture.py run --label personal --redirect paste
    python docs/spike/outlook-m0/capture.py run --label personal-basic --scopes basic
    python docs/spike/outlook-m0/capture.py run --label work --authority organizations
    python docs/spike/outlook-m0/capture.py after-revoke --label personal
    python docs/spike/outlook-m0/capture.py selftest          # no network, no secret

Secrets: the client id and secret are read from the environment (MICROSOFT_CLIENT_ID,
MICROSOFT_CLIENT_SECRET, MICROSOFT_AUTHORITY), else from ~/.config/beyric-outlook/keys.env, else
from docs/spike/outlook-m0/.env.m0 (git-ignored); never from the command line, never printed.
Access tokens live in memory only. The one refresh token is kept in `out/state-<label>.json`
(git-ignored, mode 600) so `after-revoke` can try it once after the app is removed at Microsoft;
delete `out/` when the run is finished.

Output: raw answers go to `out/raw/` (git-ignored, NOT redacted, tokens already stripped). The
redacted copies that may be committed go to docs/fe-captures/calendar-outlook/graph/, and a
readable record is appended to docs/spike/outlook-oauth-captures.md. Redaction keeps every field
name and the SHAPE of every value (URL hosts and paths, the length of ids) and replaces the
values: addresses become `person-N@example.test`, GUIDs become
`00000000-0000-0000-0000-00000000000N` (the consumer-tenant GUID is public and kept), long id-like
runs become `<id:LEN>`, URL queries become `?<query>`.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import sys
import threading
import webbrowser
from datetime import UTC, datetime, timedelta
from getpass import getpass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

import httpx

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RAW = HERE / "out" / "raw"
STATE = HERE / "out"
GRAPH_DIR = ROOT / "docs" / "fe-captures" / "calendar-outlook" / "graph"
REPORT = ROOT / "docs" / "spike" / "outlook-oauth-captures.md"

REDIRECT_LOCAL = "http://localhost:8000/api/calendar/microsoft/callback"
REDIRECT_PROD = "https://notes.beyrictech.com/api/calendar/microsoft/callback"
KEYS_FILE = Path.home() / ".config" / "beyric-outlook" / "keys.env"
AUTHORITIES = ("consumers", "organizations", "common")
LOGIN = "https://login.microsoftonline.com"
GRAPH = "https://graph.microsoft.com/v1.0"
CONSUMER_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"
G = "https://graph.microsoft.com/"
SCOPES = {
    "read": f"openid profile offline_access {G}User.Read {G}Calendars.Read",
    "basic": f"openid profile offline_access {G}User.Read {G}Calendars.ReadBasic",
}
# ASSUMED list from spec 5.1; this run is what confirms or corrects it.
SELECT = (
    "id,iCalUId,subject,start,end,isAllDay,isCancelled,isDraft,isOrganizer,organizer,"
    "responseStatus,attendees,onlineMeeting,onlineMeetingProvider,isOnlineMeeting,location,"
    "locations,body,seriesMasterId,type,sensitivity,showAs"
)
PREFER = ['outlook.timezone="UTC"', 'IdType="ImmutableId"']
TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)


# ---------------------------------------------------------------- redaction


class Redactor:
    """Deterministic: the same address or GUID always becomes the same placeholder in one run."""

    EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    GUID = re.compile(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
    )
    URL = re.compile(r"https?://[^\s\"'<>)\]]+")
    LONG = re.compile(r"[A-Za-z0-9_\-]{20,}")
    DIGITS = re.compile(r"\d{8,}")
    KEEP_KEYS = {
        "scope",
        "token_type",
        "expires_in",
        "ext_expires_in",
        "error",
        "code",
        "timeZone",
        "type",
        "ver",
        "version",
    }
    SECRET_KEYS = {"access_token", "refresh_token", "id_token", "client_secret", "code_verifier"}

    def __init__(self) -> None:
        self.mails: dict[str, str] = {}
        self.guids: dict[str, str] = {}

    def mail(self, m: re.Match) -> str:
        value = m.group(0).lower()
        if value.endswith("@example.test"):
            return value
        return self.mails.setdefault(value, f"person-{len(self.mails) + 1}@example.test")

    def guid(self, m: re.Match) -> str:
        value = m.group(0).lower()
        if value == CONSUMER_TENANT:
            return value
        return self.guids.setdefault(value, f"00000000-0000-0000-0000-{len(self.guids) + 1:012d}")

    def url(self, m: re.Match) -> str:
        parts = urlparse(m.group(0))
        path = self.DIGITS.sub(lambda x: f"<num:{len(x.group(0))}>", parts.path)
        path = self.LONG.sub(self.long, path)
        query = "?<query>" if parts.query else ""
        return f"{parts.scheme}://{parts.netloc}{path}{query}"

    def text(self, value: str) -> str:
        value = self.URL.sub(self.url, value)
        value = self.EMAIL.sub(self.mail, value)
        value = self.GUID.sub(self.guid, value)
        return self.LONG.sub(self.long, value)

    def long(self, m: re.Match) -> str:
        text = m.group(0)
        if self.GUID.fullmatch(text) or text.startswith("person-") or text.startswith("<"):
            return text
        return f"<id:{len(text)}>"

    def walk(self, node, key: str = ""):
        if isinstance(node, dict):
            return {k: self.walk(v, k) for k, v in node.items()}
        if isinstance(node, list):
            return [self.walk(v, key) for v in node]
        if key in self.SECRET_KEYS:
            return "<redacted>" if node else node
        if isinstance(node, str):
            if (
                key in {"dateTime", "createdDateTime", "lastModifiedDateTime"}
                or key in self.KEEP_KEYS
            ):
                return self.GUID.sub(self.guid, node) if key == "scope" else node
            plain = not self.EMAIL.search(node) and "://" not in node
            if key in {"displayName", "subject", "name"} and plain:
                return node[:1] + "…" if len(node) > 1 else node  # titles and names: shape only
            return self.text(node)
        return node


# ---------------------------------------------------------------- recording


def write_private(path: Path, text: str) -> None:
    """Create the file owner-only from the start (no window where it is world-readable)."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)


def strip_secrets(node):
    """Tokens never reach disk, not even in the git-ignored raw copy."""
    if isinstance(node, dict):
        return {
            k: ("<redacted>" if k in Redactor.SECRET_KEYS and v else strip_secrets(v))
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [strip_secrets(v) for v in node]
    return node


class Recorder:
    def __init__(self, label: str) -> None:
        self.label = label
        self.redactor = Redactor()
        self.index = 0
        self.notes: list[str] = []
        self.files: list[str] = []
        RAW.mkdir(parents=True, exist_ok=True, mode=0o700)
        GRAPH_DIR.mkdir(parents=True, exist_ok=True)

    def save(self, name: str, note: str, status: int | None, body, *, request: dict | None = None):
        self.index += 1
        stem = f"{self.index:02d}-{name}.{self.label}"
        payload = {"note": note, "request": request, "status": status, "body": body}
        write_private(
            RAW / f"{stem}.json", json.dumps(strip_secrets(payload), indent=2, ensure_ascii=False)
        )
        clean = self.redactor.walk(payload)
        (GRAPH_DIR / f"{stem}.json").write_text(
            json.dumps(clean, indent=2, ensure_ascii=False) + "\n"
        )
        self.files.append(stem)
        return clean

    def fact(self, text: str) -> None:
        print("  *", text)
        self.notes.append(text)


def body_of(response: httpx.Response):
    try:
        return response.json()
    except ValueError:
        return {"non_json_body_length": len(response.content)}


# ---------------------------------------------------------------- config and OAuth


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_env() -> tuple[str, str, str | None]:
    """(client id, client secret, authority or None): the environment first, then
    ~/.config/beyric-outlook/keys.env, then the old docs/spike/outlook-m0/.env.m0. Never printed."""
    keys = read_env_file(KEYS_FILE)
    old = read_env_file(HERE / ".env.m0")

    def pick(name: str, old_name: str | None = None) -> str:
        return (
            os.environ.get(name)
            or keys.get(name)
            or (old.get(old_name) if old_name else None)
            or (os.environ.get(old_name) if old_name else None)
            or ""
        )

    client_id = pick("MICROSOFT_CLIENT_ID", "MS_M0_CLIENT_ID")
    secret = pick("MICROSOFT_CLIENT_SECRET", "MS_M0_CLIENT_SECRET")
    authority = pick("MICROSOFT_AUTHORITY") or None
    if not client_id or not secret:
        sys.exit(
            "Put MICROSOFT_CLIENT_ID and MICROSOFT_CLIENT_SECRET in "
            f"{KEYS_FILE} (see the top of this script)."
        )
    if authority is not None and authority not in AUTHORITIES:
        sys.exit(f"MICROSOFT_AUTHORITY must be one of {', '.join(AUTHORITIES)}.")
    return client_id, secret, authority


def redirect_uri_for(args) -> str:
    if args.redirect_uri:
        return args.redirect_uri
    return REDIRECT_LOCAL if args.redirect == "local" else REDIRECT_PROD


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def wait_for_callback(state: str, redirect_uri: str, timeout: int = 300) -> dict[str, str]:
    """Listen on localhost:8000 for the one redirect Microsoft sends back."""
    got: dict[str, str] = {}
    done = threading.Event()
    want_path = urlparse(redirect_uri).path

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != want_path:
                self.send_response(404)
                self.end_headers()
                return
            got.update({k: v[0] for k, v in parse_qs(parsed.query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Done. You can close this tab and go back to the terminal.")
            done.set()

        def log_message(self, *args):  # never log the code
            pass

    server = HTTPServer(("localhost", 8000), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if not done.wait(timeout):
            sys.exit("No redirect arrived within 5 minutes.")
    finally:
        server.shutdown()
    return check_state(got, state)


def parse_pasted_redirect(pasted: str, redirect_uri: str, state: str) -> dict[str, str]:
    """The query of the address the person copied after consenting. Only its query is read, the
    address must be the registered redirect (host and path), and the state must be ours."""
    parts = urlparse(pasted.strip())
    want = urlparse(redirect_uri)
    if (parts.scheme, parts.netloc, parts.path) != (want.scheme, want.netloc, want.path):
        sys.exit(
            "That address is not the redirect address "
            f"({want.scheme}://{want.netloc}{want.path}). Copy the whole address bar."
        )
    got = {k: v[0] for k, v in parse_qs(parts.query).items()}
    return check_state(got, state)


def paste_callback(state: str, redirect_uri: str) -> dict[str, str]:
    print(
        "After you consent, the browser lands on a page of notes.beyrictech.com that shows an error\n"
        "or 'not available'. That is expected. Copy the WHOLE address from the address bar and\n"
        "paste it here (nothing is shown while you paste), then press Enter. Be quick: the code\n"
        "works once and expires in about a minute.\n"
    )
    return parse_pasted_redirect(getpass("Pasted address > "), redirect_uri, state)


def check_state(got: dict[str, str], state: str) -> dict[str, str]:
    if not secrets.compare_digest(got.get("state", "").encode(), state.encode()):
        sys.exit("The state in the redirect does not match: stop and tell the lead.")
    return got


def decode_claims(jwt: str) -> dict:
    """Our OWN id_token's claims (allowed: the id token is for this client). Never an access token."""
    payload = jwt.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


# ---------------------------------------------------------------- the run


def graph_get(http, token, path, rec, name, note, params=None, prefer=PREFER, extra=None):
    headers = {"Authorization": f"Bearer {token}"}
    if prefer:
        headers["Prefer"] = ", ".join(prefer)
    headers.update(extra or {})
    url = path if path.startswith("https://") else f"{GRAPH}{path}"
    response = http.get(url, params=params, headers=headers)
    shown = {"GET": path, "params": params, "Prefer": prefer}
    rec.save(name, note, response.status_code, body_of(response), request=shown)
    return response


def event_row(e: dict) -> dict:
    om = e.get("onlineMeeting") or {}
    url_hosts = sorted(
        {urlparse(u).netloc for u in re.findall(r"https?://[^\s\"'<>]+", json.dumps(e))}
    )
    return {
        "isAllDay": e.get("isAllDay"),
        "isCancelled": e.get("isCancelled"),
        "isDraft": e.get("isDraft"),
        "isOrganizer": e.get("isOrganizer"),
        "response": (e.get("responseStatus") or {}).get("response"),
        "type": e.get("type"),
        "sensitivity": e.get("sensitivity"),
        "showAs": e.get("showAs"),
        "isOnlineMeeting": e.get("isOnlineMeeting"),
        "provider": e.get("onlineMeetingProvider"),
        "joinUrlHost": urlparse(om.get("joinUrl") or "").netloc or None,
        "attendees": len(e.get("attendees") or []),
        "hasSeriesMaster": bool(e.get("seriesMasterId")),
        "hosts": url_hosts,
        "startTimeZone": (e.get("start") or {}).get("timeZone"),
    }


def run(args) -> None:
    client_id, client_secret, env_authority = load_env()
    args.authority = args.authority or env_authority or "consumers"
    redirect_uri = redirect_uri_for(args)
    scope = SCOPES[args.scopes]
    rec = Recorder(args.label)
    print(
        f"M0 run: label={args.label} authority={args.authority} scopes={args.scopes} "
        f"redirect={args.redirect}"
    )
    verifier, challenge = pkce()
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    query = urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "response_mode": "query",
            "scope": scope,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        },
        quote_via=quote,
    )
    url = f"{LOGIN}/{args.authority}/oauth2/v2.0/authorize?{query}"
    print("\nOpening the Microsoft sign-in page. If it does not open, paste this into a browser:")
    print(url, "\n")
    print(
        "While the consent screen is showing: take a SCREENSHOT of it (S2) and note whether it says"
    )
    print("'unverified', 'needs admin approval' or blocks you.\n")
    webbrowser.open(url)
    if args.redirect == "paste":
        callback = paste_callback(state, redirect_uri)
    else:
        callback = wait_for_callback(state, redirect_uri)
    rec.save(
        "callback-query",
        "What Microsoft appended to the redirect (code removed).",
        None,
        {k: ("<present>" if k == "code" else v) for k, v in callback.items()},
    )
    if "error" in callback:
        rec.fact(
            f"S2: Microsoft redirected with error={callback.get('error')} "
            f"(a blocked or declined consent): see callback-query"
        )
        finish(rec, args, scope, {})
        return

    with httpx.Client(timeout=TIMEOUT, trust_env=False, follow_redirects=False) as http:
        token = http.post(
            f"{LOGIN}/{args.authority}/oauth2/v2.0/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "authorization_code",
                "code": callback["code"],
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
                "scope": scope,
            },
        )
        tbody = body_of(token)
        rec.save(
            "token-redeem",
            "S1: the code redemption answer (tokens redacted).",
            token.status_code,
            tbody,
        )
        if token.status_code != 200:
            rec.fact(
                f"S1/S6: redemption failed HTTP {token.status_code}: "
                f"{tbody.get('error')} codes={tbody.get('error_codes')}"
            )
            finish(rec, args, scope, {})
            return
        access = tbody["access_token"]
        facts = {
            "scope": tbody.get("scope"),
            "has_refresh_token": bool(tbody.get("refresh_token")),
            "expires_in": tbody.get("expires_in"),
        }
        rec.fact(f"S1: scope string returned: {tbody.get('scope')!r}")
        rec.fact(
            f"S1: refresh token present: {facts['has_refresh_token']}; expires_in={tbody.get('expires_in')}"
        )
        if tbody.get("refresh_token"):
            write_private(
                STATE / f"state-{args.label}.json",
                json.dumps(
                    {
                        "authority": args.authority,
                        "scope": scope,
                        "refresh_token": tbody["refresh_token"],
                    }
                ),
            )
        claims = decode_claims(tbody["id_token"])
        keep = {
            k: claims.get(k)
            for k in (
                "ver",
                "iss",
                "aud",
                "tid",
                "oid",
                "sub",
                "preferred_username",
                "name",
                "email",
                "iat",
                "exp",
                "nonce",
            )
        }
        rec.save(
            "id-token-claims", "S1: the ID token claims of THIS client's own token.", None, keep
        )
        tid = claims.get("tid", "")
        rec.fact(
            f"S1: tid is the consumer tenant: {tid == CONSUMER_TENANT}; has oid: {'oid' in claims}; "
            f"has sub: {'sub' in claims}; has preferred_username: {'preferred_username' in claims}; "
            f"has email claim: {'email' in claims}"
        )
        rec.fact(
            f"S1: iss == login.microsoftonline.com/{{tid}}/v2.0: {claims.get('iss') == f'{LOGIN}/{tid}/v2.0'}; "
            f"nonce matches: {claims.get('nonce') == nonce}; aud is our client id: {claims.get('aud') == client_id}"
        )

        # S4: profile and organisation with User.Read alone
        me = graph_get(http, access, "/me", rec, "me", "S4: GET /me.", prefer=None)
        rec.fact(f"S4: GET /me HTTP {me.status_code}; fields: {sorted(body_of(me))[:12]}")
        org = graph_get(
            http,
            access,
            "/organization",
            rec,
            "organization",
            "S4: GET /organization with User.Read only.",
            prefer=None,
        )
        rec.fact(
            f"S4: GET /organization HTTP {org.status_code}; "
            f"verifiedDomains returned: {'verifiedDomains' in json.dumps(body_of(org))}"
        )

        # S3: the calendar, probe first (spec 4.5), then the window with both Prefer values
        now = datetime.now(UTC)
        probe = graph_get(
            http,
            access,
            "/me/calendarView",
            rec,
            "probe",
            "4.5 probe: one event in the next hour.",
            params={
                "startDateTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "endDateTime": (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "$top": "1",
            },
        )
        rec.fact(
            f"S6: probe HTTP {probe.status_code}; error code: "
            f"{(body_of(probe).get('error') or {}).get('code') if isinstance(body_of(probe), dict) else None}"
        )
        window = {
            "startDateTime": (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endDateTime": (now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        listing = graph_get(
            http,
            access,
            "/me/calendarView",
            rec,
            "calendarview-select",
            "S3: calendarView, $select list of spec 5.1, both Prefer values.",
            params={**window, "$select": SELECT, "$top": "250"},
        )
        events = body_of(listing).get("value", []) if listing.status_code == 200 else []
        rec.fact(
            f"S3: calendarView HTTP {listing.status_code}: {len(events)} events; "
            f"nextLink present: {'@odata.nextLink' in body_of(listing)}"
        )
        sent = listing.request.headers.get_list("prefer")
        rec.fact(
            f"S3: Prefer header lines sent: {sent}; response Preference-Applied: "
            f"{listing.headers.get('preference-applied')!r}"
        )
        rec.fact(
            "S3: start/end timeZone values seen: "
            f"{sorted({(e.get('start') or {}).get('timeZone') for e in events})}"
        )
        for i, e in enumerate(events, 1):
            rec.fact(f"S3 event {i}: {json.dumps(event_row(e), sort_keys=True)}")
        # the same window with no $select: which fields exist that the list leaves out
        full = graph_get(
            http,
            access,
            "/me/calendarView",
            rec,
            "calendarview-all-fields",
            "S3: the same window with no $select (every field Graph sends).",
            params={**window, "$top": "5"},
        )
        # ImmutableId vs not: do ids differ?
        plain = graph_get(
            http,
            access,
            "/me/calendarView",
            rec,
            "calendarview-no-immutable",
            "S3: only the timezone Prefer (ids are NOT immutable).",
            params={**window, "$select": "id,subject", "$top": "5"},
            prefer=['outlook.timezone="UTC"'],
        )
        if full.status_code == 200 and plain.status_code == 200:
            a = [e["id"] for e in body_of(full).get("value", [])]
            b = [e["id"] for e in body_of(plain).get("value", [])]
            rec.fact(
                f"S3: ids differ with and without ImmutableId: {a != b} (id lengths "
                f"{[len(x) for x in a][:3]} vs {[len(x) for x in b][:3]})"
            )
        # get_event (spec 5.7) and a missing one (S6)
        if events:
            one = graph_get(
                http,
                access,
                f"/me/events/{quote(events[0]['id'], safe='')}",
                rec,
                "event-get",
                "5.7: GET /me/events/{id} with the same Prefer values.",
                params={"$select": SELECT},
            )
            rec.fact(f"5.7: get_event HTTP {one.status_code}")
            fake = events[0]["id"][:-4] + (
                "AAAA" if not events[0]["id"].endswith("AAAA") else "BBBB"
            )
            miss = graph_get(
                http,
                access,
                f"/me/events/{quote(fake, safe='')}",
                rec,
                "event-missing",
                "S6: GET of an event id that does not exist.",
            )
            rec.fact(
                f"S6: missing event HTTP {miss.status_code}; code "
                f"{(body_of(miss).get('error') or {}).get('code')}"
            )
        # S5: safe links anywhere in the window
        raw_text = json.dumps(body_of(full)) + json.dumps(events)
        rec.fact(
            "S5: a safelinks.protection.outlook.com link appears in the events: "
            f"{'safelinks.protection.outlook.com' in raw_text}"
        )
        # S6: bad token and bad refresh
        bad = graph_get(
            http,
            "not-a-real-token",
            "/me",
            rec,
            "bad-access-token",
            "S6: a made-up access token.",
            prefer=None,
        )
        rec.fact(
            f"S6: bad access token HTTP {bad.status_code}; code "
            f"{(body_of(bad).get('error') or {}).get('code')}"
        )
        junk = http.post(
            f"{LOGIN}/{tid or args.authority}/oauth2/v2.0/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": "0." + "A" * 40,
                "scope": scope,
            },
        )
        jb = body_of(junk)
        rec.save(
            "refresh-with-garbage",
            "S6: refresh with a made-up refresh token.",
            junk.status_code,
            jb,
        )
        rec.fact(
            f"S6: garbage refresh HTTP {junk.status_code}: error={jb.get('error')} codes={jb.get('error_codes')}"
        )
        # S1 follow-up: a real refresh, to see rotation and the scope string again
        if tbody.get("refresh_token"):
            ref = http.post(
                f"{LOGIN}/{tid or args.authority}/oauth2/v2.0/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": tbody["refresh_token"],
                    "scope": scope,
                },
            )
            rb = body_of(ref)
            rec.save(
                "refresh-real",
                "S1: a real refresh (rotation: is a NEW refresh token returned?).",
                ref.status_code,
                rb,
            )
            rec.fact(
                f"S1: refresh HTTP {ref.status_code}; new refresh token returned: "
                f"{bool(rb.get('refresh_token'))}; differs from the old: "
                f"{rb.get('refresh_token') != tbody['refresh_token']}; scope: {rb.get('scope')!r}; "
                f"id_token in refresh answer: {'id_token' in rb}"
            )
            if rb.get("refresh_token"):
                write_private(
                    STATE / f"state-{args.label}.json",
                    json.dumps(
                        {
                            "authority": args.authority,
                            "scope": scope,
                            "tid": tid,
                            "refresh_token": rb["refresh_token"],
                        }
                    ),
                )
    finish(rec, args, scope, facts)


def after_revoke(args) -> None:
    client_id, client_secret, _ = load_env()
    path = STATE / f"state-{args.label}.json"
    if not path.exists():
        sys.exit("No saved state: run `run` for this label first.")
    state = json.loads(path.read_text())
    rec = Recorder(args.label + "-after-revoke")
    tid = state.get("tid") or state["authority"]
    with httpx.Client(timeout=TIMEOUT, trust_env=False, follow_redirects=False) as http:
        ref = http.post(
            f"{LOGIN}/{tid}/oauth2/v2.0/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": state["refresh_token"],
                "scope": state["scope"],
            },
        )
        rb = body_of(ref)
        rec.save(
            "refresh-after-revoke",
            "S6: refresh after the app was removed at Microsoft.",
            ref.status_code,
            rb,
        )
        rec.fact(
            f"S6: refresh after removing the app: HTTP {ref.status_code}; error={rb.get('error')}; "
            f"error_codes={rb.get('error_codes')} (700082 would mean idle expiry)"
        )
    finish(rec, args, state["scope"], {})


def finish(rec: Recorder, args, scope: str, facts: dict) -> None:
    answers = {}
    if args.cmd == "run":
        print("\nA few answers only you can give (press Enter to skip):")
        answers["consent_screen"] = input(
            "S2 What did the consent screen say? (unverified notice? blocked?) > "
        )
        answers["permissions_page_url"] = input(
            "S7 URL of the page where you can REMOVE this app's access (account.live.com/... or myapps...) > "
        )
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"\n## Run `{rec.label}` ({stamp})\n",
        f"authority `{getattr(args, 'authority', '-')}`, scopes `{scope}`\n",
        "### Facts read from the real answers\n",
    ]
    lines += [f"- {n}" for n in rec.notes]
    if answers:
        lines += ["\n### Reported by the person (not machine-read)\n"]
        lines += [f"- {k}: {rec.redactor.text(v) or '(skipped)'}" for k, v in answers.items()]
    lines += ["\n### Redacted files\n"]
    lines += [f"- `docs/fe-captures/calendar-outlook/graph/{f}.json`" for f in rec.files]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    if not REPORT.exists():
        REPORT.write_text(
            "# Outlook M0 live captures\n\nGenerated by docs/spike/outlook-m0/capture.py "
            "from real Microsoft answers. Redacted. See that folder's README.\n"
        )
    with REPORT.open("a") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"\nWrote {len(rec.files)} redacted files and appended to {REPORT.relative_to(ROOT)}")


# ---------------------------------------------------------------- selftest


def selftest() -> None:
    """No network and no secret: redaction and PKCE behave."""
    r = Redactor()
    sample = {
        "access_token": "abc",
        "refresh_token": "def",
        "scope": f"openid {G}Calendars.Read",
        "subject": "Quarterly planning",
        "organizer": {"emailAddress": {"address": "Ada@Corp.com", "name": "Ada L"}},
        "onlineMeeting": {
            "joinUrl": "https://teams.microsoft.com/l/meetup-join/19%3ameeting_ZGVhZGJlZWZkZWFkYmVlZg%40thread.v2/0?context=%7b%22Tid%22%3a%22x%22%7d"
        },
        "body": {
            "content": "<a href='https://us02web.zoom.us/j/81234567890?pwd=SECRETPASS'>Join</a> ada@corp.com"
        },
        "location": {"displayName": "https://us02web.zoom.us/j/81234567890?pwd=SECRETPASS"},
        "tid": CONSUMER_TENANT,
        "oid": "11111111-2222-3333-4444-555555555555",
        "start": {"dateTime": "2026-11-05T10:00:00.0000000", "timeZone": "UTC"},
    }
    out = json.dumps(r.walk(sample))
    for leak in (
        'abc"',
        'def"',
        "Ada@Corp",
        "ada@corp",
        "SECRETPASS",
        "81234567890",
        "11111111-2222",
        "ZGVhZGJlZWZkZWFkYmVlZg",
        "Quarterly",
    ):
        assert leak not in out, f"leaked {leak}: {out}"
    assert CONSUMER_TENANT in out and "teams.microsoft.com/l/meetup-join/" in out
    assert "person-1@example.test" in out and "00000000-0000-0000-0000-000000000001" in out
    assert "%40thread.v2" in out and "us02web.zoom.us/j/<num:11>" in out
    verifier, challenge = pkce()
    assert len(verifier) >= 43 and "=" not in challenge
    jwt = (
        "x."
        + base64.urlsafe_b64encode(json.dumps({"tid": "t"}).encode()).decode().rstrip("=")
        + ".y"
    )
    assert decode_claims(jwt) == {"tid": "t"}

    # the pasted redirect: only the registered address with OUR state is accepted
    state = "st4te"
    good = f"{REDIRECT_PROD}?code=0.ABC&state={state}&session_state=x"
    assert parse_pasted_redirect(good, REDIRECT_PROD, state)["code"] == "0.ABC"
    assert parse_pasted_redirect(f"  {good}  ", REDIRECT_PROD, state)["state"] == state
    for bad in (
        f"https://evil.example/api/calendar/microsoft/callback?code=c&state={state}",
        f"https://notes.beyrictech.com/other?code=c&state={state}",
        f"{REDIRECT_PROD}?code=c&state=wrong",
        f"{REDIRECT_PROD}?code=c",
        "not a url",
    ):
        try:
            parse_pasted_redirect(bad, REDIRECT_PROD, state)
        except SystemExit:
            continue
        raise AssertionError(f"accepted a bad pasted redirect: {bad}")
    denied = parse_pasted_redirect(
        f"{REDIRECT_PROD}?error=access_denied&state={state}", REDIRECT_PROD, state
    )
    assert denied["error"] == "access_denied" and "code" not in denied

    # keys: a file's values are read, comments skipped, quotes stripped
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "keys.env"
        f.write_text("# c\nMICROSOFT_CLIENT_ID='abc'\nMICROSOFT_AUTHORITY=consumers\n")
        assert read_env_file(f) == {
            "MICROSOFT_CLIENT_ID": "abc",
            "MICROSOFT_AUTHORITY": "consumers",
        }
        assert read_env_file(Path(tmp) / "missing.env") == {}
    print("selftest ok")
    print(json.dumps(r.walk(sample), indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("run", "after-revoke"):
        p = sub.add_parser(name)
        p.add_argument("--label", required=True, help="personal, work, work-nomailbox, ...")
        if name == "run":
            p.add_argument(
                "--authority",
                choices=AUTHORITIES,
                default=None,
                help="default: MICROSOFT_AUTHORITY from the keys file, else consumers",
            )
            p.add_argument("--scopes", choices=sorted(SCOPES), default="read")
            p.add_argument(
                "--redirect",
                choices=["local", "paste"],
                default="local",
                help="local: catch the redirect on localhost:8000; paste: you paste the address "
                "bar after consenting (for a registration with only the production redirect)",
            )
            p.add_argument(
                "--redirect-uri",
                default=None,
                help="override the redirect URI sent to Microsoft (must match the registration)",
            )
    sub.add_parser("selftest")
    args = parser.parse_args()
    if args.cmd == "selftest":
        selftest()
    elif args.cmd == "run":
        run(args)
    else:
        after_revoke(args)


if __name__ == "__main__":
    main()
