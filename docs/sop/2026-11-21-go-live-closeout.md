# SOP: the go-live closeout of test-mode billing data, and the `BILLING_LIVE_SINCE` cutover

**What shipped.** One PR, branch `feature/go-live-closeout`. Two things that must both exist before the payment keys are switched from test to live:

1. **`scripts/billing_close_test_data.py`**, a one-time closeout, run just BEFORE the flip. It ends every subscription made with test keys, ends the hours those payments bought, deletes the saved test cards and fails the abandoned test checkouts. Dry run by default; counts only.
2. **`BILLING_LIVE_SINCE`**, a setting holding the cutover time `T`. While `BILLING_LIVE` is true, a payment made before `T` is hidden from the person, is not counted as their first plan payment, is never asked about on the live provider and offers no refund.

Design and the reasons it must exist: `docs/launch/billing-go-live-code-audit.md` ("The finding that matters most", rows 9, 11, 15, 25) in PR #254. This SOP is the record of what was built and how to run it.

## Why

The database does not know which mode a row was made in. On the day the keys flip, every test row is read as live and the LIVE provider is asked about test references and test card tokens. Read from the code (and checked again for this build):

| Test-era row | Without the closeout |
|---|---|
| Active Paystack subscription | The renewal sends the TEST token to the live API (`app/billing/renewals.py` `_attempt`), gets no clean answer, and the payment stays `pending` while the subscription stays `active`: no failure counted, no grace, no mail, retried every minute for ever, and `already_subscribed` stops the person buying (`app/billing/subscriptions.py:215`). **Blocks go-live.** |
| Active Flutterwave subscription | A 400 becomes a `failed` charge: three false mails ("We could not renew your plan", "Last day to pay", "Your plan has ended") to a person who never paid, then a lapse |
| Hours from a test plan or pack | Stay in force: a test pack is 10 free hours for 12 months |
| Saved test card | Used for the person's renewal if no live card replaced it |
| First-plan refund rule | `refunds._is_first_plan_payment` counted the test payment as "first", so a person's first LIVE payment could not be refunded (409 `not_first_payment`) |
| Test invoices and payments in the person's lists | Listed and downloadable (`BN-2026-9000NN`) for payments where no money moved |

## How

**One rule, one place.** `app/billing/cutover.py` is the only place that says what "test-era" means: a payment created strictly BEFORE `T` (a row made exactly at `T` is live). `since(settings)` returns `T` only while `BILLING_LIVE` is true and the setting is set; `is_test_era(created_at, settings)` answers for a row in hand; `live_only(column, settings)` is the `where` clause for a query. Everything below calls one of those. The script's `--before` is parsed by the same `parse_instant` (`app/config.py:25`) the setting is validated with.

**The setting** (`app/config.py:433`, validators at `:258` and `:268`). An ISO 8601 timestamp WITH its zone (`2026-11-22T09:30:00Z`, or an offset such as `+01:00`); stored as given, read as UTC. Decided: **a timestamp with no zone is refused**, not assumed UTC, because a Lagos clock read as UTC is an hour out and the hour would hide or show the wrong payments; a date with no time is refused for the same reason. A bad value stops web, worker and scheduler at startup naming `BILLING_LIVE_SINCE` and never printing the value, **even while `BILLING_LIVE` is false** (a typo found on the go-live day would block the go-live). A `T` in the future is refused while `BILLING_LIVE` is true (it would hide every live payment before it) and allowed while it is false. **With `BILLING_LIVE` false the setting is ignored**: every row counts, as before.

**Where the rule is applied** (all use the helper):

