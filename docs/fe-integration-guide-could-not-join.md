# FE integration guide: "Could not join" (bot not admitted)

Written 2026-11-07. Every body below is copied from `docs/fe-captures/could-not-join/`
(`scripts/capture_could_not_join.py`: the real pipeline, the real app under uvicorn, real migrations, a
scratch database; only Vexa is a stub that answers with fixed states). Addresses, the e-mail account and
the host name are placeholders. Shipped in `docs/sop/2026-11-07-could-not-join-wiring.md`.

## 1. What it is and when it happens
A meeting's bot was **sent** and then **was not let in**: the host left it in the waiting room until
Vexa gave up, the host declined it, or Google refused the bot's ask to join. The meeting ends `failed`
(as before), and now the owner also gets **one bell entry** and, when their mail setting allows it, **one
email**. No new route, no new field: the entry arrives through the existing bell.

It is **not** sent for: the host being full, the owner's own bot limit, a note taker already on the code,
a send that failed, an event that ended early, the scheduler being down, Zoom blocking the bot, a bot
that failed, a recording that failed, a bot waiting on a prompt. Those keep their words (a calendar join
that ends this way still gets `calendar.not_recorded`).

## 2. The bell entry: `GET /api/notifications`
Real response (`03-notifications.json`; two meetings: a waiting-room timeout and a host decline):

```json
{
  "notifications": [
    {
      "id": 2,
      "kind": "meeting.not_admitted",
      "severity": "error",
      "title": "The note taker could not join your meeting",
      "body": "We tried to join Google Meet ddd-eeee-fff but were not admitted. This usually happens when the host does not let the note taker in from the waiting room.",
      "meeting_id": 2,
      "created_at": "2026-11-07T22:38:36.048265Z",
      "read_at": null
    }
  ],
  "has_more": false,
  "next_before_id": null
}
```
(second entry, `id` 1, trimmed; same shape, `meeting_id` 1.)

- **Show `title` and `body` as they come.** The words are the designer's mail sentences
  (`Emails.dc.html:137-149`) minus "at {time}": the row already shows its own time.
- `meeting_id` is **always set** for this kind (present, not null): link to the meeting. The bell page
  already does this for any entry with a `meeting_id` ("Open the meeting").
- **At most one entry per meeting, ever.** A retry that is refused again adds nothing. Do not
  build a counter or expect a second entry.
- The meeting name in `body` is the platform and code ("Google Meet ddd-eeee-fff"), never a calendar
  title or a link.
- `severity` is `error`. The current bell page marks unread entries with a "New" badge in the brand
  tone for every severity except `warning`; nothing here needs a new tone.

## 3. The meeting itself (unchanged)
`GET /api/meetings` (`04-meetings-list.json`, a failed meeting, trimmed):

```json
{
  "id": 3, "title": "Google Meet ggg-hhhh-iii", "platform": "google_meet",
  "status": "failed", "step": "waiting", "status_text": "Failed", "status_tone": "danger"
}
```
The meeting's own failure sentence for these causes is "The bot was not let into the meeting. The host
may have declined it, or the waiting room timed out." (waiting room, host declined) or "Google would not
let the bot ask to join this meeting. This sometimes happens; try sending the bot again in a few
minutes. If it keeps happening, the host's Meet settings may be blocking guests." (Google refused).
Both are existing strings; the capture shows the first (`failure_reason` printed by the script).

## 4. The email (not an API; for the designer's checklist and support)
Real rendered text (`mails.json`, template `meeting_not_joined`, subject `Meeting not recorded`, time in
the owner's stored zone, here Africa/Lagos):

```
The note taker could not join your meeting

We tried to join Google Meet btp-tkio-gtb at 12:58 GMT+1 but were not admitted. This usually happens when the host does not let the note taker in from the waiting room.

If the meeting is still running, you can send it again.

You received this because you sent the note taker to this meeting.
Email preferences: https://notes.example.test/settings#email-preferences
```
The mail follows the owner's "email me" switch (Settings, Email preferences); with it off the owner
still gets the bell entry.

## UX instructions
- **"you can send it again" has no button yet.** The designer's "Send it again" button waits for the
  send-now feature (C15b). Until then the meeting page's own retry (shown or blocked by the meeting's
  `retry_block`) is the way; do not add a button to the bell for it.
- Do not imply the user did something wrong: the host's waiting room or Google refused the bot.
- The bell entry can arrive for a meeting the user sent by hand **and** for one started by the calendar
  auto-join; do not branch on which.

## Verification table
| Claim | Status |
|---|---|
| Bell entry shape, kind, words and `meeting_id` for a waiting-room timeout and a host decline | verified live (`03`): real pipeline, real route; Vexa stubbed |
| No entry for a crashed bot, and none for a repeated failure of the same meeting | verified live (`03` holds only the two entries; the script also re-ran the first meeting's failure) |
| Google refusing the ask to join (`google_meet_join_refused`) gives the same entry | covered by tests only (`tests/test_pipeline_not_joined.py`); not captured |
| Mail text and subject | verified live in the sense that the real template rendered the real queued row (`mails.json`); **no mail provider send**, no email client rendering |
| The mail goes only with the owner's email switch on | covered by tests only (`test_mail_switched_off_gives_the_bell_but_no_mail`) |
| A real Vexa waiting-room timeout | **not exercised**: Vexa is a stub; the `completion_reason` values come from Vexa's source and earlier live runs documented in `docs/sop/2026-11-06-*` |
