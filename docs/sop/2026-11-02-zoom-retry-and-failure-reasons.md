# SOP: Zoom retry and clear bot failure reasons

## What shipped
When Zoom refuses the bot's browser, the worker now sends a new bot automatically, up to 3 bots per meeting, and every bot failure shows a specific reason instead of "The meeting bot reported a failure". Branch `feature/zoom-retry-and-reasons`.

## Why
On 2026-11-02 Zoom refused the bot twice and let the third one in. Vexa reported `[Zoom Web] zoom_requires_rtms: meeting/account blocks automated browser joins ...` with `completion_reason: awaiting_admission_rejected`, but the app ignored the reason and showed a generic failure, so Adebayo had to resend by hand without knowing why.

## How
- `app/vexa.py`: `VexaMeeting.failure_reason` is read from `data.reason`, falling back to `data.last_error.reason`; capped at 300 characters; ignored (not refused) if it is not text, because it is only shown.
- `app/pipeline.py`: in the waiting step, a failed bot whose reason contains `zoom_requires_rtms` gets a new bot (`_resend`) while `join_attempts < MAX_JOIN_ATTEMPTS` (3). The new Vexa id replaces the old one and the recording fields reset, so the worker follows the new bot. If the new bot cannot be sent, the meeting fails saying so. `bot_failure_reason` turns other failures into plain words: `awaiting_admission_rejected` without the Zoom block reads "The bot was not let into the meeting ..."; anything else appends Vexa's own reason.
- Only the Zoom browser block is retried. A host declining the bot is not, since another bot would only ask again.
- The worker now logs the database error code (for example `22003`) with a failure, not just the exception type, which hid the bigint bug. The statement is still not logged, because its parameters can hold transcript text.
- `app/templates/meeting.html`: while waiting after a refusal, the page says "Zoom turned the bot away, so a new one was sent (attempt N of 3)."
- Rejected: retrying every failure (a declined bot would knock again), and waiting longer between tries (the poll interval already spaces them about 30 s apart; the manual third try that worked came about 2 minutes after the first).

## What's involved
`alembic/versions/0005_join_attempts.py` (`meetings.join_attempts`, integer, default 1), `app/models.py`, `app/vexa.py`, `app/pipeline.py`, `app/web.py`, `app/templates/meeting.html`, tests in `tests/test_vexa.py`, `tests/test_pipeline.py`, `tests/test_migrations.py`, `tests/test_web.py`.

## Verification
Tests use the reason string captured live: a refusal sends a new bot and follows its id; the third refusal fails with "3 times in a row"; a failed resend fails clearly; three refusals then admission reach `ready`, reading the last bot's id; a declined bot is not retried; an unknown reason is shown. 241 tests across the touched suites pass; ruff clean. **Not verified live**: a real Zoom refusal followed by an automatic retry (it depends on Zoom refusing again).

## Operate / roll back
Change the cap with `MAX_JOIN_ATTEMPTS` in `app/pipeline.py`. Roll back by reverting the PR; `alembic downgrade 0004` drops the column.

## Update 2026-11-03
`_resend` was split: `send_again` (shared) and `_resend`, which fails the meeting with a given reason. Google Meet refusals reuse `send_again` with their own pacing; see `docs/sop/2026-11-03-join-refusal-handling.md`. The Zoom retry itself is unchanged.

## Follow-ups
- If Zoom keeps refusing, the lasting fix is Zoom RTMS, which needs a Zoom Marketplace app.
