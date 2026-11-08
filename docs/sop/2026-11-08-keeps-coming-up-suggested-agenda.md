# SOP: Keeps coming up and Suggested agenda (audit B19, B20, U14)

Spec: `docs/superpowers/specs/2026-11-08-designer-v2-features-design.md`, sections 4 and 5 (approved by delegation, 2026-11-08). **PR 1 (backend):** branch `feature/keeps-agenda-backend` (number in the PR list). **PR 2 (screens):** `feature/keeps-agenda-screens` (section "PR 2: the screens" below, added when it ships). **Migration 0037.** FE guide: `docs/fe-integration-guide-series-topics.md`; captures `docs/fe-captures/series-topics/` (`scripts/capture_series_topics_api.py`).

## What shipped (PR 1)
1. **Migration 0037:** table `series_topics (id, series_id, position, label, agenda, reason, meeting_ids jsonb, source_meeting_ids jsonb, computed_at)` (`reason` check: `no_decision | no_owner | not_final`; cascades with the series); three columns on `series` that drive the job (`topics_signature`, `topics_checked_at`, `topics_attempts`); `ck_ai_runs_kind` widened to allow `topics` (the `ask` migration 0034 had not covered it). No row is rewritten. Downgrade deletes the `topics` ledger rows first so the narrow check can return.
2. **The topics job** (`app/topics.py`, run from `worker.background_jobs`): one model call per series over its last five ready meetings **shared with the series' team** (each one's Key points and decisions, size-bounded), `TOPICS_PROMPT_VERSION = "topics-v1"`, setting `topics_model` (env `TOPICS_MODEL`, default `gpt-6-luna`), ledger kind `topics`. The reply is checked in code before anything is stored: JSON must parse; a topic needs a plain label (60), a plain agenda sentence (100), a reason from the list and at least two distinct valid meeting numbers (unknown numbers dropped); at most five topics; repeated labels dropped; a topic whose words overlap an **approved** decision of the series by more than half is dropped. `series_topics` is replaced as a set. A failed call or unusable reply leaves the old rows.
3. **`GET /api/series/{id}`** gains `topics` (read from storage; no model call) and `suggested_agenda` (built in code, spec 5). No new route. `docs/openapi.json` and `web/lib/api/schema.d.ts` regenerated.
4. **The Suggested agenda lines** (`series.agenda_suggestions`), in the designer's order: each visible topic's agenda sentence; "Review the overdue task" / "Review the N overdue tasks" (unfinished tasks with a due date before today in the viewer's zone); "Settle 1 open question carried over" / "Settle N open questions carried over"; "Confirm 1 suggested task from last time" / "Confirm N suggested tasks from last time" (counted over the series' **last** meeting the viewer may read, so the words are true). Suggested tasks are otherwise in no count.

## Why
The designer's Series page shows two blocks nothing computed: topics that keep coming up without a decision, and an agenda built from them and from work already tracked. Topics need a judgement about meaning ("the same topic"), so a model call; the agenda does not, so it is code.

