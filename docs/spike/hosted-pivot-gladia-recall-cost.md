# Hosted-bot pivot: Gladia vs Recall, who pays for AI, cost table, pros and cons

Date: 2026-11-06. Research only; nothing in the repo or on any server was changed.
Labels: **read** = I opened the page today and it says this. **vendor claim** = marketing on a page I read, not proven. **not verified** = I could not confirm it. **assumption** = my number, not a source.
Plain words: *STT* = speech-to-text (turns audio into a transcript). *Diarization* = working out who spoke when. *BYO* = bring your own (your key, your account). *Sub-processor* = a company that handles your customers' data on your behalf.

---

## 0. Short answers

1. **Gladia is a speech-to-text API company, not a meeting-bot company.** Its pricing page, its meeting-assistants page and its own FAQ describe transcription, diarization and small add-ons (summary, translation). It does not join calls itself. It plugs into bots made by others: Recall, Attendee and Meeting BaaS. One line on Gladia's home page ("Native meeting bot integration ... on demand") sounds like a bot, but the same company's FAQ says you capture audio with "a meeting bot or a native recording integration" and its pricing page never mentions a bot. Treat that home-page line as marketing, ask sales. **Gladia is therefore not a replacement for Recall. It is a possible replacement for the transcription part only.**
2. **Who pays for AI.** Recall, Skribby and MeetStream sell the bot plus an optional transcript. None of them sells summaries or "AI notes" (Recall says so on its own notetaker page: pass the transcript "to an LLM of your choice"). So the only vendor AI is the transcript, and on all three it is optional and replaceable. Our notes, decisions, tasks, open questions and translation stay our own model calls, unchanged by any bot vendor.
3. **Can we ditch their transcript?** Yes on all three, in different ways (section 3). But on Recall the saving is about zero: Recall's built-in transcript is USD 0.15/h and the cheapest third-party option I found is about USD 0.17/h. On Skribby and MeetStream the vendor passes provider cost through, so BYO saves little except against Skribby's managed Gladia, which carries a markup of about USD 0.31/h.
4. **Cost at 50 recorded hours a month (cash, our notes AI included, ops time and Workspace seats excluded):** self-hosted about USD 22 (live captions off) to USD 40 (live captions on); hosted bundled USD 25 (MeetStream or Skribby) to USD 35 (Recall); hosted with BYO transcription USD 28.5 to USD 41. Add about USD 15.50 a month for two Workspace seats if the bot is signed in, and ops time on top (section 4).
5. **Recommendation:** do not pivot wholesale now. The cash is a wash at pilot size. The real trade is ops time against privacy and vendor risk, and hosting does not fix Google's refusals. Add a vendor behind our existing bot seam, run a one-week bake-off, and keep our own transcription and AI calls. Details in section 6.

---

## 1. What Gladia is, and Gladia against Recall

