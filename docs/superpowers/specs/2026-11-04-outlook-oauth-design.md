# Outlook sign-in and calendar sync: design

**Status:** DRAFT 2026-11-04 for Adebayo's answers (section 1). Design only: no application code, no migration, nothing run against Microsoft.
**Why this exists:** on 2026-11-04 Adebayo decided that Outlook (Microsoft OAuth plus the Graph calendar) moves into the pilot, because the designer drew a "Connect Microsoft Outlook" button. The pasted-ICS route (plan C19 to C24) stays. **No Outlook wording may appear anywhere in the app, the policy or the landing page until Outlook works** (handoff section 2).
**Evidence base:** the pilot spec (`2026-11-03-pilot-accounts-calendar-design.md`, sections 5, 7, 12), the AI workspace spec (calendar rows only), the plan (`2026-11-03-pilot-accounts-calendar.md`, Phase C and Review Focus), the shipped Google code (`app/gcal_client.py`, `gcal_events.py`, `gcal_rules.py`, `gcal_sync.py`, `calendar_source.py`, `calendar_connect.py`, `calendar_api.py`, `calendar_text.py`, `vault.py`, `models.py`), PR #72 (C12 join runner, read from its branch), the C3, C4, C8, C15a SOPs and FE guides, the designer's files in `docs/ui/Beyric Note Taker redesign/`, and Microsoft Learn (pages listed in section 14).

**How to read it.** Three kinds of statement: **(doc)** read on a named Microsoft Learn page, with its URL in section 14; **ASSUMED** my choice or my inference, with a reason; **capture in M0** a fact I could not find in the documentation and that the throwaway live run (section 11, task M0) must record from a real account before any code relies on it. Nothing here was captured live. Every Microsoft response shape in this document is therefore a documented shape, not a capture: the FE guide will be written from real captures only (section 12).

---

## 1. Decisions at a glance, and what only Adebayo can answer

### 1.1 The twelve decisions

| # | Decision | Why in one line |
|---|---|---|
| D1 | **One app registration**, audience "Accounts in any organizational directory and personal Microsoft accounts" (`AzureADandPersonalMicrosoftAccount`), registered **in a work or school tenant, never under a bare personal account** | Apps registered with a Microsoft account can never be publisher verified (doc), and work accounts will need verification |
| D2 | **Personal Outlook.com, Hotmail and Live accounts first**: the pilot ships with our authorize endpoint set to `consumers` (setting `MICROSOFT_AUTHORITY`); the same registration flips to `common` later without re-registering | Personal accounts need no publisher verification and no admin; work accounts may hit "needs administrator approval" until verification is done |
| D3 | **Authorization code with PKCE (S256)** on the v2.0 endpoints, confidential client with a **client secret** for the pilot (12 months, expiry alert), certificate before a public launch | Same shape as the Google flow; Microsoft prefers certificates, which is a later, contained upgrade |
| D4 | **Scopes:** `Calendars.Read`, `User.Read`, `offline_access`, plus the OpenID scopes `openid` and `profile` (they give the ID token the `tid` and `oid` claims and expose no data). Nothing else | Least privilege that still reads event bodies, where Zoom and Meet links live |
| D5 | **Refresh tokens rotate on every use** (doc): the new token is saved with a compare-and-swap write on the connection row, old one discarded | The one real difference from Google, where the refresh token never changes |
| D6 | **No revoke call is documented** for an app to drop its own grant: disconnect deletes our copy and tells the person where to remove the grant at Microsoft | None found in the pages read; "revoke sign-in sessions" would sign the person out of everything, so it is rejected |
| D7 | **Read events with `calendarView` over the same window as Google, as a full listing each sync, not delta queries** | The existing sync already treats a full listing as "absent means removed"; delta adds `@removed`, token expiry and replays for no gain at pilot scale |
| D8 | **Ask Graph for UTC and immutable ids** (`Prefer: outlook.timezone="UTC"`, `IdType="ImmutableId"`), and refuse any event that comes back in another zone instead of converting a Windows zone name ourselves | Removes the whole "zone name to offset" class of bug |
| D9 | **A provider registry, one normaliser per provider, one `CalendarEvent` shape.** `calendar_connections` already has a `provider` column and a unique `(user_id, provider)`; the migration widens the check, renames the three `google_*` columns to neutral names, and adds `tenant_id` | No parallel code path: sync, rules, joins, API and screens read the same rows |
| D10 | **One live calendar connection per user at a time**, enforced by the server (409 `calendar_already_connected`); the designer's Settings card draws exactly one | Two sources for one person need a cross-source duplicate rule that would call their own meeting "already has a note taker" |
| D11 | **Teams is the Outlook reality and the bot cannot join it yet.** Outlook ships **dark** and is switched on for people only when Teams joining works (P0.7 passed, T1 to T3 live) | Otherwise most of an Outlook user's rows would say "Microsoft Teams meetings cannot be joined yet" on day one |
| D12 | **"Continue with Microsoft" on the sign-in page is NOT part of this build.** It is a separate sign-in feature with its own account-linking rule (match on `tid` plus `oid`, never on email) | Adebayo's decision was the Outlook calendar; sign-in by Microsoft is a different risk |

### 1.2 Open questions for Adebayo (each with my recommendation)

| # | Question | Recommended answer | Why |
|---|---|---|---|
| Q1 | **Which Microsoft account owns the app registration?** It must be a work or school (Microsoft Entra) account. Do you already have a Microsoft 365 or Entra tenant for Beyrichq LTD? | If not: create one with a free Azure account using a `beyrictech.com` address. Microsoft's registration page says a tenant is a prerequisite and points to a free Azure account to get one; it does not say registering an app is billed. Azure sign-up normally asks for a phone number and a card to prove identity (general knowledge, not from the pages read). The lead does the clicks and **stops at any billing page** | An app registered with a personal Microsoft account cannot be publisher verified (doc). Moving a registration later changes the client id and every user must reconnect |
| Q2 | **Who are the first Outlook testers: personal accounts (Outlook.com, Hotmail) or work accounts?** | Personal first (D2). A work-account tester is welcome only once publisher verification is done or the tester's IT admin approves the app | A work tenant on Microsoft's default consent policy blocks users from approving an unverified multitenant app that asks for more than basic sign-in (doc, section 3.4) |
| Q3 | **Start Beyrichq LTD's Microsoft Partner Center (AI Cloud Partner Program) verification now?** | Yes, now, in parallel. It is free but needs company details and a verified mailbox on `beyrictech.com`, and its duration is not documented | It is the long pole for work accounts; it blocks nothing else |
| Q4 | **Is Outlook allowed to ship dark and wait for Teams joining?** (D11) | Yes. Build and merge it behind `MICROSOFT_CALENDAR_ENABLED=false`; switch on when the Teams spike P0.7 has passed and T2 is live | You said no Outlook wording until it works; "works" for an Outlook user means Teams meetings join |
| Q5 | **One calendar at a time?** (D10) | Yes. To change calendar a person disconnects first | The designer drew one connected card; two sources need a duplicate rule we have not designed |
| Q6 | **Confirm: "Continue with Microsoft" sign-in stays out of this build** (D12) | Confirm. Ask the designer later whether it is wanted for the pilot | Sign-in by an identity provider is a separate account-takeover surface (section 8.4) |
| Q7 | **Client secret now, certificate before a public launch?** (D3) | Yes. 12-month secret, a reminder email to you 30 days before it expires, rotation steps in section 9 | Microsoft calls secrets less secure than certificates (doc); our server already guards a more valuable key (`TOKEN_ENC_KEY`) in the same `.env` |
| Q8 | **Test accounts:** a spare Outlook.com account for the build sessions, and if possible one Microsoft 365 work or school mailbox | Give both; the work one can be a colleague's mailbox used only to read its own calendar once | Without a work account every work-account behaviour in this spec stays "not verified live" |

If Adebayo answers "all recommended", the spec stands as written.

---

## 2. Goals and non-goals

