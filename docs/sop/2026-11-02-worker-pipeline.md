# SOP: Worker pipeline (Task 5)

## What shipped
`app/pipeline.py` (`advance_meeting`, `delete_expired_audio`), `app/worker.py` (loop, `python -m app.worker`) and a `worker` service in `compose.yaml`. Commit: `feat: add meeting processing worker`.

## Why
Joins Vexa, audio, transcription, speakers and notes into one resumable background process, so a restart never pays twice for OpenAI.

## How
- **Steps.** `waiting` (one Vexa poll) -> `audio` (download if `{audio_dir}/{id}/source.webm` is missing) -> `transcribing` -> `speakers` -> `notes` -> `done`. Each step commits; saved `transcript` / `speaker_transcript` are reused.
- **Waiting.** `requested/joining/awaiting_admission/active`, unknown statuses and `VexaError` keep waiting. `completed` proceeds only when `recording_status == completed` and an audio file exists; a finished meeting with no audio fails on the 3rd consecutive poll (in-memory counter, reset on restart) with the "may not have been admitted" reason. `failed` fails with no OpenAI call. Past `max_poll_hours` from `started_polling_at`, it fails.
- **Failures.** `failure_reason` is always a fixed plain sentence; exception text is never stored, only the type is logged. Audio is kept on failure so the meeting can be retried. Fewer than 5 words counts as no speech.
- **Cleanup.** `delete_expired_audio` removes the folder for ready/failed meetings older than `audio_retention_days`, then sets `audio_deleted_at`.
- **Worker.** Walks unfinished meetings by id (`FOR UPDATE SKIP LOCKED`), so a waiting meeting cannot starve others; sleeps `poll_interval_seconds` after a full sweep; cleanup hourly; SIGTERM stops after the current pass. A long OpenAI call holds the row lock and a connection (fine for one bot).

## What's involved
`app/pipeline.py`, `app/worker.py`, `compose.yaml` (`worker`, `audiodata` volume at `/srv/data/audio`), `tests/test_pipeline.py`.

## Verification
`pytest tests/test_pipeline.py` against real Postgres with fake Vexa/OpenAI. Not exercised live.

## Operate / roll back
`docker compose up -d worker`. Revert the commit to remove.

## Follow-ups
Retry action (reset status to `processing`) belongs to the web task. Nothing sets `started_polling_at` until the submit flow exists; the worker sets it on first poll if empty.
