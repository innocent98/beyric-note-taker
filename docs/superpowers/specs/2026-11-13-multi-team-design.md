# Multi-team design: one person, a personal space and several teams

Status: DRAFT for Adebayo's approval (2026-11-13). Nothing in this document is built. Facts about today's code were read from `origin/main` on 2026-11-13 (paths and lines below); the rest is design. "His call" means Adebayo said it in chat; "my call" means the lead proposes it and he may overturn it.

## 1. Intent and why

Today every account has exactly one team, and joining another team dissolves the personal one (the database enforces `team_members.user_id` unique, `app/models.py:425-427`). A consultant, a freelancer or anyone who works with two companies cannot be in both teams, and a person who joins a team loses their personal space. Adebayo's wish: a person belongs to **several teams at once**, keeps a **personal space that is never swallowed**, stays in charge of their own meetings, and can create teams themselves. Later, subscription and billing attach to a team (this document does not design billing).

## 2. Goals, non-goals, anti-goals

Goals
- A person has one permanent personal space and any number of team memberships, with a role in each (owner, admin, member).
- Anyone can create teams and invite people into them; an invitation belongs to a team, never to a person.
- A meeting starts as the recorder's and private. The recorder can **share** it to one team (the recorder stays the owner) or **move** it to one team (the team becomes the owner).
- Team screens (tasks board, projects, sprints, team page, team-wide settings, integrations) work on one chosen team at a time, with a switcher.
- No meeting, task or note of one team is ever readable by someone who is only in another team.

Non-goals (this design)
- Billing, plans, seats, per-team hours (a later design; this one only keeps `Team` a clean place to attach them).
- Choosing the team of a meeting automatically from the calendar or attendees (his choice: personal first, then share).
- Sharing one meeting with several teams at once; transferring a team's ownership inside the app; guest access.

Anti-goals (a failure even if it technically works)
- A leak across teams, or a second visibility rule scattered over the code.
- Silently changing what happens to people's existing data when the migration runs.
- Reversing the privacy promise "meetings are private unless the recorder shares them" without his decision and his lawyer's.

## 3. Constraints

- The one visibility rule stays one function (`app/visibility.py`): "Nothing else compares `shared_team_id`" is an existing invariant; keep it.
- Reading is not writing: the owner-only writes (`owns`) keep their meaning; this design adds a defined write rule for team-owned meetings (section 6.4).
- Privacy text (privacy and terms pages) says meetings are private unless shared; a moved meeting needs a wording check by his lawyer before it ships (section 9, "Theirs").
- Migrations: Alembic, one head, upgrade and downgrade round trip tested on a scratch DB; the schema change is staged so the old frontend keeps working during rollout (section 7).
- Frontend contract: every changed or new payload gets a captured, true integration guide (`docs/fe-integration-guide-*.md`), per the project rule.
- CI rules as everywhere: the full suite runs on CI only; local runs are lint, type-check and the tests of touched files.

## 4. Approaches considered

| | Idea | Trade-off |
|---|---|---|
| **A. Many memberships, personal team stays** | Remove the one-team rule; keep each person's personal team forever; a meeting is shared to one team. | Smallest change; keeps today's privacy promise; the API and the team screens gain a "which team" notion. |
| **B. Full workspaces** | Every item belongs to a workspace; a meeting is its workspace's from the start. | Cleanest for billing and company-kept history; reverses the privacy default, touches every table, higher leak risk. |
| **C. One team plus guest access** | Keep one membership, add read-only guest access. | Cheapest; does not give membership of several teams. |

**His choice: A, plus a "move to team" action** (the hybrid he named: personal first, with moving as a deliberate action). B stays possible later because billing and ownership attach to `Team`. C does not meet the goal.

## 5. Decisions already taken (his words)

- Approach A plus the move action.
- A meeting starts personal and private; the recorder decides to share or move it ("Personal, then share").
- A person may create teams themselves; invitations are per team; the chain "A invites B, B invites C, C invites A and B" is just memberships.

## 6. The design

