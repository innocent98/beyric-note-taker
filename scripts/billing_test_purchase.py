"""The test-mode purchase, by hand: start a plan checkout, prove it, make a renewal due (billing
stage S8a runbook, `docs/sop/2026-11-17-billing-subscriptions.md`).

Run it where the app's `.env` is, with the provider's TEST keys set, `BILLING_LIVE` false,
`BILLING_ENABLED=true` and the account on `BILLING_ALLOWED_EMAILS`:

    docker compose exec -T web python scripts/billing_test_purchase.py checkout EMAIL [--plan CODE]
    docker compose exec -T web python scripts/billing_test_purchase.py proof EMAIL
    docker compose exec -T web python scripts/billing_test_purchase.py make-due EMAIL --yes

- `checkout`: the same call the route `POST /api/billing/checkout` makes, for a person whose billing
  country is already chosen (`PUT /api/billing/profile/country`, or the profile screen): a pending
  payment, then the provider's hosted checkout. Prints the link and our reference. Pay it with the
  provider's test card; the provider calls the webhook, or the scheduler's sweeper settles it by
  asking the provider within a few minutes.
- `proof` (**the single proof command**): READ-ONLY (`SET TRANSACTION READ ONLY`). Checks that the
  person's plan payment is `succeeded`, that exactly one subscription is live and active on the plan
  that payment bought, that exactly one plan-period grant came from that payment with the plan's
  hours and the period's end, and says whether a card is saved. Prints `PROOF: OK` and exits 0, or
  `PROOF: FAILED` and exits 1. It prints no email, no key and no token.
- `make-due`: moves the person's live subscription's dates one hour into the past, so the
  scheduler's next pass (within 60 seconds) charges the SAVED TEST CARD for the renewal. Refuses
  unless `BILLING_LIVE` is false and the account is on the allow-list; `--yes` is required. It
  changes data, so it is for a test account only.

It does nothing at all with a live key or `BILLING_LIVE=true`, and nothing for an account that is
not on the allow-list.
"""

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text, update  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import entitlements  # noqa: E402
from app.billing import routing  # noqa: E402
from app.billing import subscriptions as subs  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    HourGrant,
    Payment,
    PaymentMethod,
    Plan,
    PlanPrice,
    Subscription,
    User,
)


def allowed(settings: Settings, email: str) -> bool:
    people = {p.strip().lower() for p in settings.billing_allowed_emails.split(",") if p.strip()}
    return "*" in people or email.strip().lower() in people


def guard(settings: Settings, email: str) -> None:
    """Stop before touching anything unless this is a test-mode run for an allow-listed account."""
    if not settings.billing_enabled:
        raise SystemExit("BILLING_ENABLED is false: nothing to do")
    if settings.billing_live:
        raise SystemExit("BILLING_LIVE is true: this script is for test mode only")
    if not allowed(settings, email):
        raise SystemExit("that account is not on BILLING_ALLOWED_EMAILS")


def find_user(db: Session, email: str) -> User:
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        raise SystemExit("no account has that email")
    return user


def checkout(db: Session, settings: Settings, email: str, plan_code: str) -> None:
    guard(settings, email)
    user = find_user(db, email)
    try:
        started = subs.start_plan_checkout(db, user, plan_code, settings, datetime.now(UTC))
    except routing.RoutingError as exc:
        db.rollback()
        raise SystemExit(f"{exc.code}: {exc.message}") from None
    except subs.Refused as exc:
        db.rollback()
        raise SystemExit(f"{exc.code}: {exc.message}") from None
    print(f"payment {started.payment.id} ({started.payment.currency}, pending)")
    print(f"reference: {started.payment.reference}")
    print(f"open this link and pay with the provider's TEST card: {started.url}")


