# SOP: share a Meet or Zoom link from Android to the app (PWA share target)

## What shipped
The installed web app now appears in Android's share sheet. Sharing a Google Meet or Zoom link (or the invitation text around it) to "Beyric Notes" opens `/share`, which shows the link it found and sends the note taker after one tap. Branch `feature/pwa-share-target`; PR in the report that started this session (number not known when this was written).

## Why
Adebayo uses an Android phone and wants to hand a link from the Meet or Zoom app to the note taker without copying and pasting it into the Meetings page.

## How
| Decision | Why |
|---|---|
| `share_target` in `web/app/manifest.ts`: `POST` to `/share-target`, `multipart/form-data`, fields `title`, `text`, `url` | Chrome's docs say the shared text goes in a request body for POST. A GET would put the whole invitation (and a Zoom passcode) in a URL that nginx logs. Android puts the link in `text`, not `url` (Chrome docs), so all three are read |
| `/share-target` is a Next route handler (`web/app/share-target/route.ts`) that reads the form, finds the link, keeps nothing, logs nothing, and answers `303` to `/share#link=<encoded link>&others=<n>` (or `/share#none`) | A fragment is never sent to a server, so no access log sees the link. Only the link reaches the page, never the surrounding text |
| `/share` (`web/app/share/page.tsx`, client part `web/components/share/share-flow.tsx`) moves the link from the `#` into this tab's `sessionStorage` and removes the `#` from the address bar and history entry | The link survives a reload and the trip through `/login`, and is in no URL, cookie or log. Expires after 15 minutes (`PENDING_MINUTES` in `web/lib/share-pending.ts`); whatever is read back is checked again, like shared text |
| Signed out: the link is kept and the page goes to `/login`; `LoginForm` returns to `/share` when a link is waiting (`hasPendingShare()`) | "Through login and back" without a `next=` parameter or an open redirect. Password sign-in only: Google sign-in leaves the tab and is off in production (no client id), so after it the owner lands on `/` and shares again |
| **One tap to send, not an automatic send** | A page that sends a recording bot on load would be a forgeable GET/POST: any web page could post a form to `/share-target` and make a signed-in owner's bot join a meeting of its choosing (the bot is a recorder, and it spends the pilot's hours). The tap goes through the same `POST /api/meetings` call and CSRF token as the Meetings form. The tap is also where "Tell participants that this meeting will be recorded" sits. To make it automatic later, a way to tell a real share from a forged one is needed first |
| `/share-target` has no CSRF token and no Origin check | A share comes from the system sheet and has no origin to prove (`Sec-Fetch-Site` for it was not observed). Nothing a forged POST could use happens there: no data changes, no bot starts; it only opens `/share` with a link filled in, which still needs the owner's tap |
| Link finding (`web/lib/share.ts`, `findMeetingLink`) mirrors `_parse` in `app/vexa.py`: host `meet.google.com` or `zoom.us`/`*.zoom.us`, path shape, no user info, no port, Zoom `pwd` kept and every other parameter dropped, https. Also accepts a link without a scheme and a bare Meet code when it is all that was shared. First link wins; a count of other links is shown | The server still has the last word (`invalid_meeting_url`); the client only has to find the right link and refuse look-alikes. Each part is cut to 4000 characters before any pattern runs |
| Checks on the page: bot busy and hours cap are read on the server (`GET /api/meetings`, `GET /api/session` `usage`) and lock the button up front, with the server's own sentence; unsupported link, `bot_already_active`, `bot_busy`, `hours_cap_reached`, `bot_send_failed` come back from `POST /api/meetings` and are shown as the server wrote them | Same checks as the Meetings page. **There is no separate "duplicate link" check on the server**: the only refusal for sending the same meeting twice is `bot_already_active` (one bot per account), which is covered |
| A service worker, `web/public/sw.js`, registered by `web/components/pwa-registration.tsx` | Chrome's docs (see below): since Chrome 108 on mobile the menu "Install" no longer needs a service worker, but **the install prompt algorithm still requires a fetch handler**. The worker is network-only: only page navigations (GET) are handled, it calls `fetch(request)`, and on failure returns a short offline page. It caches nothing and stores nothing; API calls, scripts and every POST (including the share) are left to the browser |
| No CSP change | `navigator.serviceWorker.register('/sw.js')` registered under the production CSP in real Chrome with and without a `worker-src` directive (checked with a stand-in page carrying the same `script-src`), so `web/proxy.ts` and `web/lib/csp.ts` are untouched |

Rejected: GET share target (logged URL); caching or precaching `/share` for offline (private pages must never be cached); handling the POST inside the service worker (more code on the critical path, and the server route needs no worker); passing the link through a cookie (needs a route to clear it, and would travel to the server on every request); `?next=` on `/login` (an extra thing to validate).

