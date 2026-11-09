# SOP: the "add the note taker to your event" tip

## What shipped
A small in-app tip, **"Add note@beyrictech.com to your event to skip the wait"**, with a Copy button, for people whose bot joins Google Meet meetings. Branch `feature/bot-invite-tip`, PR to `main` (number in the PR list). Adebayo's option C of 2026-11-09 ("yes, build the tip"): a tip only, **no new Google permission, no calendar write** (option B, where Beyric adds the bot to the event, is a separate design). No migration.

**Lead-designed, not in the designer's file.** The designer drew no such tip. It is built from the app's own pieces (the colours and radius of the rule cards on Settings, the secondary small copy button the notes use, the muted `meta` line) so the designer can redraw it. The words and where it sits are the part to keep.

## Why
On Google Meet a signed-in bot whose account email is on the event's guest list skips the waiting room (vendor claim in `docs/spike/meeting-bot-vendors.md`; the 2026-11-08 bake-off measured 20 to 27 s with no knock when invited, 39 to 46 s with a knock when not). Today the bot waits in the lobby until the host admits it (about 45 to 75 s in tests, gives up after about 10 minutes and the owner gets "Could not join"). Telling people the one thing that skips that wait costs nothing and needs no new permission. Zoom and Teams bots join as guests and always wait, so the tip says so and is never shown for them.

## How
| Decision | Why |
|---|---|
| **The address is a server setting, `BOT_INVITE_EMAIL`** (`app/config.py`), never in the web code | It is the bot's real Google account; hard-coding it in the web would go stale or point at the wrong mailbox. Empty by default, so the tip is hidden until the operator sets it. |
| **The server says it only when it is true**: `Settings.bot_invite_email_shown` is the address only when `BOT_PROVIDER=skribby` **and** `SKRIBBY_ACCOUNT_ID` is set (a signed-in bot; a guest bot has no address to invite) **and** the address is set | Vexa's bot, or a guest Skribby bot, is on nobody's guest list: telling people to invite it would be wrong. |
| **Validated at start**: empty is fine; otherwise exactly one plain address (lower-cased, no list, no `Name <a@b.c>`, no spaces). A bad value stops the process naming `BOT_INVITE_EMAIL` | Same stance as `SKRIBBY_ACCOUNT_ID` and `MICROSOFT_ALLOWED_TENANTS`: a typo must not reach a screen. A deliberately plain regex (no `email-validator` dependency; this is an address an operator types in). |
| **No new endpoint: one field, `bot_invite_email`, on two existing reads.** `GET /api/calendar` (Settings, the same way `microsoft_enabled` rides on it) and the meeting detail `GET /api/meetings/{id}` (the meeting page) | The pages already load both. `null` (present, not absent) whenever the server may not say it. |
| **The meeting decides for itself** (`app/bot_invite.py`): the field is the address only for a **Google Meet** meeting **run by Skribby** that is **waiting to be let in right now** (`completion_reason == awaiting_admission` and the live state not stale) **or failed because the bot was not let in** (`REASON_NOT_LET_IN`, `REASON_NOT_ADMITTED`) | The page needs no rule of its own and cannot drift from the server. A meeting Vexa ran (`bot_provider != skribby`) gets nothing. Zoom and Teams get nothing. The tip disappears by itself on the 30-second refresh once the bot is let in. |
| **Settings: only for a connected Google calendar**, as a short line under the auto-join rules (`components/app/settings-calendar.tsx`) | The tip is about Google Meet; an Outlook calendar gets none. |
| **Meeting page: in the existing state card**, under the sentence for "waiting to be let in" and under "What went wrong" for a not-admitted meeting (`components/app/meeting-view.tsx`) | Reuses the spot the page already uses for those states. |
| **One component, `components/app/bot-invite-tip.tsx`**, over the existing `CopyButton` (clipboard API, textarea fallback, "Copied" / "Copy failed", `aria-live="polite"`). New: `CopyButton` takes `onFailed`, and the tip uses it to **select the address** when the browser refuses to copy | No dead button: the worst case is the address selected for Ctrl/Cmd+C. |
| **Honest wording, no promise** | "Add `<address>` to your event to skip the wait. Put it on the event's guest list and the note taker gets in without waiting for you to admit it. This only skips the wait on Google Meet. Zoom and Teams meetings still ask the host to admit the note taker." |

Rejected: a hard-coded address in the web; a new `GET /api/bot-invite` endpoint (a third request for one string); deriving "waiting / not admitted" in the web from `status_text` or `failure_reason` strings (fragile); showing the tip for any Google Meet meeting regardless of state (noise); adding the bot to the user's events (option B, needs a Google write permission and a design).

