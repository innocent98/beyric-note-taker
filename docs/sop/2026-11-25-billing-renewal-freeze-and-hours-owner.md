# SOP: a renewal that cannot be traced no longer freezes the plan, and paid hours follow the account (billing review S1 and S2, 2026-11-25)

**What shipped.** S1: a renewal attempt whose outcome nobody can find now ends in the status machine (grace, retries, lapse, admin alert) instead of leaving the subscription `active` for ever. S2: a plan period, a pack and an admin gift are spent by account (`hour_grants.user_id`), never by the fingerprint of an email address; migration 0065 repairs any such grant that lost its owner. Plus one plain gap from the notes (N1: a pack checkout waits for an unsettled refund). Branch `fix/billing-renewal-freeze-and-hours-owner`; PR number in the pull request list. **It is money code: it needs an Opus delta review before merge.** Migration 0065 (data repair only, no schema change). No config.

## Why

Review `billing-review-2026-11-25` (M1 was fixed in the SOP `2026-11-25-billing-stale-payment-lock.md`).

**S1.** `renewals._attempt` commits the attempt's payment row, calls `charge_saved_method`, and on `ProviderError` or `OutcomeUnknown` asks the provider by our reference. If that also failed, the payment stayed `pending` and the pass returned. Every later pass did the same, and `_fail` (the only thing that moves `active` to `past_due`, opens the 72 hours of grace, schedules day 1 and day 2, sends the mail) never ran. The stored status stayed `active`, the pay link answered 502 every time, and 72 hours later a new plan was refused (`already_subscribed`) because the stored row was still live. It happened for a charge that was never made at all (a Paystack 4xx, a missing email, a missing key) and for a timeout where the charge never registered (Flutterwave `NoRecord`). A 6-hour admin alert was the only exit.

**S2.** `entitlements.mine` matched a grant by `user_id` OR by `email_hash` (the fingerprint of the address at payment time), for every kind. After a Google sign-in rewrote the address (`google_signin._sign_in_by_sub`), or an account was deleted (the foreign key empties `user_id`, the fingerprint stays) and the address was given to someone else, that someone matched the first person's paid grants and spent their hours.

## How

**S1: four cases, one machine** (the first version of this fix closed a given-up charge as `failed` and let the next slot charge again; the Opus delta review (2026-11-25) found that could debit a person twice, so it was reworked: a charge that may exist is NEVER closed on a guess).

| Case | What happens | Where |
|---|---|---|
| The charge call was refused for a reason about the card or the request (`types.NothingMoved`, a `ProviderError`: a 4xx, an input check) and the lookup finds nothing | the attempt FAILED at once: payment `failed`, admins told once, `_fail` moves the machine | `renewals._attempt`, `_charge_refused` |
| The call was refused for a reason that is OURS or the provider's (`types.ChargeBlocked`, a `NothingMoved`: a missing or refused key, Paystack 401, Flutterwave 401 and 403, a 429) | **not the person's failure**: the payment row stays as history (`failed`, `attempt` set null so the slot is free), the status machine does NOT move (no grace, no mail, no retry spent), `next_charge_at` holds the charge back 30 minutes (`BLOCKED_RETRY`; `states.due_action` now honours `next_charge_at` for an `active` row), admins told once an hour per provider | `_charge_blocked` |
| The call got no answer (`OutcomeUnknown`, or a plain `ProviderError`: a timeout, a 5xx, an unreadable answer) and the lookup cannot find it, or the provider keeps saying `pending` with no page for the payer | waits `UNRESOLVED_AFTER` (6 hours from the payment row), then **counts as a failed attempt for the clock only** (`_note_unresolved`: admins told, `outcome gave_up` -> `_fail`) | `_settle_open_payments` |
| ...every later pass | the payment is still `pending`, so it is asked about FIRST at every pass: found paid, it is settled and nothing more is charged for the period; still unknown, the slot is counted (`_fail`) but **nothing is charged**, and the pay link makes no second page (502 `provider_error`, retry) | `_settle_open_payments`, `_open_page` (unchanged) |

So an untraceable charge ends in the ordinary machine: `past_due`, grace to the due moment + 72 h, the "payment did not go through" mail and bell, the day-1 and day-2 slots counted (the last-day mail), then `lapsed` at the normal moment, after which a new plan is allowed. Money is never taken twice by us: no new charge and no new page while an earlier charge of the period is unresolved. A late webhook, or the lookup coming back, settles it (the plan renews from the payment, as for any late payment).

Adapters: `NothingMoved` is raised by `charge_saved_method` for every pre-check, for Paystack's other 4xx answers and Flutterwave's `_major_text`/`_encode` checks; `ChargeBlocked` (a subclass) for a missing key (`paystack.NotConfigured`, Flutterwave), Paystack 401 and 429 and Flutterwave 401, 403 and 429. A timeout, a 5xx, a 200 that cannot be read, a mismatch between the answer and the request, and any odd status code stay plain `ProviderError` or `OutcomeUnknown`: those may have charged the card. Because both are `ProviderError`s, every reader that treats "no answer" as no answer is unchanged.

