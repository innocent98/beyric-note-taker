"""Shared helpers for the start-an-instant-meeting test. Standard library only.

Everything secret (client secret, tokens) lives under `local/`, which is git-ignored. Nothing
here prints a token, the client secret or an e-mail address.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCAL = HERE / "local"

SCOPE_CREATED = "https://www.googleapis.com/auth/meetings.space.created"
SCOPES = [SCOPE_CREATED, "openid", "email"]
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SPACES_URL = "https://meet.googleapis.com/v2/spaces"

# `SPIKE_ACCOUNT=ws python3 auth.py` keeps a second account's token apart from the first.
ACCOUNT = os.environ.get("SPIKE_ACCOUNT", "main")
if not re.fullmatch(r"[a-z0-9_-]{1,20}", ACCOUNT):
    raise SystemExit("SPIKE_ACCOUNT may only hold lowercase letters, digits, - and _")

TOKEN_FILE = LOCAL / f"token-{ACCOUNT}.json"
SPACES_LOG = LOCAL / f"spaces-{ACCOUNT}.jsonl"

MEET_CODE = re.compile(r"[a-z]+-[a-z]+-[a-z]+")
ACCESS_TYPES = {"default": None, "open": "OPEN", "trusted": "TRUSTED", "restricted": "RESTRICTED"}


class SpikeError(Exception):
    """A problem to show the person running the test in plain words, without a stack trace."""


# --- secrets and redaction -------------------------------------------------------------------


def load_client() -> dict:
    path = LOCAL / "client_secret.json"
    if not path.exists():
        raise SpikeError(
            f"{path} is missing. Runbook step 4: download the OAuth client JSON and save it "
            "under exactly that name."
        )
    try:
        data = json.loads(path.read_text())
        client = data["installed"]
        client["client_id"], client["client_secret"]  # noqa: B018 (checks both keys exist)
    except (ValueError, KeyError, TypeError) as exc:
        raise SpikeError(
            "client_secret.json is not a Desktop-app OAuth client (it has no 'installed' "
            "section). In the console create a client of type 'Desktop app' and download again."
        ) from exc
    return client


_SECRET_SHAPES = [
    re.compile(r"ya29\.[\w-]+"),  # Google access tokens
    re.compile(r"1//[\w-]+"),  # Google refresh tokens
    re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+"),  # JWTs (the ID token)
    re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+"),  # e-mail addresses
    re.compile(r"GOCSPX-[\w-]+"),  # Google client secrets
]


def redact(text: str) -> str:
    for shape in _SECRET_SHAPES:
        text = shape.sub("[redacted]", text)
    return text


def save_private(path: Path, data: dict) -> None:
    """Write JSON readable by this user only."""
    path.parent.mkdir(exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(data, handle, indent=2)


# --- HTTP ------------------------------------------------------------------------------------


def request(
    method: str, url: str, *, headers: dict | None = None, body: bytes | None = None
) -> tuple[int, str]:
    """One HTTP call; returns (status, body text) for any answer, raises SpikeError if the
    network itself fails."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SpikeError(f"Could not reach {urllib.parse.urlsplit(url).netloc}: {exc}") from exc


def post_form(url: str, fields: dict) -> tuple[int, dict]:
    status, text = request(
        "POST",
        url,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=urllib.parse.urlencode(fields).encode(),
    )
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = {"error": "not_json", "error_description": redact(text[:200])}
    return status, parsed


# --- tokens ----------------------------------------------------------------------------------


def access_token() -> str:
    """A valid access token for the saved account, refreshed when it is about to expire."""
    if not TOKEN_FILE.exists():
        raise SpikeError(f"No token for account '{ACCOUNT}'. Run: python3 auth.py")
    token = json.loads(TOKEN_FILE.read_text())
    if token.get("expires_at", 0) > time.time() + 60:
        return token["access_token"]
    client = load_client()
    status, answer = post_form(
        TOKEN_URL,
        {
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "refresh_token": token["refresh_token"],
            "grant_type": "refresh_token",
        },
    )
    if status != 200 or "access_token" not in answer:
        raise SpikeError(
            f"Google would not refresh the token ({answer.get('error')}: "
            f"{answer.get('error_description')}). Run python3 auth.py again."
        )
    token["access_token"] = answer["access_token"]
    token["expires_at"] = time.time() + int(answer.get("expires_in", 3600))
    save_private(TOKEN_FILE, token)
    return token["access_token"]


