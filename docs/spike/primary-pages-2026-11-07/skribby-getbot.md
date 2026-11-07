SOURCE: https://skribby.io/docs/rest-api/bot-operations/getbot.md
Fetched 2026-11-07 with curl -sL -A 'Mozilla/5.0' (the .md twin of the Get Bot page: it shows the bot JSON including events[])

# Get Bot

`GET /bot/{id}`

Retrieve information about a specific bot, including its current status,
transcript (if available), participants, and lifecycle events.

## Base URL

`https://platform.skribby.io/api/v1`

`https://platform-jp.skribby.io/api/v1`

## Parameters

| Name | In | Type | Required | Description |

| --- | --- | --- | --- | --- |

| id | path | string | Yes | Bot ID |

| with-speaker-events | query | boolean | No | Include participant timeline events |

## Responses

### 200

Bot information retrieved successfully

#### application/json

```json

{
    "id": "",
    "status": "scheduled",
    "stop_reason": "invalid_meeting_url",
    "recording_start_mode": "automatic",
    "service": "gmeet",
    "scheduled_for": "",
    "time_limit": "",
    "stop_options": {
        "time_limit": 0,
        "waiting_room_timeout": 0,
        "recording_start_timeout": 0,
        "breakout_room_timeout": 0,
        "last_person_detection": 0,
        "empty_meeting_timeout": 0,
        "silence_detection": 0
    },
    "bot_name": "",
    "bot_avatar": "",
    "meeting_url": "",
    "webhook_url": "",
    "custom_metadata": {},
    "recording_url": "",
    "recording_available_until": "",
    "websocket_url": "",
    "websocket_read_only_url": "",
    "websocket_audio_url": "",
    "video": false,
    "lang": "",
    "detected_lang": "",
    "transcript": [
        {
            "start": 0,
            "end": 0,
            "speaker": 0,
            "speaker_name": "",
            "potential_speaker_names": "",
            "confidence": 0,
            "transcript": "",
            "utterances": [
                {}
            ]
        }
    ],
    "transcription_model": "",
    "transcription_model_key": "",
    "participants": [
        {
            "name": "",
            "avatar": "",
            "first_seen_at": "",
            "last_seen_at": 0,
            "left_at": 0,
            "presence_intervals": [
                {
                    "joined_at": 0,
                    "left_at": 0
                }
            ],
            "events": [
                {
                    "type": "started-speaking",
                    "timestamp": 0
                }
            ]
        }
    ],
    "events": [
        {
            "event": "",
            "data": {
                "old_status": "",
                "new_status": ""
            },
            "created_at": ""
        }
    ],
    "profanity_filter": false,
    "custom_vocabulary": [
        ""
    ],
    "created_at": "",
    "finished_at": ""
}

```

### 401

Unauthorized - Invalid API key

#### application/json

```json

{
    "message": ""
}

```

### 404

Bot not found

#### application/json

```json

{
    "message": ""
}

```
