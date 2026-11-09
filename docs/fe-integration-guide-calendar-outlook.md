# FE integration guide: the Outlook (Microsoft) calendar

**Module:** Outlook calendar, phase O (M2 to M7). **Backend:** `/api/calendar/microsoft/...` and the `microsoft_enabled` field of `GET /api/calendar`. **Companion guides:** `docs/fe-integration-guide-calendar-connect.md` (the Google connect; the routes are the same `{provider}` routes) and `docs/fe-integration-guide-calendar-settings.md` (the state, settings and per-meeting switch).

> **How much to trust this guide.** Every request and response below was copied from `docs/fe-captures/calendar-outlook/` by `scripts/capture_calendar_outlook.py`, which drives the **real app** (guard, CSRF, validation, cookies, the real consent URL, the real ID-token checks, the real probe request, the real vault and database) over HTTP. **Microsoft's own servers were not involved**: a fake (`tests/fakes/microsoft.py`) answers the consent screen, the token endpoint and the three Graph calls with *documented* shapes. **The M0 run against a real account has not happened, so nothing here has been seen from a real Microsoft account.** The table at the end marks each behaviour. Placeholder values (`ada@outlook.example.test`, the all-zero object ids, the masked `<state>`, `<nonce>`, cookie values) are fakes, never real data.

## 1. What the feature is

A person can connect an Outlook.com, Hotmail or Live calendar instead of Google's. After that the same Upcoming list, rules and per-meeting switches work, and the note taker joins Zoom and Google Meet links found in the events. **Teams meetings are recognised but cannot be joined yet**, so every Teams event reads "Cannot join" (section 6). One calendar at a time: Google **or** Outlook. Work or school accounts are not supported in the pilot (the server only accepts personal accounts until it is switched to `common`).

