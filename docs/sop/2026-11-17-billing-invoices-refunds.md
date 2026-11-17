# SOP: billing stage S9, invoices, receipts and refunds

**What shipped.** One **invoice** for every payment that was used, numbered `BN-<year>-<six digits>`; a **receipt mail** with the invoice PDF attached; the person's invoice list and PDF download in their payment history; the **admin refund** for the three cases of spec 3.5 (API routes and an admin screen); Paystack's refund with a **mandatory** `reference` and a lost answer as `OutcomeUnknown`; admin alerts for a second charge on a paid reference, a refused successful charge, a dispute, a refund with no answer and payments that waited a day. **No flag of its own**: it works the moment `BILLING_ENABLED` is on for an allow-listed account (administrators use the refund routes whether or not they are on the list). PR: filled in by the PR that carries this file (branch `feature/billing-invoices-refunds`). Stage S9 of `docs/superpowers/specs/2026-11-15-billing-and-plans-design.md` (3.5, 5.5, 6, 7). FE guide: `docs/fe-integration-guide-billing.md`, section "Invoices, receipts and refunds" (captures in `docs/fe-captures/billing-invoices/`, provider scripted).

## Why

S8a turned money into hours but left three things to a person with the provider's dashboard: nobody got a document for what they paid, a payment nothing could use (a double charge) was only an alert, and a refund was not possible from Beyric at all. A refund moves real money and cannot be undone, so the work is mostly about **never paying one twice** and **never giving or taking hours wrongly**.

## How, and what was decided

