# Frontend integration guide: Tasks (W2)

Everything the Tasks screen, the task drawer, the meeting page's Action items and Settings > Tasks need. **Every payload, status code and error string below is pasted from a real response** captured by `scripts/capture_tasks_api.py` against the running app (real HTTP, real sessions and CSRF, real database, the real worker step, the real mail builder). The files are in `docs/fe-captures/tasks-w2/`; run the script again to re-derive any of it. Long values are shortened with an explicit `<… omitted>` marker; nothing is tidied or invented. What could not be run live is marked **NOT LIVE** where it matters, and listed in the verification table at the end.

Shapes and descriptions of every field are in `docs/openapi.json` (generated into `web/lib/api/schema.d.ts`). This guide is for behaviour.

## What the feature is, in your terms

After a meeting's notes are written, the app reads the notes' **Action items** into **tasks**. Each task has a title, an assignee (a teammate, or nobody: **Needs owner**), a due date, a status (`todo`, `doing`, `done`) and a short quote from the transcript showing where it came from. People can also add tasks by hand. Tasks show on the Tasks screen (board) and on their meeting's page. When a task is assigned to somebody other than the meeting's owner, that person gets one email per meeting listing their new tasks.

Not in this version (so nothing in the API for it, and no control for it): projects, sprints, milestones, "Backlog", "Carried over", "Check project", the teammate filter chips (they need the Team screen, W3), "Invite them to the team". They arrive with W3, W4 and W9, and each adds fields and parameters then.

## Conventions (same as the rest of the API)

Session cookie; every non-GET sends `X-CSRF-Token` (from `GET /api/session`); errors are `{"error": {"code", "message"}}`, validation errors add `fields`. A task the caller may not see is **404**, never 403. A task they can see but may not change is **403 `team_forbidden`**. Ids are integers.

Logged out:

`GET /api/tasks` as **visitor** → **401**  
*capture `09-tasks-logged-out.json`*  
No login: 401.

Response body:
```json
{
  "error": {
    "code": "not_authenticated",
    "message": "Log in first."
  }
}
```

## The session badge

`badges.tasks_new` is the number on the sidebar's Tasks item: open (not done) tasks the caller can see that are still marked New. It is a **count over the whole team's visible tasks**, not only the caller's own (the designer's badge does the same). A task stops being New when anyone changes its status.

`GET /api/session` as **dan** → **200**  
*capture `20-session-after.json`*  
`badges.tasks_new` counts the New open tasks.

Response body:
```json
{
  "authenticated": true,
  "csrf_token": "<csrf-B>",
  "user": {
    "email": "dan@example.test",
    "name": "Dan Okoro",
    "is_admin": false,
    "email_verified": true,
    "time_zone": null
  },
  "usage": {
    "used_seconds": 0,
    "cap_seconds": 18000
  },
  "unread_notifications": 0,
  "badges": {
    "review_open": 2,
    "tasks_new": 4
  }
}
```

## Endpoints

### `GET /api/tasks`: the board

Query (all optional): `assignee` = `me` | `none` (Needs owner) | a user id; `status` = `todo` | `doing` | `done`; `limit` (1 to 500, default 200); `before_id` (the cursor). Newest task first (by id). **Group, sort and place into columns on the client**: `assigned_on` and `due_date` are plain `YYYY-MM-DD` dates (no time zone; compare them with the user's own "today"), `status` is the column.

`people` is the assignee list for the drawer's select (the active members of the caller's team, the caller included, `is_you` marks them). `attention` counts every open task the caller can see, whatever the filter or page: the banner's "N tasks need attention" is `needs_owner` plus nothing else today (see the traps below for `unverified`).

`GET /api/tasks` as **dan** → **200**  
*capture `17-tasks.json`*  
The owner's board. Chidi's and Amina's names matched real teammates by code; Dan's own sentence matched nobody.

Response body:
```json
{
  "tasks": [
    {
      "id": 4,
      "title": "Ask finance to confirm the contract number",
      "status": "todo",
      "assignee": {
        "user_id": 3,
        "name": "Chidi Eze",
        "is_you": false
      },
      "spoken_owner": "Chidi",
      "due_date": "2026-11-04",
      "due_phrase": "tomorrow",
      "quote": "I will ask finance tomorrow.",
      "is_new": true,
      "unverified": false,
      "source": "ai",
      "assigned_on": "2026-11-03",
      "done_at": null,
      "created_at": "2026-11-05T14:57:20.160822Z",
      "from_meeting": true,
      "meeting": {
        "id": 1,
        "title": "Google Meet nat-aaaa-bbb",
        "platform": "google_meet",
        "created_at": "2026-11-03T10:00:00Z"
      },
      "can_update": true,
      "can_edit": true,
      "can_assign": true,
      "can_claim": false,
      "can_delete": true
    },
    "<3 more task objects of the same shape omitted>"
  ],
  "people": [
    {
      "user_id": 2,
      "name": "Dan Okoro",
      "is_you": true
    },
    {
      "user_id": 3,
      "name": "Chidi Eze",
      "is_you": false
    },
    {
      "user_id": 4,
      "name": "Amina Bello",
      "is_you": false
    }
  ],
  "attention": {
    "needs_owner": 0,
    "unverified": 0
  },
  "has_more": false,
  "next_before_id": null
}
```

Filters and paging:

`GET /api/tasks?assignee=none` as **dan** → **200**  
*capture `22-tasks-filter-none.json`*

Response body:
```json
{
  "tasks": [],
  "people": [
    {
      "user_id": 2,
      "name": "Dan Okoro",
      "is_you": true
    },
    {
      "user_id": 3,
      "name": "Chidi Eze",
      "is_you": false
    },
    {
      "user_id": 4,
      "name": "Amina Bello",
      "is_you": false
    }
  ],
  "attention": {
    "needs_owner": 0,
    "unverified": 0
  },
  "has_more": false,
  "next_before_id": null
}
```

`GET /api/tasks?limit=2` as **dan** → **200**  
*capture `24-tasks-page-1.json`*

Response body:
```json
{
  "tasks": [
    {
      "id": 4,
      "title": "Ask finance to confirm the contract number",
      "status": "todo",
      "assignee": {
        "user_id": 3,
        "name": "Chidi Eze",
        "is_you": false
      },
      "spoken_owner": "Chidi",
      "due_date": "2026-11-04",
      "due_phrase": "tomorrow",
      "quote": "I will ask finance tomorrow.",
      "is_new": true,
      "unverified": false,
      "source": "ai",
      "assigned_on": "2026-11-03",
      "done_at": null,
      "created_at": "2026-11-05T14:57:20.160822Z",
      "from_meeting": true,
      "meeting": {
        "id": 1,
        "title": "Google Meet nat-aaaa-bbb",
        "platform": "google_meet",
        "created_at": "2026-11-03T10:00:00Z"
      },
      "can_update": true,
      "can_edit": true,
      "can_assign": true,
      "can_claim": false,
      "can_delete": true
    },
    {
      "id": 3,
      "title": "Check the vendor site for the spelling",
      "status": "todo",
      "assignee": {
        "user_id": 4,
        "name": "Amina Bello",
        "is_you": false
      },
      "spoken_owner": "Amina",
      "due_date": null,
      "due_phrase": null,
      "quote": "I'll look at the vendor site.",
      "is_new": true,
      "unverified": false,
      "source": "ai",
      "assigned_on": "2026-11-03",
      "done_at": null,
      "created_at": "2026-11-05T14:57:20.160822Z",
      "from_meeting": true,
      "meeting": {
        "id": 1,
        "title": "Google Meet nat-aaaa-bbb",
        "platform": "google_meet",
        "created_at": "2026-11-03T10:00:00Z"
      },
      "can_update": true,
      "can_edit": true,
      "can_assign": true,
      "can_claim": false,
      "can_delete": true
    }
  ],
  "people": [
    {
      "user_id": 2,
      "name": "Dan Okoro",
      "is_you": true
    },
    {
      "user_id": 3,
      "name": "Chidi Eze",
      "is_you": false
    },
    {
      "user_id": 4,
      "name": "Amina Bello",
      "is_you": false
    }
  ],
  "attention": {
    "needs_owner": 0,
    "unverified": 0
  },
  "has_more": true,
  "next_before_id": 3
}
```

`GET /api/tasks?limit=2&before_id=3` as **dan** → **200**  
*capture `25-tasks-page-2.json`*

