# SOP: billing stage S1, the hours model in shadow

**What shipped.** A person's recorded hours can now be held as **grants** (`hour_grants`, `usage_allocations`, migration **0049**), and `app/entitlements.py` can say what a person's state and hours left are and whether a new recording would be allowed. With `HOURS_MODE=shadow` every recording start also asks that new rule and logs one line when it would have decided differently from today's cap rule; the old rule still decides. `HOURS_MODE` defaults to `off` and then nothing runs. A read-only report, `scripts/hours_shadow_report.py`, prints the comparison per person. PR: #211, branch `feature/hours-model-shadow`. It is stage S1 of `docs/superpowers/specs/2026-11-15-billing-and-plans-design.md` (section 8, "S1 in detail"). **No payload, route or screen changes**, so there is no FE integration guide change; `docs/fe-integration-guide-billing.md` starts in S2.

## Why

The spec's per-person model (a plan belongs to a person, hours are charged to the recorder) is nearly what the code does today (`usage.record_usage` charges the recorder, the cap is per user, one block in `placement.reserve_slot` is the gate). What changes is the **number**: a lifetime cap becomes hours held in grants (a trial, a pilot grant, a plan period, a pack, an admin gift). A mistake in that rule must show up in logs before any customer meets it, so it ships first beside the old rule and decides nothing.

## How, and what was decided

| Piece | What it does |
|---|---|
| `hour_grants` | one row per source of hours: `user_id` (null after the account is deleted), `email_hash` (the same fingerprint as `usage_entries`), `kind` (`trial`, `pilot`, `plan_period`, `pack`, `admin_gift`), `seconds`, `starts_at`, `expires_at` (null = never), `source_payment_id`, `subscription_id` (no foreign keys yet: those tables arrive in S4 and S8), `reason`, `created_by`, `created_at`. Unique on (`kind`, `source_payment_id`) so a replayed payment can only grant once |
| `usage_allocations` | the part of one counted recording taken from one grant. **S1 writes none**; S2 writes them as each recording is counted. A grant that has been spent from cannot be deleted (`ON DELETE RESTRICT`) |
| The backfill (inside 0049) | one `pilot` grant per account that is not an admin and not deleted (`status <> 'deleted'` and `purged_at is null`): the cap (own `users.hours_cap_seconds`, else `app_state.default_hours_cap_seconds`, else 18000) minus the ledger (`usage_entries` by user id **or** email fingerprint), never below 0, no expiry. `starts_at` is the migration's moment. It is one SQL statement, and the fingerprint is recomputed in SQL as `sha256(lower(trim(email)))`, which `tests/test_hours_grants.py` proves equals `usage.email_hash` |
| `app/entitlements.py` | `status(session, user, now)` returns `Status(state, seconds_left, unlimited, has_grant)`; `check_join(session, user, now)` returns `JoinDecision(allowed, reason, seconds_left)` with reason `no_hours` (a grant is in force but spent) or `no_plan` (none in force). Reads only; imports no route |
| Spending order | trial or pilot, then plan hours, then admin gift, then packs by soonest expiry (spec 3.2; the spec does not place an admin gift, so I put it before the packs) |
| Usage after the migration | S1 writes no allocations, so usage counted **after** the person's pilot grant began and with no allocation is taken off the grants in that order (`entitlements._unallocated_seconds`). The migration already netted everything before it out of the grant. The estimate for the person's own running meetings (`usage.running_seconds`, extracted from `usage.hours_used` with no change in what it returns) comes off the same way. Together this makes the new rule equal to the old one for a backfilled account, which is what lets the shadow be silent when nothing is wrong |
| States | only grants exist, so only `trial`, `pilot`, `active` (a plan period) and `none` can show. `past_due` and `lapsed` need subscriptions (S8). A pack or an admin gift alone shows `none` but still allows a recording. An admin is `unlimited` (as `effective_cap` returns no cap) |
| `HOURS_MODE` | `off` (default), `shadow`, `enforce`; blank means off; any other value stops web, worker and scheduler at startup naming the setting. **Only `shadow` does anything in S1**; `enforce` is accepted and behaves as `off` until S2 |
| The shadow in `reserve_slot` | under the placement lock, after the old decision is computed and **before** it is raised, so "old refuses, new would allow" is logged too. It runs in a savepoint inside `try/except`: a failure is logged (`hours_shadow failed`) and the old rule goes on alone, and the transaction and the lock are untouched. The line is `hours_shadow disagree user=<id> old=<allow\|refuse> new=<allow\|refuse> reason=<..> seconds_left=<n>`: an id, never an email or a name |
| `scripts/hours_shadow_report.py` | like `scripts/ai_cost_report.py`: `SET TRANSACTION READ ONLY`, SELECTs only, people only as "user 1", "user 2" ranked by hours recorded in the window (`--days`, default 30). Per person: hours recorded, old cap minus used and decision, new state and hours and decision, same or DIFFERENT; then the disagreements by kind. Admins and deleted accounts are left out |

