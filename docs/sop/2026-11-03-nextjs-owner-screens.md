# SOP: Next.js owner app screens (stream C of the migration)

## What shipped
The owner app now renders in Next.js (`web/`): login, the Meetings page with the new-meeting form and list, the meeting page (notes, transcript, rewrite, cancel, retry, polling), Templates and Vocabulary, plus a typed client for stream B's JSON API. Next also took over `/` (the landing page for a visitor, the Meetings page for the owner) and `/logout`. Branch `feature/nextjs-app-screens`, PR against `feature/nextjs-scaffold` (stacked on stream A's PR #19; the lead retargets it to `main` after #19 merges). Not merged, not deployed.

## Why
Adebayo decided on 2026-11-02 to move the frontend to Next.js for parity with the designer's prototypes in `docs/ui/Beyric Note Taker redesign/`. Streams A (scaffold) and B (JSON API) made this stream possible.

## How
| Piece | Decision |
|---|---|
| API client | `web/lib/api/schema.d.ts` is generated from `docs/openapi.json` (`npm run gen:api`); `types.ts` only names its shapes. `client.ts` is one fetch wrapper that works on the server and in the browser: one `ApiError` (`status`, `code`, `message`, `fields`) for the one error shape, `gateway_error` for a non-JSON answer (never `.json()` on an HTML page), `network_error` when nothing answered, the CSRF token read once from `GET /api/session` and sent on every write, a stale token (403 `csrf_rejected`) refreshed and the write retried once (not if the session turned out to be logged out), `onUnauthorized` on a 401 `not_authenticated` |
| Where calls are made | **Reads on first load run on the server** (`lib/api/server.ts`: the visitor's `Cookie`, `X-Forwarded-For`/`-Proto` passed on; `requireOwner()` sends a logged-out visitor to `/login`; `load()` turns a 401 into that redirect and a 404 into the not-found page). **Writes and polling run in the browser** (`lib/api/browser.ts`, same origin): a login's `Set-Cookie` lands in the browser by itself, and `POST /api/meetings/<id>/regenerate` reaches nginx's 300 s `/api/` location directly instead of going through a server action posted to the page path (which would get the 60 s default). **Deviation from the brief ("used server-side"):** writes are not made from server actions, for those two reasons. Logout is the one write done on the server, see below |
| Routing | `lib/routes.ts` `NEXT_PAGES` now holds `/`, `/login`, `/meetings/[id]`, `/templates`, `/vocabulary`, `/privacy`, `/terms`, `/styleguide`; `NEXT_ROUTES` holds `/logout`. `proxy.ts`'s matcher (nonce CSP) lists the same pages (`[id]` is `:id` there); `tests/routes.test.ts` checks pages, handlers and matcher agree. Everything else still falls through to FastAPI (`/health`, `/static`, `/api`, `/meetings/<id>/cancel` and the other Jinja POST routes) |
| `/` | One URL, two pages, chosen by `GET /api/session`: owner sees Meetings (noindex), anyone else the landing page (indexed, canonical `/`). If FastAPI is unreachable the visitor sees the landing. `/welcome` redirects (307) to `/` and its page is gone. The public header offers "Open the app" to the owner |
| `/logout` | `app/logout/route.ts`: POST only. The sidebar's Log out is a plain `<form method="post" action="/logout">`, so it works without JavaScript. The handler calls `DELETE /api/session` with the visitor's cookie (reading the CSRF token itself, which a bare form cannot supply), passes FastAPI's new anonymous `Set-Cookie` on and answers 303 to `/login` (relative `Location`). Because it supplies the token, it refuses a POST whose `Sec-Fetch-Site` is not `same-origin` (or whose `Origin` host differs) with 403 (found by the automated security review of the commit). GET never logs out. Logout signs out every device (the server bumps the session epoch) |
| `/styleguide` | 404 for anyone but the owner in production (was 404 for everyone) |
| Login | Client form, `POST /api/session`; shows the API's message; locked after a 429 (15 minutes, no countdown); already-logged-in visitors are redirected to `/`. Without JavaScript the form does not submit (the Jinja form did); accepted for a single-owner app |
| Meetings | New-meeting form (link with icon, template select, "Send note taker", the recording reminder), disabled with the server's own sentence while a bot is on a meeting; the typed link stays after an error and is never put in a URL. List: icon tile, title, time in UTC (same format as the Jinja pages), template name, status badge with a dot, "Recent" header with a count, "Show older meetings" (`before_id` paging, the Jinja page showed 50 and stopped), refresh when the tab becomes visible. Worker-stale alert and empty state as before |
| Meeting page | Header with badge, details card (date, platform, template), tabs Notes and Transcript with one Copy button for the tab shown (copies raw `notes` markdown, or the transcript string), rewrite card, "What went wrong" card with Try again or the retry-blocked message (the API's `retry_block_message`, else the same three sentences as Jinja), "Waiting for the note taker to join", step names while processing, attempt notice, bot-left-early and speakers-incomplete notices. Notes come from `notes_html` through `dangerouslySetInnerHTML` in one component (`components/app/notes-html.tsx`), which a guard test pins as the only use besides JSON-LD. The transcript's `[Name]: text` lines are shown one row per turn with initials (display only; a transcript with no labels is one block). Cancel asks first (a `<dialog>`: it cannot be undone and a cancelled meeting cannot be retried). Polling: every `refresh_after_seconds` (30) while set, paused in a hidden tab, caught up on return, never overwriting a running action; an action's response replaces the state, a 409 refetches. Rewrite: select and button disabled and "Rewriting..." while it runs, a note that it can take minutes; a gateway timeout or lost connection says the change may still go through and the page checks again after a minute |
| Templates | The designer's two-pane layout (list with previews on the left, editor on the right, "Add template" top right), same limits as the server (name 100, instructions 4000), "Saved" label, duplicate name shown from the API |
| Vocabulary | Add row, a count line, a trash icon button per term (accessible name "Delete <term>"), list re-read after an add so the order is the server's, a 404 on delete treated as success |
| Design system | New components in `components/ui` (all shown in `/styleguide`): `Icon`, `IconButton`, `TabButtons`, `Badge dot`, `Field icon`/`hideLabel`. The select keeps the designer's custom chevron through `/icons/chevron-down.svg` (the CSP allows images from this origin only, so not a data: URI). Phone top bar: logo and log out on one row, the links on the next. `.avatar` was hidden on phones for the sidebar; that rule is now scoped to the sidebar |
| Guards | `tests/guards.test.ts`: the "no inline event handler" check used to flag every `onClick=`; it now flags HTML attributes (`onclick=`) everywhere and allows React event props only in files that start with `"use client"`. New guard: `dangerouslySetInnerHTML` only in the notes component and JSON-LD. No colour literal, no inline style and every-component-in-the-style-guide still pass. The chevron SVG holds a colour literal but lives in `public/`, outside the scanned folders; it is the `--text-faint` grey in both themes |

Rejected: server actions for writes (see above); parsing `notes` in the browser (the guide says use `notes_html`); a generated client with a runtime dependency (the typed wrapper is 150 lines and `openapi-typescript` is a dev dependency only).

## Copy changes against today's Jinja pages
Where the prototype clearly differs, the prototype's words are used.
| Where | Jinja | Now |
|---|---|---|
| Login heading, button | "Log in", "Log in" | "Sign in to Beyric" with "Welcome back." beneath, button "Sign in" (Auth.dc.html; the public header already says "Sign in") |
| New meeting button | "Send bot" | "Send note taker" |
| Link placeholder | none | "Paste a Google Meet or Zoom link" (the prototype also names Microsoft Teams; Teams is not supported) |
| Bot left early | "The bot left the meeting early or was removed. The notes may be incomplete." | "The note taker left the meeting early or was removed. The notes may be incomplete." |
| List | no header | "Recent" with "N meetings" |
| Meeting page, waiting | "This page updates every 30 seconds." only | "Waiting for the note taker to join" and "Admit the note taker from the waiting room if your meeting requires it." (prototype's Joining text), then the 30-second line |
| Meeting page, processing | badge only | the step name as title and "Notes and a full transcript will appear here when this finishes." |
| Rewrite card | label "Rewrite the notes with a template" | title "Rewrite notes" and "Regenerate these notes with a different template." (the old label stays as the select's accessible name) |
| Vocabulary delete | text button "Delete" | trash icon button, name "Delete <term>"; field placeholder "Add a name, product or acronym" |
| Cancel | no confirmation | confirmation dialog "Cancel this meeting?" (the integration guide asks for one) |
Unchanged: page titles and subtitles, "New meeting", the recording reminder, the worker banner, "What went wrong", "Try again", the three retry-blocked sentences, the Zoom attempt notice, the speakers notice, "Copy notes", "Copy transcript", the empty states, "Add term", "Add template", "Save", "Cancel and remove the bot". Page `<title>`s are now "Meetings - Beyric Note Taker" style (the Jinja ones ended "- Beyric Notes").

## Not built
| In the prototype | Why not |
|---|---|
| Download menu, Share notes dialog | no endpoint (stream D) |
| Translate select and banner | no endpoint (stream D) |
| Parsed notes sections (action-item board links, "Needs owner", carry-over chips), Details: duration, participants, series, milestone | the API returns notes as markdown and has no such fields (stream D) |
| Template "Delete template" | no `DELETE /api/templates/<id>` |
| Teams links | not supported by the app |
| Upcoming, Recurring, calendar card, Tasks, Projects, Decisions, Review, Team, Settings | stream D |
| Sidebar name and email ("Adebayo Tosin") | `GET /api/session` returns no identity; the sidebar says "Owner" as the Jinja one did |
| Sign-up, Google sign-in, forgot password, check email, new password | accounts do not exist yet (pilot design) |
| "Decisions" check icons and the grey "Please check" box inside the notes | `notes_html` is plain HTML; the bullets use the teal dot |

## What's involved
`web/lib/api/*`, `web/lib/{routes,format,transcript}.ts`, `web/proxy.ts`, `web/next.config.ts` (`/welcome` redirect), `web/app/{page,error}.tsx`, `web/app/{login,logout,templates,vocabulary}/`, `web/app/meetings/[id]/page.tsx`, `web/app/styleguide/page.tsx`, `web/components/app/*` (shell, login form, new-meeting form, meeting list, meeting view, transcript panel, notes, copy button, templates editor, vocabulary manager), `web/components/ui/{icon,icon-button,tab-buttons,badge,field,index}`, `web/components/site/public-shell.tsx`, `web/styles/{components,pages}.css`, `web/public/icons/chevron-down.svg`, tests, `web/scripts/{owner-e2e.mjs,owner-browser-check.mjs,seed-owner-states.sql}`, `docs/checklist/master-checklist.md`. No Python, no `compose.yaml`, no `deploy/`. Dev dependencies added: `openapi-typescript`, `jsdom`, `@testing-library/{react,dom,user-event}`.

## Verification
Run on this Mac on 2026-11-03 against a scratch compose stack (`db`, `migrate`, `web`, `next` built from this branch, development `.env`, no Vexa, no OpenAI, no worker). Meetings past `bot_sent` were inserted by `web/scripts/seed-owner-states.sql`.
| Check | Result |
|---|---|
| `npm run lint`, `typecheck`, `npm test` (89 tests, 8 files), `next build` (inside the image build) | pass |
| Client against the captures: every error case replays a file from `docs/fe-captures/owner-app/` and expects its exact code and message | `tests/api-client.test.ts` |
| Screens against the captures: all 12 meeting states of guide section 7, polling with fake timers (30 s, stops at ready, pauses hidden), cancel confirm, retry, rewrite pending and failure, 409 refetch, forms, list paging, templates, vocabulary, login lockout, `/logout` (cookie passed on, cross-site refused, GET harmless) | `tests/meeting-view.test.tsx`, `screens.test.tsx`, `server-bits.test.tsx` |
| `scripts/owner-e2e.mjs --next http://127.0.0.1:8020`: through Next only, 41 checks: landing, nonce CSP, redirects for a logged-out visitor, login (wrong then right), pages as the owner, create and update a template, duplicate refused, add and delete a vocabulary term, list meetings, meeting pages, 404s, bad link refused, `/health` and `/static` still reach FastAPI, cross-site logout refused, logout, the new cookie anonymous, the old cookie revoked on pages and API | 41/41 |
| `scripts/owner-browser-check.mjs` (Chromium, light and dark, 375 px and 1280 px, 64 screenshots): login through the form with a wrong password first, every seeded meeting state, ready meeting's transcript tab, client navigation keeps the document, template added and a duplicate refused through the UI, term added and deleted through the UI, logout through the button, no horizontal scroll, no console error or CSP violation | pass |
| Polling in the real browser (`--poll-check`) | the page's own request arrived after 30 s |
| Looked at by eye: Meetings, meeting (ready, bot left early, processing, waiting, failed, transcript), Templates, Vocabulary, login, at 375 and 1280, light and dark | two bugs found and fixed (transcript avatars hidden on phones, list titles cut off beside the badge) |
| **Not verified** | CI on GitHub; a real Vexa bot or a real OpenAI rewrite (the 300 s path, real durations, the edge cutting a rewrite short); Safari and Firefox; real nginx and Cloudflare in front; the `Secure` cookie over https; the live server; the cancel dialog and the "attempt N of M" notice were checked in tests and in the DOM, the cancel dialog was not photographed; a `bot_sent` meeting's page was only exercised in tests and one screenshot pass after seeding |

## Operate / roll back
Nothing to configure: no new environment variable (the server calls use `FASTAPI_INTERNAL_URL`, already set for `next` in `compose.yaml`). The image needs a rebuild as usual.

**Back to the Jinja pages, one path at a time:** a path Next does not own is proxied to FastAPI, so to hand a page back remove it from all three places, rebuild the `next` image and redeploy: its entry in `NEXT_PAGES` (`web/lib/routes.ts`), its entry in the matcher in `web/proxy.ts`, and its folder under `web/app/` (`tests/routes.test.ts` fails until the three agree). For `/logout` remove it from `NEXT_ROUTES` and delete `web/app/logout/`. Examples: dropping `/templates` serves `GET /templates` from Jinja again; dropping `/` and `/login` brings back the Jinja landing and login (the cookie is the same, so a session survives the switch; the Jinja forms post to `/login` and `/logout`, which FastAPI then answers). A whole-stream rollback is reverting the merge; the Jinja templates and routes were not touched. Remove `/meetings/[id]` together with `/` (the new Meetings page links to it; the Jinja list links to the Jinja page).

`web/scripts/owner-e2e.mjs` and `owner-browser-check.mjs` write into the database FastAPI uses: run them only on a scratch stack.

## Follow-ups
- API gaps for stream B (none block these screens): `DELETE /api/templates/<id>`; the identity for the sidebar (name, email) on `GET /api/session`; meeting duration and participants; structured notes (sections as data) if the designer's decision and action-item widgets are wanted; download, share and translate endpoints (stream D).
- A font preload warning ("preloaded using link preload but not used") appears in Chromium's console on any page left open for a few seconds, `/privacy` included. The `<link rel=preload>` is in `web/app/layout.tsx` (stream A). `owner-browser-check.mjs` ignores that one message.
- The login form needs JavaScript. A `POST /login` route handler could restore the no-JS path if wanted.
- Templates on a phone: the full list comes before the editor (capped at 60% of the screen height and scrollable); a select-then-edit pattern would be shorter.
- The list refreshes when the tab becomes visible; it does not poll while open (as the guide recommends).
- Dates are UTC as on the Jinja pages; showing the viewer's zone needs a client-only component to avoid a hydration mismatch.
- Re-measure `next`'s memory on the server now that it renders owner pages and calls the API (the 512 MB cap stands).
