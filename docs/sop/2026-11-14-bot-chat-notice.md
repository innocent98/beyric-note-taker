# SOP: the bot says who it is in the meeting chat when it joins (C18), behind a switch that is off

**What shipped.** When `BOT_CHAT_NOTICE_ENABLED=true`, a Skribby bot posts one line in the meeting chat on joining: "Hi, I'm {bot name}. {owner name} added me to record this meeting and write notes. If you'd rather not be recorded, please tell {owner name} before we start." Branch `feature/bot-chat-notice` (PR opened 2026-11-14, not merged by this work). **Default OFF**; it has never run in a real meeting.

## Why

The people in a meeting who did not send the bot see an unexplained participant. The sign-up box (`docs/sop/2026-11-14-recording-consent-box.md`) makes the account holder responsible for telling attendees; this line is the bot telling them itself, and giving them a way to say no before the recording starts.

## What Skribby's own pages say (read 2026-11-14)

- `https://docs.skribby.io/rest-api/openapi` **answers 301 to `https://skribby.io/docs`** (it did not open as a page of its own). That page, `https://skribby.io/docs/rest-api`, the endpoint page `https://skribby.io/docs/rest-api/bot-operations/createbot` (HTML, and its `.md` form) and the guides `https://skribby.io/docs/guides/zoom-chat` and `.../transcription-privacy` were read. A direct fetch of the OpenAPI JSON was not found at the addresses tried (`platform.skribby.io/openapi.json` redirects to a sign-in page).
- Create bot, `POST /bot`: the optional body field **`initial_chat_message`, type `string`**: "Whenever the bot joins the meeting, it'll send this message to the chat." **No maximum length is stated. No platform list is stated.** The docs landing page says "Configure an initial chat message, recording retention, and deletion to match your application's requirements" (privacy controls).
- The Zoom chat guide covers only Zoom (separate `chat-message` action, private messages, threads). Its platform table says **Google Meet and Microsoft Teams: not documented** for direct messages and threading. The old `POST /bot/{id}/chat-message` endpoint is marked deprecated.
- So: that `initial_chat_message` posts in **Meet, Zoom and Teams** is **not stated by Skribby** for Meet and Teams, and the earlier leads' note that it works on all three remains an unproven claim. Nobody has seen it live. That is why the switch is off.

## How

