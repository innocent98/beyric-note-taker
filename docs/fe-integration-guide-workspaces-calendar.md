# FE integration guide: calendar per workspace

**Status.** Built on branch `feature/workspaces-calendar` (slice W3, PR to `feature/workspaces`). Every body below is copied from a real response captured against the running app by `scripts/capture_workspaces_calendar.py` (files in `docs/fe-captures/workspaces/calendar/`, named in each heading). **Nothing was verified against a real Google or Microsoft calendar:** Google's consent screen, token endpoint and the event body are `tests/fakes/google.py` and a synthetic body; people, links and tokens are placeholders. Read with `docs/fe-integration-guide-workspaces.md` (the header rule) and the older calendar guides (`-calendar-connect`, `-calendar-settings`, `-calendar-join`, `-calendar-outlook`), which still describe every field not named here.

## 1. What changed, in the FE's terms

A calendar connection belongs to **one workspace**. A person may connect Google (or Outlook) in several workspaces, even the same account; each workspace has its own Upcoming list, Recurring cards, "not recorded" list, auto-join settings and connection. Every calendar route reads `X-Team-Id` (else the person's default workspace; a team they are not in is 404 `not_found`). The web already sends it on every route except the provider callback (`web/lib/api/client.ts`).

When the **same meeting** is on the calendar of two of their workspaces, only ONE records it (the first-connected workspace whose rules would join it, unless the person switched it on in another; a note taker already in the meeting is never moved). The other workspace's Upcoming row says where it is recorded and offers a switch.

## 2. Connect starts from the active workspace

`POST /api/calendar/google/connect` with `X-Team-Id: <personal>` -> **200** (capture `06-connect-personal-start.json`): `{"authorize_url": "https://accounts.google.com/o/oauth2/v2/auth?client_id=...&state=<state>&...", "show_unverified_notice": ...}`; sets the `calendar_flow` cookie as before.

The callback carries no header; the workspace is remembered with the single-use state. **Return address** (captures `07`, `09`): `302 /settings?calendar=connected&team=1` when the workspace is not the person's default, `302 /settings?calendar=connected` when it is. **Trap:** the `team` parameter is present only for a non-default workspace; the page must read it as the workspace to open on (the `/settings` page already does).

Errors, all as `?calendar_error=` on the return address (plus `&team=` as above):

| Code | When | Capture |
|---|---|---|
| `different_account` | another account of the same provider is connected in THIS workspace | `11` (`/settings?calendar_error=different_account`) |
| `already_connected` | another provider's live calendar is in this workspace (the callback side) | not captured; covered by `tests/test_calendar_workspaces.py` |
| `workspace_unavailable` | the person left the workspace while the consent screen was open | not captured live; covered by tests |

`POST .../connect` -> **409** `calendar_already_connected` when another provider's calendar is already connected in this workspace (capture `26`; the Microsoft row was set directly in the database, not a real Outlook connection):

```json
{"error": {"code": "calendar_already_connected", "message": "Another calendar is already connected. Disconnect it first."}}
```

`POST .../connect` with a workspace the person is not in -> 404 `not_found` "No such team." (same as every route).

## 3. The calendar state is the workspace's

`GET /api/calendar` with the header of a workspace with no connection -> `connected:false, state:"not_connected", upcoming:[]` (capture `05`); with one connected -> the usual body (captures `12`, `13`). Same account in two workspaces gives two independent states. A workspace the person is not in -> **404** (capture `14`):

```json
{"error": {"code": "not_found", "message": "No such team."}}
```

`PUT /api/calendar/settings` changes the rule, the two boxes and the template **of the active workspace only** (stored per workspace); the answer is the whole state as before.

## 4. Upcoming: a meeting another workspace records

`GET /api/calendar` in Luran, same meeting on both calendars (capture `16`; trimmed to the two rows):

```json
{
  "id": 3, "title": "Weekly planning", "platform": "google_meet", "platform_label": "Google Meet",
  "attendee_count": 2, "will_join": false, "will_join_text": "Off", "reason_text": null,
  "join_id": null, "can_send_now": false, "not_recorded_text": null,
  "recorded_in": {"team_id": 1, "name": "My team", "is_personal": true},
  "recorded_in_text": "Recorded in My team (personal). Switch here instead.",
  "moved_text": null
}
```

The recording workspace's row (capture `15`) has `will_join: true`, `recorded_in: null`, `recorded_in_text: null`.

**FE rules.** Show `recorded_in_text` as the row's muted line and a "Switch here" button (the web does this in `calendar-upcoming.tsx`); the button is the same write as the switch: `PUT /api/calendar/events/{id}/override` `{"override": true}`. `will_join_text` is `Off` for such a row (not a new word). **Traps:** `join_id` can be non-null on a row that is recorded elsewhere (a cancelled join; `can_send_now` is false); `recorded_in.name` is the raw workspace name and `is_personal` says to write "(personal)" (the text already does).

## 5. The switch moves the recording

`PUT /api/calendar/events/3/override` `{"override": true}` with Luran's header -> **200** (capture `19`):

```json
{
  "id": 3, "title": "Weekly planning", "will_join": true, "will_join_text": "Will join",
  "join_id": 4, "can_send_now": false,
  "recorded_in": null, "recorded_in_text": null,
  "moved_text": "Moved here from My team (personal). My team (personal) will not record this meeting."
}
```

**UX consequence the FE must surface:** show `moved_text` where the person sees it (the web puts it under the row's meta line). The other workspace's join, if it had not started, is cancelled, and its row now reads "Recorded in Luran" (capture `20`). Switching it back, or switching Luran off, hands it to the other workspace.

`409 already_recording` (capture `21`; a bot already in was set directly in the database, not a real bot): nothing changes, show the message.

```json
{"error": {"code": "already_recording", "message": "Luran is already recording this meeting. A note taker that is already in the meeting is never moved."}}
```

## 6. Recurring, not recorded, remove

`GET /api/calendar/recurring` and `GET /api/not-recorded` follow the header (captures `17`, `18`, `22`). A recurring meeting removed from a workspace (`series_ignored`, already per workspace) has **no card in that workspace** and keeps its card in another (tested: `tests/test_calendar_workspace_api.py`).

## 7. Disconnect, leave, removal

`POST /api/calendar/google/disconnect` with the workspace's header -> 200 `{"status": "disconnected", "revoked": true}` (capture `23`). It deletes that connection's events, the scheduled joins filed in that workspace and its calendar notices; the person's other workspaces are untouched. **`revoked: true` does not always mean Google was called:** when another workspace of theirs uses the same Google account nothing is revoked at Google (the grant is still in use; capture `24` shows 0 revoke calls); the last workspace using the account revokes it. Show the "remove Beyric at myaccount.google.com/permissions" hint only when `revoked` is false.

A member who **leaves or is removed** (`DELETE /api/team/members/{user_id}` -> 204, capture `29`) has their connection in that workspace disconnected and their scheduled joins there removed; the provider grant is revoked only if no other workspace of theirs uses the account (capture `31`: one token revoked). Their recorded meetings stay. Afterwards that workspace is 404 for them (capture `30`).

## 8. Verification table

| Behaviour | Status |
|---|---|
| Connect in two workspaces (same account), return address, `different_account`, foreign workspace 404 | verified live (captures 05 to 14), fake Google |
| Same meeting in two workspaces, `recorded_in`, switch and `moved_text`, hand back | verified live (15, 16, 19, 20); meetings planned by the real materialiser from a synthetic event body |
| `already_recording` | route live, **the running bot was set directly in the database** (21) |
| `calendar_already_connected` | route live, **the other provider's row was set directly** (26); no real Outlook sign-in |
| Reference-counted revoke on disconnect and on leave | verified live against the fake Google's revoke endpoint (23, 24, 29, 31) |
| `already_connected` and `workspace_unavailable` on the callback | covered by tests only, not captured |
| Meeting born in the winner's workspace | covered by `tests/test_calendar_arbitration.py` with a fake bot host; no live bot |
| A real Google or Microsoft calendar | **not verified** |
