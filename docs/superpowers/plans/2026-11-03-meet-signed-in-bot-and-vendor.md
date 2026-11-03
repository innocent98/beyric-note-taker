# Meet join plan B: a signed-in bot account, or a hosted vendor

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. This is a **research and decision plan**: sections 1 to 4 are for Adebayo, section 5 holds the one-session tasks. Nothing here is built yet. Steps use checkbox syntax.

**Goal:** a way into Google Meet that does not depend on anonymous guest joins, chosen by evidence from the Mac experiment and the v0.12.29 upgrade.
**Architecture:** the pilot plan already has a `bot_hosts` seam (Phase D). Track A adds a host that runs signed-in Vexa bots; Track B adds a host that is a hosted vendor. Both sit behind one `BotProvider` interface, so the worker and pipeline stop knowing about Vexa.
**Tech stack:** FastAPI worker (`app/vexa.py`, `app/pipeline.py`, `app/services.py`), Vexa Lite today, Vexa Compose or a custom bot image (Track A), Recall.ai or similar (Track B).
**Spec:** `docs/spike/meet-join-resilience.md` (items 2, 3 and 6 are the starting point); `docs/superpowers/plans/2026-11-03-pilot-accounts-calendar.md` (Phases C and D); spec `2026-11-03-pilot-accounts-calendar-design.md` (Q1: never write to calendar events).

**Evidence labels (as in the spike):** *read* = I read the source or page; *vendor* = a vendor's own page or docs; *third party* = someone else's page about a vendor; *not verified* = needs a live test or an answer from the vendor. Everything below was read on **2026-11-03** unless stated. Fetch tools summarised several pages; where a page refused (403) I say so. No account was created, no server was touched.

## Global Constraints

- No account is created or signed in by a session. Adebayo creates every account and holds its recovery details.
- No secret is printed or committed. Session cookies, API keys and S3 keys are credentials.
- Docs only in this PR. No change to `app/`, `deploy/` or the server.
- No Google write scope on calendars (spec Q1). Any option that needs one is rejected here.
- Never swap images or hosts while a meeting is `bot_sent` or `processing`.

## Review Focus

- A vendor refusal or a lost Google session must end the meeting fast and visibly, never a silent wait (the fail-fast item still comes first).
- Recordings leaving our server must be in the privacy policy before the first real recording goes to a vendor.
- A stored Google session is a credential: where it lives, who can read it, what happens when it decays.

---

## 1. How comparable products get in

**The short answer: they mostly do not have a trick.** The easy-looking part is the calendar, not the door.