### 6.1 Personal team and memberships
- Add `teams.is_personal` (bool). Every person has exactly one personal team (the "team of one" that exists today), created at sign-up as now (`teams.ensure_team`, `accounts.py:56`). It is never dissolved, never left, and nobody is invited into it; its owner is its only member and cannot be removed.
- Drop `UNIQUE(team_members.user_id)`; add `UNIQUE(team_id, user_id)`. A person has one membership per team and a role in each. Index `team_members(user_id)`.
- Teams a person creates are ordinary teams (`is_personal = false`), the creator is `owner`. **My call:** at most 3 owned non-personal teams per account (a named constant, later moved to the plan).
- Roles stay per team: `owner`, `admin`, `member`. Who may invite and manage is as today (`team_admin.require_manager`).

### 6.2 Invitations
- Accepting an invitation with an existing account **adds a membership**; it no longer dissolves the personal team. `teams.join` stops calling `move_team_content` and stops raising `already_on_team` (`teams.py:109-155`). Joining the same team twice stays refused.
- A new address still signs up through the invitation (sign-up closed or full is bypassed for it, `signup.py`); at confirmation the new account gets its personal team and the membership in the inviting team.
- Per-team hourly invitation limits stay, and a **per-person hourly limit across all their teams** is added (my call): with up to three owned teams the per-team limit alone would triple what one person can send, and invitations bypass closed or full sign-up. Creating a team does not count against the pilot's account cap; pending invitations still do, as today (`signup.slots_taken`).

### 6.3 Sharing
- `meetings.shared_team_id` stays a single nullable foreign key: one team per meeting. The share route validates that the recorder is a member of the target team and the team is not personal; it takes the team in the request (today it uses `ensure_team`, `api.py:582-583`).
- "Share new meetings automatically" (`teams.share_new_meetings`, `apply_share_default`) becomes a **per-person setting for at most one team** (my call): the recorder picks which of their teams, if any, new meetings go to. It is applied once when the notes are first written, as now (`pipeline.py:1323`), never when the bot joins.
- Unsharing sets `shared_team_id` to null as now.

### 6.4 Moving a meeting to a team (the new action)
- **Moved = the team owns it.** Representation (builder's choice, constraints below): either `meetings.owner_team_id` or a `team_owned` flag together with `shared_team_id`. The recorder stays recorded as `owner_user_id` (attribution, hours, calendar link) but is no longer the only owner.
- Read: every member of the owning team, plus the recorder while they are still a member. If the recorder leaves the team they lose access unless they are a member again.
- Write (rename, rewrite, translate, cancel, retry, delete, share changes): the recorder **and** the team's owner and admins (my call). A plain member who is not the recorder cannot change it.
- The meeting survives the recorder leaving the team **and deleting their account** (today account deletion deletes all their meetings, `deletion.py:172-179`; for team-owned meetings this must change; see section 9, "Theirs").
- Moving is reversible by the team's owner or admin ("move back to the recorder"); the recorder cannot pull a moved meeting back on their own (my call; it protects the team's history).
- Hours are still charged to the recorder's cap (my call; billing later).

### 6.5 One visibility rule
`can_read_clause` becomes "own, or shared to or owned by a team the caller belongs to": `shared_team_id IN (my team ids)` or `owner_team_id IN (my team ids)`, plus the existing "live owner" guard. Every derived read (notes, transcript, translations, exports, decisions, open questions, tasks, search, Ask, mail "Open notes" button) must go through it. `tasks.visible()` (`tasks.py:333`), `shares._team_members_by_email` (`shares.py:87`) and `mail_templates.py:591,613,791-795` stop using a single "their team".

### 6.6 Tasks follow the meeting
Tasks extracted from a meeting belong to the meeting's team when it is shared or team-owned, otherwise to the recorder's personal team (today `workspace.py:272,338` always uses the owner's one team). Sharing, unsharing and moving re-home the meeting's extracted tasks accordingly. Manually created tasks belong to the active team. Existing tasks are **not** re-homed by the migration (my call): tasks that sit today on a multi-member team because of a private meeting stay where they are, hidden by the meeting-readability check in `tasks.visible()`, which keeps that legacy path; changing what teammates and boards show today would break the anti-goal "no silent change". The new rule applies from the release on. No task may be visible to a team that cannot read its meeting. The exact handling of tasks already assigned or carried into sprints when a meeting is unshared is the builder's to settle (follow today's rules for unsharing).

