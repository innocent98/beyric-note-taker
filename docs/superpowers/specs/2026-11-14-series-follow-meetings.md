# Recurring meetings follow the meetings: design note

Status: DESIGN NOTE ONLY, 2026-11-14. No code changed, nothing built. The rule is Adebayo's (2026-11-14, chat): *a series has no team of its own for display; a team's recurring page lists a series if at least one of its meetings is in that team (shared to it or moved to it, readable by the viewer) and shows only those meetings; the personal team shows the series' private meetings (own, not shared, not moved).* Example: Monday's Standup shared to Tech, Tuesday's private: Tech's page shows Standup with Monday, the personal page shows Standup with Tuesday.

Facts about today's code were read from `origin/main` at `ec605f87` (paths and lines below). PR #193 (team-scoped Meetings list, decisions, review, badges) is open and is **not** part of this read and not redone here. "My call" = the lead proposes it and he may overturn it. This note follows `docs/superpowers/specs/2026-11-13-multi-team-design.md` section 6.9 ("the series part is the riskiest, size it first") and the stage 2b sizing in `docs/sop/2026-11-14-multi-team-stage-2.md`.

## 1. The five decisions in this note

| # | Decision | Where |
|---|---|---|
| 1 | **One series row per team scope ("a face per team"), not one row with a team filter.** A series is identified by `(team, recurring id)` exactly as today; the same Standup has one row in Tech and one in the person's personal team, each holding only the meetings of that scope. The unique index `uq_series_team_recurring` is kept; no schema change. *My call* (alternative rejected in section 3). | 3 |
| 2 | **A meeting joins the row of its own scope** (owning team, else shared team, else the recorder's personal team) **and follows it** when it is shared, unshared, moved, moved back, or its recorder leaves: one function, called from the one place all those changes already pass (`meeting_teams._touched`). | 5 |
| 3 | **Everything inside a series (agenda lines, "Keeps coming up", suggested agenda, counts, mail, project link, rename) belongs to the row, so it is per team for free.** Readers also filter by the row's scope (`visibility`), so a mixed legacy row can never show a meeting from another scope. | 4, 6 |
| 4 | **No schema migration.** One idempotent data backfill splits existing mixed rows (script, dry run first), and a new flag `SERIES_FOLLOW_MEETINGS` (default off, *my call*) lets the code ship before the backfill runs. | 8 |
| 5 | **Three backend PRs plus a small frontend one, in this order:** core and backfill (dark), readers and mail (dark), then docs, FE guide and switch-on. | 10 |

## 2. What happens today (read from origin/main)

How a series is created and keyed:

| Thing | Today | Where |
|---|---|---|
| Key | `team_id` + `calendar_recurring_id`, unique where the recurring id is not null; one `owner_user_id` (the creator); hand-made series have no recurring id | `app/models.py:1115-1153` (`uq_series_team_recurring` at 1127) |
| Made from the calendar | when a calendar meeting's notes are written: `link_from_calendar`, called by the pipeline right after `apply_share_default`; the series goes in the **recorder's default team** (`team_id_of` = `teams.ensure_team`: their only ordinary team, else their personal team) | `app/series.py:94-98, 205-238`; `app/pipeline.py:1366, 1370, 1376-1384` |
| Made before any recording | hourly `sync_from_joins` makes an empty card for a recurring event the scheduler will join, again in the recorder's default team | `app/series.py:264-309` |
| Made by hand | `create` in the team named by `X-Team-Id`, else the default team; `attach` and "Make this a recurring meeting?" accept only a series whose team equals `meeting_team_id` (owning team, else shared team, else the recorder's default team) | `app/series.py:312-327, 372-445`; `app/api_series.py:238-268, 494-540` |
| Existing rows | never re-keyed. `join` (a person joined a team in the old one-team world) moved the personal team's series into the new team via `move_team_series` (still called; dead once `join` stops dissolving). `leave` sets the leaver's unmoved meetings' `series_id` to null (`forget_meetings_of`) | `app/series.py:448-489`; `app/teams.py:296, 446-448` |
| A meeting points at a row | `meetings.series_id`, `ON DELETE SET NULL`; a team's deletion cascades its series rows | `app/models.py:806-808`, series `team_id` FK at 1137 |

What each feature does today:

| Feature | Today | Where |
|---|---|---|
| **Series list** (Recurring cards) | rows of **one** team (header, else default) that the viewer "sees": made it, **or** can read one of its meetings, **or** has its recurring event on their own calendar. Capped at 100 per team | `app/series.py:109-153`; `app/api_series.py:198-235` |
| **Series by id** (page, agenda, mail, the meeting page's Series row) | allowed when the row's team is one the caller is in **and** `_seen_by`. A series of a team they are not in is 404 | `app/series.py:128-161`; `app/api_series.py:83-87, 405-415` |
| **Recurring page** | `meetings_of` = the row's ready meetings the viewer can read (`can_read_clause`, which means shared to **any** of their teams). Counts, unfinished tasks, carried questions and the past list read the same way, tasks through `tasks.visible` | `app/series.py:495-510, 594-697`; `app/api_series.py:293-402` |
| **"Keeps coming up"** | a worker job (one model call per series) over the row's last ready meetings **shared to the row's own team** only (`in_team_clause(series.team_id)`); private meetings are never input. Read side shows a topic only if the viewer can still read every meeting behind it and each is still in this series | `app/topics.py:85-104, 365-495`; `app/series.py:873-915` |
| **Suggested agenda** | built in code (no model) from topics, overdue tasks, carried questions, suggested tasks, all as the viewer may read | `app/series.py:940-998` |
| **Typed agenda lines** | `series_agenda_items`, on the row; any member adds; author or owner/admin removes; consumed by the next meeting that joins the row | `app/series.py:773-837`; `app/models.py:1187-1205` |
| **Series mail** | agenda send: recipients are attendees of the row's last three meetings matched to **active members of the row's team**; each mail shows only what the recipient may read. Reminders: for every team with `reminders_on`, the series of that team whose recurring id has an event on a member's calendar; one mail per person per occurrence across teams (`_already_reminded`) | `app/series_mail.py:104-166, 185-202, 331-435`; `app/series.py:533-584` |
| **Ask** | **has no notion of series.** It searches `visible_meetings(user)` only | `app/ask.py:467, 625, 934, 991, 1044` (no `series` anywhere in the file) |
| **Project link, filing** | `series.project_id` (no FK); `filing.series_project` accepts it only when the project's team equals the row's team; linking needs owner/admin of the row's team | `app/series.py:339-369`; `app/filing.py:28-39` |
| **Tasks, sprints** | tasks follow the meeting's team (`teams.team_for_meeting`, `rehome_tasks`); sprints are the team's and the series' team key reads `found.team_id` | `app/teams.py:125-160`; `app/mail_summary.py:76-77` |

**A gap that exists today, and this note closes.** A person in Tech and Marketing opens Tech's Standup page and sees a meeting that was shared to Marketing, because `meetings_of` filters by "readable", not by "in this team". It is not a read leak (they may read it) but it is the exact mix Adebayo does not want. The stage 2b test pinning "not part of X's series" uses a person who is only in X (`tests/test_multi_team_derived.py:130-147`), so it does not catch it.

**What does not exist today.** The brief asks where "summary mail on/off, lead, linked project, per-series task rules" live. Today a series has only: `title`, `platform`, `project_id`, `owner_user_id` (the creator, used for rename), the topics bookkeeping columns, and its agenda lines. There is **no per-series summary-mail switch, no lead, no task rules**. Reminders are the **team's** (`teams.reminders_on`, `teams.reminder_timing`, `app/models.py:383-384`) and the person's (`user_settings.notify_reminders`). The table in section 6 therefore covers what exists.

## 3. The model: one row per team scope

**The rule, in one function.** A meeting's *scope team* is: the team that owns it, else the team it is shared to, else its recorder's **personal** team (with `MULTI_TEAM_ENABLED` and the new flag on; with either off, the recorder's default team as today: `teams.team_for_meeting`, `app/teams.py:125-138`, already has this exact shape for tasks). A series row with team `T` shows exactly the meetings whose scope team is `T`. In SQL (to live in `app/visibility.py`, which keeps its invariant that nothing else compares `shared_team_id`/`owner_team_id`):

- ordinary team `T`: `in_team_clause(T)` (shared to or owned by `T`);
- a personal team of `U`: recorded by `U`, `shared_team_id` null, `owner_team_id` null.

A reader then asks "scope clause of the row's team **and** `can_read_clause(viewer)`". Teams' pages and the personal page come out of the same rule.

**Why one row per scope and not one row with a filter** (alternative **A**, rejected, *my call*):

| | One row per scope (recommended) | One shared row, filtered per team (A) |
|---|---|---|
| Identity | `(team, recurring id)`: already unique and already safe | needs a key that is not a team. A recurring id alone is **not** safe: two people in different organisations invited to one Google event share the same id, so one row would carry two organisations' titles, agenda lines and project link |
| Agenda lines | on the row, so Tech's lines show on Tech's page only | a typed line would show on every team's page and in every team's mail: needs `series_agenda_items.scope_team_id` |
| "Keeps coming up" | the row's topics are computed from the row's meetings: today's job is already right | needs `scope_team_id` on `series_topics` **and** per-scope signature, checked-at and attempts (today three columns on `series`): a new table |
| Project link | project belongs to one team, so one link per row, natively (`filing.py:37-38`) | a project link per scope: new column or table |
| Rename, mail, counts, sprints | all keyed by the row's `team_id` already | each needs a scope argument |
| Migration | none | 3 schema changes |
| Cost | rows multiply (one per team per recurring event, bounded by `MAX_SERIES_PER_TEAM` = 100 per team); resolving a question or renaming does not carry to the sibling row | no duplicate rows |

The user-facing result is identical (Tech page: Standup with Monday; personal page: Standup with Tuesday). "No team of its own for display" holds: no row is shown anywhere because of *its* team, only because of *its meetings'* team.

**Pre-recording cards** (empty rows from `sync_from_joins` or `create`, no meeting yet) have no meeting to follow. My call: they stay in the team they were made in and show to the people `_seen_by` already allows (creator, or has the event on their calendar), **only while the row has no meetings at all**. Once a row has meetings, only a readable meeting in scope lists it (the creator and calendar clauses of `_seen_by` stop applying). Without this, Ada's empty Tech card would stay listed after her first recording went to her personal row. When the first recording arrives, `link_from_calendar` re-keys an empty row of the same recurring id in the recorder's default team to the meeting's scope **only if it has no waiting agenda lines**; with waiting lines it makes or finds the scope's row and the old row keeps its lines (they wait for that team's next meeting, which is right: a private Tuesday is not Tech's meeting).

## 4. The new rule for each piece

| Piece | New rule | Code that changes |
|---|---|---|
| **Series list** | rows with `team_id` = the named team that have **at least one ready meeting in scope that the viewer can read**; empty rows per section 3. Personal team: rows of the personal team with a private meeting of the viewer | `series.list_for`, `_seen_by` (`series.py:109-153`) |
| **Series by id / page** | row's team is one the caller is in **and** a readable meeting in scope exists (or the empty-row case). Unchanged shape: a person opens either of Standup's pages through the same URL pattern, each has its own id | `series.get` (`series.py:128-136`) |
| **Recurring page: meetings, counts, unfinished, finished, carried questions, attendees, last-meeting day** | add the row's scope clause to `meetings_of`, `carried_questions`, `counts_for`, `_series_meeting_ids`, `_resolve`; so an out-of-scope meeting never shows even in a mixed legacy row (defence in depth: the writers keep rows pure, the readers do not depend on it) | `series.py:495-510, 590-697, 749-767` |
| **"Keeps coming up"** | input = ready meetings of the row **in scope** (`source_meetings` already means "in the row's team"; for a personal row it becomes "own private meetings", see open question 4). A topic is stored on the row, so Tech's topics never contain Marketing's words. When a meeting leaves or joins a row **both** rows are marked stale (today `join` already marks old and new; `_touched` marks only the meeting's current row) | `topics.source_meetings` (`topics.py:85-104`), `meeting_teams._touched` |
| **Suggested agenda** | built from the row's topics, row-scoped overdue tasks, carried questions and suggested tasks: follows with no extra code once the readers above are scoped | `series.agenda_suggestions` (no change) |
| **Typed agenda lines** | stay on the row. A line typed on Tech's page shows on Tech's page and in Tech's mail only; the personal row has its own. Consumed by the next meeting that joins the **same row** | none |
| **Agenda mail** | from a row: attendees of the row's last three meetings (all in scope, so speakers of other scopes' meetings are never named), matched to active members of the row's team; content per recipient as today. A personal row has no one else to send to (`409 no_attendees`, the existing path) | none beyond the scoped readers |
| **Reminder mail** | still per team with `reminders_on` and still once per person per occurrence. Today which team's row wins is arbitrary (teams are iterated unordered). New: iterate ordinary teams before personal, then by team id, so the winning row is deterministic. A personal team's `reminders_on` is false by default, so a personal row reminds only if the person turned it on. Outlook's per-mailbox recurring ids still defeat the dedupe (known, recorded in the stage 2b SOP) | `series_mail.queue_due_reminders` (`series_mail.py:331-380`) |
| **Ask** | **no change.** Ask never reads series, only `visible_meetings`. The test list below still pins that a series' page content and Ask agree on what is readable | none |
| **Meeting page's Series row** | the meeting's own row (its scope), if the viewer is in the row's team: for a meeting shared to Tech, a Tech member sees "Standup" (Tech's row); the recorder's personal row of the same event is not shown on it | `series.of_meeting` (no change) |
| **Hand-made series** | made in the team named by the header (personal allowed); can take only meetings of its scope (the 2b rule, `meeting_team_id`, now returns the personal team for a private meeting when the flag is on) | `series.meeting_team_id` (`series.py:391-395`) |
| **Page payload** | add the row's team (`team: {id, name}`) to the series card and page, so the frontend can send the right `X-Team-Id` for the task drawer; today "a series carries no team id, so they use the tab's team (a known limit)" (`docs/fe-integration-guide-teams.md:1022-1024`) | `app/series_schemas.py`, `api_series._page`; additive |

A behaviour consequence to surface: **a person with exactly one team** (the default-team rule in `teams.default_membership`, `app/teams.py:78-101`) sees only that team's meetings on its Recurring page; their private recurring calls (a 1:1) move to the personal page. That is what the rule says; see open question 2.

Also: cadence and "next meeting" on a personal row read the person's own calendar events of that recurring id (`api_series._events`, `api_series.py:102-118`), which include the Tech occurrences. So the personal Standup page can say "Next: Monday 10:00" for the Tech meeting. It is their own calendar, not a leak; noted as a rough edge.

## 5. When a meeting changes team: the one hook

`meeting_teams._touched` (`app/meeting_teams.py:42-49`) is called by share, unshare, move and move back, after the tasks are re-homed. The series follows the same way:

`series.follow_meeting(session, meeting)`: compute the scope team; if the meeting's row has a different `team_id`, find or make the sibling row `(scope team, same recurring id)` (copy title and platform, creator = the recorder, **never copy `project_id`**: a project belongs to one team), set `meeting.series_id`, mark the old and the new row's topics stale. A meeting with no series is left alone. For a hand-made row (no recurring id) the sibling is found by `(scope team, same title, no recurring id)`, else made: a title match, *my call*, the alternative is a nullable `family_id` column (a migration) for an exact link.

Other places that must call it or are affected:

| Event | Today | New |
|---|---|---|
| Pipeline links a calendar meeting | `link_from_calendar` after `apply_share_default` | uses the scope team (not the default team), so an auto-shared meeting goes straight to the team's row (`series.py:205-238`) |
| Share / unshare / move / move back | tasks re-homed, topics marked stale | plus `follow_meeting` (`meeting_teams.py:42-49`) |
| Recorder **leaves** or is removed from a team | their unmoved meetings are unshared and dropped from the team's series (`forget_meetings_of`, `series.py:448-460`) | the meetings are private now, so they **follow** to the personal row instead of losing their series. A **moved** meeting stays in the team's row (as today) |
| Recorder's account deleted, moved meeting kept | the hollow user row stays; the team's row keeps the meeting | no change |
| Team deleted, moved meeting given back to its live recorder | `_give_back_meetings_of_doomed_teams` clears `owner_team_id`; the team's series rows then cascade and the meeting's `series_id` is nulled | call `follow_meeting` there before the team goes, so the meeting lands in the recorder's personal row (`app/deletion.py:149-171`) |
| Hand attach, "Make recurring from a reused link" | only a series of the meeting's team | same, with the personal team for a private meeting |
| `join` (old one-team world) `move_team_series` | merges a personal team's series into the joined team | unreachable once the flag is on (join no longer dissolves); left alone |

## 6. Series settings when one series appears in several teams

Because each team has its own row, **each setting lives on that team's row and only that team's members can reach it**. A member of Tech cannot open, rename or link the personal row or Marketing's row (404, row not in their teams). Proposed rules (*all my call*, they keep today's rules, only the "which row" is new):

| Setting | Lives on | Who may change it |
|---|---|---|
| Title (rename) | the team's row | the row's creator, or owner/admin of **that** team (today's rule, `series.rename`, `series.py:330-336`). A rename in Tech does not rename the personal row |
| Linked project | the team's row (`series.project_id`; the project is that team's: `filing.series_project` already requires it) | owner/admin of that team (`series.link_project`, `series.py:339-369`). A new sibling row starts with no link; tasks already filed keep their project |
| Typed agenda lines | the team's row | any member adds; the author or an owner/admin removes (`series.py:790-837`) |
| Reminders on/off and timing | the **team** (`teams.reminders_on`, `reminder_timing`), one switch for all that team's rows | owner/admin of the team (unchanged) |
| Agenda and reminder mail on/off for a person | the person (`user_settings.notify_reminders`) | the person; covers every team's rows |
| "On next agenda" and "Resolved" on a question | the question's meeting | the meeting's recorder, or owner/admin of a team that owns or has the meeting (`series.can_change_question`, `series.py:711-715`). Resolving applies to the same words **in the same row** only; the sibling row's copy stays open (a team resolving its own question does not close another team's) |
| Summary-mail switch, lead, per-series task rules | **do not exist today** | no rule needed; if added later they go on the row |

## 7. Tasks, sprints and the linked project

- **Tasks** already follow the meeting's team (`teams.rehome_tasks`), so a row shows the tasks of its own meetings, and they are in the same team as the row. Unfinished and finished counts use `tasks.visible(viewer)` plus the row's meetings: no change. Tasks are never copied between rows.
- **A project belongs to one team.** If a series appears in two teams there are two rows and each row links only a project of its own team. `link_project` already returns 404 for a project of another team (`series.py:360-366`), and `filing.series_project` ignores a stray link (`filing.py:37-38`). The personal team can have its own projects; a personal row may link one.
- **When a meeting moves from a linked row to a sibling**, its tasks are re-homed by `rehome_tasks` (project, milestone, sprint, carry cleared, assignee outside the new team unassigned); the sibling row has no project link yet, so the next extraction files by keyword. Nothing is carried across.
- **Sprints** read the row's team (`mail_summary._series_key`, `app/mail_summary.py:76-77`): "Opens Sprint 13" in the reminder is that team's sprint. No change.

## 8. Existing rows and migration

**Schema migration: none.** Every new rule uses columns that exist (`series.team_id`, `calendar_recurring_id`, `meetings.series_id`, `owner_team_id`, `shared_team_id`) and the existing unique index. (If open question 6 goes the other way, a nullable `series.family_id` is the only column that could be added; it is optional.)

**Data backfill, one-off and idempotent** (a script in `scripts/`, like the owner hand-over scripts; a dry run prints counts first; it calls the same `follow_meeting` the hooks use). I could not read the production counts (the lead cannot read the server); the dry run reports them.

| Existing row | What the backfill does |
|---|---|
| In the personal team, all its meetings private | nothing |
| In an ordinary team `T`, with private meetings of the creator mixed in with meetings shared to `T` (every default-team row of someone with one team) | each private meeting moves to a new row in the recorder's **personal** team (same recurring id); the shared ones stay |
| In a personal team but with meetings shared or moved to a team (people who had several teams when the calendar linked it) | each such meeting moves to the row of its team (new row if the team has none) |
| Moved meetings | stay with the team's row |
| Empty pre-recording rows | stay |
| Hand-made rows | meetings of another scope move to a same-titled row there (open question 6) |
| Agenda lines, topics | stay on the original row (topics are recomputed: both rows marked stale); lines are the team's and do not move |

Run order and rollout: the new code branches on `SERIES_FOLLOW_MEETINGS` (default off, *my call*; `MULTI_TEAM_ENABLED` is already on in production, so riding on it would flip the behaviour before the backfill). Deploy with the flag off, run the dry run, run the backfill (it is safe with the flag off: today's readers still work on split rows, and a split row is an ordinary row of its team), then switch the flag on. A **server step for Adebayo** (one command, dry run first) will be written in the build PR; none is needed before merging this note.

**Roll back.** Flag off restores today's readers exactly. The split rows stay split: for a person with one team their private recurring meetings will then sit under the personal row and not on their team's page until they switch to personal. A merge-back script is a follow-up only if wanted (not designed here).

## 9. Leak cases the tests must cover

Test cast as in `tests/test_multi_team_leaks.py` (Ada owns X; Pat is in X and Y; Quinn only in Y; Zed in Z), each with the flag on, each also checked after the occurrence is unshared. Mutation-check each (put the old rule back, a test must fail), as stages 2a and 2b did.

| # | Case | Must hold |
|---|---|---|
| 1 | Monday shared to X, Tuesday private, Wednesday shared to Y, one recurring id | X's list and page show Standup with **Monday only**; personal shows **Tuesday only**; Y shows **Wednesday only**; one row each |
| 2 | Pat (X and Y) opens X's Standup page | Wednesday (shared to Y) is **not** listed (closes today's gap, section 2); same for counts, carried questions, past list, attendees |
| 3 | Quinn (only Y) | 404 on X's row by id, its agenda route, its send route; X's Standup is not in his list; the meeting page shows no series for an X meeting he cannot read |
| 4 | Topics | X's "Keeps coming up" never contains words, labels or cited meeting ids from a meeting shared to Y or private; unsharing or moving a cited meeting hides the topic at once and marks **both** rows stale |
| 5 | Suggested agenda and counts | overdue, carried, suggested and unfinished counts on X's row include only X-scope meetings' tasks and questions, for Pat as for Dan |
| 6 | Typed agenda lines | a line typed on X's page is on no other row's page, list, mail or count; a meeting in the personal row does not consume it |
| 7 | Agenda mail | a send from X's row names no speaker, subject, summary or task of a Y or private meeting; the recipients are active X members only; Quinn never receives it |
| 8 | Reminder mail | a person in X and Y gets **one** mail per occurrence; its content is from the row picked by the new deterministic order, never mixed from both |
| 9 | Ask | an answer for a member of X never cites a meeting shared to Y only or private to another; unchanged by the new rows |
| 10 | A person who **left X** | X's rows are 404 to them; their formerly shared meetings are private and now sit in their **personal** row (not in X's row); they get no X agenda or reminder mail; X's topics no longer cite those meetings |
| 11 | A **moved** meeting whose recorder left X | stays in X's row; X members see it; the ex-recorder reads nothing of it (not the page, topics, counts, mail, Ask) |
| 12 | Recorder deleted their account, moved meeting kept | the row and its meeting stay; the page and topics work; "Former member" shows; no mail goes to the hollow account |
| 13 | A team deleted, moved meeting given back | the meeting lands in the live recorder's personal row, not in a cascaded or null series |
| 14 | Same recurring id in two organisations | two teams' rows with the same Google recurring id never merge (title, lines, project stay apart) |
| 15 | Project | a sibling row never inherits `project_id`; `link_project` to another team's project is 404; a task of a Y meeting is never filed into X's project |
| 16 | Mixed legacy row (before backfill, or a straggler) | the readers' scope clause hides every out-of-scope meeting even if the row still points at it |
| 17 | Backfill | idempotent (second run changes nothing), dry run changes nothing, never moves a moved meeting out of its team, never touches agenda lines |
| 18 | Flag off | every list, page, topic and mail is byte-for-byte what it is today for a one-team person |
| 19 | Pre-recording card | stays in its team only while the row has no meetings; disappears from X's list once the creator's first meeting lands in personal (and has no waiting lines) |

