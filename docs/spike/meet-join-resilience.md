# Spike: a reliable way into Google Meet

**Date:** 2026-11-03. **Type:** research only. Nothing was run against the server, no bot was sent, no account was touched.
**Question:** since 2026-11-03 every anonymous Meet join from the server fails (meetings 8 to 11). Meetings 4 and 5 worked on 2026-11-02. What do we do before calendar auto-join depends on it?
**Evidence labels:** *read* = I read the source, release notes or issue. *vendor* = a vendor's own documentation. *not verified* = nothing here proves it; it needs a live test.

## Summary

| # | Answer | Confidence |
|---|---|---|
| 1 | **Vexa already fixed the exact symptom, and we do not run the fix.** `v0.12.29` (merged 2026-10-30, Docker Hub image `vexaai/vexa-lite:v0.12.29`) treats Google's 403 as a typed refusal, re-knocks up to 3 times, and stops reading the refusal page as a lobby. The tag we run, `v012`, is still the `v0.12.28` image. Upgrade looks safe for `app/vexa.py` and `vexa-schema-fix` | High that the fix exists and matches our logs; **not verified** that it cures our server |
| 2 | Authenticated bots exist in Vexa but **do not work today**: #1725 is open (the bot launches `--incognito` and ignores the signed-in profile), the fix PR #1873 is unmerged, and the docs say **Lite does not support it** (we run Lite). Large effort | High on state; account-risk questions **not verified** |
| 3 | An invited, signed-in attendee skips the knock (Google's admin help, and a vendor does exactly this). It needs the bot account (2) and the bot's address on the event | High on the Google rule; **not verified** for a bot |
| 4 | Cheapest discriminator: a signed-out private window using the exact bot name, then our Vexa clone on Adebayo's Mac on a home connection. Steps in section 4 | Design only, **not run** |
| 5 | Proposed worker limit: at most 2 sends per meeting, at least 4 minutes apart, a circuit breaker after 3 refusals in 30 minutes | Proposal; Google publishes no limit I could find |
| 6 | A hosted vendor (Recall) is cheap at pilot size and becomes the fallback. Meet Media API and add-ons do not change the plan | Medium |

The most useful fact: **Vexa measured this failure at 27% of anonymous joins from a datacenter address** and says it is random per attempt, not a block on the address. Our 5 failures in a row would then be about a 1 in 700 event, so our address or timing is probably worse than theirs. That is the one thing section 4 settles.

## 1. Vexa upgrade

**What we run.** `compose.yaml` pulls `vexaai/vexa-lite:${VEXA_IMAGE_TAG:-v012}`. On Docker Hub (read 2026-11-03) `v012` and `v0.12.28` carry the same digest (`sha256:9eebb534fdab...`), last pushed 2026-10-09. So `v012` is `v0.12.28`, and it has not moved since. Adebayo can confirm the server holds that digest: `docker image inspect vexaai/vexa-lite:v012 --format '{{index .RepoDigests 0}}'` should end in `9eebb534fdab...` (read-only, not run by me).

**Releases since.**

| Release | Date | Meet join relevance |
|---|---|---|
| v0.12.28 | 2026-10-09 | None for Meet. Zoom transcript completeness; Lite and Compose store recordings in versitygw instead of MinIO. Our `compose.yaml` already runs versitygw v1.8.0 (same pin as Vexa's Makefile), so the `make migrate-storage` step does not apply |
| **v0.12.29** | 2026-10-30 | **The fix.** PR #1921 (commit `31122f9`, the same commit our spike clone sits at). Not a GitHub Release; only the merged PR, the commit and the Docker Hub tags `vexa-lite:v0.12.29` (pushed 09:20 UTC) and `vexa-bot:v0.12.29` |
| v0.13.1 (alpha), 0.13.4-rc.x | Sept to Nov | A different line. Release candidates only. Docker Hub has bot tags such as `v0.13.4-rc.2-knocks10` and `-respawn.1` (2026-11-02); I did not read what they do. Not for us |

**What #1921 changes** (read from its description): Google answers some guest joins with HTTP 403 `PERMISSION_DENIED` on one of two calls, `ResolveMeetingSpace` (while the lobby loads) or `CreateMeetingDevice` (right after "Ask to join"). The page then says "You can't join this video call" and the host never sees a request. Before the fix, v0.12.28 failed one way before the click ("Could not locate join button", after about 2.3 minutes) and another way at the click (it read the refusal screen as the waiting room and reported `awaiting_admission` for 15 to 20 minutes). That is **exactly what our logs show**: meeting 10 ended on "Could not locate join button", meetings 8, 9 and 11 polled a dead lobby. After the fix the bot re-knocks up to 3 times within the lobby budget and otherwise fails with a typed `denial`, reason `google_meet_join_refused`, attributed to the platform.

**Vexa's numbers** (their test, 2026-10-30, a Linode datacenter address in Seattle, their own Meet API rooms; same page, fresh context and fresh browser made no difference):

| | Result |
|---|---|
| v0.12.28, 45 joins | 12 refused (27%): 7 before the click, 5 at it |
| With the fix, 32 bots | 8 refused; **all 8 got in within 3 knocks**; 0 refused 3 of 3 |
| Time to the lobby | 13.5 s with no refusal, 25.1 s with one, 40.7 s with two |
| Hosted production, 24 h to 2026-10-30 | at least 28 of 121 Meet bots refused (23%) |

They did **not** run: their production egress addresses, a refused-3-of-3 case live, or signed-in bot accounts. For us this means: the refusal is probabilistic and datacenter-wide, so it is not "Google blocked our server for good". It does not explain why we saw 5 in a row.

**Other issues read.**

| Issue | State (2026-11-03) | Bearing |
|---|---|---|
| #540 "pre-0.12 no-join family" | open epic, updated 2026-10-30 | The anonymous-join failure class. A 2026-07-30 comment records a hosted anonymous join failing after the click with the cause unproven. Humanized OS-level input is already the default in v0.12 |
| #316 "rejection page silently times out" | closed 2026-05-20 | Old (0.10 era); v0.12 fails fast on a rejection screen. Does not cover the 403 case |
| #724 authenticated bots, dormant seam | closed 2026-07-20 | Built the `make login` and `BOT_AUTHENTICATED` flow (section 2) |
| #1725 incognito ignores the session | **open**, confirmed independently 2026-11-02 | Blocks authenticated Meet bots today (section 2) |
| #1655 never-admitted bots mislabelled `system_fault` | open | Their taxonomy, not our join. Useful only to read their statuses |
| #1110 bots give up after about 10 minutes in the lobby | open | The 10-minute budget is Vexa's own constant. Hosts who never admit are most of it. Not the 403 |

A search of the repo for Meet issues created since 2026-10-15 found nothing newer about the 403 beyond #1921 and its task #1118. The same fix landed first on the 0.13 line as PR #1916; #1925 (open) merges main into the 0.13.4 branch.

**Is the upgrade safe?** *Read, not run.*
- **API we call.** The routes in `app/vexa.py` (`POST /bots`, `GET /transcripts/{platform}/{id}`, `GET /transcripts/by-id/{id}`, `DELETE /recordings/{id}`, `DELETE /bots/{platform}/{id}`) all exist in the source at `31122f9`. #1921 describes itself as "bot side only"; meeting-api is unchanged. `needs_help` is still a status.
- **`vexa-schema-fix`.** `transcriptions.language` is still `String(10)` in the model at `31122f9`, so the fix is still needed and the script is idempotent. No migration came with #1921 as far as its description says (I did not diff migrations).
- **Not verified:** that the `vexa-lite:v0.12.29` image contains the new bot (the PR says bot image only; the Lite image is rebuilt on the same day). The Mac test in section 4 (arm B) proves it, because the API then returns `google_meet_join_refused` instead of a lobby wait.
- **Pin the tag.** Use `VEXA_IMAGE_TAG=v0.12.29`, never `:v012`, which Vexa moves. Roll back by putting `v012` back (`deploy/README.md`, "Update Vexa"). Re-run `docker compose up -d vexa-schema-fix` after the swap. Never swap while a meeting is `bot_sent` or `processing`.

## 2. Authenticated bot

**How Vexa does it** (docs page and source read at `31122f9`):
- `make login` opens a headed browser; a person signs in once; Vexa checks the session is really logged in, then uploads the auth-essential files (about 200 KB: cookies, storage, login data) to an S3 prefix, `BOT_USERDATA_S3_PATH`. It needs the `aws` CLI on the provisioning machine and scoped S3 keys.
- `BOT_AUTHENTICATED=true` on meeting-api is **deployment-wide**: every `POST /bots` then spawns signed in. No per-request switch. A bot never silently falls back to anonymous; a missing store is a 503 at spawn.
- Session refresh: Google rotates cookies, so Vexa writes the rotated profile back to the prefix on each **clean** teardown. A killed bot skips the write-back. Idle accounts decay; Vexa advises periodic meetings as keep-warm (no packaged job exists), a stable egress address and a stable bot image.
- **One live bot per session.** A second concurrent spawn on the same prefix gets 409. For N concurrent bots you provision N accounts, which today means N deployments.
- `AUTH_SESSION_MISSING` (typed `completed(auth_session_missing)`, permanent, no retry) is what a signed-out or decayed session reports. Recovery is one command, `make login`.
- **Lite is not supported** (docs, `authenticated-bots.mdx:147`: no userdata store, no provisioning flow). Our stack is Lite. Authenticated mode means the full Compose stack (eleven images in the v0.12.28 release), which the spike avoided because Docker had 2.8 GB on the Mac and the server is shared. This is the main cost.

**#1725.** The bot's join args include `--incognito` (`browser-args.ts:48`), and the authenticated launch adds them to the profile args, so Chromium ignores the restored profile, Meet shows the guest name field and the bot exits `auth_session_missing` in about 10 seconds. A second user reproduced it on stock `vexa-bot:v012` on 2026-11-02 with a controlled test: same profile, with the flag it fails, without it the bot is admitted and transcribes. The fix is to drop `--incognito` when authenticated. **PR #1873 is open, not merged**, and `31122f9` (v0.12.29) still has the flag. So no published image does this today; we would build our own bot image from #1873 (effort and licence handling **not verified**) or wait.

**What we would need.**

| Need | Notes |
|---|---|
| A dedicated Google account | Vexa: "a dedicated account, not a personal one", and its docs lean toward a Workspace (OU session length). **Recall requires a dedicated Google Workspace with standard-user accounts and Legacy SSO** and warns not to use a customer's company Workspace. `beyrictech.com` is not Workspace (handoff). So: either a new Workspace (a paid seat; price **not verified**) or a consumer Gmail account (cheaper; Google's terms on automated use of a consumer account are **not verified**; read them first) |
| Provisioning and refresh | Adebayo (or a session with his hands) runs `make login` on a machine that sends traffic from a stable address. Refresh is the write-back on clean teardown. Needs a keep-warm meeting and an alert on `auth_session_missing`, which today nothing in our worker sees |
| How Google treats a new account that signs in from a datacenter | I found **no authoritative source**. Anecdotes (forums, a GitHub gist) say sign-in through automation tools triggers "this browser may not be secure" and fraud checks, and that a sync tool's first logins from an unusual place trigger alerts that fade. Vexa warns that Google re-challenges sessions that look cloned or switch address or fingerprint. Expect risk. Mitigation to test, not a promise: create and warm the account from Adebayo's home with a phone and recovery email, sign in once through the same address the bot will use, then keep that address stable |
| Product cost | The bot's displayed name and picture become the **account's** (Recall: signed-in bots ignore `bot_name`). Name the account "Beyric Note Taker (recording)". Google's separate "potential risk" notice can still show for a signed-in bot (Recall). The stored session is a credential (cookies on disk, S3 keys inside the bot container's environment per Vexa), so the privacy policy must say the bot signs in to a Google account owned by Beyrichq and where the session is kept; the terms must tell users to invite or admit that account. Recovery: the account needs a recovery email and phone held by Beyrichq, not one person, and a runbook for "Google asked for a re-login" |
| Concurrency | One bot per account. Fine for the pilot (one bot at a time is already accepted). It conflicts with Phase D (several bots) unless each host has its own account |

