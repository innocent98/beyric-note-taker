"""The person's own subscription routes under `/api/billing/...` (billing spec 3.4 and 5; S8a).

Dark in the same two ways as the profile routes (`profile_api.billing_user`): a plain 404
(`not_found`) while `BILLING_ENABLED` is false and for any account not on `BILLING_ALLOWED_EMAILS`,
checked before anything is read. They sit under `/api/`, so `auth.guard` gives them the login and
CSRF rules of every person route. Only the caller's own subscription and payments are ever read or
written: there is no route that takes another person's id.

| Route | What it does |
|---|---|
| `GET /api/billing/plans` | what the caller can buy, priced on their billing country's route |
| `GET /api/billing/subscription` | their state, hours, subscription and any payment waiting |
| `POST /api/billing/checkout` | start a hosted checkout for a plan or a pack |
| `GET /api/billing/payments` | their payments, newest first (the billing history) |
| `GET /api/billing/payments/{reference}` | one payment's state (the return page polls it) |
| `POST /api/billing/subscription/cancel` | stop the renewal; access to the period's end |
| `POST /api/billing/subscription/resume` | take the cancel back |
| `GET /api/billing/subscription/change-plan/preview` | what a change would cost, up front |
| `POST /api/billing/subscription/change-plan` | upgrade (a checkout) or downgrade (at renewal) |
| `POST /api/billing/subscription/pay-link` | the provider's page to pay what is overdue |

The return page, and every poll, only show a payment's state read from our database. **Hours are
granted by a verified payment and nothing else** (`subscriptions.on_payment_succeeded`).

Errors, in the API's one shape (`{"error": {"code", "message"}}`): 404 `not_found` (billing off or
not allowed, or no such payment or plan or pack); 409 `already_subscribed`, `plan_required`,
`no_subscription`, `not_canceled`, `payment_overdue`, `same_plan`, `nothing_due`,
`not_an_upgrade`, `period_over`, `period_almost_over`; 422 `billing_country_missing`,
`billing_country_invalid`, `billing_country_unsupported`, `price_not_available`,
`validation_error`; 502 `provider_error`; 503 `provider_unavailable`.
"""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import entitlements, services
from app.api_schemas import Body, ErrorResponse, UtcDatetime
from app.billing import plans, renewals, routing, states, subscriptions
from app.billing.profile_api import RouteOut, billing_user
from app.db import get_session
from app.models import Payment, Plan, PlanPrice, Subscription, User

router = APIRouter(prefix="/api/billing", tags=["billing"])

_NOT_FOUND = {404: {"model": ErrorResponse, "description": "billing is off for this account"}}


class PriceOut(BaseModel):
    currency: str
    amount_minor: int  # kobo or cents, whole numbers


class PlanOut(BaseModel):
    code: str
    name: str
    monthly_seconds: int
    # The price in the caller's currency; null when the plan is not on sale in it (no price row).
    price: PriceOut | None


class PackOut(BaseModel):
    code: str
    name: str
    seconds: int
    valid_months: int
    price: PriceOut


class PlansOut(BaseModel):
    # What the caller's billing country pays with; null until they choose one. Without a route no
    # price is shown: nothing is guessed.
    route: RouteOut | None
    plans: list[PlanOut]
    packs: list[PackOut]


class SubscriptionOut(BaseModel):
    id: int
    # `active`, `past_due` (the 3 days of grace), `lapsed`, `canceled`, `ended`.
    status: str
    plan_code: str
    plan_name: str
    currency: str
    amount_minor: int  # what the next renewal costs now (the plan they will be on, today's price)
    current_period_start: UtcDatetime
    current_period_end: UtcDatetime
    cancel_at_period_end: bool
    pending_plan_code: str | None  # a downgrade waiting for the renewal
    grace_ends_at: UtcDatetime | None  # when the grace ends, while `past_due`
    next_retry_at: UtcDatetime | None  # the next time we will try the card
    can_cancel: bool
    can_resume: bool
    can_change_plan: bool  # false in the grace
    can_pay_now: bool  # true in the grace: `POST /subscription/pay-link`


