"""An invoice as a PDF, built when asked and never kept (billing spec section 6, stage S9).

The same shape as the meeting export (`app/export.py`): each build runs in a child process
(`app/invoice_render.py`), one at a time per web process, with a hard time limit, so a render that
hangs is killed and the web process never holds the memory. Nothing is written to disk and nothing
is cached: the file exists only in the response (or in the mail that carries it).

`document` turns an invoice row into the plain text the page shows (money and dates written out), so
the page does no arithmetic and what it says is what was stored when the invoice was issued.
"""

import json
import logging
import subprocess
import sys
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from app import export
from app.models import Invoice, Payment

logger = logging.getLogger(__name__)

# The seller's zone: Lagos (UTC+1, no daylight saving). Invoice years and dates are read in it.
SELLER_ZONE = ZoneInfo("Africa/Lagos")
TIME_LIMIT_SECONDS = 30  # below nginx's 60 s, like the meeting export
_slots = threading.Semaphore(2)  # at most two builds at once in this process

# Plain-text amounts: no symbol a PDF font or a mail client could garble.
_MINOR = 100


class InvoiceBusy(Exception):
    """Two invoice builds are running in this process."""


class InvoiceFailed(Exception):
    """The PDF could not be built."""


def money(amount_minor: int, currency: str) -> str:
    whole, cents = divmod(abs(amount_minor), _MINOR)  # integers: no float on a money record
    return f"{'-' if amount_minor < 0 else ''}{currency} {whole:,}.{cents:02d}"


def _date(at: datetime) -> str:
    local = at.astimezone(SELLER_ZONE)
    return f"{local.day} {local:%B %Y}"


def filename(invoice: Invoice) -> str:
    return f"invoice-{invoice.number}.pdf"


def _party(values: dict, order: list[tuple[str, str]]) -> list[str]:
    """The lines of a party: its name first, then each non-empty detail on its own line."""
    lines = [values.get("name") or ""]
    for key, label in order:
        text = (values.get(key) or "").strip()
        if text:
            lines.extend(f"{label}{part}" for part in text.splitlines() if part.strip())
    return [line for line in lines if line.strip()] or ["(no name given)"]


def document(invoice: Invoice, payment: Payment, refunded_at: datetime | None = None) -> dict:
    """The plain-text document for `invoice`. `refunded_at` is when its payment was refunded, if it
    was: the page then says so (there is no credit note yet, see the SOP)."""
    seller = _party(
        invoice.seller,
        [
            ("address", ""),
            ("registration_no", "Registration no. "),
            ("tax_id", "Tax ID "),
            ("email", ""),
        ],
    )
    buyer = _party(
        invoice.buyer,
        [("address", ""), ("country", "Country "), ("tax_id", "Tax ID "), ("email", "")],
    )
    line = invoice.line or {}
    period = ""
    if line.get("period_start") and line.get("period_end"):
        start = datetime.fromisoformat(line["period_start"])
        end = datetime.fromisoformat(line["period_end"])
        period = f"{_date(start)} to {_date(end)}"
    elif line.get("period_end"):
        period = f"Valid until {_date(datetime.fromisoformat(line['period_end']))}"
    currency = invoice.currency
    totals = []
    if invoice.vat_rate_bp > 0:
        rate = f"{invoice.vat_rate_bp / 100:g}%"
        totals = [
            ("Subtotal", money(invoice.subtotal_minor, currency)),
            (f"VAT {rate} (included in the price)", money(invoice.vat_minor, currency)),
        ]
    banners = []
    if invoice.test_mode:
        banners.append(
            "TEST INVOICE: paid with test keys, no real money moved. Not a tax document."
        )
    if refunded_at is not None:
        banners.append(f"REFUNDED on {_date(refunded_at)}: this payment was refunded in full.")
    notes = [f"Payment reference: {payment.reference}"]
    if payment.paid_at:
        notes.append(f"Paid {_date(payment.paid_at)}")
    if invoice.vat_rate_bp == 0:
        notes.append("No VAT is charged on this invoice.")
    return {
        "number": invoice.number,
        "issued": _date(invoice.issued_at),
        "seller": seller,
        "buyer": buyer,
        "description": line.get("description") or "Beyric Note Taker",
        "period": period,
        "subtotal": money(invoice.subtotal_minor, currency),
        "totals": totals,
        "total_label": f"Total paid ({currency})",
        "total": money(invoice.total_minor, currency),
        "banners": banners,
        "notes": notes,
        "footer": f"{invoice.seller.get('name') or 'Beyric'} - invoice {invoice.number}",
    }


def build_pdf(doc: dict) -> bytes:
    """The PDF for `doc`. Raises `InvoiceBusy` when the process is already building two (the caller
    answers 503) and `InvoiceFailed` when the child fails or runs past the time limit."""
    if not _slots.acquire(blocking=False):
        raise InvoiceBusy()
    try:
        try:
            done = subprocess.run(
                [sys.executable, "-m", "app.invoice_render"],
                input=json.dumps(doc).encode(),
                cwd=export.APP_ROOT,
                capture_output=True,
                timeout=TIME_LIMIT_SECONDS,
                env=export._child_env(),
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.error("invoice: the render ran past %s seconds", TIME_LIMIT_SECONDS)
            raise InvoiceFailed("the render ran past the time limit") from None
        if done.returncode != 0 or not done.stdout.startswith(b"%PDF-"):
            logger.error("invoice: the render exited with status %s", done.returncode)
            raise InvoiceFailed(f"the render exited with status {done.returncode}")
        return done.stdout
    finally:
        _slots.release()
