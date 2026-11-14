# SOP: the owner's Meetings page moves to `/meetings`; `/` is the landing page for everyone

**What shipped.** The Meetings dashboard now lives at `/meetings` (owner only). `/` shows the landing page to everyone, signed in or not, with one "Open the app" button in the header for a signed-in owner that leads to `/meetings`. `/welcome` is a permanent redirect to `/`. Every place that meant "the dashboard" now points at `/meetings`. Branch `feature/meetings-dashboard-route` (PR opened 2026-11-14, merged by the lead). Web plus two one-line backend defaults (the sign-in fallback and the could-not-join mail button). No migration, no API change, no FE guide change.

## Why

`/` was two pages (landing for a visitor, Meetings for the owner), so the owner could reach the landing only through `/welcome`, typed by hand. Adebayo wanted the bare domain to be the landing page for everyone, with the app one click away.

## How

| Piece | What and why |
|---|---|
| Dashboard | `web/app/meetings/page.tsx` holds `MeetingsPage` and its two helpers (calendar, not recorded), moved unchanged from `app/page.tsx`. It calls `requireOwner()` (a visitor goes to `/login`, as `/tasks` etc. do) and is `noindex, nofollow`. `/meetings/[id]` is untouched |
| Landing | `web/app/page.tsx` renders `LandingPage` for everyone with `signedIn` from `GET /api/session`. Metadata is the visitor's one for both: canonical `/`, no `noindex` (the page is identical, a crawler is never signed in) |
| Landing header | `signedIn` header: "Open the app" now leads to `/meetings`. The header and footer logo are plain `/` (no more `signedIn ? "/welcome" : "/"`). `LegalShell` lost its session read (logo is `/` for all, so it is a plain server component again); `PublicShell` (404) logo `/`, "Open the app" `/meetings` |
| `/welcome` | `next.config.ts` `redirects()` sends it to `/` with `permanent: true` (308). The page file, its `NEXT_PAGES` entry and its proxy matcher entry are gone; `/meetings` took its place in both lists (`tests/routes.test.ts` keeps them equal to `app/`) |
| Robots, sitemap | `APP_PATHS` already listed `/meetings` (a prefix, so it covers the bare page too). Sitemap still lists `/`, `/privacy`, `/terms`. `llms.txt` has nothing about the dashboard |
| Re-pointed to `/meetings` | Shell Meetings nav and both shell logos (`shell.tsx`, `shell-nav.tsx`); the "All meetings" crumb in `series-view.tsx` and `meeting-view.tsx`; the redirect after deleting a meeting; `login-form.tsx` (after sign-in), `setup-workspace.tsx` (after the name, and "Skip for now"); `share-flow.tsx` ("Go to Meetings", two places); the signed-in guards in `login`, `signup`, `forgot-password` pages and the `setup` page's non-manager redirect; `lib/accounts.ts` (`return_to` for Google); `manifest.ts` `start_url` (scope stays `/`) |
| Backend | `app/google_signin.py` `DEFAULT_RETURN_TO = "/meetings"` is the fallback for an unsafe or missing `return_to`, and `app/api_accounts.py` uses it after a Google sign-in. `RETURN_TO_PATTERN` accepts `/meetings` as it is (one leading slash, no `//`). `mail_templates.SEND_AGAIN_PATH` is `/meetings`: the could-not-join mail's "Send it again" button |
| Left alone on purpose | `/logout` (POST goes to `/login`, GET goes to `/`: the landing), `not-found.tsx` ("Go to the home page" is `/`), the old Jinja pages in `app/web.py` (dead behind Next, they keep their own `/`), `/settings`, calendar and Outlook connect `return_to`s (they go to `/settings`), meeting links in mail, Slack and Linear (already `/meetings/<id>`) |
| Resolved follow-up | The 2026-11-13 SOP asked what `/signup` does for a signed-in owner who clicks the hero's "Get started": it redirects to `/meetings` (guard above) |

## What's involved

`web/app/meetings/page.tsx` (new), `web/app/page.tsx`, `web/app/welcome/page.tsx` (deleted), `web/next.config.ts`, `web/proxy.ts`, `web/lib/routes.ts`, `web/lib/accounts.ts`, `web/app/manifest.ts`, `web/app/sitemap.ts` (comment), the components listed above, `web/scripts/*.mjs` (owner-e2e, owner-browser-check, mock-api, parity-shots, parity-local-time, browser-check: paths and expectations; these are the repo's browser/e2e scripts, there is no Playwright spec folder), `app/google_signin.py`, `app/api_accounts.py`, `app/mail_templates.py`. Tests: `web/tests/welcome.test.tsx` (rewritten), `routes`, `meetings-calendar`, `meetings-recurring`, `share-target`, `meeting-view`, `screens`, `share-page`, `setup-workspace`, `accounts-server`, `accounts-api`, `accounts-pages`, `series-view`; `tests/test_google_signin.py` (fallback and the `/meetings` allow-list test), `tests/test_send_now.py`.

## Verification

On the Mac, bounded: `npm run lint` (0 errors, old warnings only), `npm run typecheck`, `vitest run` on the 17 touched/related test files (314 tests), `ruff check .` and `ruff format --check .` (pinned 0.17.3), `pytest tests/test_google_signin.py tests/test_send_now.py` on a scratch Postgres 17 (122 passed). The full suites and the browser scripts run in CI or by hand against a running app; **not run on the Mac**: the `owner-e2e.mjs` and `owner-browser-check.mjs` scripts (updated, never executed), `next build`, and a real-browser look at `/` signed in. The unit tests prove: `/` renders the same landing words for both, the signed-in header has "Open the app" to `/meetings` and no Sign in, `/welcome` is a 308 to `/`, `/meetings` is noindex and in robots' disallow list, and the route lists agree.

## Operate / roll back

No server step: nginx's `location /` already sends every path to Next. Revert the PR to go back (`/` is Meetings for the owner again, `/welcome` the landing). After deploy, check signed in: `https://notes.beyrictech.com/` shows the landing with Open the app, which lands on `/meetings`; `/welcome` jumps to `/`; sign out, then sign in with a password and with Google and land on `/meetings`; an installed app (home-screen icon) opens `/meetings`. An already-installed PWA keeps its old `start_url` (`/`) until the browser refreshes the manifest, so it shows the landing for a while, one tap from the app.

## Follow-ups

- A copy of the old Google sign-in link (`return_to=/`) still lands on `/`, the landing, with Open the app: harmless, short-lived.
- `app/web.py` still has the retired Jinja dashboard at `/`; it is unreachable behind Next and goes with "Retire Jinja".
- The semgrep note on `api_accounts.py` (`tainted-redirect-fastapi`) is old: `return_to` is checked against `RETURN_TO_PATTERN` when the flow starts, so the redirect target is always a single-slash path.
