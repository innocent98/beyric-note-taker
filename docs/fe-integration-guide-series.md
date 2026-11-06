# Frontend integration guide: Series, agenda, carry-over and reminders (W8)

Everything the **Recurring cards** on Meetings, the **Series page**, the meeting page's **Series row** and **"On next agenda"** toggles, **Send agenda to attendees** and the **Sprints and reminders** card need. **Every payload, status code and error string below is pasted from a real response** captured by `scripts/capture_series_api.py` against the running app (real HTTP, real sessions and CSRF, real database and migrations, the real linking and reminder code, the real mail builders). The files are in `docs/fe-captures/series-w8/`; run the script again to re-derive any of it. Long lists are shortened with an explicit `<… omitted>` marker; nothing is tidied or invented. What could not be run live is marked **NOT LIVE** and listed in the verification table at the end. Names and addresses are placeholders. Times in the captures are "now" on the day they were captured, so dates differ from the ones in the examples you will see in a later run.

Field-by-field shapes are in `docs/openapi.json` (generated into `web/lib/api/schema.d.ts`). This guide is for behaviour.

## What the feature is, in your terms

A **recurring meeting** (the API calls it a *series*) is a meeting that happens again and again: "Beyric Monday planning". It belongs to a **team**. It appears in three places:

* **Meetings → Recurring cards** (`GET /api/series`): name, cadence, platform, next date, "N unfinished tasks", "N open questions". A card opens the Series page.
* **The Series page** (`GET /api/series/{id}`): next meeting and **Send agenda to attendees**, *Unfinished from last time*, *Finished since last meeting*, *Open questions carried over* (with **Mark resolved**), *Other agenda items* (add/remove), *Past meetings* and *Attendees*.
* **The meeting page**: a **Series** row in Details (a link to the page) and, beside each *open question*, a toggle with three states: **Add to next agenda**, **On next agenda**, **Resolved**.

A series appears by itself: a calendar meeting that belongs to a recurring calendar event joins the series of that event when its notes are written, and the hourly sync makes the series of events the app is going to join, so a card exists before the first recording. A person can also **make one and attach a meeting by hand** (API only, see "Not in this version").

Not in this version (nothing in the API, no control): "Add to recurring meeting" on the meeting page and "Make this a recurring meeting?" for a reused link (the designer drew neither; build them only when told), the "possibly resolved" suggestions (W8b), the sprint line under *Next meeting* and the milestone countdown in the mails (they need Sprints and Milestones), typing an outside address to send the agenda to (the designer drew one button), moving a series between teams.

## Conventions (same as the rest of the API)

Session cookie; every non-GET sends `X-CSRF-Token`; errors are `{"error": {"code", "message"}}`, validation errors add `fields`. A series of **another team is 404, never 403**; a meeting or question you may not read is 404. A call your role does not allow on your own team is **403 `team_forbidden`**. Ids are integers.

**The rule that explains most of what you see:** everything *inside* a series is limited to what the caller may read. Teammates see a series' title; they see its meetings, tasks, questions and attendees only for meetings shared with the team (plus tasks assigned to them). Two teammates therefore see **different counts and lists for the same series**, and that is correct, not a bug. Compare captures `08` and `09`.

Logged out: `GET /api/series` → 401 (capture `07-series-logged-out.json`), body `{"error": {"code": "not_authenticated", ...}}` as everywhere.

## The Recurring cards


`GET /api/series` as **ada** → **200**  
*capture `08-cards-owner.json`*  
The Recurring cards. Ada sees her private meeting's tasks and questions too.

```json
{
  "timezone": "UTC",
  "series": [
    {
      "id": 3,
      "title": "Beyric Friday review",
      "cadence": "Recurring",
      "platform": "google_meet",
      "next_starts_at": "2026-11-06T14:40:44.124050Z",
      "next_text": "Fri 6 Nov, 14:40",
      "unfinished_tasks": 2,
      "open_questions": 2
    },
    {
      "id": 1,
      "title": "Beyric Monday planning",
      "cadence": "Weekly on Mondays, 14:00",
      "platform": "google_meet",
      "next_starts_at": "2026-11-09T14:00:00Z",
      "next_text": "Mon 9 Nov, 14:00",
      "unfinished_tasks": 4,
      "open_questions": 2
    }
  ]
}
```