### 1.1 Gladia (all read unless marked)
- **Product.** Real-time STT over WebSocket, async STT over REST, "audio intelligence" add-ons (diarization, summary, translation, entity detection, sentiment, PII redaction), SDKs, webhooks (gladia.io home page, read). Pricing page lists only two plans: Self-serve (prepaid credits) and Enterprise (custom annual). No bot on the pricing page (read).
- **Bot?** No product of its own that joins calls. Its docs describe it as the STT provider inside Attendee ("You use your own Gladia API key ... in Attendee, Settings, Credentials") and Meeting BaaS (docs.gladia.io Attendee page read; the Meeting BaaS docs page returned 404 on my direct fetch, so that one comes from search results only). Its changelog entry of 2026-06-30 is the Attendee integration (read). The Recall page `recall.ai/partners/gladia` shows `"transcription_options": {"provider": "gladia"}` and calls it "fully integrated" (read; undated example, a 2023-era API shape updated 2026-10-15). A Recall co-founder is quoted on Gladia's meeting-assistants page praising its code-switching (read).
- **Price (read, gladia.io/pricing, in euros).** Async: Solaria-3 EUR 0.16/h, Solaria-1 EUR 0.27/h; both drop (0.12 and 0.21) above 10,000 h a month. Real-time EUR 0.68/h. Add-ons per hour: summarization 0.03, translation 0.05, entity recognition 0.07, PII redaction 0.07, sentiment 0.02. Free: EUR 50 of credits valid one year. Prepaid credit wallet since July 2026 (changelog, read). At an assumed EUR 1 = USD 1.14 (assumption; the search results for today's rate conflicted): Solaria-1 about USD 0.31/h, Solaria-3 about USD 0.18/h.
  - **Careful: Solaria-3 supports only 5 languages; Solaria-1 supports 100+** (pricing page, read). Use the Solaria-1 price for multilingual customers.
  - **Stale numbers exist.** Gladia's own blog posts (read, one dated 2026-08-14) still say "Starter $0.61/hr async, Growth from $0.20/hr". The live pricing page does not show Starter or Growth. I trust the pricing page, but ask sales which is real.
- **Languages and accuracy (vendor claims).** "100+ languages", code-switching (mixing languages mid-sentence), "29% better accuracy than rivals on conversational speech", "diarization errors about 3x lower" (home page). Solaria-3: 9.6% word error rate on Gladia's own English customer-call data (changelog, 2026-06-10). No independent benchmark was read.
- **Diarization and names.** Uses pyannoteAI Precision-2; **async only, not real-time; labels are generic numbers (0, 1, ...)**. Names have to come from the bot's participant events by matching timestamps (Gladia's Recall guide, read). Included in the base price on the older plans; on the new pricing page "Available on Self-serve, but only for async" (read).
- **Real-time vs after the meeting.** Both; Gladia itself recommends async as the default for meeting assistants (FAQ, read).
- **Data and trust.** Home page and FAQ claim GDPR, SOC 2 Type II, ISO 27001, HIPAA (vendor claim). EU region: home page says "100% EU data residency" but its comparison table says "EU for Enterprise-only", and the FAQ says it keeps "EU and US clusters" (all read; **conflicting, ask**). **Training on your audio is opt-in by default on Self-serve (you must opt out in your profile); off by default on Enterprise; zero retention is Enterprise only** (pricing page, read). The FAQ says paid plans do not train by default; the two statements conflict, so opt out explicitly. No default retention period stated.
- **Bring your own provider?** n/a (it is the provider). It accepts a file URL: `POST /v2/pre-recorded` with `audio_url`, `diarization`, `language_config`, `translation`, `summarization` returns `id` and `result_url` (API reference, read; the result JSON shape was not on the page, **not verified**).
- **Gladia's own meeting-note output.** Summarization types are `general`, `bullet_points`, `concise` only (read). No decisions, owners or tasks.

### 1.2 Recall (read unless marked)
- **Product.** Hosted meeting-bot API for Zoom, Meet, Teams (also Webex, GoTo, Slack) with recordings, transcript, participant data and real-time streams (recall.ai product page, read, vendor claims). Create a bot with a meeting URL. Not self-hostable.
- **Meet join.** Guest by default; signed-in only with Google accounts **we** create in our own dedicated Workspace with Legacy SSO and a certificate. "Creating Recall resources does not create Google users"; each account supports 30 concurrent bot sign-ins; start with two (docs.recall.ai google-meet-login-getting-started, read). No join-success rate published (see earlier report).
- **Price (read).** USD 0.50/h recording (billed to the second, first 5 h free, Pay-as-you-go); built-in transcript USD 0.15/h; 7 days storage free then USD 0.05 per hour retained per extra 30 days; +USD 0.10/h 4-core bot; +USD 1/h GPU bot; startups can get USD 0.25/h for first 10,000 h; Calendar API free (recall.ai/pricing, blog). Per transcribed hour, "perfect diarization" in real time uses about 1.8x the transcript credits; async about 1x ("no separate diarization fee"; diarization guide, read).
- **Transcript languages.** Recall's own transcript: real-time low-latency mode English only; accurate mode `auto` plus about 40 codes; `key_terms` (custom vocabulary) English only; no PII redaction (docs recallai-transcription, read).
- **Diarization and names.** On Meet, "perfect diarization" transcribes each participant's separate audio stream and returns the participant's name; speaker-timeline and machine diarization also available (diarization guide, read). The product page claims "100% perfect speaker diarization" (vendor claim).
- **Real-time vs after.** Both; real-time via webhook/websocket (product page).
- **Data and trust.** Four separate regions: US West, US East, EU Frankfurt, Tokyo; on Pay-as-you-go you cannot switch, you need a new account (regions doc, read). **New accounts (after 2025-06-12) keep media forever unless you set retention** (timed, forever, or zero) (data-retention doc, read). Delete Recording / Delete Bot Media are permanent; deleting does **not** delete data held by a third-party transcription provider (read). SOC 2, ISO 27001, GDPR, CCPA, HIPAA claimed (product page, vendor claim). Recall's own security page returned 404 today; I could not read its SOC 2 report, DPA or sub-processor list (**not verified**).
- **BYO transcription.** Yes (section 2).