def proof(db: Session, settings: Settings, email: str) -> bool:
    """The checks, printed. Read-only: the caller opened the transaction read only."""
    user = find_user(db, email)
    now = datetime.now(UTC)
    ok = True

    def check(label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and passed
        print(f"  [{'ok' if passed else 'FAIL'}] {label}{(': ' + detail) if detail else ''}")

    payment = db.scalars(
        select(Payment)
        .where(
            Payment.payer_user_id == user.id,
            Payment.kind == "plan",
            Payment.status == "succeeded",
        )
        .order_by(Payment.id.desc())
    ).first()
    check("a plan payment has succeeded", payment is not None)
    sub = subs.live_row(db, user.id)
    check("one live subscription, active", sub is not None and sub.status == "active")
    if payment is None or sub is None:
        return False
    price = db.get(PlanPrice, payment.plan_price_id) if payment.plan_price_id else None
    plan = db.get(Plan, price.plan_code) if price else None
    check(
        "the subscription is on the plan that payment bought",
        plan is not None and sub.plan_code == plan.code,
        f"plan {sub.plan_code}, {sub.currency} via {sub.provider}",
    )
    grants = list(
        db.scalars(
            select(HourGrant).where(
                HourGrant.source_payment_id == payment.id, HourGrant.kind == "plan_period"
            )
        )
    )
    check("exactly one plan-period grant came from that payment", len(grants) == 1)
    if len(grants) == 1 and plan is not None:
        grant = grants[0]
        check(
            "the grant has the plan's hours",
            grant.seconds == plan.monthly_seconds,
            f"{grant.seconds} s",
        )
        check(
            "the grant ends with the period",
            grant.expires_at == sub.current_period_end,
            f"ends {sub.current_period_end.isoformat()}",
        )
    card = db.scalar(
        select(PaymentMethod.id).where(
            PaymentMethod.user_id == user.id, PaymentMethod.provider == sub.provider
        )
    )
    print(f"  [{'ok' if card else 'note'}] saved card for renewals: {'yes' if card else 'NO'}")
    seen = entitlements.status(db, user, now)
    print(f"  state now: {seen.state}, hours left: {(seen.seconds_left or 0) / 3600:.2f}")
    renewals = db.scalars(
        select(Payment).where(Payment.subscription_id == sub.id, Payment.kind == "renewal")
    ).all()
    for row in renewals:
        print(f"  renewal payment {row.id}: attempt {row.attempt}, {row.status}")
    return ok


def make_due(db: Session, settings: Settings, email: str) -> None:
    guard(settings, email)
    user = find_user(db, email)
    sub = subs.live_row(db, user.id, lock=True)
    if sub is None:
        raise SystemExit("that person has no live subscription")
    shift = sub.current_period_end - (datetime.now(UTC) - timedelta(hours=1))
    db.execute(
        update(Subscription)
        .where(Subscription.id == sub.id)
        .values(
            current_period_start=Subscription.current_period_start - shift,
            current_period_end=Subscription.current_period_end - shift,
        )
    )
    db.execute(
        update(HourGrant)
        .where(HourGrant.subscription_id == sub.id)
        .values(starts_at=HourGrant.starts_at - shift, expires_at=HourGrant.expires_at - shift)
    )
    db.commit()
    print("the period ended an hour ago: the scheduler charges the saved test card within 60 s")
    print("then run `proof` again: it lists the renewal payment")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("checkout")
    one.add_argument("email")
    one.add_argument("--plan", default="plan_20h")
    two = sub.add_parser("proof")
    two.add_argument("email")
    three = sub.add_parser("make-due")
    three.add_argument("email")
    three.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    with SessionLocal() as db:
        if args.command == "checkout":
            checkout(db, settings, args.email, args.plan)
        elif args.command == "make-due":
            if not args.yes:
                raise SystemExit("this changes data: add --yes (test accounts only)")
            make_due(db, settings, args.email)
        else:
            db.execute(text("set transaction read only"))
            print("proof:")
            passed = proof(db, settings, args.email)
            db.rollback()
            print("PROOF: OK" if passed else "PROOF: FAILED")
            sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