def account_kind(id_token: str) -> str:
    """Say what kind of Google account signed in, from the ID token Google just issued.

    A Workspace account carries an `hd` (hosted domain) claim; a consumer account does not.
    Read for display only; the token came straight from Google over TLS, so it is not verified
    again. The address itself is never returned.
    """
    try:
        payload = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError, TypeError):
        return "unknown (no readable ID token)"
    if claims.get("hd"):
        return "Google Workspace account (the token has an hd claim)"
    if str(claims.get("email", "")).lower().endswith("@gmail.com"):
        return "personal Gmail account (gmail.com, no hd claim)"
    return "consumer Google Account on another domain (no hd claim)"


# --- the space -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Space:
    name: str
    meeting_uri: str
    meeting_code: str
    access_type: str | None
    entry_point_access: str | None


def parse_space(body: object) -> Space:
    """Read a `Space` answer from `spaces.create` or `spaces.get`. Strict about the three fields
    the bot test needs (name, link, code); tolerant of `config` pieces Google leaves out."""
    if not isinstance(body, dict):
        raise ValueError("the answer is not a JSON object")
    name = body.get("name")
    uri = body.get("meetingUri")
    if not isinstance(name, str) or not name.startswith("spaces/"):
        raise ValueError("no 'name' of the form spaces/...")
    if not isinstance(uri, str):
        raise ValueError("no 'meetingUri'")
    parts = urllib.parse.urlsplit(uri)
    if parts.scheme != "https" or parts.hostname != "meet.google.com":
        raise ValueError("'meetingUri' is not an https://meet.google.com link")
    code = body.get("meetingCode") or parts.path.strip("/")
    if not isinstance(code, str) or not MEET_CODE.fullmatch(code):
        raise ValueError("no meeting code of the form abc-defg-hij")
    if parts.path.strip("/") != code:
        raise ValueError("'meetingUri' and 'meetingCode' disagree")
    config = body.get("config") or {}
    if not isinstance(config, dict):
        raise ValueError("'config' is not an object")
    return Space(
        name=name,
        meeting_uri=uri,
        meeting_code=code,
        access_type=config.get("accessType"),
        entry_point_access=config.get("entryPointAccess"),
    )


def explain_error(status: int, text: str) -> str:
    """Turn a Google API error into one plain paragraph. Always keeps Google's own words so the
    exact message can be recorded."""
    try:
        error = json.loads(text)["error"]
        message = str(error.get("message", ""))
        google_status = str(error.get("status", ""))
        details = [d for d in error.get("details", []) if isinstance(d, dict)]
    except (ValueError, KeyError, TypeError, AttributeError):
        return f"Google answered HTTP {status} with text I could not read: {redact(text[:300])}"
    reason = next((d.get("reason") for d in details if d.get("reason")), "")
    metadata = next((d.get("metadata") for d in details if d.get("metadata")), {}) or {}
    quoted = f'Google said: "{redact(message)}" (HTTP {status} {google_status} {reason})'.strip()

    if reason in ("SERVICE_DISABLED", "API_DISABLED") or "has not been used in project" in message:
        link = metadata.get("activationUrl") or "the Meet REST API page in the console Library"
        return (
            "The Meet REST API is not switched on in this project (or was switched on a minute "
            f"ago and has not caught up: wait 2 minutes and retry). Open {link} and click Enable. "
            "If that page asks for billing, STOP and tell me exactly what it says. " + quoted
        )
    if reason == "ACCESS_TOKEN_SCOPE_INSUFFICIENT" or "insufficient authentication scopes" in (
        message.lower()
    ):
        return (
            "The saved token was not granted the meeting-creation permission. Run "
            "python3 auth.py again and leave every box ticked. " + quoted
        )
    if status == 401:
        return "Google did not accept the token. Run python3 auth.py again. " + quoted
    if status == 403:
        return (
            "Google REFUSED to create a meeting for this account. This is the answer to "
            "'can this account type create a space'. Record the quote below word for word, "
            "and which account (personal Gmail or Workspace) it was. " + quoted
        )
    if status == 429:
        return "Google says too many requests. Wait a few minutes. " + quoted
    return f"Google refused the request. {quoted}"


def log_space(space: Space, requested: str, action: str) -> None:
    """Append one line to local/spaces-<account>.jsonl: what we asked for and what came back."""
    LOCAL.mkdir(exist_ok=True)
    row = {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "action": action,
        "requested_access": requested,
        "returned_access": space.access_type,
        "entry_point_access": space.entry_point_access,
        "code": space.meeting_code,
    }
    with SPACES_LOG.open("a") as handle:
        handle.write(json.dumps(row) + "\n")


