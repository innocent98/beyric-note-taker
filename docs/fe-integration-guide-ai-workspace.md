# Frontend integration guide: AI workspace W1 (Decisions, Review, extraction state)

Everything below is copied from responses captured against the running app by
`scripts/capture_workspace_api.py`: the real app under uvicorn, real migrations, a real Postgres,
the worker's real extraction step (`workspace.process_next`) with **real calls to `gpt-6-luna`**,
and real HTTP. The files are in `docs/fe-captures/ai-workspace-w1/NN-name.json`; `NN` is cited as
**#NN**. Run the script again to re-derive any claim. The meetings are synthetic (the AI1 eval's),
and the users are placeholders (`ada@example.test`, `ben@example.test`). CSRF tokens are masked
`<csrf-A>`. Where something could **not** be exercised live, the text says so inline and section 9
lists it.

Read this with `docs/fe-integration-guide-owner-app.md` (session cookie, CSRF, error shape,
meetings, vocabulary): all of it still holds. Scope: the **API** of the Decisions and Review screens
and the extraction state on the meeting page. No screen is built yet. Types: `docs/openapi.json` is
updated; run `npm run gen:api` in `web/` to refresh `lib/api/schema.d.ts` (not done in this change).

## 0. How every call works (unchanged)

- Same origin, `/api/...`, JSON, the `session` cookie; `GET /api/session` first for `csrf_token`.
- Every call that is not a GET sends `X-CSRF-Token`. Without it: 403 `csrf_rejected` (#26, #43).
- Errors are `{"error": {"code", "message"}}`; validation errors add `fields`. Map on `code`.
- Logged out: 401 `not_authenticated` (#02).

## 1. What this is, in the frontend's terms

After a meeting's notes are written, the app reads the notes' **Decisions**, **Open questions** and
**Please check** bullets into lists, without asking a model, so the **Decisions** screen can search
every decision across meetings and the **Review** screen can list everything that needs a second
look. One small model call per meeting looks at each Please check item and, when it is about a name
or term that may have been misheard, supplies `heard_as` (the word as it was transcribed) so the
screen can show "Heard as X. Correct spelling:". That call can fail without harm: the lists are
already there, only the `heard_as` hints are missing.

Open questions are stored now but have **no endpoint yet** (they arrive with the series screens).

## 2. The meeting's extraction state: `extraction_status` on `GET /api/meetings/{id}`

New field on the meeting **detail** only (not on the list). Null unless the meeting is `ready`.

| Value | Meaning | What the screen does |
|---|---|---|
| `null` | notes not written yet, or a meeting from before this feature that the backfill has not reached | nothing |
| `pending` | queued, or failed once or twice and waiting to retry (at least 5 minutes apart) | show "Reading the notes..."; the Decisions and Review lists may not have this meeting yet |
| `running` | the worker is on it right now | same as `pending` |
| `done` | finished | lists are complete; `heard_as` set where the model found a misheard term |
| `failed` | gave up after 3 tries | lists are there, **`heard_as` hints are missing**. Offer nothing alarming. "Rewrite the notes" queues it again (#46) |
| `skipped` | an older meeting: decisions only, no open questions, no Review items | Decisions screen has it; Review will not. No action to offer in W1 |

**#07** `GET /api/meetings/1` -> **200**

Notes are ready, extraction not run yet.

```json
{
  "id": 1,
  "status": "ready",
  "step": "done",
  "extraction_status": "pending"
}
```

**#11** `GET /api/meetings/1` -> **200**

After the worker ran.

```json
{
  "id": 1,
  "status": "ready",
  "step": "done",
  "extraction_status": "done"
}
```

**#12** `GET /api/meetings/3` -> **200**

A meeting with nothing under Please check makes **no** model call and is `done` straight away.

```json
{
  "id": 3,
  "status": "ready",
  "extraction_status": "done"
}
```

**#46** `POST /api/meetings/1/regenerate` with body `{"template_id": 5}` -> **200**

Rewriting the notes queues the extraction again (`pending`). The response is the meeting; nothing else changes shape.

```json
{
  "id": 1,
  "status": "ready",
  "extraction_status": "pending"
}
```

**#49** `GET /api/meetings/5` -> **200**

A call that fails (here OpenAI refused a model name, for real) goes back to `pending`... 

```json
{
  "id": 5,
  "status": "ready",
  "extraction_status": "pending"
}
```

**#51** `GET /api/meetings/5` -> **200**

...and after the third failure is `failed`. The three attempts were captured one at a time (#49, #50, #51) by moving the clock past the retry gap.

```json
{
  "id": 5,
  "status": "ready",
  "extraction_status": "failed"
}
```

**#54** `GET /api/meetings/6` -> **200**

The backfill gave this old meeting its decisions only.

```json
{
  "id": 6,
  "status": "ready",
  "extraction_status": "skipped"
}
```

**Polling.** Poll `GET /api/meetings/{id}` while the value is `pending` or `running`. A suggested
cadence is every 5 seconds for up to 2 minutes, then stop and show the lists as they are. **This
timing is a suggestion, not measured**: the worker picks the meeting up on its next loop, which is
seconds when idle; the captures call the step directly, so they do not show elapsed time.

## 3. Decisions: `GET /api/decisions?q=&limit=&before_id=`

Only the caller's meetings, newest meeting first (by meeting id), hidden decisions left out. The
response is **grouped by meeting**; inside a group the decisions are in the notes' order.

**#13** `GET /api/decisions` -> **200**

Three meetings, grouped. `title` is the meeting code label (`Google Meet <code>`, `Zoom <number>`), the same as `GET /api/meetings`; it is **not** the calendar title.

```json
{
  "groups": [
    {
      "meeting": {
        "id": 3,
        "title": "Google Meet vag-cccc-ddd",
        "platform": "google_meet",
        "created_at": "2026-11-02T20:30:51.726855Z"
      },
      "decisions": [
        {
          "id": 4,
          "text": "Keep the data export in CSV for version one; Excel can wait."
        },
        {
          "id": 5,
          "text": "Leave the work estimate unassigned for now."
        },
        {
          "id": 6,
          "text": "The dashboard has no firm date; the target is the coming weeks."
        }
      ]
    },
    {
      "meeting": {
        "id": 2,
        "title": "Zoom 81234567890",
        "platform": "zoom",
        "created_at": "2026-11-01T20:30:51.725590Z"
      },
      "decisions": [
        {
          "id": 2,
          "text": "Renew the vendor for one more year."
        },
        {
          "id": 3,
          "text": "Confirm the claimed discount in writing before relying on it."
        }
      ]
    },
    {
      "meeting": {
        "id": 1,
        "title": "Google Meet nat-aaaa-bbb",
        "platform": "google_meet",
        "created_at": "2026-10-31T20:30:51.722621Z"
      },
      "decisions": [
        {
          "id": 1,
          "text": "Proceed with the Metrika rollout in the next release."
        }
      ]
    }
  ],
  "has_more": false,
  "next_before_id": null
}
```

- `q` (at most 200 characters) matches a decision's words, case-insensitively, **or** the meeting's
  title/code. `%` and `_` are plain characters. A **title match shows every decision of that
  meeting**; a words match shows only the matching decisions.

**#14** `GET /api/decisions?q=metrika` -> **200**

Words match: only the matching decision of the meeting.

```json
{
  "groups": [
    {
      "meeting": {
        "id": 1,
        "title": "Google Meet nat-aaaa-bbb",
        "platform": "google_meet",
        "created_at": "2026-10-31T20:30:51.722621Z"
      },
      "decisions": [
        {
          "id": 1,
          "text": "Proceed with the Metrika rollout in the next release."
        }
      ]
    }
  ],
  "has_more": false,
  "next_before_id": null
}
```

**#15** `GET /api/decisions?q=81234567890` -> **200**

Meeting-code match: all decisions of that meeting.

```json
{
  "groups": [
    {
      "meeting": {
        "id": 2,
        "title": "Zoom 81234567890",
        "platform": "zoom",
        "created_at": "2026-11-01T20:30:51.725590Z"
      },
      "decisions": [
        {
          "id": 2,
          "text": "Renew the vendor for one more year."
        },
        {
          "id": 3,
          "text": "Confirm the claimed discount in writing before relying on it."
        }
      ]
    }
  ],
  "has_more": false,
  "next_before_id": null
}
```

**#16** `GET /api/decisions?q=zebra` -> **200**

No match: empty list, 200 (show "No decisions match your search.").

```json
{
  "groups": [],
  "has_more": false,
  "next_before_id": null
}
```

- `limit` counts **meetings**, not decisions (default 20, 1 to 50). For the next page pass
  `before_id` = `next_before_id` of the previous page.

**#17** `GET /api/decisions?limit=1` -> **200**

Page one with `limit=1` (the `groups` are in #17; trimmed here to the paging fields).

```json
{
  "has_more": true,
  "next_before_id": 3
}
```

**#18** `GET /api/decisions?limit=1&before_id=3` -> **200**

Page two. `next_before_id` is null on the last page.

```json
{
  "has_more": true,
  "next_before_id": 2
}
```

### Hide a decision: `POST /api/decisions/{id}/hide`

No body. **204 with no body**, and hiding again is 204 too. It hides, never edits; a meeting whose
decisions are all hidden drops out of the list.

**#21** `POST /api/decisions/4/hide` -> **204**

(no body)

**#22** `POST /api/decisions/4/hide` -> **204**

Again: same.

(no body)

**#23** `GET /api/decisions?limit=1` -> **200**

The list afterwards (`limit=1`): decision 4 is gone from the group.

```json
{
  "groups": [
    {
      "meeting": {
        "id": 3,
        "title": "Google Meet vag-cccc-ddd",
        "platform": "google_meet",
        "created_at": "2026-11-02T20:30:51.726855Z"
      },
      "decisions": [
        {
          "id": 5,
          "text": "Leave the work estimate unassigned for now."
        },
        {
          "id": 6,
          "text": "The dashboard has no firm date; the target is the coming weeks."
        }
      ]
    }
  ]
}
```

Errors: someone else's or an unknown id is **404** `not_found` (#24, #25), never 403; a non-numeric
id is a plain 404 (#27); without the CSRF header 403 (#26); `limit=0` or `limit=51` is 422
`validation_error` with `fields` (#19, #20).

**#24** `POST /api/decisions/7/hide` -> **404**

```json
{
  "error": {
    "code": "not_found",
    "message": "No such decision"
  }
}
```
**#19** `GET /api/decisions?limit=0` -> **422**

```json
{
  "error": {
    "code": "validation_error",
    "message": "Some fields are missing or not valid.",
    "fields": [
      {
        "field": "limit",
        "message": "Input should be greater than or equal to 1"
      }
    ]
  }
}
```

## 4. Review: `GET /api/review?state=open|checked`

Only the caller's items (owner only: a teammate never sees them, even later). `state` defaults to
`open`; anything else is 422 (#30). Newest meeting first (by meeting id), in the notes' order. At
most 200 come back; `truncated` says there were more.

**#29** `GET /api/review` -> **200**

`heard_as` is set only on the item the model judged to be a misheard term. The third item is a number ("four seven two or four seven three"), and the model gave it no `heard_as`: **show the "Correct spelling" box only when `heard_as` is not null.**

```json
{
  "items": [
    {
      "id": 3,
      "text": "The claimed discount is unverified: Olu said, “the vendor said Daniel agreed a discount last year. I have no proof of it.”",
      "heard_as": null,
      "checked_at": null,
      "meeting": {
        "id": 2,
        "title": "Zoom 81234567890",
        "platform": "zoom",
        "created_at": "2026-11-01T20:30:51.725590Z"
      }
    },
    {
      "id": 1,
      "text": "Dan said, “I have only heard it said out loud, so I am not sure about the spelling.”",
      "heard_as": "Metrika",
      "checked_at": null,
      "meeting": {
        "id": 1,
        "title": "Google Meet nat-aaaa-bbb",
        "platform": "google_meet",
        "created_at": "2026-10-31T20:30:51.722621Z"
      }
    },
    {
      "id": 2,
      "text": "Dan said the contract number was “something like four seven two or four seven three.”",
      "heard_as": null,
      "checked_at": null,
      "meeting": {
        "id": 1,
        "title": "Google Meet nat-aaaa-bbb",
        "platform": "google_meet",
        "created_at": "2026-10-31T20:30:51.722621Z"
      }
    }
  ],
  "open_count": 3,
  "truncated": false
}
```

**Field-nesting traps.** `open_count` is the number of **open** items in all, whatever `state` you
asked for (it is the sidebar badge), not the length of `items`. `meeting` is nested in each item.
`checked_at` is always present: `null` while open. `heard_as` is always present, often `null`.

### Check, uncheck: `POST /api/review/{id}/check` and `/uncheck`

No body; both return the updated item. Checking again keeps the first time.

**#31** `POST /api/review/1/check` -> **200**

```json
{
  "id": 1,
  "text": "Dan said, “I have only heard it said out loud, so I am not sure about the spelling.”",
  "heard_as": "Metrika",
  "checked_at": "2026-11-03T20:31:04.573551Z",
  "meeting": {
    "id": 1,
    "title": "Google Meet nat-aaaa-bbb",
    "platform": "google_meet",
    "created_at": "2026-10-31T20:30:51.722621Z"
  }
}
```

**#32** `POST /api/review/1/check` -> **200**

Again: the same `checked_at` as #31.

```json
{
  "checked_at": "2026-11-03T20:31:04.573551Z"
}
```

**#33** `GET /api/review?state=checked` -> **200**

The checked list (items are in #33; trimmed to the counters).

```json
{
  "open_count": 2,
  "truncated": false
}
```

**#34** `POST /api/review/1/uncheck` -> **200**

Uncheck.

```json
{
  "id": 1,
  "checked_at": null
}
```

### Add to vocabulary: `POST /api/review/{id}/vocabulary`

Body `{"term": "..."}`. Adds the corrected spelling to the user's vocabulary **and marks the item
checked**. It does **not** change this meeting's notes.

**#36** `POST /api/review/1/vocabulary` with body `{"term": "Metrika"}` -> **201**

```json
{
  "item": {
    "id": 1,
    "text": "Dan said, “I have only heard it said out loud, so I am not sure about the spelling.”",
    "heard_as": "Metrika",
    "checked_at": "2026-11-03T20:31:04.604828Z",
    "meeting": {
      "id": 1,
      "title": "Google Meet nat-aaaa-bbb",
      "platform": "google_meet",
      "created_at": "2026-10-31T20:30:51.722621Z"
    }
  },
  "term": {
    "id": 1,
    "term": "Metrika"
  }
}
```

**#35** `POST /api/review/1/vocabulary` with body `{"term": "  "}` -> **400**

An empty term: 400 `invalid_term` and **nothing changes** (the item stays open).

```json
{
  "error": {
    "code": "invalid_term",
    "message": "Type a term to add."
  }
}
```

**#38** `POST /api/review/1/vocabulary` with body `{"term": "Metrika"}` -> **409**

The term already exists: **409** `duplicate_term`, and the item is **still marked checked** (#39 lists it under `checked` right after).

```json
{
  "error": {
    "code": "duplicate_term",
    "message": "That term is already in your vocabulary."
  }
}
```

Not found: another user's item is 404 `not_found` for all three actions (#40, #41, #42).

**#40** `POST /api/review/4/check` -> **404**

```json
{
  "error": {
    "code": "not_found",
    "message": "No such review item"
  }
}
```

### The sidebar badge: `badges.review_open` on `GET /api/session`

New field, only on the session (nowhere else). `null` while logged out (#01), a number once logged
in.

**#01** `GET /api/session` -> **200**

Logged out.

```json
{
  "authenticated": false,
  "user": null,
  "badges": null
}
```

**#28** `GET /api/session` -> **200**

Logged in with three open items.

```json
{
  "authenticated": true,
  "badges": {
    "review_open": 3
  }
}
```

**#44** `GET /api/session` -> **200**

After two items were checked or moved: it follows the items.

```json
{
  "badges": {
    "review_open": 2
  }
}
```

## 5. Every error the FE must handle here

| Where | Status | `code` | Message (as returned) |
|---|---|---|---|
| any | 401 | `not_authenticated` | 'Log in first.' (#02) |
| any write without the header | 403 | `csrf_rejected` | see #26 |
| foreign or unknown id | 404 | `not_found` | see #24, #40 |
| `limit` or `before_id` out of range, `q` too long, bad `state` | 422 | `validation_error` | with `fields` (#19, #30) |
| vocabulary: empty or bad term | 400 | `invalid_term` | 'Type a term to add.' (#35) |
| vocabulary: term exists | 409 | `duplicate_term` | 'That term is already in your vocabulary.' (#38) |

## 6. UX consequences the FE must surface

- **A rewrite can bring a hidden or checked item back.** Items are matched by their **words**. When
  the notes are rewritten, an item the model words **identically** keeps its id and its hidden or
  checked state (shown live for a hidden decision in #48). One it words **differently** is a new row:
  a hidden decision reappears and a checked Review item comes back as open. Say so near "Rewrite
  notes" ("Items you hid or checked may come back if the wording changes").
- **Adding to vocabulary does not fix this meeting.** After #36, offer "Rewrite the notes with the
  corrected spelling" (`POST /api/meetings/{id}/regenerate`).
- **`heard_as` is a hint about a name or term**, never a number, a price or a sentence (the app
  drops those). Do not show the spelling box for anything else.
- **Older meetings are thin on purpose.** Decisions exist for every meeting; Review items and open
  questions only for meetings from the last 14 days at the time of the backfill (and for every
  meeting from now on). An empty Review screen for an old meeting is not a bug.
- **Nothing is deleted when extraction fails.** `failed` costs the user only the `heard_as` hints.

## 7. Server facts that shape the screens

- The model call asks only about Please check items (W1), one attempt with a 60 second limit and a
  capped answer. `ai_runs` shows what it cost, live (#10): about 600 input and 50 to 80 output
  tokens per meeting here (short synthetic meetings; an hour-long one will be larger).
- A failed call is recorded by exception class only, the real cause (`NotFoundError` in #52). A
  transient network failure (`APIConnectionError`) happened in two of the runs taken while
  preparing these captures; those runs were discarded and re-taken, so it is **not** in the
  committed files. It behaves as #49 to #51 show: the meeting goes back to `pending` and is tried
  again at least 5 minutes later, so `pending` can last longer than a few seconds.
- The backfill (#53) is `scripts/backfill_extraction.py`: decisions for every ready meeting, the
  full extraction queued for those of the last 14 days. Nothing is ever emailed.

## 8. Verification table

| Behaviour | Verified live? | Where |
|---|---|---|
| `extraction_status` null, `pending`, `done` on the meeting | yes | #07, #11, #12 |
| rewrite queues the extraction again | yes | #46 |
| failed call -> `pending` twice -> `failed` (a real OpenAI refusal) | yes | #49, #50, #51, #52 |
| old meeting -> `skipped`, decisions only | yes | #53, #54, #55 |
| decisions list, grouping, search by words and by code, paging, empty | yes | #13 to #18 |
| hide, hide again, hidden stays out, foreign and unknown id 404 | yes | #21 to #27 |
| a hidden decision stays hidden through a rewrite that keeps the wording | yes | #48 |
| review list, `heard_as` from the real model, check, uncheck, twice | yes | #29, #31 to #34 |
| add to vocabulary 201, empty 400, duplicate 409 with the item checked | yes | #35, #36, #38, #39 |
| foreign review ids 404 on all three actions | yes | #40 to #42 |
| `badges.review_open` null, 0, 3, 2 | yes | #01, #04, #28, #44 |
| logged out 401, missing CSRF 403, bad paging 422 | yes | #02, #26, #43, #19, #20, #30 |
| a rewrite that **rewords** a hidden decision or a checked item brings it back | **no**: follows from matching by words (unit-tested in `tests/test_workspace.py`); the live rewrite kept the wording | section 6 |
| `running` seen from outside | **no**: the state lasts the length of one call and the captures call the step directly | section 2 |
| time from `ready` to `done` with the real worker loop | **no**: not run with the worker process | section 2 |
| a long (hour or more) meeting | **no**: synthetic meetings of a few hundred words | section 7 |
| two workers at once | **no**: rows are taken with `FOR UPDATE SKIP LOCKED`, tested for one worker only | |
| shape of Open questions | **n/a**: no endpoint in W1 | section 1 |
