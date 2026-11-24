# Frontend integration guide: meeting recipes, the meeting picker (slice S5)

What the **Make** dialog needs to let a person build a cross-meeting draft (Status report, Client briefing, Revision plan) from several meetings: preview which meetings a choice means, then make the draft. **Every payload, status code and error string below is pasted from a real response** captured by `scripts/capture_recipes_cross_meeting.py` against the running app (files in `docs/fe-captures/recipes-cross-meeting/`, named in each block; run the script again to re-derive any of them). The draft endpoints themselves are in `docs/fe-integration-guide-recipes.md`; this guide adds the picker and what changes on a draft made from one.

**What was NOT real in the captures** (also in the verification table):

- **The model.** A fake stood in for the OpenAI client. It cites the meeting, decision and task ids it finds in the prompt. A real model's wording will differ; the shape of every response body will not.
- **The three cross-meeting starters.** `app/recipes/library.py` is slice S3's (PR #267, not merged when this was captured). The capture registered a stand-in `status_report` (scope `many`) and `follow_up_email` (scope `one`). The real `status_report`, `client_briefing` and `revision_plan` keys are S3's.
- **The screen.** `web/components/app/meeting-picker.tsx` is tested with the API mocked (bodies shaped like the captures). It was **not looked at in a browser** and is **not mounted** in the Make dialog yet: the dialog is S3's, and mounting waits for #267.
- **`plan_inactive` (403) and `recipe_limit` (429)** on the draft request are the draft guide's, not re-run here.

## What it is, in your terms

Some recipes read **several meetings** (their `scope` is `many`). For those, the Make dialog shows a **Meetings** step before **Make draft**. The person chooses ONE way to say which meetings: a recurring meeting, a project, a sprint, a date range, or meetings ticked by hand. As soon as the choice is complete the dialog asks the server what it means and shows the answer: the meetings (title and day), how many were **left out** because the person cannot read them, and whether the **12-meeting cap** cut some. Then **Make draft** sends those meeting ids, and the choice itself, to `POST /api/documents`.

`MeetingPicker` (`web/components/app/meeting-picker.tsx`) is that step. It reports `{ picker, meetingIds, leftOut }` through `onChange`: send `meetingIds` as `meeting_ids` and `picker` as `picker`; `picker` is null (and Make stays disabled) until the choice is complete and answered.

## Endpoints

All need a login. `POST` needs the `X-CSRF-Token` header. The team is the optional `X-Team-Id` header (the active team, as on `GET /api/meetings`; `api.recipes.resolvePicker(spec, team)` sends it).

### 1. `POST /api/recipes/picker/resolve`: preview a choice

A read made as a POST (the body is a choice). Body (`PickerSpec`): **exactly one** of `series_id`, `project_id`, `sprint_id`, a date range (`from_day` and/or `to_day`, `YYYY-MM-DD`, both days included), `meeting_ids` (1 to 100, hand-picked); `tz` is optional and only used with a date range (see "Dates" below). The answer (`PickerPreview`): `meetings` (at most `max_meetings` = 12, **newest first**, each `{id, title, day}`), `left_out_count`, `left_out_message`, `capped`, `max_meetings`.

What each way means (all through the rules the app already has, never a second one):

| Way | The meetings are |
|---|---|
| `series_id` | the recurring meeting's own meetings |
| `project_id` | the meetings the project's tasks came from, plus the meetings of recurring meetings linked to the project |
| `sprint_id` | the meetings its tasks are planned into, plus the active team's meetings held on the sprint's days |
| `from_day` / `to_day` | the active team's meetings made on those days (the Meetings list's own filter) |
| `meeting_ids` | exactly those, if the person may read them |

Only meetings that are **ready and have notes or a transcript** are offered; one still processing is not shown and not counted (it is not hidden, it is not there yet).

`POST /api/recipes/picker/resolve`: A recurring meeting with 5 meetings; Daniel can read 3 → **200** *(capture `07-resolve-series.json`)*

