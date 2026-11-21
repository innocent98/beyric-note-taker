"""The go-live cutover: which billing rows were made with the providers' TEST keys.

The database does not know which mode a row was made in (`BILLING_LIVE` is a process setting), so a
time says it. `BILLING_LIVE_SINCE` is that time, `T`. **A payment created before `T` is test-era**:
it is not shown to a person, and it is not counted when the rules ask about a person's history. It
only has effect while `BILLING_LIVE` is true: with `BILLING_LIVE` false the setting is ignored and
every row counts, as before.

This module is the one place the rule lives. Everything that must hide or skip a test-era row asks
it (`is_test_era` for a row in hand, `live_only` for a query), and the one-time closeout script
(`scripts/billing_close_test_data.py`) checks its `--before` value with `parse_instant` (defined in
`app/config.py`, where the setting is validated: the same parser), so the two cannot disagree on
what a timestamp means.

What it covers is a PAYMENT's `created_at` (an invoice is judged by the payment it is for): a
payment made at or after `T` is live, one made before is not. A row made exactly at `T` is live
(the closeout touches only rows strictly before `T`).
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import true
from sqlalchemy.sql.elements import ColumnElement

from app.config import parse_instant

if TYPE_CHECKING:
    from app.config import Settings

__all__ = ["is_test_era", "live_only", "parse_instant", "since"]


def since(settings: "Settings") -> datetime | None:
    """`T` while the cutover is in force: `BILLING_LIVE` is true and `BILLING_LIVE_SINCE` is set.
    None otherwise (every row counts)."""
    raw = settings.billing_live_since.strip()
    if not settings.billing_live or not raw:
        return None
    return parse_instant(raw, name="BILLING_LIVE_SINCE")


def is_test_era(created_at: datetime, settings: "Settings") -> bool:
    """True when a payment created at `created_at` was made before the cutover."""
    cutover = since(settings)
    return cutover is not None and created_at < cutover


def live_only(column, settings: "Settings") -> ColumnElement[bool]:
    """A `where` clause that keeps only rows made at or after the cutover (all rows while it is
    not in force). `column` is `Payment.created_at`, or `BillingEvent.received_at` for a webhook
    event (an event is judged by when it arrived)."""
    cutover = since(settings)
    return true() if cutover is None else column >= cutover
