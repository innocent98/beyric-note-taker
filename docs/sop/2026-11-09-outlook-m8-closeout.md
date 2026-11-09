# SOP: Outlook phase O, M8, close-out (what exists, what is not yet true, go-live)

## What shipped
The close-out of the Outlook build: the FE guide `docs/fe-integration-guide-calendar-outlook.md` with its captures, this SOP (the phase map and the go-live checklist), the checklist update, and the two older guides pointed at the new fields. Branch `feature/outlook-m8-closeout`, **stacked on `feature/outlook-m7-policy` (#156, HELD)**: merge it only after M7 may merge (at switch-on), or cherry-pick the docs if wanted earlier. No application code, no migration.

## The phase, in one table
| Task | PR | SOP | State |
|---|---|---|---|
| M0 capture script | #148 | `2026-11-09-outlook-m0-capture-script.md` | script extended (keys file, pasted redirect, token lengths); **not run: Adebayo's step** |
| M1 foundation | earlier (#96) | `2026-11-05-outlook-m1-provider-foundation.md` | merged, live |
| M2 Microsoft OAuth client | #150 | `2026-11-09-outlook-m2-microsoft-oauth-client.md` | built, Opus review pending, fixtures are documented shapes |
| M5 event normaliser | #151 | `2026-11-09-outlook-m5-graph-event-normaliser.md` | built, fixtures are documented shapes |
| M3 connect, callback, disconnect | #152 | `2026-11-09-outlook-m3-connect-callback-disconnect.md` | built, stacked on M2, Opus review pending |
| M4 Graph source, token cache | #153 | `2026-11-09-outlook-m4-graph-source-token-cache.md` | built, stacked on M3, Opus review pending |
| M6 UI | #154 | `2026-11-09-outlook-m6-settings-ui.md` | built, parity proof attached |
| M7 policy, domain file | #156 | `2026-11-09-outlook-m7-policy-and-domain-file.md` | built, **HELD (draft) until switch-on** |
| M8 close-out | this PR | this file | FE guide from the real app with a fake Microsoft |

Merge order (stack): #148 and #151 any time; #150; #152 (retarget to `main` first); #153; #154; then M7 and M8 only at switch-on. #150's branch also contains the M0 commit (same content as #148): merge #148 first and its diff cleans itself.

## What is NOT yet true (read before believing any green tick)
- **Nothing has talked to Microsoft.** Every fixture, the fake Microsoft and the FE-guide captures use *documented* shapes. Each SOP lists its assumptions under "To confirm with the M0 run"; the union is the M0 acceptance list: scope string format, `oid` on personal accounts, token lengths, rotation on every refresh, refresh at the consumer tenant path, error answers and codes (`invalid_grant`, 700082, 7000215, 7000222, no mailbox, the 403 grant codes), `$select` coverage, both `Prefer` values in one request, ids stable under `ImmutableId`, the attendee count (organiser left out?), the reply values, where Zoom and Meet links sit, `Calendars.ReadBasic` versus `Calendars.Read`, Teams join-URL formats, Safe Links, `/organization` `verifiedDomains` with `User.Read` alone, whether a personal consent screen says "unverified", the page where a person removes the app.
- **The Opus reviews** of M2, M3 and M4 (and the phase review before go-live) are the lead's.
- Work and school accounts, publisher verification and `MICROSOFT_AUTHORITY=common` are untouched (Q2, Q3).
- Teams joining (P0.7, T1 to T3) gates the switch-on, not any merge.

## Designer gaps (consolidated; each is lead-designed from the Google pattern)
The work-account / unverified explainer; the five callback messages (`microsoft_denied`, `microsoft_failed`, `no_mailbox`, `tenant_not_allowed`, `already_connected`); the Outlook reconnect state; the Outlook disconnect note; the Microsoft first-run row states; the sentence under the connect buttons naming Microsoft; the one-calendar message. The off row "Not available yet." predates Outlook (PR #115) and stays while the flag is off. The designer's file otherwise draws everything built here.

## Go-live checklist (switch-on, in order)
1. **M0 run done** for the invited account types; every "to confirm" line settled; the captured bodies replace the documented-shape fixtures in `tests/fixtures/microsoft/`, `tests/fixtures/graph/`, `tests/fakes/microsoft.py`; `docs/fe-integration-guide-calendar-outlook.md` re-run (`scripts/capture_calendar_outlook.py`) and its table updated; the Microsoft permissions-page URL added to M6's note and M7's policy sentence.
2. **Teams gate**: P0.7 passed, T1 to T3 merged, a live Teams join from an Outlook event, the Teams policy sentence (T3) live.
3. **Opus reviews** of M2, M3, M4 merged-in and the whole-phase review done; every PR of the stack merged to `main` and deployed with the flag still false (`/health` 200, `calendar_sync` ticking, no `calendar_sync_microsoft` job).
4. **Server `.env`** (names only): `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET` (never in a chat), `MICROSOFT_AUTHORITY=consumers`, `MICROSOFT_SECRET_EXPIRES_ON=YYYY-MM-DD`; optional `MICROSOFT_ALLOWED_TENANTS`; **leave `MICROSOFT_ORGANIZER_DOMAIN_TRUST` and `MICROSOFT_PUBLISHER_VERIFIED` false** until M0 says otherwise. `TOKEN_ENC_KEY` and `CALENDAR_ENABLED` must already be set.
5. **M7 merge commit**: replace the domain file's placeholder id with the client id, re-date both legal pages, a lawyer has read the wording; merge and deploy.
6. Set `MICROSOFT_CALENDAR_ENABLED=true`, recreate `web` and `scheduler`; the lead connects a real personal account (consent seen, first sync seen, `GET /api/calendar` shows `microsoft_enabled: true` and the connection, a Zoom meeting joins, disconnect seen) and records the live captures.
7. Calendar the **client secret expiry** (12 months): the admins are reminded 30 days before; rotation steps are in spec 9.3.

## Verification
`scripts/capture_calendar_outlook.py` was run against a scratch Postgres with the real migrations (one head) and the real app, producing 48 files under `docs/fe-captures/calendar-outlook/`; the guide was generated from them (every block is a verbatim copy). `ruff check .` and `ruff format --check .` clean. The three older capture files that already had `auto_join_paused_text` got `microsoft_enabled: false` in place (one line each).

## Operate / roll back
**Server steps before merge: none.** Docs, captures and a capture script. Revert the PR to remove them.

## Follow-ups
Everything under "What is NOT yet true". After the M0 run: replace fixtures and fakes with captures, re-run the capture script, finish the guide's table.
