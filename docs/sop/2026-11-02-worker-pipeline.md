# SOP: Worker pipeline (Task 5)

## What shipped
`app/pipeline.py` (`advance_meeting`, `delete_expired_audio`), `app/worker.py` (loop, `python -m app.worker`) and a `worker` service in `compose.yaml`. Commit: `feat: add meeting processing worker`.

## Why
Joins Vexa, audio, transcription, speakers and notes into one resumable background process, so a restart never pays twice for OpenAI.

## How
- **Steps.** `waiting` (one Vexa poll) -> `audio` (download if `{audio_dir}/{id}/source.webm` is missing) -> `transcribing` -> `speakers` -> `notes` -> `done`. Each step commits; saved `transcript` / `speaker_transcript` are reused.
- **Waiting.** `requested/joining/awaiting_admission/active`, unknown statuses and `VexaError` keep waiting. `completed` proceeds when `recording_status == completed` and an audio file exists, or when the recording has stayed unfinished (e.g. `in_progress`) for `RECORDING_GRACE` (10 min) after the poll that first saw `completed` (`terminal_seen_at`): Vexa builds the audio file when `/raw` is read, so the download is attempted anyway; a finished meeting with no audio fails on the 3rd consecutive poll (in-memory counter, reset on restart) with the "may not have been admitted" reason. `failed` fails with no OpenAI call. Past `max_poll_hours` (6 h) from `started_polling_at` while still waiting, it fails. That limit belongs to the waiting phase only: a Vexa error in the speakers or notes step never fails a meeting. A download that has not worked in 6 hours fails with its own reason ("could not be downloaded") so it cannot hold the bot slot forever; Try again re-downloads.
- **Failures.** `failure_reason` is always a fixed plain sentence; exception text is never stored, only the type is logged. Audio is kept on failure so the meeting can be retried. Fewer than 5 words counts as no speech. An `AudioError` (zero-byte or corrupt file) deletes the audio folder and sets the step back to `audio`, so Try again downloads afresh instead of failing the same way. Vexa's `completion_reason` and recording id are stored (`Meeting.completion_reason`, `vexa_recording_id`) the first time they are seen; a removal reason (`evicted`, `max_bot_time_exceeded`) only adds a notice on the meeting page, never a failure.
- **Cleanup.** `delete_expired_audio` removes the folder for ready/failed meetings older than `audio_retention_days`, then deletes Vexa's copy (`DELETE /recordings/{id}`, 404 = already gone) when the recording id is known, and only then sets `audio_deleted_at`. A Vexa failure (including 409, meeting not over) leaves it unset and the next hourly pass retries.
- **Worker.** Walks unfinished meetings by id (`FOR UPDATE SKIP LOCKED`), so a waiting meeting cannot starve others; sleeps `poll_interval_seconds` after a full sweep; cleanup hourly; stamps `app_state.worker_heartbeat_at` at the start of every pass (the home page warns when it is over 5 minutes old; one long OpenAI step can outlast that); SIGTERM stops after the current pass. A long OpenAI call holds the row lock and a connection (fine for one bot).

## What's involved
`app/pipeline.py`, `app/worker.py`, `alembic/versions/0003_final_fixes.py`, `compose.yaml` (`worker`, `audiodata` volume at `/srv/data/audio`), `tests/test_pipeline.py`.

## Verification
`pytest tests/test_pipeline.py` against real Postgres with fake Vexa/OpenAI. Not exercised live.

## Operate / roll back
`docker compose up -d worker`. Revert the commit to remove.

## Follow-ups
Retry and `started_polling_at` are now handled by the web app (see `2026-11-02-web-pages.md`).
