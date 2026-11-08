# FE integration guide: Send now and Not recorded (plan task C15b)

Every body below is copied from `docs/fe-captures/send-now/` (made by `scripts/capture_send_now.py`:
real migrations, the real app over real HTTP, the real calendar sync and the real join runner).
Where something could **not** be exercised live it is marked **NOT LIVE** with the reason. Ids,
times and the capture account's address are placeholders from the capture run. Passwords in the
login captures are written `<password placeholder>`.

## What this is, in the person's terms

The note taker joins calendar meetings by itself. When it does not get in (the note taker was busy
with other meetings, the person's recording hours ran out, the 15 minutes after the start passed,
and so on) the meeting is **not recorded**, and until now the person had no way to ask again. Two
things fix that:

1. **Not recorded** — a list on the Meetings page of the last seven days' meetings the note taker
   did not get into, each with the reason in words and a **Send now** button while the meeting is
   still on.
2. **Send now** — one button. It sends the note taker to the meeting right now, through the same
   checks as pasting a link. If the checks say no, the person is told why and nothing changes.

The **"Send it again" button in the "Could not join" mail** is a plain link to the Meetings page
(`/`, the app's home; there is no `/meetings` page) in the signed-in app. It sends nothing by itself (no token in the link, opening it
does nothing): the person lands on the Meetings page and presses the button there. See "The mail".

## Endpoints

Both need the signed-in session. `POST` needs the `X-CSRF-Token` header (read it from
`GET /api/session`).

### `GET /api/not-recorded`

The caller's own meetings from the last seven days that the note taker did not get into, newest
first, at most 100. No parameters. Capture `11-list`:

```json
{
  "items": [
    {
      "join_id": 3,
      "title": "Design review",
      "starts_at": "2026-11-08T21:20:00Z",
      "ends_at": "2026-11-08T22:20:00Z",
      "platform": "google_meet",
      "platform_label": "Google Meet",
      "state": "no_capacity",
      "reason_text": "The note taker was busy with other meetings.",
      "can_send_now": true
    },
    {
      "join_id": 1,
      "title": "Weekly planning",
      "starts_at": "2026-11-08T21:08:00Z",
      "ends_at": "2026-11-08T22:08:00Z",
      "platform": "google_meet",
      "platform_label": "Google Meet",
      "state": "failed",
      "reason_text": "The note taker could not join before the 15 minutes were up.",
      "can_send_now": true
    }
  ]
}
```

Nothing recorded yet is `{"items": []}` (`06-list-empty`). Another person's rows never appear
(`28-other-user-list`).

| Field | Meaning |
|---|---|
| `join_id` | The id to send to `send-now`. It is **not** an event id and **not** a meeting id. |
| `title` | The event's title, cleaned. Without one: platform and code, e.g. `Google Meet eee-eeee-eee` (`24-list-with-ended`). |
| `state` | `failed`, `no_capacity` or `skipped`. For choosing the look only; **show `reason_text`, never the state or any code.** |
| `reason_text` | The reason in words. The server owns the words. |
| `can_send_now` | True while the meeting is still on and the account may send. Draw the Send now button only when true. |

What is on the list: `failed` and `no_capacity` joins, and `skipped` ones whose reason means the
meeting was not recorded (hours used up, too many note takers at once, the link still in use by an
earlier meeting, a note taker from another account already there, the calendar disconnected, a
suspended account). A join the note taker **did** get into and that then failed (not let in, the
recording failed) is **not** here: it is an ordinary failed meeting on the Meetings list with its
own Send it again. A meeting where the person's own note taker is already in the same meeting
(a pasted link) is not listed either: it was recorded.

### `POST /api/scheduled-joins/{join_id}/send-now`

No body. `join_id` comes from `not-recorded` or from an Upcoming row (see the next section).

**Success**, status 200 (`19-send-now`):

```json
{
  "join_id": 1,
  "meeting_id": 2,
  "status_text": "The note taker is on its way to this meeting."
}
```

