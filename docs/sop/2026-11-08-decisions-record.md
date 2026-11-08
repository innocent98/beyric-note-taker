# SOP: Decisions as a record (audit B26 to B34, U6, U8, U9, U12)

Spec: `docs/superpowers/specs/2026-11-08-designer-v2-features-design.md`, section 3 (approved by delegation, 2026-11-08). **PR 1 (backend):** branch `feature/decisions-record-backend` (number in the PR list). **PR 2 (screens):** `feature/decisions-record-screens` (section "PR 2: the screens" below). **Migration 0036.** FE guide: `docs/fe-integration-guide-decisions.md`; captures `docs/fe-captures/decisions/` (`scripts/capture_decisions_api.py`).

## What shipped (PR 1)
1. **Migration 0036:** `decisions.status` (`approved | proposed | replaced | rejected`, check constraint, default `approved`), `decided_by` (varchar 100), `quote` (varchar 300), `revisit_on` (date), `replaced_by_id` (self reference, `on delete set null`), `edited_at` (timestamptz, see below), and table `decision_tasks (decision_id, task_id)` (composite key, both foreign keys `on delete cascade`). No row is rewritten: every old decision reads as Approved with no decider and no evidence.
2. **Extraction `extract-v3`** (`EXTRACT_PROMPT_VERSION`): on the **same** per-meeting call the model also returns, for each decision bullet, `decided_by`, `quote`, `proposed` and `related_actions` (indices into the action items of the same call). The checks run in `extraction._check_decisions`: `decided_by` kept only if `name_in_transcript` passes, `quote` only if `quote_in_transcript` passes, `related_actions` only for action bullets that exist (deduplicated); `proposed` is the model's word. A failed check is stored as null / false / empty, never guessed or repaired.
3. **Writing it** (`workspace._write_decisions`, in the same transaction as the compare-and-set that records the answer, after the tasks exist): the row is found by fingerprint, as the lists are. `decided_by` and `quote` are refreshed from the answer; `status` becomes `approved` or `proposed` **only if no person has changed the decision** (`edited_at` is null); the `decision_tasks` rows of the meeting's decisions are rewritten from the answer. A failed model call writes none of this.
4. **`PATCH /api/decisions/{id}`** `{status?, revisit_on?, replaced_by_id?}`: the **owner of the meeting only** (`workspace.update_decision`, rule of `app/visibility.py`). 404 for someone who cannot read it (or a hidden decision), 403 `not_owner` for a reader who is not the owner. Returns the whole decision.
5. **`GET /api/decisions`** gains per decision `status, decided_by, quote, quote_speaker, line, revisit_on, replaced_by, replaces, related_tasks, can_edit`, and on the page `counts` (All / Approved / Proposed / Replaced / Rejected, over the whole log) and `due_this_week`; new query `status=` filters one tab. `docs/openapi.json` and `web/lib/api/schema.d.ts` regenerated.

## Why
The designer's file turns Decisions from a flat list into a tracked record (status, decided by, revisit date, replaced by, related tasks, evidence). The list was parsed from the notes by code with nothing else behind it, so each of those needed a place to live and, for the AI-judged ones, a checked source.

