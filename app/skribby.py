"""Client for the hosted Skribby meeting-bot API, and `SkribbyProvider`, its `BotProvider` face.

Selected with `BOT_PROVIDER=skribby` (`app/bot_provider.py`); Vexa stays the default. The design,
every decision and what is lost against Vexa are in `docs/sop/2026-11-08-skribby-provider.md`.
In short:

- **Polling only.** Skribby makes one webhook delivery per event and no retry; the worker already
  looks at each live meeting every `POLL_INTERVAL_SECONDS`, so there is no inbound route.
- **Skribby speaks in bot ids (UUIDs); the seam speaks in meeting codes and integers.** The id is
  stored as text in `meetings.vexa_meeting_id` like Vexa's. A stop by `(platform, code)` and a
  duplicate check need the bot behind a code, which Skribby cannot look up (it has no list call), so
  the provider asks our own `meetings` table (`DbLedger`) and remembers the bots it sent in this
  process (the window before the meeting row is committed). The recording is addressed by an integer
  *handle* made from the UUID (`recording_handle`): it fits `meetings.vexa_recording_id`, and the
  UUID comes back through the meetings table or this process's recent lookups.
- **Skribby's transcript is only evidence of who spoke.** Our own transcription still writes the
  transcript from the downloaded audio; Skribby's segments (`speaker_name` and text) become the
  `BotMeeting.segments` that label speakers, exactly where Vexa's live segments go.
- **States are mapped into Vexa's vocabulary** (`map_bot`): the pipeline reads those words.
- **Secrets.** The API key is read once, kept in the client, sent only in the `Authorization`
  header to Skribby's own origin, and removed from every error text. Nothing here logs a request
  body or a meeting link (a Zoom link carries its passcode).
"""

import ipaddress
import json
import logging
import math
import re
import socket
import threading
import time
import unicodedata
import uuid
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.bot_provider import (
    SEND_DEADLINE_SECONDS,
    BotDuplicate,
    BotMeeting,
    BotNotFound,
    BotProviderError,
    BotProviderNotConfigured,
    BotSendUncertain,
    run_within,
    scrub_links,
)
from app.config import get_settings
from app.meeting_url import _parse
from app.transcribe import _keywords

logger = logging.getLogger(__name__)

BASES = {
    "eu": "https://platform.skribby.io/api/v1",
    "jp": "https://platform-jp.skribby.io/api/v1",
}
SERVICES = {"google_meet": "gmeet", "zoom": "zoom", "teams": "teams"}

# Joined for this long and still not in: the bot is knocking, and the owner is told to admit it.
# Skribby has no status for the waiting room (`joining` covers it), so this is inferred, as the
# bake-off did (`tools/bakeoff`, "knock inferred, not observed").
KNOCK_AFTER_SECONDS = 20.0
# Skribby retries a blocked bot up to 3 times itself (`bot_detected`, then `booting` again). The
# fourth `bot_detected`, or one with nothing newer for this long, is the end.
BOT_DETECTED_ATTEMPTS = 4
BOT_DETECTED_QUIET_SECONDS = 90.0
# How long the bot waits to be let in, in minutes. Skribby's default; the first 10 minutes of a bot
# that is never admitted are free (`skribby-guide-billing.txt`).
WAITING_ROOM_MINUTES = 10
SEND_CONNECT_SECONDS = 5.0
LOOKUP_TIMEOUT_SECONDS = 5.0  # the duplicate check's looks, which run inside the send's deadline
MAX_REDIRECTS = 5
# Skribby's own `time_limit` ends the bot this long BEFORE the pipeline gives up on the meeting
# (`MAX_POLL_HOURS`), so a long call is over, and its recording processed, before the give-up.
TIME_LIMIT_MARGIN_MINUTES = 30  # (and the waiting room's allowance too: see `payload`)
# Hosts a recording may be fetched from (the name, or any subdomain of it): Skribby's own, and
# Amazon's storage (its sub-processors page: AWS, "hosting / storage"). Anything else fails closed;
# `SKRIBBY_DOWNLOAD_HOSTS` adds a host once an operator has seen where recordings really live.
DOWNLOAD_HOSTS = ("skribby.io", "amazonaws.com")
MAX_DOWNLOAD_BYTES = 1 << 30
# A validation error to a create that carried `custom_vocabulary`: retried once without it.
VOCABULARY_RETRY_STATUSES = (400, 422)
# The late-reply watch (see `SkribbyProvider.send_bot`).
LATE_ADOPT_TRIES = 10
LATE_ADOPT_PAUSE_SECONDS = 2.0
# What the process remembers of the bots it sent: this many codes, each for this long.
SENT_CODES_KEPT = 256
SENT_KEPT_FOR = 12 * 3600
MAX_FAILURE_REASON_CHARS = 300
MAX_COMPLETION_REASON_CHARS = 64  # `meetings.completion_reason` is String(64)
MAX_NAME_CHARS = 80
# The most `potential_speaker_names` entries one segment may list and still be read (real ones
# hold a few); a longer list is treated as no usable answer.
MAX_CANDIDATES = 20
# Names kept per item in the saved evidence (a real list holds a few).
MAX_EVIDENCE_NAMES = 50
# What `BotMeeting.recording_status` says while Skribby is still processing or transcribing: the
# call is over (`completed`) and the recording is not ready to download (the pipeline's own
# "finishing the recording" wait).
RECORDING_PENDING = "in_progress"
TERMINAL = ("completed", "failed")

# A recording handle is the first 60 bits of the bot's UUID with a marker bit above them, so it
# fits a BigInteger and is never mistaken for a Vexa recording id.
HANDLE_MARK = 1 << 60
UUID_BITS_DROPPED = 68

_NAME_BAD_CHARS = re.compile(r"[\[\]:<>]")


class SkribbyError(BotProviderError):
    """HTTP failure or connection problem talking to Skribby. Treat as retryable."""


class SkribbyDuplicateBot(SkribbyError, BotDuplicate):
    """A live bot of ours is already in that meeting (found in our own meetings or in this
    process: Skribby itself allows several bots in one meeting). Nothing was sent."""


class SkribbySendUncertain(SkribbyError, BotSendUncertain):
    """`send_bot` did not get a clear answer: the request timed out after it was sent, the
    connection broke, the deadline ran out, Skribby answered 5xx, or said yes in a body we could
    not read. The bot MAY be in the meeting. Skribby has no call that finds a bot by meeting, so
    the worker cannot adopt it: it gives the code up after `UNCONFIRMED_GRACE` (see the SOP's
    known gaps)."""


class SkribbyNotFound(SkribbyError, BotNotFound):
    """Skribby does not know the bot (HTTP 404, or an id that is not a Skribby bot id)."""


def _utcnow() -> datetime:
    """The provider's clock when none is given (tests replace it, so no assertion depends on the
    real time the knock inference reads)."""
    return datetime.now(UTC)


# ---- recording handles ----


def recording_handle(bot_id: str) -> int:
    """An integer for `bot_id`'s recording: the UUID's first 60 bits under a marker bit."""
    return HANDLE_MARK | (uuid.UUID(bot_id).int >> UUID_BITS_DROPPED)


