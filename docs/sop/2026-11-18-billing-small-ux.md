# SOP: three small billing fixes (cancel an unfinished checkout, absent answer expires, shorter country note)

**What shipped.** One PR, branch `fix/billing-small-ux`, three changes Adebayo found on the billing screens. (1) **Cancel this payment**: a person can put away a plan or pack checkout they started and did not finish, with `POST /api/billing/payments/{reference}/cancel`. (2) The "billing is not available to you" answer a browser tab remembers now **expires after 60 seconds**, so the Billing link and the hours card appear without a sign-out once billing is switched on. (3) The billing country card's note is shorter. PR: see the PR for this branch (not merged by the author; the lead merges on Adebayo's word after an Opus review of change 1).

## 1. Cancel this payment (the money part)

### Why

`GET /subscription` returns the newest `pending` payment as `pending_payment`, with no age limit. Paystack reports an abandoned page as `pending` and Flutterwave keeps it open, so a person who closed the page saw "You started a payment and have not finished it" for ever (the follow-up "a pending checkout is shown for ever" in `2026-11-17-billing-subscriptions.md`, now updated).

### How

New module `app/billing/cancel_checkout.py`, route in `app/billing/subscriptions_api.py` (`cancel_payment`). The rule is **ask the provider first, always**: a click is never believed over the provider's word about our reference, because the page may have been paid a moment ago in another tab.

