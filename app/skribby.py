"""Client for the hosted Skribby meeting-bot API, and `SkribbyProvider`, its `BotProvider` face.

The one provider the app runs (`app/bot_provider.py`). The design and every decision are in
`docs/sop/2026-11-08-skribby-provider.md`.
In short:

- **Polling only.** Skribby makes one webhook delivery per event and no retry; the worker already
  looks at each live meeting every `POLL_INTERVAL_SECONDS`, so there is no inbound route.
- **Skribby speaks in bot ids (UUIDs); the seam speaks in meeting codes and integers.** The id is
  stored as text in `meetings.provider_bot_id`. A stop by `(platform, code)` and a duplicate check
  need the bot behind a code, which Skribby cannot look up (it has no list call), so the provider
  asks our own `meetings` table (`DbLedger`) and remembers the bots it sent in this process (the
  window before the meeting row is committed). The recording is addressed by an integer *handle*
  made from the UUID (`recording_handle`): it fits `meetings.provider_recording_id`, and the UUID
  comes back through the meetings table or this process's recent lookups.
- **Skribby's transcript is only evidence of who spoke.** Our own transcription still writes the
  transcript from the downloaded audio; Skribby's segments (`speaker_name` and text) become the
  `BotMeeting.segments` that label speakers, which is where the pipeline reads the speaker segments.
- **States are mapped into the pipeline's vocabulary** (`map_bot`): the pipeline reads those words.
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

from app import chat_notice, meet_timeline
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
# A validation error to a create that carried an optional parameter nobody has seen accepted live
# (`custom_vocabulary`, `initial_chat_message`): retried once without them. A bot without the
# owner's vocabulary or the chat line beats no bot.
VOCABULARY_RETRY_STATUSES = (400, 422)
OPTIONAL_PARAMETERS = {
    "custom_vocabulary": "custom vocabulary",
    "initial_chat_message": "chat notice",
}
# The transcription models Skribby lists WITH custom vocabulary on BOTH its pages,
# skribby.io/pricing (the "Custom Vocab" capability) and skribby.io/docs/guides/billing
# ("custom vocab"), read 2026-11-13: Soniox v6, Groq Whisper Large v3 and v3 Turbo, Mistral
# Voxtral Mini 2, Gladia. Left out on purpose: Deepgram, AssemblyAI and Speechmatics (the two
# pages disagree: pricing lists Deepgram Nova and AssemblyAI Universal with it, billing does
# not; billing lists Speechmatics and pricing does not), and `elevenlabs/scribe-v2` (neither
# page lists it). Ids are from skribby.io/docs/guides/transcription-models. A model absent
# here is sent no vocabulary at all; the HTTP 400/422 retry above stays for a listed model whose
# answer says otherwise.
VOCABULARY_MODELS = frozenset(
    {
        "soniox/stt-async-v6",
        "groq/whisper-large-v3-turbo",
        "groq/whisper-large-v3",
        "mistral/voxtral-mini-2",
        "gladia/async",
    }
)


def takes_vocabulary(model: str) -> bool:
    """Whether Skribby lists this model with custom vocabulary (`VOCABULARY_MODELS`)."""
    return model.strip() in VOCABULARY_MODELS


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
# A tag is named after a sole candidate only when at least this many of its segments list that name
# (and all of them do): two or three lines are too little to name a person on.
MIN_SOLE_CANDIDATE_SEGMENTS = 3
# Names kept per item in the saved evidence (a real list holds a few).
MAX_EVIDENCE_NAMES = 50
# Finished bots whose speaker events the provider keeps in memory (`_events_for_naming`).
EVENTS_KEPT = 32
# The most the saved speaker events may take (as JSON text). Over it, only the speaking events are
# kept; still over, the events are dropped and the names and presence are kept (`_cap_events`).
MAX_SPEAKER_EVENTS_BYTES = 400_000
SPEAKING_EVENTS = ("started-speaking", "stopped-speaking", "left", "rejoined")
# What `BotMeeting.recording_status` says while Skribby is still processing or transcribing: the
# call is over (`completed`) and the recording is not ready to download (the pipeline's own
# "finishing the recording" wait).
RECORDING_PENDING = "in_progress"
TERMINAL = ("completed", "failed")

# A recording handle is the first 60 bits of the bot's UUID with a marker bit above them, so it
# fits a BigInteger and is never mistaken for an integer recording id.
HANDLE_MARK = 1 << 60
UUID_BITS_DROPPED = 68

_NAME_BAD_CHARS = re.compile(r"[\[\]:<>]")


class SentBotId(str):
    """The id `send_bot` returns, carrying the note taker name the bot was REALLY sent with when
    that is not the one asked for: Skribby refused the person's name and the create was made again
    with the server default (`SkribbyClient.create`). `bot_name` is that name; absent, the name
    asked for was used. Callers store it on the meeting (`getattr(bot_id, "bot_name", None)`)."""

    bot_name: str | None = None


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
    (`xxxxxxxx-xxxx-xxx`); None for a number that is not a handle (an integer recording id)."""
    if not HANDLE_MARK <= handle < HANDLE_MARK << 1:
        return None
    digits = f"{handle & (HANDLE_MARK - 1):015x}"
    return f"{digits[:8]}-{digits[8:12]}-{digits[12:]}"


