# SOP: Workspaces settings (slice W2: Tasks 5, 6, 7 and decision D4)

**What shipped.** Templates and vocabulary belong to a workspace (owners and admins write, members read and use); the pipeline, the translation, the notes rewrite, Send now and the mails read the MEETING's workspace instead of the recorder's own lists; a person's note taker name, auto-join rule and its two boxes and calendar template are kept per person per workspace (`workspace_prefs`); the Settings screen is split into "This workspace" and "Personal"; and a meeting BORN in a team workspace is shared with that workspace from the first second (D4). Branch `feature/workspaces-settings`, base `feature/workspaces`, the PR into `feature/workspaces`. Migration `0071` (placeholder number, chained on `0070`; the lead re-chains).

Spec: `docs/superpowers/specs/2026-11-24-workspaces-design.md` (sections 3, 12.1, 12.5, 12.7). Plan: `docs/superpowers/plans/2026-11-24-workspaces.md`. Foundation SOP: `docs/sop/2026-11-24-workspaces-foundation.md`. FE guide: `docs/fe-integration-guide-workspaces-settings.md` (captures `docs/fe-captures/workspaces/settings-*.json`).

## Why

After W1 every meeting lived in one workspace but the notes were still written with the recorder's personal templates and vocabulary, a member could not use a team's templates, and the note taker's name and auto-join rule were one setting for all of a person's workspaces. The rewrite also checked the template against the RECORDER, so a team admin rewriting a moved meeting could never choose a template, and a recorder who had left left nothing to choose from. D4 (his yes) says a team's meetings are shared with its members by default; nothing implemented it yet.

## How, and what was decided