| The provider says (`lookup_by_reference`) | What happens | Answer |
|---|---|---|
| paid (`successful`) | `settle.settle_transaction` (the webhook's own rules: our reference, currency, at least our amount): the plan or pack is granted. Nothing is cancelled | 200 `result` `paid` |
| `failed` | `settle_transaction` marks it `failed` | 200 `failed` |
| unpaid (`pending`: abandoned or open) | the payment row is locked, read again (it may have been settled meanwhile: then `paid`), then marked `failed` | 200 `cancelled` |
| **no record** under our reference (`NoRecord`, a `ProviderError` subclass the Flutterwave adapter raises for HTTP 400, 404 or 422 on `verify_by_reference`: Flutterwave makes a transaction only when the payer tries to pay, so a page nobody opened has none) | treated as unpaid: same as the row above | 200 `cancelled` |
| cannot be asked (down, refused key, rate limit, 5xx, timeout), no provider configured, a report that is malformed or about another reference | **nothing changes** | 502 `provider_error` |
| paid but our rules refuse it (a wrong amount, say), or a grant handler fails (the transaction is undone) | nothing changes; a refusal tells an admin (`admin.billing_payment_refused:<id>`, the same alert the sweeper raises) | 409 `payment_in_progress` |
| (no call) the payment is already `succeeded`, `refunded` or `partly_refunded` | nothing changes | 200 `paid` |

Other refusals: 404 `not_found` for someone else's or an unknown reference (the same plain answer as `GET /payments/{reference}`, the provider is not even asked) and while billing is off; 409 `cannot_cancel` for a `renewal` or `upgrade` (a renewal belongs to the clock, an upgrade to the plan change); 409 `refund_pending` while a refund of the plan is being settled (`subscriptions._refuse_while_refunding`, as everywhere).

**Decisions and rejected alternatives.**

* **"Cancelled" is `failed`. No new state, no migration.** A `failed` payment is still open to settlement (`settle.OPEN_STATUSES = ("pending", "failed")`, `_apply` and `settle_transaction` both settle an open row from the provider's report). So a cancelled page paid later by mistake is settled by the provider's webhook and its hours granted, nothing is lost (proved by test and live capture 27 and 28). A new `cancelled` state would have needed a migration and a rule that the webhook revives it: more surface for nothing.
* **A cancelled page paid later with a lost webhook is settled by the sweeper (Opus review fix).** `renewals._sweep_payments` now also asks the provider about `failed` plan and pack payments that have a page (`pay_url`), for 7 days after creation (`FAILED_LOOKBACK`; every 30 minutes the first day, every 3 hours after) and settles one the provider says is paid, with the same rules. There is no marker for "cancelled by the person" (no migration), so a plan or pack payment the provider itself reported failed is covered too (it can still be paid: harmless, and it is the same settlement). Other failed payments (a renewal, a row with no page, older than 7 days) are never asked about. **Skipped on purpose:** the daily "waited more than a day" digest (`_alert_old_pending`) still lists `pending` rows only: naming every cancelled checkout there would bury the real stuck ones, and a cancelled page that is paid is now settled by the sweeper (and a refused one alerts the admins as before). A second plan paid while one is live is kept by the existing "needs a refund by hand" path (admin alert).
* **No row lock across the provider call.** The first read takes no lock; the provider is asked with no transaction open (`db.rollback()` after the read); only the write locks the payment (order everywhere: payment, refund, subscription: this takes the payment and reads the refund). A payment settled in between is found again under the lock and answered `paid`, never marked failed; the refund guard is checked again under the lock too (a refund that starts while the provider answers stops a pack being closed). A grant handler that raises on the settle path is rolled back and answered 409 `payment_in_progress` in the module's error shape (it was a raw 500). `_settle_reported` commits only the admin alert it raised before refusing (the docstring says so). Tests: `test_a_webhook_holding_the_row_makes_the_cancel_wait_and_then_answer_paid` runs two connections in two threads (the cancel really blocks on the row lock until a settlement in another transaction commits), plus the same-thread ordering test, the refund test and the failing-handler test.
* **"No record" is unpaid, "cannot be asked" is a 502 (Opus review fix).** `lookup_by_reference` could not tell them apart (both were `ProviderError`), and Flutterwave answers HTTP 400 "no transaction" for a hosted page nobody opened, so every dollar customer's abandoned page would have got a 502 for ever. The adapter's `RequestRefused` (400, 404, 422) on `verify_by_reference` is now `types.NoRecord(ProviderError)`; every other caller still sees a `ProviderError`, and the lost-charge lookup still turns it into `OutcomeUnknown`. Safe because a `failed` payment is still settled by the webhook and the sweeper. The exact Flutterwave answer is not captured live (see Follow-ups).
* **Rejected:** cancelling on the person's word without asking the provider (could cancel a paid payment's notice while the webhook is late); a time limit on `pending_payment` (a page can legitimately be paid hours later, and the person gets no way to say "I gave up").

### What's involved

| Path | Change |
|---|---|
| `app/billing/cancel_checkout.py` | new: `cancel_checkout()` and the module note with the table above |
| `app/billing/types.py`, `app/billing/flutterwave.py` | `NoRecord(ProviderError)`; `fetch_by_reference` raises it for a refused lookup |
| `app/billing/renewals.py` | `_sweep_payments` also covers recent `failed` plan and pack checkouts with a page (`FAILED_LOOKBACK`) |
| `app/billing/subscriptions_api.py` | `POST /payments/{reference}/cancel`, `CancelPaymentOut {result, payment}`, route table and error list in the module docstring |
| `web/components/billing/cancel-payment.tsx` | new: the button (busy label "Cancelling...", error line, nothing for a `renewal` or `upgrade`), `cancelNotice()` |
| `web/components/billing/plans-view.tsx`, `billing-view.tsx` | the button beside Continue to pay and Check this payment; the notice after; the state is read again |
| `web/lib/billing.ts`, `web/lib/api/client.ts`, `web/components/billing/common.ts` | `CancelPaymentResult`, `api.billing.cancelPayment`, `cannot_cancel` shown as the server words it |
| `scripts/capture_billing_cancel_checkout.py`, `docs/fe-captures/billing-cancel-checkout/` | the live capture for the FE guide |
| `docs/fe-integration-guide-billing.md` | section "Cancelling an unfinished checkout" |

The words the screen shows: after `cancelled` or `failed` "The payment was cancelled and no hours were added. Please do not pay on the old payment page."; after `paid` "That payment had already gone through, so it was not cancelled. Your plan and hours are shown below."; a 502 shows the server's sentence under the button and leaves the notice. No migration, no setting.

### Verification

* `tests/test_billing_cancel_checkout.py`, 32 tests on real Postgres (21 first, then the review's: no record, the real Flutterwave adapter on a scripted wire for 400 and 401, a failing grant, a refund during the lookup, the two-thread lock test, the sweeper settling, leaving alone and not touching other failed rows). `tests/test_billing_flutterwave.py` adds the adapter cases: unpaid, failed, **paid in the meantime** (granted once, never cancelled), settled by the webhook while the provider was asked, wrong amount (409, admin told, stays pending), provider down, no record, provider not configured, malformed or foreign report, someone else's payment (404, provider not asked), unknown, renewal and upgrade (409), billing off, no CSRF, already paid, twice, pack, a refund in the way, a cancelled page paid later settled by the webhook. A mutation check (cancel without looking at the provider's answer) fails three of them.
* `tests/test_billing_profile.py::test_no_billing_route_takes_a_persons_id` lists the new path; `tests/test_api.py` route guards pass.
* `scripts/capture_billing_cancel_checkout.py` over real HTTP (33 files, provider scripted). `web/tests/billing-cancel-payment.test.tsx` (10 tests) on those captured bodies.
* **Not verified:** a real Paystack or Flutterwave lookup of an abandoned page (Flutterwave's HTTP status and body for "no transaction" are assumed to be 400, 404 or 422, as the adapter's refusal rule reads them); the screens in a browser.

### Operate / roll back

No setting, no migration. Revert the PR to remove the button and the route; payments cancelled meanwhile are ordinary `failed` payments (the webhook still settles them if paid).

## 2. The absent answer expires (the Billing link missing on desktop)

**Why.** A 404 `not_found` (billing off for this person) was stored in `sessionStorage` for the tab with no expiry. A tab opened before billing was switched on kept hiding the Billing link and the hours card until a sign-out and sign-in.

**How.** `web/lib/billing.ts`: `UNAVAILABLE_KEY` now holds the time it was learned, like `HEALTHY_KEY`; `billingKnownAbsent(now)` is true for `ABSENT_FOR_MS` (60 000 ms), `rememberBillingAbsent(now)` stores the time. The old value `"1"` an open tab may hold is read as 1970, so it is already expired. Storage errors are still swallowed; `forgetBillingTabState` still clears it. Cost: a person who is not on billing asks `GET /subscription` at most once a minute per tab instead of once per tab (a 404 is cheap and unchanged for the server). A stored time in the future (a clock set back) is ignored, so it cannot keep the answer past a minute. **Known and accepted:** the sidebar's `useBillingState` reads only when it mounts, so the Billing link appears on the next page load or navigation that remounts the shell, not by itself while the person sits on one page. Tests: `web/tests/billing-entry.test.tsx` (the stored value is a time; 30 s later no new request, 61 s later the link appears; the old `"1"` is expired; storage that throws).

## 3. The country note

`web/components/billing/billing-country.tsx`: the paragraph under "Billing country" is now "This sets your currency. Use the country your business is based in." (lead's wording, 2026-11-18). No provider names and no change rule inside it. The lines under the chosen country are unchanged: "Until your first payment you can change this country as often as you like." while it is not locked, and the 30-day line when it is locked. **The plans screens stop naming the payment provider** (lead, 2026-11-18): `routeStatement` in `web/lib/billing.ts` now returns "Prices in naira." or "Prices in US dollars." (currency only), used by the billing country card and the plans screen; so the statement under the plans no longer says "paid through Paystack". "You pay on the next page, on the provider's own site." stays. What is true at this head (checked with `git grep`): the provider is still named on the **admin billing screens**, the **public pricing page** (`web/components/pricing/content.ts`) and `llms.txt`; the payment return page, invoices, receipts and the Privacy Policy do **not** name it (the return page has comments only). `providerName` and the API's `route.provider` are unchanged. Whether a signed-in buyer should see the provider's name anywhere before the hosted page is an open product question (Follow-ups). Tests: `billing-lib` and `billing-plans-view` (a new one asserts neither provider name appears anywhere on the plans screen, for dollars and naira).

## Verification (all three, local)

`ruff check` and `ruff format --check` on `app/`, `tests/`, `scripts/` (ruff 0.17.3); `tsc --noEmit`; `eslint` on the touched web files; the nine `web/tests/billing-*` files (156 tests); `docs/openapi.json` unchanged (`export_openapi.py --check`: the billing routes are not in it). CI runs the rest.

## Follow-ups

| Gap | Note |
|---|---|
| Flutterwave's real answer for a lookup of a hosted page nobody opened is assumed to be HTTP 400, 404 or 422 (what `RequestRefused` covers); a 200 with an error body would still be a 502 | check against the Flutterwave test account before go-live |
| A `pending` row with no page (checkout creation failed, or Paystack says "reference not found") answers 502 for ever on cancel | rare; the sweeper's `NO_LINK_GRACE` logic covers renewals only |
| An abandoned **upgrade** checkout has no cancel (only a plan or pack does), so its notice on the billing page stays until it is paid | decide with the lead whether to allow it |
| The daily old-pending digest lists `pending` rows only (see the sweeper note above) | deliberate; revisit if cancelled-but-paid pages ever need a human |
| Whether a signed-in buyer should see the provider's name before the hosted page | product decision; today only admin screens, the pricing page and `llms.txt` name it |
| The screens were not driven in a browser | vitest on captured bodies only |
