# SOP: Recipes slice S2, documents and exports (T7, T8, T9)

**What shipped.** Tables in the Word and PDF renderers, the export of a draft as Markdown, text, CSV, Excel, Word or PDF from its current blocks, and the web document screen at `/documents/{id}` (polling, source chips, blanks and figures marked, structured block editor, Copy, mail app, Download menu, Regenerate, Discard). Branch `feature/recipes-documents`, PR 266 into `feature/recipes`.

## Why

A recipe draft is blocks (`app/recipes/blocks.py`). A proposal needs real tables in Word and PDF, and a table of tasks or flashcards needs CSV and Excel. Every format is rendered from the blocks so they cannot disagree, and every export uses what the person has edited, not the first generation (spec 5.4a).

## How, and what was decided

| Decision | Choice | Why (and what was rejected) |
|---|---|---|
| Render model | `notes_document.Table` (columns and rows of plain strings) joins the `NotesDocument` blocks; `build_document_from_blocks(doc)` maps a draft to the same model the notes use | One model behind PDF and Word already existed; a second renderer for drafts was rejected. A level 1 or 2 heading starts a section (as `##` does in notes), level 3 is a heading inside it; a board's column becomes a section with its cards as a list (S6 may replace this with its own layout). The function imports `app.recipes` inside itself because the render child imports `notes_document` and must stay free of the rest of the app |
| PDF table | `<table>` with `<thead>` (repeats on every page, `display: table-header-group`), rows `break-inside: avoid`, cells `html.escape`d | Long tables paginate; test builds 120 rows and asserts at least two pages |
| Word table | python-docx `Table Grid`, bold header, `tblHeader` on the first row, `cantSplit` on every row, text added as runs (never markup) | A real Word table, not tab-separated text |
| Formats | `md` and `txt` reuse `blocks.to_markdown` and `to_text`; `docx` and `pdf` call `export.build_file` (same child process, 45 s limit, one at a time per web process, no app settings in its environment); `csv` and `xlsx` are built in the request | CSV and Excel are cheap and need no child process |
| CSV | one table (`table=N`, default first), UTF-8 **with BOM**, CRLF | Excel reads UTF-8 only with a BOM (the naira sign). A CSV cannot hold two tables; the alternative of concatenating them was rejected. **Lead-designed consequence:** the FE offers one CSV entry per table |
| Excel | every table, one sheet each, named from the heading above it (illegal characters removed, 31 characters, `Table N` when empty or a duplicate), header bold and frozen, widths from the longest cell | `openpyxl` is the one new dependency (`pyproject.toml`) |
| Formula injection | a cell whose text, after leading whitespace, starts with `=`, `+`, `-` or `@`, or that starts with a tab or CR, is written with a leading apostrophe, in CSV and Excel, header cells too; Excel cells are also forced to the string type (`data_type = "s"`) | OWASP's rule. **Known cost:** a legitimate `-5%` or `+234...` also gets the apostrophe. Markdown, text, Word and PDF keep the text unchanged (they run nothing) |
| Route | `GET /api/documents/{id}/export?format=&table=` in a NEW file `app/api_recipe_exports.py`, included in `app/api.py`; `format` defaults to `pdf` like the meeting export | A new file means PR 265's `app/api_recipes.py` is untouched. The default exists because the IDOR guard (`tests/test_accounts.py`) calls every id route with the path only and expects 404 |
| Who may export | the draft's owner; another person's or a missing draft is 404 | The route uses `api_recipes.document_or_404` (PR 265), so slice S4 widening that one function to shared drafts widens exports with it |
| Errors | 409 `not_ready` (writing or failed), 409 `no_table`, 503 `export_busy` with `Retry-After: 5`, 504 `export_timeout`, 500 `export_failed`; project error shape | Same words as the meeting export. The Word and PDF build releases the database connection first |

### The document screen (T9)