def handle_prefix(handle: int) -> str | None:
    """The first 15 hex digits of the UUID a handle was made from, in the UUID's own spacing
    (`xxxxxxxx-xxxx-xxx`); None for a number that is not a handle (a Vexa recording id)."""
    if not HANDLE_MARK <= handle < HANDLE_MARK << 1:
        return None
    digits = f"{handle & (HANDLE_MARK - 1):015x}"
    return f"{digits[:8]}-{digits[8:12]}-{digits[12:]}"


def bot_uuid(value: str | int) -> str:
    """The canonical lower-case UUID text of a bot id, or `SkribbyNotFound` for anything else (a
    Vexa id handed to the wrong provider is "no such bot", never a request with a made-up path)."""
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise SkribbyNotFound("not a Skribby bot id") from None


# ---- what a Skribby bot body means ----


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def _events(body: dict) -> list[tuple[str, datetime | None]]:
    """The bot's status changes, `(new_status, when)`, in the order Skribby lists them. Entries
    that are not objects are skipped: events only ever sharpen the knock estimate."""
    raw = body.get("events")
    if not isinstance(raw, list):
        return []
    found = []
    for item in raw:
        data = item.get("data") if isinstance(item, dict) else None
        new = data.get("new_status") if isinstance(data, dict) else None
        if isinstance(new, str):
            found.append((new, _parse_time(item.get("created_at"))))
    return found


def _joining_seconds(events: list[tuple[str, datetime | None]], status: str, now: datetime):
    """How long the bot's last `joining` has lasted (or lasted, once it moved on): None when the
    events do not say. Clocks: Skribby's timestamps against ours, a skew of seconds at most."""
    for index in range(len(events) - 1, -1, -1):
        name, started = events[index]
        if name != "joining" or started is None:
            continue
        if index + 1 < len(events):
            ended = events[index + 1][1]
        else:
            ended = now if status == "joining" else None
        return None if ended is None else (ended - started).total_seconds()
    return None


def _clean_name(value: Any) -> str:
    """A participant's name as a label: they choose it, so label syntax (brackets, colon, angle
    brackets), control characters and line breaks go, and the length is capped. The notes code
    cleans it again; this keeps the provider's own output safe on its own."""
    if not isinstance(value, str):
        return ""
    name = "".join(" " if ch.isspace() or ord(ch) < 32 else ch for ch in value)
    name = _NAME_BAD_CHARS.sub("", name)
    return " ".join(name.split())[:MAX_NAME_CHARS].strip()


