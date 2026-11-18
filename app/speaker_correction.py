"""Line-level speaker correction: a Skribby segment follows Meet's own "who is speaking" timeline
when exactly one person was lit over it and that person is not the one the diarization tag says.

Why: naming a tag cannot fix a tag that holds other people's voices (meeting 40: 6% of the seconds
sat under the wrong person, all of it the host's speech tagged to two other people; meeting 39: 62%;
meeting 38, clean: 0.2%). Design, numbers and the people who judged the moved lines:
docs/superpowers/specs/2026-11-18-line-level-speaker-correction-design.md, SOP
docs/sop/2026-11-19-line-level-speaker-correction.md.

The rule (Adebayo, 2026-11-19: "exactly one person lit, nobody else", not a share of the line,
because a segment contains pauses and Meet's light covers only part of a long one). A segment moves
to person P when ALL hold:

* Meet's events exist, are readable, are placed on the transcript's clock and `clock_check` finds
  no shift of Meet's clock that makes the segments agree clearly better with the people they are
  named for (a hard gate: a shifted clock moves nothing, and the best shift is recorded);
* P was lit for at least `MIN_SECONDS` over the segment and no runner-up reached `AMBIGUOUS_RATIO`
  of P's seconds (a runner-up under `MIN_SECONDS` never counts);
* P is not the person the segment already carries (every word of one name is among the other's,
  never a part of a word: "Ada" is "Ada Obi", "Ann" is not "Joanne");
* P is a person: the note taker takes part in the ranking (lit beside a person it makes the
  segment ambiguous; alone on top the segment stays) but is never a destination; a blank or
  "Unknown speaker" participant is skipped;
* a name already in the meeting is P (`resolve_label`: exactly one known name, never a part of a
  word and never one of two); a line is not moved to a name nobody in the meeting is called by;
* the segment has a tag and usable times.

Otherwise it stays as it is: two or more lit (ambiguous), nobody lit (no signal), the note taker
on top (bot), no name for P (unmatched), not timed.

`MIN_SECONDS` and `AMBIGUOUS_RATIO` are the defaults of `scripts/speaker_reattribution_report.py`,
which imports them from here, so the measurement and the production rule cannot drift apart. The
report, the live path (`app/skribby.py::_segments`) and the rebuild script all read the same
`assess`.

Pure functions: no database, no settings, no logging. Nothing here holds a word anybody said; the
only names it handles are the labels it hands back.
"""

import bisect
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app import meet_timeline, skribby

MIN_SECONDS = 1.0  # fewest seconds a person must be lit over a segment to count at all
AMBIGUOUS_RATIO = 0.25  # a runner-up with this share of the top's seconds makes it ambiguous
UNKNOWN = "Unknown speaker"  # the label of a line credited to no one (`app/notes.py`)
# The clock gate: the best shift of Meet's clock must not raise the agreement of the segments with
# the people they are named for by more than this factor. 10% is what the naming rule's own check
# uses; this one measures who-agrees-with-whom, so it sees a steady 1 to 3 s offset too (see
# `clock_check`).
CLOCK_GAIN = meet_timeline.CLOCK_GAIN

# Why nothing was moved in a meeting at all (`Correction.refused`).
NO_EVENTS = "no_events"  # nothing kept (Teams, Zoom, a failed fetch): an exact no-op
UNREADABLE = "unreadable"  # kept, but not in a shape `meet_timeline` reads
CLOCK_UNPLACED = "clock_unplaced"  # Unix times with no `recording` time to place them
NO_TIMES = "no_times"  # nobody to compare, or no timed segment
CLOCK_SHIFTED = "clock_shifted"  # a shift of Meet's clock would fit clearly better


def key_and_shown(name: str) -> tuple[str, str]:
    """A participant's name -> (comparison key, shown name): the grouping the naming rule uses
    (`app/skribby.py::_name_by_timeline`), so a participant who left and rejoined is one person."""
    shown = skribby._clean_name(name)
    return (skribby._same(shown) if shown else ""), shown