| Decision | Choice | Why (and what was rejected) |
|---|---|---|
| Page and seam | `web/app/documents/[id]/page.tsx` renders `DocumentView` (keyed by the draft id, so Regenerate lands on a clean state); `DocumentBody` (`document-body.tsx`) switches on `output_type` and **returns nothing for `board`**, the place slice S6 mounts its view | The lead asked for this seam. Routes: `/documents/[id]` added to `lib/routes.ts` and `proxy.ts`, `/documents` to `APP_PATHS` (the list page is S4's) |
| Polling | `useDocument` (`use-document.ts`) copies `useTranslation`'s shape: `DOCUMENT_POLL_MS = 2000`, asks while `writing`, stops at `ready` or `failed`, keeps asking through a lost connection, and says "discarded" on a 404 | Same rhythm as translation |
| Editor | A **structured block editor**: heading (level and text), paragraph (textarea), list (items), table (cells, column headings, add or remove row and column; the last column cannot be removed). Blocks are added at the end (heading, paragraph, list, table), moved up or down with buttons, deleted. The page owns the working copy; **Save changes** sends every block with PATCH (and the title when it changed); Cancel throws it away | Spec 5.4a: not rich text. Moves are "Up" and "Down" buttons (no drag: the icon set has no arrows and a drag needs a keyboard fallback anyway). **Rejected:** insert-between-blocks (add at the end and move) |
| Sources | Each block with sources shows "Drafted from" and one chip per source: a line opens `/meetings/{id}?tab=transcript&line=N` (the chip's tooltip holds the line's first words), a meeting, decision or task opens `/meetings/{id}`. **An edited block keeps its sources** (the editor only changes text, never the sources array); a new block has none. When `sources_available` is false the chips read "not available" and link nowhere. The page cannot check whether a line still exists: the meeting page shows the transcript when `line` is not found, so a moved line degrades to the meeting; the server's own re-find (spec 5.4a) is not on this screen | |
| Blanks and figures | `[need: ...]` is highlighted in the text; a figure named in `checks.flags` is marked "check this" (dotted underline, tooltip and a screen-reader word) **only where it stands alone** (the `2` of "week 2", not the `2` of "2026" or "v2"). Counts show as two warning badges ("1 blank to fill", "3 figures to check") under the toolbar. Copy and every download add a status line when blanks remain: "Copied with 1 blank still to fill." / "<file> downloaded with 2 blanks still to fill." In the editor only blanks are marked (the server's flags are by block position, which the working copy changes) | Advisory, never blocks, as the spec says |
| Copy and mail | Copy puts `blocksToText` on the clipboard: the same text as the server's plain-text export (`tests/documents-lib.test.ts` holds the two equal against the live capture). "Open in mail app" is an Email-only `mailto:` link: a leading `Subject:` paragraph becomes the subject and leaves the body, else the title is the subject; past **1,800 characters** the button copies instead and says "Too long for a mail link" | Beyric never sends mail |
| Download menu | The notes menu's classes: Word, PDF, Markdown always; **CSV one entry per table (named from the heading above it) and Excel only when the draft has a table**. Reads the saved draft (edit mode hides the toolbar, so a download is never of unsaved text). 503 `export_busy` and the others show their messages | |
| Regenerate | `POST /api/documents` with the draft's recipe (`recipe_key` or `recipe_id`), `meeting_ids`, `options` and `use_brief`, then opens the new draft. Hidden when the recipe row or all meetings are gone. **`options` and `use_brief` were added to the draft payload** (`DocumentOut`, `app/api_schemas.py`, `app/api_recipes.py`, test `test_the_draft_carries_the_options_and_brief_choice...`) because without them Regenerate dropped the tone and recipient. The request carries `X-Team-Id` (`isTeamScoped` now answers true for `POST /api/documents`) | |
| Discard | Asks first ("Discard this draft? This cannot be undone." with **Discard draft** and **Keep**), then DELETE, then back to its meeting (or All meetings). Offered on a draft still being written, on a failed one and on a ready one | |
| Labels | "older than the meeting" for `stale`; "the meetings ... are no longer available to you" for `sources_available: false`; "N meetings were left out" for `left_out_count`; the failure message for `failed`; "Writing your draft..." for `writing` | |

**LEAD-DESIGNED pieces (not in the designer's files; every element on this page and the file formats):** the page and its header (title, status badge, output type, "Edited"), the toolbar (Copy, Open in mail app, Download, Edit, Regenerate, Discard), the two warning badges, the status and notice lines, "Drafted from" and its chips, the blank and "check this" marks, the discard confirmation, the editor (block cards with Up, Down and delete, the add-block buttons, the table editor, the title field), the Download entries and their tiles, and the export file formats and names above (the formula apostrophe, one CSV per table, one Excel sheet per table, exports from the saved blocks). They reuse `Button`, `Badge`, `Alert`, `Field`, `IconButton`, the Download menu's classes and the existing tokens; the only new CSS is the `.doc-*` block at the end of `web/styles/pages.css` and `.dl-tile-file`, using tokens only.

## What's involved

| Path | Change |
|---|---|
| `app/notes_document.py` | `Table` block, `build_document_from_blocks` |
| `app/export_render.py` | tables in `render_html` (and CSS) and `render_docx` (`_add_table`) |
| `app/recipes/exporters.py` | `export(document, fmt, table=0)`, `neutralise`, CSV and Excel builders |
| `app/api_recipe_exports.py`, `app/api.py` | the route and its inclusion |
| `pyproject.toml` | `openpyxl>=3.1` |
| `tests/test_accounts.py` | the document id classified in the IDOR guard (the same lines as PR 265, so it merges cleanly) |
| `docs/openapi.json`, `web/lib/api/schema.d.ts` | regenerated (`PYTHONPATH=. python scripts/export_openapi.py`, `npm run gen:api`) |
| `scripts/capture_recipe_exports.py`, `docs/fe-captures/recipes/exports/` | 23 live captures |
| `docs/fe-integration-guide-recipes-documents.md` | the FE guide for the export route and the document screen |
| `web/app/documents/[id]/page.tsx`, `web/components/app/document-view.tsx`, `document-body.tsx`, `document-parts.tsx`, `document-editor.tsx`, `use-document.ts`, `web/lib/documents.ts` | the document screen |
| `web/lib/api/client.ts`, `types.ts`, `web/lib/routes.ts`, `web/proxy.ts`, `web/styles/pages.css`, `web/components/app/copy-button.tsx` (`onCopied`) | the `documents` calls and types, the route, the `.doc-*` styles |
| `app/api_schemas.py`, `app/api_recipes.py`, `tests/test_api_recipes.py` | `options` and `use_brief` on the draft |

## Verification

Local, scratch Postgres 16 container (removed afterwards), only named files, never the full suite:

- `ruff check .`, `ruff format --check .` (the pinned 0.17.3).
- `tests/test_export_tables.py` (11): blocks to sections and a table of plain text, text before the first heading, a board column, HTML escaping of cells and title, the Word table with the right cell text and bold header, markup characters kept as text, a 150-row table, `docx` through the child process, PDF non-empty through the child process, a 120-row table paginating (WeasyPrint page count), and the child's environment holding no app settings.
- `tests/test_recipe_exports.py` (24): `test_formula_cells_are_neutralised_csv_and_xlsx`, `test_csv_has_bom_and_commas_in_cells_survive`, `test_xlsx_opens_with_openpyxl_and_has_header_row`, `test_markdown_table`, `test_export_uses_edited_blocks`, `test_docx_pdf_go_through_export_build_file`, plus second-table CSV, `no_table`, sheet names, text export, blanks kept, not-ready, and the route (headers, 404 for another person's and a missing draft, 401, 422, busy, timeout and failure shapes).
- Web (vitest, only touched and related files): `tests/documents-lib.test.ts` (13: Copy text equal to the server's plain-text export, sources and links, blanks and figures, the mail link and its limit, list edits) and `tests/document-view.test.tsx` (29: polling until ready and stopping, a lost connection, 404 while polling, failed, discard while writing, source chips, blank and figure marks and counts, tables, stale and sources-gone labels, Copy and its blank warning, the mail link and its fallback, the Download menu and its entries, the save and its error, edit, cancel, add, move and delete blocks, table cell, row and column edits, Regenerate and its limit message, Discard and its confirmation, and the board placeholder), `tests/routes.test.ts`, `tests/guards.test.ts`, `tests/team-header.test.ts`, `tests/download-menu.test.tsx`, `tests/bot-invite-tip.test.tsx` (it uses `CopyButton`), `npm run lint` and `npm run typecheck`. The fixtures are the REAL captured bodies of `docs/fe-captures/recipes/` (the engine's) and `docs/fe-captures/recipes/exports/24-the-draft.json`.
- Backend added in T9: `tests/test_api_recipes.py` (21, one new).
- **Looked at in a real browser** (`next dev --webpack` over the real backend on a scratch database with a seeded email draft, a writing one and a failed one) at 1280 and 390 px: the read view, the editor, the table, the writing state; a save through the real PATCH worked and showed "Edited". No horizontal page overflow at 390; a wide table scrolls inside its box. The first look showed a table squeezing "When" into "Wh/en" on a phone; fixed with a cell minimum width. **Not looked at:** dark mode, other widths, the Download menu's panel and a real file download in the browser (covered by the unit tests and the export captures), Regenerate against a real worker.
- `tests/test_export.py` (the meeting export, touched renderer), the IDOR guard in `tests/test_accounts.py`, and the two openapi tests in `tests/test_api.py`.
- Live captures: `docs/fe-captures/recipes/exports/` (the real app over HTTP, the real render child).
- **Export time with a table** (developer Mac, five runs each, 40 rows of four columns, `export.build_file`): **PDF median 0.8 s** (0.67 to 0.94 s), **Word median 0.23 s**. This is one machine; the production server is smaller and shared, so treat it as a lower bound. Nothing queues exports: a second Word or PDF request while one is running gets 503 `export_busy`; with a 0.8 s build that is a short window, and the S7 live pass should measure it on the server.
- **Not verified:** the table in a real Word and a real PDF viewer by eye (checked by reading the file back, not by looking); production export load; sharing (S4); anything that needs PR 265.

## Operate / roll back

- Deploy: `pip install` picks up `openpyxl` (the image builds from `pyproject.toml`). No migration, no setting. Nothing user-visible: the whole recipes release deploys together.
- Roll back: revert the PR; no data was written.

## Follow-ups

- **S4:** test export by a teammate of a shared draft (the route already goes through `api_recipes.document_or_404`).
- The number audit flags the digits of "Week 1" and "Week 2" in a table cell, and the "5%" of "-5%" (seen in the live capture): list ordinals are ignored but a week number is not. That is S1's audit to tune; this screen just shows what the server flags.
- Insert a block between two others (today: add at the end and move up), and drag to reorder, are not offered.
- The Download menu has no plain-text entry (Copy covers it); the route still serves `txt`.
- S7: measure export time on the server with real drafts.
- An Excel file of a board (columns as sheets) and a board PNG are S6's.
