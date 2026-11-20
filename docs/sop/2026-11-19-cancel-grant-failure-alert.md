# SOP: tell the admins when a cancelled-but-paid payment could not be granted

**What shipped.** When a person presses "Cancel this payment", the provider reports the page **paid**, and granting the hours fails, the admins now get one alert (bell and mail) for that payment. Before, only a log line was written, so a customer who had paid could be left without hours and nobody was told. Branch `fix/cancel-grant-failure-alert`; PR: see the PR for this branch (not merged by the author; the lead merges after an Opus delta review).

## Why

`cancel_checkout._settle_reported` settles a paid report with the webhook's own rules (`settle.settle_transaction`). If a grant handler raises, the transaction is rolled back (nothing kept, the payment stays `pending`) and the person gets 409 `payment_in_progress` ("We could not confirm your payment yet. We are looking into it."). The words promise someone is looking, but nothing told anyone. The provider's webhook or the sweeper would usually settle it later, but if the same handler keeps failing, only a person can fix it, and until then a paying customer has no hours.

## How

In the `except Exception as exc:` branch, after `db.rollback()`, a new helper `_tell_grant_failed` calls `notify.tell_admins` and commits only the alert. The 409 answer is unchanged.

| Decision | Why |
|---|---|
| Only when the provider's report is `successful` (`tx.status == "successful"`) | A `failed` report grants nothing, so there is no money for an admin to chase; an exception there is not this alert's business. Pinned by a test |
| Key `admin.billing_cancel_grant_failed:{payment_id}` (**renamed 2026-11-20 to the neutral `admin.billing_grant_failed:{payment_id}`**, which the webhook and the sweepers now share: see `2026-11-20-grant-failure-sweeper-alert.md`) | One alert per payment. `alerts._write` does `ON CONFLICT DO NOTHING` on (user, dedupe_key), so a second press of the button does not alert again (tested) |
| Own kind, not `admin.billing_payment_refused` | A refusal (wrong amount) and a crashed grant are different jobs for the admin; the key's prefix before `:` is the notification kind |
| Body says the payment "could not be settled" (the guard catches any exception from `settle_transaction`, not only a grant handler) and has the payment id and the exception **type name** only | Never the exception text (a provider's or DB's words can carry emails, card data or amounts), never the person, never an amount. A test puts an email, a number and "secret" into the exception and asserts none reaches the alert |
| `notify.tell_admins` directly, not `subscriptions._tell_admins` | `notify.py` is the one way billing tells admins (the latter is a one-line wrapper); `cancel_checkout` now imports `notify` |
| Own transaction after the rollback: `tell_admins` writes inside a savepoint, then `db.commit()` | The settlement was undone, so only the alert may be committed. This is the same pattern as the refusal branch below it |
| The commit is wrapped: on failure a log line with the exception type, then `db.rollback()`, itself wrapped | `tell_admins` already swallows a failed alert write (savepoint, logs the type). The commit itself (a dropped connection) is the one step left that could raise, and it must not turn the 409 into a 500; the rollback is guarded too, so a dead connection cannot do it either |

**Rejected:** alerting from the log line alone (nobody reads logs); alerting on every exception regardless of the provider's status; putting `str(exc)` in the body "to help the admin" (a leak risk; the log line and the traceback in the server logs are for that).

## What's involved

| File | Change |
|---|---|
| `app/billing/cancel_checkout.py` | imports `notify`; the grant-failure branch calls `_tell_grant_failed`; new `_tell_grant_failed` at the end of the file |
| `tests/test_billing_cancel_checkout.py` | three tests (below); the module note and docstring now list the new path |

No migration, no setting, no endpoint, no FE change (the FE guide is unchanged: the 409 answer is the same).

## Verification

* `ruff check` and `ruff format --check` on the two touched files (ruff 0.17.3, the pin); `tests/test_billing_cancel_checkout.py` only, 35 passed on a scratch Postgres 17. CI runs the rest.
* `test_a_paid_page_whose_grant_fails_tells_the_admins_once_and_still_answers_409`: a raising grant handler gives exactly one alert row with the key, kind `admin.billing_grant_failed` (the neutral name since 2026-11-20), the body names the payment id and `RuntimeError` and leaks none of the planted text, the answer is 409 `payment_in_progress` **twice** (two presses), still one alert row, payment `pending`, no hours granted, no subscription.
* `test_a_grant_failure_whose_alert_cannot_be_saved_is_still_the_same_409`: `Session.commit` is made to raise once, right after the grant handler fails (the helper's commit of the alert), so the commit guard really runs: still the same 409, the payment `pending`, no hours, no alert row (the alert's transaction was rolled back). (`notify.tell_admins`'s own swallowing of a failed alert write is notify.py's behaviour and is not what this test proves.)
* `test_a_page_the_provider_says_failed_does_not_alert_when_a_handler_fails`: no alert for a `failed` report.
* Mutation check: with the change removed, the first test fails (no alert row). The second passes on the old code too, by design: the 409 is the same before and after; it pins that the new commit step cannot turn it into a 500.

**Not verified:** the guarded `db.rollback()` inside the commit guard (a rollback that itself raises) has no test; the alert's mail was not sent for real (the mail path is `alerts._mail_admins`, unchanged).

## Operate / roll back

Nothing to run. Revert the PR to go back to a log line only. When the alert arrives: look up payment N, read the server log line `cancel of payment N could not settle it: <Type>`, fix the cause; the provider's webhook or the sweeper then settles it (the payment is still `pending`), or ask the person to press cancel again.

## Follow-ups

| Gap | Note |
|---|---|
| ~~The other paths do not alert on a grant that keeps failing~~ (found by the Opus review of this PR) | **Done 2026-11-20** in `docs/sop/2026-11-20-grant-failure-sweeper-alert.md`: the webhook path and both sweepers alert once, after 30 minutes, under the shared neutral key `admin.billing_grant_failed:{id}` |
| ~~`settle.process` stores `str(exc)[:200]` in `billing_events.error`~~ | **Done 2026-11-20** (same SOP): it stores the exception type name only |
| A later change (a cancel button for an abandoned upgrade checkout) will touch `cancel_checkout.py` too | the diff here is kept small on purpose |
