# FE integration guide: recurring meetings follow the meetings (team scope)

Everything in this file is a body, status or message **copied from a real response**: the capture script
`scripts/capture_series_follow.py` runs the real app (real migrations, auth, CSRF, team roles, visibility,
the backfill script) on a scratch Postgres and writes every exchange to `docs/fe-captures/series-follow/NN-name.json`. Run it again
to re-derive any claim. What was **not** exercised live is marked inline and listed in section 9.
Names, emails and ids are placeholders ("Ada Example", `owner@example.test`, ids from a scratch database:
yours will differ).

Related: `docs/fe-integration-guide-series.md` (the Series API this builds on),
`docs/fe-integration-guide-teams.md` (the team switcher and `X-Team-Id`),
`docs/superpowers/specs/2026-11-14-series-follow-meetings.md` (the design),
`docs/sop/2026-11-14-series-follow-switch-on.md` (the server steps).

## 1. What this is, in the person's terms

A **recurring meeting is no longer one thing per person**. Monday's Standup shared to the team "Tech" and
Tuesday's Standup kept private are two rows: the team's recurring list shows Standup with Monday only; the
person's **Personal** space shows Standup with Tuesday only. Adebayo's rule (2026-11-14): *a team's page lists
a series if at least one of its meetings is in that team, and shows only those meetings; the personal
space shows the series' private meetings.*

It switches on when the server flag `SERIES_FOLLOW_MEETINGS` is turned on (after a one-off backfill),
**and only while `MULTI_TEAM_ENABLED` is on** (it is). The frontend has no switch to read: with the flag off
every shape below is the same except that `team` is already sent (section 3). The frontend change is the
same either way.

| What the person sees | Before | After the flag is on |
|---|---|---|
| A one-team person's **private** recurring meeting | on the team's Recurring list, with the team's meetings | on their **Personal** space only; the team's list no longer shows it |
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
| Personal row | **No "Next meeting" box and no "Send agenda to attendees"** (it has nobody else to send to: 409 `no_attendees`, section 5) | `team.is_personal` |
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

* **`team` on a series is not `team` on a meeting.** The meeting object's `team` is `{id, name}` (no
  `is_personal`) and is **`null` for a private meeting** (captures 37 and 42 below). The series card's and
  page's `team` is never null.
* `next_starts_at`, `next_text` and `sprint_note` are `null` both on a **personal row** and for "no calendar
  event ahead is known". Use `team.is_personal` to tell them apart.
* The **same recurring event has one row per scope**, each with its own `id`. After the backfill Ada's Standup is
  rows 1 (team X) and 9 (her personal space); Pat's Design sync is rows 3 (personal), 10 (X) and 11 (Y)
  (`docs/fe-captures/series-follow/13-rows-after-backfill.json`). Never merge cards by title across teams; key by `id`.
* A **meeting's `series.id` changes** when the meeting is shared, unshared or moved (captures 37 and 42:
  `series.id` 1, then 9). Refetch the meeting after those calls; do not keep the old id.

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
      "id": 10,
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
      "id": 9,
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
(capture 20 is the personal list, row id 3): a frontend that sends no team to a two-team person gets the Personal
list, not a team's. Team Y's list for Pat (capture 18) holds only Design sync's row 11, with the meeting shared to Y.

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
| `17-cards-pat-x` | Pat, X | id 10 Design sync (Platform team, is_personal false); id 1 Standup (Platform team, is_personal false) |
| `18-cards-pat-y` | Pat, Y | id 11 Design sync (Client Y, is_personal false) |
| `19-cards-pat-personal` | Pat, personal | id 3 Design sync (Personal, is_personal true) |
| `22-cards-quinn-y` | Quinn, Y | id 11 Design sync (Client Y, is_personal false) |

## 5. The page, per row

`GET /api/series/{id}` takes **no** `X-Team-Id`: the row's own team decides, so a person on two teams opens either
row through its link. What it lists is only the meetings in that row's scope.

