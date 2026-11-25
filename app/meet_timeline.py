"""Meet's own "who is speaking" signal, read against Skribby's diarization tags.

Skribby records the meeting platform's speaking indicator (`participants[].events`, kept next to
the transcript: https://skribby.io/docs/guides/speaker-timelines). This module turns it into
"seconds each participant was speaking while each tag's segments were playing" and decides which
tag may be named after which participant (docs/sop/2026-11-17-name-by-meet-timeline.md).

Pure functions, no database and no settings: `app/skribby.py::_resolve_tags` calls them,
`scripts/skribby_segments_report.py` prints from them. Nothing here logs or keeps a name or a word.

The signal is the platform's speaking indicator, not a proof of who said a line: it lags, it lights
for a cough, and two people talking at once both light. That is why a name is only given on a clear
margin (`MIN_SECONDS`, `MIN_RATIO`), only after the clock has been checked, and only in a meeting
where the timeline already agrees with the names Skribby gave itself.
"""

import bisect
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

# The fewest seconds a participant must have been speaking over a tag's segments to name the tag
# after them. Meeting 39: the tags that were right and clear had 75.9 to 449 s; the two weak ones
# 11.5 and 10.9 s. Anywhere between suits that one meeting; 30 s is a few real turns, not a cough.
MIN_SECONDS = 30.0
# The winner must have at least this many times the seconds of the runner-up. Meeting 39: the right
# tags were 3.55x to 31x; the weak ones 2.45x and 1.45x. A wrong name is worse than "Unknown
# speaker", so the line sits just above the weak ones (one meeting's data: say so, do not trust it
# further than that).
MIN_RATIO = 3.0
# The winner must also have been speaking over at least this share of the tag's own seconds.
# Meeting 39's right winners held 39% to 61% of their tags (Meet's indicator lights for only
# about half of a person's speech), so 20% never blocks them; what it blocks is a tag whose real
# speaker has no usable Meet entry (a dial-in, events cut by the size cap) and which a talker-over
# would otherwise name: 35 s over a 1,000 s tag is 3.5%.
MIN_SHARE = 0.2
# A speaking interval longer than this is not read: Meet's indicator flickers with every pause,
# so one lit stretch of ten minutes means a `stopped-speaking` went missing, and what lies between
# is not known.
MAX_SPAN_SECONDS = 600.0
# Naming by exclusion (`unaccounted`, `exclusion_verdict`): the fewest seconds of its own a tag must
# cover. The tag that failed on 2026-11-23 held 193 s; a tag with under half of that (90 s, three
# times `MIN_SECONDS`) is a few turns of somebody, and a person is not named from a few turns when
# the only evidence is who is left over.
MIN_EXCLUSION_TAG_SECONDS = 90.0
# ...and the left-over person must have been lit over it this many times as long as anybody else
# (the note taker included). The 2026-11-23 tag: 84.6 s against 43.6 s, 1.94x. 1.5x asks for a
# clear lead over a person who already has a tag, without the 3.0x of `MIN_RATIO`, which is the
# line for naming a tag from Meet alone and which would have refused that meeting. Chosen from
# that one case and the over-split signature (near ties), not measured further.
EXCLUSION_RATIO = 1.5
# One-to-one naming (`app/skribby.py::_one_to_one`): a participant in the call who was lit for at
# least this long and has no tag of their own is a voice the model merged into someone else's tag
# (meeting 32's third person spoke about 30 s inside another's tag), so it vetoes the naming. Far
# under `MIN_SECONDS`: this is a veto (it refuses to name), not a naming (it risks a wrong name).
# A cough or one "mm-hm" is a few seconds; ten is a sentence or two.
MIN_EXTRA_SECONDS = 10.0
# How far either way the clock fit looks (seconds), and how much better a shift must be (a share)
# before the Meet clock is called out of step with the transcript's.
SEARCH_SECONDS = 30
CLOCK_GAIN = 1.1

Group = Callable[[str], tuple[str, str]]  # a participant's name -> (comparison key, shown name)


def _identity(name: str) -> tuple[str, str]:
    return name, name


