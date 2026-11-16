# SOP: the bot leaves a call when only other bots are left, and when no person came

**What shipped.** The worker now tells our bot to leave in two cases, both decided by one small pure
function (`app/bot_presence.py::decide`): (A) a person was in the call, none is now, and only bots (ours
included) have been there for `BOT_ONLY_BOTS_LEAVE_SECONDS` (default 300); (B) no person has been seen at
all and the bot has been in the call `BOT_NOBODY_CAME_SECONDS` (default 900). Kill switch
`BOT_LEAVE_WHEN_ONLY_BOTS` (default **true**, as Adebayo asked 2026-11-16). PR: branch
`feature/bot-leave-when-only-bots`. **No migration.** Does not touch `time_limit` / `MAX_POLL_HOURS` (that
decision is pending), the coverage rule, hours or billing code.

## Why

Meeting 38 (2026-11-16): the call ran 1 h 29 m on Skribby (0.83 USD, about 0.55 USD/h). Every person
left about 56 minutes in, but the Fireflies note taker stayed, so Skribby's `last_person_detection` (we
send 5 minutes) never fired: it counts any participant, and Skribby's own page says so
(`silence_detection` "helps when other bots remain in the call and last-person detection cannot
trigger"). Our bot sat alone with a bot for 33 minutes until Adebayo stopped it by hand. Side effect: the
last transcript segment ended at 63% of the recording, below `MIN_COVERAGE = 0.70`, so the pipeline fell
back to our own transcription (an extra ~0.70 USD/h) and `transcript_source` read `own`. Ending the bot
when the people have gone fixes both. Rule B exists because our bot joins about a minute early and is
often alone, or with another bot, before the first person comes: rule A must not fire then.

## How, and what was decided

| Decision | Choice | Why |
|---|---|---|
| Where the live list comes from | The `participants` of the same `GET /bot/{id}` the worker already makes every poll (30 s); `map_bot` reads it while the status is `recording` / `waiting_to_record` | No extra request or cost. Skribby's page says the default answer has participant data without the event logs, so no `with-speaker-events` (heavy) |
| **What is unproven** | Whether `participants` is filled live, during the call | See `docs/spike/bot-leave-only-bots-2026-11-16/README.md`. So the design fails safe: no list, an empty list, or unreadable times = the bot stays (the status quo) |
| State | **None kept.** The answer is worked out from the times inside the list (`presence_intervals`, last interval open = in the call) | Survives restarts, no column, a returning person resets the wait by itself |
| Rule A | At least one person seen; none in the call now (every person's last interval closed); `now - latest left_at >= G`. A person with an unreadable time counts as present | One person who is still in, or back, ends the countdown |
| Rule B | List non-empty, **no person in it**, `now - in_since >= N`, where `in_since` is the first `waiting_to_record` / `recording` event. An **empty** list never fires | A provider that does not fill the list live would show an empty list for every call; ending on it would end real meetings. A truly empty call is Skribby's own `empty_meeting_timeout`, which we now send as the same N minutes (was the silent default of 10), so the two rules agree and nothing doubles up |
| What the person sees (rule A) | The same as "End now": the bot is told to leave, `completion_reason = end_requested` ("The bot is leaving the meeting. The notes start once it has gone"), then the usual pipeline. The recording ends at the real end of the call, so coverage holds | Reuses a path that is already tested |
| What the person sees (rule B) | `completion_reason = nobody_came` while it leaves ("The bot is leaving: nobody else joined the meeting"); then the meeting is stored as `failed` (the only ended-with-nothing status; adding one needs a migration and web work) with `REASON_NOBODY_CAME` ("Nobody else joined this meeting, so the bot left and there are no notes to write. Nothing was counted against your hours."), and `status_text` reads **"Nobody joined"** with the **neutral** tone, not "Failed" / danger. No audio is downloaded, so no usage row is written: nothing to bill. Skribby's copy is deleted best effort | The label and tone carry it; the API's `status` is still `failed`. If Adebayo wants a proper status, that is a follow-up |
| Bot or person | `is_bot_name`, one-sided: **unsure = person** | Worst case of a wrong "person" is the status quo; of a wrong "bot" is ending a real meeting |

**The bot-name list** (`_BOT_WORDS`, `_BOT_PHRASES`, whole words only, case-folded; "bot" inside
"Abbott", "Robotham", "Botha" never matches): `bot`, `bots`, `notetaker`, `recorder`, `transcriber`,
`assistant`, brands `fireflies`, `tldv`, `avoma`, `krisp`, `sembly`, `supernormal`, `meetgeek`,
`circleback`, `tactiq`, `airgram`; phrases `note taker`, `Otter.ai`, `Read.ai`, `tl;dv`. Left out on
purpose because they are ordinary words or names: otter, grain, gong, fellow, jamie, `ai`, fathom,
copilot. `assistant` is the one generic word a real person can carry, so it does not count beside a job
word (`Executive Assistant`, `Assistant Professor`, `Assistant Manager`, `Personal Assistant to ...`).
Our own bot's names (the one the host sees, the one it was sent with, the Google account's name) match as
a whole. Tested: `MyWay's Assistant`, `Fireflies.ai Notetaker Adebayo Tosin` are bots; `Abbott Smith`,
`Robotham Jones`, `Assistant Professor Jane Doe`, `Jane Doe, Executive Assistant`, `Ai Nguyen` are
people. **Known residual risk:** a person whose display name is only "<Name>'s Assistant" or "... Bot" is
read as a bot; the effect is that the bot may leave 5 minutes after the *other* people go while that
person is still there. The kill switch and the 5-minute wait are the guards.

**Rejected:** turning on Skribby's `silence_detection` (a quiet stretch would end real calls: it was set to
0 on purpose, `docs/sop/2026-11-08-skribby-provider.md`); the bot's WebSocket participant events
(need a real-time transcription model or the Real-time Audio add-on, which we do not use); keeping a
"people seen" flag in the database (the list's own times say it, with no migration); a new meeting status
for "nobody came" (migration plus web work).

## What's involved

| File | Change |
|---|---|
| `app/bot_presence.py` (new) | `decide`, `is_bot_name`, `read_participants`, `parse_time`, `Who` |
| `app/config.py` | `bot_leave_when_only_bots`, `bot_only_bots_leave_seconds` (>= 60), `bot_nobody_came_seconds` (>= 60) |
| `app/bot_provider.py` | `BotMeeting.participants`, `BotMeeting.in_since` |
| `app/skribby.py` | `map_bot` fills them for a bot in the call; `payload` sends `empty_meeting_timeout` when the rule is on |
| `app/pipeline.py` | `_leave_if_alone` (called every poll), `_end_nobody_came`, `BOT_NOBODY_CAME`, `REASON_NOBODY_CAME`; `_remember` keeps the marker |
| `app/services.py` | words and tone for the marker and the ended meeting; no Cancel while leaving |
| `.env.example`, `deploy/README.md` | the three settings |
| `tests/test_bot_presence.py`, `tests/test_bot_leave_wiring.py` | 77 + 24 tests |

## Verification

- Ruff check and format: clean. Tests run locally: only the touched files and the neighbours that read the
  changed code (`test_bot_presence`, `test_bot_leave_wiring`, `test_bot_live_state`, `test_end_meeting`,
  `test_skribby_provider`: 433 passed on a scratch Postgres 17). CI runs the rest.
- **Not verified live:** (1) that Skribby fills `participants` while the bot records; (2) that the bot
  appears in it under the name we expect (if it does not, it counts as a person and the rule never
  fires, the safe side); (3) the real leave call on a real call (`POST /bot/{id}/stop` is the owner's
  "End now" path, proven on earlier calls, but not for this trigger); (4) the unit of the times (read as
  milliseconds when >= 1e11, else seconds; ISO text also read). The first call that logs
  `the bot was told to leave` proves them.

## Operate / roll back

- Off at once: `BOT_LEAVE_WHEN_ONLY_BOTS=false` in `.env`, recreate `web` and `worker` (no meeting
  `bot_sent` or `processing`). It also stops sending `empty_meeting_timeout` to new bots, so Skribby's
  default of 10 minutes applies again. A meeting already marked `nobody_came` still ends calmly.
- Windows: `BOT_ONLY_BOTS_LEAVE_SECONDS`, `BOT_NOBODY_CAME_SECONDS`.
- Watch for: `meeting N: the bot was told to leave (only_bots|nobody_came; K in the call)` and
  `the leave check failed (<ExceptionClass>)` in the worker log.

## Follow-ups

- Prove the live list on the first real call (see above); if `participants` is empty live, ask Skribby
  or use their WebSocket participant events (needs a real-time model or the add-on).
- A proper "ended, nobody came" meeting status and web row (needs a migration).
- `MIN_COVERAGE` in `app/transcript_source.py`, `time_limit` / `MAX_POLL_HOURS` and hours code: out of scope
  here, decisions pending.
- If a person shows up as "<Name>'s Assistant" the list may need a per-person "this is me" mark.
