SOURCE: https://skribby.io/docs.md
Fetched 2026-11-07 with curl -sL -A 'Mozilla/5.0'

Skribby is an API-first platform for deploying meeting bots to **Zoom**, **Microsoft Teams**, and **Google Meet**. Capture audio, video, real-time transcription, and structured meeting data through one integration.

## One API for every meeting

Building and maintaining your own meeting bot infrastructure is complex, expensive, and fragile. Skribby handles the infrastructure, platform-specific join logic, and bot-detection bypassing so you can focus on building your product.

- **Capture** - Record audio and video from supported meetings in **WebM** format. Access raw PCM audio streams or processed recording files.
- **Transcribe** - Choose from supported transcription models including Whisper, Deepgram, and Speechmatics.
- **Process** - Receive speaker-labeled transcripts and recording files. For real-time interactivity, use our WebSockets to receive live events.
- **Privacy controls** - Configure an initial chat message, recording retention, and deletion to match your application's requirements.

## Why Developers Choose Skribby

### Universal Compatibility

Don't build three different integrations. Skribby provides a unified interface that abstracts away the differences between Zoom, Teams, and Google Meet.

### Real-time or Post-call

Whether you need live captions for an interactive tool or high-accuracy transcription for post-meeting analysis, Skribby supports both real-time WebSocket streaming and asynchronous processing.

### Managed Infrastructure

Skribby manages worker provisioning, browser automation, and the platform-specific meeting join flow.

## Create a bot in seconds

```typescript
import { createClient } from '@skribby/sdk';

const skribby = createClient({ api_key: 'sk_...' });

const bot = await skribby.createBot({
    meeting_url: 'https://zoom.us/j/...',
    bot_name: 'Alex from Acme',
    service: 'zoom',
    transcription_model: 'groq/whisper-large-v3-turbo',
});
```

## Supported Platforms

Skribby supports the core meeting bot workflow across all three services. See [Supported Platforms](/docs/guides/platforms.md) for feature-specific differences.

|                               |      Google Meet       |    Zoom     |    Microsoft Teams     |
| :---------------------------- | :--------------------: | :---------: | :--------------------: |
| **Guest joins**               |       Supported        |  Supported  |       Supported        |
| **Authentication**            | Authenticated Accounts | ZAK Tokens  | Authenticated Accounts |
| **Recording and transcription** |       Supported        |  Supported  |       Supported        |

## Get Started

Ready to build? Choose your path:

- **Quickstart Guide** (/guides/quickstart) - Get your first bot into a meeting in under 5 minutes.
- **API Reference** (/rest-api) - Explore our comprehensive REST API endpoints and schemas.
- **SDKs** (/ts-sdk) - Integrate faster with our fully-typed library.

## Explore the Documentation

- **Bot Lifecycle** (/guides/bot-lifecycle) - Understand the states from `booting` to `finished`.
- **Meeting URLs** (/guides/meeting-urls) - Supported URL formats, Safe Links, and common pitfalls.
- **Webhooks** (/guides/webhook-events) - Set up notifications for your application.
- **Authentication** (/guides/bot-authentication) - Join internal meetings using authenticated accounts.

Need help? Join our [Discord Community](https://discord.gg/kBpu9JXCcn) or email [it@skribby.io](mailto:it@skribby.io) for technical questions.
