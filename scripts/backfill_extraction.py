"""Bring meetings that were ready before the AI workspace existed up to date. Run it once, after
the extraction release is deployed (spec 3.3 and D5):

    python scripts/backfill_extraction.py                 # recent meetings queued, old ones read
    python scripts/backfill_extraction.py --task-days 30  # a longer "recent"

Every ready meeting gets its decisions read from the notes, which needs no model. Meetings from the
last 14 days (`--task-days`) are also queued for the worker, which reads their open questions and
review items one pass at a time and asks the model for "heard as" hints. Meetings that already
have an extraction status are left alone, so running it twice changes nothing. It never sends mail.
Run it where the app's `.env` is, for example:

    docker compose exec web python scripts/backfill_extraction.py
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import workspace  # noqa: E402
from app.db import SessionLocal  # noqa: E402


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--task-days",
        type=int,
        default=workspace.BACKFILL_TASK_DAYS,
        help="meetings from this many days back are queued for the full extraction",
    )
    args = parser.parse_args(argv)
    with SessionLocal() as session:
        counts = workspace.backfill(session, datetime.now(UTC), task_days=args.task_days)
    print(
        f"Backfill done: {counts['queued']} queued for the worker, "
        f"{counts['decisions_only']} decisions only."
    )
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
