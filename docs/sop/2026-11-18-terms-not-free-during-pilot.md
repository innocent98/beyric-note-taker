# SOP: the Terms no longer say "free during the pilot"

**What shipped.** Two sentences removed from the "Pilot, fees and limits" section of the Terms, in both copies (`web/components/legal/terms-content.tsx` and `app/templates/terms.html`, kept identical; a web test compares them). Branch `fix/terms-not-free-during-pilot`; the PR number is in the PR list. No migration, no API change.

## Why

Billing is on for everyone and the pricing page sells plans, yet the Terms said "The Service is free during the pilot" and "If we introduce paid plans, we will tell you the price before anything is charged". Both now contradicted the product. Adebayo said on 2026-11-18 to remove the "free" wording.

## How

| Was | Now |
|---|---|
| "The Service is free during the pilot. Each pilot account has a limit on recorded hours ..." | "Each pilot account has a limit on recorded hours ..." (first sentence removed) |
| "If we introduce paid plans, we will tell you the price before anything is charged, and nothing will be charged without your agreement." | "Paid plans, their prices and extra hours are on the pricing page, and nothing will be charged without your agreement." |

Left alone on purpose: the limited number of accounts and waiting-list sentences (the account cap still exists), `TERMS_VERSION` and `RECORDING_ACK_VERSION` (no re-acceptance prompt; standing decision that policy changes carry no notice), and every other clause. This is a removal of two false statements, **not** the paid-plans section of the Terms. That section (auto-renewal, cancelling, grace period, refunds, expiry of hours, VAT, price changes) is still to be drafted with the lawyer: the list is in `docs/superpowers/specs/2026-11-15-billing-and-plans-design.md` section 9.

## What's involved

`web/components/legal/terms-content.tsx`, `app/templates/terms.html`, `tests/test_legal.py` (new test `test_the_terms_do_not_say_the_service_is_free_and_point_to_the_pricing_page`).

## Verification

`web/tests/legal.test.tsx` (7 tests, including the two-copies parity check) passes locally; `ruff check` and `ruff format --check` on the test file pass. `tests/test_legal.py` needs Postgres and is left to CI.

## Operate / roll back

Revert the PR. Nothing on the server changes.

## Follow-ups

- The lawyer's paid-plans section for the Terms and the payment-provider entries for the Privacy Policy (spec section 9) are still open.
- The Terms still call the service "in a pilot".
