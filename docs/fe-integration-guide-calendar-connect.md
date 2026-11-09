# Frontend integration guide: connecting and disconnecting Google Calendar (tasks C3, C4)

Everything below is copied from responses captured against the running app by
`scripts/capture_calendar_connect.py` (real app, real migrations, real database, real HTTP). The files
are in `docs/fe-captures/calendar-connect/NN-name.json`; `NN` is cited as **#NN**. Run the script
again to re-derive any claim. Emails and ids in the captures are placeholders.

**What was NOT captured live: a real Google consent.** Google's own servers (the consent screen and
the token endpoint) are replaced by `tests/fakes/google.py`, which checks the PKCE verifier, the
redirect URI and the single use of a code the way Google does. So every response and redirect below
is the real app's, but what Google itself shows and returns (its consent screen text, its real
scope names, the "Google hasn't verified this app" page) has **not** been seen. That check needs
Adebayo's Gmail test account and is listed in the last section.

Read this with `docs/fe-integration-guide-owner-app.md` (session cookie, CSRF, error shape) and
`docs/fe-integration-guide-pilot-accounts.md`. Only the connect step is covered here; the calendar
state and upcoming meetings (`GET /api/calendar`) are in `docs/fe-integration-guide-calendar-settings.md`, the settings and the per-meeting switch are later tasks (C15a part 2). Disconnect (C4) is section 6.

## 1. What this is, in the frontend's terms

A signed-in user presses **Connect Google Calendar**. The app sends their browser to Google's consent
screen, Google sends it back, and the app stores an encrypted key that lets the server read their
calendar. The same call also turns on Meet later (**incremental consent**): ask for only the
permission a feature needs, at the moment the user switches that feature on. Sign-in with Google
never touches the calendar.

