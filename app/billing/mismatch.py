"""The card-country note on a settled payment (billing spec section 5.2, "When the card's issuing
country disagrees with the billing country").

**This never refuses, reverses or delays a payment that succeeded.** A Nigerian business paying with
a foreign card, or a person billed abroad paying with a Nigerian card, is a normal customer. All
this does is write two columns on the payment, `card_country` and `country_mismatch`, so a human can
look (admin view, a note to the person: later stages). `flag_country_mismatch` is called from
`settle._mark_paid` once the provider has confirmed the payment, and it cannot raise: any failure
inside it is logged and the payment goes on exactly as it was.

The country compared is the one that applied when the payment was made (`profile.country_view` at
`paid_at`): the billing country on the profile, or the scheduled country when its time had already
come but nothing had written it to the row yet. A change still waiting at that moment does not
count: the payment was taken under the old route.

Two columns only. Not built here (see the SOP): the "two mismatched payments in a row" count and the
mail, the plain note shown to the person, the admin list.
"""

import logging
import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.billing.countries import ISO_COUNTRIES
from app.billing.profile import country_view
from app.models import BillingProfile, Payment

logger = logging.getLogger(__name__)

_TRAILING_CODE = re.compile(r"(?:^|\s)([A-Za-z]{2})$")


def normalize_card_country(raw: object) -> str | None:
    """The two-letter country in what a provider reported, upper-cased, or None. Paystack gives
    `NG`; Flutterwave's verify answer gives `NIGERIA NG` and its webhook `NG` (spec 5.2), so the
    trailing two letters are read, and only if they are an assigned ISO code (`UK` and `OF` are not,
    so a stray word cannot become a country). Anything else is None, never a guess. The trailing
    letters are the code, whatever the name before them says: `UNITED STATES NG` is `NG`."""
    if not isinstance(raw, str):
        return None
    match = _TRAILING_CODE.search(raw.strip())
    code = match.group(1).upper() if match else None
    return code if code in ISO_COUNTRIES else None


def apply_country_note(payment: Payment, billing_country: str | None) -> None:
    """Set `card_country` (kept as it is, or read from its trailing letters) and
    `country_mismatch` on `payment`. A mismatch needs both countries: with no billing country yet
    or no card country (a bank transfer carries no card) there is nothing to compare, so it is
    false. Changes nothing else."""
    card = normalize_card_country(payment.card_country)
    payment.card_country = card
    payment.country_mismatch = bool(card and billing_country and card != billing_country)


def flag_country_mismatch(db: Session, payment: Payment) -> None:
    """Read the payer's billing country and write the note on `payment`. Never raises: the lookup
    runs in a savepoint, so a failure cannot poison the transaction that is marking the payment
    paid. On failure the payment keeps its card country and is not flagged."""
    try:
        with db.begin_nested():
            country = None
            if payment.payer_user_id is not None:
                row = db.scalar(
                    select(BillingProfile).where(BillingProfile.user_id == payment.payer_user_id)
                )
                at = payment.paid_at or datetime.now(UTC)
                country = country_view(row, at).billing_country
            apply_country_note(payment, country)
    except Exception:  # a note must never stop a paid customer's hours
        logger.exception("could not compare the card country on payment %s", payment.id)
