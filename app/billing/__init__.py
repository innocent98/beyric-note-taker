"""Payments behind one provider layer (billing spec sections 5 and 7, stage S4).

Everything here is dark: `BILLING_ENABLED` defaults to false, the webhook route answers 404 while it
is off, and nothing outside this package reads or writes the billing tables. No real provider exists
yet for Flutterwave (stage S7); Paystack (stage S6, `paystack.py`) registers itself below and needs
keys in the environment to make any call; `fake.FakeProvider` is for tests.

- `types`: the neutral event, transaction and error types every provider speaks.
- `provider`: the `PaymentProvider` protocol and the table real adapters will register in.
- `fake`: the in-memory provider the tests use.
- `paystack`: the naira adapter (stage S6); registered in `provider.PROVIDER_FACTORIES` on import.
- `payments`: creates a payment row and its reference before anyone is sent to a provider.
- `inbox`: verifies a webhook and writes it to `billing_events` once.
- `settle`: re-reads the payment from the provider and decides whether it is paid.
- `api`: the one route, `POST /api/billing/webhooks/{provider}`.
"""

# Importing an adapter registers it in `provider.PROVIDER_FACTORIES`; one line per adapter.
from app.billing import paystack  # noqa: F401