class HoursOut(BaseModel):
    # What the person may still record, and what they hold in all, in seconds. Null for an admin.
    seconds_left: int | None
    holdings_seconds: int | None
    warning: str | None  # `low` (80% used), `out` (nothing left) or null


class PaymentOut(BaseModel):
    reference: str
    kind: str  # `plan`, `renewal`, `upgrade`, `pack`
    status: str  # `pending`, `succeeded`, `failed`, `refunded`, `partly_refunded`
    currency: str
    amount_minor: int
    created_at: UtcDatetime
    paid_at: UtcDatetime | None
    # The payer's own page for a payment that waits for them; null once it is paid or failed.
    pay_url: str | None


class PaymentsOut(BaseModel):
    # The caller's own payments, newest first, at most 50. `pay_url` is set only on a pending one.
    payments: list[PaymentOut]


class StateOut(BaseModel):
    # `trial`, `pilot`, `active`, `past_due`, `lapsed` or `none`: what the person can do now.
    state: str
    hours: HoursOut
    subscription: SubscriptionOut | None  # null when they have never had a plan
    pending_payment: PaymentOut | None  # a checkout or charge still waiting, newest first


class CheckoutIn(Body):
    kind: Literal["plan", "pack"]
    code: str  # the plan's or the pack's code from `GET /plans`


class CheckoutOut(BaseModel):
    payment: PaymentOut
    checkout_url: str  # send the person here; it is the provider's hosted page


class PlanChangeIn(Body):
    plan_code: str


class PlanChangeOut(BaseModel):
    # `checkout` (an upgrade: send the person to `checkout_url`), `scheduled` (a downgrade at the
    # renewal) or `cleared` (a waiting downgrade taken back).
    result: str
    subscription: SubscriptionOut
    payment: PaymentOut | None = None
    checkout_url: str | None = None


class PlanChangePreviewOut(BaseModel):
    # `upgrade` (charged now), `downgrade` (at the renewal) or `clear` (a waiting downgrade taken
    # back).
    kind: str
    plan_code: str
    currency: str
    # upgrade: what is charged now. downgrade: what the renewal will charge. clear: null.
    amount_minor: int | None
    # upgrade: the hours added to this period, in seconds. Otherwise null.
    extra_seconds: int | None
    takes_effect: str  # `now` or `next_renewal`


class PayLinkOut(BaseModel):
    payment: PaymentOut
    checkout_url: str


def _refused(exc: subscriptions.Refused) -> services.Refused:
    return services.Refused(exc.status, exc.code, exc.message)


def _payment_out(row: Payment) -> PaymentOut:
    waiting = row.status == "pending"
    return PaymentOut(
        reference=row.reference,
        kind=row.kind,
        status=row.status,
        currency=row.currency,
        amount_minor=row.amount_minor,
        created_at=row.created_at,
        paid_at=row.paid_at,
        pay_url=row.pay_url if waiting else None,
    )


def _subscription_out(db: Session, sub: Subscription, now: datetime) -> SubscriptionOut:
    plan = db.get(Plan, sub.plan_code)
    price = plans.renewal_price(db, sub, now) or db.get(PlanPrice, sub.price_id)
    # By the clock, as `entitlements` does: a subscription the scheduler has not looked at yet is
    # still shown as what time says it is, and one that is effectively over offers no buttons.
    shown = states.effective_status(subscriptions.snapshot(sub), now)
    live = shown in states.LIVE
    overdue = shown == states.PAST_DUE
    return SubscriptionOut(
        id=sub.id,
        status=shown,
        plan_code=sub.plan_code,
        plan_name=plan.name,
        currency=sub.currency,
        amount_minor=price.amount_minor,
        current_period_start=sub.current_period_start,
        current_period_end=sub.current_period_end,
        cancel_at_period_end=sub.cancel_at_period_end,
        pending_plan_code=sub.pending_plan_code,
        grace_ends_at=(sub.grace_ends_at or states.grace_end(sub.current_period_end))
        if overdue
        else None,
        next_retry_at=sub.next_charge_at if overdue and not sub.cancel_at_period_end else None,
        can_cancel=live and not sub.cancel_at_period_end,
        can_resume=live and sub.cancel_at_period_end and not overdue,
        can_change_plan=live and not overdue,
        can_pay_now=live and overdue,
    )


