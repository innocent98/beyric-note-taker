# Meeting-bot options for Beyric Note Taker: research report

> **Lead's note (2026-11-06).** Written by a research sub-agent from pages it read the same day; the lead did not re-read the vendor pages. Two things the owner asked: (1) the prices below are the **vendor's bot and, where stated, its transcript only**. Beyric's own AI (notes, decisions, tasks, open questions, translation) is a separate cost that does not change with the bot vendor. If a vendor's transcript is used, our own transcription spend (today OpenAI through Vexa, about USD 0.0045 per audio minute, still to be confirmed on OpenAI's pricing page) is replaced by the vendor's rate; Skribby and MeetStream also accept our own transcription key. (2) Earlier notes claimed Recall "runs its own signed-in account pools": the report below shows that is **not** so; you provide the accounts.

Date researched: 2026-11-06. Every page below was opened on that date. Evidence labels:
**read** = I read it on the vendor/repo page; **vendor claim** = marketing, unverified; **not verified** = I could not confirm it.

Plain-word glossary: *Guest join* = the bot joins Meet without a Google login and may have to "knock". *Signed-in bot* = the bot is logged in to a real Google account. *Knock / lobby* = the host must press Admit. *Legacy SSO* = a Google Workspace admin setting that lets a third party log accounts in for you.

---

## 0. Findings that change the picture

1. **Signed-in does not by itself skip the knock.** On Recall, Skribby and MeetStream, the bot only skips the lobby if its account email is on the calendar invite (read: Recall FAQ, Recall signed-in guide, Skribby bot-detection guide, MeetStream signed-in guide). MeetStream adds that an invited bot skips the lobby only when the meeting's Waiting room is off. Otherwise a signed-in bot still knocks, but looks more trustworthy (name and photo of a real account, no "may not be who they claim" warning).
   *My inference, not from a vendor:* since Beyric already holds the customer's calendar via OAuth, we could add the bot account as an attendee on the event ourselves. This needs a design check (it changes the customer's event; Skribby's page says "invite the assigned account to the calendar event").
2. **Recall does NOT run a pool of signed-in accounts for you.** The Recall doc says "You provide them. Recall doesn't supply a pool." You create a dedicated Google Workspace, set up Legacy SSO with a certificate you generate, and register the accounts with Recall (read: docs.recall.ai/docs/google-meet-login-getting-started). The owner's belief that Recall "runs its own signed-in account pools" is **not supported** by what I read. MeetStream uses the same SSO scheme.
3. **Skribby is the easiest signed-in setup**: you type a Gmail or Workspace account email, password and the 2-step authenticator setup key into their dashboard (read). No SSO or Workspace needed. Trade-off: you hand a bot Google account's password and TOTP seed to a very small vendor.
4. **None of the four open-source names the user gave is a call-joining bot.** Meetily, Meet2Notes, Prismical and Platypus are all desktop apps that record your own computer's audio. See section 4.
5. **Vexa #1725 has a known one-line fix, but it is not merged.** Authenticated Meet bots launch Chromium with `--incognito`, which ignores the saved login. PR #1873 removes it; open, not merged, merge-blocked on maintainer sign-offs as of 2026-11-06 (read). Separately, Vexa's own docs say authenticated mode is **not available on Lite**, only Compose or Helm (read), and only one bot per saved session at a time.
6. **The 403 problem is a datacenter-IP and guest-scrutiny problem.** Skribby's own guide says guests are "the most scrutinized", datacenter IPs are more likely blocked, and it retries up to 3 times rotating IP range and fingerprints (read, vendor description). Recall's FAQ lists "bot name" and host settings as causes of `google_meet_bot_blocked` (read). **No vendor publishes a Meet join-success rate.** Recall's claim that Skribby is "42% reliable" is a competitor's marketing (vendor claim, not verified).

---

## 1. Summary table

| Option | What it really is | Sends bot into Meet by API? | Meet join method | Price (bot + transcript) | 50 h / month | 200 h / month | Verdict |
|---|---|---|---|---|---|---|---|
| **Recall.ai** | Hosted API, US startup, $38M Series B Sep 2025 | Yes | Guest, or signed-in with **our** Workspace accounts via Legacy SSO | $0.50/h + $0.15/h built-in transcript = $0.65/h | $32.50 (first 5 h free once) | $130 | Most mature, most setup |
| **MeetStream** | Hosted API, young (Product Hunt launch Jul 2025), owners not identified | Yes | Guest, or signed-in via Legacy SSO (same pattern as Recall) | $0.35/h + $0.10/h diarised transcript = $0.45/h ($0.35 with own transcription key) | $22.50 ($17.50) | $90 ($70) | Cheap, unproven |
| **Skribby** | Hosted API, founded May 2025, bought by Alianza (US telco-software firm) Sep 2026 | Yes | Guest, or signed-in with any Gmail/Workspace account you type in | $0.35/h + transcript models $0.39 to $1.36/h all-in | $19.50 to $68 | $78 to $272 | Easiest signed-in, ownership in flux |
| **Vexa** (baseline) | Open source (Apache-2.0), self-host | Yes | Guest; authenticated mode on Compose/Helm only, bugged (#1725) | Server cost only | our VPS | our VPS | Fixable but ops heavy |
| Meetily | Desktop recorder app | **No** | Captures your computer audio | Free MIT; PRO $10/user/mo | n/a | n/a | Not a bot |
| Meet2Notes | Desktop/self-hosted local app, 1 maintainer | **No** | Local audio capture | Free MIT | n/a | n/a | Not a bot |
| Prismical | Desktop/mobile note-taker | **No** | System audio capture | Free MIT | n/a | n/a | Not a bot |
| Platypus | Desktop recorder (macOS/Windows) | **No** | Local audio capture | Free MIT | n/a | n/a | Not a bot |
| Attendee (not on the user's list, found while searching) | Open-source meeting-bot API | Yes | Chrome-based bot; signed-in docs page not reachable | Self-host; hosted price not listed | not verified | not verified | Licence is Elastic 2.0: risky for us |

Cost arithmetic: Recall 0.65 x 50 = 32.50, x 200 = 130. MeetStream 0.45 x 50 = 22.50, x 200 = 90; with own key 0.35 x 50 = 17.50, x 200 = 70. Skribby cheapest model 0.39 x 50 = 19.50, x 200 = 78; priciest listed 1.36 x 50 = 68, x 200 = 272. These do **not** include the Google Workspace licence for bot accounts (price **not verified**; I did not open Google's pricing page). Bot time is billed from arrival including lobby waiting (MeetStream, read); set a waiting-room timeout to cap that.

---

## 2. Hosted APIs in detail

### 2.1 Recall.ai (baseline)

**What / who.** US company (San Francisco in funding reports; other listings disagree, treat location as not verified). $10M Series A May 2024; $38M Series B Sep 2025 led by Bessemer, about $51M total raised (read, news and Sacra via search results). Product: Meeting Bot API plus a Desktop Recording SDK (no bot).

**Meet join.** Guest by default. Signed-in when `google_meet.login_required` is true, or when omitted, only when the meeting requires sign-in (read, FAQ). Setup is **ours**: dedicated Google Workspace, non-admin users, Legacy SSO profile (newer SAML/OIDC profiles not supported), OpenSSL certificate, a Login Group in Recall, accounts in the same Recall workspace and region (read). Each account supports up to 30 concurrent bot sign-ins; Recall suggests starting with two accounts. A signed-in bot skips the waiting room if the account email, or a Workspace group address, is on the invite. Fatal sub-codes seen in docs: `google_meet_bot_blocked`, `google_meet_login_not_available`, `google_meet_sign_in_failed`, `google_meet_knocking_disabled`, `google_meet_sign_in_missing_login_credentials` (read). The docs do not mention HTTP 403 explicitly. No join-success rate published. Signed-in bots show the account's name and photo; `bot_name` is overwritten by the account display name (read).

**Zoom / Teams.** Both supported; signed-in bots available for all three (read: signed-in-bots page). Beta breakout rooms and Webex/GoTo exist (read: MeetStream's comparison, so a competitor's description).

**API.** Create bot by meeting URL, webhooks, transcript with speaker labels, recording download, real-time streams, calendar API free (read: pricing page). Concurrency: "thousands of concurrent bots", no ceiling stated (vendor claim). I did not re-read Recall's transcript JSON this session, so the exact shape is **not verified** here.

**Price (read).** Pay-as-you-go $0.50/h recording, billed to the second, first 5 hours free, 7 days storage free, then $0.05/h per 30 days retained. Built-in transcript $0.15/h. +$0.10/h for 4-core bots; +$1/h GPU bots. Launch and Enterprise plans custom. Startup programme $0.25/h for first 10,000 h for eligible early-stage startups. Volume discounts by sales. The old "$0.50 to $0.70 per hour" is consistent: $0.50 + $0.15 = $0.65.

**Data and trust.** Four fully separate regions: US West, US East, EU (Frankfurt), Japan (Tokyo) (read). On pay-as-you-go, moving region means a new account. Retention is set per bot: timed, forever, or zero retention; accounts created after 2025-06-12 default to **keep forever**, so set it explicitly (read). Delete Recording and Delete Bot Media endpoints; permanent, no backups. Deleting media does **not** remove data held by third-party transcription providers (read). Compliance: SOC 2, ISO 27001, GDPR, CCPA, HIPAA claimed, DPA link and security portal exist (vendor claim; I could not open the trust page, `/trust` and `/security` returned 404). MeetStream's page says Recall has a SOC 2 Type 2 report (a competitor's statement). Not self-hostable.

**Swap effort for `VexaClient`.** Small to moderate. Same four verbs (create bot, get transcript, stop bot, delete media). Real work: webhook handler, mapping Recall's transcript shape to ours, and the one-off Workspace SSO setup (half a day to a day, my estimate).

**Risks.** Signed-in setup is heavy and fragile (certificate rotation, SSO assignment mistakes break sign-ins; "Ready" does not prove sign-in works; Recall warns not to use a customer's own Workspace). Still no published block rate. Default retention is infinite for new accounts.

### 2.2 MeetStream

**What / who.** "MeetStream AI", unified meeting-bot API for Zoom, Meet, Teams, aimed at AI-agent builders; Product Hunt launch dated July 7 2025; a co-founder posts as "sidhdharth" (read via search). Company name, country, funding: **not verified**. Docs are large and current (78 guide pages, OpenAPI spec, SDKs, MCP server, a Recall-migration CLI).

**Meet join.** Guest by default; signed-in needs `login_required: true`, a `google_login_domain` and one-time Workspace SSO (read). Same shape as Recall: **we** supply a dedicated Workspace, MeetStream acts as the SAML identity provider at `api.meetstream.ai`. 30 concurrent bots per login. Signed-in bots **never fall back to guest**; failure ends as `GoogleSignInFailed`. Invited bots skip the lobby only when the meeting's Waiting room is off. Blocked joins surface as `Failed` with reasons such as `cant_join_call`, `meeting_restricted`, `sign_in_required`; `CantJoinCall` is retried up to 3 more times; transient errors up to 3 attempts. No 403 handling documented. Bots that never reach a meeting (invalid link, blocked sign-in) are not charged (read). No join-success rate.

**Zoom / Teams.** Both; Teams signed-in and Zoom authenticated (ZAK/OBF) pages exist. Webex etc. beta. No Zoom breakout rooms or webinars (read).

**API.** Create bot by link; bot name required; `bot_image_url` sets camera image; webhooks (`bot.inmeeting`, `bot.stopped`, `transcription.processed`, `bot.done`, `data_deletion`), signed workspace webhooks; transcript fetched by `transcript_id` (200, or 202 while processing); per-participant audio/video (capped at 16 streams on Meet); live transcript option; recordings to your own S3/R2/OSS bucket. I could not see a real transcript JSON sample on the pages I read: **not verified**.

**Price (read).** $0.35/h ($0.25 at volume or for select startups), billed to the second, $5 free credit, no card, no minimum. 30 days storage included; beyond that $0.05/GB/month. Diarised transcript +$0.10/h; free if you bring your own transcription key. Concurrency limits not stated.

**Data and trust.** Default retention 30 days; `recording_config.retention` timed; `DELETE /bots/{id}/delete` removes audio, video, transcripts and fires a `data_deletion` webhook (read). Default storage region **not stated** on pages read. Claims: ISO 27001, GDPR, HIPAA with BAA, SOC 2 Type 2 "under audit" (read on their site; so no SOC 2 report yet). DPA, sub-processor list: **not verified** (footer pages not retrievable). Not self-hostable.

**Swap effort.** Small to moderate; docs include a mapping from Recall's API, which shows the shape is close to Recall's.

**Risks.** Young, anonymous ownership, no SOC 2 report, same heavy SSO route for signed-in.

### 2.3 Skribby

**What / who.** "Meeting Bot API", founded May 2025; acquired by **Alianza** (Utah, US telco-software company), announced about 2026-09-10 (read: Alianza press page via search). Founder Pieter Jan Pollie joins Alianza; "customers continue without interruption" (read via search). Privacy policy lists Alianza's Utah address. Default data region is **EU**, with a JP region (read). Traction figures are third-party only.

**Meet join.** Guest by default; signed-in "Authenticated Accounts": Workspace or personal Gmail account, dedicated non-admin recommended, account need not belong to the host's Workspace (read). You enter email, password and the authenticator setup key (not a 6-digit code; app passwords not supported) in Settings; pass `authentication.account_id`; `always_authenticate: true` skips the guest attempt. Invite the account's email to the event to skip the knock. About 30 active bots per account is a planning figure; use at least two accounts. Bots in the same meeting using the same account can merge into one participant. Blocked bots: status `bot_detected`, up to 3 retries rotating IP range and fingerprints (read). Skribby says nothing on the 403 specifically other than that exceeding **its own** concurrency limit returns 403. No join-success rate published.

**Zoom / Teams.** Both; Zoom authenticated uses ZAK tokens, Teams uses an Azure AD account (read).

**API.** `POST /bot`, `GET /bot/{id}`, `POST /bot/{id}/stop`, `DELETE /bot/{id}`, Bearer token; TypeScript SDK; webhooks for transcript, recording and bot events; real-time transcript; diarisation; 30+ languages; `bot_name` (60 chars on Meet, silently truncated); avatar is **not covered** in docs. Name advice: avoid "Bot", "Notetaker", "Transcription"; human-like name plus authenticated join works best (read). Transcript JSON shape: **not verified** (not on pages read).

**Price (read).** $0.35/h base, billed per minute, no contracts or minimums. Transcription extra and priced as all-in rates from $0.39/h (Groq Whisper v3 Turbo) to $1.36/h (Google Chirp 3 Realtime); bring-your-own-key allowed. Add-ons +$0.05/h for video, realtime, or 1-year storage. Free tier: 5 bot hours a month, 3 concurrent bots, audio only and Groq model only, no recording uploads. Pay-as-you-go: 25 concurrent bots; "PAYG+" custom (read). Which model gives speaker names: **not verified**. No separate price for authenticated accounts.

**Data and trust.** Default retention 7 days (recordings), 1 year with add-on; transcripts kept while the account is active unless you ask for deletion; debug data including screenshots kept 30 days regardless of deletion (read). DPA available for business customers; **no SOC 2 or ISO 27001 mentioned**; security measures listed (TLS 1.2+, AWS encryption at rest, MFA, access limited to two founders). Sub-processors (read): Hetzner (Germany), AWS (Ireland, plus US/Japan for bot instances), Cloudflare, Sentry, Stripe, and 11 transcription providers, several **US-only** (Groq, ElevenLabs, Soniox, OpenAI, xAI). So the transcription model choice decides whether audio leaves the EU. Not self-hostable.

**Swap effort.** Small: the four verbs map one to one (`POST /bot`, `GET /bot/{id}`, `POST /bot/{id}/stop`, `DELETE /bot/{id}`). Closest to our existing adapter.

**Risks.** Company two years old, just acquired by a telco-software firm that bought it to build its own product; terms may change; one critic argues the terms allow assignment at will (opinion, Beri analysis). No certifications. You store a bot Google password and TOTP seed with them. Guest join is still the default, so use `always_authenticate`.

---

## 3. Vexa (baseline) and Attendee

**Vexa.** Apache-2.0, 2,956 stars, created 2025-02-07, last push 2026-11-06, 800 open issues (read, GitHub API). Releases: v0.12.28 on 2026-10-09 (read); v0.13.1 is an alpha prerelease from 2026-09-07 "not meant for production" (read). I did **not** see a v0.12.29 listing (not verified). Authenticated mode: set `BOT_AUTHENTICATED=true`; all `POST /bots` then spawn signed in; needs `make login` once and an S3 store; Compose/Helm only, **not Lite**; one bot per session (409 on a second), so N concurrent signed-in bots need N accounts and, today, N deployments; validated on Meet only; session files are credentials (read: docs.vexa.ai/authenticated-bots). Issue #1725 (opened 2026-09-22, still open) and PR #1873 (open, one commit, independently tested by two users, blocked on maintainer sign-offs) describe the `--incognito` bug. Workaround: rebuild the bot image without the flag.
Honest read: for a pilot with low concurrency, moving from Lite to Compose plus a patched bot image is technically possible, but it keeps us on a datacenter IP and keeps the bug-fix burden on us.

**Attendee (found while searching; the user did not name it).** `attendee-labs/attendee`, 791 stars, created 2024-11-15, last push 2026-11-06, licence **Elastic License 2.0** (read: LICENSE file). ELv2 forbids providing the software to third parties "as a hosted or managed service" giving access to a substantial set of its features. Beyric sells a service built on it, so legal review is needed before use. Docs reference bot login groups for Meet but I could not open the page (404/410); join method **not verified**. Hosted version at app.attendee.dev, price not listed.

---

## 4. The user's four open-source names: what they really are

All four verified through the GitHub API and README on 2026-11-06. **None sends a bot into a call. None has a "send bot by URL" API.**

| Name | Exact repo | Stars | Licence | Created | Last push | What it is |
|---|---|---|---|---|---|---|
| Meetily | Zackriya-Solutions/meetily (formerly meeting-minutes), site meetily.ai | 32,492 | MIT | 2024-12-26 | 2026-11-06 | Desktop app (Windows, macOS; Linux build from source) that records your mic and system audio and transcribes locally. README: "no meeting bot". PRO edition $10/user/month annual. Maintainer country not stated. |
| Meet2Notes | estebanstifli/Meet2Notes, site meet2notes.eu | 39 | MIT | 2026-07-26 | 2026-11-04 | One-person, alpha, self-hosted local assistant. Captures microphone and system audio, local web UI, local HTTP API, webhooks. Its APIs do not connect to meeting platforms. |
| Prismical | amicalhq/prismical, site prismical.ai | 129 | MIT | 2026-04-13 | 2026-10-26 | Desktop (macOS, Windows) and phone note-taker; captures system audio; "without a bot joining your call". |
| Platypus | pixelsmasher13/platypus, site platypusnotes.com | 42 | MIT | 2025-06-16 | 2026-10-05 | Tauri desktop recorder; detects Zoom/Teams starting and offers to record; Windows records the microphone only. Licence line credits Heelix Technologies Inc. |

Name ambiguity: "Platypus" and "Meetily" each also appear as other repos or forks in search results (for example Ivnhq/meetily); I used the repos above, which match the "open-source, no-bot note taker" descriptions. Whether other products also share these names: **not verified**.

One-line verdicts: Meetily, Meet2Notes, Prismical, Platypus are for a person recording their own meeting on their own laptop; they cannot join a customer's Meet call and cannot be built into a SaaS bot service. Drop all four from the shortlist.

---

## 5. Ranked recommendation (pilot, 50 h/month, wants signed-in bot, privacy-sensitive, small budget)

Cost is not the deciding factor: all hosted options are under about $70/month at 50 hours. What matters is join success, trust and privacy paperwork.

1. **Recall.ai** if the paperwork matters most. Most mature, SOC 2 claimed, EU region, DPA, explicit delete API, documented fatal codes, about $32.50 per month. Cost: one-time dedicated Workspace plus Legacy SSO setup; set retention explicitly because new accounts keep media forever.
2. **Skribby** if speed of getting a signed-in bot matters most. Easiest signed-in setup (a Gmail or Workspace account, password and TOTP key), IP-rotating retries on blocks, EU default region, DPA, free tier of 5 hours a month for a test. Weaknesses: just acquired, no certifications, stored bot credentials, US-only transcription sub-processors depending on model.
3. **MeetStream** as the cheap fallback. Docs are the best of the three, price lowest, but unknown owners, SOC 2 not yet issued, and the same SSO setup as Recall.
4. **Stay on Vexa**, but move to Compose and patch #1725 ourselves. Lowest cash cost, highest ops cost; still a datacenter IP.
5. Do not pursue the four desktop apps. Treat Attendee only after a licence review.

Suggested next step (my suggestion): a one-week bake-off using Skribby's free tier and Recall's 5 free hours on the same set of real meetings with `always sign-in` on and the bot account invited, then count joins, knocks and 403s ourselves. No vendor publishes this number.

---

## 6. What I could not verify

I could not open Recall's trust/security pages (404), so its SOC 2 Type 2 report, DPA terms and sub-processor list rest on marketing pages and a competitor's statement. MeetStream's company identity, funding, default storage region, DPA, sub-processor list and concurrency limits are not published on the pages I read. I did not see a real transcript JSON sample for Recall, MeetStream or Skribby, so speaker-name and timestamp fields need to be captured against a live call. No vendor publishes a Google Meet join-success rate or says how the 403 PERMISSION_DENIED on the join call is handled (Skribby's retry-with-IP-rotation on `bot_detected` is the nearest thing). I did not price Google Workspace for bot accounts. I did not confirm a Vexa v0.12.29 release, and I did not read Meeting BaaS or Attendee's signed-in docs. Skribby's avatar support, Recall's bot-name rules and Zoom/Teams signed-in details were not read in depth. Concurrent bot limits for Recall and MeetStream were not found.

---

## 7. Top 3 questions per shortlisted vendor

**Recall.ai**
1. For Google Meet, what share of joins succeed with signed-in bots versus guests today, and how do you handle `google_meet_bot_blocked`? Can you share real numbers?
2. Can the bot name and avatar be set when signed in (docs say the account's name and photo override), and is there a supported way to get a recognisable branded name without a custom Workspace per customer?
3. Do you offer a DPA with an EU region (Frankfurt) that covers transcription sub-processors, and can we have the SOC 2 Type 2 report and sub-processor list?

**Skribby**
1. After the Alianza acquisition, what is the roadmap and price guarantee, can we get contract terms that block assignment without notice, and who holds our recordings?
2. What is your Meet join success rate with authenticated accounts versus guest, how does the 3-retry IP rotation work, and how do you store the account password and TOTP seed?
3. Can bot name and avatar be set when signed in, do you have a DPA with an EU-only transcription option, and any SOC 2 or ISO 27001 plan?

**MeetStream**
1. Who are the company and legal entity, where is it based, where is default storage, and can we have the DPA and sub-processor list?
2. What is the Meet join success rate for signed-in versus guest, and how are `CantJoinCall` and 403-type refusals handled?
3. When is the SOC 2 Type 2 report due, and what are the concurrency limits and SLA?

---

## Sources (all read on 2026-11-06 unless stated)

- Recall pricing: https://www.recall.ai/pricing (read)
- Recall Google Meet signed-in guide: https://docs.recall.ai/docs/google-meet-login-getting-started (read)
- Recall Google Meet FAQ: https://docs.recall.ai/docs/google-meet-faq (read)
- Recall signed-in bots overview: https://docs.recall.ai/docs/signed-in-bots (read)
- Recall regions: https://docs.recall.ai/docs/regions (read)
- Recall data retention: https://docs.recall.ai/docs/data-retention (read)
- Recall product page: https://www.recall.ai/product/meeting-bot-api (vendor claim)
- Recall funding: https://siliconangle.com/2025/09/05/recall-ai-lands-38m-unlock-spoken-data-scale/ and https://www.businesswire.com/news/home/20250904525808/en (search results only; not opened)
- MeetStream: https://meetstream.ai, https://meetstream.ai/pricing, https://docs.meetstream.ai/llms.txt, https://docs.meetstream.ai/guides/platforms/google-meet.md, https://docs.meetstream.ai/guides/app-integrations/google-signed-in-bots.md, https://docs.meetstream.ai/guides/features/usage-and-retention.md, https://docs.meetstream.ai/guides/help/faq.md, https://docs.meetstream.ai/guides/transcription-recordings/post-call-transcription.md, https://docs.meetstream.ai/migration/migrate-from-recall.md (all read)
- MeetStream launch listing: https://www.producthunt.com/products/meetstream-ai (search result only; not opened)
- Skribby: https://skribby.io, https://skribby.io/pricing, https://skribby.io/docs, https://skribby.io/docs/guides/{bot-authentication,platforms,bot-detection,billing,bot-naming,regions,transcription-privacy}, https://skribby.io/docs/rest-api, https://skribby.io/privacy-policy, https://skribby.io/sub-processors (all read)
- Skribby acquisition: https://www.alianza.com/whats-new/alianza-aquires-skribby/ (search result only; not opened), https://www.beri.net/tag/meeting%20bot%20API (opinion, not opened)
- Vexa repo and releases (GitHub API): https://api.github.com/repos/Vexa-ai/vexa and /releases (read); issue https://github.com/Vexa-ai/vexa/issues/1725 (read); PR https://github.com/Vexa-ai/vexa/pull/1873 (read); https://docs.vexa.ai/authenticated-bots.md (read)
- Attendee: https://api.github.com/repos/attendee-labs/attendee, https://raw.githubusercontent.com/attendee-labs/attendee/main/README.md, .../LICENSE, https://attendee.dev (read)
- Meetily: https://api.github.com/repos/Zackriya-Solutions/meeting-minutes, https://github.com/Zackriya-Solutions/meeting-minutes (read)
- Meet2Notes: https://api.github.com/repos/estebanstifli/Meet2Notes, https://github.com/estebanstifli/Meet2Notes (read)
- Prismical: https://api.github.com/repos/amicalhq/prismical, https://github.com/amicalhq/prismical (read)
- Platypus: https://api.github.com/repos/pixelsmasher13/platypus, https://github.com/pixelsmasher13/platypus (read)

---

## Lead verification, 2026-11-07

The lead read these Recall pages himself (WebFetch summaries, not raw pages): pricing, home, customers, Meeting Bot API, getting-started, build-with-AI-agents, Calendar V2 guide, Calendar FAQ, the Google Meet product page and the Google Meet login guide, plus Google's Workspace pricing page. Results and corrections:

- **Workspace seats.** Recall bills only per recorded hour; no seats. The Google Workspace requirement applies only to a **signed-in** Google Meet bot: the guide says "This setup requires administrative control of a dedicated bot Workspace", bot users must not be admins, Legacy SSO and a certificate are needed, and "Creating Recall resources does not create Google users". The seats are paid to Google. The default guest join needs none. Business Starter is EUR 6.80 per user per month on a one-year plan (EUR 6.12 for new customers' first 20 users from 2026-11-20); the earlier "USD 15.50 for two seats" used an unconfirmed exchange rate: say EUR 13.60.
- **Calendar V2 is developer-driven.** We run the OAuth and call Recall's "Schedule Bot For Calendar Event"; the page does not say Recall joins by itself, and does not name scopes, price or limits. The Calendar API is free per the pricing page. Using it would make Recall hold customers' calendar refresh tokens: keep our own sync and call create-bot at join time (the lead's inference).
- **Signed-in field name.** The earlier note's `google_meet.login_required` is not in the endpoint docs: see `docs/spike/recall-api-reference-read.md` (login group `login_mode`).
- **No Recall page gives a join-success rate**; the home page's "99.9% vs 95%" graphic is unexplained.
- Recall also sells a **Desktop Recording SDK** (records on the user's computer, no bot in the call, same USD 0.50 per hour) and Meeting Direct Connect (Zoom RTMS, Google Meet Media API).
- Not read: the endpoint-level docs were read by a separate agent on 2026-11-07 (`docs/spike/recall-api-reference-read.md`); the cost model is in `docs/spike/hosted-pivot-gladia-recall-cost.md`.
