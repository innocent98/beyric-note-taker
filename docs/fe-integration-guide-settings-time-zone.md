# Frontend integration guide: Settings > Time zone, and the Decisions and Review states

Everything below is copied from responses captured against the running app by
`scripts/capture_time_zone.py` (real app under uvicorn, real migrations, real Postgres, real HTTP;
files in `docs/fe-captures/time-zone/NN-name.json`, cited as **#NN**). Run the script again to
re-derive any claim. The person in the captures is a placeholder (`ada@example.test`). The Decisions
and Review calls the states use are the ones already captured in
`docs/fe-captures/ai-workspace-w1/` (see `docs/fe-integration-guide-ai-workspace.md`, sections 3 and
4); they did not change shape.

Read this with `docs/fe-integration-guide-owner-app.md` (session cookie, CSRF, error shape).

## 1. What it is, in the frontend's terms

A **Time zone** card on Settings (between "Tasks" and "Default notes language"): a select of zones,
the person's local time, and "Use my device". The zone is stored per person. It is used by:

| Where | What changes |
|---|---|
| Reminder and notes-ready **emails** | The meeting's start is written in that zone with its label: "Zoom 81234567891 · 06 Nov 2026, 00:30 GMT+1" (#10) |
| Every date and time on the **web pages** | Written in that zone, on the server render too (no swap after load). With no stored zone the pages keep using the browser's own zone |

A person who never chose a zone gets UTC in mail (as before) and the browser's zone on the pages.

## 2. Endpoints

All need a logged-in session; a write needs the `X-CSRF-Token` header (owner-app guide).

### `GET /api/settings/time-zone`

Logged out (#01): **401** `{"error": {"code": "not_authenticated", "message": "Log in first."}}`.

Nothing chosen yet (#05) -> **200**:

```json
{"time_zone": "UTC", "is_set": false}
```

After choosing Lagos (#08) -> **200**: `{"time_zone": "Africa/Lagos", "is_set": true}`.

After choosing UTC on purpose (#14) -> **200**: `{"time_zone": "UTC", "is_set": true}`.

### `PUT /api/settings/time-zone`

Body `{"time_zone": "Africa/Lagos"}` (#07) -> **200** `{"time_zone": "Africa/Lagos", "is_set": true}`.
Any IANA name the server knows is accepted (the designer's 18 zones, and whatever the device reports).

Errors:

| Case | Status | Body |
|---|---|---|
| Unknown name, `"Mars/Olympus"` (#15), a path `"../etc/passwd"` (#16), empty `""` (#17) | **422** | `{"error": {"code": "time_zone_unsupported", "message": "That time zone is not one we know. Pick one from the list."}}` |
| Field missing, `{}` (#18) | **422** | `{"error": {"code": "validation_error", "message": "Some fields are missing or not valid.", "fields": [{"field": "time_zone", "message": "Field required"}]}}` |
| No CSRF header (#19) | **403** | `{"error": {"code": "csrf_rejected", "message": "Missing or wrong X-CSRF-Token header. Read it from GET /api/session."}}` |

A refused write changes nothing (#20).

### `GET /api/session`: the zone for the pages

`user.time_zone` is **null until a zone is chosen** (#04) and the zone name after (#09):

```json
"user": {"email": "ada@example.test", "name": "Ada Example", "is_admin": false, "email_verified": true, "time_zone": "Africa/Lagos"}
```

**Nesting trap:** `time_zone` is `null` here when nothing is chosen, but `"UTC"` (with `is_set: false`) on
`GET /api/settings/time-zone`. Use the session's value to decide which zone to draw times in; use
`is_set` / the settings read to fill the select.

## 3. Mail, as captured

The notes-ready mail's first line (#06, #10, #12). The meeting started 23:30 UTC on 5 Nov 2026:

| Stored zone | First line of the mail |
|---|---|
| none | `Zoom 81234567890 · 05 Nov 2026, 23:30 UTC` |
| `Africa/Lagos` | `Zoom 81234567891 · 06 Nov 2026, 00:30 GMT+1` (already the next day) |
| `Asia/Kolkata` | `Zoom 81234567892 · 06 Nov 2026, 05:00 GMT+5:30` |

Labels are "UTC", "GMT", "GMT+1", "GMT-5", "GMT+5:30", the same style as the pages.

## 4. UX consequences to surface

- **Saving is immediate** (no Save button). Answer with the toast "Time zone updated", or "Time zone set from your device" after "Use my device". After a save, ask the server render again (`router.refresh()`) so every time on the page changes.
- **A 422 is developer text on the pages:** the card shows the general line "Something went wrong. Please try again." and puts the previous zone back in the select.
- **First visit shows "UTC"** in the select while the pages are still in the browser's zone (the person never chose). That is the honest stored state; the designer has not drawn a first-run default (see the SOP).
- **A zone change does not rewrite times already sent** in mail.

## 5. Decisions and Review states (no new API)

| State | Trigger | What the FE draws |
|---|---|---|
| Decisions loading | any list fetch in flight (search, Try again) | "Loading decisions..." in place of the list |
| Decisions failed | the first load, a search or Try again failed (any non-401 error) | red line "Could not load decisions. Check your connection and try again." + Try again |
| Decisions empty | `groups: []` and no search text | "No decisions yet." + "Decisions made in your meetings will appear here after each meeting ends." |
| Remove from log | `POST /api/decisions/{id}/hide` -> 204 (#21 of the AI-workspace captures) | inline ask, then toast "Decision removed from the log"; 404 counts as removed |
| Review loading / failed / empty | same as Decisions, for `GET /api/review?state=open` and `checked` | "Loading review items...", "Could not load review items. Check your connection and try again." + Try again, "Nothing to review yet." |
| Rewrite offer | `POST /api/review/{id}/vocabulary` -> 201 | "“Metrika” was added to your vocabulary. Rewrite the notes for … so they use the new spelling?" with Not now / Rewrite notes |
| Row error, term exists | the same call -> 409 `duplicate_term` (#38 there) | red line "“Metrika” is already in your vocabulary." on the row; the API has marked the item checked, so it is under Checked after a reload |
| Row error, item gone | check / uncheck / vocabulary -> 404 (#40 there) | "This item is no longer available. Its meeting was deleted." + Remove from list |

## 6. Verification table

| Behaviour | Verified |
|---|---|
| GET/PUT `/api/settings/time-zone` shapes, 401, 403, 422 (unknown, path, empty, missing) | live (#01, #05, #07, #08, #14 to #20) |
| `user.time_zone` null then set in `GET /api/session` | live (#04, #09) |
| Mail first line in UTC, Lagos, Kolkata | live (#06, #10, #12), mailer is a recorder (no provider called) |
| Pages write times in the stored zone | unit tests (`web/tests/settings-time-zone.test.tsx`) and the parity shots against the mock API; not run against a live FastAPI in a browser |
| Decisions / Review failed, empty, loading states | unit tests and the mock API (`web/scripts/mock-api.mjs --decisions failed|empty|retry-loading`); the 500 shape is the mock's, not captured |
| Reminder mails other than notes-ready | not verified: no other mail carries a time yet |
| A real device zone the server does not know | not verified (the 422 path is) |
