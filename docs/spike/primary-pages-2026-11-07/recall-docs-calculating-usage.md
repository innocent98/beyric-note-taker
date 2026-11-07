SOURCE: https://docs.recall.ai/docs/calculating-usage.md
Fetched 2026-11-07 with curl -sL -A 'Mozilla/5.0'

---
updatedAt: 2026-10-25T16:46:34.000Z
agentTools:
  projectIndex: https://docs.recall.ai/llms.txt
---

# Tracking and Calculating Usage

Track Recall usage across bots, transcription, and DSDK recordings for billing and customer reporting.

<Callout icon="📘" theme="info">
  ### Usage by workspace

  Usage is scoped to individual workspaces. To get usage by workspace, you can call the list endpoint for the related model and sum the usage
</Callout>

<br />

## Tracking usage on the dashboard

You can get a high-level overview of your overall usage by checking the Usage page of the Recall dashboard. See below for the correct link by Recall region:

<Callout icon="📘" theme="info">
  ### View the usage dashboard

  [us-west-2](https://us-west-2.recall.ai/dashboard/billing/usage)

  [us-east-1](https://us-east-1.recall.ai/dashboard/billing/usage)

  [eu-central-1](https://eu-central-1.recall.ai/dashboard/billing/usage)

  [ap-northeast-1](https://ap-northeast-1.recall.ai/dashboard/billing/usage)
</Callout>

## <Anchor target="_blank" href="doc:bot-overview">Meeting bot</Anchor> usage

### How to get a single bot's usage

<Callout icon="🚧" theme="warn">
  ### Waiting room time is included in the bot's "recording hour" usage calculation. See below for more details.
</Callout>

A single bot's usage is considered the time the bot is "active" and is calculated from when the bot triggers the `joining_call` event until a terminal event (`done`). We do not bill for bots that terminate in the `fatal` state.

* The `joining_call` event occurs when the bot first loads the meeting URL.
* The `done` event is used when the bot leaves the call/can't record (e.g. meeting ID doesn't exist)
* The "active" time  includes time spent in `waiting_room` and/or `in_call_no_recording` states.

The bot usage is charged after the bot emits a terminal event.

<Callout icon="📘" theme="info">
  ### Bot usage per workspace

  You can use the <Anchor target="_blank" href="ref:billing_usage_retrieve">Get Usage</Anchor> endpoint to retrieve the number of bot hours used within a specific time period for a given workspace.
</Callout>

### How to track bot usage for your own customers

You may want to track usage for an individual user or specific organization. For these cases, you can track bot usage with custom metadata

#### Example

Imagine you want to view all usage from the customer with ID `1234` in October. You can:

1. <Anchor target="_blank" href="ref:bot_create">Create a bot</Anchor> that includes the `customer_id` in the `metadata` field.
2. Call the <Anchor target="_blank" href="ref:bot_list">List Bots</Anchor> endpoint and filter by the `customer_id` and date range. This will return a paginated list of bots.
3. Calculate the usage for each bot and sum up bot usage for all bots returned from the above query.

<Callout icon="📘" theme="info">
  You can use <Anchor target="_blank" href="https://github.com/recallai/sample-apps/tree/main/bot_track_and_calculate_usage">this sample app</Anchor> to see how to calculate bot usage over a time range and/or for a specific customer.
</Callout>

### Meeting bot usage rates

Meeting bot usage rates for our pay-as-you-go plan can be found on our [pricing page](https://www.recall.ai/pricing), with discounts available at <Anchor target="_blank" href="https://www.recall.ai/pricing">higher tiers</Anchor>, and are rounded to the nearest second. For example, if you have a bot that is running for 5 min and 10 seconds, your bot usage will be 5 minutes and 10 seconds.

Bots operate on dedicated machines and usage is based on the machine's runtime (not just the time the bot is recording). This means that:

* There is no additional charge for scheduling bots in advance. Since the machine isn't running until the bot becomes active, you only incur charges once the bot starts.
* There is no additional costs to use specific features - Note that some bot features are more CPU intensive so you can opt to use a larger bot instance with more CPU via bot variants
* There is no additional charges for individual API requests

#### Bot variants

Some features may require more compute (e.g. <Anchor target="_blank" href="doc:stream-media">output media</Anchor>, <Anchor target="_blank" href="doc:how-to-get-separate-videos-per-participant-realtime">real-time H264 video per participant</Anchor>) and you can choose to use more powerful machines (called variants) for smoother bot and recording experiences. Due to the inherent cost of running larger machines, the prices for some variants are higher:

| Variant      | Monthly plans                                            | Notes                                                                                                                                 |
| ------------ | -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| (default)    | [standard bot usage rate](https://www.recall.ai/pricing) | -                                                                                                                                     |
| `web_4_core` | standard bot usage rate + $0.10/hour                     | -                                                                                                                                     |
| `web_gpu`    | standard bot usage rate + $1.00/hour                     | GPU bots are currently gated behind a feature flag - reach out to `support@recall.ai` with an explanation of your use case to enable. |

You can set a custom variant by adding the following to your <Anchor target="_blank" href="ref:bot_create">Create Bot</Anchor> config:

```
{
  ...
  "variant": {
    "zoom": "web_4_core",
    "google_meet": "web_4_core",
    "microsoft_teams": "web_4_core"
  }
}
```

<Callout icon="📘" theme="info">
  ### Bot variants are availabe in all regions by default.
</Callout>

## Transcription Usage

### How to get a single recording's transcription usage

There are different ways to get a recording's transcription usage for a given recording depending on what transcription provider you're using

<Accordion title="Calculating usage for Recall.ai Transcription">
  For a given recording, calculate the difference between the `started_at` and `completed_at` timestamps.
</Accordion>

<Accordion title="Calculating usage for Third-party Transcription Providers">
  For a given recording, check the transcription provider's data directly by querying the `recording.transcript.data.provider_data_download_url` field. This contains the raw data received from the transcription provider and indicates the exact audio duration that was transcribed.
</Accordion>

<Accordion title="Calculating usage for Third-party Transcription Providers (with Perfect Diarization)">
  If using your own API key for the third party transcription provider, you can query the third party transcription provider directly to get usage data.

  If you are not using your own API key for the third party transcription provider, then it is not possible to calculate billing usage outside of what is on your invoice at this time.
</Accordion>

<Accordion title="Meeting Caption Transcription">
  There is no cost associated with meeting caption transcription.
</Accordion>

### Transcription usage rates

The transcription usage rates vary by provider, and you can supply your own API key to use the custom rates you have negotiated directly.

Recall.ai transcription rates start at $0.15 per hour on our pay-as-you-go plan, with discounts available at [higher tiers](https://www.recall.ai/pricing), and are rounded to the nearest second.

<br />

## [Desktop Recording SDK](https://docs.recall.ai/docs/desktop-sdk) usage

### How to get a single DSDK's recording usage

Usage for a Desktop SDK (DSDK) recording is calculated from the recording’s lifecycle timestamps.

DSDK is not charged unless the DSDK is actively recording. Each DSDK session produces a single recording object with `started_at` and `completed_at` fields, and the billable usage for that recording is the time difference between those two values (i.e., `completed_at` - `started_at`). This captures the total duration of the recording session from when capture began to when it fully completed.

<Callout icon="📘" theme="info">
  You can use [this sample app](https://github.com/recallai/sample-apps/tree/main/dsdk_track_and_calculate_usage) to calculate DSDK usage over a time range and/or for a specific customer.
</Callout>

<br />

## FAQs

### Why does my bot usage show more hours than my recording time?

Bot usage is calculated as the bot’s active time, not just recorded minutes. Active time is measured from when the bot enters the `joining_call` state until it reaches a terminal state (e.g., `done` or `fatal`). This means usage can include time spent in the waiting room as the bot waits to join the call and time spent in the call even if the bot isn’t recording.

If you want to reduce usage for these cases, you can:

* Decrease waiting room time: reduce the `automatic_leave.waiting_room_timeout` in your [Create Bot](https://docs.recall.ai/reference/bot_create) request config
* Decrease in call not recording time: reduce the `automatic_leave.in_call_not_recording_timeout` in your [Create Bot](https://docs.recall.ai/reference/bot_create) request config

See [Making a bot automatically leave a meeting](https://docs.recall.ai/docs/automatic-leaving-behavior) to learn more about how to reduce usage for the above cases.

### How are credits consumed?

Credits are consumed based on your actual Recall.ai usage. This typically includes:

* Running Meeting Bots or DSDK recordings (recording and processing meeting media)
* Storing recording media beyond the free retention period (billed per hour of media stored)
* Creating transcripts using Recall.ai Transcription

Each action draws down from your available credit balance. You can monitor your current balance, usage, and configure auto-top-ups or alerts directly from your billing dashboard.

### Does Recall.ai charge for individual features?

There’s no separate fee to enable individual features. Recall.ai’s services use usage-based pricing, so you pay for the resources you use.

Some compute-intensive features may require larger instances or additional compute. This would include any 4-core/GPU bots or DSDK raw media, which have higher hourly rates.&#x20;

Also note that Recall.ai transcription and storage beyond the free retention period also have their own usage-based charges. See our [pricing page](https://www.recall.ai/pricing) for details.

<br />
