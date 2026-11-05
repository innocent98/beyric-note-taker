# Outlook M0: live capture run (throwaway)

**Purpose.** Before any Microsoft code is written (M2 OAuth client, M5 event reader), find out what
Microsoft really answers: the sign-in token response, the consent screens, the shape of each kind
of calendar event, the error answers. `capture.py` signs a real account in through a *local
development* app registration and records the real answers, redacted, to
`docs/spike/outlook-oauth-captures.md` and `docs/fe-captures/calendar-outlook/graph/`. It answers
S1 to S7 of `docs/superpowers/specs/2026-11-04-outlook-oauth-design.md` section 11.

**This is not part of the app.** Nothing under `app/` imports it; it is never deployed. It reads
the calendar only (`Calendars.Read`, `User.Read`), writes nothing at Microsoft, and keeps the
access token in memory. Its one secret file is git-ignored.

## A. One-time setup (about 10 minutes, Adebayo)

Use the *second* registration of spec 9.1 step 12, not the production one (production has no
`localhost` redirect, and we do not want a development secret on it).

1. Open `https://entra.microsoft.com`, sign in to the `beyric.onmicrosoft.com` tenant.
2. **Entra ID** > **App registrations** > **New registration**.
3. Name `Beyric Note Taker (local development)`.
4. Supported account types: **"Accounts in any organizational directory (Any Microsoft Entra
   directory - Multitenant) and personal Microsoft accounts"** (the same as production).
5. Redirect URI: platform **Web**, exactly `http://localhost:8000/api/calendar/microsoft/callback`.
   Register.
6. **API permissions** > **Add a permission** > **Microsoft Graph** > **Delegated**: tick
   `Calendars.Read`, `Calendars.ReadBasic`, `User.Read`, `offline_access`, `openid`, `profile`.
   (`Calendars.ReadBasic` is there only so the run can compare it with `Calendars.Read`, spec
   4.2. Do **not** press "Grant admin consent".)
7. **Certificates & secrets** > **New client secret**, description `m0-local`, expiry **3 months**.
   Copy the **Value** now: it is shown once.
8. In this folder, copy `.env.m0.example` to `.env.m0` and put the **Application (client) ID** and
   the secret **Value** in it. `.env.m0` is git-ignored. Do not paste either into a chat. The lead
   or you can run: `cp docs/spike/outlook-m0/.env.m0.example docs/spike/outlook-m0/.env.m0` and
   edit it in an editor.
9. Money rule: if any page asks for a card, a phone number for payment, or shows a price, stop.

## B. Test events (so the run has something to read)

In the spare **Outlook.com** calendar (and a work mailbox if you have one), create these, in the
next 30 days: a Zoom link in the body; a Google Meet link in the body; a Teams meeting (use "Add
Teams meeting" in Outlook on the web); an all-day event; a recurring series with one moved
occurrence; a cancelled meeting (invite yourself from another account, then cancel); a declined
invite; a private event. Have a few of them with other attendees.

## C. The runs

Stop any app listening on port 8000 first (the script listens there for the redirect). From the
repo root, with the project's venv:

| Run | Command | Sign in with | Closes |
|---|---|---|---|
| 1 | `.venv/bin/python docs/spike/outlook-m0/capture.py run --label personal --authority consumers` | the spare **Outlook.com** account | S1, S2, S3, S4, S6, S7 |
| 2 | `... run --label personal-basic --authority consumers --scopes basic` | the same account | S3 (does `Calendars.ReadBasic` return `onlineMeeting`, `location`, `responseStatus`, `organizer`?) |
| 3 | `... run --label work --authority organizations` | a **work/school** mailbox, ideally another tenant | S1 (work `tid`), S2 (blocked?), S4 (`/organization`), S5 (Safe Links) |
| 4 | `... run --label work-nomailbox --authority organizations` | an account with **no mailbox**, if you have one (a user in `beyric.onmicrosoft.com` without an Exchange licence is exactly that) | S6 (`MailboxNotEnabledForRESTAPI`) |
| 5 | `... after-revoke --label personal` | nothing: first **remove the app** at Microsoft (below), then run | S6 (what a revoked grant answers) |

Each `run` opens your browser at Microsoft's sign-in page. At the consent screen **take a
screenshot** (save under `docs/spike/outlook-m0/screens/`, they are not secret but check for your
address) and note whether it says "unverified" or "needs administrator approval". The terminal
then asks two plain questions (what the consent screen said, and the URL of the page where you
remove an app's access); answer them. Nothing else is typed.

**Removing the app before run 5.** Personal account: sign in at `https://account.live.com/consent/Manage`
(or the page you reported in run 1), find *Beyric Note Taker (local development)*, **Remove these
permissions**. Work account: `https://myapps.microsoft.com`, the app's menu, **Revoke permissions**.

**Cleanup.** When all runs are done: delete `docs/spike/outlook-m0/out/` (it holds the raw,
unredacted answers and one refresh token) and delete the client secret in Entra (step A7 > the
bin icon). Commit the redacted outputs on a `docs/outlook-m0-captures` branch.

## D. What is NOT reproducible live (the report says so)

A client secret expiring, 429 throttling, Conditional Access blocking, and error code 700082 (a
refresh token idle for 90 days) cannot be provoked in an afternoon. The report marks them "not
verified".

## E. Check the script itself without any secret

`.venv/bin/python docs/spike/outlook-m0/capture.py selftest` checks the redaction and PKCE with
no network. Run it after any edit.
