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
- `preflight STEP` (1 or 3) and `state EMAIL`: the proof at each step of the switch-on sequence in
  the SOP. READ-ONLY. `preflight 1` checks what turning billing on for the allow-list needs:
  flags, the schema, the keys PRESENT but never printed, the catalog and which currencies have a
  price. `preflight 3` checks what the hours rule and the feature gates need (no usage waiting to
  be allocated) and prints the two modes. `state` prints what one person can do under the modes
  this process runs with (state, hours left, whether a recording starts, whether Ask is allowed).
  `preflight` prints `PREFLIGHT OK` and exits 0, or `PREFLIGHT FAILED` and exits 1.
- `make-due`: moves the person's live subscription's dates one hour into the past, so the
  scheduler's next pass (within 60 seconds) charges the SAVED TEST CARD for the renewal. Refuses
  unless `BILLING_LIVE` is false and the account is on the allow-list; `--yes` is required. It
  changes data, so it is for a test account only.

WITH LIVE BILLING (`BILLING_LIVE=true`, runbook `docs/launch/billing-go-live-runbook.md` sections
5, 6 and 8) only the READ-ONLY commands run, each inside `SET TRANSACTION READ ONLY` that is checked
to be on before anything is read (`read_only`), so a write fails in the database whatever the code
says:

    docker compose exec -T web python scripts/billing_test_purchase.py proof EMAIL
    docker compose exec -T web python scripts/billing_test_purchase.py proof EMAIL --refund
    docker compose exec -T web python scripts/billing_test_purchase.py state EMAIL
    docker compose exec -T web python scripts/billing_test_purchase.py live-check

- `proof EMAIL` (live): the first real payment (section 5): the newest live plan payment is
  `succeeded` with `paid_at`; one live active subscription on its plan; the invoice has a live
  number `BN-<year>-NNNNNN`, is not test-mode and is not hidden by `BILLING_LIVE_SINCE`; the plan's
  hours were granted; and whether a SIGNED webhook reached the app. Prints `WEBHOOK PATH PROVED` or
  `WEBHOOK PATH NOT PROVED`, then `PROOF: OK` (exit 0), `PROOF: OK, BUT WEBHOOK PATH NOT PROVED`
  (exit 2) or `PROOF: FAILED` (exit 1).
- `proof EMAIL --refund` (section 6): the refund is `first_plan_7d`, `submitted`, has the provider's
  refund id, the payment is `refunded`, the subscription `ended`, the saved card is gone.
- `live-check` (section 4 before the first payment, section 8 after): the live settings (key
  PREFIXES only, never a key or a secret), the seller's details, the plans and prices, the invoice
  counters, and the webhook inbox's health. Prints `PREFLIGHT OK` or `PREFLIGHT FAILED`.
- `checkout` and `make-due` (anything that writes or moves money) refuse with live billing, and also
  when a live key or `BILLING_LIVE_SINCE` is present while `BILLING_LIVE` is false (a half-reverted
  setup). Output never has a key, a secret or an email.

