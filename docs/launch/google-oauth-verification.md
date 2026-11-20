# Google OAuth verification (sensitive scope): everything needed to start now

Prepared 2026-11-20. Docs only; nothing here changes the app. Public repo: no secrets, no personal addresses.

**How to read the marks.** Every statement about what Google requires carries a source tag like `[G1]` that points to the table in section 1 (URL and the date it was read). A statement about our own app carries a file and line. **NOT VERIFIED** means a Google page did not say it, or could not be read; it is never filled from memory. Google's pages show no "last updated" date except the User Data Policy page (15 February 2024); the dates below are the days they were read.

## 0. The short version

| Question | Answer |
|---|---|
| Do we need verification? | Yes for the Calendar scope: apps that request sensitive or restricted scopes "must complete" verification [G1]. Google's own sensitive-scope page names reading Calendar events as a sensitive example [G3]. The exact class of `calendar.events.readonly` is shown in the Cloud Console when the scope is added; no page I read states it per scope (section 3). |
| What do we ask Google for today? | Sign-in: `openid email profile`. Calendar connect: `openid email` plus `calendar.events.readonly`. The Meet scope exists in code but **no screen in the app asks for it** (section 3). |
| Restricted scopes or a security assessment? | No. The security assessment (CASA) applies to restricted scopes only [G2][G6]. We request none. |
| How long? | Google's estimates, "not guaranteed": sensitive scope 10 business days; brand verification 2 to 3 business days [G5]. The developer page says "up to 10 days" [G3]. Slower if we are slow to answer [G5]. |
| What the unverified app costs us now | Users see the unverified-app screen, and a **lifetime cap of 100 new users** applies after the app first shows that screen; it "cannot be reset or changed" and is lifted only by verification [G4][G5]. Every Google calendar connect spends one. |
| What only Adebayo can do | Cloud Console clicks, Search Console, record and upload the video, press Submit. Section 12. |
| What blocks us | Two small decisions and one code question (final scope, Meet text in the policy, product name). `docs/launch/verification-open-questions.md`. None needs a lawyer. |

## 1. Sources read (the provenance table)

Read 2026-11-20. "Fetch agent" means the `web-fetch` agent read the page with WebFetch and reported what it said; I did not open these pages in a browser myself. Where an agent could only read part of a page, that is said.

| Tag | URL | Read | Used for |
|---|---|---|---|
| G1 | https://support.google.com/cloud/answer/13463073 (OAuth App Verification Help Center) | 2026-11-20, fetch agent, full | Who must verify; non-sensitive apps may skip; links to the pages below |
| G2 | https://support.google.com/cloud/answer/13464321 (Verification requirements) | 2026-11-20, fetch agent, full | Homepage, privacy policy, authorized domains, narrowest-scope rule, Limited Use, demo video, security assessment scope |
| G3 | https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification | 2026-11-20, fetch agent, full (twice) | The 13 submission steps, the demo video rules (YouTube, Unlisted, client ID in the address bar), "up to 10 days", Verification Center, the justification rule and its Calendar example |
| G4 | https://support.google.com/cloud/answer/15549945 (Audience) | 2026-11-20, fetch agent, full | Testing versus In production, the 100-user cap, the 7-day token rule for test users and its exception for sign-in-only scopes |
| G5 | https://support.google.com/cloud/answer/13463817 (FAQ) | 2026-11-20, fetch agent, full | Estimated times, what happens while pending and on rejection, how to answer the reviewer |
| G6 | https://support.google.com/cloud/answer/13461325 (Submitting your app for verification) | 2026-11-20, fetch agent, full | Consent-screen fields, steps, "Prepare for Verification", who gets the emails |
| G7 | https://developers.google.com/terms/api-services-user-data-policy (API Services User Data Policy; "Last updated February 15, 2024" on the page) | 2026-11-20, fetch agent, full | Limited Use (three numbered requirements plus the prohibited list), privacy policy duty, minimum access |
| G8 | https://developers.google.com/identity/protocols/oauth2/production-readiness/brand-verification | 2026-11-20, fetch agent, full | Brand verification, domain verification in Search Console, 7-day validity |
| G9 | https://developers.google.com/identity/protocols/oauth2/production-readiness/policy-compliance | 2026-11-20, fetch agent, full | "Choose the most minimal data access", all scopes must be in the consent screen config |
| G10 | https://support.google.com/cloud/answer/15549049 (Branding) | 2026-11-20, fetch agent, full | Field-by-field branding rules, logo limits, support email rule |
| G11 | https://support.google.com/cloud/answer/15549135 (Data Access) | 2026-11-20, fetch agent, full | Per-scope justification and video, "do not immediately include" a new sensitive scope |
| G12 | https://developers.google.com/identity/protocols/oauth2/scopes | 2026-11-20, fetch agent. **Truncated**: the Calendar table was in the verbatim part; Meet and `openid`/`email`/`profile` rows were not | Exact descriptions of the Calendar scopes |
| G13 | https://developers.google.com/workspace/calendar/api/v3/reference/events/list and `.../events/get` | 2026-11-20, fetch agent, full | The nine scopes that authorize `events.list` and `events.get` |
| G14 | https://developers.google.com/workspace/calendar/api/auth | 2026-11-20, fetch agent, full | Meaning of the Calendar scopes |
| G15 | https://support.google.com/cloud/answer/13464018 (changes to an approved app), https://support.google.com/cloud/answer/13463816 (annual re-verification), https://support.google.com/cloud/answer/13464323 (when verification is not needed) | 2026-11-20, fetch agent, full | What re-triggers review, who must re-verify yearly, exemptions |

