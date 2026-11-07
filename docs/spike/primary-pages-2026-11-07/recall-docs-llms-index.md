SOURCE: https://docs.recall.ai/llms.txt
Fetched 2026-11-07 with curl -sL -A 'Mozilla/5.0'

# Recall.ai Documentation

> Recall.ai is the API for meeting bots, desktop recording, and mobile recording.

## Guides: 🌱 Basics
- [Getting Started with Recall.ai](https://docs.recall.ai/docs/getting-started.md): A directory of where to find information by product (e.g. Meeting Bots, Desktop Recording SDK, Mobile Recording SDK)
- [Meeting Platforms](https://docs.recall.ai/docs/meeting-platforms.md): Meeting platforms are supported for products (e.g. Meeting Bots, Desktop Recording SDK) along with setup instructions (if applicable)
- [Regions and Base URLs](https://docs.recall.ai/docs/regions.md): A list of data-isolated regions supported by Recall, their hostnames (i.e., base URLs) for making requests, and where to find API keys.
- [Local Webhook Development Setup](https://docs.recall.ai/docs/local-webhook-development.md): Required for local development: Set up a static ngrok URL to receive webhooks/websockets to your local development environment.
- [Real-Time Endpoints](https://docs.recall.ai/docs/real-time-endpoints.md): Overview for how real-time endpoints deliver live in-meeting events and data to your application over webhooks and websockets.
  - [Real-Time Webhook Endpoints](https://docs.recall.ai/docs/real-time-webhook-endpoints.md): Configure real-time webhooks to receive participant events and transcript data during a call, verify real-time endpoint webhook requests, and understand real-time endpoint webhook retry behavior.
  - [Real-Time Websocket Endpoints](https://docs.recall.ai/docs/real-time-websocket-endpoints.md): Configure real-time websocket to receive participant events and transcript data during a call, verify real-time websocket connections, and understand real-time endpoint websocket retry behavior.
  - [Real-Time Event Payloads](https://docs.recall.ai/docs/real-time-event-payloads.md): Learn the real-time endpoint event payload schemas your application receives during a call (if configured) and behaviors
- [Explorer Dashboard](https://docs.recall.ai/docs/explorer-dashboard.md): Inspect API artifacts (e.g., bots, calendars) in a UI and see related artifact logs to debug and triage issues faster.
- [Custom Metadata](https://docs.recall.ai/docs/custom-metadata.md): Add custom metadata to API artifacts (e.g., bots, recordings, DSDK uploads) so you can tag resources, map them to your internal systems, and search the API via custom metadata.
- [API v1.11: Release Notes](https://docs.recall.ai/docs/api-v111-release-notes.md): Only applicable if moving from the v1.10 -> v1.11 API.
- [Managing Users & Environments](https://docs.recall.ai/docs/environments.md): How to invite, update, or remove users on your team, manage user roles, set up separate environments via workspaces, and manage your workspace data.
- [Migrating workspaces between regions](https://docs.recall.ai/docs/migrating-between-regions.md): Move your Recall integration to off of an existing region and into a new region without breaking API, webhook, or calendar workflows.
- [Tracking and Calculating Usage](https://docs.recall.ai/docs/calculating-usage.md): Track Recall usage across bots, transcription, and DSDK recordings for billing and customer reporting.
- [Manage your billing](https://docs.recall.ai/docs/billing-faq.md): Manage billing details and understand low-balance behavior so recording isn’t interrupted.
- [Verifying webhooks, websockets and callback requests from Recall.ai](https://docs.recall.ai/docs/authenticating-requests-from-recallai.md): Verify webhook, websocket, and callback requests from Recall using HMAC verfication signatures so you only accept verified events.
- [How to Get Support](https://docs.recall.ai/docs/how-to-get-support.md): How to use Slack, email, or dashboard chat to get help from Recall.

## Guides: 🎬 Storage and Playback
- [Recordings and Media](https://docs.recall.ai/docs/recordings-and-media.md): Understand recordings and media types, and learn how to access audio, video, transcripts, participant events, and meeting metadata.
  - [Recording Webhooks](https://docs.recall.ai/docs/recording-webhooks.md): Track recording, media, and transcript webhook events so you know when media is available.
  - [How to download recording media](https://docs.recall.ai/docs/download-schemas.md): Get a recording download URL from a bot or recording, then read the JSON schemas for transcripts, participants, speaker timelines, and separate audio or video.
- [Storage and Data Retention](https://docs.recall.ai/docs/storage-and-playback.md): Configure how long Recall stores recording media, understand storage charges, and delete recordings when your application no longer needs them.
- [Video Playback](https://docs.recall.ai/docs/video-playback.md): Use Recall video URLs to play recordings directly without storing media yourself.

## Guides: 🤖 Meeting Bots
- [Bot Overview](https://docs.recall.ai/docs/bot-overview.md): Learn what a Recall bot is, how you can customize it, and what meeting data it exposes so you can build the right meeting integration.
- [Quickstart: Record a meeting using a bot](https://docs.recall.ai/docs/quickstart.md): Minimal, development implementation on how to send a bot to a meeting and retrieve the recording.
- [Creating and scheduling bots](https://docs.recall.ai/docs/creating-and-scheduling-bots.md): Preparing for production: Learn how to schedule bots and ensure your bots join on time; avoid the 507 error
- [Debugging Bots](https://docs.recall.ai/docs/debugging-bots.md): Learn how to find information about why your bot didn't behave as expected.
- [Video Layouts](https://docs.recall.ai/docs/video-layouts.md): Configure the video layout for your bot's final recording.
- [Speaker Timelines](https://docs.recall.ai/docs/speaker-timeline.md): Use speaker timelines (a sequence of speaker change events from the meeting platform) to track when participants are speaking, noting platform differences that may affect how these timelines are…
- [Bot Automatic Leave Behavior](https://docs.recall.ai/docs/automatic-leaving-behavior.md): Control when your bot should automatically leave calls.
- [Detecting Other Bots & Automatically Leaving](https://docs.recall.ai/docs/bot-detection.md): Configure your bots to automatically leave when only bots are left on a meeting.
- [Bot Webhooks](https://docs.recall.ai/docs/bot-status-change-events.md): See which bot status webhooks Recall sends so your application can react when bots join, record, finish, or fail.
  - [Status Change Webhooks: Setup & Verification](https://docs.recall.ai/docs/status-change-webhooks-setup-verification.md): Start receiving and verifying bot status change webhook events.
  - [Bot Sub Codes](https://docs.recall.ai/docs/sub-codes.md)
  - [Testing Webhooks Locally](https://docs.recall.ai/docs/testing-webhooks-locally.md): Test receiving and verifying Bot status event webhooks locally
  - [FAQs: Bot Webhooks](https://docs.recall.ai/docs/faq-webhooks.md): Frequently asked questions about Recall.ai status change webhooks
- [Bots FAQ](https://docs.recall.ai/docs/faq.md): Resolve common bot, recording, and API questions faster with this FAQ.
- [Identify Meeting Participants Uniquely](https://docs.recall.ai/docs/identify-meeting-participants-uniquely.md): Identify participants using stable platform-specific IDs.
- [Meeting URLs](https://docs.recall.ai/docs/meeting-urls.md): Use platform-specific regex patterns to validate meeting URLs before creating a bot so you can catch invalid links early.

## Guides: 🎥 Bot Features
- [Bot Recording](https://docs.recall.ai/docs/receive-a-recording.md): Configure a bot to create an MP4 recording and retrieve its download URL when processing completes.
  - [Recording control for meeting bots](https://docs.recall.ai/docs/recording-control.md): Learn how to start, stop, pause, and resume meeting bot recordings, including how each action affects media, transcription, and participant events.
  - [Media Shortcuts](https://docs.recall.ai/docs/media-shortcuts.md): Use `media_shortcuts` to access the most common recording media in one response so you can avoid extra artifact retrieval requests.
  - [How to get Mixed Video + Audio MP4 (Async)](https://docs.recall.ai/docs/how-to-get-mixed-video-mp4.md): Get a mixed video+audio MP4 recording so you can access full meeting playback after the call ends.
  - [How to get Mixed Video + Audio Stream (Realtime)](https://docs.recall.ai/docs/stream-real-time-video-rtmp.md): Configure a bot to stream mixed video+audio over RTMP so you can broadcast or process meetings live.
  - [How to get Mixed Audio MP3 (Async)](https://docs.recall.ai/docs/how-to-get-mixed-audio-async.md): Configure a bot to generate a mixed MP3 recording and retrieve it after the meeting ends.
  - [How to get Mixed Audio Stream (real-time)](https://docs.recall.ai/docs/how-to-get-mixed-audio-real-time.md): Configure a bot and websocket endpoint to receive real-time mixed raw audio so your application can process meeting audio live.
  - [How to get Separate Videos per Participant (Async)](https://docs.recall.ai/docs/how-to-get-separate-videos-per-participant-async.md): Generate separate post-call video files for each participant so you can analyze each person individually.
  - [How to get Separate Videos per Participant (Realtime)](https://docs.recall.ai/docs/how-to-get-separate-videos-per-participant-realtime.md): Configure a bot and websocket endpoint to receive separate real-time video for each participant so you can process each stream independently.
  - [How to get Separate Audio per Participant (Async)](https://docs.recall.ai/docs/how-to-get-separate-audio-per-participant-async.md): Generate separate post-call audio files for each participant.
  - [How to get Separate Audio per Participant (Realtime)](https://docs.recall.ai/docs/how-to-get-separate-audio-per-participant-realtime.md): Configure a bot and websocket endpoint to receive separate real-time audio for each participant so you can process each speaker individually during a call.
- [Send AI Agents to Meetings (Output Media)](https://docs.recall.ai/docs/stream-media.md): Use Recall.ai's Output Media API to create interactive agents that can listen and respond in real time.
- [Outputting Images/Video From a Bot's Camera/Screenshare](https://docs.recall.ai/docs/output-video-in-meetings.md): Output an image or video for your bot to customize the experience.
- [Output Speech/Audio from the Bot](https://docs.recall.ai/docs/output-audio-in-meetings.md): Make a bot speak or play audio into the meeting.
- Chat Messages
  - [Sending Chat Messages](https://docs.recall.ai/docs/sending-chat-messages.md): Send bot chat messages during meetings, understand platform limitations, and pin important notices like recording consent.
  - [Receiving Chat Messages](https://docs.recall.ai/docs/receiving-chat-messages.md): Receive meeting chat messages in real time or after the call so your app can trigger workflows and access chat history.
- Meeting Participants & Metadata
  - [Meeting Metadata](https://docs.recall.ai/docs/meeting-metadata.md): Configure and retrieve meeting metadata like titles.
  - [Meeting Participants & Events](https://docs.recall.ai/docs/meeting-participants-events.md): Access participant lists and participant events so you can track who joined, left, and changed states (e.g. camera on/off, mic on/off, etc.) in a meeting.
  - [Meeting Participant Emails](https://docs.recall.ai/docs/meeting-participant-emails.md): Get participant email addresses from calendar-linked bots so you can identify who’s in a meeting and troubleshooting tips.
- [Signed-In Bots](https://docs.recall.ai/docs/signed-in-bots.md): "Signed-in" bots allow Recall bots to join as verified users instead of guests.

## Guides: 📝 Transcription
- [Transcription overview](https://docs.recall.ai/docs/transcription.md): Compare real-time and async transcription, explore providers and features, and start with quickstarts for live transcript events or fetching a completed transcript.
- Transcription implementation guides
  - [Post-meeting transcription](https://docs.recall.ai/docs/async-transcription.md): Create transcripts after a meeting ends, handle completion or failure, and fetch the final result.
  - [Real-time transcription - meeting bots](https://docs.recall.ai/docs/bot-real-time-transcription.md): Use real-time transcription to get live utterances, partial results, and speaker data during a call from the meeting bot.
- Supported speech-to-text providers
  - [Recall.ai Transcription](https://docs.recall.ai/docs/recallai-transcription.md): The recommended, fastest, and easiest-to-use speech-to-text transcription provider. Use Recall.ai’s built-in transcription to generate real-time or post-meeting transcripts.
  - [ElevenLabs](https://docs.recall.ai/docs/elevenlabs.md): Set up and create transcripts using ElevenLabs as a speech-to-text provider for post-meeting and real-time transcription.
  - [Deepgram](https://docs.recall.ai/docs/deepgram.md): Set up and create transcripts using Deepgram as a speech-to-text provider for post-meeting and real-time transcription.
  - [AssemblyAI](https://docs.recall.ai/docs/assemblyai.md): Set up and create transcripts using AssemblyAI as a speech-to-text provider for post-meeting and real-time transcription.
  - [AWS Transcribe](https://docs.recall.ai/docs/aws-transcribe.md): Set up and create transcripts using AWS Transcribe as a speech-to-text provider for post-meeting and real-time transcription.
  - [Rev](https://docs.recall.ai/docs/rev.md): Set up and create transcripts using Rev as a speech-to-text provider for post-meeting and real-time transcription.
  - [Speechmatics](https://docs.recall.ai/docs/speechmatics.md): Set up and create transcripts using Speechmatics as a speech-to-text provider for post-meeting and real-time transcription.
  - [Grok (xAI)](https://docs.recall.ai/docs/grok-xai.md): Set up Grok as a speech-to-text provider for real-time meeting bot transcription.
  - [Soniox](https://docs.recall.ai/docs/soniox.md): Set up Soniox for post-meeting transcription and real-time transcription with meeting bots.
  - [Meeting Caption Transcription](https://docs.recall.ai/docs/meeting-caption-transcription.md): Set up meeting captions as a speech-to-text provider for post-meeting and real-time transcription.
- Advanced transcription topics
  - [Diarization](https://docs.recall.ai/docs/diarization.md): Choose the right diarization mode so your transcripts use accurate speaker labels and names across remote, shared-mic, and hybrid meetings.
  - [Multilingual transcription](https://docs.recall.ai/docs/multilingual-transcription.md): Configure speech-to-text providers for transcription in multiple languages, including language detection and code-switching support.
  - [Accessing text-to-speech provider-specific fields](https://docs.recall.ai/docs/accessing-text-to-speech-provider-specific-fields.md): Access raw provider data when you need transcript fields that are not included in Recall’s normalized transcript format.
- [Transcription FAQ](https://docs.recall.ai/docs/transcription-faq.md): Find answers to common transcription questions so you can troubleshoot speaker mapping, transcript formatting, limits, latency, and billing faster.

## Guides: 🖥️ Desktop Recording SDK
- [Desktop Recording SDK Overview](https://docs.recall.ai/docs/desktop-sdk.md): Integrate the Desktop Recording SDK to detect and record meetings and retrieve recordings and transcripts in your Electron app without adding a standard recording bot tile to the call.
- [Desktop Recording SDK Supported Platforms](https://docs.recall.ai/docs/dsdk-supported-platforms.md): Check which OSs, meeting providers, languages, and meeting metadata the Desktop Recording SDK supports so you can verify compatibility before integrating.
- [Desktop Recording SDK Raw Media](https://docs.recall.ai/docs/desktop-recording-sdk-raw-media.md): Capture higher-quality meeting media with lower local resource usage.
- [Desktop Recording SDK Real-time Transcription](https://docs.recall.ai/docs/dsdk-realtime-transcription.md): Set up Desktop SDK real-time transcription so your app can receive live speaker-attributed transcript events during a meeting.
- [Desktop Recording SDK Event Types](https://docs.recall.ai/docs/desktop-recording-sdk-event-types.md): Learn which Desktop Recording SDK events fire and what each listener returns so your app can handle recording state changes and errors.
- [Desktop Recording SDK Safe Shutdown and Recovery](https://docs.recall.ai/docs/dsdk-safe-shutdown.md): Learn how to shut down the Desktop Recording SDK safely, allow pending uploads to finish, and handle network interruptions for local and Raw Media recordings.
- [Desktop Recording SDK Methods](https://docs.recall.ai/docs/desktop-recording-sdk-methods.md): Use the Desktop Recording SDK methods to initialize the SDK, manage permissions, and control recording sessions from your app.
- [Desktop Recording SDK Webhooks](https://docs.recall.ai/docs/desktop-recording-sdk-webhooks.md): See which Desktop SDK recording and upload webhooks fire so your application can track recording progress and upload completion.
- [Additional Configurations](https://docs.recall.ai/docs/specialized-configurations.md)
  - [Desktop Recording SDK Audio-Only Recording](https://docs.recall.ai/docs/audio-only.md): Configure Desktop SDK audio-only recording to capture and diarize meetings with smaller files and lower device usage.
  - [Desktop Recording SDK  Adhoc / In-Person Meetings](https://docs.recall.ai/docs/adhoc-meetings-in-person-meetings.md): Use the Desktop SDK to record in-person or meetings for unsupported meeting platforms from your mic and speakers, and understand the speaker-label limits and diarization options.
- [MacOS Permissions](https://docs.recall.ai/docs/macos-permissions.md): Request the right macOS permissions and Info.plist settings so your Desktop SDK app can record meetings successfully without issues.
- [Publishing Your App](https://docs.recall.ai/docs/publishing-your-app.md): Publish your Electron app with the required packaging and security setup so the Desktop SDK runs correctly.
- [Integrating with non-Electron apps](https://docs.recall.ai/docs/integrating-with-non-electron-apps.md): Use a small Node.js sidecar process to use the DSDK with Tauri, Swift, and other native apps.
- [Desktop Recording SDK FAQ](https://docs.recall.ai/docs/desktop-recording-sdk-faq.md): Find answers to common Desktop Recording SDK recording, detection, upload, and platform questions so you can troubleshoot faster.
  - [Desktop Recording SDK vs Native and Cross-Platform APIs](https://docs.recall.ai/docs/desktop-recording-sdk-vs-native-and-cross-platform-apis.md): Compares Recall.ai’s Desktop Recording SDK with native and cross-platform desktop recording APIs.
- [Changelog](https://docs.recall.ai/docs/dsdk-changelog.md): Changelog for the Desktop Recording SDK

## Guides: 📅 Calendar Integration
- [Calendar integration overview](https://docs.recall.ai/docs/calendar-integration.md): Integrate bots with your users' Google Calendar or Microsoft Outlook events.
- Calendar V2
  - [Calendar V2 Integration Guide](https://docs.recall.ai/docs/calendar-v2-integration-guide.md): Integrate with your users' calendars to automate bot scheduling using calendar v2
  - [Calendar V2 Webhooks](https://docs.recall.ai/docs/calendar-v2-webhooks.md): Use Calendar V2 webhooks to track calendar and calendar event changes so you can keep bot scheduling in sync through calendar v2
  - [Scheduling Guide](https://docs.recall.ai/docs/scheduling-guide.md): Use Calendar V2 event updates to determine recording logic, schedule bots, and deduplicate shared meetings.
  - [Google Calendar](https://docs.recall.ai/docs/calendar-v2-google-calendar.md): Calendar V2: Configure Google Calendar OAuth for Recall so users can sync calendar meetings and automate bot scheduling
  - [Microsoft Outlook](https://docs.recall.ai/docs/calendar-v2-microsoft-outlook.md): Calendar V2: Configure Microsoft Outlook OAuth for Recall so users can sync calendar meetings and automate bot scheduling
  - [Calendar V2 FAQ](https://docs.recall.ai/docs/calendar-v2-faq.md): Find answers to common Calendar V2 sync, OAuth, webhook, and disconnection issues so you can troubleshoot calendar integrations faster using calendar v2
  - [Calendar Event Platform Data](https://docs.recall.ai/docs/calendar-event-platform-data.md): Review example Google Calendar and Outlook raw event payloads so you can parse provider-specific calendar data correctly for calendar v2.
  - Calendar V2 Troubleshooting Guides
    - [Troubleshooting: Microsoft Outlook Calendar Connection is Missing Events or Has Wrong Email](https://docs.recall.ai/docs/troubleshooting-microsoft-outlook-calendar-connection-is-missing-events-or-has-wrong-email.md): Use this guide if a user's connected Microsoft Outlook calendar shows no events or shows an Outlook service email instead of their expected email.
- Calendar V1
  - [Calendar V1 Integration Guide](https://docs.recall.ai/docs/calendar-v1-integration-guide.md): Set up Calendar V1 so users can connect calendars and auto-record synced meetings.
  - [Recording Preferences & Bot Deduplication](https://docs.recall.ai/docs/calendar-v1-recording-preferences.md): Configure which synced calendar meetings are recorded and prevent duplicate bots from joining the same meeting with Calendar V1.
  - [Google Calendar](https://docs.recall.ai/docs/calendar-v1-google-calendar.md): Calendar V1: Configure Google Calendar OAuth for Recall to let users sync meetings and manage auto-recording preferences.
  - [Microsoft Outlook](https://docs.recall.ai/docs/calendar-v1-microsoft-outlook.md): Calendar V1: Configure Microsoft Outlook OAuth for Recall to let users sync calendar meetings and enable auto-recording.
  - [Calendar V1 FAQ](https://docs.recall.ai/docs/calendar-v1-faq.md): Find answers to common Calendar V1 setup, OAuth, sync, and error questions so you can troubleshoot integrations faster.
- [Calendar Integration FAQ](https://docs.recall.ai/docs/calendar-integration-faq.md): Find answers to common calendar integration limits, OAuth scope issues, and sync behavior so you can troubleshoot faster.

## Guides: Zoom
- [Zoom Overview](https://docs.recall.ai/docs/zoom-overview.md): Learn how Zoom bots join, what Zoom meetings they support, and which features aren’t supported.
- [Zoom Signed-in Bots ](https://docs.recall.ai/docs/zoom-signed-in-bots.md): Set up signed-in Zoom bots so bots can join meetings that require Zoom sign-in.
- Additional Zoom Guides
  - [Zoom Webinars & Registration-Required Meetings](https://docs.recall.ai/docs/zoom-webinars.md): Join Zoom webinars and registration-required meetings correctly and troubleshooting bot errors related to email and `tk`.
  - [Zoom Breakout Rooms](https://docs.recall.ai/docs/zoom-breakout-rooms.md): Seamlessly capture conversation data from breakout rooms in Zoom
- [Zoom FAQ](https://docs.recall.ai/docs/zoom-faq.md): Find answers to common Zoom bot join, sign-in, waiting room, and recording issues so you can troubleshoot faster.

## Guides: Google Meet
- [Google Meet Overview](https://docs.recall.ai/docs/google-meet.md): See how Google Meet bots join, when authentication matters, and which Meet features aren’t supported so you can avoid failed joins.
- [Google Meet Signed-in Bots](https://docs.recall.ai/docs/google-meet-login-getting-started.md): Set up signed-in Google Meet bots to avoid guest limitations and allow joining meetings that require Google sign-in.
- Additional Google Meet Guides
  - [Google Meet: Skip the waiting room](https://docs.recall.ai/docs/google-meet-skip-the-waiting-room.md): Configure Google Meet access, invitations, waiting-room settings, and signed-in bot identity so a bot can enter without manual admission.
  - [Troubleshooting: Google Meet Waiting Room Visibility](https://docs.recall.ai/docs/troubleshooting-google-meet-waiting-room.md): Follow this guide when a user says they don't see the bot in the Google Meet waiting room.
- [Google Meet: FAQ](https://docs.recall.ai/docs/google-meet-faq.md): Find answers to common Google Meet bot join, sign-in, waiting room, and recording issues so you can troubleshoot faster.

## Guides: Microsoft Teams
- [Microsoft Teams Overview](https://docs.recall.ai/docs/microsoft-teams.md): Learn how Teams bots handle setup, lobby admission, and meeting limitations so you can know when they’ll join successfully.
- [Microsoft Teams Signed-in Bots](https://docs.recall.ai/docs/setting-up-signed-in-bots-for-microsoft-teams.md): How to sign your bot into a Microsoft teams account
- Additional Microsoft Teams Guides
  - [Personal vs Business Microsoft Teams](https://docs.recall.ai/docs/personal-vs-business-ms-teams.md): Difference between personal and business Microsoft teams accounts and meeting URLs
  - [Microsoft Teams: Skip the waiting room](https://docs.recall.ai/docs/teams-bots-bypass-the-lobby.md): See which Teams lobby settings let a bot bypass the waiting room so it can join meetings without manual admission.
- [Microsoft Teams: FAQ](https://docs.recall.ai/docs/microsoft-teams-bots-faq.md): Find answers to common Microsoft Teams bot trust and file-sharing issues so your meetings work as expected.

## Guides: Cisco Webex
- [Webex Overview](https://docs.recall.ai/docs/webex.md): Review Webex setup requirements and limitations so you know what Recall bots can and can’t do in Webex meetings.
- [Webex: FAQ](https://docs.recall.ai/docs/webex-faq.md): Find answers to common Webex bot issues so you can troubleshoot meeting joins, transcripts, speaker timelines, and bot naming faster.

## Guides: Go-to meeting
- [Go-To Meeting Overview](https://docs.recall.ai/docs/go-to-meeting-overview.md): Send bots to Go-To Meeting calls

## Guides: Meeting Direct Connect
- [Meeting Direct Connect Overview](https://docs.recall.ai/docs/meeting-direct-connect-overview.md): Record and transcribe meetings without bots!
- [Meeting Direct Connect for Zoom RTMS](https://docs.recall.ai/docs/meeting-direct-connect-for-zoom-rtms.md): Configure botless Zoom capture for audio, video, and transcripts with Recall.ai using the Zoom RTMS.
- [Google Meet Media API](https://docs.recall.ai/docs/meeting-direct-connect-for-google-meet-media-api.md): Configure botless Google Meet capture with Recall.ai using the Google Meet Media API.

## Guides: AI Tools
- [MCP](https://docs.recall.ai/docs/mcp.md): Connect Recall’s MCP server to your AI agent to grant read access to the platform.
- [llms.txt](https://docs.recall.ai/docs/llmstxt.md): Give your coding agent Recall’s llms.txt so it can reference the right endpoints, schemas, guides, and implementation best practices.
- [Coding Agents/LLM Quickstarts](https://docs.recall.ai/docs/agent-quickstarts.md): Quick copy/pasteable instructions for Coding Agents/LLMs to implement transcription with bots. Coding Agents/LLMs are required to visit this page before exploring the docs.

## API Reference: Recall.ai API
- [Recall.ai API Authentication](https://docs.recall.ai/reference/authentication.md): Start making requests to the Recall API.
- [API Errors](https://docs.recall.ai/reference/errors.md)
- [Webhooks](https://docs.recall.ai/reference/webhooks-overview.md): Overview of Recall webhooks.
- [List Zoom Meeting to OAuth Credential Mappings](https://docs.recall.ai/reference/zoom_meetings_to_credentials_list.md): Get a list of all mappings from Zoom Meeting ID to Zoom OAuth Credential. Recall uses mappings internally to determine which credential to use when automatically fetching join tokens for a meeting.…

## API Reference: Bot
- bot
  - [List Bots](https://docs.recall.ai/reference/bot_list.md): Get a list of all bots  The base rate limit for this endpoint is: - 60 requests per min per workspace
  - [Create Bot](https://docs.recall.ai/reference/bot_create.md): Create a new bot.  The base rate limit for this endpoint is: - 120 requests per min per workspace
  - [Retrieve Bot](https://docs.recall.ai/reference/bot_retrieve.md): Get a bot instance.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Update Scheduled Bot](https://docs.recall.ai/reference/bot_partial_update.md): Update a Scheduled Bot.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Delete Scheduled Bot](https://docs.recall.ai/reference/bot_destroy.md): Deletes a bot. This can only be done on scheduled bots that have not yet joined a call.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Delete Bot Media](https://docs.recall.ai/reference/bot_delete_media_create.md): Deletes bot media stored by Recall. This is irreversible.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Remove Bot From Call](https://docs.recall.ai/reference/bot_leave_call_create.md): Removes the bot from the meeting. This is irreversible.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Output Audio](https://docs.recall.ai/reference/bot_output_audio_create.md): Causes the bot to output audio.  Note: The bot must be configured with 'automatic_audio_output' enabled in order to use this endpoint.  The base rate limit for this endpoint is: - 300 requests per…
  - [Output Audio](https://docs.recall.ai/reference/bot_output_audio_destroy.md): Causes the bot to output audio.  Note: The bot must be configured with 'automatic_audio_output' enabled in order to use this endpoint.  The base rate limit for this endpoint is: - 300 requests per…
  - [Output Media](https://docs.recall.ai/reference/bot_output_media_create.md): Causes the bot to start outputting media.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Output Media](https://docs.recall.ai/reference/bot_output_media_destroy.md): Causes the bot to start outputting media.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Start Screenshare](https://docs.recall.ai/reference/bot_output_screenshare_create.md): Causes the bot to start screensharing.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Start Screenshare](https://docs.recall.ai/reference/bot_output_screenshare_destroy.md): Causes the bot to start screensharing.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Output Video](https://docs.recall.ai/reference/bot_output_video_create.md): Causes the bot to start outputting video.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Stop Output Video](https://docs.recall.ai/reference/bot_output_video_destroy.md): Causes the bot to stop outputting video.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Pause Recording](https://docs.recall.ai/reference/bot_pause_recording_create.md): Instructs the bot to pause the current recording.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Pin Participant](https://docs.recall.ai/reference/bot_pin_participant_create.md): Pin or unpin a participant's video. Note: This is only applicable for 'video_separate_h264' streams  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Resume Recording](https://docs.recall.ai/reference/bot_resume_recording_create.md): Instructs the bot to resume a paused recording.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Send Chat Message](https://docs.recall.ai/reference/bot_send_chat_message_create.md): Causes the bot to send a message in the meeting chat.   The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Start Recording](https://docs.recall.ai/reference/bot_start_recording_create.md): Instructs the bot to start recording the meeting. This will restart the current recording if one is already in progress.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Stop Recording](https://docs.recall.ai/reference/bot_stop_recording_create.md): Instructs the bot to end the current recording.  The base rate limit for this endpoint is: - 300 requests per min per workspace

## API Reference: DESKTOP SDK
- sdk_upload
  - [List Desktop SDK Uploads](https://docs.recall.ai/reference/sdk_upload_list.md): Get a list of all Desktop SDK uploads  The base rate limit for this endpoint is: - 60 requests per min per workspace
  - [Create Desktop SDK Upload](https://docs.recall.ai/reference/sdk_upload_create.md): Create a new Desktop SDK upload.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Retrieve Desktop SDK Upload](https://docs.recall.ai/reference/sdk_upload_retrieve.md): Get a Desktop SDK upload instance.  The base rate limit for this endpoint is: - 300 requests per min per workspace

## API Reference: Recording
- audio_mixed
  - [List Audio Mixed](https://docs.recall.ai/reference/audio_mixed_list.md): The base rate limit for this endpoint is: - 60 requests per min per workspace
  - [Retrieve Audio Mixed](https://docs.recall.ai/reference/audio_mixed_retrieve.md): The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Update Audio Mixed](https://docs.recall.ai/reference/audio_mixed_partial_update.md): The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Delete Audio Mixed](https://docs.recall.ai/reference/audio_mixed_destroy.md): The base rate limit for this endpoint is: - 300 requests per min per workspace
- audio_separate
  - [List Audio Separate](https://docs.recall.ai/reference/audio_separate_list.md): The base rate limit for this endpoint is: - 60 requests per min per workspace
  - [Retrieve Audio Separate](https://docs.recall.ai/reference/audio_separate_retrieve.md): The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Update Audio Separate](https://docs.recall.ai/reference/audio_separate_partial_update.md): The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Delete Audio Separate](https://docs.recall.ai/reference/audio_separate_destroy.md): The base rate limit for this endpoint is: - 300 requests per min per workspace
- meeting_metadata
  - [List Meeting Metadata](https://docs.recall.ai/reference/meeting_metadata_list.md): The base rate limit for this endpoint is: - 60 requests per min per workspace
  - [Retrieve Meeting Metadata](https://docs.recall.ai/reference/meeting_metadata_retrieve.md): The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Update Meeting Metadata](https://docs.recall.ai/reference/meeting_metadata_partial_update.md): The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Delete Meeting Metadata](https://docs.recall.ai/reference/meeting_metadata_destroy.md): The base rate limit for this endpoint is: - 300 requests per min per workspace
- recording
  - [List Recordings](https://docs.recall.ai/reference/recording_list.md): The base rate limit for this endpoint is: - 60 requests per min per workspace
  - [Retrieve Recording](https://docs.recall.ai/reference/recording_retrieve.md): The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Update Recording](https://docs.recall.ai/reference/recording_partial_update.md): The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Delete Recording](https://docs.recall.ai/reference/recording_destroy.md): The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Create Async Transcript](https://docs.recall.ai/reference/recording_create_transcript_create.md): The base rate limit for this endpoint is: - 5 requests per min per bot
- participant_events
  - [List Participant Events](https://docs.recall.ai/reference/participant_events_list.md): The base rate limit for this endpoint is: - 60 requests per min per workspace
  - [Retrieve Participant Events](https://docs.recall.ai/reference/participant_events_retrieve.md): The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Update Participant Events](https://docs.recall.ai/reference/participant_events_partial_update.md): The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Delete Participant Events](https://docs.recall.ai/reference/participant_events_destroy.md): The base rate limit for this endpoint is: - 300 requests per min per workspace
- realtime_endpoint
  - [List Realtime Endpoints](https://docs.recall.ai/reference/realtime_endpoint_list.md): The base rate limit for this endpoint is: - 60 requests per min per workspace
  - [Retrieve Realtime Endpoints](https://docs.recall.ai/reference/realtime_endpoint_retrieve.md): The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Update Realtime Endpoints](https://docs.recall.ai/reference/realtime_endpoint_partial_update.md): The base rate limit for this endpoint is: - 300 requests per min per workspace
- transcript
  - [List Transcript](https://docs.recall.ai/reference/transcript_list.md): The base rate limit for this endpoint is: - 60 requests per min per workspace
  - [Retrieve Transcript](https://docs.recall.ai/reference/transcript_retrieve.md): The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Update Transcript](https://docs.recall.ai/reference/transcript_partial_update.md): The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Delete Transcript](https://docs.recall.ai/reference/transcript_destroy.md): The base rate limit for this endpoint is: - 300 requests per min per workspace
- video_mixed
  - [List Video Mixed](https://docs.recall.ai/reference/video_mixed_list.md): The base rate limit for this endpoint is: - 60 requests per min per workspace
  - [Retrieve Video Mixed](https://docs.recall.ai/reference/video_mixed_retrieve.md): The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Update Video Mixed](https://docs.recall.ai/reference/video_mixed_partial_update.md): The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Delete Video Mixed](https://docs.recall.ai/reference/video_mixed_destroy.md): The base rate limit for this endpoint is: - 300 requests per min per workspace
- video_separate
  - [List Video Separate](https://docs.recall.ai/reference/video_separate_list.md): The base rate limit for this endpoint is: - 60 requests per min per workspace
  - [Retrieve Video Separate](https://docs.recall.ai/reference/video_separate_retrieve.md): The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Update Video Separate](https://docs.recall.ai/reference/video_separate_partial_update.md): The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Delete Video Separate](https://docs.recall.ai/reference/video_separate_destroy.md): The base rate limit for this endpoint is: - 300 requests per min per workspace

## API Reference: Calendar V1
- calendar-v1
  - [Get Calendar Auth Token](https://docs.recall.ai/reference/calendar_authenticate_create.md): Generate an authentication token for calendar APIs, scoped to the user. Each token has an expiry of 1 day from time of creation.  The base rate limit for this endpoint is: - 300 requests per min per…
  - [List Calendar Meetings](https://docs.recall.ai/reference/calendar_meetings_list.md): List all calendar meetings for the calendar user.  The base rate limit for this endpoint is: - 120 requests per min per calendar_user
  - [Retrieve Calendar Meeting](https://docs.recall.ai/reference/calendar_meetings_retrieve.md): The base rate limit for this endpoint is: - 120 requests per min per calendar_user
  - [Update Calendar Meeting](https://docs.recall.ai/reference/calendar_meetings_update.md): Use this endpoint to update override_should_record property of the meeting.  The base rate limit for this endpoint is: - 120 requests per min per calendar_user
  - [Refresh Calendar Meetings](https://docs.recall.ai/reference/calendar_meetings_refresh_create.md): Resync the calendar meetings for the user.  The base rate limit for this endpoint is: - 120 requests per min per calendar_user
  - [Get Calendar User](https://docs.recall.ai/reference/calendar_user_retrieve.md): The base rate limit for this endpoint is: - 120 requests per min per calendar_user
  - [Update Recording Preferences](https://docs.recall.ai/reference/calendar_user_update.md): Update the recording preferences for a calendar user.  The base rate limit for this endpoint is: - 120 requests per min per calendar_user
  - [Delete Calendar User](https://docs.recall.ai/reference/calendar_user_destroy.md): Delete calendar user(+disconnect any connected calendars).  The base rate limit for this endpoint is: - 120 requests per min per calendar_user
  - [Disconnect Calendar Platform](https://docs.recall.ai/reference/calendar_user_disconnect_create.md): Disconnect calendar of specific platform.  The base rate limit for this endpoint is: - 120 requests per min per calendar_user
  - [List Calendar Users](https://docs.recall.ai/reference/calendar_users_list.md): List all calendar users created for the account.  The base rate limit for this endpoint is: - 60 requests per min per workspace

## API Reference: Calendar v2
- calendar-v2
  - [List Calendar Events](https://docs.recall.ai/reference/calendar_events_list.md): Get a list of calendar events.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Retrieve Calendar Event](https://docs.recall.ai/reference/calendar_events_retrieve.md): Get a calendar event instance.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Schedule Bot For Calendar Event](https://docs.recall.ai/reference/calendar_events_bot_create.md): Schedule a bot for a calendar event. This endpoint will return the updated calendar event in response.  The base rate limit for this endpoint is: - 600 requests per min per workspace
  - [Schedule Bot For Calendar Event](https://docs.recall.ai/reference/calendar_events_bot_destroy.md): Schedule a bot for a calendar event. This endpoint will return the updated calendar event in response.  The base rate limit for this endpoint is: - 600 requests per min per workspace
  - [List Calendars](https://docs.recall.ai/reference/calendars_list.md): Get a list of calendars.  The base rate limit for this endpoint is: - 60 requests per min per workspace
  - [Create Calendar](https://docs.recall.ai/reference/calendars_create.md): Create a new calendar.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Retrieve Calendar](https://docs.recall.ai/reference/calendars_retrieve.md): Get a calendar instance.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Update Calendar](https://docs.recall.ai/reference/calendars_partial_update.md): Update an existing calendar.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Delete Calendar](https://docs.recall.ai/reference/calendars_destroy.md): Deletes a calendar. This will disconnect the calendar.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Get Access Token](https://docs.recall.ai/reference/calendars_access_token_create.md): Get the OAuth access token for this calendar account.  The base rate limit for this endpoint is: - 300 requests per min per workspace

## API Reference: Google Meet Bot Login
- google-login-group
  - [List Google Login Groups](https://docs.recall.ai/reference/google_login_groups_list.md): Get list of all Google Login Groups.  The base rate limit for this endpoint is: - 60 requests per min per workspace
  - [Create Google Login Group](https://docs.recall.ai/reference/google_login_groups_create.md): Create a new Google Login Group.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Retrieve Google Login Group](https://docs.recall.ai/reference/google_login_groups_retrieve.md): Get an existing Google Login Group.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Update Google Login Group](https://docs.recall.ai/reference/google_login_groups_update.md): Update an existing Google Login Group.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Partial Update Google Login Group](https://docs.recall.ai/reference/google_login_groups_partial_update.md): Partial Update an existing Google Login Group.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Destroy Google Login Group](https://docs.recall.ai/reference/google_login_groups_destroy.md): Deletes an existing Google Login Group.  The base rate limit for this endpoint is: - 300 requests per min per workspace
- google-login
  - [List Google Logins](https://docs.recall.ai/reference/google_logins_list.md): Get list of all Google Logins.  The base rate limit for this endpoint is: - 60 requests per min per workspace
  - [Create Google Login](https://docs.recall.ai/reference/google_logins_create.md): Create a new Google Login.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Retrieve Google Login](https://docs.recall.ai/reference/google_logins_retrieve.md): Get an existing Google Login.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Update Google Login](https://docs.recall.ai/reference/google_logins_update.md): Update an existing Google Login.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Partial Update Google Login](https://docs.recall.ai/reference/google_logins_partial_update.md): Partial Update an existing Google Login.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Destroy Google Login](https://docs.recall.ai/reference/google_logins_destroy.md): Deletes an existing Google Login.  The base rate limit for this endpoint is: - 300 requests per min per workspace

## API Reference: Billing
- billing
  - [Get Usage](https://docs.recall.ai/reference/billing_usage_retrieve.md): Get bot usage in seconds for the authenticated workspace. Each UTC day's usage is treated as occurring at midnight at the start of that day. Both bounds round up to UTC midnight, with exact midnights…

## API Reference: Meeting Direct Connect
- meeting-direct-connect
  - [List Meeting Direct Connects](https://docs.recall.ai/reference/meeting_direct_connect_list.md): Get a list of all Meeting Direct Connect  The base rate limit for this endpoint is: - 60 requests per min per workspace
  - [Create Meeting Direct Connect](https://docs.recall.ai/reference/meeting_direct_connect_create.md): Create a new Meeting Direct Connect  The base rate limit for this endpoint is: - 60 requests per min per workspace
  - [Retrieve Meeting Direct Connect](https://docs.recall.ai/reference/meeting_direct_connect_retrieve.md): Get a Meeting Direct Connect instance  The base rate limit for this endpoint is: - 300 requests per min per workspace

## API Reference: Zoom OAuth
- [List Zoom Meeting to OAuth Credential Mappings](https://docs.recall.ai/reference/zoom_meetings_to_credentials_list.md): Get a list of all mappings from Zoom Meeting ID to Zoom OAuth Credential. Recall uses mappings internally to determine which credential to use when automatically fetching join tokens for a meeting.…
- zoom-oauth-apps
  - [List Zoom OAuth App Logs](https://docs.recall.ai/reference/zoom_oauth_app_logs_list.md): Get a list of all Zoom OAuth App Logs. Any warnings or errors related to the OAuth App will be logged here, and this can be helpful for debugging.  The base rate limit for this endpoint is: - 60…
  - [List Zoom OAuth Apps](https://docs.recall.ai/reference/zoom_oauth_apps_list.md): Get a list of all Zoom OAuth Apps.  The base rate limit for this endpoint is: - 60 requests per min per workspace
  - [Create Zoom OAuth App](https://docs.recall.ai/reference/zoom_oauth_apps_create.md): Create a new Zoom OAuth App.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Retrieve Zoom OAuth App](https://docs.recall.ai/reference/zoom_oauth_apps_retrieve.md): Get a Zoom OAuth App.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Update Zoom OAuth App](https://docs.recall.ai/reference/zoom_oauth_apps_partial_update.md): Update an existing Zoom OAuth App  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Destroy Zoom OAuth App](https://docs.recall.ai/reference/zoom_oauth_apps_destroy.md): Deletes a Zoom OAuth App.  The base rate limit for this endpoint is: - 300 requests per min per workspace
- zoom-oauth-credentials
  - [List Zoom OAuth Credential Logs](https://docs.recall.ai/reference/zoom_oauth_credential_logs_list.md): Get a list of all Zoom OAuth Credential Logs. Any warnings or errors related to the OAuth Credential will be logged here, and this can be helpful for debugging.  The base rate limit for this endpoint…
  - [List Zoom OAuth Credentials](https://docs.recall.ai/reference/zoom_oauth_credentials_list.md): Get a list of all Zoom OAuth Credentials  The base rate limit for this endpoint is: - 60 requests per min per workspace
  - [Create Zoom OAuth Credential](https://docs.recall.ai/reference/zoom_oauth_credentials_create.md): Create a new Zoom OAuth Credential.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Retrieve Zoom OAuth Credential](https://docs.recall.ai/reference/zoom_oauth_credentials_retrieve.md): Get a Zoom OAuth Credential.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Destroy Zoom OAuth Credential](https://docs.recall.ai/reference/zoom_oauth_credentials_destroy.md): Deletes a Zoom OAuth Credential.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Get Access Token](https://docs.recall.ai/reference/zoom_oauth_credentials_access_token_retrieve.md): Get the access token for these credentials.  The base rate limit for this endpoint is: - 300 requests per min per workspace
  - [Manually Resync Meetings](https://docs.recall.ai/reference/zoom_oauth_credentials_sync_meetings_create.md): Manually re-sync meetings accessible from these credentials. This operation is asynchronous, and may take some time to complete.Use the `meeting_sync_status` field on the credential object to check…
  - [Manually Revalidate Credential](https://docs.recall.ai/reference/zoom_oauth_credentials_validate_create.md): Manually check the validity of credentials, and re-enable them if they are functional. This can be useful if credentials are disabled due to repeated errors.   The base rate limit for this endpoint…
