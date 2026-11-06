# SOP: W10 Integrations, part 2: Linear as the team's tracker (migration 0033)

## What shipped
An owner or admin connects Linear with a pasted personal API key and picks a Linear team; after that, each new task from a meeting **shared with the team** is created as an issue in that team, once. API `GET/PUT/DELETE /api/integrations/linear` and `POST /api/integrations/linear/teams`; the "Sync with your project tool" row of Settings > Tasks; a Linear line in the privacy page. Builds on the Slack PR (`docs/sop/2026-11-06-w10-slack.md`). PR: branch `feature/w10-tracker`.

## Decision
**Linear, chosen by Adebayo on 2026-11-06 (16:43 WAT)**, relayed by the lead after the options were put to him (spec D11: "Slack webhook, then one tracker you name"). Options weighed: Linear (pasted personal key, one fixed host `api.linear.app`, GraphQL, free plan, about M), Jira Cloud (per-site host and email+token, project/issue-type mapping, about L), Asana (token, workspace+project ids, about M), Taiga (username/password, different API; not for the pilot).

## How
| Decision | Why |
|---|---|
| Key stored in the vault (purpose `integration`, owner `team:<id>:linear`) as a small JSON with the chosen team id; the Linear team's name goes in a non-secret `label` column | Key never returned, never logged; a blob for Slack or another team does not open as Linear. |
| Connect is two calls: `POST .../linear/teams` (key in, teams out, **key not stored**) then `PUT .../linear` with key and team id (the team must be one the key can reach) | The person picks a team without us guessing; the key is re-checked against Linear at the moment of storing. |
| Fixed host constant `api.linear.app`; calls go through the new `app/outbound.py` (resolve once, public addresses only, pinned IP + SNI, no redirects, timeouts, answer cut at 64 KB, httpx request log silenced). `app/slack.py` now uses it too | One implementation of the SSRF defences for both integrations. |
| Linear's own error text is never passed on: only fixed sentences (`auth`, `rate`, `rejected`, `timeout`, `unreachable`, `no_team`) | No leak of Linear's wording or of anything it echoes. |
| Sends the task's title, due date, and a description with the owner's name and a link to the meeting here | No transcript, quote or notes. Title and names go through `titles.mail_line` (web addresses removed, domains defanged). |
| Pushed only for meetings shared with the team, only at the first full extraction, one issue per task: `tracker_issues` claim row (unique `task_id`) before the call; deleted again only on a definite refusal (a timeout or unreachable keeps it, because Linear may still have made the issue) | Same visibility rule as the Slack finding; no duplicates across retries or two workers. |
| One-way and create-only: later edits, status changes and deleted tasks are **not** synced | The scope the spec calls "one-way push of tasks". |
| Caps per team: 15 issues per meeting, 60 issues per hour, 5 key checks per hour; advisory lock around count-then-insert | Rate caps; a capped task is skipped, never queued. |
| Only tasks still `todo` and untouched are pushed; the key is re-read after each claim (a disconnect or new key mid-meeting stops the push); a 30 s budget per meeting | Opus review fixes: no push with a deleted key, no worker held by a slow Linear. |
| A refused key (`auth`) stops further pushes until the key is replaced; the first failure of other kinds stops that meeting's remaining tasks | No hammering a dead key; failures are shown on the card. |
| User-facing validation errors are **400**, not 422 (also fixed for Slack) | `ApiError.isShowable` hides 404/405/422 as developer text; the screen would show "Something went wrong". |

## What's involved
`app/linear.py` (key check, GraphQL calls, issue text), `app/outbound.py`, `app/integrations.py` (`linear_teams`, `connect_linear`, `sync_tasks`, `after_extraction`), `app/api_integrations.py`, `app/models.py` (`TrackerIssue`, `Integration.label`, kind `linear`), `alembic/versions/0033_tracker.py` (down_revision 0032; adds `label`, widens the kind check, creates `tracker_issues`; re-point with 0032 when merging after 0027 to 0031 and update `test_0033_follows_0032_and_is_the_only_head`), `app/workspace.py` (calls `after_extraction`), web `settings-tracker.tsx`, `settings-planned-sections.tsx`, privacy page and template. No new `.env` variable and no nginx change; the server must reach `api.linear.app:443`.

## Verification
`tests/test_integrations_linear.py` (38 tests: key shape, pinned address, every answer mapped to a fixed reason, private DNS refused, no redirect followed, answer size cut, no key in logs/responses/ciphertext, roles, team isolation, wrong team, caps, push once per task, private meeting not pushed, failure removes the claim, refused key blocks, crash contained, text defanged), `test_migrations.py` (0033 upgrade, check constraint, downgrade keeps Slack), vitest `settings-tracker.test.tsx` against real captures. Live captures: `docs/fe-captures/integrations-w10/` 19 to 32 (Linear itself simulated and labelled). Parity shots: `docs/ui/parity/after/w10-tracker/`.

## Parity (1440, 768, 390; mock API)
| Element | Parity | Note |
|---|---|---|
| Row title, sub, tool select, Connect | Same | Jira, Asana and Taiga are listed but cannot be chosen (disabled options) |
| Connected: green "Syncing with Linear" pill + red Disconnect | Same shape | Adds the Linear team name |
| Key field, "Find teams", team select, Connect/Cancel | **Lead-designed, not in the designer's file** | The design connects in one click |
| "Only Linear is available for now..." line, failing pill and line, teammate note | **Lead-designed, not in the designer's file** | |
| Projects and sprints in the sub ("Send tasks, projects and sprints") | As drawn | Only tasks are sent; W4/W9 data is not |

Gaps for the designer: the key and team form, the failing state, the disabled tools, the teammate note.

## Operate / roll back
Revert; `alembic downgrade 0032` removes `tracker_issues` and Linear rows (issues already in Linear stay). A team disconnects itself in Settings (the key is deleted). Rotation of `TOKEN_ENC_KEY` is the vault's existing process (the re-encrypt script does not yet cover `integrations`, follow-up).

## Opus review (2026-11-06): no Critical
Fixed: HIGH gzip (the request now asks `Accept-Encoding: identity`, test reads a real un-pre-read stream), claim kept on ambiguous failures, row re-read after each claim, per-meeting time budget, only todo/untouched tasks pushed, stray `.bak` and probe files removed. Accepted: a claim left with no `external_id` after a crash is never pushed (fails safe, counts toward the hourly cap); one bad task (e.g. a refused due date) stops that meeting's remaining tasks.

## Follow-ups
Sync of status/edits back and forth, assignee mapping (needs email match), Jira/Asana/Taiga, per-task "Open in Linear" link on the board (the issue URL is stored), re-encrypt script coverage.