Rejected: closing a given-up charge as `failed` so the next slot can charge (double-charge risk, the review's must-fix); marking every `ProviderError` from a charge as failed at once (a Paystack timeout may have charged); a short deadline (a provider outage of a few hours must not make people's plans fail); extending `grace_ends_at` or the paid period while our key is broken (it would change what a paid period is: the hold delays the charge and spends no retry, but the ordinary lapse still comes 72 h after the due moment if the key is never fixed; the hourly admin alert says so); changing `start_plan_checkout` to judge the live row by the clock (see Follow-ups).

**S2: spend by account.** `entitlements.my_grants(user)`: `user_id = user.id`, or (the grant lost its `user_id` AND is a `trial` or `pilot` AND carries this address's fingerprint). The second branch keeps the documented design that the free hours of a deleted account are found again by the person who signs up again; paid kinds never use it. Used by `grants_in_force` (spending, status, `check_join`, the allocation order) and by `hours.sync_cap_gift` (so another person's cap gift does not count against this one). `trial.had_one` and `trial.waiting` keep the fingerprint on purpose: it is the abuse check (no second trial for an address that had one). **Usage follows the account too** (delta review): `usage.owned_entries(user)` (`user_id = user.id`, or the row lost its `user_id` and carries this address's fingerprint, so a deleted account's recordings still count against whoever signs up again) replaces the fingerprint match in `usage.ledger_seconds`, `entitlements.unallocated_seconds` and `pre_pilot_seconds`, `hours.reconcile_backlog` and `_cap_baseline`; those four also anchor on `my_grants` for the trial and pilot start. Without it, the person who took over an address had the last owner's unallocated recordings taken off their hours, and `reconcile_backlog` could settle the last owner's recordings onto their paid grant.

**Why a clean account binding is possible.** Every paid grant is created with `user_id` (`subscriptions._grant`, `hours.sync_cap_gift`, `trial.give`), so no schema change is needed. A `user_id` is only emptied by deleting the account, and then the hours must go with it. Migration 0065 therefore only repairs the exceptions: a `plan_period` or `pack` grant with no `user_id` whose source payment's payer, or subscription's owner, is a live account (not `deleted`, not purged); it never overwrites a `user_id`, and skips a grant whose payer and plan owner disagree. A trial, a pilot and an admin gift are left alone. The migration is expected to touch no row on a healthy database; downgrade does nothing (the values it sets are correct).

**N1 (the part that is a plain gap).** `subscriptions.start_pack_checkout` now calls `_refuse_while_refunding` like the upgrade, change and pay link do: 409 `refund_pending` while a refund of the plan's first payment is `pending` or `unknown`.

## What's involved

| Path | Change |
|---|---|
| `app/billing/types.py` | `NothingMoved(ProviderError)` |
| `app/billing/paystack.py` | `NotConfigured` is a `NothingMoved`; `_call(refused=...)` and `_data(refused=...)`; the charge raises `NothingMoved` for pre-checks and 4xx |
| `app/billing/flutterwave.py` | `_major_text`, `_encode`, a missing key, 401, 403, 429 raise `NothingMoved`; the charge's pre-checks too |
| `app/billing/renewals.py` | `_attempt` (the `NothingMoved` branch, `gave_up`), `_charge_refused`, `_give_up`, `_settle_open_payments` (the two give-up branches); module note |
| `app/billing/fake.py` | charge outcome `refused` (test double) |
| `app/entitlements.py`, `app/hours.py` | `my_grants`; `grants_in_force` and `sync_cap_gift` use it |
| `app/billing/subscriptions.py` | `start_pack_checkout` refuses while a refund is unsettled |
| `alembic/versions/0065_grant_owner_backfill.py` | the backfill (head: 0065, chain 0064 -> 0065) |
| `docs/fe-integration-guide-billing.md`, `docs/fe-captures/billing-grant-owner/` | what the screens may now see; the one captured 409 |

## Verification

Tests written first and watched failing, then green (real Postgres, fake clock, scripted provider; local runs were the touched files only, CI runs the rest):