### 1.3 Side by side

| | Gladia | Recall |
|---|---|---|
| What it is | STT API (plus small add-ons) | Meeting-bot API (plus optional STT) |
| Joins Meet/Zoom/Teams | **No** (works behind bots) | Yes |
| Meet join method | n/a | Guest, or signed-in with our own Workspace accounts |
| Price | EUR 0.27/h async Solaria-1 (about USD 0.31); EUR 0.16 Solaria-3 (5 languages); EUR 0.68 real-time | USD 0.50/h bot + USD 0.15/h own transcript |
| Languages | 100+ (Solaria-1) or 5 (Solaria-3); code-switching (vendor claim) | Own transcript about 40 (accurate mode), English only for low-latency; other providers have their own lists |
| Diarization | Async only; numbers, not names | On Meet: named participants from separate streams |
| Real-time | Yes (EUR 0.68/h, no diarization) | Yes |
| Data region, retention | EU/US clusters, EU possibly Enterprise-only; training opt-out needed on Self-serve; ZDR Enterprise only | 4 regions; retention you set; default forever on new accounts |
| Certifications | SOC 2 II, ISO 27001, HIPAA, GDPR (vendor claim) | SOC 2, ISO 27001, GDPR, CCPA, HIPAA (vendor claim); report not seen |
| API shape | `POST /v2/pre-recorded {audio_url, ...}` then poll `result_url` | `POST /api/v1/bot {meeting_url, recording_config}` then webhooks, transcript and recording download |

### 1.4 How the two combine
- **Recall's bot + Gladia as transcriber, option 1 (documented by both).** Recall records; when its recording-ready webhook fires, we send the recording URL to Gladia async, get a transcript with numbered speakers, and map numbers to names from Recall's `participant_events` by timestamp (Gladia blog 2026-08-14, read). We hold the Gladia key and pay Gladia directly. Recall bills the bot only.
- **Option 2.** Select `gladia` inside Recall's bot request (the old `transcription_options` example on recall.ai/partners/gladia). Whose key and who pays is **not stated** on any page I read, and the current meeting-bot provider table in Recall's transcription doc does **not list Gladia** (it lists Deepgram, AssemblyAI, ElevenLabs, AWS, Rev, Speechmatics, Soniox, Grok); Gladia appears only in the Desktop SDK real-time list as `gladia_v2_streaming`. So option 1 is the safe one.
- **Gladia directly on audio we receive.** Works with any bot that gives us audio (Recall MP3, Skribby recording, Vexa recording): same call as option 1.

---

## 2. Who pays for AI, per vendor

