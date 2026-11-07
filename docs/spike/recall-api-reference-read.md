# Recall.ai endpoint-level docs: what we read (for the Vexa to Recall swap)

Date of all reads: 2026-11-07. Research only; no repo file changed.

## How to trust this report

- Labels: **read** = stated on a docs page I opened. **vendor claim** = Recall's own marketing/pricing statement (still read, but not proven by us). **not verified** = not found, or found only via search-result text, or the page contradicts itself.
- Method caveat: the fetch tool returns a small model's extract of each page, not the raw page. Where it said "condensed" or "illustrative" I say so. Field names below were returned by the tool from the page; where the tool itself flagged a typo or conflict in the docs I flag it too.
- **No full "create bot" 201 response example exists in the pages I could read.** The OpenAPI reference page for Create Bot is ~787k characters and I read it in 100k chunks (offsets 0 to 800k). It had schemas but no response example. Quickstart says the response has `id`, `join_at`, `recording_config`, `status_changes`. We should capture a real response in a sandbox before writing the adapter. (not verified)
- `google_meet.login_required` is **not documented anywhere I read.** Searched the Create Bot schema (the `GoogleMeet` schema has only `google_login_group_id`), the signed-in bots guide and the Google Meet guides. Sign-in is controlled by the login group's `login_mode` (`only_if_required` or `always`). See section 6.

---

## 1. Create bot

**Endpoint (read):** `POST /api/v1/bot/`. Base URL pattern `https://{region}.recall.ai`.

**Regions (read, /docs/regions):**

| Region | Base URL | Pay-as-you-go |
|---|---|---|
| US West | https://us-west-2.recall.ai | yes |
| US East | https://us-east-1.recall.ai | yes |
| EU | https://eu-central-1.recall.ai | yes |
| Asia | https://ap-northeast-1.recall.ai | yes |

Regions are completely independent: a different region needs a new account. API keys, webhooks, and third-party transcription keys must be added per region. A separate search result said pay-as-you-go accounts should point at us-west-2 (from the pay-as-you-go page via search snippet; that page URL returned 404 when I opened it, so this is not verified).

**Auth (read):** two pages disagree slightly.
- Quickstart and creating-bots guide: `Authorization: Token $RECALLAI_API_KEY`.
- /reference/authentication: `Authorization: $RECALLAI_API_KEY`, and says the `Token` prefix is not required. Unauthenticated gives 401.

**Request, as shown in the creating-bots guide (read, quoted):**