# --- the bot ---------------------------------------------------------------------------------

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
TIMELINE_LINE = re.compile(
    r"t\+\s*(?P<t>[\d.]+)s status=(?P<status>\S+) reason=(?P<reason>.*?) stage="
)


def require_local_vexa(base_url: str) -> str:
    """Refuse any Vexa that is not on this machine: this test must never send a bot from the
    production server."""
    parts = urllib.parse.urlsplit(base_url)
    if parts.scheme != "http" or parts.hostname not in LOCAL_HOSTS:
        raise SpikeError(
            f"{base_url} is not a Vexa on this Mac. This test only talks to http://localhost. "
            "It must never send a bot from the production server."
        )
    return base_url.rstrip("/")


def read_vexa_key(path: Path) -> str:
    """The Vexa API key from `spike/.env.api`: a bare key on one line, or KEY=value lines."""
    if not path.exists():
        raise SpikeError(f"No Vexa key file at {path} (pass --key-file).")
    lines = [ln.strip() for ln in path.read_text().splitlines() if ln.strip()]
    lines = [ln for ln in lines if not ln.startswith("#")]
    if len(lines) == 1 and "=" not in lines[0]:
        return lines[0]
    pairs = dict(ln.split("=", 1) for ln in lines if "=" in ln)
    for name in ("VEXA_API_KEY", "API_KEY", "X_API_KEY"):
        if pairs.get(name):
            return pairs[name].strip().strip("'\"")
    raise SpikeError(
        f"Cannot tell which line of {path} is the key. Its names are: {', '.join(sorted(pairs))}. "
        "Pass --key-file with a file that holds only the key."
    )


def parse_timeline(lines: list[str]) -> list[tuple[float, str, str | None]]:
    """Read `t+ 18.5s status=awaiting_admission reason=None stage=None` lines (the format of
    the E2 experiment's logs and of `send_bot.py`) into (seconds, status, reason) events,
    keeping only the lines where the status changed."""
    events: list[tuple[float, str, str | None]] = []
    for line in lines:
        match = TIMELINE_LINE.search(line)
        if not match:
            continue
        reason = match["reason"].strip()
        status = match["status"]
        if events and events[-1][1] == status:
            continue
        events.append((float(match["t"]), status, None if reason == "None" else reason))
    return events


def summarise_timeline(events: list[tuple[float, str, str | None]]) -> dict:
    """Read one bot's status changes into what the test records.

    `waited` means the bot reached `awaiting_admission` (it knocked). On the old v0.12.28 image
    that status is not proof of a real waiting room, so this only means something against
    v0.12.29, the image production runs. The person's own answer ("did you see a wants-to-join
    prompt?") is the ground truth; this is the corroboration.
    """
    first = {}
    for seconds, status, _ in events:
        first.setdefault(status, seconds)
    final_status, final_reason = (events[-1][1], events[-1][2]) if events else ("none", None)
    waited = "awaiting_admission" in first
    refused = any(
        status == "failed" and (reason or "").startswith("google_meet_join_refused")
        for _, status, reason in events
    )
    if refused:
        outcome = "refused"
        plain = "Google refused the bot's join before any host could see it (typed refusal)."
    elif "active" in first and not waited:
        outcome = "walked_in"
        plain = "The bot went straight in with no waiting state: it did not need to knock."
    elif "active" in first:
        outcome = "knocked_then_in"
        plain = (
            f"The bot knocked (waiting room at {first['awaiting_admission']:.0f}s) and was let "
            f"in at {first['active']:.0f}s: a person had to admit it."
        )
    elif waited and final_status in ("failed", "completed"):
        outcome = "knocked_not_admitted"
        plain = f"The bot knocked and was never let in (ended {final_status}, {final_reason})."
    elif waited:
        outcome = "knocked_waiting"
        plain = "The bot knocked and was still waiting when we stopped it: nobody admitted it."
    else:
        outcome = "no_result"
        plain = f"The bot never got as far as a waiting room (last status: {final_status})."
    return {
        "outcome": outcome,
        "knocked": waited,
        "seconds_to_waiting_room": first.get("awaiting_admission"),
        "seconds_to_active": first.get("active"),
        "plain": plain,
    }
