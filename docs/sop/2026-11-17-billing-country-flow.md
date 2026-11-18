# SOP: billing country flow (suggest, confirm, free until the first payment)

**What shipped.** The billing country is now suggested instead of asked cold, can be changed freely and instantly until the person has ever paid, and keeps the 30-day and end-of-period rules from the first payment on. Every screen says what the country means and, when a change is refused or scheduled, the date and why. Branch `feature/billing-country-flow` (PR #233). Spec: `docs/superpowers/specs/2026-11-15-billing-and-plans-design.md` section 5.2. No migration, no new setting.

## Why

2026-11-17: the first live user was forced to pick a country from a long list, chose Ethiopia by mistake and could not change it back, because the rule (`app/billing/profile.py`) was one change per 30 days (`country_change_too_soon`, 409) for everyone, including a person who had not paid anything. The country still must not be guessed silently (a VPN or a traveller would be routed wrongly), so the decision was: suggest, ask for confirmation, and lock only once money is involved.

## How, and what was decided

| Decision | Detail |
|---|---|
| Suggest, never auto-save | `GET /api/billing/profile` carries `suggested_country` and `suggested_route` while no country is saved, from the saved time zone (`app/billing/zone_countries.py`: IANA `zone.tab` plus a few legacy names). Zones with no country (`UTC`, `Etc/*`) or a country with no route suggest nothing. The browser's language region (`en-NG` gives `NG`, bare `en` gives nothing) is the fallback, read in the browser and checked with `GET /route`. With neither, the picker shows. A read saves nothing |
| Confirmation | The "Billing country" card shows the suggestion as one line ("Nigeria. Prices in naira, paid through Paystack. Not Nigeria? Change") with **Use Nigeria**. On `/billing/plans` the plans are priced for the suggestion as a preview (`GET /plans?country=XX`, ignored for a person with a live plan) and **Choose this plan** saves the country first, then starts the checkout: continuing to checkout is the confirmation. If the save is refused no checkout starts |
| Picker | Nigeria first, a "Common" group (NG US GB GH KE ZA CA IN AE DE FR AU), then all countries, with a search box that filters the native select (works on a phone) |
| Free before the first payment | `profiles.country_lock(db, user_id, now)` is None while the person has no payment in status `succeeded`, `refunded` or `partly_refunded` and no live subscription. Then `change_country` applies at once, any number of times, and still writes the `billing_country_changes` row |
| The rule after | Unchanged: one change in 30 days, a route-crossing change with a live plan is `scheduled` for the period end, a second one while it waits is `country_change_pending`. **Only changes since the first payment count towards the 30 days**, so changes made while it was free never lock anyone out, and the first change after paying is allowed (same as before for "first choice, one fix"). A live subscription with no payment on record counts every change |
| Stays locked | Once paid, the lock stays even after the plan ends ("ever had a payment") |
| Open checkouts (review S1) | Any change **from a saved country** that takes effect closes the person's unpaid **plan** checkouts (a first choice has none: a checkout exists only under a saved country) (`profiles.supersede_open_plan_checkouts`: status `pending` becomes `failed`, nothing was bought). Otherwise a person could change country while a naira checkout was open, pay it, and hold a naira plan under a dollar profile (a dollar checkout with no warning at renewal), or have checkouts open in both currencies. If a closed page is paid anyway, `_plan_paid` sees a country change after the payment was created, makes no plan and tells the admins to refund it (`admin.billing_refund_needed`). Chosen over refusing the change: refusing would lock out a person who backs out of the provider's page after a mistaken pick (the original complaint) for as long as the checkout counts as open, and there is no route to cancel a checkout. Pack and upgrade checkouts are not touched: they are paid on the route the subscription was sold on, whatever the country says |
| Words | 409 messages now carry the date (`You can change it again on 17 December 2026.`, `... waiting for the end of your paid period (17 December 2026) ...`) **in the person's saved time zone (UTC when none)**, the same zone the card shows it in (`LocalTime`), so there is no day's difference west of UTC. A scheduled change says why (the plan was bought on that route; a saved card cannot move) |

Rejected: counting the free changes towards the 30 days (it would lock a person out for a month right after they fixed a mistake and paid); guessing from the IP (spec 5.2); auto-saving the suggestion on page load; a free-text country box.

**One product note for the lead:** a Nigerian whose browser reports `en-US` and whose account has no saved time zone is shown "United States, US dollars through Flutterwave" and might press Use. The picker is one click away and Nigeria heads it, the card says the currency in words and "Choosing a plan saves X as your billing country" appears under the prices, but this is the place a wrong guess can still slip through. A safer variant (suggest only from the time zone) is a one-line change in `billing-country.tsx` if you want it.

## What's involved

| Path | What |
|---|---|
| `app/billing/profile.py` | `country_lock`, `suggest_country`, `CountryLock`, the lock in `country_change_allowed_at` and `change_country`, `supersede_open_plan_checkouts`, dates in the saved zone |
| `app/billing/subscriptions.py` | `_plan_paid` refuses to make a plan for a checkout that predates a country change |
| `app/billing/zone_countries.py` | new: zone to country table, `country_for_zone` |
| `app/billing/profile_api.py` | `ProfileOut` gains `country_locked`, `suggested_country`, `suggested_route` (twelve keys) |
| `app/billing/subscriptions_api.py` | `GET /plans?country=XX` preview |
| `web/components/billing/billing-country.tsx`, `plans-view.tsx` | the card (suggestion, picker, search, lock copy) and the confirm-by-checkout path |
| `web/lib/countries.ts`, `billing.ts`, `api/client.ts`, `web/styles/pages.css` | common countries, region from browser languages, search filter; new profile keys; `plans(country?)`; `.bill-suggest` |
| `tests/test_billing_country_flow.py` (new), `tests/test_billing_profile.py`, `web/tests/billing-plans-view.test.tsx`, `billing-lib.test.ts`, `billing-fixtures.ts` | tests; the old 30-day tests now pay first |
| `scripts/capture_billing_country.py` (new), `capture_billing_profile.py`, `dev_billing_stack.py` | live captures; two dev people with time zones (`lagos@`, `addis@`) |
| `docs/fe-integration-guide-billing.md` (new section), `fe-integration-guide-billing-profile.md`, `docs/fe-captures/billing-country/` (new), `billing-profile/` (re-captured) | the contract |

## Verification

* Python: `tests/test_billing_country_flow.py` and `tests/test_billing_profile.py` (the files touched) pass on real Postgres; ruff check and format clean. Vitest: the billing test files pass; eslint and `tsc --noEmit` clean. CI runs the rest.
* Live over HTTP with the scripted provider (`docs/fe-captures/billing-country/`): Lagos zone suggests `NG`, Addis suggests `ET`, no zone suggests nothing; three changes before paying all applied; after a real checkout and webhook one change applied and the next 409 with the date; a route-crossing change scheduled and a second refused as pending.
* In a browser against the local app (`scripts/dev_billing_stack.py`): suggestion for Lagos and Addis, browser-language suggestion (`en-GH`), the picker with Nigeria first and the search, three changes in a row, a scripted payment, the disabled change with its date, and the scheduled change with its reason, at 1440 and 390 px. Screenshots are kept outside git by the lead.
* Not verified: a real Paystack or Flutterwave (the provider is scripted).

## Operate / roll back

Nothing to switch on: it rides on the existing billing flags. Roll back by reverting the PR; no data changes shape (the history table is the same, and the extra free changes it holds are ordinary rows).

## Follow-ups

* **`GET /plans?country=` preview** (review S5): it prices any valid country for a person with no live plan and saves nothing. It exposes only the public price list and the route table, so it is harmless, but it is a second way to read prices and is not in `docs/openapi.json`. Keep it in mind when the pricing page changes.
* **Card-country mismatch compares against the current country** (review S4, `app/billing/mismatch.py:71`): it compares the card's country with the billing country at the moment the payment settles, not the one the checkout was opened under. With a closed checkout paid late it now refunds without granting, but a mismatch note for a payment made across a country change can still name the newer country. Record the country on the payment if the note ever matters.
* A closed checkout page paid anyway needs a refund by hand (rare; the admins are told). If it turns out to be common, settle it on the route it was sold on instead and align the profile.
* The time-zone table is a static copy of `zone.tab`; refresh it if IANA adds a zone that browsers report.
* Whether the browser-language fallback should be kept (see the product note).
* The billing routes are still not in `docs/openapi.json`.
