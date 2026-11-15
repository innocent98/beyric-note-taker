# Billing, plans and feature gates: design spec

Status: DRAFT for Adebayo's answers (written 2026-11-15, by a sub-session of the 21st lead). **Nothing here is built.** No application code, no migration and no endpoint come with this document.

Facts about today's code were read from `origin/main` at `871ed74f` (Alembic head **0047**). Facts about vendors and competitors were read from the vendor's own page on 2026-11-15; each one names its URL, and section 12 lists what could not be confirmed. If a sentence says "PROPOSED", nobody decided it yet. If it says "DECIDED", it is Adebayo's word from the handoff (`docs/HANDOFF-master.md`, 2026-11-14 23:45 block, section B).

## 0. Read this first

**What this is for.** Today every account gets every feature and a lifetime cap of 5 recorded hours. Nobody can pay. This spec says how a team pays for hours, how those hours are counted and shared, what happens at the limits, and how money gets in through Flutterwave (and later Paystack), in small safe steps.

### The five findings that matter most

| # | Finding | Why it matters |
|---|---|---|
| 1 | **At about 15 USD for 20 hours, the margin is thin, and negative on today's Skribby-only cost.** With the one-transcript flip, a team that uses all 20 hours leaves about 1 to 3 USD (7 to 20%) before servers, support, tax and currency loss. On today's Skribby meetings (0.74 to 0.91 USD an hour) the same team costs us 15 to 18 USD: a loss. | Sections 4.2 and 4.3. Hold the price until the flip is proven and the cost is measured again (Question 1). |
| 2 | **Pooled team hours look small next to the seat prices competitors show.** Otter's pricing page lists a Pro plan with 1,200 recording minutes (20 hours) per user a month at about 17 USD per user. Fathom shows unlimited recordings per user on its team plans from 15 to 19 USD per user. Our 20 hours are for the whole team. | Section 4.4. Our bot costs real money per hour (Skribby 0.35 USD an hour), so we cannot copy "unlimited". The pricing page must explain "hours shared by the team" in the first line. |
| 3 | **Neither provider's own subscription feature fits our rules.** Flutterwave plans are card-only, tied to one email, retried 3 times 30 minutes apart, then cancelled. Paystack says a failed subscription charge is not retried. We want a 7-day grace period and retries over days. | Section 5.3. Recommendation: we keep the billing clock ourselves and use the providers only to take a payment (hosted checkout, then a saved card for renewals, with a "pay link" mail as the fallback). |
| 4 | **Where the money lands matters.** Our biggest costs (Skribby, OpenAI) are billed in USD by card. Flutterwave's pricing page says international cards are charged and settled in the local currency by default. Paystack's page says the same for naira, and describes USD settlement as a public pilot. Neither page confirms a Nigerian business can price in USD and be settled in USD. | Sections 5.5 and 12. This is the first thing to test in each provider's test mode, and the reason the first build should not promise USD until it is proven. |
| 5 | **The hours rule lives in one place today, which makes the change small and safe.** Every way of sending the bot (pasted link, Send now, calendar auto-join) goes through `placement.reserve_slot`; the hours check is one block there (`app/placement.py:175`). Replacing a per-person lifetime cap with a per-team monthly pool is a change to that block, the ledger and the warnings, not a rewrite. | Sections 1 and 2. Two real gaps found on the way: the "hours used up" notice is sent once per skipped meeting (a team with an empty pool would get a flood), and the running-meeting estimate counts only the person's own meetings, not their teammates'. |

### What is decided and what is only proposed

| Topic | Status | Where |
|---|---|---|
| Provider: Flutterwave for NGN and USD, or Paystack for NGN plus Flutterwave for USD, behind one provider layer routed by currency | **DECIDED** (his words); which of the two is **open** | 5 |
| His two Paystack business accounts are taken by another product; he will send no Flutterwave email | **DECIDED** (a fact he gave) | 5, Question 7 |
| Hours shared per team, never "unlimited" | **DECIDED** | 3 |
| The word "free" is not used anywhere; a one-time trial; no permanent free plan | **DECIDED** | 3.8, 10 |
| Calendar auto-join stays on every plan | **DECIDED** (my rec, he did not object) | 2 |
| Business plan not shown | **DECIDED** | 10 |
| First paid plan: about 20 meeting hours a month for about 15 USD; next tier 40 h | His guess, **not a decision** | 4 |
| Everything else in sections 3, 5 to 9 | **PROPOSED** | marked inline |

### Decisions needed, in short

Ten yes or no questions with my recommendation are in section 11. Facts only an accountant or lawyer can supply are in sections 6 and 9.

## 1. What exists today, and what a plan replaces

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
| The stop | `app/placement.py:174-179` inside `reserve_slot` | refuses with `hours_cap_reached` (403) when used is at or above the cap, under the placement lock |
| Who calls it | `services.py:943` (pasted link), `send_now.py` (Send now), `join_runner.py:640` (calendar auto-join) through `place_bot` | one gate for all three |
| What the person is told | `services.py:540-553` `hours_cap_message`; `join_runner.py:810-830` `_SKIP_REASONS` and `_NOTICES` | the message says "for the pilot" and asks them to write to hello@beyrictech.com; a calendar skip raises one notification per skipped meeting (dedupe key `calendar.{reason}:{join.id}`) |
| What the screens read | `app/api.py:191-205` (`usage` in the session payload: `used_seconds`, `cap_seconds`) | the sidebar usage bar and the "hours used up" state |
| Admin | `app/admin.py:87-126, 188-194`, `app/api_admin.py:68-110`, `app/api_schemas.py:448-513` | per-person override and the pilot default |
| Sign-up brake | `app/signup.py:86` `slots_taken`, `app_state.max_accounts` (10) | the pilot account cap |
| Teams | `app/teams.py` (`MAX_OWNED_TEAMS = 3` at line 239, `create_owned_team` line 180, `default_membership` line 78), `app/visibility.py`, `app/team_invites.py:38-47` | membership, personal team, invitation caps |

### 1.3 What a plan has to replace