### 6.7 The active team in the API and the screens
- New `GET /api/teams` (my memberships with role, member count, is_personal) and `POST /api/teams` (create). Existing team-implicit routes (`/api/team*`, `/api/tasks`, `/api/projects*`, `/api/milestones*`, `/api/sprints*`, `/api/series*`, `/api/integrations/*`, `/api/settings/reminders`, `/api/meetings/{id}/share`) take an explicit team per request (path, header or query: builder's choice, but explicit per request, never a server-side "current team" that two browser tabs would fight over).
- When no team is given, an old client gets its **default team**: its single non-personal team if it has exactly one (what it sees today), else the personal team. This keeps today's frontend and today's users working through the rollout.
- Frontend: a team switcher in the shell (the app already has a sidebar and a top bar on phones); the Team, Tasks, Projects, Sprints, Settings (team section) and integrations screens work on the chosen team; the meeting page's share switch becomes a team choice plus a Move action. The designer's files in `docs/ui/` are the source: where they have nothing for the switcher or the Move action, it is lead-designed at his request and must be marked so.

### 6.8 Leaving, removing, deleting
- Leaving or being removed from a team: no new "team of one" is created (they already have a personal team: `teams.py:174,234` change). Meetings the person recorded and **shared** to that team are unshared (as today); meetings **moved** to it stay with the team; their open assigned tasks in that team are unassigned; invitations they sent are revoked. The owner still cannot leave while others remain.
- Deleting a team: the owner may delete a team they own that has no other members; its tasks, projects, sprints, integrations and invitations cascade as today; meetings shared to it become private again; meetings moved to it need a defined fate (builder proposes: back to the recorder if still an account, else deleted with the team; this is also under "Theirs").
- Account deletion: as today for the person's own meetings, except team-owned ones (6.4); `teams.remove_users` (`teams.py:37-57`) is made to work per team.

### 6.9 Series, mail, integrations (my calls; the series part is the riskiest)
- **Series (recurring meetings).** A series stays keyed as today (`team_id` plus `calendar_recurring_id`, owned by one person, `series.py`) and is created in the recorder's default team (their personal team unless they have exactly one non-personal team and no explicit choice). The recurring page, "Keeps coming up" topics, the suggested agenda, series mail and Ask show only occurrences the viewer can read through the one visibility rule; an occurrence shared to a team other than the series' team is simply not part of that team's series view. The builder maps the exact behaviour and sizes it first, because leak tests for series, topics, agenda and Ask depend on it.
- **Mail.** One mail per person per meeting, never one per team. The "Open notes" button appears when the meeting is readable by the recipient through the rule. For a moved meeting whose recorder has left, no notes mail goes to the recorder; team owners and admins are not mailed automatically. Reminder settings (`teams.reminders_on`, `reminder_timing`) stay per team, managed by that team's owners and admins.
- **Integrations (Slack, Linear).** They belong to a team. A meeting's post and task export go to the integration of the team the meeting is shared or moved to; the personal team has no integrations in this release. Posts already made are not recalled when a meeting is unshared, moved or re-homed (a Slack post cannot be unsent): say so in the SOP.

## 7. Migration and rollout

Staged so nothing flag-days. **Order changed 2026-11-13 at Adebayo's request (his call: "go with A plus move"): the team-owned meeting and the Move action come in stage 2, not last**, because a company keeps its meeting history when someone leaves only if the team can own meetings.
1. **Schema and rules (dark):** add `teams.is_personal` with a partial unique index (one personal team per owner). Backfill rule (my call): a team with exactly one member and **no pending invitations, no integrations, no projects, sprints or milestones** is that person's personal team; a solo team that has any of those stays a normal team (its owner also gets a separate new personal team, and the old team's content stays where it is). **Important backfill:** people who today sit in a team with others have no personal team (it was dissolved when they joined): create one for each of them. Add the ownership column or flag for moved meetings (unused until stage 2). Add `UNIQUE(team_id, user_id)`, then drop the old unique index on `user_id`. Rewrite the one visibility rule, the teams module (`join` no longer dissolves, `leave` creates no new team of one), the lookups in section 8, the default-team compatibility rule, and the setting `MULTI_TEAM_ENABLED` (default off). Downgrade is only defined while nobody has two memberships; the real roll-back is to revert the code and leave the schema.
2. **API, team-owned meetings and Move (dark):** `GET /api/teams`, `POST /api/teams`, an explicit team on every team-implicit route, and the moved-meeting rules of 6.4 with the Move route, tasks following the meeting (6.6), series, mail and integrations (6.9). **Gated by the two "Theirs" decisions in section 9.**
3. **Frontend:** the switcher, the teams list, create team, the share-or-move control.
4. **Switch on:** `MULTI_TEAM_ENABLED` goes on only after stage 3 is live.
Each stage is its own PR (or small group), CI green, deployed one at a time. **`MULTI_TEAM_ENABLED`, default off (my call), gates joining a second team and creating a team** (a second membership is refused while it is off): stages 1 and 2 ship dark, so no one with an old client ever lands on a screen that does not understand two teams.

