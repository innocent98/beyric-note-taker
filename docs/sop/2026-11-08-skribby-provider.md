# SOP: `SkribbyProvider`, the hosted bot behind `BOT_PROVIDER=skribby` (plan task B2)

**Branch:** `feature/skribby-provider`. **PR:** #147 (opened 2026-11-09). **One migration:** 0038. **No endpoint, no screen, no API shape changes** (so no FE guide, and `docs/openapi.json` is unchanged). **Vexa is untouched and stays the default and the way back.** Seam this builds on: `docs/sop/2026-11-08-bot-provider-seam.md` (B1, PR #144). Why Skribby: the bake-off (`docs/spike/bakeoff-results/`, `docs/spike/meeting-bot-vendors.md`) and Adebayo's decision of 2026-11-08: Vexa is refused by Google about a quarter of the time and did not get in at all in his last trial; a signed-in Skribby bot (Google account `note@beyrictech.com`, kept as an Authenticated Account in Skribby's dashboard) was admitted in 20 and 27 s when invited to the event and in 39 to 46 s when not, with no "not signed in" warning.

**Not run against the real Skribby yet.** Everything below is built and tested against a pretend Skribby whose answers come from Skribby's own pages and the bake-off tool. The things only a live run can show are listed in "Not verified live"; `scripts/skribby_live_check.py` is the one-meeting check for Adebayo to run before flipping the setting.

## 1. DESIGN (read this first)

Sources: `docs/spike/primary-pages-2026-11-07/skribby-*` (read in full for this task: REST overview, create bot, get bot (with the `.md` twin that shows `events[]`), stop bot, bot lifecycle, webhook events, authentication, naming, billing, troubleshooting, best practices, regions, transcription models and privacy, meeting URLs, bot detection, sub-processors, privacy policy) and `tools/bakeoff/bakeoff.py` (status names, the knock inference, create and stop bodies). The page for `DELETE /bot/{id}` was **not** saved: its existence and purpose come from the REST overview ("Delete Bot Data") and the best-practices page ("call the delete endpoint to permanently remove the data from Skribby's servers"); the status codes are an assumption (see "Not verified live").

### 1.1 Every `BotProvider` operation, mapped to Skribby