## 10. Size and order

Estimate from the call sites counted above: about 12 functions in `app/series.py`, 3 in `app/series_mail.py`/`app/topics.py`, 4 hook sites (`meeting_teams._touched`, `teams.leave` path, `deletion._give_back_meetings_of_doomed_teams`, the pipeline link), one clause in `app/visibility.py`, a script. **Three backend PRs and one small frontend PR**, each CI-green and deployed one at a time, none needing a schema migration:

| PR | Content | Ships |
|---|---|---|
| **S1: scope, writers, backfill (dark)** | `visibility` scope clause; `series.scope_team`, `follow_meeting`; hooks in share/unshare/move/move back/leave/team-deleted/pipeline link/attach/make-recurring; `sync_from_joins` and empty-row rule; the backfill function and script; the flag (default off). Tests 1, 10, 11, 13, 14, 15, 17, 18, 19 | dark |
| **S2: readers and mail (dark)** | scoped `list_for`, `get`, `meetings_of`, carried questions, counts, `_resolve`, topic source; deterministic reminder order; `team` on the series payloads (additive); tests 2 to 9, 12, 16 | dark |
| **S3: docs and switch-on** | SOP, FE integration guide section with live captures (the project's capture script), the master checklist, the server step, then the flag on after the backfill ran | on |
| **F1 (frontend, small)** | show the row's team on the series page; send its team as `X-Team-Id` for the drawer | after S2 |

S1 comes first because the readers cannot be switched on before rows are pure, and S2 can then be reviewed on its own as a leak review (Opus, one review: this is permission code).

## 11. Open questions for Adebayo (each yes/no)

1. **Do you accept "one Standup row per team" as how it is built** (Tech's page and the personal page each have their own Standup, with their own agenda lines, topics, rename and project link; renaming or resolving a question in one does not change the other)?
2. **Do people with only one team lose their private recurring meetings from their team's Recurring page** (they move to the personal page, as your rule says)? If no, a one-team person's private meetings would have to keep showing on the team page, which breaks the rule above for them.
3. **Should agenda lines typed on Tech's Standup page stay on Tech's page only**, so a private Tuesday meeting does not consume them?
4. **Should "Keeps coming up" also work on the personal page**, from the person's own private meetings (one more model call per personal recurring meeting)? Today it never reads private meetings.
5. **When one person is in two teams that both have a Standup row, should the single reminder mail come from the ordinary team ahead of the personal one, then the lower team id** (today it is arbitrary)?
6. **For hand-made recurring meetings (no calendar link), may a meeting that changes team follow to a same-titled row in the new team** (rather than being taken out of its series)?
7. **Is it fine that this needs one command from you on the server after the deploy** (a dry run, then the backfill), with the new flag `SERIES_FOLLOW_MEETINGS` switched on only after it?
8. **Do you want the personal Standup page's "Next meeting" to show the Tech occurrence's time** (it reads the person's own calendar), or should I hide the next date on personal rows?

## 12. Not verified

- Nothing ran: no code, no tests, no server. Row counts and the shape of real data (how many series have mixed meetings) are unknown; the backfill's dry run will say.
- The frontend's use of `GET /api/series` and the series page (headers sent, how the Recurring cards pick a team) was not re-read here: PR #193 is changing it. The note assumes the active team's id arrives in `X-Team-Id` as in `docs/fe-integration-guide-teams.md`.
- I did not read the mail templates' wording for series mail, only who is chosen and what is queried.
- I did not add a line to `docs/checklist/master-checklist.md` (the other open PRs touch it); it should get the S1 to S3 items when Adebayo decides.
