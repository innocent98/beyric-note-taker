SOURCE: https://docs.recall.ai/docs/microsoft-teams.md
Fetched 2026-11-07 with curl -sL -A 'Mozilla/5.0'

---
updatedAt: 2026-08-28T20:37:26.000Z
agentTools:
  projectIndex: https://docs.recall.ai/llms.txt
---

# Microsoft Teams Overview

Learn how Teams bots handle setup, lobby admission, and meeting limitations so you can know when they’ll join successfully.

## Setup

<Callout icon="✅" theme="okay">
  ### No setup needed

  Teams Web bots work "out-of-the-box," no setup or configuration needed
</Callout>

## Joining & Lobby Behavior

By default, Microsoft Teams bots will need to be let in from the lobby, though this behavior can be overridden by Teams organization settings.

Authenticated Teams bots are still subject to any lobby settings enabled by the host. For instance, the host can still require that *all* participants enter through the waiting room.

*For additional details on Teams Lobby settings, please see the official [Teams documentation](https://learn.microsoft.com/en-us/microsoftteams/who-can-bypass-meeting-lobby?utm_source=chatgpt.com) on this topic.*

<Callout icon="📘" theme="info">
  ### Best practices

  Due to the above behavior, users may run into scenarios where they're unable to let in their own bot from the lobby (for example, when attending external meetings).

  To ensure that a participant with the appropriate permissions allows the bot into the call, we recommend that end users notify the host of the meeting in advance that they plan to bring a notetaker to the meeting.
</Callout>

<br />

## Limitations

***

### Non-standard Teams meetings

Non-standard Teams meetings are currently unsupported. This includes:

* Registration-required meetings
* Registration-required internal webinars
* Teams Live Events
* Town Hall Events
* Direct calls
* Microsoft 365 GCC High Cloud
* Teams breakout rooms

### Chat messages&#x20;

Bots do not support sending and receiving normal messages or chat messages from Teams channel meetings.

### Other features

Currently, Microsoft Teams bots do not support:

* Excel live