`meeting_id` is the new meeting. It appears on the Meetings list as a calendar meeting with the
event's title, status `bot_sent` ("Bot sent", `23-meeting-after-send`), and the page follows it
from there like any other meeting. The row has left the Not recorded list (`22-list-after-send`).
After a success, take the row off the list and send the person to the meeting (or refresh both
lists); show `status_text` as the toast.

**Pressed twice** gives the same 200 with the same `meeting_id` and sends one bot
(`20-send-now-again`; also proved by a concurrency test). The button can be pressed again safely,
but disable it while the call is out.

**Every refusal changes nothing**: the row stays on the list, `can_send_now` stays as it was
(`14-list-after-refusal`). There is no hidden retry: a 409 means "not sent". Show the `message`
as the toast or the row's note and leave the button there so the person can try again.

| Status | `error.code` | `error.message` (verbatim) | Capture | When |
|---|---|---|---|---|
| 409 | `bot_busy` | The note taker is busy right now. Try again in a few minutes. | `13`, `21` | The one note taker is in another meeting. |
| 403 | `hours_cap_reached` | You have used your 5 recorded hours for the pilot, so the note taker will not join new meetings. Write to us at hello@beyrictech.com if you need more. | `17` | The recorded hours are used up. The number of hours is the person's own cap. |
| 409 | `cannot_send_now` | The note taker is already getting ready for this meeting. | `15` | The join is still `scheduled` (the scheduler will join it). Other messages for this code, from `app/send_now.py`: "This meeting is switched off, so the note taker will not join it." (switched off) and "The note taker already joined this meeting." (done). **NOT LIVE** for these two: only the first text was captured. |
| 409 | `meeting_ended` | This meeting is already over, so the note taker cannot join it. | `25` | `now` is after the meeting's end. |
| 502 | `bot_send_failed` | The bot could not be sent. Please try again in a moment. | `18` | The bot service did not take the bot. Nothing was recorded. |
| 404 | `not_found` | No such meeting | `16`, `29` | A join that does not exist or is not the caller's (the two look the same). |
| 401 | `not_authenticated` | Log in first. | `02` | No session. |
| 403 | `csrf_rejected` | Missing or wrong X-CSRF-Token header. Read it from GET /api/session. | `05` | Header left out. |