Notes for the card:

* `cadence` is `Weekly on Mondays, 14:00`, `Daily, 14:00` or just **`Recurring`** (show it as it is): a claim is made only when the stored times prove it. It is written in the viewer's saved time zone (`timezone`; UTC until they choose one on Settings).
* `next_starts_at` / `next_text` are **null** when no calendar event ahead is known (a series made by hand, or a calendar without a future occurrence): hide the "Next:" chip.
* `unfinished_tasks` and `open_questions` count only what the **caller** may read. The same cards for a teammate:


`GET /api/series` as **dan** → **200**  
*capture `09-cards-teammate.json`*  
Dan sees only what he may read: the counts differ from Ada's.

```json
{
  "timezone": "UTC",
  "series": [
    {
      "id": 3,
      "title": "Beyric Friday review",
      "cadence": "Recurring",
      "platform": "google_meet",
      "next_starts_at": "2026-11-06T14:40:44.124050Z",
      "next_text": "Fri 6 Nov, 14:40",
      "unfinished_tasks": 2,
      "open_questions": 2
    },
    {
      "id": 1,
      "title": "Beyric Monday planning",
      "cadence": "Weekly on Mondays, 14:00",
      "platform": "google_meet",
      "next_starts_at": "2026-11-09T14:00:00Z",
      "next_text": "Mon 9 Nov, 14:00",
      "unfinished_tasks": 3,
      "open_questions": 2
    }
  ]
}
```

* Sorted soonest next meeting first, series without a next date last by name. Another team's cards never appear:


`GET /api/series` as **eve** → **200**  
*capture `10-cards-outsider.json`*  
Another team: empty.

```json
{
  "timezone": "UTC",
  "series": []
}
```

## The Series page


`GET /api/series/1` as **ada** → **200**  
*capture `11-page-owner.json`*  
The Series page as the owner of the meetings sees it.

```json
{
  "id": 1,
  "title": "Beyric Monday planning",
  "cadence": "Weekly on Mondays, 14:00",
  "platform": "google_meet",
  "timezone": "UTC",
  "next_starts_at": "2026-11-09T14:00:00Z",
  "next_text": "Mon 9 Nov, 14:00",
  "can_rename": true,
  "agenda_recipients": 1,
  "unfinished": [
    {
      "id": 1,
      "title": "Finish payment checkout",
      "status": "todo",
      "assignee": {
        "user_id": 3,
        "name": "Dan Okoro",
        "is_you": false
      },
      "spoken_owner": null,
      "due_date": null,
      "due_phrase": null,
      "quote": null,
      "is_new": false,
      "unverified": false,
      "source": "ai",
      "assigned_on": "2026-10-26",
      "done_at": null,
      "created_at": "2026-11-06T13:45:44.421166Z",
      "from_meeting": true,
      "meeting": {
        "id": 1,
        "title": "Beyric Monday planning",
        "platform": "google_meet",
        "created_at": "2026-10-26T14:00:00Z"
      },
      "can_update": true,
      "can_edit": true,
      "can_assign": true,
      "can_claim": false,
      "can_delete": true
    },
    {
      "id": 2,
      "title": "Log design refinements on the tracker",
      "status": "todo",
      "assignee": null,
      "spoken_owner": null,
      "due_date": null,
      "due_phrase": null,
      "quote": null,
      "is_new": false,
      "unverified": false,
      "source": "ai",
      "assigned_on": "2026-10-26",
      "done_at": null,
      "created_at": "2026-11-06T13:45:44.421166Z",
      "from_meeting": true,
      "meeting": {
        "id": 1,
        "title": "Beyric Monday planning",
        "platform": "google_meet",
        "created_at": "2026-10-26T14:00:00Z"
      },
      "can_update": true,
      "can_edit": true,
      "can_assign": true,
      "can_claim": true,
      "can_delete": true
    },
    "<… 2 more omitted>"
  ],
  "finished_since_text": "Mon 2 Nov",
  "finished": [
    {
      "id": 7,
      "title": "Send the pricing draft",
      "status": "done",
      "assignee": {
        "user_id": 2,
        "name": "Ada Lovelace",
        "is_you": true
      },
      "spoken_owner": null,
      "due_date": null,
      "due_phrase": null,
      "quote": null,
      "is_new": false,
      "unverified": false,
      "source": "ai",
      "assigned_on": "2026-10-26",
      "done_at": "2026-11-03T14:00:00Z",
      "created_at": "2026-11-06T13:45:44.421166Z",
      "from_meeting": true,
      "meeting": {
        "id": 1,
        "title": "Beyric Monday planning",
        "platform": "google_meet",
        "created_at": "2026-10-26T14:00:00Z"
      },
      "can_update": true,
      "can_edit": true,
      "can_assign": true,
      "can_claim": false,
      "can_delete": true
    }
  ],
  "questions": [
    {
      "id": 3,
      "text": "Which pricing model will the team choose?",
      "meeting": {
        "id": 2,
        "title": "Beyric Monday planning",
        "platform": "google_meet",
        "created_at": "2026-11-02T14:00:00Z"
      },
      "can_update": true
    },
    {
      "id": 4,
      "text": "Who covers support in December?",
      "meeting": {
        "id": 2,
        "title": "Beyric Monday planning",
        "platform": "google_meet",
        "created_at": "2026-11-02T14:00:00Z"
      },
      "can_update": true
    }
  ],
  "agenda": [],
  "past_meetings": [
    {
      "id": 2,
      "title": "Beyric Monday planning",
      "created_at": "2026-11-02T14:00:00Z",
      "duration_seconds": 2760,
      "tasks_count": 2
    },
    {
      "id": 1,
      "title": "Beyric Monday planning",
      "created_at": "2026-10-26T14:00:00Z",
      "duration_seconds": 2760,
      "tasks_count": 3
    }
  ],
  "attendees": [
    {
      "name": "Ada Lovelace",
      "is_teammate": true
    },
    {
      "name": "Dan Okoro",
      "is_teammate": true
    },
    {
      "name": "Guest Visitor",
      "is_teammate": false
    }
  ]
}
```