| Decision | Choice |
|---|---|
| Who writes | Owners and admins write a workspace's templates and terms (403 `team_forbidden`); members read and use them. Another workspace's row, and a workspace the caller is not in, is 404 (the existence check comes before the role check, so a member never learns more from a 403). The personal workspace is its person's own. |
| Uniqueness | A template name is unique among a workspace's live templates and a term among its terms, whoever wrote them (`uq_templates_team_name`, `uq_vocabulary_team_term`). The cutover had copied several members' templates into one workspace: migration `0071` retires a later copy with the same instructions (what pointed at it moves to the kept one) and renames one with different instructions "Name (2)", so nothing a person wrote is lost or changed. |
| `templates.team_id` | Made NOT NULL: `0070` filled every row, and `0071` stops if one is null rather than guess. |
| New workspace | `teams.create_owned_team` seeds the default templates (made by the creator) and sets `share_new_meetings` true. Existing teams keep the value they had (migration touches nothing). |
| Copy from personal | `POST /api/templates/copy-from-personal` and `/api/vocabulary/copy-from-personal` (owner or admin of a team workspace; 409 `personal_team` for the personal one): a name already live is skipped, never overwritten; the personal rows stay. |
| What a meeting reads | `workspace_prefs.for_meeting` / `terms`: the vocabulary and default template of `meeting.workspace_team_id`, never the recorder's. Used by the pipeline (`_terms`, shadow check, notes), the translation job, the notes rewrite, the vocabulary sent to the bot (`transcript_source.send_options(workspace_team_id=...)`), a resent bot's name, Send now's template check. Nothing reads the recorder's account for it, so it works after they left. |
| Rewrite | `services.regenerate_notes` takes the template from the MEETING's workspace (`services._workspace_template`); a personal template of the recorder is no choice there (400 `template_required`). |
| Placement | `placement._resolve` returns the workspace too; a template must be one of that workspace. A calendar join queued before the cutover that names a template of another workspace uses the workspace's default instead of failing at the door (never for a manual request). |
| Per-workspace prefs | `WorkspacePrefs` row per (workspace, person), falling back to the person's own defaults (`user_settings`, `users.bot_name`). `workspace_prefs.Prefs` has the attribute names of a `UserSettings` row, so `JoinSettings.for_user`, `gcal_sync`, `join_runner`, `organizer_bot`, `mail_summary` take it unchanged; each reads the CONNECTION's / join's / series' workspace. `GET/PUT /api/workspace-prefs` act on the active workspace; `PUT /api/calendar/settings` and it share `calendar_api.apply_prefs`, so the two cannot differ. The first write copies the person's current values into the new row, so changing one field never resets the others. |
| Bot name | Workspace's own name, else the person's default, else the server's. `PUT /api/settings/bot-name` stays the PERSONAL default. |
| Template deleted | The workspace's prefs and unsent calendar joins (that workspace only) that pointed at it move to the workspace's oldest template; the personal default follows in the personal workspace. `gcal_sync.set_join_template` now needs `team_id`. |
| Account deletion | Templates and terms a person wrote in a team workspace that outlives them pass to the workspace's owner (or the member who joined first when they were the owner) instead of being deleted with them (`deletion._hand_workspace_lists_on`). |
| Mail (12.7) | Reminders already check the series' workspace setting (`teams.reminders_on`) AND the person's `notify_reminders`; the join sentence in a reminder now reads the person's rules in the series' workspace. `workspace_prefs.mail_allowed` states the rule. Notes-ready and task mails are about the person's own meeting: their preference only. No mail content or link changed (a meeting opens by id and the web switches workspace, 12.2). |
| **D4 and `share_new_meetings`** | A meeting is born shared when its workspace is a team workspace whose "share new meetings" switch is ON and the recorder belongs to it (`teams.birth_share_team_id`, called by `placement.place_bot`, the one place a meeting is made: pasted link, Send now, calendar join, member or owner alike). The switch IS the D4 default: new teams start with it on, each existing team keeps what it stored, and a manager turning it off makes later meetings born private. The personal workspace stays private. Existing meetings are never changed (the helper runs only when a row is made; the migration touches no meeting visibility). The old "default read when the notes are first written" (`teams.apply_share_default`, owners and admins only) stays for meetings made before this and for the person's own auto-share choice. |
| "Keep private" | The existing unshare. It now also sets `meetings.kept_private`, and `apply_share_default` returns early for such a meeting: otherwise a meeting born shared and unshared during the call would be shared AGAIN when its notes were first written (privacy bug the new default would have made likely). Sharing again by hand clears the mark. The flag is never read for who may see a meeting (`app/visibility.py` is unchanged). |
| `/templates`, `/vocabulary` pages | Now team pages (`?team=`), like Settings, so the server render names the same workspace as the tab. |

### Lead-designed UI (the designer's files draw none of it)

| Element | Where | Built from |
|---|---|---|
| "Personal" and "This workspace" group headings with one line each | Settings (`web/app/settings/page.tsx`, `SettingsGroup`) | `.set-title`, `.set-sub` of the existing cards |
| "Note taker name in this workspace" card | Settings > This workspace (`settings-workspace-bot-name.tsx`) | the "Note taker name" card, same classes and `Field` |
| Read-only templates for a member: one notice line, read-only fields, no Add, Save, Delete | `/templates` | `Alert kind="info"`, existing `Field`/`TextareaField` `readOnly` |
| Read-only vocabulary for a member: one notice line, no add form, no remove buttons | `/vocabulary` | `Alert kind="info"` |
| "Copy from my personal workspace" button and its one result line | `/templates`, `/vocabulary` (owners and admins of a team workspace) | secondary `Button`, `Alert kind="info"` |

## What's involved

