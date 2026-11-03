# Vexa's calendar sync (secret ICS address) against our Google OAuth design

**Written:** 2026-11-03. Source of the Vexa facts: https://docs.vexa.ai/how-to/calendar-sync (read 2026-11-03). Everything marked *(not verified)* is general knowledge or my estimate, not something checked against Google, Microsoft or a live feed.

## What Vexa actually does

Adebayo saw the Vexa dashboard connect to Google Calendar and Outlook. It does **not** use sign-in with Google or Microsoft. The page says: "No OAuth, no calendar permissions."

| Step | What the user does |
|---|---|
| Google | Settings, then the calendar, then Integrate calendar, copy **Secret address in iCal format** (`…/private-…/basic.ics`) |
| Outlook / Microsoft 365 | Outlook on the web, Settings, Calendar, Shared calendars, **Publish a calendar**, "Can view all details", copy the **ICS** link |
| In Vexa | Dashboard, Calendar, name it, paste the link, choose auto-join, Connect (an immediate sync runs and shows counts or a named error) |

Vexa then polls that URL (hosted: every 60 s; self-hosted: every 5 min, `CALENDAR_SYNC_INTERVAL_S`), imports every event that has a Meet, Zoom or Teams link, and sends the bot from 60 s before the start until 10 min after. Other rules from the page: recurring series import only the **next** occurrence; feeds are capped at 2 MB; up to 10 connections per user; moves and cancellations update a meeting only while it is still planned; a leaked link is rotated with Reset in Google Calendar. Self-hosted Vexa ships the API (`POST /user/calendars` with `ics_url`, `auto_join`, `bot_name`; `GET`/`PATCH`/`DELETE`; `/sync`) but not the dashboard; the dashboard is hosted-only.

## Against our design (spec section 7: Google OAuth, `calendar.events.readonly`, poll every 5 min plus a recheck 3 min before)

| | Secret ICS link | Google OAuth (our spec) |
|---|---|---|
| **Setup for the user** | About 6 to 8 steps, buried in calendar settings, desktop web only *(not verified for mobile)*, then copy and paste. Many users will not find it | Two clicks: "Connect Google Calendar", then Google's consent screen |
| **Workspace and company accounts** | The secret address is often hidden until a Workspace admin allows external sharing; Microsoft tenants can block publishing *(the Workspace part is on Vexa's page; the Microsoft part is not verified)* | A Workspace admin can also block third-party apps, but a verified app is usually allowed |
| **Providers covered** | Google, Outlook, Microsoft 365, Apple, anything that publishes ICS, with one code path | Google only; Microsoft needs its own app registration, consent and publisher verification |
| **Freshness** | Provider publication time plus one polling interval. Google's secret feed is widely reported to lag by minutes to hours *(not verified; a 30-minute spike can measure it)*. A meeting created 20 minutes before it starts may not be in the feed in time | Events API is live on every poll; a recheck 3 min before the join catches late changes |
| **Recurring meetings** | Vexa imports the next occurrence only; we would have to expand the series ourselves | `singleEvents=true`, instances arrive individually |
| **Instant Meet calls (not on the calendar)** | Impossible: they are not in the feed. Needs the Workspace Events path, which needs OAuth anyway (Phase I) | Same OAuth connection can later add the Meet scope |
| **Security of what the user hands over** | The link is a bearer secret that reads the **whole** calendar (titles, attendees, descriptions, links) with no scope, no audit trail at Google, and it can leak in logs or backups. Revoking means Reset in Google and re-pasting | Scoped, revocable in the user's Google account, refresh token we encrypt at rest (`TOKEN_ENC_KEY`) |
| **Our attack surface** | We would fetch a **user-supplied URL** from our server: an SSRF risk unless we allowlist calendar hosts, force https, refuse redirects and cap size and time | The URLs are Google's, fixed |
| **Google review burden** | None (no Google API data). Privacy policy needs no "Google API Services User Data" wording | The sensitive scopes need Google's verification. Adebayo says the app is verified; the approved scopes still need confirming |
| **Token expiry and "disconnected" states** | None; the link works until Reset | Test-mode 7-day expiry gone once verified; revoked or `invalid_grant` states still need the banner in section 7.9 |
| **Declined or "organised by me" rules** | Possible from `ORGANIZER` and `ATTENDEE partstat` if we know the user's email, which we do | Native in the event data |
| **Delay to ship** | Smallest: parse ICS, store link, same scheduler | Already designed; needs the Google project and the scope check |

## Should we use Vexa's own calendar API instead of building the scheduler?

Not recommended, even though self-hosted Vexa exposes it. Our product owns things Vexa's sync does not know about: the per-account **hours cap checked before every dispatch**, per-user ownership of the resulting meeting, the **capacity registry** (a queue is not acceptable), the 2-minute lead time and 15-minute late window we chose, the rules "all / only ones I organise / only when I turn it on", and the "Can't join" reasons on the Upcoming list. If Vexa joined on its own we would learn about the meeting afterwards, could not gate on hours or capacity, and would depend on Vexa's per-user model *(how Vexa users map to our accounts is unchecked)*. Keep the scheduler ours and treat Vexa as the bot layer only.

## Recommendation (for Adebayo to confirm)

1. **Google OAuth stays the main path.** For ordinary users it is a two-click flow against a six-step paste, it is fresher, it handles recurring series and later instant-Meet, and the verification cost is already paid.
2. **Add the secret-ICS link as a second, optional source**, behind one calendar-source interface, for **Outlook and Microsoft 365 users and anyone who will not grant Google access**. That brings Outlook into the pilot at a fraction of the Microsoft app-registration cost (the pilot answer was "Outlook later"; this changes that to "Outlook through a pasted link, slower"). Label it honestly in the UI: "Updates can take a while; meetings made just before they start may be missed."
3. **If we build ICS:** the SSRF guard above is mandatory; store the link encrypted and show it masked; expand recurring rules ourselves; show a first-sync result like Vexa does (counts or a named error), because paste errors are the main support cost.
4. Do not promise Outlook auto-join in the privacy policy or landing copy until it ships.

## What would settle the unverified parts

| Question | Cheapest test |
|---|---|
| How stale is a Google secret feed? | Create a calendar event with a Meet link, poll the secret address every minute, record when it appears. Repeat for an edit and a cancel |
| Does Outlook "Publish a calendar" lag, and can a Microsoft 365 tenant block it? | Same test on a personal Outlook account; ask one company user to find the Publish option |
| Does the Google ICS carry the Meet link reliably? | Inspect the feed for `X-GOOGLE-CONFERENCE` and the description text |
| How would Vexa users map to our accounts if we ever used its calendar API? | Read Vexa's user and API-key docs; only matters if we revisit the decision above |

These are queued in `docs/superpowers/plans/2026-11-03-pilot-accounts-calendar.md` as the optional "C-ICS" block once the pilot-plan session lands it, pending Adebayo's decision.