Nothing runs for an account that is not on the allow-list in test mode.
"""

import argparse
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, inspect, or_, select, text, update  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import entitlements  # noqa: E402
from app.billing import cutover, routing  # noqa: E402
from app.billing import subscriptions as subs  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    LIVE_SUBSCRIPTION_STATUSES,
    BillingEvent,
    HourGrant,
    Invoice,
    InvoiceCounter,
    MailOutbox,
    Payment,
    PaymentMethod,
    Plan,
    PlanPrice,
    Refund,
    Subscription,
    User,
)


def allowed(settings: Settings, email: str) -> bool:
    people = {p.strip().lower() for p in settings.billing_allowed_emails.split(",") if p.strip()}
    return "*" in people or email.strip().lower() in people


def looks_live(settings: Settings) -> bool:
    """True when the settings carry a sign of live billing even though `BILLING_LIVE` may say
    otherwise: a Paystack `sk_live_` / `pk_live_` key, a Flutterwave key without `_TEST`, or the
    cutover time. `Settings` refuses such a mix at startup; this is the second, independent lock
    for the commands that write, so a tampered flag alone cannot open them."""
    paystack = (settings.paystack_secret_key.strip(), settings.paystack_public_key.strip())
    flutterwave = (settings.flutterwave_secret_key.strip(), settings.flutterwave_public_key.strip())
    return (
        any(key.startswith(("sk_live_", "pk_live_")) for key in paystack)
        or any(key and "_TEST" not in key for key in flutterwave)
        or bool(settings.billing_live_since.strip())
    )


def guard(settings: Settings, email: str) -> None:
    """Stop before touching anything unless this is a test-mode run for an allow-listed account."""
    if not settings.billing_enabled:
        raise SystemExit("BILLING_ENABLED is false: nothing to do")
    if settings.billing_live or looks_live(settings):
        raise SystemExit(
            "BILLING_LIVE is true (or a live key / BILLING_LIVE_SINCE is set): this command is "
            "for test mode only; with live billing only proof, state and live-check run"
        )
    if not allowed(settings, email):
        raise SystemExit("that account is not on BILLING_ALLOWED_EMAILS")


def find_user(db: Session, email: str) -> User:
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        raise SystemExit("no account has that email")
    return user


@contextmanager
def read_only(db: Session) -> Iterator[None]:
    """Everything inside runs in a transaction the DATABASE holds read only, and is rolled back at
    the end. The setting is read back and the run stops unless it is on, so a read command can never
    quietly become a writer."""
    db.rollback()  # `set transaction` must be the first statement of its transaction
    db.execute(text("set transaction read only"))
    if db.execute(text("show transaction_read_only")).scalar_one() != "on":
        db.rollback()
        raise SystemExit("could not make the transaction read only: nothing was read")
    try:
        yield
    finally:
        db.rollback()


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
    invoice = db.scalars(select(Invoice).where(Invoice.payment_id == payment.id)).first()
    check("that payment has exactly one invoice", invoice is not None)
    if invoice is not None:
        check(
            "the invoice is for what was paid, and its parts add up",
            invoice.total_minor == payment.amount_minor
            and invoice.currency == payment.currency
            and invoice.subtotal_minor + invoice.vat_minor == invoice.total_minor,
            f"{invoice.number}, {invoice.currency} {invoice.total_minor / 100:.2f}, "
            f"VAT {invoice.vat_minor / 100:.2f} at {invoice.vat_rate_bp} bp",
        )
        receipt = db.scalar(
            select(MailOutbox.status).where(
                MailOutbox.template == "billing_receipt",
                MailOutbox.payload["invoice_id"].as_integer() == invoice.id,
            )
        )
        mailed = receipt or "not queued (no mail provider configured)"
        print(f"  [{'ok' if receipt else 'note'}] receipt mail: {mailed}")
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


def preflight(db: Session, settings: Settings, step: int) -> bool:
    """The checks for one step of the switch-on sequence; read-only. Prints every line."""
    ok = True

    def check(label: str, passed: bool, detail: str = "", *, must: bool = True) -> None:
        nonlocal ok
        ok = ok and (passed or not must)
        mark = "ok" if passed else ("FAIL" if must else "note")
        print(f"  [{mark}] {label}{(': ' + detail) if detail else ''}")

    if step == 1:
        people = [p for p in settings.billing_allowed_emails.split(",") if p.strip()]
        check("BILLING_ENABLED is true", settings.billing_enabled)
        check(
            "BILLING_ALLOWED_EMAILS is not empty",
            bool(people),
            "every account" if "*" in people else f"{len(people)} account(s)",
        )
        check("BILLING_LIVE is false (test keys only)", not settings.billing_live)
        check(
            "TOKEN_ENC_KEY is set (saved cards are encrypted with it)", bool(settings.token_enc_key)
        )
        check("the Paystack secret key is set", bool(settings.paystack_secret_key.strip()))
        check(
            "the Paystack webhook secret is set",
            bool(settings.paystack_webhook_secret.strip()),
            "without it the older signature header is used",
            must=False,
        )
        check(
            "the Flutterwave secret key is set",
            bool(settings.flutterwave_secret_key.strip()),
            "dollars cannot be bought without it",
            must=False,
        )
        tables = set(inspect(db.get_bind()).get_table_names())
        check("migration 0052 is applied", {"subscriptions", "plans", "plan_prices"} <= tables)
        check(
            "migration 0053 is applied (invoices and refunds)",
            {"invoices", "refunds", "invoice_counters"} <= tables,
        )
        # Stage S9: what an invoice says about the seller comes from these settings and nothing is
        # invented. A test-mode invoice is marked as not a tax document, so these only block live.
        for label, value in (
            ("BILLING_SELLER_ADDRESS", settings.billing_seller_address),
            ("BILLING_SELLER_REGISTRATION_NO", settings.billing_seller_registration_no),
            ("BILLING_SELLER_TAX_ID", settings.billing_seller_tax_id),
        ):
            check(
                f"{label} is set (the seller's line on every invoice)",
                bool(value.strip()) and "PLACEHOLDER" not in value.upper(),
                "" if value.strip() else "empty: the line is left off the invoice, never invented",
                must=False,
            )
        check(
            "BILLING_VAT_RATE_BP",
            True,
            f"{settings.billing_vat_rate_bp} basis points of VAT INCLUDED in the price "
            "(0: no VAT line; the accountant decides)",
        )
        if "plans" in tables:
            plan_rows = db.execute(
                select(Plan.code, PlanPrice.currency)
                .join(PlanPrice, PlanPrice.plan_code == Plan.code)
                .where(Plan.active.is_(True))
            ).all()
            currencies = sorted({c for _, c in plan_rows})
            check("an active plan has a price", bool(plan_rows), ", ".join(currencies))
            for currency in ("USD", "NGN"):
                check(
                    f"a price in {currency}",
                    currency in currencies,
                    "" if currency in currencies else "people billed there get price_not_available",
                    must=False,
                )
        print(
            "  [note] the billing clock runs in the scheduler process: recreate it after the change"
        )
    elif step == 3:
        check(
            "HOURS_MODE",
            settings.hours_mode in ("shadow", "enforce"),
            settings.hours_mode,
            must=False,
        )
        check("FEATURE_GATES_MODE", True, settings.feature_gates_mode)
        pilots = db.scalars(
            select(User)
            .join(HourGrant, HourGrant.user_id == User.id)
            .where(HourGrant.kind == "pilot")
            .distinct()
        ).all()
        backlog = sum(entitlements.unallocated_seconds(db, p) for p in pilots)
        check(
            "no usage waits to be allocated to a pilot grant",
            backlog == 0,
            f"{backlog} s: run scripts/hours_reconcile_backlog.py first" if backlog else "",
        )
        check("billing is on (the hours rule sells what billing buys)", settings.billing_enabled)
    else:
        raise SystemExit("preflight takes step 1 or 3 (step 2 is `checkout` then `proof`)")
    return ok


def state(db: Session, settings: Settings, email: str) -> None:
    """What one person can do under the modes this process runs with."""
    user = find_user(db, email)
    now = datetime.now(UTC)
    held = entitlements.status(db, user, now, settings.max_poll_hours * 3600)
    join = entitlements.check_join(db, user, now, settings.max_poll_hours * 3600)
    ask = entitlements.can(db, user, entitlements.ASK, now)
    print(f"  state: {held.state}, hours left: {(held.seconds_left or 0) / 3600:.2f}")
    print(
        f"  a new recording would {'start' if join.allowed else f'be refused ({join.reason})'}"
        f" if HOURS_MODE were enforce (it is {settings.hours_mode})"
    )
    print(
        f"  Ask Beyric is {'allowed' if ask else 'closed'} by the rule; the gate is "
        f"{settings.feature_gates_mode} (closed only when enforce)"
    )


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


# ---- live billing: the read-only proofs (runbook sections 4 to 8) -------------------------------

LIVE_NUMBER = re.compile(r"BN-([0-9]{4})-([0-9]{6})")  # a positive year: the live series
WEBHOOK_PROVED = "WEBHOOK PATH PROVED: a signed webhook reached the app and was decided"
WEBHOOK_NOT_PROVED = (
    "WEBHOOK PATH NOT PROVED: the sweeper settled it without a signed webhook "
    "(or the webhook arrived but was not decided): tell the lead"
)


class Checks:
    """The printed checks of one run: `must` ones decide the exit code, `note` ones only inform."""

    def __init__(self) -> None:
        self.ok = True

    def __call__(self, label: str, passed: bool, detail: str = "", *, must: bool = True) -> None:
        self.ok = self.ok and (passed or not must)
        mark = "ok" if passed else ("FAIL" if must else "note")
        print(f"  [{mark}] {label}{(': ' + detail) if detail else ''}")


def money(minor: int, currency: str) -> str:
    return f"{currency} {minor / 100:,.2f}"


def prefix_of(value: str, *known: str) -> str:
    """Which of the KNOWN prefixes a key starts with, never any other character of it."""
    value = value.strip()
    if not value:
        return "missing"
    return next((p for p in known if value.startswith(p)), "an unrecognised prefix")


def plan_payment(db: Session, user: User, settings: Settings) -> Payment | None:
    """The person's newest live plan payment that succeeded, else their newest live one of any
    status (so the proof can say what it is). Test-era payments (before `BILLING_LIVE_SINCE`) do
    not count."""
    base = select(Payment).where(
        Payment.payer_user_id == user.id,
        Payment.kind == "plan",
        cutover.live_only(Payment.created_at, settings),
    )
    return (
        db.scalars(base.where(Payment.status == "succeeded").order_by(Payment.id.desc())).first()
        or db.scalars(base.order_by(Payment.id.desc())).first()
    )


def signed_event(db: Session, payment: Payment, settings: Settings) -> BillingEvent | None:
    """The best `billing_events` row for this payment: a `charge_succeeded` of its provider that
    names its reference (Paystack `data.reference`, Flutterwave `data.tx_ref`; the test fake puts
    `reference` at the top), signed and decided if there is one, else any."""
    data = BillingEvent.payload["data"]
    rows = list(
        db.scalars(
            select(BillingEvent)
            .where(
                BillingEvent.provider == payment.provider,
                BillingEvent.type == "charge_succeeded",
                cutover.live_only(BillingEvent.received_at, settings),
                or_(
                    data["reference"].astext == payment.reference,
                    data["tx_ref"].astext == payment.reference,
                    BillingEvent.payload["reference"].astext == payment.reference,
                ),
            )
            .order_by(BillingEvent.id)
        )
    )
    proved = [r for r in rows if r.signature_ok and r.processed_at is not None]
    return (proved or rows or [None])[0]


def live_proof(db: Session, settings: Settings, email: str) -> int:
    """Section 5's proof for one person after a REAL payment. Read-only (the caller opened the
    transaction with `read_only`). Returns the exit code: 0 proved, 1 failed, 2 everything but the
    webhook path is right."""
    user = find_user(db, email)
    now = datetime.now(UTC)
    check = Checks()
    check("BILLING_LIVE is true (this proof is for real payments)", settings.billing_live)
    payment = plan_payment(db, user, settings)
    check("a live plan payment exists for this account", payment is not None)
    if payment is None:
        print("PROOF: FAILED")
        return 1
    check(
        "the payment succeeded",
        payment.status == "succeeded",
        f"payment {payment.id}, {payment.provider}, {money(payment.amount_minor, payment.currency)}"
        f", status {payment.status}" + (" (use --refund)" if payment.status == "refunded" else ""),
    )
    check("paid_at is set", payment.paid_at is not None)
    check(
        "the card country is on file",
        bool(payment.card_country),
        f"{payment.card_country}, country_mismatch {payment.country_mismatch}",
        must=False,
    )
    price = db.get(PlanPrice, payment.plan_price_id) if payment.plan_price_id else None
    plan = db.get(Plan, price.plan_code) if price else None
    check("the payment names a plan price", plan is not None and price is not None)
    live_subs = list(
        db.scalars(
            select(Subscription).where(
                Subscription.user_id == user.id,
                Subscription.status.in_(LIVE_SUBSCRIPTION_STATUSES),
            )
        )
    )
    sub = live_subs[0] if len(live_subs) == 1 else None
    check("exactly one live subscription", len(live_subs) == 1, f"{len(live_subs)} found")
    if sub is not None:
        check("the subscription is active", sub.status == "active", sub.status)
        check(
            "the subscription is on the plan and route that payment bought",
            plan is not None
            and sub.plan_code == plan.code
            and sub.provider == payment.provider
            and sub.currency == payment.currency
            and payment.subscription_id == sub.id,
            f"plan {sub.plan_code}, {sub.currency} via {sub.provider}",
        )
    invoice = db.scalars(select(Invoice).where(Invoice.payment_id == payment.id)).first()
    check("that payment has an invoice", invoice is not None)
    if invoice is not None:
        found = LIVE_NUMBER.fullmatch(invoice.number)
        check(
            "the invoice number is in the live series BN-<year>-NNNNNN",
            found is not None and int(found.group(1)) > 0,
            invoice.number,
        )
        check(
            "the invoice is not test-mode", not invoice.test_mode, f"test_mode {invoice.test_mode}"
        )
        check(
            "the invoice is not hidden by BILLING_LIVE_SINCE",
            not cutover.is_test_era(payment.created_at, settings),
            f"the payment was made after {cutover.since(settings)}",
        )
        check(
            "the invoice is for what was paid, and its parts add up",
            invoice.total_minor == payment.amount_minor
            and invoice.currency == payment.currency
            and invoice.subtotal_minor + invoice.vat_minor == invoice.total_minor,
            f"{money(invoice.total_minor, invoice.currency)}, VAT {invoice.vat_minor / 100:.2f}",
        )
        if found is not None:
            counter = db.get(InvoiceCounter, int(found.group(1)))
            check(
                "the live counter has reached this number",
                counter is not None and counter.last_number >= int(found.group(2)),
                f"counter {counter.last_number if counter else 'missing'}",
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
        check(
            "the grant equals the plan's hours",
            grants[0].seconds == plan.monthly_seconds,
            f"{grants[0].seconds / 3600:.2f} h, the plan has {plan.monthly_seconds / 3600:.2f} h",
        )
        if sub is not None:
            check("the grant ends with the period", grants[0].expires_at == sub.current_period_end)
    held: dict[str, int] = {}
    for grant, left in entitlements.grants_in_force(db, user, now):
        held[grant.kind] = held.get(grant.kind, 0) + left
    seen = entitlements.status(db, user, now)
    print(
        "  [note] hours held now: "
        + (", ".join(f"{k} {v / 3600:.2f} h" for k, v in held.items()) or "none")
        + f" (state {seen.state}); a trial's leftover adds to the plan's hours"
    )
    saved = db.scalar(
        select(func.count())
        .select_from(PaymentMethod)
        .where(PaymentMethod.user_id == user.id, PaymentMethod.provider == payment.provider)
    )
    check("a saved card for renewals", bool(saved), "yes" if saved else "NO", must=False)
    event = signed_event(db, payment, settings)
    proved = event is not None and event.signature_ok and event.processed_at is not None
    if event is not None:
        print(
            f"  [note] webhook event {event.id} ({event.type}): signature_ok {event.signature_ok}"
            f", decided {event.processed_at is not None}"
            + (", an error is recorded" if event.error else "")
        )
    print(WEBHOOK_PROVED if proved else WEBHOOK_NOT_PROVED)
    if not check.ok:
        print("PROOF: FAILED")
        return 1
    if not proved:
        print("PROOF: OK, BUT WEBHOOK PATH NOT PROVED")
        return 2
    print("PROOF: OK")
    return 0


def live_refund_proof(db: Session, settings: Settings, email: str) -> int:
    """Section 6's proof after the first refund. Read-only. Returns the exit code (0 or 1)."""
    user = find_user(db, email)
    check = Checks()
    refund = db.scalars(
        select(Refund)
        .join(Payment, Payment.id == Refund.payment_id)
        .where(
            Payment.payer_user_id == user.id,
            Payment.kind == "plan",
            cutover.live_only(Payment.created_at, settings),
        )
        .order_by(Refund.id.desc())
    ).first()
    check("a refund of a live plan payment exists for this account", refund is not None)
    if refund is None:
        print("REFUND PROOF: FAILED")
        return 1
    payment = db.get(Payment, refund.payment_id)
    assert payment is not None  # the foreign key
    check("the reason is first_plan_7d", refund.reason == "first_plan_7d", refund.reason)
    check("the refund is submitted", refund.status == "submitted", refund.status)
    check(
        "the provider's refund id is present",
        bool(refund.provider_refund_id),
        "present" if refund.provider_refund_id else "missing",
    )
    check(
        "the refund is the whole payment",
        refund.amount_minor == payment.amount_minor and refund.currency == payment.currency,
        f"{money(refund.amount_minor, refund.currency)} of "
        f"{money(payment.amount_minor, payment.currency)}",
    )
    check("the payment is refunded", payment.status == "refunded", payment.status)
    sub = db.get(Subscription, payment.subscription_id) if payment.subscription_id else None
    check(
        "the subscription is ended",
        sub is not None and sub.status == "ended",
        sub.status if sub else "none",
    )
    grant = db.get(HourGrant, refund.grant_id) if refund.grant_id else None
    check(
        "the hours the payment bought were ended",
        grant is not None
        and grant.expires_at is not None
        and refund.grant_expires_before is not None
        and grant.expires_at < refund.grant_expires_before,
        f"{refund.seconds_removed / 3600:.2f} h removed",
    )
    saved = db.scalar(
        select(func.count())
        .select_from(PaymentMethod)
        .where(PaymentMethod.user_id == user.id, PaymentMethod.provider == payment.provider)
    )
    check("the saved card is deleted", not saved, must=False)
    print("  [note] the money reaching the card is proved by the bank statement, not by the app")
    print("REFUND PROOF: OK" if check.ok else "REFUND PROOF: FAILED")
    return 0 if check.ok else 1


