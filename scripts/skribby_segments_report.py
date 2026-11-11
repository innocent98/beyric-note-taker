"""Print what Skribby sent for one meeting, grouped by speaker tag, with the name the app derived.

Run it where the app's `.env` is, with the meeting's id, and paste the output back:

    docker compose exec -T web python scripts/skribby_segments_report.py 29

The evidence is written just before Skribby's copy is deleted and kept 7 days
(`skribby_segment_evidence`). This reads it and changes nothing. It prints what people said, so
treat the output like the transcript: do not paste it anywhere public.

How to read it: each block is one diarization tag ("speaker" in Skribby's answer). The header says
the name the app gave the tag and why: `given` (Skribby named it on its own segments),
`elimination` (taken from `potential_speaker_names` by ruling out the others, only while
SKRIBBY_NAME_BY_ELIMINATION is on) or `unknown` (with the reason). Under it, every segment of that
tag: time, the `speaker_name` Skribby put on that segment, the names it listed as possible, and
the text. If the names Skribby gave are wrong, the fault is Skribby's; if they are right and the
notes still credit the wrong person, it is our alignment.

The last section, "Meet's own speaker timeline", compares each tag's name with the meeting
platform's own "who is speaking" signal that Skribby records (`participants[].events`, kept next
to the transcript; https://skribby.io/docs/guides/speaker-timelines). Its times are read as
absolute (Unix milliseconds, as the docs' example suggests, not proven live) and placed on the
transcript's clock from the moment the bot's status became `recording`, which is only close to the
recording's own start. If the two clocks look out of step the section says by how much, and
`--offset SECONDS` re-runs it with the Meet times shifted. Without events, or with events in a
shape it cannot read, it says so in one line.
"""

import argparse
import bisect
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import SkribbySegmentEvidence  # noqa: E402

NO_TAG = "(no tag)"


def _clock(seconds) -> str:
    if not isinstance(seconds, int | float):
        return "  ?:??"
    total = int(seconds)
    return f"{total // 60:3d}:{total % 60:02d}"


# ---- Meet's own speaker timeline ----

SEARCH_SECONDS = 30  # how far either way the clock fit looks
NAME_WIDTH = 12


def _seconds(value) -> float | None:
    """A time as seconds: Skribby's example shows 13-digit integers (Unix milliseconds); anything
    that large is divided by 1000. Not a number: None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value / 1000 if value >= 1e11 else float(value)


def _same(name: str) -> str:
    plain = unicodedata.normalize("NFKD", name)
    return " ".join("".join(c for c in plain if not unicodedata.combining(c)).casefold().split())


def _merge(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[list[float]] = []
    for start, end in sorted(i for i in intervals if i[1] > i[0]):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def _overlap(intervals: list[tuple[float, float]], start: float, end: float) -> float:
    """Seconds of [start, end] covered by `intervals` (merged, sorted)."""
    ends = [i[1] for i in intervals]
    total = 0.0
    for index in range(bisect.bisect_right(ends, start), len(intervals)):
        a, b = intervals[index]
        if a >= end:
            break
        total += max(0.0, min(b, end) - max(a, start))
    return total


def speaking_intervals(record) -> dict[str, list[tuple[float, float]]] | None:
    """`{participant name: speaking intervals, seconds}` from the saved events, or None when the
    record is not in the shape this reads. A `started-speaking` opens an interval and
    `stopped-speaking` or `left` closes it; one never closed ends at the last time seen anywhere in
    the record."""
    people = record.get("participants") if isinstance(record, dict) else None
    if not isinstance(people, list):
        return None
    seen, named = [], {}
    for person in people:
        if not isinstance(person, dict) or not isinstance(person.get("name"), str):
            continue
        events = [e for e in person.get("events") or [] if isinstance(e, dict)]
        stamped = sorted(
            (t, e.get("type")) for e in events if (t := _seconds(e.get("timestamp"))) is not None
        )
        seen += [t for t, _ in stamped]
        named.setdefault(person["name"], []).extend(stamped)
    if not named:
        return None
    last = max(seen) if seen else 0.0
    result: dict[str, list[tuple[float, float]]] = {}
    for name, stamped in named.items():
        opened, spans = None, []
        for t, kind in sorted(stamped):
            if kind == "started-speaking" and opened is None:
                opened = t
            elif kind in ("stopped-speaking", "left") and opened is not None:
                spans.append((opened, t))
                opened = None
        if opened is not None:
            spans.append((opened, last))
        result[name] = _merge(spans)
    return result


def _anchor(record, spans) -> tuple[float | None, str]:
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


def timeline_section(items: list[dict], tags: dict, record, offset: float = 0.0) -> list[str]:
    """The lines of the "Meet's own speaker timeline" section."""
    head = ["", "== Meet's own speaker timeline (Skribby participant events; not proven live) =="]
    if not record:
        return [*head, "  No speaker events were kept for this meeting."]
    spans = speaking_intervals(record)
    if spans is None:
        return [*head, "  Speaker events were kept but are not in a shape this report can read."]
    if record.get("truncated"):
        head.append(f"  (events were cut to fit the size cap: {record['truncated']})")
    zero, how = _anchor(record, spans)
    if zero is None:
        return [*head, f"  {how}, so they cannot be placed on the transcript."]
    shift = zero - offset
    spans = {n: _merge([(a - shift, b - shift) for a, b in v]) for n, v in spans.items()}
    head.append(f"  {how}; Meet times shifted by {offset:+.0f} s.")
    names = list(spans)
    out = [*head, "", "  Seconds speaking according to Meet:"]
    out += [f"    {n}: {sum(b - a for a, b in spans[n]):.1f} s" for n in names]
    heard: dict[str, list[tuple[float, float]]] = {}
    for item in items:
        a, b = _seconds(item.get("start")), _seconds(item.get("end"))
        if a is not None and b is not None and b > a and item.get("speaker") is not None:
            heard.setdefault(item["speaker"], []).append((a, b))
    if not heard:
        return [*out, "", "  No Skribby segment has both a tag and times, so nothing to compare."]
    union = _merge([span for v in spans.values() for span in v])

    def total_overlap(shift_meet: int) -> float:
        """Overlap if Meet's times were moved `shift_meet` seconds later."""
        return sum(
            _overlap(union, a - shift_meet, b - shift_meet) for v in heard.values() for a, b in v
        )

    best = max(range(-SEARCH_SECONDS, SEARCH_SECONDS + 1), key=total_overlap)
    cols = "".join(f"{n[:NAME_WIDTH]:>{NAME_WIDTH + 2}}" for n in names)
    out += ["", "  Overlap in seconds between each tag's segments and each participant speaking:"]
    out.append(f"    {'tag':<6}{cols}{'tag total':>11}  derived name -> verdict")
    for tag in sorted(heard, key=str):
        mine = _merge(heard[tag])
        total = sum(b - a for a, b in mine)
        cells = {n: sum(_overlap(spans[n], a, b) for a, b in mine) for n in names}
        top = max(names, key=lambda n: cells[n])
        derived = (tags.get(tag) or {}).get("name") or ""
        if cells[top] <= 0:
            verdict = "no participant was speaking during this tag's segments"
        elif not derived:
            verdict = f"UNNAMED; Meet says {top}"
        elif _same(derived) in _same(top) or _same(top) in _same(derived):
            verdict = "ok"
        else:
            verdict = f"MISMATCH: Meet says {top} spoke most"
        row = "".join(f"{cells[n]:>{NAME_WIDTH + 2}.1f}" for n in names)
        out.append(
            f"    {str(tag):<6}{row}{total:>11.1f}  {derived or 'Unknown speaker'} -> {verdict}"
        )
    here, there = total_overlap(0), total_overlap(best)
    if best != 0 and there > here * 1.1:
        out.append(
            f"\n  Clock check: moving the Meet times {best:+d} s would raise the total overlap "
            f"from {here:.0f} s to {there:.0f} s. If that looks right, re-run with "
            f"--offset {offset + best:g}."
        )
    else:
        out.append(
            "\n  Clock check: no shift of up to 30 s either way fits the segments clearly better."
        )
    return out