```json
{
  "series_id": 1
}
```

```json
{
  "meetings": [
    {
      "id": 3,
      "title": "Review",
      "day": "2026-11-16"
    },
    {
      "id": 2,
      "title": "Planning",
      "day": "2026-11-09"
    },
    {
      "id": 1,
      "title": "Kick-off",
      "day": "2026-11-02"
    }
  ],
  "left_out_count": 2,
  "left_out_message": "2 meetings were left out because you cannot read them or they are not ready.",
  "capped": false,
  "max_meetings": 12
}
```

`left_out_count: 2` here is the two private meetings of Ada's that Daniel cannot read. They are **not named and their ids are not in the answer**; the sentence is all there is.

`POST /api/recipes/picker/resolve`: A recurring meeting with 14 meetings: 12 come back → **200** *(capture `08-resolve-series-capped.json`)*

```json
{
  "series_id": 2
}
```

```json
{
  "meetings": [
    {
      "id": 19,
      "title": "Stand-up 14",
      "day": "2026-12-05"
    },
    {
      "id": 18,
      "title": "Stand-up 13",
      "day": "2026-12-04"
    },
    {
      "id": 17,
      "title": "Stand-up 12",
      "day": "2026-12-03"
    },
    {
      "id": 16,
      "title": "Stand-up 11",
      "day": "2026-12-02"
    },
    {
      "id": 15,
      "title": "Stand-up 10",
      "day": "2026-12-01"
    },
    {
      "id": 14,
      "title": "Stand-up 09",
      "day": "2026-11-30"
    },
    {
      "id": 13,
      "title": "Stand-up 08",
      "day": "2026-11-29"
    },
    {
      "id": 12,
      "title": "Stand-up 07",
      "day": "2026-11-28"
    },
    {
      "id": 11,
      "title": "Stand-up 06",
      "day": "2026-11-27"
    },
    {
      "id": 10,
      "title": "Stand-up 05",
      "day": "2026-11-26"
    },
    {
      "id": 9,
      "title": "Stand-up 04",
      "day": "2026-11-25"
    },
    {
      "id": 8,
      "title": "Stand-up 03",
      "day": "2026-11-24"
    }
  ],
  "left_out_count": 0,
  "left_out_message": null,
  "capped": true,
  "max_meetings": 12
}
```

`capped: true`: tell the person the oldest were cut.

`POST /api/recipes/picker/resolve` (header `X-Team-Id: 2`): A project → **200** *(capture `09-resolve-project.json`)*

```json
{
  "project_id": 1
}
```

```json
{
  "meetings": [
    {
      "id": 2,
      "title": "Planning",
      "day": "2026-11-09"
    },
    {
      "id": 1,
      "title": "Kick-off",
      "day": "2026-11-02"
    }
  ],
  "left_out_count": 1,
  "left_out_message": "1 meeting was left out because you cannot read it or it is not ready.",
  "capped": false,
  "max_meetings": 12
}
```


`POST /api/recipes/picker/resolve` (header `X-Team-Id: 2`): A sprint → **200** *(capture `10-resolve-sprint.json`)*

```json
{
  "sprint_id": 1
}
```

```json
{
  "meetings": [
    {
      "id": 6,
      "title": "Stand-up 01",
      "day": "2026-11-22"
    },
    {
      "id": 3,
      "title": "Review",
      "day": "2026-11-16"
    },
    {
      "id": 1,
      "title": "Kick-off",
      "day": "2026-11-02"
    }
  ],
  "left_out_count": 0,
  "left_out_message": null,
  "capped": false,
  "max_meetings": 12
}
```


`POST /api/recipes/picker/resolve` (header `X-Team-Id: 2`): A date range → **200** *(capture `11-resolve-dates.json`)*

```json
{
  "from_day": "2026-11-02",
  "to_day": "2026-11-09",
  "tz": "Africa/Lagos"
}
```

