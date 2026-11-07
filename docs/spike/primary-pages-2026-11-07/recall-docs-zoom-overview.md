SOURCE: https://docs.recall.ai/docs/zoom-overview.md
Fetched 2026-11-07 with curl -sL -A 'Mozilla/5.0'

---
updatedAt: 2026-11-05T02:33:57.000Z
agentTools:
  projectIndex: https://docs.recall.ai/llms.txt
---

# Zoom Overview

Learn how Zoom bots join, what Zoom meetings they support, and which features aren’t supported.

## Setup

<Callout icon="✅" theme="okay">
  ### No setup required

  Recall.ai bots support Zoom out-of-the-box, with no additional setup or configuration needed. **Recall.ai's Zoom meeting bots only require a meeting URL** to join Zoom calls.
</Callout>

## Joining Behavior

Since the bot joins as any other participant does, it's subject to the waiting room settings of the meeting:

* If the user has a waiting room configured for their meeting, the bot must be admitted from the waiting room.
* If they do not have a waiting room, they can join the meeting directly without being admitted.

## Additional meeting types & features

In addition to standard Zoom meetings, Zoom bots support a wide variety of Zoom meetings. Some of these require additional implementation or setup, and we have guides for each of these:

* <Anchor target="_blank" href="doc:zoom-signed-in-bots">Zoom Signed-in Bots</Anchor>: Join meetings where sign-in is required
* <Anchor target="_blank" href="doc:zoom-webinars">Zoom Webinars & Registration-Required Meetings</Anchor>
* <Anchor target="_blank" href="doc:zoom-breakout-rooms">Zoom Breakout Rooms</Anchor>

*For common questions about Zoom behavior as it pertains to Zoom bots, see&#x20;*[Zoom FAQ](https://docs.recall.ai/docs/zoom-faq)*.*

## Limitations

The following Zoom features are currently not supported by our Zoom bots:

|                         | Workarounds                                                                                               |
| :---------------------- | :-------------------------------------------------------------------------------------------------------- |
| OnZoom Events           | -                                                                                                         |
| Zoom Gov                | Zoom-level limitation so this isn't supported at this time (ZfG does not support public third party apps) |
| Language Interpretation | -                                                                                                         |
| VDI Client              | -                                                                                                         |
| Video SDK Sessions      | -                                                                                                         |
| Zoom Webinar Plus       | -                                                                                                         |
| Interpreter channels    | -                                                                                                         |
| Webinar Q\&A            | -                                                                                                         |
| Zoom Contact Center     | -                                                                                                         |

# FAQs

## What does a Recall.ai Zoom bot require to join a Zoom meeting?

Recall.ai's Zoom meeting bots only require a meeting URL to join a Zoom meeting.

<br />