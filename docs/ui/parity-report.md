# UI parity report: Next.js frontend against the designer's files

**Date:** 2026-11-03. **Branch:** `feature/nextjs-ui-parity`. **Against:** `docs/ui/Beyric Note Taker redesign/` as merged in PR #23 (Auth, Landing, Brand, Emails, logo files). SOP: `docs/sop/2026-11-03-ui-parity.md`.

**Update, stream D (accounts pages, branch `feature/stream-d-auth-pages`, SOP `docs/sop/2026-11-03-accounts-auth-pages.md`):** the account screens that have a backend now exist and follow `Auth.dc.html`: sign-up, confirm email, forgot and reset password, "pilot full", and the sign-in form with email, Forgot password? and Google. The tables below are updated; the section "Account screens (stream D)" has the screen-by-screen account. The screens without a backend stay unbuilt (workspace setup, team invite, Microsoft, keep me signed in, the 6-digit code).

**Update (2026-11-04, branch `feature/settings-sections-recurring`, SOP `docs/sop/2026-11-04-settings-sections-recurring.md`):** the Settings sections below Calendar (Sprints and reminders, Tasks, Default notes language) and the Recurring cards on Meetings are now drawn as designed. The three Settings sections are drawn with every control off (their features are not built); the Recurring cards come from the calendar's recurring events and have no tasks or questions chips and no link. In the "Out of scope" table below, the rows for the Settings screens and the Recurring cards are therefore partly built: see that SOP for the parity table and the gaps.

Adebayo looked at https://notes.beyrictech.com after the Next.js front door and the owner screens went live and said it was not in parity. This report lists what differed (screen by screen), what was changed, what was left out on purpose, and what cannot be built until the backend exists.

## How it was checked

- Every designer file was opened in a real Chromium (the `.dc.html` files are served read-only with `python3 -m http.server`) and the matching Next page was opened beside it, at **1440, 768 and 390 px**. The prototype of the main app is one stateful page, so its screens were reached by clicking as a person would.
- Positions and computed styles of the same labels on both sides were diffed (font size, weight, colour, padding, radius, position), then differences were fixed in the tokens and components, not as one-off overrides.
- `web/scripts/parity-shots.mjs` repeats the screenshots (design left, Next right, one JPEG per screen and width). `web/scripts/mock-api.mjs` replays the captured API responses so the owner screens render without Docker.
- The **built** app (`next build`, standalone server) was what the "after" screenshots show, not the dev server.
- Hard numbers: the landing page is **4806 px tall at 1440 and 5789 px at 768** in both the prototype and the Next page. Section heights, type sizes and colours line up from header to footer.

## Evidence

| Screen | Before (1440) | After (1440, 768, 390) |
|---|---|---|
| Landing | `docs/ui/parity/before/landing-1440.jpg` | `docs/ui/parity/after/landing-{1440,768,390}.jpg` |
| Sign in | `before/signin-1440.jpg` | `after/signin-{1440,768,390}.jpg` |
| Privacy | `before/privacy-1440.jpg` | `after/privacy-{...}.jpg` |
| Terms | `before/terms-1440.jpg` | `after/terms-{...}.jpg` |
| Meetings | `before/app-meetings-1440.jpg` | `after/app-meetings-{...}.jpg` |
| Meeting (ready) | `before/app-detail-1440.jpg` | `after/app-detail-{...}.jpg` |
| Templates | `before/app-templates-1440.jpg` | `after/app-templates-{...}.jpg` |
| Vocabulary | `before/app-vocabulary-1440.jpg` | `after/app-vocabulary-{...}.jpg` |
| Meeting states with no prototype (processing, waiting for the bot, failed, not found) | | `after/app-detail-processing-*`, `app-detail-waiting-*`, `app-detail-failed-*`, `app-meeting-missing-*` |

Each image has the designer's render on the left and the Next page on the right. The "before" images were taken from the live code on `main` (`2c26f20`) before any change.

## Which file wins

| File | Used as |
|---|---|
| `Brand.dc.html` | The token source: palette, logo, type |
| `Auth.dc.html`, `Landing.dc.html` | Sign-in and landing (they replace `Auth v1` and `Landing v1`, which are ignored) |
| `Beyric Note Taker.dc.html` | The app shell and the five owner screens |
| `Privacy Policy.dc.html`, `Terms of Service.dc.html` | Layout only; the words are the live legal text (see "Left as it is on purpose") |
| `Logo Export.dc.html` and `logo/*.png` | Favicon, PWA and Apple icons, share image |
| `Logo Options.dc.html` | Reference (the options the designer considered); not used |
| `Emails.dc.html` | Out of scope (needs backend), see below |

