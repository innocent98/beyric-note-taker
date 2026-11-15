# Remove this recurring meeting

**What shipped.** A "Remove this recurring meeting" action: `DELETE /api/series/{id}`, a red text button at the foot of the Series page,
a question that says what happens, and a toast on the Meetings page afterwards. Branch `feature/remove-recurring-meeting`; the PR number
is on the checklist. **One migration, 0048** (new table `series_ignored`). **LEAD-DESIGNED** (Adebayo, 2026-11-15, in chat): the designer drew
no such control. It reuses the Meeting page's "Delete meeting" pieces (the red text button, the inline question box, Cancel and the danger
button); the one new style is the list inside that box (`.sr-remove-list`, `web/styles/pages.css`). It also **answers the open "ghost empty card"
decision**: an emptied row is not deleted by the server on its own; the person removes it with this action.

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
| **Bringing it back** | **not built.** There is no undo and no list of removed ones. The only way back today is by hand on the server (below) | **real choice**: a "Removed recurring meetings" list with Bring back needs a screen the designer did not draw; I left it as a follow-up rather than invent one |

The question says (review fix S2): a team row "removes it for everyone in {team}"; "No meeting is deleted" (the lines, topics and link are, and the list says so); "You can't undo this"; a Personal row has its own sentence for the other pages.

A removed scope still gets a row for the same event in a **different** scope: removing Tech's Standup does not stop Monday's private Standup
from being grouped in the person's Personal row, and does not stop a meeting shared to another team from grouping there. This is by the
"one row per scope" rule (`docs/sop/2026-11-14-series-follow-core.md`).

## Who may (the permission decisions)

`series.can_remove` (`app/series.py`, the one rule) and `series.remove`, called by `DELETE /api/series/{id}` (`app/api_series.py`):

| Caller | Result |
|---|---|
| Owner or admin of the row's team | allowed |
| The person who made the row, while still a member of its team | allowed |
| A personal row's owner | allowed (they are the `owner` member of their personal team) |
| A plain member who did not make the row and can read it | **403 `team_forbidden`**, the project's usual team message, nothing changed |
| A person outside the team, another person's personal row, an unknown id, a second removal | **404** (the route's `_series_or_404`, which uses `series.get`: not in the row's team, or cannot see it) |
| The person who made the row but left the team | 404 at the route; the rule itself also answers no (`can_remove` checks the membership first, fails closed) |

The page carries `can_remove` (top level, next to `can_rename`), from the same rule, so the button is drawn only when the call would be allowed.
`meetings_kept` in the answer counts only the row's meetings **the caller may read** (a legacy mixed row can point at a meeting they may not read;
that meeting is ungrouped all the same but not counted for them).

## How

* **Table `series_ignored`** (`team_id` FK cascade, `calendar_recurring_id`, `created_by` FK set null, unique `(team_id, calendar_recurring_id)`).
  The key is the row's own team, so it is per scope. Rejected: a "removed" flag on `series` (every reader would have to filter it, and a missed
  one would show a removed row); a global per-person list (one removal would hide the same event in another team).
* **`series.remove`** locks the row (`FOR UPDATE`), counts, inserts the mark (`ON CONFLICT DO NOTHING`), sets `meetings.series_id` null for every
  meeting in the row (in or out of scope), then deletes the row; the foreign keys take the agenda lines, topics, reminders and sends.
* **Review fix M1:** `sync_from_joins` also skips the event when it is ignored in the person's PERSONAL team (looked up, never made). A person with one ordinary team has that team as their default, so without this the removed Personal row came back as an empty card in their team.
* **The three writers that make a row from a recurring event id check `series.is_ignored`** and make none: `link_from_calendar` (also before it
  re-keys an empty card into an ignored scope), `sync_from_joins`, and `place_meeting` through `_row_for_scope` (a meeting that follows a share to an
  ignored scope is cut loose from its series: `Placement.ignored`; the backfill counts it as moved, not as "no room"). Hand attach and "Make this a
  recurring meeting?" make hand-made rows and are not affected.
* **Flag `SERIES_FOLLOW_MEETINGS` off:** the action works the same, **but a team row then also holds teammates' private meetings** (the old model): an admin who has the event on their own calendar sees the row and can remove it, which ungroups those meetings and marks the event for the whole team; `meetings_kept` will say 0 for them (F6). The mark is keyed by the row's team; the off-path writers make rows in the
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
| `scripts/capture_series_follow.py`, `docs/fe-captures/series-follow/52` to `76` | the live capture (`--keep 51` writes only the new ones) |
| `docs/fe-integration-guide-series-teams.md` (section 10, table rows), `docs/checklist/master-checklist.md`, the three series-follow SOPs (ghost-card pointers) | docs |
| `tests/test_series_remove.py` (27), `tests/test_migrations.py` (0048), `web/tests/series-remove.test.tsx` (15) | tests |

## Verification