def _route_of(db: Session, user: User, request: Request, now: datetime) -> RouteOut | None:
    try:
        route = subscriptions.person_route(db, user, request.app.state.settings, now)
    except routing.RoutingError:
        return None
    return RouteOut(currency=route.currency, provider=route.provider)


@router.get("/plans", responses=_NOT_FOUND)
def read_plans(
    request: Request,
    country: str | None = Query(default=None),
    db: Session = Depends(get_session),
    user: User = Depends(billing_user),
) -> PlansOut:
    """What the caller can buy and what it costs on the route their billing country picks. A
    person who has a plan sees prices in the currency the plan was sold in (a pack is paid on that
    route); otherwise in their billing country's. No route, no prices.

    `?country=NG` is a PREVIEW for a person who has not confirmed a country yet (the screen
    suggests one): prices on that country's route, nothing saved. The screen saves the country
    before it starts a checkout, which always uses the saved one. Ignored for a person with a
    live plan. A country that is not one or cannot be routed is a 422, as on `GET /route`."""
    now = datetime.now(UTC)
    sub = subscriptions.live_row(db, user.id)
    if country is not None and sub is None:
        settings = request.app.state.settings
        try:
            found = routing.provider_for(
                routing.clean_country(country), settings.billing_country_routes
            )
        except routing.RoutingError as exc:
            raise services.Refused(422, exc.code, exc.message) from None
        route = RouteOut(currency=found.currency, provider=found.provider)
    else:
        route = _route_of(db, user, request, now)
    currency = sub.currency if sub is not None else (route.currency if route else None)
    shown_route = RouteOut(currency=sub.currency, provider=sub.provider) if sub else route
    if currency is None:
        return PlansOut(route=None, plans=[], packs=[])
    out_plans = []
    for plan in plans.active_plans(db):
        price = plans.current_price(db, plan.code, currency, now)
        out_plans.append(
            PlanOut(
                code=plan.code,
                name=plan.name,
                monthly_seconds=plan.monthly_seconds,
                price=PriceOut(currency=price.currency, amount_minor=price.amount_minor)
                if price
                else None,
            )
        )
    packs = [
        PackOut(
            code=product.code,
            name=product.name,
            seconds=product.seconds,
            valid_months=product.valid_months,
            price=PriceOut(currency=product.currency, amount_minor=product.amount_minor),
        )
        for product in plans.active_products(db, currency)
    ]
    return PlansOut(route=shown_route, plans=out_plans, packs=packs)


@router.get("/subscription", responses=_NOT_FOUND)
def read_subscription(
    request: Request,
    db: Session = Depends(get_session),
    user: User = Depends(billing_user),
) -> StateOut:
    """The caller's state, hours, subscription (the live one, else the newest that ended) and the
    newest payment still waiting for them. A read: it writes nothing."""
    now = datetime.now(UTC)
    settings = request.app.state.settings
    status = entitlements.status(db, user, now, settings.max_poll_hours * 3600)
    sub = subscriptions.latest_row(db, user.id)
    waiting = db.scalars(
        select(Payment)
        .where(Payment.payer_user_id == user.id, Payment.status == "pending")
        .order_by(Payment.id.desc())
    ).first()
    return StateOut(
        state="none" if status.unlimited else status.state,
        hours=HoursOut(
            seconds_left=status.seconds_left,
            holdings_seconds=status.holdings_seconds,
            warning=status.warning,
        ),
        subscription=_subscription_out(db, sub, now) if sub else None,
        pending_payment=_payment_out(waiting) if waiting else None,
    )


