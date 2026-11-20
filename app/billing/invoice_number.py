"""The invoice number a person sees, and how it maps to the one stored.

Stored (`invoices.number`, unique) there are two series: the live one, `BN-<year>-<six digits>`,
and the internal series of invoices made while `BILLING_LIVE` is false, `BN-T-<year>-<six digits>`,
counted under the negative year in `invoice_counters` so purchases made with the providers' test
keys never use up a live number (`invoices.next_number`). Nothing a person or an API client sees
may say or imply that a payment was made with test keys, so the `T-` never leaves the server:
everything that shows a number (the list, the PDF, its file name, the receipt mail, the payment
history, the admin page) goes through `shown`, and the PDF route turns the number it is given back
with `stored_candidates`.

`shown` moves the internal series to the top of the six-digit range: `BN-T-2026-000011` is shown
as `BN-2026-900011`. It is a plain function of the stored number, so it is the same for a row made
before this change and one made after, it is one-to-one (two rows never show the same number), and
it cannot meet a live number (`BN-2026-000001`, `...000002`, ...) until a year has 900,000 live
invoices. Nothing is rewritten in the database (no migration): the stored `BN-T-` strings stay.
"""

import re

# Where the internal series starts when shown. A live year has to pass this many invoices before a
# live number can equal a shown internal one.
INTERNAL_SERIES_OFFSET = 900_000

_STORED = re.compile(r"BN-(T-)?([0-9]{4})-([0-9]{6,9})")
_SHOWN = re.compile(r"BN-([0-9]{4})-([0-9]{6,9})")


def shown(stored: str) -> str:
    """The number to show for a stored one: a live number as it is, an internal-series number moved
    to the top of the range (`BN-T-2026-000011` -> `BN-2026-900011`). A string that is not shaped
    like an invoice number is returned unchanged."""
    found = _STORED.fullmatch(stored)
    if found is None or found.group(1) is None:
        return stored
    return f"BN-{found.group(2)}-{int(found.group(3)) + INTERNAL_SERIES_OFFSET:06d}"


def stored_candidates(number: str) -> list[str]:
    """The stored numbers a shown `number` can be: itself (a live number) and, when it is in the top
    range, the internal-series number that `shown` maps to it. At most one row matches, because a
    live number never reaches that range (see the module note)."""
    candidates = [number]
    found = _SHOWN.fullmatch(number)
    if found is not None and int(found.group(2)) >= INTERNAL_SERIES_OFFSET:
        candidates.append(
            f"BN-T-{found.group(1)}-{int(found.group(2)) - INTERNAL_SERIES_OFFSET:06d}"
        )
    return candidates