def name_words(name: str) -> frozenset[str]:
    """The words of a name, case-, accent- and punctuation-insensitive: "Zoë Obi-Ade" is
    {zoe, obi, ade}. Whole words are all names are ever compared by."""
    plain = unicodedata.normalize("NFD", skribby._same(skribby._clean_name(name)))
    plain = "".join(ch for ch in plain if unicodedata.category(ch) != "Mn")
    return frozenset(re.findall(r"[^\W_]+", plain))


def same_person(a: str, b: str) -> bool:
    """One name's words are all among the other's ("Ada" and "Ada Obi", "Obi Ada"), never a part of
    a word: "Ann" is not "Joanne", "Tomi" is not "Tomiwa", "Ade" is not "Adebayo". Two names that
    share one word each and differ in another ("Ada Obi", "Ada Eze") are two people."""
    wa, wb = name_words(a), name_words(b)
    return bool(wa and wb) and (wa <= wb or wb <= wa)


def known_names(tags: dict, items: list[dict]) -> list[str]:
    """Every name already in use in the meeting: the tags' and the segments' own."""
    names = [info["name"] for info in tags.values() if info.get("name")]
    return names + [n for item in items if (n := skribby._clean_name(item.get("speaker_name")))]


def resolve_label(person: str, known: list[str]) -> str | None:
    """The label a line moved to `person` takes: the spelling a tag or segment already uses for
    exactly that name, else the one known name that is the same person (so the line joins that
    person's turns), else None. None when no known name is that person or when several are:
    a line is never moved to a name nobody in the meeting is called by, because that can be a
    respelling of the person it is already under, nor to one of two equally good names."""
    words = name_words(person)
    exact = [name for name in known if name_words(name) == words]
    if exact:
        return exact[0]
    near = sorted({name for name in known if same_person(name, person)})
    return near[0] if len(near) == 1 else None


Index = tuple[list[tuple[float, float]], list[float]]  # merged intervals and their end times


def _index(intervals: list[tuple[float, float]]) -> Index:
    return intervals, [i[1] for i in intervals]


def _cover(index: Index, start: float, end: float) -> float:
    """Seconds of [start, end] covered by an indexed list of merged intervals
    (`meet_timeline.overlap`, with the end times worked out once instead of at every call)."""
    intervals, ends = index
    total = 0.0
    for i in range(bisect.bisect_right(ends, start), len(intervals)):
        a, b = intervals[i]
        if a >= end:
            break
        total += max(0.0, min(b, end) - max(a, start))
    return total


def _classify(
    indexed: dict[str, Index],
    start: float,
    end: float,
    min_seconds: float,
    ambiguous_ratio: float,
    bot: frozenset[str] | set[str],
) -> tuple[str, str | None, float]:
    ranked = sorted(
        ((_cover(v, start, end), n) for n, v in indexed.items()),
        key=lambda pair: (-pair[0], pair[1]),
    )
    if not ranked or ranked[0][0] < min_seconds:
        return "no_signal", None, 0.0
    top, person = ranked[0]
    second = ranked[1][0] if len(ranked) > 1 else 0.0
    if second >= min_seconds and second >= ambiguous_ratio * top:
        return "ambiguous", person, top
    if person in bot:
        return "bot", person, top
    return "clear", person, top


def classify(
    spans: dict[str, list[tuple[float, float]]],
    start: float,
    end: float,
    min_seconds: float = MIN_SECONDS,
    ambiguous_ratio: float = AMBIGUOUS_RATIO,
    bot: frozenset[str] | set[str] = frozenset(),
) -> tuple[str, str | None, float]:
    """`(outcome, person, seconds)` for one segment: `outcome` is `clear`, `ambiguous`, `bot` or
    `no_signal`; `person` and `seconds` are Meet's top participant and the seconds they were lit
    over [start, end] (person is None for `no_signal`). `bot` names the participants that are the
    note taker: they take part in the ranking (a note taker lit beside a person is two lit, so
    ambiguous) but are never a destination (`bot` when the note taker alone was on top)."""
    indexed = {n: _index(v) for n, v in spans.items()}
    return _classify(indexed, start, end, min_seconds, ambiguous_ratio, bot)