| Today | Becomes |
|---|---|
| One lifetime allowance per person | A monthly pool per paying team, plus a one-time trial per person |
| `effective_cap(user)` | `entitlements.pool_for(user)` (section 2.1): which pool pays for this person's recordings, and how much is left |
| `hours_used(user)` counts the person's own running meetings | Counts every running meeting charged to the same pool (teammates' too) |
| `_warn_when_low`: once per person, in-app only | Once per pool per period at 80% and at 100%, in-app plus a mail to the team's owners and admins (section 3.3) |
| `hours_cap_message` ("for the pilot", asks for a mail) | A message that names the team's pool and links to the billing screen (section 3.3) |
| Admin sets a number on a person | Admin grants hours to a team with a written reason (`hour_grants`, kind `admin_gift`); the platform admin stays uncapped |

### 1.4 Personal team and the team model (what the plan attaches to)

From the multi-team design (`docs/superpowers/specs/2026-11-13-multi-team-design.md`, approved) and `app/teams.py`: every person has one permanent personal team (nobody else can join it, it has no integrations, Slack and Linear are refused on it with 409 `personal_team`) and can belong to several other teams; a person may own up to 3 ordinary teams. A meeting starts private and the recorder shares it to one team or moves it to one. Billing was deliberately left out of that design ("later, subscription and billing attach to a team").

PROPOSED rules for where billing attaches:

1. **A subscription belongs to an ordinary team, never to a personal team.** A person who wants to pay alone creates a team of one (they can own three). The personal team only ever holds the person's trial hours. Reason: "teams, not individuals" is the target market, the personal team is a space nobody else can enter, and a plan on a team keeps one pool and one payer.
2. **Every person has one "hours team": the pool their recordings are charged to.** Default: the same team the app already treats as their default (`teams.default_membership`: their one ordinary team if they have exactly one, else their personal team). They can pick another team they belong to in Settings. The meeting is charged at the moment the recording is prepared, to the hours team the recorder has at that moment. Sharing, moving or unsharing the meeting later never moves the charge (this matches the multi-team design: "hours are still charged to the recorder", section 6.4).
3. **The payer is a person**: the team's owner by default (only an owner can start, change or cancel a plan; admins can buy extra-hour packs; members can see the pool). If the owner changes (`teams.transfer_owner`, `teams.py:525`) the saved card does not move with them: the plan keeps running to the end of the paid period and the new owner must add a card to renew (section 3.7).

## 2. The plan model, entitlements and where each check lives

### 2.1 Model in one paragraph (PROPOSED)

Plans are rows in a table, not code: a plan has a code, a name, monthly hours, a member limit and one price per currency. A team has at most one live subscription row with a status. Hours are held in grants (a trial, a plan period, a pack, an admin gift), and every counted recording is split across grants in a fixed order. One small module, `app/entitlements.py`, answers every question ("may this person start a recording?", "may this team ask Beyric?", "how many hours are left?"). No other code compares plan names or statuses, the same way `app/visibility.py` is the only place that decides who may read a meeting.

### 2.2 Entitlements for launch (PROPOSED)

The pricing page already promises that "every feature exists for every account" and that plans differ only in hours. So the launch plans differ in **hours and a member limit only**. The gates below exist so a feature can be held back later, and so a team with no active plan stops costing us money.

