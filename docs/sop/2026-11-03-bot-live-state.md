# SOP: show what the bot is doing while the call runs

## What shipped
_Updated the same day by `docs/sop/2026-11-03-bot-end-controls.md`: the state now carries the time it was seen (`bot_state_seen_at`) and reads "Checking on the bot..." when old, one failed lookup no longer clears it (two in a row do), `requested`, `stopping` and a call that ended have their own words, and the list polls. Where this document says otherwise below, that one wins._

While a meeting is `bot_sent`, the owner now sees what the bot is doing: joining, waiting to be let in, in the meeting, recording, or waiting on a prompt. Branch `feature/bot-live-state`, PR to `main` (number in the PR list; opened after PR 35 merged as `303fbd7`). No new column, no new API field, no migration.

## Why
On 2026-11-03 Adebayo admitted the bot into his Meet and the page still said "Bot sent" for the whole call. `services.status_text` said nothing about the bot until processing began, on purpose (it must not claim a recording that does not exist), so he read it as the bot not being recognised. Vexa reports the state while the call runs and the worker already polls it every 30 seconds; the app just threw it away.

## How
| Piece | Decision |
|---|---|
| States | Only those Vexa reports (lifecycle schema, `spike/vexa` at `31122f9`: `joining`, `awaiting_admission`, `active`, `needs_help`; `requested` and the terminal ones are not live states and read "Bot sent" or the usual words). Real runs on 2026-11-03: a good join (meeting 12) went `joining`, `awaiting_admission`, `active`, done; a refused v0.12.28 join went `awaiting_admission`, `needs_help`, failed |
| Where it is kept | `Meeting.completion_reason`, the column PR 35 already uses for the worker's own markers, while the meeting is `bot_sent`. Rejected: a new `bot_state` column (a migration for a value that lives minutes and is only displayed), and the in-memory dicts in `pipeline.py` (the API runs in another process) |
| One derived value | `recording` is not a Vexa status. The worker stores it when Vexa says `active` and the lookup has transcript segments or a recording id. `active` alone only means the bot is in: the text then says "Bot is in the meeting", never "recording" |
| Never over a marker | `_record_live_state` writes only when the column is empty or already holds a live state, so `google_meet_join_refused` (the pause before the second bot), a Vexa completion reason such as `awaiting_admission_rejected`, and the `needs_help` failure marker are never replaced. A live `needs_help` and the failure marker share a string, told apart by `status` (`bot_sent` vs `failed`) and `terminal_seen_at` (set only when the worker ends a join; `is_join_failure` already required both, and the pause reads only the refusal marker). The one place that did not was a timing helper in `tests/test_join_refusal.py` (`settle_time`), which now checks `terminal_seen_at` too |
| Cleared when | Vexa says anything that is not a live state (completed, failed, requested), Vexa cannot be reached for two polls in a row (one failed lookup keeps the last state, whose time is not renewed, so after two poll intervals the page says "Checking on the bot..."; after the second miss it says "Bot sent" again, then follows Vexa on the next poll), and `send_again` (reset to none). On the way to `ready` it is gone, so it never reads as a "reason" |
| Words | `services.BOT_STATE_TEXT`, read by `status_text` only for `bot_sent`; the bot name in the awaiting text is `settings.bot_name`. `needs_help` reuses PR 35's sentence minus its ending ("could not continue ... send the bot again"), which is true only after the 2 minute grace has failed the join: "The bot is waiting on a prompt in the meeting, such as a consent or verification screen. Check the meeting for a prompt." |
| Refresh | Nothing to add: `refresh_after_seconds` is already 30 while `bot_sent`, the Jinja page has its meta refresh, the Next page polls. A test follows joining, awaiting, in the meeting, recording through the polling |
| Next page | The wait card's heading is now `status_text` (it was a fixed "Waiting for the note taker to join", wrong once the bot is in) and its line says notes appear when the meeting ends. The old "Admit the note taker from the waiting room" line is gone; the awaiting text carries that instruction |
| Long badges | The texts are sentences and `.badge` is `nowrap`. A badge whose text is over 28 characters gets `badge-wrap` (Jinja macro and `Badge`), capped at 16rem; short words stay on one line. Checked at 375 px |

## What's involved
`app/pipeline.py` (`BOT_RECORDING`, `BOT_LIVE_STATES`, `_live_state`, `_record_live_state`, `_poll`), `app/services.py` (`BOT_STATE_TEXT`, `status_text`), `app/templates/components.html`, `app/static/components.css`, `web/components/app/meeting-view.tsx`, `web/components/ui/badge.tsx`, `web/styles/components.css`, tests (`tests/test_bot_live_state.py` new; `tests/test_web.py`, `tests/test_api.py`, `web/tests/meeting-view.test.tsx`, `web/tests/screens.test.tsx`, `web/tests/badge.test.tsx` new), `scripts/capture_owner_api.py` (`run_live_state`) with captures `104` to `110`, `docs/fe-integration-guide-owner-app.md` (section 7 and the verification table), the master checklist. `docs/openapi.json` and `web/lib/api/schema.d.ts` regenerate unchanged.

## Verification
- Tests first, watched failing (10 worker and text tests, 8 view tests, the badge test), then green. Python: states, the unknown fallback, Vexa unreachable, a good join poll by poll and ending clean, the refused v0.12.28 sequence, never replacing the refusal marker, other statuses untouched, the bot-name setting, Jinja page and list, API detail and list. Web: one test per capture, the poll that follows the bot, the list row, the badge.
- The captures `104` to `110` come from the real app over HTTP with the real `advance_meeting` fed one fake Vexa answer per state. The older captures were not rewritten: no shape they show changed.
- **NOT verified live:** a real Vexa call. The state names are read from Vexa's schema and the 2026-11-03 sequence reported by the lead; the fakes follow it. Whether `recording_id` or segments appear early in a live call is also not measured, so "Recording" may show up later than the bot's first words.

## Operate / roll back
Nothing to configure or migrate. Roll back by reverting the PR: rows left holding a live state read "Bot sent" on the old code (an unknown value is ignored by `bot_left_early`, which checks `ready` meetings only).

## Follow-ups
- `status_tone` is still `warning` for every `bot_sent` text, including "Recording". A calmer tone for `active` and `recording` needs a tone per state in `STATUS_TONES` and the `Badge` tones in the web types.
- ~~The list does not poll~~ Done in `bot-end-controls`: `refresh_after_seconds` on the list, a meta refresh on the Jinja list, `router.refresh()` on the Next list.
- Watch one real call and confirm the sequence and when "Recording" first appears; update this SOP.