```json
{
  "meetings": [
    {
      "id": 2,
      "title": "Planning",
      "day": "2026-11-09"
    },
    {
      "id": 1,
      "title": "Kick-off",
      "day": "2026-11-02"
    }
  ],
  "left_out_count": 0,
  "left_out_message": null,
  "capped": false,
  "max_meetings": 12
}
```

`day` is the day in the person's **stored** time zone, else the `tz` sent (send the browser's: `Intl.DateTimeFormat().resolvedOptions().timeZone`, as the Meetings list does). `left_out_count` is always 0 for a date range: it is a list of what the person may read.

`POST /api/recipes/picker/resolve`: Only `from_day`: that day or later → **200** *(capture `12-resolve-from-only.json`)*

```json
{
  "from_day": "2026-12-20"
}
```

```json
{
  "meetings": [
    {
      "id": 20,
      "title": "Outside the range",
      "day": "2027-01-01"
    }
  ],
  "left_out_count": 0,
  "left_out_message": null,
  "capped": false,
  "max_meetings": 12
}
```


`POST /api/recipes/picker/resolve`: Nothing matches → **200** *(capture `13-resolve-nothing.json`)*

```json
{
  "from_day": "2030-01-01"
}
```

```json
{
  "meetings": [],
  "left_out_count": 0,
  "left_out_message": null,
  "capped": false,
  "max_meetings": 12
}
```

An empty `meetings` is a normal 200: say so, and do not enable Make.

`POST /api/recipes/picker/resolve`: Hand-picked: one private meeting and one id that does not exist → **200** *(capture `14-resolve-hand-picked.json`)*

```json
{
  "meeting_ids": [
    1,
    4,
    3,
    987654
  ]
}
```

```json
{
  "meetings": [
    {
      "id": 3,
      "title": "Review",
      "day": "2026-11-16"
    },
    {
      "id": 1,
      "title": "Kick-off",
      "day": "2026-11-02"
    }
  ],
  "left_out_count": 2,
  "left_out_message": "2 meetings were left out because you cannot read them or they are not ready.",
  "capped": false,
  "max_meetings": 12
}
```

A meeting the person cannot read and one that does not exist look the same on purpose: both are left out and counted.

**Errors** (project error shape `{"error": {"code", "message"}}`):

- Not logged in: body `{"series_id": 1}` → **401** `{"error": {"code": "not_authenticated", "message": "Log in first."}}` *(capture `06-resolve-logged-out.json`)*
- No way named: body `{}` → **422** `{"error": {"code": "validation_error", "message": "Some fields are missing or not valid.", "fields": [{"field": "body", "message": "Value error, Choose exactly one of series_id, project_id, sprint_id, a date range or meeting_ids."}]}}` *(capture `15-resolve-none.json`)*
- Two ways named: body `{"series_id": 1, "project_id": 1}` → **422** `{"error": {"code": "validation_error", "message": "Some fields are missing or not valid.", "fields": [{"field": "body", "message": "Value error, Choose exactly one of series_id, project_id, sprint_id, a date range or meeting_ids."}]}}` *(capture `16-resolve-two-ways.json`)*
- `from_day` after `to_day`: body `{"from_day": "2026-11-09", "to_day": "2026-11-02"}` → **422** `{"error": {"code": "invalid_date_range", "message": "From must be on or before To. Swap the two dates."}}` *(capture `17-resolve-bad-range.json`)*
- Unknown time zone: body `{"from_day": "2026-11-01", "tz": "Mars/Phobos"}` → **422** `{"error": {"code": "invalid_time_zone", "message": "That time zone is not known. Write a name such as Africa/Lagos or America/New_York."}}` *(capture `18-resolve-bad-zone.json`)*
- No CSRF token: body `{"series_id": 1}` → **403** `{"error": {"code": "csrf_rejected", "message": "Missing or wrong X-CSRF-Token header. Read it from GET /api/session."}}` *(capture `19-resolve-no-csrf.json`)*
- A team the caller is not in: body `{"from_day": "2026-11-01"}`, `X-Team-Id: 999999` → **404** `{"error": {"code": "not_found", "message": "No such team."}}` *(capture `20-resolve-wrong-team.json`)*
- Another team's recurring meeting (as ada's team's series, asked by eve): body `{"series_id": 1}` → **404** `{"error": {"code": "not_found", "message": "No such recurring meeting."}}` *(capture `21-resolve-other-teams-series.json`)*
- Another team's project: body `{"project_id": 1}` → **404** `{"error": {"code": "not_found", "message": "No such project."}}` *(capture `22-resolve-other-teams-project.json`)*
- Another team's sprint: body `{"sprint_id": 1}` → **404** `{"error": {"code": "not_found", "message": "No such sprint."}}` *(capture `23-resolve-other-teams-sprint.json`)*