### Goals
1. A person with a personal Outlook.com account connects their calendar in two clicks, sees their next meetings with the designer's three words ("Will join", "Off", "Cannot join"), and the existing join runner (C12) sends the bot the way it does for Google.
2. Work or school accounts work with the same code when the registration and the tenant policy allow it, and fail with a plain message when they do not.
3. Tokens are stored exactly as Google's are (AES-GCM, `TOKEN_ENC_KEY`), disconnect deletes everything C4 deletes, and nothing Microsoft sends is trusted beyond what it has to be.
4. No second code path: one sync loop, one rules function, one event row, one set of screens, one API.

### Non-goals (this version)
- Sign-in with a Microsoft account (D12); sharing or shared calendars (`Calendars.Read.Shared`); secondary calendars (primary only, as Google); writing to the calendar (no write scope, ever, in this version); reading mail or Teams chat; Microsoft Graph change notifications (webhooks) and delta queries (D7); an admin-consent request link; the Outlook ICS route (plan C19 to C24, separate).
- Joining Teams meetings: that is plan P0.7 and T1 to T3. This spec only decides how Outlook behaves while Teams is not joinable (section 7).

---

## 3. Decision 1: which accounts, which registration

### 3.1 What Microsoft documents

| Fact | Source |
|---|---|
| Four audiences exist: single tenant (`AzureADMyOrg`), any work or school tenant (`AzureADMultipleOrgs`), work or school plus personal (`AzureADandPersonalMicrosoftAccount`), personal only (`PersonalMicrosoftAccount`). It can be changed later in the manifest | supported-accounts-validation; howto-modify-supported-accounts |
| The endpoint path chooses who may sign in: `common` (work, school and personal), `organizations`, `consumers`, or a tenant id. The effective audience is the narrower of the registration and the endpoint | msal-client-application-configuration; v2-oauth2-auth-code-flow |
| `consumers` is a nickname for the personal-account tenant `9188040d-6c67-4c5b-b112-36a304b66dad`, and a personal sign-in's ID token has `tid` equal to it | access-tokens; id-token-claims-reference |
| `Calendars.Read` (delegated), `User.Read` and `offline_access` do **not** need admin consent, and `Calendars.Read` and `User.Read` are available for consent by personal accounts | permissions-reference |
| Since November 2020, where risk-based step-up consent is on, users cannot consent to newly registered multitenant apps that are not publisher verified when the app asks for more than basic sign-in and profile, in tenants other than the app's own; the consent screen shows an "unverified publisher" warning | publisher-verification-overview |
| Publisher verification is free, but needs a verified Partner Center (CPP) global account, a tenant associated with it, a publisher domain on the app (not `*.onmicrosoft.com`), a mailbox on a matching domain, a user with MFA and the right roles in both places. **An app registered with a Microsoft account cannot be publisher verified** | publisher-verification-overview |
| Admins can allow only verified-publisher apps for low-impact permissions, or disable user consent; users then see "ask your administrator" | user-admin-consent-overview; application-consent-experience |
| `common` and `organizations` should be avoided for apps aimed at external users who may be guests; naming an audience is recommended | msal-client-application-configuration |

### 3.2 The choices

| Question | Decision | Reasoning |
|---|---|---|
| **Single or multi-tenant?** | Multi-tenant plus personal (`AzureADandPersonalMicrosoftAccount`) | A single-tenant registration would only serve our own tenant. Personal accounts are the first users and need a registration that allows them |
| **Personal first, how?** | Registration allows both; **our authorize and token URLs use the `consumers` path** until Q2 moves. Setting `MICROSOFT_AUTHORITY` is `consumers` or `common` (nothing else is accepted) | Flipping a setting, not a registration, opens work accounts. A work account sent to `consumers` is refused by Microsoft before it reaches us |
| **Which tenant owns the registration** | A Beyrichq Entra tenant (Q1), with `beyrictech.com` added and DNS-verified as a custom domain | Required later for publisher verification; costs nothing now |
| **Is publisher verification needed for the pilot?** | **No for personal accounts. Yes before inviting work accounts** into tenants with default consent settings. Start it now (Q3); it has a long, undocumented lead time | The doc's rule is about users consenting "in tenants that aren't the tenant where the app is registered". Personal accounts are not in a tenant that has such a policy. Whether the personal-account consent screen also shows "unverified" is not documented: **capture in M0** (screenshot) |
| **Admin consent consequences** | We do not request admin consent or build an admin-consent link in v1. A work user who is blocked sees Microsoft's own page; we cannot detect it (Microsoft does not redirect back). The Settings explainer for work accounts is a designer request | The admin-consent endpoint (`/{tenant}/v2.0/adminconsent`, doc) needs its own registered redirect route and UI: a follow-up, not v1 |
| **Redirect URI** | `https://notes.beyrictech.com/api/calendar/microsoft/callback`, type **Web**, registered exactly (HTTPS, case-sensitive, no trailing slash, no wildcard: all doc, reply-url). A second registration "Beyric Note Taker (local development)" with `http://localhost:8000/api/calendar/microsoft/callback` only if a developer needs a live run (the docs advise separate registrations for development and production) | Microsoft matches the string exactly; `http` is allowed only for `localhost` |
| **Guest and mailbox-less accounts** | Refused at connect time by a probe (section 4.5) | A Microsoft account with no Outlook mailbox (or a work account without Exchange Online) has no calendar to read |

---

## 4. Decision 2: the OAuth flow

### 4.1 Connect (mirrors `calendar_connect.start_connect` and `finish_connect`)

1. `POST /api/calendar/microsoft/connect` (CSRF, signed in, feature on). Writes an `oauth_flows` row (state hash, PKCE verifier, nonce, `return_to`, 10-minute expiry, **new column `provider = 'microsoft'`**) and returns the authorize URL.
2. Authorize URL: `https://login.microsoftonline.com/{consumers|common}/oauth2/v2.0/authorize` with `client_id`, `response_type=code`, `redirect_uri`, `response_mode=query`, `scope=openid profile offline_access User.Read Calendars.Read` (Graph scopes written as `https://graph.microsoft.com/Calendars.Read` and `.../User.Read`), `state`, `nonce`, `code_challenge`, `code_challenge_method=S256`, `prompt=select_account`. No `login_hint`: the person's Beyric email is often not their Outlook address. All parameters and values are in the code-flow page (doc).
3. Microsoft redirects to `GET /api/calendar/microsoft/callback`. The callback runs the **same seven checks** as Google's `finish_connect` (state known, unused, unexpired, belongs to a Microsoft calendar flow; signed in as the flow's user; the path-scoped flow cookie present (`calendar_flow_ms`, path `/api/calendar/microsoft`); no `error` from Microsoft; the code redeems; the needed scope is granted; a refresh token came) plus the Microsoft rules in 4.4 and 4.5. A Microsoft state can never complete at the Google callback or the reverse: the flow's `provider` is checked.
4. The authorization code lives about one minute (doc): the callback redeems it first thing, never queues it.
5. On success: store the encrypted refresh token, redirect to `/settings?calendar=connected`. The first sync runs at once (`last_sync_at = null`), as for Google. On failure: `?calendar_error=<code>`, nothing stored. New codes: `microsoft_denied`, `microsoft_failed`, `scope_missing`, `no_mailbox`, `tenant_not_allowed`, `wrong_account`, `already_connected` (the designer drew none of these; section 12.3).

### 4.2 Scopes: minimal, and why not more

| Scope | Why | Admin consent | Notes |
|---|---|---|---|
| `Calendars.Read` | Reads events on the signed-in user's calendars, including body and location, where Zoom and Meet links live | No (doc) | Available to personal accounts (doc) |
| `User.Read` | Reads the person's profile (`GET /me`) for the account label; for a work account also the organisation's id, name and verified domains (doc) | No | Also gives the stable user identity |
| `offline_access` | Without it Microsoft issues **no refresh token** on the v2.0 endpoint (doc) | No | Appears on every consent page as "Maintain access to data you have given it access to" |
| `openid`, `profile` | Return an ID token with `tid`, `oid`, `preferred_username` (`tid` needs `profile`, doc). They grant no data access | No | We do **not** request `email`: the `email` and `preferred_username` claims are display-only, never for deciding anything (doc, claims-validation) |

