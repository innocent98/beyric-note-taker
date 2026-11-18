# SOP: two more plans, Pro and Scale

**What shipped.** Two monthly plans next to the 20-hour one, decided by Adebayo on 2026-11-17: **Pro** (50 hours a month, USD 40.00 or NGN 56,000) and **Scale** (100 hours a month, USD 80.00 or NGN 112,000). Migration **0054** seeds the plans and four prices; the pricing page lists them from its catalog array; the billing screens show and sell all three and change between any two. PR from `feature/more-plans`.

## Merge order and the revision number (read before merging)

PR #234 (S9) owns revision **0053** (down revision 0052). This PR is numbered **0054** and its `down_revision` is still `"0052"` so that CI sees one head while #234 is unmerged. **Merge #234 first, then change `down_revision` in `alembic/versions/0054_more_plans.py` to `"0053"`** (one line; `tests/test_more_plans_migration.py` accepts either), push, and merge. Two heads fail the single-head check in `tests/test_migrations.py`.

## Why

The decided catalog grew from one plan to three. Plans and prices are data (spec 2.1 and 3.4), so nothing in the billing code names a plan: the plans route, checkout, the change-plan preview (proration) and the renewal already read the rows.

## How

| Piece | Decision |
|---|---|
| Migration `0054_more_plans.py` | rows only: `plan_50h` "Pro" (sort order 2) and `plan_100h` "Scale" (3), not public (`is_public` false: the page prints them from content, a test pins the numbers); four `plan_prices` rows (`month`): USD 4000 and NGN 5,600,000 kobo; USD 8000 and NGN 11,200,000 kobo. The first plan and its prices are untouched. A price change later is a new row, never an edit (spec 3.4). Written row by row in the shape of 0052 so the pricing test reads it. Downgrade deletes the four prices and two plans and **refuses** while a subscription, a pending downgrade or a payment points at either (money records), tested for each case |
| Names in one place | Pro and Scale are the lead's proposal. On the page they are `PRO_NAME` and `SCALE_NAME` at the top of `web/components/pricing/content.ts`; a test fails if they differ from the seeded names. A rename after the migration has run is one new migration that updates `plans.name` plus that one-line change. The billing screens read `name` from the API |
| Pricing page | two entries in `CATALOG` (`content.ts`), nothing else: the cards (now trial, three plans, pack, in a 3 + 2 grid), the comparison table (4 columns), the hero, FAQ, description, `llms.txt` and the JSON-LD offers (each monthly plan with `billingDuration` `P1M`) all follow. The seed test loops over every entry and reads all migrations (last row wins), so it covered the two plans the moment they were added |
| Billing screens | no change needed to the plan list, the checkout or the change-plan flow: all generic. **One wording fix found while proving it:** after an upgrade the Billing page said "That is not the $18.00 you paid last time: the price changed" (it compared with the first plan payment, but the plan had changed on purpose). The comparison now ignores a plan whose latest plan payment is an upgrade; tested |
| Dev stack | `scripts/dev_billing_stack.py` no longer inserts a dev-only 40-hour plan: it uses the real Pro and Scale rows, with new scenarios `pro`, `scale` and `ngpro`, and `downgrade` is now Pro with a downgrade to the 20-hour plan waiting. `scripts/capture_billing_subscriptions.py` (S8a captures, history) still inserts its own `capture_40h` and was not rerun |
| Captures | `scripts/capture_billing_plans.py` drives the real routes over HTTP with the three real plans: `docs/fe-captures/billing-plans/` (40 files) |

## What's involved

`alembic/versions/0054_more_plans.py`, `tests/test_more_plans_migration.py`, `web/components/pricing/content.ts`, `web/components/billing/billing-view.tsx`, `web/tests/pricing-real.test.tsx`, `landing-security-copy.test.tsx`, `billing-view.test.tsx`, `scripts/dev_billing_stack.py`, `scripts/capture_billing_plans.py`, `docs/fe-captures/billing-plans/`, the FE guide section "More plans".

## Margin note (arithmetic from spec section 4: cost 0.56 to 0.66 USD an hour live, 0.71 stress, 5.16% Flutterwave fee on an international card, net = price x 0.9484)

| Plan | USD per hour | Net per hour | Margin per hour at 0.56 / 0.66 / stress 0.71 | Whole allowance used (price, net, margin at 0.56 to 0.66, stress) | Break-even hours at 0.66 and stress |
|---|---|---|---|---|---|
| 20 hours, 18 USD (decided earlier) | 0.90 | 0.853 | +0.29 / +0.19 / +0.14 | 18, 17.07, +3.9 to +5.9 (22 to 33%), +2.9 (16%) | 25.9 and 24.0 |
| **Pro**, 50 hours, 40 USD | **0.80** | 0.759 | +0.20 / +0.10 / +0.05 | 40, 37.94, **+9.9 to +4.9 (12 to 25%)**, **+2.4 (6%)** | 57.5 and 53.4 |
| **Scale**, 100 hours, 80 USD | **0.80** | 0.759 | +0.20 / +0.10 / +0.05 | 80, 75.87, **+19.9 to +9.9 (12 to 25%)**, **+4.9 (6%)** | 115 and 107 |

