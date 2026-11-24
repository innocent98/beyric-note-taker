# Frontend integration guide: meeting recipes, the draft API (slice S1)

Everything the **Make** flow needs from the API to ask for a draft, wait for it, show it, edit it and discard it. **Every payload, status code and error string below is pasted from a real response** captured by `scripts/capture_recipes.py` against the running app (files in `docs/fe-captures/recipes/`, named in each block; run the script again to re-derive any of them).

**What was NOT real in the captures** (also stated in the verification table):

- **The model.** No OpenAI key or network was used: a fake stood in for the client. It returns the strict-JSON shape the code asks for and cites real line numbers it is shown. A real model's wording will differ; the shape of every response body will not.
- **The starter recipes.** `app/recipes/library.py` and `GET /api/recipes` belong to slice S3 and are not on this branch. The capture registered one starter, `follow_up_email`. The final starter keys are S3's (`follow_up_email, proposal, requirements, marketing_post, decision_memo, sop, action_tracker, board, status_report, client_briefing` in the plan).
- **`plan_inactive` (403)** was not run live (it needs `FEATURE_GATES_MODE=enforce` and a person with no plan). Its shape is the project's usual error shape; the message text comes from `feature_gates.plan_inactive_message("make")`: *"Making documents needs an active plan or trial. You can still read, search and export your notes, and use your tasks and decisions."* (verified in `tests/test_recipe_jobs.py`, not over HTTP).
- **The daily cap** was 6 in the capture (`RECIPE_DAILY_LIMIT=6`); the default is 20, and the message names the number in force.
- **Not in this slice:** exports (Word, PDF, Markdown, CSV, Excel), sharing, the recipe library, the team brief, the meeting picker and boards. Each has its own guide text when its slice lands.

## What it is, in your terms

A person presses **Make** on a ready meeting, picks a recipe (a starter such as "Follow-up email", or one of their own) and presses **Make draft**. Beyric writes a document from the meeting: blocks of headings, paragraphs, bullets and tables, each remembering the transcript lines it was drafted from. The page shows "Writing…", polls, and opens the document when it is ready. Facts the meeting did not give are left as visible **blanks** (`[need: start date]`) and figures that appear in neither the meeting, the notes, the tasks nor the team brief are marked **check this**. The person edits in place, regenerates (a new draft) or discards.

A draft is **private to the person who made it** (sharing is a later slice). Another person's draft is a **404**, never a 403.

## Endpoints

