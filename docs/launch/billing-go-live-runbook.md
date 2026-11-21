# Billing go-live runbook (stage S11): from test keys to live keys

Written 2026-11-21 for Adebayo. Billing now runs on the providers' TEST keys (Paystack for naira, Flutterwave for dollars). No real money has moved. This runbook switches it to live keys, takes one real payment per provider with your own card, refunds it, and says how to watch and how to go back.

How to read it: every step has the exact command or click path and **one proof** (a command whose output you check, or a screen). If a proof does not match, stop and tell the lead session; do not go on to the next step. Commands run on the server: `ssh haven-respite`, then `cd /opt/beyric-note-taker`. **You type every key into the server's `.env` file yourself. Never paste a key, a secret hash or a signing secret into chat, git or a PR.**

Companion documents: `docs/launch/billing-go-live-code-audit.md` (what in the code depends on test data), `docs/launch/billing-go-live-questions.md` (the six things only you can answer).

## 0. The closeout (built: PR #255)

The code audit found that **rows made with test keys misbehave the moment live keys are in**: a Paystack test subscription gets stuck forever (the person can neither renew nor buy), a Flutterwave one sends three false "payment failed" mails, and test packs and plans keep their free hours. Two things were built for it and **shipped before this runbook can be used (PR #255, `docs/sop/2026-11-21-go-live-closeout.md`)**: a one-time closeout script, `scripts/billing_close_test_data.py`, and a setting `BILLING_LIVE_SINCE` that hides test-era payments from people. **Step 6 below is that script, and step 3 (pausing billing) must come first. Do not flip the keys before the script has run.** The script also ends every pilot hour grant (`--end-pilot`, owner decision 2026-11-21: nothing from the pilot carries over; no mail, nobody else uses the app). The owner decided (2026-11-21) that the people whose pilot ends also get one ordinary trial instead, so step 6 uses BOTH flags, `--end-pilot --trial-for-ended-pilot` (it needs `TRIAL_SECONDS` above 0, which production has at 10800): each gets 3 hours for 14 days from the run. The app sends them no mail; tell them yourself if you wish. `PAID_PLANS_OPEN_ON` must stay unset: the 60-day pilot expiry job and its mail are no longer wanted (dead code, to remove later). It is not a question for you; it is our work. The SOP has the same steps with the reason for each.

## 1. What you prepare yourself

| # | What | Where, and what the provider's own pages say | Proof |
|---|---|---|---|
| 1.1 | **Paystack account activated** (live mode) | Dashboard, the Compliance page. For a Nigerian registered company, per Paystack's Nigeria page (read 2026-11-21): for a privately held company incorporated Dec 2020 onwards, RC number, Memorandum and Articles of Association, Certificate of Incorporation, **Tax Identification Number**, CAC status report, at least one director, and the owners of at least 50%, plus proof of address (electricity, water, waste or cable TV bill). The account section needs a **corporate bank account in the same name as the registration document**. "Your business will automatically go live if all the fields are complete and your documents have been verified"; if there is a problem the Business Reviews team writes within 24 hours | The dashboard toggle (top right) is green and says **Live** (Paystack, Test Mode and Live Mode) and the activation status reads Pre-approved or Approved |
| 1.2 | **Flutterwave account approved** | Dashboard, onboarding. For a registered company (Nigeria onboarding page, read 2026-11-21): Certificate of Incorporation, Memorandum and Articles of Association, NIN and BVN of the business representative, proof of residential address (electricity meter number) for the representative and directors, a verifiable website URL, valid ID of all shareholders and directors. Original documents only, no photocopies or scans; **each director gets an email to verify separately and at least one must finish**. Until approved: "Live payments stay disabled until you complete KYC" (Flutterwave docs quickstart) and "Only approved accounts are eligible for settlements" (settlements page). **No approval time is stated on any page I read** | You can switch the dashboard to live mode and see live keys under Settings, API Keys |
| 1.3 | **Settlement bank accounts** | Paystack: account section above, payouts next working day (T+1) (pricing page). Flutterwave: "Add your preferred bank account details for settlements in the settings section"; Nigerian merchants are settled to their Flutterwave Microfinance Bank account, local cards 1 business day, international 5 (support page, read 2026-11-21) | The bank details show as saved in each dashboard |
| 1.4 | **Dollar settlement decision** | Flutterwave: international cards are "charged and settled in your local currency by default"; non-NGN balances need "Settle into my payout balance" under Settings, Business preference, or a settlement account in that currency set up through support. Paystack: "we're running a public pilot of USD settlements" to Zenith Bank domiciliary accounts. **Whether your Flutterwave account can create a USD checkout at all is not on any page I read; the first real dollar payment is the proof** | Question 2 in the questions file |
| 1.5 | **Live keys** | Paystack: switch to Live mode, then the API keys area; the **webhook signing secret** (`whsec_`) is under Developers, API configuration (Paystack webhooks page). Flutterwave: Settings, API Keys under DEVELOPERS, with the mode toggle in the sidebar (Flutterwave authentication page: "When you switch between modes, we'll also switch the API keys shown"; live keys are masked, "Generate new keys" rotates them) | You can see a secret key and a public key for each provider in live mode |
| 1.6 | **Two fresh test people** | Two new sign-ups with email addresses you control, never used for a test purchase (one per provider). Reason: the admin refund screen only allows "first plan payment" for a person's earliest plan payment ever, and a test-era payment counts (audit row 15) | Two confirmed accounts, each showing the 3-hour trial and no plan |
| 1.7 | **A card** | Your own, with enough limit for NGN 25,000 and USD 18, which you can see in your banking app | n/a |

## 2. The exact `.env` changes (names only, no values)

File: `/opt/beyric-note-taker/.env`. All keys change in ONE edit, because `BILLING_LIVE` is one flag for both providers.

| Variable | Change | Note |
|---|---|---|
| `BILLING_LIVE` | `false` → `true` | The one flag. The app also checks it at startup against the keys, below |
| `PAYSTACK_SECRET_KEY` | test → live | Must start `sk_live_` |
| `PAYSTACK_PUBLIC_KEY` | test → live | Must start `pk_live_` |
| `PAYSTACK_WEBHOOK_SECRET` | test `whsec_…` → live `whsec_…` | Each mode has its own signing secret. Left at the test value, every live webhook is a 401 |
| `FLUTTERWAVE_SECRET_KEY` | test → live | Must NOT contain `_TEST` |
| `FLUTTERWAVE_PUBLIC_KEY` | test → live | Accepted, not read by the app |
| `FLUTTERWAVE_SECRET_HASH` | a new value you make up | The same text goes in the live dashboard's webhook page. Make it with `openssl rand -hex 32` in your own terminal; do not reuse the test one |
| `BILLING_ALLOWED_EMAILS` | **unchanged** (`'*'`): billing stays open to everyone | Owner decision 2026-11-21: no narrowing for the first payments. The risk, in one sentence: a fault in the live payment path would meet a real customer, and the owner accepted that |
| `BILLING_ENABLED` | `false` in step 3 (the pause), back to `true` in step 7 | The pause is what keeps test rows from being made while the closeout runs |
| `BILLING_COUNTRY_ROUTES`, `HOURS_MODE`, `FEATURE_GATES_MODE`, `TRIAL_SECONDS` | unchanged | Default routes, `enforce`, `enforce`, `10800` |
| `BILLING_SELLER_NAME`, `…_ADDRESS`, `…_REGISTRATION_NO`, `…_EMAIL` | unchanged | Already set. `BILLING_SELLER_TAX_ID` stays empty (no tax ID): the invoice then shows no tax line. `BILLING_VAT_RATE_BP` stays unset (0): no VAT is charged |
| `BILLING_LIVE_SINCE` | add: the cutover time `T`, the **same text** you pass to `--before` in step 6 (for example `2026-11-22T09:30:00Z`: UTC, with the `Z`) | Only has effect while `BILLING_LIVE` is true. Text with no zone, or not a timestamp, or in the future, stops web, worker and scheduler at startup naming this setting. It hides every payment made before `T` from people (invoice list and PDF, payments list) and from the first-payment refund rule; the admin screen still lists them |

**The rule: test and live keys never mix.** With `BILLING_LIVE=true`, web, worker and scheduler **refuse to start** if any key is the wrong kind, and name the setting but never print the key. The messages (from `app/config.py`):

- `PAYSTACK_SECRET_KEY is not a live-mode key (it must start with sk_live_) while BILLING_LIVE is true` (same for `PAYSTACK_PUBLIC_KEY` and `pk_live_`)
- `FLUTTERWAVE_SECRET_KEY is a Flutterwave TEST key (it contains _TEST) while BILLING_LIVE is true` (same for `FLUTTERWAVE_PUBLIC_KEY`)
- `PAYSTACK_WEBHOOK_SECRET is set but PAYSTACK_SECRET_KEY is not` and `FLUTTERWAVE_SECRET_HASH is set but FLUTTERWAVE_SECRET_KEY is not`

`sk_live_` / `pk_live_` are the app's assumption; the Paystack pages I read do not state them. If Paystack's live keys look different the stack will not start, which is safe and obvious: tell the lead.

**Both accounts are live (owner, 2026-11-21), so this path is optional, not the plan.** If one turns out not to be, go live with the other alone. Remove all three variables of the other provider from `.env` (a hash without a key stops startup) and switch its country route off: for Paystack only, `BILLING_COUNTRY_ROUTES=NG=NGN:paystack,*=off`; for Flutterwave only, `BILLING_COUNTRY_ROUTES=NG=off,*=USD:flutterwave`. A refused country gets `billing_country_unsupported` (`deploy/README.md`).

## 3. Webhook setup in each live dashboard

Both URLs are the same ones test mode used. Set each in the dashboard **in live mode** (check the toggle first).

| | Paystack | Flutterwave |
|---|---|---|
| URL | `https://notes.beyrictech.com/api/billing/webhooks/paystack` | `https://notes.beyrictech.com/api/billing/webhooks/flutterwave` |
| Where | Live mode, add the webhook URL on the dashboard (Paystack webhooks page: "Add the webhook URL on your Paystack dashboard"); signing secret under Developers, API configuration | Settings, Webhooks (Flutterwave webhooks page: "Navigate to Webhooks to add your webhook URL. Check all the boxes and save"); choose the **V3** webhook, because the app reads the `verif-hash` header and not v4's header (`deploy/README.md`) |
| Secret | The `whsec_` signing secret from the dashboard goes into `PAYSTACK_WEBHOOK_SECRET`. The header is `Paystack-Signature: t=…,v1=…`; a 300-second window | The **secret hash** you made up goes into the dashboard field and into `FLUTTERWAVE_SECRET_HASH`; it comes back unchanged in the **`verif-hash`** header |
| Events | A standard dashboard webhook URL gets every event; the app acts on `charge.success`, the `refund.*` events and `charge.dispute.*` and answers 200 "ignored" to the rest. (Choosing events per URL is a pilot feature on Paystack's page) | "Check all the boxes"; the app acts on `charge.completed` and the refund notice and ignores the rest. Also tick the refund webhook ("Enable webhook for refunds", refunds page) |
| Retries | Live: every 3 minutes for 4 tries, then hourly for 72 hours; needs a 200 | Turn **webhook retries on** in the dashboard: 3 tries, 30 minutes apart; needs a 200 within 60 seconds |
| Not read on a page | Whether test and live webhook settings are separate screens in the new dashboard: the Paystack page says test and live have separate endpoints for its pilot endpoints; for Flutterwave the page does not say. Look at the screen: it must say live | |

**Proof (no secret needed, safe to run):** the route must answer 401 for an unsigned call, which proves billing is on, the route is reachable through nginx and the secret is required. A 404 means billing is off or the provider is not registered.

```bash
for p in paystack flutterwave; do curl -s -o /dev/null -w "$p %{http_code}\n" -X POST "https://notes.beyrictech.com/api/billing/webhooks/$p" -H 'content-type: application/json' -d '{}'; done
```

Expected: `paystack 401` and `flutterwave 401`. (From `app/billing/api.py`: 404 off, 401 bad signature, nothing stored.) Whether the raw body survives nginx and the Next.js proxy unchanged is proved only by the first real payment's signed event (step 10).

## 4. The order of the switch

Do it in one sitting, on a quiet day, with Adebayo at the terminal. **Restart only when no meeting is live or due.**

| Step | Do | Proof (one) |
|---|---|---|
| **1. Accounts and dashboards ready** | Section 1 done, both webhooks created in live mode (section 3), the secret hash made | The two webhook rows show in the live dashboards |
| **2. Gate check** (a recreate follows; the gate is checked again before the second one) | `bash -c '. deploy/gate.sh; join_due_soon && echo "no join is due"'` and look for meetings in progress: `q <<<"select count(*) from meetings where status in ('bot_sent','processing');"` | Prints `no join is due` and the count is `0`. If a join is due it prints the time and exits 1: wait, then ask again. **Also, before the pause (the admin screen is switched off by it):** `q <<<"select count(*) from refunds where status in ('pending','unknown');"` must print `0`; if not, resolve each open refund in Admin, Billing, Refunds as made or not made first (every payment before the flip is a test payment, so every open refund is a test refund) |
| **3. Pause billing** (so no test row can be made while the closeout runs; **this comes BEFORE the backup and before `T`**) | `cp -p .env .env.before-live-$(date +%Y%m%d-%H%M)`, edit `.env` yourself: `BILLING_ENABLED=false` (nothing else), then `docker compose up -d --force-recreate web worker scheduler`. This stops new checkouts, the billing pages and routes (a plain 404), the webhook route and the billing clock (renewals, retries, the sweeper); hours, recordings and the trial go on as before. People see no billing screens until step 8 | `docker compose exec -T web printenv BILLING_ENABLED </dev/null` prints `false`, and the curl in section 3 answers `404` for both providers (billing off). Then wait: a checkout opened a few minutes ago may still be paid on the provider's page; the closeout refuses to run while a payment made in the last 30 minutes is still pending |
| **4. Backup** | `BACKUP_DIR=~/backups/beyric-notes bash deploy/backup.sh` (the path and form the deploy README's section 7 uses) | A new dump file stamped now: `ls -lt ~/backups/beyric-notes \| head -3`. **It is the only undo** for the closeout |
| **5. Look at what is there** (read-only) | Paste the helper and queries below | The counts are known before anything changes (a record of what the closeout will close) |
| **6. Closeout** (needs PR #255 deployed; **take `T` now, after the pause**) | `T=$(date -u +%Y-%m-%dT%H:%M:%SZ); echo "$T"`. Dry run (changes nothing): `docker compose exec -T web python scripts/billing_close_test_data.py --before "$T" --end-pilot --trial-for-ended-pilot </dev/null`, read the counts against step 5, then apply: `docker compose exec -T web python scripts/billing_close_test_data.py --before "$T" --apply --end-pilot --trial-for-ended-pilot </dev/null; echo "exit $?"`. A line starting `REFUSED` says why and nothing was changed: a payment still pending in the last 30 minutes (wait), a billing row made at or after `T` (billing was not paused: pause it, take a new `T`), a `T` older than 60 minutes (take a new one), or `--trial-for-ended-pilot` needs `TRIAL_SECONDS` above 0 and a daily trial budget (if one is set) big enough for the people owed a trial, or it refuses; a test refund still open (the admin screen is off while billing is paused: un-pause with `BILLING_ENABLED=true` and a recreate, resolve it in Admin, Billing, Refunds, pause again, take a NEW `T`; step 2 counts them before the pause so this should not happen). ``--end-pilot` also refuses while a meeting is being recorded or a join is due (wait, then run again). `FAILED for one person` lines or a deadlock with a refund someone was making: run the same command again, it only does what is left | The last lines say `PROOF: OK` and `exit 0`: zero live test-era subscriptions, zero test cards, zero test packs or plan hours in force, zero pending test checkouts. If it still fails, restore the step 4 backup and tell the lead |
| **7. Edit `.env`: the live settings, in ONE edit** | Edit `.env` yourself and make every change in section 2 together: the live keys and the webhook secret and hash, `BILLING_LIVE=true`, **`BILLING_LIVE_SINCE=<the T from step 6>`** and `BILLING_ENABLED=true` (billing comes back on only here, with live keys) | `grep -E '^(BILLING_LIVE\|BILLING_ENABLED\|PAYSTACK_SECRET_KEY\|FLUTTERWAVE_SECRET_KEY)=' .env \| cut -c1-24` shows `BILLING_LIVE=true`, `BILLING_ENABLED=true`, a `PAYSTACK_SECRET_KEY=sk_live_` prefix and a Flutterwave key with no `_TEST` (do not paste this output into chat) |
| **8. Recreate** (a restart does not re-read `.env`; **ONE recreate for all of step 7**) | Repeat the gate check of step 2, then `docker compose up -d --force-recreate web worker scheduler` | `docker compose ps` shows web, worker and scheduler Up, and `docker compose exec -T web printenv BILLING_LIVE </dev/null` prints `true`, and the same command with `printenv BILLING_LIVE_SINCE` prints your `T`. If web exits, `docker compose logs web --tail 30` names the wrong setting (section 2) |
| **9. Nothing was made in the gap, the webhook route, and the old test webhooks** | Before anyone pays: `q <<<"select (select count(*) from payments where created_at >= '$T') as payments, (select count(*) from subscriptions where created_at >= '$T') as subscriptions, (select count(*) from payment_methods where created_at >= '$T') as cards;"` (it must run in the same terminal as step 6 so `$T` is set), then the curl in section 3. Then **remove the webhook URL from both providers' TEST dashboards** (Paystack and Flutterwave, test mode): a test webhook that arrived after the flip would be a test event hitting the live endpoint | The query prints `0 \| 0 \| 0` (any other number: stop and tell the lead, because a row made after `T` and before the flip is a test row the app now reads as live), and the curl prints `paystack 401`, `flutterwave 401` |
| **10. First real payments** | Section 5, one provider at a time | Section 5's proof |
| **11. Refund each** | Section 6 | Section 6's proof |
| **12. Watch** | Section 8 | the first-hour checks |

The read-only helper and the step 5 queries (they cannot write: the session is read-only):

```bash
q() { docker compose exec -T db sh -c 'PGOPTIONS="-c default_transaction_read_only=on" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1'; }
q <<'SQL'
select 'subscriptions' as what, status, provider, count(*) from subscriptions group by 2,3 order by 2,3;
select 'grants in force' as what, kind, count(*), round(sum(seconds)/3600.0,1) as hours from hour_grants where kind in ('plan_period','pack') and (expires_at is null or expires_at > now()) group by 2;
select 'saved cards' as what, provider, count(*) from payment_methods group by 2;
select 'invoice counters' as what, year, last_number from invoice_counters order by year;
select 'invoices' as what, test_mode, count(*) from invoices group by 2;
SQL
```

Expected before the closeout: counters only under a **negative** year (`-2026`), no positive-year counter row, every invoice `test_mode = t`. A positive-year counter row would mean live numbers were already used: tell the lead.

## 5. The first real payment, per provider (with your own card)

**Needs your explicit yes in chat for each payment: it moves real money.** Use the plan, not the pack: a pack can only be bought with a live plan. The cheapest real payment is the Starter plan.

| | Paystack, naira | Flutterwave, dollars |
|---|---|---|
| Person | fresh account A, billing country **Nigeria** | fresh account B, billing country **any other country** you choose on `/billing/plans` (the screen states the currency before you pay; your Nigerian card will be flagged `country_mismatch`, which is only a note, never a refusal, `app/billing/mismatch.py`) |
| Plan | Starter, 20 hours, **NGN 25,000** | Starter, 20 hours, **USD 18** |
| Click path | sign in as A → `/billing/plans` → confirm country → Starter → pay | sign in as B → same |
| On the provider's page | The amount must read NGN 25,000.00. If it reads anything else, close it and tell the lead | The amount must read USD 18.00 (or tell the lead what it shows). **Whether Flutterwave takes a USD payment from your account at all, and what your bank is charged for a Nigerian card, is not verified** |
| Back in the app | `/billing/return` first says the payment is pending, then paid; `/billing` shows the plan Active and the hours (the trial's leftover plus 20) | same |
| Cost to you | The refund returns the payment; **whether the provider's fee comes back is not on Paystack's refund page or Flutterwave's refund pages (NOT VERIFIED)**. Paystack's local fee is 1.5% + NGN 100 (about NGN 475 on NGN 25,000, pricing page 2026-11-21); Flutterwave's international fee is 4.8% plus 7.5% VAT on the fee (its pricing page; local cards 2%). Expect to lose the fees | |

**Proof for each payment (one query, read-only, uses `q` from section 4):**

```bash
q <<'SQL'
select p.id, p.provider, p.kind, p.currency, p.amount_minor, p.status, p.paid_at is not null as paid, p.card_country, p.country_mismatch, i.number, i.test_mode
from payments p left join invoices i on i.payment_id = p.id order by p.id desc limit 2;
SQL
```

Expected for the Paystack payment: provider `paystack`, kind `plan`, `NGN`, `2500000`, status `succeeded`, `paid` true, invoice number `BN-<year>-000001`, `test_mode` **f**. For the Flutterwave one: `flutterwave`, `USD`, `1800`, `succeeded`, next number `BN-<year>-000002`, `test_mode` f. Then the event proof: `q <<<"select provider, type, signature_ok, processed_at is not null as decided, error from billing_events order by id desc limit 4;"` must show a `charge_succeeded` event with `signature_ok` t and `decided` t for each provider: that is the proof that the signed webhook reached the app through nginx. If the payment shows `succeeded` but there is no event, the sweeper settled it by asking the provider (a few minutes), and the webhook path is NOT proved: tell the lead.

What to check in the screens: the receipt mail arrives with the invoice PDF (`BN-<year>-000001`, seller name, address and registration number, no tax ID line, no VAT line, Lagos date); `/billing` → Invoices lists it; `/admin/billing` shows the payment with the refund button (provider names appear only on admin screens).

**Not provable live now:** the saved-card **renewal** (it needs a month to pass; `make-due` refuses live). The first real renewal is the proof; watch it (section 8).

## 6. The first refund

Why this account: the admin refund screen allows "first plan payment, within 7 days, none of its hours used" only for a person's earliest plan payment ever (`app/billing/refunds.py:120-136`). Do not record a meeting on account A or B before refunding: used hours block the refund.

1. Sign in as admin → `/admin/billing` → the payment's row → **Refund** (the one case the row offers: first plan payment).
2. The refund ends the plan, removes its hours and deletes the saved card.

**Proof (one query):**

```bash
q <<'SQL'
select r.id, r.reason, r.status, r.amount_minor, r.provider_refund_id is not null as has_provider_id, p.status as payment_status, s.status as sub_status
from refunds r join payments p on p.id = r.payment_id left join subscriptions s on s.id = p.subscription_id order by r.id desc limit 2;
SQL
```

Expected: reason `first_plan_7d`, refund status `submitted`, `has_provider_id` true, payment status `refunded`, subscription `ended`. Then the dashboards: the refund shows in each provider's own dashboard, and the money reaches your card later: Paystack says a processed refund "may still take up to 10 business days" (refunds page); Flutterwave says card refunds complete "within 3-15 days after initiation" (refunds page). **Proof that the money is back is your bank statement**, not the app.

If the refund shows status `unknown` (the call got no answer), do not press anything twice: the admin screen tells you to look in the provider's dashboard and say what you see (`resolve`), because Flutterwave v3 has no idempotency key and a second refund can pay twice.

Paystack refund states you may meet on the dashboard: `pending`, `processing`, `needs-attention` (it needs the customer's bank details; the app does not handle this one, do it in the dashboard), `failed`, `processed` (Paystack refunds page).

## 7. Invoice numbering and test-era invoices

**Numbering starts at `BN-<year>-000001` for live.** Proved by the code, not yet live: test invoices were counted under a negative year in `invoice_counters` (`app/billing/invoices.py:87-102`), live ones under the positive year, so the first live invoice is `BN-2026-000001` (the year is Lagos time; a payment after 1 January 2027 starts `BN-2027-000001`). The step 5 query shows the state before; the section 5 query shows the first live number. **Your two refunded payments use up numbers 000001 and 000002**, so your first real customer is `BN-<year>-000003`. A refunded invoice stays on file with its REFUNDED notice; nothing is deleted. Whether the accountant wants those two voided differently is question 3.

**Test-era invoices: only live invoices are shown once live (built, PR #255).** Test-era invoices had numbers `BN-2026-9000NN` for payments where no money moved (`docs/sop/2026-11-20-remove-test-mode-markers.md`). With `BILLING_LIVE_SINCE` set, the person's invoice list and PDF route (a plain 404), the payments list and the first-payment refund rule ignore every payment made before it. Admins keep seeing everything, and a test-era payment offers no refund (409 `before_go_live`).

## 8. After go-live: what to watch

| When | Check | How |
|---|---|---|
| **First hour** | Every webhook arrives signed and is decided | `q <<<"select provider, type, signature_ok, processed_at is not null as decided, error is not null as err from billing_events order by id desc limit 10;"` : no `signature_ok` false, no `decided` false older than 5 minutes |
| | No error loop in the logs | `docker compose logs worker scheduler --since 1h \| grep -i -E 'billing (due\|sweep) pass failed\|not issued\|no known outcome'` prints nothing |
| | Admin alerts (the bell) | none of: "An invoice could not be issued", "A payment was refused by the settlement rules", "Payments have waited more than a day" |
| | Provider dashboards | the live transactions list shows exactly your two payments |
| **First day** | Receipt mails sent | `q <<<"select template, status, count(*) from mail_outbox where template = 'billing_receipt' group by 1,2;"` |
| | Real people's payments | each real payment has a `succeeded` row, an invoice and a decided event; a payment `pending` for more than 5 minutes is a webhook problem |
| | First settlement | Paystack pays out the next working day; Flutterwave local 1 business day, international 5 (pages above). Confirm the money lands in the right account and currency |
| | Trial cost | sign-up is open and each new account gets 3 free hours with no daily cap (`TRIAL_DAILY_BUDGET_SECONDS` unset): `docker compose exec -T web python scripts/trial_report.py </dev/null` |
| **First renewal** (about a month later) | The saved-card charge works live. NOT VERIFIED until then | The same two queries; the renewal payment has `kind` `renewal`; a failed one opens a 3-day grace and mails the person |

## 9. Rollback

Choose by what has happened. **Never switch back to test keys once a real customer has paid**: their saved live cards and live subscriptions would be sent to the test API and fail the same way test rows fail after the flip (audit, first section), and a real payment made in between would be asked about on the wrong side and never settled.

| You want | Do | What happens to data and to a payment made in between |
|---|---|---|
| **Pause new purchases only**, keep renewals and webhooks running | `BILLING_COUNTRY_ROUTES=NG=off,*=off`, recreate web, worker and scheduler | New checkouts are refused (`billing_country_unsupported`). Renewals still run on the route each plan was sold on (`app/billing/renewals.py:444-454` treats an unroutable country as "not moved"). A payment already started settles normally. **Safest pause** |
| **Pause everything** | `BILLING_ENABLED=false`, recreate | Billing pages and routes answer 404 and the billing clock stops (no renewals, retries, lapses or sweeper, `app/scheduler.py:270`). Hours and gates keep working from the rows (`entitlements` does not read this flag). A payment made meanwhile: its webhook gets a 404, and the provider retries (Paystack live: 4 tries 3 minutes apart then hourly for 72 hours; Flutterwave: 3 tries 30 minutes apart if retries are on). When you switch back on, the sweeper asks the provider by reference for payments up to 3 days old and settles them; older ones need the provider's dashboard and a manual refund |
| **Back to test keys** | Only before the first real customer: put test keys, `BILLING_LIVE=false`, recreate | No data is lost. Anything paid live in between is never settled; refund it in the provider's dashboard by hand |
| **A bad key or secret** | Fix `.env`, recreate | Startup refuses wrong-kind keys; a wrong webhook secret gives 401s and the sweeper settles payments within minutes |

After any rollback: `docker compose ps`, then the section 8 first-hour checks.

## 10. Provenance and what is NOT verified live

Provider pages read on **2026-11-21**. Paystack pages: read by me in the in-app browser (Paystack answers automated fetches with 403, but they read fine in the browser). Flutterwave pages: read through the web-fetch agent (two runs), not by me directly.

| Page | Read | Used for |
|---|---|---|
| https://paystack.com/docs/payments/webhooks/ | yes | signature headers, 300 seconds, retries, IP list, signing secret location, events |
| https://paystack.com/docs/payments/refunds/ | yes | refund API, own reference, states, up to 10 business days |
| https://paystack.com/pricing | yes | fees, T+1, USD settlement pilot |
| https://paystack.com/docs/payments/test-payments/ | yes | test cards |
| https://paystack.com/docs/api/ | yes | "test and live environments, each with their own set of API keys"; the page did not show key prefixes |
| https://support.paystack.com/en/articles/2125506 (How to activate your business) | yes | go-live steps, corporate account |
| https://support.paystack.com/en/articles/2123970 (Compliance requirements for Nigeria) | yes | company documents, Tax Identification Number |
| https://support.paystack.com/en/articles/6543554 (Activation status) | yes | Pre-approved, Awaiting Review, Approved, Needs Attention |
| https://support.paystack.com/en/articles/2129922 (Test Mode and Live Mode) | yes | the Live toggle, separate data per mode |
| https://developer.flutterwave.com/docs/authentication | agent | keys location, `_TEST` prefix, mode toggle |
| https://developer.flutterwave.com/docs/webhooks | agent | `verif-hash`, 200, 60 seconds, 3 retries 30 minutes apart |
| https://developer.flutterwave.com/docs/payment-operations/refunds | agent | refund API, partial refunds, minimum NGN 100, 3-15 days |
| https://developer.flutterwave.com/docs/payment-operations/settlements | agent | approved accounts only, bank details in settings |
| https://developer.flutterwave.com/docs/quickstart | agent | "Live payments stay disabled until you complete KYC" |
| https://developer.flutterwave.com/docs/testing | agent | test mode only for test data, test receipts to the business email |
| https://developer.flutterwave.com/docs/recurring-payments/card-tokenization | agent | tokens valid one year, tied to the email |
| https://flutterwave.com/ng/pricing | agent | local 2%, international 4.8%, VAT on fees, local-currency settlement default |
| https://flutterwave.com/ng/support/my-account/onboarding-requirements-for-using-flutterwave-in-nigeria | agent | company documents |
| https://flutterwave.com/ng/support/my-account/how-to-successfully-verify-your-business-on-flutterwave | agent | originals only, director verification |
| https://flutterwave.com/ng/support/my-account/refunding-customers | agent | dashboard refund path, 3-15 business days |
| https://flutterwave.com/ng/support/payments/settlement-in-different-currencies | agent | Naira cards settle in Naira, payout balance for other currencies |
| https://flutterwave.com/ng/support/general/what-are-the-currencies-accepted-on-flutterwave | agent | USD listed for card payments |

Could not read: https://developer.flutterwave.com/docs/going-live and https://developer.flutterwave.com/docs/api-keys (both 404). No page I read gives Flutterwave's approval time, how to choose events, whether test and live webhook settings are separate, the key format of a live secret key, whether fees come back on a refund, or whether a Nigerian merchant can charge in USD. No Paystack page I read gives live key prefixes, the exact dashboard menu for live keys, or whether fees come back on a refund.

| Behaviour | Status |
|---|---|
| Startup refuses wrong-kind keys with the quoted messages | verified in code and by the existing tests (not run live) |
| 401 from the webhook route for an unsigned call | verified in code (`app/billing/api.py`); not curled today |
| A signed live webhook reaches the app unchanged through nginx and Next.js | **not verified live** (proved by step 10 of section 4 / section 5 proof) |
| Live checkout, verify and refund calls on both providers | **not verified live** (test mode only) |
| Flutterwave accepts USD from this account, and what it settles | **not verified** (spec 5.6 still open) |
| Saved-card renewal live, both providers | **not verified live** (needs a month) |
| What Paystack answers to a test card token on the live API, and what Flutterwave answers (HTTP 400 per the adapter's note) | **not verified**; the closeout makes it moot |
| Whether fees return on a refund | **not verified** (neither provider's refund pages say) |
| The numbering starts at `BN-<year>-000001` | verified in code and by tests; not yet seen on a live row |
| The closeout script, `BILLING_LIVE_SINCE` filter | built and tested on a scratch Postgres (PR #255); **not yet run on the production database**: the dry run in step 6 is its first real run |

## 11. Follow-ups

| Item | Size |
|---|---|
| Closeout script and `BILLING_LIVE_SINCE` filter | done (PR #255) |
| A read-only live `proof` and `preflight` for `scripts/billing_test_purchase.py` | half a day |
| Fix the stale line in `deploy/README.md:127` about a "not a tax document" test invoice | minutes |
| Terms paid-plans section and Privacy entries for Paystack and Flutterwave (lawyer; does not block) | lawyer |
| Handle Paystack's `refund.needs-attention` state in the app | small, after the first live refund shows how often it occurs |