| | Recall | Skribby | MeetStream | Gladia |
|---|---|---|---|---|
| In the bot price | Recording only (USD 0.50/h) | Bot + raw audio recording + realtime webhooks (USD 0.35/h) | Bot (USD 0.35/h) | n/a |
| Transcript | Extra USD 0.15/h (own engine, model not named) | Extra, per model: all-in USD 0.39 to 1.36/h | Extra USD 0.10/h (diarized) | The product itself |
| Summaries / "AI notes" | **Not sold** (page says use your own LLM) | **None found** on pages read; search shows transcript-and-data only (not verified) | **None listed as sold** | Summary EUR 0.03/h, translation EUR 0.05/h (basic) |
| Force their AI on us? | No | No (`none` = audio only, free) | No | No |
| BYO transcription | Yes: add each provider key in the Recall dashboard, per region. Bot providers: Deepgram, AssemblyAI, ElevenLabs, AWS Transcribe, Rev, Speechmatics, Soniox, Grok (Grok real-time only). **No OpenAI, no Gladia in that table.** | Yes, keys saved in dashboard, ID passed as `transcription_credentials`. Providers: Deepgram, **OpenAI** (gpt-4o-mini-transcribe, gpt-4o-transcribe, gpt-4o-transcribe-diarize; **not gpt-transcribe**), xAI, AssemblyAI, Speechmatics, Rev AI, **Gladia**, ElevenLabs, Soniox, Mistral, Google, AWS. Invalid key means no fallback (`invalid_api_key`) | Yes, keys saved under Integrations. Providers: Deepgram, AssemblyAI, JigsawStack, Sarvam (+ MeetStream's own, and Meet/Teams captions, English only). **No OpenAI, no Gladia** | n/a |
| Price effect of BYO | Recall's pages do not say whether it adds any fee; you pay the provider directly (blog, read). Treat "no extra fee" as **not verified** | Standard model: bot rate only, provider bills you. Realtime model: +USD 0.05/h (read, billing and credentials guides) | "No extra charge"; the diarized transcript fee USD 0.10/h disappears (read) | n/a |
| Raw audio/video | Yes: add `recording_config.audio_mixed_mp3` and download after the call; MP4 video on by default; download URL per recording. Separate per-participant tracks: not clearly documented | Raw audio recording included; model `none` = audio only; recordings are WebM; real-time raw PCM. Default retention 1 week (1 year +USD 0.05/h) | Recordings to our own S3, R2 or OSS bucket; audio, video, transcript; mixed audio download exists. Audio-only option not confirmed | n/a |
| Speaker names | Named participants on Meet (separate streams) | Participant list supported; how names land in transcripts is **not verified** | Speaker labels on Meet; real names **not verified** | Numbers only |

**Our own AI is separate and unchanged.** From the repo (all estimates, nothing measured on a real hour yet; `docs/sop/2026-11-02-notes-model-gpt-6-luna.md`, `2026-11-05-extraction-output-cap.md`, `2026-11-04-w7-translate.md`):

| Our model call (gpt-6-luna, USD 0.10 in / 0.50 out per million tokens) | Per meeting hour |
|---|---|
| Speaker labelling + notes | about USD 0.02 (about 45k in, 17k out) |
| Extraction (decisions, tasks, open questions) | about USD 0.004; retry worst case about 0.02 |
| Translation | about USD 0.01 per language (about USD 0.03 with heavy reasoning) |
| **Total used below** | **USD 0.05/h** (labelling, notes, extraction, one translation, with a small margin; assumption built from those estimates) |

The ledger `ai_runs` will give the real number after a few meetings (`docs/checklist/master-checklist.md` still lists "Real OpenAI spend per meeting hour" as open). The earlier spend note (`docs/spike/cost-and-capacity-2026-11-03.md`) measured USD 1.54 total over about 1.0 recorded hour including experiments, so the all-in figure there is dominated by transcription, not notes.

**OpenAI transcription price confirmed (read, developers.openai.com/api/docs/pricing, today):** gpt-transcribe **USD 0.0045/min** (= USD 0.27/h); Whisper USD 0.006/min (USD 0.36/h); gpt-4o-transcribe USD 0.006/min; gpt-4o-mini-transcribe USD 0.003/min (USD 0.18/h); gpt-4o-transcribe-diarize USD 0.006/min; live transcription models USD 0.017/min (USD 1.02/h). The team's USD 0.0045 figure is right for gpt-transcribe.

Other providers' direct prices (read today): Deepgram Nova-3 batch multilingual USD 0.0052/min = USD 0.31/h, diarization included (monolingual USD 0.26/h); AssemblyAI Universal-2 USD 0.15/h, Universal-3.5 Pro USD 0.21/h, diarization +USD 0.02/h (so USD 0.17 and 0.23).

---

## 3. Cost table

### 3.1 Inputs and assumptions
- Our notes AI: USD 0.05/h (section 2). Same on every row.
- Transcription: OpenAI gpt-transcribe USD 0.27/h (read). Vexa's live captions on whisper-1 USD 0.36/h at the Whisper rate (read), if live captions stay on; whether they must is a product choice (the earlier note assumed both run).
- **Server.** The Contabo box costs about USD 70 a year = USD 5.83/month for the whole machine, shared with Haven and Novex (cost note, Adebayo's statement). One bot used about 1.4 of its 4 cores and the host load was 4.1 (measured, one sample). Honest reading: by headroom the bot is the main user, so our fair share is at least a third (about USD 1.94) and more likely most of it; today's real extra cash is about zero because the box is paid for anyway. For a credible plan I use the cost note's own recommendation, a dedicated bot machine: **USD 6/month at 50 h, USD 12 at 200 h, USD 25 at 500 h** (assumption; the note's estimate for an 8-core box was USD 15 to 40, unverified).
- **Ops time (separate line, assumption):** self-hosted 2 / 3 / 5 hours a month at USD 40/h = USD 80 / 120 / 200 (Docker disk, Vexa upgrades, bot image, join failures). Hosted: 0.5 / 0.5 / 1 hour a month = USD 20 / 20 / 40 (vendor integration, Workspace account upkeep, webhooks). My guesses; change them.
- **Workspace seats for a signed-in bot:** Business Starter is **EUR 6.80 per user per month on a 1-year commitment** (read, workspace.google.com/pricing, today; new-customer 10% off from 2026-11-20 for the first 20 users, EUR 6.12; no separate monthly price shown; page is in euros). A search result put it at USD 7 (annual) and about USD 8.40 (monthly), not read on Google's own page. Two accounts = 2 x 6.80 x 1.14 = **about USD 15.50/month**. Recall and MeetStream require a dedicated Workspace for signed-in bots (Recall: read; MeetStream: read earlier). Skribby takes a Gmail or Workspace account typed into its dashboard, so a consumer Gmail costs USD 0 (Google's terms on automated use of a consumer account: not verified). Guest-only joins need no seats but are what Google refuses.
- Exchange rate EUR 1 = USD 1.14 (assumption, not verified).

