# FE integration guide: Sprints (W9 backend)

Every body below is copied from `docs/fe-captures/sprints-w9/NN-*.json`, captured against the running app by `scripts/capture_sprints_api.py` (real HTTP, real database, real task filing, the real hourly rollover function). Re-run the script to re-derive any of it. Names, emails and passwords in the captures are placeholders. **Dates in the captures are the day they were captured (2026-11-06, a Friday)**: your own run will show other dates, the shapes are what matter. Long task bodies are trimmed to the sprint fields where marked; no field is invented.

All routes are under `/api`, need the session cookie, and take `X-CSRF-Token` on writes. Errors have the usual shape `{"error": {"code", "message", "fields"?}}`.

## What this is, in the FE's terms
A **sprint** is a dated window of work. A team has a **current** sprint and a **next** sprint at all times (the board never shows others except **closed** ones). A sprint ends by itself when its end date passes in the team's time zone; "Close sprint" ends the current one now. Either way the **unfinished tasks move to the next sprint** and get `carried: true` ("Carried over"). A task with `sprint_id: null` is in the **Backlog**.

Which sprint a new task lands in is decided by its due date (no date: Backlog; a date inside or before the current sprint: current; later: next), unless the request names a sprint.

A project may have its **own sprint length** ("Sprint length" on the Projects screen). Then it has its own pair of sprints (`own_series: true`) and its tasks can only be planned into those.

Who may do what: **everyone on the team reads** sprints. **Owner and admin** close a sprint, change the team's sprint settings and a project's length (`403 team_forbidden` for a member, captures 40, 47, 57). **Planning a task into a sprint** is filing: owner, admin, and the maker of a manual task only. A member who was only *given* a task can tick it but cannot move it between sprints (capture 31 is the 403; Adebayo confirmed this rule 2026-11-06). Anyone may create a task with a sprint. A sprint of another team is **404** on the sprint routes (capture 58) and **422 `sprint_not_on_team`** inside a task body (captures 27, 37).

## Endpoints
| Call | Captures | Notes |
|---|---|---|
| `GET /api/sprints?project=` | 09, 11 to 13, 45, 50, 51, 63, 65 | the board's sprint list; `project` is a project id, left out is "All projects" |
| `POST /api/sprints/{id}/close` | 57 to 62 | owner/admin; only the current sprint |
| `GET /api/tasks?sprint=` | 18 to 22 | `none` (Backlog), `current`, `next` or a sprint id |
| `POST /api/tasks` with `sprint_id` | 23 to 28, 33 | optional; see below |
| `PATCH /api/tasks/{id}` with `sprint_id` | 31, 32, 34 to 38, 52 to 54 | owner/admin or maker; `null` is the Backlog |
| `GET` / `PATCH /api/team` | 10, 40 to 46 | `sprint_length`, `sprint_custom_days`, `sprint_align_weekdays` (Settings > Sprints) |
| `PATCH /api/projects/{id}` with `sprint_length` | 47 to 49, 55, 56 | a project's own length; `null` follows the team's |
| `GET /api/projects` | 16 | now carries `sprint_length`, `in_current_sprint`, `in_backlog` |

