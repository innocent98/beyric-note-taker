# SOP: W0.5 Meeting delete

## What shipped
`DELETE /api/meetings/{id}`: the owner deletes a meeting and everything derived from it. API only: the design draws no delete control, so no screen (designer gap). Branch `feature/w05-meeting-delete`, one PR against `main` (not merged). Migration **0021**.

## Why
Spec 3.3 and 13.1: a deleted meeting deletes everything taken from it, and the landing and privacy pages already promise it. No delete existed.

## How
| Piece | Decision |
|---|---|
| Who | `owned_meeting_or_404`: the recorder only; a teammate or outsider gets 404 (same as an unknown id) |
| When | Refused with 409 `meeting_active` while `status` is `bot_sent` or `processing` (the existing `ACTIVE` pair), checked under the row lock (`_lock_meeting`, so a worker holding it gives the usual `meeting_busy`). A `ready` or `failed` meeting (including a cancelled one) can go |
| Cascade | DB level. Translations, decisions, open questions and review items already had `ON DELETE CASCADE`. **Migration 0021** changes `notifications.meeting_id` from `SET NULL` to `CASCADE` (a notification about a deleted meeting would otherwise keep its title and body) |
| Outside any FK | Mail not yet sent: `notes_ready` `mail_outbox` rows (not `sent`: those feed the mail budget and caps) whose JSON `payload.meeting_id` is the id are deleted in the same transaction. The audio folder is removed after the commit. Vexa's copy of the recording is deleted **before** the DB delete when `audio_deleted_at` is null. The id comes from `deletion.recording_to_delete` (now public, shared with account deletion): it asks Vexa for the recording of a meeting whose id was never stored (a cancelled one) and waits out the grace; if Vexa refuses or has not settled, 502 `recording_delete_failed` and nothing is deleted, so the id is never lost while a copy exists |
| Kept on purpose (`meeting_id` nulled) | `usage_entries`: **hours are not released**. The code and the privacy page already say deleting does not give hours back, and the cap would be trivial to game otherwise (the spec does not say to release them). `ai_runs`: cost per hour and the daily translation limit; no meeting text (the exception is stored as a class name). `scheduled_joins`: the row stays `joined`, so the scheduler does not send a bot to the same calendar event again |
| Generic test | `tests/test_meeting_delete.py::test_every_table_that_points_at_a_meeting_cascades_or_is_kept_on_purpose` walks `Base.metadata`: any table with an FK to `meetings` must cascade or be listed in `KEPT` with a reason. W2's `tasks` fails it until it cascades |
| Rejected | Deleting `ai_runs` and `scheduled_joins` rows (the brief listed ai_runs): it would erase cost history and reset the per-day translation count, and deleting the join would re-send a bot. Soft delete (the spec says deleted) |

## What's involved
`app/services.py` (`delete_meeting`), `app/deletion.py` (`recording_to_delete` made public), `app/api.py` (route), `app/models.py` (`Notification.meeting_id`), `alembic/versions/0021_notification_cascade.py`, `app/templates/privacy.html` and `web/components/legal/privacy-content.tsx` (retention line, kept identical), `docs/openapi.json`, `web/lib/api/schema.d.ts`, `scripts/capture_meeting_delete.py`, `docs/fe-captures/meeting-delete/`, `docs/fe-integration-guide-meeting-delete.md`, tests `tests/test_meeting_delete.py`, `tests/test_team_visibility.py` (route classified owner-only), `tests/test_migrations.py`, `pyproject.toml` (F811 ignore for the test file). Landing copy unchanged: "Delete a meeting" is true of the service, but no screen offers it yet.

## Verification
- `tests/test_meeting_delete.py`: cascade schema walk; a full delete of a meeting with every kind of derived row while a second meeting keeps its rows; 409 for both active states, nothing deleted; ready and failed can go; teammate and outsider 404; unknown id 404; Vexa recording deleted, Vexa failure gives 502 with nothing deleted and no upstream text; no Vexa call when audio is already deleted; the audio folder is removed.
- `tests/test_team_visibility.py`: the route enumeration now classifies `DELETE /api/meetings/{meeting_id}` as owner-only, so outsiders and teammates are checked for 404 on a private and a shared meeting.
- `tests/test_migrations.py::test_0021_*`: FK rule `n` to `c`, a notification goes with its meeting, downgrade, upgrade.
- Live capture: `docs/fe-captures/meeting-delete/` (real HTTP).

## Operate / roll back
- Deploy: the migration runs with the app; it drops and recreates one foreign key (a brief lock on `notifications`, a handful of rows). **Number clash:** 0021 currently has `down_revision = "0019"`; when the C13 migration 0020 merges first, whoever merges second must re-point (`0021.down_revision = "0020"`) so `alembic heads` stays one.
- Roll back: `alembic downgrade 0020`/`0019` restores `SET NULL`. Deleted meetings are not recoverable (no soft delete); backups hold them for 14 days.

## Review
One Opus review (foreground-requested; the harness ran it async and it was awaited). **No Critical. One High, fixed:**

| Finding | Outcome |
|---|---|
| High: a cancelled/failed meeting never had its Vexa recording id stored, so delete left Vexa's audio orphaned | Fixed: `deletion.recording_to_delete` is reused; tests for the unstored id and for an unsettled Vexa (502, nothing deleted) |
| Medium: mail worker holding the outbox row past `lock_timeout` gave a 500 | Fixed: 55P03 on the delete maps to 409 `meeting_busy` |
| Medium: Vexa deleted, then the DB commit fails, leaves a meeting with no remote audio | Accepted: a retry recovers (Vexa's 404 counts as success); the reverse order would lose the id |
| Low: `sent` outbox rows were deleted, lowering the mail budget counts; unindexed scan | Fixed: only `notes_ready` rows not `sent` |
| Low: a failed `rmtree` was silent | Fixed: logged |
| Low: extraction worker's `ai_runs` insert fails on a meeting deleted mid-call | Follow-up (translation already handles it; `workspace.py` ~176) |
| Low: migration docstring said Revises 0020 | Fixed |

## Follow-ups
- Designer: the delete control and confirmation on the meeting page, with the task count (W2 adds tasks; the endpoint then needs a count for the dialog).
- `workspace.py` extraction: tolerate a meeting deleted during the model call when recording `ai_runs` (as translation does).
- W2/W6: when shares and tasks exist, add them to the cascade test (it will force it) and to the mail payload clean-up (`share_id`).
