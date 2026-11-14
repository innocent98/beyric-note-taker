# Recurring meetings follow the meetings, PR 2 of 3: readers and mail (dark)

PR 2 of the three in section 10 of `docs/superpowers/specs/2026-11-14-series-follow-meetings.md`
(Adebayo's answers 2026-11-14: 1 yes, 2 yes, 3 yes, 4 no, 5 yes, 6 yes, 7 yes, 8 hide "next meeting"
on personal rows). **PR 1** (`docs/sop/2026-11-14-series-follow-core.md`, #199, merged `dfbe741d`)
made the writers keep a row to one scope. **PR 3** (docs, FE guide with live captures, the
switch-on) comes later. Branch `feature/series-follow-readers`; PR number is on the checklist.
**No schema migration.** Dark: with `SERIES_FOLLOW_MEETINGS` unset every list, page, topic and mail
is what it was (the old suites are the proof, see Verification), except one additive field (below).

## What shipped

Every reader of a series row's meetings now also asks for the row's **scope** when the flag is on:
the Recurring cards and their counts, the series by id, the page (past meetings, unfinished and
finished tasks, carried questions, attendees, last-meeting day, suggested agenda), "Keeps coming
up", the agenda and reminder mail, the meeting page's Series row. A meeting of another scope that a
legacy row still points at is never shown, counted, mailed or sent to the model. Plus: no "Keeps
coming up" on a personal row, "next meeting" hidden on a personal row, one deterministic reminder
mail, and the row's `team` on the card and the page. And four leftovers of the PR 1 Opus re-check.

## Why

The readers could not be switched on until rows were pure (PR 1 writes and backfills them). But a
row can still point at a meeting of another scope (before the backfill, or a straggler), and a
person in two teams reads both, so "readable" is not "in this team": Pat (X and Y) opening X's
Standup saw Y's Wednesday. The note's section 2 calls that gap; this PR closes it in the readers,
so they do not depend on the writers being perfect.

## How, and what was decided

| Piece | What it does | Decision / alternative |
|---|---|---|
| Scope in every row reader | `series.in_row(session, series)` is `series_id == row` and, flag on, `series_scope_clause(row's team)`; `rows_in_scope` is the same for the cards' counts (one query for the teams). Used by `meetings_of`, `_series_meeting_ids` (unfinished, finished), `carried_questions`, `counts_for`, `_resolve`, `topics_for` | One helper, so the rule is in `visibility.py` once (`series_scope_clause`) and no reader compares team columns. Flag off, `in_row` is exactly `series_id == series.id`, as before |
| `get`, `list_for`, `_seen_by` | flag on: a row is seen when a meeting **in its scope** that the caller may read is in it; an empty card (no meeting at all) stays for its creator or calendar owner (PR 1's rule). `get` loads the row among the caller's teams first, then checks `_seen_by` against that row's team; a missing team fails closed | A person only in Y: 404 on X's row, its agenda, send, rename and project routes; X is not in his list (the team header is a 404). Flag off: the old single query |
| `of_meeting` | flag on: the meeting page shows the series only when **that meeting** is in the row's scope | Else Pat would see "Standup" (X's) on Y's Wednesday through a legacy row |
| "Keeps coming up" | `topics.source_meetings` returns nothing for a personal row (never asked, stored topics cleared by the job); `topics_for` returns nothing for a personal row and, for a team row, requires every cited and every shown meeting to be in the row's scope | Adebayo, 2026-11-14: no "Keeps coming up" on the personal page for now. An ordinary team's input was already `in_team_clause`, which is the same rule |
| Suggested agenda, counts | no code of their own: built from the scoped readers | A suggested task, an overdue task or a carried question of Y's Wednesday never reaches X's page or its counts |
| Typed agenda lines | unchanged: they are on the row, so they stay on the page they were typed on | A personal Tuesday linking to its own row consumes nothing of X's |
| Series mail | unchanged code, scoped input: `recipients`/`attendees` read `meetings_of`, so only people of the row's in-scope meetings, matched to active members of the row's team; each mail shows only what its reader may read | A personal row: its team has one member, so it mails nobody else (`409 no_attendees`) |
| Reminder mail | `queue_due_reminders` iterates the teams with reminders on in the order **ordinary teams, then personal, then lower team id** (flag on only); `_already_reminded` still keeps it to one mail per person per occurrence | The old order was whatever the table gave (the personal team usually first). A row with nothing in scope and no lines says nothing, so the next row speaks |
| Ask | **no code**. Ask searches `visible_meetings(user)`; it never reads series | A test pins it: Ada (X only) asking about an occurrence shared to Y or private gets "not found" and no model call |
| "Next meeting" on a personal row | `api_series._timing` returns no upcoming date for a personal row, so `next_starts_at`, `next_text` and `sprint_note` are null on the card and the page | Adebayo's answer 8. The cadence text still reads the person's calendar; that is the recurring event's real cadence and is left alone |
| `team` on the card and the page | `SeriesCard.team` and `SeriesPage.team` are `{id, name}` of the row's team, **always present, flag on or off** | Additive: the frontend sends it as `X-Team-Id` for the task drawer (F1). A required field in `docs/openapi.json`, so `web/lib/api/schema.d.ts` is **not** regenerated here (it would make mocks fail the frontend typecheck): F1 regenerates it |

**The four leftovers of the PR 1 re-check.**

| # | Fix |
|---|---|
| 1 | A new hand-made sibling row is named from the **meeting** (`titles.meeting_name`), not the source row, and its twin is found by the source title **or** the meeting's name. A rename in X no longer reaches Y's new row or a leaver's personal row |
| 2 | `test_a_meeting_shared_to_its_recorders_own_personal_team_is_private` now fails with either half of S1 reverted (the clause, via the personal row's reader; the value, via a deleted recorder's skip). The "agree on every state" test now enumerates every reachable state for every recorder who can reach it against every team (7 teams), with the unreachable states listed in its docstring |
| 3 | The empty-card re-key refuses when the scope's team is already at `MAX_SERIES_PER_TEAM` (the card stays, the link makes no row) |
| 4 | PR 1's SOP: suspended accounts' meetings now move (only **deleted** recorders are "left alone"); runbook step 4 is its own block that shows the flag and the services' start times, says "if it deadlocks, run it again" and that a sign-up during the run inflates "personal spaces made" |

## What's involved

| File | Change |
|---|---|
| `app/series.py` | `in_row`, `rows_in_scope`, `is_personal_row`; `_seen_by(user, team)`, `get`, `list_for`, `of_meeting`, `meetings_of`, `_series_meeting_ids`, `carried_questions`, `counts_for`, `_resolve`, `topics_for` scoped; `_rekey_empty_card` cap; `_row_for_scope` naming and twin match |
| `app/topics.py` | `source_meetings`: personal row has no input, scope by `in_row` |
| `app/series_mail.py` | `queue_due_reminders`: deterministic team order (flag on) |
| `app/api_series.py`, `app/series_schemas.py` | `team` on the card and the page; no upcoming date on a personal row |
| `docs/openapi.json` | `team` on `SeriesCard` and `SeriesPage` (regenerated with every router loaded, as the test does) |
| `tests/test_series_readers.py` | 25 new tests |
| `tests/test_series_follow.py` | the two PR 1 tests strengthened, three new carry tests (now 47) |
| `docs/sop/2026-11-14-series-follow-core.md`, `docs/checklist/master-checklist.md` | PR 1 SOP fixes, S2 ticked |

## Verification

The new test file was written **before** the code: 13 of the 24 tests it had then failed (the
25th, the meeting page's Series row, came after); the other 11
pin behaviour that already held (the personal row mails nobody else, a leaver's access, Ask, a
moved meeting's recorder). The mutation check below, not the red run, is what shows the tests
bite.

Leak cases of section 9 that this PR touches, each as a test of `tests/test_series_readers.py`:
1 and 2 (each page lists its own meeting only; Pat in X and Y sees only Monday on X's page, in the
cards' counts, the unfinished and finished lists, the carried questions, the attendees, the
suggested agenda; the same for a mixed legacy row, case 16), 3 (Quinn only in Y: 404 on X's row by
id, agenda, add line, send, rename, project; X is not in his list), 4 (topics: the job never reads
a Y or private meeting for X's row; a topic citing another scope's meeting is hidden even from a
reader of both; none on a personal row), 5 (overdue, suggested and carried of Y never reach X),
6 (a line typed on X's page is on no other page, list or mail), 7 (X's agenda mail names no Y or
private subject, task, question or speaker; a row holding only a Y meeting has no recipients), 8
(one reminder per person per occurrence, from the ordinary lower-id team, the personal team and Y
turned on, in an order that is wrong for the old code), 9 (Ask), 10 (a person who left X: 404, no
X mail, no X topic, the meeting follows him to the personal page), 11 (a moved meeting whose
recorder left: Ada reads it and its topic, he reads nothing of it, not Ask, mail or the personal
page), 12 (recorder deleted: the page and the topic work, no mail to the hollow account); the
meeting page's Series row; "next meeting" hidden; `team` on the payload.

**Mutation check** (21 mutations, one at a time, each against its test file; the output is in the
PR): 20 killed, 1 equivalent. Killed: `in_row` ignores scope; `rows_in_scope` ignores scope;
`_seen_by` ignores scope; `get` uses the old query; `of_meeting` drops its check; `topics_for` and
`source_meetings` show or read a personal row; `_resolve`, `carried_questions`,
`_series_meeting_ids`, `meetings_of`, `topics_for` readable: each without the scope; the reminder
order removed, and "id only"; "next meeting" shown on a personal row; hand-made new row copies the
source title; twin match ignores the meeting's name; the re-key ignores the cap; S1's clause and
S1's value reverted (each fails the strengthened test on its own). The equivalent mutant:
`topics.source_meetings` without `in_row`, because for an ordinary team `in_team_clause` is the
same rule; the personal case is a separate early return and is killed.

Locally, bounded (CI runs the rest): `ruff check`, `ruff format --check`, and 386 tests green on a
scratch Postgres 17: `tests/test_series.py`, `test_series_mail.py`, `test_series_project_link.py`,
`test_series_follow.py`, `test_series_readers.py`, `test_topics.py`, `test_calendar_recurring.py`,
`test_multi_team_{derived,leaks,moved,flag_off,review_fixes,rules}.py`; plus
`tests/test_api.py::test_the_committed_openapi_file_matches_the_routes`. The flag is off in every
test of the old files, so they are the byte-for-byte proof for the flag-off paths.
**Not verified:** against production data or a real calendar, in a browser, or the frontend with
the new `team` field (F1); the cadence text on a personal row; a reminder mail rendered by a real
mail provider; and `_timing`'s cadence still counts a mixed row's out-of-scope joined meetings
(only ones the viewer may read; not changed, not a leak).

## Operate / roll back

Nothing to run. The flag stays off after merge; PR 3 gives the switch-on. Roll back by reverting
the PR: no data changed. With the flag on and this PR reverted, PR 1's rows stay pure but the old
readers would list a one-team person's private recurring meetings as empty cards (the note's
section 8).

## Follow-ups

- **F1 (frontend):** show the row's team on the series page, send it as `X-Team-Id` for the task
  drawer, regenerate `web/lib/api/schema.d.ts` (`npm run gen:api`), hide the "next meeting" chip
  when `next_starts_at` is null (it already is for a null).
- **PR 3:** the FE integration guide with live captures (the new `team` field, the 404s, the
  personal row), the server step, the switch-on.
**From the Opus review of this PR (no blocker; the should-fix is applied: every "restart" for the
flag in the PR 1 SOP, `deploy/README.md`, `app/config.py` and `.env.example` is now
`docker compose up -d --force-recreate web worker scheduler`, and step 4 prints the flag from
inside each running service). Not built, listed so none is lost:**

- `_seen_by` does not require the in-scope meeting to be `ready` (the note says "ready"): a row
  whose only in-scope meeting is still processing lists a card with no past meetings.
- `_resolve` scopes the twins but not the source: in a mixed legacy row, resolving a Y-scope
  question can resolve the same words on an X-scope meeting for someone who may change both. Fix:
  skip the twins when the source meeting is not itself `in_row`.
- A hand-made sibling is now also found by the meeting's own name, so a generic title ("Standup")
  can join an unrelated hand-made row of that name in the new team. Not a leak (the meeting is
  that team's); a misgrouping, and the backfill does it irreversibly.
- `team` on the payload has no `is_personal`, and `next_starts_at: null` means both "personal row"
  and "no known next date": add `team.is_personal` (additive) before F1.
- The topics job still claims personal rows every recheck and clears them at once; exclude them
  in the claim query.
- `send_agenda` on a personal row answers "nobody on your team": F1 hides "Send agenda" there
  (`agenda_recipients` is already 0).
- `docs/openapi.json` also gained two unrelated `maxLength: 2000` lines (`BotNameIn.bot_name`,
  `DisplayNameIn.name`): the committed file on main was stale; correct and harmless.
- Ghost empty cards (PR 1 follow-up) remain a decision for Adebayo or the lead.
- A reminder from the personal row can still reach its owner when his ordinary team's row has
  nothing to say (by design: the one mail comes from the first row with something to say).
- `_timing` (cadence) reads the joined meetings of the whole row, not the scope: harmless once
  rows are pure; scope it if the cadence of a mixed row ever matters.
