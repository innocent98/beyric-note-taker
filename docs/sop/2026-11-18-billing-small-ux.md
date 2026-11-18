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
| cannot be asked, no record under the reference, no provider configured, a report that is malformed or about another reference | **nothing changes** | 502 `provider_error` |
| paid but our rules refuse it (a wrong amount, say) | nothing changes, an admin is told (`admin.billing_payment_refused:<id>`, the same alert the sweeper raises) | 409 `payment_in_progress` |
| (no call) the payment is already `succeeded`, `refunded` or `partly_refunded` | nothing changes | 200 `paid` |

Other refusals: 404 `not_found` for someone else's or an unknown reference (the same plain answer as `GET /payments/{reference}`, the provider is not even asked) and while billing is off; 409 `cannot_cancel` for a `renewal` or `upgrade` (a renewal belongs to the clock, an upgrade to the plan change); 409 `refund_pending` while a refund of the plan is being settled (`subscriptions._refuse_while_refunding`, as everywhere).

**Decisions and rejected alternatives.**

* **"Cancelled" is `failed`. No new state, no migration.** A `failed` payment is still open to settlement (`settle.OPEN_STATUSES = ("pending", "failed")`, `_apply` and `settle_transaction` both settle an open row from the provider's report). So a cancelled page paid later by mistake is settled by the provider's webhook and its hours granted, nothing is lost (proved by test and live capture 27 and 28). A new `cancelled` state would have needed a migration and a rule that the webhook revives it: more surface for nothing.
* **What cannot be settled:** a cancelled page paid later whose webhook never arrives. `renewals.sweep_pending` only looks at `pending` rows, exactly as for a payment the provider itself reported failed. Accepted: providers retry webhooks, and the screen tells the person not to pay the old page. A second plan paid while one is live is kept by the existing "needs a refund by hand" path (admin alert).
* **No row lock across the provider call.** The first read takes no lock; the provider is asked with no transaction open (`db.rollback()` after the read); only the write locks the payment (order everywhere: payment, refund, subscription: this takes the payment and reads the refund). A payment settled by the webhook in between is found again under the lock and answered `paid`, never marked failed (test `test_a_payment_settled_while_the_provider_was_asked_is_not_marked_failed`).
* **A provider's "no record" is a 502, not "unpaid".** `lookup_by_reference` cannot tell "no record" from "cannot be asked" (both are `ProviderError`), so the endpoint does not guess. See Follow-ups: this may leave an abandoned Flutterwave page uncancellable.
* **Rejected:** cancelling on the person's word without asking the provider (could cancel a paid payment's notice while the webhook is late); a time limit on `pending_payment` (a page can legitimately be paid hours later, and the person gets no way to say "I gave up").

### What's involved

| Path | Change |
|---|---|
| `app/billing/cancel_checkout.py` | new: `cancel_checkout()` and the module note with the table above |
| `app/billing/subscriptions_api.py` | `POST /payments/{reference}/cancel`, `CancelPaymentOut {result, payment}`, route table and error list in the module docstring |
| `web/components/billing/cancel-payment.tsx` | new: the button (busy label "Cancelling...", error line, nothing for a `renewal` or `upgrade`), `cancelNotice()` |
| `web/components/billing/plans-view.tsx`, `billing-view.tsx` | the button beside Continue to pay and Check this payment; the notice after; the state is read again |
| `web/lib/billing.ts`, `web/lib/api/client.ts`, `web/components/billing/common.ts` | `CancelPaymentResult`, `api.billing.cancelPayment`, `cannot_cancel` shown as the server words it |
| `scripts/capture_billing_cancel_checkout.py`, `docs/fe-captures/billing-cancel-checkout/` | the live capture for the FE guide |
| `docs/fe-integration-guide-billing.md` | section "Cancelling an unfinished checkout" |

The words the screen shows: after `cancelled` or `failed` "The payment was cancelled and no hours were added. Please do not pay on the old payment page."; after `paid` "That payment had already gone through, so it was not cancelled. Your plan and hours are shown below."; a 502 shows the server's sentence under the button and leaves the notice. No migration, no setting.