Response body:
```json
{
  "tasks": [
    {
      "id": 2,
      "title": "Share a draft of the dashboards",
      "status": "todo",
      "assignee": {
        "user_id": 4,
        "name": "Amina Bello",
        "is_you": false
      },
      "spoken_owner": "Amina",
      "due_date": "2026-11-11",
      "due_phrase": "on Wednesday of next week",
      "quote": "I'll share a draft on Wednesday of next week.",
      "is_new": true,
      "unverified": false,
      "source": "ai",
      "assigned_on": "2026-11-03",
      "done_at": null,
      "created_at": "2026-11-05T14:57:20.160822Z",
      "from_meeting": true,
      "meeting": {
        "id": 1,
        "title": "Google Meet nat-aaaa-bbb",
        "platform": "google_meet",
        "created_at": "2026-11-03T10:00:00Z"
      },
      "can_update": true,
      "can_edit": true,
      "can_assign": true,
      "can_claim": false,
      "can_delete": true
    },
    {
      "id": 1,
      "title": "Set up the tracking plan",
      "status": "todo",
      "assignee": {
        "user_id": 3,
        "name": "Chidi Eze",
        "is_you": false
      },
      "spoken_owner": "Chidi",
      "due_date": "2026-11-06",
      "due_phrase": "by Friday",
      "quote": "For analytics I will set up the tracking plan by Friday.",
      "is_new": true,
      "unverified": false,
      "source": "ai",
      "assigned_on": "2026-11-03",
      "done_at": null,
      "created_at": "2026-11-05T14:57:20.160822Z",
      "from_meeting": true,
      "meeting": {
        "id": 1,
        "title": "Google Meet nat-aaaa-bbb",
        "platform": "google_meet",
        "created_at": "2026-11-03T10:00:00Z"
      },
      "can_update": true,
      "can_edit": true,
      "can_assign": true,
      "can_claim": false,
      "can_delete": true
    }
  ],
  "people": [
    {
      "user_id": 2,
      "name": "Dan Okoro",
      "is_you": true
    },
    {
      "user_id": 3,
      "name": "Chidi Eze",
      "is_you": false
    },
    {
      "user_id": 4,
      "name": "Amina Bello",
      "is_you": false
    }
  ],
  "attention": {
    "needs_owner": 0,
    "unverified": 0
  },
  "has_more": false,
  "next_before_id": null
}
```

Bad query values:

`GET /api/tasks?assignee=x` as **dan** → **422**  
*capture `26-tasks-invalid-assignee.json`*

Response body:
```json
{
  "error": {
    "code": "validation_error",
    "message": "Some fields are missing or not valid.",
    "fields": [
      {
        "field": "assignee",
        "message": "String should match pattern '^(me|none|\\d{1,10})$'"
      }
    ]
  }
}
```

`GET /api/tasks?limit=501` as **dan** → **422**  
*capture `27-tasks-invalid-limit.json`*

Response body:
```json
{
  "error": {
    "code": "validation_error",
    "message": "Some fields are missing or not valid.",
    "fields": [
      {
        "field": "limit",
        "message": "Input should be less than or equal to 500"
      }
    ]
  }
}
```

`has_more` and `next_before_id`: pass `before_id` = `next_before_id` for the next page. `next_before_id` is null on the last page.

### `GET /api/tasks/{id}`: the drawer

Same object as a list item. Opening the drawer from the board needs no call; call it to refresh one task.

`GET /api/tasks/4` as **chidi** → **200**  
*capture `33-chidi-task.json`*

Response body:
```json
{
  "id": 4,
  "title": "Ask finance to confirm the contract number",
  "status": "todo",
  "assignee": {
    "user_id": 3,
    "name": "Chidi Eze",
    "is_you": true
  },
  "spoken_owner": "Chidi",
  "due_date": "2026-11-04",
  "due_phrase": "tomorrow",
  "quote": "I will ask finance tomorrow.",
  "is_new": true,
  "unverified": false,
  "source": "ai",
  "assigned_on": "2026-11-03",
  "done_at": null,
  "created_at": "2026-11-05T14:57:20.160822Z",
  "from_meeting": true,
  "meeting": null,
  "can_update": true,
  "can_edit": false,
  "can_assign": false,
  "can_claim": false,
  "can_delete": false
}
```