@router.post(
    "/checkout",
    responses={
        **_NOT_FOUND,
        409: {"model": ErrorResponse, "description": "`already_subscribed`, `plan_required`"},
        422: {
            "model": ErrorResponse,
            "description": "`billing_country_missing`, `billing_country_unsupported`, "
            "`price_not_available`",
        },
        502: {"model": ErrorResponse, "description": "`provider_error`"},
        503: {"model": ErrorResponse, "description": "`provider_unavailable`"},
    },
)
def start_checkout(
    request: Request,
    body: CheckoutIn,
    db: Session = Depends(get_session),
    user: User = Depends(billing_user),
) -> CheckoutOut:
    """Start a hosted checkout for a plan (first plan; on the route the billing country picks) or a
    pack (needs a live plan; on the route the plan was sold on). The payment row is made and
    committed first; the answer is the provider's page. Nothing is granted by this call or by the
    page the payer returns to: only the verified payment grants."""
    settings = request.app.state.settings
    now = datetime.now(UTC)
    try:
        if body.kind == "plan":
            started = subscriptions.start_plan_checkout(db, user, body.code, settings, now)
        else:
            started = subscriptions.start_pack_checkout(db, user, body.code, settings, now)
    except routing.RoutingError as exc:
        db.rollback()
        raise services.Refused(422, exc.code, exc.message) from None
    except subscriptions.Refused as exc:
        db.rollback()
        raise _refused(exc) from None
    return CheckoutOut(payment=_payment_out(started.payment), checkout_url=started.url)


@router.get("/payments", responses=_NOT_FOUND)
def list_payments(
    db: Session = Depends(get_session),
    user: User = Depends(billing_user),
) -> PaymentsOut:
    """The caller's own payments, newest first (at most 50): the billing history. A read."""
    rows = db.scalars(
        select(Payment)
        .where(Payment.payer_user_id == user.id)
        .order_by(Payment.id.desc())
        .limit(50)
    )
    return PaymentsOut(payments=[_payment_out(row) for row in rows])


@router.get("/payments/{reference}", responses=_NOT_FOUND)
def read_payment(
    reference: str,
    db: Session = Depends(get_session),
    user: User = Depends(billing_user),
) -> PaymentOut:
    """One of the caller's own payments. The page the payer returns to polls this until `status`
    is no longer `pending`. Someone else's reference is a plain 404, the same as an unknown one."""
    row = db.scalars(
        select(Payment).where(Payment.reference == reference, Payment.payer_user_id == user.id)
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "Not found.")
    return _payment_out(row)


def _own_state(db: Session, user: User, sub: Subscription, now: datetime) -> SubscriptionOut:
    return _subscription_out(db, sub, now)


@router.post(
    "/subscription/cancel",
    responses={**_NOT_FOUND, 409: {"model": ErrorResponse, "description": "`no_subscription`"}},
)
def cancel(
    db: Session = Depends(get_session), user: User = Depends(billing_user)
) -> SubscriptionOut:
    """Stop the renewal. Access, and the hours, last to the end of the paid period; nothing is
    refunded. In the grace it also stops the retries. Asking twice changes nothing."""
    now = datetime.now(UTC)
    try:
        sub = subscriptions.cancel(db, user, now)
    except subscriptions.Refused as exc:
        db.rollback()
        raise _refused(exc) from None
    out = _own_state(db, user, sub, now)
    db.commit()
    return out


@router.post(
    "/subscription/resume",
    responses={
        **_NOT_FOUND,
        409: {
            "model": ErrorResponse,
            "description": "`no_subscription`, `not_canceled`, `payment_overdue`",
        },
    },
)
def resume(
    db: Session = Depends(get_session), user: User = Depends(billing_user)
) -> SubscriptionOut:
    """Take a cancel back while the paid period still runs. Refused in the grace."""
    now = datetime.now(UTC)
    try:
        sub = subscriptions.resume(db, user, now)
    except subscriptions.Refused as exc:
        db.rollback()
        raise _refused(exc) from None
    out = _own_state(db, user, sub, now)
    db.commit()
    return out