def bot_uuid(value: str | int) -> str:
    """The canonical lower-case UUID text of a bot id, or `SkribbyNotFound` for anything else (a
    An id handed to the wrong provider is "no such bot", never a request with a made-up path)."""
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
    value: list[dict],
    own_names: tuple[str, ...],
    by_elimination: bool,
    events: dict | None = None,
    by_timeline: bool = False,
) -> dict[str, dict[str, str]]:
    """For every diarization tag Skribby used: `{"name", "how", "note"}`. `how` says why the tag
    has the name it has: `given` (its own segments carry exactly one `speaker_name`),
    `elimination` or `sole_candidate` (see `_tag_names`; only when `by_elimination`),
    `meet_timeline` or `meet_exclusion` (only when `by_timeline` and `events` are given: see
    `_name_by_timeline` and `_name_by_exclusion`) or `unknown` (`name` is empty and `note` says
    why: `several_names`, `no_candidates`, `elimination_off`, `unresolved`, `shared_claim`).
    With elimination off, `note` is
    `elimination_off` for a tag it would have named, and `would_be` holds the name it would have
    taken, so a saved record shows what the switch changed. With the timeline rule on and a tag
    left unnamed, `timeline` says why the speaking indicator did not name it."""
    reading = _timeline_reading(value, events) if by_timeline and events else None
    result, chosen, listed, bot = _earlier_rules(value, own_names, by_elimination, reading)
    if reading is not None:
        _name_by_timeline(result, reading, bot, listed, chosen)
    return result


def _meeting_gate(reading: meet_timeline.Reading, named: dict[str, str]) -> str | None:
    """Does Meet's timeline agree with the names already given (`named`: tag -> name key)? None
    when at least one named tag's top speaker is that person and none has a different person
    clearly on top; `contradicted` (the signal or the clock is wrong) or `uncorroborated` (no named
    tag is confirmed, so nothing says the Meet names and clock are the transcript's). The same
    gate `_name_by_timeline` applies before it names anything."""
    agreeing = 0
    for tag, key in named.items():
        top, _ = meet_timeline.verdict(
            reading.cells.get(tag, {}), set(), reading.totals.get(tag, 0.0)
        )
        if top is None:
            continue  # the timeline has no clear say about this tag: no vote either way
        if top != key:
            return "contradicted"
        agreeing += 1
    return None if agreeing else "uncorroborated"


def _earlier_rules(
    value: list[dict],
    own_names: tuple[str, ...],
    by_elimination: bool,
    reading: meet_timeline.Reading | None = None,
):
    """Skribby's own name, elimination and the sole candidate: `(result, chosen, listed, bot)`,
    `result` as `_resolve_tags` describes it, `chosen` the name each of those rules picked for a
    tag (also for a tag left unnamed by a shared claim or by the elimination switch), `listed` each
    tag's candidates and `bot` the bot's own names as comparison keys. `reading` is the timeline
    (`_timeline_reading`) or None: without it only round 1 of the chain runs (see below)."""
    bot = {_same(name) for name in map(_clean_name, own_names) if name}
    given: dict[str, dict[str, str]] = {}  # tag -> {comparison key: the name as shown}
    listed: dict[str, dict[str, str] | None] = {}  # tag -> candidates, None when too many
    segments: dict[str, int] = {}  # tag -> its segments
    listing: dict[str, dict[str, int]] = {}  # tag -> candidate key -> segments that list it
    for item in value:
        tag = _tag(item.get("speaker"))
        if tag is None:
            continue
        segments[tag] = segments.get(tag, 0) + 1
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
                counts = listing.setdefault(tag, {})
                counts[key] = counts.get(key, 0) + 1
    holders: dict[str, set[str]] = {}  # name key -> the tags that carry it
    for tag, named in given.items():
        for key in named:
            holders.setdefault(key, set()).add(tag)
    chosen: dict[str, str] = {}
    chosen_how: dict[str, str] = {}
    shared_tags: set[str] = set()  # tags whose pick is contested: they get no name
    shared_keys: set[str] = set()  # names claimed by more than one tag: never "held"
    open_tags = [tag for tag, named in given.items() if not named and listed.get(tag)]
    # A chain: a name one round gave to exactly one tag is held by it in the next round, so the tag
    # whose candidates were ruled out only by that name can be named in its turn. Rounds are
    # synchronous (every tag in a round sees the same holders), so the order of the tags changes
    # nothing. Each round settles at least one tag, so there are at most `len(open_tags)` rounds.
    # Round 1 is exactly what the rules did before the chain. A name from round 2 on rests on a
    # name that was itself inferred, and Skribby over-splits people (one person on two tags makes a
    # held name that is the same person's): it stands only on Meet's positive evidence. The
    # timeline must be usable (readable events with a speaking interval, a checked clock) and agree
    # with the names already given (`_meeting_gate`), and the person must lead the tag by
    # `meet_timeline.leads`. A name that fails is neither given nor held, so an error cannot run
    # down the chain and the timeline rule may still name the tag. No usable Meet: round 1 only.
    for round_number in range(1, len(open_tags) + 1):
        if round_number > 1:
            if reading is None or reading.refused:
                break
            named_now = {tag: next(iter(named)) for tag, named in given.items() if len(named) == 1}
            named_now.update(
                {tag: _same(name) for tag, name in chosen.items() if tag not in shared_tags}
            )
            if _meeting_gate(reading, named_now):
                break
        proposals: dict[str, tuple[str, str, str]] = {}  # tag -> (key, name, how)
        for tag in open_tags:
            if tag in chosen:
                continue
            candidates = listed[tag]
            ruled_out = [key for key in candidates if holders.get(key, set()) - {tag}]
            left = {
                key: name
                for key, name in candidates.items()
                if key not in bot and key not in ruled_out
            }
            if len(left) != 1:
                continue
            key, name = next(iter(left.items()))
            if ruled_out:
                how = "elimination"
            elif (
                len(candidates) == 1
                and any(given.values())  # Skribby named a tag itself in this meeting
                and listing[tag][key] == segments[tag] >= MIN_SOLE_CANDIDATE_SEGMENTS
            ):
                how = "sole_candidate"  # one name on every segment, nobody else has it
            else:
                continue
            proposals[tag] = (key, name, how)
        claims: dict[str, list[str]] = {}
        for tag, (key, _name, _how) in proposals.items():
            claims.setdefault(key, []).append(tag)
        progress = False
        for key, tags in claims.items():
            if len(tags) > 1 or key in shared_keys:
                shared_keys.add(key)  # contested: nobody holds it, nobody is named by it
                shared_tags.update(tags)
                for tag in tags:
                    chosen[tag], chosen_how[tag] = proposals[tag][1], proposals[tag][2]
                progress = True
            elif round_number == 1 or meet_timeline.leads(reading.cells.get(tags[0], {}), key):
                tag = tags[0]
                chosen[tag], chosen_how[tag] = proposals[tag][1], proposals[tag][2]
                holders.setdefault(key, set()).add(tag)
                progress = True
        if not progress:
            break
    result: dict[str, dict[str, str]] = {}
    for tag, named in given.items():
        if len(named) == 1:
            result[tag] = {"name": next(iter(named.values())), "how": "given", "note": ""}
        elif named:
            result[tag] = {"name": "", "how": "unknown", "note": "several_names"}
        elif tag in shared_tags:
            result[tag] = {"name": "", "how": "unknown", "note": "shared_claim"}
        elif tag in chosen and by_elimination:
            result[tag] = {"name": chosen[tag], "how": chosen_how[tag], "note": ""}
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
    return result, chosen, listed, bot