* **Tests first**, then the code (`tests/test_series_remove.py`, 26): owner, admin, creator allowed; plain member 403; outsider and other people's personal row 404; creator
  who left 404 (and the rule alone says no); removal in X leaves Y's row, the personal row and every meeting's readability exactly as before; past meetings, tasks, open
  questions and sharing untouched; the count is only what the caller may read; lines and topics go, the project and the filed task stay; the hourly sync, a later calendar
  meeting and a later share do not bring it back; flag off; hand-made rows; the ghost card; the mark itself and its cascade. **Mutation checks** (put the old rule back, a
  test fails): `can_remove` always true (2 fail), membership check removed (1 fails, after the rule-level test was added), `is_ignored` always false (6 fail).
* **Migration 0048:** `tests/test_migrations.py::test_0048_...` runs upgrade, downgrade, upgrade on a scratch Postgres 17; `test_single_head` and `test_migration_matches_models` pass.
* **Bounded, with CI running the rest:** `ruff check .`, `ruff format --check .`, `tests/test_series.py`, `test_series_follow.py`, `test_series_readers.py`, `test_series_project_link.py`,
  `test_series_remove.py` and the openapi test (150 passed); web `npm run typecheck`, `npm run lint`, `vitest` of `series-remove`, `series-follow`, `series-view`, `series-lead-designed`,
  `recurring-cards`, `meetings-recurring`, `guards`, `meeting-view` (all passed).
* **Live capture** (`scripts/capture_series_follow.py --keep 51`): 25 new exchanges against the running app, every status and body in section 10 of the FE guide.
* **Not verified:** the button and question in a real browser (tested against the captures only, no screenshots, no parity pass at 1440, 768 and 390 px); the removal with a real Google or
  Outlook calendar; the flag-off removal live (tests only); mail (a removed row's reminders simply stop; not built in the capture).

## Operate / roll back

* **Deploy:** the migration `0048` runs with the normal deploy (it is additive; old code ignores the table). Nothing to do on the server by hand.
* **Roll back the code:** revert; the table stays and is ignored. `alembic downgrade 0047` drops it (the marks are lost and a removed calendar row may come back at the next sync).
* **Bring a removed recurring meeting back by hand** (support only, not for the app): delete its mark, then the next calendar sync or recording makes the row again for new
  meetings (old meetings are **not** regrouped; that would need the backfill idea of `place_meeting`, not written):
  `delete from series_ignored where team_id = <team id> and calendar_recurring_id = '<event id>';`
* A mark holds only the calendar event's id and who removed it; it goes with its team.

## Open decision for Adebayo (review S1): who counts as "the person who made the row"

For a calendar row the "creator" (`owner_user_id`) is nobody's choice: it is whoever's sync or recording came first. As built (his brief says "the person who created the row if they are still a
member"), a **plain member who is that creator can remove the team's row**. That ungroups every teammate's meetings in it, deletes waiting agenda lines others wrote (a plain member may not do that
elsewhere), drops the project link (owner or admin only elsewhere), and marks the event ignored for the whole team, with no undo in the app. So the first teammate whose calendar synced can permanently
remove Tech's Standup for everyone. Not changed; waiting for his answer. Smallest fix if he wants it narrower: a creator who is not an owner or admin may remove only a hand-made row, or one that holds
no meeting but their own, no project link and no lines by others; otherwise 403.

## Follow-ups

* **Bring back:** a "Removed recurring meetings" list with "Bring back" (needs a design), and regrouping the old meetings when it comes back.
* **`suggest_recurring`** stays true on an ungrouped meeting whose link was used before (captures 67 and 72), so "Make this a recurring meeting?" can show right after a removal. Pressing it makes a
  new hand-made recurring meeting. Hide it for a removed event if wanted.
* **Card menu** on the Recurring cards, if the designer draws one.
* **Browser parity pass** (screenshots at 1440, 768, 390 px) of the question box.
* **Race:** a meeting being linked to a row at the very moment it is removed can fail its link (the pipeline's link is best effort and logs it); the meeting is kept and the next recording is
  not affected. Not locked further.
* **F1 lock order.** `remove` locks the series row, then the meetings; a share (`meeting_teams.share` to `place_meeting`) and the extraction (`mark_topics_stale`) lock the meeting first. Removing and sharing the
  same row at the same instant can deadlock; Postgres fails one request (500), nothing is corrupted. Fix if wanted: lock the row's meetings, ordered by id, before the series.
* **F2** A write racing the removal (an agenda line added, a meeting attached at that instant) fails its foreign key check and returns a 500. The pipeline's link is protected by its savepoint.
* **F3** A sync or recording that passed `is_ignored` just before the removal committed can make the row again (`_get_or_create` inserts after the delete). The window is tiny; removing again works.
* **F4** A team of one dissolved into another team with multi-team off (`move_team_series`): the marks cascade away with the old team, so removed cards can come back in the new team, and a moved row can land
  in a scope that has a mark. Multi-team is on in production, so legacy only. Fix: move the marks too.
* **F5** After a Personal row is removed, a later manual share of a meeting of that event to a team does not join the team's row (the private meeting was never linked, and `place_meeting` ignores a meeting
  in no series). Before the removal it would have moved there. Auto-share is unaffected (applied before the link). Needs a small design (on share, try the calendar link for a meeting in no series).
* **F6** Flag off: see the "works the same" line above (an admin can remove a team row that holds teammates' private meetings).
