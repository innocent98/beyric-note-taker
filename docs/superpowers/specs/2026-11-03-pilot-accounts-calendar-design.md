# Pilot accounts and calendar auto-join: design

**Status:** ANSWERED 2026-11-03 by Adebayo: "all recommended" except the changes in the table directly below. Design only: no application code. Where a later section still says "ASSUMED, confirm (Qn)", the answer below wins.
**Evidence base:** `docs/HANDOFF-master.md` (sections 2, 3, 5, 6), `docs/checklist/master-checklist.md` (Modules 2 and 2b), the MVP design, the Next.js migration plan, the current code (`app/models.py`, `app/worker.py`, `app/web.py`, `app/auth.py`, `app/api.py`, `app/services.py`, `app/vexa.py`), the public-pages SOP, the designer's screens in `docs/ui/Beyric Note Taker redesign/`, and Google's own documentation (sources at the end).

How to read this: every choice I had to make myself is marked **`ASSUMED, confirm (Qn)`** and points at a question in the list below. Everything marked "decided" was settled by Adebayo earlier and is not reopened here. Nothing in this document was run against Google; every Google behaviour is quoted from documentation or marked unverified.

---

## 0a. Adebayo's answers (2026-11-03) — these override the table below

| Q | Answer | Consequence |
|---|---|---|
| all others | "All recommended" | Q1, Q3 to Q6 (mail), Q8 to Q10, Q12 to Q14, Q16, Q18, Q21, Q23 stand as written |
| Q2 join time | Dispatch the bot **1 minute before the start** (a bot needs about a minute to open Chrome and ask to join, so it arrives at about the start time; dispatching at the start would make it about a minute late and miss the opening). Re-check the event 2 minutes before; keep trying until 15 minutes after the start. Make the lead time a setting (`AUTOJOIN_LEAD_SECONDS`, default 60) and **measure** real join times in the spike before fixing the number | replaces "2 minutes" |
| Q6 mail | **Resend is ready**; email verification and password reset use it from day one | the "Google sign-in only until mail works" fallback is dropped; needs `RESEND_API_KEY` and the sending domain in the server `.env` (Adebayo) |
| Q7 Google expiry | **CORRECTED 2026-11-03 (Adebayo): only the app's BRANDING is verified, not the sensitive scopes.** The first reading ("app verified, no 7-day expiry") was wrong | The original Q7 recommendation applies: before testers connect a calendar, switch the Cloud project from Testing to **"In production"** (still unverified), so refresh tokens do not lapse after 7 days; testers see "Google hasn't verified this app" on the calendar consent screen (capped at 100 users, fine for 10). Sign-in with Google uses only `openid email profile`, so it shows no warning. Submit the sensitive-scope verification as soon as the calendar flow works (demo video, policy, justifications; up to about 10 days), with the final scope list fixed first (a new scope later means a new review). `GOOGLE_OAUTH_TESTING` stays on until the project is switched to production, then off |
| Calendar sources | **Added 2026-11-03 (Adebayo, "Yes, add ICS")**: Google OAuth stays primary; a pasted secret-ICS link is a second source for Outlook, Microsoft 365 and others, behind one calendar-source interface. No Microsoft OAuth app | Overrides "Outlook later" in Q4 and Q17 for the calendar itself; the policy and landing copy still must not claim Outlook until it ships. ICS needs no Google review. See `docs/spike/vexa-calendar-sync-ics.md` |
| Q11 instant-Meet | **Add the control** (a Settings switch, "Also join Google Meet calls I start myself", off until turned on) | designer to draw it; build after the spike |
| Q15 passwords | **Minimum 8 characters**, argon2id, plus a check against a list of common and breached passwords, no composition rules (NIST 800-63B). Security stays tight where it is cheap: rate limits, Google sign-in as the easy path | the design's "10 characters" recommendation is dropped. The stakes are not money but confidential meeting transcripts, so the blocklist and the limits stay |
| Q17 Teams | **Investigate and build it**: a spike with a real Teams meeting (Vexa supports Teams); if it works, Teams links join like Meet and Zoom, from a pasted link and from Google Calendar events that carry a Teams link. Outlook calendar stays later. The privacy policy and terms must then name Teams | replaces "Can't join yet" |
| Q19 deploy gate | Not clear to Adebayo; explained: auto-join starts bots with nobody pressing anything, so a deploy that restarts the stack at 09:59 could miss a 10:00 meeting. The gate makes the deploy refuse (or wait) when a join is due within 10 minutes. **Recommended: keep it** | stands |
| Q20 Workspace | `beyrictech.com` is **not** Google Workspace | Instant-Meet detection through the Workspace Events API may not work for Gmail accounts; the spike runs on a personal Gmail first, and the design needs a fallback if it fails (paste a link, or join calendar meetings only) |
| Q22 alerts | **Email via Resend** | no Telegram |
| Login rate limit | Change from 5 tries in 15 minutes to **5 tries in 5 minutes per address, plus 10 failures per hour per account** (the per-account cap stops guessing spread over many addresses) | see `docs/sop/` for the shipped change |
| Forwarded-for gap | **Close it now**, properly: an edge secret set by nginx that the app checks before trusting a forwarded address | in progress as its own PR |

## 0. Open questions for Adebayo (answer in one pass)

Each has my recommended answer. If you reply "all recommended", the spec stands as written.

| # | Question | Recommended answer | Why |
|---|---|---|---|
| Q1 | **Recording notice.** Does the bot say or post anything when it joins? | Keep the bot name "Beyric Note Taker (recording)" **and**, if Vexa can post to meeting chat (to be checked in a spike), post one chat line on joining: "Beyric Note Taker is recording and transcribing this meeting for {first name}. Ask them to remove the bot if you do not agree." Never speak aloud. Never write to the calendar event (that would need a write scope). | The name alone is easy to miss. A chat line costs nothing, needs no extra Google scope, and makes the privacy policy's "participants are told" claim stronger. See section 10 |
| Q2 | **How early does the bot join a calendar meeting?** | Send the bot **2 minutes before the start time**; re-check the event 3 minutes before; keep trying until 15 minutes after the start; give up after that and say so. | A bot takes about a minute to open Chrome and knock. Earlier means a slot (about 1.2 GB) sits idle and the bot waits alone in a lobby. See section 7.6 |
| Q3 | **Overlapping meetings and capacity.** Do we rent a second machine for bots? | **Yes, before more than one or two testers are invited.** Keep "one bot at a time" on the current box until measured; add a second small server that runs only Vexa Lite and register it in the dispatcher. Two users with meetings at the same hour is normal, and a refused bot is an unrecorded meeting. Join every meeting that has its toggle on; overlap is handled by capacity, not by skipping. | The current Vexa is capped at 3 GB, which is about one to two bots. See section 9 |
| Q4 | **Outlook in the first version?** | **Later.** Hide the "Connect Microsoft Outlook" button until it is built. Google first. | The privacy policy already says "Google Calendar only", and Microsoft needs its own app registration and review |
| Q5 | **Who is told when auto-join fails?** | The **account owner**, in the app (a banner plus a row on the Meetings page that says "Not recorded, and why"), and by **email as soon as a mail provider exists**. **You (admin)** are told about anything systemic (no capacity, a bot host down, Google errors for several users, a user at 5 hours) in the app and by email. | In-app only means nobody sees it while away from the screen. See section 11 |
| Q6 | **Mail provider.** | **Resend** (or Postmark): verify `beyrictech.com` with SPF and DKIM records. Until it works, open sign-up with **Google sign-in only** and issue password-reset links by hand from the admin screen. Password sign-up opens only once verification email is delivered. | Email verification is what stops junk accounts from taking the 10 slots. Google sign-in already proves the address, so it needs no mail. See section 4.3 |
| Q7 | **Bridge the 7-day expiry now.** | Switch the Google OAuth app to **"In production"** (still unverified) for the pilot, while the verification review runs. Testers see a "Google hasn't verified this app" screen and click through. | Google's page ties the seven-day expiry to apps in **Testing**; unverified apps in production are capped at 100 new users for the life of the project, plenty for 10 accounts. I could not find Google saying in so many words that production status removes the expiry, so this is `ASSUMED`. Test it in week one. `GOOGLE_OAUTH_TESTING` is switched **off the day the app is moved to production**, not when verification is approved. The 100-user cap counts people who see the unverified-app screen, which only the sensitive calendar and Meet scopes trigger; plain sign-in (`openid email profile`) does not (`ASSUMED`) |
| Q8 | **What happens to scheduled joins when the calendar disconnects?** | **Pause them** and show a red "Calendar disconnected: N meetings in the next 24 hours will not be recorded" banner. Alternative: keep joining already-synced meetings for 24 hours. | Safe and simple. The alternative records more, but it records moved or cancelled meetings blindly and after the user's connection is gone. See section 7.9 |
| Q9 | **Two pilot users in the same meeting.** | One bot only. The meeting belongs to whichever user's join fired first; the other sees "A Beyric note taker is already in this meeting." (neutral wording: it does not say who sent it). No sharing of notes in this version. | Two identically named bots in one call is a worse failure than the missing copy |
| Q10 | **Does the admin count toward the 10 accounts?** | **No.** Ten non-admin accounts plus you. | You asked for ten testers |
| Q11 | **Instant-Meet toggle.** | A separate switch in Settings, "Also join Google Meet calls I start myself", **off until the user turns it on** (turning it on is what asks Google for the Meet permission, and it stays on once granted), independent of the calendar rule (even "only when I turn it on"). It works without the Calendar permission. Needs one new control from the designer. | The calendar rules are about events; an instant meeting is not an event |
| Q12 | **Waitlist.** | A form (email, optional name). The admin sees the list and clicks "Invite" when a slot is free; with no mail provider he gets a copyable link. No automatic promotion. | You approve who gets a slot |
| Q13 | **Templates and vocabulary.** | Each user gets **their own copy** of the four default templates at sign-up and owns their vocabulary. No shared, editable templates. | One rule ("everything belongs to a user") and it matches how `meetings.template_id` works today |
| Q14 | **At capacity, wait or fail?** | A bounded, **visible** retry: try every 15 seconds until 15 minutes after the start (the same deadline as every join), show "Waiting for a free note taker" with the deadline, alert you at once, give the user a "Send now" button. After the deadline it becomes a failed row "Not recorded: all note takers were busy". Alternative: fail immediately. This is **not** a silent queue. | Late joining still records most of a meeting. See section 9.5 |
| Q15 | **Passwords.** | At least 10 characters (the design says 8), argon2id hashing, checked against a short list of the most common passwords. | Cheap, and the design's 8 is a minimum |
| Q16 | **How the 5 hours are measured.** | Seconds of **recorded audio** (what we transcribe and pay for), counted when the recording is processed. Checked when a join is dispatched; a meeting already running finishes even if it crosses 5 h. A "1 hour left" banner at 4 h. | Waiting-room time and failed joins cost nothing, so they should not count. See section 6 |
| Q17 | **Teams links on the calendar.** | Shown as "Can't join yet" until Teams support ships (Vexa supports it; about a day plus a test call, per the checklist). | The policy and the product both say Meet and Zoom |
| Q18 | **Which calendars are read?** | The user's **primary calendar only**. | Smallest reading that does the job. Secondary calendars can come later |
| Q19 | **Deploy gate.** | `deploy/deploy.sh` also refuses to deploy when a calendar join is due in the next 10 minutes (not just when a meeting is `bot_sent` or `processing`); an override flag exists for emergencies. | Auto-join makes "no meeting right now" much less common |
| Q20 | **Is `beyrictech.com` a Google Workspace domain, and which Google account runs the spike?** | Use **two accounts**: one personal @gmail.com and one Workspace account. | Whether instant-Meet detection works can depend on the account type (section 8). If your own account is Workspace, your own meet.new calls are likely covered even if testers' Gmail accounts are not |
| Q21 | **Legal read.** | A lawyer reads the privacy policy and terms **after** the wording in section 12 is merged, before inviting testers; ask them also whether Beyrichq LTD must register with the Nigeria Data Protection Commission as a data controller. | Not legal advice; I cannot answer the registration question |
| Q22 | **Admin alerts while you are away.** | Email to `ADMIN_ALERT_EMAIL` (defaults to your sign-in email) is enough once Resend exists. If you want a phone push before that, a Telegram bot message is the cheapest (no domain setup). Say if you want it. | Capacity alerts are time-critical |
| Q23 | **Small assumptions** (all listed in section 21): a `tentative` or unanswered invite is joined; a switch on a recurring event applies to that one occurrence; a meeting moved after the bot was sent is ignored; the hours-cap message points to `hello@beyrictech.com` (a mailbox that may not exist yet); unverified accounts are deleted after 48 hours; notifications are kept 90 days. | Accept all | Defaults that cost nothing to change later |

---

## 1. Goals and non-goals

### Goals

1. Anyone can sign up (until 10 accounts exist); everyone else sees "pilot full" and can join a waitlist.
2. Every account owns its meetings, templates, vocabulary and calendar connection. One user can never see or change another's data.
3. 5 recorded hours per account for the whole pilot (no reset); admin uncapped; both caps removable later by a setting.
4. A user connects Google Calendar once. The note taker then joins every calendar meeting that has a Meet or Zoom link, following the user's rule (all with a link / only ones I organise / only when I turn it on), a per-meeting switch, and a template for calendar meetings.
5. The note taker also joins every Google Meet the user starts as host (scheduled, instant, meet.new, with or without a calendar event), and never joins meetings that are not on their calendar and that they did not start.
6. **A refused bot is an unrecorded meeting.** A dispatcher puts each bot on a machine with free capacity. At capacity the owner is alerted at once; there is no silent queue.
7. The "calendar disconnected" state is clear and recoverable (Google test-mode authorisations expire after 7 days).
8. Pass Google's OAuth verification (public pages exist; calendar flow and demo video come from this work).
9. Privacy policy and terms keep claiming only what the app does.

