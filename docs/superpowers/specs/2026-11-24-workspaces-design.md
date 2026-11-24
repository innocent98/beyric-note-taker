# Workspaces: a team becomes the container of everything

**Written 2026-11-24. Status: APPROVED by Adebayo 2026-11-24, with all my picks including the feature-gate rule in section 12 point 6 ("go with your recommendation"). Nothing is built yet.** Project-size design; one complete release, no half builds (his rule, 2026-11-24). Facts cite `file:line` from a code audit of `main` `48e026b8`. **Decided** = his words or his yes; **My call** = I chose, he has not seen it.

## 1. Intent

Today a team is a partial workspace. He wants it complete: switch to a team and everything you see is that team's, switch to your personal space and you see only that. Each workspace has its own calendar connection, its own recurring meetings, its own templates, vocabulary, integrations and settings, so one person can run Luran and a personal life from one login without the two mixing. Billing stays per person. And it should also serve schools: a lecturer owns a class workspace, students join as members.

His words (2026-11-24): "if I switch to Luran team, everything that I'm having is supposed to be on Luran: the meetings, the ask query, task, project, decision, review, teams, notification, templates, vocabulary, settings"; "I can connect individual calendars" per workspace; the same calendar may be connected to two workspaces with a way to say which one records; "remove the recurring from my personal team" while keeping it in Luran; free people are invited as members at no cost; the $18 plan owns at most three teams.

## 2. Decisions

| # | Decision | Source |
|---|---|---|
| D1 | A workspace is a team; the personal space is a workspace of one (it is created for everyone at sign-up already, `app/teams.py:65-75`) | his words |
| D2 | Billing stays **per person**: hours and plan belong to the person; a meeting spends the recorder's hours (`app/usage.py:106-111`) | his yes |
| D3 | A member may record into **any workspace they belong to, on their own hours**; a member with no hours simply cannot record | his yes |
| D4 | In a team workspace, meetings are **shared with all members by default**, with a per-meeting "keep private"; the personal workspace stays private | his yes |
| D5 | Plan limit on **owned team workspaces**: Free 0 (the personal one only; being a member is free and unlimited), **Starter (18 USD) 3**, **Pro 10**, **Scale unlimited** | his words for Free and Starter; Pro and Scale his "good" |
| D6 | Schools: a class workspace is a team workspace the lecturer owns and students join free as members; students read notes; study recipes (recipes spec 5.3a); **not a learning-management system** | his words |
| D7 | The existing consent banner stays the consent mechanism for recording | his words |

## 3. What switching changes (the target)

