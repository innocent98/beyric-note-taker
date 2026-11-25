# SOP: Calendar per workspace (slice W3, tasks 8 and 9)

**What shipped.** A calendar connection belongs to one workspace; one person may connect the same provider (even the same account) in several; an event held in several of their workspaces is recorded exactly once by a fixed rule; the Upcoming list says where; leaving a workspace takes the member's connection with them. Branch `feature/workspaces-calendar`, PR into `feature/workspaces`. Migration `0064` (placeholder, chained on `0063` (W2's); W2 uses `0063+`, W4 `0080+`; whichever merges second re-chains). Spec `docs/superpowers/specs/2026-11-24-workspaces-design.md` sections 4 and 12.3, 12.4.

## Why
`calendar_connections` was unique per `(user, provider)` and every calendar read took "the person's connection", so a workspace could not have its own calendar, and the same event on two calendars could have two bots. Worst failures: another workspace's calendar data shown, or a meeting recorded twice.

## How, and what was decided
| Decision | Choice |
|---|---|
| Key | unique `(user_id, team_id, provider)`; `oauth_flows.team_id` carries the workspace (found by the single-use state hash, so the callback needs no header and nothing can be forged). Redirect adds `?team=<id>` only for a non-default workspace. |
| Refusals | per workspace: a second account of the same provider is `different_account` (existing code); `already_connected` / 409 `calendar_already_connected` is the OTHER provider's calendar in the same workspace. Google in one workspace and Outlook in another is allowed. Same account again in the same workspace is a reconnect. |
| Grant | `calendar_connect.grant_still_used`: disconnect, leave and removal revoke at Google only when no other live connection of the person has the same account (provider, subject, tenant). Hooks always run. |
| Chooser | `app/calendar_arbitration.py` `choose_workspace`: (0) a join whose bot is already in or that ran keeps the meeting, (a) a workspace where the person switched it on, (b) the first-connected workspace whose rules would join it (lowest connection id: stable across reconnects; `connected_at` is rewritten), (c) none if none would. Grain: same platform and code, starts within 5 minutes. The one-live-join-per-person-per-code index stays the guarantee. |
| Race | `lock_person` (a Postgres advisory transaction lock per person) around `apply_batch`, `rerun_rules` and the override route; a test with two syncs at once fails without it. |
| Loser | its not-yet-sent join is cancelled with reason `recorded_elsewhere`; `_heal_losers` gives it back when the winner no longer records (disconnect, cancellation, rule change). The winner's join is made, moved or revived by the same `_decide` as before. A person with one calendar takes the old code path unchanged. |
| Settings | read through W2's `app/workspace_prefs.py` (`for_user`, `row_for_update`); `PUT /api/calendar/settings` writes the active workspace's row through the shared `calendar_api.apply_prefs`, whose switch reset is limited to that workspace's connection. Runner, organiser's-bot check and template reset read the join's own workspace. Organiser's-bot behaviour is otherwise unchanged (matching by account). |
| Born in | `ScheduledJoin.team_id` is the winner's; the runner already files the meeting there (`workspace_team_id=join.team_id`). |
| Notices | `alerts.notify_user(team_id=...)`: calendar notices are filed in their connection's workspace, so a disconnect of that workspace deletes them. |
| Leave | `teams.leave` calls `calendar_connect.detach_workspace` (events, joins and calendar notices of that workspace go, the connection row is deleted, provider clean-up parked on `session.info`); `DELETE /api/team/members/{id}` settles it after the commit (`settle_released`). Recorded meetings stay. |
| Recurring | cards come from the workspace's connection and hide recurring ids in `series_ignored` for that workspace (it was already per team). |

## What's involved
`alembic/versions/0064_calendar_per_workspace.py`; `app/models.py`; `app/calendar_connect.py`, `app/microsoft_connect.py`, `app/calendar_api.py`, `app/calendar_recurring.py`, `app/calendar_text.py`, `app/calendar_arbitration.py` (new), `app/gcal_sync.py`, `app/join_runner.py`, `app/calendar_states.py`, `app/organizer_bot.py`, `app/alerts.py`, `app/teams.py`, `app/api_team.py`, `web/components/app/calendar-upcoming.tsx`; `docs/openapi.json`, `web/lib/api/schema.d.ts`; `scripts/capture_workspaces_calendar.py` (and a `team=` argument on the recorder in `capture_calendar_connect.py`); tests `test_calendar_workspaces.py`, `test_calendar_arbitration.py`, `test_calendar_workspace_api.py`, `test_workspaces_calendar_migration.py`, `test_workspace_leaks.py` (calendar GETs moved out of `PENDING_SLICE`), `web/tests/calendar-upcoming.test.tsx`.

## UI (lead-designed, none in the designer's files)
On a row another workspace records: the server's line "Recorded in X. Switch here instead." (the row's `meta-line`), a secondary "Switch here" button (the same write as the switch), the switch shown `Off`; after a switch a muted `moved_text` line; a refusal shows in the list's error line. No new CSS or tokens. Looked at in a real browser at 390 and 1280 px against the real backend with a fake Google.

## Verification
Scratch Postgres 16 (Docker, removed afterwards); only named test files, never the full suite. Mutation checks: removing the workspace filter from `live_calendar_connection` fails the leak guard; removing the advisory lock fails the race test. **Not verified:** any real Google or Microsoft calendar (consent, token, event bodies are fakes or synthetic), a real running bot, production data, the server, and every test file CI runs that I did not.

## Operate / roll back
Deploy with the release (migration `0064` is metadata only). `alembic downgrade 0062` keeps each person's oldest connection per provider and deletes the extra ones with their events and joins. Reconnect behaviour of existing one-workspace people is unchanged.

## Follow-ups
- The Settings calendar card does not say which workspace it belongs to (needs a design line).
- `api_workspace_prefs.py` (W2) still calls `live_calendar_connection(session, user)`; it now passes the workspace (see the merge).
