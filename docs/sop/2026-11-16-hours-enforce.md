# SOP: billing stage S2, enforce the hours rule per person, with the new warnings

**What shipped.** With `HOURS_MODE=enforce` (still `off` by default; Adebayo switches it on himself) the person's own grants decide whether a new recording starts, each counted recording is split across their grants, and the person gets an 80% notice and a 100% notice (a bell entry and one mail each, once per person and period). The refusal says `reason` (`no_hours` or `no_plan`) and no longer says "pilot" or "hello@". `usage` in the session payload carries new fields. With `off` or `shadow` nothing changes for any person's joins. PR: see the PR for branch `feature/hours-enforce`. It is stage S2 of `docs/superpowers/specs/2026-11-15-billing-and-plans-design.md` (section 8, plus 2.2, 3.1 to 3.3 and 3.7). No migration. FE guide: `docs/fe-integration-guide-billing.md` (captures in `docs/fe-captures/hours-usage/`).

## Why

S1 put a person's hours into grants and compared the new rule with the old cap in a log. S2 makes the new rule the one that decides, so a plan, a trial, a pack and an admin gift can each be spent in a fixed order, a grant's hours left is always a sum (auditable), and the person is told once when their hours run low or out.

## How, and what was decided

| Piece | What it does |
|---|---|
| The block in `reserve_slot` (`app/placement.py`) | `enforce`: `entitlements.check_join` decides, under the placement lock, and raises 403 `hours_cap_reached` with `reason` (`no_hours`: hours held, all used; `no_plan`: none held). `off` and `shadow`: the old cap rule decides as before (shadow still logs). A running meeting is never touched: the rule only decides whether a NEW recording starts |
| Spending order (decided by the lead, 2026-11-16; spec 3.2 has the line) | (1) trial or pilot, (2) plan hours, oldest period first, (3) admin gifts and packs together, earliest `expires_at` first, no expiry last. The rule: whatever ends soonest is used first. `entitlements.spend_key` is the one place |
| State | a person with a plan period in force is `active`, even while a pilot grant still has hours (S1 review: it used to follow the spending order and showed `pilot`) and even with the plan's hours spent. Otherwise the first trial or pilot grant with hours; a pack or a gift alone is `none`. The admin is `unlimited` and is checked first everywhere |
| Allocation (`app/hours.py`, `allocate`) | each counted recording is split across the person's grants in force at the count, taking from the first grant in order and spilling into the next, into `usage_allocations` (a recording can have two rows). Idempotent per entry. An admin gets none. A **per-person advisory lock** (`HOURS_LOCK` 6020) stops two workers counting two recordings of one person at the same moment from spending the same hour twice |
| A recording longer than what is left | counted **in full** (a running meeting finishes), the excess **written off** (not billed, not carried into the next period; spec 3.3). It is stored as an over-draw on the last grant spent from (a row larger than that grant had left, which `entitlements` reads as zero left, never less). That row also marks the entry settled. Decided: this keeps "an entry with no allocation" meaning exactly "usage counted before allocations existed" |
| When allocations are written | `shadow` and `enforce`; **`off` writes nothing**. In `shadow` a failure is logged by exception class and user id only (`hours_shadow allocation failed ...`) and the count goes on; in `enforce` a failure stops the count (it is retried and counted once, `record_usage` is idempotent) |
| Usage counted during S1 (decided: how the first enforce run treats it) | S1 wrote no allocations and `entitlements.unallocated_seconds` took that usage off the grants anyway, so **nobody's hours change** when enforcement starts. `scripts/hours_reconcile_backlog.py` (dry run by default, `--apply` keeps it) turns it into allocations on the person's pilot grants, so it can never come off paid hours when the pilot grant later expires (S10). The usage term is now bounded by the pilot grant's expiry, never counts an entry that has any allocation (a written-off shortfall included), and excludes the recording being allocated (no double count). A test shows hours left identical before and after the script, and the S1 review's case (2 h in S1, pilot ends, plan bought: 18 h wrong, 20 h right) |
| The notices (spec 3.3) | 80%: used reaches 80% of what the person holds (every grant in force, spent or not; integer math, so 79.9% does not warn and 80% does). 100%: nothing left. Both are checked when a recording is counted, so a jump from 50% to 100% sends only the 100% notice. Dedupe key = kind + the person's **period key** (the newest plan period in force, else the first grant in spending order, else `none`), so one per person and period. The same 100% key is used when a calendar meeting is skipped for want of hours, so many skipped meetings raise one notice, not one each. Mail goes through `alerts.notify_user` (respects `notify_email`, bounces and suppression). The old once-ever 80% notice (`usage.hours_low` key `usage.hours_low`) runs only in `off` and `shadow` |
| Join runner | under `enforce` a skipped meeting gets skip reason `hours_cap` (`no_hours`) or the new `no_plan` (none held); both are retried until the deadline like `hours_cap` was, and both list as "Not recorded" with Send now. `no_plan` was added to `RETRYABLE_SKIPS`, `MOVABLE_SKIPS`, `NOT_RECORDED_SKIP_REASONS` and the reason texts. The per-meeting `calendar.hours_cap` notice stays in `off` and `shadow` |
| Refusal words | `hours_cap_message` (`app/services.py`) no longer names a pilot or an address. Three texts: the old cap rule names the cap, `no_hours`, `no_plan`. This text changes in every mode (the old "pilot" wording is wrong whatever the flag says); the decision does not |
| Admin cap edit (S1 review follow-up) | `admin.update_user` turns a raised cap into an `admin_gift` grant of the difference (no expiry, a written reason), in every mode, because the grants rule never reads `users.hours_cap_seconds`. Lowering or clearing a cap gives and takes nothing |
| `usage` in the session payload | old fields kept; under `enforce` they are read from the grants (`cap_seconds` = hours held now, `used_seconds` = held minus left, running meetings counted). New fields `state`, `seconds_left`, `warning`, `can_record`, `unlimited`, `period_ends_at`, `trial_ends_at`; **all null unless `enforce`** |

