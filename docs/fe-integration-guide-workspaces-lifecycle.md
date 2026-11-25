# FE integration guide: workspace limits, delete, classes and the member gate (slice W4)

Every body below is copied from a real response captured against the running app (`scripts/capture_workspaces_lifecycle.py`; real migrations, real app, scratch Postgres; files in `docs/fe-captures/workspaces-lifecycle/`). Names are placeholders (Ada, Pat, Quinn, `example.test`). Ids are the scratch database's. Where something could not be run live it is marked **NOT LIVE**.

## 1. What this is, in the screen's terms

- A person may **own** only as many team workspaces as their plan allows (Free none, 20-hour plan 3, Pro 10, Scale as many as they like). Being a **member** of any number is free. The personal space never counts.
- An owner can **delete** a team once nobody else is in it. Its meetings are not deleted: each goes back to the person who recorded it, private, in their personal space.
- A **class** is a team in a school's words (Class, Students, Lecturer) that shares every meeting with its members by default. Same screens, same rules, one wording helper.
- A free member can **Ask** (and later **Make**) on a team whose OWNER has a plan. Nothing else widens.

## 2. `GET /api/teams` (the switcher's list and the Make-a-team card)

`GET /api/teams`. Pat, on Free:

```json
{
  "teams": [
    {
      "id": 2,
      "name": "My team",
      "role": "owner",
      "member_count": 1,
      "is_personal": true,
      "is_default": false,
      "can_manage": true,
      "kind": "team"
    },
    {
      "id": 4,
      "name": "Platform team",
      "role": "member",
      "member_count": 2,
      "is_personal": false,
      "is_default": true,
      "can_manage": false,
      "kind": "team"
    }
  ],
  "can_create_more": 0,
  "owned_teams": 0,
  "max_owned_teams": 0,
  "plan_name": null,
  "limit_message": "Owning a team needs a paid plan. Upgrade to the 20 hours a month plan to own up to 3 teams."
}
```
*Captured: `docs/fe-captures/workspaces-lifecycle/07-pat-teams-free.json` (HTTP 200).*

Ada, on the 20-hour plan, owning 1 of 3:

`GET /api/teams`

```json
{
  "teams": [
    {
      "id": 1,
      "name": "My team",
      "role": "owner",
      "member_count": 1,
      "is_personal": true,
      "is_default": false,
      "can_manage": true,
      "kind": "team"
    },
    {
      "id": 4,
      "name": "Platform team",
      "role": "owner",
      "member_count": 2,
      "is_personal": false,
      "is_default": true,
      "can_manage": true,
      "kind": "team"
    }
  ],
  "can_create_more": 2,
  "owned_teams": 1,
  "max_owned_teams": 3,
  "plan_name": "20 hours a month",
  "limit_message": null
}
```
*Captured: `docs/fe-captures/workspaces-lifecycle/09-ada-teams-starter.json` (HTTP 200).*

**Field-nesting and null traps.** `can_create_more` is a number, or **null when the plan has no limit** (Scale, an admin). `max_owned_teams` is a number, **0 on Free**, or null for unlimited. `plan_name` is null on Free **and for a person whose plan has lapsed**. `limit_message` is null while the person may make another team; when it is a string, show it as it is (it names the plan to upgrade to) and link to the plans screen. `kind` is on every team row, the personal space included (`"team"`).

At the limit (three of three):

`GET /api/teams`

```json
{
  "can_create_more": 0,
  "owned_teams": 3,
  "max_owned_teams": 3,
  "plan_name": "20 hours a month",
  "limit_message": "Your plan owns up to 3 teams and you have 3. Upgrade to the Pro plan to own up to 10 teams."
}
```
*(the `teams` array omitted here; captured in full at `docs/fe-captures/workspaces-lifecycle/13-ada-teams-at-limit.json`)*

## 3. `POST /api/teams` (make a team or a class)

`{"name": "...", "kind": "team" | "class"}`; `kind` is optional (`team`). Unknown `kind` is a 422.

`POST /api/teams`, body `{"name": "Maths 101", "kind": "class"}`

