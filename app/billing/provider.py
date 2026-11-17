"""The one interface the app asks of a payment provider (billing spec section 5.1).

Same pattern as `app/bot_provider.py`: a `Protocol`, a table of factories, a fake for tests. Nothing
outside `app/billing/` knows a vendor's name or field names. Real adapters register themselves in
`PROVIDER_FACTORIES` when their module is imported (`app/billing/__init__.py` imports each one). A
provider name nobody registered is not accepted by the webhook route, and the fake is
never registered by the app, so it is never reachable in production.

Errors: `verify_webhook` raises `WebhookRejected`, `parse_event` raises `BadEvent` (or
`IgnoredEvent`, a genuine event we do not act on), and every call that talks to the vendor raises
`ProviderError` (retryable: nothing it describes moved money). A call that moves money and got no
answer (`charge_saved_method`, `refund`) may raise `OutcomeUnknown`, which is NOT a `ProviderError`:
look the payment up, never retry blind.
"""

from collections.abc import Callable, Mapping
from typing import Protocol, runtime_checkable

from app.billing.types import Checkout, ProviderError, ProviderEvent, ProviderTransaction
from app.config import Settings


@runtime_checkable
class PaymentProvider(Protocol):
    name: str
    currencies: tuple[str, ...]

    def start_checkout(self, payment, payer, return_url: str) -> Checkout:
        """A hosted-checkout link for `payment` (a `models.Payment`), paid by `payer` (a
        `models.User`), sent back to `return_url`. The link carries `payment.reference`."""
        ...

    def verify_webhook(self, headers: Mapping[str, str], raw_body: bytes) -> None:
        """Raise `WebhookRejected` unless the request is genuine. `raw_body` is the exact bytes
        received: a signature is over those bytes, never over a re-encoded body."""
        ...

    def parse_event(self, raw_body: bytes) -> ProviderEvent:
        """The neutral event in `raw_body`, or `BadEvent`. Only called after `verify_webhook`."""
        ...

    def fetch_transaction(self, provider_tx_id: str) -> ProviderTransaction:
        """Re-read one payment from the provider's own API."""
        ...

    def charge_saved_method(
        self,
        token: str,
        amount_minor: int,
        currency: str,
        reference: str,
        *,
        payer_email: str | None = None,
    ) -> ProviderTransaction:
        """Charge a saved card token (a renewal). Returns the transaction as the provider reports
        it; a refused charge is a `failed` result and a charge that needs the payer is a `pending`
        one (with `payer_action_url`, the payer's own link: never open a second checkout for it),
        never an exception. A call that got no answer may raise `OutcomeUnknown`.

        **This answer never grants hours.** It is the provider's reply to a charge, not a verified
        payment: only `settle`, after `fetch_transaction`, marks a payment paid.
        `payer_email` is the email the provider saw on the first payment (`email_at_provider`):
        Paystack and Flutterwave charge a saved card only with the email that created it, so an
        adapter that needs it raises `ProviderError` when it is missing; one that does not need it
        ignores it."""
        ...

    def refund(
        self, provider_tx_id: str, amount_minor: int, *, reference: str | None = None
    ) -> str:
        """Refund `amount_minor` of a payment; returns the provider's refund id. `reference` is
        our own idempotency key for this refund (made from the refund row): a provider that takes
        one refuses a retry with the same key instead of refunding twice. A caller must pass one.
        No answer may raise `OutcomeUnknown`: never retry it blind, a second refund can pay twice
        wherever the provider has no idempotency key (Flutterwave v3 has none)."""
        ...


# provider name (the `{provider}` in the webhook path) -> a function that builds it from settings.
PROVIDER_FACTORIES: dict[str, Callable[[Settings], PaymentProvider]] = {}


def provider_named(name: str, settings: Settings) -> PaymentProvider | None:
    """The provider registered under `name`, or None for a name nobody registered. The test double
    is never returned while `BILLING_LIVE` is true, even if something registered it."""
    if name == "fake" and settings.billing_live:
        return None
    factory = PROVIDER_FACTORIES.get(name)
    return factory(settings) if factory else None


def lookup_by_reference(provider: PaymentProvider, reference: str) -> ProviderTransaction:
    """Ask the provider about a payment by OUR reference: for a payment whose webhook never came and
    for a charge whose outcome is not known. The adapters name it differently (Paystack
    `verify_reference`, Flutterwave `fetch_by_reference`); the fake has the first. Raises
    `ProviderError` when the provider cannot be asked or has nothing under that reference."""
    for name in ("verify_reference", "fetch_by_reference"):
        method = getattr(provider, name, None)
        if method is not None:
            return method(reference)
    raise ProviderError("this provider cannot look a payment up by its reference")
