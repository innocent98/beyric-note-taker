# SOP: Meeting recipes, slice S5 (cross-meeting picker)

**What shipped.** The meeting picker for recipes that read several meetings (Status report, Client briefing, Revision plan): `resolve_meetings` turns "a recurring meeting, a project, a sprint, a date range or these meetings" into at most 12 meetings the caller may read, newest first, counting (never naming) the ones they cannot; `POST /api/recipes/picker/resolve` previews it; a draft made from a picker keeps `left_out_count` and says so; the web component `MeetingPicker`. PR into `feature/recipes`: see the PR list on that branch (head branch `feature/recipes-cross-meeting`). Spec `docs/superpowers/specs/2026-11-24-meeting-recipes-design.md` 5.3b, 5.4, 5.4a; plan task 15. FE guide `docs/fe-integration-guide-recipes-cross-meeting.md`, captures `docs/fe-captures/recipes-cross-meeting/`.

## Why

A status report over a sprint or a client briefing before the next call needs the right set of meetings, and a person must never get, through a draft, the content of a meeting they cannot read. The engine (S1) already built a several-meetings context and left out unreadable meetings; what was missing was a way to choose the meetings, a preview that shows what was chosen and what was left out, and the left-out count on the draft.

## How, and what was decided

| Decision | Choice | Why (and what was rejected) |
|---|---|---|
| The rule for "readable" | `visibility.can_read_clause` on every meeting, and the same ready-with-content test as `service._readable_meetings` (`picker.usable`). Unreadable: left out and **counted**; its title, day, id and words never leave the server | The permission rule in the brief. A meeting that is merely not ready is neither offered nor counted: it is not hidden, it is not there yet |
| Counting the left-out | `count(scope) - count(scope AND can_read)`, not `count(scope AND NOT can_read)` | `NOT can_read_clause` is null (so false) for a meeting with no team: a private meeting would never be counted. Found by the first test run |
| A series | `series.get` (404 for another team's or one the caller may not see) and `series.in_row` (scope included) | The Series page's own rules; no second rule |
| A project | `projects.project_of_team` on the active team (404 otherwise). Meetings = those its tasks (`Task.project_id`) came from, plus those of the recurring meetings linked to it (`Series.project_id`, `series.rows_in_scope`) | Meetings have no project column: the app files a task into a project from its meeting and from the meeting's series (`filing.series_project`), so these two are how a project "has" meetings |
| A sprint | The active team's sprint (404 otherwise). Meetings = those its tasks are planned into (`sprints.sprint_filter`, the board's own rule, so "current" and "next" stand for every series' one) **plus** the team's meetings held on the sprint's days (team's owner's zone, `services.day_range_utc`) | **A call to check.** A sprint has tasks and dates but no meetings of its own. Tasks alone miss a planning meeting that produced no task; dates alone miss a meeting that planned work into the sprint from earlier. The union is two existing rules, not a new one. Say so if you want one of them only |
| A date range | The Meetings list's own filter: `visibility.readable_in` on the active team (`X-Team-Id`) and `services.day_range_utc` in the person's stored zone, else the `tz` sent | Same days as the list's headings. Never counts unreadable meetings: it is only a list of what the caller may read |
| Hand-picked | Each id through `can_read_clause`; an id that is not readable and one that does not exist look the same (both left out, counted) | No existence oracle |
| The cap | At most 12, newest first (`created_at` then `id` descending), applied **after** unreadable and not-ready ones are removed, so they do not use up slots. `capped` tells the screen when the cut happened. At most the 200 newest candidates are read before the cap | A scope of hundreds of meetings must not be loaded to show twelve |
| Where `left_out_count` comes from | `request_draft` already stored `left_out_count` for the ids it was sent that the caller cannot read or that are not ready. The picker sends only readable ids, so a new optional body field **`picker`** (the same choice) is **resolved again by the server** only to count the unreadable ones; `left_out = max(from the ids, from the picker)` so a client that sends both is not double counted. A number from the client is never trusted | A client-written number would be a lie waiting to happen. `picker` never adds a meeting |
| `picker` with a recipe that reads one meeting | 422 `meetings_invalid` | A choice only makes sense for a `many` recipe |
| The sentence | `picker_spec.left_out_sentence`: "2 meetings were left out because you cannot read them or they are not ready." (singular: "1 meeting was left out because you cannot read it or it is not ready.") On the draft it is `DocumentOut.left_out_message`, a computed field | The brief's example said only "because you cannot read them", but `left_out_count` on a draft also counts a meeting that was not ready when the request was made (S1's rule), so the shorter sentence would sometimes be untrue |
| Import cycle | `PickerSpec` and the sentence live in a leaf module `app/recipes/picker_spec.py` (pydantic only); `picker.py` re-exports `PickerSpec` | `api_schemas.py` imports only `recipes.blocks`; importing the resolver (services, series, projects) into it would be a cycle risk |
| What the model reads | **Unchanged and already right**: with several meetings `build_context` sends each meeting's notes, decisions, tasks and open questions and never a transcript. New tests pin it, including the case where unreadable meetings leave ONE meeting: it is still not sent as a full transcript (the worker uses `scope == "one"`, not "one meeting left") | Mutation-checked: making the worker use `len(meetings) == 1` makes that test fail |
| Sources | **Unchanged and already right**: `meeting`, `decision` and `task` kinds each carry `meeting_id`; a `line` source or another meeting's decision is dropped | Pinned by `tests/test_recipe_cross_meeting.py` |

