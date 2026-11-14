# Frontend integration guide: teams (multi-team, stages 2 and 3: the meeting and its team, the teams list, an explicit team per request, the switch the screens read)

**Status: dark.** Everything here works only when the server runs with `MULTI_TEAM_ENABLED=true`
(off by default, and it stays off until the frontend of stage 3 is live). With the switch off nothing
below changes for anyone except two things: `POST /api/meetings/{id}/move` and `POST /api/teams`
answer **409 `multi_team_disabled`**, and `can_move` is always `false`. Sections 1 to 8 are stage 2a
(the meeting and its team), sections 9 to 13 are stage 2b (the teams list, creating a team, an
explicit team per request, series, the auto-share team). Sections 14 to 16 are stage 3 (the frontend): the switch the screens read, integrations and the personal space, and what the screens built on all of this do.

**Every payload, status and error string below was copied from a real response captured against the
running app** (`scripts/capture_teams_api.py`, a scratch database with the real migrations, uvicorn,
httpx; captures in `docs/fe-captures/teams/`). Placeholders: ids are the scratch database's, names
and emails are fake (`Ada Example`, `owner@example.test`). Where something could not be exercised
live, the section says so and where the shape came from. The last section lists what was proved how.

## 1. What this is, in the screen's terms

A person now belongs to a **personal space** (always, it is never listed as a place to share to) and
to **any number of teams**. A meeting is in one of three states, and only one team is ever involved:

| State | What the person sees | `team` | `owned_by_team` |
|---|---|---|---|
| **Private** | only the recorder reads it | `null` | `false` |
| **Shared** with a team | every member of that team reads it; the recorder still owns it and may unshare | `{id, name}` | `false` |
| **Moved** to a team | the team owns it: every member reads it; it stays with the team if the recorder leaves it or deletes their account | `{id, name}` | `true` |

**Who may change what** (rename, rewrite, translate, delete, cancel, retry: the `can_*` flags say):
the recorder of a private or shared meeting; for a **moved** meeting, the recorder *while still a
member* **and** the team's owner and admins. A plain member who is not the recorder reads it and
changes nothing. A recorder who left the team can neither read nor change their own moved meeting.

## 2. Conventions

Unchanged: session cookie, `X-CSRF-Token` on every non-GET, errors `{"error": {"code", "message"}}`
(see `docs/fe-integration-guide-team.md`). A meeting the caller may not read is **404 `not_found`**
(message `No such meeting`), never 403, exactly as before.

## 3. New fields on `MeetingDetail` (`GET /api/meetings/{id}` and every route that returns it)

| Field | Type | Meaning |
|---|---|---|
| `team` | `{id, name}` or `null` | The team the meeting is shared with, or, when `owned_by_team`, owns it. |
| `owned_by_team` | bool | `true` for a moved meeting. |
| `can_move` | bool | Show the Move action. `true` only for the recorder of a **finished** (`ready`), **not yet moved** meeting, and only while the switch is on. |
| `can_move_back` | bool | Show "Move back to {recorder}". `true` for the owner and admins of the team that owns the meeting (whether or not it has a recorder: see 6). |

