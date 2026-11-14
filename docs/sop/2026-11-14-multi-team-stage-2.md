# Multi-team, stage 2: team-owned meetings and Move, the share route's team, then the API (dark)

Stage 2 of `docs/superpowers/specs/2026-11-13-multi-team-design.md`, delivered as **two PRs, not
stacked** (both from `origin/main`):

- **2a, the permission core** (this document's first part): moved meetings, the Move and Move-back
  routes, the share route that takes a team, account deletion that keeps moved meetings, tasks that
  follow the meeting, migration `0044`.
- **2b, the API surface** (second part, added by the 2b PR): `GET/POST /api/teams`, an explicit team
  on every team-implicit route, series, mail and integrations per team, the invitation limit, one
  auto-share team.

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
- Roll back: revert the code; the column is harmless. `alembic downgrade 0043` drops the column; any
  erased account row (status `deleted`, `purged_at` set) then looks like a deleted account waiting for
  its purge, and the **old** purge would delete the moved meetings that point at it. Revert the code
  first and expect that; with the switch never turned on there are no such rows.
- **Do not turn on `MULTI_TEAM_ENABLED`** before stage 3 and the privacy wording below.

## Follow-ups and known gaps

- **Privacy policy and terms** still promise that deleting an account deletes the person's meetings.
  A moved meeting stays with its team (spec 9.1). His lawyer must read the changed wording before the
  switch-on; this PR does not touch those pages.
- A moved meeting whose recorder deleted their account is skipped by the worker jobs that guard on
  "the owner is deleted" (re-extraction, translation) and by the Keeps-coming-up topics query
  (`app/topics.py`): that is deliberate (no more spend, no recorder), but it means such a meeting is
  not re-extracted or retranslated. Revisit if teams ask.
- The audio and Vexa/Skribby copy of a kept moved meeting are kept by `purge_user` (the meeting is the
  team's). A team deleting such a meeting later goes through the normal delete route, which removes
  them.
- Series, mail and integrations for moved meetings, and the explicit team on the other routes, are
  stage 2b.
