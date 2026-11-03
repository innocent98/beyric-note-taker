# SOP: AI workspace W1: decisions, open questions and review items read from the notes

## What shipped
Phases **W0.2**, **W0.1 (spike AI1 only)** and **W1** of `docs/superpowers/specs/2026-11-03-ai-workspace-design.md` (sections 1 to 5, 14, 15; D1 to D19 accepted as recommended). After a meeting's notes are written the worker reads the Decisions, Open questions and Please check bullets into their own tables, and one small model call marks which Please check items are about a misheard name or term. The JSON API for the **Decisions** and **Review** screens is live; no screen is built. Branch `feature/ai-extraction-w1`, PR to `main` (number in the PR list). Migration `0011`.

## Why
Every new screen of the redesign is a view of the same few sentences in `meetings.notes`. Decisions and Review are the first two because they need no team layer and carry no new permission risk: code reads what the user already sees, so the screens cannot disagree with the notes tab, and the model's part is a small hint. The spike came first because the same call will later carry tasks (W2), where a wrong owner is the failure to avoid.

## How
| Piece | Decision |
|---|---|
| Structure vs words | **Code owns structure.** Lists come from the notes' bullets (`app/extraction.py` `parse_notes`: bullets, numbered lines, plain lines, `None noted` = none, bold stripped, repeats merged). The model never decides what is on a list |
| The model call | One call per meeting, strict JSON schema, `EXTRACT_MODEL` (default `gpt-6-luna`), `extract-v1`. **W1 asks only about Please check bullets** (the action items are passed empty): tasks are W2, and paying for answers nothing reads would also let a task-side failure mark W1 `failed`. W2 removes that one line |
| Checks after the call | Everything is checked against the transcript before it is kept (`check_extraction`): `quote_in_transcript` (labels, case, accents, punctuation ignored; fuzzy at 0.9 for 3+ words), owner must be a whole word in the transcript, due date real and weekday-consistent, title shares 60% of its words with the bullet, a milestone needs a verified quote. A failed check downgrades the item and never deletes it. In W1 only `heard_as` is used: it must be in the transcript, at most three words, no digits (numbers and prices are not terms) |
| States | `meetings.extraction_status`: null, `pending`, `running`, `done`, `failed`, `skipped`; plus `extraction_attempts`, `extraction_attempted_at`, `extraction_version` (prompt version). Set `pending` in the **same commit** that sets `ready` (`pipeline._step_notes`) and by `regenerate_notes`. Never fails the meeting |
| Worker | A fourth job in `worker.background_jobs` (own try/except, logs the type only): `workspace.process_next` takes one due meeting (`FOR UPDATE SKIP LOCKED`). Due = `pending` and at least 5 minutes since the last attempt, or `running` for over 15 minutes (a crash). Three attempts, then `failed`. Rejected: running it inside `advance_meeting` (it would hold the meeting lock through a model call and tie retries to a meeting that is no longer in flight) and a new `STEPS` value |
| Lists survive failure | Decisions, open questions and review items are written **before** the model is asked, so a failed call costs only the `heard_as` hints |
| Re-runs | `_reconcile` by fingerprint (hash of the normalised words): same words keep the row and what a person did (hidden decision, checked review item); vanished rows go; new ones are added. A rewrite that words an item differently is a new row (the hidden or checked state does not carry over); the FE guide says so |
| Backfill (D5) | `workspace.backfill` / `scripts/backfill_extraction.py`: every ready meeting gets its decisions (status `skipped` for old ones, no model); meetings from the last 14 days are queued (`pending`) for the worker. Idempotent, never mails. The migration backfills nothing |
| Ledger (W0.2) | `ai_runs` (meeting, kind, model, prompt version, tokens, status, error class name). Outlives the meeting (`ON DELETE SET NULL`), like `usage_entries`. Prompt versions in `app/ai_versions.py` |
| Scoping | Everything through the meeting's owner (`Meeting.owner_user_id`); review items also carry `owner_user_id`. A foreign id is 404. There is no team layer yet (W0.4): `decisions.team_id` of the spec is not added, and `visible_meetings()` will replace the owner filter in `app/workspace.py` when W0.4 lands |
| API | `GET /api/decisions` (grouped by meeting, `q`, `limit` in meetings, `before_id`), `POST /api/decisions/{id}/hide` (204), `GET /api/review?state=`, `POST /api/review/{id}/check`, `/uncheck`, `/vocabulary` (409 if the term exists, item still checked), `badges.review_open` on `GET /api/session`, `extraction_status` on the meeting detail. Search is `ILIKE` with escaped `%` and `_` over text, calendar title and meeting code |
| Timezone | Due dates are resolved in UTC until users have a timezone (W0.4); the meeting date is `created_at`'s date. Only matters once tasks use it |

