# SOP: UI foundation (one design system for every screen)

## What shipped
Design tokens, self-hosted Geist, a set of shared Jinja components, an owner-only style guide, and guard tests. All 10 existing templates now use them, so the logged-in app, the login page and the public pages look like one product. Branch `feature/ui-foundation`. Spec and decision: `docs/superpowers/specs/2026-11-02-frontend-approach.md` (Adebayo, 2026-11-02: stay on Jinja; UI parity and UX flexibility are conditions).

## Why
The logged-in app used the system font and a blue accent while the public pages used the brand teal through a separate override, and Geist wasn't loaded anywhere. Every new screen (accounts, calendar, team, tasks, sprints) would have widened that gap. Switching cost was lowest before those screens existed.

## How
| Piece | Where | Notes |
|---|---|---|
| Tokens | `app/static/tokens.css` | Colour, type, spacing, radius, shadow; light and dark. Values from the designer's `Brand.dc.html` and app screens. The only file allowed to hold a colour literal |
| Components CSS | `app/static/components.css` | App shell (232px sidebar on desktop, a top bar under 860px), buttons, fields, cards, badges, alerts, lists, segmented control, tabs, menu, dialog, auth layout, style-guide helpers |
| Page CSS | `app/static/app.css` | Only the public landing and legal layouts; tokens only |
| Components | `app/templates/components.html` | Jinja macros: logo, button, badge, alert, card, csrf_field, field, select_field, textarea_field, segmented, menu, menu_item, tabs, nav_link, list_row, empty_state, page_head, dialog |
| Layouts | `base.html` (sidebar shell when logged in, centred auth card when not), `public_base.html`, `head.html` (shared head) | |
| Fonts | `app/static/fonts/` (Geist and Geist Mono variable, Latin and Latin-extended; SIL OFL licence included) | Taken from the `@fontsource-variable` npm packages |
| Style guide | `GET /styleguide` (`app/web.py`), `templates/styleguide.html` | Owner only; `X-Robots-Tag: noindex, nofollow` and a robots meta |
| Interaction | `app/static/app.js` | Added: `<dialog>` open/close, menu closes on outside click or Escape. Pages still work without JS |
| CSP | `app/main.py:18` | Added `font-src 'self'`. Without it `default-src 'none'` blocks the fonts. Still no `unsafe-inline` |

Decisions: pure CSS and native elements (`<dialog>`, `<details>`, radio inputs) rather than a JS library, so the strict CSP stays and pages work without JS; dark mode follows the system and reuses the public pages' dark values; the app copy was left unchanged (the designer's button labels such as "Send note taker" differ from ours: align with the features that need them).

## Verification
- `tests/test_ui_guards.py` (no database): no colour literals outside `tokens.css`; no inline `style=` or `on*=` in templates; every macro appears in the style guide; both base layouts include the shared head.
- `tests/test_web.py`: `/styleguide` needs a login and is `noindex`; every layout links the three stylesheets and the font; fonts and favicon are served; CSP has `font-src 'self'` and no `unsafe-inline`; the sidebar marks the current page. Full suite: 337 passed, plus these.
- Checked by eye in the browser pane against a local server with sample meetings: login, Meetings, a ready meeting and a processing meeting, the style guide, and the landing page; light and dark, desktop (1280) and phone (375). Console had no errors, Geist loaded under the CSP, and the dialog and menu worked. Not yet checked: Templates, Vocabulary, Terms and Privacy by eye on a phone; Safari and Firefox; the failed-meeting page.

## Operate / roll back
No migration or config. Static files are cached by browsers after the first load, so a visitor may need one refresh after deploy. Roll back by reverting the PR; nothing else depends on it.

## Follow-ups
- Spike htmx and Alpine (CSP build) under the strict CSP before using them; add a toast component with the first feature that needs it.
- PWA basics: manifest, icons, service worker.
- Align copy with the designer's screens as each feature is built (Send note taker, subtitles, empty states).
- Review the mobile top bar (nav wraps to two rows with the logo).
- Local screenshot comparison against the `.dc.html` prototypes per new screen is manual; there is no visual regression in CI by design.
