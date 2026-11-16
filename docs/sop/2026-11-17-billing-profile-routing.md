# SOP: billing stage S5, the billing profile, the billing country and routing

**What shipped.** A person's billing profile (who is billed, and the **billing country** they choose), the rule that turns a country into a currency and a provider, the rules for changing the country, the card-country note on a settled payment, and four thin routes for the person's own profile. Two new tables (`billing_profiles`, `billing_country_changes`, migration **0051**). Everything is dark: the routes answer a plain 404 while `BILLING_ENABLED` is false (the default), no real provider exists yet, nothing is charged, and nothing outside `app/billing/` reads the new tables. PR: see the PR for branch `feature/billing-profile-routing`. Stage S5 of `docs/superpowers/specs/2026-11-15-billing-and-plans-design.md` (sections 5.2, 7, 8). FE guide: `docs/fe-integration-guide-billing-profile.md`, built from `docs/fe-captures/billing-profile/`.

## Why

The country decides what a person pays in and through whom (Nigeria: naira through Paystack; everywhere else: dollars through Flutterwave), so it has to be a stored, explicit choice made by the person: a VPN or a traveller would send an IP guess the wrong way. It also has to be hard to flip back and forth to pick the cheaper price list, so a change is limited and written to a history. The stages after this (the two adapters, S6 and S7, and the checkout in S8) read the profile and cannot work without it.

## How, and what was decided

| Piece | What it does |
|---|---|
| `routing.provider_for(country, routes_text)` | returns a `Route(currency, provider)`. Missing country: `CountryMissing`; not an assigned ISO code (a name, `UK`, `XK`, `ZZ`): `CountryInvalid`; a real country with no route: `CountryUnsupported`. Never a default, never a guess, no IP anywhere |
| `BILLING_COUNTRY_ROUTES` | the table as data: `COUNTRY=CURRENCY:provider,...` with `*` for every other country; default `NG=NGN:paystack,*=USD:flutterwave`. A named country beats `*`. **To switch a provider off, remove its entry**: a country left with no route is refused (`billing_country_unsupported`), never sent to the other provider. A bad value stops web, worker and scheduler at startup naming the setting (same pattern as `HOURS_MODE`); a blank line is the default |
| `countries.ISO_COUNTRIES` | the 249 officially assigned ISO 3166-1 codes (a test counts them). `UK` and `XK` are deliberately not in it |
| `profile.change_country` | the rules below; locks the profile row (`FOR UPDATE`, made first if missing) so two changes at the same moment cannot both pass the 30-day rule |
| `profile.live_subscription_period_end` | **the one function S8 fills in**: when the paid period of the person's live subscription ends, or `None`. Returns `None` for everyone today. Tests replace it with a stub for the live branches |
| `mismatch.flag_country_mismatch` | sets `card_country` and `country_mismatch` on a settled payment; see below |
| `profile_api.py` | `GET/PUT /api/billing/profile`, `PUT /api/billing/profile/country`, `GET /api/billing/route?country=XX` |

**The change rules** (`profile.change_country`; one parametrized test row each, `tests/test_billing_profile.py::test_every_row_of_the_change_table`):

| Situation | Result |
|---|---|
| the same country as now | `unchanged`, nothing written |
| a change is already waiting for the end of a period | refused, 409 `country_change_pending` (even a request to go back to the current country) |
| the first choice | `applied` at once; **not counted** as a change for the 30-day rule, so one wrong first pick can be fixed straight away |
| another real change in the last 30 days | refused, 409 `country_change_too_soon` |
| no live subscription | `applied` at once |
| a live subscription, same route (same currency and provider) | `applied` at once |
| a live subscription, a different route (crossing Nigeria and elsewhere) | `scheduled`: the profile keeps the old country, `pending_billing_country` and `pending_effective_at` (the end of the paid period) hold the new one. A period end already in the past counts as no live subscription |

Every applied or scheduled change writes one `billing_country_changes` row (`old_country` null for the first choice; `created_at` is when the person asked and is what the 30-day rule counts from; `effective_at` is when it applies). A scheduled change takes effect through `apply_due_pending`, called when the profile is read or written, and by S8's renewal; it writes no second row. Only the signed-in person's own profile is ever read or written: no route takes a user id.

**The card-country note** (`mismatch.py`). `settle._mark_paid` calls `flag_country_mismatch` after the provider has confirmed the payment and before the grant handlers. It writes `card_country` (the trailing two letters of what the provider reported: Paystack's `NG`, Flutterwave's `NIGERIA NG`) and `country_mismatch` (true only when both countries exist and differ) compared with the person's **current** billing country (a merely scheduled change does not count: the payment was taken under the old route). **It cannot refuse, reverse or delay a payment:** it runs in a savepoint, swallows and logs any failure, and the grants run after it either way. A test breaks it on purpose and shows the payment is still paid and granted.