## How: decisions and trade-offs
| Topic | Decision |
|---|---|
| One call | No extra model call: decisions ride on the call that already reads the action items. A meeting with decisions and **no action items** is now asked about too (`full` is true when there are decisions or action items); before, it would have made no call. The ledger and `extraction_version` record `extract-v3` |
| Tasks switched off | An owner with "Create tasks from action items" off still gets the small review-only call (`extract-review-v1`): their decisions stay Approved with no decider or evidence. Asking for decision details too means a second prompt shape for that call; follow-up if wanted |
| Hand-set values survive | A person's status, revisit date or replaced-by is never overwritten. The spec says "match by fingerprint, never overwrite"; the extraction itself writes `status` (`proposed`), so the code needs to tell a model's `proposed` from one a person chose. `decisions.edited_at` (an extra column beyond the spec's list) is set by every successful PATCH and read by `_write_decisions`. Alternative rejected: only let the model set the status for a decision it just created: an old decision (Approved by default) could then never become Proposed by a re-read, and the first pass of the same extraction would have made the row before the model answered |
| Same text, same row | Identity is the fingerprint of the bullet's words, as for hide. If a rewrite changes the wording the decision is a new row and a person's changes on the old one go with it (the FE guide says so) |
| Who may change | The owner of the meeting only (U9, spec). Teammates read status and date as text (`can_edit` false) |
| `replaced` and `rejected` | By hand only (U12). Rejected changes nothing else. Replaced offers an optional `replaced_by_id`; moving off Replaced clears it; naming one while not Replaced is 422 `replaced_by_needs_replaced` |
| `replaced_by_id` rules | A decision the caller may read, not hidden, not the decision itself, and the chain from it must not come back (422 `replaced_by_self`, `replaced_by_not_found`, `replaced_by_cycle`). On read, `replaced_by` is null if the viewer may not read the target (a private meeting's decision), and `replaces` lists only readable ones, so a link never leaks a private decision's words |
| `replaces` is a list | Several decisions can name the same one; the designer shows one. The FE draws a line each |
| Related tasks | Link rows are written for every task the notes made, including a suggested one, and **filtered on read**: only tasks the viewer may see (`tasks.visible`) and never a suggested task (`tasks.confirmed`); a dismissed (tombstoned) task is not visible. Accepting a suggested task makes its link appear. Rewriting the links on each answered run lets the model change its mind about which task carries a decision |
| Evidence | `line` and `quote_speaker` are computed on read with `extraction.TranscriptIndex` (the same function the Tasks API uses), over the transcript string the page shows; null when there is no quote or it is not found again. No time is stored or shown |
| Banner | `due_this_week` counts approved and proposed decisions the caller may read with `revisit_on` between Monday and Sunday of the **current week in the caller's own zone** (Settings; UTC until chosen). Last week's missed days are not counted (spec: "falls this week"). No mail, no bell (U6) |
| Tab filter | `status=` is applied on the server because the list pages by meeting: filtering a loaded page would hide decisions that are on the next page. Counts ignore `q` and `status` ("counts are over the whole log") |
| Hide | Unchanged (`POST /api/decisions/{id}/hide`). A hidden decision is in no group or count, cannot be PATCHed (404) and cannot be picked as a replacement |

## What's involved
| Place | Where |
|---|---|
| Migration | `alembic/versions/0036_decisions_record.py` |
| Models | `app/models.py` `Decision` (new columns), `DecisionTask`, `DECISION_STATUSES` |
| Prompt and checks | `app/extraction.py` (`ModelDecision`, `CheckedDecision`, `_check_decisions`, `EXTRACT_SYSTEM_FULL`, `EXTRACT_SCHEMA_FULL`, `_user_message`), `app/ai_versions.py` |
| Writing and rules | `app/workspace.py` (`_write_decisions`, `run_extraction`, `decision_counts`, `due_this_week`, `update_decision`, `list_decisions`) |
| API | `app/api_workspace.py` (`_present_decisions`, `GET`, `PATCH`), `app/api_schemas.py` (`DecisionOut`, `DecisionList`, `DecisionPatch`, `DecisionRef`, `DecisionTaskRef`, `DecisionCounts`) |
| Generated | `docs/openapi.json`, `web/lib/api/schema.d.ts` |
| Tests | `tests/test_decisions_record.py` (41), `tests/test_migrations.py` (0036), `tests/test_accounts.py` (the IDOR guard lists the PATCH route), the existing extraction and workspace tests updated for `extract-v3` |
| Docs | `docs/fe-integration-guide-decisions.md`, `docs/fe-captures/decisions/`, `scripts/capture_decisions_api.py` |

## Verification
* `tests/test_decisions_record.py`: the checks (decider, quote, related actions, strays), what a run stores and links, failed checks store nothing, a decisions-only meeting is asked about, the ledger version, hand-set values survive a re-run (including the model saying the opposite), an untouched decision still follows the model, a hidden one stays hidden, a failed second call keeps the first answer, links follow the latest answer and cascade, the list shape, counts, tabs, banner (zone UTC+14, the week's edges), every PATCH rule and error, owner 200 / reader 403 / non-reader 404, private meeting never leaks (decisions, counts, tasks, links), suggested and dismissed tasks never listed, old rows read as Approved.
* `tests/test_migrations.py`: one head, 0036 follows 0035, up/down round trip, old decisions read Approved, check constraint, self reference goes null when the target is deleted, link rows cascade.
* The whole of `tests/test_accounts.py` (IDOR and route enumeration), `tests/test_api.py`, the workspace, extraction, tasks, suggested-tasks and team-visibility suites, and `pytest --collect-only` over all of `tests/` pass; `ruff check`, `ruff format --check`, `scripts/export_openapi.py --check`; web `typecheck`, `lint`, `vitest` pass.
* A live run over real HTTP against the real app and migrations (`scripts/capture_decisions_api.py`): 48 captures in `docs/fe-captures/decisions/`. **The model reply there is a stand-in** (no OpenAI key in the session): the real model's choices for `decided_by`, `quote`, `proposed` and `related_actions` are not verified.

## Operate / roll back
* **Deploy:** migration 0036 runs on deploy (`alembic upgrade head`): adds columns and a table, rewrites no row, takes only brief locks. Nothing to switch on.
* **Cost:** the extraction call grows by the decision bullets (a few hundred output tokens). Old decisions are **not** re-read by the deploy. The existing `backfill_tasks` pass requeues recent meetings (last 14 days) whose extraction version is not `extract-v3`, quietly (no mail), if the owner runs it: that is a model call per recent meeting, the same shape of cost as the `extract-v2` bump. A person can also rewrite a meeting's notes to have its decisions read.
* **Roll back:** revert the code, then `alembic downgrade 0035` (drops the new columns and `decision_tasks`; statuses, dates and links set by people are lost). The extra columns are harmless to old code if only the code is rolled back.

## Review fixes (Opus review of #137, Lows 1, 2, 3, 7; findings 4, 5, 6 accepted as designed)
| Finding | Fix |
|---|---|
| 1 `decided_by` was any phrase said in the meeting | `extraction.speaker_in_transcript`: the name must be a speaker label of the transcript or words of one (`Grace` for `[Grace Okoro]:`), at most four words, no digits; an unlabelled transcript yields none |
| 2 Concurrent PATCHes could leave status and `replaced_by_id` inconsistent, or close a loop | `workspace.update_decision` locks the row (and the target) `FOR UPDATE` in id order, re-reads the decision after the lock (`session.refresh`), and naming a replacement also takes one advisory lock (`DECISION_LINK_LOCK`) so a chain of three requests at once cannot close a loop |
| 3 An empty PATCH marked the decision touched | 422 `nothing_to_change`, nothing written |
| 7 `revisit_on` took any date | 422 `validation_error` outside 2000-01-01 to 2100-12-31 |
| 4 Editing a bullet's wording resets hand-set values | Accepted by design (identity is the text's fingerprint). **Stated plainly:** an edit of the wording, or a rewrite that changes it, deletes the old decision row and makes a new Approved one with nothing set; anything that pointed at the old one loses its link (a decision that was Replaced by it stays Replaced with none named). In the FE guide, section 7 |
| 5 Decisions-only meetings now make a full call | Accepted, documented above (the failure path is pending, then failed after three tries, and `projects.propose` runs for them) |
| 6 `quote` is the model's text at up to 10% difference | Accepted, same pattern as task quotes |

## Follow-ups
* A meeting that is rewritten with the decision's wording changed loses that decision's status, date and links (finding 4): a stable identity would need a reconciliation by similarity, not planned.
* An owner with tasks off gets no decision details (above).
* The API has no "get one decision"; the FE finds a Replaced by / Replaces target by searching its words. A `GET /api/decisions/{id}` is a small addition if wanted.
* `replaced_by` choices are built by the FE from the decisions it already loaded (most recent 30); no dedicated endpoint.
* Revisit dates have no reminder (U6, decided: none).
* The real model's behaviour for the new fields is unchecked until the first live meeting; the AI1 eval does not yet score decisions.

---

# PR 2: the screens (parity D:802-870)

Stacked on PR 1 (`feature/decisions-record-backend`); retarget to `main` once PR 1 is merged. Web only: no migration, no API change.

## What shipped (PR 2)
`web/components/app/decisions-view.tsx` is the designer's Decisions screen: tabs All / Approved / Proposed / Replaced / Rejected with counts over the whole log, the amber "N decision is / decisions are due to be revisited this week." banner, a status pill on every row (Approved green, Proposed amber, Replaced grey, Rejected red; replaced and rejected text greyed), a meta line ("Decided by {name} · N related task(s) · Revisit {day}", each part only when there is one), and an expandable row: **Status** select, **Revisit on**, **Decided by**, the evidence box with **Show in transcript** (`/meetings/{id}?tab=transcript&line=N`) or "No transcript is available for this meeting.", **Related tasks**, **Replaced by / Replaces** links, and **Remove from log** with its ask. Subtitle (B26): "Decisions made in your meetings, with their status and the tasks that carry them out." "No decisions match." for a tab or search with nothing; "No decisions yet." (only the empty box, no tabs or search) for an empty log. Words in `web/lib/decisions.ts`; styles in `web/styles/pages.css` (`.dec-*`).

## Parity (1440 / 768 / 390, design on the left, page on the right)
Shots in `docs/ui/parity/after/app-decisions*-{1440,768,390}.jpg`, made with `web/scripts/parity-shots.mjs` against `web/scripts/mock-api.mjs`, which now serves **the designer's own sample** of 13 decisions (same words, statuses, deciders, revisit days and related tasks), stateful (PATCH and Remove change it, `/__mock/reset` restores it; `--decisions teammate` answers `can_edit: false`).

| Piece | Design | Page | Shot |
|---|---|---|---|
| Subtitle, tabs with counts, search box beside them | D:806-820 | same words, sizes, colours (segmented control, 30 px tabs, 280 px search) | `app-decisions` |
| Amber revisit banner | D:822 | same box, icon and words | `app-decisions` |
| Group header, "Open notes" | D:823-827 | unchanged | `app-decisions` |
| Row: 88 px pill column, text, meta line, chevron | D:830-835 | same grid, pill colours, greyed text | `app-decisions`, `-tab-proposed` |
| Open row: Status, Revisit on, Decided by | D:837-841 | same panel and three-column row; **Revisit on is a date input** (lead-designed) | `-open`, `-open-links` |
| Evidence box, "Show in transcript" | D:842-847 | same box; **no time** (none stored) | `-open`, `-open-links` |
| "No transcript is available for this meeting." | D:848 | same words | `-open-noevidence` |
| Related tasks (dot, title, state; opens the task) | D:849-855 | same rows; opens the board's task drawer | `-open`, `-open-links` |
| Replaced by / Replaces | D:856-857 | same lines and link style | `-open-links`, `-open-replaced` |
| Remove from log, ask, toast | D:858-866 | unchanged words, inside the row | `-confirm`, `-removed` |
| "Status changed to {Rejected}" toast | D:1955 | same | `-status-changed` |
| "No decisions match." | D:868 | same | `-nomatch` |

Differences that remain are the app's own chrome (the sidebar's Notifications item and badges, which exist in the app and not in the prototype) and the sample's times in the evidence box, which the page does not show.

## Lead-designed, not in the designer's file
| Piece | What was built |
|---|---|
| Revisit on field (U8) | A real `input type=date` in the select's shape (34 px, same border), plus a quiet **No date** button that clears it (shown only when a day is set). The design's select of four fixed days is sample data |
| Replaced by select | Choosing Replaced offers an optional "Replaced by" select (No decision chosen, then the person's other decisions, at most 30, read once from the list API); not drawn in the file |
| Teammate view | Status and Revisit on as plain text in the same layout (no select), "Decided by" and the rest unchanged |
| Failure message | "Could not update the decision. Try again." in the page's error line; the select goes back to the old value |
| Task that cannot be opened | "Could not open this task. Try again." |
| Tab strip and panel on a phone (U7) | At 640 px and under the tabs scroll sideways and the open row's panel runs the full width of the row; the file has no `@media` |
| "Replaced by:" / "Replaces:" links | The API has no "get one decision", so the link searches the decision's words (cut to 200 characters), tab All, and opens that row |
| Empty log | Tabs, search and banner are left out (the design draws them only when there is a list) |

## Verification
`web/tests/decisions-record.test.tsx` (23, fed by the captured bodies in `docs/fe-captures/decisions/`): tabs and counts, a tab asks the API for one status, empty tab and empty log, the banner words, pills and meta line, greyed rows, the open row (select options, date field, evidence link, related tasks, remove), no-evidence and no-decider cases, a quote without a link, status change (PATCH, toast, pill and counts move), a row leaves a tab it no longer belongs to, revisit set and cleared with the banner following, Replaced by choices and the other side's "Replaces", failure line, a 404 drops the row, a teammate sees text only, the links search, a related task opens the drawer, the week rule. The existing `decisions-view` tests were updated for the new subtitle and the tab group. `npm run lint`, `typecheck` and `vitest run` pass.

## Follow-ups
* The Replaced by select's choices come from the first 10 meetings of the list; a decision from an older meeting cannot be chosen from the screen yet (the server accepts any in the log).
* No keyboard shortcut or focus move after a tab change; the tabs are plain buttons with `role=tab`.
