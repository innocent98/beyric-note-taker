# FE integration guide: meetings the bot joins from the calendar (task C12)

Written 2026-11-04 for the front end. Every body below is copied from `docs/fe-captures/calendar-titles/`
(`scripts/capture_calendar_titles.py`: real app under uvicorn, real migrations, a scratch database,
the real join runner and placement; only the Vexa host and the Google event are synthetic). Long
objects are trimmed to the fields that matter and say so. Nothing here is written from the schema.

## 1. What changed for the front end

**No endpoint, field or status code was added or changed** (`docs/openapi.json` is byte-identical;
`npm run gen:api` has nothing to do). What changed is the **value** of one existing field:
`title` on `GET /api/meetings`, `GET /api/meetings/{id}`, the Decisions and Review groups
(`meeting.title`) and the file name of an export. A meeting the bot joined from a calendar event now
carries **the event's title**; a meeting made from a pasted link still reads `Google Meet <code>` or
`Zoom <code>`.

A meeting joined from the calendar has `source: "calendar"` in the database; the API does not
expose `source`. Do not try to tell the two apart by the title.

## 2. The title is attacker-controlled text: render it as plain text

Anyone can invite the user to an event with any title. The server cleans it **once, when it is
stored, and again when it is sent** (`app/titles.py`):

| What the server does | What it does not do |
|---|---|
| removes control characters, bidirectional override and isolate characters, line and paragraph separators; turns newlines and tabs into one space; collapses runs of spaces; trims; cuts at 200 characters with `…` | it does **not** strip or escape markup and it does **not** remove web addresses from the API answer |
| keeps zero-width joiners and variation selectors (emoji sequences, Persian) | |

So the API answer can legitimately contain `<script>` and `https://...` as characters. **Render
`title` as text, never as HTML, never as Markdown, never auto-linked.** React's default text rendering
is correct; `dangerouslySetInnerHTML`, a Markdown component or a "linkify" helper on this field is a
vulnerability. Real capture (`03-meetings-list.json`, trimmed to these fields; the second title is
the hostile test title, cleaned by the server: the right-to-left override and the line break are gone,
the markup and the address are still there as text):

```json
{"meetings": [
  {"id": 2, "title": "Google Meet ddd-eeee-fff", "platform": "google_meet", "native_id": "ddd-eeee-fff", "status": "ready"},
  {"id": 1, "title": "Q3 gnirts review Bcc: attacker@example.test <script>alert(1)</script> https://evil.example.test/login", "platform": "google_meet", "native_id": "aaa-bbbb-ccc", "status": "ready"}
]}
```

`GET /api/meetings/1` (`06-meeting-1.json`) returns the same `title`, `platform` and `native_id`.

Meeting 2 is an event with an empty title: the field falls back to `Google Meet ddd-eeee-fff`.
**Never assume a title is short, unique or non-empty-looking**: use CSS truncation (`text-overflow`)
for long ones, and show the platform and code elsewhere if the design needs them (`platform`,
`native_id` are still sent).

## 3. Export file names

The download's `Content-Disposition` is built from the cleaned title with path, quote and header
characters removed (`07-export-docx-1.json`, verbatim):

```
attachment; filename="Q3 gnirts review Bcc attackerexample.test scriptalert(1)script httpsevil.example.testlogin.docx"; filename*=UTF-8''Q3%20gnirts%20review%20Bcc%20attacker%40example.test%20scriptalert%281%29script%20httpsevil.example.testlogin.docx
```

Use the header as it comes; do not rebuild a name from `title`. The Word and PDF documents carry the
cleaned title as their heading (HTML-escaped in the PDF).

## 4. What the user can see about a join (not an API yet)

The join states (`scheduled`, `joined`, `no_capacity`, `skipped`, `failed`, `cancelled`,
`completed`) and their reasons are **not exposed by any route yet**: `GET /api/calendar` and the
override routes (C15a) own that, with plain-words text from the server (`app/calendar_text.py`).
What exists today is the **notification** the runner writes (bell, and an email for the two that
matter). These are the `kind`s and the exact English; show `title` and `body` as they come:

| `kind` | When | Email | Words (the event name is the cleaned, short, link-defanged one) |
|---|---|---|---|
| `calendar.no_capacity` | the host was full when the bot was due; repeats at most once per 30 minutes while it lasts | yes | title `The note taker is busy`; body `The note taker is in another meeting and could not join "<name>" yet. It will keep trying until HH:MM UTC. If it cannot get in by then, this meeting will not be recorded.` |
| `calendar.not_recorded` | the 15 minutes after the start passed with no bot in | yes | title `Meeting not recorded`; body `The note taker could not join "<name>" before the 15 minutes were up, so this meeting was not recorded.` |
| `calendar.join_failed` | the bot host refused the send; retried every 15 s until the deadline | no | title `The note taker could not start`; body `The note taker could not start for "<name>". It will try again until HH:MM UTC.` |
| `calendar.hours_cap` | the user's recording hours are used up | no | title `Recording hours used up` |
| `calendar.user_limit` | the user already has the most bots allowed | no | title `Too many note takers at once` |
| `calendar.meeting_has_note_taker` | another account's bot is already in that meeting (one bot per meeting code; sharing waits for W3/W6) | no | title `A note taker is already there`; body names no one |

**UX instructions.**
- A calendar meeting can end as **"Not recorded"** without the user doing anything wrong (the host
  was full for 15 minutes). Never show a missing recording as an empty or "processing" state: when the
  routes arrive, "not recorded" is its own state with the words above.
- `calendar.meeting_has_note_taker` must be shown as the notice it is: it says a note taker is
  already in the meeting and **does not say whose**. Do not add a name or a "share" button (sharing is
  not built).
- Notification kinds that start `calendar.` are deleted when the user disconnects Google; do not
  cache them client-side as if they were permanent.

## 5. Verification table

| Behaviour | Status |
|---|---|
| `title` in the list and detail is the cleaned event title; empty title falls back to platform and code | verified live (`03`, `04`, `06`): the meeting was made by the real runner, the answers are real |
| Markup and an address stay in the API title as text | verified live (`03`, `06`) |
| Control and bidi characters never reach the API or the file name | verified live (`03`, `07`) and by `tests/test_titles.py` |
| Export file name from the title | verified live (`07`) |
| Notification kinds and words in section 4 | **covered by tests only** (`tests/test_join_runner.py`); no route returns notifications for this guide yet, so no HTTP capture exists |
| The notes-ready mail subject and body with a calendar title (bare domains written `evil[.]example[.]test`) | covered by tests only (`tests/test_titles.py`, `tests/test_mail_upgrade.py`); no mail was captured from a real provider |
| A real Google event title, a real Vexa host | **not verified**: the event and the host are synthetic |