### GET /api/sprints
Capture 09, the first call for a team (it makes the first two sprints):
```json
{
 "project_id": null,
 "own_series": false,
 "today": "2026-11-06",
 "sprint_length": "week5",
 "sprint_custom_days": 10,
 "sprint_align_weekdays": true,
 "sprints": [
  {
   "id": 1,
   "number": 1,
   "name": "Sprint 1",
   "start_date": "2026-11-02",
   "end_date": "2026-11-06",
   "state": "current",
   "closed_at": null,
   "closed_early": false,
   "tasks_total": 0,
   "tasks_done": 0
  },
  {
   "id": 2,
   "number": 2,
   "name": "Sprint 2",
   "start_date": "2026-11-09",
   "end_date": "2026-11-13",
   "state": "next",
   "closed_at": null,
   "closed_early": false,
   "tasks_total": 0,
   "tasks_done": 0
  }
 ],
 "current_id": 1,
 "next_id": 2,
 "can_manage": true
}
```
- `sprints` is ordered: up to 20 **closed** ones (oldest first), then the **current**, then the **next**. `state` says which. `current_id` and `next_id` repeat the last two.
- With **no `project`** it is the team's pair and the counts cover every project (and the sprint of a project with its own length that is in the same place, so "All projects" shows everything planned for "now").
- With `project=<id>`: if that project has its own length, `own_series` is `true` and you get its own pair (capture 50, `project_id` 2, `sprint_length` `week14`); otherwise you get the team's sprints counting **only that project's tasks** (capture 51, `own_series` false).
- `tasks_total` / `tasks_done` are what the caller may see ("3 of 8 done"). Two teammates can see different counts.
- `today` is today in the **viewer's** time zone. Work out "4 days left" (`end_date - today`, 0 is "Last day"), "Starts Mon 9 Nov" (`start_date > today`) and the Closed label from it. The server does not send those strings.
- `sprint_length` is the length this list runs on: the team's, or the project's own. `can_manage` false means hide Close sprint.
- The Backlog card "Next sprint" is `next_id`; "Backlog" is `?sprint=none`.

A closed sprint (capture 63, trimmed to that row):
```json
{"id": 1, "number": 1, "name": "Sprint 1", "start_date": "2026-11-05", "end_date": "2026-11-10", "state": "closed", "closed_at": "2026-11-06T16:00:01.276190Z", "closed_early": true, "tasks_total": 0, "tasks_done": 0}
```
`closed_early` is true when it was closed by hand before its end date, false when it ended by itself or was closed on its last day.

