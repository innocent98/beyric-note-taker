# FE integration guide: recurring meetings follow the meetings (team scope)

Everything in this file is a body, status or message **copied from a real response**: the capture script
`scripts/capture_series_follow.py` runs the real app (real migrations, auth, CSRF, team roles, visibility,
the backfill script) on a scratch Postgres and writes every exchange to `docs/fe-captures/series-follow/NN-name.json`. Run it again
to re-derive any claim. What was **not** exercised live is marked inline and listed in section 9. Sections 1 to 9 use captures 01 to 51; **section 10 (remove a recurring meeting) uses 52 to 89, section 11 (the simpler model) 90 to 106 and section 12 (bring back, and the hint on a Personal card) 107 to 129**. The page bodies in sections 1 to 9 were captured before `can_remove` was added to the page, so they do not show it; section 10.2 shows it.
Names, emails and ids are placeholders ("Ada Example", `owner@example.test`, ids from a scratch database:
yours will differ).

Related: `docs/fe-integration-guide-series.md` (the Series API this builds on),
`docs/fe-integration-guide-teams.md` (the team switcher and `X-Team-Id`),
`docs/superpowers/specs/2026-11-14-series-follow-meetings.md` (the design),
`docs/sop/2026-11-14-series-follow-switch-on.md` (the server steps),
`docs/sop/2026-11-15-remove-recurring-meeting.md` (section 10, removing a recurring meeting),
`docs/sop/2026-11-16-bring-back-removed-recurring-meeting.md` (section 12, bringing one back).

## 1. What this is, in the person's terms

