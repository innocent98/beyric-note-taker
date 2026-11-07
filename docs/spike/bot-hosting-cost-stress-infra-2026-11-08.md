# Who runs the meeting bot: cost, stress, upkeep and a test plan (self-hosted Vexa, a bigger machine, Recall, Skribby)

**Written:** 2026-11-07, late evening WAT (file dated 2026-11-08 as asked), for Adebayo. **Type:** research and design only. Nothing was run on any server, no vendor or Google account was opened, no key was used, notes.beyrictech.com was not contacted.
**How every fact is labelled:**

| Label | Meaning |
|---|---|
| **measured** | read from our own server or a log, or counted in our own repo |
| **vendor-stated** | a vendor's or platform's own page, read with `curl` on 2026-11-07 (raw text, not a model's summary). The page is named, and a copy is in `docs/spike/primary-pages-2026-11-07/` |
| **earlier note** | stated in one of our earlier notes; I did not re-read the original page |
| **estimate** | my arithmetic or judgement, with the formula shown |
| **unverified** | I could not confirm it; general knowledge or a page that would not load |

Money is in USD unless a page quotes euros. Where I convert euros I use **EUR 1 = USD 1.14 (assumption, not verified, same as `docs/spike/hosted-pivot-gladia-recall-cost.md`)**. Our own OpenAI cost (transcription and notes, about USD 0.63 to 0.68 per recorded hour today, *earlier note*) is **left out of every number below**: it is the same on every option. The one place a bot choice could change it is named in section 2.4.

---

## 0. The answer first

1. **Money is not what decides this.** At pilot size (50 recorded hours a month) every option costs between nothing extra and about USD 40 a month. At 1,000 hours a month the hosted bots cost USD 350 to 500 and a big self-hosted machine costs about USD 30 to 320. The real trade is **reliability, your time, and where recordings go**.
2. **Moving to a bigger server does not fix the problem you are trying to fix.** Google refuses our anonymous bot about 1 in 4 times *(vendor-stated by Vexa: 27% of 45 joins from a Linode datacenter address, 23% of its hosted production bots over 24 hours; earlier note, I did not re-read the Vexa PR)*. A bigger machine is still a datacenter address and still a guest. A bigger machine buys **capacity** (more bots at once), nothing else.
3. **Our current server holds one bot, not two.** One bot used about 1.4 of the 4 cores and 1.4 GiB of memory, and the whole machine's load was 4.1 on 4 cores *(measured, one sample, 2026-11-03)*. Vexa's own docs say Vexa Lite shares one virtual screen and is "best for one browser session at a time" *(vendor-stated: `vexa-docs-deployment-lite.md`)*. We have **never** run two bots at once. That is the first thing to measure (section 6).
4. **Vexa signed-in mode is not usable yet.** Upstream issue #1725 is open; fix PR #1873 is open, not merged, and GitHub reports it has merge conflicts with main; its last activity was 2026-11-02. It also does not run on Lite, needs one Google account per bot at a time, and keeps us on a datacenter address *(section 1.3)*.
5. **Vexa runs a hosted service** (USD 0.30 per bot hour, USD 0.50 with transcript) that speaks the same API as our `VexaClient`, so switching would be nearly free in code. But it is the same bot with the same Google refusals, hosted in one US datacenter, and it sends audio to Cloudflare for transcription *(vendor-stated: `vexa-pricing.txt`, `vexa-subprocessors.txt`)*. It fixes upkeep, not reliability.
6. **Hosted bots cost about the same as each other and a lot more than a small server:** Skribby USD 0.35 per hour, Recall USD 0.50, Vexa hosted USD 0.50 (all bot only, our OpenAI cost excluded). What you buy is someone else patching Chrome and the Meet page, and (for a signed-in bot) easier accounts. **No vendor publishes a Google Meet join-success rate**, so none of them has proven it beats us. That is what the bake-off is for.
7. **Zoom and Teams are not a reason to choose hosted, but they are a reason to expect trouble later.** Zoom now requires apps that join other people's meetings to be authorised (OBF or ZAK token, or its RTMS system) and says its Meeting SDK "does not support bots or AI notetakers". Vendors absorb that; a self-hosted bot that joins through Zoom's web page may be hit by it. Teams is untested by us and has its own lobby rules. Google Meet is probably the *most random* platform for a guest bot, not necessarily the hardest *overall* (section 4).

### Decision table

| | A. Vexa on the current shared server | B. Vexa on its own bigger machine (Phase D) | B2. Vexa hosted by Vexa | C. Recall | D. Skribby |
|---|---|---|---|---|---|
| Cash at 50 h / month | USD 0 extra (box costs about 5.83 a month already) | 6 to 99 (rent for one bot) | 15 to 25 | 25 (guest) to 40.50 (signed-in) | 17.50 (guest) |
| Cash at 200 h | USD 0 extra, but one bot only | 6 to 99 (rent for one bot) | 60 to 100 | 100 to 115.50 | 70 |
| Cash at 1,000 h | does not fit | 28 to 315 (rent for 5 bots; 1,000 h really wants 6 or more) | 300 to 500 | 500 to 515.50 | 350 |
| Bots at once | 1 (measured) | 1 per 1.4 cores *(estimate)*, untested | "unlimited bots" claimed, not tested | no ceiling published; limits shown in the dashboard | Free 3, Pay As You Go 25 *(vendor-stated)* |
| Fixes Google refusals? | no | no | no | not proven | not proven |
| Our time per month *(estimate)* | 2 to 4 h | 4 to 8 h | 0.5 to 1 h | 1 to 2 h | 1 to 2 h |
| Recordings leave our server? | no | no | yes (US, plus Cloudflare) | yes (region you pick) | yes (EU default) |
| Time to switch to it | now | days (Phase D) | hours | days | days |
| Biggest risk | our shared server runs hot and hurts Haven and Novex | we carry every Chrome/Meet fix and the upkeep | same refusals as ours | setup of signed-in bots is heavy | owner changed in Sept 2026; no certifications seen |

**Pick for now (details in section 7):** stay on A for the pilot, send no new money, and finish the bake-off with at least 15 trials per arm and add Zoom and Teams trials. If the bake-off shows a hosted, signed-in, invited bot getting in clearly more often than ours, move to **Skribby first** (cheapest, simplest signed-in), or **Recall** if the privacy paperwork matters more than the setup. Build the bot-layer seam (checklist B1) either way: it is what makes any switch cheap.

---

## 1. The four options, and what I checked

### 1.1 A. Self-hosted Vexa on the current shared server

- Server `haven-respite`: 4 vCPU, 7.8 GiB, shared with Haven and Novex, about USD 70 a year *(measured / Adebayo's statement, `docs/spike/cost-and-capacity-2026-11-03.md`)*. The Contabo plan list shows a 4-core, 8 GB plan at EUR 5.50 a month "for the first 24 months" *(vendor-stated: `contabo-vps.txt`; the monthly-term price is not on the page)*, which matches.
- One bot: `vexa` container 138% CPU (about 1.4 cores), 1.95 GiB of its 3 GiB cap; other containers 40 to 92 MiB each; host load 4.1 on 4 cores, 3.2 GiB free *(measured, one sample, bot idle in a call, no transcription burst: treat as a floor)*.
- Our compose file caps `vexa` at 3 GiB (`compose.yaml:212`) and puts **no CPU cap** on anything, so a second bot can take CPU from the other two projects *(measured: read from the file)*.
- The app sends at most `bot_max_local` bots at a time, default 1, and refuses the rest with `bot_busy`; there is **no queue, on purpose**: "a queued meeting is an unrecorded meeting" *(`app/placement.py` docstring, `app/config.py:26`)*.

### 1.2 B. Self-hosted Vexa on a dedicated or bigger machine (Phase D)

- Phase D is planned, not built: `bot_hosts` table, health checker, `place_bot()` for several hosts, capacity endpoint, second host files, a load test, "do not invite more than one or two testers before D9b" *(`docs/checklist/master-checklist.md`, Phase D lines)*.
- **Lite versus Compose.** Vexa says in Lite "bots share a single Xvfb [virtual screen], best for one browser session at a time. Compose isolates bots in containers; the Helm chart gives each bot its own Pod" *(vendor-stated: `vexa-docs-deployment-lite.md`, "Limits vs compose")*. So **more than one bot at a time realistically means moving from Lite to Compose**, which is a different, heavier stack (about eleven images, *earlier note*, `docs/spike/meet-join-resilience.md`). We have not run Compose on a server.
- Vexa says to build from source you want 8 vCPU / 16 GB; the published images "any modest machine works" *(vendor-stated: `vexa-docs-quickstart.md`)*. That sentence is about starting the stack, not about running many bots.

### 1.3 B-plus. "Upgrading Vexa": what it would and would not bring

| Question | Answer | Label |
|---|---|---|
| What do we run? | v0.12.29 (the 403 fix: re-knock up to 3 times) | earlier note (`meet-join-resilience.md`) and your brief |
| Latest GitHub **release** | v0.12.28, 2026-10-09. v0.13.1 is an alpha pre-release from 2026-09-07. v0.12.29 is a Docker tag and a commit, not a GitHub release | vendor-stated: `vexa-github-releases.json` |
| Issue #1725 "Authenticated Google Meet bots launch Chromium with `--incognito` and ignore restored session" | **Open.** Opened 2026-09-22, last comment 2026-11-06 (a third independent user on v0.12.28 confirming it, and noting a further problem: the signed-in "Join anyway" button is not in the bot's list of known buttons) | vendor-stated: `vexa-github-issue-1725*.json` |
| PR #1873 (the fix: drop `--incognito` when signed in) | **Open, not merged.** One commit, 5 files, opened 2026-10-24, last updated 2026-11-02. GitHub's field `mergeable_state` is `dirty` (it conflicts with main, so it needs a rebase). Vexa's merge bot on 2026-10-24 listed four missing sign-offs: value, a second reviewer, architecture and security. One independent user posted a "CONFIRMED" test on a real Meet call on 2026-11-02 | vendor-stated: `vexa-github-pr-1873*.json` |
| How fast do maintainers move? | Vexa's own index page says (as of 2026-08-20) 26 reports were waiting for triage, median 13 days, target 3 days, "we are not hitting it" | vendor-stated: `vexa-docs-llms.md` |
| Does signed-in mode work on Lite? | **No.** "Lite does not package the provisioning flow or a userdata store" | vendor-stated: `vexa-docs-deployment-lite.md`, `vexa-docs-authenticated-bots.md` |
| How many signed-in bots at once? | **One bot per saved Google session**; a second gets HTTP 409. For N at once you provision N accounts, "today that means N deployments" | vendor-stated: `vexa-docs-authenticated-bots.md` |
| Keeping the session alive | Google rotates cookies; Vexa writes the fresh session back only when a bot **leaves cleanly**; idle accounts decay; "no packaged keep-warm job"; keep a stable egress address and bot image; re-login is one command (`make login`) when it dies | vendor-stated: same page |
| Platforms it is validated on | Google Meet only; Teams and Zoom "on their own waves" | vendor-stated: same page |
| Would a bigger machine plus signed-in fix the 403s? | Unknown. Signed-in bots "reduce" blocks at other vendors, not remove them *(earlier note on Recall)*; it is still a datacenter address. **Not verified for Vexa**: nobody has published a result | unverified |

So "upgrading Vexa" to get signed-in means: move to Compose, build a bot image with `--incognito` removed (or wait for #1873), create and warm a Google account, run `make login`, keep it fresh, and repeat for every extra bot. That is plan A1/A2 in `docs/superpowers/plans/2026-11-03-meet-signed-in-bot-and-vendor.md`, not started.

### 1.4 B2. Vexa's own hosted service (found while checking)

| | |
|---|---|
| Price | **Build on Vexa: USD 0.30 per bot hour; real-time transcription +USD 0.20 per hour; USD 0.50 all in.** 12 months of audio storage included. "Unlimited bots". USD 2 of free credit, no card (about 4 transcribed hours). Individual plan USD 12 per seat per month is for one person and their agents, **one concurrent bot**, not for products | vendor-stated: `vexa-pricing.txt` |
| Same API as ours? | The docs describe the hosted and self-hosted quickstart as one path ("hosted or self-host"). The hosted service "runs the published v0.12.28 images" (checked by them 2026-10-26) | vendor-stated: `vexa-docs-quickstart.md`, `vexa-docs-roadmap-status.md`. **Unverified** that the five routes we call (`app/vexa.py`) behave identically there; the free credit would test it |
| Do we need their transcript? | Probably yes: with transcription off Vexa returned no segments and no speaker data, and our notes use those segments to name speakers | measured, `docs/spike/vexa-spike-report.md` ("Live transcription check"). So plan on USD 0.50, not 0.30 |
| Where | One region: Seattle, USA (Akamai/Linode `us-sea`); "we do not currently offer EEA or UK hosting"; audio of every transcribed meeting goes to Cloudflare Workers AI | vendor-stated: `vexa-subprocessors.txt`, `vexa-site-privacy.txt` |
| DPA / SOC 2 | A "DPA" and "Security" link are in the site footer; `vexa.ai/dpa` returned 404 for me, so I **did not read a DPA**. A security page exists about self-hosting. No SOC 2 mentioned on pages I read | unverified |
| Reliability | Same bot code, so the same Google refusals (their hosted production: at least 28 of 121 Meet bots refused, 23%, in one day) | earlier note |

### 1.5 C. Recall and D. Skribby

Covered in sections 2 to 5, from the pages in `docs/spike/primary-pages-2026-11-07/`. Recall's legal entity on its privacy page and DPA is **Hyperdoc Inc.** Skribby's pages say "An Alianza Company".

---

## 2. Cost

### 2.1 What each rate is, and where it comes from

| Item | Self-host A / B | Vexa hosted | Recall | Skribby |
|---|---|---|---|---|
| Bot | machine rent (below) | USD 0.30 per h | **USD 0.50 per h**, billed to the second, from the bot's first load of the meeting link to its end, **waiting-room time included**; bots that end in the `fatal` state are not billed | **USD 0.35 per h**, billed per minute from creation to end, lobby wait included; a bot not admitted within 10 minutes is free |
| Vendor transcript (optional: we keep our own OpenAI) | n/a | +USD 0.20 per h | +USD 0.15 per h | +USD 0.04 (Groq Whisper Turbo) up to +0.62 per h by model; `none` (audio only) costs 0 |
| Storage | disk on our machine | 12 months included | 7 days free, then USD 0.05 per recorded hour per extra 30 days | 1 week default; 1 year +USD 0.05 per h |
| Google account(s) for signed-in | none on Lite; N accounts for N bots on Compose | not stated | a **dedicated Google Workspace** with Legacy SSO (no Gmail path is written on Recall's login guide; Recall's chat assistant said Gmail works, which the guide does not support: `recall-support-chat-2026-11-07.md`) | a personal Gmail **or** Workspace account typed into their dashboard |
| Free to try | n/a | USD 2 credit | first 5 recorded hours | Free plan: 5 bot hours a month, 3 at once |
| Source | `contabo-vps.txt`, Hetzner pages | `vexa-pricing.txt` | `recall-pricing-reread.txt`, `recall-docs-calculating-usage.md` | `skribby-pricing-reread.txt`, `skribby-guide-billing.txt` |

**Google Workspace seat:** Business Starter EUR 6.80 per user per month (page also shows EUR 6.12 for the first 20 users of a new customer, from 2026-11-20) *(vendor-stated: `google-workspace-pricing.txt`; the page does not name the commitment term, earlier note says one year)*. In USD: 6.80 x 1.14 = **7.75 per seat**; two seats (the number Recall and Skribby both advise as a minimum) = **15.50 a month**. A free Gmail account costs 0 but Google's terms on automated use of a consumer account are **unverified**.

### 2.2 Machine rent for self-hosting (the sizing rule is mine)

Sizing rule *(estimate)*: cores needed = 1.4 x bots + 1 for Vexa and its database; memory needed = 0.54 GiB + 1.4 GiB x bots plus the operating system. One sample, idle bot, no transcription burst, so treat as a floor and keep a third spare.

| Bots at once | Cores / memory needed *(estimate)* | Cheap option (Contabo, plan page `contabo-vps.txt`) | Steady option (Hetzner dedicated vCPU, `hetzner-cloud-dedicated-plans.txt`) | Middle option (Hetzner shared vCPU, `hetzner-cloud-shared-plans.txt`) |
|---|---|---|---|---|
| 1 | 2.4 cores, 1.9 GiB | Cloud VPS 4 (4 vCPU, 8 GB) EUR 5.50 = **USD 6.27** | CCX23 (4 vCPU, 16 GB) EUR 86.49 = **USD 98.60** | CPX32 (4 vCPU, 8 GB) EUR 35.99 = **USD 41.03** |
| 3 | 5.2 cores, 4.7 GiB | Cloud VPS 8 (8 vCPU, 24 GB) EUR 14.00 = **USD 15.96** | CCX33 (8 vCPU, 32 GB) EUR 138.99 = **USD 158.45** | CPX42 (8 vCPU, 16 GB) EUR 69.99 = **USD 79.79** |
| 5 | 8.0 cores, 7.5 GiB | Cloud VPS 12 (12 vCPU, 48 GB) EUR 25.00 = **USD 28.50** | CCX43 (16 vCPU, 64 GB) EUR 276.49 = **USD 315.20** | CPX52 (12 vCPU, 24 GB) EUR 100.99 = **USD 115.13** |

Caveats: Contabo's page does not say whether its vCPUs are shared or dedicated and its price is the 24-month-term price; our own measurement already showed that the shared box runs hot, so treat "cheap" as the optimistic price and Hetzner dedicated as the predictable one. Hetzner's page marked some plans "currently unavailable" in some locations, prices are excluding VAT, and the page lists an older shared series at higher prices; I used the newer series. The shared Hetzner plan is cheaper than dedicated but shares CPU with neighbours.

### 2.3 Monthly cash totals

**Formula, hosted:** `hours x rate + seats`. Peak concurrency does **not** enter a hosted bill (it only has to stay under the vendor's limit). **Formula, self-hosted:** `machine rent for the bots at once + seats`; hours do not change the rent (they only decide whether the machine is busy enough to keep up). Our OpenAI cost is excluded everywhere. Storage is taken as 0 for the hosted vendors because we download the audio and delete the vendor copy inside the free period (a rule we would have to build), and as "disk we already pay for" for self-hosting (audio size per hour is **unmeasured**).

**Hosted, any peak concurrency up to the vendor's limit** (guest = no Google seats; signed-in adds the 15.50 for two seats):

| Option | Rate | 50 h | 200 h | 1,000 h |
|---|---|---|---|---|
| Skribby, audio only | 0.35 x H | **17.50** | **70.00** | **350.00** |
| Skribby, signed-in with 2 Workspace seats | 0.35 x H + 15.50 | 33.00 | 85.50 | 365.50 (Gmail accounts: same as guest) |
| Recall, own transcript kept elsewhere | 0.50 x H | **25.00** | **100.00** | **500.00** |
| Recall, signed-in (Workspace is required) | 0.50 x H + 15.50 | 40.50 | 115.50 | 515.50 |
| Recall with its transcript on | 0.65 x H | 32.50 | 130.00 | 650.00 |
| Vexa hosted, bot only | 0.30 x H | 15.00 | 60.00 | 300.00 |
| Vexa hosted, with transcript (what we need) | 0.50 x H | **25.00** | **100.00** | **500.00** |

Free quotas not subtracted: Recall's first 5 hours (once), Vexa's USD 2. Volume discounts and Recall's startup rate (USD 0.25 per hour for the first 10,000 hours, if we qualify) are real but **unverified for us**.

**Self-hosted, hours by bots at once** (cheap-option rent from 2.2; "n/a" means it cannot work; seats: Lite has no signed-in mode, so the guest column adds nothing, and Compose signed-in adds one seat of 7.75 per bot at once):

| Peak bots | 50 h | 200 h | 1,000 h |
|---|---|---|---|
| **A, current server** | 1 bot: **0 extra** (box 5.83 a month already paid, shared) | 1 bot: **0 extra**, but 200 h is 28% of every hour of the month on one bot: only works if meetings never overlap | n/a: 1,000 h needs about 6 bots at once in office hours (see below) |
| 1 (B, own machine) | 6.27 (signed-in: 14.02) | 6.27 (14.02) | n/a |
| 3 (B) | 15.96 (signed-in: 39.22) | 15.96 (39.22) | n/a at 3 |
| 5 (B) | 28.50 (signed-in: 67.26) | 28.50 (67.26) | 28.50 to 315 *(5 bots busy a quarter of every hour in the month is 900 h, so 1,000 h wants 6 or more)* |

**Read the grid with this in mind:** the peak and the hours go together. Spread over about 22 office days of 8 hours (176 h) *(estimate)*, 50 h is about 0.3 bots on average, 200 h about 1.1, 1,000 h about 5.7, and meetings cluster at the top of the hour, so peaks run two to three times the average. Cells such as "1,000 h on 1 bot" cannot happen.

### 2.4 What the money says

| Comparison | Result |
|---|---|
| Cheapest cash at every size | Self-hosting (rent only). At 200 h a month, Recall (100) costs 84 more than a 3-bot Contabo machine (15.96) *(arithmetic from the tables)* |
| What your time does to that | At USD 40 per hour (the earlier note's assumption) 84 buys **2.1 hours a month**. Self-hosting on its own machine is estimated at 4 to 8 hours a month (section 3), so **at 200 h a month hosted is cheaper once your time counts**. The break-even is roughly **600 to 1,000 recorded hours a month for Skribby** (0.35 x H = 4 to 8 h x 40 = 160 to 320 plus the machine) and 400 to 650 for Recall *(estimates, they move with the 4 to 8 hours guess)* |
| What a vendor could save us | A hosted bot that gives us per-participant names in its own events might let us switch Vexa's live `whisper-1` captions off (USD 0.36 per hour, *earlier note*). Not counted; needs a live check |
| What is not in any number | Our time, the bake-off itself, the privacy-policy rewrite for a new sub-processor, and the cost of meetings we fail to record (a refused bot is an unrecorded meeting) |

---

## 3. Stress and capacity

### 3.1 How many bots at once, and what limits it

| | Limit | Label |
|---|---|---|
| **A: current server** | **1 bot.** One bot = about 1.4 cores + 1.4 GiB on a 4-core, 7.8 GiB box already at load 4.1. The `vexa` container is capped at 3 GiB: Vexa idle 0.54 GiB + 2 bots (2.8) = 3.3 GiB, over the cap, so the second bot is likely killed before the CPU runs out. Lite also has one shared screen | measured (1 sample) + estimate; Vexa's statement is vendor-stated |
| **B: own machine, Compose** | about 1 bot per 1.4 cores *(estimate)*: 3 bots in 8 cores, 5 in 12. Per-bot container isolation. **Never measured** | estimate, unverified |
| **B2: Vexa hosted** | "Unlimited bots" on Build on Vexa; Individual plan: 1 | vendor-stated (`vexa-pricing.txt`). No concurrency number published; not tested |
| **C: Recall** | No ceiling on the pages I read. Each bot "runs on its own dedicated machine". **Two kinds of bot:** a bot created with a join time **more than 10 minutes ahead** is a *scheduled bot*: it reserves a machine, "always joins on time", never fails for lack of capacity, no extra charge. A bot created **less than 10 minutes** ahead is an *ad-hoc bot* from a shared warm pool per region; the pool "can still be drained", and then the call fails with **HTTP 507**: wait and retry every 30 s or use the `Retry-After` header. Each workspace has a concurrent ad-hoc limit shown in its dashboard under Developers, Rate Limits (the number is behind a login: **unknown to me**). API rate limits per workspace: create bot 120 a minute, retrieve 300, list 60; over the limit gives 429 with `Retry-After`. Google sign-in: about 50 concurrent bots per Google login in their tests, with Recall's own buffer kicking in at 30; when every account is full the bot fails with `google_meet_login_not_available`. Zoom free accounts limit simultaneous meetings | vendor-stated: `recall-reference-errors.md`, `recall-docs-google-meet-faq.md`, `recall-docs-zoom-signed-in-bots.md`, `recall-reference-bot-create.md` |
| **D: Skribby** | Per **organisation**, not per key or account: Free 3, Pay As You Go 25, "PAYG+" negotiated. Bots in booting, joining, waiting, recording, leaving, processing or transcribing all count; scheduled and finished bots do not. Over the limit the create call fails **at once with HTTP 403** ("You have reached the limit of concurrent meeting bots for your plan"); "no bot is created or queued, and Skribby does not retry". Per Google account: no Skribby cap; "about 30 active bots per Google account" is their conservative planning figure; at least two accounts advised | vendor-stated: `skribby-guide-bot-authentication.txt`, `skribby-guide-billing.txt` |

**A design finding for Recall that matters to us:** our auto-join sends the bot **60 seconds before the start** (`app/config.py:129`, `autojoin_lead_seconds`). On Recall that makes every calendar join an *ad-hoc* bot and exposes us to the 507 pool-drain case at busy times (Monday 09:00). Using Recall well means creating the bot 10+ minutes ahead as a scheduled bot, and updating or deleting it if the meeting moves (Recall has "Update Scheduled Bot" and "Delete Scheduled Bot" calls, listed in `recall-docs-llms-index.md`). Our runner re-reads each event two minutes before the start (`app/join_runner.py`), so this is a real design change, not a switch of a URL.

### 3.2 What breaks first, and what a spike looks like

| | What breaks first | A spike (say 5 meetings start at 09:00) | Label |
|---|---|---|---|
| A | Memory cap of the `vexa` container, then CPU (the other two projects slow down) | 1 meeting gets a bot; the other 4 get "busy, try in a few minutes" and are **not recorded**: the app has no queue by design | measured behaviour of our code (`app/placement.py`) + estimate |
| B | CPU on the bot machine; at 5 bots on 8 cores you are at about 100% before any transcription burst | Up to the machine's limit all join; extra ones refused the same way. With Phase D several machines can share | estimate |
| B2 | Not stated | Not stated | unverified |
| C | Ad-hoc pool (507) if bots are created late; then the workspace ad-hoc limit | Late-created bots may get 507 and must be retried every 30 s; scheduled bots are unaffected | vendor-stated |
| D | The organisation's concurrency number (25 on pay-as-you-go), counted **including bots still processing**, so a busy hour keeps counting after calls end | The 26th create gets 403 immediately; we must queue and retry ourselves | vendor-stated |

### 3.3 How each option fails

| Failure | A and B (we run it) | B2 / C / D (a vendor runs it) |
|---|---|---|
| **Google changes the Meet page** | The bot stops joining until Vexa releases a fix and we pull it. Releases land every few weeks (v0.12.22 on 2026-08-12 up to v0.12.28 on 2026-10-09; the join fix v0.12.29 on 2026-10-30 was a commit and a Docker tag, not a release) *(vendor-stated: releases file)*. We can patch the bot ourselves, as people do for #1725 | The vendor fixes it for all customers. How fast: **unstated** by all three; Recall's and Skribby's docs pages show recent update dates, which is only a sign they are maintained |
| **A shared-IP or datacenter block** | One address, shared with Haven and Novex. If Google scores it badly, every join suffers and **nothing rotates it**. Vexa has no proxy setting we found (*earlier note*) | Skribby says it retries a blocked bot up to 3 times "rotating IP range and fingerprints" and that datacenter ranges are more likely blocked; Recall's pages say nothing on rotation, only that bot name and host settings cause `google_meet_bot_blocked`. B2 is a single Linode datacenter |
| **Vendor or platform outage** | Only our own: a Docker or disk problem, noticed when a meeting is not recorded. Nobody is on call (you are the only person) | Outage is theirs and we cannot fix it. Recall's price table lists "uptime guarantees" as a Launch/Enterprise feature; the stripped text shows a dash in the pay-as-you-go column, so assume none *(vendor-stated, my reading of a flattened table: unverified)*. Recall's and Skribby's status pages: Recall's would not connect for me; Skribby's footer says "Loading status": **I read neither** |
| **A hot shared server** | **Yes, this one is ours alone.** One extra bot takes about 1.4 of 4 cores from Haven and Novex; no CPU cap exists in compose | Not applicable |
| **Signed-in account trouble** | A Google security check kills the saved session; the bot then reports `auth_session_missing` until someone runs `make login` | Skribby's own troubleshooting table: Google may ask for an unsupported check and you must sign in to the account yourself outside Skribby and clear it; Recall: SSO certificate rotation or a wrong assignment breaks sign-in, and "Ready" does not prove it works *(earlier note)* |
| **Vendor changes terms or ownership** | n/a | Skribby: acquired by Alianza (about 2026-09-10, *earlier note*; footer "An Alianza Company" read today). Recall: the legal entity is Hyperdoc Inc. (read); raised USD 38M in Sept 2025 *(earlier note)* |

---

## 4. Upkeep, hurdles, lock-in, data protection

### 4.1 Who does what

| Chore | A | B | B2 Vexa hosted | C Recall | D Skribby |
|---|---|---|---|---|---|
| Patch Chrome and the bot image | us | us | Vexa | Recall | Skribby |
| Follow Meet, Zoom, Teams page changes | us (wait for Vexa, or patch) | us | Vexa | Recall | Skribby |
| Upgrade Vexa (with schema fix, tag pin, never during a live meeting) | us (`deploy/README.md`) | us, plus a Compose migration | Vexa | n/a | n/a |
| Watch for failed joins | us | us | us (our own alert) | us (webhooks) | us (webhooks or polling) |
| Rotate IPs | nobody | nobody | nobody | not stated | Skribby (retries) |
| Keep signed-in accounts healthy (two-step, checks, keep-warm) | n/a on Lite | us, one account per bot at a time, and no keep-warm job exists | not stated | us: the Workspace, SSO certificate, 30-per-login rule | us: Gmail or Workspace accounts, TOTP key stored at Skribby |
| Backups | us (`deploy/backup.sh` exists) | us | Vexa (12 months audio stated) | Recall for 7 days, then us | Skribby for 1 week (we download) |
| Be on call | you | you | nobody on our side, but nobody for a missed meeting either | same | same |

**Hours a month, my estimate, label it so:** A 2 to 4 (the earlier note used 2, 3, 5 at 50, 200, 500 hours); B 4 to 8 (Compose, patched image, account upkeep, monitoring); B2 0.5 to 1; C 1 to 2, plus a one-off half to one day for Workspace and SSO *(earlier note)*; D 1 to 2. At USD 40 an hour that is, per month, A 80 to 160, B 160 to 320, vendors 20 to 80. These are guesses; replace them with your own.

### 4.2 Lock-in and how big is a swap in our code

There is **no bot-layer boundary today**. The design for one (`BotProvider`, checklist item B1, plan section 3.3) is written and not built. What exists, counted in the repo (*measured*):

| What | Where | Size |
|---|---|---|
| The only Vexa client | `app/vexa.py`, 347 lines: link parsing for Meet and Zoom, then five calls: `send_bot` (`POST /bots`), `get_meeting` and `get_meeting_by_id` (`GET /transcripts/...`), `stop_bot` (`DELETE /bots/{platform}/{id}`), `delete_recording`, `download_audio` | small, one file |
| Who calls it | `VexaClient` appears in `app/pipeline.py` (109 lines that mention Vexa), `app/placement.py` (46), `app/services.py` (43), `app/deletion.py` (36), `app/join_runner.py` (15), `app/gcal_sync.py` (11), `app/worker.py` (6), `app/api.py` (13), `app/web.py` (9), `app/models.py` (4) and a few one-liners. About 360 lines across 17 of the app's files, out of 29,400 lines in `app/` | medium spread |
| Vexa's words inside our logic | `app/pipeline.py:213-223` reads Vexa's status words (`active`, `needs_help`, `awaiting_admission`), its `completion_reason` and the failure text `zoom_requires_rtms` (`pipeline.py:136`); `app/notes.py` reads Vexa's live `segments` as "Speaker: text" to name speakers | the real work: translate another vendor's states and transcript shape |
| The database | two columns on `meetings`: `vexa_meeting_id`, `vexa_recording_id` (`app/models.py:665,675`) | small migration |
| Rules that assume one Vexa | "one API key keeps one bot per meeting code" (`placement.py`); a single host (`bot_max_local`) | changes with Phase D |
| Tests | 52 test files mention Vexa; a dedicated `tests/test_vexa.py` | rewrite or wrap |

**Estimates of effort *(mine)*:** B1 seam, no behaviour change: one session (the plan's own estimate). A second backend behind it: **Skribby** about 1 to 2 sessions (its four calls match ours one to one: `POST /bot`, `GET /bot/{id}`, `POST /bot/{id}/stop`, `DELETE /bot/{id}`), **Recall** about 2 to 3 (webhook receiver, scheduled-bot timing from 3.1, transcript mapping, mixed MP3 download), **Vexa hosted** about a few hours (change `VEXA_BASE_URL` and `VEXA_API_KEY`, `app/config.py:12-13`; then prove the five routes on the free credit). Lock-in is low in data (we keep our own audio, transcript and notes) and moderate in code until B1 exists.

### 4.3 Data protection: a vendor becomes a sub-processor

| | A / B | B2 Vexa hosted | C Recall | D Skribby |
|---|---|---|---|---|
| Does the recording leave our server? | No (OpenAI already receives audio for transcription) | Yes | Yes | Yes |
| Where | our server | Seattle, USA only; Cloudflare global for transcription | **US West, US East, EU (Frankfurt), Japan**; an account works in one region only; on pay-as-you-go a move needs a new account *(vendor-stated, earlier note: `recall-docs-regions.md`)* | **EU default** (Germany hosting, Ireland AWS), Japan isolated region; US/Japan for bot instances by region |
| Agreement | n/a | DPA linked in footer, **not read** | **Public EU and UK Data Processing Addendum** with Hyperdoc Inc.: sub-processor list at security.recall.ai/subprocessors with notice and a 10-day objection window, return or delete data at our choice, audit rights *(read: `recall-data-processing-agreement.txt`)* | A DPA is mentioned by an earlier note; I found **no DPA page** (`skribby.io/dpa` and `/security` returned 404). Sub-processor list is public *(read: `skribby-sub-processors.txt`, updated 2026-08-12)* |
| Certificates | none | none seen | Privacy page links a SOC 2 Type 2 report and an ISO 27001 certificate on `security.recall.ai` (a trust portal that loads by script, so **I could not read the reports or the live sub-processor list**) | None named on pages read; the privacy policy lists measures (TLS 1.2+, AWS encryption at rest) |
| Retention default | ours | 12 months audio | **Keep forever** on accounts made after 2025-06-12 unless we set `recording_config.retention` (timed hours, forever, or none); our code must set it to a short period. Deleting does not delete data held by third-party transcription providers | recordings 1 week (1 year with add-on); transcripts while the account is active unless deleted; **debug data including screenshots 30 days even after a deletion request** |
| Sub-processors that see audio | OpenAI (already) | Cloudflare, Linode | list not readable (script-loaded page) | hosting (Hetzner, Linode, AWS) plus, if a vendor transcript is used, up to 12 transcription providers, several US-only; `none` (audio only) avoids those |
| Sub-processors for the bot account | none | none | Google Workspace holds the bot's account | Skribby stores the **bot account's password and authenticator key** |

Each hosted vendor would need a line in our privacy policy as a new sub-processor, and your earlier decision (no notice of policy changes) means the wording must not promise notice *(memory: policy changes, no notice)*.

---

## 5. Zoom and Teams

### 5.1 What each option needs today

| | Zoom | Teams |
|---|---|---|
| **A / B: Vexa (self-hosted)** | Joins as a guest through Zoom's web page (v0.12 "joins Zoom via web client only"; OBF/ZAK "not carried") *(vendor-stated: `vexa-docs-roadmap-status.md`)*. Worked in our spike on three short calls, including one admitted from a waiting room *(measured)*. A host who requires signed-in users blocks it. Some meetings return `zoom_requires_rtms`, and our code already maps that (`pipeline.py:136`); what triggers it I have not read | Vexa lists Teams as supported *(vendor-stated: pricing page)* but **we have never tested it** (your brief). Signed-in Teams "validates on its own wave" |
| **B2: Vexa hosted** | same bot, same limits | same |
| **C: Recall** | **"Only requires a meeting URL"**; no setup *(vendor-stated: `recall-docs-zoom-overview.md`)*. Waiting room applies; only host or co-host can admit. A host who sets "Only authenticated users can join" gives the bot `meeting_requires_sign_in`; a signed-in Zoom bot "is available on pay-as-you-go" but you must **email support**, no self-service steps (`recall-docs-zoom-signed-in-bots.md`). Not supported: Zoom Gov, Webinar Plus, Video SDK sessions, interpreter channels and others | **"No setup needed"** for a guest bot; default lobby, so the host must admit it. Hosts can disable anonymous join entirely. Signed-in Teams: a **new, separate Microsoft 365 Business tenant** (Recall's guide says buy Business Basic), security defaults turned off in Entra ID, bot name taken from the account, personal `teams.live.com` links fail when sign-in is mandatory *(vendor-stated: `recall-docs-setting-up-signed-in-bots-for-microsoft-teams.md`, `recall-docs-microsoft-teams.md`)*. Unsupported: registration-required meetings, live events, town halls, direct calls, GCC High, breakout rooms |
| **D: Skribby** | Guest by default; authenticated with a **ZAK token** we generate per user through Zoom's OAuth (tokens last 2 hours for regular users); Skribby **does not accept an OBF token**; a granular scope `user:read:zak` makes joins fail as `not_admitted` (use the classic scopes) *(vendor-stated: `skribby-guide-bot-authentication.txt`)*. We would need our own Zoom OAuth app; whether Zoom reviews it for ZAK use: **unverified** | Guest joins "vary with the host organisation's external access policies"; authenticated needs an **Azure AD account** and **MFA must be off** on it *(vendor-stated: `skribby-guide-platforms.txt`, `skribby-guide-bot-authentication.txt`)* |

### 5.2 The platforms' own rules

| Platform | What the primary pages say |
|---|---|
| **Zoom** | Since **2 March 2026** (the blog; the changelog said 23 February; the "FAQ" page that explains the change returned 404 for me) Meeting SDK apps joining meetings outside their own account must be authorised with an OBF or ZAK token, or use RTMS; "apps will no longer be able to join meetings outside their own account anonymously". An OBF token comes from the user's OAuth consent and needs a Marketplace review for apps without the `user:read:token` scope. Zoom's Meeting SDK home page says the SDK "is designed for human use cases and does not support bots or AI notetakers" and points notetakers to **RTMS**, "instead of having participant bots in meetings" *(vendor-stated: `zoom-obf-blog.txt`, `zoom-changelog-auth.txt`, `zoom-meeting-sdk-home.txt`, `zoom-rtms.txt`)* |
| **Teams** | "Anonymous users can join a meeting" is **On** by default; "Who can bypass the lobby" defaults to **People in my org and guests**, so an anonymous bot waits in the lobby until an organiser or presenter admits it; "Anonymous users and dial-in callers can start a meeting" is **Off**. An organisation that turns anonymous join off blocks every guest bot, "regardless of lobby settings" *(vendor-stated: `microsoft-teams-meeting-settings.txt`, `microsoft-teams-lobby.txt`)*. By default a Teams bot shows "Unverified" after its name *(Recall FAQ citing Microsoft)* |
| **Google Meet** | Meeting access Open / Trusted / Restricted. In a Trusted meeting, external guests **invited through the Calendar event and signed in** skip the knock; in Restricted, anyone invited through the event does; uninvited people knock, and a host can switch knocking off *(vendor-stated: `google-meet-join-without-asking.txt`)* |

What we have not been able to say about Zoom: **how Recall and Skribby join Zoom** (through the SDK with an approved app, or through a browser) is not stated on the pages I read, so whether Zoom's tightening hurts them differently from us is **unverified**. Vexa joins through the browser page, which is not the SDK the March rule names; whether Zoom treats a browser bot differently is **unverified**, but the `zoom_requires_rtms` messages show Zoom already turns some of them away.

### 5.3 Is Google Meet the hardest platform for a bot?

**Plainly: it is the hardest on randomness, not proven the hardest overall.** My reasoning:

- Meet is the only platform where we have a measured, unexplained refusal rate for a guest bot (about 1 in 4, from Vexa; our own five bake-off trials are too few). Google refuses some guest joins with no host involvement, so retries and a signed-in account help, and the refusal is not something the host can fix *(earlier note)*.
- Zoom and Teams refusals look **rule-based**, not random: the host's waiting room, "only signed-in users", a tenant that turns off anonymous join, an app-approval rule. They fail the same way every time for a given host, so they are easier to explain and to message to the user but cannot be fixed by retrying. For business customers on Teams I would expect the biggest share of "cannot join at all", because one admin switch blocks every guest.
- Zoom's direction (authorised apps, RTMS for notetakers) is the largest **long-term** risk for a bot that joins as a guest, and it falls hardest on a self-hosted one.
- **We have no data on Teams at all, and Zoom only on three short calls.** No vendor publishes a success rate on any platform.

---

## 6. Stress-test plan (design only; nothing was run)

**Rule:** never touch `haven-respite` or notes.beyrictech.com. Both tests use throwaway machines and test meetings. Adebayo makes any accounts and keys.

### 6.1 Self-hosted Vexa: how many bots can one machine carry

**Questions it answers:** does Lite run two bots at all; how many bots does a machine carry in Compose; what breaks first; what does it cost.

**Machine (rent by the hour, delete after):** Hetzner Cloud bills hourly and "will never exceed its monthly price cap" *(vendor-stated: `hetzner-cloud-shared-plans.txt`)*. Contabo's page does not show hourly billing, so it cannot serve as a throwaway. Two sizes, one after the other, to see shared against dedicated CPU:

| Size | Price per hour (excl. VAT) | In USD | A 6-hour session |
|---|---|---|---|
| CPX42, 8 shared vCPU, 16 GB | EUR 0.1122 | about 0.13 | about USD 0.77 |
| CCX33, 8 dedicated vCPU, 32 GB | EUR 0.2227 | about 0.25 | about USD 1.52 |

(Some plans were marked unavailable in some locations on the page; if so use CCX23 at EUR 0.1386 or CPX52 at EUR 0.1618.) Choose a European location. **The join success you measure here does not transfer to production**, because a Hetzner address is a different datacenter address from Contabo's: this test is for CPU, memory and stability; the bake-off is for join success.

**Steps (a script a session can write once you approve; not written now):**
1. Create the machine, install Docker, clone Vexa at the tag we run, run `make lite` for stage 1 and `make all` (Compose) for stage 2. Use a test API key; never paste it in chat.
2. **Stage 1, Lite:** send **2 bots** into 2 different test meetings. Expected from Vexa's docs: it may not work (one shared screen). Record yes or no.
3. **Stage 2, Compose, ramp:** for N = 1, 2, 3, 4, 5, 6: send N bots, each to its own test meeting, hold 20 minutes, stop, wait 5 minutes, next N. Then a **spike**: 5 bots sent within 10 seconds. Each test meeting needs its own host (Vexa keeps one bot per meeting code), so Adebayo needs N Google accounts to host (free Gmail is fine) *(whether one account can host two Meets at once is **unverified**; use separate browser profiles)*. In each meeting play a podcast or recorded speech by sharing a tab with sound, so the bot has real audio. Press Admit for each knock.
4. **Record, every 5 seconds to a file:** `docker stats --no-stream` for every container, `uptime` load average, `free -m`, plus for each bot: the time `POST /bots` was sent, the time the status first became `active`, the final `completion_reason`, and the status transitions. Keep a table per step.
5. **After each step:** download each recording (`/recordings/{id}/media/{media_id}/raw`), check its length against the 20 minutes, and run `ffmpeg -af silencedetect` to count audio gaps; compare the transcript's word count per minute against the source speech; listen to one in three.
6. **Stop rule:** stop at the first step where sustained CPU is over 85%, memory is over 85%, a join fails, or audio gaps appear. The capacity is the step before.

**What it costs *(estimate)*:** machine USD 1 to 3 for both sizes; our OpenAI live captions on 21 bot-runs of 20 minutes = 420 bot-minutes at USD 0.006 a minute (the `whisper-1` rate read in `docs/spike/hosted-pivot-gladia-recall-cost.md`) about **USD 2.50**, or 0 if captions are switched off for a pure load run (the machine does not do the transcription itself, so load should be close); about 4 hours of Adebayo's time plus helpers. **Total under USD 6 plus 4 hours.**

**Decision it feeds:** the measured bots-per-core number replaces the 1.4-cores estimate in section 2.2 and sets `bot_max_local` for Phase D (checklist D9b).

### 6.2 A hosted vendor: does it really carry its stated limit

**Aim:** measure the vendor's concurrency limit and what happens at it, inside the free hours.

| | Skribby (Free plan) | Recall (first 5 hours free) |
|---|---|---|
| Stated limit | 3 bots at once; 5 bot hours a month; audio only | no number on the pages; the dashboard (Developers, Rate Limits) shows the workspace's ad-hoc limit; create bot is 120 calls a minute |
| Test | Read the limit first (free to look). Then create **4 bots within a few seconds**, each into a different test meeting. Expect bots 1 to 3 to be created and bot 4 to fail **at once with HTTP 403** and the words "You have reached the limit of concurrent meeting bots for your plan". Then stop one and retry to see how fast capacity returns (bots still processing keep counting) | Read the ad-hoc limit L from the dashboard. Create L ad-hoc bots (join time under 10 minutes), then L + 1, and record the code (507 or 429) and `Retry-After`. Repeat with **scheduled** bots (join time 15 minutes ahead) to see that they are never refused |
| Cost control | Bots that are not admitted within 10 minutes are free, so leave the extra bots unadmitted; admit only one to measure join time. Billing runs from creation, so stop each bot within a minute: 4 bots x 1 minute = about 4 minutes of the 5 hours | Recall bills from the first load of the link and counts waiting-room time, but not bots that end `fatal`; stop each bot after one minute: about 5 to 10 minutes of the 5 hours |
| Record | HTTP status and body of each create call, time to `joining`, time to `recording`, final status, any `bot_detected` | the same, plus `bot.joining_call` to `bot.in_call_recording` times from the webhooks |
| Free-hour cost | about 0.1 h of 5 | about 0.2 h of 5 |

Mark any observation about **how many Google joins succeed** as a separate bake-off number; this test measures the vendor's limit, not Google's mood. Adebayo signs up and creates keys; neither of us pastes keys in chat.

---

## 7. Recommendation

### 7.1 For now

1. **Stay on A for the pilot.** One bot at a time, one or two testers, no new spend. The extra cash of anything else is small, but the decision is better made on bake-off numbers than on guesses, and there is nothing to gain by moving a guest bot to a bigger datacenter machine.
2. **Run the bake-off to at least 15 trials per arm, and add Zoom and Teams.** Five trials said nothing. How many is enough *(my Fisher exact test arithmetic)*: with 10 per arm only a huge gap shows (50% against 100% gives p about 0.03; 73% against 100% does not, p about 0.21). With 30 per arm 73% against 100% shows clearly (p about 0.005), but 73% against 95% is still borderline (p about 0.08). The bake-off tool reports its own threshold; use whichever is stricter. Add about 5 Zoom and 5 Teams trials per arm, including a Teams meeting hosted by someone outside our organisation with the default lobby.
3. **Build the seam (B1) regardless.** It costs one session and turns every later switch from a rewrite into a configuration change.
4. **Run the stress test (6.1) before inviting more than two testers, and before buying any machine.** It costs about USD 6 and four hours, and it tells you whether Phase D is "buy a bigger machine" or "go hosted".

### 7.2 Triggers to switch

| Trigger | Move | What it costs Adebayo (time, besides money) |
|---|---|---|
| Bake-off: a hosted bot (signed-in and invited, or even guest) gets in at least about 10 points more often than Vexa guest across at least 30 trials, **or** Vexa guest is below about 90% over its first 30 trials | **Skribby first**: cheapest (USD 0.35 an hour), easiest signed-in (a Gmail account plus an authenticator key, no Workspace), its four calls match ours, EU default region. Take **Recall** instead if a DPA, SOC 2 and ISO paperwork matter to the first customers more than easy setup: Recall is the only one with a public DPA and linked reports, but a signed-in bot needs a dedicated Workspace and SSO and scheduled bots (3.1) | about 1.5 hours for the bake-off setup, 30 minutes to create the bot account and 2-step, one or two sessions of build, and a privacy-policy line; Recall adds a half to one day of Workspace and SSO setup *(earlier note)* |
| Real peak is 2 or more bots at once, or a spike leaves meetings unrecorded | **Compare B (after the stress test) with hosted at your real hours.** At about 200 hours a month hosted wins once your time counts (2.4); at about 600 to 1,000 hours a month or more, and if you can spare 4 to 8 hours a month, your own machine wins | stress test 4 hours; Phase D build; 4 to 8 hours a month |
| Zoom or Teams customers appear and our bot is refused for rule reasons | A hosted vendor carries Zoom's approvals and Teams' account setup; self-hosted would need our own Zoom Marketplace app and, for RTMS, Zoom's review | weeks of approvals if self-hosted, **unverified** |
| Vexa merges #1873 and publishes a release | Re-try signed-in on Compose on a throwaway machine (plan A1) | about half a day |
| The privacy answer is "recordings must not leave our server" | Stay self-hosted (A or B) and accept the refusal rate | none, a product trade |

I would **not** choose Vexa hosted (B2) unless reliability turns out not to matter: it keeps the refusals, keeps recordings in one US region and adds a Cloudflare hop, and costs the same as Recall.

### 7.3 What the bake-off must show before the decision

1. Join success, time to the door and knock rate per arm with at least 15 (better 30) trials, on the same mix of host types and meeting access settings, one arm per meeting.
2. **Signed-in and invited** against guest on Skribby: does inviting the bot account to the calendar event really skip the knock and the refusal? (No vendor publishes this.)
3. At least a few Zoom and Teams joins per arm, with the host settings written down.
4. Whether Skribby's Free plan allows a signed-in account at all (its pages do not say; the bake-off README already lists this).
5. Anything else the bake-off finds about Google security checks on a new bot account.

### 7.4 What is still unknown

| Unknown | Why it matters | How to settle it |
|---|---|---|
| Can our Lite stack, or Compose, run 2 or more bots? bots per core | sets whether Phase D is worth it | stress test 6.1 |
| Google Meet join success of Skribby, Recall, Vexa hosted | the whole decision | bake-off |
| Whether Vexa hosted accepts our five API calls unchanged | would make B2 a one-line switch | USD 2 free credit |
| Recall's ad-hoc limit number and what its pool does at 09:00 on a Monday | decides if scheduled bots are mandatory | dashboard, test 6.2 |
| Teams and Zoom behaviour of any option at scale | future customers | bake-off extension |
| Whether Recall can use a Gmail bot account (its assistant says yes, its guide implies no) | decides whether Recall needs a Workspace | Recall support's written answer (questions already drafted in `recall-support-chat-2026-11-07.md`) |
| Real audio size per hour and whether keeping 12 months of audio is affordable on our disk | storage cost | measure in the stress test |
| How Recall and Skribby join Zoom (SDK or browser) | tells how exposed they are to Zoom's rule change | ask both in writing |
| Skribby's DPA and certifications | privacy paperwork | ask in writing (questions already in `meeting-bot-vendors.md`) |
| Recall's SOC 2 report and live sub-processor list | privacy paperwork | request through the trust portal |

---

## Pages read (all in `docs/spike/primary-pages-2026-11-07/`, each starts with its URL and fetch date)

| Topic | Files |
|---|---|
| Vexa hosted and docs | `vexa-pricing.txt`, `vexa-docs-llms.md`, `vexa-docs-deployment-lite.md`, `vexa-docs-authenticated-bots.md`, `vexa-docs-roadmap-status.md`, `vexa-docs-quickstart.md`, `vexa-site-privacy.txt`, `vexa-subprocessors.txt` |
| Vexa GitHub (API responses) | `vexa-github-issue-1725.json`, `vexa-github-issue-1725-comments.json`, `vexa-github-pr-1873.json`, `vexa-github-pr-1873-comments.json`, `vexa-github-releases.json`, `vexa-github-repo.json` |
| Recall | `recall-docs-llms-index.md`, `recall-pricing-reread.txt` (also the earlier `recall-pricing.txt`), `recall-docs-calculating-usage.md`, `recall-reference-errors.md`, `recall-docs-google-meet-faq.md`, `recall-docs-google-meet-login-getting-started.md`, `recall-docs-zoom-*.md`, `recall-docs-microsoft-teams*.md`, `recall-docs-teams-bots-bypass-the-lobby.md`, `recall-docs-personal-vs-business-ms-teams.md`, `recall-docs-storage-and-playback.md`, `recall-data-processing-agreement.txt`, `recall-privacy.txt` |
| Skribby | `skribby-pricing-reread.txt`, `skribby-guide-billing.txt`, `skribby-guide-bot-authentication.txt`, `skribby-guide-platforms.txt`, `skribby-guide-bot-detection.txt`, `skribby-guide-regions.txt`, `skribby-guide-transcription-privacy.txt`, `skribby-guide-team-management.txt`, `skribby-guide-best-practices.txt`, `skribby-privacy-policy.txt`, `skribby-sub-processors.txt` |
| Zoom | `zoom-meeting-sdk-home.txt`, `zoom-meeting-sdk-auth.txt`, `zoom-obf-blog.txt`, `zoom-changelog-auth.txt`, `zoom-rtms.txt` |
| Microsoft and Google | `microsoft-teams-lobby.txt`, `microsoft-teams-meeting-settings.txt`, `google-meet-join-without-asking.txt`, `google-workspace-pricing.txt` |
| Machines | `hetzner-cloud-shared-plans.txt`, `hetzner-cloud-dedicated-plans.txt`, `contabo-vps.txt` |

**Tried and not read:** Zoom's "FAQ - Updates to Meeting SDK authorization" (404), `skribby.io/terms`, `/dpa`, `/security`, `/status` (404), `vexa.ai/terms` and `/dpa` (404), Recall's trust portal and sub-processor list (load by script, so the saved text is empty), Recall's and Linode's/Vultr's status and pricing pages (no connection or 403). The `.txt` files are tag-stripped text, so pricing tables built by script or tabs could hide content; open the live page before paying.

**Code and notes read:** `app/vexa.py`, `app/placement.py`, `app/config.py`, `compose.yaml`, `docs/spike/cost-and-capacity-2026-11-03.md`, `meeting-bot-vendors.md`, `hosted-pivot-gladia-recall-cost.md`, `vexa-spike-report.md`, `meet-join-resilience.md`, `recall-support-chat-2026-11-07.md`, `tools/bakeoff/README.md`, the Phase D lines of `docs/checklist/master-checklist.md`.
