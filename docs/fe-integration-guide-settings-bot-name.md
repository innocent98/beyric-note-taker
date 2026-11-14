# Frontend guide: the note taker name (Settings > "Note taker name")

Every body, status and error string below is copied from a capture made against the running app (`scripts/capture_bot_name.py`, files in `docs/fe-captures/bot-name/`, number in brackets). Run the script again to re-derive any claim. Placeholders: the account, its email and the Skribby stand-in's bot ids are fake.

## 1. What it is, in the frontend's terms

A person can choose the name people see when their note taker joins their meetings, for example "Alex from Product". Empty means the server default. The card sits in Settings between Time zone and Default notes language. It is lead-designed (no designer file): it reuses the Time zone card, `Field` and the standard buttons. The name applies to meetings created from now on; a bot already sent keeps the name it joined with.

## 2. Endpoints (all need a login; writes need `X-CSRF-Token`)

### `GET /api/settings/bot-name` -> 200 [04, 06, 09, 17]

```json
{
  "bot_name": null,
  "bot_name_default": "Beyric Meeting Support",
  "warning": null,
  "bot_name_applies_when_signed_in": false,
  "meet_account_name": null
}
```

After a save the same five fields come back with `bot_name` set (06 is read after a save; 17 is read after the refusals and still holds the previous good value).

- `bot_name` is **`null` when none is set (present, never absent)**. `bot_name_default` is read from the server (`SKRIBBY_BOT_NAME` while Skribby is the provider): use it for the placeholder and the help text. Never hard-code it.
- `warning` is `null`, or the soft warning (below). Present on every answer.
- `bot_name_applies_when_signed_in` is the server's switch `BOT_NAME_APPLIES_WHEN_SIGNED_IN`, **read only**: `false` (live evidence 2026-11-14: Skribby ignores the custom name for the signed-in Meet bot).
- `meet_account_name` is the Google account's own name that the signed-in Meet bot always shows (`SKRIBBY_ACCOUNT_NAME`), or `null` when the page should say nothing about Meet's name (no signed-in account, no account name set, or the switch above is `true`) [31 has a name, 32 and 04 have `null`]. When it is a string the card shows under the help text: "On Google Meet the note taker always shows its own name, “<meet_account_name>”. Your name shows on Zoom and Teams."; when `null`, nothing.
- Not signed in: 401 `{"error":{"code":"not_authenticated","message":"Log in first."}}` [03].
- Sending `bot_name_applies_when_signed_in` (or any unknown field) in a PUT is a 422 `validation_error`: the flag is read only (unit test, not captured).

### `PUT /api/settings/bot-name` -> 200 [05, 07, 08, 10, 22, 23]

Request `{ "bot_name": "  Alex   from  Product " }` -> response (the stored value, trimmed, inner whitespace collapsed):

```json
{
  "bot_name": "Alex from Product",
  "bot_name_default": "Beyric Meeting Support",
  "warning": null,
  "bot_name_applies_when_signed_in": false,
  "meet_account_name": null
}
```

- An emoji is accepted: `{"bot_name": "Notes 📝 for Ada"}` is stored as sent [10]. A dot is accepted unless the name ends in a listed domain: `{"bot_name": "Dr.Smith"}` is stored as sent [07].
- **Soft warning** [08, 09]: `{"bot_name": "Alex's AI Notetaker"}` is **saved (200)** and the answer has `"warning": "Google Meet and Zoom may refuse bots with names like this."` (the same on a later GET). It fires for the whole words Bot, Notetaker, Note taker, Recording, Transcription and AI, in any case. Draw it as a notice under the field, never as an error, and never block the save. The browser does not repeat the rule: it shows the `warning` of the last answer.
- **Clear** with `{"bot_name": null}` [22] or `{"bot_name": ""}` [23] (also whitespace only): 200 with `"bot_name": null`.
- The card's Save with an empty field should send `null`.

### Errors

