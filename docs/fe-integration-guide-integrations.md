# Frontend guide: Integrations (W10), part 1: Slack

Every body below is copied from `docs/fe-captures/integrations-w10/` (run `scripts/capture_integrations_api.py`). **Slack itself is simulated in those captures** (the app, auth, roles, validation, database and vault are real); the real HTTP call to Slack is covered by backend tests. Webhook addresses are fake placeholders.

## What it is
In Settings > Tasks, the row "Post tasks to Slack". An owner or admin pastes a Slack incoming-webhook address. After a meeting's notes are read, the channel gets one message listing the meeting's new tasks and their owners. It is per team, not per person.

## Endpoints (all need a login; writes need `X-CSRF-Token`)
| Call | Who | Success |
|---|---|---|
| `GET /api/integrations/slack` | any team member | 200 `SlackOut` |
| `PUT /api/integrations/slack` body `{"webhook_url": "..."}` | owner or admin | 200 `SlackOut` |
| `DELETE /api/integrations/slack` | owner or admin | 204, empty; safe to repeat |

`SlackOut` (captures 05, 10, 11, 15):
```json
{ "connected": true, "status": "connected", "last_error": null, "last_posted_at": null, "can_manage": true }
```
Not connected (05): `connected:false, status:null, last_error:null, last_posted_at:null`. After a post (14) `last_posted_at` is an ISO time. A failing connection (15): `"status": "failing", "last_error": "Slack no longer accepts this webhook. It may have been removed, so connect it again."`. A teammate (11): `can_manage:false`. **The address is never in any response.** Do not keep it in state after sending.

## Errors (exact, from the captures)
| Status | code | message |
|---|---|---|
| 400 (06) | `slack_webhook_invalid` | "That is not a Slack webhook address. It starts with https://hooks.slack.com/services/." |
| 502 (07) | `slack_unreachable` | "Slack no longer accepts this webhook. It may have been removed, so connect it again." (fixed sentences; Slack's own words are never passed on) |
| 403 (08, 12) | `team_forbidden` | "Only an owner or admin of the team can do that." |
| 429 | `integration_rate` | "Too many tries. Wait a little and try again." (5 connect tries per team per hour; **not captured**: shape from the code, covered by a backend test) |
| 503 | `integrations_unavailable` | "Connecting is not available right now." (no vault key on the server; **not captured**, backend test only) |

**Trap:** the address error is a **400**, not 422, on purpose: the app's `ApiError.isShowable` treats 404, 405 and 422 as developer text and would hide the sentence behind "Something went wrong".

## UX rules the FE must follow
- Connecting posts a **test message** into the person's channel. Say so beside the field (the screen does).
- Show `status:"failing"` with `last_error` on the card; posts keep being tried for the next meetings. Reconnecting clears it.
- A failed post never affects notes or tasks. Do not show Slack errors on the meeting page.
- Show Connect/Disconnect only when `can_manage`; a member gets a note instead.
- Only meetings **shared with the team** post (a private meeting's tasks are not visible to teammates, so they are not sent to Slack). Tell the person this next to the switch if asked; sharing later does not post that meeting.
- Nothing is posted for a meeting older than the mail window, for backfilled meetings, or twice for the same meeting. Hourly cap: 30 posts per team (skipped, not queued).
- The channel name is unknown (a webhook does not reveal it): show "Slack connected", not `#channel`.

## What gets posted (capture 13, simulated Slack)
`*<https://notes.beyrictech.com/meetings/1|Planning call>*: 2 new tasks` then `• send the contract (Dan Okoro)` and `• Someone should book the venue (needs an owner)`. Web addresses in task text are removed, bare domains written `evil[.]com`, mentions and links neutralised.

## Verification table
| Behaviour | Verified |
|---|---|
| GET/PUT/DELETE shapes, 400/403/502, member view, failing state | live against the running app (Slack simulated) |
| Post text after a real extraction | live (Slack simulated) |
| Real call to hooks.slack.com (pinned IP, no redirect, status mapping) | backend tests with a mock transport, not live |
| 429 `integration_rate`, 503 `integrations_unavailable` | backend tests only |
