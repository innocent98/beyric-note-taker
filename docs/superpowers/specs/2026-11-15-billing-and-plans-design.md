# Billing, plans and feature gates: design spec (per-person model)

Status: REVISED 2026-11-16, fourth version. The per-person model (a plan belongs to a **person**, not a team) was decided on 2026-11-15; on 2026-11-16 Adebayo answered the last five questions with "Yes, all five", so **no yes/no question is open any more**. What he decided is marked DECIDED with the date. **Nothing here is built.** No application code, no migration and no endpoint come with this document.

Facts about today's code were read from `origin/main`; every `file:line` was re-checked on 2026-11-16 against `cf140748` (Alembic head **0048**, `0048_series_ignored.py`, so the next free number is **0049**). Facts about vendors and competitors were read from the vendor's own page on 2026-11-15; each names its URL, and section 12 lists what could not be confirmed. "PROPOSED" means a detail nobody decided yet (build details, not product questions). "DECIDED" means Adebayo's word, in the handoff (`docs/HANDOFF-master.md`, 2026-11-14 23:45 block, section B) or in his answers of 2026-11-15 and 2026-11-16 relayed by the 21st lead.

## State of this spec (2026-11-16): decided, open, first stage to build

**Decided** (his words, 2026-11-15 and 2026-11-16, "Yes, all five" for the last five):
- **Model:** a plan belongs to a person; teams only share; the recorder is charged; Ask Beyric, new translations, rewriting notes, new recordings and calendar auto-join need a live plan or trial (sections 1 to 3).
- **Hours rules:** once per meeting, failed bots zero, 80% warning, at 100% running recordings finish and new ones stop, monthly, no rollover, price change at renewal, 3-day grace with no new hours (section 3).
- **Trial:** about 3 hours, once per person, no card, ends at the 3 hours or 14 days, whichever is first (3.8). **Pilot accounts:** keep their leftover hours, which expire 60 days after paid plans open, with one mail before; a lawyer item (3.9).
- **Price:** the first plan is **18 USD for 20 hours a month**, decided now but **not printed on `/pricing` until the one-transcript flip has produced real cost data** (the flip went live 2026-11-16 12:20 WAT). A second tier of about 34 USD for 40 hours is a suggestion only; plan names are still to choose (4.2, 10).
- **Organizer's bot preferred** when teammates share a calendar event: decided, **built last** (stage S12), only after a three-account live test (2.5, section 8).
- **Packs:** 10 hours for about 10 USD per person, valid 12 months, only with a live plan; after a lapse leftover pack hours allow recordings but not Ask, translation or rewriting (4.3).
- **Cancel and refunds:** cancel any time with access to the end of the paid month and no part refund; refund only a double charge, a first payment within 7 days with no hours used, or an unused pack within 14 days; the lawyer reads it before launch (3.4, 3.5).
- **Money:** Paystack (naira) and Flutterwave (dollars) behind one layer, routed by the billing country the person chooses; our own billing clock; both accounts are new; test keys in a local env file only (section 5).