```json
{
  "id": 5,
  "name": "Maths 101",
  "role": "owner",
  "member_count": 1,
  "is_personal": false,
  "is_default": false,
  "can_manage": true,
  "kind": "class"
}
```
*Captured: `docs/fe-captures/workspaces-lifecycle/10-ada-create-class.json` (HTTP 201).*

The class's own page (`GET /api/team` with the class in `X-Team-Id`), note `kind` and `share_new_meetings`:

`GET /api/team` with `X-Team-Id: 5`

```json
{
  "id": 5,
  "name": "Maths 101",
  "is_personal": false,
  "kind": "class",
  "share_new_meetings": true,
  "reminders_on": false,
  "reminder_timing": "both",
  "my_role": "owner",
  "sprint_length": "week5",
  "sprint_custom_days": 10,
  "sprint_align_weekdays": true,
  "member_count": 1,
  "pending_invites": 0,
  "can_manage": true,
  "can_change_roles": true
}
```
*Captured: `docs/fe-captures/workspaces-lifecycle/11-ada-class-team.json` (HTTP 200).*

Refusals:

- Free: `POST /api/teams`, body `{"name": "Mine"}`

```json
{
  "error": {
    "code": "team_limit",
    "message": "Owning a team needs a paid plan. Upgrade to the 20 hours a month plan to own up to 3 teams."
  }
}
```
*Captured: `docs/fe-captures/workspaces-lifecycle/08-pat-create-team-free.json` (HTTP 409).*

- At the limit: `POST /api/teams`, body `{"name": "One too many"}`

```json
{
  "error": {
    "code": "team_limit",
    "message": "Your plan owns up to 3 teams and you have 3. Upgrade to the Pro plan to own up to 10 teams."
  }
}
```
*Captured: `docs/fe-captures/workspaces-lifecycle/14-ada-create-over-limit.json` (HTTP 409).*

- A plan that lapsed (the person still owns the teams): `POST /api/teams`, body `{"name": "Another"}`

```json
{
  "error": {
    "code": "team_limit",
    "message": "Owning a team needs a paid plan. Upgrade to the 20 hours a month plan to own up to 3 teams."
  }
}
```
*Captured: `docs/fe-captures/workspaces-lifecycle/20-ada-create-lapsed.json` (HTTP 409).*

- Bad `kind`: `POST /api/teams`, body `{"name": "Odd", "kind": "school"}`

```json
{
  "error": {
    "code": "validation_error",
    "message": "Some fields are missing or not valid.",
    "fields": [
      {
        "field": "kind",
        "message": "Input should be 'team' or 'class'"
      }
    ]
  }
}
```
*Captured: `docs/fe-captures/workspaces-lifecycle/15-ada-create-bad-kind.json` (HTTP 422).*

**UX consequences the FE must surface.**
- A lapse or a downgrade **never removes or locks a team**. After a lapse `GET /api/teams` shows `owned_teams` 3 and `max_owned_teams` 0 (capture `19-ada-teams-lapsed.json`): say nothing alarming; only the Make-a-team card is off, with `limit_message`.
- The refusal and `limit_message` are the same sentence: show the server's, never your own copy.

## 4. `DELETE /api/teams/{id}` (owner only, nobody else in the team)

204 with no body on success (capture `32-ada-delete-team-alone.json`). Ask first: say the meetings are **not** deleted and go back to the people who recorded them, private, in their personal spaces. After a delete, **load the Teams list afresh** (a full page load, like the switcher), and forget the stored team.

Refusals, all verbatim:

- someone else is in it: `DELETE /api/teams/4`

```json
{
  "error": {
    "code": "team_has_members",
    "message": "Other people are still in this team. Remove them first: you can delete it once you are its only member."
  }
}
```
*Captured: `docs/fe-captures/workspaces-lifecycle/21-ada-delete-with-members.json` (HTTP 409).*

- not the owner: `DELETE /api/teams/4`

```json
{
  "error": {
    "code": "team_forbidden",
    "message": "Only the owner of the team can delete it."
  }
}
```
*Captured: `docs/fe-captures/workspaces-lifecycle/22-pat-delete-not-owner.json` (HTTP 403).*

- not in the team at all (the same answer as for a team that does not exist): `DELETE /api/teams/4`

