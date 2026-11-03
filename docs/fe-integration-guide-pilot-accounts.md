# Frontend integration guide: pilot accounts (sign-up, verification, reset, waitlist, Google, admin, notifications)

Everything below is copied from responses captured against the running app by
`scripts/capture_pilot_api.py` (real app, real migrations, real database, real HTTP). The files are
in `docs/fe-captures/pilot-accounts/NN-name.json`; `NN` is cited as **#NN**. Run the script again to
re-derive any claim. Where something could **not** be exercised live, the text says so inline and the
last section lists it. Emails, names and passwords in the captures are placeholders
(`ada@example.test` and so on); tokens are masked `<token-N>`.

Read this together with `docs/fe-integration-guide-owner-app.md` (session cookie, CSRF, error shape,
meetings, templates, vocabulary): all of it still holds, plus the changes in section 11.

Scope: the **API**. The screens (sign-up, forgot password, confirm email, pilot full, waitlist, the
bell, the admin screens) are stream D of Module 2b and are not built yet; this guide is what they are
built against. The pages that the emailed links open belong to the frontend and **must exist before
sign-up opens** (section 3).

## 0. How every call works (unchanged, restated for the new routes)

- Same origin, `/api/...`, JSON, the `session` cookie. Call `GET /api/session` first: it sets the
  cookie and returns `csrf_token`.
