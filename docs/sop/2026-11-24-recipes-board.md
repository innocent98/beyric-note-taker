# SOP: Meeting recipes, slice S6 (the board)

**What shipped.** The board of a meeting: a static, one-page, editable picture (columns by theme, cards with kind, owner and source), made by the `board` recipe, edited by its creator one step at a time (`PATCH /api/documents/{id}/board`), shown by a new screen component with drag (mouse and touch) and a "Move to column…" menu, downloadable as a PNG built in the browser, and renderable to PDF and Word as columns-as-sections. PR into `feature/recipes`: see the PR list on that branch (branch `feature/recipes-board`). Plan task 16; spec 5.3c and 5.4a. No migration. **Mounted (2026-11-24, after #266 merged):** `DocumentBody` (`web/components/app/document-body.tsx`) renders `BoardView` for `output_type === "board"`; the page's block Edit button is hidden for a board; `onSaved` is the page's `replace`, `onStale` refetches the draft. PDF and Word go through S2's `GET /api/documents/{id}/export` and the same `export.build_file` renderer (a board has no tables, so the table-aware parts do not apply), using `board.to_notes_document` so each card keeps its kind and owner (`app/recipes/exporters.py`, 6 lines).

## Why

Adebayo wanted "something similar to the whiteboard, not live, that everybody will use". What that can honestly be: a picture made from the meeting's own decisions, actions, questions and key points, grouped by theme, that a person can tidy by hand and share as an image or a PDF. Not a drawing canvas: no free shapes, no arrows.

## How, and what was decided

| Decision | Choice | Why (and what was rejected) |
|---|---|---|
| What a board is | An ordinary draft with `output_type: "board"` whose blocks are `column`s of `card`s (the engine's blocks, no new table) | Polling, discard, the audit, sharing and exports are the draft engine's, so none was rebuilt |
| Making one | The model call is the engine's. `generate.generate_blocks` calls `board.shape` for the output type `board`: only columns are kept (a heading or paragraph the model adds is dropped), at most 12, and a reply with no card in any column **fails the draft as `structure`** | A board must never half-show. Two lines were added to `app/recipes/generate.py` (a lazy import and the call): the only edit to a sibling slice's file |
| The recipe's words | S3's `board` starter (`app/recipes/library.py`): "group the decisions, action items, open questions and key points into between three and six themes, one column per theme ... each card ... with its owner ... and the line it came from". Tests and captures use that real starter with a fake model | It matches what the engine shapes (columns of cards with kind, owner, sources; at most 12 columns); `test_the_real_board_starter_is_a_board_that_reads_one_meeting` pins it |
| One operation per call | `move_card`, `rename_column`, `add_column`, `add_card`, `edit_card`, `delete_card`, `recolour` (the plan's list) plus **`delete_column`** | Without it a column made by mistake could never be removed. The last column cannot be deleted |
| Numbers | `column` = place of a column; `card` = place counting **through the columns** from 0 (the plan's `card: i`); `index` = place in the target column **after** the moved card has left | One unambiguous integer per card, as the plan's interface has it. `lib/board.ts` computes them, so the drag and the menu send identical calls (a test proves it) |
| Recolour | `recolour {card, kind}` gives the card another kind; colour is a function of kind (teal decision, amber action, blue question, grey note) | The spec says "recolour by type"; there is no free colour |
| Stale-index protection (409) | Every call carries `revision` = the draft's `updated_at` as the screen last saw it; if it differs, **409 `stale_board`**; a column or card that no longer exists is also 409 `stale_board`. The check and the write run under a row lock (`SELECT ... FOR UPDATE`), so two tabs cannot both win | Matching indexes alone cannot tell "card 2" from "another card now at 2". Rejected: an `If-Match` header (awkward through the proxy and the client) and a counter column (needs a migration) |
| Who may edit | Only the creator, and the route looks the draft up by `owner_user_id` itself instead of `api_recipes.document_or_404`, which slice S4 widens to readers. Anyone else: 404. A test shares a board with a teammate's team and checks they still get 404 | S4 would otherwise silently give edit rights to everyone a board is shared with |
| Strict bodies | Pydantic models with `extra="forbid"` and `strict=True` (a number is a number, not `"0"` or `true`); numbers at most 1000, text at most 2000 characters before cleaning; then plain text and the stored bounds (column name 100, card 300, owner 80) | The request cap is generous so the real bounds (and their cutting) stay in one place, `blocks.py` |
| Refused while writing | 409 `writing`; a failed board 409 `not_ready`; a draft that is not a board 409 `not_a_board` | As the generic edit |
| PDF and Word | `board.to_notes_document(title, blocks, when=)`: a column is a section, its cards a bullet list, each with its kind as a bold lead-in ("Decision: ", none for a note) and the owner in italics; an empty column says "No cards." It goes through the existing renderer (`export.build_file`) | The kind is words, not colour, so a black-and-white print still tells a decision from a note. S2 already draws columns in `build_document_from_blocks` (without the kind label); the lead may point its board branch at this function, see Follow-ups |
| PNG | Built in the browser with a canvas (`web/lib/board-png.ts`), twice the board's size, from the page's own CSS variables (no colour of its own); capped at 8192 pixels a side. Layout (pure), drawing (calls on a context) and the file are three pieces so the first two are tested without a canvas | The spec: no server load. jsdom has no canvas; the real file was checked in a browser |
| Drag | Pointer events on a handle (`touch-action: none`), so one code path serves mouse and finger; the card shows in its new place at once and goes back if the call fails; one change at a time | The Tasks board uses HTML5 drag and drop, which has no touch support, so it was not reused for the code; its **columns and cards were reused as they are** (`tk-col`, `tk-card`) |
| The menu | Every card has a native `select` "Move to column…" and a **Move** button that appears once a column is chosen; nothing moves on choosing | A closed select fires `change` on every arrow key in some browsers; with one step a keyboard user could move a card several columns by arrowing |
| On a phone | The columns stack (no sideways scroll); the menu is the supported way, dragging is best effort | Spec 5.4a |

**LEAD-DESIGNED pieces (the designer's files draw none of this)**, built from existing components and tokens only (`tk-board`/`tk-col`/`tk-card`, `Button`, `Alert`, `Icon`, `select`, `textarea`, `rh-btn`, `icon-btn`; colours only from `tokens.css`):

1. The board screen: bar with **Add column** and **Download PNG**; the note "Only the person who made this board can change it." for a reader.
2. A card: kind chip (Decision, Action, Question, Note) with a stripe of the same kind colour, text, source links ("Line 2", "Meeting"), owner pill, drag handle, **Move to column…** select with **Move**, **Edit**.
3. A column heading: name, card count, **Rename**, a delete (trash) button, "No cards yet", **Add card**.
4. The card form (Card text, Owner, Kind; Add or Save, Cancel, Delete card), the column name form, and the column delete confirmation ("Delete “Pricing” and its 2 cards?").
5. The wording of every refusal (the API's sentences, shown as they are) and "Could not make the PNG. Try again."
6. The CSS block "The board of a meeting" in `web/styles/pages.css` (`bd-*`).
7. The PNG's look: title, columns as grey panels, cards as white tiles with a kind stripe and the kind in capitals.

## What's involved

| Path | Change |
|---|---|
| `app/recipes/board.py` | **new**: `shape`, the strict `Op` models, `apply` (pure), `to_notes_document`, `StaleBoard`, `BoardInvalid` |
| `app/api_recipe_board.py` | **new**: `PATCH /api/documents/{id}/board` |
| `app/api.py` | registers the router beside `api_recipes` (2 lines) |
| `app/recipes/exporters.py` | a board's Word and PDF use `board.to_notes_document` (6 lines) |
| `web/components/app/document-body.tsx`, `document-view.tsx` | mount `BoardView`, hide Edit for a board, refetch on a stale board |
| `tests/test_accounts.py` | the id guard sends the board route a valid body (CI's `test (2/4)` failure: it got 422 before 404) |
| `app/recipes/generate.py` | calls `board.shape` for output type `board` (4 lines in `generate_blocks`) |
| `docs/openapi.json`, `web/lib/api/schema.d.ts` | regenerated (the operation bodies are in the schema as `MoveCard`, `RenameColumn`, ...) |
| `web/lib/board.ts` | **new**: types from the schema, card numbering, `moveOp`, `menuMove`, `applyMove`, `dropTarget` |
| `web/lib/board-png.ts` | **new**: `layoutBoard`, `drawBoard`, `boardPng`, `pngSize`, `boardFilename`, `paletteFromCss` |
| `web/components/app/board-view.tsx` | **new**: `BoardView({ doc, canEdit, onSaved, onStale })` |
| `web/lib/api/client.ts` | `api.board.edit(id, revision, op)` (one entry beside `questions`) |
| `web/styles/pages.css` | the `bd-*` block, after the Tasks board's `tk-empty` |
| `scripts/capture_recipe_board.py`, `docs/fe-captures/recipes/board/`, `docs/fe-integration-guide-recipes-board.md` | live captures (34 files) and the guide. `--serve PORT` leaves the app up for a browser pass |
| Tests | `tests/test_recipe_board.py` (42), `tests/test_recipe_board_generate.py` (8), `tests/test_api_recipe_board.py` (35); `web/tests/board-lib.test.ts` (10), `board-png.test.ts` (13), `board-view.test.tsx` (29) |

## Verification

- **Backend (local, bounded, scratch Postgres `recipes-board-pg` on port 55511, removed afterwards):** the three board test files above and `tests/test_recipe_generate.py` (the file whose code was touched); `ruff check .` and `ruff format --check .` with the project's pinned ruff 0.17.3 (the venv's, not the Homebrew 0.15). Named tests include: each operation validated and bounded; only the creator can edit (a stranger, and a teammate it is shared with: 404); a stale `revision`, a missing card and a missing column are 409; two edits with the same revision cannot both win; a board renders to a PDF and a DOCX (the DOCX text has the cards) through `export.build_file`.
- **Web:** `npm run lint` (0 errors), `npm run typecheck`, and the three board test files plus `tests/guards.test.ts` (the colour-literal guard). Component tests: a drag with the mouse and with a finger (simulated pointer events) sends `move_card` with the same numbers as the "Move to column…" menu to the same place (`sends exactly the call the drag to the same place sends`); the card shows in its new column before the answer and goes back on a refusal; stale screen asks the page to reload; one change at a time; a screen that cannot edit has no handle, menu or add controls; the PNG is built from the board on the screen and saved as `<title>.png`.
- **Real browser, 1280 and 390 px** (the built-in browser against the real app and worker with a fake model, on a throwaway page that mounted `BoardView`, removed afterwards): a mouse drag moved a card to the end of another column and the server had it; the menu plus **Move** moved a card at 390 px; at 390 px the columns stack with no sideways scroll; the downloaded PNG was 2272 by 808 pixels for a 1136 by 404 board (the board's size times two), named `Onboarding board.png`, in the page's font, readable. A first look showed the PNG in a serif font (the font was read from the root, not the body) and a column that scrolled sideways on a phone; both fixed and looked at again.
- **Not verified:** a drag with a **finger** on a real phone (the browser tool sends mouse input only); arrow keys on the native select in a real browser (not needed after the Move button, but not tried); the **real model's** grouping into themes (slice S7's live pass); PDF and Word **over HTTP** (S2's route); a screen reader.

## Operate / roll back

No migration, no setting, no new dependency, no server job. Revert the PR; drafts already made stay valid blocks. Nothing here is visible until S3's `board` starter exists.

## Follow-ups (for the lead and the other slices)

1. ~~Mount `BoardView`~~ done (see top). `canEdit` is still `true` on the page: slice S4 passes false for a reader of a shared board.
2. ~~S3's `board` recipe~~ checked against the real starter (see the table above).
3. ~~S2's exporter~~ done: `exporters.export` builds a board's render model with `board.to_notes_document`.
4. **Conflicts to expect:** `web/lib/api/client.ts` and `web/lib/api/schema.d.ts` / `docs/openapi.json` (regenerate after merging); `app/api.py` (one import, one line).
5. `app/api_recipe_board.py` imports `_out` from `app/api_recipes.py`; if S4 changes `_out`'s signature (a reader), this call follows it.
6. A card's `Move` choice is kept per position; a card that moves away while another has a column chosen can leave that choice on the card that took its place (cosmetic, one more press needed).
