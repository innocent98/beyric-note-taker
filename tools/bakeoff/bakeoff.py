"""Bot bake-off runner: count how often Skribby and Recall bots get into a Google Meet.

Runs on the owner's Mac, standard library only, and talks to nothing but the two vendors' APIs.
It never calls notes.beyrictech.com and never reads production data.

    python3 tools/bakeoff/bakeoff.py run --arm skribby-guest --meet-url https://meet.google.com/abc-defg-hij
    python3 tools/bakeoff/bakeoff.py note --arm vexa --meet-url ... --result admitted
    python3 tools/bakeoff/bakeoff.py report

See tools/bakeoff/README.md for the plain-words steps.
"""

from __future__ import annotations

import argparse
import collections
import csv
import dataclasses
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:  # Lagos has no daylight saving: a fixed +1 hour is exact if the zone database is missing
    from zoneinfo import ZoneInfo

    LAGOS = ZoneInfo("Africa/Lagos")
except Exception:  # pragma: no cover - depends on the machine
    LAGOS = timezone(timedelta(hours=1), "WAT")
UTC = timezone(timedelta(0), "UTC")

RUN_ARMS = ("skribby-guest", "skribby-auth", "recall-guest")
ALL_ARMS = ("vexa",) + RUN_ARMS
ARM_VENDOR = {
    "vexa": None,
    "skribby-guest": "skribby",
    "skribby-auth": "skribby",
    "recall-guest": "recall",
}
HOST_TYPES = ("self", "other", "external")
NOTE_RESULTS = ("admitted", "knocked-then-admitted", "refused", "never-joined")
RESULTS = NOTE_RESULTS + ("error",)  # error = our side or the vendor's API, not Google's answer
# Plain, different, human-looking names (the lead's protocol). The signed-in Skribby bot shows the
# Google account's own name instead; Skribby still wants the field, so it gets a plain one.
BOT_NAMES = {"skribby-guest": "Ada W", "skribby-auth": "Jo T", "recall-guest": "Sam K"}

CSV_COLUMNS = [
    "time_utc",
    "time_lagos",
    "trial_id",
    "arm",
    "host_type",
    "meeting_code",
    "result",
    "seconds_to_lobby",
    "seconds_to_admission",
    "final_status",
    "bot_minutes",
    "notes",
]
DEFAULT_LOG = Path(__file__).resolve().parent / "logs" / "bakeoff.csv"
DEFAULT_KEYS_FILE = Path.home() / ".config" / "beyric-bakeoff" / "keys.env"
KEY_NAMES = (
    "SKRIBBY_API_KEY",
    "SKRIBBY_ACCOUNT_ID",
    "SKRIBBY_REGION",
    "RECALL_API_KEY",
    "RECALL_REGION",
)
SECRET_KEYS = ("SKRIBBY_API_KEY", "RECALL_API_KEY")

DEFAULT_LOBBY_LIMIT = 600  # seconds the bot may wait at the door
DEFAULT_STAY = 60  # seconds in the call after admission, then we ask the vendor to leave
DEFAULT_CAP_HOURS = 4.5  # per vendor; both free allowances are 5 hours
DEFAULT_KNOCK_THRESHOLD = (
    20  # Skribby only: seconds in `joining` that we read as "someone admitted it"
)
POST_LEAVE_GRACE = 120  # seconds we keep watching after asking the bot to leave
MAX_POLL_FAILURES = 5
BOT_DETECTED_QUIET = 90  # Skribby retries after bot_detected; this long unchanged means it gave up
RECALL_REGIONS = ("us-west-2", "us-east-1", "eu-central-1", "ap-northeast-1")
SKRIBBY_BASES = {
    "eu": "https://platform.skribby.io/api/v1",
    "jp": "https://platform-jp.skribby.io/api/v1",
}
MEET_URL = re.compile(
    r"^https://meet\.google\.com/(?:meet/)?([a-z]{3}-[a-z]{4}-[a-z]{3})(?:[/?#].*)?$"
)


class BakeoffError(Exception):
    """A problem the owner can fix (bad option, missing key). Printed plainly, no traceback."""


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


class NetworkError(Exception):
    pass


# --------------------------------------------------------------------------------------------
# Keys and redaction
# --------------------------------------------------------------------------------------------


def load_keys(env=None, keys_file=None) -> dict:
    """Environment variables win over the keys file. Nothing here ever prints a value."""
    env = os.environ if env is None else env
    path = Path(keys_file) if keys_file else Path(env.get("BAKEOFF_KEYS_FILE") or DEFAULT_KEYS_FILE)
    found: dict = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            name = name.strip().removeprefix("export ").strip()
            value = value.strip().strip("'\"")
            if name in KEY_NAMES and value:
                found[name] = value
    for name in KEY_NAMES:
        if env.get(name):
            found[name] = env[name]
    return found


SENSITIVE_HEADERS = {"authorization", "x-api-key", "cookie", "proxy-authorization"}
_TOKEN_PATTERN = re.compile(r"(?i)\b(bearer|token)\s+[A-Za-z0-9._~+/=-]{8,}")


def redact_headers(headers: dict) -> dict:
    return {k: ("[redacted]" if k.lower() in SENSITIVE_HEADERS else v) for k, v in headers.items()}