All need a login. Every write needs the `X-CSRF-Token` header (from `GET /api/session`). The team in play is the optional `X-Team-Id` header (the active team: it decides whose brief and team recipes are used; omitted = the person's default team).

### 1. `POST /api/documents`: ask for a draft

Body (`DocumentIn`): exactly one of `recipe_key` (a starter) or `recipe_id` (a recipe row of the person's or their active team's); `meeting_ids` (1 to 12; one for one-meeting recipes); `options` (the Make dialog's choices: text, number or true/false values, at most 10); `use_brief` (default true).

`POST /api/documents` as **ada** → **202** *(capture `07-create.json`)*

```json
{
  "method": "POST",
  "path": "/api/documents",
  "body": {
    "recipe_key": "follow_up_email",
    "meeting_ids": [
      1
    ],
    "options": {
      "tone": "friendly",
      "recipient": "Daniel"
    }
  }
}
```

```json
{
  "id": 1,
  "title": "Follow-up email",
  "output_type": "email",
  "recipe_key": "follow_up_email",
  "recipe_id": null,
  "status": "writing",
  "edited": false,
  "meeting_ids": [
    1
  ],
  "created_at": "2026-11-24T13:39:06.998047Z",
  "updated_at": "2026-11-24T13:39:06.998047Z",
  "blocks": [],
  "checks": {
    "blanks": [],
    "flags": []
  },
  "stale": false,
  "sources_available": true,
  "left_out_count": 0,
  "failure_code": null,
  "failure_message": null
}
```

Always **202** with `status: "writing"` and `blocks: []`. Nothing is written by the request; the worker writes it a moment later.

**Double press.** The same request (same recipe, same meetings, same `options`) while that draft is still `writing` returns **the same draft** (same `id`), starts nothing and does not count against the day's limit *(capture `08-create-again-same-request.json`: 202, `id` 1 again)*. A different `options` value, or asking after the draft is finished, makes a new draft.

**Using a recipe row** *(capture `22-create-with-recipe-row.json`)*: the request body is `{"recipe_id": 1, "meeting_ids": [4], "use_brief": false}`; the answer has `recipe_key: null` and `recipe_id: 1`:

```json
{
  "id": 2,
  "title": "Short thank-you",
  "output_type": "email",
  "recipe_key": null,
  "recipe_id": 1,
  "status": "writing"
}
```

### 2. `GET /api/documents/{id}`: poll, then read

`GET /api/documents/1` while writing *(capture `09-get-writing.json`)* → **200**:

```json
{
  "id": 1,
  "title": "Follow-up email",
  "output_type": "email",
  "recipe_key": "follow_up_email",
  "recipe_id": null,
  "status": "writing",
  "edited": false,
  "meeting_ids": [
    1
  ],
  "created_at": "2026-11-24T13:39:06.998047Z",
  "updated_at": "2026-11-24T13:39:06.998047Z",
  "blocks": [],
  "checks": {
    "blanks": [],
    "flags": []
  },
  "stale": false,
  "sources_available": true,
  "left_out_count": 0,
  "failure_code": null,
  "failure_message": null
}
```

Poll every **2 seconds** while `status` is `writing` (the worker normally takes a few seconds; a long meeting up to a minute or two). Stop on `ready` or `failed`.

`GET /api/documents/1` when ready *(capture `11-get-ready.json`)* → **200**. Read this one closely: the model wrote a setup fee of 120,000 naira that is nowhere in the meeting, cited a transcript line (99) that does not exist, and left one blank.

```json
{
  "id": 1,
  "title": "Thanks for today, next steps on the onboarding",
  "output_type": "email",
  "recipe_key": "follow_up_email",
  "recipe_id": null,
  "status": "ready",
  "edited": false,
  "meeting_ids": [
    1
  ],
  "created_at": "2026-11-24T13:39:06.998047Z",
  "updated_at": "2026-11-24T13:39:07.095237Z",
  "blocks": [
    {
      "type": "paragraph",
      "text": "Subject: Thanks for today, next steps on the onboarding",
      "sources": []
    },
    {
      "type": "paragraph",
      "text": "Hi Daniel, thank you for your time today.",
      "sources": [
        {
          "kind": "line",
          "meeting_id": 1,
          "line": 1,
          "quote": "Thanks for joining, Daniel.",
          "ref": null
        }
      ]
    },
    {
      "type": "paragraph",
      "text": "As agreed, the onboarding is 50,000 naira and starts on 2026-12-01. The setup fee is 120,000 naira. Our first call is [need: call date].",
      "sources": [
        {
          "kind": "line",
          "meeting_id": 1,
          "line": 2,
          "quote": "We would like the onboarding for 50,000 naira,",
          "ref": null
        }
      ]
    },
    {
      "type": "bullets",
      "items": [
        {
          "text": "Ada sends the plan by Friday.",
          "sources": [
            {
              "kind": "line",
              "meeting_id": 1,
              "line": 3,
              "quote": "Agreed. I will send the plan by Friday.",
              "ref": null
            }
          ]
        }
      ]
    }
  ],
  "checks": {
    "blanks": [
      {
        "block_index": 2,
        "what": "call date"
      }
    ],
    "flags": [
      {
        "block_index": 2,
        "text": "120,000 naira",
        "kind": "figure"
      }
    ]
  },
  "stale": false,
  "sources_available": true,
  "left_out_count": 0,
  "failure_code": null,
  "failure_message": null
}
```

What to take from it:

- `blocks` is the document. Each block has a `type`: `heading` (`level` 1 to 3, `text`), `paragraph` (`text`, `sources`), `bullets` (`items`, each `text` and `sources`), `table` (`columns`: list of strings; `rows`: list of rows, each a list of cells `{text, sources}`) and, for boards later, `column` (`title`, `cards`). **Text is plain**: no HTML, no links, no Markdown. Show it escaped.
- A **source** is `{kind: "line", meeting_id, line, quote, ref}`. For a line source, link to `/meetings/{meeting_id}?tab=transcript&line={line}`. `quote` is the first words of that line, written by the server from the real line (never the model's): if the line at that number no longer starts with those words, the meeting was edited, so degrade to "open the meeting". The nonexistent line 99 was **dropped by the server**: you never see a source that was not sent to the model. Other kinds (`meeting`, `decision`, `task` with a `ref`) appear in cross-meeting drafts.
- `checks.blanks` lists every `[need: ...]` token with the block it is in; highlight them and warn on Copy ("1 blank still to fill"). `checks.flags` lists figures to check: `kind: "figure"` = not found in the meeting, notes, tasks or brief; `kind: "derived"` = looks worked out from figures that are there (a sum, "two weeks" from "14 days"). **They are advisory**: show a warning with the list, never block Copy or export. Here `120,000 naira` is flagged and `50,000 naira` and `2026-12-01` are not (the meeting said them).
- `block_index` is the 0-based position in `blocks`.
- `title` is the model's title for the document; before it is ready it is the recipe's name.

### 3. `GET /api/documents`: a person's drafts

`GET /api/documents` *(capture `12-list.json`)* → **200**:

```json
{
  "documents": [
    {
      "id": 1,
      "title": "Thanks for today, next steps on the onboarding",
      "output_type": "email",
      "recipe_key": "follow_up_email",
      "recipe_id": null,
      "status": "ready",
      "edited": false,
      "meeting_ids": [
        1
      ],
      "created_at": "2026-11-24T13:39:06.998047Z",
      "updated_at": "2026-11-24T13:39:07.095237Z"
    }
  ]
}
```

Newest first, the caller's own only. `?meeting_id=1` keeps the drafts made from that meeting, cross-meeting ones included *(capture `13-list-for-meeting.json`)*; a meeting with none gives `{"documents": []}` *(capture `14-list-other-meeting.json`)*. Paging: `limit` (1 to 100, default 50) and `before_id` (pass the smallest `id` you have). A list item has no `blocks` and no `checks`. (`?shared=` for drafts shared with the team arrives with slice S4.)

### 4. `PATCH /api/documents/{id}`: save an edit

Body: **every block as it now stands** (not a diff), and optionally a new `title` (up to 200 characters). A block may keep the `sources` it has and **cannot gain new ones** (a source the draft did not already cite is silently dropped). Text is cleaned as at generation (HTML stripped, length bounded). The draft becomes `edited: true`; `checks` in the answer is worked out again from what was saved.

`PATCH /api/documents/1` *(capture `15-patch.json`; the body replaced the 120,000 with 50,000, added a heading and a table, and tried to add a source to line 3)* → **200**:

```json
{
  "id": 1,
  "title": "Next steps",
  "output_type": "email",
  "recipe_key": "follow_up_email",
  "recipe_id": null,
  "status": "ready",
  "edited": true,
  "meeting_ids": [
    1
  ],
  "created_at": "2026-11-24T13:39:06.998047Z",
  "updated_at": "2026-11-24T13:39:09.304710Z",
  "blocks": [
    {
      "type": "heading",
      "level": 1,
      "text": "Next steps"
    },
    {
      "type": "paragraph",
      "text": "As agreed, the onboarding is 50,000 naira and starts on 2026-12-01. The setup fee is 50,000 naira. Our first call is [need: call date].",
      "sources": [
        {
          "kind": "line",
          "meeting_id": 1,
          "line": 2,
          "quote": "We would like the onboarding for 50,000 naira,",
          "ref": null
        },
        {
          "kind": "line",
          "meeting_id": 1,
          "line": 3,
          "quote": null,
          "ref": null
        }
      ]
    },
    {
      "type": "table",
      "columns": [
        "Step",
        "Owner"
      ],
      "rows": [
        [
          {
            "text": "Send the plan",
            "sources": []
          },
          {
            "text": "Ada",
            "sources": []
          }
        ]
      ]
    }
  ],
  "checks": {
    "blanks": [
      {
        "block_index": 1,
        "what": "call date"
      }
    ],
    "flags": []
  },
  "stale": false,
  "sources_available": true,
  "left_out_count": 0,
  "failure_code": null,
  "failure_message": null
}
```

The fixed figure is no longer flagged; the blank is still listed, now at `block_index` 1 because the blocks changed. The new paragraph's `sources` kept only the original line 2 source.

Refusals:

| When | Capture | Status and body |
|---|---|---|
| Draft still `writing` | `10-patch-while-writing` | 409 `writing`: This draft is still being written.
| Draft `failed` | `26-patch-failed` | 409 `not_ready`: This draft failed; make a new one.
| A table row that does not match its columns | `16-patch-bad-table` | 422 `blocks_invalid`: Those blocks cannot be saved. Check the tables and try again.
| No blocks at all | `17-patch-empty` | 422 `blocks_invalid`: Those blocks cannot be saved. Check the tables and try again.
| Body not the right shape | `18-patch-bad-body` | 422 `validation_error`: Some fields are missing or not valid.
| No CSRF token | `19-patch-no-csrf` | 403 `csrf_rejected`: Missing or wrong X-CSRF-Token header. Read it from GET /api/session.

(`422 validation_error` also carries a `fields` list, see capture `18-patch-bad-body.json`.)

### 5. `DELETE /api/documents/{id}`: discard

**204**, no body *(capture `40-delete.json`)*. It also cancels a draft still being written. It does **not** give back a slot of the day's limit (the model may already have been called). Afterwards `GET` is 404 *(capture `41-get-after-delete`)* and so is a second `DELETE` *(capture `42-delete-again`)*.

**Regenerating is not an endpoint**: press Make again (a new `POST`) and you get a new draft; the old one stays until the person discards it.

## Errors the FE must handle

Every error is `{"error": {"code", "message"}}`; show `message` as is.

| Where | Status | `code` | Message (exact) | Capture |
|---|---|---|---|---|
| Any, not signed in | 401 | `not_authenticated` | Log in first.
| Any write, no or wrong CSRF token | 403 | `csrf_rejected` | Missing or wrong X-CSRF-Token header. Read it from GET /api/session.
| `POST`, neither or both of `recipe_key`/`recipe_id` | 422 | `recipe_invalid` | Name the recipe with exactly one of recipe_key and recipe_id.
| `POST`, no meetings / malformed body | 422 | `validation_error` (with `fields`) | Some fields are missing or not valid.
| `POST`, no such starter or recipe | 404 | `not_found` | No such recipe.
| `POST`, a meeting that is not the caller's, or does not exist | 404 | `not_found` | No such meeting.
| `POST`, the notes are not written yet | 409 | `not_ready` | The notes are not ready yet.
| `POST`, `X-Team-Id` of a team the caller is not in | 404 | `not_found` | No such team.
| `POST`, the day's limit reached | 429 | `recipe_limit` | You have made 6 documents today. Try again tomorrow.
| `POST`, no live plan or trial (enforce mode only) | 403 | `plan_inactive` | the text in the notes above | **not run live** |
| `GET`/`PATCH`/`DELETE` another person's draft, or none | 404 | `not_found` | No such document.
| `PATCH` while writing | 409 | `writing` | This draft is still being written.
| `PATCH` a failed draft | 409 | `not_ready` | This draft failed; make a new one.
| `PATCH`, blocks that do not fit | 422 | `blocks_invalid` | Those blocks cannot be saved. Check the tables and try again.

## A draft that fails

A failed draft is **200** with `status: "failed"`, empty `blocks`, a `failure_code` and a plain `failure_message` to show *(capture `25-get-failed.json`)*:

```json
{
  "id": 3,
  "status": "failed",
  "failure_code": "model_reply",
  "failure_message": "The writing service sent back something unusable. Try again.",
  "blocks": []
}
```

Codes the server uses (the message for each is the sentence in `app/recipes/service.py` `FAILURE_MESSAGES`; only `model_reply` was captured live, the rest are verified in `tests/test_recipe_jobs.py`): `model_reply` and `structure` (the writing service sent back something unusable), `model_error` (it did not answer), `empty` (nothing in the meeting to write from), `too_long`, `gave_up` (stopped after three tries), `not_ready`, `recipe_gone`, `owner_gone`, `access_gone` (the person left the team or lost the recipe before it ran), `error`. Offer "Try again" (a new `POST`). A failed attempt **counts** against the day's limit when the model was reached; `empty`, `too_long`, `not_ready`, `recipe_gone`, `owner_gone` and `access_gone` do not (no model call).

## Field-nesting traps

- `checks` is **inside** the draft (`checks.blanks`, `checks.flags`), not a separate call, and is recomputed on every read from the **current** blocks, so it is right after an edit and changes if the meeting's notes change.
- `meeting_ids` is on both the list item and the full draft; `blocks`, `checks`, `stale`, `sources_available`, `left_out_count`, `failure_*` are **only on the full draft**.
- `recipe_key` and `recipe_id` are both present on every draft and exactly one is non-null.
- `sources` is on paragraphs, bullet **items** and table **cells** (inside each cell object), never on the bullets block or the table block itself, and not on headings.
- A table cell is `{text, sources}`, not a string; `columns` **is** a list of strings.
- `quote` and `ref` are present and may be null; a `meeting` or `task` source has `line: null`.
- The 404 for another person's draft is indistinguishable from a draft that does not exist, by design.

## States and polling

| `status` | Meaning | The FE does |
|---|---|---|
| `writing` | Queued or being written | Show "Writing…", poll every 2 s, disable edit; Discard works |
| `ready` | Blocks are there | Show the document, `checks`, edit, Copy |
| `failed` | Gave up or was refused by the checks | Show `failure_message` and "Try again"; edit is refused |

A draft can stay `writing` if the worker is down; it is retried up to three times and then fails with `gave_up`. Stop polling when the page closes.

## UX consequences the FE must surface

- **Blanks warn before Copy and export**: "1 blank still to fill". Highlight each `[need: ...]` in place. It is a warning, not a block.
- **Flagged figures are a warning with the list, never a block**, and they must be worded as "check this", not "wrong": the figure may be right and simply not in the meeting (the person may have typed it). Re-read `checks` after every save.
- **`stale: true`** means a meeting's notes or transcript, or the team brief, changed after the draft was made (*capture `21-get-stale.json`*): label the draft "older than the meeting". Regenerating makes a new draft; it never overwrites.
- **`sources_available: false`** means none of the draft's meetings can be opened by the caller (*capture `23-get-meeting-deleted.json`*): show source links as not available. In that state **`checks.flags` is computed against nothing, so every figure is flagged** (the capture shows the figures the meeting did say flagged too): do not present the flags as findings then.
- **`left_out_count`** is how many chosen meetings were left out (not readable by the caller, or not ready). When above 0, say so on the draft ("1 meeting was left out"). It is 0 for one-meeting recipes (those are refused instead).
- **The day's limit** (20 by default) counts every attempt that reached the model, failures and regenerations included, and a discarded draft is not given back. When the 429 arrives, show the message; it clears 24 hours after the oldest counted attempt.
- **A source never grants access.** Do not build a link that assumes the viewer can open the meeting for a draft that is not theirs.
- **Editing is whole-document**: send all blocks. A lost update from two tabs is last-write-wins.

## Verification

| Behaviour | How verified |
|---|---|
| 202 queued, polling, ready with blocks and checks | **Verified live** (captures 07, 09, 11) |
| Double press returns the same draft | **Verified live** (capture 08) |
| Server-written `quote`; unknown source dropped | **Verified live** (capture 11) |
| Blanks and flagged figures in `checks` | **Verified live** (captures 11, 15) |
| PATCH: saved, edited, audit recomputed, new source refused | **Verified live** (capture 15, 20) |
| PATCH while writing 409; on a failed draft 409; bad blocks 422; no CSRF 403 | **Verified live** (10, 26, 16, 17, 18, 19) |
| List, filter by meeting, empty list | **Verified live** (12, 13, 14) |
| 401, CSRF 403, 404s, 409 `not_ready`, 422s | **Verified live** (06, 27 to 34) |
| Another person's draft is 404 on GET, PATCH, DELETE and absent from their list | **Verified live** (35 to 38) |
| `stale` and `sources_available` | **Verified live** (21, 23) |
| Failed draft: `failure_code`, `failure_message` | **Verified live** for `model_reply` (24, 25); other codes **not run live**, verified in `tests/test_recipe_jobs.py` |
| Daily limit 429 | **Verified live** with a limit of 6 (capture 39); the default 20 is a setting |
| DELETE 204 and 404 after | **Verified live** (40 to 42) |
| `plan_inactive` 403 | **Not run live**; `tests/test_recipe_jobs.py::test_the_make_gate_refuses_before_anything_is_written` |
| The model's real wording, real latency | **Not exercised**: a fake model. Measure on the live run in slice S7 |
| `X-Team-Id` choosing the brief and team recipes | Wrong team 404 verified live (34); team recipes and the brief arrive with slice S3 |
