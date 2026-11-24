# Frontend integration guide: meeting recipes, the board (slice S6)

What the **board screen** needs from the API: make a board from a meeting, show it, change it one step at a time, download it. **Every payload, status code and error string below is pasted from a real response** captured by `scripts/capture_recipe_board.py` against the running app and the real worker (files in `docs/fe-captures/recipes/board/`, named in each section). Bodies are trimmed where long; no field was invented or tidied. The draft API this builds on is in `docs/fe-integration-guide-recipes.md`; read its sections on polling and the error shape first.

**What was NOT real in the captures:**

- **The model.** A fake stood in for the OpenAI client (no key, no network). It returns the strict-JSON shape the code asks for and cites real line numbers it is shown. A real model's wording, and how well it groups a meeting into themes, will differ; the shape of every body will not. The **real** model is run in slice S7.
- **The `board` starter.** `app/recipes/library.py` is slice S3's; the capture registered a stand-in with the same key `board`. Make a board with `{"recipe_key": "board", ...}`.
- **PDF and Word of a board** go through slice S2's `GET /api/documents/{id}/export?format=`, which is not on this branch. The render model (`board.to_notes_document`) was built and rendered (PDF and DOCX files came out, sizes in capture `34-board-as-pdf-and-word.json`) but **not fetched over HTTP**. The export route now builds a board with it (each card keeps its kind and owner); verified through the route in `tests/test_api_recipe_board.py` and in a real browser, not captured.
- **A teammate the board is shared with** (slice S4): the route looks the draft up by its creator, so they get 404 exactly like a stranger; shape from `tests/test_api_recipe_board.py`, not captured live.
- **Touch dragging** was not run on a real phone (see the verification table).

## What it is, in your terms

A **board** is a one-page picture of a meeting: **columns** (themes) holding **cards** (a decision, an action, a question or a note), each card with an owner when the meeting names one and the place in the meeting it came from. It is **static**: not live, no free shapes, no arrows. The person who made it can move cards between columns, rename and add columns, add and edit cards and change a card's kind (the colour follows the kind). Anyone who can open it can download it as a **PNG** (built in the browser) or PDF/Word (S2's export).

A board is an ordinary draft with `output_type: "board"` (`GET /api/documents/{id}`), so polling, discarding, the audit and sharing are the same as for any draft. The document page switches on `output_type`: `board` mounts `BoardView`, which edits through the route below, **not** through `PATCH /api/documents/{id}` (that route saves whole block lists and has no stale-screen check).

## Numbers: the one thing to get right

| Name | Means | Example |
|---|---|---|
| `column` | a column's place on the board, from 0 | `3` is the fourth column |
| `card` | a card's place **counting through the columns in order**, from 0 | with 2 cards in column 0, card `2` is the first card of column 1 |
| `index` | a place **inside the target column once the moved card has left it** | to the end of a column that holds 2 others: `2` |

`lib/board.ts` has the helpers (`cardNumber`, `moveOp`, `menuMove`, `dropTarget`); use them so a drag and the menu send the same thing.

## 1. Make a board: `POST /api/documents`

As for any draft (`recipe_key: "board"`, one meeting). Answers **202** with `status: "writing"` and `blocks: []`; poll `GET /api/documents/{id}` every 2 seconds (`02-make-board.json`). A board **never half-shows**: a model reply with no card in any column (or no column at all) fails the draft as `structure` (`32-get-failed-board.json`: `status: "failed"`, `failure_code: "structure"`, `blocks: []`).

## 2. Read it: `GET /api/documents/{id}` *(capture `04-get-board.json`)*

```json
{
  "id": 1,
  "title": "Onboarding board",
  "output_type": "board",
  "recipe_key": "board",
  "recipe_id": null,
  "status": "ready",
  "edited": false,
  "meeting_ids": [1],
  "created_at": "2026-11-24T14:47:41.576014Z",
  "updated_at": "2026-11-24T14:47:41.721572Z",
  "checks": {
    "blanks": [{ "block_index": 1, "what": "call date" }],
    "flags": [{ "block_index": 0, "text": "120,000 naira", "kind": "figure" }]
  },
  "stale": false,
  "sources_available": true,
  "left_out_count": 0,
  "failure_code": null,
  "failure_message": null,
  "blocks": [ "...columns, below..." ]
}
```