A 404 from `resolve` means the choice went away since the list was read (a project removed, the team left). The component says "That choice is no longer available. Choose again." The 404 and 422 `message` strings are developer text: do not show them as they are (the `errorMessage` helper already does not).

### 2. `POST /api/documents` with a `picker`

Same endpoint and answers as the draft guide, plus one optional body field, **`picker`**: the same choice that was previewed. Send the preview's `meetings[].id` as `meeting_ids` as well.

`POST /api/documents`: Make a cross-meeting draft from the preview above → **202** *(capture `25-create-from-picker.json`)*

```json
{
  "recipe_key": "status_report",
  "meeting_ids": [
    3,
    2,
    1
  ],
  "picker": {
    "series_id": 1
  }
}
```

```json
{
  "id": 1,
  "title": "Status report",
  "output_type": "document",
  "recipe_key": "status_report",
  "recipe_id": null,
  "status": "writing",
  "edited": false,
  "meeting_ids": [
    1,
    2,
    3
  ],
  "created_at": "2026-11-24T14:47:49.527855Z",
  "updated_at": "2026-11-24T14:47:49.527855Z",
  "blocks": [],
  "checks": {
    "blanks": [],
    "flags": []
  },
  "stale": false,
  "sources_available": true,
  "left_out_count": 2,
  "failure_code": null,
  "failure_message": null,
  "left_out_message": "2 meetings were left out because you cannot read them or they are not ready."
}
```

The draft says `left_out_count: 2` and `left_out_message` **from the first moment** (the 202 already has them). Note `meeting_ids` on the draft lists only the three it is made from.

`GET /api/documents/1`: The same draft once it is ready (poll the draft every 2 seconds); see "Sources" below → **200** *(capture `26-get-ready.json`)*

```json
{
  "id": 1,
  "title": "Status report",
  "output_type": "document",
  "recipe_key": "status_report",
  "recipe_id": null,
  "status": "ready",
  "edited": false,
  "meeting_ids": [
    1,
    2,
    3
  ],
  "created_at": "2026-11-24T14:47:49.527855Z",
  "updated_at": "2026-11-24T14:47:49.566948Z",
  "blocks": [
    {
      "type": "heading",
      "level": 1,
      "text": "Progress"
    },
    {
      "type": "paragraph",
      "text": "Kick-off: on track.",
      "sources": [
        {
          "kind": "meeting",
          "meeting_id": 1,
          "line": null,
          "quote": null,
          "ref": null
        },
        {
          "kind": "decision",
          "meeting_id": 1,
          "line": null,
          "quote": null,
          "ref": 1
        },
        {
          "kind": "task",
          "meeting_id": 1,
          "line": null,
          "quote": null,
          "ref": 1
        }
      ]
    },
    {
      "type": "paragraph",
      "text": "Planning: on track.",
      "sources": [
        {
          "kind": "meeting",
          "meeting_id": 2,
          "line": null,
          "quote": null,
          "ref": null
        },
        {
          "kind": "decision",
          "meeting_id": 2,
          "line": null,
          "quote": null,
          "ref": 2
        },
        {
          "kind": "task",
          "meeting_id": 2,
          "line": null,
          "quote": null,
          "ref": 2
        }
      ]
    },
    {
      "type": "paragraph",
      "text": "Review: on track.",
      "sources": [
        {
          "kind": "meeting",
          "meeting_id": 3,
          "line": null,
          "quote": null,
          "ref": null
        },
        {
          "kind": "decision",
          "meeting_id": 3,
          "line": null,
          "quote": null,
          "ref": 3
        },
        {
          "kind": "task",
          "meeting_id": 3,
          "line": null,
          "quote": null,
          "ref": 3
        }
      ]
    }
  ],
  "checks": {
    "blanks": [],
    "flags": []
  },
  "stale": false,
  "sources_available": true,
  "left_out_count": 2,
  "failure_code": null,
  "failure_message": null,
  "left_out_message": "2 meetings were left out because you cannot read them or they are not ready."
}
```


