# FE integration guide: recurring meetings follow the meetings (team scope)

Everything in this file is a body, status or message **copied from a real response**: the capture script
`scripts/capture_series_follow.py` runs the real app (real migrations, auth, CSRF, team roles, visibility,
the backfill script) on a scratch Postgres and writes every exchange to `docs/fe-captures/series-follow/NN-name.json`. Run it again
to re-derive any claim. What was **not** exercised live is marked inline and listed in section 9. Sections 1 to 9 use captures 01 to 51; **section 10 (remove a recurring meeting) uses 52 to 76**. The page bodies in sections 1 to 9 were captured before `can_remove` was added to the page, so they do not show it; section 10.2 shows it.
Names, emails and ids are placeholders ("Ada Example", `owner@example.test`, ids from a scratch database:
yours will differ).

Related: `docs/fe-integration-guide-series.md` (the Series API this builds on),
`docs/fe-integration-guide-teams.md` (the team switcher and `X-Team-Id`),
`docs/superpowers/specs/2026-11-14-series-follow-meetings.md` (the design),
`docs/sop/2026-11-14-series-follow-switch-on.md` (the server steps),
`docs/sop/2026-11-15-remove-recurring-meeting.md` (section 10, removing a recurring meeting).

## 1. What this is, in the person's terms

A **recurring meeting is no longer one thing per person**. Monday's Standup shared to the team "Tech" and
Tuesday's Standup kept private are two rows: the team's recurring list shows Standup with Monday only; the
person's **Personal** space shows Standup with Tuesday only. Adebayo's rule (2026-11-14): *a team's page lists
a series if at least one of its meetings is in that team, and shows only those meetings; the personal
space shows the series' private meetings.*

It switches on when the server flag `SERIES_FOLLOW_MEETINGS` is turned on (after a one-off backfill),
**and only while `MULTI_TEAM_ENABLED` is on** (it is). The frontend has no switch to read: with the flag off
every shape below is the same except that `team` is already sent and `scoped` is `false` (section 3). The
frontend reads `scoped` on the page to know which rules hold.

| What the person sees | Before | After the flag is on |
|---|---|---|
| A one-team person's **private** recurring meeting (every meeting of it private) | on the team's Recurring list, with the team's meetings | its meetings are on their **Personal** space only. **The team's list keeps an empty card for it** (0 and 0, for its creator only; its page says nothing is shared with the team): captures 14 and 50 |
| A team's Standup page | everything the person may read in that row, any team | only the meetings **shared with (or moved to) that team** |
| The Personal Standup page | whatever was in the person's default team's row | only the person's own, private meetings of that series |
| "Next meeting" on a Personal row | the person's calendar | **none** (Adebayo, answer 8) |
| "Keeps coming up" on a Personal row | n/a | **none** for now (Adebayo, answer 4) |
| Sharing, unsharing or moving a meeting | the meeting stayed in its row | the meeting **moves to the row of its new scope** (section 6) |

## 2. The frontend changes this needs (done in the same PR as this guide)

| Where | Change | Source of truth |
|---|---|---|
| Recurring cards (Meetings page) | While the server's multi-team switch is on (`multi_team` in `GET /api/session`), the cadence line also names the row's team, or "Personal": `Weekly on Saturdays, 20:00 · Platform team` | `team` on the card, section 3 |
| Series page header | Same words under the title, plus one plain line saying what the page lists | `team` on the page |
| Personal row | **No "Next meeting" box and no "Send agenda to attendees"** (it has nobody else to send to: 409 `no_attendees`, section 5), the scope line and the "private meetings" empty words, **only while `scoped` is true**. With `scoped` false (the flag is off: a person on two teams has a personal default team whose row still holds shared meetings and has a next date) the page is drawn as it was | `team.is_personal` and `scoped` on the page |
| Task drawer on the Series page | The people list is asked for in the **row's** team (`X-Team-Id: team.id`), not the tab's | `team.id` |
| Empty rows | Plain words for "no meeting left" and "nobody yet" (section 7) | the empty page of section 7 |

All of it is marked LEAD-DESIGNED in the code (`web/lib/series-team.ts`): the designer's files draw no team
on a recurring card or page.

## 3. The `team` field: on the card and on the page, at the top level

`team` is `{id, name, is_personal}` on **every** `SeriesCard` (`GET /api/series`, `POST /api/series`,
`PUT /api/series/{id}/project`, `PATCH /api/series/{id}`) and on the `SeriesPage` (`GET /api/series/{id}`).
Always present, flag on or off. **Captured live: the list and the page only.** The `POST`, `PUT` and `PATCH`
answers build `team` with the same function (`app/api_series.py::_team_ref`) and are the same `SeriesCard` schema
(`docs/openapi.json`); their bodies were **not** captured here. **A personal space is a team whose `name` is `"My team"`**: never show that
name for a personal row; show **"Personal"** when `is_personal` is true.