def _tag(value: Any) -> str | None:
    """Skribby's own diarization tag of a segment ("1", "2", ...), stable across a meeting: a
    string or an integer is a tag, anything else (a boolean, a float, a list, empty) is none."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _same(name: str) -> str:
    """What two spellings of a name are compared by: compatibility-normalised, with invisible
    format characters (zero-width space and the like) dropped, then case-folded."""
    plain = unicodedata.normalize("NFKC", name)
    return "".join(ch for ch in plain if unicodedata.category(ch) != "Cf").casefold()


def _candidate_names(item: dict) -> dict[str, str] | None:
    """The cleaned names in a segment's `potential_speaker_names` as `{comparison key: name}`, the
    first spelling of a name kept, in the order listed; None when the list is so long that choosing
    from it would be a guess made on a cut-off list (real ones hold a few)."""
    listed = item.get("potential_speaker_names")
    if not isinstance(listed, list):
        return {}
    if len(listed) > MAX_CANDIDATES:
        return None
    names: dict[str, str] = {}
    for entry in listed:
        name = _clean_name(entry.get("name")) if isinstance(entry, dict) else ""
        if name:
            names.setdefault(_same(name), name)
    return names


def _resolve_tags(
    value: list[dict], own_names: tuple[str, ...], by_elimination: bool
) -> dict[str, dict[str, str]]:
    """For every diarization tag Skribby used: `{"name", "how", "note"}`. `how` says why the tag
    has the name it has: `given` (its own segments carry exactly one `speaker_name`),
    `elimination` (see `_tag_names`; only when `by_elimination`) or `unknown` (`name` is empty and
    `note` says why: `several_names`, `no_candidates`, `elimination_off`, `unresolved`,
    `shared_claim`). With elimination off, `note` is `elimination_off` for a tag it would have
    named, and `would_be` holds the name it would have taken, so a saved record shows what the
    switch changed."""
    bot = {_same(name) for name in map(_clean_name, own_names) if name}
    given: dict[str, dict[str, str]] = {}  # tag -> {comparison key: the name as shown}
    listed: dict[str, dict[str, str] | None] = {}  # tag -> candidates, None when too many
    for item in value:
        tag = _tag(item.get("speaker"))
        if tag is None:
            continue
        named = given.setdefault(tag, {})
        own = _clean_name(item.get("speaker_name"))
        if own:
            named.setdefault(_same(own), own)
        candidates = _candidate_names(item)
        if candidates is None or listed.get(tag, {}) is None:
            listed[tag] = None
        else:
            for key, name in candidates.items():
                listed.setdefault(tag, {}).setdefault(key, name)
    holders: dict[str, set[str]] = {}  # name key -> the tags that carry it
    for tag, named in given.items():
        for key in named:
            holders.setdefault(key, set()).add(tag)
    claims: dict[str, list[str]] = {}  # candidate key -> the tags that would take it
    chosen: dict[str, str] = {}
    for tag, named in given.items():
        candidates = listed.get(tag)
        if named or not candidates:
            continue
        ruled_out = [key for key in candidates if holders.get(key, set()) - {tag}]
        left = {
            key: name for key, name in candidates.items() if key not in bot and key not in ruled_out
        }
        if ruled_out and len(left) == 1:
            key, name = next(iter(left.items()))
            claims.setdefault(key, []).append(tag)
            chosen[tag] = name
    result: dict[str, dict[str, str]] = {}
    for tag, named in given.items():
        if len(named) == 1:
            result[tag] = {"name": next(iter(named.values())), "how": "given", "note": ""}
        elif named:
            result[tag] = {"name": "", "how": "unknown", "note": "several_names"}
        elif tag in chosen and len(claims[_same(chosen[tag])]) > 1:
            result[tag] = {"name": "", "how": "unknown", "note": "shared_claim"}
        elif tag in chosen and by_elimination:
            result[tag] = {"name": chosen[tag], "how": "elimination", "note": ""}
        elif tag in chosen:
            result[tag] = {
                "name": "",
                "how": "unknown",
                "note": "elimination_off",
                "would_be": chosen[tag],
            }
        elif not listed.get(tag):
            result[tag] = {"name": "", "how": "unknown", "note": "no_candidates"}
        else:
            result[tag] = {"name": "", "how": "unknown", "note": "unresolved"}
    return result


def _tag_names(
    value: list[dict], own_names: tuple[str, ...] = (), by_elimination: bool | None = None
) -> dict[str, str]:
    """The name each of Skribby's diarization tags stands for, for the tags where it is knowable.

    Skribby names a tag on some segments (`speaker_name`) and, for a participant it could not pin
    down, lists `potential_speaker_names` instead. A tag takes the one name its own segments carry.

    Only when `by_elimination` is true (the setting `SKRIBBY_NAME_BY_ELIMINATION`, read when None;
    off by default because it can name a tag after someone who is not in the call): a tag with no
    name of its own is named by elimination, its candidates minus the bot's own names, minus every
    name another tag already has, and only when at least one candidate was dropped for being
    another tag's and exactly one is left (a lone candidate nothing rules anything out for has no
    support). A name two tags would both take goes to neither. Whatever stays unclear is left out:
    the label is "unknown", never a name made up."""
    if by_elimination is None:
        by_elimination = get_settings().skribby_name_by_elimination
    resolved = _resolve_tags(value, own_names, by_elimination)
    return {tag: info["name"] for tag, info in resolved.items() if info["name"]}


def _segments(
    body: dict, own_names: tuple[str, ...] = (), by_elimination: bool | None = None
) -> list[dict[str, Any]]:
    """`{"speaker", "text"}` for each of Skribby's transcript segments (the shape the speaker
    labelling reads). Absent or null is none; any other shape means Skribby changed its response,
    which we refuse to guess about. A segment's own `speaker_name` wins; one without it takes its
    diarization tag's name (see `_tag_names`; `own_names`, the bot's own, are never a speaker)."""
    value = body.get("transcript")
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise SkribbyError("get_meeting failed: 'transcript' is not a list of JSON objects")
    by_tag = _tag_names(value, own_names, by_elimination)
    segments = []
    for item in value:
        text = item.get("transcript")
        if isinstance(text, str) and text.strip():
            name = _clean_name(item.get("speaker_name"))
            if not name:
                tag = _tag(item.get("speaker"))
                name = by_tag.get(tag, "") if tag is not None else ""
            segments.append({"speaker": name, "text": text})
    return segments


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return float(value)


def _evidence(
    body: dict, own_names: tuple[str, ...], by_elimination: bool
) -> dict[str, Any] | None:
    """What Skribby sent for a meeting, kept for a few days so a bad labelling can be diagnosed
    (`skribby_segment_evidence`): each transcript item as it came (`speaker` the diarization tag,
    `speaker_name`, the NAMES in `potential_speaker_names`, `start`, `end`, `text`) and, per tag,
    the name the app derived and why (`_resolve_tags`). None when there is no transcript. It
    holds the words people said, so it is never logged or shown."""
    value = body.get("transcript")
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        return None
    if not value:
        return None
    items = []
    for item in value:
        listed = item.get("potential_speaker_names")
        names = (
            [_clean_name(entry.get("name")) for entry in listed if isinstance(entry, dict)]
            if isinstance(listed, list)
            else []
        )
        text = item.get("transcript")
        items.append(
            {
                "speaker": _tag(item.get("speaker")),
                "speaker_name": _clean_name(item.get("speaker_name")) or None,
                "potential_speaker_names": [name for name in names if name][:MAX_EVIDENCE_NAMES],
                "start": _number(item.get("start")),
                "end": _number(item.get("end")),
                "text": text if isinstance(text, str) else "",
            }
        )
    return {
        "items": items,
        "tags": _resolve_tags(value, own_names, by_elimination),
        "by_elimination": by_elimination,
    }


def _safe_evidence(
    body: dict, own_names: tuple[str, ...], by_elimination: bool
) -> dict[str, Any] | None:
    """`_evidence`, which is only a record for diagnosis: whatever goes wrong in building it leaves
    it out (logged by exception class only) and never stops the meeting being read."""
    try:
        return _evidence(body, own_names, by_elimination)
    except Exception as exc:
        logger.warning("skribby: could not keep the segment evidence (%s)", type(exc).__name__)
        return None


def _last_segment_end(body: dict) -> float | None:
    """Where the last of Skribby's transcript segments ends, in seconds from the start of the
    recording (the unit of its `start` and `end`, seen on a real answer: 7.53 and 19.11). The
    latest end of any segment; None when no segment has a usable one."""
    value = body.get("transcript")
    if not isinstance(value, list):
        return None
    ends = [
        float(end)
        for item in value
        if isinstance(item, dict)
        and isinstance(end := item.get("end"), int | float)
        and not isinstance(end, bool)
        and math.isfinite(end)
        and end >= 0
    ]
    return max(ends) if ends else None


def _recording_url(body: dict) -> str | None:
    url = body.get("recording_url")
    return url if isinstance(url, str) and url.startswith("https://") else None


_NOT_ADMITTED_TEXT = {
    "invalid_meeting_url": "the link is not a valid meeting link",
    "call_already_finished": "the meeting had already ended",
    "host_in_another_meeting": "the host has not started this meeting (the host is in another)",
    "manually_stopped": "the bot was stopped before it was let in",
}
_FAILED_TEXT = {
    "auth_required": (
        "auth_required: the meeting only lets signed-in people in and the bot has no account"
    ),
    "invalid_credentials": "invalid_credentials: the bot's Google sign-in was refused",
    "invalid_api_key": "invalid_api_key: the transcription key was refused",
    "failed": "failed: the meeting bot service reported an internal error",
}
_LIVE = {
    "scheduled": "requested",
    "booting": "requested",
    "waiting_to_record": "active",
    "recording": "active",
    "breakout_waiting_room": "awaiting_admission",
    "leaving": "stopping",
}
# How Skribby's `stop_reason` of a finished bot reads in the pipeline's words: a normal end says
# nothing (as Vexa's does), being removed is `evicted` (the pipeline's removal notice), our own stop
# is `stopped`; any other reason (`empty_meeting_timeout`, `silence_detection_triggered`, ...) is
# kept as Skribby wrote it.
_FINISHED_REASON = {"kicked": "evicted", "manually_stopped": "stopped", "meeting_ended": None}
_known_odd: set[str] = set()


def _blocked_text(service: Any) -> str:
    """Why the bot ended as `bot_detected`, in the words the pipeline's retry logic reads: Google's
    refusal and Zoom's block each have their own retry rules (`app/pipeline.py`)."""
    if service == "gmeet":
        return (
            "google_meet_join_refused: Skribby reports that Google blocked the bot (bot_detected) "
            "after its own retries"
        )
    if service == "zoom":
        return (
            "zoom_requires_rtms: Skribby reports that Zoom blocked the bot (bot_detected) after "
            "its own retries"
        )
    return "bot_detected: the platform blocked the bot after Skribby's own retries"


def _bot_detected_is_final(
    events: list[tuple[str, datetime | None]], body: dict, now: datetime
) -> bool:
    if body.get("finished_at"):
        return True
    detected = [when for name, when in events if name == "bot_detected"]
    if len(detected) >= BOT_DETECTED_ATTEMPTS:
        return True
    last = events[-1] if events else None
    if last is not None and last[0] == "bot_detected" and last[1] is not None:
        return (now - last[1]).total_seconds() >= BOT_DETECTED_QUIET_SECONDS
    return False


