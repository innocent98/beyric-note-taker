"""Shared helpers for the P0.2 instant-Meet spike. Stdlib only, throwaway.

Everything secret lives in ./local/ (git-ignored): client_secret.json (you save it),
token.json (auth.py writes it), state.json, events.jsonl and log/ (redacted request logs).
"""
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
LOCAL = HERE / "local"
# SPIKE_ACCOUNT=ws python3 ... runs the whole flow for a second account (the Workspace one, S3)
# with its own token, state, logs, topic and subscription, so the two accounts never mix events.
ACCOUNT = os.environ.get("SPIKE_ACCOUNT", "")
DATA = LOCAL / ACCOUNT if ACCOUNT else LOCAL
CLIENT_FILE = LOCAL / "client_secret.json"
TOKEN_FILE = DATA / "token.json"
STATE_FILE = DATA / "state.json"
EVENTS_FILE = DATA / "events.jsonl"
S6_FILE = DATA / "s6.jsonl"
LOG_DIR = DATA / "log"

_SUFFIX = f"-{ACCOUNT}" if ACCOUNT else ""
TOPIC_ID = f"meet-spike-events{_SUFFIX}"
SUB_ID = f"meet-spike-events-pull{_SUFFIX}"
MEET_SERVICE_ACCOUNT = "meet-api-event-push@system.gserviceaccount.com"
STARTED = "google.workspace.meet.conference.v2.started"

SCOPE_BASE = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/pubsub",  # spike only: lets this script create topic, subscription, pull
    "https://www.googleapis.com/auth/meetings.space.readonly",
]
SCOPE_CREATED = "https://www.googleapis.com/auth/meetings.space.created"

TOKEN_URL = "https://oauth2.googleapis.com/token"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


# ---------- redaction: nothing secret or personal reaches a log or the note ----------
_PATTERNS = [
    (re.compile(r"ya29\.[\w\-.]+"), "<access-token>"),
    (re.compile(r"1//[\w\-.]+"), "<refresh-token>"),
    (re.compile(r'("(?:access_token|refresh_token|id_token|client_secret|code|code_verifier)"\s*:\s*")[^"]+'), r"\1<redacted>"),
    (re.compile(r"[\w.+\-]+@(?!system\.gserviceaccount\.com)[\w\-]+(?:\.[\w\-]+)+"), "<email>"),
    (re.compile(r"\b(\d{3})\d{10,}(\d{2})\b"), lambda m: f"{m.group(1)}…{m.group(2)}(id)"),
]


def redact(text):
    for pat, rep in _PATTERNS:
        text = pat.sub(rep, text)
    return text


# ---------- config / token ----------
def load_client():
    if not CLIENT_FILE.exists():
        die(f"{CLIENT_FILE} not found. Save the OAuth client JSON from the Cloud console there (runbook step 4).")
    raw = json.loads(CLIENT_FILE.read_text())
    c = raw.get("installed") or raw.get("web")
    if not c:
        die("client_secret.json has neither 'installed' nor 'web'; download the Desktop app client JSON.")
    return c


def project_id():
    return load_client()["project_id"]


def topic_path():
    return f"projects/{project_id()}/topics/{TOPIC_ID}"


def sub_path():
    return f"projects/{project_id()}/subscriptions/{SUB_ID}"


def load_state():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def save_state(**kw):
    s = load_state()
    s.update(kw)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(s, indent=2))


def write_private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def _token():
    if not TOKEN_FILE.exists():
        die(f"{TOKEN_FILE} not found. Run auth.py first (runbook step 5).")
    return json.loads(TOKEN_FILE.read_text())


def refresh(tok):
    c = load_client()
    data = urllib.parse.urlencode({
        "client_id": c["client_id"], "client_secret": c["client_secret"],
        "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=data), timeout=30) as r:
            body = json.loads(r.read())
    except urllib.error.HTTPError as e:
        err = redact(e.read().decode(errors="replace"))
        die(f"token refresh failed (HTTP {e.code}): {err}\n"
            "If this says invalid_grant, the 7-day Testing-mode refresh token has expired: re-run auth.py.")
    tok["access_token"] = body["access_token"]
    tok["expires_at"] = time.time() + body.get("expires_in", 3600) - 60
    write_private(TOKEN_FILE, json.dumps(tok, indent=2))
    return tok


def access_token():
    tok = _token()
    if time.time() >= tok.get("expires_at", 0):
        tok = refresh(tok)
    return tok["access_token"]


# ---------- the one HTTP helper: every call is logged, redacted ----------
def call(step, method, url, body=None, params=None, log=True, timeout=130):
    """Return (status, parsed_json_or_text). Never raises on HTTP errors."""
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {access_token()}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, raw = r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read().decode(errors="replace")
    except Exception as e:  # network trouble: report, do not crash a long-running watcher
        status, raw = 0, f"{type(e).__name__}: {e}"
    elapsed = time.time() - t0
    try:
        parsed = json.loads(raw) if raw else {}
    except ValueError:
        parsed = raw
    if log:
        write_log(step, method, url, body, status, parsed, elapsed)
    return status, parsed


def write_log(step, method, url, body, status, parsed, elapsed):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    pretty = json.dumps(parsed, indent=2) if not isinstance(parsed, str) else parsed
    req_body = json.dumps(body, indent=2) if body is not None else ""
    entry = (f"=== {now_iso()}  [{step}]  ({elapsed:.2f}s)\n"
             f"REQUEST  {method} {url}\n"
             f"         Authorization: Bearer <redacted>\n"
             f"{redact(req_body)}\n"
             f"RESPONSE HTTP {status}\n{redact(pretty)}\n\n")
    with open(LOG_DIR / f"{step}.log", "a") as f:
        f.write(entry)
    print(f"[{step}] {method} {redact(url)} -> HTTP {status}")


def append_event(kind, **fields):
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(EVENTS_FILE, "a") as f:
        f.write(json.dumps({"kind": kind, "at": now_iso(), **fields}) + "\n")


def show(parsed):
    print(redact(json.dumps(parsed, indent=2) if not isinstance(parsed, str) else parsed))


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))