### Non-goals (this version)

- Outlook, Teams, iCal and any non-Google calendar.
- Billing, paid plans, teams and workspaces, invitations of teammates, tasks and sprints (handoff section 6.5 comes after accounts).
- Sharing a meeting between users, notes by email, translation.
- Writing to the user's calendar (no events created, edited or annotated).
- Reading meeting content from Google (no Meet recordings, transcripts or chat; the bot records itself).
- A real queue of bots. Auto-scaling machines up and down.
- SSO for organisations, SAML, two-factor authentication (listed as follow-ups).

---

## 2. User stories

**Visitor**
- As a visitor I can create an account with email and password, or with Google, and I am told clearly if the pilot is full and can leave my email on a waitlist.

**Pilot user**
- I confirm my email (or sign in with Google) and land on my own empty Meetings page with my four default templates.
- I paste a link and a bot joins, exactly as today, within my hours.
- I connect Google Calendar, pick a rule and a template, and see my next meetings with a switch each: "Will join", "Off", or "Can't join" with the reason (no link, Teams, declined, cancelled).
- I turn on "also join Meet calls I start" and then start a meeting with meet.new; the bot appears within about a minute.
- I see how many of my 5 hours I have used. At 5 hours the bot stops joining, tells me why, and any meeting already running still finishes.
- If my Google connection lapses I see a red banner and a one-click reconnect, and I know which meetings will not be recorded.
- When a bot fails to join I see "Not recorded" with the reason and a "Send now" button if the meeting is still on.
- I can disconnect Google at any time and my calendar data is deleted.

**Admin (Adebayo)**
- I see users, their usage, the waitlist, the bot machines and their load, and the pilot settings (open/closed, number of accounts, hours per account).
- I invite someone from the waitlist, suspend or delete an account, raise one user's hours.
- I am alerted when a bot cannot be placed, a machine is down, or Google calls fail for several users.

---

## 3. What is already decided (not reopened)

Open sign-up capped at 10 accounts, the 11th sees "pilot full" plus a waitlist. 5 recorded hours per account for the whole pilot, no reset; at 5 h the bot stops joining and says why; a running meeting finishes; admin uncapped; both caps removable later. Auto-join joins every calendar meeting with a link and every Meet he starts as host; never meetings that are neither on the calendar nor started by him; Zoom links on the calendar are joined too. Rule choices, per-meeting toggle and calendar template from the designer. Start Google OAuth verification now. Entity: Beyrichq LTD, Lagos, Nigeria. Policy claims only what the app does. Never deploy while a meeting is `bot_sent` or `processing`. No bot queue at launch: a dispatcher places bots. Frontend: Next.js on the FastAPI JSON API (`/api`, session cookie, `X-CSRF-Token`).

---

## 4. Accounts

### 4.1 Today (what changes)

`app/auth.py` has one owner: a password in config (`owner_password`), a stateless signed cookie `{auth, csrf, iat, epoch}`, a global `app_state.session_epoch` that logout bumps, and a signing key derived from both `session_secret` and `owner_password`. Everything in the database belongs to nobody (no `user_id` anywhere).

### 4.2 New session model

- The signed cookie stays (stateless, HMAC, CSRF token inside; `HttpOnly`, `Secure`, **`SameSite=Lax`**, so the top-level redirect back from Google still carries it; 30 days absolute as today, no idle expiry). Its fields become `{uid, csrf, iat, epoch, gepoch}`: `auth: bool` becomes **`uid: int | null`**, `gepoch` is the global epoch, `epoch` the user's, and the epoch check becomes **two** checks: the global epoch (sign everyone out) and a new **per-user `users.session_epoch`** (logout and password change revoke that user only; today logout signs out everyone, which is wrong with several users).
- The signing key derives from `session_secret` only. Dropping `owner_password` from the key signs everyone out once at deploy time; that is fine and expected.
- Anonymous sessions stay (the login form needs a CSRF token), and logging in still issues a fresh cookie (no promotion of a pre-login cookie).
- `guard()` loads the user on each request (one indexed lookup, replacing the epoch lookup); a suspended or deleted user gets a 401.
- `request.state.user` is the only way any service function learns who is calling; every query takes the user (section 5.3).
- The existing owner becomes the **admin user**: migration `0006` creates one user from `OWNER_EMAIL` plus a hash of `OWNER_PASSWORD` (read once, at migration time) with `is_admin = true`, and everything existing is assigned to it (section 5.4). `OWNER_PASSWORD` can then be removed from `.env`.

### 4.3 Sign-up, sign-in, verification, reset