## 3. Invited-attendee path

Google's admin help ([who can join without asking](https://support.google.com/a/users/answer/12172260)): in a "trusted" meeting, external guests **invited through the Calendar event and signed in to a Google account** enter without knocking; in a "restricted" meeting, anyone invited through the event does. External people not on the event must knock. A host can also switch knocking off, in which case uninvited guests are declined automatically. The pre-join page's own "sign in with the Google account your host invited" fits this. Recall's docs describe the same behaviour for its signed-in bots and add: the bot's email must be on the event (a Google group address with the bot accounts as members also works).

So an invited bot skips the knock that is being refused. **Not verified:** that a bot gets past the 403 this way (Recall says signed-in bots "reduce" blocks, not remove them), and what a host's organisation policy does to an outside account.

How it fits calendar auto-join:

| Option | What it costs |
|---|---|
| **A. The user adds the bot's address to the event** (we show it in settings and on the Upcoming row) | No new scope. Friction; users forget; no effect on pasted links or instant meetings |
| B. We add the bot as an attendee | Needs a calendar **write** scope. The pilot spec (Q1) chose to never write to events, and the scope list is being frozen for Google verification (Phase V), so adding a write scope changes that submission. It also emails an invitation and shows the bot to every attendee. Works only on events the user can edit |
| C. ICS users | A and B both fail (we have no write access to a pasted feed), so A with help text only |