| Thing | Today (audit) | Target |
|---|---|---|
| Meetings list, detail | list per team, detail per user (`app/visibility.py:107-127`) | Every meeting belongs to exactly **one workspace from birth**; list and detail follow the active workspace |
| Ask Beyric | per user, all teams (`app/ask.py:467, 593`) | The active workspace's meetings, decisions and tasks only. (My call: no "ask across workspaces" toggle in this release) |
| Tasks, projects, sprints, milestones | per team already | unchanged, but on every route |
| Decisions, review | per team via the meeting | unchanged; review items follow the meeting's workspace |
| Series, Recurring cards | series per team, calendar cards per user (`app/calendar_recurring.py:160-216`) | both per workspace; "remove a recurring meeting" per workspace |
| Notifications | per user (`models.py:251-267`) | tagged with a workspace; the bell shows the active workspace's; the switcher shows an unread dot per other workspace so nothing is missed (My call) |
| Templates, vocabulary | per user (`models.py:346-370`) | per workspace: owners and admins edit, members read and use. New workspaces start with the seeded default templates. Existing ones stay in the owner's personal workspace. Vocabulary used for a meeting is its workspace's only (My call) |
| Settings | mixed | **Personal (never switch):** sign-in, name, time zone, email preferences, plan and hours. **Per workspace:** name, share default, reminders, sprints, integrations (Slack, Linear), bot name, auto-join rule, "also join unanswered", "skip one-on-ones", calendar template (the last four belong to that workspace's calendar connection) |
| Calendar | one connection per person per provider, no team (`models.py:574`) | a connection belongs to **one workspace**; one person may hold a Google or Outlook connection in several workspaces, even the same account |

## 4. Calendars per workspace

- `CalendarConnection` unique becomes `(user_id, team_id, provider)`; the "same account twice" refusals (`already_connected`, `different_account`, `app/calendar_connect.py:142-153, 334-348`) apply per workspace. Join rules live on the connection's workspace.
- **Same calendar in two workspaces (decided in principle):** the event shows in both Upcoming lists but **only one bot joins**. The platform already allows one live join per person per meeting code (`models.py:687-694`); that rule becomes the arbitration. Which workspace records: **the one the person switches it on in; until they choose, the first-connected workspace wins** (My call: stable and predictable). The other workspace's row says "Recorded in Luran. Switch here instead" and the switch moves it, with a visible note.
- **Recurring meetings:** "remove from this workspace" hides the series and its calendar card in that workspace only (`SeriesIgnored` is already per team scope, `models.py:1708-1715`).
- **Where a meeting is born:** a calendar join creates its meeting in that connection's workspace; "Send now" and pasted links use the active workspace. The organiser's-bot rule (`app/organizer_bot.py`) is unchanged: the organiser's account records into the organiser's workspace; a guest who is a member of that workspace now sees it by default (D4).

## 5. Limits and billing

- New plan feature `max_owned_teams` (a column on `Plan`, `models.py:1172-1192`; the code comment at `app/teams.py:237` already calls for "a plan setting later"): Free 0, Starter 3, Pro 10, Scale unlimited (a null or large value). Replaces the constant `MAX_OWNED_TEAMS = 3` (`app/teams.py:239`). Joining never counts. A **trial counts as Free** for this limit. (My call.)
- A person whose plan lapses or drops below the number of workspaces they own **keeps them** (nothing is deleted or locked) but cannot create more; creating shows the plan message. (My call: never destroy a team over billing.)
- No "team pays" or seats in this release (billing design section 10A stays separate).

## 6. Schools

A **class** is a team workspace with a `kind = class` label that changes wording only (Class, Students, Lecturer), puts the School recipe pack first, and defaults to sharing every class with its members. Students join through the invite link as members at no cost and read the notes, transcript, recipes' drafts they make for themselves. A lecturer's class recap and quiz recipes make shareable output (read-only share with the class). The consent banner is unchanged. Out of scope: enrolment, grading, assignment scheduling, any learning-management feature.

## 7. Server and web changes

- The active workspace becomes **required context on every route** (explicit `X-Team-Id`, default the personal workspace), not the optional narrowing it is when the flag is off (`app/team_admin.py:78-87`). `MULTI_TEAM_ENABLED` is already on in production, so the flag is removed in this release. The web adds `X-Team-Id` to every route that is not purely personal (`web/lib/api/client.ts:172-186` `isTeamScoped` is extended to Ask, templates, vocabulary, notifications, calendar and settings routes).
- The pipeline, translation, notes rewrite and mails read the recorder's templates, vocabulary and prefs by user id (`app/pipeline.py:835-845`, `app/services.py:693-701`, `app/translation.py:686`, mails). They switch to the **meeting's workspace** (and keep working when the recorder has left: moved meetings outlive recorders).
- A workspace can be **deleted** by its owner when no other member remains (there is no route today, audit item 16): its meetings go back to their recorders' personal workspaces, as account deletion does (`app/deletion.py:128-150`). Owner transfer stays a script for now.
- The switcher stays a native select with a full reload (`web/components/app/team-switcher.tsx`) and gains unread dots.

## 8. Migration (one cutover, no dark flag)

Backfill so nothing disappears: meetings with `owner_team_id` or `shared_team_id` get that workspace as their workspace, every other meeting the recorder's personal workspace; calendar connections, templates, vocabulary and the per-connection rules move to the owner's personal workspace; notifications get the workspace of their meeting (else the personal one); series, tasks, projects keep their `team_id`. The migration and the code that reads the new columns ship together, tested on a copy of production-shaped data, with a documented downgrade.

## 9. Goals, non-goals, anti-goals

**Goals:** every list, search, Ask answer, setting and calendar behaves per workspace; no data lost at migration; a free person can still be a member everywhere; plan limits enforced.
**Non-goals:** team-pays billing and seats; Ask across workspaces; a learning-management system; auto-creating teams from attendees; moving existing meetings between workspaces beyond the existing Move action.
**Anti-goals:** a person seeing another workspace's data after switching (a leak is the worst failure); a meeting recorded twice because two workspaces share a calendar; a recorded meeting landing in the wrong workspace silently; losing templates or vocabulary at migration.

## 10. Estimate and order

About **8 to 12 working days of calendar time** (a cold review said 10 to 15 and I agree it is more than the 5 to 10 I first wrote: W2, W3 and W4 all wait for W1's columns and the required header), two or three sessions at once after W1. Slices: (W1) workspace on meetings and Ask plus the required header on every route, and the migration; (W2) templates, vocabulary, notifications and settings per workspace, and the pipeline reading the meeting's workspace; (W3) calendar connections per workspace, arbitration, recurring per workspace; (W4) plan limit and workspace creation and deletion, class workspaces and the switcher's unread dots; (W5) integration, privacy wording check, SOPs, an Opus review of every permission path, and the migration rehearsal. Risks in order: the calendar arbitration, the migration, and the pipeline's reads of recorder-level data.

## 11. Open points that are his (none block starting; section 12 settles the builder's questions)

1. Pro 10 and Scale unlimited are taken as approved (his "good").
2. Whether a lecturer should be able to make a class recap shareable to students who are not members of the workspace (a public link): this document says no (members only), because public links do not exist anywhere in Beyric today.
3. Ask across workspaces stays out of this release; say if you want it.

## 12. Calls made after the builder check (My calls unless marked; he can overrule any)

1. **Existing meetings at cutover keep their visibility exactly; nothing is published.** The workspace becomes the meeting's *container* (`workspace_team_id`, new, not null); who can read it stays what it is today (`shared_team_id` set means shared, null means private to the recorder). The D4 default (shared) applies only to meetings **born after the cutover** in a team workspace; "keep private" is the existing unshare. Backfill: workspace = `owner_team_id`, else `shared_team_id`, else the recorder's personal workspace; `shared_team_id` is not touched.
2. **A meeting reached from outside the active workspace (deep link, email, notification, search hit):** the id route authorises by readability only, as today (the header never turns a readable meeting into a 404), the response carries the meeting's workspace, and the web **switches the active workspace to it** with a toast "Switched to Luran". Lists, search, Ask and counts are the header-scoped ones; the leak tests target those.
3. **A member who leaves or is removed:** their meetings **stay** in the workspace (shown as "Former member", as moved meetings are today); their calendar connection in that workspace is disconnected automatically and their scheduled joins there are cancelled; the provider grant is revoked only when no other workspace connection of theirs uses the same account (reference count). A deleted workspace (owner, no other members) takes its tasks, projects, series and integrations; its meetings go back to their recorders' personal workspaces, or are deleted if the recorder is gone (as account deletion does).
4. **Calendar arbitration, concretely.** Grain: **per event occurrence** (same platform and meeting code, overlapping start), not per series or connection. `ScheduledJoin` gets `team_id` (its connection's workspace); the existing one-live-join-per-person-per-meeting-code index stays and is the guarantee. Chooser, in order: (a) a workspace where the person switched that meeting **on** wins; (b) otherwise workspaces whose rules **would join** it are candidates, and the **first-connected** wins; (c) so if the first-connected workspace's rules would skip it and another's would join, the other records. The losing workspace's Upcoming row reads "Recorded in <workspace>" with a switch to move it. Disconnecting one workspace never revokes the grant another workspace still uses (point 3).
5. **Existing calendar users at cutover:** a connection moves to the person's **current default workspace** (`teams.default_membership`, the one they already see their meetings in: the only team, else the only ordinary team, else personal), so their meetings keep landing where they see them today. Their templates and vocabulary are **copied once** into that workspace (personal ones stay as well), so notes are written the same way after the cutover. A "copy from my personal workspace" action also exists on the Templates and Vocabulary pages for any other workspace.
6. **Classes and limits:** a class counts as an owned team, so a lecturer needs a plan with an owned-teams allowance (Starter and up). Recipes spend **no recording hours**. **Decided (his yes, 2026-11-24):** the feature gate (`ask`, `make`, translate, rewrite) needs the *caller* to have a live plan or trial (`app/feature_gates.py`), so a free student could read a class's notes but not use Ask or the study recipes. **Recommendation: a member may use Ask and Make on a workspace's meetings when the workspace OWNER has a live plan, within the daily caps, without paying themselves.** Otherwise the school case and "join as a member free" do not give members the thing they came for. (Not team-pays billing: hours and plans stay per person; this only widens who passes the feature gate on someone else's paid workspace.)
7. **Mail:** a mail is sent only if the **person's** preference allows it AND, for workspace-level mail (reminders), the **workspace** setting is on; its content and links read the **meeting's workspace**.
8. **Ask inside a team workspace** uses exactly the meeting readability rule (`can_read_clause`), so a member never gets an answer from another member's unshared meeting; the workspace only narrows which readable meetings are searched.