> **Superseded in part (2026-11-16):** where sections 1 to 9 say a recurring card from a calendar is made in the person's default team, or that sharing a meeting makes the team's row, section 11 (the simpler model) replaces that: calendar cards live in the Personal space, and only an owner or admin makes a team's card. The captures 01 to 51 were made before that change; in particular the backfill output in captures 10 to 13 and 46 to 47 shows the backfill making team rows, which it no longer does (the system makes only Personal rows: a meeting whose team has no card is left ungrouped, and the report then has one more line, `Meetings left ungrouped (their team has no card for them): N`).

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
| `can_remove` on the page: true for the owner of the team; false for a plain member, including the one who made the row while it holds a meeting; true for the creator of a completely empty row | **verified live** (52, 53, 77, 80, 85) |
| `DELETE /api/series/{id}`: 200 with `meetings_kept`, `agenda_lines_removed`, `remembered`, for a team row, an emptied (ghost) row, a personal row with a waiting line and a hand-made row | **verified live** (57, 60, 64, 74, 76) |
| Remove errors: 403 `team_forbidden` for a plain member, **even the one who made the row, while something is in it (a meeting, a waiting line)**, 404 for a person outside the team, 404 for another person's personal row, 404 the second time | **verified live** (54, 55, 56, 65, 66, 78, 83) |
| The creator of a completely empty row may remove it (200); once a line is added, 403; the line removed, 200 again | **verified live** (79 to 86) |
| The creator's rule with a linked project, a pending agenda mail or reminder mail in the row, a sent mail not counting, a meeting or line arriving between the first look and the lock (refuse, nothing deleted), another plain member 404/403 | **not exercised live**: `tests/test_series_remove.py` |
| A connected calendar's recurring event still makes its card (the removal feature changes nothing for events never removed) | **verified live** (87: the real `series.sync_from_joins`, not an HTTP call; 88) |
| A removal acts on one row: the sibling rows of the same event (personal, another team) are untouched | **verified live** (59, 61 to 63, 69) |
| The meeting stays, ungrouped (`series` null) and readable | **verified live** (67, 72) |
| The calendar sync does not make the removed row again | **verified live** for a person in two teams (70: the real `series.sync_from_joins`, run in the capture process, not an HTTP call). **The one-team person who removes a Personal row: tests only** (`tests/test_series_remove.py`) |
| A later share to the removed scope does not regroup the meeting | **verified live** (71 to 73) |
| Remove with `SERIES_FOLLOW_MEETINGS` off; a later calendar meeting not regrouped; a creator who left the team (404) | **not exercised live**: covered by `tests/test_series_remove.py` |
| Remove without CSRF token or session | **not exercised live**: the project's usual write guard; covered by `tests/test_series_remove.py` |
| The remove button, the question and the toast in a browser against a live server | **not exercised**: tested against these captures (`web/tests/series-remove.test.tsx`), no screenshots |
| A calendar recurring card is made in the person's Personal space, not in a team (even a one-team person's) | **verified live** (87 to 89) |
| A plain member's share makes no team card; an owner's share makes it and takes in the meetings the team already has | **verified live** (90 to 96) |
| A plain member's hand-made team card and hand-attach are 403; their Personal card is 201 | **verified live** (97 to 99) |
| `can_make_recurring` and `suggest_recurring` on the meeting page | **verified live** (100, 101); the controls drawn from them: **tests only** (vitest) |
| The cleanup script: dry run, apply, dry run; the next sync makes the Personal card | **verified live** (102 to 106; the sync after it: `tests/test_series_personal_model.py`) |
| Reminders for a calendar-linked Personal card; a hand-made card never reminds; `reminders_on` off by default | **not exercised live**: fake-clock tests only |
| An owner's explicit share clears the "removed" mark and makes the card again | **verified live** (71 to 73) |
| Flag off: every rule of section 11 as before | **not exercised live**: `tests/test_series_personal_model.py` |
| The removed list for a Personal scope and for a team, with a name from the caller's own calendar else `Recurring meeting`, `removed_by`, the scope | **verified live** (112, 113) |
| An empty list | **verified live** (129) |
| The list: 403 `team_forbidden` for a plain member, 404 for a team that is not the caller's | **verified live** (114, 115) |
| Bring back: 200 `{team}` for a team's mark and for a Personal mark | **verified live** (120, 123) |
| Bring back: 403 for a plain member, 404 for Ada's Personal mark asked by someone else, 404 for an outsider, 404 for an unknown id, 404 the second time | **verified live** (116 to 119, 122) |
| The hourly sync makes no Personal card while the mark stands and makes it after bring-back | **verified live** (111 and 124: the real `series.sync_from_joins`, run in the capture process, not an HTTP call; 125 the list) |
| A team's card is not made by the bring-back itself | **verified live** (121) |
| A team's card made by an owner's auto-shared recording after the bring-back, with the team's meetings of the event joining it | **not exercised live**: `tests/test_series_bring_back.py` |
| Bring back changes nothing else (meetings, tasks, questions, rows, agenda lines, the other scopes' marks) | **not exercised live**: `tests/test_series_bring_back.py` |
| A hand-made recurring meeting and a plain creator's removal leave nothing to list; `removed_by` null; the 100-row limit; a malformed id; CSRF | **not exercised live**: `tests/test_series_bring_back.py` |
| Flag off: the list and bring back work on the default team's mark | **not exercised live**: `tests/test_series_bring_back.py` |
| `team_card` on a Personal page: null, then the team's card once one exists; null on the team's own page | **verified live** (108, 127, 128) |
| `team_card` null for a hand-made Personal card, for a card the caller may not see, with the flag off | **not exercised live**: `tests/test_series_bring_back.py` |
| The Settings section, the hint and the changed removal line in a browser against a live server | **not exercised**: tested against these captures (`web/tests/series-bring-back.test.tsx`), no screenshots |

## 10. Remove this recurring meeting

**LEAD-DESIGNED** (Adebayo, 2026-11-15): the designer drew no such control. Added in PR `feature/remove-recurring-meeting`;
SOP `docs/sop/2026-11-15-remove-recurring-meeting.md`. Captures 52 to 89, made by the same script
(`python scripts/capture_series_follow.py <admin url> --keep 51` writes only 52 and up; without `--keep` it rewrites all). Ids are
from a scratch database: yours will differ. It runs with the flag `SERIES_FOLLOW_MEETINGS` **on**, as production is; what it does with the flag off is
in the verification table (not exercised live).

### 10.1 What it is, in the person's terms

A person who may manage a recurring meeting (an owner or admin of its team, or the owner of a Personal row; the person who made a row only while it is completely empty) can **remove** it. Its past meetings stay: they become ordinary meetings that are no longer grouped.
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
| Another team's, or the person's personal, row of the same event | untouched. A meeting of another scope that a legacy row still pointed at is put into the row of its own scope, not ungrouped (tests only; production has none after the backfill) |

### 10.2 Who may, and the new field `can_remove`

Every series page now carries `can_remove` (top level, next to `can_rename`). **Draw the button only when it is `true`.** The rule, on the server:
**an owner or an admin of the row's team may remove it** (Adebayo, 2026-11-15: "Only owners and admins"). The person who made the row has **no power over a team row that has anything in it** (for a calendar row they are only whoever's calendar synced or recorded first), **but may remove it while it is completely empty** ("Yes, empty rows only"): no meeting grouped under it, no waiting agenda line, no linked project, no agenda or reminder mail still to be sent. That is how an empty ("ghost") card is cleared. A personal row's only member is its owner, so a person may remove their own Personal row, empty or not. Every other member gets 403.

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