## What's involved
- Server: `app/config.py` (`bot_invite_email`, its validator, `bot_invite_email_shown`), `app/bot_invite.py` (the meeting rule), `app/api_schemas.py` (`MeetingDetail.bot_invite_email`), `app/api.py` (`_detail`), `app/calendar_api.py` (`CalendarOut.bot_invite_email`, `_calendar_out`).
- Contract: `docs/openapi.json` (the two fields and the `CalendarOut` description, edited by hand in the file's own style: a full regeneration with this machine's library versions rewrites the whole file), `web/lib/api/schema.d.ts` (regenerated with `npm run gen:api`).
- Web: `components/app/bot-invite-tip.tsx` (new), `components/app/copy-button.tsx` (`onFailed`), `components/app/settings-calendar.tsx`, `components/app/meeting-view.tsx`, `styles/pages.css` (`.invite-tip`, tokens only).
- Config and docs: `.env.example`, `deploy/README.md` (settings table), `pyproject.toml` (F811 ignore for the new test file, as the other fixture-importing test files have), the master checklist, `docs/fe-integration-guide-bot-invite-tip.md` (new) with pointers in the calendar-settings and owner-app guides.
- Tests and evidence: `tests/test_bot_invite_tip.py` (42 cases), `tests/test_calendar_api.py` (the pinned body gains the field), `web/tests/bot-invite-tip.test.tsx` (14 cases), `web/tests/helpers.ts`, `scripts/capture_bot_invite_tip.py`, `docs/fe-captures/bot-invite-tip/`.

## Verification
- **Backend:** `tests/test_bot_invite_tip.py` against a real Postgres: the setting (off by default, empty ok, a plain address kept trimmed and lower-cased, 11 bad values refused naming `BOT_INVITE_EMAIL`, a bad env value stops `get_settings()`), the exposure rule (Skribby + account + address shows it; each one missing, and Vexa, hides it), the meeting rule (waiting and not-let-in show it; Zoom, Teams, no platform, Vexa-run, joining, in the call, ready, processing, a stale state, no state time, another failure all say nothing), and the two responses' shape (calendar connected or not, after the settings write; meeting waiting, not let in, Zoom, server not set up). `tests/test_calendar_api.py` (touched, pinned body) passes with the field.
- **Web:** `web/tests/bot-invite-tip.test.tsx` (14 cases) fed the live captures: the tip shows the server's address, the limit and a Copy button; nothing without an address; copy calls the clipboard and reads "Copied"; a refused copy reads "Copy failed" and selects the address; placed under the rules on Settings for Google only (not for Outlook, not with `null`); on the meeting page for waiting and not-let-in, not for Zoom, a bot already in the call, or an unset address.
- **Live captures:** `scripts/capture_bot_invite_tip.py` against the real app over HTTP (13 files). Guide: `docs/fe-integration-guide-bot-invite-tip.md`.
- **Not verified:** a real Skribby bot skipping the wait on a real guest list (vendor claim and bake-off measurement only); the page in a real browser; Safari and Firefox clipboard behaviour. Local checks were the fast ones only (ruff, `npm run lint`, `npm run typecheck`, the two new test files and the touched calendar test); CI ran the rest.

## Operate / roll back
- **Turn on:** put `BOT_INVITE_EMAIL=note@beyrictech.com` in the server `.env` and restart `web`, `worker` and `scheduler` (every process validates the setting at start). It shows only while `BOT_PROVIDER=skribby` and `SKRIBBY_ACCOUNT_ID` is set.
- **Turn off:** remove the line (or empty it) and restart; the field becomes `null` and the tip disappears. Nothing is stored.
- **A bad value** (more than one address, a display name, a typo with a space) stops the process at start with `BOT_INVITE_EMAIL must be one email address, such as note@example.com`: fix the line.
- **Roll back the code:** revert the PR. The old web ignores the extra field. **Server steps before merge: none** (the tip stays hidden until `BOT_INVITE_EMAIL` is set).

## Follow-ups
- Designer to redraw the tip (the box, the title line, the limit line, where it sits on the meeting page).
- Adebayo: set the `.env` line after merge; then confirm on one real invited and one uninvited Google Meet that the bot skips the wait only when invited, and that the tip reads right on his screens.
- Option B (Beyric adds the bot to the user's events) is still a separate design: it needs a Google write permission.
- `GET /api/meetings/{id}` reads `get_settings()` (as `status_text` does) rather than the app's `state.settings`: harmless in production (they are the same), but a test must patch `app.api.get_settings`.
