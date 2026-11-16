# Bring back a removed recurring meeting, and the hint on a Personal card

**What shipped.** Two small follow-ups of #205 (remove) and #207 (the simpler model), one PR on branch `feature/recurring-bring-back` (PR number on the checklist).
(1) A **Removed recurring meetings** card in Settings with a **Bring back** button per row: `GET /api/series/removed` and `POST /api/series/removed/{id}/bring-back`.
(2) A hint on a Personal recurring card: `team_card` on `GET /api/series/{id}` and one info message under "Other agenda items".
**No migration.** **LEAD-DESIGNED** (the brief of the lead session, 2026-11-16): the designer drew neither piece. Both reuse existing components and classes; nothing new is styled.

## Why

* #205 remembers a removed calendar recurring meeting per scope (`series_ignored`) so the hourly sync does not make it again, and shipped with no way back except a SQL line by hand.
* #207 keeps a calendar card in the person's Personal space. Once a team also has a card of the same event, the person's recordings go into the team's card, so lines typed into the Personal
  card are not used by them. The page said nothing about it (follow-up at the foot of #207's SOP).

## What it does, in plain words

| Thing | What happens | Product choice? |
|---|---|---|
| **Bring back** | clears the ONE removal mark of that scope. **It makes no card by itself** and restores nothing: no agenda line, no topic, no project link, no regrouping of past meetings | true to how removal works |
| When the card returns, Personal | at the next hourly calendar sync (`sync_from_joins`), once a join of the event is scheduled | follows the code |
| When the card returns, a team | the next time an owner or admin shares a meeting of the event to the team, or their recording of it is auto-shared (`link_from_calendar`). The hourly sync makes only Personal cards (#207). The returning card takes in the meetings of the same event the team already has (#207's adoption) | follows the code |
| A recurring meeting made by hand | **nothing to bring back**: its removal wrote no mark, so it is never listed. Nothing is invented to undo deleted data | true to the code |
| A plain creator's removal of an empty legacy row | writes no mark (#207), so it is never listed either | follows the code |
| Who may | exactly the removal rule (`series.is_manager`): a team's mark, an **owner or admin** of that team; a Personal mark, the person (the `owner` member of their personal team). Any other member: **403 `team_forbidden`** (the project's usual error). Outside the team, an unknown id, a second call: **404** | the brief |
| The list's name for a row | the mark holds only the event's id (a deliberate privacy choice of #205). The name is what **the viewer's own calendar** calls the event; else `Recurring meeting`. A teammate's calendar title is never used | **real choice**, see below |
| The hint | `team_card`: on a Personal card from the calendar (flag on), when an ordinary team of the viewer's has a card of the same event that the viewer may see (`series.get`). Not for a hand-made card, a team's page, flag off, or a card the viewer cannot see | follows #207's rule |

### Choices I made where the code left room

* **The name of a removed row.** Options: (a) a `title` column on `series_ignored` (a migration; old marks in production have none, so most rows would still say nothing), (b) the viewer's own calendar, (c) nothing. I took (b): no migration,
  never a teammate's title, honest fallback. **Cost:** a team's list can show several `Recurring meeting` rows (it did in capture 113). The date and the remover's name tell them apart. If that proves too thin, add (a) later (a nullable column filled at removal).
* **Where the list lives:** Settings, as its own card, drawn only for the people who may use it (`team.can_manage`) and only when something was removed. Settings already holds the reminder switch for recurring meetings and is the screen a person visits for choices about their recurring meetings. The Meetings page draws nothing when it has no cards, which is exactly when a removed list matters, so it would have needed a new empty state.
* **Wording of the hint** names the consequence in the code, not a general claim: "Meetings you share with {team} go to that card, so items you add here are not used for them." (A meeting that stays private stays in the Personal card and still consumes its lines.)
* **The removal question** now says on its calendar line "...until you bring it back in Settings" (it used to say it would not be made again, which stopped being the whole truth).
* **`POST .../bring-back`, not `DELETE`:** the mark is an internal record; the person's act is "bring back".

## What's involved

| File | Change |
|---|---|
| `app/series.py` | `RemovedItem`, `removed_for`, `bring_back`, `team_card` (after `remove`) |
| `app/api_series.py`, `app/series_schemas.py` | `GET /api/series/removed`, `POST /api/series/removed/{id}/bring-back`, `RemovedList`, `RemovedRecurring`, `BroughtBack`, `SeriesTeamCard`, `SeriesPage.team_card` |
| `docs/openapi.json`, `web/lib/api/schema.d.ts` | regenerated |
| `web/components/app/settings-removed-recurring.tsx`, `web/app/settings/page.tsx` | the Settings card |
| `web/components/app/series-view.tsx`, `series-remove.tsx` | the hint; the calendar line of the removal question |
| `web/lib/api/client.ts`, `types.ts`, `web/scripts/mock-api.mjs` | `api.series.removed` and `bringBack`; `/api/series/removed` is team-scoped (sends `X-Team-Id`); the mock |
| `scripts/capture_series_follow.py`, `docs/fe-captures/series-follow/107` to `129` | part 7 of the live capture (`--keep 106`) |
| `docs/fe-integration-guide-series-teams.md` (section 12, three patched lines, table rows), `docs/checklist/master-checklist.md`, this SOP and the follow-up lines of the two earlier SOPs | docs |
| `tests/test_series_bring_back.py` (26), `web/tests/series-bring-back.test.tsx` (9), `web/tests/team-header.test.ts` (one path) | tests |

## Verification

* **Backend tests first** (`tests/test_series_bring_back.py`, written and run red before the code): the list for owner, admin, plain member (403), outsiders (404), the person's Personal space (and the owner of their team 404), no header = default team, only the asked scope, a title from the viewer's own calendar and never a teammate's,
  hand-made and plain-creator removals leaving nothing; bring back for owner, admin, plain member (403), outsiders and a member who left (404), the person on their own mark, unknown / malformed / second call (404), CSRF and no session; one mark cleared and no meeting, task, question, row or agenda line changed; the hourly sync making the Personal card again; an owner's auto-shared recording making a team's card after the mark is cleared (and the team's meetings of the event joining it); flag off; the hint (present, absent without a team card, on a team page, hand-made, a card the viewer cannot see, flag off).
  **Mutation checks** (break the rule, a test fails): the manager check removed from `bring_back` (1 fails); the title read from any member's calendar (1 fails).
* **Web tests** (`web/tests/series-bring-back.test.tsx`) against captures 108, 112, 113, 114, 120, 122, 127 and 128. **Written after the web code** (the backend was test-first; the web side was not).
* **Live:** captures 107 to 129 against the running app; every body in guide section 12 was compared with its capture by script (12 blocks, 0 differences).
* **Bounded local runs, CI runs the rest:** `ruff check .`, `ruff format --check .`; `tests/test_series_bring_back.py`, `tests/test_series_remove.py` and the openapi test (67 passed); web `npm run typecheck`, `npm run lint` (0 errors), `vitest` of `series-bring-back`, `series-remove`, `series-view`, `series-lead-designed`, `settings-page`, `settings-reminders`, `recurring-cards`, `team-header`.
* **Not verified:** the Settings card, the hint and the changed removal line in a real browser (captures only, no screenshots, no parity pass at 1440, 768 and 390 px); a real calendar; the team case live (the hourly sync is not what makes a team card, so it is tests only); `removed_by` null live; the 100-row limit.

## Operate / roll back

* **Deploy:** nothing to do on the server. No migration, no setting. **Roll back:** revert the PR; marks are untouched by it except the ones people brought back (those are gone, which is what they asked for).
* **Bring a mark back by hand** is no longer needed (the SQL line in `2026-11-15-remove-recurring-meeting.md` still works).
* Gotcha for whoever regenerates the OpenAPI file: `python scripts/export_openapi.py` run alone drops routes (it imports `app.api` only); import `app.main` first (the way the test and the app do), or the committed file loses most paths.

## Follow-ups

* **Regroup old meetings** when a card returns (the idea of `place_meeting`'s backfill): not built, nothing deleted by removal prevents it.
* **A name for every removed row**: a nullable `title` on `series_ignored`, filled at removal (needs a migration; old marks stay nameless).
* **Browser parity pass** (screenshots at 1440, 768 and 390 px) of the Settings card and the hint.
* **`suggest_recurring` after a removal** (from #205) still stands.
* The same hint could name the **team's lines** on the Personal card when the team's reminders are on (not asked for).