## 8. What is involved (from a read-only inventory of `origin/main`, 2026-11-13)

Breaks or is wrong with two memberships (all keyed on `TeamMember.user_id == user`): `teams.py` (`team_of :14`, `ensure_team :22`, `membership :73`, `apply_share_default :239`), `visibility.py:25`, `tasks.py` (`visible :333`, `membership :347`, `permissions :365`), `team_admin.py:40 my_membership` (the base of about 45 route call sites in `api_team`, `api_projects`, `api_sprints`, `api_integrations`), `shares.py:87`, `series.py` (`team_id_of :89`, `membership :92`, `is_manager :96`, uses at 123, 133, 219, 282, 308, 692), `series_mail.py:197,286`, `mail_templates.py:591,613,791-795`, `workspace.py:272,338`, `api.py:582-583`, `api_tasks.py:77,155,429-431`. Not affected (they work by team id): `topics.py`, `filing.py`, `sprints.py`, `projects.py`, `integrations.py`, `mail_summary.py` (the inventory did not read the last two line by line).

Join and leave today: `POST /api/team/invites/accept` (`api_team.py:392-420`) to `teams.join`, which dissolves the personal team and moves its tasks, projects, milestones and series (`move_team_content`, `teams.py:84-106`); sprints are not moved; integrations are lost with the old team by cascade; meetings stay but shared ones become private. Leave and remove: `team_admin.py:158-170` to `teams.leave` (`teams.py:158-236`).

Data: `team_id` on tasks, projects, sprints, milestones, series, integrations, integration_posts, tracker_issues, team_invites, team_members; `shared_team_id` on meetings; per-person only: templates, vocabulary, calendar connections, review items, user settings, hours cap (`users.hours_cap_seconds`).

Frontend: `web/lib/api/client.ts:393-404` (the `team` namespace), `web/lib/api/schema.d.ts`, the pages and components that call `api.team.*` (`meetings/[id]`, `projects`, `settings`, `setup`, `tasks`, `team` pages; `settings-reminders`, `settings-sprints`, `team-invite-dialog`, `team-view`, `accept-invite`, `setup-workspace`, `signup-flow`), and `web/scripts/mock-api.mjs`. Existing tests delete from `team_members` by user and assume one row.

## 9. Open items

