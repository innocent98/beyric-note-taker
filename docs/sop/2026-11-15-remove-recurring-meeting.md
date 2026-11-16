# Remove this recurring meeting

**What shipped.** A "Remove this recurring meeting" action: `DELETE /api/series/{id}`, a red text button at the foot of the Series page,
a question that says what happens, and a toast on the Meetings page afterwards. Branch `feature/remove-recurring-meeting`; the PR number
is on the checklist. **One migration, 0048** (new table `series_ignored`). **LEAD-DESIGNED** (Adebayo, 2026-11-15, in chat): the designer drew
no such control. It reuses the Meeting page's "Delete meeting" pieces (the red text button, the inline question box, Cancel and the danger
button); the one new style is the list inside that box (`.sr-remove-list`, `web/styles/pages.css`). It also **answers the open "ghost empty card"
decision**: an emptied row is not deleted by the server on its own; the person removes it with this action.

> **Superseded in part (2026-11-16, `docs/sop/2026-11-16-series-personal-calendar-owner-admin-teams.md`):** calendar cards now live in the person's Personal space; only an owner or admin makes a team's card; a plain creator's removal of an empty legacy row writes no team-wide mark.

## Why

Before this there was no way to delete a recurring meeting. A row that lost its last meeting stayed as an empty card, and a recurring
event from the calendar was made again by the next sync even if the person did not want it. Adebayo approved a remove action.

## What it does, in plain words