def live_check(db: Session, settings: Settings) -> bool:
    """What must be true of a live system, and the webhook inbox's health (sections 4 and 8).
    Read-only. A key is judged by its PREFIX and never printed; no email is printed."""
    now = datetime.now(UTC)
    check = Checks()
    check("BILLING_ENABLED is true", settings.billing_enabled)
    check("BILLING_LIVE is true", settings.billing_live)
    check(
        "the Paystack secret key is a live key",
        prefix_of(settings.paystack_secret_key, "sk_live_") == "sk_live_",
        prefix_of(settings.paystack_secret_key, "sk_live_", "sk_test_"),
    )
    check(
        "the Paystack public key is a live key",
        prefix_of(settings.paystack_public_key, "pk_live_") == "pk_live_",
        prefix_of(settings.paystack_public_key, "pk_live_", "pk_test_"),
        must=False,
    )
    flw = settings.flutterwave_secret_key.strip()
    check(
        "the Flutterwave secret key is set and is not a test key",
        bool(flw) and "_TEST" not in flw,
        prefix_of(flw, "FLWSECK-"),
    )
    check(
        "the Flutterwave public key is not a test key",
        bool(settings.flutterwave_public_key.strip())
        and "_TEST" not in settings.flutterwave_public_key,
        prefix_of(settings.flutterwave_public_key, "FLWPUBK-"),
        must=False,
    )
    check("FLUTTERWAVE_SECRET_HASH is set", bool(settings.flutterwave_secret_hash.strip()))
    paystack_secret = settings.paystack_secret_key.strip()
    webhook = settings.paystack_webhook_secret.strip()
    if not webhook:
        check(
            "PAYSTACK_WEBHOOK_SECRET is unset: the classic x-paystack-signature check, keyed with "
            "the secret key (`app/billing/paystack.py`)",
            True,
        )
    else:
        check(
            "PAYSTACK_WEBHOOK_SECRET is set and is not the secret key",
            webhook != paystack_secret,
            "the timestamped Paystack-Signature scheme is used: the dashboard must send that header"
            if webhook != paystack_secret
            else "it equals the secret key: delete the line to use the classic check",
        )
    try:
        since = cutover.since(settings)
    except ValueError:
        since = None
    check(
        "BILLING_LIVE_SINCE is set and has passed",
        since is not None and since <= now,
        since.isoformat() if since else "missing",
    )
    for label, value in (
        ("BILLING_SELLER_NAME", settings.billing_seller_name),
        ("BILLING_SELLER_ADDRESS", settings.billing_seller_address),
        ("BILLING_SELLER_REGISTRATION_NO", settings.billing_seller_registration_no),
    ):
        check(f"{label} is set", bool(value.strip()) and "PLACEHOLDER" not in value.upper())
    check(
        "BILLING_SELLER_TAX_ID",
        bool(settings.billing_seller_tax_id.strip()),
        "set" if settings.billing_seller_tax_id.strip() else "empty: no tax line on invoices",
        must=False,
    )
    print(
        f"  [note] BILLING_VAT_RATE_BP: {settings.billing_vat_rate_bp} (VAT included in the price)"
    )
    prices = db.execute(
        select(Plan.code, Plan.monthly_seconds, PlanPrice.currency, PlanPrice.amount_minor)
        .join(PlanPrice, PlanPrice.plan_code == Plan.code)
        .where(
            Plan.active.is_(True),
            PlanPrice.active_from <= now,
            or_(PlanPrice.active_until.is_(None), PlanPrice.active_until > now),
        )
        .order_by(Plan.sort_order, Plan.code, PlanPrice.currency)
    ).all()
    check("an active plan has a price", bool(prices))
    for code, seconds, currency, amount in prices:
        print(f"  [note] plan {code}: {seconds / 3600:g} h, {money(amount, currency)}")
    for currency in ("USD", "NGN"):
        check(f"a price in {currency}", any(row[2] == currency for row in prices))
    counters = db.scalars(
        select(InvoiceCounter).where(InvoiceCounter.year > 0).order_by(InvoiceCounter.year)
    ).all()
    print(
        "  [note] live invoice counters: "
        + (
            ", ".join(f"{c.year} up to {c.last_number}" for c in counters)
            if counters
            else "none used yet (the first real payment will be BN-<year>-000001)"
        )
    )
    # Section 8: the webhook inbox and the payments, from the cutover on.
    stale = now - timedelta(minutes=5)
    unsigned = db.scalar(
        select(func.count())
        .select_from(BillingEvent)
        .where(
            BillingEvent.signature_ok.is_(False),
            cutover.live_only(BillingEvent.received_at, settings),
        )
    )
    undecided = db.scalar(
        select(func.count())
        .select_from(BillingEvent)
        .where(
            BillingEvent.processed_at.is_(None),
            BillingEvent.received_at < stale,
            cutover.live_only(BillingEvent.received_at, settings),
        )
    )
    check("no stored webhook has signature_ok false", unsigned == 0, f"{unsigned} found")
    check(
        "no webhook has waited more than 5 minutes to be decided",
        undecided == 0,
        f"{undecided} found",
    )
    pending = db.scalar(
        select(func.count())
        .select_from(Payment)
        .where(
            Payment.status == "pending",
            Payment.created_at < stale,
            cutover.live_only(Payment.created_at, settings),
        )
    )
    check(
        "no payment has been pending more than 5 minutes",
        pending == 0,
        f"{pending} found"
        + (": an abandoned checkout, or a webhook that did not arrive" if pending else ""),
        must=False,
    )
    unused = db.scalar(
        select(func.count())
        .select_from(Payment)
        .where(
            Payment.needs_refund_reason.is_not(None),
            cutover.live_only(Payment.created_at, settings),
        )
    )
    check("no payment waits for a person to refund it", unused == 0, f"{unused} found", must=False)
    return check.ok


