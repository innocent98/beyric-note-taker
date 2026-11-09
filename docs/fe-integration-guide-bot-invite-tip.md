# Frontend integration guide: the "add the note taker to your event" tip

Everything below is copied from responses captured against the running app by
`scripts/capture_bot_invite_tip.py` (real migrations, the real app under uvicorn, real HTTP, a
scratch Postgres; files in `docs/fe-captures/bot-invite-tip/NN-name.json`, cited as **#NN**). Run the
script again to re-derive any claim. The address in the captures, `note@example.test`, is a
placeholder: production sends whatever the operator put in `BOT_INVITE_EMAIL`. Emails, ids and
dates in the captures are placeholders or the day the script ran (2026-11-09).

**What was NOT captured live:** a real Skribby bot, a real Google Meet, a real calendar. Google is
the stand-in used by every calendar capture (`tests/fakes/google.py`); no bot was sent. The meetings
in #06 to #09 and #11 are rows set directly in the states the worker leaves them in
(`bot_sent` waiting to be let in; `failed` with the not-let-in reason), and the app answered for them
over HTTP. That a real Skribby bot skips the wait when its address is on the guest list is the
vendor's claim and the bake-off's measurement (`docs/spike/meeting-bot-vendors.md`: 20 to 27 s when
invited, 39 to 46 s with a knock when not), **not** something these captures show.

## 1. What this is, in the frontend's terms

On Google Meet a signed-in note taker whose Google address is on the event's guest list gets in
without waiting for the host. So the app tells people: **"Add note@... to your event to skip the
wait"**, with a Copy button. The tip is small, honest about its limit (it only skips the wait on
Google Meet; Zoom and Teams still ask the host to admit the note taker) and never promises that it
works every time.

**The server owns whether it applies and what the address is.** The web code has no address in it. Two
reads carry one new field, `bot_invite_email`: a string (the address) when the server may say it, and
**`null` otherwise**. When it is `null` draw nothing. It is a public address, not a secret.

| Where | Read | Draw the tip when |
|---|---|---|
| Settings, Calendar section, as a short line under the "Join automatically" rules | `GET /api/calendar` | `connected` is true, `provider` is `"google"` and `bot_invite_email` is a string |
| A meeting's page | `GET /api/meetings/{id}` | `bot_invite_email` is a string (the server already checked the rest, below) |

Nothing else changes. No new endpoint, no write, no CSRF, no new error.

**When the server sends an address** (all three must hold, else `null` everywhere):
`BOT_PROVIDER=skribby`, `SKRIBBY_ACCOUNT_ID` is set (a signed-in bot: a guest bot has no address to
invite) and `BOT_INVITE_EMAIL` is set. On a meeting the server adds: the meeting is Google Meet, the
bot that ran it was Skribby's, and it is **either** waiting to be let in right now (the live state
`awaiting_admission`, seen within about the last two minutes) **or** failed because it was not let in. Do not
re-derive any of this from `status_text` or `failure_reason`: read the field.

## 2. `GET /api/calendar`

