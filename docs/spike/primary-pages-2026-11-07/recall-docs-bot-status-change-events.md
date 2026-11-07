SOURCE: https://docs.recall.ai/docs/bot-status-change-events.md
Fetched 2026-11-07 with curl -sL -A 'Mozilla/5.0'

---
updatedAt: 2026-09-01T17:22:51.000Z
agentTools:
  projectIndex: https://docs.recall.ai/llms.txt
---

# Bot Webhooks

See which bot status webhooks Recall sends so your application can react when bots join, record, finish, or fail.

Recall uses bot status changes to capture the lifecycle of a bot.

These status changes are exposed through webhooks, which your application can use to create a real-time experience or react to asynchronous events outside of the API request cycle.

For example, you may want to update something on your end when a bot transitions its status from `joining_call` to `in_call_recording`. When these asynchronous events happen, we'll make a POST request to the address you give us and you can do whatever you need with it on your end.

After a webhook is configured for an environment, notifications will be sent for all events for that environment.

<Callout icon="🚧" theme="warn">
  ### Important webhook handler considerations

  - `2xx`**response:** Your webhook handler should return a HTTP `2xx` Code
  - **Retries:** If Recall doesn't receive a `2xx` response from your server, we will continue to try the message for the next 24 hours, with an increasing delay between attempts.
  - **Timeouts:** Webhook events have a timeout of 15 seconds. If you plan to kick off longer running tasks upon receiving certain events, make sure to do this asynchronously so you respond to requests before they time out.
</Callout>

## Events

This webhook is sent whenever the bot's status is changed and is delivered via Svix to the endpoints configured in your Recall webhooks dashboard.

<Callout icon="📘" theme="info">
  ### View the webhooks dashboard

  - <Anchor target="_blank" href="https://us-east-1.recall.ai/dashboard/webhooks/">(US) us-east-1</Anchor>
  - <Anchor target="_blank" href="https://us-west-2.recall.ai/dashboard/webhooks/">(US) us-west-2</Anchor>
  - <Anchor target="_blank" href="https://eu-central-1.recall.ai/dashboard/webhooks/">(EU) eu-central-1</Anchor>
  - <Anchor target="_blank" href="https://ap-northeast-1.recall.ai/dashboard/webhooks/">(JP) ap-northeast-1</Anchor>
</Callout>

Below is the JSON schema you can expect to receive for any bot status change webhooks:

```json
{
  "event": string,
  // bot.joining_call, bot.in_waiting_room, bot.in_call_not_recording,
  // bot.recording_permission_allowed, bot.recording_permission_denied,
  // bot.in_call_recording, bot.call_ended, bot.done, bot.fatal
  "data": {
    "data": {
      "code": string,
      "sub_code": string | null,
      "updated_at": string
    },
    "bot": {
      "id": string,
      "metadata": object
    }
  }
}
```

<Table>
  <thead>
    <tr>
      <th>
        Event
      </th>

      <th>
        Description
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td>
        `bot.joining_call`
      </td>

      <td>
        The bot has acknowledged the request to join the call, and is in the process of connecting. This status can be emitted more than once for the same bot if a join attempt is retried (for example after a transient provider failure). Prefer `in_call_not_recording` or `in_call_recording` if you need a signal that the bot has entered the meeting.
      </td>
    </tr>

    <tr>
      <td>
        `bot.in_waiting_room`
      </td>

      <td>
        The bot is in the waiting room of the meeting.
      </td>
    </tr>

    <tr>
      <td>
        `bot.in_call_not_recording`
      </td>

      <td>
        The bot has joined the meeting, however is not recording yet. This could be because the bot is still setting up, does not have recording permissions, or the recording was paused.
      </td>
    </tr>

    <tr>
      <td>
        `bot.recording_permission_allowed`
      </td>

      <td>
        The bot has joined the meeting and it's request to record the meeting has been allowed by the host.
      </td>
    </tr>

    <tr>
      <td>
        `bot.recording_permission_denied`
      </td>

      <td>
        The bot has joined the meeting and it's request to record the meeting has been denied. Refer to the `data.sub_code` for the exact reason.
      </td>
    </tr>

    <tr>
      <td>
        `bot.in_call_recording`
      </td>

      <td>
        The bot is in the meeting, and is currently recording the audio and video.
      </td>
    </tr>

    <tr>
      <td>
        `bot.call_ended`
      </td>

      <td>
        The bot has left the call. The `data.sub_code` will contain machine readable code for why the call ended. <br /><br />Note: [Recordings Media](https://docs.recall.ai/docs/recordings-and-media) may not be available at this point. You should wait for the `bot.done` event to confirm that the media is available to download
      </td>
    </tr>

    <tr>
      <td>
        `bot.done`
      </td>

      <td>
        The bot has shut down. If bot produced `in_call_recording` event, the video is uploaded and available for download.
      </td>
    </tr>

    <tr>
      <td>
        `bot.fatal`
      </td>

      <td>
        The bot has encountered an error that caused it to shut down. The `data.sub_code` will contain machine readable code for why bot failed.

        A `fatal` event typically occurs when the bot is unable to join a call, though it can sometimes happen during a call, for example, if Microsoft Teams has a transient server issue and disconnects. In the latter case, the media will be recovered up until the point of the `fatal`, and the bot will still emit a `done` event.
      </td>
    </tr>

    <tr>
      <td>
        `bot.breakout_room_entered`
      </td>

      <td>
        The bot has entered a breakout room.
      </td>
    </tr>

    <tr>
      <td>
        `bot.breakout_room_left`
      </td>

      <td>
        The bot has left a breakout room.
      </td>
    </tr>

    <tr>
      <td>
        `bot.breakout_room_opened`
      </td>

      <td>
        A breakout room has been opened.
      </td>
    </tr>

    <tr>
      <td>
        `bot.breakout_room_closed`
      </td>

      <td>
        A breakout room has been closed.
      </td>
    </tr>
  </tbody>
</Table>

<Callout icon="🚧" theme="warn">
  ### We may add additional Status Change event codes

  You should not treat the `data.code` and `data.sub_code` as an enum, as we may add values in the future without prior notice. We will never remove values without notifying all our customers and a long depreciation period, as we consider removing values a breaking change.
</Callout>

The list of `sub_code` & corresponding descriptions can be found here [here](https://docs.recall.ai/docs/sub-codes#fatal-sub-codes).

# FAQ

***

## Why is my webhook endpoint disabled?

If all attempts to a specific endpoint fail for a period of 5 days, the endpoint will be disabled. The clock only starts after multiple deliveries failed within a 24 hour span, with at least 12 hours difference between the first and the last failure.

## Can I be alerted when webhook delivery is failing for my endpoint?

Yes! From the Recall dashboard, you can subscribe to email notifications that will inform you if webhook delivery is consistently failing for your endpoints.

![](https://files.readme.io/518e90e7e3cd1a25fbf93595e5ce75d8ec0b430dcc412439274ab3163a28ca29-CleanShot_2025-11-17_at_17.51.21.png)

<br />

## Where can I see bot status change webhooks sent for a bot?

You can see all bot status change webhooks sent from the dashboard on the bot page in the bot explorer. Once on the bot page, you can click the "View Webhooks" button and you'll be brought to the webhooks page with the list of bot status change webhook events for this bot

![](https://files.readme.io/91dea679496e21e137f49f6c95448f3ad0dd6550574e4503c10aed0bbf789ab4-CleanShot_2025-10-15_at_15.10.572x.png)