| Path | Change |
|---|---|
| `alembic/versions/0071_workspaces_settings.py` | uniqueness per workspace, dedupe, `templates.team_id` NOT NULL, `meetings.kept_private`, downgrade |
| `app/models.py` | `Template`/`VocabularyTerm` uniqueness, `Meeting.kept_private` |
| `app/workspace_prefs.py` (new) | `Prefs`, `for_user`, `row_for_update`, `terms`, `default_template_id`, `for_meeting`, `mail_allowed` |
| `app/api_workspace_prefs.py` (new), `app/api_schemas.py`, `app/api.py` | `/api/workspace-prefs`, templates and vocabulary routes on the active workspace, the two copy routes |
| `app/services.py` | template and vocabulary services take the workspace; `_workspace_template`; `manager_or_403`; copy functions |
| `app/pipeline.py`, `app/translation.py`, `app/transcript_source.py`, `app/placement.py`, `app/send_now.py` | read the meeting's workspace; D4 at birth |
| `app/gcal_sync.py`, `app/join_runner.py`, `app/organizer_bot.py`, `app/calendar_api.py`, `app/mail_summary.py` | prefs of the connection's / join's / series' workspace; `apply_prefs` |
| `app/teams.py`, `app/meeting_teams.py`, `app/deletion.py`, `app/web.py` | seeding and share default for new teams, `birth_share_team_id`, `kept_private`, hand-over on account deletion, the old server pages act on the default workspace |
| `web/components/app/templates-editor.tsx`, `vocabulary-manager.tsx`, `settings-workspace-bot-name.tsx`, `web/app/{settings,templates,vocabulary}/page.tsx`, `web/lib/api/{client,types}.ts`, `web/lib/active-team.ts` | the screens above |
| `tests/test_workspace_{templates,pipeline,prefs,birth_share}.py`, `tests/test_workspaces_settings_migration.py`, `tests/test_workspace_leaks.py` | new tests; the guard now lists templates, vocabulary and `/api/workspace-prefs` as workspace data (their cast carries a marker in each) |

## Verification

Scratch Postgres 17 (Docker, removed afterwards), only named test files, never the full suite (CI runs it). New tests: `tests/test_workspace_templates.py` (14), `test_workspace_pipeline.py` (10), `test_workspace_prefs.py` (12), `test_workspace_birth_share.py` (8), `test_workspaces_settings_migration.py` (3), the extended `test_workspace_leaks.py` (17), and vitest `workspace-templates-vocabulary`, `settings-workspace-bot-name`, `settings-page` (71 with `screens`). D4 and the leak guard were checked by mutation (the helper made to return None, the template list made to ignore the workspace: both fail). Existing files run after the change: calendar settings, gcal sync, placement, send now, organizer bot, join runner, mail summary, series mail, translation, web, accounts, API, migrations (82), team and workspace test files, about 3,400 tests in all. Tests that encoded the old recorder-owned rules were updated, not skipped: `test_calendar_settings_api` reads the workspace row, `test_send_now` (a workspace with no template left), `test_join_runner` (a tampered template of another user is never used; the join takes its own workspace's default), `test_migrations` (raw inserts now need `team_id`), `test_template_delete`. Lint, format check, `tsc`, eslint (3 pre-existing warnings) clean. Real browser (built-in browser pane against a real local FastAPI, Postgres and `next dev`) at 1280 and 390 px, as a plain member: Templates and Vocabulary read-only with the notice, Settings with both groups and the new card; the manager view (copy button) is proved by vitest only. Live HTTP captures: `docs/fe-captures/workspaces/settings-*.json`. **Not verified:** a real Google or Outlook calendar, a real model call (rewrite and pipeline use a recording fake), any mail send, the cutover on production-shaped data (W5), and the full suite (CI).

## Operate / roll back

`alembic downgrade 0070` restores the per-author uniqueness and drops `kept_private`; templates retired or renamed by the upgrade stay so, and per-workspace prefs written meanwhile stay in `workspace_prefs` (0070's table). Nothing deploys before the whole release.

## Follow-ups

- `/api/calendar` GETs are still the person's own list until W3; its settings now read and write the CONNECTION's workspace row. W3 must keep `workspace_prefs.for_user(session, user, connection.team_id)` when it keys connections by workspace.
- A member's review item "Add to vocabulary" in a team workspace is now 403 `team_forbidden` (only owners and admins write the list); the web shows the error text. A "suggest a term" path for members is not built.
- A team made before this keeps `share_new_meetings` off until a manager turns it on; the Team screen already has that switch.
