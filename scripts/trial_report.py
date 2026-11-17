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

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import pilot_expiry, trial  # noqa: E402
from app.config import Settings  # noqa: E402


def _hours(seconds: int) -> str:
    return f"{seconds / 3600:.1f}".removesuffix(".0")


def build(session: Session, settings: Settings, now: datetime) -> list[str]:
    lines = [f"Trial and pilot expiry report, {now:%Y-%m-%d %H:%M} UTC", ""]
    rule = trial.hours_rule_in_force(settings)
    lines.append(
        f"The hours rule is {'in force' if rule else 'NOT in force'} "
        f"(HOURS_MODE={settings.hours_mode}, FEATURE_GATES_MODE={settings.feature_gates_mode}); "
        "the trial does not depend on it, the pilot expiry acts only when it is."
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
        f"from {end - timedelta(days=settings.pilot_expiry_notice_days):%Y-%m-%d} UTC."
    )
    lines.append(
        f"People holding pilot grants in force: {len(holders)}; with hours left (they would be "
        f"mailed): {len(told)}."
    )
    if end <= now:
        lines.append("WARNING: that end is already past. With no mail ever sent, nothing expires.")
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