def seconds(value) -> float | None:
    """A time as seconds: Skribby's example shows 13-digit integers (Unix milliseconds); anything
    that large is divided by 1000. Not a number: None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value / 1000 if value >= 1e11 else float(value)


def merge(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[list[float]] = []
    for start, end in sorted(i for i in intervals if i[1] > i[0]):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def overlap(intervals: list[tuple[float, float]], start: float, end: float) -> float:
    """Seconds of [start, end] covered by `intervals` (merged, sorted)."""
    ends = [i[1] for i in intervals]
    total = 0.0
    for index in range(bisect.bisect_right(ends, start), len(intervals)):
        a, b = intervals[index]
        if a >= end:
            break
        total += max(0.0, min(b, end) - max(a, start))
    return total


def speaking_intervals(
    record, group: Group = _identity
) -> dict[str, list[tuple[float, float]]] | None:
    """`{participant name: speaking intervals, seconds}` from the saved events, or None when the
    record is not in the shape this reads. A `started-speaking` opens an interval and
    `stopped-speaking` or `left` closes it. An interval that never closes is **dropped**, not
    extended: a connection that dropped mid-sentence sends no `stopped-speaking`, and an interval
    run to the end of the record would have that person "speaking" over every later tag (the bot
    leaving while someone talks leaves one open interval in an ordinary meeting, so this is not
    rare, and one short interval is all it costs). An interval longer than `MAX_SPAN_SECONDS` is
    dropped for the same reason. `group` says which names are one person (a participant who left
    and rejoined is listed once per visit under one name): their events are read together, the
    first spelling is the name shown. A name `group` gives an empty key is skipped."""
    people = record.get("participants") if isinstance(record, dict) else None
    if not isinstance(people, list):
        return None
    named: dict[str, tuple[str, list]] = {}
    for person in people:
        if not isinstance(person, dict) or not isinstance(person.get("name"), str):
            continue
        key, shown = group(person["name"])
        if not key:
            continue
        events = [e for e in person.get("events") or [] if isinstance(e, dict)]
        stamped = sorted(
            (t, e.get("type")) for e in events if (t := seconds(e.get("timestamp"))) is not None
        )
        named.setdefault(key, (shown, []))[1].extend(stamped)
    if not named:
        return None
    result: dict[str, list[tuple[float, float]]] = {}
    for shown, stamped in named.values():
        opened, spans = None, []
        for t, kind in sorted(stamped):
            if kind == "started-speaking" and opened is None:
                opened = t
            elif kind in ("stopped-speaking", "left") and opened is not None:
                if t - opened <= MAX_SPAN_SECONDS:
                    spans.append((opened, t))
                opened = None
        result[shown] = merge(spans)
    return result


def anchor(record, spans) -> tuple[float | None, str]:
    """The Meet clock's zero on the transcript's clock: (seconds to subtract, how it was got).
    Times that are small numbers are taken as already relative to the recording."""
    sample = [t for spans_ in spans.values() for span in spans_ for t in span]
    if not sample or min(sample) < 1e9:
        return 0.0, "times read as seconds from the recording start"
    stamp = record.get("recording_started_at") if isinstance(record, dict) else None
    try:
        return datetime.fromisoformat(stamp).timestamp(), (
            f"times read as Unix time, placed from the bot's `recording` status at {stamp}"
        )
    except (TypeError, ValueError):
        return None, "times are Unix time but the bot's `recording` status time was not kept"


def heard_by_tag(items: list[dict], tag_of: Callable[[dict], object] | None = None) -> dict:
    """Each tag's timed segments as `(start, end)` seconds (tag -> list); untagged or untimed
    segments are left out. `tag_of` reads an item's tag (default: its `speaker`)."""
    heard: dict = {}
    for item in items:
        tag = tag_of(item) if tag_of else item.get("speaker")
        a, b = seconds(item.get("start")), seconds(item.get("end"))
        if a is not None and b is not None and b > a and tag is not None:
            heard.setdefault(tag, []).append((a, b))
    return heard