### 3.2 Per recorded hour

| Setup | Bot | Transcript | Our notes AI | **Per hour** |
|---|---|---|---|---|
| A1 Self-host Vexa Lite + OpenAI, live captions on | server (below) | 0.27 + 0.36 = 0.63 | 0.05 | 0.68 + server |
| A2 same, live captions off | server | 0.27 | 0.05 | 0.32 + server |
| B1 Recall, own transcript | 0.50 | 0.15 | 0.05 | **0.70** |
| B2 MeetStream, own diarized transcript | 0.35 | 0.10 | 0.05 | **0.50** |
| B3 Skribby, Soniox async (diarized) | 0.35 | 0.10 (0.45 all-in) | 0.05 | **0.50** (0.44 with Groq Whisper, 0.60 with AssemblyAI Universal) |
| C1 Recall + OpenAI gpt-transcribe on the MP3 | 0.50 | 0.27 | 0.05 | **0.82** |
| C2 Recall + AssemblyAI (its provider table) | 0.50 | 0.17 | 0.05 | **0.72** |
| C3 Recall + Gladia Solaria-1 on the recording | 0.50 | 0.31 | 0.05 | **0.86** |
| C4 Skribby audio-only + OpenAI gpt-transcribe | 0.35 | 0.27 | 0.05 | **0.67** |
| C5 Skribby or MeetStream + AssemblyAI BYO | 0.35 | 0.17 | 0.05 | **0.57** |
| C6 MeetStream + Deepgram BYO | 0.35 | 0.31 | 0.05 | **0.71** |
| D Hosted bot + vendor AI notes | none of Recall, Skribby, MeetStream sells AI notes; the only vendor-side text AI I found is Gladia's add-ons: summary + translation EUR 0.08 = USD 0.09/h for a general bullet summary that has no decisions or owners. Not a replacement for our extraction, and not cheaper than our USD 0.05 |

Arithmetic for the transcript column: gpt-transcribe 0.0045 x 60 = 0.27; Whisper 0.006 x 60 = 0.36; Gladia 0.27 x 1.14 = 0.31; AssemblyAI 0.15 + 0.02 = 0.17; Skribby Soniox 0.45 all-in minus 0.35 base = 0.10; Skribby bundled Gladia all-in USD 0.97 minus 0.35 = 0.62 (so a Gladia call from Skribby costs about USD 0.31/h more than calling Gladia with our own key).

