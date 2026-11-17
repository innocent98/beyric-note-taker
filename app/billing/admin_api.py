"""The admin's billing routes under `/api/admin/billing/...` (billing spec 3.5, stage S9).

Admin only: a visitor gets 401 from the guard, a signed-in person who is not an admin gets 403
`forbidden` (the same dependency as every admin route), and while `BILLING_ENABLED` is false every
route answers a plain 404 before anything is read. No route reads a meeting, a note or a calendar.

- `GET /api/admin/billing/payments`: recent payments, newest first, each with whether a refund is
  allowed.
- `POST /api/admin/billing/payments/{payment_id}/refund`: refund one payment, for one of the three
  cases.
- `GET /api/admin/billing/refunds`: refunds, newest first, those waiting for a person marked.
- `POST /api/admin/billing/refunds/{refund_id}/resolve`: say what the provider's dashboard shows for
  an unresolved refund.

The rules are `app/billing/refunds.py`: the server reads the facts and refuses what the rules refuse
(409 with a code), whatever the screen showed. The request names the case (`reason`) as the
admin's own statement; the server checks it, and the amount is never taken from the request.
"""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import services
from app.api_admin import require_admin
from app.api_schemas import Body, ErrorResponse, UtcDatetime
from app.billing import refunds, subscriptions
from app.db import get_session
from app.models import Invoice, Payment, Refund, User

LIST_DEFAULT, LIST_MAX = 50, 100


def billing_admin(request: Request, admin: User = Depends(require_admin)) -> User:
    """An admin, once billing is on; a plain 404 before anything is read otherwise."""
    if not request.app.state.settings.billing_enabled:
        raise HTTPException(404, "Not found.")
    return admin


router = APIRouter(
    prefix="/api/admin/billing",
    dependencies=[Depends(billing_admin)],
    tags=["admin"],
    responses={
        403: {"model": ErrorResponse, "description": "Not an admin"},
        404: {"model": ErrorResponse, "description": "billing is off"},
    },
)


class RefundOption(BaseModel):
    # The one case that fits this payment's kind, and whether the rules allow it right now.
    reason: str  # `double_charge`, `first_plan_7d` or `pack_14d`
    allowed: bool
    message: str | None  # why not, when it is not allowed


class RefundBrief(BaseModel):
    id: int
    status: str  # `pending`, `submitted`, `unknown`, `failed`
    reason: str
    created_at: UtcDatetime


class AdminPayment(BaseModel):
    id: int
    reference: str
    payer_email: str | None  # null once the account is gone
    provider: str
    kind: str
    status: str  # `pending`, `succeeded`, `failed`, `refunded`
    currency: str
    amount_minor: int
    created_at: UtcDatetime
    paid_at: UtcDatetime | None
    needs_refund_reason: str | None  # money arrived that nothing could use
    invoice_number: str | None
    refund: RefundBrief | None  # the newest refund of it, if any
    refund_option: RefundOption | None  # null unless the payment succeeded


class AdminPayments(BaseModel):
    payments: list[AdminPayment]


class AdminRefund(BaseModel):
    id: int
    payment_id: int
    payer_email: str | None
    reference: str  # our key for it: look for this in the provider's dashboard
    reason: str
    status: str
    currency: str
    amount_minor: int
    provider_refund_id: str | None
    note: str | None
    error: (
        str | None
    )  # `provider_refused`, `provider_unavailable`, `no_answer`, `confirmed_not_made`
    created_at: UtcDatetime
    resolved_at: UtcDatetime | None
    needs_resolution: bool  # `unknown`, or `pending` for so long that its process died


class AdminRefunds(BaseModel):
    refunds: list[AdminRefund]


class RefundIn(Body):
    reason: Literal["double_charge", "first_plan_7d", "pack_14d"]
    note: str | None = Field(default=None, max_length=refunds.NOTE_MAX)


class ResolveIn(Body):
    # What the provider's dashboard shows for this refund's reference: it `made` it (the refund
    # exists) or `not_made` (there is none, so a new one may be started).
    outcome: Literal["made", "not_made"]


def _now() -> datetime:
    return datetime.now(UTC)


def _option(db: Session, payment: Payment, now: datetime) -> RefundOption | None:
    if payment.status != "succeeded":
        return None
    if payment.needs_refund_reason is not None:
        reason = "double_charge"
    elif payment.kind == "plan":
        reason = "first_plan_7d"
    elif payment.kind == "pack":
        reason = "pack_14d"
    else:
        return RefundOption(
            reason="double_charge",
            allowed=False,
            message="A renewal or an upgrade is not refunded unless it is a double charge.",
        )
    try:
        refunds.assess(db, payment, reason, now, lock=False)
    except refunds.Refused as exc:
        return RefundOption(reason=reason, allowed=False, message=exc.message)
    return RefundOption(reason=reason, allowed=True, message=None)


def _brief(row: Refund | None) -> RefundBrief | None:
    if row is None:
        return None
    return RefundBrief(id=row.id, status=row.status, reason=row.reason, created_at=row.created_at)


