# FE integration guide: Projects and milestones (W4 backend)

Every body below is copied from `docs/fe-captures/projects-w4/NN-*.json`, captured against the running app by `scripts/capture_projects_api.py` (real HTTP, real database, real task filing and proposal code). Re-run the script to re-derive any of it. Names, emails and ids are placeholders. Anything not exercised live is marked **NOT LIVE** with where its shape came from.

All routes are under `/api`, need the session cookie, and take `X-CSRF-Token` on writes (same as the other routes). Errors have the usual shape `{"error": {"code", "message", "fields"?}}`.

## What this is, in the FE's terms
A **project** is a named, coloured group of tasks that the team files tasks into by **keywords**. A **milestone** is a dated goal ("Client Portal launch, 8 Dec"). The model never makes a milestone by itself: when a meeting states a date it leaves a **proposal** on that meeting, and an owner or admin confirms it. Tasks get a project when they are read from a meeting (by keywords, no AI) or when someone picks one.

Who may do what: **everyone on the team reads** projects and milestones; **owner and admin write** (`403 team_forbidden` for a member, capture 22 to 24, 42). A project or milestone of another team is **404 `not_found`** (captures 27 to 29, 43), never 403. Counts inside a project or milestone only count tasks the caller may see (a task from someone's private meeting is not counted for others).

## Endpoints

### Projects
| Call | Capture | Notes |
|---|---|---|
| `GET /api/projects` | 09, 13, 21 | `{projects: [...], can_manage}`, oldest first. `can_manage` false means hide create, edit and delete controls |
| `POST /api/projects` | 10 to 12 | body all optional: `{name?, color?, keywords?}`. 201 with the project. A blank body is "New project": `name` is `""` |
| `GET /api/projects/{id}` | 20, 51 | one project |
| `PATCH /api/projects/{id}` | 14 to 19, 25 | partial; send one field per request (autosave). `keywords` replaces the whole list |
| `DELETE /api/projects/{id}` | 54 | 204. Its tasks stay with `project_id: null`; its milestones are deleted with it |

Project (capture 13):
```json
{"id": 2, "name": "Client Portal", "color": "#3C4A7A", "keywords": ["portal", "checkout"],
 "open_tasks": 0, "milestone": null, "created_at": "2026-11-05T22:00:02.313693Z"}
```
`milestone` is `{"id": 1, "name": "Client Portal launch", "due_date": "2026-12-08"}` (capture 51): the next confirmed milestone still ahead, else the latest that passed, else `null` ("None yet"). `name` may be `""` until typed: show "Untitled project". The six colours are `#0E6B5E #3C4A7A #8A4B00 #8A2E5C #1F6FB2 #4A524E`; any `#RRGGBB` is accepted. When `color` is left out on create, the next swatch in that order is used.

Keywords: trimmed, one line, duplicates in any case dropped (capture 16: `["Scratch", "scratch", " tmp "]` became `["Scratch", "tmp"]`), at most 20, each at most 40 characters.

Errors seen live: `422 keyword_too_long` "A keyword can be at most 40 characters." (17); `422 validation_error` for a bad colour (`fields: [{"field": "color", "message": "String should match pattern '^#[0-9A-Fa-f]{6}$'"}]`, 18), for `"name": null` (19), and for more than 20 keywords (NOT LIVE: covered by `tests/test_projects_api.py`; same shape as 18).

### Milestones
| Call | Capture | Notes |
|---|---|---|
| `GET /api/milestones?project_id=` | 39, 46, 50 | confirmed milestones only, soonest first. Proposals are never listed here |
| `GET /api/meetings/{id}/milestones` | 38, 40, 41 | what the meeting page shows: proposals and confirmed ones that came from this meeting. Anyone who can read the meeting may ask (404 otherwise, capture 41) |
| `POST /api/milestones/{id}/confirm` | 44, 45 | owner/admin. Body optional `{project_id?}`; idempotent (45 returns the same `confirmed_at`) |
| `POST /api/milestones` | 52 | by hand, owner/admin, confirmed at once: `{name, due_date, project_id?}`. **No screen draws this**; it exists for the API |
| `PATCH /api/milestones/{id}` | 53 | `{name?, due_date?, project_id?}`, owner/admin |
| `DELETE /api/milestones/{id}` | NOT LIVE | 204, owner/admin; turns a proposal down or removes a milestone, its tasks keep `milestone_id: null`. Covered by `tests/test_projects_api.py` only |

Milestone, a proposal (capture 38) and after Confirm (capture 44):
```json
{"id": 1, "name": "Client Portal launch", "due_date": "2026-12-08", "project_id": 2,
 "source_meeting": {"id": 1, "title": "Portal planning", "platform": "google_meet", "created_at": "2026-11-03T10:00:00Z"},
 "quote": "Launch is December 8.", "confirmed_at": null, "task_count": 0, "done_count": 0,
 "can_confirm": true, "can_edit": true}
```
After Confirm: `confirmed_at` is `"2026-11-05T22:00:02.625399Z"` and `can_confirm` is `false`. Capture 50 shows `"task_count": 1, "done_count": 0` once a task is filed under it (the milestone card's "x of y launch tasks done"). `quote` and `source_meeting` are `null` for someone who cannot read the source meeting (a confirmed milestone is team-wide; the words said are not), and a **proposal** from a meeting the caller cannot read is `404 not_found` on every route, for any role (NOT LIVE: covered by `tests/test_projects_api.py`). `days to go` is not sent: compute it from `due_date` in the user's zone.

## Tasks (what changed on the existing routes)
Every task now carries `project_id`, `milestone_id` (ids, `null` for none) and `check_project` (capture 31). `GET /api/tasks` adds filters `?project=<id>|none` and `?milestone=<id>` (bad values `422 validation_error`, capture 33) and `attention.check_project`, the count for the banner "1 needs its project checked" (capture 30: `{"needs_owner": 0, "unverified": 0, "check_project": 1}`, open tasks only). `POST /api/tasks` takes an optional `project_id` (file a new task under the open project tab). `PATCH /api/tasks/{id}` takes `project_id`, `milestone_id`, `check_project`:
- choosing a project clears `check_project` (capture 36); `{"check_project": false}` is the Confirm button on the "Check project" line (34); `true` is refused, `422 validation_error` "check_project can only be set to false." (35);
- a project of another team: `422 project_not_on_team` "That project is not on your team." (37); a milestone of another team or a **proposal**: `422 milestone_not_on_team` (NOT LIVE, covered by tests);
- who may: the same people who may change a task's status (owner/admin, the assignee, the maker of a manual task); others get `403 team_forbidden`;
- the project dot and name come from joining `project_id` to `GET /api/projects`; fetch projects once per screen.

### How a task is filed (so the FE can explain "Check project")
Keywords are matched as whole words in the meeting title, the task title and the quote. One clear winner with two or more keywords is filed quietly (captures 30: tasks 1 and 2). One keyword, or a tie, files to the best match and sets `check_project` (task 3 in capture 30). A team with a single project files everything there, unflagged. No match, no project. Linking a recurring meeting to a project is W8 and is not available.

## UX consequences to surface
- A task can show **"Check project"** and a project that is only a guess: show the badge and the drawer line with a Confirm button, and count it in the banner.
- **Deleting a project** changes every task in it to "no project" and deletes its milestones: the Projects screen says "Its tasks now have no project."
- A **member** sees projects but not the controls (`can_manage` false). A member who owns a meeting sees its milestone proposal but `can_confirm` is false: do not draw a Confirm button for them.
- `open_tasks` and milestone progress follow what the viewer may see, so two teammates can see different numbers.

## State and polling
Proposals appear after the meeting's extraction finishes (`extraction_status` on the meeting): fetch `GET /api/meetings/{id}/milestones` with the meeting, and again when `extraction_status` turns `done`. No other polling.

## Verification table
| Claim | Status |
|---|---|
| Project create, patch (each field), get, list, delete; errors 17 to 19 | verified live (captures 09 to 20, 54, 56) |
| Member read OK, write 403; outsider 404 on every id | verified live (21 to 29, 42, 43) |
| Task filing by keyword, `check_project` flag, `attention.check_project` | verified live (30, 31) through the real extraction run with a **scripted model answer** (the model is not called) |
| Milestone proposal stored, listed on the meeting, confirm, confirm again | verified live (38 to 45) with the same scripted answer |
| Milestone progress counts, project headline milestone | verified live (50, 51) |
| Task patch: project, milestone, confirm check, bad project | verified live (34 to 37, 48) |
| Delete project leaves tasks without project | verified live (54, 55) |
| `DELETE /api/milestones/{id}`, `milestone_not_on_team`, `too_many_keywords`, a rewrite replacing proposals, a tie or one-hit match flagged in unit form | NOT LIVE: covered by `tests/test_projects_api.py` and `tests/test_projects_filing.py` |
| Real model output for milestones | NOT LIVE: needs a real call; the quality gate for it is the AI1 eval (spec 3.1) |
