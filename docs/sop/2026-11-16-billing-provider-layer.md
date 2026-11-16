# SOP: billing stage S4, the provider layer and the webhook inbox

**What shipped.** `app/billing/`: the `PaymentProvider` interface, an in-memory `FakeProvider`, neutral event and payment types, a webhook route (`POST /api/billing/webhooks/{provider}`) that checks the provider's signature on the raw body, writes each genuine event to an inbox once, and **re-reads the payment from the provider before it will mark anything paid**. Three new tables (`payments`, `payment_methods`, `billing_events`, migration **0050**). Everything is dark: `BILLING_ENABLED` defaults to `false`, the route answers 404 while it is off, no real provider exists (Paystack is S6, Flutterwave S7), nothing outside `app/billing/` reads or writes the tables, no key, no vendor call, no money. Branch `feature/billing-provider-layer`; PR number is added to this line when it is opened. Stage S4 of `docs/superpowers/specs/2026-11-15-billing-and-plans-design.md` (sections 5.1, 5.3, 5.4, 5.5, 7, 8). **No FE integration guide:** no endpoint the frontend consumes exists yet (the only route is called by payment providers); `docs/fe-integration-guide-billing.md` starts when a screen needs one.

## Why

The money rule has to be written, tested and reviewed before any vendor is connected. A webhook is only a claim: Flutterwave's check is a shared secret, Paystack's a signature, and both tell you to ask their API before giving value. Building the rule once, against a fake, means S6 and S7 only translate vendor shapes into the neutral types and cannot change what "paid" means.

## How, and what was decided

| Piece | What it does |
|---|---|
| `types.py` | `ProviderEvent` (what a webhook claims), `ProviderTransaction` (what the provider reports when asked), `Checkout`, the four event kinds, and the errors `WebhookRejected`, `BadEvent`, `ProviderError`, `RetryLater`. Amounts are minor units. A card token field has `repr=False` so it cannot reach a log |
| `provider.py` | the `PaymentProvider` Protocol (spec 5.1) and `PROVIDER_FACTORIES`, **empty**: no provider name is accepted by the route until an adapter registers itself, and the fake is never registered by the app, so it cannot be reached in production |
| `payments.new_payment` | makes the `pending` payment and its reference `bn-<id>-<random>` before anyone is sent to a provider |
| `inbox.receive` | 1) `verify_webhook(headers, raw_body)`; 2) `parse_event`; 3) insert one `billing_events` row, unique on (provider, event id). A bad signature or an unreadable body writes nothing. A second delivery finds the first row |
| `settle.process` | the rules below |
| `api.py` | the route; 404 first (flag off, or no such provider), before the body is read |
| `fake.FakeProvider` | HMAC-SHA256 of the exact bytes in `X-Fake-Signature`; remembers every body it verified, every transaction it was asked for, every charge and refund |

**When a payment becomes `succeeded`** (`settle.py`): the payment row is found by the event's reference and locked (`FOR UPDATE`); the payment is re-read with `fetch_transaction`; then, on the provider's own report and never the webhook's: status is `successful` (still `pending` means try again later, not refused), the reference is the payment's, the currency is what we asked for, the amount is **at least** what we asked for (spec 5.4; a higher amount is accepted). Anything else is refused: the payment is untouched and the reason is written to `billing_events.error`. A payment already `succeeded` is not re-read and runs nothing.

**The grant point.** The first and only time a payment moves to `succeeded`, each function in `settle.GRANT_HANDLERS` runs in the same transaction, row still locked. **The list is empty in S4**; the hour grants of S8 (and the plan period of S5/S8) register there. A handler that raises undoes the whole step. This is where "replay grants once" is enforced: by the status change under the lock, with `hour_grants`' unique (kind, source payment id) as the second line.