```
curl -X POST https://$RECALLAI_REGION.recall.ai/api/v1/bot \
    -H 'Authorization: Token $RECALLAI_API_KEY' \
    -H 'Content-Type: application/json' \
    -d '
    {
      "join_at": "SOME_TIME_MORE_THAN_10_MIN_IN_THE_FUTURE",
      "meeting_url": "$MEETING_URL",
      "bot_name": "My Bot",
    }'
```
(The guide's JSON has a trailing comma; remove it.)

**Quickstart config (read, quoted fragment):** `"recording_config": {"transcript": {"provider": {"recallai_streaming": {}}}}`, plus a top-level `metadata` object.

**Fields we would use (read, from the OpenAPI `BotRequest`/`Bot` schema):**

| Field | Type / default | Notes |
|---|---|---|
| `meeting_url` | required | Cleared "a few days after the bot joins". Google Meet pattern in /docs/meeting-urls: `meet.google.com/(?:meet/)?xxx-xxxx-xxx`. `/lookup` and `g.co/meet` forms: not mentioned (not verified). |
| `bot_name` | string, default `"Meeting Notetaker"`, max 100 | Ignored for signed-in Google Meet bots (account name is used). |
| `join_at` | ISO 8601, nullable | More than 10 min ahead = scheduled bot; less than 10 min or omitted = ad-hoc bot. Docs recommend at least 10 min ahead in production. |
| `recording_config` | object | Holds `transcript.provider`, `realtime_endpoints` (default `[]`), `start_recording_on` (`call_join`, `participant_join` default, `participant_speak`), `video_mixed_layout` (`speaker_view` default, `gallery_view`, `gallery_view_v2`, `audio_only`), `audio_mixed_mp3`, `audio_separate_mp3`, `audio_separate_raw`, `retention`. |
| `automatic_leave` | object | See section 6. |
| `metadata` | object of string values | Returned on webhooks (`bot.metadata`). Not deleted when media is deleted. |
| `google_meet` | object | Only `google_login_group_id` documented. |
| `chat` | object | `on_bot_join` (`send_to`, `message` max 4096 in schema, `pin` default false), `on_participant_join` (`message`, required `exclude_host`). |
| `automatic_video_output` | object | Bot camera image, see section 6. |
| `variant` | object | e.g. `{"zoom": "web_4_core"}`; Google Meet variant not stated. |

**Response codes (read):** 201 returns a `Bot`; 400 (body not shown for Create); 507 = ad-hoc bot pool empty ("Out of adhoc bots -- Please retry in 30 seconds"; docs say retry about every 30 s). 403 body `"code": "request_blocked"` with `detail` starting "Request was blocked due to security rules." 429 carries `Retry-After`. Create Bot rate limit: **120 requests per minute per workspace** (read, on the Create Bot reference). Leave call and delete recording: 300 per minute per workspace.

**Retrieve bot (read):** `GET /api/v1/bot/{id}/`. `status_changes` entries have `code`, `message`, `created_at`, `sub_code`. `recordings` array holds the recording, whose `media_shortcuts` holds transcript, video_mixed etc. (the quickstart names `media_shortcuts.video_mixed.data.download_url` and says the transcript download URL is in the `transcript` object in `media_shortcuts`, field `data`). I did not see the full object shape in one place; the exact JSON is not verified.

## 2. Bot lifecycle

**Status events (read, /docs/bot-status-change-events):** `bot.joining_call`, `bot.in_waiting_room`, `bot.in_call_not_recording`, `bot.recording_permission_allowed`, `bot.recording_permission_denied`, `bot.in_call_recording`, `bot.call_ended`, `bot.done`, `bot.fatal`; plus four breakout-room events (`bot.breakout_room_entered/left/opened/closed`).

- `bot.joining_call`: accepted and connecting; can repeat if a join attempt is retried. For "in the meeting" the docs recommend `in_call_not_recording` or `in_call_recording`.
- `bot.call_ended`: bot left; `data.sub_code` gives the reason; media may not be ready yet, wait for `bot.done`.
- `bot.done`: bot shut down; if it recorded, video is uploaded and downloadable.
- `bot.fatal`: shutdown due to an error, usually a join failure; can also fire mid-call, in which case media is recovered to that point and `bot.done` still follows.
- Docs warn `code` and `sub_code` may gain new values without notice (not a closed enum).

**Payload shape (read, schema; the fetch tool wrote the sample below from the schema, values made up):**

```json
{
  "event": "bot.in_call_recording",
  "data": {
    "data": { "code": "in_call_recording", "sub_code": null, "updated_at": "2026-09-01T17:30:00.000Z" },
    "bot": { "id": "example-bot-id", "metadata": {} }
  }
}
```
Nesting trap: `data.data.code` and `data.bot.id`.

**Google Meet fatal sub codes (read, /docs/sub-codes; meanings paraphrased by the fetch tool, action column from the page):**

| Sub code | Meaning | Action on page |
|---|---|---|
| `google_meet_internal_error` | Google Meet internal issue stopped the bot | none |
| `google_meet_sign_in_failed` | Bot could not sign in to Google | none |
| `google_meet_sign_in_captcha_failed` | Google captcha blocked sign-in | none |
| `google_meet_bot_blocked` | Bot was disallowed from joining | See Google Meet FAQ. Causes there: bot name that trips Google's filters (test in incognito), or host disabled "Anyone with the meeting link can ask to join" |
| `google_meet_sso_sign_in_failed` | SSO sign-in failed | none |
| `google_meet_sign_in_missing_login_credentials` | No login credentials configured | Create an authenticated Meet bot |
| `google_meet_sign_in_missing_recovery_credentials` | No recovery credentials | none |
| `google_meet_sso_sign_in_missing_login_credentials` | No SSO login credentials | none |
| `google_meet_sso_sign_in_missing_totp_secret` | SSO password lacks TOTP secret | none |
| `google_meet_video_error` | Video error prevented joining | none |
| `google_meet_meeting_room_not_ready` | Room not ready when bot tried | none |
| `google_meet_login_not_available` | Login group lacks enough Google logins | Add more logins |
| `google_meet_permission_denied_breakout` | Rejected joining an active breakout room | n/a |
| `google_meet_knocking_disabled` | Host settings prevent asking to join | Host enables "Anyone with the meeting link can ask to join" |
| `google_meet_watermark_kicked` | Watermark turned on mid-call; unsigned bot removed | Use authenticated bot set to always sign in |
| `google_meet_organisation_restricted` | Only host-org members can join | Change Meet safety settings, or use a signed-in bot |
| `sso_login_account_disabled`, `sso_login_invalid_recipient`, `sso_not_configured` | SSO account/domain problems | fix SSO setup |

Generic fatal codes that also apply: `bot_errored`, `meeting_not_found`, `meeting_not_accessible`, `meeting_not_started`, `meeting_requires_registration`, `meeting_requires_sign_in`, `meeting_link_expired`, `meeting_link_invalid`, `meeting_password_incorrect`, `meeting_locked`, `meeting_full`, `meeting_ended`, `failed_to_launch_in_time` (Recall-side slow launch; page says re-run Create Bot).

`call_ended` sub codes: `call_ended_by_host`, `call_ended_by_platform_idle`, `call_ended_by_platform_max_length`, `call_ended_by_platform_waiting_room_timeout`, `timeout_exceeded_waiting_room`, `timeout_exceeded_noone_joined`, `timeout_exceeded_everyone_left`, `timeout_exceeded_silence_detected`, `timeout_exceeded_only_bots_detected_using_participant_names`, `..._using_participant_events`, `timeout_exceeded_in_call_not_recording`, `timeout_exceeded_in_call_recording`, `timeout_exceeded_recording_permission_denied`, `bot_kicked_from_call`, `bot_kicked_from_waiting_room`, `bot_received_leave_call`, `meeting_not_started`, `breakout_room_not_open`. (The FAQ page also mentions `timeout_exceeded_only_bots_in_call`, which is not in the table I read.)

**Which are retried by Recall: not stated.** The sub-codes page gives no automatic-retry rule for any Google Meet code. Its only retry advice is for `failed_to_launch_in_time` (you re-run Create Bot). `bot.joining_call` can repeat "if a join attempt is retried", so Recall retries something internally, but the docs do not say what. The FAQ page lists "automatic join retries" as not covered. So: we must implement our own retry for `failed_to_launch_in_time`, 507, and 429. (read; retry policy not verified)

**Webhook delivery (read):** bot, recording and transcript-status events go through Svix webhooks you configure per region in the dashboard ("Webhooks" tab, you must subscribe to each event). Handler must return 2xx; timeout 15 s; retries immediately, then 5 s, 5 min, 30 min, 2 h, 5 h, 10 h, 10 h; endpoint auto-disabled after 5 days of all failures. Svix webhooks come from static IPs. Bot status can also be read by polling `GET /api/v1/bot/{id}/` (`status_changes`), but the docs describe webhooks as the delivery path and do not recommend polling (not documented as a pattern).

## 3. Transcript

**How we get it (read):** three ways.
1. After the meeting: a transcription artifact is attached to the recording; the transcript file URL is `recordings[].media_shortcuts.transcript.data.download_url` (quickstart: "located in the `transcript` object in the `media_shortcuts`"), or `GET /api/v1/transcript/{id}/` and its `data.download_url`. You GET the URL with no auth header (presigned).
2. Real-time: `recording_config.realtime_endpoints` with `"events": ["transcript.data"]` to a webhook or websocket URL you host.
3. Post-meeting job for an existing recording: `POST /api/v1/recording/{RECORDING_ID}/create_transcript/` with body e.g. `"provider": {"recallai_async": {"language_code": "auto"}}` and `"diarization": {"use_separate_streams_when_available": true}`. Poll `GET /api/v1/transcript/{TRANSCRIPT_ID}/` or subscribe to `transcript.done` / `transcript.failed` (id at `data.transcript.id`). No response body example is on that page.

**Downloaded transcript JSON (read; /docs/download-schemas; the fetch tool said it condensed the schema, field names exact):**

```json
[
  {
    "participant": {
      "id": 0,
      "name": "string or null",
      "is_host": "boolean or null",
      "platform": "string or null",
      "extra_data": "json or null",
      "email": "string or null"
    },
    "language_code": "en",
    "words": [
      {
        "text": "word text",
        "start_timestamp": { "absolute": "ISO 8601 string, null for async transcription", "relative": 0.0 },
        "end_timestamp":   { "absolute": "ISO 8601 string, null for async transcription", "relative": 0.0 }
      }
    ]
  }
]
```
Notes (read): `participant.id` is a number, not unique across meetings. `platform` values listed: `desktop`, `dial_in`, `unknown`. `relative` is seconds from start of recording. `language_code` is BCP-47. Words are word-level only: there is no sentence/segment text or segment start field, so our client builds segments by joining `words` per entry. First utterance can have speaker `null` (Transcription FAQ). Real-time `transcript.data` payload has the same participant/words/language_code fields, with `end_timestamp` possibly null (`transcript.partial_data` also exists). No literal full sample with real values is on the pages (not verified as an example).

**Diarization (read, /docs/diarization):**

| Method | Audio | Labels | Note |
|---|---|---|---|
| Perfect (default, recommended) | separate per-participant streams | participant | cannot split people who share one device |
| Hybrid (async only) | separate streams plus machine inside shared streams | participant and generic | needs your own mapping code; shared-stream speakers arrive as `{participant_id}-{anonymous_label}` with `participant.id` null |
| Speaker-timeline | single mixed stream | participant | uses the platform's active-speaker events; can be wrong with overlap |
| Machine | single mixed stream | generic (A, B, 0, 1) | provider diarization |

**How names are resolved (read):** under Perfect, each stream already belongs to a platform participant, so the name is the display name Google Meet shows for that participant. Platform names only; no email on Meet (Transcription FAQ: platforms do not expose emails, so matching to calendar attendees is fuzzy by name). If you get "Speaker A" or "0, 1, 2" instead of names, machine diarization is on (remove provider diarize flags). The Google Meet separate-audio guide confirms separate audio per participant works on Meet, limited to the 16 most recent speakers at a time.

**Custom vocabulary / key terms (read, from Create Bot schema and provider pages):**

| Provider | Field | Detail |
|---|---|---|
| Recall.ai (`recallai_streaming`, `recallai_async`) | `key_terms`, `spelling`, `filter_profanity` | `key_terms` English only (`en`, `en_au`, `en_uk`, `en_us`); value format not shown on the page; not available in `prioritize_low_latency` mode |
| Deepgram streaming | `keyterm`, `keywords` | `keyterm` only with Nova-3 (schema text). Deepgram guide page itself mentions only `model`, `language`, `mip_opt_out` |
| Deepgram async | `keyterm` (array), `keywords` | in schema |
| AssemblyAI streaming v3 | `keyterms_prompt` | list of words/phrases |
| AssemblyAI async / chunked | `keyterms_prompt` (up to 200 for Universal-2, 1000 for Universal-3-Pro), `custom_spelling` | `keyterms_prompt` seen on the async-chunked and async schemas |
| Gladia v2 async | `custom_vocabulary` | marked Beta |
| Others seen | AWS `vocabulary_names`; Speechmatics `additional_vocab`; Grok `keyterm` | |

## 4. Bring-your-own transcription

**Providers for the bot (read, Create Bot schema key names):**
- Real-time `recording_config.transcript.provider`: `recallai_streaming`, `assembly_ai_v3_streaming`, `assembly_ai_async_chunked`, `deepgram_streaming`, `gladia_v2_streaming`, `rev_streaming`, `aws_transcribe_streaming`, `speechmatics_streaming`, `elevenlabs_streaming`, `soniox_streaming`, `grok_streaming`, `meeting_captions`.
- Async (`create_transcript` / transcript artifact) `provider`: `recallai_async`, `elevenlabs_async`, `deepgram_async`, `assembly_ai_async`, `aws_transcribe_async`, `rev_async`, `speechmatics_async`, `gladia_v2_async`, `google_speech_v2_async`, `soniox_async`.
- **OpenAI: not in any list.** Gladia: yes (listed in the schema and the async provider table, though the guide index has no Gladia page). Google Cloud STT: async only. `meeting_captions` = Meet's own captions, free, no auto language detection.

**Config shapes (read, quoted):**
- Deepgram real-time: `"deepgram_streaming": {"model": "nova-3"}` with `"realtime_endpoints"` and `"events": ["transcript.data"]`. Async: `"deepgram_async": {"model": "nova-3"}`. Key added in the Recall dashboard per region; Deepgram key role must be Member, Admin or Owner ("Transcription will fail with the Default role"); you also add Deepgram Project ID.
- AssemblyAI async: `{"provider": {"assembly_ai_async": {"speech_models": ["universal-2"]}}}`. Real-time: `"assembly_ai_v3_streaming": {}` inside `recording_config.transcript.provider`, plus `realtime_endpoints`. Key added per region in the dashboard; EU hosts selectable (`api.eu.assemblyai.com`).
- Gotcha (read): AssemblyAI "real-time chunked" updates arrive with 3 to 5 minute delays (page says so).
- Another gotcha: `provider_data_download_url` (raw provider JSON) is unavailable for Recall.ai Transcription, async with Perfect Diarization, and `assembly_ai_async_chunked`.
- Recall's own provider: `"recallai_streaming": {"mode": "prioritize_low_latency", "language_code": "en"}` (English only) or `{"mode": "prioritize_accuracy", "language_code": "auto"}`; default mode is `prioritize_accuracy` with events often delayed 3 to 10 minutes. Async: `"recallai_async": {"language_code": "auto"}`; about 40 languages.

**Does Recall charge extra when we bring our own key?**
- Read on docs.recall.ai/v1.10/docs/ai-transcription (older versioned URL, opened 2026-11-07): "Recall does not charge an additional fee for using transcription through a 3rd party." The provider bills you directly. The current page docs.recall.ai/docs/ai-transcription returned only a heading when fetched, so the sentence is on the v1.10 page only. Treat as read, but on a versioned page (vendor claim, possibly stale).
- Pricing page (read) says built-in transcription "$0.15/h" and that they integrate third-party providers, but does **not** state any fee for BYO. Pricing blog (updated 2026-09-14): "pay them directly", no separate Recall charge stated.
- Provider concurrency: "Certain transcription providers require that you reach out to increase your concurrency limit" (same v1.10 page). Our scale is small, but check Deepgram/AssemblyAI concurrent-stream limits.
- Silent audio: usage docs say captured audio with no speech "can be billed even if there's no speech."

**Raw audio for our own transcription later (read):**
- Mixed audio: set `recording_config.audio_mixed_mp3 = {}`. Then `GET /api/v1/audio_mixed?recording_id=...`, read each item's `data.download_url`. Format MP3. The page text also says `audio_mixed_raw`; the fetch tool flagged this as conflicting with the examples. Not verified which key is right; test it.
- Separate per-participant audio: `recording_config.audio_separate_mp3 = {}` (recommended) or `audio_separate_raw = {}`. Then `GET /api/v1/audio_separate?recording_id=RECALL_RECORDING_ID`; each result has `id`, `recording`, `created_at`, `status`, `metadata`, `data.download_url`, `format`. That URL returns a JSON manifest of parts, each with its own `download_url`. One participant can have several parts (new part after each mute/unmute). Max 16 recent speakers at a time. Works on Zoom, Teams, Meet; not Webex. "Automatically enabled" for workspaces. No cost stated.
- Webhook names exist: `audio_mixed.done`, `audio_separate.done` (listed on /docs/recording-webhooks; the how-to pages did not mention them).
- Availability duration: governed by retention (section 5). Presigned URL lifetime: "not stated".
- Also: Transcription FAQ says you can get per-participant audio and send it to your own provider or model.

## 5. Recording access and deletion

- **Download URLs (read):** presigned; plain HTTP GET with no `Authorization` header; they expire but **how long is not stated**; request the recording again to get a fresh URL; a new URL cannot restore deleted media.
- **Retention (read, /docs/storage-and-playback):** set `recording_config.retention`: `{"type": "timed", "hours": 168}`, `{"type": "forever"}`, or `null` (Zero Data Retention). Default: accounts created before 2025-06-12 get 7-day expiry; accounts created after keep media **indefinitely** (and pay storage after the free window). So we should set retention explicitly. Changing config does not affect existing recordings.
- **Storage price (read):** first 168 hours (7 days) free, then `$0.000069` per hour_hour (pricing page: "$0.05 per hour of recording retained for 30 days").
- **Delete endpoints (read):**
  - `POST /api/v1/bot/{id}/delete_media/`: "Deletes bot media stored by Recall. This is irreversible." 200 returns the Bot; 400 "Cannot delete media for an in-progress bot."; 409 "A request to delete media for this bot is already in progress."
  - `DELETE /api/v1/recording/{id}/`: 204, no body; 300 per minute per workspace.
  - Also delete per artifact: Delete Transcript, Audio Mixed, Audio Separate, Video Mixed, Participant Events, Meeting Metadata endpoints exist (names in llms.txt, not opened).
  - Webhook `recording.deleted` is sent.
  - Scheduled bot not yet joined: `DELETE` "Delete Scheduled Bot" (`bot_destroy`, not opened).
- **Stop bot (read):** `POST /api/v1/bot/{id}/leave_call/`, "Removes the bot from the meeting. This is irreversible." 200 returns Bot; 400 no body; 300/min.
- **What is not deleted (read):** custom bot metadata and bot meeting URLs are not deleted. Deleting media does **not** delete data held by third-party transcription providers (Deepgram, AssemblyAI); check their own retention (AssemblyAI/Deepgram deletion is on us). Recall keeps no backups of deleted media.

## 6. Joining behaviour

- **Display name (read):** `bot_name`, default "Meeting Notetaker", max 100 chars. For a Meet bot that joins **signed in**, Meet uses the Google account's name and ignores `bot_name`.
- **Bad-name risk (read, Meet FAQ):** names with profanity or other trigger words can be auto-blocked by Google (`google_meet_bot_blocked`); test a name by joining from incognito.
- **Avatar (read):** unsigned bots get a coloured circle with the first letter of the name; only a signed-in bot shows a profile picture (the Google account's). Separately, a bot camera image can be set with `automatic_video_output` (`in_call_not_recording` / `in_call_recording`), each `{"kind": "jpeg", "b64_data": "..."}`; JPEG 16:9, 1280x720 recommended, max 1.3 MB; supported on Google Meet. Quoted example:
  ```json
  {"meeting_url": "MEETING_URL", "automatic_video_output": {"in_call_recording": {"kind": "jpeg", "b64_data": "/9j/4AAQSk..."}}}
  ```
  Whether the camera image shows for an unsigned Meet bot is not stated beyond the support table.
- **Anonymous default (read, /docs/google-meet):** bots join as an anonymous user and "must be manually admitted". Authenticated bots are still subject to host waiting-room settings; any signed-in participant of the host's Workspace can admit. Meet livestreams and breakout rooms: not supported. Anonymous participants may show a "may not be who they claim to be" label; signed-in bots avoid it (though Google may show another risk warning).
- **Waiting room (read, Meet FAQ):** nobody gets an admit prompt unless someone with admit rights (by default the host and host-org members) is in the call. If nobody from the host org has joined yet, no one sees it. Invited people can sometimes join before the host, which confuses users.
- **Host never admits the bot (read, /docs/automatic-leaving-behavior):** bot leaves after `automatic_leave.waiting_room_timeout`, default 1200 s (create schema says minimum 30), sub code `timeout_exceeded_waiting_room`. Google Meet and Zoom have no platform timeout (Teams has a 30-minute platform cap). Other leave settings, defaults per the page: `noone_joined_timeout` 1200 (min 30), `everyone_left_timeout` `{timeout: 2, activate_after: null}`, `in_call_not_recording_timeout` 3600, `recording_permission_denied_timeout` 30, `silence_detection` `{timeout: 3600, activate_after: 1200}`, bot detection by participant events (600 / 1200) and by participant names (600 / 120, default matches "notetaker", "recorder", "assistant"). Careful: that name list means two notetaker bots in one meeting can make each leave (read, /docs/bot-detection).
- **Skip the waiting room (read, /docs/google-meet-skip-the-waiting-room):** Open meetings (Meeting access Open) let even an unsigned bot in without knocking. Trusted or Restricted meetings need a signed-in bot whose Google account (or a Google Group containing it) is on the Calendar event guest list. "No Recall or Google Meet configuration guarantees automatic admission."
- **Chat message / recording notice (read, /docs/sending-chat-messages):** yes on Google Meet. Create-bot `chat.on_bot_join` (`send_to`, `message`, `pin`) sends as soon as the bot joins; `chat.on_participant_join` messages each joiner; Meet supports recipient `everyone` only; **500 character limit on Meet** (the schema allows 4096 generally); pinning supported on Meet and needs continuous chat disabled; late joiners do not see an unpinned message, a pinned one stays visible all meeting. Also `POST /api/v1/bot/{id}/send_chat_message/` (Zoom DM example `{"to": "...", "message": "..."}`; for Meet only `everyone`). This can carry our "recording and notes" notice. The `bot.joining_call` sequence also posts nothing automatically (not stated).
- **Signed-in Meet bots (read, /docs/google-meet-login-getting-started):** needs a dedicated Google Workspace you administer, a non-admin bot user, an organizational unit, an OpenSSL key and certificate, a **Legacy SSO profile** in Google Admin pointed at Recall's sign-in/sign-out URLs (Google's newer SAML/OIDC profiles aren't supported), a Google Login Group created via API (`login_mode`: `only_if_required` or `always`), and Google Logins added to it. Create bot then sends `"google_meet": {"google_login_group_id": "${GOOGLE_LOGIN_GROUP_ID}"}`. Capacity: one Google account supports "30 concurrent bot sign-ins" (Meet FAQ: about 50 sessions, with Recall assigning a different account at 30); start with two logins. If logins run out: `google_meet_login_not_available`. Cost of the Workspace seat: not stated. Page says the feature is auto-enabled for workspaces. **`google_meet.login_required` does not exist in the docs I read** (not verified); the equivalent is `login_mode` on the login group.
- **Google refusing anonymous bots (read):** the Meet FAQ does not say Google refuses anonymous bots outright. It lists specific causes: name filters, host control "Anyone with the meeting link can ask to join" off, org-only restriction, and watermark. It recommends signed-in bots for restricted meetings.

## 7. Limits

- **Concurrency (read, creating-and-scheduling-bots FAQ):** "concurrent bot limit" applies to **ad-hoc** bots (join_at under 10 min or omitted); the page names 30 ("30 concurrent bots limit"). "There are no limits on concurrent bots live in meetings scheduled more than 10 minutes in advance." Plan-specific difference (pay-as-you-go vs enterprise): not stated. The errors page says the workspace's concurrent ad-hoc limit shows in the dashboard under Developers > Rate Limits. Error body/code for exceeding it: not stated.
- **Ad-hoc pool (read):** a warm pool; when empty Create Bot returns 507; retry every 30 s; docs say use ad-hoc sparingly and schedule bots as the primary path. For auto-join from the calendar, set `join_at` at least 10 minutes ahead, which avoids both the 30 cap and 507.
- **Start lead time (read, FAQ):** bot does not join exactly at `join_at`; if a precise arrival matters, set `join_at` 10 to 15 s earlier. Scheduled bots are created when join_at is more than 10 min ahead; `PATCH` update only works while not dispatched ("Only non-dispatched bots can be updated").
- **Rate limits (read):** Create Bot 120/min/workspace; leave_call and delete recording 300/min; sliding window; 429 has `Retry-After`. Overall per-account limits not on a single page.
- **Meeting length cap, free-tier concurrency:** not stated.

## 8. Calendar V2 (optional)

Optional for us: bots can be created with plain `POST /api/v1/bot/` and `join_at`; Calendar V2 is a convenience (the integration guide marks only Create Calendar and Schedule Bot as "required" within the calendar flow). Calendar API is free (pricing page).

| Endpoint (name from docs) | One line |
|---|---|
| Create Calendar (`calendars_create`) | Connect a user's Google/Outlook calendar by `oauth_client_id`, `oauth_client_secret`, `oauth_refresh_token`, `platform` |
| List / Retrieve / Update / Delete Calendar | Manage connections; update used to replace a revoked refresh token |
| Get Access Token (`calendars_access_token_create`) | Access token for the calendar |
| List Calendar Events / Retrieve Calendar Event | Read synced events (use `last_updated_ts`, check `is_deleted`) |
| Schedule Bot For Calendar Event (`calendar_events_bot_create`) | Schedule a bot for an event; calling again overrides |
| Delete Bot From Calendar Event (`calendar_events_bot_destroy`) | Unschedule; deleted events unschedule automatically |

Webhooks: `calendar.update` (e.g. disconnected; payload `{"event": "calendar.update", "data": {"calendar_id": "<uuid>"}}`) and `calendar.sync_events`. Requires our own Google/Microsoft OAuth clients and storing refresh tokens with Recall, so it duplicates our existing calendar connection and is not needed to swap bots.

## 9. What changes our cost or effort estimate

1. **Public webhook URL needed.** Status/recording/transcript webhooks go to an endpoint we register in the Recall dashboard (per region); local dev docs recommend a static ngrok URL. Vexa adapter polls; Recall can be polled via `GET /bot/{id}/` but the docs do not endorse it. Plan: webhooks plus a poll fallback, and an idempotent handler (retries up to about 24 h).
2. **Signature verification is real work.** Headers `webhook-id`, `webhook-timestamp`, `webhook-signature` (legacy accounts, created before 2025-12-15: `svix-*`); HMAC-SHA256 over `id.timestamp.payload`, base64, compare constant-time; secret starts `whsec_`, one workspace secret in the dashboard API keys page; TypeScript sample in the docs, no Python sample mentioned. No replay-window guidance.
3. **No official SDKs found.** The docs show raw `curl`/`fetch` against REST; llms.txt and an MCP exist; the agent quickstarts page says nothing about client libraries (not verified, tool read only the first 100k of 131k chars). Our `httpx`-style adapter is fine.
4. **Transcript shape is word-level**; we rebuild segments and map `participant.name` to speaker. Names are Meet display names (can be "Unknown"/null, no email); first utterance can have null speaker.
5. **Two-step transcript delivery**: wait for `bot.done` then `recording.done`/`transcript.done`, then GET the download URL. Not a single call like Vexa.
6. **Cost (my arithmetic from vendor claims):** about 50 recorded hours a month at $0.50/hr = about $25/month, first 5 recording hours free once; plus $0.15/hr for Recall transcription = $7.50 (50 h), or Deepgram/AssemblyAI billed directly if we bring a key; 7 days free storage, then $0.05 per recording hour per 30 days (set timed retention or delete after we copy audio). No monthly platform fee (pricing blog, updated 2026-09-14). Billing: from `joining_call` to a terminal event, per second; waiting-room time counts as billed; bots ending in `fatal` are not billed (usage page). Larger variants (`web_4_core` +$0.10/h, GPU +$1/h) are not needed.
7. **Join-failure handling is on us**: 507, 429, `failed_to_launch_in_time`, `google_meet_bot_blocked`, `google_meet_knocking_disabled`, `google_meet_organisation_restricted`. Anonymous Meet bots can still be blocked or never admitted; the signed-in route requires a Workspace + Legacy SSO setup, which is a large one-off task (est. hours to a day; our judgement, not from the docs).
8. **Vexa-specific calls map as follows:** send bot = `POST /api/v1/bot/`; status = webhooks `bot.*` or `GET /bot/{id}/`; stop = `POST /bot/{id}/leave_call/`; transcript = `media_shortcuts.transcript.data.download_url`; audio = `audio_mixed_mp3` / `audio_separate_mp3` then `GET /audio_mixed` or `/audio_separate`; delete = `POST /bot/{id}/delete_media/` or `DELETE /recording/{id}/` (+ delete at Deepgram/AssemblyAI if we use BYO keys).
9. **Regional lock-in:** account and keys are per region; choose one (EU `eu-central-1` if data residency matters) before creating the account.

## Not stated or not verified (summary)

- Full Create Bot 201 response JSON; real captured transcript sample; exact `media_shortcuts` JSON.
- `google_meet.login_required` field (absent from docs read).
- Which sub codes Recall retries internally; whether join retries exist at all.
- Presigned download URL lifetime; audio availability beyond the retention window.
- Pay-as-you-go vs enterprise concurrency numbers; error body for exceeding the ad-hoc concurrency cap.
- BYO-key fee: only the versioned v1.10 docs page states "no additional fee"; the live pricing page is silent. Confirm with Recall support in writing.
- Cost of the Google Workspace seat for signed-in bots; Workspace admin effort.
- Whether an unsigned bot's camera image is shown; whether Google Meet `/lookup` and `g.co/meet` links are accepted.
- The pay-as-you-go plan page (docs.recall.ai/page/self-serve-plan-pay-as-you-go) returned 404; its limitations list was not read.

## Sources (all opened 2026-11-07)

- https://docs.recall.ai/llms.txt
- https://docs.recall.ai/reference/bot_create.md (offsets 0 to 800k)
- https://docs.recall.ai/reference/bot_retrieve.md (first 100k)
- https://docs.recall.ai/reference/bot_delete_media_create.md
- https://docs.recall.ai/reference/recording_destroy.md
- https://docs.recall.ai/reference/bot_leave_call_create.md
- https://docs.recall.ai/reference/authentication.md
- https://docs.recall.ai/reference/errors.md
- https://docs.recall.ai/reference/webhooks-overview.md
- https://docs.recall.ai/docs/regions.md
- https://docs.recall.ai/docs/quickstart.md
- https://docs.recall.ai/docs/creating-and-scheduling-bots.md
- https://docs.recall.ai/docs/bot-overview.md
- https://docs.recall.ai/docs/bot-status-change-events.md
- https://docs.recall.ai/docs/status-change-webhooks-setup-verification.md
- https://docs.recall.ai/docs/sub-codes.md
- https://docs.recall.ai/docs/faq.md
- https://docs.recall.ai/docs/faq-webhooks.md
- https://docs.recall.ai/docs/local-webhook-development.md
- https://docs.recall.ai/docs/authenticating-requests-from-recallai.md
- https://docs.recall.ai/docs/transcription.md
- https://docs.recall.ai/docs/transcription-faq.md
- https://docs.recall.ai/docs/diarization.md
- https://docs.recall.ai/docs/download-schemas.md
- https://docs.recall.ai/docs/real-time-event-payloads.md
- https://docs.recall.ai/docs/async-transcription.md
- https://docs.recall.ai/docs/recallai-transcription.md
- https://docs.recall.ai/docs/deepgram.md
- https://docs.recall.ai/docs/assemblyai.md
- https://docs.recall.ai/docs/ai-transcription and https://docs.recall.ai/v1.10/docs/ai-transcription (BYO fee sentence)
- https://docs.recall.ai/docs/recording-webhooks.md
- https://docs.recall.ai/docs/recordings-and-media.md
- https://docs.recall.ai/docs/storage-and-playback.md
- https://docs.recall.ai/docs/how-to-get-mixed-audio-async.md
- https://docs.recall.ai/docs/how-to-get-separate-audio-per-participant-async.md
- https://docs.recall.ai/docs/automatic-leaving-behavior.md
- https://docs.recall.ai/docs/bot-detection.md
- https://docs.recall.ai/docs/google-meet.md
- https://docs.recall.ai/docs/google-meet-faq.md
- https://docs.recall.ai/docs/google-meet-login-getting-started.md
- https://docs.recall.ai/docs/google-meet-skip-the-waiting-room.md
- https://docs.recall.ai/docs/signed-in-bots.md
- https://docs.recall.ai/docs/sending-chat-messages.md
- https://docs.recall.ai/docs/output-video-in-meetings.md
- https://docs.recall.ai/docs/meeting-urls.md
- https://docs.recall.ai/docs/calendar-v2-integration-guide.md
- https://docs.recall.ai/docs/calendar-v2-webhooks.md
- https://docs.recall.ai/docs/calculating-usage.md
- https://docs.recall.ai/docs/agent-quickstarts.md
- https://www.recall.ai/pricing
- https://www.recall.ai/blog/new-recall-ai-pricing-for-2026 (published 2026-03-10, updated 2026-09-14)
- https://www.recall.ai/product/transcription-api
- Web searches (standard mode) on BYO key fee and concurrency, used only to find the v1.10 page; the pay-as-you-go plan page link they returned was 404.
