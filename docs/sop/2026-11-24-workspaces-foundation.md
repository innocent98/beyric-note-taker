# SOP: Workspaces foundation (slice W1)

**What shipped (PR A, Tasks 1 to 3; Task 4 web follows in PR B).** A team becomes the container of everything. Every meeting, notification, template, vocabulary term, calendar connection and scheduled join now carries its workspace (a team); the active workspace is **required context on every route** (the `X-Team-Id` header, else the personal workspace, never "no narrowing"); lists, counts, badges, Ask and the bell narrow by it through one function. The multi-team switch is removed (it was on in production already). Branch `feature/workspaces-foundation`, base `feature/workspaces`. Migration `0062` (placeholder number; the lead renumbers).

Spec: `docs/superpowers/specs/2026-11-24-workspaces-design.md` (sections 3, 4, 8, 12). Plan: `docs/superpowers/plans/2026-11-24-workspaces.md`.

## Why

A team was only a partial workspace: meetings listed per team but Ask, notifications, templates and the calendar were per person across every team, so switching team did not switch what a person saw. The worst failure is a person seeing workspace A's data after switching to B, so the narrowing is one helper and a permanent guard test enumerates every route.

## How, and what was decided

| Decision | Choice |
|---|---|
| Container vs readability | `meetings.workspace_team_id` (NOT NULL, FK, no ON DELETE action) is the **container**. Who may read is unchanged: `shared_team_id` / `owner_team_id` and `app/visibility.py`. `visibility.in_workspace(user, team)` = readable AND filed in the workspace. The header only narrows, never widens. |
| Cutover (spec 8, 12.1) | Backfill: workspace = `owner_team_id`, else `shared_team_id`, else the recorder's personal team (made when missing). **`shared_team_id` and `owner_team_id` are never touched**; the rehearsal test asserts identical visibility columns and row counts. |
| Filing rule kept true everywhere | A meeting shared to or owned by a team is filed in that team: a model attribute event (`app/models.py`, `_file_in_team`) does it on every writer, plus explicit writers in `app/meeting_teams.py` (share, move, move back to the recorder's personal workspace), `app/teams.py` (leave: the leaver's unmoved meetings go to their personal workspace; the share default uses the meeting's workspace), `app/deletion.py` (given back to the recorder's personal workspace). Unshare ("keep private") leaves the meeting in the team's workspace, readable by its recorder only. |
| Insert-time defaults | `app/birth_workspace.py`: column defaults give every row a workspace even from paths that do not know it (meeting: owning team, else shared team, else personal; notification: its meeting's workspace, else personal; template and vocabulary: the owner's personal workspace; calendar connection and join: the connection's / the person's default workspace). Explicit values win. |
| No header | The person's **default workspace** on every route, `GET /api/session` included (`app/workspace_context.py` `require`/`current`; `teams.default_membership`: only team, else only ordinary team, else personal; the same as `is_default` in `GET /api/teams`). Never "everything". Chosen over strictly-personal so pilot users keep landing where they see their meetings today; the web always names its workspace. One-line revert in `workspace_context.require` and `visibility.active_or_default`. A team the caller is not in is 404 on data routes and quietly the default on `GET /api/session` (the first call of every page). |
| Detail by id | Authorises by readability only; returns `workspace_team_id` and `workspace` (`id`, `name`, `is_personal`, `kind`; null when the reader is not a member of that team) so the web can switch with a toast. |
| Session | `active_workspace`, and `other_workspaces` with `unread_notifications` each (the switcher dot). `multi_team` stays in the response as a constant `true` for the older web. |
| Notifications | `notifications.team_id`; the bell, its count and "read all" are per workspace; hours and account notices (no meeting) are in the personal workspace. |
| Ask | `answer`, `scope`, `suggestions`, `retrieve`, `candidates` take the active workspace and search only its readable meetings; the tasks path lists only that workspace's tasks. No "ask across workspaces". |
| Born in | A pasted link / Send now: the active workspace (`PlacementRequest.workspace_team_id`); a calendar join: `ScheduledJoin.team_id`. |
| Not done here (other slices) | Calendar GETs (`/api/calendar`, `/recurring`, `/not-recorded`), templates and vocabulary routes are classed `PENDING_SLICE` in the guard test (W2/W3 move them). D4 (shared by default in a team workspace) is not implemented: the existing per-team share switch still decides, now keyed to the meeting's workspace. |

## What's involved

| Path | Change |
|---|---|
| `alembic/versions/0062_workspaces_core.py` | columns, `workspace_prefs`, backfill, downgrade (copies of templates/vocabulary are dropped or taken out of use) |
| `app/models.py` | `Meeting.workspace_team_id`, `Notification/Template/VocabularyTerm/CalendarConnection/ScheduledJoin.team_id`, `Team.kind`, `Plan.max_owned_teams`, `WorkspacePrefs`; templates unique on `(owner, team, name)`, vocabulary on `(owner, team, term)` |
| `app/workspace_context.py` (new) | `requested_team`, `require`, `current` |
| `app/birth_workspace.py` (new) | the column defaults |
| `app/visibility.py` | `in_workspace`, `active_or_personal`, `readable_in` (no `None`) |
| `app/team_admin.py` | `my_membership`, `active_team` always resolve a workspace |
| `app/teams.py`, `app/meeting_teams.py`, `app/deletion.py` | flag removed; filing rules above; `move_team_content` removed (only the old flag-off join used it) |
| `app/notifications.py`, `app/api_accounts.py` | per-workspace bell, `unread_by_workspace` |
| `app/ask.py`, `app/api_ask.py`, `app/api_tasks.py`, `app/api_series.py`, `app/workspace.py`, `app/services.py`, `app/api.py` | narrowing, session and detail fields |
| `.env.example`, `deploy/README.md`, `scripts/clean_env.py` | `MULTI_TEAM_ENABLED` removed (a leftover line is dropped by `clean_env`) |
| `tests/test_workspaces_migration.py`, `tests/test_workspace_context.py`, `tests/test_workspace_leaks.py` | rehearsal, context, the permanent guard |