**Nesting traps (all seen in the captures):**
- `shared_with_team` is **`true` for a moved meeting too** (it means "out of the recorder's private
  space"). Use `owned_by_team` to tell shared from moved. Do not branch on `shared_with_team` alone.
- `can_share` is **`false` for a moved meeting** (the share switch goes away).
- `shared_by` is `null` for the recorder's own view and the recorder's name for everyone else,
  **including a team admin who may change the meeting**: `can_delete: true` with `shared_by: "Ada
  Example"` is normal. When the recorder deleted their account it is the string **`"Former member"`**.
- `team` is read **through** the meeting. It is `null` for a private meeting even though the person
  has teams. There is no team id on the task payloads (see 7).

A private meeting, as its recorder sees it:

`GET /api/meetings/1` as **Ada** → **200**  
*A private meeting: `team` is null, `owned_by_team` false, `can_move` true (the switch is on for this run).* (capture `08-detail-private.json`)
*Trimmed to the fields that matter here; the full `MeetingDetail` is in the capture.*
```json
{
  "id": 1,
  "status": "ready",
  "can_regenerate": true,
  "can_delete": true,
  "shared_with_team": false,
  "can_share": true,
  "shared_by": null,
  "team": null,
  "owned_by_team": false,
  "can_move": true,
  "can_move_back": false
}
```

## 4. Share (`POST /api/meetings/{id}/share`, `DELETE` to unshare)

The share route now takes the team **in the request**. The body is optional so today's frontend keeps
working:

- **No body** (today's frontend): the person's default team, which is their only team other than the
  personal one. If the person is on **two or more** such teams it is **422 `team_required`**: the old
  behaviour (silently the personal team) is gone on purpose.
- **`{"team_id": N}`**: the person must belong to team N, and N must not be their personal space.

`POST /api/meetings/1/share` as **Ada** → **200**  
*No body: what today's frontend sends. Ada has exactly one ordinary team (X), so it is the default.* (capture `11-share-old-client.json`)
*Trimmed to the fields that matter here; the full `MeetingDetail` is in the capture.*
```json
{
  "id": 1,
  "status": "ready",
  "can_regenerate": true,
  "can_delete": true,
  "shared_with_team": true,
  "can_share": true,
  "shared_by": null,
  "team": {
    "id": 4,
    "name": "Platform team"
  },
  "owned_by_team": false,
  "can_move": true,
  "can_move_back": false
}
```

`POST /api/meetings/1/share` as **Ada** with body `{"team_id": 4}` → **200**  
*The team named in the request.* (capture `13-share-named.json`)
*Trimmed to the fields that matter here; the full `MeetingDetail` is in the capture.*
```json
{
  "id": 1,
  "status": "ready",
  "can_regenerate": true,
  "can_delete": true,
  "shared_with_team": true,
  "can_share": true,
  "shared_by": null,
  "team": {
    "id": 4,
    "name": "Platform team"
  },
  "owned_by_team": false,
  "can_move": true,
  "can_move_back": false
}
```

Errors the share switch must handle:

`POST /api/meetings/1/share` as **Ada** with body `{"team_id": 5}` → **404**  
*Ada is not in Y: 404, the same answer as for a team that does not exist.* (capture `15-share-not-my-team.json`)
```json
{
  "error": {
    "code": "not_found",
    "message": "No such team."
  }
}
```

`POST /api/meetings/1/share` as **Ada** with body `{"team_id": 1}` → **409**  
*Her personal team is not a place to share to.* (capture `16-share-personal-team.json`)
```json
{
  "error": {
    "code": "personal_team",
    "message": "Choose a team. Your personal space is only yours; nobody else would see the meeting."
  }
}
```

`POST /api/meetings/2/share` as **Pat** → **422**  
*Pat is on two ordinary teams and sent no team: 422, never a guess. (Pat's own meeting.)* (capture `17-share-ambiguous.json`)
```json
{
  "error": {
    "code": "team_required",
    "message": "You are on more than one team. Choose which team to share this meeting with."
  }
}
```

`POST /api/meetings/1/share` as **Ada** with body `{"team_id": 4}` → **409**  
*A moved meeting is not shared or unshared by hand.* (capture `28-share-moved.json`)
```json
{
  "error": {
    "code": "meeting_team_owned",
    "message": "This meeting belongs to a team. An owner or admin of the team can move it back first."
  }
}
```

(`DELETE /api/meetings/{id}/share` on a moved meeting gives the same `meeting_team_owned`, capture
`29-unshare-moved.json`.) Sharing again to another team **moves the share** (one team per meeting); it is not
an error. *Not captured live*: sharing a meeting that was shared to team A to team B (covered by
`tests/test_multi_team_moved.py::test_share_takes_the_team_in_the_request`).

## 5. Move (`POST /api/meetings/{id}/move`, body `{"team_id": N}`)

Only the recorder, only a finished meeting, only into a team they belong to. **The confirmation the
page must show first** (his decision, spec 9.1): *"This meeting will belong to the team. It stays with
the team even if you leave or delete your account."* After the move the meeting is the team's: its
tasks move to the team, any earlier share is replaced.

`POST /api/meetings/1/move` as **Ada** with body `{"team_id": 4}` → **200**  
*Moved: the team owns it. `owned_by_team` true, `can_move` false, `can_move_back` true because Ada also owns X.* (capture `25-move.json`)
*Trimmed to the fields that matter here; the full `MeetingDetail` is in the capture.*
```json
{
  "id": 1,
  "status": "ready",
  "can_regenerate": true,
  "can_delete": true,
  "shared_with_team": true,
  "can_share": false,
  "shared_by": null,
  "team": {
    "id": 4,
    "name": "Platform team"
  },
  "owned_by_team": true,
  "can_move": false,
  "can_move_back": true
}
```

Doing it again for the same team is fine (200, capture `26-move-again-same-team.json`). Errors:

`POST /api/meetings/3/move` as **Ada** with body `{"team_id": 4}` → **409**  
*Only a finished meeting can be moved.* (capture `22-move-not-ready.json`)
```json
{
  "error": {
    "code": "meeting_not_ready",
    "message": "Wait until the notes are ready, then move the meeting."
  }
}
```

`POST /api/meetings/1/move` as **Ada** with body `{"team_id": 5}` → **404**  
*Not a member of Y: 404.* (capture `23-move-not-my-team.json`)
```json
{
  "error": {
    "code": "not_found",
    "message": "No such team."
  }
}
```

`POST /api/meetings/1/move` as **Ada** with body `{"team_id": 1}` → **409**  
(capture `24-move-personal.json`)
```json
{
  "error": {
    "code": "personal_team",
    "message": "Choose a team. Your personal space is only yours; nobody else would see the meeting."
  }
}
```

`POST /api/meetings/1/move` as **Ada** with body `{"team_id": 6}` → **409**  
*Already the team's: another team is refused until it is moved back.* (capture `27-move-already-moved.json`)
```json
{
  "error": {
    "code": "already_moved",
    "message": "This meeting already belongs to another team. An owner or admin of that team can move it back first."
  }
}
```

For a person who is not the recorder, or cannot read the meeting, the answer is the usual **404
`not_found`** (`No such meeting`), the same as every other change route (capture `32-moved-delete-by-member.json` shows
it for delete). **409 `multi_team_disabled`**, message `Moving a meeting to a team is not available
yet.`, is what the route answers while the switch is off. *Not captured live*: the server for the
capture runs with the switch on; the string is from `app/meeting_teams.py` and is tested in
`tests/test_multi_team_moved.py::test_moving_is_refused_while_the_switch_is_off`.

## 6. Move back (`POST /api/meetings/{id}/move-back`, no body)

Gives the meeting back to its recorder (private to them again). **Only an owner or admin of the team
that owns it.** The recorder cannot pull it back alone, and a plain member cannot: the team keeps its
history.

`POST /api/meetings/1/move-back` as **Ada** → **200**  
*The owner of X gives it back: private to Ada again, tasks go to her personal team.* (capture `37-move-back.json`)
*Trimmed to the fields that matter here; the full `MeetingDetail` is in the capture.*
```json
{
  "id": 1,
  "status": "ready",
  "can_regenerate": true,
  "can_delete": true,
  "shared_with_team": false,
  "can_share": true,
  "shared_by": null,
  "team": null,
  "owned_by_team": false,
  "can_move": true,
  "can_move_back": false
}
```

Errors:

`POST /api/meetings/1/move-back` as **Pat** → **403**  
*A reader who is not an owner or admin: 403.* (capture `33-move-back-by-member.json`)
```json
{
  "error": {
    "code": "team_forbidden",
    "message": "Only an owner or admin of the team can move it back."
  }
}
```

`POST /api/meetings/1/move-back` as **Quinn** → **404**  
(capture `35-move-back-by-outsider.json`)
```json
{
  "error": {
    "code": "not_found",
    "message": "No such meeting"
  }
}
```

`POST /api/meetings/1/move-back` as **Ada** → **409**  
*Not moved now: 409.* (capture `38-move-back-never-moved.json`)
```json
{
  "error": {
    "code": "not_moved",
    "message": "This meeting does not belong to a team."
  }
}
```

`POST /api/meetings/1/move-back` as **Pat** → **409**  
*Nobody to give it back to: 409.* (capture `45-recorder-deleted-move-back.json`)
```json
{
  "error": {
    "code": "recorder_gone",
    "message": "The person who recorded this meeting has left, so it cannot be given back."
  }
}
```

(A person who cannot read the meeting gets 404, capture `35-move-back-by-outsider.json` above; a reader without the
role gets **403 `team_forbidden`**: the only 403 in these routes.)

## 7. What follows the meeting: tasks

The tasks the AI took from a meeting move with it (share, unshare, move, move back); tasks typed in
by hand stay where they were made. The task payloads carry **no team id**: they are read through the
meeting, so a person sees a task exactly when they may read its meeting. What Pat sees on the tasks
board (`GET /api/tasks`, trimmed):

Before the share (private meeting): `GET /api/tasks` as **Pat** → **200**  
*Pat sees none of them: the meeting is private.* (capture `10-tasks-pat-before.json`)
*Trimmed to id, title and assignee; each task has the usual fields (see the tasks guide).*
```json
{
  "tasks": []
}
```

After sharing to team X:

`GET /api/tasks` as **Pat** → **200**  
*After the share the tasks follow the meeting into X: Pat (a member of X) now sees both.* (capture `14-tasks-pat-shared.json`)
*Trimmed to id, title and assignee; each task has the usual fields (see the tasks guide).*
```json
{
  "tasks": [
    {
      "id": 2,
      "title": "Book the room",
      "assignee": {
        "user_id": 1,
        "name": "Ada Example",
        "is_you": false
      }
    },
    {
      "id": 1,
      "title": "Send the deck",
      "assignee": null
    }
  ]
}
```

After moving it to X:

`GET /api/tasks` as **Pat** → **200**  
*Its tasks are the team's.* (capture `31-tasks-pat-moved.json`)
*Trimmed to id, title and assignee; each task has the usual fields (see the tasks guide).*
```json
{
  "tasks": [
    {
      "id": 2,
      "title": "Book the room",
      "assignee": {
        "user_id": 1,
        "name": "Ada Example",
        "is_you": false
      }
    },
    {
      "id": 1,
      "title": "Send the deck",
      "assignee": null
    }
  ]
}
```

And after moving back (nothing of it is exposed):

`GET /api/tasks` as **Pat** → **200**  
*Pat's tasks from this meeting are gone from X; nothing of it is exposed.* (capture `39-tasks-pat-after-move-back.json`)
*Trimmed to id, title and assignee; each task has the usual fields (see the tasks guide).*
```json
{
  "tasks": []
}
```

**Surface this:** when a meeting is shared, unshared, moved or moved back, its tasks go to the
destination team's board, and **an assignee who is not a member of the destination team is cleared**.
In the capture of Ada's own board (`09-tasks-before.json`) "Send the deck" is assigned to Pat. The script then
shared the meeting, unshared it and shared it again (captures `11-share-old-client.json`, `12-unshare.json`,
`13-share-named.json`): the unshare sent the tasks to Ada's personal space, where Pat is not a member, so
Pat's assignment was cleared, and on Pat's board after the second share (above) the task has
`"assignee": null`. Say so in the unshare and move-back confirmations ("Tasks from this meeting go back
to you; teammates' assignments are removed"). The project, milestone and sprint of those tasks are
cleared too (tested; not in these captures).

## 8. When the recorder leaves, or deletes their account

The meeting stays with the team. What each person sees (captures, in order): Ada, the recorder, **left**
the team:

`GET /api/meetings/1` as **Ada** → **404**  
*Ada left X: she can no longer read the meeting she recorded.* (capture `41-left-team-detail.json`)
```json
{
  "error": {
    "code": "not_found",
    "message": "No such meeting"
  }
}
```

...and cannot delete it either:

`DELETE /api/meetings/1` as **Ada** → **404**  
*...nor delete it.* (capture `42-left-team-delete.json`)
```json
{
  "error": {
    "code": "not_found",
    "message": "No such meeting"
  }
}
```

Pat (now the team's owner) still reads it, with Ada's name:

`GET /api/meetings/1` as **Pat** → **200**  
*Pat, now the owner of X, still has it; Ada's name is still shown (her account exists).* (capture `43-left-team-still-the-teams.json`)
*Trimmed to the fields that matter here; the full `MeetingDetail` is in the capture.*
```json
{
  "id": 1,
  "status": "ready",
  "can_regenerate": true,
  "can_delete": true,
  "shared_with_team": true,
  "can_share": false,
  "shared_by": "Ada Example",
  "team": {
    "id": 4,
    "name": "Platform team"
  },
  "owned_by_team": true,
  "can_move": false,
  "can_move_back": true
}
```

Then Ada **deleted her account**: the meeting stays, her name and email are gone:

`GET /api/meetings/1` as **Pat** → **200**  
*Ada deleted her account: the team keeps the meeting, `shared_by` is "Former member", and no name or email of hers appears.* (capture `44-recorder-deleted-account.json`)
*Trimmed to the fields that matter here; the full `MeetingDetail` is in the capture.*
```json
{
  "id": 1,
  "status": "ready",
  "can_regenerate": true,
  "can_delete": true,
  "shared_with_team": true,
  "can_share": false,
  "shared_by": "Former member",
  "team": {
    "id": 4,
    "name": "Platform team"
  },
  "owned_by_team": true,
  "can_move": false,
  "can_move_back": true
}
```

Nothing of the person remains in the payload: `shared_by` is the string `"Former member"`. The
meeting can no longer be moved back (409 `recorder_gone`, above): there is nobody to give it to. The
account is purged by the worker, not by a route, so the capture script calls the purge directly
(`deletion.purge_user`); the HTTP reads before and after are real.

What happens to **moved meetings when a team is deleted** (no route for deleting a team exists yet, so
this is documented from the rules in `app/deletion.py`, not captured): the meetings moved to a team that
is deleted go back to their recorder as private meetings if that account still exists, otherwise they
are deleted with the team. A team is deleted today only by the last member's account deletion.

## 9. The teams list and creating a team (stage 2b)

`GET /api/teams` is what the switcher reads. **`is_default`** marks the team a request with **no**
`X-Team-Id` acts on (see section 10): a person with exactly one ordinary team has that one; with two or more
it is their personal space. The personal space is made here if the account has none.

`GET /api/teams` as **Pat** → **200**  
*Pat's teams (X, owned by Pat since Ada handed it over, Y, and the personal space). `is_default` marks the one a request with no X-Team-Id acts on: with two ordinary teams it is the personal space.* (capture `46-teams-list.json`)
```json
{
  "teams": [
    {
      "id": 2,
      "name": "My team",
      "role": "owner",
      "member_count": 1,
      "is_personal": true,
      "is_default": true,
      "can_manage": true
    },
    {
      "id": 4,
      "name": "Platform team",
      "role": "owner",
      "member_count": 1,
      "is_personal": false,
      "is_default": false,
      "can_manage": true
    },
    {
      "id": 5,
      "name": "Client Y",
      "role": "member",
      "member_count": 2,
      "is_personal": false,
      "is_default": false,
      "can_manage": false
    }
  ],
  "can_create_more": 2
}
```

Quinn owns Y and is only in Y, so Y is their default:

`GET /api/teams` as **Quinn** → **200**  
*Quinn owns Y: their default is Y (their only ordinary team).* (capture `47-teams-list-quinn.json`)
```json
{
  "teams": [
    {
      "id": 3,
      "name": "My team",
      "role": "owner",
      "member_count": 1,
      "is_personal": true,
      "is_default": false,
      "can_manage": true
    },
    {
      "id": 5,
      "name": "Client Y",
      "role": "owner",
      "member_count": 2,
      "is_personal": false,
      "is_default": true,
      "can_manage": true
    }
  ],
  "can_create_more": 2
}
```

`POST /api/teams` `{"name": ...}` makes a team the caller owns (201, the same shape as one list row).
While the switch is off `can_create_more` is **0** (nothing can be created). At most **three owned teams** besides the personal space (the personal space and teams the person only
belongs to do not count); `can_create_more` in the list says how many remain.

`POST /api/teams` as **Pat** with body `{"name": "  Side project "}` → **201**  
*The name is trimmed. 201; the caller is the owner.* (capture `48-teams-create.json`)
```json
{
  "id": 7,
  "name": "Side project",
  "role": "owner",
  "member_count": 1,
  "is_personal": false,
  "is_default": false,
  "can_manage": true
}
```

`POST /api/teams` as **Pat** with body `{"name": "   "}` → **422**  
(capture `49-teams-create-empty-name.json`)
```json
{
  "error": {
    "code": "invalid_name",
    "message": "Give the team a name."
  }
}
```

`POST /api/teams` as **Pat** with body `{"name": "One too many"}` → **409**  
*Pat now owns three teams besides the personal one (X, Side project, Book club); Y, which Pat only belongs to, does not count.* (capture `51-teams-create-over-limit.json`)
```json
{
  "error": {
    "code": "team_limit",
    "message": "You can own up to 3 teams."
  }
}
```

After the second team the list says `can_create_more: 0` (capture `52-teams-list-after.json`).
While the switch is off `POST /api/teams` answers **409 `multi_team_disabled`**, message `Creating a
team is not available yet.` *Not captured live* (the capture server runs with the switch on; tested in
`tests/test_multi_team_api.py::test_creating_a_team_is_refused_while_the_switch_is_off`).

## 10. Which team a request is about: the `X-Team-Id` header

The team-implicit routes (`/api/team*`, `/api/tasks` (list and create), `/api/projects*`,
`/api/milestones*`, `/api/sprints*`, `/api/series` (list and create), `/api/integrations/*`) take an
**explicit team per request** in the header **`X-Team-Id: <id>`**. Nothing is remembered on the server
between requests, so two browser tabs can work on two teams. **Send it on every one of those calls
from the team switcher**; without it a route acts on the default team (what today's frontend gets).

No header (Pat's default is the personal space):

`GET /api/team` as **Pat** → **200**  
*No header: the default team, here the personal space (`is_personal` true).* (capture `53-team-no-header.json`)
*Trimmed to the fields that matter here; the full `MeetingDetail` is in the capture.*
```json
{
  "id": 2,
  "name": "My team",
  "is_personal": true,
  "my_role": "owner",
  "member_count": 1,
  "can_manage": true
}
```

With the header, for team X and for team Y:

`GET /api/team` as **Pat** → **200**  
*`X-Team-Id` names the team for this request only.* (capture `54-team-header-x.json`)
*Trimmed to the fields that matter here; the full `MeetingDetail` is in the capture.*
```json
{
  "id": 4,
  "name": "Platform team",
  "is_personal": false,
  "my_role": "owner",
  "member_count": 1,
  "can_manage": true
}
```

`GET /api/team` as **Pat** → **200**  
(capture `55-team-header-y.json`)
*Trimmed to the fields that matter here; the full `MeetingDetail` is in the capture.*
```json
{
  "id": 5,
  "name": "Client Y",
  "is_personal": false,
  "my_role": "member",
  "member_count": 2,
  "can_manage": false
}
```

Errors: a team the caller is not in is **404 `not_found`** (message `No such team.`), the same as a
team that does not exist; a header that is not an id is a **422 validation error**; a team the caller is
in but may not change is the usual **403 `team_forbidden`**.

`GET /api/team` as **Quinn** → **404**  
*A team the caller is not in: 404, the same answer as for a team that does not exist.* (capture `56-team-header-not-my-team.json`)
```json
{
  "error": {
    "code": "not_found",
    "message": "No such team."
  }
}
```

`GET /api/team` as **Pat** → **422**  
*A header that is not a team id: 422 validation error.* (capture `57-team-header-garbage.json`)
```json
{
  "error": {
    "code": "validation_error",
    "message": "Some fields are missing or not valid.",
    "fields": [
      {
        "field": "X-Team-Id",
        "message": "Input should be a valid integer, unable to parse string as an integer"
      }
    ]
  }
}
```

`PATCH /api/team` as **Pat** with body `{"name": "Renamed"}` → **403**  
*Pat is a plain member of Y: 403 `team_forbidden`.* (capture `58-team-patch-as-member.json`)
```json
{
  "error": {
    "code": "team_forbidden",
    "message": "Only an owner or admin of the team can do that."
  }
}
```

**Field-nesting traps:** `GET /api/team` now has `is_personal` (additive); `GET /api/tasks` **with** the
header is one team's board (its `people`, its `attention` counts), **without** it every team's tasks
the person may see, as before (`people` are then the default team's). The header never widens what a
person may read: meetings and tasks are still read through the meeting rule.

Members of Y, as Pat (a plain member) reads them: capture `59-members-y.json`.

A task made by hand belongs to the team named (`POST /api/tasks` with the header), and only that team's
members see it:

`POST /api/tasks` as **Pat** with body `{"title": "Pay the invoice"}` → **201**  
*A task made by hand belongs to the team named.* (capture `61-tasks-create-in-y.json`)
*Trimmed to the fields that matter here; the full `MeetingDetail` is in the capture.*
```json
{
  "id": 3,
  "title": "Pay the invoice",
  "source": "manual",
  "assignee": null
}
```

`GET /api/tasks` as **Quinn** → **200**  
(capture `62-tasks-quinn-sees-it.json`)
*Trimmed to id, title and assignee; each task has the usual fields (see the tasks guide).*
```json
{
  "tasks": [
    {
      "id": 3,
      "title": "Pay the invoice",
      "assignee": null
    }
  ]
}
```

## 11. Series (recurring meetings) belong to one team

A series is made in the recorder's **default team** when the calendar links it, and in the team named by
`X-Team-Id` when made by hand. `GET /api/series` lists **one team's** series (header, else default).
**By id** (`GET /api/series/{id}` and its sub-routes, the series on a meeting's page) a series is read
through **its own team**: a person on two teams opens the series of either with no header, and a series
of a team they are not in is **404 `not_found`**. **A meeting can only join a series of its own team** (`PUT /api/meetings/{id}/series`): the team that owns it, else the team it is shared with, else (a private meeting) the person's default team; any other series is **404 `not_found`** (*not captured live; tested in `tests/test_multi_team_guard.py`*). A series page lists only the occurrences the viewer
may read, so a meeting its recorder shared to a *different* team is not part of this team's series.

`POST /api/series` as **Pat** with body `{"title": "Client sync"}` → **201**  
*A recurring meeting made in team Y.* (capture `63-series-create-in-y.json`)
```json
{
  "id": 1,
  "title": "Client sync",
  "cadence": "Recurring",
  "platform": null,
  "next_starts_at": null,
  "next_text": null,
  "unfinished_tasks": 0,
  "open_questions": 0,
  "project": null
}
```

`GET /api/series` as **Pat** → **200**  
(capture `64-series-list-y.json`)
```json
{
  "timezone": "UTC",
  "series": [
    {
      "id": 1,
      "title": "Client sync",
      "cadence": "Recurring",
      "platform": null,
      "next_starts_at": null,
      "next_text": null,
      "unfinished_tasks": 0,
      "open_questions": 0,
      "project": null
    }
  ]
}
```

`GET /api/series` as **Pat** → **200**  
*X has none.* (capture `65-series-list-x.json`)
```json
{
  "timezone": "UTC",
  "series": []
}
```

## 12. The auto-share team (`/api/settings/auto-share`)

Each person may choose **one** team their new meetings are shared to automatically, once, when the
notes are first written (never when the bot joins). A person who chose none keeps today's rule (the
team switch on `PATCH /api/team`, for the team's owners and admins). The choice reads as `null` after
the person leaves that team. While the switch is off the setting is refused with **409 `multi_team_disabled`** (message `Choosing a team for new meetings is not available yet.`) and reads as `null` (*not captured live; tested*). **Say so on the settings screen:** "New meetings are shared with this
team when their notes are ready."

`GET /api/settings/auto-share` as **Pat** → **200**  
(capture `66-auto-share-read.json`)
```json
{
  "team_id": null
}
```

`PUT /api/settings/auto-share` as **Pat** with body `{"team_id": 5}` → **200**  
*One team at most; it applies when a meeting's notes are first written.* (capture `67-auto-share-set.json`)
```json
{
  "team_id": 5
}
```

`PUT /api/settings/auto-share` as **Pat** with body `{"team_id": 2}` → **409**  
(capture `68-auto-share-personal.json`)
```json
{
  "error": {
    "code": "personal_team",
    "message": "Choose a team. Your personal space is only yours."
  }
}
```

`PUT /api/settings/auto-share` as **Quinn** with body `{"team_id": 4}` → **404**  
*Quinn is not in X.* (capture `69-auto-share-not-my-team.json`)
```json
{
  "error": {
    "code": "not_found",
    "message": "No such team."
  }
}
```

`PUT /api/settings/auto-share` as **Pat** with body `{"team_id": null}` → **200**  
(capture `70-auto-share-clear.json`)
```json
{
  "team_id": null
}
```

## 13. Mail, invitations, integrations (stage 2b)

- **One mail per person per meeting.** The agenda and reminder mails before a recurring meeting are
  sent once per person and occurrence even when two teams each have a series for the same recurring
  event. The share mail's "Open notes" button shows only to a person who may read the meeting. Reminder
  settings (`reminders_on`, `reminder_timing`) are per team (`PATCH /api/team` with the header).
- **Invitation caps.** Besides the team's own caps there is now a cap **per person across all their
  teams**: 20 invitations in an hour and 20 in a day, revoked ones included. Over it the answer is the
  existing **409 `invite_limit`** (`You have reached the limit for invitations. Revoke one, or try
  again tomorrow.`). *Not captured live* (it needs 20 sends); tested.
- **Integrations (Slack, Linear)** belong to a team: `GET/PUT/DELETE /api/integrations/*` take the
  header. A meeting's Slack post goes to the integration of the team the meeting is shared or moved to,
  and a private meeting's tasks are never posted. Posts already made are **not recalled** when a
  meeting is unshared, moved or re-homed (a Slack post cannot be unsent): tell the person so in the
  unshare and move confirmations. *Not captured live* (no Slack in the capture).

## 14. How the frontend learns the switch is on (stage 3)

`GET /api/session` (and the login and logout answers, which are the same shape) carries one read-only
boolean, **`multi_team`**: whether this server lets a person belong to several teams. It is a server
setting, not the person's: the same answer logged in or out. The frontend draws the team switcher, the Teams
screen and the Move action **only when it is `true`**, and with it `false` the app is exactly what it was
before multi-team. It is a required field (never absent).

`GET /api/session` with the switch on, before login (capture `02-session-ada.json`; the visitor has a cookie but is not signed in):

```json
{
  "authenticated": false,
  "csrf_token": "<csrf-A>",
  "user": null,
  "usage": null,
  "unread_notifications": null,
  "badges": null,
  "multi_team": true
}
```

`POST /api/session` as **Ada** with the switch on → **200** (capture `03-login-ada.json`):

```json
{
  "authenticated": true,
  "csrf_token": "<csrf-B>",
  "user": {
    "email": "owner@example.test",
    "name": "Ada Example",
    "is_admin": true,
    "email_verified": true,
    "time_zone": null,
    "recording_acknowledged": false
  },
  "usage": {
    "used_seconds": 0,
    "cap_seconds": null
  },
  "unread_notifications": 0,
  "badges": {
    "review_open": 0,
    "tasks_new": 2
  },
  "multi_team": true
}
```

`GET /api/session` with the switch **off** (capture `71-session-anonymous-switch-off.json`; the capture server runs in one process, so the switch is turned off for this single read):

```json
{
  "authenticated": false,
  "csrf_token": "<csrf-A>",
  "user": null,
  "usage": null,
  "unread_notifications": null,
  "badges": null,
  "multi_team": false
}
```

Other ways to guess the switch are worse: `can_create_more` is also 0 when a person owns three teams, `can_move` is also false for an unfinished meeting, and `POST /api/teams` answering `multi_team_disabled` is a write.
(`can_create_more` is 0 while the switch is off, section 9, but that is not a way to *read* the switch.)

## 15. Slack and Linear are not for the personal space (stage 3)

Spec 6.9: the personal space has no integrations. While the switch is on, `PUT /api/integrations/slack`,
`POST /api/integrations/linear/teams` and `PUT /api/integrations/linear` on a person's **personal** team
(`X-Team-Id: <personal id>`, or no header when the personal space is their default team) are refused
**409 `personal_team`**, before anything is checked, posted or stored. Reading the state
(`GET`) and disconnecting (`DELETE`, safe to repeat) still work on any team. With the switch **off** the
team of one is the only team there is, so connecting stays allowed (tested, not captured: the capture server
has the switch on). The Settings screen draws no Connect for a personal space; an integration already connected there is still shown, with Disconnect and a line saying it cannot be connected again.

`PUT /api/integrations/slack` as **Pat** with `X-Team-Id` = Pat's personal team, body `{"webhook_url": "https://example.test/not-a-slack-webhook"}` → **409**  
*Slack is a team's, not the personal space's: 409 while the switch is on. Nothing is posted or stored.* (capture `72-integrations-slack-personal-team.json`)
```json
{
  "error": {
    "code": "personal_team",
    "message": "Slack and Linear belong to a team. Your personal space has none; choose a team first."
  }
}
```

`POST /api/integrations/linear/teams` as **Pat**, personal team → **409** (capture `73-integrations-linear-teams-personal-team.json`): the same body.

`GET /api/integrations/slack` as **Pat**, personal team → **200**  
*Reading the state of a personal team still works (the screen hides the section instead).* (capture `74-integrations-slack-read-personal-team.json`)
```json
{
  "connected": false,
  "status": null,
  "last_error": null,
  "last_posted_at": null,
  "can_manage": true
}
```

`PUT /api/integrations/slack` as **Pat**, team X (which Pat owns), the same invalid address → **400**  
*A team Pat owns gets past the personal-team rule: this fake address ends in 400, as before.* (capture `75-integrations-slack-ordinary-team.json`)
```json
{
  "error": {
    "code": "slack_webhook_invalid",
    "message": "That is not a Slack webhook address. It starts with https://hooks.slack.com/services/."
  }
}
```

## 16. What the stage 3 screens do with all this (the contract the frontend keeps)

For the next person to touch the screens. Every sentence is a rule the code and its tests keep.

- **Which calls carry `X-Team-Id`.** Exactly the routes that read it (section 10): `/api/team` and its
  `members` and `invites` routes (not `invites/preview` or `invites/accept`: the token names the team),
  `GET` and `POST /api/tasks` (not `/api/tasks/{id}`), `/api/projects*`, `/api/milestones*`, `/api/sprints*`,
  `GET` and `POST /api/series` (not `/api/series/{id}*`), `/api/integrations/*`, and
  `/api/meetings/{id}/milestones`. One interceptor in `web/lib/api/client.ts` (`isTeamScoped`), used by the
  browser client and by the server render. **Never** on `/api/teams`, `/api/session`, the meeting routes
  (the share and move routes take the team in the **body**), `/api/settings/*` or any other route.
- **Per browser tab, never on the server.** On the Team, Tasks, Projects and Settings pages the team is in the
  URL, `?team=<id>`, because the server render can only read the URL. This tab remembers the choice in
  `sessionStorage` (one tab's), for pages without the parameter (the meeting page), stored with a hash of the
  signed-in address so another person signing in in the same tab never inherits it (it is also cleared on login
  and logout through the API client). The default team is "no choice": no parameter, no header. A second tab never
  sees the first tab's choice. An id above 2147483647 (the server's limit, else 422) is read as no team.
- **The meeting page asks about the MEETING's team**, not the tab's: a meeting shared to team X lists X's
  series and X's milestones, and its task drawer reads X's people, projects, milestones and sprints (header
  `X-Team-Id: <meeting.team.id>`); a private one uses the default team. Share and Move name their team in the body.
  The Decisions and Series pages open the same drawer, but a decision or a series carries no team id, so they use the
  tab's team (a known limit, see the SOP).
- **A team that is not the person's (404 `not_found`, message `No such team.`).** Server render: `?team=` that
  is not in `GET /api/teams` (they left, it was deleted, a hand-typed id) redirects to the same page without
  it and `?notice=team-gone`. Browser call: a team-scoped call that carried the TAB's team (not one a call named for itself, such as a
  meeting's) and got this 404 sends the tab to the same page on the default team with the same notice, never twice
  within 15 seconds. The notice reads: "That team is no longer
  available to you, so you are back on your default team." The 404 shares its `code` with every "no such
  thing" 404, so the frontend tells this one by the exact message `No such team.` and by having sent the header.
- **Words shown as written.** The Move confirmation: "This meeting will belong to the team. It stays with the
  team even if you leave or delete your account." The refusals of share, move and move back
  (`team_required`, `personal_team`, `meeting_team_owned`, `meeting_not_ready`, `already_moved`,
  `team_forbidden`, `not_moved`, `recorder_gone`, `multi_team_disabled`) show the server's own `message`
  (sections 4 to 6, 9); `team_limit` and `invalid_name` on creating a team likewise. A 404 `No such team.` on
  share or move reads "You are no longer in that team. Choose another." and the switcher's list is fetched again.
- **A moved meeting's recorder** is shown as the string the API sends: `Former member` when the account is
  gone (section 8); the Details row reads "Recorded by" for a moved meeting and "Shared by" otherwise.
- **UX consequences surfaced (section 7 and 13):** the stop-sharing and move-back texts say that tasks go back
  to the recorder and teammates' assignments are removed; the Move and move-back texts say that Slack posts
  already made are not recalled.
- **Lead-designed, not in the designer's file** (marked in the code, the SOP and the parity report so the
  designer can review): the team switcher, the Teams screen, the meeting page's team card and its two
  confirmation dialogs, the personal-space notice on the Team screen.

## 17. Verification table

| Behaviour | Verified |
|---|---|
| Detail shape and new fields for private, shared, moved (recorder, member, admin) | **verified-live** (captures 08, 11, 13, 25, 30, 36, 37, 43, 44) |
| Share with no body, with a team, to a team they are not in (404), to a personal team (409), ambiguous (422), on a moved meeting (409) | **verified-live** (11, 13, 15 to 17, 28, 29) |
| Move: not ready (409), not a member (404), personal (409), twice (200), already moved (409) | **verified-live** (22 to 27) |
| Move back: member (403), outsider (404), never moved (409), recorder deleted (409) | **verified-live** (33, 35, 38, 45) |
| Readers other than owner/admin cannot change a moved meeting (404) | **verified-live** (32) |
| Outsider reads nothing of a moved or shared-to-X meeting (404) | **verified-live** (20, 34) |
| Tasks follow share, unshare, move, move back | **verified-live** (10, 14, 31, 39) |
| Recorder leaves: reads and deletes nothing (404), team keeps it | **verified-live** (41 to 43) |
| Recorder deletes account: `"Former member"`, no name or email | **verified-live** for the read (44); the purge itself is called by the script, not over HTTP |
| `multi_team_disabled` while the switch is off | **not live** (tested only) |
| Moved meetings when a team is deleted | **not live** (no route; tested in `tests/test_multi_team_moved.py`) |
| Share moving from team A to team B | **not live** (tested only) |
| Teams list, create (201, 422, 409 limit), default marking | **verified-live** (46 to 52) |
| `X-Team-Id`: named team, no header, not my team (404), bad header (422), member forbidden (403) | **verified-live** (53 to 58) |
| Members list, tasks board and manual task per team | **verified-live** (59 to 62); the board filter is also tested |
| Series created and listed per team, read by its own team | **verified-live** for create and list (63 to 65); the by-id and not-shared-to-this-team rules are **tested only** |
| Auto-share team: read, set, personal (409), not mine (404), clear | **verified-live** (66 to 70); the effect on a new meeting's notes is **tested only** |
| `multi_team_disabled` on `POST /api/teams`; the per-person invitation caps; one reminder per person per occurrence; Slack and Linear per team | **not live** (tested only; no mail or Slack in the capture) |
| Projects, milestones, sprints and integrations with the header | **not live** (same pattern as the team route; tested in `tests/test_multi_team_api.py`) |
| `multi_team` in the session: switch on (logged out, logged in) and off | **verified-live** (02, 03, 71); the switch-off read is made in the capture process with the switch turned off for that one call |
| Slack and Linear refused on a personal team (409 `personal_team`), read still 200, an ordinary team gets past it | **verified-live** (72 to 75); the switch-off case (team of one allowed) is **tested only** (`tests/test_multi_team_stage3.py`) |
| The header on exactly the team-scoped routes, per tab, the `team-gone` notice, the meeting page's own team | **tested against the captured shapes** in `web/tests/` (the screens were also looked at in a browser against the mock, not against a real FastAPI) |
