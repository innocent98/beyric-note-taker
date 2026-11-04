# SOP: Disconnect Google (Phase C, task C4)

## What shipped
The user's own **Disconnect Google**: `POST /api/calendar/google/disconnect` (login and CSRF) and the service `calendar_connect.disconnect(session, user, google)`. It deletes the user's calendar data and empties the connection row, then revokes the refresh token at Google. Also the two C3 follow-ups: replacing a dead connection with a different Google account now **revokes the old token** (it used to be overwritten unrevoked), and the `meet_subscription_*` reset is shared by both paths. Branch `feature/c4-calendar-disconnect`, one PR (number in the PR list). **Security-critical (tokens, deletion): wants the Opus review before merge.**

**After this merges, `CALENDAR_ENABLED` may be switched on** (with `TOKEN_ENC_KEY` set and `https://notes.beyrictech.com/api/calendar/google/callback` authorised at Google): the privacy policy's promise to delete calendar data on disconnect is now kept by the server. Two caveats: the Settings → Calendar screen with the button does not exist yet (stream D / C15), and a real Google revoke has not been seen (below).

## Why
Spec 7.9 and 12.2: a disconnect must take the access away at Google and delete what we hold, and the privacy policy says so. C3 deliberately left it out and kept the feature dark until it existed.

