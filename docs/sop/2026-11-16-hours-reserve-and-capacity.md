# SOP: billing stage S2b, the reserve for a second meeting, the Skribby capacity default, the original pilot total

**What shipped.** Four decisions of the owner (relayed by the lead, 2026-11-16) on top of S2 (`docs/sop/2026-11-16-hours-enforce.md`, merged as #214 and deployed): (1) under `HOURS_MODE=enforce` a second concurrent meeting of one person starts only if their hours cover a reserve for each running meeting; (2) the capacity ceiling `BOT_MAX_LOCAL` defaults to 20 under Skribby (still 1 under Vexa); (3) the screens and mails show the original pilot total ("4 of 5 h"); (4) the review's list of small fixes. PR: branch `feature/hours-reserve-and-capacity` (number in the PR list). No migration. `HOURS_MODE` is still `off` by default; items 1 and 3 only act under `enforce`; item 2 changes the default under `BOT_PROVIDER=skribby` as soon as it is deployed, **but on this server the `BOT_MAX_LOCAL=3` line in `.env` wins until it is deleted or edited** (see "Switching on and the server's `.env`").

## Why

- **Reserve.** A meeting just placed counts as about 0 s until it has run a while (`usage.running_seconds` measures from `started_polling_at`). With 60 s left a person could paste two links 20 s apart and both started; each is then counted in full and the excess written off, so up to about 12 h per person and period (two bots at 6 h each) went unbilled. The spec had said the placement lock prevented it; it only orders joins.
- **Capacity.** `BOT_MAX_LOCAL` protected our own box under Vexa (one bot at a time). Under Skribby the bots run on Skribby's servers; its billing page shows our plan allows 25 concurrent bots (a screenshot Adebayo sent; "Request an increase" exists there). The real per-person control is `USER_BOT_LIMIT` (default 2).
- **Pilot total.** A pilot grant holds only what was left of the old allowance, so a person who had used 4 of 5 hours would have seen "0 of 1 h" the day the flag flips.

## How, and what was decided

| Piece | What it does |
|---|---|
| The reserve (`entitlements.check_join`) | with `reserve_seconds` (`HOURS_RESERVE_MINUTES`, default 30, `0` = off) a SECOND concurrent meeting needs `hours left - what is still set aside for the running ones >= what the new one reserves`. Every meeting of the same person that is running or just placed (status `bot_sent`: the bot is in the call or was just sent; not counted yet. A `processing` meeting has left and reserves nothing, so the first meeting after one has ended is never held to the reserve) reserves 30 minutes, or its calendar event's length when a calendar join made it (`scheduled_joins.starts_at/ends_at`), never more than the poll cap (6 h). What has run already is counted by the estimate, so only the rest of the reserve is added (a meeting 20 minutes in sets aside 10 more). The new meeting reserves 30 minutes, or its event's length: `PlacementRequest.expected_seconds`, set by the join runner and by Send now. **A first meeting is never held to it.** Boundary: with one meeting just placed, 1 h left starts a second, 59 min 59 s does not |
| The refusal | 403 `hours_cap_reached` with `reason` `reserve`, in plain words: not enough hours left to record another meeting at the same time, hours are set aside for each meeting that is running, the ones a meeting does not use come back when it ends, try again once one has finished. No word about a pilot or an address |
| Calendar auto-join | skip reason `hours_reserved` (retried until the deadline, moves with the event, listed as "Not recorded" with Send now, reason text "Your hours were set aside for another meeting that was running."). It raises a per-meeting bell notice that says the note taker "has not joined ... yet" and "will keep trying until HH:MM UTC" (it usually does join when the other meeting ends), **not** the out-of-hours notice: the person is not out of hours. When the other meeting ends the join goes ahead (tested) |
| `usage.can_record` | now also false while a meeting is running and the hours cannot cover two reserves, so the screen does not offer a start that placement would refuse |
| Off and shadow | the old cap rule alone decides; no reserve. **Shadow cannot preview the reserve:** the shadow comparison asks `check_join` without it, so `hours_shadow_report.py` never lists reserve refusals; the first time they can happen is `enforce` |
| Capacity default (`app/config.py`) | `BOT_MAX_LOCAL` unset: 1 under Vexa, `SKRIBBY_BOT_CEILING` = 20 under Skribby (below our plan's 25). A value in `.env` always wins (the old name `MAX_ACTIVE_BOTS` too). `USER_BOT_LIMIT` stays 2. The stale comments ("the box fits about one") are fixed in `config.py`, `placement.py`, `.env.example` and `deploy/README.md` |
| Original pilot total | `Status.display_offset_seconds`: while a pilot grant is in force, the usage counted at or before that grant was made (migration 0049 made it as cap minus ledger) is added to both numbers: `used_seconds` and `cap_seconds` in the payload, and the 80% mail ("You have used 4.8 of your 5 recorded hours"). **No migration and no new column:** the ledger already holds the baseline, exactly (the same figure `hours._cap_baseline` uses for cap gifts). The warning, `seconds_left` and every decision are still of the hours actually held. Gone once no pilot grant is in force. Edge: an account that had used more than its cap (a running meeting that overshot) holds 0 and shows "6 of 6", not the old 5 |
| Reconcile spends across all grants | `hours.reconcile_backlog(session, user, now)` settles the S1 backlog across the person's grants in force in the decided order, exactly as the backlog term takes it off, so hours left never change (the S2 version used the pilot grants only and changed hours for people who had used a cap raise). The script's refusal stays as a safety net |
| NULL app default cap | the cap-gift baseline for an account with no pilot grant is a fixed 18000 s, as migration 0049's "else 18000", not the app's current default (a NULL one crashed the admin cap edit and the script) |
| Lowering the app default | no longer stacks the gift (same fixed baseline) |
| Deleted accounts | `sync_cap_gift` gives nothing to a deleted or purged account and the script skips them (grants follow the email fingerprint, so a gift would have come back on a re-sign-up) |
| A swallowed allocation failure | still logged (class and ids), and now also one admin alert **per person** and hour (`admin.hours_allocation_failed`, names ids only; the words say the reconcile script settles a person with a pilot grant and anyone else needs a manual look) |
| The reconcile script's clock | `now` is read from the database inside the transaction (`select now()`): a cap gift made by the run is stamped with the transaction's `now()`, and a Python clock read before the session opened left that gift out of "grants in force" for the whole run (a person would end with 3 h left instead of 2 h and the script printed "0 before, 0 after"). A real-clock test covers it, with S1 usage after the raise |
| Small ones | one meeting reserves once even if several `scheduled_joins` rows name it (`meeting_id` is not unique); an event length of zero or less is not a length; the share page does not lock on `reserve` (the FE guide says so) |
| Spec 3.2 | the spend-order line was checked against the code (a test reads the spec text and `SPEND_RANK`) and the pilot display rule is noted |

Rejected: counting a just-placed meeting at a minimum only (the same as the reserve but blind to a long calendar event); a hold row per placed meeting (a migration, and a leaked hold locks hours); a new column for the pilot baseline (the ledger has it).

## What's involved

| File | Change |
|---|---|
| `app/entitlements.py` | `RESERVE`, `_reserved_beyond_elapsed`, `check_join(reserve_seconds, expected_seconds)`, `pre_pilot_seconds`, `Status.display_offset_seconds` |
| `app/placement.py`, `app/send_now.py`, `app/join_runner.py` | `PlacementRequest.expected_seconds`; the enforce branch passes the reserve; `hours_reserved` skip reason and notice |
| `app/services.py`, `app/api.py` | the refusal words for `reserve`; `_usage_out` (offset, `can_record`) |
| `app/hours.py` | reconcile across grants, fixed baseline, deleted accounts, the admin alert, the 80% mail's numbers |
| `app/config.py`, `.env.example`, `deploy/README.md` | `BOT_MAX_LOCAL` default by provider, `HOURS_RESERVE_MINUTES` |
| `app/gcal_sync.py`, `app/calendar_text.py`, `app/mail_summary.py` | `hours_reserved` next to the other hours skip reasons |
| `scripts/hours_reconcile_backlog.py` | deleted accounts, the new `now`, the docstring |
| `web/lib/share-cap.ts`, `web/app/share/page.tsx`, `web/components/share/share-flow.tsx`, `web/lib/api/client.ts`, `web/tests/share-page.test.tsx` | the share page: a `reserve` refusal does not lock it and hours set aside are not shown as "used all" (`ApiError.reason`); web lint, type-check and the share and client tests pass |
| `tests/test_hours_reserve.py` (new, 43 tests); `tests/test_hours_enforce.py` (reconcile tests updated) | |

## Verification

* **Run locally** (lint, format and named files only; CI runs the rest): `ruff check .` and `ruff format --check .` clean (0.17.3). New and changed tests pass: the reserve (a first meeting unchanged, 60 s left refused, the 1 h boundary, elapsed time counted, a calendar event's length for the running and the new meeting, the poll cap, `0` turns it off, other people and the admin unaffected, off and shadow have no reserve, the refusal words, the setting, `can_record`), the join runner (reason `hours_reserved`, no out-of-hours notice, tried again when the other meeting ends, the event's length reaches placement), capacity (1 under Vexa, 20 under Skribby, `.env` wins, the old name, a value of 1 stays), the pilot total (4 of 5 over HTTP, the 80% mail, over-cap, gone once the grant ends, a payer with pilot hours left), and the review list. Mutations: `<` to `<=` in the reserve and a zeroed offset each fail the tests that should catch them. Also run and passing: `test_placement`, `test_join_refusal`, `test_send_now`, `test_hours_*`, `test_entitlements_status`, `test_usage`, `test_accounts`, `test_admin`, `test_join_runner`, `test_gcal_sync`, `test_mail_summary`, `test_calendar_api`, the provider and Skribby tests (989) and the OpenAPI drift test.
* **Not verified:** nothing ran against production or a real meeting; the reserve was not exercised with two real Skribby bots; `HOURS_MODE=enforce` has not been on in a running server.

## Switching on and the server's `.env`

1. **`BOT_MAX_LOCAL` on the server.** Its `.env` has `BOT_MAX_LOCAL=3`, which wins over the new default of 20. To use the default (recommended), delete the line, then recreate so it is re-read:

   ```bash
   sed -i '/^BOT_MAX_LOCAL=/d' .env && docker compose up -d --force-recreate web worker scheduler
   ```

   To keep a number instead, set it: `sed -i 's/^BOT_MAX_LOCAL=.*/BOT_MAX_LOCAL=15/' .env` and recreate the same way. `MAX_ACTIVE_BOTS=` (the old name) is read too; check `grep -n "MAX_ACTIVE_BOTS\|BOT_MAX_LOCAL" .env` shows nothing unexpected. Our Skribby plan allows 25 at once; 20 leaves room. If you ever need more, use "Request an increase" on Skribby's billing page first.
2. The reserve needs nothing set: `HOURS_RESERVE_MINUTES` defaults to 30 and only acts under `enforce`. `HOURS_RESERVE_MINUTES=0` turns it off.
3. The steps in `docs/sop/2026-11-16-hours-enforce.md` ("Before switching on") still apply; run `hours_reconcile_backlog.py` again right before `enforce`.

## Operate / roll back

Revert the PR. The capacity default only matters under Skribby: to go back to the old behaviour set `BOT_MAX_LOCAL=1` in `.env`. The reserve is off with `HOURS_RESERVE_MINUTES=0` or any mode but `enforce`.

## Follow-ups

| Item | Why it matters |
|---|---|
| **A calendar event's length is user-controlled and may be shorter than the real meeting** (a 1-minute event with a 6-hour link reserves 1 minute): the decision was the event's length *instead of* 30 minutes. An automated review suggested a floor (never below `HOURS_RESERVE_MINUTES`); not built, the owner decided "instead". Open for the lead: add a floor? Cost of the floor: a short calendar meeting also needs 30 minutes held | a person who does this on purpose gets the old leak (up to about 12 h of our cost) |
| The 80% warning for a pilot person is of the grant held, so the bar can read 80% while `warning` is null and `low` comes at 96% of the original total; the FE must drive the warning from `warning`, never from the bar ratio (FE guide) | the 80% mail goes out at the same moment as `low` |
| After S10 gives pilot grants an expiry, the display offset ends with the pilot grant and a person's bar changes again | tell them in the S10 mail |
| `UsageOut.state`/`warning` are still plain strings | a `Literal` later |
