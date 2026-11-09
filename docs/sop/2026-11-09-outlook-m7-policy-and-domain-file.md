# SOP: Outlook phase O, M7, privacy and terms rows and the domain file (HELD)

## What shipped
The privacy-policy and terms rows for Outlook (Outlook spec 8.2) in both copies, and the file Microsoft reads to tie our domain to the app registration (spec 8.3). Branch `feature/outlook-m7-policy`, **stacked on `feature/outlook-m6-ui` (PR #154)**.

> **HELD: this PR must NOT merge until the switch-on gate of spec section 7 is met and `MICROSOFT_CALENDAR_ENABLED` is about to go on.** A merge deploys, and the policy must never run ahead of the product. Until then the live policy and terms say nothing about Outlook (the test that guarded that is rewritten here: it flips with this PR).

## Why
Spec 8.2: the policy has to name Microsoft data, what is read, what is kept and how it is removed, in the same PR that makes the feature live; the app registration needs a verified publisher domain before work accounts are invited (spec 8.3).

## How
| Where | Change |
|---|---|
| Privacy, "Information you give us", account | "If you connect Outlook, we also keep an identifier that Microsoft gives your account (a tenant and user identifier) and the email address of that Microsoft account." The spec's draft said "your name and the email address"; **the code stores no Microsoft name** (only the tenant id, the object id and the address as a label), so the name is left out: the policy says only what is true |
| Privacy, new section `<h2 id="microsoft">Microsoft user data</h2>` after Google's | What is read from the main Outlook calendar (title, start and end, whether you organise it, your reply, the number of attendees, not their addresses, the meeting link); that an event's description and location are read only to find the link and not kept (true: `graph_events` scans and drops them); that nothing is created, changed or deleted; the same handling rules as Google user data (no sale, no advertising, no AI training, people at Beyric do not read it except for support, security or the law); and the disconnect paragraph: deleting our copy and the credential, and that Microsoft gives an app no way to withdraw its own access, so the person removes Beyric in their Microsoft account's app permissions. **No Microsoft URL** is named: the page is not confirmed (M0 S7); add it from the capture |
| Privacy, sharing | "Meeting and calendar platforms (Google, Microsoft and Zoom)" |
| Privacy, security | "Calendar access credentials are encrypted before they are stored" (was "Google access ...") |
| Privacy, legal bases | "such as when you connect your Google account or your Outlook calendar" |
| Terms, third-party services | "Google Meet, Google Calendar, Microsoft Outlook and Zoom". **Microsoft Teams is not added**: the spec lists it at the gate, but joining Teams is not built (T1 to T3, P0.7); its sentence belongs to T3 and the policy must not promise it |
| "Last updated" | **Not changed here.** Re-date both pages (the `Last updated` line and the terms version the test pins) in the same commit that merges this PR, at switch-on |
| TOC | `["microsoft", "Microsoft user data"]` after `google` in `web/components/legal/toc.ts` |
| Domain file | `web/public/.well-known/microsoft-identity-association.json` = `{"associatedApplications":[{"applicationId": ...}]}`. The id is a loud placeholder, **`REPLACE-WITH-THE-APPLICATION-CLIENT-ID-AT-SWITCH-ON`: replace it with the real Application (client) ID in the merge commit** (it is public, not a secret). A vitest test accepts only a GUID or that placeholder |
| Rejected | A route handler that builds the file from `MICROSOFT_CLIENT_ID` (adds a Next route and needs the variable in the front end's environment); naming a Microsoft permissions URL before M0 confirms it; adding Teams wording now |

## What's involved
`app/templates/privacy.html`, `app/templates/terms.html`, `web/components/legal/privacy-content.tsx`, `terms-content.tsx`, `toc.ts` (kept identical by `web/tests/legal.test.tsx`), `web/public/.well-known/microsoft-identity-association.json`, `web/tests/microsoft-identity-association.test.ts`, `tests/test_legal.py` (two tests rewritten, one added).

## Verification
- `web/tests/legal.test.tsx` (the two copies have the same text, ids and links), `tests/test_legal.py` (12), the new domain-file test, full vitest (992), typecheck, lint.
- **The domain file was served by the built app**: `GET /.well-known/microsoft-identity-association.json` answered 200 `Content-Type: application/json; charset=UTF-8` from a production `next build` (Microsoft rejects another type). `deploy/nginx.conf` has only `location /` for this path (everything goes to Next), so **no nginx edit is needed**.
- **Not verified:** the file with the real client id on the real domain, and Microsoft's publisher verification itself (Partner Center, spec Q3: Adebayo's step). A lawyer must read the wording first (handoff section 5); this is not legal advice.

## Operate / roll back
**Server steps before merge: none** (a static file in the web image; the web container is rebuilt by the deploy). **Merge only at switch-on**, in this order: gate met, replace the placeholder id, re-date both pages, merge, deploy, set `MICROSOFT_CALENDAR_ENABLED=true`. Roll back by reverting the PR (the Microsoft features then must be off).

## Follow-ups
- Add the Microsoft permissions URL to the disconnect paragraph and to M6's note once M0 S7 confirms it.
- Teams sentence with T3.
- Publisher domain verification in the tenant (DNS TXT) and publisher verification (Q3), Adebayo's steps.

**Update 2026-11-09 (evening):** the placeholder was replaced with the real Application (client) ID (given by Adebayo in chat; public, not a secret) in `chore/ms-domain-file-client-id`, so the file is ready for Microsoft's publisher-domain check. Still to do by Adebayo: add and DNS-verify `beyrictech.com` in the Entra tenant (the work tenant `beyric.onmicrosoft.com`), set it as the app's publisher domain, and finish the Partner Center verification.
