"""What the trial sweep and the pilot expiry would do right now (billing stage S10): a dry run to
read BEFORE setting `TRIAL_SECONDS` or `PAID_PLANS_OPEN_ON`.

Run it where the app's `.env` is:

    docker compose exec -T web python scripts/trial_report.py </dev/null

READ-ONLY: it opens its transaction with `SET TRANSACTION READ ONLY` and only runs SELECTs. It
prints counts and dates only, never a name, an email or an id. It reads the settings the process
has, so to see what values you intend to set would do, pass them to the command:

    docker compose exec -T -e TRIAL_SECONDS=10800 -e HOURS_MODE=enforce web \
        python scripts/trial_report.py </dev/null

It never gives or expires anything: only the scheduler jobs do, and only once the settings are set.
"""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import pilot_expiry, trial  # noqa: E402
from app.config import Settings  # noqa: E402
from app.models import HourGrant  # noqa: E402

# The lapse line is a NOTE until the first trial is this close to ending, then a WARNING.
LAPSE_WARNING_DAYS = 2


def _hours(seconds: int) -> str:
    return f"{seconds / 3600:.1f}".removesuffix(".0")


def build(session: Session, settings: Settings, now: datetime) -> list[str]:
    lines = [f"Trial and pilot expiry report, {now:%Y-%m-%d %H:%M} UTC", ""]
    enforcing = settings.hours_mode == "enforce"
    lines.append(
        f"HOURS_MODE is {settings.hours_mode} ({'enforcing' if enforcing else 'NOT enforcing'}); "
        f"FEATURE_GATES_MODE is {settings.feature_gates_mode}. The trial does not depend on them; "
        "the pilot expiry acts only under HOURS_MODE=enforce."
    )
    lines += ["", "TRIAL"]
    if settings.trial_seconds <= 0:
        lines.append("TRIAL_SECONDS is 0: the trial is off. Nobody would be given one.")
    else:
        waiting = len(trial.waiting(session))
        lines.append(
            f"A trial is {_hours(settings.trial_seconds)} h for {settings.trial_days} days, "
            f"given once per person."
        )
        lines.append(f"Confirmed accounts that hold no trial or pilot grant: {waiting}.")
        budget = settings.trial_daily_budget_seconds
        if budget:
            per_day = budget // settings.trial_seconds
            used = trial.given_in_window(session, now)
            room = max((budget - used) // settings.trial_seconds, 0)
            days = -(-waiting // per_day) if per_day else 0
            lines.append(
                f"Daily budget {_hours(budget)} h = {per_day} trial(s) in any 24 hours; "
                f"{room} free right now; the {waiting} waiting would take about {days} day(s)."
            )
        else:
            lines.append("No daily budget (TRIAL_DAILY_BUDGET_SECONDS=0): all of them at once.")
            lines.append(
                f"Hours that would be given at once: {_hours(waiting * settings.trial_seconds)}."
            )
    lines += _trial_lapse(session, settings, now, enforcing)
    lines += ["", "PILOT EXPIRY"]
    end = pilot_expiry.end_of(settings)
    if end is None:
        lines.append("PAID_PLANS_OPEN_ON is unset: no pilot hours expire and no mail goes.")
        return lines
    holders = pilot_expiry._holders(session, now)
    told = [u for u in holders if pilot_expiry._pilot_seconds_left(session, u, now) > 0]
    lines.append(
        f"Paid plans open {settings.paid_plans_open_on}; pilot hours can be used until the end of "
        f"{pilot_expiry.last_day_of(end)} (UTC)."
    )
    lines.append(
        f"The one mail goes {settings.pilot_expiry_notice_days} days before: "
        f"from {end - timedelta(days=settings.pilot_expiry_notice_days):%Y-%m-%d} UTC; nobody's "
        f"hours end sooner than {settings.pilot_expiry_min_notice_days} days after their mail."
    )
    if not enforcing:
        lines.append(
            "NOTE: HOURS_MODE is not enforce, so the job would do nothing yet (no end, no mail)."
        )
    lines.append(
        f"People holding pilot grants in force: {len(holders)}; with hours left (they would be "
        f"mailed): {len(told)}."
    )
    if end <= now:
        lines.append(
            "WARNING: that end is already past. If any pilot grant was never given an end, "
            "nothing expires until the date is moved."
        )
    return lines


def _trial_lapse(session: Session, settings: Settings, now: datetime, enforcing: bool) -> list[str]:
    """The preflight for the switch-on order: a trial lasts TRIAL_DAYS in every mode, so trials
    given before HOURS_MODE=enforce end on their own clock. Say so when enforce is not on yet
    and some will end before it can be switched on: a NOTE while there is time (the normal state
    between steps 2 and 5), a WARNING when the first is under LAPSE_WARNING_DAYS away or any has
    already ended."""
    held = session.execute(
        select(func.count(), func.min(HourGrant.expires_at)).where(
            HourGrant.kind == "trial", HourGrant.expires_at > now
        )
    ).one()
    ended = session.scalar(
        select(func.count()).where(HourGrant.kind == "trial", HourGrant.expires_at <= now)
    )
    lines = [
        f"Trials in force: {held[0]}"
        + (f", the first ends {held[1]:%Y-%m-%d %H:%M} UTC." if held[0] else ".")
    ]
    if enforcing:
        return lines
    if held[0]:
        days = max((held[1] - now).days, 0)
        what = (
            f"{held[0]} trial(s) in force end on their own clock, the first in {days} day(s): "
            "switch HOURS_MODE=enforce before then, or those people are refused `no_plan` (no "
            "plan can be bought yet)."
        )
        # Normal while the switch-on is under way; a warning only when it is nearly too late.
        lines.append(("WARNING: " if days < LAPSE_WARNING_DAYS else "NOTE: ") + what)
    if ended:
        lines.append(
            f"WARNING: {ended} trial(s) have already ended while HOURS_MODE was not enforce (any "
            "whose hours were used or not: that is not known before enforce). Those people hold "
            "nothing at enforce; see the SOP for the one-line fix."
        )
    return lines


def main() -> int:
    from app.config import get_settings
    from app.db import SessionLocal

    now = datetime.now(UTC)
    with SessionLocal() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        lines = build(session, get_settings(), now)
        session.rollback()
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