The invite path is a **second layer** on top of a signed-in bot, not a substitute for it: it needs the account from section 2.

## 4. Egress address or browser fingerprint?

Facts that narrow it. The `v012` image has not changed since 2026-10-09, so the code that worked on 2026-11-02 and failed on 2026-11-03 is the same; either Google changed, or the refusal is random and we were unlucky, or our address got worse. Vexa's data (27% random, independent of a fresh browser or context) argues against a pure fingerprint block. Fresh contexts do not change the address, so that test cannot separate address from fingerprint. Recall's FAQ also lists the **bot name** as a block trigger (it names profanity; innocent names can trip it), and tells you to test in an incognito window with the same name. Adebayo's private-window test may not have used our exact name.

**Ladder, cheapest first. Adebayo runs these; I did not.**

| Step | Time | Run | Reads as |
|---|---|---|---|
| **E0** | 2 min | Signed-out private window, join one of his own meetings with the name exactly `Beyric Note Taker (recording)` | Refused: the name (change `BOT_NAME`, done). Admitted: not the name |
| E1 | 10 min | Same, but through the server's address. Open a SOCKS tunnel to the server (`ssh -D 1080 <his user>@<server>`; read-only use of the box) and point a private browser window at `localhost:1080`. Host a meeting, ask to join with the exact name | Refused: **the address is flagged** even for a human. Admitted: the address is fine for humans (bots may still be treated differently). Vexa has no proxy setting I could find, so the bot itself cannot be sent through the tunnel (not verified) |
| **E2 (run first)** | about 2 h | The Mac experiment below | Table below |

