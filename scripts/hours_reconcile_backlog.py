"""Bring the grants in line with the old cap rule before `HOURS_MODE=enforce` (billing stage S2).

Run it where the app's `.env` is. By default it only SHOWS what it would do (it does the work in a
transaction and rolls it back); `--apply` keeps it:

    docker compose exec -T web python scripts/hours_reconcile_backlog.py </dev/null
    docker compose exec -T web python scripts/hours_reconcile_backlog.py --apply </dev/null

It does two things for each person, in this order:

1. **Cap raises.** The grants rule never reads `users.hours_cap_seconds`, so an admin's raise
   (5 h to 10 h) made before `enforce` gave the person nothing. It gives them the difference as
   `admin_gift` grants (`hours.sync_cap_gift`), derived from the cap as it stands, so a cap that
   went down and up again is gifted once, and running it twice gifts nothing more. This is the only
   step that adds hours, and it is shown as "gift".
2. **The S1 backlog.** Stage S1 recorded no allocations, so every recording counted after a
   person's pilot grant was made has none. Until it does, `app/entitlements.py` takes those seconds
   off the grants anyway (`unallocated_seconds`). This step turns them into allocations across the
   person's grants **in the decided spending order** (pilot or trial, plan hours, gifts and packs),
   exactly as that term spends them, so the hours left do not change; what it adds is that the
   backlog can never come off paid hours when a pilot grant later expires (S10). The script prints
   hours left before and after this step for every person, marks any change, and **refuses
   `--apply` while any person's hours would change** (it should never happen: it is a safety net).

Run it once before switching to enforce and **again right before the switch** (dry run, then
`--apply`): recordings and cap edits made in `shadow` in between are settled by the second run. It
is safe to re-run. Deleted accounts are skipped.

It writes only `usage_allocations` and `hour_grants`. It never prints a name, an email or a title:
people appear as "user 1", "user 2", ranked by seconds settled.
"""

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import entitlements, hours  # noqa: E402
from app.models import HourGrant, User  # noqa: E402


@dataclass(frozen=True)
class Row:
    user_id: int
    gifted: int  # seconds given for a raised cap
    settled: int  # seconds of backlog turned into allocations
    before: int | None  # hours left before the backlog step (None: no limit)
    after: int | None

    @property
    def changes_hours(self) -> bool:
        return self.before != self.after


def reconcile(session: Session, now: datetime, max_poll_seconds: int) -> list[Row]:
    """Gift the raised caps, then settle every person's backlog. Returns a row for each person
    something happened to. Writes nothing but grants and allocations; the caller commits or rolls
    back."""
    pilots = select(HourGrant.user_id).where(
        HourGrant.kind == "pilot", HourGrant.user_id.is_not(None)
    )
    overridden = select(User.id).where(User.hours_cap_seconds.is_not(None))
    done = []
    for user in session.scalars(
        select(User)
        .where(
            User.id.in_(pilots.union(overridden)),
            User.status != "deleted",
            User.purged_at.is_(None),
        )
        .order_by(User.id)
    ):
        gifted = hours.sync_cap_gift(session, user)
        before = entitlements.status(session, user, now, max_poll_seconds).seconds_left
        settled = hours.reconcile_backlog(session, user, now)
        after = entitlements.status(session, user, now, max_poll_seconds).seconds_left
        if gifted or settled:
            done.append(Row(user.id, gifted, settled, before, after))
    return done


def _h(seconds: int | None) -> str:
    return "no limit" if seconds is None else f"{seconds / 3600:.2f} h"


def render(rows: list[Row], *, apply: bool) -> str:
    if not rows:
        return "Nothing to gift and no usage waiting to be settled."
    verb = "Done" if apply else "Would do (nothing was kept)"
    lines = [f"{verb}: cap raises gifted, and usage counted before allocations existed settled:"]
    for rank, row in enumerate(sorted(rows, key=lambda r: (-r.settled, r.user_id)), start=1):
        flag = "  <-- HOURS CHANGE" if row.changes_hours else ""
        lines.append(
            f"user {rank}: gift {_h(row.gifted)}; settled {_h(row.settled)}; "
            f"left {_h(row.before)} before settling, {_h(row.after)} after{flag}"
        )
    changed = sum(row.changes_hours for row in rows)
    if changed:
        lines.append(
            f"{changed} person(s) would change hours left by settling: look at them first. "
            "--apply refuses while that is so."
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
        refused = args.apply and any(row.changes_hours for row in rows)
        if args.apply and not refused:
            session.commit()
        else:
            session.rollback()
    print(render(rows, apply=args.apply and not refused))
    if refused:
        print("Nothing was applied.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