| Product | How it enters Meet (evidence) | What that tells us |
|---|---|---|
| **Fireflies** | Joins **as a guest**. The host must admit it from the waiting room; it waits up to 10 minutes on Meet, then leaves. Added by auto-join, by putting its address on the invite, or "Add to live meeting". Its guide tells admins to allow "users not signed in", and since Google's 2026 change it shows under "With potential risks" in the participants panel (vendor: [FAQ](https://guide.fireflies.ai/articles/9554534786-how-fireflies-joins-and-records-your-meetings-faqs), [Meet guide](https://guide.fireflies.ai/articles/7581948912-how-to-invite-fireflies-to-google-meet-meetings)) | A guest bot, like ours. It is not magic. The invite address is how it learns the meeting, not how it skips the knock (the page does not say it skips) |
| **Otter** | Calendar auto-join for events with a Meet, Zoom or Teams link. Appears as a **guest** named after the user; the meeting must allow guests; it cannot sign in to an organisation's account to get round guest limits (third party summary of help.otter.ai; the help page itself returned 403 to me) | Same as Fireflies |
| **Fathom** | Calendar detection in a **desktop app plus a Chrome extension on the user's machine**; the Meet link must be in the event's location field; "Add to Meeting" pastes a link and it joins at once (vendor: [help](https://help.fathom.video/en/articles/449472)). The page does not say which account the bot uses | The user's own client drives it. How the bot enters is **not verified** |
| **Read.ai** | Since 2026-03-26 a **botless** Meet integration on Google's Meet Media API, free, with a consent notice before recording, plus auto-join for unscheduled meetings (vendor: [blog](https://read.ai/post/read-ai-introduces-a-botless-integration-with-google-meet)). Older help pages described a participant bot | The spike's finding stands: Meet Media API is a preview that we cannot rely on. Read says it launched on it; the conditions for a third party are **not verified** |
| **Recall.ai** | The infrastructure many notetakers reportedly sit on (third party: sub-processor pages of HighlightAI and Modjo name it; **I found no source that Fireflies, Otter, Fathom or Read use it**). Its bots join as guests unless login is required (login mode `only_if_required` is its recommendation). Signed-in bots need **the customer's own dedicated Workspace** (standard user, own OU, Legacy SSO), Recall pools the logins, 30 concurrent sign-ins per Google account, `google_meet_login_not_available` when a pool is full. A signed-in bot **skips the waiting room only if its address is on the event** (a Google group address with the bot accounts as members works). It still can show Google's risk label (vendor: [login guide](https://docs.recall.ai/docs/google-meet-login-getting-started)) | Even the vendor with a pool tells you to bring a Workspace. The pool is a convenience, not a secret door |
| **Blocks happen to everyone** | Recall documents `google_meet_bot_blocked`: Google's own detection can refuse a bot, a flagged **bot name** triggers it, and a host who unticks "Anyone with the meeting link can ask to join" auto-declines guests (vendor: [Meet FAQ](https://docs.recall.ai/docs/google-meet-faq)). Metaview, tl;dv and Airspeed say Google began labelling all notetaker bots "potential risks" in **late March 2026**, hiding the usual Admit button and in some cases defaulting to deny (third party, **not verified** against Google's own notes) | Our 403 is a known class. March 2026 is context, not a proven cause: our meetings 4 and 5 joined after it |

### Fact versus guess

| Question | Answer |
|---|---|
| Do they use signed-in dedicated accounts? | **Recall: yes, as an option the customer must set up.** Fireflies and Otter: their own pages describe guest joins, so by their own account, no. Others: **not verified** |
| Does the calendar invite get them past the door? | **Only for a signed-in account whose address is on the event** (Google's rule: [admin help](https://support.google.com/a/users/answer/12172260)). Guests invited by address still knock |
| Account pools and managed egress? | Recall pools signed-in accounts; **its egress addresses are not documented** and its FAQ does not mention addresses. Pools of residential addresses for anyone: **a guess** |
| Partnerships? | Recall and Zoom announced a real-time data partnership (a Recall blog title seen in search, not read); Read built on Meet Media API. Google has no public partner route for a bot. **Not copyable now** |
| Why does it *feel* easy? | Most of their meetings are the user's own, the user is the host, and the host admits the bot in one click. We are in that same position. What they have that we lack is **volume and tuning**: many accounts, many addresses, a team that watches Google's changes |

### What we could copy

| Idea | Cost | Verdict |
|---|---|---|
| Guest bot, host admits (we do this) | Done | Keep as the default |
| Signed-in account on the invite | One Workspace seat per concurrent bot, plus engineering (section 2) | Track A |
| Pool and egress managed by someone else | Per-hour vendor fee (section 3) | Track B |
| Botless via Meet Media API | Not available to us | Skip |
| Meet add-on | Cannot capture participant audio (spike section 6) | Skip |

---

## 2. Track A: our own signed-in bot account

### 2.1 Decisions for Adebayo

| # | Decision | Recommendation | Why, and what is not verified |
|---|---|---|---|
| A-D1 | **Workspace seat or consumer Gmail** | **A Workspace seat**, Business Starter | Vexa says "a dedicated account, not a personal one" and leans on Workspace controls; Recall requires a Workspace for signed-in bots. Google's consumer terms (effective 2026-07-30, Nigeria version) have **no rule against a software-driven account**; they bar automated access that ignores robots.txt, fake accounts and hiding who you are, and let Google suspend an account (read: [terms](https://policies.google.com/terms)). A consumer account is also the first to hit "this browser may not be secure" checks (anecdotes only). **Not verified:** Workspace's own additional terms on bot use; read them before paying |
| A-D2 | **Price** | Starter, per user per month, annual: **EUR 6.80 list, EUR 6.12 introductory for the first 20 users** (page read 2026-11-03, shown in EUR; third-party sites say USD 7 annual, USD 8.40 monthly). Starter does **not** include Meet recording, which the bot does not need | Confirm at checkout. One seat per concurrent bot |
| A-D3 | **Domain** | A bot address on `beyrictech.com`, e.g. `notes-bot@beyrictech.com`, which means putting the domain on Workspace (handoff: it is not Workspace today) | The same Workspace could host the planned `privacy@`, `legal@`, `security@` mailboxes. **Not verified:** whether Workspace can be set up for the domain without moving existing mail |
| A-D4 | **Recovery email and phone** | Held by Beyrichq: a shared company mailbox and a company phone number, never one person's | A lost bot account with a personal recovery phone is a dead product |
| A-D5 | **Name and photo** | Display name **Beyric Note Taker (recording)**, photo our logo. A signed-in bot shows the **account's** name, not `BOT_NAME` (Recall) | Recall warns innocent names can be blocked; test the name in a private window first (spike step E0) |
| A-D6 | **Who runs `make login`** | Adebayo, once per account, from his home connection with a phone at hand | A sign-in from a datacenter address is where Google challenges (spike section 2). The bot must then keep that address stable |

### 2.2 What changes in our stack

| Piece | Today | Under Track A |
|---|---|---|
| Vexa | **Lite**, which has no authenticated mode (docs) | **Full Compose stack** (eleven images in v0.12.28), or Lite plus a patched bot (not possible: Lite has no userdata store) |
| Bot image | `vexa-bot:v012`, launches `--incognito` and ignores the signed-in profile (#1725) | Our own build from **PR #1873** (open since 2026-10-24; independently confirmed working on 2026-11-02; maintainers' merge card still lists missing sign-offs, so **no date**). Dockerfile based on the Vexa tree at the pinned commit plus the patch; rebuild when upstream merges. Licence: Adebayo confirmed commercial use (his statement; text not re-read) |
| Where it runs | One Vexa Lite on the shared VPS (4 vCPU, 7.8 GB, with Haven and Novex) | **Not on the shared VPS.** Eleven images will not fit beside two other products. Put it on the **bot machine** Phase D already plans (P0.6), as a `bot_hosts` row. **Idle and per-bot memory of the full stack is unmeasured**; the pilot plan's 8 GB estimate was for Lite. First task measures it |
| One bot per account | n/a | Vexa returns 409 for a second concurrent spawn on the same stored profile. `BOT_AUTHENTICATED` is deployment-wide, so **N parallel signed-in bots means N deployments**, each with its own account and S3 prefix. Google itself allows many concurrent sign-ins (Recall quotes 30 per account), so this is Vexa's profile lock, not Google's. **Not verified:** whether two bots sharing one profile copy would be tolerated |
| Zoom on the same host | Works anonymously | **Not verified** whether Zoom bots still spawn on an authenticated deployment (a missing store is a 503 at spawn). Until answered, route Zoom to the Lite host, Meet to the signed-in host: `bot_hosts.platforms` |

### 2.3 Operating it

| Concern | Plan |
|---|---|
| **Provisioning** | `make login` on Adebayo's Mac (headed browser; Vexa checks the session is real, then uploads about 200 KB of auth files to `BOT_USERDATA_S3_PATH`). Needs the `aws` CLI and scoped keys to the object store; the store must be reachable from the Mac (an SSH tunnel to the bot machine). Runbook step list written in task A4. Never paste keys |
| **Session refresh** | Vexa writes the rotated profile back on each **clean** teardown only, so a killed bot loses it. Never `docker kill` a running bot; the worker stops through the API. Nightly volume backup of the store |
| **Keep-warm** | Idle accounts decay and Vexa ships no job. Proposal (**design, not verified**): a weekly calendar event on the bot account's own calendar with a Meet link, which the bot joins alone for 2 minutes and leaves cleanly. Alternative: a real meeting each week |
| **Alerts** | The worker maps `completed(auth_session_missing)` (permanent, no retry, appears in about 10 s) to: meeting failed with a plain reason, host marked `auth_ok=false` so placement stops sending bots to it, one admin alert through C11b (`notify_admins(kind="admin.bot_auth")`), recovery text for the admin page |
| **Runbook: "Google asked for a re-login"** | 1. Alert arrives. 2. Adebayo opens the bot's mailbox from home (Google often emails the cause). 3. Runs `make login` from the same address as before. 4. Sets the host back to `auth_ok`, sends one test bot to his own Meet. 5. If Google demands a phone code, the company phone receives it. 6. If the account is suspended: appeal; meanwhile the vendor fallback (Track B) or anonymous joins carry on |
| **Concurrency** | Pilot: 1 account, 1 deployment, one signed-in bot at a time (the pilot already accepts one bot at a time). Launch: N accounts for N peak parallel Meet bots, **or** keep the signed-in host for hosts who need restricted meetings and the vendor for peaks |

### 2.4 The invited-attendee path with calendar auto-join

Google admits a signed-in external account that is on the Calendar event without a knock (Trusted and Restricted meetings; [admin help](https://support.google.com/a/users/answer/12172260)). Whether this also gets past the 403 is **not verified** (Recall says it reduces blocks).

| Piece | Design |
|---|---|
| **What the user does** | Adds the bot's address to the event. Settings → Calendar shows the address with a copy button and one line of help. The invite mails the bot mailbox, which nobody reads; **no auto-accept is needed to join** |
| **Use a group address** | One Google group, `recorder@beyrictech.com`, with the bot accounts as members (Recall documents the same trick). Users always add the same address however many accounts stand behind it. **Not verified** by us |
| **What we read** | `events.list` should return attendees under the read-only scope (**not verified**; C5 already has a live scope check, add the attendee list to it). C6 stores `bot_invited` (is the address among the attendees). **Nothing is written to Google** |
| **Upcoming row** | "Bot invited" or "Add the bot to this event for the best chance of entry" |
| **Placement** | `bot_invited` meetings prefer the signed-in host; others go to a guest host. Pasted links and instant Meet meetings have no invite, so they stay guests |
| **Rejected: the bot adds itself** | Needs a calendar **write** scope. Spec Q1 chose never to write to events and the scope list is frozen for Google verification (Phase V). Still rejected |
| **ICS users** | Same help text; we have no write access to a feed anyway |

### 2.5 Privacy policy and terms

| Item | Change |
|---|---|
| **Policy** | Name the account (Beyric Note Taker, owned by Beyrichq LTD); say the bot signs in to a Google account and keeps its session on our bot machine; Google is already a sub-processor for calendar, so add the bot sign-in to its row; say the session is deleted if the feature is withdrawn. **Check:** whether the object-store volume is encrypted at rest; if not, say what protects it (disk permissions, the host) and fix before launch |
| **Terms** | Hosts must invite or admit the account; the bot may be refused by a host's settings |
| **Recording notice (C18)** | Unchanged; the participant list now shows the account's name |
| **Facts to keep true** | No policy wording about "signed-in" bots until a signed-in bot has joined a real meeting |

### 2.6 Effect on Phase C and D tasks

| Task | Effect |
|---|---|
| C6 event parsing | Add `bot_invited` from the attendee list |
| C8, C15a (Upcoming, Settings) | Show the status and the address; designer draws the two lines |
| C11 `place_bot`, D1, D2, D4 | `bot_hosts` gains `kind`, `platforms`, `auth_ok`; `place_bot` prefers a host whose `platforms` include the meeting's and whose `kind` suits `bot_invited`. `vexa_for(host)` becomes `provider_for(host)` (section 3) |
| D3 health | A signed-in host is unhealthy when `auth_ok` is false |
| D8 second host files | The signed-in host needs the full Compose files, not Lite's |
| C11b alerts | New kind `admin.bot_auth` |
| V0, V1, C18 | Wording above, only once a signed-in bot has joined for real |

---

## 3. Track B: a hosted vendor as a provider

### 3.1 Vendors (read 2026-11-03)

| | **Recall.ai** | **Meeting BaaS** | **Attendee** | Skribby |
|---|---|---|---|---|
| Exists and maintained | Yes; pricing blog updated 2026-09-14 (vendor) | Yes; SAS SPOKE, Paris; bots and SDKs public (vendor) | Yes; open source, 787 stars, 5,160 commits, hosted sign-up free (vendor: GitHub) | Third party only: pricing near USD 0.35 per hour, reported acquired; **not verified, I would not include it** |
| Price per hour | **USD 0.50** pay as you go (was 0.70); transcription +0.15; first 5 hours free; startup programme 0.25 for the first 10,000 hours; 4-core bots +0.10 (vendor: [pricing](https://www.recall.ai/pricing), [blog](https://recall.ai/blog/new-recall-ai-pricing-for-2026)) | **1 token per hour** with speaker diarisation; tokens USD 0.50 down to 0.35 by pack size; Gladia transcription +0.25 token; 8 hours free; **tokens are charged while the bot waits to be admitted** (vendor: [pricing](https://www.meetingbaas.com/pricing)) | Hosted price not on the pages I read; self-hosted is free (Django, Postgres, Redis) | |
| Returns | Mixed audio (MP3) or separate audio per participant; participant list and a speaker timeline; transcript by Recall, by the platform's captions or by a partner (vendor: [bot overview](https://docs.recall.ai/docs/bot-overview)) | Recording with speaker identification and timestamps; transcript by Gladia or your provider; can push to our S3 (vendor) | Recording, and transcript segments with speaker name, id and time (vendor) | |
| Data location, retention | Media kept in the region where captured (sub-processor list shows AWS in US, EU and JP); retention is **ours to set** per bot: timed hours, forever or none; deleting removes media including backups; 7 days of storage free (vendor: [retention](https://docs.recall.ai/docs/data-retention)) | Paris, GDPR; retention by plan: 3 days (pay as you go) to 30 days (vendor) | Ours if self-hosted | |
| Sub-processors and DPA | Public list and DPA; general authorisation, 10 days notice of new ones, an objection can end the service; SOC 2, ISO 27001, HIPAA BAA (vendor: [list](https://security.recall.ai/subprocessors), [DPA](https://www.recall.ai/data-processing-agreement)) | DPA published; the sub-processor list is on a trust centre I did not read | n/a self-hosted | |
| Meet, Zoom, Teams | All three, plus Webex, GoTo, Slack Huddles | All three | **Docs list Meet, Zoom, Teams, Webex; the GitHub page lists only Zoom as working. Conflict, not verified** | |
| Signed-in Meet bots | Yes: our Workspace, their login pool (vendor) | Blog posts cover them; **not read** | A "Signed In Bots" guide is listed; **not read** | |

**Pick:** Recall first, because it is the best documented and carries the largest pool; **Meeting BaaS second** (EU company, open bots, cheaper tokens, but charges lobby time and has day limits). Attendee is a *self-hosted* option, so it brings back the account and egress problem and adds nothing over Vexa.

### 3.2 What we keep and lose

Our pipeline downloads audio, transcribes it ourselves, and uses **live "speaker: text" turns** only to label speakers (`app/notes.py:149`, `_clean_segments` reads just `speaker` and `text`, in order).

| Need | Recall | Result |
|---|---|---|
| Audio file | Mixed MP3, fetched from a media link (expiry not read) | **Kept** |
| Speaker hints | Needs the vendor transcript (Recall's at +0.15 per hour, or platform captions). A bare speaker timeline has no text, and we transcribe without timestamps | **Kept at +0.15 per hour**, or lost if we buy none. Caption price is **not verified** |
| Raw per-speaker audio | Available | A bonus, unused |
| Lobby time billed | Per recording hour; whether time in the lobby counts is **not verified** | Stop bots at once on refusal anyway |
| Vexa-only items | `completion_reason`, `needs_help`, Vexa's `google_meet_join_refused` | Mapped to our own failure codes (below) |

### 3.3 Design: `BotProvider` behind `VexaClient`

Today `VexaClient` exposes `send_bot(url) -> int`, `get_meeting_by_id`, `stop_bot(platform, native_id)`, `download_audio(recording_id, media_id, path)`, `delete_recording(id)`, with `VexaMeeting` as the result, and the pipeline reads Vexa's strings (`status == "failed"`, `ZOOM_BLOCKED`, `awaiting_admission_rejected`).

```python
# app/providers/base.py (new)
class FailureCode(StrEnum): REFUSED; NOT_ADMITTED; BLOCKED; AUTH_MISSING; PLATFORM_BLOCKED; OTHER
@dataclass(frozen=True)
class BotRef: provider: str; bot_id: str          # bot_id is text: Vexa int or Recall UUID
@dataclass
class BotInfo: state: Literal["joining","waiting","in_call","processing","done","failed"]
               segments: list[dict]; audio_ref: str | None
               failure: FailureCode | None; failure_text: str | None
class BotProvider(Protocol):
    def send_bot(self, meeting_url: str, *, name: str) -> BotRef
    def status(self, ref: BotRef) -> BotInfo
    def stop_bot(self, ref: BotRef) -> None
    def download_audio(self, ref: BotRef, audio_ref: str, path: Path) -> None
    def delete_media(self, ref: BotRef) -> None
```

| Decision | Choice |
|---|---|
| **Structure** | `VexaProvider` wraps today's `VexaClient` (its parsing and URL checks stay). `RecallProvider` is new. `provider_for(host)` replaces `vexa_for(host)` (Phase D2) |
| **Meeting columns** | Expand then contract: add `meetings.provider_ref` and `provider_media_ref` (text) and `bot_hosts.kind`; backfill from `vexa_meeting_id`, `vexa_recording_id` (a BigInteger, unfit for a UUID); drop the old columns in a later release |
| **Which provider a meeting uses** | By host: `place_bot` picks among enabled, healthy hosts that support the platform. Default order: guest Vexa host, signed-in Vexa host (if it exists), then the vendor. **The vendor is used only as a fallback** (after a typed refusal on attempt 2, or when the circuit breaker is open) and only while `VENDOR_FALLBACK=true`, which stays false until the policy names the vendor |
| **Fake for tests** | `tests/fakes/provider.py` with scripted states and failures. One **contract test** module runs against the Fake, `FakeVexaHost` (C11) and, behind an env var, a real Recall sandbox bot, so a provider cannot drift |
| **Migration** | Step 1 pure refactor, no behaviour change, every existing test passes. Step 2 add `RecallProvider` disabled (no key). Step 3 Adebayo's own Meet through Recall. Step 4 policy, then flag on |
| **Hours cap and cost** | The hours ledger is unchanged (recorded hours). Add `meetings.provider` and `meetings.provider_cost_cents` (estimate: billed hours times the configured rate, plus transcription). An admin view sums spend per month and a `VENDOR_MAX_SPEND_USD` guard stops fallback sends when reached. User caps stay the pilot's 5 hours |
| **Retention** | Set `recording_config.retention` explicitly (timed, 24 hours) and call delete right after our own download; Recall's default for accounts made after 2025-06-12 is **indefinite** |

### 3.4 Privacy policy changes

| Change | Detail |
|---|---|
| New sub-processor row | Recall.ai: purpose (meeting bot and recording), data (audio, participant names, meeting link), region, link to its list |
| Recordings leave our server | The policy and the meeting detail say the recording was made by a vendor's bot and deleted there within 24 hours of our download; today the text says the audio stays on our server |
| DPA | Sign Recall's; note its 10-day change notice and objection clause. Transfers outside Nigeria and the EU need the usual wording; lawyer review (already on Adebayo's list) |
| Landing and sign-in copy | Unchanged until fallback is live |

---

## 4. Recommendation

### 4.1 Order of work and triggers

| Order | Step | Effort | Trigger |
|---|---|---|---|
| 0 | Spike steps E0, E2 (Mac), then v0.12.29 on the server, then the worker fail-fast and pacing task | already planned | Always first |
| 1 | **B1 `BotProvider` refactor** with the Fake | 1 session, no vendor needed | **Start regardless of results**: it is also the Phase D seam, it is test-covered, and it makes both tracks cheap |
| 2 | **B2 RecallProvider** disabled behind a flag, tested on Adebayo's own Meet | 1 session | Arm B (v0.12.29) gets in **fewer than 4 of 5** on the Mac, or the server still gets 2 or more refusals in 5 sends after the upgrade |
| 3 | **A1 measure** the full Compose stack and answer the two open questions (Zoom on an authenticated deployment, patched bot build) on the Mac | 1 session | Same trigger as B2, or a pilot host who needs restricted meetings |
| 4 | A2 to A7: signed-in host, `make login`, alerts, runbook, invite path | 3 to 4 sessions, **weeks of calendar** (account, move off Lite, our bot image until #1873 merges) | Step 3 shows signed-in bots get in where guests do not (including past the 403) |
| 5 | Policy and terms for the chosen track | 1 session | Before any real use |

Reading the Mac results: Arm A 0 or 1 of 5 **and** Arm B 0 or 1 of 5 means guests are being refused from anywhere: start B2 and A1 together. Arm B 4 or 5 of 5: upgrade, do only B1, and watch. Arm A 4 or 5 of 5 reaching the host's knock: the server's address is the problem, fix egress first (cheaper than either track).

**My recommendation: build the provider seam first, keep Recall as the fallback, and build Track A only if signed-in bots prove they pass where guests do not.** The vendor costs about USD 25 to 35 a month at pilot size, is a day or two of integration, and carries the account pool. Track A is weeks of calendar time with a permanent re-login chore and a credential to protect, and Google's 2026 bot labelling means even a signed-in account may be flagged.

### 4.2 Cost

Assumptions: pilot = 10 accounts times 5 hours = **50 hours at most** (spike); launch = about 50 users at **20 recorded hours each a month = 1,000 hours**, an assumption of mine, not a measurement. Our own OpenAI and server costs are the same on every row and are left out. Prices read 2026-11-03.

| Option | Pilot (50 h) | Launch (1,000 h) | Notes |
|---|---|---|---|
| **Recall**, audio plus its transcript | 50 x (0.50 + 0.15) = **USD 32.50** (first 5 hours free) | about **USD 650**, less with volume pricing or the startup programme | If we skip its transcript: USD 25 and 500, and lose speaker hints |
| **Meeting BaaS** | 50 tokens at 0.50 = **USD 25**; with Gladia 62.5 tokens, USD 31 (one USD 50 pack covers it) | 1,000 to 1,250 tokens at 0.35 to 0.50 = **USD 350 to 625** | Charges lobby time; retention 3 to 30 days by plan |
| **Track A**, one signed-in host | **EUR 6.80 a month** for one seat, plus the bot machine (already planned for Phase D; full-stack memory unmeasured) | About 5 seats (peak 5 parallel Meet bots) = **EUR 34** a month plus bot machines | Cheap per hour, expensive in people-time; Zoom and non-invited Meet joins still need Lite |
| Do nothing | USD 0 | USD 0 | Joins stay probabilistic (Vexa measured 27% refused from a datacenter) |

Break-even: Track A's seats are below USD 1,000 a year at any size here. Its real price is the engineering (about 4 sessions plus Adebayo's hands) and the ongoing risk. A vendor is cheaper to start and dearer at scale (about USD 650 a month at launch size).

### 4.3 Needs Adebayo

| # | Decision or hand | When |
|---|---|---|
| 1 | **Is Recall (or Meeting BaaS) acceptable as a sub-processor** for fallback recordings, given recordings leave our server? A yes lets B2 start; the vendor is off until the policy names it | Before B2 goes live |
| 2 | **If Track A is reached: Workspace seat on `beyrictech.com` (about EUR 6.80 a month), a company recovery mailbox and phone**, and who holds them | At A2, not before |
| 3 | **Create the accounts himself**: a Recall account and API key (the free 5 hours cover B2's test), and later the Workspace seat. Sessions never create or sign in | B2, A2 |
| 4 | Run the Mac experiment E2 (already asked) and paste the arm tables | First |
| 5 | Read Workspace's additional terms on automated use (not verified), and approve the bot's name and photo | Before A2 |

---

## 5. Tasks (one session each, PR only, never merged by the session)

Tasks use the pilot plan's style: tests first, fast checks locally (CI runs the rest), SOP and checklist in the same PR. IDs are new: **B** for the provider seam and vendor, **A** for the signed-in account.

### Task B1: `BotProvider` seam, no behaviour change

**Files:** Create `app/providers/__init__.py`, `base.py`, `vexa_provider.py`, `tests/fakes/provider.py`, `tests/test_provider_contract.py`; Modify `app/vexa.py` (kept, wrapped), `app/pipeline.py`, `app/services.py`, `app/worker.py` (use `provider_for`), alembic migration (`meetings.provider_ref`, `provider_media_ref`, `bot_hosts.kind`, backfill); `docs/openapi.json` if a schema changes.
**Interfaces:** the five-method `BotProvider`, `BotRef`, `BotInfo`, `FailureCode` of section 3.3; `def provider_for(host_or_none) -> BotProvider`.
- [ ] Tests first: `test_vexa_provider_maps_refused_completion_to_failure_code_refused`; `test_zoom_blocked_maps_to_platform_blocked`; `test_awaiting_admission_rejected_maps_to_not_admitted`; `test_every_pipeline_step_works_through_the_fake_provider`; `test_meeting_with_only_legacy_vexa_columns_still_polls` (migration backfill); `test_migration_round_trip`; the contract tests run against `FakeProvider` and `VexaProvider` with a scripted HTTP fake.
- [ ] F with the migration round trip, **full suite** (pipeline and services). **Review:** Opus (the refactor touches every bot path). **Needs:** nothing; folds into D1/D2 when Phase D starts. **Done:** all existing tests pass unchanged; `FakeProvider` drives a meeting from `send_bot` to notes.

### Task B2: `RecallProvider` behind a flag

**Files:** Create `app/providers/recall.py`, `tests/test_recall_provider.py`; Modify `app/config.py` (`RECALL_API_KEY`, `RECALL_REGION`, `VENDOR_FALLBACK=false`, `VENDOR_MAX_SPEND_USD`), `.env.example`, `deploy/README.md` rows; migration `meetings.provider`, `provider_cost_cents`.
**Interfaces:** `class RecallProvider(BotProvider)`; sets `recording_config.retention` timed 24 h; transcript segments converted to `{"speaker", "text"}`.
- [ ] Tests first (HTTP faked from Recall's documented shapes, which are **not** captured live): `test_send_sets_explicit_retention`; `test_blocked_status_maps_to_failure_blocked`; `test_login_not_available_maps_to_other_with_text`; `test_audio_download_never_sends_the_api_key_to_a_storage_host`; `test_delete_media_is_called_after_download`; `test_cost_estimate_is_hours_times_rate`.
- [ ] F. **Review:** Opus (credential handling, deletion). **Needs:** B1, Adebayo's Recall key for a live check. **Done:** one live bot from Adebayo's Meet to notes; captured bodies saved under `docs/fe-captures` style for the SOP, with a placeholder for the key.

### Task B3: Fallback placement and policy text

**Files:** Modify `app/placement.py` (after C11/D4), `app/join_runner.py`, privacy and terms pages; Test `tests/test_placement.py`.
- [ ] Tests first: `test_vendor_not_used_while_flag_is_false`; `test_vendor_used_after_second_refusal_on_meet`; `test_spend_guard_stops_fallback`; `test_meeting_detail_names_the_provider`.
- [ ] F. **Review:** Opus (placement locking). **Needs:** B2, C11, Adebayo's yes (4.3 item 1). **Done:** policy row, SOP, FE note.

### Task A1: Measure the full stack and settle two questions (Mac, no server)

**Files:** `docs/spike/signed-in-bot-measure.md`.
- [ ] Run Vexa Compose on the Mac with a patched bot built from #1873 (build steps recorded); measure idle and per-bot memory; test whether a Zoom bot spawns on an authenticated deployment; test a signed-in bot invited to a Meet event (after Adebayo makes the account). **Needs:** Adebayo's account, E2 results. **Done:** a table of measured numbers and the verdict on 2.2's open rows.

### Task A2 to A7 (only after A1 passes)

| Task | Deliverable |
|---|---|
| A2 | Signed-in host files (`deploy/bot-signed-in/`), bound to loopback or a private link, memory-capped; `bot_hosts.kind='vexa_auth'`, `platforms`, `auth_ok` |
| A3 | Worker and health: `auth_session_missing` handling, host paused, admin alert `admin.bot_auth` |
| A4 | Provisioning and recovery **runbook** (`docs/sop/`), `make login` steps, backup of the store |
| A5 | Keep-warm job |
| A6 | Invite path: `bot_invited` in C6, Upcoming row, Settings address and group, placement preference |
| A7 | Policy and terms rows, SOP, FE guide with live captures |

Each needs the earlier ones; A3 and A6 get a Sonnet review, A2 and A4 touch credentials and get Opus.

## Self-review

| Ask | Where answered |
|---|---|
| How do comparable products join, with sources and confidence | 1 |
| Decision list for Adebayo, Workspace versus Gmail, price with date, recovery, name | 2.1 |
| Stack changes, deployment shape, provisioning, refresh, alerts, runbook, concurrency, invite path, policy, effect on C and D | 2.2 to 2.6 |
| Vendors, price, returns, location, DPA, platforms, provider design, fake, migration, privacy, cost accounting | 3 |
| Order, triggers, cost table at pilot and launch, needs Adebayo, tasks, checklist mirror | 4 and 5; the checklist lines are in `docs/checklist/master-checklist.md` under Module 2 |

**Known gaps:** fetches were summarised by a tool; Otter's and Read's own help pages returned 403 and Skribby's pricing page was not reached; Recall's DPA and region choice, lobby billing and caption price are unconfirmed; Meeting BaaS's and Attendee's signed-in guides were not read; nothing here was run against Google.

## Sources

Vendor: [Recall pricing](https://www.recall.ai/pricing), [pricing blog](https://recall.ai/blog/new-recall-ai-pricing-for-2026), [signed-in bots](https://docs.recall.ai/docs/google-meet-login-getting-started), [Meet FAQ](https://docs.recall.ai/docs/google-meet-faq), [bot overview](https://docs.recall.ai/docs/bot-overview), [retention](https://docs.recall.ai/docs/data-retention), [sub-processors](https://security.recall.ai/subprocessors), [DPA](https://www.recall.ai/data-processing-agreement); [Meeting BaaS](https://www.meetingbaas.com/) and [pricing](https://www.meetingbaas.com/pricing); [Attendee](https://github.com/attendee-labs/attendee) and [docs](https://docs.attendee.dev/); Fireflies [FAQ](https://guide.fireflies.ai/articles/9554534786-how-fireflies-joins-and-records-your-meetings-faqs) and [Meet guide](https://guide.fireflies.ai/articles/7581948912-how-to-invite-fireflies-to-google-meet-meetings); [Fathom help](https://help.fathom.video/en/articles/449472); [Read.ai blog](https://read.ai/post/read-ai-introduces-a-botless-integration-with-google-meet). Google: [who can join without asking](https://support.google.com/a/users/answer/12172260), [terms](https://policies.google.com/terms), [Workspace pricing](https://workspace.google.com/pricing). Vexa: [PR #1873](https://github.com/Vexa-ai/vexa/pull/1873), issue #1725, and the spike's reading. Third party (not verified): UC Today, Metaview, tl;dv, Airspeed and Avoma pages on the 2026 "potential risks" label; summaries of help.otter.ai; HighlightAI and Modjo sub-processor lists.
