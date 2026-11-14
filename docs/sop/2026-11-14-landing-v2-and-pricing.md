# SOP: Landing v2 and the /pricing page

## What shipped
The public Landing rebuilt from the designer's second Landing file, and a new public `/pricing` page from the designer's Pricing file, both with every claim cut to what the app really does. Branch `feature/landing-v2-and-pricing`, one PR (web only: no API, no migration). Dashboard design: no work (it equals the 2026-11-07 design we built to, plus CRM, which is deferred).

## Why
The designer delivered new Landing, Pricing and dashboard files on 2026-11-14. The audit (`docs/superpowers/specs/2026-11-14-public-pages-audit.md`) found claims the app cannot back (HubSpot, Jira, Asana, times in the transcript, "every attendee", "unlimited", encryption at rest, "never train"). Adebayo's decisions of 2026-11-14: public pages capture what we really offer; the design's look, layout and components stay; his decisions and what has shipped override the designer.

## How
- **Look and layout from the design, words from the app.** `web/components/landing/landing.tsx` rebuilds the sections in the designer's order (hero, tools strip, Why Beyric, five deep dives each with a mock-up, dark basics, Who it's for, security, FAQ, closing band, footer). The designer's inline styles became classes in `web/styles/public.css`, with colours as tokens (five new tokens in `tokens.css`; `guards.test.ts` forbids colour literals and inline styles).
- **One data file per page.** `web/components/landing/content.ts` holds every landing word; `web/components/pricing/content.ts` holds every plan name, price, hour value, table row and question. **To enter Adebayo's final numbers: set `price` and `hours` on a plan in `PLANS` (a `null` prints "To be announced"); the card and the comparison table both read those two fields.** A test proves a decided value flows through, and that the page component holds no plan words.
- **Pricing page** shows the structure he decided (a one-time trial, a paid Team plan, a larger plan, hours shared across the team, extra-hour packs) with NO figure: every price and hour value is "To be announced". Plan names Trial / Team / Team Plus are working names (mine; he has not chosen names). Nothing is gated: every feature row is "Yes" on every plan, calendar auto-join included. Every plan's button goes to `/signup` (no plan is on sale).
- **Sign-up is invite-only, said plainly** in one place (`SIGNUP_NOTE` in `content.ts`): under the hero button, in the closing band, and on the pricing page. When sign-up opens, that is one edit.
- **Wiring:** `/pricing` added to `NEXT_PAGES` (`web/lib/routes.ts`), the proxy matcher (`web/proxy.ts`), the sitemap, `robots.txt`, `llms.txt`; "Pricing" in the header and footer. The JSON-LD `offers` block (price 0, "Free during the pilot") is removed: no price is decided and "free" is out.
- `/welcome` and the signed-in behaviour (logo to `/welcome`, "Open the app") are untouched, and the same on `/pricing`.
- **Rejected:** keeping the designer's "Get started free" / "free to start" (decision: no "free" wording); a "Business" plan; showing Adebayo's guessed numbers ("about 20 hours", "about 40") as if decided.

## What's involved
`web/components/landing/{content.ts,landing.tsx}`, `web/components/pricing/{content.ts,pricing.tsx}`, `web/app/pricing/page.tsx`, `web/styles/{public,tokens}.css`, `web/lib/routes.ts`, `web/proxy.ts`, `web/app/{sitemap,robots}.ts`, `web/app/llms.txt/route.ts`, `web/components/site/json-ld.tsx`; tests `web/tests/landing-v2.test.tsx` (new), `parity.test.tsx`, `landing-security-copy.test.tsx`.

## Verification
- Lint, type-check and the tests of the touched files only (`landing-v2`, `landing-security-copy`, `parity`, `welcome`, `routes`, `guards`); CI runs the rest and the build.
- **Parity evidence:** `docs/ui/parity/landing-v2/{landing,pricing}-{1440,768,390}.jpg`, the designer's file on the left and ours on the right, whole page, taken from `next dev` against `web/scripts/mock-api.mjs` (never the pilot). `metrics.json` has page heights and `scrollWidth` per shot: **no horizontal scroll at any of the three widths** (scrollWidth equals the viewport on all twelve shots). Page heights are within about 4% of the design.
- **Light and dark:** the designs are light only (as the 2026-11-03 parity report says); nothing to compare.
- **Keyboard:** tabbing the landing page visits skip link, logo, the four header links, Sign in, Get started, the hero's two links, Privacy Policy, the six questions, the closing links and the footer, in reading order; `/pricing` the same. The mock-ups hold no focusable element (the "Confirm task" and "Dismiss" buttons are spans in an `aria-hidden` panel).
- Not verified: the live site (no server access here), a real phone, Safari, and Google's or any crawler's reading of the new structured data.

