# Frontend integration guide: Ask Beyric

Everything the **Ask Beyric** page needs from the API: the scope line, the question chips, and an answer whose every point carries source chips. **Every payload, status code and error string below is pasted from a real response** captured by `scripts/capture_ask_api.py` against the running app (real HTTP, sessions, CSRF, team visibility, Postgres full-text retrieval, the checks on the model's reply, the cap, the ledger, migrations). Files: `docs/fe-captures/ask-beyric/`; run the script again to re-derive any of it. Names and addresses are placeholders. Shapes: `docs/openapi.json` (`web/lib/api/schema.d.ts`). This guide is for behaviour.

**One thing was NOT real in the captures: the model.** There is no OpenAI key or network in the capture, so a fake stood in for it (it cites the first passage it is shown, or misbehaves on purpose where a capture says so). The *wording* of a real answer will differ from the captured `"The answer drawn from the first passage."`; the *shape* of every response will not, because code builds the chips and checks the text. Anything that depends on a real model is marked **NOT LIVE** in the table at the end.

## What it is, in your terms

A page where a person types a question and gets an answer built only from meetings they may read: their own, and those shared with their team. Every point of an answer has one to three **source chips**; a chip opens the meeting (for a transcript passage, at the right row of the Transcript tab).

Three kinds of answer, one door (`POST /api/ask`):

| `kind` | When | Model call | Counts toward the daily cap |
|---|---|---|---|
| `tasks` | The question is about the person's own tasks ("my tasks", "assigned to me", "what did I commit to", "what am I supposed to do"), optionally with a date phrase | No | No |
| `answer` | Anything else, when passages were found and the model could support at least one point | One call | Yes |
| `none` | Nothing was found, or the model found nothing it could support | None when nothing was found | Only if a model call was made |

## Endpoints

Both need a login. `POST` needs the `X-CSRF-Token` header like every write.

### 1. `GET /api/ask/scope`: the scope line and the chips

Ada (owns Pricing review; a teammate's Design sync is shared with her team; the teammate's private meeting and a meeting still processing are not hers to read):

`GET /api/ask/scope` as **ada** → **200** *(capture `10-scope-ada.json`)*

```json
{
  "meetings": 2,
  "decisions": 1,
  "tasks": 2,
  "suggestions": [
    "What did we decide about pricing?",
    "What did I commit to last week?",
    "What did Daniel Okoro say?",
    "When is the launch?",
    "Which decisions were made on Monday?"
  ]
}
```

* "Searches **2** meetings, **1** decisions and **2** tasks": ready meetings the person may read, their unhidden decisions, and tasks on those meetings that the person may see. Same visibility rule as everything else; no model call.
* `suggestions` are the chips, in the designer's order. The third is built from the person's data: **"What did {Name} say?"**, the most frequent other speaker in their last 10 ready meetings. **It is left out when there is none**, so the list has 4 or 5 entries: **do not index it by position.** Capture `12-scope-eve.json` (a person with no meetings):

```json
{
  "meetings": 0,
  "decisions": 0,
  "tasks": 0,
  "suggestions": [
    "What did we decide about pricing?",
    "What did I commit to last week?",
    "When is the launch?",
    "Which decisions were made on Monday?"
  ]
}
```

* The name is the speaker label as it appears in the transcript (here a full name: `Daniel Okoro`).
* No meetings at all: `meetings` is 0 (show "Nothing to search yet. Meetings appear here after they end.").

### 2. `POST /api/ask`: ask

Body: `{"question": "..."}`. The question is trimmed; empty or more than 300 characters is a 422 `question_invalid`.

**An answer** (`kind: "answer"`). Ada: `What did we decide about pricing?` → **200** *(capture `13-ask-answer.json`)*

```json
{
  "kind": "answer",
  "lead": "Here is what your meetings say.",
  "points": [
    {
      "text": "The answer drawn from the first passage.",
      "cites": [
        {
          "meeting_id": 1,
          "title": "Pricing review",
          "date_label": "Mon 2 Nov",
          "owner_name": null,
          "shared": false,
          "target": "notes",
          "line": null
        }
      ]
    }
  ]
}
```

**A transcript passage** (`target: "transcript"`, with a `line`). Ada: `What did Daniel say?` → **200** *(capture `15-ask-transcript-line.json`)*

```json
{
  "kind": "answer",
  "lead": "Here is what your meetings say.",
  "points": [
    {
      "text": "The answer drawn from the first passage.",
      "cites": [
        {
          "meeting_id": 1,
          "title": "Pricing review",
          "date_label": "Mon 2 Nov",
          "owner_name": null,
          "shared": false,
          "target": "transcript",
          "line": 1
        }
      ]
    }
  ]
}
```

`line` is the **1-based row of the transcript as the meeting page draws it** (`web/lib/transcript.ts` `parseTranscript`: a `[Name]:` line starts a row, any other line continues the row above). Open the meeting at `?tab=transcript&line=N`. It is the row of the passage's first `[Name]:` line, so the passage may run on a few rows below it.

**A teammate's shared meeting.** Ada: `Tell me about the Zanzibar logo` → **200** *(capture `16-ask-shared-teammate-meeting.json`)*

```json
{
  "kind": "answer",
  "lead": "Here is what your meetings say.",
  "points": [
    {
      "text": "The answer drawn from the first passage.",
      "cites": [
        {
          "meeting_id": 2,
          "title": "Design sync",
          "date_label": "Tue 3 Nov",
          "owner_name": "Dan Okoro",
          "shared": true,
          "target": "notes",
          "line": null
        }
      ]
    }
  ]
}
```

`owner_name` is the owner's display name, present only for a meeting that is not the asker's; `shared` is true when the meeting is shared with a team. Chip label (lead-designed): `{title} · {date_label}` for the asker's own meeting, and `{title} · {date_label} · {first name of owner_name}` otherwise.

**Nothing found** (`kind: "none"`). Ada asks about a word that appears only in her teammate's **private** meeting: `What about the Mulberry reorganisation?` → **200** *(capture `17-ask-private-teammate-word.json`)*

```json
{
  "kind": "none",
  "lead": "I could not find that in your meetings.",
  "points": []
}
```

The same question from the meeting's owner (capture `18-ask-private-owner.json`) is an `answer`, so the private meeting is searchable only by the person who may read it. An unrelated question (`19-ask-nothing-retrieved.json`) is the same body. The words under it ("Try asking about a person, a project, a decision or a date.") are the page's, not the API's.

**The model found nothing it could support** is the same body (capture `20-ask-found-false.json`). It cost a model call, so it counts toward the cap; the FE cannot tell it from "nothing retrieved" and does not need to.

**Tasks** (`kind: "tasks"`). Ada: `What are my tasks?` → **200** *(capture `23-ask-tasks.json`)*

```json
{
  "kind": "tasks",
  "lead": "You have 2 open tasks assigned to you.",
  "points": [
    {
      "text": "Send the pricing sheet (due Fri 13 Nov, to do)",
      "cites": [
        {
          "meeting_id": 1,
          "title": "Pricing review",
          "date_label": "Mon 2 Nov",
          "owner_name": null,
          "shared": false,
          "target": "transcript",
          "line": 4
        }
      ]
    },
    {
      "text": "Prepare the reorganisation plan (to do)",
      "cites": []
    }
  ]
}
```

* Without a date phrase, only open tasks (not done) assigned to the person. Free: no model call, and it still works when the daily cap is spent (capture 23 was taken after the cap was reached in 22).
* **The second point has `"cites": []`.** That task came from a teammate's private meeting: the person may see the task (it is assigned to them) but not open the meeting, so there is no link. **Render a point with no chips; do not assume at least one.** For `kind: "answer"` every point has at least one cite.
* With a date phrase (`last week`, `this week`, `yesterday`, `today`, a weekday, `29 Oct`, `2026-10-29`) the list is the tasks **assigned** that day or week, in any status (the done ones show `(done)`). Ada: `What did I commit to last week?` (capture `24-ask-tasks-last-week.json`):

```json
{
  "kind": "tasks",
  "lead": "You have 2 tasks assigned to you last week.",
  "points": [
    {
      "text": "Send the pricing sheet (due Fri 13 Nov, to do)",
      "cites": [
        {
          "meeting_id": 1,
          "title": "Pricing review",
          "date_label": "Mon 2 Nov",
          "owner_name": null,
          "shared": false,
          "target": "transcript",
          "line": 4
        }
      ]
    },
    {
      "text": "Book the launch venue (done)",
      "cites": [
        {
          "meeting_id": 1,
          "title": "Pricing review",
          "date_label": "Mon 2 Nov",
          "owner_name": null,
          "shared": false,
          "target": "notes",
          "line": null
        }
      ]
    }
  ]
}
```

* A task whose quote was found in the transcript cites `transcript` with the `line` (point 1 above); otherwise it cites `notes` (point 2).
* At most 20 points; the lead then says `...; the first 20 are shown.`
* No tasks is still `kind: "tasks"`, with a lead that says so and no points (capture `25-ask-tasks-none.json`):

```json
{
  "kind": "tasks",
  "lead": "You have no open tasks assigned to you.",
  "points": []
}
```

## Errors the FE must handle

**The error envelope is `{"error": {"code", "message"}}`, the same as every other route. It is not `{"detail": ...}`** (the first design sketch used `detail`; it was wrong).

| Status | `code` | When | Body (captured) |
|---|---|---|---|
| 422 | `question_invalid` | Blank, or longer than 300 characters after trimming | ``{"error": {"code": "question_invalid", "message": "Type a question of up to 300 characters."}}`` |
| 422 | `validation_error` | The body is not `{"question": string}` (`fields` names the field) | ``{"error": {"code": "validation_error", "message": "Some fields are missing or not valid.", "fields": [{"field": "question", "message": "Input should be a valid string"}]}}`` |
| 429 | `ask_limit` | The person's daily cap is spent | ``{"error": {"code": "ask_limit", "message": "You have asked 5 questions today. Try again tomorrow."}}`` |
| 429 | `ask_rate` | More than 20 questions in a minute (the free ones count too) | ``{"error": {"code": "ask_rate", "message": "Slow down a little and try again in a minute."}}`` |
| 502 | `ask_failed` | The model gave no usable reply (not JSON, cut off, an error, a timeout) | ``{"error": {"code": "ask_failed", "message": "Beyric could not answer that. Try again."}}`` |
| 403 | `csrf_rejected` | No or wrong `X-CSRF-Token` | ``{"error": {"code": "csrf_rejected", "message": "Missing or wrong X-CSRF-Token header. Read it from GET /api/session."}}`` |
| 401 | `not_authenticated` | Not logged in | ``{"error": {"code": "not_authenticated", "message": "Log in first."}}`` |

Full bodies: captures `26`, `27`, `28`, `29`, `22`, `31`, `21`, `30`, `09`. The 422 for a body that is the wrong shape (capture 28):

```json
{
  "error": {
    "code": "validation_error",
    "message": "Some fields are missing or not valid.",
    "fields": [
      {
        "field": "question",
        "message": "Input should be a valid string"
      }
    ]
  }
}
```

**Two different 429s: tell them apart by `code`.** `ask_rate` is a per-minute brake (20 questions a minute, free ones included): show its message and let the person ask again in a minute. `ask_limit` is the daily cap: no retry. The `ask_limit` message says the **cap**, not a count: it is 30 in production (`ASK_DAILY_LIMIT`); the capture ran with 5, so it reads "5". The FE shows the message as it comes.

## Field-nesting traps

* **`cites` is on each point, not on the answer.** The answer has `kind`, `lead`, `points`; each point has `text` and `cites`.
* **`line` is null unless `target` is `"transcript"`**, and `target: "notes"` always has `line: null`.
* **`owner_name` is null for the asker's own meeting**, a name otherwise. `shared` is independent of it: the asker's own meeting that they have shared with the team has `owner_name: null, shared: true`.
* **`date_label` is a day, never a time** ("Mon 2 Nov"), in the asker's time zone. Nothing in the API gives a time of day.
* **A `tasks` answer with no points is a normal 200**, not a "could not find". Show its lead, not the "I could not find that" card.
* **`suggestions` has 4 or 5 entries.** Do not index it.
* **Text is plain.** `title`, `lead` and `text` are plain text (code strips Markdown, HTML and links from the model's words, and cleans meeting titles). Render them as text, never as HTML or Markdown, and do not turn anything in them into a link.

## States and timing

There is nothing to poll. One `POST` does all the work and answers when it is done.

* **A search question can take a while.** The model call has a 30-second timeout and one retry on a connection error, so the worst case is about 60 seconds. Show "Searching your meetings..." and **do not time the request out before about 65 seconds**. A question that finds nothing, and every `tasks` question, answers at once.
* One request at a time per person is enough: the server serialises one person's questions anyway (so two clicks cannot both pass the cap). Disable Ask while a question is running.

## UX consequences the FE must surface

* **The cap.** After the cap (30 model-calling questions in a rolling 24 hours) show the 429 message with **no retry button**. Task questions and questions that find nothing are free, so the person can still ask those.
* **A failed question still counts.** A 502 used one of the day's questions (the call was made). The designed "Try again" button is right, but each press spends one: do not auto-retry.
* **"Answers can be wrong. Check the sources."** under each answer (lead-designed). No thumbs, no report button: nothing is stored to receive them.
* **Nothing is kept.** The question and the answer are not stored or logged (the ledger keeps the account, model, token counts and time). The thread lives in page state only; a refresh clears it. The privacy page should say so (the two sentences are in the PR body).
* **A chip can outlive its meeting.** A meeting that is deleted or unshared after the answer was drawn answers 404 when its chip is clicked: show the toast "That meeting is no longer available".
* **A chip for a teammate's meeting says whose it is** (`owner_name`); a chip for the asker's own does not.
* **A point for a task with no chips is normal** (the person may see the task but not its meeting).

## Verification

| Behaviour | Verified |
|---|---|
| Scope counts for owner, teammate, outsider; hidden decision and unreadable-meeting task not counted (10 to 12) | live |
| Search answer, chip fields, shared meeting chip with `owner_name` and `shared: true` (13, 16) | live |
| Transcript cite with `line` (15) and task cite with `line` (23) | live |
| A teammate's private meeting never reaches the passages or the answer (17, 18) | live |
| Nothing retrieved is free and makes no model call (19, 32: no ledger row) | live |
| `found: false` is `none` (20) | live, **model faked** to say it |
| Unusable reply is 502 `ask_failed`, counted (21, 32) | live, **model faked** to return text that is not JSON |
| Cap reached is 429 `ask_limit` (22) | live, with the cap set to 5 (`ASK_DAILY_LIMIT=5`); the 30 default is unit-tested (`tests/test_ask.py::test_the_31st_question_is_429_and_the_message_says_how_many`) |
| Tasks path: list, date phrase, no tasks, a point with no chips; free over the cap (23 to 25) | live |
| 422 `question_invalid` and `validation_error`, 403 CSRF, 401 (08, 09, 26 to 30) | live |
| Per-minute limit, 429 `ask_rate` (31) | live (20 free questions, then the refusal); the window is unit-tested with the default of 20 in `tests/test_ask.py` |
| The ledger keeps no text (32) | live |
| The words of a **real** model's answer, a real timeout, the retry on a connection error | **NOT LIVE**: no key or network in the capture. Covered with a fake model in `tests/test_ask.py` (retry, errors, bad JSON, unknown ids, long text, HTML and Markdown) |
| Unknown passage ids dropped, point with no cite dropped, long text cut, HTML and Markdown made plain | **NOT LIVE** (needs a model that misbehaves): `tests/test_ask.py` |
| The rolling 24 hours (a question 25 hours ago does not count) | **NOT LIVE** (needs a 24-hour wait): `tests/test_ask.py::test_the_cap_is_rolling_24_hours_and_per_person` |
| `?tab=transcript&line=N` opens the Transcript tab at the row and highlights it | **NOT LIVE** here: it is the meeting page's job (the screens session); `line` is tested against the page's own splitting rules in `tests/test_ask.py::test_transcript_rows_split_exactly_as_the_meeting_page_does` |
