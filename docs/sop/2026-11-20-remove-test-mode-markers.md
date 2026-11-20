# SOP: no test-mode wording anywhere a person can see it

**What shipped.** One PR, branch `chore/remove-test-mode-markers`. Billing runs on the providers' TEST keys until go-live, and the app used to say so in four places: the receipt mail ("Test mode: no real money moved. This is not a tax document."), the invoice PDF banner ("TEST INVOICE: paid with test keys..."), the invoice number (`BN-T-2026-000001`) and the `test_mode` field of the invoice list. All four are gone. A person, an administrator or an API client now sees the same invoice, receipt and numbers as in production. PR: see the branch on GitHub (the lead links it).

## Why

Owner decision, Adebayo, 2026-11-20: "remove everything, everything should be gone", and "do not [add a test-mode banner], anywhere". The experience must be identical to production, so nothing may tell a person the payment was a test.

## How

| Marker | Before | After |
|---|---|---|
| Receipt mail line | an extra muted paragraph when `invoice.test_mode` | removed (`app/billing/receipt_mail.py`) |
| PDF banner | `TEST INVOICE: ...` in `document()["banners"]` | removed; `banners` holds only the REFUNDED notice (`app/billing/invoice_pdf.py`) |
| `test_mode` in `GET /api/billing/invoices` | returned, typed in `web/lib/billing.ts` | removed from `InvoiceOut` (`app/billing/invoices_api.py`) and from the FE type; nothing in the FE ever rendered it |
| Invoice number `BN-T-...` | shown in the list, payments list, PDF, file name, mail subject and body, admin payments | every surface calls `invoice_number.shown()` |

### The numbering decision

What is **kept** internally, unchanged: an invoice made while `BILLING_LIVE` is false takes its number from its own counter (`invoice_counters` under the negative year) and is stored as `BN-T-<year>-<n>`; `invoices.test_mode` stays as a server-only flag. Both exist so a test purchase never uses up a live number and the unique index on `invoices.number` can never fire between the two series. No migration, no stored row rewritten.

What changed is only what is **shown**. `app/billing/invoice_number.py` (`shown`, `stored_candidates`) maps the stored string to a display string:

| Stored | Shown |
|---|---|
| `BN-2026-000123` (live) | `BN-2026-000123` (as is) |
| `BN-T-2026-000011` (internal series) | `BN-2026-900011` (the counter plus 900,000) |

Why this and not "just drop the `T-`": dropping it would show `BN-2026-000011` for a test-era invoice, and at go-live the first live invoice of a year is also `BN-2026-000001`... so one displayed number could belong to two rows, and the PDF route (which looks a number up) could not tell them apart. Moving the internal series to the top of the six-digit range makes the mapping **one-to-one** and keeps it clear of the live numbers until a year has 900,000 live invoices. So, unlike the brief's worst case, **a displayed number does not repeat between a test-era invoice and a live one**. Rejected alternatives: a one-time go-live step marking test-era invoices superseded (a manual step that can be forgotten, and the number would still repeat until it ran); a data migration renaming stored rows (touches money records, and other open work may add migrations).

Old rows (made before this change, stored `BN-T-`) need nothing: they are mapped on the way out, so they list and download. The PDF route turns the number it is given back with `stored_candidates` and looks the row up under either spelling. The old spelling in a URL (`/invoices/BN-T-.../pdf`) is now a 404: the `NUMBER` pattern in `invoices_api.py` accepts only the shown shape, so the old alias is not kept alive. (Nothing links to a number: the mail carries a "Open billing" button, and the FE builds the path from the list.)

### Kept on purpose (internal safety, not indicators)

- `BILLING_LIVE` and the start-up checks that refuse to mix test and live keys with it (`app/config.py` about lines 257 to 300, `app/billing/paystack.py`, `flutterwave.py`, `provider.py`). Their messages go to the operator's log, not to a person.
- `invoices.test_mode` column, `number_text(..., test=)` and `next_number(..., test=)` in `app/billing/invoices.py`: the separate counter.
- The operator runbook's output (`scripts/billing_test_purchase.py`: "BILLING_LIVE is false (test keys only)") and `docs/fe-captures/billing-invoices/70-operator-preflight.json`: operator tooling, never shown to a person.
- Comments and docs about how the providers' test keys work.