def map_bot(
    body: dict, now: datetime, bot_id: str | None = None, own_names: tuple[str, ...] = ()
) -> BotMeeting:
    """Skribby's bot body as a `BotMeeting`, in the vocabulary the pipeline reads (see the SOP's
    table). `bot_id` is the id the caller asked for (the recording's handle is made from it; the
    body's own `id` is the fallback). `own_names` are the names the bot itself goes by (a candidate
    speaker name that is one of them is never used). `vexa_id` is left for the caller to set."""
    raw = body.get("status")
    if not isinstance(raw, str):
        raise SkribbyError("get_meeting failed: 'status' is not a string")
    stop = body.get("stop_reason")
    stop = stop if isinstance(stop, str) else None
    events = _events(body)
    seconds = _joining_seconds(events, raw, now)
    knocked = seconds is not None and seconds >= KNOCK_AFTER_SECONDS

    if raw == "finished":
        by_elimination = get_settings().skribby_name_by_elimination
        url = _recording_url(body)
        handle = _handle_for(bot_id or body.get("id"))
        reason = _FINISHED_REASON[stop] if stop in _FINISHED_REASON else stop
        return BotMeeting(
            status="completed",
            segments=_segments(body, own_names, by_elimination),
            evidence=_safe_evidence(body, own_names, by_elimination),
            last_segment_end=_last_segment_end(body),
            recording_id=handle if url else None,
            media_id=handle if url else None,
            recording_status="completed" if url else None,
            completion_reason=reason[:MAX_COMPLETION_REASON_CHARS] if reason else None,
            reached_lobby=knocked,
        )
    if raw in ("processing", "transcribing"):
        return BotMeeting(
            status="completed", recording_status=RECORDING_PENDING, reached_lobby=knocked
        )
    if raw == "not_admitted":
        door = {
            "request_denied": "awaiting_admission_rejected",
            "waiting_room_timeout": "awaiting_admission_timeout",
        }.get(stop or "")
        if door is not None:
            return BotMeeting(status="failed", completion_reason=door, reached_lobby=True)
        text = _NOT_ADMITTED_TEXT.get(stop or "", f"not admitted ({stop or 'no reason given'})")
        return BotMeeting(status="failed", failure_reason=text, reached_lobby=knocked)
    if raw == "bot_detected":
        if _bot_detected_is_final(events, body, now):
            return BotMeeting(status="failed", failure_reason=_blocked_text(body.get("service")))
        return BotMeeting(status="requested")  # Skribby is retrying with a new browser
    if raw in _FAILED_TEXT:
        return BotMeeting(status="failed", failure_reason=_FAILED_TEXT[raw][:300])
    if raw == "joining":
        return BotMeeting(
            status="awaiting_admission" if knocked else "joining", reached_lobby=knocked
        )
    if raw in _LIVE:
        status = _LIVE[raw]
        return BotMeeting(status=status, reached_lobby=knocked or status == "awaiting_admission")
    if raw not in _known_odd:  # a status Skribby added: keep the bot live and say so once
        _known_odd.add(raw)
        logger.warning("Skribby reported a status this build does not know (%s)", raw[:40])
    return BotMeeting(status="requested")


def _handle_for(bot_id: Any) -> int | None:
    try:
        return recording_handle(bot_uuid(bot_id if bot_id is not None else ""))
    except SkribbyNotFound:
        return None


# ---- the ledger: the bots behind a meeting code, from our own database ----


class BotLedger(Protocol):
    """What the provider asks of our own records, because Skribby cannot answer it."""

    def live_bots(self, platform: str, code: str) -> list[str]:
        """Skribby bot ids of meetings in the call on this code, newest first."""
        ...

    def bots_for_prefix(self, prefix: str) -> list[str]:
        """Skribby bot ids that start with `prefix` (see `handle_prefix`)."""
        ...

    def adopt_late_bot(self, platform: str, code: str, bot_id: str) -> bool:
        """Give a bot whose "created" answer came after the send deadline to the placeholder
        meeting waiting for it (the one kept as an unconfirmed start). False when there is none
        (yet)."""
        ...


class DbLedger:
    """The ledger over the `meetings` table: rows `bot_provider = 'skribby'` with a bot id. Each
    call opens a short session of its own (the callers are inside transactions with locks held;
    this one only reads what is committed, and never waits for a row)."""

    def __init__(self, session_factory: Callable[[], Any] | None = None) -> None:
        self._factory = session_factory

    def _session(self):
        if self._factory is not None:
            return self._factory()
        from app import db  # inside: read at call time, so a test can replace `SessionLocal`

        return db.SessionLocal()

    def live_bots(self, platform: str, code: str) -> list[str]:
        from sqlalchemy import select

        from app.models import Meeting

        with self._session() as session:
            rows = session.scalars(
                select(Meeting.vexa_meeting_id)
                .where(
                    Meeting.bot_provider == "skribby",
                    Meeting.platform == platform,
                    Meeting.native_id == code,
                    Meeting.status == "bot_sent",
                    Meeting.vexa_meeting_id.is_not(None),
                )
                .order_by(Meeting.id.desc())
            )
            return [str(row) for row in rows]

    def adopt_late_bot(self, platform: str, code: str, bot_id: str) -> bool:
        from sqlalchemy import select

        from app.models import Meeting
        from app.placement import SEND_UNCONFIRMED

        with self._session() as session:
            row = session.scalars(
                select(Meeting)
                .where(
                    Meeting.bot_provider == "skribby",
                    Meeting.platform == platform,
                    Meeting.native_id == code,
                    Meeting.status == "bot_sent",
                    Meeting.vexa_meeting_id.is_(None),
                    Meeting.completion_reason == SEND_UNCONFIRMED,
                )
                .order_by(Meeting.id.desc())
                .limit(1)
                .with_for_update(skip_locked=True)
            ).first()
            if row is None:
                return False
            # As `pipeline._settle_unconfirmed` does when the bot shows up: pinned to its id, and
            # the "starting the bot" marker cleared so the worker's next look reads its state.
            row.vexa_meeting_id = bot_id
            row.completion_reason = None
            row.bot_state_seen_at = None
            session.commit()
            return True

    def bots_for_prefix(self, prefix: str) -> list[str]:
        from sqlalchemy import select

        from app.models import Meeting

        with self._session() as session:
            rows = session.scalars(
                select(Meeting.vexa_meeting_id)
                .where(
                    Meeting.bot_provider == "skribby",
                    Meeting.vexa_meeting_id.like(prefix + "%"),
                )
                .limit(3)
            )
            return [str(row) for row in rows]


# ---- the HTTP client ----