| Plan state | What the team can do | What stops |
|---|---|---|
| `trial` (a person's own, personal team) | everything, until the trial hours run out | new recordings once the hours are used |
| `active` or `past_due` (inside the 7 days) | everything | nothing |
| `lapsed` (the grace ended, no renewal) or no plan | read, search, export, tasks board, decisions, settings, billing, delete | new recordings, calendar auto-join joins, Ask Beyric, new translations, connecting Slack or Linear |
| Pool empty (plan active, hours used) | everything that does not need new recorded hours | new recordings and auto-joins until a pack or upgrade |

Cost reasoning: recordings cost 0.5 to 0.9 USD an hour; Ask and translation call OpenAI per use; reading, exporting and the board cost almost nothing, so they stay open after a lapse. That keeps the promise "notes and export stay available".

### 2.3 Per-feature gates and where each check lives

| Gate | Check goes here (file:line today) | Refusal | Notes |
|---|---|---|---|
| **Join: pasted link, Send now, shared link** | `app/placement.py:174-179` in `reserve_slot`, replacing the per-person block with `entitlements.check_join(session, user, now)` | keep 403 `hours_cap_reached` (the frontend already knows it); add a `reason` field (`pool_empty`, `plan_lapsed`) | Runs under the placement lock, so two starts cannot both read the last free hour. `services.placement_refusal` (`services.py:548`) maps it. |
| **Calendar auto-join** | the same line, reached through `join_runner.place_bot` (`join_runner.py:640`); skip reasons in `_SKIP_REASONS` (`join_runner.py:810`) | `hours_cap` stays; add `plan_lapsed` | Connecting a calendar, the rule picker and the per-event switch are never gated (DECIDED: auto-join on every plan). |
| **Ask Beyric** | `app/api_ask.py` route `POST /ask` (line 82) calling `ask.answer` (`app/ask.py:841`, daily limit `ask_daily_limit` at line 857) | 403 `plan_inactive` for a lapsed team | The existing per-person daily limit stays as a cost brake. |
| **Export** (PDF, Word) | `app/api.py:516` `export_meeting` | not gated | Promise: export stays after a lapse. |
| **Translation** | `app/api_translation.py:73` `request_translation` | 403 `plan_inactive` for a lapsed team; reading an existing translation is never gated | French and Spanish notes only today. |
| **Integrations** (Slack, Linear) | `app/api_integrations.py` connect routes (personal-team refusal at line 31-41 shows the pattern) | connecting refused when lapsed; posting for earlier meetings continues | A post cannot be unsent anyway (multi-team design 6.9). |
| **Team size** | `app/teams.py:302` `join`, `app/team_invites.py:240` `_check_caps` (today `MAX_PENDING = 10`, `MAX_PER_DAY = 20`) | 409 `member_limit` | There is no seat price, but a pooled plan with no limit could be shared by hundreds. Limit comes from the plan row. The numbers are not decided (Question 2 asks only for the rule; Adebayo sets them). |
| **Owned teams** | `app/teams.py:239` `MAX_OWNED_TEAMS = 3` | 409 `team_limit` | Stays 3; later it can come from the plan. |
| **Sign-up** | `app/signup.py:86` `slots_taken`, `app_state.max_accounts` (10) | pilot full | Keep it as the brake on trial give-aways when sign-up opens wider (section 3.8). |

### 2.4 What the screens read

`app/api.py:191-205` builds `usage` for the session (`used_seconds`, `cap_seconds`). Keep both names and fill them from the person's hours team so the existing sidebar bar keeps working, and add `pool` fields (team name, period end, status, warning level) the frontend can use when it is ready. A new `docs/fe-integration-guide-billing.md` is built from live captures in the stage that adds each field (section 8).

## 3. Hours accounting, limits, grace, downgrade, lapse, cancel, refund

All rules in this section are **PROPOSED** except "shared per team, never unlimited".

### 3.1 What counts

| Question | Rule | Check against today's code |
|---|---|---|
| Unit | seconds of the recording the pipeline prepared (what we transcribe and pay for); shown as hours with one decimal | same as `usage.py` today |
| Many people in one meeting | counts **once**: the recording is what counts, not the attendees | `placement.py` already refuses a second bot into the same live meeting (`meeting_has_note_taker`, `already_in_meeting`), so it holds for a live meeting. The same Meet link used again later is a new recording and counts again. |
| Failed bots | count **zero**: nothing is counted unless audio was prepared | true today (`pipeline.py:1053` runs only when a recording exists). Our own cost is not zero: Skribby bills a bot from creation to its end, and a bot never admitted is free only for the first 10 minutes of waiting (https://skribby.io/docs/guides/billing, "What Counts as Billable Time"). |
| Shared, moved, viewed, exported, asked about | cost no hours | by design |
| Rounding | none: counted in seconds | Skribby bills per minute, so we lose under a minute a meeting; fine |
| Whose pool | the recorder's hours team at the moment the recording is prepared; never re-charged | section 1.4 |

### 3.2 The pool and the order hours are used

A pool is the team's grants. For a recording of S seconds charged to a team, the seconds are taken in this order, splitting a recording across two grants when needed:

1. the recorder's own trial grant, if any is left (trial hours are the person's; using them first keeps the team's paid hours),
2. the team's plan hours for the current period (they expire at the period's end),
3. the team's packs, oldest expiry first.

Each split is recorded in `usage_allocations` (section 7), so a grant's remaining hours are always a sum, never a number someone edited. This is why a table of allocations is proposed instead of a running balance: it is auditable, and an expiry or a refund can be explained line by line.

### 3.3 Warnings, stop and the exact moments

| Moment | What happens | Who is told | How |
|---|---|---|---|
| Period starts (first payment, or each renewal) | plan hours for the period are granted; the 80% and 100% flags for this pool reset | owners and admins: receipt mail (section 6) | mail |
| Used reaches **80%** of what the pool holds (plan hours of this period plus unexpired pack hours) | warning once per pool per period | owners and admins; the person whose recording crossed it | in-app notification for each; one mail to owners and admins. Today's 80% rule (`LOW_HOURS_FRACTION`) is per person and in-app only. |
| Used reaches **100%** | **in-progress recordings finish and are counted**; **no new recording starts** (pasted link, Send now, calendar auto-join all refuse with `hours_cap_reached`, reason `pool_empty`) until a pack is bought, the plan is upgraded or the period renews | owners and admins; every member sees the sidebar bar full | one mail to owners and admins, in-app for all. **Only one** "pool is empty" notice per pool per period, not one per skipped meeting (today's code raises one per skipped meeting: `join_runner.py` dedupe key includes the join id). Each skipped meeting still shows as "Not recorded" with a Send now button, which is the existing way to recover once hours exist. |
| Pack bought or upgrade paid | hours are granted immediately and the stop lifts | the buyer | the billing screen and a receipt mail |
| Period ends with hours left | **unused plan hours expire** (no rollover); pack hours stay until their own expiry | none | the usage screen shows "expired" lines |
| Renewal payment fails | status `past_due`, **grace of 7 days from the due date** (service continues in full) | owners and admins | mail on day 0, day 3 and day 6; banner on every screen while in grace |
| Our retries during grace | day 1, day 3 and day 5 at the same hour, using the saved card; each failure and the last success are written to the payment table | | our own scheduler (section 5.3) |
| Grace ends unpaid (day 7) | status `lapsed`: the stops in section 2.2 apply; the team's data and exports are untouched; remaining pack hours still work for recordings (they were paid for) | owners and admins | mail once; banner |
| Anyone pays later | status returns to `active`, a new period starts from the payment date, hours are granted | | |

The overshoot to accept (PROPOSED): a meeting that was already running when the pool hit zero finishes and is counted in full even if that takes the pool below zero; the shortfall is **written off**, not carried into the next period and not billed. The risk is bounded: concurrent bots are limited (`user_bot_limit` per person, `bot_max_local` for the host) and a bot stops after 6 hours (`max_poll_hours`). Several teammates can start bots in the last minutes; today's estimate for running meetings (`usage.hours_used`) covers only the person's own meetings, and the pool version must count every running meeting charged to the pool.

### 3.4 Changing plan

| Change | Rule | Reason |
|---|---|---|
| **Upgrade** (Team to Team Plus) | takes effect at once; we charge the price difference for the days left in the period, and add the extra hours for the same fraction of the month (rounded down to 0.1 hour); the next renewal is at the new price | simple to explain; the customer gets what they pay for immediately |
| **Downgrade** | takes effect at the next renewal (`pending_plan_code`); the current period is untouched; if the team has more members than the lower plan allows, the owner must remove some before the downgrade can be confirmed | no refunds and no half periods |
| **Cancel** | the owner cancels; no new renewal; everything works until the period ends; no refund of the unused part; packs stay valid for their own 12 months; the team goes to the "no plan" state at the period's end | the standard SaaS rule |
| **Price change** | applies from the next renewal only; the subscription row keeps the price it was sold at until then | DECIDED proposal in the handoff, kept here |
| **Currency change** | not allowed mid-subscription; a new subscription in the other currency starts at the next renewal if the owner asks | avoids half-converted periods |

### 3.5 Refunds (PROPOSED, and the first thing to give the lawyer)

| Case | Rule |
|---|---|
| Charged twice, or charged by mistake | always refunded in full, by a person at Beyric, in the same currency |
| First plan payment | refunded on request within 7 days if no hour of it was used |
| Any other payment | no refund; at our discretion for a real service failure on our side |
| Pack | refunded on request within 14 days if none of its hours were used |
| Where it happens | first version: an admin action in our own admin area that calls the provider's refund endpoint, then marks the payment `refunded` and removes the grant; **no** self-service refunds |

The competitor anchor: Fathom's pricing page carries a 90-day guarantee label on its plans (https://fathom.ai/pricing, read 2026-11-15); I did not read what its terms say behind that label. Chargebacks (a bank pulling a payment back) are not described in any provider page I read; treat them as a refund plus a flag on the team and ask each provider how disputes arrive before going live.

### 3.6 Lapse: what the team keeps

Notes, transcripts, decisions, tasks, exports and the ability to delete everything stay for as long as the account exists (as today). How long we keep a lapsed team's data is not decided; the Privacy Policy says transcripts and notes are kept "until you ask us to delete them or close your account" (`web/components/legal/privacy-content.tsx:100`), so the lapse changes nothing. The lawyer should confirm that this is what we want to say once people stop paying.

### 3.7 Edge cases to decide while building

| Case | Proposed handling |
|---|---|
| The owner deletes their account while paying | `teams.remove_users` already hands the team to an heir; the plan is set to end at the paid period's end, the heir is told, and the heir adds a card to renew. The saved card token is deleted with the owner. |
| The team is deleted while subscribed | refused until the owner cancels; unused packs are lost, and the Terms say so |
| A member leaves | their past recordings stay charged to the pool; their trial hours stay with them |
| Hours team points at a team the person left | falls back to their default (section 1.4) |
| The platform admin | stays uncapped (`effective_cap` returns none for `is_admin` today) |
| Pilot accounts (10, 5 hours each) | keep their remaining pilot hours as a `pilot` grant on their personal team; nothing is charged; the grant has no expiry; before launch they get one plain mail explaining the plans. Their existing ledger rows stay. |

### 3.8 The one-time trial (PROPOSED)

About 3 hours, once per person, given at the first confirmed sign-in, no card. It is tied to the person (and to the email fingerprint, so deleting an account and signing up again does not give a second trial; the ledger already does this for hours). It is spent first (section 3.2). No "free" wording anywhere: the word is "trial" and "included". **Risk:** every trial costs us about 0.6 to 0.9 USD an hour, so a fake account costs about 2 to 3 USD. The brake is the same one that exists now: verified email, and the account cap (`max_accounts`) kept as a ceiling on how many new trials can start while sign-up is open. Section 8 adds a daily trial-hours budget switch.

## 4. Cost and margin arithmetic

### 4.1 Inputs, with sources

| Input | Value | Source |
|---|---|---|
| Real cost per recorded hour, all meetings | **0.52 to 0.65 USD** | `scripts/ai_cost_report.py`, run 2026-11-14 on one account, 6.12 recorded hours (3.26 on Skribby), as reported in `docs/HANDOFF-master.md` section B. I did not re-run it: the lead cannot reach the server. OpenAI prices in the script are partly from secondary sources (its own comments say so). |
| Cost per recorded hour, Skribby meetings only | **0.74 to 0.91 USD** | same report |
| Cost after the one-transcript flip (not live) | about **0.56 to 0.66 USD** | the lead's estimate in the handoff, same report, not measured. The flip saves our own transcription (0.27 to 0.36 USD an hour in the script). |
| Skribby bot | 0.35 USD an hour, billed per minute, from creation to the end (lobby wait included, first 10 minutes free if never admitted) | https://skribby.io/docs/guides/billing |
| Transcription model on Skribby (today) | ElevenLabs v2: 0.20 USD an hour | same page |
| The 1.0 to 1.25 "billed hours per recorded hour" factor | a judgement in the script, no source | check the real Skribby invoice |
| Flutterwave fees | local 2% (split on the page as 1.4% transaction fee plus 0.6% platform fee), international **4.8%**; the page says a 7.5% VAT applies to the transaction fees; international cards are charged and settled in the local currency by default | https://flutterwave.com/ng/pricing (read 2026-11-15) |
| Paystack fees (Nigeria) | local 1.5% + NGN 100 (100 waived under NGN 2,500, fee capped at NGN 2,000); international **3.9% + NGN 100**; international cards are charged and settled in naira by default, with an option to be settled in USD; the page calls USD settlement a public pilot, paid into Zenith Bank domiciliary accounts | https://paystack.com/pricing (opened in the in-app browser; the `/ng/pricing` address gave a 404) |
| Paystack: VAT on its fees, an international-fee cap | **not on the page**; unconfirmed | section 12 |

Flutterwave's effective rates, from the page: international 4.8% x 1.075 = **5.16%**; local 2% x 1.075 = **2.15%**. Paystack's effective rate cannot be completed without its VAT rule.

### 4.2 Margin on the first plan (about 15 USD, 20 hours), paid by an international card through Flutterwave

Net after the fee: 15.00 x (1 - 0.0516) = **14.23 USD**. Cost = hours used x cost per hour. "Margin" is before servers, mail, support, tax and currency loss.

| Cost case | Cost per hour | Cost at 20 h used | Margin at 20 h | Margin as % of 15 USD | Team must use less than this to make money |
|---|---|---|---|---|---|
| Blended, as measured (includes non-Skribby meetings) | 0.52 to 0.65 | 10.40 to 13.00 | +1.23 to +3.83 | 8 to 26% | 21.9 to 27.4 h |
| **Skribby meetings only, today** | 0.74 to 0.91 | 14.80 to 18.20 | **-3.97 to -0.57** | -26 to -4% | **15.6 to 19.2 h** |
| One transcript (the flip), projected | 0.56 to 0.66 | 11.20 to 13.20 | +1.03 to +3.03 | 7 to 20% | 21.6 to 25.4 h |

At 70% use (14 hours): blended +5.13 to +6.95; Skribby-only today +1.49 to +3.87; one transcript +4.99 to +6.39.

What price would make a plan safe? Break-even price for 20 hours on the worst Skribby-only case: 18.20 / 0.9484 = **19.19 USD** (this is the lead's "nearer 19"). For a 30% margin on the one-transcript case: 13.20 / (0.9484 - 0.30) = **20.36 USD**, and 11.20 / 0.6484 = 17.27 USD at its best. So 15 USD for 20 full hours is viable only after the flip, and only because few teams will use every hour. **No team's real usage has been measured** (one account); the shadow stage (section 8, S1) measures hours per team per month before any price is final.

### 4.3 Packs at about 1 USD an hour

A 10-hour pack at 10 USD, international card: net 10 x 0.9484 = 9.48, so 0.948 USD an hour. Margin per hour: blended 0.30 to 0.43; one transcript 0.29 to 0.39 (31 to 41%); **Skribby-only today 0.04 to 0.21**. Paystack's fixed NGN 100 would hurt a small pack, so packs have a minimum of 10 hours (PROPOSED).

### 4.4 Competitor anchors (read 2026-11-15)

| Vendor | What its page shows | URL | Caveat |
|---|---|---|---|
| Fathom | Free plan with unlimited recordings; Team 19 USD monthly or 15 USD annual per user (2 users minimum); Business 34 or 25; Premium (individual) 20 or 16; Enterprise 35 annual only; a 90-day guarantee label on the plans | https://fathom.ai/pricing | prices are per user; recordings unlimited |
| Fireflies | Free; Pro 18 USD monthly or 10 annual per seat; Business 29 or 19; Enterprise 39 annual only; Pro storage of 8,000 minutes per seat and 20 to 50 AI credits depending on the plan | https://fireflies.ai/pricing | per seat; transcription listed as unlimited |
| Otter | Basic 300 transcription minutes a month; Pro 1,200 in-app recording minutes, listed at 16.99 USD per user per month with a second, promotional 8.49 figure; Business 30 and 24 per user per month; the monthly minute limits are marked as not rolling over | https://otter.ai/pricing | the monthly versus annual label on each number was not clear in the page text I extracted |

What this supports (my reading, not the vendors'): (a) "no rollover of plan hours" has a precedent (Otter marks its minute limits as no rollover); (b) these tools price per seat and give each person many hours or unlimited recordings, so 20 hours for a whole team looks stingy unless the page leads with price per team and says why hours are counted; (c) a free tier of unlimited recordings is something we cannot afford (see the multi-team spec, section 11).

## 5. The provider layer

### 5.1 The interface (PROPOSED)

Same pattern as `app/bot_provider.py` (a `Protocol` plus a fake for tests). One new module `app/billing/` with:

| Piece | Job |
|---|---|
| `PaymentProvider` (Protocol) | `name`, `currencies`; `start_checkout(payment, payer, return_url)` returns a hosted-checkout link and our reference; `verify_webhook(headers, raw_body)` raises if the request is not genuine; `parse_event(raw_body)` returns a neutral `ProviderEvent` (kind: `charge_succeeded`, `charge_failed`, `refund`, `dispute`; our reference; the provider's transaction id; amount in minor units; currency; payer email; a reusable card token if present); `fetch_transaction(provider_tx_id)` re-reads the payment from the provider; `charge_saved_method(token, amount, reference)` for renewals; `refund(provider_tx_id, amount)` |
| `FlutterwaveProvider`, `PaystackProvider` | the two real adapters, test mode and live mode by keys |
| `FakeProvider` | deterministic, in-memory; the only provider CI ever talks to |
| `routing.provider_for(currency)` | one table from settings, for example `{"NGN": "paystack", "USD": "flutterwave"}`; until a Paystack account exists, `{"NGN": "flutterwave", "USD": "flutterwave"}` |

Nothing outside `app/billing/` knows a provider's name: payments, grants and entitlements use neutral fields. A webhook route per provider (`POST /api/billing/webhooks/{provider}`) is the only provider-shaped surface.

### 5.2 Routing by currency (PROPOSED)

The currency is chosen **once per team before checkout**, from the billing country the owner enters (section 6): Nigeria gives NGN, anywhere else gives USD. The card's own country (Paystack's authorization carries `country_code`; Flutterwave's transaction carries `card.country`) is a check, not the chooser, because it is known only after the card is entered. The naira price list is **fixed and set by Adebayo** (no live conversion), reviewed when the rate moves; the rate is his pending decision (handoff C5).

### 5.3 Who owns the billing clock (recommendation)

| Option | What the providers say | Verdict |
|---|---|---|
| Use the provider's subscriptions | Flutterwave: you create a payment plan and a customer is subscribed on the first charge; card payments only; the subscription is tied to the customer's email and that cannot be changed afterwards; it emails the customer a reminder before each charge; **a failed charge is retried 3 more times at 30-minute intervals**, and after three failures in a row the subscription is cancelled; it sends a webhook for each charge and for cancellation (https://developer.flutterwave.com/docs/recurring-payments/payment-plans). Paystack: cards and Direct Debit (Nigeria) only; billing dates by day of month (created after the 28th is billed on the 28th); a webhook `invoice.create` three days before the charge, then `charge.success` or `invoice.payment_failed`; **the page says a failed subscription charge is not retried** (https://paystack.com/docs/payments/subscriptions/) | Cannot give a 7-day grace with day-1, 3, 5 retries, a mid-period upgrade, a pack, or a price change at renewal without fighting the provider |
| **We keep the clock** (recommended) | First payment through hosted checkout, which returns a reusable card token. Renewals: our scheduler charges the saved token on the due date and on retry days, writes every attempt, and moves the status. Paystack documents the call: `POST /transaction/charge_authorization` with an `authorization_code` marked reusable, and a `queue` flag for scheduled charges (https://paystack.com/docs/api/transaction/). Flutterwave's verify response carries a card `token` (https://developer.flutterwave.com/docs/transaction-verification) but **I could not open a Flutterwave page on charging a saved token** (the two addresses I tried returned 404), so that call is unconfirmed. | One state machine, ours, testable with a fake clock. Needs a fallback. |
| Fallback inside the recommended option | If a renewal charge fails or no usable token exists, we mail the owner a **pay link** (a fresh hosted checkout for the same amount) at the same moments in section 3.3. A manual renewal is the safe floor. | Plan for it from day one: I could not find any source on how often Nigerian bank cards refuse a charge made without the cardholder present; assume many do. |

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

Both are public sandbox values, safe to put in a test plan. **A dashboard test key needs an account at each vendor, and I did not create one** (task rule). Whether Beyrichq has a Flutterwave account with test keys is Question 7's companion fact, not something I could check.

### 5.6 USD and settlement: the open risk

Section 4.1 shows both pricing pages say international cards are charged and settled in the local currency by default. For USD checkout the first test-mode job is to prove, per provider: can a Nigerian merchant create a USD payment, what does the customer see, in what currency and at what rate is it settled, and can it be settled in USD. Until that is proven the first build should **not promise USD**; with Paystack, USD settlement is described as a pilot. We pay vendors in USD by card (Skribby's billing page lists USD and card payment through Stripe), so a USD-settled account is an operations need, not a nicety.

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

Next free Alembic number: **0048**. Checked on 2026-11-15: `alembic/versions/` ends at `0047_bot_name.py` on `main` (`871ed74f`), and `git ls-remote --heads origin` shows only `main` and two old branches, so no open work has taken 0048 (the `gh` CLI could not reach GitHub for a PR list; re-check when starting). All migrations are additive, so the old code keeps working during a rollout.

| Table (new unless marked) | Key columns | Notes |
|---|---|---|
| `plans` | `code` (primary key: `trial`, `team`, `team_plus`), `name`, `monthly_seconds`, `member_limit`, `is_public`, `sort_order`, `active` | rows are data, edited by migration or admin; no plan name appears in code except in tests |
| `plan_prices` | `plan_code`, `currency` (`NGN`, `USD`), `interval` (`month`), `amount_minor`, `active_from`, `active_until` | price history; a subscription points at the price row it was sold at (a price change at renewal is a new row) |
| `products` / pack prices | `code`, `seconds`, `currency`, `amount_minor`, `valid_months` | packs of 10 hours at launch |
| `team_subscriptions` | `team_id`, `payer_user_id`, `plan_code`, `price_id`, `status` (`active`, `past_due`, `lapsed`, `canceled`, `ended`), `currency`, `provider`, `current_period_start`, `current_period_end`, `cancel_at_period_end`, `pending_plan_code`, `grace_ends_at`, `next_charge_at`, `retry_count` | at most one live row per team (partial unique index); never on a personal team (checked in the route and in `entitlements`, because a database check cannot look at another table) |
| `payment_methods` | `team_id`, `payer_user_id`, `provider`, `token` (encrypted with `app/vault.py`), `brand`, `last4`, `expiry`, `email_at_provider` | never a card number; the token only |
| `payments` | `team_id`, `subscription_id`, `provider`, `provider_tx_id`, `reference` (unique), `kind` (`plan`, `renewal`, `upgrade`, `pack`), `currency`, `amount_minor`, `status` (`pending`, `succeeded`, `failed`, `refunded`, `partly_refunded`), `paid_at` | unique (provider, provider tx id) |
| `billing_events` | `provider`, `dedupe_key`, `type`, `payload` (JSONB), `signature_ok`, `received_at`, `processed_at`, `error` | the webhook inbox; unique (provider, dedupe key); payloads are pruned after a retention period to be chosen |
| `hour_grants` | `team_id` or `user_id` (one of them), `email_hash`, `kind` (`trial`, `pilot`, `plan_period`, `pack`, `admin_gift`), `seconds`, `starts_at`, `expires_at`, `source_payment_id`, `reason`, `created_by` | unique (kind, source payment id) so a replay cannot grant twice |
| `usage_allocations` | `usage_entry_id`, `grant_id`, `seconds` | splits a counted recording across grants (section 3.2) |
| `usage_entries` (changed) | add `team_id` (set null on team delete) | the pool a recording was charged to; old rows keep null and are read as "pilot" |
| `invoices` | `number` (unique), `team_id`, `payment_id`, `issued_at`, `currency`, `subtotal_minor`, `vat_rate_bp`, `vat_minor`, `total_minor`, `seller` (JSON snapshot), `buyer` (JSON snapshot) | the PDF is built on request |
| `billing_profiles` | `team_id`, `company_name`, `address`, `country`, `tax_id`, `billing_email` | |
| `user_settings` (changed) | add `hours_team_id` | the person's chosen hours team (section 1.4) |
| `refunds` | `payment_id`, `amount_minor`, `reason`, `created_by`, `provider_ref` | |

Migrations, in the order of the stages: **0048** plans, prices, products, grants, allocations, the two column additions, and the **backfill of pilot accounts into `pilot` grants** (each account's remaining hours = its cap minus its ledger, so nobody loses or gains hours); **0049** subscriptions, payment methods, payments, billing events, billing profiles; **0050** invoices and refunds. Each needs the usual round trip on a scratch database: one head, upgrade, downgrade. Downgrade of 0048 is only safe while no grant other than `pilot` exists.

Settings (flags), all off or dark first: `HOURS_POOL_MODE` (`off`, `shadow`, `enforce`), `BILLING_ENABLED`, `BILLING_ROUTES` (the currency to provider table), `BILLING_LIVE` (test keys or live keys), `TRIAL_SECONDS` (3 hours, 0 turns the trial off), `TRIAL_DAILY_BUDGET_SECONDS`, `BILLING_VAT_RATE_BP`.

## 8. Build stages: separate small PRs, in order

Every stage is its own PR, deployed one at a time, dark first, with its SOP, its checklist lines and (where a payload changes) a true integration guide built from captures against the running app. Locally only lint, format, type-check and the tests of the touched files run (CI runs the rest).

| Stage | What | Size | Flag | How it is tested without real money |
|---|---|---|---|---|
| **S0** | This spec, the checklist map | docs | none | link check |
| **S1** Hours model in shadow | Migration 0048; `app/entitlements.py` with `pool_for` and `status`; grants and allocations; the pilot backfill; an admin read of "per team: hours this month, what plan it would need"; nothing is enforced. A daily job logs where the new rule would have refused and the old rule did not (and the reverse) | M | `HOURS_POOL_MODE=shadow` | unit tests with the existing clock injection (`app/clock.py`); a backfill test with a one-member, a many-member and a deleted-account fixture; a shadow comparison test. **The shadow week is also the first real data on team hours.** |
| **S2** Enforce the pool, with the new warnings | The one block in `reserve_slot`; pool-wide running estimate; 80% and 100% notices with owner and admin mail; one "pool is empty" notice per pool and period; `usage` in the session payload gets pool fields; the message stops saying "pilot" and "hello@"; the hours-team setting | M | `HOURS_POOL_MODE=enforce` | boundary tests at 79.9%, 80%, 99.9% and 100%; two teammates starting bots at once under the placement lock; a running meeting finishing past zero; the join runner skip reason; no leak between two teams' pools. FE guide from live captures. |
| **S3** Provider layer and the webhook inbox | `app/billing/` (Protocol, routing, `FakeProvider`, Flutterwave adapter in test mode); migration 0049 (payments, events, methods, profiles, subscriptions, empty); the webhook route with verification, dedupe, re-fetch | L | `BILLING_ENABLED=false` | signature vectors written from the vendor pages above; replay of the same event twice; wrong amount, wrong currency, wrong reference; a bad signature; the raw-body test through the real proxy; Flutterwave sandbox card flow in test mode (needs test keys) |
| **S4** Subscriptions, packs and the lapse states | The status machine; our scheduler for due dates, retries, grace and lapse; checkout for a plan and a pack; upgrade proration; downgrade and cancel at period end; the lapsed-state gates (Ask, translation, integrations); the billing screens | L (may split into two PRs: money first, gates second) | `BILLING_ENABLED` for allow-listed teams | a table of (status, event) to (status, effect) run with a fake clock through every row; end to end with `FakeProvider`; in provider test mode: success, declined card, insufficient funds, a saved-token renewal |
| **S5** Invoices, billing profile, refunds, receipts | Migration 0050; invoice numbering and PDF; the billing profile before the first payment; the admin refund action; receipt mail | M | with S4 | invoice number uniqueness under two payments at once; PDF content test; refund of a pack with used hours is refused |
| **S6** Paystack adapter for NGN | the adapter and the routing change | M | `BILLING_ROUTES` | Paystack test cards (section 5.5); **blocked until Beyrichq has a Paystack account** (Question 7) |
| **S7** Trial, pilot conversion and the pilot's end | the 3-hour trial at first sign-in with the daily budget; the pilot mail; `max_accounts` becomes a trial brake | S | `TRIAL_SECONDS` | trial once per person and per email fingerprint; deleting and re-registering gives nothing |
| **S8** Go-live | `/pricing` numbers (section 10), Terms and Privacy (section 9), the live keys, one real small payment by Adebayo with his own card followed by a refund (**needs his explicit yes: it moves real money**), then the sign-up cap raised | S | `BILLING_LIVE` | the runbook; nothing here is tested by code |

A shadow-then-enforce order means a mistake in the hours rule shows up in the logs for a week before any customer meets it, and the pay screens come only after the rule they sell has been live.

## 9. What the Terms and Privacy Policy must say, and what to ask the lawyer

Today the Terms still say "The Service is free during the pilot" (`web/components/legal/terms-content.tsx:30`), which is the wording he rejected for the pricing page, and the hours message asks people to write to `hello@beyrictech.com`, which the audit says is not a confirmed mailbox. Both change in S2 and S8.

### Terms (new section, drafted by the lawyer from this list)

- the plans, that hours are counted in recorded hours, shared by the team, and what counts (section 3.1); that plan hours expire at the end of each period and packs last 12 months (PROPOSED); that unused hours are not refunded
- who the customer is: the team owner buys for the team and confirms they may do so
- auto-renewal, how to cancel, that cancelling ends the plan at the period's end, and that a failed payment starts 7 days of grace and then stops new recordings, Ask and translation while keeping the data
- price changes: that they apply from the next renewal (the handoff rule is "no notification of policy changes"; whether a price rise needs a notice is a contract question, below)
- taxes: VAT added or included, who issues the invoice (Beyrichq LTD)
- refunds and chargebacks (section 3.5)
- the trial: one per person, may be withdrawn for abuse, no card needed
- what happens to a team's data and packs when the team is deleted while subscribed
- a clause that no recording law is changed by paying (the existing "Recording laws and consent" section still stands)

### Privacy Policy (additions)

- the payment providers (Flutterwave, and Paystack once it is used) as recipients: what we send (the payer's name and email, the amount, our reference) and that card details are entered on the provider's page and are not seen or stored by us (only a token)
- what we keep: payments, invoices, the billing profile, the hours ledger; that invoices are kept after account deletion for the period the law requires (to be confirmed), and that the existing "hours counted with a one-way fingerprint of the email address" (`privacy-content.tsx:105`) also covers the trial
- the transfer of payer data to the providers' countries (the lawyer's existing NDPA question, now with two more recipients)

### Questions to put to the lawyer

1. Auto-renewing subscriptions sold to Nigerian businesses and to individuals: what must the sign-up and cancel flow say, and is a 7-day grace period before service stops acceptable in the Terms?
2. Is "plan hours expire each month, no refund of unused hours" enforceable and fair as worded?
3. Refunds: is the 7-day / 14-day rule (section 3.5) acceptable, and what do the card networks and the providers require for chargebacks?
4. Price changes at renewal: is a notice before a price rise required for subscribers, even though our rule is that legal pages promise no notice of policy changes?
5. Invoice records: how long must they be kept, and does that override an account deletion request?
6. Payment data going to Flutterwave and Paystack: what must the Privacy Policy say under the NDPA?
7. The trial: is keeping a one-way fingerprint of the email to stop repeat trials covered by the existing wording, or does it need its own line?
8. Seller details on the invoice (registered name, address, registration number) and the right wording for VAT lines.
9. Selling to customers in other countries: does that need anything in the Terms or the Policy now?
10. The unread items already open from #191 and #198 stay open; this list adds to them.

### Questions to put to the accountant

Is Beyrichq LTD under the VAT threshold and may it opt out of charging VAT; whether a software subscription counts as a "professional service"; whether sales to customers abroad are zero-rated; stamp duty on receipts; e-invoicing timing for a company our size; and what to do about VAT or sales tax in the customer's own country.

## 10. What changes on `/pricing` when the numbers are decided

`web/components/pricing/content.ts` was built so the numbers are one edit:

- Set `price` (for example `"$15"`) and `hours` (for example `"20 hours"`) on each plan in `PLANS`; the cards and the comparison table read the same two fields. `names` and `taglines` are edited in the same array.
- The same file's words need one pass when a plan goes on sale: `HERO.body` says "No plan is on sale yet, and every price and number of hours below is still to be announced"; the FAQ answer "Can I buy a plan today?" says "Not yet"; the Trial's `cta` and the two paid cards' `cta` ("Start with the trial") change to the buy action; the trial row of the table keeps "To be announced" only until its hours are set.
- Add two FAQ entries the real model needs: "What happens when the hours run out?" (recordings in progress finish, new ones stop until a pack or the next period) and "What happens if I stop paying?" (notes and export stay). No "free" and no "unlimited" anywhere (DECIDED); Business stays hidden.
- Currency: the page shows one price per currency from the same plan row; with two currencies the page needs a currency note and the card must not imply a conversion.
- Outside this file: `web/app/llms.txt/route.ts` and the Terms line quoted in section 9. `web/components/site/json-ld.tsx` publishes no offer on purpose (its comment says so) and can add one after prices exist.
- The first line of the page should say "the team shares the hours" (finding 2).

## 11. Questions for Adebayo (yes or no)

Each has my recommendation and the reason, in plain words.

1. **Hold the 15 USD and 20 hours price until the one-transcript flip has run for a week and the cost is measured again?**
   **Recommend: yes.** On today's cost a team using every hour loses money, and after the flip the margin is thin (section 4.2). A week of shadow data (S1) will also show how many hours teams really use.

2. **Count hours the way section 3.1 says: pooled per team, once per meeting however many attend, failed bots count zero, charged to the recorder's chosen team at the moment the recording is ready, and each plan has a member limit (numbers set by you)?**
   **Recommend: yes.** It matches what the code already counts and keeps one clear rule for customers.

3. **At 80% warn the team's owners and admins; at 100% let recordings already running finish, stop new recordings and calendar joins until a pack or upgrade, and never bill beyond the plan?**
   **Recommend: yes.** A recording cut off mid-meeting is the worst experience; the overshoot is bounded and written off.

4. **Monthly billing only at launch, plan hours expire each month (no rollover), packs of about 1 USD an hour last 12 months and can be bought only by a team with a live plan, and a price change applies from the next renewal?**
   **Recommend: yes.** Monthly is the simplest to run; Otter marks its limits no rollover; packs priced above the plan's hourly rate push people to upgrade.

5. **If a renewal fails: 7 days of grace with our own retries, then stop new recordings, Ask and translation while keeping notes and export; cancel any time with access to the period's end and no part refund; refund only a double charge, or a first payment within 7 days with no hours used?**
   **Recommend: yes.** It keeps the promise that notes stay yours without leaving us paying for service nobody pays for.

6. **NGN when the billing country is Nigeria and USD everywhere else, with a fixed naira price list that you set (no live conversion)?**
   **Recommend: yes.** One currency per team avoids half-converted periods; a fixed list is easy to explain. USD stays unpromised until a test-mode run proves settlement (section 5.6).

7. **Build the first version on Flutterwave only (NGN and USD) behind the provider layer, and add Paystack later only if a Paystack account becomes available? Do you already have a Flutterwave account with test keys?**
   **Recommend: yes to Flutterwave first.** Your two Paystack accounts are taken, so Paystack would block the build. The layer makes it an adapter later. (The test keys are a fact only you can give; without them S3 can only run on the fake provider.)

8. **We keep the billing clock (our scheduler charges the saved card and falls back to a pay link), instead of the providers' own subscription feature?**
   **Recommend: yes.** Their retry and cancel rules (3 tries in 90 minutes then cancel; no retries at all) cannot give the 7-day grace.

9. **Paid plans attach only to ordinary teams; a person paying alone makes a team of one; the personal space holds only the trial?**
   **Recommend: yes.** One pool, one payer, and it matches "teams, not individuals".

10. **A one-time trial of about 3 hours per person (by verified email, no card), spent before the team's hours; the 10 pilot accounts keep what is left of their 5 hours as a pilot grant?**
    **Recommend: yes.** It keeps the pilot's promise, and the account cap stays as a brake on give-aways.

### Facts only you, an accountant or a vendor can give (not yes or no)

Beyrichq's registration and tax numbers for invoices; whether it charges VAT (section 6); the naira price list and rate; whether it has a Flutterwave account and keys; whether a Paystack account can be opened for this product; the real Skribby invoice hours (to replace the 1.0 to 1.25 factor); whether to send the six open lawyer questions from #198 together with the ten in section 9.

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
| Skribby bot price, billable time, model prices, billing in USD by card | https://skribby.io/docs/guides/billing | curl to text |
| Fathom, Fireflies and Otter prices | https://fathom.ai/pricing, https://fireflies.ai/pricing, https://otter.ai/pricing | curl to text |
| Nigerian VAT reform summary | https://www.forvismazars.com/ng/en/services/tax/nigeria-s-tax-reform-in-focus/2025-nta-vat-key-business-changes | curl to text; an accounting firm's article, not the law |

**Could not confirm (every one is marked where it matters):**

- The Nigeria Tax Act and Administration Act text, and any official tax-authority page: `firs.gov.ng` timed out. A PwC PDF I tried answered 403.
- Paystack: whether VAT applies to its fees; an international fee cap; whether a Nigerian merchant can price a checkout in USD; its webhook IP list.
- Flutterwave: the call that charges a saved card token (two pages returned 404); whether a Nigerian merchant can price in USD and be settled in USD; its own VAT help page (the page is built by script and came back empty); its refund and dispute pages.
- Provider onboarding for the company (KYC), and whether Beyrichq already has an account at either vendor.
- How often Nigerian bank cards refuse a charge made without the cardholder present.
- Chargeback handling at both providers.
- Stamp duty on receipts.
- VAT or sales tax in other countries.
- The 0.52 to 0.91 USD cost range is the lead's relay of the 2026-11-14 report; I did not re-run the script and its comments say some OpenAI figures are from secondary sources. The one-transcript range is a projection, not a measurement.
- Competitor terms behind their guarantees and per-user limits (only the pricing pages were read).
- The vendor webhook behaviours above are from the vendor pages, not from a live test; they are "verified live" only when S3 runs them.
