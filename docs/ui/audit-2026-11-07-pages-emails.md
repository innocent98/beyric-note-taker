# UI audit: designer's non-app files (Auth, Landing, Emails, Privacy, Terms, Brand, logos)

Read-only audit of commit 4159b74 (`main`), worktree `lead-audit`. Design folder: `docs/ui/Beyric Note Taker redesign/` (abbreviated `D/`). All files were read in full. Paths are relative to the repo root. "Not verified" means I did not run or render anything: this is a code-against-design read only.

## 0. Which version is current, and what the designer did not draw

- **Auth.dc.html is current** (505 lines, 9 screens: sign-in, sign-up, verify, setup, invite, forgot, sent, reset, done; the screen list is `D/Auth.dc.html:332`). `Auth v1.dc.html` (92 lines: sign-in, sign-up, forgot, check-email, new-password) is the older one and is a strict subset.
- **Landing.dc.html is current** (280 lines). `Landing v1.dc.html` (130 lines, centred hero, 3 feature cards, 3 steps) is older.
- **The designer drew NO**: waiting list, "pilot full" or "sign-up closed" screens, a "confirm email by link" page, an unsubscribe page, a pricing section on the landing page (there is none in Landing.dc.html), a "Could not join" for the calendar, a disconnect mail, an agenda mail, an "already registered" mail, a waiting-list invite mail, or any email subject lines or preheaders (Emails.dc.html has titles and bodies only). So "subject matches" cannot be judged for any mail.
- `Emails.dc.html` draws **9** mails: Reminder 1 hour (L19), Reminder 30 minutes (L49), New tasks (L66), Team invite (L84), Notes ready (L98), Notes shared (L118), Could not join (L137), Verify email (L151), Reset password (L165).
- Note on a designer inconsistency: Auth.dc.html draws a **6-digit code** to verify email (L119-140, L335 comment `RULES`), while Emails.dc.html draws a **link button** "Confirm email" (L158). The built app follows the email (link).

Known decisions (listed, not flagged as defects):
1. Landing copy kept as designed even where it overstates (2026-11-03; `docs/ui/parity-report.md` "Copy that runs ahead of the product"; `docs/checklist/master-checklist.md:153`, revisit at V0). Several of those lines have since become true after W phases (tasks, recurring, decisions log, share/export/translate, Slack, Linear). Still ahead of the product: "Microsoft Teams" chip and FAQ 1 (`web/components/landing/content.ts:22,30`; Teams links are refused), "Outlook", "Jira", "Asana" (content.ts:29), and "at rest with AES-256" (content.ts:15; `docs/sop/2026-11-02-public-pages.md` says no disk encryption was set up and the live privacy text, `web/components/legal/privacy-content.tsx:101`, says only TLS). I did not verify infrastructure. This last one is the riskiest line for the Google review; it is covered by the decision but worth the owner's eye at V0.
2. Legal text is the live text, not the designer's sample text (`docs/ui/parity-report.md` "Left as it is on purpose"); no notice-of-changes promise (`privacy-content.tsx:127`, `terms-content.tsx:82`); "Last updated 4 November 2026" and `TERMS_VERSION = "2026-11-04"` (`app/signup.py:43`) not bumped without the owner's word. Note: the live privacy text now mentions Slack, Linear and Teams-less scope (`privacy-content.tsx:71-72`), added after 4 Nov without a date bump: that is the owner's call under the standing rule, flagged only so it is a conscious one.
3. Entity "Beyrichq LTD" in place of "Beyric Technologies", mailbox `privacy@` in place of `hello@`, "Meeting link" placeholder (parity-report).

## 1. Master table

Class codes: **M** BUILT-MATCHES, **D** BUILT-DIFFERS, **OFF** BUILT-OFF, **L** BUILT-LEAD-DESIGNED, **NB** NOT-BUILT, **U** UNCLEAR. "(known)" = difference is documented in the parity report or an SOP as a decision.

### Auth (`D/Auth.dc.html`)

