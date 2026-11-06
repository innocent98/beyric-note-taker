# W9 Sprints, backend (PR 1)

**What shipped:** sprints for a team (and, optionally, per project): a current and a next sprint always exist, they close by themselves when their end date passes (or by hand), unfinished tasks carry over, and tasks are filed into a sprint by their due date. PR 1 of 2 (screens are PR 2). Branch `feature/w9-sprints-backend`, **stacked on `feature/w4-projects-backend`** (#100). Migration **0031**, `down_revision` 0028: whoever merges later re-points it (`alembic heads` must show one).

## Why
Spec 6.4 and decision D10 (build last, minimal first). The Tasks screen draws a sprint selector, a sprint header with progress and "Close sprint", a Backlog view and "Carried over"; Settings draws the Sprints card (D12); Projects draws "Sprint length", "In current sprint" and "In backlog". None had data until now.

## How, and the decisions
| Decision | Choice | Why / alternative rejected |
|---|---|---|
| Series | One per team, plus one per project that has its own length. Always exactly two open sprints (current = the earlier, next). Closed ones stay | The spec says "current and next materialised"; storing the role would need updates, ordering by `number` does not |
| Lengths | The designer's list: `week5`, `week7`, `week14`, `month1` (30 days), `month3`, `month6` (calendar months), `custom` (1 to 365). A project can use any but `custom` (its select has no Custom) | |
| Monday to Friday | `teams.sprint_align_weekdays` (the toggle "Open and close sprints at recurring meetings") only affects `week5`: it opens on Monday, ends Friday, next opens the next Monday. Spec ambiguity row 9: boundaries are dates, never meeting times | Off gives plain back-to-back 5-day blocks (my reading of the toggle; the designer's sample always draws Monday to Friday) |
| First sprint | Starts today; aligned `week5` starts on this week's Monday (next Monday on a weekend) | Weekend case means "current" can start in the future |
| Closing | Hourly scheduler job `rollover_sprints` (on the sync loop, with the sweeps) closes any sprint whose end date is before today in the team's zone; catches up several in one pass (cap 60). Early close: next starts today, **its end date does not change** (row 9); the closed sprint keeps its planned end date | Alternative: truncate the closed sprint's end to today. Rejected: the spec is silent, and a recorded `closed_early` already tells the story |
| Team's zone | The team owner's Settings time zone, else UTC (`teams.timezone` is not kept in sync anywhere) | |
| Carry over | Unfinished (`status != done`, not deleted) tasks move to the next sprint and get `carried`; done tasks stay in the closed sprint. A person placing a task clears `carried` | |
| Filing by due date (row 3) | none: Backlog; due on or before the current sprint's end (past due too): current; later: next. Used for AI tasks (new and refreshed, untouched ones only) and for a new task that names no sprint. `sprint_id: null` in a request is an explicit Backlog | A person's move is never undone by a re-run (`touched`) |
| Who plans a task | Same as W4 filing: owner/admin of the task's team, and the maker of a manual task. A member given a task cannot re-file it (Adebayo, 2026-11-06). Anyone on the team may create a task with a sprint valid for the team | |
| Per-project length | Setting it makes the project's own pair and moves its tasks from the team's current/next into it; clearing moves them back and drops the open project sprints; deleting the project does the same first (its closed sprints go with it, `ON DELETE CASCADE`). Moving a task to a project in another series keeps its place in time (`sprints.rehome`) | Heavier than a single series, kept because the designer's file draws it; this is the cut line if the team wants less (D10) |
| "All projects" board | `?sprint=current|next` and the team sprint's counts stand for every series' current/next | One selector for the whole team without a second list |
| Concurrency | One advisory transaction lock per team (`SPRINT_LOCK` 6020) around ensuring, closing and reflowing, so a request and the scheduler cannot make or close the same sprint twice | |
| Backfill | None. Existing tasks are in the Backlog; each team gets its sprints the first time the job or a board looks | |

## What's involved
| Area | Path |
|---|---|
| Migration | `alembic/versions/0031_sprints.py`: `sprints` (unique `(team_id, coalesce(project_id,0), number)`), `teams.sprint_length/sprint_custom_days/sprint_align_weekdays`, `projects.sprint_length`, `tasks.sprint_id` (SET NULL) and `carried`; check constraints on the lengths and the 1 to 365 days |
| Rules | `app/sprints.py` (windows, series, close, rollover, reflow, per-project length, `Placer`, `usable`, `rehome`, `sprint_filter`) |
| Routes | `app/api_sprints.py`: `GET /api/sprints?project=`, `POST /api/sprints/{id}/close`; edits in `app/api_tasks.py` (`?sprint=`, `sprint_id`, `carried`), `app/api_team.py` (`PATCH /api/team` sprint fields), `app/api_projects.py`, `app/api_schemas.py` |
| Services | `app/tasks.py` (create, update, reconcile, list, `check_sprint`), `app/projects.py` (length, stats, delete), `app/teams.py` (joining or leaving puts tasks in the Backlog), `app/sweeps.py` (job) |
| Docs and captures | `docs/fe-integration-guide-sprints.md`, `docs/fe-captures/sprints-w9/`, `scripts/capture_sprints_api.py`, `docs/openapi.json`, `web/lib/api/schema.d.ts` |

## Verification
- `tests/test_sprints.py` (24): window arithmetic for every length, month clamping, Monday alignment, series idempotence, rollover (boundary day, catch-up, twice, a failing team does not stop the others), early close, filing by date, reflow, per-project series and `rehome`, the sprint filter.
- `tests/test_sprints_api.py` (28): shapes, counts follow visibility, project boards, close (carry, `moved` follows visibility, 403, 404, 409), tasks (filter, create variants, other team's / closed / wrong-series / unknown ids, member given a task, maker of a task, rehoming), team settings, project length, delete, joining a team.
- Edited: `tests/test_accounts.py` (the IDOR guard lists the sprint id), `test_sweeps.py`, `test_migrations.py` (0031 defaults, constraints, round trip), `test_team.py`, `test_api_tasks.py`.
- Live captures 01 to 68 against the real app (see the FE guide's verification table for what was and was not live).
- Opus review of the permission checks: see below.

## Operate / roll back
- **Server steps before merge: none.** No new setting; the job runs in the existing scheduler. After deploy, the first hourly pass (or the first board load) makes each team's first two sprints.
- Roll back: `alembic downgrade 0028` drops the table and columns (tasks keep everything else). Re-point `down_revision` if #99/#104 merge first.

## Opus review
Opus, read-only, on commit 83521c4: **no Critical or High finding**. No cross-team read, create, move, close or count through any id; no 403-before-404 leak; no member path that moves a task between sprints; migration 0031 safe for production. Three smaller findings, all fixed with a test where testable:
1. Medium: a project given its own length again weeks later resumed a stale series in the past. Now the series starts at `max(next after the last closed sprint, first start for today)` (`test_a_project_that_gets_its_own_length_again_weeks_later...`).
2. Low/Medium: decisions read before the team lock. `set_project_length` and `rollover_team` now lock then refresh the project and team, `close_sprint` refreshes the team under the lock, and `tasks.create`/`update` take the lock before `check_sprint`.
3. Low: a refused PATCH could create sprints first. `rehome` now runs after all checks, just before the writes.

## Follow-ups
- PR 2: the screens (sprint selector, header, Close sprint, Backlog, Carried over, drawer select, Settings card, project select and stats).
- W8 (#104/#105) sprint line under "Next meeting" and its sprint rows: a small commit once they are in main.
- Existing tasks are not back-filled into sprints.
- No history view beyond the last 20 closed sprints.