"Theirs" (only Adebayo can decide; both gate stage 2, the move action, so stage 1 can be built now):
1. **Account deletion and moved meetings.** A moved meeting holds other people's words and belongs to the team; if the recorder deletes their account, do their moved meetings stay with the team (the record stays, the recorder's name is removed) or go with them? Staying needs a privacy-policy and terms change his lawyer reads (the promise today is that deleting an account deletes the person's meetings).
2. **Who may move.** Any recorder moves their meeting into any team they belong to (my call), or only owners and admins of the team accept a moved meeting?

My calls to check (all marked "my call" above): three owned teams per account (checked in the route, not by a database constraint); one auto-share team per person; moved meetings writable by the recorder plus team owners and admins; the recorder cannot pull a moved meeting back; hours charged to the recorder; explicit team per request with a default-team rule for old clients; `MULTI_TEAM_ENABLED` off until the frontend is live; existing tasks not re-homed; the backfill rule for personal teams; a per-person invitation limit; one mail per person per meeting; no integrations on the personal team; posts not recalled.

Left to the builder: exact column or flag for moved meetings; request shape for the team (path, header or query); the order of PRs inside the stages; handling of tasks already in sprints when a meeting is re-homed; the exact series behaviour in 6.9 (size it first); the switcher's design inside the existing shell; test structure (the leak tests must cover: a person in teams X and Y sees a meeting shared to X only through X membership; a person only in Y never sees it; moved meetings survive the recorder leaving; unsharing and moving re-home tasks without exposing them; series, topics, agenda and Ask).

## 10. Verification the build must show

Leak tests as above for meetings, derived data (notes, transcript, translations, exports, decisions, open questions, tasks, search, Ask) and the mail "Open notes" button; migration up and down on a scratch DB with both a one-member team, a many-member team and a person with a swallowed personal team; the old frontend (no team given) unchanged for a person with one team; captured true payloads for every new or changed endpoint in a new `docs/fe-integration-guide-teams.md`; an SOP per shipped step and the checklist updated.

## 11. Benchmark: how competitors model teams (read 2026-11-13)

Read at Adebayo's request, from the vendors' own pages where they opened. The fetch tool summarises a page; read the pages themselves before quoting any line. Pages that refused to open (HTTP 403) are marked.

| Vendor | What the pages say | Page |
|---|---|---|
| Fireflies | The person who recorded a meeting is its owner (its "My Meetings" holds meetings you hosted or recorded). Privacy per meeting: public link, workspace and participants, teammates only, participants only, owner only; each person sets their own default (the default is "teammates and anyone with the link"); applying a new default to past meetings is optional. Admins can edit summaries, transcripts and speaker labels, regenerate and download for meetings they can access, and move publicly accessible meetings to a channel; they find a private meeting only if they were a participant or are in its private channel; only the meeting owner can delete. Not covered: what happens when a member leaves, and how several teams are handled. | `guide.fireflies.ai/articles/6920296475-learn-about-post-meeting-admin-controls-for-team-meetings`, `guide.fireflies.ai/articles/5319481450-how-to-configure-privacy-settings-for-your-meeting-recaps` |
| Otter | Search summaries of its help centre (the pages themselves returned 403): removing a member turns them into an individual account that keeps what they created, and what they shared stays with collaborators; deactivating keeps all data and is the recommended way; deleting is permanent; for Enterprise, a removed or deleted user's conversations are no longer discoverable by admins (deactivate to keep them); ownership transfers only to an existing admin. | `help.otter.ai` articles "Delete a member from your Workspace", "Change the owner of Workspace", "Enterprise Admin Controls Overview" (not opened) |
| Fathom | Plans: Free; Premium $20 monthly or $16 annual per user (individuals); Team $19 or $15 per user, 2-user minimum (a shared workspace with folders, comments, team-wide search and Ask); Business $34 or $25; Enterprise $35 annual (SSO, SCIM, custom retention). One person can use several accounts (work email and personal email) and switches by logging out; its help page says nothing about teams or about one account belonging to several. | `fathom.ai/pricing`, `help.fathom.video/en/articles/297024` |
| Read AI | Not benchmarked on workspaces: its support pages returned 403. | |

What it suggests (the lead's reading, not a vendor claim):
1. The pattern in the two best-documented tools is person-recorded meetings with a per-meeting visibility level and a workspace default, and limited admin power (in Fireflies a private meeting stays hidden from admins and only the owner deletes). That is closer to this design (A plus Move) than to a workspace that owns everything from the first second.
2. How departures are handled is a real product feature: Otter offers deactivate-to-keep versus remove versus delete. Our team-owned meetings and the per-team rules in 6.4 and 6.8 answer the same need. A "deactivate" style option can come later.
3. None of the pages found documents one login in several teams; Fathom's answer is several accounts. Several memberships under one login is therefore a plausible edge, not a proven one: the gap is that Otter, Fireflies and Read were not confirmed either way.
4. Price anchors from Fathom: team seats between $15 and $19 (Team) and up to $25 to $34 (Business) per user per month, with a free tier of unlimited recordings. At our estimated cost of $0.56 to $0.83 per meeting hour, an unlimited free tier is not an option for us; plans must carry an hours allowance. Not yet designed.
