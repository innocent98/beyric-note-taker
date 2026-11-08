# Frontend guide: the Decisions record

**Module:** Decisions (spec `docs/superpowers/specs/2026-11-08-designer-v2-features-design.md`, section 3; audit rows B26 to B34, U6, U8, U9, U12). **Backend:** migration 0036, extraction `extract-v3`, `GET /api/decisions` (new fields), `PATCH /api/decisions/{id}` (new), `POST /api/decisions/{id}/hide` (unchanged).

**Every payload, status code and error string below is copied from `docs/fe-captures/decisions/`**, captured over real HTTP by `scripts/capture_decisions_api.py` against the running app (real migrations, auth, CSRF, owner rule, visibility, the worker's extraction step, the real checks on the model's answer, the real link rows, a real re-run). The one thing that is **not real** is the model: this session had no OpenAI key, so the model's answer to the extraction is a stand-in written for the capture (it says which decisions are proposed, who decided and which quote). The FE can rely on the *shape* and on everything done with the answer; the real model's choices come with the first live meeting. The stand-in holds a speaker nobody said, a quote nobody said and an action index that does not exist on purpose, to show the checks drop them. Names, addresses and passwords are placeholders (`<password placeholder>` in the files). Each block carries its file as `[capture NN]`.

## 1. What it is, in the screen's terms

The Decisions screen stops being a flat list and becomes a **record**: each decision has a **status** (Approved, Proposed, Replaced, Rejected), who **decided** it, the **words** that show it, a **Revisit on** day, the **tasks** that carry it out, and a **Replaced by / Replaces** link to other decisions.

* Approved or Proposed come from the meeting's AI reading (a decision that was only suggested is Proposed). **Replaced and Rejected are only ever set by a person.** Every decision that existed before this shipped reads as **Approved with nothing else** (no decider, no evidence, no tasks): show it as such (section 5 says what to draw for the missing evidence).
* **Only the person who recorded the meeting can change a decision's status or revisit day.** Teammates who can read it see the status and the date as plain text (no select). The flag is `can_edit` on each decision.
* A decision is **never changed by the AI after a person has touched it.** If the notes are read again, a status, revisit day or replaced-by the owner set stays exactly as they left it. (Who decided it and the evidence words do refresh.)

## 2. What changed, at a glance

| Call | Change |
|---|---|
| `GET /api/decisions` | Each decision gains `status, decided_by, quote, quote_speaker, line, revisit_on, replaced_by, replaces, related_tasks, can_edit`. The page gains `counts` and `due_this_week`. New query `status=` (a tab) |
| `PATCH /api/decisions/{id}` | **New.** Change `status`, `revisit_on`, `replaced_by_id` (owner of the meeting only) |
| `POST /api/decisions/{id}/hide` | Unchanged (Remove from log). 204 |

Nothing in the Decisions flow sends mail or a bell: **there is no notification for a revisit day** (the banner is the only surface).

## 3. `GET /api/decisions`

