SOURCE: https://docs.recall.ai/docs/bot-detection.md
Fetched 2026-11-07 with curl -sL -A 'Mozilla/5.0'

---
updatedAt: 2026-09-23T06:37:43.000Z
agentTools:
  projectIndex: https://docs.recall.ai/llms.txt
---

# Detecting Other Bots & Automatically Leaving

Configure your bots to automatically leave when only bots are left on a meeting.

You may run into scenarios where all participants leave a call but there are still other bots or notetakers present.

Recall.ai has several `automatic_leave` settings you can configure to handle these situations, so your bots leave automatically when there are only other bots left on the call.

At a high level, these settings control when your bots leave when:

* **All participants remaining have names that look like a bot**
* **All  participant remaining one ever speaks or shares screen**
* **The call has been silent for too long**

The leaving behaviors for each of these can be configured in the `automatic_leave` object in your <Anchor target="_blank" href="ref:bot_create">Create Bot</Anchor> requests, or any other bot creation endpoints such as the <Anchor target="_blank" href="ref:calendar_events_bot_create">Schedule Bot For Calendar Event</Anchor> endpoint.

<br />

## Bot detection mechanisms

### Using participant name matches

This is the most accurate way to detect bots, but is also the most explicit and requires the most configuration.

This bot detection mechanism works by substring matching (case on a set of known notetaker names you provide, so it's most effective when you provide a comprehensive list of known notetaker names.

**Example Configuration:**

```json
"automatic_leave": {
  "bot_detection": {
    "using_participant_names": {
      "matches": ["notetaker", "recorder", "assistant", "copilot", "grain", "fellow", "tl;dv", "read.ai", "fathom", "agent"],
      "activate_after": 300, // 5 minutes
      "timeout": 5
    }
  }
}
```

**Behavior:**

* For the first 5 minutes of the call, this automatic leave mechanism is not active.
* After 5 minutes, if every remaining participant matches one of the strings in the `matches` array, start a 5s timer.
* If the condition above remains true, the bot leaves after 5 seconds.
* If the condition has not been met after the 5 minute `activate_after` period, the bot will continuously check until this condition is met, and leave after 5 seconds of this being true.

If you find new scenarios where bots take a long time to time out, we recommend adding to your list of `matches` to optimize this behavior and handle as many notetaker names as possible.

### Using participant events

This bot detection mechanism works by analyzing participant **behavior**, and assumes a participant is a bot if the participant both:

* Does **not** speak (does not produce an [Active Speaker Event](https://docs.recall.ai/docs/speaker-timeline)) at any point in the call.
* Does **not** share their screen at any point in the call.

**Example configuration:**

```json
"automatic_leave": {
  "bot_detection": {
    "using_participant_events": {
      "activate_after": 300, // 5 minutes
      "timeout": 10
    }
  }
}
```

**Behavior:**

* For the first 5 minutes, this automatic leave mechanism is not active.
* After 5 minutes, this mechanism kicks in and begins checking if any participants have not had an active speaker event or shared their screen.
  * If *any* participants have had either an active speaker event or shared their screen: There are still "real" participants present, and the bot will **not** automatically leave.
  * If all remaining participants have not spoken during the call or shared their screen: There are only bots present and thus the bot will leave after 10 seconds.

### Using silence detection

Silence detection is your "fallback" if bots are not detected based on participant names or events.

It's also the most likely to produce false positives (for example, if users take a quick break during a call), so in general you should configure this most conservatively compared to the other bot detection mechanisms.

**Example configuration:**

```json
"automatic_leave": {
  "silence_detection": {
    "activate_after": 1200,
    "timeout": 300
  }
}
```

**Behavior:**

* For the first 20 minutes, this automatic leave mechanism is not active.
* After 20 minutes, this mechanism kicks in and begins analyzing the audio for silence.
* At any point after 20 minutes:
  * If there is 5 minutes of continuous silence, the bot will leave.
  * If audio is detected during this 5 minute countdown, the `timeout` timer will reset.

<br />

## How to fine-tune your `activate_after` and `timeout` values

A simple way to think about this:

`activate_after` → **How long should the bot wait before a given automatic leave mechanism kicks in?**

For aggressive bot detection and minimizing the amount of time a bot waits, pick a low value like 120-300 seconds. Keep in mind that this may increase the number of false positives if your users ever join calls late and there are only bots present.

To be extra safe and minimize the chance of bots leaving when they shouldn't, choose a higher value like 600-1200 seconds.

`timeout` → **Once the automatic leave condition is met, how long should the bot wait before leaving?**

For aggressive bot detection and minimizing the amount of time a bot remains on the call with only bots present, pick a low value such as 10–30 seconds for participant names/events, and 5–10 minutes for silence detection. To prevent false positives, we recommend setting the `silence_detection.timeout` to a minimum of several minutes.

To be extra safe, choose a higher value like 3-5 minutes for participant names/events, and 10-20 minutes for silence detection.

<Callout icon="📘" theme="info">
  We recommend starting from the example configurations above, and adjusting based on how aggressive you want your bots to be about leaving and how safe you'd like to be about preventing false positives.
</Callout>

## Behavior when recording is paused or stopped

Bot detection and silence detection timers behave differently depending on whether recording is **paused** or **stopped**.

### Pausing recording

When recording is paused, most `automatic_leave` timers are **paused** (frozen in place). When recording is resumed, each paused timer **resumes from where it left off**. For example, if a `bot_detection.using_participant_events` timer had 6 seconds remaining out of a 10 second `timeout` when recording was paused, it will resume with 6 seconds remaining when recording is resumed.

<Callout icon="📘" theme="info">
  The `everyone_left_timeout` and `in_call_not_recording_timeout` timers are **not** paused when recording is paused.
</Callout>

### Stopping recording

When recording is stopped, `automatic_leave` timers are **not** paused - they continue running. Only `silence_detection` is suspended, since there is no active audio stream to analyze.

This means that if the bot's recording is stopped and only bots remain on the call, the `bot_detection.using_participant_names` and `bot_detection.using_participant_events` timers will still trigger and  cause the bot to leave.
