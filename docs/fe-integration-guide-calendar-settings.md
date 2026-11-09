# Frontend integration guide: calendar on Settings and Meetings (task C15a, slices 1 and 2)

Everything below is copied from responses captured against the running app by
`scripts/capture_calendar_state.py` (the read; real app, real migrations, real Postgres, real HTTP;
files in `docs/fe-captures/calendar-state/NN-name.json`, cited as **#NN**) and
`scripts/capture_calendar_settings.py` (the writes, section 3a; files in
`docs/fe-captures/calendar-settings/NN-name.json`, cited as **#wNN**). Run a script again to
re-derive any claim. Emails and ids in the captures are placeholders, and the dates are those of
the day the script ran (2026-11-04).

**What was NOT captured live: a real Google calendar.** Google's servers (consent screen, token
endpoint, Calendar API) are replaced by `tests/fakes/google.py`. The events in #09 went through the
**real sync job** (`gcal_sync.sync_connection` over `GoogleSource`) reading synthetic Google event
bodies, so the stored rows and the route's answer are the app's own; what a real Google returns for
a real calendar has not been seen (needs Adebayo's Gmail test account, see the last section). Two
things are not reachable through the app yet and were set on the stored row directly (each capture
says so): the `degraded` and `needs_reconnect` states (task C13). The auto-join rule is now set
through the real `PUT /api/calendar/settings` (#10 and #11 are its answers).

Read this with `docs/fe-integration-guide-calendar-connect.md` (the connect and disconnect routes and
the redirect query the Settings page receives) and `docs/fe-integration-guide-owner-app.md` (session
cookie, CSRF, error shape).

## 1. What this is, in the frontend's terms

Two screens, both from the designer's `Beyric Note Taker.dc.html`:

- **Settings** (`/settings`, the page Google's callback sends the browser back to): the Calendar
  section. Not connected: the Connect Google Calendar button. Connected: the account row with
  Disconnect, the "Join automatically" rules, the "Template for calendar meetings" select (both write
  through `PUT /api/calendar/settings`, section 3a).
- **Meetings** (`/`): while no calendar is connected, the "Join meetings automatically" band with a
  Connect calendar button (it goes to Settings); once one is, the Upcoming list with the account, the
  rule label and a row per meeting, each with a switch (`PUT /api/calendar/events/{id}/override`,
  section 3a).

**One read, `GET /api/calendar`, feeds both**, and the server owns the words: the state sentence, the
last failure, the rule label and each meeting's answer are plain English, ready to show. Do not turn
any field into text yourself and do not show a code. The rule's three radio descriptions on Settings
are static interface text keyed by `rule` (the designer's words).

## 2. `GET /api/calendar`

Signed in; no CSRF header (it is a read). Nothing is fetched from Google by the call: it reads what
the sync job last stored (the first sync runs within about a minute of connecting, then about every 5
minutes).

### Not connected (#05; #19 for another user; #21 after a disconnect)

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
  "upcoming": []
}
```

This is also the answer for a connection made only for Meet (no calendar permission): from the
calendar's point of view nothing is connected (covered by a test, not captured). `rule` is returned
even when nothing is connected (the person's stored rule, `all` until they choose one).

### Connected, before the first sync (#08)

```json
{
  "connected": true,
  "state": "connected",
  "state_text": "Connected",
  "account_email": "grace@example.test",
  "provider": "google",
  "last_sync_at": null,
  "last_error": null,
  "rule": "all",
  "rule_text": "Joining all meetings",
  "calendar_template_id": 1,
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
  "account_email": "grace@example.test",
  "provider": "google",
  "last_sync_at": "2026-11-04T14:57:42.885017Z",
  "last_error": null,
  "rule": "all",
  "rule_text": "Joining all meetings",
  "calendar_template_id": 1,
  "upcoming": [
    {
      "id": 1,
      "title": "Weekly planning",
      "starts_at": "2026-11-04T16:57:00Z",
      "ends_at": "2026-11-04T17:27:00Z",
      "platform": "google_meet",
      "platform_label": "Google Meet",
      "attendee_count": 2,
      "will_join": true,
      "will_join_text": "Will join",
      "reason_text": null
    },
    {
      "id": 3,
      "title": "Lunch with Sam",
      "starts_at": "2026-11-04T22:57:00Z",
      "ends_at": "2026-11-04T23:27:00Z",
      "platform": null,
      "platform_label": null,
      "attendee_count": 2,
      "will_join": false,
      "will_join_text": "Cannot join",
      "reason_text": "Add a meeting link to this event to record it"
    },
    {
      "id": 7,
      "title": "Partnership idea",
      "starts_at": "2026-11-06T21:57:00Z",
      "ends_at": "2026-11-06T22:27:00Z",
      "platform": "google_meet",
      "platform_label": "Google Meet",
      "attendee_count": 2,
      "will_join": false,
      "will_join_text": "Off",
      "reason_text": "Waiting for you to accept the invite"
    }
  ]
}
```

(Three of the seven items; #09 has all of them.) What the page draws per row, as in the design: day
and time (the viewer's own zone, see section 5), title, `platform_label` (or **"No meeting link"** when
it is `null`: the design's words), the duration, `N people` from `attendee_count` (left out when 0)
and `will_join_text` at the right. **`will_join_text` is always one of the designer's three words:**

| `will_join_text` | `reason_text` (the row's hover text; `null` when it will join) | Cases |
|---|---|---|
| `Will join` | `null` | normal |
| `Off` | e.g. `Waiting for you to accept the invite` (#09), `Skipped because you are not the organiser` (#10), `Auto-join is set to only when you turn it on` (#11) | the rule or the person's own switch says no, and they could change it |
| `Cannot join` | e.g. `Add a meeting link to this event to record it`, `Microsoft Teams meetings cannot be joined yet`, `You declined this invite` (#09) | nothing the person switches changes it |

Other reasons exist and are covered by tests, not captured: `You switched this meeting off` (Off),
`All-day events are not joined`, `Too long to join (over 6 hours)`, `This kind of event is not
joined`, `Already over`, `This event was cancelled` (Cannot join). A reason the server does not know
yet comes back as `Will not join` (Cannot join). Show the words verbatim; use `will_join` only to dim
a row if you want (the design dims "Cannot join" rows).

What the list is: events that have not ended and start within the next **7 days**, soonest first, at
most **100**. Cancelled events are left out. The eighth event in the capture (in 10 days) is not in
the list.

### The rule (#09, #10, #11)

`rule` is `all`, `organizer` or `none`; `rule_text` is the designer's label for the Meetings page
header ("Joining all meetings" / "Joining meetings you organise" / "Auto-join is off" (#10, #11)). The
meeting answers follow the rule (#11: every joinable meeting is `Off` with "Auto-join is set to only
when you turn it on"). It is changed with `PUT /api/calendar/settings` (section 3a).

### Fields

| Field | Meaning |
|---|---|
| `connected` | `true` while the calendar can still be read: states `connected` and `degraded`. **`false` for `needs_reconnect`** |
| `state` | `not_connected`, `connected`, `degraded`, `needs_reconnect`. Choose the layout from it |
| `state_text` | The sentence for the state; show it verbatim when the state is not `connected` |
| `account_email` | The account connected (before 2026-11-05 this field was called `google_email`). `null` when not connected. **Present for `needs_reconnect`**, so the person knows which account to fix |
| `provider` | Which calendar it is: `"google"` (the only value today; `"microsoft"` is reserved). `null` when not connected. Use it for the provider label; until Microsoft ships, print "Google" as the screen does now |
| `bot_invite_email` | The note taker's Google address when the server may tell people to add it to an event's guest list (so the bot skips the waiting room on Google Meet), else `null`. **Present as a string or `null`** (the generated type marks it optional). Draw the Settings tip only when `connected` is true, `provider` is `google` and this is a string. A public address, never write one in the web code. Real bodies and the rule: `docs/fe-integration-guide-bot-invite-tip.md` (#05 and #10 there) |
| `last_sync_at` | UTC, with microseconds. The last time the calendar was read completely; `null` before the first sync. It does not move while syncs fail |
| `last_error` | The last failure in plain words, or `null` after a success |
| `rule`, `rule_text` | The auto-join rule and its label (always present) |
| `calendar_template_id` | The person's own template chosen for calendar meetings, or `null` for the default. **Always present.** Sign-up gives every account its General copy, so for real accounts it is a number (`1` in #05, #09); `null` appears only after the person is sent back to the default (#w10) |
| `upcoming[].id` | The event's id: what the switch sends (`PUT /api/calendar/events/{id}/override`) |
| `upcoming[].starts_at`, `ends_at` | UTC, `Z` suffix, whole seconds |
| `upcoming[].platform` | `google_meet`, `zoom`, `teams` or `null`; `platform_label` is its name, `null` with it |
| `upcoming[].attendee_count` | How many people are invited: **a number, never addresses** |

What the response never has, by construction (a test pins the key set and searches the text): attendee
addresses, event descriptions, locations, the meeting link, a Zoom passcode, any token. #09 was made
from events that carried all of those in Google's body.

### `last_error` (#12, #13)

```json
"last_error": "Google no longer lets Beyric read your calendar. Connect again to fix this."
```

(#12: Google refused the refresh token, as when access is revoked.)

```json
"last_error": "The last check of your calendar did not work. Beyric will try again soon."
```

(#13: Google answered 503. The same sentence is given for anything that may pass on its own.)

### Field-nesting traps and states that do not move yet

- **`state` stays `connected` after a revoked grant** (#12: `connected: true`, `state: "connected"`,
  with the `last_error` above). Nothing turns a revoked grant into `needs_reconnect` yet (task C13). So
  Settings must show `last_error` for `connected` and `degraded`. Offer "Connect Google Calendar
  again" when `state` is `needs_reconnect`; today the person can also press Disconnect and connect.
- **`needs_reconnect` still carries `last_error` and `upcoming`** (#15, #16: `last_error` is the
  generic sentence and `upcoming` holds the events as last synced). Settings shows `state_text` and
  hides `last_error` there (two sentences about the same trouble contradict each other). The Meetings
  page treats `needs_reconnect` like not connected (the Connect calendar band, which leads to Settings
  where "connect again" is): the design has no banner for it (a designer gap).
- `degraded` (#14): `{"connected": true, "state": "degraded", "state_text": "Connected, but your calendar has not been read lately.", ...}`.
  **Not reached live**: nothing sets it yet; the row was set directly.
- `needs_reconnect`, reason revoked (#15): `"state_text": "Google access was removed. Connect again to carry on."`; reason expired (#16): `"Google access ran out. Connect again to carry on."`;
  the third reason (`error`) gives `"Google Calendar stopped working. Connect again to carry on."` (test only).
  **Not reached live**: the rows were set directly.

### Errors

| Status | `code` | Body | When |
|---|---|---|---|
| 401 | `not_authenticated` | `{"error": {"code": "not_authenticated", "message": "Log in first."}}` (#01) | no session |
| 404 | `calendar_unavailable` | `{"error": {"code": "calendar_unavailable", "message": "Google Calendar is not available."}}` (#04) | the server has `CALENDAR_ENABLED` off, or no Google client |

On the 404, Settings says "Google Calendar is not available yet." with no button, and the **Meetings
page draws nothing for the calendar** (no band, no list: it looks as it did before the calendar
existed). The Meetings page also draws nothing when the read fails for any other reason: the calendar
never breaks that page.

## 3. Connect and disconnect from Settings

Both are the C3 and C4 routes, unchanged: see `docs/fe-integration-guide-calendar-connect.md`.

- **Connect:** `POST /api/calendar/google/connect` with `{"features": ["calendar"], "return_to": "/settings"}`
  (CSRF header). Answer `{"authorize_url": "...", "show_unverified_notice": true}`. If
  `show_unverified_notice` is true, show the explainer first (the page does, with a "Continue to
  Google" button); then `window.location.assign(authorize_url)`: a navigation, not `fetch`. The
  default return path (no `return_to`) is now `/settings`. The Meetings band's "Connect calendar" is a
  plain link to `/settings`: the connect itself happens there.
- **Disconnect:** `POST /api/calendar/google/disconnect` (CSRF header, no body), answer
  `{"status": "disconnected", "revoked": true}` (#20). Then `GET /api/calendar` is the not-connected
  body (#21): nothing of the account or the events remains. If `revoked` is `false`, tell the person
  to remove Beyric at `myaccount.google.com/permissions` (the page does). The page asks first, in
  words: Beyric forgets the Google connection, deletes the calendar events it copied and cancels the
  meetings it planned to join; meetings already recorded and notes stay; a meeting being recorded now
  carries on.

### What the callback's redirect brings back

Google's callback answers with a 302 to `/settings?calendar=connected` or `?calendar_error=<code>`
(#07 is the success; the error codes are captured in `docs/fe-captures/calendar-connect/`). Settings
reads them on the server, shows the message once and removes the query from the address:

| Query | Message |
|---|---|
| `calendar=connected` | Google Calendar connected. |
| `google_denied` | You did not connect your calendar. (not styled as an error) |
| `scope_missing` | To join your meetings automatically we need permission to read your calendar events. Try again and leave the calendar box ticked. |
| `no_refresh_token` | Google did not finish connecting. Remove Beyric at myaccount.google.com/permissions and try again. |
| `google_failed` | Something went wrong connecting Google. Try again. |
| `different_account` | A different Google account is already connected ({email}). To use another one, disconnect first. (`{email}` is `account_email` of the same page's read) |
| `wrong_account` | This connection was started from a different account. Sign in with the right account and start again. |
| anything else | the `google_failed` message; the code from the address is never printed |

## 3a. Settings and the switch on each row (slice 2)

Both are writes: signed in, **CSRF header required**, `Content-Type: application/json`. Both answer 404
`calendar_unavailable` while the server has the calendar off (#w40, #w41), 401 without a session (#w38)
and 403 `csrf_rejected` without the header (#w39). Captured by `scripts/capture_calendar_settings.py`
(#wNN = `docs/fe-captures/calendar-settings/NN-*.json`), real app, the sync job reading a fake Google.

### `PUT /api/calendar/settings`: the rule and the template

Body: `{"rule": "all" | "organizer" | "none"}`, `{"calendar_template_id": <id> | null}`, or both. **A field
left out stays as it is.** At least one is required. The answer is the **whole state** (the same body as
`GET /api/calendar`), so redraw the Settings card and anything that reads `rule`, `rule_text`,
`calendar_template_id` or `upcoming` from it.

Request `{"calendar_template_id": 2}` (#w08, answer trimmed to the changed fields and the first rows):

```json
{
  "connected": true,
  "state": "connected",
  "state_text": "Connected",
  "account_email": "grace@example.test",
  "provider": "google",
  "rule": "all",
  "rule_text": "Joining all meetings",
  "calendar_template_id": 2,
  "upcoming": ["... seven rows, unchanged by a template change ..."]
}
```

Request `{"calendar_template_id": null}` answers the same with `"calendar_template_id": null` (#w10): the
default. The select offers only the person's own templates (`GET /api/templates`), so it never sends
`null` itself; when the answer has `null`, show the person's **first (oldest) template** as selected
(the General copy; the list is sorted by name, so it is not its first entry).

Request `{"rule": "none"}` (#w18; "Only when I turn it on"): every joinable row becomes `Off`:

```json
{
  "rule": "none",
  "rule_text": "Auto-join is off",
  "upcoming": [
    {"id": 1, "title": "Weekly planning", "will_join_text": "Off", "reason_text": "Auto-join is set to only when you turn it on"},
    {"id": 3, "title": "Lunch with Sam", "will_join_text": "Cannot join", "reason_text": "Add a meeting link to this event to record it"}
  ]
}
```

(Two of the seven rows, fields trimmed; the answer has every field of section 2.) Request `{"rule": "organizer"}`
(#w32): `Partnership idea` (an invite the person did not organise) is `Off` with "Skipped because you are not
the organiser"; the meetings they organise are `Will join`.

What a **rule change** does (verified live: the stored joins are in #w19 and #w33):

- every per-meeting switch on a meeting that **has not started** is cleared; a meeting already in
  progress keeps its switch;
- every stored event is decided again from the new rule, so a join the rule now forbids is cancelled and
  one it now allows is made or brought back. This includes events beyond the 7-day list;
- choosing the rule that is **already set** changes nothing, switches included (#w34);
- nothing is fetched from Google and no bot is sent.

A **template change** is also written onto the meetings already planned that have not sent a bot (#w09),
and does not touch switches.

The rule and template can be written with **no calendar connected** (#w44: the answer is the
not-connected state with the new rule); the screen only offers them when connected.

| Status | `code` | Body | When |
|---|---|---|---|
| 404 | `not_found` | `{"error": {"code": "not_found", "message": "No such template"}}` (#w14, #w15) | the id is not one of the caller's own templates, or does not exist: the same answer, and **nothing is stored, the rule in the same body included** |
| 422 | `validation_error` | `{"error": {"code": "validation_error", "message": "Some fields are missing or not valid.", "fields": [{"field": "body", "message": "Value error, send a rule, a calendar_template_id, or both"}]}}` (#w16) | an empty body |
| 422 | `validation_error` | `... "fields": [{"field": "rule", "message": "Input should be 'all', 'organizer' or 'none'"}]` (#w17) | an unknown rule (`null` for `rule`, other fields and strings for the id are 422 too: covered by tests, not captured) |

404 and 422 are developer text: show "Something went wrong. Please try again." and put the control back.

### `PUT /api/calendar/events/{id}/override`: the switch on a row

`{id}` is `upcoming[].id`. Body `{"override": true | false | null}` (the key is required; `null` clears the
switch so the rule decides again). **Send `!will_join` when the row is pressed** (the design's toggle).
The answer is that one row (the shape of an `upcoming[]` item), so replace the row with it.

Request `{"override": true}` on a meeting the rule had switched off (#w20):

```json
{
  "id": 1,
  "title": "Weekly planning",
  "starts_at": "2026-11-04T16:58:00Z",
  "ends_at": "2026-11-04T17:28:00Z",
  "platform": "google_meet",
  "platform_label": "Google Meet",
  "attendee_count": 2,
  "will_join": true,
  "will_join_text": "Will join",
  "reason_text": null
}
```

Request `{"override": false}` (#w22): `"will_join": false`, `"will_join_text": "Off"`, `"reason_text": "You switched this meeting off"`.
Request `{"override": null}` (#w23, under the rule `none`): `"will_join_text": "Off"`, `"reason_text": "Auto-join is set to only when you turn it on"`.

**The one-click way into an invite the person has not accepted** (#w25 before, #w26 after): the row says `Off`
with "Waiting for you to accept the invite"; `{"override": true}` answers `Will join`. The unanswered-invite rule
itself is unchanged: such an invite joins on its own only when the person organised it or the organiser
shares their email domain.

| Status | `code` | Body | When |
|---|---|---|---|
| 409 | `not_joinable` | `{"error": {"code": "not_joinable", "message": "You declined this invite"}}` (#w28) | nothing the person switches can change this meeting. Other messages captured: "Microsoft Teams meetings cannot be joined yet" (#w29), "Add a meeting link to this event to record it" (#w30); covered by tests: "Already over", "This event was cancelled", "All-day events are not joined", "Too long to join (over 6 hours)". Nothing is stored. The same for `true`, `false` and `null` |
| 404 | `not_found` | `{"error": {"code": "not_found", "message": "No such event"}}` (#w35, #w36, #w43) | another person's event, an id that does not exist, or any id after a disconnect (the events are deleted) |
| 422 | `validation_error` | `... "fields": [{"field": "override", "message": "Input should be a valid boolean"}]` (#w37) | `"yes"`, a number, a missing key |

What the page does with them: **409** shows the server's `message` as it is (it is the reason, in
words), puts the switch back and **reads `GET /api/calendar` again** (the meeting changed behind the page:
declined, over), so the row then says `Cannot join` with that reason. **Network failure** says "Could not reach the
server. Check your connection and try again." and puts the switch back. 404 and 422 say "Something went wrong.
Please try again."

Rows with `will_join_text` `Cannot join` are drawn dimmed with the switch off and **do nothing when pressed**
(the 409 above is only the safety net for a row that went stale). Those with `Off` and `Will join` are pressable.

**Field-nesting traps:** the settings answer is the whole state (`upcoming` is an array of rows, under
the top-level object), the override answer is **one row, not wrapped** (no `upcoming`). `calendar_template_id` is
at the top level of the state, never on a row. A row's `id` is the event's id, not a scheduled-join id.

**Not verified live:** a real Google calendar (everything is a fake Google; the events went through the
real sync job); what the runner will do with the changed join rows (task C12 is not built: #w06, #w09, #w19,
#w21, #w27, #w33 are the stored `scheduled_joins` as the routes left them). A meeting already past its start by
more than 15 minutes shows `Will join` after a switch on, but no join is made for it (spec 7.7, "In progress").

## 4. Polling

None needed. The pages read once per load (the Meetings page also re-reads whenever its meeting list
refreshes). A person who just connected sees "Waiting for the first update" until a reload after about
a minute; an automatic refresh is a later improvement.

## 5. UX consequences to surface

- **Days and times are the viewer's own** ("Today", "Tomorrow", "Wed 4 Nov", "14:00"), written in the
  browser after the page loads (the server does not know the zone), so the day and time cells are empty
  for a moment in the server-rendered HTML. The API gives UTC; the Settings "Last updated" line is in
  UTC and says so.
- **"Will join" is a promise about the rules, and since C12 the runner keeps it** (2026-11-04): the
  scheduler sends the bot at the start minus 60 s, unless the host is full (then it tries every
  15 s, tells the owner, and ends as "not recorded" at start + 15 minutes), the account is suspended
  or the calendar was disconnected. The routes do not show those join states yet (C15a); see
  `docs/fe-integration-guide-calendar-join.md` for the notifications the runner writes and the
  meeting titles. Switching a meeting or the rule off is honoured up to the moment the bot is sent.
- The recording reminder ("Tell participants that this meeting will be recorded.") is the New meeting
  card's own line, as designed; the Upcoming list does not repeat it.
- **The switch on each row writes the person's own choice for that meeting** and beats the rule in
  both directions; changing the rule **clears every switch on meetings that have not started** (section
  3a). Say so near the radios if the person has switched meetings by hand: the switches they set are
  gone after a rule change (the designer's prototype does the same, silently).
- **An invite the person has not accepted waits** (`Off`, "Waiting for you to accept the invite"):
  the switch is the one-click way to join it. Do not hide the switch on `Off` rows.
- **Choosing a template or rule has an immediate effect on meetings already planned** (their joins are
  changed or cancelled), not only on future ones.
- A Teams meeting says `Cannot join` with "Microsoft Teams meetings cannot be joined yet". The
  designer's Calendar line names Teams (`The note taker reads your calendar and joins meetings that
  have a Google Meet, Microsoft Teams or Zoom link.`); the page uses the designer's words, so until
  Teams works that sentence and the row disagree (a designer decision, listed in the SOP).

## 6. Verification table

| Behaviour | Status |
|---|---|
| 401 without a session (#01) | verified live against the running app |
| 404 `calendar_unavailable` with the feature off (#04) | verified live |
| Not connected, connected before first sync, connected with seven meetings, plain-words errors (#05, #08, #09, #12, #13) | verified live, Google faked; the events went through the real sync job |
| The 7-day window, ordering, cancelled left out, cap of 100, no attendee/description/passcode/link in the answer, the guest count as a number, emoji and non-Latin titles, one user never sees another's rows, the three labels | verified by the pytest suite against a real Postgres (40 tests, mutation-checked); #09 and #19 show it over HTTP |
| Another account's read (#19), after disconnect (#21), disconnect answer (#20) | verified live |
| `rule` and `rule_text` for `organizer` and `none` (#10, #11) | verified live: set through the real `PUT /api/calendar/settings` |
| `degraded` (#14), `needs_reconnect` revoked and expired (#15, #16) | **not reachable live yet (C13)**: the stored row was set directly, the route's answer is real |
| `needs_reconnect` with reason `error`, the reasons not in #09 to #11 | covered by tests only |
| Meet-only connection reads as not connected | covered by a test only |
| The pages themselves (`/settings`, the Meetings band and Upcoming list) | verified in a built app (`next build`, standalone server) next to the designer's file at 1440, 768 and 390 px against a mock API replaying these captures (slice 2: also a real keypress on a rule in the built app against that mock); slice 1 was also run in a browser pane against a real local FastAPI. **Not** the slice 2 controls against a real local FastAPI, **not** Safari or Firefox, not the live site |
| `PUT /api/calendar/settings` (rule, template, both, empty, bad rule, foreign and unknown template, no calendar connected), `PUT .../override` (on, off, clear, an unaccepted invite, 409 declined, Teams and no link, foreign, unknown and after-disconnect 404, bad body, 401, 403, feature off) (#w01 to #w44) | verified live against the running app, Google faked; the stored `scheduled_joins` after each write are in the captures |
| Rule change clears switches on meetings that have not started and keeps those in progress; choosing the same rule changes nothing; template reaches planned joins but not `joined` ones; another user's rows untouched; every 409 reason (declined, over, cancelled, all-day, too long, no link, Teams) | verified by the pytest suite against a real Postgres (63 tests, mutation-checked); the captures show declined, Teams, no link, the rule change and the same-rule call over HTTP |
| What the runner (C12) does with the changed rows | **not verified**: C12 is not built |
| A real Google calendar, a real consent screen (including the wording of Google's own unverified-app warning, which the page describes but has not seen), real `hd` and scope names | **not verified**: needs Adebayo's Gmail test account (checklist C3 and C5 live steps) |

## 7. `GET /api/calendar/recurring` (the Recurring cards on Meetings)

Captured by `scripts/capture_calendar_recurring.py` into `docs/fe-captures/calendar-recurring/NN-name.json`
(cited as **R#NN**; the same setup as above: real app, real Postgres, real HTTP, events through the
real sync job over a fake Google, so **no real Google recurring event has been read**). The times in
the files are those of the run day (2026-11-04). Signed in; no CSRF header (a read).

**What it is.** One card per recurring meeting on the connected calendar: the name, a cadence, the
next occurrence. It groups the stored events by Google's own recurring-event id. It reads what the
sync stored (events from an hour back to two weeks ahead, kept two days after they end) and fetches
nothing from Google.

**Query:** `?tz=<IANA zone>`, the browser's own (`Intl.DateTimeFormat().resolvedOptions().timeZone`).
The server writes the cadence and the next date in that zone; without it the texts are in UTC and
`timezone` says so (R#09). The weekday and clock time depend on the zone, so always send it.

Africa/Lagos (R#10, trimmed to two of the five series):

```json
{
  "timezone": "Africa/Lagos",
  "series": [
    {"name": "Beyric Friday review", "cadence": "Weekly on Fridays, 15:00", "next_starts_at": "2026-11-06T14:00:00Z", "next_text": "Fri 6 Nov, 15:00"},
    {"name": "Beyric Monday planning", "cadence": "Weekly on Mondays, 14:00", "next_starts_at": "2026-11-09T13:00:00Z", "next_text": "Mon 9 Nov, 14:00"}
  ]
}
```

The same rows in Pacific/Auckland (R#11) read `"cadence": "Weekly on Saturdays, 03:00"` and
`"next_text": "Sat 7 Nov, 03:00"` for the Friday review, with the same `next_starts_at`. Draw the texts as
they are.

**Cadence values** (R#09 to R#11): `Daily, 09:30`, `Weekly on Mondays, 14:00` (two days: `Mondays and
Wednesdays`), or just `Recurring` (R#10: a series with one occurrence in the data, and one whose time
of day changes). The server says a cadence only when the stored times prove it; never derive one in
the browser.

**Nothing to draw:** not connected, connected before the first sync, after a disconnect and for a
second account without a calendar all answer 200 with `{"timezone": "UTC", "series": []}` (R#05, R#08,
R#16, R#18). Draw nothing then (no heading either).

**Errors:**

| Status | Body | Meaning |
|---|---|---|
| 401 | `{"error": {"code": "not_authenticated", ...}}` (R#01) | no session |
| 404 | `{"error": {"code": "calendar_unavailable", "message": "Google Calendar is not available."}}` (R#04) | the feature is off: draw nothing |
| 422 | `{"error": {"code": "invalid_timezone", "message": "That time zone is not known. Use a name like Africa/Lagos."}}` (R#12) | unknown zone |
| 422 | `{"error": {"code": "validation_error", "message": "Some fields are missing or not valid.", "fields": [{"field": "tz", "message": "String should have at most 64 characters"}]}}` (R#13) | zone name longer than 64 characters |

**Field traps and what is deliberately absent**

- Only four fields per card: `name`, `cadence`, `next_starts_at` (UTC, `Z`), `next_text`. No id, no
  link, no attendees; the card has no key, use the index.
- **No "unfinished tasks" and no "open questions" chip.** The designer drew both; tasks do not exist
  yet (W2), and a past meeting's link to its series is lost when the event row is deleted two days
  after it ends, so a count would shrink silently. The page draws the "Next:" chip only.
- **The card is not a link** (the Series page is W8): no chevron, no hover state, nothing to press.
- Only meetings: cancelled events, all-day events and out-of-office or focus-time events are left out,
  and so is a series whose title is blank. A series appears only while it has an occurrence that has
  not started yet (a meeting in progress is not "next"). Soonest next occurrence first, at most 50.
- Because the data window is two weeks, a series that meets less often than every two weeks may not
  appear, or appears as `Recurring`.

**Not captured live:** a real Google recurring event (the series ids and instances are synthetic), and
a clock change inside the window (covered by a pytest case, not by a capture).

| Behaviour | Status |
|---|---|
| 401, 404 feature off, empty list (never connected, before first sync, after disconnect, other user), series in UTC, Lagos and Auckland, 422 unknown zone, 422 too long (R#01, R#04 to R#18) | verified live against the running app (Google faked, real sync job) |
| Grouping, cadence rules (daily, weekly one or several days, every unprovable case), zone and clock-change boundaries, cancelled and in-progress rules, isolation between users, no address, link, passcode or source id, emoji titles | verified by `tests/test_calendar_recurring.py` (55 tests, real Postgres) |
| The cards on the page | verified in a built app next to the designer's file at 1440, 768 and 390 px against a mock replaying these captures; **not** the live site, not Safari or Firefox, not a real Google calendar |
