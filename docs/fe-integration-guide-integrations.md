# Frontend guide: Integrations (W10): Slack and Linear

Every body below is copied from `docs/fe-captures/integrations-w10/` (run `scripts/capture_integrations_api.py`). **Slack and Linear themselves are simulated in those captures** (the app, auth, roles, validation, database and vault are real); the real HTTP call to Slack is covered by backend tests. Webhook addresses are fake placeholders.

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


---

# Part 2: Linear (the project tool)

In the same Settings > Tasks card, "Sync with your project tool". Only Linear is built; Jira, Asana and Taiga stay listed but unselectable. After connecting, each new task from a meeting **shared with the team** becomes a Linear issue (title, due date, owner's name and a link to the meeting), once. It is one way and create-only: later edits and status changes are not synced.

## Endpoints (captures 19 to 32)
| Call | Who | Success |
|---|---|---|
| `GET /api/integrations/linear` | any team member | 200 `LinearOut` |
| `POST /api/integrations/linear/teams` body `{"api_key": "..."}` | owner or admin | 200 `{"teams": [{"id","key","name"}]}` (the key is **not** stored) |
| `PUT /api/integrations/linear` body `{"api_key": "...", "team_id": "..."}` | owner or admin | 200 `LinearOut` |
| `DELETE /api/integrations/linear` | owner or admin | 204; safe to repeat |

`LinearOut` (26): `{"connected": true, "status": "connected", "last_error": null, "last_synced_at": null, "team_name": "Engineering", "can_manage": true}`. Not connected (19): all null/false except `can_manage`. After a push (29) `last_synced_at` is set. Failing (30): `"status": "failing"` and a fixed `last_error`. **The key is never in any response.** Note the field is `last_synced_at` here, `last_posted_at` for Slack.

Flow for the screen: paste key -> `POST .../teams` -> choose a team -> `PUT` with the same key and the chosen `team_id` (the key is sent twice; keep it only in the field until the PUT succeeds, then clear it).

## Errors (exact strings from the captures)
| Status | code | message |
|---|---|---|
| 400 (20) | `linear_key_invalid` | "That does not look like a Linear personal API key. It starts with lin_api_." |
| 400 (21) | `linear_key_rejected` | "Linear did not accept that key. Create a new personal API key in Linear and paste it." |
| 400 (24) | `linear_team_invalid` | "That team is not one this key can use." |
| 403 (22) | `team_forbidden` | "Only an owner or admin of the team can do that." |
| 429 | `integration_rate` | "Too many tries. Wait a little and try again." (5 key checks per team per hour; **not captured**, backend test only) |
| 502 | `linear_unreachable` | "We could not reach Linear." / "Linear took too long to answer." / "Linear refused the request. Check that the key can create issues in that team." (**not captured**, backend tests only) |
| 503 | `integrations_unavailable` | "Connecting is not available right now." (**not captured**) |

These are **400**, not 422, on purpose (see the Slack trap above).

## UX rules
- Tell the person which tasks go: only meetings shared with the team, and only the first time notes are read; sharing later does not push.
- Show `status:"failing"` with `last_error`. A key Linear refused stops all pushes until it is replaced (reconnect); other failures are tried again at the next meeting.
- Disconnecting keeps issues already created in Linear.
- Caps: 15 issues per meeting, 60 per team per hour (skipped, not queued).
- Teammates see the state, not the buttons (`can_manage`).

## What is sent for a task (capture 28, simulated Linear)
`{"team_id": "team-0000-fake", "title": "send the contract", "description": "Owner in the meeting: Dan Okoro\n\nFrom Third call: https://notes.beyrictech.com/meetings/3", "dueDate": null}`; a task with no matched owner says `needs an owner`.

## Verification table (part 2)
| Behaviour | Verified |
|---|---|
| GET/POST teams/PUT/DELETE shapes, 400/403, member view, failing state, issue payload after a real extraction | live against the running app (Linear simulated) |
| The real GraphQL call to api.linear.app (pinned IP, headers, answer mapping) | backend tests with a mock transport; **not live against Linear** (no real key was used) |
| 429, 502, 503 | backend tests only |
