"""The receipt mail: one per used, successful payment, with the invoice attached (stage S9).

A SESSION template (`mail_templates.SESSION_TEMPLATES`): the outbox row holds the invoice id only
(`{"invoice_id": 7}`), and the text and the PDF are built when the mail is SENT, from the stored
invoice (the PDF is never stored, `app/billing/invoice_pdf.py`). Nothing about the payer, the
amount or the buyer is ever copied into `mail_outbox`.

It is account mail, not notes mail: a receipt is the record of a payment the person made, so it is
not held by the daily notes caps and an unsubscribe does not stop it; only a bounce or a complaint
does (`mail._is_suppressed`). If the PDF cannot be built (the renderer is busy or fails) the mail
still goes, without the file, and says the invoice is under Billing, so a receipt is never lost
for want of a renderer.
"""

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.billing import invoice_number, invoice_pdf
from app.billing.invoice_pdf import money
from app.config import Settings
from app.mail_layout import Block, Button, Heading, Meta, Para, Rows, render_html, render_text
from app.mail_message import Attachment, Message, clean_line
from app.mail_templates import Skip, session_template
from app.models import Invoice, Payment, Refund

logger = logging.getLogger(__name__)

TEMPLATE = "billing_receipt"


@session_template(TEMPLATE)
def receipt(
    session: Session, payload: dict, to: str, settings: Settings, now: datetime
) -> Message | Skip:
    invoice_id = payload.get("invoice_id")
    if isinstance(invoice_id, bool) or not isinstance(invoice_id, int):
        return Skip("invoice_gone")
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        return Skip("invoice_gone")
    payment = session.get(Payment, invoice.payment_id)
    if payment is None or payment.status != "succeeded":
        # refunded before the mail went: the receipt would be for money that came back
        return Skip("payment_not_paid")
    base = settings.public_base_url.rstrip("/")
    number = invoice_number.shown(invoice.number)
    line = (invoice.line or {}).get("description") or "Beyric Note Taker"
    heading = "Thank you for your payment"
    rows: list[tuple[str, str]] = [
        ("Invoice", number),
        (clean_line(line, 100), money(invoice.total_minor, invoice.currency)),
    ]
    if invoice.vat_rate_bp > 0:
        rows.append(
            (
                f"VAT {invoice.vat_rate_bp / 100:g}% included",
                money(invoice.vat_minor, invoice.currency),
            )
        )
    blocks: list[Block] = [
        Heading(heading, after=10),
        Meta(
            f"{money(invoice.total_minor, invoice.currency)} paid, invoice {number}",
            after=18,
        ),
        Rows(tuple(rows)),
    ]
    attachments: tuple[Attachment, ...] = ()
    try:
        refund = (
            session.query(Refund)
            .filter(Refund.payment_id == payment.id, Refund.status == "submitted")
            .first()
        )
        pdf = invoice_pdf.build_pdf(
            invoice_pdf.document(invoice, payment, refund.resolved_at if refund else None)
        )
        attachments = (Attachment(invoice_pdf.filename(invoice), pdf, "application/pdf"),)
        blocks.append(Para("Your invoice is attached as a PDF.", muted=True, before=14))
    except (invoice_pdf.InvoiceBusy, invoice_pdf.InvoiceFailed):
        logger.warning("receipt %s sent without its PDF", invoice.id)
        blocks.append(
            Para("You can download your invoice from your billing page.", muted=True, before=14)
        )
    blocks.append(Button("Open billing", f"{base}/billing"))
    footer = "You received this because you made a payment to Beyric Note Taker."
    subject = clean_line(f"Your receipt {number}", 120)
    return Message(
        to=to,
        subject=subject,
        text=render_text(blocks, footer),
        html=render_html(subject, blocks, footer),
        attachments=attachments,
    )