@dataclass
class Placed:
    """Meet's speaking intervals on the transcript's clock. `spans` holds every participant that
    is a person or the note taker (a blank name or "Unknown speaker" is left out), `bot` the shown
    names of those that are the note taker. It is None and `refused` says why when they cannot be
    used; `how` says how the clock was placed."""

    spans: dict[str, list[tuple[float, float]]] | None = None
    refused: str | None = None
    how: str = ""
    shift: float = 0.0
    bot: frozenset[str] = frozenset()

    @property
    def people(self) -> dict[str, list[tuple[float, float]]]:
        return {n: v for n, v in (self.spans or {}).items() if n not in self.bot}


def place(record, own_names: tuple[str, ...] = (), offset: float = 0.0) -> Placed:
    """The participants' speaking intervals of `record` (the kept speaker events) on the
    transcript's clock, shifted by `offset` seconds (the report's knob; production passes none)."""
    if not record:
        return Placed(refused=NO_EVENTS)
    spans = meet_timeline.speaking_intervals(record, key_and_shown)
    if not spans:
        return Placed(refused=UNREADABLE)
    zero, how = meet_timeline.anchor(record, spans)
    if zero is None:
        return Placed(refused=CLOCK_UNPLACED, how=how)
    bot_keys = {skribby._same(n) for n in map(skribby._clean_name, own_names) if n}
    shift = zero - offset
    placed = {
        n: meet_timeline.merge([(a - shift, b - shift) for a, b in v])
        for n, v in spans.items()
        if skribby._same(n) != skribby._same(UNKNOWN)
    }
    bot = frozenset(n for n in placed if skribby._same(n) in bot_keys)
    return Placed(placed, how=how, shift=shift, bot=bot)


@dataclass
class ClockFit:
    """How well Meet's clock fits the transcript's. `best` is the shift (seconds, Meet's times
    moved later when positive) with the most agreement, `here` the agreement now and `there` at
    `best`, in seconds; `basis` says what was compared."""

    best: float = 0.0
    here: float = 0.0
    there: float = 0.0
    basis: str = "named"

    @property
    def fine(self) -> bool:
        return not (self.best != 0 and self.there > self.here * CLOCK_GAIN)

    @property
    def gain(self) -> float:
        """How much more the segments agree at `best` than now, as a share; 9.99 when they do not
        agree at all now and do at `best`."""
        if self.here > 0:
            return min(self.there / self.here - 1, 9.99)
        return 9.99 if self.there > 0 else 0.0


# Whole seconds up to 30 either way (as the naming rule's check), and half seconds up to 5: the
# offsets that matter most for a 1 s rule are small ones.
SHIFTS = sorted({float(s) for s in range(-30, 31)} | {s / 2 for s in range(-10, 11)})


