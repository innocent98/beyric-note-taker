# SOP: billing stage S6, the Paystack adapter (naira)

**What shipped.** `app/billing/paystack.py`: the `PaymentProvider` for Paystack, naira only. It starts a hosted checkout (amount in whole kobo, our own reference, the callback address), re-reads a payment from Paystack by id (`fetch_transaction`) or by our reference (`verify_reference`), verifies webhooks on the raw body (both signature header forms), charges a saved card, and refunds. It registers itself as `paystack` in `provider.PROVIDER_FACTORIES`, so the route `POST /api/billing/webhooks/paystack` and `routing.py`'s `paystack` entry resolve to it. **Dark and never run against a real Paystack:** `BILLING_LIVE` defaults to `false`, no key exists yet, and with no secret key set the adapter cannot make any call. PR: #222, branch `feature/paystack-adapter`. Stage S6 of `docs/superpowers/specs/2026-11-15-billing-and-plans-design.md` (sections 5.1, 5.3 to 5.5, 5.7, 8). FE guide: `docs/fe-integration-guide-billing.md`, section "Paystack adapter" (no route the browser calls changed). Live capture of the webhook route: `docs/fe-captures/paystack-webhook/`.

## Why

S4 wrote the money rule once, against a fake: a webhook is a claim, the payment is re-read from the provider, and only a matching report marks it paid. S6 is the first real vendor behind that rule. Its job is to turn Paystack's shapes into the neutral types without ever deciding "paid" itself, and to fail loudly (never as a quiet success) when Paystack's answer is not what its pages promise.

## How, and what was decided