class SkribbyClient:
    """The one object that knows Skribby's URLs and holds its API key."""

    def __init__(
        self,
        *,
        api_key: str,
        region: str = "eu",
        account_id: str = "",
        bot_name: str = "Beyric Meeting Support",
        account_name: str = "",
        transcription_model: str = "groq/whisper-large-v3-turbo",
        left_alone_seconds: int = 0,
        max_poll_hours: int = 6,
        timeout: float = 30.0,
        send_deadline: float = SEND_DEADLINE_SECONDS,
        base_url: str | None = None,
        download_hosts: tuple[str, ...] = (),
        resolve: Callable[[str], list[str]] | None = None,
    ) -> None:
        key = api_key.strip()
        if not key:
            raise BotProviderNotConfigured("SKRIBBY_API_KEY is not set")
        if base_url is None and region not in BASES:
            raise BotProviderNotConfigured("SKRIBBY_REGION must be eu or jp")
        self._key = key
        self.region = region
        self._base_url = (base_url or BASES[region]).rstrip("/")
        own_host = httpx.URL(self._base_url).host
        self._download_hosts = (*DOWNLOAD_HOSTS, own_host, *(h.lower() for h in download_hosts))
        self._resolve = resolve or _resolve_host
        self._account_id = account_id
        self._bot_name = bot_name
        self._account_name = account_name
        self._model = transcription_model.strip() or "none"
        self._left_alone_seconds = left_alone_seconds
        self._max_poll_hours = max_poll_hours
        self._timeout = timeout
        self.send_deadline = send_deadline

    @property
    def own_names(self) -> tuple[str, ...]:
        """The names the bot goes by in a meeting (the guest name, and the signed-in account's name
        when it is known): never the name of a speaker."""
        return tuple(name for name in (self._bot_name, self._account_name) if name.strip())

    def __repr__(self) -> str:  # never shows the key
        return f"SkribbyClient(base_url={self._base_url!r})"

    # -- helpers --

    def _scrub(self, text: str) -> str:
        return text.replace(self._key, "[redacted]")

    def _http(self, connect: float | None = None, timeout: float | None = None) -> httpx.Client:
        seconds = timeout or self._timeout
        return httpx.Client(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._key}", "Accept": "application/json"},
            timeout=httpx.Timeout(seconds, connect=connect or seconds),
            follow_redirects=False,
        )

    def _failure(self, what: str, response: httpx.Response) -> SkribbyError:
        """An error for a non-success answer: the status and Skribby's own `message` (a short
        public text such as the concurrency limit), never a header or the request."""
        status = response.status_code
        if status == 401:
            return SkribbyError(f"{what} failed: Skribby refused the API key (HTTP 401)")
        message = ""
        try:
            body = response.json()
            if isinstance(body, dict):
                # Skribby's own reason: `message`, or `error`/`code`/`detail` when it uses those
                # (a bad-parameter 400 is not documented), as short text only.
                parts = []
                for name in ("message", "error", "code", "detail"):
                    value = body.get(name)
                    if isinstance(value, list | dict):
                        value = json.dumps(value, default=str)
                    if isinstance(value, str) and value.strip():
                        parts.append(value.strip())
                message = " ".join(parts)[:300]
        except ValueError:
            pass
        message = scrub_links(message)  # a link can carry a passcode
        detail = f": {self._scrub(message)}" if message else ""
        return SkribbyError(f"{what} failed: HTTP {status}{detail}")

    # -- the calls --

    def payload(
        self,
        platform: str,
        code: str,
        canonical_url: str,
        vocabulary: list[str] | None = None,
    ) -> dict[str, Any]:
        stop_options: dict[str, Any] = {
            "waiting_room_timeout": WAITING_ROOM_MINUTES,
            # The ghost-bot backstop. Skribby counts it from when the bot is in, our give-up from
            # the send, so it is the give-up minus the longest wait at the door and minus 30 minutes
            # for the recording to be processed: 6 h = 360 - 10 - 30 = 320 minutes.
            "time_limit": min(
                720,
                max(
                    1,
                    self._max_poll_hours * 60 - TIME_LIMIT_MARGIN_MINUTES - WAITING_ROOM_MINUTES,
                ),
            ),
            # Off: a quiet stretch must not send the bot away (Vexa never left for silence; the
            # "only person left" rule below ends a bot whose call is over). Skribby's default is
            # 15 minutes.
            "silence_detection": 0,
        }
        if self._left_alone_seconds > 0:
            stop_options["last_person_detection"] = max(1, math.ceil(self._left_alone_seconds / 60))
        body: dict[str, Any] = {
            "transcription_model": self._model,
            "service": SERVICES[platform],
            "meeting_url": canonical_url,
            "bot_name": self._bot_name,
            "custom_metadata": {"app": "beyric-note-taker", "meeting_code": code},
            "stop_options": stop_options,
        }
        # The account is a Google one: only a Meet bot signs in with it. A Zoom bot would need a
        # ZAK token and a Teams bot an account in the meeting's own tenant (Skribby's bot
        # authentication page); neither is ours, so both join as guests.
        if self._account_id and platform == "google_meet":
            body["authentication"] = {"account_id": self._account_id, "always_authenticate": True}
        # The owner's own words and names, for the transcription model to spell right. Skribby's
        # create-bot page: `custom_vocabulary` (string[]), "Only supported on certain models" and
        # no stated limits, so our own cap (`app.transcribe` keywords: 50 terms, 100 characters)
        # applies. Not sent when transcription is off (`none`): nothing would read it.
        terms = _keywords(vocabulary or [])
        if terms and self._model != "none":
            body["custom_vocabulary"] = terms
        return body

    def _post_bot(self, payload: dict[str, Any]) -> httpx.Response:
        with self._http(connect=SEND_CONNECT_SECONDS) as client:
            return client.post("/bot", json=payload)

    def create(self, payload: dict[str, Any]) -> str:
        """`POST /bot`; the new bot's id. Errors follow the interface: never connected is plain,
        no clear answer is `SkribbySendUncertain`, a 4xx is plain.

        `custom_vocabulary` has never been exercised against the real service: if Skribby answers
        a validation error (400 or 422) to a create that carries it, the create is made ONCE more
        without it (a 4xx means no bot was made, so this cannot make two) and that is logged, with
        no terms. A bot without the owner's vocabulary beats no bot."""
        try:
            response = self._post_bot(payload)
            if response.status_code in VOCABULARY_RETRY_STATUSES and "custom_vocabulary" in payload:
                logger.warning(
                    "Skribby refused the bot create with HTTP %s; retrying once without the "
                    "custom vocabulary",
                    response.status_code,
                )
                payload = {k: v for k, v in payload.items() if k != "custom_vocabulary"}
                response = self._post_bot(payload)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise SkribbyError(f"send_bot failed: {self._scrub(str(exc))}") from None
        except httpx.HTTPError as exc:
            raise SkribbySendUncertain(f"send_bot failed: {self._scrub(str(exc))}") from None
        status = response.status_code
        if status >= 500:  # a gateway can time out after Skribby already made the bot
            raise SkribbySendUncertain(f"send_bot failed: HTTP {status}")
        if status not in (200, 201):
            raise self._failure("send_bot", response)
        try:
            return bot_uuid(response.json()["id"])
        except (ValueError, KeyError, TypeError, AttributeError, SkribbyNotFound):
            raise SkribbySendUncertain(
                "send_bot failed: the answer had no readable bot id"
            ) from None

    def get_bot(self, bot_id: str, *, timeout: float | None = None) -> dict[str, Any]:
        bot_id = bot_uuid(bot_id)
        try:
            with self._http(timeout=timeout) as client:
                response = client.get(f"/bot/{bot_id}")
        except httpx.HTTPError as exc:
            raise SkribbyError(f"get_meeting failed: {self._scrub(str(exc))}") from None
        if response.status_code == 404:
            raise SkribbyNotFound("get_meeting failed: HTTP 404")
        if response.status_code != 200:
            raise self._failure("get_meeting", response)
        try:
            body = response.json()
        except ValueError:
            raise SkribbyError("get_meeting failed: the answer is not JSON") from None
        if not isinstance(body, dict):
            raise SkribbyError("get_meeting failed: response body is not a JSON object")
        return body

    def stop(self, bot_id: str) -> str:
        """`POST /bot/{id}/stop`: `stopped`, `gone` (404) or `refused` (403: Skribby says the bot
        cannot be stopped, which is what a bot that is already over answers)."""
        bot_id = bot_uuid(bot_id)
        try:
            with self._http() as client:
                response = client.post(f"/bot/{bot_id}/stop")
        except httpx.HTTPError as exc:
            raise SkribbyError(f"stop_bot failed: {self._scrub(str(exc))}") from None
        if response.status_code == 404:
            return "gone"
        if response.status_code == 403:
            return "refused"
        if response.status_code != 200:
            raise self._failure("stop_bot", response)
        return "stopped"

    def delete(self, bot_id: str) -> str:
        """`DELETE /bot/{id}`: Skribby's data for the bot (recording, transcript, participants).
        `gone` for a 404 (the caller cannot tell "deleted before" from "never in this region"),
        else `deleted` for a 2xx."""
        bot_id = bot_uuid(bot_id)
        try:
            with self._http() as client:
                response = client.delete(f"/bot/{bot_id}")
        except httpx.HTTPError as exc:
            raise SkribbyError(f"delete_recording failed: {self._scrub(str(exc))}") from None
        if response.status_code == 404:
            return "gone"
        if not 200 <= response.status_code < 300:
            raise self._failure("delete_recording", response)
        return "deleted"

    def probe(self, url: str) -> int:
        """The HTTP status Skribby (or its storage) answers for a recording link, without keeping
        the body (the live check uses it to show a deleted recording is gone). The same checks as a
        download apply, so it never goes to a host the download would refuse."""
        target = httpx.URL(url)
        self._check_download_target(target)
        base = httpx.URL(self._base_url)
        own = (target.scheme, target.netloc) == (base.scheme, base.netloc)
        headers = {"Authorization": f"Bearer {self._key}"} if own else {}
        try:
            with httpx.Client(timeout=self._timeout) as client:
                with client.stream("GET", target, headers=headers) as response:
                    return response.status_code
        except httpx.HTTPError as exc:
            raise SkribbyError(f"probe failed: {self._scrub(str(exc))}") from None

    def _check_download_target(self, target: httpx.URL) -> None:
        """Fail closed before any request to `target`: https, a host we allow, and no address that
        is private, loopback, link-local or otherwise not public."""
        if target.scheme != "https":
            raise SkribbyError("download_audio failed: the recording is not on https")
        host = (target.host or "").lower()
        if not any(
            host == allowed or host.endswith("." + allowed) for allowed in self._download_hosts
        ):
            raise SkribbyError(
                "download_audio failed: the recording is on a host that is not allowed "
                f"({host[:80]}); add it to SKRIBBY_DOWNLOAD_HOSTS only if it is Skribby's storage"
            )
        try:
            addresses = [host] if _is_ip(host) else self._resolve(host)
            public = bool(addresses) and all(
                ipaddress.ip_address(a.split("%")[0]).is_global for a in addresses
            )
        except (OSError, ValueError):
            public = False
        if not public:
            raise SkribbyError(
                "download_audio failed: the recording's host is not a public address"
            )

    def download(self, url: str, path: Path) -> None:
        """Stream the recording at `url` to `path`. https only, a host on the allow list, no
        non-public address, redirects followed by hand (each hop checked the same way), the API key
        only to Skribby's own origin, at most `MAX_DOWNLOAD_BYTES`, and the bytes received must
        match the `Content-Length` when there is one and not be empty: the caller deletes the
        provider's copy once this returns, so a short file must never pass."""
        path = Path(path)
        base = httpx.URL(self._base_url)
        target = httpx.URL(url)
        try:
            with httpx.Client(timeout=self._timeout) as client:
                for _ in range(MAX_REDIRECTS + 1):
                    self._check_download_target(target)
                    own = (target.scheme, target.netloc) == (base.scheme, base.netloc)
                    headers = {"Authorization": f"Bearer {self._key}"} if own else {}
                    with client.stream("GET", target, headers=headers) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise SkribbyError(
                                    "download_audio failed: redirect without a place"
                                )
                            target = target.join(location)
                            continue
                        if response.status_code != 200:
                            raise SkribbyError(
                                f"download_audio failed: HTTP {response.status_code}"
                            )
                        self._write(response, path)
                        return
                raise SkribbyError("download_audio failed: too many redirects")
        except SkribbyError:
            path.unlink(missing_ok=True)
            raise
        except (httpx.HTTPError, OSError) as exc:
            path.unlink(missing_ok=True)
            raise SkribbyError(f"download_audio failed: {self._scrub(str(exc))}") from None

    @staticmethod
    def _write(response: httpx.Response, path: Path) -> None:
        declared = response.headers.get("content-length")
        try:
            expected = int(declared) if declared is not None else None
        except ValueError:
            expected = None
        if expected is not None and expected > MAX_DOWNLOAD_BYTES:
            raise SkribbyError("download_audio failed: the recording is larger than the limit")
        encoded = bool(response.headers.get("content-encoding"))
        chunks = response.iter_bytes() if encoded else response.iter_raw()
        written = 0
        with path.open("wb") as out:
            for chunk in chunks:
                written += len(chunk)
                if written > MAX_DOWNLOAD_BYTES:
                    raise SkribbyError(
                        "download_audio failed: the recording is larger than the limit"
                    )
                out.write(chunk)
        if written == 0:
            raise SkribbyError("download_audio failed: the recording is empty")
        if expected is not None and not encoded and written != expected:
            raise SkribbyError(f"download_audio failed: received {written} of {expected} bytes")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.split("%")[0])
        return True
    except ValueError:
        return False


