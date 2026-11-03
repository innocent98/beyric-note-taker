# SOP: end the bot on demand, send `automatic_leave`, and tighten the live bot state

## What shipped
One PR, branch `feature/bot-end-controls` (number in the PR list), three things:

1. **"End now and write the notes"** (`POST /api/meetings/{id}/end`, a page button, a Next button with a confirmation): the owner stops the bot while it is in the call and the pipeline writes the notes from what was recorded. It is not Cancel.
2. **`automatic_leave`**: `app/vexa.py` sends `automatic_leave.max_time_left_alone` with every bot, from the new setting `BOT_LEFT_ALONE_SECONDS` (default 300).
3. **The follow-ups from the read-only review of #40** (live bot state): a time on the state and a staleness rule, a 2-miss hysteresis, honest words for starting, leaving and a call that ended, list polling, a worker-stale notice on the meeting, and three small page fixes.

**Nothing here has run against a real call.** See "NOT verified live".

## Why
On 2026-11-03 the bot stayed in a Meet about 10 minutes after the other person left (Vexa's silence rule `left_alone`, default window 600000 ms), so the notes were late. Adebayo chose an explicit button over only shortening the window; the shorter window is added too, but as a setting. The review of #40 found that the live state was a bare string that could go on saying "Recording" for ever if the worker stopped, that the meeting list never refreshed, and that a few Vexa statuses read "Bot sent" or "in the meeting" when that was not true.

## How

### A. End now and write the notes
**What Vexa does** (read in `spike/vexa` at `31122f9`, `lifecycle/stop_router.py` and `lifecycle/stop.py`; our server runs v0.12.29):

| Bot state when `DELETE /bots/{platform}/{id}` arrives | Result |
|---|---|
| `active` | Marked `stop_requested`, answers 200 `{"status": "stopping", ...}` at once, publishes a `leave` command on Redis. The bot leaves, flushes, uploads its recording chunks, and ends **`completed` with `completion_reason` `stopped`**. The source says an `active` bot "finalizes its recording cleanly"; the master audio file is assembled lazily when read (`GET /recordings/{id}/media/{id}/raw`) |
| `awaiting_admission`, `joining`, `requested` | Same request; the workload is also torn down directly because a booting bot may not be listening. It ends **`failed` with reason `stopped`** (the FSM only allows `completed` from `active`) and recorded nothing |
| Already gone | 404, which `VexaClient._delete` already counts as stopped |

**Our design**

| Piece | Decision |
|---|---|
| Who can end | `services.can_end`: `bot_sent`, step `waiting`, and the worker's last state is `awaiting_admission`, `active` or `recording` (`pipeline.BOT_ENDABLE_STATES`). Not `requested`, `joining`, `needs_help` (the worker has its own 2 minute rule; Cancel covers it) or `stopping`. The state's age is **not** checked: a worker a little behind must not take the button away |
| What it does | `services.end_meeting`: refuses early without a lock (a meeting being processed must not make the request wait on the worker's row lock for minutes), then takes the **same blocking row lock as `cancel_meeting`**, checks again, calls `stop_bot`, stores the marker `completion_reason = "end_requested"` (`pipeline.BOT_END_REQUESTED`) and `bot_state_seen_at`, commits. Status stays `bot_sent`, step `waiting` |
| Errors | 409 `not_endable` ("The bot can only be ended while it is in the call or waiting to be let in."), 409 `already_ending` (marker present: no double end, `stop_bot` runs once), 502 `bot_stop_failed` (Vexa error; nothing changes, can be pressed again), 404 for another owner's meeting (`meeting_or_404`), 403 without the CSRF header (the route-enumerating tests cover it) |
| Why a marker | The API and the worker are different processes, and between the press and the worker's next look the page must not say "Recording". The marker is not a live state, so `_record_live_state` never replaces it (same protection as the refusal marker). When Vexa reports the bot gone, `_remember` replaces it with Vexa's own reason (`stopped`) |
| What the pipeline does afterwards | Nothing new for the good case: a `completed` meeting with a recording is downloaded and processed whatever its reason (`stopped` is not in `REMOVAL_REASONS`, so no "left early" notice). While the recording is "in progress" the worker already waits up to `RECORDING_GRACE` (10 minutes), then downloads anyway |
| Nothing recorded | After the usual `NO_AUDIO_GRACE_POLLS` (3) polls with no recording, the meeting is `failed`, step `waiting`, `retry_block` `no_audio`: **the same result an empty meeting gets today**, not a hung meeting. Only the words differ when Vexa's reason is `stopped`: `REASON_ENDED_NOTHING_RECORDED` ("You ended this meeting before anything was recorded, so there are no notes to write."), also for a bot ended at the door (`failed` + `stopped`, which has the same words at once). Other failures keep their old reasons |
| Cancel | Unchanged and still offered while the bot leaves (it discards and makes no OpenAI call). Cancel after End is allowed |
| Words | While the marker is set: "The bot is leaving the meeting. The notes start once it has gone". After Vexa reports completed and the recording is not ready: "The call ended; finishing the recording" |
| Pages | Jinja: a form with the button and a line saying the bot leaves now and the notes are written from what it heard (a plain POST, like Cancel). Next: a primary button beside Cancel and a dialog ("The bot will leave the meeting now and the notes will be written from what it heard so far...", choices "Keep recording" and "End now and write the notes"); a 409 refetches, as for Cancel |

Rejected: reusing Cancel (it discards by design); setting `status` to `processing` at once (the recording does not exist until the bot has gone and Vexa's `stopping` can take a while, so the download step would just fail and retry); a new column for "end requested" (the marker column already exists for exactly this); allowing End while `joining` (nothing recorded; Cancel says it better).

### B. `automatic_leave` (`BOT_LEFT_ALONE_SECONDS`)
`VexaClient.send_bot` adds `"automatic_leave": {"max_time_left_alone": seconds * 1000}`. Confirmed in Vexa's source (`meeting_api/bot_spawn/router.py`, `_resolve_automatic_leave`): the public field is snake_case, a **positive integer in milliseconds**, anything else is 422; it becomes `everyoneLeftTimeout` for the bot, and without it the bot's default silence window (10 minutes, `BOT_ALONE_SILENCE_WINDOW_MS`) applies. The resolved dict always keeps `waitingRoomTimeout` at Vexa's own default, so the lobby wait is **not** changed by sending this. v0.12.29 is the version in `spike/vexa`, so the field is in the version we run (the server's own check, a real bot request, was not done).

| Setting | Value |
|---|---|
| `BOT_LEFT_ALONE_SECONDS` | default `300`; `0` or less sends nothing (Vexa's 10 minutes); the value is multiplied by 1000 |
| Where to change it | `.env` on the server, then `docker compose up -d --force-recreate web worker` with no meeting `bot_sent` or `processing`. Both services build a `VexaClient` (the web service for a new meeting, the worker for a resend). No code deploy. Applies to bots sent afterwards |
| **The caution** | **The rule is silence, not headcount.** Vexa leaves after that long with no one else speaking, so a short window can cut a quiet meeting: a long screen-share with no talking, a reading break, a very quiet room. The notes then cover only what the bot heard, and the owner is not warned. 300 s is a first guess to be measured; written in `.env.example` and `deploy/README.md` too |

### C. Follow-ups from the review of #40
| Item | What changed |
|---|---|
| Staleness | New column `meetings.bot_state_seen_at` (migration `0008_bot_state_seen_at`, nullable, downgrade drops it; no existing column fitted: `started_polling_at` and `terminal_seen_at` mean other things). The worker stamps it on every poll that sees a live state. `services.status_text(meeting, now)` shows a live state only if it was seen within `BOT_STATE_STALE_POLLS` (2) times `POLL_INTERVAL_SECONDS` (60 s with the default); older, or with no time, it reads "Checking on the bot...". A meeting with no state at all still reads "Bot sent" |
| Worker looks stale | `MeetingDetail.worker_stale` (true only while the meeting is `bot_sent` or `processing` and the heartbeat is over 20 minutes old, the same rule as the home page). Banner on the Jinja and Next meeting pages |
| FE guide claim | The guide said the worker looks every 30 seconds. Corrected: the worker sweeps meetings one after another and rests 30 s after a sweep, so a meeting being transcribed holds up the others for as long as it takes |
| List polling | `MeetingList.refresh_after_seconds` (30 while any listed meeting is `bot_sent` or `processing`, else null). The Jinja list has a 30 s meta refresh under the same condition; the Next list calls `router.refresh()` at that interval, skips hidden tabs and stops when it becomes null |
| Words | New live states `requested` ("Starting the bot") and `stopping` ("The bot is leaving the meeting"); `bot_sent` with `terminal_seen_at` set (Vexa said completed, recording not ready) reads "The call ended; finishing the recording". "in the meeting" is never said after the call ended. The refusal pause still reads "Bot sent" |
| Hysteresis | One failed lookup keeps the last state and does not renew its time; two in a row (`LOOKUP_MISSES_BEFORE_UNKNOWN`) clear it (counter in memory, like the other worker counters, so a restart only forgets a miss) |
| `needs_help` with segments or a recording | Stored as `active` ("Bot is in the meeting"), not as the prompt text (`_join_problem` already leaves such a bot alone). Rejected: calling it "Recording", because the bot asked for help and nothing proves it is still capturing |
| Pages | The Next state badge is a polite live region (`role="status"`, `aria-live="polite"`); a state sentence over 60 characters is a `<p>` in the wait card, not an `<h2>` |

## What's involved
- Python: `app/config.py` (`bot_left_alone_seconds`), `app/vexa.py` (`send_bot`), `app/models.py` and `alembic/versions/0008_bot_state_seen_at.py`, `app/pipeline.py` (`BOT_LIVE_STATES`, `BOT_END_REQUESTED`, `BOT_ENDABLE_STATES`, `_live_state`, `_record_live_state`, `_lookup_missed`, `_nothing_recorded_reason`, `REASON_ENDED_NOTHING_RECORDED`), `app/services.py` (`can_end`, `end_meeting`, `bot_state_is_stale`, `status_text`), `app/api.py` and `app/api_schemas.py` (`can_end`, `worker_stale`, `refresh_after_seconds` on the list, `/end`), `app/web.py`, `app/templates/meeting.html`, `app/templates/index.html`.
- Web: `web/lib/api/client.ts` (`meetings.end`), `web/components/app/meeting-view.tsx`, `web/components/app/meeting-list.tsx`, `web/components/ui/badge.tsx`; `docs/openapi.json` and `web/lib/api/schema.d.ts` regenerated with `scripts/export_openapi.py` and `npm run gen:api`.
- Tests: `tests/test_end_meeting.py` (new), `tests/test_bot_live_state.py`, `tests/test_vexa.py`, `tests/test_api.py`, `tests/test_web.py`, `tests/test_migrations.py`, `web/tests/meeting-view.test.tsx`, `web/tests/screens.test.tsx`.
- Docs: `docs/fe-integration-guide-owner-app.md` (5.1, new 5.4a, 7, 8, 10), captures `111` to `122` from `scripts/capture_owner_api.py` (`run_end_controls`), `.env.example`, `deploy/README.md`, the checklist, and `docs/sop/2026-11-03-bot-live-state.md` (updated where this changes it).

## Verification
- Tests written first and watched failing, then green. The full Python suite, `ruff check`, `ruff format --check`, and in `web/`: `npm run lint`, `npm run typecheck`, `npx vitest run` (counts in the PR).
- The migration round trip (`0007` down and up) is a test (`test_bot_state_seen_at_is_added_and_dropped`).
- The captures `111` to `122` come from the real app over HTTP with the real `pipeline.advance_meeting` fed one fake Vexa answer per state: starting, recording with `can_end`, End (200, the repeat 409, the 409 for a joining bot, the 502), leaving, the call ended with the recording being finished, ended before anything was recorded (the worker's real three polls ending `failed` / `no_audio`), a state seen five minutes ago ("Checking on the bot..."), and a heartbeat 30 minutes old (`worker_stale` true). Every body in the FE guide is copied from them.
- The good path after End (recording downloaded, transcript, speaker labels, notes, `ready`) is a test with the real pipeline and fake Vexa and OpenAI, not a capture (the worker does not run in the capture script).

## NOT verified live
Nothing in this change has run against a real call, Vexa or OpenAI.
- **What a stopped bot does on a real call**: that `DELETE` on an `active` bot ends `completed` / `stopped` with a downloadable recording, and how long that takes. Read from Vexa's source only. If the recording is slow, the worker's 10 minute `RECORDING_GRACE` covers it; if it is never ready the meeting fails like any other with no recording.
- **That v0.12.29 on the server accepts `automatic_leave`** and the bot then leaves after the configured silence. Read from `router.py`; no real bot request was made. A 422 would stop every send, so **watch the first bot after deploy** (the app shows "The bot could not be sent" and the worker logs `send_bot failed`). Setting `BOT_LEFT_ALONE_SECONDS=0` removes the field if it ever has to be backed out without a deploy.
- Whether 300 s is a good window: unmeasured.
- The state names Vexa reports (`requested`, `stopping`) as read from its schema; the sequence in a real call is as for #40, not re-observed.
- The Next dialog and live-region behaviour in a screen reader: tested with jsdom only.

## Operate / roll back
- **Deploy order**: the migration adds one nullable column, so the new code can start before or after it only if the column exists: the `migrate` service runs it first, as usual. Deploy with no meeting `bot_sent` or `processing` (the repo's rule); a restart mid-call only loses the in-memory hysteresis counters.
- **Change the window**: edit `BOT_LEFT_ALONE_SECONDS` in `.env`, recreate `web` and `worker` (see above).
- **Roll back**: revert the PR. `alembic downgrade 0007` drops `bot_state_seen_at` (nothing else reads it). Rows that hold `end_requested` read as "Bot sent" on the old code, are never replaced by the old worker (it only overwrites live states and `None`), and are replaced by Vexa's own reason once the bot is gone. To stop sending `automatic_leave` without a rollback: `BOT_LEFT_ALONE_SECONDS=0`.
- **Migration number**: `0008` here assumes it lands before #37's migration; if #37 merges first, this one is renumbered `0009` with `down_revision = "0008"` (the single-head test fails loudly if two heads exist).

## Follow-ups
- Watch one real call: press End, then time it to `ready` and update this SOP with the real sequence and delay; confirm Vexa takes `automatic_leave` and measure a few windows.
- A calmer `status_tone` for `active`, `recording` and the leaving states (still `warning`).
- Tell the owner when the silence rule is about to end the bot (nothing in the API says so).
- The Jinja badge is not a live region (the page reloads whole every 30 s); the Next pages replace it.