A plain member can read the page and gets `can_remove: false` (capture 53, same page, only these fields shown). **So does the plain member who made the row, while something is in it** (capture 77, Pat's own calendar row, which holds a meeting):

`GET /api/series/1` as Pat (a plain member of X) → **200** (`docs/fe-captures/series-follow/53-remove-page-pat-x-cannot.json`)

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
`can_rename` and `can_remove` are **not** the same rule: a plain member who made the row can rename it (`can_rename` true) but, while it has anything in it, not remove it (`can_remove` false); capture 77 shows both fields on one page. **`can_remove` can change from one read to the next for the same person** (it turns false when a line is added to a creator's empty row and true again when it is taken out: captures 80 to 85), so read it from the page you just fetched, never cache it. Do not derive one from the other.

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

`DELETE /api/series/14` as Ada (owner of team X) → **200** (`docs/fe-captures/series-follow/76-remove-hand-made-delete.json`)

```json
{
  "meetings_kept": 0,
  "agenda_lines_removed": 0,
  "remembered": false
}
```

### 10.4 Errors, exactly as returned

A plain member who is not the row's creator, or its creator while something is in it (**403**, nothing changed; capture 78 is the creator of a row holding a meeting). Show the `message` as is:

`DELETE /api/series/1` as Pat (a plain member of X) → **403** (`docs/fe-captures/series-follow/54-remove-pat-x-403.json`)

```json
{
  "error": {
    "code": "team_forbidden",
    "message": "You can't do that here. Ask an owner or admin of your team."
  }
}
```

The plain member removing the calendar row he made himself, which holds a meeting: the same 403 (captures 77 and 78):

`GET /api/series/7` as Pat (a plain member of X, who made the row) → **200** (`docs/fe-captures/series-follow/77-remove-page-pat-own-x-row.json`)

```json
{
  "id": 7,
  "title": "Design sync",
  "team": {
    "id": 4,
    "name": "Platform team",
    "is_personal": false
  },
  "can_rename": true,
  "can_remove": false,
  "...": "trimmed: the rest of the page is section 5"
}
```

`DELETE /api/series/7` as Pat → **403** (`docs/fe-captures/series-follow/78-remove-pat-own-x-row-403.json`)

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

### 10.4b The creator and an empty row (legacy rows)

A plain member can no longer make a team's recurring meeting (section 11), so this applies to **legacy rows**: an empty hand-made row that a plain member made before that change (an empty card shows to its creator only). While it is completely empty its creator may remove it. Capture 79 is the row inserted directly (not an HTTP call); the rest are real calls. Same row, same person, only what is in it changes:

`docs/fe-captures/series-follow/79-remove-legacy-empty-hand-made-row.json`

```json
{
  "id": 15,
  "team_id": 4,
  "made_by": "a plain member, before the change"
}
```

`GET /api/series/15` as Pat (a plain member of X) → **200** (`docs/fe-captures/series-follow/80-remove-page-pat-empty-hand-made.json`)

```json
{
  "id": 15,
  "title": "Pat's own recurring",
  "team": {
    "id": 4,
    "name": "Platform team",
    "is_personal": false
  },
  "can_rename": true,
  "can_remove": true,
  "...": "trimmed: the rest of the page is section 5"
}
```

He adds one agenda line; the row is no longer empty:

`POST /api/series/15/agenda` as Pat → **201** (`docs/fe-captures/series-follow/81-remove-pat-hand-made-add-line.json`)

```json
{
  "id": 2,
  "text": "Plan the offsite",
  "can_remove": true
}
```

`GET /api/series/15` as Pat → **200** (`docs/fe-captures/series-follow/82-remove-page-pat-hand-made-with-line.json`)

```json
{
  "id": 15,
  "title": "Pat's own recurring",
  "team": {
    "id": 4,
    "name": "Platform team",
    "is_personal": false
  },
  "can_rename": true,
  "can_remove": false,
  "...": "trimmed: the rest of the page is section 5"
}
```

**403**, nothing changed:

`DELETE /api/series/15` as Pat → **403** (`docs/fe-captures/series-follow/83-remove-pat-hand-made-with-line-403.json`)

```json
{
  "error": {
    "code": "team_forbidden",
    "message": "You can't do that here. Ask an owner or admin of your team."
  }
}
```

He removes his own line (its author may), the row is empty again, and the removal is allowed. `remembered` is **false**: a hand-made row has no calendar event to remember, and a plain creator's removal never writes the team-wide mark (only an owner's or admin's does):

`DELETE /api/series/15/agenda/2` as Pat → **204** (`docs/fe-captures/series-follow/84-remove-pat-hand-made-remove-line.json`)

(no body)

`GET /api/series/15` as Pat → **200** (`docs/fe-captures/series-follow/85-remove-page-pat-hand-made-empty-again.json`)

```json
{
  "id": 15,
  "title": "Pat's own recurring",
  "team": {
    "id": 4,
    "name": "Platform team",
    "is_personal": false
  },
  "can_rename": true,
  "can_remove": true,
  "...": "trimmed: the rest of the page is section 5"
}
```

`DELETE /api/series/15` as Pat → **200** (`docs/fe-captures/series-follow/86-remove-pat-hand-made-delete.json`)

```json
{
  "meetings_kept": 0,
  "agenda_lines_removed": 0,
  "remembered": false
}
```

What counts as "something in it" is decided on the server, again at the moment of removal (under the row's lock): a meeting grouped under it, a waiting agenda line, a linked project, or an agenda or reminder mail of it that has not gone yet. If one arrives between the page load and the click the answer is the same 403 and nothing is deleted. Not exercised live (tests only): the project, the pending mail and the race.

### 10.5 What the person sees afterwards

* **The meeting stays.** Capture 67 is meeting 1, which was in the removed Standup: **200**, `series` is `null`. Do not keep the old `series.id` of a
  meeting; refetch the meeting.
* **Trap: `suggest_recurring` is `true` on those meetings** (capture 67: the call's link was used again; it is `false` when the caller may not group the meeting, section 11). The existing "Make this a recurring meeting?" chip
  therefore still shows on a meeting that was just ungrouped. Pressing it makes a **new, hand-made** recurring meeting (`POST /api/meetings/{id}/recurring`), which is
  allowed: removal only stops the calendar from grouping it. The chip is not changed by this feature (a follow-up: hide it for a removed event).
* **The list.** The removed card is gone from that team's list (capture 68); the sibling rows are still there: Ada's Personal Standup (capture 69), Pat's X and
  Y rows of Design sync after he removed his Personal one (captures 62, 63).
* **The calendar does not bring it back.** The real hourly sync (`series.sync_from_joins`) makes 0 rows after the removals (capture 70, not an HTTP call, run
  in the capture process). A later calendar meeting of that event is not grouped in the removed scope.
* **An owner's or admin's explicit share makes the card again.** Ada, the owner of X, shared meeting 2 (her private Standup) to X, where Standup had been removed
  (capture 71): the meeting is now in a **new** Standup row of X with a new id (capture 72: `series` `{id: 15, title: "Standup"}`), X's list shows Standup again (capture 73),
  and the "removed" mark for X is cleared. A plain member's share would make no card (section 11), and the automatic link after a recording still respects the mark.

**What the frontend does** (`web/components/app/series-remove.tsx`, `recurring-cards.tsx`): a red text button "Remove this recurring meeting" at the foot of the
Series page, only when `can_remove`. Pressing it opens the inline question (same box and buttons as "Delete meeting"): *Remove this recurring meeting? This removes it for everyone in {team}. The past meetings stay. They will not be grouped any more. No meeting is deleted. You can't undo this.* (on a Personal row: *Your past meetings stay. They will not be grouped any more. No meeting is deleted. You can't undo this.*) with one line each for the past meetings, the waiting agenda items (the number from
`agenda.length` of the page), the linked project, "Keeps coming up", tasks and open questions, the calendar, and (while multi-team is on) "this removes it from
{team} only" (on a Personal row: "from your Personal space only; the same recurring meeting on a team's page is not changed"). On success it goes to the Meetings page, where the Recurring cards say once: *Recurring meeting removed. Your past meetings stay.* A 404 is treated
as success; any other error shows the server's message and stays.

### 10.6 UX consequences the frontend must surface

* **It cannot be undone in the sense of getting the row back.** The row, its waiting agenda lines and its topics are gone. The calendar's memory of the removal can be cleared with **Bring back**
  (section 12), which lets an empty card be made again later. Do not offer an Undo on the removal itself.
* **It is per team.** The same recurring meeting can be on a team's page and on the person's Personal page. Removing one leaves the other. The dialog says so
  while multi-team is on.
* **Do not promise the meetings are "deleted".** They are not. Say they stay and are no longer grouped.
* **An emptied card can now be removed**: by an owner or admin of its team (capture 57), and by the person who made it while it is completely empty (captures 80 and 86). That closes the ghost card of section 7:
  an empty card is shown only to the person who made it, who may now remove it. A plain member's row with a waiting line, a meeting, a linked project or a mail still to go in it is not empty: they get 403 there, and the
  page's `can_remove` says so.

### 10.7 A connected calendar still makes its recurring cards, in the person's Personal space

Removal changes nothing about how a connected calendar produces recurring cards, except that they are now made in the person's **Personal space**, never in a team (section 11). The hourly sync (`series.sync_from_joins`) makes a card for each recurring event that the scheduler will join (a join is scheduled for it, so within the hour of the calendar sync, not the instant the calendar is connected), and a recording's notes link the meeting to its event's card. The only thing removal adds is a check that skips an event somebody **removed** for that scope. Real run, for an event that was never removed (capture 87 is the real sync run in the capture process, not an HTTP call; 88 and 89 are the lists):

`docs/fe-captures/series-follow/87-remove-calendar-recurring-still-makes-a-card.json`

```json
{
  "rows_made": 1,
  "recurring_event_id": "rid-connected"
}
```

Ada's **Personal** list holds the new card (capture 88, only the new card shown); her team X list does not (capture 89, `Weekly planning` is not there although X is her default team):

```json
{
  "id": 16,
  "title": "Weekly planning",
  "team": {
    "id": 1,
    "name": "My team",
    "is_personal": true
  },
  "cadence": "Recurring",
  "platform": "google_meet",
  "next_starts_at": null,
  "next_text": null,
  "unfinished_tasks": 0,
  "open_questions": 0,
  "project": null
}
```

## 11. The simpler model: where a recurring card lives and who makes a team card

Adebayo, 2026-11-15/16: "I just don't want us to complicate things". It applies while `SERIES_FOLLOW_MEETINGS` is on (as in production). With it off every rule below is as before (not exercised live; `tests/test_series_personal_model.py`). Branch `feature/series-personal-calendar-owner-admin-teams`, SOP `docs/sop/2026-11-16-series-personal-calendar-owner-admin-teams.md`. Captures 90 to 106. **Sections 1 to 9 describe the model before this change; where they say a card is made in a person's default team, or that a share makes the team's row, this section replaces them.**

### 11.1 The rules, in the person's terms

| What | Rule |
|---|---|
| A recurring card from a person's **own calendar** | always in their **Personal space**, never in a team, even when a team is their default (captures 88 and 89). Nothing overrides it and it is never moved into a team: an owner's or admin's share of a meeting of the event makes a **separate** team card, and the Personal card stays (tests only, one-team, two-team and plain member) |
| A **team's** recurring card | made only by an **owner or admin** of the team: when they share or move a meeting of a recurring calendar event to it (or, when their recording is auto-shared, by the automatic link), or by hand (`POST /api/series`, `PUT /api/meetings/{id}/series`, `POST /api/meetings/{id}/recurring`) |
| A plain member **shares** a meeting | the share works as ever (`shared_with_team` true) and makes **no card** (`series` null, captures 91 to 93). If the team already has the card, the meeting **joins it** |
| A plain member makes or adds **by hand** to a team's card | **403** `team_forbidden` (captures 97 and 99). In their own Personal space they may (capture 98) |
| An owner or admin makes the team's card | the meetings of the same recurring event that the team **already has** (shared to it or moved to it) and that no card holds yet **join it** (capture 96: both meetings). Only meetings whose calendar event row still exists can be found (about two days after the event) |
| An owner's or admin's **share or move** after the card was removed | makes the card again and **clears the "removed" mark** for that team (section 10.5). The automatic link after a recording respects the mark |
| Taking a meeting **out** of a recurring meeting | unchanged: its owner may |

### 11.2 A plain member shares: no card (captures 90 to 93)

Pat, a plain member of X, recorded a private meeting of a recurring event; the link put it in a card in his Personal space (capture 90). He shares it to X:

`POST /api/meetings/7/share` as Pat (a plain member of X) → **200** (`docs/fe-captures/series-follow/91-model-pat-shares-no-card.json`)

```json
{
  "id": 7,
  "title": "Design review",
  "shared_with_team": true,
  "team": {
    "id": 4,
    "name": "Platform team"
  },
  "series": null,
  "...": "trimmed: the rest is the meeting object of docs/fe-integration-guide-share.md"
}
```

The meeting page: shared, and in **no** recurring meeting:

`GET /api/meetings/7` as Pat → **200** (`docs/fe-captures/series-follow/92-model-pat-shared-meeting.json`)

```json
{
  "id": 7,
  "title": "Design review",
  "shared_with_team": true,
  "team": {
    "id": 4,
    "name": "Platform team"
  },
  "series": null,
  "suggest_recurring": false,
  "can_make_recurring": false,
  "recurring_team_id": 4,
  "...": "trimmed: the rest is the meeting object"
}
```

X's list has no card for it (capture 93: `Design review` is not in it).

### 11.3 An owner shares: the card is made, and takes in the meetings the team already has (captures 94 to 96)

`POST /api/meetings/8/share` as Ada (owner of X) → **200** (`docs/fe-captures/series-follow/94-model-ada-shares-makes-card.json`)

```json
{
  "id": 8,
  "title": "Design review",
  "shared_with_team": true,
  "team": {
    "id": 4,
    "name": "Platform team"
  },
  "series": {
    "id": 19,
    "title": "Design review"
  },
  "...": "trimmed: the rest is the meeting object"
}
```

(capture 95 is X's list, now with `Design review`.) The new card's page lists **both** meetings, Ada's and Pat's earlier one that was waiting without a card:

`GET /api/series/19` as Ada → **200** (`docs/fe-captures/series-follow/96-model-x-card-page.json`)

```json
{
  "id": 19,
  "title": "Design review",
  "team": {
    "id": 4,
    "name": "Platform team",
    "is_personal": false
  },
  "can_remove": true,
  "...": "trimmed: the rest of the page is section 5"
}
```

`past_meetings` in capture 96 holds meetings 8 and 7 (Ada's and Pat's).

### 11.4 By hand (captures 97 to 101)

A plain member cannot make a team's card; in their own Personal space they can:

`POST /api/series`  `X-Team-Id: 4` as Pat → **403** (`docs/fe-captures/series-follow/97-model-pat-creates-team-card-403.json`)

```json
{
  "error": {
    "code": "team_forbidden",
    "message": "You can't do that here. Ask an owner or admin of your team."
  }
}
```

`POST /api/series`  `X-Team-Id: 2` as Pat → **201** (`docs/fe-captures/series-follow/98-model-pat-creates-personal-card.json`)

```json
{
  "id": 20,
  "title": "Pat's weekly",
  "team": {
    "id": 2,
    "name": "My team",
    "is_personal": true
  },
  "cadence": "Recurring",
  "platform": null,
  "next_starts_at": null,
  "next_text": null,
  "unfinished_tasks": 0,
  "open_questions": 0,
  "project": null
}
```

Nor add a meeting to a team's card by hand (his shared meeting 'Retro', not in a card):

`PUT /api/meetings/9/series` as Pat → **403** (`docs/fe-captures/series-follow/99-model-pat-attach-403.json`)

```json
{
  "error": {
    "code": "team_forbidden",
    "message": "You can't do that here. Ask an owner or admin of your team."
  }
}
```

**The meeting page tells the frontend whether to draw the two controls and which team to ask about** (new fields `can_make_recurring` and `recurring_team_id` on the meeting, next to `suggest_recurring`). `recurring_team_id` is the team to send as `X-Team-Id` to list and make recurring meetings for this meeting: its own or shared team, else the recorder's **Personal** space (a private meeting's `team` is null, so never guess it from `team`); null for anyone but the recorder. For a plain member's shared meeting both are `false`: draw neither "Add to recurring meeting" nor "Make this a recurring meeting?" (capture 100). A meeting already in a recurring meeting still shows its link, without "Change". For the owner of X (capture 101) `can_make_recurring` is `true`. A private meeting is always `true` for its recorder (it goes to their Personal card).

`GET /api/meetings/9` as Pat → **200** (`docs/fe-captures/series-follow/100-model-pat-meeting-page-flags.json`)

```json
{
  "id": 9,
  "title": "Retro",
  "shared_with_team": true,
  "series": null,
  "suggest_recurring": false,
  "can_make_recurring": false,
  "recurring_team_id": 4,
  "...": "trimmed: the rest is the meeting object"
}
```

`GET /api/meetings/8` as Ada → **200** (`docs/fe-captures/series-follow/101-model-ada-meeting-page-flags.json`)

```json
{
  "id": 8,
  "title": "Design review",
  "shared_with_team": true,
  "series": {
    "id": 19,
    "title": "Design review"
  },
  "suggest_recurring": false,
  "can_make_recurring": true,
  "recurring_team_id": 4,
  "...": "trimmed: the rest is the meeting object"
}
```

**What a plain member sees instead:** nothing. There is no disabled button and no message: the "Series" row shows only the link to the team's recurring meeting when the meeting is in one, and is absent otherwise. (Not exercised live in a browser; the controls' presence is tested against these bodies in `web/tests/series-lead-designed.test.tsx`.)

### 11.5 The cleanup of the empty cards the old sync made in a team (captures 102 to 106)

The old sync made an empty placeholder in the person's default team for each recurring event they would record. `scripts/remove_empty_team_cards.py` removes the **empty, calendar-made** cards of ordinary teams, **without writing any "removed" mark**, so the next sync makes the person's Personal card. A card with a meeting, a waiting agenda line, a linked project or a mail still to go stays; so do hand-made cards and every Personal card. Real output (capture 102 is the list before, two placeholders in it):

`docs/fe-captures/series-follow/103-model-cleanup-1-dry-run.json`

```text
DRY RUN: nothing was changed. Run again with --apply to do it.
Calendar-made recurring cards of teams looked at: 6
Empty ones removed: 2
Kept, because something is in them: 4
...a meeting is grouped under it: 4
...an agenda line is waiting: 0
...a project is linked: 0
...a mail about it is still to be sent: 0
```

`docs/fe-captures/series-follow/104-model-cleanup-2-apply.json`

```text
APPLIED: the database was changed.
Calendar-made recurring cards of teams looked at: 6
Empty ones removed: 2
Kept, because something is in them: 4
...a meeting is grouped under it: 4
...an agenda line is waiting: 0
...a project is linked: 0
...a mail about it is still to be sent: 0
```

`docs/fe-captures/series-follow/105-model-cleanup-3-dry-run-again.json`

```text
DRY RUN: nothing was changed. Run again with --apply to do it.
Calendar-made recurring cards of teams looked at: 4
Empty ones removed: 0
Kept, because something is in them: 4
...a meeting is grouped under it: 4
...an agenda line is waiting: 0
...a project is linked: 0
...a mail about it is still to be sent: 0
```

Capture 106 is X's list after: the placeholders are gone and the cards that hold meetings stay.

### 11.6 Reminders (not exercised live: tests only)

The mail is unchanged. A calendar-linked **Personal** card with one recorded meeting queues the 1-hour and 30-minute reminders (`tests/test_series_personal_model.py`, fake clock) **when the Personal team's `reminders_on` is on** (it is off by default and **stays off by default**; a person whose team has reminders on gets none for a calendar card that now lives in Personal until they switch Personal on: switch to Personal, then Settings, **Remind attendees before recurring meetings**). It needs: a card made from a calendar event (a hand-made card has no next occurrence and never reminds), at least one recorded meeting or a waiting agenda line in the card, the event on the person's own calendar inside the window (55 minutes before for the 1-hour mail, 20 minutes for the 30-minute one), and the person not having switched reminder mail off. A placeholder card with no recorded meeting sends nothing.

### 11.7 UX consequences the frontend must surface

* A recurring meeting from the person's own calendar appears in **Personal**, not in the team's Meetings page. Do not tell a person it will appear in their team.
* A plain member who shares a recurring meeting sees it shared but not grouped, until an owner or admin makes the team's card. Do not show an error.
* Do not offer "Add to recurring meeting" or "Make this a recurring meeting?" when `can_make_recurring` is `false`. Send `recurring_team_id` as `X-Team-Id` for the picker's list and create.
* The Personal card of an event stays the person's own: once a team card exists, their recordings go into the team's card and the lines typed into the Personal card are not carried into it or mailed. The Personal page now says so: `team_card` (section 12.5).
* A plain member's shared recordings stay ungrouped while their Personal card is empty, until an owner or admin makes the team's card (then those meetings join it). The empty Personal card is not a fault.

## 12. Bring back a removed recurring meeting, and the hint on a Personal card

**LEAD-DESIGNED** (a follow-up of sections 10 and 11; the designer drew neither piece). Branch `feature/recurring-bring-back`, SOP
`docs/sop/2026-11-16-bring-back-removed-recurring-meeting.md`. **No migration.** Captures 107 to 129, made by the same script
(`python scripts/capture_series_follow.py <admin url> --keep 106` writes only 107 and up). Every body below is pasted from a capture.
It runs with `SERIES_FOLLOW_MEETINGS` **on**. Ids are from a scratch database: yours will differ.

### 12.1 What it is, in the person's terms

Removing a recurring meeting from a calendar (section 10) is remembered **per scope** (a team's, or one person's Personal space) so the sync
does not make the card again. This section adds a list of what was removed and a **Bring back** button that forgets that one memory.

* **Bring back does not make the card at that moment, and it never restores anything.** It only lets the card be made again: a **Personal** card by
  the next hourly calendar sync (within the hour, once a join of the event is scheduled); a **team's** card the next time an owner or admin shares a
  meeting of that event to the team, or their recording of it is shared automatically (the hourly sync only makes Personal cards, section 11). The
  agenda lines, the "Keeps coming up" topics and the project link that went with the old card are **not** restored, and the past meetings are **not**
  grouped into the new card (a team's new card does take in the meetings of the same event the team already has, section 11.3).
* **A recurring meeting made by hand has nothing to bring back.** Removing it leaves no memory (capture 86: `remembered` false), so it is never listed.
  The same for the empty legacy card a plain member removed (it writes no mark).
* **Who:** exactly who may remove. A team's list and its Bring back: an **owner or admin** of the team. A Personal list: the person. Any other member of the
  team gets 403 `team_forbidden`; someone outside the team gets 404.
* Draw it in **Settings**, as its own card ("Removed recurring meetings"), only for an owner or admin of the current team or the person in their Personal space
  (`team.can_manage` from `GET /api/team`), and only when the list has something in it.

### 12.2 The list: `GET /api/series/removed`

The `X-Team-Id` header names the team or the person's Personal space (the client sends it on this path like on `GET /api/series`); without it the default team's.

`docs/fe-captures/series-follow/112-bring-back-list-personal.json`

```text
GET /api/series/removed   (X-Team-Id: 1)  ->  200
```

```json
{
  "removed": [
    {
      "id": 5,
      "title": "Weekly planning",
      "team": {
        "id": 1,
        "name": "My team",
        "is_personal": true
      },
      "removed_at": "2026-11-16T13:52:21.111884Z",
      "removed_by": "Ada Example"
    },
    {
      "id": 4,
      "title": "Standup",
      "team": {
        "id": 1,
        "name": "My team",
        "is_personal": true
      },
      "removed_at": "2026-11-16T13:52:20.701333Z",
      "removed_by": "Ada Example"
    }
  ]
}
```

`docs/fe-captures/series-follow/113-bring-back-list-team-x.json`

```text
GET /api/series/removed   (X-Team-Id: 4)  ->  200
```

```json
{
  "removed": [
    {
      "id": 6,
      "title": "Recurring meeting",
      "team": {
        "id": 4,
        "name": "Platform team",
        "is_personal": false
      },
      "removed_at": "2026-11-16T13:52:21.119545Z",
      "removed_by": "Ada Example"
    },
    {
      "id": 1,
      "title": "1:1",
      "team": {
        "id": 4,
        "name": "Platform team",
        "is_personal": false
      },
      "removed_at": "2026-11-16T13:52:20.535019Z",
      "removed_by": "Ada Example"
    }
  ]
}
```

`docs/fe-captures/series-follow/129-bring-back-list-quinn-y-empty.json`

```text
GET /api/series/removed   (X-Team-Id: 5)  ->  200
```

```json
{
  "removed": []
}
```


Field notes (all from the captures above):

* **`id` is the removal mark's id, not a series id.** It is what `bring-back` takes. Do not link it to `/series/{id}`.
* **`title` is what the caller's own calendar calls the event, else `"Recurring meeting"`** (capture 113: the first row, the owner's team list, has no event of her own for it).
  The server never uses a teammate's calendar title, because the mark holds only the event's id. Expect several rows to say `Recurring meeting`; show the date and the name that removed it so they can be told apart.
* `removed_by` is a **display name**, or **`null`** once that person has left the team (the `null` case is **not captured live**: tests only; the field is typed `string | null`).
* Newest removal first. At most 100 rows (not exercised live).
* The scope is on every row (`team`), so a row says whether it is "Personal" (`team.is_personal`) or a team.

Errors of the list:

`docs/fe-captures/series-follow/114-bring-back-list-pat-x-403.json`

```text
GET /api/series/removed   (X-Team-Id: 4)  ->  403
```

```json
{
  "error": {
    "code": "team_forbidden",
    "message": "You can't do that here. Ask an owner or admin of your team."
  }
}
```

`docs/fe-captures/series-follow/115-bring-back-list-quinn-x-404.json`

```text
GET /api/series/removed   (X-Team-Id: 4)  ->  404
```

```json
{
  "error": {
    "code": "not_found",
    "message": "No such team."
  }
}
```


### 12.3 Bring back: `POST /api/series/removed/{id}/bring-back`

No body. Needs the `X-CSRF-Token` header like every write (a missing token is 403 from the project's usual guard: **not exercised live**, covered by `tests/test_series_bring_back.py`).
It does not read `X-Team-Id`: the mark's id says which scope.

`docs/fe-captures/series-follow/120-bring-back-team.json`

```text
POST /api/series/removed/6/bring-back  ->  200
```

```json
{
  "team": {
    "id": 4,
    "name": "Platform team",
    "is_personal": false
  }
}
```

`docs/fe-captures/series-follow/123-bring-back-personal.json`

```text
POST /api/series/removed/5/bring-back  ->  200
```

```json
{
  "team": {
    "id": 1,
    "name": "My team",
    "is_personal": true
  }
}
```


The answer names the scope whose mark was cleared (`team`), so the screen can say which. **A second call for the same mark is 404**, as is an unknown id:

`docs/fe-captures/series-follow/122-bring-back-team-again-404.json`

```text
POST /api/series/removed/6/bring-back  ->  404
```

```json
{
  "error": {
    "code": "not_found",
    "message": "No such removed recurring meeting."
  }
}
```

`docs/fe-captures/series-follow/119-bring-back-unknown-404.json`

```text
POST /api/series/removed/987654/bring-back  ->  404
```

```json
{
  "error": {
    "code": "not_found",
    "message": "No such removed recurring meeting."
  }
}
```


Who may not:

`docs/fe-captures/series-follow/116-bring-back-pat-team-403.json`

```text
POST /api/series/removed/6/bring-back  ->  403
```

```json
{
  "error": {
    "code": "team_forbidden",
    "message": "You can't do that here. Ask an owner or admin of your team."
  }
}
```

`docs/fe-captures/series-follow/117-bring-back-pat-ada-personal-404.json`

```text
POST /api/series/removed/5/bring-back  ->  404
```

```json
{
  "error": {
    "code": "not_found",
    "message": "No such removed recurring meeting."
  }
}
```

`docs/fe-captures/series-follow/118-bring-back-quinn-team-404.json`

```text
POST /api/series/removed/6/bring-back  ->  404
```

```json
{
  "error": {
    "code": "not_found",
    "message": "No such removed recurring meeting."
  }
}
```


A malformed id (`/api/series/removed/abc/bring-back`) is a plain 404 (**not exercised live**; `tests/test_series_bring_back.py`).

### 12.4 What the person sees next

* Capture 121 is X's card list right after bringing back X's mark: **no card for it** (the call made none). Capture 111 and 124 are NOT HTTP calls: the real
  `series.sync_from_joins` run in the capture process. Before the bring-back it made no card for the Personal event (`planning_rows` 0); after it, it made one (`planning_rows` 1).
  Capture 125 is the Personal list after: the card is back as a **new empty card with a new id** (the old id is gone).
* After a Personal bring-back the person must wait for the sync (within the hour). Tell them so; do not navigate to a card page.
* A team's card is made by an owner's or admin's share or auto-shared recording (section 11.3). **Not exercised live for the team case**: covered by
  `tests/test_series_bring_back.py` (`link_from_calendar` refuses while the mark stands and makes the card after, and the team's meetings of the same event join it).
* Bring back writes nothing else: the table counts of meetings, tasks, questions, rows and agenda lines are equal before and after (`tests/test_series_bring_back.py`; **not exercised live**).

### 12.5 The hint on a Personal card: `team_card` on `GET /api/series/{id}`

A **new top-level field on the page**, `team_card`, **always present** (`null` or an object). It is non-null only on a **Personal** card from the calendar, only while `scoped` is true,
and only when an ordinary team **of the caller's** has a card of the **same recurring meeting** that the caller may see. Why the hint exists: a meeting of that event that goes to the team's
card is grouped there, so the lines typed into the agenda of the Personal card are not used by it (section 11.7). The Personal card keeps working on its own for meetings that stay private.

A Personal page with no team card of the same event (capture 108, trimmed to the field's neighbours; the rest of the page is as in section 5):

```text
GET /api/series/16  ->  200      (docs/fe-captures/series-follow/108-bring-back-personal-page-no-team-card.json)
```

```json
{
  "team": {"id": 1, "name": "My team", "is_personal": true},
  "team_card": null,
  "scoped": true
}
```

The same card once X has one (capture 127):

```json
{
  "team": {"id": 1, "name": "My team", "is_personal": true},
  "team_card": {
    "id": 26,
    "team": {
      "id": 4,
      "name": "Platform team",
      "is_personal": false
    }
  },
  "scoped": true
}
```

And the team's own page (capture 128): `"team_card": null`.

* **Nesting trap:** `team_card.team` is the **team card's** team; the page's own `team` is the Personal space. `team_card.id` is a **series id** (link to `/series/{id}`), unlike `id` in the removed list.
* The hint is **not** drawn for a hand-made Personal card (it has no recurring event), for a team's page, with the flag off, or when the team's card is one the caller may not see (all
  **not exercised live**; `tests/test_series_bring_back.py`).
* The wording the frontend draws, in the Agenda column under "Other agenda items", as an info message: "This recurring meeting also has a card in {team}. Meetings you share with {team} go to that card, so items you add here are not used for them. Add them to {team}'s card instead." (the last words are a link to `/series/{team_card.id}`).

### 12.6 UX consequences the frontend must surface

* **Do not promise the old card back.** Say what comes back: an empty card, later. The lines, topics and project link are gone for good, and past meetings stay ungrouped.
* **The delay is real.** Personal: up to an hour. Team: not until an owner or admin shares a meeting of the event (or their recording is shared automatically).
* **The removal dialog now ends "until you bring it back in Settings"** on its calendar line. A hand-made recurring meeting has no such line to follow: for it the removal is final.
* **Do not draw the list for a plain member** (the call is 403). Draw nothing, not an error, when the call fails.
* **A row's `title` can be generic.** Do not treat `Recurring meeting` as a bug.

### 12.7 Where it is in the frontend

`web/components/app/settings-removed-recurring.tsx` (mounted by `web/app/settings/page.tsx` when `team.can_manage`), `web/components/app/series-view.tsx` (the hint),
`web/components/app/series-remove.tsx` (the calendar line), `web/lib/api/client.ts` (`api.series.removed`, `api.series.bringBack`; `/api/series/removed` is team-scoped so the header goes with it),
`web/tests/series-bring-back.test.tsx` (against captures 108, 112, 113, 114, 120, 122, 127, 128).