Field notes (**nesting traps first**):

* `unfinished` and `finished` are **full task objects** (the same shape as `GET /api/tasks`: `can_update`, `can_claim`, `assignee` null = "Needs owner", ...). Reuse the Tasks components. Open the task drawer from them as on the Tasks screen.
* A task's `meeting` is **null** when the task is visible to the caller only because it is assigned to them (the meeting is private to someone else): draw the row without a meeting link.
* `questions[].meeting` is the meeting the question was asked in (`From <title>`). A question asked twice in the series with the same words is listed **once**, from the newest meeting.
* `finished_since_text` ("Mon 2 Nov") is the **last meeting's date**; `finished` are the tasks completed on or after that date. It is `null` (and `finished` empty) when the series has no meeting the caller may read: show the empty line "Nothing finished yet. Completed tasks will show here."
* `agenda_recipients` is how many teammates *Send agenda to attendees* would reach (never the caller). **When it is 0, switch the button off** (it would answer 409 `no_attendees`).
* `can_rename` / `agenda[].can_remove` / `questions[].can_update` say what to draw; the server checks again.
* `attendees` are names from the speaker labels of the last three meetings the caller may read; `is_teammate` is true when the name matched a team member. **No address is ever sent.** Draw initials from `name`.

The same page for a teammate, who cannot read the owner's private meeting (compare the lists and `past_meetings` with the owner's):


`GET /api/series/1` as **dan** → **200**  
*capture `12-page-teammate.json`*  
Same page for a teammate: the private meeting is not listed and its tasks and questions are not counted.