**Could not be read or answered by any page I read (all marked NOT VERIFIED where used):**

| Item | State |
|---|---|
| https://support.google.com/cloud/answer/13463071 (the FAQ address in the task) | HTTP 404; replaced by G5 |
| Sensitive or restricted class of each Calendar scope | G12 and G14 show a description only. The class appears as a lock icon or "sensitive" tag in the Console. https://developers.google.com/workspace/guides/configure-oauth-consent (linked "Scope categories") was **not fetched** |
| Demo video length; number of videos; whether one video may cover several scopes | Not stated. The pages say the video must show each scope's feature [G3][G11], which implies one video can cover several, but no page says so |
| Whether reviewers want test credentials | Not mentioned on any page read |
| Whether the app must be "In production" before submitting | G6 says apps in development, testing or staging are "not applicable for verification". G11 asks only that branding be published first. The cloud project is already In production per the checklist (P0.9, 2026-11-03) |
| Search Console click path | Not on a Google page I read (only "verify ownership ... in the Google Search Console" [G3]). Section 9 gives the usual path and marks it |
| `meetings.space.readonly` description and class | Not in the verbatim part of G12 |
| Google Search Console's own help pages | Not read |

## 2. What Google requires today

All from the pages above. Where two pages repeat a rule, one tag is shown.

**Who must verify.** Apps requesting sensitive or restricted scopes "must complete" verification before access is granted [G1]. Apps with only non-sensitive scopes need none and can use a lighter brand verification [G1].

**Public pages.**
- Home page: publicly reachable, not only for logged-in users; says what the app does; "can not be only a login page"; on a domain you own and have verified; links to the privacy policy (same link as on the consent screen); a Play Store or Facebook page does not count [G2][G3][G10].
- Privacy policy: on the home page's domain; linked from the home page and from the consent screen; must disclose "how the app accesses, uses, stores, or shares" Google user data; kept accurate; in-product notices prominent [G2][G3][G7].
- Terms of service: a link is required for all external production apps [G10].
- Home page, privacy and terms links must all be filled before verification can be submitted [G10].

**Domain.** Every domain used (home page, privacy, terms, redirect URIs) must be an authorized domain whose ownership is verified in Google Search Console by a project Owner or Editor; the top private domain is verified (a subdomain such as `sub.example.com` needs `example.com`) [G3][G8][G10].

**Scopes.**
- Ask for the narrowest scope that works; the justification must say why narrower scopes will not do and what breaks [G2][G3][G11]. No scopes for "future enhancements" [G2][G7].
- Every scope the production app uses must be declared in the consent screen configuration [G9].
- A new sensitive scope must not be requested by the app until it is approved [G11]. A later scope change means a new review [G6].