| Place | What changes |
|---|---|
| Invoice list and PDF route, `app/billing/invoices_api.py:103`, `:141` | a test-era invoice is not listed; its PDF answers 404 `not_found`, the same body as an unknown number |
| Payments list, `payment` read, `_invoice_numbers`, the waiting `pending_payment`, `app/billing/subscriptions_api.py:217`, `:335`, `:402`, `:426` | not listed; a read by reference is 404; no invoice number is shown for it; a test-era pending checkout is not "the waiting payment" |
| Cancel a checkout, `app/billing/cancel_checkout.py:115` | 404 (the provider is never asked about it) |
| First plan payment rule, `app/billing/refunds.py:134` | a person's first live plan payment is the first at or after `T` |
| Refund of a test-era payment, `app/billing/refunds.py:162` | refused 409 `before_go_live` before anything is written or the provider called (a refund call on a payment the live provider does not know would end `unknown` and could never be asked again). This is new: the audit did not list it |
| Sweeper, `app/billing/renewals.py:612` (parked webhook events, by `received_at`) and `:668` (pending and failed-with-a-page payments) | test-era rows are never looked up on the live API (the audit called this noise; it also fed the "Payments have waited more than a day" alert) |
| Admin payments list, `app/billing/admin_api.py` | **still lists everything** (an admin keeps the whole history; it is not shown to people and shows no test wording). The refund option of a test-era payment is `allowed: false` with the reason |

**The closeout script**, `scripts/billing_close_test_data.py`. Standard library plus the app's own models and session helpers. Per person, ONE transaction (`close_person`), with the person's hours lock held (`hours.lock_person`, the lock a recording takes) so no recording is counted against those hours meanwhile:

