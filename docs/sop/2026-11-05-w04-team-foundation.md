# SOP: W0.4 Team foundation

## What shipped
A team of one for every account, the column that can share a meeting, and the one function that decides who may read a meeting. Backend only: no Team screen (W3), no share switch (waits for the designer), no response shape changes. Branch `feature/w04-team-foundation`, one PR against `main` (not merged). Migration **0019**.

## Why
Tasks (W2), Team (W3), Projects (W4), Share (W6), Series (W8) and Sprints (W9) all need "who may read this meeting". Adding permissions after those exist means touching every query again. So the rule exists first, in one place, while nothing is shared yet (every meeting stays private).

## How
| Piece | Decision |
|---|---|
| Tables | `teams` (`id`, `name` "My team", `owner_user_id`, `timezone`, `created_at`) and `team_members` (`team_id`, `user_id` **unique**: one team per user, `role` owner/admin/member with a check, `aliases text[]`, `joined_at`). Sprint and reminder columns of spec section 4 are left for the phases that use them (W3, W8, W9): the spec gives no defaults and an additive migration costs nothing |
| `meetings.shared_team_id` | Nullable, `ON DELETE SET NULL`, partial index. Null = private, which is every row. Nothing writes it yet |
| The rule | `app/visibility.py`: `visible_meetings(user)` = own meetings OR `shared_team_id` = the caller's team (a subquery on `team_members`). `can_read_clause(user)` is the same condition for queries that join `meetings`. `owns(user, meeting)` for writes. A user with no team row (tests, half-made accounts) sees only their own: a null team never matches a null `shared_team_id`. `is_admin` is never consulted, so the platform admin has no route into team content |
| Reads vs writes | `services.visible_meeting_or_404` (reads) and `services.owned_meeting_or_404` (writes). The old `meeting_or_404` is gone, so a new route must choose. The owner-only version goes through the visible one first, so it can never be wider than a read, and answers **404** (not 403) to a teammate: the brief says writes stay owner-only, and a 403 would confirm the id |
| Creation paths | `accounts.add_user_defaults` calls `teams.ensure_team`. Password sign-up, Google sign-up and the capture scripts already go through it; the owner account made by migration 0006 is covered by the 0019 backfill. `ensure_team` is idempotent |
| Backfill | Plain SQL in the migration, two statements, writes only where a row is missing: a team for each not-deleted user with no membership and no team, then the owner membership for each team without one. Safe to re-run and mends a half-finished run |
| Account deletion | `teams.remove_users` (called from `deletion.purge_user` and `accounts.delete_unverified`): drops the memberships, deletes a team left empty, hands a team that still has members to the earliest-joined one (a purge must never be blocked; spec 4 says W3 refuses deleting an owner of a shared team until ownership is moved by hand) |
| Rejected | A `team_id` column on `users` (two sources of truth; the unique `team_members.user_id` is the single one). Filtering shared meetings in each route (the point is one function). Letting teammates translate a shared meeting (the brief keeps translate owner-only; spec 8.3 allows it later) |

## What's involved
`alembic/versions/0019_teams.py`, `app/models.py` (`Team`, `TeamMember`, `Meeting.shared_team_id`), `app/visibility.py` (new), `app/teams.py` (new), `app/services.py`, `app/api.py`, `app/api_translation.py`, `app/web.py`, `app/workspace.py`, `app/accounts.py`, `app/deletion.py`, `docs/openapi.json` and `web/lib/api/schema.d.ts` (two description lines only), tests `tests/test_team_visibility.py` (new), `tests/test_migrations.py`, `tests/test_signup.py`, `tests/test_google_signin.py`. `app/pipeline.py` is **not touched**.

