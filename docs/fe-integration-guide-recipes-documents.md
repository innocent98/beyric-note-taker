# FE integration guide: downloading a recipe draft (export route)

The contract for `GET /api/documents/{id}/export`, the download behind Copy-adjacent actions on a draft (Word, PDF, Markdown, CSV, Excel). The draft API itself (make, poll, edit, discard) is in `docs/fe-integration-guide-recipes.md`; this file covers exports only and will gain the document-screen notes when slice S2's web part (T9) lands.

**Where every body below comes from:** `scripts/capture_recipe_exports.py` drove the real app over HTTP on a scratch database and saved each exchange to `docs/fe-captures/recipes/exports/NN-name.json` (verbatim). Placeholder people only. **Not real:** the drafts were inserted straight into the database (the capture was made before PR 265 merged), so the draft ids (`1`, `2`...) are the scratch database's; the blocks are the stored shape. Statuses 500, 503 and 504 were **not run live** (see the table at the end).

## What it is, in the FE's terms

A person has a finished draft open and picks "Word", "PDF", "Markdown", "CSV" or "Excel". The browser downloads a file built from the draft **as it stands now, edits included**: save the editor first (`PATCH`), then export, or the person gets the saved text, not the unsaved text on screen. Nothing is stored; each press builds the file again.

## Request

`GET /api/documents/{id}/export?format=<md|txt|csv|xlsx|docx|pdf>&table=<n>`

| Query | Default | Meaning |
|---|---|---|
| `format` | `pdf` | one of `md`, `txt`, `csv`, `xlsx`, `docx`, `pdf` |
| `table` | `0` | **CSV only**: which table of the draft (counting from 0, in page order) the CSV holds |