| id | Designed item | Design line | Code | Class | Notes |
|---|---|---|---|---|---|
| A01 | Sign in: "Welcome back", "Sign in to see your meeting notes and tasks.", Work email, Password with "Forgot password?", Show/Hide, "Signing in..." | 32-69, 449 | `web/components/app/login-form.tsx:65-128` | M | Words and placeholders identical (`you@company.com`, `Your password`). |
| A02 | Google button (sign-in and sign-up), two-column "Google" / "Microsoft" row | 40, 81 | `web/components/auth/google-button.tsx:10-36`, `login-form.tsx:71-76`, `signup-flow.tsx:205-210` | D (known) | Label is "Continue with Google" (design: "Google"), one full-width button, drawn only when `GET /api/pilot` says `google_signin: true`. |
| A03 | Microsoft button (sign-in and sign-up) | 41, 82 | none (`login-form.tsx:17-18` comment) | NB | Backend-blocked: no Microsoft sign-in (only Outlook calendar, M1 foundation). Omitted, not drawn as "Not available yet". |
| A04 | "Keep me signed in for 30 days" checkbox | 63-66 | none | NB | Backend-blocked: session lifetime is fixed. Omitted, not switched off. |
| A05 | Sign-in wrong-password message: "That email and password do not match. Try again or reset your password." | 373 | `app/services.py:200` ("That email or password is not right.") plus `login-form.tsx:83` extra line | D (known) | The API's words are shown; extra line about confirming email added. |
| A06 | Field-level errors under inputs: "Enter your email.", "Enter a valid email, like you@company.com.", "Enter your password." | 370-371 | `login-form.tsx:41-48` | D | Built checks only for empty, shows one alert above the form (not under the field), and has no valid-email check. Not in the parity report. Small. |
| A07 | Header link "New to Beyric? Create account" | 433 | `web/app/login/page.tsx:18` | M | |
| A08 | Create account: title, sub "Send the note taker to your next meeting in a few minutes.", Full name "Ada Okafor", Work email, Password "Create a password", button, "Creating account..." | 73-117 | `web/components/auth/signup-flow.tsx:191-272` | M | |
| A09 | 3-bar strength meter + 3 rules ("8+ characters", "A number", "An uppercase letter") | 104-109, 335 | `web/components/auth/password-field.tsx:50-61` (one rule "8 to 128 characters" + "Not a common password") | D (known) | API requires neither number nor uppercase; documented (`parity-report.md` Account screens). Same on reset (A18). |
| A10 | "By creating an account you agree to the Terms and Privacy Policy." sentence | 114 | `signup-flow.tsx:251-267` (checkbox "I agree to the Terms and the Privacy Policy.") | D (known) | API needs `accept_terms: true`. |
| A11 | Sign-up error: "Password does not meet the requirements." | 380 | `web/lib/accounts.ts:11-15` ("Use at least 8 characters.") | D (known) | Follows the rule change. Other messages ("Enter your name.", "Enter your work email.") are identical (`signup-flow.tsx:88-89`). |
| A12 | Verify email screen: "Check your inbox", "Enter the 6-digit code we sent to X", 6 boxes, "Checking code...", "Resend code" / "Resend in 0:30", "Wrong email? Change it" | 119-140 | none | NB | Backend uses link + password, not a code (`parity-report.md`). Replaced by A13. |
| A13 | (What was built instead) "Check your email", "We will email a confirmation link to X...", Open Gmail / Open Outlook, "Did not get it? Check spam, or resend the link", "Wrong email? Change it" | n/a (adapted from 226-239 and 135-138) | `signup-flow.tsx:156-188`, `mail-links.tsx` | D (known) | Heading differs from "Check your inbox"; resend wait is 60 s ("resend in 1:00") not 30 s. |
| A14 | "Set up your workspace": STEP 2 OF 2, Workspace name ("e.g. Beyric"), optional "Connect your calendar" (Google Calendar, Microsoft Outlook rows with Connect/Connecting.../Connected), "Continue to Beyric", "Skip for now", error "Give your workspace a name." | 142-169, 398 | none | NB | **No longer backend-blocked**: `PATCH /api/team` renames the team (`app/api_team.py:117-121`, `web/lib/api/client.ts:370`), Google calendar connect exists (Settings). The checklist and parity report still say "no backend" (`docs/checklist/master-checklist.md:168`). Never drawn, not even as "Not available yet". UI-only work now (Outlook row would need to be off). |
| A15 | "Join your team" invite screen: avatar stack banner "Adebayo Tosin invited you to the Beyric workspace", locked Email with lock icon, Full name, Password + rules, "Join Beyric"/"Joining...", header "Have an account? Sign in" | 171-208, 436 | `web/components/auth/accept-invite.tsx:29-166`, `signup-flow.tsx:194-198` | D | Built as a different flow: `/accept-invite` page has heading "{inviter} invited you to join {team}", no name/password form, no avatar banner, button "Create your account" or "Accept invitation". New users go to `/signup` with locked email, title "Create your account", an info Alert, a terms checkbox. **The code and SOP call this lead-designed ("the mail is drawn, the page is not", `accept-invite.tsx:18-19`, `docs/sop/2026-11-05-w3-team-ui.md:20`), but Auth.dc.html does draw the screen.** Wrong label. |
| A16 | Forgot: lock tile, "Reset your password", "Enter the email you signed up with and we will send you a reset link.", Work email, "Send reset link", "Sending..." | 210-224 | `web/components/auth/forgot-password-flow.tsx:96-118` | M | |
| A17 | Reset link sent: "Check your email", "If an account exists for X, a reset link is on its way. It expires in 30 minutes.", Open Gmail/Outlook, "Did not get it? Check spam, or resend the link" / "resend in 0:30" | 226-239 | `forgot-password-flow.tsx:61-94` | M (known) | Words identical. Wait is 60 s, not 30 s. Adds "Wrong email? Change it" (not drawn). |
| A18 | New password: "Choose a new password", "For X. You will be signed out on other devices.", New password + meter, Confirm password "Type it again", "Update password" | 241-269 | `web/components/auth/reset-password-form.tsx:89-124` | D (known) | Sub line is "You will be signed out on every device." and drops "For X." (link carries only a token); one rule instead of the 3-bar meter. |
| A19 | "Password updated" + "You can now sign in with your new password." + "Back to sign in" | 271-280 | `reset-password-form.tsx:61-72` | M | Adds "You were signed out on every device." |
| A20 | Page frame: logo, header link per screen ("Back to sign in" default), footer "© 2026 Beyric Technologies", Privacy / Terms / Help (hello@) | 17-26, 285-288 | `web/components/site/auth-shell.tsx:18-44` | M (known) | Entity and mailbox changed by decision. |
| A21 | Teal panel: "Every meeting, written up and assigned.", product preview card, NEW TASK card "Sprint 12", "Works with Google Meet, Microsoft Teams and Zoom" | 291-327 | `auth-shell.tsx:46-103` | M | Strings identical. |
| A22 | Layout: two columns that stack below 520 px (grid auto-fit) | 15 | `web/styles/public.css:15,28,235` | M | Not verified live; parity report says checked at 375 and 1280. |
| A23 | Pilot closed / pilot full / waiting-list form (not drawn) | none | `web/components/auth/pilot-screen.tsx`, `waitlist-form.tsx`, `web/app/pilot-full/page.tsx` | L | Code says "Auth.dc.html draws neither" (`pilot-screen.tsx:6`). Accounts SOP (`docs/sop/2026-11-03-accounts-auth-pages.md`) says "no prototype" but never uses the label "lead-designed". |
| A24 | Confirm email page `/verify-email` (password field, "Confirm email", "Email confirmed", "This link does not work") | none | `web/components/auth/verify-email-form.tsx` | L | Same labelling gap. |
| A25 | "Send a new link" form for a dead link | none | `web/components/auth/resend-link-form.tsx` | L | Same. |
| A26 | Unsubscribe page `/unsubscribe` | none | `web/components/auth/unsubscribe-form.tsx:12-16` | L | Labelled correctly (`docs/sop/2026-11-06-w6-share-screens.md:23`). |
| A27 | `/login?error=` messages (google_denied, terms_required, pilot_closed, account_suspended, email_unsupported) | none | `web/lib/accounts.ts:55-62` | L | Not labelled "lead-designed" in the SOP. |

