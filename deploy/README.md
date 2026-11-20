# Deploying Beyric Note Taker

One Ubuntu 24.04 server runs everything. It may be shared with other apps (the first server is),
so nothing here takes a public port except through nginx.

| Piece | Where it runs | Reachable from |
|---|---|---|
| Front door: `next` (Next.js) | Docker Compose in this repo | `127.0.0.1:8020` only; this is the port nginx proxies to |
| App: `db`, `migrate`, `worker`, `web` (FastAPI) | Docker Compose in this repo | `web` on `127.0.0.1:8021` only (operator and rollback); visitors reach it through `next` |
| nginx + certbot | **on the host**, not in Docker | the internet, ports 80 and 443 |

- Bots are not run here: they are Skribby's (hosted), reached over the internet from `web`, `worker`
  and `scheduler`. The self-hosted Vexa this server once ran was removed on 2026-11-20 (section 8,
  "After Vexa was removed from the repo").
- Three networks: `default` (web, worker, scheduler; has internet), `appdb` (the app database; no
  internet) and `front` (`next` and `web` only; pinned to `172.28.1.0/24`, so check the server has no
  other network on it: `docker network inspect $(docker network ls -q) | grep Subnet`).
- nginx connects to `127.0.0.1:8020`, which is `next`, and `next` connects to FastAPI as
  `http://web:8000`. `next` renders the few pages it owns (`/privacy`, `/terms` and some review
  pages) and proxies every other path to FastAPI unchanged, so the Jinja pages, `/health` and the
  login keep working as before. The visitor's address reaches FastAPI like this: nginx sets
  `X-Forwarded-For` (the address Cloudflare reports) and a secret header `X-Edge-Secret` on every
  request, `next` passes both on unchanged, and FastAPI believes the forwarded address **only on a
  request that carries the right secret** (`EDGE_SHARED_SECRET`, below). It never trusts a proxy
  by its IP address: Docker rewrites every container's and the host loopback's connection to the
  bridge gateway, so any container on the server could pose as nginx. Without the secret a request
  is identified by its socket peer, so a container that forges `X-Forwarded-For` cannot choose the
  address the login limit counts (`uvicorn` runs with `--no-proxy-headers` for this reason). Only
  processes on the server can reach ports 8020 and 8021; none of them can read the secret.
- `next` is capped at 512 MB of memory (`mem_limit`) and holds no secrets. Measured on a Mac it
  used 37 MB idle and 65 MB at peak under 240 requests, so the cap is a ceiling, not a need.
  Building its image (`next build`) peaked at about 0.8 GB for its largest process, once per
  deploy; measure on the server (SOP `2026-11-02-nextjs-scaffold.md`).
- Minimum server: **2 vCPUs** and room for the containers' own memory limits (`web` 768 MB, `next`
  512 MB, `scheduler` 256 MB, plus `db` and `worker`). The earlier 4 GB figure counted Vexa, which no
  longer runs here.

Paths below: the app lives in `/opt/beyric-note-taker` (like `/opt/haven`), backups in `~/backups/beyric-notes`, and the
domain is `notes.beyrictech.com`. Steps marked **(sudo)** need the server owner's password.

## 0. What the owner must supply

- [ ] SSH access as a user in the `docker` group, and someone who can run `sudo`.
- [ ] A DNS `A` record for the domain pointing at the server. Proxied through Cloudflare (orange
      cloud) is supported: `deploy/nginx.conf` restores each visitor's real address from
      `CF-Connecting-IP`. Set Cloudflare's SSL/TLS mode to **Full (strict)**; "Flexible" would
      loop forever against the HTTP-to-HTTPS redirect certbot adds.
- [ ] An email address for Let's Encrypt expiry notices.
- [ ] An OpenAI API key with a spend limit set in the OpenAI dashboard.
- [ ] The owner's login password for the app.

## 1. Ports: check from outside

Only 22, 80 and 443 should answer. Docker bypasses `ufw` for published ports, so the real guard
is that `compose.yaml` publishes nothing on a public interface. Check from **your laptop**:

```bash
for p in 22 80 443 8020 8021 18056 8056 3001 8100 5432; do
  printf '%s: ' "$p"; nc -z -G 3 SERVER_IP "$p" && echo open || echo closed
done
```

Expected: 22, 80, 443 open; the rest closed. (Another app's port may show open; that is that
app's to fix, not this one's.) Run it again in step 9.

## 2. Server preparation

Docker, the compose plugin, nginx and certbot must be installed. Then:

```bash
ls -l ~/.claude/.credentials.json    # expected: "No such file or directory"
```

### Read access to the repository

The server pulls the code with its own read-only deploy key.

```bash
ssh-keygen -t ed25519 -N '' -C beyric-notes-server -f ~/.ssh/beyric_notes_github
cat >> ~/.ssh/config <<'EOF'
Host github-beyric-notes
    HostName github.com
    User git
    IdentityFile ~/.ssh/beyric_notes_github
    IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
cat ~/.ssh/beyric_notes_github.pub
```

Add that public key to the GitHub repository as a **read-only deploy key**
(`gh repo deploy-key add <file> --repo innocent98/beyric-note-taker --title server`), then:

```bash
sudo mkdir -p /opt/beyric-note-taker && sudo chown "$USER": /opt/beyric-note-taker    # (sudo)
git clone git@github-beyric-notes:innocent98/beyric-note-taker.git /opt/beyric-note-taker
```

## 3. Secrets: `.env`

The file lives only on the server, is git-ignored, and is never overwritten by a deploy.

```bash
cd /opt/beyric-note-taker
umask 077
cp .env.example .env
```

Edit `.env`. Every secret and password the app or compose needs is **empty** in `.env.example` on purpose: the app and compose refuse to start until each one is filled in (`tests/test_clean_env.py` proves it), so a copied file can never run with a published value. `.env.example` is laid out in sections (`# ---- Core ----`, `# ---- Billing ----`, ...) and holds only the settings production sets; every other setting, with its default and what it does, has a row below (add the line to `.env` only to change it). On a server whose `.env` grew over time, section 8 "Cleaning the server `.env`" rewrites it into this layout.