**Limited Use** (G7, "Additional Requirements for Specific API Scopes", item 2; it applies to the raw data and to data derived from it). The page has three numbered requirements, not four:
1. Use only to provide or improve user-facing features that are prominent in the app.
2. No transfer except: to provide those features, with the user's consent; for security (for example abuse); to comply with law; for a merger or sale, after explicit prior consent.
3. No human reads it except: the user agreed to view specific data; for security; as the law requires; or when aggregated for internal operations under applicable law.
Separately prohibited: selling or giving it to ad platforms, data brokers or resellers; using it for ads (including retargeting); using it for creditworthiness or lending [G7]. The policy text has no AI training clause; G5 (FAQ, Limited Use section) says user data may not be used to train or improve foundational or frontier models, and G2 says the video must show explicit consent if data is used for AI training. Our policy forbids AI training on Google data either way (section 7).
There is **no required wording** for a Limited Use statement on a Google page I read; the policy must "fully document" data use [G7]. We keep the sentence the policy already carries.

**Demo video** [G2][G3][G11] (the details the pages state):
- Upload to YouTube Studio, Visibility **Unlisted**, and paste the link [G3].
- Show the full OAuth grant flow in **English** (the consent screen language toggle is bottom-left) [G2][G3].
- Show the consent screen with the app name [G3] and "the browser address bar ... includes your app's OAuth client ID" [G3].
- Show the exact scopes requested on the consent screen [G2][G11]. Show the app feature each sensitive scope enables [G3].
- Show the same app, name and branding as submitted [G2]. If the app has several OAuth clients, the video must include all clients assigned to the project [G11].
- Narration is suggested [G11]. Length: not stated.

**Branding rules** [G10]: the app name must "distinctively represent" the business, no Google names, no generic words such as "app" or "mobile"; it shows on the consent screen only after verification. Support email: a Google Group the user manages, or the signed-in user's registered Google account. Logo: JPG, PNG or BMP, at most 1 MB, square, 120 by 120 px recommended. Developer contact: Google "will send notifications" there.

**Process and timing.** Enter branding, **Verify Branding** (automated, "usually completes in a few minutes"; manual review "2-3 business days" [G8][G3]); a compliant result is valid for 7 days, so publish branding promptly [G8][G10]. Data-access verification needs published branding first [G3][G11]. Review mail goes to the project owners, editors and the developer contact; to reply, answer the last email from the verification team [G5][G6]. Rejection: the unapproved scopes stop working; resubmit all materials; no limit on resubmissions is stated [G5].

**After approval.** Changing the app name, logo, homepage, privacy link or a redirect URI needs brand verification again [G15]. Adding a sensitive scope needs a justification and a submission [G15]. Annual re-verification applies to restricted-scope apps only (security assessment every 12 months) [G15]; the pages I read do not describe a yearly step for sensitive-only apps.

## 3. What we ask Google for, and why (from the code)

| Scope | Where requested | File and line | Why |
|---|---|---|---|
| `openid email profile` | Sign in with Google | `app/google_oauth.py:41`, used at `:123` | Create or find the account: name, address, Google account id. Nothing else is read. |
| `openid email` | Calendar connect (identity part) | `app/google_oauth.py:151` | Record which Google account was connected, and its Workspace domain, so a calendar of a different account is refused. `profile` is not asked there. |
| `https://www.googleapis.com/auth/calendar.events.readonly` | Settings, Connect Google Calendar | `app/google_oauth.py:46`; the only button asks `features: ["calendar"]` at `web/lib/api/client.ts:617` | Read upcoming events so the note taker can join the meetings that have a video link. |
| `https://www.googleapis.com/auth/meetings.space.readonly` | **Not requested by any screen.** The API still accepts `features: ["meet"]` | `app/google_oauth.py:47`, `app/calendar_api.py:74` | Built for a Meet-push idea that the checklist parks (P0.2 spike, P0.10 test). Do not declare it. |

Calls made with the Calendar scope: only `GET .../calendars/primary/events` and `.../events/{id}` (`app/gcal_client.py:5,48`), `singleEvents=true`, from one hour ago to 14 days ahead (`app/gcal_client.py:49-50`), then incremental sync. Nothing is created, changed or deleted. The token request asks for offline access and consent each time (`app/google_oauth.py:151-158`), PKCE S256, single-use state.

**What we keep from an event** (`app/models.py:610-655`, `app/gcal_events.py:1-8`): id, title, start and end, meeting link and platform, whether the user is the organiser, whether the organiser is in the user's own Workspace domain (a yes or no; the address is not kept), the number of attendees (not their addresses), the user's own reply, status, all-day and event type. Description and location are read only to find a meeting link and are dropped. Rows are deleted two days after the event ends (`app/sweeps.py:9`) and on disconnect (`app/calendar_connect.py:518`).

