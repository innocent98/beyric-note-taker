# Frontend integration guide: Keeps coming up and Suggested agenda (Series page)

Everything the two new blocks of the **Series page** need from the API. **Every payload, status code and error string below is pasted from a real response** captured by `scripts/capture_series_topics_api.py` against the running app (real HTTP, sessions, CSRF, team visibility, Postgres, migrations, the topics job's own code). Files: `docs/fe-captures/series-topics/`; run the script again to re-derive any of it. Names and addresses are placeholders. Shapes: `docs/openapi.json` (`web/lib/api/schema.d.ts`). This guide is for behaviour.

**Two things were NOT real in the captures, and are marked where they matter:**

* **The model.** There is no OpenAI key or network in the capture, so a stand-in answered the topics call (it returns the topics a good model would, and one that an approved decision should remove, so the code's checks are exercised). The *wording* of a real model's labels and agenda sentences will differ from `"Pricing model"` / `"Decide the pricing model"`; the *shape* of every response will not, because code checks and bounds everything (labels at most 60 characters, agenda sentences at most 100, plain text).
* **The worker.** The topics job ran because the script called `topics.process_next` against the same database, as the worker's loop does, not in a separate worker process. When it runs in production (see "When topics appear") is from the code, not from a capture.

## What it is, in your terms

Two blocks on the Series page, between the three score tiles and "Unfinished from last time":

* **Keeps coming up**: topics that were discussed in two or more meetings of this recurring meeting with no approved decision. One amber row per topic. **The whole section is absent when there are none**: no heading, no spinner, no empty state.
* **Suggested agenda**: numbered lines the next meeting could cover, each with **Add to agenda** / **Added**. Built in code from the topics, overdue tasks, carried-over questions and suggested tasks. **Absent when there are no lines.**

Both arrive inside the existing `GET /api/series/{id}` response, as two new top-level fields. **No new endpoint.** A page load never calls a model: topics are computed by the worker beforehand and read from storage.

## The two new fields

`GET /api/series/{id}` as **ada**, the series' owner (3 meetings of the series are shared with the team; she also has a private one of her own) → **200** *(capture `13-series-ada.json`, other fields left out here)*

```json
{
  "topics": [
    {
      "id": 1,
      "label": "Pricing model",
      "agenda": "Decide the pricing model",
      "reason": "no_decision",
      "line": "Discussed in 3 meetings without a decision",
      "dates_text": "Mon 19 Oct, Mon 26 Oct and Mon 2 Nov 2026",
      "meetings": [
        {"id": 1, "title": "Monday planning", "platform": "google_meet", "created_at": "2026-10-19T10:00:00Z"},
        {"id": 2, "title": "Monday planning", "platform": "google_meet", "created_at": "2026-10-26T10:00:00Z"},
        {"id": 3, "title": "Monday planning", "platform": "google_meet", "created_at": "2026-11-02T10:00:00Z"}
      ]
    },
    {
      "id": 2,
      "label": "Compensation review",
      "agenda": "Give the compensation review an owner",
      "reason": "no_owner",
      "line": "Raised in 2 meetings, still no owner or date",
      "dates_text": "Mon 26 Oct and Mon 2 Nov 2026",
      "meetings": [
        {"id": 2, "title": "Monday planning", "platform": "google_meet", "created_at": "2026-10-26T10:00:00Z"},
        {"id": 3, "title": "Monday planning", "platform": "google_meet", "created_at": "2026-11-02T10:00:00Z"}
      ]
    }
  ],
  "suggested_agenda": [
    {"kind": "topic", "text": "Decide the pricing model"},
    {"kind": "topic", "text": "Give the compensation review an owner"},
    {"kind": "overdue", "text": "Review the overdue task"},
    {"kind": "questions", "text": "Settle 1 open question carried over"},
    {"kind": "suggested", "text": "Confirm 1 suggested task from last time"}
  ]
}
```

(The capture holds the whole page; the JSON above is the two fields, copied unchanged except for putting each short object on one line.)

### `topics[]`: one amber row each

| Field | Use it for |
|---|---|
| `label` | The bold line ("Pricing model"). Plain text, at most 60 characters. Render as text, never as HTML or Markdown. |
| `line` | The sub-line under it, **written by the server**: draw it as is. Three templates by `reason` (D:698-711): `no_decision` "Discussed in N meetings without a decision", `no_owner` "Raised in N meetings, still no owner or date", `not_final` "Discussed in N meetings, approval rules not final". N is the number of meetings **this viewer may read** (see the trap below). |
| `dates_text` | The grey third line ("Mon 19 Oct, Mon 26 Oct and Mon 2 Nov 2026"): the readable meetings' days, oldest first, in the **viewer's** time zone; the year follows the last date. |
| `agenda` | The sentence "Suggested agenda" offers for this topic. Also appears as a `suggested_agenda` row of kind `topic`; you do not need to build that row yourself. |
| `reason` | `"no_decision" \| "no_owner" \| "not_final"`. `line` already encodes it; keep it only if you want to colour by it (the designer draws all rows alike). |
| `meetings[]` | The readable meetings of the topic, oldest first: `id`, `title`, `platform`, `created_at`. The designer draws no links; you may link the dates to the meetings. |

### `suggested_agenda[]`: one numbered row each

`kind` is `"topic" | "overdue" | "questions" | "suggested"`; `text` is the row's words **and** what "Add to agenda" posts. Order is fixed by the server: topic lines first (one per topic, in the topics' order), then at most one each of overdue, questions, suggested. Number the rows 1..N in the order received. A line with nothing behind it is not sent: "Review the overdue task" / "Review the N overdue tasks", "Settle 1 open question carried over" / "Settle N open questions carried over", "Confirm 1 suggested task from last time" / "Confirm N suggested tasks from last time". The plural is chosen by the server.

Before any topic exists (the worker has not looked at the series yet, or it has none) the three count-based lines are still there. `GET` as ada before the job ran → **200** *(capture `08-series-before-the-job.json`)*:

```json
{
  "topics": [],
  "suggested_agenda": [
    {"kind": "overdue", "text": "Review the overdue task"},
    {"kind": "questions", "text": "Settle 1 open question carried over"},
    {"kind": "suggested", "text": "Confirm 1 suggested task from last time"}
  ]
}
```

## Add to agenda / Added (existing routes)

"Add to agenda" posts the row's `text` to the route that already exists. `POST /api/series/1/agenda` as ada, body `{"text": "Decide the pricing model"}` → **201** *(capture `16-agenda-add.json`)*:

```json
{"id": 1, "text": "Decide the pricing model", "can_remove": true}
```

The page then lists it under `agenda` *(capture `17-series-after-add.json`)*:

```json
[{"id": 1, "text": "Decide the pricing model", "can_remove": true}]
```

* **"Added" is a comparison the FE makes:** a suggested row shows **Added** (teal) when a live `agenda[]` item has **exactly the same `text`**. `suggested_agenda` does not change when a line is added; it never hides an added line.
* **A second click on "Added" removes it:** `DELETE /api/series/1/agenda/{id}` with the matching item's `id` → **204**, no body *(capture `18-agenda-remove.json`)*. The row goes back to "Add to agenda" (`19-series-after-remove.json`).
* **Added items land in "Other agenda items"**, the existing list (`agenda[]`), with its existing rules: `can_remove` is false for a member who did not add it (only the author or an owner or admin removes), so for such a viewer a second click on "Added" must not offer removal (show the "Added" state without the toggle, or let the 403 `team_forbidden` toast); at most 30 waiting lines (409 `agenda_full`); a line is plain text with no web address. Suggested texts never contain one.
* The two routes' errors are in the existing guide `docs/fe-integration-guide-series.md`; they are unchanged.

## What a viewer may see: the traps

**What topics are made from.** The worker feeds the model **only meetings shared with the series' team**. A private meeting, even the owner's own, is never read, so no wording of it can appear in a label, a sub-line or an agenda sentence. Sharing can change after a topic was written, so the server also checks on every read: a topic is sent only to a viewer who **can still read every meeting behind it**: the ones it cites (`meetings`) and every other meeting the model was shown. Consequences:

1. **A meeting being unshared (or deleted) can make topics vanish.** Captured: Ada makes the 2 Nov meeting private (`DELETE /api/meetings/3/share` → **200**, capture `20-unshare-meeting.json`). `GET` as **dan** → **200** *(capture `21-series-dan-after-unshare.json`)*: `"topics": []` and `"suggested_agenda": []`. Both topics came from a set of meetings that included it, so both are gone for him (and the count-based lines for that meeting's tasks and question too). Ada, who still reads it, is unchanged (capture `22-series-ada-after-unshare.json`). The worker recomputes from the meetings still shared shortly after (unsharing marks the series), and the topics return. Do not explain the absence, do not cache a topics list across users or across reloads.
2. **`line`, `dates_text` and `meetings` are the topic's cited meetings**, all readable by the viewer (otherwise the topic is not sent at all). Draw `line` as received; never recompute N.
3. **A topic is never partly shown.** There is no "hidden topic" notice, no count of hidden ones, no empty-agenda variant.
4. **Different viewers can still see different `suggested_agenda` lines.** The overdue, question and suggested-task lines are counted over what the viewer may see (see below), so Ada's and Dan's differ in capture `13` vs `14` even though their topics are the same.

Another team's series is **404** *(capture `15-series-eve.json`)*: `{"error": {"code": "not_found", "message": "No such recurring meeting"}}` is the existing error shape, unchanged. No login: **401** `{"error": {"code": "not_authenticated", "message": "Log in first."}}` *(capture `07-series-logged-out.json`)*.

**Counts in "Suggested agenda" are the viewer's.** The overdue, question and suggested-task counts cover only tasks and questions the viewer may see, the same filter as "Unfinished from last time" and the carried questions on the same page. A suggested task (one still waiting to be accepted) is in **no other list or count on the page**; it appears here only as "Confirm N suggested tasks from last time" (**N counts only the series' last meeting the viewer may read**, so the line can disappear when a newer meeting is recorded), and it is never counted as overdue.

## When topics appear (what the worker does, for the FE's expectations)

* Topics are computed by the background worker, **one model call per series**, after the notes of one of the series' meetings have been read (decisions are final then). Not on a page load, not on demand. A new topic therefore shows up **some minutes after a meeting's notes are ready**, not instantly: the FE does not poll for it and shows nothing special while waiting.
* The call reads the series' last five ready meetings (their "Key points" and decisions) that are **shared with the series' team**. A private meeting is never read, whoever owns it. (This is stricter than the spec's "owner's view", on purpose: see the SOP.)
* The stored set is replaced each time. If the model call fails or its reply is unusable, the old topics stay exactly as they were (capture `24-series-after-a-failed-run.json`: after a new meeting was added and the model answered badly on purpose, `topics` are identical to capture `13`; `past_meetings` and `finished_since_text` differ because of the new meeting, and the "Confirm 1 suggested task" line is gone because that count follows the series' last meeting, which is now the new one) and the worker tries again a few times; nothing is shown to anyone.
* A topic that an approved decision of the series already covers (more than half of its words are in the decision) is dropped by the code. In the capture the stand-in returned a third topic, "Hiring plan", which was dropped because Monday's approved decision reads "The hiring plan for next quarter is approved". See `11-stored-after-the-job.json` (two stored rows) and `10-prompt-sent-to-model.json` (what the model was shown).
* **NOT LIVE:** how good a real model's topics are (does it find real repeats? does it propose too many?) is unchecked until the first live series. The shapes are checked; the quality is not.

## UX consequences the FE must surface

* **Never show a spinner or placeholder for either block.** Absent means absent.
* **Do not turn `line` into a claim about the future.** It says what happened ("Discussed in 3 meetings without a decision"), nothing about what should happen.
* **A hidden topic is not an error and not a zero.** Do not render "0 topics" or a count of hidden ones.
* **"Add to agenda" changes the shared agenda**, which the whole team sees and which is mailed by "Send agenda to attendees". Adding is immediate (no confirmation), as in the design; the toast wording is the FE's.
* **The Suggested agenda lines are suggestions, not state.** They change as tasks are done, questions are resolved or the worker recomputes; an "Added" row stays on the agenda even if its suggestion later disappears.

## Verification table

| Behaviour | Verified |
|---|---|
| `topics[]` and `suggested_agenda[]` field names, nesting, values, as captured above | **Live** (real HTTP, `08`, `13`, `14`) |
| `line` templates for `no_decision` and `no_owner` with real counts | **Live** (`13`) |
| `line` template for `not_final` | **Not live**: no captured topic had that reason; unit test `test_the_three_sub_lines` and `app/series.py` `_SUBLINES` |
| `dates_text` across a year boundary ("Mon 29 Dec 2025 and Mon 5 Jan 2026") | **Not live**: unit test `test_the_date_line_spans_years_and_sorts_oldest_first` |
| The model's input holds shared meetings only (a private meeting of the owner and of a teammate are absent) | **Live** (`10-prompt-sent-to-model.json`: three meetings, no PAYBAND or MULBERRY text); tests `test_the_job_reads_only_meetings_shared_with_the_team`, `test_a_series_whose_only_meetings_are_private_is_not_asked` |
| Unsharing a meeting the model was shown hides every topic that came from it, and the lines of its tasks and question | **Live** (`20`, `21`; Ada unchanged in `22`) |
| A topic is hidden unless the viewer reads every cited and every shown meeting | **Not live** beyond the unshare capture: `test_a_topic_is_shown_only_if_the_viewer_reads_every_meeting_behind_it`, `test_a_topic_the_model_saw_a_meeting_for_is_hidden_when_that_meeting_is_unshared` |
| Sharing, unsharing and decision changes mark the series for a new look | **Not live**: `test_sharing_unsharing_and_decisions_mark_the_series_stale` |
| Another team gets 404; no login gets 401 | **Live** (`15`, `07`) |
| Overdue, questions and suggested lines; plurals; "Confirm" counts the last meeting the viewer may read only | **Live** for the singulars (`08`, `13`, `14`, `24`); plurals by `test_plurals_and_missing_lines`; last-meeting rule by `test_confirm_suggested_tasks_counts_the_last_meeting_only` |
| A suggested task is in no other list on the page | **Live** (`unfinished` in `08` and `13` holds only "Send the pricing sheet"); not counted as overdue even when its due date has passed: `test_the_suggested_agenda_lines_are_built_in_code_in_order` |
| Add to agenda: 201 with `{id, text, can_remove}`; page lists it; DELETE answers 204 | **Live** (`16` to `19`) |
| The two blocks absent when empty | **Live** (`topics: []` in `08`; `[]` and `[]` in `21`); the absence on screen is FE behaviour |
| Approved decision removes a topic | **Live** (`11`: two rows from the stand-in's three) |
| A failed model call leaves the stored topics; failure is in the ledger | **Live** (`24`, `25`) |
| After three failed calls the series tries again after a day; a reply that arrives after a newer change is not written | **Not live**: `test_a_series_that_failed_three_times_tries_again_after_a_day`, `test_a_reply_that_arrives_after_the_series_was_marked_stale_is_not_written` |
| A page load never calls the model | **Not live**: `test_the_page_reads_stored_topics_and_never_calls_the_model` |
| Real model quality; the worker as a separate process; minutes between notes and topics | **Not live** (stand-in model; job called in-process) |