More refusal codes exist and share the pasted-link wording; they were exercised by tests, **not
captured live**: 409 `already_in_meeting` ("A note taker is already in that meeting for you. Wait
for it to finish first."), 409 `meeting_has_note_taker` ("A note taker is already in this
meeting."), 409 `user_limit` ("You have reached the most bots allowed at once (N). Wait for one to
finish first."), 403 `account_suspended` ("This account is suspended."), 409 `template_required`
("Choose a notes template for calendar meetings in Settings, then try again."). Show any `message`
as it comes; do not branch on codes other than for the toast tone.

## Upcoming rows carry the same answer

`GET /api/calendar` (and the row answered by `PUT /api/calendar/events/{id}/override`) gained three
fields on every `upcoming` item. Capture `12-calendar-upcoming`, the row the note taker did not get
into, then a meeting it joined, then one still to come:

```json
{
  "id": 1,
  "title": "Weekly planning",
  "will_join": true,
  "will_join_text": "Will join",
  "reason_text": null,
  "join_id": 1,
  "can_send_now": true,
  "not_recorded_text": "The note taker could not join before the 15 minutes were up."
}
```

```json
{ "id": 2, "title": "Customer call", "join_id": 2, "can_send_now": false, "not_recorded_text": null }
```

(Other fields of the item are as before and are left out here.)

- `join_id` is the scheduled join behind the event, `null` when it has none (the event is not
  joined). It is what `send-now` takes. **It is not the item's `id`**, which is the event id the
  override route takes.
- `can_send_now` true: draw Send now on the row. `not_recorded_text`: the sentence to show under the
  title (the same sentence the Not recorded list gives); it can be set while `can_send_now` is false
  (the meeting is over).
- **Trap:** `will_join` and `will_join_text` are about the **rule** ("Will join"), not about what
  happened. A row can say "Will join" and carry `not_recorded_text`: the note taker was supposed to
  join and did not. Show the not-recorded line and the button; leave the switch as it is.
- **Trap:** a calendar connected while a meeting is already past its first 15 minutes has **no
  join row at all** (the server leaves it "in progress" and does not join it by itself). Such a row
  has `join_id: null` and no Send now. Sending the note taker into a meeting that is already under
  way for those is a follow-up, not built.

## The mail

The "Could not join" mail (a bot that was sent and not admitted) now has the designer's "Send it
again" button. Capture `30-could-not-join-mail` (the real template, a placeholder meeting and time):

```text
Send it again:
https://notes.example.test/
```

The link is the Meetings page in the app: no token, no query, nothing happens by opening it. A
person who is signed out sees the landing page (its Log in link) instead of the Meetings list; that was
not captured. The Meetings page then has that meeting's own Send it
again (the failed-meeting page, unchanged) and, for a meeting the scheduler never got into, the Not
recorded list above (its screens are a separate PR from this API).

## UX consequences to surface

- **Never show a code or the state name.** `reason_text` and `message` are the words.
- **A refusal is "not sent"**: do not remove the row or show success. Keep the button.
- **Busy is common.** There is one note taker and no queue. `bot_busy` ("busy right now") is
  the usual refusal during working hours; the person can press again a little later.
- **Only offer Send now while `can_send_now` is true.** A meeting that is over keeps its row (so the
  person sees it was not recorded) with the reason and no button.
- **Send now can place a bot into a meeting that started long ago.** It is the person's choice; the
  notes will cover from when the bot joined. Say nothing more: the server has already checked the
  meeting is still on.
- **A pressed button restarts a 15-minute window on the server** (the row's `deadline_at`); the
  client does not see it and needs no timer.

## Polling

None needed for Send now. After a success the new meeting follows the Meetings list's own refresh
(`refresh_after_seconds` on the list and on the meeting).

## Verification table

| Behaviour | Status |
|---|---|
| `GET /api/not-recorded` shape, ordering, other user sees nothing, 401 | verified live (`01`, `06`, `11`, `24`, `28`) |
| A row ended `failed` by the real runner's deadline; a row `no_capacity` made by the real runner with the host full | verified live; the runner's clock was moved four minutes ahead for that run (the runner takes its clock as an argument) |
| Send now refused `bot_busy`, nothing changes | verified live (`13`, `14`, `21`) |
| Send now refused `hours_cap_reached` | verified live (`17`; the capture account is an admin, made ordinary for that call because admins have no cap) |
| Send now `bot_send_failed` (host refuses) | verified live against the fake host (`18`) |
| Send now success, new meeting, row leaves the list | verified live against the fake host (`19`, `22`, `23`) |
| Pressed twice: same answer, one bot; two simultaneous presses: one bot | repeat verified live (`20`); the simultaneous case is a test (`tests/test_send_now.py`), not a capture |
| `meeting_ended` | verified live on a row **inserted directly** (`24`, `25`): the runner only ends a row this way at its deadline, 15 minutes after a start |
| `cannot_send_now` for a `scheduled` join | verified live (`15`); the "switched off" and "done" texts are tests only |
| Another person's join: 404 | verified live (`29`) |
| `already_in_meeting`, `meeting_has_note_taker`, `user_limit`, `account_suspended`, `template_required` | tests only (`tests/test_send_now.py`), not captured |
| Upcoming rows carry `join_id`, `can_send_now`, `not_recorded_text` | verified live (`12`) |
| The mail button and its link | rendered by the real template (`30`); **NOT LIVE**: no real mail was sent or opened in a mail client |
| A real bot placed in a real meeting | **NOT LIVE**: `FakeVexaHost` took the bot; no real Vexa or Google Meet was involved |