```json
{
  "error": {
    "code": "not_found",
    "message": "No such team."
  }
}
```
*Captured: `docs/fe-captures/workspaces-lifecycle/23-quinn-delete-stranger.json` (HTTP 404).*

- the personal space: `DELETE /api/teams/1`

```json
{
  "error": {
    "code": "personal_team",
    "message": "Your personal space cannot be deleted."
  }
}
```
*Captured: `docs/fe-captures/workspaces-lifecycle/24-ada-delete-personal.json` (HTTP 409).*

After a delete, the meetings:

- the owner's own meeting is in her personal space (`workspace_team_id`, `workspace`): `GET /api/meetings/1`

```json
{
  "id": 1,
  "title": "Ada's planning call",
  "status": "ready",
  "workspace_team_id": 1,
  "workspace": {
    "id": 1,
    "name": "My team",
    "is_personal": true,
    "kind": "team"
  },
  "shared_with_team": false,
  "owned_by_team": false,
  "team": null,
  "shared_by": null,
  "...": "(other fields omitted from this excerpt)"
}
```
*Captured: `docs/fe-captures/workspaces-lifecycle/33-ada-adas-meeting-home.json` (HTTP 200).*

- the meeting Pat recorded and MOVED to the team is Pat's again, in HIS personal space, private (never deleted while its recorder has an account): `GET /api/meetings/2`

```json
{
  "id": 2,
  "title": "Pat's client call",
  "status": "ready",
  "workspace_team_id": 2,
  "workspace": {
    "id": 2,
    "name": "My team",
    "is_personal": true,
    "kind": "team"
  },
  "shared_with_team": false,
  "owned_by_team": false,
  "team": null,
  "shared_by": null,
  "...": "(other fields omitted from this excerpt)"
}
```
*Captured: `docs/fe-captures/workspaces-lifecycle/34-pat-pats-meeting-home.json` (HTTP 200).*

- the deleted team is nobody's: `GET /api/team` with `X-Team-Id: 4`

```json
{
  "error": {
    "code": "not_found",
    "message": "No such team."
  }
}
```
*Captured: `docs/fe-captures/workspaces-lifecycle/35-ada-team-deleted.json` (HTTP 404).*

**NOT LIVE:** deleting a team that holds a meeting whose recorder's account is gone (that meeting IS deleted, with its recording). It needs a bot provider; `tests/test_workspace_lifecycle.py` covers it with the fake provider. The route returns 409 `meeting_active` or 502 `recording_delete_failed` if such a meeting cannot be removed yet; retrying finishes the job (the team is still there).

## 5. Leaving and being removed

- The personal space cannot be left: `DELETE /api/team/members/2` with `X-Team-Id: 2`

```json
{
  "error": {
    "code": "personal_team",
    "message": "Your personal space cannot be left."
  }
}
```
*Captured: `docs/fe-captures/workspaces-lifecycle/25-pat-leave-personal.json` (HTTP 409).*

- The owner cannot leave (new words): `DELETE /api/team/members/1` with `X-Team-Id: 4`

```json
{
  "error": {
    "code": "owner_cannot_leave",
    "message": "You own this team. Ask the pilot admin to hand it to someone else before you leave, or delete it once you are its only member."
  }
}
```
*Captured: `docs/fe-captures/workspaces-lifecycle/26-ada-owner-leave.json` (HTTP 409).*

- Removing a member (owner or admin): `DELETE /api/team/members/2` with `X-Team-Id: 4`: 204. The member **loses access at once**:

`GET /api/team` with `X-Team-Id: 4`: {"error": {"code": "not_found", "message": "No such team."}}
`GET /api/meetings` with `X-Team-Id: 4`: {"error": {"code": "not_found", "message": "No such team."}}
`GET /api/meetings/1`: {"error": {"code": "not_found", "message": "No such meeting"}}

- A meeting a member MOVED to the team stays with it, for the team to read (Ada, after Pat was removed): `GET /api/meetings/2`

