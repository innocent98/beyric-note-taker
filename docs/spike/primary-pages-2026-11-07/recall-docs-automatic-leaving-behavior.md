SOURCE: https://docs.recall.ai/docs/automatic-leaving-behavior.md
Fetched 2026-11-07 with curl -sL -A 'Mozilla/5.0'

---
updatedAt: 2026-10-28T16:53:31.000Z
agentTools:
  projectIndex: https://docs.recall.ai/llms.txt
---

# Bot Automatic Leave Behavior

Control when your bot should automatically leave calls.

There are certain scenarios where you might want your bot to automatically leave calls.

Some of these include:

1. When a bot is stuck in a waiting room
2. When everyone leaves the call
3. When there are only other bots left in the call
4. When a bot is in a call but not recording.

<Callout icon="📘" theme="info">
  The `automatic_leave` setting ensures the bot waits the configured number of seconds before initiating the leave process. The time taken for the bot to fully exit the call can add a few additional seconds, (up to 11 seconds in rare cases).
</Callout>

## Default timeout values

To control the behavior of bots leaving calls, you can configure the `automatic_leave` object when calling [Create Bot](https://docs.recall.ai/reference/bot_create).

Recall automatically sets sensible defaults when a parameter isn't provided. The defaults (in seconds) for are as follows:

```json
{
  "automatic_leave": {
    "waiting_room_timeout": 1200,
    "noone_joined_timeout": 1200,
    "everyone_left_timeout": {
      "timeout": 2,
      "activate_after": null
    },
    "in_call_not_recording_timeout": 3600,
    "recording_permission_denied_timeout": 30,
    "silence_detection": {
      "timeout": 3600,
      "activate_after": 1200
    },
    "bot_detection": {
      "using_participant_events": {
        "timeout": 600,
        "activate_after": 1200
      },
      "using_participant_names": {
        "timeout": 600,
        "activate_after": 120,
        "matches": [
          "notetaker",
          "recorder",
          "assistant"
        ]
      }
    }
  }
}
```

## Participant timeouts

### Silence Detection Timeout

This heuristic uses period of continuous silence in a call to detect if there are only bots remaining in the call.

* `automatic_leave.silence_detection.timeout`: The number of seconds of continuous silence that should occur on the call for the bot to automatically leave. (**Default: 3600(60 minutes)**)
* `automatic_leave.silence_detection.activate_after`: In some cases meeting can start late which can cause continuous silence at the beginning (when bots join on time). You can use this parameter to avoid such false positives by adding a buffer before silence detection is activated. (**Default: 1200(20 minutes)**)

### Everyone Left Timeout

This setting controls how quickly the bot leaves when the bot is the only participant left in the call. The bot uses two parameters:

* `automatic_leave.everyone_left_timeout.timeout`
  * Defines how long (in seconds) the bot will wait after it detects that it’s alone before leaving. Default: 2s.
  * After the bot sees that all other participants have left, it starts this timer. If no one else is back in the call when the timer ends, the stops recording and leaves.
* `automatic_leave.everyone_left_timeout.activate_after`
  * The number of seconds after recording starts before the bot begins checking whether everyone else has left. This is measured from recording start, not when the bot joins the meeting.
  * Defaults to null, which removes the recording-start requirement. If you provide a numeric value and no recording is configured (`recording_config: null`), this check will never activate.
  * This can prevent false positives early in the call (for example, people joining and dropping in the first few minutes). Before this threshold, the bot will not auto-leave just because it’s briefly alone.

The bot must detect at least 1 participant in the call before this timeout is enabled. If the bot joins a call and there's no participant in the call, the bot will not start this timeout until a participant joins the call. The other timeouts still apply.

With the default 2s timeout, if everyone briefly leaves and then rejoins, the bot will likely leave before they’re re-admitted. Increasing the timeout makes the bot more tolerant for cases where the participant joins then leaves temporarily, but you’ll also get more silence at the end of the recording because the bot will sit in an empty call longer before hanging up.

### No-one Joined Timeout

This setting controls how quickly the bot leaves when the bot is the only participant that has joined the call. This is common in cases where the bot joins a meeting without a waiting room but no other participants ever joins. (**Default: 1200 (20 minutes)**)

## Waiting room timeout

You can use the `automatic_leave.waiting_room_timeout` parameter to customize the timeout of bots in waiting rooms.

For timeouts triggered by the `automatic_leave.waiting_room_timeout` parameter, the bot status change will contain a `timeout_exceeded_waiting_room` sub code.

The default value is 20 minutes (1200 seconds).

## Platform default timeouts

Unlike other meeting platforms, **Microsoft Teams has a platform-enforced waiting room timeout of 30 minutes.**

If a bot times out due to platform-enforced waiting room timeout, the bot status change will contain a  `call_ended_by_platform_waiting_room_timeout` sub code.

Since this is enforced by Teams, this timeout will occur regardless of whether you set your bot's `automatic_leave.waiting_room_timeout` to a value longer than this.

For example, if you configure this value to 35 minutes, a Microsoft Teams bot will still timeout after 30 minutes of being in the waiting room.

<Callout icon="📘" theme="info">
  ### Disabled waiting rooms

  The other thing to keep in mind is that for some platforms, hosts can **disable waiting rooms**.

  Think about the bot just like a normal participant. If there's a waiting room enabled, then it will go into the waiting room with other participants.

  If there is no waiting room enabled, then it will skip the waiting room and join the meeting directly, just like other participants.
</Callout>

## **Platform Waiting Rooms**

| Platform        | Default Timeout | Can disable?         |
| --------------- | --------------- | -------------------- |
| Google Meet     | Indefinite      | ✅                    |
| Microsoft Teams | 30 minutes      | :x:                  |
| Zoom            | Indefinite      | :white\_check\_mark: |

## Recording Timeouts

### **In Call Not Recording Timeout**

You may want to set a maximum limit on the amount of time that a bot can be in a call while not recording (e.g. waiting for the host/participants to grant recording consent).

You can do this using the `automatic_leave.in_call_not_recording_timeout` parameter. This will allow you to specify the maximum number of seconds that the bot can be in the call while not recording before it leaves automatically. (**Default: 3600 (60 minutes)**)

### **In Call Recording Timeout**

You may want to set a maximum limit on the amount of time that a bot can be recording in a call (e.g. limiting free accounts to 30 minutes of recording per meeting).

You can do this using the `automatic_leave.in_call_recording_timeout` parameter. This will allow you to specify the maximum number of seconds that the bot can be in the call recording before it leaves automatically.

### **Recording Permission Denied Timeout**

A bot can request for recording permissions and have the request denied by the host/co-host in 2 ways:

* A host/co-host explicitly denies the request via the recording consent prompt shown to the host
* A host/co-host doesn't respond to the recording consent prompt (e.g. they didn't see the request) and the request is automatically denied after 60s