Tip on (#05): Skribby, signed in, address set; a Google calendar connected.

```http
GET /api/calendar
Cookie: session=<cookie>
```
```http
HTTP 200
content-type: application/json
```
```json
{
  "connected": true,
  "state": "connected",
  "state_text": "Connected",
  "account_email": "grace@example.test",
  "provider": "google",
  "last_sync_at": null,
  "last_error": null,
  "rule": "all",
  "rule_text": "Joining all meetings",
  "calendar_template_id": 1,
  "auto_join_paused": false,
  "auto_join_paused_text": null,
  "microsoft_enabled": false,
  "bot_invite_email": "note@example.test",
  "upcoming": []
}
```
_Capture: `docs/fe-captures/bot-invite-tip/05-calendar-tip-on.json`_

Tip off (#10): the same call with `BOT_INVITE_EMAIL` empty. **The field is present and `null`**, not
absent. #12 (no `SKRIBBY_ACCOUNT_ID`) and #13 (`BOT_PROVIDER=vexa`) are byte-for-byte the same shape
with `null`.

```json
{
  "connected": true,
  "state": "connected",
  "state_text": "Connected",
  "account_email": "grace@example.test",
  "provider": "google",
  "last_sync_at": null,
  "last_error": null,
  "rule": "all",
  "rule_text": "Joining all meetings",
  "calendar_template_id": 1,
  "auto_join_paused": false,
  "auto_join_paused_text": null,
  "microsoft_enabled": false,
  "bot_invite_email": null,
  "upcoming": []
}
```
_Capture: `docs/fe-captures/bot-invite-tip/10-calendar-tip-off-address-unset.json`_

`PUT /api/calendar/settings` answers with the same whole state, so it carries the field too (covered
by a test, `tests/test_bot_invite_tip.py`; not captured over HTTP here).

## 3. `GET /api/meetings/{id}` (and the writes that answer with the same object)

Waiting to be let in (#06): a Google Meet meeting, bot waiting in the lobby. Trimmed (the unchanged
tail of the object is as in `docs/fe-integration-guide-owner-app.md` 5.2).

```http
GET /api/meetings/1
Cookie: session=<cookie>
```
```http
HTTP 200
content-type: application/json
```
```json
{
  "id": 1,
  "title": "Google Meet aaa-bbbb-ccc",
  "platform": "google_meet",
  "native_id": "aaa-bbbb-ccc",
  "status": "bot_sent",
  "step": "waiting",
  "status_text": "Waiting to be let in. Admit 'Beyric Meeting Support' in the meeting",
  "status_tone": "warning",
  "failure_reason": null,
  "bot_invite_email": "note@example.test",
  "can_cancel": true,
  "can_end": false,
  "refresh_after_seconds": 30
}
```
_Capture: `docs/fe-captures/bot-invite-tip/06-meeting-waiting-tip-on.json`_

Not let in (#07): the bot was sent and never admitted. Trimmed.

```json
{
  "id": 2,
  "title": "Google Meet ddd-eeee-fff",
  "platform": "google_meet",
  "status": "failed",
  "step": "waiting",
  "status_text": "Failed",
  "status_tone": "danger",
  "failure_reason": "The bot was not let into the meeting. The host may have declined it, or the waiting room timed out.",
  "bot_invite_email": "note@example.test",
  "retry_block": "no_audio",
  "retry_block_message": "This meeting can't be retried: nothing was captured. Send the bot again with a new link.",
  "can_retry": false
}
```
_Capture: `docs/fe-captures/bot-invite-tip/07-meeting-refused-tip-on.json`_

`null` for the same waiting state on **Zoom** (#08: Zoom guests always wait for the host) and for a
Google Meet bot that is already in the call (#09), and for the same waiting meeting when the server
has no address (#11). #08, trimmed:

```json
{
  "id": 3,
  "title": "Zoom 81234567890",
  "platform": "zoom",
  "status": "bot_sent",
  "status_text": "Waiting to be let in. Admit 'Beyric Meeting Support' in the meeting",
  "failure_reason": null,
  "bot_invite_email": null
}
```
_Capture: `docs/fe-captures/bot-invite-tip/08-meeting-zoom-tip-on.json`_

**Where on the page.** Reuse the spot the page already uses for the waiting and the failed states: in
the state card, under the sentence, beside "Cancel and remove the bot" (waiting) or the "What went
wrong" text and "Try again" (not let in). The tip appears and disappears with the 30-second refresh,
because it follows `bot_invite_email`: once the bot is let in the field turns `null`.

## 4. Field-nesting traps

- `bot_invite_email` is a **top-level** field of both bodies, with the same name and meaning. It is
  **not** in `GET /api/meetings` (the list's items are `MeetingSummary`, which does not have it) and
  not inside `upcoming[]`. Read it from the detail.
- It is always present on the two bodies the types describe, as a string or `null`. The generated
  type marks it optional (`bot_invite_email?: string | null`) because the schema gives it a default;
  treat `undefined` and `null` the same (no tip). An older cached body without the key is fine.
- On `GET /api/calendar` the field is **not** tied to `connected`: it is present for a person with no
  calendar too (it is a server fact). The Settings tip is for a connected **Google** calendar only,
  so check `connected` and `provider` yourself; Outlook gets no tip.

## 5. UX consequences to surface

- **Do not promise.** Say what happens ("gets in without waiting for you to admit it") and the
  limit in the same place ("This only skips the wait on Google Meet. Zoom and Teams meetings still
  ask the host to admit the note taker"). Never "guaranteed" or "always".
- **Never write the address in the web code.** It comes from the server and can change; a stale
  hard-coded address would send people to a mailbox that is not the bot's.
- **The copy button must not be dead.** Use the clipboard API; when the browser refuses, say "Copy
  failed" and select the address so Ctrl/Cmd+C finishes the job. "Copied" is announced (the button is
  `aria-live="polite"`).
- Showing the tip does not change what the bot does. Nothing is added to anyone's event by the app.

## 6. Verification table

| Behaviour | Status |
|---|---|
| `GET /api/calendar` carries the address when Skribby + account + address are set (#05) | verified live against the running app (Google faked) |
| The same read has `bot_invite_email: null` with the address unset, with no account, with Vexa (#10, #12, #13) | verified live |
| `GET /api/meetings/{id}` carries it for a Google Meet meeting waiting to be let in and for one not let in (#06, #07) | verified live; the two meetings are rows set directly in the states the worker leaves them in (no real bot) |
| `null` for Zoom waiting, a Google Meet bot already in the call, and an unset address (#08, #09, #11) | verified live |
| The rule in every combination (each missing setting, Vexa, other platforms, other states, a state not seen for a while, Vexa-run meetings), the setting's validation (empty ok, bad value refuses at start naming `BOT_INVITE_EMAIL`), `PUT /api/calendar/settings` carrying the field | verified by the pytest file `tests/test_bot_invite_tip.py` against a real Postgres; not captured |
| The tip on Settings and on the meeting page, its copy button and its fallback | verified by `web/tests/bot-invite-tip.test.tsx`, fed these captures; **not** looked at in a real browser against the real app, **not** Safari or Firefox |
| That a real signed-in Skribby bot on the guest list skips the waiting room | **not verified here**: vendor claim and the 2026-11-08 bake-off measurement (`docs/spike/meeting-bot-vendors.md`) |
| `bot_invite_email` while `BOT_PROVIDER=skribby` with a real `SKRIBBY_ACCOUNT_ID` on the live server | **not verified**: needs Adebayo's `.env` line (checklist) |