# What the timeline rule may name: a tag the other rules left unnamed because they could not tell
# (not a tag that carries two names of its own: that tag holds two people, a name would be one of
# them). `elimination_off` is allowed only when the timeline agrees with what elimination would
# have said.
_TIMELINE_MAY_NAME = ("unresolved", "shared_claim", "no_candidates", "elimination_off")


def _timeline_reading(value: list[dict], events: dict | None) -> meet_timeline.Reading:
    """Meet's speaking timeline read against the tags of the transcript items `value`
    (`meet_timeline.read`; names grouped and compared as everywhere in this module)."""

    def group(name: str) -> tuple[str, str]:
        shown = _clean_name(name)
        return (_same(shown) if shown else ""), shown

    items = [
        {"tag": _tag(item.get("speaker")), "start": item.get("start"), "end": item.get("end")}
        for item in value
    ]
    return meet_timeline.read(items, events, group, tag_of=lambda item: item["tag"])


def _name_by_timeline(
    result: dict[str, dict[str, str]],
    reading: meet_timeline.Reading,
    bot: set[str],
    listed: dict[str, dict[str, str] | None],
    chosen: dict[str, str],
) -> None:
    """Name tags still unnamed after Skribby's name, elimination and the sole candidate, after the
    participant who was speaking most over their segments, by Meet's own speaking indicator
    (`app/meet_timeline.py`; docs/sop/2026-11-17-name-by-meet-timeline.md). Changes `result` in
    place and never touches a tag that already has a name. A tag it does not name gets a
    `timeline` key saying why. All of these must hold, else nothing is named:

    * the events exist, are readable and are on the transcript's clock, and no shift of up to
      30 s fits clearly better (`meet_timeline.read`);
    * the timeline agrees with the names the other rules gave: at least one named tag's top
      speaker is that person (so the Meet names and the clock are the transcript's), and none
      has a different person clearly on top (`contradicted`: the signal or the clock is wrong);
    per tag: the winner has at least `MIN_SECONDS`, `MIN_SHARE` of the tag's own seconds and
    `MIN_RATIO` times the runner-up, is not the bot, is on the tag's own candidate list when it
    has one, and is the name the earlier rules had picked for the tag (`chosen`) when they picked
    one but could not give it (a `shared_claim` or `elimination_off` tag). Names are compared
    exactly after cleaning ("Ada" is never "Ada Obi"). Several tags may take one name:
    that is how the model's over-splitting of one person is put right. Participants listed under
    one name (a rejoin) are one person."""
    named = {tag: _same(info["name"]) for tag, info in result.items() if info["name"]}
    open_tags = [tag for tag, info in result.items() if not info["name"]]
    if reading.refused:  # the events are there but cannot be used: say why on the tags
        for tag in open_tags:
            result[tag]["timeline"] = reading.refused
        return
    gate = _meeting_gate(reading, named)
    if gate:
        for tag in open_tags:
            result[tag]["timeline"] = gate
        return
    for tag in open_tags:
        info = result[tag]
        if info["note"] not in _TIMELINE_MAY_NAME:
            info["timeline"] = "not_eligible"
            continue
        key, why = meet_timeline.verdict(
            reading.cells.get(tag, {}), bot, reading.totals.get(tag, 0.0)
        )
        if key is None:
            info["timeline"] = why
            continue
        picked = _same(chosen[tag]) if tag in chosen else ""
        candidates = listed.get(tag) or {}
        if (picked and key != picked) or (candidates and key not in candidates):
            info["timeline"] = "disagrees"
            continue
        result[tag] = {"name": reading.shown[key], "how": "meet_timeline", "note": ""}
    _name_by_exclusion(result, reading, bot, listed, chosen)


