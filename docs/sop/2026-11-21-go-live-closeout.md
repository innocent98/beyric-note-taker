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
| Missing-invoice sweep, `app/billing/invoices.py` `issue_missing` | a test-era payment with no invoice is never invoiced (it would take a LIVE number and queue a receipt) |
| Country lock, `app/billing/profile.py` `country_lock` | a test payment no longer locks the person's billing country |
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

**Refuses** (exit 2, nothing changed, dry run included; the table under "Operate" says what to do for each): no `--before`; a `--before` that is not a timestamp with a zone, is in the future, or is older than 60 minutes (`--max-age-minutes N`); `BILLING_LIVE` true (it runs BEFORE the flip); any payment, subscription or saved card created at or after `T` (while `BILLING_LIVE` is false those are test rows the closeout would skip and the app would then read as live: this is what makes the operator pause billing first); a refund of a test-era payment still `pending` or `unknown`; a payment `pending` that was made in the last 30 minutes (someone may be paying). The message gives counts, never a value or an id. Anything unexpected outside one person's transaction (a database error) prints its type only and exits 1: a SQLAlchemy message would carry ids.

**Output**: counts and times only, never a name, email, token, reference or id. A before/after table and, under `--apply`, `PROOF: OK` only when after the run no live test-era subscription, no test card, no in-force bought hours and no pending test payment remain; otherwise `PROOF: FAILED`. A dry run prints `PROOF: not run`. It prints the reminder to run `deploy/backup.sh` first. Idempotent: a second `--apply` changes nothing and ends `PROOF: OK`.

### The after-state is inert (what else was checked)

| Thing the code reads | Why it cannot reach the live provider after the closeout |
|---|---|
| `ended` subscriptions | `renewals.run_due` selects only `active` and `past_due`; `states.due_action` answers nothing for `ended`; `live_row` reads only live statuses, so checkout is open again |
| `pending_plan_code` (a scheduled downgrade), `grace_ends_at`, `next_charge_at`, `retry_count` | applied only by the renewal of a live subscription; the first three are cleared, `retry_count` is never read for an ended row |
| Pending test payments | `failed`; and the sweeper skips every pre-`T` payment anyway |
| Failed test checkouts with a page (a person cancelled it) | the sweeper asked about them for 7 days; now it skips them |
| Parked test webhook events in `billing_events` | the sweeper skips events received before `T`; they are left in the table untouched |
| Open refunds on test payments | the script REFUSES while one is `pending` or `unknown`; and `refunds.resolve` refuses a refund of a test-era payment once the cutover is in force (`before_go_live`), because `made` would delete the person's LIVE saved card and mail them and `not_made` would give test hours back |

## `--end-pilot`: the pilot ends at go-live (owner decision 2026-11-21)

The owner's words: everything should end and close; no pilot grant, no reminder mail, nobody else uses the app. It is an explicit, separate opt-in (`--end-pilot`), so the plain closeout never touches pilot hours.