Rejected: writing allocations in `off` (the flag means the hours code does not run); a migration for a "settled" marker (it would have clashed with the billing provider layer's 0050 and the over-draw row does the same job); deciding the 80% share against the old lifetime cap for pilot accounts (the spec says "of what the person holds").

## What's involved

| File | Change |
|---|---|
| `app/entitlements.py` | `spend_key`, `grants_in_force`, `unallocated_seconds`, `held_after_backlog`; `status` gains `holdings_seconds`, `warning`, `period_ends_at`, `trial_ends_at`, `period_key`; state rule |
| `app/hours.py` | new: `allocate`, `reconcile_backlog`, `notify_low`, `notify_out`, `notify_out_of_hours`, `after_counted` |
| `app/usage.py` | `record_usage` allocates (and notifies under `enforce`); takes `settings` |
| `app/placement.py` | the `enforce` branch of the block; `PlacementRefused.reason` |
| `app/services.py`, `app/api.py` | refusal words and `reason` in the error body; `_usage_out` |
| `app/api_schemas.py`, `docs/openapi.json`, `web/lib/api/schema.d.ts` | the new `UsageOut` fields (the type file was edited by hand to the generator's output: `npm run gen:api` was not run, no `node_modules` here) |
| `app/join_runner.py`, `app/gcal_sync.py`, `app/calendar_text.py`, `app/mail_summary.py` | `no_plan` and the one-notice rule |
| `app/admin.py` | the gift for a raised cap |
| `scripts/hours_reconcile_backlog.py`, `scripts/capture_hours_usage.py` | new |
| `.env.example`, `deploy/README.md`, `pyproject.toml` (a `F811` ignore for the new test file, as the other fixture-importing tests have) | docs and lint |
| `tests/test_hours_enforce.py` (new, 53 tests); `tests/test_usage.py`, `test_hours_shadow.py`, `test_entitlements_status.py` (updated: the new null fields, the message words, `enforce` is no longer a no-op, a spent plan is `active`) | |

## Verification

* **Run locally** (lint, format and the named files only; CI runs the rest): `ruff check .` and `ruff format --check .` clean (ruff 0.17.3, the pin). 53 new tests pass: boundaries at 79.9%, 80%, 99.9% and 100% (state, join decision, notices), a running meeting finishing past zero (counted in full, never below zero, nothing carried to the next period, no second notice), the join-runner skip reasons and one notice for two skipped meetings, two accounts never sharing hours, a recording split across two grants (and across trial then plan), the spending order, the notice sent once per person and period (and again in a new period), `notify_email` off, flag off and shadow changing nothing (the old rule decides, no allocation in `off`, no new notice in either), an allocation failure in each mode, two workers counting at once (a mutation without the lock fails the test), the S1 backlog (hours left identical before and after reconcile, the S1 review's case, an over-cap entry, no double count beside a new recording, the script's dry run), the admin gift, and the payload and refusal over real HTTP. Mutation checks: changing `>=` to `>` at 80% and making the notice key unique per call each fail the tests that should catch them. Also run and passing: `test_placement`, `test_join_refusal`, `test_hours_grants`, `test_hours_shadow`, `test_hours_shadow_report`, `test_entitlements_status`, `test_usage`, `test_ai_runs_usage`, `test_join_runner`, `test_admin`, `test_send_now`, `test_mail_summary`, `test_gcal_sync`, `test_calendar_api` and the OpenAPI drift test.
* **Live capture** (the real app under uvicorn with `HOURS_MODE=enforce`, real migrations, real HTTP): `scripts/capture_hours_usage.py`, files in `docs/fe-captures/hours-usage/`. It shows the payload for the admin, a pilot grant, 80%, out of hours, a count past zero, a plan plus pilot, a trial, no hours, the flag off, the two 403 refusals and the notices. The full suite was not run; CI is the authority.
* **Not verified:** nothing ran against production data or a real meeting (the recordings in the capture are counted by calling `usage.record_usage`, not by a real bot and pipeline); the mail a notice queues was checked in tests only (the capture does not read the outbox); no real Skribby or Postgres at production scale; `HOURS_MODE=enforce` has not been on in a running server.

## Before switching on (for Adebayo; none of this is done by the deploy)

1. **Accounts made after migration 0049 hold no hours** (the trial is stage S10). Under `enforce` they are refused with `no_plan`, and so is any new sign-up while sign-up is open. Close that first: the S10 trial, or a grant for those accounts. `scripts/hours_shadow_report.py` lists them as `old rule allows, new rule refuses: no grant in force`.
2. Deploy S2 with `HOURS_MODE` unset (nothing changes), then set `shadow` and recreate web, worker and scheduler so counted recordings start being allocated.
3. `docker compose exec -T web python scripts/hours_reconcile_backlog.py </dev/null` (a dry run: it prints how many hours per person it would settle, "user 1", "user 2", and keeps nothing), then the same with `--apply`. Running it again changes nothing.
4. `docker compose exec -T web python scripts/hours_shadow_report.py </dev/null`: every disagreement must be explained (point 1 is the expected one).
5. Set `HOURS_MODE=enforce`, recreate web, worker and scheduler.

## Operate / roll back

Roll back the behaviour by setting `off` (or `shadow`) and recreating. The grants, allocations and gifts stay and do nothing; the old rule reads the ledger, which `enforce` kept up to date (the same counts), so the two rules agree again at once. Notices already sent stay. Revert the PR to remove the code; there is no migration to undo.

## Follow-ups

| Item | Why it matters |
|---|---|
| The sign-up gap (point 1 above) | blocks `enforce` |
| `app_state.default_hours_cap_seconds` changes (`app/admin.py` pilot settings) do not make grants. Only a per-person cap raise does | an admin who raises the app-wide default expects every account to gain hours; under `enforce` nobody does |
| The gift's `created_by` is empty: `admin.update_user` does not know which admin pressed the button | audit; pass the actor from `app/api_admin.py` |
| `web/app/share/page.tsx:22` builds its own "for the pilot ... hello@beyrictech.com" sentence from `cap_seconds` instead of showing the server's message | the FE session; the backend message is clean |
| The 80% share for a pilot account is of the grant it holds (its leftover from the migration), not of the old 5 hours | a pilot person with 1 h left is warned after 0.8 h more |
| A pack or an upgrade bought mid-period does not reset the notices (the period key is the plan period or the first grant) | spec 3.3 says the flags reset at a period start; revisit with S8 |
| The 80% and 100% checks include the running-meeting estimate (waiting-room time too), so a notice can come slightly early | the same estimate that gates joins |
| The Terms still say "free during the pilot" (`web/components/legal/terms-content.tsx:30`) | S11 |
| The skipped meeting's stale notice ("Sharing notes between accounts is not available yet") | S12 |
