"""Remove the EMPTY recurring cards the calendar sync made in a team, so each person's calendar
cards live in their personal space (the simpler recurring model).

docker compose exec -T web python scripts/remove_empty_team_cards.py # dry run docker compose exec
-T web python scripts/remove_empty_team_cards.py --apply # do it

The default is a DRY RUN: it does the identical work, prints counts, and rolls everything back.
With `--apply` it saves. Safe to run twice (the second run removes nothing). It never writes an
"ignored" mark, so the next calendar sync makes the personal card. A card with a meeting, a
waiting agenda line, a linked project or a mail still to go is kept; so is a card made by hand and
every personal card. Counts only: no title, name or address is printed. See
`docs/sop/2026-11-16-series-personal-calendar-owner-admin-teams.md`.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal  # noqa: E402
from app.series_backfill import remove_empty_team_cards  # noqa: E402


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply", action="store_true", help="save the changes (without it: a dry run)"
    )
    args = parser.parse_args(argv)
    with SessionLocal() as session:
        report = remove_empty_team_cards(session, apply=args.apply)
    print("\n".join(report.lines()))
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
