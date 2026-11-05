# SOP: W4 Projects and milestones, backend (PR 1 of 2)

## What shipped
`projects` and `milestones` tables, project and milestone routes, filing of a task into a project by keywords (with the "Check project" flag), milestone proposals from the extraction call (never auto-created, confirmed by an owner or admin), and project and milestone filters on the Tasks API. Backend only; the screens are PR 2 (`feature/w4-projects-screens`). Migration **0028** (0027 is Outlook M1, #96). Branch `feature/w4-projects-backend`. PR: see the PR page (not merged).

## Why
Spec 4, 6.2, 6.3, 8.2 and decision D14. W2 made tasks without a place to file them, and the model sees dates ("Launch is December 8") that a person must be the one to turn into a milestone.

## How: the decisions
| Topic | Decision | Alternatives rejected |
|---|---|---|
| Filing | `app/filing.py`, code only. Score = how many of a project's keywords appear as whole words/phrases (case and accents ignored) in meeting title + task title + quote; a keyword counts once. One project in the team: filed there, unflagged. No hit and several projects: none. One clear winner with two or more hits: confident. One hit or a tie: best (oldest of a tie) and `check_project` | Counting every mention (one repeated word would beat two real keywords); asking the model |
| Series seam | `filing.series_project(session, meeting)` returns `None` until W8; `file_task(series_project_id=...)` already gives it priority. W8 changes that one function | A guess at the series table now |
| Where filing runs | `tasks.reconcile`, for every task it creates or refreshes. A task a person touched (`edited_at`) is never refiled, so choosing a project survives a re-run | Filing in the API only |
| Roles | everyone on the team reads; owner and admin write (403 `team_forbidden`); another team's id is 404 before the role is checked. A member may change a task's project, milestone and check flag when they may change its status (assignee, maker) | Admin-only task filing (a member could not confirm their own task) |
| Milestones | proposal = row with `confirmed_at` null and the quote. `propose` runs where the tasks are refined, after the checks (real date, quote in the transcript); at most 5 per meeting; a rewrite replaces the unconfirmed ones and never repeats a confirmed one; it is filed to a project by the same keyword rule. Confirm is `POST /api/milestones/{id}/confirm` (idempotent, optional `project_id`). Only confirmed milestones appear in `GET /api/milestones` or can be given to a task | Auto-create (a spoken date can be a guess, D14) |
| Privacy of the words | a proposal belongs to its meeting: `_milestone_or_404` gives 404 to anyone who cannot read the source meeting, even an admin, so ids cannot be used to read a date out of a private meeting. A confirmed milestone's `quote` and `source_meeting` are sent only to people who can read that meeting | Sending the quote team-wide |
| Dismissing | `DELETE` removes a proposal; a rewrite could propose it again (no screen draws a dismiss) | A tombstone column |
| Deleting a project | tasks keep, `project_id` null, `check_project` cleared; its milestones are deleted (`ON DELETE CASCADE`); tasks keep `milestone_id` null | Keeping milestones with no project (a milestone on no tab) |
| Meeting delete | `services.delete_meeting` removes that meeting's proposals; a confirmed milestone stays and `source_meeting_id` is nulled (listed in `KEPT` of the cascade test) | Cascading (would delete a confirmed milestone) |
| Leaving a team | tasks that move with the leaver lose `project_id`, `milestone_id`, `check_project`; unconfirmed proposals from their meetings are deleted. Joining a team moves projects and milestones with the dissolved team of one | Leaving dangling ids that name another team's project |
| Not stored yet | `sprint_length` and `series_ids` (spec 6.3) wait for W9 and W8, as W2 did for sprint columns | Columns nothing can set |
| Stats | the project carries `open_tasks`; "In current sprint" and "In backlog" need W9 and are not sent | Inventing sprint numbers |

## What's involved
- `alembic/versions/0028_projects_milestones.py`: `projects`, `milestones`, `tasks.project_id/milestone_id/check_project` (+ indexes). Down revision **0026**; re-point to 0027 if #96 merges first.
- New: `app/filing.py`, `app/projects.py`, `app/api_projects.py`, `scripts/capture_projects_api.py`, `docs/fe-integration-guide-projects.md`, `docs/fe-captures/projects-w4/`.
- Changed: `app/models.py`, `app/tasks.py` (`reconcile`, `list_tasks`, `attention`, `create`, `update`, `check_assignable`), `app/teams.py` (`move_team_content`, `leave`), `app/workspace.py`, `app/services.py`, `app/api.py`, `app/api_tasks.py`, `app/api_schemas.py`, `docs/openapi.json`, `web/lib/api/schema.d.ts`.
- Tests: `tests/test_projects_filing.py`, `tests/test_projects_api.py` (new), `tests/test_accounts.py` (IDOR guard knows project and milestone ids), `tests/test_team_visibility.py` (meeting milestones route classified), `tests/test_meeting_delete.py`, `tests/test_migrations.py`, `tests/test_api_tasks.py`.

## Verification
- Fast checks local (own Postgres 54333): `ruff check .`, `ruff format --check .`, pytest on the files above plus `tests/test_accounts.py`, `tests/test_api.py`, OpenAPI tests; `export_openapi.py --check`, `npm run gen:api`.
- Route enumeration: `test_every_new_route_is_classified` lists all 11 routes; the IDOR guard calls each id route as a stranger (404); members get 403 on every write; a teammate sees a shared meeting's proposal and 404 on a private one.
- Live capture: `docs/fe-captures/projects-w4/` (56 exchanges), see the FE guide's verification table. The model answer in the capture is scripted.

## Operate / roll back
- **Server steps before merge:** none. No new environment variables. Check `alembic heads` shows one head after 0027 (#96) lands.
- **Deploy:** the migration only adds tables and nullable columns. Nothing is backfilled: existing tasks keep no project.
- **Roll back:** `alembic downgrade 0026` drops both tables and the three task columns (project data is lost; tasks are unchanged).

## Follow-ups
- PR 2: screens (Projects, project tabs, milestone card, drawer fields, "Set milestone" on the meeting page).
- W8: `series_project` and the Recurring meetings list; W9: sprint length and the two sprint stats.
- A milestone-quality eval row in the AI1 eval (the model's milestones are only checked by code here).
