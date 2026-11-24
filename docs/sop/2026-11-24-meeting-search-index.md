# SOP: a full-text index for meeting search (migration 0057)

**What shipped.** Migration **0057**: the SQL function `meeting_search_text(notes, speaker_transcript, transcript)` and a GIN index `ix_meetings_search_text` on a call to it, built `CONCURRENTLY`. Both readers of meeting text, **Ask Beyric's `candidates()`** and the **Meetings list's `q` search** (PR 261), now call that one function, and the list's `keyword_clause` was split so the index can be used. Branch `feature/meeting-search-index`; PR: see the checklist entry. Alembic head: **0057**. Adebayo approved the index on 2026-11-24, to be built after #260 (migration 0056) merged.

**This PR is stacked on PR 261.** `app/meeting_search.py` and the list's `q` search exist only in 261, which is still open, so this branch is `origin/main` (with #260) plus 261's commit cherry-picked, then this change. The first commit is 261's (same patch, cherry-picked); after 261 merges, rebase this branch onto `main` and Git drops it. Review only the second commit.

## Why

`docs/sop/2026-11-24-export-formats-and-meeting-search.md` measured the search at about **3 ms per meeting the caller may read** (13 s at 4,000 meetings, 0.9 s at 300), because every search rebuilt each meeting's `tsvector` with `to_tsvector('simple', concat_ws(...))`. Ask Beyric's `candidates()` does the same on every question (and again inside `ts_rank`), so it was just as slow.

## How, and what was decided

