# SOP: payment row locks read the row as it is now (billing review M1, 2026-11-25)

**What shipped.** Every `SELECT ... FOR UPDATE` on a payment (and the few on subscriptions and refunds that lacked it) now carries `.execution_options(populate_existing=True)`, so a lock never returns an old copy of the row. Branch `fix/billing-stale-payment-lock`; PR number in the pull request list (it needs an Opus delta review before merge: it is money code). No migration, no config.

## Why

Review `billing-review-2026-11-25` found one must-fix before the first real payment (M1). Sessions are built with `expire_on_commit=False` (`app/db.py:19`), and SQLAlchemy does not overwrite the attributes of an object the session already holds when a later SELECT, even a `FOR UPDATE`, returns the same row. The lock was taken, but `payment.status` was still the old value. The callers that hold a payment from before a provider call and a commit are exactly the ones a webhook can beat:

| Caller | What went wrong when a webhook had already settled the payment |
|---|---|
| `renewals._attempt` (saved-card renewal), `_sweep_payments`, `_settle_open_payments`, `_open_page` (pay link) -> `settle.settle_transaction` | read `pending`, ran every grant handler again, the second grant hit the live-plan or period check and put `needs_refund_reason` on the payment that bought the month (an admin following the alert refunds money the person used); a `failed` report was written over a `succeeded` payment |
| A second webhook event for the same payment (`settle._apply`) | same, when this process already held the payment as `pending` |

## How

Follow the pattern already used at `subscriptions.py` `_locked_subscription`, `renewals.py`, `cancel_checkout._locked` and `refunds.resolve`: add `.execution_options(populate_existing=True)` to the locking statement. It is safe here because the session autoflushes before the SELECT, so no unsaved change is lost. Rejected: switching the app to `expire_on_commit=True` (changes every caller's behaviour and costs a reload after each commit) and `db.refresh(payment)` after the lock (a second statement that can be forgotten the same way).

## What's involved

| File | Lock | Row |
|---|---|---|
| `app/billing/settle.py` `_apply` | webhook settlement | Payment |
| `app/billing/settle.py` `settle_transaction` | sweeper, renewal charge, pay link | Payment |
| `app/billing/refunds.py` `_check` (when `lock`) | refund eligibility | Subscription |
| `app/billing/refunds.py` `request` step one | start a refund | Payment |
| `app/billing/refunds.py` `request` step one, first-plan-7-days | end the plan | Subscription |
| `app/billing/refunds.py` step three (after the provider call, a new transaction in the same session) | finish a refund | Payment and Refund |
| `app/billing/refunds.py` `resolve` | admin resolves a refund | Payment |
| `app/billing/invoices.py` issue loop (`skip_locked`) | issue an invoice | Payment |

Already correct, untouched: `subscriptions.py` (two), `renewals.py` (two), `cancel_checkout.py`, `profile.py`, `refunds.py` `resolve`'s refund lock.

## Verification

`tests/test_billing_stale_payment_lock.py` (new, real Postgres): session A is built like the app's (no expire on commit) and holds the payment as `pending`; session B settles it through the real webhook path and commits; then A runs the settle path. Three tests: `settle_transaction` does not re-run grants, set `needs_refund_reason` or add a second subscription; a `failed` report does not overwrite `succeeded`; a second webhook event (`_apply`) does not re-run grants. All three FAILED before the fix (`grant_calls == [1, 1]`) and pass after. Ran locally with the touched files' tests only (settle, refunds, invoices, inbox, payments, grant-failure alert, renewals); CI runs the rest. **Not verified:** a live race against a real provider (none can be forced); the refund and invoice locks have no stale-read test of their own, they are the same one-line change.

## Operate / roll back

Plain deploy, nothing to switch. To revert, revert the PR: behaviour goes back to the stale read, so do it only if the change itself misbehaves.

## Follow-ups

S1 and S2 of the same review are Adebayo's pick and are not part of this change. A lint rule that rejects `with_for_update()` on a mapped class without `populate_existing` would stop the next one.
