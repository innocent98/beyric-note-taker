# Frontend approach: stay on FastAPI and Jinja, or move to Next.js?

**Status: SUPERSEDED the same evening. First decided 2026-11-02 to stay on Jinja; then Adebayo chose Next.js instead (see `2026-11-02-nextjs-migration-plan.md`). The text below is kept for the record.** Earlier status: stay on FastAPI and Jinja. Condition from him: **UI parity and UX flexibility are very important**; the plan for that is in "Keeping parity and flexibility" below. The comparison that led to it is kept as written. Written 2026-11-02 after he asked whether Python for the frontend is still right given the volume of UI, SEO/AEO/GEO, responsiveness, PWA, and brand parity. This replaces the earlier one-line decision ("server-rendered, not Next.js", handoff section 2), which was made before the team, tasks and sprints UI existed.

## Where we are today (counted from the repo, 2026-11-02)

| Thing | Size |
|---|---|
| Templates | 10 (`app/templates/`: base, public_base, landing, login, index, meeting, templates, vocabulary, privacy, terms) |
| Hand-written CSS / JS | 89 lines (`app/static/app.css`) / 41 lines (`app/static/app.js`: copy button, double-submit guard) |
| htmx / Alpine | not used yet |
| CSP | `'self'` only: no inline scripts, no inline styles, no external fonts (`app/main.py:53`) |
| Designed but not built | accounts and pilot, calendar and settings, team, tasks board, sprints, decisions log, agenda, sync, languages and translation, share and download (all in `docs/ui`, as `.dc.html` prototypes that use inline styles) |

**The switching cost is at its lowest right now.** Ten small templates are cheap to replace. Every screen built on Jinja from here raises the cost of changing later. That is the real reason to decide this week and not when the tasks board is half built.

## What each concern needs

