# SOP: billing, plans and feature-gating spec (2026-11-15)

**What shipped.** One design document, `docs/superpowers/specs/2026-11-15-billing-and-plans-design.md`, and a new "Billing, plans and feature gates" module in `docs/checklist/master-checklist.md`. Docs only: no code, no migration, no endpoint. PR from branch `docs/billing-and-plans-spec` (number on the PR page).

**Why.** Adebayo asked on 2026-11-15 for the billing work to start with a spec. Today nobody can pay: every account has every feature and a lifetime cap of 5 recorded hours per person.

**How.** I read the handoff, the pricing content file, the multi-team design, the cost report script and the code that limits hours (`app/usage.py`, `app/placement.py`, the join runner, the team model), then read each vendor and competitor page myself (section 12 of the spec lists every URL and how it was read). Rules that Adebayo has not decided are marked PROPOSED throughout.

**What is in it.** Today's code and what a plan replaces (1); the plan model and where each gate lives in code (2); hours rules with the exact moments of warning, stop, grace, downgrade, lapse, cancel and refund (3); margin arithmetic with sources (4); the provider layer, webhooks, idempotency and test modes (5); VAT and invoices (6); data model and migrations 0048 to 0050 (7); eight build stages (8); what the Terms, the Privacy Policy and the lawyer need (9); the `/pricing` edit (10); ten yes or no questions (11); provenance and what could not be confirmed (12).

**Verification.** Every vendor figure was read from the vendor's page on 2026-11-15 (six Flutterwave pages with curl, five Paystack pages in the in-app browser, Skribby, Fathom, Fireflies, Otter, one Nigerian tax article). Code line references were checked against `origin/main` at `871ed74f`. The cost figures are relayed from the 2026-11-14 report in the handoff, not re-run. Nothing was tested live and no account was created at any vendor.

**Operate or roll back.** Nothing to run. Revert the PR to remove the document and the checklist module.

**Follow-ups.** Adebayo's ten answers; a new cost run after the one-transcript flip; the accountant's VAT answers and the lawyer's list; test keys at Flutterwave; USD pricing and settlement proved in test mode; then stage S1.