- **The server counts again.** It does not take a number from the client: it resolves `picker` itself to count the meetings the person cannot read. `picker` only fills `left_out_count`; it never adds a meeting: only `meeting_ids` the person can read are used. If the preview and the draft disagree (a meeting became unreadable in between) the draft's number is the truth.
- A draft made without `picker` still counts: a hidden meeting named in `meeting_ids` is left out and counted.

`POST /api/documents`: No `picker`, one hidden id in `meeting_ids` → **202** *(capture `30-create-without-picker.json`)*

```json
{
  "recipe_key": "status_report",
  "meeting_ids": [
    2,
    5
  ]
}
```

```json
{
  "id": 2,
  "title": "Status report",
  "output_type": "document",
  "recipe_key": "status_report",
  "recipe_id": null,
  "status": "writing",
  "edited": false,
  "meeting_ids": [
    2
  ],
  "created_at": "2026-11-24T14:47:49.747793Z",
  "updated_at": "2026-11-24T14:47:49.747793Z",
  "blocks": [],
  "checks": {
    "blanks": [],
    "flags": []
  },
  "stale": false,
  "sources_available": true,
  "left_out_count": 1,
  "failure_code": null,
  "failure_message": null,
  "left_out_message": "1 meeting was left out because you cannot read it or it is not ready."
}
```


Errors specific to `picker`:

- `picker` with a recipe that reads one meeting: body `{"recipe_key": "follow_up_email", "meeting_ids": [1], "picker": {"series_id": 1}}` → **422** `{"error": {"code": "meetings_invalid", "message": "This recipe uses one meeting, not a choice."}}` *(capture `28-create-picker-for-one-meeting-recipe.json`)*
- `picker` naming another team's recurring meeting: body `{"recipe_key": "status_report", "meeting_ids": [21], "picker": {"series_id": 1}}` → **404** `{"error": {"code": "not_found", "message": "No such recurring meeting."}}` *(capture `29-create-picker-other-teams-series.json`)*

## Sources of a cross-meeting draft

Look at the `blocks` of the ready draft (capture `26-get-ready.json`, above). Sources are of three kinds, and **every one carries `meeting_id`**:

| `kind` | Meaning | Where the link goes |
|---|---|---|
| `meeting` | drafted from that meeting as a whole | the meeting page |
| `decision` | a decision of that meeting (`ref` is the decision id) | the meeting page (its decisions) |
| `task` | a task of that meeting (`ref` is the task id) | the meeting page (its tasks) |

There are **no `line` sources** in a cross-meeting draft: the model is never given the transcripts, so it cannot cite a line (and a line it invents is dropped by the server). Do not build a `?tab=transcript&line=` link for these.

What the model is sent is checked in capture `27-prompt-check.json` (a note from the script, not a request): per meeting the notes, decisions, tasks and open questions; **no transcript, no line numbers, nothing of the two meetings the person cannot read**. A meeting that has nothing to read (no notes, decisions, tasks or questions) is skipped.

