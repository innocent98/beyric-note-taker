"""Payments behind one provider layer (billing spec sections 5 and 7, stage S4).

Everything here is dark: `BILLING_ENABLED` defaults to false, the webhook route answers 404 while it
is off, and nothing outside this package reads or writes the billing tables. No real provider exists
yet (Paystack is stage S6, Flutterwave S7); the only provider is `fake.FakeProvider`, used by tests.

- `types`: the neutral event, transaction and error types every provider speaks.
- `provider`: the `PaymentProvider` protocol and the table real adapters will register in.
- `fake`: the in-memory provider the tests use.
- `payments`: creates a payment row and its reference before anyone is sent to a provider.
- `inbox`: verifies a webhook and writes it to `billing_events` once.
- `settle`: re-reads the payment from the provider and decides whether it is paid.
- `api`: the one route, `POST /api/billing/webhooks/{provider}`.
"""