### 3.3 Per month (cash; ops and seats are separate lines)

| Setup | 50 h | 200 h | 500 h |
|---|---|---|---|
| **A1** Self-host, live on: 0.68 x h + server (6 / 12 / 25) | 34 + 6 = **40.0** | 136 + 12 = **148** | 340 + 25 = **365** |
| **A2** Self-host, live off: 0.32 x h + server | 16 + 6 = **22.0** | 64 + 12 = **76** | 160 + 25 = **185** |
| **B1** Recall bundled: 0.70 x h | **35** | **140** | **350** |
| **B2** MeetStream bundled: 0.50 x h | **25** | **100** | **250** |
| **B3** Skribby (Soniox): 0.50 x h | **25** | **100** | **250** |
| **C1** Recall + OpenAI own: 0.82 x h | **41** | **164** | **410** |
| **C2** Recall + AssemblyAI: 0.72 x h | **36** | **144** | **360** |
| **C4** Skribby audio-only + OpenAI: 0.67 x h | **33.5** | **134** | **335** |
| **C5** Skribby or MeetStream + AssemblyAI: 0.57 x h | **28.5** | **114** | **285** |
| D | not offered by these vendors (see above) | | |
| **Add: Workspace seats, 2 accounts** (Recall, MeetStream signed-in; Skribby optional) | +15.5 | +15.5 | +15.5 |
| **Add: ops time, self-hosted (assumption)** | +80 | +120 | +200 |
| **Add: ops time, hosted (assumption)** | +20 | +20 | +40 |
| Recall free allowance: first 5 h free once; Skribby and MeetStream have small free credits (MeetStream USD 5) | -2.5 to -3.5 once | | |

Reading it:
- **Cash is close at pilot size**: A2 USD 22 and B2 USD 25 at 50 h. Self-hosting only gets clearly cheaper if live captions are off and the server stays small.
- **The bundle is not the lever.** Recall's USD 0.15 transcript about equals AssemblyAI's USD 0.17 BYO, so "ditching their AI" saves nothing on Recall. On Skribby and MeetStream BYO saves USD 0 to 0.10/h at best. Switch transcription only for quality, language coverage, custom vocabulary or privacy reasons.
- **The notes AI is under 10% of any row**. At 500 h it is USD 25 a month against USD 250 to 410 for bot plus transcript. The bill that matters is the bot and transcript.
- **Ops time is the swing line** in either direction; with my assumptions it exceeds the cash at pilot size, which is the owner's actual argument. It is the least reliable number here.
- The vendors' own bulk discounts (Recall Launch/Enterprise, MeetStream USD 0.25/h at volume) would lower rows at 200 and 500 h; not published, not applied.
- Billing traps: MeetStream and Skribby bill waiting-room time once the bot is admitted (Skribby: first 10 min free if never admitted; MeetStream: waiting time billed unless `waiting_room_timeout` is short, never-joined bots free). Recall bills recording to the second.

---

## 4. Pivot to hosted: honest pros and cons for about 10 accounts

