# Frontend integration guide: connecting Google Calendar (task C3)

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
state, settings and upcoming meetings are later tasks (C15a), and disconnect is C4.

## 1. What this is, in the frontend's terms

A signed-in user presses **Connect Google Calendar**. The app sends their browser to Google's consent
screen, Google sends it back, and the app stores an encrypted key that lets the server read their
calendar. The same call also turns on Meet later (**incremental consent**): ask for only the
permission a feature needs, at the moment the user switches that feature on. Sign-in with Google
never touches the calendar.

The whole feature is **off** until the server sets `CALENDAR_ENABLED=true` (and a Google client and an
encryption key exist). While off, both routes answer 404 `calendar_unavailable` (#06, #07): hide the
Connect button on that answer.

## 2. The flow, step by step

1. `POST /api/calendar/google/connect` (signed in, `X-CSRF-Token`). Body is optional.
2. If `show_unverified_notice` is `true`, show the "Google hasn't verified this app" explainer
   **before** navigating. Then `window.location = authorize_url` (a full navigation, not `fetch`).
3. The user presses Allow (or Cancel, or unticks a box) on Google's screen.
4. Google redirects the browser to `GET /api/calendar/google/callback`. The app answers with a **302**
   to the page you named in `return_to` (default `/settings/calendar`), with the result in the query
   string. **The callback never returns JSON.** Read `calendar` / `calendar_error` from the URL on
   that page, show the message, then refresh the calendar state.

### 2.1 `POST /api/calendar/google/connect`

Request body (all fields optional, unknown fields are refused):

| Field | Meaning |
|---|---|
| `features` | `["calendar"]` (default), `["meet"]` or `["calendar", "meet"]`. Anything else: 422 (#05) |
| `return_to` | A relative path to land on after Google. It must start with exactly one `/`. Anything else (`//evil`, `https://...`, `/\evil`) is **silently replaced by `/settings/calendar`**: nothing is rejected, nothing leaks |

```json
{ "return_to": "/settings/calendar" }
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
| `/settings/calendar?calendar=connected` | Stored. The first sync starts within a minute or so | "Google Calendar connected" |
| `...?calendar_error=google_denied` | The user pressed Cancel | "You didn't connect your calendar." No error styling |
| `...?calendar_error=scope_missing` | The user **unticked the calendar box** on Google's screen. Nothing was stored | "To join your meetings automatically we need permission to read your calendar events. Try again and leave the calendar box ticked." (The old connection, if there was one, is untouched) |
| `...?calendar_error=no_refresh_token` | Google gave no long-lived key (it sometimes does not), and the old one cannot stand in (first connect, a different Google account, a dead connection, or a new permission). Nothing stored | "Google didn't finish connecting. Remove Beyric at myaccount.google.com/permissions and try again." **Not reproduced live** (the fake can omit the key; a real Google that omits it was not seen) |
| `...?calendar_error=google_failed` | Anything else: an unknown, used or expired state; a different browser; Google or the exchange failed | "Something went wrong connecting Google. Try again." |
| `...?calendar_error=wrong_account` | The signed-in user is not the one who started the connect (a link forwarded or planted) | "This connection was started from a different account. Sign in with the right account and start again." |
| `/login?error=google_failed` | Nobody was signed in when Google came back (#25) | The login page already handles `google_failed` |

**Field and path traps**

- The result is in the **query string of your own page**, not in JSON. If `return_to` already has a
  query (`/settings/calendar?tab=rules`) the result is appended after it
  (`?tab=rules&calendar=connected`).
- Errors land on the stored `return_to` when the app could tell where the user came from; when the
  flow itself is unknown, used or expired it lands on the default `/settings/calendar`.
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
  "google_account_email": "grace@example.test",
  "google_sub": "g-grace-1",
  "google_hosted_domain": null,
  "status": "connected",
  "granted_scopes": "email https://www.googleapis.com/auth/calendar.events.readonly openid",
  "refresh_token_enc": "<bytes, 51 bytes>",
  "key_version": 1,
  "last_sync_at": null,
  "sync_token": null,
  "expected_expiry_at": null
}
```

- The connected Google account **can differ from the sign-in email** (`google_account_email`). The
  Settings screen must show which Google account is connected, not assume the login address.
- `refresh_token_enc` is bytes, never text, and no API ever returns it.
- A **reconnect replaces** the row (#26–#28): still one connection per user. Connecting a different
  Google account than before also removes the old account's upcoming events and the joins that had not
  started (a join already running keeps going).
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

## 6. Verification table

| Behaviour | Verified |
|---|---|
| `POST connect` bodies, status codes, errors 401/403/404/422 and the `calendar_flow` cookie | **live** against the running app (#03–#08, #32) |
| Callback redirects: connected, cancelled, calendar box unticked, reused state, other browser, different user, signed out, reconnect, Meet upgrade | **live** against the running app **with Google's side faked** (#09–#31) |
| The stored row, token as bytes | **live** (#10, #28, #31) |
| 503 `calendar_unavailable` (no encryption key) | tested only (`tests/test_calendar_connect.py`), not captured |
| `no_refresh_token` from a real Google | not seen; the fake can omit the token |
| Google's real consent screen, real scope names (Google may return `https://www.googleapis.com/auth/userinfo.email` where the fake returns `email`), the "unverified app" page, `hd` for a Workspace account | **not verified**: needs Adebayo's Gmail test account |
| `show_unverified_notice` after Google verifies the app | the flag flips in #32; Google's side not seen |