| App operation | Skribby call(s) | Rules and notes |
|---|---|---|
| `send_bot(url)` | `POST /bot` (`transcription_model`, `service`, `meeting_url`, `bot_name`, `custom_metadata`, `stop_options`, and `authentication` when an account is set) | Link parsed first (`UnsupportedMeetingUrl` before anything is asked). Then **our own one-bot-per-code check** (below). Returns the bot's UUID as text. Whole call inside `SEND_DEADLINE_SECONDS` (`run_within`). 201 with a readable `id` is success; 5xx, a timeout after the send, a broken connection, or a yes with no readable id is `BotSendUncertain`; a connection never made is a plain `BotProviderError`; any other 4xx is a plain error carrying Skribby's own `message` |
| `get_meeting_by_id(id)` | `GET /bot/{id}` | `id` must be a UUID, else `BotNotFound` (a Vexa integer handed to Skribby is "no such bot", never a request). 404 is `BotNotFound`. The body is mapped to a `BotMeeting` (1.2) |
| `get_meeting(platform, code)` | the same `GET`, for the newest live bot of ours on the code | Skribby has **no call that finds a bot by meeting** (no list endpoint; "Get Scheduled Bots" only lists scheduled ones), so the bot behind a code is found in **our own `meetings` table** (`DbLedger`: rows `bot_provider = 'skribby'`, status `bot_sent`, with a bot id) plus the bots this process has sent and not yet seen end. None known is `BotNotFound` |
| `stop_bot(platform, code)` | `POST /bot/{id}/stop` for each bot of ours on the code (same lookup) | Nothing known on the code is success with no request. 404 is success. **403** (Skribby's "Bot cannot be stopped", which is what a bot already over answers) is success only if a `GET` shows the bot over; for a live bot it is an error, never a silent pass. Other failures are `BotProviderError`; the other bots are still tried first. `ValueError` for a platform or code the client would never accept, as for Vexa |
| `delete_recording(recording_id)` | `DELETE /bot/{id}` | Deletes everything Skribby holds for the bot: recording, transcript, participants, record. 404 is success. The id is a **recording handle** (1.3) turned back into the bot id through the table or this process's recent lookups; an id that resolves to nothing, or to two bots, is an error and nothing is deleted by guesswork |
| `download_audio(recording_id, media_id, path)` | `GET /bot/{id}` for `recording_url`, then `GET` that URL | https only; redirects followed by hand (max 5), **the API key goes only to Skribby's own origin**, never to a storage host or over a downgrade to http; the file is written to `path` and removed on any failure |
| (extra, not in the interface) `delete_bot_record(bot_id)` | `DELETE /bot/{id}` | For a bot that never made a recording (not admitted, blocked, stopped at the door): Skribby still keeps the meeting link, the bot's name and its timeline. The retention pass calls it where `delete_recording` has no handle. The pass checks the method on the provider's class, so Vexa and every test double are unaffected |
| "End now and write the notes" (`services.end_meeting`) | `stop_bot` as above | The bot goes `leaving`, `processing`, `transcribing`, `finished` (minutes). Mapped as `stopping`, then "the call ended, finishing the recording" (the pipeline's own wording for Vexa's wait), then `completed` with the recording. The `end_requested` marker stays on the row until then, as for Vexa |
| "Cancel" (`services.cancel_meeting`) | `stop_bot` | The meeting is failed at once, as for Vexa; the bot, if it was already in, finishes and its record is deleted at retention like any other |
| Capacity, health, user or key calls | none | Capacity is counted in our own database by `app/placement.py` (`BOT_MAX_LOCAL`, default 1; **Skribby's Free plan allows 3 at once** and over the limit `POST /bot` answers 403 "You have reached the limit of concurrent meeting bots for your plan", which surfaces as a send error) |
| Webhooks | **none, on purpose** | See 1.5 |

**One bot per meeting code, across users.** Skribby lets several bots into one meeting, so the rule is ours, in two layers. `place_bot` already refuses a second live meeting on a code (`meeting_has_note_taker`) under the placement lock. The provider adds its own check inside `send_bot`: every bot we know on the code (this process's own sends, then the live Skribby meetings on the code in our table) is looked up at Skribby; one that is not over blocks the send as `BotDuplicate`; one that is over, or that Skribby no longer has, is forgotten. This matters for the resend after Google's refusal, which sends while the meeting row still holds the refused bot's id: that bot is over, so it does not block itself. A table that cannot be read stops the send (nothing was sent) instead of guessing "free". The in-process memory also covers the window between the send and the row's commit, which the table cannot see, and is what lets `place_bot`'s compensating stop find a bot whose row never landed.

### 1.2 Status mapping (`map_bot` in `app/skribby.py`)

The pipeline reads Vexa's words, so Skribby's states are mapped into them. Statuses are the 14 on Skribby's lifecycle page (`bot_detected` is not always terminal).

| Skribby `status` (and `stop_reason`) | `BotMeeting.status` | Other fields | What the owner reads |
|---|---|---|---|
| `scheduled`, `booting` | `requested` | | "Starting the bot" |
| `joining`, joined less than 20 s ago | `joining` | | "Bot is joining the meeting" |
| `joining` for 20 s or more | `awaiting_admission`, `reached_lobby` | | "Waiting to be let in. Admit '{name}' in the meeting" |
| `breakout_waiting_room` | `awaiting_admission` | | the same |
| `waiting_to_record`, `recording` | `active` | no segments yet | "Bot is in the meeting" |
| `leaving` | `stopping` | | "The bot is leaving the meeting" |
| `processing`, `transcribing` | `completed` | `recording_status = in_progress`, no ids | "The call ended; finishing the recording" |
| `finished` | `completed` | `recording_status = completed`, recording and media id (handle) if `recording_url` is there; segments; `completion_reason` below | notes are written |
| `finished`, `meeting_ended` | | `completion_reason` empty, as a normal Vexa end | |
| `finished`, `kicked` / `manually_stopped` | | `evicted` / `stopped` | the "removed from the call" notice for `evicted` |
| `finished`, `empty_meeting_timeout`, `last_person_detected`, `silence_detection_triggered`, `recording_start_timeout`, ... | | kept as Skribby wrote it (cut at 64 characters, the column) | |
| `not_admitted`, `request_denied` | `failed` | `completion_reason = awaiting_admission_rejected`, `reached_lobby` | "The bot was not let into the meeting..." + bell and mail |
| `not_admitted`, `waiting_room_timeout` | `failed` | `completion_reason = awaiting_admission_timeout`, `reached_lobby` | the same |
| `not_admitted`, `invalid_meeting_url` / `call_already_finished` / `host_in_another_meeting` / `manually_stopped` | `failed` | `failure_reason` in plain words ("the link is not a valid meeting link", "the meeting had already ended", "the host has not started this meeting (the host is in another)", "the bot was stopped before it was let in") | "The bot reported a failure... The bot reported: ..." |
| `bot_detected`, Skribby still retrying (fewer than 4 detections, newest under 90 s old, no `finished_at`) | `requested` | | "Starting the bot" |
| `bot_detected`, final (4th detection, or 90 s with nothing newer, or `finished_at`) on Google Meet | `failed` | `failure_reason` starts **`google_meet_join_refused`** | the pipeline's refusal path: stop, one polite retry with a second bot after 4 to 5 minutes, then "Google would not let the bot ask to join..." |
| `bot_detected`, final, Zoom | `failed` | `failure_reason` starts `zoom_requires_rtms` (the words the pipeline's Zoom retry reads) | "Zoom blocked the bot..." and up to 3 bots |
| `bot_detected`, final, other | `failed` | `failure_reason` starts `bot_detected` | generic failure |
| `auth_required`, `invalid_credentials`, `invalid_api_key`, `failed` | `failed` | `failure_reason` starts with the status name and says what it means | generic failure with Skribby's word in it |
| a status this build does not know | `requested` | logged once by name | the bot stays live; the 6-hour limit applies |

The "Waiting to be let in" message is **inferred, not observed**: Skribby has no waiting-room status (`joining` covers the door, the knock and the wait). 20 s is the bake-off's threshold (`DEFAULT_KNOCK_THRESHOLD`); the time is read from the bot's `events` (Skribby's timestamp against our clock; a skew of seconds does not matter at this size). A bot that is simply slow to load can read "Waiting to be let in" for a moment; that is cosmetic.

### 1.3 Ids and columns (and migration 0038)

| What | Where it lives |
|---|---|
| Skribby's bot id (UUID) | `meetings.vexa_meeting_id`, which is `String(100)` and already holds any provider's id as text. Canonical lower-case |
| Which provider ran the meeting | **new `meetings.bot_provider`** (migration **0038**, `String(32) NOT NULL DEFAULT 'vexa'`: every old row is `vexa`, a metadata-only change on Postgres 11+, one head, up and down tested). Set by `place_bot` from the provider that sent the bot and re-set by `send_again` (a hand retry goes to the provider in use now) |
| The recording | **a handle**, an integer in `meetings.vexa_recording_id` (BigInteger) made from the bot's UUID: its first 60 bits under a marker bit, so it is between 2^60 and 2^61, fits the column, and is never a Vexa recording id. It comes back to the UUID through the table (`vexa_meeting_id LIKE 'prefix%'`, Skribby rows only; two matches is an error) or this process's recent lookups |
| Skribby's key, account, region | settings only (below), never a column |

Why a column and routing: `build_provider()` returns the provider the **setting** names, but a meeting in flight, a finished meeting waiting for its retention, and an account being deleted all belong to the provider that ran them. Without the column, flipping `BOT_PROVIDER` would ask Vexa about a Skribby UUID (`int()` fails), or delete a Skribby handle as a Vexa recording id. `provider_for(default, meeting.bot_provider)` (`app/bot_provider.py`) hands a call site that works on an existing meeting the right provider: the caller's default if it is a real provider with the same name, a double with no name always, and the meeting's own provider otherwise. Applied in `pipeline.advance_meeting` (every poll), `pipeline.delete_expired_audio`, `deletion.purge_user`, `services.cancel_meeting`, `services.end_meeting` and `services.delete_meeting`. A Skribby meeting whose key has since been removed raises `BotProviderNotConfigured` (a retryable `BotProviderError`): the meeting waits, it is not failed.

Alternatives rejected: widening `vexa_recording_id` to text (rewrites a column the Vexa path and many tests read as an int); passing the bot id through every call (an interface change touching about nine call sites and every test double); routing by the shape of the id (a heuristic with no data behind it).

### 1.4 Where the transcript and the speaker names come from

**Decision: hybrid. Our own transcription still writes the transcript; Skribby's transcript is used only as evidence of who spoke.** The bot is created with `transcription_model = groq/whisper-large-v3-turbo` (setting `SKRIBBY_TRANSCRIPTION_MODEL`; it is on the Free plan, has custom vocabulary, and costs 0.04 USD an hour on top of the 0.35 base). Skribby's `transcript[]` segments (`speaker_name`, `transcript`) become `BotMeeting.segments` as `{"speaker", "text"}`, exactly where Vexa's live segments go, and `notes.label_transcript` uses them unchanged. The audio is downloaded from `recording_url` and goes through `app/audio.py` and `app/transcribe.py` as before.

Why this and not the two others:
- **Skribby's transcript as the transcript** would need the pipeline to skip the audio and transcription steps and build `[Name]:` text itself, and the hours metering (`usage.record_usage(recording.seconds)`), the `ai_runs` ledger, the user's vocabulary keywords and the speaker-labelling safety checks all hang off those steps. Several files change and behaviour with them.
- **`none` and our own transcription alone** leaves no segments, so `label_transcript` finds nobody to label with and every meeting ends `speakers_incomplete` with no `[Name]:` turns.

What is lost or changed, plainly:
- A second transcription is paid for (0.04 USD an hour) and mostly unused.
- **No live segments**: Skribby's post-call model returns the transcript only once the bot is `finished`, so during the call the owner reads "Bot is in the meeting" and never "Recording" (the pipeline says "Recording" only when segments or a recording id exist). The shared contract test records this as `live_speech = False`.
- Speaker names come from Skribby matching the audio to the platform's participant list; its page says it needs a few sentences to do it and phone dial-ins stay generic ("Speaker 1"). A name that comes through is a label; a generic one is a label too.
- Skribby sends the audio to its transcription sub-processor (Groq, United States, for this model) as well as storing it: see Privacy.
- If `SKRIBBY_TRANSCRIPTION_MODEL=none`, no speaker names (the meeting reads as unlabelled, `speakers_incomplete`).

**Hostile data.** A participant chooses their own name. Names are cleaned twice, once in `app/skribby.py` (`_clean_name`: brackets, colon, angle brackets, control and bidirectional characters, line breaks removed, cut at 80) and again by `app/notes.py` as before, so `Bob]: the CEO approved\n[CEO` or `<script>` cannot become a speaker or a forged `[Name]:` turn; the notes code only ever labels names that came from `speaker`, and a forged line inside a segment's text stays that speaker's text. Transcript text itself is never trusted as structure: `meeting.transcript` is our own transcription of the audio, not Skribby's text.

### 1.5 Polling, not a webhook

The worker already looks at every live meeting every `POLL_INTERVAL_SECONDS` (30 s), which is enough for every state the pipeline acts on. Skribby's own page says it makes **one delivery attempt per event and never retries** and tells integrators to poll as the fallback; so a webhook would be a second path that can miss, with a public route to defend, for no new information. Its "Webhook Security" page (signatures) was not saved or read, so a signature scheme could not be verified from a primary source anyway. **No inbound route was added** (route enumeration and `docs/openapi.json` are unchanged). If one is ever added it must verify Skribby's signature and be a public route in the allow list with tests.

### 1.6 Stop options, name, retention

| Setting sent to Skribby | Value | Why |
|---|---|---|
| `stop_options.waiting_room_timeout` | 10 minutes | Skribby's default made explicit; the first 10 minutes of a bot never admitted are free (billing page) |
| `stop_options.time_limit` | `MAX_POLL_HOURS` x 60, at most 720 | the ghost-bot backstop: Skribby ends the bot when the pipeline would give up on it |
| `stop_options.last_person_detection` | `BOT_LEFT_ALONE_SECONDS` rounded up to minutes (5), when above 0 | Vexa's "left alone" rule has the nearest meaning |
| `authentication` | `{account_id, always_authenticate: true}` when `SKRIBBY_ACCOUNT_ID` is set | the bake-off's auth arm; without `always_authenticate` Skribby tries as a guest first |
| `custom_metadata` | `{app, meeting_code}` | lets an operator find a bot in the dashboard; never a passcode |

**Guest bot name:** `SKRIBBY_BOT_NAME`, default **`Beyric Meeting Support`**. Skribby's naming guide says names that sound like a recorder ("Bot", "Notetaker", "Recorder", "Assistant", "AI", "Recording", "Notes") raise Google's bot detection and a service-desk name ("Acme Meeting Support") does not; the old Vexa name "Beyric Note Taker (recording)" contains four of them. The trade is disclosure: participants cannot tell from the name that it records. A chat announcement (`initial_chat_message`) is the usual answer and is **not** turned on here (it is a create parameter that was never exercised live; a wrong value could fail every send): follow-up. A signed-in bot shows the Google account's own name and Skribby ignores `bot_name`; the "Admit '...'" line then reads `SKRIBBY_ACCOUNT_NAME` (optional; the account's display name), else the guest name.

**Retention and deletion (the promise).** What `/privacy` promises is `AUDIO_RETENTION_DAYS` (default **7**, shown on the page; the brief for this task said 14, but the code and the page say 7). The existing hourly cleanup (`pipeline.delete_expired_audio`) deletes the local folder and then the provider's copy at that age; for Skribby that is `DELETE /bot/{id}`, **which exists** and removes the recording, the transcript and the record of the bot. Skribby's own default is to delete the **recording** after 7 days by itself (`recording_available_until`); the transcript and the bot record stay at Skribby until that `DELETE` (their privacy policy says transcripts are kept while the account is active unless deleted). So vendor-side data lives at most `AUDIO_RETENTION_DAYS` after the meeting was created, plus the pass's hour, the same as Vexa's copy, **except**:

> **LOUD: Skribby keeps "debugging data (including screenshots)" for 30 days "regardless of other deletion requests"** (its privacy policy, section on retention; `skribby-privacy-policy.txt`). `DELETE /bot/{id}` cannot remove it. The privacy page must say so before the flip (sentences below). Not published anywhere I found: what those screenshots show (a meeting's participants are plausible).

Also not deleted early: the vendor copy is deleted at the app's retention, not the moment the notes exist, because "Try again" after a failed notes step can need to download the audio again.

## 2. What shipped

`SkribbyProvider` (and `SkribbyClient`, the status mapping, the ledger, the handles) in `app/skribby.py`; `BOT_PROVIDER=skribby` and the `SKRIBBY_*` settings with a startup check; migration 0038; per-meeting routing; `scripts/skribby_live_check.py`; tests. Vexa's code path is unchanged.

## 3. How, and the decisions the brief did not settle

| Topic | Decision |
|---|---|
| Errors | Skribby-named subclasses of the neutral ones (`SkribbyError`, `SkribbyDuplicateBot`, `SkribbySendUncertain`, `SkribbyNotFound`), as Vexa's are, so logs say `SkribbySendUncertain` and callers catch the neutral class |
| Timeouts and retries | Same style as the Vexa client: 30 s per phase, 5 s to connect for a send, **no automatic retries** (a retried `POST /bot` could make two bots; Skribby has no idempotency key), the whole send under `SEND_DEADLINE_SECONDS` |
| Secrets | The key is read once, held in the client, sent only in the `Authorization` header to Skribby's own origin, removed (`[redacted]`) from every error text, never chained as a cause (the original error can carry it), absent from every `repr` (`SkribbyClient`, `SkribbyProvider`, and `Settings` through `Field(repr=False)`), and never logged. No request body or link is logged (a Zoom link carries its passcode). Skribby's own words in an error (`message`) are cut at 200 characters and scrubbed |
| Startup check | `BOT_PROVIDER=skribby` without `SKRIBBY_API_KEY` stops web, worker and scheduler with `BOT_PROVIDER is 'skribby' but SKRIBBY_API_KEY is not set`; `SKRIBBY_REGION` must be `eu` or `jp`; `SKRIBBY_ACCOUNT_ID` must be a UUID (checked whenever it is set, so a typo is caught before the flip). A blank key line counts as not set |
| The factory | `build_provider("skribby")` returns one provider per settings object (it keeps what this process has sent). A test or script that clears the settings cache gets a fresh one |
| Unconfirmed send | If `POST /bot` is answered 5xx or not at all, the bot MAY exist and Skribby cannot be asked ("no bot is known on that code"), so the worker cannot adopt it as it can with Vexa: after the 60 s grace it stops what it knows (nothing) and fails the meeting "could not be confirmed". See Known gaps |
| Shared tests changed | The contract tests now run for Skribby too. Three honest differences are written into the shared file: the bot id may be a string, Google's refusal is "failed with a reason that starts `google_meet_join_refused`" (the documented rule) rather than one exact sentence, and Skribby has no segments while the bot is `active` (`live_speech`). `test_an_unknown_provider...` used `skribby` as its example of an unknown value; it uses `nope` now |
| One fix on the way | `services._send_refused_meeting_again` passed `int(vexa_meeting_id)` to `commit_or_stop`, which would raise on a UUID: it passes the text now (`_is_recorded` already compared as text) |

## 4. What's involved

| Area | Files |
|---|---|
| New | `app/skribby.py` (client, mapping, ledger, provider, handles), `alembic/versions/0038_meeting_bot_provider.py`, `scripts/skribby_live_check.py`, `tests/test_skribby_provider.py`, `tests/test_skribby_e2e.py`, `tests/test_skribby_live_check.py` |
| Changed | `app/bot_provider.py` (`BotId`, `BotProviderNotConfigured`, the `skribby` factory, `provider_name`, `provider_for`), `app/config.py` (settings, validators, `shown_bot_name`), `app/models.py` (`Meeting.bot_provider`), `app/pipeline.py` (routing per meeting, `send_again` sets the provider, cleanup of a bot with no recording), `app/placement.py` (stores the provider; id types), `app/deletion.py` and `app/services.py` (routing; the `int()` fix; the owner's "Admit" name), `app/vexa.py` (`name = "vexa"`) |
| Config and docs | `.env.example`, `deploy/README.md`, `docs/checklist/master-checklist.md` (B2), the seam SOP (a pointer), this SOP |
| Tests changed | `tests/test_provider_contract.py` (a `SkribbyWorld`; the three differences above), `tests/test_migrations.py` (0038) |

## 5. Verification

See the PR body for the exact run. In short: ruff check and format clean (ruff 0.17.3 pinned); `pytest --collect-only` over all of `tests/`; the bot, pipeline, placement, join, deletion, end, delete, send-now, admin, accounts, API, web, migration, OpenAPI and route-enumeration suites pass; CI runs the rest. New: the contract scenarios for Skribby (the same scenarios the fake and Vexa pass: 27 more cases); every status and stop reason in the table above; the knock inference; the retried block; Skribby's real error bodies (400 meeting_url, 401, the two 403 messages, 429, 5xx, timeouts); secrets absent from errors, logs (`caplog` at DEBUG through a send, lookups, stop, delete and a whole meeting), reprs and settings; hostile names and forged turns; the ledger over the real table; handles; routing in both directions; a meeting from send to notes with speaker names, then the retention delete; a decline; a refusal with the second bot; an unconfirmed send; cancel, delete and a hand retry. **Mutation checks:** turning off name cleaning, the duplicate check, the live-403 rule, the key-only-to-Skribby rule, the scrub, the knock inference and the processing mapping each fails tests.

### Not verified live (what the live check must show)
- `recording_url`: whether it needs the API key, what format the audio is (ffmpeg reads whatever it is; the file is named `source.webm` regardless), how long it stays valid.
- `DELETE /bot/{id}`: its status codes (404 for gone is assumed) and that it really removes the transcript and recording.
- Stop: that **403** really is what a bot that is already over answers (assumed from the stop page's "Bot cannot be stopped").
- That the Free plan accepts `groq/whisper-large-v3-turbo` and returns `speaker_name` filled in for Meet.
- The timestamp format in `events[].created_at` (parsed as ISO 8601, with or without `Z`; a value that does not parse only disables the knock estimate).
- Whether a signed-in bot that is refused (`invalid_credentials`, seen once at about 30 s on 2026-11-08) leaves a clean failure.
- The 4-detection and 90-second rules for a blocked bot.

## 6. Operate / roll back

**Server steps before merge** (also in the PR): the migration (`alembic upgrade head` runs on deploy; 0038 adds one column with a default, no downtime); no `.env` change is needed while `BOT_PROVIDER` stays `vexa`.

**To switch on** (after the live check, in a window with no meetings: a restart drops the worker's in-memory counters, which only gives each live meeting a fresh grace):
1. Server `.env`: `BOT_PROVIDER=skribby`, `SKRIBBY_API_KEY=...` (the key stays in the server `.env` only), and for the signed-in bot `SKRIBBY_ACCOUNT_ID=<the Authenticated Account's id>` and `SKRIBBY_ACCOUNT_NAME=<the account's display name>`. Leave `SKRIBBY_ACCOUNT_ID` out for a guest bot. `BOT_MAX_LOCAL` is our own counter: raise it to at most 3 on Free (Skribby's limit), 25 on Pay As You Go.
2. Restart web, worker and scheduler. A wrong or missing setting stops them at startup with a message naming it.
3. Send one real meeting; check it reaches notes with speaker names; check Skribby's dashboard shows the bot and, after the retention, no longer holds it.

**Roll back:** set `BOT_PROVIDER=vexa` and restart. New bots go to Vexa; meetings Skribby already holds keep being worked on through Skribby **if `SKRIBBY_API_KEY` is still set** (leave it set until they are over and their retention has run), else they wait (not fail) until it is. No data migration is needed; the column stays. To revert the code: revert the PR; `alembic downgrade 0037` drops the column (any Skribby meeting then loses the memory of its provider: drain them first).

**The Skribby side:** the bot account's login (a Google security check can break the saved session: `invalid_credentials`; sign in to the account outside Skribby, clear the check, retry), the Free plan's 5 bot hours a month and 3 bots at once (a 403 on send, worded by Skribby).

## 7. Privacy and policy text to add before the flip

(For the privacy page and terms; the lead and Adebayo decide the wording. These are the facts they must carry.)
- "Meeting recordings and transcripts are processed by Skribby (a meeting-bot service owned by Alianza), which joins your meeting as the note taker, records it, and transcribes it for speaker names. Skribby is a sub-processor; its sub-processors are listed at skribby.io/sub-processors, and the transcription model we use (Groq) processes audio in the United States."
- "We delete the recording and transcript at Skribby when your audio is deleted, {AUDIO_RETENTION_DAYS} days after the meeting. Skribby also deletes the recording by itself after 7 days."
- "Skribby keeps technical debugging data, which can include screenshots, for 30 days, and does not delete it on request."
- Bot data region: Europe by default (`SKRIBBY_REGION=eu`).
- The sub-processor list gains Skribby (and, behind it, Groq).

## 8. Known gaps and follow-ups

| Gap | What it needs |
|---|---|
| **An uncertain send can leave a bot we cannot find.** Skribby has no way to ask "is there a bot in this meeting", so after a 5xx or a lost answer the bot may be in the meeting recording with no row for it. Its own limits end it (waiting-room 10 min, `time_limit` = `MAX_POLL_HOURS`), and the meeting is failed with "could not be confirmed". | Rare (the create call answers at once), but it is a recording we did not intend. Options: add a "find by metadata" call if Skribby ever adds one, or stop the bot by hand from the dashboard (`custom_metadata.meeting_code` is set for this). The send error is logged by type only, so an operator seeing `SkribbySendUncertain` should look at the dashboard |
| The "invited event" path (add `note@beyrictech.com` to the user's event so the bot is admitted without knocking) | The lead is designing it separately. It needs a way for the app to say "this bot attends that event" and to know it accepted; nothing in this PR changes a screen or the Upcoming/Settings copy |
| Disclosure that the note taker records | A chat message on joining (`initial_chat_message`) and the recording-notice work (C18), now more important because the guest name no longer says "(recording)" |
| A plan limit (403 on send) is a plain send error | Map it to the owner-facing "busy" message |
| Skribby's own transcript is paid for and mostly unused | Once the live check shows its quality, use it as the transcript and drop our transcription (a pipeline change, with the hours metering and `ai_runs` moved with it) |
| `meetings.vexa_meeting_id`, `vexa_recording_id` and the `vexa` parameter names | Neutral names (`provider_ref`) and a neutral status enum, the B1 follow-up, now with two providers in play |
| Zoom and Teams through Skribby | Zoom is mapped (the `zoom_requires_rtms` words); Zoom with a passcode and Zoom's own authentication (ZAK tokens) were not exercised. Teams stays off (`TEAMS_JOIN_ENABLED`) |
| Several bot accounts | Skribby does not pool accounts; one `SKRIBBY_ACCOUNT_ID` is a single point of failure and bots using one account in one meeting can merge |