## How: decisions and trade-offs
| Topic | Decision |
|---|---|
| When it runs | In the worker, never on a page load. `workspace.run_extraction` marks the series stale (`topics_checked_at = null`) when a meeting of it finishes extraction (`done` or `failed`), and `series.join`/`attach` do when a meeting joins or leaves. A claim (`claim_next`) stamps `topics_checked_at`, commits, and then the call is made with nothing held. A series not marked is looked at again every 6 hours (an unchanged one costs no call), which covers a meeting that was unshared or deleted |
| One call per change | `topics_signature` is a hash of the prompt version, the model, the exact input and the series' approved decision texts. If it equals the stored one there is no call. A series with fewer than two ready *shared* meetings makes no call and has its topics cleared. A series with a meeting still `pending` or `running` extraction waits (its decisions are not final): the extraction marks it stale when it finishes |
| Failure | A failed call is in the ledger (`status failed`, error class name only, no text) and the old rows stay. The series is tried again after 30 minutes, three times in a row (`topics_attempts`), then waits **24 hours** (`ATTEMPTS_RESET`) and may try again, so a provider outage does not freeze it until its next meeting; a stale mark resets the count too. The compare-and-set on `topics_checked_at` means a mark made while the call ran is not lost, and **the topic rows are written only when that update matched exactly one row** (a stale reply never overwrites newer state; the series is looked at again at once) |
| **Visibility (deviation from the spec, on purpose)** | Spec section 4 said the job reads "the Series page's own set for the owner". An Opus review (#141) showed that put the owner's *private* meetings into the model's input, and nothing ties a label's words to the meetings it cites, so private wording could reach a teammate. **Decision (lead, to be told to Adebayo): the job reads only meetings every reader may read**, i.e. `Meeting.shared_team_id == series.team_id` (ready, series members, live owners) and nothing else (`topics.source_meetings`). Cost: the owner's private meetings no longer feed topics, not even for the owner. A series is always a team's, so there is no owner-only case. Tests: `test_the_job_reads_only_meetings_shared_with_the_team` (one private meeting of the owner and one of a teammate: neither their wording nor their decisions are in the model input; once shared it is), `test_a_series_whose_only_meetings_are_private_is_not_asked` |
| Read-time guard (unsharing later) | The job stores every meeting it showed the model (`series_topics.source_meeting_ids`). `series.topics_for` shows a topic only to a viewer who can **still read every cited meeting and every shown meeting** (ready, in the series, `visible_meetings(viewer)`); one unshared, moved or deleted hides the topic until the worker recomputes. The count and dates cover the cited meetings, all readable. Sharing, unsharing (`api.py`) and a decision's change or hide (`workspace.py`) mark the series stale. Tests: `test_a_topic_is_shown_only_if_the_viewer_reads_every_meeting_behind_it`, `test_a_topic_the_model_saw_a_meeting_for_is_hidden_when_that_meeting_is_unshared`, `test_sharing_unsharing_and_decisions_mark_the_series_stale` |
| No model on read | The page reads `series_topics` rows. Test: the route runs with `topics._call` and `topics.OpenAI` made to raise |
| Meeting text is data | The user message holds meeting text between `<meeting>` blocks with `&`, `<` and `>` escaped (the prompt names all three escapes); the system prompt says all of it is data, not instructions. Titles are not sent; only dates |
| Size | At most 12 Key points of 300 characters and 10 decisions per meeting, one meeting's block at most 2,600 characters, cut on whole lines (the discussion's budget cannot crowd out the decisions). Output at most 900 tokens, timeout 45 s with one retry on a transport error |
| Plain text | Label and agenda go through the same plain-text rule as Ask (no Markdown, HTML or link, bounded). Stored as text, rendered as text |
| `line` and `dates_text` server-side | The three sub-line templates and the date list are written by the server in the viewer's zone, so the FE draws what it is given and cannot count the wrong meetings |
| Agenda lines | `kind` + `text` per line. "Added" is the FE's exact-text comparison with the live agenda; the existing `POST`/`DELETE` agenda routes are used as they are |