An id the caller cannot see (someone else's task, a task of a private meeting they are not assigned to, a deleted task, an id that does not exist) is the same answer:

`GET /api/tasks/3` as **chidi** → **404**  
*capture `34-chidi-other-task.json`*  
A task of the private meeting that is not his: 404.

Response body:
```json
{
  "error": {
    "code": "not_found",
    "message": "No such task"
  }
}
```

### `PATCH /api/tasks/{id}`

Send only the fields that change: `title`, `status`, `assignee_user_id` (null clears it), `due_date` (`YYYY-MM-DD`, null clears it). A field sent with the value it already has is not a change. Unknown fields are a 422. The response is the whole updated task.

What the caller may do is on the task itself, so the screen can show only what will work:

| Flag | Meaning |
|---|---|
| `can_update` | change `status` and `due_date` |
| `can_edit` | change `title` |
| `can_assign` | give it to someone else, or to nobody |
| `can_claim` | take it for themselves (it has no owner yet) |
| `can_delete` | delete it |

Roles: an **owner or admin** of the team may do all of it to every task they can see. A **member** may change the status and due date of a task assigned to them, may take a task nobody has (`assignee_user_id` set to their own id), and may do everything to a task they made themselves. Anything else is 403, and **one forbidden field refuses the whole request** (nothing is applied).

A member moves their own task (a status change also clears New, and sets `done_at` when it becomes `done`):

`PATCH /api/tasks/4` as **chidi** → **200**  
*capture `41-member-status.json`*  
A member changes status of a task assigned to them.

Request body:
```json
{
  "status": "doing"
}
```

Response body:
```json
{
  "id": 4,
  "title": "Ask finance to confirm the contract number",
  "status": "doing",
  "assignee": {
    "user_id": 3,
    "name": "Chidi Eze",
    "is_you": true
  },
  "spoken_owner": "Chidi",
  "due_date": "2026-11-04",
  "due_phrase": "tomorrow",
  "quote": "I will ask finance tomorrow.",
  "is_new": false,
  "unverified": false,
  "source": "ai",
  "assigned_on": "2026-11-03",
  "done_at": null,
  "created_at": "2026-11-05T14:57:20.160822Z",
  "from_meeting": true,
  "meeting": null,
  "can_update": true,
  "can_edit": false,
  "can_assign": false,
  "can_claim": false,
  "can_delete": false
}
```

`PATCH /api/tasks/4` as **chidi** → **200**  
*capture `42-member-due.json`*

Request body:
```json
{
  "due_date": "2026-11-09"
}
```

Response body:
```json
{
  "id": 4,
  "title": "Ask finance to confirm the contract number",
  "status": "doing",
  "assignee": {
    "user_id": 3,
    "name": "Chidi Eze",
    "is_you": true
  },
  "spoken_owner": "Chidi",
  "due_date": "2026-11-09",
  "due_phrase": "tomorrow",
  "quote": "I will ask finance tomorrow.",
  "is_new": false,
  "unverified": false,
  "source": "ai",
  "assigned_on": "2026-11-03",
  "done_at": null,
  "created_at": "2026-11-05T14:57:20.160822Z",
  "from_meeting": true,
  "meeting": null,
  "can_update": true,
  "can_edit": false,
  "can_assign": false,
  "can_claim": false,
  "can_delete": false
}
```

A member tries to rename, unassign, or mix an allowed change with a forbidden one:

`PATCH /api/tasks/4` as **chidi** → **403**  
*capture `43-member-title.json`*  
403 `team_forbidden`; nothing changes.

Request body:
```json
{
  "title": "Renamed"
}
```

Response body:
```json
{
  "error": {
    "code": "team_forbidden",
    "message": "You can't do that to this task. Ask an owner or admin of your team."
  }
}
```

`PATCH /api/tasks/4` as **chidi** → **403**  
*capture `44-member-reassign.json`*

Request body:
```json
{
  "assignee_user_id": null
}
```

Response body:
```json
{
  "error": {
    "code": "team_forbidden",
    "message": "You can't do that to this task. Ask an owner or admin of your team."
  }
}
```

`PATCH /api/tasks/4` as **chidi** → **403**  
*capture `45-member-mixed.json`*  
One field not allowed refuses the whole request.

Request body:
```json
{
  "status": "done",
  "title": "Sneaky"
}
```

Response body:
```json
{
  "error": {
    "code": "team_forbidden",
    "message": "You can't do that to this task. Ask an owner or admin of your team."
  }
}
```

The task is unchanged by the refused calls (`doing`, the due date from the allowed call, `is_new` false):

`GET /api/tasks/4` as **chidi** → **200**  
*capture `47-member-task-after.json`*  
Unchanged by the refused calls; `status` doing, `is_new` false after the first move.

Response body:
```json
{
  "id": 4,
  "title": "Ask finance to confirm the contract number",
  "status": "doing",
  "assignee": {
    "user_id": 3,
    "name": "Chidi Eze",
    "is_you": true
  },
  "spoken_owner": "Chidi",
  "due_date": "2026-11-09",
  "due_phrase": "tomorrow",
  "quote": "I will ask finance tomorrow.",
  "is_new": false,
  "unverified": false,
  "source": "ai",
  "assigned_on": "2026-11-03",
  "done_at": null,
  "created_at": "2026-11-05T14:57:20.160822Z",
  "from_meeting": true,
  "meeting": null,
  "can_update": true,
  "can_edit": false,
  "can_assign": false,
  "can_claim": false,
  "can_delete": false
}
```

A member takes a task nobody has, and may not take one somebody else has:

`PATCH /api/tasks/2` as **chidi** → **200**  
*capture `53-member-claim.json`*  
A member may take a task nobody has.

Request body:
```json
{
  "assignee_user_id": 3
}
```

Response body:
```json
{
  "id": 2,
  "title": "Share a draft of the dashboards",
  "status": "todo",
  "assignee": {
    "user_id": 3,
    "name": "Chidi Eze",
    "is_you": true
  },
  "spoken_owner": "Amina",
  "due_date": "2026-11-11",
  "due_phrase": "on Wednesday of next week",
  "quote": "I'll share a draft on Wednesday of next week.",
  "is_new": true,
  "unverified": false,
  "source": "ai",
  "assigned_on": "2026-11-03",
  "done_at": null,
  "created_at": "2026-11-05T14:57:20.160822Z",
  "from_meeting": true,
  "meeting": {
    "id": 1,
    "title": "Google Meet nat-aaaa-bbb",
    "platform": "google_meet",
    "created_at": "2026-11-03T10:00:00Z"
  },
  "can_update": true,
  "can_edit": false,
  "can_assign": false,
  "can_claim": false,
  "can_delete": false
}
```

`PATCH /api/tasks/3` as **chidi** → **403**  
*capture `56-member-claim-taken.json`*  
Not one somebody else has: 403.

Request body:
```json
{
  "assignee_user_id": 3
}
```

Response body:
```json
{
  "error": {
    "code": "team_forbidden",
    "message": "You can't do that to this task. Ask an owner or admin of your team."
  }
}
```

An admin assigns, and tries to assign to someone who is not on the team:

`PATCH /api/tasks/2` as **amina** → **200**  
*capture `54-admin-assign.json`*

Request body:
```json
{
  "assignee_user_id": 3,
  "due_date": "2026-11-12"
}
```

Response body:
```json
{
  "id": 2,
  "title": "Share a draft of the dashboards",
  "status": "todo",
  "assignee": {
    "user_id": 3,
    "name": "Chidi Eze",
    "is_you": false
  },
  "spoken_owner": "Amina",
  "due_date": "2026-11-12",
  "due_phrase": "on Wednesday of next week",
  "quote": "I'll share a draft on Wednesday of next week.",
  "is_new": true,
  "unverified": false,
  "source": "ai",
  "assigned_on": "2026-11-03",
  "done_at": null,
  "created_at": "2026-11-05T14:57:20.160822Z",
  "from_meeting": true,
  "meeting": {
    "id": 1,
    "title": "Google Meet nat-aaaa-bbb",
    "platform": "google_meet",
    "created_at": "2026-11-03T10:00:00Z"
  },
  "can_update": true,
  "can_edit": true,
  "can_assign": true,
  "can_claim": false,
  "can_delete": true
}
```

`PATCH /api/tasks/2` as **amina** → **422**  
*capture `55-admin-assign-off-team.json`*  
422 `assignee_not_on_team`.

Request body:
```json
{
  "assignee_user_id": 999999
}
```

Response body:
```json
{
  "error": {
    "code": "assignee_not_on_team",
    "message": "That person is not on your team."
  }
}
```

Validation:

`PATCH /api/tasks/2` as **amina** → **422**  
*capture `58-patch-validation.json`*

Request body:
```json
{
  "title": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
}
```

Response body:
```json
{
  "error": {
    "code": "validation_error",
    "message": "Some fields are missing or not valid.",
    "fields": [
      {
        "field": "title",
        "message": "String should have at most 200 characters"
      }
    ]
  }
}
```

`PATCH /api/tasks/2` as **amina** → **422**  
*capture `59-patch-bad-status.json`*

Request body:
```json
{
  "status": "blocked"
}
```

Response body:
```json
{
  "error": {
    "code": "validation_error",
    "message": "Some fields are missing or not valid.",
    "fields": [
      {
        "field": "status",
        "message": "Input should be 'todo', 'doing' or 'done'"
      }
    ]
  }
}
```

`status` and `title` cannot be null; this is the one validation answer without `fields`:

`PATCH /api/tasks/2` as **amina** → **422**  
*capture `60-patch-null-status.json`*

Request body:
```json
{
  "status": null
}
```

Response body:
```json
{
  "error": {
    "code": "validation_error",
    "message": "Status and title cannot be null."
  }
}
```

Missing CSRF header:

`PATCH /api/tasks/2` as **amina** → **403**  
*capture `61-patch-no-csrf.json`*

Request body:
```json
{
  "status": "done"
}
```

Response body:
```json
{
  "error": {
    "code": "csrf_rejected",
    "message": "Missing or wrong X-CSRF-Token header. Read it from GET /api/session."
  }
}
```

Done, and clearing a due date (the `due_phrase`, the words as said, stays):

`PATCH /api/tasks/2` as **amina** → **200**  
*capture `62-patch-done.json`*  
`done_at` set.

Request body:
```json
{
  "status": "done"
}
```

Response body:
```json
{
  "id": 2,
  "title": "Share a draft of the dashboards",
  "status": "done",
  "assignee": {
    "user_id": 3,
    "name": "Chidi Eze",
    "is_you": false
  },
  "spoken_owner": "Amina",
  "due_date": "2026-11-12",
  "due_phrase": "on Wednesday of next week",
  "quote": "I'll share a draft on Wednesday of next week.",
  "is_new": false,
  "unverified": false,
  "source": "ai",
  "assigned_on": "2026-11-03",
  "done_at": "2026-11-05T14:57:22.240459Z",
  "created_at": "2026-11-05T14:57:20.160822Z",
  "from_meeting": true,
  "meeting": {
    "id": 1,
    "title": "Google Meet nat-aaaa-bbb",
    "platform": "google_meet",
    "created_at": "2026-11-03T10:00:00Z"
  },
  "can_update": true,
  "can_edit": true,
  "can_assign": true,
  "can_claim": false,
  "can_delete": true
}
```

`PATCH /api/tasks/2` as **amina** → **200**  
*capture `63-patch-clear-due.json`*

Request body:
```json
{
  "due_date": null
}
```

Response body:
```json
{
  "id": 2,
  "title": "Share a draft of the dashboards",
  "status": "done",
  "assignee": {
    "user_id": 3,
    "name": "Chidi Eze",
    "is_you": false
  },
  "spoken_owner": "Amina",
  "due_date": null,
  "due_phrase": "on Wednesday of next week",
  "quote": "I'll share a draft on Wednesday of next week.",
  "is_new": false,
  "unverified": false,
  "source": "ai",
  "assigned_on": "2026-11-03",
  "done_at": "2026-11-05T14:57:22.240459Z",
  "created_at": "2026-11-05T14:57:20.160822Z",
  "from_meeting": true,
  "meeting": {
    "id": 1,
    "title": "Google Meet nat-aaaa-bbb",
    "platform": "google_meet",
    "created_at": "2026-11-03T10:00:00Z"
  },
  "can_update": true,
  "can_edit": true,
  "can_assign": true,
  "can_claim": false,
  "can_delete": true
}
```

### `POST /api/tasks`: New task

Anyone on the team may. The task has no meeting (`from_meeting` false, `meeting` null, `quote` null). Everything is optional: **a body of `{}` makes a blank task**, which is what the drawer's "New task" button needs (the designer opens the drawer with an empty title; show "Untitled task" for `title` ""). `status` defaults to `todo`.

`POST /api/tasks` as **chidi** → **201**  
*capture `64-create.json`*  
Anyone on the team may.

Request body:
```json
{
  "title": "Order the cake",
  "assignee_user_id": 3,
  "due_date": "2026-11-10"
}
```

Response body:
```json
{
  "id": 5,
  "title": "Order the cake",
  "status": "todo",
  "assignee": {
    "user_id": 3,
    "name": "Chidi Eze",
    "is_you": true
  },
  "spoken_owner": null,
  "due_date": "2026-11-10",
  "due_phrase": null,
  "quote": null,
  "is_new": false,
  "unverified": false,
  "source": "manual",
  "assigned_on": "2026-11-05",
  "done_at": null,
  "created_at": "2026-11-05T14:57:22.285778Z",
  "from_meeting": false,
  "meeting": null,
  "can_update": true,
  "can_edit": true,
  "can_assign": true,
  "can_claim": false,
  "can_delete": true
}
```

`POST /api/tasks` as **chidi** → **201**  
*capture `65-create-blank.json`*  
The drawer's blank task: `title` "" is allowed.

Request body:
```json
{}
```

Response body:
```json
{
  "id": 6,
  "title": "",
  "status": "todo",
  "assignee": null,
  "spoken_owner": null,
  "due_date": null,
  "due_phrase": null,
  "quote": null,
  "is_new": false,
  "unverified": false,
  "source": "manual",
  "assigned_on": "2026-11-05",
  "done_at": null,
  "created_at": "2026-11-05T14:57:22.343998Z",
  "from_meeting": false,
  "meeting": null,
  "can_update": true,
  "can_edit": true,
  "can_assign": true,
  "can_claim": true,
  "can_delete": true
}
```

`POST /api/tasks` as **chidi** → **422**  
*capture `66-create-off-team.json`*

Request body:
```json
{
  "assignee_user_id": 999999
}
```

Response body:
```json
{
  "error": {
    "code": "assignee_not_on_team",
    "message": "That person is not on your team."
  }
}
```

`POST /api/tasks` as **chidi** → **422**  
*capture `67-create-too-long.json`*

Request body:
```json
{
  "title": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
}
```

Response body:
```json
{
  "error": {
    "code": "validation_error",
    "message": "Some fields are missing or not valid.",
    "fields": [
      {
        "field": "title",
        "message": "String should have at most 200 characters"
      }
    ]
  }
}
```

### `DELETE /api/tasks/{id}`

204, no body. A task you made by hand is removed. A task read from a meeting is **not brought back** if the meeting's notes are read again; to you it is gone (404 afterwards). A member may only delete tasks they made (403 `team_forbidden` otherwise, shown above).

`DELETE /api/tasks/5` as **amina** → **204**  
*capture `69-manual-delete.json`*  
204, no body; a manual task is gone.

Response: no body.

`GET /api/tasks/5` as **amina** → **404**  
*capture `70-manual-after-delete.json`*

Response body:
```json
{
  "error": {
    "code": "not_found",
    "message": "No such task"
  }
}
```

`DELETE /api/tasks/2` as **dan** → **204**  
*capture `71-ai-delete.json`*  
An AI task is kept as an invisible tombstone so the same bullet does not bring it back.

Response: no body.

`GET /api/tasks/2` as **dan** → **404**  
*capture `72-ai-after-delete.json`*

Response body:
```json
{
  "error": {
    "code": "not_found",
    "message": "No such task"
  }
}
```

### `GET /api/meetings/{id}/tasks` and the meeting detail

The meeting page's Action items. The same list is also inside `GET /api/meetings/{id}` as `tasks`, with `tasks_count` next to it (and `tasks_count` on every row of `GET /api/meetings`, for the list's "8 tasks"). The order is the order of the notes' Action items. `assigned_people` is the number of different assignees: "8 tasks created, assigned to 3 people".

