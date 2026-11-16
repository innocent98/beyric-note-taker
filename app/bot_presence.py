"""Who is in a call, and when the bot should leave because only bots are left.

Skribby's own `last_person_detection` counts every participant, so a call whose people have all
gone but where another note taker stays (Fireflies, Otter, ...) never ends it: our bot sat there
for 33 minutes in meeting 38 (2026-11-16). This module is the rule we add on top. It is pure: no
clock, no network, no database. The caller hands it the participant list the provider returned and
the time, and it answers "leave" or "stay". Because the answer is worked out from the times in the
list itself, nothing about it has to be remembered between polls (a restart loses nothing).

Two rules, both ending in "leave":

* **only bots** (`ONLY_BOTS`): at least one person has been in the call, none is in it now, and the
  last of them left `only_bots_after` seconds ago or more. A person who comes back is in the call
  again, so the wait starts over by itself.
* **nobody came** (`NOBODY_CAME`): no person has been seen at all, and the bot has been in the call
  `nobody_after` seconds or more. Our bot joins about a minute early, so before the first person
  arrives it is often alone, or with another bot; this rule must never fire on a call that has had
  a person, and it is not reached by one (see `decide`).

Telling a bot from a person is a name heuristic (`is_bot_name`) and is deliberately one-sided: a
name we are not sure about is a PERSON. The worst case of a wrong "person" is the status quo (the
bot stays and more hours are used); the worst case of a wrong "bot" is ending a real meeting, so
the list only holds names and words that say "bot" on their own.

Names are personal data: nothing here logs or returns one beyond the caller's own list.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

ONLY_BOTS = "only_bots"
NOBODY_CAME = "nobody_came"

# Words that, as a whole word, make a name a bot's: a role no person puts in their own name.
# "bot" must be a whole word, so "Abbott" and "Robotham" stay people. Brand words are the ones that
# are not also ordinary names (so no "otter", "grain", "gong", "fellow", "jamie").
_BOT_WORDS = frozenset(
    {
        "bot",
        "bots",
        "notetaker",
        "notetakers",
        "recorder",
        "transcriber",
        "assistant",
        "fireflies",
        "tldv",
        "avoma",
        "krisp",
        "sembly",
        "supernormal",
        "meetgeek",
        "circleback",
        "tactiq",
        "airgram",
    }
)
# Whole-word runs that say "bot" only together: `note taker`, `Otter.ai`, `Read.ai`, `tl;dv`.
_BOT_PHRASES = (
    ("note", "taker"),
    ("note", "takers"),
    ("otter", "ai"),
    ("read", "ai"),
    ("tl", "dv"),
)
# "assistant" is the one generic word a real person can carry (a job title). Beside one of these it
# reads as a job, so the name stays a person's.
_JOB_BEFORE = frozenset(
    {
        "executive",
        "administrative",
        "admin",
        "personal",
        "teaching",
        "research",
        "legal",
        "medical",
        "dental",
        "office",
        "graduate",
        "vice",
        "associate",
        "senior",
        "junior",
        "assistant",
        "virtual",
    }
)
_JOB_AFTER = frozenset(
    {
        "professor",
        "manager",
        "director",
        "principal",
        "teacher",
        "coach",
        "dean",
        "editor",
        "secretary",
        "attorney",
        "president",
        "vp",
        "to",
        "chief",
        "superintendent",
        "pastor",
        "pa",
        "ea",
        "sales",
    }
)
_WORD = re.compile(r"[a-z0-9]+")


def _words(name: str) -> list[str]:
    return _WORD.findall(name.casefold())


def is_bot_name(name: Any, own_names: tuple[str, ...] = ()) -> bool:
    """Whether a participant's name is a bot's. Our own bot's names (`own_names`, the name it was
    sent with) match as a whole; everything else is read by the word lists above. Anything that is
    not a plain, non-empty string, and any name no rule recognises, is a person."""
    if not isinstance(name, str):
        return False
    words = _words(name)
    if not words:
        return False
    if any(words == _words(own) for own in own_names if own):
        return True
    for index, word in enumerate(words):
        if word == "assistant":
            before = words[index - 1] if index else None
            after = words[index + 1] if index + 1 < len(words) else None
            if before in _JOB_BEFORE or after in _JOB_AFTER:
                continue
            return True
        if word in _BOT_WORDS:
            return True
    return any(
        tuple(words[i : i + len(phrase)]) == phrase
        for phrase in _BOT_PHRASES
        for i in range(len(words) - len(phrase) + 1)
    )


@dataclass(frozen=True)
class Who:
    """One participant as the decision reads them. `here` is True while they are in the call, and
    also when their times could not be read (an unreadable record is treated as present). When they
    are not here, `last_left` is when they last left."""

    name: str
    here: bool
    last_left: datetime | None = None


def parse_time(value: Any) -> datetime | None:
    """A moment from what Skribby sends: an ISO 8601 string, or an integer that is Unix
    milliseconds (the speaker-timelines page's examples have 13-digit integers) or seconds. Zero,
    empty and anything unreadable is None. The unit of the integers is not stated on the page, so a
    value is read as milliseconds only when it is too large to be seconds."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        seconds = value / 1000 if value >= 1e11 else value
        try:
            return datetime.fromtimestamp(seconds, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        try:
            stamp = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)
    return None


def _presence(person: dict) -> tuple[bool, datetime | None]:
    """`(here, last_left)` for one participant record. The `presence_intervals` are the source:
    the last interval being open (no `left_at`) means in the call. Without intervals the record's
    own `left_at` says it. A time that is present but cannot be read means "here", never "gone"."""
    intervals = person.get("presence_intervals")
    if isinstance(intervals, list) and intervals:
        last = intervals[-1]
        if not isinstance(last, dict):
            return True, None
        if not last.get("left_at"):
            return True, None
        left = parse_time(last.get("left_at"))
        return (True, None) if left is None else (False, left)
    if not person.get("left_at"):
        return True, None
    left = parse_time(person.get("left_at"))
    return (True, None) if left is None else (False, left)


def read_participants(raw: Any) -> list[Who] | None:
    """The participant list of a Skribby bot body as `Who` entries, or None when the body has no
    list at all (then nobody is judged). An entry that is not an object, or has no readable name,
    is a person who is here: it can only make the bot stay."""
    if not isinstance(raw, list):
        return None
    found = []
    for item in raw:
        if not isinstance(item, dict):
            found.append(Who("", True))
            continue
        name = item.get("name")
        here, left = _presence(item)
        found.append(Who(name if isinstance(name, str) else "", here, left))
    return found


def decide(
    people: list[Who] | None,
    in_since: datetime | None,
    now: datetime,
    *,
    only_bots_after: float,
    nobody_after: float,
    own_names: tuple[str, ...] = (),
) -> str | None:
    """`ONLY_BOTS`, `NOBODY_CAME`, or None (stay).

    `people` is the provider's participant list (None when it gave none: stay). An EMPTY list also
    stays: it is what a provider that does not fill the list while the call runs would show for
    every call, so it cannot be told from a call with no one in it (the provider's own
    `empty_meeting_timeout` ends a truly empty call). `in_since` is when the bot got into the call
    (None when unknown: the "nobody came" rule then waits)."""
    if not people:
        return None
    persons = [who for who in people if not is_bot_name(who.name, own_names)]
    if any(who.here for who in persons):
        return None
    if persons:
        left = [who.last_left for who in persons if who.last_left is not None]
        if len(left) != len(persons):
            return None  # a person whose leaving time is unknown is not judged
        if (now - max(left)).total_seconds() >= only_bots_after:
            return ONLY_BOTS
        return None
    if in_since is not None and (now - in_since).total_seconds() >= nobody_after:
        return NOBODY_CAME
    return None