**Token handling.** Only the refresh token is kept, encrypted with AES-256-GCM before storage, bound to the user and the purpose; the key lives in the server `.env`, never in the database or a backup (`app/vault.py:1-12`). The access token is dropped, never stored. Disconnect deletes the user's events, scheduled joins and the token, then revokes the token at Google (`app/calendar_connect.py:440-470`, `:518`; best effort, and the person is told to remove the app at myaccount.google.com/permissions if the revoke fails). Production refuses to start without the key when the calendar is on (`docs/sop/2026-11-03-phase-c-foundations.md`).

**Class of the Calendar scope.** G12 and G14 give only descriptions. Class stays **NOT VERIFIED from a page**; the Console labels it when you add it under Data Access. The checklist and handoff already treat it as sensitive.

**A narrower scope exists, and the answer is not on any page.** `events.list` and `events.get` accept nine scopes [G13], including `calendar.events.owned.readonly` ("See the events on Google calendars you own") and `calendar.events.readonly` ("View events on all your calendars") [G12][G14]. We read only the primary calendar. Neither page says whether events on your primary calendar that someone else organised count as "calendars you own" [G14]. If the owned scope returns them, Google may ask why we did not choose it [G2][G3]. This is the first item in `verification-open-questions.md`. Until it is settled, the justification below says why `calendar.events.readonly` is needed and names the narrower alternative honestly.

## 4. What we already satisfy, and what is missing

| Requirement | State | Where |
|---|---|---|
| Public home page on our domain | Yes, `https://notes.beyrictech.com/` returns 200 (checked 2026-11-20) | `web/components/landing/` |
| Home page explains the app, not only a login | Yes: product description, features, security cards, FAQ | `web/components/landing/content.ts` |
| Privacy policy on the same domain, linked from the home page | Yes, `https://notes.beyrictech.com/privacy` returns 200; the footer carries it | `web/components/legal/privacy-content.tsx` |
| Terms of service | Yes, `https://notes.beyrictech.com/terms` returns 200 | `web/components/legal/terms-content.tsx` |
| Privacy policy discloses Google access, use, storage, sharing, deletion | Yes: section "Google user data", Limited Use sentences, retention list | `privacy-content.tsx:40-55, 96-109` |
| Limited Use statement | Yes, linked to Google's policy, five bullets | `privacy-content.tsx:47-54` |
| No AI training on Google data | Yes in words; in code a grep of `app/notes.py` finds no calendar title in the notes path (not traced through every module) | `privacy-content.tsx:52`; `app/notes.py` |
| Revocation and deletion | Yes: disconnect in the app and at Google; policy says so | `privacy-content.tsx:55`; `app/calendar_connect.py` |
| Incremental, in-context consent | Yes: the calendar is asked when the person presses Connect, never at sign-in | SOP `docs/sop/2026-11-03-phase-c-google-connect.md` |
| Cloud project In production | Recorded by Adebayo 2026-11-03 | checklist P0.9 |
| Branding verified | Recorded as "branding verified only; sensitive scopes NOT verified" (checklist P0.1). Not re-checked in the Console by this session | checklist |
| Logo at the right size | Prepared: 120 px file in the repo | `web/public/brand/beyric-note-taker-google-120.png` |
| **Search Console ownership of `beyrictech.com`** | **Open** (checklist: "Search Console TXT for beyrictech.com") | checklist line 859 |
| **Final scope list frozen** | **Open** (checklist V0a) | open question 1 and 2 |
| **Demo video** | **Open**; script in section 8 | |
| **Test account with a Google calendar for the video** | **Open** | open question 4 |
| **Product name final** | Handoff says decide before submitting | open question 3 |
| **Mailboxes `privacy@`, `legal@`, `security@` receiving mail** | Open per checklist ("mailboxes"); Google writes to the project contacts, not these | open question 5 |

## 5. Claims on our public pages that a reviewer could flag

Reviewers compare the policy and home page with the app and the video [G2][G10]. Found in `web/components/legal/*` and `web/components/landing/content.ts` (read 2026-11-20). Lawyer wording is **not** a blocker; these are factual mismatches.