The whole feature is **off** until the server sets `CALENDAR_ENABLED=true` (and a Google client and an
encryption key exist). While off, both routes answer 404 `calendar_unavailable` (#06, #07): hide the
Connect button on that answer.

## 1b. Provider-neutral names (Outlook phase O, M1, 2026-11-05)

The connect, callback and disconnect routes are now `/api/calendar/{provider}/...` with `provider` one of
`google` or `microsoft`. **Only `google` exists.** The Google URLs in this guide are unchanged
(`/api/calendar/google/connect`, `.../callback`, `.../disconnect`) and behave exactly as captured below. A
request for `microsoft` answers `404 calendar_unavailable` ("This calendar is not available."); any other
name is a 422. Nothing in the app may call a `microsoft` route or print Outlook wording yet.

What else changed, and what to build on it:

| Change | Where it shows |
|---|---|
| The stored-row names in the captures below were renamed (`google_account_email` to `account_email`, `google_sub` to `account_subject`, `google_hosted_domain` to `account_domain`). **No API returns these rows**; they are the database dumps of the captures. The files were renamed in place; a fresh run of `scripts/capture_calendar_connect.py` on 2026-11-05 produced the same keys | #10, #28, #31, #35, #38, #40, #44, #46 |
| New callback error `?calendar_error=already_connected` and new connect error **409 `calendar_already_connected`** ("Another calendar is already connected. Disconnect it first."): the user already has *another provider's* calendar connected. Only reachable once Microsoft exists, since nobody has two providers. A Meet-only Google connection never causes it, and neither does a disconnected one | **tested only** (`tests/test_calendar_providers.py`, with a stand-in Microsoft row); not capturable live yet |
| `GET /api/calendar` now has `account_email` and `provider` instead of `google_email` (see the settings guide) | `fe-integration-guide-calendar-settings.md` |


## 2. The flow, step by step

1. `POST /api/calendar/google/connect` (signed in, `X-CSRF-Token`). Body is optional.
2. If `show_unverified_notice` is `true`, show the "Google hasn't verified this app" explainer
   **before** navigating. Then `window.location = authorize_url` (a full navigation, not `fetch`).
3. The user presses Allow (or Cancel, or unticks a box) on Google's screen.
4. Google redirects the browser to `GET /api/calendar/google/callback`. The app answers with a **302**
   to the page you named in `return_to` (default `/settings`), with the result in the query
   string. **The callback never returns JSON.** Read `calendar` / `calendar_error` from the URL on
   that page, show the message, then refresh the calendar state.

### 2.1 `POST /api/calendar/google/connect`

Request body (all fields optional, unknown fields are refused):

| Field | Meaning |
|---|---|
| `features` | `["calendar"]` (default), `["meet"]` or `["calendar", "meet"]`. Anything else: 422 (#05) |
| `return_to` | A relative path to land on after Google. It must start with exactly one `/`. Anything else (`//evil`, `https://...`, `/\evil`) is **silently replaced by `/settings`**: nothing is rejected, nothing leaks |

```json
{ "return_to": "/settings" }
```

Response 200 (#08):

```json
{
  "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth?client_id=capture-client-id.apps.example.test&redirect_uri=https%3A%2F%2Fnotes.example.test%2Fapi%2Fcalendar%2Fgoogle%2Fcallback&response_type=code&scope=openid+email+https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fcalendar.events.readonly&state=<state>&nonce=<nonce>&code_challenge=<code_challenge>&code_challenge_method=S256&access_type=offline&prompt=consent&include_granted_scopes=true&login_hint=admin%40example.test",
  "show_unverified_notice": true
}
```

(`<state>`, `<nonce>`, `<code_challenge>` are masked in the capture; the real URL carries random
values.) The response also sets the `calendar_flow` cookie (`HttpOnly; Max-Age=600;
Path=/api/calendar/google; SameSite=lax`). **Do not read or send it yourself**: the browser does. It
ties the callback to this browser, so the same-origin navigation must happen in the same browser
within **10 minutes** (the flow's life).

| Field | Meaning |
|---|---|
| `authorize_url` | Navigate to it. Do not parse it, store it or show it |
| `show_unverified_notice` | `true` until the server sets `GOOGLE_APP_VERIFIED=true` (#08 `true`, #32 `false`). Show the explainer first when true |

Errors (the usual `{"error": {"code", "message"}}` shape):

| Status | `code` | When |
|---|---|---|
| 401 | `not_authenticated` | no session (#03) |
| 403 | `csrf_rejected` | missing or wrong `X-CSRF-Token` (#04) |
| 404 | `calendar_unavailable` | the feature is switched off or Google is not configured (#06). Message: `Google Calendar is not available.` |
| 422 | `validation_error` | bad `features`; `fields` names it (#05: `Input should be 'calendar' or 'meet'`) |
| 503 | `calendar_unavailable` | the server has no usable encryption key, so it refuses before Google is involved. Message: `Google Calendar cannot be connected right now.` **Not captured live** (it needs a server without a key; covered by a test) |

### 2.2 `GET /api/calendar/google/callback` (the redirect Google sends the browser to)

You never call this. The result is always a **302**, and the app also deletes the `calendar_flow`
cookie. The `Location` values below are exact (#09, #12, #14, #15, #19, #23, #25):

| Location | Meaning | What to show |
|---|---|---|
| `/settings?calendar=connected` | Stored. The first sync starts within a minute or so | "Google Calendar connected" |
| `...?calendar_error=google_denied` | The user pressed Cancel | "You didn't connect your calendar." No error styling |
| `...?calendar_error=scope_missing` | The user **unticked the calendar box** on Google's screen. Nothing was stored | "To join your meetings automatically we need permission to read your calendar events. Try again and leave the calendar box ticked." (The old connection, if there was one, is untouched) |
| `...?calendar_error=no_refresh_token` | Google gave no long-lived key (it sometimes does not), and the old one cannot stand in (first connect, a different Google account, a dead connection, or a new permission). Nothing stored | "Google didn't finish connecting. Remove Beyric at myaccount.google.com/permissions and try again." **Not reproduced live** (the fake can omit the key; a real Google that omits it was not seen) |
| `...?calendar_error=google_failed` | Anything else: an unknown, used or expired state; a different browser; Google or the exchange failed | "Something went wrong connecting Google. Try again." |
| `...?calendar_error=different_account` | A Google account is **already connected and working**, and the person picked a **different** Google account in Google's chooser (#34). Nothing was changed: the first connection, its events and its permissions stay as they were (#35) | "A different Google account is already connected ({email}). To use another one, disconnect first." Needs the connected email: `google_email` of `GET /api/calendar` (`docs/fe-integration-guide-calendar-settings.md`); the disconnect call is section 6 |
| `...?calendar_error=wrong_account` | The signed-in user is not the one who started the connect (a link forwarded or planted) | "This connection was started from a different account. Sign in with the right account and start again." |
| `/login?error=google_failed` | Nobody was signed in when Google came back (#25) | The login page already handles `google_failed` |

**Field and path traps**

- The result is in the **query string of your own page**, not in JSON. If `return_to` already has a
  query (`/settings?tab=rules`) the result is appended after it
  (`?tab=rules&calendar=connected`).
- Errors land on the stored `return_to` when the app could tell where the user came from; when the
  flow itself is unknown, used or expired it lands on the default `/settings`.
- `calendar` and `calendar_error` are never both present.
- Remove the two parameters from the address bar (`history.replaceState`) after reading them, so a
  reload does not show the message again.

## 3. Incremental consent (turning Meet on later)

Call connect with `{"features": ["meet"]}`. Google then shows only the Meet permission; the calendar
permission given earlier comes back with it (#29, #30: `calendar=connected`, and the stored scopes
hold both, #31). `["calendar", "meet"]` asks for both at once. If the user unticks the Meet box the
answer is `scope_missing` and the existing connection stays exactly as it was.

A Meet-only connection (no calendar permission) is valid: Meet detection does not need the calendar.
What a connection can do is derived from the scopes Google granted, never from what was asked, so an
unticked box can never switch a feature on.

## 4. What the server keeps (for the Settings screen, later tasks)

#10 is the stored row after the first connect, read from the database:

```json
{
  "user_id": 1,
  "provider": "google",
  "account_email": "grace@example.test",
  "account_subject": "g-grace-1",
  "account_domain": null,
  "status": "connected",
  "granted_scopes": "email https://www.googleapis.com/auth/calendar.events.readonly openid",
  "refresh_token_enc": "<bytes, 51 bytes>",
  "key_version": 1,
  "last_sync_at": null,
  "sync_token": null,
  "expected_expiry_at": null
}
```

- The connected Google account **can differ from the sign-in email** (`account_email`, called `google_account_email` before 2026-11-05). The
  Settings screen must show which Google account is connected, not assume the login address.
- `refresh_token_enc` is bytes, never text, and no API ever returns it.
- A **reconnect with the same Google account replaces** the row (#26–#28): still one connection per
  user. A **different** Google account is refused (`different_account`, #34) while the connection
  works (`status` `connected` or `degraded`), so an accidental pick in Google's chooser can never
  swap or wipe the calendar. A switch needs the old connection removed first (disconnect, section 6) or dead
  (`needs_reconnect`); only then is the different account accepted, and the old account's events,
  the joins that had not started and its Meet subscription are dropped (a join already running keeps
  going).
- When Google issues no new long-lived key but the same account is already connected with a working
  key, the connect still succeeds and the newly granted permissions are merged into the stored list.
- `expected_expiry_at` is set to seven days after connecting **only** while the server flag
  `GOOGLE_OAUTH_TESTING` is on (the Google project is in Testing and tokens lapse). Null otherwise.
- These fields reach the frontend through `GET /api/calendar` (task C15a), not yet built.

## 5. UX consequences the frontend must surface

- **Show the unverified-app explainer** when `show_unverified_notice` is true, before leaving the
  site: testers will see Google's "Google hasn't verified this app" page and must know to choose
  Advanced, then continue.
- **Always handle the return**, including cancel. A user who presses Cancel lands back on your page
  with `calendar_error=google_denied`.
- **Say which permission is needed** when `scope_missing` comes back; the user unticked it on purpose
  or by accident.
- **Tell testers that a Google Testing project drops the connection after 7 days** while
  `expected_expiry_at` is set (later tasks add it to the state).
- **Do not retry silently.** Each connect makes a new Google consent; a failed one needs the user's
  own click.

## 6. Disconnect (task C4)

`POST /api/calendar/google/disconnect`: signed in, `X-CSRF-Token`, **no body**. The "Disconnect Google"
button calls it, after a confirmation (it deletes data, see below).

Request and response, captured (#39):

```
POST /api/calendar/google/disconnect        (X-CSRF-Token: <csrf-B>, no body)
200 {"status": "disconnected", "revoked": true}
```

`revoked` is `true` when Google confirmed the access is gone (#39) and `false` when Google could not
be asked (#45: Google's revoke endpoint answered 503, or the server could not read its stored key).
**When it is `false`, show it** (see 6.1): the data is deleted here either way, but Google may still
list Beyric as having access.

- **Always 200 with `status` `disconnected`**, including a second click (#41, `revoked: true`: a repeat
  has nothing left to revoke and cannot re-check an earlier failure, so **act on the first answer**),
  a second tab, and a user who has no connection at all (`revoked: true`, tested only). There is no "not connected" error to handle. After it, re-read the
  calendar state and show the not-connected card.
- **It works while the calendar feature is switched off** (`CALENDAR_ENABLED=false`): unlike connect it
  never answers 404 `calendar_unavailable`. Tested only (`tests/test_calendar_connect.py`), not captured.
- **A Google outage is not an HTTP error** (#45): if Google cannot be asked to revoke, the call is
  still 200 and the data is still deleted here, with `"revoked": false`. Do not show a failure, but do
  show the notice in 6.1.
- Errors, exactly as returned: signed out `401` `{"error": {"code": "not_authenticated", "message":
  "Log in first."}}` (#36); missing or wrong CSRF `403` `{"error": {"code": "csrf_rejected",
  "message": "Missing or wrong X-CSRF-Token header. Read it from GET /api/session."}}` (#37).

**What it deletes and what stays** (state of the database before #38, after #40):

| Deleted | Kept |
|---|---|
| The stored Google key (the revoke is sent to Google first), the Google email, account id and permissions | Meetings already recorded, with their titles and links |
| The user's calendar events | The hours used |
| **Every** scheduled join, in every state | The user's calendar settings (rule, template) |
| Calendar notifications | Other notifications (for example the hours notice) |

The row after (#40): `"status": "disconnected"`, `"account_email": null`, `"account_subject": null`,
`"granted_scopes": null`, `"refresh_token_enc": null`, `"key_version": null`, calendar rows 0. The
counts before were 2 events and 2 joins (one `scheduled`, one `joined`) and the notification kinds
`calendar.not_recorded` and `usage.hours_low`; after, 0, 0 and `usage.hours_low` only.

**A meeting being recorded right now carries on** and ends up in Meetings as usual (tested, not
captured: the captures have no running meeting). Its scheduled join is gone, so it no longer shows as
a calendar join in Upcoming.

**Connecting again** works at once (#42, #43): the same 302 to `/settings?calendar=connected`,
for the same Google account or **another** one (#44 shows the other account connected). This is the
way to **switch accounts**: `different_account` is refused while one works, so show "Disconnect, then
connect the other account".

### 6.1 UX consequences the frontend must surface for disconnect

- **When `revoked` is `false`, tell the person**: "Your calendar data is deleted from Beyric, but we
  could not reach Google to remove our access. Remove Beyric at
  https://myaccount.google.com/permissions to finish." Show it once, on the response to the click
  (a repeat answers `true`). Not captured with a real Google outage, only with Google's endpoint
  scripted to answer 503 (#45).
- **Confirm first, and say what goes**: "This stops Beyric joining your calendar meetings and deletes
  your upcoming calendar meetings and join schedule from Beyric. Meetings already recorded stay."
  Disconnecting cannot be undone; a reconnect starts a fresh first sync.
- **Do not promise the meeting in progress will stop**: it keeps recording.
- **Do not treat `disconnected` as a lapse.** The user chose it: no red "your calendar is disconnected"
  banner, no reconnect nag. That banner is for `needs_reconnect` (C14, C15a).
- After success, clear any cached upcoming list and the join switches on screen.

## 7. Verification table

| Behaviour | Verified |
|---|---|
| `POST connect` bodies, status codes, errors 401/403/404/422 and the `calendar_flow` cookie | **live** against the running app (#03–#08, #32) |
| Callback redirects: connected, cancelled, calendar box unticked, reused state, other browser, different user, signed out, reconnect, Meet upgrade | **live** against the running app **with Google's side faked** (#09–#31) |
| The stored row, token as bytes | **live** (#10, #28, #31) |
| 503 `calendar_unavailable` (no encryption key) | tested only (`tests/test_calendar_connect.py`), not captured |
| `no_refresh_token` from a real Google | not seen; the fake can omit the token |
| Google's real consent screen, real scope names (Google may return `https://www.googleapis.com/auth/userinfo.email` where the fake returns `email`), the "unverified app" page, `hd` for a Workspace account | **not verified**: needs Adebayo's Gmail test account |
| `404 calendar_unavailable` for `/api/calendar/microsoft/*`, `422` for an unknown provider, 409 `calendar_already_connected`, callback `already_connected` | **tested only** (`tests/test_calendar_providers.py`); not captured, they cannot happen against the running app until Microsoft ships |
| `different_account` while a working connection exists | **live** against the running app, Google faked (#33–#35) |
| Replacing a dead connection with another account (events dropped), merge of scopes without a new key | tested only (`tests/test_calendar_connect.py`), not captured |
| `show_unverified_notice` after Google verifies the app | the flag flips in #32; Google's side not seen |
| `POST disconnect`: 200 `{"status": "disconnected", "revoked": true}`, a repeat, the 401 and 403 errors, the row emptied, events, joins and calendar notifications deleted, the unrelated notification kept, connect again with another account | **live** against the running app, Google faked (#36–#44) |
| Disconnect while Google's revoke endpoint answers 503: still 200 with `"revoked": false`, deleted locally | **live**, Google faked (#45, #46). Google's real answer to a revoke was not seen |
| Disconnect with the feature flag off; a running meeting carries on; meetings, usage and settings kept; only the caller's rows touched | tested only (`tests/test_calendar_connect.py`), not captured |
| A real Google revoke (token stops working, Beyric leaves myaccount.google.com/permissions) | **not verified**: needs Adebayo's Gmail test account |

**Outlook.** The same `{provider}` routes connect a Microsoft Outlook calendar (`microsoft` instead of `google`): see `docs/fe-integration-guide-calendar-outlook.md`. `DisconnectOut` gained an optional `reason` (absent for Google).
