# SOP: a bot in the waiting room is no longer withdrawn by Vexa's lobby-timeout warning

## What shipped
A bot waiting to be let into a Google Meet is no longer pulled out after 12 to 14 minutes. Vexa's "lobby budget nearly spent" warning (`needs_help` raised from the waiting room) used to trip our 2-minute "stuck on a prompt" rule; the bot now keeps knocking until the host admits it or Vexa's own 15-minute lobby budget runs out. The owner's page keeps saying "Waiting to be let in". When Vexa ends the wait, the meeting reads "The bot was not let into the meeting" instead of a raw Vexa message. Branch `fix/lobby-needs-help`. Extends `docs/sop/2026-11-03-join-refusal-handling.md` (the `needs_help` row there is narrowed by this change).

## Why (found live 2026-11-06)
Two of the three joins on 2026-11-06 failed the same way: meeting 15 (manual link, sent 11:14 WAT) and meeting 16 (the first calendar auto-join, sent 13:59 WAT). In both, another person was hosting; the bot waited in the waiting room; the host clicked Admit; the bot vanished.

Evidence (read-only, run by Adebayo; the captures stay in the lead session's scratchpad):

| Time (WAT), meeting 16 | Source | What happened |
|---|---|---|
| 13:59:26 | Vexa lifecycle | `awaiting_admission`: the bot is in the waiting room |
| 13:59 to 14:13:30 | bot log `botlog.txt` | "Still in Google Meet waiting room... Ns elapsed" every 2 s, up to 844 s: never admitted while there |
| 14:11:28 | bot log | `[Escalation] Triggered: waiting_room_timeout_approaching`; Vexa sets `needs_help` |
| 14:13:31 | Vexa meeting row | `stopped while awaiting admission (withdrew the join request)`, `stop_requested: true`, attribution `user_action`: **our worker's stop** |

Meeting 15 shows the same pattern: `needs_help` at 11:27:11, stopped at 11:29:43.

**Root cause.** In Vexa v0.12.29 (`modules/join/src/shared/escalation.ts` at `31122f9`) the bot raises `needs_help` with reason `waiting_room_timeout_approaching` once 80% of its lobby budget has passed. The budget is 900 s by default (`bot_spawn/service.py`, `DEFAULT_LOBBY_BUDGET_S`), so the warning comes at 720 s. Our rule in `pipeline._join_problem` treated every `needs_help` without audio as "stuck on a consent or verification prompt" and stopped the bot after `NEEDS_HELP_GRACE` (2 minutes). So every bot not admitted within 12 minutes was withdrawn at about 14 minutes, even when the host was about to admit it. The host saw it as "admitted, then gone". Meeting 14 worked because Adebayo hosted it and admitted the bot within a minute.

## How
Vexa's own rule (`lifecycle/machine.py`) is that `needs_help` entered after `awaiting_admission` is still the lobby. We read the same evidence:

| Piece | Change |
|---|---|
| `app/vexa.py` | `VexaMeeting.reached_lobby`: true when `data.status_transition` has a step `to: "awaiting_admission"`. Malformed entries are skipped, so a bad payload can only keep a bot waiting, never end one |
| `app/pipeline.py` `_join_problem` | The 2-minute `needs_help` rule skips a bot that `reached_lobby`. Vexa ends that wait itself at its lobby budget. The rule still applies to `needs_help` before the lobby (a consent gate or captcha) |
| `app/pipeline.py` `_live_state` | `needs_help` from the lobby is stored as `awaiting_admission`, so the page keeps saying "Waiting to be let in. Admit '…' in the meeting" |
| `app/pipeline.py` `bot_failure_reason` | Vexa's `awaiting_admission_timeout` reads as `REASON_NOT_LET_IN` ("…or the waiting room timed out"), like `awaiting_admission_rejected` |

**Rejected / not done.**
- A longer lobby budget. Passing `automatic_leave.max_wait_for_admission` per request is not enough: Vexa's reconcile sweep reaps pre-active bots at its own `VEXA_LOBBY_BUDGET_S` + 60 s (`lifecycle/reconcile.py`, `default_preactive_grace`), so the container variable would have to change too. Not needed for today's failures: both hosts admitted within 15 minutes. Listed under Follow-ups.
- Reading Vexa's escalation reason. It is not in the fields we parse. The transition history is the rule Vexa itself uses, so we use that.

No migration, no new setting, no server step.

## What's involved
- `app/vexa.py` (`VexaMeeting.reached_lobby`, `_reached_lobby`)
- `app/pipeline.py` (`_join_problem`, `_live_state`, `bot_failure_reason`)
- `tests/test_join_refusal.py` (new section "needs_help raised from the waiting room"), `tests/test_vexa.py` (transition parsing)

## Verification
- New tests replay the 2026-11-06 sequence: lobby, then the warning, then two grace periods. The bot is not stopped, the page state stays `awaiting_admission`, and a later `active` gets a bot into the call. Vexa's `awaiting_admission_timeout` gives the "not let in" reason, and `needs_help` before the lobby still ends after the grace.
- `tests/test_join_refusal.py`, `tests/test_vexa.py`, `tests/test_bot_live_state.py`, `tests/test_pipeline.py`, `tests/test_end_meeting.py`: 285 passed locally on Postgres. `ruff check` and `ruff format --check` are clean. CI runs the full suite.
- **Not verified live yet:** a real meeting where the host admits after the 12-minute warning. Check the next auto-joined meeting hosted by someone else.

## Operate / roll back
Deploys with the usual merge (never while a meeting is `bot_sent` or `processing`). Roll back by reverting the PR. Nothing is stored differently: the live state is the same `completion_reason` column.

## Follow-ups
- A longer lobby for calendar meetings, if hosts often admit after 15 minutes: set `VEXA_LOBBY_BUDGET_S` on the `vexa` service (and, if needed, `max_wait_for_admission` per request). Check that Vexa Lite reads that variable first.
- The waiting room itself: an anonymous guest always knocks in Google Meet. Skipping the lobby needs a signed-in bot account invited to the event. Vexa Lite does not support signed-in bots, and Vexa's signed-in mode is blocked by its issue #1725 (see `docs/spike/meet-join-resilience.md`, sections 2 and 3). A separate decision.