@router.get("/payments")
def list_payments(
    limit: int = Query(LIST_DEFAULT, ge=1, le=LIST_MAX),
    only: Literal["all", "needs_refund"] = Query("all"),
    db: Session = Depends(get_session),
) -> AdminPayments:
    """Recent payments, newest first, each with the one refund case that fits it and whether the
    rules allow it now. `only=needs_refund` lists the payments that succeeded and nothing could
    use. A read."""
    now = _now()
    query = select(Payment).order_by(Payment.id.desc()).limit(limit)
    if only == "needs_refund":
        query = query.where(Payment.needs_refund_reason.is_not(None))
    out = []
    for payment in db.scalars(query).all():
        payer = db.get(User, payment.payer_user_id) if payment.payer_user_id else None
        newest = db.scalars(
            select(Refund).where(Refund.payment_id == payment.id).order_by(Refund.id.desc())
        ).first()
        out.append(
            AdminPayment(
                id=payment.id,
                reference=payment.reference,
                payer_email=payer.email if payer else None,
                provider=payment.provider,
                kind=payment.kind,
                status=payment.status,
                currency=payment.currency,
                amount_minor=payment.amount_minor,
                created_at=payment.created_at,
                paid_at=payment.paid_at,
                needs_refund_reason=payment.needs_refund_reason,
                invoice_number=db.scalar(
                    select(Invoice.number).where(Invoice.payment_id == payment.id)
                ),
                refund=_brief(newest),
                refund_option=_option(db, payment, now),
            )
        )
    return AdminPayments(payments=out)


def _refund_out(db: Session, row: Refund, now: datetime) -> AdminRefund:
    payment = db.get(Payment, row.payment_id)
    payer = db.get(User, payment.payer_user_id) if payment and payment.payer_user_id else None
    stuck = row.status == "pending" and now - row.created_at >= refunds.STUCK_AFTER
    return AdminRefund(
        id=row.id,
        payment_id=row.payment_id,
        payer_email=payer.email if payer else None,
        reference=row.reference,
        reason=row.reason,
        status=row.status,
        currency=row.currency,
        amount_minor=row.amount_minor,
        provider_refund_id=row.provider_refund_id,
        note=row.note,
        error=row.error,
        created_at=row.created_at,
        resolved_at=row.resolved_at,
        needs_resolution=row.status == "unknown" or stuck,
    )


@router.get("/refunds")
def list_refunds(
    limit: int = Query(LIST_DEFAULT, ge=1, le=LIST_MAX),
    db: Session = Depends(get_session),
) -> AdminRefunds:
    """Refunds, newest first. A refund with `needs_resolution` true is waiting for a person to look
    in the provider's dashboard and say what is there. A read."""
    now = _now()
    rows = db.scalars(select(Refund).order_by(Refund.id.desc()).limit(limit)).all()
    return AdminRefunds(refunds=[_refund_out(db, row, now) for row in rows])


@router.post(
    "/payments/{payment_id}/refund",
    responses={
        404: {"model": ErrorResponse, "description": "`not_found`"},
        409: {
            "model": ErrorResponse,
            "description": "`already_refunded`, `payment_not_paid`, `refund_in_progress`, "
            "`not_a_double_charge`, `wrong_kind`, `not_used_for_hours`, `not_first_payment`, "
            "`window_over`, `payer_gone`, `recording_in_progress`, `hours_used`, `plan_changed`, "
            "`no_provider_transaction`",
        },
    },
)
def refund_payment(
    payment_id: int,
    body: RefundIn,
    request: Request,
    admin: User = Depends(billing_admin),
    db: Session = Depends(get_session),
) -> AdminRefund:
    """Refund payment `payment_id` in full, for the case `reason`. The server checks the facts
    (409 with a code when the rules refuse), writes the refund, ends the hours it covers, asks the
    provider ONCE and answers with the refund as it stands: `submitted`, `failed` (the provider
    refused; nothing moved, the hours are back) or `unknown` (no answer: look at the dashboard,
    never ask again until resolved)."""
    settings = request.app.state.settings
    now = _now()
    try:
        row = refunds.request(db, payment_id, body.reason, admin, body.note, settings, now)
    except subscriptions.Refused as exc:
        db.rollback()
        raise services.Refused(exc.status, exc.code, exc.message) from None
    return _refund_out(db, row, now)


@router.post(
    "/refunds/{refund_id}/resolve",
    responses={
        404: {"model": ErrorResponse, "description": "`not_found`"},
        409: {"model": ErrorResponse, "description": "`not_unresolved`"},
    },
)
def resolve_refund(
    refund_id: int,
    body: ResolveIn,
    admin: User = Depends(billing_admin),
    db: Session = Depends(get_session),
) -> AdminRefund:
    """Say what the provider's dashboard shows for an unresolved refund. Nothing here calls the
    provider: `made` makes the payment refunded, `not_made` closes the refund and gives the hours
    back, so a new refund may be started."""
    now = _now()
    try:
        row = refunds.resolve(db, refund_id, body.outcome, admin, now)
    except subscriptions.Refused as exc:
        db.rollback()
        raise services.Refused(exc.status, exc.code, exc.message) from None
    return _refund_out(db, row, now)
