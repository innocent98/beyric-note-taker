# SOP: Decisions as a record (audit B26 to B34, U6, U8, U9, U12)

Spec: `docs/superpowers/specs/2026-11-08-designer-v2-features-design.md`, section 3 (approved by delegation, 2026-11-08). **PR 1 (backend):** branch `feature/decisions-record-backend` (number in the PR list). **PR 2 (screens):** `feature/decisions-record-screens`, added to this SOP when it ships. **Migration 0036.** FE guide: `docs/fe-integration-guide-decisions.md`; captures `docs/fe-captures/decisions/` (`scripts/capture_decisions_api.py`).

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
| Tests | `tests/test_decisions_record.py` (35), `tests/test_migrations.py` (0036), `tests/test_accounts.py` (the IDOR guard lists the PATCH route), the existing extraction and workspace tests updated for `extract-v3` |
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

## Follow-ups
* An owner with tasks off gets no decision details (above).
* The API has no "get one decision"; the FE finds a Replaced by / Replaces target by searching its words. A `GET /api/decisions/{id}` is a small addition if wanted.
* `replaced_by` choices are built by the FE from the decisions it already loaded (most recent 30); no dedicated endpoint.
* Revisit dates have no reminder (U6, decided: none).
* The real model's behaviour for the new fields is unchecked until the first live meeting; the AI1 eval does not yet score decisions.