## What's involved
| Place | Where |
|---|---|
| Migration | `alembic/versions/0037_series_topics.py` |
| Models | `app/models.py` `SeriesTopic`, `Series.topics_*`, `AI_RUN_KINDS` |
| The job | `app/topics.py`; `app/worker.py` (`find_topics`, `background_jobs`); `app/workspace.py` (the stale mark after an answered extraction); `app/series.py` (`mark_topics_stale`, `topics_for`, `topic_line`, `topic_dates`, `suggested_task_count`, `agenda_suggestions`, and the mark in `join` and `attach`) |
| Words | `app/words.py` (the stemmer and stop words, moved out of `app/ask.py`, which imports them; behaviour unchanged; used for the overlap with approved decisions) |
| Notes parsing | `app/extraction.py` `key_points` (the "Discussion" of the spec: the notes' Key points section) |
| API | `app/api_series.py` `_page`, `app/series_schemas.py` (`TopicOut`, `AgendaSuggestion`, `SeriesPage.topics`, `.suggested_agenda`) |
| Config and ledger | `app/config.py` `topics_model`, `app/ai_versions.py`, `app/ai_runs.py` |
| Tests | `tests/test_topics.py` (the checks, the job, visibility, the page, the agenda lines), `tests/test_migrations.py` (0037) |
| Docs | `docs/fe-integration-guide-series-topics.md`, `docs/fe-captures/series-topics/`, `scripts/capture_series_topics_api.py` |

## Verification
* `tests/test_topics.py`: the checks (bad JSON, bad shape, one meeting, the same meeting twice, unknown numbers, bad reason, empty fields, more than five, a repeated label, plain text, the decision overlap); the job (one call stores the topics and a ledger row; the input is the last five meetings' Key points and decisions, size-bounded with whole blocks; meeting text escaped and the prompt says it is data; an unchanged series costs no call; a change replaces the set; a failed call keeps the old rows, records a failed row, retries three times then waits; a bad reply of four kinds; an empty valid answer clears; fewer than two meetings makes no call and clears; waits for a pending extraction; the extraction and a join mark the series stale; the worker runs it and a failure cannot stop the other jobs); **visibility** (the job reads only meetings shared with the team: private meetings of the owner and of a teammate are absent from the model input, wording and decisions both, and a series of only private meetings is not asked; a viewer sees a topic only if they can read every cited and every shown meeting; unsharing a shown meeting hides the topics; sharing, unsharing and a decision change mark the series stale; a meeting moved out of the series stops counting; another team gets 404; topics go with their series); **robustness** (after three failures the series retries after a day; a reply that arrives after a newer change is not written; `&` escaped); "Confirm N suggested tasks" counts the last meeting only; the page reads storage and never calls the model; the Suggested agenda lines, plurals, counts limited to what the viewer sees, suggested tasks in no other count; add and remove through the existing routes.
* `tests/test_migrations.py`: one head, 0037 follows 0036, up/down round trip, the `topics` kind, the reason check, cascade, the ledger rows removed on downgrade.
* The whole of `tests/test_accounts.py` (IDOR and route enumeration), `tests/test_api.py` (OpenAPI), the series, workspace, suggested-tasks, decisions and ask suites; `ruff check`, `ruff format --check`, `scripts/export_openapi.py --check`; `pytest --collect-only` over all of `tests/`.
* A live run over real HTTP against the real app and migrations (`scripts/capture_series_topics_api.py`): 21 captures. **The model reply there is a stand-in** (no OpenAI key in the session), and the job was called in-process, not by a separate worker.

## Operate / roll back
* **Deploy:** migration 0037 runs on deploy (`alembic upgrade head`): a new table, three columns on `series` (one with a default), a check constraint swap on `ai_runs`; brief locks. Env `TOPICS_MODEL` is optional (default `gpt-6-luna`). After the deploy every series looks stale (`topics_checked_at` is null), so the worker looks at each once: a series with at least two ready meetings makes **one call** (the cost of the first pass is one call per such series, a few thousand input tokens each). Nothing to switch on.
* **Cost after that:** one call per series per meeting whose notes are read, and none for a series whose input did not change.
* **Roll back:** revert the code, then `alembic downgrade 0036` (drops `series_topics`, the three columns and the `topics` ledger rows). Rolling back only the code is safe: the new columns and table are ignored by old code, the old CHECK is wider than old code needs.

## Follow-ups
* **Review findings accepted, not built (Opus review of #141):** (5) a person can reset the failure count by re-attaching a meeting, which matters only while replies keep failing; there is no global or daily cap on topics calls (cost is at most one call per background pass, about four a day per series plus one per change): add a cap like `ASK_DAILY_LIMIT` if the pilot grows. (6) A topics call can hold the worker's meeting loop for about 90 s (45 s timeout and one retry), the same as extraction; move it to a thread like translation if it matters.
* Still only on the 6-hour look: extraction giving up on a stuck run, the backfill's `skipped` path and an account being deleted (`app/deletion.py`). The topic rows then stay until the next look; the read guard hides a topic whose meetings are gone.
* Real-model quality of the topics is unchecked until the first live series (stand-in in the captures); the AI1 eval does not score it. `topics_model` can be changed without a deploy of code.
* A topic is dropped by word overlap with approved decisions (a crude stem). A decision approved *after* the call does not remove a stored topic until the series is recomputed (the next stale mark or the 6-hour look: it changes the input, so a call is made).
* The job runs inside `background_jobs`, so a 45-second call (more with the retry) delays the meeting polling pass by that much; extraction already works the same way. If the pilot grows, move it to its own thread like translation.
* The Series cards (list) do not show topics; only the page does.

---

# PR 2: the screens (parity D:698-725, script D:1559-1565 and 1886-1915)

Stacked on PR 1 (`feature/keeps-agenda-backend`); retarget to `main` once PR 1 is merged. Web only: no migration, no API change.

## What shipped (PR 2)
`web/components/app/series-view.tsx` draws the two blocks between the score tiles and "Unfinished from last time", as the designer's file orders them.

* **Keeps coming up**: heading, "Topics discussed in more than one meeting without a decision.", one amber row per `topics[]` (refresh icon, bold `label`, the server's `line` in amber, `dates_text` in grey). **Absent** when `topics` is empty: no heading, no spinner, no empty state (U14).
* **Suggested agenda**: heading, "Built from repeat topics, overdue work, open questions and tasks to confirm.", numbered rows in the order the server sends them, each with **Add to agenda** / **Added** (teal fill, `aria-pressed`). Absent when `suggested_agenda` is empty.
* **Add to agenda** posts the line's exact `text` to `POST /api/series/{id}/agenda`; the item lands in "Other agenda items". The row shows **Added** when a live `agenda[]` item has exactly the same text, and a second click removes that item (`DELETE .../agenda/{id}`). A double click does nothing while a click is in flight. Refusals (`agenda_full`, 403) show in the page's error line and leave the row as it was.
* `web/lib/api/types.ts` (`SeriesTopic`, `AgendaSuggestion`), `web/styles/pages.css` (`.sr-topic*`, `.sr-sug*`), token `--warning-pale` (the designer's `#FFFBF2`).

## Parity (1440 / 768 / 390, design on the left, page on the right)
Shots in `docs/ui/parity/after/app-series-{1440,768,390}.jpg`, `app-series-friday-*` (one topic, "approval rules not final"), `app-series-added-*` (the first row pressed: the design turns it into "Added", ours too) and `app-series-notopics-*` (Next alone), made with `web/scripts/parity-shots.mjs` against `web/scripts/mock-api.mjs`, which now serves the designer's own TOPICS sample (D:1559-1565) for series 1 and 3 and builds the agenda lines from the page as the server does (`--topics none` removes the topics; `/__mock/reset` restores the agenda).

| Piece | Design | Page |
|---|---|---|
| "Keeps coming up" heading (15 px / 600), grey sub-line | D:699-700 | same sizes and colours |
| Amber topic row: border `#F3DDB0`, fill `#FFFBF2`, radius 10, padding 10 12, icon 16 px `#9A5B00` | D:704-710 | same (tokens `--warning-line`, `--warning-pale`, `--warning`) |
| Topic label 550, sub-line 12 px `#7A4A00`, dates 12 px `#6E7772` | D:709 | same |
| "Suggested agenda" heading and sub-line | D:713-714 | same |
| Rows: 22 px number chip (radius 6, `#EEF1F0`, 11 px / 600), text, 28 px button (radius 7, 12 px / 550), rows divided by `#F0F2F1` | D:717-722 | same |
| Added: fill `#E6F1EE`, border `#BFDCD5`, text `#0A5248`, label "Added" | D:1904 | same (`--teal-tint`, `--teal-border`, `--teal-deep`) |
| Placement: after the three tiles, before "Unfinished from last time" | D:701-725 | same (asserted by a test) |
| The words of the four agenda lines | D:1892-1895 | the server writes them with the same words and plurals |

The sample words in the shots differ from the design's (the mock's page is the owner's view of the captured "Beyric Monday planning"): the blocks, their order, sizes, colours and states are what is compared.

## Lead-designed, not in the designer's file
| Piece | What we did |
|---|---|
| Phone (≤ 480 px, U7) | The "Add to agenda" / "Added" button drops under the row text, indented past the number chip, so a long line keeps its width. Tablet (768) is the desktop layout |
| An Added line the person may not remove (`can_remove` false) | The button is disabled with the title "Only the person who added it, or an owner or admin, can remove this." (the design has no permissions) |
| A failed add or remove | The page's existing red error line (the server's own words for `agenda_full`); the row keeps its state |
| Loading state (U14) | None: the blocks are absent until the server sends rows |
| A viewer who may not read everything behind a topic | Nothing is drawn about it: the server sends fewer topics (none, when a meeting behind them was unshared). Never a "hidden" notice |

## Verification
* `web/tests/series-view.test.tsx` (new block, driven by the real captures `docs/fe-captures/series-topics/13`, `14` and `21`): both blocks in order after the tiles, the three sub-line templates and dates as sent, numbered rows in the received order, absent when empty (no heading, no spinner), a teammate's page (same topics, no "Confirm" line) and a teammate after an unshare (neither block, nothing said), Add posts the exact text and the row becomes Added and the item is in "Other agenda items", a second click removes it, Added at once for an existing item, disabled when it may not be removed, refusal shown and the row unchanged. Older captures get the two empty fields the API now always sends.
* `npm run lint`, `typecheck`, `vitest` (all 61 files), the design guards (the new colour is a token).
* Parity shots as above; the mock and the page agree with the captured shapes.

## Follow-ups
* The Series cards (list) do not show topics.
* Real-model topics are unchecked until the first live series; the mock's topics are the designer's sample.