| Concern | Needs | Jinja + htmx/Alpine | Next.js (App Router) |
|---|---|---|---|
| SEO, AEO, GEO | Real HTML in the first response (most crawlers and AI engines don't run JS); sitemap, robots, JSON-LD, OG tags, canonical URLs | Easy and already how it works | Equal. Server components, `generateMetadata`, `sitemap.ts`. No advantage |
| Where SEO counts | Only public pages: landing, privacy, terms, later pricing and blog. The logged-in app is invisible to search; shared-note links should be `noindex` | Fine | Fine |
| Responsive | CSS | Same | Same |
| PWA | Manifest, icons, service worker, install prompt. Offline use of authenticated pages is hard in any stack | Hand-written service worker; fine | Same, with a library such as Serwist. Small edge |
| Brand and look parity | One set of tokens and one set of components, used everywhere | **Weak.** No component model. Parity comes from discipline and partials/macros. The designer's output must be rewritten from inline styles into tokens either way | **Strong.** Components, tokens and a UI kit (for example shadcn/ui) in one tree, typed props |
| Volume of UI | Many screens, shared pieces, forms, state | OK for forms and lists. Gets heavy for boards | Built for this |
| Rich interaction | Tasks board with drag and drop, sprint planning, inline edit, filters, optimistic updates, live status | **Strained.** htmx swaps HTML; board state and optimistic updates become hand-rolled JS | Natural (React state, `dnd-kit`, a data library) |
| Our strict CSP | No inline script or style | Already satisfied | Next injects inline scripts for hydration. Needs nonces and `strict-dynamic`; doable but real work, and must be tested |

## Options

| | What | Cost | Risk |
|---|---|---|---|
| **A. Stay on Jinja** | htmx and Alpine for the interactive bits; boards written as hand-rolled JS | Lowest now | Tasks, sprints and parity screens get expensive and fragile; may be rewritten later at a higher cost |
| **B. Jinja plus islands** | Jinja for pages; the tasks and sprints screens become small client-side components (Preact or Svelte) served as static files | Medium | Two UI styles inside one app; parity needs care; a build step for the islands anyway |
| **C. Next.js for the whole frontend** | FastAPI becomes a JSON API (plus the Pub/Sub and webhook endpoints); Next.js serves every page, public and logged-in | Highest now: rebuild 10 templates, add an auth bridge, a second deploy unit | A second runtime to run on a shared 7.8 GB server; two test suites; API types kept in sync |
| **D. Next.js for the app, Jinja for public pages** | Mixed | High | The worst for parity: two component sets for one brand. Not recommended |

## What option C would involve (the part that is easy to underestimate)

- **Auth:** the owner login and (soon) accounts are FastAPI cookie sessions. Next would call FastAPI server-side and forward the cookie, or FastAPI stays the single front door and reverse-proxies page requests to Next. Either way, same origin so cookies and the CSP keep working.
- **Routing:** nginx (Adebayo's to change; `sudo` needs his password) or FastAPI sends `/api/*` and the webhook endpoints to FastAPI and everything else to Next.
- **Deploy:** a second image and compose service, built and restarted by `deploy/deploy.sh`. Memory use of a Next server on the shared box has **not been measured**.
- **Contract:** FastAPI needs JSON endpoints for everything the templates now render. The FE integration guide rule in `~/.claude/CLAUDE.md` then applies again (captured, real responses), which is useful but is work.
- **Rewrite scope today:** the 10 templates, the CSS and the JS. Small. The public pages must keep the same truthful content (see the public-pages SOP) so Google verification is not disturbed.

## Decision

Stay on Jinja (option A, with option B's islands kept as an escape hatch for the tasks board). Next.js is not rejected for ever: the open items below are what would reopen it, and the plan below is built so that a later move is a swap of the rendering layer, not a rewrite of the backend.

## Keeping parity and flexibility

**Parity debt today.** The logged-in app and the public pages already look different: `app/static/app.css:2` styles the app with system-ui and a blue accent (`#1d4ed8`), while the public pages override to the brand teal (`#0E6B5E`) through a separate `body.public` block (`app.css:45`). Geist is not loaded. Closing that gap is the first job, before new screens multiply it.

| Rule | How it is enforced |
|---|---|
| **One source of tokens** | A single `app/static/tokens.css` holds colour, type (Geist), spacing, radius, shadow, in light and dark, taken from the designer's prototypes. The public/app split disappears; one theme, used everywhere |
| **Components, not page styles** | Each reusable piece (button, badge, card, field, segmented control, menu, dialog, tabs, list row, empty state, toast) is a Jinja macro in `app/templates/components/` with its CSS in `components.css`. Screens compose macros; a new look is added to the macro, never to one page |
| **Self-hosted font** | Geist files in `/static/fonts/`, so the strict CSP (`'self'` only) stays untouched |
| **A living style guide** | An owner-only, `noindex` page (`/styleguide`) renders every component in every state, light and dark, at phone width. It is the parity reference and the thing the designer reviews |
| **Cheap automatic guards** | A test that fails on a hard-coded colour outside `tokens.css`, an inline `style=` attribute, or a macro missing from the style guide. Runs with the normal unit tests. Visual screenshot comparison against the designer's prototypes is done locally per screen, not in CI (heavy, per the CI rules) |
| **Designer flow** | A new designer screen is first mapped to existing components. A missing component is added to the style guide first, then used. The `.dc.html` prototypes stay the visual reference |

**UX flexibility**

- **Progressive enhancement:** every page works as plain HTML and forms; htmx and Alpine add inline edit, filters, menus, optimistic updates. Fast to build, and works without JS.
- **Fragment endpoints:** any interactive widget is backed by a route that returns an HTML fragment. A screen that outgrows htmx (the tasks board) can then be replaced by a small client-side island, or later by another front end, without touching business logic.
- **Escape hatch:** islands are served as static files (no new runtime). The decision to use one is made per screen, when it is scheduled.
- **To verify in a spike before relying on them (not yet checked):** htmx and Alpine under the strict CSP. Alpine's standard build uses `eval`, so the CSP build (`@alpinejs/csp`) is needed, and htmx's built-in indicator `<style>` must be switched off (`includeIndicatorStyles`). Both libraries are self-hosted in `/static`.
- **PWA, responsive, SEO/AEO/GEO:** manifest, icons and service worker are static files; layouts are mobile-first CSS; the public pages keep server-rendered HTML with JSON-LD, sitemap, robots, OG tags and `llms.txt`, and shared-note links are `noindex`.

**Order of work.** A "UI foundation" PR comes before accounts: tokens, Geist, base layout, the core macros, the style guide and the two guard tests, then the existing 10 templates moved onto it. That brings the logged-in app to the brand straight away and gives every later screen (accounts, calendar, team, tasks, sprints) the same parts. Tracked in the checklist as Module 2a.

## Recommendation made at the time (kept for the record)

1. **Decide before building the Team, Tasks and Sprints screens** (handoff section 6 item 5), and ideally before accounts, since that is when the screen count starts to grow.
2. If those screens are going to be built soon and as designed, **choose C**, because the volume, the interactive board, the PWA and the one-brand-one-component-set requirement all point the same way, and the cost is lowest now. Do it in phases so the live pilot is never down:
   - Phase 0: stand Next.js up next to the current app (new compose service, same origin, no traffic yet) and rebuild the public pages there first. They are the lowest-risk pages and the SEO surface.
   - Phase 1: login and the existing meeting screens.
   - Phase 2: accounts and calendar, then team and tasks, built natively.
   - The Jinja app keeps serving everything not yet moved. Remove it last.
3. If the tasks board is far away or may be cut, **choose A**, and revisit when it is scheduled. Option B is a fallback if C's operating cost on the shared server turns out too high.
4. **Do not choose D.**

I lean towards C, with the honest caveats that it costs more now, it adds a runtime to a shared box, and the open measurements below could change that.

## Open items that would reopen the decision

- [ ] Measure a Next.js production container's memory on `haven-respite` (current free headroom unknown; Vexa alone is capped at 3 GB).
- [ ] Confirm the CSP can be kept strict with Next (nonce-based) in a small spike.
- [ ] Decide who builds the UI components (the designer's `.dc.html` files are prototypes; they need turning into components and tokens either way).
- [ ] Timing: how soon do the Team, Tasks and Sprints screens need to ship?
- [ ] Whether the Google OAuth verification timeline allows a front-end migration in parallel (the public pages must stay live and accurate throughout).