def clock_fit(spans: dict, heard: dict) -> tuple[int, float, float]:
    """How well Meet's clock fits the segments' clock: `(best shift, overlap now, overlap at the
    best shift)`. `spans` is each participant's speaking intervals already placed on the
    transcript's clock; `heard` is `heard_by_tag`. The shift is whole seconds, up to
    SEARCH_SECONDS either way, by which Meet's times would be moved later."""
    union = merge([span for v in spans.values() for span in v])

    def total_overlap(shift_meet: int) -> float:
        """Overlap if Meet's times were moved `shift_meet` seconds later."""
        return sum(
            overlap(union, a - shift_meet, b - shift_meet) for v in heard.values() for a, b in v
        )

    best = max(range(-SEARCH_SECONDS, SEARCH_SECONDS + 1), key=total_overlap)
    return best, total_overlap(0), total_overlap(best)


def clock_is_fine(best: int, here: float, there: float) -> bool:
    """The report's own test for "no shift fits clearly better" (the opposite of its warning)."""
    return not (best != 0 and there > here * CLOCK_GAIN)


@dataclass
class Reading:
    """What the timeline says about a meeting. `cells[tag][key]` is the seconds participant `key`
    was speaking over that tag's segments; `shown[key]` is the participant's name as Meet shows
    it. `refused` is why nothing may be named from it (None when it may be)."""

    cells: dict = field(default_factory=dict)
    totals: dict = field(default_factory=dict)  # tag -> seconds its segments cover
    shown: dict[str, str] = field(default_factory=dict)
    # key -> seconds that participant was lit in all (clock-free: it needs no placing on the
    # transcript's clock, so it is filled even when the clock is refused)
    lit: dict[str, float] = field(default_factory=dict)
    # False when nobody has a usable speaking interval (the names are known, `lit` says nothing:
    # `events_dropped`): a person absent from `lit` was not seen silent, only unseen
    lit_known: bool = True
    # `cells` read again with Meet's times moved by the shift that fits best, set only when the
    # clock is refused as `clock_shifted` (`cells` is then the zero-shift reading)
    shifted_cells: dict = field(default_factory=dict)
    refused: str | None = None


def read(items: list[dict], record, group: Group, tag_of=None) -> Reading:
    """The timeline of `record` against the tags of `items` (dicts with `start`, `end` and a tag).
    `refused` says why it cannot be used: `no_events` (nothing kept, not readable, or nobody has a
    usable speaking interval),
    `clock_unplaced` (Unix times with no `recording` time to place them), `no_times` (no timed
    segment) or `clock_shifted` (a shift of up to 30 s would fit clearly better, so the Meet clock
    is not trusted at zero)."""
    spans = speaking_intervals(record, group) if record else None
    if not spans:
        return Reading(refused="no_events")
    # `spans` is keyed by the shown name; keys are the same strings here by construction of `group`
    # below, so the cells are keyed by the comparison key.
    keys = {shown: group(shown)[0] for shown in spans}
    people = {
        "shown": {keys[n]: n for n in spans},  # everyone in the participant list, lit or not
        "lit": {keys[n]: sum(b - a for a, b in v) for n, v in spans.items()},
    }
    if not any(spans.values()):
        # Participants with no usable speaking interval at all (the size cap's `events_dropped`
        # keeps the names and empties every list; some platforms may send names only): no timeline,
        # the same as no events. The names are still the call's participant list.
        return Reading(refused="no_events", lit_known=False, **people)
    zero, _ = anchor(record, spans)
    if zero is None:
        return Reading(refused="clock_unplaced", **people)
    spans = {n: merge([(a - zero, b - zero) for a, b in v]) for n, v in spans.items()}
    heard = heard_by_tag(items, tag_of)
    if not heard:
        return Reading(refused="no_times", **people)

    def cells_at(shift: float = 0.0) -> dict:
        """Overlaps if Meet's times were moved `shift` seconds later (as `clock_fit` moves them)."""
        return {
            tag: {
                keys[n]: sum(overlap(spans[n], a - shift, b - shift) for a, b in merge(intervals))
                for n in spans
            }
            for tag, intervals in heard.items()
        }

    cells = cells_at()
    totals = {tag: sum(b - a for a, b in merge(intervals)) for tag, intervals in heard.items()}
    reading = Reading(cells=cells, totals=totals, **people)
    best, here, there = clock_fit(spans, heard)
    if not clock_is_fine(best, here, there):
        reading.refused = "clock_shifted"
        reading.shifted_cells = cells_at(best)
    return reading