### Landing (`D/Landing.dc.html`)

| id | Designed item | Design line | Code | Class | Notes |
|---|---|---|---|---|---|
| L01 | Sticky header: logo, nav How it works / Features / Security / FAQ, "Sign in" | 17-34 | `web/components/landing/landing.tsx:18-37` | M | |
| L02 | Header dark button "Get started" (to sign-up) | 31 | none | D (known) | Replaced by nothing: only "Sign in". Reasoning in the parity report ("sign-up is closed") is stale: `/signup` now exists with the waiting list. |
| L03 | Hero pill "New / Tasks, sprints and backlogs from every meeting", h1 "Your meetings, turned into finished work.", lede | 38-40 | `landing.tsx:44-55` | M | |
| L04 | Hero buttons "Get started free" and "See the app" | 42-43 | `landing.tsx:56-61` (single "Sign in") | D (known) | Parity report says not drawn on purpose; "See the app" links to the prototype file (legitimately dropped). |
| L05 | "Joins calls on" Google Meet / Microsoft Teams / Zoom chips | 45-52 | `landing.tsx:62-71`, `content.ts:30` | M | Known overstatement (Teams). |
| L06 | Product window (sidebar, "Beyric Monday planning", Summary, 3 action items), "8 tasks created" toast, "Recording · 12:48" | 55-93 | `landing.tsx:74-139` | M | |
| L07 | Tools strip "Works with the tools you use": Google Calendar, Outlook, Slack, Jira, Linear, Asana | 96-101 | `landing.tsx:144-155`, `content.ts:29` | M | Known overstatement (Outlook, Jira, Asana). |
| L08 | How it works: eyebrow, h2, 3 cards (01 It joins and listens, 02 It writes the notes, 03 It assigns the work) with mini mock-ups | 103-155 | `landing.tsx:170-240` | M | |
| L09 | "BEYOND THE NOTES" dark section: h2, side line, 6 feature cards | 157-176, 247-253 | `landing.tsx:242-267`, `content.ts:5-12` | M | All six titles and bodies identical. |
| L10 | Privacy and security: eyebrow, "Your meetings stay yours.", Privacy Policy link, 4 items | 178-193, 254-259 | `landing.tsx:269-291`, `content.ts:14-19` | M | |
| L11 | FAQ "Questions, answered", 5 Q and A, first open, one open at a time | 195-207, 260-266 | `landing.tsx:293-314`, `content.ts:21-27` | M | Native `<details name="faq">`. |
| L12 | Closing teal band: "Spend the meeting in the conversation.", "Create your account" (white) + "Sign in" (outline) | 209-220 | `landing.tsx:316-332` | D (known) | Only "Sign in" built. |
| L13 | Footer: logo, "Meeting notes, decisions and tasks for teams, from Beyric Technologies.", Product / Company columns, © 2026, notes.beyrictech.com | 222-242 | `landing.tsx:334-363` | M (known) | Entity Beyrichq LTD; Contact is `privacy@`. |
| L14 | (Not drawn) page title, meta, JSON-LD, llms.txt, sitemap | none | `web/app/page.tsx:15-21`, `web/components/site/json-ld.tsx` | L | True-statement metadata written by the lead. |