Before the worker has read the meeting (`extraction_status` is `pending` or `running`) the list is empty:

`GET /api/meetings/1/tasks` as **dan** → **200**  
*capture `12-meeting-tasks-before.json`*

Response body:
```json
{
  "tasks": [],
  "assigned_people": 0,
  "extraction_status": "pending"
}
```

After:

`GET /api/meetings/1/tasks` as **dan** → **200**  
*capture `18-meeting-tasks.json`*

Response body:
```json
{
  "tasks": [
    {
      "id": 1,
      "title": "Set up the tracking plan",
      "status": "todo",
      "assignee": {
        "user_id": 3,
        "name": "Chidi Eze",
        "is_you": false
      },
      "spoken_owner": "Chidi",
      "due_date": "2026-11-06",
      "due_phrase": "by Friday",
      "quote": "For analytics I will set up the tracking plan by Friday.",
      "is_new": true,
      "unverified": false,
      "source": "ai",
      "assigned_on": "2026-11-03",
      "done_at": null,
      "created_at": "2026-11-05T14:57:20.160822Z",
      "from_meeting": true,
      "meeting": {
        "id": 1,
        "title": "Google Meet nat-aaaa-bbb",
        "platform": "google_meet",
        "created_at": "2026-11-03T10:00:00Z"
      },
      "can_update": true,
      "can_edit": true,
      "can_assign": true,
      "can_claim": false,
      "can_delete": true
    },
    "<3 more task objects of the same shape omitted>"
  ],
  "assigned_people": 2,
  "extraction_status": "done"
}
```

The detail carries the same `tasks`:

`GET /api/meetings/1` as **dan** → **200**  
*capture `16-meeting-after.json`*  
Now `done`, tasks refined.

Response body:
```json
{
  "id": 1,
  "title": "Google Meet nat-aaaa-bbb",
  "platform": "google_meet",
  "native_id": "nat-aaaa-bbb",
  "status": "ready",
  "step": "done",
  "status_text": "Ready",
  "status_tone": "success",
  "template_id": 5,
  "template_name": "General",
  "created_at": "2026-11-03T10:00:00Z",
  "tasks_count": 4,
  "failure_reason": null,
  "retry_block": null,
  "retry_block_message": null,
  "can_cancel": false,
  "can_end": false,
  "can_retry": false,
  "can_regenerate": true,
  "join_attempts": 1,
  "max_join_attempts": 3,
  "bot_left_early": false,
  "speakers_incomplete": false,
  "refresh_after_seconds": null,
  "worker_stale": false,
  "join_retry_at": null,
  "extraction_status": "done",
  "notes": "<omitted>",
  "notes_html": "<omitted>",
  "transcript": "<omitted>",
  "translations": [],
  "duration_seconds": null,
  "participants": [
    "Dan",
    "Chidi",
    "Amina"
  ],
  "decisions_count": 1,
  "review_open_count": 2,
  "tasks": [
    {
      "id": 1,
      "title": "Set up the tracking plan",
      "status": "todo",
      "assignee": {
        "user_id": 3,
        "name": "Chidi Eze",
        "is_you": false
      },
      "spoken_owner": "Chidi",
      "due_date": "2026-11-06",
      "due_phrase": "by Friday",
      "quote": "For analytics I will set up the tracking plan by Friday.",
      "is_new": true,
      "unverified": false,
      "source": "ai",
      "assigned_on": "2026-11-03",
      "done_at": null,
      "created_at": "2026-11-05T14:57:20.160822Z",
      "from_meeting": true,
      "meeting": {
        "id": 1,
        "title": "Google Meet nat-aaaa-bbb",
        "platform": "google_meet",
        "created_at": "2026-11-03T10:00:00Z"
      },
      "can_update": true,
      "can_edit": true,
      "can_assign": true,
      "can_claim": false,
      "can_delete": true
    },
    {
      "id": 2,
      "title": "Share a draft of the dashboards",
      "status": "todo",
      "assignee": {
        "user_id": 4,
        "name": "Amina Bello",
        "is_you": false
      },
      "spoken_owner": "Amina",
      "due_date": "2026-11-11",
      "due_phrase": "on Wednesday of next week",
      "quote": "I'll share a draft on Wednesday of next week.",
      "is_new": true,
      "unverified": false,
      "source": "ai",
      "assigned_on": "2026-11-03",
      "done_at": null,
      "created_at": "2026-11-05T14:57:20.160822Z",
      "from_meeting": true,
      "meeting": {
        "id": 1,
        "title": "Google Meet nat-aaaa-bbb",
        "platform": "google_meet",
        "created_at": "2026-11-03T10:00:00Z"
      },
      "can_update": true,
      "can_edit": true,
      "can_assign": true,
      "can_claim": false,
      "can_delete": true
    },
    {
      "id": 3,
      "title": "Check the vendor site for the spelling",
      "status": "todo",
      "assignee": {
        "user_id": 4,
        "name": "Amina Bello",
        "is_you": false
      },
      "spoken_owner": "Amina",
      "due_date": null,
      "due_phrase": null,
      "quote": "I'll look at the vendor site.",
      "is_new": true,
      "unverified": false,
      "source": "ai",
      "assigned_on": "2026-11-03",
      "done_at": null,
      "created_at": "2026-11-05T14:57:20.160822Z",
      "from_meeting": true,
      "meeting": {
        "id": 1,
        "title": "Google Meet nat-aaaa-bbb",
        "platform": "google_meet",
        "created_at": "2026-11-03T10:00:00Z"
      },
      "can_update": true,
      "can_edit": true,
      "can_assign": true,
      "can_claim": false,
      "can_delete": true
    },
    {
      "id": 4,
      "title": "Ask finance to confirm the contract number",
      "status": "todo",
      "assignee": {
        "user_id": 3,
        "name": "Chidi Eze",
        "is_you": false
      },
      "spoken_owner": "Chidi",
      "due_date": "2026-11-04",
      "due_phrase": "tomorrow",
      "quote": "I will ask finance tomorrow.",
      "is_new": true,
      "unverified": false,
      "source": "ai",
      "assigned_on": "2026-11-03",
      "done_at": null,
      "created_at": "2026-11-05T14:57:20.160822Z",
      "from_meeting": true,
      "meeting": {
        "id": 1,
        "title": "Google Meet nat-aaaa-bbb",
        "platform": "google_meet",
        "created_at": "2026-11-03T10:00:00Z"
      },
      "can_update": true,
      "can_edit": true,
      "can_assign": true,
      "can_claim": false,
      "can_delete": true
    }
  ]
}
```