## What's involved
- New: `app/extraction.py`, `app/workspace.py`, `app/api_workspace.py`, `app/ai_runs.py`, `app/ai_versions.py`, `alembic/versions/0011_ai_workspace_w1.py`, `scripts/backfill_extraction.py`, `scripts/capture_workspace_api.py`, `tests/eval/extraction_eval.py` with `tests/eval/extraction/` (7 cases), tests below.
- Changed: `app/models.py` (`AiRun`, `Decision`, `OpenQuestion`, `ReviewItem`, meeting columns), `app/config.py` (`extract_model`), `app/pipeline.py` (`_step_notes`), `app/services.py` (`regenerate_notes`, `add_term_from_review`, `meeting_title`), `app/worker.py`, `app/api.py`, `app/api_schemas.py`, `docs/openapi.json`, `pyproject.toml` (F811 ignores for the two new fixture-importing test files), `app/templates/privacy.html` and `web/components/legal/privacy-content.tsx` (three rows, identical text).
- Tests: `tests/test_ai_runs.py`, `test_extraction_parse.py`, `test_extraction_checks.py`, `test_extraction_call.py`, `test_extraction_eval.py`, `test_workspace.py`, `test_api_workspace.py`, `test_backfill_script.py`, and additions to `test_migrations.py` (round trip, and the 0010 test now downgrades to `0009` by name, not `-1`), `test_pipeline.py`, `test_api.py`, `test_accounts.py` (the IDOR enumeration now covers `decision_id` and `item_id`).
- Docs: `docs/spike/ai-extraction-eval.md` and its captures, `docs/fe-integration-guide-ai-workspace.md` with `docs/fe-captures/ai-workspace-w1/`, the master checklist.

## Verification
- **AI1 eval (before wiring anything): go on luna.** 7 cases, 30 action bullets: 0 invented or wrong owners, 30 of 30 quotes found, 17 of 17 stated dates right, 30 of 30 bullets covered, the misheard term found with no false one. See `docs/spike/ai-extraction-eval.md` for what it does and does not prove (small, synthetic; the real-transcript review is still owed before W2).
- Tests first, watched failing, then green. Fast checks locally (ruff, ruff format, the touched test files, the migration round trip on a scratch Postgres) and **the full Python suite once** because models, services, the session payload and the worker changed. The full run found one real problem in my own work: the eval harness leaked `NOTES_MODEL` into the process environment and broke a notes test later in the run; fixed with a context manager and a test.
- Live captures (`scripts/capture_workspace_api.py`): the real app over HTTP, the real worker step with real `gpt-6-luna` calls, a real OpenAI refusal for the failed states, the backfill, a real notes rewrite. Read in the FE guide.
- Web: `tests/legal.test.tsx` passes with the privacy rows changed in both places.
- **Not verified:** the worker process end to end (the captures call `workspace.process_next` directly), a meeting of an hour or more, two workers, the effect of a rewrite that rewords a hidden decision (unit-tested, not live), and CI's web job beyond the legal test.

## Operate / roll back
- Deploy runs migration `0011` (additive: three tables, `ai_runs`, four nullable or defaulted meeting columns). The worker starts extracting every new meeting by itself.
- **After deploying, once:** `docker compose exec web python scripts/backfill_extraction.py`. Without it old meetings have no decisions and the Decisions screen shows only meetings finished after the deploy. It is safe to repeat.
- Setting: `EXTRACT_MODEL` (default `gpt-6-luna`). To change prompt wording bump `EXTRACT_PROMPT_VERSION`.
- Roll back by reverting the PR; `0011` downgrades cleanly (tested) but drops the lists and the ledger. The old code ignores the new columns.
- Cost: about 1,100 input and 100 to 130 output tokens per meeting in the live runs (short meetings), about a fiftieth of a cent. `select kind, model, sum(input_tokens), sum(output_tokens) from ai_runs group by 1, 2` gives the real number.

## Follow-ups
- **Real transcripts:** five real meetings reviewed by Adebayo before W2 (owner, due date, quote) and a timing of the quote check on an hour-long one.
- `npm run gen:api` in `web/` to refresh `lib/api/schema.d.ts` (the session payload gained `badges`); done with the first screen (D3 Decisions, D5 Review).
- W2 removes the "ask only about Please check" line in `workspace._ask_the_model` and stores the action items; W0.4 swaps the owner filter for `visible_meetings()` and adds `decisions.team_id`.
- Open questions are stored but have no endpoint (the series screens, W8).
- A rewrite that rewords an item loses what the person did to it; matching by fuzzy text instead of exact words would keep it, at the price of surprising merges. Decide when users complain.
- No "extract again" button for a `failed` or `skipped` meeting (rewriting the notes is the only way). Cheap to add if wanted.
- The extraction date uses UTC until users have a timezone.