def report(session: Session, meeting_id: int, offset: float = 0.0) -> str:
    """The report text for one meeting; says so plainly when nothing is kept for it."""
    row = session.scalar(
        select(SkribbySegmentEvidence).where(SkribbySegmentEvidence.meeting_id == meeting_id)
    )
    if row is None:
        return (
            f"Meeting {meeting_id}: no Skribby evidence is kept (it is saved before Skribby's "
            "copy is deleted and removed after 7 days, or this meeting did not use Skribby).\n"
        )
    items = list(row.items or [])
    tags = dict(row.tags or {})
    lines = [
        f"Meeting {meeting_id}: {len(items)} items from Skribby, saved "
        f"{row.created_at:%Y-%m-%d %H:%M} UTC; name by elimination was "
        f"{'ON' if row.by_elimination else 'OFF'}.",
    ]
    by_tag: dict[str, list[dict]] = {}
    for item in items:
        tag = item.get("speaker")
        by_tag.setdefault(tag if tag is not None else NO_TAG, []).append(item)
    for tag in sorted(by_tag, key=lambda t: (t == NO_TAG, str(t))):
        info = tags.get(tag, {})
        name = info.get("name") or "Unknown speaker"
        how = info.get("how", "none (a segment with no tag is never named)")
        note = info.get("note")
        would_be = info.get("would_be")
        why = f", {note}" if note else ""
        if would_be:
            why += f" (elimination would have said: {would_be})"
        lines += ["", f"== tag {tag}: {name} [{how}{why}] - {len(by_tag[tag])} segments =="]
        for item in by_tag[tag]:
            given = item.get("speaker_name") or "-"
            listed = ", ".join(item.get("potential_speaker_names") or []) or "-"
            lines.append(
                f"  {_clock(item.get('start'))}-{_clock(item.get('end')).strip()} "
                f"named: {given} | possible: {listed}"
            )
            lines.append(f"      {item.get('text', '')}")
    lines += timeline_section(items, tags, row.events, offset)
    return "\n".join(lines) + "\n"


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("meeting_id", type=int)
    parser.add_argument(
        "--offset",
        type=float,
        default=0.0,
        help="seconds added to Meet's times before comparing (see the clock check)",
    )
    args = parser.parse_args(argv)
    from app.db import SessionLocal

    with SessionLocal() as session:
        print(report(session, args.meeting_id, args.offset), end="")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
