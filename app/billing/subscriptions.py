"""Subscriptions: buying a plan or a pack, what a verified payment does, cancel and change plan
(billing spec sections 3 and 5; stage S8a).

**Nothing here grants hours from a charge answer or a redirect.** The only road to an hour grant is
`on_payment_succeeded`, which `settle.py` runs once, inside the transaction that marks a payment
`succeeded`, and only after the payment was re-read from the provider and matched the reference, the
currency and at least the amount we asked for (`settle._mark_paid`). `save_card` runs just before it
and keeps the reusable card token, encrypted, for renewals.

**Checkout** (`start_plan_checkout`, `start_pack_checkout`, `renewals.pay_link`): the payment row is
made and COMMITTED before the person is sent to the provider (a fast webhook must find it), and the
route is the one the person's billing country picks (`routing.provider_for`) for a first plan, and
the route the subscription was SOLD on for everything after (a pack, an upgrade, a renewal) until a
country change that crossed routes has taken effect.

**What a verified payment does** (`on_payment_succeeded`, by `payments.kind`):

| kind | effect |
|---|---|
| `plan` | a new live subscription from the payment date, its period's hours granted |
| `renewal` | `states.TABLE`'s `payment_succeeded` row: a NEW period and its hours |
| `upgrade` | the new plan now, and the extra hours for the rest of the period (spec 3.4) |
| `pack` | a pack grant, valid `valid_months` from the payment |

A renewal's period starts at the due moment when it was paid within an hour of it (no drift, no
gap) and at the payment date when it was paid later (the grace, or after a lapse), spec 3.3.

A payment whose period was already settled (`payments.cycle_end` is not the subscription's
`current_period_end`: two payments for one cycle, say a saved-card charge whose answer was lost and
the pay link), one that arrives for a cancelled or ended subscription, or a plan payment when the
person already has a live one, grants nothing and raises an admin alert naming the payment: the
money arrived, a human refunds it (spec 3.5, "charged twice"). Refunding itself is stage S9.

**Cancel** stops the renewal and keeps access to the period's end; **resume** takes it back; a
**downgrade** is stored as `pending_plan_code` and applied by the renewal; an **upgrade** is a
hosted checkout for the prorated difference. In the grace none of upgrade, downgrade or resume is
possible (`states.TABLE` has no such row); cancel stops the retries.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import alerts, usage, vault
from app.billing import plans, routing, settle, states
from app.billing import profile as profiles
from app.billing.payments import new_payment
from app.billing.provider import PaymentProvider, provider_named
from app.billing.types import BillingError, ProviderError, ProviderTransaction
from app.config import Settings
from app.models import (
    LIVE_SUBSCRIPTION_STATUSES,
    BillingCountryChange,
    HourGrant,
    Payment,
    PaymentMethod,
    Plan,
    PlanPrice,
    Subscription,
    User,
)

logger = logging.getLogger(__name__)

# A renewal paid within this long after the due moment still starts its period AT the due moment
# (no drift, no gap): the saved-card charge is made a few seconds after it, by a scheduler pass.
ON_TIME = timedelta(hours=1)
# Payments of these kinds replace the person's saved card with the one that paid.
CARD_SAVING_KINDS = ("plan", "renewal", "upgrade")


class Refused(BillingError):
    """A request the rules refuse. `status`, `code` and `message` are what the API says."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass(frozen=True)
class Started:
    """A checkout that was started: our payment row and where to send the person."""

    payment: Payment
    url: str


def return_url(settings: Settings) -> str:
    """Where the provider sends the payer back. The page only shows the payment's state; the grant
    is never made there."""
    return settings.billing_return_url or f"{settings.public_base_url.rstrip('/')}/billing/return"


def money(amount_minor: int, currency: str) -> str:
    """`USD 18.00`, `NGN 1,500.00`: plain text for a mail (no symbol a mail client may garble)."""
    return f"{currency} {amount_minor / 100:,.2f}"


# ---- reading ----