| Case | Status | Body (exact) | Capture |
|---|---|---|---|
| 51 characters or more (the limit is 50, counted after whitespace is collapsed) | 422 | `{"error":{"code":"bot_name_invalid","message":"Use 50 characters or fewer."}}` | 11 |
| Looks like an email (`x@y`) | 422 | `... "message":"Don't put an email address in the name."` | 12 |
| Looks like a web address: `://`, `www.`, a meeting-host link, or `label.tld` with a TLD from the short list `com net org io ai co app dev me us uk ng info biz xyz link live gg tv ly` (`Alex from acme.com`, `Notes.ai`). `Dr.Smith`, `St.Mary` and `Alex.Coach` are fine | 422 | `... "message":"Don't put a web address in the name."` | 13, 14 |
| Hidden or control characters (a bidirectional override, a zero-width space, a control character), or a name made only of characters that draw nothing (Braille blank U+2800, U+034F, a bare variation selector) | 422 | `... "message":"Use plain letters, numbers and punctuation only: that name has hidden or control characters."` | 15 (the override; the invisible-only names are unit tests) |
| `bot_name` not a string (for example `12`) | 422 | `{"error":{"code":"validation_error","message":"Some fields are missing or not valid.","fields":[{"field":"bot_name","message":"Input should be a valid string"}]}}` | 16 |
| No `X-CSRF-Token` | 403 | `{"error":{"code":"csrf_rejected","message":"Missing or wrong X-CSRF-Token header. Read it from GET /api/session."}}` | 18 |

A refused write changes nothing [17].

## 3. Traps

- **Show the 422 message.** `errorMessage()` in `web/lib/api/client.ts` treats every 422 as developer text and shows "Something went wrong." For `code === "bot_name_invalid"` show `error.message` as it comes: it is written for the person. (The component does this.)
- A trailing or inner space typed by the person is cleaned by the server: **replace the field's value with the response's `bot_name`** after a save.
- Hidden characters in a name are **refused**, not removed: do not strip them in the browser either; show the server's line.
- `bot_name` appears in **no other API answer**; the meeting detail does not carry it.

## 4. UX consequences the page must surface

- Under the help text, **when `meet_account_name` is a string**, one plain sentence: "On Google Meet the note taker always shows its own name, “<meet_account_name>”. Your name shows on Zoom and Teams." (lead-designed, true by live evidence).
- The help text is the owner's approved wording: "This is the name people see when the note taker joins your meetings. Leave it empty to use “<default>”. Choose a name your attendees will recognise as your notes taker, such as “Alex from Product”. Don't use another person's name, and don't hide that it records. Google Meet and Zoom may refuse bots whose names look like recorders." The default in it is `bot_name_default`.
- **On Google Meet the custom name does not show.** The bot there is signed in as a shared Google account and always shows that account's own name (live evidence 2026-11-14, a real Meet call: waiting list, participant list and chat sender). The app's "Waiting to be let in. Admit '...' in the meeting" line (the `status_text` of `GET /api/meetings/{id}`) names that account name on a signed-in Meet bot when the server's `SKRIBBY_ACCOUNT_NAME` is set. On Zoom and Teams it names the custom name (guest bots; not yet seen live). Do not add copy that promises the custom name on Meet.
- A change applies to the **next** meeting. A meeting already sent keeps its name.

## 5. What the meeting answer shows (the "Admit" line)

`GET /api/meetings/{id}` -> `status_text` while the bot waits [27 to 30]:

| Meeting | `status_text` |
|---|---|
| sent with the name "Alex from Product" (Meet, guest) [27] | `Waiting to be let in. Admit 'Alex from Product' in the meeting` |
| the same for a Zoom meeting [28] | `Waiting to be let in. Admit 'Alex from Product' in the meeting` |
| sent after clearing (server default) [29] | `Waiting to be let in. Admit 'Beyric Meeting Support' in the meeting` |
| a meeting from before the feature (null name) [30] | `Waiting to be let in. Admit 'Beyric Meeting Support' in the meeting` |

