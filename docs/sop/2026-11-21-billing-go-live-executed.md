# SOP: billing went live (2026-11-21), what was run and what it proved

**What shipped.** Billing was switched from the providers' test keys to LIVE keys on the production server, 2026-11-21 about 16:03 WAT, following `docs/launch/billing-go-live-runbook.md` (PR #254) with the closeout of PR #255 (`docs/sop/2026-11-21-go-live-closeout.md`). Nothing else changed in code. No real payment has been made yet.

## Why

Billing, plans, trial, invoices and refunds were built and enabled in test mode (2026-11-17 and 18). Production release needs live keys. The code audit (`docs/launch/billing-go-live-code-audit.md`) showed rows made with test keys would misbehave after the flip, so a one-time closeout and a cutover time were built first.

## How (the run, in order, with the proof of each step from his pastes)

| Step | What | Proof seen |
|---|---|---|
| Gate | `deploy/gate.sh` `join_due_soon`, meetings in progress, open refunds | `no join is due`, `0`, `0` |
| Pause | `BILLING_ENABLED=false` in `.env` (copy `.env.before-live-<time>` first), recreate web, worker, scheduler (`--no-deps`) | `BILLING_ENABLED in web: false`; both webhook routes 404 |
| Backup | `deploy/backup.sh` | `notetaker-20261121T144306Z.sql.gz`, 461,765 bytes (the undo) |
| Look | read-only counts of subscriptions, grants, cards, invoice counters, invoices | every table EMPTY: no test billing data existed on the server |
| Cutover time | taken on the server after the pause: `T = 2026-11-21T14:45:33Z` | printed |
| Closeout, dry run | `scripts/billing_close_test_data.py --before T --end-pilot --trial-for-ended-pilot` | 4 pilot grants to end, 4 trials to give, zeros elsewhere |
| Closeout, apply | same with `--apply` | `PROOF: OK`, exit 0; database check: `pilot` 4 grants, 0 in force; `trial` 4 grants, 4 in force |
| Live settings, one edit by him | `BILLING_ENABLED=true`, `BILLING_LIVE=true`, `BILLING_LIVE_SINCE=T`, Paystack and Flutterwave live keys, `FLUTTERWAVE_SECRET_HASH` new, `BILLING_ALLOWED_EMAILS=*` unchanged | prefix proof: `sk_live_`, `pk_live_`, `FLWSECK-`, `FLWPUBK-`; hash length 64 |
| Correction before the recreate | `PAYSTACK_WEBHOOK_SECRET` held a 48-character value (the length of the secret key): deleted the line, so the adapter uses the classic `x-paystack-signature` check with the secret key (`app/billing/paystack.py` `verify_webhook`) | `webhook-secret lines left: 0` |
| Recreate | gate check again, one `docker compose up -d --force-recreate --no-deps web worker scheduler` | web, worker, scheduler Up; `BILLING_LIVE: true`, `SINCE` = T |
| Gap check | payments, subscriptions, cards created at or after T | `0 | 0 | 0` |
| Webhook routes | unsigned POST | `paystack 401`, `flutterwave 401` |
| First hour | errors in the logs of web, worker, scheduler | none |

Decisions taken on the way (his words): billing stays open to everyone for the first payments (no narrowing); both provider accounts are live; naira settlement first for dollar payments; every pilot grant ends now and those accounts get an ordinary trial; no VAT, no TIN.

## What's involved

`.env` on the server (names only above), `scripts/billing_close_test_data.py`, `app/config.py` (`BILLING_LIVE`, `BILLING_LIVE_SINCE`), the two live dashboards' webhooks (created by him: URLs `https://notes.beyrictech.com/api/billing/webhooks/paystack` and `.../flutterwave`, Flutterwave V3). No migration, no deploy in the run itself.

## Verification

**Verified live:** the run's proofs above. **NOT verified live:** any real payment, a signed webhook through nginx (the first real payment's `billing_events` row with `signature_ok` true proves it), the dashboards' webhook entries, what Flutterwave does with a USD payment from this account, a renewal, a refund.

## Operate / roll back

To pause purchases without losing data: `BILLING_ENABLED=false` and recreate (billing pages 404, the billing clock stops; hours, recordings and the trial go on). To go back to test keys: put the test keys back, `BILLING_LIVE=false`, remove `BILLING_LIVE_SINCE`, recreate; rows made live in between stay (they are real money records: do not do this after a real payment without reading the runbook's section 9). The closeout is not undone by itself: restore the backup above (a full database restore, which loses everything after it).

## Follow-ups

First real payments and refunds (runbook sections 5 and 6); dollar settlement decision after the first USD payment; remove the dead pilot-expiry job; a read-only live `proof` script; VAT only if Beyrichq registers (naira add-on, a money-path build).
