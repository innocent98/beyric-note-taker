# Frontend guide: Suggested tasks and "Ask before adding unclear tasks"

**Module:** Tasks (spec `docs/superpowers/specs/2026-11-08-designer-v2-features-design.md`, section 2; audit rows B12, B13, B21 to B24). **Backend:** migration 0035, extraction `extract-v2`, `POST /api/tasks/{id}/accept`, `GET /api/tasks?suggested=`, `ask_before_unclear` in the Tasks settings.

**Every payload, status code and error string below is copied from `docs/fe-captures/suggested-tasks/`**, captured over real HTTP by `scripts/capture_suggested_tasks_api.py` against the running app (real migrations, auth, CSRF, roles, visibility, the worker's extraction step, the mail builder). The one thing that is **not real** is the model: this session had no OpenAI key, so the model's answer to the extraction is a stand-in written for the capture (it says which items are unclear and in what words). The FE can rely on the *shape* and on everything done with the answer; the real model's choices and wording come with the first live meeting. Names, addresses and passwords are placeholders (`<password placeholder>` in the files). The file number is given with each block as `[capture NN]`.

## 1. What it is, in the screen's terms

When a meeting ends, the AI reads its action items. A clear commitment ("I'll finish the checkout page before Friday") becomes a task as before. An item it is unsure about (vague, conditional, nobody took it on) waits as a **suggested task** until a person **accepts** it (it joins the board) or **dismisses** it (it is gone, and the same sentence is not suggested again). This happens only when the person who owns the meeting has **"Ask before adding unclear tasks"** on (it is on by default, Settings > Tasks).

A suggested task is **not a task yet**: the server leaves it out of the Board and Backlog lists, every count, the sidebar badge and every mail, so the FE must **not add it back** anywhere (section 7). It appears in three places only: the Tasks screen's **Suggested tasks** section, the **meeting's Action items row** (pill "To confirm") and the **task drawer** (amber box).

## 2. What changed, at a glance

| Call | Change |
|---|---|
| `GET /api/tasks` | **Default leaves suggested tasks out.** New query `suggested=true` lists only suggested tasks (the Suggested tasks section) |
| `TaskOut` (every endpoint that returns a task) | New fields `suggested`, `confidence`, `reason`, `quote_speaker`, `quote_line` |
| `POST /api/tasks/{id}/accept` | **New.** Accept a suggested task |
| `DELETE /api/tasks/{id}` | Unchanged call; this **is** Dismiss (204, tombstone) |
| `PATCH /api/tasks/{id}` | Unchanged. Editing a suggested task leaves it suggested. `suggested` is not a PATCH field (422) |
| `GET /api/meetings/{id}/tasks` | New `to_confirm`; `assigned_people` counts only tasks that were made |
| `GET /api/settings/tasks`, `PUT /api/settings/tasks` | New `ask_before_unclear` (on by default; optional in the PUT) |

## 3. The task object: new fields

A suggested task from the owner's own view `[capture 13]` (first element of `tasks`, whole object):

```json
{
  "id": 4,
  "title": "Look into the invoice export",
  "status": "todo",
  "assignee": null,
  "spoken_owner": null,
  "due_date": null,
  "due_phrase": null,
  "quote": "Someone should look into the invoice export, it keeps failing.",
  "is_new": true,
  "unverified": false,
  "source": "ai",
  "assigned_on": "2026-11-08",
  "done_at": null,
  "created_at": "2026-11-08T15:33:58.561931Z",
  "from_meeting": true,
  "meeting": {
    "id": 1,
    "title": "Google Meet nat-aaaa-bbb",
    "platform": "google_meet",
    "created_at": "2026-11-08T15:03:58.139957Z"
  },
  "project_id": null,
  "milestone_id": null,
  "sprint_id": null,
  "carried": false,
  "check_project": false,
  "suggested": true,
  "confidence": "low",
  "reason": null,
  "quote_speaker": "Dan",
  "quote_line": 5,
  "can_update": true,
  "can_edit": true,
  "can_assign": true,
  "can_claim": true,
  "can_delete": true,
  "can_decide": true
}
```

| Field | Type | Meaning |
|---|---|---|
| `suggested` | bool | Waiting for a person. False for every task that is on the board |
| `confidence` | `"high"` \| `"medium"` \| `"low"` \| null | What the AI said. Null for a manual task and for any task made before this feature. A suggested task is always `medium` or `low` (the pill reads "Medium confidence" amber, "Low confidence" red; `high`, if ever shown, "High confidence" green) |
| `reason` | string \| null | Why it is unclear, plain text, at most 120 characters. **Null is possible on a suggested task** (the AI gave none, or its reason held a link or markup and was dropped: the task above is an example). Draw the reason line only when it is not null. Never render it as HTML or a link |
| `quote_speaker` | string \| null | Who said `quote`, from the transcript line that holds it. **Filled only for a suggested task and in `GET /api/tasks/{id}` and the accept answer**; on a Board or meeting list the other tasks have it null (a transcript is not read for every task of a long list) `[capture 12]` |
| `quote_line` | int \| null | The 1-based row of the meeting's **Transcript tab** that holds the quote (the rows `web/lib/transcript.ts` makes). **No time is stored or shown**. Same rule as `quote_speaker` |
| `can_decide` | bool | The caller may accept or dismiss it. **Only ever true for a suggested task.** Use it, not `can_edit` or `can_delete`, to enable Accept and Dismiss (section 8) |

**Nesting and null traps**
* `quote_speaker` and `quote_line` are **null whenever the caller may not read the meeting** (an assignee of a task from someone's private meeting gets the quote and nothing around it: **`reason` is null for them too**, because it can quote the speaker), and also when the quote was not found in the transcript. `[capture 20]` is that case: `meeting: null`, `quote` set, `reason: null`, `quote_speaker: null`, `quote_line: null`, `can_decide: false`. Draw the evidence box with the quote alone, no speaker line, and hide "Show source" (the meeting is not theirs to open).
* `quote` itself can be null (`unverified: true`): then there is no evidence box at all.
* `confidence` and `reason` are **on the task**, at the top level, on every endpoint (list, one task, a meeting's list, the meeting page's `tasks`, a series page's task lists). They are not nested.
* After Accept, `suggested` is false but `confidence` and `reason` **stay** as they were (`[capture 30]`: `suggested: false`, `confidence: "medium"`, the reason still there). Do not use `confidence` to decide what is waiting; use `suggested`.

## 4. The Tasks screen

**Board and Backlog:** `GET /api/tasks` as before. Suggested tasks are **not** in it `[capture 12]` (trimmed to the fields that matter; `attention` is whole):

```json
{
  "tasks": [
    {
      "id": 5,
      "title": "Book the venue",
      "suggested": false,
      "confidence": "high"
    },
    {
      "id": 1,
      "title": "Finish the checkout page",
      "suggested": false,
      "confidence": "high"
    }
  ],
  "attention": {
    "needs_owner": 0,
    "unverified": 0,
    "check_project": 0
  },
  "has_more": false,
  "next_before_id": null
}
```

**Suggested tasks section:** `GET /api/tasks?suggested=true` `[capture 13]` (trimmed to the fields that matter):

```json
{
  "tasks": [
    {
      "id": 4,
      "title": "Look into the invoice export",
      "suggested": true,
      "confidence": "low",
      "reason": null,
      "quote_speaker": "Dan",
      "quote_line": 5
    },
    {
      "id": 3,
      "title": "Help with the demo",
      "suggested": true,
      "confidence": "low",
      "reason": "Offered on a condition: \"if you need it\".",
      "quote_speaker": "Chidi",
      "quote_line": 4
    },
    {
      "id": 2,
      "title": "Look into the analytics setup",
      "suggested": true,
      "confidence": "medium",
      "reason": "Raised as something to do \"at some point\", with no commitment or date.",
      "quote_speaker": "Amina",
      "quote_line": 3
    }
  ],
  "attention": {
    "needs_owner": 0,
    "unverified": 0,
    "check_project": 0
  },
  "has_more": false
}
```

* Draw the section only when `tasks` is not empty; the heading count is `"{N} to confirm"` with N = `tasks.length` (the list is newest first; it holds up to 200, `has_more` and `next_before_id` work as everywhere).
* The other filters (`assignee`, `status`, `project`, `milestone`, `sprint`) act inside whichever list is asked for `[capture 14]`.
* The evidence box is `quote_speaker` (bold) and `“{quote}”`. The designer's file also draws a time next to the speaker and a paraphrase line under the quote: **neither is stored, so both are left out** (decision of the lead, spec section 2).
* **Show source:** link to `/meetings/{meeting.id}?tab=transcript&line={quote_line}` when `meeting` and `quote_line` are not null; when `quote_line` is null open `/meetings/{meeting.id}?tab=transcript`.
* **Edit** opens the drawer. The task **stays suggested** until Accept `[capture 28]`: `title` changed, `suggested` still true.

## 5. Accept, Dismiss, Edit

### Accept: `POST /api/tasks/{id}/accept` (no body)

Auth: logged in, CSRF header as for every write. `[capture 30]` (Dan, owner of the team, accepts Amina's task; whole response body, trimmed to the fields that changed or matter):

```json
{
  "id": 2,
  "title": "Look into the analytics setup",
  "suggested": false,
  "confidence": "medium",
  "reason": "Raised as something to do \"at some point\", with no commitment or date.",
  "assignee": {
    "user_id": 4,
    "name": "Amina Bello",
    "is_you": false
  },
  "due_date": null,
  "project_id": null,
  "sprint_id": null,
  "is_new": true,
  "quote_speaker": "Amina",
  "quote_line": 3,
  "can_edit": true,
  "can_delete": true,
  "can_decide": false
}
```

What the server does when it accepts (nothing for the FE to do):
* clears `suggested`; the task is on the board and in every count from now on;
* files it **as creation does**: a project (the recurring meeting's linked project, else the keywords, else the team's only project) and a sprint (by its due date; none without one). Here the team has no project and the task has no due date, so both stay null. A project or sprint a person set while editing it is kept (not captured; unit-tested);
* queues the **assignee mail** ("You have 1 new task") when there is an assignee who is not the meeting's owner, the owner's "Email assignees" is on, and the meeting is not an old or "quiet" (backfilled) one. **Not sent again** to a person who already had a mail about the same meeting (one mail per person per meeting): `[capture 38]` shows Chidi's from the extraction and one for Amina; `[capture 39]` is what each mail says.

**Idempotent:** a second click or a second tab gets the same 200 and the same body, and no second mail `[capture 31]`.

Errors (exact):

| Status | When | Body |
|---|---|---|
| 403 | Caller can see the task but may neither edit it nor own its meeting (a member who is not an owner or admin of the task's team; see section 8) | ```json
{
  "error": {
    "code": "team_forbidden",
    "message": "You can't do that to this task. Ask an owner or admin of your team."
  }
}
``` |
| 404 | No such task, or the caller cannot see it (not the assignee, cannot read the meeting), **or it was dismissed** | ```json
{
  "error": {
    "code": "not_found",
    "message": "No such task"
  }
}
``` |
| 409 | The task never was a suggestion (a clear task, a manual task, one made before this feature) `[capture 25]` | ```json
{
  "error": {
    "code": "not_suggested",
    "message": "This task is not waiting to be confirmed."
  }
}
``` |
| 403 | Missing or wrong CSRF header `[capture 27]` | ```json
{
  "error": {
    "code": "csrf_rejected",
    "message": "Missing or wrong X-CSRF-Token header. Read it from GET /api/session."
  }
}
``` |

### Dismiss: `DELETE /api/tasks/{id}`

204 with no body `[capture 35]`. The task is gone for good: `GET /api/tasks/{id}` is then 404 `[capture 36]` and accept is 404 `[capture 37]`. The same sentence is not suggested again, **even when the notes are read again**: the server keeps a tombstone `[capture 41]` (after a re-read Chidi's suggestion is still waiting, the dismissed one is not back). A member who may not dismiss gets 403 `team_forbidden` `[capture 22]`, the same body as accept.

### Toasts

Success: **"Task added to the board"** (Accept) and **"Suggestion dismissed"** (Dismiss). A failed call (any non-2xx, or no network): **"Could not update the task. Try again."** and the row stays (lead-designed: the file draws no failure). On 404 the task is gone for this person: remove the row after the toast.

## 6. The meeting page

`GET /api/meetings/{id}/tasks` `[capture 15]` lists **every** task of the meeting, the suggested ones too. Trimmed rows and whole summary fields:

```json
{
  "tasks": [
    {
      "id": 1,
      "title": "Finish the checkout page",
      "suggested": false,
      "confidence": "high",
      "assignee": {
        "user_id": 3,
        "name": "Chidi Eze",
        "is_you": false
      }
    },
    {
      "id": 2,
      "title": "Look into the analytics setup",
      "suggested": true,
      "confidence": "medium",
      "assignee": {
        "user_id": 4,
        "name": "Amina Bello",
        "is_you": false
      }
    },
    {
      "id": 3,
      "title": "Help with the demo",
      "suggested": true,
      "confidence": "low",
      "assignee": {
        "user_id": 3,
        "name": "Chidi Eze",
        "is_you": false
      }
    },
    {
      "id": 4,
      "title": "Look into the invoice export",
      "suggested": true,
      "confidence": "low",
      "assignee": null
    },
    {
      "id": 5,
      "title": "Book the venue",
      "suggested": false,
      "confidence": "high",
      "assignee": {
        "user_id": 2,
        "name": "Dan Okoro",
        "is_you": true
      }
    }
  ],
  "assigned_people": 2,
  "to_confirm": 3,
  "extraction_status": "done"
}
```

* The summary line is **"{made} tasks created, assigned to {assigned_people} people, {to_confirm} to confirm"**, with `made = tasks.filter(t => !t.suggested).length` (here 2) and the ", K to confirm" tail only when `to_confirm > 0`. **`assigned_people` already leaves out the suggested tasks' people** (here 2, not 3): do not recount it from `tasks`.
* The Action items row shows the pill **"To confirm"** on a task with `suggested: true`.
* `GET /api/meetings/{id}` (the page) has the same `tasks` list, and its `tasks_count` is the number **made**, not the list length `[capture 16]`:

```json
{
  "tasks_count": 2,
  "tasks": [
    {
      "id": 1,
      "title": "Finish the checkout page",
      "suggested": false
    },
    {
      "id": 2,
      "title": "Look into the analytics setup",
      "suggested": true
    },
    {
      "id": 3,
      "title": "Help with the demo",
      "suggested": true
    },
    {
      "id": 4,
      "title": "Look into the invoice export",
      "suggested": true
    },
    {
      "id": 5,
      "title": "Book the venue",
      "suggested": false
    }
  ]
}
```

* The meetings list `tasks_count` is the same made-only number: 2 here `[capture 17]` (the meeting has 5 tasks, 3 of them suggested).

## 7. What the FE must not count (the server already leaves suggested tasks out)

Do not add suggested tasks back into any of these; use the numbers as the API gives them:

| Where | Field | Capture |
|---|---|---|
| Board and Backlog lists | `GET /api/tasks` (default) | 12 |
| Tasks attention banners | `attention.needs_owner`, `.unverified`, `.check_project` (`needs_owner` is `0` in capture 12 although one of the waiting tasks has no owner) | 12 |
| Sidebar badge | `badges.tasks_new` in `GET /api/session` | 18 (`{"review_open": 0, "tasks_new": 2}`) |
| Meetings list and page | `tasks_count` | 16, 17 |
| A series page | `unfinished`, `finished`, `past_meetings[].tasks_count`, the Recurring cards' `unfinished_tasks` | not captured (tested in `tests/test_suggested_exclusions.py`) |
| Projects | `open_tasks`, `in_current_sprint`, `in_backlog`, a milestone's `task_count` and `done_count` | not captured (tested) |
| Sprints | `tasks_total`, `tasks_done`, and `moved` when a sprint closes | not captured (tested) |
| Team page | each member's `open_tasks` | not captured (tested) |
| Mail | the one-hour reminder's "Still open from last time", the summary box's "closed with N of M tasks done", the assignee mail | `[capture 10]`, `[capture 38]`, `[capture 39]` for the assignee mail; the others tested |

## 8. Who may do what

| Person | See it | Accept, Dismiss, Edit title |
|---|---|---|
| Owner or admin of the task's team, who can read the meeting | yes | yes |
| The person who **owns the meeting** the task came from, whatever their role | yes | **yes** to Accept and Dismiss (`can_decide` true); the other edits follow `can_edit` (not captured; unit-tested) |
| A member who is not an owner or admin and does not own the meeting (also when it is their own task) | yes, if the task is theirs or they can read the meeting | **no: 403 `team_forbidden`** for Accept, Dismiss and a title edit `[capture 21, 22]` |
| Anyone who cannot read the meeting and is not the assignee | **no: 404** | 404 `[capture 23, 24]` |

The rule is "whoever may edit the task, or owns the meeting it came from". **Use `can_decide` on the task** to draw Accept and Dismiss as working buttons (Edit opens the drawer for anyone who can see the task; its fields follow `can_update`, `can_edit`, `can_assign`): for Chidi in `[capture 20]` it is false. A teammate who merely reads a shared meeting cannot decide (403 `team_forbidden`).

## 9. Settings: "Ask before adding unclear tasks"

`GET /api/settings/tasks` `[capture 09]` (all three on, also with no settings row):

```json
{
  "auto_create_tasks": true,
  "email_assignees": true,
  "ask_before_unclear": true
}
```

`PUT /api/settings/tasks` `[capture 42]` request and response:

```json
{
  "auto_create_tasks": true,
  "email_assignees": true,
  "ask_before_unclear": false
}
```
```json
{
  "auto_create_tasks": true,
  "email_assignees": true,
  "ask_before_unclear": false
}
```

* `ask_before_unclear` is **optional** in the PUT. Left out, it stays as it was: `[capture 44]` sends only the two old fields and gets `ask_before_unclear: false` back (it had been turned off).
* Not a boolean: 422 `[capture 45]`:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Some fields are missing or not valid.",
    "fields": [
      {
        "field": "ask_before_unclear",
        "message": "Input should be a valid boolean"
      }
    ]
  }
}
```

* On means an action item the AI rates medium or low waits under Suggested tasks; off means every action item becomes a task. **Turning it off does not move tasks already waiting**: they stay suggested until someone accepts or dismisses them `[capture 47]`. Say so in the row's help text only if it is not obvious; the designer's text is "Clear commitments like "I'll take checkout" are added straight away. Vague or conditional ones wait under Suggested tasks."
* The setting is read **when a meeting is read** (a few minutes after it ends), not when a task is shown.

## 10. Other errors

`GET /api/tasks?suggested=maybe` `[capture 48]`:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Some fields are missing or not valid.",
    "fields": [
      {
        "field": "suggested",
        "message": "Input should be a valid boolean, unable to interpret input"
      }
    ]
  }
}
```

`PATCH /api/tasks/{id}` with `{"suggested": false}` `[capture 29]` (accepting is the accept route, not a field):

```json
{
  "error": {
    "code": "validation_error",
    "message": "Some fields are missing or not valid.",
    "fields": [
      {
        "field": "suggested",
        "message": "Extra inputs are not permitted"
      }
    ]
  }
}
```

## 11. UX consequences the FE must surface

* **A suggested task is in no count.** The "Tasks" sidebar badge, the attention banners and every "N open" figure do not include it. Do not "helpfully" add it, and do not show a task as "Needs owner" while it waits.
* **Accept can email someone.** Before accepting a task assigned to a teammate, nothing is shown (the design has no confirmation), but the person will get "You have 1 new task" unless they were already mailed about this meeting. Do not add a "sent" claim to the toast: the mail is queued, and may be skipped (switches, older meeting, already mailed).
* **Dismiss is permanent.** There is no undo and no list of dismissed tasks; the same sentence will not come back. The design's toast is the only feedback.
* **Edit does not accept.** After editing a suggested task, it is still waiting (the drawer's amber box stays). A person who changes the status of a suggested task (say to "done") still leaves it suggested and uncounted (not captured; unit-tested): the drawer keeps showing the amber box.
* **A task can stop being suggested while the page is open** (someone else accepted it, or it was dismissed). Accept on a task already accepted answers 200 (draw it as accepted); on a task dismissed or no longer visible, 404: show the failure toast and drop the row.
* **Turning the setting off keeps what is waiting.** Someone who switches it off may expect the Suggested tasks section to empty. It does not.
* **A reason can be missing** (null): the row has no amber line, the drawer's box reads "Suggested task, medium confidence." with nothing after it.

## 12. State machine and polling

A task's life: `made by the worker` -> **suggested** (`suggested: true`) -> Accept -> **on the board** (`suggested: false`), or -> Dismiss -> **gone** (404). A task is never suggested again after it was accepted, and a dismissed one is never back.

There is nothing to poll for: suggestions appear when the worker has read a meeting (`extraction_status` `done` on the meeting page, which the meeting page already polls while a meeting is being read). On the Tasks screen, refetch `GET /api/tasks?suggested=true` and `GET /api/tasks` after each Accept, Dismiss or edit, and when the screen gains focus. A short interval is not needed.

## 13. What was not exercised live

* **The owner-member rule** (a plain member who owns the meeting may accept and dismiss): unit-tested (`test_a_plain_member_who_owns_the_meeting_may_accept_and_dismiss_its_suggestions`), not captured.

* **The model's real answer.** The capture uses a stand-in reply (see the top). The prompt `extract-v2` and the checks on `reason` are covered by unit tests (`tests/test_suggested_tasks.py`), not by a model call.
* **Accept filing into a project and a sprint with a due date.** The capture's team has no project and the accepted task has no due date, so `project_id` and `sprint_id` stay null in `[capture 30]`. The filing is covered by `tests/test_suggested_tasks_api.py` (`test_accepting_files_the_task_mails_the_assignee_and_clears_the_flag`).
* The series, project, milestone, sprint and team figures (section 7), the one-hour reminder and the summary box: the rule is tested (`tests/test_suggested_exclusions.py`); no capture of them was made.
* A **second mail to someone already mailed**: the capture shows the first mail for each person; "not mailed again" is tested (`test_someone_already_mailed_about_the_meeting_is_not_mailed_again`).

## 14. Verification table

| Behaviour | Verified |
|---|---|
| Suggested tasks absent from `GET /api/tasks`, `attention` and the badge | Live (captures 12, 18) |
| `GET /api/tasks?suggested=true` and its extra fields | Live (13) |
| `TaskOut` new fields; null reason; speaker and line | Live (13, 20, 30) |
| Private-meeting assignee sees the quote without speaker or line | Live (20) |
| Accept: 200, idempotent, mail queued once, rendered mail | Live (30, 31, 38, 39) |
| Accept 403, 404, 409, CSRF | Live (21, 23, 25, 26, 27) |
| A member who owns the meeting may accept and dismiss; a plain reader may not | **Not live** (unit-tested) |
| Dismiss 204, then 404, tombstone survives a re-read | Live (35, 36, 37, 40, 41) |
| Edit keeps it suggested; `suggested` is not a PATCH field | Live (28, 29) |
| Meeting task list `to_confirm`, `assigned_people`, `tasks_count` | Live (15, 16, 17) |
| Settings `ask_before_unclear`: default, PUT, optional, invalid, off keeps waiting tasks | Live (09, 42 to 47) |
| `suggested=maybe` is 422 | Live (48) |
| The model decides which items are unclear, and its reasons | **Not live** (stand-in reply; unit-tested checks) |
| Accept files into a project and sprint | **Not live** (unit-tested) |
| Exclusion from series, projects, milestones, sprints, team counts and the reminder and summary mails | **Not live** (unit-tested, `tests/test_suggested_exclusions.py`) |
| Not mailed again to a person already mailed about the meeting | **Not live** (unit-tested) |