There is no pricing section in either landing file, so nothing pricing-related is pending.

### Emails (`D/Emails.dc.html`) and the app's mail code

| id | Designed mail | Design line | Template | Class | Notes |
|---|---|---|---|---|---|
| E01 | Reminder, 1 hour before | 19-46 | `app/series_mail.py:445-536` (`series_reminder`, kind `1h`) | D | Exists; see section 4. Pill is plain grey meta text, not a teal pill; labels are "Last time, Mon 2 Nov", "Decisions made", "Open questions to settle", "Still open from last time" in label style, not the designed small uppercase letter-spaced grey labels; no "Sprint 13 opens" in the meta line; no grey summary box ("Sprint 12 closed with 7 of 11 tasks done. Client Portal launch is in 29 days. The note taker will join this meeting automatically."); task owner shown as "title · name" on one line, not name under the title. |
| E02 | Reminder, 30 minutes before | 49-63 | `series_mail.py:525-532` | D | Missing the "Last time the team agreed... Read the full notes" paragraph and link (L59); agenda line is "Check on N unfinished task(s)." without the ", including ..." tail. Same pill and label styling difference as E01. |
| E03 | New tasks assigned | 66-82 | `app/mail_templates.py:488-571` | M | Heading, intro, rows, "Open my tasks", "Not yours? Open the task to reassign it.", footer and "Email preferences" all present. Subject adds the meeting name. |
| E04 | Team invite | 84-96 | `mail_templates.py:167-197` | M (known) | Footer is the standard signature "Beyric Note Taker, a product of Beyrichq LTD", not "Beyric Technologies". |
| E05 | Notes ready | 98-116 | `mail_templates.py:424-468` | M | Meta line, "Your meeting notes are ready", Summary / Decisions / Action items ("None noted."), "View full notes", "The note taker left early...", footer + "Email preferences". |
| E06 | Notes shared with you | 118-135 | `mail_templates.py:624-745` | D | "Open notes" button only for a teammate who may read it (design always draws it); footer "Sent by {name} ({email}) using Beyric Note Taker." vs "Sent by adebayo@beyrictech.com using Beyric Note Taker."; adds an unsubscribe line (needed, not drawn). Heading, meta (with "· N min"), quote box, sections, attached-file line match. |
| E07 | Could not join | 137-149 | none | NB | **Designed mail not built.** No template named for it. Nearest: the generic `alert` mail for "Meeting not recorded" (`app/join_runner.py:477-491`, `app/mail_templates.py:200-220`) has the title as heading, plain paragraphs, no "Send it again" button, no "Email preferences" link. Listed open in `master-checklist.md:204` (C14). Backend event exists (join failure notices); UI/template only. |
| E08 | Verify email | 151-163 | `mail_templates.py:113-123` | D | "Confirm this is your email to finish **creating** your Beyric Note Taker account." (design: "finish **setting up**"); note "The link works once and expires in 24 hours. If you did not create an account, you can ignore this email." (design: "This link expires in 24 hours. If you did not create an account, you can ignore this email."). Greeting "Hi {name}, confirm..." only when the name is plain. Heading and button identical. |
| E09 | Reset password | 165-177 | `mail_templates.py:126-137` | D | Note "The link works once and expires in 30 minutes. If you did not ask for this, ignore this email: your password has not changed." (design: "This link expires in 30 minutes. If you did not ask for this, your password will stay the same."). Intro and button identical. |
| E10 | Shared layout (logo mark, card, footer) | 21-22, 44 | `app/mail_layout.py:183-192` | D (known) | Logo is an HTML "B" tile, not the designed b-mark SVG; system font stack instead of Geist (documented in `mail_layout.py:10-13`, SOP w03). |