| What | Behaviour |
|---|---|
| Dry run | its own table row and a line "People holding pilot hours in force": counts only, no names |
| `--apply --end-pilot` | every `pilot` grant still in force gets `expires_at = now`, one transaction per person under the same hours lock. Never trial, plan, pack or admin-gift grants (the plain closeout's own rules cover the bought ones); never an administrator's grant; a deleted person's grants are included. **No mail, no bell entry** (it only updates `hour_grants`; a test counts mail and notifications before and after) |
| Refuses | while a meeting is being recorded (`bot_sent`, `processing`) or a join is due within 10 minutes (the deploy gate's two questions, in Python), because it would end hours a recording is using |
| Ledger | usage allocations are kept; ended grants stay as history (the same move as every other ended grant); idempotent |
| `PROOF: OK` | additionally means no pilot hours are in force |

**What a person who held a pilot grant sees afterwards.** They have no hours and **no trial**: `app/trial.py` gives a trial only to someone who never held a `trial` or `pilot` grant (`KINDS_THAT_USE_THE_TRIAL_UP`), and that stays true. Their state is `none` with 0 seconds and no grant (the same as anyone with nothing), a recording is refused with `no_plan`, and they must buy a plan. A test pins these values (`tests/test_billing_closeout.py`). The FE needs nothing new: this is the existing "no plan" state.

**Owner decision (2026-11-21): the go-live run uses BOTH flags**, `--end-pilot --trial-for-ended-pilot`. The flags stay opt-in in the script; the documented steps below use them. Production holds exactly four pilot grants for four people, not all of them the owner's: **the other three or more people lose their pilot hours at go-live and receive a 3 hour, 14 day trial instead.** The app sends them no mail and no bell entry, so tell them yourself if you wish. The dry run prints both counts (the pilot grants and the people holding them, and `Trials to give to ended-pilot people: N`); read both before applying.

**The two outcomes for a person whose pilot ended:**

| | Without `--trial-for-ended-pilot` (the default) | With `--end-pilot --trial-for-ended-pilot` |
|---|---|---|
| Hours | none | **one ordinary trial: 3 hours for 14 days, counted from the run** (`TRIAL_SECONDS` and `TRIAL_DAYS` as set; the server's `TRIAL_SECONDS` must be above 0) |
| State the FE sees | `none`, 0 seconds, joins refused with `no_plan` | `trial` with the trial's hours, the screens a new sign-up sees; when it ends (hours or 14 days) the same `none` |
| What they must do | buy a plan | use the trial, then buy a plan |

`--trial-for-ended-pilot` is a separate opt-in, only valid together with `--end-pilot`, never the default. For each person whose pilot grant ended **in this run**, who is confirmed, active, not suspended and not an administrator, it calls the app's own `trial.give` (so length, days, the daily budget and the ledger rule are exactly a new sign-up's; nothing is copied). **It deliberately bypasses one check, once, for this one-time conversion:** the rule that a past pilot grant used the trial up (`KINDS_THAT_USE_THE_TRIAL_UP`, `app/trial.py`). That is `give(..., after_pilot=True)`, which then counts only an earlier TRIAL grant: a person who already holds a trial gets none, and nothing else in the app passes the argument (a test shows a normal `give` still refuses a past pilot). The dry run says so in a line of its own and prints `Trials to give to ended-pilot people: N` (or `0 (TRIAL_SECONDS is 0 ...)`; a daily trial budget, if one is set, can reduce N at the run). The pilot's end and the trial are one transaction per person, so a failure gives neither; a second `--apply` gives none (no pilot left to end, and the person now holds a trial).

**`PAID_PLANS_OPEN_ON` must stay unset.** `app/pilot_expiry.py` (the 60-day expiry job and its 14-day mail) does nothing while it is unset, and it must stay that way: the job is now dead code to remove in a later change (with `docs/sop/2026-11-17-trial-and-pilot-expiry.md`'s pilot half). Nothing here removes it.

## Rejected alternatives

- **A `test_mode` column on `payments`, `subscriptions`, `payment_methods` and `hour_grants`**: a migration on four money tables for a one-time event, with a backfill that would itself need a cutover time. One timestamp does the same job with no schema change. (`invoices.test_mode` already exists and stays; it only picks the number series.)
- **Raw SQL by hand on the server**: money rows, no review, no dry run, no proof.
- **Wait for the test subscriptions to end by themselves**: under test keys they renew successfully, so they never end.
- **Calling `renewals._close`** to end the subscriptions: it mails the person "Your plan has ended".
- **Deleting test payments and invoices**: they are money records and history; hiding is enough, and an admin keeps them.
- **Ending pilot grants inside the plain closeout**: the owner's decision is explicit and a plain run must never end hours it was not asked to; a separate flag keeps its dry run readable.
- **Assuming UTC for a timestamp with no zone**: an hour of error would silently move the line.

## Files

`app/billing/cutover.py` (new), `app/config.py`, `app/billing/{invoices_api,subscriptions_api,cancel_checkout,refunds,renewals,admin_api,invoices,profile,profile_api}.py`, `scripts/billing_close_test_data.py` (new), `scripts/capture_billing_cutover.py` (new, the FE capture), `scripts/clean_env.py` (the cleaner now knows `BILLING_LIVE_SINCE` and never removes it), `deploy/README.md` (the env row; the stale "a test-mode invoice says it is not a tax document" sentence, audit row 25, replaced), `tests/test_billing_closeout.py`, `tests/test_billing_cutover.py`, `tests/test_clean_env.py`, `tests/test_billing_refunds.py` (the two direct `_start` calls take `settings`), `docs/fe-integration-guide-billing.md` (new last section), `docs/fe-captures/billing-cutover/`. **No migration. `.env.example` is unchanged**: it lists no billing switch by name besides `BILLING_ENABLED` and `BILLING_ALLOWED_EMAILS`, and `BILLING_LIVE` and the keys are documented in `deploy/README.md` (the new row is there). **Signature changes** a reader may trip on: `refunds.assess(db, payment, reason, now, settings, *, lock=True)`, `refunds._start(..., note, settings, now)`, `refunds.resolve(..., now, settings)` and `invoices.issue_missing(session, now, settings)` take `settings`; `profile.country_lock`, `country_change_allowed_at` and `change_country` take an optional `settings` (None means no cutover; every route passes it); `subscriptions_api._invoice_numbers(db, rows, settings)`.

## Verification

Local, on a scratch Postgres, tests of the touched files and a named related set (the full suite is CI's):

| What | Where |
|---|---|
| Every row kind before and after; trial, pilot and gift grants kept; rows made after `T` untouched (a live plan, its card, its grant, a pending checkout); payments, invoices and refunds never deleted and only `pending` to `failed`; idempotent second `--apply`; a deleted person's rows; a closed person can buy a live plan | `tests/test_billing_closeout.py` |
| Refuses: missing, blank, garbage, date-only, no-zone, future and stale (61 minutes; 60 passes; `--max-age-minutes` raises it) `--before`; `BILLING_LIVE` true; a row made at or after `T` (a live plan, a card, a checkout; also a test checkout finished after a first run); a pending payment 10 minutes old (not 31); an open refund (`pending`, `unknown`; a `submitted` one does not stop it) | same |
| One person's failure rolls that person back whole, the next person is still closed, exit 1 and `PROOF: FAILED`, then a clean re-run | same |
| Output has no secret: a planted card token, emails and every payment reference are absent from dry-run and apply output | same |
| Renewals of an `ended` subscription never charge or look up (fake provider records zero calls; control: both were due before the closeout); the sweeper skips test-era pending, failed-with-page and parked events (control: without the setting it asks about them) | same |
| Setting validation: valid forms (`Z`, offsets, space separator), garbage, no zone, date-only, future with and without `BILLING_LIVE`, ignored while not live, the boundary at `T` | `tests/test_billing_cutover.py` |
| The filter in each route with test and live rows mixed (person's invoices, PDF, payments, payment read, cancel, waiting payment; the admin list; the refund 409); the first-payment rule with a test-era plan payment, the closeout, then a live one | same |
| One person with test AND live history (closed, then a live plan: closing again changes nothing live); a grant linked only through the subscription; a partly used grant (ended, allocations kept); a database error prints its type only; the dry run counts test payments with no invoice | same |
| The missing-invoice sweep skips a test-era payment (control: it issues it without the cutover); the country lock ignores a test payment (control: it locks without it; the country changes at once); `refunds.resolve` refuses a test-era refund (control: it resolves without the cutover) | `tests/test_billing_cutover.py` |
| `--trial-for-ended-pilot`: one ordinary trial (3 h, 14 days from the run, the same reason text as a sign-up's) to an eligible ended-pilot person; none to an admin, the deleted person's grants, a holder of a trial, an unconfirmed or suspended account, or with `TRIAL_SECONDS` 0; refused without `--end-pilot`; dry-run counts; idempotent; one person's failure leaves their pilot and no trial; a normal `trial.give` still refuses a past pilot | same |
| `--end-pilot`: dry run counts grants and people; apply ends non-admin pilot grants only (an admin's, an already ended one, trial and gift untouched; the deleted person's included), no mail or bell entry, idempotent, ledger rows kept, refuses while a join is due or a meeting is running, one person's failure leaves their pilot hours; the post-pilot state is `none`, 0 seconds, no grant, refusal `no_plan`, and no trial; un-pause refusal wording; `--max-age-minutes` 0 refused; the country functions raise `TypeError` without `settings` | same |
| A mutation check: with `cutover.since` disabled, 6 of the cutover tests fail | run once by hand, then restored |
| FE capture of the 404 for a hidden invoice and the rest, over real HTTP on the real app | `docs/fe-captures/billing-cutover/` (run: `python scripts/capture_billing_cutover.py <scratch database URL>`) |

**Not verified:** any of this against a real Paystack or Flutterwave (the providers are the repo's fake); the script on the production database (its first real run is the dry run in step 4 below); a browser.

## Operate: the exact steps, in plain words

The runbook (`docs/launch/billing-go-live-runbook.md`, section 4, steps 2 to 9) has the same steps in its table. Each step has ONE proof; if it does not match, stop and tell the lead session. Commands run on the server, in `/opt/beyric-note-taker`. The app must already be deployed with this PR.

**Why the order matters (Opus review of PR #255).** While `BILLING_LIVE` is false, anything created at or after `T` is a TEST row that the closeout skips and the app then reads as live (a test checkout finished after the closeout would make a new test plan and card, which hit the stuck-forever Paystack renewal a month later). So billing is **paused first** and `T` is taken **after** the pause; then no test row can be made between `T` and the flip, and the script refuses to run if one was.

| # | Do | Proof |
|---|---|---|
| 1 | **Gate check** (a recreate follows): `bash -c '. deploy/gate.sh; join_due_soon && echo "no join is due"'`. **And, before the pause** (the pause switches the admin screen off): `q <<<"select count(*) from refunds where status in ('pending','unknown');"` | prints `no join is due`, and the refund count is `0`. If not, resolve each open refund in Admin, Billing, Refunds as made or not made first (every payment before the flip is a test payment) |
| 2 | **Pause billing.** Edit `.env` yourself: `BILLING_ENABLED=false` (nothing else), then `docker compose up -d --force-recreate web worker scheduler`. This stops new checkouts, the billing pages and routes (404), the webhook route and the billing clock (renewals, retries, sweeper); hours and recordings go on | `docker compose exec -T web printenv BILLING_ENABLED </dev/null` prints `false`, and the webhook curl in the runbook answers `404` for both providers |
| 3 | **Back up the database**: `BACKUP_DIR=~/backups/beyric-notes bash deploy/backup.sh` | `ls -lt ~/backups/beyric-notes \| head -3` shows a new dump stamped now. **This is the only undo** |
| 4 | **Take `T` now, after the pause**, in UTC, and keep it in this terminal: `T=$(date -u +%Y-%m-%dT%H:%M:%SZ); echo "$T"` | prints a time ending in `Z`, such as `2026-11-22T09:30:00Z` |
| 5 | **Dry run** (changes nothing): `docker compose exec -T web python scripts/billing_close_test_data.py --before "$T" --end-pilot --trial-for-ended-pilot </dev/null` | says `DRY RUN`, a table of counts and `PROOF: not run`. A line starting `REFUSED` means do not go on; it says why (see the table below) |
| 6 | **Read the table.** Compare with the runbook's step 5 queries: the live test-era subscriptions, the bought hours in force, the saved cards, the abandoned checkouts. Trial, pilot and gift hours are never in it | the counts match; if a count is far larger than expected, stop |
| 7 | **Apply**: `docker compose exec -T web python scripts/billing_close_test_data.py --before "$T" --apply --end-pilot --trial-for-ended-pilot </dev/null; echo "exit $?"` | the last lines say `PROOF: OK (...)` and `exit 0`. `PROOF: FAILED` or `exit 1`: run the same command again (it only does what is left); if it fails again, restore the backup (below) and tell the lead |
| 8 | **Edit `.env` yourself, ONE edit**: the live keys and secrets, `BILLING_LIVE=true`, `BILLING_LIVE_SINCE=<the T from step 4, exactly as printed>`, and `BILLING_ENABLED=true` (`BILLING_ALLOWED_EMAILS` stays `'*'`: owner decision, no narrowing) | `grep -E '^(BILLING_LIVE\|BILLING_LIVE_SINCE\|BILLING_ENABLED)=' .env` prints the three lines; `BILLING_LIVE_SINCE` ends in `Z` (do not paste keys into chat) |
| 9 | **Recreate, ONE restart** (repeat the gate check first): `docker compose up -d --force-recreate web worker scheduler` | `docker compose ps` shows web, worker and scheduler Up, and `docker compose exec -T web printenv BILLING_LIVE_SINCE </dev/null` prints `T`. If web exits, `docker compose logs web --tail 30` names `BILLING_LIVE_SINCE` (no zone, not a timestamp, or in the future) |
| 10 | **Check the gap and the old webhooks, before anyone pays.** With the runbook's `q` helper: `q <<SQL` ... `select (select count(*) from payments where created_at >= '$T') as payments, (select count(*) from subscriptions where created_at >= '$T') as subscriptions, (select count(*) from payment_methods where created_at >= '$T') as cards; SQL`. Then **remove the webhook URL from both providers' TEST dashboards** | prints `0 \| 0 \| 0`. Any other number means a row was made after `T` and before the flip: stop and tell the lead. (Also the read-only check that nothing before `T` is live: `select status, count(*) from subscriptions where created_at < '$T' group by 1;` shows no `active` or `past_due`, and `select count(*) from payment_methods where created_at < '$T';` prints `0`) |

**What makes the script refuse** (exit 2, nothing changed; a dry run refuses too):

| Message | What to do |
|---|---|
| `--before is required`, not a timestamp, no zone, in the future | write `T` as printed by step 4 |
| `--before is older than 60 minutes` | take a new `T` (the bound is `--max-age-minutes N`, default 60) |
| `BILLING_LIVE is true` | the flip was done early: stop and tell the lead |
| `billing rows were made at or after --before` (counts of payments, subscriptions, saved cards) | billing was not paused (or a row was made since): pause it, take a new `T`; if rows already exist, tell the lead (they are test rows the closeout would skip) |
| `refund(s) of test-era payments are still open` | the pause switched the admin screen off, so: un-pause (`BILLING_ENABLED=true`, recreate web, worker and scheduler), resolve each in Admin, Billing, Refunds as made or not made, pause again, take a NEW T, run again. Step 1's count exists so this does not happen |
| `meeting(s) are being recorded, or a join is due` (only with `--end-pilot`) | wait until none is (the step 1 gate), then run again |
| `--max-age-minutes must be 1 or more` | use a positive number |
| `payment(s) made in the last 30 minutes are still pending` | someone may be at the provider's page: wait until 30 minutes after the last one |

**Two lines to know.** The script holds a person's hours lock and then their payment row; a refund being made at the same moment takes them in the other order and can deadlock: Postgres ends one side, that person is counted as failed, and running the command again finishes them (billing is paused, so this should not happen). Rows of a deleted person (no owner) are closed without an hours lock, because there is no person to lock; with billing paused nothing else touches them.

**Roll back.** The closeout is not undoable by itself. If something is wrong after step 7, restore the step 3 dump (`deploy/README.md`, "Restore (practise once)": it restores into a scratch database first, then the real one) and tell the lead; nothing else restores the subscriptions, hours and cards. Before step 8 you can stay on test keys and the app is unharmed (people with ended test plans simply have none; they can buy again). To turn the cutover off later, remove the `BILLING_LIVE_SINCE` line and recreate: hidden rows reappear (nothing was deleted). Never go back to test keys after a real customer has paid (runbook section 9).

## Follow-ups

- The runbook (PR #254, merged) was updated in this PR to the pause-first order, steps 2 to 9 of its section 4.
- A read-only `proof` and `preflight` for live mode (audit row 19) is still open and not blocking.
- `deploy/README.md` and the runbook do not yet describe how an admin finds an open test refund beyond "Admin, Billing, Refunds"; check the wording on the screen when it is first used.