| Row | Closed to |
|---|---|
| Subscription made before `T`, `active` or `past_due` | `ended`; `next_charge_at`, `grace_ends_at`, `pending_plan_code` cleared; `canceled_at` set to now when it was empty. **No mail** (it does not go through `renewals._close`) |
| Hour grant of kind `plan_period` or `pack` still in force whose source payment, **or whose subscription**, was made before `T` | `expires_at = now`. **Trial, pilot and admin-gift grants are never selected.** (The audit named the source payment only; the subscription is added so an upgrade's grant is caught too.) |
| Saved card (`payment_methods`) made before `T` | deleted |
| Payment `pending` made before `T` | `failed` (no provider call, `pay_url` kept) |

Payments, invoices and refunds are never deleted. Each update re-checks the condition it selected on, so a row another process changed in between is left alone. A failure for one person (any exception) rolls back that person alone, is counted (type only), and the run continues; the exit code is 1 if any person failed or the proof does not hold. `canceled_at` keeps an earlier time when one is there (the audit said "now"; the earlier time is the person's real request).

**Refuses** (exit 2, nothing changed, dry run included): no `--before`; a `--before` that is not a timestamp with a zone; a `--before` in the future; `BILLING_LIVE` true (it runs BEFORE the flip); a payment `pending` that was made in the last 10 minutes (someone is paying). The message never echoes the value.

**Output**: counts and times only, never a name, email, token, reference or id. A before/after table and, under `--apply`, `PROOF: OK` only when after the run no live test-era subscription, no test card, no in-force bought hours and no pending test payment remain; otherwise `PROOF: FAILED`. A dry run prints `PROOF: not run`. It prints the reminder to run `deploy/backup.sh` first. Idempotent: a second `--apply` changes nothing and ends `PROOF: OK`.

### The after-state is inert (what else was checked)

| Thing the code reads | Why it cannot reach the live provider after the closeout |
|---|---|
| `ended` subscriptions | `renewals.run_due` selects only `active` and `past_due`; `states.due_action` answers nothing for `ended`; `live_row` reads only live statuses, so checkout is open again |
| `pending_plan_code` (a scheduled downgrade), `grace_ends_at`, `next_charge_at`, `retry_count` | applied only by the renewal of a live subscription; the first three are cleared, `retry_count` is never read for an ended row |
| Pending test payments | `failed`; and the sweeper skips every pre-`T` payment anyway |
| Failed test checkouts with a page (a person cancelled it) | the sweeper asked about them for 7 days; now it skips them |
| Parked test webhook events in `billing_events` | the sweeper skips events received before `T`; they are left in the table untouched |
| Open refunds on test payments | not touched (none expected; a refund still `pending` or `unknown` on a test payment is an admin's to resolve) |

## Rejected alternatives

- **A `test_mode` column on `payments`, `subscriptions`, `payment_methods` and `hour_grants`**: a migration on four money tables for a one-time event, with a backfill that would itself need a cutover time. One timestamp does the same job with no schema change. (`invoices.test_mode` already exists and stays; it only picks the number series.)
- **Raw SQL by hand on the server**: money rows, no review, no dry run, no proof.
- **Wait for the test subscriptions to end by themselves**: under test keys they renew successfully, so they never end.
- **Calling `renewals._close`** to end the subscriptions: it mails the person "Your plan has ended".
- **Deleting test payments and invoices**: they are money records and history; hiding is enough, and an admin keeps them.
- **Assuming UTC for a timestamp with no zone**: an hour of error would silently move the line.

## Files

`app/billing/cutover.py` (new), `app/config.py`, `app/billing/{invoices_api,subscriptions_api,cancel_checkout,refunds,renewals,admin_api}.py`, `scripts/billing_close_test_data.py` (new), `scripts/capture_billing_cutover.py` (new, the FE capture), `scripts/clean_env.py` (the cleaner now knows `BILLING_LIVE_SINCE` and never removes it), `deploy/README.md` (the env row; the stale "a test-mode invoice says it is not a tax document" sentence, audit row 25, replaced), `tests/test_billing_closeout.py`, `tests/test_billing_cutover.py`, `tests/test_clean_env.py`, `tests/test_billing_refunds.py` (the two direct `_start` calls take `settings`), `docs/fe-integration-guide-billing.md` (new last section), `docs/fe-captures/billing-cutover/`. **No migration. `.env.example` is unchanged**: it lists no billing switch by name besides `BILLING_ENABLED` and `BILLING_ALLOWED_EMAILS`, and `BILLING_LIVE` and the keys are documented in `deploy/README.md` (the new row is there). **Signature changes** a reader may trip on: `refunds.assess(db, payment, reason, now, settings, *, lock=True)` and `refunds._start(..., note, settings, now)` now take `settings`; `subscriptions_api._invoice_numbers(db, rows, settings)`.

## Verification

Local, on a scratch Postgres, tests of the touched files and a named related set (the full suite is CI's):

| What | Where |
|---|---|
| Every row kind before and after; trial, pilot and gift grants kept; rows made after `T` untouched (a live plan, its card, its grant, a pending checkout); payments, invoices and refunds never deleted and only `pending` to `failed`; idempotent second `--apply`; a deleted person's rows; a closed person can buy a live plan | `tests/test_billing_closeout.py` |
| Refuses: missing, blank, garbage, date-only, no-zone, future `--before`; `BILLING_LIVE` true; a payment pending 3 minutes (but not 11) | same |
| One person's failure rolls that person back whole, the next person is still closed, exit 1 and `PROOF: FAILED`, then a clean re-run | same |
| Output has no secret: a planted card token, emails and every payment reference are absent from dry-run and apply output | same |
| Renewals of an `ended` subscription never charge or look up (fake provider records zero calls; control: both were due before the closeout); the sweeper skips test-era pending, failed-with-page and parked events (control: without the setting it asks about them) | same |
| Setting validation: valid forms (`Z`, offsets, space separator), garbage, no zone, date-only, future with and without `BILLING_LIVE`, ignored while not live, the boundary at `T` | `tests/test_billing_cutover.py` |
| The filter in each route with test and live rows mixed (person's invoices, PDF, payments, payment read, cancel, waiting payment; the admin list; the refund 409); the first-payment rule with a test-era plan payment, the closeout, then a live one | same |
| A mutation check: with `cutover.since` disabled, 6 of the cutover tests fail | run once by hand, then restored |
| FE capture of the 404 for a hidden invoice and the rest, over real HTTP on the real app | `docs/fe-captures/billing-cutover/` (run: `python scripts/capture_billing_cutover.py <scratch database URL>`) |

**Not verified:** any of this against a real Paystack or Flutterwave (the providers are the repo's fake); the script on the production database (its first real run is the dry run in step 4 below); a browser.

## Operate: the exact steps, in plain words

The runbook (`docs/launch/billing-go-live-runbook.md`, PR #254) puts these between its step 4 and step 6. Each step has ONE proof; if it does not match, stop and tell the lead session. Commands run on the server, in `/opt/beyric-note-taker`. **Nobody buys anything while this is going on** (a few minutes). The app must already be deployed with this PR.

| # | Do | Proof |
|---|---|---|
| 1 | **Back up the database**: `BACKUP_DIR=~/backups/beyric-notes bash deploy/backup.sh` | `ls -lt ~/backups/beyric-notes \| head -3` shows a new dump stamped now. **This is the only undo** (the closeout cannot undo itself) |
| 2 | **Take the cutover time** `T`, in UTC, and keep it: `T=$(date -u +%Y-%m-%dT%H:%M:%SZ); echo "$T"` | prints a time ending in `Z`, such as `2026-11-22T09:30:00Z`. Write it down: the same text goes in step 5 and step 6 |
| 3 | **Dry run** (changes nothing): `docker compose exec -T web python scripts/billing_close_test_data.py --before "$T" </dev/null` | says `DRY RUN`, a table of counts (about as many subscriptions as the runbook's step 3 query showed) and `PROOF: not run`. A line starting `REFUSED` means do not go on: it says why (someone paying: wait ten minutes; `BILLING_LIVE` already true: the flip was done early) |
| 4 | **Read the table.** The four numbers must be what you expect from the runbook's step 3 queries (the live test-era subscriptions, the bought hours in force, the saved cards, the abandoned checkouts). Trial, pilot and gift hours are never in it | the counts match; if a count is far larger than expected, stop |
| 5 | **Apply**: `docker compose exec -T web python scripts/billing_close_test_data.py --before "$T" --apply </dev/null; echo "exit $?"` | the last lines say `PROOF: OK (...)` and `exit 0`. `PROOF: FAILED` or `exit 1` means some person was not closed: run the same command again (it only does what is left); if it fails again, restore the backup (below) and tell the lead |
| 6 | **Edit `.env` yourself** (runbook step 6): the live keys, `BILLING_LIVE=true`, and a new line `BILLING_LIVE_SINCE=<the T from step 2>`, exactly as printed | `grep -E '^BILLING_LIVE_SINCE=' .env` prints the line, and the value ends in `Z` (do not paste keys into chat) |
| 7 | **Recreate** (runbook step 7): `docker compose up -d --force-recreate web worker scheduler` | `docker compose ps` shows web, worker and scheduler Up, and `docker compose exec -T web printenv BILLING_LIVE_SINCE </dev/null` prints `T`. If web exits, `docker compose logs web --tail 30` names `BILLING_LIVE_SINCE` (no zone, not a timestamp, or in the future) |
| 8 | **Read-only check after the flip** (the script now refuses to run, by design): with the runbook's `q` helper, `q <<SQL` ... `select status, count(*) from subscriptions where created_at < '$T' group by 1; select count(*) from payment_methods where created_at < '$T'; SQL` | only `ended`, `lapsed` or `canceled` rows before `T` (no `active`, no `past_due`), and the card count is `0` |

**Roll back.** The closeout is not undoable by itself. If something is wrong after step 5, restore the step 1 dump (`deploy/README.md`, "Restore (practise once)": it restores into a scratch database first, then the real one) and tell the lead; nothing else restores the subscriptions, hours and cards. Before step 6 you can stay on test keys and the app is unharmed (people with ended test plans simply have none; they can buy again). To turn the cutover off later, remove the `BILLING_LIVE_SINCE` line and recreate: hidden rows reappear (nothing was deleted). Never go back to test keys after a real customer has paid (runbook section 9).

## Follow-ups

- Update PR #254's runbook text to name these exact commands and the `BILLING_LIVE_SINCE=<T>` line (its sections 0, 2 and 4 say "its exact form is in that PR"); this SOP's table above is the text to use.
- A read-only `proof` and `preflight` for live mode (audit row 19) is still open and not blocking.
- A refund that is `pending` or `unknown` on a test payment is not handled by the script (none expected); an admin resolves it.
