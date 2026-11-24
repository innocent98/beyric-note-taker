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
| Accents folded in the stored text (review fix) | The function does `to_tsvector('simple', regexp_replace(normalize(text, NFKD), '[combining marks]', '', 'g'))` | **A real bug in the first version of this PR:** a typed word goes through `app.extraction.normalise` (NFKD, combining marks dropped), so "équipe" became `equip:*`, but the stored text kept "équipe" and never matched; French, Spanish, Portuguese and Yoruba ("Ọlá", dots below) were unsearchable by notes or transcript. The fold is in the IMMUTABLE function, so the index is built once on folded text. `normalize(text, NFKD)` (PG 13+), `regexp_replace` and `to_tsvector` with a named configuration are IMMUTABLE; `unaccent` is not (and needs an extension). Migration 0057 is edited in place (it was unmerged). |
| What the fold covers | The marks in U+0300 to U+036F and the supplements U+1AB0 to U+1AFF, U+1DC0 to U+1DFF, U+20D0 to U+20FF, U+FE20 to U+FE2F (`SEARCH_FOLD_MARKS` in `app/models.py`, repeated in the migration). **Known gap:** `normalise` drops every combining mark (about 930 characters, Python's `unicodedata.combining`), the function only these blocks. Hebrew points, Arabic vowel signs and Indic signs typed in a search are dropped by `normalise` but not removed from the stored text, so a word that carries them will not match. Nothing in the latin, greek or cyrillic scripts is affected. I did not list all 196 ranges because the list depends on the Unicode version of the Python in use and would make the test flaky. |
| Titles fold too | The title match compares `fold(title)` (the same NFKD and marks) with the folded text typed, in SQL and in Python (`meeting_search.fold`), so "equipe" finds the title "Équipe produit" and "équipe" finds "equipe design" | Same symptom as the stored text; cheap, since titles have no index and are scanned anyway. |
| Same vector as before, for text without accents | `coalesce(notes,'') \|\| ' ' \|\| coalesce(nullif(speaker_transcript,''), transcript, '')` against the old `concat_ws(' ', notes, coalesce(nullif(speaker_transcript,''), transcript))` | A test compares the two on eight cases without accents; another proves the accented text's vector equals the vector of its Python-folded text, also from a decomposed (NFD) spelling. |
| The title subquery is scoped (review fix) | `keyword_clause(text, readable)`: both subqueries now carry the caller's visibility condition (`visibility.readable_in`), written with `.correlate(None)` so each reads its own `meetings` | Before, the title subquery read every person's meetings. Behaviour is unchanged (the outer query already filtered), but a search no longer evaluates other people's titles or text. **Cost, honestly:** in the 4,000-meeting test the planner still reads the whole table for the title test (a sequential scan, 6 ms, because one person owns every row); with many users it can use the owner index (`ix_meetings_owner_user_id`) or the shared-team index for the cheap visibility test first. A test checks both subqueries carry the condition. |
| Index | `CREATE INDEX CONCURRENTLY ix_meetings_search_text ON meetings USING gin (meeting_search_text(...))` run in Alembic's `autocommit_block` | `CONCURRENTLY` so the worker's constant writes to `meetings` are not blocked while it builds. **The project had no such pattern before** (every earlier index is made inside the migration's transaction, which locks writes); `autocommit_block` is Alembic's own way and works here: it commits the function first, then builds. Cost on the 4,000-meeting scratch table (165 MB): **17.5 s to build, 13 MB**. With the real table (a few hundred meetings) expect about a second. |
| A failed build | A concurrent build that fails leaves an INVALID index of the same name. The migration starts with `DROP INDEX CONCURRENTLY IF EXISTS`, so running `alembic upgrade` again starts clean (tested by marking the index invalid and re-running) | Otherwise a second run would hit "already exists" or silently keep a useless index. |
| Frozen copy | The migration has its own copy of the function text; `app.models.MEETING_SEARCH_TEXT_SQL` is the app's. A test keeps them equal | A migration must not change with the app. |
| Tests build schemas with `create_all` | `Base.metadata` has a `before_create` event that creates the function, and `Meeting.__table_args__` has the same `Index`, so the test database has the function and the index | Without it every test that searches would fail (no function), and the model would drift from the migration (`test_migration_matches_models` passes). |
| The list's query shape | `keyword_clause` is `id IN (title/untitled matches) OR id IN (full-text matches)`, not one `OR` over the row | With the title `ILIKE` in the same `OR`, Postgres cannot use the full-text index and scans every row (measured: rare word 12.6 s even with the index). |

## Before and after (`scripts/time_meeting_search.py`, 4,000 meetings, 165 MB, medians of 3, milliseconds)

Same scratch data as the earlier SOP (synthetic text, 4,000 to 9,000 words a transcript; **the common words are in 95% of the meetings, which is the unkind case**). "PR 261 as shipped" is the one-`OR` query building the vector inline, from the first run. The "no index" and "with the index" columns are the **final code, with the accent fold** (the fold makes a vector costlier to rebuild: about 5 ms a meeting instead of 3, so "no index" got slower and the index gained more).

| Search | PR 261 as shipped (no index) | this code, no index | **this code, with the index** |
|---|---|---|---|
| list: rare word (3 rows) | 13,095 | 22,620 | **11** |
| list: nothing matches | 12,549 | 21,084 | **9** |
| Ask: rare word (3 rows) | n/a | 20,468 | **17** |
| Ask: nothing matches | n/a | 20,275 | **2** |
| list: a word in most meetings (`pricing`) | 185 | 19,376 | 18,490 |
| list: two common words | 14,811 | 18,979 | 18,392 |
| Ask: a word in most meetings | n/a | 42,654 | 39,312 |
| Ask: two common words | n/a | 38,262 | 35,769 |

Index build `CONCURRENTLY`: **22.7 s, 13 MB** at this size.

**The index does what it can: a selective search is now milliseconds for both readers.** `EXPLAIN (ANALYZE)` of the SQL each reader really sends (`scripts/time_meeting_search.py` prints all four plans), for `ingestion` (3 matches):

```text
list search, with the index:
  SubPlan 5
    ->  Bitmap Heap Scan on meetings meetings_2 (actual time=0.145..0.148 rows=3 loops=1)
          Recheck Cond: (to_tsvector('simple', ... ) @@ '''ingestion'':*'::tsquery)
          ->  Bitmap Index Scan on ix_meetings_search_text (actual time=0.119..0.119 rows=3 loops=1)
  Execution Time: 6.883 ms          (was 18,626 ms with a Seq Scan)

Ask candidates, with the index:
  ->  Bitmap Heap Scan on meetings (actual time=4.928..13.923 rows=3 loops=1)
        ->  Bitmap Index Scan on ix_meetings_search_text (actual time=0.012..0.012 rows=3 loops=1)
  Execution Time: 13.588 ms          (was 19,326 ms with a Seq Scan)
```

The test `test_ask_candidates_and_the_list_search_are_both_planned_on_the_index` does the same on every run: it captures the SQL from the real `ask.candidates` and `services.list_meetings` calls and EXPLAINs it with sequential scans switched off.

**What it does not fix, and an honest regression.** When a word is in most meetings the planner correctly ignores the index (a sequential scan is cheaper than fetching almost every row), and each row's vector is rebuilt: **18 to 44 s** here. Ask is worse (its `ts_rank` rebuilds the vector a second time). Two things to know:
- For the **list**, PR 261's one-`OR` shape stopped after 51 matches, so a common word took 185 ms; the split shape (needed for the index) computes every match first, so a common word is now as slow as everything else without the index. A search for a rare word is the common use (a name, a project) and went from 13 s to 13 ms; the common-word case went from fast to slow. On real text a word is in far fewer than 95% of meetings, and the cost is still about 3 ms per matching meeting.
- Ask's slow case is not new (already 26 s before the accent fold; the fold added about 5 ms a meeting without the index).

## The better fix, measured but NOT in this PR: a stored `tsvector` column

`scripts/time_search_stored_column.py` adds `meetings.search_tsv tsvector GENERATED ALWAYS AS (meeting_search_text(...)) STORED` and a GIN index on it, on the same data (median ms):

| Search | list | Ask |
|---|---|---|
| a word in most meetings | **70** | **82** |
| two common words | **72** | **223** |
| rare word | **3** | **0** |
| nothing matches | **3** | **0** |

Every case under 230 ms, for both readers, because nothing rebuilds a vector any more. The cost: `ALTER TABLE ... ADD COLUMN ... STORED` **rewrites the table under an ACCESS EXCLUSIVE lock** (51 s on the 4,000-meeting scratch table, about a second or two on a few hundred real meetings, during which the worker's writes wait), the table roughly **doubles in size** (165 MB to 328 MB here; the vector holds every word and its positions), the model needs the column `deferred` (or every `select(Meeting)` would load a vector), and a rewrite of notes or transcript recomputes it. It is still "a GIN index", but it is a schema change and a data change, so I did not substitute it for what was approved. **Recommendation: do it as the next PR (0058)**, replacing the expression index; the function, the split query and the tests here carry over unchanged. Say yes and I will build it.

## What's involved

| Path | Change |
|---|---|
| `alembic/versions/0057_meeting_search_index.py` | function, concurrent index, downgrade, leftover-invalid-index guard |
| `app/models.py` | `MEETING_SEARCH_TEXT_SQL` (with the fold), `SEARCH_FOLD_MARKS`, the `before_create` event, `ix_meetings_search_text` on `Meeting` |
| `app/meeting_search.py` | `document()` calls the function (typed `TSVECTOR`); `keyword_clause(text, readable)` split into two scoped subqueries, accent-folded title match, `fold()` |
| `app/ask.py` | **unchanged**: it already called `meeting_search.document()` |
| `tests/test_meeting_search_index.py` | migration, function, the fold (French, Spanish, Yoruba, Vietnamese, NFD), the title fold, Ask on accented text, scoped subqueries, the two plans |
| `docs/checklist/master-checklist.md-e` | **deleted**: a macOS `sed -i` backup (272 KB) that had been committed by mistake; no `-e`, `.orig`, `.bak` or `.rej` file is tracked on this branch or on 261 |
| `scripts/time_meeting_search.py`, `scripts/time_search_stored_column.py` | the before/after run and the candidate |
| `docs/sop/2026-11-24-export-formats-and-meeting-search.md` (copy from 261) | the speed follow-up now points here |

## Verification

Local, scratch Postgres 17 on port 55432 (Docker not running), only named files: `ruff check` and `ruff format --check` on everything; `tests/test_meeting_search_index.py` (14), `tests/test_export_formats_and_search.py` (49, the search's correctness with the split query), `tests/test_ask.py`, `tests/test_migrations.py` (includes `test_migration_matches_models` and the single head), `tests/test_skip_one_on_ones_migration.py`: **286 passed**. The migration was run for real up and down (0056 to 0057 to 0056 to head) and after a simulated half-built index. The timing runs above. **Not verified:** a real server's table size and build time; the deploy path (`alembic upgrade head` through `deploy/` with `autocommit_block`: the same Alembic call, but not run through the deploy script); every test file CI runs and I did not.

## Operate / roll back

- Deploy: the normal `alembic upgrade head`. The function and the index are made first; until the index finishes (about a second on the real table) searches work, slowly. **Server step before merge: none.** If the build is interrupted, run `alembic upgrade head` again (it drops the invalid leftover first). Check: `\di+ ix_meetings_search_text` and `select indisvalid from pg_index where indexrelid = 'ix_meetings_search_text'::regclass;` (must be `t`).
- Roll back: `alembic downgrade 0056` drops the index and the function; the old code (and this code) keep working, slowly. Code from before this PR does not call the function, so the migration can run ahead of the deploy and the deploy ahead of it only if the function exists first (it is made in the same migration, before the index).

## Follow-ups

- The stored `tsvector` column above (recommended).
- Ask's `ts_rank` still rebuilds the vector for every match; the stored column fixes that too.
- Rebase onto `main` after PR 261 merges and drop the cherry-picked commit.