@router.get(
    "/subscription/change-plan/preview",
    responses={
        **_NOT_FOUND,
        404: {"model": ErrorResponse, "description": "`not_found` or `plan_not_found`"},
        409: {
            "model": ErrorResponse,
            "description": "`no_subscription`, `payment_overdue`, `same_plan`, `period_over`, "
            "`period_almost_over`",
        },
        422: {"model": ErrorResponse, "description": "`price_not_available`"},
    },
)
def preview_change_plan(
    plan_code: str,
    db: Session = Depends(get_session),
    user: User = Depends(billing_user),
) -> PlanChangePreviewOut:
    """What moving to `plan_code` would cost and when it takes effect, so the screen can say it
    before the person commits (an upgrade is charged the moment they press the button). Reads
    only, with the same refusals as the change itself."""
    try:
        preview = subscriptions.preview_plan_change(db, user, plan_code, datetime.now(UTC))
    except subscriptions.Refused as exc:
        raise _refused(exc) from None
    return PlanChangePreviewOut(
        kind=preview.kind,
        plan_code=preview.plan_code,
        currency=preview.currency,
        amount_minor=preview.amount_minor,
        extra_seconds=preview.extra_seconds,
        takes_effect=preview.takes_effect,
    )


@router.post(
    "/subscription/change-plan",
    responses={
        **_NOT_FOUND,
        409: {
            "model": ErrorResponse,
            "description": "`no_subscription`, `payment_overdue`, `same_plan`, `not_an_upgrade`, "
            "`period_over`, `period_almost_over`",
        },
        422: {"model": ErrorResponse, "description": "`price_not_available`"},
    },
)
def change_plan(
    request: Request,
    body: PlanChangeIn,
    db: Session = Depends(get_session),
    user: User = Depends(billing_user),
) -> PlanChangeOut:
    """Move to another plan. A plan with more hours is an upgrade: a hosted checkout for the price
    difference for the days left (`result` `checkout`); the plan changes and the extra hours come
    when that payment is verified. A smaller plan is a downgrade stored for the next renewal
    (`scheduled`). Choosing the plan you are on takes a waiting downgrade back (`cleared`).
    Refused in the grace (`payment_overdue`)."""
    now = datetime.now(UTC)
    try:
        change = subscriptions.change_plan(
            db, user, body.plan_code, request.app.state.settings, now
        )
    except subscriptions.Refused as exc:
        db.rollback()
        raise _refused(exc) from None
    out = _subscription_out(db, change.subscription, now)
    db.commit()
    return PlanChangeOut(
        result=change.result,
        subscription=out,
        payment=_payment_out(change.started.payment) if change.started else None,
        checkout_url=change.started.url if change.started else None,
    )


@router.post(
    "/subscription/pay-link",
    responses={
        **_NOT_FOUND,
        409: {"model": ErrorResponse, "description": "`no_subscription`, `nothing_due`"},
        422: {"model": ErrorResponse, "description": "`price_not_available`"},
        502: {"model": ErrorResponse, "description": "`provider_error`"},
        503: {"model": ErrorResponse, "description": "`provider_unavailable`"},
    },
)
def pay_link(
    request: Request,
    db: Session = Depends(get_session),
    user: User = Depends(billing_user),
) -> PayLinkOut:
    """The provider's page to pay what is overdue, for a plan in its grace: the same page as a
    charge already open for this period (3-D Secure), else a fresh hosted checkout."""
    now = datetime.now(UTC)
    try:
        started = renewals.pay_link(db, user, request.app.state.settings, now)
    except subscriptions.Refused as exc:
        db.rollback()
        raise _refused(exc) from None
    return PayLinkOut(payment=_payment_out(started.payment), checkout_url=started.url)
