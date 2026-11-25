# SOP: Meeting recipes, slice S1 (the engine)

**What shipped.** The engine under every other recipes slice: storage, the block schema, the number audit, the model call, the queue and worker with the daily cap, and the draft API (`POST/GET/PATCH/DELETE /api/documents`). PR A (tables, models, gate, block schema): [#264](https://github.com/innocent98/beyric-note-taker/pull/264); PR B (audit, generation, queue and worker, API, docs): see the PR list on `feature/recipes`. Nothing here is user-visible by itself: no screen calls it yet. Spec `docs/superpowers/specs/2026-11-24-meeting-recipes-design.md`, plan `docs/superpowers/plans/2026-11-24-meeting-recipes.md` (tasks 1 to 6). Migrations `0059` and `0060` (renumbered from the placeholders `0060` and `0061` when `feature/recipes` took `main`, whose head was `0058`; the chain is `0058` then `0059` then `0060`). If another release (workspaces) lands first, renumber again.

## Why

Adebayo wants a meeting to become the documents a team needs next (follow-up email, proposal and more), grounded in what was said, editable, and never a draft with an invented price or date. The engine has to make a draft safely (permissions, injection, cost) before any screen exists.

## How, and what was decided

| Decision | Choice | Why (and what was rejected) |
|---|---|---|
| Shape of a draft | **Structured blocks with sources** (`heading`, `paragraph`, `bullets`, `table`, board `column`/`card`), not free Markdown. Every renderer escapes for itself | Sources, tables, the audit and the editor all need structure. Markdown out of a model cannot be audited cell by cell |
| Where the model is told | The model returns strict JSON (`json_schema`, every field required, nullable where unused); code validates (`blocks.parse_blocks`): HTML, links and Markdown marks removed (the rule of `ask.plain`), sizes bounded, a source the model was not sent dropped, a table row that does not match its columns **refuses the whole draft** (`structure`) | A short row padded silently would put a price under the wrong heading. A refused draft fails cleanly and never half-shows |
| Source keys | `(meeting_id, line)` for a line, `(meeting_id, None)` for a meeting, `(kind, meeting_id, ref)` for a decision or task | A decision id and a line number would otherwise collide; the plan's `set[tuple[int, int\|None]]` could not tell them apart |
| `quote` on a line source | **Written by the server from the real line** (first 8 words), never taken from the model | The model's quote is the one thing a drifted source could not be re-found by; the plan said "the first words of the line" |
| Blanks | `[need: <what>]` in the text; the prompt forbids guessing; code finds them (`audit.blanks`) | Advisory warnings, never blocks |
| Number audit | `app/recipes/audit.py`: digits, currencies (₦, N, NGN, naira, $, €, £, codes, words), `k`/`m`/`bn`, English number words, dates (ISO, US, UK, "15th Nov", ambiguous day/month matches either way), times, percentages. Each figure becomes keys (`v:50000`, `c:NGN:50000`, `d:2026-12-01`, `t:15:00`); a figure is supported when a key is in the corpus. `derived` = a sum, difference, product or quotient of figures in the corpus, or a length of time in another unit. Ignores line numbers, list ordinals, `v2.1`, bare years, a table's numbering column. Over-flags on purpose | Spec 5.4. Seeded recall test: 10 of 10 invented figures flagged |
| Fencing | `generate._fence` neutralises **every** fence tag of the prompt (`meeting`, `brief`, `options`) in every fenced text, on top of `notes._fence` | `notes._fence` neutralises only its own tag: a brief saying `</meeting>` would look like the end of the meeting |
| What the model reads | One meeting: transcript as `[L12] [Name]: text` (numbering of `ask.transcript_rows`) plus notes, decisions, tasks, open questions with ids. Several: notes, decisions, tasks, questions only, **never several transcripts** (guarded: `whole_transcript` with more than one meeting is an error) | Cost and context; spec 5.3b |
| Failure codes | `empty`, `too_long` (no model call, no count), `model_error`, `model_reply` (not JSON or cut off), `structure`; worker adds `gave_up`, `not_ready`, `recipe_gone`, `owner_gone`, `access_gone`, `error` | Plain `failure_message` per code in `service.FAILURE_MESSAGES`; no meeting words in any code |
| Queue | Copy of the translation pattern: request writes the draft `writing` and answers 202; `service.process_next` claims (`FOR UPDATE SKIP LOCKED`), reads, commits, calls the model with nothing held, writes with a compare-and-set on the claim; `run_loop` thread started by `worker.start_recipe_thread`, joined on shutdown. Dead worker: reclaimed after `STALE_RUNNING` (10 min), at most 3 claims then `gave_up` | One request, one ledger row: a reclaim reuses the row |
| Daily cap | Ask's pending-then-settle ledger: under a per-user advisory lock the request checks `daily_count` and writes the `pending` `ai_runs` row (kind `recipe`) in one short transaction; the worker settles it. Counts every attempt that reached the model, failures and regenerations included, a discarded draft too. A draft that failed **before** the model (`NOT_CALLED`) deletes its pending row and gives the slot back. Default 20 | Two requests at once cannot both take the last slot (test with real threads) |
| Double submit | Same person, recipe, meetings and options while a draft is `writing` (within 2 minutes) returns that draft; decided under the same lock as the cap | A second tab or a double click must not make a second draft or a second count |
| Gate | `entitlements.MAKE = "make"` in `FEATURES`, `feature_gates._WORDS["make"] = "Making documents"`, checked in `request_draft` before anything is written. Not in the worker | Same as Ask: a queued draft was allowed when asked for |
| Access at worker time | The worker re-checks, as the owner: still on the draft's team (`access_gone`), the recipe not archived and still theirs or their team's (`recipe_gone`), each meeting still readable (left out and counted). `reading()` (the audit on read) uses the team brief only while the person is still a member | Found by the automated security review of this slice: a person who left a team must not get the brief's text or a hint of it |
| `GET` computes `checks`, `stale` and `sources_available` | Every read audits the **current** blocks against the current meetings' text, decisions, tasks and brief; `stale` compares `source_hash` with a fresh hash of the meetings and brief | A person's edit must be audited too; nothing audit-related is stored |
| Team | `X-Team-Id` header (`team_admin.requested_team`), default team otherwise; the draft stores `team_id` (whose brief it used) | The active-team switcher already exists |
| Starters | `service.starter_for(key)` imports `app.recipes.library.STARTERS` lazily (a list or dict of objects with `key, name, instruction, output_type, scope, uses_brief`); slice S3 writes that module | S1 cannot depend on S3's file; until it exists no starter resolves (404) and the tests register fakes |
| `GET /api/documents` | Own drafts, `meeting_id`, `before_id`, `limit`. The plan's `mine` and `shared` parameters are **not** there | `shared` needs slice S4's visibility rules (and `mine` is meaningless without it); S4 adds them |

**LEAD-DESIGNED pieces (not in the designer's files):** none on screen in this slice. The wording the FE will show comes from the API: the failure sentences (`FAILURE_MESSAGES`), the cap sentence ("You have made N documents today. Try again tomorrow.") and the `blocks_invalid` sentence ("Those blocks cannot be saved. Check the tables and try again.").

## What's involved

| Path | Change |
|---|---|
| `alembic/versions/0059_recipes_core.py` | tables `recipes`, `documents`, `document_meetings`, `document_shares`, `team_briefs`; widens `ck_ai_runs_kind` with `recipe`. Downgrade deletes `recipe` ledger rows, drops the tables |
| `alembic/versions/0060_recipes_ledger_link.py` | `documents.ledger_run_id` (no FK: the ledger outlives the draft) |
| `app/models.py` | `Recipe`, `Document`, `DocumentMeeting`, `DocumentShare`, `TeamBrief`; `AI_RUN_KINDS` + `recipe`; `DOCUMENT_STATUSES`, `RECIPE_OUTPUT_TYPES`, `RECIPE_SCOPES` |
| `app/recipes/blocks.py` | the schema, `parse_blocks`, `dump_blocks`, `load_blocks`, `texts`, `to_markdown`, `to_text` |
| `app/recipes/audit.py` | `audit`, `blanks`, `normalise_figures`, `Flag`, `Blank` |
| `app/recipes/generate.py` | `build_context`, `generate_blocks`, `source_hash`, `GenerationFailed`, `RecipeSpec`, the strict `SCHEMA` and `SYSTEM` prompt |
| `app/recipes/service.py` | `request_draft`, `resolve_recipe`, `daily_count`, `process_next`, `run_loop`, `reading`, `failure_message` |
| `app/api_recipes.py`, `app/api_schemas.py`, `app/api.py` | the five routes and their bodies |
| `app/worker.py` | `start_recipe_thread`, joined at shutdown |
| `app/entitlements.py`, `app/feature_gates.py`, `app/ai_versions.py`, `app/ai_runs.py`, `app/config.py` | `MAKE`, "Making documents", `RECIPE_PROMPT_VERSION = "recipe-v1"`, settings `recipe_model`, `recipe_daily_limit` (20), `brief_max_chars` (12000), `draft_max_blocks` (60), `import_max_bytes` (5 MB), `import_max_pages` (40) |
| `scripts/clean_env.py`, `deploy/README.md` | the six settings registered (CI's `test_clean_env` caught them missing on PR A) |
| `docs/openapi.json`, `web/lib/api/schema.d.ts` | regenerated (`PYTHONPATH=. python scripts/export_openapi.py`: without `PYTHONPATH=.` it imports the main checkout's `app`). **The exporter's file omits the `/api/billing` routes (pre-existing, not changed here)** |
| `scripts/capture_recipes.py`, `docs/fe-captures/recipes/`, `docs/fe-integration-guide-recipes.md` | live captures (44 exchanges) and the guide |
| Tests | `test_recipes_migration`, `test_recipe_blocks`, `test_recipe_audit`, `test_recipe_generate`, `test_recipe_jobs`, `test_api_recipes`; additions to `test_ai_runs`, `test_feature_gates` |

## Verification

- Locally (bounded, scratch Postgres `recipes-engine-pg` on port 5441, removed afterwards): the migration test (upgrade from `0056`, a `recipe` ledger row and a draft with a meeting link, downgrade, round trip; the checks refuse a bad status, type, scope and a second share), `test_migrations`, the blocks, audit, generate, jobs and API tests (about 110 tests), the gate table in `test_feature_gates` for `make`, `test_clean_env`, and four generic route tests in `test_api.py` (login needed, CSRF on writes, reads, the committed OpenAPI file). `ruff check` and `ruff format --check` clean. **The full suite, shards and e2e were left to CI.**
- Review Focus lines this slice owns, each pinned by a test: injection (`test_injection_line_does_not_change_rules`, `test_prompt_fences_meeting_and_brief_text`); empty, long and non-English transcripts (`test_empty_transcript_fails_cleanly`, `test_too_long_transcript_fails_with_too_long_and_never_calls_the_model`, `test_non_english_transcript_still_audited_on_digits`); double submit and the cap race (`test_double_submit_creates_one_draft`, `test_double_submit_race_makes_one_draft`, `test_cap_race_two_requests_one_slot`: real threads, one slot, one draft); patch while writing (`test_patch_while_writing_is_409`).
- Live capture over real HTTP against the real app and worker loop with a **fake model** (no key, no network): 44 exchanges, `docs/fe-captures/recipes/`. The real model's wording and latency were **not** exercised; slice S7 does the live run.

## Operate / roll back

- **Worker:** the existing worker process now also runs a `recipes` thread; it needs the same `OPENAI_API_KEY`. Nothing to deploy separately. A stuck draft shows `writing` and is retried after 10 minutes, three times, then `failed` (`gave_up`).
- **Cost:** `ai_runs` rows of kind `recipe` carry the tokens (`scripts/ai_cost_report.py` groups by kind). The cap is `RECIPE_DAILY_LIMIT`.
- **Switch off:** there is no UI yet. To refuse generation, set `FEATURE_GATES_MODE=enforce` (people without a plan) or `RECIPE_DAILY_LIMIT=0` (everyone: the 429 message then reads "You have made 0 documents today").
- **Roll back:** `alembic downgrade 0056` (deletes every draft, share, recipe, brief and `recipe` ledger row), or revert the integration branch; nothing else depends on the tables until the later slices land.

## Follow-ups

- `GET /api/documents?shared=` and the reader's view (`hidden` sources) are slice S4; `X-Team-Id` team recipes and the brief screens S3; the picker and `left_out_count` display S5.
- `service.starter_for` expects `app/recipes/library.py` to export `STARTERS` (S3).
- A draft whose meetings are all gone (`sources_available: false`) flags every figure; the guide tells the FE not to present those flags as findings. Slice S7 deletes single-meeting drafts with their meeting.
- Audit tuning (false flags) comes from the real-meeting run after release.
- Measure real latency and cost per draft per recipe in S7.
