# SOP: Public landing, privacy and terms pages

## What shipped
When logged out, `/` is now a landing page. `/privacy` and `/terms` are public. Logged in, `/` is still the meetings page. Branch `feature/public-pages`.

## Why
Calendar auto-join needs Google OAuth. Calendar access is a "sensitive" scope, and in Testing mode Google expires each authorisation after 7 days, so the app has to pass Google's verification. That review needs a public home page that explains the app, plus a privacy policy covering Google user data (Limited Use). Adebayo approved building these first so verification can start early.

## How
- `app/auth.py`: `PUBLIC_PATHS = {"/login", "/privacy", "/terms"}`, and `/` is public for GET/HEAD only. POSTs to public paths still need the CSRF token.
- `app/web.py`: `index` renders `landing.html` when there is no session login. New `privacy` and `terms` routes. The privacy page takes the audio retention from `settings.audio_retention_days` (7), so the page can't drift from the code.
- `app/templates/public_base.html` (brand header, footer "© 2026 Beyrichq LTD", links), plus `landing.html`, `privacy.html` and `terms.html`. The app's `base.html` gets a Privacy / Terms footer.
- `app/static/app.css`: `body.public` styles with the brand colours from `docs/ui/Beyric Note Taker redesign/Brand.dc.html`, with a dark mode. No inline styles and no external fonts, because the CSP is `'self'` only. Geist wasn't added; the pages use the system font stack.
- **The text was adapted from the designer's drafts (`docs/ui/.../Privacy Policy.dc.html`, `Terms of Service.dc.html`) so every claim is true today.** Google reviewers compare the policy with the app. Changes:

| Designer draft | Shipped | Why |
|---|---|---|
| Beyric Technologies, [registered address] | Beyrichq LTD, Lagos, Nigeria | Adebayo |
| Google Meet, Teams and Zoom; Outlook | Google Meet and Zoom; Google Calendar only | Teams and Outlook aren't built |
| Audio deleted after [30] days | 7 days, from settings | `audio_retention_days = 7` |
| Encrypted at rest (AES-256), regular security reviews | TLS in transit, limited access, separate bot network | No disk encryption is set up |
| Analytics cookies, payment processing, error monitoring | One sign-in cookie; no analytics | None of these exist |
| Delete meetings, export, close account in the app | By email request | Not built yet |
| Backups removed within 90 days | Deleted after 14 days | `deploy/backup.sh` `KEEP_DAYS=14` |
| [primary hosting region] | France (Contabo) | IP lookup of 161.97.107.151 says FR; **confirm in the Contabo panel** |
| DPO [name] | Removed | No DPO named yet |
| Paid plans and renewals | Free pilot with an hours limit | Pilot: 10 accounts, 5 hours each |
| (none) | Google Meet "meeting started" access; no AI training on Google data | Planned instant-Meet auto-join; Google's AI clause |
| "Share with the room" feature | "Names spelled right" (vocabulary) | Sharing isn't built |
| "Get started free" / Create account | "Sign in", plus "private pilot" | Sign-up isn't built |

## What's involved
`app/auth.py`, `app/web.py`, `app/templates/{public_base,landing,privacy,terms,base}.html`, `app/static/app.css`, `tests/test_web.py` (the new public-page tests, and five login probes moved from `/` to `/templates`).

## Verification
- New tests: logged-out `/` is the landing page and shows no meeting data or form. Logged-in `/` is still the meetings page. All three public pages are 200 with no external resources and link to Privacy and Terms. The legal pages name Beyrichq LTD and have no `[` placeholders. The Limited Use statement is present. The retention days match settings. POST to the legal pages is refused. Owner paths still redirect.
- The full suite passes (327 passed); ruff check and format are clean.
- Not yet checked visually in a browser.

## Operate / roll back
Revert the PR. The legal text lives in the templates, so update the "Last updated" date with every change.

## Follow-ups
- Adebayo to confirm: that `privacy@`, `legal@` and `security@beyrictech.com` mailboxes exist; the hosting country; whether to name a DPO; a lawyer's read before launch.
- Each new feature (sign-up, sharing, Outlook, Teams, translation, analytics) must update the privacy policy in the same PR.
- Self-host Geist if the brand font matters.