def clock_check(
    items: list[dict], tags: dict, people: dict[str, list[tuple[float, float]]]
) -> ClockFit:
    """Does Meet's clock fit the segments' clock? Each timed segment that is named is compared with
    the speaking time of the person it is named for: the seconds that person was lit over it, summed
    over the meeting, for each shift of Meet's clock (`SHIFTS`). A steady offset of a second or two
    moves a lot of that agreement (turns are a few seconds long), where the union of everyone's
    light, which the naming rule compares, barely changes in a conversation without gaps. With no
    named segment it falls back to the union against all segments. A steady lag of the indicator
    itself looks like an offset, so a lag of a second or more stops the correction for the meeting:
    the safe side. Ties go to no shift."""
    union = _index(meet_timeline.merge([span for v in people.values() for span in v]))
    own_cache: dict[str, Index | None] = {}
    named: list[tuple[float, float, Index]] = []
    everything: list[tuple[float, float]] = []
    for item in items:
        tag = tag_of(item)
        a, b = meet_timeline.seconds(item.get("start")), meet_timeline.seconds(item.get("end"))
        if tag is None or a is None or b is None or b <= a:
            continue
        everything.append((a, b))
        current = current_name(item, tags, tag)
        if not current:
            continue
        if current not in own_cache:
            matching = [v for n, v in people.items() if same_person(current, n)]
            own_cache[current] = _index(meet_timeline.merge([i for v in matching for i in v]))
        if own_cache[current][0]:
            named.append((a, b, own_cache[current]))
    if named:
        pairs, basis = named, "named"
    else:
        pairs, basis = [(a, b, union) for a, b in everything], "all"

    def score(shift: float) -> float:
        return sum(_cover(index, a - shift, b - shift) for a, b, index in pairs)

    scores = {shift: score(shift) for shift in SHIFTS}
    best = max(SHIFTS, key=lambda shift: (scores[shift], -abs(shift)))
    return ClockFit(best, scores[0.0], scores[best], basis)


def skribby_body(items: list) -> dict:
    """The evidence items in the shape of Skribby's own answer, which is what `skribby._segments`
    and `skribby._resolve_tags` read (the evidence keeps the candidate names as plain strings and
    the words under `text`)."""
    return {
        "transcript": [
            {
                "speaker": i.get("speaker"),
                "speaker_name": i.get("speaker_name"),
                "potential_speaker_names": [
                    {"name": name} for name in i.get("potential_speaker_names") or []
                ],
                "start": i.get("start"),
                "end": i.get("end"),
                "transcript": i.get("text"),
            }
            for i in items
        ]
    }


def tag_of(item: dict) -> str | None:
    return skribby._tag(item.get("speaker"))


@dataclass
class Assessment:
    """What the timeline says about one segment (`index` is its position in the list given)."""

    index: int
    tag: str
    start: float
    end: float
    current: str  # the person the segment carries now: its own name, else its tag's, else ""
    outcome: str  # agrees | move | ambiguous | no_signal | bot | unmatched
    person: str | None
    lit: float
    label: str | None = None  # the label a `move` takes

    @property
    def seconds(self) -> float:
        return self.end - self.start


def current_name(item: dict, tags: dict, tag: str) -> str:
    """The name a segment is shown under today (`app/skribby.py::_segments`): its own
    `speaker_name` wins, else its tag's name, else nobody."""
    own = skribby._clean_name(item.get("speaker_name"))
    return own or (tags.get(tag) or {}).get("name") or ""


def assess(
    items: list[dict],
    tags: dict,
    spans: dict[str, list[tuple[float, float]]],
    min_seconds: float = MIN_SECONDS,
    ambiguous_ratio: float = AMBIGUOUS_RATIO,
    bot: frozenset[str] | set[str] = frozenset(),
) -> tuple[list[Assessment], dict[str, float]]:
    """Every segment against the timeline: `(assessments, not_timed)`, `not_timed` holding the
    `count` of segments with no tag or no usable times and the `seconds` of those that have times.
    `items` are Skribby's transcript items or the evidence's (`speaker`, `speaker_name`, `start`,
    `end`); `tags` is `_resolve_tags`' answer; `spans` is `place(...).spans` (the note taker's
    spans included) and `bot` the note taker's names there. A clear winner who is not the person
    the segment carries is a `move` only when a name already in the meeting is that person
    (`resolve_label`); else it is `unmatched` and stays."""
    found: list[Assessment] = []
    not_timed = {"count": 0, "seconds": 0.0}
    indexed = {n: _index(v) for n, v in spans.items()}
    known = known_names(tags, items)
    for index, item in enumerate(items):
        tag = tag_of(item)
        a, b = meet_timeline.seconds(item.get("start")), meet_timeline.seconds(item.get("end"))
        if tag is None or a is None or b is None or b <= a:
            not_timed["count"] += 1
            if a is not None and b is not None and b > a:
                not_timed["seconds"] += b - a
            continue
        current = current_name(item, tags, tag)
        outcome, person, lit = _classify(indexed, a, b, min_seconds, ambiguous_ratio, bot)
        label = None
        if outcome == "clear":
            if current and same_person(current, person):
                outcome = "agrees"
            else:
                label = resolve_label(person, known)
                outcome = "move" if label else "unmatched"
        found.append(Assessment(index, tag, a, b, current, outcome, person, lit, label))
    return found, not_timed


