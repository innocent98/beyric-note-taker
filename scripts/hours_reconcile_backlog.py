"""Settle the usage counted between migration 0049 and the first allocation (billing stage S2).

Run it where the app's `.env` is. By default it only SHOWS what it would do (it does the work in a
transaction and rolls it back); `--apply` keeps it:

    docker compose exec -T web python scripts/hours_reconcile_backlog.py </dev/null
    docker compose exec -T web python scripts/hours_reconcile_backlog.py --apply </dev/null

Why. Stage S1 recorded no allocations, so every recording counted after a person's pilot grant was
made has no `usage_allocations` row. Until it does, `app/entitlements.py` takes those seconds off
the person's grants anyway (`unallocated_seconds`), so **nobody's hours left change, before or
after this script**. It matters later: when a pilot grant expires (stage S10) the same seconds
would otherwise come off the person's paid hours. Run it once before `HOURS_MODE=enforce`; running
it again changes nothing (an entry that has an allocation is settled).

It writes only `usage_allocations`, and only on the person's own pilot grants, oldest recording
first. It never prints a name, an email or a title: people appear as "user 1", "user 2", ranked by
seconds settled. A recording longer than what the pilot grant had left is settled in full and the
excess is written off, as at a live count (`app/hours.py`).
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import entitlements, hours  # noqa: E402
from app.models import HourGrant, User  # noqa: E402


def reconcile(session: Session, now: datetime, max_poll_seconds: int) -> list[tuple[int, int, int]]:
    """Settle every person's backlog. Returns `(user id, seconds settled, seconds left)` for each
    person who had any, where `seconds left` is what they may record after, and writes nothing
    but allocations. The caller commits or rolls back."""
    ids = session.scalars(
        select(HourGrant.user_id).where(HourGrant.kind == "pilot", HourGrant.user_id.is_not(None))
    ).all()
    done = []
    for user in session.scalars(select(User).where(User.id.in_(set(ids))).order_by(User.id)):
        settled = hours.reconcile_backlog(session, user)
        if settled:
            left = entitlements.status(session, user, now, max_poll_seconds).seconds_left
            done.append((user.id, settled, left))
    return done


def render(rows: list[tuple[int, int, int]], *, apply: bool) -> str:
    verb = "Settled" if apply else "Would settle (nothing was kept)"
    if not rows:
        return "No usage is waiting to be settled."
    lines = [f"{verb} the usage counted before allocations existed:"]
    ranked = sorted(rows, key=lambda r: (-r[1], r[0]))
    for rank, (_, settled, left) in enumerate(ranked, start=1):
        lines.append(
            f"user {rank}: {settled / 3600:.2f} h settled; {left / 3600:.2f} h left afterwards "
            "(the same as before)"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="keep the changes (default: dry run)")
    args = parser.parse_args(argv)
    from app.config import get_settings
    from app.db import SessionLocal

    settings = get_settings()
    now = datetime.now(UTC)
    with SessionLocal() as session:
        rows = reconcile(session, now, settings.max_poll_hours * 3600)
        if args.apply:
            session.commit()
        else:
            session.rollback()
    print(render(rows, apply=args.apply))
    return 0


if __name__ == "__main__":
    sys.exit(main())
