# SOP: "Could not join" mail and bell, wired to the not-admitted failure

## What shipped
When a bot that was sent is **not admitted**, the owner now gets the designer's "Could not join" mail (`mail_templates.NOT_JOINED`) and one bell entry of a new kind, `meeting.not_admitted`. Before this, that path sent nothing. Branch `feature/could-not-join-wiring`, PR to `main` (number on the PR page; not merged by the author). Adebayo said "Yes, wire it" on 2026-11-07 for this one cause. Python only: no migration, no API shape, no screen. Cause mapping and the mail's words: `docs/sop/2026-11-07-mails-parity.md` ("Could not join: cause mapping").

## Why
The mail existed (built in the mails-parity PR) but nothing sent it, so an owner whose Meet call refused or ignored the bot saw only a "Failed" meeting row. The bell also said nothing for a pipeline failure.

## How
`app/pipeline.py` `_fail` takes a new keyword, `not_admitted_at`. It is passed (as `now`, the poll that saw the failure) from exactly two places, and only for these causes:

| Cause | Call site | Told? |
|---|---|---|
| Vexa `completion_reason` `awaiting_admission_timeout` or `awaiting_admission_rejected` (host declined), reason `REASON_NOT_LET_IN` | `_poll`, `info.status == "failed"` branch: told when `bot_failure_reason(...) == REASON_NOT_LET_IN` | yes |
| Google refused the ask to join (`MEET_REFUSED`), after the second bot (`REFUSAL_MAX_SENDS`) | `_on_join_problem` | yes |
| First Google refusal (only pauses for one retry), Zoom blocked (also reports `awaiting_admission_rejected`, but `bot_failure_reason` returns the Zoom reason), owner ended the bot, bot failed, recording failed, needs help, send unconfirmed, a new bot could not be sent after a refusal, completed with no recording, six-hour limit, host full / own limit / code in use / event ended (join rows, `app/join_runner.py`) | unchanged | **no** |

`_fail` calls `_tell_not_admitted` **before the commit**, so the bell entry and the failed state land in one transaction.

**Failure paths (why a mail problem can never stop the bell or the state change):**
1. The mail payload (`mail_templates.not_joined_payload`, which reads the owner's time zone from the database) is built **inside a savepoint with a catch-all**. A payload that cannot be built is logged by exception type only, and the mail falls back to the plain alert mail (the bell's own title and body): `mail.deliver_pending` turns a `ValueError` from a build into a permanent failure with no retry, so a bad payload must never be queued.
2. `alerts.notify_user` writes the bell row first (deduped by `meeting.not_admitted:<meeting id>`, `INSERT ... ON CONFLICT DO NOTHING`), and queues the mail only for the call that wrote the row, in its own savepoint. Mail switched off, an address on the suppression list, an inactive account or no provider: bell only (`alerts._wants_email`, unchanged).
3. The `notify_user` call itself is wrapped in a catch-all in `_tell_not_admitted`: a caller bug (ValueError) is logged and the meeting still fails and commits.

**Dedupe.** The key holds the meeting id, so a meeting produces one bell entry and one mail however often it fails again (a manual retry that is refused again says nothing new). Manual sends and calendar auto-joins both go through the pipeline, so both are covered.

**Words.** The designer drew no notifications list, so the bell entry reuses the mail's own sentences from `Emails.dc.html:137-149`: title "The note taker could not join your meeting"; body "We tried to join Google Meet btp-tkio-gtb but were not admitted. This usually happens when the host does not let the note taker in from the waiting room." The only change is that "at {time}" is left out of the bell body (the bell row already shows its own time in the owner's zone). Both strings are constants in `mail_templates.py` (`NOT_JOINED_TITLE`, `NOT_JOINED_REASON`), shared with the mail. Severity `error`.

Rejected: sending from `app/join_runner.py` (a joined row never reaches `_expire`, so it cannot know about a waiting room; the PR #117 review found the same); a separate admin alert (not asked for); showing a later deadline as the time (the mail says when the failure was seen).

## What's involved
- `app/pipeline.py`: `NOT_ADMITTED_KIND`, `_tell_not_admitted`, `_fail(..., not_admitted_at=)`, the two call sites.
- `app/mail_templates.py`: `NOT_JOINED_TITLE`, `NOT_JOINED_REASON` (the template reads them; its words are unchanged).
- `tests/test_pipeline_not_joined.py` (new), `pyproject.toml` (per-file F811 ignore, as the sibling test files have).
- `scripts/capture_could_not_join.py` and `docs/fe-captures/could-not-join/` (real captures); `docs/fe-integration-guide-could-not-join.md`.

## Verification
`tests/test_pipeline_not_joined.py` (21 tests): the timeout, host declined and Google refused (after the last send) each give exactly one bell and one mail with the time of the poll that saw it; the first refusal gives none; the owner's time zone is used; each of unknown bot failure, recording failed, owner ended, send failed after a refusal, needs help, Zoom blocked, completed with no recording gives neither; a second failure of the same meeting gives nothing new; two meetings give two; a failing payload build still leaves the meeting failed, the bell sent and a plain alert mail; a database error in the build is rolled back alone; a mail that cannot be queued and a `notify_user` that raises both leave the meeting failed; mail switched off or suppressed gives the bell only; the queued mail renders without the "Send it again" button. Also green: `tests/test_pipeline.py`, `test_join_refusal.py`, `test_bot_live_state.py`, `test_end_meeting.py`, `test_alerts.py`, `test_mail.py`, `test_join_runner.py`, `test_api.py`, `test_vexa.py`, `test_web.py`; `pytest --collect-only` over `tests/` (4169, no import cycle); `ruff check` and `ruff format --check`. Real capture (`scripts/capture_could_not_join.py`: real pipeline, real app under uvicorn, real migrations, Vexa stubbed): `GET /api/notifications` returns the two entries, none for the crashed bot. **Not verified:** a real Vexa waiting-room timeout and a real mail provider send (Vexa is stubbed; mail goes to the outbox and is rendered by the real template); how Gmail or Outlook draw the mail (already unchecked in the mails-parity SOP).

## Operate / roll back
No migration, no environment variable, no nginx line, no server step before merge. Revert the PR to stop sending; bell entries and queued rows already written stay (a queued `meeting_not_joined` row after a revert would still render, because the template is not removed). Note: the migration number 0034 stays free.

## Follow-ups
- C15b (send now): turn on `mail_templates.SEND_AGAIN_PATH`; until then the mail says "you can send it again" with no button.
- The web bell page (`web/components/app/notifications-view.tsx`) is a generic list: it shows `title` and `body` as they come and links any entry with a `meeting_id` to "Open the meeting", so no web change was needed. The designer drew no notification list; if they draw a card for this kind, replace the two constants.
- A meeting that completes with no recording (`REASON_NOT_ADMITTED`, "may not have been admitted") is not told: the spec lists only Vexa's two admission reasons and Google's refusal.
