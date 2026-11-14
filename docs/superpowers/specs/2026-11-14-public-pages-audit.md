# Audit: the designer's new Landing, Pricing and dashboard files against what the app really does

Read-only audit, 2026-11-14 (evening), against `origin/main` at `ec605f8` (Alembic head 0047). No web code was changed. This file is the whole deliverable.

## Read this first

**What was audited.** Three new design files, delivered 2026-11-14 and still uncommitted in Adebayo's main checkout (`docs/ui/Beyric Note Taker redesign/`): `... – Landing.html`, `... – Pricing.html`, `... – Beyric Note Taker.html` (the app: the Meetings "dashboard" and every other screen). They are bundler files; the page text sits in a JSON string (`__bundler/template`). I decoded each into the session's scratch folder with a small Python script (reading only; nothing in the main checkout was touched) and read them in line ranges.

**The five biggest gaps**

| # | Gap | Where the design says it | Today |
|---|---|---|---|
| 1 | **HubSpot / CRM does not exist.** | Landing: tools strip, feature card, "Who it's for" card, FAQ "Does it update our CRM? Yes"; Pricing: Business plan; App: a whole "CRM" screen, sidebar item and a HubSpot panel on the meeting page | Zero code. `grep -ri hubspot app web` finds nothing. Adebayo has deferred CRM. |
| 2 | **There is no plan, price, entitlement or payment system, and no `/pricing` page.** | Pricing: three plans, "Unlimited meetings", 16-row comparison, plan-gated features | Every account gets every feature. The only limit is a lifetime recorded-hours cap per account (`app/usage.py`, default 5 h). No billing code, no billing spec yet. |
| 3 | **Security and privacy claims the product cannot back.** | Landing security card "Encrypted in transit and at rest ... AES-256"; "Visible ... under a clear name"; "Audio is removed after a set period"; Pricing "Data used to train models: Never" on every plan | No disk encryption is set up (`docs/sop/2026-11-02-public-pages.md:21`, checklist line "at rest with AES-256" is still open). The live landing already says this (and "Encrypted end to end"). Users can now choose any bot name. |
| 4 | **Integrations the page lists but the app lacks.** | "Works with" strip and "send it all to Jira, Linear or Asana" | Linear: one-way, tasks only, built. Slack: one-way webhook, built. **Jira and Asana: not built** (listed in Settings but unselectable). Outlook: built, never tested against real Microsoft. |
| 5 | **Times and briefs that do not exist.** | Landing mock-ups show `00:12`, `09:48` chips; "who said what **and when**"; "a pre-meeting brief sent to **every attendee**" | We store no turn times (`docs/fe-integration-guide-ask-beyric.md:291`, `fe-integration-guide-suggested-tasks.md:77`; checklist "Transcript turn times (A18): not stored"). The brief is an opt-in reminder mail to team members who attended, never outsiders (`app/series_mail.py` decision D16). |

**One finding outside the three files that matters more than all of them:** the **live Privacy Policy says "The software that joins meetings runs on our own server; no other company receives the recording from it"** (`web/components/legal/privacy-content.tsx:87`). Since 2026-11-09 the bot is **Skribby**, a hosted vendor that receives the audio. Skribby is not on the policy's list of providers; the checklist line "Privacy page and sub-processor list: add Skribby" is still open (`docs/checklist/master-checklist.md:447`). The SOP says Skribby also keeps "debugging data (including screenshots)" for 30 days regardless of deletion requests (`docs/sop/2026-11-08-skribby-provider.md` section 7). Fix this before anything else in this file.

**Another surprise, in the good direction:** the dashboard design is **the same as the 2026-11-07 design we already built to**, plus CRM. I diffed the new file's visible text against the 7 Nov file (81 diff lines, all CRM) and its script (CRM data plus one new sample meeting "Client call: quarterly review"). So the dashboard has **no new work except CRM**, and CRM is deferred.

## How I checked