The meetings list carries `tasks_count`:

`GET /api/meetings` as **dan** → **200**  
*capture `19-meetings-list.json`*  
`tasks_count` on the list.

Response body:
```json
{
  "meetings": [
    {
      "id": 1,
      "title": "Google Meet nat-aaaa-bbb",
      "platform": "google_meet",
      "native_id": "nat-aaaa-bbb",
      "status": "ready",
      "step": "done",
      "status_text": "Ready",
      "status_tone": "success",
      "template_id": 5,
      "template_name": "General",
      "created_at": "2026-11-03T10:00:00Z",
      "tasks_count": 4
    }
  ],
  "has_more": false,
  "next_before_id": null,
  "worker_stale": true,
  "refresh_after_seconds": null
}
```

### `POST /api/meetings/{id}/tasks/extract`: older meetings

For a meeting from before tasks existed and older than 14 days, which the backfill leaves alone ("Extract tasks from this meeting"). Owner only (a teammate gets 404). 202 with the new status; the worker does it within a few minutes and the tasks are **not New and nobody is emailed**. **There is no designed button for this yet** (designer gap); the endpoint is ready.

`POST /api/meetings/1/tasks/extract` as **dan** → **202**  
*capture `79-extract-pending.json`*  
First: 202.

Response body:
```json
{
  "extraction_status": "pending"
}
```

While it is waiting or running:

`POST /api/meetings/1/tasks/extract` as **dan** → **409**  
*capture `80-extract-pending-again.json`*  
While it waits: 409 `extraction_pending`.

Response body:
```json
{
  "error": {
    "code": "extraction_pending",
    "message": "This meeting is already being read. Check back shortly."
  }
}
```

If the owner has switched tasks off:

`POST /api/meetings/1/tasks/extract` as **dan** → **409**  
*capture `82-extract-tasks-off.json`*  
Tasks switched off: 409 `tasks_off`.

Response body:
```json
{
  "error": {
    "code": "tasks_off",
    "message": "Turn on “Create tasks from action items” in Settings first."
  }
}
```

A teammate (not the owner):

`POST /api/meetings/1/tasks/extract` as **chidi** → **404**  
*capture `78-extract-not-owner.json`*  
A teammate: 404 (owner only).

Response body:
```json
{
  "error": {
    "code": "not_found",
    "message": "No such meeting"
  }
}
```

### `GET` and `PUT /api/settings/tasks`: the Settings Tasks card

Two switches, both on by default. "Create tasks from action items" is `auto_create_tasks`; "Email assignees" is `email_assignees`. Both are always sent together on PUT. The other two rows of the designer's card (Sync with your project tool, Post tasks to Slack) are W10 and have no API.

`GET /api/settings/tasks` as **dan** → **200**  
*capture `75-settings-tasks.json`*

Response body:
```json
{
  "auto_create_tasks": true,
  "email_assignees": true
}
```

`PUT /api/settings/tasks` as **dan** → **200**  
*capture `76-settings-tasks-put.json`*

Request body:
```json
{
  "auto_create_tasks": true,
  "email_assignees": false
}
```

Response body:
```json
{
  "auto_create_tasks": true,
  "email_assignees": false
}
```

`PUT /api/settings/tasks` as **dan** → **422**  
*capture `77-settings-tasks-invalid.json`*

Request body:
```json
{
  "auto_create_tasks": "yes"
}
```

Response body:
```json
{
  "error": {
    "code": "validation_error",
    "message": "Some fields are missing or not valid.",
    "fields": [
      {
        "field": "auto_create_tasks",
        "message": "Input should be a valid boolean"
      },
      {
        "field": "email_assignees",
        "message": "Field required"
      }
    ]
  }
}
```

Switching "Create tasks" off does **not** delete tasks that exist; it stops new ones being made. Switching "Email assignees" off also stops mail already queued.

## Who sees which task (the privacy rules the UI must respect)

| The task is | Who sees it |
|---|---|
| From a meeting | its assignee; the meeting's owner; anyone who can read the meeting (a meeting shared with the team) |
| Manual | everyone on the team |

**An assignee of a task from a private meeting sees the task and its quote, nothing else.** Look at Chidi's list: `from_meeting` is true, `meeting` is null, and the meeting itself is a 404. Do not render an "Open the meeting" link when `meeting` is null (the drawer's "From the meeting" box then shows the quote only, with no meeting name and no link). The privacy policy says this on purpose.

`GET /api/tasks` as **chidi** → **200**  
*capture `32-chidi-tasks.json`*  
Chidi sees only the tasks given to him: the task and its quote, no meeting (`meeting` null, `from_meeting` true).

Response body:
```json
{
  "tasks": [
    {
      "id": 4,
      "title": "Ask finance to confirm the contract number",
      "status": "todo",
      "assignee": {
        "user_id": 3,
        "name": "Chidi Eze",
        "is_you": true
      },
      "spoken_owner": "Chidi",
      "due_date": "2026-11-04",
      "due_phrase": "tomorrow",
      "quote": "I will ask finance tomorrow.",
      "is_new": true,
      "unverified": false,
      "source": "ai",
      "assigned_on": "2026-11-03",
      "done_at": null,
      "created_at": "2026-11-05T14:57:20.160822Z",
      "from_meeting": true,
      "meeting": null,
      "can_update": true,
      "can_edit": false,
      "can_assign": false,
      "can_claim": false,
      "can_delete": false
    },
    {
      "id": 1,
      "title": "Set up the tracking plan",
      "status": "todo",
      "assignee": {
        "user_id": 3,
        "name": "Chidi Eze",
        "is_you": true
      },
      "spoken_owner": "Chidi",
      "due_date": "2026-11-06",
      "due_phrase": "by Friday",
      "quote": "For analytics I will set up the tracking plan by Friday.",
      "is_new": true,
      "unverified": false,
      "source": "ai",
      "assigned_on": "2026-11-03",
      "done_at": null,
      "created_at": "2026-11-05T14:57:20.160822Z",
      "from_meeting": true,
      "meeting": null,
      "can_update": true,
      "can_edit": false,
      "can_assign": false,
      "can_claim": false,
      "can_delete": false
    }
  ],
  "people": [
    {
      "user_id": 2,
      "name": "Dan Okoro",
      "is_you": false
    },
    {
      "user_id": 3,
      "name": "Chidi Eze",
      "is_you": true
    },
    {
      "user_id": 4,
      "name": "Amina Bello",
      "is_you": false
    }
  ],
  "attention": {
    "needs_owner": 0,
    "unverified": 0
  },
  "has_more": false,
  "next_before_id": null
}
```