### Not under our control

The provider's own hosted checkout page (Paystack, Flutterwave) may show its own test banner while we use test keys. That is the provider's page; it goes away with live keys.

## What's involved

| File | Change |
|---|---|
| `app/billing/invoice_number.py` (new) | `shown` (line 29), `stored_candidates` (line 39), `INTERNAL_SERIES_OFFSET = 900_000` |
| `app/billing/receipt_mail.py` | number shown in subject, summary and rows; test paragraph removed |
| `app/billing/invoice_pdf.py` | file name, `document()` number and footer shown; banner removed |
| `app/billing/invoices_api.py` | `test_mode` field removed; `NUMBER` is the shown shape; list shows `shown`; the PDF lookup uses `stored_candidates` |
| `app/billing/subscriptions_api.py` | `_invoice_numbers` (the payment's `invoice_number`) shows `shown` (one line plus the import) |
| `app/billing/admin_api.py` | the admin payments' `invoice_number` shows `shown` |
| `app/billing/invoices.py`, `app/models.py` | docstrings only: the `T-` is internal; `test_mode` is server-only |
| `web/lib/billing.ts` | `test_mode` removed from the invoice type |
| `scripts/capture_billing_invoices.py` | no `test_mode` in the rows dump; numbers shown |
| `docs/fe-integration-guide-billing.md`, `docs/fe-captures/billing-invoices/` | re-captured from the running app; prose updated |
| `docs/sop/2026-11-17-billing-invoices-refunds.md`, `docs/HANDOFF-master.md`, `docs/checklist/master-checklist.md` | the marker wording replaced; the "BN-T- must not appear after go-live" check removed |

## Verification

- `tests/billing_support.py` has `assert_no_test_wording` (a regex over test mode, test invoice, test keys, sandbox, no real money, not a tax document, `BN-T-`). It runs on the receipt mail (subject, text, html, attachment name), the PDF (the layout text, the `pdftotext` text, the file name), the invoice list, the payments list and the admin payments.
- `tests/test_billing_invoices.py` (new tests at the end): the mapping is one-to-one and round-trips; test-era and live numbers never share a shown value; a test-mode invoice and the same payment issued live read the same apart from the number; with `BILLING_LIVE=true` the live counter numbers exactly as before (`BN-2026-000001`, the internal counter untouched).
- `tests/test_billing_invoices_api.py`: no `test_mode` key; a row stored `BN-T-` lists, downloads and extracts clean; the old spelling in a URL is a 404; a live-numbered row lists and downloads.
- `web/tests/no-test-mode-wording.test.ts`: reads every file under `web/app`, `web/components`, `web/lib` and fails on the same wording.
- Re-captured against the running app (uvicorn, real HTTP, real PDFs) with `scripts/capture_billing_invoices.py` on a scratch Postgres; the new bodies are in the FE guide and `docs/fe-captures/billing-invoices/`.
- Locally: `ruff check .`, `ruff format --check .`, the two invoice test files, `tests/test_billing_subscriptions_api.py`, web `npm run lint`, `npm run typecheck` and the invoice test files. CI ran the rest.

## Operate / roll back

No setting, no migration. Revert the PR to bring the old wording back; stored rows were never changed, so a revert needs no data step.

## Follow-ups

| Item | Who |
|---|---|
| **Go-live: what a person sees of test-era invoices.** They keep showing, with ordinary-looking numbers (`BN-2026-9000NN`), for payments where no real money moved. The numbers cannot collide with live ones, so nothing breaks, but the lead should decide before go-live whether pilot users still see them. The small option: when `BILLING_LIVE` is true, list and download only `test_mode = false` invoices (a filter in `invoices_api.py`; the payments' `invoice_number` would need the same). Not done here: it changes what a person can open and is the owner's call | lead |
| Re-capture `docs/fe-captures/billing-cancel-checkout/` once the open change to that folder (cancel an abandoned upgrade) has merged: its three captures with `invoice_number` still read `BN-T-2026-0000NN` (the guide section says so). Not done here to avoid conflicts with that change | lead or the next billing session |
| The provider's hosted page may show its own test banner | none, goes with live keys |