Decisions and where I departed from the spec or the brief:

* **`pending_billing_country` sits on `billing_profiles`, with `pending_effective_at`** (the spec puts it on `subscriptions`, a table S8 builds). S8 reads it from the profile at renewal; if S8 prefers it on the subscription it can move it.
* **Migration numbers move up by one.** 0050 shipped with S4, so S5 is `0051_billing_profile.py` and the spec's later numbers are now **S8 = 0052, S9 = 0053**. The spec (sections 7 and 8) and the checklist are updated.
* **Nothing else is touched in S4's code except one call and one behaviour in `settle._mark_paid`:** the mismatch call, and the card country is now read from its trailing two letters (`NIGERIA NG` gives `NG`) instead of being dropped to nothing when it is not two letters. The spec says the build must read the trailing letters; the S4 test that expected `None` for `NIGERIA NG` now expects `NG`. Anything that is not a trailing two-letter code (`NGA`, `NIGERIA`, empty) is still stored as nothing.
* **A request to change to the current country while a change is waiting is refused** (`country_change_pending`), not treated as a cancel: cancelling a waiting change needs its own rule (below).
* **The allow-list is cheap, so it is in:** `BILLING_ALLOWED_EMAILS` (comma-separated, case-insensitive). With billing on and the list empty (default) every account may use the routes; with a list, anyone else gets the same 404 as billing off. It is a rollout switch for the pay screens' pilot, not an access-control boundary.
* **A fourth route, `GET /api/billing/route?country=XX`**, is not in the brief but is what spec 5.2 needs ("the screen states the result before any payment"): it reads the table and stores nothing.
* **`PUT /api/billing/profile` replaces all four details and never touches the country**; a `billing_country` key is a 422 (unknown field). The country has one door, the one with the rules.
* **Deleting the account deletes both tables' rows** (cascade): the profile is personal data. Payments keep their rows (`payer_user_id` set null, from S4). The downgrade of 0051 refuses while any row exists, like 0050.
* **Rejected:** a `country` dropdown list served by the API (the FE ships its own list of codes; one more thing to keep true); storing bad-country attempts; an IP-based default of any kind.

## What's involved