## Verification

Scratch Postgres 16 (Docker, removed afterwards), only named files, never the full suite. The cutover rehearsal seeds five people, personal and ordinary teams, shared, moved and private meetings, connections, templates, vocabulary, notifications and joins, and asserts counts, unchanged visibility columns, every backfill rule, and the round trip. The guard test fails when `in_workspace` ignores the workspace (checked by mutation). **Not verified:** a copy of production data, the server, and every test file CI runs that I did not.

## Operate / roll back

- **`MULTI_TEAM_ENABLED`:** `scripts/clean_env.py` removes `MULTI_TEAM_ENABLED=true` from the server `.env`. Rolling the code back to a version that reads the flag therefore needs the line put back first (`MULTI_TEAM_ENABLED=true`), or every team feature switches off.

Deploy: normal; the migration is long and `deploy.sh` backs up first. Roll back: `alembic downgrade` to the previous head drops the columns and table; per-workspace preferences are lost. Nothing deploys before the whole release.

## Follow-ups

- `notifications.team_id` keeps its BIRTH workspace after a share, a move or a leave (accepted for now).
- A person who leaves a team keeps recording into it through a calendar connection filed there until W3 disconnects the connection on leave (accepted until W3).

W2 (templates, vocabulary, settings), W3 (calendar, arbitration), W4 (limits, lifecycle, gate), W5 (integration). Old `scripts/capture_*` still reference the removed flag. The web (PR B) must send `X-Team-Id` on every non-personal route and switch on `workspace_team_id`.

---

## Task 4: the web (PR B)

**What shipped.** The web names its workspace on every route that is not the person's own, switches to a meeting's workspace when one is opened by its id, and shows unread notifications of the other workspaces in the switcher. Branch `feature/workspaces-web`, base `feature/workspaces`. No new dependency, no backend change (the API fields it reads shipped in PR A). FE guide: `docs/fe-integration-guide-workspaces.md`, every body from `docs/fe-captures/workspaces/` (`scripts/capture_workspaces.py`).

| Decision | Choice |
|---|---|
| Which calls carry `X-Team-Id` | `isTeamScoped` (`web/lib/api/client.ts`) is now "every `/api/*` call except the person's own" (sign-in/out, auth, billing, admin, public and token routes, `GET`/`POST /api/teams`, invitation preview/accept, the profile settings, the provider callbacks). Routes that only open one thing by id, and the ones W2/W3 will scope (templates, vocabulary, calendar, settings), carry it too: the server ignores it where it does not read it. |
| Auto-switch | `planMeetingSwitch` (`web/lib/active-team.ts`, pure) and `MeetingWorkspaceSync` (rendered in `MeetingView`): on opening a meeting whose `workspace` is not the tab's, store it (the default workspace is stored as "no choice") and toast "Switched to <name>". Nothing when the meeting is already in the tab's workspace, when `workspace` is null (reader not a member) or when it is not among the person's workspaces. |
| Switcher dots | `useWorkspaceUnread` reads `other_workspaces` from `GET /api/session` when the switcher appears and whenever the tab changes workspace. An OTHER workspace with unread gets "(N unread)" in its option and one small dot sits on the select; none when nothing is unread. A failed read draws nothing. |

**LEAD-DESIGNED, not in the designer's file (for the designer to review):** the toast wording "Switched to <name>" (the designer's own toast component), the switcher's "(N unread)" option suffix, and the dot on the select's corner (`.team-switch-dot`, one rule in `web/styles/components.css`, tokens only).

**Verification.** `npm run lint` (3 old warnings in `scripts/parity-meeting-delete.mjs`), `npm run typecheck`, and named vitest files only: `team-header` (the new scoping rule), new `workspace-switch` (11 tests on the real captures: header on Ask/templates/vocabulary/notifications/calendar/settings, the person's own routes carry none, the switch and the toast, the dots), `team-switcher`, `active-team`, `team-proxy`, `team-server`, `team-scoped-screens`, `shell-drawer`, `meeting-team-card`, `meeting-view`, `meeting-series`, `meeting-milestones`, `ask`, `ask-page`, `public-shell`, `decisions-review-shell`, `team-view`, `teams-view`, `tasks-view` (413 tests, all green). Looked at in a real browser (the Next dev server over the real backend and the capture database, signed in as the capture's test account) at **1280 and 390 px**: the dot and options on the sidebar and the top-bar switcher, opening a meeting of Platform team from the personal workspace showed the toast "Switched to Platform team", moved the switcher and stored the tab's choice; no horizontal overflow at 390. **Not checked:** dark mode, a screen reader, other widths, the live site.

**Follow-ups.** The server-rendered sidebar badges on a page with no `?team=` (the meeting page, Ask, Notifications) still count the default workspace; they follow the tab's workspace only on the team pages (pre-existing; a later slice may carry the tab's workspace in a cookie). `POST /api/meetings` born-in-workspace was not run live (needs the bot provider).