| # | Claim | Problem | Fix, before or at submission |
|---|---|---|---|
| 1 | Policy "Google Meet:" bullet: "if you turn on joining meetings you start yourself, Google tells us when a Meet call you host begins" (`privacy-content.tsx:45`) | No screen asks for the Meet scope (`web/lib/api/client.ts:617`); the feature is parked (checklist P0.2, P0.10). The policy describes a permission we do not request. A reviewer comparing scopes to text will see a feature in the policy that is not in the app. | Remove the bullet in a small code PR (this task is docs only). If Meet push is ever built, it needs its own scope review (checklist V0a) |
| 2 | Policy "Google Calendar:" bullet lists "title, time, organiser, attendees and meeting link" (`:44`) | True of what is received. It does not say that the description and location are read only to find the link and dropped, and that attendee addresses are not kept. The Outlook paragraph does say so (`:60`). Not wrong, but the Google paragraph is less exact than the code | Add one sentence matching `:60`. Small copy PR |
| 3 | Policy "Last updated 14 November 2026" (`:7`) while Terms changed on 2026-11-18 (git) and billing is live in test mode | Payment providers (Paystack, Flutterwave) are **not named anywhere in the Privacy Policy** (grep of `web/components/legal` and `app/templates`: no match). A reviewer who signs up sees billing screens | Add payment-provider rows and re-date. This is about all users, not Google data. The lawyer reads it later; it does not block |
| 4 | Policy "Who we share with": audio goes to Skribby, ElevenLabs and OpenAI (`:77-79`) | Accurate, disclosed since #198. Reviewers read Limited Use as applying to Google user data only. Calendar data does not go to these vendors (the meeting link goes to Skribby to join, which is "to provide the feature") | None. Keep: the video should not imply audio is Google data |
| 5 | Landing "Not used to train models" (`content.ts:112`) | Already reworded to "our providers say they do not train" with the Privacy Policy named; no "AES-256" or "end to end" claim remains (`content.ts:111`) | None |
| 6 | Terms: service "joins ... on Google Meet and Zoom" (`terms-content.tsx:26`); landing says Teams works | Teams T3 wording pending (checklist). Not a Google issue | Leave; mention only if asked |
| 7 | Home page footer and legal pages use `privacy@`, `legal@`, `security@` | The checklist says these mailboxes were still to be set up. A reviewer may email `privacy@` | Confirm they receive mail (open question 5) |

## 6. Consent-screen branding: exact values

| Field (Google Auth Platform, Branding [G10]) | Value to enter | Source of the value |
|---|---|---|
| App name | `Beyric Note Taker` | Product name on every page; the handoff asks that this be final before submitting (open question 3) |
| User support email | A Google Group or the registered Google account of the person signing in (rule [G10]). Proposed: `privacy@beyrictech.com` only if it is a Google Group or Workspace address managed in the Console; otherwise Adebayo's own registered address | open question 5 |
| App logo | `web/public/brand/beyric-note-taker-google-120.png` (120 px; JPG, PNG or BMP, at most 1 MB [G10]) | repo file |
| Application home page | `https://notes.beyrictech.com/` | |
| Application privacy policy link | `https://notes.beyrictech.com/privacy` | |
| Application terms of service link | `https://notes.beyrictech.com/terms` | |
| Authorized domains | `beyrictech.com` | Google verifies the top private domain [G8][G10] |
| Developer contact information | One address that is read daily | open question 5 |
| Authorized redirect URIs on the OAuth client | `https://notes.beyrictech.com/api/auth/google/callback` and `https://notes.beyrictech.com/api/calendar/google/callback` | `app/google_oauth.py:42-45` allow-list; `PUBLIC_BASE_URL=https://notes.beyrictech.com` in `.env.example:57` |
| Scopes declared (Data Access) | `openid`, `.../auth/userinfo.email`, `.../auth/userinfo.profile`, `.../auth/calendar.events.readonly` (or the owned scope if open question 1 says so). **Not** `meetings.space.readonly`. | section 3 |

A change to name, logo, homepage, privacy link or redirect URI after approval triggers brand verification again [G15]; fill them right the first time.

## 7. Text to paste into the submission

Written to be true of today's app. Re-read against the app one last time on the day (checklist V0).

### 7.1 How will the app use the data (one answer for the whole project)