- `tests/test_billing_renewals.py` (new): a refused charge fails the attempt at once; a charge nobody can find moves the clock after 5 hours of waiting and 7 hours of silence but stays `pending` and is never charged again (the day-1 and day-2 slots are counted, the last-day mail goes, the plan lapses, a new plan is allowed); a given-up charge whose lookup comes back paid at the day-1 slot is settled with ONE charge and one new grant (for both `ProviderError` and `OutcomeUnknown`, with the lookup down for the six hours); a given-up charge paid by its webhook is settled; a QUEUED charge that succeeds after the window gives exactly one charge and one grant; a queued charge still pending moves the clock and charges nothing; the pay link makes no second page beside an untraceable charge; a charge blocked by our key or a rate limit spends no retry, sends no customer mail, alerts the admins, is not retried for 30 minutes and then renews with the fixed key; a webhook that settles the payment while the lookup fails is not counted as a failure. One existing test seeded a held slot with a real-clock `created_at`; it now uses the test clock.
- `tests/test_billing_paystack.py`, `tests/test_billing_flutterwave.py` (new): which failures are `NothingMoved` (other 4xx, every pre-check), which are `ChargeBlocked` (missing key, Paystack 401 and 429, Flutterwave 401, 403 and 429) and which are neither (timeouts, 5xx, unreadable 200, odd status). `tests/test_billing_states.py`: an `active` row past its end honours `next_charge_at`.
- `tests/test_hours_grant_owner.py` (new, 9 tests): after an address change and after a deletion the next owner of the address spends nothing (plan period, pack, admin gift, and a trial after an address change); the payer keeps all of it; an orphaned pilot or trial is still found by the fingerprint; another person's cap gift is not counted; a second trial is still refused to an address that had one. Delta review: the previous owner's unallocated usage does not come off the next owner's hours, `reconcile_backlog` settles nothing onto their grant, the ledger and `pre_pilot_seconds` are the account's own, the cap baseline ignores the previous owner's pilot and usage, and usage a deleted account left still counts against the next person at the address.
- `tests/test_billing_grant_owner_migration.py` (new): one head, 0065 after 0064; the backfill binds exactly the clear cases and none of the others; upgrade, downgrade, upgrade again, on a scratch database. `tests/test_migrations.py` passes (single head, models match).
- `tests/test_billing_refunds.py`, `tests/test_billing_subscriptions_api.py` (new, 1 each): the pack checkout 409 at service level and over HTTP (the capture).
- Also run and green: `test_entitlements_status`, `test_hours_grants`, `test_hours_enforce`, `test_hours_reserve`, `test_hours_shadow`, `test_billing_subscriptions`, `test_trial`, `test_usage`, `test_billing_states`, `test_billing_stale_payment_lock`. `ruff check` and `ruff format --check` clean.

**Not verified:** anything against a real provider. Not confirmed from a vendor page: what Paystack or Flutterwave answer to a lookup of a reference they never saw (the design does not depend on it: either answer waits then gives up), and that Paystack's `charge_authorization` 4xx means nothing was charged (it is documented that a card decline is a 200 with `status: failed`; a 4xx is a refused request).

## Operate / roll back

Deploy normally; migration 0065 runs on start and should update 0 rows (count `hour_grants` rows of kind `plan_period` or `pack` with a null `user_id` before and after if you want to see it). New admin alerts to expect: "A renewal charge was refused by the provider" (a pile of these for different people means a key or adapter problem) and "A renewal charge was given up on" (look the reference up in the provider's dashboard). Roll back by reverting the PR; the migration needs no downgrade step.

## Follow-ups

Notes of the review not fixed, with the reason:

| Note | Not fixed because |
|---|---|
| N1, the rest (a pack paid after the plan ended still grants hours) | the person did pay, and whether to grant or flag for refund is a product decision; no money is lost either way |
| N2 (a cancel pressed during an in-flight renewal still renews) | rare; the fix changes what happens to money already taken, so it needs a decision (grant and keep, or refund) |
| N3 (trial abuse by address aliases) | an anti-abuse policy choice, bounded by the daily budget and `max_accounts`, not a bug |
| N4 (does Paystack's `POST /refund` take `reference`) | a vendor fact only the first real refund can prove; nothing retries, so no double refund either way |
| N5 (a refund that later fails leaves the person with nothing) | by design, with an admin alert; keep the runbook step |
| N6 (Flutterwave lookup by reference with several attempts) | not verified with the vendor; check on the first USD payment (pay after one deliberate decline if practical) |
| N7 (upgrade proration below a provider minimum) | needs the providers' minimum amounts, which are unverified, and refusing late upgrades changes the product |
| N8 (renewal day-of-month drift, UTC) | documented in `plans.py`; a billing-page wording choice |

Also: `start_plan_checkout` still judges the live row by its stored status (review S1 suggested the clock). Not done: with the give-up a stored row cannot freeze on an untraceable charge, a row the scheduler simply has not reached is charged by it on recovery, and a new plan beside a stored-live row would hit `uq_subscriptions_one_live`. A lint rule for `with_for_update()` without `populate_existing` (from the M1 SOP) is still open. A real lookup that says "no record" (Paystack `verify` of an unseen reference, as Flutterwave's `NoRecord`) could shorten the 6 hours for an unknown outcome once the vendors' answers are confirmed live. An admin tool to resolve an untraceable charge by hand (mark it paid or failed after looking at the provider's dashboard) would let a person pay at once instead of waiting for the lapse: today a charge that may exist blocks every new charge and page for its period, and the admins are told at six hours to look it up. Paystack's lookup of a reference it never saw is not confirmed to be distinguishable from an outage (Flutterwave's `NoRecord` is, but its adapter already folds it into `OutcomeUnknown`), so a positive "no record" is not used to supersede a charge.