def scrub(text: str, secrets=()) -> str:
    """Remove every known secret and any 'Bearer ...' or 'Token ...' string from text."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return _TOKEN_PATTERN.sub(lambda m: f"{m.group(1)} [redacted]", text)


# --------------------------------------------------------------------------------------------
# HTTP layer (swapped for a fake in the tests)
# --------------------------------------------------------------------------------------------


@dataclasses.dataclass
class Request:
    method: str
    url: str
    headers: dict
    body: dict | None = None


@dataclasses.dataclass
class Response:
    status: int
    headers: dict
    text: str

    def json(self):
        try:
            return json.loads(self.text) if self.text.strip() else {}
        except ValueError:
            return {}


class UrllibTransport:
    """The real network. One place, so the tests can replace it."""

    def send(self, req: Request, timeout: float = 30.0) -> Response:
        data = json.dumps(req.body).encode() if req.body is not None else None
        headers = {"User-Agent": "beyric-bakeoff/1.0", "Accept": "application/json", **req.headers}
        if data is not None:
            headers.setdefault("Content-Type", "application/json")
        http_request = urllib.request.Request(
            req.url, data=data, method=req.method, headers=headers
        )
        try:
            with urllib.request.urlopen(http_request, timeout=timeout) as resp:  # noqa: S310
                return Response(
                    resp.status, _lower(resp.headers), resp.read().decode("utf-8", "replace")
                )
        except urllib.error.HTTPError as exc:
            return Response(exc.code, _lower(exc.headers), exc.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise NetworkError(str(exc)) from exc


def _lower(headers) -> dict:
    return {k.lower(): v for k, v in dict(headers).items()}


class Client:
    """Sends a request politely: waits out 429 and busy answers, never hammers."""

    RETRY_BUSY = (409, 502, 503, 504, 507)

    def __init__(self, transport, sleep, secrets=()):
        self.transport = transport
        self.sleep = sleep
        self.secrets = tuple(secrets)

    def call(self, req: Request, expect=(200, 201, 202, 204)) -> Response:
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = self.transport.send(req)
            except NetworkError as exc:
                if attempt >= 4:
                    raise NetworkError(scrub(str(exc), self.secrets)) from exc
                self.sleep(2**attempt)
                continue
            if resp.status in expect:
                return resp
            if resp.status == 429 and attempt <= 6:
                self.sleep(_retry_after(resp, 5.0))
                continue
            if resp.status in self.RETRY_BUSY and attempt <= 4:
                self.sleep(_retry_after(resp, 30.0 if resp.status == 507 else 3.0 * attempt))
                continue
            raise ApiError(resp.status, scrub(resp.text.strip()[:400], self.secrets))


def _retry_after(resp: Response, default: float) -> float:
    try:
        return min(max(float(resp.headers.get("retry-after", default)), 1.0), 120.0)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------------------------
# Time helpers
# --------------------------------------------------------------------------------------------

_TS = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?$")


def parse_ts(value) -> datetime | None:
    """ISO-8601 to an aware datetime. Python 3.9 cannot read 'Z' or 7 decimals, so do it here."""
    if not isinstance(value, str):
        return None
    m = _TS.match(value.strip())
    if not m:
        return None
    day, hms, frac, zone = m.groups()
    if zone in (None, "Z"):
        tz = UTC
    else:
        sign = 1 if zone[0] == "+" else -1
        digits = zone[1:].replace(":", "")
        tz = timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:])))
    stamp = datetime.strptime(f"{day} {hms}", "%Y-%m-%d %H:%M:%S")
    return stamp.replace(microsecond=int((frac or "0")[:6].ljust(6, "0")), tzinfo=tz)


# --------------------------------------------------------------------------------------------
# What a status poll means
# --------------------------------------------------------------------------------------------


@dataclasses.dataclass
class Event:
    code: str
    sub: str | None
    at: float  # seconds since the vendor's own first timestamp (clock skew cannot touch it)


@dataclasses.dataclass
class Snapshot:
    events: list
    status: str
    sub: str | None
    end_at: float | None = None  # when the vendor says the bot finished, in the same seconds


@dataclasses.dataclass
class Outcome:
    result: str
    final_status: str
    seconds_to_lobby: float | None
    seconds_to_admission: float | None
    notes: list


@dataclasses.dataclass
class Context:
    knock_threshold: float = DEFAULT_KNOCK_THRESHOLD
    lobby_limit_hit: bool = False
    interrupted: bool = False


def first_at(events, phases, phase_of, before=None):
    for e in events:
        if phase_of(e.code) in phases and (before is None or e.at <= before):
            return e.at
    return None


def timeline_text(events) -> str:
    return " ".join(f"{e.code}+{e.at:.0f}s" for e in events)


class Vendor:
    name = ""
    poll_default = 5.0

    def __init__(self, keys: dict, dry_run: bool = False):
        self.keys = keys
        self.dry_run = dry_run

    def need(self, key: str) -> str:
        value = self.keys.get(key)
        if value:
            return value
        if self.dry_run:
            return f"<{key}>"
        raise BakeoffError(
            f"{key} is missing. Put it in ~/.config/beyric-bakeoff/keys.env or set it as an "
            "environment variable (README step 3)."
        )

    def secrets(self):
        return [self.keys[k] for k in SECRET_KEYS if self.keys.get(k)]

    def has_phase(self, snap: Snapshot, phase: str) -> bool:
        return any(self.phase_of(e.code) == phase for e in snap.events)


class Skribby(Vendor):
    name = "skribby"
    poll_default = 5.0
    PHASES = {
        "scheduled": "starting",
        "booting": "starting",
        "joining": "joining",  # includes the waiting room: Skribby has no separate lobby status
        "waiting_to_record": "admitted",
        "recording": "admitted",
        "breakout_waiting_room": "admitted",
        "leaving": "after",
        "processing": "after",
        "transcribing": "after",
        "finished": "done",
        "not_admitted": "failed",
        "bot_detected": "failed",
        "auth_required": "failed",
        "invalid_credentials": "failed",
        "invalid_api_key": "failed",
        "failed": "failed",
    }
    TERMINAL = {
        "finished",
        "not_admitted",
        "auth_required",
        "invalid_credentials",
        "invalid_api_key",
        "failed",
    }

    def phase_of(self, code: str) -> str:
        return self.PHASES.get(code, "unknown")

    def base(self) -> str:
        region = (self.keys.get("SKRIBBY_REGION") or "eu").lower()
        if region not in SKRIBBY_BASES:
            raise BakeoffError(f"SKRIBBY_REGION must be eu or jp, not {region!r}.")
        return SKRIBBY_BASES[region]

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.need('SKRIBBY_API_KEY')}"}

    def create_request(
        self, arm, meet_url, name, lobby_limit, stay, trial_id, model="none"
    ) -> Request:
        # stop_options are minutes. time_limit is our safety net if this program is ever killed.
        wait_minutes = max(1, math.ceil(lobby_limit / 60))
        body = {
            "transcription_model": model,
            "service": "gmeet",
            "meeting_url": meet_url,
            "bot_name": name,
            "custom_metadata": {"bakeoff_trial": trial_id},
            "stop_options": {
                "waiting_room_timeout": wait_minutes,
                "time_limit": wait_minutes + math.ceil(stay / 60) + 5,
            },
        }
        if arm == "skribby-auth":
            body["authentication"] = {
                "account_id": self.need("SKRIBBY_ACCOUNT_ID"),
                "always_authenticate": True,
            }
        return Request("POST", f"{self.base()}/bot", self.headers(), body)

    def status_request(self, bot_id) -> Request:
        return Request("GET", f"{self.base()}/bot/{bot_id}", self.headers())

    def leave_request(self, bot_id) -> Request:
        return Request("POST", f"{self.base()}/bot/{bot_id}/stop", self.headers())

    def bot_id(self, data) -> str | None:
        return data.get("id")

    def snapshot(self, data: dict, local_now: float) -> Snapshot:
        anchor = parse_ts(data.get("created_at"))
        raw = []
        for item in data.get("events") or []:
            code = (item.get("data") or {}).get("new_status")
            if code:
                raw.append((code, parse_ts(item.get("created_at"))))
        if anchor is None:
            anchor = next((ts for _, ts in raw if ts), None)
        events = [
            Event(
                code, None, max(0.0, (ts - anchor).total_seconds()) if ts and anchor else local_now
            )
            for code, ts in raw
        ]
        status = data.get("status") or (events[-1].code if events else "unknown")
        if not events or events[-1].code != status:
            events.append(Event(status, None, local_now))
        events[-1].sub = data.get("stop_reason")
        finished = parse_ts(data.get("finished_at"))
        end_at = (finished - anchor).total_seconds() if finished and anchor else None
        return Snapshot(events, status, data.get("stop_reason"), end_at)

    def is_terminal(self, snap: Snapshot, quiet_for: float) -> bool:
        if snap.status in self.TERMINAL:
            return True
        if snap.status == "bot_detected":  # Skribby retries up to 3 times; wait for it to give up
            return (
                sum(1 for e in snap.events if e.code == "bot_detected") >= 4
                or quiet_for >= BOT_DETECTED_QUIET
            )
        return False

    def classify(self, snap: Snapshot, ctx: Context) -> Outcome:
        ev = snap.events
        notes = []
        detected = sum(1 for e in ev if e.code == "bot_detected")
        if detected:
            notes.append(f"bot_detected x{detected} (Skribby retried)")
        final = f"{snap.status}/{snap.sub}" if snap.sub else snap.status
        admitted_at = first_at(ev, {"admitted"}, self.phase_of)
        if admitted_at is not None:
            joined = [e.at for e in ev if e.code == "joining" and e.at <= admitted_at]
            waited = admitted_at - (joined[-1] if joined else admitted_at)
            knocked = waited >= ctx.knock_threshold
            notes.append(
                f"knock inferred, not observed: {waited:.0f}s in joining "
                f"({'at least' if knocked else 'under'} {ctx.knock_threshold:.0f}s)"
            )
            return Outcome(
                "knocked-then-admitted" if knocked else "admitted", final, None, admitted_at, notes
            )
        if ctx.interrupted:
            return Outcome("error", "interrupted", None, None, notes + ["stopped by Ctrl-C"])
        if snap.status == "not_admitted":
            if snap.sub == "request_denied":
                return Outcome("refused", final, None, None, notes)
            if snap.sub in ("waiting_room_timeout", "manually_stopped"):
                return Outcome("never-joined", final, None, None, notes)
            return Outcome("error", final, None, None, notes)
        if snap.status in ("bot_detected", "auth_required"):
            return Outcome("refused", final, None, None, notes)
        if snap.status in ("invalid_credentials", "invalid_api_key", "failed"):
            return Outcome("error", final, None, None, notes)
        if ctx.lobby_limit_hit or snap.status not in self.TERMINAL:
            return Outcome("never-joined", final, None, None, notes)
        return Outcome("error", final, None, None, notes + ["finished without ever recording"])

    def bot_minutes(self, snap: Snapshot, local_elapsed: float) -> float:
        # Billed per minute from creation to the terminal status (billing page). Counted whole.
        end = snap.end_at
        if end is None:
            end = snap.events[-1].at if self.is_final(snap) else local_elapsed
        return float(math.ceil(max(end, 0.0) / 60.0))

    def is_final(self, snap: Snapshot) -> bool:
        return snap.status in self.TERMINAL


RECALL_REFUSAL_FATAL = {
    "google_meet_bot_blocked",
    "google_meet_knocking_disabled",
    "google_meet_organisation_restricted",
    "google_meet_sign_in_missing_login_credentials",  # the meeting insists on a signed-in guest
    "meeting_requires_sign_in",
    "meeting_locked",
}
RECALL_LOBBY_TIMEOUT = {
    "timeout_exceeded_waiting_room",
    "call_ended_by_platform_waiting_room_timeout",
}


class Recall(Vendor):
    name = "recall"
    poll_default = (
        10.0  # Recall's docs prefer webhooks; this tool has no public address to receive them
    )
    PHASES = {
        "joining_call": "joining",
        "in_waiting_room": "lobby",
        "in_call_not_recording": "admitted",
        "recording_permission_allowed": "admitted",
        "recording_permission_denied": "admitted",
        "in_call_recording": "admitted",
        "call_ended": "after",
        "done": "done",
        "fatal": "failed",
    }

    def phase_of(self, code: str) -> str:
        return self.PHASES.get(code, "unknown")

    def base(self) -> str:
        region = (self.need("RECALL_REGION")).lower()
        if self.dry_run and region == "<recall_region>":
            return "https://<RECALL_REGION>.recall.ai/api/v1"
        if region not in RECALL_REGIONS:  # also stops the key being sent to an arbitrary host
            raise BakeoffError(f"RECALL_REGION must be one of {', '.join(RECALL_REGIONS)}.")
        return f"https://{region}.recall.ai/api/v1"

    def headers(self) -> dict:
        return {"Authorization": f"Token {self.need('RECALL_API_KEY')}"}

    def create_request(
        self, arm, meet_url, name, lobby_limit, stay, trial_id, model=None
    ) -> Request:
        body = {
            "meeting_url": meet_url,
            "bot_name": name,
            "automatic_leave": {"waiting_room_timeout": max(30, int(lobby_limit))},
            # No transcript, audio only, and a one-hour retention: the trial keeps nothing.
            "recording_config": {
                "retention": {"type": "timed", "hours": 1},
                "video_mixed_layout": "audio_only",
            },
            "metadata": {"bakeoff_trial": trial_id},
        }
        return Request("POST", f"{self.base()}/bot/", self.headers(), body)

    def status_request(self, bot_id) -> Request:
        return Request("GET", f"{self.base()}/bot/{bot_id}/", self.headers())

    def leave_request(self, bot_id) -> Request:
        return Request("POST", f"{self.base()}/bot/{bot_id}/leave_call/", self.headers())

    def bot_id(self, data) -> str | None:
        return data.get("id")

    def snapshot(self, data: dict, local_now: float) -> Snapshot:
        raw = []
        for item in data.get("status_changes") or []:
            code = str(item.get("code") or "").removeprefix("bot.")
            if code:
                raw.append((code, item.get("sub_code"), parse_ts(item.get("created_at"))))
        anchor = next((ts for _, _, ts in raw if ts), None)
        events = [
            Event(
                code, sub, max(0.0, (ts - anchor).total_seconds()) if ts and anchor else local_now
            )
            for code, sub, ts in raw
        ]
        if not events:
            return Snapshot([], "unknown", None)
        done = next((e.at for e in events if e.code in ("done", "fatal")), None)
        return Snapshot(events, events[-1].code, events[-1].sub, done)

    def is_terminal(self, snap: Snapshot, quiet_for: float) -> bool:
        if snap.status in ("done", "fatal"):
            return True
        return snap.status == "call_ended" and quiet_for >= 60  # `done` should follow

    def classify(self, snap: Snapshot, ctx: Context) -> Outcome:
        ev = snap.events
        lobby_at = first_at(ev, {"lobby"}, self.phase_of)
        admitted_at = first_at(ev, {"admitted"}, self.phase_of)
        fatal = next((e for e in reversed(ev) if e.code == "fatal"), None)
        ended = next((e for e in reversed(ev) if e.code == "call_ended"), None)
        closing = fatal or ended
        final = (
            f"{closing.code}/{closing.sub}"
            if closing and closing.sub
            else (closing.code if closing else snap.status)
        )
        if admitted_at is not None:
            knocked = lobby_at is not None and lobby_at <= admitted_at
            return Outcome(
                "knocked-then-admitted" if knocked else "admitted", final, lobby_at, admitted_at, []
            )
        if ctx.interrupted:
            return Outcome("error", "interrupted", lobby_at, None, ["stopped by Ctrl-C"])
        sub = closing.sub if closing else None
        if fatal is not None:
            result = "refused" if sub in RECALL_REFUSAL_FATAL else "error"
            return Outcome(result, final, lobby_at, None, [])
        if sub == "bot_kicked_from_waiting_room":
            return Outcome("refused", final, lobby_at, None, [])
        if sub in RECALL_LOBBY_TIMEOUT or sub == "bot_received_leave_call" or ctx.lobby_limit_hit:
            return Outcome("never-joined", final, lobby_at, None, [])
        return Outcome("never-joined", final, lobby_at, None, ["no terminal status was seen"])

    def bot_minutes(self, snap: Snapshot, local_elapsed: float) -> float:
        # Billed per second from joining_call to done (usage page). Fatal bots are not billed,
        # but the tool still counts them: a cap that over-counts is the safe way round.
        end = snap.end_at if snap.end_at is not None else local_elapsed
        return round(max(end, 0.0) / 60.0, 2)


def make_vendor(arm: str, keys: dict, dry_run: bool = False) -> Vendor:
    return (Skribby if ARM_VENDOR[arm] == "skribby" else Recall)(keys, dry_run)


# --------------------------------------------------------------------------------------------
# The log
# --------------------------------------------------------------------------------------------


def meeting_code(url: str) -> str:
    m = MEET_URL.match(url.strip())
    if not m:
        raise BakeoffError(
            "That is not a Google Meet link. Open the call and copy the address that looks like "
            "https://meet.google.com/abc-defg-hij "
            "(short links and /lookup/ links are not accepted)."
        )
    return m.group(1)


def code_tail(code: str) -> str:
    """Last four characters of the code without its dashes: enough to tell calls apart, no more."""
    return code.replace("-", "")[-4:]


def read_rows(path: Path) -> list:
    if not Path(path).is_file():
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def append_row(path: Path, row: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        if fresh:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in CSV_COLUMNS})


def make_row(start: datetime, arm, host_type, code, outcome: Outcome, minutes, extra_notes) -> dict:
    def seconds(value):
        return "" if value is None else f"{value:.1f}"

    return {
        "time_utc": start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "time_lagos": start.astimezone(LAGOS).strftime("%Y-%m-%d %H:%M:%S"),
        "trial_id": f"{start.astimezone(UTC):%Y%m%dT%H%M%SZ}-{arm}",
        "arm": arm,
        "host_type": host_type or "unknown",
        "meeting_code": code_tail(code),
        "result": outcome.result,
        "seconds_to_lobby": seconds(outcome.seconds_to_lobby),
        "seconds_to_admission": seconds(outcome.seconds_to_admission),
        "final_status": outcome.final_status,
        "bot_minutes": "" if minutes is None else f"{minutes:.2f}",
        "notes": " | ".join(n for n in list(outcome.notes) + list(extra_notes) if n),
    }


def used_minutes(rows, vendor: str, since: str | None = None) -> float:
    total = 0.0
    for row in rows:
        if ARM_VENDOR.get(row.get("arm", "")) != vendor:
            continue
        if since and (row.get("time_utc") or "")[:10] < since:
            continue
        try:
            total += float(row.get("bot_minutes") or 0)
        except ValueError:
            pass
    return total


# --------------------------------------------------------------------------------------------
# One trial
# --------------------------------------------------------------------------------------------


@dataclasses.dataclass
class TrialSettings:
    arm: str
    meet_url: str
    host_type: str | None
    note: str
    lobby_limit: float
    stay: float
    poll: float
    knock_threshold: float
    model: str | None
    bot_name: str


def run_trial(vendor, client, settings: TrialSettings, clock, sleep, wall_now, say) -> dict:
    """Create the bot, watch it, ask it to leave, and return the finished log row."""
    code = meeting_code(settings.meet_url)
    start = wall_now()
    trial_id = f"{start.astimezone(UTC):%Y%m%dT%H%M%SZ}-{settings.arm}"
    t0 = clock()

    def elapsed() -> float:
        return clock() - t0

    extra = [settings.note] if settings.note else []
    req = vendor.create_request(
        settings.arm,
        settings.meet_url,
        settings.bot_name,
        settings.lobby_limit,
        settings.stay,
        trial_id,
        settings.model,
    )
    try:
        bot_id = vendor.bot_id(client.call(req).json())
        if not bot_id:
            raise ApiError(0, "the answer had no bot id")
    except (ApiError, NetworkError) as exc:
        say(f"Could not start the bot: {exc}")
        outcome = Outcome("error", "create-failed", None, None, [f"create failed: {exc}"])
        return make_row(start, settings.arm, settings.host_type, code, outcome, 0.0, extra)
    say(
        f"Bot created ({vendor.name}). Watching it; this ends when it is in the call about "
        f"{settings.stay:.0f}s, fails, or waits {settings.lobby_limit:.0f}s at the door."
    )

    snap = None
    last_status = None
    status_since = 0.0
    admitted_local = None
    left_at = None
    lobby_hit = False
    interrupted = False
    terminal = False
    failures = 0
    stopped_reason = None

    def leave(reason: str) -> None:
        nonlocal left_at
        left_at = elapsed()
        try:
            client.call(vendor.leave_request(bot_id))
            say(f"[+{left_at:.0f}s] asked {vendor.name} to leave ({reason})")
        except (ApiError, NetworkError) as exc:  # already gone is fine
            say(f"[+{left_at:.0f}s] leave request answered: {exc}")

    hard_stop = settings.lobby_limit + settings.stay + POST_LEAVE_GRACE
    try:
        while True:
            sleep(settings.poll)
            try:
                data = client.call(vendor.status_request(bot_id)).json()
                failures = 0
            except (ApiError, NetworkError) as exc:
                failures += 1
                say(
                    f"[+{elapsed():.0f}s] could not read the status "
                    f"({failures}/{MAX_POLL_FAILURES}): {exc}"
                )
                if failures >= MAX_POLL_FAILURES:
                    stopped_reason = f"status unreadable: {exc}"
                    break
                continue
            snap = vendor.snapshot(data, elapsed())
            if snap.status != last_status:
                last_status = snap.status
                status_since = elapsed()
                say(f"[+{elapsed():.0f}s] {snap.status}{'/' + snap.sub if snap.sub else ''}")
            if admitted_local is None and vendor.has_phase(snap, "admitted"):
                admitted_local = elapsed()
            if vendor.is_terminal(snap, elapsed() - status_since):
                terminal = True
                break
            if left_at is None:
                if admitted_local is not None and elapsed() - admitted_local >= settings.stay:
                    leave(f"in the call for {settings.stay:.0f}s")
                elif admitted_local is None and elapsed() >= settings.lobby_limit:
                    lobby_hit = True
                    leave(f"{settings.lobby_limit:.0f}s at the door")
            if left_at is not None and elapsed() - left_at > POST_LEAVE_GRACE:
                break
            if elapsed() > hard_stop:
                break
    except KeyboardInterrupt:
        interrupted = True
        say("Stopping: asking the bot to leave so it does not keep running up hours.")
    finally:
        if not terminal and left_at is None:
            leave("trial ended")

    if snap is None:
        outcome = Outcome(
            "error", "no-status", None, None, [stopped_reason or "no status was read"]
        )
        return make_row(start, settings.arm, settings.host_type, code, outcome, None, extra)
    ctx = Context(settings.knock_threshold, lobby_hit, interrupted)
    outcome = vendor.classify(snap, ctx)
    minutes = vendor.bot_minutes(snap, elapsed())
    notes = [f"timeline {timeline_text(snap.events)}"]
    if stopped_reason:
        notes.append(stopped_reason)
    if lobby_hit:
        notes.append(f"lobby limit {settings.lobby_limit:.0f}s reached")
    elif left_at is not None and admitted_local is not None:
        notes.append(f"stayed {settings.stay:.0f}s then asked to leave")
    if not terminal:
        notes.append("no terminal status within the grace time; minutes are an estimate")
    outcome.notes = outcome.notes + notes
    return make_row(start, settings.arm, settings.host_type, code, outcome, minutes, extra)


# --------------------------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------------------------


def wilson(successes: int, n: int) -> tuple:
    """A 95 percent range for a share of n trials: how wrong a small sample can be."""
    if n == 0:
        return (0.0, 0.0)
    z = 1.96
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - spread), min(1.0, centre + spread))


def gap_needed(n: int) -> int:
    """Points of difference between two arms of n trials each that is more than luck.

    Worked out for join rates near 75 percent, the rough size of the problem we are measuring.
    """
    return round(100 * 1.96 * math.sqrt(2 * 0.75 * 0.25 / n))


def _number(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def build_report(rows, cap_hours: float = DEFAULT_CAP_HOURS) -> str:
    out = []
    by_arm = collections.OrderedDict((arm, []) for arm in ALL_ARMS)
    for row in rows:
        by_arm.setdefault(row.get("arm", "?"), []).append(row)
    out.append(f"Bake-off report: {len(rows)} trials logged\n")
    sizes = []
    for arm, trials in by_arm.items():
        if not trials:
            continue
        valid = [t for t in trials if t.get("result") != "error"]
        errors = len(trials) - len(valid)
        admitted = [t for t in valid if t["result"] in ("admitted", "knocked-then-admitted")]
        knocked = [t for t in valid if t["result"] == "knocked-then-admitted"]
        times = sorted(
            x for x in (_number(t.get("seconds_to_admission")) for t in admitted) if x is not None
        )
        low, high = wilson(len(admitted), len(valid))
        out.append(f"{arm}")
        out.append(
            f"  trials: {len(trials)} ({len(valid)} counted, "
            f"{errors} errors on our side or the vendor's API)"
        )
        if valid:
            out.append(
                f"  admitted: {len(admitted)} of {len(valid)} = "
                f"{100 * len(admitted) / len(valid):.0f}% "
                f"(true rate could be anywhere from {100 * low:.0f}% to {100 * high:.0f}%)"
            )
        if admitted:
            basis = (
                "inferred from time in 'joining'"
                if arm.startswith("skribby")
                else (
                    "seen as a waiting-room status"
                    if arm.startswith("recall")
                    else "as noted by hand"
                )
            )
            out.append(f"  needed a knock: {len(knocked)} of {len(admitted)} admitted ({basis})")
        if times:
            out.append(
                f"  seconds to admission: median {statistics.median(times):.0f}, "
                f"slowest {max(times):.0f}"
            )
        for label, wanted in (
            ("refused", "refused"),
            ("never joined", "never-joined"),
            ("errors", "error"),
        ):
            reasons = collections.Counter(
                (t.get("final_status") or "no reason given")
                for t in trials
                if t["result"] == wanted
            )
            if reasons:
                out.append(
                    f"  {label}: " + ", ".join(f"{k} x{v}" for k, v in reasons.most_common())
                )
        if valid:
            sizes.append(len(valid))
        out.append("")
    out.append("Bot minutes used (the vendor's own dashboard is the real bill):")
    for vendor in ("skribby", "recall"):
        used = used_minutes(rows, vendor)
        out.append(f"  {vendor}: {used:.1f} min = {used / 60:.2f} h of the {cap_hours:g} h cap")
    out.append("")
    if sizes:
        n = min(sizes)
        if n < 5:
            out.append(
                f"In plain words: {n} counted trial(s) in the smallest arm is too few to say "
                "anything yet. "
                "Keep going."
            )
        else:
            out.append(
                f"In plain words: with {n} trials in the smallest arm, only a big gap "
                f"(about {gap_needed(n)} percentage points or more) is more than luck; a small gap "
                f"tells you nothing yet. 10 trials per arm shows a big gap, not a small one."
            )
    else:
        out.append("No trials counted yet. Run one (README step 4), then run this again.")
    return "\n".join(out)


# --------------------------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bakeoff.py",
        description="Compare Vexa, Skribby and Recall bots on real Google Meet joins.",
    )
    p.add_argument(
        "--log", help=f"CSV log path (default {DEFAULT_LOG.relative_to(DEFAULT_LOG.parents[2])})"
    )
    p.add_argument("--keys-file", help=f"keys file (default {DEFAULT_KEYS_FILE})")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser(
        "run", help="send a Skribby or Recall bot into a Meet and log what happens"
    )
    run.add_argument("--arm", required=True, choices=RUN_ARMS)
    run.add_argument("--meet-url", required=True)
    run.add_argument("--host-type", choices=HOST_TYPES)
    run.add_argument("--note", default="")
    run.add_argument(
        "--lobby-limit",
        type=float,
        default=DEFAULT_LOBBY_LIMIT,
        metavar="SECONDS",
        help="give up after this long at the door (default 600)",
    )
    run.add_argument(
        "--stay",
        type=float,
        default=DEFAULT_STAY,
        metavar="SECONDS",
        help="seconds in the call after admission before asking the bot to leave (default 60)",
    )
    run.add_argument(
        "--poll",
        type=float,
        metavar="SECONDS",
        help="seconds between status checks (default 5 Skribby, 10 Recall)",
    )
    run.add_argument("--bot-name", help="override the plain default name for this arm")
    run.add_argument(
        "--cap-hours",
        type=float,
        help="refuse a new trial above this many bot hours for the vendor "
        f"(default {DEFAULT_CAP_HOURS})",
    )
    run.add_argument(
        "--budget-since",
        metavar="YYYY-MM-DD",
        help="count only bot minutes logged on or after this day (Skribby's 5 hours renew monthly)",
    )
    run.add_argument("--force", action="store_true", help="run even above the cap")
    run.add_argument(
        "--knock-threshold",
        type=float,
        default=DEFAULT_KNOCK_THRESHOLD,
        metavar="SECONDS",
        help="Skribby only: this long in 'joining' counts as a knock (default 20)",
    )
    run.add_argument(
        "--skribby-model", default="none", help="Skribby transcription model (default none)"
    )
    run.add_argument(
        "--dry-run", action="store_true", help="print the exact requests and send nothing"
    )

    note = sub.add_parser("note", help="record by hand what a Vexa bot did")
    note.add_argument("--arm", required=True, choices=("vexa",))
    note.add_argument("--meet-url", required=True)
    note.add_argument("--result", required=True, choices=NOTE_RESULTS)
    note.add_argument(
        "--seconds-to-door",
        type=float,
        metavar="N",
        help="seconds from pressing start in the Beyric app until the bot was in the call",
    )
    note.add_argument(
        "--reason", default="", help="what you saw, e.g. 'Google said it could not join'"
    )
    note.add_argument("--host-type", choices=HOST_TYPES)
    note.add_argument("--note", default="")

    rep = sub.add_parser("report", help="show the results so far")
    rep.add_argument("--cap-hours", type=float, help="the cap shown next to the minutes used")
    return p


def _cap(args, env) -> float:
    if args.cap_hours is not None:
        return args.cap_hours
    try:
        return float(env.get("BAKEOFF_CAP_HOURS", DEFAULT_CAP_HOURS))
    except ValueError:
        return DEFAULT_CAP_HOURS


def format_request(req: Request) -> str:
    lines = [f"{req.method} {req.url}"]
    lines += [f"  {k}: {v}" for k, v in redact_headers(req.headers).items()]
    if req.body is not None:
        lines.append("  body: " + json.dumps(req.body, indent=2).replace("\n", "\n  "))
    return "\n".join(lines)


def cmd_run(args, env, deps) -> int:
    out = deps["out"]
    say = lambda text: print(text, file=out, flush=True)  # noqa: E731
    code = meeting_code(args.meet_url)
    if args.lobby_limit < 30:
        raise BakeoffError("--lobby-limit must be at least 30 seconds (Recall's own minimum).")
    if args.stay < 0:
        raise BakeoffError("--stay cannot be negative.")
    keys = load_keys(env, args.keys_file)
    vendor = make_vendor(args.arm, keys, dry_run=args.dry_run)
    name = args.bot_name or BOT_NAMES[args.arm]
    trial_id = f"<time>-{args.arm}"
    if args.dry_run:
        model = args.skribby_model if vendor.name == "skribby" else None
        create = vendor.create_request(
            args.arm, args.meet_url, name, args.lobby_limit, args.stay, trial_id, model
        )
        say("DRY RUN: nothing is sent, nothing is logged. Authorization is hidden.\n")
        say("1. create the bot:\n" + format_request(create))
        say(
            "\n2. then every few seconds, to read its status:\n"
            + format_request(vendor.status_request("<bot id>"))
        )
        say(
            "\n3. at the end (or if it waits too long), to make it leave:\n"
            + format_request(vendor.leave_request("<bot id>"))
        )
        return 0
    log = Path(args.log or env.get("BAKEOFF_LOG") or DEFAULT_LOG)
    cap = _cap(args, env)
    used = used_minutes(read_rows(log), vendor.name, args.budget_since)
    if used >= cap * 60 and not args.force:
        say(
            f"Not starting: {vendor.name} has used {used / 60:.2f} h of bot time in the log, "
            f"at or above the {cap:g} h cap. Check the vendor's dashboard, then raise "
            "--cap-hours or add --force."
        )
        return 3
    vendor.need("SKRIBBY_API_KEY" if vendor.name == "skribby" else "RECALL_API_KEY")
    if not args.host_type:
        say("Note: --host-type was not given; this trial is logged as host type 'unknown'.")
    settings = TrialSettings(
        arm=args.arm,
        meet_url=args.meet_url,
        host_type=args.host_type,
        note=args.note,
        lobby_limit=args.lobby_limit,
        stay=args.stay,
        poll=args.poll or vendor.poll_default,
        knock_threshold=args.knock_threshold,
        model=args.skribby_model if vendor.name == "skribby" else None,
        bot_name=name,
    )
    client = Client(deps["transport"], deps["sleep"], vendor.secrets())
    say(
        f"Trial: {args.arm}, call ending {code_tail(code)}, "
        f"host type {args.host_type or 'unknown'}."
    )
    row = run_trial(vendor, client, settings, deps["clock"], deps["sleep"], deps["now"], say)
    append_row(log, row)
    say("\nResult: " + row["result"] + (f" ({row['final_status']})" if row["final_status"] else ""))
    say(
        f"Seconds to admission: {row['seconds_to_admission'] or '-'}   "
        f"Bot minutes: {row['bot_minutes'] or '-'}"
    )
    say(f"Logged to {log}")
    return 0


def cmd_note(args, env, deps) -> int:
    out = deps["out"]
    code = meeting_code(args.meet_url)
    log = Path(args.log or env.get("BAKEOFF_LOG") or DEFAULT_LOG)
    admitted = args.result in ("admitted", "knocked-then-admitted")
    notes = []
    if args.seconds_to_door is not None and not admitted:
        notes.append(f"{args.seconds_to_door:.0f}s until the bot's answer")
    if args.note:
        notes.append(args.note)
    notes.append("entered by hand")
    outcome = Outcome(
        args.result, args.reason, None, args.seconds_to_door if admitted else None, notes
    )
    row = make_row(deps["now"](), "vexa", args.host_type, code, outcome, None, [])
    append_row(log, row)
    print(f"Logged a Vexa trial: {args.result}. File: {log}", file=out)
    return 0


def cmd_report(args, env, deps) -> int:
    log = Path(args.log or env.get("BAKEOFF_LOG") or DEFAULT_LOG)
    rows = read_rows(log)
    if not rows:
        print(f"No trials yet in {log}. Run a trial first (README step 4).", file=deps["out"])
        return 0
    print(build_report(rows, _cap(args, env)), file=deps["out"])
    return 0


def main(argv=None, *, transport=None, clock=None, sleep=None, now=None, out=None, env=None) -> int:
    env = os.environ if env is None else env
    deps = {
        "transport": transport or UrllibTransport(),
        "clock": clock or time.monotonic,
        "sleep": sleep or time.sleep,
        "now": now or (lambda: datetime.now(UTC)),
        "out": out or sys.stdout,
    }
    args = build_parser().parse_args(argv)
    try:
        return {"run": cmd_run, "note": cmd_note, "report": cmd_report}[args.command](
            args, env, deps
        )
    except BakeoffError as exc:
        print(f"Problem: {exc}", file=deps["out"])
        return 2


if __name__ == "__main__":
    sys.exit(main())
