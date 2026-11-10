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
"""

import argparse
import sys
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


def report(session: Session, meeting_id: int) -> str:
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
    return "\n".join(lines) + "\n"


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("meeting_id", type=int)
    args = parser.parse_args(argv)
    from app.db import SessionLocal

    with SessionLocal() as session:
        print(report(session, args.meeting_id), end="")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