def _name_by_exclusion(
    result: dict[str, dict[str, str]],
    reading: meet_timeline.Reading,
    bot: set[str],
    listed: dict[str, dict[str, str] | None],
    chosen: dict[str, str],
) -> None:
    """The last route (`how` = `meet_exclusion`; docs/sop/2026-11-23-speaker-name-chain.md): when
    after every other rule exactly ONE tag is unnamed and exactly ONE person in Meet's own
    participant list is left over, the tag is that person's. Called by `_name_by_timeline` once its
    own gates have passed (the events are readable, the clock is checked, a named tag agrees with
    the timeline and none is contradicted), so those hold here too. Changes `result` in place;
    a lone open tag it does not name gets an `exclusion` key saying why. All of these must hold:

    * one open tag only (with two, which of the two is which is a guess), and it is one the other
      rules left for lack of an answer (`_TIMELINE_MAY_NAME`; never a tag with two names of its
      own);
    * one participant left over: anyone in Meet's participant list who is not the note taker and
      not the name of a named tag counts, however little they were lit (a dial-in or a weak
      indicator looks exactly like that, and is a possible speaker too); a rejoin is one person.
      The one exception: when the tag has a candidate list, left-over participants who are not on
      it are ignored (Skribby says the speaker is one of those);
    * the left-over person is on that list when there is one (a list too long to read counts as no
      way to check: refused) and is the name the earlier rules had picked for the tag when they
      picked one they could not give;
    * the tag covers `MIN_EXCLUSION_TAG_SECONDS`, the person was lit over it for `MIN_SECONDS` and
      `MIN_SHARE`, and `EXCLUSION_RATIO` times as long as anybody else, the note taker included
      (`meet_timeline.exclusion_verdict`). That is far under the timeline rule's `MIN_RATIO`:
      being the only one left over is the evidence, Meet need only clearly not point at someone
      else."""
    open_tags = [tag for tag, info in result.items() if not info["name"]]
    if not open_tags:
        return
    if len(open_tags) > 1:
        return  # no key: with several open tags the route is not in play, the tags say why already
    tag = open_tags[0]
    info = result[tag]
    if info["note"] not in _TIMELINE_MAY_NAME:
        info["exclusion"] = "not_eligible"
        return
    taken = {_same(other["name"]) for other in result.values() if other["name"]}
    candidates = listed.get(tag)
    if tag in listed and candidates is None:
        info["exclusion"] = "not_on_list"  # too long a list to read: no way to check
        return
    left = meet_timeline.unaccounted(reading, taken, bot, set(candidates) if candidates else None)
    if len(left) != 1:
        info["exclusion"] = "participants"
        return
    key = left[0]
    if candidates and key not in candidates:
        info["exclusion"] = "not_on_list"
        return
    if tag in chosen and _same(chosen[tag]) != key:
        info["exclusion"] = "disagrees"
        return
    why = meet_timeline.exclusion_verdict(
        reading.cells.get(tag, {}), key, reading.totals.get(tag, 0.0)
    )
    if why != "named":
        info["exclusion"] = why
        return
    result[tag] = {"name": reading.shown[key], "how": "meet_exclusion", "note": ""}


def apply_timeline(
    tags: dict[str, dict[str, str]],
    value: list[dict],
    events: dict | None,
    own_names: tuple[str, ...] = (),
    by_elimination: bool = True,
) -> dict[str, dict[str, str]]:
    """A copy of `tags` (what `_resolve_tags` kept with the evidence) after the timeline step, so
    `scripts/skribby_segments_report.py` can say what the rule would do to a meeting saved before
    it existed. The candidates and the earlier rules' picks are read again from `value` (the
    transcript items, Skribby's own shape or the evidence's: `speaker`, `start`, `end`,
    `potential_speaker_names` as `{"name"}` dicts) with the same bot names the live path uses
    (`own_names`), so the report and the live path cannot disagree about the bot or a claim."""
    result = {tag: dict(info) for tag, info in tags.items()}
    if not events:
        return result
    reading = _timeline_reading(value, events)
    earlier, chosen, listed, bot = _earlier_rules(value, own_names, by_elimination, reading)
    # A tag saved unnamed is read again by today's earlier rules: a meeting saved before the chain
    # (2026-11-23) shows what it would name now. A tag that was named keeps what was saved.
    for tag, info in earlier.items():
        if not result.get(tag, {}).get("name"):
            result[tag] = dict(info)
    _name_by_timeline(result, reading, bot, listed, chosen)
    return result


def _tag_names(
    value: list[dict],
    own_names: tuple[str, ...] = (),
    by_elimination: bool | None = None,
    events: dict | None = None,
    by_timeline: bool | None = None,
) -> dict[str, str]:
    """The name each of Skribby's diarization tags stands for, for the tags where it is knowable.

    Skribby names a tag on some segments (`speaker_name`) and, for a participant it could not pin
    down, lists `potential_speaker_names` instead. A tag takes the one name its own segments carry.

    Only when `by_elimination` is true (the setting `SKRIBBY_NAME_BY_ELIMINATION`, read when None;
    off by default because it can name a tag after someone who is not in the call): a tag with no
    name of its own is named by elimination, its candidates minus the bot's own names, minus every
    name another tag already has (a name an earlier round gave a tag counts: a chain), and only
    when at least one candidate was dropped for being another tag's and exactly one is left. The
    one exception, `sole_candidate`: a tag whose
    segments list one and the same name and nothing else (every segment lists it, at least
    `MIN_SOLE_CANDIDATE_SEGMENTS` segments), a name no other tag has and the bot does not go by,
    in a meeting where at least one tag carries a name of its own (proof that Skribby's naming
    works there), is named after it although nothing was ruled out (meeting 37: the tag Skribby
    split off cleanly but never named). A name two tags would both take goes to neither. Whatever
    stays unclear is left out: the label is "unknown", never a name made up.

    Last, only when `by_timeline` is true (`NAME_BY_MEET_TIMELINE`, read when None) and the
    meeting's speaker `events` are given: a tag still unnamed takes the name of the participant
    Meet's own speaking indicator shows speaking most over it, on a clear margin
    (`_name_by_timeline`)."""
    if by_elimination is None:
        by_elimination = get_settings().skribby_name_by_elimination
    if by_timeline is None:
        by_timeline = get_settings().name_by_meet_timeline
    resolved = _resolve_tags(value, own_names, by_elimination, events, by_timeline)
    return {tag: info["name"] for tag, info in resolved.items() if info["name"]}