### Every `Meeting.owner_user_id` filter, accounted for
| File:line | Before | After | Why |
|---|---|---|---|
| `services.py:361` `list_meetings` | `owner_user_id == user.id` | `visible_meetings(user)` | read |
| `services.py:373` `visible_meeting_or_404` (was `meeting_or_404`) | owner filter | `visible_meetings(user)` + id | read |
| `services.py:386` `owned_meeting_or_404` | (same function, owner filter) | `visible_meetings` then `owns()` | write |
| `api.py:321` `GET /meetings/{id}` | owner | visible | read |
| `api.py:371` `GET /meetings/{id}/export` | owner | visible | read (download) |
| `api.py:476, 496, 514, 531` cancel, end, retry, regenerate | owner | owned | write |
| `api_translation.py:89` request translation | owner | owned | write (spends money) |
| `api_translation.py:109` `GET .../translations/{lang}` | owner | visible | read |
| `api_translation.py:127` delete translation | owner | owned | write |
| `web.py:171` legacy meeting page | owner | visible | read; its buttons are owned |
| `web.py:183, 199, 215, 231` legacy regenerate, cancel, end, retry | owner | owned | write |
| `workspace.py:321` `list_decisions` | `owner_user_id == user.id` | `can_read_clause(user)` | read: decisions follow the meeting (spec 8.2) |
| `workspace.py:355` `hide_decision` | owner | **unchanged** | write; `hidden_at` is one flag for everybody |
| `workspace.py:369, 382, 394` review items | `ReviewItem.owner_user_id` | **unchanged** | owner only by spec 8.2 |
| `workspace.py:223, 281` `claim_next`, `claim_next_rewrite` | "not a deleted account" | **unchanged** | worker backlog, no caller |
| `worker.py:38` | same | **unchanged** | worker backlog |
| `translation.py:530` `daily_count` | `owner_user_id == user_id` | **unchanged** | the account's own spend, not a read |
| `translation.py:627` | deleted-account filter | **unchanged** | worker backlog |
| `usage.py:56` | `owner_user_id == user.id` | **unchanged** | the account's own hours cap |
| `placement.py:175, 184` | owner | **unchanged** | the account's own bot limit and "already in this meeting" |
| `join_runner.py:676, 693` | `join.user_id` | **unchanged** | "my bot is on this code": a teammate's shared meeting must not stop mine joining |
| `deletion.py:113, 135, 147` | owner | **unchanged** | purge deletes the owner's own rows |
| `pipeline.py`, `mail.py`, `mail_templates.py`, `gcal_sync.py` | `session.get(Meeting, id)` by id inside the worker | **unchanged** | no caller identity; the worker acts for the owner. `pipeline.py` untouched |

Open questions have no read route yet (W1 built only extraction); when one is added it joins `meetings` through `can_read_clause`. Calendar-linked lookups (`calendar_api`, `gcal_*`) read `calendar_events` and `scheduled_joins`, which are the owner's and never shared; none selects a meeting by user.

## Verification
- `tests/test_team_visibility.py`: private by default; shared reaches the team and nobody else; no-team account; platform admin gets nothing; a user who left sees nothing more; unique membership and role check; `remove_users`; **route enumeration**: every `/api` route with `{meeting_id}` must be classified read or owner-only (a new one fails until classified), then an outsider gets 404 on every one for a private and a shared meeting, a teammate gets 404 on every one for a private meeting, 200 on the three reads of a shared meeting (detail, export with and without transcript, translation), and 404 on all six writes, which also changes nothing; decisions, review items and hide follow the rules.
- `tests/test_migrations.py::test_0019_*`: backfill on a scratch DB (active and suspended get a team, deleted does not, the 0006 admin is covered), run again adds nothing, a missing membership is mended, nothing is shared, downgrade drops the tables and the column and keeps meetings, upgrade again. One head (`alembic heads` = `0019`).
- Sign-up and Google sign-up tests assert the new account has a team.
- Full pytest suite run once locally before the PR (shared code).

## Operate / roll back
- **Deploy:** the migration runs on deploy with the app. The backfill is two `INSERT ... SELECT` statements; at the pilot's size (a handful of accounts) it takes well under a second, and it is idempotent (re-running adds nothing).
- **Roll back:** `alembic downgrade 0018` drops `team_members`, `teams` and `meetings.shared_team_id` (nothing is shared, so no data is lost). The previous app version ignores the new tables. Rolling back the code without the schema is also safe: the old code never reads them.
- No new settings, no new routes, no response shape change, so no FE guide.

## Review
(filled in after the Opus review: see below)

## Follow-ups
- W3: invites, roles, the Team screen, the share switch that writes `shared_team_id`, and the rule that a calendar "all meetings" never publishes silently (read at share time).
- `MeetingDetail.template` and the list summary name the owner's template. Templates are owner-only (spec 8.2); once a meeting is shared, a teammate sees that one name. Decide with the designer whether to hide it for non-owners.
- `MeetingDetail` still carries `can_cancel`, `can_end`, `can_retry`, `can_regenerate` for a teammate, true or false by status though the routes refuse them. W3 should add `is_owner` and zero these.
- Spec 8.3: teammates translating or exporting a shared meeting cost the recorder nothing. Translate stays owner-only here; revisit in W3.
- `teams` sprint and reminder columns, `share_new_meetings` (spec 8.2) arrive with W3/W8/W9.