| Flow | Behaviour |
|---|---|
| **Password sign-up** | Name, email, password (design: `Auth.dc.html`, "Create account"). Under the account-cap lock (section 4.4) the app creates an unverified user, a verification token and a mail, and always answers 202 "Check your email", **also for an email that already has an account** (an unverified one gets a fresh verification mail; a verified one gets a "you already have an account" mail), so sign-up cannot be used to find out who is registered. Terms acceptance is recorded at this step (`terms_accepted_at`, `terms_version` = a constant holding the policy's "last updated" date). **An unverified user gets no session**: a correct password answers 403 `email_not_verified`, and the frontend shows the "Confirm your email" screen with a Resend button (`POST /api/signup/resend` with an email: 1 per minute per email, 5 per hour per address, always 202). Unverified accounts older than 48 hours are deleted by a sweep, which frees their slot. |
| **Google sign-in / sign-up** | Authorization-code flow with PKCE, scopes `openid email profile` only (non-sensitive). Verify the ID token (issuer, audience, expiry, nonce) and require `email_verified = true`. A Google user is created already verified, with no password and with terms accepted at the sign-up consent text. Matching order: (1) by `google_sub`; (2) by email: a **verified** password user gets the Google identity linked; an **unverified** password row with the same email is verified, linked, and its `password_hash` is **cleared** (someone may have pre-registered a victim's address to plant a password); (3) otherwise a new user. If the person's Google email later changes, the account stays matched by `google_sub` and the stored email is updated only if it does not collide with another user. |
| **Email verification** | Link with a random 32-byte token, **only the SHA-256 hash stored**, 24 hours, single use. `GET` of the link shows a button that `POST`s (so mail scanners that pre-fetch links do not verify the account). |
| **Password reset** | "Forgot password" always answers "If that email has an account, we sent a link" (no account enumeration). Token as above, **30 minutes** (the design says 30), single use, sets a new password, bumps that user's `session_epoch`. |
| **Sign-in** | Email plus password (or Google). Argon2id (`argon2-cffi`, parameters from its current recommended profile; `hashlib.scrypt` is the no-new-dependency alternative). Failed attempts are limited per client address (existing `LoginLimiter`) **and** per email (10 per 15 minutes) so one account cannot be brute-forced from many addresses; the accepted cost is that someone can lock a victim out for 15 minutes (password reset and Google sign-in still work). Both limiters stay in memory, which is correct for the single `web` replica that runs today (note it before ever adding a second replica). `ASSUMED, confirm (Q15)`: minimum 10 and **maximum 128 characters** (an argon2 cost cap on public endpoints), checked against a vendored list of the 10,000 most common passwords (`app/data/`). |
| **Sign-out** | Bumps this user's `session_epoch` only. |

**Mail.** No email exists yet. The design needs two kinds of mail, in this priority: (1) verification and password reset (accounts), (2) "Could not join" and admin alerts. The in-app channel always works; email is an added channel.

- A small `Mailer` interface with a `ConsoleMailer` (development and tests), and a `ResendMailer` (or Postmark; `ASSUMED, confirm (Q6)`). Mail is written to a `mail_outbox` table and sent by the scheduler with retries; **a failed mail never fails the action that caused it**, and the admin screen shows failures.
- Required DNS from Adebayo for `beyrictech.com`: SPF, DKIM and a DMARC record for the provider; a `no-reply@` sending address and the existing `privacy@`, `legal@`, `security@` mailboxes. The designer's `Emails.dc.html` already has Verify, Reset, Could-not-join and Notes-ready layouts.
- **Fallback if mail is not ready at launch (`ASSUMED, confirm (Q6)`):** Google sign-in only for open sign-up; existing users can sign in with a password; the admin generates a one-time reset link from the admin screen and sends it himself. Password sign-up is switched off by a setting until a verification mail has actually been delivered once.

### 4.4 The 10-account cap and the waitlist

- Cap lives in `app_state.max_accounts` (default 10, `NULL` = no cap) and `app_state.signups_open` (default **false**; the admin opens the pilot only after ownership scoping is deployed). The count is **non-admin accounts that are not deleted, including suspended and unverified ones** (a suspended account keeps its slot, so un-suspending can never exceed the cap; deleting frees it), **plus live invite reservations** (`ASSUMED, confirm (Q10)`: admin excluded).
- `GET /api/pilot` answers `open`, `full` or `closed`. `closed` makes sign-up fail with 403 `pilot_closed`; `full` with 409 `pilot_full`. A valid **invite token** (`invite_token` field on `POST /api/signup`, or carried through the Google flow) bypasses `closed` and `full` because it already holds a slot, but only for the email it was issued to.
- The Google callback never puts an email in a URL. On `pilot_full` it sets a 15-minute signed cookie holding the Google email and redirects to `/pilot-full`; `GET /api/pilot` returns that email as `prefill_email` for the waitlist form.
- **Race-free:** the sign-up transaction takes a Postgres advisory lock (a new constant next to the existing `NEW_MEETING_LOCK` pattern in `app/services.py`), counts, and inserts. Test: ten parallel sign-ups at nine accounts produce exactly one success.
- Full: sign-up (password or Google) answers HTTP 409 with code `pilot_full`; the frontend shows the "Pilot full" screen with the waitlist form, the Google email pre-filled. (Google sign-in that hits `pilot_full` must not leave a half-created user.)
- **Waitlist** (`waitlist_entries`): email (unique, lower-cased), optional name, created time, status `waiting | invited | joined | removed`. The form is public, rate-limited by address, and gives the same answer whether or not the email is already listed. The admin sees the list; "Invite" creates a single-use invite token that **reserves a slot** for that email for 7 days (so the invited person is not beaten to it) and either emails the link (if mail works) or shows it to copy. `ASSUMED, confirm (Q12)`.
- Closing an account frees its slot (hard delete or status `deleted`).

### 4.5 Admin

`users.is_admin` (set only by the migration and by a command-line script; never settable through the API). Admin-only API and screens (section 13): users and usage, per-user hours override, suspend/delete, waitlist, pilot settings, bot hosts and capacity, mail failures. Every admin endpoint is under `/api/admin/` and the route-enumeration test asserts each one rejects a non-admin with 403.

### 4.6 Account closing and deletion

v1 keeps the policy's "by email request" for closing an account (the SOP already says so), plus an in-app **Disconnect Google** that deletes calendar data immediately. Deletion is a **hard delete** done by one function used by both a script (`scripts/delete_user.py`) and the admin API (`DELETE /api/admin/users/{id}` sets `status = deleting` and the scheduler runs the function, so no extra queue is needed). It stops that user's running bots, revokes the Google token and deletes the Events subscription, then deletes meetings, audio (local and Vexa copy via the existing `delete_recording`), templates, vocabulary, tokens, calendar data and notifications. The usage ledger keeps seconds and `email_hash` only (no personal data), which is why re-signing up does not reset the hours. A **suspended** user cannot sign in, their sync stops, their scheduled joins become `skipped` (`account_suspended`), and meetings already running finish. An in-app "Delete my account" is a follow-up.

---

## 5. Data model and migrations

### 5.1 New and changed tables

Names follow the existing models (`app/models.py`, SQLAlchemy 2.0 `Mapped`). All times are `timestamptz`. All new ids are `bigint` (the bigint fix in PR #3 is the precedent).

**Accounts**

| Table | Columns (key ones) |
|---|---|
| `users` | `id`, `email` (unique on `lower(email)`), `name`, `password_hash` (null for Google-only), `google_sub` (unique, null), `email_verified_at`, `is_admin`, `status` (`active`/`suspended`/`deleted`), `hours_cap_seconds` (null = use default; admin override), `session_epoch`, `terms_accepted_at`, `terms_version`, `created_at`, `last_login_at` |
| `auth_tokens` | `id`, `user_id` (null for waitlist invites), `email`, `purpose` (`verify_email`/`reset_password`/`waitlist_invite`), `token_hash` (unique), `expires_at`, `used_at`, `created_at` |
| `oauth_flows` | `state_hash` (pk), `purpose` (`signin`/`calendar`/`meet_scope`), `user_id` (null for sign-in), `code_verifier`, `nonce`, `return_to`, `expires_at` (10 min). Short-lived, swept hourly |
| `waitlist_entries` | `id`, `email` (unique lower), `name`, `status`, `invited_at`, `invite_token_id`, `created_at` |
| `app_state` (existing single row) | adds `signups_open` (bool, **default false**: nobody can sign up until the admin opens the pilot, after ownership scoping is deployed), `max_accounts` (int, default 10, null = no cap), `default_hours_cap_seconds` (int, default 18000, null = no cap), `password_signup_enabled` (bool, default false), `scheduler_heartbeat_at` |

**Ownership of existing data** (`NOT NULL` after backfill)

| Table | Change |
|---|---|
| `meetings` | `owner_user_id` FK; `source` (`manual`/`calendar`/`instant_meet`); `title` (calendar title; null for pasted links; it is the user's own meeting content, so it stays after a calendar disconnect); `recorded_seconds` null. Later migrations add `bot_host_id` (Phase 4). There is **no** `scheduled_join_id` on `meetings`: the link goes one way, `scheduled_joins.meeting_id` |
| `templates` | `owner_user_id` FK; unique `(owner_user_id, name)` replaces unique `(name)` |
| `vocabulary_terms` | `owner_user_id` FK; unique `(owner_user_id, term)` replaces unique `(term)` |

**Usage and notices**

| Table | Columns |
|---|---|
| `usage_entries` | `id`, `user_id` (`ON DELETE SET NULL`), `email_hash` (SHA-256 of the lower-cased email, kept after the user is deleted so signing up again does not reset the 5 hours), `meeting_id` (unique, `ON DELETE SET NULL`), `seconds`, `counted_at`. Separate from `meetings` so deleting a meeting does not give hours back. Used hours = rows where `user_id` is the user **or** `email_hash` matches theirs |
| `notifications` | `id`, `user_id`, `kind`, `severity` (`info`/`warning`/`error`), `title`, `body`, `meeting_id` null, `dedupe_key`, `created_at`, `read_at`. Unique on `(user_id, dedupe_key)`; a repeating alert puts a time bucket in its key (for example `capacity:2026-11-03T10:00` for 30-minute buckets), so "de-duplicate per 30 minutes" is a key choice, not a second mechanism. **Admin alerts are ordinary rows for every admin user**, with `kind` starting `admin.`. Kept 90 days |
| `mail_outbox` | `id`, `to_email`, `template`, `payload` (jsonb; holds the link while pending, replaced by `{}` when sent, and also scrubbed 24 hours after a failure since the token has expired), `status` (`pending`/`sent`/`failed`), `attempts`, `last_error`, `created_at`, `sent_at`. 3 attempts, 1, 5 and 30 minutes apart; sent rows deleted after 30 days |

**Calendar and auto-join**

| Table | Columns |
|---|---|
| `calendar_connections` (it is the user's **Google connection**; the calendar and Meet features are each derived from `granted_scopes`, so Meet detection works without the Calendar scope) | `id`, `user_id` (unique per `provider`), `provider` (`google`), `google_account_email`, `google_sub`, `google_hosted_domain` (the ID token's `hd` claim: present for Workspace accounts, absent for personal Gmail; used to pick the instant-Meet mode), `status` (`connected`/`degraded` (the 30-minute-of-errors warning)/`needs_reconnect`/`disconnected`), `needs_reconnect_reason` (`expired`/`revoked`/`error`), `granted_scopes` (text), **`refresh_token_enc`** (bytea), `key_version`, `connected_at`, `last_sync_at`, `last_error`, `last_error_at`, `sync_token`, `expected_expiry_at` (set to `connected_at + 7 days` only while the OAuth app is in Testing), `meet_subscription_name`, `meet_subscription_expires_at`, `meet_subscription_state` (`none`/`active`/`needs_reconnect`/`error`; a separate flag from `status`, because Meet can fail while the calendar is fine). Access tokens are **not stored** (section 12.1). A new `prompt=consent` grant replaces the stored refresh token (encrypting the new one); if Google returns none, the old one is kept |
| `user_settings` | `user_id` (pk, **created at sign-up** with defaults: rule `all`, `calendar_template_id` = the user's own "General" copy, `join_instant_meets` false, `notify_email` true), `auto_join_rule` (`all`/`organizer`/`none`), `calendar_template_id` FK (`ON DELETE SET NULL`, falling back to the user's first template), `join_instant_meets` (bool), `instant_meet_mode_override` (null, or `events`/`poll`/`off`, set by the admin), `notify_email` (bool) |
| `calendar_events` | `id`, `user_id`, `connection_id`, `google_event_id`, `recurring_event_id`, `title`, `starts_at`, `ends_at`, `platform` (`google_meet`/`zoom`/`teams`/null), `meeting_url`, `native_id`, `organizer_self`, `attendee_count` (a number, **not** the addresses), `self_response` (`accepted`/`declined`/`tentative`/`needsAction`), `status`, `override` (null/true/false: the user's switch), `google_updated_at`, `synced_at`. Unique `(connection_id, google_event_id)`. Rows for events that ended more than 2 days ago are deleted; all rows are deleted on disconnect. `meeting_url` can carry a Zoom password: never logged, never echoed (the existing rule) |
| `scheduled_joins` | `id`, `user_id`, `source` (`calendar`/`instant_meet`), `calendar_event_id` null, `platform`, `native_id`, `meeting_url`, `starts_at`, `ends_at`, `run_at` (calendar: `starts_at - 2 min`; instant: now), `state` (section 7.7), `skip_reason`, `template_id`, `attempts`, `deadline_at` (`starts_at + 15 min` for both sources), `meeting_id` null (`FK meetings`, the only link between the two), `last_error`, `created_at`, `updated_at`. Unique `(user_id, calendar_event_id)` (one row per event, revived and reused rather than duplicated). Partial unique index on `(platform, native_id)` where `state` is live (`dispatching`/`no_capacity`/`joined`); `joined` ends as soon as the meeting's bot is finished (7.7), so a recurring Meet code is free again |
| `bot_hosts` (Phase 4 migration) | `id`, `name`, `base_url`, `api_key_enc` (null = use the `VEXA_API_KEY` env value, which is how host `local` starts, so no encryption key is needed at that migration), `key_version`, `max_bots`, `enabled`, `draining`, `last_health_at`, `last_health_ok`, `created_at` (section 9) |
| `pubsub_inbox` (Phase 3 migration) | `message_id` (pk), `payload` (jsonb), `received_at`, `processed_at` null, `outcome` (`matched`/`unmatched`/`ignored`/`error`). The web process writes here and returns; the scheduler works through unprocessed rows (section 8.4). Swept after 7 days. Pub/Sub delivers at least once; the primary key drops duplicates |

### 5.2 Constraints and indexes worth naming

Check constraints for every enum-like column (the pattern `_in(...)` in `models.py`); an index on `scheduled_joins (state, run_at)` for the scheduler; on `calendar_events (user_id, starts_at)` for the Upcoming list; on `meetings (owner_user_id, id desc)` for the list.

### 5.3 Per-user scoping rule

Every service function that touches meetings, templates, vocabulary, calendar data, notifications or usage takes the acting `User`, and the query filters by `owner_user_id`. A foreign id returns **404, not 403** (ids are sequential integers; 403 would reveal that the row exists). The existing `/api` route-enumeration test (`tests/test_api.py`) is extended so that for **every route with an id in the path**, a second user's call returns 404. Jinja pages are retired by the Next.js migration; until each page moves, the Jinja routes use the same scoped service calls.

### 5.4 Migration outline (Alembic, after `0005`)

| Revision | Content | Notes |
|---|---|---|
| `0006_users_and_auth` (Phase 1) | `users`, `auth_tokens`, `oauth_flows`, `waitlist_entries`, `notifications`, `mail_outbox`, `user_settings`, new `app_state` columns (`signups_open` false); creates the admin user and its settings row from `OWNER_EMAIL` and `OWNER_PASSWORD` (env read at migration time, hashed) | Migration fails loudly if either is missing in production |
| `0007_ownership_and_usage` (Phase 1) | add `owner_user_id` (nullable) to `meetings`, `templates`, `vocabulary_terms`; backfill all rows to the admin; set `NOT NULL`; swap the unique constraints; add `meetings.source`, `title`, `recorded_seconds`; create `usage_entries` (no backfill: no existing meeting has a known duration). Copy-on-sign-up defaults are code (`DEFAULT_TEMPLATES`, a constant holding the four templates from migration `0001`), not data | Downgrade drops the columns and restores the old uniques (fails if two users have a same-named template: document it) |
| `0008_calendar` (Phase 2) | `calendar_connections`, `calendar_events`, `scheduled_joins` (`meeting_id` FK to `meetings`) | |
| `0009_pubsub_inbox` (Phase 3) | `pubsub_inbox` | |
| `0010_bot_hosts` (Phase 4) | `bot_hosts` seeded with one row `local` (`base_url` from `VEXA_BASE_URL`, `api_key_enc` null, `max_bots = 1`); `meetings.bot_host_id` FK, backfilled to `local` | |

Rules for each: one head; upgrade and downgrade round trip on a scratch database (the existing `tests/test_migrations.py` pattern); migrations are written to run while the pilot is up (backfills are small).

### 5.5 Settings and secrets (new environment variables)

`OWNER_EMAIL`, `TOKEN_ENC_KEY` (32 random bytes, base64), `TOKEN_ENC_KEY_PREVIOUS` (optional, rotation), `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_OAUTH_TESTING` (bool, drives the 7-day hint), `GOOGLE_PROJECT_ID`, `GOOGLE_PUBSUB_TOPIC`, `GOOGLE_PUBSUB_AUDIENCE`, `GOOGLE_PUBSUB_SERVICE_ACCOUNT`, `MAIL_PROVIDER`, `MAIL_API_KEY`, `MAIL_FROM`, `INSTANT_MEET_MODE` (`events`/`poll`/`off`). All in the server `.env` (never in git); `app/config.py` validates them at start in production like `check_start` does today.

---

## 6. The hours cap

- **Unit and measurement.** `meetings.recorded_seconds` is the duration of the recording the pipeline prepares: `app/audio.py` already computes the full length (`_duration` at the start of chunking). When the audio step finishes, one `usage_entries` row is written (idempotent on `meeting_id`). A meeting that fails with no audio counts zero. Waiting-room time counts zero. `ASSUMED, confirm (Q16)`.
- **When it is checked.** At the moment a bot is about to be dispatched, for **all three sources** (pasted link, calendar, instant Meet): `used >= cap` refuses. **Exact formula:** `used = sum(usage_entries.seconds for this user or email_hash) + sum(now - meetings.started_polling_at)` over this user's meetings whose status is `bot_sent` or `processing` and that have no usage entry yet, each term capped at `max_poll_hours` (6 h). The second term is deliberately an over-estimate (it includes waiting-room time) so two long meetings started at 4h55 cannot both slip through, and it covers meetings whose bot has left but whose audio is not yet counted. No `bot_joined_at` is needed. A running meeting that crosses the cap still finishes ("a running meeting finishes"). The source of truth is the ledger plus those live meetings; nothing is cached. The "1 hour left" notice uses the user's **effective** cap (their override or the default).
- **Cap value.** `users.hours_cap_seconds` if set, else `app_state.default_hours_cap_seconds` (18000 s). Admin: never capped. `NULL` default = no cap anywhere (the "removable later" switch).
- **What the user is told.**
  - Pasted link: HTTP 403 `hours_cap_reached`, "You have used your 5 recorded hours for the pilot, so the note taker will not join new meetings. Write to us at hello@beyrictech.com if you need more." (`ASSUMED`: the address, use whichever mailbox exists.)
  - Calendar or instant meeting: the scheduled join becomes `skipped` with reason `hours_cap`, and one notification: "Not recorded: you have used your 5 recorded hours." The Upcoming list shows "Hours used up" in place of "Will join".
  - At 4 hours used: an info notification and a banner, "About 1 hour left".
- **Display.** `GET /api/session` returns used and cap seconds; the app shows a usage bar ("2.4 of 5 h").
- **Why audio length and not wall-clock:** it is what OpenAI bills, it is already measured, and failed joins cost nothing. Rejected: counting from `bot_joined_at` to `bot_left_at` (needs Vexa timestamps we do not rely on today).

---

## 7. Google Calendar auto-join

### 7.1 Scopes (least privilege)

| Scope | Why | Sensitivity | When requested |
|---|---|---|---|
| `openid email profile` | Sign-in with Google | Non-sensitive | At sign-in |
| `https://www.googleapis.com/auth/calendar.events.readonly` | Read events on the user's calendar (title, time, organiser, attendee count, link, response). Narrower than `calendar.readonly` (which adds calendar settings and the calendar list) and than any write scope | Sensitive (Google classifies Calendar scopes for verification; the Calendar auth page itself does not label them, so confirm in the console) | When the user clicks "Connect Google Calendar" |
| `https://www.googleapis.com/auth/meetings.space.readonly` | Learn that a Meet call the user owns has started (Events API), and look up its meeting link | Sensitive (stated in Google's Events API scope list) | Only when the user turns on "also join Meet calls I start" (incremental authorisation) |

`calendar.events.owned.readonly` ("View events on calendars you own") is **not** enough: it would drop meetings other people invite the user to. `ASSUMED`: `calendar.events.readonly` is accepted by both `events.list` and `events.get` (the Calendar auth page does not map scopes to methods; confirm in the first live call).

**Incremental authorisation:** each scope is requested at the moment its feature is switched on, with `include_granted_scopes=true`. Sign-in alone never touches calendar data.

### 7.2 Connect flow (OAuth, server side)

1. `POST /api/calendar/google/connect` (CSRF) creates an `oauth_flows` row (state, PKCE verifier, nonce) and returns the Google consent URL. Parameters: `access_type=offline`, `prompt=consent` (so a refresh token is always issued), `login_hint` = the user's email, `include_granted_scopes=true`.
2. Google redirects to `GET /api/calendar/google/callback` (a public route that is state-checked, not login-checked, but also requires the signed-in cookie so the flow cannot be completed by someone else). The app exchanges the code, checks the granted scopes include what the feature needs (the user can untick boxes on the consent screen; if the calendar scope is missing the connection is refused with a clear message), stores the **encrypted refresh token**, records the connected Google account's email (it can differ from the sign-in email; Settings shows which one), and redirects to Settings → Calendar.
3. The first sync runs immediately.

The redirect URI is `https://notes.beyrictech.com/api/calendar/google/callback` (an `/api/` path, so it reaches FastAPI through the Next.js fallback and the nginx `/api/` locations).

### 7.3 Token refresh

Access tokens are minted from the refresh token when needed and held **in the scheduler's memory only** (about an hour). A refresh that returns `invalid_grant` marks the connection `needs_reconnect` (section 7.8); other errors (network, 5xx) retry with backoff and only raise a notification after 30 minutes of failure. The refresh token is never logged; the token vault object redacts itself in `repr`.

### 7.4 What is read, and how

- Only the **primary** calendar (`ASSUMED, confirm (Q18)`), `singleEvents=true` so recurring series arrive as individual instances.
- **Incremental sync:** a full list covers "now minus 1 hour" to "now plus 14 days" (following `pageToken` until the last page) and stores the `nextSyncToken`; later calls pass the `syncToken` alone (Google does not allow `timeMin`/`timeMax` together with a `syncToken`) and receive only changes, including cancellations (`status = cancelled`). Because the window cannot slide with a token, a **full resync runs every 6 hours** (and on `410 Gone`, which means the token expired), which also picks up recurring occurrences that came into range. (From Google's Events reference: cancelled events are returned during incremental sync.)
- **Fields kept:** id, recurring id, title, start/end, organiser flag (`organizer.self`), the user's own response (`attendees[].self`, `responseStatus`), attendee **count**, event type and status, and the conference link (see 7.5). Attendee emails, descriptions and locations are **not stored** (the description and location are read only to look for a Zoom link, then dropped).
- **Polling, not push, in v1.** A sync per connection **every 5 minutes**, plus a **single-event re-check 3 minutes before each join** (`events.get`). Why not Calendar push notifications (`events.watch`): the webhook needs a public HTTPS endpoint with channel tokens, "there's no automatic way to renew a notification channel", and Google says "Notifications are not 100% reliable", so a polling safety net is needed anyway; the 3-minute re-check already catches a move or cancel in the only window that matters. Push can be added later as a latency optimisation without changing the data model. Cost of polling: 10 users every 5 minutes is about 3,000 calls a day, trivial against Google's quotas.

### 7.5 Which events become joinable

An event gets a platform and link in this order: `conferenceData.entryPoints` of type `video`, then `hangoutLink`, then a Zoom or Meet URL found in the location or description, parsed with the existing `parse_meeting_url` in `app/vexa.py`. Then:

| Event | Result |
|---|---|
| Google Meet link | joinable |
| Zoom link (any `zoom.us/j/...` with its password parameter) | joinable (Zoom may refuse the bot; the existing retry applies) |
| Teams link | "Can't join yet" (`ASSUMED, confirm (Q17)`) |
| No link | "No meeting link" (the design's text) |
| All-day events (`start.date`), `eventType` other than `default` (focus time, out of office, working location, birthday) | ignored |
| `status = cancelled` | ignored; any scheduled join cancelled |
| The user's response is `declined` | not joined (shown as "Declined") |
| Already ended, or longer than 6 hours | ignored (matches `max_poll_hours`) |
| `needsAction` or `tentative` | follow the rule (`ASSUMED`: join) |

**Rule evaluation** is one pure function `will_join(event, settings) -> (bool, reason)` with a table-driven test over rule × organiser × response × platform × override:

- Rule `all`: every joinable event.
- Rule `organizer`: only events where `organizer_self` is true.
- Rule `none`: only events the user has switched on.
- The per-meeting `override` (the design's toggle) beats the rule in both directions, and **resets to null when the rule is changed** (the designer's prototype clears `overrides` on rule change).
- A switch on a recurring event applies to that instance only (`ASSUMED`; the design shows single events).

### 7.6 Join timing

| When | What happens |
|---|---|
| `starts_at - 3 min` | Re-fetch the single event. If cancelled, moved, declined or the link changed, update the join (7.7) |
| `starts_at - 2 min` (`run_at`) | Run the pre-flight checks (connection state, hours cap, per-user concurrency, dedupe, capacity: section 9) and send the bot |
| `starts_at + 15 min` (`deadline_at`) | If the bot never got in, stop trying and record a failure the user can see (section 11) |

`ASSUMED, confirm (Q2)`. If the scheduler was down at `run_at` (deploy, crash), on restart it dispatches every join whose `run_at` has passed and whose `deadline_at` has not, so a restart never skips a meeting.

### 7.7 Scheduled-join states

A `scheduled_joins` row exists **only for events that will be joined** (`will_join` true). When a rule change or a switch makes an event not-joined, its row becomes `cancelled`; when it flips back, the same row is revived to `scheduled` (the unique `(user_id, calendar_event_id)` index forbids a second row). The Upcoming list computes its states and reasons from the event plus the settings, not from stored rows.

`scheduled` → (pre-flight) → `dispatching` → `joined` (a `Meeting` row exists, `meeting_id` is set, and from here **the meeting is the truth**: the existing worker follows it, including the Zoom resend and every bot failure) → `completed`. `joined` becomes `completed` in the same transaction in which the pipeline moves the meeting out of `bot_sent` (to `processing` or `failed`), so the live-join index frees the meeting code the moment the bot is done.

Side exits, all stored on the same row:

| State | Reason (`skip_reason`) | Leaves the state when |
|---|---|---|
| `skipped` | `hours_cap`, `calendar_disconnected`, `duplicate_meeting`, `account_suspended`, `user_limit` (2 bots) | `calendar_disconnected` is revived to `scheduled` on reconnect if the deadline has not passed; `user_limit` and `duplicate_meeting` are retried until the deadline; the others are final |
| `cancelled` | event deleted or cancelled, or switched off | revived if switched back on before the deadline |
| `no_capacity` | (section 9.5) | retried every 15 s to the deadline, then `failed` |
| `failed` | deadline passed, `send_bot` failed on every host 3 times, or capacity never came | **Send now** (below) |

**"Not recorded" rows.** A join that never produced a `Meeting` (states `failed`, `no_capacity`, and `skipped` for reasons `hours_cap`, `calendar_disconnected`, `duplicate_meeting`, `account_suspended`) is shown on the Meetings page from `GET /api/not-recorded` (last 7 days). A join that did dispatch and then failed is an ordinary failed `Meeting` row (the existing failed-meeting UI) with its reason. **Send now** (`POST /api/scheduled-joins/{id}/send-now`, the id comes from the `not-recorded` and `upcoming` payloads) is allowed while `now <= ends_at` (the meeting is still on) and in states `failed`, `no_capacity` or `skipped` (not `account_suspended`). It re-runs the **full pre-flight** (hours cap, concurrency, dedupe, capacity) and never bypasses it; refusals come back as the same error codes (section 13). It sets `deadline_at` to `now + 15 min`.

### 7.8 Moved, cancelled, overlapping, duplicate

- **Moved before dispatch:** the sync updates `starts_at`, `run_at` and `deadline_at` on the same row. **Moved after dispatch** (the bot is already sent): ignored; the bot stays, and Vexa's own timeouts end it if nobody comes. `ASSUMED`.
- **Cancelled before dispatch:** state `cancelled`. **Cancelled after dispatch while still `bot_sent`:** call the existing `cancel_meeting` service (stops the bot and removes the meeting if nothing was recorded).
- **Overlapping (same user):** both join if both are switched on and capacity exists (`ASSUMED, confirm (Q3)`). The user attends one; the other records whatever happens. The hours cap meters it. A per-user limit of **2 simultaneous bots** stops one user from taking every slot.
- **Overlapping (across users):** a capacity question only (section 9).
- **Same meeting, two users:** the partial unique index on `(platform, native_id)` for live joins gives one bot; the second user's join becomes `skipped` with reason `duplicate_meeting` and the message "A Beyric note taker is already in this meeting." The same check covers a **pasted link**: the placement step refuses (409 `already_in_meeting`) when any live meeting, by any user, has the same platform and `native_id`. `native_id` is exactly what `parse_meeting_url` returns today (the lower-cased Meet code, or the numeric Zoom meeting id without the passcode), so different URL spellings of one meeting match. `ASSUMED, confirm (Q9)`.
- **Events that appear or move after `run_at`:** if the event starts in the past but `now <= deadline_at`, the join is dispatched at once (a late join); if it is older than that but still on (connecting the calendar mid-meeting), the Upcoming row says "In progress" with **Send now**.
- **Same meeting from the calendar and from instant-Meet detection:** same index, same outcome.
- **Recurring events** reuse the same Meet code every week, so dedupe is on "no live join for this code right now", never on "ever seen this code".

### 7.9 The "calendar disconnected" state

Three causes, one visible state:

| Cause | How we notice | What the user sees |
|---|---|---|
| Google test-mode expiry (7 days) | `invalid_grant` at refresh; or the **early warning**: while `GOOGLE_OAUTH_TESTING` is on, a banner appears when `expected_expiry_at` is under 24 hours away ("Google will disconnect your calendar tomorrow. Reconnect now"), with a button | Red banner "Your Google calendar is disconnected. N meetings in the next 24 hours will not be recorded", Settings → Calendar shows "Disconnected (reason)" and one **Reconnect** button |
| User revoked access at Google | `invalid_grant` | Same, reason "access was removed" |
| Repeated API errors | 30 minutes of failures | A warning (not red) "We could not reach your calendar since 14:05", retrying |

On `needs_reconnect`: stop syncing, set `calendar_connection.status`, pause that user's calendar joins (state `skipped`, reason `calendar_disconnected`; `ASSUMED, confirm (Q8)`), send one in-app notification and one email (if mail exists), and list how many of the next 24 hours' meetings are affected (from the cached events). Reconnect restores `connected`, resyncs, and **revives** the `calendar_disconnected` joins whose deadline has not passed (7.7). The "could not reach your calendar" warning row sets `status = degraded` (sync keeps retrying; joins are not paused). Pasting a link keeps working throughout.

If Q7 is accepted (production, unverified), the 7-day expiry should not occur; the state and warning stay in the product anyway because users can also revoke from Google.

**Disconnect (user-initiated)** is different from lapse: `POST /api/calendar/google/disconnect` revokes the token at Google (`oauth2.googleapis.com/revoke`), deletes the Events API subscription if any, deletes the user's `calendar_events` and **all** their `scheduled_joins`, deletes the encrypted token, and sets `disconnected`. Meetings already recorded stay (exact list in 12.2).

---

## 8. Instant-Meet auto-join (Meet calls the user starts)

### 8.1 Mechanism

The Google Workspace Events API can subscribe to **a user** as target, `//cloudidentity.googleapis.com/users/USER`, where Google's documentation says the subscription "receives events about all meeting spaces where the user is the owner of the meeting space". Event type: `google.workspace.meet.conference.v2.started`. Delivery is by Cloud Pub/Sub (CloudEvents), pushed to our endpoint.

Setup: a Google Cloud project with **billing enabled** and the Events and Pub/Sub APIs on (the create-subscription page lists billing as a prerequisite; Pub/Sub at this volume is effectively free); one Pub/Sub topic; the Pub/Sub Publisher role granted on that topic to `meet-api-event-push@system.gserviceaccount.com` (documented as the service account that delivers Meet events); a push subscription to `https://notes.beyrictech.com/api/webhooks/google/pubsub` with authentication (a service account whose signed OIDC token we verify).

### 8.2 The question: does it work for personal @gmail.com accounts?

**Finding: undocumented; I could not confirm it either way. Treat it as NOT working until the spike proves it.**

What the sources say:

- The Meet events guide and the Meet events overview (developers.google.com) **never state which account types can subscribe**; there is no "Workspace only" and no "personal accounts supported" statement. The Events API authorisation page says user-target subscriptions "typically require user authentication" and makes no account-type statement for Meet. The Meet release notes (to April 2026) contain nothing about personal accounts.
- A secondary source (a GitHub pull request that ran the Meet REST API in a live test with a personal Gmail) reports that `conferenceRecords`, participants and sessions **do return** for a personal account, and `transcripts.list` returns empty (transcripts need features Gmail lacks). That suggests the Meet REST API itself is usable from a personal account, not that Events subscriptions are.
- Recall.ai (a vendor) states that some Meet API capabilities (stable meeting URLs, recordings and transcripts) need a paid Workspace plan and that "free Gmail accounts don't support" the lookup-URL method. That is about artifacts and creation, not about start events.
- Since November 2025 Google also delivers `conference.v2.started` to **calendar invitees**, not just owners. That is useful for calendar meetings only; our calendar path does not need it.

**Confidence.** That subscriptions with the `users/` target work for a personal Gmail account: **about 40%** (my estimate; no source says so). That `conferenceRecords.list` works for a personal account: **about 70%** (one secondary live report). That an owner-only filter exists on `conferenceRecords.list`: **unknown**; the reference lists scopes and the filter fields (`space.meeting_code`, `space.name`, `start_time`, `end_time`) but does not say whose records are returned.

### 8.3 The throwaway spike (Task P0.2; runs in parallel with accounts)

Needs from Adebayo: a Google Cloud project with billing, an OAuth client (Testing mode), two test accounts (one @gmail.com, one Workspace; Q20). Pass criteria in bold.

| # | Test | Pass means |
|---|---|---|
| S1 | With the personal account, authorise `meetings.space.readonly` and create a subscription on `users/{id}` for `conference.v2.started` | **Subscription is created (HTTP 200)**; record the returned `expireTime` and state, and **which value `{id}` must be** (the docs say the `signedinUser.user` id; does `users/me` work, or is it the OIDC `sub`?) |
| S2 | Same account: start a call from meet.new; then one via "Start an instant meeting" in the Meet app; then one from a calendar event | **An event arrives in Pub/Sub within 60 s** for each; record payload (it should hold a conference record name) |
| S3 | Repeat S1/S2 with the Workspace account | Same; shows whether the answer depends on account type. Also try after an admin restriction if one exists |
| S4 | From the payload, call `conferenceRecords.get`, then `spaces.get` | **Returns the meeting URI/code** so a bot can be sent |
| S5 | Personal account: `conferenceRecords.list` with `filter=end_time IS NULL` while (a) a call the user started is live, (b) a call someone else started is live and the user joined, (c) a call is live that the user is not in | Which of a/b/c appear. **Needed: (a) appears; ideally (b) and (c) do not**, otherwise the owner cannot be told from a participant |
| S6 | Leave the subscription alone for 7 days | Does it expire or suspend; what does `expireTime` do; how do we renew |
| S7 | Per-minute quota on `conferenceRecords.list` | We can poll 10 users every 30 s |
| S8 | Vexa: can it post a chat message; does one API key enforce its own concurrent-bot limit; does it list running bots; which cheap endpoint serves as a health check | Answers Q1 and section 9 |
| S9 | The Workspace or Gmail account is **invited** to a call that someone else starts | **No event is delivered** on the invitee's `users/` subscription (if one is, the handler must prove ownership or drop it) |

### 8.4 Design: three modes behind one setting, plus manual fallbacks

`INSTANT_MEET_MODE` (and a per-user override for the spike):

| Mode | When used | How it works |
|---|---|---|
| **`events`** (preferred) | If S1 to S4 pass for the account type | Subscription per connected user. The push endpoint verifies Google's OIDC token (audience and service account), **inserts the message into `pubsub_inbox`** (the primary key drops duplicates) in one short transaction, and returns 204. It does nothing else, so a crash after the insert loses nothing: the scheduler works through rows with `processed_at` null. For each one the scheduler (1) looks up the conference and then the space (meeting code), (2) applies the **ownership rule below**, (3) checks the code is not already live-joined, (4) creates a `scheduled_join` with source `instant_meet` (`starts_at` = the conference start, `run_at` = now, `deadline_at` = start + 15 min) and dispatches |
| **`poll`** (fallback if S1 or S2 fail but S5 passes) | Personal accounts where Events fails | The scheduler calls `conferenceRecords.list` with `end_time IS NULL` every 30 seconds per connected user who has instant-Meet on. A record that is new, whose space is not already being recorded, and (if S5 shows (b)/(c) appear) whose **owner** can be established, becomes a join. Cost: the bot arrives up to about 30 s plus a minute later than with Events, so the first minute or two of a call is missed. State this to the user: "Detects the start within about a minute" |
| **`off`** | Nothing works for the account | The switch is hidden, replaced by the manual routes below |

**Ownership rule (both modes), so an instant-Meet detection can never override a calendar rule or join someone else's meeting:**
1. If the user's cached calendar events contain the same meeting code within the day, **the calendar path governs** (rule, switch, declined, all apply) and the instant path does nothing.
2. Otherwise the meeting is "not on the calendar", and it is joined only if the user **started** it. In `events` mode that is guaranteed by the subscription target (only meeting spaces the user owns are delivered on a `users/` subscription); because Google also sends `conference.v2.started` to *invitees* since November 2025, spike test S9 checks that an invitee's subscription does **not** deliver a call someone else started, and if it does the handler drops any event it cannot prove the user owns. In `poll` mode the owner must be provable from the data (S5), or `poll` stays off.

**Instant-Meet mode selection.** `INSTANT_MEET_MODE` is `events`, `poll`, `off` or `auto`. `auto` reads `calendar_connections.google_hosted_domain` (the ID token's `hd` claim): a Workspace account uses `events`; a personal account uses `INSTANT_MEET_PERSONAL_MODE` (default `off` until the spike decides). The admin can override one user through `user_settings.instant_meet_mode_override`.

If S5 shows that `list` cannot separate a call the user started from one they merely joined, **`poll` is not allowed** (it would join meetings that are not on the calendar and that he did not start, which Adebayo ruled out), and the product falls to the manual routes.

**Manual routes (always available):**
- **F3, "Start a meeting with the note taker":** a button that creates a Meet space through the Meet API (`meetings.space.created`, sensitive) and opens it, with the bot sent in the same tap. Works only if `spaces.create` works for the account type (Recall.ai suggests creation can need Workspace; unverified).
- **F4, paste a link** (exists today), improved by making the Next.js app an installable PWA with a share target, so "Share to Beyric" from a phone is one tap.

**What this means for the pilot, honestly:** if Events works for personal Gmail, the instant-Meet promise holds for everyone. If it does not, Adebayo's own account (if it is a Workspace domain, Q20) is likely covered by `events`, and Gmail testers get `poll` if S5 passes, else the manual routes. The spec's user-facing wording must not promise more than the mode that is live for that account.

### 8.5 Subscription lifecycle

Created when the user turns the switch on (after the incremental consent), stored in `calendar_connections.meet_subscription_*`. A reconcile job runs every 6 hours: lists subscriptions, renews any within 24 hours of `expireTime`, recreates missing ones, and reacts to suspension (authorisation revoked) by marking the instant-Meet feature "needs reconnect" with the same banner as 7.9. The documented lifetime depends on whether event data is included in the payload and the page gives no number, so the real value comes from S6; renewal is built regardless.

---

## 9. The bot dispatcher and capacity registry

### 9.1 Why

Today one bot at a time is enforced by an advisory lock and a check (`NEW_MEETING_LOCK`, `bot_already_active` in `app/services.py`). With ten users and automatic joins, two meetings at 10:00 is the normal case. A queued meeting is an unrecorded meeting, so capacity must exist and be placed by a dispatcher.

### 9.2 Planning numbers (assumptions; to be measured)

| Number | Value | Source |
|---|---|---|
| Memory per bot | about 1.2 GB on top of Vexa idle | spike report: 1.2 to 1.4 GB with one bot against 0.55 to 0.83 GB idle |
| Vexa idle | about 0.54 GB | handoff |
| Vexa Lite cap on the current box | 3 GB (`mem_limit: 3g` in `compose.yaml`) | compose |
| Bots that fit in 3 GB | `(3 - 0.54) / 1.2` is about 2 by memory; **one until proven** | arithmetic |
| A dedicated 8 GB machine | `(8 - 1 OS - 0.54) / 1.2` is about 5 by memory | arithmetic |
| CPU per bot | **unmeasured**; Chrome plus real-time audio may bind before memory does | |
| Spike report's earlier figure | "an estimated 12 to 16 GB for 10 at once, untested" | spike report |

Task M1 measures 1, 2 and 3 simultaneous bots on one machine (memory, CPU, audio lag in live transcripts) before `max_bots` is raised above 1.

### 9.3 Registry

`bot_hosts` (section 5.1): each row is one Vexa Lite stack with its own API base URL and key. `max_bots` is set by the admin (conservative default 1). The current Vexa becomes host `local`. `draining` stops new placements without disturbing running bots (for upgrades). **Where the second machine lives:** a separate small server running only Vexa Lite (a copy of the `vexa` and its database and storage services from `compose.yaml`, in `deploy/vexa-host/`), reachable from the app server over a private link (WireGuard or Tailscale; the Vexa API must never be public), because the pipeline downloads audio from the host that recorded it.

`meetings.bot_host_id` records the host; the worker's polling, audio download, recording delete and `stop_bot` all use that meeting's host (today they use one `VexaClient()` built from settings). `get_vexa` in `app/services.py` becomes `vexa_for(host)`.

### 9.4 Placement

One function, the only place a bot is sent:

1. Take a **per-host** advisory lock (a new constant plus the host id), so two dispatches cannot read the same free slot and one slow host does not block the others. The lock is held across the `send_bot` call; that call keeps the existing 30-second client timeout, so the worst case is bounded.
2. Candidate hosts: `enabled`, not `draining`, and `last_health_ok` within the last 2 minutes (the scheduler pings every 30 s).
3. Active bots on a host = count of `meetings` with that `bot_host_id` and `status = 'bot_sent'` (the bot is alive until the recording is available; `processing` meetings no longer hold a bot). If Vexa can list running bots (S8), cross-check against it and trust the larger number.
4. Pick the host with the most free slots (ties: lowest id). If `active < max_bots`, create the `Meeting` row with that host and call `send_bot`.
5. If `send_bot` fails on that host, mark it unhealthy for 2 minutes and try the next one. As in `_create_meeting` today, the bot is sent first and the `Meeting` row inserted after, in the same transaction as the lock; if the insert fails after a successful send, the bot is stopped (best effort). Nothing is rolled back when every host fails: no row was created.
6. A host that is `draining` or `enabled = false` receives no new bots, but meetings already placed on it keep being polled, downloaded and cleaned up through its row; a host with meetings cannot be deleted.
7. Before all of that, the pre-flight checks in order: connection state, hours cap, per-user concurrency (2; with one host it only matters once `max_bots` is above 2, so it is mostly a Phase 4 safeguard), dedupe (the partial unique index plus the live-meeting check for pasted links), then capacity.
8. **Health:** the scheduler calls a cheap Vexa endpoint on each host every 30 seconds with a 3-second timeout (which endpoint is task S8's answer; until then, the `send_bot` result is the only signal).

**Phasing.** Phases 2 and 3 build `place_bot()` with **one** host (`local`, `max_bots` from a setting, default 1) and already include the whole no-capacity path of 9.5, so an auto-join that finds the bot busy is alerted, retried and shown, never dropped. Phase 4 generalises the same function to the `bot_hosts` registry. Until then the global one-bot rule stays, but a pasted link that finds the bot busy answers 409 `bot_busy` with the neutral text "The note taker is busy with another meeting. Try again in a few minutes." (not the current `bot_already_active` wording, which would reveal other users' activity).

### 9.5 At capacity

Never silent. When no host has a free slot:

1. The join becomes `no_capacity`. The owner gets an in-app notification and the row shows **"Waiting for a free note taker (retrying until 10:15)"** with a **Send now** button.
2. The admin is alerted at once (in-app and email, de-duplicated per 30 minutes), naming who was refused and for which meeting.
3. The scheduler retries placement **every 15 seconds** until `deadline_at` (`ASSUMED, confirm (Q14)`: start + 15 minutes, the same deadline as every join). Joining late still records most of a meeting.
4. At the deadline the join becomes `failed` and the owner sees a failed row **"Not recorded: all note takers were busy at 10:00"**, plus an email once mail exists.
5. Pasting a link while at capacity returns HTTP 503 `no_capacity` with the same words and the next expected free time if known.

This is a bounded, visible, alerted retry, not a queue of work promised to later. If Adebayo prefers none, set the retry window to zero and the behaviour is "fail immediately and alert".

### 9.6 Forecast (avoid the problem rather than react)

Calendar joins are known in advance. A job every 5 minutes counts, for each of the next 60 minutes, the joins that will be live at once (`starts_at` to `starts_at` plus the event length) against total `max_bots` of healthy hosts. If the peak exceeds capacity, the admin gets a **warning 60 minutes ahead** ("At 10:00 you will have 4 bots and capacity for 2"), which is time to raise a limit, start a machine, or message a user. Instant meetings cannot be forecast; they rely on headroom.

### 9.7 Related worker changes

- A new **`scheduler` service** (own process, same image, `python -m app.scheduler`) does everything time-critical: calendar sync, re-checks, dispatch, Pub/Sub event handling, host health, mail sending, forecast, subscription reconcile. It must not share a loop with `app.worker`, because the worker can be blocked for minutes in one transcription pass (the note in `run_pass` says so) and a join due at 10:00 cannot wait behind that.
- Only one scheduler acts at a time: a **session-level advisory lock on a dedicated, direct Postgres connection** (this stack has no connection pooler in front of Postgres; if one is ever added, that connection must bypass it). The lock is released the moment the connection dies, and a standby retries every 10 seconds, so failover takes about 10 seconds. The clock is injected so tests can run a whole day in milliseconds.
- **Two loops in the one process, on separate threads and database sessions:** a **dispatch loop** every 10 seconds (re-checks, dispatch, capacity retries, inbox processing, host health) and a **sync loop** that wakes every 60 seconds and syncs each connection that is due (every 5 minutes), with a per-call timeout, so a slow Google response can never delay a dispatch.
- **Periodic jobs** (each is one small task in the build order): hourly sweeps of `oauth_flows`, `auth_tokens`, `pubsub_inbox` (7 days), `calendar_events` (ended over 2 days ago), unverified accounts (48 hours) and notifications (90 days); `mail_outbox` sending and retries every tick; the 4-hour usage notice (written when a usage entry pushes a user past 80%); the 6-hourly full calendar resync and Events-subscription reconcile; the forecast every 5 minutes; the scheduler heartbeat written to `app_state.scheduler_heartbeat_at` every tick (the admin page alerts after 5 minutes without one).
- `app.worker` gets 2 replicas (`next_meeting_id` already uses `skip_locked`), and the note in `run_pass` about the connection being held during OpenAI calls is addressed (session released between steps) before concurrency above 2.
- Deploy gate (`ASSUMED, confirm (Q19)`): `deploy/deploy.sh` also refuses while a calendar join is due within 10 minutes.

---

## 10. The recording notice

Today: the bot's name is "Beyric Note Taker (recording)"; the UI says "Tell participants that this meeting will be recorded."; the policy says the user is responsible for telling participants. With auto-join the user is not present when the bot is sent, so the product should do more.

`ASSUMED, confirm (Q1)`:

1. **Keep the bot name** (always).
2. **One chat message on joining**, if Vexa can post to meeting chat (S8): "Beyric Note Taker is recording and transcribing this meeting for {first name}. If you do not agree, ask them to remove the bot." No speech, no video overlay.
3. **If Vexa cannot post chat:** the name only, plus a reminder to the user in the Upcoming list and in the "Will join" email/notification ("Tell participants the meeting is recorded").
4. **Not done:** editing the calendar invite (needs a write scope), a consent prompt participants must click (Read.ai-style prompts are for apps that sit inside the meeting UI; we cannot add UI to Meet or Zoom).
5. The privacy policy sentence is changed to match whichever of 2 or 3 ships (section 12.3). Until P0.3 answers whether Vexa can post chat, the policy keeps today's sentence and nothing claims a chat message.
6. **The first name** in the chat line is the first word of `users.name`, reduced to letters, spaces and hyphens and cut at 30 characters; if nothing is left, the line says "the meeting host". English only in v1.
7. The reminder in the Upcoming list ("Tell participants the meeting is recorded") is in-app from Phase C; **no "will join" email is promised** until a mail provider exists, and then only as an option (`notify_email`).

Legal exposure sits with the user per the terms; this section is about being honest and visible, not a legal opinion (Q21).

---

## 11. Failure handling and who is told

Channels today: **in-app** (always). **Email** is added when the mail provider exists (Q6). A `notifications` table backs both; `GET /api/notifications` feeds a bell and the banners.

| Event | User sees (in-app; email when available) | Admin told? |
|---|---|---|
| Bot could not get in (host declined, Zoom turned it away 3 times, lobby timeout) | Meeting row "Not recorded: {Vexa's reason in plain words}" (the existing `bot_failure_reason` text), "Send now" while the meeting is still on | No (user-level) |
| No capacity | Section 9.5 | **Yes**, immediately |
| Hours cap reached | Section 6 | Once, as an info item |
| Calendar disconnected | Section 7.9 | If more than one user in an hour |
| Google API errors | Warning after 30 min | If 3 or more users |
| Bot host unhealthy | none | **Yes**, immediately |
| Recording ready | Existing meeting page; email "notes ready" is the next module, not this one | No |
| Mail failed to send | none | Admin screen list |
| Instant-Meet event could not be matched to a user or space | none (logged) | Counted on the admin page |

Rules: a failure never looks like success (the MVP's anti-goal); every failure row states a reason in words, never a code; one notification per `dedupe_key` per meeting (no storms).

---

## 12. Security and privacy

### 12.1 Tokens at rest

- Refresh tokens and host API keys are encrypted with **AES-256-GCM** (the `cryptography` package), a 12-byte random nonce per value, authenticated data = purpose plus the owning `user_id` (known before the first insert; the host key uses the host name), so a ciphertext copied to another user fails to decrypt. Stored as `nonce || ciphertext` in a bytea column with `key_version`.
- The key is `TOKEN_ENC_KEY` in the server `.env`, **not in the database and not in backups** (`deploy/backup.sh` backs up the database only), so a stolen backup contains no usable tokens. Rotation: set the new key, move the old one to `TOKEN_ENC_KEY_PREVIOUS`, run a re-encrypt script (its own task, C9), remove the old key. `TOKEN_ENC_KEY` is required from Phase 2; in production the app refuses to start without it once `GOOGLE_CLIENT_ID` is set. A decrypt failure never crashes anything: that connection becomes `needs_reconnect` (reason `error`) and the admin is alerted.
- Access tokens are not persisted. Tokens, codes and meeting links with passwords are never logged (a logging test greps the captured logs of the full flow). The vault object's `repr` is `<redacted>`.
- OAuth `state` is single-use with a 10-minute expiry; PKCE on every flow; redirect URIs fixed in the Google console and checked against an allow-list in code: `https://notes.beyrictech.com/api/auth/google/callback` (sign-in) and `https://notes.beyrictech.com/api/calendar/google/callback` (calendar and Meet). `return_to` must be a relative path that starts with a single `/` (never `//` or a scheme), checked by a regular expression and stored server-side in `oauth_flows`, never taken from the callback URL.

### 12.2 Other controls

- Passwords: argon2id; reset and verify tokens stored as hashes; login limits per client and per email; same response for existing and unknown emails. **Mail:** `ConsoleMailer` (prints the link) is refused when `app_env` is `production`.
- **IDOR:** section 5.3; the route-enumeration test is the guard.
- **Pub/Sub endpoint** (the most exposed new route): verify the bearer OIDC token (signature via Google's keys, `aud`, `email` equal to the configured service account, `email_verified`), reject everything else with 401, drop duplicates, never trust the body for identity (look the user up from the subscription name we stored), 1 MB body limit, own rate limit zone in nginx (20 requests per second per address with a burst of 40, enough for Google's push, low enough to blunt abuse). It is exempt from login and CSRF **by design** and listed explicitly in the route-enumeration test. Opus review required (handoff section 7).
- Admin API: 403 for non-admins, tested per route.
- The Vexa API on the second host: private network only; API key per host.
- Google data minimisation: attendee counts not addresses; descriptions not stored; calendar rows deleted after 2 days and on disconnect.
- CSP unchanged for FastAPI-rendered pages; Next.js sets its own nonce CSP.
- **Nginx limits for the new public routes** (`deploy/nginx.conf`, new zones next to the existing ones): one zone `api_auth` at 10 requests per minute per address (burst 10) shared by sign-up, resend, verify, forgot, reset and Google start; the waitlist 5 per minute (burst 5); the Pub/Sub path as above.
- **What a calendar disconnect deletes, exactly:** the encrypted token (after revoking it at Google), the Events subscription, every `calendar_events` row, every `scheduled_joins` row (all states), and calendar-kind notifications. **What stays:** meetings already recorded, including their `title` and `url` (a Zoom link's passcode is kept in `meetings.url` exactly as it is today, because Retry needs it), the usage ledger, and the user's settings row. The privacy policy's "calendar data" means the first list, and its wording in 12.3 says so.

### 12.3 Privacy policy and terms: claims that change, and the new sentences

The current text (`app/templates/privacy.html`, `terms.html`) was written to be true for one owner. Each row changes **in the same PR as the feature** (the SOP rule). Exact new sentences:

| Where | Today | New sentence |
|---|---|---|
| Information we collect, account | "Account details: your name, email address and password. We store passwords only in hashed form." | "Account details: your name and email address, and a password (stored only in hashed form) or, if you sign in with Google, your Google account identifier. We also keep a count of the hours of meetings recorded for your account, and, if you join the waiting list, your email address." |
| Google user data, Calendar | "we read the title, time, organiser, attendees and meeting link of your upcoming events" | "Google Calendar: we read the title, start and end time, whether you are the organiser, your reply, the number of attendees (not their addresses) and the meeting link of events on your primary calendar. We use this only to decide which meetings to join, following the rules you choose, and to show your upcoming meetings in the app. We do not create, change or delete calendar events." |
| Google user data, Meet | "Google tells us when a Meet call you host begins, with its meeting code" | **If `events` mode:** keep the sentence. **If `poll` mode:** "if you turn on joining meetings you start yourself, we ask Google every 30 seconds whether a Meet call you are hosting has started, and receive its meeting code. We do not receive the meeting's audio, video or chat from Google." |
| Google sign-in (new) | none | "If you sign in with Google, we receive your name, email address and Google account identifier, and nothing else, unless you also connect your calendar." |
| How long we keep, calendar | "Calendar data: we keep only what is needed for upcoming meetings, and delete it when you disconnect your calendar." | "Calendar data: we keep upcoming events and events from the last two days, and delete all of it when you disconnect your calendar or close your account." (matches the 2-day sweep) |
| Security (new, only after 12.1 ships) | "secrets are kept out of our code" | add: "Google access credentials are encrypted before they are stored, and the encryption key is kept apart from the database and its backups." |
| Recording and participant notice | "The note taker appears in the meeting's participant list while it records." | **If chat message ships:** add "When it joins, it posts one message in the meeting chat saying that the meeting is being recorded." **If not:** leave as is. Either way add: "When a calendar rule sends the note taker to a meeting automatically, the person whose account sent it is responsible for telling participants." |
| How we use your information | "To send service emails, such as account verification, password resets and notices about meetings." | Stays only **once mail is sending**; until then delete "account verification, password resets" so the policy is not ahead of the app |
| Who we share with | Contabo, OpenAI, Cloudflare, platforms | Add the mail provider by name when chosen, and Google Cloud Pub/Sub as the way Google tells us a call began (events mode only) |
| Your rights | email request | unchanged in v1; changes when in-app deletion ships |
| Terms, "Pilot, fees and limits" | "Each pilot account has a limit on recorded hours, shown in the app" | true once the usage bar ships; **add**: "The pilot has a limited number of accounts. When it is full you can join a waiting list." |

**Which task changes which row:** account rows ("Information we collect, account", "Google sign-in", "How we use" email wording, Terms "Pilot") by A20; "Calendar" and "How long we keep" by C17; the "Security" row by C2 (it ships with the vault, not before); "Recording and participant notice" by whichever of Q1's options ships (task I7 if the chat message needs Vexa support found by P0.3); "Meet" and "Who we share with" by I7; "Your rights" by the later in-app deletion.

**Verification-specific checks (Google compares the policy to the app):** the home page and policy are on `notes.beyrictech.com`, public, and linked from the consent screen; the Limited Use statement stays verbatim; every scope requested has a matching sentence; no scope is requested that the live app does not use.

---

## 13. API additions (for the Next.js frontend)

All under `/api`, JSON, the existing error shape `{"error": {"code", "message"}}`, session cookie, and `X-CSRF-Token` on every non-GET. Payloads below are **intentions, not captures**; the integration guide (`docs/fe-integration-guide-pilot-accounts-calendar.md`) will be written from real captured responses after each phase ships, per the project rule.

**Changed existing**
- `GET /api/session`: now returns the user (email, name, admin flag, verified flag), usage (seconds used, cap seconds or null), calendar status, pilot status. `POST /api/session` logs in with email and password. `DELETE /api/session` signs out this user only.
- Meetings, templates, vocabulary: same routes, now scoped to the caller (foreign ids give 404). `POST /api/meetings` adds the `hours_cap_reached` (403), `bot_busy` (409, until Phase 4), `already_in_meeting` (409) and `no_capacity` (503) errors; `bot_already_active` is removed from the API. Meeting objects gain `source`, `title`, `not_recorded_reason`.

**Public**
- `GET /api/pilot`: open, full, or closed (no counts of other people beyond "full").
- `POST /api/signup`; `POST /api/signup/resend`; `POST /api/verify-email`; `POST /api/password/forgot`; `POST /api/password/reset`.
- `POST /api/waitlist`.
- `GET /api/auth/google/start`, `GET /api/auth/google/callback` (redirects).
- `POST /api/webhooks/google/pubsub` (OIDC-verified, no cookie, no CSRF; explicit exception).

**Calendar**
- `GET /api/calendar`: connection state, account email, reason when disconnected, rule, template, instant-Meet switch and mode.
- `POST /api/calendar/google/connect`, `GET /api/calendar/google/callback`, `POST /api/calendar/google/disconnect`.
- `PUT /api/calendar/settings`: rule, calendar template, instant-Meet switch.
- `GET /api/calendar/upcoming`: next 7 days plus the last 24 hours; each item has its join id (if any), title, start, duration, platform, attendee count, and a `state` with a `reason` in plain words. The server owns the wording (one module, `app/calendar_text.py`), so the frontend never invents reasons. States: `will_join`, `off`, `cannot_join` (no link, Teams), `declined`, `hours_used`, `duplicate`, `disconnected`, `waiting_for_capacity`, `in_progress`, `done`, `not_recorded`.
- `PUT /api/calendar/events/{id}/override`: true, false or null (null clears it). On an event that cannot be joined (declined, ended, Teams, no link) it answers 409 `not_joinable` with the reason; a rule change clears overrides on events that have not started.
- `POST /api/scheduled-joins/{id}/send-now` (rules in 7.7).
- `GET /api/not-recorded`: the last 7 days of joins that never produced a meeting, each with a plain-words reason and its join id.
- `PUT /api/calendar/settings` returns 409 `consent_required` with an `authorize_url` when turning on instant-Meet needs the Meet permission (or the calendar rule needs the Calendar permission); the connect call takes the list of features to authorise, and both features share one callback.

**Notifications**
- `GET /api/notifications` (`limit` default 50, `before_id`), `POST /api/notifications/{id}/read`, `POST /api/notifications/read-all`. The unread count for the bell is `unread_notifications` in `GET /api/session`.

**Admin (`/api/admin/...`, 403 for non-admins; lists take `limit` and `before_id`)**
- `GET /users`, `PATCH /users/{id}` (only `status` between `active` and `suspended`, and `hours_cap_seconds`), `DELETE /users/{id}` (sets `deleting`; the scheduler runs the deletion), `POST /users/{id}/reset-link` (returns a one-time reset link once; the no-mail fallback of 4.3). The last remaining admin can be neither suspended nor deleted (409 `last_admin`). There is **no** endpoint for an admin to read another user's meetings, notes or calendar.
- `GET /waitlist`, `POST /waitlist/{id}/invite` (returns `invite_url` when no mail provider works), `DELETE /waitlist/{id}`.
- `GET /pilot`, `PUT /pilot` (open/closed, max accounts, default hours, password sign-up switch).
- `GET /capacity` (hosts, active bots, free slots, forecast, scheduler heartbeat, count of unmatched instant-Meet events), `POST /hosts`, `PATCH /hosts/{id}`.
- `POST /connections/{id}/pause` and `/resume` (kill switch per connection); `PUT /users/{id}/instant-meet-mode`.
- `GET /alerts`.

**New error codes** (all in the existing `{"error": {"code", "message"}}` shape): `pilot_closed` 403, `pilot_full` 409, `email_taken` is never returned (sign-up always answers 202), `email_not_verified` 403, `invalid_credentials` 401, `token_invalid_or_expired` 400, `password_too_weak` 422, `invite_invalid` 400, `account_suspended` 403, `hours_cap_reached` 403, `bot_busy` 409, `already_in_meeting` 409, `no_capacity` 503, `calendar_not_connected` 409, `consent_required` 409, `not_joinable` 409, `last_admin` 409, `template_in_use` 409. Messages are written for display; the frontend maps on `code`. `GET /api/pilot` returns `{state: open|full|closed, prefill_email}`.

**Guard changes** (`app/auth.py`): `PUBLIC_API` becomes a list of exact public routes (including the sign-up, verify, reset, waitlist and Google start/callback routes); a second small set marks the webhook route as "own authentication". The route-enumeration test fails if a route is public by accident.

**Nginx** (`deploy/nginx.conf`): the new zones and numbers are in 12.2 (the JSON API PR limited only `/api/session`; sign-up, forgot, Google start and the Pub/Sub path are the new spam targets).

### Screens the Next.js app needs

Designed already: sign in, create account, forgot password, check your email, new password (`Auth.dc.html`); Calendar card, Upcoming, Settings → Calendar with rules and template; verify and reset emails (`Emails.dc.html`). **Not designed, to request from the designer:** "Pilot full" and waitlist form; "Confirm your email" landing; usage bar and "hours used up" state; "Calendar disconnected" banner and reconnect; the instant-Meet switch; "Not recorded" meeting rows with "Send now"; "Waiting for a free note taker"; the notification bell; the four admin screens (users, waitlist, capacity, pilot settings); Google "unverified app" explainer for testers (if Q7).

---

## 14. Testing approach

CI runs the heavy suites; locally run lint, format, type checks and the tests of the touched area (project rule). No new rule of mine overrides that.

| Layer | What |
|---|---|
| Pure units | `will_join` matrix; event to platform/link parser (including Zoom passwords and Teams); dispatcher placement function (free slots, health, draining, ties); token vault round trip, wrong AAD fails, `repr` redacted; password and token hashing; the cap arithmetic |
| Service/DB (existing pattern, real Postgres in Docker) | sign-up cap under 10 parallel requests; waitlist reservation; ownership scoping for every service; ledger idempotence; hours check with an in-flight meeting; dedupe index (two users, one code); calendar sync with a mocked Google (httpx `MockTransport`) covering `syncToken`, `410`, cancelled, moved; scheduler run with an injected clock covering "scheduler was down at `run_at`"; dispatcher race (two joins, one slot, exactly one placed) |
| Migrations | upgrade and downgrade for 0006 to 0009 on a scratch DB with seeded owner data; backfill assigns everything to the admin; one head |
| Security | route-enumeration test extended: every route classified public, own-auth, user or admin; foreign-id 404 for every id route; Pub/Sub endpoint rejects a missing, wrong-audience, wrong-account or expired token; logging test proves no token, code or meeting URL password appears in captured logs of the full OAuth flow |
| Fakes | `FakeGoogle` (OAuth, Calendar, Meet, Events), `FakeVexaHost` with a capacity counter, `FakeMailer`; Google behaviour is **never** assumed from the fake: each fake behaviour is copied from a live capture (spike or test account) and noted |
| Live, manual | Google test users: connect, sync, a real calendar meeting joined, a moved and a cancelled meeting, a revoked token, an expired token if reproducible; Zoom meeting from the calendar; capacity: three bots at once on the second host. Captured responses feed the FE integration guide, as the project rule requires |
| Load | M1 (bots per machine) and M2 (second host with `max_bots` raised), recording memory, CPU and transcription lag per bot count |
| Not simulated | The 7-day expiry (needs 7 days or the Google test clock, if any), Google's real review: labelled "not verified live" in the guide |

---

## 15. Build order (tasks for `superpowers:writing-plans`)

Order: **accounts (A), Google Calendar auto-join (C), instant-Meet auto-join (I), several bots at once (D), verification submission (V).** Phase 0 runs in parallel and needs Adebayo. **Do not open sign-up (`signups_open`) or invite testers until A-phase ownership scoping is deployed, and do not invite more than one or two testers until Phase D is done**, or two overlapping meetings will fail. Every task is meant to be one small PR-sized unit with its own tests; task IDs are stable so the plan can reference them.

**Frontend dependency.** Every backend task is API-first and has no dependency on the Next.js screens. Each phase ends with a live-capture run (`scripts/capture_*`, as stream B did) and an update of `docs/fe-integration-guide-pilot-accounts-calendar.md`; the screens themselves are Module 2b stream D, which starts when streams A to C of that module are merged. Until a screen exists, the capture scripts and the admin's curl are the way to exercise a feature. The Jinja `/login` form gets an email field in A4 so the owner can still sign in while pages migrate; retiring Jinja belongs to Module 2b.

Model split (token economy): Sonnet builds and does per-task reviews; Opus reviews auth, OAuth tokens, the Pub/Sub endpoint and the dispatcher locking (marked **Opus**).

### Phase 0: prerequisites and spikes (parallel, no app code)

| Task | Owner | Done when |
|---|---|---|
| P0.1 Google Cloud project, billing, OAuth consent screen, Search Console verification of `beyrictech.com`, OAuth client (Testing) | Adebayo | Client ID and secret exist; Q7 decided |
| P0.2 Instant-Meet spike S1 to S7 and S9 (section 8.3) | session + Adebayo's accounts | Results table filled in; `INSTANT_MEET_MODE` and `INSTANT_MEET_PERSONAL_MODE` chosen |
| P0.3 Vexa checks S8: chat posting, per-key concurrency limit, bot listing, health endpoint | session | Notes in `docs/spike/` |
| P0.4 Mail provider: account, DNS (SPF, DKIM, DMARC), a test mail delivered | Adebayo | `MAIL_*` values in `.env` |
| P0.5 Measure bots per machine (M1) on the app server | Adebayo runs the SSH steps | `max_bots` for host `local` decided |
| P0.6 Choose and rent the second bot machine (Q3); must exist before Phase D's load test | Adebayo | A reachable server with Docker |

### Phase A: accounts

| ID | Task | Needs |
|---|---|---|
| A1 | Migration `0006` (users, tokens, oauth flows, waitlist, notifications, mail outbox, user settings, `app_state` columns with `signups_open` false) and models; admin creation from env; migration tests | none |
| A2 | Password hashing (argon2id, length rules, common-password list) and the one-time-token utility (generate, hash, compare, expire) | A1 |
| A3 | `Mailer` interface, `ConsoleMailer` (refused in production), `ResendMailer`, and the outbox sender with retries and scrubbing | A1 |
| A4 | **Opus** Session cookie to `{uid, csrf, iat, epoch, gepoch}`, per-user epoch, `guard()` loads the user, key derivation without `owner_password`, `request.state.user`; Jinja login form gets an email field; existing tests adapted, none deleted | A1, A2 |
| A5 | `/api/session` get/login/logout on email and password; per-email limiter; `email_not_verified` | A4 |
| A6 | Migration `0007` (ownership, `usage_entries`, meeting columns), backfill to admin, swapped unique constraints | A1 |
| A7 | Scope the **meeting** service functions and routes by user; foreign id gives 404; extend the route-enumeration test for meetings | A4, A6 |
| A8 | Scope **templates and vocabulary** the same way; `DEFAULT_TEMPLATES` copied at sign-up; `template_in_use` | A7 |
| A9 | Hours: write `recorded_seconds` and the usage entry in the pipeline's audio step; the hours formula and gate in `create_meeting`; usage in `/api/session`; the 4 h notice | A6, A7 |
| A10 | Sign-up endpoint with the cap lock, `pilot_full`/`pilot_closed`, `GET /api/pilot`, terms acceptance; 202 on every outcome | A3, A8 |
| A11 | Email verification (button-to-POST), resend with its limits, the 48 h unverified sweep, `password_signup_enabled` switch | A10 |
| A12 | Password reset (30 minutes, bumps epoch, no enumeration) | A3, A5 |
| A13 | Google sign-in, part 1: OAuth client wrapper, PKCE, `oauth_flows`, ID-token verification | A4 |
| A14 | **Opus** Google sign-in, part 2: matching order (sub, verified email, unverified email with password cleared), terms acceptance, `pilot_full` cookie flow | A10, A13 |
| A15 | Waitlist API and invite tokens with slot reservations and redeem on sign-up | A10 |
| A16 | Admin API part 1: users list and `PATCH`, pilot settings, last-admin guard, route tests | A4 |
| A17 | Admin API part 2: waitlist invite and the no-mail reset link; `scripts/delete_user.py` and the `deleting` job | A15, A16 |
| A18 | In-app notifications: table use, `GET /api/notifications`, read calls, unread count in `/api/session` | A1 |
| A19 | Nginx zones for the public auth routes; `deploy/` updates; deploy order doc: A6 to A8 deploy **before** A10 is switched on | A10 |
| A20 | Privacy and terms changes for accounts (12.3 rows "account", "Google sign-in", "How we use" and Terms "Pilot"); SOP; checklist; FE guide with live captures | all A |

### Phase C: Google Calendar auto-join

| ID | Task | Needs |
|---|---|---|
| C1 | Migration `0008` (connections, events, scheduled joins) and models | A6 |
| C2 | **Opus** Token vault (AES-GCM, AAD, key version, redacting `repr`, missing-key behaviour) and its settings | none |
| C3 | Connect flow: incremental consent, scope check, encrypted refresh token, `consent_required` | A13, C1, C2 |
| C4 | Disconnect flow (revoke at Google, delete everything listed in 12.2) | C3 |
| C5 | Calendar client: list with paging, `syncToken`, `410`, full resync, `events.get` | C2 |
| C6 | Event parsing (platform, link priority, Zoom passcode handling, Teams, all-day, event types) | none |
| C7 | `will_join` pure function with the full matrix test | C6 |
| C8 | Materialiser: events to `calendar_events` and `scheduled_joins` (create, move, cancel, revive); the plain-words module `app/calendar_text.py` | C1, C5, C7 |
| C9 | Key re-encryption script | C2 |
| C10 | `app.scheduler` skeleton: leader lock, two loops, injected clock, heartbeat, compose service, periodic-sweep framework | C1 |
| C11 | `place_bot()` with the single `local` host, the per-user and dedupe checks, and the whole no-capacity path (retry, alerts, `failed`); pasted links use it (`bot_busy`) | C10, A9 |
| C12 | Join runner: 3-minute re-check, pre-flight, dispatch, `Meeting` with `source=calendar`, restart catch-up, `joined` to `completed` hook in the pipeline | C8, C11 |
| C13 | Refresh-failure handling and the connection states (`needs_reconnect`, `degraded`), the 7-day early warning, revival on reconnect | C3, C10 |
| C14 | Disconnected notifications, banner data, email via the outbox | C13, A18 |
| C15 | API: upcoming, settings, override, send-now, not-recorded | C8, C12 |
| C16 | Deploy gate in `deploy/deploy.sh` (Q19) | C10 |
| C17 | Privacy rows "Calendar" and "How long we keep"; SOP; checklist; FE guide with captures from a real calendar | all C |

### Phase I: instant-Meet auto-join

| ID | Task | Needs |
|---|---|---|
| I1 | Incremental consent for `meetings.space.readonly` and the settings switch | C3, P0.2 |
| I2 | Migration `0009` (`pubsub_inbox`) and the **Opus** push endpoint (OIDC verification, insert-and-return, nginx zone) | P0.2 |
| I3 | Subscription manager: create, renew, reconcile, suspension handling, state flag | I1 |
| I4 | Inbox processor: conference, space, ownership rule, calendar-first rule, `scheduled_join` source `instant_meet`, dispatch | I2, C12 |
| I5 | `poll` mode behind the setting (built only if the spike says `events` fails for personal accounts and S5 allows it) | I1, C12 |
| I6 | Manual routes: "start a meeting with the note taker" (only if `spaces.create` works) and the PWA share-target notes for the frontend | I1 |
| I7 | Privacy rows "Meet" and "Who we share with"; SOP; FE guide | all I |

### Phase D: several bots at once

| ID | Task | Needs |
|---|---|---|
| D1 | Migration `0010` (`bot_hosts`, `meetings.bot_host_id`, seed `local`) | C11 |
| D2 | `vexa_for(host)` factory, per-host encrypted keys; every pipeline path (poll, download, delete, stop, retry) uses the meeting's host | D1 |
| D3 | Health checker in the scheduler | D1, P0.3 |
| D4 | **Opus** `place_bot()` generalised: per-host lock, free-slot counting, failover between hosts | D2, D3 |
| D5 | Per-user concurrency limit (2) wired to its skip reason | D4 |
| D6 | Forecast job and `GET /api/admin/capacity` | D4 |
| D7 | Admin host endpoints (`POST/PATCH /hosts`) | D1, A16 |
| D8 | Second host files in `deploy/vexa-host/` and the private-link instructions (Adebayo does the SSH steps) | P0.6 |
| D9 | Load test with 3 bots (M2); set each host's `max_bots`; worker replicas to 2; session release between pipeline steps | D8 |
| D10 | SOP; checklist; FE guide | all D |

### Phase V: verification submission

| ID | Task |
|---|---|
| V1 | Re-read the live consent screen against the policy and run the "claims versus app" check from 12.3 |
| V2 | Demo video script and recording: sign-in, connect with the real consent screen, each scope's use (Upcoming list, a joined meeting, instant-Meet start), disconnect and deletion; upload unlisted to YouTube. Google's guidance: "fully demonstrates how a user initiates and grants access to the requested scopes" |
| V3 | Scope justifications (drafts below) entered in the console |
| V4 | Submit; answer the reviewer (Google says the sensitive review "can take up to 10 days"); on approval confirm the app is in production |

**Draft scope justifications.**
- `calendar.events.readonly`: "Beyric Note Taker joins the user's online meetings to take notes. It needs to read the title, time, organiser flag, reply, attendee count and meeting link of events on the user's primary calendar to know which meetings to join and to show them in the Upcoming list. A narrower scope (`calendar.events.owned.readonly`) would exclude meetings the user is invited to. We do not write to the calendar."
- `meetings.space.readonly`: "When the user enables 'join Meet calls I start myself', we need to learn that a Meet call they own has started, and read its meeting link, so the note taker can join. We do not read recordings, transcripts or chat."

---

## 16. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Events API does not work for personal Gmail | Instant-Meet promise fails for most testers | Spike first; `poll` mode; manual routes; honest wording (section 8) |
| `conferenceRecords.list` cannot tell owner from participant | `poll` mode would join meetings he did not start | Do not ship `poll` then; manual routes |
| Capacity is wrong (CPU binds, not memory) | Bots die or lag | M1 and M2 measure first; `max_bots` starts at 1; admin alerts |
| Two overlapping meetings with no second machine | Unrecorded meeting | Do not invite testers before Phase 4; forecast warning; bounded retry |
| Google verification delayed or rejected | Weekly reconnects or the unverified screen | Q7 bridge; early-warning banner; clear scope justifications; policy matches the app |
| Mail provider not ready | No verification or reset mail | Google-only sign-up; admin-issued reset links; password sign-up off by setting |
| Token theft | Calendar access for ten users | AES-GCM, key apart from backups, revoke on disconnect, no logging, short list of readers |
| Deploys during auto-joins | A bot killed mid-meeting | Deploy gate (Q19), restart catch-up in the scheduler |
| Bot blocked by Zoom or Google at higher volume from one IP | Failed joins | Existing Zoom retry; per-reason messages; second host adds an IP |
| Scheduler single point of failure | No auto-joins | Heartbeat shown on admin page; alert after 5 minutes without a beat; leader lock allows a second replica |
| Consent and recording laws | Legal exposure for users and Beyrichq | Section 10 notice; terms put responsibility on the user; lawyer read (Q21); this is not legal advice |
| Next.js migration moves under this work | Rework | Everything here is API-first; screens come in Stream D |
| Hours-cap overshoot by running meetings | A user exceeds 5 h | Accepted by decision; bounded by the 2-bot per-user limit |

---

## 17. Operate and roll back

- **Deploy:** each phase is its own PR(s) and deploy; `0006`/`0007` need the app stopped only for the seconds the migration runs (the existing `migrate` service does this).
- **Roll back:** revert the PR; `alembic downgrade` to the previous revision (0007 downgrade restores single-owner uniqueness, fails if two users hold a same-named template). Revert from Phase 2 onward is safe to do at any time: calendar tables are additive.
- **Kill switches (no deploy):** `app_state.signups_open`, `password_signup_enabled`, `INSTANT_MEET_MODE=off`, `bot_hosts.enabled`, per-connection pause (admin), and the pilot cap and hours settings.
- **Secrets:** `TOKEN_ENC_KEY` must be backed up separately and safely; losing it forces every user to reconnect.
- **Monitoring:** scheduler heartbeat, worker heartbeat (exists), host health, mail failures, count of `failed` and `no_capacity` joins in the last 24 hours, all on the admin capacity screen.

---

## 18. Follow-ups (not in this version)

Outlook; Teams links; Calendar push notifications; notes-ready email; in-app account deletion; two-factor sign-in; sharing a meeting between users; teams and invitations; reading secondary calendars; auto-scaling bot machines; Zoom RTMS; a status page.

---

## 19. Sources

- Meet events guide: https://developers.google.com/workspace/events/guides/events-meet (user target, event types, "Limitations": invitees)
- Meet events overview: https://developers.google.com/workspace/meet/api/guides/events-overview
- Create a Workspace Events subscription (prerequisites: billing, publisher service account, user authentication): https://developers.google.com/workspace/events/guides/create-subscription
- Events API authorisation scopes for Meet (`meetings.space.created`, `meetings.space.readonly`, both sensitive): https://developers.google.com/workspace/events/guides/auth
- Meet REST API overview and meeting spaces: https://developers.google.com/workspace/meet/api/guides/overview and https://developers.google.com/workspace/meet/api/guides/meeting-spaces-overview
- `conferenceRecords.list` reference (scopes, filter fields, page size): https://developers.google.com/workspace/meet/api/reference/rest/v2/conferenceRecords/list
- Meet release notes (events GA 15 Feb 2024; invitees receive `conference.v2.started` from 12 Nov 2025): https://developers.google.com/workspace/meet/release-notes
- Calendar push notifications (HTTPS, no auto renewal, "not 100% reliable"): https://developers.google.com/workspace/calendar/api/guides/push
- Calendar events resource (`hangoutLink`, `conferenceData`, `organizer.self`, statuses): https://developers.google.com/workspace/calendar/api/v3/reference/events
- Calendar scopes list: https://developers.google.com/workspace/calendar/api/auth
- OAuth app status, 7-day test expiry, 100-user cap: https://support.google.com/cloud/answer/15549945
- OAuth brand and sensitive-scope verification (public homepage, same-domain policy, demo video, 10-day review): https://developers.google.com/identity/protocols/oauth2/production-readiness/brand-verification and https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification
- Secondary, unofficial (labelled as such above): Recall.ai on creating and recording Google Meets programmatically, https://www.recall.ai/blog/create-a-google-meet-programmatically; a GitHub pull request reporting a personal-Gmail API Explorer test (`topoteretes/cognee-community` PR 416).

---

## 20. Builder-check result

A fresh Sonnet subagent read only this spec (first version, 725 lines) and returned **40 questions**. Every one was resolved by a change in this document; none was left open or sent to Adebayo except where it became a Q-number above. In order of severity:

| Builder's question (short) | Fixed in |
|---|---|
| Migrations referenced tables created in a later revision; circular FK `meetings` and `scheduled_joins` | 5.1 and 5.4 rewritten: `0006` to `0010` each create only what exists; one-way link `scheduled_joins.meeting_id` |
| Sign-up shipped before ownership scoping; `signups_open` default | `signups_open` default false; A6 to A8 before A10; deploy-order task A19 |
| Single global bot lock leaks other users' activity in Phases 2 and 3 | 9.4 "Phasing": `place_bot()` and the no-capacity path from Phase C, neutral `bot_busy` message |
| `joined` never ended; undefined transitions; "bot never got in" | 7.7: `completed`, exit table, the meeting is the truth after dispatch |
| What backs "Not recorded" rows; send-now rules | 7.7 and 13: `GET /api/not-recorded`, send-now never bypasses pre-flight |
| 10 vs 15 minute deadline; instant-Meet times; late events | One deadline (start + 15 min) everywhere (Q14, 9.5, 7.7, 5.1); 7.8 late-join rule |
| Instant path could join a calendar meeting the user switched off, or one they did not start | 8.4 ownership rule (calendar first; owner only), spike S9 |
| Instant-Meet consent and Calendar scope coupling; user id; refresh token replacement | Q11, 5.1 (`calendar_connections` is the Google connection), S1, 13 (`consent_required`) |
| Pub/Sub hand-off to another process undefined | `pubsub_inbox` (5.1, 8.4) |
| `syncToken` with `timeMin`; pagination; full resync | 7.4 |
| Hours formula and who sets `bot_joined_at` | 6: exact formula, no new timestamps |
| Unverified users fill slots; invites; redeem; `closed`; Google `pilot_full` without PII in a URL | 4.3 and 4.4 |
| Google linking collisions and pre-registration hijack | 4.3 (password cleared on linking an unverified row) |
| Unverified session contradiction; sign-up enumeration; resend limits | 4.3 |
| Deletion semantics, re-sign-up resetting hours, suspended users | 4.6, 5.1 (`email_hash`) |
| Notification de-dupe, admin alert storage, heartbeat | 5.1 (time-bucketed keys, admin rows, `scheduler_heartbeat_at`) |
| What survives a disconnect; Zoom passcodes | 12.2 exact lists |
| `mail_outbox` payload scrubbing; ConsoleMailer in production | 5.1, 12.2 |
| Template defaults, ON DELETE, `user_settings` creation, `notify_email` | 5.1, 5.4, 13 |
| Override semantics and materialising; will-join inputs; solo events; several links | 7.5, 7.7 |
| State vocabulary mismatch; reason strings | 13 (`app/calendar_text.py`, state list) |
| Cross-source dedupe and `native_id` normalisation | 7.8 |
| Placement details (rollback, lock timeout, which statuses count, draining) | 9.4 |
| Scheduler leader lock, tick, loops, periodic jobs | 9.7 |
| Q8 pointed at 7.8; `degraded` state; meet subscription state | Q8, 5.1, 7.9 |
| `GOOGLE_OAUTH_TESTING` meaning; 100-user cap vs sign-in | Q7 |
| Cookie schema, SameSite, Jinja login | 4.2, A4 |
| `return_to`, redirect URIs, nginx zones for all public routes | 12.1, 12.2 |
| Password length cap, common list, limiter storage, lock-out DoS, terms capture | 4.3 |
| Missing admin endpoints, last-admin guard, instant mode per user | 13, 5.1, 8.4 |
| AAD at first insert; encryption key at migration; decrypt failure | 12.1, 5.1 (`api_key_enc` null for `local`) |
| Missing numbers | Appendix 21 |
| Task dependencies, task size, FE dependency, undesigned screens | 15 rewritten (IDs, "Needs" columns, small tasks, frontend-dependency paragraph), 13 screens list |
| Policy rows mapped to tasks | 12.3 |
| Reference defects; empty section 20; assumptions without Q numbers; `hello@` | Q8 fixed, this section filled, Q23 added, Q4/Q5/Q13/Q21 are referenced where they apply |
| Per-user cap meaningless before a second host; when the second machine must exist | 9.4 item 7, P0.6, section 15 intro |
| Recording-notice conditionals; first-name source | 10 items 5 to 7 |
| API contract: error codes, pagination, pilot shape, unread count | 13 |

The fixed spec has not been through a second builder check; the changes were mechanical consequences of the 40 answers.

---

## 21. Appendix: small assumptions and numbers

**Small assumptions (all covered by Q23):** a `tentative` or unanswered invite is joined; a solo event with a link is joined under rule `all`; a switch on a recurring event applies to that occurrence; a meeting moved after the bot was sent is ignored; a link added or removed after dispatch is ignored; events spanning midnight are handled by timestamps; shared or delegated calendars are not read; with several links on one event the first supported one in the 7.5 priority order wins; the hours-cap text points to `hello@beyrictech.com`; unverified accounts are deleted after 48 hours; notifications are kept 90 days; sent mail rows 30 days.

**Numbers in one place**

| Thing | Value |
|---|---|
| Calendar sync | every 5 min per connection; full resync every 6 h; window now - 1 h to now + 14 days |
| Join | re-check at start - 3 min; dispatch at start - 2 min; deadline start + 15 min |
| Capacity retry | every 15 s until the deadline |
| Scheduler | dispatch loop 10 s; sync loop wakes 60 s; leader failover about 10 s; heartbeat each tick; alert after 5 min without |
| `send_bot` | existing 30 s client timeout; up to 3 tries 15 s apart on different hosts before `failed` |
| Host health | ping every 30 s, 3 s timeout, healthy if seen in the last 2 min |
| Per-user concurrent bots | 2 |
| Tokens | verify link 24 h; reset link 30 min; invite 7 days; OAuth state 10 min |
| Login limits | per address as today (5 failures per 15 min); per email 10 per 15 min |
| Password | 10 to 128 characters; argon2id |
| Google error alert | a warning for the user after 30 min of failures; the admin is told when 3 or more users are affected within 30 min |
| Retention | calendar events 2 days after ending; unverified accounts 48 h; `pubsub_inbox` 7 days; notifications 90 days; sent mail 30 days; audio 7 days (existing) |
| Nginx | `api_auth` 10/min per address burst 10; waitlist 5/min burst 5; Pub/Sub 20/s burst 40 |
