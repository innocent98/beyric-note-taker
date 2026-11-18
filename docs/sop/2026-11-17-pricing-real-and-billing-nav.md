# SOP: the real /pricing page, and billing reachable from the signed-in app

**What shipped.** (1) The public `/pricing` page now prints what is on sale: a one-time trial, one monthly plan, one pack, with both currencies. (2) The signed-in app got real ways into billing: a **Billing** link under Configure, a sidebar **hours card**, and links on the hours notices and the "Not recorded" hours reasons. No backend change, no migration, no new flag. PR from `feature/pricing-real-and-billing-nav`.

## Go-ahead to print prices, and what it does not settle

Spec section 10 and S11 say no price is printed before S11 and the cost condition of section 4.2. The go-ahead to print now is Adebayo's, **2026-11-17, about 20:12 to 20:20 WAT**, given to the lead: he said "the pricing page is lying", that he wants all billing switched on for everyone, and he approved the plans (the plan, the pack and both naira prices, decided that day). The lead relayed it to this session as the task; this SOP is the record. It does **not** settle the lawyer's items below.

**Two printed lines the lawyer still has to read:** (1) the plan card's "Cancel any time: the plan keeps working until the period you paid for ends" and (2) the FAQ "Can I cancel?" ("Yes, any time. Your plan keeps working until the end of the period you paid for, and then does not renew."). Cancel-any-time is Adebayo's decision (2026-11-16); the exact words are for the lawyer (spec 9, question 1).

**Open conflict, not changed here (lawyer text):** `web/components/legal/terms-content.tsx:30` still says "The Service is free during the pilot ... If we introduce paid plans, we will tell you the price before anything is charged". It contradicts a public page that sells plans (and the JSON-LD offers beside it). S11 pairs the /pricing numbers with the Terms change; the Terms need the lawyer's rewrite before or with the billing switch-on.

## Why

On 2026-11-17, as billing was being switched on, Adebayo found two things broken: `/pricing` still said no plan was on sale and every price was "To be announced" (with a Team Plus plan that does not exist), and a signed-in person could reach `/billing/plans` only by typing it. Both made the live billing look unfinished.

## What the page says now (every line has a source)