**What we stop managing.** The Vexa Lite container and its upgrades (the Meet 403 fix sits in v0.12.29, unmerged authenticated mode in #1873), the bot image and Chromium, Docker disk and recording storage, CPU contention with Haven and Novex (one bot already uses about 1.4 of 4 cores), per-bot concurrency, and part of the join-reliability work (IP rotation, retries). Skribby, for instance, retries blocked joins up to 3 times with new IP ranges and fingerprints (read earlier).

**What stays ours either way.** The app, database, worker, our notes/extraction/translation calls, calendar sync and scheduling, the usage ledger, the privacy and terms pages, and the integration code (webhook handler, mapping the transcript shape, the seam behind `VexaClient`).

**Gains.** No server care for the bot; concurrency beyond 1 to 5 bots without a new machine; a documented failure vocabulary (Recall `google_meet_bot_blocked` and friends); EU region available on Recall; regional processing choices; a vendor that is paid to track Google, Zoom and Teams changes; Zoom and Teams later without building anything.

**Losses and risks.**
1. **Privacy.** Recordings leave our server. The vendor becomes a sub-processor; so does any transcription provider we choose. Recall keeps media forever by default on new accounts and deleting does not reach third-party transcribers. Skribby's default region is EU, but some model providers are US-only or global (read earlier; the privacy guide today lists OpenAI diarize as "global processing"). Gladia Self-serve trains on your audio unless you opt out. Privacy policy, DPA and sub-processor list must be updated before real customers.
2. **The Google refusal is not solved by hosting.** Recall tells us to bring our own Workspace accounts and says nothing on a join-success rate; no vendor publishes one. Signed-in bots only skip the knock if the account is on the invite. Only the vendor's IP pool and retries are an improvement, and nobody has proven how much. (earlier report, re-checked on Recall's signed-in guide today.)
3. **Lock-in.** Light if we keep the seam: the four calls (create, status/transcript, stop, delete) are similar across all three, and MeetStream even documents a Recall migration. Transcript shapes and speaker-name handling are the sticky part.
4. **Vendor risk.** Skribby was bought by Alianza in September 2026 (earlier report; search result only); MeetStream's owners are not identified and SOC 2 is "under audit"; Recall is the best funded (USD 38M Series B, Sept 2025) but its security documents were unreachable today.
5. **Unit cost grows with use**, our server does not; at 500 h hosted is roughly USD 250 to 410 a month against USD 185 to 365 self-hosted before ops time.
6. **Capacity and limits.** Skribby Pay-as-you-go: 25 concurrent bots; Recall and MeetStream: not published.

---

## 5. What I could not verify
- Gladia's own meeting bot ("on demand" line): ask. Gladia Meeting BaaS docs page (404 on direct fetch).
- Recall: whether BYO transcription adds any fee; who pays when `provider: gladia` is set inside a Recall bot; SOC 2 report, DPA, sub-processors (trust and security pages 404).
- Any join-success rate for Meet, any vendor.
- MeetStream: company identity, region, SOC 2 report, speaker real names, audio-only option; Skribby: speaker-name behaviour on Meet, whether it has any summary feature (search only).
- Independent accuracy for any transcript engine; all accuracy numbers above are vendor claims.
- USD price of Workspace Business Starter on Google's own page (only EUR shown); EUR/USD rate.
- Our real notes-AI cost per hour (estimates; ledger rows will settle it).

---

## 6. Recommendation and order of steps

**For the pilot: do not pivot yet; make the hosted bot a tested second option.** Reasons: cash is a wash at 50 h; the Google refusal, the thing that actually hurts, is not solved by hosting alone; recordings leaving our server is a policy change we have promised customers nothing about yet.

Order:
1. Finish the cheap local fixes first (v0.12.29 upgrade and the E0/E2 experiment from `docs/spike/meet-join-resilience.md`), so the baseline join rate is known.
2. Build the vendor adapter behind the existing seam for **Skribby first** (nearest to our adapter; BYO OpenAI or audio-only; free tier; a Gmail account is enough for a signed-in test) and **Recall second** (5 free hours; needs the Workspace + SSO setup, half a day to a day by the earlier estimate).
3. One-week bake-off on the same real meetings: count joins, knocks, refusals, speaker-name quality, cost per hour from each dashboard. Send the vendor's audio to **our** transcription (gpt-transcribe, our keyword hints, which Recall's own transcript cannot match because its `key_terms` are English only).
4. Decide with real numbers: if the hosted join rate is clearly better than ours, switch the bot and keep everything else; if not, stay self-hosted on its own machine.
5. Before any customer data goes to a vendor: DPA, sub-processor line in the privacy policy, retention set explicitly (Recall: not "forever"), region choice.
6. Gladia: use it only if we want better multilingual transcription or cheaper hours than gpt-transcribe; call it on the recording after the meeting (async, with diarization), then map speaker numbers to names from the bot's participant events. Do not expect it to replace the bot.

**Top questions for Recall sales**
1. What is the Meet join success rate for signed-in versus guest bots on your own numbers, and how much does a signed-in bot reduce `google_meet_bot_blocked`?
2. If we bring a Deepgram or AssemblyAI key, do you charge anything beyond the USD 0.50/h recording fee? Is Gladia supported as a bot provider (async), and who pays?
3. Can we have the SOC 2 Type 2 report, the DPA, the EU (Frankfurt) sub-processor list, and confirmation that zero-retention plus our own transcription means no media at rest on your side?
4. What is the concurrency ceiling on Pay-as-you-go, and what discounts apply at 200 and 500 h a month?

