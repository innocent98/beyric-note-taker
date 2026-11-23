"""Print what Skribby sent for one meeting, grouped by speaker tag, with the name the app derived.

Run it where the app's `.env` is, with the meeting's id, and paste the output back:

    docker compose exec -T web python scripts/skribby_segments_report.py 29

The evidence is written just before Skribby's copy is deleted and kept 7 days
(`skribby_segment_evidence`). This reads it and changes nothing. It prints what people said, so
treat the output like the transcript: do not paste it anywhere public.

How to read it: each block is one diarization tag ("speaker" in Skribby's answer). The header says
the name the app gave the tag and why: `given` (Skribby named it on its own segments),
`elimination` (taken from `potential_speaker_names` by ruling out the others; a name an earlier
tag was given by elimination counts as taken, so it can run down a chain), `sole_candidate`
(every segment of the tag listed one and the same name, nobody else has it), both only while
SKRIBBY_NAME_BY_ELIMINATION is on, `meet_timeline` or `meet_exclusion` (see the last section), or
`unknown` (with the reason). Under it, every segment of that
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
shape it cannot read, it says so in one line. A final section says which unnamed tags today's
naming rules would name (and by which route: `elimination`, `meet_timeline` or `meet_exclusion`),
and why not for the others. It reads the saved evidence again and changes nothing.
"""

import argparse
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import bot_names, meet_timeline, skribby  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.models import Meeting, SkribbySegmentEvidence  # noqa: E402

NO_TAG = "(no tag)"


def _clock(seconds) -> str:
    if not isinstance(seconds, int | float):
        return "  ?:??"
    total = int(seconds)
    return f"{total // 60:3d}:{total % 60:02d}"


# ---- Meet's own speaker timeline ----

# The arithmetic lives in `app/meet_timeline.py`, shared with the naming rule (`meet_timeline`
# in the tag headers), so the report and the app can never read the timeline two ways.
SEARCH_SECONDS = meet_timeline.SEARCH_SECONDS  # how far either way the clock fit looks
NAME_WIDTH = 12

_seconds = meet_timeline.seconds
_merge = meet_timeline.merge
_overlap = meet_timeline.overlap
speaking_intervals = meet_timeline.speaking_intervals
_anchor = meet_timeline.anchor
heard_by_tag = meet_timeline.heard_by_tag
clock_fit = meet_timeline.clock_fit


def _same(name: str) -> str:
    plain = unicodedata.normalize("NFKD", name)
    return " ".join("".join(c for c in plain if not unicodedata.combining(c)).casefold().split())


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
    heard = heard_by_tag(items)
    if not heard:
        return [*out, "", "  No Skribby segment has both a tag and times, so nothing to compare."]
    best, here, there = clock_fit(spans, heard)
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


def naming_section(
    items: list[dict],
    tags: dict,
    record,
    own_names: tuple[str, ...] = (),
    by_elimination: bool = True,
) -> list[str]:
    """What today's naming rules would do to the tags this meeting left unnamed, run on the saved
    items, tags and events at offset 0 (the rule never uses `--offset`: a shifted clock is a reason
    to name nothing): the chained elimination, then the timeline rule (`NAME_BY_MEET_TIMELINE`,
    `app/skribby.py::_name_by_timeline`), then exclusion against Meet's participant list
    (`meet_exclusion`). Each tag it would name prints the route in brackets. Read-only: nothing is
    saved, so a meeting saved before a rule existed shows what the rule would have done, and the
    meeting itself stays as it was."""
    head = ["", "== What naming from Meet's timeline would do to the unnamed tags =="]
    if not record:
        return [*head, "  No speaker events were kept, so it would name nothing."]
    value = [
        {
            **i,
            "potential_speaker_names": [
                {"name": n} for n in i.get("potential_speaker_names") or []
            ],
        }
        for i in items
    ]
    after = skribby.apply_timeline(tags, value, record, own_names, by_elimination)
    out = []
    for tag in sorted(after, key=str):
        if tags.get(tag, {}).get("name"):
            continue
        if after[tag].get("name"):
            out.append(f"  tag {tag}: would be named {after[tag]['name']} [{after[tag]['how']}]")
        else:
            why = after[tag].get("timeline") or "no reason"
            if after[tag].get("exclusion"):
                why += f"; by exclusion: {after[tag]['exclusion']}"
            out.append(f"  tag {tag}: stays unknown ({why})")
    return [*head, *(out or ["  Every tag already has a name."])]


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
            why += f" (naming by elimination or sole candidate would have said: {would_be})"
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
    meeting = session.get(Meeting, meeting_id)
    own = bot_names.own_names(get_settings(), meeting.bot_name if meeting else None)
    lines += naming_section(items, tags, row.events, own, bool(row.by_elimination))
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
