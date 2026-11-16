"""The webhook inbox (billing spec section 5.4): check the request, write it down once.

`receive` does three things in this order and stops at the first that fails, writing nothing:

1. `provider.verify_webhook(headers, raw_body)`: the provider's own check, on the exact bytes that
   arrived. A request that fails is dropped: it is not stored, so a stranger cannot fill the table.
2. `provider.parse_event(raw_body)`: the neutral event.
3. Insert one `billing_events` row keyed by (provider, event id). A second delivery of the same
   event hits the unique key and gets the row the first one made, so a replay is never a second
   event.

Deciding what the event means is `settle.process`; nothing here moves money.

The stored payload is the body with every token and secret removed (`scrub`): vendors put reusable
card tokens in their webhook bodies, and a token belongs encrypted in `payment_methods`, not in a
readable inbox row. The field names it looks for are by name only; the real adapters (S6, S7) must
confirm them against the vendors' real payloads.
"""

import json
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.billing.provider import PaymentProvider
from app.billing.types import EVENT_KINDS, BadEvent, ProviderEvent
from app.models import BillingEvent

MAX_DEDUPE_KEY = 200
REMOVED = "[removed]"
_SECRET_KEYS = frozenset({"authorization_code", "signature", "secret", "cvv", "card_number", "pan"})


def scrub(value):
    """`value` (parsed JSON) with the value of every key that names a token or a secret replaced."""
    if isinstance(value, dict):
        return {key: REMOVED if _is_secret(key) else scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def _is_secret(key: str) -> bool:
    name = str(key).lower()
    return "token" in name or name in _SECRET_KEYS


def _usable(event: ProviderEvent) -> bool:
    """The text fields are text, the id fits its column, the kind is one we know. (The event's
    amount is a claim that is never read; only the provider's own report decides.)"""
    ids = (event.event_id, event.reference, event.provider_tx_id)
    return (
        event.kind in EVENT_KINDS
        and all(isinstance(value, str) and value for value in ids)
        and len(event.event_id) <= MAX_DEDUPE_KEY
        and len(event.reference) <= 64
        and len(event.provider_tx_id) <= 128
    )


def receive(
    db: Session,
    provider: PaymentProvider,
    headers: Mapping[str, str],
    raw_body: bytes,
    now,
) -> tuple[BillingEvent, ProviderEvent]:
    """Verify, parse and store one webhook. Returns its inbox row (the new one, or the one an
    earlier delivery made) and the parsed event. Raises `WebhookRejected` or `BadEvent`."""
    provider.verify_webhook(headers, raw_body)
    event = provider.parse_event(raw_body)
    if not _usable(event):
        raise BadEvent("the event has no usable kind, id or reference")
    try:
        payload = json.loads(raw_body)
    except ValueError as exc:
        raise BadEvent("the body is not JSON") from exc
    if not isinstance(payload, dict):
        raise BadEvent("the body is not a JSON object")

    row = BillingEvent(
        provider=provider.name,
        dedupe_key=event.event_id,
        type=event.kind,
        payload=scrub(payload),
        received_at=now,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        row = db.scalars(
            select(BillingEvent).where(
                BillingEvent.provider == provider.name, BillingEvent.dedupe_key == event.event_id
            )
        ).one()
    else:
        db.commit()
    return row, event