| Piece | What it does |
|---|---|
| **Invoice per used payment** (`app/billing/invoices.py`) | A grant handler registered right after the subscription handler (`subscriptions.register`), in the same transaction that marks the payment `succeeded`. It runs in a savepoint and **never stops the hours**: a failure is logged by type, the admins are told, and the sweeper (`issue_missing`) issues it on its next pass. A payment nothing could use (`payments.needs_refund_reason`, now written by `subscriptions.needs_refund`) is **not invoiced**: no supply happened. `invoices.payment_id` is unique, so one payment never has two |
| **Number** | `BN-<year>-<six digits>`, year in **Lagos time** (a payment at 23:30 UTC on 31 December is next year's first). Taken with one atomic upsert on `invoice_counters` whose row lock lasts to the end of the issuing transaction: two payments settled together queue on it (proved with two real connections, and eight at once), a rollback gives the number back (**no gaps**). The unique index on `invoices.number` is the second line. **Rejected:** a Postgres sequence (a rolled-back transaction leaves a hole, and tax numbering wants none) and `max(number)+1` (races) |
| **VAT** | `BILLING_VAT_RATE_BP` (default 0) is the VAT **included** in the price charged: `vat = round_half_up(total * rate / (10000 + rate))` in integers, `subtotal = total - vat`, a check constraint holds `subtotal + vat = total`. The rate is stored on the invoice. **VAT added on top is NOT built** (spec 6 says "added or included from the same setting"): it would change every amount a provider is asked to charge, renewals and prorations included, and the accountant has not answered. Zero-rating for customers abroad is not guessed either |
| **Seller and buyer** | The seller comes from `BILLING_SELLER_*` settings, the buyer from the billing profile (else the account), both **snapshotted** onto the invoice. **Nothing is invented**: the registration and tax numbers are empty until Adebayo supplies them, an empty one is left off the invoice, `.env.example` shows clearly labelled PLACEHOLDER values (`RC-PLACEHOLDER-NOT-REAL`) that must not go on a live server, and `preflight 1` notes each empty or placeholder line. An invoice made with test keys says **TEST INVOICE, not a tax document** (`invoices.test_mode`) |
| **PDF** (`app/invoice_render.py`, `app/billing/invoice_pdf.py`) | Built when asked, never stored, in a child process (the same design as the meeting export: killed on a time limit, no secrets in its environment, every text escaped). Two at a time per web process; a third gets 503 `invoice_busy`. A refunded invoice's PDF says REFUNDED. **No credit note yet** (follow-up) |
| **Receipt mail** (`app/billing/receipt_mail.py`) | Queued in the same step, **ids only** in the outbox (`{"invoice_id": N}`), built and sent by the worker: the text and the PDF exist only while it is sent. A new `SESSION_TEMPLATES` kind in `app/mail_templates.py`: account mail, not counted as notes mail, **not stopped by an unsubscribe** (a bounce still stops it). If the PDF cannot be built the mail goes without it and says the invoice is under Billing. The old plain "Payment received" alert mail is gone (the bell entry stays): one mail per payment |
| **Refund rules** (`app/billing/refunds.py`) | Exactly the three cases, **whole payments only, the amount is never taken from the request**. `double_charge`: the payment succeeded and nothing could use it. `first_plan_7d`: the person's first plan payment **ever** (a person refunded once and buying again has no second first payment), within 7 days, no hours used, no later upgrade or renewal. `pack_14d`: a pack within 14 days, none of its hours used. "No hours used" is read as `entitlements` reads hours (allocations **and** the backlog, in the spending order), so trial hours spent first do not block a plan refund; a recording running or being prepared blocks it |
| **Double-refund protection** | (1) one live refund per payment: a partial unique index plus the payment's row lock (two admins pressing at once make one refund and one provider call, tested with two connections); (2) the refund row, with our own `reference`, is committed **before** the provider is called; (3) the provider is called **once**; (4) **no retry, by code or button**: a call with no answer becomes `unknown`, the admins are alerted with the reference, and only a person who looked at the provider's dashboard can resolve it (`made`: the payment becomes refunded; `not_made`: the refund closes and a NEW one may start); a `pending` row left by a dead process is the same after 10 minutes; (5) Paystack gets our reference as its idempotency key (a repeat is a 409, which we treat as unknown, never as "retry"), Flutterwave v3 has none so it is never repeated at all |
| **The hours** | The grant the payment bought is **ended when the refund starts** (under the payment's lock and the person's hours lock, the one `hours.allocate` takes, so a recording counted at that moment is seen, tested with two connections) and given back if the provider refuses or an admin confirms none was made. An `unknown` refund keeps them removed. A first-plan refund ends the subscription (`states.REFUNDED`, a new row in the table) and deletes the saved card; a pack refund touches nothing else |
| **Providers** | `refund(..., reference)` is mandatory in the protocol and the fake, Paystack and Flutterwave. **Paystack**: a timeout, transport error, 5xx, 409, an unreadable answer, an answer without a usable refund id and an answer for another amount are `OutcomeUnknown` (not a `ProviderError`, so no retry-on-error loop catches them); a 4xx, a refused key and a refund Paystack reports `failed` stay `ProviderError`. The fake can script both |
| **Alerts** (`app/billing/notify.py`, `settle.py`, `renewals.sweep`) | By ids only. A second successful charge under a different transaction id for an already paid reference (the provider is asked first, so a stray event is no alert; the S3 follow-up); a successful charge the rules refused; a dispute; a refund with no answer or a stuck `pending` one; one **daily digest** of payments pending 24 to 48 hours (an abandoned or refused checkout and a lost payment look the same). Refund and dispute webhooks are now marked decided (they used to wait for ever, "for S9") |
| **Screens** | Payment history: "Invoice BN-..." and a download button per used payment (one at a time, "Preparing...", the server's sentence on failure). Admin: `/admin/billing` (payments with the server's verdict and reason, a confirm step that says what the case does, the refunds, and the "Waiting for you" list for unknown outcomes); a "Refunds" card in Settings for administrators. LEAD-DESIGNED: no admin or invoice design exists, built from the billing page's lists |

Departures from the spec, on purpose: no `partly_refunded` (whole payments only, the status stays unused); invoice and refund rows have no `team_id` (per person); `invoices.line` is a JSON snapshot of what was bought, not columns; the refund table has no `created_by` foreign key behaviour beyond set null; the spec's "billing profile's VAT fields" are not added (VAT is a setting, the buyer's tax number already exists on the profile).

## What's involved

| Path | What |
|---|---|
| `alembic/versions/0053_invoices_and_refunds.py` | `invoice_counters`, `invoices`, `refunds` (partial unique index: one live refund per payment), `payments.needs_refund_reason`. Downgrade refuses while any invoice or refund exists |
| `app/models.py`, `app/config.py` | `Invoice`, `InvoiceCounter`, `Refund`; `BILLING_SELLER_*`, `BILLING_VAT_RATE_BP` |
| `app/billing/invoices.py`, `invoice_pdf.py`, `app/invoice_render.py`, `receipt_mail.py`, `notify.py` | numbering, VAT, issue, the PDF, the mail, the one admin-alert helper |
| `app/billing/refunds.py` | the rules, the three steps, `resolve`, the stuck alert |
| `app/billing/invoices_api.py`, `admin_api.py`, `subscriptions_api.py` | the person's routes; the admin's routes; `invoice_number` on every payment |
| `app/billing/settle.py`, `renewals.py`, `subscriptions.py`, `states.py` | the alerts and decided refund events; the sweeper steps; the handler registration and `needs_refund_reason`; the `REFUNDED` event |
| `app/billing/paystack.py`, `flutterwave.py`, `fake.py`, `provider.py` | mandatory refund reference, money-call errors |
| `app/mail.py`, `app/mail_templates.py`, `app/hours.py` | the session template kind and its suppression rule; `hours.lock_person` made public |
| `web/components/billing/payment-history.tsx`, `admin-billing-view.tsx`, `settings-billing-admin.tsx`, `web/app/admin/billing/page.tsx`, `web/lib/billing.ts`, `web/lib/api/client.ts`, `web/lib/save-file.ts` | the screens and the client calls |
| `scripts/capture_billing_invoices.py`, `scripts/dev_billing_stack.py`, `scripts/billing_test_purchase.py` | the live captures; the dev stack's S9 scenarios and the administrator `root@example.test`; `proof` now checks the invoice and `preflight 1` the seller's lines and migration 0053 |
| Tests | `test_billing_invoices.py`, `test_billing_refunds.py`, `test_billing_alerts.py`, `test_billing_invoices_api.py`; route inventories in `test_admin.py`, `test_accounts.py`, `test_billing_profile.py`; provider tests; `web/tests/billing-invoices.test.tsx` |

Route inventories: the new routes are all signed-in routes, so they are covered by the generic enumerations in `tests/test_api.py` (login first, CSRF on writes) without a public or CSRF-exempt entry (`PUBLIC` and `CSRF_EXEMPT` are unchanged: only the webhook is exempt); `tests/test_accounts.py` classifies `{number}` like `{reference}` (a payer-only 404), `tests/test_admin.py` lists the four admin routes, `tests/test_billing_profile.py` lists the billing paths; `tests/test_google_signin.py`'s public routes are unchanged.

## Verification

On real Postgres (a scratch container, removed afterwards); no network; no keys.

| What | Proof |
|---|---|
| Invoice numbers under concurrency | `test_billing_invoices.py`: two real connections (the second **waits** on the first's counter lock, then gets the next number), eight at once get eight distinct numbers with no gap, a rolled-back invoice gives its number back, each year counts from one, the unique indexes and the sum check refuse bad rows |
| VAT arithmetic | parametrized values (NGN 25,000.00 at 7.5%: 174,419 VAT, 2,325,581 subtotal), a sweep over rates and totals proving parts add up and the VAT is within one minor unit, nonsense refused |
| Which payments are invoiced | plan, renewal and pack get one each in order; a replayed webhook makes none; a payment nothing could use gets none and no receipt; an invoice failure never stops the hours and the sweeper issues it later |
| The PDF | the real child process; the text laid out by WeasyPrint **and** extracted from the bytes by `pdftotext` (where present) contains the seller, buyer, line, total, payment reference, test notice, VAT lines and refunded notice; hostile text in a name is escaped |
| The receipt mail | ids-only payload, built at send time with the PDF, sent without it when the renderer fails, skipped for a refunded or missing payment, not stopped by an unsubscribe but by a bounce |
| Refund rules | every case and every refusal (`test_billing_refunds.py`: 29 tests), the day-7 and day-14 boundaries to the second, hours used, trial hours spent first, a running recording, not the first payment, a later upgrade, a wrong case, a used payment as a double charge, a deleted account |
| The double-refund risk | two admins at once: **one provider call**; no answer: `unknown`, no retry from either route, resolve both ways, a dead process's `pending` row; a recording counted on another connection while the refund waits for the hours lock refuses it |
| The routes | `test_billing_invoices_api.py`: dark with billing off (admin included), the allow-list, 401, 403, CSRF, another person's invoice a 404, the PDF headers and bytes, busy and failed renderer, every admin refusal code, bad bodies |
| Providers | Paystack's refund: reference mandatory, every lost-answer shape `OutcomeUnknown`, refusals `ProviderError`; Flutterwave's reference mandatory |
| Alerts and sweeper | `test_billing_alerts.py`: second charge (once, only when the provider confirms, parked when it cannot be asked), refused charge, dispute, the daily digest, invoices issued by the sweeper, one failing step does not stop the others |
| Live over HTTP | `scripts/capture_billing_invoices.py`: 70 captures, the real app under uvicorn, real signed webhooks, the real PDFs and their extracted text kept beside them |
| In a browser | the local app with the scripted provider: payment history with downloads and the refund screen at 1440, 768 and 390 wide (screenshots in the lead's notes folder `billing-s9/`), a real click downloaded `invoice-BN-2026-000001.pdf`, a real click on the refund confirm refunded the payment |
| Migration | one head; model and migration agree; upgrade, downgrade, upgrade (`test_migrations.py`) |
| Lint and types | `ruff check .`, `ruff format --check .` (0.17.3, as CI); web `npm run lint`, `npx tsc --noEmit` |

**Not verified, and why.** *Everything about a real provider's refund:* no real Paystack or Flutterwave was called (no keys here); the real refund answers, `refund.processed` / `refund.failed` events, how long a card refund takes, and a Paystack 409 for a repeated reference are from their documentation. *A real timeout* is scripted (`refund_error`). *A real mail provider* delivering the receipt (a recording mailer built the message). *The seller's real numbers* (not supplied). *Nothing is deployed.*

## Operate / roll back

**Switch-on proof, env values only** (nothing new to flip: invoices and refunds come with billing). `P=docker compose exec -T web python scripts/billing_test_purchase.py`.

| Step | Do | Proof | Must print |
|---|---|---|---|
| 0. Deploy | migrations to 0053 (`alembic upgrade head`) | `docker compose exec -T web alembic current` | `0053 (head)` |
| 1. The seller's lines (before any LIVE payment) | in `.env`: `BILLING_SELLER_ADDRESS`, `BILLING_SELLER_REGISTRATION_NO`, `BILLING_SELLER_TAX_ID`, `BILLING_SELLER_EMAIL` (real values from Adebayo and the accountant), `BILLING_VAT_RATE_BP` (the accountant's answer, default 0); recreate web, worker and scheduler | `$P preflight 1` | `[ok] migration 0053 is applied`, and no `[note] BILLING_SELLER_... is set` line left (a note per empty or placeholder line); `BILLING_VAT_RATE_BP` printed |
| 2. A test-mode purchase | the S8a runbook (`$P checkout ...`) | `$P proof test@account.example` | `PROOF: OK` including `[ok] that payment has exactly one invoice` and `[ok] the invoice is for what was paid, and its parts add up`, and a `receipt mail:` line (`pending`/`sent`, or a `[note]` when no mail provider is set) |
| 3. The PDF | signed in as the test account, `/billing` > Payments > Download invoice | the file `invoice-BN-<year>-<number>.pdf` opens and says TEST INVOICE | |
| 4. A refund (test mode) | signed in as an administrator, `/admin/billing`, the test payment, Refund | the row shows "Refund sent"; `$P state test@account.example` shows no plan | `state: none` |

**Order of invoice years.** A number is taken when the invoice is issued, in the Lagos year of the payment. Do not edit `invoice_counters` by hand.

**An unknown refund** (the alert says so). Look in the provider's dashboard for the reference in the alert (`bnrf-...`: Paystack shows it as the refund reference, Flutterwave as the refund's `comments`). Then on `/admin/billing`, "Waiting for you": "The refund exists" or "No refund exists". Never start a new refund before resolving.

**Roll back.** `BILLING_ENABLED=false` and recreate web and scheduler: every route, admin ones included, is a 404 again and the clock stops. Invoices, refunds and counters stay. The migration's downgrade refuses while any invoice or refund exists (export, then delete by hand if you must).

## Follow-ups

| Item | Where |
|---|---|
| **Credit note** for a refunded invoice (the PDF says REFUNDED; Nigerian rules for a credit note and for a VAT invoice's contents are the accountant's) | accountant, then S11 |
| **VAT added on top** of the price, zero-rating for customers abroad, e-invoicing (spec 6) | accountant |
| The seller's real registration, tax number, address | Adebayo |
| **Provider refund events** (`refund.processed`, `refund.failed`) are stored and marked decided but do not change a refund's state: a refund Paystack later reports failed is not noticed. Poll the provider by reference, or act on the event | later |
| An automatic lookup of an `unknown` Paystack refund by reference (`GET /refund/:reference`) instead of the person looking at the dashboard; Flutterwave has none | later |
| Paystack's **saved-card charge** still raises a plain `ProviderError` for a timeout (S6 follow-up): the clock already treats it as unknown, but only the refund moved to `OutcomeUnknown` here | S6 follow-up |
| A partial refund, and a refund for a real service failure on our side outside the three cases (by hand in the provider's dashboard today; nothing in Beyric records it) | decision |
| An invoice list page of its own (the payment history carries the downloads) and a billing email field on the invoice screen | later |
| The invoice PDF's look is lead-designed and plain; the designer may redraw it | designer |
| A refund unresolved for a long time has no reminder beyond the first alert | later |
| `payments.needs_refund_reason` stays null for payments that needed a refund before this migration (their alert exists): refund those by hand | noted |
| The refused-charge alert can arrive twice for one incident (once from the webhook, once from the sweeper's lookup): different keys, harmless | noted |
