# FE integration guide: the recording-consent box

Every request and response below is copied from a capture made against the running app by
`python scripts/capture_recording_ack.py <postgres url>` (2026-11-14). The files are in
`docs/fe-captures/recording-ack/`, numbered as `#NN` here. Emails are placeholders
(`ada@example.test`, `olu@example.test`, `grace@example.test`); tokens and cookies are masked by the
script. Section 7 says what was not exercised live.

## 1. What it is, in the frontend's terms

A person who creates an account must tick a second box, under the Terms box:

> I understand that I'm responsible for telling the people in my meetings that Beyric Note Taker will
> record and transcribe them, and for getting their consent wherever the law requires it.

Under it a link, **How to tell attendees**, that shows:

> One line in the invite or at the start of the call is enough, for example: 'I'm using Beyric to
> record this call and take notes.'

These words are the owner's, approved 2026-11-14. Do not reword them. `RECORDING_ACK_VERSION =
"2026-11-14"` (`app/signup.py`) is the version the server stores; change both together.

**Not designed by the designer** (their files have no such box): it reuses the terms checkbox
component and the app's link button, marked lead-designed in the code and the PR.

The box is **never pre-ticked**. The server enforces it as well as the form.

| Where | What the frontend does |
|---|---|
| `/signup`, password | second checkbox, no pre-tick; `accept_recording: true` in the body (section 2) |
| `/signup?invite=` for a team (the "Join your team" screen) | the same box; same body |
| `/signup`, "Continue with Google" | the button stays inert until BOTH boxes are ticked; the start address carries `accept_recording=true` (section 3) |
| `/login?error=recording_ack_required` | a message and the "Go to sign-up" link (section 3) |
| Meetings page, account older than the box | a one-time card with an **I understand** button (section 4) |

## 2. Password sign-up: `POST /api/signup` (public)

New field in the body: **`accept_recording`** (boolean). The request that works (#05; the password is
masked by the script):

```json
{ "name": "Ada Example", "email": "ada@example.test", "password": "<password>", "accept_terms": true, "accept_recording": true }
```

Answer, 202 (#05):

```json
{ "status": "check_your_email" }
```

Refusals, all 422 with the usual error shape:

| Body sent | Answer | Capture |
|---|---|---|
| `accept_recording: false` | `{"error": {"code": "recording_ack_required", "message": "Confirm that you will tell the people in your meetings that Beyric will record them."}}` | #02 |
| `accept_recording` **left out** | the same `recording_ack_required` answer, byte for byte. It is NOT `validation_error` | #03 |
| `accept_terms: false` and `accept_recording: false` | `{"error": {"code": "terms_required", "message": "Accept the Terms and the Privacy Policy to create an account."}}`: the terms are asked for first | #04 |

**Field-nesting trap:** nothing nests. `accept_recording` sits next to `accept_terms`, flat.

**UX instructions.**

- Show the `recording_ack_required` message **under the recording box** (the `Checkbox` component's
  `error` prop), not as a page-level alert. The frontend also checks before calling, so the server
  answer is the fallback for a modified client.
- The refusal is the same for an address that has an account and one that does not (the check runs
  before any lookup), so it leaks nothing. Do not add anything address-specific.
- A refused sign-up creates no account (database read after #02 to #04: no row).

What the server stores on a successful sign-up (read from the `users` row, #06; the time is masked by
the script):

```json
{ "recording_ack_at": "<a time>", "recording_ack_version": "2026-11-14" }
```

## 3. Google sign-up

The consent box lives on `/signup`, before the Google button, exactly as the Terms box does; Google's
own page cannot show it. The button passes both ticks to the start route:

`GET /api/auth/google/start?accept_terms=true&accept_recording=true&return_to=/`

`accept_recording` is a new query parameter (default false). Like `accept_terms`, it only matters for a
**new** account.

What the callback does (the callback is always a 302, never JSON):

| Start sent | Callback answers | Capture |
|---|---|---|
| `accept_terms=true` only (new Google user) | `302` to `/login?error=recording_ack_required`; no account is made | #11 |
| neither | `302` to `/login?error=terms_required` (terms first) | #12 |
| both | `302` to `return_to`, a session cookie, and an account that holds the time and version | #13, #14 |
| both or neither, **existing** Google account | `302` to `/`, signed in: an existing account needs neither box and is not marked as having ticked it | #17 |

The `/login` page already shows messages for `?error=<code>`; the new code needs:

> To create an account with Google, tick the box about telling the people in your meetings that
> Beyric records them. Start on the sign-up page.

with the **Go to sign-up** link (as for `terms_required`). Those words are ours, not the owner's.

Read from the `users` row after the Google sign-up (#14):

```json
{ "recording_ack_at": "<a time>", "recording_ack_version": "2026-11-14" }
```

**Sign-in with Google on `/login`** is a plain link with no boxes. For someone with no account it ends
in `terms_required` (the terms are checked first), which already sends them to `/signup`.

## 4. Accounts older than the box: `recording_acknowledged` and the one-time card

**New field on `GET /api/session`** (and on the login answer, which has the same shape):
`user.recording_acknowledged` (boolean, always present for a logged-in user). Same nesting, same level
as `email_verified`:

An account from before the box (#20, the login answer):

```json
{
  "authenticated": true,
  "csrf_token": "<csrf-H>",
  "user": { "email": "olu@example.test", "name": "Olu Example", "is_admin": false, "email_verified": true, "time_zone": null, "recording_acknowledged": false },
  "usage": { "used_seconds": 0, "cap_seconds": 18000 },
  "unread_notifications": 0,
  "badges": { "review_open": 0, "tasks_new": 0 }
}
```

An account that ticked the box at sign-up (#09, the login answer) has `"recording_acknowledged": true`.
A logged-out visitor (#01, #08) has `"user": null`, so there is no flag to read.

**The card.** On the Meetings page, when `user.recording_acknowledged` is `false`, draw a card with the
same words as the box, the **How to tell attendees** link, and an **I understand** button. Place it
above the new-meeting form. **It never blocks anything**: the form, the calendar, auto-join and the bot
all work while it shows (#22: a meeting is created and the bot is sent while the flag is `false`):

```json
{ "url": "https://meet.google.com/abc-defg-hij", "template_id": 14 }
```
answered `201` with the meeting (`"status": "bot_sent"`, truncated here; #22).

`POST /api/settings/recording-ack` (logged in, needs `X-CSRF-Token`, **no body**), the button's call:

```json
{ "recording_acknowledged": true }
```

(#24, status 200.) The server stores the time and the version, the first time only. After it,
`GET /api/session` says `"recording_acknowledged": true` (#26) and the card is gone for good. A second
call (a double click) answers the same 200 and changes nothing (#27); backend test
`tests/test_recording_ack.py::test_the_card_button_stores_the_time_and_the_version_once` proves the
first time is the one kept.

| Case | Answer | Capture |
|---|---|---|
| no `X-CSRF-Token` | `403 {"error": {"code": "csrf_rejected", "message": "Missing or wrong X-CSRF-Token header. Read it from GET /api/session."}}` | #23 |
| logged out | `401 {"error": {"code": "not_authenticated", "message": "Log in first."}}` | #29 |

**UX instructions.**

- If the call fails, keep the card, show the error under the text, and leave the button usable. Do not
  hide the card on failure: the next page load asks again.
- The card is for **existing** accounts only. A sign-up that ticked the box never sees it.
- Do not gate any action on the flag. This was decided deliberately: gating the pilot's existing users
  could silently stop their meetings.

## 5. State

There is no polling and no state machine: `recording_acknowledged` goes `false` → `true` once, for an
older account, and never back. Re-read it from `GET /api/session` on the next page load.

## 6. Changes to other guides

`docs/fe-integration-guide-pilot-accounts.md` (sections 4, 9 and the traps list) and
`docs/fe-integration-guide-team.md` (the invitation sign-up bodies) now point here: every
`POST /api/signup` body needs `accept_recording: true`, and the Google start needs
`accept_recording=true`. The older captures in `docs/fe-captures/pilot-accounts/` and `team/` were made
before the field existed and were **not** re-made; their request bodies lack it.

## 7. Verification table

| Behaviour | Verified live? |
|---|---|
| Sign-up without the box, with it left out, with terms also missing: the three answers above | Yes: #02, #03, #04 |
| Sign-up with the box stores the time and version; refused sign-ups leave no row | Yes: #05, #06, and a row check printed by the script after #04 |
| Google new account without the box is turned back; with both it is created; existing account signs in | Yes: #11, #12, #13, #14, #17. **Google's own servers are a stand-in** (the consent screen and the token exchange; the redirect URL, the flow cookie and the callback are the app's real ones) |
| `recording_acknowledged` false for an older account, true after sign-up | Yes: #20, #09 |
| The card's route: success, repeat, no CSRF, logged out | Yes: #24, #27, #23, #29 |
| Nothing is blocked while the flag is false | Yes: #22 (a meeting and a bot send; the bot provider is a fake that takes the link) |
| "The first click's time is kept on a repeat" | Backend test only; the capture masks the time |
| The old Google flow cookie `<state>.t` (a flow started before the deploy) | Backend test only (`test_a_flow_started_before_the_box_existed_keeps_its_terms_but_has_no_box`): that person is told `recording_ack_required` and starts again |
| The `/login` message for `recording_ack_required` and the sign-up page in a real browser | Looked at in the in-app browser against the repo's mock API (`web/scripts/mock-api.mjs`), not against the real backend; the real backend's redirect is #11 |