### Verification

* `tests/test_billing_cancel_checkout.py`, 21 tests on real Postgres: unpaid, failed, **paid in the meantime** (granted once, never cancelled), settled by the webhook while the provider was asked, wrong amount (409, admin told, stays pending), provider down, no record, provider not configured, malformed or foreign report, someone else's payment (404, provider not asked), unknown, renewal and upgrade (409), billing off, no CSRF, already paid, twice, pack, a refund in the way, a cancelled page paid later settled by the webhook. A mutation check (cancel without looking at the provider's answer) fails three of them.
* `tests/test_billing_profile.py::test_no_billing_route_takes_a_persons_id` lists the new path; `tests/test_api.py` route guards pass.
* `scripts/capture_billing_cancel_checkout.py` over real HTTP (31 files, provider scripted). `web/tests/billing-cancel-payment.test.tsx` (10 tests) on those captured bodies.
* **Not verified:** a real Paystack or Flutterwave lookup of an abandoned page; the screens in a browser.

### Operate / roll back

No setting, no migration. Revert the PR to remove the button and the route; payments cancelled meanwhile are ordinary `failed` payments (the webhook still settles them if paid).

## 2. The absent answer expires (the Billing link missing on desktop)

**Why.** A 404 `not_found` (billing off for this person) was stored in `sessionStorage` for the tab with no expiry. A tab opened before billing was switched on kept hiding the Billing link and the hours card until a sign-out and sign-in.

**How.** `web/lib/billing.ts`: `UNAVAILABLE_KEY` now holds the time it was learned, like `HEALTHY_KEY`; `billingKnownAbsent(now)` is true for `ABSENT_FOR_MS` (60 000 ms), `rememberBillingAbsent(now)` stores the time. The old value `"1"` an open tab may hold is read as 1970, so it is already expired. Storage errors are still swallowed; `forgetBillingTabState` still clears it. Cost: a person who is not on billing asks `GET /subscription` at most once a minute per tab instead of once per tab (a 404 is cheap and unchanged for the server). Tests: `web/tests/billing-entry.test.tsx` (the stored value is a time; 30 s later no new request, 61 s later the link appears; the old `"1"` is expired; storage that throws).

## 3. The country note

`web/components/billing/billing-country.tsx`: the paragraph under "Billing country" is now "This sets your currency. Use the country your business is based in." (lead's wording, 2026-11-18). No provider names and no change rule inside it; the statement under the plans ("Prices in naira, paid through Paystack.") and the pay page keep saying the provider. The lines under the chosen country are unchanged: "Until your first payment you can change this country as often as you like." while it is not locked, and the 30-day line when it is locked. Test: `web/tests/billing-plans-view.test.tsx`.

## Verification (all three, local)

`ruff check` and `ruff format --check` on `app/`, `tests/`, `scripts/` (ruff 0.17.3); `tsc --noEmit`; `eslint` on the touched web files; the nine `web/tests/billing-*` files (154 tests); `docs/openapi.json` unchanged (`export_openapi.py --check`: the billing routes are not in it). CI runs the rest.

## Follow-ups

| Gap | Note |
|---|---|
| **A Flutterwave hosted page nobody opened may be uncancellable.** If Flutterwave's `verify_by_reference` answers "no transaction found" (HTTP 400/404, or 200 with an error status), the adapter raises `ProviderError` and the cancel answers 502 for ever. The adapter's `RequestRefused` (HTTP 400, 404, 422) could become a neutral `NoRecord(ProviderError)` that the cancel treats as unpaid (safe: a `failed` payment is still settled by the webhook). Not done because the exact vendor answer is not verified live | check against the Flutterwave test account before go-live |
| A cancelled page paid later whose webhook is lost is never settled | add `failed` rows of the last days to `renewals.sweep_pending` |
| An abandoned **upgrade** checkout has no cancel (only a plan or pack does), so its notice on the billing page stays until it is paid | decide with the lead whether to allow it |
| The screens were not driven in a browser | vitest on captured bodies only |