**Not requested, and why:** `Calendars.ReadBasic` (leaves out body and attachments, doc; it might still carry `location`, `onlineMeeting` and `responseStatus`: **capture in M0 S3**. If it does and Zoom links also arrive outside the body, narrowing later is a scope change that makes everyone consent again, so the decision is taken **before** testers connect, not after); `Calendars.ReadWrite` and any write scope (the product never writes to calendars); `Calendars.Read.Shared` (other people's calendars); `Mail.*`, `Chat.*`; `OnlineMeetings.Read` (the Teams join URL is already on the event as `onlineMeeting.joinUrl`, doc, so the cloud-communications API is not needed); `email` (see above). A new scope later is a new consent for every user, so the list is fixed here.

### 4.3 Tokens, refresh and rotation

| Topic | Decision | Source |
|---|---|---|
| Access token | About one hour. Held in memory only, never stored, never logged, **never decoded**: "Don't attempt to validate or read tokens for any API you don't own", and personal-account tokens may be encrypted | v2-oauth2-auth-code-flow; scopes-oidc |
| Refresh token lifetime | Default 90 days for a web app; each use returns a new refresh token. Our sync loop uses it about hourly, so it never idles | refresh-tokens |
| **Rotation** | Every refresh returns a **new** refresh token. The old one is "not revoked when used" and "you are expected to discard" it. So `MicrosoftTokenCache._mint` returns `(access token, lifetime, new refresh token)` and calls a writer that runs, in its own short transaction, `UPDATE calendar_connections SET refresh_token_enc = :new, key_version = :v WHERE id = :id AND refresh_token_enc = :previous`. **Compare-and-swap:** a stale writer never overwrites a newer token. If the write fails the sync carries on with the access token, and the old refresh token still works until its own expiry, so a lost write is not an outage | refresh-tokens; v2-oauth2-auth-code-flow |
| Free re-encryption | The rewrite uses the **current** key version, so Microsoft tokens migrate to a new `TOKEN_ENC_KEY` by themselves; the C9 re-encrypt script still covers idle rows | vault.py rotation notes |
| Refresh endpoint | `https://login.microsoftonline.com/{tid}/oauth2/v2.0/token` with the connection's own `tenant_id` (never `common`), same scope list (a subset is allowed), client secret in the form body | v2-oauth2-auth-code-flow |
| Errors | `invalid_grant` and `interaction_required` / `consent_required` at the token endpoint become `AuthRevoked` (only a reconnect helps). `invalid_client` (our own secret expired or wrong), `temporarily_unavailable`, 429 and 5xx become `TransientError`, and an expired client secret additionally alerts the admin. Reason mapping: **always `revoked`**, except `error_codes` containing 700082 (refresh token expired from inactivity, **capture in M0**) which is `expired`. Messages carry the status, the error string and the numeric codes, never `error_description` (it has text, trace and correlation ids) | v2-oauth2-auth-code-flow; same contract as `gcal_client` |
| Revocation by Microsoft | Password change by the user, an admin reset, a user or admin revoking tokens, and Conditional Access can end a grant. For confidential clients a plain password change does not (doc table). All surface as `invalid_grant`, handled above | refresh-tokens |
| Encryption at rest | `vault.encrypt(purpose="ms_refresh_token", owner=user_id)`, AES-256-GCM, the existing `TOKEN_ENC_KEY` and `key_version`. **One new purpose string** in `vault.PURPOSES`, so a Google blob copied into a Microsoft row fails to open. No new key | vault.py |

### 4.4 The ID token, and the tenant checks

The ID token from the **code redemption** is checked the way `google_oauth.py` checks Google's: it arrives straight from Microsoft's token endpoint over TLS in answer to a request that carried our client secret and PKCE verifier, and the documentation says confidential clients can use that token for authorization (v2-oauth2-auth-code-flow). OpenID Connect Core 3.1.3.7 permits skipping the signature check on that channel. Ignore the `id_token` in **refresh** responses (the page calls it "unsigned" and not for security decisions).

Checks, all before anything is stored: `aud` equals our client id; `exp` in the future and `iat` not ahead by more than 60 s; `nonce` equals the flow's; `tid` is a GUID; `iss` equals `https://login.microsoftonline.com/{tid}/v2.0` for **that** `tid` (the doc's chain-of-trust rule for tenant-independent endpoints); the token has `oid` (or `sub`); when `MICROSOFT_ALLOWED_TENANTS` is not empty, `tid` is in it, else `tenant_not_allowed`. Result: `account_kind = personal` if `tid` is `9188040d-6c67-4c5b-b112-36a304b66dad`, else `work`.

**Identity of a connection** is `tid` plus `oid` (doc: "use the immutable claim values `tid` and `oid` as a combined key"); never an email, `preferred_username` or UPN (doc: "Never use claims like email, preferred_username ... to determine whether the user should have access"). The email shown on Settings comes from `GET /me` (`mail`, else `userPrincipalName`) and is a label only. Whether a personal-account token carries `oid` is not stated for sure: **capture in M0 S1**; if it does not, `sub` with `tid` is the key.

### 4.5 Probe before storing

Before the callback stores anything, it calls `GET /me/calendarView?startDateTime=now&endDateTime=now+1h&$top=1` with the new access token. 200 means a calendar can be read. A 404-class "no mailbox" answer (the documented-by-experience code is `MailboxNotEnabledForRESTAPI`: **capture in M0 S6**), a 401 or a 403 means nothing is stored and the redirect carries `no_mailbox` or `scope_missing`. This keeps a mailbox-less Microsoft account or a blocked tenant from becoming a connection that "works" and never syncs. Because it uses the redeemed token it adds one Graph call and at most the 15-second client timeout.

If M0 S4 shows `GET /organization` returns `verifiedDomains` with only `User.Read` (the permissions page says `User.Read` allows reading them), a work connection also stores them in `tenant_domains`, used for the same-organisation test in 5.4. Otherwise `tenant_domains` stays empty and that test is simply false.

### 4.6 Disconnect and revoke (reuses C4, `calendar_connect.disconnect`)

The Microsoft identity platform pages read describe **no revocation endpoint** an app can call for its own grant. Users remove access in their account's app permissions (work: `myapps.microsoft.com`, doc; personal: the consent page of their Microsoft account, **URL to capture in M0**), admins in Enterprise applications, and `revokeSignInSessions` exists but ends all of a user's sessions everywhere, so it is rejected. Therefore:

- Disconnect deletes exactly what C4 deletes (token, events, every `scheduled_joins` row in every state, calendar-kind notifications), keeps what C4 keeps (meetings, usage, settings), commits, **then** the provider's clean-up hook runs. For Microsoft that hook only drops the in-memory token; there is nothing to revoke. The response says `revoked: false` with the reason `provider_has_no_revoke` so the screen shows the existing "remove it yourself" note with Microsoft's address instead of Google's.
- The privacy policy must say this plainly (section 8.2): disconnecting deletes our copy; to remove Beyric from the Microsoft account the person does it at Microsoft.

---

## 5. Decision 3: reading events

### 5.1 `calendarView`, as a full listing each time (D7)

| | `calendarView` full window each sync | `calendarView/delta` |
|---|---|---|
| Fits the shipped sync | Yes: a batch with `full=True, complete=True` is already "events not named were removed or moved out" (`gcal_sync._cancel_missing`); no token needed, so `_full_due` is simply true | Needs a token column use, `@removed` objects, token expiry |
| Doc facts | Needs `startDateTime` and `endDateTime`; `$top` 1 to 1000; follows `@odata.nextLink` | Window is baked into the token and cannot slide; **`$select`, `$filter`, `$orderby`, `$search` are not supported**; deleted events come back under `@removed` and so do changed events *outside* the window; replays are possible; an Outlook delta token can vanish when a cache fills (`syncStateNotFound`) |
| Payload per call | `$select` keeps it small | Always the full event (no `$select`) |
| Call volume | About 288 calls a day per connection at the shipped five-minute cadence; the Outlook limit is 10,000 requests per 10 minutes and four concurrent per app and mailbox (doc) | Fewer bytes, same call count |

