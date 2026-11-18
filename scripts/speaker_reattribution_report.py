"""Measure how many Skribby lines would move to a different person if each segment followed Meet's
own "who is speaking" timeline instead of the diarization tag. Changes nothing, anywhere.

Run it where the app's `.env` is, with the meeting's id, and paste the output back:

    docker compose exec -T web python scripts/speaker_reattribution_report.py 40

Why: the tags are named right (meeting 40: every tag "ok" in `skribby_segments_report.py`) yet a tag
holds other people's voices (about 59% of one tag's time overlapped the person it was named for).
Naming the tag cannot fix that: the model itself mixed the voices. This asks the question a
line-level correction would depend on: for each segment, which participant does Meet's timeline show
speaking over most of it, and is that the person the tag says? Design and risks:
docs/superpowers/specs/2026-11-18-line-level-speaker-correction-design.md.

Reads `skribby_segment_evidence` (kept 7 days) and the same Meet timeline, clock placement and
participant grouping (`app/meet_timeline.py`, `app/skribby.py`) the naming rule uses. Per segment:

* `agrees`: Meet's top speaker is the person the tag names.
* `would move`: a different person was speaking over it, clearly (see the two knobs below).
* `ambiguous`: two or more people were lit over it (the runner-up has at least `--ambiguous-ratio`
  of the top's seconds and `--min-seconds`). Would NOT move.
* `no signal`: nobody was lit for `--min-seconds` over it. Would NOT move.
* `not timed`: the segment has no tag or no usable times, so it cannot be read at all.

A tag with no name counts as "Unknown speaker": a clear Meet speaker over it is a move (from nobody
to somebody). Names are compared after cleaning, equal or one inside the other, the same relaxed
test `skribby_segments_report.py` uses for its "ok", so a spelling difference is not called a move.
The bot is never a candidate. A participant who left and rejoined is one person.

The Meet clock is read as `skribby_segments_report.py` reads it (Unix milliseconds placed from the
bot's `recording` status, not proven live) and its own clock check is printed; if it says the clock
may be off, every number below is suspect: re-run with `--offset`. Meet's indicator lights for only
about half of a person's speech and lags, so seconds here are "lit while the line played".

It prints the text of at most the first 15 moves (cut at 200 characters), so a person can judge
them, and nothing else that was said. Treat the output like the transcript.
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import bot_names, meet_timeline, skribby  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.models import Meeting, SkribbySegmentEvidence  # noqa: E402

MAX_MOVES_SHOWN = 15
TEXT_WIDTH = 200
UNKNOWN = "Unknown speaker"
# Report knobs, not production thresholds (the design note proposes the production ones).
MIN_SECONDS = 1.0  # fewest seconds a person must be lit over a segment to count at all
AMBIGUOUS_RATIO = 0.25  # runner-up with this share of the top's seconds makes a segment ambiguous


def _key_and_shown(name: str) -> tuple[str, str]:
    """A participant's name -> (comparison key, shown name): the grouping the naming rule uses
    (`app/skribby.py::_name_by_timeline`), so a rejoin is one person."""
    shown = skribby._clean_name(name)
    return (skribby._same(shown) if shown else ""), shown


def same_person(a: str, b: str) -> bool:
    """Equal after cleaning, or one inside the other (the segments report's "ok" test)."""
    ka, kb = skribby._same(a), skribby._same(b)
    return bool(ka and kb) and (ka == kb or ka in kb or kb in ka)


def _clock(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60:3d}:{total % 60:02d}"


def _tag_of(item: dict) -> str | None:
    return skribby._tag(item.get("speaker"))


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


def analyse(
    items: list[dict],
    tags: dict,
    spans: dict[str, list[tuple[float, float]]],
    min_seconds: float = MIN_SECONDS,
    ambiguous_ratio: float = AMBIGUOUS_RATIO,
) -> dict[str, Any]:
    """Every number of the report. `spans` is each participant's merged speaking intervals already
    on the transcript's clock (the bot left out)."""
    segments = []
    not_timed = {"count": 0, "seconds": 0.0}
    for item in items:
        tag = _tag_of(item)
        a, b = meet_timeline.seconds(item.get("start")), meet_timeline.seconds(item.get("end"))
        if tag is None or a is None or b is None or b <= a:
            not_timed["count"] += 1
            if a is not None and b is not None and b > a:
                not_timed["seconds"] += b - a
            continue
        named = (tags.get(tag) or {}).get("name") or ""
        outcome, person, lit = classify(spans, a, b, min_seconds, ambiguous_ratio)
        if outcome == "clear":
            outcome = "agrees" if named and same_person(named, person) else "move"
        segments.append(
            {
                "tag": tag,
                "tag_name": named,
                "start": a,
                "end": b,
                "seconds": b - a,
                "outcome": outcome,
                "person": person,
                "lit": lit,
                "text": item.get("text") or "",
            }
        )
    return {"segments": segments, "not_timed": not_timed, "people": _people(spans, segments, tags)}


def _people(spans: dict, segments: list[dict], tags: dict) -> dict[str, dict]:
    """Per participant: their Meet seconds, and where those seconds sit: under a tag named for
    them, under a tag named for someone else (by that person), under an unnamed tag, or in no
    segment at all."""
    by_tag: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for seg in segments:
        by_tag[seg["tag"]].append((seg["start"], seg["end"]))
    merged = {tag: meet_timeline.merge(v) for tag, v in by_tag.items()}
    anywhere = meet_timeline.merge([iv for v in by_tag.values() for iv in v])
    people: dict[str, dict] = {}
    for person, intervals in spans.items():
        total = sum(b - a for a, b in intervals)
        row = {"total": total, "own": 0.0, "other": 0.0, "unnamed": 0.0, "elsewhere": {}}
        for tag, tag_intervals in merged.items():
            seconds = sum(meet_timeline.overlap(tag_intervals, a, b) for a, b in intervals)
            if seconds <= 0:
                continue
            named = (tags.get(tag) or {}).get("name") or ""
            if not named:
                row["unnamed"] += seconds
                label = UNKNOWN
            elif same_person(named, person):
                row["own"] += seconds
                continue
            else:
                row["other"] += seconds
                label = named
            row["elsewhere"][label] = row["elsewhere"].get(label, 0.0) + seconds
        covered = sum(meet_timeline.overlap(anywhere, a, b) for a, b in intervals)
        row["outside"] = max(0.0, total - covered)
        people[person] = row
    return people


def render(header: str, result: dict[str, Any], knobs: tuple[float, float]) -> str:
    segments = result["segments"]
    out = [
        header,
        f"  Read-only. A segment needs {knobs[0]:g} s of Meet signal; a runner-up with "
        f"{knobs[1]:.0%} of the top's seconds makes it ambiguous.",
    ]
    out += ["", "== Segments by outcome =="]
    out.append(f"    {'outcome':<30}{'segments':>9}{'seconds':>10}")
    labels = [
        ("agrees", "agrees with the tag's name"),
        ("move", "WOULD MOVE to another person"),
        ("ambiguous", "ambiguous (2+ lit): stays"),
        ("no_signal", "no Meet signal: stays"),
    ]
    for key, label in labels:
        rows = [s for s in segments if s["outcome"] == key]
        out.append(f"    {label:<30}{len(rows):>9}{sum(s['seconds'] for s in rows):>10.1f}")
    nt = result["not_timed"]
    out.append(f"    {'not timed or no tag: stays':<30}{nt['count']:>9}{nt['seconds']:>10.1f}")

    out += ["", "== Per tag: what would move out of it, and to whom =="]
    out.append(
        f"    {'tag':<5}{'named':<16}{'segs':>5}{'secs':>8}{'move':>6}{'move s':>8}{'%':>6}"
        f"{'ambig s':>9}{'nosig s':>9}  moves to"
    )
    tag_rows: dict[str, list[dict]] = defaultdict(list)
    for seg in segments:
        tag_rows[seg["tag"]].append(seg)
    for tag in sorted(tag_rows, key=str):
        rows = tag_rows[tag]
        total = sum(s["seconds"] for s in rows)
        moved = [s for s in rows if s["outcome"] == "move"]
        move_s = sum(s["seconds"] for s in moved)
        dest: dict[str, float] = defaultdict(float)
        for s in moved:
            dest[s["person"]] += s["seconds"]
        to = ", ".join(f"{p} {v:.1f} s" for p, v in sorted(dest.items(), key=lambda kv: -kv[1]))
        amb = sum(s["seconds"] for s in rows if s["outcome"] == "ambiguous")
        nos = sum(s["seconds"] for s in rows if s["outcome"] == "no_signal")
        name = (rows[0]["tag_name"] or UNKNOWN)[:15]
        out.append(
            f"    {str(tag):<5}{name:<16}{len(rows):>5}{total:>8.1f}{len(moved):>6}{move_s:>8.1f}"
            f"{(100 * move_s / total if total else 0):>5.0f}%{amb:>9.1f}{nos:>9.1f}  {to or '-'}"
        )

    out += ["", "== Per person: Meet's speaking seconds and where they sit =="]
    out.append(
        f"    {'person':<18}{'Meet s':>8}{'own tag':>9}{'other':>8}{'unnamed':>9}{'no seg':>8}"
        f"{'% elsewhere':>12}  under another tag"
    )
    for person, row in sorted(result["people"].items(), key=lambda kv: -kv[1]["total"]):
        total = row["total"]
        away = row["other"] + row["unnamed"]
        share = f"{100 * away / total:.0f}%" if total else "-"
        where = ", ".join(
            f"{p} {v:.1f} s" for p, v in sorted(row["elsewhere"].items(), key=lambda kv: -kv[1])
        )
        out.append(
            f"    {person[:17]:<18}{total:>8.1f}{row['own']:>9.1f}{row['other']:>8.1f}"
            f"{row['unnamed']:>9.1f}{row['outside']:>8.1f}{share:>12}  {where or '-'}"
        )

    moves = sorted((s for s in segments if s["outcome"] == "move"), key=lambda s: s["start"])
    out += ["", f"== The first {MAX_MOVES_SHOWN} moves, so a person can judge them =="]
    if not moves:
        out.append("    None: no segment would move.")
    for seg in moves[:MAX_MOVES_SHOWN]:
        text = " ".join(seg["text"].split())
        text = text if len(text) <= TEXT_WIDTH else text[: TEXT_WIDTH - 1] + "…"
        out.append(
            f"    {_clock(seg['start'])}-{_clock(seg['end']).strip()} tag {seg['tag']} "
            f"{seg['tag_name'] or UNKNOWN} -> {seg['person']} "
            f"(lit {seg['lit']:.1f} of {seg['seconds']:.1f} s)"
        )
        out.append(f"        {text}")
    if len(moves) > MAX_MOVES_SHOWN:
        out.append(f"    ({len(moves) - MAX_MOVES_SHOWN} more moves not shown)")
    return "\n".join(out) + "\n"


def build_report(
    meeting_id: int,
    items: list[dict],
    tags: dict,
    record,
    own_names: tuple[str, ...] = (),
    offset: float = 0.0,
    min_seconds: float = MIN_SECONDS,
    ambiguous_ratio: float = AMBIGUOUS_RATIO,
    saved: str = "",
) -> str:
    """The report text from the saved parts; says so plainly when the timeline cannot be used."""
    header = f"Meeting {meeting_id}: {len(items)} items from Skribby{saved}."
    if not record:
        return f"{header}\n  No speaker events were kept, so there is nothing to compare with.\n"
    spans = meet_timeline.speaking_intervals(record, _key_and_shown)
    if not spans:
        return f"{header}\n  Speaker events were kept but are not in a shape this can read.\n"
    zero, how = meet_timeline.anchor(record, spans)
    if zero is None:
        return f"{header}\n  {how}, so they cannot be placed on the transcript.\n"
    bot = {skribby._same(n) for n in map(skribby._clean_name, own_names) if n}
    shift = zero - offset
    spans = {
        n: meet_timeline.merge([(a - shift, b - shift) for a, b in v])
        for n, v in spans.items()
        if skribby._same(n) not in bot
    }
    header += f"\n  {how}; Meet times shifted by {offset:+.0f} s."
    if record.get("truncated"):
        header += f"\n  (events were cut to fit the size cap: {record['truncated']})"
    heard = meet_timeline.heard_by_tag(items, _tag_of)
    if not spans or not heard:
        return f"{header}\n  Nobody to compare, or no timed segment.\n"
    best, here, there = meet_timeline.clock_fit(spans, heard)
    if meet_timeline.clock_is_fine(best, here, there):
        header += "\n  Clock check: no shift of up to 30 s fits the segments clearly better."
    else:
        header += (
            f"\n  CLOCK WARNING: moving Meet's times {best:+d} s would raise the overlap from "
            f"{here:.0f} s to {there:.0f} s; the numbers below may be off. "
            f"Re-run with --offset {offset + best:g} to see them shifted."
        )
    result = analyse(items, tags, spans, min_seconds, ambiguous_ratio)
    return render(header, result, (min_seconds, ambiguous_ratio))


def report(session: Session, meeting_id: int, **knobs) -> str:
    """The report for one meeting; says so plainly when nothing is kept for it."""
    row = session.scalar(
        select(SkribbySegmentEvidence).where(SkribbySegmentEvidence.meeting_id == meeting_id)
    )
    if row is None:
        return (
            f"Meeting {meeting_id}: no Skribby evidence is kept (it is saved before Skribby's "
            "copy is deleted and removed after 7 days, or this meeting did not use Skribby).\n"
        )
    meeting = session.get(Meeting, meeting_id)
    own = bot_names.own_names(get_settings(), meeting.bot_name if meeting else None)
    return build_report(
        meeting_id,
        list(row.items or []),
        dict(row.tags or {}),
        row.events,
        own,
        saved=f", saved {row.created_at:%Y-%m-%d %H:%M} UTC",
        **knobs,
    )


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("meeting_id", type=int)
    parser.add_argument("--offset", type=float, default=0.0, help="seconds added to Meet's times")
    parser.add_argument("--min-seconds", type=float, default=MIN_SECONDS)
    parser.add_argument("--ambiguous-ratio", type=float, default=AMBIGUOUS_RATIO)
    args = parser.parse_args(argv)
    from app.db import SessionLocal

    with SessionLocal() as session:
        print(
            report(
                session,
                args.meeting_id,
                offset=args.offset,
                min_seconds=args.min_seconds,
                ambiguous_ratio=args.ambiguous_ratio,
            ),
            end="",
        )
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