READ_ONLY_COMMANDS = ("proof", "state", "preflight", "live-check")
WRITE_COMMANDS = ("checkout", "make-due")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("checkout")
    one.add_argument("email")
    one.add_argument("--plan", default="plan_20h")
    two = sub.add_parser("proof")
    two.add_argument("email")
    two.add_argument("--refund", action="store_true", help="prove the first refund (section 6)")
    pre = sub.add_parser("preflight")
    pre.add_argument("step", type=int)
    st = sub.add_parser("state")
    st.add_argument("email")
    sub.add_parser("live-check")
    three = sub.add_parser("make-due")
    three.add_argument("email")
    three.add_argument("--yes", action="store_true")
    return parser


def run(argv: list[str], settings: Settings, db: Session) -> int:
    """One command, and its exit code. The commands that write are refused first and by `guard`
    (and so with live billing); every other command runs inside `read_only`."""
    args = build_parser().parse_args(argv)
    if args.command in WRITE_COMMANDS:
        if args.command == "checkout":
            checkout(db, settings, args.email, args.plan)
        else:
            if not args.yes:
                raise SystemExit("this changes data: add --yes (test accounts only)")
            make_due(db, settings, args.email)
        return 0
    assert args.command in READ_ONLY_COMMANDS
    with read_only(db):
        if args.command == "live-check":
            print("live-check:")
            passed = live_check(db, settings)
            print("PREFLIGHT OK" if passed else "PREFLIGHT FAILED")
            return 0 if passed else 1
        if args.command == "preflight":
            if settings.billing_live:
                raise SystemExit(
                    "BILLING_LIVE is true: `preflight` is for test keys, use live-check"
                )
            print(f"preflight, step {args.step}:")
            passed = preflight(db, settings, args.step)
            print("PREFLIGHT OK" if passed else "PREFLIGHT FAILED")
            return 0 if passed else 1
        if args.command == "state":
            state(db, settings, args.email)
            return 0
        if args.refund:
            print("refund proof:")
            return live_refund_proof(db, settings, args.email)
        print("proof:")
        if settings.billing_live:
            return live_proof(db, settings, args.email)
        passed = proof(db, settings, args.email)
        print("PROOF: OK" if passed else "PROOF: FAILED")
        return 0 if passed else 1


def main() -> None:
    settings = get_settings()
    with SessionLocal() as db:
        sys.exit(run(sys.argv[1:], settings, db))


if __name__ == "__main__":
    main()