Rejected: writing allocations from `record_usage` in S1 (that is S2's change to the write path and the spec keeps S1 free of behaviour change); giving each new sign-up a grant in S1 (a sign-up change, see Follow-ups); running the shadow only after the old check (it would never see "old refuses, new allows").

## What's involved

| File | Change |
|---|---|
| `alembic/versions/0049_hour_grants.py` | the two tables, the backfill, a downgrade that refuses while a grant other than `pilot` exists |
| `app/models.py` | `HOUR_GRANT_KINDS`, `HourGrant`, `UsageAllocation` next to `UsageEntry` |
| `app/entitlements.py` | new: `status`, `check_join`, `Status`, `JoinDecision` |
| `app/usage.py` | `running_seconds` extracted from `hours_used` (same result) |
| `app/placement.py` | `_shadow_hours` and the call in `reserve_slot` (the old check is unchanged in outcome) |
| `app/config.py`, `.env.example`, `deploy/README.md` | `HOURS_MODE` |
| `scripts/hours_shadow_report.py` | new, read-only |
| `tests/test_hours_grants.py`, `tests/test_entitlements_status.py`, `tests/test_hours_shadow.py`, `tests/test_hours_shadow_report.py` | new |

## Verification

* **Run locally** (lint, format and the named tests only; CI runs the rest): `ruff check .` and `ruff format --check .` clean (ruff 0.17.3, the version pinned in `pyproject.toml`; CI has no separate type-check step). 50 new tests pass: backfill (pilot account untouched, partly used, fully used, over the cap, own override, app default changed, no app default, deleted-and-re-registered with a row keyed by both id and fingerprint counted once, suspended, deleted, purged, admin), status and `check_join` with the injected clock (`tests.helpers.FakeClock`: starts, expires exactly at the expiry, a running meeting, the poll cap, two accounts never share hours, by-fingerprint grants, the spending order, read-only transaction), the shadow (off and `enforce` never call the new rule, `shadow` logs each of the two disagreements and stays silent when the rules agree or both refuse, a failure never breaks a join, the log carries no email, a database error leaves the transaction usable), the report, and the migration round trip (up, down, up with the same grants; the downgrade refuses with a non-pilot grant and changes nothing). The existing `tests/test_placement.py`, `test_usage.py`, `test_ai_runs_usage.py`, `test_join_refusal.py`, `test_models.py`, `test_dockerfile_scripts.py` (164 tests) and five existing migration tests pass untouched. The full suite was not run here; CI is the authority.
* **The report script was run for real** against a scratch database migrated to head and seeded with two accounts made after the migration: it printed both as `old rule allows, new rule refuses: no grant in force` (see Follow-ups). The scratch database and server were removed afterwards.
* **Not verified:** nothing was run against the production database or any real user's data; `HOURS_MODE=shadow` has not been on in a running server.

## Operate / roll back

1. Deploy (the migration runs with the deploy). Behaviour is unchanged: `HOURS_MODE` unset means `off`.
2. To look: set `HOURS_MODE=shadow` in `.env`, recreate web, worker and scheduler (`docker compose up -d --force-recreate web worker scheduler`; a restart does not re-read `.env`), then `docker compose exec -T web python scripts/hours_shadow_report.py </dev/null` and `docker compose logs web | grep hours_shadow`.
3. To stop: remove the line (or set `off`) and recreate. Nothing else holds state.
4. Roll back the code: revert the PR. The tables are additive and nothing from before reads them, so the old code runs with them present. To also drop them, `alembic downgrade 0048` (it refuses once any non-pilot grant exists; the pilot grants can be rebuilt from the ledger).

## Follow-ups

| Item | Why it matters |
|---|---|
| **Accounts created after 0049 have no grant** (the trial is stage S10), so the shadow reports each of them as `old rule allows, new rule refuses: no grant in force`, in the log and the report, while sign-up is open. This is the new rule being honest about a gap, not a bug in the comparison. Before S2 enforces anything, either S10's trial (or a pilot-style grant at sign-up) must exist, or that gap is closed another way | decision for the lead |
| A `NULL` `app_state.default_hours_cap_seconds` means "no cap" to `usage.effective_cap`, but the backfill follows the spec's wording (`... else 18000`) and gives such accounts a finite grant. Production has 18000, so nothing changes there | a spec/code wording difference, harmless today |
| Usage recorded by a transaction still in flight at the moment 0049 runs could be counted in neither the backfill nor the "since the grant began" term (its `counted_at` is earlier but it commits later). The window is a moment at deploy; the report would show it as one person's hours differing | S2 can reconcile when it starts writing allocations |
| `hour_grants.created_at` is not in spec section 7; added so grants carry the usual creation time | none |
| `enforce` does nothing until S2 | S2 |
| `docs/fe-integration-guide-billing.md` | starts in S2, from live captures (S1 changes no payload) |
