"""The neutral types a payment provider speaks: nothing here knows a vendor's field names.

An adapter turns its vendor's webhook body into a `ProviderEvent` and its vendor's "verify" call
into a `ProviderTransaction`. Amounts are always whole minor units (kobo, cents) and currencies
are the ISO codes the `payments` table allows.
"""

from dataclasses import dataclass, field

# What a webhook can tell us. `charge_succeeded` and `charge_failed` move a payment; `refund` and
# `dispute` are stored and left for the refund stage (S9).
EVENT_KINDS = ("charge_succeeded", "charge_failed", "refund", "dispute")
# What the provider says about one payment when we ask it directly.
TRANSACTION_STATUSES = ("successful", "failed", "pending")


class BillingError(Exception):
    """Base of every billing failure the inbox and the settlement handle."""


class WebhookRejected(BillingError):
    """The request is not genuine (wrong or missing signature or secret). Nothing is written."""


class BadEvent(BillingError):
    """The request was genuine but its body is not an event we can read."""


class ProviderError(BillingError):
    """A call to the provider failed or gave no usable answer. Retryable: nothing it describes can
    have moved money. (A money call that got no answer is `OutcomeUnknown`, never this.)"""


class OutcomeUnknown(BillingError):
    """A call that moves money (a charge, a refund) got no answer: it may or may not have happened.
    Deliberately NOT a `ProviderError`: a handler that retries on `ProviderError` must not catch
    this one, because a repeated refund or charge can pay twice (no idempotency key is documented
    for a Flutterwave v3 refund). The caller looks the payment up first and never retries blind."""


@dataclass(frozen=True)
class ProviderEvent:
    """One webhook, as we understand it. It is a claim, not a fact: money moves only after the
    payment is re-read from the provider (`settle.process`).

    `event_id` is the dedupe key: the same event delivered again (a retry) must give the same id,
    and two different events about one payment (a charge, then its refund) must give different
    ones. The adapter builds it from what its vendor sends."""

    event_id: str
    kind: str
    reference: str
    provider_tx_id: str
    amount_minor: int
    currency: str
    payer_email: str | None = None
    card_token: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class ProviderTransaction:
    """A payment as the provider itself reports it when asked. This, not the webhook, decides."""

    provider_tx_id: str
    reference: str
    status: str
    amount_minor: int
    currency: str
    payer_email: str | None = None
    card_token: str | None = field(default=None, repr=False)
    card_country: str | None = None
    # Where the payer must go to finish this payment (a `pending` saved-card charge waiting for the
    # payer's 3D Secure step). The charge stays open at the provider and can still succeed: send
    # this link, never a second checkout for the same period.
    action_url: str | None = None
    # Why a `failed` result came back without any transaction (the provider refused the call).
    failure_reason: str | None = None


@dataclass(frozen=True)
class Checkout:
    """Where to send the payer, and the reference we made for the payment."""

    url: str
    reference: str


class RetryLater(BillingError):
    """The event is stored but could not be decided now (the provider did not answer, still says
    pending, or a step after it failed). Nothing was applied; the webhook answers 503 so the
    provider sends the same event again."""
