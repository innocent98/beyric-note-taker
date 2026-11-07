SOURCE: https://docs.recall.ai/reference/bot_create.md
Fetched 2026-11-07 with curl -sL -A 'Mozilla/5.0' (EXCERPT: see the marker inside)

---
updatedAt: 2026-09-23T16:23:55.000Z
agentTools:
  projectIndex: https://docs.recall.ai/llms.txt
---

# Create Bot

Create a new bot.

The base rate limit for this endpoint is:
- 120 requests per min per workspace

### Use this endpoint to:

* Schedule a bot to join planned meetings and capture recordings, <Anchor target="_blank" href="doc:transcription">transcripts</Anchor>, and meeting data.
* Join ad-hoc meetings when a bot can't be scheduled in advance.
* Stream real-time video and audio from a meeting to your app.
* Create <Anchor target="_blank" href="doc:stream-media">interactive agents</Anchor> that join calls to answer questions, guide workflows, or respond in real time.

<Callout icon="❗️" theme="error">
  In production, use [scheduled bots](https://docs.recall.ai/reference/errors#scheduled-bots) by setting the `join_at` parameter to a time at least 10 minutes in the future to guarantee your bot will join on time.

  You should use [ad-hoc bots](https://docs.recall.ai/reference/errors#ad-hoc-bots) sparingly, only when your use case requires it (e.g. for spontaneous meetings). You can create ad-hoc bots by omitting the `join_at` or setting it to a time that is less than 10 minutes in the future.
</Callout>

<Callout icon="⚠️" theme="warn">
  ### **Real-time transcription latency**

  When using `recallai_streaming`, `mode` defaults to `prioritize_accuracy`. Transcript events are typically delayed by **3–10 minutes**, including when delivered through real-time endpoints.

  For English transcripts within seconds, set `recording_config.transcript.provider.recallai_streaming` to:

  ```json
  {
     "mode": "prioritize_low_latency",
     "language_code": "en"
   }
  ```

  This mode delivers event&#x73;**&#x20;immediately after an utterance is finalized (typically between 1–3 seconds)**. It supports English only and does not support customization options such as `key_terms`, `spelling`, or `filter_profanity`.

  Configure event delivery separately through `recording_config.realtime_endpoints`. See [Recall.ai Transcription](https://docs.recall.ai/docs/recallai-transcription) for examples and supported options.
</Callout>

> 📘 Bot status change events
>
> To track bot lifecycle/status changes, listen for bot status change webhook events. Do not poll the API for bot status changes. Polling for bot status changes is an anti-pattern and must not be used in production integrations.
>
> Bot status change events are delivered through the webhook configured in the Recall webhook dashboard. They cannot be subscribed to through `recording_config.realtime_endpoints`.
>
> See <Anchor label="Status Change Webhooks" target="_blank" href="doc:status-change-webhooks-setup-verification">Status Change Webhooks</Anchor> for setup and verification details, and <Anchor label="Bot Webhooks" target="_blank" href="doc:bot-status-change-events">Bot Webhooks</Anchor> for the list of available bot status changes.

# OpenAPI definition

```json

--- EXCERPT of the OpenAPI JSON on that page (only the parts bakeoff.py uses; the full page is ~769 KB) ---

## POST /api/v1/bot/  (Create Bot)
{
 "description": "Create a new bot.\n\nThe base rate limit for this endpoint is:\n- 120 requests per min per workspace",
 "responses": {
  "201": {
   "content": {
    "application/json": {
     "schema": {
      "$ref": "#/components/schemas/Bot"
     }
    }
   },
   "description": ""
  },
  "400": {
   "description": "No response body"
  },
  "507": {
   "content": {
    "application/json": {
     "schema": {
      "description": "If no bots are available in the ad-hoc bot pool the HTTP 507 code is returned. The ad-hoc bot pool is replenished every few minutes so retrying the request will eventually succeed."
     }
    }
   },
   "description": ""
  }
 }
}

## schema AutomaticLeave
{
 "type": "object",
 "properties": {
  "waiting_room_timeout": {
   "type": "integer",
   "minimum": 30,
   "default": 1200,
   "description": "The number of seconds after which the bot will automatically leave the call, if it has not been let in from the waiting room. If you join Zoom using an OBF token, this timeout also caps how long the bot will wait for the associated user to join the meeting; if the user never joins before the timeout, the join fails with zoom_obf_user_not_in_meeting. Note that this has a max value of 600 seconds for Google Meet and 1800 for Microsoft Teams."
  },
  "noone_joined_timeout": {
   "type": "integer",
   "minimum": 30,
   "default": 1200,
   "description": "The number of seconds after which the bot will automatically leave the call, if it has joined the meeting but no other participant has joined."
  },
  "everyone_left_timeout": {
   "allOf": [
    {
     "$ref": "#/components/schemas/AutomaticLeaveEveryoneLeft"
    }
   ],
   "description": "The number of seconds after which the bot will automatically leave the call, if there were other participants in the call who have all left. Note: The everyone_left waiter remains active and will trigger even if recording is paused, unlike other automatic leave waiters which are paused when recording is paused."
  },
  "in_call_not_recording_timeout": {
   "type": "integer",
   "minimum": 1,
   "default": 3600,
   "description": "The number of seconds after which the bot will automatically leave the call, if it has joined the call and is not recording.This includes all periods when the bot is not recording, even if the bot has recorded previously."
  },
  "in_call_recording_timeout": {
   "type": "integer",
   "minimum": 1,
   "description": "The number of seconds after which the bot will automatically leave the call, if it has joined the call and started recording it. This can be used to enforce a maximum recording time limit for a bot. There is no default value for this parameter, meaning a bot will continue to record for as long as the meeting lasts."
  },
  "recording_permission_denied_timeout": {
   "type": "integer",
   "minimum": 1,
   "default": 30,
   "description": "The number of seconds after which the bot will automatically leave the call, if it has joined the call but has not started recording. For e.g This can occur due to bot being denied permission to record(Zoom meetings)."
  },
  "silence_detection": {
   "allOf": [
    {
     "$ref": "#/components/schemas/AutomaticLeaveSilenceDetection"
    }
   ],
   "default": {
    "timeout": 3600,
    "activate_after": 1200
   }
  },
  "bot_detection": {
   "allOf": [
    {
     "$ref": "#/components/schemas/AutomaticLeaveBotDetection"
    }
   ],
   "default": {
    "using_participant_events": {
     "timeout": 600,
     "activate_after": 1200
    },
    "using_participant_names": {
     "matches": [
      "notetaker",
      "recorder",
      "assistant"
     ],
     "timeout": 600,
     "activate_after": 120
    }
   }
  }
 }
}

## schema BotRecordingConfigRetentionRequest
{
 "type": "object",
 "properties": {
  "type": {
   "allOf": [
    {
     "$ref": "#/components/schemas/BotRecordingConfigRetentionSharedTypeEnum"
    }
   ],
   "description": "The type of the retention policy.\n\n* `timed` - Timed\n* `forever` - Forever"
  },
  "hours": {
   "type": "integer",
   "minimum": 1,
   "default": 168,
   "description": "The number of hours to retain the recording."
  }
 },
 "required": [
  "type"
 ]
}

## schema BotRecordingConfigRetentionSharedTypeEnum
{
 "enum": [
  "timed",
  "forever"
 ],
 "type": "string",
 "description": "* `timed` - Timed\n* `forever` - Forever"
}

## schema VideoMixedLayoutEnum
{
 "enum": [
  "speaker_view",
  "gallery_view",
  "gallery_view_v2",
  "audio_only"
 ],
 "type": "string",
 "description": "* `speaker_view` - speaker_view\n* `gallery_view` - gallery_view\n* `gallery_view_v2` - gallery_view_v2\n* `audio_only` - audio_only"
}