```json
{
  "id": 1,
  "title": "Beyric Monday planning",
  "cadence": "Weekly on Mondays, 14:00",
  "platform": "google_meet",
  "timezone": "UTC",
  "next_starts_at": "2026-11-09T14:00:00Z",
  "next_text": "Mon 9 Nov, 14:00",
  "can_rename": false,
  "agenda_recipients": 1,
  "unfinished": [
    {
      "id": 1,
      "title": "Finish payment checkout",
      "status": "todo",
      "assignee": {
        "user_id": 3,
        "name": "Dan Okoro",
        "is_you": true
      },
      "spoken_owner": null,
      "due_date": null,
      "due_phrase": null,
      "quote": null,
      "is_new": false,
      "unverified": false,
      "source": "ai",
      "assigned_on": "2026-10-26",
      "done_at": null,
      "created_at": "2026-11-06T13:45:44.421166Z",
      "from_meeting": true,
      "meeting": null,
      "can_update": true,
      "can_edit": false,
      "can_assign": false,
      "can_claim": false,
      "can_delete": false
    },
    "<… 2 more omitted>"
  ],
  "finished_since_text": "Mon 2 Nov",
  "finished": [],
  "questions": [
    {
      "id": 3,
      "text": "Which pricing model will the team choose?",
      "meeting": {
        "id": 2,
        "title": "Beyric Monday planning",
        "platform": "google_meet",
        "created_at": "2026-11-02T14:00:00Z"
      },
      "can_update": false
    },
    "<… 1 more omitted>"
  ],
  "agenda": [],
  "past_meetings": [
    {
      "id": 2,
      "title": "Beyric Monday planning",
      "created_at": "2026-11-02T14:00:00Z",
      "duration_seconds": 2760,
      "tasks_count": 2
    }
  ],
  "attendees": [
    {
      "name": "Ada Lovelace",
      "is_teammate": true
    },
    {
      "name": "Dan Okoro",
      "is_teammate": true
    },
    {
      "name": "Guest Visitor",
      "is_teammate": false
    }
  ]
}
```

Another team: 


`GET /api/series/1` as **eve** → **404**  
*capture `13-page-outsider.json`*  
404 for another team.

```json
{
  "error": {
    "code": "not_found",
    "message": "No such recurring meeting"
  }
}
```

After a question is resolved it leaves `questions` (capture `24-page-after-resolve.json`).

## The meeting page

`GET /api/meetings/{id}` gains two fields (everything else unchanged):



`GET /api/meetings/2` as **ada** → **200**  
*capture `15-meeting-detail.json`*  
Only the two new fields shown; the rest of the body is as before.


```json
{
  "id": 2,
  "series": {
    "id": 1,
    "title": "Beyric Monday planning"
  },
  "open_questions": [
    {
      "id": 3,
      "text": "Which pricing model will the team choose?",
      "carriable": true,
      "carry": true,
      "resolved": false,
      "can_update": true
    },
    {
      "id": 4,
      "text": "Who covers support in December?",
      "carriable": true,
      "carry": true,
      "resolved": false,
      "can_update": true
    }
  ]
}
```


A meeting that is in no series (capture `16-meeting-detail-lone.json`): `series` is `null` and `open_questions` is `[]`.


A teammate reading the shared meeting (capture `17-meeting-detail-teammate.json`): `open_questions[0].can_update` is `false`, so draw the toggle disabled.