Two facts about the designs shaped the work. **The designs are light only**: no file draws a dark theme, so the dark overrides stream A had added were removed (a visitor on a dark OS saw a page the designer never drew). **The main-app prototype is not responsive**: at 390 px it crushes its own content (see `docs/ui/parity/after/app-templates-390.jpg`, left side), so the phone layout of the app (top bar, stacked panes) stays the engineered one from stream C, restyled with the new tokens. Landing, sign-in and legal pages are responsive in the prototype and follow it.

## In scope: every screen that exists today

| Screen | What differed before | What was done | Status |
|---|---|---|---|
| **Logo, favicon, icons** | The old speech-bubble mark in the app, `app/icon.svg`, PWA icons, Apple icon; no share image | New "b" mark (`components/ui/logo.tsx`, one SVG, three sizes 26/28/30, dark variant); favicon is the 16 px variant (no centre dot, heavier stroke, as Brand says); icons and `public/brand/` copied or resized from the designer's exports; `public/og.png` (1200x630) with Open Graph and Twitter large-image metadata on every page (`scripts/make-icons.sh`) | Matches |
| **Tokens and theme** (`styles/tokens.css`) | Dark overrides; success, warning and danger colours differed from the designs (`#1A7F37` against `#166534`, `#FEF3F2` against `#FDECEA`); no tokens for the inks (`#2E3532`, `#4A524E`), soft borders (`#EDF0EE`, `#F0F2F1`), washes, avatar palette, landing night palette | Light only; every colour the designs use is a named token (nothing is a one-off override); focus is the designer's teal border plus 3 px ring; `lib/brand.ts` and its guard test follow | Matches |
| **App shell and sidebar** | Active link was teal text on a teal tint (design: `#EEF1F0` with ink text); inactive links `#5B6460` (design `#4A524E`); link height 39 px (design 36); a Privacy/Terms footer the design does not have; old logo | Colours, line height and logo as drawn; the footer is removed (the links stay on the landing, sign-in and legal pages) | Matches, except the items in "Needs backend" |
| **Meetings** | Field label "Meeting link (Google Meet or Zoom)" (design: "Meeting link"); note text `#6E7772` (design `#5B6460`); list dividers `#E3E7E5` (design `#EDF0EE`), row hover, chevron and separator greys | Label and colours as drawn; the platforms are named in the placeholder instead; rows use the soft dividers and `#F8FAF9` hover | Matches, except the items in "Needs backend" |
| **Meeting (ready)** | Back link sat 4 px above the title (design 20); side cards padded 20 (design 18); rewrite card gap 12 (design 10); select 40 px (design 36); tabs teal text (design ink text, teal rule); notes were plain bullets; participants avatar palette; "Please check" had no box | Spacing as drawn; **notes are split into their parts** (`wrapSections` in `components/app/notes-html.tsx`): check marks for Decisions, quiet dots for Open questions, the grey "Please check" box, 5 px teal bullets, 26 px between parts; compact select and card; transcript avatars use the design's five pairs | Matches, except the items in "Needs backend" |
| **Meeting states with no prototype** (waiting, processing, failed, not found) | Used the old tokens | Use the designer's empty-state card (44 px tile, 16 px title, `#5B6460` text) with the new tokens | Matches the designer's empty-state pattern; the words are the app's (they describe real states the prototype does not have) |
| **Templates** | Add template and Save were 40 px / 14 px (design 36 / 13); page stack gap 28 (design 24); template rows 61 px (design 56); selected row colours | `btn-md`, `stack-md`, `line-height: normal` on the rows, selected row `#EEF5F3` with `#0A5248` name | Matches (the "Delete template" button was added 2026-11-04: `docs/sop/2026-11-04-template-delete.md`) |
| **Vocabulary** | The whole page was centred in a 720 px column (design: left-aligned, content 720 px wide); count line and dividers | Left-aligned 720 px measure, soft dividers, `#FAFBFA` count line | Matches |
| **Sign in (`/login`)** | A centred card on grey with a different mark and title | The prototype's layout: form on the left (logo, "Welcome back", password with Show / Hide, Sign in button 46 px, footer links), teal panel on the right with the product preview; the panel drops below the form on a phone | Matches (see "Account screens (stream D)" for the form's later additions) |
| **Landing (`/`)** | A different page: centred hero, pilot wording, three steps | `Landing.dc.html` section by section: sticky header, hero with the product window and floating cards, tools strip, three "how it works" cards, dark "Beyond the notes" section, security grid, FAQ (native `<details>`, one open at a time, no JavaScript), teal closing band, footer. Words are the designer's (one data file, `components/landing/content.ts`) | Matches (see "Copy that runs ahead of the product") |
| **Privacy and Terms** | No header tabs, no "On this page", a small type scale | The prototype's frame: sticky header with Privacy / Terms tabs, sticky "On this page" list (ids from the real headings, test-enforced), 40 px title, 17 px introduction, 20 px section titles, teal bullets, "Questions about this page?" box, footer | Matches the layout; the words are the live legal text |
| **404 and error pages** | Used the old header and an auth card | Built from the legal header and footer and the empty-state card | No prototype; matches the design language |
| **Style guide** (`/styleguide`, owner only) | Showed the old logo and dark theme wording | Shows the new variants (button sizes, dark logo, teal band button, large field, compact select, small badge, new swatches) | Guard test keeps it complete |