def verdict(
    cells: dict[str, float],
    bot: set[str],
    total: float,
    min_seconds: float = MIN_SECONDS,
    ratio: float = MIN_RATIO,
    share: float = MIN_SHARE,
) -> tuple[str | None, str]:
    """`(key, "named")` for the participant who may name a tag whose overlaps are `cells` and
    whose segments cover `total` seconds, or `(None, why)`: `no_overlap` (nobody was speaking over
    it), `bot` (the winner is the note taker itself), `too_little` (the winner has under
    `min_seconds`), `small_share` (under `share` of the tag's seconds: crosstalk, not the tag's
    speaker) or `not_clear` (under `ratio` times the runner-up). The bot takes part in the
    ranking: if it ranks first the tag is not named, it is never skipped to the person below."""
    ranked = sorted(cells.items(), key=lambda kv: kv[1], reverse=True)
    if not ranked or ranked[0][1] <= 0:
        return None, "no_overlap"
    top, top_seconds = ranked[0]
    if top in bot:
        return None, "bot"
    if top_seconds < min_seconds:
        return None, "too_little"
    if top_seconds < share * total:
        return None, "small_share"
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if top_seconds < ratio * second:
        return None, "not_clear"
    return top, "named"


def unaccounted(
    reading: Reading, taken: set[str], bot: set[str], only: set[str] | None = None
) -> list[str]:
    """The participants (comparison keys) who are neither the note taker nor the name of a tag
    already named (`taken`): the people a tag nobody else has could be. **Everyone in Meet's
    participant list counts**, however little they were lit or whether they have events at all: a
    dial-in or a person the indicator rarely lights is exactly the speaker Meet cannot see, and
    leaving them out would let the one person who is visible look like the only one left. `only`,
    when given, limits the count to those keys (a tag's own candidate list: Skribby says the
    speaker is one of them). Participants listed under one name are already one key."""
    return [
        key
        for key in reading.shown
        if key not in taken and key not in bot and (only is None or key in only)
    ]


def exclusion_verdict(cells: dict[str, float], key: str, total: float) -> str:
    """`"named"` when participant `key`, the only one left over, may be the speaker of a tag whose
    overlaps are `cells` and whose segments cover `total` seconds, else why not: `tag_too_short`
    (under `MIN_EXCLUSION_TAG_SECONDS`), `too_little` (`key` was lit under `MIN_SECONDS` over the
    tag), `small_share` (under `MIN_SHARE` of the tag's seconds: the same crosstalk guard as
    `verdict`, which also stops a tag whose real speaker has no Meet entry from being given to the
    one person left over who talked over it), `beaten` (somebody else, the note taker included,
    was lit as long or longer over it) or `too_close` (the best of the others was lit over it for
    more than `key` / `EXCLUSION_RATIO`: a tag lit about as much over someone who already has a
    tag is as likely that person's, an over-split, as the one left over's). The ratio is far under
    `MIN_RATIO`: being left over is the evidence, Meet only has to point clearly the same way."""
    if total < MIN_EXCLUSION_TAG_SECONDS:
        return "tag_too_short"
    mine = cells.get(key, 0.0)
    if mine < MIN_SECONDS:
        return "too_little"
    if mine < MIN_SHARE * total:
        return "small_share"
    rest = max((seconds for other, seconds in cells.items() if other != key), default=0.0)
    if rest >= mine:
        return "beaten"
    if mine < EXCLUSION_RATIO * rest:
        return "too_close"
    return "named"


def leads(cells: dict[str, float], key: str) -> bool:
    """Does Meet's timeline positively support `key` on a tag whose overlaps are `cells`? `key` was
    lit over it for at least `MIN_SECONDS` and `EXCLUSION_RATIO` times as long as anybody else (the
    note taker included). The check a name inferred through a chain must pass
    (`app/skribby.py::_earlier_rules`): a lean the other way, a tie or no overlap fails it."""
    mine = cells.get(key, 0.0)
    if mine < MIN_SECONDS:
        return False
    rest = max((seconds for other, seconds in cells.items() if other != key), default=0.0)
    return mine >= EXCLUSION_RATIO * rest