| Thing | What happens | Product choice? |
|---|---|---|
| Past meetings | stay as ordinary meetings, not grouped any more. Nothing of them changes: notes, transcript, tasks, decisions, open questions, sharing. A meeting of another scope that a legacy row still pointed at is not ungrouped: it moves to the row of its own scope first (review fix S3) | his words |
| Tasks and open questions | stay with their meetings | default I chose |
| Waiting "Other agenda items" lines | **deleted** with the row; the question says how many | **real choice**: alternative is to refuse removal while lines wait, or move them nowhere (they belong to the team's row) |
| Linked project | the project stays; only the link goes. Tasks already filed in it stay filed | default I chose |
| "Keeps coming up" topics | deleted with the row (they are computed from the row's meetings and have no other home) | default I chose |
| Reminders and agenda sends of the row | deleted with the row (history only) | default I chose |
| A row from a calendar recurring event | **remembered as removed for that team (or that person's Personal space)**: the hourly sync does not make it again, a later meeting of the event is not grouped there, and sharing a meeting to that scope does not regroup it | his words |
| Another team's or person's row of the same event | untouched. A removal acts on ONE row | his words |
| A hand-made recurring meeting | removed; nothing to remember (no calendar event) | default |
| **Bringing it back** | **built later (2026-11-16, `docs/sop/2026-11-16-bring-back-removed-recurring-meeting.md`):** a "Removed recurring meetings" card in Settings with Bring back, which clears the one mark so an empty card can be made again. It restores no lines, topics or grouping. At first it was left out: it needed a screen the designer did not draw | lead-designed, Adebayo 2026-11-16 (the lead's brief) |

The question says (review fix S2): a team row "removes it for everyone in {team}"; "No meeting is deleted" (the lines, topics and link are, and the list says so); "You can't undo this"; a Personal row has its own sentence for the other pages.

A removed scope still gets a row for the same event in a **different** scope: removing Tech's Standup does not stop Monday's private Standup
from being grouped in the person's Personal row, and does not stop a meeting shared to another team from grouping there. This is by the
"one row per scope" rule (`docs/sop/2026-11-14-series-follow-core.md`).

## Who may (the permission decisions)

`series.can_remove` (`app/series.py`, the one rule) and `series.remove`, called by `DELETE /api/series/{id}` (`app/api_series.py`):

| Caller | Result |
|---|---|
| Owner or admin of the row's team | allowed |
| The person who made the row (still a member), a plain member, **while it is completely empty**: no meeting grouped under it, no waiting agenda line, no linked project, no agenda or reminder mail still to be sent (`series.is_empty`) | allowed (Adebayo, 2026-11-15: "Yes, empty rows only"). This is how an empty ("ghost") card is cleared |
| That same person once **anything** is in the row (a calendar row's "creator" is just whoever synced or recorded first; a hand-made row's is whoever made it) | **403 `team_forbidden`**: no extra power (Adebayo: "Only owners and admins") |
| A personal row's owner | allowed (they are the `owner` member of their personal team) |
| Any other plain member who can read the row (an empty calendar row is shown to a teammate who has the event on their own calendar) | **403 `team_forbidden`**, the project's usual team message, nothing changed |
| A person outside the team, another person's personal row, an unknown id, a second removal | **404** (the route's `_series_or_404`, which uses `series.get`: not in the row's team, or cannot see it) |
| The person who made the row but left the team | 404 at the route; the rule itself also answers no (`is_manager` needs a membership, fails closed) |

The page carries `can_remove` (top level, next to `can_rename`), from the same rule, so the button is drawn only when the call would be allowed.
`meetings_kept` in the answer counts only the row's meetings **the caller may read** (a legacy mixed row can point at a meeting they may not read;
that meeting is ungrouped all the same but not counted for them).

## How

* **Table `series_ignored`** (`team_id` FK cascade, `calendar_recurring_id`, `created_by` FK set null, unique `(team_id, calendar_recurring_id)`).
  The key is the row's own team, so it is per scope. Rejected: a "removed" flag on `series` (every reader would have to filter it, and a missed
  one would show a removed row); a global per-person list (one removal would hide the same event in another team).
* **`series.remove`** locks the row (`FOR UPDATE`), counts, inserts the mark (`ON CONFLICT DO NOTHING`), sets `meetings.series_id` null for every
  meeting in the row (in or out of scope), then deletes the row; the foreign keys take the agenda lines, topics, reminders and sends.
* **Dialog for an empty row** (nothing grouped, nothing waiting, as the page shows it): "No meeting is grouped under it and nothing is waiting in it. You can't undo this.", with the calendar and other-pages lines kept and the list of what happens to meetings, lines, project, topics and tasks left out (it would say nothing true). `can_remove` is on the page only, not on the list cards (no card menu).
* **A connected calendar still makes its recurring rows (asked by Adebayo, confirmed from the code and a real run).** `sync_from_joins` makes a card for each recurring event the scheduler will join, so a card appears once a join is scheduled (within the hour of the calendar sync, not the instant the calendar is connected), and a recording's notes link the meeting to its event's row. Removal only adds `is_ignored` checks, which skip an event somebody removed for that scope; no mark exists until a removal. Capture 87 is the real sync making a card for a never-removed event, and 88 the list showing it; capture 70 shows the same sync making none for removed ones.
* **Review fix M1:** `sync_from_joins` also skips the event when it is ignored in the person's PERSONAL team (looked up, never made). A person with one ordinary team has that team as their default, so without this the removed Personal row came back as an empty card in their team.
* **The three writers that make a row from a recurring event id check `series.is_ignored`** and make none: `link_from_calendar` (also before it
  re-keys an empty card into an ignored scope), `sync_from_joins`, and `place_meeting` through `_row_for_scope` (a meeting that follows a share to an
  ignored scope is cut loose from its series: `Placement.ignored`; the backfill counts it as moved, not as "no room"). Hand attach and "Make this a
  recurring meeting?" make hand-made rows and are not affected.
* **Flag `SERIES_FOLLOW_MEETINGS` off:** the action works the same, **but a team row then also holds teammates' private meetings** (the old model): an admin who has the event on their own calendar sees the row and can remove it, which ungroups those meetings and marks the event for the whole team; `meetings_kept` will say 0 for them (F6). **Also with the flag off:** a Personal row left over from the flag-on period that its one-team person removes is not covered by the sync's personal check (that check runs with the flag on only), so the sync can still make a card in their team and later meetings group there. That is the old model, where Personal scopes do not exist; not a bug. The mark is keyed by the row's team; the off-path writers make rows in the
  recorder's default team, which is the row's team, so the check holds there too (tested).
* **Frontend:** `web/components/app/series-remove.tsx` (own component, so the Series view needs no router unless the person may remove),
  `series-view.tsx`, `recurring-cards.tsx` (the toast, via `sessionStorage` like "Meeting deleted"), `lib/deleted-flag.ts`, `lib/api/client.ts`
  (`api.series.remove`). A 404 on remove is treated as "already gone". **No card menu:** the Recurring cards are links and the design system has no
  per-card menu (the `Menu` is a labelled button); inventing one is not "where the existing patterns allow". The action is one click away on the page.

## What's involved

| File | Change |
|---|---|
| `alembic/versions/0048_series_ignored.py`, `app/models.py` | the table and `SeriesIgnored` |
| `app/series.py` | `is_ignored`, `can_remove`, `remove`, `Removal`; the checks in `link_from_calendar`, `sync_from_joins`, `_row_for_scope`, `place_meeting`; `Placement.ignored` |
| `app/api_series.py`, `app/series_schemas.py` | `DELETE /api/series/{id}`, `SeriesRemoved`, `SeriesPage.can_remove` |
| `docs/openapi.json`, `web/lib/api/schema.d.ts` | regenerated (`scripts/export_openapi.py`, `npm run gen:api`) |
| `web/components/app/series-remove.tsx`, `series-view.tsx`, `recurring-cards.tsx`, `web/lib/deleted-flag.ts`, `web/lib/api/client.ts`, `types.ts`, `web/styles/pages.css`, `web/scripts/mock-api.mjs` | the button, the question, the toast |
| `scripts/capture_series_follow.py`, `docs/fe-captures/series-follow/52` to `88` | the live capture (`--keep 51` writes only the new ones) |
| `docs/fe-integration-guide-series-teams.md` (section 10, table rows), `docs/checklist/master-checklist.md`, the three series-follow SOPs (ghost-card pointers) | docs |
| `tests/test_series_remove.py` (27), `tests/test_migrations.py` (0048), `web/tests/series-remove.test.tsx` (17) | tests |

## Verification

* **Tests first**, then the code (`tests/test_series_remove.py`, 40): owner and admin allowed; a plain member 403, the creator of a row with anything in it (a meeting, a line, a project, a mail to go) included, but allowed on a completely empty row, calendar or hand-made; the rule asked again under the row's lock (what arrives in between refuses); outsider and other people's personal row 404; creator
  who left 404 (and the rule alone says no); removal in X leaves Y's row, the personal row and every meeting's readability exactly as before; past meetings, tasks, open
  questions and sharing untouched; the count is only what the caller may read; lines and topics go, the project and the filed task stay; the hourly sync, a later calendar
  meeting and a later share do not bring it back; flag off; hand-made rows; the ghost card; the mark itself and its cascade. **Mutation checks** (put the old rule back, a
  test fails): `can_remove` always true (5 fail), the creator allowed again (4 fail), `is_ignored` always false (6 fail), the personal check in `sync_from_joins` removed (1 fails), the stray move removed (1 fails), the creator always allowed (12 fail), the mail check dropped from `is_empty` (2 fail), the second look under the lock removed (2 fail).
* **Migration 0048:** `tests/test_migrations.py::test_0048_...` runs upgrade, downgrade, upgrade on a scratch Postgres 17; `test_single_head` and `test_migration_matches_models` pass.
* **Bounded, with CI running the rest:** `ruff check .`, `ruff format --check .`, `tests/test_series.py`, `test_series_follow.py`, `test_series_readers.py`, `test_series_project_link.py`,
  `test_series_remove.py` and the openapi test (150 passed); web `npm run typecheck`, `npm run lint`, `vitest` of `series-remove`, `series-follow`, `series-view`, `series-lead-designed`,
  `recurring-cards`, `meetings-recurring`, `guards`, `meeting-view` (all passed).
* **Live capture** (`scripts/capture_series_follow.py --keep 51`): 37 new exchanges against the running app, every status and body in section 10 of the FE guide.
* **Not verified:** the button and question in a real browser (tested against the captures only, no screenshots, no parity pass at 1440, 768 and 390 px); the removal with a real Google or
  Outlook calendar; the flag-off removal live (tests only); mail (a removed row's reminders simply stop; not built in the capture).

## Operate / roll back

* **Deploy:** the migration `0048` runs with the normal deploy (it is additive; old code ignores the table). Nothing to do on the server by hand.
* **Roll back the code:** revert; the table stays and is ignored. `alembic downgrade 0047` drops it (the marks are lost and a removed calendar row may come back at the next sync).
* **Bring a removed recurring meeting back by hand** (support only, not for the app): delete its mark, then the next calendar sync or recording makes the row again for new
  meetings (old meetings are **not** regrouped; that would need the backfill idea of `place_meeting`, not written):
  `delete from series_ignored where team_id = <team id> and calendar_recurring_id = '<event id>';`
* A mark holds only the calendar event's id and who removed it; it goes with its team.

## Follow-ups

* **Bring back:** the list and the button are built (`docs/sop/2026-11-16-bring-back-removed-recurring-meeting.md`). Still open: regrouping the old meetings when a card comes back.
* **`suggest_recurring`** stays true on an ungrouped meeting whose link was used before (captures 67 and 72), so "Make this a recurring meeting?" can show right after a removal. Pressing it makes a
  new hand-made recurring meeting. Hide it for a removed event if wanted.
* **Card menu** on the Recurring cards, if the designer draws one.
* **Browser parity pass** (screenshots at 1440, 768, 390 px) of the question box.
* **Race:** a meeting being linked to a row at the very moment it is removed can fail its link (the pipeline's link is best effort and logs it); the meeting is kept and the next recording is
  not affected. Not locked further.
* **F1 lock order.** `remove` locks the series row, then the meetings; a share (`meeting_teams.share` to `place_meeting`) and the extraction (`mark_topics_stale`) lock the meeting first. Removing and sharing the
  same row at the same instant can deadlock; Postgres fails one request (500), nothing is corrupted. Fix if wanted: lock the row's meetings, ordered by id, before the series. **Also (review fix S3):** two removals of rows that hold strays of each other can deadlock the same way; one fails with a 500, nothing is lost. Legacy strays only; production has none after the backfill apart from skipped private meetings of deleted accounts, which are never moved.
* **F2** A write racing the removal (an agenda line added, a meeting attached at that instant) fails its foreign key check and returns a 500. The pipeline's link is protected by its savepoint. **Also (review fix S3):** if a stray's target row is removed at the very moment the stray is moved into it, the foreign key check fails with an unhandled `IntegrityError` and a 500; that removal rolls back and nothing is lost. Legacy strays only.
* **F3** A sync or recording that passed `is_ignored` just before the removal committed can make the row again (`_get_or_create` inserts after the delete). The window is tiny; removing again works.
* **F4** A team of one dissolved into another team with multi-team off (`move_team_series`): the marks cascade away with the old team, so removed cards can come back in the new team, and a moved row can land
  in a scope that has a mark. Multi-team is on in production, so legacy only. Fix: move the marks too.
* **F5** After a Personal row is removed, a later manual share of a meeting of that event to a team does not join the team's row (the private meeting was never linked, and `place_meeting` ignores a meeting
  in no series). Before the removal it would have moved there. Auto-share is unaffected (applied before the link). Needs a small design (on share, try the calendar link for a meeting in no series).
* **F6** Flag off: see the "works the same" line above (an admin can remove a team row that holds teammates' private meetings).
* **Closed (2026-11-15, "Yes, empty rows only"): the empty card a plain member made.** Its creator may remove it while it is completely empty, so nobody is stuck with it. An owner or admin who cannot see someone's empty card still cannot remove it unless the event is on their own calendar; that is unchanged and not a gap, because its creator can.