## Left as it is on purpose

| Where | Designer's version | Shipped | Why |
|---|---|---|---|
| Legal text, privacy and terms | Sample wording ("Beyric Technologies", "[registered address]", calendar and sharing sections) | The live text: Beyrichq LTD, Lagos, pilot terms, the Google user data section, retention days from `AUDIO_RETENTION_DAYS` | Google compares the policy with the app and the entity is Adebayo's decision. `tests/legal.test.tsx` still compares the text with the Jinja templates |
| Entity in footers | "Beyric Technologies" | Beyrichq LTD (`LEGAL_NAME`) | Same decision |
| Contact and Help mailbox | `hello@beyrictech.com` | `privacy@beyrictech.com` | The handoff lists `privacy@` as the confirmed mailbox; `hello@` is not confirmed |
| Phone header of the landing page | The section links wrap and clip at 390 px | The links hide below 720 px (the footer repeats them) and Sign in stays | The prototype's header breaks on a phone |
| Phone layout of the app | Not responsive (crushed at 390) | Top bar and stacked panes from stream C, new tokens | See "Which file wins" |
| Meeting link placeholder | "Paste a Google Meet, Microsoft Teams or Zoom link" | "Paste a Google Meet or Zoom link" | Teams links are refused by the backend |
| Status words | Ready, Failed, Joining, Recording | The API's own (Ready, Failed, Bot sent, step names) | They are real states of the pipeline |
| Landing buttons that lead to sign-up | "Get started", "Get started free", "Create your account", "Create account" | Not drawn on the landing page; the hero and closing band say "Sign in". The sign-in page now has "Create account" | Sign-up is closed; the landing page is not part of stream D |
| "See the app" | A link to the prototype file | Not drawn | It is not a page |

## Account screens (stream D)

Checked in a real Chromium-based browser pane at 375 and 1280 px against `web/scripts/mock-api.mjs`, which replays the responses captured from the running API (`docs/fe-captures/pilot-accounts/`). Against `Auth.dc.html` (the source of truth). Sign-up is **closed** in the real pilot, so `/signup` draws the closed screen by default.