When the bot's request to record is denied, the bot will enter the `recording_permission_denied` state. The recording permission denied timeout defines how long the bot will stay after having it's recording consent automatically denied. (**Default: 30 seconds**)

## **Bot Detection Timeouts**

Recall provides multiple ways to mitigate instances of meetings where participants are bots leading to longer recording lengths and processing times. There are various heuristics that can be enabled/configured depending on usage pattern and requirements.

### Using Participant Events

<Callout icon="📘" theme="info">
  ### Participant Events and Google Meet

  Google Meet occasionally emits false positive participant events for meeting bots in calls. Because of this, the participant events heuristic is less reliable in Google Meet calls compared to other platforms.

  We recommend using a combination of participant events and [participant names](#using-participant-names) to produce the most reliable bot detection & automatic leaving behavior across all platforms.
</Callout>

This heuristic relies on marking participants as bots based on their activity in the call. A participant who has not emitted either `active_speaker` or `screenshare_start` for the entire duration of the meeting is marked as a bot.

* `automatic_leave.bot_detection.using_participant_events.timeout`: The number of seconds after which the bot will leave the call if all remaining participants have been marked as bots. (**Default: 600(10 minutes)**)
* `automatic_leave.bot_detection.using_participant_events.activate_after`: Similar to previous heuristic, this parameter can be used to add a buffer before the heuristic gets activated to avoid false positives. (**Default: 1200(20 minutes)**)

### Using Participant Names

This heuristic relies on marking participants as bots based of their name in the meeting.

* `automatic_leave.bot_detection.using_participant_names.matches`: Provide a list of strings which will be substring matched (case insensitively) to every participant name to mark them as a bot. For e.g given `matches: ['notetaker']`, a participant with name `Meeting Notetaker` will be marked as a bot. (**Default: `["notetaker", "recorder", "assistant"]`**)
* `automatic_leave.bot_detection.using_participant_names.timeout`: The number of seconds after which the bot will leave the call if all remaining participants have been marked as bots. (**Default: 600 (10 minutes)**)
* `automatic_leave.bot_detection.using_participant_names.activate_after`: Similar to previous heuristic, this parameter can be used to add a buffer before the heuristic gets activated to avoid false positives. (**Default: 120 (2 minutes)**)

*Participant events are generally less reliable than participant names as a heuristic for detecting bots. If your bot names use identifiable phrases such as 'bot', 'notetaker', or your company name, we highly recommend using this for best bot detection accuracy.*

See [Detecting Other Bots & Automatically Leaving](https://docs.recall.ai/docs/bot-detection#using-participant-name-matches) for a sample list of names to watch for.

## Behavior when recording is paused or stopped

Bot detection and silence detection timers behave differently depending on whether recording is **paused** or **stopped**.

### Pausing recording

When recording is paused, most `automatic_leave` timers are **paused** (frozen in place). When recording is resumed, each paused timer **resumes from where it left off**. For example, if a `bot_detection.using_participant_events` timer had 6 seconds remaining out of a 10 second `timeout` when recording was paused, it will resume with 6 seconds remaining when recording is resumed.

<Callout icon="📘" theme="info">
  The `everyone_left_timeout` & `in_call_not_recording_timeout` timers are **not** paused when recording is paused.
</Callout>

### Stopping recording

When recording is stopped, `automatic_leave` timers are **not** paused - they continue running. Only `silence_detection` is suspended, since there is no active audio stream to analyze.

This means that if the bot's recording is stopped and only bots remain on the call, the `bot_detection.using_participant_names` and `bot_detection.using_participant_events` timers will still trigger and  cause the bot to leave.