Delta's savings do not matter at ten users, and its extra states are exactly where a half-applied change could cancel a real meeting. **Decision: full window every sync; revisit delta only if throttling or payload size ever bites.** Implementation facts: same window constants as Google (`WINDOW_BEFORE` one hour, `WINDOW_AFTER` fourteen days); `Prefer: odata.maxpagesize` is not needed because `$top=250` is sent; the `@odata.nextLink` URL is **only followed when it starts with `https://graph.microsoft.com/v1.0/`** (a full URL comes from Microsoft's answer; the Google client's rule "no URL, host or path comes from the answers" must hold here too, and the `Authorization` header must never go anywhere else); a runaway chain stops at the same `MAX_PAGES` and returns an incomplete batch (so no cancellation is applied, the existing contract).

`$select` list (ASSUMED, confirm in M0): `id, iCalUId, subject, start, end, isAllDay, isCancelled, isDraft, isOrganizer, organizer, responseStatus, attendees, onlineMeeting, onlineMeetingProvider, isOnlineMeeting, location, locations, body, seriesMasterId, type, sensitivity, showAs`. The doc says `createdDateTime` and `lastModifiedDateTime` do not support `$select`, so `calendar_events.source_updated_at` stays null for Microsoft (nothing reads it today).

Headers on every event call: `Prefer: outlook.timezone="UTC"` and `Prefer: IdType="ImmutableId"` (two preferences; sent as two header lines, **confirm in M0** that Graph honours both in one response). `ImmutableId` matters because by default an event's id **changes when it moves between folders or calendars** (doc, outlook-immutable-id), and `source_event_id` is our stable key; it must also be sent on `get_event`, or the ids would not match. Ids are case-sensitive, up to a few hundred characters (column holds 1024), and `get_event` quotes them into one path segment exactly as the Google client does.

**Primary calendar only** (`/me/calendarView`), matching the Google decision (spec Q18).

### 5.2 Recurrence

`calendarView` already expands a series into occurrences and exceptions (doc); each has its own id and `seriesMasterId`. So **we expand nothing** for Microsoft (the paste-a-link route is the one that must). `recurring_event_id` is `seriesMasterId`. A per-meeting switch applies to that one occurrence, as for Google.

### 5.3 Time zones: how every instant becomes UTC (Review Focus 2)

With `Prefer: outlook.timezone="UTC"` (and by default, doc) `start` and `end` arrive as `{"dateTime": "2026-11-05T10:00:00.0000000", "timeZone": "UTC"}`: no offset, seven fractional digits. The normaliser:

1. Requires `timeZone == "UTC"`. Anything else raises `EventParseError("the event time is not in UTC")` and counts as unreadable (the existing path: logged by type, never cancels a stored meeting). **It never maps a Windows zone name** ("W. Europe Standard Time") itself.
2. Parses `dateTime` with `datetime.fromisoformat`, attaches UTC. (Checked here: the project's own Python 3.13 accepts the seven fractional digits; the declared floor is 3.12, which I could not run, so M5 has a test with the exact string so a version difference cannot slip in.)
3. **All-day events** (`isAllDay`): the documented behaviour is midnight-to-midnight with the zone not honoured, and organiser zone is not in the payload (a Microsoft Q&A thread reports exactly this). We never join all-day events, so the instants are only stored with `all_day=True`; the "wrong local day" problem cannot reach a join. `will_join` already answers "All-day events are not joined".
4. DST and midnight are no issue because nothing is converted: the instant is whatever UTC says. `originalStartTimeZone` is ignored.

### 5.4 Fields to the one event shape (`ParsedEvent`)

| `ParsedEvent` field | From Graph | Notes |
|---|---|---|
| `source_event_id` | `id` (immutable) | |
| `recurring_event_id` | `seriesMasterId` | null for single events |
| `title` | `subject` | same `_clean` and 1,000-character cut as Google |
| `starts_at`, `ends_at` | `start`, `end` (5.3) | |
| `all_day` | `isAllDay` | |
| `status` | `isCancelled` true gives `cancelled`, otherwise `confirmed` (`showAs` is not used) | A cancelled meeting stays in `calendarView` with `isCancelled`; a deleted one is simply absent (`_cancel_missing`) |
| `event_type` | always `default` (every `type`: `singleInstance`, `occurrence`, `exception`, `seriesMaster`) | Google's focus time and out-of-office are separate event types; Outlook has no equivalent, and skipping on `showAs` of `free` or `oof` would hide real meetings |
| `organizer_self` | `isOrganizer` (doc: also true when a delegate organised it) | |
| `self_response` | `responseStatus.response`: `accepted` to `accepted`; `tentativelyAccepted` to `tentative`; `declined` to `declined`; `notResponded` to `needsAction`; `organizer` to `accepted`; `none` to null | Doc: `none` is the organiser's-eye value and appears on meetings with no invitees; `notResponded` is the invitee's own "has not answered" |
| `attendee_count` | `len(attendees) + 1` when `attendees` is non-empty, else 0 | ASSUMED: Graph's `attendees` omits the organiser (it has its own field) where Google's includes everyone; this keeps "N people" equal across providers. **Confirm against a capture** |
| `organizer_same_org` | The organiser's address domain is in the connection's `tenant_domains` (work accounts, 4.5); always false for personal accounts and while `tenant_domains` is empty | Conservative: false means an unanswered colleague invite waits for "accept", the safe direction. The risk of a spoofed internal address is a review item (section 8.1) |
| `sensitivity` | `private`, `personal`, `confidential` | **Parity with Google:** the owner's own events are read and joined the same; nothing special is done, because the owner already sees their private titles. The handoff's warning (rule `all` joins private meetings) is unchanged |
| `isDraft` | ignored | An organiser editing their own unsent meeting still means to hold it. Confirm in M0 |

### 5.5 Finding the meeting link

Order, first usable link wins, each passed through the **existing** `_classify` (`parse_meeting_url`, plus the Teams hosts `teams.microsoft.com/l/meetup-join/`, `/meet/`, `teams.live.com/meet/`):

1. `onlineMeeting.joinUrl`, when `isOnlineMeeting` is true. The doc says to use it and **not** the legacy `onlineMeetingUrl`, which will be deprecated. `onlineMeetingProvider` is `teamsForBusiness`, `skypeForBusiness`, `skypeForConsumer` or `unknown` (doc); only the URL's host decides the platform, never the provider string.
2. `location.displayName` and each `locations[].displayName`, scanned for URLs (Zoom for Outlook and Meet add-ins often write the link here).
3. `body.content`. The default format is HTML, which suits the existing scanner: `gcal_events._urls_in_text` already unescapes entities and finds `https://...` inside markup, so an `href` is found even when the visible text is "Join Zoom Meeting". We do **not** ask for `text` (it would drop the hidden href).
4. **Safe Links:** Microsoft 365 tenants can rewrite links in invites to `https://<region>.safelinks.protection.outlook.com/?url=<encoded real link>&...`. This is general experience, **not** from the pages I read: **capture in M0 S5** with a real work account. If it appears, the normaliser unwraps it **only** when the host ends with `.safelinks.protection.outlook.com` and the decoded target then passes `_classify`; otherwise the link is ignored.

Teams links produce `platform = teams` with `native_id = None` (until T1), and `will_join` answers `teams_unsupported` while `TEAMS_JOIN_ENABLED` is false. A Zoom or Meet link beside a Teams link in one event: the first match in the order above wins (the Teams join URL, when present).

### 5.6 What the rules do with it (`app/gcal_rules.py`, unchanged)

Because `will_join` reads only `ParsedEvent` fields, the organiser rule, the unanswered-invite safeguard (joins only if the person organised it or `organizer_same_org`), the declined check, the 6-hour cap, per-meeting overrides and the Teams switch all apply with no change. The first Microsoft consequence is the Teams reason (section 7).

### 5.7 Re-check before the join (`get_event`)

`GET /me/events/{id}` with the same two `Prefer` values and the same `$select`; 404 (`ErrorItemNotFound`) or 410 returns `None` ("the source no longer has it"), exactly the contract `join_runner._recheck` expects. The runner skips the re-check when `conn.provider != calendar.provider`; the runner and the sync must therefore receive a **registry of sources keyed by provider** instead of one source (task M1; this touches code PR #72 adds).

---

## 6. Decision 4: plugging in without a parallel path

### 6.1 The provider registry

New `app/calendar_providers.py`: one table keyed by provider (`google`, `microsoft`; `ics` is added by C23 through the same table).

| Entry | Google today | Microsoft |
|---|---|---|
| Source factory (`CalendarSource`) | `GoogleSource` | `MicrosoftSource` (`app/graph_client.py`) |
| Event parser | `gcal_events.parse_event` | `graph_events.parse_event` (new), same `ParsedEvent` |
| Feature check (`granted_features`) | exact scope strings | `Calendars.Read` among the granted scopes (compared after stripping `https://graph.microsoft.com/`, case-insensitively; **capture the exact `scope` string in M0 S1**) |
| Clean-up on disconnect | revoke at Google | none (4.6) |
| Words | "Google" texts in `calendar_text.py` | provider-aware: the same keys, "Microsoft" in the sentences, "Microsoft Outlook" as the designer's provider label |

Call sites that change: `gcal_sync._parse_all` picks the parser by `conn.provider`; `register_calendar_sync` registers one sync job per enabled provider (`sync_due_connections` already filters by `source.provider`); `join_runner` takes the registry (PR #72 builds one `GoogleSource` and shares its token cache between sync and runner; the same sharing holds per provider); `calendar_connect` and `calendar_api` get a `{provider}` path parameter (a `Literal["google","microsoft"]`) so the existing Google URLs are unchanged. Per provider, one `AccessTokenCache`; the Microsoft one adds the rotation writer.

### 6.2 Data and the migration (`0017_microsoft_calendar`, after C12's `0016_autojoin_markers`; number checked with `alembic heads` at rebase)

| Change | Detail |
|---|---|
| `calendar_connections.provider` check | widened to `('google', 'ics', 'microsoft')` |
| Neutral identity columns | rename `google_account_email` to `account_email`, `google_sub` to `account_subject`, `google_hosted_domain` to `account_domain`. Inventory (`grep`, 2026-11-04): about 30 references in about 10 files across `app/`, `tests/`, `scripts/` and the connect FE guide; no data movement. The sign-in column `users.google_sub` is a different thing and is **not** renamed |
| New columns | `tenant_id` (varchar 36, null), `account_kind` (`personal`/`work`, null for Google and ICS, with a check), `tenant_domains` (text, null) |
| `oauth_flows.provider` | varchar 16, default `'google'`, check in `('google','microsoft')`; `OAUTH_PURPOSES` unchanged (`calendar`) |
| What does **not** change | `granted_scopes` (free text), the `meet_*` columns (stay `none` for Microsoft), `expected_expiry_at` (unused: no testing-mode expiry), `calendar_events`, `scheduled_joins`, `user_settings`, every constraint and index |
| Backfill | none needed beyond the renames; existing Google rows keep their values under the new names. Downgrade renames back and drops the three new columns (fails if a `microsoft` row exists: document it) |

**Why rename rather than keep `google_*` for Google and add `ms_*`:** that would be the parallel path. **Why rename rather than leave the names and document them as "legacy":** a Microsoft subject in a column called `google_sub` is exactly how a later bug (matching the wrong provider) is born. The rename is cheap now (about 30 references) and expensive after more tasks (C13, C14, C15b, ICS) add more.

### 6.3 One connection per user or per provider (D10)

The schema stays "one row per provider per user" (the unique constraint exists). The **product** rule is one live calendar at a time:

- `connect` (start) and the callback refuse with 409 `calendar_already_connected` when the user has another provider's connection with status `connected`, `degraded` or `needs_reconnect` **and** the calendar feature. A Google row that is Meet-only (Phase I) does not count. The check runs under the same per-user row lock `calendar_connect` already uses, so two tabs cannot both win.
- A `disconnected` row of either provider never blocks.
- The designer's Settings shows both connect buttons only when nothing is connected, so the 409 is only reachable by a second tab or a hand-made request. The message for it is a designer gap, but it needs no new UI element.
- **Data migration needed for this rule: none** (no user has two calendar rows today; only Adebayo's Google is connected).

### 6.4 API and screens (parity with the designer; nothing invented)

- `GET /api/calendar`: `google_email` is replaced by `account_email` and a new `provider` (`google`, `microsoft`), so the Settings card can print the designer's provider label ("Google Calendar", "Microsoft Outlook") and `{email} · Connected`. FE and API ship together (same repo), so no compatibility alias; the mock API, captures and the guide are regenerated in M8.
- `POST /api/calendar/{provider}/connect`, `GET .../callback`, `POST .../disconnect`; `ConnectOut.show_unverified_notice` stays a boolean, true for Microsoft only while `MICROSOFT_PUBLISHER_VERIFIED` is false **and** M0 shows Microsoft shows a warning to that audience.
- **The designer drew:** on Settings, when nothing is connected, two buttons side by side, "Connect Google Calendar" and "Connect Microsoft Outlook", each turning into "Connecting..." while working; when connected, one card with the provider name, "{email} · Connected" and "Disconnect", then the "Join automatically" rules and the template select. On the first-run setup screen (`Auth.dc.html`, designed, not built) the same two choices as rows reading "Connect", "Connecting..." and "Connected". The Settings sentence already names "Google Meet, Microsoft Teams or Zoom". The plan (Needs row 13) had asked the designer to replace the Outlook button with a paste-a-link card; Adebayo's 2026-11-04 decision restores the drawn button, so the drawing is simply built, and the ICS card, if it is still built, is an addition the designer must place.
- **Not drawn, so listed as designer gaps (M6 builds them from the existing Google pattern, with "Microsoft" in the sentences, and lists them in its SOP):** the unverified-app or "needs administrator approval" explainer for work accounts, the callback error messages (`no_mailbox`, `tenant_not_allowed`, `already_connected`), the reconnect state, and the disconnect note that points to Microsoft's permission page. **No Outlook word reaches any screen before `MICROSOFT_CALENDAR_ENABLED` is on and the section 7 gate is met.**

---

## 7. Decision 5: the Teams reality

Most Outlook meetings are Teams meetings: the event's `onlineMeetingProvider` is `teamsForBusiness` (work) or a consumer provider (personal), with a `joinUrl`. The bot cannot join Teams yet (Vexa supports it; the spike P0.7 and T1 to T3 have not run: there is no Teams result in `docs/spike/`). What happens meanwhile, honestly:

| Layer | Behaviour |
|---|---|
| Normaliser | A Teams event becomes `platform = teams`, `meeting_url` = the join URL (kept, never logged, never in an API field; a Teams link carries meeting context in its query), `native_id = None` |
| Rules | `will_join` returns `teams_unsupported` while `TEAMS_JOIN_ENABLED` is false |
| Upcoming | The row says the designer's "Cannot join" with the shipped sentence "Microsoft Teams meetings cannot be joined yet" (`app/calendar_text.py`). The per-meeting switch answers 409 `not_joinable`, as for any event that cannot be joined |
| Runner | Never sees these rows (no `scheduled_joins` row exists for a "no" event) |
| After T2 | `TEAMS_JOIN_ENABLED` on: the same rows become joinable with no Outlook code change, because the Teams path is provider-neutral (T1 parses the URL, T2 fills `native_id`) |

**What must exist before Outlook is worth switching on for people (the gate, D11):** (1) P0.7 passed and the policy sentence it fixed ("Zoom, Google Meet and Microsoft Teams" or the narrower truth) is live (T3); (2) T1 and T2 merged with a live join of a Teams meeting started from an Outlook calendar event; (3) the M0 captures exist for the account types being invited; (4) the privacy and terms rows in 8.2 are merged and read by a lawyer; (5) the connect flow has been run live with a real personal account by the lead (consent screen seen, first sync seen, disconnect seen). Until then the flag stays false in production and the Outlook button is not drawn (the Settings page must look exactly as it does today). **A middle option, rejected:** show Outlook with a plain "most meetings will say Cannot join" line. It adds copy the designer did not draw, and it makes the first impression of the feature a list of refusals.

---

## 8. Security, privacy, and Microsoft's own steps

### 8.1 Review focus for the later Opus review (one list, mapped to tasks)

| # | Focus | Where | Attack or failure to test |
|---|---|---|---|
| 1 | **Refresh-token rotation and the compare-and-swap write** | M4 | Two refreshes race: the stale writer must not overwrite the newer token; a failed write must not lose the connection; the old token must be discarded from memory; no token in a log, `repr` or exception text |
| 2 | **Token storage** | M2 | New vault purpose; a Google blob in a Microsoft row fails to open; `TOKEN_ENC_KEY` missing at start refuses to boot when `MICROSOFT_CLIENT_ID` is set (extends `check_start`) |
| 3 | **State, PKCE, nonce, flow cookie, `return_to`** | M3 | Replay, expiry, a Google state at the Microsoft callback and the reverse, a different signed-in user, login CSRF (a victim lured to the callback with an attacker's code gets `wrong_account`), `//evil` and `/\evil` as `return_to`, redirect URI exactly equal to the registered string |
| 4 | **Tenant confusion** | M2, M3 | `iss` and `tid` must agree; `aud` equals our client id; a token from an unlisted tenant when `MICROSOFT_ALLOWED_TENANTS` is set; connection identity is `tid`+`oid`, never email; a user who reconnects as a different Microsoft identity drops the old identity's events and unstarted joins (the Google rule); guest accounts and accounts with no mailbox are refused by the probe |
| 5 | **ID token trust** | M2 | Used only from the code redemption; refresh `id_token` ignored; `preferred_username` and `/me` mail are labels only; the decision to skip the signature check is recorded here and revisited if Microsoft sign-in is ever added |
| 6 | **Graph URLs** | M4 | `@odata.nextLink` followed only under `https://graph.microsoft.com/v1.0/`; redirects off; `trust_env` off; the `Authorization` header never sent elsewhere; event ids quoted into one path segment; `.` and `..` refused; page cap |
| 7 | **Attacker-controlled event text** | M5 | Anyone can send an invite: title (cut, cleaned), body and location (only scanned for links, then dropped), HTML in the body, a Zoom link in an invite from a stranger (the unanswered-invite safeguard must hold: `notResponded` must map to `needsAction`), Safe Links unwrapping only for the exact host, a link with `user:pass@host` or a port rejected by the existing parser |
| 8 | **Same-organisation test** | M5 | `tenant_domains` from Microsoft only; an organiser address in the tenant's domain that a stranger spoofs would make an unanswered invite join. Decide with the M0 capture: keep the test (relying on Exchange Online's anti-spoofing), or leave `organizer_same_org` false for Microsoft |
| 9 | **Rate limits and throttling** | M4 | A 429 is `TransientError` (the connection retries at the shipped pace after an error); the per-mailbox ceiling (10,000 per 10 minutes, four at once, doc) is far above our two to three calls a minute; no concurrent calls for one connection |
| 10 | **Secret handling** | M2, ops | Client secret only in the form body of a server-side POST, never logged; expiry alert; rotation without downtime (section 9.3) |
| 11 | **One-calendar rule under concurrency** | M3 | Two tabs connecting Google and Outlook at once: exactly one wins |
| 12 | **Disconnect** | M3 | Deletes the same set as C4; no bot is sent after the moment of disconnect (Review Focus 5); the response never claims a revoke that did not happen |
| 13 | **Logging** | all | The existing "no code or token in captured logs" test run over the whole Microsoft flow, including the `error_description` strings and trace ids, which are not logged |

### 8.2 Privacy policy and terms: sentences needed (Microsoft data)

Each row changes **in the same PR as the feature goes live** (the SOP rule). The policy must never run ahead of the product, and a merge deploys, so the M7 PR is **held and merged only at switch-on**, not with the dark build. Both `app/templates/{privacy,terms}.html` and the `web/` copies change (a vitest test compares them). Not legal advice; a lawyer reads them first (handoff section 5).

| Where | New sentence |
|---|---|
| Information we collect, account | add: "If you connect Outlook, we also keep an identifier that Microsoft gives your account (a tenant and user identifier), your name and the email address of that Microsoft account." |
| Calendar information | "Outlook calendar: we read the title, start and end time, whether you are the organiser, your reply, the number of attendees (not their addresses) and the meeting link of events on your main Outlook calendar. We use this only to decide which meetings to join, following the rules you choose, and to show your upcoming meetings in the app. We do not create, change or delete calendar events." (Mirrors the shipped Google sentence; attendee **names and addresses are not stored**, which the designer's draft policy wrongly claims; keep the live text, not the draft.) |
| Who we share with / sources | "Meeting and calendar platforms (Google, Microsoft and Zoom), as needed to join meetings and to read calendar events you have authorised." Microsoft is named because we call Microsoft Graph; **this differs from the ICS route, where no Microsoft API is called** (plan C24) |
| Disconnect | "You can disconnect Outlook in the app, which deletes the calendar data we hold and our stored Microsoft access credential. Microsoft does not let us withdraw that permission for you: to remove Beyric from your Microsoft account, remove it in your Microsoft account's app permissions." (exact Microsoft URL from M0) |
| Security | the existing sentence ("Google access credentials are encrypted ...") becomes "Calendar access credentials are encrypted ..." |
| How long we keep | unchanged wording ("we keep upcoming events and events from the last two days and delete all of it when you disconnect"); it is already provider-neutral |
| Terms, third-party services | the live terms name only Meet, Calendar and Zoom: add "Microsoft Outlook" and "Microsoft Teams" only at the switch-on gate (the designer's draft already lists both) |
| Microsoft terms | Microsoft's identity platform requires the publisher to accept its "Microsoft identity platform for developers Terms of Use" in the verification flow (doc), and the app registration carries our privacy and terms URLs (below) |

### 8.3 Microsoft's own review and verification: what exists and what does not

I found **no Microsoft review gate** like Google's sensitive-scope verification in the pages read: an unverified multitenant app simply works for users who are allowed to consent. (I also found no per-app user cap for unverified apps; **not verified**.) What does exist (all free per the pages read):

| Step | For | Who | Needed when |
|---|---|---|---|
| Branding in the registration: name, logo, home page, terms and privacy URLs (`https://notes.beyrictech.com/`, `/terms`, `/privacy`) | the consent screen | lead | before the first live run |
| **Publisher domain** `beyrictech.com`: verify the domain in the tenant (DNS TXT), host `https://notes.beyrictech.com/.well-known/microsoft-identity-association.json` with `{"associatedApplications":[{"applicationId":"<client id>"}]}` served as `application/json` (doc: wrong content type is rejected) | prerequisite of verification | M7 adds the file (`web/public/.well-known/`); the nginx config sends everything to Next (`location /`), so no edit is expected; **verify in M7**, and it may be a server step | before publisher verification |
| **Publisher verification** (Partner Center CPP global account verified, tenant associated, MFA, roles) | work or school users in default-policy tenants | Adebayo with the lead | before inviting work accounts (Q2, Q3) |
| Microsoft 365 Publisher Attestation and Certification | enterprise trust badges | nobody | not for the pilot (doc: complementary programs) |

### 8.4 Why sign-in by Microsoft is a different feature (D12)

Using Microsoft to log into Beyric makes an ID token the proof of who someone is. The documentation is blunt that `email`, `preferred_username` and UPN must never decide identity (they are mutable and can be controlled by tenant admins or users) and that `tid`+`oid` is the key. A later "Continue with Microsoft" feature must (a) match accounts only on `tid`+`oid` after an explicit link made while signed in, (b) never auto-link by email (the Google rule "a verified password user gets the Google identity linked by email" has no safe Microsoft equivalent, since Microsoft email is not verified), (c) verify the ID token signature against Microsoft's keys, and (d) add `/api/auth/microsoft/callback` to the same registration. None of that is built or designed here.

---

## 9. What Adebayo (or the lead in his Chrome) does in Azure

**Rule for the person doing the clicks:** if any page asks for a card, a phone number for payment, a subscription, "Start free trial", "Upgrade", "Activate" or shows a price, **stop and tell Adebayo**. Nothing below should cost money. Labels are as Microsoft's own pages name them (doc); if the portal shows something different, read the label aloud rather than guessing.

### 9.1 App registration (portal: `https://entra.microsoft.com`)

1. Open `https://entra.microsoft.com` and sign in with a **work or school account** of the Beyrichq tenant. **Billing risk here:** if there is no tenant, Microsoft points to "create a free Azure account", which normally asks for a phone number and a credit card for identity verification. **Stop and ask.**
2. If you have several tenants, click the Settings (gear) icon, then "Directories + subscriptions", and switch to the Beyrichq tenant.
3. In the left menu: **Entra ID** > **App registrations** > **New registration**.
4. **Name:** `Beyric Note Taker`. (Users see this name on the consent screen.)
5. **Supported account types:** choose **"Accounts in any organizational directory (Any Microsoft Entra directory - Multitenant) and personal Microsoft accounts (such as Skype, Xbox)"**.
6. **Redirect URI:** in the platform list choose **Web**, and type exactly `https://notes.beyrictech.com/api/calendar/microsoft/callback`.
7. Click **Register**. On the **Overview** page copy the **Application (client) ID** and the **Directory (tenant) ID** into a note (not a chat).
8. Left menu **Authentication**: confirm the one Web redirect URI is listed; leave "Allow public client flows" at **No**; leave the Implicit grant boxes **unticked**.
9. Left menu **API permissions** > **Add a permission** > **Microsoft Graph** > **Delegated permissions**. Tick **`Calendars.Read`**, **`User.Read`**, **`offline_access`**, **`openid`**, **`profile`**. Click **Add permissions**. Do **not** add anything else. Do **not** press "Grant admin consent" (users consent for themselves).
10. Left menu **Certificates & secrets** > **Client secrets** > **New client secret**. Description `beyric-prod-2026-11`. Expires: **12 months** (Microsoft's maximum is 24; it recommends under 12). Click **Add**, and **copy the secret's Value at once**: it is never shown again. Send it to the server `.env` directly (the lead can paste it into the file with Adebayo watching), never into a chat or a commit.
11. Left menu **Branding & properties**: set the logo (the Beyric logo from `docs/ui/Beyric Note Taker redesign/logo`), **Home page URL** `https://notes.beyrictech.com/`, **Terms of service URL** `https://notes.beyrictech.com/terms`, **Privacy statement URL** `https://notes.beyrictech.com/privacy`. Save.
12. **Optional, only if a developer needs a live run on a laptop:** repeat steps 3 to 10 for a second registration named `Beyric Note Taker (local development)` with Web redirect URI `http://localhost:8000/api/calendar/microsoft/callback` (Microsoft allows `http` only for `localhost`), and a 3-month secret.
13. **Later, not now (Q3):** domain verification and publisher verification (section 8.3). The lead starts the Partner Center enrolment only after Adebayo says yes: **Partner Center identity verification is a point where documents or fees could be requested. Stop and ask.**

### 9.2 New server `.env` variables (names only; values never in git)

`MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_CALENDAR_ENABLED` (default `false`), `MICROSOFT_AUTHORITY` (`consumers` for the pilot, `common` later), `MICROSOFT_ALLOWED_TENANTS` (optional, empty means any), `MICROSOFT_SECRET_EXPIRES_ON` (a date, `YYYY-MM-DD`, drives the 30-day-before reminder), `MICROSOFT_PUBLISHER_VERIFIED` (default `false`, drives the explainer). `TOKEN_ENC_KEY` and `CALENDAR_ENABLED` already exist and are required. `app/config.py` refuses to start in production when `MICROSOFT_CLIENT_ID` is set without a secret or without `TOKEN_ENC_KEY`, the same rule as Google.

### 9.3 Secret rotation (every 12 months, or at once if it leaks)

Create the new secret first (step 10, new description), put it in `.env` as `MICROSOFT_CLIENT_SECRET`, recreate `web` and `scheduler`, confirm a sync succeeds, then delete the old secret in the portal and update `MICROSOFT_SECRET_EXPIRES_ON`. Connections keep working throughout: refresh tokens belong to the user and the app, not to the secret.

### 9.4 Test accounts

| Account | For | Needed by |
|---|---|---|
| A spare **Outlook.com** account (personal) | M0 and every live capture; a few test events: Zoom link in the body, a Meet link, a Teams meeting (a personal Teams link), an all-day event, a recurring series with one moved occurrence, a cancelled meeting, a declined invite, a private event | M0, M2 to M8 |
| One **Microsoft 365 work or school** mailbox in a tenant other than Beyrichq's, ideally with default consent settings | the work-account paths: consent blocked or allowed, `tid`, Safe Links, `verifiedDomains`, the same-organisation test | M0, M8 |
| A second tenant's guest account (optional) | tenant-confusion tests | M3 review |

---

## 10. Implementation plan

Branches `feature/<slug>`; nothing here may reach the remote under any `claude/` name; commits and PRs carry no AI attribution; the lead merges on Adebayo's word. Each task is one PR, follows the plan's standard cycle (fast local checks, CI for the rest), and **depends on C12 (PR #72) being merged** wherever it touches `join_runner.py`, `scheduler.py` or migration numbering. **Sonnet** implements and reviews each task; **Opus** per task only where marked (tokens and OAuth), plus one Opus review of the whole phase before M8 is called ready.

| Task | What | Needs | Opus per task |
|---|---|---|---|
| **M0** Live capture run (no application code) | Adebayo's Azure steps (9.1) done and the two test accounts exist. A throwaway script (not merged to `app/`) connects the real accounts and records **real** responses to `docs/spike/outlook-oauth-captures.md` and redacted JSON under `docs/fe-captures/calendar-outlook/graph/`: S1 token response (the exact `scope` string, ID token claims for a personal and a work account: `tid`, `oid`, `sub`, `preferred_username`, `iss`), S2 consent screenshots (does a personal account see "unverified"? does a work account get blocked?), S3 `calendarView` events of every kind in 9.4 with both `Prefer` values and with `Calendars.ReadBasic` for comparison, S4 `GET /me` and `GET /organization` with `User.Read` only, S5 a work event with Safe Links, S6 error shapes (no mailbox, a revoked grant by removing the app at Microsoft then refreshing, error codes seen, 700082 if reproducible), S7 the personal-account permission page URL and a Teams event's join URL formats | Azure registration, accounts. **Not** C12 | no |
| **M1** Provider-neutral foundation | The registry (6.1), migration `0017` (6.2), column renames, `oauth_flows.provider`, the `{provider}` routes, `account_email` and `provider` on `GET /api/calendar`, shared HTTP helpers lifted out of `gcal_client.py`, sync and runner given a source registry, one-calendar rule (6.3) wired for Google alone. **No behaviour change for Google**; full suite | C12 merged, M0 not needed | no |
| **M2** Microsoft OAuth client | `app/microsoft_oauth.py`: authorize URL (4.1), code redemption, ID token checks (4.4), refresh with rotation result, error mapping (4.3); vault purpose `ms_refresh_token`; `check_start` rule; settings above. httpx `MockTransport` tests whose bodies are copied from the M0 captures | M1, M0 | **Yes** (tokens, tenant checks) |
| **M3** Connect, callback, disconnect | Services and routes for Microsoft on the shared flow code: seven checks plus 4.4 and 4.5, the probe, `tenant_domains`, the 409 rule under the row lock, the flag (routes answer 404 `calendar_unavailable` while off), disconnect with no revoke | M1, M2 | **Yes** (OAuth redirect, state) |
| **M4** Graph source and token cache | `app/graph_client.py`: `MicrosoftSource.sync` (full window, headers, `$select`, nextLink rule, caps, error mapping), `get_event`, `MicrosoftTokenCache` with the compare-and-swap rotation writer, 429 handling, expired-secret alert through `app/alerts.py` | M1, M2 | **Yes** (rotation) |
| **M5** Event normaliser | `app/graph_events.py` to `ParsedEvent` (section 5): zone, links, Safe Links, responses, organiser, cancelled, count, `organizer_same_org`; dispatch in `gcal_sync._parse_all`; fixtures **copied from the M0 captures**; table tests of 5.4 against `will_join` | M1, M0 | no |
| **M6** Settings and Meetings UI | The designer's connect buttons and connected card (parity table, 1440/768/390), provider-aware text (`calendar_text.py`), the designer-gap list of 6.4; drawn **only** when the server says Microsoft is enabled (`GET /api/calendar` carries `microsoft_enabled`), so production looks unchanged while the flag is false | M3, M4, M5 | no |
| **M7** Policy, terms, domain file | Section 8.2 rows in both copies; `web/public/.well-known/microsoft-identity-association.json`; **held until switch-on**; SOP says "Server steps before merge: none" unless the `.well-known` file needs an nginx change | M6, gate in section 7 | no |
| **M8** Close-out | SOP, checklist ticks, the FE guide `docs/fe-integration-guide-calendar-outlook.md` built from **live captures** (list below), regenerated mock API and `GET /api/calendar` captures, designer-gaps list, phase Opus review, go-live checklist | M0 to M7 | phase review |

**Order and parallelism** (at most two or three at once): M0 and M1 start together (M0 needs Adebayo's Azure steps, M1 needs #72 merged); then M2, M5 together; then M3, M4; then M6, M7, M8. Teams (P0.7, T1 to T3) runs beside this and gates only the **switch-on**, not any merge. Switch-on order: lead runs the five-point gate in section 7, sets `MICROSOFT_CALENDAR_ENABLED=true` (then `MICROSOFT_AUTHORITY=common` only after Q2/Q3), recreates `web` and `scheduler`.

### Live captures the later FE guide needs (real responses from a real account, never from schema)

1. `POST /api/calendar/microsoft/connect` (the real `authorize_url` shape, the notice flag). 2. Every callback redirect actually produced: connected, cancelled consent (`microsoft_denied`), a second connect while Google is connected (`already_connected`), a mailbox-less account (`no_mailbox`), a tenant outside `MICROSOFT_ALLOWED_TENANTS` (`tenant_not_allowed`). 3. `GET /api/calendar` connected for a personal and for a work account (`provider`, `account_email`, state text). 4. `GET /api/calendar` with Upcoming rows that include a Teams event with the flag off ("Cannot join: Microsoft Teams meetings cannot be joined yet"), a Zoom-in-body event, an all-day event (absent), a cancelled one, a declined one. 5. Disconnect (`revoked: false`) and the screen after it. 6. `needs_reconnect`: remove the app at Microsoft's permission page, wait for the next sync, capture the state and the plain-words text. 7. The raw Graph payloads (redacted) the normaliser fixtures come from. **Not reproducible live, so marked "not verified" in the guide's table:** a 429, a client-secret expiry, a Conditional Access block, and work-account results if no work mailbox was available.

---

## 11. Spike content (M0), the questions it must close

| # | Question | Why it matters |
|---|---|---|
| S1 | The exact token response: `scope` string format, `tid`/`oid`/`sub`/`preferred_username`/`iss` for personal and work | 4.3, 4.4, the feature check, the identity key |
| S2 | Consent screens: "unverified" for personal accounts? work account blocked? what a blocked user sees | D2, Q2, the designer explainer |
| S3 | `calendarView` payload per event kind; both `Prefer` values honoured; does `Calendars.ReadBasic` return `onlineMeeting`, `location`, `responseStatus`, `organizer`, and do Zoom/Meet links appear outside the body | the scope decision, which is final before testers |
| S4 | `GET /me` and `GET /organization` with `User.Read` alone | `tenant_domains` and the same-organisation test |
| S5 | Safe Links in a work event | 5.5 |
| S6 | Error shapes: no mailbox, revoked grant, 401 after refresh, error codes (700082) | 4.3, 4.5 |
| S7 | Personal-account permissions page URL; Teams join-URL formats (work and personal); `attendees` count versus organiser | 4.6, 5.4, T1 |

---

## 12. Follow-ups, rejected alternatives

**Rejected:** delta queries (D7); a personal-only registration (blocks verification forever); registering under a personal Microsoft account (same); `Calendars.ReadWrite` or any write scope; the cloud-communications `OnlineMeetings.Read` scope (the join URL is on the event); decoding access tokens (documented as unsupported); `revokeSignInSessions` on disconnect; Graph change notifications now (webhooks need renewal and a public endpoint, and polling is needed anyway); a generic "any OIDC provider" abstraction (two providers, a table is enough); keeping `google_*` column names; showing Outlook before Teams joins (section 7).

**Follow-ups, not blocking:** an admin-consent link for blocked work tenants (`/{tenant}/v2.0/adminconsent`, doc, needs its own redirect route and a designed screen); Microsoft Graph change notifications for lower latency; the certificate credential (Q7); Microsoft sign-in (D12); secondary and shared calendars; the "one calendar at a time" rule revisited with a designed duplicate rule; Microsoft 365 Publisher Attestation.

## 13. What this document changes elsewhere

`docs/checklist/master-checklist.md` gets the module "Outlook calendar (Microsoft OAuth plus Graph)" with the tasks above, unchecked. The plan's "Outlook is no longer later" block and spec Q4 stay as written (the ICS route is unchanged and still pending); when M7 merges, the plan lines naming "no Microsoft OAuth app" are amended by the lead. No code, config or migration changes in this PR: **Server steps before merge: none.**

## 14. Sources (Microsoft Learn, read 2026-11-04)

- Supported account types and validation: https://learn.microsoft.com/entra/identity-platform/supported-accounts-validation ; https://learn.microsoft.com/entra/identity-platform/v2-supported-account-types ; https://learn.microsoft.com/entra/identity-platform/howto-modify-supported-accounts ; https://learn.microsoft.com/entra/identity-platform/msal-client-application-configuration
- Authorization code flow, refresh and error codes: https://learn.microsoft.com/entra/identity-platform/v2-oauth2-auth-code-flow
- Refresh tokens, lifetime, rotation, revocation table: https://learn.microsoft.com/entra/identity-platform/refresh-tokens
- Scopes and `offline_access`: https://learn.microsoft.com/entra/identity-platform/scopes-oidc
- Graph permissions reference (`Calendars.Read`, `Calendars.ReadBasic`, `User.Read`, `offline_access`): https://learn.microsoft.com/graph/permissions-reference
- Consent: https://learn.microsoft.com/entra/identity/enterprise-apps/user-admin-consent-overview ; https://learn.microsoft.com/entra/identity-platform/application-consent-experience ; https://learn.microsoft.com/entra/identity-platform/v2-admin-consent ; https://learn.microsoft.com/entra/identity-platform/consent-types-developer
- Publisher verification and domain: https://learn.microsoft.com/entra/identity-platform/publisher-verification-overview ; https://learn.microsoft.com/entra/identity-platform/howto-configure-publisher-domain
- Redirect URI rules: https://learn.microsoft.com/entra/identity-platform/reply-url
- Claims and tenant validation: https://learn.microsoft.com/entra/identity-platform/claims-validation ; https://learn.microsoft.com/entra/identity-platform/id-token-claims-reference ; https://learn.microsoft.com/entra/identity-platform/access-tokens
- App registration and credentials: https://learn.microsoft.com/entra/identity-platform/quickstart-register-app ; https://learn.microsoft.com/graph/auth-register-app-v2 ; https://learn.microsoft.com/entra/identity-platform/how-to-add-credentials
- Calendar API: https://learn.microsoft.com/graph/api/resources/event ; https://learn.microsoft.com/graph/api/resources/responsestatus ; https://learn.microsoft.com/graph/api/user-list-calendarview ; https://learn.microsoft.com/graph/api/event-get ; https://learn.microsoft.com/graph/outlook-calendar-concept-overview ; https://learn.microsoft.com/graph/outlook-calendar-online-meetings
- Delta: https://learn.microsoft.com/graph/api/event-delta ; https://learn.microsoft.com/graph/delta-query-events ; https://learn.microsoft.com/graph/delta-query-overview
- Immutable ids: https://learn.microsoft.com/graph/outlook-immutable-id
- Throttling: https://learn.microsoft.com/graph/throttling-limits#outlook-service-limits ; https://learn.microsoft.com/graph/throttling
- Revoking access: https://learn.microsoft.com/entra/identity-platform/howto-update-permissions ; https://learn.microsoft.com/entra/identity/users/users-revoke-access

Not found in Microsoft's pages (so marked, never asserted): the personal-account permissions page URL, whether a personal-account consent screen says "unverified", the `MailboxNotEnabledForRESTAPI` and `700082` strings, Safe Links in calendar bodies, partner verification lead time, and any per-app user cap for unverified multitenant apps.