| Screen (route) | Designed | Built | Differences from the design, and why |
|---|---|---|---|
| Sign in (`/login`) | Google and Microsoft buttons, "or sign in with email", work email, password with "Forgot password?", "Keep me signed in for 30 days", "New to Beyric? Create account" in the header | Google button (only when `GET /api/pilot` says `google_signin: true`), the divider, email, password with Show / Hide and "Forgot password?", "New to Beyric? Create account" (leads to the closed or full screen while the pilot is closed or full). `?error=<code>` shows a message for `google_denied`, `google_failed`, `terms_required`, `pilot_closed`, `account_suspended`, `email_unsupported`; any other code gets a general line | **No Microsoft button** (no backend), **no "Keep me signed in"** (the session lifetime is not optional). The Google button is full width (one button, not two columns). A wrong password adds "If you have just signed up, confirm your email first", because the API answers an unconfirmed account like a wrong password |
| Create account (`/signup`) | Google and Microsoft, full name, work email, password with a 3-rule meter (8+ characters, a number, an uppercase letter), "By creating an account you agree to..." sentence | Google (when available), full name, work email, password, a **Terms and Privacy checkbox** (the API needs `accept_terms: true`; Google sign-up sends `accept_terms=true` only once it is ticked), "Create account" | **Password rules:** the designed meter says a number and an uppercase letter are required; the API requires neither (8 to 128 characters, not a common password), so the screen shows one live rule ("8 to 128 characters") and the note "Not a common password" (only the server has the list). The terms sentence became the checkbox the API requires |
| Pilot closed and pilot full (`/signup`, `/pilot-full`, after a 403 `pilot_closed` or 409 `pilot_full`) | not drawn | Icon tile, title ("Sign-up is not open yet", "The pilot is full"), one line (the API's own words), the waiting-list form (email, optional name) pre-filled from `prefill_email` or the address typed, then "You are on the list" | Built from the design's pieces; there is no prototype to compare with |
| Check your email after sign-up (design: "Verify email", a 6-digit code) | six code boxes, "Resend code" after 30 s, "Change it" | "Check your email", "We will email a confirmation link to...", Open Gmail / Open Outlook, "Did not get it? ... resend in 1:00" (60 s: the server drops a second mail inside a minute), "Change it" | **No code boxes**: the API confirms with a link and the password, not a code. The words never say whether the address was already registered |
| Confirm email (`/verify-email?token=`) | none | Password field ("the password you chose when you signed up"), "Confirm email", then "Email confirmed" and "Go to sign in"; a dead link shows "This link does not work" with a "Send a new link" form; a full pilot shows the pilot-full screen and says the link was not used up | No prototype. A GET never confirms (mail scanners open links): the button posts |
| Forgot password and "check your email" (`/forgot-password`) | Reset your password; Check your email with Open Gmail / Outlook and "resend in 0:30" | As drawn, with the 30-minute expiry in the text and a 60 s resend wait | Resend waits 60 s, not 30 |
| New password and "Password updated" (`/reset-password?token=`) | New password with the 3-rule meter, confirm, "Update password", "Password updated" | As drawn; one rule instead of three (see sign-up); the sub line says "You will be signed out on every device" (the API signs the user out everywhere); "For {email}" is dropped because the link carries only a token; a dead link shows "Ask for a new link" | |

**Not built, no backend:** "Set up your workspace" (workspace name, calendar connect), "Join your team" (team invite acceptance), the Microsoft buttons, "Keep me signed in", the 6-digit email code.

**Two small layout fixes made on the way:** the sign-in header no longer wraps the logo and the link on a 375 px screen (the sentence before the link hides below 480 px), and the Show / Hide button fills the field's height on a touch screen (it sat low).

**Safety:** every account form has `method="post"`, so a submit before the page is interactive can never put a password in the URL (the guard test checks every form, the sign-in form included).

## Copy that runs ahead of the product

The landing page and the sign-in panel use the designer's words, and some describe things the app cannot do yet. **Decision (Adebayo, 2026-11-03): keep as designed.** Revisit before the page is submitted for Google verification, because Google reads the page and compares it with the app:

| Where | Designer's words | What is true today |
|---|---|---|
| Hero pill, "How it works" step 3, feature "Tasks, sprints and backlogs" | Tasks, sprints and backlogs from every meeting; items assigned to teammates | No tasks, projects or teams |
| Platform chips, FAQ 1, sign-in panel | "Microsoft Teams" | Teams links are refused |
| Tools strip, FAQ 1 and 4 | Google Calendar, Outlook, Slack, Jira, Linear, Asana | No integration exists |
| Feature cards | Recurring meetings that remember; searchable decisions log; share, export and translate; reminders before every call | None built |
| FAQ 5 | Email, PDF and Word download of notes | No endpoint |

The words are in one file (`web/components/landing/content.ts`) plus the two preview mock-ups, so trimming them is a small edit. The page `<title>`, meta description, JSON-LD and `llms.txt` were **not** changed and still say only what is true (Google Meet and Zoom, free during the pilot).

## Out of scope: needs backend, not built

> **Stale since 2026-11-07.** Almost every row below has been built since (Tasks, Projects, Team, Settings, Series, Share, Download, Translate, Emails). The current state is in `docs/ui/audit-2026-11-07-app.md` and `docs/ui/audit-2026-11-07-pages-emails.md`. Still true: the sidebar shows "Owner" instead of the name and email.

| Designed element | Where | Needs |
|---|---|---|
| ~~Sign up, verify email code~~ (built as link and password: see "Account screens (stream D)"); set up workspace, accept (team) invite | `Auth.dc.html` | Workspaces, team invites |
| ~~Forgot password, check email, new password, "password updated"~~ | `Auth.dc.html` | built in stream D |
| Microsoft sign-in button (Google is built) | `Auth.dc.html` | Microsoft OAuth |
| "Keep me signed in" on sign-in (the email field is built) | `Auth.dc.html` | A session lifetime option |
| ~~Sidebar name and email ("Adebayo Tosin")~~ | App shell | Done 2026-11-07 (batch 2, PR A): the session already carries `user.name` and `user.email` |
| Sidebar: Tasks (with count), Projects, Team, Settings (**Decisions and Review, with the Review count, are built**: `docs/sop/2026-11-03-stream-d-decisions-review.md`) | App shell | Tasks, projects, team and settings APIs |
| "Join meetings automatically" card, Connect calendar, Upcoming list and toggles | Meetings | Calendar connection and auto-join (pilot design, PR #20) |
| Recurring (series) cards, the Series screen | Meetings, meeting page | Series model and API |
| "N tasks" link on a list row | Meetings | Tasks |
| Download menu (PDF, Word, with or without transcript) | Meeting page | Export endpoints |
| Share notes dialog | Meeting page | Email sending, share endpoint |
| ~~Language select, translated banner, "Show original"~~ (built with W7, see "Translate (W7)") | Meeting page | built |
| Duration, Participants, Series, Milestone in Details | Meeting page | Fields on the meeting API |
| Action items as tasks with owner, due date, "Needs owner", "View board" | Meeting page | Structured notes and tasks |
| "On next agenda" chips, Mark resolved, agenda items, Send agenda | Meeting page, series | Agenda and carry-over model |
| ~~Delete template~~ | Templates | **Built** 2026-11-04 (`DELETE /api/templates/<id>`): the red text button as drawn, plus an inline ask the design does not have; see `docs/sop/2026-11-04-template-delete.md` |
| Projects, Tasks board, Team (invite), Settings screens (**Decisions and Review are built**) | Main app prototype | Everything above |
| Emails: reminders, notes shared, invitations, verification | `Emails.dc.html` | Mail provider (pilot design Q6), HTML templates, scheduling |
| Sign-up buttons and "See the app" on the landing page | `Landing.dc.html` | Not part of stream D's account screens; left as it was (the sign-up pages exist, but sign-up is closed) |

No page and no control was faked for any of these.

## Not verified

- Safari and Firefox (Chromium only).
- The live site: the pages were checked on a local production build against a mock that replays the captured API, not against real FastAPI, Cloudflare or nginx.
- Real data: long meeting titles, many meetings, a very long transcript were checked in the mock's seven meetings only.
- Print styles, reduced-motion, and high-contrast modes.
- The FAQ uses `<details name="faq">` (exclusive group). Browsers older than 2024 open several at once; nothing breaks.
- Account screens (stream D): not run against real FastAPI, a real mail, or a real Google round trip; the mock replays captures and applies a few magic values (see the header of `web/scripts/mock-api.mjs`). The `prefill_email` cookie (path `/api/pilot`) is not in the mock, so that path was checked with the mock's own value, not with the real cookie. No side-by-side screenshots were committed for these screens (Playwright is not installed here, so `parity-shots.mjs` could not run); they were looked at in the built-in browser at 375 and 1280 px.

## Translate (W7), 2026-11-04

Checked as above (built app, the mock replaying `docs/fe-captures/translate/`, Chromium, **1440, 768 and 390 px**), design left and page right: `docs/ui/parity/after/app-translate-original-*`, `app-translate-working-*`, `app-translate-fr-*`, `app-translate-fr-transcript-*`, `app-translate-es-*`, `app-translate-download-*`, `app-settings-language-*`; the Settings and meeting screens were re-shot (`app-settings*`, `app-detail`). The table of what was designed, what is built and every difference is in `docs/sop/2026-11-04-w7-translate.md` ("Parity table"). In short: the Language select, "Translating to French...", "Translated from English to French · Show original", the translated notes with the dictionary's headings, the translated transcript, Copy, the Download menu's language note and the live "Default notes language" card with the designer's toast all follow the file; the failed, out-of-date, offer and error states are the smallest things (the design is silent) and are listed as gaps. Differences left: the bordered Action items box (Tasks), turn times in the transcript (not stored), the design's 900 ms translation against a real one.
