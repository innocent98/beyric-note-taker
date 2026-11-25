# Frontend integration guide: meeting recipes (the whole feature)

One guide for the whole release: make a document from a meeting, wait for it, edit it, share it with the team, export it, discard it; the recipe library and the team brief; cross-meeting recipes; the board. **Every payload, status code and error string in this file is pasted from a real response** captured against the running app (files in `docs/fe-captures/recipes/`, named in each block; each capture script says how to run it again). Where something could not be run live it is marked **not run live** inline and in the verification table at the end.

## Where each part lives

| Part | Read | Real captures |
|---|---|---|
| The draft API (make, poll, read, list, edit, discard) | this file, sections 1 to 5 | `docs/fe-captures/recipes/` (44 exchanges, `scripts/capture_recipes.py`) |
| **Sharing, what a reader sees, stale, deleting a meeting** | this file, "Sharing and the reader's view" and "When a meeting changes or goes" | `docs/fe-captures/recipes/integration/` (40 exchanges, `scripts/recipes_live.py capture`) |
| Exports (Word, PDF, Markdown, text, CSV, Excel) and the document screen | `docs/fe-integration-guide-recipes-documents.md` | `docs/fe-captures/recipes/exports/` |
| The eighteen starters, recipes, the team brief, file import | `docs/fe-integration-guide-recipes-library.md` | `docs/fe-captures/recipes-library/` |
| The meeting picker and cross-meeting drafts | `docs/fe-integration-guide-recipes-cross-meeting.md` | `docs/fe-captures/recipes-cross-meeting/` |
| The board (columns, cards, moves, PNG and PDF) | `docs/fe-integration-guide-recipes-board.md` | `docs/fe-captures/recipes/board/` |
| The live pass of every starter on seeded meetings, and export timings | the SOP `docs/sop/2026-11-24-recipes-integration.md` | `docs/fe-captures/recipes/live-pass/` |

**What was NOT real in the captures** (the same for every part):

- **The model.** No OpenAI key is configured on the development machine, so a stand-in model wrote every captured draft. It returns the strict-JSON shape the code asks for and copies real lines of the prompt it was sent. A real model's wording, latency and token counts will differ; the shape of every response body will not.
- **`plan_inactive` (403)** was not run live (it needs `FEATURE_GATES_MODE=enforce` and a person with no plan). Shape and message come from `tests/test_recipe_jobs.py`: *"Making documents needs an active plan or trial. You can still read, search and export your notes, and use your tasks and decisions."*
- **The daily cap** was 6 in the engine capture; the default is 20, and the message names the number in force.
- **The second app in front** (nginx, the production Next build) was not in the loop for any capture; the browser pass used the Next dev server in front of the real API.

## What it is, in your terms

A person presses **Make** on a ready meeting, picks a recipe (a starter such as "Follow-up email", or one of their own) and presses **Make draft**. Beyric writes a document from the meeting: blocks of headings, paragraphs, bullets and tables, each remembering the transcript lines it was drafted from. The page shows "Writing…", polls, and opens the document when it is ready. Facts the meeting did not give are left as visible **blanks** (`[need: start date]`) and figures that appear in neither the meeting, the notes, the tasks nor the team brief are marked **check this**. The person edits in place, regenerates (a new draft) or discards.

A draft is **private to the person who made it** until they share it, read-only, with their team (see "Sharing and the reader's view"). Anyone else's draft is a **404**, never a 403.

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

Newest first. With no filter the list is the caller's **own** drafts; `?shared=true` lists drafts teammates shared with them, `?mine=true&shared=true` both (see the sharing section for the extra row fields). `?meeting_id=1` keeps the drafts made from that meeting, cross-meeting ones included *(capture `13-list-for-meeting.json`)*; a meeting with none gives `{"documents": []}` *(capture `14-list-other-meeting.json`)*. Paging: `limit` (1 to 100, default 50) and `before_id` (pass the smallest `id` you have). A list item has no `blocks` and no `checks`.

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

## Sharing and the reader's view

A person can share a **ready** draft, read-only, with **the team it was made in** (`team` on the draft). Every current member of that team can then open it. Nobody else can. This is the contract the share dialog and the Documents page are built on.

### The rules the screen must not break

