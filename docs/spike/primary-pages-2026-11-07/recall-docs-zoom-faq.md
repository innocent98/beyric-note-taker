SOURCE: https://docs.recall.ai/docs/zoom-faq.md
Fetched 2026-11-07 with curl -sL -A 'Mozilla/5.0'

---
updatedAt: 2026-11-05T19:02:41.000Z
agentTools:
  projectIndex: https://docs.recall.ai/llms.txt
---

# Zoom FAQ

Find answers to common Zoom bot join, sign-in, waiting room, and recording issues so you can troubleshoot faster.

## What does a Recall.ai Zoom bot require to join a Zoom meeting?

Recall.ai's Zoom meeting bots only require a meeting URL to join a Zoom meeting.

## What if Zoom returns an internal error?

On rare occasions, your bot may fail to enter a call with a `zoom_internal_error` [sub code](https://docs.recall.ai/docs/sub-codes).

Unfortunately this means something went wrong on Zoom's end, and we have limited visibility into the underlying error. We do our best to handle these errors internally through retries but on rare occasions this error can cause a bot to fail.

If you're seeing this error recur for a certain situation or user, please let us know and we can raise the issue with Zoom.

<Callout icon="📘" theme="info">
  If you get a `zoom_internal_error` when providing a `start_url` containing a pre-embedded`zak` token, if the meeting is scheduled far into the future, you will receive a `zoom_internal_error` when the bot tries to join.

  This is because `zak` tokens are short-lived, and attempting to join a meeting with an expired token will cause Zoom to throw an error.
</Callout>

## What happens if there is a password on the Zoom meeting?

If there is a password on the meeting, it should be embedded in the meeting URL as a query parameter by default. For example, a meeting URL with an embedded password might look like this: <https://zoom.us/j/123?pwd=abc>. Everything after the `pwd=`in the URL is the password, which the bot will use to join the call.

If the password is not in the query parameter, the bot will not be able to join the call.

## Does the bot need to be let in every time?

The bot will go into the waiting room only if there is a waiting room enabled.

Think about the bot just like a normal participant. If there's a waiting room enabled, then it will go into the waiting room with other participants.

If there is no waiting room enabled, then it will skip the waiting room and join the meeting directly, just like other participants.

## Why isn't my bot sending chat messages?

If the chat messages sent by your bot aren't showing up, it's likely a problem with your Zoom configuration. Please ensure that "Continuous Meeting Chat" is enabled on your Zoom account.

<Image src="https://files.readme.io/743c501a76963f60eabad93a13bef499c43bac7a3c72028a8b11433222505581-CleanShot_2024-09-13_at_13.24.41.png" align="center" border={true} />

You should also ensure that direct messages are enabled on your Zoom account.

<Image src="https://files.readme.io/5de0f5ec6147da80ea02ca0ea3d019af07c11b09f9ff1234ae2e1796c217c3f9-CleanShot_2025-06-25_at_16.09.232x.png" align="center" border={true} />

<br />

## Why aren't DM's from my bot showing up?

You will need to make sure that the host's Zoom chat settings are set to chat with "Everyone and anyone directly" if you want the bot to be able to DM anyone in the call. If this setting is not configured, the bot will only be able to DM the host of the call.

<Image src="https://files.readme.io/4699653ba912c3355cda31060f656edcbe117d9d5e1c591b569804016e72b16b-CleanShot_2025-08-16_at_18.52.472x.png" align="center" border={true} />

<br />

## Is there a way to hide participants (bots) that don't have their video on?

This is controlled by Zoom settings at the individual user level, and bots can't control this setting for other participants.

If an end user would like to remove the bot from their view on Zoom calls, they can disable the **Show non-video participants** setting under **Meeting & webinars > Participant video:**

![](https://files.readme.io/3c34b6ed69a087aeb04d1c34ad9eb566164b2e11de06def3eb939896a00dc1a0-CleanShot_2025-10-14_at_17.15.192x.png)

## Can I control who let's the bot in from the waiting room?

Only participants with the [host and co-host](https://support.zoom.com/hc/en/article?id=zm_kb\&sysparm_article=KB0064033) role can let participants in from the waiting room. This is a [platform-imposed limitation by Zoom](https://support.zoom.com/hc/en/article?id=zm_kb\&sysparm_article=KB0065164)

## Can I customize the recording consent disclaimer per meeting?

No, the recording consent disclaimer [can only be customized at the organization level](https://support.zoom.com/hc/en/article?id=zm_kb\&sysparm_article=KB0068402) on certain plans

<br />

## The bot says that it's in the waiting room, but I don't see it. Why?

This situation can arise if the meeting was created by a third party that doesn't intend on joining the call.

Typically, participants in the waiting room can [only be admitted by the host or co-host of the meeting](https://support.zoom.com/hc/en/article?id=zm_kb\&sysparm_article=KB0064033). If there is no host or co-host present, then participants in the meeting will not be able to see or interact with participants in the waiting room.

To fix this, the person who scheduled the meeting can modify their settings to allow anybody to admit the bot, not just the host or co-host. You can find this setting in the [Zoom web settings portal](https://zoom.us/profile/setting?tab=meeting), under "Waiting room options". Once the setting is changed to "Host, co-hosts, and anyone who bypassed the waiting room (only if host and co-hosts are not present)", the participants in this call should be able to see the bot in the waiting room and admit it.

<Image src="https://files.readme.io/80e1b7fdf65976b7dc02fd11ee23aa713739af888a3ce5fe4c3315e04a4aad0b-image.png" align="center" border={true} />

## Why did my Zoom bot not record audio?

The bot will not be able to record audio for a specific participant if the participant has the "Live Performance Audio" feature enabled.

![](https://files.readme.io/b274b8a2641e4f914ea26e46f692e1d385013c7e7e447b72e00c48f9efcc80b8-CleanShot_2026-03-12_at_15.46.442x.png)

![](https://files.readme.io/ee4aa1995b7d19adfcefefa6655c703bb0515121462a70ae09f70b840f52dd3b-CleanShot_2026-07-02_at_08.50.012x.png)

## If a Zoom participant update their name mid-call, will this be reflected through the API?

Yes. If a Zoom participant updates their name during a call:

* The participant's name will be reflected with the updated name when fetched through the API.&#x20;
* If you've subscribed to the `participant_events.update` event in a [Real-Time Endpoint](https://docs.recall.ai/docs/real-time-endpoints), you will receive a corresponding event with the updated name.&#x20;

## Why did my bot enter the waiting room when the meeting has waiting room disabled?

Zoom may place a bot in the waiting room if the host has another meeting in progress. The bot will remain there until the other meeting ends, at which point Zoom automatically admits the bot into the meeting. Note that in this case, the bot would not be admitted into the call by the host, meaning the host doesn't need to be present.

<br />