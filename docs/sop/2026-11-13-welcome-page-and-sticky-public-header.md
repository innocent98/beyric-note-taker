# SOP: `/welcome` (the landing page for everyone) and the sticky public header

**What shipped.** `/welcome` shows the landing page to everyone, signed in or not; the owner's way home from the public pages leads to it; and the landing header now really stays at the top while scrolling. Branch `feature/welcome-page-and-sticky-nav` (PR opened 2026-11-13, not merged by this work). Web only: no API, migration or server step, no FE guide change. The landing words and the legal pages are untouched.

## Why

`/` is the landing page for a visitor and the Meetings page for the owner (`web/app/page.tsx`), so a signed-in owner could never read the landing page, and from `/privacy` or `/terms` the logo led to their Meetings page. Adebayo chose a second address that always shows the landing page. Separately, the landing header was meant to be sticky (`position: sticky` was already there) but scrolled away: see the root cause below.

## How

| Piece | What and why |
|---|---|
| The page | `web/app/welcome/page.tsx` renders `LandingPage` (new, `web/components/landing/landing-page.tsx`: skip link, header, `Landing`, footer, JSON-LD; moved out of `app/page.tsx`, which now uses it too) with `signedIn` from `GET /api/session`. No redirect for anyone |
| Search | **Canonical `/`**, not `noindex`. A crawler is never signed in, so it reads the same page as `/`; the canonical folds the two into one. Not in the sitemap, and **not blocked in `robots.txt`**: a blocked page's canonical is never read. Canonical plus `noindex` is a mixed signal, so only the canonical. `/` itself is untouched for visitors |
| Old redirect | `next.config.ts` redirected `/welcome` to `/` (the review copy of the landing page lived there until stream C). It would have beaten the page, so it is removed. `/welcome` is added to `NEXT_PAGES` and to `proxy.ts`'s matcher (`tests/routes.test.ts` keeps them equal to `app/`); nginx's `location /` already sends it to Next |
| Signed-in header | Only when `signedIn`: the header and footer logo lead to `/welcome` (the page they are on), and the header's Sign in and Get started become one "Open the app" button (to `/`, their Meetings page), so the owner is never offered Sign in. A visitor's header is the designer's, unchanged (the hero's "Get started" buttons are landing copy and stay) |
| Public pages | `LegalShell` (privacy, terms; now an async server component reading the session) and `PublicShell` (the 404 page): the logo leads to `/welcome` for the owner, to `/` for a visitor as before. `Logo` already took an `href` |
| In-app link | **Left out on purpose.** The designer's file has no footer area or legal links inside the app (searched `docs/ui/`: the sidebar ends in the identity row and Log out, Settings has no footer). Per the brief nothing was invented. The owner reaches `/welcome` through the logo on `/privacy`, `/terms` and the 404 page, or by address. A link inside the app needs a design |
| Sticky header, root cause | `.ld { overflow-x: hidden }` made the landing wrapper a scroll container, and a sticky header sticks to its nearest scroll container, which here never scrolls: measured in a browser, scrolling 1500px moved the header to `top: -1500`. Now `overflow-x: clip` (the same cut, no scroll container; `hidden` stays as the line before it for a browser without `clip`). The privacy and terms header (`.lg-header`) was already sticking (measured `top: 0` after a 1500px scroll) |
| Anchors | `scroll-margin-top`: `.ld #main` 69px and `.lg #main` 65px (Skip to content), the landing sections 68 → 69px (header 68px + 1px border), the legal headings were already 72px. Measured: every landing section link and the terms contents list land just below the header. z-index: `.ld-header` 20, `.lg-header` 5, under the dialogs, drawers and toasts (40 to 60) |
| Not changed | Login and sign-up (`AuthShell`) have no header bar; their top row is part of the form column, so there is nothing to stick. The look of the headers is the designer's; only the sticking changed |

## What's involved

`web/app/welcome/page.tsx` (new), `web/components/landing/landing-page.tsx` (new), `web/app/page.tsx`, `web/components/landing/landing.tsx` (`LandingHeader` and `LandingFooter` take `signedIn`, default false), `web/components/site/legal-shell.tsx`, `web/components/site/public-shell.tsx`, `web/lib/routes.ts`, `web/proxy.ts`, `web/next.config.ts`, `web/styles/public.css`. Test: `web/tests/welcome.test.tsx` (13 cases: owner and visitor both get the landing page, same words as `/`; an owner's logo and header; a visitor's `/` unchanged; canonical, no robots tag, not blocked, not in the sitemap; the redirect gone; the public pages' logo for owner and visitor; the CSS guards: sticky and top 0 with a token background, z-index under every dialog, no scroll container around a sticky header, the scroll margins).

## Verification

On the Mac, bounded: `npm run lint` (0 errors, 3 old warnings), `npm run typecheck`, `vitest run` on `welcome`, `routes`, `parity`, `guards`, `meetings-calendar`, `meetings-recurring`. **Looked at in a real browser** (`next dev`, in-app browser): `/` and `/welcome` as a visitor: the header stays at `top: 0` after scrolling 2500px at desktop width and 1800px at 375px, no horizontal scroll at 375px, the four section links and Skip to content land under the header; `/terms`: header sticks, the first contents link lands under it, the logo leads to `/`; `/welcome` carries `<link rel="canonical" href="https://notes.beyrictech.com/">` and no robots tag. **Not looked at:** anything as a signed-in owner (the dev server had no backend, so the session was always "not signed in"; the owner variants are covered by the unit tests with a mocked session only), and dark mode.

## Operate / roll back

Nothing to run. Revert the PR to go back (`/welcome` redirects to `/` again). After deploy, check `https://notes.beyrictech.com/welcome` signed out (landing page, header unchanged) and signed in (landing page, "Open the app").

## Follow-ups

- A link to `/welcome` inside the app needs a designer's footer or menu entry.
- At 375px the landing header's "Sign in" and "Get started" wrap onto two lines (the designer's header, as it was): not changed here.
- A signed-in owner who clicks the hero's "Get started" goes to `/signup`: not checked what that page does for a signed-in session.
