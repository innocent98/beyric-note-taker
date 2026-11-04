# Frontend integration guide: the calendar settings page (task C15a, first slice)

Everything below is copied from responses captured against the running app by
`scripts/capture_calendar_state.py` (real app, real migrations, real Postgres, real HTTP). The files
are in `docs/fe-captures/calendar-state/NN-name.json`; `NN` is cited as **#NN**. Run the script again
to re-derive any claim. Emails and ids in the captures are placeholders, and the dates are those of
the day the script ran.

**What was NOT captured live: a real Google calendar.** Google's servers (consent screen, token
endpoint, Calendar API) are replaced by `tests/fakes/google.py`. The events in #09 went through the
**real sync job** (`gcal_sync.sync_connection` over `GoogleSource`) reading synthetic Google event
bodies, so the stored rows and the route's answer are the app's own; what a real Google returns for
a real calendar has not been seen (needs Adebayo's Gmail test account, see the last section). Two
states (`degraded`, `needs_reconnect`) are not reachable yet (task C13); their captures set the stored
row directly and say so.

Read this with `docs/fe-integration-guide-calendar-connect.md` (the connect and disconnect routes and
the redirect query the page receives) and `docs/fe-integration-guide-owner-app.md` (session cookie,
CSRF, error shape).

## 1. What this is, in the frontend's terms

`/settings/calendar` is the page Google's callback sends the browser back to. It shows one of three
things: a Connect button (nothing connected), the connected account with its upcoming meetings, or a
"connect again" prompt (Google took the access away). **One read, `GET /api/calendar`, answers all
three**, and the server owns the words: the state sentence, the last failure and each meeting's "will
it join" answer are plain English, ready to show. Do not turn any field into text yourself and do not
show a code.

The settings, the per-meeting switch and "Send now" are later tasks (C15a part 2, C15b); they will
extend this same response, so ignore fields you do not know.

## 2. `GET /api/calendar`

Signed in; no CSRF header (it is a read). Nothing is fetched from Google by the call: it reads what
the sync job last stored (the first sync runs within about a minute of connecting, then about every 5 minutes).

### Not connected (#05, #17 for another user, #19 after a disconnect)

```json
{
  "connected": false,
  "state": "not_connected",
  "state_text": "Google Calendar is not connected.",
  "google_email": null,
  "last_sync_at": null,
  "last_error": null,
  "upcoming": []
}
```

This is also the answer for a connection made only for Meet (no calendar permission): from the
calendar's point of view nothing is connected (covered by a test, not captured).

### Connected, before the first sync (#08)

```json
{
  "connected": true,
  "state": "connected",
  "state_text": "Connected",
  "google_email": "grace@example.test",
  "last_sync_at": null,
  "last_error": null,
  "upcoming": []
}
```

`last_sync_at: null` means "waiting for the first update" (about a minute): show that, not an empty
calendar.

### Connected with meetings (#09)

```json
{
  "connected": true,
  "state": "connected",
  "state_text": "Connected",
  "google_email": "grace@example.test",
  "last_sync_at": "2026-11-04T12:49:53.819611Z",
  "last_error": null,
  "upcoming": [
    {
      "id": 1,
      "title": "Weekly planning",
      "starts_at": "2026-11-04T14:49:00Z",
      "ends_at": "2026-11-04T15:19:00Z",
      "platform": "google_meet",
      "platform_label": "Google Meet",
      "will_join": true,
      "will_join_text": "Will join"
    },
    {
      "id": 3,
      "title": "Lunch with Sam",
      "starts_at": "2026-11-04T20:49:00Z",
      "ends_at": "2026-11-04T21:19:00Z",
      "platform": null,
      "platform_label": null,
      "will_join": false,
      "will_join_text": "No video link"
    }
  ]
}
```

(Two of the seven items; #09 has all of them.) The seven in #09 show every answer the page can get:

| `will_join_text` | `platform` | Why |
|---|---|---|
| `Will join` | `google_meet`, `zoom` | normal |
| `No video link` | `null` | no recognised link |
| `Microsoft Teams meetings cannot be joined yet` | `teams` | Teams is not supported yet |
| `You declined this invite` | `google_meet` | the user declined |
| `Waiting: accept the invite to join` | `google_meet` | an unanswered invite from outside the user's organisation never joins on its own |

Other sentences exist and are covered by tests but not in the captures: `Skipped: you are not the
organiser`, `Off: joining only meetings you switch on`, `Off: you switched this one off`, `All-day
events are not joined`, `Too long to join (over 6 hours)`, `This kind of event is not joined`,
`Already over`, `This event was cancelled`. A sentence the server does not know yet comes back as
`Will not join`. Show `will_join_text` verbatim; use `will_join` only for the colour.

What the list is: events that have not ended and start within the next **7 days**, soonest first, at
most **100**. Cancelled events are left out. The eighth event in the capture (in 10 days) is not in
the list.

### Fields

| Field | Meaning |
|---|---|
| `connected` | `true` while the calendar can still be read: states `connected` and `degraded`. **`false` for `needs_reconnect`** |
| `state` | `not_connected`, `connected`, `degraded`, `needs_reconnect`. Choose the layout from it |
| `state_text` | The sentence for the state; show it verbatim when the state is not `connected` |
| `google_email` | The Google account connected. `null` when not connected. **Present for `needs_reconnect`**, so the person knows which account to fix |
| `last_sync_at` | UTC, with microseconds. The last time the calendar was read completely; `null` before the first sync. It does not move while syncs fail |
| `last_error` | The last failure in plain words, or `null` after a success |
| `upcoming[].id` | The event's id (for the switch in a later slice) |
| `upcoming[].starts_at`, `ends_at` | UTC, `Z` suffix, whole seconds |
| `upcoming[].platform` | `google_meet`, `zoom`, `teams` or `null`; `platform_label` is its name, `null with it` |

What the response never has, by construction (a test pins the key set and searches the text): attendee
addresses, event descriptions, locations, the meeting link, a Zoom passcode, any token. The capture
#09 was made from events that carried all of those in Google's body.

### `last_error` (#10, #11)

```json
"last_error": "Google no longer lets Beyric read your calendar. Connect again to fix this."
```

(#10: Google refused the refresh token, as when access is revoked.)

```json
"last_error": "The last check of your calendar did not work. Beyric will try again soon."
```

(#11: Google answered 503. The same sentence is given for anything that may pass on its own.)

### Field-nesting traps and states that do not move yet

- **`state` stays `connected` after a revoked grant** (#10: `connected: true`, `state: "connected"`,
  with the `last_error` above). Nothing turns a revoked grant into `needs_reconnect` yet (task C13). So
  the page must show `last_error` for `connected` and `degraded`, and the "connect again" sentence
  of the revoked case is the one in `last_error`, not in `state_text`. Offer "Connect Google Calendar
  again" when `state` is `needs_reconnect`; today the user can also press Disconnect and connect.
- **`needs_reconnect` still carries `last_error` and `upcoming`** (#13, #14: `last_error` is the
  generic sentence and `upcoming` holds the events as last synced). The page shows `state_text` and
  hides `last_error` there (two sentences about the same trouble contradict each other), and keeps the
  list (those meetings are still scheduled; they may be stale).
- `degraded` (#12): `{"connected": true, "state": "degraded", "state_text": "Connected, but your calendar has not been read lately.", ...}`.
  **Not reached live**: nothing sets it yet; the row was set directly.
- `needs_reconnect`, reason revoked (#13): `"state_text": "Google access was removed. Connect again to carry on."`; reason expired (#14): `"Google access ran out. Connect again to carry on."`;
  the third reason (`error`) gives `"Google Calendar stopped working. Connect again to carry on."` (test only).
  **Not reached live**: the rows were set directly.

### Errors

| Status | `code` | Body | When |
|---|---|---|---|
| 401 | `not_authenticated` | `{"error": {"code": "not_authenticated", "message": "Log in first."}}` (#01) | no session |
| 404 | `calendar_unavailable` | `{"error": {"code": "calendar_unavailable", "message": "Google Calendar is not available."}}` (#04) | the server has `CALENDAR_ENABLED` off, or no Google client |

On the 404 show "Google Calendar is not available yet." and no button (the page does; the sidebar
Settings link stays).

## 3. Connect and disconnect from this page

Both are the C3 and C4 routes, unchanged: see `docs/fe-integration-guide-calendar-connect.md`.

- **Connect:** `POST /api/calendar/google/connect` with `{"features": ["calendar"], "return_to": "/settings/calendar"}`
  (CSRF header). Answer `{"authorize_url": "...", "show_unverified_notice": true}`. If
  `show_unverified_notice` is true, show the explainer first (the page does, with a "Continue to
  Google" button); then `window.location.assign(authorize_url)`: a navigation, not `fetch`. Press once:
  the page keeps the button disabled while the call is in flight.
- **Disconnect:** `POST /api/calendar/google/disconnect` (CSRF header, no body), answer
  `{"status": "disconnected", "revoked": true}` (#18). Then `GET /api/calendar` is the not-connected
  body (#19): nothing of the account or the events remains. If `revoked` is `false`, tell the person
  to remove Beyric at `myaccount.google.com/permissions` (the page does). The page asks first, in
  words: Beyric forgets the Google connection, deletes the calendar events it copied and cancels the
  meetings it planned to join; meetings already recorded and notes stay; a meeting being recorded now
  carries on.

### What the callback's redirect brings back

Google's callback answers with a 302 to `/settings/calendar?calendar=connected` or
`?calendar_error=<code>` (#07 is the success; the error codes are captured in
`docs/fe-captures/calendar-connect/`). The page reads them on the server, shows the message once and
removes the query from the address. Words used (the connect guide's, section 2):

| Query | Message |
|---|---|
| `calendar=connected` | Google Calendar connected. |
| `google_denied` | You did not connect your calendar. (not styled as an error) |
| `scope_missing` | To join your meetings automatically we need permission to read your calendar events. Try again and leave the calendar box ticked. |
| `no_refresh_token` | Google did not finish connecting. Remove Beyric at myaccount.google.com/permissions and try again. |
| `google_failed` | Something went wrong connecting Google. Try again. |
| `different_account` | A different Google account is already connected ({email}). To use another one, disconnect first. (`{email}` is `google_email` of the same page's read) |
| `wrong_account` | This connection was started from a different account. Sign in with the right account and start again. |
| anything else | the `google_failed` message; the code from the address is never printed |

## 4. Polling

None needed. The page reads once per load. A person who just connected sees "Waiting for the first
update" until a reload after about a minute; an automatic refresh is a later improvement.

## 5. UX consequences to surface

- **Times are UTC**, because the page is rendered on the server and in the browser and both must
  agree. Every time on the page says `UTC`. A person in Lagos is one hour ahead; showing local time
  needs a decision (a follow-up for the designer).
- **"Will join" is a promise about the rules, not about the bot.** Nothing sends a bot from the
  calendar yet (the runner, task C12, and `place_bot()`, task C11, are not built). The page shows the
  answer as the rules give it. It is dark until the server turns `CALENDAR_ENABLED` on: do not switch it
  on for other users before C11 and C12, or "Will join" will promise something that does not happen.
- Show the reminder "Tell participants that this meeting will be recorded." when at least one listed
  meeting will join (the designed line).
- A Teams meeting says it cannot be joined yet. The designer's calendar sentence names Teams; the
  page leaves it out until Teams works.

## 6. Verification table

| Behaviour | Status |
|---|---|
| 401 without a session (#01) | verified live against the running app |
| 404 `calendar_unavailable` with the feature off (#04) | verified live |
| Not connected, connected before first sync, connected with seven meetings, plain-words errors (#05, #08, #09, #10, #11) | verified live, Google faked; the events went through the real sync job |
| The 7-day window, ordering, cancelled left out, cap of 100, no attendee/description/passcode/link in the answer, emoji and non-Latin titles, one user never sees another's rows | verified by the pytest suite against a real Postgres (35 tests, mutation-checked); #09 and #17 show it over HTTP |
| Another account's read (#17), after disconnect (#19), disconnect answer (#18) | verified live |
| `degraded` (#12), `needs_reconnect` revoked and expired (#13, #14) | **not reachable live yet (C13)**: the stored row was set directly, the route's answer is real |
| `needs_reconnect` with reason `error`, the sentences not in #09 | covered by tests only |
| Meet-only connection reads as not connected | covered by a test only |
| The page itself (`/settings/calendar`) | verified in a browser pane against a real local FastAPI and Postgres (connected, disconnect with the ask, not-connected, the unverified-app notice, an error redirect, phone width); **not** the built production image, not Safari or Firefox, not the live site |
| A real Google calendar, a real consent screen (including the wording of Google's own unverified-app warning, which the page describes but has not seen), real `hd` and scope names | **not verified**: needs Adebayo's Gmail test account (checklist C3 and C5 live steps) |
