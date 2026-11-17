# SOP: one public header and one public footer

**What shipped.** Every public page (the landing page at `/` and `/welcome`, `/pricing`, `/privacy`, `/terms`, the 404 page) now wears the same header and the same footer, built once. `/pricing` no longer says hours are shared by the whole team. Branch `feature/public-shell-unified`; PR #224.

## Why

Adebayo looked at `/pricing` and saw a header and footer unlike the other public pages, and incomplete. There were three implementations: the landing header/footer (`LandingHeader`, `LandingFooter`), the pricing page's own one-line `PricingFooter` with a two-link header, and the small `lg-header`/`lg-footer` of the legal pages (also used by the 404 page). His decision: ONE header and ONE footer for all public pages, and adding public pages later must cost nothing (he may split Privacy and Terms into separate pages).

Which one is the designed one: the designer's second Landing file (2026-11-14, as recorded in `docs/sop/2026-11-14-landing-v2-and-pricing.md`) draws the header (logo, Product, Who it's for, Pricing, Security, Sign in, Get started) and the three-column footer (brand line, Product, Company, entity and address). The legal pages' tab header and the pricing one-line footer were our own reductions of it (the legal file has its own small header; the pricing file draws a one-line footer). The landing pair is the fullest, carries every link the others have (Pricing, Privacy, Terms, Contact) and is the one the visitor meets first, so it is the one kept. The pricing design file itself sits uncommitted in Adebayo's checkout and was not read (instruction); the pricing header and footer were taken from the existing SOP's description of it.

## How