Before the backfill and with the flag off (capture 08, a team's list, the card already has `team`):

`GET /api/series`  `X-Team-Id: 4` as Ada → **200** (`docs/fe-captures/series-follow/08-off-cards-ada.json`)

```json
{
  "timezone": "UTC",
  "series": [
    {
      "id": 3,
      "title": "1:1",
      "team": {
        "id": 4,
        "name": "Platform team",
        "is_personal": false
      },
      "cadence": "Weekly on Saturdays, 20:00",
      "platform": "google_meet",
      "next_starts_at": "2026-11-21T20:00:00Z",
      "next_text": "Sat 21 Nov, 20:00",
      "unfinished_tasks": 1,
      "open_questions": 1,
      "project": null
    },
    {
      "id": 1,
      "title": "Standup",
      "team": {
        "id": 4,
        "name": "Platform team",
        "is_personal": false
      },
      "cadence": "Weekly on Saturdays, 20:00",
      "platform": "google_meet",
      "next_starts_at": "2026-11-21T20:00:00Z",
      "next_text": "Sat 21 Nov, 20:00",
      "unfinished_tasks": 2,
      "open_questions": 2,
      "project": null
    }
  ]
}
```

Before the backfill the Standup page is one row holding both of Ada's meetings, the shared one and the private one
(capture 09: `past_meetings` lists meetings 2 and 1, `docs/fe-captures/series-follow/09-off-page-ada.json`); that is the mixing this feature ends.

### Traps (nesting and nulls)

* **`scoped` (page only, top level, boolean).** `true` while `SERIES_FOLLOW_MEETINGS` is on: the page lists only
  its team's scope and a personal row has no next date. `false`: the old row, drawn as it was (a personal
  team's row may then hold shared meetings and has a real `next_text`). Do not infer it from `is_personal`.
  Captured: `false` in capture 09 (flag off, before the backfill), `true` on every flag-on page (24, 25, ...).

* **`team` on a series is not `team` on a meeting.** The meeting object's `team` is `{id, name}` (no
  `is_personal`) and is **`null` for a private meeting** (captures 37 and 42 below). The series card's and
  page's `team` is never null.
* `next_starts_at`, `next_text` and `sprint_note` are `null` both on a **personal row** and for "no calendar
  event ahead is known". Use `team.is_personal` to tell them apart.
* The **same recurring event has one row per scope**, each with its own `id`. After the backfill Ada's Standup is
  rows 1 (team X) and 11 (her personal space); her all-private 1:1 is rows 3 (X, empty) and 12 (personal); Pat's Design sync is rows 4 (personal), 13 (X) and 14 (Y)
  (`docs/fe-captures/series-follow/13-rows-after-backfill.json`). Never merge cards by title across teams; key by `id`.
* A **meeting's `series.id` changes** when the meeting is shared, unshared or moved (captures 37 and 42:
  `series.id` 1, then 11). Refetch the meeting after those calls; do not keep the old id.

## 4. The lists, per team

`GET /api/series` is read with the active team in `X-Team-Id` (unchanged). Ada has one ordinary team X
(id 4) and a personal space (id 1).

**Team X's list** (cards of both X's own Standup and Pat's Design sync, because Pat shared a meeting of it to X; the
private Standup meeting is not counted):

`GET /api/series`  `X-Team-Id: 4` as Ada → **200** (`docs/fe-captures/series-follow/14-cards-ada-x.json`)

```json
{
  "timezone": "UTC",
  "series": [
    {
      "id": 3,
      "title": "1:1",
      "team": {
        "id": 4,
        "name": "Platform team",
        "is_personal": false
      },
      "cadence": "Weekly on Saturdays, 20:00",
      "platform": "google_meet",
      "next_starts_at": "2026-11-21T20:00:00Z",
      "next_text": "Sat 21 Nov, 20:00",
      "unfinished_tasks": 0,
      "open_questions": 0,
      "project": null
    },
    {
      "id": 13,
      "title": "Design sync",
      "team": {
        "id": 4,
        "name": "Platform team",
        "is_personal": false
      },
      "cadence": "Weekly on Saturdays, 20:00",
      "platform": "google_meet",
      "next_starts_at": "2026-11-21T20:00:00Z",
      "next_text": "Sat 21 Nov, 20:00",
      "unfinished_tasks": 1,
      "open_questions": 1,
      "project": null
    },
    {
      "id": 1,
      "title": "Standup",
      "team": {
        "id": 4,
        "name": "Platform team",
        "is_personal": false
      },
      "cadence": "Weekly on Saturdays, 20:00",
      "platform": "google_meet",
      "next_starts_at": "2026-11-21T20:00:00Z",
      "next_text": "Sat 21 Nov, 20:00",
      "unfinished_tasks": 1,
      "open_questions": 1,
      "project": null
    }
  ]
}
```

**Ada's personal space** (the Standup row of her private meeting; `next_*` null, `is_personal` true):

`GET /api/series`  `X-Team-Id: 1` as Ada → **200** (`docs/fe-captures/series-follow/15-cards-ada-personal.json`)

```json
{
  "timezone": "UTC",
  "series": [
    {
      "id": 12,
      "title": "1:1",
      "team": {
        "id": 1,
        "name": "My team",
        "is_personal": true
      },
      "cadence": "Weekly on Saturdays, 20:00",
      "platform": "google_meet",
      "next_starts_at": null,
      "next_text": null,
      "unfinished_tasks": 1,
      "open_questions": 1,
      "project": null
    },
    {
      "id": 11,
      "title": "Standup",
      "team": {
        "id": 1,
        "name": "My team",
        "is_personal": true
      },
      "cadence": "Weekly on Saturdays, 20:00",
      "platform": "google_meet",
      "next_starts_at": null,
      "next_text": null,
      "unfinished_tasks": 1,
      "open_questions": 1,
      "project": null
    }
  ]
}
```

