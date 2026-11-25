"""Plans for the workspace tests: the three seeded plans with the owned-team allowance the
migration seeds (`plan_20h` 3, `plan_50h` 10, `plan_100h` unlimited), and a way to put a person on
one. Not a test module."""

from datetime import UTC, datetime, timedelta

from app.models import Plan, PlanPrice, Subscription

HOUR = 3600
# code, name, hours, owned-team allowance (None = unlimited), sort order
PLANS = (
    ("plan_20h", "20 hours a month", 20, 3, 1),
    ("plan_50h", "Pro", 50, 10, 2),
    ("plan_100h", "Scale", 100, None, 3),
)


def seed_plans(session) -> None:
    """The plans and one USD price each (idempotent)."""
    for code, name, hours, allowance, order in PLANS:
        if session.get(Plan, code) is not None:
            continue
        session.add(
            Plan(
                code=code,
                name=name,
                monthly_seconds=hours * HOUR,
                sort_order=order,
                max_owned_teams=allowance,
            )
        )
        session.flush()
        session.add(
            PlanPrice(
                plan_code=code,
                currency="USD",
                amount_minor=1800 * order,
                active_from=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    session.commit()


def subscribe(session, user, code="plan_20h", status="active", now=None) -> Subscription:
    """Put `user` on plan `code`: a subscription paid up to a month from `now`. `status` other
    than a live one is a plan that has ended (`lapsed`, `canceled`). Seeds the plans first."""
    seed_plans(session)
    now = now or datetime.now(UTC)
    price = session.query(PlanPrice).filter_by(plan_code=code).one()
    sub = Subscription(
        user_id=user.id,
        payer_user_id=user.id,
        plan_code=code,
        price_id=price.id,
        status=status,
        currency="USD",
        provider="flutterwave",
        current_period_start=now - timedelta(days=1),
        current_period_end=now + timedelta(days=29),
    )
    if status == "past_due":
        sub.grace_ends_at = now + timedelta(days=2)
    session.add(sub)
    session.commit()
    return sub