def live_row(db: Session, user_id: int, *, lock: bool = False) -> Subscription | None:
    """The person's live subscription row (`active` or `past_due`), optionally locked."""
    query = select(Subscription).where(
        Subscription.user_id == user_id, Subscription.status.in_(LIVE_SUBSCRIPTION_STATUSES)
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return db.scalars(query).first()


def latest_row(db: Session, user_id: int) -> Subscription | None:
    """The person's live subscription, else the newest one that ended (history)."""
    live = live_row(db, user_id)
    if live is not None:
        return live
    return db.scalars(
        select(Subscription).where(Subscription.user_id == user_id).order_by(Subscription.id.desc())
    ).first()


def snapshot(sub: Subscription) -> states.Snapshot:
    return states.Snapshot(
        status=sub.status,
        current_period_end=sub.current_period_end,
        cancel_at_period_end=sub.cancel_at_period_end,
        grace_ends_at=sub.grace_ends_at,
        next_charge_at=sub.next_charge_at,
        retry_count=sub.retry_count,
    )


def sold_route(sub: Subscription) -> routing.Route:
    return routing.Route(sub.currency, sub.provider)


def person_route(db: Session, user: User, settings: Settings, now: datetime) -> routing.Route:
    """The route the person's billing country pays with right now. Raises a `routing.RoutingError`
    (`billing_country_missing`, `billing_country_invalid`, `billing_country_unsupported`) when no
    country is chosen or none can be routed: nothing is guessed."""
    view = profiles.country_view(profiles.get_profile(db, user), now)
    return routing.provider_for(view.billing_country, settings.billing_country_routes)


# ---- checkout ----


def _adapter(route_provider: str, settings: Settings) -> PaymentProvider:
    adapter = provider_named(route_provider, settings)
    if adapter is None:
        raise Refused(503, "provider_unavailable", "Payments are not available right now.")
    return adapter


def send_to_provider(
    db: Session, adapter: PaymentProvider, payment: Payment, payer: User, settings: Settings
) -> Started:
    """Commit the payment row, THEN ask the provider for the hosted checkout (a fast webhook finds
    the row), keep the link on the payment and commit again. A provider that fails leaves the
    payment `failed` (nothing was bought) and the person is told to try again."""
    db.commit()
    try:
        checkout = adapter.start_checkout(payment, payer, return_url(settings))
    except ProviderError:
        logger.warning("checkout for payment %s could not be started", payment.id)
        payment.status = "failed"
        db.commit()
        raise Refused(
            502, "provider_error", "The payment page could not be opened. Try again in a moment."
        ) from None
    payment.pay_url = checkout.url
    db.commit()
    return Started(payment, checkout.url)


def start_plan_checkout(
    db: Session, user: User, plan_code: str, settings: Settings, now: datetime
) -> Started:
    """Checkout for a first plan, on the route the person's billing country picks."""
    route = person_route(db, user, settings, now)
    plan = plans.get_plan(db, plan_code)
    if plan is None or not plan.active:
        raise Refused(404, "plan_not_found", "That plan does not exist.")
    if live_row(db, user.id) is not None:
        raise Refused(409, "already_subscribed", "You already have a plan.")
    price = plans.current_price(db, plan.code, route.currency, now)
    if price is None:
        raise Refused(
            422,
            "price_not_available",
            f"That plan is not on sale in {route.currency} yet.",
        )
    # A double click or a second tab asks for the same plan again: the first page, not a second
    # payment that could be paid too (which would be a refund by hand). Only an unpaid checkout of
    # this price, made in the last day.
    open_one = db.scalars(
        select(Payment)
        .where(
            Payment.payer_user_id == user.id,
            Payment.kind == "plan",
            Payment.status == "pending",
            Payment.plan_price_id == price.id,
            Payment.pay_url.is_not(None),
            Payment.created_at > now - timedelta(days=1),
        )
        .order_by(Payment.id.desc())
    ).first()
    if open_one is not None:
        return Started(open_one, open_one.pay_url)
    adapter = _adapter(route.provider, settings)
    payment = new_payment(
        db,
        payer=user,
        provider=route.provider,
        kind="plan",
        currency=route.currency,
        amount_minor=price.amount_minor,
    )
    payment.plan_price_id = price.id
    payment.created_at = now
    return send_to_provider(db, adapter, payment, user, settings)


def start_pack_checkout(
    db: Session, user: User, product_code: str, settings: Settings, now: datetime
) -> Started:
    """Checkout for a pack of hours. Only with a live plan (spec 4.3); a plan in its grace counts,
    because a pack is a payment that succeeds. It is paid on the route the subscription was sold
    on, so an unfinished country change never splits one person across two providers."""
    sub = live_row(db, user.id)
    if sub is None:
        raise Refused(409, "plan_required", "You need a plan to buy extra hours.")
    product = plans.get_product(db, product_code, sub.currency)
    if product is None:
        raise Refused(404, "product_not_found", "That pack is not on sale in your currency.")
    adapter = _adapter(sub.provider, settings)
    payment = new_payment(
        db,
        payer=user,
        provider=sub.provider,
        kind="pack",
        currency=sub.currency,
        amount_minor=product.amount_minor,
        subscription_id=sub.id,
    )
    payment.product_id = product.id
    payment.created_at = now
    return send_to_provider(db, adapter, payment, user, settings)


# ---- what a verified payment does ----


def save_card(db: Session, payment: Payment, tx: ProviderTransaction) -> None:
    """Keep the reusable card token a verified payment carried, encrypted, replacing the person's
    earlier card with this provider. Best effort: it runs in its own savepoint and a failure (no
    vault key, say) is logged by type and told to the admins, never allowed to stop the hours the
    person paid for; the renewal then has no card and falls back to the pay link."""
    if payment.kind not in CARD_SAVING_KINDS or not tx.card_token or payment.payer_user_id is None:
        return
    try:
        with db.begin_nested():
            payer = db.get(User, payment.payer_user_id)
            blob, version = vault.encrypt(
                tx.card_token, purpose="payment_method", owner=payment.payer_user_id
            )
            db.execute(
                delete(PaymentMethod).where(
                    PaymentMethod.user_id == payment.payer_user_id,
                    PaymentMethod.provider == payment.provider,
                )
            )
            db.add(
                PaymentMethod(
                    user_id=payment.payer_user_id,
                    provider=payment.provider,
                    token_enc=blob,
                    key_version=version,
                    email_at_provider=(tx.payer_email or (payer.email if payer else None) or "")[
                        :254
                    ]
                    or None,
                )
            )
            db.flush()
    except Exception as exc:
        logger.error("saving the card of payment %s failed: %s", payment.id, type(exc).__name__)
        _tell_admins(
            db,
            f"admin.billing_card_not_saved:{payment.id}",
            "A paid card could not be saved for renewals",
            f"Payment {payment.id} succeeded but its card could not be kept "
            f"({type(exc).__name__}). "
            "The person's renewals will use the pay link until they pay again.",
        )


def saved_card(db: Session, user_id: int, provider: str) -> tuple[str, str | None] | None:
    """The person's saved token and the email the provider saw, or None (no card, or one that
    cannot be opened: the renewal then falls back to the pay link)."""
    method = db.scalars(
        select(PaymentMethod)
        .where(PaymentMethod.user_id == user_id, PaymentMethod.provider == provider)
        .order_by(PaymentMethod.id.desc())
    ).first()
    if method is None:
        return None
    try:
        secret = vault.decrypt(
            method.token_enc, method.key_version, purpose="payment_method", owner=user_id
        )
    except vault.VaultError:
        logger.error("a saved card of user %s could not be opened", user_id)
        return None
    return secret.reveal(), method.email_at_provider


def _tell_admins(db: Session, key: str, title: str, body: str) -> None:
    """One admin alert per key, by ids only (no emails). Best effort: it must not stop the money."""
    try:
        with db.begin_nested():
            alerts.notify_admins(db, kind=key.split(":")[0], title=title, body=body, dedupe_key=key)
    except Exception as exc:
        logger.error("admin alert %s failed: %s", key.split(":")[0], type(exc).__name__)


def needs_refund(db: Session, payment: Payment, why: str) -> None:
    """Money arrived that no subscription period can use: tell the admins, grant nothing."""
    logger.warning("payment %s succeeded but grants nothing: %s", payment.id, why)
    _tell_admins(
        db,
        f"admin.billing_refund_needed:{payment.id}",
        "A payment needs a refund or a look",
        f"Payment {payment.id} ({payment.kind}, {money(payment.amount_minor, payment.currency)}) "
        f"succeeded but nothing can use it: {why}. Refund it in the provider's dashboard.",
    )


def _receipt(db: Session, payment: Payment, what: str) -> None:
    if payment.payer_user_id is None:
        return
    alerts.notify_user(
        db,
        payment.payer_user_id,
        kind="billing.receipt",
        severity="info",
        title="Payment received",
        body=f"We received your payment of {money(payment.amount_minor, payment.currency)} "
        f"for {what}. Thank you.",
        dedupe_key=f"billing.receipt:{payment.id}",
        email_template=alerts.ALERT_TEMPLATE,
    )


def _grant(
    db: Session,
    user: User,
    *,
    kind: str,
    seconds: int,
    starts_at: datetime,
    expires_at: datetime | None,
    payment: Payment,
    subscription_id: int | None,
) -> bool:
    """One hour grant for one payment. The unique key (kind, source payment) makes a replay a
    no-op: returns False when the grant was already there."""
    try:
        with db.begin_nested():
            db.add(
                HourGrant(
                    user_id=user.id,
                    email_hash=usage.email_hash(user.email),
                    kind=kind,
                    seconds=seconds,
                    starts_at=starts_at,
                    expires_at=expires_at,
                    source_payment_id=payment.id,
                    subscription_id=subscription_id,
                )
            )
            db.flush()
    except IntegrityError:
        return False
    return True


def on_payment_succeeded(db: Session, payment: Payment) -> None:
    """The grant handler `settle` runs once for each payment that first becomes `succeeded`."""
    if payment.kind == "plan":
        _plan_paid(db, payment)
    elif payment.kind == "renewal":
        _renewal_paid(db, payment)
    elif payment.kind == "upgrade":
        _upgrade_paid(db, payment)
    elif payment.kind == "pack":
        _pack_paid(db, payment)


def _plan_paid(db: Session, payment: Payment) -> None:
    price = db.get(PlanPrice, payment.plan_price_id) if payment.plan_price_id else None
    payer = db.get(User, payment.payer_user_id) if payment.payer_user_id else None
    if price is None or payer is None or payer.status != "active":
        # (a checkout paid after the account was deleted makes no plan for a ghost)
        return needs_refund(db, payment, "the plan is gone or the account is not active")
    # The checkout was opened under a billing country and priced in its currency. If the country was
    # changed afterwards the open checkouts were closed (`profiles.supersede_open_plan_checkouts`),
    # so this page was paid after being closed: a plan sold on a route the profile no longer shows
    # would renew on the wrong route with no warning. Take no plan; a person refunds it.
    changed_since = db.scalar(
        select(BillingCountryChange.id)
        .where(
            BillingCountryChange.user_id == payer.id,
            BillingCountryChange.created_at > payment.created_at,
        )
        .limit(1)
    )
    if changed_since is not None:
        return needs_refund(
            db, payment, "the billing country was changed after this checkout was opened"
        )
    plan = db.get(Plan, price.plan_code)
    start = payment.paid_at
    sub = Subscription(
        user_id=payer.id,
        payer_user_id=payer.id,
        plan_code=plan.code,
        price_id=price.id,
        status=states.ACTIVE,
        currency=payment.currency,
        provider=payment.provider,
        current_period_start=start,
        current_period_end=plans.add_months(start),
    )
    try:
        with db.begin_nested():
            db.add(sub)
            db.flush()
    except IntegrityError:  # the partial unique index: this person already has a live plan
        return needs_refund(db, payment, "the person already has a live plan")
    payment.subscription_id = sub.id
    _grant(
        db,
        payer,
        kind="plan_period",
        seconds=plan.monthly_seconds,
        starts_at=start,
        expires_at=sub.current_period_end,
        payment=payment,
        subscription_id=sub.id,
    )
    _receipt(db, payment, f"{plan.name}")


def _locked_subscription(db: Session, payment: Payment) -> Subscription | None:
    if payment.subscription_id is None:
        return None
    return db.scalars(
        select(Subscription)
        .where(Subscription.id == payment.subscription_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()


def _renewal_paid(db: Session, payment: Payment) -> None:
    sub = _locked_subscription(db, payment)
    price = db.get(PlanPrice, payment.plan_price_id) if payment.plan_price_id else None
    if sub is None or price is None or sub.user_id is None:
        return needs_refund(db, payment, "the subscription is gone")
    current = payment.cycle_end == sub.current_period_end
    transition = states.apply(
        sub.status, states.PAYMENT_SUCCEEDED if current else states.PAYMENT_STALE
    )
    if transition is None or states.NEEDS_REFUND in transition.effects:
        return needs_refund(
            db, payment, f"its period was already settled or the plan is {sub.status}"
        )
    user = db.get(User, sub.user_id)
    paid, due = payment.paid_at, payment.cycle_end
    start = due if paid <= due + ON_TIME else paid
    try:
        with db.begin_nested():
            if states.EXPIRE_LEFTOVER in transition.effects:
                end_leftover(db, sub, paid)
            plan = db.get(Plan, price.plan_code)
            sub.plan_code, sub.price_id = plan.code, price.id
            if sub.pending_plan_code == plan.code:
                sub.pending_plan_code = None
            sub.provider, sub.currency = payment.provider, payment.currency  # a route move
            sub.status = transition.status
            sub.current_period_start = start
            sub.current_period_end = plans.add_months(start)
            sub.grace_ends_at = sub.next_charge_at = None
            sub.retry_count = 0
            sub.updated_at = paid
            db.flush()
    except IntegrityError:  # reviving a lapsed one beside a live plan the person bought since
        db.refresh(sub)
        return needs_refund(db, payment, "the person has another live plan now")
    payment.subscription_id = sub.id
    _grant(
        db,
        user,
        kind="plan_period",
        seconds=plan.monthly_seconds,
        starts_at=start,
        expires_at=sub.current_period_end,
        payment=payment,
        subscription_id=sub.id,
    )
    _receipt(db, payment, f"{plan.name}")


def end_leftover(db: Session, sub: Subscription, at: datetime) -> None:
    """The subscription's plan hours that are still in force end at `at` (no rollover). A grace
    had extended them to its end; a new period, or the lapse, ends them now."""
    db.execute(
        update(HourGrant)
        .where(
            HourGrant.subscription_id == sub.id,
            HourGrant.kind == "plan_period",
            HourGrant.starts_at <= at,
            HourGrant.expires_at > at,
        )
        .values(expires_at=at)
    )


def _upgrade_paid(db: Session, payment: Payment) -> None:
    sub = _locked_subscription(db, payment)
    price = db.get(PlanPrice, payment.plan_price_id) if payment.plan_price_id else None
    if (
        sub is None
        or price is None
        or sub.user_id is None
        or sub.status != states.ACTIVE
        or payment.cycle_end != sub.current_period_end
        # It must land INSIDE the period it was for, judged by the clock at the payment: paid at
        # or after the due moment, before the scheduler pass has run (or while the renewal's
        # outcome is unknown), the stored row still says `active` but the extra hours would end
        # before they began and a late old-price renewal would flip the plan back.
        or state_now(sub, payment.paid_at) != states.ACTIVE
    ):
        return needs_refund(db, payment, "the period it was for has moved on")
    user = db.get(User, sub.user_id)
    old, new = db.get(Plan, sub.plan_code), db.get(Plan, price.plan_code)
    old_price = db.get(PlanPrice, sub.price_id)
    try:
        # The hours are the proration of the moment the person asked (the payment was made then).
        extra = plans.prorate_upgrade(
            old_amount_minor=old_price.amount_minor,
            new_amount_minor=price.amount_minor,
            old_seconds=old.monthly_seconds,
            new_seconds=new.monthly_seconds,
            period_start=sub.current_period_start,
            period_end=sub.current_period_end,
            now=payment.created_at,
        ).extra_seconds
    except plans.NoUpgrade as exc:
        return needs_refund(db, payment, exc.message)
    sub.plan_code, sub.price_id, sub.pending_plan_code = new.code, price.id, None
    sub.updated_at = payment.paid_at
    if extra > 0:
        _grant(
            db,
            user,
            kind="plan_period",
            seconds=extra,
            starts_at=payment.paid_at,
            expires_at=sub.current_period_end,
            payment=payment,
            subscription_id=sub.id,
        )
    _receipt(db, payment, f"the change to {new.name}")


def _pack_paid(db: Session, payment: Payment) -> None:
    from app.models import Product

    product = db.get(Product, payment.product_id) if payment.product_id else None
    payer = db.get(User, payment.payer_user_id) if payment.payer_user_id else None
    if product is None or payer is None or payer.status != "active":
        return needs_refund(db, payment, "the pack is gone or the account is not active")
    _grant(
        db,
        payer,
        kind="pack",
        seconds=product.seconds,
        starts_at=payment.paid_at,
        expires_at=plans.add_months(payment.paid_at, product.valid_months),
        payment=payment,
        subscription_id=payment.subscription_id,
    )
    _receipt(db, payment, product.name)


def register() -> None:
    """Put the two handlers where `settle` runs them. Idempotent: importing this module twice (the
    app and the scheduler both do) registers each once."""
    if save_card not in settle.TRANSACTION_HANDLERS:
        settle.TRANSACTION_HANDLERS.append(save_card)
    if on_payment_succeeded not in settle.GRANT_HANDLERS:
        settle.GRANT_HANDLERS.append(on_payment_succeeded)


register()


# ---- cancel, resume, change plan ----


def _locked_live(db: Session, user: User) -> Subscription:
    sub = live_row(db, user.id, lock=True)
    if sub is None:
        raise Refused(409, "no_subscription", "You do not have a plan.")
    return sub


def state_now(sub: Subscription, now: datetime) -> str:
    """The subscription's status judged by the clock, as `entitlements` and the API do: what the
    rules below are applied to, so a plan whose renewal pass has not run yet (the scheduler was
    down) is already in its grace, or over, for a person pressing a button."""
    return states.effective_status(snapshot(sub), now)


def cancel(db: Session, user: User, now: datetime) -> Subscription:
    """Stop the renewal. Access, and the hours, last to the end of the paid period; nothing is
    refunded (spec 3.4). In the grace it also stops the retries. Flushed, not committed."""
    sub = _locked_live(db, user)
    if sub.cancel_at_period_end:
        return sub
    transition = states.apply(state_now(sub, now), states.CANCEL)
    if transition is None:
        raise Refused(409, "no_subscription", "You do not have a plan.")
    sub.cancel_at_period_end = True
    sub.canceled_at = now
    if states.STOP_ATTEMPTS in transition.effects:
        sub.next_charge_at = None
    sub.updated_at = now
    db.flush()
    return sub


def resume(db: Session, user: User, now: datetime) -> Subscription:
    """Take a cancel back, while the paid period is still running. Refused in the grace."""
    sub = _locked_live(db, user)
    if not sub.cancel_at_period_end:
        raise Refused(409, "not_canceled", "Your plan is not set to end.")
    now_state = state_now(sub, now)
    if now_state not in states.LIVE:  # the period it would be resumed for is already over
        raise Refused(409, "no_subscription", "You do not have a plan.")
    if states.apply(now_state, states.RESUME) is None:
        raise Refused(
            409, "payment_overdue", "Pay what is overdue first; a plan in grace cannot be resumed."
        )
    sub.cancel_at_period_end = False
    sub.canceled_at = None
    sub.updated_at = now
    db.flush()
    return sub


@dataclass(frozen=True)
class PlanChange:
    result: str  # `checkout` (an upgrade to pay), `scheduled` (a downgrade at renewal), `cleared`
    subscription: Subscription
    started: Started | None = None


def _classify_change(
    db: Session, sub: Subscription, plan_code: str, now: datetime
) -> tuple[str, Plan, Plan, PlanPrice | None]:
    """What asking for `plan_code` would be: `(kind, target, current, new price)` where `kind` is
    `upgrade`, `downgrade` or `clear` (a waiting downgrade taken back; no price then). Raises
    `Refused` for a plan that does not exist, the plan already held, a plan not on sale in the
    subscription's currency, and (409 `payment_overdue`) any change in the grace. Reads only."""
    target = plans.get_plan(db, plan_code)
    if target is None or not target.active:
        raise Refused(404, "plan_not_found", "That plan does not exist.")
    current = db.get(Plan, sub.plan_code)
    if target.code == sub.plan_code and sub.pending_plan_code is None:
        raise Refused(409, "same_plan", "You are already on that plan.")
    upgrade = target.monthly_seconds > current.monthly_seconds
    event = states.UPGRADE if upgrade else states.DOWNGRADE
    if states.apply(state_now(sub, now), event) is None:
        raise Refused(
            409, "payment_overdue", "A plan cannot be changed while a payment is overdue."
        )
    if target.code == sub.plan_code:
        return "clear", target, current, None
    new_price = plans.current_price(db, target.code, sub.currency, now)
    if new_price is None:
        raise Refused(
            422, "price_not_available", f"That plan is not on sale in {sub.currency} yet."
        )
    return ("upgrade" if upgrade else "downgrade"), target, current, new_price


def _prorate(
    db: Session, sub: Subscription, target: Plan, current: Plan, new_price: PlanPrice, now: datetime
) -> plans.Proration:
    old_price = db.get(PlanPrice, sub.price_id)
    try:
        return plans.prorate_upgrade(
            old_amount_minor=old_price.amount_minor,
            new_amount_minor=new_price.amount_minor,
            old_seconds=current.monthly_seconds,
            new_seconds=target.monthly_seconds,
            period_start=sub.current_period_start,
            period_end=sub.current_period_end,
            now=now,
        )
    except plans.NoUpgrade as exc:
        raise Refused(409, exc.code, exc.message) from None


@dataclass(frozen=True)
class PlanChangePreview:
    kind: str  # `upgrade`, `downgrade` or `clear`
    plan_code: str
    currency: str
    # upgrade: what is charged now. downgrade: what the renewal will charge. clear: null.
    amount_minor: int | None
    # upgrade: the hours added to this period. Otherwise null.
    extra_seconds: int | None
    takes_effect: str  # `now` or `next_renewal`


def preview_plan_change(
    db: Session, user: User, plan_code: str, now: datetime
) -> PlanChangePreview:
    """What `change_plan` would do and cost, so a screen can say it BEFORE the person commits to a
    checkout. Reads only; the same refusals as `change_plan`, for the same reasons."""
    sub = live_row(db, user.id)
    if sub is None:
        raise Refused(409, "no_subscription", "You do not have a plan.")
    kind, target, current, new_price = _classify_change(db, sub, plan_code, now)
    if kind == "clear":
        return PlanChangePreview("clear", target.code, sub.currency, None, None, "now")
    if kind == "downgrade":
        return PlanChangePreview(
            "downgrade", target.code, sub.currency, new_price.amount_minor, None, "next_renewal"
        )
    proration = _prorate(db, sub, target, current, new_price, now)
    return PlanChangePreview(
        "upgrade", target.code, sub.currency, proration.amount_minor, proration.extra_seconds, "now"
    )


def change_plan(
    db: Session, user: User, plan_code: str, settings: Settings, now: datetime
) -> PlanChange:
    """Move to another plan. **Upgrade** (a plan with more hours): a hosted checkout for the price
    difference for the days left; the plan changes, and the extra hours are granted, when that
    payment is verified. **Downgrade**: stored for the next renewal, the current period untouched.
    Choosing the plan you are on takes a waiting downgrade back. Refused in the grace (409
    `payment_overdue`). The checkout is on the route the subscription was sold on."""
    sub = _locked_live(db, user)
    kind, target, current, new_price = _classify_change(db, sub, plan_code, now)
    if kind == "clear":
        sub.pending_plan_code = None
        sub.updated_at = now
        db.flush()
        return PlanChange("cleared", sub)
    if kind == "downgrade":
        sub.pending_plan_code = target.code
        sub.updated_at = now
        db.flush()
        return PlanChange("scheduled", sub)
    proration = _prorate(db, sub, target, current, new_price, now)
    adapter = _adapter(sub.provider, settings)
    payment = new_payment(
        db,
        payer=user,
        provider=sub.provider,
        kind="upgrade",
        currency=sub.currency,
        amount_minor=proration.amount_minor,
        subscription_id=sub.id,
    )
    payment.plan_price_id = new_price.id
    payment.cycle_end = sub.current_period_end
    payment.created_at = now
    return PlanChange("checkout", sub, send_to_provider(db, adapter, payment, user, settings))


# ---- account deletion ----


def end_for_deleted_account(db: Session, user: User, now: datetime) -> None:
    """The person deleted their account: the live subscription ends, no refund of the paid period
    (the Terms say so, spec 3.7), and the saved cards are deleted. Their hours-ledger rows stay."""
    sub = live_row(db, user.id, lock=True)
    if sub is not None:
        transition = states.apply(sub.status, states.ACCOUNT_DELETED)
        sub.status = transition.status
        sub.next_charge_at = sub.grace_ends_at = None
        sub.updated_at = now
    db.execute(delete(PaymentMethod).where(PaymentMethod.user_id == user.id))
    db.flush()