Mails the app sends that the designer did not draw (all L): 
| id | Mail | Template | Notes |
|---|---|---|---|
| E11 | "You already have an account" | `mail_templates.py:140-151` | Used by sign-up for a known address. Not labelled lead-designed in `docs/sop/2026-11-04-w03-mail-upgrade.md`. |
| E12 | "A place is waiting for you" (waiting-list invite) | `mail_templates.py:154-164` | Same. |
| E13 | Generic alert mail (meeting not recorded, note taker in another meeting) | `mail_templates.py:200-220`, callers `join_runner.py:491,777` | Same. This is where "Could not join" should be. |
| E14 | "Your calendar is disconnected" | `mail_templates.py:226-250` | Labelled lead-designed (`docs/sop/2026-11-05-phase-c-disconnect-banner-email.md:3`). Built from the Could-not-join layout. |
| E15 | "Agenda for the next meeting" | `app/series_mail.py:256-311` | Designer drew no agenda mail (only the "View agenda" button in the reminder). Not labelled lead-designed in `docs/sop/2026-11-06-w8-series-backend.md` ("the designer drew one button"). |

### Privacy Policy (`D/Privacy Policy.dc.html`)

Frame and layout: M (rows P01-P03). Sections: the live text replaces the sample text by decision, so each section is D (known). Code line numbers are in `web/components/legal/privacy-content.tsx`; the Jinja copy `app/templates/privacy.html` is kept equal by test.

| id | Designed item | Design line | Code line | Class | Notes |
|---|---|---|---|---|---|
| P01 | Sticky header with Privacy / Terms tabs, footer | 16-27, 56-61 | `web/components/site/legal-shell.tsx:22-61` | M (known) | Entity, Contact mailbox. |
| P02 | "ON THIS PAGE" list, 15 anchors | 29-34, 68-186 | `legal-shell.tsx:36-43`, `legal/toc.ts:3-19` | M | Same 15 ids; order differs (see P05). |
| P03 | Title, "Effective 2 November 2026 · Last updated 2 November 2026", intro, "Questions about this page?" box | 191-193, 53 | `privacy-content.tsx:6-8`, `legal-shell.tsx:46-48` | D (known) | Last updated is 4 November 2026 (owner decision, no further bump). |
| P04 | who | 69 | 10 | D (known) | Beyrichq LTD, Lagos; controller/processor sentence rewritten (users, not "customers"). |
| P05 | collect | 73 | 14 | D (known) | "Calendar information" subsection moved into the Google section; "Information collected automatically" shortened; "Teams" subsection added. |
| P06 | use | 98 | 57 | D (known) | Order: built puts Google before it. "Understand how the product is used in aggregate" removed. |
| P07 | legal bases | 111 | 106 | D (known) | Near-verbatim, order moved to after transfers. |
| P08 | recording | 121 | 81 | D (known) | Near-verbatim. |
| P09 | share | 126 | 67 | D (known) | Named processors (OpenAI, Resend, Slack, Linear, Contabo, Cloudflare) instead of generic. |
| P10 | google | 136 | 40 | D (known) | Expanded for Calendar and Meet scopes. |
| P11 | retention | 144 | 86 | D (known) | Days from settings, not "[30]"; backups 14 days not 90. |
| P12 | security | 153 | 100 | D (known) | No AES-256 / "security reviews" claim. |
| P13 | transfers | 156 | 103 | D (known) | France hosting named. |
| P14 | rights | 159 | 115 | D (known) | "California residents" subsection (L171-172) is **not** in the built text. |
| P15 | children | 174 | 120 | D (known) | |
| P16 | cookies | 177 | 123 | D (known) | One cookie, no analytics (designer: analytics with permission). |
| P17 | changes | 180 | 126 | D (known) | No notice promise (owner decision). |
| P18 | contact | 183 | 129 | D (known) | DPO line removed, placeholders filled. |

### Terms of Service (`D/Terms of Service.dc.html`)

| id | Designed item | Design line | Code line (`web/components/legal/terms-content.tsx`) | Class | Notes |
|---|---|---|---|---|---|
| T01 | Frame, header tabs, "ON THIS PAGE" (17 anchors), title, date line, intro | 15-57, 150-155 | `legal-shell.tsx`, `toc.ts:21-39`, `terms-content.tsx:8-10` | M / D (known) | Date 4 November 2026. |
| T02 | accept | 69 | 12 | D (known) | "Beyrichq LTD". |
| T03 | account | 73 | 16 | D (known) | Adds invite/share sentence. |
| T04 | service | 81 | 25 | D (known) | Google Meet and Zoom only (no Teams); "Pilot" sentence. |
| T05 | fees ("Plans, fees and payment") | 113 | 29 (`pilot`, "Pilot, fees and limits") | D (known) | Free pilot instead of paid plans; section moved to 4th. |
| T06 | recording | 85 | 32 | D (known) | |
| T07 | content | 94 | 41 | D (known) | |
| T08 | accuracy | 99 | 47 | D (known) | |
| T09 | use | 102 | 50 | D (known) | |
| T10 | third | 117 | 61 | D (known) | No Teams / Outlook named. |
| T11 | termination | 120 | 64 | D (known) | "ask us for a copy" instead of in-app export. |
| T12 | disclaimer | 124 | 68 | D (known) | |
| T13 | liability | 127 | 71 | D (known) | |
| T14 | indemnity | 131 | 75 | D (known) | |
| T15 | law | 134 | 78 | D (known) | Brackets removed. |
| T16 | changes | 137 | 81 | D (known) | 30-day email notice removed (owner decision). |
| T17 | general | 140 | 84 | D (known) | |
| T18 | contact | 143 | 87 | D (known) | |