def _correct_lines(
    value: list[dict],
    resolved: dict[str, dict[str, str]],
    events: dict | None,
    own_names: tuple[str, ...],
    enabled: bool | None,
):
    """The line-level speaker correction (`app/speaker_correction.py`) of a meeting's transcript
    items, or None when it does not run: switched off (`SPEAKER_LINE_CORRECTION`), not the
    Skribby-transcript mode (`TRANSCRIPT_SOURCE=skribby`; `enabled` None reads both settings), or
    no speaker events (Teams, Zoom, a failed fetch: an exact no-op). Never raises and never stops
    a meeting: anything that goes wrong keeps the lines under their tags' names and is logged by
    exception class only (the data holds names and words)."""
    if enabled is None:
        settings = get_settings()
        enabled = settings.speaker_line_correction and settings.transcript_source == "skribby"
    if not enabled or not events:
        return None
    try:
        from app import speaker_correction  # here: that module reads this one's name helpers

        return speaker_correction.correct(value, resolved, events, own_names)
    except Exception as exc:
        logger.warning(
            "skribby: the speaker line correction failed (%s); lines keep their tags' names",
            type(exc).__name__,
        )
        return None


def _labelled(
    body: dict,
    own_names: tuple[str, ...] = (),
    by_elimination: bool | None = None,
    events: dict | None = None,
    by_timeline: bool | None = None,
    by_correction: bool | None = None,
):
    """`(segments, correction)`: `_segments`' answer and what the line correction did (None when
    it did not run)."""
    value = body.get("transcript")
    if value is None:
        return [], None
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise SkribbyError("get_meeting failed: 'transcript' is not a list of JSON objects")
    if by_elimination is None:
        by_elimination = get_settings().skribby_name_by_elimination
    if by_timeline is None:
        by_timeline = get_settings().name_by_meet_timeline
    resolved = _resolve_tags(value, own_names, by_elimination, events, by_timeline)
    by_tag = {tag: info["name"] for tag, info in resolved.items() if info["name"]}
    correction = _correct_lines(value, resolved, events, own_names, by_correction)
    moves = correction.moves if correction is not None else {}
    segments = []
    for index, item in enumerate(value):
        text = item.get("transcript")
        if isinstance(text, str) and text.strip():
            name = _clean_name(item.get("speaker_name"))
            if not name:
                tag = _tag(item.get("speaker"))
                name = by_tag.get(tag, "") if tag is not None else ""
            segments.append({"speaker": moves.get(index, name), "text": text})
    return segments, correction


def _segments(
    body: dict,
    own_names: tuple[str, ...] = (),
    by_elimination: bool | None = None,
    events: dict | None = None,
    by_timeline: bool | None = None,
    by_correction: bool | None = None,
) -> list[dict[str, Any]]:
    """`{"speaker", "text"}` for each of Skribby's transcript segments (the shape the speaker
    labelling reads). Absent or null is none; any other shape means Skribby changed its response,
    which we refuse to guess about. A segment's own `speaker_name` wins; one without it takes its
    diarization tag's name (see `_tag_names`; `own_names`, the bot's own, are never a speaker).
    Last, a segment Meet's speaking timeline clearly shows another person speaking over moves to
    that person (`app/speaker_correction.py`; `by_correction` None reads `SPEAKER_LINE_CORRECTION`
    and `TRANSCRIPT_SOURCE`); without `events`, or switched off, the labels are exactly the
    tags'."""
    return _labelled(body, own_names, by_elimination, events, by_timeline, by_correction)[0]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return float(value)


def _evidence(
    body: dict,
    own_names: tuple[str, ...],
    by_elimination: bool,
    events: dict | None = None,
    by_timeline: bool = False,
    correction=None,
) -> dict[str, Any] | None:
    """What Skribby sent for a meeting, kept for a few days so a bad labelling can be diagnosed
    (`skribby_segment_evidence`): each transcript item as it came (`speaker` the diarization tag,
    `speaker_name`, the NAMES in `potential_speaker_names`, `start`, `end`, `text`) and, per tag,
    the name the app derived and why (`_resolve_tags`). None when there is no transcript. It
    holds the words people said, so it is never logged or shown. When the line correction ran
    (`correction`, `_labelled`), an item it moved carries `corrected_to` (the label it now has;
    `speaker` stays the original tag, so the effect can be audited and undone) and `correction`
    holds its numbers (`speaker_correction.Correction.summary`). A meeting it did not run for has
    neither key: the record is exactly what it was."""
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
    record = {
        "items": items,
        "tags": _resolve_tags(value, own_names, by_elimination, events, by_timeline),
        "by_elimination": by_elimination,
    }
    if correction is not None and correction.ran:
        for index, label in correction.moves.items():
            items[index]["corrected_to"] = label
        record["correction"] = correction.summary()
    return record