## How
| Piece | Decision |
|---|---|
| What is deleted (spec 12.2) | The user's `calendar_events`; **every** `scheduled_joins` row in every state (including `dispatching`, `no_capacity`, `joined`: with no row left, the runner has nothing to send, and a bot already in a meeting is a `meetings` row, which is untouched); notifications whose `kind` starts with `calendar.` (`calendar_connect.CALENDAR_NOTIFICATION_PREFIX`). Every statement is scoped to the signed-in user |
| What stays | `meetings` (title, url with a Zoom passcode, a running meeting carries on, nobody tells the bot to leave), `usage_entries`, `user_settings`, notifications of other kinds (`usage.*`, `admin.*`) |
| The connection row | **Kept, status `disconnected`** (spec 7.9 "sets `disconnected`"), emptied: token and key version, granted scopes, Google email, sub and domain, sync token, last sync, error fields, expiry hint, `meet_subscription_*` reset to none. The row holds no Google identity afterwards. Connect again updates the same row (C3's reconnect path), for the same or **another** Google account (that is how a switch is done: disconnect, then connect). Rejected: deleting the row (the spec says `disconnected`, and C13/C15a read the status) |
| Revoke | `GoogleOAuth.revoke(token)`: POST to the **fixed** `https://oauth2.googleapis.com/revoke`, token in the form body only. 200 is done; **400 `invalid_token` counts as done** (already revoked at myaccount.google.com or expired); anything else raises `GoogleError` after a log line holding only the HTTP status or the exception type. **Best effort:** a revoke failure, an undecryptable token (lost key), or no Google client configured still deletes locally and logs `google token for user N was not revoked` (no token, no detail). The revoke is **not** gated by `CALENDAR_ENABLED`: the person can always disconnect |
| Order and atomicity (Opus review fix) | Lock the row (`FOR UPDATE`), copy the token into memory (`ReleasedConnection`, the token as a `Secret`), delete, empty the row, **COMMIT**; only then revoke and run the hooks, with no transaction or lock open. The first version revoked inside the transaction and held the row lock and a pooled database connection for a slow Google's 15 s timeouts. **Accepted trade-off:** a crash in the few milliseconds between the commit and the revoke leaves a live token at Google with no copy here; the person's remedy is myaccount.google.com/permissions (the `revoked` field below tells them when the revoke did not happen). The replace-a-dead-connection path does the same: the old token is revoked after the new connection is committed |
| `revoked` in the response (Opus review fix) | `{"status": "disconnected", "revoked": bool}`: `false` when a token existed and Google could not be asked to revoke it (unreachable, refused, unreadable key, no client configured), so the front end tells the person to remove Beyric at myaccount.google.com/permissions; `true` when Google confirmed or nothing was left to revoke. A repeat answers `true` (it cannot re-check an earlier failure): the first answer is the one to act on |
| Idempotent | A second submit (double click, two tabs) finds no token: no second revoke, same 200. With no connection at all it deletes nothing, creates nothing, and answers 200 `{"status": "disconnected"}` |
| `ON_DISCONNECT` | Module-level list in `app/calendar_connect.py`; each hook is `hook(session, released, google)` and runs **after the commit** (so a failing hook cannot undo the deletion). `released` is a `ReleasedConnection` snapshot (`id`, `user_id`, `meet_subscription_name`, `token` as a `Secret` or None, `had_token`), because the row no longer holds them: I3 deletes the Events subscription with it. A hook that raises (including a database error) is rolled back and logged by exception type only, and the next hook still gets a usable session. Hooks also run when a dead connection is replaced by another account. `register_token_cache(cache)` adds a hook that calls `AccessTokenCache.forget` |
| Replacing a dead connection (C3 follow-up) | `_drop_old_account` now calls the same revoke-and-hooks helper before the old token is overwritten, then deletes the old account's events and not-yet-live joins as before. Reconnecting the **same** account revokes nothing |
| Response | `200 {"status": "disconnected"}` always. A Google outage is not an error for the user (the person can revoke at myaccount.google.com/permissions) |

## What's involved
| Path | Change |
|---|---|
| `app/calendar_connect.py` | `disconnect`, `ON_DISCONNECT`, `ReleasedConnection`, `register_token_cache`, `CALENDAR_NOTIFICATION_PREFIX`, `_release`, `_release_at_google`, `_forget_meet_subscription`; `_store` collects the replaced old connection and `finish_connect` revokes it after the commit |
| `app/google_oauth.py` | `GoogleOAuth.revoke`, `REVOKE_URL` |
| `app/calendar_api.py` | the route and `DisconnectOut` (`status`, `revoked`) |
| `docs/openapi.json`, `web/lib/api/schema.d.ts` | regenerated with `scripts/export_openapi.py` and `npm run gen:api` |
| `tests/test_calendar_connect.py`, `tests/fakes/google.py` | 29 new tests; the fake's revoke endpoint now records requests, answers 400 `invalid_token` for a token already revoked and can be scripted to fail |
| `scripts/capture_calendar_connect.py`, `docs/fe-captures/calendar-connect/` (36 onwards), `docs/fe-integration-guide-calendar-connect.md` | live captures and the guide section |
| `docs/checklist/master-checklist.md` | C4 |

No migration, no new environment variable, no nginx change. The route is a normal login-and-CSRF route, so the route-enumeration tests (`tests/test_api.py`, `tests/test_edge.py`) cover it without a listing.

## Verification
| Claim | How | Result |
|---|---|---|
| Revoke at Google, delete exactly the 12.2 list, keep meetings, usage, settings | `tests/test_calendar_connect.py`: a join in every one of the 8 states, spec list, kept list, running meeting untouched | pass |
| Revoke failure (503, 500, 403, connection error, timeout), undecryptable token, no Google client, hook failure: still deleted locally, warning logged, no token in logs, `revoked: false` | same file | pass |
| No lock or open transaction around Google (review fix) | the fake revoke endpoint looks at the database from a second connection while it is being called: the wipe is committed and `FOR UPDATE NOWAIT` succeeds (disconnect and account replacement) | pass |
| A hook that issues bad SQL cannot undo the deletion, and the next hook still works (review fix) | same file | pass |
| The access-token cache is dropped on disconnect (review fix) | a real `AccessTokenCache`, minted once, forgotten by the hook, minted again | pass |
| Own rows only | a second connected user's connection, events, joins, notifications and token are untouched and not revoked | pass |
| Idempotent, double submit, no connection, connect again (same and other account), the replace-dead-connection revoke | same file | pass |
| Login and CSRF required; works with the flag off | same file | pass |
| The tests guard | mutation checks: dropping the user filter on joins, events or notifications, skipping the revoke, skipping the revoke on account replacement, not clearing the token, committing after instead of before the revoke, dropping the hook's rollback, or dropping the cache hook each fails a named test | done |
| Whole suite, ruff, OpenAPI | see the PR | see the PR |
| Real HTTP and real database | `scripts/capture_calendar_connect.py` captures 36 onwards | recorded |
| **Not verified** | **A real Google revoke** (the fake is built from Google's documented behaviour, not a capture: confirm with Adebayo's Gmail that the token stops working and the app disappears from myaccount.google.com/permissions), what Google answers to a second revoke of the same token (assumed 400 `invalid_token`), the front end button (does not exist yet) | live step with the C3 live check |

## Operate / roll back
- **Server steps before merge: none.** After the merge, to switch the calendar on: `CALENDAR_ENABLED=true`, `TOKEN_ENC_KEY` set, the callback URL authorised at Google.
- To disconnect a user by hand (support): the user does it in the app once the screen exists; until then `POST /api/calendar/google/disconnect` with their session, or `scripts/delete_user.py` deletes the whole account.
- Roll back: the route is additive; reverting leaves rows as they are. A `disconnected` row is harmless to C3's connect.

## Follow-ups
- **Privacy wording:** C3's SOP asked for "in the app" to go back into the disconnect sentence "when the button ships". The sentence still says revoke at myaccount.google.com or ask us, because **no screen has the button yet**; change both copies (`app/templates/privacy.html`, `web/components/legal/privacy-content.tsx`) in the PR that ships the Settings → Calendar disconnect button.
- **Notification kinds:** C11b, C12 and C14 must give every calendar-related notification a `kind` starting `calendar.`, or a disconnect will not delete it.
- C14 must treat a `disconnected` status as a user choice (no "calendar disconnected" banner, mail or admin alert; those are for `needs_reconnect`); C15a's `GET /api/calendar` shows "not connected" for it.
- **I3 must land its `ON_DISCONNECT` hook before any Events subscription is created.** The spec says disconnect deletes the Events subscription; today the hook list is empty, so only the local `meet_subscription_*` fields reset and nothing exists at Google to delete. The hook gets the subscription name and the token from `ReleasedConnection`.
- **C8b (sync loop) must call `calendar_connect.register_token_cache(cache)`** once for the process's `AccessTokenCache` (the cache lives in whoever builds the `GoogleSource`; none is built in app code yet). It is per process: another process's cache would keep an access token for at most its remaining lifetime (under an hour) after a disconnect, and the next `get` fails because the row has no refresh token.
- **C8 and C12 must guard against a disconnect that lands mid-run:** before writing events, dispatching a join or reviving one, lock the connection (or join) row `FOR UPDATE` and re-check that its status is still live (`connected` or `degraded`). A sync that read the connection before the disconnect could otherwise write events back after the wipe. Nothing guards that yet because the writers do not exist on this branch.
- ICS disconnect (`disconnect_ics`, plan task I-series) is separate and not in this change.
- A refused connect (unticked box) leaves an identity-only grant at Google; still untidied (C3 follow-up).
