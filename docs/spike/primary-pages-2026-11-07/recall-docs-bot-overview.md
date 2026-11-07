---
updatedAt: 2026-10-18T23:05:18.000Z
agentTools:
  projectIndex: https://docs.recall.ai/llms.txt
---

# Bot Overview

Learn what a Recall bot is, how you can customize it, and what meeting data it exposes so you can build the right meeting integration.

<br />

<Embed typeOfEmbed="youtube" url="https://www.youtube.com/watch?v=5rNgqTuGToI" html="%3Ciframe%20class%3D%22embedly-embed%22%20src%3D%22%2F%2Fcdn.embedly.com%2Fwidgets%2Fmedia.html%3Fsrc%3Dhttps%253A%252F%252Fwww.youtube.com%252Fembed%252F5rNgqTuGToI%253Ffeature%253Doembed%26display_name%3DYouTube%26url%3Dhttps%253A%252F%252Fwww.youtube.com%252Fwatch%253Fv%253D5rNgqTuGToI%26image%3Dhttps%253A%252F%252Fi.ytimg.com%252Fvi%252F5rNgqTuGToI%252Fhqdefault.jpg%26type%3Dtext%252Fhtml%26schema%3Dyoutube%22%20width%3D%22854%22%20height%3D%22480%22%20scrolling%3D%22no%22%20title%3D%22YouTube%20embed%22%20frameborder%3D%220%22%20allow%3D%22autoplay%3B%20fullscreen%3B%20encrypted-media%3B%20picture-in-picture%3B%22%20allowfullscreen%3D%22true%22%3E%3C%2Fiframe%3E" href="https://www.youtube.com/watch?v=5rNgqTuGToI" providerUrl="https://www.youtube.com/" providerName="YouTube" />

## What is a bot?