| Decision | Choice | Why |
|---|---|---|
| One function, called by both readers | `meeting_search_text(notes, speaker_transcript, transcript)`, `LANGUAGE sql IMMUTABLE PARALLEL SAFE`. `meeting_search.document()` returns a call to it; `ask.candidates` already called `document()`, so **the two expressions are now textually identical because there is one copy of the SQL** | An index is used only when the query's expression matches the index's. Before this the expression was written once, in Python (`document()`), so Ask and the list already shared it; what was missing was an IMMUTABLE function to index (`concat_ws` is only STABLE, so `CREATE INDEX` refuses it). |
| The function inlines | A plain one-statement SQL function: Postgres inlines it into the query, so the plan shows the body, and the index matches that inlined body (visible in the plans below). **Do not make it plpgsql or add `SECURITY DEFINER`/`SET`: it would stop inlining and the index would stop being used.** | Found by `EXPLAIN`. |
| Same vector as before | `coalesce(notes,'') || ' ' || coalesce(nullif(speaker_transcript,''), transcript, '')` against the old `concat_ws(' ', notes, coalesce(nullif(speaker_transcript,''), transcript))` | A test compares the two on eight cases (nulls, empty strings, accents, apostrophes). |
| Index | `CREATE INDEX CONCURRENTLY ix_meetings_search_text ON meetings USING gin (meeting_search_text(...))` run in Alembic's `autocommit_block` | `CONCURRENTLY` so the worker's constant writes to `meetings` are not blocked while it builds. **The project had no such pattern before** (every earlier index is made inside the migration's transaction, which locks writes); `autocommit_block` is Alembic's own way and works here: it commits the function first, then builds. Cost on the 4,000-meeting scratch table (165 MB): **17.5 s to build, 13 MB**. With the real table (a few hundred meetings) expect about a second. |
| A failed build | A concurrent build that fails leaves an INVALID index of the same name. The migration starts with `DROP INDEX CONCURRENTLY IF EXISTS`, so running `alembic upgrade` again starts clean (tested by marking the index invalid and re-running) | Otherwise a second run would hit "already exists" or silently keep a useless index. |
| Frozen copy | The migration has its own copy of the function text; `app.models.MEETING_SEARCH_TEXT_SQL` is the app's. A test keeps them equal | A migration must not change with the app. |
| Tests build schemas with `create_all` | `Base.metadata` has a `before_create` event that creates the function, and `Meeting.__table_args__` has the same `Index`, so the test database has the function and the index | Without it every test that searches would fail (no function), and the model would drift from the migration (`test_migration_matches_models` passes). |
| The list's query shape | `keyword_clause` is `id IN (title/untitled matches) OR id IN (full-text matches)`, not one `OR` over the row | With the title `ILIKE` in the same `OR`, Postgres cannot use the full-text index and scans every row (measured: rare word 12.6 s even with the index). |

## Before and after (`scripts/time_meeting_search.py`, 4,000 meetings, 165 MB, medians of 3, milliseconds)

Same scratch data as the earlier SOP (synthetic text, 4,000 to 9,000 words a transcript; **the common words are in 95% of the meetings, which is the unkind case**; `rows` is what came back).

| Search | PR 261 as shipped (no index) | this code, no index | **this code, with the index** |
|---|---|---|---|
| list: rare word (3 rows) | 13,095 | 12,705 | **13** |
| list: nothing matches | 12,549 | 13,810 | **12** |
| Ask: rare word (3 rows) | n/a (not measured) | 13,337 | **16** |
| Ask: nothing matches | n/a | 13,768 | **3** |
| list: a word in most meetings (`pricing`) | 185 | 12,925 | 15,038 |
| list: two common words | 14,811 | 12,681 | 12,835 |
| Ask: a word in most meetings | n/a | 25,949 | 28,906 |
| Ask: two common words | n/a | 25,203 | 26,153 |

**The index does what it can: a selective search is now milliseconds for both readers.** `EXPLAIN (ANALYZE)` of the SQL each reader really sends (`scripts/time_meeting_search.py` prints all four plans), for `ingestion` (3 matches):

```text
list search, with the index:
  SubPlan 5
    ->  Bitmap Heap Scan on meetings meetings_2 (actual time=0.145..0.148 rows=3 loops=1)
          Recheck Cond: (to_tsvector('simple', ... ) @@ '''ingestion'':*'::tsquery)
          ->  Bitmap Index Scan on ix_meetings_search_text (actual time=0.119..0.119 rows=3 loops=1)
  Execution Time: 10.863 ms          (was 13,790 ms with a Seq Scan)

Ask candidates, with the index:
  ->  Bitmap Heap Scan on meetings (actual time=4.928..13.923 rows=3 loops=1)
        ->  Bitmap Index Scan on ix_meetings_search_text (actual time=0.012..0.012 rows=3 loops=1)
  Execution Time: 13.988 ms          (was 14,147 ms with a Seq Scan)
```

The test `test_ask_candidates_and_the_list_search_are_both_planned_on_the_index` does the same on every run: it captures the SQL from the real `ask.candidates` and `services.list_meetings` calls and EXPLAINs it with sequential scans switched off.

**What it does not fix, and an honest regression.** When a word is in most meetings the planner correctly ignores the index (a sequential scan is cheaper than fetching almost every row), and each row's vector is rebuilt: **12 to 29 s** here. Ask is worse (its `ts_rank` rebuilds the vector a second time). Two things to know:
- For the **list**, PR 261's one-`OR` shape stopped after 51 matches, so a common word took 185 ms; the split shape (needed for the index) computes every match first, so a common word is now as slow as everything else without the index. A search for a rare word is the common use (a name, a project) and went from 13 s to 13 ms; the common-word case went from fast to slow. On real text a word is in far fewer than 95% of meetings, and the cost is still about 3 ms per matching meeting.
- Ask's slow case is not new (the same 26 s before this PR).

## The better fix, measured but NOT in this PR: a stored `tsvector` column

`scripts/time_search_stored_column.py` adds `meetings.search_tsv tsvector GENERATED ALWAYS AS (meeting_search_text(...)) STORED` and a GIN index on it, on the same data (median ms):

| Search | list | Ask |
|---|---|---|
| a word in most meetings | **64** | **76** |
| two common words | **65** | **153** |
| rare word | **3** | **1** |
| nothing matches | **3** | **0** |

Every case under 160 ms, for both readers, because nothing rebuilds a vector any more. The cost: `ALTER TABLE ... ADD COLUMN ... STORED` **rewrites the table under an ACCESS EXCLUSIVE lock** (31 s on the 4,000-meeting scratch table, about a second or two on a few hundred real meetings, during which the worker's writes wait), the table roughly **doubles in size** (165 MB to 329 MB here; the vector holds every word and its positions), the model needs the column `deferred` (or every `select(Meeting)` would load a vector), and a rewrite of notes or transcript recomputes it. It is still "a GIN index", but it is a schema change and a data change, so I did not substitute it for what was approved. **Recommendation: do it as the next PR (0058)**, replacing the expression index; the function, the split query and the tests here carry over unchanged. Say yes and I will build it.

## What's involved

| Path | Change |
|---|---|
| `alembic/versions/0057_meeting_search_index.py` | function, concurrent index, downgrade, leftover-invalid-index guard |
| `app/models.py` | `MEETING_SEARCH_TEXT_SQL`, the `before_create` event, `ix_meetings_search_text` on `Meeting` |
| `app/meeting_search.py` | `document(meeting=Meeting)` calls the function (typed `TSVECTOR`, takes an alias); `keyword_clause` split into two subqueries |
| `app/ask.py` | **unchanged**: it already called `meeting_search.document()` |
| `tests/test_meeting_search_index.py` | migration, function, the two plans |
| `scripts/time_meeting_search.py`, `scripts/time_search_stored_column.py` | the before/after run and the candidate |
| `docs/sop/2026-11-24-export-formats-and-meeting-search.md` (copy from 261) | the speed follow-up now points here |

## Verification

Local, scratch Postgres 17 on port 55432 (Docker not running), only named files: `ruff check` and `ruff format --check` on everything; `tests/test_meeting_search_index.py` (8), `tests/test_export_formats_and_search.py` (49, the search's correctness with the split query), `tests/test_ask.py`, `tests/test_migrations.py` (includes `test_migration_matches_models` and the single head), `tests/test_skip_one_on_ones_migration.py`: **271 passed**. The migration was run for real up and down (0056 to 0057 to 0056 to head) and after a simulated half-built index. The timing runs above. **Not verified:** a real server's table size and build time; the deploy path (`alembic upgrade head` through `deploy/` with `autocommit_block`: the same Alembic call, but not run through the deploy script); every test file CI runs and I did not.

## Operate / roll back

- Deploy: the normal `alembic upgrade head`. The function and the index are made first; until the index finishes (about a second on the real table) searches work, slowly. **Server step before merge: none.** If the build is interrupted, run `alembic upgrade head` again (it drops the invalid leftover first). Check: `\di+ ix_meetings_search_text` and `select indisvalid from pg_index where indexrelid = 'ix_meetings_search_text'::regclass;` (must be `t`).
- Roll back: `alembic downgrade 0056` drops the index and the function; the old code (and this code) keep working, slowly. Code from before this PR does not call the function, so the migration can run ahead of the deploy and the deploy ahead of it only if the function exists first (it is made in the same migration, before the index).

## Follow-ups

- The stored `tsvector` column above (recommended).
- Ask's `ts_rank` still rebuilds the vector for every match; the stored column fixes that too.
- Rebase onto `main` after PR 261 merges and drop the cherry-picked commit.
