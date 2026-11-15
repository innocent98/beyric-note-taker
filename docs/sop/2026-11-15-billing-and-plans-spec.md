# SOP: billing, plans and feature-gating spec (2026-11-15)

**What shipped.** One design document, `docs/superpowers/specs/2026-11-15-billing-and-plans-design.md`, and a new "Billing, plans and feature gates" module in `docs/checklist/master-checklist.md`. Docs only: no code, no migration, no endpoint. PR #206 from branch `docs/billing-and-plans-spec`.

**Why.** Adebayo asked on 2026-11-15 for the billing work to start with a spec. Today nobody can pay: every account has every feature and a lifetime cap of 5 recorded hours per person.

**How.** I read the handoff, the pricing content file, the multi-team design, the cost report script and the code that limits hours (`app/usage.py`, `app/placement.py`, the join runner, the team model), then read each vendor and competitor page myself (section 12 of the spec lists every URL and how it was read). Rules that Adebayo has not decided are marked PROPOSED throughout.

**What is in it.** Today's code and what a plan replaces (1); the plan model and where each gate lives in code (2); hours rules with the exact moments of warning, stop, grace, downgrade, lapse, cancel and refund (3); margin arithmetic with sources (4); the provider layer, webhooks, idempotency and test modes (5); VAT and invoices (6); data model and migrations 0048 to 0051 (7); ten build stages (8); what the Terms, the Privacy Policy and the lawyer need (9); the `/pricing` edit (10); the decided and the open questions (11); a section on transcription and names (4A); provenance and what could not be confirmed (12).

**Verification.** Every vendor figure was read from the vendor's page on 2026-11-15 (six Flutterwave pages with curl, five Paystack pages in the in-app browser, Skribby, Fathom, Fireflies, Otter, one Nigerian tax article). Code line references were checked against `origin/main` at `871ed74f`. The cost figures are relayed from the 2026-11-14 report in the handoff, not re-run. Nothing was tested live and no account was created at any vendor.

**Operate or roll back.** Nothing to run. Revert the PR to remove the document and the checklist module.

**Follow-ups.** Adebayo's answers to the five open questions; a new cost run after the one-transcript flip; the accountant's VAT answers and the lawyer's list; test keys for both providers in a local env file; dollar pricing and settlement proved in Flutterwave's test mode; then stage S1.

**Revision 2026-11-15 (after Adebayo answered section 11).** Decided and marked with the date: Q2, Q3, Q4 and Q6; the grace is 3 days with no new hours during it (it was 7); both providers are built, Paystack for naira and Flutterwave for dollars, routed by the billing country the owner chooses (changes, scheduled changes and card-country mismatches are specified in section 5.2). Still open: Q1 (the price hold does not stop the build), Q5 beyond the grace, Q8, Q9, Q10, each now with a plain-words line. Both provider accounts exist and are new, made for Beyric (his words, relayed); test keys are still to go in a local env file; their verification status is not known. Added: section 4A (transcription and names, checked against `app/notes.py`, `app/pipeline.py`, `app/transcript_source.py` and `app/skribby.py`), section 5.7 (accounts and test keys, local env only), the Paystack and Flutterwave card-country fields (read from their verify-response samples), and a build order with both adapters (stages S3 to S10, migrations 0048 to 0051).

**Second revision 2026-11-15.** The Paystack account question is closed: both accounts are new and made for Beyric. Question 9 now says in plain words how a team subscription works (one payer per team, one shared pool, a member limit, a solo person subscribes through a one-step "Just me" team, charging follows the recorder's hours team and not sharing).