### POST /api/sprints/{id}/close
Capture 61 (owner, closed early; the sprint's dates had been moved in the database first, so that today fell inside it: a labelled set-up step):
```json
{
 "closed": {
  "id": 1,
  "number": 1,
  "name": "Sprint 1",
  "start_date": "2026-11-05",
  "end_date": "2026-11-10",
  "state": "closed",
  "closed_at": "2026-11-06T16:00:01.276190Z",
  "closed_early": true,
  "tasks_total": 0,
  "tasks_done": 0
 },
 "current": {
  "id": 2,
  "number": 2,
  "name": "Sprint 2",
  "start_date": "2026-11-06",
  "end_date": "2026-11-13",
  "state": "current",
  "closed_at": null,
  "closed_early": false,
  "tasks_total": 6,
  "tasks_done": 0
 },
 "moved": 3
}
```
`moved` counts the unfinished tasks **the caller may see**; use it for "3 unfinished tasks moved to Sprint 13". `current` is the sprint that is now current (its `start_date` is today when closing early; its end date does not change). Other errors:
- member: `403 team_forbidden` "Only an owner or admin of the team can do that." (57)
- another team's id or one that does not exist: `404 not_found` "No such sprint." (58, 60)
- the next sprint, or one already closed: `409 sprint_not_current` "Only the current sprint can be closed." (59, 62)

Closing is not undoable. Ask for confirmation in the UI if the design does.

### Tasks
Every task now has `sprint_id` (null is the Backlog) and `carried`. Capture 23 (trimmed to the sprint fields; the rest is unchanged from the tasks guide):
```json
{"id": 4, "due_date": "2099-01-01", "project_id": null, "sprint_id": 2, "carried": false, "can_edit": true}
```
**Creating a task** (`POST /api/tasks`):
| Body | Result | Capture |
|---|---|---|
| `{"title", "due_date": "2099-01-01"}` (no `sprint_id`) | `sprint_id` = the next sprint (date after the current sprint) | 23 |
| `{"title"}` | `sprint_id: null` | 24 |
| `{"title", "sprint_id": <current>, "due_date": ...}` | that sprint, whatever the date | 25 |
| `{"title", "sprint_id": null, "due_date": "2000-01-01"}` | `null`: an explicit null is the Backlog | 26 |

**Field-nesting trap:** *leaving `sprint_id` out* and *sending `sprint_id: null`* are different. Out means "file it by the due date"; `null` means Backlog. The Backlog view's "New task" must send `null`; the Board's "New task" must send the sprint being shown.

`sprint_id` must be an **open** sprint of the caller's team, in the series of the task's project. Anything else, including another team's sprint, a closed one, one that does not exist and the team's sprint for a project with its own length, is the same answer:
```json
{
 "error": {
  "code": "sprint_not_on_team",
  "message": "That sprint is not available for this task."
 }
}
```
and `sprint_id: 0` or below is `422 validation_error` with `fields: [{"field": "sprint_id", "message": "Input should be greater than or equal to 1"}]` (28).

**Planning an existing task** (`PATCH`): owner and admin may (34, 35, 36, 38); the maker of a manual task may; a member given the task gets `403 team_forbidden` "You can't do that to this task. Ask an owner or admin of your team." (31) and nothing changes. Ticking the task (`status`) stays allowed for them (30). A person placing a task clears `carried`. A request that changes nothing (a member sending `sprint_id: null` for a task already in the Backlog) is 200 and does nothing (32). Use `can_edit` on the task to decide whether to draw the Sprint select as editable: `can_edit` true is exactly who may plan.

**Moving a task to a project with its own length** keeps its place in time: current becomes the other series' current, next the next, Backlog stays (capture 54: a task in the next sprint stays in the next sprint). Do not send `sprint_id` in the same request unless you mean it.

**Filtering** (`GET /api/tasks?sprint=`): `none`, `current`, `next` or an id; anything else is `422 validation_error` (22). `current` and `next` stand for every series' current and next sprint, so the "All projects" board is `?sprint=current`. A sprint id of another team returns an empty list (21).

### Settings: Sprints card
`GET /api/team` carries (capture 10):
```json
{
 "sprint_length": "week5",
 "sprint_custom_days": 10,
 "sprint_align_weekdays": true,
 "can_manage": true
}
```
`sprint_length` is one of `week5`, `week7`, `week14`, `month1`, `month3`, `month6`, `custom`: the designer's list ("1 week, 5 working days", "1 week, 7 days", "2 weeks", "1 month, 30 days", "3 months", "6 months", "Custom"). `sprint_custom_days` is 1 to 365 and only matters for `custom` (it is kept when you switch away). `sprint_align_weekdays` is the "Open and close sprints at recurring meetings" switch, which the spec reads as **Monday to Friday**: it only has an effect for `week5`.

`PATCH /api/team` (owner/admin) takes any of the three; send one per change. An admin changing the length (capture 44) redraws the open sprints at once: the current sprint keeps its start day and gets the new end; the next follows (capture 45). Closed sprints and the tasks are untouched. A new length can put the current sprint's end in the past: it then closes at the next hourly pass and its unfinished tasks carry over.

Errors: `403 team_forbidden` for a member (40); `422 validation_error` for an unknown length (41) or `sprint_custom_days` outside 1 to 365 (42); and
```json
{
 "error": {
  "code": "align_needs_week5",
  "message": "Only available for 1 week, 5 working day sprints."
 }
}
```
when `sprint_align_weekdays: true` is sent for any length but `week5` (the design flashes the same sentence). Nothing is changed in that case.

### Projects: Sprint length and the two stats
`GET /api/projects` (capture 16) now has, per project, `sprint_length` (`null` follows the team's), `in_current_sprint` and `in_backlog` (open tasks the caller may see). The select on the Project screen has the same options as Settings **except `custom`** (`422 validation_error` for it, 48), headed "Workspace default (…)" for `null`. `PATCH /api/projects/{id}` with `{"sprint_length": "week14"}` makes the project's own sprints and moves its tasks that were in the team's current and next sprint into them (49, then 50); `{"sprint_length": null}` is allowed and moves them back (55). A member gets 403 (47). **`null` here is meaningful**: the other project fields refuse null.

## UX consequences to surface
- **Closing a sprint moves tasks.** Say how many (`moved`): "Sprint 12 closed. 3 unfinished tasks moved to Sprint 13".
- **A sprint can close while the page is open** (the hourly job). Refetch `GET /api/sprints` when the tab regains focus; a Close on a sprint that already closed is `409 sprint_not_current`: refetch and say nothing alarming.
- **Changing the team's length or a project's length moves dates.** The Settings card's preview line ("Current sprint: Sprint 12, Mon 2 Nov to Fri 6 Nov") must be recomputed from the new `GET /api/sprints` after a PATCH.
- **Giving a project its own length moves its tasks** into a new pair of sprints; taking it back moves them again. Worth a sentence next to the select.
- **Deleting a project that has its own length** keeps its tasks and puts them in the team's sprints (capture 67). Its closed sprints are deleted with it.
- **An early-closed sprint keeps its planned end date**, so its range can run past the day the next sprint started (capture 61: Sprint 1 ends 2026-11-10, Sprint 2 starts 2026-11-06). Show each sprint's own range; do not try to draw them end to end.
- **Joining another team** puts every task you bring in the new team's Backlog.
- **A member can be refused planning** a task assigned to them: draw the Sprint select read-only when `can_edit` is false, do not offer it and then fail.
- **A new team has no sprints until someone asks:** the first `GET /api/sprints` makes them (capture 09), and the hourly job makes them for every team. Do not treat an empty list as a state.
- Weekend: on a Saturday or Sunday the first aligned (Monday to Friday) sprint of a brand new team starts **next Monday**, so "current" can have `start_date > today` ("Starts Mon 9 Nov").

## State and polling
There is no per-sprint state to poll: `state` is `closed`, `current` or `next`. Refetch `GET /api/sprints` after: a Close, a settings change, a project length change, planning a task (for the progress counts), and on tab focus. No push; no polling timer needed.

## Verification table
| Claim | Status |
|---|---|
| First call makes current and next; lists; project board with and without its own series; outsider's own sprints | verified live (09, 11, 45, 50, 51, 63) |
| Close: owner, member 403, outsider 404, next 409, again 409, missing 404, `moved`, carried tasks | verified live (57 to 64) |
| Early close sets `closed_early` and the next starts today | verified live (61, 63), after a labelled set-up that moved the sprint's dates in the database so today fell inside it |
| Filing by due date: AI task with and without a date; manual create variants | verified live (17, 23 to 26) through the real extraction run with a **scripted model answer** (the model is not called) |
| Planning a task: owner, admin, member given a task 403, other team's sprint 422, outsider 404, wrong series 422 | verified live (31 to 39, 53) |
| Task filter `sprint=` | verified live (18 to 22) |
| Team settings: member 403, bad values, align rule, admin change redraws sprints | verified live (40 to 46) |
| Project own length: set, 403, `custom` refused, own series, move task across series, clear, set again, delete | verified live (47 to 56, 67, 68) |
| The hourly rollover closing ended sprints and carrying tasks | verified live (65, 66) by calling the real `sprints.rollover_team` with a clock 40 days ahead, not by waiting for the scheduler process; the scheduler registration is covered by `tests/test_sweeps.py` only |
| The hourly job in the running scheduler process, in production | NOT LIVE: needs the deployed scheduler; watch the first Monday after merge |
| `month1`, `month3`, `month6`, `week7` window arithmetic | unit tests only (`tests/test_sprints.py`); the captures use `week5`, `week14` and `custom` |
| Team time zone for "ended": the team owner's Settings zone | unit-tested with UTC only; no capture of a non-UTC team |