### Brand and logos

| id | Designed item | Design line | Code | Class | Notes |
|---|---|---|---|---|---|
| BR01 | Logo + wordmark (Beyric 650 / Note Taker 450 slate) | Brand 24-27 | `web/components/ui/logo.tsx:15-60` | M | Same path `M38 24V72A24 24 0 1 0 62 48`, centre dot. |
| BR02 | Dark variant (#1A9483 tile on #121715) | 28-31 | `logo.tsx` tone="dark", `tokens.css:41` | M | |
| BR03 | App icon, 1024 export, Google 120 | 34-37, Logo Export 24-27 | `web/public/brand/*.png`, `web/public/icons/*` | M | Files copied from `D/logo/`. |
| BR04 | Mono mark | 38-41 | none | U | Reference artwork; no consumer. Not verified whether needed. |
| BR05 | Small sizes: dot dropped at 16 px and below | 42-48, 51 | `logo.tsx:16-27` (`dot` prop, stroke 16), `web/app/icon.svg` | M | |
| BR06 | 8 colour tokens | 83-92 | `web/styles/tokens.css:39-44` and the rest (all 8 hex values found) | M | |
| BR07 | Type: Geist + Geist Mono | 73-75 | `web/public/fonts/*` | M | |
| BR08 | `Logo Export.dc.html`, `Logo Options.dc.html` | n/a | `logo/` PNGs only | M (reference) | Reference only; not built, correctly. |

## 2. NOT-BUILT

1. **A03** Microsoft sign-in buttons: backend-blocked (no Microsoft SSO).
2. **A04** "Keep me signed in for 30 days": backend-blocked (fixed session lifetime).
3. **A12** 6-digit email code screen: replaced by the link flow (design conflict with Emails.dc.html's link mail).
4. **A14** "Set up your workspace" (workspace name, optional calendar connect, "Skip for now"): was backend-blocked, **now buildable** (`PATCH /api/team` name, Google calendar connect exist). Stale "no backend" text at `docs/checklist/master-checklist.md:168` and `docs/ui/parity-report.md` ("Not built, no backend"). Nothing drawn, not even a "Not available yet" line.
5. **E07** "Could not join" email: a designed mail with no template. The join-failure event and the generic alert mail exist; a template with "The note taker could not join your meeting", the platform/time line, and "Send it again" does not. Checklist D14 (`master-checklist.md:204`) still lists it open.

Nothing in these files uses a "Not available yet" line: omitted items are simply absent, so the rule "drawn features without a backend are switched off with a line" is applied as omission here (the line exists only in `web/components/app/projects-view.tsx:232`).

## 3. BUILT-DIFFERS

Undocumented or mislabelled (act on these):
- **A15 Join your team / Accept invite**: designed in Auth.dc.html:171-208, built as a different pair of screens and labelled "lead-designed" because the SOP and the code comment believe only the mail was drawn (`accept-invite.tsx:18-19`, `docs/sop/2026-11-05-w3-team-ui.md:20`). Needs either a rebuild to the drawn screen (banner with avatars, locked email, Full name, Password + rules, "Join Beyric", header "Have an account? Sign in") or the designer's sign-off, and the SOP corrected.
- **E01/E02 reminder mails** differ from the drawing in pill, label style, missing meta suffix, missing summary box, missing "Read the full notes" paragraph (details in the table). Not recorded as differences in `docs/sop/2026-11-06-w8-series-backend.md`.
- **E08/E09 verify and reset mails**: the "works once" wording and "finish creating" differ from the drawing. Minor, not recorded.
- **E06**: owner's footer line and the conditional "Open notes" button differ (button rule is spec decision D7, `mail_templates.py:632`, the footer is not recorded).
- **A06** sign-in field errors (one alert, no valid-email check).
- **L02, L04, L12** landing sign-up buttons: documented, but the reason ("sign-up is closed", parity-report) is stale now that `/signup` exists with a waiting list. Owner decision needed on whether "Get started" / "Create your account" return (they could point at `/signup`, which shows closed or full screens when the pilot is not open).

Documented and deliberate (known): A02, A05, A09, A10, A11, A13, A17 (60 s), A18, A20, L13, E04 footer, E10, P03-P18, T01-T18.

## 4. Emails: built / not built

| Designed mail | Template exists | Subject | Wording | Layout | Status |
|---|---|---|---|---|---|
| Reminder 1 h | `series_reminder` (`app/series_mail.py:445`) | no design subject; built "Starts in 1 hour: {series}" | footer and button words match ("View agenda", "Join meeting", "Reminder settings", "You are getting this because you attend X.") | differs (see E01) | built, reminders off by default per team (`series_mail.py:458`, `teams.reminders_on`) |
| Reminder 30 min | same, kind `30m` | "Starts in 30 minutes: {series}" | partly (see E02) | differs | built |
| New tasks | `tasks_assigned` (`mail_templates.py:488`) | "You have N new tasks: {meeting}" | matches | matches (new `Rows` block) | built |
| Team invite | `team_invite` (`mail_templates.py:167`) | heading used as subject | matches | matches | built |
| Notes ready | `notes_ready` (`mail_templates.py:424`) | "Your notes are ready: {meeting}" | matches | matches | built |
| Notes shared | `notes_shared` (`mail_templates.py:624`) | "{sender} shared meeting notes: {meeting}" | matches (footer, button differ) | matches | built |
| Could not join | none | n/a | n/a | n/a | **not built** |
| Verify email | `verify_email` (`mail_templates.py:113`) | "Confirm your email for Beyric Note Taker" | differs slightly | matches | built |
| Reset password | `reset_password` (`mail_templates.py:126`) | "Reset your Beyric Note Taker password" | differs slightly | matches | built |

Reminders, "New tasks", "Notes shared", and "Team invite" are **built** (contrary to the stale line in `docs/ui/parity-report.md` "Out of scope: Emails: reminders, notes shared, invitations, verification" and `master-checklist.md:204`, which should be reconciled). Disconnect mail is built but was never drawn. The only designed email not built is "Could not join".

Not drawn by the designer but sent: E11 already_registered, E12 waitlist_invite, E13 alert, E14 calendar_disconnected, E15 series_agenda. Only E14 is labelled lead-designed in an SOP.

## 5. Wording mismatches (spot-check: 62 strings Grepped across `web/app`, `web/components`, `web/lib`, `app`)

Found verbatim (33): "Your meetings, turned into finished work." (landing.tsx:51), "Tasks, sprints and backlogs from every meeting" (:49), "Works with the tools you use" (:148), "From conversation to assigned work, with no one taking notes." (:175), "It joins and listens" (:178), "It writes the notes" (:192), "It assigns the work" (:207), "BEYOND THE NOTES" (:248), "One place for what was said, decided and promised." (:249), "Recurring meetings that remember" (content.ts:7), "A searchable decisions log" (:8), "Reminders before every call" (:11), "Your meetings stay yours." (:274), "Encrypted end to end" (content.ts:15), "Visible to everyone" (:17), "Questions, answered" (:297), "Spend the meeting in the conversation." (:321), "notes.beyrictech.com" (:358), "Questions about this page? Email" (legal-shell.tsx:47), "ON THIS PAGE" (:37), "Welcome back" (login-form.tsx:68), "Sign in to see your meeting notes and tasks." (:69), "or sign in with email" (:74), "or use your email" (signup-flow.tsx:208), "Send the note taker to your next meeting in a few minutes." (:193), "Every meeting, written up and assigned." (auth-shell.tsx:50), "Enter the email you signed up with and we will send you a reset link." (forgot-password-flow.tsx:99), "Did not get it? Check spam, or" (:71), "Passwords do not match." (reset-password-form.tsx:37), "Type the password again." (:36), "Enter your name." / "Enter your work email." / "Enter a valid email, like you@company.com." (signup-flow.tsx:88-90), "Starts in 1 hour" / "Starts in 30 minutes" (series_mail.py:473), "Open my tasks", "Not yours? Open the task to reassign it.", "You received this because you are on the Beyric team." (mail_templates.py:560-563), "Accept invite", "This invite expires in 7 days." (:185-187), "Your meeting notes are ready", "View full notes", "The note taker left early, so these notes may be incomplete.", "You received this because you sent the note taker to this meeting.", "None noted." (:448-456, 405), "The full transcript is attached." (:720), "Confirm your email address", "Confirm email" (:116-118), "View agenda", "Reminder settings", "You are getting this because you attend" (series_mail.py:487,250,535).

Missing or differing (29):

| Designed string | Design | In code |
|---|---|---|
| "Get started" (header) | Landing:31 | absent |
| "Get started free" | Landing:42 | absent (only a comment, landing.tsx:7) |
| "See the app" | Landing:43 | absent (comment only) |
| "Create your account" (closing band button) | Landing:216 | absent on the landing; exists as the signup page title and accept-invite button |
| "© 2026 Beyric Technologies" / "from Beyric Technologies" | Landing:227,239 | "Beyrichq LTD" (landing.tsx:341,357) (known) |
| "hello@beyrictech.com" | Landing:235 | `privacy@beyrictech.com` (landing.tsx:353) (known) |
| "That email and password do not match. Try again or reset your password." | Auth:373 | `app/services.py:200` "That email or password is not right." |
| "Enter a valid email" on sign-in, "Enter your password." under the field | Auth:370-371 | only "Enter your password." in an alert (login-form.tsx:46) |
| "Password does not meet the requirements." | Auth:380 | "Use at least 8 characters." (accounts.ts:13) |
| "8+ characters", "A number", "An uppercase letter" | Auth:335 | "8 to 128 characters", "Not a common password" (password-field.tsx:56-58) |
| "By creating an account you agree to the Terms and Privacy Policy." | Auth:114 | checkbox "I agree to the Terms and the Privacy Policy." (signup-flow.tsx:258-266) |
| "Check your inbox" | Auth:123 | "Check your email" (signup-flow.tsx:159) |
| "Enter the 6-digit code we sent to X." | Auth:124 | "We will email a confirmation link to X..." (signup-flow.tsx:160) |
| "Checking code...", "Resend code", "Resend in 0:30" | Auth:133,136 | absent; "resend in 1:00" (use-countdown.ts) |
| "Set up your workspace", "Workspace name", "STEP 2 OF 2", "Continue to Beyric", "Skip for now", "Give your workspace a name.", "Connect your calendar (optional)" | Auth:144-166, 398 | none anywhere |
| "Join your team", "You will see shared meeting notes and the tasks assigned to you.", "Join Beyric", "Joining..." | Auth:182-206 | "Join as a member to see shared meeting notes and the tasks assigned to you." (accept-invite.tsx:153) and "Accept invitation" (:162); "Joining..." only on the waitlist button |
| "For X. You will be signed out on other devices." | Auth:245 | "You will be signed out on every device." (reset-password-form.tsx:91) |
| "Microsoft" button, "Keep me signed in for 30 days" | Auth:41,65 | none |
| "Google" button label | Auth:40 | "Continue with Google" (google-button.tsx:15) |
| "Hi X, confirm this is your email to finish setting up your ... account." | Emails:157 | "...to finish creating your..." (mail_templates.py:117) |
| "This link expires in 24 hours." | Emails:159 | "The link works once and expires in 24 hours." (:119) |
| "This link expires in 30 minutes. If you did not ask for this, your password will stay the same." | Emails:173 | "The link works once and expires in 30 minutes. If you did not ask for this, ignore this email: your password has not changed." (:133-134) |
| "The note taker could not join your meeting", "Send it again", "We tried to join ... but were not admitted" | Emails:142-145 | absent everywhere |
| "LAST TIME, MON 2 NOV" / "DECISIONS MADE" / "OPEN QUESTIONS TO SETTLE" / "STILL OPEN FROM LAST TIME" / "ON THE AGENDA" (uppercase, small, grey, letter-spaced) | Emails:30-57 | same words in sentence case, label style bold 13 px ink (series_mail.py:496,511,515,522,532; mail_layout.py:120-124) |
| "Sprint 12 closed with 7 of 11 tasks done. ..." summary box; "Sprint 13 opens" in meta | Emails:25,42 | absent |
| "Read the full notes" link and "Last time the team agreed..." paragraph (30-minute mail) | Emails:59 | absent |
| "Sent by adebayo@beyrictech.com using Beyric Note Taker." | Emails:133 | "Sent by {name} ({email}) using Beyric Note Taker." (mail_templates.py:732) |
| "Beyric Technologies" mail footer (team invite, verify, reset) | Emails:94,161,175 | "Beyric Note Taker, a product of Beyrichq LTD" (mail_templates.py:63) |
| "Questions about this page? ... hello@" / DPO line / "California residents" subsection / analytics-cookie sentence | Privacy:172,184,178 | not in the live text (known) |

## 6. Not verified

- No page was rendered, no mail was sent or opened, no screenshot was compared: the visual claims (spacing, colours, mobile) rest on `docs/ui/parity-report.md` and SOPs, not on my own check. Parity images under `docs/ui/parity/after/` were not opened.
- Whether the legacy Jinja pages (`app/templates/landing.html`, `login.html`, `privacy.html`, `terms.html`) are still reachable in production (the Next front door may shadow them). The legal text equality test (`tests/test_legal.py`, `web/tests/legal.test.tsx`) was not run.
- Whether sign-up auto-creates a team (relevant to how big A14 is).
- Live infrastructure behind the landing's "AES-256 at rest" and "close your account at any time" lines (content.ts:15,18).
- Mail look in Gmail/Outlook/Apple Mail (the SOP itself says this was not done).
- Whether Brand's "Mono mark" (BR04) needs a consumer.
- `docs/ui/parity-report.md` and the checklist predate the W phases; I did not audit the main app file (a sibling audit covers it).