**A team row** (Ada, X's Standup: meeting 1 only; trimmed, the task and the page's other lists as marked):

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
  "cadence": "Weekly on Saturdays, 20:00",
  "platform": "google_meet",
  "timezone": "UTC",
  "next_starts_at": "2026-11-21T20:00:00Z",
  "next_text": "Sat 21 Nov, 20:00",
  "sprint_note": null,
  "can_rename": true,
  "agenda_recipients": 0,
  "unfinished": "<1 task>  (trimmed here, each is the task object of docs/fe-integration-guide-tasks.md)",
  "questions": [
    {
      "id": 1,
      "text": "Who owns A1 shared?",
      "meeting": 1
    }
  ],
  "agenda": [],
  "topics": [],
  "past_meetings": [
    1
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

`GET /api/series/9` as Ada → **200** (`docs/fe-captures/series-follow/25-page-ada-personal.json`)

```json
{
  "id": 9,
  "title": "Standup",
  "team": {
    "id": 1,
    "name": "My team",
    "is_personal": true
  },
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

`GET /api/series/10` as Quinn → **404** (`docs/fe-captures/series-follow/30-page-quinn-x-404.json`)

```json
{
  "error": {
    "code": "not_found",
    "message": "No such recurring meeting"
  }
}
```

`GET /api/series/9` as Pat → **404** (`docs/fe-captures/series-follow/32-page-pat-ada-standup-404.json`)

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

`POST /api/series/9/agenda/send` as Ada → **409** (`docs/fe-captures/series-follow/35-personal-send-agenda.json`)

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
3. `DELETE /api/meetings/2/share` → 200, `series` is row 9 again (capture 42, `team` back to `null`); X's card is back
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
      "id": 9,
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

`GET /api/series/9` as Ada → **200** (`docs/fe-captures/series-follow/41-page-ada-personal-after-share.json`)

```json
{
  "id": 9,
  "title": "Standup",
  "team": {
    "id": 1,
    "name": "My team",
    "is_personal": true
  },
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

| Where | Words (all in `web/lib/series-team.ts` and `series-view.tsx`) |
|---|---|
| Past meetings, personal row | None of your private meetings are in this series right now. |
| Past meetings, team row | No meeting of this series is shared with {team name} right now. |
| Past meetings, switch off | No meetings recorded in this series yet. (as before) |
| Attendees | Nobody yet. |
| Unfinished / Finished / Questions | Everything from last time is done. / Nothing finished yet. Completed tasks will show here. / No open questions. (as before) |
| Recurring cards, no row | Nothing is drawn (as before) |

**Open decision (Adebayo / lead): the ghost empty card.** A one-team person who shares their only private Standup
meeting keeps an empty "Standup" card in their Personal space. By the design note's rule an empty card stays for its creator.
Not changed in this PR; the frontend renders it cleanly.

## 8. The server-side switch, as the frontend feels it

* **The backfill** (real output, run with the flag still off). Dry run first, then `--apply`, then a dry run that must find
  nothing. Counts only; no title, name or address is printed:

`docs/fe-captures/series-follow/10-backfill-1-dry-run.json`

```text
DRY RUN: nothing was changed. Run again with --apply to do it.
Recurring-meeting rows looked at: 2
Rows that held a meeting of another team or of the personal space: 2
Meetings moved to the row of their own team or personal space: 3
New rows made for them: 3
Meetings taken out of their series (the team had no room): 0
Meetings left alone (their recorder's account is gone): 0
Personal spaces made for people who had none: 0
Rows left with no meeting after the split: 0
...of which still hold waiting agenda lines: 0
```

`docs/fe-captures/series-follow/11-backfill-2-apply.json`

```text
APPLIED: the database was changed.
Recurring-meeting rows looked at: 2
Rows that held a meeting of another team or of the personal space: 2
Meetings moved to the row of their own team or personal space: 3
New rows made for them: 3
Meetings taken out of their series (the team had no room): 0
Meetings left alone (their recorder's account is gone): 0
Personal spaces made for people who had none: 0
Rows left with no meeting after the split: 0
...of which still hold waiting agenda lines: 0
```

`docs/fe-captures/series-follow/12-backfill-3-dry-run-again.json`

```text
DRY RUN: nothing was changed. Run again with --apply to do it.
Recurring-meeting rows looked at: 5
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
Recurring-meeting rows looked at: 5
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
Recurring-meeting rows looked at: 5
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
| Personal row: no next meeting, `agenda_recipients` 0, send refused 409 | **verified live** (15, 25, 35) |
| Agenda line stays on its row | **verified live** (34, 36, 41) |
| Share / unshare move the meeting between rows, counts follow | **verified live** (37 to 44) |
| Emptied row is a plain empty page | **verified live** (40, 41) |
| Backfill dry run, apply, repeat; the flag-on straggler round | **verified live** (10 to 12, 45 to 47) |
| Moving a meeting to a team and back, a person leaving a team, a team deleted | **not exercised live**: covered by `tests/test_series_follow.py` (leak cases 10, 11, 13); same hook as share |
| "Keeps coming up" never shown on a personal row, and a team's topics only from its scope | **not exercised live**: the model is not called here, so `topics` is `[]` on every page (vacuous); covered by `tests/test_series_readers.py` |
| Agenda mail and reminder mail (who gets what, one reminder per person, order) | **not exercised live**: no mail is built here; `scripts/capture_series_api.py` shows the mails before this change; the scope and the order are covered by `tests/test_series_readers.py` |
| `next_starts_at` on a team row when the next occurrence is on a teammate's calendar | **verified live** (capture 26, Pat's calendar gives Ada's X list its next date) |
| Real Google or Outlook calendars; the real flag flip in the containers | **not exercised**: calendar rows are made as the sync leaves them; the flag is flipped inside the running process (`get_settings.cache_clear()`) |
| The frontend in a browser against a live server | **not exercised**: the frontend is tested against these captures (`web/tests/series-follow.test.tsx`) |