**Draw nothing new until the server says so.** `GET /api/calendar` has `microsoft_enabled`. While it is `false` the Settings page and the first-run rows look exactly as they did before Outlook existed (Google's button, and the Outlook button disabled with "Not available yet."). Only when it is `true` draw "Connect Microsoft Outlook" as a live button next to Google's.

## 2. `GET /api/calendar` (the state)

`GET /api/calendar` (capture `45-off-state.json`)

Response **200**:

```json
{
  "connected": false,
  "state": "not_connected",
  "state_text": "Google Calendar is not connected.",
  "account_email": null,
  "provider": null,
  "last_sync_at": null,
  "last_error": null,
  "rule": "all",
  "rule_text": "Joining all meetings",
  "calendar_template_id": 1,
  "auto_join_paused": false,
  "auto_join_paused_text": null,
  "microsoft_enabled": false,
  "upcoming": []
}
```

Not connected, Outlook off. The same answer with Outlook on:

`GET /api/calendar` (capture `03-state-not-connected.json`)

Response **200**:

```json
{
  "connected": false,
  "state": "not_connected",
  "state_text": "Google Calendar is not connected.",
  "account_email": null,
  "provider": null,
  "last_sync_at": null,
  "last_error": null,
  "rule": "all",
  "rule_text": "Joining all meetings",
  "calendar_template_id": 1,
  "auto_join_paused": false,
  "auto_join_paused_text": null,
  "microsoft_enabled": true,
  "upcoming": []
}
```

Connected to Outlook, before the first sync (capture `21`):

```json
{
  "connected": true,
  "state": "connected",
  "state_text": "Connected",
  "account_email": "ada@outlook.example.test",
  "provider": "microsoft",
  "last_sync_at": null,
  "last_error": null,
  "rule": "all",
  "rule_text": "Joining all meetings",
  "calendar_template_id": 1,
  "auto_join_paused": false,
  "auto_join_paused_text": null,
  "microsoft_enabled": true,
  "upcoming": []
}
```

Needs to connect again (the row was set by hand to what a revoked grant leaves; capture `37`):

```json
{
  "connected": false,
  "state": "needs_reconnect",
  "state_text": "Microsoft access was removed. Connect again to carry on.",
  "account_email": "ada@outlook.example.test",
  "provider": "microsoft",
  "last_sync_at": null,
  "last_error": "Microsoft no longer lets Beyric read your calendar. Connect again to fix this.",
  "rule": "all",
  "rule_text": "Joining all meetings",
  "calendar_template_id": 1,
  "auto_join_paused": false,
  "auto_join_paused_text": null,
  "microsoft_enabled": true,
  "upcoming": []
}
```

**Traps.**
- `provider` is `"microsoft"` (not `"outlook"`). The label to print is **"Microsoft Outlook"** (Google's is "Google Calendar").
- `account_email` is a **label**, not an identity: it comes from Microsoft's profile and is only shown. Never match accounts on it.
- `microsoft_enabled` is present on **every** answer of `GET /api/calendar` and `PUT /api/calendar/settings` (not nested). It is `false` unless the server has Outlook on.
- `state_text` for `not_connected` still says "Google Calendar is not connected." Do not print it when nothing is connected (the Settings card draws buttons then); `state_text` for `needs_reconnect` is in the provider's own words ("Microsoft access was removed. Connect again to carry on.").
- `last_error` is plain words and never carries a status or a Microsoft message.

## 3. Connect

`POST /api/calendar/microsoft/connect` (capture `07-connect.json`)

Request body:

```json
{
  "features": [
    "calendar"
  ],
  "return_to": "/settings"
}
```

Response **200**:

```json
{
  "authorize_url": "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize?client_id=capture-ms-client-id&response_type=code&redirect_uri=https%3A%2F%2Fnotes.example.test%2Fapi%2Fcalendar%2Fmicrosoft%2Fcallback&response_mode=query&scope=openid+profile+offline_access+https%3A%2F%2Fgraph.microsoft.com%2FUser.Read+https%3A%2F%2Fgraph.microsoft.com%2FCalendars.Read&state=<state>&nonce=<nonce>&code_challenge=<code_challenge>&code_challenge_method=S256&prompt=select_account",
  "show_unverified_notice": false
}
```

Navigate the browser to `authorize_url` (a full page load, never `fetch`). The response also sets the cookie `calendar_flow_ms` (path `/api/calendar/microsoft`, HttpOnly, 10 minutes): it ties the callback to this browser, so the connect call and the redirect back must be the same browser.

`show_unverified_notice` is `false` here because the pilot accepts personal accounts only. It becomes `true` only for the work-account setup while the publisher is unverified (not captured: the server was configured for personal accounts). When it is `true`, show the explainer before navigating (the Settings guide describes the pattern; Microsoft's words are in `settings-calendar.tsx`).

Errors of `connect`:

| Status | Body | When | Capture |
|---|---|---|---|
| 401 | `{"error": {"code": "not_authenticated", "message": "Log in first."}}` | no session | `04-connect-without-login` |
| 403 | `{"error": {"code": "csrf_rejected", "message": "Missing or wrong X-CSRF-Token header. Read it from GET /api/session."}}` | no CSRF header | `05-connect-without-csrf` |
| 422 | `{"error": {"code": "invalid_features", "message": "Microsoft Outlook connects the calendar only."}}` | `features` other than `["calendar"]` | `06-connect-meet-feature` |
| 404 | `{"error": {"code": "calendar_unavailable", "message": "This calendar is not available."}}` | Outlook is switched off | `46-off-connect` |
| 409 | `{"error": {"code": "calendar_already_connected", "message": "Another calendar is already connected. Disconnect it first."}}` | Google (or a Google that needs reconnecting) is connected | `26-google-connect-while-outlook-is-connected` shows the reverse (Google refused while Outlook is connected); the Outlook side is the same code |
| 503 | `calendar_unavailable` | the server cannot store a token safely | not captured (needs a server without its key); shape from the Google guide |

## 4. The callback redirect

Microsoft sends the browser to `GET /api/calendar/microsoft/callback`; the server always answers **302**, never JSON, to the `return_to` given at connect (`/settings`), and clears the flow cookie. The captured redirects:

| `Location` | When | Capture |
|---|---|---|
| `/settings?calendar_error=microsoft_failed` | unknown or used-up state, or no flow cookie | `08-callback-state-unknown.json` |
| `/settings?calendar_error=microsoft_denied` | the person pressed Cancel at Microsoft | `10-callback-denied.json` |
| `/settings?calendar_error=no_mailbox` | the account has no calendar to read (the probe answered 404) | `12-no-mailbox-callback.json` |
| `/settings?calendar_error=tenant_not_allowed` | a work-account tenant at the pilot's personal-only endpoint | `14-tenant-not-allowed-callback.json` |
| `/settings?calendar_error=scope_missing` | the calendar permission was not granted | `16-scope-missing-callback.json` |
| `/settings?calendar_error=different_account` | another Microsoft identity while one works | `28-different-account-callback.json` |
| `/settings?calendar_error=already_connected` | another calendar was connected while this one was in progress | `33-race-outlook-callback.json` |
| `/settings?calendar=connected` | connected | `19-personal-callback.json` |

Words for each code are in `settings-calendar.tsx` (`messageFor`); the designer drew none of them (lead-designed, listed in the M6 SOP). **Never print the code itself.** A code the page does not know becomes the generic "Something went wrong connecting Google. Try again." (the Settings component's existing fallback).

A signed-out visitor at the callback goes to `/login?error=microsoft_failed` (not captured: the test client sends cookies; shape from the Google guide and the test `test_a_signed_out_visitor_goes_to_login_and_the_state_is_used_up`). A different signed-in user than the one who started the flow gets `wrong_account` (tested, not captured).

## 5. Disconnect

`POST /api/calendar/microsoft/disconnect` (capture `39-disconnect.json`)

Response **200**:

```json
{
  "status": "disconnected",
  "revoked": false,
  "reason": "provider_has_no_revoke"
}
```

There is **no way for an app to remove its own access at Microsoft**, so the answer says `revoked: false` with `reason: "provider_has_no_revoke"` when a token existed: show "Microsoft Outlook disconnected." and tell the person to remove Beyric Note Taker in their Microsoft account's app permissions (the page address is not confirmed, so none is printed). Repeating the call:

`POST /api/calendar/microsoft/disconnect` (capture `41-disconnect-again.json`)

Response **200**:

```json
{
  "status": "disconnected",
  "revoked": true
}
```

**Trap:** `reason` is **absent** (not `null`) whenever it does not apply: Google's answer is unchanged, `{"status": "disconnected", "revoked": true}`. Test `"reason" in body`, not `body.reason === null`. After a disconnect the local copy is gone (`40-rows-after-disconnect`: no events, no joins, the calendar notification deleted, `usage.hours_low` kept). The call also works while Outlook is switched off (`48-off-disconnect`).

## 6. Upcoming rows, Teams, one calendar

A Teams event (capture `22`) is listed, and read as:

```json
{
  "id": 1,
  "title": "Planning (Teams)",
  "starts_at": "2026-11-09T17:54:15.800183+01:00",
  "ends_at": "2026-11-09T18:24:15.800183+01:00",
  "platform": "teams",
  "platform_label": "Microsoft Teams",
  "attendee_count": 2,
  "will_join": false,
  "will_join_text": "Cannot join",
  "reason_text": "Microsoft Teams meetings cannot be joined yet",
  "join_id": null,
  "can_send_now": false,
  "not_recorded_text": null
}
```

The per-meeting switch on it is refused:

`PUT /api/calendar/events/1/override` (capture `23-override-teams-409.json`)

Request body:

```json
{
  "override": true
}
```

Response **409**:

```json
{
  "error": {
    "code": "not_joinable",
    "message": "Microsoft Teams meetings cannot be joined yet"
  }
}
```

Show "Cannot join" and the `reason_text`; do not offer the switch. A Zoom row beside it reads `will_join: true`, `will_join_text: "Will join"`. The Teams link itself never reaches the browser.

## 7. Switched off

With Outlook off (captures `45` to `48`): `GET /api/calendar` answers `microsoft_enabled: false`; `connect` and `callback` answer **404** `calendar_unavailable` (`{"error": {"code": "calendar_unavailable", "message": "This calendar is not available."}}`); `disconnect` still answers 200. **If the switch is turned off while a person has an Outlook connection**, `GET /api/calendar` keeps showing it connected but it stops syncing (the sync source is only built while the switch is on): do not rely on that state; it is an operator mistake, not a state to design for.

## 8. States and polling

States of `state`: `not_connected` (buttons), `connected` and `degraded` (the card; `degraded` adds a "not been read lately" line), `needs_reconnect` (the card with "Connect Microsoft Outlook again" and the server's `state_text`). **No polling.** Read `GET /api/calendar` when the page loads and after a write; the sync runs on the server every 5 minutes and `last_sync_at` says when it last finished (`null` before the first one: "Waiting for the first update. This usually takes a minute.").

## 9. What the person will experience that may surprise them

- **No revoke.** Disconnecting here does not remove Beyric from their Microsoft account. Say so (section 5).
- **Most Outlook meetings are Teams meetings** and will read "Cannot join" for now. Do not hide them; the sentence is the server's.
- **Only one calendar.** Connecting Outlook while Google is connected is refused; say "disconnect first".
- **A different Microsoft account** than the connected one is refused until they disconnect (`different_account`).
- **Personal accounts only for now.** A work or school account is refused by Microsoft before it reaches us; if the server later allows work accounts, some organisations will block the app and Microsoft's own page says so (we cannot detect it).
- A Microsoft account with **no Outlook mailbox** cannot be connected (`no_mailbox`).

## 10. Verification table

| Behaviour | Status |
|---|---|
| `GET /api/calendar` shapes (not connected, connected, needs reconnect, switched off, Teams and Zoom rows), `microsoft_enabled` | **Verified against the real app** (captures `03`, `21`, `22`, `37`, `45`); the Outlook identity was created by the real connect flow with a fake Microsoft |
| `connect` request and response, the flow cookie, every error status and string in section 3 except 503 | **Verified against the real app**; the consent URL is the real client's, the parameters are as documented |
| Callback redirects: connected, unknown state, cancelled, no mailbox, tenant not allowed, scope missing, different account, already connected | **Verified against the real app** with a fake Microsoft; the *Microsoft side* (what a real account sends, including the real error answer of a mailbox-less account) is **not verified**: the M0 run (S2, S3, S6) |
| Disconnect (`revoked: false`, `reason`, repeat, rows after) | **Verified against the real app** |
| Per-meeting switch on a Teams event (409 `not_joinable`) | **Verified against the real app** |
| Switched-off answers | **Verified against the real app** (a second app with the switch off) |
| Callback for a signed-out visitor, `wrong_account`, the 503 `calendar_unavailable` | **Not captured**: covered by tests only (`tests/test_microsoft_connect.py`) |
| A real Microsoft consent screen (is a personal account told the app is "unverified"?), real token and ID-token contents (`oid` present? scope string format?), real `calendarView` events | **Not verified live**: waiting for the M0 run (`docs/spike/outlook-m0/`) |
| A work or school account (blocked consent, `tenant_domains`, Safe Links) | **Not verified live**: needs a work mailbox in another tenant |
| A revoked grant detected by the sync (the `needs_reconnect` capture is a hand-set row), a 429, a client-secret expiry, a Conditional Access block | **Not reproducible live** (or not yet): shapes are from documentation and tests |
| The Microsoft permissions page address for the disconnect note | **Unknown**: M0 S7 |
