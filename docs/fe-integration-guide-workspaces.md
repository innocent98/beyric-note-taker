# FE integration guide: workspaces

**Status.** Backend merged on `feature/workspaces` (PR #270), web in PR B. Every payload below is copied from a real response captured against the running app by `scripts/capture_workspaces.py` (files in `docs/fe-captures/workspaces/`, listed by name next to each one). Placeholders: Pat, Ada, Quinn and the team names are the script's cast. Ids are those of that run: Pat's personal space is **2** ("My team"), Platform team **4**, Client Y **5**.

## 1. What a workspace is, in the FE's terms

A **workspace is a team**; a person's personal space is a workspace of one (`is_personal: true`, named "My team", shown "(personal)"). Everything the screens show (meetings, decisions, review, tasks, Ask answers, the bell) belongs to **one** workspace at a time. The tab names its workspace in the request header **`X-Team-Id`**. There is no "show everything": a request with no header acts on the person's **default workspace** (their only team, else their only ordinary team, else their personal one; the same one `GET /api/teams` marks `is_default`).

## 2. Which calls carry the header (`isTeamScoped`, `web/lib/api/client.ts`)

Every `/api/*` call EXCEPT the person's own: log in and out (`POST` and `DELETE /api/session`; `GET /api/session` is scoped), `/api/auth/*`, `/api/billing/*`, `/api/admin/*`, `/api/pilot`, `/api/waitlist`, `/api/signup*`, `/api/verify-email`, `/api/password/*`, `/api/mail/*`, `GET`/`POST /api/teams`, the invitation `preview` and `accept`, the profile settings (`name`, `email`, `language`, `time-zone`, `recording-ack`) and the calendar providers' `/callback`. Sending it to a route that does not read it is harmless. A team the person is not in is `404 not_found` "No such team." on the routes that read it (the web then returns the tab to its default workspace, `onTeamGone`).

## 3. The session names the workspace and the unread of the others

`GET /api/session  (header `X-Team-Id: 4`)` -> **200** (capture `06-session-x.json`; as Pat)

```json
{
  "authenticated": true,
  "csrf_token": "<csrf-B>",
  "user": {
    "email": "pat@example.test",
    "name": "Pat Example",
    "is_admin": false,
    "email_verified": true,
    "time_zone": null,
    "recording_acknowledged": false
  },
  "usage": {
    "used_seconds": 0,
    "cap_seconds": 18000,
    "state": null,
    "seconds_left": null,
    "warning": null,
    "can_record": null,
    "unlimited": null,
    "period_ends_at": null,
    "trial_ends_at": null
  },
  "unread_notifications": 1,
  "badges": {
    "review_open": 1,
    "tasks_new": 2
  },
  "active_workspace": {
    "id": 4,
    "name": "Platform team",
    "is_personal": false,
    "kind": "team"
  },
  "other_workspaces": [
    {
      "id": 2,
      "name": "My team",
      "is_personal": true,
      "kind": "team",
      "unread_notifications": 2
    },
    {
      "id": 5,
      "name": "Client Y",
      "is_personal": false,
      "kind": "team",
      "unread_notifications": 1
    }
  ],
  "multi_team": true
}
```

- `active_workspace` is the one named by the header (else the default). `badges` and `unread_notifications` count **that workspace only**.
- `other_workspaces` lists every other workspace the person is in with its `unread_notifications`: the switcher shows a dot and "(N unread)" for each one above zero.
- `multi_team` is always `true` now and need not be read.
- **Trap:** `GET /api/session` with a team the caller is not in is NOT an error (every page reads it first): it answers the default workspace. Every other route answers 404 for it.

No header, for someone in two ordinary teams (capture `08`), and a stale team (capture `09`, Quinn, who is not in X):

`GET /api/session  (no `X-Team-Id`)` -> **200** (capture `08-session-no-header.json`; as Pat)
(only the named fields shown; every value is verbatim)

```json
{
  "active_workspace": {
    "id": 2,
    "name": "My team",
    "is_personal": true,
    "kind": "team"
  },
  "unread_notifications": 2,
  "badges": {
    "review_open": 1,
    "tasks_new": 0
  }
}
```
`GET /api/session  (header `X-Team-Id: 4`)` -> **200** (capture `09-session-stale-team.json`; as Quinn)
(only the named fields shown; every value is verbatim)

```json
{
  "active_workspace": {
    "id": 5,
    "name": "Client Y",
    "is_personal": false,
    "kind": "team"
  },
  "other_workspaces": [
    {
      "id": 3,
      "name": "My team",
      "is_personal": true,
      "kind": "team",
      "unread_notifications": 0
    }
  ]
}
```

## 4. Opening a meeting by its id from another workspace

Detail routes authorise by **readability only**: the header never turns a readable meeting into a 404. The answer carries the meeting's workspace so the page can switch.

`GET /api/meetings/2  (header `X-Team-Id: 5`)` -> **200** (capture `27-meeting-from-another-workspace.json`; as Pat)
(only the named fields shown; every value is verbatim)

```json
{
  "id": 2,
  "title": "Pat shared to X",
  "workspace_team_id": 4,
  "workspace": {
    "id": 4,
    "name": "Platform team",
    "is_personal": false,
    "kind": "team"
  },
  "shared_with_team": true,
  "team": {
    "id": 4,
    "name": "Platform team"
  }
}
```

**FE rule (done in `MeetingWorkspaceSync`):** if `workspace` is not null and is not the workspace this tab is on, store it as the tab's workspace (the switcher follows) and show the toast **"Switched to <workspace.name>"**. `workspace` is `null` only when the reader is not a member of that workspace (nothing to switch to). A meeting in the active workspace (capture `28`) names the same one: no toast. A meeting the person cannot read is 404 whatever the header says (capture `29`).

`GET /api/meetings/1  (header `X-Team-Id: 2`)` -> **200** (capture `28-meeting-in-active-workspace.json`; as Pat)
(only the named fields shown; every value is verbatim)

```json
{
  "id": 1,
  "workspace_team_id": 2,
  "workspace": {
    "id": 2,
    "name": "My team",
    "is_personal": true,
    "kind": "team"
  }
}
```
`GET /api/meetings/6  (header `X-Team-Id: 4`)` -> **404** (capture `29-meeting-not-readable.json`; as Pat)

```json
{
  "error": {
    "code": "not_found",
    "message": "No such meeting"
  }
}
```

## 5. Lists follow the header

`GET /api/meetings`, `/api/decisions`, `/api/review`, `/api/tasks`, `/api/notifications`, `/api/ask/scope` and the team routes show only the named workspace. Meetings of Pat in the three workspaces (captures `10`, `11`, `12`; the ids differ per workspace, no meeting appears in two):

- `10-meetings-personal.json`: [(1, 'Pat private')]
- `11-meetings-x.json`: [(3, 'Ada shared to X'), (2, 'Pat shared to X')]
- `12-meetings-y.json`: [(5, 'Quinn shared to Y'), (4, 'Pat shared to Y')]

A team the caller is not in (capture `14`):

`GET /api/meetings  (header `X-Team-Id: 99999`)` -> **404** (capture `14-meetings-team-not-mine.json`; as Pat)

```json
{
  "error": {
    "code": "not_found",
    "message": "No such team."
  }
}
```

**Trap (field nesting):** a meeting's `workspace_team_id` is at the top level of `GET /api/meetings/{id}`; the **list** items (`MeetingSummary`) do not carry it. Do not read it from a list.

## 6. Notifications

The bell lists and counts the **active workspace's** notifications; an hours or account notice (no meeting) is in the personal workspace. Reading all marks only the named workspace's.

`GET /api/notifications  (header `X-Team-Id: 4`)` -> **200** (capture `22-notifications-x.json`; as Pat)

```json
{
  "notifications": [
    {
      "id": 2,
      "kind": "meeting.ready",
      "severity": "info",
      "title": "Notes ready: pat_in_x",
      "body": "",
      "meeting_id": 2,
      "created_at": "2026-11-24T22:37:45.714160Z",
      "read_at": null
    }
  ],
  "has_more": false,
  "next_before_id": null
}
```
`POST /api/notifications/read-all  (header `X-Team-Id: 4`)` -> **200** (capture `37-notifications-read-all-x.json`; as Pat)

```json
{
  "unread_notifications": 0
}
```

After that read the session shows X at 0 and the other workspaces unchanged (capture `38`).

## 7. Ask

`POST /api/ask` and `GET /api/ask/scope` search only the active workspace's readable meetings, decisions and tasks. Counts per workspace (captures `30`, `31`, `32`):

- `30-ask-scope-personal.json`: `{"meetings": 1, "decisions": 1, "tasks": 0}`
- `31-ask-scope-x.json`: `{"meetings": 2, "decisions": 2, "tasks": 0}`
- `32-ask-scope-y.json`: `{"meetings": 2, "decisions": 2, "tasks": 0}`

The tasks path (no model) in X (capture `33`):

`POST /api/ask  (header `X-Team-Id: 4`)` -> **200** (capture `33-ask-tasks-x.json`; as Pat)

```json
{
  "kind": "tasks",
  "lead": "You have 2 open tasks assigned to you.",
  "points": [
    {
      "text": "Order the cake (to do)",
      "cites": []
    },
    {
      "text": "Book the room (to do)",
      "cites": []
    }
  ]
}
```

The model answer shape (captures `35`, `36`) is **real route, fake model**: the sentence is the capture script's, the retrieval and cites are real.

## 8. UX consequences the FE must surface

- Opening a link to a meeting of another workspace switches the tab and **must** say "Switched to <name>" (never switch silently).
- The switcher **must** show that another workspace has unread notifications (dot + count in the option); otherwise a notice in a workspace the person is not looking at is missed.
- Switching a workspace reloads the page's data (the existing full page load).

## 9. Verification table

| Behaviour | Status |
|---|---|
| Session per workspace, `other_workspaces` unread, stale team falls back | verified live (captures 05 to 09, 38) |
| Meeting by id from another workspace answers its own workspace | verified live (27, 28, 29) |
| Lists, decisions, review, tasks, notifications, Ask scope per workspace | verified live (10 to 26, 30 to 34) |
| Read-all notifications per workspace | verified live (37, 38) |
| Ask model answers | route live, **model faked** (35, 36) |
| `POST /api/meetings` born in the active workspace | **not run live** (needs the bot provider); covered by `tests/test_workspace_*` |
| Toast and switcher dots in a browser | looked at in a real browser at 1280 and 390 px against the real backend (not the live site) |
