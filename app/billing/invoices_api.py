"""The person's own invoices under `/api/billing/invoices` (billing spec section 6, stage S9).

Dark exactly like every other billing route (`profile_api.billing_user`): a plain 404 while
`BILLING_ENABLED` is false and for an account not on `BILLING_ALLOWED_EMAILS`. They sit under
`/api/`, so `auth.guard` gives them the login rule of every person route. Only the caller's own
invoices are ever read: there is no route that takes another person's id, and someone else's
invoice number is a plain 404, the same as an unknown one.

| Route | What it does |
|---|---|
| `GET /api/billing/invoices` | the caller's invoices, newest first (at most 50) |
| `GET /api/billing/invoices/{number}/pdf` | one invoice as a PDF file, built now and never stored |

Errors, in the API's one shape (`{"error": {"code", "message"}}`): 404 `not_found`; 503
`invoice_busy` (two invoice files are being built, try again in a few seconds); 500
`invoice_failed` (the file could not be built).
"""

import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import services
from app.api_schemas import ErrorResponse, UtcDatetime
from app.billing import cutover, invoice_pdf
from app.billing.invoice_number import shown as shown_invoice_number
from app.billing.invoice_number import stored_candidates
from app.billing.profile_api import billing_user
from app.db import get_session
from app.models import Invoice, Payment, Refund, User
from app.notes_document import content_disposition

router = APIRouter(prefix="/api/billing", tags=["billing"])

_NOT_FOUND = {404: {"model": ErrorResponse, "description": "billing is off for this account"}}
NUMBER = re.compile(r"BN-[0-9]{4}-[0-9]{6,9}")  # the number as shown (`invoice_number.shown`)
LIST_MAX = 50


class InvoiceOut(BaseModel):
    number: str  # `BN-2026-000123`
    issued_at: UtcDatetime
    currency: str  # `NGN` or `USD`
    # Whole minor units (kobo, cents). `total` is what the person paid; `vat` is the VAT that price
    # includes at `vat_rate_bp` (0 today: no VAT line); `subtotal + vat = total`.
    subtotal_minor: int
    vat_rate_bp: int
    vat_minor: int
    total_minor: int
    description: str  # what was bought, in one line
    payment_reference: (
        str  # the reference of the payment it is for (the same one `/payments` shows)
    )
    payment_status: str  # `succeeded`, or `refunded` once the payment was refunded
    refunded_at: UtcDatetime | None


class InvoicesOut(BaseModel):
    invoices: list[InvoiceOut]


def invoice_out(invoice: Invoice, payment: Payment, refunded_at: datetime | None) -> InvoiceOut:
    return InvoiceOut(
        number=shown_invoice_number(invoice.number),
        issued_at=invoice.issued_at,
        currency=invoice.currency,
        subtotal_minor=invoice.subtotal_minor,
        vat_rate_bp=invoice.vat_rate_bp,
        vat_minor=invoice.vat_minor,
        total_minor=invoice.total_minor,
        description=(invoice.line or {}).get("description") or "Beyric Note Taker",
        payment_reference=payment.reference,
        payment_status=payment.status,
        refunded_at=refunded_at,
    )


def _refunded_at(db: Session, payment: Payment) -> datetime | None:
    if payment.status != "refunded":
        return None
    return db.scalar(
        select(Refund.resolved_at).where(
            Refund.payment_id == payment.id, Refund.status == "submitted"
        )
    )


@router.get("/invoices", responses=_NOT_FOUND)
def list_invoices(
    request: Request, db: Session = Depends(get_session), user: User = Depends(billing_user)
) -> InvoicesOut:
    """The caller's own invoices, newest first (at most 50). A read. Invoices of payments made
    before the go-live cutover (`BILLING_LIVE_SINCE`) are not listed."""
    settings = request.app.state.settings
    rows = db.execute(
        select(Invoice, Payment)
        .join(Payment, Payment.id == Invoice.payment_id)
        .where(Invoice.payer_user_id == user.id, cutover.live_only(Payment.created_at, settings))
        .order_by(Invoice.id.desc())
        .limit(LIST_MAX)
    ).all()
    return InvoicesOut(
        invoices=[
            invoice_out(invoice, payment, _refunded_at(db, payment)) for invoice, payment in rows
        ]
    )


@router.get(
    "/invoices/{number}/pdf",
    response_class=Response,
    responses={
        **_NOT_FOUND,
        200: {"content": {"application/pdf": {}}, "description": "the invoice as a PDF file"},
        503: {"model": ErrorResponse, "description": "`invoice_busy`"},
    },
)
def download_invoice(
    request: Request,
    number: str = Path(...),
    db: Session = Depends(get_session),
    user: User = Depends(billing_user),
) -> Response:
    """One of the caller's own invoices as a PDF. Built when asked and never stored: nothing about
    it is cached. Someone else's number is a plain 404, and so is the number of an invoice made
    before the go-live cutover (`BILLING_LIVE_SINCE`): to the person it does not exist."""
    if not NUMBER.fullmatch(number):
        raise HTTPException(404, "Not found.")
    settings = request.app.state.settings
    row = db.execute(
        select(Invoice, Payment)
        .join(Payment, Payment.id == Invoice.payment_id)
        .where(
            Invoice.number.in_(stored_candidates(number)),
            Invoice.payer_user_id == user.id,
            cutover.live_only(Payment.created_at, settings),
        )
    ).first()
    if row is None:
        raise HTTPException(404, "Not found.")
    invoice, payment = row
    doc = invoice_pdf.document(invoice, payment, _refunded_at(db, payment))
    name = invoice_pdf.filename(invoice)
    db.close()  # the build can take seconds: do not hold a database connection meanwhile
    try:
        content = invoice_pdf.build_pdf(doc)
    except invoice_pdf.InvoiceBusy:
        raise services.Refused(
            503, "invoice_busy", "Invoices are being prepared. Try again in a few seconds."
        ) from None
    except invoice_pdf.InvoiceFailed:
        raise services.Refused(
            500, "invoice_failed", "The invoice could not be prepared. Try again."
        ) from None
    return Response(
        content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(name),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