**E2 steps** (Vexa clone and its stack: `spike/vexa`, `make -C deploy/lite up`, API on `localhost:8056`, key in `spike/.env.api`; never paste or print the key):
1. Arm A, the failing image. In `spike/vexa/.env` set `IMAGE_TAG=v012`; `make -C deploy/lite down && make -C deploy/lite up`; confirm with `docker ps --format '{{.Names}} {{.Image}}'`.
2. Adebayo hosts one Meet in a signed-in browser with default settings and keeps it open. Send the bot with the same body the app sends (`POST /bots`, `{"meeting_url": "https://meet.google.com/<code>", "bot_name": "Beyric Note Taker (recording)"}`).
3. Watch for up to 3 minutes. Note one outcome: **K** (the host sees "Beyric Note Taker (recording) wants to join": deny it), or **R** (no request reached the host; the bot's log has `403`, `PERMISSION_DENIED` or "The caller does not have permission"; find it with `docker logs <vexa container> 2>&1 | grep -i -E '403|permission|can.t join|refus'`).
4. Stop the bot (`DELETE /bots/google_meet/<code>`). Wait **10 minutes** (add a minute or two of jitter), then repeat. Five attempts, about 65 minutes. Politeness is part of the test: do not repeat faster than this.
5. Arm B: set `IMAGE_TAG=v0.12.29`, restart, repeat the five attempts. Here an R shows up as completion reason `google_meet_join_refused` after up to 3 re-knocks, and the status API shows it (read from #1921, not verified live).
6. Record each attempt (time, arm, K or R, seconds to the lobby) in a table, and keep the log lines (no keys, no meeting links needed).

| Arm A (v012, home) | Reading | Next |
|---|---|---|
| 4 or 5 of 5 reach K | The **server's address** (or its amd64 versus the Mac's arm64 build) is the problem | Change egress (section 6, step 4); still upgrade |
| 2 or 3 of 5 reach K | Random refusals like Vexa's census; the address is not decisive | Arm B should fix it; upgrade, then re-measure on the server |
| 0 or 1 of 5 reach K | Not the address; Google is refusing this browser from anywhere | Arm B decides if re-knock rescues it; if B is also 0 or 1 of 5, go to authenticated or a vendor |

| Arm B (v0.12.29, home) | Reading |
|---|---|
| 4 or 5 of 5 get in | The upgrade works at a home address; then test it on the server (his call) |
| Fewer, with typed `google_meet_join_refused` | The failure is now clean and fast; retry limits (section 5) matter more |

Limits of E2: five samples cannot tell 27% from 50%; the Mac builds arm64 Chrome and the server amd64, so "fingerprint" is not held perfectly equal; time of day changes between arms. Treat it as a strong hint, not a measurement.

## 5. Pacing

We sent 5 attempts in 25 minutes from one address, one anonymous name, one meeting. Google publishes no limit I could find, so this is **a proposal, not a documented threshold**. Vexa's own policy is up to 3 re-knocks inside one bot run on v0.12.29 (so one `send_bot` can already knock 4 times). Anything the worker adds sits on top of that.

| Rule | Value |
|---|---|
| Sends per meeting occurrence for a refusal | **2** (the first, one retry) |
| Gap before the retry | at least **4 minutes**, plus 0 to 60 s jitter; never inside the bot's own re-knocks |
| Auto-join window | stop after the first 15 minutes; mark "Not recorded: Google would not let the bot in" with a manual **Send again** button, itself limited to 1 per 5 minutes per user |
| Circuit breaker, per egress address | **3 refusals in 30 minutes** pauses auto-sending for 30 minutes and alerts the admin once; manual sends still work |
| Stagger | meetings at :00 and :30 start at start minus lead plus a 0 to 20 s random offset |
| On refusal | stop the bot at once (`stop_bot`); never leave it polling a dead page |
| Timing | cap the wait for the door at about 2 minutes after the click; `needs_help` from Vexa counts as a failed join after a 30 s grace |

## 6. Alternatives to a browser bot

- **Meet Media API.** Still described as a developer preview: Recall says all participants must be enrolled in Google's preview program and only the 3 most relevant participants' audio and video arrive, with no transcript; a June 2026 blog says restricted scopes and a security assessment (**not verified**; the Google page I tried returned 404). It cannot be used for public users while in preview. No change to the plan.
- **Meet add-ons.** A developer-forum thread asks how a public add-on can get participant audio without a bot, which suggests it cannot (title only, **not verified**). No change.
- **A hosted vendor (Recall.ai).** This one **does** change the fallback. Recall's blog says pay-as-you-go fell to $0.50 per recorded hour in early 2026 (its pricing page snapshot still showed $0.70; **not verified**), transcription extra, billed per recording hour. The pilot cap is 10 accounts times 5 hours, so at most 50 hours, about $25 to $35 a month. Recall also reports `google_meet_bot_blocked` and runs its own signed-in account pools, so it is not immune, but it carries the account, pooling and egress work. Cost to us: a new client behind `VexaClient`, a sub-processor line in the privacy policy, and recordings leaving our server. Keep as plan B if E2 and the upgrade fail.

## Recommendation, in order of effort

| Order | Step | Effort | Why |
|---|---|---|---|
| 0 | E0 (private window, exact bot name) and check the server's image digest | 5 min | Rules out the cheapest causes |
| 1 | **E2 on the Mac** (the one experiment to run first) | about 2 h | Decides address versus Google-side, and proves the v0.12.29 image on a home address |
| 2 | Upgrade the server to **`VEXA_IMAGE_TAG=v0.12.29`** after a Mac test call (Adebayo, between meetings) | 30 min | It is the vendor's own fix for this symptom; rollback is one variable |
| 3 | Worker: fail fast on a refusal, plus the pacing and breaker in section 5 (the checklist item "Fail fast when Google refuses the bot's join request") | 1 task | Needed whatever the cause; no Google dependency |
| 4 | If E2 points at the address: a different egress (the second bot machine of Phase D at another provider is a free test; a proxy later) | hours to days | Cheaper than accounts |
| 5 | Authenticated bot plus the invited-attendee path | **weeks**: new account, move off Lite, our own bot image until #1873 merges, one bot per account | Only if 1 to 4 fail, or pilot hosts need restricted meetings |
| 6 | Vendor fallback | days | If nothing above gives a join rate the pilot can promise |

**Effect on the pilot plan** (`docs/superpowers/plans/2026-11-03-pilot-accounts-calendar.md`):

| Change | Where |
|---|---|
| **New P0.10** "Join-resilience experiment" (E0, E1, E2); **new P0.11** "Server on v0.12.29" (Adebayo deploys; a session records the digest before and after) | Phase 0 |
| P0.3 and P0.8 run on the **same pinned image the server will run** (`v0.12.29`), and P0.8 records refusals, re-knocks and time to the door. Do not size `AUTOJOIN_LEAD_SECONDS` on 60 s alone: the door takes 25 to 41 s after a refusal, so the 90th percentile may exceed it | P0.3, P0.8 |
| **New C12b** "Refusal handling and pacing": map `google_meet_join_refused` and `needs_help` to a failed join with a plain reason; stop the bot; the 2-sends limit, retry gap, 15-minute window and circuit breaker; admin alert through C11b. C12's `no_capacity` retry stays as it is, and C12 must not reuse its 15 s retry for refusals | After C12 |
| C12 text for "Not recorded": add a refusal variant ("Google would not let the bot ask to join") and the Send again button | C12, C15b |
| C6/C8, only if the invite path is adopted: record whether the bot's address is among the event's attendees and show "Add the bot to this event" on Upcoming | New task, gated |
| C18 (recording notice) and V0/V1 (policy and terms): only if authenticated bots are adopted, name the bot's Google account and where its session is stored | Gated on step 5 |
| Phase D: unchanged for steps 0 to 4. Under step 5 each host needs its own account and deployment | Gated on step 5 |

**What Adebayo must provide.** (a) E0 and E1 (ten minutes of his hands); (b) host a Meet and allow about two hours for E2 on the Mac; (c) paste the server's image digest; (d) approval to move the server to `v0.12.29` once the Mac test passes; (e) only if step 5 is reached: a decision on a Workspace seat versus a Gmail account for the bot, and a recovery email and phone held by Beyrichq; (f) a yes or no on Recall as plan B.

## Sources

Read 2026-11-03: Vexa releases v0.12.18 to v0.12.28 and PR [#1921](https://github.com/Vexa-ai/vexa/pull/1921); issues [#540](https://github.com/Vexa-ai/vexa/issues/540), [#316](https://github.com/Vexa-ai/vexa/issues/316), [#724](https://github.com/Vexa-ai/vexa/issues/724), [#1725](https://github.com/Vexa-ai/vexa/issues/1725), [#1655](https://github.com/Vexa-ai/vexa/issues/1655), [#1110](https://github.com/Vexa-ai/vexa/issues/1110); PR [#1873](https://github.com/Vexa-ai/vexa/pull/1873); Vexa [authenticated bots](https://docs.vexa.ai/authenticated-bots) and its source at commit `31122f9` (`provision-cli.ts`, `bot_spawn/service.py`, `browser-args.ts`, `deploy/lite/Makefile`, `docs/docs/authenticated-bots.mdx`); Docker Hub tags for `vexaai/vexa-lite` and `vexaai/vexa-bot`; Google [who can join without asking](https://support.google.com/a/users/answer/12172260); Recall [signed-in bots](https://docs.recall.ai/docs/google-meet-login-getting-started), [Meet FAQ](https://docs.recall.ai/docs/google-meet-faq), [Meet Media API](https://docs.recall.ai/docs/google-meet-media-api), [2026 pricing](https://recall.ai/blog/new-recall-ai-pricing-for-2026). Pages summarised by a fetch tool, not read in full: the Vexa and Recall docs pages.
