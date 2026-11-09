# SOP: Outlook phase O, M6, Settings and first-run screens for Outlook

## What shipped
The Outlook parts of the screens, as the designer drew them (`Beyric Note Taker.dc.html`: `outlookLabel`, `connectOutlook`, the connected row), drawn live **only when the server says so**: `GET /api/calendar` has a new `microsoft_enabled` boolean (true when `MICROSOFT_CALENDAR_ENABLED` and the client are on), provider-aware words on the server, and the web changes. Branch `feature/outlook-m6-ui`, **stacked on `feature/outlook-m4-graph-source` (PR #153)**; retarget in order when the bases merge. No migration.

## Why
M3 and M4 built the backend dark. Spec 6.4: the designer drew two buttons and one connected card; everything else (errors, explainer, disconnect note) was not drawn and is built from the Google pattern and listed below as designer gaps.

## How
| Piece | Decision |
|---|---|
| **Flag off = today's page** | With `microsoft_enabled` false or absent the Settings card and the first-run rows render **exactly as before**: one live Google button, the Outlook button switched off with "Not available yet." (it has been shown that way since PR #115). The 61 earlier Settings tests, 9 setup tests and the upcoming/meetings tests pass unchanged. No Outlook wording is added to any screen in that state. (The brief said "nothing while the flag is off"; the spec section 7 says "must look exactly as it does today", and today includes the off row, so that is what is kept: a lead decision if the off row should go too.) |
| Not connected, Outlook on | The designer's two buttons side by side, both live, each reading "Connect Google Calendar" / "Connect Microsoft Outlook" and "Connecting..." while its own call runs (both disabled meanwhile). The "Not available yet." line goes. The note under them says "Google or Microsoft ... your main calendar" |
| Connected | The provider name from the state (`Google Calendar` / `Microsoft Outlook`), "{address} . Connected" (or "Needs to connect again"), Disconnect. The confirmation, the aria label, the success line and the reconnect button follow the provider. `disconnect` and `connect` take the provider (`api.calendar.connect("microsoft")`) |
| Disconnect note | Google: the existing link to `myaccount.google.com/permissions` when `revoked` is false. Microsoft: `revoked: false` with `reason: provider_has_no_revoke` shows "Microsoft does not let Beyric remove its own access. To be sure, open the app permissions of your Microsoft account and remove Beyric Note Taker there." **No URL**: the page address is not confirmed (M0 S7), so none is invented |
| Callback words (designer gaps, lead-designed) | `microsoft_denied`: "You did not connect your calendar." `microsoft_failed`: "Something went wrong connecting Microsoft Outlook. Try again." `no_mailbox`: "That Microsoft account has no calendar Beyric can read. Try another account, one that has an Outlook mailbox." `tenant_not_allowed`: "That Microsoft account belongs to an organisation Beyric cannot connect yet. Try a personal Outlook.com, Hotmail or Live account." `already_connected`: "Another calendar is already connected. To use a different one, disconnect first." `different_account` and `wrong_account` follow the provider. `scope_missing` keeps one sentence for both. A code the page does not know still becomes `google_failed`; no code is ever printed |
| Unverified explainer | The server's `show_unverified_notice` for Microsoft is true only for authority `common` while the publisher is unverified (work accounts are documented to meet that screen; a personal account's consent is not documented to, M0 S2), so the pilot (`consumers`) shows no explainer. When shown: "Microsoft may warn that Beyric comes from a publisher it has not verified yet. That is expected for now. Continue to Microsoft and accept the permissions." |
| First-run rows | `SetupWorkspace` takes `microsoftEnabled` and `connectedProvider`: off keeps the disabled Outlook row; on makes it a live row ("Connect", "Connecting...", "Connected"); the other row stays disabled once one calendar is connected |
| Server words | `calendar_text.reconnect_text(provider, reason)`, `error_text(recorded, provider)`, `state_text` follow the connection's provider ("Microsoft access was removed. Connect again to carry on." and its siblings; Google's sentences are pinned unchanged). The "calendar disconnected" bell entry and its email use them; the email footer names "Microsoft Outlook" or "Google Calendar" (payload `provider`; no payload keeps Google's footer). A Teams event stays "Cannot join" with "Microsoft Teams meetings cannot be joined yet" while Teams joining is off |
| Availability | `GET /api/calendar`, settings and recurring now need `CALENDAR_ENABLED` and (Google configured **or** Outlook on), so an Outlook-only server works |

## Designer gaps (built from the Google pattern, for the designer to confirm)
The unverified or "needs administrator approval" explainer for work accounts; the five Outlook callback messages above; the reconnect state for Outlook; the Outlook disconnect note; the Microsoft first-run row states; the sentence under the buttons naming Microsoft; the one-calendar-at-a-time message. The designer drew none of these.

## What's involved
- Server: `app/calendar_api.py` (`microsoft_enabled`), `app/calendar_text.py`, `app/calendar_states.py`, `app/mail_templates.py`; tests `tests/test_calendar_microsoft_words.py` (18), `tests/test_microsoft_connect.py` (notice flag).
- Web: `lib/api/client.ts` and `types.ts` (provider argument), `lib/calendar-notice.ts` (five new codes), `components/app/settings-calendar.tsx`, `components/auth/setup-workspace.tsx`, `app/setup/page.tsx`, `lib/api/schema.d.ts` and `docs/openapi.json` (regenerated), `web/scripts/mock-api.mjs` (`--calendar microsoft_off|microsoft_on|microsoft_connected|microsoft_needs_reconnect`), `web/scripts/parity-outlook.mjs`, `web/tests/settings-calendar-outlook.test.tsx` (19).

## Verification
- **Parity proof** (`docs/ui/parity/after/outlook-settings-calendar-report.txt` and the six `outlook-settings-calendar-{a,b}-{1440,768,390}.jpg`, designer left, built app right, Next on a mock API): the connected row, the provider name, the address line and the Disconnect button match the designer's computed styles and positions exactly (state B); the two buttons match the designer's type, colour, border, radius and padding and share one row (state A); the only difference is the existing Button's `line-height` and a 2 px width, which the Google button shows identically.
- Vitest 990 pass; typecheck clean; lint 0 errors (3 old warnings).
- Backend suites listed in the PR body.
- **Not verified live:** nothing on these screens was seen against a real Microsoft account; the Outlook state bodies in the tests and the mock are the Google captures with the new fields added by hand (documented shape). The captures for the FE guide come from the live run (M8).

## Operate / roll back
**Server steps before merge: none.** The field is `false` until `MICROSOFT_CALENDAR_ENABLED` is on, so production looks unchanged. Roll back by reverting the PR (the old web ignores the extra field).

## Follow-ups
- M7 (policy, held), M8 (FE guide from live captures, regenerate the calendar captures with `microsoft_enabled`).
- Designer to confirm the gap list; the Microsoft permissions-page address (M0 S7) for the disconnect note.
