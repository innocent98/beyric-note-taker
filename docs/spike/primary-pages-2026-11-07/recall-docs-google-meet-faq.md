SOURCE: https://docs.recall.ai/docs/google-meet-faq.md
Fetched 2026-11-07 with curl -sL -A 'Mozilla/5.0'

---
updatedAt: 2026-10-13T18:47:18.000Z
agentTools:
  projectIndex: https://docs.recall.ai/llms.txt
---

# Google Meet: FAQ

Find answers to common Google Meet bot join, sign-in, waiting room, and recording issues so you can troubleshoot faster.

## Can the bot skip the waiting room?

If the bot is [signed in to a Google account](https://docs.recall.ai/reference/google-meet-login-getting-started), and that account's email address has been added to the meeting invite, then it will be able to skip the waiting room and directly enter the meeting.

## The bot shows as in\_waiting\_room, but I don't see the pop-up?

If a bot was sent to a Google Meet meeting, but your user didn't see it ask to be let in from the waiting room, it's likely because they were not the host of the meeting.

If the meeting was organized by someone else, by default, they won't be able to let anyone in (and won't see any indication of the bot asking to be let in), as only the "Hosts" will see popups to let participants in from the waiting room. By default, other users from the host's Google organization will also be able to allow participants in.

If none from the host's Google org is in the call yet, nobody will see a pop-up to let the bot in. Once someone from the host organization joins, they will see the pop-up appear.

If you're not sure whether you have permissions to allow participants into the call, you can look at the participants panel in the Google Meet call.

<Image src="https://files.readme.io/ea48e720680eb2f9c5e54d09446cb243d01e63a4bb2fd1f78937576a7f09cc92-CleanShot_2025-07-17_at_09.55.20.png" alt="Meeting host's point of view.  
As the meeting host, by default you're able to view and admit participants from the waiting room." align="center" caption="Meeting host's point of view.
As the meeting host, by default you're able to view and admit participants from the waiting room." />

