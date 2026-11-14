# Multi-team, stage 2: team-owned meetings and Move, the share route's team, then the API (dark)

Stage 2 of `docs/superpowers/specs/2026-11-13-multi-team-design.md`, delivered as **two PRs, not
stacked** (both from `origin/main`):

- **2a, the permission core** (this document's first part): moved meetings, the Move and Move-back
  routes, the share route that takes a team, account deletion that keeps moved meetings, tasks that
  follow the meeting, migration `0044`.
- **2b, the API surface** (the second part below): `GET/POST /api/teams`, an explicit team on every
  team-implicit route, series, mail and integrations per team, the invitation caps, one auto-share
  team, migration `0045`. **2b's branch contains 2a's commits** (a merge of 2a's branch): migration
  `0045` has to follow `0044`, which only 2a adds, so 2b cannot be built on bare `origin/main`
  and keep one Alembic head. Merge 2a first; then 2b's diff is only its own.

Both ship **dark**: `MULTI_TEAM_ENABLED` is unset (off) everywhere and this change does not touch
`.env.example` or any deploy file. FE contract: `docs/fe-integration-guide-teams.md` (captured live
by `scripts/capture_teams_api.py`; captures in `docs/fe-captures/teams/`).

---

# Part 2a: the permission core

**What shipped.** A meeting can now be **moved** to a team (the team owns it). The recorder reads and
changes it only while a member of that team; the team's owner and admins change it too; it survives
the recorder leaving the team and deleting their account (shown as "Former member"). The share route
takes the team in the request. Tasks follow the meeting. PR: see the checklist (number added when
opened); migration `0044`.

## Why

Stage 1 made one person able to be in several teams, but a meeting was still only ever its recorder's
(`owns` was recorder-only, `can_read_clause` always let the recorder read, `deletion.py` deleted
every meeting the account recorded), and Share still went to the default team, which with two
ordinary teams is the personal team (200, shared with nobody). The two prerequisites of the stage-1
review, now done together with leak tests:

1. **Moved meetings** (spec 6.4, 9.1): the recorder reads and owns one only while in the owning team;
   owners and admins of the team change it; account deletion must not delete it.
2. **The share route** (spec 6.3): it takes a team, validates membership and that the team is not
   personal; the stage-1 test that pinned the bug was replaced.

## How, and what was decided on the way

| Piece | What it does | Decision / alternative rejected |
|---|---|---|
| Representation | `meetings.owner_team_id` (the column stage 1 added); `shared_team_id` is cleared on a move so the two never disagree | A flag was rejected in stage 1: it cannot say which team |
| `visibility.can_read_clause` | recorder reads their own meeting **only if no team owns it**; every member of the owning team reads it (through `team_read_clause`, which has no "live owner" guard for a team's meeting: it outlives its recorder) | The one rule stays one function |
| `visibility.owns(session, user, meeting)` | unmoved: recorder only. Moved: a member of the owning team who is the recorder, or its owner or admin. `recorded_by` is the stricter "recorded it AND may still change it", used for putting a meeting into one's own recurring series | The signature gained `session` (a role lookup); all 7 callers changed. Review items, vocabulary, templates stay the recorder's whatever the team may read |
| `app/meeting_teams.py` | share, unshare, move, move-back, in one module | |
| Move | `POST /api/meetings/{id}/move {team_id}`: recorder only, finished meeting only (`409 meeting_not_ready`), a team they belong to (`404` otherwise), not personal (`409 personal_team`), same team again is 200, another team `409 already_moved`, switch off `409 multi_team_disabled` | **My call: only a `ready` meeting.** Moving a meeting still being recorded would put the bot, the hours and the pipeline's owner mail in the team's hands mid-flight. **My call: a moved meeting changes team only via move-back** (the recorder must not pull it out of the team's history: spec 6.4) |
| Move back | `POST /api/meetings/{id}/move-back`: owner or admin of the owning team (`403 team_forbidden` for any other reader, `404` for non-readers), `409 not_moved`, `409 recorder_gone` | 403 not 404 for readers: they already know the id exists |
| Share | `POST /api/meetings/{id}/share` takes optional `{team_id}`; unshare is unchanged; both `409 meeting_team_owned` on a moved meeting | **My call:** no body = the default team **unless the person has two or more ordinary teams: `422 team_required`**, never the personal team. Today's frontend (no body, one team) is unchanged |
| Account deletion | `deletion.meetings_to_delete`: the account's unmoved meetings, plus meetings moved to a team that goes with the account. Moved meetings of a team that stays are **kept**; the account row is **erased but kept** (`deletion.erase_account_row`: name, email, password, Google id gone; every row that hung on it deleted or nulled exactly as the foreign keys would; status `deleted`, `users.purged_at` set so the worker does not purge it again). The detail shows `shared_by: "Former member"` | **Rejected: `meetings.owner_user_id` nullable with `ON DELETE SET NULL`.** It would put a null recorder into about 85 code paths that read the column. A hollow row keeps every one of them correct and is one helper. A template a kept meeting still names is kept and soft-deleted, not removed. The audio and the provider copy of a kept meeting are **kept** (the meeting is the team's); the privacy wording that says otherwise is the open gate below |
| Team deletion (spec 6.8) | A team is deleted today only when its last member's account is purged (`teams.remove_users`). **My choice:** a meeting moved to a team that is deleted goes **back to its recorder, private, if that account still exists** (the foreign key nulls `owner_team_id`), and is **deleted with the team** if its recorder's account is gone or being deleted | Spec 6.8's proposal, as the lead asked |
| Leaving | `teams.leave` no longer takes a leaver's **moved** meetings: they stay with the team with their tasks and series membership | Spec 6.8 |
| Tasks follow the meeting (spec 6.6) | `teams.team_for_meeting` (owning team, else shared team, else personal team; default team while the switch is off) is used by the extractor, and `teams.rehome_tasks` re-files the AI tasks on share, unshare, move and move-back: project, milestone, sprint and carry are cleared, an assignee who is not in the destination team is unassigned, and a date proposed from the meeting follows. Manually created tasks never move | **Re-homing runs only while `MULTI_TEAM_ENABLED` is on**, so flag-off behaviour is exactly today's (existing tasks are not re-homed, spec 6.6) |
| Display | `MeetingDetail` gains `team`, `owned_by_team`, `can_move`, `can_move_back`; `shared_by` is the recorder's name for everyone who is not the recorder (including an admin who may change it) | `can_share` is false on a moved meeting; `shared_with_team` stays true for it (documented trap) |

## What's involved

- Migration `alembic/versions/0044_users_purged_at.py` (additive nullable `users.purged_at`); models.
- `app/visibility.py`, `app/meeting_teams.py` (new), `app/teams.py` (`team_for_meeting`,
  `rehome_tasks`, `doomed_teams`, `ordinary_teams_of`; `leave`), `app/deletion.py`,
  `app/api.py` (share, unshare, move, move-back, detail), `app/api_schemas.py`, `app/services.py`,
  `app/workspace.py` (extractor team; decision hide), `app/api_workspace.py`, `app/api_tasks.py`,
  `app/series.py`, `app/ask.py`.
- Tests: `tests/test_multi_team_moved.py` (new, 42), `tests/test_multi_team_leaks.py` (the replaced
  share test), `tests/test_team_visibility.py` (route classification), `tests/test_migrations.py`
  (`0044`).
- Docs: `docs/fe-integration-guide-teams.md`, `docs/fe-captures/teams/`, the note in
  `docs/fe-integration-guide-team.md`, `docs/openapi.json`.

## Verification

Scratch Postgres 17 on this Mac; the project's own `ruff` (0.17.3): `ruff check .` and `ruff format
--check .` clean. Tests were written **before** the code (they failed on a missing `purged_at` and
`owns(session, ...)`).

- **Mutation check** (the old rule put back, one at a time, `test_multi_team_moved.py` plus
  `test_multi_team_leaks.py`): recorder always reads, 3 fail; `owns` recorder-only, 2 fail; account
  deletion deletes every meeting the account recorded, 2 fail; re-homing off, 3 fail; Share ignoring
  the request's team, 4 fail. Every mutation is caught.
- Scenario tests, all passing: Ada moves a meeting to X and leaves X: she cannot read, change or
  delete it, the team can; Ada deletes her account: the team keeps it, "Former member", no name or
  email, the worker does not purge the hollow row again, the email is free again; the last member
  deleting their account deletes the team and the moved meeting; the team's deletion returns a moved
  meeting to its live recorder.
- Bounded runs of existing files, on the final code unless noted: the multi-team files,
  `test_team_visibility`, `test_meeting_delete`, `test_team`, `test_api_tasks`, `test_series`,
  `test_ask` (356 tests; one failed first, `test_every_route_with_a_meeting_id_is_classified`, because
  the new routes were not classified yet; fixed, rerun 81 passed); `test_admin`, `test_workspace`,
  `test_tasks`, `test_shares`, `test_api`, `test_accounts`, `test_topics`, `test_suggested_tasks`,
  `test_series_mail` (583 tests; one failed first, `test_every_api_route_with_an_id_returns_404...`,
  which needed a body for `/move`; fixed, rerun passed); `test_api_translation`, `test_api_workspace`,
  `test_decisions_record`, `test_integrations`, `test_projects_api`, `test_sprints_api`,
  `test_suggested_tasks_api`, `test_titles` (262 passed); `test_migrations -k "0044 or single_head or
  matches_models"` (4 passed).
- Live: `scripts/capture_teams_api.py` ran the whole journey against the real app over HTTP and the
  FE guide is built from those captures.
- **Not run**: the full suite, a shard, e2e, coverage (CI does). **Not proven**: nothing ran against
  the server, real mail, Slack or Linear, or production rows; the purge was called directly in the
  capture script (the worker route is unchanged), not through the admin API.

## Operate / roll back

- Deploy: `alembic upgrade head` runs `0044` (one nullable column). Nothing changes while the switch
  is off, except that `POST /api/meetings/{id}/share` now also accepts a team and `MeetingDetail`
  carries four new fields (additive).
- **Roll back: before the first Move, revert the code** (the column and the constraint are harmless;
  `alembic downgrade 0043` drops them). **After the first Move, do NOT revert the code or downgrade:**
  the old `purge_deleted` has no `purged_at` filter, so it would purge every hollow account row again
  and delete the moved meetings that point at it with them, and the old `can_read_clause`
  (`owner_user_id == user.id OR ...`) would let former recorders read moved meetings again. The real
  rollback after the first Move is to **keep this code and switch the flag off** (`MULTI_TEAM_ENABLED`
  unset): Move, creating a team and a second membership are refused, and everything already moved
  stays the team's. With the switch off, moving a meeting back still works but does not re-file its
  tasks (follow-up 10 below).
- **Do not turn on `MULTI_TEAM_ENABLED`** before stage 3 and the privacy wording below.

## Review fixes (Opus review of #182, head 47d73e2)

Tests written first (`tests/test_multi_team_review_fixes.py`, 13), then the fixes:

1. **Review screen** (`app/workspace.py` `open_review_count`, `list_review`, `review_item`) now reads
   through `can_read_clause`: a recorder who left the team has none of the moved meeting's title or
   "Please check" bullets, and a rewrite writes no review items for a recorder who cannot read it.
2. **Extraction race:** `run_extraction` re-reads and locks the meeting's team columns (`session.
   refresh(..., with_for_update=True)`; sessions do not expire on commit) before it files tasks and
   milestones and before the Slack/Linear post, so a move or unshare during the model call is seen.
3. **Email shares** (`POST/GET /api/meetings/{id}/shares`) are the recorder's: `services.
   recorded_meeting_or_404`; a team admin gets 404 (no share history, no share silently dropped as
   `sender_changed`). The design of "a team admin mails the notes" is for later.
4. **Team deleted with a moved meeting:** the meeting goes back to its live recorder with its tasks and
   date proposals re-filed in their personal team (`deletion._give_back_meetings_of_doomed_teams`,
   always, whatever the switch says), instead of cascading away with the team.
5. **Race between share and move:** each of share, unshare, move and move back takes the meeting's row
   (`SELECT ... FOR UPDATE`) first, and migration `0044` adds `ck_meetings_team_xor`: `owner_team_id is
   null or shared_team_id is null` (a meeting is private, shared with a team or owned by one, never
   owned by one team and shared with another). Tested with two sessions.
6. `tasks.owns_meeting_of` uses `visibility.owns`, like the `can_decide` flag.
7. Notifications: `unread_count` and `list_notifications` hide a notification that names a meeting
   the person can no longer read (old and new ones), and the recipient-cap notice is not written for
   a recorder who left. The stale `Meeting.owner_team_id` comment was fixed.

Also tested: an erased account's linked rows (calendar connection, notifications, settings) are gone
and an integration it connected is nulled.

## Follow-ups and known gaps

- **8. (2b fixes it)** A moved meeting whose recorder deleted their account is still treated as "being
  deleted" by the worker claim, translation (`app/translation.py` `_claim`), extraction and the topics
  query, because the hollow row stays `status = 'deleted'`: translations of it stay `translating` and
  a rewrite never refreshes its tasks. The filters become "owner deleted AND not team-owned".
- **9.** Translation quota: `daily_count` counts the caller's own meetings' queued translations plus
  their ledger rows, but the ledger row is written under the meeting's recorder, so a team admin's
  translations of moved meetings eat the recorder's daily allowance and never the admin's. Decide at
  the plans design (hours and quotas per team); recorded.
- **10.** `move_back` has no switch check and `rehome_tasks` does nothing while the switch is off: after
  an on-then-off rollback an admin's move-back leaves the tasks in the team (hidden by the meeting
  rule, but an assignee still sees them). Re-file always on move back, or refuse while off.
- **11.** Two checks disagree on "personal": `_member_team` refuses any team flagged personal,
  `ordinary_teams_of` counts a legacy flagged team that someone else owns as ordinary. Only legacy
  hand-built fixtures can differ.
- **12. For his lawyer (the privacy wording gate before the switch-on):** deleting an account keeps,
  for a meeting moved to a team: the meeting (notes, transcript, audio and provider copy), the title,
  the meeting link (`url`, which can be the person's personal meeting room), and the text of any
  notes template the meeting was written with (kept, soft-deleted, owned by the hollow row, because a
  meeting must name its template). The person's name, email and credentials are gone.
- **Privacy policy and terms** still promise that deleting an account deletes the person's meetings.
  A moved meeting stays with its team (spec 9.1). His lawyer must read the changed wording before the
  switch-on; this PR does not touch those pages.
- The audio and the Vexa/Skribby copy of a kept moved meeting are kept by `purge_user` (the meeting is
  the team's). A team deleting such a meeting later goes through the normal delete route, which
  removes them.
- Series, mail and integrations for moved meetings, and the explicit team on the other routes, are
  stage 2b.

---

# Part 2b: the API surface

**What shipped.** The teams list and team creation; an explicit team per request on the
team-implicit routes (the `X-Team-Id` header); series read through their own team and listed and
created per team; a cap on invitations per person across teams; one reminder mail per person per
occurrence; one auto-share team per person. PR: see the checklist. Migration `0045`.

## The series sizing (done before building it)

The spec called series "the riskiest" piece. Sized by reading `app/series.py`, `app/api_series.py`,
`app/series_mail.py` and `app/topics.py` before any change:

- A series has **one fixed `team_id`**, made in its recorder's default team when the calendar links
  it (`link_from_calendar`, `sync_from_joins`: unchanged on purpose, spec 6.9).
- Only **three things** needed a team: the list (`list_for`), hand-made creation (`create`), and the
  by-id lookup (`get`). `get` asked "is it my DEFAULT team's"; it now asks "is it a team I am in" (the
  series' own team), which makes every by-id route and `of_meeting` safe with **no header**: about 3
  functions in `series.py`, 2 routes in `api_series.py` and one call in `api_projects.py`.
- `topics.py` and the agenda sends work by `series.team_id` already; `series_mail` checks membership of
  the series' team. The two risks were the **reminders** (one person, two teams, the same recurring
  event, one series per team: two mails) and **what a series page shows** (an occurrence shared to a
  different team). Both are covered below by tests.
- Size: about 10 call sites and one new function, not a rewrite. Done in this PR.

## How, and what was decided on the way

| Piece | What it does | Decision / alternative rejected |
|---|---|---|
| Teams list | `GET /api/teams`: id, name, role, member count, `is_personal`, `is_default`, `can_manage`; `can_create_more`. Makes the personal team if missing (`ensure_personal_team`) | `is_default` is the compatibility rule made visible (`teams.default_membership`) so the switcher can mark it |
| Create | `POST /api/teams {name}`: the caller owns it; at most `teams.MAX_OWNED_TEAMS` = 3 owned non-personal teams (`409 team_limit`), checked in the route; `409 multi_team_disabled` while the switch is off; `422 invalid_name` | A database constraint was rejected (spec: later a plan setting) |
| Explicit team | The **`X-Team-Id` header** (`team_admin.requested_team`, one dependency; `team_admin.my_membership(session, user, team_id)`). On `/api/team*`, `/api/tasks` (list, create), `/api/projects*`, `/api/milestones*`, `/api/sprints*`, `/api/series` (list, create), `/api/integrations/*`. A team the caller is not in is `404 not_found` | **Header, not path or query:** one place per route, nothing in the URL to cache or log differently, and the frontend sets it from one interceptor. **Rejected: a server-side "current team"** (two tabs would fight). A path prefix would have changed ~45 URLs |
| No header | The default team, exactly as stage 1 (`teams.default_membership`): today's frontend and one-team users are unchanged | `GET /api/tasks` with no header still lists every team's tasks, as before |
| Series by id | `series.get`: a series of a team the caller is in AND visible (`_seen_by`) | Stage 1's "personal team's series, 404 otherwise" is gone; its test was rewritten |
| Reminders | `series_mail._already_reminded`: once a person was reminded for a recurring event's occurrence by one series, a series of another team for the same event stays quiet | Spec 6.9: one mail per person per meeting. A per-person key on `series_reminders` was rejected (a migration for a rule a query can hold) |
| Invitation caps | `team_invites._check_caps` also counts the inviter's invitations over **all** teams: 20 in an hour, 20 in a day (revoked included) | **My call: both equal the per-team daily cap**, so a person with one team (and everyone while the switch is off) never hits them first: no flag-off change. With three teams they are held to one team's allowance, not three |
| Auto-share team | `user_settings.auto_share_team_id` (migration `0045`, nullable, `ON DELETE SET NULL`), `GET/PUT /api/settings/auto-share`. `teams.apply_share_default` shares to the chosen team (any role in it: it is their own choice for their own meeting), else the team switch for owners and admins as before. A team they left reads as none | Reusing `teams.share_new_meetings` as a per-person flag was rejected: it is a team-wide setting for owners and admins |
| Integrations | Already by team id; a meeting's post goes to the team it is shared or moved to because the extractor files by `teams.team_for_meeting` (2a) and `notify_meeting` posts only for `in_team`. The routes take the header | Posts already made are not recalled when a meeting is unshared, moved or re-homed (a Slack post cannot be unsent). The FE guide says to tell the person |

## What's involved

- Migration `alembic/versions/0045_auto_share_team.py`; `app/models.py` (`UserSettings`).
- `app/api_teams.py` (new), `app/team_admin.py` (`requested_team`), `app/api_team.py`,
  `app/api_projects.py`, `app/api_sprints.py`, `app/api_integrations.py`, `app/api_series.py`,
  `app/api_tasks.py`, `app/api_settings.py`, `app/api_schemas.py`, `app/api.py`, `app/series.py`,
  `app/series_mail.py`, `app/tasks.py`, `app/team_invites.py`, `app/teams.py`.
- Tests: `tests/test_multi_team_api.py` (new, 26), `tests/test_series_mail.py` (the two-team reminder
  test), `tests/test_multi_team_derived.py` (the series test rewritten), `tests/test_team.py` (route
  classification, `is_personal`), `tests/test_migrations.py` (`0045`).
- Docs: `docs/fe-integration-guide-teams.md` (sections 9 to 14), `docs/fe-captures/teams/46..70`, the
  OpenAPI file.

## Verification

Scratch Postgres 17 on this Mac; the project's own `ruff`: `ruff check .` and `ruff format --check .`
clean; `scripts/export_openapi.py --check` clean.

- **Mutation check** (one at a time, the old behaviour put back): the header ignored, 4 tests fail;
  series by id through the default team only, 4 fail; no per-person invitation cap, 2 fail; no
  cross-team reminder dedupe, 1 fails (the first version of that test did not fail: it had no
  recipient in the twin series; fixed, then it caught the mutation); the tasks board ignoring the
  team, 1 fails; no limit on owned teams, 3 fail.
- Bounded runs of the touched areas: see the PR body for the exact counts (the multi-team files, the
  team, series, series mail, tasks, projects, sprints, integrations, accounts and API files, and the
  migrations `0043` to `0045`). One run failed first on seven tests: a route called as a plain function
  without the new argument, the new routes not classified in the route-enumerating test, the new
  `is_personal` field in an exact-dict test, and the per-person hourly cap tighter than today's
  per-team daily one (set equal to it); all fixed.
- Live: the capture script ran the journey over HTTP; the guide is built from the captures.
- **Not run**: the full suite, shards, e2e, coverage (CI does). **Not proven**: nothing ran against
  the server, real mail, Slack or Linear. The per-person invitation caps, the reminder dedupe, the
  header on the projects, sprints and integrations routes and the effect of the auto-share choice on a
  new meeting are tested but not in the live captures.

## Operate / roll back

- Deploy: `alembic upgrade head` runs `0045` (one nullable column). Nothing changes while the switch
  is off, apart from the additive header, the new routes and fields; `POST /api/teams` is refused with
  `multi_team_disabled`.
- Roll back: revert the code; the column is harmless; `alembic downgrade 0044` drops it.
- Still do not turn on `MULTI_TEAM_ENABLED` before stage 3 and the privacy wording.

## Follow-ups

- The frontend must send `X-Team-Id` from the switcher (stage 3); `web/lib/api/schema.d.ts` is not
  regenerated here (the frontend is stage 3's).
- There is no route to delete a team or hand one on (the pilot admin does the hand-over by script); the three-owned-teams cap therefore never frees up by itself.
