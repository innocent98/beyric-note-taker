# Experiment E2: does the same bot get refused from a home connection?

**Date:** 2026-11-03 (runs 12:20 to 13:32 WAT, which is 11:20 to 12:32 UTC; times below are WAT, UTC plus one hour). **Type:** live experiment on Adebayo's Mac. Nothing was sent from, or run on, the production server.
**Plan:** experiment E2 of [`meet-join-resilience.md`](meet-join-resilience.md) section 4.
**Evidence:** [`meet-join-experiment/`](meet-join-experiment/): per-attempt timelines and trimmed bot-log excerpts (`logs/`), and five in-flight screenshots (`screens/`). No keys or `.env` content are in any of it (scanned).

## Verdict

| Question | Answer |
|---|---|
| Does the failing image (v0.12.28) get refused from a home connection too? | **Yes: 5 of 5 refused.** The address is not what separates us from a working join |
| Does v0.12.29 get in from the same Mac? | **Yes: 3 of 3 reached the lobby**, one of them only because the new re-knock rescued a refusal (stopped at 3 of 5 on Adebayo's call) |
| Upgrade the server? | **Yes, to `VEXA_IMAGE_TAG=v0.12.29`** (pinned tag), then re-measure on the server |
| Change the server's address? | **Not yet.** This test cannot show the server's address is fine on v0.12.29; the server's own result after the upgrade decides it |

Confidence: moderate. The arms ran one after the other, not interleaved, and arm A's 5 of 5 refusals is much worse than Vexa's published 27%, so a time-of-day effect is possible (see "What this does not settle").

## Setup

| | Arm A | Arm B |
|---|---|---|
| Image | `vexaai/vexa-lite:v012` = v0.12.28, digest `sha256:9eebb534fdab...` (same digest the spike recorded for the server tag) | `vexaai/vexa-lite:v0.12.29`, digest `sha256:baaac073a129...`; the image contains the new refusal code (`google_meet_join_refused` is in its bot source) |
| Host | Mac, Docker Desktop (arm64, about 3 GB), Vexa Lite via `make -C deploy/lite up`, API on `localhost:8056` | same |
| Room | one Meet hosted by Adebayo from his own signed-in account, kept open throughout | same room |
| Request | `POST /bots` with `{"meeting_url": "https://meet.google.com/tip-mhbx-fcp", "bot_name": "Beyric Note Taker (recording)"}` | same |
| Pacing | 5 attempts, starts 10:12 to 10:58 minutes apart (10 minutes plus 0 to 90 s of jitter, never faster) | 3 attempts, starts 11:09 and 10:20 apart |
| Watch | up to 3 minutes per attempt, then `DELETE /bots/google_meet/<code>`, bot confirmed gone before the next | same |

Two things I added to the spike's procedure. The Vexa log that matters is not in `docker logs`: the bot's own output goes to `/tmp/vexa-workloads/<workload>.log` inside the container, so I copied those out. And the bot's own checkpoint screenshots turned out useless (one is the Workspace marketing page, the others are blank), so from attempt 4 on a sampler took a picture of the bot's browser (`ffmpeg -f x11grab` on display `:99`) at +40 s, +90 s and +150 s. Attempts 1 to 3 have no such picture.

## Every attempt

K = reached the real waiting room. R = refused. "To lobby" is seconds from `POST /bots` to API status `awaiting_admission`; on v0.12.28 that status is **not** proof of a lobby (see attempts 3 to 5), so those cells say so.

| Arm | # | Start (WAT) | Result | To lobby | Evidence |
|---|---|---|---|---|---|
| A (v0.12.28) | 1 | 12:20 | **R**, before the click | none | Bot log: 12 lines `statusCode = 403` with "The caller does not have permission". Page sat on "Getting ready...", the name field was only found after about 2 minutes, and by then the page was the Workspace marketing page (the bot's own checkpoint screenshot). API stayed `joining` for the whole 3 minutes; I stopped it. [`A1`](meet-join-experiment/logs/A1-bot-excerpt.txt) |
| A | 2 | 12:31 | **R**, before the click | none | Same 403 lines. API turned `failed`, `join_failure`, reason `never_reached_lobby` at 164 s: "Could not locate join button ... url=workspace.google.com/products/meet". [`A2`](meet-join-experiment/logs/A2-bot-excerpt.txt) |
| A | 3 | 12:41 | **R**, at the click (host report) | none (API said 18.4 s, false) | The bot clicked "Ask to join" and v0.12.28 reported `awaiting_admission` after 18.4 s. **Adebayo saw no "wants to join" request.** No screenshot exists. I classify it R from his report plus the identical log and API signature to attempt 4. [`A3`](meet-join-experiment/logs/A3-bot-excerpt.txt) |
| A | 4 | 12:52 | **R**, at the click | none (API said 21.4 s, false) | Screenshot at +40 s: "You can't join this video call", "Returning to home screen" countdown; at +90 s the Workspace marketing page. Host saw no request. [`screen`](meet-join-experiment/screens/A4-refused-at-click-t40.png) |
| A | 5 | 13:03 | **R**, at the click | none (API said 85.4 s, false) | Screenshot at +90 s: same "You can't join this video call" page. [`screen`](meet-join-experiment/screens/A5-refused-at-click-t90.png) |
| B (v0.12.29) | 1 | 13:07 | **K**, then admitted | 40.7 | API `awaiting_admission` at 40.7 s, `active` at 46.8 s after Adebayo admitted the bot (he did not have to). One "Ask to join", no refusal lines. I stopped the bot straight after, per the protocol, so the attempt ended there. [`B1`](meet-join-experiment/logs/B1-bot-excerpt.txt) |
| B | 2 | 13:18 | **K**, after one refusal | 45.8 | First "Ask to join": `CreateMeetingDevice answered HTTP 403`, "You can't join this video call". The bot logged "refused knock 1 of 3 ... knocking again", clicked again 10 s later and reached the lobby. Screenshot at +90 s shows "Please wait until a meeting host brings you into the call". Host saw the guest waiting. [`B2`](meet-join-experiment/logs/B2-bot-excerpt.txt), [`screen`](meet-join-experiment/screens/B2-waiting-room-t90.png) |
| B | 3 | 13:29 | **K** | 18.5 | One "Ask to join", no refusal lines; same waiting-room screen at +90 s. Host saw the guest waiting. Arm B stopped here on Adebayo's call. [`B3`](meet-join-experiment/logs/B3-bot-excerpt.txt), [`screen`](meet-join-experiment/screens/B3-waiting-room-t90.png) |

Arm A: 0 of 5 K. Arm B: 3 of 3 K (first-knock refused in 1 of the 3).

## Reading, with the spike's tables

**Arm A (v012, home): 0 of 5.** This is the spike's last row: *"Not the address; Google is refusing this browser from anywhere. Arm B decides if re-knock rescues it."* The two failure shapes match the server's logs: before the click (our meetings 10 and the "Could not locate join button" cases) and at the click (our meetings 8, 9 and 11, where the bot polled a dead lobby). The home connection did not help v0.12.28.

**Arm B (v0.12.29, home): 3 of 3.** The nearest row is *"4 or 5 of 5 get in: the upgrade works at a home address; then test it on the server"*. Three samples fall short of that row's bar, so this is consistent with it, not proof. What is solid is the mechanism: attempt B2 was refused at the click, exactly like attempts A3 to A5, and the new code re-knocked and reached the lobby (log and screenshot).

**What it means for the question the spike asked** (address versus Google side): arm A being refused 5 of 5 from Adebayo's home connection says the address alone is not the cause. It does not say the server's address is clean.

## What this does not settle

- 0 of 5 against 3 of 3 is unlikely to be chance (about 1 in 56 if both arms had the same rate), but it cannot tell 27% from 50%.
- **The arms were not interleaved, so time of day is a confound.** Arm A ran 12:20 to 13:06, arm B 13:07 to 13:32. At Vexa's measured 27% first-knock refusal, five refusals in a row is about a 1 in 700 event, and arm B's first-knock results were 2 accepted and 1 refused, which looks like Vexa's rate. So either Google was refusing more during arm A's hour, or something in the v0.12.29 image besides the re-knock changed the first-knock rate. This run cannot tell which. A control (run v012 again now) would, at 55 minutes of Adebayo's time.
- **The B arm started 4 min 40 s after the last A attempt** (12:03:07 to 12:07:47 UTC), inside the 10-minute rule; the stack was restarted in between. If Google punishes repeat knocks from one address, this would have hurt arm B, not helped it.
- **Mac is arm64, server is amd64**, so the browser build is not held equal. Docker Desktop reaches the internet through the Mac's connection; I did not read the egress address, so "home connection" is Adebayo's word, not a measurement.
- **One room, one host account.** Other hosts' Meet settings could still refuse guests (the pilot hosts' organisations).
- **Attempt 3 has no picture.** It is classified R from the host's report; the evidence is as strong as for attempts 4 and 5 only if you trust that report.
- **Not done:** E0 and E1 (private window with the exact name; human through the server's address), a refused-three-of-three case, and any run on the server.

## Recommendation for the server

| Step | Do it? | Why |
|---|---|---|
| Move the server to `VEXA_IMAGE_TAG=v0.12.29` (pin the tag, never `:v012`; re-run `docker compose up -d vexa-schema-fix`; swap between meetings; roll back by putting `v012` back) | **Yes** | It is the vendor's fix for this symptom and it rescued a real refusal here. Arm A showed v0.12.28 failing from two networks, and misreporting refusals at the click as a lobby wait for 15 to 20 minutes |
| Change the server's egress address | **Not now** | The home address did not help v0.12.28, so the address is not shown to be the problem. Re-measure after the upgrade: record knocks, refusals and time to lobby in P0.8. Only if the server still sees refusals the re-knock cannot rescue (typed `google_meet_join_refused` after 3 knocks) look at a different address |
| Worker: fail fast, 2-send limit, breaker (spike section 5; checklist "Fail fast when Google refuses the bot's join request") | **Yes, still needed** | v0.12.29 still ends in a typed refusal when all three knocks fail, and v0.12.28 reports `awaiting_admission` for a dead page. Never read `awaiting_admission` as proof of a lobby on the old image |
| Authenticated bot or a vendor | **Hold** | Only if the server on v0.12.29 stays unreliable. Arm B does not point there |

Sizing note for `AUTOJOIN_LEAD_SECONDS`: arm B reached the lobby after 18.5, 40.7 and 45.8 s. Vexa's own times are 13.5 s with no refusal, 25.1 s with one, 40.7 s with two. B1 took 40.7 s with no refusal in its log, so about 45 s is a realistic upper figure even without refusals, and a lead of 60 s leaves little room.

## Operate / clean up

All cleanup is done: containers `vexa-lite`, `vexa-lite-postgres`, `vexa-lite-storage` and the `vexa-lite-net` network are removed (data volumes kept, as `make down` does), `IMAGE_TAG` in `spike/vexa/.env` is back at `v012`, and no bot is running. The pulled `vexaai/vexa-lite:v0.12.29` image (about 8 GB) is still on the Mac so a pre-upgrade test call needs no download. Remove it with `docker rmi vexaai/vexa-lite:v0.12.29` if the disk is needed.

## Follow-ups

- Optional control: run v012 again on the Mac now, to separate "time of day" from "image" (about 55 minutes of hosting).
- After the server upgrade, record per-meeting knocks, refusals and time to lobby (pilot-plan P0.8) and compare with this table.
- Keep the in-flight screenshot trick (`ffmpeg -f x11grab -i :99 -frames:v 1`) in mind for any future join test: the bot's checkpoint screenshots do not show the lobby.
- The bot's own log, not `docker logs`, carries the join evidence; the checklist item "Keep Vexa's botlog.txt ... for failed joins" is the same need.
