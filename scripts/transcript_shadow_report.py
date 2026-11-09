"""One report on the sampled Skribby-versus-ours transcript check, to decide whether to switch.

Run it once, where the app's `.env` is, and paste the whole output back:

    docker compose exec -T web python scripts/transcript_shadow_report.py
    docker compose exec -T web python scripts/transcript_shadow_report.py --days 14

It reads numbers only (`transcript_shadow_metrics` and `meetings.transcript_source`): never
transcript text, names or emails. The only things it prints about a meeting are its id and
numbers. It changes nothing.

How to read it: in `TRANSCRIPT_SOURCE=skribby`, for `TRANSCRIPT_SHADOW_PERCENT` percent of
meetings our own transcription was run too and compared, then thrown away. "Words in common" is
the words both have, each counted as often as both have it, over the larger of the two counts: 1.00
means the same words (order is not compared). A LOW share means the two disagree, which is not
alone proof that Skribby is the worse one: open the meeting, or run `transcript_compare.py` on it
while the audio is kept, to see which.
"""

import argparse
import statistics
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import Meeting, TranscriptShadowMetric  # noqa: E402

WORST = 5
HOUR = 3600


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _fmt(value: float | None, places: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{places}f}"


def report(session: Session, since: datetime | None = None) -> str:
    """The report text. `since` keeps only meetings created from then on (all when None)."""
    source = select(Meeting.transcript_source, func.count()).group_by(Meeting.transcript_source)
    metrics = select(TranscriptShadowMetric).order_by(TranscriptShadowMetric.id)
    if since is not None:
        source = source.where(Meeting.created_at >= since)
        metrics = metrics.join(Meeting, Meeting.id == TranscriptShadowMetric.meeting_id).where(
            Meeting.created_at >= since
        )
    counts = {key or "not set": n for key, n in session.execute(source)}
    rows = list(session.scalars(metrics))

    lines = ["Transcript source check", ""]
    lines.append("Meetings by where their transcript came from:")
    for key in sorted(counts):
        lines.append(f"  {key}: {counts[key]}")
    if not counts:
        lines.append("  none")
    lines += ["", f"Meetings with a comparison: {len(rows)}"]
    if not rows:
        lines.append(
            "Nothing to compare yet (TRANSCRIPT_SHADOW_PERCENT is 0, or no meeting has run)."
        )
        return "\n".join(lines) + "\n"

    used = {key: sum(1 for r in rows if r.notes_source == key) for key in ("skribby", "own")}
    lines.append(
        f"  notes made from Skribby's: {used['skribby']}; fell back to ours (Skribby's was "
        f"missing or too short): {used['own']}"
    )
    hours = sum(r.audio_seconds for r in rows) / HOUR
    lines.append(f"  audio compared: {hours:.1f} hours")
    both = [r for r in rows if r.skribby_words and r.own_words]
    timed = [r for r in rows if r.audio_seconds]
    skribby_rate = _median([r.skribby_words / (r.audio_seconds / 60) for r in timed])
    own_rate = _median([r.own_words / (r.audio_seconds / 60) for r in timed])
    lines += [
        "",
        "Words in common (1.00 = the same words):",
        f"  median {_fmt(_median([r.shared_share for r in rows]))}, "
        f"lowest {_fmt(min(r.shared_share for r in rows))}, "
        f"highest {_fmt(max(r.shared_share for r in rows))}",
        "Skribby's words as a share of ours (below 1.00 = Skribby wrote less):",
        f"  median {_fmt(_median([r.skribby_words / r.own_words for r in both]))} "
        f"over {len(both)} meetings where both wrote something",
        "Words a minute of audio (Skribby / ours):",
        f"  median {_fmt(skribby_rate, 1)} / {_fmt(own_rate, 1)}",
        "Named speakers per meeting (Skribby):",
        f"  median {_fmt(_median([float(r.skribby_speakers) for r in rows]), 1)}",
    ]
    segments = sum(r.segments for r in rows)
    unnamed = sum(r.unnamed_segments for r in rows)
    share = unnamed / segments if segments else None
    lines.append(
        f"Segments with no speaker name: {unnamed} of {segments} ({_fmt(share, 2)} of all segments)"
    )
    lines += ["", f"The {WORST} meetings where the two disagree most (lowest words in common):"]
    for r in sorted(rows, key=lambda r: (r.shared_share, r.meeting_id))[:WORST]:
        lines.append(
            f"  meeting {r.meeting_id}: in common {r.shared_share:.2f}, Skribby {r.skribby_words} "
            f"words, ours {r.own_words}, {r.audio_seconds // 60} min, notes from {r.notes_source}"
        )
    return "\n".join(lines) + "\n"


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=None, help="only meetings from the last N days")
    args = parser.parse_args(argv)
    from app.db import SessionLocal

    since = datetime.now(UTC) - timedelta(days=args.days) if args.days else None
    with SessionLocal() as session:
        print(report(session, since), end="")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