`GET /api/meetings/1` as **chidi** → **404**  
*capture `35-chidi-meeting.json`*  
The meeting itself stays private: 404.

Response body:
```json
{
  "error": {
    "code": "not_found",
    "message": "No such meeting"
  }
}
```

A user from another team sees nothing, and every id is a 404:

`GET /api/tasks` as **eve** → **200**  
*capture `37-eve-tasks.json`*  
Another team: nothing.

Response body:
```json
{
  "tasks": [],
  "people": [
    {
      "user_id": 5,
      "name": "Eve Outsider",
      "is_you": true
    }
  ],
  "attention": {
    "needs_owner": 0,
    "unverified": 0
  },
  "has_more": false,
  "next_before_id": null
}
```

`GET /api/tasks/4` as **eve** → **404**  
*capture `38-eve-task.json`*  
404, never 403.

Response body:
```json
{
  "error": {
    "code": "not_found",
    "message": "No such task"
  }
}
```

`PATCH /api/tasks/4` as **eve** → **404**  
*capture `39-eve-patch.json`*

Request body:
```json
{
  "status": "done"
}
```

Response body:
```json
{
  "error": {
    "code": "not_found",
    "message": "No such task"
  }
}
```

`DELETE /api/tasks/4` as **eve** → **404**  
*capture `40-eve-delete.json`*

Response body:
```json
{
  "error": {
    "code": "not_found",
    "message": "No such task"
  }
}
```

Once the meeting is shared with the team, a teammate gets `meeting` filled in. **NOT LIVE:** there is no "Share with team" route yet (W3), so the script shared the meeting with SQL; the response shape is real:

`GET /api/tasks/4` as **chidi** → **200**  
*capture `57-member-sees-meeting.json`*  
Now the meeting is shared, `meeting` is filled in.

Response body:
```json
{
  "id": 4,
  "title": "Ask finance to confirm the contract number",
  "status": "doing",
  "assignee": {
    "user_id": 3,
    "name": "Chidi Eze",
    "is_you": true
  },
  "spoken_owner": "Chidi",
  "due_date": "2026-11-09",
  "due_phrase": "tomorrow",
  "quote": "I will ask finance tomorrow.",
  "is_new": false,
  "unverified": false,
  "source": "ai",
  "assigned_on": "2026-11-03",
  "done_at": null,
  "created_at": "2026-11-05T14:57:20.160822Z",
  "from_meeting": true,
  "meeting": {
    "id": 1,
    "title": "Google Meet nat-aaaa-bbb",
    "platform": "google_meet",
    "created_at": "2026-11-03T10:00:00Z"
  },
  "can_update": true,
  "can_edit": false,
  "can_assign": false,
  "can_claim": false,
  "can_delete": false
}
```

An admin sees every task of a shared meeting:

`GET /api/meetings/1/tasks` as **amina** → **200**  
*capture `50-admin-meeting-tasks.json`*

Response body:
```json
{
  "tasks": [
    {
      "id": 1,
      "title": "Set up the tracking plan",
      "status": "todo",
      "assignee": {
        "user_id": 3,
        "name": "Chidi Eze",
        "is_you": false
      },
      "spoken_owner": "Chidi",
      "due_date": "2026-11-06",
      "due_phrase": "by Friday",
      "quote": "For analytics I will set up the tracking plan by Friday.",
      "is_new": true,
      "unverified": false,
      "source": "ai",
      "assigned_on": "2026-11-03",
      "done_at": null,
      "created_at": "2026-11-05T14:57:20.160822Z",
      "from_meeting": true,
      "meeting": {
        "id": 1,
        "title": "Google Meet nat-aaaa-bbb",
        "platform": "google_meet",
        "created_at": "2026-11-03T10:00:00Z"
      },
      "can_update": true,
      "can_edit": true,
      "can_assign": true,
      "can_claim": false,
      "can_delete": true
    },
    "<3 more task objects of the same shape omitted>"
  ],
  "assigned_people": 2,
  "extraction_status": "done"
}
```

## The assignee email ("New tasks assigned")

One mail per assignee per meeting, sent by the background worker after the extraction finishes, never to the meeting's owner for their own tasks, never for backfilled meetings, never for a task nobody owns, and only while the owner's "Email assignees" is on and the recipient's email preference allows it. Nothing for the frontend to call. What it means for the UI:

* A person can receive the mail for a task they then find is not theirs: the mail says "Not yours? Open the task to reassign it." The drawer's Assignee select is that path.
* The mail lists only tasks still `todo`, still New, and still assigned to them when it is sent.
* If the meeting is private to its owner, the mail does not carry its title: it says "A meeting of <owner>" (as below). Once the meeting is shared with the team it carries the title and "recorded by <owner>".

The outbox rows (ids only, never text) and what the mail provider would be handed (**NOT LIVE** for delivery: the script uses a capturing mailer, not Resend; the message is built by the real code). The "Due ..." words are worked out against the day the script ran (a real clock), not against the sample meeting's date, so "Due tomorrow" next to 2026-11-04 is an artefact of running the script later:

*capture `29-mail-outbox.json`*  
`mail_outbox` after the extraction: one row per assignee, ids only; none for Dan (the owner) and none for the task nobody owns.

```json
[
  {
    "to": "chidi@example.test",
    "template": "tasks_assigned",
    "payload": {
      "user_id": 3,
      "meeting_id": 1
    },
    "status": "pending"
  },
  {
    "to": "amina@example.test",
    "template": "tasks_assigned",
    "payload": {
      "user_id": 4,
      "meeting_id": 1
    },
    "status": "pending"
  }
]
```

*capture `30-mail-rendered.json`*  
What the mail provider would be handed (CAPTURING MAILER, not Resend).

```json
[
  {
    "to": "chidi@example.test",
    "subject": "You have 2 new tasks: A meeting of Dan Okoro",
    "text": "A meeting of Dan Okoro · 03 Nov 2026\n\nYou have 2 new tasks\n\nHi Chidi, these action items from the meeting were assigned … <330 more characters omitted> …",
    "html": "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-sca… <2660 more characters omitted> …"
  },
  {
    "to": "amina@example.test",
    "subject": "You have 2 new tasks: A meeting of Dan Okoro",
    "text": "A meeting of Dan Okoro · 03 Nov 2026\n\nYou have 2 new tasks\n\nHi Amina, these action items from the meeting were assigned … <319 more characters omitted> …",
    "html": "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-sca… <2652 more characters omitted> …"
  }
]
```

Asking to mail the same person about the same meeting again queues nothing (this is the unique index, and the same thing a re-run of the extraction does):

*capture `31-mail-second-attempt.json`*  
Queueing the same person for the same meeting again.

```json
{
  "queued": 0
}
```

## State machine and polling

`extraction_status` on the meeting (and on `GET /api/meetings/{id}/tasks`) says where the reading stands: `pending` and `running` are one state for the screen ("Reading the action items..."), `done` is finished, `failed` gave up after three tries, `skipped` is an older meeting that only has decisions. Meetings refresh on their own cadence already (`refresh_after_seconds`); while the status is `pending` or `running`, ask for `/meetings/{id}/tasks` again every 15 to 30 seconds and stop on `done`, `failed` or `skipped`. The tasks screen needs no polling (reload on focus is enough).

**Designer gap:** there is no designed "reading..." or "could not read" state for the Action items block (the checklist lists it already). The statuses are ready.

When the model call fails, the tasks the notes say are still created straight from the notes' bullets: owner and due phrase read by code (the owner only if the name is in the transcript), `unverified` true and no quote. The meeting stays `pending` and is retried after five minutes, up to three times. **NOT LIVE:** the failure below is a stand-in (the client raised a dropped-connection error) because the session that captured this had no OpenAI key:

`GET /api/meetings/1` as **dan** → **200**  
*capture `14-meeting-after-failed-call.json`*  
STAND-IN failure (a dropped connection). The tasks the notes say are there already, `unverified` with no quote, and the meeting retries in 5 minutes (`pending`).

Response body:
```json
{
  "id": 1,
  "title": "Google Meet nat-aaaa-bbb",
  "platform": "google_meet",
  "native_id": "nat-aaaa-bbb",
  "status": "ready",
  "step": "done",
  "status_text": "Ready",
  "status_tone": "success",
  "template_id": 5,
  "template_name": "General",
  "created_at": "2026-11-03T10:00:00Z",
  "tasks_count": 4,
  "failure_reason": null,
  "retry_block": null,
  "retry_block_message": null,
  "can_cancel": false,
  "can_end": false,
  "can_retry": false,
  "can_regenerate": true,
  "join_attempts": 1,
  "max_join_attempts": 3,
  "bot_left_early": false,
  "speakers_incomplete": false,
  "refresh_after_seconds": null,
  "worker_stale": false,
  "join_retry_at": null,
  "extraction_status": "pending",
  "notes": "<omitted>",
  "notes_html": "<omitted>",
  "transcript": "<omitted>",
  "translations": [],
  "duration_seconds": null,
  "participants": [
    "Dan",
    "Chidi",
    "Amina"
  ],
  "decisions_count": 1,
  "review_open_count": 2,
  "tasks": [
    {
      "id": 1,
      "title": "Set up the tracking plan by Friday.",
      "status": "todo",
      "assignee": {
        "user_id": 3,
        "name": "Chidi Eze",
        "is_you": false
      },
      "spoken_owner": "Chidi",
      "due_date": null,
      "due_phrase": null,
      "quote": null,
      "is_new": true,
      "unverified": true,
      "source": "ai",
      "assigned_on": "2026-11-03",
      "done_at": null,
      "created_at": "2026-11-05T14:57:20.160822Z",
      "from_meeting": true,
      "meeting": {
        "id": 1,
        "title": "Google Meet nat-aaaa-bbb",
        "platform": "google_meet",
        "created_at": "2026-11-03T10:00:00Z"
      },
      "can_update": true,
      "can_edit": true,
      "can_assign": true,
      "can_claim": false,
      "can_delete": true
    },
    {
      "id": 2,
      "title": "Share a draft of the dashboards on Wednesday of next week.",
      "status": "todo",
      "assignee": {
        "user_id": 4,
        "name": "Amina Bello",
        "is_you": false
      },
      "spoken_owner": "Amina",
      "due_date": null,
      "due_phrase": null,
      "quote": null,
      "is_new": true,
      "unverified": true,
      "source": "ai",
      "assigned_on": "2026-11-03",
      "done_at": null,
      "created_at": "2026-11-05T14:57:20.160822Z",
      "from_meeting": true,
      "meeting": {
        "id": 1,
        "title": "Google Meet nat-aaaa-bbb",
        "platform": "google_meet",
        "created_at": "2026-11-03T10:00:00Z"
      },
      "can_update": true,
      "can_edit": true,
      "can_assign": true,
      "can_claim": false,
      "can_delete": true
    },
    {
      "id": 3,
      "title": "Check the vendor site for the spelling.",
      "status": "todo",
      "assignee": {
        "user_id": 4,
        "name": "Amina Bello",
        "is_you": false
      },
      "spoken_owner": "Amina",
      "due_date": null,
      "due_phrase": null,
      "quote": null,
      "is_new": true,
      "unverified": true,
      "source": "ai",
      "assigned_on": "2026-11-03",
      "done_at": null,
      "created_at": "2026-11-05T14:57:20.160822Z",
      "from_meeting": true,
      "meeting": {
        "id": 1,
        "title": "Google Meet nat-aaaa-bbb",
        "platform": "google_meet",
        "created_at": "2026-11-03T10:00:00Z"
      },
      "can_update": true,
      "can_edit": true,
      "can_assign": true,
      "can_claim": false,
      "can_delete": true
    },
    {
      "id": 4,
      "title": "Ask finance tomorrow to confirm the contract number.",
      "status": "todo",
      "assignee": {
        "user_id": 3,
        "name": "Chidi Eze",
        "is_you": false
      },
      "spoken_owner": "Chidi",
      "due_date": null,
      "due_phrase": null,
      "quote": null,
      "is_new": true,
      "unverified": true,
      "source": "ai",
      "assigned_on": "2026-11-03",
      "done_at": null,
      "created_at": "2026-11-05T14:57:20.160822Z",
      "from_meeting": true,
      "meeting": {
        "id": 1,
        "title": "Google Meet nat-aaaa-bbb",
        "platform": "google_meet",
        "created_at": "2026-11-03T10:00:00Z"
      },
      "can_update": true,
      "can_edit": true,
      "can_assign": true,
      "can_claim": false,
      "can_delete": true
    }
  ]
}
```

After the retry (a recorded `gpt-6-luna` reply for the same synthetic meeting, **NOT LIVE** for the model call itself), the same four tasks (same ids) are refined in place:

`GET /api/meetings/1` as **dan** → **200**  
*capture `16-meeting-after.json`*  
Now `done`, tasks refined.

Response body:
```json
{
  "id": 1,
  "title": "Google Meet nat-aaaa-bbb",
  "platform": "google_meet",
  "native_id": "nat-aaaa-bbb",
  "status": "ready",
  "step": "done",
  "status_text": "Ready",
  "status_tone": "success",
  "template_id": 5,
  "template_name": "General",
  "created_at": "2026-11-03T10:00:00Z",
  "tasks_count": 4,
  "failure_reason": null,
  "retry_block": null,
  "retry_block_message": null,
  "can_cancel": false,
  "can_end": false,
  "can_retry": false,
  "can_regenerate": true,
  "join_attempts": 1,
  "max_join_attempts": 3,
  "bot_left_early": false,
  "speakers_incomplete": false,
  "refresh_after_seconds": null,
  "worker_stale": false,
  "join_retry_at": null,
  "extraction_status": "done",
  "notes": "<omitted>",
  "notes_html": "<omitted>",
  "transcript": "<omitted>",
  "translations": [],
  "duration_seconds": null,
  "participants": [
    "Dan",
    "Chidi",
    "Amina"
  ],
  "decisions_count": 1,
  "review_open_count": 2,
  "tasks": [
    {
      "id": 1,
      "title": "Set up the tracking plan",
      "status": "todo",
      "assignee": {
        "user_id": 3,
        "name": "Chidi Eze",
        "is_you": false
      },
      "spoken_owner": "Chidi",
      "due_date": "2026-11-06",
      "due_phrase": "by Friday",
      "quote": "For analytics I will set up the tracking plan by Friday.",
      "is_new": true,
      "unverified": false,
      "source": "ai",
      "assigned_on": "2026-11-03",
      "done_at": null,
      "created_at": "2026-11-05T14:57:20.160822Z",
      "from_meeting": true,
      "meeting": {
        "id": 1,
        "title": "Google Meet nat-aaaa-bbb",
        "platform": "google_meet",
        "created_at": "2026-11-03T10:00:00Z"
      },
      "can_update": true,
      "can_edit": true,
      "can_assign": true,
      "can_claim": false,
      "can_delete": true
    },
    {
      "id": 2,
      "title": "Share a draft of the dashboards",
      "status": "todo",
      "assignee": {
        "user_id": 4,
        "name": "Amina Bello",
        "is_you": false
      },
      "spoken_owner": "Amina",
      "due_date": "2026-11-11",
      "due_phrase": "on Wednesday of next week",
      "quote": "I'll share a draft on Wednesday of next week.",
      "is_new": true,
      "unverified": false,
      "source": "ai",
      "assigned_on": "2026-11-03",
      "done_at": null,
      "created_at": "2026-11-05T14:57:20.160822Z",
      "from_meeting": true,
      "meeting": {
        "id": 1,
        "title": "Google Meet nat-aaaa-bbb",
        "platform": "google_meet",
        "created_at": "2026-11-03T10:00:00Z"
      },
      "can_update": true,
      "can_edit": true,
      "can_assign": true,
      "can_claim": false,
      "can_delete": true
    },
    {
      "id": 3,
      "title": "Check the vendor site for the spelling",
      "status": "todo",
      "assignee": {
        "user_id": 4,
        "name": "Amina Bello",
        "is_you": false
      },
      "spoken_owner": "Amina",
      "due_date": null,
      "due_phrase": null,
      "quote": "I'll look at the vendor site.",
      "is_new": true,
      "unverified": false,
      "source": "ai",
      "assigned_on": "2026-11-03",
      "done_at": null,
      "created_at": "2026-11-05T14:57:20.160822Z",
      "from_meeting": true,
      "meeting": {
        "id": 1,
        "title": "Google Meet nat-aaaa-bbb",
        "platform": "google_meet",
        "created_at": "2026-11-03T10:00:00Z"
      },
      "can_update": true,
      "can_edit": true,
      "can_assign": true,
      "can_claim": false,
      "can_delete": true
    },
    {
      "id": 4,
      "title": "Ask finance to confirm the contract number",
      "status": "todo",
      "assignee": {
        "user_id": 3,
        "name": "Chidi Eze",
        "is_you": false
      },
      "spoken_owner": "Chidi",
      "due_date": "2026-11-04",
      "due_phrase": "tomorrow",
      "quote": "I will ask finance tomorrow.",
      "is_new": true,
      "unverified": false,
      "source": "ai",
      "assigned_on": "2026-11-03",
      "done_at": null,
      "created_at": "2026-11-05T14:57:20.160822Z",
      "from_meeting": true,
      "meeting": {
        "id": 1,
        "title": "Google Meet nat-aaaa-bbb",
        "platform": "google_meet",
        "created_at": "2026-11-03T10:00:00Z"
      },
      "can_update": true,
      "can_edit": true,
      "can_assign": true,
      "can_claim": false,
      "can_delete": true
    }
  ]
}
```

