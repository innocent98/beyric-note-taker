# SOP: tell the admins when a paid payment's grant keeps failing in the background

**What shipped.** When the provider says a payment is **paid** and granting its hours or plan keeps failing in the background (the webhook, or either sweeper), the admins now get **one** alert (bell and mail) for that payment, once the failure has lasted 30 minutes. The alert key is neutral, `admin.billing_grant_failed:{payment_id}`, and the cancel button uses the same one, so a payment is announced once whichever path sees it first. `billing_events.error` no longer stores exception text. Branch `fix/grant-failure-sweeper-alert`; PR: see the PR for this branch (not merged by the author; the lead merges after an Opus review and a delta review). Follow-up to [`2026-11-19-cancel-grant-failure-alert.md`](2026-11-19-cancel-grant-failure-alert.md) (#244), whose Opus review found these paths silent.

## Why

A grant that raises is undone with the whole step, so the payment stays `pending` and the person has paid for nothing. Before this change:

* `settle.process` (the webhook, and `_sweep_events`) parked the event and raised `RetryLater`;
* `renewals._sweep_payments` caught `Exception` and logged the type;
* so a customer was surfaced only by pressing Cancel (#244) or by the 24 to 48 hour `admin.billing_pending_old` digest, which lists `pending` rows only and names no cause.

Separately, `settle.process` stored `f"{type(exc).__name__}: {str(exc)[:200]}"` in `billing_events.error`. An exception's text can hold an email, an amount or the provider's own words, and that column is kept for ever (a PII-in-DB risk). Nothing in the app reads this column except tests (checked: the admin API's `error` field is `refunds.error`, a different table).

## How

| Decision | Why |
|---|---|
| **One neutral key** `notify.grant_failed_key(payment_id)` = `admin.billing_grant_failed:{id}`; the cancel helper's one key line now calls it | One payment, one alert, whichever path is first. `alerts._write` does `ON CONFLICT (user_id, dedupe_key) DO NOTHING`. Both orders are tested (cancel then sweeper, sweeper then cancel); the title and body are the same words on both paths |
| **Alert only when the provider itself said `successful`** | In `settle._apply` the exception is wrapped (`_PaidButNotGranted`) only for a `charge_succeeded` event whose re-read transaction is `successful` and well formed, so only a failure inside the grant step counts. In `_sweep_payments` a local `paid` flag is set only after `lookup_by_reference` returned `successful`. A `failed` or `pending` report, a provider that cannot be asked (`ProviderError`) and a lookup that blows up in an unexpected way never alert (all tested) |
| **Threshold: 30 minutes** (`settle.GRANT_ALERT_AFTER`), measured on a durable timestamp that is already in the data | The first failure is logged only. The sweeper asks again every 2 minutes, so 30 minutes is about 15 failed passes (or several of the provider's own webhook retries): a one-off lock timeout or deadlock that the next pass clears never pages anyone, a persistent failure is looked at within the hour. In-memory counters are not used: workers restart |
| The clock is `billing_events.received_at` on the event path (the webhook, a provider retry, `_sweep_events`) and `payments.created_at` on the payment-sweeper path | An event row exists before anything is decided, so its age is exactly "how long has this been failing". A payment has no such marker: a failed grant leaves it `pending` and `last_checked_at` moves on every pass. Its checkout age is the only durable clock without a migration. **Known limit:** a payment whose checkout was left open for hours and is paid just now, with no event stored, alerts on its first failed sweep (age already over 30 minutes), not after 30 minutes of failing. That is the accepted trade: a rare transient failure then costs one "look at it" line, while a missed one is a customer who paid and has nothing. If it proves noisy, the exact fix is a nullable `payments.grant_failed_at` (see follow-ups) |
| The alert is written by `notify.tell_grant_failed`, which commits **only the alert**, in its own transaction after the rollback, with the commit and the rollback guarded | Same pattern as #244. `tell_admins` already swallows a failed write (savepoint, logs the type); the commit is the one step left that could raise, and it must not stop the sweep or turn the webhook's 503 into a 500. Both failures are tested: the sweep goes on and settles the next payment |
| Body: payment id and exception **type name** only | Never `str(exc)`, an amount, an email or provider text. The test plants an email, a number and "secret" in the exception and asserts none is in the alert, the stored error or the log |
| `billing_events.error` now holds the exception type name (`RuntimeError`, `ProviderError`) for any failure; `RetryLater` keeps its own message | `RetryLater`'s only message is a literal of ours ("the provider still reports the payment as pending"). The shape (a nullable text, `processed_at` null while waiting) is unchanged. The only readers were tests (updated: `test_billing_inbox.py`, `test_billing_paystack_flow.py` asserted provider words) |
| The log line is `type at file:line`, not `logger.exception` | The traceback of `logger.exception` ends with `Type: <message>` and those lines are kept. `settle._log_failure` logs the type and the innermost frame location, which is what an operator needs to find the cause, without any text |
| `_sweep_events` is unchanged apart from a comment | It calls `settle.process`, which now alerts, so the event sweeper is covered by the same code as the webhook; its own `except Exception` only sees a stored body that will not parse, where nothing was paid or granted |

**Rejected:** alerting on the first failure (a blip pages someone); an in-memory failure counter (lost at every restart); alerting from a `RetryLater` alone (a provider that is down or still says pending is not a paid customer); a new `payments.grant_failed_at` column (exact, but a migration on the money table in the week of the production release, and the lead expected none; recorded as the follow-up); alerting on any exception from the sweeper's lookup (could be the provider's blip, nothing is known to be paid); `logger.exception` kept "for debugging" (the text would still reach the logs).

## What's involved

| File | Change |
|---|---|
| `app/billing/notify.py:25,32` | `grant_failed_key`; `tell_grant_failed` (alert and guarded commit) |
| `app/billing/settle.py:92-98` | `GRANT_ALERT_AFTER`, `_PaidButNotGranted`; `:131` `_log_failure`; `:139` `alert_grant_failed` (the threshold); `:105` `process` (type-only error, alert); `_apply` wraps the grant step; the module note explains the rule |
| `app/billing/renewals.py:628` `_sweep_payments` | `paid` flag; alert in the `except Exception` branch; `_sweep_events` comment only |
| `app/billing/cancel_checkout.py:204` | **the key line only**, `notify.grant_failed_key(payment_id)` (another change is editing this file). Its docstring at line 25 still names the old key `admin.billing_cancel_grant_failed:<id>`; fix that one word after that change lands |
| `tests/test_billing_grant_failure_alert.py` (new, 17 tests) | below |
| `tests/test_billing_cancel_checkout.py`, `test_billing_inbox.py`, `test_billing_paystack_flow.py` | the new key; the stored error is the type |

No migration (Alembic head stays 0054), no setting, no endpoint, **no FE change** (no response shape changed; the FE integration guide is unchanged).

## Verification

Locally, on a scratch Postgres 17 (removed afterwards): `ruff check .` and `ruff format --check .` (ruff 0.17.3, the pin), and only these files: `test_billing_grant_failure_alert.py`, `test_billing_cancel_checkout.py`, `test_billing_inbox.py`, `test_billing_paystack_flow.py`, plus the `sweep` tests of `test_billing_renewals.py`. CI runs the full suite.

| Test | Proves |
|---|---|
| paid event, grant keeps failing (webhook path, redelivered at 0, 10, 29, 31, 45, 90, 600 minutes) | no alert before 30 minutes; exactly one alert row after, however many passes; payment still `pending`, nothing granted |
| stored error, alert and log hold no exception text | an email, a number and "secret" in the exception are absent from `billing_events.error` (`RuntimeError`), the alert and the captured log |
| a `ProviderError` with text on the event path | the stored error is `ProviderError`, the text is absent from error and log |
| provider cannot be asked (event path and sweeper; `ProviderError` and `ValueError`) | no alert however old |
| one failed pass the next clears | settled on the next sweep, no alert ever |
| provider says `failed` or `pending` | no alert, no grant (both paths) |
| sweeper alone, no event | no alert at 3, 6, 25 minutes; one alert from 31 minutes over seven more passes |
| event waiting plus the payment sweeper on one payment | one alert |
| grant recovers after nine failures | one alert, then settled, nothing new |
| alert write raises / alert commit raises | the sweep goes on and settles the other payment; logged by type only |
| cancel then sweeper, sweeper then cancel | one alert for the payment, the same title and body from both paths |

Mutation checks: `GRANT_ALERT_AFTER` set to 0 fails four tests; `if paid:` replaced by `if True:` fails the unexpected-lookup-error test. (The first version of that guard test passed under the mutation, because `ProviderError` is caught by an earlier clause; the `ValueError` case was added for that reason.)

**Not verified:** against a real Paystack or Flutterwave (the fake provider is scripted); the alert mail was not sent for real (`alerts._mail_admins`, unchanged); the guarded `db.rollback()` inside the commit guard (a rollback that itself raises) has no test.

## Operate / roll back

* **How an admin sees it:** a bell entry and one mail, title "A paid payment could not be granted", body "The provider reports payment N paid, but it could not be settled (RuntimeError). Look at it." Open payment N (`GET /api/admin/billing/payments`), read the server log line `billing event E was paid but could not be granted: <Type> at <file>:<line>` or `sweeping payment N failed: <Type>`, and fix the cause. The payment is still `pending`; the next sweeper pass (or the provider's next webhook retry) settles it once the handler works, with no manual step. The alert is not repeated and is not cleared; if the cause is a bug, fix it and watch the payment turn `succeeded`.
* **Roll back:** revert the PR. The alert returns to the cancel path only, under the old key name; nothing is stored that needs undoing (`billing_events.error` values already written are just shorter).

## Follow-ups

| Gap | Note |
|---|---|
| A payment with no stored event alerts on its first failed sweep when its checkout is over 30 minutes old (see the table above) | Add a nullable `payments.grant_failed_at`, set on the first failure and read by all three paths, if the alert is ever noisy; needs a migration |
| `cancel_checkout._tell_grant_failed` duplicates `notify.tell_grant_failed` (same title, body, key, guarded commit) | collapse it once the other change to `cancel_checkout.py` has merged; also fix the old key in that file's docstring (line 25) |
| The saved-card renewal paths in `renewals.py` (`_settle_charge`, `_settle_open_payments`, `_open_page`) call `settle_transaction` and, on a raising handler, re-raise or refuse without this alert | not in the scope asked; the sweepers settle those payments later and now alert |
| The alert does not clear itself when the payment is finally granted | an admin's note, not a state; fine for now |