1. **Sharing a draft shares its words and its title, never its meetings.** The draft's text and title are visible to every teammate even if they cannot open the meeting it was made from, and both can reveal what was said. The share dialog must say so: **"Teammates will read this draft even if they cannot open the meeting."** plus that the title and text can reveal what was said, and that a teammate who cannot open all its meetings sees a generic title.
2. **A draft is shared with the team it was made in, only.** `POST .../share` takes no team and ignores `X-Team-Id`. A draft made in the person's own (personal) space cannot be shared; neither can one whose creator has left its team. The draft says so in advance: **`share_blocked`** is a plain sentence (or null) on the creator's full draft: show it and disable Share.
3. **Access is looked up on every request.** Stopping the share, a reader leaving the team, or the creator leaving the team ends access at once, and a share never comes back if the creator later joins again (leaving deletes their shares). Handle a 404 on a draft the person had open.
4. **Every person's view is redacted by what they can read.** For anyone (the creator too) a source of a meeting they cannot read is `{"kind": "hidden"}`.

### 6. `POST /api/documents/{id}/share` and `DELETE /api/documents/{id}/share`

Creator only (another person's draft, including a reader's attempt, is a 404). Needs `X-CSRF-Token`; no team in the request. Asking twice changes nothing.

*Share* (capture `07-share-private-draft.json`): `POST /api/documents/2/share` as ada (creator) → **200**

Shares with the active team (the default team here; send `X-Team-Id` to name another). 200 with the whole draft: `shared_with` now lists the team.

```json
{
  "id": 2,
  "title": "Follow-up email",
  "output_type": "email",
  "recipe_key": "follow_up_email",
  "recipe_id": null,
  "status": "ready",
  "edited": false,
  "meeting_ids": [
    9
  ],
  "created_at": "2026-11-24T22:00:21.266421Z",
  "updated_at": "2026-11-24T22:00:21.396397Z",
  "access": "owner",
  "owner_name": null,
  "shared_with": [
    {
      "id": 2,
      "name": "Acme Sales"
    }
  ],
  "team": {
    "id": 2,
    "name": "Acme Sales"
  },
  "blocks": [
    {
      "type": "paragraph",
      "text": "Subject: Thanks for today, next steps",
      "sources": []
    },
    {
      "type": "paragraph",
      "text": "Hi there,",
      "sources": []
    },
    {
      "type": "paragraph",
      "text": "Thank you for your time today. Here is a short recap of what we covered.",
      "sources": [
        {
          "kind": "line",
          "meeting_id": 9,
          "line": 1,
          "quote": "The acquisition of Orion for 9 million dollars",
          "ref": null
        }
      ]
    }
  ],
  "checks": {
    "blanks": [
      {
        "block_index": 4,
        "what": "date of the next step"
      }
    ],
    "flags": []
  },
  "stale": false,
  "sources_available": true,
  "left_out_count": 0,
  "failure_code": null,
  "failure_message": null,
  "options": {},
  "use_brief": true,
  "can_edit": true,
  "share_blocked": null,
  "left_out_message": null
}
```

*Share again* (capture `08-share-again.json`): `POST /api/documents/2/share` as ada (creator) → **200**

Asking twice changes nothing and is not an error.

```json
{
  "id": 2,
  "shared_with": [
    {
      "id": 2,
      "name": "Acme Sales"
    }
  ],
  "team": {
    "id": 2,
    "name": "Acme Sales"
  },
  "access": "owner"
}
```

`DELETE` answers **204** with no body and removes the share for every team at once; asking again is also 204 *(capture `21-unshare.json`)*.

Errors, all **409** with the project error shape: `not_ready` ("Only a finished draft can be shared." — **not run live**, verified in `tests/test_recipe_visibility.py`), `team_left` ("You are no longer in the team this draft was made in, so it cannot be shared." — **not run live**, same file) and `personal_team`:

*Share a draft made in a personal space* (capture `25-share-from-a-personal-space.json`): `POST /api/documents/3/share` as eve (outsider, own team) → **409**

409 `personal_team`: the draft was made in her personal space, which has no team to share it with. Show the server's message (the same sentence is in `share_blocked` on the draft).

```json
{
  "error": {
    "code": "personal_team",
    "message": "This draft was made in your own space. Make it in a team workspace to share it with that team."
  }
}
```

### 7. What a teammate reads: `GET /api/documents/{id}` as a reader

