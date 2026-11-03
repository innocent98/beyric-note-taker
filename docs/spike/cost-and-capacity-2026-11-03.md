# Cost and capacity: what we have spent, and self-hosted against a hosted bot vendor

**Written:** 2026-11-03 by the fourth lead session, from Adebayo's OpenAI exports (`completions_usage_2026-11-01_2026-11-03.csv`, `cost_2026-11-01_2026-11-03.csv`, kept on his Mac, not in the repo), his statement that the Contabo server costs about USD 70 a year, and `docker stats` read over ssh during meeting 12. Evidence labels: *measured* (read from a file or the server today), *estimate* (my arithmetic or memory, not checked against a price page), *vendor* (a vendor page read by a fetch tool, see `docs/superpowers/plans/2026-11-03-meet-signed-in-bot-and-vendor.md`).

## What we have spent since 2026-11-01

| Day (UTC) | OpenAI, all products | What happened |
|---|---|---|
| 11-01 | USD 0.096 | Spike calls (2-minute Meet and Zoom tests), one chat request group |
| 11-02 | USD 1.351 | Meeting 3 (about 6 min), meeting 5 (about 56 min, live hints plus transcription plus notes), the model comparison runs (gpt-5.6-luna, gpt-5.4-mini, gpt-6-luna) |
| 11-03 | USD 0.096 | Meeting 12 (a few minutes), 5 small chat requests |
| **Total** | **USD 1.543** *(measured, the cost export)* | |

- The chat export (`completions_usage`) shows only notes and speaker-label calls: about 40,000 input and 28,000 output tokens on 11-02 across 39 requests. It has **no audio line items**, so transcription is only in the cost export, which has no per-line breakdown. Export "group by line item" from the OpenAI usage page to split audio from chat.
- Reading: the app recorded about **1.0 hour** in total (meetings 3 and 5). The whole bill including comparison experiments is USD 1.54, so the all-in cost per recorded hour is **at most about USD 1.3** (11-02 alone, including the experiments) and probably about **USD 0.7** (final transcription plus Vexa's live `whisper-1`, each near USD 0.36 an hour, plus cents for notes) *(estimate, rates from memory)*. The price pages timed out when I tried to read them today: re-check before quoting.
- The usage ledger in the database (`usage_entries`) started with PR #31, so it has no rows for these meetings.

## What one bot costs on the server *(measured, one sample)*

`docker stats --no-stream` and `free -h` at 14:00 WAT on 2026-11-03, one bot active in meeting 12, server `haven-respite` (4 vCPU, 7.8 GiB, shared with Haven and Novex):

| Container | CPU | Memory |
|---|---|---|
| `vexa` (Vexa Lite plus the bot's Chrome) | 138% (about 1.4 cores) | 1.95 GiB of its 3 GiB cap |
| `next`, `web`, `worker`, `db`, `vexa-db`, `vexa-storage` | 0 to 7% each | 40 to 92 MiB each |
| Host | load average 4.1 on 4 cores | 3.2 GiB available |

So one bot uses about **1.4 cores and 1.4 GiB** on top of Vexa's idle 0.54 GiB. The earlier plan (about 8 bots on a 16 GB, 8-core machine) was memory arithmetic only; **by CPU an 8-core machine runs about 4 to 5 bots**, and this server already runs hot with one. One sample, bot idle in a call, no transcription burst: treat as a floor.

## Server against a hosted vendor

Assumptions (stated, not measured): the pilot is at most 50 recorded hours (10 accounts, 5 hours each); launch is about 50 users at 20 hours each, 1,000 hours a month, with at most about 5 bots at once. OpenAI costs (transcription, notes) apply on every row unless a vendor's own transcript replaces ours.

| | Pilot (50 h a month) | Launch (1,000 h a month, peak 5 bots) |
|---|---|---|
| **Current server** | about USD 5.8 a month for the whole box *(measured: his USD 70 a year)*, shared with two other projects; fits **one** bot at a time | does not fit |
| **Own bot machine, self-hosted** | a second box of the same size about USD 6 a month *(estimate: same price tier)*, so about USD 0.12 per recorded hour at 50 hours | one machine of 8 to 12 cores, roughly USD 15 to 40 a month *(estimate, unverified)*, so about USD 0.02 to 0.04 per recorded hour |
| **Hosted vendor (Recall)** | 50 x (0.50 plus 0.15 for its transcript) = **USD 32.50**, the first 5 hours free *(vendor)* | about **USD 650** (USD 500 without its transcript) *(vendor, volume pricing may lower it)* |
| OpenAI on top | about USD 36 *(estimate)* | about USD 720 *(estimate)* |

Reading: self-hosting is cheaper per hour at every size here, even at pilot size on a dedicated USD 6 machine; the vendor costs about as much as a year of server for a month of the pilot. The reasons to pay a vendor are not money: it absorbs Google's refusals, account pools and concurrency, and it needs no upkeep. The reasons against: recordings leave our server (a new sub-processor in the privacy policy), a per-hour cost that grows with use, and a dependency we cannot fix ourselves.

## Recommendation

1. Keep the pilot self-hosted, one bot at a time on the current server, and invite one or two testers only.
2. Move the bot to its **own machine** before inviting more people: the current box saturates its four cores with one bot while it also serves Haven and Novex.
3. Keep the vendor seam as the fallback (plan B, `docs/superpowers/plans/2026-11-03-meet-signed-in-bot-and-vendor.md`). Trigger: Meet join success below about 90% over the first 30 attempts on `v0.12.29`, or peak concurrency that we do not want to run ourselves.
4. Re-run this note with a full month of real usage and a "group by line item" OpenAI export before launch pricing.

## Open

- The OpenAI line-item export; the price of a bigger Contabo plan (not verified); a measurement with two bots at once and during transcription; real recorded hours per account after sign-up opens.
