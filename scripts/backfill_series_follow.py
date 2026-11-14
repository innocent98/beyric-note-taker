"""Split the recurring-meeting rows that mix teams, so each team (and each person's personal space)
has its own row holding only its own meetings.

    docker compose exec -T web python scripts/backfill_series_follow.py            # dry run
    docker compose exec -T web python scripts/backfill_series_follow.py --apply    # do it

The default is a DRY RUN: it does the identical work, prints counts, and rolls everything back. With
`--apply` it saves. Safe to run twice (the second run moves nothing), safe with the flag
`SERIES_FOLLOW_MEETINGS` off. Counts only: no title, name or address is printed. See
`docs/sop/2026-11-14-series-follow-core.md`.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal  # noqa: E402
from app.series_backfill import split_mixed_series  # noqa: E402


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply", action="store_true", help="save the changes (without it: a dry run)"
    )
    args = parser.parse_args(argv)
    with SessionLocal() as session:
        report = split_mixed_series(session, apply=args.apply)
    print("\n".join(report.lines()))
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