def _cap_events(record: dict[str, Any]) -> dict[str, Any]:
    """`record` kept within `MAX_SPEAKER_EVENTS_BYTES` as JSON text, and `truncated` says what was
    done: None (untouched), `speaking_only` (every participant's events cut to started-speaking,
    stopped-speaking, left and rejoined) or `events_dropped` (no events at all: the names, the
    presence intervals and the times are kept). `bytes` is the size before any cut."""

    def size(value: Any) -> int:
        return len(json.dumps(value, default=str))

    record["bytes"] = size(record)
    record["truncated"] = None
    if record["bytes"] <= MAX_SPEAKER_EVENTS_BYTES:
        return record
    people = [p for p in record["participants"] if isinstance(p, dict)]
    record["participants"] = people
    for person in people:
        listed = person.get("events")
        if isinstance(listed, list):
            person["events"] = [
                e for e in listed if isinstance(e, dict) and e.get("type") in SPEAKING_EVENTS
            ]
    record["truncated"] = "speaking_only"
    if size(record) > MAX_SPEAKER_EVENTS_BYTES:
        for person in people:
            person["events"] = []
        record["truncated"] = "events_dropped"
    return record


def speaker_events_record(body: Any) -> dict[str, Any] | None:
    """What Skribby's `?with-speaker-events=true` answer says about who was speaking, kept as it
    came (`participants`, each with `name`, `presence_intervals` and `events`; the schema is
    documented at https://skribby.io/docs/guides/speaker-timelines but not proven live) with the
    bot's status times (`status_times`, and `recording_started_at`, the first `recording`) so the
    absolute event times can be placed on the transcript's own clock. None when the answer has no
    participants. Reads nothing that can raise: whatever is not the expected shape is left out. It
    holds names: never logged or shown."""
    if not isinstance(body, dict):
        return None
    people = body.get("participants")
    if not isinstance(people, list) or not people:
        return None
    times = [(name, when) for name, when in _events(body) if when is not None]
    started = next((when for name, when in times if name == "recording"), None)
    return _cap_events(
        {
            "participants": people,
            "status_times": [[name, when.isoformat()] for name, when in times],
            "recording_started_at": started.isoformat() if started else None,
        }
    )