| Piece | What and why |
|---|---|
| Setting | `BOT_CHAT_NOTICE_ENABLED` (`app/config.py`, `.env.example`, `deploy/README.md`), default `false` |
| Words | `app/chat_notice.py` `notice_text`: the approved line, exact. Owner name empty (no display name): "The host added me ... please tell the host" |
| Bot name | The name the host sees for that meeting's bot, from `bot_names.shown_name` (`app/bot_names.py`, per-user note taker name, `docs/sop/2026-11-14-per-user-bot-name.md`), passed to `chat_notice.send_options(..., bot_name=)`. Without one, `chat_notice.notice_bot_name(settings)`: the server default. On a signed-in Meet bot it is the account's own name until `BOT_NAME_APPLIES_WHEN_SIGNED_IN` is on |
| Cleaning | (Changed by the per-user name work: the BOT name is only made one plain line and cut with `titles.clean_title`, because it is validated when set and defanging it printed "Dr[.]Smith"; the OWNER's name still gets what follows.) Both names went through `titles.mail_line` (one plain line, control and direction characters dropped, a web address replaced by "[link removed]", a bare domain defanged) and are cut at 60 characters, which also keeps the whole line to about 250 characters. The names are typed by people and read by everyone in the meeting |
| Where it joins the send | `transcript_source.send_options` already feeds both callers of `send_bot` (placement and resend); it now also returns `chat_notice` when the setting is on and the provider takes one (`accepts_chat_notice`, Skribby only). Vexa and any other provider get nothing and no error. The owner's name is read inside a savepoint; a failed read means "the host", never a failed send |
| Skribby call | `SkribbyClient.payload(..., chat_notice)` adds `initial_chat_message` only when given |
| Safety net | `SkribbyClient.create` already retried once without `custom_vocabulary` on a 400/422 (a 4xx means no bot was made). It now does the same for `initial_chat_message`, dropping both optional fields together, and logs which by name, never the text. So a parameter Skribby rejects costs the notice, not the bot |
| Rejected | A per-meeting toggle (nothing asked for it); a Vexa implementation (stopped); logging the text; sending the owner's email when there is no name |

## What's involved

`app/chat_notice.py` (new), `app/transcript_source.py` (`send_options`), `app/skribby.py` (`OPTIONAL_PARAMETERS`, `payload`, `create`, `SkribbyProvider.send_bot` / `accepts_chat_notice`), `app/config.py`, `.env.example`, `deploy/README.md`, `tests/test_chat_notice.py`. No migration, no API or FE change, so no FE guide.

## Verification

`ruff check .`, `ruff format --check .` and `pytest tests/test_chat_notice.py tests/test_transcript_source.py tests/test_skribby_provider.py` on a scratch Postgres (327 passed). The Skribby HTTP client is faked with `pytest-httpx`: parameter absent while the setting is off; present with the exact approved text when on, on Meet, Zoom and Teams links; long, odd, empty and link-carrying names cleaned and cut; Vexa never given one; a 422 or 400 retried once without the field, both optional fields dropped together, the text not in the log; a 403 not retried; a database error reading the owner's name still sends the line.

**Not verified:** anything against the real Skribby service or a real meeting. Whether `initial_chat_message` is accepted, whether the line appears in the chat on each platform, whether a guest bot may post in a Teams or Meet chat at all, and whether Skribby truncates a long line, are all unknown.

## Operate / roll back

Off by default; nothing changes until `BOT_CHAT_NOTICE_ENABLED=true` is in the server `.env` and web, worker and scheduler are restarted. To roll back, set it `false` (or remove it) and restart; a bot already in a meeting keeps whatever it posted. No data to undo.

### How to prove it live (one real meeting on each platform)

1. Turn it on in the server `.env` (`BOT_CHAT_NOTICE_ENABLED=true`), restart `web`, `worker`, `scheduler`.
2. Make sure your account has a display name (Settings), since "the host" is the fallback.
3. Start a test meeting yourself on each platform you can: **Google Meet, Zoom, Microsoft Teams** (a second person or device in the room helps: open the meeting chat and watch). Paste the link into Beyric as usual.
4. Admit the bot (Meet and Zoom ask the host; Teams may place it in the lobby). **What to look for:** within a few seconds of the bot appearing, one chat message from the bot reading exactly "Hi, I'm <the bot's name>. <your name> added me to record this meeting and write notes. If you'd rather not be recorded, please tell <your name> before we start."
5. Note per platform: line appeared (yes/no), wording right, names right, how long after joining, and whether the meeting still recorded and got notes. A bot that joined with no line in the chat on a platform means that platform ignores the parameter; a bot that failed to join with the setting on and joined with it off would be a bug to report (the app should have retried without the field and logged `retrying once without the chat notice`).
6. Server logs: `docker compose logs worker web | grep "chat notice"` shows a retry if Skribby refused the field.
7. **To turn it off:** `BOT_CHAT_NOTICE_ENABLED=false` (or delete the line), restart `web`, `worker` and `scheduler`.

Record the result on the checklist line for C18 and tell the lead; the lead decides whether to leave it on.

## Follow-ups

- Prove it live on Meet, Zoom and Teams (above), then decide whether to default it on.
- Per-user bot name (a later session): change `notice_bot_name` only.
- If Skribby documents a length limit, set `NAME_LIMIT` from it.
- The privacy policy and terms may want to mention the chat line; that is the lawyer's, untouched here.