* **Series row** in Details: draw it when `series` is not null, as a link to `/series/{series.id}` showing `series.title`. It is null when the meeting is in no series, **and also when the series belongs to a team the viewer is not on**.
* **Open-question toggle.** Draw it only when `carriable` is true (the meeting is in a series). Label: `resolved` → **Resolved**; else `carry` → **On next agenda**; else **Add to next agenda**. Clicking toggles `carry` (see below). A question of a meeting in no series has no toggle (the designer's prototype draws none).
* `open_questions` is empty until the meeting is ready.

## Questions: On next agenda, Mark resolved

`PATCH /api/questions/{id}` with `{"carry": bool}` and/or `{"resolved": bool}`. Returns the question (the same shape as `open_questions[]`).


`PATCH /api/questions/3`  body: `{"carry": false}` as **ada** → **200**  
*capture `18-question-off.json`*  
"Add to next agenda" state.

```json
{
  "id": 3,
  "text": "Which pricing model will the team choose?",
  "carriable": true,
  "carry": false,
  "resolved": false,
  "can_update": true
}
```

`PATCH /api/questions/3`  body: `{"carry": true}` as **ada** → **200**  
*capture `19-question-on.json`*  
"On next agenda".

```json
{
  "id": 3,
  "text": "Which pricing model will the team choose?",
  "carriable": true,
  "carry": true,
  "resolved": false,
  "can_update": true
}
```

`PATCH /api/questions/3`  body: `{"resolved": true}` as **ada** → **200**  
*capture `23-question-resolve.json`*  
"Mark resolved".

```json
{
  "id": 3,
  "text": "Which pricing model will the team choose?",
  "carriable": true,
  "carry": true,
  "resolved": true,
  "can_update": true
}
```

Errors:


`PATCH /api/questions/3`  body: `{"resolved": true}` as **dan** → **403**  
*capture `20-question-teammate.json`*  
A member who can only read: 403 `team_forbidden`.

```json
{
  "error": {
    "code": "team_forbidden",
    "message": "You can't do that here. Ask an owner or admin of your team."
  }
}
```

`PATCH /api/questions/3`  body: `{"resolved": true}` as **eve** → **404**  
*capture `21-question-outsider.json`*  
404.

```json
{
  "error": {
    "code": "not_found",
    "message": "No such question"
  }
}
```

`PATCH /api/questions/3`  body: `{}` as **ada** → **422**  
*capture `22-question-empty.json`*  
422.

```json
{
  "error": {
    "code": "validation_error",
    "message": "Send carry or resolved."
  }
}
```

* Turning `carry` **on** also reopens a resolved question (as the designer's toggle does). Turning it **off** leaves it resolved or not as it was.
* **Resolved applies to the same words asked in the series' other meetings** the caller may change, so a question raised twice does not come back.
* `409 not_in_series` is returned for `carry` on a question of a meeting that is in no series (you should not be able to click it: `carriable` is false).
* Who may change a question: the meeting's owner, or an owner/admin of the team the meeting is shared with. A member who can only *read* gets **403 `team_forbidden`**: show the control disabled (`can_update` false).

## The agenda ("Other agenda items")


`POST /api/series/1/agenda`  body: `{"text": "Budget review"}` as **ada** → **201**  
*capture `25-agenda-add.json`*  


```json
{
  "id": 1,
  "text": "Budget review",
  "can_remove": true
}
```

`GET /api/series/1/agenda` as **dan** → **200**  
*capture `29-agenda-list.json`*  
`can_remove` is per line: its author or a manager.

```json
[
  {
    "id": 1,
    "text": "Budget review",
    "can_remove": false
  },
  {
    "id": 2,
    "text": "Hiring update",
    "can_remove": true
  }
]
```

`POST /api/series/1/agenda`  body: `{"text": "See https://example.test/x"}` as **ada** → **422**  
*capture `27-agenda-link.json`*  
A web address is refused: 422 `invalid_agenda_item`.

```json
{
  "error": {
    "code": "invalid_agenda_item",
    "message": "Write the item without a web address."
  }
}
```

`POST /api/series/1/agenda`  body: `{"text": "   "}` as **ada** → **422**  
*capture `28-agenda-empty.json`*  
422.

```json
{
  "error": {
    "code": "invalid_agenda_item",
    "message": "Type what should be discussed."
  }
}
```

`DELETE /api/series/1/agenda/1` as **ada** → **204**  
*capture `31-agenda-remove.json`*  
204.

```json
null
```

`DELETE /api/series/1/agenda/1` as **dan** → **403**  
*capture `30-agenda-remove-not-mine.json`*  
403.

```json
{
  "error": {
    "code": "team_forbidden",
    "message": "You can't do that here. Ask an owner or admin of your team."
  }
}
```

`DELETE /api/series/1/agenda/1` as **ada** → **404**  
*capture `32-agenda-remove-again.json`*  
404.

```json
{
  "error": {
    "code": "not_found",
    "message": "No such agenda item"
  }
}
```

* Plain text, at most 200 characters, **no web address** (the mail goes to teammates as text; a link cannot be planted). At most 30 waiting lines: **409 `agenda_full`** ("The agenda has as many items as fit. Remove one first." ; covered by `tests/test_series.py`, NOT in the live captures).
* Any member adds; the author or an owner/admin removes (`can_remove`).
* Lines typed before a meeting **begins** are consumed by it (they disappear from the list once the next meeting of the series is recorded or attached): the list shows only what is waiting.

## Send agenda to attendees


`POST /api/series/1/agenda/send` as **ada** → **200**  
*capture `35-send-agenda.json`*  
Goes to teammates who were in the last three meetings, not the sender.

```json
{
  "count": 1,
  "names": [
    "Dan Okoro"
  ]
}
```

`POST /api/series/1/agenda/send` as **eve** → **404**  
*capture `34-send-outsider.json`*  
404.

```json
{
  "error": {
    "code": "not_found",
    "message": "No such recurring meeting"
  }
}
```

The button needs **no body**. The agenda goes to the **teammates who were in the last three meetings** (matched by name from the speaker labels, plus the account that recorded), **never the caller**, never anyone outside the team. Toast text: "Agenda sent to {count} attendees" (`count`; `names` are the people). The mail goes out within a minute.

Refusals (all `{"error": {...}}`):


`POST /api/series/1/agenda/send` as **ada** → **409**  
*capture `59-send-after-optout.json`*  
Dan no longer counts as a recipient: nobody left, 409 `no_attendees`.

```json
{
  "error": {
    "code": "no_attendees",
    "message": "There is nobody on your team to send it to yet. The agenda goes to teammates who were in the last three meetings."
  }
}
```

* **409 `no_attendees`**: nobody to send it to (also when the attendees turned reminder mail off). Switch the button off when `agenda_recipients` is 0.
* **409 `nothing_to_send`** ("Add an agenda item first. There is nothing to send yet."): no waiting line, no carried question and no unfinished task. (`tests/test_series_mail.py`; NOT in the live captures.)
* **429 `agenda_rate_limited`** ("You have sent as many agendas as fit in a day. Try again tomorrow."): 5 sends and 20 people a day per account, at most 10 people a send. (Tests only; NOT live.)
* **503 `mail_unavailable`** when the server has no mail provider. (Not reachable in the capture, which runs with the console provider.)

What the mail says is in "The mails" below.

## Making a series and attaching a meeting by hand (no screen drawn)

The designer drew no "Add to recurring meeting". The API exists so the screen can be added when it is designed; **do not build one without approval**.


`POST /api/series`  body: `{"title": "Design sync"}` as **ada** → **201**  
*capture `39-series-create.json`*  
Made by hand: no calendar, no next date.

```json
{
  "id": 4,
  "title": "Design sync",
  "cadence": "Recurring",
  "platform": null,
  "next_starts_at": null,
  "next_text": null,
  "unfinished_tasks": 0,
  "open_questions": 0
}
```

`POST /api/series`  body: `{"title": "Hand made"}` as **ada** → **201**  
*capture `41-series-create-2.json`*  


```json
{
  "id": 5,
  "title": "Hand made",
  "cadence": "Recurring",
  "platform": null,
  "next_starts_at": null,
  "next_text": null,
  "unfinished_tasks": 0,
  "open_questions": 0
}
```

`PUT /api/meetings/4/series`  body: `{"series_id": 5}` as **ada** → **200**  
*capture `42-attach.json`*  
Only the meeting's owner may.

```json
{
  "series": {
    "id": 5,
    "title": "Hand made"
  }
}
```

`PUT /api/meetings/4/series`  body: `{"series_id": 5}` as **dan** → **404**  
*capture `43-attach-teammate.json`*  
404 for a teammate.

```json
{
  "error": {
    "code": "not_found",
    "message": "No such meeting"
  }
}
```

`PUT /api/meetings/4/series`  body: `{"series_id": 5}` as **eve** → **404**  
*capture `44-attach-foreign-series.json`*  
404.

```json
{
  "error": {
    "code": "not_found",
    "message": "No such meeting"
  }
}
```

`PUT /api/meetings/4/series`  body: `{"series_id": null}` as **ada** → **200**  
*capture `45-detach.json`*  


```json
{
  "series": null
}
```

`PATCH /api/series/5`  body: `{"title": "Design sync (weekly)"}` as **ada** → **200**  
*capture `46-rename.json`*  


```json
{
  "id": 5,
  "title": "Design sync (weekly)",
  "cadence": "Recurring",
  "platform": null,
  "next_starts_at": null,
  "next_text": null,
  "unfinished_tasks": 0,
  "open_questions": 0
}
```

`PATCH /api/series/5`  body: `{"title": "No"}` as **dan** → **403**  
*capture `47-rename-teammate.json`*  
403: not the creator and not a manager.

```json
{
  "error": {
    "code": "team_forbidden",
    "message": "You can't do that here. Ask an owner or admin of your team."
  }
}
```

## Reminders: the Settings card

The designer's **Sprints and reminders** card has the toggle *Remind attendees before recurring meetings* and a timing select. The API:

* **Team switch and timing (owner and admin):** `PATCH /api/team` with `reminders_on` and/or `reminder_timing` (`"1h"`, `"30m"` or `"both"`; the select's three labels are *1 hour before*, *30 minutes before*, *1 hour and 30 minutes before*). `GET /api/team` returns both. **`reminders_on` is false until a manager switches it on.**


`GET /api/team` as **ada** → **200**  
*capture `50-team-before.json`*  
`reminders_on` is false until a manager switches it on.

```json
{
  "id": 2,
  "name": "My team",
  "share_new_meetings": false,
  "reminders_on": false,
  "reminder_timing": "both",
  "my_role": "owner",
  "member_count": 2,
  "pending_invites": 0,
  "can_manage": true,
  "can_change_roles": true
}
```

`PATCH /api/team`  body: `{"reminders_on": true, "reminder_timing": "both"}` as **ada** → **200**  
*capture `51-team-reminders-on.json`*  


```json
{
  "id": 2,
  "name": "My team",
  "share_new_meetings": false,
  "reminders_on": true,
  "reminder_timing": "both",
  "my_role": "owner",
  "member_count": 2,
  "pending_invites": 0,
  "can_manage": true,
  "can_change_roles": true
}
```

`PATCH /api/team`  body: `{"reminder_timing": "2h"}` as **ada** → **422**  
*capture `52-team-bad-timing.json`*  
422.

```json
{
  "error": {
    "code": "validation_error",
    "message": "Some fields are missing or not valid.",
    "fields": [
      {
        "field": "reminder_timing",
        "message": "Input should be '1h', '30m' or 'both'"
      }
    ]
  }
}
```

`PATCH /api/team`  body: `{"reminders_on": true}` as **dan** → **403**  
*capture `49-team-reminders-member.json`*  
403 for a member.

```json
{
  "error": {
    "code": "team_forbidden",
    "message": "Only an owner or admin of the team can do that."
  }
}
```

* **A person's own switch:** `GET /api/settings/reminders` and `PUT /api/settings/reminders` with `{"notify_reminders": bool}` (on by default). It stops the agenda mails and the reminders for that person, also those already queued. This is not drawn by the designer; the existing email-preferences area is where it belongs, **ask before adding a control**.


`GET /api/settings/reminders` as **ada** → **200**  
*capture `48-prefs-default.json`*  


```json
{
  "notify_reminders": true
}
```

`PUT /api/settings/reminders`  body: `{"notify_reminders": false}` as **dan** → **200**  
*capture `57-prefs-off.json`*  
Dan opts out of agenda and reminder mail.

```json
{
  "notify_reminders": false
}
```

## The mails (what teammates receive)

Assembled by code from stored data; nothing is generated. Each recipient sees **only what they may read**: the last meeting's summary and decisions appear only if they can read that meeting; carried questions and unfinished tasks only from meetings and tasks they can see. A mail with nothing to say is not sent.

**Agenda mail** (capture `38-agenda-rendered.json`, plain-text part; the HTML has the designer's layout):


```
Agenda: Beyric Monday planning

Beyric Monday planning · sent by Ada Lovelace

Agenda for the next meeting

Hi Dan, here is what the team wants to cover.

On the agenda

- Hiring update
- Budget review
- Who covers support in December?

Still open from last time

- Finish payment checkout · Dan Okoro (No due date)
- Finish payment checkout · Dan Okoro (No due date)
- Log design refinements on the tracker · Needs owner (No due date)

View the agenda:
https://notes.example.test/series/1

You received this because you attend Beyric Monday planning, and Ada Lovelace sent it.
Reminder settings: https://notes.example.test/settings#reminders
```


**1-hour reminder** (capture `54-reminder-1h-rendered.json`):


```
Starts in 1 hour: Beyric Friday review

Starts in 1 hour

Beyric Friday review

Fri 6 Nov, 14:40 to 15:25 UTC · Google Meet

Join meeting:
https://meet.google.com/abc-defg-hij

View agenda:
https://notes.example.test/series/3

Last time, Fri 30 Oct

The team reviewed Client Portal progress ahead of the launch. Payment checkout is the main risk. A second sentence that makes the summary a little longer.

Decisions made

- Finish checkout this week.

Open questions to settle

- Which pricing model will the team choose?
- Who covers support in December?

Still open from last time

- Finish payment checkout · Dan Okoro (No due date)
- Log design refinements on the tracker · Needs owner (No due date)

You are getting this because you attend Beyric Friday review.
Reminder settings: https://notes.example.test/settings#reminders
```


**30-minute reminder** (capture `56-reminder-30m-rendered.json`):


```
Starts in 30 minutes: Beyric Friday review

Starts in 30 minutes

Beyric Friday review

Fri 6 Nov, 14:40 to 15:25 UTC · Google Meet

Join meeting:
https://meet.google.com/abc-defg-hij

On the agenda

- Which pricing model will the team choose?
- Who covers support in December?
- Check on 2 unfinished tasks.

You are getting this because you attend Beyric Friday review.
Reminder settings: https://notes.example.test/settings#reminders
```


Queued rows hold ids only (capture `53-reminder-queued.json`, `37-agenda-outbox.json`); running the queue again queues nothing (capture `55-reminder-requeue.json`: once per person and occurrence).

Things the UI should say or surface:

* **Reminders are off until a manager switches them on**, and the card should say who can ("Owners and admins" pill, as drawn).
* The reminder uses the calendar's meeting link for the *Join meeting* button; it is shown only for Google Meet and Zoom links.
* The Settings text must not promise reminders to people outside the team: **they are never sent**.

## State and polling

There is no long-running state: every call answers at once and the mail is sent by the background sender within a minute. After *Send agenda*, no polling is needed. The Series page can be refetched on focus; counts on the cards change when a task is ticked or a question resolved, so refetch `GET /api/series` after those.

## UX consequences the frontend must surface

* **Different people see different counts** for the same series (privacy by design). Never "correct" a count against another person's.
* **A recurring meeting can show "Recurring" for its cadence** when the calendar holds too little to prove a pattern. Show it as it is.
* **Sending the agenda sends real email** to teammates. The button needs no confirmation in the design, but the toast must name the number of people, and it must be off when nobody can receive it.
* **Mark resolved resolves the same question in earlier meetings too.**
* A series appears **about an hour** after the calendar sync sees the recurring event (or when the first notes are written), not instantly.

## Verification

| Behaviour | Verified |
|---|---|
| Cards, counts per viewer, cadence from calendar rows, next date | **live** (captures 08 to 10) |
| Series page, per-viewer filtering, outsider 404 | **live** (11 to 14) |
| Meeting detail `series` and `open_questions`, teammate `can_update` | **live** (15 to 17) |
| Question toggle, resolve, 403/404/422 | **live** (18 to 24) |
| Agenda add/list/remove, link refusal, empty, author-or-manager removal | **live** (25 to 33) |
| `agenda_full` (30 lines) | tests only (`tests/test_series.py`) |
| Send agenda, who it goes to, outsider 404, `no_attendees` | **live** (34 to 38, 59) |
| `nothing_to_send`, `agenda_rate_limited`, `mail_unavailable` | tests / not reachable live |
| Mail text for agenda, 1-hour and 30-minute reminders | **live** through the real builder and `deliver_pending` with a **capturing mailer** (not Resend, no real inbox) |
| Reminder queueing, once per person and occurrence, the two windows | **live** against stored event rows; the scheduler tick itself (every minute) is covered by `tests/test_sweeps.py`, **NOT live** |
| Calendar linking (`link_from_calendar`) from a real sync | **NOT LIVE**: events and joins are rows made by the script as the sync and the scheduler leave them; the Google round-trip is in the calendar guides |
| Make/attach/rename by hand, team switch, personal preference | **live** (39 to 52, 57 to 58) |
| HTML layout of the mails against the designer's file | built by the real layout code; **not compared to a rendered screenshot** |