@dataclass
class Correction:
    """What the rule did to one meeting. `moves` is `{item position: the label to show}`; empty
    when `refused` says why nothing could be read (an exact no-op), or when nothing moves."""

    moves: dict[int, str] = field(default_factory=dict)
    refused: str | None = None
    assessments: list[Assessment] = field(default_factory=list)
    not_timed: dict[str, float] = field(default_factory=lambda: {"count": 0, "seconds": 0.0})
    how: str = ""
    clock: ClockFit | None = None

    @property
    def ran(self) -> bool:
        """True when events existed, so there is something to say about the meeting (a refusal
        for a shifted clock included). False for a meeting with no events: nothing is recorded."""
        return self.refused != NO_EVENTS

    def outcome(self, name: str) -> tuple[int, float]:
        rows = [a for a in self.assessments if a.outcome == name]
        return len(rows), sum(a.seconds for a in rows)

    @property
    def moved_segments(self) -> int:
        return len(self.moves)

    @property
    def moved_seconds(self) -> float:
        return sum(a.seconds for a in self.assessments if a.index in self.moves)

    def summary(self) -> dict[str, Any]:
        """Numbers only, for the evidence and the log line: nothing a person said, no name.
        `clock_shift` is the best shift of Meet's clock found (seconds; 0 is the clock as
        placed) and `clock_gain` how much more the segments agreed at that shift (a share), so
        a steady offset shows after the first live meeting."""
        row = {
            "moved_segments": self.moved_segments,
            "moved_seconds": round(self.moved_seconds, 1),
            "segments": len(self.assessments) + int(self.not_timed["count"]),
            "refused": self.refused,
        }
        if self.clock is not None:
            row["clock_shift"] = self.clock.best
            row["clock_gain"] = round(self.clock.gain, 3)
        return row


def correct(
    items: list[dict],
    tags: dict,
    events,
    own_names: tuple[str, ...] = (),
    min_seconds: float = MIN_SECONDS,
    ambiguous_ratio: float = AMBIGUOUS_RATIO,
) -> Correction:
    """Apply the rule to a meeting: `items` are Skribby's transcript items (or the evidence's),
    `tags` what `_resolve_tags` made of them, `events` the kept speaker events. Never raises on
    what the data holds; the caller keeps the uncorrected transcript if it does."""
    placed = place(events, own_names)
    if placed.spans is None:
        return Correction(refused=placed.refused, how=placed.how)
    people = placed.people
    if not people or not meet_timeline.heard_by_tag(items, tag_of):
        return Correction(refused=NO_TIMES, how=placed.how)
    fit = clock_check(items, tags, people)
    if not fit.fine:
        return Correction(refused=CLOCK_SHIFTED, how=placed.how, clock=fit)
    assessments, not_timed = assess(
        items, tags, placed.spans, min_seconds, ambiguous_ratio, placed.bot
    )
    moves = {a.index: a.label for a in assessments if a.outcome == "move" and a.label}
    return Correction(moves, None, assessments, not_timed, placed.how, fit)