| Source | What I did |
|---|---|
| The three design files | Decoded the template string; read Landing (690 lines) and Pricing (472) in full and the App file's shell, Meetings and CRM screens; text-diffed the whole App file against the 7 Nov `Beyric Note Taker.dc.html` |
| The app | `origin/main` at `ec605f8`: `web/components/landing/*`, `web/app/*`, `web/components/legal/*`, `app/*.py`, the FE guides and SOPs named in each row |
| Adebayo's decisions | `docs/HANDOFF-master.md` (2026-11-14 block first), `docs/checklist/master-checklist.md`, and the lead's memory notes of 2026-11-13/14 |
| Not verified | Anything on the live server (flags, data). I did not open the live site. **Server flags are as of the 2026-11-14 10:00 handoff**: `MULTI_TEAM_ENABLED` off, `MICROSOFT_CALENDAR_ENABLED` true, sign-up open (10 accounts, 5 h each). TLS version 1.2 or higher is **not** verified from the repo (`deploy/` sets no `ssl_protocols`; certbot's default file normally does; check on the server). |

Status words used below: **SHIPPED** (built and live on `main`), **PARTLY** (built, but the claim goes further than the product), **NOT BUILT**, **WRONG** (the claim is false or contradicts what we store or promise).

## Decisions that bind this audit

| Decision (his words or the handoff) | Effect here |
|---|---|
| 2026-11-14: "we have the final say, what we've done overrides in many cases"; public pages must capture what we really offer; we update them to say what we do and who we are | **Ends** the 2026-11-03 rule "keep the designer's landing copy even where it runs ahead" (`docs/ui/parity-report.md`, "Copy that runs ahead of the product"; that table is also stale: almost every row there is built now). Claims below the SHIPPED line must be cut or reworded, not copied. |
| Pricing is Adebayo's: guess of about 20 h a month for about $15, shared team hours, nothing decided; standard user-centric plan rules wanted; Paystack (NGN) and Flutterwave (USD) both possible | Every number, limit and plan on the Pricing design is a **placeholder** (section 3). This audit proposes no prices. |
| CRM deferred, to be discussed later | Treated as NOT BUILT and out of scope; listed so the pages do not promise it. |
| Dashboard stays at `/` ("Keep it at /"); `/welcome` stays | Section 2. |
| Legal entity **Beyrichq LTD** trading as **Beyric**; `privacy@beyrictech.com` is the confirmed mailbox, `hello@` is not (`docs/ui/parity-report.md`) | The designs' footers say "Beyric Technologies" and `hello@`. |
| Privacy and terms may change without notice; pages must not promise notice | Keep out of FAQ copy. |
| Target market is **teams**, not individuals | Fits the new designs' positioning. |
| Recurring meetings follow the meetings; multi-team approach A plus Move; one team per tab; privacy wording must be read by his lawyer before `MULTI_TEAM_ENABLED` goes on | Sections 2 and 4 (R6). |

## 1. Landing design against the app

### 1.1 Claim by claim

`L-n` ids are mine. "Design" is the Landing file's own words, shortened.

| Id | Claim in the design | Status | Proof, or what is true |
|---|---|---|---|
| L1 | "Beyric writes the notes, assigns every action item to the right person, files it in the right sprint, and opens your next meeting with what is still unfinished" | PARTLY | Notes, owner matching by name, filing into project and sprint all built (`app/notes.py`, `app/tasks.py`, `app/filing.py`). "Every action item ... right person" overclaims: items with no clear owner wait under "Needs owner". The "opens your next meeting" part is the Series page plus an opt-in reminder mail (L12). |
| L2 | "Other note takers hand you a summary" / "A typical note taker ... leaves you to copy tasks into your tracker" | PARTLY | A comparison we cannot prove; the competitor benchmark in `docs/superpowers/specs/2026-11-13-multi-team-design.md` (section 11: Fireflies, Otter, Fathom) was about teams and pricing, not about what they push to trackers. Keep it soft or drop it. |
| L3 | Joins Google Meet, Microsoft Teams, Zoom | SHIPPED | Skribby guest join proved live for Teams and Zoom 2026-11-09 (`HANDOFF-master.md` 17th lead, section B). Caveats: Zoom hosts that block guests need a ZAK token we do not handle; Zoom retest after the bot-name change is still open. |
| L4 | Hero mock-up: "Notes ready", "8 tasks created, assigned to 3 people in Sprint 12", "Beyric Monday planning" | SHIPPED (as a sample) | Illustration only; the app's own status word is "Ready". Needs to be recognisably a sample. |
| L5 | "Works with the tools you use": Google Calendar | SHIPPED | `app/gcal_*.py`, `docs/fe-integration-guide-calendar-*.md` |
| L5 | ... Outlook | PARTLY | Built (`app/graph_*.py`, `microsoft_*.py`, PRs #148 to #157); switched on on the server 2026-11-13 but **never tested against real Microsoft** (checklist, handoff). Work or school accounts of other companies need Microsoft publisher verification first (handoff 18th lead, C2). |
| L5 | ... Slack | SHIPPED | One-way incoming webhook, per team (`app/slack.py`, `docs/fe-integration-guide-integrations.md`). "Works with" suggests more than that. |
| L5 | ... Linear | PARTLY | One-way: each new task becomes a Linear issue (title, due date, owner name, link). Not projects, sprints or milestones. |
| L5 | ... HubSpot, Jira, Asana | NOT BUILT | No code. Jira and Asana appear only as greyed options in `web/components/app/settings-tracker.tsx:17` and `settings-planned-sections.tsx:38`. |
| L6 | "Writes notes, decisions and open questions" | SHIPPED | `app/notes.py`, `app/extraction.py` |
| L7 | "Assigns each task to its owner, with a due date" | SHIPPED | Owner matched by name and alias; due date when one was said. |
| L8 | "Files work into projects, sprints and milestones" | SHIPPED | Projects (`app/projects.py`), sprints with roll-over (`app/sprints.py`: unfinished tasks move on with `carried`), milestones (`app/api_projects.py`). |
| L9 | **Ask your meetings** ("across every meeting your team has had"; answer plus the moment it was said, with `00:12`, `09:30` chips) | PARTLY | Ask Beyric is built (#133, #134, #140; `app/ask.py`). It answers from the meetings **the asker may read**, not every meeting of the team. It links to the exact **line** of the transcript; it shows **no time** (`fe-integration-guide-ask-beyric.md:291`). The time chips are WRONG. |
| L10 | **Suggested tasks** with a confidence level, the reason and the quote ("09:48 Daniel: ..."); Confirm / Dismiss | SHIPPED | #135, #136 (`docs/sop/2026-11-08-suggested-tasks.md`). The `09:48` time is not stored (`fe-integration-guide-suggested-tasks.md:77`): drop the time, keep the quote. |
| L10 | "Words it could not hear clearly go to a review list" | SHIPPED | Review screen (audit rows B1 to B3, `docs/ui/audit-2026-11-07-main-v2.md`). |
| L11 | **Decisions log**: Approved / Proposed / Replaced / Rejected, "replaces", who made it, revisit date, searchable | SHIPPED | `app/models.py:90`, #137, #139; the Decisions page has a search (`decisions-view.tsx`). "Replaced" and "Rejected" are set by hand, not detected (comment at `app/models.py:89`): the copy "when the team changes its mind, the old decision is marked as replaced" is true only if a person marks it. |
| L12 | **Recurring meetings**: carried tasks and open questions; "topics that keep coming back without a decision" and a suggested agenda | SHIPPED | #141, #142 (`docs/sop/2026-11-08-keeps-coming-up-suggested-agenda.md`). |
| L12 | "Attendees get a short brief before the call" / "A pre-meeting brief sent to every attendee" | PARTLY | Reminder mails exist (`app/series_mail.py`) but go **only to active team members who attended one of the last three meetings**, only when the team switched reminders on (off by default), never to outside addresses. |
| L13 | Tasks board by project and sprint; unfinished work rolls into the next sprint; launch dates become milestones | SHIPPED | `app/sprints.py`, `app/projects.py`; milestones are proposed from what was said and confirmed by a person. |
| L13 | "Or send it all to Jira, Linear or Asana" | WRONG | Only **tasks** go to **Linear**. No projects or sprints leave the app; Jira and Asana are not built. |
| L14 | "Joins on its own": every meeting with a video link or only chosen ones | SHIPPED | Rule picker and per-event switch (`app/gcal_rules.py`; auto-join on by default, decision 2026-11-04). Outlook half: see L5. |
| L14 | Transcript "with who said what **and when**, matched to teammates even when they join under another name" | PARTLY, "when" is WRONG | Speaker names come from Skribby's tags and Meet's names, with aliases (`app/display_names.py`). The measured meeting 34 had 5 tags for 7 speakers (handoff 19th lead). **No time per line is stored.** |
| L14 | Templates and vocabulary; "client calls, interviews and team updates" | SHIPPED | Seeded templates General, Client call, Team update, Interview (`alembic/versions/0001_initial.py:22`); vocabulary list. Caveat: Skribby does not list custom vocabulary for ElevenLabs (today's model; handoff 19th lead, B), so the list works through the after-the-fact vocabulary pass (#173, `docs/sop/2026-11-13-skribby-vocabulary-pass.md`), which was tested with a one-term list only. |
| L14 | "Customer calls update HubSpot ... next step, close date and amount suggested for you to approve" | NOT BUILT | See gap 1. |
| L14 | Share by email, PDF or Word, "read them in another language" | PARTLY | Share and export built (`app/export.py`, #99). Translation is **notes only, French and Spanish only** (`app/translation.py:56`). |
| L14 | "Invite teammates as owners, admins or members, and send task updates to Slack" | SHIPPED | `app/models.py:27`, `docs/fe-integration-guide-team.md`. |
| L15 | Who it's for: Product ("Sprints, Milestones, Jira, Linear or Asana"), Sales (HubSpot), Founders (Decisions, Ask), Hiring (Interview template, "without a verdict written for you") | PARTLY | Product: see L13. Sales: HubSpot NOT BUILT. Founders: SHIPPED. Hiring: the template tells the model not to give a verdict (`alembic/versions/0001_initial.py:18`); that is an instruction, not a guarantee. Recording candidates is also a legal subject (section 6). |
| L16 | Security card: "Encrypted in transit with TLS 1.2 or higher **and at rest with AES-256**" | **WRONG** | No disk or database encryption is set up (`docs/sop/2026-11-02-public-pages.md:21`). Only stored credentials are encrypted: calendar tokens, the Slack webhook, the Linear key (`app/vault.py`; Privacy "Security" section). TLS: not verified from the repo. The live landing says the same line and also "Encrypted end to end" (`web/components/landing/content.ts`); both are the riskiest lines before Google verification (checklist, "Landing line at rest with AES-256"). |
| L16 | "Never used for training ... not used to train any model, ours or anyone else's. There is nothing to opt out of" | PARTLY | Our policy says our providers do not train on the data, **relying on OpenAI's API terms** (vendor-stated, `privacy-content.tsx:77`). Skribby and the transcription provider behind the chosen model (ElevenLabs today, `SKRIBBY_TRANSCRIPTION_MODEL`) are not named on the policy at all, and I have not read their terms for this audit. "Anyone else's" is absolute. |
| L16 | "The note taker appears in the participant list **under a clear name** while it records, and anyone can ask the host to remove it" | PARTLY | True of the default name "Beyric Note Taker (recording)". Since #189 each user can set their own bot name (up to 50 characters, `app/bot_names.py`), so the name is not guaranteed clear. A chat notice exists but is **off** (`bot_chat_notice_enabled`, `app/config.py:251`, #188). |
| L16 | "You control retention: delete a meeting, export, close your account at any time. Audio is removed automatically after a set period" | PARTLY | Our copy of the audio goes after **7 days** (`app/config.py:320`), one setting for everyone, not per team. Skribby keeps debugging data 30 days that we cannot delete. Meetings moved to a team stay with the team when the recorder leaves (Privacy "How long we keep information"). |
| L17 | FAQ "Which meeting platforms": Meet, Teams, Zoom; paste a link or connect Google Calendar or Outlook | SHIPPED (with L3 and L5 caveats) | |
| L17 | FAQ "How are tasks assigned": "Items with no clear owner wait under Needs owner" | SHIPPED | `docs/fe-integration-guide-tasks.md` |
| L17 | FAQ "What if the notes get something wrong?" | SHIPPED | See L10. |
| L17 | FAQ "Can we keep using Jira, Linear or Asana? **Yes. Tasks, projects and sprints can be sent**" | WRONG | See L13. |
| L17 | FAQ "Does it update our CRM? **Yes, if you use HubSpot** ... Internal meetings are never sent" | NOT BUILT | |
| L17 | FAQ "Do participants know they are being recorded? Yes ... You are responsible for telling attendees and getting any consent" | SHIPPED | Matches Terms ("Recording laws and consent", `terms-content.tsx`) and the sign-up consent box (#187). It is not true that participants are always told: the chat notice is off. |
| L18 | CTA "Free to start" | PARTLY | Terms: "The Service is free during the pilot" with an hours limit (`terms-content.tsx:30`). Sign-up is capped at 10 accounts, so "Get started free" can lead to the "pilot full" screen. |
| L19 | Footer "© 2026 Beyric Technologies", `hello@beyrictech.com` | WRONG | The legal name is Beyrichq LTD; the confirmed mailbox is `privacy@` (decisions above). |

Nothing in the three designs claims SOC 2, ISO 27001, GDPR or HIPAA compliance, SSO, audit logs or a language list for transcription (I searched all three files for those words). Pricing uses "Unlimited" three times and "forever" once.

### 1.2 The new Landing against today's live page

The live page (`web/components/landing/landing.tsx`, words in `content.ts`) is the **2026-11-02 designer file**. The new file is a different page, not a retouch.

| Part | Live now | New design | What it means |
|---|---|---|---|
| Header links | How it works, Features, Security, FAQ; Sign in; "Get started" | Product, Who it's for, **Pricing**, Security; Sign in; "Get started free" | "Pricing" leads nowhere: there is no `/pricing` route (`web/app/` has none; `sitemap.ts` lists only `/`, `/privacy`, `/terms`). |
| Hero | Pill "New: Tasks, sprints and backlogs..."; "joins your calls, writes clear notes..."; "Get started free" | Pill "The note taker that follows through"; longer lede (L1, L2); "Get started free" plus **"See the app"** | "See the app" links to the prototype file (not a page); the live page already dropped it on purpose. |
| Tools strip | Google Calendar, Outlook, Slack, Jira, Linear, Asana | Adds **HubSpot** | L5. Live strip already lists Jira and Asana that do not exist. |
| Body | "How it works" in 3 steps; dark "Beyond the notes" with 6 cards | "Why Beyric" comparison; **5 deep dives** (Ask, Honest about what it heard, Decisions, Recurring, Tasks and sprints); dark "basics" with 6 cards; **Who it's for** (4 cards) | Most deep-dive features are built (L9 to L13), which makes this a better page than the live one, if trimmed. |
| Security | 4 cards ("Encrypted end to end"...) | 4 cards (L16) | Both need the same fixes. |
| FAQ | 5 questions | 7 questions (adds the CRM, "how is this different", "what if notes are wrong") | |
| Closing and footer | "Create your account" plus "Sign in"; footer Beyrichq LTD, `privacy@` | "Create your account" plus **"See pricing"**; footer "Beyric Technologies", `hello@`, Product / Company columns | Keep our entity and mailbox. |
| Signed-in view | `/welcome` shows the same page with "Open the app" (#177) | Not drawn | Keep our behaviour. |

Other live public files that now disagree with the product (no design involved): `web/app/llms.txt/route.ts` says "private pilot ... there is no public sign-up yet" and "Coming soon: connect Google Calendar"; both are out of date (sign-up opened 2026-11-13, calendar is built). The JSON-LD offer says price 0 "Free during the private pilot" (`web/components/site/json-ld.tsx:26`).

## 2. Dashboard (the app file) against the live `/`

### 2.1 What is new in this file

Only CRM: a sidebar item "CRM" with a "New" badge (under Configure), a **CRM screen**, and a **HubSpot panel** on the meeting page (shown "Sent" or "Not sent to HubSpot" with "Add as a contact" and "Send to a contact"). Everything else is word for word the 7 Nov design, already built and proved (`docs/ui/audit-2026-11-07-app.md`, `docs/ui/parity-report.md`).

### 2.2 Every block of the Meetings screen

"Matches" rests on the 7 Nov parity audit plus the header comments of each component (I read the code and its comments, not every pixel in a browser).

| Block (design) | Live `/` | Verdict |
|---|---|---|
| Sidebar: logo, Main (Meetings, Ask Beyric, Tasks, Projects, Decisions, Review, Team) | All present (`web/components/app/shell.tsx:66-73`) | Matches; ours **adds** Notifications (with unread count) and, while multi-team is on, a **Teams** link and the **team switcher** (`team-switcher.tsx`) |
| Sidebar: CONFIGURE (Templates, Vocabulary, Settings) | Present | Matches |
| Sidebar: **CRM** with "New" | Absent | NOT BUILT, by decision (deferred) |
| Sidebar: user card with log out | Present (`shell.tsx:84-98`) | Matches |
| Heading "Meetings" and its line | `PageHead` (`web/app/page.tsx`) | Matches |
| New meeting card: Meeting link, Notes template, "Send note taker", "Tell participants ..." | `new-meeting-form.tsx` | Matches; ours **adds an optional title** (pasted links have no calendar name) and the busy line |
| "Join meetings automatically" band (no calendar) | `calendar-upcoming.tsx` | Matches |
| Upcoming list (calendar connected): account chip, rule link, per-row switch | Same | Matches; ours **adds** "Not recorded" with Send now (#145, #146) and the "add note@ to your event" tip (#164) |
| Recurring cards ("Next:", unfinished, questions) | `recurring-cards.tsx` | Matches |
| Recent list: icon, title, date and template, task label, status pill, chevron | `meeting-list.tsx` | Matches; ours **does better**: day headings (#176), paging for older meetings, a refresh while a meeting is being worked on, real status words, and a **date filter** (From / To, #186) the design does not have |
| (not drawn) | Recording-consent card for older accounts (#187); worker-stale alert; phone drawer and top bar | Ours only. Keep. |

### 2.3 Conflicts and cautions

| Item | Detail |
|---|---|
| Dashboard stays at `/` | The design file has no routing opinion (its logo goes to its Meetings screen); nothing in the new file moves the dashboard. No conflict. |
| Multi-team switcher and Teams page | The design has neither. Our shell puts the switcher at the top of the sidebar and a Teams link in the Main group. A CRM item would need a slot in this layout; keep CRM out until decided. |
| "Team" versus "Teams" | Design has one "Team" item. We keep both (`/team` is the active team's members; `/teams` lists and makes teams). |
| CRM and teams | The CRM design assumes one team ("Meetings with someone outside your team", "Connected to the Beyric Technologies account"). With several teams per person, which team is "your team" is undefined; Slack and Linear are already refused on a personal team (stage 3, 409 `personal_team`); HubSpot would need the same rule and a per-team connection. Do not build CRM before the team rules are settled. |
| Date filter, day headings | Not in any design; both stay. The new file has no list controls, so nothing to merge. |
| Sample data | The new file's demo meeting is "Client call: quarterly review" with Harbour Logistics, Ngozi Eze, a $24,000 budget. It exists to show the HubSpot flow; do not copy it into the product or the landing. |

Other screens of the app file (Tasks, Projects, Series, Decisions, Review, Team, Settings, Templates, Vocabulary, Ask Beyric, meeting page) are unchanged from 7 Nov apart from the HubSpot panel, so their parity status is whatever the 7 Nov audit and the later batches recorded.

## 3. Pricing design: every plan, price, limit and promise

There is **no pricing page, route or entitlement code** in the app. "Placeholder" below means: replace by Adebayo's decision; nothing here is a recommendation. `$00` is already a placeholder in the design.

### 3.1 Plans

| Item | Design says | Mark | Can we honour it today? |
|---|---|---|---|
| Plan names | Free, Team ("Most teams pick this"), Business | PLACEHOLDER | No plan object exists. His own guess so far: a first paid plan of about 20 hours for about $15; shared team hours; nothing decided. |
| Free: "$0 forever" | PLACEHOLDER (price and "forever") | A permanent free tier is **not decided**; the lead's undecided billing proposal had no permanent free tier (a one-time trial instead). Today's real rule is a lifetime 5-hour cap per account during a pilot with a 10-account cap. |
| Free: "A set number of meetings each month" | PLACEHOLDER (limit) | Wrong unit: we meter **recorded hours, not meetings, and not per month** (`app/usage.py`). The cap is a lifetime ledger. |
| Team: "$00 per person, per month" | PLACEHOLDER | "Per person" is a seat model we do not have; his idea is shared team hours. |
| Team: "Unlimited meetings" | PLACEHOLDER, WRONG as drawn | Cost is 0.56 to 0.83 USD per meeting hour (estimates, handoff), so unlimited has a real cost; he leans to an hours allowance. Do not print "unlimited". |
| Business: "$00 per person, per month", "Talk to us" (mailto `hello@`) | PLACEHOLDER | `hello@` is not a confirmed mailbox. |
| Business: "Everything in Team" plus HubSpot sync | NOT BUILT | Gap 1. |
| Business: "Send tasks to Jira, Linear or Asana" | PARTLY | Linear tasks only; Jira and Asana NOT BUILT. |
| Business: "Milestones across projects" | SHIPPED, but gated wrongly | Milestones exist for every account today. Making them Business-only means removing them from others. |
| Business: "Admin roles and workspace controls" | PARTLY | Owner, admin, member exist (`app/models.py:27`); there is no SSO, audit log or workspace console. |
| Business: "Custom audio retention period" | NOT BUILT | One global 7-day setting (`audio_retention_days`). A per-team setting is new work and the Privacy page quotes the global number. |
| Business: "Priority support" | NOT BUILT | No support process or mailbox is defined. |
| Free "Calendar auto-join: No" (comparison row) | CONFLICT | Decision 2026-11-04: auto-join is on for everyone who connects a calendar ("users connect and leave"). Gating it contradicts that decision. |
| Free "Templates: General only" | NOT BUILT | All templates are open to all. |

### 3.2 Comparison table (16 rows): what each row needs

| Row | Today | Gating needed |
|---|---|---|
| Notes, decisions, action items; transcript with speaker names; PDF and Word export | SHIPPED for all | none |
| Calendar auto-join, Tasks/projects/sprints, Decisions log, Recurring memory, Ask, Templates and vocabulary, Slack | SHIPPED for all | **Plan gating does not exist.** Each of these is available to every account. |
| Meetings each month: Limited / Unlimited / Unlimited | Hours cap, not meetings | New metering model (see 3.1) |
| HubSpot sync; Jira, Linear or Asana sync; Custom retention period | NOT BUILT / PARTLY / NOT BUILT | New features |
| Milestones | SHIPPED for all | Would need to be removed from lower plans |
| "Data used to train models: Never" on every plan | PARTLY | Vendor-stated (section 1.1, L16) |

### 3.3 FAQ and other promises

| Promise | Mark | Note |
|---|---|---|
| "Anyone on your team who signs in to Beyric counts. People who only attend meetings or receive notes by email are free." | PLACEHOLDER | A seat definition is a pricing decision; mail recipients outside the team are real today (shares, reminders only to members). |
| "Change or cancel any time; access until the end of the period you paid for; export before you go" | PLACEHOLDER | Needs a billing provider and period logic. Export exists. |
| "If I move back to Free my notes, decisions and tasks stay readable and exportable; paid features stop for new meetings" | PLACEHOLDER | A policy decision; not coded. |
| "Discounts for small teams or startups: write to hello@" | PLACEHOLDER | Business decision, unconfirmed mailbox. |
| "Start free", "It is free to start", "Get started free" | PARTLY | Terms say free during the pilot with an hours limit; terms may need a paid-plan section and the lawyer. |
| "Your data is never used to train a model, on any plan" | PARTLY | Section 1.1 L16. |
| No mention of currency, tax (Flutterwave VAT is 7.5% on fees per his note), refunds, invoices or NGN | missing | Needed before any price is shown; currency routing (NGN versus USD) is still unanswered. |

## 4. Privacy and legal risks in public claims

| # | Risk | Where | Why it matters |
|---|---|---|---|
| R1 | **Privacy Policy says no other company gets the recording; Skribby gets it.** Sub-processor list lacks Skribby and its transcription sub-processors; Skribby's 30-day debugging data is not disclosed | Live `/privacy` line 87; checklist line 447 | A false statement on a page Google reads for OAuth verification and users rely on. Lawyer-read wording needed. Already tracked, still open. |
| R2 | **"Encrypted at rest (AES-256)" and "Encrypted end to end"** | Live landing now; new Landing; checklist line already flags it | A security claim we cannot prove is the classic complaint and a verification risk. Replace with what is true (TLS in transit as far as verified; credentials encrypted before storage). |
| R3 | **"Visible under a clear name"** while users can type any bot name; **chat notice off** | New Landing; #189, #188 | Recording consent: the notice that tells participants is not switched on. Either switch it on after its live proof (checklist) or soften the claim. |
| R4 | **Recording consent in the claims** ("Participants know ... you are responsible") | FAQ | Consistent with Terms and the sign-up box (#187); keep that exact allocation of duty. A hiring card invites interview recording, where candidate consent and employment law bite harder; consider a plain line. |
| R5 | **Data deletion**: "Delete a meeting ... close your account ... Audio removed after a set period" | Security card | Partly true (7 days, our copy only; Skribby's 30-day debugging data; moved-to-team meetings stay with the team after the recorder closes their account). |
| R6 | **"Former member"** | Not in any of the three designs | But the Privacy and Terms on `main` already carry the multi-team wording and "Former member" (#191, merged although its title says "do not merge before the lawyer reads it"; whether its deploy ran is not checked) while `MULTI_TEAM_ENABLED` is off. Ask whether the lawyer has read it. Public pages must not promise that account deletion deletes a moved meeting. |
| R7 | **"Never used for training ... nothing to opt out of"** | Landing and Pricing | Rests on OpenAI's terms; unverified for the other processors. Say "our providers do not train on it under their terms" only after each processor's terms are read and named. |
| R8 | **Outlook** listed as a connection | Tools strip, "Joins on its own" | Built, untested, and the Microsoft consent screen shows an unverified publisher until Partner Center verification. |
| R9 | **Comparison with "other note takers"** | Landing | Unprovable claims about competitors invite complaint; keep to what we do. |
| R10 | **Pricing promises** (cancel, refunds, "forever", "unlimited", retention) | Pricing | Become contract terms the moment a plan sells. Terms and Privacy need a paid-plan section and a lawyer read. |
| R11 | **Entity and mailbox** | Footers | "Beyric Technologies" is not the legal entity; `hello@` is unconfirmed. |

## 5. Gaps, ranked by effort

S = a few hours, M = about a day, L = several days, XL = a project with its own spec.

| Rank | Gap | Effort | Notes |
|---|---|---|---|
| 1 | Remove AES-256 and "end to end" from the live landing | S | Copy only; do not wait for the redesign |
| 2 | Privacy page: add Skribby and its transcription sub-processors, fix "no other company" line | S to M | Lawyer read; already on the checklist |
| 3 | `llms.txt` and JSON-LD say "no public sign-up", "coming soon: calendar" | S | Copy only |
| 4 | New Landing built from the design with every claim cut to SHIPPED (drop HubSpot, Jira, Asana, times, "every attendee", "unlimited") | M | Web only; reuse `content.ts` pattern and existing components |
| 5 | Footer entity and mailbox in the new pages | S | Decision, then copy |
| 6 | `/pricing` page with plan names and "to be announced" values, sitemap, header link | M | Static; waits for his answers (section 6) |
| 7 | Chat notice live (consent) | S to M | Built (#188), off; needs one live proof first |
| 8 | Keep "clear name" honest: constrain or label user bot names | S to M | Product decision |
| 9 | Store transcript turn times (to allow "00:12" chips and "when") | L | Backend first; Skribby's per-segment times exist only in the 7-day evidence table |
| 10 | Translation beyond French and Spanish; languages claim | M each | A list entry plus an eval per language (`app/translation.py:55`) |
| 11 | Per-team audio retention | M | Setting, sweep, Privacy wording |
| 12 | Jira and/or Asana sync | L each | Linear is the pattern (`app/linear.py`, `docs/sop/2026-11-06-w10-linear-tracker.md`) |
| 13 | Plans, shared team hours, entitlements and gating | XL | New spec: plan model, hours pool per team, per-feature gates, downgrade rules, terms |
| 14 | Billing: Paystack (NGN) and Flutterwave (USD) behind one provider layer, VAT, invoices, cancel and refund | XL | Needs his currency decision and the real-cost query |
| 15 | HubSpot / CRM (OAuth, contact matching, deal-field proposals with approval, privacy wording, review by HubSpot) | XL | Deferred; also needs team rules first (section 2.3) |

## 6. Yes or no questions for Adebayo

1. Fix the two false security lines (AES-256, "end to end") on the **live** landing now, as a copy-only PR, before any redesign?
2. Fix the Privacy Policy now (add Skribby and its transcription sub-processors, correct "no other company receives the recording") and send it to the lawyer with the multi-team wording?
3. Has the lawyer already read the multi-team wording that #191 put on the live Privacy and Terms pages?
4. Build the new Landing but **only with what is shipped** (no HubSpot, Jira, Asana, no timestamps, no "every attendee", no "unlimited"; Linear and Slack stay, with Outlook worded as it is)?
5. Keep the designer's "who said what and when" out of the copy until we store turn times?
6. Keep "Beyrichq LTD" and `privacy@` in the new footers (and not "Beyric Technologies" and `hello@`)?
7. Ship a `/pricing` page before prices are decided (plan names and "price to be announced")? A "no" means the header "Pricing" link waits.
8. Is **Free / Team / Business** the plan structure you want to start from?
9. Do you want any permanent free plan at all (versus a one-time trial)?
10. Should plans be counted in **hours** (shared per team) rather than "unlimited meetings"?
11. Keep calendar auto-join available on every plan (your 2026-11-04 principle), and gate other features instead?
12. Keep CRM, the CRM sidebar item and the HubSpot meeting-page panel **out of the product and out of every public page** until the CRM discussion?
13. Switch on the bot's chat notice (#188) after one live proof, so "participants know" is true?
14. Keep users' free-text bot name, and reword the landing to "a name you can see" instead of "a clear name"?
15. Keep the "Hiring teams" card (recorded interviews)?
16. Say "Free during the pilot" (not "free forever" or "free to start") on every page until prices exist?

## 7. Proposed build order

| Step | What | Depends on |
|---|---|---|
| 0 | Copy-only hotfix PR on the live pages: security card lines, `llms.txt`, JSON-LD. Separate PR: Privacy Policy sub-processor and "no other company" fix, marked for the lawyer | Q1, Q2 |
| 1 | Adebayo answers section 6 | none |
| 2 | **Landing v2** from the new design, claims trimmed to SHIPPED, our footer, our signed-in `/welcome` behaviour; update `content.ts`, its mock-ups and tests; one SOP | Q4 to Q6, Q13 to Q16 |
| 3 | `/pricing` page with placeholders and honest "pricing to be announced", added to nav and sitemap (only if Q7 is yes) | Q7, Q16 |
| 4 | Billing spec (plan model, shared team hours, gating, downgrade, provider layer for NGN and USD), then the real cost report already running in session `cost-report` | his answers to Q8 to Q11 |
| 5 | Build plans and billing, then fill the real numbers into the pricing page; lawyer reads terms for paid plans | step 4 |
| 6 | Smaller product gaps by value: chat notice live, turn times, per-team retention, translation languages, Jira or Asana | each its own decision |
| 7 | CRM discussion; if it goes ahead, a spec after the team rules are final (section 2.3) | his CRM decision |
| 8 | Recheck every public claim against the app once more before the Google OAuth verification submission (Phase V; checklist line "Revisit the designer's landing and sign-in copy") | all above |

Dashboard: no build step. The only new thing in that design is CRM (step 7).
