# SOP: more export formats, Tasks CSV and keyword search on the Meetings list

**What shipped.** Three small gaps from the Claude Meetings comparison (`docs/competitive/claude-meetings-gap-analysis-2026-11-24.md`, section 4 rows 6 and 8), in one PR: (1) `GET /api/meetings/{id}/export` takes **`format=md`** and **`format=txt`** (Markdown and plain text of the notes, with the transcript when asked, in en/fr/es), (2) a new **`GET /api/tasks/export?format=csv`** and an **Export CSV** button on the Tasks screen, (3) an optional **`q`** keyword search on `GET /api/meetings` and a **search box** above the Meetings list. Branch `feature/export-formats-and-meeting-search`; PR: see the checklist entry. **No migration** (Alembic head unchanged at 0055), no new setting, no model call.

## Why

The gap analysis found that teams moving notes into Notion, Obsidian or a spreadsheet had only PDF and Word, and that finding an old meeting meant scrolling (search existed only in Decisions). Both were marked small. Adebayo chose to close the small gaps.

## How, and what was decided

| Decision | Choice | Why (and what was rejected) |
|---|---|---|
| Markdown and text bypass the renderer | `app/export_text.py` builds them in the request; the route returns before `export.build_file`, so they never touch the one-at-a-time semaphore and **never answer `export_busy`** | The child process exists for WeasyPrint's memory and hang risk; text has neither. A test holds the semaphore and proves md/txt still answer 200 while the renderer is replaced by a function that fails if called. |
| What Markdown contains | `# title`, the meta line, **the stored notes verbatim**, then `## Transcript` with one paragraph per turn (`**Name:** text`), then an italic footer | Notes are already Markdown; round-tripping through the block model would lose nothing but risk differences. A transcript line that begins like Markdown (`#`, `-`, `1.`) is backslash-escaped so it cannot become a heading or list; speaker names are escaped. **Rejected:** rendering Markdown from `NotesDocument` (a second source of truth for the same text). |
| What text contains | Built from the `NotesDocument` the PDF and Word renderers use: title underlined with `=`, sections with `-`, bullets as `- `, numbers as `1.`, nested lists indented, quotes `> `, code indented four spaces, bold/italic dropped, links as `text (address)` | Same document, so the four formats cannot drift. Transcript is `Name: text`. |
| Language | Same `MeetingTranslation` lookup and the **same 409 `translation_missing`** rule as PDF and Word, unchanged code path (the lookup runs before the format branch) | One rule. The words the app adds (`Transcript`, the footer) stay English, as in PDF and Word (follow-up). |
| Tasks CSV rows | `tasks.list_tasks` (the function behind the Tasks list route, so `tasks.visible` and `X-Team-Id` scoping are the same), paged 500 at a time until done (cap 20 pages), then `present()` for the assignee, meeting reference and meeting readability | **No second visibility rule.** The meeting columns are blank for a meeting the caller may not read, exactly like the screen. The date is the caller's stored zone's day (UTC when none). Suggested and deleted tasks are not in it, as on the board. |
| Tasks CSV scope | The whole team board, **not** the screen's chips and tabs (they are client-side filters; the API has none for them) | The brief said "exactly the tasks the Tasks page shows the caller for the active team". **Decision to check:** if you want the button to export the *filtered* view, the page would send `assignee`, `project` and `sprint` (the route would need to accept them). |
| CSV safety | `csv` module; a cell beginning with `=`, `+`, `-`, `@`, tab or CR gets a single quote in front (**also names and meeting titles, not only task titles**); UTF-8 with a BOM | The quote is visible in Excel and Sheets (documented in the FE guide). Rejected: relying on the spreadsheet's own prompts. |
| Search: where | `app/meeting_search.py` holds the `to_tsvector('simple', notes ++ transcript)` expression and the prefix `tsquery`; **`ask.candidates` now calls the same two helpers** (identical SQL, its tests pass unchanged) | The brief asked for one shared function if Ask's behaviour does not change. Ask joins stems with OR and ranks; the list joins them with **AND** (a search box narrows; "pricing roadmap" means both), through `prefix_query(words, " & ")`. |
| Search: what matches | Title contains the text (ILIKE with `%`, `_` and `\` escaped), **or** a ready meeting whose notes/transcript contain every stem (words via `app/words.py`: filler words such as "meeting" and "last" are dropped, a search of only those matches titles only), **or** for an untitled meeting the code in its shown name ("Google Meet abc-defg-hij") | The untitled rule is an addition to the brief: without it the visible name is not searchable. Meetings that are not `ready` match on title only (their text can be half written). |
| Search: where it sits in the query | One more `where` on `services.list_meetings`, after the visibility rule and the active-team narrowing, before ordering and `before_id` | It can only remove rows from what the caller may read; paging, dates, ordering unchanged. A test proves another account's meeting and a meeting of another team of the caller's are never found. |
| Search: validation | `services.parse_keywords`: trimmed, spaces squeezed, 2 to 100 characters else **422 `invalid_query`** ("Search needs 2 to 100 characters.") including an empty `q=` | As the brief; the page validates before sending so a person never sees it. |
| Search UI | A `role="search"` form with a `Field` (search icon), Search and Clear search buttons, inside the existing date-filter card; the text is in the address (`?q=`); heading `Meetings matching "x"`; empty state "No meetings match."; "Show older meetings" sends the same `q` | Existing components and tokens only. |

**LEAD-DESIGNED pieces (the designer's files have none), to show the designer:** the search box and its two buttons, the heading and the empty state; the **Export CSV** button on the Tasks header (secondary `Button` with the download icon, "Preparing..." while it runs, a toast when done); the **Markdown** and **Plain text** rows in the Download menu (same row layout, two new tile colours `dl-tile-md` and `dl-tile-txt` from existing tint tokens).

## What's involved

| Path | Change |
|---|---|
| `app/export_text.py` (new) | `build_markdown`, `build_text`, `TEXT_FORMATS` |
| `app/api.py` (`export_meeting`, `list_meetings`, `_file_response`) | `format` gains `md` and `txt` (early return before the renderer); `q` parameter |
| `app/tasks_csv.py` (new), `app/api_tasks.py` (`export_tasks`) | the CSV builder and `GET /api/tasks/export` (declared before `/tasks/{id}`) |
| `app/meeting_search.py` (new), `app/services.py` (`parse_keywords`, `list_meetings(keywords=)`), `app/ask.py` | shared full-text helpers; the list's `q`; Ask uses the helpers (no behaviour change) |
| `web/components/app/download-menu.tsx`, `web/styles/pages.css` | two rows, two tile colours |
| `web/components/app/tasks-view.tsx`, `web/lib/api/client.ts` | Export CSV; `api.tasks.exportCsv`; `/api/tasks/export` is team-scoped in `isTeamScoped`; `meetings.list` takes `q` |
| `web/lib/meeting-search.ts` (new), `web/components/app/meeting-filter.tsx`, `meeting-list.tsx`, `web/app/page.tsx` | the search box, address, heading, empty state |
| `docs/openapi.json`, `web/lib/api/schema.d.ts` | regenerated (see below) |
| `tests/test_export_formats_and_search.py`, `web/tests/meeting-search.test.tsx`, `tasks-export.test.tsx` (new), `download-menu.test.tsx`, `meetings-date-filter.test.tsx` (the keyboard-order test now starts at the search box) | tests |
| `scripts/capture_export_formats_and_search.py`, `docs/fe-captures/export-formats-and-search/` | live captures (36 exchanges) |
| `scripts/time_meeting_search.py` | the timing run below |
| `docs/fe-integration-guide-export.md` (section 8), `docs/fe-integration-guide-owner-app.md` (5.1b and the verification table), `docs/fe-integration-guide-tasks.md` (pointer) | FE guides |

`docs/openapi.json` was regenerated with `PYTHONPATH=. python scripts/export_openapi.py` (run it with the worktree on the path, or it imports the other checkout's `app`). `python scripts/export_openapi.py --check` passes on the final tree. The diff adds only this PR's own changes (`/api/tasks/export`, the `q` parameter, the two new `format` values and their content types, three docstrings). **It added no `/api/billing` route** (the generator does not emit them: `docs/openapi.json` holds none and none were added), so the "known stale" note did not show up as extra diff.

## Verification

Local, scratch Postgres 17 (a throwaway `initdb` cluster on port 55432; Docker was not running), only named files, never the full suite:

- `ruff check` and `ruff format --check` (the project's pinned 0.17.3) on `app`, `tests`, `scripts`; `npm run lint` (0 errors, the 3 warnings are in an old script) and `npm run typecheck` in `web/`.
- Backend: `tests/test_export_formats_and_search.py` (49 tests: md and txt content and headers, transcript on/off and the unlabelled fallback, transcript lines that look like Markdown, file-name cleaning, renderer never called and no `export_busy`, 404/409/422 and every `translation_missing` case incl. stale and unfinished, the CSV's eight columns, time zone of the meeting date, the injection quote for titles, names and meeting titles, suggested/deleted excluded, more than one page, the same list as the screen, active-team scoping with two teams, a private meeting's blank columns, 401, title/notes/transcript matching, `%` and `_` literal, all-words and prefix stems, speaker transcript wins, not-ready meetings match by title only, untitled code, trimming, dates and order, 422 bounds, paging with `q`, another account's and another team's meetings never found), plus the directly touched `tests/test_ask.py`, `tests/test_export.py` and `tests/test_api_tasks.py` (215 passed).
- Frontend (vitest, touched files only): `meeting-search` (lib and list), `tasks-export`, `download-menu`, and the directly related `meetings-date-filter`, `meeting-list-days`, `meetings-recurring`, `meetings-calendar`, `screens`, `tasks-view`, `api-client`, `date-range`.
- Live captures against the running app: `docs/fe-captures/export-formats-and-search/` (36 exchanges).
- Search timing on a scratch database (`scripts/time_meeting_search.py`, 4,000 meetings, 4,000 to 9,000 words of transcript each, notes of about 400 words; table and TOAST 165 MB on an Apple-silicon laptop, Postgres 17, nothing else tuned):

| Search (4,000 meetings, one person may read all of them) | median of 3 | what happens |
|---|---|---|
| no search | 14 ms | the baseline list |
| only filler words (`meeting`): title match only | 16 ms | no full-text part |
| a word in most meetings (`pricing`, 51 rows found) | 214 ms | the newest-first scan stops after 51 matches |
| two words (`pricing roadmap`) | **12.8 s** | scans and decodes every meeting's text |
| a rare word (`ingestion`, 3 rows) | **12.7 s** | same |
| nothing matches (`qqzzxx`) | **13 to 26 s** (two runs, a noisy machine) | same |
| a title phrase (1 row) | **13.2 s** | the full-text part runs on every row the title does not match |

With 300 meetings of the same kind (13 MB): **0.93 s** for every search that has a full-text part, 14 ms without one. So the cost is about **3 ms per meeting the caller may read** (each is about 50 KB of text that `to_tsvector` rebuilds on every search), whatever the word. The plan is a sequential scan of `meetings` with the whole `OR` as its filter (`scripts/time_meeting_search.py` prints it). Synthetic text, 4,000 to 9,000 words per transcript; a real meeting has fewer words on average, so real times are proportionally lower, but a person with a few hundred meetings is already past the 300 ms line you set. **This is slow. Not fixed in this PR, as agreed.**

Comparison only (a GIN expression index on the 4,000-row scratch database, 13 MB; **nothing of this is in the PR**):

| Search | one `OR` as shipped, with the index | `id IN (title) OR id IN (full text)`, with the index |
|---|---|---|
| a word in most meetings (`pricing`) | 175 ms | 12.8 s |
| two common words | 172 ms | 12.9 s |
| a rare word (3 rows) | 12.6 s (the title `ILIKE` in the same `OR` forces a sequential scan: **the index alone does nothing**) | **3 ms** |
| nothing matches | 12.6 s | **3 ms** |

Without the index the split shape is no better (15 to 29 s). The index only pays for a search with few matches, and only when the title match is split from the full-text match; for a word in nearly every meeting both shapes are slow or fast for opposite reasons (the one `OR` stops after 51 matches; the split shape rechecks every match). My synthetic words are in 95% of meetings, which is the unkind case; real words are rarer.

- **Browser:** looked at in the embedded browser against `next dev --webpack` and the real API on a scratch database: the search box with a result, the empty state and the phone layout (390 px; also 1280 px emulated), the Download menu with four rows (390 px), the Tasks header with Export CSV (390 px and about 800 px). A click on Export CSV sent `GET /api/tasks/export?format=csv` through the Next proxy and got 200. **Not observed:** the file being saved to disk and the "downloaded" toast (both unit-tested), the Markdown and text clicks on a real page. `next dev` needed `--webpack` here because Turbopack refuses a `node_modules` that is a symlink out of the worktree.
- **Not verified:** Excel, Sheets, Numbers, Notion, Obsidian opening the files; a real team's data; the server; every test file CI runs and I did not.

## Operate / roll back

- Deploy: normal, nothing to configure. **Server step before merge: none.**
- Roll back: revert the PR. Nothing is stored (files are built per request) and there is no migration, so there is nothing to undo.
- If search is slow on the server: see the first follow-up.

## Follow-ups

- **Search speed.** Adebayo approved a GIN index as its own PR **after #260 (migration 0056) merges**. What it must be, from the measurements above:
  1. A wrapper function, because `concat_ws` is only STABLE and an index needs IMMUTABLE:
     ```sql
     CREATE FUNCTION meeting_search_text(notes text, speaker_transcript text, transcript text)
     RETURNS tsvector LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
       SELECT to_tsvector('simple', coalesce(notes, '') || ' ' ||
              coalesce(nullif(speaker_transcript, ''), transcript, ''))
     $$;
     CREATE INDEX CONCURRENTLY ix_meetings_search_text ON meetings
       USING gin (meeting_search_text(notes, speaker_transcript, transcript));
     ```
     This is the exact expression `scripts/time_meeting_search.py` indexed (13 MB for 4,000 meetings). Same text as `meeting_search.document()` today (`concat_ws` skips a null note; `||` with `coalesce` does the same).
  2. `meeting_search.document()` becomes `func.meeting_search_text(Meeting.notes, Meeting.speaker_transcript, Meeting.transcript)`. Ask Beyric's `candidates()` already calls `document()`, so **Ask uses the same expression and the index with no change of its own** (its `ts_rank` still reads each matching row's text, so Ask gains on the filter, not on the ranking).
  3. `keyword_clause` must put the title match and the full-text match in **separate subqueries** (`id IN (title matches) OR id IN (full-text matches)`), or the title `ILIKE` in the same `OR` makes Postgres scan every row and ignore the index (measured above).
  4. Not measured, worth trying in that PR: a stored generated `tsvector` column (same wrapper) with the GIN index on it, so a recheck or a sequential scan reads a column instead of rebuilding the vector; it adds a table rewrite to the migration. The index PR should re-run `scripts/time_meeting_search.py` and decide between the two on its numbers.
- The words the app adds to a translated file (`## Transcript`, the footer) are English in all four formats; translating them is a small separate change.
- The Tasks CSV is the whole team board, not the screen's filtered view (decision above). Past 10,000 tasks it keeps the newest and says nothing.
- The Markdown file has no front-matter and no task list; a "Tasks from this meeting" section is an obvious later addition.
- The designer has not seen the lead-designed pieces.
