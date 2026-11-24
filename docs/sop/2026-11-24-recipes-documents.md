# SOP: Recipes slice S2, documents and exports (T7, T8; T9 added when it lands)

**What shipped (so far).** Tables in the Word and PDF renderers, and the export of a draft as Markdown, text, CSV, Excel, Word or PDF from its current blocks. Branch `feature/recipes-documents`, PR 266 into `feature/recipes`. The web part (T9: document view, block editor, polling hook) is **not built yet**: it needs the generated types of the draft API (PR 265) and is added to this SOP when it lands.

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

**LEAD-DESIGNED pieces (not in the designer's files):** the export file formats and names above, the formula apostrophe, one CSV per table, one Excel sheet per table, the rule that exports use the saved blocks. (The T9 screens will be listed here when built.)

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
| `docs/fe-integration-guide-recipes-documents.md` | the FE guide for the export route |

## Verification

Local, scratch Postgres 16 container (removed afterwards), only named files, never the full suite:

- `ruff check .`, `ruff format --check .` (the pinned 0.17.3).
- `tests/test_export_tables.py` (11): blocks to sections and a table of plain text, text before the first heading, a board column, HTML escaping of cells and title, the Word table with the right cell text and bold header, markup characters kept as text, a 150-row table, `docx` through the child process, PDF non-empty through the child process, a 120-row table paginating (WeasyPrint page count), and the child's environment holding no app settings.
- `tests/test_recipe_exports.py` (24): `test_formula_cells_are_neutralised_csv_and_xlsx`, `test_csv_has_bom_and_commas_in_cells_survive`, `test_xlsx_opens_with_openpyxl_and_has_header_row`, `test_markdown_table`, `test_export_uses_edited_blocks`, `test_docx_pdf_go_through_export_build_file`, plus second-table CSV, `no_table`, sheet names, text export, blanks kept, not-ready, and the route (headers, 404 for another person's and a missing draft, 401, 422, busy, timeout and failure shapes).
- `tests/test_export.py` (the meeting export, touched renderer), the IDOR guard in `tests/test_accounts.py`, and the two openapi tests in `tests/test_api.py`.
- Live captures: `docs/fe-captures/recipes/exports/` (the real app over HTTP, the real render child).
- **Export time with a table** (developer Mac, five runs each, 40 rows of four columns, `export.build_file`): **PDF median 0.8 s** (0.67 to 0.94 s), **Word median 0.23 s**. This is one machine; the production server is smaller and shared, so treat it as a lower bound. Nothing queues exports: a second Word or PDF request while one is running gets 503 `export_busy`; with a 0.8 s build that is a short window, and the S7 live pass should measure it on the server.
- **Not verified:** the table in a real Word and a real PDF viewer by eye (checked by reading the file back, not by looking); production export load; sharing (S4); anything that needs PR 265.

## Operate / roll back

- Deploy: `pip install` picks up `openpyxl` (the image builds from `pyproject.toml`). No migration, no setting. Nothing user-visible: the whole recipes release deploys together.
- Roll back: revert the PR; no data was written.

## Follow-ups

- **S4:** test export by a teammate of a shared draft (the route already goes through `api_recipes.document_or_404`).
- T9: the document screens, the polling hook, the editor, vitest, a look in a real browser at 390 and 1280 px; this SOP and the FE guide gain their parts then.
- S7: measure export time on the server with real drafts.
- An Excel file of a board (columns as sheets) and a board PNG are S6's.