**Top questions for Gladia sales**
1. Do you sell or operate any bot that joins Meet, Zoom or Teams (your home page says "native meeting bot integration on demand")? If yes: join method, price, success rate.
2. On Self-serve, is our audio used for training by default (pricing page says opt-in by default, FAQ says not on paid plans)? Is EU processing available on Self-serve or Enterprise only, and is zero retention available below Enterprise?
3. Real pricing today: pricing page says EUR 0.27/h async Solaria-1, your blog says USD 0.61/h Starter. Which applies, and is diarization included? Which model do you recommend for 100+ languages with code-switching, and how does diarization handle 6-person calls with numbered speakers?
4. Can you share independent benchmark data for meeting audio and a DPA with sub-processor list?

---

## Sources (all opened on 2026-11-06 unless noted)

Repo (read): `docs/spike/cost-and-capacity-2026-11-03.md`, `docs/spike/meet-join-resilience.md`, `.claude/worktrees/lead-w4/docs/spike/meeting-bot-vendors.md`, `docs/sop/2026-11-02-notes-model-gpt-6-luna.md`, `docs/sop/2026-11-05-extraction-output-cap.md`, `docs/sop/2026-11-04-w7-translate.md`, `docs/sop/2026-11-05-notes-cost-ledger.md`, `docs/checklist/master-checklist.md`.

Gladia (read): https://www.gladia.io/pricing; https://www.gladia.io; https://www.gladia.io/use-cases/meeting-assistants; https://www.gladia.io/blog/gladia-meeting-assistants-note-takers-30-customer-faqs; https://www.gladia.io/blog/add-speech-to-text-to-a-recallai-meeting-bot (dated 2026-08-14); https://www.gladia.io/changelog; https://docs.gladia.io/chapters/integrations/attendee.md; https://docs.gladia.io/api-reference/v2/pre-recorded/init; https://www.recall.ai/partners/gladia; https://www.recall.ai/blog/how-to-receive-multilingual-meeting-transcription-with-the-recall-ai-and-gladia-integration. Search-result only (not opened): https://docs.gladia.io/chapters/partners-integrations/meeting-recorders/meeting-baas (404 when fetched), https://www.meetingbaas.com/en/meeting-transcription-api/gladia.

Recall (read): https://www.recall.ai/pricing; https://recall.ai/blog/new-recall-ai-pricing-for-2026; https://www.recall.ai/product/meeting-bot-api; https://recall.ai/product/notetaker-api; https://docs.recall.ai/docs/transcription.md; https://docs.recall.ai/docs/recallai-transcription; https://docs.recall.ai/docs/deepgram; https://docs.recall.ai/docs/dsdk-realtime-transcription.md; https://docs.recall.ai/docs/diarization; https://docs.recall.ai/docs/regions; https://docs.recall.ai/docs/data-retention; https://docs.recall.ai/docs/how-to-get-mixed-audio-async; https://docs.recall.ai/docs/google-meet-login-getting-started. https://www.recall.ai/security returned 404.

Skribby (read): https://skribby.io/pricing; https://skribby.io/docs; https://skribby.io/docs/guides/billing; https://skribby.io/docs/guides/transcription-credentials; https://skribby.io/docs/guides/transcription-models; https://skribby.io/docs/guides/platforms; https://skribby.io/docs/guides/transcription-privacy.

MeetStream (read): https://meetstream.ai/pricing; https://docs.meetstream.ai/guides/transcription-recordings/post-call-transcription.md; https://docs.meetstream.ai/guides/features/usage-and-retention.md.

Other (read): OpenAI https://developers.openai.com/api/docs/pricing (redirect from platform.openai.com/docs/pricing); Deepgram https://deepgram.com/pricing; AssemblyAI https://www.assemblyai.com/pricing; Google Workspace https://workspace.google.com/pricing?hl=en (EUR only).

Search results only (not opened): Workspace USD 7 / 8.40 figures (hostraha, eesel and similar third-party pages); EUR/USD rate (pluang.com, allratestoday.com; conflicting); Recall "no built-in summaries" via search plus the opened notetaker page; Skribby no-AI-summary via search.
