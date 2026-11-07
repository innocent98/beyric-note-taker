SOURCE: https://docs.recall.ai/docs/google-meet.md
Fetched 2026-11-07 with curl -sL -A 'Mozilla/5.0'

---
updatedAt: 2026-04-17T04:41:51.000Z
agentTools:
  projectIndex: https://docs.recall.ai/llms.txt
---

# Google Meet Overview

See how Google Meet bots join, when authentication matters, and which Meet features aren’t supported so you can avoid failed joins.

## Setup

> ✅ No setup required
>
> Recall supports Google Meet bots out-of-the-box, no setup or configuration needed.

## Joining Behavior

By default, Google Meet bots need to be **manually admitted** into calls and will be an "anonymous" user.

**When a bot attempts to join a Google Meet call, this dialog will be displayed to participants:**

<Image border={false} src="https://files.readme.io/65a5e7b-Screen_Shot_2023-03-18_at_7.56.55_PM.png" title="Screen Shot 2023-03-18 at 7.56.55 PM.png" />

<br />

Any signed in participant in the host's Google Workspace can let the bot in.

If you want the bot to automatically join Google Meet calls, the bot must be [Authenticated](https://docs.recall.ai/docs/google-meet-login-getting-started).

Authenticated bots are still subject to any waiting room settings enabled by the host.

## Limitations

***

### Google Meet Livestreams

A [Google Meet livestream](https://support.google.com/meet/answer/9308630?hl=en\&co=GENIE.Platform%3DDesktop) allows users to broadcast a video meeting to a large audience in a view-only format. This is particularly useful for events where interaction is not required from the viewers, such as webinars.

Google meet livestreams are currently not supported.

### Breakout Rooms

Google Meet breakout rooms are not currently supported.