```json
{
  "id": 2,
  "title": "Pat's client call",
  "status": "ready",
  "workspace_team_id": 4,
  "workspace": {
    "id": 4,
    "name": "Platform team",
    "is_personal": false,
    "kind": "team"
  },
  "shared_with_team": true,
  "owned_by_team": true,
  "team": {
    "id": 4,
    "name": "Platform team"
  },
  "shared_by": "Pat Example",
  "...": "(other fields omitted from this excerpt)"
}
```
*Captured: `docs/fe-captures/workspaces-lifecycle/31-ada-pats-meeting-stays.json` (HTTP 200).*

What a leaver recorded and kept (not moved) goes to their own personal space, private (covered by `tests/test_workspace_lifecycle.py`, not captured). **NOT LIVE here: the leaver's calendar connection and scheduled joins**, which slice W3 disconnects and cancels.

## 6. The member gate (Ask now, Make when recipes lands)

`POST /api/ask` sends the active workspace in `X-Team-Id` as always. A person with no plan:

- on a team whose owner has a plan: allowed. `POST /api/ask` with `X-Team-Id: 4`, body `{"question": "What are my open tasks?"}`

```json
{
  "kind": "tasks",
  "lead": "You have no open tasks assigned to you.",
  "points": []
}
```
*Captured: `docs/fe-captures/workspaces-lifecycle/16-pat-ask-in-paid-workspace.json` (HTTP 200).*

(That is the free "my tasks" answer, which sits behind the same gate; no model is called in the capture.)

- in their own personal space: `POST /api/ask` with `X-Team-Id: 2`, body `{"question": "What are my open tasks?"}`

```json
{
  "error": {
    "code": "plan_inactive",
    "message": "Asking Beyric needs an active plan or trial. You can still read, search and export your notes, and use your tasks and decisions."
  }
}
```
*Captured: `docs/fe-captures/workspaces-lifecycle/17-pat-ask-in-personal-workspace.json` (HTTP 403).*

- on that same team after the owner's plan lapsed: `POST /api/ask` with `X-Team-Id: 4`, body `{"question": "What are my open tasks?"}`

```json
{
  "error": {
    "code": "plan_inactive",
    "message": "Asking Beyric needs an active plan or trial. You can still read, search and export your notes, and use your tasks and decisions."
  }
}
```
*Captured: `docs/fe-captures/workspaces-lifecycle/18-pat-ask-owner-lapsed.json` (HTTP 403).*

**What covers a member:** the workspace owner is on a plan (`active`, or the 3 grace days after a failed renewal) or is an admin. A trial or a pilot grant does not. **Daily caps are the member's own** (their own 429 `ask_limit`), never shared with the owner. `translate` and `rewrite` stay the caller's own plan: a free member still gets 403 `plan_inactive` there.

**NOT LIVE:** the `make` gate (the recipes release is not on this branch). The rule is the same function; the shape of its refusal is the 403 `plan_inactive` above with the words "Making documents needs an active plan or trial. You can still read, search and export your notes, and use your tasks and decisions." (from `tests/test_workspace_gate.py`, not captured).

## 7. Verification table

| Behaviour | Status |
|---|---|
| `GET /api/teams` fields (Free, Starter, at the limit, lapsed, after deletes) | verified live (captures 07, 09, 13, 19, 36) |
| `POST /api/teams` team, class, Free refusal, limit refusal, lapsed refusal, bad kind | verified live (08, 10, 14, 15, 20) |
| Class `kind` and `share_new_meetings` on `GET /api/team` | verified live (11) |
| `DELETE /api/teams/{id}` success and the four refusals | verified live (21 to 24, 32) |
| Meetings after a delete (home, private, recorder's own space) | verified live (33, 34) |
| Delete with a recorder-gone meeting | NOT LIVE (test with the fake provider) |
| Personal space cannot be left; owner cannot leave | verified live (25, 26) |
| Removed member loses access at once; moved meeting stays | verified live (27 to 31) |
| Leaver's calendar connection and joins | NOT LIVE (slice W3) |
| Ask gate for a free member (paid owner, personal space, lapsed owner) | verified live (16 to 18) |
| `make` gate, `translate`/`rewrite` caller-only, caps per caller, the three modes | NOT LIVE (tests only: `tests/test_workspace_gate.py`) |
| Screens at 390 and 1280 px | verified in a real browser against the local app |