## Field-nesting traps

- `left_out_count` and `left_out_message` exist in **two places**: the picker preview and the draft (`DocumentOut`, top level, beside `stale`). On the draft `left_out_message` is last in the JSON; it is derived from the count and is null for 0.
- The draft's `meeting_ids` lists the meetings it is made from (the readable ones), **not** the ones left out. `left_out_count` is the difference the person cares about.
- The choice travels **twice** on the draft request: `meeting_ids` (what to read) and `picker` (what the person chose, only for counting). They are not interchangeable.
- `day` in a preview is a plain `YYYY-MM-DD` string (a date, not an instant). The Meetings list's `created_at` is an instant; do not mix them.
- `picker.meeting_ids` (hand-picked, up to 100) and the draft's `meeting_ids` (1 to 12) have different limits.

## State machine and polling

The preview is a single request per complete choice; there is nothing to poll. Ask again whenever the choice changes and **drop an answer that arrives for an older choice** (the component keeps each answer with the choice it answers). Making the draft is the draft guide's state machine (`writing` → `ready` or `failed`, poll every 2 seconds).

## UX consequences the FE must surface

- **Say what was left out.** Show `left_out_message` under the list, in a notice, on the picker and again on the draft. The wording is the server's: *"2 meetings were left out because you cannot read them or they are not ready."* **Never try to name or count them on the screen yourself.**
- **Say when the cap cut some** (`capped`): "More than 12 meetings match. The 12 newest are used." Hand-picking stops at 12 ticks (the component disables the rest).
- **A project or a sprint can include meetings from outside what the person can read.** That is exactly what `left_out_count` is for; it can be non-zero even though the person chose "everything of the sprint".
- **Make is disabled until the preview has at least one meeting** and no error.
- **Choose again after a team switch.** The component forgets the recurring meeting, project and sprint when the active team changes (they belong to a team).
- A sprint's meetings include those *held* on the sprint's days as well as those its tasks are planned into: a status report over "Sprint 2" can include a planning meeting that produced no task.

## Verification

| Behaviour | How verified |
|---|---|
| Series, project, sprint, date range, hand-picked previews; cap at 12 with `capped`; empty answer | **Verified live** (captures 07 to 14) |
| Left-out count and sentence; no title, no id, no word of a hidden meeting in any response | **Verified live** (07, 09, 14; the script fails if the private meetings' words appear in any saved response) |
| 401, 403 CSRF, 404 for another team's series, project, sprint and for a wrong `X-Team-Id`, 422 shapes | **Verified live** (06, 15 to 23) |
| A draft made with `picker`: count and sentence on the 202 and on the ready draft | **Verified live** (25, 26) |
| `picker` for a one-meeting recipe 422; for another team's series 404; hidden id without `picker` still counted | **Verified live** (28, 29, 30) |
| Sources of kinds `meeting`, `decision`, `task`, each with `meeting_id`; no `line` sources | **Verified live** with a fake model that cites what it is shown (26); the real model's choice of sources is not exercised |
| The prompt has no transcript, no line markers, nothing of unreadable meetings | **Verified live** (27, a check of the actual prompt the worker sent) and by tests (`tests/test_recipe_cross_meeting.py`) |
| The sprint rule (tasks planned into it plus meetings held on its days) | **Verified live** (10) and by `tests/test_recipe_picker.py` |
| Time-zone handling of `day` | Verified live with `tz` only (11); a **stored** time zone is covered by the Meetings list's tests, not re-run here |
| The real starters (status report, client briefing, revision plan) | **Not run**: a stand-in recipe; one test per real starter is added after PR #267 merges |
| The picker screen in a browser, inside the Make dialog | **Not done**: vitest only, API mocked; mounting waits for #267 |
| `plan_inactive`, `recipe_limit` | Not re-run here; see the draft guide |