def _resolve_host(host: str) -> list[str]:
    """Every address `host` resolves to (the real resolver; tests replace it)."""
    return [info[4][0] for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)]


# ---- the provider ----


class SkribbyProvider:
    """`SkribbyClient` behind the `BotProvider` interface, with the rules of the interface kept
    where Skribby does not keep them itself (one live bot per meeting code, a stop by code)."""

    name = "skribby"  # what `meetings.bot_provider` remembers
    HANDLES_KEPT = 256
    BOTS_KEPT_PER_CODE = 5

    def __init__(
        self,
        client: SkribbyClient,
        ledger: BotLedger | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._client = client
        self._sleep = sleep or time.sleep
        self._ledger: BotLedger = ledger or DbLedger()
        self._clock = clock or _utcnow
        self._guard = threading.Lock()
        # Bots sent by this process, per `(platform, code)`, oldest first: the window between the
        # send and the meeting row's commit, which our own table cannot see.
        # Bounded: `SENT_CODES_KEPT` codes, each entry for `SENT_KEPT_FOR` seconds.
        self._sent: OrderedDict[tuple[str, str], list[tuple[str, float]]] = OrderedDict()
        # Recent handle -> bot id answers, so a recording looked up a moment ago is found again
        # without the table (a deletion that looks the recording up and deletes it in one go).
        self._handles: OrderedDict[int, str] = OrderedDict()

    @classmethod
    def from_settings(cls, settings) -> "SkribbyProvider":
        if not settings.skribby_api_key.strip():
            raise BotProviderNotConfigured("SKRIBBY_API_KEY is not set")
        client = SkribbyClient(
            api_key=settings.skribby_api_key,
            region=settings.skribby_region,
            account_id=settings.skribby_account_id,
            bot_name=settings.skribby_bot_name,
            account_name=settings.skribby_account_name,
            transcription_model=settings.skribby_transcription_model,
            left_alone_seconds=settings.bot_left_alone_seconds,
            max_poll_hours=settings.max_poll_hours,
            download_hosts=tuple(
                host.strip() for host in settings.skribby_download_hosts.split(",") if host.strip()
            ),
        )
        return cls(client)

    @property
    def region(self) -> str:
        """The Skribby region this provider talks to (`meetings.bot_region` remembers it)."""
        return self._client.region

    def __repr__(self) -> str:
        return "SkribbyProvider()"

    # ---- what this process remembers ----

    def _remember_bot(self, key: tuple[str, str], bot_id: str) -> None:
        now = time.monotonic()
        with self._guard:
            for old_key in list(self._sent):  # drop what is too old, then what is over the cap
                kept = [(b, at) for b, at in self._sent[old_key] if now - at < SENT_KEPT_FOR]
                if kept:
                    self._sent[old_key] = kept
                else:
                    del self._sent[old_key]
            bots = self._sent.setdefault(key, [])
            if bot_id not in [b for b, _ in bots]:
                bots.append((bot_id, now))
            del bots[: -self.BOTS_KEPT_PER_CODE]
            self._sent.move_to_end(key)
            while len(self._sent) > SENT_CODES_KEPT:
                self._sent.popitem(last=False)

    def _forget_bot(self, bot_id: str) -> None:
        with self._guard:
            for key, bots in list(self._sent.items()):
                kept = [(b, at) for b, at in bots if b != bot_id]
                if kept:
                    self._sent[key] = kept
                else:
                    del self._sent[key]

    def _remember_handle(self, bot_id: str) -> None:
        handle = recording_handle(bot_id)
        with self._guard:
            self._handles[handle] = bot_id
            self._handles.move_to_end(handle)
            while len(self._handles) > self.HANDLES_KEPT:
                self._handles.popitem(last=False)

    def _candidates(self, platform: str, code: str) -> list[str]:
        """Bots that may hold the code, newest first: this process's own sends, then the live
        meetings on the code in our table. A table that cannot be read is an error, never "none"."""
        with self._guard:
            mine = [b for b, _ in reversed(self._sent.get((platform, code), []))]
        try:
            rows = self._ledger.live_bots(platform, code)
        except Exception as exc:
            raise SkribbyError(
                f"could not read our own meetings ({type(exc).__name__}); nothing was done"
            ) from None
        found: list[str] = []
        for bot_id in [*mine, *rows]:
            try:
                bot_id = bot_uuid(bot_id)
            except SkribbyNotFound:
                continue
            if bot_id not in found:
                found.append(bot_id)
        return found

    def _resolve(self, handle: int) -> str:
        """The bot a recording handle was made from."""
        try:
            handle = int(handle)
        except (TypeError, ValueError):
            raise SkribbyError("not a Skribby recording") from None
        with self._guard:
            known = self._handles.get(handle)
        if known is not None:
            return known
        prefix = handle_prefix(handle)
        if prefix is None:
            raise SkribbyError("not a Skribby recording")
        try:
            rows = {bot_uuid(row) for row in self._ledger.bots_for_prefix(prefix)}
        except SkribbyNotFound:
            rows = set()
        except Exception as exc:
            raise SkribbyError(f"could not read our own meetings ({type(exc).__name__})") from None
        if len(rows) != 1:
            raise SkribbyError("this recording is not one of ours (no single meeting holds it)")
        bot_id = rows.pop()
        self._remember_handle(bot_id)
        return bot_id

    # ---- looking at a bot ----

    def _look(self, bot_id: str, *, timeout: float | None = None) -> BotMeeting:
        body = self._client.get_bot(bot_id, timeout=timeout)
        info = map_bot(body, self._clock(), bot_id, self._client.own_names)
        info.vexa_id = bot_id
        if info.recording_id is not None:
            self._remember_handle(bot_id)
        if info.status in TERMINAL:
            self._forget_bot(bot_id)
        return info

    # ---- the BotProvider interface ----

    # The owner's vocabulary can be passed to `send_bot` (`vocabulary=`): callers check this.
    accepts_vocabulary = True

    def send_bot(self, meeting_url: str, vocabulary: list[str] | None = None) -> str:
        platform, code, canonical = _parse(meeting_url)  # UnsupportedMeetingUrl for a bad link
        payload = self._client.payload(platform, code, canonical, vocabulary)
        lock = threading.Lock()
        state: dict[str, Any] = {"started": False, "bot_id": None, "abandoned": False}

        def attempt() -> str:
            self._refuse_if_held(platform, code)
            with lock:
                if state["abandoned"]:  # the caller already gave up: nothing may be sent now
                    raise SkribbyError("send_bot abandoned before anything was sent")
                state["started"] = True
            bot_id = self._client.create(payload)
            with lock:
                late = state["abandoned"]
                state["bot_id"] = bot_id
            if late:  # the caller gave up on this send: the bot exists and must not be lost
                self._keep_late_bot(platform, code, bot_id)
            return bot_id

        try:
            bot_id = run_within(self._client.send_deadline, attempt)
        except TimeoutError:
            with lock:
                bot_id = state["bot_id"]  # it arrived just as the deadline did: it counts
                started = state["started"]
                state["abandoned"] = bot_id is None  # a send not yet begun now never will
            if bot_id is None:
                if not started:  # still checking the code: nothing was sent
                    raise SkribbyError(
                        "send_bot failed: the check for a bot already there timed out"
                    ) from None
                raise SkribbySendUncertain(
                    "send_bot failed: no answer within the deadline"
                ) from None
        self._remember_bot((platform, code), bot_id)
        return bot_id

    def _keep_late_bot(self, platform: str, code: str, bot_id: str) -> None:
        """Skribby's "created" answer came after the send's deadline, so `place_bot` has kept the
        meeting as an unconfirmed start. Remember the bot here, log its id for an operator, and give
        it to the placeholder meeting (the worker then manages it like any other bot). If no
        placeholder takes it within `LATE_ADOPT_TRIES` looks (the worker already gave the meeting
        up), nobody is waiting for this bot: it is stopped and its data deleted rather than left
        recording unmanaged. Runs in the send's own thread, which outlives the call."""
        self._remember_bot((platform, code), bot_id)
        logger.warning("Skribby bot %s was created after the send deadline; attaching it", bot_id)
        adopt = getattr(self._ledger, "adopt_late_bot", None)
        for attempt in range(LATE_ADOPT_TRIES):
            try:
                if adopt is not None and adopt(platform, code, bot_id):
                    logger.warning("Skribby bot %s was attached to its meeting", bot_id)
                    return
            except Exception as exc:
                logger.warning("late bot %s: could not attach it (%s)", bot_id, type(exc).__name__)
            if attempt + 1 < LATE_ADOPT_TRIES:
                self._sleep(LATE_ADOPT_PAUSE_SECONDS)
        logger.error("Skribby bot %s has no meeting waiting for it: stopping it", bot_id)
        try:
            self._stop_one(bot_id)
            self._delete_verified(bot_id)
        except Exception as exc:
            logger.error(
                "Skribby bot %s could not be stopped or deleted (%s): do it in the dashboard",
                bot_id,
                type(exc).__name__,
            )
        self._forget_bot(bot_id)

    def _refuse_if_held(self, platform: str, code: str) -> None:
        """One live bot per meeting code, across users: Skribby lets several bots into one meeting,
        so the rule is ours. A bot we know of on the code that is not over blocks the send; one that
        is over (or that Skribby no longer has) is forgotten."""
        for bot_id in self._candidates(platform, code):
            try:
                info = self._look(bot_id, timeout=LOOKUP_TIMEOUT_SECONDS)
            except SkribbyNotFound:
                self._forget_bot(bot_id)
                continue
            if info.status not in TERMINAL:
                raise SkribbyDuplicateBot("send_bot refused: a bot is already in that meeting")

    def get_meeting(self, platform: str, native_id: str) -> BotMeeting:
        """The newest bot we know on the code. Skribby cannot look up by meeting code, so a bot we
        have no record of (an unconfirmed send whose answer was lost) is not found."""
        for bot_id in self._candidates(platform, native_id):
            try:
                return self._look(bot_id)
            except SkribbyNotFound:
                self._forget_bot(bot_id)
        raise SkribbyNotFound("get_meeting failed: no bot of ours is known on that meeting")

    def get_meeting_by_id(self, vexa_meeting_id: str | int) -> BotMeeting:
        return self._look(bot_uuid(vexa_meeting_id))

    def stop_bot(self, platform: str, native_id: str) -> None:
        if platform not in SERVICES or not re.fullmatch(r"[A-Za-z0-9-]+", native_id):
            raise ValueError("unsupported platform or meeting code")
        failure: SkribbyError | None = None
        for bot_id in self._candidates(platform, native_id):
            try:
                self._stop_one(bot_id)
            except SkribbyError as exc:  # try the others, then report the first failure
                failure = failure or exc
        if failure is not None:
            raise failure

    def _stop_one(self, bot_id: str) -> None:
        if self._client.stop(bot_id) != "refused":
            return
        # 403: Skribby says the bot cannot be stopped. That is the answer for a bot that is
        # already over; for a live one it is a real refusal, which must not pass as a stop.
        if self._look(bot_id).status not in TERMINAL:
            raise SkribbyError("stop_bot failed: HTTP 403 for a bot that is still live")

    def _delete_verified(self, bot_id: str) -> None:
        """Delete everything Skribby holds for the bot and check it is gone: a 2xx alone is not
        taken for it. A 404 on the delete is gone (the caller's region rule keeps a bot of another
        region from being mistaken for one that is deleted). After a 2xx the bot is fetched again:
        404, or an answer with no recording link and no transcript, is gone; anything else raises,
        so the caller tries again and does not mark the copy deleted."""
        if self._client.delete(bot_id) == "gone":
            return
        try:
            body = self._client.get_bot(bot_id)
        except SkribbyNotFound:
            return
        if body.get("recording_url") or body.get("transcript"):
            raise SkribbyError(
                "delete_recording failed: Skribby still shows the recording or transcript after "
                "the delete (not verified gone)"
            )

    def delete_recording(self, recording_id: int) -> None:
        bot_id = self._resolve(recording_id)
        self._delete_verified(bot_id)
        self._forget_bot(bot_id)
        with self._guard:
            self._handles.pop(recording_handle(bot_id), None)

    def delete_bot_record(self, bot_id: str | int) -> None:
        """Delete everything Skribby keeps for a bot, by its id, and verify it (see
        `_delete_verified`): the early delete once the audio and the speaker names are ours, the
        record of a bot that never made a recording (the meeting link, the bot's name, its
        timeline), the replaced bot of a resend. A bot Skribby no longer has is success."""
        bot_id = bot_uuid(bot_id)
        self._delete_verified(bot_id)
        self._forget_bot(bot_id)

    def download_audio(self, recording_id: int, media_id: int, path: Path) -> None:
        bot_id = self._resolve(recording_id)
        url = _recording_url(self._client.get_bot(bot_id))
        if url is None:
            raise SkribbyError("download_audio failed: Skribby has no recording for this bot")
        self._client.download(url, Path(path))


def check_region_unchanged(settings, session_factory: Callable[[], Any] | None = None) -> int:
    """Say loudly, at startup, when Skribby meetings of ANOTHER region still hold data.

    Skribby's `eu` and `jp` regions are separate accounts: a bot sent to one answers 404 in the
    other. The processes still start (a Low risk is no reason to take the app down); instead every
    Skribby operation on such a meeting is refused with `BotRegionMismatch` (see
    `provider_for`), so a lookup never reads 404 as "no speaker names" and a delete is never marked
    done: they wait and are logged, until the region is set back. Logs an ERROR naming the setting
    and returns how many meetings are affected. No-op unless Skribby is the provider; a database
    that cannot be reached is a warning."""
    if settings.bot_provider != "skribby":
        return 0
    from sqlalchemy import func, select
    from sqlalchemy.exc import SQLAlchemyError

    from app.models import Meeting

    try:
        with (session_factory or DbLedger()._session)() as session:
            open_elsewhere = session.scalar(
                select(func.count(Meeting.id)).where(
                    Meeting.bot_provider == "skribby",
                    Meeting.bot_region.is_not(None),
                    Meeting.bot_region != settings.skribby_region,
                    Meeting.provider_copy_deleted_at.is_(None),
                )
            )
    except SQLAlchemyError as exc:
        logger.warning(
            "could not check SKRIBBY_REGION against open meetings (%s)", type(exc).__name__
        )
        return 0
    if open_elsewhere:
        logger.error(
            "SKRIBBY_REGION is %r but %s Skribby meeting(s) of the other region still hold data "
            "there: their lookups and deletes are refused until it is set back",
            settings.skribby_region,
            open_elsewhere,
        )
    return int(open_elsewhere or 0)
