# Frontend guide: the note taker name (Settings > "Note taker name")

Every body, status and error string below is copied from a capture made against the running app (`scripts/capture_bot_name.py`, files in `docs/fe-captures/bot-name/`, number in brackets). Run the script again to re-derive any claim. Placeholders: the account, its email and the Skribby stand-in's bot ids are fake.

## 1. What it is, in the frontend's terms

A person can choose the name people see when their note taker joins their meetings, for example "Alex from Product". Empty means the server default. The card sits in Settings between Time zone and Default notes language. It is lead-designed (no designer file): it reuses the Time zone card, `Field` and the standard buttons. The name applies to meetings created from now on; a bot already sent keeps the name it joined with.

## 2. Endpoints (all need a login; writes need `X-CSRF-Token`)

### `GET /api/settings/bot-name` -> 200 [04, 06, 14]

```json
{ "bot_name": null, "bot_name_default": "Beyric Meeting Support" }
```

After a save: `{ "bot_name": "Alex from Product", "bot_name_default": "Beyric Meeting Support" }` (06 is read after a save of the name below; 14 is read after the refusals and still holds the previous good value).

- `bot_name` is **`null` when none is set (present, never absent)**. `bot_name_default` is read from the server (`SKRIBBY_BOT_NAME` while Skribby is the provider): use it for the placeholder and the help text. Never hard-code it.
- Not signed in: 401 `{"error":{"code":"not_authenticated","message":"Log in first."}}` [03].

### `PUT /api/settings/bot-name` -> 200 [05, 07, 19, 20]

Request `{ "bot_name": "  Alex   from  Product " }` -> response (the stored value, trimmed, inner whitespace collapsed):

```json
{ "bot_name": "Alex from Product", "bot_name_default": "Beyric Meeting Support" }
```

- An emoji is accepted: `{"bot_name": "Notes 📝 for Ada"}` is stored as sent [07].
- **Clear** with `{"bot_name": null}` [19] or `{"bot_name": ""}` [20] (also whitespace only): 200 with `"bot_name": null`.
- The card's Save with an empty field should send `null`.

### Errors

| Case | Status | Body (exact) | Capture |
|---|---|---|---|
| 51 characters or more (the limit is 50, counted after whitespace is collapsed) | 422 | `{"error":{"code":"bot_name_invalid","message":"Use 50 characters or fewer."}}` | 08 |
| Looks like an email (`x@y`) | 422 | `... "message":"Don't put an email address in the name."` | 09 |
| Looks like a web address (`https://...`, `www.`, or a bare domain such as `Alex from acme.example`) | 422 | `... "message":"Don't put a web address in the name."` | 10, 11 |
| Hidden or control characters (a bidirectional override, a zero-width space, a control character) | 422 | `... "message":"Use plain letters, numbers and punctuation only: that name has hidden or control characters."` | 12 |
| `bot_name` not a string (for example `12`) | 422 | `{"error":{"code":"validation_error","message":"Some fields are missing or not valid.","fields":[{"field":"bot_name","message":"Input should be a valid string"}]}}` | 13 |
| No `X-CSRF-Token` | 403 | `{"error":{"code":"csrf_rejected","message":"Missing or wrong X-CSRF-Token header. Read it from GET /api/session."}}` | 15 |

A refused write changes nothing [14].

## 3. Traps

- **Show the 422 message.** `errorMessage()` in `web/lib/api/client.ts` treats every 422 as developer text and shows "Something went wrong." For `code === "bot_name_invalid"` show `error.message` as it comes: it is written for the person. (The component does this.)
- A trailing or inner space typed by the person is cleaned by the server: **replace the field's value with the response's `bot_name`** after a save.
- Hidden characters in a name are **refused**, not removed: do not strip them in the browser either; show the server's line.
- `bot_name` appears in **no other API answer**; the meeting detail does not carry it.

## 4. UX consequences the page must surface

- The help text is the owner's approved wording: "This is the name people see when the note taker joins your meetings. Leave it empty to use “<default>”. Choose a name your attendees will recognise as your notes taker, such as “Alex from Product”. Don't use another person's name, and don't hide that it records. Google Meet and Zoom may refuse bots whose names look like recorders." The default in it is `bot_name_default`.
- **On Google Meet the name may not show.** The bot there is signed in as a shared Google account, and it is unproven whether Skribby shows the custom name for a signed-in bot. Until the live check is done, the app's "Waiting to be let in. Admit '...' in the meeting" line (the `status_text` of `GET /api/meetings/{id}`) names the **account's own name** on a signed-in Meet bot, not the custom one. On Zoom and Teams it names the custom name. Do not add copy that promises the custom name on Meet.
- A change applies to the **next** meeting. A meeting already sent keeps its name.

## 5. What the meeting answer shows (the "Admit" line)

`GET /api/meetings/{id}` -> `status_text` while the bot waits [24 to 27]:

| Meeting | `status_text` |
|---|---|
| sent with the name "Alex from Product" (Meet, guest) [24] | `Waiting to be let in. Admit 'Alex from Product' in the meeting` |
| the same for a Zoom meeting [25] | `Waiting to be let in. Admit 'Alex from Product' in the meeting` |
| sent after clearing (server default) [26] | `Waiting to be let in. Admit 'Beyric Meeting Support' in the meeting` |
| a meeting from before the feature (null name) [27] | `Waiting to be let in. Admit 'Beyric Meeting Support' in the meeting` |

The capture server has no signed-in account, so the signed-in Meet row of the table in the SOP is **not captured** (it comes from the unit tests: the line names the account's own name).

## 6. What was sent to Skribby (stand-in)

[22] records the bodies a stand-in Skribby received from the real app and client: `{"service": "gmeet", "bot_name": "Alex from Product", ...}`, then zoom with the same name, then gmeet with `"Beyric Meeting Support"` after clearing. [23] is the stored `meetings.bot_name` of the three.

## 7. Verification table

| Behaviour | Verified |
|---|---|
| GET/PUT shapes, null default, clear, trimming, emoji | live (#04 to #07, #19, #20) |
| 401, 403, every 422 string and the validation-error shape | live (#03, #08 to #13, #15) |
| A refusal changes nothing | live (#14) |
| The name sent as `bot_name` for Meet and Zoom, default after clearing | live against a stand-in Skribby (#22, #23); the real Skribby was not called |
| `status_text` names the custom name / default / null-name meeting | live (#24 to #27) |
| Signed-in Meet "Admit" line, Teams body, calendar auto-join and resend paths | unit tests only (`tests/test_bot_name.py`); not captured |
| What a participant sees in Meet, Zoom, Teams | **not verified** (see the SOP, "How to prove it live") |
| The card in a browser against the live API | not verified; unit tests and the mock-free component test only |
