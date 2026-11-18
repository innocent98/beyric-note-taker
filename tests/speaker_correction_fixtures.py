"""Synthetic Skribby meetings for the line-level speaker correction tests.

Each fixture is built from NUMBERS taken from `scripts/speaker_reattribution_report.py` runs
(2026-11-18) and laid out as a timeline of invented segments with invented names and placeholder
words. Nothing here is, or is copied from, a real meeting.

* Meeting 40 (three speakers, every tag named): 170 segments agree, 32 would move, 19 are
  ambiguous, 181 have no Meet signal (402 in all). All of that is exactly what the report printed.
* Meeting 39 (six of nine tags unnamed): 128 segments would move. The report's other counts are
  not in the repository, so the rest of this fixture is invented (labelled as such below).
* Meeting 38 (clean, four speakers): 2 segments would move; the rest is invented.

The builder places every segment on its own stretch of time, `GAP` seconds apart (wider than the
clock check's 30 s search, so no shift of Meet's clock can ever fit better), and lights Meet's
speaking indicator exactly as the case says: the whole segment for a clear one, two people half
each for an ambiguous one, nobody for "no signal".
"""

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime

BOT = "Beyric Note Taker (recording)"
START = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
GAP = 40.0

ADA, BO, CY, DI = (f"{n} Example" for n in ("Ada", "Bo", "Cy", "Di"))


@dataclass
class Case:
    items: list[dict]  # Skribby's transcript items (the raw shape `skribby._segments` reads)
    events: dict  # the kept speaker events
    expect: dict[str, int]  # outcome -> segments, as BUILT (not as any rule computed them)
    moved_to: dict[int, str] = field(default_factory=dict)  # item position -> who it should go to

    @property
    def body(self) -> dict:
        return {"transcript": self.items}

    def evidence_items(self) -> list[dict]:
        """The items as the evidence keeps them (`skribby._evidence`)."""
        return [
            {
                "speaker": i["speaker"],
                "speaker_name": i["speaker_name"],
                "potential_speaker_names": [n["name"] for n in i["potential_speaker_names"]],
                "start": i["start"],
                "end": i["end"],
                "text": i["transcript"],
            }
            for i in self.items
        ]


def stamp(seconds: float) -> int:
    return int((START.timestamp() + seconds) * 1000)


def build(
    tags: dict[str, str | None],
    plan: dict[str, int],
    *,
    seed: int,
    people: list[str] | None = None,
) -> Case:
    """`tags` maps each diarization tag to the person its segments are named after (None: a tag
    Skribby never named and listed no candidate for). `plan` is the number of segments of each
    outcome: `agrees` (only on a named tag), `move`, `ambiguous`, `no_signal`. The order is a fixed
    shuffle (`seed`)."""
    people = people or sorted({p for p in tags.values() if p})
    named = [t for t, p in tags.items() if p]
    kinds = [kind for kind, count in plan.items() for _ in range(count)]
    random.Random(seed).shuffle(kinds)
    cursor = {"tag": 0, "other": 0, "named": 0}
    lit: dict[str, list[tuple[float, float]]] = {p: [] for p in people}
    items, moved_to, at, word = [], {}, 0.0, 0
    for index, kind in enumerate(kinds):
        if kind == "agrees":
            tag = named[cursor["named"] % len(named)]
            cursor["named"] += 1
        else:
            tag = list(tags)[cursor["tag"] % len(tags)]
            cursor["tag"] += 1
        own = tags[tag]
        length = 3.0 + index % 4
        others = [p for p in people if p != own]
        if kind == "agrees":
            lit[own].append((at, at + length))
        elif kind == "move":
            who = (
                others[cursor["other"] % len(others)]
                if own
                else people[cursor["other"] % len(people)]
            )
            cursor["other"] += 1
            lit[who].append((at, at + length))
            moved_to[index] = who
        elif kind == "ambiguous":
            first = people[cursor["other"] % len(people)]
            second = people[(cursor["other"] + 1) % len(people)]
            cursor["other"] += 1
            lit[first].append((at, at + length / 2))
            lit[second].append((at + length / 2, at + length))
        elif kind != "no_signal":
            raise ValueError(kind)
        count = 5 + index % 3
        items.append(
            {
                "speaker": tag,
                "speaker_name": own,
                "potential_speaker_names": [],
                "start": at,
                "end": at + length,
                # placeholder words, all different (a transcript of one repeated word is a loop)
                "transcript": " ".join(f"w{word + n}" for n in range(count)),
            }
        )
        word += count
        at += length + GAP
    participants = []
    for person, spans in lit.items():
        events = []
        for a, b in spans:
            events += [
                {"type": "started-speaking", "timestamp": stamp(a)},
                {"type": "stopped-speaking", "timestamp": stamp(b)},
            ]
        participants.append({"name": person, "events": events})
    participants.append({"name": BOT, "events": []})
    record = {"participants": participants, "recording_started_at": START.isoformat()}
    return Case(items, record, dict(plan), moved_to)


def meeting_40() -> Case:
    """Three speakers, every tag named; 170 / 32 / 19 / 181 segments by outcome."""
    return build(
        {"0": ADA, "1": BO, "2": CY},
        {"agrees": 170, "move": 32, "ambiguous": 19, "no_signal": 181},
        seed=40,
    )


def meeting_39() -> Case:
    """Six tags, three of them never named (`Unknown speaker`). 128 moves are the report's; the
    other counts and the split of tags are INVENTED to give the moves somewhere to come from. The
    unnamed tags get their moves from all three people evenly, so Meet's timeline cannot name them
    (no clear winner) and the line correction is the only thing that credits them."""
    return build(
        {"0": ADA, "1": BO, "2": CY, "3": None, "4": None, "5": None},
        {"agrees": 220, "move": 128, "ambiguous": 12, "no_signal": 60},
        seed=39,
    )


def meeting_38() -> Case:
    """The clean control: four speakers, every tag named; 2 moves (the report's), rest INVENTED."""
    return build(
        {"0": ADA, "1": BO, "2": CY, "3": DI},
        {"agrees": 300, "move": 2, "ambiguous": 5, "no_signal": 40},
        seed=38,
    )


def old_segments(body: dict, own_names: tuple[str, ...], by_elimination, events, by_timeline):
    """What `skribby._segments` returned BEFORE the line correction existed: a transcript item's
    own `speaker_name`, else its tag's derived name. A copy of the old code, kept here so "off
    equals today's output" is checked against something that cannot change with the new code."""
    from app import skribby

    value = body.get("transcript")
    if value is None:
        return []
    by_tag = skribby._tag_names(value, own_names, by_elimination, events, by_timeline)
    segments = []
    for item in value:
        text = item.get("transcript")
        if isinstance(text, str) and text.strip():
            name = skribby._clean_name(item.get("speaker_name"))
            if not name:
                tag = skribby._tag(item.get("speaker"))
                name = by_tag.get(tag, "") if tag is not None else ""
            segments.append({"speaker": name, "text": text})
    return segments