**LEAD-DESIGNED pieces (not in the designer's files):** the **meeting picker** in the Make dialog's "Meetings" step, built from the app's own select, field and checkbox styles and the Alert component: a "Choose meetings by" select (A recurring meeting, A project, A sprint, A date range, Pick meetings), the select or two date boxes or a scrolling checklist (up to 12) that goes with it, the "N meetings will be used" count with the list of meetings (title and day), a notice "More than 12 meetings match. The 12 newest are used." and the left-out notice with the server's sentence. New CSS: the `mp-*` classes at the end of `web/styles/pages.css` (tokens only). The sentence about meetings left out, on the picker and on the draft.

## What's involved

| Path | Change |
|---|---|
| `app/recipes/picker.py` | `resolve_meetings(session, user, spec, *, team_id=None) -> Resolved(meetings, left_out_count, capped)`, `usable`, `MAX_MEETINGS`, `SCAN_LIMIT` |
| `app/recipes/picker_spec.py` | `PickerSpec` (exactly one way; `tz`), `left_out_sentence`, `MAX_MEETINGS` |
| `app/api_recipe_picker.py` | `POST /api/recipes/picker/resolve` -> `PickerPreview` (a new file; registered in `app/api.py`) |
| `app/recipes/service.py` | **Only this:** `request_draft(..., picker: PickerSpec \| None = None)` and the helper `_picker_left_out` (two places: the signature and one `if picker is not None: left_out = max(...)` line). `generate.py` is **not changed**: it already did the right thing |
| `app/api_schemas.py` | `DocumentIn.picker`; `DocumentOut.left_out_message` (a computed field, so no route code had to change) |
| `app/api_recipes.py` | **One line:** `create_document` passes `picker=body.picker`. The brief said not to edit this file for the new route (done: the route is in its own file); the pass-through could not be done anywhere else. Expect a trivial merge with S2/S4's edits of the same file |
| `web/components/app/meeting-picker.tsx`, `web/styles/pages.css` | the component and its styles |
| `web/lib/api/client.ts`, `web/lib/api/types.ts`, `web/lib/api/schema.d.ts` | `api.recipes.resolvePicker`, `/api/recipes/picker/resolve` made team-scoped in `isTeamScoped`, type names, regenerated schema |
| `docs/openapi.json` | regenerated with `PYTHONPATH=. python scripts/export_openapi.py` (without `PYTHONPATH=.` the script imports the main checkout's `app` and drops the recipes routes) |
| `scripts/capture_recipes_cross_meeting.py`, `docs/fe-captures/recipes-cross-meeting/`, `docs/fe-integration-guide-recipes-cross-meeting.md` | live captures (30 exchanges) and the guide |
| `web/components/app/make-dialog.tsx` | The picker is mounted in the `scope = "many"` slot (unless a different one is given through `renderMeetingPicker`, which now also receives the dialog's `team` and an `onChange(ids, picker)`); the draft request sends `picker` with `meeting_ids`; Make waits (disabled) until the picker has at least one meeting; the picker follows the team chosen in the dialog |
| `web/lib/api/client.ts` | `resolvePicker` sits inside the library's `api.recipes` object (the library branch added `api.recipes` too) |
| Tests | `tests/test_recipe_picker.py` (23), `tests/test_api_recipe_picker.py` (15), `tests/test_recipe_cross_meeting.py` (5), `tests/test_recipe_starters_cross_meeting.py` (5: the real `status_report`, `client_briefing` and `revision_plan` through the picker and the worker), `web/tests/meeting-picker.test.tsx` (12), four new cases in `web/tests/make-dialog.test.tsx` (the picker mounts only for `many` recipes, Make waits for a choice and sends `picker`, none for a one-meeting recipe, the picker follows the dialog's team; S3's fallback-text test was replaced), a line in `web/tests/team-header.test.ts` |

## Verification

- Locally (bounded; scratch Postgres `recipes-cross-pg`, removed afterwards): the three picker test files, `test_api_recipes.py` and `test_recipe_jobs.py` (84 tests, all pass; the last two because `service.py` was touched), the route tests in `test_api.py` (`-k "openapi or route"`), `ruff check` and `ruff format --check` clean; web: `meeting-picker`, `team-header`, `guards`, `api-client` tests, `tsc --noEmit` and `eslint` on the touched files clean. **The full suite, shards and e2e were left to CI.**
- Mutation checks: reverting the unreadable count to `NOT can_read` fails the left-out tests (found that way); making the worker send a full transcript when one meeting is left fails `test_one_meeting_left_after_the_others_are_unreadable_is_still_not_a_full_transcript`.
- Live over real HTTP against the real app and worker with a **fake model**: 30 exchanges in `docs/fe-captures/recipes-cross-meeting/`. The script also fails if the words of the private meetings appear in any saved response (they do not), and records what the prompt contained (no transcript, no line numbers, nothing of the unreadable meetings).
- After #267 merged: one test per real starter (`status_report`, `client_briefing`, `revision_plan`) proves it resolves the picker's output, is built from notes, decisions, tasks and questions with no transcript and nothing of an unreadable meeting, and that its sources are `meeting`, `decision` and `task` with a `meeting_id`.
- **Not verified:** the real model's choice of sources; the picker in a real browser, inside the Make dialog (vitest only, API mocked).

## Operate / roll back

No migration, no setting, no worker change. Nothing is user-visible until the integration branch ships; the Make dialog already mounts the picker. Roll back by reverting the PR: the picker route and the `picker` field go away (the FE must then not send `picker`) and drafts lose only the left-out sentence; `left_out_count` itself stays, as S1 built it.

## Follow-ups

- Look at the picker in the Make dialog in a real browser at 390 and 1280 px.
- Sprint rule (tasks planned into it plus meetings held on its days) is a call to confirm.
- A hand-picked list reads the 50 newest meetings (`GET /api/meetings?limit=50`); a person with older meetings picks them through the other ways. A search box would fix it; not built.
- `left_out_count` on a draft also counts a meeting that was not ready at request time (S1's rule), which is why the sentence says "or they are not ready".
- The Make dialog starts from a meeting page, so the first meeting is not pre-ticked in the picker: the person chooses the set. Spec 5.2 draws a Make page for cross-meeting recipes; that page is not built, the dialog serves both.
