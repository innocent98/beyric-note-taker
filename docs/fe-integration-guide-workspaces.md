# FE integration guide: workspaces

**Status.** Foundation shipped in PR #270 and #274; sections 10 and 11 are the final integration (W5, the Workspaces release PR). Every payload below is copied from a real response captured against the running app by `scripts/capture_workspaces.py` (files in `docs/fe-captures/workspaces/`, listed by name next to each one). Placeholders: Pat, Ada, Quinn and the team names are the script's cast. Ids are those of that run: Pat's personal space is **2** ("My team"), Platform team **4**, Client Y **5**.

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


## 10. Drafts, recipes and the team brief per workspace (the recipes release meets workspaces)

Added in the final integration (W5). Captured by `scripts/capture_workspaces_final.py` into `docs/fe-captures/workspaces-final/` (32 files, listed by number below). Cast, as in that script: **Ada** owns the team **Platform team** (id **4**) on the 20-hour plan; **Pat** is a member of it on Free; **Quinn** is a stranger. Pat's personal workspace is **2**. The drafts were written straight into the scratch database as finished (no model call); a draft Pat asks for stays `writing` because the draft worker is not running in the capture (see the table at the end).

### 10.1 The rule

Every draft, recipe and brief call carries `X-Team-Id` (the web already sends it on every `/api/*` call but the person's own ones, section 2). What each answers for the **active workspace**:

| Call | What the workspace changes |
|---|---|
| `GET /api/documents` (no `meeting_id`) | Your own drafts **made in** that workspace. `?shared=true`: drafts teammates shared **with that team**. Both: the two together. |
| `GET /api/documents?meeting_id=N` | **Not narrowed**: the drafts made from meeting N (the meeting page asks this), in any workspace, still only ones the caller may read. |
| `GET /api/documents/{id}` and every write on one draft | **Ignores** the header: authorised by who may read or edit that draft. |
| `POST /api/documents/{id}/share` | Shares with the team the draft was **made in**, whatever header is sent. |
| `GET /api/recipes`, `POST/PUT/DELETE /api/recipes` | The starters, the person's own recipes, and **that workspace's** recipes. |
| `GET/PUT /api/briefs`, `POST /api/briefs/import` | That workspace's brief. Members read; owners and admins write. |
| `POST /api/recipes/picker/resolve` | A date range, project or sprint is read **inside that workspace** (the Meetings list's rule). |
| `POST /api/documents` | The workspace decides whose brief and team recipes are used; the draft is filed in it (`team`). |

A team the person is not in is `404 not_found` on every call that reads the header (`{"error": {"code": "not_found", "message": "No such team."}}`), and the same for an ex-member.

### 10.2 The drafts list (captures 07, 08, 09, 10, 11)

`GET /api/documents` with `X-Team-Id: 4` as Ada (capture 07). Ada's other draft, made in her personal workspace, is **not** in it; with her personal workspace's id the same call lists only that one (capture 08). No header gave X's list too (09: Ada is in her personal workspace and in one ordinary team, so her default is that team).

```json
{
  "documents": [
    {
      "id": 1,
      "title": "Platform follow-up",
      "output_type": "document",
      "recipe_key": "follow_up_email",
      "recipe_id": null,
      "status": "ready",
      "edited": false,
      "meeting_ids": [
        1
      ],
      "created_at": "2026-11-25T12:55:23.858796Z",
      "updated_at": "2026-11-25T12:55:23.858796Z",
      "access": "owner",
      "owner_name": null,
      "shared_with": [],
      "team": {
        "id": 4,
        "name": "Platform team"
      }
    }
  ]
}
```

**Trap: `team` is the creator's view.** It is `null` for a reader of a shared draft (capture 14 below), because the team a draft was made in is the creator's to know. Read it only when `access` is `"owner"`.

### 10.3 Sharing and reading a shared draft (captures 12, 13, 14, 15, 16, 17)

`POST /api/documents/1/share` as Ada with her **personal** workspace as the header (capture 12): it still shares with Platform team, because that is where the draft was made. Excerpt (the body is the whole `DocumentOut`; fields trimmed here are `blocks`, `checks`, dates and the rest):

```json
{
  "id": 1,
  "title": "Platform follow-up",
  "status": "ready",
  "access": "owner",
  "can_edit": true,
  "shared_with": [
    {
      "id": 4,
      "name": "Platform team"
    }
  ],
  "team": {
    "id": 4,
    "name": "Platform team"
  },
  "share_blocked": null
}
```

A draft made in the personal workspace cannot be shared: `409` (capture 13)

```json
{
  "error": {
    "code": "personal_team",
    "message": "This draft was made in your own space. Make it in a team workspace to share it with that team."
  }
}
```

Pat, in Platform team, lists what teammates shared with it (capture 14). `access` is `shared`, the creator's name is shown, `team` and `shared_with` are empty for a reader:

```json
{
  "documents": [
    {
      "id": 1,
      "title": "Platform follow-up",
      "output_type": "document",
      "recipe_key": "follow_up_email",
      "recipe_id": null,
      "status": "ready",
      "edited": false,
      "meeting_ids": [
        1
      ],
      "created_at": "2026-11-25T12:55:23.858796Z",
      "updated_at": "2026-11-25T12:55:23.858796Z",
      "access": "shared",
      "owner_name": "Ada Example",
      "shared_with": [],
      "team": null
    }
  ]
}
```

The same call in Pat's personal workspace returns `{"documents": []}` (capture 15), and his own drafts in Platform team are `{"documents": []}` too (16). Opening the one draft by its id works from any workspace (17), because it is authorised by who may read it. After Pat is removed from the team, `GET /api/documents/1` is `404` `{"error": {"code": "not_found", "message": "No such document."}}` (31): access ends at once.

**UX the FE must surface.** A draft made while in a personal workspace has `share_blocked` set (a plain sentence); show it and disable Share. A draft shared with a team can be read by every member, **including one who cannot open the meeting it came from**: the Share dialog must say so (the existing dialog does).

### 10.4 Recipes and the brief (captures 18 to 23)

`GET /api/recipes` in Platform team (capture 18) returns 19 rows: the 18 starters and the team's own recipe. In Pat's personal workspace (19) only the starters. The team's recipe, verbatim; `can_edit` is false for Pat because he is a member:

```json
[
  {
    "id": 1,
    "key": null,
    "pack": null,
    "source": "team",
    "name": "Platform weekly recap",
    "instruction": "Write the recap the platform team sends on Friday.",
    "output_type": "document",
    "options": {},
    "scope": "one",
    "uses_brief": true,
    "can_edit": false,
    "updated_at": "2026-11-25T12:55:23.858796Z"
  }
]
```

A starter row looks like this (the long `instruction` and the `options` map are trimmed here; the capture has them whole):

```json
{
  "id": null,
  "key": "follow_up_email",
  "pack": "general",
  "source": "starter",
  "name": "Follow-up email",
  "output_type": "email",
  "scope": "one",
  "uses_brief": true,
  "can_edit": false,
  "updated_at": null
}
```

The envelope also has `team` (the active workspace as `{id, name, is_personal, can_manage}`). The brief (capture 20, as Pat):

```json
{
  "team": {
    "id": 4,
    "name": "Platform team",
    "is_personal": false,
    "can_manage": false
  },
  "text": "Our prices: audit 50,000 naira, retainer 120,000 naira.",
  "max_chars": 12000,
  "updated_at": "2026-11-25T12:55:23.858796Z",
  "updated_by": null
}
```

A member writing it: `403` `{"error": {"code": "team_forbidden", "message": "Only an owner or admin of the team can do that."}}` (capture 21). An owner may (22). Pat's personal workspace has a brief of its own, empty: `"text": ""`, `"updated_at": null` (23).

### 10.5 The meeting picker (captures 24, 25)

`POST /api/recipes/picker/resolve` with `{"from_day": "2020-01-01", "to_day": "2099-12-31"}`. In Platform team Pat is offered the team's meeting (capture 24); in his personal workspace only his own call (25). Capture 24 whole:

```json
{
  "meetings": [
    {
      "id": 1,
      "title": "Platform planning call",
      "day": "2026-11-25"
    }
  ],
  "left_out_count": 0,
  "left_out_message": null,
  "capped": false,
  "max_meetings": 12
}
```


### 10.6 Making a draft: the plan gate follows the workspace the meetings live in (captures 26, 27, 28)

A member on Free may **make** (and Ask in) a workspace whose **owner** has a plan, from that workspace's meetings, within the daily limits. The gate looks at the chosen meetings: if they are all filed in one workspace the caller belongs to and its owner has a live plan, it passes; otherwise it is the caller's own plan. The header does not decide it (a meeting of Platform team asked for with the personal header passes the same way). Trial or pilot grants of the owner do **not** cover members.

Pat (Free), meeting 1 (in Platform team, whose owner has a plan): accepted, `202`. This is the whole response:

```json
{
  "id": 3,
  "title": "Follow-up email",
  "output_type": "email",
  "recipe_key": "follow_up_email",
  "recipe_id": null,
  "status": "writing",
  "edited": false,
  "meeting_ids": [
    1
  ],
  "created_at": "2026-11-25T12:55:24.747083Z",
  "updated_at": "2026-11-25T12:55:24.747083Z",
  "access": "owner",
  "owner_name": null,
  "shared_with": [],
  "team": {
    "id": 4,
    "name": "Platform team"
  },
  "blocks": [],
  "checks": {
    "blanks": [],
    "flags": []
  },
  "stale": false,
  "sources_available": true,
  "left_out_count": 0,
  "failure_code": null,
  "failure_message": null,
  "failure_detail": null,
  "options": {},
  "use_brief": true,
  "can_edit": true,
  "share_blocked": null,
  "left_out_message": null
}
```

Pat's own meeting in his own workspace: `403` (capture 27)

```json
{
  "error": {
    "code": "plan_inactive",
    "message": "Making documents needs an active plan or trial. You can still read, search and export your notes, and use your tasks and decisions."
  }
}
```

After Ada's plan lapsed, the first request is refused the same way (capture 28); once she is active again it passes. **The page must say the reason**, not just "failed": use `error.message` as it is.

### 10.7 What leaving does (captures 29 to 32)

Removing Pat (`DELETE /api/team/members/2` as Ada: `204`, capture 29) ends, at once: `GET /api/documents?shared=true` with the team's header is `404 not_found` "No such team." (30), `GET /api/documents/1` is `404 not_found` "No such document." (31), `GET /api/recipes` with the team's header is `404` (32). The web handles the first through `onTeamGone` (back to the default workspace with a notice).

## 11. The rest of the workspaces release

This guide is the entry point. The other slices have their own guides, each verified against captures of the same kind:

| Area | Guide | Captures |
|---|---|---|
| Templates, vocabulary, per-workspace settings, "keep private", the share default | `docs/fe-integration-guide-workspaces-settings.md` | `docs/fe-captures/workspaces/settings-*.json` |
| Calendar per workspace, arbitration, Upcoming "recorded in", recurring per workspace | `docs/fe-integration-guide-workspaces-calendar.md` | `docs/fe-captures/workspaces/calendar/` |
| Owned-workspace limit, create, delete, leave, classes, the gate rule for Ask | `docs/fe-integration-guide-workspaces-lifecycle.md` | `docs/fe-captures/workspaces-lifecycle/` |

### Things the page must do that no single response shows

- **After the release is deployed, every account has a personal workspace and every old meeting is in one workspace.** Nothing the person could read before is hidden and nothing was shared: the existing `shared_team_id` stays as it was (rehearsed on a copy of production-shaped data, `docs/fe-captures/workspaces-cutover/cutover-rehearsal.txt`). A person who used two teams will find them as two workspaces; their **calendar connection, templates and vocabulary** moved to their default workspace (the one they already saw their meetings in) and were copied once.
- **The header is required context.** The web must send `X-Team-Id` on every call that is not the person's own (section 2); a missing header is the default workspace, never "everything". A call that forgets it silently shows the default workspace.
- **Personal recipes are not per workspace.** A recipe a person wrote for themselves (`source: "personal"`) appears in every workspace of theirs; only team recipes and the brief change. Do not label them with a workspace.

## 12. Verification table (sections 10 and 11)

| Behaviour | Status |
|---|---|
| Drafts list per workspace; own, shared, both; header ignored with `meeting_id` | verified live (captures 07 to 11, 14 to 16) |
| Share goes to the draft's own team; personal draft 409; shared draft readable by id from any workspace | verified live (12, 13, 17) |
| Recipes library, brief, brief write refusal per workspace | verified live (18 to 23) |
| Meeting picker per workspace | verified live (24, 25) |
| Make accepted for a free member on a paid owner's workspace; refused for own meeting and after a lapse | verified live (26 to 28) |
| Removal ends access to shared drafts, recipes and the team at once | verified live (29 to 32) |
| A draft actually **written** by the model for such a member | **not run live**: the capture has no model and no draft worker; the worker is the recipes release's, covered by `tests/test_recipe_jobs.py` |
| Personal recipes shown in every workspace | by design, stated from the code (`app/api_recipe_library.py` lists personal rows for the person whatever the header); no capture of it |
| Cutover on production-shaped data | rehearsed on a scratch database (`docs/fe-captures/workspaces-cutover/cutover-rehearsal.txt`); not run on the real server |