| Variable | Value |
|---|---|
| `POSTGRES_PASSWORD` | `openssl rand -hex 24` |
| `DATABASE_URL` | not in `.env.example`: compose sets it for the containers; set it only to run the app outside Docker (`DATABASE_URL=postgresql+psycopg://postgres:<password>@localhost:5432/notetaker`) |
| `SKRIBBY_API_KEY`, `SKRIBBY_ACCOUNT_ID`, `SKRIBBY_ACCOUNT_NAME`, `SKRIBBY_REGION`, `SKRIBBY_BOT_NAME`, `SKRIBBY_TRANSCRIPTION_MODEL`, `SKRIBBY_DOWNLOAD_HOSTS` | `SKRIBBY_API_KEY` is **required**: Skribby is the bot provider (see `docs/sop/2026-11-08-skribby-provider.md`), and web, worker and scheduler refuse to start without it, naming the setting. The rest are optional. Key from Skribby's dashboard; account id is the Authenticated Account of the signed-in bot (leave out for a guest); region `eu` (default) or `jp` (do not change it while Skribby meetings are open: the app logs an error and refuses Skribby operations on those meetings until it is set back); download hosts only if the live check shows recordings on a host other than Skribby's or Amazon's. Keep the key in the server `.env` only |
| `TRANSCRIPT_SOURCE`, `TRANSCRIPT_SHADOW_PERCENT` | Optional; default `own` and `0` (today's behaviour). `TRANSCRIPT_SOURCE=skribby` makes a Skribby meeting's notes from Skribby's transcript (falling back to our own transcription when it is missing or under 20 words a minute of audio); a bad value, or `skribby` with a `SKRIBBY_TRANSCRIPTION_MODEL` other than `elevenlabs/scribe-v2` (a model with speaker names; the default Groq model has none), stops web, worker and scheduler at startup naming the setting. `TRANSCRIPT_SHADOW_PERCENT` (0 to 100) also runs our transcription on that share of meetings to compare numbers only. Flip by editing `.env` and recreating web, worker and scheduler (`docker compose up -d --force-recreate web worker scheduler`; a restart does not re-read `.env`; guarded steps in `docs/sop/2026-11-16-skribby-name-sole-candidate.md`); read the result with `docker compose exec -T web python scripts/transcript_shadow_report.py`. See `docs/sop/2026-11-09-transcript-source-switch.md` |
| `NAME_BY_MEET_TIMELINE` | Optional; default `true`. A speaker tag Skribby did not name (and elimination and the sole-candidate rule could not) takes the name of the participant Meet's own speaking indicator shows speaking most over it, only on a clear margin (at least 30 s, a fifth of the tag and 3 times the runner-up), after a clock check, and in a meeting where that indicator agrees with the names Skribby gave; the saved evidence says `meet_timeline` for each. Costs one extra request to Skribby per finished meeting (kept in memory per process). `false` turns it off (such a tag stays `Unknown speaker`). Independent of `SKRIBBY_NAME_BY_ELIMINATION`: with elimination off the timeline may still give a tag the name elimination would have given when Meet agrees; to stop all derived names turn both off. A value that is not `true` or `false` stops web, worker and scheduler at startup. Recreate web, worker and scheduler to change it (`docker compose up -d --force-recreate web worker scheduler`). See `docs/sop/2026-11-17-name-by-meet-timeline.md` |
| `SPEAKER_LINE_CORRECTION` | Optional; default `true` (leave unset). With `TRANSCRIPT_SOURCE=skribby`, a line moves to the person Meet's own speaking timeline shows speaking over it when **exactly one person was lit** (at least 1 s, and no runner-up with a quarter of their seconds) and that person is not the one its tag says; never when two or more are lit, nobody is lit, the segment has no times or tag, or the target is the note taker; only after the same clock check the naming uses, and only for a meeting whose speaker events were kept (Teams, Zoom and a failed fetch change nothing). The evidence marks moved lines (`corrected_to`) and the worker log says `speaker correction moved X of Y segments`. It never stops a meeting. **This is the one emergency switch, not a rollout dial:** `false` leaves every line under its tag's name (meetings already corrected stay as they are until restored with the rebuild script's `--restore`; the rebuild script then builds without it). A value that is not `true` or `false` stops web, worker and scheduler at startup. Recreate web, worker and scheduler to change it (`docker compose up -d --force-recreate web worker scheduler`). See `docs/sop/2026-11-19-line-level-speaker-correction.md` |
| `PREFER_ORGANIZER_BOT` | Optional; default `true` (leave unset). When several accounts hold the same calendar event with the same meeting link, the account that **organizes** it gets its bot in on time and every other account's join runner waits `ORGANIZER_BOT_WAIT_SECONDS` (default `120`, 0 to 600) after its run time before it places; if a bot for that meeting code is then already in or reserved it stays out, as before, and its owner is told that someone else's note taker is there. A guest waits **only while** the organizer has an account here, a live join for the event, a good calendar connection, an auto-join rule and event that still say join, and the means to record (hours, a plan or a trial, and a free bot slot of their own); a lone guest, an organizer with no account, auto-join off, no hours or no plan never causes a wait, and the wait always ends at the run time plus the wait, so a meeting cannot go unrecorded because of it. Hours are charged to the account whose bot recorded (unchanged). **This is the one emergency switch, not a rollout dial:** `false` goes back to the first placed bot recording. A value that is not `true` or `false`, or a wait outside 0 to 600, stops web, worker and scheduler at startup. Recreate web, worker and scheduler to change either (`docker compose up -d --force-recreate web worker scheduler`). No migration. See `docs/sop/2026-11-19-organizer-bot-preferred.md` |
| `HOURS_MODE` | Optional; default `off` (only the old per-account cap rule runs). `shadow` keeps that rule deciding and also asks the new grants-based rule (`app/entitlements.py`) on every recording start, logging one `hours_shadow` line when the two disagree; it changes nothing for anyone. `enforce` (billing stage S2) makes the grants-based rule decide a recording start (403 `hours_cap_reached` with a `reason`), splits every counted recording across the person's grants, and raises the 80% and 100% notices with a mail; **do not switch it on before the steps in `docs/sop/2026-11-16-hours-enforce.md` ("Before switching on"): accounts made after migration 0049 hold no hours and would be refused**. `shadow` also splits counted recordings (silently). A bad value stops web, worker and scheduler at startup naming the setting. Needs migration 0049 applied first. Read the comparison with `docker compose exec -T web python scripts/hours_shadow_report.py </dev/null` (read-only; no names or emails). Before `enforce`, run `docker compose exec -T web python scripts/hours_reconcile_backlog.py </dev/null` (a dry run), then again with `--apply`. Change by editing `.env` and recreating web, worker and scheduler. See `docs/sop/2026-11-16-hours-model-shadow.md` and `docs/sop/2026-11-16-hours-enforce.md` |
| `TRIAL_SECONDS`, `TRIAL_DAYS`, `TRIAL_DAILY_BUDGET_SECONDS` | Optional (billing stage S10). `TRIAL_SECONDS` (default `0` = no trial; `10800` = 3 hours) is the size of the one-time trial a person is given when their email is first confirmed (password link or Google); it ends at the hours used or `TRIAL_DAYS` days (default `14`) after it is given. It is given in any `HOURS_MODE` (recommended value `10800`), once per person and per email fingerprint (deleting an account and signing up again gives nothing), and the scheduler's `trial_sweep` job (every minute, only registered while `TRIAL_SECONDS` > 0) gives one to every confirmed account that holds none: that is what closes the S2 "accounts made after 0049 hold no hours" gap. `TRIAL_DAILY_BUDGET_SECONDS` (default `0` = no cap) limits the trial seconds given in any 24 hours; it must not be smaller than `TRIAL_SECONDS` (the process stops at startup). `max_accounts` stays the other brake. Read what a setting would do first: `docker compose exec -T web python scripts/trial_report.py </dev/null` (read-only, counts only). Change by editing `.env` and recreating web, worker and scheduler. See `docs/sop/2026-11-17-trial-and-pilot-expiry.md` |
| `PAID_PLANS_OPEN_ON`, `PILOT_EXPIRY_DAYS`, `PILOT_EXPIRY_NOTICE_DAYS`, `PILOT_EXPIRY_MIN_NOTICE_DAYS` | Optional (billing stage S10). `PAID_PLANS_OPEN_ON` (a date, `2027-02-01`; unset by default = **nothing ever expires**) is the day paid plans open: every pilot grant then ends `PILOT_EXPIRY_DAYS` (default `60`) days later, at the end of that day UTC, and each confirmed person with pilot hours left gets one mail and one bell entry `PILOT_EXPIRY_NOTICE_DAYS` (default `14`) days before, and nobody's hours end sooner than `PILOT_EXPIRY_MIN_NOTICE_DAYS` (default `7`) after their mail (the notice days must be at least that, below `PILOT_EXPIRY_DAYS` and at most 60; the process refuses to start otherwise). The scheduler's `pilot_expiry` job (hourly, only registered while the date is set) does both; it only acts under `HOURS_MODE=enforce`. **Needs Adebayo's and the lawyer's confirmation of the mail text before it is set** (`docs/sop/2026-11-17-trial-and-pilot-expiry.md`). If the end is already past and some pilot hours were never given an end, nothing expires and a warning is logged. Recreate the scheduler to change it |
| `FEATURE_GATES_MODE` | Optional; default `off` (every feature stays open and nothing extra is read). Billing stage S3. `shadow` keeps Ask Beyric, new translations (also the translation behind sending notes in French or Spanish) and rewriting notes with a template open, and logs one `feature_gates_shadow would have refused feature=.. user=.. state=..` line (ids only) each time a person without a live plan or trial would have been refused; it changes nothing for anyone. `enforce` refuses them with 403 `plan_inactive`; reading, search, export, tasks, decisions and the Review spelling step stay open. **Do not switch `enforce` on before go-live (S11)**: the pilot accounts have these features today and would lose them. A bad value stops web, worker and scheduler at startup naming the setting. No migration. Change by editing `.env` and recreating web, worker and scheduler. See `docs/sop/2026-11-17-feature-gates-shadow.md` |
| `BILLING_ENABLED` | Optional; default `false`. While false, `POST /api/billing/webhooks/{provider}` answers 404 and nothing is stored. Paystack (S6) and Flutterwave (S7) are built and need their keys (below), so `true` accepts a webhook only for a provider whose keys and webhook secret are set; since stage S5 `true` also turns on the billing profile routes (`/api/billing/profile`, `/api/billing/profile/country`, `/api/billing/route`), and since stage S8 the subscription routes (`/api/billing/plans`, `/subscription`, `/checkout`, `/payments/{reference}`, `/subscription/cancel|resume|change-plan|pay-link`) and the **billing clock**, a job of the scheduler process (renewals, retries, grace, lapse, the sweeper). Needs migrations 0050 to 0053 applied first, and `TOKEN_ENC_KEY` (saved cards are encrypted with it). A bad value stops web, worker and scheduler at startup naming the setting. Change by editing `.env` and recreating web, worker and scheduler. See `docs/sop/2026-11-16-billing-provider-layer.md` and `docs/sop/2026-11-17-billing-profile-routing.md` |
| `BILLING_RETURN_URL` | Optional (billing stage S8). Where a provider sends the payer back after the hosted checkout. Empty (default) means `PUBLIC_BASE_URL` + `/billing/return`. The page only shows the payment's state: nothing is granted there. Change by editing `.env` and recreating web, worker and scheduler. See `docs/sop/2026-11-17-billing-subscriptions.md` |
| `BILLING_SELLER_NAME`, `BILLING_SELLER_ADDRESS`, `BILLING_SELLER_REGISTRATION_NO`, `BILLING_SELLER_TAX_ID`, `BILLING_SELLER_EMAIL`, `BILLING_VAT_RATE_BP` | Optional (billing stage S9, invoices). The seller's details copied onto each invoice when it is issued (name default `Beyrichq LTD`; the rest empty by default: an empty line is left off the invoice, **nothing is invented**; the registration and tax numbers must come from Adebayo and the accountant). `BILLING_VAT_RATE_BP` is the VAT **included** in the price (750 = 7.5%), default 0 (no VAT line); a VAT added on top is not built. Needs migration 0053. A test-mode invoice (`BILLING_LIVE` false) says it is not a tax document. Change by editing `.env` and recreating web, worker and scheduler. See `docs/sop/2026-11-17-billing-invoices-refunds.md` |
| `BILLING_COUNTRY_ROUTES`, `BILLING_ALLOWED_EMAILS` | Optional (billing stage S5). `BILLING_COUNTRY_ROUTES`: which currency and provider each billing country pays with: `COUNTRY=CURRENCY:provider` or `COUNTRY=off` entries, `*` for every country with no entry of its own (a country with an entry never falls through to `*`); default `NG=NGN:paystack,*=USD:flutterwave`. **To switch a provider off write `off`:** `NG=off,*=USD:flutterwave` refuses Nigeria (Paystack off), `NG=NGN:paystack,*=off` refuses everyone else (Flutterwave off); a refused country gets `billing_country_unsupported`. **Deleting the `NG` entry does not switch Paystack off**: Nigeria then takes the `*` route, dollars through Flutterwave. Only `paystack` (NGN) and `flutterwave` (USD) are accepted: a typo or a wrong pairing stops web, worker and scheduler at startup naming the setting; a blank line means the default. `BILLING_ALLOWED_EMAILS`: comma-separated emails that may use the billing profile routes while `BILLING_ENABLED=true`; **closed by default** (empty means no account), `*` means every account; anyone else gets the same 404 as billing off. Change by editing `.env` and recreating web, worker and scheduler. See `docs/sop/2026-11-17-billing-profile-routing.md` |
| `BILLING_LIVE`, `PAYSTACK_SECRET_KEY`, `PAYSTACK_PUBLIC_KEY`, `PAYSTACK_WEBHOOK_SECRET` | Optional (billing stage S6, Paystack, naira). **Keys live only in the server's `.env`: never in git, chat, logs or a PR.** All unset by default: with no secret key the adapter cannot make any call and every Paystack webhook is a 401. `BILLING_LIVE` (default `false`) decides which kind of key the process may hold: `false` accepts only `sk_test_` / `pk_test_` keys (no real money), `true` only `sk_live_` / `pk_live_`; a key of the other kind stops web, worker and scheduler at startup naming the setting (never printing the key). `PAYSTACK_WEBHOOK_SECRET` is the `whsec_...` signing secret (Paystack dashboard, Developers > API configuration; test and live modes each have their own): once set, the timestamped `Paystack-Signature` header is required (300-second window; two signatures are accepted while Paystack rotates a secret) and the older `x-paystack-signature` header alone is refused; unset, the older header (signed with the secret key) is used. The webhook address to set in the Paystack dashboard, for each mode, is `https://notes.beyrictech.com/api/billing/webhooks/paystack`. **Not verified live:** that the raw body arrives unchanged through nginx and the Next.js proxy (the runbook in `docs/sop/2026-11-17-billing-paystack-adapter.md` is the test, with `scripts/paystack_test_run.py`). Change by editing `.env` and recreating web, worker and scheduler. See `docs/sop/2026-11-17-billing-paystack-adapter.md` |
| `FLUTTERWAVE_SECRET_KEY`, `FLUTTERWAVE_SECRET_HASH`, `FLUTTERWAVE_PUBLIC_KEY` | Optional (billing stage S7, the Flutterwave adapter for US dollars). **Keys live only in the server's `.env`: never in git, chat, logs or a PR.** All unset by default: with no secret key the adapter cannot make any call (nothing leaves the process) and every Flutterwave webhook is a 401. `BILLING_LIVE` (row above) decides which kind of key the process may hold: `false` accepts only a Flutterwave TEST key (its text contains `_TEST`, as Flutterwave's authentication page states), `true` refuses a key that contains `_TEST`; a key of the wrong kind stops web, worker and scheduler at startup naming the setting (never the key). What a live key looks like is not documented, so nothing else is checked. `FLUTTERWAVE_SECRET_HASH`: the value you type into the dashboard's Webhooks page (choose the **V3** webhook; Flutterwave returns it unchanged in the `verif-hash` header); empty refuses every webhook; set without the secret key it stops the process at start. `FLUTTERWAVE_PUBLIC_KEY`: accepted, not read. The webhook URL is `https://<host>/api/billing/webhooks/flutterwave` and needs `BILLING_ENABLED=true`. Change by editing `.env` and recreating web, worker and scheduler. Run the test-mode steps in `docs/sop/2026-11-17-billing-flutterwave-adapter.md` before any live key. See that SOP |
| `BOT_INVITE_EMAIL` | Optional, default empty. The Google address of the signed-in Skribby bot (`SKRIBBY_ACCOUNT_ID`), for example `note@beyrictech.com`. When it is set and `SKRIBBY_ACCOUNT_ID` is set, Settings and a waiting or not-admitted Google Meet meeting page show "Add this address to your event to skip the wait" with a Copy button (a signed-in bot on the event's guest list skips the waiting room). Empty, or a guest bot: no tip. One plain email address, or the app refuses to start naming this setting. Restart `web`, `worker` and `scheduler` after changing it. A public address, not a secret. See `docs/sop/2026-11-09-bot-invite-tip.md` |
| `BOT_NAME_APPLIES_WHEN_SIGNED_IN` | Optional, default `false`. Each person can set their own note taker name in Settings (empty = `SKRIBBY_BOT_NAME`); it is sent to Skribby for every meeting. **Live evidence 2026-11-14 (the owner's real Meet call): the signed-in Google Meet bot (`SKRIBBY_ACCOUNT_ID`) shows the Google account's own name in the waiting list, the participant list and as the chat sender, not `SKRIBBY_BOT_NAME`**, so Skribby ignores `bot_name` for it. Set `SKRIBBY_ACCOUNT_NAME` to that account's real name: Settings then says "On Google Meet the note taker always shows its own name, ...", and the "Admit '...'" line and the chat notice name it. A custom name shows on Zoom and Teams (guest bots; not yet seen live). Leave this `false`; set `true` only if a later live Meet call shows a custom name (`docs/sop/2026-11-14-per-user-bot-name.md`); restart `web`, `worker` and `scheduler` |
| `BOT_CHAT_NOTICE_ENABLED` | Optional, default `false`. When `true`, the bot posts one line in the meeting chat when it joins: "Hi, I'm <bot name>. <owner> added me to record this meeting and write notes. If you'd rather not be recorded, please tell <owner> before we start." (the bot name is the name it joins with, the owner is the recorder's display name, "the host" when there is none). Off until a real meeting on each platform has shown it works: Skribby's page only says the parameter exists. If Skribby refuses a send that carries it, the app sends once more without it and logs a warning, so a bad parameter cannot stop a bot. Turn off by setting `false` or removing it; restart `web`, `worker` and `scheduler`. See `docs/sop/2026-11-14-bot-chat-notice.md` for how to prove it live |
| `OPENAI_API_KEY` | the OpenAI key |
| `OWNER_EMAIL`, `OWNER_PASSWORD` | The admin account's email and a long password. Migration `0006` creates the account from them (once; later changes to these two do nothing), and the sign-in is then email plus password. **Both must be in `.env` before the first deploy that contains migration `0006`**, or that migration fails and rolls back. `OWNER_PASSWORD` can be removed after it has run |
| `SESSION_SECRET` | `openssl rand -hex 48`. The app refuses to start if it is shorter than 32 characters (it is the only key behind every session cookie) |
| `APP_ENV`, `COOKIE_SECURE` | `production`, `true` |
| `EDGE_SHARED_SECRET` | `openssl rand -hex 32`, the **same value** nginx sends as `X-Edge-Secret` (section 6, "Edge secret"). Empty = not configured: the app starts and logs a warning, and every client is limited as the proxy's address (one shared login limit for all visitors, so set it). `X-Forwarded-For` is never believed without it |
| `BOT_LEFT_ALONE_SECONDS` | Optional, default `300`. How long the bot stays in a call after the last other person stops speaking before it leaves on its own (sent to Skribby as `last_person_detection`, in whole minutes rounded up; `0` sends nothing so Skribby's own default applies). **It is a silence rule, not a headcount**: a short window can cut a quiet meeting (a long screen-share with no talking, a reading break), and the notes then cover only what the bot heard. Change it by editing `.env` and recreating `web` and `worker` (`docker compose up -d --force-recreate web worker`, with no meeting `bot_sent` or `processing`): no image rebuild, no code deploy. It applies to bots sent after that; a bot already in a call keeps the window it was sent with |
| `PUBLIC_BASE_URL`, `MAIL_PROVIDER`, `RESEND_API_KEY`, `MAIL_FROM` | Pilot accounts, section 10. `RESEND_API_KEY` from Resend (the sending domain `notes.beyrictech.com` is the verified one); `MAIL_FROM` defaults to `Beyric Note Taker <no-reply@notes.beyrictech.com>`, so set it only to use another verified address; leave `MAIL_PROVIDER` empty. Without a key the app runs, no mail goes out and password sign-up stays off |
| `MAIL_HOURLY_BUDGET`, `MAIL_HOURLY_RESERVE` | Optional, safe defaults (30 and 10). A budget for all outgoing mail in a rolling hour (**0 or less means no budget, unlimited; it does not stop mail, and the app warns about it at start**); over it mail waits with the reason `over_budget` and the admins get one notice an hour. Password-reset and invitation mail may use all of it; verification, "already registered" and notes mail not the last `MAIL_HOURLY_RESERVE`; alerts to the admins are never held. To end a login lockout on an account by hand: `docker compose exec web python scripts/unlock_login.py someone@example.com` (a password reset also ends it) |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Optional until Sign in with Google is wanted (both empty = the feature is off). The OAuth client's redirect address is `https://notes.beyrictech.com/api/auth/google/callback` |
| `CALENDAR_ENABLED`, `GOOGLE_OAUTH_TESTING`, `GOOGLE_APP_VERIFIED` | Google Calendar connect (task C3), all optional and `false` by default. `CALENDAR_ENABLED=true` switches the connect routes on (they answer 404 until then) and needs `TOKEN_ENC_KEY` in production. `GOOGLE_OAUTH_TESTING=true` while the Google Cloud project is in Testing. `GOOGLE_APP_VERIFIED=true` once Google has verified the app. The same OAuth client must also list `https://notes.beyrictech.com/api/calendar/google/callback` as a redirect address |
| `TOKEN_ENC_KEY` | **Required before the deploy once `GOOGLE_CLIENT_ID` is set** (or `CALENDAR_ENABLED=true`): the app refuses to start in production without it, so a server that has Google sign-in but no key crash-loops `web` (this took the site down for about 12 minutes on 2026-11-03). `openssl rand -base64 32`; it encrypts the calendar refresh tokens and pasted calendar links. Keep a copy somewhere safe (a password manager): it is not in the database or a backup, and **losing it means every user reconnects their calendar**. See "Before a deploy that needs a new setting" in section 8. `TOKEN_ENC_KEY_PREVIOUS`, `TOKEN_ENC_KEY_VERSION` (default `1`) are for rotation only |
| `BOT_MAX_LOCAL`, `USER_BOT_LIMIT`, `HOURS_RESERVE_MINUTES` | Optional. `BOT_MAX_LOCAL`: bots on meetings at once across all users, a safety ceiling; a new meeting past it answers `409 bot_busy`, and `0` sends no new bots. Unset, it is `20` (the bots run on Skribby's servers; our plan allows 25 at once, and its billing page has "Request an increase"); a line in `.env` always wins, so to use the default **delete the line** (`sed -i '/^BOT_MAX_LOCAL=/d' .env`, then `docker compose up -d --force-recreate web worker scheduler`). `USER_BOT_LIMIT` (default `2`): bots one user may have at once (`409 user_limit`), the real per-person control. `HOURS_RESERVE_MINUTES` (default `30`, `0` = off, only under `HOURS_MODE=enforce`): a second concurrent meeting of one person starts only if their hours cover this many minutes for each meeting running or just placed (the calendar event's length when known). The old name `MAX_ACTIVE_BOTS` is still read as `BOT_MAX_LOCAL`. See `docs/sop/2026-11-16-hours-reserve-and-capacity.md` |
| `AUDIO_RETENTION_DAYS` | Optional, default `7`. The same value is shown on `/privacy` (compose passes it to `next`) |
| `AUTOJOIN_ENABLED`, `AUTOJOIN_LEAD_SECONDS` | Optional, defaults `true` and `60`. Calendar auto-join is **on by default** (people connect their calendar and leave; there is no user switch for the feature as a whole). `AUTOJOIN_ENABLED=false` is the operator's emergency stop: after `docker compose up -d scheduler` no bot is sent from any calendar (the scheduler logs `AUTOJOIN_ENABLED is false` and records since when; Settings and Upcoming show a plain "paused" line, read from that record, so `web` need not restart). Joins wait; when it is switched back on, those whose 15 minutes passed meanwhile are closed **silently** (no "not recorded" mail), and joins still inside their 15 minutes go out. `AUTOJOIN_LEAD_SECONDS`: how long before the start the bot is sent |
| `MULTI_TEAM_ENABLED` | Optional, default `false`. Stage 1 of the multi-team design ships dark: with `false` a person is on one team at a time exactly as before (accepting an invitation dissolves their team of one, or is refused with `409 already_on_team` when they are on a team with others). `true` lets a person keep their personal team and belong to several others: accepting adds a membership and nothing is dissolved. Do **not** set it to `true` until the frontend with the team switcher is live (stage 3); the schema change (migration 0043) is safe to leave in place with the flag off. Restart `web`, `worker` and `scheduler` after changing it. Roll back: set `false`, restart. See `docs/sop/2026-11-13-multi-team-stage-1.md` |
| `SERIES_FOLLOW_MEETINGS` | Optional, default `false`. Recurring meetings follow the meetings: a series has one row per team scope (each team, and the person's personal space) and a meeting moves to the row of its own scope when it is shared, unshared, moved, or its recorder leaves a team. It does nothing unless `MULTI_TEAM_ENABLED` is `true` too. **Switch it on only after the backfill has run**: `docker compose exec -T web python scripts/backfill_series_follow.py` (a dry run: counts only, changes nothing), then the same with `--apply` (idempotent); after switching on and recreating the services, run it with `--apply` **once more** and expect 0 moved (a share during the first run, or before the recreate, can leave a straggler). After changing it run `docker compose up -d --force-recreate web worker scheduler`: a plain `restart` does not re-read `.env`; check with `docker compose exec -T web printenv SERIES_FOLLOW_MEETINGS </dev/null` (and the same for `worker` and `scheduler`). Roll back: set `false`, recreate; rows that were split stay split. The exact steps, with guards, are in `docs/sop/2026-11-14-series-follow-switch-on.md` (background: `docs/sop/2026-11-14-series-follow-core.md`) |
| `SCHEDULER_DISPATCH_SECONDS`, `SCHEDULER_SYNC_SECONDS` | Optional, defaults `10` and `60`: how often the `scheduler` service's two loops run (and how often a standby retries the leader lock). Leave them alone unless told to |
| `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_SECRET_EXPIRES_ON`, `MICROSOFT_CALENDAR_ENABLED` | Outlook calendar (Azure app registration, `docs/sop/2026-11-09-outlook-m2-microsoft-oauth-client.md`). All optional while the switch is `false`. Id and secret together make the client; production refuses to start with an id and no secret, or with `MICROSOFT_CALENDAR_ENABLED=true` and no complete client. The switch turns the whole feature on (the routes answer 404 `calendar_unavailable` and the screens draw nothing until it is `true`, and `CALENDAR_ENABLED` must be `true` too). `TOKEN_ENC_KEY` is required once the id is set. `MICROSOFT_SECRET_EXPIRES_ON` (`YYYY-MM-DD`, the date the secret in Azure expires) drives the reminder 30 days before |
| `MICROSOFT_AUTHORITY`, `MICROSOFT_ALLOWED_TENANTS`, `MICROSOFT_PUBLISHER_VERIFIED`, `MICROSOFT_ORGANIZER_DOMAIN_TRUST` | Optional. `MICROSOFT_AUTHORITY` (default `consumers`: personal Outlook.com, Hotmail, Live; `common` adds work and school accounts, once publisher verification is done; anything else stops startup). `MICROSOFT_ALLOWED_TENANTS`: comma-separated directory (tenant) ids a connection must come from, empty = any, a non-GUID stops startup. `MICROSOFT_PUBLISHER_VERIFIED` (default `false`): once Microsoft's publisher verification is done. `MICROSOFT_ORGANIZER_DOMAIN_TRUST` (default `false`): when `true`, a work connection also reads the organisation's verified domains and an unanswered invite from an organiser in them joins like a colleague's; it trusts the organiser address as Exchange Online reports it |
| `TRANSCRIPTION_MODEL`, `NOTES_MODEL` | Optional, defaults `gpt-transcribe` and `gpt-6-luna` (OpenAI, used by this app after the meeting). `TRANSCRIPTION_MODEL` turns the recording into the transcript you read; `NOTES_MODEL` adds speaker names to it and writes the notes. |
| `EXTRACT_MODEL`, `EXTRACT_MAX_OUTPUT_TOKENS`, `TRANSLATE_MODEL`, `ASK_MODEL`, `ASK_DAILY_LIMIT`, `TOPICS_MODEL` | Optional, defaults `gpt-6-luna` (all four models), `32000` and `30`. Each model setting moves one call alone: `EXTRACT_MODEL` reads notes and transcript into decisions, open questions and review items (`EXTRACT_MAX_OUTPUT_TOKENS` is the most output it may ask for on its retry after a cut-off); `TRANSLATE_MODEL` translates notes and transcripts; `ASK_MODEL` answers a question on the Ask Beyric page (`ASK_DAILY_LIMIT`: how many such questions one person may ask in a rolling 24 hours); `TOPICS_MODEL` finds topics repeated across a series, one call per series when its last meeting's notes are read |
| `POLL_INTERVAL_SECONDS`, `MAX_POLL_HOURS`, `AUDIO_DIR` | Optional, defaults `30`, `6` and `data/audio`. `POLL_INTERVAL_SECONDS`: how long the `worker` rests between sweeps of unfinished meetings (and, times a fixed factor, how long a bot state may go unrefreshed before it is treated as stale). `MAX_POLL_HOURS`: a meeting not finished being recorded after this many hours is failed, and it is the longest a calendar meeting is joined. `AUDIO_DIR`: where recordings are kept inside the container; compose mounts the `audiodata` volume at `/srv/data/audio`, so leave it alone |
| `TEAMS_JOIN_ENABLED` | Optional, default `true` (Skribby joins Teams as a guest). `false` switches it off: a Teams event then reads "Microsoft Teams meetings cannot be joined yet" and a pasted Teams link is refused |
| `MAIL_DAILY_CAP`, `MAIL_RECIPIENT_DAILY_CAP`, `ADMIN_ALERT_EMAIL` | Optional, defaults `80`, `5` and empty. `MAIL_DAILY_CAP`: once this many mails have gone since 00:00 UTC (the day Resend counts, account mail included) notes mail waits for the next day; 80 leaves room under Resend's Free plan of 100 a day; `0` = no cap. `MAIL_RECIPIENT_DAILY_CAP`: notes mails one address may get in 24 hours; more are skipped. `ADMIN_ALERT_EMAIL`: where admin alerts (no bot slot free, a meeting that was not recorded) are mailed; empty = each admin's own address |
| `ICS_ALLOWED_HOSTS` | Optional, default `calendar.google.com,outlook.office365.com,outlook.live.com`. The hosts a pasted private calendar link may point at (comma separated; an exact name, or `.suffix` for every subdomain). Only an operator edits it, never a user. The defaults are not yet confirmed against real feeds (spike C19): check before relying on them |

## 4. Check the bot provider key

Bots are Skribby's: the app needs `SKRIBBY_API_KEY` in `.env` and nothing else to send them (there is
no bot software to start on the server). Web, worker and scheduler refuse to start without it.

```bash
cd /opt/beyric-note-taker
grep -q '^SKRIBBY_API_KEY=.' .env && echo "SKRIBBY_API_KEY is set"   # the value is never printed
```

## 5. Start the app

```bash
docker compose up -d --build           # migrate runs first, then web and worker, then next
docker compose ps
curl -s http://127.0.0.1:8020/health   # ok  (through next, to FastAPI and the database)
curl -s http://127.0.0.1:8020/healthz  # ok  (next itself)
curl -s http://127.0.0.1:8021/health   # ok  (FastAPI directly)
```

`web` and `worker` run as the non-root user `app`; the image makes `/srv/data/audio` theirs. A
volume left by an older version is owned by root and needs, once:
`docker compose run --rm --user root worker chown -R app:app /srv/data/audio`.

## 6. HTTPS with nginx and Let's Encrypt (sudo)

Same layout as the server's other sites: a file in `sites-available`, linked into `sites-enabled`,
then `certbot --nginx` adds HTTPS to it.

```bash
cd /opt/beyric-note-taker
sudo cp deploy/nginx.conf /etc/nginx/sites-available/beyric-notes
sudo ln -s /etc/nginx/sites-available/beyric-notes /etc/nginx/sites-enabled/beyric-notes
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d notes.beyrictech.com
```

`nginx -t` checks every site on the server, so a mistake stops before anything reloads. The file
shares `http {}` with other sites, so its zone names are prefixed (`notes_login`,
`notes_general`), and `limit_req_status` and the Cloudflare real-IP settings sit inside its own
server block, so they change nothing for Haven or Novex.

If certbot's challenge fails while the record is proxied (usually Cloudflare's "Always Use HTTPS"
redirecting the challenge), switch the record to DNS only, run certbot, and switch it back.

After a later change to `deploy/nginx.conf`, do not copy it over the live file (that would drop
certbot's lines): edit `/etc/nginx/sites-available/beyric-notes` by hand, then `sudo nginx -t &&
sudo systemctl reload nginx`.

### What `deploy/nginx.conf` holds, and why

The file carries no prose of its own; the reasons live here.

- **Install is by hand, once:** `sudo cp` to `sites-available`, link into `sites-enabled`, `sudo nginx -t && sudo systemctl reload nginx`, then `sudo certbot --nginx -d notes.beyrictech.com`. Certbot then adds `listen 443 ssl`, the certificate lines and the HTTP-to-HTTPS redirect (marked `# managed by Certbot`); everything else in the file stays in that HTTPS server block. The tracked file is only the template: never copy it over the live one.
- **Zones are prefixed and `limit_req_status` sits in the server block** because `sites-enabled` files share one `http {}` with every other site.
- **`set_real_ip_from` lists Cloudflare's ranges** (https://www.cloudflare.com/ips, checked 2026-11-02) and `real_ip_header CF-Connecting-IP` trusts that header only from them. After it `$remote_addr` is the visitor's address, which both the edge limits and the app's login limit key on. A request sent straight to the server's IP keeps its own address, so the header cannot be spoofed from outside Cloudflare. Refresh the ranges if Cloudflare changes them.
- **`X-Forwarded-For` is set to `$remote_addr`, never appended** (`$proxy_add_x_forwarded_for`), so a client-sent copy is discarded. `X-Edge-Secret` proves the request came through this nginx: the app believes `X-Forwarded-For` only on a request that carries it (`EDGE_SHARED_SECRET`). The value lives in the root-only `/etc/nginx/snippets/beyric-edge-secret.conf` (one line: `proxy_set_header X-Edge-Secret "<value>";`), pulled in by an `include`; `nginx -t` fails while that file is missing, which is deliberate. See "Edge secret" below.
- **Proxy headers are set once, at server level.** A `proxy_set_header` inside a `location` replaces every inherited one and would silently drop the secret and the address for that path (`tests/test_edge.py` guards it).
- **Headers the app already sends are not repeated** (Content-Security-Policy, X-Content-Type-Options, Referrer-Policy, Cache-Control): a second copy would go out as a duplicate header, and a second CSP can only tighten the first and could break pages silently. nginx adds only Strict-Transport-Security, X-Frame-Options and Permissions-Policy.
- **Login:** 6 password attempts a minute per address at the edge (zone `notes_login`, burst 3), on top of the app's own counters (5 failures in 5 minutes per address, 10 in an hour per account). Only `POST` is keyed, so page views of `/login` are free; `/api/session` (the JSON login) is counted the same way. The optional trailing slash matters: the Next.js front door drops it before proxying, so `POST /login/` must be counted here too; `deploy/nginx-limit-check.sh` proves it.
- **Public account routes** (sign-up, resend, verify, forgot, reset, start of Google sign-in, invite preview, mail opt-out) share one counter per address (`notes_api_auth`, 10 a minute, burst 10), so rotating between them gains nothing; the waitlist has a tighter one (`notes_waitlist`, 5 a minute). The app has its own per-email and per-address limits behind these (`app/limits.py`); these stop a flood before it reaches Python. The Google callback is not listed: it needs a single-use state, and a flood of it is only cheap failures.
- **Rewriting notes** (`/meetings/<id>/regenerate`) waits up to 300 s instead of 60 because it calls OpenAI inside the request. Cloudflare gives up after 100 s on its free plan and shows its own timeout page (524); the rewrite likely, but unverified, finishes on the server, so reload the meeting page.

### Edge secret (one-time, and the order matters)

nginx proves to the app that a request came through it by adding `X-Edge-Secret` to every
request. The value is a random string kept in two places and in no git repository: the app's
`.env` (`EDGE_SHARED_SECRET`) and a root-only nginx snippet that `deploy/nginx.conf` includes.
**Never print it**: it must not reach the terminal, shell history, a chat or a log. The commands
below keep it in a shell variable and write it straight to the two files. Run them in bash, as
the deploy user, in this order, so nothing breaks half way (until step 4 the app trusts addresses
the old way; nothing about login changes for the owner):

```bash
# 1. Generate once; write the root-only nginx snippet and the .env line from the same variable.
SECRET=$(openssl rand -hex 32)
sudo mkdir -p /etc/nginx/snippets
sudo install -m 600 -o root -g root /dev/null /etc/nginx/snippets/beyric-edge-secret.conf
printf 'proxy_set_header X-Edge-Secret "%s";\n' "$SECRET" \
  | sudo tee /etc/nginx/snippets/beyric-edge-secret.conf >/dev/null
# Replace any existing EDGE_SHARED_SECRET line (an empty one from .env.example, or an old value
# when rotating) with a fresh one; neither command prints a value.
sed -i '/^EDGE_SHARED_SECRET=/d' /opt/beyric-note-taker/.env
[ -n "$(tail -c1 /opt/beyric-note-taker/.env)" ] && echo >> /opt/beyric-note-taker/.env  # last line needs its newline
printf 'EDGE_SHARED_SECRET=%s\n' "$SECRET" >> /opt/beyric-note-taker/.env
chmod 600 /opt/beyric-note-taker/.env
unset SECRET

# 2. Prove both files hold the same value, without showing either (prints "same"):
cmp -s <(sed -n 's/^EDGE_SHARED_SECRET=//p' /opt/beyric-note-taker/.env) \
       <(sudo sed -n 's/.*X-Edge-Secret "\(.*\)";/\1/p' /etc/nginx/snippets/beyric-edge-secret.conf) \
  && echo same || echo DIFFERENT

# 3. Put the include line in the live site file (once), under the other proxy_set_header lines:
#      include /etc/nginx/snippets/beyric-edge-secret.conf;
#    (it is in deploy/nginx.conf; edit /etc/nginx/sites-available/beyric-notes by hand, see above)
sudo nginx -t && sudo systemctl reload nginx

# 4. Deploy (or `docker compose up -d --force-recreate web`) once no meeting is bot_sent or
#    processing. The app reads the secret at startup.
```

`printf` is a shell builtin, so the value is not in any process's command line either. Do not
`cat`, `echo` or `diff` either file; to look at one, use `sudo grep -c X-Edge-Secret <file>` (a count).

Check it after the deploy:

```bash
docker compose logs web | grep EDGE_SHARED_SECRET           # no output: the startup warning is gone
curl -sI https://notes.beyrictech.com/health | grep -i x-edge-ok     # x-edge-ok: true
```

`X-Edge-Ok` says whether *that request* carried the right secret, so `true` through the public
URL means nginx, Next and the app agree on it; it never contains the secret. (`deploy.sh` checks
`/health` on `127.0.0.1:8020`, which skips nginx, so it reads `false` there: that is expected.)
Afterwards the spoof test in section 9 still ends in `429`. If `docker compose logs web` later
shows `arrived without a valid X-Edge-Secret header`, a request reached `next` without going
through nginx, or nginx lost the include: look at the site file first.

The app never refuses to start without the secret; it logs `EDGE_SHARED_SECRET is not set ...`
at startup, and until the secret is set every visitor is limited as the proxy's address (one shared
login limit; the forwarded address is never believed). Rotate
by repeating steps 1 to 4 with a new value, replacing the old `.env` line and snippet (a short
window of shared login limits between steps 3 and 4 is harmless). `deploy/nginx-limit-check.sh`
proves the nginx half (a client-sent `X-Edge-Secret` or `X-Forwarded-For` is replaced) without a
server.

What the site does: proxies to `127.0.0.1:8020`, the Next.js front door (60 s, or 300 s for
`/meetings/<id>/regenerate`); takes the visitor's address from `CF-Connecting-IP`, trusted only
from Cloudflare's published ranges; **overwrites** `X-Forwarded-For` with that address and adds the `X-Edge-Secret` header, so a client
cannot dodge the login limit; limits POST `/login` to 6 a minute per address (burst 3, then 429)
and all traffic to 20 requests a second; adds HSTS (1 year), `X-Frame-Options: DENY` and a
`Permissions-Policy`. The app sets its own CSP, `nosniff`, `Referrer-Policy` and `Cache-Control`.

Cloudflare closes a request after 100 s (free plan). Only rewriting notes can take that long; the
page then shows Cloudflare's 524 error, and the rewrite is likely, but not verified, to finish on
the server. Reload the meeting page.

## 7. Daily backup

```bash
BACKUP_DIR=~/backups/beyric-notes /opt/beyric-note-taker/deploy/backup.sh     # once by hand
crontab -e
```

```
17 3 * * * BACKUP_DIR=$HOME/backups/beyric-notes /opt/beyric-note-taker/deploy/backup.sh >> $HOME/backups/beyric-notes/backup.log 2>&1
```

The script dumps the app database inside the `db` container, gzips and tests it, writes mode 600,
and deletes dumps older than 14 days. Recordings are temporary and not backed up.

### Restore (practise once)

```bash
cd /opt/beyric-note-taker
FILE=~/backups/beyric-notes/notetaker-YYYYMMDDTHHMMSSZ.sql.gz
docker compose exec -T db psql -U postgres -c 'CREATE DATABASE notetaker_restore'
gzip -dc "$FILE" | docker compose exec -T db psql -U postgres -d notetaker_restore -v ON_ERROR_STOP=1 -q
docker compose exec -T db psql -U postgres -d notetaker_restore -c 'SELECT count(*) AS meetings, count(notes) AS with_notes FROM meetings'
docker compose exec -T db psql -U postgres -c 'DROP DATABASE notetaker_restore'
```

Over the live database (disaster recovery only):

```bash
docker compose stop web worker
docker compose exec -T db psql -U postgres -c 'DROP DATABASE notetaker' -c 'CREATE DATABASE notetaker'
gzip -dc "$FILE" | docker compose exec -T db psql -U postgres -d notetaker -v ON_ERROR_STOP=1 -q
docker compose up -d web worker
```

## 8. Continuous deployment, updating and rolling back

Merging a PR into `main` deploys it: `ci` runs, and when it passes `.github/workflows/deploy.yml`
connects over SSH and runs `deploy/deploy.sh <sha>`. That script refuses any commit not on
`origin/main`, refuses while a calendar join is due (see "Deploying while a join is due" below),
backs up the database, checks out the commit, builds (both images: FastAPI and
Next.js), starts, and waits up to two minutes until all three checks pass: `127.0.0.1:8020/health`
(through `next` to FastAPI and the database), `127.0.0.1:8020/healthz` (`next` itself) and
`127.0.0.1:8021/health` (FastAPI directly). If the new version never turns healthy it starts the
previous commit again (with `--remove-orphans`, so a commit from before `next` existed does not
leave the `next` container holding port 8020) and the workflow fails.
The deploy job waits up to 45 minutes (`timeout-minutes` in `deploy.yml`) because the server's
image build alone takes about 19 minutes; the run turns green or red only when the script ends, and
the `deployed <sha>` line in its log is the proof. Do not shorten it below the build time: GitHub
cancels only the SSH client, the script keeps running on the server and holds `.deploy.lock`, and a
re-run then says "another deploy is running" until it finishes.
A failing `docker compose up` (compose exits non-zero when a dependency is unhealthy, for example a
crash-looping `web`) does not stop the script: the health check decides, and the rollback runs when
the services do not come up. A build that fails restores the previous checkout and starts nothing.
The rollback also starts any container compose left in the `Created` state (`next`, waiting on a
dependency), since a stuck one would leave the site down. `tests/test_deploy_script.py` runs the
script against stand-ins for `docker`, `git` and `curl` to prove each case.

**The deploy that introduces `next`** moves port 8020 from `web` to `next`, so for a few seconds
the site answers 502 while `web` is recreated on 8021 and `next` starts. Do it when no meeting is
`bot_sent` or `processing` (the worker is not restarted, but the pilot should not be mid-call).
Before it, check the server has about 1.5 GB of free memory for the Next.js build (`free -h`).

### Before a deploy that needs a new setting

The app refuses to start on a missing or unusable required setting (that is the safe behaviour), and
`deploy/deploy.sh` then rolls back, so a forgotten setting costs a failed deploy rather than an
outage, but it still blocks the release. A PR that adds a required environment variable lists it
under **"Server steps before merge"** in its body; do those steps on the server first.

The current one: with `GOOGLE_CLIENT_ID` set in `.env`, add `TOKEN_ENC_KEY` before the deploy that
contains it. This appends a fresh key without printing it, then recreates the two services that
read it:

```bash
cd <the project directory on the server>   # where compose.yaml and .env are
grep -q '^TOKEN_ENC_KEY=.' .env || { [ -z "$(tail -c1 .env)" ] || echo >> .env; \
  printf 'TOKEN_ENC_KEY=%s\n' "$(openssl rand -base64 32)" >> .env; }
docker compose up -d --force-recreate web worker
```

Then copy the value from `.env` into your password manager (`grep '^TOKEN_ENC_KEY=' .env`, in a
private terminal). Losing it means every user reconnects their calendar.

### Deploying while a join is due

Auto-join starts a bot with nobody pressing anything, so a deploy that restarts `web`, `worker` and
`scheduler` just before a join is dispatched can drop that recording. `deploy/deploy.sh` therefore
asks the database first (`deploy/gate.sh`, `join_due_soon`) and **refuses** when a join in state
`scheduled`, `no_capacity` or `dispatching` has `run_at` within the next 10 minutes (or already
past) and a `deadline_at` that has not passed. It prints one line naming the earliest due time (UTC)
and exits 1, before it changes anything (no backup, no checkout, no build). The CD workflow then
fails with that line in its log; nothing is retried. **Wait until the join has started** (up to about
10 minutes), then re-run the failed `deploy` workflow in GitHub (Actions, the failed run, "Re-run
failed jobs": it deploys the same commit) or run the deploy by hand (below).

If the database cannot be asked (the `db` container is down, the query errors, the table is
missing), the gate **refuses** as well, with its own line: it fails closed, never open. On a brand
new server with no stack running yet, use the override for that first deploy.

To deploy anyway (an emergency fix; a deliberate choice to lose a join), set
`ALLOW_DEPLOY_WITH_JOINS_DUE=1` for **that one deploy**, by hand on the server. Only the exact value
`1` counts. The CD key cannot pass it (its forced command takes only the SHA), so run the deploy
yourself:

```bash
cd /opt/beyric-note-taker && git fetch origin main
ALLOW_DEPLOY_WITH_JOINS_DUE=1 deploy/deploy.sh "$(git rev-parse origin/main)"
```

It prints `WARNING: a calendar join is due at <time>; deploying anyway` and carries on. Do not put
the variable in `.env`, a shell profile or the `authorized_keys` line to leave it on: that turns the
gate off for every deploy. If CD must be allowed through once (nobody at the server for a
by-hand run), temporarily change the key's `command="..."` in `~/.ssh/authorized_keys` to
`command="env ALLOW_DEPLOY_WITH_JOINS_DUE=1 /opt/beyric-note-taker/deploy/deploy.sh"`, re-run the
workflow, and **put the line back** straight after.

To take the gate out altogether, delete the two lines in `deploy/deploy.sh` that read
`. "$(dirname ...)/gate.sh"` and `deploy_gate || return 1` (`deploy/gate.sh` can stay or go). Like
every change to `deploy.sh`, it applies from the deploy after the one that ships it: the running
script is the one already on the server, so **the deploy that introduces the gate is not itself
gated**. `deploy/deploy-gate-check.sh` proves the gate against a scratch Postgres with the real
schema (see its header); `tests/test_deploy_script.py` proves the wiring in `deploy.sh`.

### Deploy key setup (once)

GitHub needs a private key to log in. It gets its own key, not the owner's: a key stored in
GitHub can be read by any workflow change, and the owner's key opens a full shell on a server that
also runs Haven and Novex. This key can do one thing, run `deploy/deploy.sh` with a commit SHA,
and removing its one line from `authorized_keys` revokes it without touching anyone's access.
On the server:

```bash
ssh-keygen -t ed25519 -N '' -C beyric-notes-cd -f ~/beyric-notes-cd
echo "command=\"/opt/beyric-note-taker/deploy/deploy.sh\",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty $(cat ~/beyric-notes-cd.pub)" >> ~/.ssh/authorized_keys
```

Store these repository secrets, then delete `~/beyric-notes-cd*` from the server:

| Secret | Value |
|---|---|
| `DEPLOY_SSH_KEY` | contents of `~/beyric-notes-cd` (the private key) |
| `DEPLOY_HOST` | the server IP |
| `DEPLOY_USER` | the SSH user |
| `DEPLOY_KNOWN_HOSTS` | `ssh-keyscan -t ed25519 SERVER_IP` output |

### By hand

```bash
cd /opt/beyric-note-taker && git fetch origin main
deploy/deploy.sh "$(git rev-parse origin/main)"
```

Roll back: re-deploy an older commit of `main` with `deploy/deploy.sh <sha>`, or revert the PR on
GitHub so CD deploys the revert. To take the Next.js front door out of the path alone (and keep the
rest of the release), point nginx at FastAPI directly: change `proxy_pass http://127.0.0.1:8020`
to `http://127.0.0.1:8021` in `/etc/nginx/sites-available/beyric-notes` (three places),
`sudo nginx -t && sudo systemctl reload nginx`. The Jinja pages, including `/privacy` and `/terms`,
then answer from FastAPI as before. If the bad version ran a migration, older code may not run on the
new schema: `docker compose run --rm migrate alembic downgrade -1` first, or restore the backup the
deploy took (step 7).

### Cleaning the server `.env`

Why: over time the server's `.env` collected long comments and lines that only repeat a default. `scripts/clean_env.py` rewrites it into the layout of `.env.example` (section headers, no other comments, every value kept exactly as written). It uses only Python's standard library, so the server's own `python3` runs it, and it prints key **names** only: never a value.

Wait until the CD deploy of the merge has printed `deployed <sha>` (the script is then in `/opt/beyric-note-taker/scripts/`; do not `git pull` there, the deploy leaves the checkout detached), then:

```bash
cd /opt/beyric-note-taker
python3 scripts/clean_env.py /opt/beyric-note-taker/.env            # dry run: changes nothing
python3 scripts/clean_env.py /opt/beyric-note-taker/.env --apply    # when the dry run reads right
```

The dry run lists what stays, what would be removed (a value equal to the code's own default, an empty `PAYSTACK_WEBHOOK_SECRET=`, which behaves like no line, or a leftover line of something the app no longer reads: every `VEXA_*` key, `BOT_NAME`, `BOT_PROVIDER` and `COMPOSE_PROFILES=vexa`, whatever the value), keys it does not know (kept at the bottom, never deleted), duplicates (the last one wins, as docker compose reads it) and any required key that is missing. It refuses, and writes nothing, if a required key is missing, a line cannot be read, the file is a symlink or belongs to another user, the file changes while it is being rewritten, a kept value contains `$` (compose expands `$NAME` in unquoted and double-quoted values, so moving keys could change what it means: the dry run names the keys, edit them by hand first), or the result would lose a key's value. Two shapes are not handled and are refused or left as they are: a CRLF line ending inside a quoted multi-line value, and a lone unclosed quote. `--apply` first saves `.env.bak-<UTC time>` (mode 600), then replaces `.env` atomically (mode 600). Run it again and nothing changes.

An environment change is read only when a container is **recreated**; a restart does not re-read `.env`. This is the same restart a deploy does, so use the deploy's own gate: run it **only when no meeting is being recorded and no calendar join is due** (the deploy refuses in that case, `deploy/gate.sh`). After `--apply`:

```bash
bash -c '. deploy/gate.sh; join_due_soon && echo "no join is due"'   # a time printed instead means a join is due: wait
docker compose up -d --force-recreate --no-deps web worker scheduler
```

(`--no-deps` recreates exactly those three: without it compose also starts `migrate` again, which is harmless but not needed. `next` needs nothing: no value changes, and it gets `AUDIO_RETENTION_DAYS` from compose with the same default of 7.) To go back: `cp .env.bak-<time> .env`, then the same command. Delete old backups when you are satisfied: they hold every secret.

### After Vexa was removed from the repo (2026-11-20)

**What changed.** The self-hosted bot host (Vexa) is gone from the repo: its compose services, volume
and network definitions, `.env.vexa`, its scripts and every setting of it. Bots are Skribby's;
`BOT_PROVIDER`, `BOT_NAME` and all `VEXA_*` lines are no longer read. Old meetings still say
`bot_provider = 'vexa'` in the database: they stay readable, exportable and deletable, and nothing
asks anyone about them (their copy was verified deleted before the removal). Details and the
decision record: `docs/sop/2026-11-20-remove-vexa.md`.

**The first deploy after the merge removes the stopped Vexa containers, on its own.** The deploy
runs `docker compose up -d --remove-orphans`; a service that is no longer in `compose.yaml` is an
orphan, and its container is removed, stopped or not (shown with a throwaway compose project on
2026-11-20). It removes **containers only**: the volumes, the network and the images stay. Check:

```bash
cd /opt/beyric-note-taker
docker compose ps -a | grep -i vexa       # nothing: the containers are gone
docker volume ls | grep -i vexa           # <project>_vexapgdata and <project>_vexastorage: still there
docker network ls | grep -i vexa          # <project>_vexainternal: still there
docker images | grep -E 'vexaai/vexa-lite|versity/versitygw'   # still there
```

**Optional, a separate step you approve: delete what is left of Vexa on the server.** The volumes
hold Vexa's own database and its recording storage. This cannot be undone. Run it only after the
deploy above and only if you do not want Vexa's data kept. On 2026-11-20 this check printed 0
(no Vexa recording was left to delete); run it again first, and stop if it is not 0:

```bash
docker compose exec -T db psql -U postgres -d notetaker -c "select count(*) as vexa_copy_left from meetings where bot_provider = 'vexa' and status in ('ready', 'failed') and audio_deleted_at is null and (vexa_recording_id is not null or vexa_meeting_id is not null);"
```

Use the exact names the `grep` lines above printed; `<project>` is the prefix they show (the folder
name of the checkout, normally `beyric-note-taker`):

```bash
docker volume rm <project>_vexapgdata <project>_vexastorage
docker network rm <project>_vexainternal
docker image rm vexaai/vexa-lite:v012 versity/versitygw:v1.8.0
rm -f /opt/beyric-note-taker/.env.vexa
```

`postgres:17-alpine` is shared with the app's own database image: do not remove it. If `docker
image rm` says an image is in use, stop: something still runs it.

**Then clean the server `.env`** (section 8, "Cleaning the server `.env`"): the dry run now names the
leftover `VEXA_*`, `BOT_NAME` and `BOT_PROVIDER` lines (and `COMPOSE_PROFILES=vexa`) as "no longer
read by the app", and `--apply` drops them. Until you do, they are harmless: the app ignores them
and compose does not require them (checked with a leftover `COMPOSE_PROFILES=vexa` too).

**Rolling back to Vexa** is no longer a setting: it means reverting the removal PR on `main`, and
the volumes must still exist. Once they are deleted, Vexa would start empty.

## 9. Verification checklist

From **outside** the server:

```bash
curl -sI http://notes.beyrictech.com/ | head -3                                   # 301 to https
curl -sI https://notes.beyrictech.com/login | grep -i -E 'HTTP|strict|x-frame|content-security'
curl -s -o /dev/null -w '%{http_code}\n' https://notes.beyrictech.com/health      # 200
```

- [ ] Ports as in step 1.
- [ ] HTTPS shows the login page with a valid certificate; one `Content-Security-Policy`, plus HSTS
      and `X-Frame-Options`.
- [ ] Login works and the cookie is `Secure`.
- [ ] Spoof test: wrong passwords with a different made-up `X-Forwarded-For` each time go `401`,
      then `429`, and changing the header does not bring `401` back:

  ```bash
  HOST=https://notes.beyrictech.com; JAR=$(mktemp)
  TOKEN=$(curl -s -c "$JAR" "$HOST/api/session" | sed -n 's/.*"csrf_token": *"\([^"]*\)".*/\1/p')
  for i in 1 2 3 4 5 6 7 8; do
    curl -s -b "$JAR" -o /dev/null -w '%{http_code}\n' -X POST "$HOST/api/session" \
      -H 'Content-Type: application/json' -H "X-CSRF-Token: $TOKEN" \
      -H "X-Forwarded-For: 10.9.8.$i" -d '{"password":"wrong"}'
  done; rm -f "$JAR"
  ```

  Run it in bash. To test the Cloudflare header, send `-H "CF-Connecting-IP: 10.7.7.$i"` straight
  to the server (`--resolve notes.beyrictech.com:443:SERVER_IP`); through Cloudflare that header
  gets a 403 from Cloudflare itself. Expect your own address in `docker compose logs web`. The test
  locks your own address out of login for 5 minutes (the app's per-address limit), and each failed
  attempt also counts against the account's 10 an hour, so do not repeat it within the hour.
- [ ] `docker compose ps`: `db`, `web` healthy; `worker`, `scheduler` up; no provider errors in
      `docker compose logs worker`.
- [ ] Other apps on the server still answer after the install.
- [ ] After a reboot, the stack and nginx come back.
- [ ] A backup exists and the scratch restore shows meetings and notes.
- [ ] A merge to `main` deploys through CD.
- [ ] Task 8 acceptance: a real meeting end to end.

## 10. Pilot accounts: deploy and open

Sign-up exists in the code but **stays closed**: `app_state.signups_open` is `false` and nothing in
a deploy changes it. The order matters:

1. **Ownership scoping must already be live** (accounts core, migrations 0006 and 0007). Check:
   `GET /api/session` as the admin shows `user.is_admin: true`.
2. **Add the mail and Google variables to `.env`** (section 3) *before* the deploy. A missing
   `RESEND_API_KEY` is not an error; it only means no mail and no password sign-up. Never put these
   values in a message or a commit.
3. **Migration `0008`** (one nullable column on `auth_tokens`) runs with the deploy. It is
   additive: old code ignores the column. Roll back with
   `docker compose run --rm migrate alembic downgrade 0007` before starting old code (it only
   drops that column; verification links not yet used then stop working).
4. **nginx, by hand** (never copy `deploy/nginx.conf` over the live file: it would drop certbot's
   lines). In `/etc/nginx/sites-available/beyric-notes`, add the two `limit_req_zone` lines next to
   the existing ones (they sit above `server {`), and the two `location` blocks for the account
   routes and the waitlist next to the login location, exactly as in `deploy/nginx.conf`
   (the comments there say what each does). Then `sudo nginx -t && sudo systemctl reload nginx`.
   Prove it without touching the server: `deploy/nginx-limit-check.sh` runs the repository's file in
   a throwaway container (the account routes share one counter of 11 requests, then 429; the
   waitlist 6; the Google callback and `GET /api/pilot` are not counted).
5. **Check**: `curl -s https://notes.beyrictech.com/api/pilot` answers
   `{"state":"closed","prefill_email":null,"google_signin":false}` (`true` once the Google variables
   are set).
6. **Open the pilot** only when you decide to, as the admin, with a signed-in session. Until the
   admin screens exist, use curl with a cookie jar (the CSRF token comes from `GET /api/session`;
   placeholders in capitals):

   ```bash
   J=$(mktemp); H=https://notes.beyrictech.com
   CSRF=$(curl -s -c $J -b $J $H/api/session | python3 -c 'import json,sys;print(json.load(sys.stdin)["csrf_token"])')
   CSRF=$(curl -s -c $J -b $J -H "X-CSRF-Token: $CSRF" -H 'content-type: application/json' \
     -d '{"email":"ADMIN_EMAIL","password":"ADMIN_PASSWORD"}' $H/api/session \
     | python3 -c 'import json,sys;print(json.load(sys.stdin)["csrf_token"])')
   curl -s -b $J -H "X-CSRF-Token: $CSRF" -H 'content-type: application/json' -X PUT $H/api/admin/pilot \
     -d '{"signups_open":true,"max_accounts":10,"default_hours_cap_seconds":18000,"password_signup_enabled":false}'
   ```

   Keep `password_signup_enabled` `false` until a real verification mail has been delivered once
   (sign up with Google, or hand out reset links, until then). Do not invite more than one or two
   testers until several bots at once are supported (spec section 15, phase D).
7. **Close it again** with the same call and `"signups_open":false`, or in the database:
   `update app_state set signups_open = false;`.

Operating notes: the worker sends the mail and removes deleted accounts (`docker compose logs
worker`; a mail provider that is down only delays mail, and `GET /api/admin/mail-failures` lists
what failed). Delete one account for good with
`docker compose exec web python scripts/delete_user.py someone@example.com` (it asks first).

The `scheduler` service (`python -m app.scheduler`, same image as `worker`, limited to 256 MB) does
the time-critical work of the calendar features; see `docs/sop/2026-11-03-phase-c-scheduler.md`.
Only one copy acts, through a Postgres advisory lock on its own connection, so a second replica
is a standby and takes over within about 10 seconds. It needs no setting and nothing depends on
it: if it cannot start, `web` and `next` are unaffected. `deploy/deploy.sh` waits up to 60
seconds after `up` for a heartbeat newer than the deploy (`app_state.scheduler_heartbeat_at`) and
rolls back when there is none. Check it by hand with `docker compose logs --tail 20 scheduler` and
`docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA' <<< "select now() - scheduler_heartbeat_at from app_state;"`
(a few seconds when healthy).

## Follow-ups

- Backups stay on the same server. Copy them elsewhere once the owner picks a place.
- No rotation procedure for the Skribby key: create a new one in Skribby's dashboard, update `.env`,
  `docker compose up -d --force-recreate --no-deps web worker scheduler`, then delete the old key.
- Nothing monitors the stack. Add an uptime check on `/health` (it returns 503 when the database
  is down).