- Every call that is not a GET sends `X-CSRF-Token: <csrf_token>`, **public routes too** (sign-up,
  forgot password and the waitlist included). Without it: 403 `csrf_rejected` (#17).
- Errors are always `{"error": {"code": "...", "message": "..."}}`; validation errors add `fields`.
  Map on `code`, show `message`.
- Logging in or out replaces the CSRF token; use the one in the newest response.

## 1. What this is, in the frontend's terms

A visitor can create an account (password or Google), confirm their email, and sign in. The pilot has
at most N places (the admin sets N). When it is **full**, the visitor sees "pilot full" and can join a
**waitlist**; the admin invites people from the list, which reserves a place for them for 7 days.
Everything else (reset a forgotten password, a bell of notifications, the admin's screens) hangs off
that. **Sign-up is closed** (`state: "closed"`) until the admin opens it; nothing in a deploy opens it.

## 2. The pilot's state: `GET /api/pilot` (public)

Call it when the sign-up page loads and whenever you show the sign-up or Google button.

```json
{ "state": "open", "prefill_email": null, "google_signin": true }
```
(#11). `state` is `open`, `full` or `closed`; nothing else about the pilot is public (no counts).

| Field | Meaning |
|---|---|
| `state` | `closed`: the admin has not opened sign-up (#4). `full`: the cap is reached (#57). `open` (#11) |
| `google_signin` | Show "Continue with Google" only when `true`. `false` when the server has no Google client configured (#2) |
| `prefill_email` | The Google address of a visitor who was just turned away by `pilot_full` (a 15-minute cookie); pre-fill the waitlist form with it. `null` otherwise (#122) |

`closed` and `full` are different screens: *closed* says sign-up is not open yet, *full* says the
pilot is full. Both offer the waitlist.

**A place is taken when an account is confirmed, not when it signs up.** Accounts that have not
confirmed their email hold no place, so `open` can still be `open` with several unconfirmed accounts
(#53). Consequence for the frontend: someone can sign up while there is room and find the pilot full
when they confirm (section 5, `pilot_full` on the verify call).

## 3. Pages the emailed links open (the frontend must build these)

Links are built from the server's `PUBLIC_BASE_URL` (in the captures `https://notes.example.test`;
in production `https://notes.beyrictech.com`). The worker would send these (the `mail_outbox` rows,
#20, #32, #41, #79):

| Mail | Link | The page must |
|---|---|---|
| Verify email | `…/verify-email?token=<token>` | read `token` from the query; show a **password field** and a button; on click `POST /api/verify-email` (section 5). **A GET must never verify**: mail scanners open links |
| Reset password | `…/reset-password?token=<token>` | read `token`; show a new-password field; `POST /api/password/reset` (section 7) |
| Waitlist invite | `…/signup?invite=<token>` | show sign-up with the email field locked to the invited address (the invite works for that address only); send the token as `invite_token` (section 4) |
| Already registered | `…/login` | the ordinary sign-in page |

Also needed: `/pilot-full` (where the Google callback sends a turned-away visitor, section 9) and
`/login?error=<code>` showing the codes in section 9.

## 4. Sign-up: `POST /api/signup` (public)

Request (#18):

```json
{ "name": "Ada Example", "email": "ada@example.test", "password": "<password>", "accept_terms": true }
```
`invite_token` (string) is optional. The body is strict: an unknown field is a 422 (#16), a missing one
too (#15), and `accept_terms` must be the boolean `true`.

**Response for a new address, an address that already has an account, and a repeat within a minute is
identical: 202** (#18, #19, #31):

```json
{ "status": "check_your_email" }
```

**UX rule: sign-up never says whether an address is registered. Do not add that.** Always show "Check
your email". Do not promise the mail arrives instantly, and do not offer a second click to resend for
60 seconds (see the silent drop below).

What the server did, which you cannot see: a new address got an unverified account and a verification
mail; an unverified address got a **new** verification mail (the form can be resubmitted); a verified
address got an "already registered" mail (#32); a repeat for the same address within a minute did
**nothing** (#19, only one mail was queued: the script asserts it).

Errors (all independent of whether the address exists):

| Status | `code` | `message` (exact) | Show |
|---|---|---|---|
| 403 | `pilot_closed` | Sign-up is not open yet. Check back soon or join the waiting list. | the closed screen + waitlist (#5) |
| 409 | `pilot_full` | The pilot is full. Leave your email and we will write when a place opens. | the pilot-full screen + waitlist (#59) |
| 403 | `password_signup_disabled` | Creating an account with a password is not available yet. Use Google to sign up. | **not captured live** (the capture runs with password sign-up on); message from `app/signup.py`, tested. Also the answer when **no mail provider is configured** (nothing could send the link). Hide the password form when you know it is off |
| 400 | `invite_invalid` | That invitation is not valid any more. Join the waiting list to get a new one. | #82, #84 |
| 422 | `password_too_weak` | That password is too common. Choose one that is harder to guess. | #12. Other messages of this code: `Use at least 8 characters.`, `Use at most 128 characters.` (the first two are **not captured live**, from `app/passwords.py`) |
| 422 | `terms_required` | Accept the Terms and the Privacy Policy to create an account. | #13 |
| 422 | `invalid_email` | Enter a valid email address. | #14. ASCII addresses only for now; an address with accented letters is refused |
| 422 | `invalid_name` | Enter your name (up to 200 characters). | **not captured live**; tested |
| 422 | `validation_error` | Some fields are missing or not valid. (+ `fields`) | #15, #16 |
| 429 | `too_many_requests` | Too many attempts. Try again later. | #124: the 6th sign-up call in an hour from one address. Also when 100 unconfirmed password accounts already exist and none is older than an hour (the same answer for every address) |
| 429 | `server_busy` | The server is busy. Try again in a moment. | **not captured live**; tested |
| 403 | `csrf_rejected` | Missing or wrong X-CSRF-Token header. Read it from GET /api/session. | #17 |

Passwords: 8 to 128 characters, not on a list of common passwords, no other rules.

**Terms:** `accept_terms: true` is stored with the version accepted (the "Last updated" date of the
Terms and Privacy pages). Show the checkbox beside links to both pages.

**Nginx** sits in front: more than about 11 calls to the account routes in a moment from one address
get a 429 from nginx itself, **whose body is not our JSON** (nginx's own page). **Not captured through
the app** (the limits were proven with a throwaway nginx, `deploy/nginx-limit-check.sh`, status codes
only): handle a 429 whose body is not JSON as "too many attempts".

## 5. Confirming the email: `POST /api/verify-email` (public)

```json
{ "token": "<token-1>", "password": "<password>" }
```
(#24) answers `200 {"status": "verified"}`. **It does not sign the user in** (#26: the session is still
anonymous); send them to the sign-in page. The token is the one in the link; `password` is **the
password they chose at sign-up**, typed again. That is on purpose: a link confirms only the password
of the sign-up that produced it, so nobody can register someone else's address with a password they
know and wait for it to be confirmed.

| Status | `code` | `message` | Meaning and what to do |
|---|---|---|---|
| 400 | `password_mismatch` | That is not the password you chose when you signed up. If you do not remember it, create the account again. | The link stays usable (#22). After **5** wrong passwords the link is dead (next row; not captured live, tested) |
| 400 | `token_invalid_or_expired` | This link has expired or was already used. Ask for a new one. | Wrong, used (#25), expired (24 h; **not captured live**, tested) or dead after 5 wrong passwords. Offer "Send a new link" (section 6) |
| 409 | `pilot_full` | The pilot filled up before this account was confirmed. Join the waiting list and we will write when a place opens. | #58: the link is **not** used up and works when a place opens (the pilot is `full` only because other people confirmed first). Show the pilot-full screen with the waitlist form. An account made from an invitation, or whose own invitation is live, confirms even when the pilot is full (the invitation is redeemed by confirming) |
| 422 | `validation_error` | … | missing `token` or `password` |

If the person signed up twice (say a typo the first time) both links exist and each works only with
its own password. Say "Use the password you chose when you signed up" on the page.

**Signing in before confirming** (#21): a correct password for an unconfirmed account is
`401 invalid_credentials` — "That email or password is not right." — **the same answer as a wrong
password or an unknown email**, and no session. This is deliberate: a different answer ("confirm your
email") would let anyone find out whether an address has a confirmed account (sign up with a chosen
password, then sign in with it). **There is no `email_not_verified` code any more.** The "Confirm your
email" screen (with the Resend button) is shown **after sign-up**, not as an answer to sign-in; on a
sign-in error add a line such as "If you have just signed up, confirm your email first (check your
inbox)".

## 6. Resend the verification mail: `POST /api/signup/resend` (public)

```json
{ "email": "ben@example.test" }
```
Answers `202 {"status": "check_your_email"}` **always**: an unconfirmed address (#35), the same address
again within a minute (#36), an unknown one (#37), a malformed one (#38), an already confirmed one.
It cannot tell you whether a mail was sent. Limits: 1 per minute per email and 5 per hour per client
address; over the limit the call is still 202 and does nothing. The new link confirms **the same
password as the newest earlier link** (resend never takes a new password), so if the person has
forgotten what they typed, send them back to sign-up.

## 7. Forgotten password: `POST /api/password/forgot` and `POST /api/password/reset`

`forgot` takes `{"email": "..."}` and answers `202 {"status": "check_your_email"}` for a known
address (#39) and an unknown one (#40) alike. Show: "If that email has an account, we sent a link."
A mail is sent only for a **confirmed, active** account (an unconfirmed one should use Resend; a
suspended one gets nothing). Same limits as resend.

`reset` takes `{"token": "<token-2>", "password": "<password>"}`:

| Status | Body | Meaning |
|---|---|---|
| 200 | `{"status": "password_changed"}` (#44) | Done. **Every session of that user is signed out** (#46) and the response does **not** sign anyone in: send them to sign in |
| 422 | `password_too_weak` (#42) | The link is not used up; let them try again |
| 400 | `token_invalid_or_expired` (#43, #45) | Used, wrong, or older than 30 minutes (**30 minutes not captured live**, tested) |

An account that signed up with Google only (no password) can use this flow to **add** a password.

## 8. The waitlist: `POST /api/waitlist` (public)

```json
{ "email": "wendy@example.test", "name": "Wendy Example" }
```
`name` is optional. Answers `202 {"status": "on_the_list"}` (#49) **whether or not the address is
already listed or has an account** (#50). A repeat changes nothing. 422 `invalid_email` for a malformed
address (#51). Per-address and per-email limits are silent (still 202). The admin invites from the
list (section 12); the person then gets the invite mail (section 3). Someone the admin removed stays
removed if they ask again.

## 9. Sign in with Google (redirects, not JSON)

Show the button only when `GET /api/pilot` says `google_signin: true`.

1. **Navigate the browser** (a full page load, not `fetch`) to
   `/api/auth/google/start?accept_terms=true&return_to=/meetings`. `accept_terms=true` must be sent
   when the person ticked the terms checkbox next to the button; without it an **existing** account
   still signs in but a **new** one is refused (`terms_required`). `return_to` is a path on this site;
   anything else becomes `/`. The answer is a **302** to Google's consent screen, which asks for
   `openid email profile` only (#108). It also sets a short `google_flow` cookie.
2. Google sends the browser to `/api/auth/google/callback`, which **always redirects**:

| Where the browser lands | When | Capture |
|---|---|---|
| the stored `return_to` (default `/`), with a **new session cookie** | success: an existing account, a linked account, or a new account (created already confirmed, with no password) | #116, #117 |
| `/login?error=google_failed` | anything wrong: unknown or reused state, expired flow, the flow finished in another browser, a bad token | #110, #114 |
| `/login?error=google_denied` | the person pressed Cancel at Google | #115 |
| `/login?error=terms_required` | a new account and `accept_terms` was not sent | #113 |
| `/login?error=pilot_closed` | a new account while the pilot is closed | #111 |
| `/login?error=account_suspended` | the account is suspended | **not captured live**; tested |
| `/login?error=email_unsupported` | Google's address has non-ASCII letters (not supported yet) | **not captured live**; tested |
| `/pilot-full` | a new account and the pilot is full. **No address is in the URL.** A 15-minute cookie is set; `GET /api/pilot` then returns it as `prefill_email` (#121, #122) | #121 |

Matching, in short: an account already linked to that Google account signs in; otherwise an account
with the same email gets the Google account linked (**an unconfirmed password account is confirmed and
its password is removed**, so a planted password is gone); otherwise a new account is made. A person who
already has an account is never blocked by `pilot_closed` or `pilot_full`.

When the Google client is not configured the routes answer 404 `google_signin_unavailable` — "Sign in
with Google is not available." (#3).

**Not exercised live:** Google itself. In the capture the consent screen and the code exchange are
replaced by a stand-in that returns a chosen identity (`StandInGoogle`); the consent URL in #108 is the
real one the app builds, and the redirects and cookies are the real ones. **The real round trip
(consent screen, Google's token endpoint, the ID token) has not been run; do that once with a real
Google test account before relying on it.**

## 10. Notifications (the bell)

`GET /api/session` now carries `unread_notifications` (section 11).

`GET /api/notifications?limit=50&before_id=` (#100, #101): newest first.

```json
{
  "notifications": [
    { "id": 1, "kind": "account.reset_link_issued", "severity": "warning",
      "title": "A password reset link was made for your account",
      "body": "An administrator made a one-time password reset link for your account. If you did not ask for this, tell us at once.",
      "meeting_id": null, "created_at": "2026-11-03T14:02:58.563880Z", "read_at": null }
  ],
  "has_more": false, "next_before_id": null
}
```
`severity` is `info`, `warning` or `error`. Page with `before_id = next_before_id`; `limit` 1 to 100
(else 422 `validation_error`, #102).

- `POST /api/notifications/{id}/read` → the notification with `read_at` set (#103); repeating it keeps
  the first time (#104). Someone else's id: **404** `not_found` — "No such notification" (#105).
- `POST /api/notifications/read-all` → `{"unread_notifications": 0}` (#106).

Kinds that exist today: `usage.hours_low` (written by the app at 80 % of the hours) and
`account.reset_link_issued` (the admin made a reset link for this user: show it prominently). In
#100 ids 2 and 3 were **inserted by SQL** in the shape `usage.hours_low` takes; id 1 is the real notice.

## 11. Changes to routes you already use

**`GET` and `POST /api/session`** gain a top-level field (#1, #7, #29):

```json
{ "authenticated": true, "csrf_token": "<csrf-E>",
  "user": { "email": "ada@example.test", "name": "Ada Example", "is_admin": false, "email_verified": true },
  "usage": { "used_seconds": 0, "cap_seconds": 18000 },
  "unread_notifications": 0 }
```
`unread_notifications` is `null` while logged out (#1) and a number when logged in. **Trap:** it sits
next to `user` and `usage`, not inside either.

**`POST /api/meetings`** has a new refusal: **409 `bot_busy`** — "The note taker is busy right now. Try
again in a few minutes." (#98). It means the note taker is on someone else's meeting and says nothing
about whose. **Trap:** `bot_already_active` — "A bot is already on a meeting. Wait for it to finish
first." (#94) now means **the caller's own** meeting is running; it no longer fires because of anyone
else. Show both messages as they come.

## 12. The admin API (`/api/admin/...`)

Every route needs a signed-in **admin**: a visitor gets 401 `not_authenticated` — "Log in first."
(#74), an ordinary user 403 `forbidden` — "This is for the administrator only." (#73). There is no
route to read another user's meetings, notes or calendar.

**Users.** `GET /api/admin/users?limit=&before_id=` (#60, #61), newest first, deleted users not listed:

```json
{ "users": [ { "id": 5, "email": "late@example.test", "name": "Late Example", "is_admin": false,
    "status": "active", "email_verified": false, "has_password": true, "has_google": false,
    "created_at": "2026-11-03T14:02:57.748030Z", "last_login_at": null, "used_seconds": 0,
    "cap_seconds": 18000, "hours_cap_override": null } ],
  "has_more": true, "next_before_id": 5 }
```
`cap_seconds` is what the user may record (override, else default; `null` = no cap, an admin);
`hours_cap_override` is the admin's per-user number alone. No hash or Google id is ever returned.

`PATCH /api/admin/users/{id}` with `{"status": "suspended"|"active"}` and/or `{"hours_cap_seconds":
n|null}` (#64 to #66, #72): returns the same user object. Only the fields sent change; `null` clears
the override. An empty body is 422 `validation_error` — "Send `status` or `hours_cap_seconds`." (#67);
`is_admin` or any other field is 422 (#68). Suspending signs the user out **for good** (reactivating
does not revive old sessions). An unknown or deleted id is 404 `not_found` (#71).

`DELETE /api/admin/users/{id}` → `202 {"status": "deleting"}` (#90). The user is out and their place is
free at once (#91, #92: a deleted account looks unknown); the **worker** removes their data afterwards
(not run in the capture).

**Last admin:** suspending or deleting the only active admin is 409 `last_admin` — "This is the only
active admin account, so it cannot be suspended or deleted." (#69, #70).

`POST /api/admin/users/{id}/reset-link` → `{"reset_url": "…/reset-password?token=<token-3>"}` (#75):
for when mail is not working. Shown once, never mailed, valid 30 minutes, replaces an earlier link,
and the user gets a `warning` notification. Refused for an **admin** account — 409 `admin_account`,
"A reset link cannot be made for an admin account." (#76) — and for an unconfirmed
(`user_not_verified`) or suspended (`account_suspended`) one (**the last two not captured live**,
tested).

**Pilot settings.** `GET /api/admin/pilot` (#8):

```json
{ "signups_open": false, "max_accounts": 10, "default_hours_cap_seconds": 18000,
  "password_signup_enabled": false, "slots_taken": 0, "state": "closed" }
```
`PUT /api/admin/pilot` takes the four settings (**all required**; `max_accounts` and
`default_hours_cap_seconds` may be `null` for no limit) and returns the same object (#10, #77). A bad
number is 422 with `fields` (#9). `slots_taken` counts confirmed accounts that are not admins (suspended ones too), accounts made
from an invitation that are not confirmed yet, **plus live invitations** (#80). A plain unconfirmed
account holds no place. `password_signup_enabled` stays
off until a real verification mail has been delivered once.

**Waitlist.** `GET /api/admin/waitlist?limit=&before_id=` (#62): oldest first, without the removed;
`before_id` means "continue after this id" (`next_before_id`).

`POST /api/admin/waitlist/{id}/invite` reserves a place for 7 days:
- mail configured → `{"emailed": true, "invite_url": null, "expires_at": "2026-11-10T14:02:58.644188Z"}` (#78);
  the entry becomes `invited` and the person is mailed;
- **no mail provider** → `{"emailed": false, "invite_url": "https://notes.example.test/signup?invite=<token-8>", "expires_at": "2026-11-10T14:02:59.007071Z"}`
  (#87): the admin sends the link himself;
- no free place → 409 `pilot_full` — "There is no free place to reserve. Raise the limit first." (#63);
- someone who already joined or was removed → 409 `waitlist_entry_closed` (**not captured live**, tested).

Inviting again replaces the earlier link and holds one place. If the invited person already has an **unconfirmed** account, they can confirm it with their original link even when the pilot is full (the invitation's place is theirs), or sign up again with `invite_token`, or sign in with Google: each redeems the invitation for that account and the entry becomes `joined`. The invited address signs up with
`invite_token` (#83) even while the pilot is `full` or `closed`; for any other address it is 400
`invite_invalid` (#82) and it works once (#84); the entry then shows `joined` (#85).
`DELETE /api/admin/waitlist/{id}` → 204: removes the person and frees any reserved place (#88).

**Failed mail.** `GET /api/admin/mail-failures` → `{"failures": []}` (#89; empty here because the worker
is not running in the capture). Each item is `{id, to_email, template, attempts, last_error,
created_at}`, never a link. **Item shape not captured live** (tested).

## 13. State machines and polling

- **Account:** `unverified` (created, cannot sign in: it gets the ordinary 401) → `active` (verified;
  a place is taken now) ⇄ `suspended` (cannot sign in; signed out at once) → deleted (looks unknown).
  An unverified account is **deleted automatically 48 hours after its last sign-up submission** (a
  resend does not extend it; the worker; not run live). It held no place, so nothing is freed, **except** for a person who came in with an invitation: their place was held, the sweep deletes the account and puts them back on the waitlist (`waiting`), and the place is free again.
- **Waitlist entry:** `waiting` → `invited` (a place is held 7 days) → `joined`; or `removed`.
- **Pilot:** `closed` ⇄ `open` ⇄ `full`, by the admin's settings and the number of places taken.
- **Polling:** the bell: read `unread_notifications` from `GET /api/session` (call it on page load and
  after your own actions); fetch the list only when the bell is opened. The admin lists need no
  polling.

## 14. UX consequences the frontend must surface

1. **Signing in never says "confirm your email".** An unconfirmed account gets the ordinary "That email or password is not right." Put the "confirm your email" screen after sign-up.
2. **"Check your email" is the only answer to sign-up, resend and forgot-password.** Never say whether an address exists. Never say "we sent you a mail" as a fact; say "if…".
3. **The verify page asks for the password again.** Say why in one line ("the one you chose when you signed up"). Five wrong tries kill the link: after `token_invalid_or_expired` offer a new link.
4. **A place is taken when the account is confirmed.** Someone can sign up while there is room and get `pilot_full` (409) when they confirm: show the pilot-full screen and the waitlist form, and keep the link (it works when a place opens).
5. **Verification and reset do not sign the person in.** Send them to the sign-in page afterwards.
6. **A password reset signs the user out on every device.** Say so.
7. **A mail can be dropped silently** (1 per minute per address). Do not show a "resend" button as instantly usable; a 30 to 60 second wait is honest.
8. **A suspended account is signed out on its next request** (401 `not_authenticated`): handle a sudden 401 with the sign-in screen, not an error. Suspending also ends old sessions for good.
9. **`account.reset_link_issued` is a security notice**: show it in the bell with its `warning` severity. It means the administrator made a one-time reset link for this user at their request; whoever uses that link can sign in as them.
10. **Google sign-up needs the terms checkbox ticked first**, and the button must pass `accept_terms=true`.
11. **Admin screens must treat `emailed: false` as "copy this link and send it yourself"** and show the link once.

## 15. Verification table

| Behaviour | Status |
|---|---|
| Pilot states, closed/open/full, `google_signin`, `prefill_email` | **verified live** (#2, #4, #11, #57, #122) |
| Sign-up 202 for new, existing and repeat address; mails queued (`verify_email`, `already_registered`) | **verified live** (#18 to #20, #31, #32; the mail rows are read from `mail_outbox`) |
| Sign-up errors: closed, full, weak password, terms, bad email, missing/extra field, no CSRF, rate limit | **verified live** (#5, #12 to #17, #59, #124) |
| Sign-up errors: `password_signup_disabled` (also with no mail provider), `invalid_name`, `server_busy`, the cap of 100 unconfirmed accounts and its clearing of abandoned ones, password length messages | **not exercised live** (tested in `tests/test_signup.py`) |
| Verify email: ok, wrong password, unknown, used; not signed in; login before verifying is the ordinary 401 | **verified live** (#21 to #26) |
| A place is taken at confirmation: unconfirmed accounts leave the pilot `open` (#53); confirming after it filled up is 409 `pilot_full` and the link survives (#58) | **verified live** |
| Verify email: 24-hour expiry, 5-wrong-passwords lock | **not exercised live** (tested with injected expiry and repeated calls) |
| Resend and forgot: identical 202s; reset ok, weak, bad, used; sessions revoked | **verified live** (#35 to #48) |
| Reset 30-minute expiry | **not exercised live** (tested) |
| Waitlist join, repeat, bad email | **verified live** (#49 to #51) |
| Admin users list, patch (suspend, hours, empty, `is_admin`, last admin, unknown), delete, 403 and 401 | **verified live** (#60 to #74, #90 to #92) |
| Admin reset link (made, for an admin refused, notification) | **verified live** (#75, #76, #100); the `user_not_verified` and `account_suspended` refusals **not live** |
| Admin pilot read, write, validation; slots with a reservation | **verified live** (#8 to #10, #77, #80) |
| Admin invite (mailed, link returned without mail, full, signup with it, wrong address, used) | **verified live** (#63, #78, #82 to #87); `waitlist_entry_closed` **not live** |
| Admin mail-failures | **empty list verified live** (#89); item shape not live |
| `bot_busy` for someone else's meeting; `bot_already_active` for your own | **verified live** (#93, #94, #98) |
| Notifications: list, page, bad limit, read, repeat, foreign 404, read-all, unread count | **verified live** (#99 to #106) |
| Google routes: start redirect and cookie, callback success, denied, unknown state, no cookie, terms, closed, full, off | **verified live with Google's side stood in** (#3, #108 to #122) |
| Google: the real consent screen, token exchange and ID token; `account_suspended`, `email_unsupported` redirects | **not exercised live** |
| Mail delivery by Resend | **not exercised** (nothing is sent in the capture; the mail text is not captured, only the queued payload) |
| The worker: sending mail, purging deleted accounts, the 48-hour sweep, hourly sweeps | **not exercised live** (tested in `tests/test_mail.py`, `test_admin.py`, `test_maintenance.py`) |
| nginx limits (shared zone, waitlist zone, callback not counted) | **verified with a throwaway nginx** (`deploy/nginx-limit-check.sh`, status codes only), not through the app; the body of nginx's 429 is not JSON and was not captured |
