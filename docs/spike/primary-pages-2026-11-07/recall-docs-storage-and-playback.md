SOURCE: https://docs.recall.ai/docs/storage-and-playback.md
Fetched 2026-11-07 with curl -sL -A 'Mozilla/5.0'

---
updatedAt: 2026-11-02T04:51:16.000Z
agentTools:
  projectIndex: https://docs.recall.ai/llms.txt
---

# Storage and Data Retention

Configure how long Recall stores recording media, understand storage charges, and delete recordings when your application no longer needs them.

Recall.ai can [store recordings and their associated media](https://docs.recall.ai/docs/recordings-and-media) so your application can retrieve them after a meeting. You control how long that media is retained: configure automatic expiration, keep it until you explicitly delete it, or disable recording-media storage altogether with Zero Data Retention.

# Setting a data retention policy

Set the `retention` field in the recording configuration when creating a recording (e.g., `recording_config.retention`). For example, you can provide the recording configuration when [creating a bot](https://docs.recall.ai/reference/bot_create), [creating a Desktop SDK upload](https://docs.recall.ai/reference/sdk_upload_create), or [creating a Meeting Direct Connect session](https://docs.recall.ai/reference/meeting_direct_connect_create), or when calling [Start Recording](https://docs.recall.ai/reference/bot_start_recording_create).

| Policy              | Configuration                     | Behavior                                                                                                       |
| ------------------- | --------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| Timed               | `{"type": "timed", "hours": 168}` | Automatically deletes recording media after the configured duration.                                           |
| Forever             | `{"type": "forever"}`             | Retains recording media until you explicitly delete it.                                                        |
| Zero Data Retention | `null`                            | Disables recording-media storage. Your application receives meeting data through configured real-time streams. |

For example, retain recording media for seven days:

```json
{
  "recording_config": {
    "retention": {
      "type": "timed",
      "hours": 168
    }
  }
}
```

Start Recording accepts the recording configuration directly, so use `retention` at the top level of that request.

For timed retention, specify the duration in hours. The retention period begins when the recording transitions to `done`; check `expires_at` on the recording for its expiration timestamp.

<Callout icon="📘" theme="info">
  ## Default retention

  If you omit `recording_config.retention`, the default depends on when your account was created:

  - **Before June 12, 2025:** Recording media expires after seven days.
  - **After June 12, 2025:** Recording media is retained indefinitely.

  Configure retention explicitly in your application to ensure consistent behavior.
</Callout>

## Configuring retention for meeting bots

For meeting bots, where you configure retention depends on how the recording starts:

* **Automatic recording:** Set retention in the bot’s recording configuration. To change the configuration of a scheduled bot before it joins, use [Update Scheduled Bot](https://docs.recall.ai/reference/bot_partial_update). For bots scheduled through Calendar V2, use [Schedule Bot For Calendar Event](https://docs.recall.ai/reference/calendar_events_bot_create).
* **Manually started recording:** Set `retention` on each [Start Recording](https://docs.recall.ai/reference/bot_start_recording_create) request. This endpoint accepts the recording configuration directly in the request body and does not inherit it from a previous recording.

Changing your application’s configuration does not delete existing stored recordings. To remove media already stored by Recall, use the deletion endpoints below.

# Expiration and deletion

Recording media is deleted automatically when its timed retention period ends. You can also delete stored media manually through the API.

**To be clear, when a recording expires or is deleted, all&#x20;**[**Recording Media**](https://docs.recall.ai/docs/recordings-and-media)**&#x20;is permanently removed from our servers, including backups**. Deleting or expiring a recording does not remove data stored by third-party transcription providers; however you can check with your transcription provider on what your retention policy is.

When the recording expires and/or is deleted, Recall sends a `recording.deleted` [Status Change Webhook](https://docs.recall.ai/docs/status-change-webhooks-setup-verification).

<Callout icon="⚠️" theme="warn">
  ### **Deletion is permanent**

  Expired or deleted recording media cannot be recovered. Recall does not retain backups or archives of deleted recording media. Download any outputs your application needs before deleting them or allowing them to expire.
</Callout>

## Automatic recording expiration

Retrieve the [Recording](https://docs.recall.ai/reference/recording_retrieve) and inspect `expires_at` to see when its media will expire:

```json
{
  "id": "a5437136-4b69-429a-9e0c-cd388fd8fee6",
  "expires_at": "2026-10-07T12:00:00Z"
}
```

If `expires_at` is `null`, no expiration timestamp is currently set so the recording will never expire. This does not mean Zero Data Retention is enabled; check the recording’s retention configuration.

<Callout icon="📘" theme="info">
  **Download URLs are temporary access links and expire separately.** If a download URL expires while the media is still stored, retrieve the recording again to obtain a fresh URL. A new URL cannot restore deleted media. See [Video Playback](https://docs.recall.ai/docs/video-playback#handling-url-expiration) for signed-URL handling.
</Callout>

## Manual recording deletion

Use [Delete Recording](https://docs.recall.ai/reference/recording_destroy) to delete a recording and its associated [Recording Media](https://docs.recall.ai/docs/recordings-and-media) (which includes backups), or [Delete Bot Media](https://docs.recall.ai/reference/bot_delete_media_create) to delete all recording media stored for a bot. Expiration and manual deletion remove recording media.

## Zero Data Retention

To disable recording-media storage from the start, use Zero Data Retention. See [Zero Data Retention](https://docs.recall.ai/docs/zero-data-retention) for setup, supported transcription configurations, delivery limitations, and the distinction between Zero Data Retention and temporary storage followed by deletion.

# Data retention scope

## Recording Media

Recording retention applies to the media produced by a recording, including audio, video, transcripts, participant events, speaker timelines, and captured meeting metadata. See [Recordings and Media](https://docs.recall.ai/docs/recordings-and-media) for the available media types.

Custom metadata and bot meeting URLs are outside the scope of recording-media retention. See [Data outside recording retention](#data-outside-recording-retention).

## Data outside recording retention

Recording retention controls stored recording media. Other data follows separate policies:

| Data                | Retention and deletion behavior                                                                                                                                                                                                                                   |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Custom bot metadata | Not deleted when recording media expires or is deleted.                                                                                                                                                                                                           |
| Bot meeting URL     | Not deleted when recording media expires or is deleted.                                                                                                                                                                                                           |
| Calendar events     | Retained up to 60 days in the past for Calendar V1 and V2, then automatically removed. Recording-media expiration or deletion does not change this policy. See [Calendar Integration FAQ](https://docs.recall.ai/docs/calendar-integration-faq#how-long-does-recall-store-calendar-data). |

Because these separately retained records contain no recording media or captured participant or meeting content, they cannot be used to recover expired or deleted recording media.

# Pricing

The first 168 hours (seven days) of recording-media storage are free. After that, storage is billed at **$0.000069 USD per&#x20;**`hour_hour`.

One `hour_hour` is one hour of recording media stored for one billable hour. Usage depends on both the recording’s duration and how long its media remains stored.

Storage is billed for recording media as a whole, at the same rate regardless of which media types you configure. A one-hour transcript-only recording incurs the same storage charge as a one-hour recording containing audio, video, and transcripts. Media types are not billed separately or added together.

```text
Billable storage-hours = max(total storage-hours − 168, 0)
Usage (hour_hour) = recording duration in hours × billable storage-hours
Storage cost = usage (hour_hour) × $0.000069
```

## Examples

Calculate storage charges in three steps:

1. Subtract the 168 free storage-hours from the total storage time, with a minimum of zero billable hours.
2. Multiply the billable storage-hours by the recording duration in hours to get `hour_hour` usage.
3. Multiply that usage by $0.000069 to calculate the storage cost.

| Recording duration     | Total storage-hours | Billable storage-hours  | Usage (`hour_hour`) | Cost calculation             |
| ---------------------- | ------------------- | ----------------------- | ------------------- | ---------------------------- |
| 1 hour                 | 144                 | `max(144 − 168, 0) = 0` | `1 × 0 = 0`         | `0 × $0.000069 = $0`         |
| 1 hour                 | 169                 | `169 − 168 = 1`         | `1 × 1 = 1`         | `1 × $0.000069 = $0.000069`  |
| 30 minutes (0.5 hours) | 170                 | `170 − 168 = 2`         | `0.5 × 2 = 1`       | `1 × $0.000069 = $0.000069`  |
| 2 hours                | 192                 | `192 − 168 = 24`        | `2 × 24 = 48`       | `48 × $0.000069 = $0.003312` |

For example, once a two-hour recording has passed the free storage period, each additional hour it remains stored adds **2&#x20;**`hour_hour` of usage.

## Understand dashboard usage

The dashboard reports storage usage in `hour_hour`. Multiply the usage shown for a reporting period by $0.000069 to calculate its storage charge.

For example, **48&#x20;**`hour_hour` reported for a day costs `48 × $0.000069 = $0.003312`. The day groups the usage for reporting; usage accrues from the number of recording-hours stored during each billable storage-hour.

A recording accrues no billable storage usage during its first 168 storage-hours and stops accruing usage after its media is deleted.

## Stop ongoing storage charges

Delete existing stored media that your application no longer needs, and configure timed retention for future recordings.

To remove existing recordings through the API:

1. Call [List Recordings](https://docs.recall.ai/reference/recording_list). Use `created_at_after` and `created_at_before` to filter by creation time if needed, and follow the `next` URL until there are no more pages.
2. Identify the recordings your application no longer needs and download any media you want to keep.
3. Call [Delete Recording](https://docs.recall.ai/reference/recording_destroy) for each recording ID. Deletion permanently removes the recording and its associated media, stopping further storage charges for that media.

For billing questions, contact [`support@recall.ai`](mailto:support@recall.ai)​.

# Store recording data in your own systems

Recall does not write finished recordings directly into your storage bucket. To retain finished recordings in your own storage:

1. Configure normal recording retention.
2. Wait for the outputs your application needs to finish processing.
3. Download those outputs and upload them to your storage.
4. Verify that your copies are available.
5. Delete the recording media from Recall.

If your application needs real-time data without recording-media storage on Recall, use [Zero Data Retention](https://docs.recall.ai/docs/zero-data-retention) and save the incoming streams/events in your systems.

# Storage and data security

<Callout icon="far fa-circle-check" theme="success">
  ### No personal or meeting data ever leaves the region it was captured in.

  To be clear, all data related to users or all data that is captured in meetings never leaves the region it was captured in.
</Callout>

## Data residency

[Recording media](https://docs.recall.ai/docs/recordings-and-media) are processed and stored in the Recall region where they were captured. Backups containing participant data remain in that region. All personal data captured and/or stored is also limited to the region it was captured in.

For Recall.ai Transcription processing locations, see [Recall.ai Transcription regions](https://docs.recall.ai/docs/recallai-transcription#what-regions-can-i-configure-with-recallai-transcription). This is separate from where Recall stores recording media and its backups.

## Encryption

Recall.ai has several measure to ensure that data is secure:

* All data stored in Recall’s database is encrypted at rest using AWS RDS encryption (AES-256).&#x20;
* Recording media is encrypted at rest using Amazon S3 server-side encryption (AES-256).&#x20;
* Data transmitted between Recall’s servers and your application is encrypted in transit using TLS.&#x20;

These controls help protect credentials, secrets, and API keys from unauthorized access to stored data or interception in transit. For more information about Recall.ai’s security controls, visit our [security portal](https://security.recall.ai).

# FAQ

## Can I recover an expired or deleted recording?

No. Recording-media deletion is permanent. Download the outputs you need before the recording expires or is deleted.

## Why do storage charges continue after I change retention?

Changing future recording requests does not remove existing stored recordings.

Review older recordings, previously scheduled bots, other workspaces, and any request paths that still use indefinite retention.

## Does deleting recording media remove custom metadata?

No. Custom metadata has separate retention policies. See [Data outside recording retention](#data-outside-recording-retention).

## Does Recall use customer data to train AI models?

Recall.ai does not use Customer Data for the purpose of training or fine-tuning machine learning or artificial intelligence models. Customer Data is processed solely to provide the contracted Services and is not retained or repurposed for unrelated development.