| Printed | Source |
|---|---|
| Trial: **3 hours**, valid **14 days**, no card, once | spec 3.8 (DECIDED 2026-11-15). 14 days is `trial_days` in `app/config.py`; **3 hours is the env value `TRIAL_SECONDS=10800` on the server**, not a seed (the default is 0): the switch-on must have it set or the page is wrong |
| One monthly plan named **"20 hours a month"**, **20 hours**, **$18** or **₦25,000** a month | seed of `alembic/versions/0052_subscriptions.py` (`plan_20h`, 1800 cents, 2,500,000 kobo; the name is the seed's own, the same name the billing screens show) |
| Pack **"10 extra hours"**: 10 hours, **$10** or **₦15,000**, valid **12 months**, only with a live plan | seed (`pack_10h`, 1000 cents, 1,500,000 kobo, `valid_months` 12); spec 4.3 |
| Nigeria pays naira through Paystack, everyone else US dollars through Flutterwave; "separate prices, not a conversion" | spec 5.2 and section 10 (the BILLING country decides; a public page cannot know the visitor's, so it says who pays in which) |
| Hours belong to the person; unused plan hours do not roll over | spec 1.4, 3.3 |
| Cancel any time; the plan keeps working until the period you paid for ends (the plan card line and the FAQ "Can I cancel?") | spec 3.4 (the cancel row); **printed on Adebayo's cancel-any-time decision of 2026-11-16, and still a LAWYER-TO-READ item** (spec 9 puts cancel and the period's end on the Terms list) |
| Hours run out: running recordings finish, new ones wait; Ask, translate, rewrite keep working on a plan | spec 3.3 |
| Teammates read shared notes without a plan; they need their own plan or trial for Ask Beyric, translating, rewriting and their own note taker | spec 2.2 |

**Left out on purpose (the lawyer has not read them):** the sentence that the unused part of a period is not refunded (printed in the first version of this PR, removed after the Opus review: spec 9 and lawyer question 2 are open; a test fails if the page prints "refund" anywhere), refund windows (7 and 14 days), the failed-payment grace, what happens when payments stop, any "we will tell you" promise, any VAT or invoice wording. A test fails if the page prints `within N days of`, "money-back", "guarantee", "grace", "lapse", or "we will tell". No "free", no "unlimited", no Business or Team Plus.

Design is the designer's: the same three cards, table, FAQ and closing band. Changes: copy and data; the plan card shows the dollar price big with the naira price under it; a one-line currency note under the cards; the comparison table has two columns (Trial, the plan) because a pack is an addition, not a plan; a phone-width rule so the two-column table fits without sideways scroll.

CTAs (the page is `async` and reads the session, as the public shell already does): a visitor gets **Get started** to `/signup`. A signed-in person gets **Choose a plan** to `/billing/plans` **only when billing answers for them** (the page asks `GET /api/billing/subscription` on the server; a 404 or any failure means no), else **Open the app** to `/`, so nobody lands on "Plans are not available for your account yet". JSON-LD: a monthly plan's offers carry a `UnitPriceSpecification` with `billingDuration` `P1M`; a pack's do not.

## One source of truth for the numbers

The page is public and unsigned, so it cannot read `GET /api/billing/plans` (that answer is for one signed-in person's billing country). Alternatives rejected: a new unauthenticated prices route (new API surface and a second place that can disagree) and building the page from the database at request time (the page is a static server component). Chosen: one typed copy in `web/components/pricing/content.ts` (`TRIAL`, `PLAN`, `PACK`), read by the cards, the table, the FAQ, the page description, `llms.txt` and the JSON-LD `offers`, and **`web/tests/pricing-real.test.tsx` parses the seed of migration 0052 and fails when any printed price, hour count, name or validity differs**, and when the page prints any number that is not one of the decided ones.

**One array.** What can be bought is the `CATALOG` array in `content.ts` (an entry per plan or pack: seed code, seed name, hours, whole-dollar and whole-naira prices, validity for a pack, tagline, list). The cards, the comparison table's columns (the trial plus every plan; a pack is a row, not a column), the hero and FAQ sentences, the page description, `llms.txt` and the JSON-LD `offers` (every entry in both currencies) are all built from it. **Adding a second or third plan is: its seed row by migration, one `CATALOG` entry, nothing else.** The test loops over every entry and compares name, hours, validity and both prices with the seed, and a second test checks the cards, table columns and offers grow with the array.

**When a price changes:** write a new `plan_prices` row (or a new product row) by migration and edit the entry in `content.ts`. The test reads every migration in order and takes the last seed row for a code, so a price-change row or a new plan's row is found without editing the test. It cannot see a price changed by hand in the database. (Mutation checked: changing `usdMajor` 18 to 19 fails the plan's test.)

## Billing entry points (signed-in)

| Entry point | Where | Leads to | Shows when |
|---|---|---|---|
| **Billing** link | Configure group of the sidebar and the phone drawer, after Settings; current on `/billing`, `/billing/plans`, `/billing/return` (they now pass `current="billing"`) | `/billing` | billing is on for the person |
| **Hours card** | sidebar, above the person's name: plan name or "Trial" / "Pilot hours" / "No plan", hours left with the bar the billing page draws, "Choose a plan" or "Add hours" | live plan (`active`, `past_due`): `/billing`; no plan, a trial, a pilot or a lapsed plan: `/billing/plans` | billing is on and the person has a limit on hours (an unlimited person gets the link only) |
| Hours notices | Notifications: `usage.hours_low`, `usage.hours_out` ("Add hours or open billing" or "Choose a plan"), `pilot.expiry` ("Open billing" / "Choose a plan") | same rule | billing is on |
| Not recorded reasons | the Not recorded list and the calendar's upcoming rows, only for the two hours sentences of `app/calendar_text.py` ("Your recording hours are used up.", "You have no recording hours at the moment."): "Add hours" or "Choose a plan" | same rule | billing is on |

Already there from S8b and unchanged: the Settings "Plan and billing" card, `GateText` on the Ask card, rewrite, translate, share and new-meeting refusals ("See plans", "Add hours or choose a plan"), and the grace and lapse banner.

**How "billing is on" is decided:** the same question the banner and Settings card ask, `GET /api/billing/subscription`; a 404 `not_found` means off or not on the allow-list, is remembered for the tab, and everything draws nothing. No new flag, no new request type. `web/components/billing/use-billing-state.ts` shares one request among the entry points of a page and reuses the answer for 10 seconds (and forgets it on a sign-in or sign-out in the tab), so the sidebar costs at most one request per page load (a not-on-billing person: one per tab).

**Not found:** the task described a "trial-ending notice" and an existing sidebar usage bar. Neither exists: the trial and pilot send no in-app notice about ending in this code (the pilot sends `pilot.expiry`, linked above), and the sidebar had no hours bar, so the card is new. The `GateText` links on the Ask gate and the out-of-hours refusals already existed (S8b) and were only checked.

## What's involved

| Path | What |
|---|---|
| `web/components/pricing/content.ts`, `pricing.tsx`, `web/app/(public)/pricing/page.tsx` | the page (data, layout, metadata, signed-in CTAs) |
| `web/app/llms.txt/route.ts`, `web/components/site/json-ld.tsx` | the same numbers in the crawler text and `offers` |
| `web/styles/public.css` | `.ld-plan-alt`, `.ld-plans-note`, `.ld-table-2` |
| `web/components/billing/sidebar-billing.tsx`, `use-billing-state.ts`, `hours-reason-link.tsx` | the Billing link, the hours card, the shared read, the Not recorded link |
| `web/components/app/shell.tsx`, `notifications-view.tsx`, `not-recorded.tsx`, `calendar-upcoming.tsx` | where they sit |
| `web/lib/billing.ts` | `hasLivePlan`, `billingEntryHref`, a tab epoch for the cached read |
| `web/styles/components.css` | `.side-hours*` |
| `web/tests/pricing-real.test.tsx`, `billing-entry.test.tsx` | the tests below |

## Verification

* **Tests (vitest):** `pricing-real` (15: the seed pin, looped over every `CATALOG` entry, for name, hours, validity and both prices; every printed number is a decided one; no Team Plus, "to be announced", free, unlimited; currency note; table honesty; FAQ leaves out lawyer items; CTAs for a visitor and a signed-in person; metadata, `llms.txt`, JSON-LD) and `billing-entry` (14: where each state leads, the card per state, nothing while billing is off, remembered for the tab, the shell's nav order, the notices, the Not recorded sentences pinned to `app/calendar_text.py`). `landing-security-copy` now asserts the JSON-LD offers are the four seeded prices (it said "no price" before), still no "free", still the legal entity. `landing-v2` lost its pricing block (moved into `pricing-real`), `public-shell` awaits the page. Lint and type-check clean; the 15 test files that cover a touched file pass; CI runs the rest.
* **Live, local, by me:** `scripts/dev_billing_stack.py` (real app, migrations 0001 to 0052, scripted provider) on a scratch Postgres plus `next dev`, driven in a headless browser at **1440 and 390 px**. Signed in as a trial person, a person with no plan, an active plan, a lapsed plan, 90% used and all used: every one clicked the **Billing** link (all lead to `/billing`) and the **hours card** (trial, no plan, lapsed: `/billing/plans`; active, 90%, out: `/billing`; the phone drawer too); the hours notices led to Billing or the plans screen by the same rule; the Ask gate for a person with no plan showed "See plans" and led to `/billing/plans`; `/pricing` as a visitor (three **Get started** to `/signup`) and as a signed-in person (three **Choose a plan** to `/billing/plans`). The printed numbers were compared with the database rows of the scratch stack (`plans`, `plan_prices`, `products`): 20 hours, $18, ₦25,000; 10 hours, $10, ₦15,000, 12 months. Screenshots (not in git): `/Users/adebayovictor/Library/Application Support/Claude-Work/side-session-notes/local_a3623d84-4f5c-43f6-8ae2-036674e9b898/billing-nav/`.
* **Not verified live:** the "Not recorded" link (a skipped hours join needs a scheduler run; covered by tests with the server's real sentence); a person on billing for whom the billing read fails with a non-404 (draws nothing, tested); the trial hours on the real server (`TRIAL_SECONDS`).

## Operate / roll back

Nothing to run. **The trial line is true only while the server has `TRIAL_SECONDS=10800`** (default 0 means no trial) **and `TRIAL_DAYS` is 14** (the default). The lead reports it is set on the server now. A blocking check for the switch-on runbook: `docker compose exec -T web python scripts/trial_report.py` shows trials in force, and the env file shows the value. The test reads `app/config.py` (default 0, "10800 is 3 hours") and `deploy/README.md` and compares them with the page, so it fails if the page's hours stop matching the documented value; it cannot see the server's env. Roll back by reverting the PR; no data is touched.

## Follow-ups

| Item | Note |
|---|---|
| The page still carries the landing's sentence "Sign-up is invite-only for now. If you have an invitation, create your account; if not, join the waiting list." (`SignupNote`, the designer's, shared with the landing page) next to a **Get started** button | untouched here; decide with the sign-up switch-on whether it still holds |
| Hero title "Pay when your team runs on it." is the designer's and sits oddly beside per-person hours | copy decision |
| The refund, failed-payment and stop-paying answers, and the two cancel lines above, wait for the lawyer (spec 3.4, 3.5, 9) | add or confirm the FAQ entries once read |
| The Terms still say "free during the pilot ... we will tell you the price" (`terms-content.tsx:30`) | lawyer rewrite, with the billing switch-on |
| The `GateText` links (Ask, share, rewrite) are drawn whether or not billing is on for the person; the new entry points are gated. Same sentences only appear under enforce, so it is harmless once billing is on for everyone | unify if billing stays on an allow-list |
| A real `trial ends soon` notice does not exist; the sidebar card shows the hours, not the trial's end date | would need the trial end in `GET /subscription` (it is in `usage.trial_ends_at` of the session) |