New fields on **every** draft and list row: `access` (`"owner"` or `"shared"`), `owner_name` (the creator's name for a shared draft, `"Former member"` once their account is gone, null for your own), `shared_with` (list of `{id, name}`: the teams the creator shared it with; **always empty for a reader**) and `team` (`{id, name}`: the team the draft was made in; creator's view, null for a reader). On the full draft also `can_edit` (false for a reader) and `share_blocked` (string or null, the creator's ready drafts only).

*Before it is shared* (capture `04-teammate-before-share.json`): `GET /api/documents/2` as tunde (teammate) → **404**

404, not 403: a teammate cannot tell the draft exists.

```json
{
  "error": {
    "code": "not_found",
    "message": "No such document."
  }
}
```

*A reader who cannot read the source meeting (THE TRAP)* (capture `10-teammate-reads-hidden-sources.json`): `GET /api/documents/2` as tunde (teammate) → **200**

THE TRAP: Tunde cannot read the private meeting. Every source of it is `{"kind": "hidden"}` (`meeting_id`, `line`, `quote`, `ref` all null), `meeting_ids` is `[]`, `sources_available` false, `left_out_count` 0, `options` empty, `can_edit` false, `access` `shared`. The draft's own words ARE visible: that is what the share dialog warns about.

```json
{
  "id": 2,
  "title": "Follow-up email, 24 Nov",
  "output_type": "email",
  "recipe_key": "follow_up_email",
  "recipe_id": null,
  "status": "ready",
  "edited": false,
  "meeting_ids": [],
  "created_at": "2026-11-24T22:00:21.266421Z",
  "updated_at": "2026-11-24T22:00:21.396397Z",
  "access": "shared",
  "owner_name": "Ada Lovelace",
  "shared_with": [],
  "team": null,
  "blocks": [
    {
      "type": "paragraph",
      "text": "Subject: Thanks for today, next steps",
      "sources": []
    },
    {
      "type": "paragraph",
      "text": "Hi there,",
      "sources": []
    },
    {
      "type": "paragraph",
      "text": "Thank you for your time today. Here is a short recap of what we covered.",
      "sources": [
        {
          "kind": "hidden",
          "meeting_id": null,
          "line": null,
          "quote": null,
          "ref": null
        }
      ]
    }
  ],
  "checks": {
    "blanks": [
      {
        "block_index": 4,
        "what": "date of the next step"
      }
    ],
    "flags": [
      {
        "block_index": 3,
        "text": "9 million dollars",
        "kind": "figure"
      }
    ]
  },
  "stale": false,
  "sources_available": false,
  "left_out_count": 0,
  "failure_code": null,
  "failure_message": null,
  "options": {},
  "use_brief": false,
  "can_edit": false,
  "share_blocked": null,
  "left_out_message": null
}
```

Read this response carefully:

- `blocks[].sources` entries for a meeting the reader may not read are **`{"kind": "hidden", ...}`** with `meeting_id`, `line`, `quote` and `ref` all **null**. `kind: "hidden"` exists only in what a person is sent; it is never stored and an edit cannot write it. Render it as a "not available" chip and **never build a link from it** (`meeting_id` is null).
- **`title` is generic** ("Follow-up email, 24 Nov": the recipe name and the day it was made) for a reader who cannot read EVERY source meeting, because the model's title can carry a hidden meeting's title. The creator, and a reader who can read all the sources, get the real title. The same rule applies to the title in the list rows.
- `meeting_ids` holds **only the meetings the reader may read** (here `[]`). `sources_available` is false when that list is empty. `left_out_count` is 0, `options` is `{}` and `use_brief` is false for a reader: those are the creator's choices.
- `checks` (blanks and flagged figures) is worked out from what the reader can read: a figure that only a hidden meeting contains **is flagged "check this" for the reader**. `stale` is **false** for a reader who cannot read every source meeting (they cannot tell).
- `can_edit` is false: hide Edit, Share, Regenerate and Discard. Copy, Open in mail app and Download still work (a reader may export the draft's text, `GET .../export` answers 200 *(capture `20-teammate-exports-markdown.json`)*; an export never contains sources, and for a reader who cannot read every source meeting its file name and heading use the generic title).

*A reader who CAN read the meeting* (capture `11-teammate-reads-open-sources.json`): `GET /api/documents/1` as tunde (teammate) → **200**

Tunde CAN read the sales call: sources keep their meeting ids, lines and quotes; `meeting_ids` lists it.

```json
{
  "id": 1,
  "title": "Proposal",
  "output_type": "document",
  "recipe_key": "proposal",
  "recipe_id": null,
  "status": "ready",
  "edited": false,
  "meeting_ids": [
    1
  ],
  "created_at": "2026-11-24T22:00:20.955484Z",
  "updated_at": "2026-11-24T22:00:21.167163Z",
  "access": "shared",
  "owner_name": "Ada Lovelace",
  "shared_with": [],
  "team": null,
  "blocks": [
    {
      "type": "paragraph",
      "text": "Thanks for making the time, Daniel. I wanted to go through what your team needs for onboarding.",
      "sources": [
        {
          "kind": "line",
          "meeting_id": 1,
          "line": 1,
          "quote": "Thanks for making the time, Daniel. I wanted",
          "ref": null
        }
      ]
    },
    {
      "type": "paragraph",
      "text": "So the first goal is route assignment and customer tracking.",
      "sources": [
        {
          "kind": "line",
          "meeting_id": 1,
          "line": 5,
          "quote": "So the first goal is route assignment and",
          "ref": null
        }
      ]
    }
  ],
  "checks": {
    "blanks": [
      {
        "block_index": 9,
        "what": "when"
      },
      {
        "block_index": 9,
        "what": "when"
      }
    ],
    "flags": [
      {
        "block_index": 11,
        "text": "450,000 naira",
        "kind": "figure"
      }
    ]
  },
  "stale": false,
  "sources_available": true,
  "left_out_count": 0,
  "failure_code": null,
  "failure_message": null,
  "options": {},
  "use_brief": false,
  "can_edit": false,
  "share_blocked": null,
  "left_out_message": null
}
```

Sources then keep their `meeting_id`, `line` and `quote`, exactly as the creator sees them, and the title is the real one.

**The creator's own view is redacted the same way.** If the creator has lost access to a meeting (it was unshared from the team, they left the team) or it was deleted, its sources come back as `hidden` for them too (see the cross-meeting capture in "Deleting a meeting" below): show "not available", never a link. `meeting_ids` for the creator still lists the draft's existing meetings.

### 8. The list, with and without sharing

*Drafts shared with me* (capture `12-teammate-list-shared.json`): `GET /api/documents?shared=true` as tunde (teammate) → **200**

Rows carry `access: shared` and `owner_name`; `meeting_ids` only has meetings the reader may read.

```json
{
  "documents": [
    {
      "id": 2,
      "title": "Follow-up email, 24 Nov",
      "output_type": "email",
      "recipe_key": "follow_up_email",
      "recipe_id": null,
      "status": "ready",
      "edited": false,
      "meeting_ids": [],
      "created_at": "2026-11-24T22:00:21.266421Z",
      "updated_at": "2026-11-24T22:00:21.396397Z",
      "access": "shared",
      "owner_name": "Ada Lovelace",
      "shared_with": [],
      "team": null
    }
  ]
}
```

*My own drafts (the default)* (capture `13-teammate-list-own-is-empty.json`): `GET /api/documents` as tunde (teammate) → **200**

The default list is the caller's OWN drafts.

```json
{
  "documents": []
}
```

*Both* (capture `14-teammate-list-both.json`): `GET /api/documents?mine=true&shared=true` as tunde (teammate) → **200**

Both scopes together.

```json
{
  "documents": [
    {
      "id": 2,
      "title": "Follow-up email, 24 Nov",
      "output_type": "email",
      "recipe_key": "follow_up_email",
      "recipe_id": null,
      "status": "ready",
      "edited": false,
      "meeting_ids": [],
      "created_at": "2026-11-24T22:00:21.266421Z",
      "updated_at": "2026-11-24T22:00:21.396397Z",
      "access": "shared",
      "owner_name": "Ada Lovelace",
      "shared_with": [],
      "team": null
    }
  ]
}
```

*The creator's list: who can read each draft* (capture `15-owner-list-shows-shared-with.json`): `GET /api/documents` as ada (creator) → **200**

The creator's list: `shared_with` shows who can read each draft.

```json
{
  "documents": [
    {
      "id": 2,
      "title": "Follow-up email",
      "output_type": "email",
      "recipe_key": "follow_up_email",
      "recipe_id": null,
      "status": "ready",
      "edited": false,
      "meeting_ids": [
        9
      ],
      "created_at": "2026-11-24T22:00:21.266421Z",
      "updated_at": "2026-11-24T22:00:21.396397Z",
      "access": "owner",
      "owner_name": null,
      "shared_with": [
        {
          "id": 2,
          "name": "Acme Sales"
        }
      ],
      "team": {
        "id": 2,
        "name": "Acme Sales"
      }
    }
  ]
}
```

Rows for a reader carry `meeting_ids` cut to the meetings they may read, `owner_name` and the generic title where it applies. A draft that stopped being shared, or whose team the reader left, is simply **absent** from `?shared=true` *(capture `23-teammate-list-after-unshare.json`)*. `?meeting_id=N` with `shared=true` never lists a shared draft of a meeting the caller cannot read.

### Every write is the creator's

A reader's `PATCH`, `DELETE`, `POST .../share`, `DELETE .../share` and the board's `PATCH .../board` are all **404** `not_found` "No such document." *(captures 16 to 19; the board route is verified in `tests/test_recipe_visibility.py`, not captured)*. After the creator stops sharing, the reader's `GET` is 404 again *(capture `22-teammate-after-unshare.json`)*.

*Someone outside the team* (capture `06-outsider-before-share.json`): `GET /api/documents/2` as eve (outsider, own team) → **404**

404 for anyone outside the team, shared or not.

```json
{
  "error": {
    "code": "not_found",
    "message": "No such document."
  }
}
```

### A board is saved only through its own route

`PATCH /api/documents/{id}` on a **board** is refused with **409 `use_board`** ("A board is changed one step at a time. Use the board's own screen to edit it."): the board route carries the revision check and the row lock, and a whole-document save from an old screen could undo a move. The board screen never calls the plain save.

*Plain save of a board* (capture `32-plain-save-of-a-board-is-refused.json`): `PATCH /api/documents/6` as ada (creator) → **409**

409 `use_board`: a board is changed one step at a time through `PATCH /api/documents/{id}/board` (it carries the revision check). The plain whole-document save would let a stale screen undo a move.

```json
{
  "error": {
    "code": "use_board",
    "message": "A board is changed one step at a time. Use the board's own screen to edit it."
  }
}
```

## When a meeting changes or goes

### Stale ("older than the meeting text")

`stale` is true when any of the draft's meetings' **notes or transcript** (a notes rewrite, a transcript or speaker edit) or the **team brief** changed after the draft was made. Label the draft "older than the meeting text". It is a label, not an error: the draft still reads and exports. **Regenerate** is a plain new `POST /api/documents` with the same recipe, meetings and options: it makes a **new** draft (a new `id`) and never overwrites the stale one. A person's own edit does not make a draft stale.

*Before the notes change* (capture `27-fresh-is-not-stale.json`): `GET /api/documents/4` as ada (creator) → **200**

`stale` false.

```json
{
  "id": 4,
  "stale": false
}
```

*After the notes change* (capture `28-after-the-notes-changed.json`): `GET /api/documents/4` as ada (creator) → **200**

`stale` true: label it 'older than the meeting text'. (The notes were changed straight in the database to stand for a rewrite; a transcript edit or a change of the brief does the same.)

```json
{
  "id": 4,
  "stale": true
}
```

*Regenerate* (capture `29-regenerate-makes-a-new-draft.json`): `POST /api/documents` as ada (creator) → **202**

Regenerate is a plain new POST: a NEW id; the stale draft is untouched.

```json
{
  "id": 5,
  "status": "writing"
}
```

*Not run live: a transcript edit through the transcript editor and a change of the brief (both are the same hash on the server, covered in `tests/test_recipe_integration.py`; the transcript editor is on `main`, not on this branch). Speaker corrections change the transcript text, so they also mark a draft stale.*

### Discarding a draft

`DELETE /api/documents/{id}` is 204. A draft the worker has **not started yet** gives its place in the day's limit back (no model was called); once the worker has started it, the attempt still counts. *(Not captured: the worker starts within a poll in the capture environment; verified in `tests/test_recipe_integration.py`.)*

### Deleting a meeting

`DELETE /api/meetings/{id}` deletes the drafts **made from that meeting alone**; a cross-meeting draft stays as long as at least one of its meetings does. Tell the person before they confirm (the meeting page's confirmation does: "This also deletes the 2 documents made only from it. A document made from several meetings stays; its link to this meeting will read not available.").

*Delete the meeting* (capture `36-delete-a-meeting.json`): `DELETE /api/meetings/3` as ada (creator) → **204**

204. The drafts made from this meeting ALONE are deleted with it; a cross-meeting draft stays.

```json
null
```

*Its single-meeting draft* (capture `37-its-single-meeting-draft-is-gone.json`): `GET /api/documents/7` as ada (creator) → **404**

404.

```json
{
  "error": {
    "code": "not_found",
    "message": "No such document."
  }
}
```

*The cross-meeting draft* (capture `38-the-cross-meeting-draft-stays.json`): `GET /api/documents/8` as ada (creator) → **200**

Still `ready`. `meeting_ids` no longer holds the deleted meeting; a source that still names it opens a 404 on the meeting page (show 'not available').

```json
{
  "id": 8,
  "status": "ready",
  "meeting_ids": [
    4
  ],
  "sources_available": true,
  "a source that pointed at the deleted meeting, as the creator now sees it (on a bullet item)": [
    {
      "kind": "hidden",
      "meeting_id": null,
      "line": null,
      "quote": null,
      "ref": null
    }
  ]
}
```

*The list for the deleted meeting* (capture `39-list-for-the-deleted-meeting.json`): `GET /api/documents?meeting_id=3` as ada (creator) → **200**

Empty.

```json
{
  "documents": []
}
```

A source on the surviving draft that pointed at the deleted meeting comes back `hidden` (see above): show it as "not available". The ledger keeps the cost of the deleted drafts (`ai_runs` rows are never removed with a draft) and they still count toward the day's limit, except a draft discarded before the worker started it.

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
| `GET`/`PATCH`/`DELETE`/`POST share`/`DELETE share` another person's draft, or none; a reader's write; a reader after unshare or after leaving the team | 404 | `not_found` | No such document. |
| `POST .../share` of a draft made in the person's own space | 409 | `personal_team` | This draft was made in your own space. Make it in a team workspace to share it with that team. |
| `POST .../share` when the creator left the draft's team | 409 | `team_left` | You are no longer in the team this draft was made in, so it cannot be shared. | **not run live** |
| `PATCH` of a board | 409 | `use_board` | A board is changed one step at a time. Use the board's own screen to edit it. |
| `POST .../share` while the draft is not ready | 409 | `not_ready` | Only a finished draft can be shared. | **not run live** |
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

A failed draft also carries `failure_detail` (string or null): for a `structure` failure, the checks' reason in words about the shape only ("block 6: table row 3 has 2 cells for 3 columns"), never meeting text. It is for diagnosis: **do not show it to the person**; show `failure_message`. *(Added after the first real-model run; not in the captures above, which predate it: shape from `tests/test_recipe_repair.py::test_the_detail_reaches_the_api_for_the_creator`. A `structure` failure is now asked for a second time by the server before it is reported, and the second call counts toward the day's limit.)*

Codes the server uses (the message for each is the sentence in `app/recipes/service.py` `FAILURE_MESSAGES`; only `model_reply` was captured live, the rest are verified in `tests/test_recipe_jobs.py`): `model_reply` and `structure` (the writing service sent back something unusable), `model_error` (it did not answer), `empty` (nothing in the meeting to write from), `too_long`, `gave_up` (stopped after three tries), `not_ready`, `recipe_gone`, `owner_gone`, `access_gone` (the person left the team or lost the recipe before it ran), `error`. Offer "Try again" (a new `POST`). A failed attempt **counts** against the day's limit when the model was reached; `empty`, `too_long`, `not_ready`, `recipe_gone`, `owner_gone` and `access_gone` do not (no model call).

## Field-nesting traps

- `checks` is **inside** the draft (`checks.blanks`, `checks.flags`), not a separate call, and is recomputed on every read from the **current** blocks, so it is right after an edit and changes if the meeting's notes change.
- `meeting_ids` is on both the list item and the full draft; `blocks`, `checks`, `stale`, `sources_available`, `left_out_count`, `failure_*` are **only on the full draft**.
- `recipe_key` and `recipe_id` are both present on every draft and exactly one is non-null.
- `sources` is on paragraphs, bullet **items** and table **cells** (inside each cell object), never on the bullets block or the table block itself, and not on headings.
- A table cell is `{text, sources}`, not a string; `columns` **is** a list of strings.
- `quote` and `ref` are present and may be null; a `meeting` or `task` source has `line: null`.
- `access`, `owner_name`, `shared_with`, `team` are on **both** the list row and the full draft; `can_edit` and `share_blocked` are **only** on the full draft.
- `title` is not always the model's title: for a reader who cannot read every source meeting it is "<recipe>, <day month>".
- `shared_with` is the creator's view: it is `[]` for a reader even though the draft is shared with them.
- A `hidden` source has `meeting_id: null`: any code that reads `source.meeting_id` must cope with null.
- `meeting_ids` is **not** the draft's full meeting list for a reader: it is the readable part.
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
- **`sources_available: false`** means none of the draft's meetings can be opened by the caller (for a reader it means they may read none of them) (*capture `23-get-meeting-deleted.json`*): show source links as not available. In that state **`checks.flags` is computed against nothing, so every figure is flagged** (the capture shows the figures the meeting did say flagged too): do not present the flags as findings then.
- **`left_out_count`** is how many chosen meetings were left out (not readable by the caller, or not ready). When above 0, say so on the draft ("1 meeting was left out"). It is 0 for one-meeting recipes (those are refused instead).
- **The day's limit** (20 by default) counts every attempt that reached the model, failures and regenerations included, and a discarded draft is not given back. When the 429 arrives, show the message; it clears 24 hours after the oldest counted attempt.
- **A source never grants access.** Do not build a link that assumes the viewer can open the meeting for a draft that is not theirs: use `sources[].kind` (`hidden`) and `meeting_ids`.
- **Sharing reveals the draft's words.** The share dialog must say "Teammates will read this draft even if they cannot open the meeting."
- **Deleting a meeting deletes its single-meeting drafts.** Say so in the delete confirmation.
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
| `X-Team-Id` choosing the brief and team recipes | Wrong team 404 verified live (34); team recipes and the brief: see the library guide |
| Share, share again, unshare (200, 200, 204) | **Verified live** (integration 07, 08, 21) |
| A reader: 404 before the share, hidden sources, readable sources, lists, export, writes 404, 404 after unshare, outsider 404 | **Verified live** (integration 04, 06, 10 to 23) |
| `personal_team` 409 (draft made in a personal space), `share_blocked` | **Verified live** (integration 25); `share_blocked` and `team_left` verified in `tests/test_recipe_visibility.py` |
| A share goes to the draft's own team whatever team is active; a share does not come back after leave and rejoin | **Not run live**; `tests/test_recipe_visibility.py` (mutation-checked) |
| The creator's own view redacts sources of a meeting they lost or that was deleted | **Verified live** for a deleted meeting (integration 38); lost access in `tests/test_recipe_visibility.py` |
| Generic title, reader `stale` false and checks from the readable view | **Verified live** (integration 10, 12: private meeting); readable case in `tests/test_recipe_visibility.py` |
| `use_board` 409 on a plain save of a board | **Verified live** (integration 32); a stale save cannot undo a move in `tests/test_api_recipe_board.py` |
| Discard before the worker starts gives the day's place back | **Not run live**; `tests/test_recipe_integration.py` (three tests) |
| `not_ready` 409 on share | **Not run live**; `tests/test_recipe_visibility.py` |
| Leaving the team ends a reader's access; the creator leaving ends the team's | **Not run live** (not captured; the membership is removed in the database in the tests); `tests/test_recipe_visibility.py` (two tests, mutation-checked) |
| A reader's board edit is 404 | **Not run live**; `tests/test_recipe_visibility.py` |
| `stale` after a notes change; regenerate makes a new draft | **Verified live** (integration 26 to 30) |
| `stale` after a transcript edit or a brief change | **Not run live**; `tests/test_recipe_integration.py` |
| Deleting a meeting: single-meeting draft gone, cross-meeting draft stays, list empty | **Verified live** (integration 34 to 39) |
| Account deletion removes drafts made only from the person's meetings | **Not run live**; `tests/test_recipe_integration.py::test_purging_an_account_runs_the_same_cleanup` |
