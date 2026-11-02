# SOP: Final fix wave (2026-11-02)

## What shipped
Sixteen fixes on `feature/note-taker-mvp` found in the last review: transcription keywords as a list, database-aware `/health`, Vexa-side audio deletion, stuck-recording handling and a Cancel button, honest notes failures, speaker and removal notices, worker heartbeat, plus deploy and pin fixes. Commits: `fix: send transcription keywords as a capped list`, `feat: add migration 0003 ...`, `feat: add Vexa recording delete and bot stop ...`, `fix: fail notes on partial replies ...`, `feat: harden recording lifecycle, cancel, heartbeat, health and meeting notices`, `chore: restart db, honour empty trusted proxy list ...`, plus docs. Evidence per item: `.superpowers/sdd/2026-11-02-beyric-note-taker-mvp/final-fix-report.md`.

## Why and how
| Area | Problem | Fix |
|---|---|---|
| Keywords (`app/transcribe.py`) | Sent one newline-joined string; SDK 3.42.1 types it as a list | Pass a list, at most 50 terms of 100 chars. Live call with two terms spelled both right |
| Health (`app/main.py`, `compose.yaml`) | `/health` ignored the database; `db` never restarted | `select 1`, 503 `unavailable` on failure; `restart: unless-stopped` |
| Vexa audio copy (`app/pipeline.py`, `app/vexa.py`) | Only the local copy was deleted | Recording id stored on first sight; cleanup also calls `DELETE /recordings/{id}`; `audio_deleted_at` set only when both are gone |
| Stuck recording | `in_progress` blocked new bots for 6 h | After 10 min from the first `completed` poll, download anyway (`/raw` assembles the file on read). `POST /meetings/{id}/cancel` stops the bot with `DELETE /bots/{platform}/{native_id}` |
| Notes (`app/notes.py`) | Missing headings were filled with "None noted"; `content_filter` replies were accepted | Any finish reason but `stop`, or any missing heading, raises `NotesFailed`. Nothing is filled in |
| Speakers | Labelling failures only logged | `label_transcript` returns `(text, complete)`; `Meeting.speakers_incomplete` drives a notice |
| Removal | `completion_reason` unused | Stored; notice for `evicted` and `max_bot_time_exceeded`; never a failure |
| Retry loops | Corrupt `source.webm` repeated forever; late Vexa blip failed a meeting that had a transcript | `AudioError` deletes the folder and returns to `audio`; the 6 h limit applies to waiting only |
| Edge and docs | 60 s nginx timeout on regenerate; README spoof test lacked CSRF; `:-` default ignored an empty value | 300 s on that route; README fixed (`max_concurrent_bots: 1` too); `${VAR-default}` |
| Heartbeat | Nothing showed a dead worker | `app_state.worker_heartbeat_at` each pass; home-page warning after 20 min (was 5) |
| Vexa shapes | Wrong-typed fields were iterated or ignored | Non-list or non-object fields raise `VexaError` |
| Pins | `openai>=2.0` lacks `keywords`; ruff floating | `openai>=2.50` (first release with `keywords`, found by inspecting wheels); `ruff==0.17.3` |

## What's involved
`alembic/versions/0003_final_fixes.py` (meetings: `vexa_recording_id`, `speakers_incomplete`, `completion_reason`, `terminal_seen_at`; app_state: `worker_heartbeat_at`), `app/{transcribe,vexa,notes,pipeline,worker,web,main,models,config}.py`, templates `index.html` and `meeting.html`, `app/static/app.{css,js}`, `compose.yaml`, `deploy/{nginx.conf,README.md}`, `.env.example`, `pyproject.toml`, tests for each.

## Verification
Full suite, ruff check and format against a throwaway `postgres:17`; migration upgrade and downgrade on a scratch database with one head; `nginx -t` in a throwaway container; the README spoof-test commands run against a local app; one live transcription call with two keywords.

## Operate / roll back
Run `alembic upgrade head` (the `migrate` service does). Roll back with `alembic downgrade 0002` and revert the commits. Meetings that finished before this release have no stored recording id, so their Vexa copy is not deleted by cleanup.

## Follow-ups
- Done 2026-11-02: the heartbeat warning threshold was raised from 5 to 20 minutes (`app/web.py` `WORKER_STALE_AFTER`), because a healthy pass on an hour-long recording outlasted 5. A pass longer than 20 minutes would still warn.
- No lockfile (no `uv` or `pip-tools` here); only ruff is pinned exactly.
- Task 7 server steps and Task 8 acceptance are still open.
