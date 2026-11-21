# SOP: the read-only live proof script (`scripts/billing_test_purchase.py`)

**What shipped.** One PR, branch `feature/live-proof-script`. The script's read-only commands now work while billing is LIVE: `proof EMAIL` (the first real payment), `proof EMAIL --refund` (the first refund), `state EMAIL`, and a new `live-check` (the live settings and the webhook inbox's health). Everything that writes or moves money (`checkout`, `make-due`) still refuses. The runbook's hand-pasted SQL proofs (sections 5, 6, 8 of `docs/launch/billing-go-live-runbook.md`) became one-line commands; the SQL stays as the fallback. No migration, no endpoint change, no FE change.

## Why

Billing went live on 2026-11-21 (`docs/sop/2026-11-21-billing-go-live-executed.md`). Adebayo makes the first real payment per provider and refunds it, and had to paste SQL by hand to prove each step, reading columns by eye. The script's `proof` was written for test mode (it never checked the invoice series, the cutover or the webhook), and its `preflight` fails on purpose when `BILLING_LIVE` is true. Nothing in the app was wrong; the proof tooling was missing.

## How

| Decision | Why |
|---|---|
| Every read command runs inside `read_only(db)`: `SET TRANSACTION READ ONLY`, then `SHOW transaction_read_only` must say `on` or the run stops before reading, rolled back at the end | The guarantee is the database's, not the code's: a write fails even if a future edit adds one. A test replaces the live proof with a function that runs an `UPDATE` and the run fails with PostgreSQL's read-only error |
| Write commands are refused by `guard`, which now also refuses when a live key (`sk_live_`, `pk_live_`, a Flutterwave key without `_TEST`) or `BILLING_LIVE_SINCE` is present while `BILLING_LIVE` is false (`looks_live`) | A second lock that a flipped flag alone cannot open. `Settings` already refuses that mix at startup, so this only matters for a tampered copy; the test builds one with `model_copy` |
| `proof` picks the live proof when `BILLING_LIVE` is true, the old proof otherwise (unchanged). `--refund` is its own mode | Test mode keeps what S8a documented; live adds the checks live needs |
| New command `live-check` instead of changing `preflight`. `preflight` under live now says "use live-check" | `preflight 1` and `3` are the test-mode switch-on steps and their tests stay as they are |
| Exit codes of the live proof: 0 proved, 1 failed, 2 all checks pass but the webhook path is not proved | The sweeper settling a payment is legitimate, but the first payment exists to prove the webhook, so it must not look like a plain pass |
| An event belongs to a payment when it is the provider's `charge_succeeded`, received at or after the cutover, naming the payment's reference (Paystack `data.reference`, Flutterwave `data.tx_ref`; the test fake puts `reference` at the top) | `billing_events` has no payment column; the reference is the only link the stored payload carries |
| Output never has a key, secret, token, refund id or email: keys are judged by prefix and only a KNOWN prefix is printed (`prefix_of`) | Runbook rule: nothing secret in chat. Rejected: printing the first N characters of a key |

## What's involved

| Path | Change |
|---|---|
| `scripts/billing_test_purchase.py` | `read_only`, `looks_live`, `guard`, `live_proof`, `live_refund_proof`, `live_check`, `run` (the dispatcher `main` calls), `build_parser` |
| `tests/test_billing_test_purchase.py` | 13 new tests on the real Postgres fixtures (24 in the file) |
| `docs/launch/billing-go-live-runbook.md` | sections 5, 6, 8 (command first, SQL fallback), 11 |
| `docs/launch/billing-go-live-code-audit.md` | row 19 updated |
| `docs/checklist/master-checklist.md` | dated block |

What the live `proof` checks (column names from `app/models.py`): `payments.status = succeeded` and `paid_at`; exactly one `subscriptions` row for the person in `active`/`past_due`, status `active`, plan/provider/currency equal to the payment's and `payments.subscription_id` pointing at it; `invoices.number` matches `BN-<year>-NNNNNN` (positive year), `test_mode` false, the payment not before `BILLING_LIVE_SINCE` (`app/billing/cutover.py`), total equals the payment and the parts add up, `invoice_counters` for that year has reached the number; one `hour_grants` row of kind `plan_period` from the payment with `seconds` = the plan's `monthly_seconds` and `expires_at` = the period end (a trial's leftover is printed on the "hours held now" line); a saved card (note); the webhook as above. `--refund`: `refunds.reason = first_plan_7d`, `status = submitted`, `provider_refund_id` present, amount and currency equal the payment, `payments.status = refunded`, the subscription `ended`, the refund's `hour_grants` row ended before its remembered expiry, no saved card for that provider (note). `live-check`: `BILLING_ENABLED` and `BILLING_LIVE`, key prefixes, `FLUTTERWAVE_SECRET_HASH`, `PAYSTACK_WEBHOOK_SECRET` unset (classic `x-paystack-signature`, `app/billing/paystack.py` `verify_webhook`) or set and different from the secret key, `BILLING_LIVE_SINCE` set and past, seller name, address, registration number (tax ID a note), active plans with USD and NGN prices (listed), positive-year invoice counters (informational), unsigned or 5-minute-undecided webhooks since the cutover (fail), pending payments over 5 minutes and payments waiting for a refund (notes).

## Verification

- 24 tests pass on a scratch Postgres 16 (`tests/test_billing_test_purchase.py`): live refuses `checkout` and `make-due`, also with `BILLING_LIVE` flipped off; a write inside the live path fails in the database and leaves the row unchanged; the read-only setting is checked (a lying database stops the run); live proof passes with a signed event (exit 0), says `WEBHOOK PATH NOT PROVED` with none, an unsigned or undecided one (exit 2); fails for a test-mode invoice, a test-era payment and a wrong grant; refund proof fails before a refund and passes after a real `refunds.request`; `live-check` passes for a ready server, fails for each missing precondition, shows no key or secret; `state` and test-mode `proof` still run through the dispatcher.
- Ruff check and format check (the project's pinned 0.17.3) clean.
- **NOT verified:** a run on the real server, against real payment data, or a real signed webhook (the first real payment is the first run; the reference field names of the real Paystack and Flutterwave payloads are taken from the adapters' parsers, not from a stored live event). The full test suite was left to CI.

## Operate / roll back

Run on the server with `docker compose exec -T web python scripts/billing_test_purchase.py <command> </dev/null; echo "exit $?"` (commands above). Nothing to deploy beyond the normal image; nothing to roll back: the commands write nothing. Reverting the PR restores the old script and the SQL proofs in the runbook.

## Follow-ups

- Run it on the first real payments and fix any field the real payloads name differently.
- A `proof` for a renewal and for a pack (not part of the first-payment runbook).
- The pending-payment and refund-waiting lines are notes, not failures; tighten them once real traffic shows what is normal.