The ledger of the two calls (`ai_runs`):

*capture `15-worker-ai-runs.json`*  
`ai_runs` after both attempts.

```json
[
  {
    "kind": "extract",
    "status": "failed",
    "prompt_version": "extract-v1",
    "input_tokens": null,
    "output_tokens": null,
    "error": "APIConnectionError"
  },
  {
    "kind": "extract",
    "status": "ok",
    "prompt_version": "extract-v1",
    "input_tokens": 1184,
    "output_tokens": 702,
    "error": null
  }
]
```

## What a re-run does to tasks (rewrite the notes)

Matched by the words of the notes' bullet. A task that was not touched is refreshed in place (same id). A task someone moved, ticked, edited or assigned is never overwritten or removed. A task that was deleted does not come back. A bullet that vanished from the rewritten notes removes its untouched task. A new bullet adds a task. **Nobody is emailed again** for the same meeting (the unique index). Captured after the script rewrote the notes ("rewrite the notes" re-queues the meeting) with the same recorded reply: the ids are the ones from before, the edited and moved ones are untouched and the deleted task did not return:

`GET /api/tasks` as **dan** → **200**  
*capture `73-tasks-after-rerun.json`*  
After 'rewrite the notes': same ids; the moved and edited tasks are untouched; the deleted one did not come back.

Response body:
```json
{
  "tasks": [
    {
      "id": 6,
      "title": "",
      "status": "todo",
      "assignee": null,
      "spoken_owner": null,
      "due_date": null,
      "due_phrase": null,
      "quote": null,
      "is_new": false,
      "unverified": false,
      "source": "manual",
      "assigned_on": "2026-11-05",
      "done_at": null,
      "created_at": "2026-11-05T14:57:22.343998Z",
      "from_meeting": false,
      "meeting": null,
      "can_update": true,
      "can_edit": true,
      "can_assign": true,
      "can_claim": true,
      "can_delete": true
    },
    "<3 more task objects of the same shape omitted>"
  ],
  "people": [
    {
      "user_id": 2,
      "name": "Dan Okoro",
      "is_you": true
    },
    {
      "user_id": 3,
      "name": "Chidi Eze",
      "is_you": false
    },
    {
      "user_id": 4,
      "name": "Amina Bello",
      "is_you": false
    }
  ],
  "attention": {
    "needs_owner": 1,
    "unverified": 0
  },
  "has_more": false,
  "next_before_id": null
}
```

*capture `74-mail-after-rerun.json`*  
The outbox after the re-run: still one mail per assignee.

```json
[
  {
    "to": "chidi@example.test",
    "status": "sent"
  },
  {
    "to": "amina@example.test",
    "status": "sent"
  }
]
```

## Backfill

`scripts/backfill_extraction.py` (run once after deploy, by the lead or Adebayo) queues meetings from the last 14 days that were read before tasks existed. Their tasks are **not New** and **nobody is emailed**:

*capture `84-backfill.json`*  
`workspace.backfill` (what scripts/backfill_extraction.py runs).

```json
{
  "queued": 0,
  "decisions_only": 0,
  "tasks_queued": 2
}
```

*capture `85-backfill-mail.json`*  
Newest outbox id before and after the backfilled meeting was read: nobody is mailed.

```json
{
  "before": 2,
  "after": 2
}
```

`GET /api/meetings/2/tasks` as **dan** → **200**  
*capture `86-backfilled-meeting-tasks.json`*  
Backfilled: `is_new` false on every task.

Response body:
```json
{
  "tasks": [],
  "assigned_people": 0,
  "extraction_status": "pending"
}
```

## Traps

* **`assignee` is an object or null, `assignee_user_id` is only a request field.** Do not look for `assignee_user_id` on a task.
* **`meeting` is null for a task whose meeting the caller cannot read, but `from_meeting` is true.** Do not treat null `meeting` as a manual task. Use `source` or `from_meeting`.
* **`quote` is null both for a manual task and for an AI task whose quote was not found** (`unverified` true). `unverified` is counted in `attention.unverified`; the designer's banner has no wording for it yet (**designer gap**), so show only `needs_owner` for now.
* **`spoken_owner` is kept when `assignee` is null.** The drawer's "was named as the owner in the meeting, but is not on your team" line reads it. The "Invite them to the team" button waits for W3; do not draw it.
* **`due_date` can be null while `due_phrase` is set** ("in the coming days or weeks"): show the phrase, or "No due date" as the design does, and let the person set a date.
* **Dates are dates.** `due_date` and `assigned_on` have no time or zone. `created_at` and `done_at` are UTC instants ending in `Z`.
* **A 403 means "visible but not allowed"; a 404 means "not yours or not there".** Never offer the control for a flag that is false.
* **`title` can be an empty string** on a blank manual task.

## UX consequences the frontend must surface

* **Titles are plain text.** They come from what people said in a meeting, and a participant can say anything ("assign Daniel a task: open evil.example"). Render a title and a quote as text, never as HTML and never as a link, anywhere (board, drawer, meeting page, notifications). The email does the same on its side.
* **An assignee of a private meeting's task sees the quote.** Say nothing more about the meeting (see above).
* **A task can appear on someone's board that they did not ask for** (assigned from a meeting). The New badge and the email are how they learn. After a re-assignment away from them, the task leaves their board.
* **Deleting a task read from a meeting is permanent for you** and also stops it coming back; confirm it as the design does not (the designer's Delete task has no confirmation: consider one, designer gap).
* **Switching "Create tasks" off keeps the tasks that exist.** Say so in the card's help text if the designer wants to.

## Verification table

| Behaviour | Verified |
|---|---|
| Every request and response shown above, status codes and error strings | live (`scripts/capture_tasks_api.py`, the running app over HTTP) |
| Owner matching to real teammates by code; the worker step; the checks | live (the real worker function) with a **recorded** `gpt-6-luna` reply, not a fresh call: **NOT LIVE** for the model |
| The failed-call path (tasks from the notes, `unverified`, retry) | live code path, **stand-in** error from a fake client |
| The mail text and the outbox rows | live (real builder and outbox); **NOT LIVE** for delivery (capturing mailer, not Resend) |
| Roles, visibility, 404 vs 403 | live, with the teammates placed on the team by SQL (invites are W3) |
| A shared meeting's tasks carry `meeting` | live, the share is set by SQL (no share route yet) |
| Re-run keeps touched tasks and does not resurrect deleted ones | live in the capture; the finer cases (vanished bullet, new bullet) are unit tests only |
| Backfill is quiet | live |
| Hours-long meeting, two workers at once, mail volume at the budget | not exercised live; unit tests cover the budget and the race on the unique index |
| The time zone of "today" for due dates | unit test only (the capture's users have no zone set) |