The capture server has no signed-in account, so the signed-in Meet row of the table in the SOP is **not captured** (it comes from the unit tests: the line names the account's own name).

## 6. What was sent to Skribby (stand-in)

[25] records the bodies a stand-in Skribby received from the real app and client: `{"service": "gmeet", "bot_name": "Alex from Product", ...}`, then zoom with the same name, then gmeet with `"Beyric Meeting Support"` after clearing. [26] is the stored `meetings.bot_name` of the three.

## 7. "Your name" (Settings, `users.name`)

The display name shown to teammates and said by the note taker in the meeting chat ("Hi, I'm ... Ada Example added me ..."); without one the chat says "The host". Lead-designed card, label "Your name", help text "This is the name shown to your teammates and in the message the note taker posts when it joins your meetings."

### `GET /api/settings/name` -> 200 [33, 38]

`{ "name": null }` for an account that has none (the capture's admin account, made by the migration); `{ "name": "Ada Example" }` after a save. `name` is present and `null`, never absent. `GET /api/session` carries the same name in `user.name` [39].

### `PUT /api/settings/name` -> 200 [37]

Request `{ "name": "  Ada   Example\u202e " }` (the last character is a direction override) -> `{ "name": "Ada Example" }`: trimmed, inner whitespace collapsed, control, direction and invisible characters **removed** (not refused). 1 to 100 characters; **a name cannot be emptied**.

| Case | Status | Body (exact) | Capture |
|---|---|---|---|
| Empty, spaces only, or nothing visible left after cleaning | 422 | `{"error":{"code":"name_invalid","message":"Enter your name."}}` | 34 |
| 101 characters or more (counted after whitespace is collapsed) | 422 | `{"error":{"code":"name_invalid","message":"Use 100 characters or fewer."}}` | 35 |
| `name` not a string (`null`, a number), missing, or an unknown field | 422 | `{"error":{"code":"validation_error","message":"Some fields are missing or not valid.","fields":[{"field":"name","message":"Input should be a valid string"}]}}` | 36 |
| No `X-CSRF-Token` | 403 | the usual `csrf_rejected` body | 40 |

Traps: show `error.message` for `name_invalid` (the shared `errorMessage()` hides every 422 as developer text); **replace the field's value with the response's `name`** after a save; call `router.refresh()` so the sidebar user and other pages show the new name; there is **no Clear** (an empty name is refused). A name stored before this route (sign-up allowed up to 200 characters) reads back as stored and can be saved again only at 100 or fewer.

## 8. Verification table

| Behaviour | Verified |
|---|---|
| GET/PUT shapes, null default, clear, trimming, emoji, dotted name, the flag | live (#04 to #07, #10, #22, #23) |
| The soft warning (saved, `warning` set, also on GET) | live (#08, #09) |
| 401, 403, every 422 string and the validation-error shape | live (#03, #11 to #16, #18) |
| A refusal changes nothing | live (#17) |
| The name sent as `bot_name` for Meet and Zoom, default after clearing | live against a stand-in Skribby (#25, #26); the real Skribby was not called |
| `status_text` names the custom name / default / null-name meeting | live (#27 to #30) |
| `meet_account_name` set and `null` | live (#31, #32, #04) |
| "Your name": read `null` and a name, save with cleaning, 422 empty and too long, strict body, CSRF, session shows it | live (#33 to #40) |
| The chat notice says the saved name instead of "The host" | unit test only (`tests/test_display_name.py`); not captured, and the real meeting has not been seen with it |
| Google Meet shows the account's own name, not the configured one | **relayed live evidence 2026-11-14** (the owner's real call, via the lead); not captured by us |
| The flag set to `true` in the answer, invisible-only names, signed-in Meet "Admit" line, Teams body, calendar auto-join, send now, retries, Skribby refusing a name | unit tests only (`tests/test_bot_name.py`, `tests/test_bot_name_paths.py`); not captured |
| What a participant sees in Meet, Zoom, Teams | **not verified** (see the SOP, "How to prove it live") |
| The card in a browser against the live API | not verified; unit tests and the mock-free component test only |
