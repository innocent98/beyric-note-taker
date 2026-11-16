"""Print each person's hours under the old cap rule and under the new grants-based rule, and where
the two would decide differently (billing stage S1, the shadow comparison).

Run it where the app's `.env` is (the last 30 days; `--days N` changes that):

    docker compose exec -T web python scripts/hours_shadow_report.py </dev/null

READ-ONLY: it opens its transaction with `SET TRANSACTION READ ONLY` (Postgres refuses any write
after that) and only runs SELECTs, through `app/usage.py` and `app/entitlements.py`, which only
read. It never reads or prints a name, an email, a title or a transcript; people appear only as
"user 1", "user 2" (ranked by hours recorded in the window, ties by account order) and nothing
else about them.

For each active or suspended account that is not an admin it shows:
  - hours recorded in the window (`usage_entries`, counted in the window);
  - the OLD rule: the cap (own override, else the app default) minus hours used, with the running
    meetings' estimate, and whether a new recording would be allowed (`app/usage.py`);
  - the NEW rule: the state and the seconds left from the person's grants, and whether a new
    recording would be allowed (`entitlements.check_join`);
  - whether the two decisions agree.
Disagreements are listed by kind. It compares the two rules as they stand now; the same comparison
is logged live, one `hours_shadow` line per disagreement, while `HOURS_MODE=shadow`.
"""

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import entitlements, usage  # noqa: E402
from app.models import UsageEntry, User  # noqa: E402

# ---- Rows (numbers only, never text) ----


@dataclass(frozen=True)
class PersonRow:
    user_id: int
    window_seconds: int  # recorded in the window
    old_left: int | None  # None = no cap
    old_allows: bool
    new_state: str
    new_left: int | None
    new_allows: bool
    new_reason: str | None


# ---- Reading (SELECTs only) ----


def open_read_only(session: Session) -> None:
    """Make this session's transaction read-only. Must be the first statement."""
    session.execute(text("SET TRANSACTION READ ONLY"))


def fetch_people(
    session: Session, since: datetime, now: datetime, max_poll_seconds: int
) -> list[PersonRow]:
    in_window = dict(
        session.execute(
            select(UsageEntry.user_id, func.coalesce(func.sum(UsageEntry.seconds), 0))
            .where(UsageEntry.counted_at >= since, UsageEntry.user_id.is_not(None))
            .group_by(UsageEntry.user_id)
        ).all()
    )
    users = session.scalars(
        select(User)
        .where(User.status != "deleted", User.purged_at.is_(None), User.is_admin.is_(False))
        .order_by(User.id)
    ).all()
    rows = []
    for user in users:
        cap = usage.effective_cap(session, user)
        used = usage.hours_used(session, user, now, max_poll_seconds)
        decision = entitlements.check_join(session, user, now, max_poll_seconds)
        rows.append(
            PersonRow(
                user_id=user.id,
                window_seconds=int(in_window.get(user.id, 0)),
                old_left=None if cap is None else max(cap - used, 0),
                old_allows=cap is None or used < cap,
                new_state=entitlements.status(session, user, now, max_poll_seconds).state,
                new_left=decision.seconds_left,
                new_allows=decision.allowed,
                new_reason=decision.reason,
            )
        )
    return rows


# ---- Arithmetic and printing (pure: the tests call these) ----


def hours(seconds: float | None) -> str:
    return "no cap" if seconds is None else f"{seconds / 3600:.2f} h"


def disagreement(row: PersonRow) -> str | None:
    """The kind of disagreement, or None when the two rules decide the same."""
    if row.old_allows == row.new_allows:
        return None
    if row.new_allows:
        return "old rule refuses, new rule allows"
    if row.new_reason == entitlements.NO_PLAN:
        return "old rule allows, new rule refuses: no grant in force"
    return "old rule allows, new rule refuses: grant hours spent"


def ranked(rows: list[PersonRow]) -> list[PersonRow]:
    return sorted(rows, key=lambda r: (-r.window_seconds, r.user_id))


def render(*, days: int, now: datetime, rows: list[PersonRow]) -> str:
    lines = [
        f"Hours under the old and the new rule; window: last {days} days to "
        f"{now:%Y-%m-%d %H:%M} UTC",
        "Admins are left out (no limit under either rule). People are ranked by hours recorded.",
        "",
    ]
    if not rows:
        lines.append("No accounts to compare.")
        return "\n".join(lines)
    kinds: dict[str, int] = {}
    for rank, row in enumerate(ranked(rows), start=1):
        kind = disagreement(row)
        if kind:
            kinds[kind] = kinds.get(kind, 0) + 1
        lines.append(
            f"user {rank}: recorded {hours(row.window_seconds)} | "
            f"old: {hours(row.old_left)} left, {'allow' if row.old_allows else 'refuse'} | "
            f"new: {row.new_state}, {hours(row.new_left)} left, "
            f"{'allow' if row.new_allows else 'refuse'} | "
            f"{'same' if kind is None else 'DIFFERENT'}"
        )
    lines += ["", f"{len(rows)} account(s); {sum(kinds.values())} disagreement(s)."]
    lines += [f"  {n} x {kind}" for kind, n in sorted(kinds.items())]
    if not kinds:
        lines.append("The two rules agree for everyone.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=30, help="how far back to look (default 30)")
    args = parser.parse_args(argv)
    if args.days < 1:
        parser.error("--days must be at least 1")

    from app.config import get_settings
    from app.db import SessionLocal

    now = datetime.now(UTC)
    since = now - timedelta(days=args.days)
    with SessionLocal() as session:
        open_read_only(session)
        rows = fetch_people(session, since, now, get_settings().max_poll_hours * 3600)
        session.rollback()
    print(render(days=args.days, now=now, rows=rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