> Beyric Note Taker (https://notes.beyrictech.com) is a meeting note taker for teams. A signed-in user can connect their Google Calendar so that the note taker joins their online meetings by itself instead of asking them to paste a link each time. We read the user's upcoming calendar events (the title, start and end time, the video-meeting link, whether they organise the event, how many people are invited and their own reply) and use them for two things only, both visible in the app: to decide which meetings the note taker should join, following the join rules the user chooses on the Settings page, and to show the user their upcoming meetings on the Meetings page. We do not create, change or delete calendar events. We read an event's description and location only to look for a meeting link and do not keep them. We keep attendee counts, not attendee addresses. We keep the data for as long as the event is upcoming and delete it two days after the event ends, and immediately when the user disconnects. Google user data is not used for advertising, is not sold, is not used to train AI models, and is not read by our staff except for support the user asks for, security or legal reasons. The sign-in permissions (openid, email, profile) are used only to create the account and show the user's name and address.

### 7.2 Scope justification: `calendar.events.readonly` (G3 step 13.1, G11)

> The app needs to read the user's calendar events so that the note taker can join the user's online meetings at the time they start. The feature cannot work without the event's start time and its video-meeting link, which are only available from the event. We use `events.list` and `events.get` on the user's primary calendar and nothing else, in the window from one hour ago to fourteen days ahead (`https://www.googleapis.com/calendar/v3/calendars/primary/events`). The scope is read-only: the app never writes to the calendar. Narrower scopes would break the feature: `calendar.events.freebusy` ("See the availability on Google calendars you have access to") returns no meeting links or titles, so the app could not know which event to join or where; `calendar.events.public.readonly` covers public calendars only. [**If open question 1 is settled as "owned scope does not work", add:** `calendar.events.owned.readonly` ("See the events on Google calendars you own") was tested with a real account on <date> and did not return events that other people organised and invited the user to, which are most of the meetings the user wants recorded.] The scope is requested only when the user presses "Connect Google Calendar" in Settings, never at sign-in, and the user can disconnect at any time in the app or at myaccount.google.com/permissions, which deletes the calendar data we hold and revokes the token.

The `freebusy` and `public` sentences rest on Google's own descriptions in G12. The bracketed sentence must only be pasted after the test; do not paste it untested.

### 7.3 Scope justification: `openid`, `email`, `profile`

Google's pages I read do not say these need a justification (they appear as sign-in scopes; G4 treats apps that request only them as exempt from the test-user expiry). **NOT VERIFIED whether the Console asks for text for them.** If it does:

> Used for "Sign in with Google" only: the email address and Google account identifier identify the account, and the name is shown in the app. No other profile data is read or stored.

### 7.4 Limited Use compliance statement

> Beyric Note Taker's use and transfer to any other app of information received from Google APIs adheres to the Google API Services User Data Policy, including the Limited Use requirements. We use Google user data only to provide the user-facing features described above. We do not transfer it to others except as needed to provide those features, for security, to comply with law, or in a merger or sale with the user's notice. We do not use it for advertising and do not sell it. We do not use it to develop, improve or train generalised AI or machine learning models. People at Beyric do not read it unless the user asks for support, it is needed for security or abuse, or the law requires it.

This is the policy's own text (`privacy-content.tsx:47-54`), so the form and the live page say the same. G7 gives no required wording, so none is invented.

### 7.5 Data retention and deletion

> Calendar events: kept while upcoming and deleted two days after the event ends; deleted at once when the user disconnects Google Calendar in the app. The Google refresh token is encrypted (AES-256-GCM) before storage, with a key kept outside the database and its backups; the access token is never stored. On disconnect we delete the token and ask Google to revoke it. Closing the account deletes the account and its stored data; account information is deleted within 30 days of closing, and database backups are deleted after 14 days. Users can also remove our access at myaccount.google.com/permissions or ask privacy@beyrictech.com.

Sources: `app/sweeps.py:9`, `app/calendar_connect.py:518`, `app/vault.py:1-12`, `privacy-content.tsx:101-102,108`.

### 7.6 Up to three documentation links (G3 step 12)

`https://notes.beyrictech.com/` ; `https://notes.beyrictech.com/privacy#google` ; `https://notes.beyrictech.com/terms`.

## 8. Demo video script

Record on the real production site at `https://notes.beyrictech.com`, with a **test account that has a Google calendar** and one upcoming event with a Google Meet link (create it before recording). Language of the Google screens: **English** [G3]. Narrate in English [G11]. Length is not stated by Google; aim for 4 to 6 minutes. Upload as **Unlisted** on YouTube [G3]. Use a full-screen browser window that shows the **address bar the whole time**.

| Shot | On screen | Say | Why Google wants it |
|---|---|---|---|
| 1 | Home page `notes.beyrictech.com`, then `/privacy`, scroll to "Google user data", then `/terms` | "This is Beyric Note Taker. It joins online meetings and writes notes. Here is our privacy policy with the Google user data section, and our terms." | Pages match the submitted links [G2] |
| 2 | Sign in page, press **Continue with Google** | "I sign in with Google." | Sign-in flow, if used [G3] |
| 3 | Google's sign-in consent screen. **Pause 4 seconds** with the address bar readable (it shows the client ID) and the app name visible. Show the language toggle on English | "Google shows the app name and asks only for name, email and profile." | App name, client ID in the address bar, scopes [G3] |
| 4 | Back in the app: Meetings page with no calendar connected, then **Settings → Calendar** | "Nothing from my calendar has been read yet. Calendar access is a separate, optional step." | Incremental consent, in context |
| 5 | Press **Connect Google Calendar** | "I connect my Google Calendar." | Grant flow start [G3] |
| 6 | Google's consent screen for the calendar. **Pause 5 seconds**, address bar with the client ID visible, scope list expanded so "View events on all your calendars" (or the owned wording, whichever is final) is readable | "This is the permission: read-only access to calendar events. The app cannot change my calendar. I allow it." | Exact scopes on the consent screen [G2][G3] |
| 7 | Back in Settings, "Connected", then the **Upcoming** list on the Meetings page showing the Meet event | "The app now lists my upcoming meetings. This is the first use: showing me what is coming." | Feature using the scope [G3] |
| 8 | The auto-join rule picker: choose "All meetings with a video link", show a per-meeting switch | "The second use: I choose which meetings the note taker joins. It uses the event's start time and meeting link." | Feature using the scope [G3] |
| 9 | (Optional, if you can run it live) at meeting start, show the bot in the Meet participant list | "At the start time the note taker joins using the link from the event." | End-to-end |
| 10 | Settings → Calendar → **Disconnect**, confirm, then the Upcoming list is empty | "I can disconnect at any time. That deletes my calendar data and the token." | Revocation and deletion [G7] |
| 11 | Open `myaccount.google.com/permissions` in a new tab and show the app listed or removed | "I can also remove access from my Google account." | |
| 12 | End on the Privacy Policy "Google user data" section | "We follow Google's Limited Use rules, do not sell data, and do not use it to train AI." | Limited Use |

Rules to keep: show every Google screen in English; never blur the address bar on shots 3 and 6; do not show real private events (use a test calendar); do not show tokens or `.env` files. If the app ever has more than one OAuth client in the project, show how each is used [G11]; today there is one web client (`GOOGLE_CLIENT_ID`).

## 9. Domain verification (Search Console)

Required before submitting: ownership of `beyrictech.com` verified in Google Search Console by a project Owner or Editor [G3][G8]. The checklist already notes "Search Console TXT for `beyrictech.com`" (line 859).

The click path below is **NOT VERIFIED against a Google page** (the pages I read only say to verify in Search Console); it is the usual flow. Search Console shows the exact record to add.

1. Open https://search.google.com/search-console and sign in with the Google account that owns the Cloud project.
2. **Add property**, choose **Domain** (not URL prefix), type `beyrictech.com`.
3. Copy the TXT record Search Console shows. Add it as a TXT record on the root of `beyrictech.com` at the DNS host (Cloudflare per the Privacy Policy).
4. Back in Search Console press **Verify**; DNS can take minutes to hours.
5. Make sure the account that verifies is an Owner or Editor of the Cloud project, or add the Cloud project's owner as an owner of the property.

## 10. Timeline Google's pages state

| Step | Time | Source |
|---|---|---|
| Brand verification, automated | "usually completes in a few minutes" | [G3][G8] |
| Brand verification, manual | 2 to 3 business days | [G5][G8] |
| Sensitive scope verification | 10 business days estimated; "up to 10 days" | [G5][G3] |
| Compliant branding stays valid | 7 days before you must publish | [G8][G10] |

Google calls these estimates, "not guaranteed", and they "vary based on developer responsiveness" [G5]. No other number is given; none is invented here. Meanwhile the app runs on the unverified-app screen with the 100-new-user lifetime cap [G4][G5].

## 11. Reviewer replies

Reply to the last email from the verification team [G5]. Keep answers short and point to the section of the policy or the minute mark in the video. Keep this page open when answering; if Google asks why a narrower scope was not chosen, section 3 is the answer.

## 12. Adebayo does this himself

Console steps only you can do; I cannot sign in, upload or submit. Do them in this order. The Console addresses are the ones Google's pages give [G3].

1. **Decide the three open items** in `docs/launch/verification-open-questions.md` (scope, Meet text, product name). Nothing below needs your answers first except step 6.
2. **Search Console.** Do section 9 (add `beyrictech.com` as a Domain property, add the TXT record at your DNS host, press Verify).
3. **Open the Google Auth Platform Branding page** for the project: https://console.developers.google.com/auth/branding?project=PROJECT_ID (replace `PROJECT_ID` with your Cloud project ID; the pattern is from [G3] step 5). If you do not know the ID: Cloud console, top bar project picker, copy the ID.
4. **Fill Branding** with the values in section 6: app name, support email, logo file, home page, privacy link, terms link, authorized domain, developer contact. Save (it saves as "Draft Branding").
5. Press **Verify Branding**. Wait for "Ready to publish" (a few minutes, or 2 to 3 business days if manual review is needed), then **Publish branding** within 7 days [G3][G10].
6. **Prepare the test account and the video** (section 8): a Google account with a calendar and a Meet event; record with the address bar visible; upload to YouTube Studio, Visibility **Unlisted**; copy the link.
7. **Data Access page** (Auth Platform left menu, "Data Access"): press **Add or remove scopes**; add `openid`, `userinfo.email`, `userinfo.profile` and `calendar.events.readonly` (or the final choice). Do **not** add the Meet scope. Save. The Console shows each scope's class; write down what it says for the Calendar scope and tell me, so section 3 can be updated.
8. **Verification Center:** https://console.developers.google.com/auth/verification [G3]. Under "Data access status" paste the justification from 7.2 (and 7.3 if asked), the how-it-uses text from 7.1, up to three documentation links from 7.6, and the YouTube link. Check the Console's own field names and lengths; if a field is shorter than the text, shorten but keep the facts.
9. **Press Submit for Verification.** Confirm the project owners, editors and the developer contact addresses are all addresses you read [G6].
10. **Watch your email** and answer the last email from the verification team [G5]; the status also shows in the Verification Center [G3].
11. Afterwards: tell the lead the result, and set `GOOGLE_APP_VERIFIED=true` on the server (it only hides the in-app "Google has not verified this app" notice; the flag is `app/config.py:649`; this is a server step the lead will give you).

## 13. Follow-ups

- Remove the Meet bullet from the policy and terms, and add the exact-fields sentence to the Google bullet (section 5, rows 1 and 2): one small web PR.
- Add payment providers to the Privacy Policy (row 3). Affects every user; the lawyer reads it later.
- Test `calendar.events.owned.readonly` with a real account (open question 1).
- After approval: record the date and the approved scope list in the checklist (Phase V); any new Google scope later is a new review [G6].
- Update this file with the Console's class for the Calendar scope once Adebayo reads it.

## 14. Verification table (how much to trust each part)

| Part | State |
|---|---|
| Google requirements (sections 2, 10) | Read from Google's own pages on 2026-11-20 through the fetch agent. Not re-read by me in a browser. Gaps listed in section 1 |
| Calendar scope class | NOT VERIFIED from a page |
| Whether the owned scope covers invited events | NOT VERIFIED; must be tested |
| Our scopes, retention, deletion, token vault (sections 3, 4) | Read from the code on `main` at `817c2578`; **not run against real Google in this task**. The live Google round trip is still marked "not verified" in `docs/sop/2026-11-03-phase-c-google-connect.md` |
| Public pages return 200 | Checked 2026-11-20 with a plain GET of `/`, `/privacy`, `/terms` |
| Search Console steps | NOT VERIFIED against a Google page |
| Console field names (Verification Center) | From G3; the Console itself was not opened |
