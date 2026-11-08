# SOP: Ask Beyric backend (spec `docs/superpowers/specs/2026-11-08-designer-v2-features-design.md`, section 1; checklist 3F)

PR: branch `feature/ask-beyric-backend` (number in the PR list). **One migration, 0034** (a one-line check-constraint widening; the spec said none, see "Spec corrections"). FE guide: `docs/fe-integration-guide-ask-beyric.md`; captures `docs/fe-captures/ask-beyric/` (`scripts/capture_ask_api.py`). The page itself is the `ask-screens` session's.

## What shipped
1. **`GET /api/ask/scope`**: `{meetings, decisions, tasks, suggestions}` for the scope line and the chips. No model.
2. **`POST /api/ask`** `{"question"}`: answers from the meetings the caller may read. Three kinds of answer: `tasks` (the person's own tasks, code only), `answer` (points with source chips), `none`.
3. **`app/ask.py`**: intent routing, date hints, Postgres full-text candidate selection, passage cutting and scoring, the model call and every check on its reply, the cap, the ledger row.
4. **Settings**: `ASK_MODEL` (default `gpt-6-luna`), `ASK_DAILY_LIMIT` (default 30). Both optional. `ASK_PROMPT_VERSION = "ask-v1"` in `app/ai_versions.py`, registered in `ai_runs._VERSION_OF_KIND`.
5. **Migration 0034** widens `ck_ai_runs_kind` to allow `'ask'`. Downgrade deletes `ask` rows first.

## Why
The designer's new "Ask Beyric" page: a question answered only from meetings the person may read, each point citing its source meeting. Trust is the feature, so code (not the model) decides what is shown and where each chip points.

## How: decisions
| Topic | Decision |
|---|---|
| **Visibility** | Every read starts from `visible_meetings(user)` (`app/visibility.py`) with `status = 'ready'`; decisions and tasks of the candidates are read for those meetings only. Tasks use `tasks.visible(user)` (the one tasks rule). A teammate's private meeting never reaches the passages, the prompt, a cite or the scope counts. A chip is built by code from the passage's meeting, never from anything the model says |
| **Intent** | `is_task_question`: four patterns on the normalised text (`my tasks` / `my action items`, `assigned to me`, `what did I commit to`, `what am I supposed to do`). Everything else is search |
| **Date hint** | `today`, `yesterday`, `last week`, `this week`, a weekday, `29 Oct` / `29th October` / `Oct 29`, `2026-10-29`, resolved in the person's zone (`user_settings.time_zone`). A weekday or a date with no year means the most recent such day not after today. **Ignored, never guessed:** `next Friday`, two different hints, a day that does not exist |
| **Candidates** | Ready, readable, date-narrowed; `to_tsvector('simple', concat_ws(' ', notes, coalesce(speaker_transcript, transcript)))` against the question's content words as an OR prefix query (`decid:* \| pric:*`; a light stemmer so "decide" finds "decided" and "decisions"), `ts_rank` order, at most 12. A summary question (`summar`, `recap`, `what happened`, or only a date) on a day with one to three meetings adds those meetings and sends their notes sections whole |
| **Passages** | A notes bullet (with its heading, so "decisions" finds the Decisions section), up to six transcript rows, an unhidden decision (skipped when it is a bullet already), a task (title and verified quote). Score = distinct question words present; a word matching the speaker label counts 2. Top 12, each cut to 600 characters, numbered `P1..P12` |
| **`line`** | The 1-based **row** of the transcript string the page draws: `ask.transcript_rows` is a line-for-line port of `web/lib/transcript.ts` `parseTranscript` (a `[Name]:` line starts a row, another line continues it, text before the first label is its own row, no label at all is one row; JavaScript's `.` also stops at `\r`). Pinned by `test_transcript_rows_split_exactly_as_the_meeting_page_does`. A task cites `transcript` when its quote is found in a row (exact words, else the row sharing at least 60% of them), else `notes` |
| **Prompt** | System prompt says the passages are data, not instructions (the translation prompt's wording). The user message holds the question as a JSON string and each passage inside `<passage id="P3">` blocks; `<` and `>` in meeting text are escaped so nothing closes a block. Output: strict JSON schema, 700 tokens, 30 s timeout, one retry on a connection error only |
| **Checks (`check_reply`)** | JSON must parse and be an object (else 502). `found: false`, or no point left, is `none`. A cite is kept only if its id was sent (max 3 per point, deduplicated); a point with no cite is dropped; at most 6 points. Text goes through `plain()`: Markdown links keep their label, tags, URLs, `**`, backticks and leading `#`/`-`/`>` are removed, whitespace collapsed, cut at 400 (lead 300) with an ellipsis. An empty lead becomes "Here is what your meetings say." |
| **Cap** | `ASK_DAILY_LIMIT` model-calling questions per person in a rolling 24 hours, counted from `ai_runs` (`kind='ask'`, `user_id`, `created_at`). Failed calls count. Tasks questions and questions that retrieve nothing make no row. An advisory lock per account (`0x6173...|user_id`) is taken before the count and released at commit, so two concurrent questions cannot both pass; it also means one person's questions queue behind each other (up to ~60 s worst case) |
| **Failures** | Any model failure (error, cut off, not JSON, wrong shape) writes a `failed` ledger row (class name only) and is 502 `ask_failed`. The ledger row is committed before the error is raised |
| **Privacy** | The question and the answer are not stored, and nothing logs them: the only log line is `ask failed for user N (ErrorClassName)`. Pinned with `caplog` at DEBUG on every path (answer, failure, refusal, tasks) |
| **Where the answer's wording lives** | Only the model's `text` and `lead` (checked and made plain). Task points are code: `{title} ({due Fri 13 Nov, }{to do/in progress/done})` |

## Spec corrections (what the spec got wrong, and what was done)
1. **"No migration" was impossible.** `ai_runs.kind` is guarded by a check constraint (`ck_ai_runs_kind`, `app/models.py` `AI_RUN_KINDS`) that did not allow `'ask'`, so `ai_runs.record(kind="ask")` would fail with a CHECK violation (as `translate` and `transcribe` needed 0023 and 0025). Migration **0034** widens it. **Merge-order note:** the spec gives 0034 to Suggested tasks (C1); whichever merges second renumbers (two lines: the revision ids). `tests/test_migrations.py::test_0033_follows_0032_and_is_the_only_head` pinned the head to 0033 and was loosened to `test_0033_follows_0032` (`test_single_head` already checks one head), so the next migration does not break it again
2. **The error envelope is `{"error": {"code", "message"}}`**, the project's one shape, not the spec sketch's `{"detail": {...}}`.
3. **Tasks with nothing to show** are `kind: "tasks"` with a lead that says so and no points (not `none`), because "I could not find that in your meetings" is the wrong thing to tell someone with no tasks.
4. **A task point can have `cites: []`** (a task assigned to the person from a meeting they may not read: "assignee sees the quote only" rule; no link is made).
5. **`owner_name` is the owner's whole display name**; the FE takes the first name for the chip label. `shared` means "the meeting is shared with a team".
6. **The name chip** ("What did {Name} say?") **skips the asker's own name** (matched on their name, first name or the part of their email before `@`) and "Unknown speaker" / "Speaker N": asking what you yourself said was not the point of the chip. Spec 1.8 item 1 says "most frequent speaker"; this is a refinement, easy to drop (`frequent_speaker`).
7. **Without a date phrase, the tasks path lists open tasks only; with one it lists every status** assigned in that period (the spec did not say).

## What's involved
| Path | Change |
|---|---|
| `app/ask.py` (new) | Everything above. `answer()` is the front door; `scope()` the scope line; `retrieve()`, `check_reply()`, `date_hint()`, `transcript_rows()` are the testable parts |
| `app/api_ask.py` (new) | The two routes and their Pydantic models (`AskIn`, `AskOut`, `PointOut`, `CiteOut`, `ScopeOut`); registered in `app/api.py` |
| `app/config.py`, `app/ai_versions.py`, `app/ai_runs.py`, `app/models.py` | `ask_model`, `ask_daily_limit`; `ASK_PROMPT_VERSION`; `ask` in `_VERSION_OF_KIND` and `AI_RUN_KINDS` |
| `alembic/versions/0034_ai_runs_ask.py` | The check constraint |
| `docs/openapi.json`, `web/lib/api/schema.d.ts` | Regenerated (additions only) |
| `tests/test_ask.py` (new, 105 tests), `tests/test_migrations.py` | Below |
| `scripts/capture_ask_api.py`, `docs/fe-captures/ask-beyric/` | The live captures |
| `pyproject.toml` | `F811` ignored for `tests/test_ask.py` (fixtures imported by name, as in the other API tests) |

## Verification
* `tests/test_ask.py` (105): visibility (a teammate's private meeting never reaches passages, answer, cite or scope; a shared one does and its chip names the owner; an unshared one stops; an outsider's never); every model failure shape with a fake client (unknown ids, no cite, `found: false`, malformed JSON, wrong shape, cut off, error, retry on a connection error, no retry on other errors); bounds (400/300/6 points, 12 passages of 600 characters, 12 meetings); HTML/Markdown/links made plain; the cap (the 31st question 429, setting honoured, failed calls count, task and empty-retrieval questions free, rolling 24 hours, per person, limit 0); the question never in `caplog` (DEBUG) or the ledger; prompt injection (text in the user message inside a passage block, not in the system prompt, cannot close the block); `line` against the page's own splitting rules (seven shapes); date hints (twelve resolved, six ignored); routes, OpenAPI operation ids and statuses, login and CSRF.
* The route-enumeration and OpenAPI tests of `tests/test_api.py` (every route needs login, writes need CSRF, the committed `docs/openapi.json` is current), `tests/test_accounts.py`, `tests/test_admin.py`, `tests/test_team_visibility.py` and `tests/test_migrations.py` (0034 upgrade, downgrade, re-upgrade; one head) pass; `pytest --collect-only` over `tests/` collects 4297 tests (no import cycle); `ruff check .` and `ruff format --check .` clean (ruff 0.17.3).
* **Live capture** (`scripts/capture_ask_api.py`: real app under uvicorn, real database and migrations, 31 exchanges). **The model was faked** (no key or network); retrieval, cites, checks, the cap, the ledger and every error body are the real code.

## Operate / roll back
* Nothing to do on the server except the migration (runs with the normal deploy). Optional env: `ASK_MODEL`, `ASK_DAILY_LIMIT`. A restart of `api` applies them.
* **Cost:** one `gpt-6-luna` call per searching question: about 3 to 5 thousand tokens in, at most 700 out; at the cap of 30 a day that is bounded per person. Read it from `ai_runs where kind = 'ask'`.
* **Roll back:** revert the PR. If it is reverted without running the 0034 downgrade, the database keeps the wider check and any `ask` rows, and nothing breaks; the downgrade deletes the `ask` rows before it restores the narrow check. To switch the feature off without a revert, set `ASK_DAILY_LIMIT=0`: every searching question answers 429 and tasks questions still work.

## Follow-ups
* **Suggested tasks (C1):** this module reads tasks in three places (`_task_answer`, the task passages in `retrieve`, the scope count). When `tasks.suggested` lands, an unconfirmed suggestion must be excluded from all three (the spec's list of "does not count" places names the Board and counts; add Ask Beyric).
* **Decisions record (D):** decisions gain a verified `quote`; a decision with one should cite `transcript` with its line like a task does (`_meeting_passages`, `decision` branch).
* A GIN index on the full-text expression when a person has more than a few hundred meetings (spec 1.3).
* **Not stored on purpose:** history, feedback, the question. Privacy page copy is the lead's (two sentences in the PR body).
* `openai` logs request bodies at DEBUG level; production runs at INFO. Do not enable DEBUG on the `openai` logger in production: it would write the question and meeting text to the log (the translation path has the same exposure).