| Piece | What it does |
|---|---|
| Hosted checkout | `POST /transaction/initialize` with `{email, amount: "<kobo>", currency: "NGN", reference, callback_url}`. The amount is sent as text of a whole number (the docs type it as text); no float is ever built. The returned `reference` must be ours and the address must be `https` on `paystack.com`, else `ProviderError` |
| Verify | `fetch_transaction(id)` is `GET /transaction/:id`; `verify_reference(reference)` is `GET /transaction/verify/:reference` (for the payer's return and a payment whose webhook never came, stage S8). Both give a `ProviderTransaction`; `settle.py` still compares reference, currency and amount |
| Webhook, new header | `Paystack-Signature: t=<unix>,v1=<key id>:<128 hex>[,v1=...]`: HMAC SHA512 of `t`, a dot and the raw body, keyed with the whole `whsec_` secret; `t` must be within 300 seconds of our clock; **every** `v1` is compared (constant time, no early exit); any one matching is enough (a rotation carries two); other fields are ignored |
| Webhook, old header | `x-paystack-signature`: HMAC SHA512 of the raw body keyed with the secret key, no time. **Used only while `PAYSTACK_WEBHOOK_SECRET` is unset.** Once it is set the timestamped header is required and the old one alone is refused (a captured body plus its old signature has no clock and would replay for ever). A bad timestamped signature is never rescued by a good old one |
| Events | Paystack sends **no event id**, so the dedupe key is `<event name>:<object id>` (`charge.success:302961`, `refund.processed:rr:<refund reference>`, `charge.dispute.create:id:<dispute id>`). Only `charge.success`, the four `refund.*` and the three `charge.dispute.*` are read; anything else is `IgnoredEvent`. A `charge.success` whose reference we could not have made (over 64 characters or odd characters: another product on the same Paystack account) is `IgnoredEvent` too. **Refund and dispute events are never lost to a null field** (review fix): the refund reference, the transaction reference and the amounts are claims nobody reads, so when Paystack leaves one null the event is still stored (key `rr:<refund reference>`, else `id:<refund id>`, else `body:<hash of the body>`, each prefixed so they cannot collide and **never the transaction reference alone**, which two partial refunds of one payment would share; stable for a redelivery; placeholder `unknown` / `0` / `NGN` for the missing claims) and waits for S9; only a body whose `data` is not an object is a 400 |
| `IgnoredEvent` (new, `types.py`, `api.py`) | A genuine, signed event we do not act on (`ping`, invoices, transfers, subscriptions) is answered **200 `{"status":"ignored"}` and stored nowhere**. Without it the route would 400 and Paystack would resend every one for 72 hours. Only a request that passed the signature check can be ignored |
| Statuses | `success` is `successful`; `failed` and `reversed` are `failed`; `abandoned`, `ongoing`, `pending`, `processing`, `queued` are `pending` (never `failed`: a failed payment can no longer be paid; closes the S4 follow-up). Paystack sends webhooks **only for successful** transactions, so a failure is learned only by asking |
| Strict answers (closes the #212 review items for providers) | A Paystack answer is used only if its id is a whole number (unsigned 64-bit), its reference is text, its **amount is a whole number** (a float such as `5000.0` is refused, never rounded), its **currency is three upper-case letters** (`ngn` is refused, never upper-cased) and its status is documented. The shape is checked **before** the status, so a `pending` answer with a broken amount is an error too. The refusal is a `ProviderError`: the route answers 503, the event stays waiting, the payment stays `pending`, and Paystack's resend (or `verify_reference`) settles it once the answer is well formed. `settle._malformed` stays as the second line behind it |
| Saved card | `POST /transaction/charge_authorization` with the token, the **email that made the card** (Paystack: only that email can charge it), whole kobo and our reference. A decline is HTTP 200 with `status: failed`, mapped to a `failed` transaction (not an exception). A card the bank wants challenged again (`paused: true`) is `pending` and **carries `payer_action_url`** (new field on `ProviderTransaction`, checked to be `https` on paystack.com) where the payer must be sent; its `provider_tx_id` is our own reference, not a Paystack id (do not pass it to `fetch_transaction`). **The answer is checked against the request**: another reference, another currency or a lower amount is a `ProviderError` (outcome unknown: resolve with `verify_reference`), never a result; callers must still settle through `settle`'s rules before granting. A timeout leaves the outcome unknown: the reference is unique, so the caller resolves it with `verify_reference` before charging again (a repeat is refused as a duplicate reference) |
| Refund | `POST /refund` `{transaction: <id>, amount: <int kobo>, reference: <ours>}`; returns Paystack's refund id. The refund API documents an optional unique `reference` (8 to 64 letters, digits, `_`, `-`) and a 409 Conflict answer, so `refund(..., reference=...)` (new optional keyword on the protocol and the fake) lets S9 pass a key made from its own refund row: a retry after a timeout is refused as a conflict (a `ProviderError`), not refunded twice. **S9 must pass one;** without it a retried partial refund can refund twice. The answer's amount must equal the amount asked, else `ProviderError`. It is queued, not done: `refund.processed` arrives later (stored and left waiting for S9) |
| Card country | `authorization.country_code` goes to `ProviderTransaction.card_country` untouched; `mismatch.py` normalises it and never refuses a paid payment |
| No call without a key | Every call goes through one function that raises `NotConfigured` **before opening any connection** when there is no secret key, or when the key is not `sk_test_` while `BILLING_LIVE` is false (not `sk_live_` while true). `app/config.py` stops the process at startup for the same mismatch, for a blank `PAYSTACK_WEBHOOK_SECRET` and for a webhook secret with no secret key, naming the setting and never printing the key. A key that is not plain ASCII is refused as not configured (it could not be sent in a header). A client timeout of 10 seconds read, no redirects, no proxy from the environment; error messages carry no key, address, body or email |
| The fake | `provider_named` returns nothing for `fake` while `BILLING_LIVE` is true, even if something registered it (closes the S4 follow-up) |

Decisions and where I departed from the brief or spec:

* **`charge_saved_method` gained an optional keyword, `payer_email`** (protocol, fake and Paystack). The brief's signature has no email, but Paystack's docs say an authorization can be charged only with the email that created it. The fake ignores it; Paystack raises `ProviderError` if it is missing. S8 passes `payment_methods.email_at_provider`. This is the one shared-file change that S7 should know about (Flutterwave also takes an email).
* **"Flag off: no call may leave the process" is implemented as "no key of the right kind, no call".** `BILLING_LIVE=false` means TEST keys, so Adebayo's test-mode round trip is possible with the flag at its default; but with the defaults (no keys) nothing can be sent, and a live key is refused while the flag is off. If the lead wants a stricter second switch (no network even with test keys), say so: it is a few lines.
* **The malformed-answer outcome is an exception, not a normalised value.** The #212 review suggested normalising lossless cases (upper-casing a currency, converting `5000.0`). The brief says a float or lower-case currency is malformed, so the adapter refuses and does not coerce; the retryable 503 means nothing is lost.
* **Webhook amounts are checked strictly too** (an event with a float amount is a 400 and stores nothing): the event's amount is never read, but a body that is not the documented shape is not an event we understand.
* **Alternatives rejected:** accepting the old header alongside the new one (replay); using Paystack's own subscriptions (spec 5.3: we keep the clock); a normalising adapter (above); sending the amount as a number (the docs type it as text).

## What's involved

| Path | Change |
|---|---|
| `app/billing/paystack.py` (new) | the adapter, `NotConfigured`, `from_settings`, registration |
| `app/billing/__init__.py` | imports `paystack` so it registers (one line per adapter) |
| `app/billing/types.py`, `app/billing/api.py` | `IgnoredEvent`; the route answers 200 `ignored` |
| `app/billing/provider.py`, `app/billing/fake.py` | `payer_email` keyword on `charge_saved_method`; `fake` refused while `BILLING_LIVE` |
| `app/config.py` | `billing_live`, `paystack_secret_key`, `paystack_public_key`, `paystack_webhook_secret` (all `repr=False`, empty by default); a startup check that keys match `BILLING_LIVE` |
| `.env.example`, `deploy/README.md` | the four setting names (no values) and the webhook address to set in the dashboard |
| `scripts/paystack_test_run.py` (new) | the test-mode round trip by hand (below). Exits before touching the database when `BILLING_LIVE` is true; the pending payment is committed only after Paystack gave a checkout, so a failed run leaves no row |
| `scripts/capture_paystack_webhook.py` (new), `docs/fe-captures/paystack-webhook/` | the live capture of the webhook route |
| `tests/test_billing_paystack.py`, `tests/test_billing_paystack_flow.py`, `tests/fixtures/paystack/` (new) | the tests and the documented samples |
| `docs/fe-integration-guide-billing.md`, `docs/checklist/master-checklist.md` | guide section and the S6 lines |

No migration. No table changes. Nothing outside `app/billing/` reads Paystack's field names.

## Pages read (2026-11-17, in the in-app browser: Paystack answers `curl` and the fetch tool with 403)

| Page | What I took from it |
|---|---|
| https://paystack.com/docs/payments/webhooks/ | both signature headers, the 300-second window, several `v1` values during a rotation, `t.body` signing, constant-time compare, retry timings, the IP list, the `charge.success`, `refund.processed` and `charge.dispute.create` samples (no event id in any of them; the refund `amount` is text) |
| https://paystack.com/docs/api/transaction/ | initialize, verify, fetch-by-id, charge-authorization (needs `email`), and the `authorization` object (`reusable`, `country_code`) |
| https://paystack.com/docs/api/refund/ | `POST /refund`, the answer (`data.id`, `status: pending`), 400 and 409 cases |
| https://paystack.com/docs/payments/verify-payments/ | the transaction statuses; "webhook events for just successful transactions"; check `data.status`, not `status` |
| https://paystack.com/docs/payments/recurring-charges/ | store the authorization **and the email**; only that email can charge it; the `paused` answer for a challenged card |
| https://paystack.com/docs/payments/test-payments/ | the test cards (successful reusable `4084 0840 8408 4081`, declined `4084 0800 0000 5408`, no token `5078 5078 5078 5078 53`, insufficient funds `4084 0800 0067 0037`, refund scenarios) |
| https://paystack.com/docs/api/ | base URL `https://api.paystack.co`, amounts in the sub-unit, `NGN` minimum 50.00 |
| https://paystack.com/docs/api/errors/ | HTTP codes ("always a 200 for a charge or verify": read `data`), the error body (`status`, `message`, `type`, `code`) |
| https://paystack.com/docs/api/authentication/ | key prefixes `sk_test_`, `sk_live_`, `pk_test_`, `pk_live_`; `Authorization: Bearer <secret key>` |

The webhooks page was read by two people on 2026-11-17 (me, and the lead, in a browser): the header form `Paystack-Signature: t=<unix>,v1=<key id>:<hex>` (key id starts `whk_`), the signature as lowercase hex HMAC SHA512 of `t`, a dot and the raw body keyed with the **whole `whsec_` secret including the prefix**, the 300-second window, several `v1` values, separate test and live secrets (Developers > API configuration), the three IP addresses and `x-paystack-signature` staying while the account has a secret key are all on that page. The code follows each point: `t` must be digits and appear once, a `v1` without a `:` is ignored, the signature must be 128 lowercase hex, the secret is used whole. What is **not** confirmed is that our account's dashboard has a `whsec_` secret and sends the header: if it does not, leave `PAYSTACK_WEBHOOK_SECRET` unset and the old header is used. The refund, transaction (initialize, verify, fetch, charge authorization), verify-payments and recurring-charges pages were read by me in the in-app browser (table above); nobody else has read them yet.

Paystack's webhooks page prints **no complete signature vector** (its sample header is elided with `...`). The vectors in the tests are therefore **computed from the documented algorithm with `openssl`**, over the exact bytes of a fixture file, with invented secrets and the sample header's own timestamp (`1760270400`); see `tests/fixtures/paystack/README.md`. They prove the adapter agrees with an independent HMAC implementation, not that Paystack's own bytes verify.

## Verification

* **Unit tests, no network, no keys, no database** (`tests/test_billing_paystack.py`, 363 cases): the openssl vectors (new header, old header, both rotation secrets); a tampered body, a re-encoded body, a rewritten timestamp; the window at 0, ±300 (accepted) and ±301 (refused); two signatures either one enough, two that miss; every signature compared with no early exit; unreadable and duplicate-field headers; the old header refused once a webhook secret is set; events mapped (kobo are `int`, the event id is stable on a redelivery and differs between a charge and a refund, a token only from a reusable card, a text refund amount read as whole kobo); every documented status mapped and unknown ones refused; a float, text or bool amount, a lower-case currency and a missing id or reference refused **for both `success` and `ongoing`**; checkout, charge, refund request bodies asserted byte by byte (whole kobo, no decimal point); a declined and a paused charge are results, not exceptions; HTTP 500, 502, 429, 401, 404, 400, 409, non-JSON, JSON that is not Paystack's shape, a redirect, timeouts and connection errors are explicit `ProviderError`s that leak no key, address or email; no call leaves without a key of the right kind (eight key cases by five calls); the settings reject a key of the other kind and keep keys out of the repr. A socket guard fails any test that opens a real connection.
* **Through the real route and Postgres** (`tests/test_billing_paystack_flow.py`, 51 cases): a signed `charge.success` is paid only after the re-read; a replay grants once and asks Paystack once; wrong amount, currency, reference and status (six cases) are refused whatever the webhook claims; a higher amount is accepted; a float or text amount, a lower-case currency and a float id leave the payment `pending` (503) and the retry settles it; `ongoing` is tried again later; Paystack down, 401, non-JSON, timeout and connection error are 503; a tampered body, a stale timestamp, the wrong secret and no header store nothing; two signatures accepted; the old header works only until a webhook secret is set; no keys set means 401; billing off means 404; ignored events are 200 and stored nowhere; a refund event is stored and left waiting; the stored event holds no card token; the runbook script's functions.
* **Mutation checks** (each break caught by a test): the window widened, a float accepted as an amount, lower-case currency accepted, the old-header fallback, the key-kind check removed.
* **Live capture of the webhook route** (`scripts/capture_paystack_webhook.py`: real uvicorn, real migrations, real HTTP, real settlement, the real adapter; **Paystack's API side scripted from the documented samples**): 15 files, copied into the FE guide.
* **Locally run:** `ruff check .`, `ruff format --check .` (the CI lint steps, ruff 0.17.3 from the project's own virtualenv), and the tests of the files I touched or added (`tests/test_billing_paystack.py`, `tests/test_billing_paystack_flow.py`, plus `test_billing_inbox`, `test_billing_payments`, `test_billing_routing`, `test_billing_mismatch` because I touched the shared files they cover). Never the full suite; CI runs it.

### Not verified live (no Paystack test keys yet)

Nothing below was run against a real or test-mode Paystack. Every Paystack shape in the code, the tests and the capture comes from a page named above.

* A real test-mode round trip: initialize, pay with a test card, the webhook arriving, the re-read by id, a saved-card charge, a refund.
* The raw body reaching the app **unchanged through nginx and the Next.js proxy** (the signature is over the exact bytes; spec 5.4 says the first test is a real webhook through the live path). An nginx edit (his one-line `sed`), a rate-limit exemption for `/api/billing/webhooks/` and an edge body limit may be needed.
* That a real `charge.success` carries the documented fields (the page's sample is from 2016 and has no `reusable`; the verify sample does), and what `authorization.country_code` holds for a foreign card.
* That our account actually sends `Paystack-Signature` (the page documents it; whether this dashboard has a `whsec_` secret is not checked), and the secret's behaviour on rotation.
* That `GET /transaction/:id` (used because the webhook gives the id) answers the same shape as `GET /transaction/verify/:reference`: the page shows both with the same sample.
* Paystack's documented IP list is **not** used (signature only). Adding it at nginx is a possible hardening.

## Runbook: the first Paystack test-mode run (his steps; nothing here has been run)

**Purpose:** prove, with no real money, that a real Paystack payment reaches our database as `succeeded`, through the real address, nginx and the proxy. **You need:** the Paystack account's **test mode** keys. Put them in the server's `.env` only: never in chat, git or a ticket.

1. In the Paystack dashboard switch to **Test mode**. Copy the test **secret key** (`sk_test_...`) and **public key** (`pk_test_...`) from the Developers overview page, and the **webhook signing secret** (`whsec_...`) from Developers > API configuration.
2. On the server, add to `.env` (replace the three values; leave `BILLING_LIVE` unset or `false`):
   ```text
   BILLING_ENABLED=true
   BILLING_LIVE=false
   BILLING_ALLOWED_EMAILS=<your own account's email>
   PAYSTACK_SECRET_KEY=sk_test_...
   PAYSTACK_PUBLIC_KEY=pk_test_...
   PAYSTACK_WEBHOOK_SECRET=whsec_...
   ```
   Recreate web, worker and scheduler (`docker compose up -d --force-recreate web worker scheduler`). A live key, or a key of the wrong kind, stops them at start with a message naming the setting.
3. In the dashboard (test mode) set the webhook URL to `https://notes.beyrictech.com/api/billing/webhooks/paystack`. If your dashboard has a test-webhook button, press it. **What proves the path:** Paystack shows `200` for the delivery, and `docker compose logs web` has no `refused: bad signature` line for it. A `ping` is answered `200 {"status":"ignored"}` and stored nowhere. If the log says bad signature, the body or a header changed on the way (nginx or the proxy): stop and tell the lead.
4. Make a test payment for your own account (the script creates a `pending` payment, asks Paystack for a checkout, and prints the link; it sends nothing without a test key):
   ```bash
   docker compose exec -T web python scripts/paystack_test_run.py checkout you@example.com --naira 50 </dev/null
   ```
   It prints a `reference` and a link. Open the link and pay with the reusable test card `4084 0840 8408 4081` (any future expiry, CVV `408`).
5. **What proves it works:** within a few seconds
   ```bash
   docker compose exec -T web python scripts/paystack_test_run.py status <reference> </dev/null
   ```
   shows `status: succeeded`, a `provider_tx_id`, `card_country: NG`, and one event `charge.success:<id>` with `decided: True` and `error: None`. Then
   ```bash
   docker compose exec -T web python scripts/paystack_test_run.py verify <reference> </dev/null
   ```
   shows `status: successful`, the amount in kobo, `card_can_be_charged_again: True`. That one real delivery is the proof that the raw body survives nginx and the proxy, that the signature scheme matches, and that the event shape is the documented one.
6. Then, one at a time with the same script: the declined card `4084 0800 0000 5408` (`status` stays `pending`; no webhook comes for a failed payment, `verify` says `failed`); the card that makes no token `5078 5078 5078 5078 53` (pays, `card_can_be_charged_again: False`); and re-send the first event from the dashboard (**the replay proof:** `status` still shows one event row and the payment unchanged).
7. If anything differs, copy the `status` and `verify` output (no keys appear in it) to the lead. Not covered by the script: the saved-card charge and the refund (they need the S8 payment-method storage and the S9 refund action); the unit tests carry them until then.
8. To switch off: delete the three Paystack lines and set `BILLING_ENABLED=false`, recreate web, worker and scheduler.

## Operate / roll back

1. Deploy: no migration. With the defaults nothing changes for anyone: `BILLING_ENABLED` false means the route is a 404; with it true and no keys every Paystack webhook is a 401 and no call can be made.
2. Switch on for the test run only with the runbook above. `BILLING_LIVE=true` and live keys are for go-live (S11), with the lead's and Adebayo's explicit yes (it moves real money).
3. Roll back: revert the PR. The tables are unchanged. Remove the Paystack lines from `.env`.

## Follow-ups

| Item | Where |
|---|---|
| **A found S4 bug (not changed here):** if a provider's report names a transaction id another payment already holds, `settle._mark_paid` answers 500 instead of the intended "transaction already used" refusal: `db.begin_nested()` autoflushes the pending `UPDATE` before its savepoint exists, so the unique violation poisons the outer transaction and `db.refresh` raises `PendingRollbackError`. Reproduced by the capture script (before I gave each payment its own transaction id). Needs a Paystack bug to happen (ids are unique), so low urgency; the fix is to flush inside the savepoint. | lead, S4 file `app/billing/settle.py:171` |
| `charge_saved_method` takes `payer_email`; S8 must pass the email Paystack saw on the first payment (`payment_methods.email_at_provider`), and must store it with the token | S8 |
| Resolve an uncertain charge (timeout) with `verify_reference` before charging again; store the renewal's reference before calling | S8 |
| `queue: true` for scheduled charges (spec 5.3) is not used: an immediate answer was simpler to reason about. Decide when the scheduler exists | S8 |
| S9 must pass `reference=` to `refund` (made from its refund row) and treat a 409 as "already created", then look the refund up before retrying | S9 |
| Refund and dispute events are stored and left waiting (their claim fields may be placeholders); `charge.dispute.remind` repeats with the same key and is deduped | S9 |
| A sweeper for events whose retries ran out (Paystack: every 3 minutes for 4 tries, then hourly for 72 hours): `verify_reference` is the tool for a payment whose webhook never came | before live, S8 |
| An nginx rate-limit exemption and body limit for the webhook path; consider Paystack's published IP list as a second check at the edge | the test-mode run |
| `charge_saved_method` and `refund` answers' remaining fields are not compared with anything but the request (reference, currency, amount); a refund's `currency` is not checked | S9 |
| Payload retention: the stored event still holds the payer's email, name and the card's first six and last four digits (card token and card signature are removed) | decision, S8 |
| Paystack's fee is not modelled: the amount charged can exceed the amount asked (documented sample); VAT on Paystack's fees is still unconfirmed (spec section 12) | spec |
| Flutterwave's adapter (S7) adds its own registration line in `app/billing/__init__.py` and its settings beside these | S7 |