Same call with **no `X-Team-Id`**: the person's default team. Ada's is X, so the list is X's (capture 16, same
bodies as above). Pat is in two ordinary teams, so **his default team is his personal space**
(capture 20 is the personal list, row id 4): a frontend that sends no team to a two-team person gets the Personal
list, not a team's. Team Y's list for Pat (capture 18) holds only Design sync's row 14, with the meeting shared to Y.

**Empty state** (Quinn's personal space has no recurring meeting): `200`, an empty list, not an error. Draw
nothing (as today):

`GET /api/series`  `X-Team-Id: 3` as Quinn → **200** (`docs/fe-captures/series-follow/21-cards-quinn-personal-empty.json`)

```json
{
  "timezone": "UTC",
  "series": []
}
```

**A team the person is not in, or that does not exist** (the same answer for both):

`GET /api/series`  `X-Team-Id: 105` as Pat → **404** (`docs/fe-captures/series-follow/23-cards-not-my-team.json`)

```json
{
  "error": {
    "code": "not_found",
    "message": "No such team."
  }
}
```

Which teams a card list holds, for the other captures (title, team, is_personal):

| Capture | Person, team | Cards |
|---|---|---|
| `17-cards-pat-x` | Pat, X | id 13 Design sync (Platform team, is_personal false); id 1 Standup (Platform team, is_personal false) |
| `18-cards-pat-y` | Pat, Y | id 14 Design sync (Client Y, is_personal false) |
| `19-cards-pat-personal` | Pat, personal | id 4 Design sync (Personal, is_personal true) |
| `22-cards-quinn-y` | Quinn, Y | id 14 Design sync (Client Y, is_personal false) |

## 5. The page, per row

`GET /api/series/{id}` takes **no** `X-Team-Id`: the row's own team decides, so a person on two teams opens either
row through its link. What it lists is only the meetings in that row's scope.

**A team row** (Ada, X's Standup: meeting 1 only; the one task is trimmed as marked, nothing else is changed):

`GET /api/series/1` as Ada → **200** (`docs/fe-captures/series-follow/24-page-ada-x.json`)

```json
{
  "id": 1,
  "title": "Standup",
  "team": {
    "id": 4,
    "name": "Platform team",
    "is_personal": false
  },
  "scoped": true,
  "cadence": "Weekly on Saturdays, 20:00",
  "platform": "google_meet",
  "timezone": "UTC",
  "next_starts_at": "2026-11-21T20:00:00Z",
  "next_text": "Sat 21 Nov, 20:00",
  "sprint_note": null,
  "can_rename": true,
  "agenda_recipients": 0,
  "unfinished": [
    {
      "id": 1,
      "title": "Follow up on A1 shared",
      "...": "trimmed: the task object of docs/fe-integration-guide-tasks.md"
    }
  ],
  "finished_since_text": "Sat 31 Oct",
  "finished": [],
  "questions": [
    {
      "id": 1,
      "text": "Who owns A1 shared?",
      "meeting": {
        "id": 1,
        "title": "Standup",
        "platform": "google_meet",
        "created_at": "2026-10-31T20:00:00Z"
      },
      "can_update": true
    }
  ],
  "agenda": [],
  "topics": [],
  "suggested_agenda": [
    {
      "kind": "questions",
      "text": "Settle 1 open question carried over"
    }
  ],
  "past_meetings": [
    {
      "id": 1,
      "title": "Standup",
      "created_at": "2026-10-31T20:00:00Z",
      "duration_seconds": 1500,
      "tasks_count": 1
    }
  ],
  "attendees": [
    {
      "name": "Ada Example",
      "is_teammate": true
    },
    {
      "name": "Guest Visitor",
      "is_teammate": false
    }
  ]
}
```

**A personal row** (Ada's own, private Standup, meeting 2 only; note `team.is_personal`, `next_*` and
`sprint_note` null, `topics` `[]` and `agenda_recipients` 0). The one task is trimmed as marked:

`GET /api/series/11` as Ada → **200** (`docs/fe-captures/series-follow/25-page-ada-personal.json`)

```json
{
  "id": 11,
  "title": "Standup",
  "team": {
    "id": 1,
    "name": "My team",
    "is_personal": true
  },
  "scoped": true,
  "cadence": "Weekly on Saturdays, 20:00",
  "platform": "google_meet",
  "timezone": "UTC",
  "next_starts_at": null,
  "next_text": null,
  "sprint_note": null,
  "can_rename": true,
  "agenda_recipients": 0,
  "unfinished": [
    {
      "id": 2,
      "title": "Follow up on A2 private",
      "...": "trimmed: the task object of docs/fe-integration-guide-tasks.md"
    }
  ],
  "finished_since_text": "Sat 7 Nov",
  "finished": [],
  "questions": [
    {
      "id": 2,
      "text": "Who owns A2 private?",
      "meeting": {
        "id": 2,
        "title": "Standup",
        "platform": "google_meet",
        "created_at": "2026-11-07T20:00:00Z"
      },
      "can_update": true
    }
  ],
  "agenda": [],
  "topics": [],
  "suggested_agenda": [
    {
      "kind": "questions",
      "text": "Settle 1 open question carried over"
    }
  ],
  "past_meetings": [
    {
      "id": 2,
      "title": "Standup",
      "created_at": "2026-11-07T20:00:00Z",
      "duration_seconds": 1500,
      "tasks_count": 1
    }
  ],
  "attendees": [
    {
      "name": "Ada Example",
      "is_teammate": true
    },
    {
      "name": "Guest Visitor",
      "is_teammate": false
    }
  ]
}
```

**404s** (the same body whether the row does not exist or is not the person's: they must not tell apart).
Quinn is only in Y, so X's row is not his; Pat is in X but Ada's personal row is hers alone:

`GET /api/series/13` as Quinn → **404** (`docs/fe-captures/series-follow/30-page-quinn-x-404.json`)

```json
{
  "error": {
    "code": "not_found",
    "message": "No such recurring meeting"
  }
}
```

`GET /api/series/11` as Pat → **404** (`docs/fe-captures/series-follow/32-page-pat-ada-standup-404.json`)

```json
{
  "error": {
    "code": "not_found",
    "message": "No such recurring meeting"
  }
}
```

**Send agenda on a personal row** is refused (nobody else is on it). The frontend hides the button; this is
the answer if it is called anyway:

`POST /api/series/11/agenda/send` as Ada → **409** (`docs/fe-captures/series-follow/35-personal-send-agenda.json`)

```json
{
  "error": {
    "code": "no_attendees",
    "message": "There is nobody on your team to send it to yet. The agenda goes to teammates who were in the last three meetings."
  }
}
```

**A line typed on a personal row stays there** (capture 34 added it; capture 36, X's page, has an empty `agenda`
with the same row's team): the agenda is per row.

`GET /api/series/1` as Ada → **200** (`docs/fe-captures/series-follow/36-x-page-after-personal-line.json`)

```json
{
  "id": 1,
  "team": {
    "id": 4,
    "name": "Platform team",
    "is_personal": false
  },
  "agenda": [],
  "...": "trimmed: the rest of the page"
}
```

## 6. The rows follow the meetings

Sharing a meeting to a team moves it from the personal row to the team's row (the row is made if the team has none);
unsharing moves it back; moving to a team and moving back do the same; a person who leaves a team takes their
shared meetings back to their personal row. No frontend call is needed: refetch. Real sequence (Ada's Standup,
meeting 2):

1. `POST /api/meetings/2/share` `{"team_id": 4}` → 200, the meeting's `series` is now row 1 (X's):

`POST /api/meetings/2/share` as Ada → **200** (`docs/fe-captures/series-follow/37-share-a2.json`)

```json
{
  "id": 2,
  "title": "Standup",
  "shared_with_team": true,
  "team": {
    "id": 4,
    "name": "Platform team"
  },
  "owned_by_team": false,
  "series": {
    "id": 1,
    "title": "Standup"
  },
  "...": "trimmed: the rest is the meeting object of docs/fe-integration-guide-share.md"
}
```

2. X's card now counts both meetings, `2` unfinished tasks and `2` open questions (capture 38), and X's page lists meetings
   `[2, 1]` (capture 39). Ada's personal Standup row has **no meeting left** (section 7).
3. `DELETE /api/meetings/2/share` → 200, `series` is row 11 again (capture 42, `team` back to `null`); X's card is back
   to 1 and 1 (capture 43) and the personal card has its meeting again (capture 44).

Tasks follow too: the meeting's task was filed in her personal space and moved to X with it (capture 38 counts 2).

## 7. Empty states, in the words the frontend uses

A row that loses its last meeting is **not removed** (the "ghost" empty card; see Open decision below): the card stays,
with `0` and `0`; its page is all empty lists. This is the real answer after Ada shared her only private Standup
(card, capture 40):

`GET /api/series`  `X-Team-Id: 1` as Ada → **200** (`docs/fe-captures/series-follow/40-cards-ada-personal-after-share.json`)

```json
{
  "timezone": "UTC",
  "series": [
    {
      "id": 12,
      "title": "1:1",
      "team": {
        "id": 1,
        "name": "My team",
        "is_personal": true
      },
      "cadence": "Weekly on Saturdays, 20:00",
      "platform": "google_meet",
      "next_starts_at": null,
      "next_text": null,
      "unfinished_tasks": 1,
      "open_questions": 1,
      "project": null
    },
    {
      "id": 11,
      "title": "Standup",
      "team": {
        "id": 1,
        "name": "My team",
        "is_personal": true
      },
      "cadence": "Weekly on Saturdays, 20:00",
      "platform": "google_meet",
      "next_starts_at": null,
      "next_text": null,
      "unfinished_tasks": 0,
      "open_questions": 0,
      "project": null
    }
  ]
}
```

and the page (capture 41; the agenda line typed earlier is still there: lines belong to the row):

`GET /api/series/11` as Ada → **200** (`docs/fe-captures/series-follow/41-page-ada-personal-after-share.json`)

```json
{
  "id": 11,
  "title": "Standup",
  "team": {
    "id": 1,
    "name": "My team",
    "is_personal": true
  },
  "scoped": true,
  "cadence": "Weekly on Saturdays, 20:00",
  "platform": "google_meet",
  "timezone": "UTC",
  "next_starts_at": null,
  "next_text": null,
  "sprint_note": null,
  "can_rename": true,
  "agenda_recipients": 0,
  "unfinished": [],
  "finished_since_text": null,
  "finished": [],
  "questions": [],
  "agenda": [
    {
      "id": 1,
      "text": "Prepare my own notes",
      "can_remove": true
    }
  ],
  "topics": [],
  "suggested_agenda": [],
  "past_meetings": [],
  "attendees": []
}
```

**An all-private recurring meeting leaves an empty card in the team** (Ada's 1:1: every meeting private, so after the
backfill its X row has no meeting). It stays on X's list **for Ada only** (capture 14 lists it with `0` and `0`;
Pat's list, capture 17, does not), and opened it is empty (capture 50); to Pat it is a 404 (capture 51).
This is what the dry run's "Rows left with no meeting after the split" counts (capture 10: 1).

`GET /api/series/3` as Ada → **200** (`docs/fe-captures/series-follow/50-page-ada-x-emptied-row.json`)

```json
{
  "id": 3,
  "title": "1:1",
  "team": {
    "id": 4,
    "name": "Platform team",
    "is_personal": false
  },
  "scoped": true,
  "unfinished": [],
  "finished": [],
  "questions": [],
  "agenda": [],
  "topics": [],
  "past_meetings": [],
  "attendees": [],
  "...": "trimmed: cadence, platform, timezone, next_*, sprint_note, can_rename, agenda_recipients, finished_since_text, suggested_agenda (as on every page)"
}
```

`GET /api/series/3` as Pat → **404** (`docs/fe-captures/series-follow/51-page-pat-x-emptied-row-404.json`)

```json
{
  "error": {
    "code": "not_found",
    "message": "No such recurring meeting"
  }
}
```

| Where | Words (all in `web/lib/series-team.ts` and `series-view.tsx`) |
|---|---|
| Past meetings, personal row | None of your private meetings are in this series right now. |
| Past meetings, team row | No meeting of this series is shared with {team name} right now. |
| Past meetings, switch off | No meetings recorded in this series yet. (as before) |
| Attendees | Nobody yet. |
| Unfinished / Finished / Questions | Everything from last time is done. / Nothing finished yet. Completed tasks will show here. / No open questions. (as before) |
| Recurring cards, no row | Nothing is drawn (as before) |

**DECIDED 2026-11-15: the ghost empty card is removed by the person, with "Remove this recurring meeting" (section 10); the server never removes it by itself.** (Was: open decision, the ghost empty card.) A one-team person who shares their only private Standup
meeting keeps an empty "Standup" card in their Personal space, and one whose meetings were all private keeps an empty card in their team. By the design note's rule an empty card stays for its creator.
Not changed in this PR; the frontend renders it cleanly.

## 8. The server-side switch, as the frontend feels it

* **The backfill** (real output, run with the flag still off). Dry run first, then `--apply`, then a dry run that must find
  nothing. Counts only; no title, name or address is printed:

`docs/fe-captures/series-follow/10-backfill-1-dry-run.json`

```text
DRY RUN: nothing was changed. Run again with --apply to do it.
Recurring-meeting rows looked at: 3
Rows that held a meeting of another team or of the personal space: 3
Meetings moved to the row of their own team or personal space: 4
New rows made for them: 4
Meetings taken out of their series (the team had no room): 0
Meetings left alone (their recorder's account is gone): 0
Personal spaces made for people who had none: 0
Rows left with no meeting after the split: 1
...of which still hold waiting agenda lines: 0
```

`docs/fe-captures/series-follow/11-backfill-2-apply.json`

```text
APPLIED: the database was changed.
Recurring-meeting rows looked at: 3
Rows that held a meeting of another team or of the personal space: 3
Meetings moved to the row of their own team or personal space: 4
New rows made for them: 4
Meetings taken out of their series (the team had no room): 0
Meetings left alone (their recorder's account is gone): 0
Personal spaces made for people who had none: 0
Rows left with no meeting after the split: 1
...of which still hold waiting agenda lines: 0
```

`docs/fe-captures/series-follow/12-backfill-3-dry-run-again.json`

```text
DRY RUN: nothing was changed. Run again with --apply to do it.
Recurring-meeting rows looked at: 7
Rows that held a meeting of another team or of the personal space: 0
Meetings moved to the row of their own team or personal space: 0
New rows made for them: 0
Meetings taken out of their series (the team had no room): 0
Meetings left alone (their recorder's account is gone): 0
Personal spaces made for people who had none: 0
Rows left with no meeting after the split: 0
...of which still hold waiting agenda lines: 0
```

* **After the flag is on, a "straggler"** (a meeting put back in a row of another scope by hand, as an old writer could):
  every reader still hides it. X's page lists meeting 1 only (capture 45), the flag-on backfill moves it (capture 46,
  "moved … 1"), and the next dry run says 0 (capture 47).

`docs/fe-captures/series-follow/46-backfill-4-flag-on-apply-moves-the-straggler.json`

```text
APPLIED: the database was changed.
Recurring-meeting rows looked at: 7
Rows that held a meeting of another team or of the personal space: 1
Meetings moved to the row of their own team or personal space: 1
New rows made for them: 0
Meetings taken out of their series (the team had no room): 0
Meetings left alone (their recorder's account is gone): 0
Personal spaces made for people who had none: 0
Rows left with no meeting after the split: 0
...of which still hold waiting agenda lines: 0
```

`docs/fe-captures/series-follow/47-backfill-5-flag-on-dry-run-says-zero.json`

```text
DRY RUN: nothing was changed. Run again with --apply to do it.
Recurring-meeting rows looked at: 7
Rows that held a meeting of another team or of the personal space: 0
Meetings moved to the row of their own team or personal space: 0
New rows made for them: 0
Meetings taken out of their series (the team had no room): 0
Meetings left alone (their recorder's account is gone): 0
Personal spaces made for people who had none: 0
Rows left with no meeting after the split: 0
...of which still hold waiting agenda lines: 0
```

## 9. Verification table

| Behaviour | Status |
|---|---|
| `team` `{id, name, is_personal}` on list cards and pages, flag off and on | **verified live** (captures 08, 09, 14 to 29) |
| `team` on the answers of `POST /api/series`, `PATCH /api/series/{id}`, `PUT /api/series/{id}/project` | **not exercised live**: same builder and schema; shape from `docs/openapi.json` |
| One row per scope after the backfill; each list and page shows its own meetings only | **verified live** (13 to 29) |
| Default team with no header (one-team person, two-team person) | **verified live** (16, 20) |
| Empty personal list; 404 for a team that is not the person's | **verified live** (21, 23) |
| 404 for a row of another team or another person's personal row | **verified live** (30, 31, 32, 33) |
| Personal row (flag on): no next meeting, `agenda_recipients` 0, send refused 409 | **verified live** (15, 25, 35) |
| Agenda line stays on its row | **verified live** (34, 36, 41) |
| Share / unshare move the meeting between rows, counts follow | **verified live** (37 to 44) |
| Emptied row is a plain empty page | **verified live** (40, 41) |
| Backfill dry run, apply, repeat; the flag-on straggler round | **verified live** (10 to 12, 45 to 47) |
| Moving a meeting to a team and back, a person leaving a team, a team deleted | **not exercised live**: covered by `tests/test_series_follow.py` (leak cases 10, 11, 13); same hook as share |
| "Keeps coming up" never shown on a personal row, and a team's topics only from its scope | **not exercised live**: the model is not called here, so `topics` is `[]` on every page (vacuous); covered by `tests/test_series_readers.py` |
| Agenda mail and reminder mail (who gets what, one reminder per person, order) | **not exercised live**: no mail is built here; `scripts/capture_series_api.py` shows the mails before this change; the scope and the order are covered by `tests/test_series_readers.py` |
| `next_starts_at` on a team row when the next occurrence is on a teammate's calendar | **verified live** (capture 14: Ada has no event of Design sync, Pat's calendar gives it its next date in her X list) |
| An all-private recurring meeting leaves an empty card in the team, for its creator only | **verified live** (14, 17, 50, 51) |
| `scoped` on the page, true with the flag on and false with it off | **verified live** (09 false; 24, 25 and the rest true) |
| The task drawer's `GET /api/tasks` with `X-Team-Id` of a **personal** team | **not exercised live**: vitest against a mock only |
| Real Google or Outlook calendars; the real flag flip in the containers | **not exercised**: calendar rows are made as the sync leaves them; the flag is flipped inside the running process (`get_settings.cache_clear()`) |
| The frontend in a browser against a live server | **not exercised**: the frontend is tested against these captures (`web/tests/series-follow.test.tsx`) |
| `can_remove` on the page: true for the owner of the team, false for a plain member who did not make the row | **verified live** (52, 53) |
| `DELETE /api/series/{id}`: 200 with `meetings_kept`, `agenda_lines_removed`, `remembered`, for a team row, an emptied (ghost) row, a personal row with a waiting line and a hand-made row | **verified live** (57, 60, 64, 74, 76) |
| Remove errors: 403 `team_forbidden` for a plain member, 404 for a person outside the team, 404 for another person's personal row, 404 the second time | **verified live** (54, 55, 56, 65, 66) |
| A removal acts on one row: the sibling rows of the same event (personal, another team) are untouched | **verified live** (59, 61 to 63, 69) |
| The meeting stays, ungrouped (`series` null) and readable | **verified live** (67, 72) |
| The calendar sync does not make the removed row again | **verified live** (70: the real `series.sync_from_joins`, run in the capture process, not an HTTP call) |
| A later share to the removed scope does not regroup the meeting | **verified live** (71 to 73) |
| Remove with `SERIES_FOLLOW_MEETINGS` off; a later calendar meeting not regrouped; a creator who left the team (404) | **not exercised live**: covered by `tests/test_series_remove.py` |
| Remove without CSRF token or session | **not exercised live**: the project's usual write guard; covered by `tests/test_series_remove.py` |
| The remove button, the question and the toast in a browser against a live server | **not exercised**: tested against these captures (`web/tests/series-remove.test.tsx`), no screenshots |

## 10. Remove this recurring meeting

**LEAD-DESIGNED** (Adebayo, 2026-11-15): the designer drew no such control. Added in PR `feature/remove-recurring-meeting`;
SOP `docs/sop/2026-11-15-remove-recurring-meeting.md`. Captures 52 to 76, made by the same script
(`python scripts/capture_series_follow.py <admin url> --keep 51` writes only 52 and up; without `--keep` it rewrites all). Ids are
from a scratch database: yours will differ. It runs with the flag `SERIES_FOLLOW_MEETINGS` **on**, as production is; what it does with the flag off is
in the verification table (not exercised live).

### 10.1 What it is, in the person's terms

A person who may manage a recurring meeting can **remove** it. Its past meetings stay: they become ordinary meetings that are no longer grouped.
Nothing in them is deleted or changed (notes, tasks, decisions, open questions and sharing all stay). A recurring meeting that came
from a **calendar event** is remembered as removed **for that team (or that person's Personal space) only**, so the calendar does not make it
again and later meetings of that event are not grouped there. It acts on **one row**: removing Tech's Standup does not touch the Standup on
another team's page or on the person's Personal page (captures 59, 62, 63, 69).

This also settles the "ghost empty card" (section 7): an emptied card is not removed by the server by itself; the person removes it
with this action (capture 57).

What goes with the row, and what stays (the dialog says each of these in words, `web/components/app/series-remove.tsx`):

| Thing | What happens |
|---|---|
| Past meetings | stay, as ordinary meetings (capture 67: `series` is `null`, the meeting is still 200) |
| Their tasks, decisions, open questions, notes, sharing | untouched |
| Waiting "Other agenda items" lines | removed with the row (capture 74: `agenda_lines_removed` 1) |
| "Keeps coming up" topics, reminders, agenda sends of that row | removed with the row |
| The linked project | stays; only the link goes. Tasks already filed in it stay filed |
| The calendar event | remembered as removed for this team (or Personal space) when the row came from one; `remembered` says so |
| Another team's, or the person's personal, row of the same event | untouched |

### 10.2 Who may, and the new field `can_remove`

Every series page now carries `can_remove` (top level, next to `can_rename`). **Draw the button only when it is `true`.** The rule, on the server:
the person must be a member of the row's team, and be the person who made the row, or an owner or admin of that team. A personal row's only
member is its owner.

`GET /api/series/1` as Ada (owner of team X) → **200** (`docs/fe-captures/series-follow/52-remove-page-ada-x-before.json`)

```json
{
  "id": 1,
  "title": "Standup",
  "team": {
    "id": 4,
    "name": "Platform team",
    "is_personal": false
  },
  "scoped": true,
  "can_rename": true,
  "can_remove": true,
  "agenda_recipients": 0,
  "...": "trimmed: the rest of the page is section 5"
}
```

A plain member who did not make the row can read the page and gets `can_remove: false` (capture 53, same page, only these fields shown):

`GET /api/series/1` as Pat (a plain member of X) → **200** (`docs/fe-captures/series-follow/53-remove-pat-x-cannot.json`)

```json
{
  "id": 1,
  "title": "Standup",
  "team": {
    "id": 4,
    "name": "Platform team",
    "is_personal": false
  },
  "can_rename": false,
  "can_remove": false,
  "...": "trimmed: the rest of the page is section 5"
}
```

**Trap:** `can_remove` is on the **page** only, not on the list cards. There is no remove menu on a card: open the page.
`can_rename` and `can_remove` are not the same rule in one case: a plain member who made the row can both rename and remove it;
a plain member who did not make it can do neither. Do not derive one from the other.

### 10.3 The call

`DELETE /api/series/{id}`, no body. It needs the session cookie and the `X-CSRF-Token` header like every write. It names the row by id, so it needs
no `X-Team-Id`. It works for a row of any of the person's teams, or their personal row.

`DELETE /api/series/1` as Ada (owner of team X) → **200** (`docs/fe-captures/series-follow/64-remove-ada-x-standup.json`)

```json
{
  "meetings_kept": 1,
  "agenda_lines_removed": 0,
  "remembered": true
}
```

| Field | Meaning |
|---|---|
| `meetings_kept` | how many of the row's meetings **the caller may read**, which are now ordinary meetings (never a count of meetings the caller may not read) |
| `agenda_lines_removed` | the waiting agenda lines that went with the row |
| `remembered` | `true` when the row came from a calendar event and the event is now remembered as removed for this team or Personal space; `false` for a recurring meeting made by hand |

The 1:1 "ghost" card (a row with no meeting left), removed by its team's owner:

`DELETE /api/series/3` as Ada (owner of team X) → **200** (`docs/fe-captures/series-follow/57-remove-ada-ghost-1on1.json`)

```json
{
  "meetings_kept": 0,
  "agenda_lines_removed": 0,
  "remembered": true
}
```

A personal row with a waiting line (the line she typed earlier on that page goes with the row):

`DELETE /api/series/11` as Ada (owner of her personal space) → **200** (`docs/fe-captures/series-follow/74-remove-ada-personal-standup-emptied.json`)

```json
{
  "meetings_kept": 0,
  "agenda_lines_removed": 1,
  "remembered": true
}
```

A recurring meeting made by hand has no calendar event to remember:

`DELETE /api/series/15` as Ada (owner of team X) → **200** (`docs/fe-captures/series-follow/76-remove-hand-made-delete.json`)

```json
{
  "meetings_kept": 0,
  "agenda_lines_removed": 0,
  "remembered": false
}
```

### 10.4 Errors, exactly as returned

A plain member who did not make the row and is not an owner or admin (**403**, nothing changed). Show the `message` as is:

`DELETE /api/series/1` as Pat (a plain member of X) → **403** (`docs/fe-captures/series-follow/54-remove-pat-x-403.json`)

```json
{
  "error": {
    "code": "team_forbidden",
    "message": "You can't do that here. Ask an owner or admin of your team."
  }
}
```

A person outside the team, or a personal row of another person (**404**, nothing changed; the same answer as for a row that does not exist):

`DELETE /api/series/1` as Quinn (not in X) → **404** (`docs/fe-captures/series-follow/55-remove-quinn-x-404.json`)

```json
{
  "error": {
    "code": "not_found",
    "message": "No such recurring meeting"
  }
}
```

`DELETE /api/series/11` as Pat (in X) on Ada's personal row → **404** (`docs/fe-captures/series-follow/56-remove-pat-ada-personal-404.json`)

```json
{
  "error": {
    "code": "not_found",
    "message": "No such recurring meeting"
  }
}
```

The same removal again (**404**: the row is gone). Treat this 404 as "already removed" and carry on, as `series-remove.tsx` does:

`DELETE /api/series/1` as Ada → **404** (`docs/fe-captures/series-follow/65-remove-ada-x-standup-again.json`)

```json
{
  "error": {
    "code": "not_found",
    "message": "No such recurring meeting"
  }
}
```

Reading the removed row (**404**):

`GET /api/series/1` as Ada → **404** (`docs/fe-captures/series-follow/66-remove-page-ada-x-gone.json`)

```json
{
  "error": {
    "code": "not_found",
    "message": "No such recurring meeting"
  }
}
```

Not exercised live, from the schema and tests only: a missing or wrong CSRF token or no session (the project's usual 403 and 401, as for every write), a non-numeric id (404 like any
unknown id, tested in `tests/test_series_remove.py`).

### 10.5 What the person sees afterwards

* **The meeting stays.** Capture 67 is meeting 1, which was in the removed Standup: **200**, `series` is `null`. Do not keep the old `series.id` of a
  meeting; refetch the meeting.
* **Trap: `suggest_recurring` is `true` on those meetings** (captures 67 and 72: the call's link was used again). The existing "Make this a recurring meeting?" chip
  therefore still shows on a meeting that was just ungrouped. Pressing it makes a **new, hand-made** recurring meeting (`POST /api/meetings/{id}/recurring`), which is
  allowed: removal only stops the calendar from grouping it. The chip is not changed by this feature (a follow-up: hide it for a removed event).
* **The list.** The removed card is gone from that team's list (capture 68); the sibling rows are still there: Ada's Personal Standup (capture 69), Pat's X and
  Y rows of Design sync after he removed his Personal one (captures 62, 63).
* **The calendar does not bring it back.** The real hourly sync (`series.sync_from_joins`) makes 0 rows after the removals (capture 70, not an HTTP call, run
  in the capture process). A later calendar meeting of that event is not grouped in the removed scope.
* **A later share does not regroup it.** Ada shared meeting 2 (her private Standup) to X, where Standup was removed: the share worked and the meeting is in
  **no** recurring meeting (capture 72: `series` is `null`), and X's list has no Standup (capture 73).

**What the frontend does** (`web/components/app/series-remove.tsx`, `recurring-cards.tsx`): a red text button "Remove this recurring meeting" at the foot of the
Series page, only when `can_remove`. Pressing it opens the inline question (same box and buttons as "Delete meeting"): *Remove this recurring meeting? Your past
meetings stay. They will not be grouped any more. Nothing is deleted.* with one line each for the past meetings, the waiting agenda items (the number from
`agenda.length` of the page), the linked project, "Keeps coming up", tasks and open questions, the calendar, and (while multi-team is on) "this removes it from
{team} only". On success it goes to the Meetings page, where the Recurring cards say once: *Recurring meeting removed. Your past meetings stay.* A 404 is treated
as success; any other error shows the server's message and stays.

### 10.6 UX consequences the frontend must surface

* **It cannot be undone from the app.** The row, its waiting agenda lines and its topics are gone, and the event is remembered as removed. There is no "bring it
  back" call yet (a follow-up). Do not offer an Undo.
* **It is per team.** The same recurring meeting can be on a team's page and on the person's Personal page. Removing one leaves the other. The dialog says so
  while multi-team is on.
* **Do not promise the meetings are "deleted".** They are not. Say they stay and are no longer grouped.
* **An emptied card can now be removed**: it is the answer to the ghost card of section 7.