Query: `q` (search, up to 200 chars: matches a decision's words or its meeting's title), `status` (`approved`, `proposed`, `replaced`, `rejected`: one tab), `limit` (meetings per page, default 20, up to 50), `before_id` (the previous page's `next_before_id`). Auth: signed in (cookie); no CSRF needed for GET.

The owner's whole log, two meetings (the first listed is private), no filter `[capture 11]`:

```json
{
  "groups": [
    {
      "meeting": {
        "id": 2,
        "title": "Google Meet nat-pppp-rrr",
        "platform": "google_meet",
        "created_at": "2026-11-08T16:54:13.167501Z"
      },
      "decisions": [
        {
          "id": 4,
          "text": "Let the contractor go at the end of the month.",
          "status": "approved",
          "decided_by": null,
          "quote": null,
          "quote_speaker": null,
          "line": null,
          "revisit_on": null,
          "replaced_by": null,
          "replaces": [],
          "related_tasks": [],
          "can_edit": true
        }
      ]
    },
    {
      "meeting": {
        "id": 1,
        "title": "Google Meet nat-aaaa-bbb",
        "platform": "google_meet",
        "created_at": "2026-11-08T16:34:13.162482Z"
      },
      "decisions": [
        {
          "id": 1,
          "text": "Keep the free plan for two more months.",
          "status": "approved",
          "decided_by": "Dan",
          "quote": "I think we should keep the free plan for two more months.",
          "quote_speaker": "Dan",
          "line": 1,
          "revisit_on": null,
          "replaced_by": null,
          "replaces": [],
          "related_tasks": [
            {
              "id": 1,
              "title": "Update the pricing page",
              "status": "todo"
            }
          ],
          "can_edit": true
        },
        {
          "id": 2,
          "text": "Move the launch to 30 November.",
          "status": "approved",
          "decided_by": null,
          "quote": null,
          "quote_speaker": null,
          "line": null,
          "revisit_on": null,
          "replaced_by": null,
          "replaces": [],
          "related_tasks": [
            {
              "id": 2,
              "title": "Tell the customers about the new launch date",
              "status": "todo"
            }
          ],
          "can_edit": true
        },
        {
          "id": 3,
          "text": "Look at a lifetime deal at some point.",
          "status": "proposed",
          "decided_by": "Amina",
          "quote": "I suggest we also look at a lifetime deal, maybe later.",
          "quote_speaker": "Amina",
          "line": 4,
          "revisit_on": null,
          "replaced_by": null,
          "replaces": [],
          "related_tasks": [],
          "can_edit": true
        }
      ]
    }
  ],
  "has_more": false,
  "next_before_id": null,
  "counts": {
    "all": 4,
    "approved": 3,
    "proposed": 1,
    "replaced": 0,
    "rejected": 0
  },
  "due_this_week": 0
}
```

### Field notes (the traps)

| Field | What it is, and what to do |
|---|---|
| `status` | `approved` \| `proposed` \| `replaced` \| `rejected`. Drives the pill (Approved green, Proposed amber, Replaced grey, Rejected red) and greys the text of replaced and rejected ones |
| `decided_by` | A speaker's name, **only when the server found that name in the transcript**; otherwise `null` (as on decision 2 above, where the stand-in model named a person who is not in the meeting). Show "Decided by {name}" only when not null. **It is not the user who owns the meeting.** |
| `quote` | The words that show the decision, **only when the server found them in the transcript**; otherwise `null` |
| `quote_speaker`, `line` | Who said `quote` and its **1-based row in the transcript the meeting page shows**. `null` together when there is no `quote` **or** the quote could not be found again in the displayed transcript (so `quote` can be present with `line` null: show the words without the link). Never a time: none is stored |
| `revisit_on` | `YYYY-MM-DD` or `null`. A calendar day, no time zone: show it as written (for example "Revisit Sun 8 Nov 2026") |
| `replaced_by` | `{id, text}` of the decision that replaced this one, or `null`. Only filled for status `replaced`. `null` also when the person chose none **or the viewer may not read the chosen decision** |
| `replaces` | A **list** (a decision can replace several): decisions that name this one as their replacement and that the viewer may read. Show one "Replaces: ..." line each |
| `related_tasks` | `{id, title, status}` with status `todo` \| `doing` \| `done`, in the order of the meeting's notes. **Only tasks the viewer may see, and never a suggested task** (a task waiting under Suggested tasks is not listed until someone accepts it; a dismissed task is never listed). The list can differ between viewers of the same decision. The meta line's count is `related_tasks.length` |
| `can_edit` | `true` for the person who recorded the meeting. Render the Status select and the Revisit on field only when true; otherwise plain text |

**Nesting trap:** `counts` and `due_this_week` are on the **page**, next to `groups`, not inside a group or a decision. `can_edit` is on each **decision**, not on the group's `meeting`.

### Tabs, counts, banner

The five tabs are All / Approved / Proposed / Replaced / Rejected. Tab counts come from `counts` (`all` = the others added up), **over the viewer's whole log, whatever tab or search is showing** `[capture 15]` (the page with `?status=proposed`, `groups` left out):

```json
{
  "counts": {
    "all": 4,
    "approved": 3,
    "proposed": 1,
    "replaced": 0,
    "rejected": 0
  },
  "due_this_week": 0
}
```

Pick a tab with `?status=...`: the server returns only those decisions (grouped by meeting as always, paged by meeting). A tab with nothing in it `[capture 16]` answers `groups` empty with the counts unchanged:

```json
[]
```

"No decisions match." is for a tab or search with no groups; "No decisions yet." is for `counts.all == 0` with no search and no tab. A hidden decision (Remove from log) is in no group and no count. Not a valid status `[capture 18]`:

```json
422
{
  "error": {
    "code": "validation_error",
    "message": "Some fields are missing or not valid.",
    "fields": [
      {
        "field": "status",
        "message": "Input should be 'approved', 'proposed', 'replaced' or 'rejected'"
      }
    ]
  }
}
```

**The banner** ("{N} decision is / decisions are due to be revisited this week.", amber, calendar icon) shows when `due_this_week > 0`. It counts **approved and proposed** decisions the viewer may read whose `revisit_on` falls **this week, Monday to Sunday, in the viewer's own time zone** (their Settings zone; UTC until chosen). A decision whose day passed last week is not counted. After the owner set a revisit day of "today" `[capture 21]`: `"due_this_week": 1`.

### Who sees what (viewer cast: owner, teammate on the same team, outsider)

| Viewer | Result |
|---|---|
| Owner of the meetings | Everything, `can_edit: true` `[capture 11]` |
| Teammate, meeting **shared with the team** | The shared meeting's decisions only, `can_edit: false`; their counts cover only what they can read `[capture 13]` |
| Teammate, meeting **private** | Nothing: the decision is not listed, not counted, and its related tasks and links never appear |
| Someone on no shared team | `groups: []`, zero counts `[capture 14]` |

The teammate's view `[capture 13]`: `counts` `{"all": 3, "approved": 2, "proposed": 1, "replaced": 0, "rejected": 0}`, `due_this_week` `0`, one group (the shared meeting) whose decisions all have `"can_edit": false`. The outsider `[capture 14]`:

```json
{
  "groups": [],
  "has_more": false,
  "next_before_id": null,
  "counts": {
    "all": 0,
    "approved": 0,
    "proposed": 0,
    "replaced": 0,
    "rejected": 0
  },
  "due_this_week": 0
}
```

## 4. `PATCH /api/decisions/{id}`

Body: any of `status`, `revisit_on`, `replaced_by_id`; send only what changes; unknown fields are refused. `revisit_on: null` clears the day; `replaced_by_id: null` clears the link; `status` cannot be null. Needs the `X-CSRF-Token` header like every write. Returns **the whole decision** (same shape as in the list), 200.

Change the status `[capture 19]` (request body `{"status": "rejected"}`):

```json
{
  "id": 1,
  "text": "Keep the free plan for two more months.",
  "status": "rejected",
  "decided_by": "Dan",
  "quote": "I think we should keep the free plan for two more months.",
  "quote_speaker": "Dan",
  "line": 1,
  "revisit_on": null,
  "replaced_by": null,
  "replaces": [],
  "related_tasks": [
    {
      "id": 1,
      "title": "Update the pricing page",
      "status": "todo"
    }
  ],
  "can_edit": true
}
```

A revisit day `[capture 20]` (request body `{"revisit_on": "<today>"}`; the day in the capture is the day it was run) and clearing it `[capture 22]` (request body `{"revisit_on": null}`): the response `revisit_on` is `"2026-11-08"` then `null`.

**Replaced, with the decision that replaced it, in one request** `[capture 23]` (request body `{"status": "replaced", "replaced_by_id": 2}`):

```json
{
  "id": 3,
  "text": "Look at a lifetime deal at some point.",
  "status": "replaced",
  "decided_by": "Amina",
  "quote": "I suggest we also look at a lifetime deal, maybe later.",
  "quote_speaker": "Amina",
  "line": 4,
  "revisit_on": null,
  "replaced_by": {
    "id": 2,
    "text": "Move the launch to 30 November."
  },
  "replaces": [],
  "related_tasks": [],
  "can_edit": true
}
```

Rules the FE can rely on:

* Choosing **Replaced** may come with an optional `replaced_by_id` (the select of "your other decisions, most recent 30"). Choosing it later, or clearing it with `null`, keeps the status Replaced `[capture 26]`, `[capture 27]`, `[capture 28]`.
* Moving a decision **off Replaced** clears its `replaced_by` (and so the other decision's `replaces`) `[capture 25]`: response `replaced_by` is `null`. **Rejected changes nothing else**: related tasks are not touched.
* After the change the other decision shows the link back `[capture 24]`: decision 2 has `"replaces": [{"id": 3, "text": "Look at a lifetime deal at some point."}]` and decision 3 has `"replaced_by": {"id": 2, "text": "Move the launch to 30 November."}`.
* The select of replacement choices has no endpoint of its own: build it from the decisions already loaded (other than the one being edited), newest first, at most 30. Hidden decisions and other people's private ones are refused by the server even if sent.

### Errors (exact strings)

| Case | Status | Body |
|---|---|---|
| A teammate who **can read** the decision but did not record the meeting `[capture 29]` | 403 | `{"error": {"code": "not_owner", "message": "Only the person who recorded the meeting can change this."}}` |
| Someone who **cannot read** it: a private meeting `[capture 30]`, not on the team `[capture 31]`, no such id `[capture 32]`, a hidden decision `[capture 48]` | 404 | `{"error": {"code": "not_found", "message": "No such decision"}}` |
| No CSRF header `[capture 33]` | 403 | `{"error": {"code": "csrf_rejected", "message": "Missing or wrong X-CSRF-Token header. Read it from GET /api/session."}}` |
| `replaced_by_id` while the status is not Replaced `[capture 34]` | 422 | `{"error": {"code": "replaced_by_needs_replaced", "message": "Choose Replaced before naming the decision that replaced this one."}}` |
| `replaced_by_id` is the decision itself `[capture 35]` | 422 | `{"error": {"code": "replaced_by_self", "message": "A decision cannot be replaced by itself."}}` |
| The chosen decision already leads back to this one `[capture 37]` | 422 | `{"error": {"code": "replaced_by_cycle", "message": "That decision is already replaced by this one."}}` |
| `replaced_by_id` is not a decision in the caller's log `[capture 38]` | 422 | `{"error": {"code": "replaced_by_not_found", "message": "That decision is not in your log."}}` |
| Status not one of the four `[capture 39]` | 422 | `{"error": {"code": "validation_error", "message": "Some fields are missing or not valid.", "fields": [{"field": "status", "message": "Input should be 'approved', 'proposed', 'replaced' or 'rejected'"}]}}` |
| `"status": null` `[capture 40]` | 422 | `{"error": {"code": "validation_error", "message": "Status cannot be null."}}` |
| A revisit day that is not a date `[capture 41]` | 422 | `{"error": {"code": "validation_error", "message": "Some fields are missing or not valid.", "fields": [{"field": "revisit_on", "message": "Input should be a valid date or datetime, invalid character in year"}]}}` |
| An unknown field `[capture 42]` | 422 | `{"error": {"code": "validation_error", "message": "Some fields are missing or not valid.", "fields": [{"field": "text", "message": "Extra inputs are not permitted"}]}}` |

The 404 text for a decision that is missing, hidden or unreadable is the same on purpose. A teammate never needs to see the 403: render no select for them (`can_edit: false`). If it happens anyway (their access changed meanwhile), show the page's error line **"Could not update the decision. Try again."** (lead-designed wording) and leave the row as it was.

### Toasts

On a 200 for a status change show **"Status changed to {Approved}"** (the designer's words; the label is the new status). A failed change shows the error line above; the select goes back to its old value.

## 5. Evidence, "Show in transcript", "No transcript is available"

* If `quote` is not null, draw the evidence box: initials circle and name from `quote_speaker` (fall back to `decided_by` when `quote_speaker` is null), the quote in curly quotes, **no time anywhere**. If `line` is not null, add the **Show in transcript** link to `/meetings/{meeting.id}?tab=transcript&line={line}` (the meeting page already highlights that row). With `line` null, show the box without the link.
* If `quote` is null, show **"No transcript is available for this meeting."** in place of the box (the designer's words; it is also what every decision made before this feature shows).

## 6. Replaced by / Replaces links

The designer's "Replaced by: {text}" opens that decision with the tab on All and the search cleared. The API has no "get one decision" call: set the search `q` to the first 200 characters of `replaced_by.text` (or of the `replaces` entry's text), fetch with no `status`, and expand the row whose `id` matches. If it is not in the answer the viewer may no longer read it: do nothing.

## 7. UX consequences the FE must surface

1. **The owner's status and date are final for the AI.** Do not tell the owner that a re-read of the notes may change them: it will not `[capture 43]` (the stand-in model said the opposite for every decision after the owner changed them, and every status stayed as the owner set it).
2. **A related task may disappear from a decision** without anyone editing the decision: when a task is dismissed, deleted, or turned back into a suggestion. A decision's related-task count can differ between two teammates.
3. **A suggested task is never a related task** `[capture 45]`: the pricing task was made a suggested task and is gone from decision 1's `related_tasks` (it comes back when it is accepted).
4. **There is no mail or bell for a revisit day.** Do not offer "remind me".
5. **Hiding is unchanged**: "Remove from log" (inside the expanded row) asks "Remove this decision from the log? It stays in the meeting notes." and calls `POST /api/decisions/{id}/hide` (204) `[capture 46]`; the decision then leaves the groups and the counts `[capture 47]`, and cannot be changed (404).
6. **A person's changes are lost only if the notes change that decision's words**: a decision is identified by its text, so if the notes are rewritten and that bullet's wording changes it is a new decision (Approved, nothing set). Do not cache a decision across a rewrite.

## 8. Verification table

| Behaviour | Verified |
|---|---|
| Shapes of the list, counts, `due_this_week`, tabs, the owner, teammate and outsider views | **verified live** (captures 11 to 18, 21) |
| Every PATCH success and every error string in section 4 | **verified live** (captures 19 to 42, 48) |
| The owner's changes survive a re-read of the notes | **verified live** against a stand-in model reply (capture 43); also unit tests |
| The checks drop a decider, a quote and a related action the transcript does not support | **verified live** against a stand-in model reply (captures 9 and 10); also unit tests |
| Suggested tasks never appear as related tasks | **verified live** (capture 45) and by unit tests |
| `quote_speaker` / `line` of the evidence | **verified live** for found quotes (decisions 1 and 3 of capture 11); the "quote present, row not found" case (`line` null with `quote` set) is a unit test only, not captured |
| `due_this_week` in a **non-UTC zone** and at the week's edges | **unit test only** (zone UTC+14, Monday and Sunday); the capture ran in the default zone |
| What the **real model** returns for `decided_by`, `quote`, `proposed`, `related_actions` | **not verified**: stand-in reply. The first live meeting shows the real choices and how often `proposed` fires |
| The meeting page opening at `?tab=transcript&line=N` | not part of this API; built in the Ask Beyric screens (PR 133) |