`blocks` holds **only** `column` blocks (the first of four):

```json
{
  "type": "column",
  "title": "Pricing and start",
  "cards": [
    {
      "text": "Onboarding is 50,000 naira",
      "kind": "decision",
      "owner": null,
      "sources": [
        { "kind": "line", "meeting_id": 1, "line": 2, "quote": "We would like the onboarding for 50,000 naira,", "ref": null }
      ]
    },
    { "text": "Setup fee of 120,000 naira", "kind": "note", "owner": null, "sources": [] }
  ]
}
```

**Field-nesting traps**

- `cards[].owner` is present and `null` when the meeting names no one; `sources` is present and `[]` for a card nobody drafted from (every card a person adds).
- `checks[].block_index` is the **column's place**, not a card's. The audit says a column holds a figure to check, not which card; show the flag on the column, and find the card by `text` if you want to mark it.
- A line source's `quote` is written by the server from the real line (the first 8 words), never by the model. Link a line source to `/meetings/{meeting_id}?tab=transcript&line={line}`; a `decision`, `task` or `meeting` source links to its meeting.
- `updated_at` has microseconds and a `Z`. **Send it back exactly as received** as the next call's `revision`.

## 3. Change it: `PATCH /api/documents/{id}/board`

**One operation per call.** The body is the operation's fields plus `revision` (the draft's `updated_at` as your screen last saw it). The answer is **200 with the whole draft** (same shape as section 2): hand it to your state, and use its `updated_at` as the next `revision`. `edited` becomes `true`. Needs the creator's login and `X-CSRF-Token`.

| `op` | Fields | Notes |
|---|---|---|
| `move_card` | `card`, `to_column`, `index` | what a drag and the "Move to column…" menu both send |
| `rename_column` | `column`, `title` | |
| `add_column` | `title`, optional `index` | at the end by default; at most 12 columns |
| `delete_column` | `column` | deletes its cards too; the last column cannot be deleted. *Added beyond the plan's list, so a column made by mistake can be removed* |
| `add_card` | `column`, `text`, optional `kind` (default `note`), `owner`, `index` | at the end by default; at most 30 cards in a column |
| `edit_card` | `card`, and any of `text`, `owner`, `kind` | send only what changes; `owner: ""` clears it; at least one is required |
| `delete_card` | `card` | |
| `recolour` | `card`, `kind` | colour follows the kind, so this gives the card another kind |

Text is made plain (no HTML, no link, no Markdown marks) and cut: a column name at 100 characters, card text at 300, an owner at 80. A card keeps its sources when moved or edited; a new card has none.

Real exchanges (`05-move-card.json`; request, then the changed column of the answer):

```json
{ "revision": "2026-11-24T14:47:41.721572Z", "op": "move_card", "card": 2, "to_column": 3, "index": 0 }
```
```json
{
  "type": "column",
  "title": "Parked",
  "cards": [{ "text": "Setup fee of 120,000 naira", "kind": "note", "owner": null, "sources": [] }]
}
```

The same file set shows each other operation: `06-move-card-within-a-column`, `07-move-card-from-menu-to-end` (what the menu sends: the end of the chosen column), `08-rename-column` (`<b>Do next</b>` came back as `Do next`), `09-add-column`, `10-add-card`, `11-edit-card`, `12-recolour`, `13-delete-card`, `14-delete-column`. After all of them `15-get-after-edits.json` shows what was saved, `edited: true`, and the audit worked out again from the cards as they now stand.

### Errors the board screen must handle