Needs a login (cookie); a GET, so no CSRF header. Use a plain link or `fetch` + blob: the response is an attachment with `Content-Disposition` (clean file name from the draft's title, UTF-8 form in `filename*`).

Response headers on success (captured, PDF shown): `content-type: application/pdf`, `content-disposition: attachment; filename="Proposal for Acme Ltd.pdf"; filename*=UTF-8''Proposal%20for%20Acme%20Ltd.pdf`, `cache-control: no-store`.

| `format` | `content-type` | What the file is |
|---|---|---|
| `md` | `text/markdown; charset=utf-8` | Markdown; tables are pipe tables; the text is escaped for Markdown (`\[need: trainer\]`, `\-5% ...`) |
| `txt` | `text/plain; charset=utf-8` | plain text: bullets as `- `, a table as lines joined by ` \| `, nothing escaped |
| `csv` | `text/csv; charset=utf-8` | ONE table, UTF-8 **with a byte order mark** (first bytes `ef bb bf`), CRLF lines |
| `xlsx` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | EVERY table, one sheet each, named from the heading above it (else `Table N`), bold frozen header row |
| `docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | Word, real tables |
| `pdf` | `application/pdf` | PDF, real tables, header row repeats on each page |

Verbatim captures: Markdown `06-markdown.json`, text `07-text.json`, CSV `08-csv-first-table.json` and `09-csv-second-table.json`, Excel `11-xlsx.json` (read back with openpyxl), Word `12-docx.json` (read back with python-docx), PDF `13-pdf.json` (size and header only).

Captured CSV of table 1 (the second table), file text exactly:

```
﻿Item,Price,Note
Onboarding,"₦50,000","Paid, monthly"
"Add-on, ""priority"" support",'=1+1,'-5% for 12 months
```

(a U+FEFF mark opens the file). **Formula neutralising, a UX consequence:** any CSV or Excel cell that could run as a formula (starts with `=`, `+`, `-` or `@` after spaces, or with a tab or return) gets a leading apostrophe. The draft `=1+1` is exported as `'=1+1`, and `-5% for 12 months` as `'-5% for 12 months`. Excel cells are all text (`cell_types: ["s"]` in capture 11). The apostrophe is visible in the cell. Do not promise "exactly what you see" for those cells; the Markdown, text, Word and PDF files keep the text unchanged.

## Which formats to offer

CSV and Excel exist only for a draft **with a table**. The draft's `blocks` (from `GET /api/documents/{id}`) tell you: show "CSV" and "Excel" only when at least one block has `"type": "table"`. If you show them anyway, the refusal is below. For CSV with several tables, offer one entry per table and pass `table=0`, `table=1`...; Excel needs no choice.

## Errors (verbatim from captures)

All use the project shape `{"error": {"code", "message"}}`.

| Status | `code` | `message` | When | Capture |
|---|---|---|---|---|
| 401 | `not_authenticated` | `Log in first.` | no login | `05-logged-out.json` |
| 404 | `not_found` | `No such document.` | another person's draft, a draft that does not exist, or an id that cannot exist (same body for all, never 403) | `22-someone-elses-draft.json`, `23-no-such-draft.json` |
| 409 | `not_ready` | `This draft is not ready yet.` | status `writing` or `failed` | `19-still-writing.json` |
| 409 | `no_table` | `This draft has no table to export that way.` | `csv` or `xlsx` with no table, or `table` past the last table | `10-csv-no-such-table.json`, `16-csv-without-a-table.json`, `17-xlsx-without-a-table.json` |
| 422 | `validation_error` | `Some fields are missing or not valid.`, with `fields: [{"field": "format", "message": "Input should be 'md', 'txt', 'csv', 'xlsx', 'docx' or 'pdf'"}]` | unknown `format` | `20-bad-format.json` |
| 422 | `validation_error` | same, with `{"field": "table", "message": "Input should be greater than or equal to 0"}` | negative `table` | `21-bad-table-number.json` |
| 503 | `export_busy` | `Another file is being prepared. Try again in a few seconds.` plus header `Retry-After: 5` | **Word and PDF only**: another file is being built in the same server process | **not run live**: shape from `tests/test_recipe_exports.py` and the meeting export's identical answer |
| 504 | `export_timeout` | `Preparing the file took too long. Try again.` | Word or PDF ran past 45 s | **not run live** (same) |
| 500 | `export_failed` | `The file could not be prepared. Try again.` | the renderer failed | **not run live** (same) |

## Traps and what the FE must surface

- **Word and PDF are built one at a time per server process.** Two people pressing "PDF" together can get 503 `export_busy`: show "Another file is being prepared. Try again in a few seconds." and retry after `Retry-After` seconds (5). A 40-row table builds in under a second on a developer machine (SOP has the numbers); production load is not measured yet. Markdown, text, CSV and Excel are built in the request and never busy.
- **Export what was saved.** The route reads the saved blocks. Unsaved editor text is not in the file.
- **Blanks travel into the files.** `[need: ...]` tokens are in every export as text. The plan is that Copy and export warn "N blanks still to fill" (the count is in `checks.blanks` of the draft); the file itself carries no warning.
- **The id is the draft's id, in the path**, never a meeting id.
- **Binary bodies:** read the response as a blob. Do not read a CSV through `response.text()` and re-encode it; the byte order mark is what makes Excel read the naira sign right.

## Verification table

| Behaviour | Verified |
|---|---|
| md, txt, csv (two tables), xlsx, docx, pdf return 200 with the headers above | live (captures 06 to 14) |
| default format is PDF | live (14) |
| csv `table=N`, `no_table` past the last | live (09, 10) |
| csv BOM and quoting; formula apostrophes in CSV and Excel | live (08, 09, 11) |
| Excel sheet per table, text cells | live (11, read back with openpyxl) |
| Word tables | live (12, read back with python-docx) |
| PDF content (table, pagination) | PDF size and header only live; content checked by `tests/test_export_tables.py` |
| edited blocks are exported | live (15) |
| 401, 404, 409 (`not_ready`, `no_table`), 422 | live (05, 10, 16 to 23) |
| 503, 504, 500 | **not run live**: unit tests with the renderer replaced (`tests/test_recipe_exports.py`) |
| an export of a draft shared by a teammate | **not built**: slice S4 widens who may read (`document_or_404`); today only the owner can export |
