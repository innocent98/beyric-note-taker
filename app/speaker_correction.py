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

* Meet's events exist, are readable, are placed on the transcript's clock and no shift of up to
  30 s fits the segments clearly better (the naming rule's own clock check, a hard gate here);
* P was lit for at least `MIN_SECONDS` over the segment and no runner-up reached `AMBIGUOUS_RATIO`
  of P's seconds (a runner-up under `MIN_SECONDS` never counts);
* P is not the person the segment already carries (equal after cleaning, or one name inside the
  other, so "Ada" is not moved to "Ada Obi");
* P is a person: not the note taker (its names are left out of the timeline before anything is
  ranked), not a blank or "Unknown speaker";
* the segment has a tag and usable times.

Otherwise it stays as it is: two or more lit (ambiguous), nobody lit (no signal), not timed.

`MIN_SECONDS` and `AMBIGUOUS_RATIO` are the defaults of `scripts/speaker_reattribution_report.py`,
which imports them from here, so the measurement and the production rule cannot drift apart. The
report, the live path (`app/skribby.py::_segments`) and the rebuild script all read the same
`assess`.

Pure functions: no database, no settings, no logging. Nothing here holds a word anybody said; the
only names it handles are the labels it hands back.
"""

from dataclasses import dataclass, field
from typing import Any

from app import meet_timeline, skribby

MIN_SECONDS = 1.0  # fewest seconds a person must be lit over a segment to count at all
AMBIGUOUS_RATIO = 0.25  # a runner-up with this share of the top's seconds makes it ambiguous
UNKNOWN = "Unknown speaker"  # the label of a line credited to no one (`app/notes.py`)

# Why nothing was moved in a meeting at all (`Correction.refused`).
NO_EVENTS = "no_events"  # nothing kept (Teams, Zoom, a failed fetch): an exact no-op
UNREADABLE = "unreadable"  # kept, but not in a shape `meet_timeline` reads
CLOCK_UNPLACED = "clock_unplaced"  # Unix times with no `recording` time to place them
NO_TIMES = "no_times"  # nobody to compare, or no timed segment
CLOCK_SHIFTED = "clock_shifted"  # a shift of up to 30 s would fit clearly better


def key_and_shown(name: str) -> tuple[str, str]:
    """A participant's name -> (comparison key, shown name): the grouping the naming rule uses
    (`app/skribby.py::_name_by_timeline`), so a participant who left and rejoined is one person."""
    shown = skribby._clean_name(name)
    return (skribby._same(shown) if shown else ""), shown


def same_person(a: str, b: str) -> bool:
    """Equal after cleaning, or one inside the other (the segments report's "ok" test)."""
    ka, kb = skribby._same(a), skribby._same(b)
    return bool(ka and kb) and (ka == kb or ka in kb or kb in ka)


def classify(
    spans: dict[str, list[tuple[float, float]]],
    start: float,
    end: float,
    min_seconds: float = MIN_SECONDS,
    ambiguous_ratio: float = AMBIGUOUS_RATIO,
) -> tuple[str, str | None, float]:
    """`(outcome, person, seconds)` for one segment: `outcome` is `clear`, `ambiguous` or
    `no_signal`; `person` and `seconds` are Meet's top participant and the seconds they were lit
    over [start, end] (person is None for `no_signal`)."""
    ranked = sorted(
        ((meet_timeline.overlap(v, start, end), n) for n, v in spans.items()),
        key=lambda pair: (-pair[0], pair[1]),
    )
    if not ranked or ranked[0][0] < min_seconds:
        return "no_signal", None, 0.0
    top, person = ranked[0]
    second = ranked[1][0] if len(ranked) > 1 else 0.0
    if second >= min_seconds and second >= ambiguous_ratio * top:
        return "ambiguous", person, top
    return "clear", person, top


@dataclass
class Placed:
    """Meet's speaking intervals on the transcript's clock, the bot left out. `spans` is None and
    `refused` says why when they cannot be used; `how` says how the clock was placed."""

    spans: dict[str, list[tuple[float, float]]] | None = None
    refused: str | None = None
    how: str = ""
    shift: float = 0.0


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
    bot = {skribby._same(n) for n in map(skribby._clean_name, own_names) if n}
    shift = zero - offset
    placed = {
        n: meet_timeline.merge([(a - shift, b - shift) for a, b in v])
        for n, v in spans.items()
        if skribby._same(n) not in bot and skribby._same(n) != skribby._same(UNKNOWN)
    }
    return Placed(placed, how=how, shift=shift)


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
    """What the timeline says about one segment (`item` is its position in the list given)."""

    index: int
    tag: str
    start: float
    end: float
    current: str  # the person the segment carries now: its own name, else its tag's, else ""
    outcome: str  # agrees | move | ambiguous | no_signal
    person: str | None
    lit: float

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
) -> tuple[list[Assessment], dict[str, float]]:
    """Every segment against the timeline: `(assessments, not_timed)`, `not_timed` holding the
    `count` of segments with no tag or no usable times and the `seconds` of those that have times.
    `items` are Skribby's transcript items or the evidence's (`speaker`, `speaker_name`, `start`,
    `end`); `tags` is `_resolve_tags`' answer; `spans` is `place(...).spans`."""
    found: list[Assessment] = []
    not_timed = {"count": 0, "seconds": 0.0}
    for index, item in enumerate(items):
        tag = tag_of(item)
        a, b = meet_timeline.seconds(item.get("start")), meet_timeline.seconds(item.get("end"))
        if tag is None or a is None or b is None or b <= a:
            not_timed["count"] += 1
            if a is not None and b is not None and b > a:
                not_timed["seconds"] += b - a
            continue
        current = current_name(item, tags, tag)
        outcome, person, lit = classify(spans, a, b, min_seconds, ambiguous_ratio)
        if outcome == "clear":
            outcome = "agrees" if current and same_person(current, person) else "move"
        found.append(Assessment(index, tag, a, b, current, outcome, person, lit))
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
        """Numbers only, for the evidence and the log line: nothing a person said, no name."""
        return {
            "moved_segments": self.moved_segments,
            "moved_seconds": round(self.moved_seconds, 1),
            "segments": len(self.assessments) + int(self.not_timed["count"]),
            "refused": self.refused,
        }


def _label_for(person: str, known: list[str]) -> str:
    """The label to show for `person`: the name a tag or a segment already carries for the same
    person (so the moved line joins that person's turns instead of making a second spelling), else
    the name Meet shows."""
    key = skribby._same(person)
    exact = [name for name in known if skribby._same(name) == key]
    if exact:
        return exact[0]
    near = sorted({name for name in known if same_person(name, person)})
    return near[0] if len(near) == 1 else person


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
    heard = meet_timeline.heard_by_tag(items, tag_of)
    if not placed.spans or not heard:
        return Correction(refused=NO_TIMES, how=placed.how)
    if not meet_timeline.clock_is_fine(*meet_timeline.clock_fit(placed.spans, heard)):
        return Correction(refused=CLOCK_SHIFTED, how=placed.how)
    assessments, not_timed = assess(items, tags, placed.spans, min_seconds, ambiguous_ratio)
    known = [info["name"] for info in tags.values() if info.get("name")]
    known += [name for item in items if (name := skribby._clean_name(item.get("speaker_name")))]
    moves = {
        a.index: _label_for(a.person, known)
        for a in assessments
        if a.outcome == "move" and a.person
    }
    return Correction(moves, None, assessments, not_timed, placed.how)