| File | Change |
|---|---|
| `alembic/versions/0051_billing_profile.py` | the two tables, check constraints (two-letter upper-case codes; pending country and date both set or both null), index on (`user_id`, `created_at`) |
| `app/models.py` | `BillingProfile`, `BillingCountryChange` |
| `app/billing/routing.py`, `countries.py`, `profile.py`, `mismatch.py`, `profile_api.py` | new |
| `app/billing/settle.py` | `_mark_paid` calls `flag_country_mismatch` and reads the card country with `normalize_card_country` (the old `_iso2` is gone) |
| `app/config.py`, `.env.example`, `deploy/README.md` | `BILLING_COUNTRY_ROUTES` (validated at startup), `BILLING_ALLOWED_EMAILS` |
| `app/main.py` | registers the profile router after the webhook router |
| `tests/test_billing_routing.py`, `test_billing_profile.py`, `test_billing_mismatch.py`, `test_billing_profile_migration.py` | new |
| `tests/test_billing_inbox.py` | one expectation changed (`NIGERIA NG` now gives `NG`) |
| `pyproject.toml` | `tests/test_billing_mismatch.py` added to the F811 ignores (it reuses S4's fixtures by import, like the other billing and hours tests) |
| `scripts/capture_billing_profile.py`, `docs/fe-captures/billing-profile/` | the live capture run behind the FE guide |
| `docs/fe-integration-guide-billing-profile.md`, `docs/checklist/master-checklist.md`, `docs/superpowers/specs/2026-11-15-billing-and-plans-design.md` | the guide; S5 lines; the renumbering |

**No change was needed in the route-inventory tests** (`tests/test_accounts.py`, `tests/test_api.py`, `tests/test_google_signin.py`): the new routes have no path parameter, so the id classification has nothing to classify, and the inventory tests enumerate them from the OpenAPI schema on their own (with billing off they answer 401 to a visitor, 403 to a write without CSRF, and 404 to a read, which those tests already accept). They were run and pass; `tests/test_billing_profile.py` adds the two checks that matter here (`test_no_billing_profile_route_takes_a_persons_id`, and the per-route login, CSRF and flag-off behaviour).

## Verification

* **Run locally** (lint, format and the named tests only; CI runs the rest, and has no separate type-check step): `ruff check .` and `ruff format --check .` clean (ruff 0.17.3, the pin); `tests/test_billing_routing.py`, `test_billing_profile.py`, `test_billing_mismatch.py`, `test_billing_profile_migration.py` (154 tests), `tests/test_billing_inbox.py`, `test_billing_payments.py`, `test_billing_migration.py`, `tests/test_hours_grants.py`, `tests/test_models.py`, `tests/test_edge.py`, the four route-inventory tests (`test_api.py` x3 with the file-routes one, `test_google_signin.py` public routes, `test_accounts.py` id classification x2) and `tests/test_migrations.py::test_single_head` and `::test_migration_matches_models` pass, on a scratch Postgres 17 container that was removed afterwards. The full suite was not run here; CI is the authority.
* **What the tests prove:** every row of the routing table (Nigeria; US, GB, GH, KE, ZA, CI, FR, JP), case and spaces read, and every missing or not-a-country value refused with the right code; one provider switched off leaves its countries refused, not rerouted; a bad `BILLING_COUNTRY_ROUTES` stops startup naming the setting; every row of the change table above; two changes inside 30 days refused and the boundary exact (one second short refused, exactly 30 days allowed); the first choice is free once; a waiting change refuses every further request, inside or past 30 days; a waiting change applies at its time and writes no second history row; a refused request writes nothing; a profile and its history go with the account; the note is set for a foreign card, a Nigerian card for a person billed abroad, `NIGERIA NG`, no profile, no card; **a broken note function leaves the payment paid and granted**; a refused payment gets no note; with billing off every route is a 404 and writes nothing (even with a bad body); a visitor gets 401 and a write without CSRF gets 403 before billing is asked; the allow-list (case-insensitive, others get 404); two people never see or change each other's profile; the migration round trip, its constraints in the database, the downgrade guard and the cascade.
* **Live captures** (`docs/fe-captures/billing-profile/`, 36 exchanges against the real app under uvicorn with real migrations) back every body and error string in the FE guide, except the cases it marks tests-only.
* **Not verified:** a real **scheduled** country change (it needs a live subscription, stage S8; the capture seeds the waiting row by SQL and says so); any real provider, Paystack or Flutterwave (nothing here calls a vendor); what a real foreign-issued card returns in either provider's card-country field (spec 5.2 says the samples are sandbox values); the screens (none built); anything on the server (nothing was deployed or run there).

## Operate / roll back

1. Deploy: migration 0051 runs with it. With `BILLING_ENABLED` unset (false) nothing changes for anyone: the routes are a 404, the tables stay empty.
2. To open the profile routes to one account while billing stays otherwise dark: set `BILLING_ENABLED=true` and `BILLING_ALLOWED_EMAILS=<that account's email>` in the server `.env`, then recreate web, worker and scheduler. Even then no webhook is accepted (no provider is registered) and nothing is charged.
3. To switch a provider off later (S6/S7 onward): edit `BILLING_COUNTRY_ROUTES`, for example `NG=NGN:paystack` to stop taking payers from outside Nigeria; recreate the three containers. A person already on a removed route sees `route: null` on their profile until they change country.
4. A person who chose the wrong country and has used their one change has **no self-service fix**, and there is no admin route yet: the operator changes `billing_profiles.billing_country` by SQL and should add a `billing_country_changes` row with it. (Follow-up below.)
5. Roll back the code: revert the PR; the tables are unused by older code. To drop them, `alembic downgrade 0050` (refuses while any profile or change row exists; delete them by hand first if you must).

## Follow-ups

| Item | Where |
|---|---|
| **Fill in `profile.live_subscription_period_end`** (the live subscription's period end) and call `profile.apply_due_pending` at renewal; the first checkout after a crossing change uses the new route | S8 |
| The "two mismatched payments in a row" count and the mail asking the person to check the country (spec 5.2, point 3) | later, with S8's mail |
| The plain note shown once to the person ("Your card was issued in X; your billing country is Y"): needs a read route for the latest payment's note | S8 or S9 |
| An admin list of mismatched accounts, and an admin way to correct a person's country (with a history row) | S9's admin work |
| Cancelling a waiting (scheduled) change; today the person waits for the period end | decision, S8 |
| A billing profile must exist (country set) before the first payment: S8's checkout must call `routing.provider_for` and refuse with the codes here | S8 |
| Whether Flutterwave accepts a payer from every country is unconfirmed (spec 5.2); countries it refuses need a route entry or a check at checkout. Sanctioned or unsupported countries: ask the lawyer | S7, lawyer |
| The Terms must say the billing country is where the customer is established (spec 5.2, point 4); the profile screen must say so | S11, lawyer |
| The profile's `tax_id` and `address` feed S9's invoice buyer snapshot; VAT wording waits for the accountant | S9 |
| `BILLING_ALLOWED_EMAILS` is an email list in the environment; a per-account flag in the database may be better once the pilot has more than a few accounts | S8 |