| Decision | Detail |
|---|---|
| One layout | Route group `web/app/(public)/` with `layout.tsx` that wraps its pages in `PublicShell`. `welcome`, `pricing`, `privacy`, `terms` moved into it (URLs unchanged). A new public page = a folder in the group. |
| Two pages wrap themselves | `/` (also the owner's Meetings page, so it cannot live in the group) and `app/not-found.tsx` (a layout does not wrap it) use `<PublicShell>` directly: same component, same output. |
| Parts | `components/site/public-header.tsx`, `public-footer.tsx`, `public-shell.tsx` (skip link, header, `<main id="main">`, footer), `public-links.ts` (home and section addresses, Contact), `public-nav-link.tsx` (client; marks `aria-current="page"` from the pathname, so a page added later is marked with no code). |
| Links work from every page | Section links are built on the visitor's home: `/#product`, `/#teams`, `/#security`, `/#features`; for the signed-in owner `/welcome#product` and so on. Pricing, Privacy, Terms, Contact are the same everywhere. |
| Kept behaviour | `getSession` (GET /api/session): "Open the app" for the owner, Sign in and Get started for everyone else, FastAPI unreachable included; owner's logo to `/welcome`, visitor's to `/`; skip link; sticky header (`.ld-header`); copyright `LEGAL_NAME`; Contact `mailto:privacy@beyrictech.com`. |
| Legal pages | `components/legal/legal-page.tsx` (was `LegalShell`) now draws only the "On this page" list and the article; the landmarks come from the shell. The "Questions about this page" line stays. |
| Page content only | `LandingPage` is now JSON-LD plus `Landing`; `Pricing` is the sections only; `/pricing` and `/welcome` no longer read the session themselves. |
| `.ld` is a column | The shell root keeps the footer at the bottom of a short page (the 404). The legal body took the legal font size and line height on `.lg-body`. |

Rejected: marking the current page per page with a prop (every new page would need wiring; the client link does it from the address). Putting `/` in the group with a redirect (changes the owner's app). Keeping the legal tab header (it would be a second header).

Two small visual points, both kept equal to before this change: the footer links keep their old hover (a hover rule added at first was removed, because it changed `/`); the legal pages' links keep their hover underline (`.lg-article a:hover`), which `.ld a { text-decoration: none }` would otherwise have removed.

Header on a phone: the four links hide below 720 px as before (SOP D34), Pricing stays reachable from the footer; below 480 px the logo drops "Note Taker".

## Removed (dead code)

`LandingHeader`, `LandingFooter`, `PricingFooter`, `PricingPage`, `LegalShell`, the old `PublicShell` body, and the CSS `.lg`, `.lg-header*`, `.lg-tabs*`, `.lg-footer*`, `.ld-pfooter*`, `.lg #main`. A test (`public-shell.test.tsx`) fails if any returns.

## Pricing wording (decided model: per person)

`web/app/(public)/pricing/page.tsx` description and `web/components/pricing/content.ts`: hours "each month, for you"; "Extra-hour packs when you need more"; table row "Hours shared across the team" is now "Hours belong to the person" (Yes on every plan); FAQ "How are meeting hours counted?" says the hours belong to the person who has the plan, a team is only a place to share notes, teammates read shared notes without a plan of their own, packs add more. No price, no hour number printed. Left as they were: plan names Team / Team Plus and their taglines (names are open per the billing spec section 10), the hero "Pay when your team runs on it."

Other places that said team-shared hours: none in `web/` or `app/` code (checked by search). Docs only:

| File | State |
|---|---|
| `docs/sop/2026-11-14-landing-v2-and-pricing.md:12` and `:87` (row P10) | corrected with a dated note |
| `docs/checklist/master-checklist.md:976` (S11) | corrected |
| `docs/sop/2026-11-13-welcome-page-and-sticky-public-header.md` (paths, `LegalShell`, `.lg #main`) | updated with dated notes |
| `docs/superpowers/specs/2026-11-14-public-pages-audit.md:159,246`, `docs/HANDOFF-master.md:134,136`, `docs/superpowers/specs/2026-11-15-billing-and-plans-design.md:562,565` | mentioned, not changed (history; the lead handles the handoff and the spec) |

## Files

`web/app/(public)/{layout,welcome/page,pricing/page,privacy/page,terms/page}.tsx`; `web/app/page.tsx`; `web/components/site/public-*.ts(x)`; `web/components/legal/legal-page.tsx`; `web/components/landing/{landing,landing-page}.tsx`; `web/components/pricing/{pricing.tsx,content.ts}`; `web/styles/public.css`. Tests: `web/tests/public-shell.test.tsx` (new), `public-render.tsx` (helper), `welcome`, `landing-v2`, `parity`, `routes`. No sitemap or robots change (same four paths; `/welcome` stays out of the sitemap). `proxy.ts` and `NEXT_PAGES` unchanged: a new public page still needs its path added there (`tests/routes.test.ts` enforces it).

## Verification

- Local: `npm run lint` and `npm run typecheck` (as CI's web job runs them) clean apart from three old warnings in `scripts/parity-meeting-delete.mjs`; tests of the touched files and the guards, legal and security-copy tests: 8 files, 96 tests green on the first pass; after the review fixes the six touched files (88 tests) are green again. Not run locally: the full suite and `npm run build` (CI runs them).
- `public-shell.test.tsx`: each of the six pages, as visitor and (for the five public ones) as owner, has header and footer equal to the shared shell's; one `header`, `footer`, `main`, `nav`; skip link first; exact header and footer link lists for both variants; Sign in with no session; section links start with the home path; `aria-current` on Pricing, Privacy, Terms and nowhere else.
- Dev server (`next dev`, FastAPI not running, so visitor view only): `/`, `/welcome`, `/pricing`, `/privacy`, `/terms` returned 200 with one header, footer and main each, and the right link marked current. Screenshots at 1280 and 390 px in the session's notes folder. The 404 page was drawn through a throwaway page that calls `notFound()` (removed). - Second pass (2026-11-17, dev server plus a stand-in for `GET /api/session` returning `{"authenticated": true}`, then FastAPI down): the owner's header on `/pricing` reads "Open the app" with the logo and section links on `/welcome`; clicking "Security" from `/pricing` and "Who it's for" on `/welcome` both land the section at 69 px, just under the header; at 390 px the footer's "Security" link on `/pricing` leads to `/#security` at 69 px with no sideways scroll; the legal link underlines on hover; a visitor with FastAPI down sees Sign in and Get started. The markup is also pinned in a test against fixed strings (header and footer, visitor and owner), and every page is checked for exactly one `h1`.
- Not checked: a real phone, the live site, dark mode (the public pages have none), a production build outside CI.

## Operate / roll back

Nothing to run or migrate. Revert the PR to go back to the three implementations.

## Follow-ups

- Answered yes for the sign-in and sign-up pages (`AuthShell`): see `docs/sop/2026-11-17-auth-shared-header.md`. `web/app/error.tsx` stays outside (client error boundary).
- Nit: a visitor on `/welcome` has "Product" lead to `/#product` (leaves the page) instead of scrolling in place; the content is identical.

- Split Privacy and Terms into separate pages later: add folders under `app/(public)/`, a `NEXT_PAGES` and proxy matcher entry, a sitemap line.
- Plan names and taglines still say "Team" under a per-person model (billing spec section 10).
- The 404 page is only reached through `notFound()`; unknown URLs still show FastAPI's JSON (already on the checklist).
