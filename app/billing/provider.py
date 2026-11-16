"""The one interface the app asks of a payment provider (billing spec section 5.1).

Same pattern as `app/bot_provider.py`: a `Protocol`, a table of factories, a fake for tests. Nothing
outside `app/billing/` knows a vendor's name or field names. Real adapters register themselves in
`PROVIDER_FACTORIES` when they are built (Paystack S6, Flutterwave S7); until then the table is
empty, so no webhook is accepted for any provider name, and the fake is never reachable in
production.

Errors: `verify_webhook` raises `WebhookRejected`, `parse_event` raises `BadEvent`, and every call
that talks to the vendor raises `ProviderError` (retryable).
"""

from collections.abc import Callable, Mapping
from typing import Protocol, runtime_checkable

from app.billing.types import Checkout, ProviderEvent, ProviderTransaction
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
        self, token: str, amount_minor: int, currency: str, reference: str
    ) -> ProviderTransaction:
        """Charge a saved card token (a renewal). Returns the transaction as the provider reports
        it; a charge that needs the payer is a failed or pending transaction, never an exception."""
        ...

    def refund(self, provider_tx_id: str, amount_minor: int) -> str:
        """Refund `amount_minor` of a payment; returns the provider's refund id."""
        ...


# provider name (the `{provider}` in the webhook path) -> a function that builds it from settings.
PROVIDER_FACTORIES: dict[str, Callable[[Settings], PaymentProvider]] = {}


def provider_named(name: str, settings: Settings) -> PaymentProvider | None:
    """The provider registered under `name`, or None for a name nobody registered."""
    factory = PROVIDER_FACTORIES.get(name)
    return factory(settings) if factory else None