## What's involved
`web/app/manifest.ts`, `web/app/share-target/route.ts` (new), `web/app/share/page.tsx` (new), `web/components/share/share-flow.tsx` (new), `web/components/pwa-registration.tsx` (new, mounted in `web/app/layout.tsx`), `web/public/sw.js` (new), `web/lib/share.ts` and `web/lib/share-pending.ts` (new), `web/components/app/login-form.tsx` (return to `/share`), `web/lib/routes.ts` (`/share` in `NEXT_PAGES` and `APP_PATHS`, `/share-target` in `NEXT_ROUTES`), `web/proxy.ts` (`/share` in the matcher, so the page gets the nonce CSP), tests `web/tests/share-link.test.ts`, `share-page.test.tsx`, `share-target.test.ts`, captures `docs/fe-captures/share-target/`. No Python, no migration, no env variable, no nginx change (a POST to `/share-target` goes through `location /` like `/logout` does; the 1 MB body limit is far above a shared invitation).

## Verification
- `npm run lint`, `npm run typecheck`, `npx vitest run` in `web/`: all pass (274 tests; 35 new: link extraction incl. long sentence, Zoom passcode, Meet code, no link, two links and hostile text; the route handler; the page's states and every refusal; the login round trip; storage expiry and tampering; manifest and service worker content).
- Production build of `web/` run locally, real headless Chrome (current stable on this Mac) driven over the DevTools protocol, with a stand-in for FastAPI that replays the captured responses in `docs/fe-captures/owner-app/` (not the real backend): the manifest parses with no errors, `Page.getInstallabilityErrors` is empty, the service worker registers and activates with no CSP violations, a cross-origin multipart POST to `/share-target` lands on `/share` with the link, signed out it goes to `/login` and after sign-in returns to `/share`, one tap sends `POST /api/meetings` with the CSRF token and the right body, and each refusal shows the server's sentence. Phone-sized screenshots looked right.
- Real responses from the running route: `docs/fe-captures/share-target/`.
- **NOT verified:** the real Android share sheet (needs the phone: see the test below); what `Sec-Fetch-Site` and `Origin` a real Android share POST carries; behaviour against the real FastAPI and database (the existing endpoints are unchanged and already captured); Chrome's own install prompt, which needs engagement signals that a headless run cannot give; behaviour on the live server behind nginx and Cloudflare.

**The phone test (Adebayo, about two minutes):**
1. On the Android phone, open https://notes.beyrictech.com in Chrome, sign in, then Chrome menu (three dots) -> **Install app** (or "Add to Home screen" -> Install). Open it once from the home screen.
2. Open Google Meet (or Zoom), open a meeting's link or invitation, tap **Share**, choose **Beyric Notes**. If you are signed out, sign in; you come back to the shared link.
3. Check the page shows "Google Meet abc-defg-hij" (your meeting), tap **Send note taker**, then open **Meetings** and check the new meeting is listed as "Bot sent".
If Beyric Notes is missing from the share sheet, uninstall and reinstall the app once (Android registers share targets when the app is installed) and tell the lead what you saw.

## Operate / roll back
Revert the PR. An installed copy keeps its old manifest until Chrome refreshes it (a day or so), so its share entry can stay in the sheet for a while; `/share-target` and `/share` then no longer exist and the share opens the Meetings page's fallback (FastAPI's 404). To retire the service worker without a redeploy of the page code, ship a `sw.js` that calls `registration.unregister()`.

## Follow-ups
- Decide whether a share should send without the tap, which needs a way to tell a real share from a forged post.
- Google sign-in does not return to `/share` (the OAuth round trip leaves the tab; it is off in production).
- Optional: a "Share a link" explanation on the Meetings page, and an install hint for Android phones.
- A shared link left after a refusal stays in the tab's storage for 15 minutes and is shown again if `/share` is opened with nothing new shared.

## Sources (read 2026-11-03)
- Chrome for Developers, "Receiving shared data with the Web Share Target API": the app must be installed first; a `share_target` with `action`, `method`, `enctype` and `params`; POST data is a request body; "on Android the `url` field will be empty", URLs arrive in `text` or `title`.
- Chrome for Developers blog, "Revisiting Chrome's installability criteria": the service worker `fetch()` requirement for install-from-menu was removed in Chrome 108 (mobile) and 112 (desktop); "the algorithm that displays the install prompt still requires the presence of a `fetch()` handler"; Chrome 112 ignores empty fetch handlers (so the handler does real work).
- MDN, `share_target` manifest member: installed PWA only; use POST when sharing causes a side effect.
