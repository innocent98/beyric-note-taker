# Frontend integration guide: teams (multi-team, stage 2a: the meeting and its team)

**Status: dark.** Everything here works only when the server runs with `MULTI_TEAM_ENABLED=true`
(off by default, and it stays off until the frontend of stage 3 is live). With the switch off nothing
below changes for anyone except one thing: `POST /api/meetings/{id}/move` answers **409
`multi_team_disabled`** and `can_move` is always `false`. Stage 2b (the teams list, create team, an
explicit team on every team-implicit route) is documented in its own section when it ships.

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

## 9. Verification table

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
| Mail, Slack and Linear for moved meetings; the teams list; an explicit team on other routes | **stage 2b** |