## Parity report: every difference from the designer's files
"Cut" = not built, so not claimed. "Decision" = Adebayo, 2026-11-14. "Honest wording" = the claim ran past the product (audit id in brackets).

### Landing
| # | Design | Ours | Why |
|---|---|---|---|
| D1 | "Get started free" (header, hero) | "Get started" | Decision: no "free" wording |
| D2 | "See the app" button (links to the prototype file) | Not drawn | Not a page (as on the live site) |
| D3 | Hero lede "Other note takers hand you a summary. Beyric ... assigns every action item to the right person ... opens your next meeting with what is still unfinished" | "Beyric writes the notes, matches each action item to the person who owns it, files it in the right project and sprint, and brings what is still unfinished back to your next recurring meeting." | Honest wording: no claim about others, items with no owner wait (L1, L2, R9) |
| D4 | (nothing) | Line "Sign-up is invite-only for now ... join the waiting list" under the hero button and in the closing band | Decision: say invite-only plainly |
| D5 | (nothing) | "A sample meeting, for illustration." above the hero window; "The meetings, people and dates in these examples are made up." under the deep dives | Honest wording: the mock-ups are samples (L4) |
| D6 | Tools strip "Works with the tools you use": Google Calendar, Outlook, Slack, HubSpot, Jira, Linear, Asana | "Works with": Google Calendar, Outlook calendar, Slack, Linear | Cut: HubSpot, Jira, Asana (not built). Outlook named as a calendar, Slack and Linear are one-way (L5) |
| D7 | Why Beyric: heading "A summary is where most note takers stop. It is where the work starts."; left card "A typical note taker" with four claims about others | Heading "A summary is only the start. The work is what happens next."; left card "Notes on their own": a recording and a summary / a list of action items / tasks copied by hand / each meeting starts from zero | Honest wording: no unprovable claim about competitors (L2, R9) |
| D8 | Why Beyric body "carries every meeting through to the point where someone owns the next step" | "...each task has an owner, or is clearly waiting for one" | Honest wording: Needs owner (L1) |
| D9 | Right card: "Assigns each task to its owner, with a due date"; "Opens the next meeting with what is still unfinished" | "Matches each task to its owner, with a due date when one was said"; "Shows what is still unfinished at the next recurring meeting" | Honest wording (L1, L7, L12) |
| D10 | Ask: "the moment it was said", "across every meeting your team has had"; time chips `00:12`, `09:30` | "the line it came from", "across the meetings you can open"; chips read "Transcript line" | Cut: we store no turn times; Ask reads only what the asker may open (L9) |
| D11 | Suggested task quote with a `09:48` chip | Quote without the time | Cut: no turn times (L10) |
| D12 | "go to a review list instead of into your notes as fact" | "go to a review list" | Honest wording (L10) |
| D13 | Decisions: "the old decision is marked as replaced and points to the new one"; "Who made it and when, with the transcript line" | "mark the old decision as replaced and it points to the new one"; "Who made it and when, linked back to the meeting" | Honest wording: replacing is by hand (L11) |
| D14 | Recurring: "Attendees get a short brief before the call"; "A pre-meeting brief sent to every attendee" | "A team can also switch on a short reminder email before the call"; "An optional reminder email to team members who attended recently" | Honest wording: opt-in, members only (L12) |
| D15 | Tasks: "Launch dates ... become milestones"; "Milestones with progress, taken from what was said"; "Or send it all to Jira, Linear or Asana" | "are suggested as milestones, for you to confirm"; "suggested from what was said"; "Tasks from team meetings can also be sent to Linear" | Honest wording; cut Jira and Asana; Linear is tasks only (L13) |
| D16 | Dark section, six cards | Five cards: the HubSpot card ("Customer calls update HubSpot") is cut, so the grid shows 3 + 2 | Cut: not built |
| D17 | "Joins on its own": Google Calendar or Outlook | "Connect Google Calendar, or Outlook (new, and still being tested)" | Honest wording (L5, L14) |
| D18 | "who said what and when" | "who said what"; plus "Names it is unsure of go to a review list" | Cut: no turn times; names can be wrong (L14) |
| D19 | "read them in another language" | "read the notes in French or Spanish" | Honest wording: notes only, two languages (L14) |
| D20 | Who it's for: four cards; Sales card "logged in HubSpot without retyping", "HubSpot"; Product card "Jira, Linear or Asana"; Hiring card | Three cards: Hiring card cut; Sales card says "in notes you can send on by email" with uses "Client call template · Share by email"; Product card uses "Sprints · Milestones · Linear"; Founders "across your team's meetings" (was "the company") | Decision (cut Hiring); cut HubSpot, Jira, Asana |
| D21 | Security heading "Your meetings stay yours. They never train a model." | "Your meetings stay yours." | Decision: no training promise as ours |
| D22 | Card "Encrypted in transit and at rest ... AES-256" | "Encrypted in transit": "Traffic to the site is encrypted with TLS, and the sign-in tokens for connected calendars are stored encrypted." (the wording #197 already put live) | Decision: no claim of encryption at rest; nothing is encrypted at rest (L16) |
| D23 | Card "Never used for training ... ours or anyone else's. There is nothing to opt out of" | "Not used to train models": we do not use meeting audio, transcripts or notes to train AI models, and our providers say they do not train on it either; the Privacy Policy names them | Decision: say only what the Privacy Policy says (L16, R7) |
| D24 | "under a clear name" | "under a name you can see" | Decision: users can pick any bot name (L16) |
| D25 | Retention card without a caveat | Adds "Copies held by our providers follow their own schedules, listed in the Privacy Policy." | Honest wording: Privacy Policy retention section (L16, R5) |
| D26 | FAQ "How is this different from other AI note takers?" | "What does Beyric do beyond a summary?" (same answer without the comparison) | Honest wording (R9) |
| D27 | FAQ platforms | Adds "Outlook support is new and still being tested." | Honest wording (L5) |
| D28 | FAQ "Can we keep using Jira, Linear or Asana? Yes. Tasks, projects and sprints can be sent" | "Can we send tasks to our tracker?": Linear only, tasks only, once a team owner or admin connects it; "Jira and Asana are not supported" | Cut: not built; honest wording (L13, L17) |
| D29 | FAQ "Does it update our CRM?" | Not drawn (six questions, not seven) | Cut: CRM not built, decision |
| D30 | FAQ "Do participants know they are being recorded? Yes. ... with a clear name" | Same question, answer starts "The note taker joins as a visible participant, under a name you can see in the participant list" and keeps the duty to tell attendees; no "Yes" | Honest wording: the chat notice is built but off (L17, R3) |
| D31 | Closing "... Free to start." | "Paste a meeting link and get notes, owners and tasks back." plus the invite-only line | Decision: no "free" |
| D32 | Footer "Beyric Technologies", `hello@beyrictech.com` | "Beyrichq LTD", `privacy@beyrictech.com` | Decision |
| D33 | Footer links Product, Features, Pricing, Security | Same; "Pricing" goes to `/pricing` | none (listed for completeness) |
| D34 | Header: links wrap onto a second row on a phone | Links hidden below 720 px; below 480 px the logo drops "Note Taker" so Sign in and Get started fit on one row | Same call as 2026-11-03 (the prototype's phone header breaks); Pricing stays reachable from the closing band and the footer |
| D35 | Status chip "Notes ready" etc. use the prototype's chips | Same words; chips built as `.ld-status` | none (look) |
| D36 | Security intro had no sticky behaviour | None (the old sticky intro is removed) | Follows the design |
| D37 | Mock-up buttons ("Confirm task", "Dismiss") are `<button>`s | `<span>`s inside an `aria-hidden` panel | Keyboard order: decorative controls must not take focus |
| D38 | FAQ opens one at a time with state | Native `<details name="faq">` (no JavaScript), first open | Existing pattern on the live page |
| D39 | Hero "Notes ready" window | Same | none |
| D40 | `/welcome` | Untouched | Decision |

### Pricing
| # | Design | Ours | Why |
|---|---|---|---|
| P1 | Heading "Start free. Pay when your team runs on it." | "Pay when your team runs on it." | Decision: no "free" |
| P2 | "Your data is never used to train a model, on any plan." | Replaced by "No plan is on sale yet, and every price and number of hours below is still to be announced." plus the invite-only line | Decision (no training promise); honest wording |
| P3 | Plans Free / Team / Business | Trial / Team / Team Plus (names are working names) | Decision: one-time trial, then Team, then a larger plan; no Business, no free plan |
| P4 | Prices `$0 forever`, `$00 per person, per month` | "To be announced" on every plan, with "one-time trial" / "per month" under it | Decision: nothing is decided; no seat model |
| P5 | Free: "A set number of meetings each month" | "Meeting hours: To be announced, one time" | Hours, not meetings; the trial is one time |
| P6 | Team badge "Most teams pick this" | Cut (teal frame kept) | Not a fact we can state |
| P7 | Buttons "Get started free", "Start with Team", "Talk to us" (`hello@`) | "Get started", "Start with the trial", "Start with the trial"; all to `/signup` | No plan is on sale; no `hello@` |
| P8 | Team list: unlimited meetings, calendar auto-join, tasks, decisions, recurring "and briefs", ask, templates, translation, Slack | Hours line, "Extra-hour packs when the team needs more", "Every feature in the table below" | Cut "unlimited"; nothing is gated, so features are not listed per plan |
| P9 | Business plan: HubSpot sync, Jira or Asana, milestones across projects, admin roles, custom retention, priority support | Not drawn | Cut: not built, or not gated by plan (decisions) |
| P10 | Table: "Meetings each month Limited / Unlimited / Unlimited"; gated rows (auto-join No, templates "General only", milestones Business only) | Rows "Meeting hours", "Hours shared across the team", "Extra-hour packs"; every feature row "Yes" on every plan | Decision: hours shared; do not gate features that exist for everyone |
| P11 | Table rows HubSpot sync; Jira, Linear or Asana sync; Custom retention period; Data used to train models "Never" | "Tasks sent to Linear" (Yes); others cut | Cut: not built; no training promise |
| P12 | (nothing) | Note under the table "Outlook calendar support is new and still being tested." | Honest wording (L5) |
| P13 | FAQ: who counts as a person; change or cancel; moving back to Free; discounts (`hello@`) | "Can I buy a plan today?", "How are meeting hours counted?", "What does the trial include?" | The design's answers are pricing policy not decided or billing not built |
| P14 | Closing "Try it on your next meeting. It is free to start." | "Try it on your next meeting." plus the invite-only line | Decision: no "free" |
| P15 | Footer "Beyric Technologies", `hello@` | "Beyrichq LTD", `privacy@` | Decision |
| P16 | Header: Product, Pricing; "Get started free" | Product, Pricing (current); "Get started"; owner sees "Open the app" | D1; same signed-in rule as `/welcome` |

## Operate / roll back
Revert the PR. Nothing to deploy beyond the web image. After deploy: `/pricing` must answer 200 on `notes.beyrictech.com` (it is a Next page: the `NEXT_PAGES` and proxy-matcher lists must agree, a test checks).

## Follow-ups
- Adebayo's numbers: edit `PLANS` in `web/components/pricing/content.ts` (price, hours, names). Terms and Privacy need a paid-plan section and a lawyer read before any plan sells; **Terms still say "The Service is free during the pilot"** (not touched here).
- `SITE_DESCRIPTION` (`web/lib/site.ts`, the meta description) and the Terms still say "Google Meet and Zoom" only; Teams works.
- When the chat notice (#188) goes on, update FAQ D30 and the security card "Visible to everyone".
- When sign-up opens, change `SIGNUP_NOTE` and the invite-only FAQ answer on `/pricing`.
- Stale Jinja landing (`app/templates/landing.html`) still exists behind the Next page; not touched.