Pro and Scale are both **0.80 USD an hour**, 11% below the 20-hour plan's 0.90. With every hour used they still make money in the live range and in the stress case, but the stress margin is thin (about 6%), and the break-even (53 and 107 hours) is only a little above the allowances. At the pre-flip cost of 0.74 to 0.91 an hour these two would be near zero or lose money (net 0.759 an hour), so they depend on the flip's lower cost holding (spec 4.2). Naira at about 1,500 NGN to the USD (the rate in 0052's seed comment): Pro NGN 56,000 is about 0.75 USD an hour and Scale about 0.75 too, below the 0.80 of the dollar price; a foreign card through Paystack costs 3.9% + NGN 100 and a local one 1.5% + NGN 100, so the stress case in naira is about break-even. Not measured: real usage per person (one account measured); most people will not use every hour. The naira rate and these prices are Adebayo's to review as the rate moves.

## Verification

* **Migration, on a scratch Postgres:** `tests/test_more_plans_migration.py` (5): one head, follows 0052 or 0053; the three plans and six prices exactly as decided (names, seconds, sort order, kobo and cents, month interval, not public, active); the pack and the first plan unchanged; downgrade to 0052 removes only the two plans and four prices and upgrade to head restores them; the downgrade refuses with a subscription, a pending downgrade, or a payment on Pro or Scale. With the existing `test_billing_subscriptions_migration.py` and `test_migrations.py`: 87 passed. `ruff check` and `ruff format --check` clean (the pinned version).
* **Pricing page:** `web/tests/pricing-real.test.tsx` (19) and the JSON-LD test: every catalog entry against the migrations, 8 offers (monthly ones with P1M). Lint, type-check and the touched test files pass.
* **Live, local, by me:** the real app with the scripted provider and `next dev`. Over HTTP (`scripts/capture_billing_plans.py`): the catalog in dollars and naira, a checkout for each plan, Starter to Pro, Starter to Scale, Pro to Scale upgrades with their proration (for example Starter to Pro: 2199 cents today for 29.9 extra hours a second after the period began, against 2200 and 30 for the whole month), a naira Pro checkout (5,600,000 kobo), Scale to the 20-hour plan scheduled and taken back, Scale to Pro scheduled, and a scheduler pass at the period's end that charged Pro's 4000 and applied the downgrade (plan Pro, 180000 seconds). In the browser: `/billing/plans` showed all three with their prices (and in naira ₦25,000, ₦56,000, ₦112,000); **Choose this plan** made the right payment for Scale (USD 8000) and for Pro in naira (NGN 5,600,000); Billing as a person on the 20-hour plan: Starter to Pro preview ($21.99, 29 h 54 min) and confirm through the provider page, then Pro to Scale ($39.99), then Scale to the 20-hour plan scheduled with **Keep Scale** offered. Database rows afterwards: 1800, upgrade 2199, upgrade 3999. Screenshots (not in git): `/Users/adebayovictor/Library/Application Support/Claude-Work/side-session-notes/local_a3623d84-4f5c-43f6-8ae2-036674e9b898/billing-nav/` (`more-plans-pricing-1440.png`, `-390.png`, `more-plans-billing-plans-1440.png`, `-390.png`, and `journey-*` for the change-plan steps).
* **Not verified:** a real provider's page for these amounts; a naira upgrade or downgrade in the browser; the S8a captures were not regenerated; the "Before you pay" list on `/billing/plans` still says cancelling "does not refund the unused part ... payments are refunded only under our refund rules" (S8b wording, reviewed then), while the public pricing page prints no refund wording at all: the lawyer reads both.

## Operate / roll back

Apply after #234 (see the top), then `alembic upgrade head`; the plans appear at once in `GET /api/billing/plans` for everyone billing answers for, and the pricing page prints them from the web build. Roll back: revert the web part, then `alembic downgrade 0053` (or `0052`): it refuses while anyone is on Pro or Scale.

## Follow-ups

| Item | Note |
|---|---|
| The first plan is still named "20 hours a month" (kept as seeded) next to "Pro" and "Scale"; a name like "Starter" would read better | one new migration plus the content line |
| `down_revision` flips to 0053 when #234 merges | see the top |
| Naira margin on foreign cards is thin at the stress cost | Adebayo's rate and price review |
| Teal "main" card stays the first plan; no "most popular" badge (not decided) | design |
