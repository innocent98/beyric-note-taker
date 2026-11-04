# SOP: `place_bot()`, the one place a bot is sent (Phase C, task C11)

## What shipped
`app/placement.py`: `PlacementRequest`, `PlacementRefused` and `place_bot(session, vexa, req, *, now)`, plus `reserve_slot` (the lock and the four checks, used by the retry path). The pasted-link path (`services.create_meeting`, which the Jinja form, the JSON API and the PWA share target all reach) and the "send a new bot to a refused meeting" retry now go through it. `NEW_MEETING_LOCK` and the `bot_already_active` answer are gone. A full note taker answers the neutral **409 `bot_busy`**. Branch `feature/c11-place-bot`, one PR (number in the PR list). No calendar join calls it yet: **C12 (the join runner) is the first caller after the pasted link.** **Dispatcher locking: wants the Opus review before merge.**

## Why
Spec 9.4: one function must be the only place a bot is sent, so the rules about how many bots may run, and for whom, exist once. Before it, the rules lived inline in `_create_meeting` and again in the retry path, behind a single global lock, and a user's own running meeting was named as theirs while another user's was "busy". C12 needs the same checks for a calendar join, and a calendar join cannot ask a person to press the button again, so the refusal has to be a code the runner can act on.

## How
| Piece | Decision |
|---|---|
| Pre-flight order | Hours gate (`usage.effective_cap` and `usage.hours_used`, the same functions as before), per-user limit, duplicate, capacity. A test walks all four with everything wrong at once and then removes the faults one by one. The user's calendar connection is **the caller's** check: a pasted link has no connection |
| Codes | `hours_cap_reached` (403, `PlacementRefused.cap_seconds` carries the cap for the words), `user_limit` (409), `already_in_meeting` (409), `bot_busy` (409). `place_bot` raises `PlacementRefused` for these and lets a failed send raise `VexaError`; `services` turns them into `Refused` (`placement_refusal`, and `bot_send_failed` 502). A caller bug (unknown user, a template that is not the user's, an unknown `source`) is a `ValueError` before anything is sent, never a refusal |
| Lock | `pg_advisory_xact_lock(PLACEMENT_LOCK = 6004, 1)`, taken **first**, before any check, and held across `send_bot` until the meeting row is committed. The second int is the host id, always 1 until D4. Because the lock is taken before the checks, a request reads the capacity only after every earlier placement has committed (READ COMMITTED gives each statement a fresh snapshot), so two requests can never both see the one free slot |
| Lock wait | `set local lock_timeout` to `LOCK_WAIT_SECONDS` (35 s: `VexaClient` gives a send 30 s, plus a margin) around the lock. A waiter stuck longer answers `bot_busy` instead of hanging. A test asserts the margin is above the client's own default timeout and at most 15 s over it |
| Send failure | `VexaError` is raised **after a rollback**: no meeting row, lock released, the slot is free for the next request (a test places a bot straight after a failed send) |
| Insert failure after a successful send | The flush or the commit fails: roll back, `stop_bot(platform, native_id)` best effort, re-raise the **original** error (a failing stop is logged by exception type and never hides it). Tested at both the flush and the commit |
| Per user, not global, for "already in a meeting" | **Decided by the lead after the #45 review, kept here:** one live bot per meeting code is per user. A second account in the same meeting gets its own bot until C12 decides how sharing works, and nobody can block another user's join by registering a link first. This **supersedes the plan's test name** `test_same_code_by_other_user_is_already_in_meeting`, which is split into `test_same_code_by_same_user_is_already_in_meeting` (refused) and `test_same_code_by_other_user_gets_its_own_bot_until_sharing_is_decided` (not refused). Every other plan test name is used as written |
| What counts as a live bot | For the per-user limit and the host's capacity: `bot_sent` **and** `processing`, exactly as `ACTIVE` did before (a meeting being transcribed still holds the slot). For the duplicate check only `bot_sent`: once the bot has left, the same recurring code is free again. **Spec 9.4 step 3 would count `bot_sent` only for capacity too** (a `processing` meeting no longer holds a bot). That was not changed here because it would alter what the existing pasted-link tests and the production box do; it is the first thing to revisit when back-to-back calendar meetings arrive (see follow-ups) |
| Retry of a refused meeting | Now uses `reserve_slot`, so it takes the same lock and runs the same four checks (it used to share only the old lock). Order changed to **row lock first, then placement lock** (`_lock_meeting`, which has its own 10 s bound and answers `meeting_busy`), so nothing ever waits for a row while holding the placement lock |
| No queue | A refusal is an answer, never a queued request: a queued meeting is an unrecorded meeting (decision 2026-11-03). C12's bounded retry until the deadline is the runner's job, not this function's |
| Settings | `BOT_MAX_LOCAL` (default 1) replaces `MAX_ACTIVE_BOTS`, which is **still read** as an alias so an existing server `.env` keeps working with no edit; the new name wins when both are set. `USER_BOT_LIMIT` (default 2). Both are `>= 0` (0 = no new bots, a drain); a negative value stops the app at start-up. Safe defaults: **no server `.env` change is needed** |
| Neutral busy | The `bot_busy` text is the one it already had ("The note taker is busy right now. Try again in a few minutes."): no name, address, id, title or hint that anyone is recording. A test makes another user's meeting with a title and a code and checks none of it appears in the response |
| `bot_already_active` | Retired. A user's own running meeting now fills the slot like anyone else's (`bot_busy`) while `BOT_MAX_LOCAL` is 1. With room for more, the own-bot rules are `user_limit` and `already_in_meeting`, which are about the user's own bots and so may say so |

## What's involved
| Path | Change |
|---|---|
| `app/placement.py` (new) | everything above |
| `app/services.py` | `create_meeting` and `_send_refused_meeting_again` call placement; `placement_refusal` maps codes to `Refused`; `NEW_MEETING_LOCK`, `_require_bot_room` removed |
| `app/config.py`, `.env.example`, `deploy/README.md` | `bot_max_local` (alias `MAX_ACTIVE_BOTS`), `user_bot_limit` |
| `app/api.py`, `docs/openapi.json`, `web/lib/api/schema.d.ts` | the endpoint description (the response codes were already 400, 403, 409, 502); no shape change |
| `app/scheduler.py`, `app/signup.py` | comments that named `NEW_MEETING_LOCK` |
| `tests/test_placement.py` (new, 24 tests), `tests/fakes/vexa_host.py` (new: `FakeVexaHost` with a send counter, peak concurrent sends, slow and failing sends) | tests |
| `tests/test_api.py`, `tests/test_web.py`, `tests/test_join_refusal.py` | adapted to the new codes and words, none deleted; four tests added (same meeting, user limit, neutral busy, retry into a meeting you are already in) |

No migration, no nginx change, no new required environment variable.

## Verification
| Claim | How | Result |
|---|---|---|
| Two requests, one slot: exactly one meeting, the other `bot_busy` | threads, each with its own session and connection, on a real Postgres; the fake host takes 0.4 s per send so both are inside the check together; the host's counter says one bot was sent and its peak of simultaneous sends is 1 | pass |
| The lock is held across `send_bot` | during the send a second connection tries the lock and must not get it; it is free again after the commit | pass |
| **Mutation check** | with the `pg_advisory_xact_lock` call removed, `test_two_concurrent_requests_one_slot_exactly_one_meeting_other_bot_busy`, `test_the_lock_is_held_across_send_bot` and `test_lock_wait_is_bounded_by_the_send_timeout` each fail; restored, all 24 pass | done |
| Send failure and insert failure leave no meeting, no stuck lock, no orphan bot | `test_send_failure_creates_no_meeting_and_releases_the_lock`, `test_insert_failure_after_send_stops_the_bot` (flush and commit) | pass |
| Lock wait is bounded | a second connection holds the lock; the request answers `bot_busy` after the (shortened) wait and sends nothing | pass |
| Existing pasted-link tests | pass with the new codes and words; none deleted | pass |
| Whole suite, ruff, OpenAPI | see the PR | see the PR |
| **Not verified** | a real Vexa host (the fake is a counter, not a capture); the lock under real load (two real web workers racing for a real 30 s send); the PWA share path end to end on a phone (it reaches `POST /api/meetings`, which is covered, and nothing about it changed) | next live meeting |

## Operate / roll back
- **Server steps before merge: none.** `BOT_MAX_LOCAL` and `USER_BOT_LIMIT` are optional. If the server `.env` has `MAX_ACTIVE_BOTS`, it keeps working.
- During a deploy the old `web` and the new `web` are never running together (the compose service restarts), so the old single-key lock (6001) and the new two-key lock (6004, 1) do not need to agree. A bot that was sent and not recorded (the process killed between the send and the commit) is not detectable here; Vexa's `automatic_leave` ends it.
- To stop all new bots without a deploy: set `BOT_MAX_LOCAL=0` and restart `web` and `scheduler` (every request then answers `bot_busy`).
- Roll back: revert the PR. There is no data change; the lock number is not stored anywhere.

## Follow-ups
- **FE guide (queued for C17):** `docs/fe-integration-guide-owner-app.md` still says `bot_already_active` and `MAX_ACTIVE_BOTS` in "One bot at a time per user" (line about the limit across users). It must be rewritten with a **captured** `bot_busy` response (and `user_limit`, `already_in_meeting`) from the running app: no capture has been taken, so none is pasted here. The front end should show `message` as it comes, treat 409 `bot_busy` as "try again later" (no automatic retry loop on a pasted link), and never expect a name in it.
- **Spec 9.4 step 3 (capacity counts `bot_sent` only):** the first back-to-back pair of calendar meetings will wait for the earlier meeting's transcription. Decide with C12 whether to count `bot_sent` alone once the worker's load no longer needs the slot.
- **C12:** lock the join row `FOR UPDATE` **before** calling `place_bot` (never after), re-check the connection and rule state, and decide what a calendar `already_in_meeting` and `user_limit` mean for a join row. `place_bot` commits, so C12's "joined" row update is a second transaction: if that gap matters, add a hook that runs before the commit.
- **D4:** the host id (second lock int) and the per-host capacity count; `PlacementRequest` gains the host. `LOCK_WAIT_SECONDS` should follow the client's timeout instead of mirroring it.
- A bot that was sent when the process dies before the commit has no row; D3's health check or a reconcile against Vexa's running bots would find it.