A **bot** is the fundamental entity for accessing a meeting. Bots are single-use and bots are mapped to a single meeting (i.e. you can't have one bot join multiple meeting). The bot itself exists as a participant in the call and has access to all meeting features and data any other participant has.

With a bot, you can interact within the meeting via video, audio, chat, screenshare, and more. You can also access the meeting data (e.g. video, audio, transcription, participants, and metadata) in real-time during a call, as well as after the call for as long as the data is <Anchor label="retained" target="_blank" href="doc:data-retention">retained</Anchor>. The bot is white label-able, allowing you to customize your bot's appearance and control its interaction in the meeting itself.

<Callout icon="✅" theme="okay">
  Some popular use cases for meeting bots are:

  * Streaming/displaying the meeting recording and transcript from the call in your app (real-time or post-meeting) [<Anchor label="real time transcription sample app" target="_blank" href="https://github.com/recallai/sample-apps/tree/main/bot_real_time_transcription">real time transcription sample app</Anchor>].
  * Extracting summaries and intelligence using AI to show in your app (real-time or post-meeting).
  * Running analysis/workflows based on the meeting transcript (real-time or post-meeting).
  * Having voice/avatar AI agents in meetings who interact with the meeting participants (real-time) [<Anchor label="voice agent sample app" target="_blank" href="https://github.com/recallai/voice-agent-demo">voice agent sample app</Anchor>]
</Callout>

Whether you want to run real-time analysis, transcribe and record meetings, or extract summaries and intelligence using AI, this can all be done through a bot.

### Supported platforms

Recall bots support most of the top meeting platforms, but each of the platforms has their own nuances to be aware of.

| Platform                                           | Supported |
| :------------------------------------------------- | :-------- |
| [Zoom](https://docs.recall.ai/docs/zoom-overview)                          | ✅         |
| [Google Meet](https://docs.recall.ai/docs/google-meet)                     | ✅         |
| [Microsoft Teams](https://docs.recall.ai/docs/microsoft-teams)             | ✅         |
| [Webex](https://docs.recall.ai/docs/webex)                                 | ✅         |
| [Go-To Meeting](https://docs.recall.ai/docs/go-to-meeting-overview) (Beta) | ✅         |

## Creating and scheduling bots

See [Creating and scheduling bots](https://docs.recall.ai/docs/creating-and-scheduling-bots) for more info.

## Customizing your bot

By default, bots show up to meetings like an anonymous participant with their mic/camera off and with the name "Meeting Notetaker".

Regardless of whether you're using our [Calendar Integration](https://docs.recall.ai/docs/calendar-integration) or the [Create Bot](https://docs.recall.ai/reference/bot_create) endpoint, bots are fully whitelabelable which means you can customize how the bot shows up and how it interacts in the meeting. With bots, you can:

* Customize the bot's name - you can set the bot's name by setting the `bot_name` field in the <Anchor label="Create Bot" target="_blank" href="ref:bot_create">Create Bot</Anchor> request.
* Customize the <Anchor label="bot's display image" target="_blank" href="doc:output-video-in-meetings">bot's display image</Anchor> - you can make the bot share an image either through the camera (participant tile) or through a screenshare (if permitted by host settings).
* Make the bot play audio/speak into the meeting using <Anchor label="Output Media" target="_blank" href="doc:stream-media">Output Media</Anchor>.

For example, you can make a bot an active participant by turning it into a <Anchor label="voice agent and/or live avatar" target="_blank" href="doc:stream-media">voice agent and/or live avatar</Anchor> - stream a live avatar into the meeting through the bot's camera and play real-time audio so it can speak/respond.

## Accessing meeting data

Bot's have access to all meeting data that regular participants do, saved as <Anchor label="recording media" target="_blank" href="doc:storage-and-playback#recording-media">recording media</Anchor>. There are two ways to access this data:

* In real-time using <Anchor label="Real-Time Endpoints" target="_blank" href="doc:real-time-endpoints">Real-Time Endpoints</Anchor>
* After the meeting has completed on the bot recording's [media shortcuts](https://docs.recall.ai/docs/media-shortcuts)

### Video + audio

|                     | Mixed                                                                                                                                                               | Separate |
| :------------------ | :------------------------------------------------------------------------------------------------------------------------------------------------------------------ | :------- |
| Async (File)        | <Anchor label="How to get Mixed Video MP4 (Async)" target="_blank" href="doc:how-to-get-mixed-video-mp4">How to get Mixed Video MP4 (Async)</Anchor>                | -        |
| Real-time (Streams) | <Anchor label="How to get Mixed Video + Audio (Realtime)" target="_blank" href="doc:stream-real-time-video-rtmp">How to get Mixed Video + Audio (Realtime)</Anchor> | -        |

### Video only

|                     | Mixed                              | Separate                                                                                                                                                                                                            |
| :------------------ | :--------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Async (File)        | (Only accessible as video + audio) | <Anchor label="How to get Separate Videos per Participant (Async)" target="_blank" href="doc:how-to-get-separate-videos-per-participant-async">How to get Separate Videos per Participant (Async)</Anchor>          |
| Real-time (Streams) | (Only accessible as video + audio) | <Anchor label="How to get Separate Videos per Participant (Realtime)" target="_blank" href="doc:how-to-get-separate-videos-per-participant-realtime">How to get Separate Videos per Participant (Realtime)</Anchor> |

### Audio only

|                     | Mixed                                                                                                                                                      | Separate                                                                                                                                                                                                         |
| :------------------ | :--------------------------------------------------------------------------------------------------------------------------------------------------------- | :--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Async (File)        | <Anchor label="How to get Mixed Audio MP3 (Async)" target="_blank" href="doc:how-to-get-mixed-audio-async">How to get Mixed Audio MP3 (Async)</Anchor>     | <Anchor label="How to get Separate Audio per Participant (Async)" target="_blank" href="doc:how-to-get-separate-audio-per-participant-async">How to get Separate Audio per Participant (Async)</Anchor>          |
| Real-time (Streams) | <Anchor label="How to get Mixed Audio (real-time)" target="_blank" href="doc:how-to-get-mixed-audio-real-time">How to get Mixed Audio (real-time)</Anchor> | <Anchor label="How to get Separate Audio per Participant (Realtime)" target="_blank" href="doc:how-to-get-separate-audio-per-participant-realtime">How to get Separate Audio per Participant (Realtime)</Anchor> |

### Transcription

There are three ways to generate transcripts through Recall:

* <Anchor target="_blank" href="ref:recallai-transcription">Recall.ai Transcription</Anchor>: Using transcription directly through Recall.
* <Anchor target="_blank" href="doc:meeting-caption-transcription">Meeting Caption Transcription</Anchor>: Using the meeting platform's native closed captioning feature to generate a transcript in real-time.
* <Anchor target="_blank" href="doc:ai-transcription">AI Transcription</Anchor>: Using a 3rd party AI transcription integration.

Which of these you choose depends on your requirements, and we recommend reviewing the documentation for each to decide which best fits your use case.

### Participants and participant events

Bots capture several different types of participant data:

* A list of participants who are in the call
* A [speaker timeline](https://docs.recall.ai/docs/speaker-timeline) of all the timestamps of when a different participant started speaking
* A list of all the participant events that occurred throughout the call (e.g. camera on/off, screenshare on/off, mic on/off, chat messages, etc.)

This data is particularly useful for showing the user additional information about the meeting or controlling features based on participant states in your app. See <Anchor label="Meeting Participants & Events" target="_blank" href="doc:meeting-participants-events">Meeting Participants & Events</Anchor> for more info.

### Meeting data

Bots can also capture some data about the meeting itself (e.g. meeting title). See <Anchor label="Meeting Metadata" target="_blank" href="doc:meeting-metadata">Meeting Metadata</Anchor> for more info.

<br />