**Open:** **no yes/no questions.** Open facts only (section 11): his test keys; Beyrichq's tax and registration numbers and whether it charges VAT; the naira price list and rate; the measured cost per hour after the flip; the lawyer's list (the six from #198 plus section 9) and the accountant's; the three-account live test.

**First stage to build: S1, the hours model in shadow** (section 8, "S1 in detail"): migration 0049 (hour grants and allocations, pilot backfill), `app/entitlements.py`, a logging-only shadow comparison and a read-only report script, behind `HOURS_MODE=shadow`, with no change in behaviour while the flag is off. It needs no keys, no vendor and no real money. S4 (the provider layer on a fake provider) touches different files and can run after S1 or beside it, one writer per set of files.

## 0. Read this first

**The model in plain words (DECIDED 2026-11-15).** If you want to use the note taker, you subscribe. It is your bot that joins your meetings, and the hours are yours. You can create a team and invite friends. The meeting's notes, transcript, summary, decisions and tasks you may **share** with that team, and teammates can read what is shared **without subscribing**. Ask Beyric, translation and rewriting notes do not work for anyone whose trial is finished, who never subscribed, or whose subscription has expired. If a friend also wants those features after the 3-hour trial, the friend pays too. Why he dropped the shared team pool: a teammate could use up hours the owner thinks he bought, and the owner could not explain where they went.

**What this is for.** Today every account gets every feature and a lifetime cap of 5 recorded hours, and nobody can pay. This spec says how a person pays for hours, how those hours are counted, what each person can and cannot do with and without a plan, what happens at the limits, and how money gets in through Paystack (naira) and Flutterwave (dollars), in small safe steps.

### What is decided and what is only proposed

| Topic | Status | Where |
|---|---|---|
| A plan belongs to a **person**; the hours are that person's; no team pool, no member limit on a plan, no team of one, no hours team | **DECIDED 2026-11-15** ("Yes, per person") | 1.4, 2 |
| Teams stay a place to share: a member reads shared meetings, transcripts, notes, decisions and tasks without paying | **DECIDED 2026-11-15** | 1.4, 2.2 |
| Hours are charged to the **recorder** (the person whose bot recorded the meeting) when the recording is prepared; sharing or moving a meeting later never moves the charge | **DECIDED 2026-11-15** | 3.1 |
| Ask Beyric, new translations, rewriting notes, new recordings and calendar auto-join stop for anyone with no plan, an expired plan or a used-up trial; reading, search, export, tasks, decisions and delete stay | **DECIDED 2026-11-15** | 2.2, 2.3 |
| Hours counted once per meeting, failed bots count zero (Q2); 80% warning and at 100% running recordings finish and new ones stop, now **per person** (Q3); monthly billing, no rollover, packs, a price change applies at renewal (Q4); naira for a Nigerian billing country and dollars elsewhere (Q6) | **DECIDED 2026-11-15** | 3 |
| Grace is **3 days** after a failed renewal and **no new hours until payment succeeds**, now per person | **DECIDED 2026-11-15** | 3.3 |
| Both providers, Paystack for naira and Flutterwave for dollars, behind one layer, routed by the **billing country the person chooses at first purchase** and saved on their profile; we keep the billing clock ourselves; both accounts are new and made for Beyric | **DECIDED 2026-11-15** | 5 |
| Trial: about 3 hours per person, no card, once per person (email fingerprint); the 10 pilot accounts keep what is left of their 5 hours as a pilot grant | **DECIDED 2026-11-15** | 3.8, 3.9 |
| The trial ends when the 3 hours are used **or 14 days after sign-up, whichever comes first** (his words: "yes, good") | **DECIDED 2026-11-15** | 3.8 |
| The pilot accounts' leftover hours expire 60 days after paid plans open, with one mail before | **DECIDED 2026-11-16** ("Yes, all five"); a lawyer item | 3.9 |
| First plan **18 USD for 20 hours a month**; **not printed on `/pricing`** until the flip has produced real cost data; second tier about 34 USD for 40 hours is a suggestion only; plan names still to choose | **DECIDED 2026-11-16** (price and hours); names and the second tier open as facts | 4.2, 10 |
| Organizer's bot preferred; built only after the three-account live test, as its own late stage (S12) | **DECIDED 2026-11-16** | 2.5, 8 |
| Packs: 10 hours for about 10 USD per person, valid 12 months, only with a live plan; after a lapse leftover pack hours allow recordings but not Ask, translation or rewriting | **DECIDED 2026-11-16** | 4.3 |
| Cancel any time with access to the end of the paid month and no part refund; refunds only for a double charge, a first payment within 7 days with no hours used, or an unused pack within 14 days; the lawyer reads it before launch | **DECIDED 2026-11-16** | 3.4, 3.5 |
| Test keys in a local env file, never in chat, never in git | **DECIDED 2026-11-15** | 5.7 |
| The word "free" is not used anywhere; no permanent free plan; Business plan not shown; calendar auto-join stays on for everyone who has hours | **DECIDED** (handoff) | 10 |
| Build details not listed (table names, flags, test shapes) | **PROPOSED** (the builder may adjust) | marked inline |

### The findings that matter most

| # | Finding | Why it matters |
|---|---|---|
| 1 | **The per-person model is almost what the code already does.** `usage.record_usage` charges the meeting's owner, the recorder (`app/usage.py:68-90`), the cap is per user (`app/usage.py:31`), and the join gate is one block in `placement.reserve_slot` that already reads the person's own hours (`app/placement.py:175-180`). The change is a monthly grant instead of a lifetime cap, plus plan states, not a new accounting system. | Sections 1 and 8. Smaller and safer than the pooled version. |
| 2 | **At 18 USD for 20 hours the margin holds on the cost we now expect, and the flip is live.** The real cost per meeting hour is the Skribby bot 0.35 USD plus ElevenLabs 0.20 USD = 0.55 (his words), plus a few cents of our own OpenAI, so 0.56 to 0.66 USD an hour. For 20 hours fully used, international card, net of fees: **+3.9 to +5.9 USD (22 to 33%)**, and still +2.9 USD (16%) if Skribby bills the model on the full bot time. At 15 USD the same team leaves +1.0 to +3.0. The flip's real cost is not measured yet. | Section 4.2. The price is decided but is not printed until a new cost report and the Skribby invoice confirm it. |
| 3 | **Several teammates with the same calendar event: today the first bot placed wins and the others are skipped.** That decides who pays. The rule is a database check (`app/placement.py:203-212`), written while on Vexa and not proved live under Skribby with three calendars. The skipped person gets a notice that is now out of date ("Sharing notes between accounts is not available yet", `app/join_runner.py:831-835`). | Section 2.5. **Decided 2026-11-16: prefer the organizer's bot**, built last, after a three-account live test (T1). |
| 4 | **The rewrite-notes route was missing from the gates, and it costs money.** `POST /meetings/{id}/regenerate` (`app/api.py:817`, `app/services.py:656`) calls the notes model on every press. Review's "add the corrected spelling" does not call a model (`app/api_workspace.py:297-315`), so it is not gated. | Section 2.3. |
| 5 | **Neither provider's own subscription feature gives a 3-day grace with our retries**, and the dollar side of settlement is unproven. | Sections 5.3 and 5.6. Decided: we keep the billing clock; each provider is proved in its own test mode. |

### Open

No yes/no question is open. Section 11 lists the open facts (keys, tax numbers, naira list, measured cost, lawyer and accountant lists, the live test).

## 1. What exists today, and what changes

### 1.1 Today's rule in plain words

A person has a lifetime allowance of recorded hours: 5 hours for every non-admin account by default (`app_state.default_hours_cap_seconds` = 18000), or a number an admin set for that person (`users.hours_cap_seconds`). The allowance never refills. The unit is seconds of recorded audio, the length of the recording the pipeline prepared. Waiting-room time and failed joins cost nothing. Deleting a meeting gives nothing back, and deleting an account and signing up again does not reset the hours (the ledger also keys on a one-way fingerprint of the email address).

### 1.2 Where it lives in code

| Part | Where | What it does today |
|---|---|---|
| The cap for one person | `app/usage.py:31` `effective_cap` | admin = no cap; else the person's override; else the app-wide default |
| What was used | `app/usage.py:41-66` `ledger_seconds`, `hours_used` | ledger sum for that person (by user id or email fingerprint) plus an estimate for the person's own meetings still running (time since the bot was sent, capped at 6 hours each) |
| Counting | `app/usage.py:68-90` `record_usage`, called from `app/pipeline.py:1053` (`_step_transcribing`) | one ledger row per meeting, written when the recording is prepared; idempotent per meeting; a failed bot that never produced audio is never counted |
| The ledger table | `app/models.py:812` `UsageEntry` (`usage_entries`) | `user_id` (set null on user delete), `email_hash`, `meeting_id` (unique), `seconds` |
| The warning | `app/usage.py:92-119` `_warn_when_low` | at 80% (`LOW_HOURS_FRACTION`) one in-app notice per person, once ever, "Most of your recorded hours are used". No mail |
| The stop | `app/placement.py:175-180` inside `reserve_slot` | refuses with `hours_cap_reached` (403) when used is at or above the cap, under the placement lock |
| Who calls it | `services.py:943` (pasted link), `send_now.py` (Send now), `join_runner.py:642` (calendar auto-join) through `place_bot` | one gate for all three |
| What the person is told | `services.py:540-553` `hours_cap_message`; `join_runner.py:810-830` `_SKIP_REASONS` and `_NOTICES` | the message says "for the pilot" and asks them to write to hello@beyrictech.com; a calendar skip raises one notification per skipped meeting (dedupe key `calendar.{reason}:{join.id}`) |
| What the screens read | `app/api.py:191-205` (`usage` in the session payload: `used_seconds`, `cap_seconds`) | the sidebar usage bar and the "hours used up" state |
| Admin | `app/admin.py:87-126, 188-194`, `app/api_admin.py:68-110`, `app/api_schemas.py:448-513` | per-person override and the pilot default |
| Sign-up brake | `app/signup.py:86` `slots_taken`, `app_state.max_accounts` (10) | the pilot account cap |
| Teams | `app/teams.py` (`MAX_OWNED_TEAMS = 3` at line 239, `create_owned_team` line 180, `default_membership` line 78), `app/visibility.py`, `app/team_invites.py:38-47` | membership, personal team, invitation caps |

### 1.3 What a plan changes

The rule is already per person, so the plan replaces the **number**, not the structure.

| Today | Becomes |
|---|---|
| A lifetime allowance of 5 hours per person (`effective_cap`) | The person's hours: a one-time trial, a monthly plan, packs, and for the pilot accounts a pilot grant (section 3) |
| `effective_cap(user)` returns a number or none | `entitlements.status(user)` returns the person's state (`trial`, `pilot`, `active`, `past_due`, `lapsed`, `none`) and hours left (section 2.1) |
| `hours_used` counts the person's own running meetings | Same: the person's own, which is now exactly the right set (no pool, so no teammates' meetings to count) |
| `_warn_when_low`: once per person ever, in-app only | Once per person per period at 80% and at 100%, in-app plus a mail to the person (section 3.3) |
| `hours_cap_message` ("for the pilot", asks them to write to hello@beyrictech.com) | A message naming the person's plan state and linking to the billing screen |
| Admin sets a number on a person | Admin grants hours to a person with a written reason (`hour_grants`, kind `admin_gift`); the platform admin stays uncapped |
| Every feature open to every account | Ask Beyric, new translations and rewriting notes need a live plan or trial (section 2.3) |

### 1.4 Teams stay what they are: a place to share

From the multi-team design (`docs/superpowers/specs/2026-11-13-multi-team-design.md`, approved) and `app/teams.py`: every person has one permanent personal team that nobody else can join (Slack and Linear are refused on it with 409 `personal_team`) and may belong to several other teams (a person may own up to 3, `app/teams.py:239`). A meeting starts private and its recorder shares it to one team or moves it to one.

**Nothing in teams changes for billing (DECIDED 2026-11-15).** There is no team pool, no member limit that comes from a plan, no "team of one", no "hours team" and no payer of a team. A team member reads the meetings shared or moved to the team, with their transcripts, notes, decisions and tasks, whether or not they subscribe; the one visibility rule in `app/visibility.py` is untouched. What a plan decides is what a **person** may do: record, auto-join, ask, translate, rewrite. A solo person needs no team at all. The existing team limits (`MAX_OWNED_TEAMS`, invitation caps in `app/team_invites.py`) stay as abuse guards and are not tied to a plan.

## 2. The plan model, states and where each gate lives

### 2.1 The model (PROPOSED shape, DECIDED principle)

Plans are rows in a table, not code: a plan has a code, a name, monthly hours and one price per currency. A person has at most one live subscription with a status. Hours are held in grants (a trial, a pilot grant, a plan period, a pack, an admin gift), and every counted recording is split across the person's grants in a fixed order (section 3.2). One small module, `app/entitlements.py`, answers every question ("may this person start a recording?", "may this person ask Beyric?", "how many hours are left?"). No other code compares plan names or statuses, the same way `app/visibility.py` is the only place that decides who may read a meeting.

### 2.2 What a person can do in each state (DECIDED 2026-11-15)

| State | Who | Can do | Cannot do |
|---|---|---|---|
| `trial` | a person in the first 3 hours or 14 days after sign-up (the 14 days DECIDED 2026-11-15) | everything | new recordings once the 3 hours are used; everything in the next row once the trial has ended |
| `pilot` | the 10 pilot accounts while a pilot grant has hours (until it expires, 60 days after paid plans open) | everything | same as above |
| `active` | a person with a paid plan | everything: record, calendar auto-join, Ask Beyric, translate, rewrite | new recordings while the plan's hours and packs are used up (Ask, translate and rewrite **keep working**: they use no recorded hours) |
| `past_due` | the 3 days after a failed renewal (section 3.3) | read, search, export, tasks, decisions, Ask, translate, rewrite, settings, billing; record **only from hours still left** | the new period's hours (granted only when payment succeeds); change plan |
| `lapsed`, `none`, or a trial that has ended | no plan, an expired plan, a used-up or expired trial, or a person who never subscribed (for example a teammate who only reads) | **read** everything they are allowed to read (their own meetings and everything shared with their teams), **search**, **export**, tasks, decisions, settings, billing, **delete**; record only from paid pack hours that remain | **Ask Beyric, new translations, rewriting notes, new recordings and calendar auto-join** |

A teammate who never subscribes lives in the last row: they read what the team shares, search it, export it and use tasks and decisions. They cannot Ask Beyric, translate or rewrite until they subscribe, and their own bot never joins. This is what the model is for: a friend who wants the features pays.

Cost reasoning: recordings cost 0.5 to 0.9 USD an hour; Ask, translation and rewriting each call OpenAI per use; reading, searching, exporting and the board cost almost nothing, so they stay open. That keeps the promise "notes and export stay available".

### 2.3 Per-feature gates and where each check lives

All of them are checked against the **caller**: the person pressing the button, not the meeting's recorder. A team admin who rewrites a moved meeting's notes is gated by their own plan.

| Gate | Check goes here (file:line today) | Refusal | Notes |
|---|---|---|---|
| **Join: pasted link, Send now, shared link** | `app/placement.py:175-180` in `reserve_slot`: the block that reads `usage.effective_cap` and `usage.hours_used` becomes `entitlements.check_join(session, user, now)` | keep 403 `hours_cap_reached` (the frontend already knows it); add a `reason` field (`no_hours`, `no_plan`) | Runs under the placement lock, which puts two starts in order; it does **not** by itself stop one person starting two meetings on their last minute, because a meeting just placed counts as about 0 s until it has run a while. **Decided and built in S2b (2026-11-16):** under `enforce` a second concurrent meeting starts only if the hours left cover a reserve for each running or just-placed meeting (30 minutes, or its calendar event's length); a first meeting is unchanged (`docs/sop/2026-11-16-hours-reserve-and-capacity.md`). `services.placement_refusal` (`services.py:548`) maps it. |
| **Calendar auto-join** | the same block, reached through `join_runner.place_bot` (`app/join_runner.py:642`); skip reasons in `_SKIP_REASONS` (`join_runner.py:810`) | `hours_cap` stays; add `no_plan` | Connecting a calendar, the rule picker and the per-event switch are never gated. |
| **Ask Beyric** | `app/api_ask.py:82` route `POST /ask`, calling `ask.answer` (`app/ask.py:841`; the per-person daily limit `ask_daily_limit` is read at line 857) | 403 `plan_inactive` | The existing daily limit stays as a cost brake. Ask answers from what the asker may read, so a teammate's shared meetings are readable by Ask only if the asker has a plan. |
| **Translation (new requests)** | `app/api_translation.py:73` `request_translation` | 403 `plan_inactive` | Reading an existing translation is never gated. French and Spanish notes only today. |
| **Rewrite notes with another template (was missing)** | `app/api.py:817` route `POST /meetings/{id}/regenerate` (`regenerate_notes`, line 822) calling `services.regenerate_notes` (`app/services.py:656`), which calls the notes model each time (`notes_model`, recorded in `ai_runs` as kind `notes`). The legacy page route is `app/web.py:175` | 403 `plan_inactive` | Costs OpenAI on every press. Gate both routes in `services.regenerate_notes` so the legacy route cannot bypass it. |
| **Review: "add the corrected spelling"** | `app/api_workspace.py:299-315` `add_review_term` | **not gated** | It only adds the term to the person's vocabulary and marks the item checked; it calls no model (its docstring says the notes are not rewritten). The paid step is the "rewrite the notes" button that follows, which is `POST /meetings/{id}/regenerate` above. |
| **Export** (PDF, Word) | `app/api.py:519` `export_meeting` | not gated | Promise: export stays. |
| **Search, notes, transcript, tasks, decisions, series** | the existing reads | not gated | Reading costs almost nothing. |
| **Retry of a failed meeting** | `services.retry_meeting` (`app/api.py:804`) | not gated | The recording's hours are counted once per meeting (`usage.record_usage` is idempotent), so a retry never charges again; the small notes cost is accepted. |
| **Integrations** (Slack, Linear), teams, invitations | `app/api_integrations.py`, `app/teams.py`, `app/team_invites.py` | not gated | They belong to teams and cost nothing per use; they are not part of a plan. |
| **Sign-up** | `app/signup.py:86` `slots_taken`, `app_state.max_accounts` (10) | pilot full | Kept as the brake on trial give-aways while sign-up is open (section 3.8). |

### 2.4 What the screens read

`app/api.py:191-205` builds `usage` for the session (`used_seconds`, `cap_seconds`). Keep both names and fill them from the person's current grants so the existing sidebar bar keeps working, and add fields the frontend can use when it is ready (state, period end, the trial's end date, warning level). A new `docs/fe-integration-guide-billing.md` is built from live captures in the stage that adds each field (section 8).

### 2.5 Several teammates, one calendar event: who pays (what the code does today)

When two or more teammates connect calendars that hold the same event with the same meeting link, each one's join runner tries to send that person's bot. The code allows **one live bot per meeting code**: `reserve_slot` refuses another person's bot when a meeting on the same platform and code is already in the call (`app/placement.py:203-212`, a check in our own database, not in the bot provider), the others are skipped with `meeting_has_note_taker` and are not retried (`app/join_runner.py:94-102`), and the skipped person is told (`app/join_runner.py:831-835`). Two runners racing on the same code are separated by the unique index `uq_scheduled_joins_live_code` and the placement lock (`app/models.py:655`, `app/join_runner.py:654`). So **the first account placed pays**, and the others are charged nothing because their bot never recorded.

**Not proved live:** this was written while on Vexa and has not been tested under Skribby with three calendars connected. The first live test with three accounts is stage T1 (section 8); the organizer rule waits for it.

**Three things this rule leaves rough:** (1) "first placed" is a race, not a choice: the organizer's bot may lose to a guest's. (2) The skipped person's notice says "Sharing notes between accounts is not available yet", which stopped being true when teams arrived; the notes are visible to them only if the recorder shares the meeting (private by default, with an optional auto-share team per person). (3) The recorder alone is charged, which is what the model wants.

**Decided 2026-11-16: prefer the organizer's bot.** It is built only after the three-account live test and as its own late stage (S12, section 8): the organizer's bot goes on time, the other teammates' bots wait about two minutes and stay out if it is already in, and when the organizer has no account, no hours or no plan the first placed bot still records, as today. The skipped person's stale notice is fixed in the same stage. What the build can use: The data exists: each calendar event stores whether the person is the organizer (`CalendarEvent.organizer_self`, `app/models.py:621`, filled for Google in `app/gcal_sync.py:315`; for Outlook the query asks for `isOrganizer`, `app/graph_client.py:81`, but I did not check that it reaches `organizer_self`), and a rule "only meetings I organize" already exists (`app/gcal_rules.py:96`).

## 3. Hours accounting, limits, grace, downgrade, lapse, cancel, refund

Marked **DECIDED 2026-11-15** below: charging the recorder, once per meeting and failed bots at zero (Q2), 80% and 100% (Q3), monthly with no rollover, packs and a price change at renewal (Q4), the 3-day grace with no new hours. Everything else here is a build detail marked **PROPOSED**; the cancel and refund rules, the packs and the pilot expiry were decided on 2026-11-16.

### 3.1 What counts and who pays (DECIDED 2026-11-15)

| Question | Rule | Check against today's code |
|---|---|---|
| Unit | seconds of the recording the pipeline prepared (what we transcribe and pay for); shown as hours with one decimal | same as `app/usage.py` today |
| **Who is charged** | **the recorder**: the person whose bot recorded the meeting, at the moment the recording is prepared. Sharing, moving or unsharing the meeting later never moves the charge | `usage.record_usage` charges `meeting.owner_user_id` (`app/usage.py:77`), and a moved meeting keeps its recorder as `owner_user_id` (`app/visibility.py`, docstring) |
| Many people in one meeting | counts **once**: the recording is what counts, not the attendees; with several teammates' calendars the first placed bot is the recorder (section 2.5) | `placement.py` refuses a second bot into the same live meeting |
| Failed bots | count **zero**: nothing is counted unless audio was prepared | true today (`app/pipeline.py:1053` runs only when a recording exists). Our own cost is not zero: Skribby bills a bot from creation to its end, and a bot never admitted is free only for the first 10 minutes of waiting (https://skribby.io/docs/guides/billing, "What Counts as Billable Time") |
| Shared, moved, read, exported, asked about | cost no recorded hours | by design |
| Rounding | none: counted in seconds | Skribby bills per minute, so we lose under a minute a meeting |

### 3.2 The order hours are used

For a recording of S seconds charged to a person, the seconds are taken in this order, splitting a recording across two grants when needed:

1. the person's trial or pilot grant, if any is left and not ended (it ends first by design),
2. their plan hours, oldest period first (a period's hours expire at its end, except that during the 3 days of grace after a failed renewal the old period's leftover stays usable, section 3.3),
3. then admin gifts and packs together, by earliest `expires_at` first, a grant with no expiry last (**decided 2026-11-16 for S2**: whatever ends soonest is used first; this also places an admin gift, which the first version of this order left out).

The usage counted at or before a pilot grant was made is shown as part of both numbers while that grant is in force ("4 of 5 h", not "0 of 1 h"); decisions use the hours actually held. A second concurrent meeting reserves 30 minutes per running or just-placed meeting (its calendar event's length when known) under `enforce` (section 2.3).

A person who holds a plan period in force is `active` even while a pilot grant still has hours (they pay; the state does not follow the spending order). A recording longer than all the hours left is counted in full and the excess is written off (section 3.3): it is stored as an over-draw on the last grant spent from, which reads as zero left and never as less.

Each split is recorded in `usage_allocations` (section 7), so a grant's remaining hours are always a sum, never a number someone edited: auditable, and an expiry or a refund can be explained line by line. That matters here more than before, because the reason for the per-person model was that a team owner could not explain where hours went; now each person's ledger explains theirs.

### 3.3 Warnings, stop and the exact moments (per person)

| Moment | What happens | Who is told | How |
|---|---|---|---|
| Period starts (first payment, or each renewal) | the plan hours for the period are granted; the person's 80% and 100% flags reset | the person: receipt mail (section 6) | mail |
| Used reaches **80%** of what the person holds (plan hours of this period plus unexpired pack hours; for a trial, 80% of the trial hours) | warning once per period | the person | in-app notification and one mail. Today's 80% rule (`LOW_HOURS_FRACTION`, `app/usage.py:23`) is in-app only and once ever |
| Used reaches **100%** | **in-progress recordings finish and are counted**; **no new recording starts** (pasted link, Send now, calendar auto-join all refuse with `hours_cap_reached`, reason `no_hours`) until a pack is bought, the plan is upgraded or the period renews. **Ask, translate and rewrite keep working** on a paid plan | the person | one mail, in-app. **Only one** "out of hours" notice per person per period, not one per skipped meeting (today's code raises one per skipped meeting: the dedupe key in `app/join_runner.py` includes the join id). Each skipped meeting still shows as "Not recorded" with a Send now button, the existing way to recover once hours exist |
| Pack bought or upgrade paid | hours are granted immediately and the stop lifts | the person | the billing screen and a receipt mail |
| Period ends with hours left | **unused plan hours expire** (no rollover), except that after a failed renewal they stay usable until the 3-day grace ends or the payment succeeds; pack hours stay until their own expiry | none | the usage screen shows "expired" lines |
| Renewal payment fails at the due date (day 0) | status `past_due`; **grace of 3 days (72 hours) from the due date**; the new period's hours are **not** granted (**DECIDED 2026-11-15**) | the person | mail on **day 0** (payment failed, with a pay link) and on **day 2** (last day, with a pay link); a banner on every screen during grace |
| Our retries during grace | **day 1 and day 2**, at the due hour, using the saved card; each attempt and its result are written to the payment table; a pay link in both mails is the fallback | | our own scheduler (section 5.3) |
| Grace ends unpaid (72 hours after the due date) | status `lapsed`: the person falls to the last row of section 2.2; the old period's leftover plan hours expire; remaining paid pack hours still work for recordings; their data and exports are untouched | the person | one mail; a banner |
| Payment succeeds during grace or later | status returns to `active`; a new period starts from the payment date and its hours are granted then; the old period's leftover hours, if any are still there, expire at that moment (no rollover) | the person | receipt mail |

The overshoot to accept (PROPOSED): a meeting that was already running when the person's hours hit zero finishes and is counted in full even if that takes them below zero; the shortfall is **written off**, not carried into the next period and not billed. The risk is bounded: concurrent bots are limited (`user_bot_limit` per person, `bot_max_local` for the host) and a bot stops after 6 hours (`max_poll_hours`). Because hours are now per person, today's estimate for the person's own running meetings (`usage.hours_used`) is exactly the right set; the earlier worry about counting teammates' meetings is gone.

#### Grace in exact words (DECIDED 2026-11-15)

Adebayo's reason: a 7-day grace could let someone run 40 hours on a card that has failed. Agreed: **3 days, and no new hours until payment succeeds.**

| | During the 3 days |
|---|---|
| **Stays on** | reading, search, export, tasks, decisions, **Ask Beyric**, translation, rewriting notes, the calendar connection and its rules, settings, the billing screen, deleting data |
| **Recording** | a pasted link, Send now and a calendar auto-join still start, but **only from hours still left**: the old period's unused plan hours (usable until grace ends or the payment succeeds) and paid pack hours that have not expired. The 100% rule applies when they run out |
| **Off** | the new period's hours; upgrade and downgrade. A pack may still be bought, because that is a payment that succeeded |
| **Worst case of extra hours** | **zero** beyond hours the person had already paid for, plus the running-meeting overshoot above (a meeting running when the hours run out finishes; the bot stops after 6 hours). That is the bound he asked for |

Moments, counted from the due date: day 0 the attempt fails and the first mail goes; day 1 retry; day 2 retry and the second mail; at 72 hours the status becomes `lapsed`.

### 3.4 Changing plan

| Change | Rule | Reason |
|---|---|---|
| **Upgrade** (to a bigger plan) | not possible during grace; otherwise takes effect at once; we charge the price difference for the days left in the period and add the extra hours for the same fraction of the month (rounded down to 0.1 hour); the next renewal is at the new price | simple to explain; the person gets what they pay for immediately |
| **Downgrade** | not possible during grace; takes effect at the next renewal (`pending_plan_code`); the current period is untouched | no refunds and no half periods |
| **Cancel** | the person cancels; no new renewal; everything works until the period ends; no refund of the unused part; packs stay valid for their own 12 months; the person falls to the last row of section 2.2 at the period's end | the standard SaaS rule |
| **Price change** (DECIDED, Q4) | applies from the next renewal only; the subscription keeps the price it was sold at until then | |
| **Currency change** | not allowed mid-subscription; a new subscription in the other currency starts at the next renewal if the person asks (section 5.2) | avoids half-converted periods |

### 3.5 Refunds (DECIDED 2026-11-16; the lawyer reads them before launch)

| Case | Rule |
|---|---|
| Charged twice, or charged by mistake | always refunded in full, by a person at Beyric, in the same currency |
| First plan payment | refunded on request within 7 days of the payment (this is the refund window, not the grace) if no hour of it was used |
| Any other payment | no refund; at our discretion for a real service failure on our side |
| Pack | refunded on request within 14 days if none of its hours were used |
| Where it happens | first version: an admin action in our own admin area that calls the provider's refund endpoint, then marks the payment `refunded` and removes the grant; **no** self-service refunds |

The competitor anchor: Fathom's pricing page carries a 90-day guarantee label on its plans (https://fathom.ai/pricing, read 2026-11-15); I did not read what its terms say behind that label. Chargebacks (a bank pulling a payment back) are not described in any provider page I read; treat them as a refund plus a flag on the account and ask each provider how disputes arrive before going live.

### 3.6 Lapse: what the person keeps

Notes, transcripts, decisions, tasks, exports and the ability to delete everything stay for as long as the account exists (as today). The Privacy Policy says transcripts and notes are kept "until you ask us to delete them or close your account" (`web/components/legal/privacy-content.tsx:100`), so a lapse changes nothing. The lawyer should confirm that is what we want to say once people stop paying.

### 3.7 Edge cases to decide while building

| Case | Proposed handling |
|---|---|
| The person deletes their account while subscribed | the subscription ends, the saved card token is deleted, no refund of the paid period; the Terms say so. Their hours-ledger rows stay under the email fingerprint, as today |
| A recorder leaves a team or the team is deleted | nothing changes for billing: the recorder's hours and plan are theirs |
| A moved meeting after its recorder deletes their account | the meeting stays with the team (multi-team decision); its recorded hours were already charged to the recorder |
| The platform admin | stays uncapped (`effective_cap` returns none for `is_admin` today) |
| A reader-only teammate asks Beyric across shared meetings | refused with `plan_inactive` until they subscribe (section 2.2) |

### 3.8 The one-time trial (3 hours and 14 days both DECIDED 2026-11-15)

About 3 hours, once per person, given at the first confirmed sign-in, no card (DECIDED 2026-11-15). Tied to the person and to the email fingerprint, so deleting an account and signing up again does not give a second trial (the ledger already does this for hours). Spent first (section 3.2). No "free" wording anywhere: the word is "trial" and "included".

**The trial ends when the 3 hours are used or 14 days after sign-up, whichever comes first (DECIDED 2026-11-15, his words "yes, good").** The reason: a teammate who only reads would never use the 3 hours, so Ask Beyric would stay on for them forever. "Sign-up" here is the day the email is confirmed (`users.email_verified_at`, `app/models.py:131`; `users.created_at` is at line 149), because the trial is given at the first confirmed sign-in. After it ends, a person with no plan is in the last row of section 2.2.

**Risk:** every trial costs us about 0.6 to 0.9 USD an hour, so a fake account costs about 2 to 3 USD. The brake is the one that exists now: verified email and the account cap (`max_accounts`), kept as a ceiling on how many new trials can start while sign-up is open. Stage S10 adds a daily trial-hours budget switch.

### 3.9 The pilot grant (kept, and its expiry DECIDED 2026-11-16)

The 10 pilot accounts keep what is left of their 5 hours as a `pilot` grant (decided 2026-11-15). **Decided 2026-11-16: the pilot grant expires 60 days after paid plans open, with one plain mail before.** The reason: a pilot grant keeps Ask, translation and rewriting switched on for as long as it has hours, even for a pilot account that only reads, which is the same problem as the trial's. It also changes what the pilot was promised (5 hours for the pilot, today's Terms line), so the wording is a lawyer item (section 9, question 10).

## 4. Cost and margin arithmetic

### 4.1 Inputs, with sources

| Input | Value | Source |
|---|---|---|
| Real cost per recorded hour, all meetings | **0.52 to 0.65 USD** | `scripts/ai_cost_report.py`, run 2026-11-14 on one account, 6.12 recorded hours (3.26 on Skribby), as reported in `docs/HANDOFF-master.md` section B. I did not re-run it: the lead cannot reach the server. OpenAI prices in the script are partly from secondary sources (its own comments say so). |
| Cost per recorded hour, Skribby meetings only | **0.74 to 0.91 USD** | same report |
| **Real cost per meeting hour, in his words (2026-11-16)** | **Skribby bot 0.35 USD + ElevenLabs 0.20 USD = 0.55 USD per meeting hour**, plus our own OpenAI for notes and Ask, "a few cents". Skribby bills per meeting and deletes the meeting afterwards, so the check against its invoice is monthly | his statement via the lead; the two vendor prices match https://skribby.io/docs/guides/billing (read 2026-11-15). The OpenAI part is inferred from the 2026-11-14 report's range (0.01 to 0.02 USD an hour), not measured on its own |
| **Cost per recorded hour after the flip (live since 2026-11-16 12:20 WAT)** | **0.56 to 0.66 USD**; stress case 0.71 | recomputed in 4.2 from the line above. It is a projection until a new `scripts/ai_cost_report.py` run covers flipped meetings. The flip removes our own transcription (0.27 to 0.36 USD an hour in the script) and the labelling model calls |
| Skribby bot | 0.35 USD an hour, billed per minute, from creation to the end (lobby wait included, first 10 minutes free if never admitted) | https://skribby.io/docs/guides/billing |
| Transcription model on Skribby (live) | ElevenLabs v2: 0.20 USD an hour | same page |
| The 1.0 to 1.25 "billed hours per recorded hour" factor | a judgement in the script, no source | check the real Skribby invoice (monthly) |
| Flutterwave fees | local 2% (split on the page as 1.4% transaction fee plus 0.6% platform fee), international **4.8%**; the page says a 7.5% VAT applies to the transaction fees; international cards are charged and settled in the local currency by default | https://flutterwave.com/ng/pricing (read 2026-11-15) |
| Paystack fees (Nigeria) | local 1.5% + NGN 100 (100 waived under NGN 2,500, fee capped at NGN 2,000); international **3.9% + NGN 100**; international cards are charged and settled in naira by default, with an option to be settled in USD; the page calls USD settlement a public pilot, paid into Zenith Bank domiciliary accounts | https://paystack.com/pricing (opened in the in-app browser; the `/ng/pricing` address gave a 404) |
| Paystack: VAT on its fees, an international-fee cap | **not on the page**; unconfirmed | section 12 |

Flutterwave's effective rates, from the page: international 4.8% x 1.075 = **5.16%**; local 2% x 1.075 = **2.15%**. Paystack's effective rate cannot be completed without its VAT rule.

### 4.2 Margin by price and hours, per person, paid by an international card through Flutterwave

**Recomputed on 2026-11-16 from his 0.55 USD per meeting hour plus our OpenAI.** The old range still holds:

| Case | Calculation (USD per recorded hour) | Result |
|---|---|---|
| Low | 0.35 + 0.20, no overhead, + 0.01 OpenAI | **0.56** |
| High | 0.35 x 1.25 for the bot staying longer than the recording (the script's judgement, no source) + 0.20 + 0.02 OpenAI | **0.66** (0.6575) |
| Stress | if Skribby also bills the model on the full bot time: 0.55 x 1.25 + 0.02 | **0.71** (0.7075) |
| Before the flip (history) | measured on Skribby meetings in the 2026-11-14 report | 0.74 to 0.91 |

The OpenAI figures (0.01 to 0.02) are inferred from the report's range, not measured on their own; "a few cents" is his phrase. The 1.25 factor has no source until the monthly Skribby invoice is checked.

Net after the fee: price x (1 - 0.0516) = price x 0.9484. Cost = hours used x cost per hour. "Margin" is before servers, mail, support, tax and currency loss. Every row assumes the full allowance is used, the worst case for us; no person's real monthly usage has been measured (one account).

| Offer | Price per hour | Net after the 5.16% fee | Margin at 0.56 to 0.66 (live) | Margin at 0.71 (stress) | Margin before the flip (0.74 to 0.91) | Break-even hours at 0.56 to 0.66 |
|---|---|---|---|---|---|---|
| **18 USD for 20 hours (DECIDED 2026-11-16)** | 0.90 | 17.07 | **+3.9 to +5.9** (22 to 33%) | **+2.9** (16%) | -1.1 to +2.3 | 25.9 to 30.5 (above the 20-hour allowance) |
| 17 USD for 20 hours | 0.85 | 16.12 | +2.9 to +4.9 (17 to 29%) | +1.9 (11%) | -2.1 to +1.3 | 24.4 to 28.8 |
| 15 USD for 20 hours | 0.75 | 14.23 | +1.0 to +3.0 (7 to 20%) | 0.0 | -4.0 to -0.6 | 21.6 to 25.4 |
| 15 USD for 15 hours | 1.00 | 14.23 | +4.3 to +5.8 (29 to 39%) | +3.6 (24%) | +0.6 to +3.1 | above 15 |
| About 34 USD for 40 hours (a suggestion only) | 0.85 | 32.25 | +5.8 to +9.8 (17 to 29%) | +3.8 (11%) | -4.2 to +2.6 | 48.9 to 57.6 (above the allowance) |

Checked: I recomputed every figure; the 18, 17 and 15 USD rows match the table the lead showed Adebayo. A naira customer paying a local card through Paystack pays 1.5% + NGN 100 (VAT on that fee is not on Paystack's page), below the 5.16% used here; a foreign card charged in naira costs more (3.9% + NGN 100). The naira price list is Adebayo's to set.

**What the table says.** At 18 USD for 20 hours the offer cannot lose on direct cost even if a person uses every hour: break-even is above the allowance in the live range, and the stress case still leaves 2.9 USD. 15 USD for 20 hours would leave nothing in the stress case.

**What has to be true before 18 USD is printed on `/pricing`** (his decision: the price waits for real cost data from the flip): (1) the flip has run long enough to give a meaningful sample; I suggest at least 10 recorded hours from at least 3 people (PROPOSED, the lead may change it); (2) a new `scripts/ai_cost_report.py` run on those meetings gives a cost per hour at or below 0.66 (or at most 0.71 if the invoice shows the model billed on bot time); (3) the monthly Skribby invoice agrees with the report. If the measured cost is above 0.75, the price is revisited before anything is printed.

### 4.3 Packs at about 1 USD an hour

**Decided 2026-11-16:** a pack is **10 hours for about 10 USD, per person, valid 12 months, bought only with a live plan**; after a lapse the leftover pack hours still allow recordings but not Ask, translation or rewriting (section 2.2, last row). Margin: net 10 x 0.9484 = 9.48 USD, so 0.948 USD an hour. Against the live cost of 0.56 to 0.66 USD an hour that leaves 0.29 to 0.39 an hour (31 to 41%), and 0.24 an hour (25%) in the stress case of 4.2 (0.71). Paystack's fixed NGN 100 would hurt a small pack, so packs have a minimum of 10 hours.

### 4.4 Competitor anchors (read 2026-11-15)

| Vendor | What its page shows | URL | Caveat |
|---|---|---|---|
| Fathom | Free plan with unlimited recordings; Team 19 USD monthly or 15 USD annual per user (2 users minimum); Business 34 or 25; Premium (individual) 20 or 16; Enterprise 35 annual only; a 90-day guarantee label on the plans | https://fathom.ai/pricing | prices are per user; recordings unlimited |
| Fireflies | Free; Pro 18 USD monthly or 10 annual per seat; Business 29 or 19; Enterprise 39 annual only; Pro storage of 8,000 minutes per seat and 20 to 50 AI credits depending on the plan | https://fireflies.ai/pricing | per seat; transcription listed as unlimited |
| Otter | Basic 300 transcription minutes a month; Pro 1,200 in-app recording minutes, listed at 16.99 USD per user per month with a second, promotional 8.49 figure; Business 30 and 24 per user per month; the monthly minute limits are marked as not rolling over | https://otter.ai/pricing | the monthly versus annual label on each number was not clear in the page text I extracted |

What this supports (my reading, not the vendors'): (a) **with a plan per person, 20 hours for about 17 to 18 USD is in line with Otter's Pro** (1,200 minutes is 20 hours per user), which the pooled-team version was not; (b) Fathom and Fireflies show unlimited recordings per seat, which we cannot match because our bot costs real money per hour (Skribby 0.35 USD an hour); (c) "no rollover of plan hours" has a precedent (Otter marks its minute limits as no rollover); (d) a free tier of unlimited recordings is not an option for us (the multi-team spec, section 11).

## 4A. Transcription and names, in plain words

This explains why the cost of an hour changes with the one-transcript flip (section 4.1) and what the flip does to speaker names. Every statement below was checked against the code on `main` at `cf140748` (2026-11-16), except where it says otherwise.

1. **Where names come from.** Skribby's bot returns the transcript in segments. Each segment carries a voice tag (`speaker`, Skribby's own split of voices), and Skribby puts a name on some tags (`speaker_name`) or, for a person it could not pin down, lists candidates (`potential_speaker_names`). Our code turns tags into names (`app/skribby.py:309`, `_resolve_tags`) and never invents one: what stays unclear is "Unknown speaker". A setting (`skribby_name_by_elimination`, default true at `app/config.py:236`) lets a tag with no name take the one candidate left after removing the bot's own names and the names other tags already have, and, since #208 (2026-11-16), a tag whose segments all list the same single candidate that nobody else has (the "sole candidate" rule, same switch).
2. **What a model does and does not do.** The names that turn out right come, per the 19th lead's measurement on meeting 34, from Meet's own speaking signal, which Skribby records as participant events (the code keeps those events as evidence, `app/skribby.py:520-537`; the measurement is in `docs/HANDOFF-master.md`, 2026-11-14 block, section B, and `scripts/skribby_segments_report.py`). I did not find a Skribby page that says how it gets the names; this is the lead's measured finding, not a vendor statement. A transcription model's part is the voice split: on meeting 34 it gave 5 tags for 7 speakers, merging small speakers into others. So names depend on Skribby's participant events plus a voice split by whichever model is chosen.
3. **Until 2026-11-16 12:20 WAT** (`TRANSCRIPT_SOURCE=own`, the default in `app/config.py:227`): our own OpenAI transcription of the audio wrote the text (`transcription_model`, `gpt-transcribe`; `app/pipeline.py:1008`). Skribby's segments, with their names, were used only as a guide. `label_transcript` (`app/notes.py:232`) cut our text into sections of up to 4,000 words and, for each section, picked the Skribby segments at the same place by **share of text** (`_segments_for`, `app/notes.py:187`; there are no per-line times) with a small overlap. It then asked the notes model (`notes_model`) to add `[Name]:` labels, and kept the section unlabelled if more than 2% of its words changed (`MAX_WORD_DRIFT`, `app/notes.py:26`). That step is a model call with its own cost, recorded in `ai_runs` as kind `label` (`app/pipeline.py:1327-1336`). The code default is still `own`; the server's `.env` decides.
4. **The flip is live** (`TRANSCRIPT_SOURCE=skribby` on the server since 2026-11-16 12:20 WAT, with the sampled comparison off; **this comes from the 21st lead's message, I did not read the server**): the stored transcript is built straight from Skribby's segments as `[Name]: text` turns; **our transcription and the model-based labelling are both skipped** (`app/pipeline.py:1066-1080`; `app/transcript_source.py`). Our own transcription still runs as a fallback when Skribby's text is missing, too short for the recording (under 20 words a minute), a loop of the same few words, or has no segment times (`fallback_reason`, `app/transcript_source.py`; the "stops before 70% of the recording" rule that stood here was removed on 2026-11-16, `docs/sop/2026-11-16-drop-transcript-coverage-rule.md`); each fallback is the old, dearer path for that meeting. The vocabulary pass still applies to the text.
5. **Why that changes cost.** Skribby makes a transcript for every bot meeting whichever source we use, and it is on the bill (ElevenLabs v2 at 0.20 USD an hour, https://skribby.io/docs/guides/billing). Before the flip we also paid for a second transcript (our OpenAI transcription, 0.27 to 0.36 USD an hour in `scripts/ai_cost_report.py`) and for the labelling model calls (not priced separately here). The flip stops both, which is the drop from 0.74 to 0.91 USD an hour on Skribby meetings to the 0.56 to 0.66 USD of section 4.2. That range is a projection until a new report runs on flipped meetings.
6. **What could go wrong.** If speaker names do not survive on the ElevenLabs model, or Skribby's text is often missing, the meetings fall back to the old path (dearer) or the flip is switched off by an environment line, and the cost returns toward 0.74 to 0.91 USD an hour. That is why 18 USD is not printed until real cost data exists (4.2). The price wait does not stop the billing build: every billing stage is dark and no price is published until stage S11.

## 5. The provider layer

### 5.1 The interface (PROPOSED)

Same pattern as `app/bot_provider.py` (a `Protocol` plus a fake for tests). One new module `app/billing/` with:

| Piece | Job |
|---|---|
| `PaymentProvider` (Protocol) | `name`, `currencies`; `start_checkout(payment, payer, return_url)` returns a hosted-checkout link and our reference; `verify_webhook(headers, raw_body)` raises if the request is not genuine; `parse_event(raw_body)` returns a neutral `ProviderEvent` (kind: `charge_succeeded`, `charge_failed`, `refund`, `dispute`; our reference; the provider's transaction id; amount in minor units; currency; payer email; a reusable card token if present); `fetch_transaction(provider_tx_id)` re-reads the payment from the provider; `charge_saved_method(token, amount, reference)` for renewals; `refund(provider_tx_id, amount)` |
| `FlutterwaveProvider`, `PaystackProvider` | the two real adapters, test mode and live mode by keys |
| `FakeProvider` | deterministic, in-memory; the only provider CI ever talks to |
| `routing.provider_for(billing_country)` | one small function: Nigeria gives naira and Paystack; any other country gives US dollars and Flutterwave (section 5.2). The two providers are both built; the table is data in settings so one can be switched off |

Nothing outside `app/billing/` knows a provider's name: payments, grants and entitlements use neutral fields. A webhook route per provider (`POST /api/billing/webhooks/{provider}`) is the only provider-shaped surface.

### 5.2 Routing by billing country (**DECIDED 2026-11-15** in principle; the detail below is PROPOSED)

**The rule he gave:** build both providers; Nigeria is naira through Paystack; everywhere else is dollars through Flutterwave; the system must tell whether a customer is billing from Nigeria or from outside it.

**The key is the billing country the person chooses at their first purchase and saved on their billing profile** (`billing_profiles.billing_country`, section 7). It is not guessed from the visitor's IP address: a VPN or a traveller would send the wrong way. The screen shows the country list, then states the result before any payment ("Prices in naira, paid through Paystack" or "Prices in US dollars, paid through Flutterwave"). The naira price list is **fixed and set by Adebayo** (no live conversion), reviewed when the rate moves; the rate is his pending decision (handoff C5).

| Billing country | Currency | Provider | Price list |
|---|---|---|---|
| Nigeria | NGN | Paystack | the naira list |
| Any other country | USD | Flutterwave | the dollar list |

Paystack's pages also list Ghana, Kenya, South Africa and Côte d'Ivoire (https://paystack.com/pricing), but our account is a Nigerian business account, so those countries go to Flutterwave in dollars. Whether Flutterwave accepts a payer from every country is not confirmed: its coverage page came back empty (it is built by script).

**When the person changes the billing country later** (PROPOSED; only the person may, each change is written to a history table with who, when, old and new, and no more than one change in 30 days):

| Situation | What the build does |
|---|---|
| No live subscription | the change applies at once; the next checkout uses the new route |
| A live subscription, same currency (for example two non-Nigerian countries) | the profile and later invoices change now; nothing else moves |
| A live subscription, crossing between Nigeria and elsewhere | the change is **scheduled for the end of the paid period** (`pending_billing_country`). Until then renewals, retries and packs keep the old currency and provider. At the renewal the person gets a fresh first-payment checkout with the new provider and price list (a saved card token belongs to one provider and cannot be moved). The old subscription ends at the period's end; hours already granted are untouched; an unpaid new checkout follows the normal 3-day grace |

**When the card's issuing country disagrees with the billing country.** What each provider's page shows (sample responses, not live captures):

| Provider | Field in the verify response | Notes |
|---|---|---|
| Paystack | `data.authorization.country_code` (sample value `NG`), plus `bin`, `channel`, `currency`, `ip_address` (https://paystack.com/docs/api/transaction/, "Verify Transaction") | present for card payments; the sample is a test card, so I could not see what a foreign card returns |
| Flutterwave | `data.card.country` (verify sample `NIGERIA NG`, but the webhook sample shows `NG`: two formats), `data.card.issuer`, `data.card.first_6digits`, `data.payment_type` (https://developer.flutterwave.com/docs/transaction-verification and https://developer.flutterwave.com/docs/webhooks) | the build must read the trailing two letters; bank transfers and mobile money carry no card, so no card country |

Not confirmed: that either field is filled for every real card, and what each returns for a card issued abroad (the samples are sandbox values).

PROPOSED handling, after `fetch_transaction` has confirmed the payment:

1. **Never refuse a payment that succeeded.** A Nigerian business with a foreign-issued card, or a person billed abroad with a Nigerian card, is a normal customer. Store `card_country` on the payment and set `country_mismatch` when it differs from the billing country.
2. Show the person a plain note once ("Your card was issued in X; your billing country is Y"), and list mismatched accounts in the admin view for a human look.
3. After two mismatched payments in a row, mail the person asking them to correct the billing country if it is wrong. The system never changes the country by itself.
4. **Why this matters:** the provider's fee rises when a foreign card is charged in naira (Paystack 3.9% + NGN 100 against 1.5% + NGN 100 for a local card; Flutterwave 4.8% against 2%, both from their pricing pages), and the naira list could be cheaper than the dollar list on a given day, so a foreign team could choose Nigeria to pay less. The Terms must say the billing country is where the customer is established (lawyer, section 9); the mismatch flag is the check.
5. The other direction: a person billed in dollars paying with a Nigerian card needs a card its bank lets spend in dollars. No page I read says how often that fails; the screen tells the payer to use a card that can pay in dollars, or to change the billing country if they bill from Nigeria.

### 5.3 Who owns the billing clock (recommendation)

| Option | What the providers say | Verdict |
|---|---|---|
| Use the provider's subscriptions | Flutterwave: you create a payment plan and a customer is subscribed on the first charge; card payments only; the subscription is tied to the customer's email and that cannot be changed afterwards; it emails the customer a reminder before each charge; **a failed charge is retried 3 more times at 30-minute intervals**, and after three failures in a row the subscription is cancelled; it sends a webhook for each charge and for cancellation (https://developer.flutterwave.com/docs/recurring-payments/payment-plans). Paystack: cards and Direct Debit (Nigeria) only; billing dates by day of month (created after the 28th is billed on the 28th); a webhook `invoice.create` three days before the charge, then `charge.success` or `invoice.payment_failed`; **the page says a failed subscription charge is not retried** (https://paystack.com/docs/payments/subscriptions/) | Cannot give a 3-day grace with our own retries on day 1 and day 2, a mid-period upgrade, a pack, or a price change at renewal without fighting the provider (and the two providers behave differently, which we would have to hide) |
| **We keep the clock** (recommended) | First payment through hosted checkout, which returns a reusable card token. Renewals: our scheduler charges the saved token on the due date and on retry days, writes every attempt, and moves the status. Paystack documents the call: `POST /transaction/charge_authorization` with an `authorization_code` marked reusable, and a `queue` flag for scheduled charges (https://paystack.com/docs/api/transaction/). Flutterwave's verify response carries a card `token` (https://developer.flutterwave.com/docs/transaction-verification) but **I could not open a Flutterwave page on charging a saved token** (the two addresses I tried returned 404), so that call is unconfirmed. | One state machine, ours, testable with a fake clock. Needs a fallback. |
| Fallback inside the recommended option | If a renewal charge fails or no usable token exists, we mail the person a **pay link** (a fresh hosted checkout for the same amount) at the same moments in section 3.3. A manual renewal is the safe floor. | Plan for it from day one: I could not find any source on how often Nigerian bank cards refuse a charge made without the cardholder present; assume many do. |

### 5.4 Webhooks, verification and idempotency (PROPOSED, with what each vendor page says)

| Rule | Detail |
|---|---|
| One inbox | The webhook route reads the **raw body**, verifies it, writes a row to `billing_events` (provider, a dedupe key, type, body, whether the signature passed, times) and answers 200 at once; the worker processes the row afterwards. A unique index on (provider, dedupe key) makes a replay do nothing. |
| Flutterwave verification | A secret hash you set in the dashboard comes back unchanged in the **`verif-hash`** header; missing or different means discard. It is a shared secret, not a signature of the body. Answer **200** or it counts as a failure; the request times out after **60 seconds**; with retries enabled it retries **3 times, 30 minutes apart**. (https://developer.flutterwave.com/docs/webhooks) |
| Paystack verification | The newer **`Paystack-Signature`** header looks like `t=<unix time>,v1=<key id>:<signature>`; the signature is a lowercase hex HMAC SHA512 of `t`, a dot, and the raw body, keyed with the whole `whsec_` signing secret; reject if `t` is more than **300 seconds** off; an event can carry two `v1` values while a secret is being rotated, so check each; compare in constant time. The older **`x-paystack-signature`** is an HMAC SHA512 of the raw body with the secret key and has no time. Live mode retries every 3 minutes for 4 tries, then hourly for 72 hours; test mode retries hourly for 10 hours with a 30-second timeout; a retry is signed again with a new `t`. (https://paystack.com/docs/payments/webhooks/) Paystack also documents an IP allow-list; the page text I saved was cut before the addresses, so none are listed here. |
| Never trust the webhook alone | Both vendors tell you to re-read the payment from their API before giving value (Flutterwave tells you to check the status, amount, currency and reference match what you expected). Our rule: grant hours only after `fetch_transaction` returns status successful, our reference, the currency we asked for, and an amount at least what we asked for. This matters most for Flutterwave, whose check is a static secret. |
| The success redirect grants nothing | The page the customer returns to only shows the payment's state (`pending`, `paid`, `failed`) read from our database; the frontend polls it. Only a verified payment writes a grant. |
| Idempotent grants | `hour_grants` has a unique key on (kind, source payment id). Processing the same payment twice, from a webhook, a poll and a retry, can only create one grant. |
| Our reference | We create the payment row and its reference (`bn-<payment id>-<random>`) before sending anyone to a provider, so every webhook can be matched to what we expected. |
| Raw body through the proxy | `notes.beyrictech.com` serves Next.js on port 8020 which proxies unknown paths to the API. A signature is checked on the exact bytes, so the first test is a real test-mode webhook through the live path; an nginx edit (his one-line `sed` pattern) may be needed. Add the webhook path to the rate-limit exemptions and keep a body-size limit. |

### 5.5 Test modes (what each vendor gives us)

| Vendor | Test details (public docs) |
|---|---|
| Flutterwave (https://developer.flutterwave.com/docs/testing) | Test mode works only with test credentials; Mastercard 5531886652142950, expiry 09/32, CVV 564, PIN 3310, OTP 12345 (also 3DS cards for Visa and Verve); special OTPs mock errors (5548 wrong OTP, 6648 insufficient funds) when OTP is entered in their modal; test the checkout from `localhost`, not `127.0.0.1`; test-mode receipt emails go to the business email. |
| Paystack (https://paystack.com/docs/payments/test-payments/) | Successful reusable card 4084 0840 8408 4081 (expiry any future date, CVV 408); declined card 4084 0800 0000 5408; a card that creates no token (5078 5078 5078 5078 53); refund-scenario cards (failed 4084 0800 0067 1803, needs attention 4084 0800 0067 1902). |

Both are public sandbox values, safe to put in a test plan. **A dashboard test key needs an account at each vendor, and I did not create one** (task rule). Adebayo says both accounts are new and made for Beyric, so he creates the test keys himself (section 5.7); I could not check that.

### 5.6 Dollars and settlement: the open risk (the Flutterwave side)

Section 4.1 shows both pricing pages say international cards are charged and settled in the local currency by default. Naira through Paystack is the ordinary case (naira in, naira settled). For dollar checkout through Flutterwave the first test-mode job is to prove: can our merchant account create a USD payment, what does the customer see, in what currency and at what rate is it settled, and can it be settled in USD. Until that is proven, do **not promise that dollar payments are settled in dollars**; Paystack describes USD settlement as a pilot. We pay vendors in USD by card (Skribby's billing page lists USD and card payment through Stripe), so a USD-settled account is an operations need, not a nicety.

### 5.7 Accounts and keys

- **Accounts.** Both the Paystack and the Flutterwave accounts exist and are **new, made for Beyric** (Adebayo, relayed 2026-11-15); the Paystack one is not the account used by another product. They are assumed to be under Beyrichq LTD. **What is not known:** whether either account's business verification (KYC) is finished; Flutterwave's settlement page says only approved accounts are settled (https://developer.flutterwave.com/docs/payment-operations/settlements, "Requirements"), so each account's live approval is a go-live check, not an assumption. **Still to do:** he puts the test keys in a local env file (below). Until he does, S6 (Paystack) and S7 (Flutterwave) can be built and tested only against the signature vectors and the fake provider, not in the vendors' test modes.
- **Test keys go in a local env file, never in chat, never in git** (his rule via the lead). The repository already ignores `.env` and `.env.*` (`.gitignore` lines 1 and 2) and keeps names without values in `.env.example`. PROPOSED names: `FLUTTERWAVE_SECRET_KEY`, `FLUTTERWAVE_PUBLIC_KEY`, `FLUTTERWAVE_SECRET_HASH` (the webhook secret hash), `PAYSTACK_SECRET_KEY`, `PAYSTACK_PUBLIC_KEY`, `PAYSTACK_WEBHOOK_SECRET` (the `whsec_` signing secret). No key goes into a PR body, a log line, a test fixture or a captured response. Test and live keys are never mixed: the app checks at start that `BILLING_LIVE` matches the keys (each provider's key prefixes should be confirmed from its docs when building; I did not read them).
- **Webhook addresses** are set separately in each dashboard for each mode (test and live), each pointing at `POST /api/billing/webhooks/{provider}`.

## 6. VAT, invoices and Nigerian rules

I read one Nigerian source and no official one. Treat everything below as "what to ask the accountant", not as advice.

| Question | What I could confirm | What I could not |
|---|---|---|
| Rate | VAT stays at 7.5% under the 2025 reform, effective 1 January 2026 (Forvis Mazars Nigeria article dated 16 March 2026, https://www.forvismazars.com/ng/en/services/tax/nigeria-s-tax-reform-in-focus/2025-nta-vat-key-business-changes) | the Act itself; any FIRS or Nigeria Revenue Service page (the `firs.gov.ng` address timed out) |
| Do we have to charge VAT? | Same article: a small business with turnover of NGN 100 million or less and fixed assets not above NGN 250 million, not counting providers of professional services, is exempt from registering for, charging, filing and remitting VAT, and may opt in by written notice | whether Beyrichq LTD qualifies, whether a software subscription is a "professional service", and whether the figure is right: one web search summary said NGN 50 million, which conflicts with this article, so I did not use it |
| Electronic invoices | Same article: the NRS e-invoicing platform (called the Merchant Buyer Solution) is phased in, starting with taxpayers of NGN 5 billion turnover and above (enforcement April to June 2026), later for medium and emerging taxpayers | whether and when it reaches us |
| Sales to customers abroad | not confirmed. It is common for exports of services to be zero-rated, but I found no source | accountant |
| Foreign customers' own VAT or sales tax (for example in the EU) | not researched | accountant, before selling to consumers abroad. The target is teams (businesses) |
| What the provider's VAT is | Flutterwave: 7.5% VAT is on the transaction **fee**. Paystack: its pricing page does not say | Paystack's rule |
| Stamp duty on receipts | not confirmed. Flutterwave's settlement sample contains a `stampduty_charge` field (value 0 in the sample, https://developer.flutterwave.com/docs/payment-operations/settlements) | accountant |

### What the build does about it (PROPOSED)

- **Invoices are ours, not the provider's receipts.** One row per successful payment (`invoices`), a sequential number per year (`BN-2026-000123`), the seller (Beyrichq LTD, address; **the registration and tax numbers must be supplied by Adebayo, they are not in the repo**), the buyer from a billing profile (company name, address, country, optional tax number, billing email), currency, amount, VAT rate in basis points and VAT amount, and the plan period or pack. The PDF is **built when asked and never stored**, like the meeting export (`app/export_render.py`), and is mailed with the receipt.
- **VAT is a setting, default zero**, `BILLING_VAT_RATE_BP`, so the accountant's answer is a configuration change plus a wording change and not a migration. The invoice and checkout screens show "VAT included" or "VAT added" from the same setting.
- A billing profile is required before the first payment (billing country decides the currency, section 5.2).
- Keep invoices when an account is deleted. How long the law says to keep them is not confirmed; the lawyer and accountant answer it (section 9).

## 7. Data model and migration sketch

Next free Alembic number: **0049**. Checked on 2026-11-16: `alembic/versions/` on `origin/main` (`cf140748`) ends at `0048_series_ignored.py` (PR #205 took 0048 after the first version of this spec), and `git ls-remote --heads origin` showed no other branch with a migration; the `gh` CLI could not list open PRs when this was written, so **check `alembic/versions/` on `main` again when starting each stage**, because the numbers below move if another migration lands first. All migrations are additive, so the old code keeps working during a rollout. **Everything is keyed to a person (`user_id`); no table gets a `team_id` and `usage_entries` is unchanged** (it already records the recorder).

| Table (new unless marked) | Key columns | Notes |
|---|---|---|
| `plans` | `code` (primary key), `name`, `monthly_seconds`, `is_public`, `sort_order`, `active` | rows are data, edited by migration or admin; no member limit; no plan name appears in code except in tests |
| `plan_prices` | `plan_code`, `currency` (`NGN`, `USD`), `interval` (`month`), `amount_minor`, `active_from`, `active_until` | price history; a subscription points at the price row it was sold at (a price change at renewal is a new row) |
| `products` / pack prices | `code`, `seconds`, `currency`, `amount_minor`, `valid_months` | packs of 10 hours at launch |
| `subscriptions` | `user_id` (who the plan is **for**: the person whose hours it grants), `payer_user_id` (who **pays**: the same person today), `plan_code`, `price_id`, `status` (`active`, `past_due`, `lapsed`, `canceled`, `ended`), `currency`, `provider`, `current_period_start`, `current_period_end`, `cancel_at_period_end`, `pending_plan_code`, `grace_ends_at`, `next_charge_at`, `retry_count`, `pending_billing_country` | at most one live row per `user_id` (partial unique index). **Every gate and every grant reads `user_id`, never the payer**, so a later seat (section 10A) is a row where the payer is someone else. Today `payer_user_id = user_id`. `sponsor_team_id` is deliberately **not** in the first migration (section 10A says why) |
| `payment_methods` | `user_id` (the payer who saved the card), `provider`, `token` (encrypted with `app/vault.py`), `brand`, `last4`, `expiry`, `email_at_provider` | never a card number; the token only |
| `payments` | `payer_user_id`, `subscription_id` (nullable: a later seat payment covers several subscriptions through a `payment_items` table added then), `provider`, `provider_tx_id`, `reference` (unique), `kind` (`plan`, `renewal`, `upgrade`, `pack`), `currency`, `amount_minor`, `status` (`pending`, `succeeded`, `failed`, `refunded`, `partly_refunded`), `paid_at`, `card_country`, `country_mismatch` | unique (provider, provider tx id) |
| `billing_events` | `provider`, `dedupe_key`, `type`, `payload` (JSONB), `signature_ok`, `received_at`, `processed_at`, `error` | the webhook inbox; unique (provider, dedupe key); payloads are pruned after a retention period to be chosen |
| `hour_grants` | `user_id`, `email_hash`, `kind` (`trial`, `pilot`, `plan_period`, `pack`, `admin_gift`), `seconds`, `starts_at`, `expires_at`, `source_payment_id`, `subscription_id` (the subscription a `plan_period` grant came from), `reason`, `created_by` | unique (kind, source payment id) so a replay cannot grant twice. A trial grant carries its own `expires_at` (sign-up plus 14 days, decided); a pilot grant carries the pilot expiry (60 days after paid plans open, decided; set when plans open) |
| `usage_allocations` | `usage_entry_id`, `grant_id`, `seconds` | splits a counted recording across the person's grants (section 3.2) |
| `usage_entries` | **unchanged** | already records `user_id`, the email fingerprint, `meeting_id` and seconds; the recorder is the owner (`app/usage.py:77`) |
| `invoices` | `number` (unique), `payer_user_id`, `payment_id`, `issued_at`, `currency`, `subtotal_minor`, `vat_rate_bp`, `vat_minor`, `total_minor`, `seller` (JSON snapshot), `buyer` (JSON snapshot) | the PDF is built on request |
| `billing_profiles` | `user_id` (the payer), `name_or_company`, `address`, **`billing_country`** (chosen by the person at their first purchase; the routing key, section 5.2), `tax_id`, `billing_email` | one row per person |
| `billing_country_changes` | `user_id`, `old_country`, `new_country`, `effective_at`, `created_at` | the history behind the change rules in section 5.2 |
| `refunds` | `payment_id`, `amount_minor`, `reason`, `created_by`, `provider_ref` | |

What is **not** in the model any more: a team pool, `team_id` on usage, `user_settings.hours_team_id`, a member limit on a plan, a subscription on a team.

Migrations, one per group of stages, in this order: **0049** (stage S1) `hour_grants`, `usage_allocations` and the **backfill of pilot accounts into `pilot` grants** (each account's remaining hours = its cap minus its ledger, so nobody loses or gains hours); **0050** (stage S4) payments, payment methods, billing events; **0051** (stage S5) billing profiles with the billing country and its history (0050 shipped with S4, so S5 took its own number, **updated 2026-11-17**; the pending country change sits on the profile until S8 has a subscription row); **0052** (stage S8) `plans`, `plan_prices`, `products`, `subscriptions`; **0053** (stage S9) invoices and refunds. Each needs the usual round trip on a scratch database: one head, upgrade, downgrade. Downgrade of 0049 is only safe while no grant other than `pilot` exists.

Settings (flags), all off or dark first: `HOURS_MODE` (`off`, `shadow`, `enforce`: the per-person hours rule), `FEATURE_GATES_MODE` (`off`, `shadow`, `enforce`: Ask, translation and rewrite), `BILLING_ENABLED`, `BILLING_COUNTRY_ROUTES` (the country to provider table: Nigeria to Paystack, everything else to Flutterwave), `BILLING_LIVE` (test keys or live keys), `TRIAL_SECONDS` (3 hours, 0 turns the trial off), `TRIAL_DAYS` (14), `TRIAL_DAILY_BUDGET_SECONDS`, `BILLING_VAT_RATE_BP`.

## 8. Build stages: final, small, ordered

Every stage is its own PR, deployed one at a time, **dark first** (a flag that is off, or a state nobody reaches), with its SOP, its checklist lines and, where a payload changes, a true integration guide built from captures against the running app. Locally only lint, format, type-check and the tests of the files touched run; **never the full suite, a shard or e2e on this Mac** (CI runs them). The price (section 4.2) is decided but not printed, and **nothing in these stages needs it**: no price is shown before S11 and prices sit in data rows. **No stage uses real money before S11**; every other stage is tested on a fake provider or in a provider's test mode.

**The feature gates ship dark and switch on with the go-live, not earlier** (a separate flag, `FEATURE_GATES_MODE`): the pilot accounts have Ask, translation and rewriting today, and a gate switched on before plans exist would take them away from people who cannot yet pay.

**Order and dependencies.** S1, then S2, then S3 (they touch `app/entitlements.py`, `app/placement.py` and the gated routes, one writer at a time). S4 touches new files only (`app/billing/`) and can run after S1 or beside it, with a different writer. S5 needs S4; S6 and S7 each need S5 and are independent of each other. S8 needs S2, S5 and at least one adapter. S9 needs S8. S10 needs S2. S11 needs everything. T1 is a runbook that can run at any time. S12 needs T1 and S2.

| Stage | What | Size | Dark by | How it is tested without real money |
|---|---|---|---|---|
| **S0** | This spec and the checklist map | docs | none | link check |
| **S1** Hours model in shadow | Migration 0049; models `HourGrant` and `UsageAllocation`; the pilot backfill; `app/entitlements.py` with `status` and `check_join`; a logging-only shadow comparison in `reserve_slot`; a read-only report script; detail below | M | `HOURS_MODE=shadow` (default `off`): no behaviour changes | unit tests with the existing clock injection (`app/clock.py`); backfill fixtures (pilot account, partly used, fully used, deleted-and-re-registered, admin); the shadow comparison on a seeded database; migration round trip on a scratch database |
| **S2** Enforce the hours rule per person, with the new warnings | The one block in `reserve_slot` (`app/placement.py:175-180`) uses `entitlements`; 80% and 100% notices with a mail to the person; one "out of hours" notice per person and period (not one per skipped meeting); `usage` in the session payload gets the new fields; the refusal message stops saying "pilot" and "hello@"; each counted recording is split across grants | S to M | `HOURS_MODE=enforce` stays off until Adebayo switches it | boundary tests at 79.9%, 80%, 99.9% and 100%; a running meeting finishing past zero; the join-runner skip reason; two accounts never share hours; FE guide from live captures |
| **S3** Feature gates | `entitlements.can(user, feature)` and the gates of section 2.3: Ask, new translations, rewrite notes (inside `services.regenerate_notes` so the legacy route cannot bypass it); a shadow mode that logs "would have refused" | S to M | `FEATURE_GATES_MODE=shadow` (default `off`) | a table test of every state in section 2.2 against every gate; the reader-only teammate; Review's "add the corrected spelling" stays open |
| **S4** Provider layer and webhook inbox | `app/billing/`: the `PaymentProvider` protocol, `FakeProvider`, neutral event and payment types; the webhook route with verification hooks, dedupe and re-fetch before any grant; the payments, payment-methods and events tables (first half of migration 0050) | M | `BILLING_ENABLED=false` | replay of the same event twice; wrong amount, wrong currency, wrong reference; a bad signature; everything against `FakeProvider` |
| **S5** Billing profile, country and routing | the billing profile with the person's `billing_country` and its history (migration 0051: 0050 shipped with S4); `routing.provider_for(billing_country)`; the change rules (immediate, same currency, scheduled at renewal); the card-country mismatch flag; only the person changes their own | M | `BILLING_ENABLED` for allow-listed accounts only | a table test of every row in section 5.2; a mismatch with the fake provider; two changes inside 30 days refused. No keys needed |
| **S6** Paystack adapter (naira) | hosted checkout, verify, both signature header forms, saved-card charge, refund | M | `BILLING_LIVE=false` | signature vectors from Paystack's page (300-second window, two signatures during a rotation); Paystack's test cards (section 5.5); the raw body through the real proxy; needs his Paystack test keys in a local env file |
| **S7** Flutterwave adapter (dollars) | hosted checkout, verify, the `verif-hash` check, saved-card charge (call unconfirmed, the pay link is the fallback), refund; **prove dollar pricing and settlement (section 5.6)** or record it as unproven | M | `BILLING_LIVE=false` | Flutterwave's test cards and OTPs (section 5.5); replay and wrong-amount cases; a USD payment end to end in test mode; needs his Flutterwave test keys |
| **S8** Subscriptions, packs and the lapse states | migration 0052 (`plans`, `plan_prices`, `products`, `subscriptions`); the status machine; our scheduler for due dates, retries (day 1 and day 2), the 3-day grace and lapse; no new hours until a payment succeeds; checkout for a plan and a pack through the provider the country picks; upgrade proration; downgrade and cancel at period end; the billing screens | L (split: **S8a, the money half, built 2026-11-17** (`docs/sop/2026-11-17-billing-subscriptions.md`); S8b, the screens, not built) | `BILLING_ENABLED` for allow-listed accounts | a table of (status, event) to (status, effect) run with a fake clock through every row, including "payment succeeds on day 2" and "grace ends"; the worst-case hours during grace asserted as zero; end to end with `FakeProvider`; then in each provider's test mode: success, declined card, insufficient funds, a saved-card renewal |
| **S9** Invoices, receipts, refunds | migration 0053; invoice numbering and PDF; the billing profile's VAT fields; the admin refund action; receipt mail | M | with S8 | invoice number uniqueness under two payments at once; PDF content test; a refund of a pack with used hours is refused; the refund window rules of 3.5 |
| **S10** Trial and pilot expiry | the 3-hour trial at first confirmed sign-in, ending at the 3 hours or after 14 days; the pilot grants' 60-day end with its mail; the trial-hours daily budget; `max_accounts` kept as a brake | S | `TRIAL_SECONDS=0` (trial off) | trial once per person and per email fingerprint; deleting and re-registering gives nothing; the 14th day and the pilot expiry with a fake clock |
| **S11** Go-live | `/pricing` numbers (section 10), Terms and Privacy (section 9), live keys in the server's env file only, `FEATURE_GATES_MODE=enforce`, one real small payment through each provider by Adebayo with his own card then a refund (**needs his explicit yes: it moves real money**), then the sign-up cap raised | S | `BILLING_LIVE` | the runbook; the price is printed only after the cost condition of 4.2 is met |
| **T1** Three-account live test | not code: three accounts on three calendars hold the same event with one meeting link; confirm that only one bot joins, who is charged, and what the others see | runbook | none | needs three real accounts and a real meeting (Skribby costs a few cents); results go in the SOP |
| **S12** Organizer's bot preferred | the organizer's bot goes on time; the other teammates' bots wait about two minutes and stay out if it is in; the first placed bot still records when the organizer has no account, hours or plan; the stale skipped notice is fixed | S to M | a flag, `PREFER_ORGANIZER_BOT` (default off) | the placement rules with a fake clock and three fake accounts; the organizer flag from Google (`organizer_self`) and, once checked, Outlook; built **only after T1** |

A shadow-then-enforce order means a mistake in the hours rule shows up in the logs before any customer meets it, and the pay screens come only after the rule they sell has been live.

### S1 in detail (ready to start)

Suggested branch `feature/hours-model-shadow` (no AI or `claude` name in a branch). Goal: the hours of every person are held in grants and a new rule can be compared with the old one, with no behaviour change.

1. **Models and migration.** Add `HourGrant` and `UsageAllocation` to `app/models.py` next to `UsageEntry` (line 812) and a migration `alembic/versions/0049_hour_grants.py` (down revision `0048`), columns as in section 7. Re-check the number first.
2. **Backfill inside the migration.** For every non-admin account that is not deleted, one `pilot` grant of its cap (its own override `users.hours_cap_seconds`, else `app_state.default_hours_cap_seconds`, else 18000) minus its ledger (`usage_entries` summed by user id or email fingerprint, as `usage.ledger_seconds` does, `app/usage.py:41`), never below 0, no expiry yet. Usage before S2 is already netted out of the grant, so no allocation rows are written for the past.
3. **`app/entitlements.py`.** `status(session, user, now)` returns the person's state and hours left; `check_join(session, user, now)` returns the decision the new rule would make. Pure reading, no route imports.
4. **Shadow, logging only.** In `reserve_slot`, after the existing check (`app/placement.py:175-180`), when `HOURS_MODE=shadow` call `check_join` and log one line if its decision differs from the old one. Add `HOURS_MODE` (`off`, `shadow`, `enforce`; default `off`) to `app/config.py`, `.env.example` and `deploy/README.md`. A read-only `scripts/hours_shadow_report.py`, written like `scripts/ai_cost_report.py` (read-only transaction, no names or emails), prints hours per person per 30 days and the disagreements.
5. **Tests** (named files are suggestions): `tests/test_hours_grants.py` for the backfill cases, `tests/test_entitlements_status.py` with the injected clock, a migration round trip on a scratch database. Run only these locally, plus lint, format and type-check as CI does.
6. **Done when:** with the flag off nothing changes (the existing usage and placement tests pass in CI untouched); with it on, the logs and the report show the comparison on a seeded database; the migration goes up and down; the SOP, the checklist lines and the FE guide note are written; the PR carries no AI attribution.

## 9. What the Terms and Privacy Policy must say, and what to ask the lawyer

Today the Terms still say "The Service is free during the pilot. Each pilot account has a limit on recorded hours, shown in the app" (`web/components/legal/terms-content.tsx:30`), which is the wording he rejected for the pricing page, and the hours message asks people to write to `hello@beyrictech.com`, which the audit says is not a confirmed mailbox. Both change in S2 and S11.

### Terms (new section, drafted by the lawyer from this list)

- **Plans belong to a person.** Each person who wants Ask Beyric, translation, rewriting notes, a note taker of their own or calendar auto-join subscribes themselves (after the trial). A company name may appear on the invoice; the customer is the person who signs up and pays
- what a team member can do without a plan: read what is shared with them (meetings, transcripts, notes, decisions, tasks), search, export, use tasks and decisions, and delete; what stops (section 2.2); that sharing a meeting changes who can read it, never who pays for its recording
- hours: counted in recorded hours; **the recorder pays**; when several people's note takers would join the same meeting only the first records and the others do not join; hours expire at the end of each period (no rollover); packs last 12 months; unused hours are not refunded
- the trial: about 3 hours, once per person, ends at the 3 hours or 14 days after sign-up, whichever comes first; no card; may be withdrawn for abuse
- auto-renewal, how to cancel, that cancelling ends the plan at the period's end, and that a failed payment starts 3 days of grace in which no new hours are granted, after which recordings, Ask, translation and rewriting stop while the data is kept
- price changes: that they apply from the next renewal (the handoff rule is "no notification of policy changes"; whether a price rise needs a notice is a contract question, below)
- taxes: VAT added or included, who issues the invoice (Beyrichq LTD)
- refunds and chargebacks (section 3.5)
- the pilot accounts' hours: what happens to the pilot grant (section 3.9); this changes the promise in today's Terms line and is a lawyer item
- a clause that no recording law is changed by paying (the existing "Recording laws and consent" section still stands)

### Privacy Policy (additions)

- the payment providers (Paystack and Flutterwave) as recipients: what we send (the payer's name and email, the amount, our reference) and that card details are entered on the provider's page and are not seen or stored by us (only a token)
- what we keep: payments, invoices, the billing profile, the hours ledger; that invoices are kept after account deletion for the period the law requires (to be confirmed), and that the existing "hours counted with a one-way fingerprint of the email address" (`privacy-content.tsx:105`) also covers the trial
- the transfer of payer data to the providers' countries (the lawyer's existing NDPA question, now with two more recipients)

### Questions to put to the lawyer

1. Auto-renewing subscriptions sold to Nigerian businesses and to individuals: what must the sign-up and cancel flow say, and is a 3-day grace period, with no new hours in it, acceptable in the Terms?
2. Is "plan hours expire each month, no refund of unused hours" enforceable and fair as worded?
3. Refunds (decided 2026-11-16, the lawyer reads them before launch): is the refund window rule (7 days for a first payment, 14 for a pack, section 3.5) acceptable, and what do the card networks and the providers require for chargebacks?
4. Price changes at renewal: is a notice before a price rise required for subscribers, even though our rule is that legal pages promise no notice of policy changes?
5. Invoice records: how long must they be kept, and does that override an account deletion request?
6. Payment data going to Paystack and Flutterwave: what must the Privacy Policy say under the NDPA?
7. The trial: is keeping a one-way fingerprint of the email to stop repeat trials covered by the existing wording, or does it need its own line? Is ending it after 14 days acceptable?
8. Seller details on the invoice (registered name, address, registration number) and the right wording for VAT lines.
9. Selling to customers in other countries: does that need anything in the Terms or the Policy now?
10. The pilot accounts were told they have 5 hours for the pilot: what must we say, and how, now that their leftover hours end 60 days after paid plans open (decided 2026-11-16, one mail before)?
11. "Only the first note taker records": is it enough to say so in the Terms, given the other attendees' bots do not join?
12. The unread items already open from #191 and #198 stay open; this list adds to them.

### Questions to put to the accountant

Is Beyrichq LTD under the VAT threshold and may it opt out of charging VAT; whether a software subscription counts as a "professional service"; whether sales to customers abroad are zero-rated; stamp duty on receipts; e-invoicing timing for a company our size; and what to do about VAT or sales tax in the customer's own country.

## 10. What changes on `/pricing` when the numbers are decided

`web/components/pricing/content.ts` was built so the numbers are one edit, but the **per-person model changes its words**: today it says the hours are "shared by the whole team".

- Set `price` (`"$18"`) and `hours` (`"20 hours"`) on the first paid plan in `PLANS`; the cards and the comparison table read the same two fields. **Do this only in stage S11, after the cost condition of section 4.2 is met**: the price is decided but must not be printed before the flip has produced real cost data. The second tier (about 34 USD for 40 hours) is a suggestion; its number and both plans' names are still to choose.
- Words to change in the same file: `hoursUnit` of the two paid plans ("each month, shared by the whole team" becomes "each month, for you"); the plan `tagline`s and the names `Team` and `Team Plus` (the ids `team` and `team-plus` too: a per-person plan is not a team plan, so the names are open); the paid cards' `includes` line "Extra-hour packs when the team needs more" ("when you need more"); the table row "Hours shared across the team" (replace it by a row that says the hours are the person's own, and that Ask Beyric, translation and rewriting notes need a plan); the FAQ answer "How are meeting hours counted?" (it says the hours are shared by the whole team); `HERO.body` ("No plan is on sale yet ...") and the FAQ "Can I buy a plan today?" ("Not yet") once a plan is on sale; the `cta` texts ("Start with the trial") to the buy action.
- Add FAQ entries the real model needs: "Can my teammates read my meetings without subscribing?" (yes: they read what you share; they need their own plan for Ask Beyric, translation and rewriting notes); "What happens when the hours run out?" (recordings in progress finish, new ones stop until a pack or the next period; Ask, translation and rewriting keep working on a paid plan); "What happens if I stop paying?" (notes and export stay); "Who pays when my whole team joins the same meeting?" (the first note taker that joins records it and its owner's hours count). No "free" and no "unlimited" anywhere (DECIDED); Business stays hidden.
- Currency: the page shows one price per currency from the same plan row; with two currencies the page needs a currency note and must not imply a conversion.
- Outside this file: `web/app/llms.txt/route.ts` and the Terms line quoted in section 9. `web/components/site/json-ld.tsx` publishes no offer on purpose and can add one after prices exist.

## 10A. Later, only if companies ask

A **seat-based team plan** is the way a company could later pay centrally for its staff, and it is **not designed or built here**. The idea: a company's team owner buys N seats and assigns each seat to a member; a seat gives *that person* the ordinary person plan, with their own personal hours (so there is no shared pool and nobody can use up hours someone else expects); the company receives one invoice for the N seats; if a seat is removed, the person falls back to their own plan or to none, and their past notes stay theirs. A pooled team-hours plan is the alternative, but it brings back the "who used the hours" problem that made Adebayo drop the team pool, so **seats are the recommendation**. The team side already exists: owners and admins are real roles (`TEAM_ROLES`, `app/models.py:27`; `MANAGER_ROLES`, `app/teams.py:236`), and the places a seat would be released when someone is removed or leaves are `team_admin.remove_member` (`app/team_admin.py:210`, which calls `teams.leave`, `app/teams.py:375`).

**What is built now so this needs no rewrite, and no more:** the subscription row says who **pays** (`payer_user_id`, a person today) and who it is **for** (`user_id`), every gate and every grant reads the "for" person, an hours grant knows its subscription (`hour_grants.subscription_id`), and a payment belongs to a payer and may later cover several subscriptions. A seat is then simply a subscription whose payer is the company's owner. **Left out of the first migration on purpose:** `sponsor_team_id`, the nullable link from a subscription to the company's team. Adding a nullable column to Postgres is a quick, additive change that needs no backfill, so adding it later costs almost nothing; adding it now would leave an unused column nobody tests and invite code that guesses at the seat rules before they are decided. The only existing rule that will need a decision then is "at most one live subscription per person": a person with their own plan who is given a seat would need the seat to take precedence, or to stack. No seat screens are designed here.

## 11. What is decided, and what is still open

**There are no open yes/no questions.** Adebayo answered the last five on 2026-11-16 with "Yes, all five", taking my recommendations as written.

### Decided on 2026-11-16 ("Yes, all five")

| # | Decision | Where |
|---|---|---|
| 1 | First plan **18 USD for 20 hours a month**, decided now but **not printed on `/pricing`** until the flip has produced real cost data (the flip went live 2026-11-16 12:20 WAT); the second tier about 34 USD for 40 hours is a suggestion only; plan names still to choose | 4.2, 10 |
| 2 | The organizer's bot is preferred when teammates share a calendar event; **built last** (S12), only after the three-account live test (T1) | 2.5, 8 |
| 3 | Packs: 10 hours for about 10 USD per person, valid 12 months, only with a live plan; after a lapse leftover pack hours allow recordings but not Ask, translation or rewriting | 4.3 |
| 4 | Cancel any time with access to the end of the paid month and no part refund; refunds only for a double charge, a first payment within 7 days with no hours used, or an unused pack within 14 days; the lawyer reads it before launch | 3.4, 3.5 |
| 5 | The pilot accounts' leftover hours expire 60 days after paid plans open, with one mail before; a lawyer item | 3.9 |

Decided earlier and no longer questions: the trial's 14-day end (2026-11-15, "yes, good"). The older questions about pools, hours teams and teams of one were dropped by the per-person model.

### Open facts (not yes or no)

Each is a fact someone has to supply or measure, with who and what it blocks.

| Fact | Who | Blocks |
|---|---|---|
| Test keys for both new accounts (Paystack and Flutterwave), created by him and kept in a local env file only; whether each account's business verification is finished | Adebayo | S6 and S7 in the vendors' test modes; S11 |
| Beyrichq's registration and tax numbers for invoices; whether it charges VAT (section 6); the accountant's answers | Adebayo, accountant | S9 invoices; S11 |
| The naira price list: **decided 2026-11-17: the 20-hour plan NGN 25,000 a month and the 10-hour pack NGN 15,000 (1 USD is about 1,500 NGN, "round figures"; seeded by migration 0052)**; the second tier's naira price is open | Adebayo | the second tier; S11 |
| The measured cost per hour after the flip: a new `scripts/ai_cost_report.py` run on flipped meetings and the monthly Skribby invoice (the condition in 4.2) | the lead, Adebayo | printing the price in S11 |
| Plan names ("Team" no longer fits a per-person plan) and the second tier's number | Adebayo | S8 data rows; S11 |
| The lawyer's list: the six open questions from #198 together with the twelve in section 9 | Adebayo, lawyer | S11 |
| The three-account live test (T1) | Adebayo with two helpers | S12 |

## 12. Provenance and what I could not confirm

How I read pages: the web fetch tool refused every domain in this session ("unable to verify if the domain is safe"), so I downloaded each page with `curl` and converted the HTML to text myself, and I opened the Paystack pages (which answer `curl` with 403) in the in-app browser. I created no account, signed up nowhere and contacted no vendor. All pages were read on 2026-11-15.

| Claim | Page | How read |
|---|---|---|
| Flutterwave fees, VAT on fees, local-currency settlement default | https://flutterwave.com/ng/pricing | curl to text |
| Flutterwave webhooks (`verif-hash`, 200, 60 seconds, 3 retries 30 minutes apart, re-verify) | https://developer.flutterwave.com/docs/webhooks | curl to text |
| Flutterwave payment plans (card only, email tied, 3 retries, cancel, webhooks) | https://developer.flutterwave.com/docs/recurring-payments/payment-plans | curl to text |
| Flutterwave verification checks and the card `token` field | https://developer.flutterwave.com/docs/transaction-verification | curl to text |
| Flutterwave test cards and OTPs | https://developer.flutterwave.com/docs/testing | curl to text |
| Flutterwave settlement sample (`stampduty_charge`) | https://developer.flutterwave.com/docs/payment-operations/settlements | curl to text |
| Paystack fees, USD settlement pilot | https://paystack.com/pricing | in-app browser |
| Paystack webhooks (both signature forms, retries) | https://paystack.com/docs/payments/webhooks/ | in-app browser (text cut before the IP list) |
| Paystack subscriptions (cards and Direct Debit only, no retry of a failed charge, the event list) | https://paystack.com/docs/payments/subscriptions/ | in-app browser |
| Paystack charge authorization | https://paystack.com/docs/api/transaction/ | in-app browser (the section text) |
| Paystack test cards | https://paystack.com/docs/payments/test-payments/ | in-app browser |
| Paystack verify-transaction response (`authorization.country_code`, `bin`, `ip_address`, `currency`, `fees`) | https://paystack.com/docs/api/transaction/ ("Verify Transaction") | in-app browser, read through the page's own text |
| Skribby bot price, billable time, model prices, billing in USD by card | https://skribby.io/docs/guides/billing | curl to text |
| Fathom, Fireflies and Otter prices | https://fathom.ai/pricing, https://fireflies.ai/pricing, https://otter.ai/pricing | curl to text |
| Nigerian VAT reform summary | https://www.forvismazars.com/ng/en/services/tax/nigeria-s-tax-reform-in-focus/2025-nta-vat-key-business-changes | curl to text; an accounting firm's article, not the law |

**Could not confirm (every one is marked where it matters):**

- The Nigeria Tax Act and Administration Act text, and any official tax-authority page: `firs.gov.ng` timed out. A PwC PDF I tried answered 403.
- Paystack: whether VAT applies to its fees; an international fee cap; whether a Nigerian merchant can price a checkout in USD; its webhook IP list.
- Flutterwave: whether it accepts payers from every country (its coverage page is built by script and came back empty); the call that charges a saved card token (two pages returned 404); whether a Nigerian merchant can price in USD and be settled in USD; its own VAT help page (the page is built by script and came back empty); its refund and dispute pages.
- Provider onboarding for the company (KYC) and whether it is finished; both accounts are new and made for Beyric (Adebayo, 2026-11-15).
- What each provider's card-country field returns for a card issued abroad: the pages show sandbox samples only (section 5.2).
- That the provider's key prefixes let the app tell test keys from live ones (not read).
- Where Skribby's speaker names come from: the code and the lead's measurement say participant events, but no Skribby page I read says so (section 4A).
- How often Nigerian bank cards refuse a charge made without the cardholder present.
- Chargeback handling at both providers.
- Stamp duty on receipts.
- VAT or sales tax in other countries.
- The 0.52 to 0.91 USD cost range is the lead's relay of the 2026-11-14 report; I did not re-run the script and its comments say some OpenAI figures are from secondary sources. The one-transcript range is a projection, not a measurement.
- Competitor terms behind their guarantees and per-user limits (only the pricing pages were read).
- The vendor webhook behaviours above are from the vendor pages, not from a live test; they are "verified live" only when S6 and S7 run them in each provider's test mode.
