# Frontend integration guide: linking a recurring meeting to a project, and the Series page's sprint line

Everything the **Projects → Recurring meetings** checklist, the project list's **"N open tasks · M recurring meetings"** line and the **sprint line under "Next meeting"** on the Series page need. **Every payload, status code and error string below is pasted from a real response** captured by `scripts/capture_series_project_api.py` against the running app (real HTTP, sessions, CSRF, roles, database and migrations, the real sprint-making code, the real reminder mail builder). Files: `docs/fe-captures/series-project-link/`; run the script again to re-derive any of it. Names and addresses are placeholders; dates are the day of the capture. Shapes: `docs/openapi.json`. This guide is for behaviour.

## What it is, in your terms

* **A recurring meeting can be linked to one project.** Tasks taken from that meeting's notes are then filed into that project first (before keyword matching), without a "Check project" flag. Linking is the checklist on the project page: ticking a row links it, unticking unlinks it.
* **A recurring meeting is in at most one project.** Ticking a row that belongs to another project **moves** it; the row on the other project says **"Linked to {that project's name}"** (the designer's line).
* **The Series page** shows **"Opens Sprint N"** under "Next meeting" when that next meeting is the one that opens sprint N for you.

No new migration: `series.project_id` already existed. Deleting a project unlinks its recurring meetings.

## Who may link (the rule)

| Caller | Link / unlink |
|---|---|
| Owner or admin of the series' team | yes |
| Plain member (even the creator of the series) | **403 `team_forbidden`** |
| A recurring meeting the caller cannot see (a teammate's private recurring call), or another team's | **404 `not_found`**, as for reading it |
| A project that is not on the series' team | **404 `not_found`** |

This is the same rule as creating, changing and deleting a project, because the link decides where the whole team's tasks are filed. Every member can **read** the links (`project` on each card). FE: switch the rows off for a member (`can_manage: false` on `GET /api/projects`); show no pointer.

## The calls

### 1. Which recurring meetings exist and which project each is in: `GET /api/series` (existing call, new field)

`GET /api/series` as **ada** → **200**  
*capture `16-cards-linked.json`*  
`project` on each card: this is what the checklist ticks, and "Linked to {name}" is its name when ticked for another project.

```json
{
  "timezone": "UTC",
  "series": [
    {
      "id": 1,
      "title": "Beyric Monday planning",
      "cadence": "Weekly on Mondays, 14:00",
      "platform": "google_meet",
      "next_starts_at": "2026-11-09T14:00:00Z",
      "next_text": "Mon 9 Nov, 14:00",
      "unfinished_tasks": 0,
      "open_questions": 0,
      "project": {
        "id": 1,
        "name": "Client Portal"
      }
    },
    {
      "id": 3,
      "title": "Beyric Friday review",
      "cadence": "Weekly on Fridays, 14:00",
      "platform": "google_meet",
      "next_starts_at": "2026-11-13T14:00:00Z",
      "next_text": "Fri 13 Nov, 14:00",
      "unfinished_tasks": 0,
      "open_questions": 0,
      "project": {
        "id": 1,
        "name": "Client Portal"
      }
    }
  ]
}
```

`project` is `null` when not linked (capture `08-cards-before.json`). The checklist is this list: a row is ticked when `project.id` equals the open project's id, shows "Linked to {project.name}" when `project` is another project, and shows nothing on the right when `null`. **Only the recurring meetings the caller may see are in the list.**

### 2. Link, move or unlink: `PUT /api/series/{id}/project`

Body `{"project_id": <int>}` links or moves; `{"project_id": null}` unlinks. The field is **required** (an empty body is 422). The answer is the recurring meeting's card (same shape as above). The same call twice gives the same answer.

`PUT /api/series/1/project  body {"project_id": 1}` as **ada** → **200**  
*capture `13-link-planning.json`*  
Owner links Monday planning to Client Portal. The answer is the series' card.

```json
{
  "id": 1,
  "title": "Beyric Monday planning",
  "cadence": "Weekly on Mondays, 14:00",
  "platform": "google_meet",
  "next_starts_at": "2026-11-09T14:00:00Z",
  "next_text": "Mon 9 Nov, 14:00",
  "unfinished_tasks": 0,
  "open_questions": 0,
  "project": {
    "id": 1,
    "name": "Client Portal"
  }
}
```

`PUT /api/series/1/project  body {"project_id": 2}` as **ada** → **200**  
*capture `21-link-move.json`*  
Linking to another project MOVES the recurring meeting (a recurring meeting is in at most one project).

```json
{
  "id": 1,
  "title": "Beyric Monday planning",
  "cadence": "Weekly on Mondays, 14:00",
  "platform": "google_meet",
  "next_starts_at": "2026-11-09T14:00:00Z",
  "next_text": "Mon 9 Nov, 14:00",
  "unfinished_tasks": 0,
  "open_questions": 0,
  "project": {
    "id": 2,
    "name": "Beyric Note Taker"
  }
}
```

`PUT /api/series/1/project  body {"project_id": null}` as **ada** → **200**  
*capture `31-unlink.json`*  
Unlink: `project` null.

```json
{
  "id": 1,
  "title": "Beyric Monday planning",
  "cadence": "Weekly on Mondays, 14:00",
  "platform": "google_meet",
  "next_starts_at": "2026-11-09T14:00:00Z",
  "next_text": "Mon 9 Nov, 14:00",
  "unfinished_tasks": 0,
  "open_questions": 0,
  "project": null
}
```

Errors (exact strings):

`PUT /api/series/1/project  body {"project_id": 2}` as **dan** → **403**  
*capture `19-link-member.json`*  
A plain member may not link: 403 `team_forbidden`.

```json
{
  "error": {
    "code": "team_forbidden",
    "message": "You can't do that here. Ask an owner or admin of your team."
  }
}
```

`PUT /api/series/5/project  body {"project_id": 1}` as **ada** → **404**  
*capture `23-link-private-series.json`*  
Ada is the team owner but cannot see Dan's private 1:1: 404, as for reading it.

```json
{
  "error": {
    "code": "not_found",
    "message": "No such recurring meeting"
  }
}
```

`PUT /api/series/1/project  body {"project_id": 3}` as **ada** → **404**  
*capture `24-link-foreign-project.json`*  
A project of another team: 404 `not_found`.

```json
{
  "error": {
    "code": "not_found",
    "message": "No such project."
  }
}
```

`PUT /api/series/1/project  body {}` as **ada** → **422**  
*capture `28-link-bad-body.json`*  
`project_id` is required (null to unlink): 422.

```json
{
  "error": {
    "code": "validation_error",
    "message": "Some fields are missing or not valid.",
    "fields": [
      {
        "field": "project_id",
        "message": "Field required"
      }
    ]
  }
}
```

`PUT /api/series/1/project  body {"project_id": null}` as **ada** → **403**  
*capture `30-link-no-csrf.json`*  
No CSRF token: 403 `csrf_rejected` (the same rule as every write).

```json
{
  "error": {
    "code": "csrf_rejected",
    "message": "Missing or wrong X-CSRF-Token header. Read it from GET /api/session."
  }
}
```


Also captured: not logged in → 401 (`07-link-logged-out.json`); a project id that does not exist → 404 `No such project.` (`25`); a recurring meeting id that does not exist → 404 (`26`); a caller on another team → 404 (`27`); `project_id: 0` → 422 (`29`).

### 3. The project list: `GET /api/projects` (existing call, new field)

`GET /api/projects` as **ada** → **200**  
*capture `22-projects-moved.json`*  
Client Portal 1, Beyric Note Taker 1.

```json
{
  "projects": [
    {
      "id": 1,
      "name": "Client Portal",
      "color": "#0E6B5E",
      "keywords": [
        "portal",
        "checkout"
      ],
      "open_tasks": 0,
      "sprint_length": null,
      "in_current_sprint": 0,
      "in_backlog": 0,
      "recurring_meetings": 1,
      "milestone": null,
      "created_at": "2026-11-07T22:20:37.554432Z"
    },
    {
      "id": 2,
      "name": "Beyric Note Taker",
      "color": "#3C4A7A",
      "keywords": [],
      "open_tasks": 0,
      "sprint_length": null,
      "in_current_sprint": 0,
      "in_backlog": 0,
      "recurring_meetings": 1,
      "milestone": null,
      "created_at": "2026-11-07T22:20:37.688788Z"
    }
  ],
  "can_manage": true
}
```

`recurring_meetings` counts the recurring meetings linked to the project **that the caller may see**, i.e. the ticked rows the caller can see in the checklist. After a tick or untick the Projects screen recounts from the cards it holds; reloading this list gives the same numbers. Sub-line wording from the designer: `"{open_tasks} open tasks · {recurring_meetings} recurring meetings"`, with `meeting` when the count is 1.

Deleting a project unlinks its recurring meetings: capture `33-project-delete.json` then `34-cards-after-delete.json` (both cards `project: null`).

## The sprint line: `GET /api/series/{id}` (existing call, new field `sprint_note`)

`GET /api/series/1` as **ada** → **200**  
*capture `35-page-planning.json`*  
`sprint_note`: the next meeting (Monday 14:00 UTC) is the day sprint 2 starts and nothing of the series has happened since the sprint began: "Opens Sprint 2".

```json
{
  "id": 1,
  "title": "Beyric Monday planning",
  "cadence": "Weekly on Mondays, 14:00",
  "platform": "google_meet",
  "timezone": "UTC",
  "next_starts_at": "2026-11-09T14:00:00Z",
  "next_text": "Mon 9 Nov, 14:00",
  "sprint_note": "Opens Sprint 2",
  "can_rename": true,
  "agenda_recipients": 1,
  "<…omitted>": "unfinished, finished, questions, agenda, past_meetings, attendees: unchanged, see fe-integration-guide-series.md"
}
```

`GET /api/series/3` as **ada** → **200**  
*capture `37-page-review.json`*  
The next meeting is the Friday: no sprint starts that day, so `sprint_note` is null. There is no rule for "Closes Sprint N" yet, so it is never sent.

```json
{
  "id": 3,
  "title": "Beyric Friday review",
  "cadence": "Weekly on Fridays, 14:00",
  "platform": "google_meet",
  "timezone": "UTC",
  "next_starts_at": "2026-11-13T14:00:00Z",
  "next_text": "Fri 13 Nov, 14:00",
  "sprint_note": null,
  "can_rename": true,
  "agenda_recipients": 1,
  "<…omitted>": "unfinished, finished, questions, agenda, past_meetings, attendees: unchanged, see fe-integration-guide-series.md"
}
```


* `sprint_note` is `null` (draw nothing) or the exact text to draw, **"Opens Sprint N"**. It is only ever sent when `next_text` is not null.
* **The rule is the one-hour reminder mail's** (`mail_summary.opening_sprint`): a sprint opens on its start date (or, for a sprint that starts on a weekend, the first working day after the previous sprint's end), the next meeting must fall on that day **in the caller's time zone**, and no meeting of the series may have happened (or be on the caller's calendar) since the sprint began. Capture `39-reminder-1h-rendered.json` is the mail for the same occurrence; its meta line says "Sprint 2 opens" where the page says "Opens Sprint 2":