| Status | `code` | `message` (as returned) | Capture | What the screen does |
|---|---|---|---|---|
| 409 | `stale_board` | The board changed since you opened it. It has been reloaded. | `16-stale-screen`, `17-stale-index`, `18-stale-column` | the `revision` is old (another tab edited), or a column or card named is not there. **Reload the draft** (`GET`), show the message, drop the change |
| 409 | `writing` | This draft is still being written. | `03-edit-while-writing` | keep polling; do not offer edits |
| 409 | `not_ready` | This draft failed; make a new one. | `33-edit-failed-board` | show the failure, offer Make again |
| 409 | `not_a_board` | This draft is not a board. | `28-not-a-board` | a bug in the page: only mount `BoardView` for `output_type: "board"` |
| 422 | `title_empty` | Give the column a name. | `19-title-empty` | keep the name box open |
| 422 | `card_empty` | A card needs some text. | `20-card-empty` | keep the card form open (the form already disables Save for empty text) |
| 422 | `board_full` | A board holds at most 12 columns. | `29-board-full` | the "Add column" button is already disabled at 12 |
| 422 | `column_full` | A column holds at most 30 cards. | `30-column-full` | also when a card is moved **into** a full column; revert the optimistic move |
| 422 | `last_column` | A board keeps at least one column. | `31-last-column` | the delete button is already disabled for the last column |
| 422 | `revision_required` | Send the revision (the draft's updated_at) you last saw. | `21-revision-missing` | a bug in the page |
| 422 | `revision_invalid` | That revision is not a time. | `22-revision-bad` | a bug in the page |
| 422 | `validation_error` | Some fields are missing or not valid. (with `fields`) | `23-body-unknown-operation`, `24-body-extra-field` | a bug in the page. Bodies are strict: an unknown field or operation, a negative or text number, text over 2000 characters, an unknown `kind` |
| 403 | `csrf_rejected` | Missing or wrong X-CSRF-Token header. Read it from GET /api/session. | `25-no-csrf` | the client retries once with a fresh token |
| 404 | `not_found` | No such document. | `26-someone-elses-board`, `27-no-such-board` | another person's board is a 404, never a 403; **only the creator edits, also once a board is shared with a team** |

`board_full`, `column_full`, `title_empty`, `card_empty`, `last_column` and `stale_board` are written for the person: show `message` as it is. (`ApiError.isShowable` hides 422 messages as developer text, so `BoardView` shows these by code.)

## State machine

`writing` → `ready` (edit) or `failed`. A board is edited only when `ready`. One change at a time: the screen disables its controls while a call is in flight, and the revision changes with each answer.

## UX consequences the FE must surface

- **A stale screen is not an error to hide.** When `stale_board` comes back, say so and reload; the person's change did not happen.
- **A moved card shows in its new place at once** and goes back if the call fails; show why (the failure sentence above).
- **Only the creator can change a board.** For anyone else the screen shows no handle, no menu and no add controls, and says "Only the person who made this board can change it."
- **A card the person adds or rewrites has no source and may carry a figure the meeting never said.** The audit (`checks.flags`) is re-run on every read; show the warning on the column (`block_index`).
- **Choosing a column in the menu does not move the card; the Move button does.** A keyboard that arrows through a closed select fires `change` on each arrow in some browsers, which would move the card more than once.
- **On a phone the columns stack** (no sideways scroll). Dragging works there as best effort; the menu is the supported way.
- **The PNG is built from what is on the screen** (including edits), at twice the board's size (`Onboarding board.png`: a board of 1136 by 404 points came out 2272 by 808 pixels). It never goes to the server.

## Verification table

| Behaviour | Verified |
|---|---|
| Make a board; `GET` shows columns only, sources with server-written quotes, audit by column | live (captures 02, 04) |
| Each of the eight operations, with revision | live (05 to 14, 15) |
| `stale_board` (old revision, missing card, missing column) | live (16 to 18) |
| `writing`, `not_ready`, `not_a_board` | live (03, 33, 28) |
| `title_empty`, `card_empty`, `board_full`, `column_full`, `last_column` | live (19, 20, 29 to 31) |
| `revision_required`, `revision_invalid`, `validation_error` | live (21 to 24) |
| 403 without CSRF; 404 for another person and for a draft that does not exist | live (25 to 27) |
| A reply with no card fails the draft as `structure` | live (32) |
| PDF and Word of a board render | rendered in the capture script, **not over HTTP** (34); route is S2's |
| Teammate the board is shared with gets 404 | **not live**; `tests/test_api_recipe_board.py::test_only_the_creator_edits_even_a_teammate_it_is_shared_with` |
| Two tabs at once: the second is `stale_board` | test only (`test_two_edits_at_once_cannot_both_win`); the revision check runs under a row lock |
| Drag with a mouse in a real browser (1280 px); the Move menu; the PNG file is 2272 by 808 for a 1136 by 404 board | live, real browser (SOP) |
| Drag with a finger | **not run** (the browser tool sends mouse input only); pointer events with `touch-action: none` on the handle, tested with simulated touch events |
| The real model's grouping of a meeting into themes | **not run** (S7) |