| Answer | When |
|---|---|
| 404 | billing off, or no such provider (nothing stored) |
| 413 | body over 256 KB (read in chunks and cut off; nothing stored) |
| 401 | signature wrong or missing (nothing stored; one log line with the provider name only) |
| 400 | genuine, but the body is not an event we can read (nothing stored) |
| 503 | stored but not decided (provider down, still pending, a handler failed): the provider sends it again, and the retry finds the row and tries again |
| 200 | decided (applied **or refused**: a refusal is a final answer, so the provider is not asked to resend) or already seen |

Decisions and where I departed from the spec's wording:

* **Processed in the request, not by a worker.** Spec 5.4 says "answers 200 at once; the worker processes afterwards". There is no worker for this yet and the spec does not say where it runs, so the route decides the event itself and answers 503 when it cannot, relying on the provider's own retries (Flutterwave 3 times 30 minutes apart, Paystack every 3 minutes then hourly for 72 hours; both from the spec's reading of their pages, not tested). **A sweep for events still waiting (`processed_at` null) belongs in the S8 scheduler**, which is also where Flutterwave's short retry window matters.
* **Refused events are stored and answered 200.** The spec says "a bad signature is refused"; I read the wrong amount, currency and reference cases as "refused, not granted, recorded with the reason", since the provider would only send the same thing again.
* **Bad-signature requests are not stored**, although the spec lists `signature_ok` on `billing_events`. Storing them lets a stranger fill the table. The column is kept (so S6/S7 may choose otherwise) and is always true today.
* **`ProviderEvent.event_id` was added to the neutral event** (spec 5.1's list has no dedupe key, but 5.4 needs one). The adapter builds it so a retry gives the same id and two different events about one payment give different ids.
* **`charge_saved_method` takes a `currency`** (spec 5.1 lists token, amount, reference; both vendors need the currency).
* **Webhook payloads are scrubbed before storing**: any key whose name contains `token`, or is `authorization_code`, `signature`, `secret`, `cvv`, `card_number` or `pan`, is replaced with `[removed]`. Vendors put reusable card tokens in webhook bodies, and a token belongs encrypted in `payment_methods`, not in a readable inbox row. The key names are a guess from the spec's reading of the vendors; S6 and S7 must check them against real payloads.
* **No card token is saved in S4.** `payment_methods` is created and empty; nothing writes it until renewals (S8), which will encrypt with `app/vault.py` (a new purpose is added there then).
* **`payments.subscription_id` and `hour_grants.source_payment_id` have no foreign key** (the subscriptions table is S8). Adding the `hour_grants.source_payment_id` foreign key to `payments` is a follow-up for S8's migration.
* **`payer_user_id` is set null when the account is deleted**, so money records survive (spec 6, "keep invoices"); `payment_methods.user_id` cascades (spec 3.7: the saved token is deleted).
* **Rejected:** an in-process background task for processing (it needs its own database session, which the tests' dependency override would not reach); storing the raw body (the signature input is not needed after the check, and it carries tokens).

## What's involved

| File | Change |
|---|---|
| `app/billing/__init__.py`, `types.py`, `provider.py`, `fake.py`, `payments.py`, `inbox.py`, `settle.py`, `api.py` | new |
| `alembic/versions/0050_billing_payments_inbox.py` | the three tables; the downgrade refuses while a payment or a saved card exists |
| `app/models.py` | `Payment`, `PaymentMethod`, `BillingEvent`, `PAYMENT_KINDS`, `PAYMENT_STATUSES`, `BILLING_CURRENCIES` |
| `app/auth.py` | **`WEBHOOK_PREFIX`: `guard` lets `/api/billing/webhooks/` past its login and CSRF check** (a provider has neither; the signature is the credential). The only edit outside `app/billing/` that changes behaviour. The path is open for every method but only a POST has a handler, and the handler's first line is the flag |
| `app/main.py` | registers the billing router after the API router |
| `app/config.py`, `.env.example`, `deploy/README.md` | `BILLING_ENABLED` (default false) |
| `tests/test_billing_inbox.py`, `tests/test_billing_payments.py` | new, 36 tests |
| `tests/test_hours_grants.py` | one assertion dropped: it required 0049 to be the single head, which any later migration breaks (`tests/test_migrations.py::test_single_head` already covers one head) |

## Verification

* **Run locally** (lint, format and the named tests only; CI runs the rest, and has no separate type-check step): `ruff check .` and `ruff format --check .` clean (ruff 0.17.3, the pin); `tests/test_billing_inbox.py` and `tests/test_billing_payments.py` (36 tests), `tests/test_hours_grants.py`, `tests/test_models.py`, `tests/test_edge.py` and `tests/test_migrations.py::test_single_head` and `::test_migration_matches_models` pass. The migration went up from empty to 0050, down to 0049 (refused with a payment present, ran clean without), and up again, on a scratch Postgres 17 that was removed afterwards. The full suite was not run here; CI is the authority.
* **What the tests prove:** the same event twice grants once (and the replay does not even re-ask the provider); two different events for one payment grant once; a failed payment that is later paid grants once; a wrong amount, a wrong currency, a wrong reference and a non-successful status are each refused **while the webhook claims the right numbers**, so the decision is shown to come from the provider's report; a larger amount is accepted; an unknown reference and another provider's payment are refused; a bad, missing, or other-body signature stores nothing and fetches nothing; the verifier is given the request's exact bytes (indented, reordered, non-ASCII, trailing newlines); an unreadable body and an oversize body store nothing; with billing off, or for an unregistered provider, the route is a 404 that writes nothing; a provider that is down, or still says pending, gives 503 and a later retry settles; a grant handler that fails leaves the payment `pending` and the retry works; tokens never reach the stored payload; the route needs no login and no CSRF header.
* **Mutation check** (each break caught by a test): amount check off, currency check off, reference check off, handler runs for an already paid payment, flag check off, signature check skipped, the `auth.py` exemption off, the unique key ignored.
* **Not verified against any real vendor:** every provider shape here is neutral and exercised only through `FakeProvider`. Not checked: that a real Paystack or Flutterwave body maps onto `ProviderEvent` (S6, S7); the exact header names and signature schemes (spec 5.4 reads them from the vendors' pages; none was run); that a real webhook reaches `/api/billing/webhooks/{provider}` **with its body unchanged through nginx and the Next.js proxy** (spec 5.4's "first test is a real test-mode webhook through the live path"; an nginx edit and a rate-limit exemption may be needed); which fields carry card tokens; the vendors' retry timings; that the 256 KB limit is above a real payload. Nothing was run on the server.

## Operate / roll back

1. Deploy: migration 0050 runs with it. `BILLING_ENABLED` unset means off, the route is a 404, and nothing changes for anyone.
2. There is nothing to switch on yet: even with `BILLING_ENABLED=true` no provider is registered, so every webhook is a 404.
3. Roll back the code: revert the PR; the tables are unused by older code. To drop them, `alembic downgrade 0049` (refuses while any payment or saved card exists, which cannot happen before S6).

## Follow-ups

| Item | Where |
|---|---|
| **Migration numbering: 0050 ships with S4, so S5's billing-profile tables cannot be added to it** (the spec says "second half of 0050"); S5 takes the next free revision, and the spec's later numbers (0051 plans and subscriptions, 0052 invoices) move up by one each, or S5 can fold into S8's | lead, spec section 7 |
| A sweep for `billing_events` with `processed_at` null (retries the vendors no longer send), and a pruning job for old payloads | S8 scheduler |
| Register the hour-grant handler in `settle.GRANT_HANDLERS`; add the `hour_grants.source_payment_id` foreign key | S8 |
| Real adapters: map each vendor to the neutral types, confirm the token key names to scrub, the retry timings and the signature headers; register in `PROVIDER_FACTORIES` | S6, S7 |
| Save the reusable card token (encrypted, `app/vault.py` purpose) from the first payment | S8 |
| The nginx/Next.js path for the raw body, the rate-limit exemption and a body limit at the edge | S6 test-mode run |
| Card-country mismatch flag (`payments.country_mismatch` is created, never set) | S5 |
| Refund and dispute events are stored and left waiting (`error` says so) | S9 |