```json
[
  {
    "to": "ada@example.test",
    "subject": "Starts in 1 hour: Beyric Monday planning",
    "text": "Starts in 1 hour\n\nBeyric Monday planning\n\nMon 9 Nov, 14:00 to 14:45 UTC · Google Meet · Sprint 2 opens\n<… omitted>"
  }
]
```

* Teams with no sprint rows yet (they are made the first time a task is filed or the board is opened) get `null`.
* **"Closes Sprint N" is never sent.** The designer draws it on the Friday review, but nothing in the app decides which meeting closes a sprint (sprints close when their end date passes, not at a meeting), so there is no rule to reuse; the Friday capture above is `null`. FE: nothing to draw until that is decided.

## UX consequences the FE must surface

* **A tick can move a recurring meeting out of another project.** The row says "Linked to {other}" before the tick, so the person sees it; after the tick the other project's count drops by one.
* **Linking changes future filing only.** Tasks already filed stay where they are.
* **A member sees ticks but cannot change them.** Do not show a control that answers 403.
* **The sprint line is per viewer.** Two teammates may see different lines for the same series (the rule looks at the meetings and calendar events each may see), exactly like the reminder mail.

## Verification

| Behaviour | Verified |
|---|---|
| Link, link again, move, unlink, answers and counts (captures 13 to 22, 31) | live |
| Member 403, private series 404, foreign project 404, missing ids 404, bad bodies 422, no CSRF 403, logged out 401 | live |
| Deleting a project unlinks its recurring meetings (33, 34) | live |
| `sprint_note` "Opens Sprint 2" for owner and teammate (35, 36), null for the Friday review (37) | live |
| The reminder mail says the same for the same occurrence (38, 39) | live (mail provider replaced by a capturing mailer) |
| Tasks filed into the linked project by the real extraction run | **NOT LIVE**: needs the model; covered by `tests/test_projects_filing.py` (`test_a_meeting_of_a_linked_series_files_every_task_into_that_project`) |
| A stray `project_id` (another team's, or deleted) is treated as no link | **NOT LIVE**: cannot be made through the API; covered by `tests/test_projects_filing.py` and `tests/test_series_project_link.py` |