<Image src="https://files.readme.io/12cae6ba1fb3d45aaacffeecfe458db0f20e4dc0c4669a02370295527f4b9ba7-CleanShot_2025-07-17_at_10.00.59.png" alt="Visitor's point of view.  
As a visitor, by default you cannot view and admit participants from the waiting room.  
This behavior can be changed by the organizer through Google Meet [host control settings](https://support.google.com/meet/answer/16229038?hl=en&co=GENIE.Platform%3DDesktop)." align="center" caption="Visitor's point of view.
As a visitor, by default you cannot view and admit participants from the waiting room.
This behavior can be changed by the organizer through Google Meet [host control settings](https://support.google.com/meet/answer/16229038?hl=en\&co=GENIE.Platform%3DDesktop)." />

Participants whose email address is on the event invite are able to join with no host present, so other invited participants may join before the host and wonder why the bot is not joining.

This behavior can be configured as shown [here](https://support.google.com/a/answer/13722774?hl=en), but since you likely don't want to ask someone to change their Google workspace settings, we recommend informing the host/organizer of the meeting that you've invited a bot. This way, there is no confusion and there's no need for them to change their settings.

### **What if the user is the host and is still seeing this behavior?**

If the user is the host and is still unable to see the bot's request to join, it's likely due to their host control settings.

Under **Meeting Access > Meeting Access type**: There's a checkbox labelled "Anyone with the meeting link can ask to join":

<Image src="https://files.readme.io/d47eceb50e55efce5cbe5114dd81e7361f04cd0d409aabdf656cf057047766f3-CleanShot_2025-08-18_at_09.54.51.png" align="center" border={true} />

If this box is unchecked, then **all anonymous participants and participants not on the calendar invite** will be automatically declined when attempting to join the meeting.

This will be surfaced as a `fatal` [Status Change](https://docs.recall.ai/docs/status-change-webhooks-setup-verification) with the `google_meet_bot_blocked` sub code through the API.

For more information on this behavior, see Google's [official documentation](https://support.google.com/a/users/answer/11989526?hl=en#zippy=%2Clearn-how) on this setting.

<Callout icon="📘" theme="info">
  To enable bots to join calls with this setting enabled, you should use [Signed-In Google Google Meet Bots](https://docs.recall.ai/docs/google-meet-login-getting-started), and ensure the underlying email address of the bot is on the calendar invite.
</Callout>

## How do I change the bot's icon in the participants list?

By default, the bot's icon in the Google Meet's participant list will be the first letter of it's name, in a circle of random color such as the following:

<Image src="https://files.readme.io/b6dba0a-CleanShot_2024-06-04_at_15.48.432x.png" align="center" width="350px" />

The only way to change this to something else is to have the bot join as a [Signed-In Google Meet Bot](https://docs.recall.ai/docs/google-meet-login-getting-started). If the bot is signed in, it's icon will be the profile picture of the account it's signed in to.

<br />

## Why did my bot fail to join a meeting with `google_meet_bot_blocked` status code?

Google has some detection metrics in place to automatically block unwanted users from joining calls for various reasons.

One of the main reasons are when the bot name contains certain words (curses, profane language, or other trigger words). Other related times, the filter can be triggered by seemingly innocent names and phrases. To test this issue, join a Google Meet call from an incognito browser window with the name you're testing. If you're rejected, you'll need to modify the name until it's accepted by Google.

The other case when this can happen is when the host's **Host Controls** settings prevent all anonymous participants and any users not on the underlying calendar invite from "knocking". For more information on how to solve this, please see [this FAQ](https://docs.recall.ai/docs/google-meet-faq#what-if-the-user-is-the-host-and-is-still-seeing-this-behavior).

<br />

## Why is my bot failing to join a call with the `google_meet_sign_in_missing_login_credentials` status code?

This error happens when the bot tries to join a meeting that requires all participants to be signed in to Google accounts. This is a setting that's controlled by the Google Workspace Administrator. The admin will need to change their Meet safety settings to allow **all users** to join meetings hosted in their organization. Alternatively, you can implement [signed-in Google Meet bots](https://docs.recall.ai/docs/google-meet-login-getting-started) to get around this.

<Image src="https://files.readme.io/8253c7a7623d9a4f94440a4ec26523739a8a3b649cdb07a96b4458cafa5805aa-image.png" align="center" />

<br />

<br />

## Why is my bot failing to join a call with the `google_meet_knocking_disabled` status code?

This error happens when the bot tries to join a meeting that has disallowed external participants from asking to join the meeting. This can be resolved by checking the "Anyone with the meeting link can ask to join" box.

<Image src="https://files.readme.io/666eef48b0ef36276dec816f335b6b67ae4756fd09d817a0293f8c2e10cbe4c7-CleanShot_2025-09-09_at_08.44.15.png" align="center" border={true} />

<br />

## How to handle adaptive audio causing incorrect speaker attribution for in transcripts?

Google meet's [Adaptive audio](https://support.google.com/meet/answer/14263133?hl=en-GB\&src=supportwidget0\&authuser=0) routes everyone’s in-room audio through a single device so you can join from multiple laptops without echo. When that happens, your recorder sees one audio stream tied to one participant, so all speech from the room is attributed to that person

The workaround is to use [machine diarization](https://docs.recall.ai/docs/diarization#machine-diarization)  to re-transcribe the call. The transcription provider will separate speakers by voice and produce a new transcript with accurate attribution. The tradeoff is machine diarization can’t see the meeting roster, so speakers are labeled anonymously (e.g., “A,” “B,” “C”) rather than by participant names.

It’s hard to detect adaptive audio automatically because users can enable it mid-meeting. Most developers add a “Re-transcribe” button in their dashboard to run an async re-transcription using machine diarization when misattribution is noticed. This results in:

* Most transcriptions being diarized with speaker names
* These special cases being transcribed with appropriate speaker attritbution

<br />

## Can you minimize/hide the bot's tile for all other participants?

Hiding participant video is an individual participant setting, so it's not something that Recall bots or other users can control for other participants on a call.

If this is a big problem for your customers then you may want to explore a non-bot form factor for capturing meetings, like our [Desktop Recording SDK](https://docs.recall.ai/docs/desktop-sdk).

<br />

## Why did my bot end with the `bot_received_leave_call` sub code when no request was made to the [Remove Bot From Call](https://docs.recall.ai/reference/bot_leave_call_create)?

This can happen when the host has Host Management settings enabled host chooses the "End meeting for everyone" option when ending the call.

<Image src="https://files.readme.io/054e1f8c96524d4e4f7fa97ad70fe0c911cda477d427558b7a04338a818a810b-CleanShot_2025-11-10_at_17.21.112x.png" align="center" width="40% " />

This will remove the bot from the call, regardless of whether it's in the waiting room or the main room.

<br />

## How to get rid of "participant may not be who they claim to be" disclaimer under the participant name?

This message is a security measure implemented by Google [for anonymous participants](https://arc.net/l/quote/gmcerpyl) when they suspect the participant may not be who they claim to be.

You can work around this by having the [Google meet bot sign-in](https://docs.recall.ai/docs/google-meet-login-getting-started) when joining meetings. This will verify the bot as a real participant to Google so the disclaimer won't show.

There are a few limitations to be aware of though with using Google Meet bots:

* The bot name will be taken from the Google account used to authenticate the bot. This means the `bot_name` parameter in [Create Bot](https://docs.recall.ai/reference/bot_create) endpoint will be overwritten by the Google account's display name.
* The bot image will be taken from the Google account used to authenticate the bot. This means participant's image in the call will be taken from the Google account's (if set). You can still use `automatic_video_output` to [output an image](https://docs.recall.ai/docs/output-video-in-meetings) via the bot's camera.

# Authenticated Bots

***

## What's the concurrency limit per Google Login?

Based on our testing, the concurrency limit for a single Google Login is *roughly* 50, but this number isn't made public by Google and can have a high degree of variance.

Because of this, Recall adds an extra buffer which will kick in at 30 concurrent logins and assigns a different account to the bot. You should use this number as a heuristic for determining the number of logins you need, but we recommend also erring on the side of caution by adding an extra buffer yourself.

<br />

## What happens when all of my Google Logins are at their login limit?

When the limit is reached for all accounts, you'll receive a fatal event with the sub code `google­_meet­_login­_not­_available` and you should create a new user to increase your concurrency limit.

<br />

## Can I use the same Google Workspace for multiple Recall accounts/environments?

Yes - you shouldn't have any issues with sharing the same Google Workspace across multiple accounts.

<br />

## Can I enable authenticated Google Meet bots for some meetings, but use the default behavior for others?

Yes! To configure authentication behavior for Google Meet, you can use the `google_meet.login_required` parameter in [Create Bot](https://docs.recall.ai/reference/bot_create).

If this parameter is absent in the request body, bots will authenticate **only if** the Google Meet is sign-in only. Otherwise, the bot will default to joining as an anonymous user.

If you set `google_meet.login_required` to `true`, bots will **always** join Google Meet calls as authenticated users, regardless of the meeting's joining requirements.

<br />

## Why does the meeting title in the bot's metadata different from the actual title?

This occurs when the bot joins a Google Meet meeting where the end time is has already past. Google Meet doesn't show the real title after the meeting has ended so it is not possible to get the meeting title

<br />

## Can bots raise their hands in Google Meet meetings?&#x20;

No, currently Google Meet bots do not support raising their hands during meetings.&#x20;

<br />
