# SOP: cancel an abandoned UPGRADE checkout

**What shipped.** A person can put away an upgrade checkout they started and did not finish, with the same button and the same route they already had for a plan or pack checkout: `POST /api/billing/payments/{reference}/cancel`. Branch `feature/cancel-upgrade-checkout` (PR: see the PR for this branch; not merged by the author, the lead merges on Adebayo's word after the Opus and delta reviews). No new route, no migration, no setting.

## Why

`GET /subscription` returns the newest `pending` payment as `pending_payment`. A person who clicked "Change plan", reached the provider's page for an upgrade and closed it left a `pending` `upgrade` payment, and the billing page kept saying "You have a payment that is not finished" until it was paid. For a plan or pack the person had **Cancel this payment** since 2026-11-18 (`2026-11-18-billing-small-ux.md`); for an upgrade the route answered 409 `cannot_cancel` ("an upgrade belongs to the plan change") and the screen offered nothing. Adebayo decided on 2026-11-20 to allow it (it was the follow-up row "an abandoned upgrade checkout has no cancel").

## How

The rule is the one in `2026-11-18-billing-small-ux.md`, unchanged: **ask the provider first**, a paid page is settled and never cancelled, a wrong amount is refused and told to the admins, a provider that cannot be asked changes nothing (502), "no record" (`NoRecord`, Flutterwave for a page nobody opened) is unpaid, and "cancelled" is `failed`, not a new state. The only code change that makes an upgrade cancellable is one tuple, `CANCELLABLE_KINDS = ("plan", "pack", "upgrade")`. The rest of this change is the proof that the existing machinery is safe for an upgrade, and one more place where the upgrade had to be added (the sweeper).

### What "cancel" means for an upgrade (decision 1)

Only the **payment** goes `failed`. `cancel_checkout` never reads the subscription for writing, so the plan stays as it was: same plan and price, same period, same `next_charge_at`, same `pending_plan_code`, no hour grant added or changed. The tests compare **every column** of the subscription and hour-grant rows before and after (`held()` in `tests/test_billing_cancel_checkout.py`) and the person-visible state (`plan_view`); the live capture does the same against the database (`38-upgrade-plan-rows-before-and-after-cancel.json`, `identical: true`). A paid page is settled by `settle.settle_transaction` as for any payment (the plan changes, the extra hours are granted once) and answered `paid`.

### A page paid AFTER the cancel (decision 2: the late-payment question)

Read first: what an upgrade payment does when it is paid late today (`subscriptions._upgrade_paid`, the S8a Opus review's "late upgrade takes money for nothing" case). It is applied **only** if the subscription is still the same live one, `active`, no refund waiting, `payment.cycle_end == sub.current_period_end` (the period the upgrade was bought for is still running) and the clock at the payment still says `active`. In every other case `needs_refund` runs: nothing is granted, the plan does not change, the payment keeps `succeeded` with `needs_refund_reason` set (so a refund of it is never a double refund), and the admins get `admin.billing_refund_needed:<id>`. A second upgrade to the same or a smaller plan fails `prorate_upgrade` (`NoUpgrade`) and is handled the same way.

Cancelling does not touch any of that, so **there is no new money path and nothing is reopened**: a cancelled upgrade page paid later is settled by the webhook, **inside the period** the upgrade is applied exactly as it would be for an upgrade page the person never cancelled (the plan changes, extra hours granted); **after the period moved on** it grants nothing and the admins are told. Proved by `test_a_cancelled_upgrade_page_paid_later_inside_the_period_is_applied_as_any_open_upgrade` and `test_a_cancelled_upgrade_page_paid_after_the_period_moved_on_grants_nothing_and_alerts` (the renewal runs first, then the late webhook: plan still `p20`, the rows unchanged since the renewal, one `admin.billing_refund_needed` naming the payment). The second one is the S8a case, and it is not live-capturable (a month must pass).

**The one gap this found, and fixed: the sweeper.** A cancelled upgrade page that is paid later **with a lost webhook** was not found: `renewals._sweep_payments` asked only about `pending` payments and about `failed` **plan and pack** checkouts. The same upgrade page, never cancelled, stays `pending` and is asked about for 3 days; cancelling made it `failed` and moved it out of reach, i.e. cancelling created a new way to take money with nothing granted and nobody told. The fix is to add `"upgrade"` to that tuple (`renewals.py`, one word and its comments). A test settles it (`test_the_sweeper_settles_a_cancelled_upgrade_page_paid_later_with_a_lost_webhook`) and one proves a still-unpaid cancelled upgrade page is asked about and left alone (and, by the existing parametrized test, that a failed `renewal` is still never asked). A mutation check (removing `"upgrade"` from the tuple) fails both, and removing it from `CANCELLABLE_KINDS` fails 15 of the upgrade tests.

### The rest, exactly as plan and pack are tested (decision 3)

| Case | Result | Test |
|---|---|---|
| abandoned page (`pending`) | 200 `cancelled`, payment `failed`, `pay_url` null, plan rows identical, notice gone, no admin alert | `test_an_abandoned_upgrade_page_is_cancelled_and_the_plan_is_exactly_as_it_was` |
| provider has no record (`NoRecord`) | 200 `cancelled`, rows identical | `test_an_upgrade_page_the_provider_has_no_record_of_is_cancelled` |
| provider says `failed` | 200 `failed`, rows identical | `test_an_upgrade_the_provider_says_failed_...` |
| paid in the meantime | 200 `paid`, upgrade applied once, never cancelled | `test_an_upgrade_page_paid_in_the_meantime_is_settled_and_never_cancelled` |
| wrong amount | 409 `payment_in_progress`, admin told (`admin.billing_payment_refused`), stays `pending`, rows identical | `test_an_upgrade_page_paid_wrongly_...` |
| provider cannot be asked | 502 `provider_error`, nothing changes, notice stays | `test_an_upgrade_provider_that_cannot_be_asked_...` |
| already `succeeded` | 200 `paid`, provider not asked, rows identical | `test_an_upgrade_already_paid_answers_paid_...` |
| someone else's reference | 404 `not_found`, provider not asked | `test_someone_elses_upgrade_is_a_plain_404_...` |
| billing off, unknown reference, no CSRF | 404, 404, 403 | the existing kind-agnostic tests |
| refund of the plan in progress | 409 `refund_pending`, provider not asked | `test_an_upgrade_is_not_cancelled_while_a_refund_...` |
| refund starts while the provider answers | 409 `refund_pending`, stays `pending` | `test_a_refund_that_starts_while_the_provider_answers_stops_an_upgrade_being_closed` |
| two-connection lock race | the cancel waits on the row lock, the settlement commits (plan `p40`, two grants), the cancel answers `paid` | `test_a_webhook_holding_an_upgrade_row_...` (two threads) |
| a renewal | still 409 `cannot_cancel`, provider not asked | `test_a_renewal_cannot_be_cancelled_here` (replaces the old "renewal or upgrade" test) |
| start another upgrade after a cancel | a new payment, and it is the new `pending_payment` | `test_a_cancelled_upgrade_can_be_started_again_...` |

**Billing-page notice (decision 4).** `pending_payment` reads only `pending` rows, so a `failed` upgrade is gone from it: the same test reads `GET /subscription` before (kind `upgrade`) and after (`null`).

### The screen (decision 5)

`web/components/billing/cancel-payment.tsx`: `CancelPayment` returns nothing only for a `renewal` (was: anything but plan or pack); the button, its busy label "Cancelling..." and its error line are the app's own. `cancelNotice(result, kind)` takes the payment's kind: after `cancelled` or `failed` an **upgrade** says "The payment was cancelled. Your plan and hours have not changed. Please do not pay on the old payment page." (the plan and pack words "no hours were added" would be wrong for an upgrade; "plan and hours" is the phrase the paid notice already uses); after `paid` the sentence is unchanged. `billing-view.tsx` passes the waiting payment's kind. The plans screen is untouched (it shows the waiting notice for a plan or pack only; a person with a plan is sent to Billing). No provider is named, money is not printed by the new words.

**Rejected alternatives.**

* **Cancelling an upgrade also clears a downgrade or touches the subscription** (to "make sure it is as before"): nothing changes in the subscription, so there is nothing to restore, and writing it would have added a lock-order and race surface for no benefit.
* **A new `cancelled` state, or marking an upgrade "cancelled by the person" so a late payment is refused and refunded automatically:** needs a migration, a new money path (an automatic refund) and a rule that the webhook honours it. The existing rule for a late upgrade page already keeps the money for a refund by hand and tells the admins when it cannot be used, and applies it when it can.
* **A separate sweep rule for upgrades or a shorter window:** the same tuple and the same 7-day window as plan and pack is one concept, not two.
* **Hiding the notice instead of closing the payment:** the page could still be paid later, and the person must be told not to pay it; closing it is what makes the provider-first answer meaningful.

## What's involved

| Path | Change |
|---|---|
| `app/billing/cancel_checkout.py:66` | `CANCELLABLE_KINDS` gains `"upgrade"`; module and function docstrings say what an upgrade cancel does and why a late page is no new path (the `_tell_grant_failed` helper is untouched) |
| `app/billing/renewals.py:671` | `_sweep_payments` also asks about a recent `failed` upgrade page (plus the comment and docstring) |
| `app/billing/subscriptions_api.py` | route docstring, route table and the `cannot_cancel` description (a renewal) only; no code |
| `web/components/billing/cancel-payment.tsx`, `web/components/billing/billing-view.tsx` | the button for an upgrade, the upgrade wording, the kind passed to `cancelNotice` |
| `tests/test_billing_cancel_checkout.py` | the old "renewal or upgrade" test now covers a renewal only; 16 new upgrade tests (51 in the file) |
| `web/tests/billing-cancel-payment.test.tsx` | the renewal-only and all-three-kinds button tests, and 4 upgrade tests on the captured bodies (16 in the file) |
| `scripts/capture_billing_cancel_checkout.py`, `docs/fe-captures/billing-cancel-checkout/` | the fake `upgrade` refusal is now a `renewal` refusal (capture 30); captures 33 to 44 are a real upgrade cancel against the running app |
| `docs/fe-integration-guide-billing.md` | section "Cancelling an UPGRADE checkout (2026-11-20)", the cancel chapter's kind lists, error table and verification table |
| `docs/sop/2026-11-18-billing-small-ux.md`, `docs/sop/2026-11-17-billing-subscriptions.md`, `docs/checklist/master-checklist.md` | the stale "an upgrade has no cancel" rows updated, one checklist line |

No migration (Alembic head stays 0054). No new route, so the route-inventory tests did not need changing. No setting, no deploy step.

## Verification

* **Backend, real Postgres (scratch container, removed afterwards):** `tests/test_billing_cancel_checkout.py` 51 passed; `tests/test_billing_alerts.py` and `tests/test_billing_renewals.py` sweeper tests (`-k sweep`) 7 passed. `ruff check .` and `ruff format --check .` clean (ruff 0.17.3). Mutation checks, as above.
* **FE:** `npx vitest run tests/billing-cancel-payment.test.tsx` 16 passed; `npm run typecheck` clean; `npm run lint` 0 errors (3 old warnings in files not touched).
* **Live capture:** `scripts/capture_billing_cancel_checkout.py` against a scratch Postgres with real migrations and the real app under uvicorn: a plan held, an upgrade checkout started, cancelled (200 `cancelled`), `GET /subscription` before and after identical but for `pending_payment`, the database rows identical, the old page's webhook applying the upgrade, and a second upgrade paid in another tab answered `paid`.
* **Not verified:** a real Paystack or Flutterwave lookup of an abandoned **upgrade** page (as for plan and pack, the provider is a scripted double; what Flutterwave answers for a page nobody opened is still the 400/404/422 assumption); the upgrade after the period moved on is a test, not a live capture; the screens were not driven in a browser (vitest on captured bodies, as before).

## Operate / roll back

Nothing to run. To roll back, revert the PR: upgrade checkouts go back to 409 `cannot_cancel` and no button; payments cancelled meanwhile are ordinary `failed` payments that the webhook and the sweeper settle if paid (if the sweeper change is reverted, only a *lost webhook* on a cancelled upgrade page would go unnoticed, so revert the two together or neither).

## Follow-ups

| Gap | Note |
|---|---|
| A late-paid cancelled upgrade page inside the period gets the extra hours prorated at the moment the person **asked** (`payment.created_at`), not at the payment: the same rule as any upgrade page paid late, so a person who waits days to pay gets slightly more hours than the days left justify | existing S8a behaviour, unchanged; worth a product look if it matters |
| Two upgrade checkouts open at once (the server does not de-duplicate): both paid, the second becomes a refund by hand | existing, noted in `2026-11-17-billing-screens.md`; cancelling the old one when starting a new one is a possible later step |
| Real-provider lookups of an abandoned upgrade page | check against the test accounts before go-live, with the plan and pack ones |