def _safe_evidence(
    body: dict,
    own_names: tuple[str, ...],
    by_elimination: bool,
    events: dict | None = None,
    by_timeline: bool = False,
    correction=None,
) -> dict[str, Any] | None:
    """`_evidence`, which is only a record for diagnosis: whatever goes wrong in building it leaves
    it out (logged by exception class only) and never stops the meeting being read."""
    try:
        return _evidence(body, own_names, by_elimination, events, by_timeline, correction)
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
# nothing, being removed is `evicted` (the pipeline's removal notice), our own stop
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
    body: dict,
    now: datetime,
    bot_id: str | None = None,
    own_names: tuple[str, ...] = (),
    speaker_events: dict | None = None,
) -> BotMeeting:
    """Skribby's bot body as a `BotMeeting`, in the vocabulary the pipeline reads (see the SOP's
    table). `bot_id` is the id the caller asked for (the recording's handle is made from it; the
    body's own `id` is the fallback). `own_names` are the names the bot itself goes by (a candidate
    speaker name that is one of them is never used). `speaker_events` is the meeting's
    speaker-events record (`speaker_events_record`), given by the caller for a finished bot when
    `NAME_BY_MEET_TIMELINE` or `SPEAKER_LINE_CORRECTION` is on; without it the timeline names
    nothing and no line moves. `provider_id` is left for the caller to set."""
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
        by_timeline = get_settings().name_by_meet_timeline
        url = _recording_url(body)
        handle = _handle_for(bot_id or body.get("id"))
        reason = _FINISHED_REASON[stop] if stop in _FINISHED_REASON else stop
        segments, correction = _labelled(
            body, own_names, by_elimination, speaker_events, by_timeline
        )
        return BotMeeting(
            status="completed",
            segments=segments,
            evidence=_safe_evidence(
                body, own_names, by_elimination, speaker_events, by_timeline, correction
            ),
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


def _refusal_text(response: httpx.Response) -> str:
    """What Skribby said about a refused create, lower-cased and bounded, only to see which
    parameter it names. Never logged."""
    try:
        return response.text[:4000].lower()
    except Exception:  # an unreadable body: nothing is blamed in particular
        return ""


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
                select(Meeting.provider_bot_id)
                .where(
                    Meeting.bot_provider == "skribby",
                    Meeting.platform == platform,
                    Meeting.native_id == code,
                    Meeting.status == "bot_sent",
                    Meeting.provider_bot_id.is_not(None),
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
                    Meeting.provider_bot_id.is_(None),
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
            row.provider_bot_id = bot_id
            row.completion_reason = None
            row.bot_state_seen_at = None
            session.commit()
            return True

    def bots_for_prefix(self, prefix: str) -> list[str]:
        from sqlalchemy import select

        from app.models import Meeting

        with self._session() as session:
            rows = session.scalars(
                select(Meeting.provider_bot_id)
                .where(
                    Meeting.bot_provider == "skribby",
                    Meeting.provider_bot_id.like(prefix + "%"),
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
        chat_notice: str | None = None,
        bot_name: str | None = None,
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
            # Off: a quiet stretch must not send the bot away (a bot must not leave for silence; the
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
            # The person's own note taker name for this meeting (`app/bot_names.py`), else the
            # server default. Sent on every platform, signed in or not: whether Skribby shows it on
            # a signed-in Google Meet bot is unproven (`docs/sop/2026-11-14-per-user-bot-name.md`).
            "bot_name": (bot_name or "").strip() or self._bot_name,
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
        # applies. Sent only to a model Skribby lists with it (`VOCABULARY_MODELS`), so not when
        # transcription is off (`none`) and not to ElevenLabs.
        terms = _keywords(vocabulary or [])
        if terms and takes_vocabulary(self._model):
            body["custom_vocabulary"] = terms
        # The line the bot posts in the meeting chat when it joins. Skribby's create-bot page: an
        # optional string, "Whenever the bot joins the meeting, it'll send this message to the
        # chat", with no stated limit and no per-platform list (read 2026-11-14), so it is never
        # sent unless the caller was asked to (`BOT_CHAT_NOTICE_ENABLED`, `app.chat_notice`).
        if chat_notice:
            body["initial_chat_message"] = chat_notice
        return body

    def _post_bot(self, payload: dict[str, Any]) -> httpx.Response:
        with self._http(connect=SEND_CONNECT_SECONDS) as client:
            return client.post("/bot", json=payload)

    def create(
        self,
        payload: dict[str, Any],
        used: dict[str, Any] | None = None,
        fallback_notice: str | None = None,
    ) -> str:
        """`POST /bot`; the new bot's id. Errors follow the interface: never connected is plain,
        no clear answer is `SkribbySendUncertain`, a 4xx is plain.

        `custom_vocabulary` and `initial_chat_message` have never been exercised against the real
        service: if Skribby answers a validation error (400 or 422) to a create that carries either,
        the create is made ONCE more without them (a 4xx means no bot was made, so this cannot make
        two) and that is logged by name, never with the terms or the line. A bot without the owner's
        vocabulary or the chat line beats no bot.

        The same goes for a person's own note taker name: if the create carried a `bot_name` other
        than the server default and Skribby answers 400 or 422, the create is made once more with
        the default (the answer's own text decides what is dropped: a refusal that names only
        `bot_name` keeps the optional fields, one that names only an optional field keeps the name,
        anything else drops both). The chat notice names the bot, so when the name is replaced it
        is replaced too, with `fallback_notice` (the same line built with the default name): the
        consent line must still go out and must not name a bot that did not join. Without a
        fallback (a direct caller) a notice that holds the refused name is dropped. When the name
        was replaced, `used["bot_name"]` (the dictionary the caller passed) is the default that the
        accepted create carried."""
        try:
            response = self._post_bot(payload)
            optional = [name for name in OPTIONAL_PARAMETERS if name in payload]
            custom_name = payload.get("bot_name") != self._bot_name
            if response.status_code in VOCABULARY_RETRY_STATUSES and (optional or custom_name):
                said = _refusal_text(response)
                name_blamed = custom_name and "bot_name" in said
                optional_blamed = [n for n in optional if n in said]
                drop_name = custom_name and (name_blamed or not optional_blamed)
                drop_optional = optional and (optional_blamed or not name_blamed)
                dropped = []
                if drop_name:
                    dropped.append("person's own note taker name")
                if drop_optional:
                    dropped += [OPTIONAL_PARAMETERS[name] for name in optional]
                logger.warning(
                    "Skribby refused the bot create with HTTP %s; retrying once without the %s",
                    response.status_code,
                    " and the ".join(dropped),
                )
                custom = payload["bot_name"] if drop_name else None
                payload = {
                    k: v
                    for k, v in payload.items()
                    if not (drop_optional and k in OPTIONAL_PARAMETERS)
                }
                if drop_name:
                    payload["bot_name"] = self._bot_name
                    notice = payload.get("initial_chat_message")
                    if isinstance(notice, str) and fallback_notice:
                        payload["initial_chat_message"] = fallback_notice
                    elif (
                        isinstance(notice, str)
                        and custom
                        and chat_notice.names_the_bot(notice, custom)
                    ):
                        payload.pop("initial_chat_message")  # it names the refused name
                    if used is not None:
                        used["bot_name"] = self._bot_name
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

    def get_bot(
        self, bot_id: str, *, timeout: float | None = None, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        bot_id = bot_uuid(bot_id)
        try:
            with self._http(timeout=timeout) as client:
                response = client.get(f"/bot/{bot_id}", params=params)
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
        self._events_lock = threading.Lock()
        self._events_kept: OrderedDict[str, dict] = OrderedDict()
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

    def _look(
        self,
        bot_id: str,
        *,
        timeout: float | None = None,
        own_names: tuple[str, ...] = (),
    ) -> BotMeeting:
        body = self._client.get_bot(bot_id, timeout=timeout)
        # Never a speaker: the server default and the account's name (`client.own_names`), the
        # meeting's own stored name when the caller knows it (`own_names`), and the name Skribby
        # itself says the bot has (`bot_name` in the body, possibly cut to the platform's limit), so
        # a custom name is excluded even when the caller could not say it.
        reported = body.get("bot_name")
        names = (
            *self._client.own_names,
            *own_names,
            *((reported,) if isinstance(reported, str) else ()),
        )
        info = map_bot(
            body,
            self._clock(),
            bot_id,
            tuple(dict.fromkeys(n for n in names if n)),
            speaker_events=self._events_for_naming(body, bot_id, timeout),
        )
        info.provider_id = bot_id
        if info.recording_id is not None:
            self._remember_handle(bot_id)
        if info.status in TERMINAL:
            self._forget_bot(bot_id)
        return info

    def _events_for_naming(self, body: dict, bot_id: str, timeout: float | None):
        """The speaker events the timeline naming (`NAME_BY_MEET_TIMELINE`) and the line correction
        (`SPEAKER_LINE_CORRECTION`, with `TRANSCRIPT_SOURCE=skribby`) read, for a bot that
        has finished with a transcript: the answer of a second request (`with-speaker-events`),
        because the plain one carries none. None for any other bot (one request per poll is
        enough while it is live), when the setting is off, or when the request fails or answers in
        a shape that is not the documented one: then the tags are named without the timeline, as
        before. Never raises; logged by exception class only (a message could hold a name).

        A finished bot is looked at more than once (the status poll, then the transcript step),
        so a good answer is kept in this process (the last `EVENTS_KEPT` bots, as the sent codes
        are): one extra request per meeting and process, not per look. A failure is not kept, so
        the next look asks again. The events of a finished meeting do not change."""
        settings = get_settings()
        if (
            body.get("status") != "finished"
            or not (
                settings.name_by_meet_timeline
                or (settings.speaker_line_correction and settings.transcript_source == "skribby")
            )
            or not isinstance(body.get("transcript"), list)
        ):
            return None
        with self._events_lock:
            if bot_id in self._events_kept:
                return self._events_kept[bot_id]
        try:
            record = speaker_events_record(
                self._client.get_bot(
                    bot_id, timeout=timeout, params={"with-speaker-events": "true"}
                )
            )
        except Exception as exc:
            logger.warning("skribby: no speaker events for naming (%s)", type(exc).__name__)
            return None
        if record is not None:
            with self._events_lock:
                self._events_kept[bot_id] = record
                while len(self._events_kept) > EVENTS_KEPT:
                    self._events_kept.popitem(last=False)
        return record

    # ---- the BotProvider interface ----

    # The owner's vocabulary can be passed to `send_bot` (`vocabulary=`): callers check this.
    accepts_vocabulary = True
    # So can the line the bot posts in the meeting chat when it joins (`chat_notice=`, see
    # `app.chat_notice`): off unless the caller was told to send one.
    accepts_chat_notice = True
    # And the name of the bot (`bot_name=`, the person's own note taker name), and, when looking at
    # a bot, the names it goes by (`own_names=`), so a custom name is never named as a speaker.
    accepts_bot_name = True
    accepts_own_names = True

    def send_bot(
        self,
        meeting_url: str,
        vocabulary: list[str] | None = None,
        chat_notice: str | None = None,
        bot_name: str | None = None,
        chat_notice_fallback: str | None = None,
    ) -> str:
        platform, code, canonical = _parse(meeting_url)  # UnsupportedMeetingUrl for a bad link
        payload = self._client.payload(platform, code, canonical, vocabulary, chat_notice, bot_name)
        lock = threading.Lock()
        state: dict[str, Any] = {"started": False, "bot_id": None, "abandoned": False, "name": None}

        def attempt() -> str:
            self._refuse_if_held(platform, code)
            with lock:
                if state["abandoned"]:  # the caller already gave up: nothing may be sent now
                    raise SkribbyError("send_bot abandoned before anything was sent")
                state["started"] = True
            used: dict[str, Any] = {}
            bot_id = self._client.create(payload, used, chat_notice_fallback)
            with lock:
                late = state["abandoned"]
                state["bot_id"] = bot_id
                state["name"] = used.get("bot_name")
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
        fallback = state.get("name")
        if fallback:
            bot_id = SentBotId(bot_id)
            bot_id.bot_name = fallback
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

    def get_meeting(
        self, platform: str, native_id: str, own_names: tuple[str, ...] = ()
    ) -> BotMeeting:
        """The newest bot we know on the code. Skribby cannot look up by meeting code, so a bot we
        have no record of (an unconfirmed send whose answer was lost) is not found."""
        for bot_id in self._candidates(platform, native_id):
            try:
                return self._look(bot_id, own_names=own_names)
            except SkribbyNotFound:
                self._forget_bot(bot_id)
        raise SkribbyNotFound("get_meeting failed: no bot of ours is known on that meeting")

    def get_meeting_by_id(
        self, provider_bot_id: str | int, own_names: tuple[str, ...] = ()
    ) -> BotMeeting:
        return self._look(bot_uuid(provider_bot_id), own_names=own_names)

    def get_speaker_events(self, provider_bot_id: str | int) -> dict[str, Any] | None:
        """Who was speaking when, from the meeting platform itself (see `speaker_events_record`),
        for the evidence kept to diagnose a bad labelling. A request of its own, separate from the
        fetch that labels speakers, so nothing about it can break that: never raises, and any
        failure (the request, a shape that is not what the docs describe) is None, logged by
        exception class only (never a name)."""
        try:
            body = self._client.get_bot(
                bot_uuid(provider_bot_id), params={"with-speaker-events": "true"}
            )
            return speaker_events_record(body)
        except Exception as exc:
            logger.warning("skribby: no speaker events kept (%s)", type(exc).__name__)
            return None

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


def require_configured(settings) -> None:
    """Stop a process at startup when `SKRIBBY_API_KEY` is not set, naming the setting (never a
    key): with no key no bot can be sent, and it is better to fail at start than at the first
    meeting. Called by the three processes' entry points (web's factory, worker, scheduler), not
    while the settings are built, so tools and tests that never send a bot need no key."""
    if not settings.skribby_api_key.strip():
        raise ValueError("SKRIBBY_API_KEY is not set: no bot can be sent to a meeting")


def check_region_unchanged(settings, session_factory: Callable[[], Any] | None = None) -> int:
    """Say loudly, at startup, when Skribby meetings of ANOTHER region still hold data.

    Skribby's `eu` and `jp` regions are separate accounts: a bot sent to one answers 404 in the
    other. The processes still start (a Low risk is no reason to take the app down); instead every
    Skribby operation on such a meeting is refused with `BotRegionMismatch` (see
    `provider_for`), so a lookup never reads 404 as "no speaker names" and a delete is never marked
    done: they wait and are logged, until the region is set back. Logs an ERROR naming the setting
    and returns how many meetings are affected. No-op while Skribby is not configured (no key); a
    database that cannot be reached is a warning."""
    if not settings.skribby_api_key.strip():
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
