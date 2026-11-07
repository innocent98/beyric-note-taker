SOURCE: https://docs.recall.ai/docs/sub-codes.md
Fetched 2026-11-07 with curl -sL -A 'Mozilla/5.0'

---
updatedAt: 2026-10-26T22:09:44.000Z
agentTools:
  projectIndex: https://docs.recall.ai/llms.txt
---

# Bot Sub Codes

Sub codes are a field (`sub_code`) added to certain [bot status change](https://docs.recall.ai/reference/bot-status-change-events) events to provide extra context.

Here's an example of a status change object structure:

```json
{
  "code": "call_ended",
  "sub_code": "bot_kicked_from_call",
  "updated_at": "2023-04-28T08:51:37.741790Z"
}
```

Here, we can interpret this status change as follows:

* **Code:** `call_ended` - The status change event category (Call Ended)
* **Sub Code:**`bot_kicked_from_call` - The underlying reason for the status change (sub code)

## Sub Code Types

Currently there are 3 distinct sub code categories:

* [Call Ended Sub Codes](#call-ended-sub-codes)
* [Fatal Sub Codes](#fatal-sub-codes)
* [Recording Permission Denied (Zoom only)](#recording-permission-denied-sub-codes)

| Sub Code Type               | Event                         | Platforms |
| :-------------------------- | :---------------------------- | :-------- |
| Call Ended                  | `call_ended`                  | All       |
| Fatal                       | `fatal`                       | All       |
| Recording Permission Denied | `recording_permission_denied` | Zoom      |

Note: Platform specific `sub_code`is prefixed with platform name (e.g `zoom_sdk_credentials_missing`)

<Callout icon="🚧" theme="warn">
  ### We may add additional sub_codes

  You should not treat the `sub_code` as an enum, as we may add values in the future without prior notice. We will never remove values without notifying all our customers and a long depreciation period, as we consider removing values a breaking change.
</Callout>

## Recording Permission Denied Sub Codes

Since Zoom bots have a few extra requirements to meet for recording, there are more places that bots can run into trouble.

When a bot fails to record in a Zoom call, the `recording_permission_denied` webhook event will include a Zoom-specific sub code that provides more context into the reason it failed.

These `sub_code` will be added to the `recording_permission_denied` event.

<Table>
  <thead>
    <tr>
      <th>
        Sub Code
      </th>

      <th>
        Description
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td>
        `zoom_local_recording_disabled`
      </td>

      <td>
        The meeting host has their global user-level local recording setting disabled. [More info](https://docs.recall.ai/docs/common-zoom-recording-errors#the-hosts-local-recording-setting-is-disabled) The host was not presented with the recording consent popup.
      </td>
    </tr>

    <tr>
      <td>
        `zoom_local_recording_request_disabled`
      </td>

      <td>
        The meeting host has their user-level local recording setting enabled, but the has disabled the advanced recording option labelled "Hosts can give meeting participants permission to record locally". [More info](https://docs.recall.ai/docs/common-zoom-recording-errors) The host was not presented with the recording consent popup.
      </td>
    </tr>

    <tr>
      <td>
        `zoom_local_recording_request_disabled_by_host`
      </td>

      <td>
        The meeting host has disabled participants from requesting local recording permission or has denied all future requests. [More info](https://docs.recall.ai/docs/common-zoom-recording-errors#the-host-has-disabled-requesting-recording-permission-within-the-current-call) The host was not presented with the recording consent popup.
      </td>
    </tr>

    <tr>
      <td>
        `zoom_bot_in_waiting_room`
      </td>

      <td>
        The bot is in the waiting room due to which local recording cannot be requested. The host was not presented with the recording consent popup.
      </td>
    </tr>

    <tr>
      <td>
        `zoom_host_not_present`
      </td>

      <td>
        The host was not present in the meeting when the bot requested local recording permission. This only occurs in very rare cases where the bot ​does​ request permission when the host isn't present. One example is when a host leaves/disconnects right as the bot requests permissions.
      </td>
    </tr>

    <tr>
      <td>
        `zoom_local_recording_request_denied_by_host`
      </td>

      <td>
        The host denied the bot's local recording request or if the host hasn't responded to the local recording request within 60s. The host was presented with the recording consent popup.
      </td>
    </tr>

    <tr>
      <td>
        `zoom_local_recording_denied`
      </td>

      <td>
        The bot sent a recording permission request to the host, but did not receive a response and the request timed out.
      </td>
    </tr>

    <tr>
      <td>
        `zoom_local_recording_grant_not_supported`
      </td>

      <td>
        The meeting host is using Zoom Room or other Zoom client that does not support local recording permission.
      </td>
    </tr>

    <tr>
      <td>
        `zoom_sdk_key_blocked_by_host_admin`
      </td>

      <td>
        The Zoom SDK key used by the bot is blocked by the Zoom user's workspace admin. The only workaround for this is to:

        - Have the host's Zoom organization reach out to their Zoom account manager
        - Request your Zoom app's client ID be removed from the Zoom organization's block list
      </td>
    </tr>

    <tr>
      <td>
        `zoom_invalid_email`
      </td>

      <td>
        Ensure that a valid `zoom.user_email` was provided with no invisible characters
      </td>
    </tr>

    <tr>
      <td>
        `zoom_government_not_supported`
      </td>

      <td>
        Meetings hosted on ZoomGov are not supported
      </td>
    </tr>
  </tbody>
</Table>

## Call Ended Sub Codes

The `call_ended` event signifies that a bot left, or was removed from, the call.

Recall attaches a `sub_code` to these events to expose the underlying reason why the bot is no longer in the call. This can be for obvious reasons such as the host ending the call (`call_ended_by_host`), but sometimes its less obvious why a bot left a call.

Below is a list of all call ended sub codes and what they mean.

| Sub Code                                                       | Description                                                                                                                                                                                                                                                               |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `call_ended_by_host`                                           | The call has been ended by the meeting host.                                                                                                                                                                                                                              |
| `call_ended_by_platform_idle`                                  | The call has been ended by the meeting platform, because it was idle.                                                                                                                                                                                                     |
| `call_ended_by_platform_max_length`                            | The call has been ended by the meeting platform because it reached the maximum meeting length. For instance, on Zoom, the maximum length of a meeting when on a free account is [40 minutes](https://support.zoom.com/hc/en/article?id=zm_kb\&sysparm_article=KB0067966). |
| `call_ended_by_platform_waiting_room_timeout`                  | The bot could not join the call because meeting platform's maximum waiting room time exceeded. For Google Meet, this is 10 minutes. Zoom and Teams don't have a timeout.                                                                                                  |
| `timeout_exceeded_waiting_room`                                | The bot left the call because it was in the waiting room for too long. This is the timeout specified by [automatic\_leave.waiting\_room\_timeout](https://docs.recall.ai/docs/automatic-leaving-behavior).                                                                                        |
| `timeout_exceeded_noone_joined`                                | The bot left the call because nobody joined the call for too long.                                                                                                                                                                                                        |
| `timeout_exceeded_everyone_left`                               | The bot left the call because everyone else left.                                                                                                                                                                                                                         |
| `timeout_exceeded_silence_detected`                            | The bot left the call because all other participants were likely bots [based off continuous silence detection heuristic](https://docs.recall.ai/docs/bot-detection#/using-silence-detection).                                                                             |
| `timeout_exceeded_only_bots_detected_using_participant_names`  | The bot left the call because all other participants were likely bots [based off participant names heuristic](https://docs.recall.ai/docs/bot-detection#/using-participant-names).                                                                                        |
| `timeout_exceeded_only_bots_detected_using_participant_events` | The bot left the call because all other participants were likely bots [based off participant events heuristic](https://docs.recall.ai/docs/bot-detection#/using-participant-events).                                                                                      |
| `timeout_exceeded_in_call_not_recording`                       | The bot left the call because it never started recording e.g. remained in the in\_call\_not\_recording state for longer than [automatic\_leave.in\_call\_not\_recording\_timeout](https://docs.recall.ai/reference/bot_create)                                                                         |
| `timeout_exceeded_in_call_recording`                           | The bot left the call because it exceeded the timeout set in [automatic\_leave.in\_call\_recording\_timeout](https://docs.recall.ai/reference/bot_create) .                                                                                                                                            |
| `timeout_exceeded_recording_permission_denied`                 | The bot left the call because it exceed the timeout set in [recording\_permission\_denied\_timeout](https://docs.recall.ai/reference/bot_create)                                                                                                                                                       |
| `bot_kicked_from_call`                                         | The bot was removed from the call by the host.                                                                                                                                                                                                                            |
| `bot_kicked_from_waiting_room`                                 | The bot was removed from the waiting room by the host.                                                                                                                                                                                                                    |
| `bot_received_leave_call`                                      | The bot received leave call request.                                                                                                                                                                                                                                      |
| `meeting_not_started`                                          | The bot was unable to join the meeting because it hasn't started yet.                                                                                                                                                                                                     |
| `breakout_room_not_open`                                       | The bot was created with `join_specific_room` configured and the room is not open to join (or closes).                                                                                                                                                                    |

## Fatal Sub Codes

When a bot hits a fatal error, a `fatal` event is emitted with an attached `sub_code` that provides more context. Below is a list of possible fatal sub codes, their meaning, and any recommended action to take, if any.

| Sub Code                        | Message                                                                                 | Recommended action                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| ------------------------------- | --------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `bot_errored`                   | The bot ran into an unexpected error.                                                   |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `meeting_not_found`             | No meeting was found at the given link.                                                 | On Zoom this happens most often when the meeting was deleted on Zoom, but the bot was not unscheduled from the meeting. You can confirm this is the issue by trying to visit the URL through your browser.                                                                                                                                                                                                                                                                                                                                                                                                        |
| `meeting_not_accessible`        | This meeting is not accessible.                                                         | On Teams, this happens when the bot attempts to join a meeting but is not allowed to join due to meeting access settings. The organizer must change their Meeting Access settings [here](https://admin.teams.microsoft.com/company-wide-settings/external-communications).                                                                                                                                                                                                                                                                                                                                        |
| `meeting_not_started`           | The meeting has not started yet.                                                        |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `meeting_requires_registration` | The meeting requires registration.                                                      | Currently not supported for MS Teams. For Zoom, see [Registration-Required Meetings & Webinars](https://docs.recall.ai/docs/zoom-webinars#registration-required-meetings-and-webinars).                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `meeting_requires_sign_in`      | The meeting can only be joined by signed in users.                                      | Incase of Zoom bots, this error message means that the Zoom meeting has [only authenticated users can join](https://support.zoom.us/hc/en-us/articles/360060549492-Allowing-only-authenticated-users-in-meetings) enabled. By default, the bot is unauthenticated, so it cannot join these calls. To bypass this error, follow the steps in [Joining "Sign In Required" Zoom Meetings](https://docs.recall.ai/docs/zoom-signed-in-bots). For MS Teams, see [Signed-In Microsoft Teams Bots](https://docs.recall.ai/docs/setting-up-signed-in-bots-for-microsoft-teams). For Google Meet, see [Signed-In Google Meet Bots](https://docs.recall.ai/docs/google-meet-login-getting-started). |
| `meeting_link_expired`          | The meeting link has expired.                                                           |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `meeting_link_invalid`          | The meeting does not exist or the link is invalid.                                      |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `meeting_password_incorrect`    | The meeting password is incorrect.                                                      |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `meeting_locked`                | The meeting is locked.                                                                  |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `meeting_full`                  | The meeting is full.                                                                    |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `meeting_ended`                 | The bot attempted to join a meeting that has already ended and can no longer be joined. |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `failed_to_launch_in_time`      | The bot took too long to launch due to an error on Recall's infrastructure.             | Re-run the [Create Bot](https://docs.recall.ai/reference/bot_create) endpoint to launch another bot for your meeting.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |

<br />

### Zoom Fatal Sub Codes

<Table align={["left","left","left"]}>
  <thead>
    <tr>
      <th>
        Sub Code
      </th>

      <th>
        Message
      </th>

      <th>
        Recommended action
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td>
        `zoom_sdk_credentials_missing`
      </td>

      <td>
        The bot was not able to join because Zoom SDK credentials were not configured.
      </td>

      <td>

      </td>
    </tr>

    <tr>
      <td>
        `zoom_sdk_update_required`
      </td>

      <td>
        A newer version of the Zoom SDK is required to join this meeting.
      </td>

      <td>

      </td>
    </tr>

    <tr>
      <td>
        `zoom_sdk_app_not_published`
      </td>

      <td>
        The SDK credentials configured in Recall dashboard have not been approved by Zoom. Bots using unapproved Zoom credentials can only join meetings hosted in the workspace of the user that created the credentials.
      </td>

      <td>
        In order for your bot to join calls outside of your Zoom workspace, you must submit your Zoom app for approval.
      </td>
    </tr>

    <tr>
      <td>
        `zoom_email_blocked_by_admin`
      </td>

      <td>
        The Zoom account this bot is joining from has been disallowed to join this meeting by the Zoom workspace administrator.
      </td>

      <td>

      </td>
    </tr>

    <tr>
      <td>
        `zoom_registration_required`
      </td>

      <td>
        The bot failed to join because registration is required for this Zoom meeting.
      </td>

      <td>
        Ensure that bot is joining from an allowed webinar URL. Read more about [how to send Zoom bots to webinars](https://docs.recall.ai/reference/zoom-webinars).
      </td>
    </tr>

    <tr>
      <td>
        `zoom_captcha_required`
      </td>

      <td>
        The bot failed to join because captcha is required for this Zoom meeting.
      </td>

      <td>

      </td>
    </tr>

    <tr>
      <td>
        `zoom_account_blocked`
      </td>

      <td>
        Someone previously removed the signed-in Zoom bot and selected the "Once removed, `BOT_NAME` will not be able to rejoin the meeting." option. This prevents the bot from rejoining the meeting for the rest of the session
      </td>

      <td>
        There are two options:

        - Have the bot sign-in with a different Zoom user account
        - To enable the bot to re-enter the room, the participants of this meeting would need to end the meeting and then restart it
      </td>
    </tr>

    <tr>
      <td>
        `zoom_invalid_join_token`
      </td>

      <td>
        Zoom's SDK rejected the join token provided by the bot. This can happen due to an expired token, the token missing permissions (for instance, being generated for the wrong meeting), or the token being invalid.
      </td>

      <td>

      </td>
    </tr>

    <tr>
      <td>
        `zoom_token_expired`
      </td>

      <td>
        The provided token (such as a [Join Token for Local Recording](https://docs.recall.ai/docs/providing-zoom-join-tokens) or when using [Zoom Signed-in Bots](https://docs.recall.ai/docs/zoom-signed-in-bots)) was expired when the bot attempted to use it to join the Zoom meeting.
      </td>

      <td>

      </td>
    </tr>

    <tr>
      <td>
        `zoom_invalid_signature`
      </td>

      <td>
        The Zoom SDK was not able to generate a valid meeting-join signature. This could mean that your Zoom SDK credentials are invalid, or the meeting link is malformed.
      </td>

      <td>
        Follow [this guide](https://recallai.readme.io/docs/set-up-zoom#create-zoom-api-credentials) to set up Zoom credentials. Double check that your meeting link is correct.
      </td>
    </tr>

    <tr>
      <td>
        `zoom_internal_error`
      </td>

      <td>
        The bot errored due to an internal Zoom error.
      </td>

      <td>

      </td>
    </tr>

    <tr>
      <td>
        `zoom_join_timeout`
      </td>

      <td>
        The request to join the Zoom meeting timed out.
      </td>

      <td>

      </td>
    </tr>

    <tr>
      <td>
        `zoom_email_required`
      </td>

      <td>
        The bot failed to join because providing an email is required to join this Zoom meeting.
      </td>

      <td>
        Provide a zoom.user_email when creating the bot as outlined in [Email Required Meetings](https://docs.recall.ai/docs/step-2-create-your-first-zoom-bot#bot-cant-join-email-required-meetings).
      </td>
    </tr>

    <tr>
      <td>
        `zoom_sso_login_required`
      </td>

      <td>
        The bot failed to join because the meeting requires members to log in via Single Sign-On.
      </td>

      <td>
        The host's Zoom meeting only admits people who sign in through the host organization's own Single Sign-On. The bot cannot complete that login, including when it joins as a signed-in user. The host or their Zoom admin has to relax the SSO requirement for the meeting.
      </td>
    </tr>

    <tr>
      <td>
        `zoom_web_disallowed`
      </td>

      <td>
        The Zoom meeting host has disallowed joining from the web which prevents the bot from joining the meeting.
      </td>

      <td>
        Have the host disable E2E encryption for the meeting. Settings to do this are [here](https://support.zoom.com/hc/en/article?id=zm_kb\&sysparm_article=KB0075502#:~:text=Show%20less-,How%20to%20enable%20or%20disable%20end%2Dto%2Dend%20encryption%20for%20meetings,-By%20default%2C%20Zoom)
      </td>
    </tr>

    <tr>
      <td>
        `zoom_connection_failed`
      </td>

      <td>
        The bot failed to join the meeting due to a internal server error on Zoom's end. Although we retry the connection, Zoom consistently fails to connect, disallowing the bot from joining
      </td>

      <td>

      </td>
    </tr>

    <tr>
      <td>
        `zoom_error_multiple_device_join`
      </td>

      <td>
        The bot failed to join the meeting due to another participant with the same credentials having joined the meeting. This can happen when a bot is sent to a [Registration-Required Zoom Meetings](https://docs.recall.ai/docs/zoom-webinars), with a meeting URL whose `tk` parameter has already been used by a participant in the Zoom meeting.
      </td>

      <td>

      </td>
    </tr>

    <tr>
      <td>
        `zoom_meeting_not_accessible`
      </td>

      <td>
        The Zoom meeting was not accessible for the bot.
      </td>

      <td>
        This is due to the Zoom host disallowing participants from joining from specific countries. Outside of the supported [Regions](https://docs.recall.ai/docs/regions) , you can't create bots in specific countries at this time. The host can enable bots to join by disabling this setting as shown [here](https://support.zoom.com/hc/en/article?id=zm_kb\&sysparm_article=KB0064685)
      </td>
    </tr>

    <tr>
      <td>
        `zoom_meeting_host_inactive`
      </td>

      <td>
        The request to join the Zoom meeting failed, as the meeting host has been disabled, deleted, or restricted. You will not be able to join this meeting.
      </td>

      <td>

      </td>
    </tr>

    <tr>
      <td>
        `zoom_invalid_webinar_invite`
      </td>

      <td>
        The invite to the Zoom webinar was invalid. Check the password and any tokens passed in.
      </td>

      <td>

      </td>
    </tr>

    <tr>
      <td>
        `zoom_another_meeting_in_progress`
      </td>

      <td>
        This host has another meeting in progress. Once the host ends the previous meeting and starts the new one, the bot will be able to join.
      </td>

      <td>

      </td>
    </tr>

    <tr>
      <td>
        `zoom_obf_user_not_in_meeting`
      </td>

      <td>
        The Zoom user associated with the OBF token provided is not in the meeting.
      </td>

      <td>

      </td>
    </tr>

    <tr>
      <td>
        `zoom_anonymous_join_not_allowed`
      </td>

      <td>
        The bot attempted to join a Zoom meeting using SDK credentials, having the bot sign-in
      </td>

      <td>
        Follow our [guide](https://docs.recall.ai/docs/zoom-signed-in-bots) to configure your bots to sign-in.
      </td>
    </tr>

    <tr>
      <td>
        `zoom_obf_or_zak_missing_or_invalid`
      </td>

      <td>
        The OBF token that's passed to the bot is invalid or the bot isn't signed-in. Optionally provide a valid OBF to the bot when it joins the call.
      </td>

      <td>

      </td>
    </tr>

    <tr>
      <td>
        `zoom_voice_recording_not_supported`
      </td>

      <td>
        This bot attempted to join a Zoom meeting ID that corresponds to a Zoom Voice Recorder Summary, which can't be joined. See <Anchor target="_blank" href="https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0080794">this page</Anchor> for more information.
      </td>

      <td>

      </td>
    </tr>

    <tr>
      <td>
        `zoom_bot_blocked`
      </td>

      <td>
        The bot was disallowed from joining the meeting.
      </td>

      <td>

      </td>
    </tr>

    <tr>
      <td>
        `zoom_authorized_participants_only`
      </td>

      <td>
        This Zoom meeting only allows authorized participants. The bot joined using a signed-in Zoom account, but that account is not authorized for this meeting.
      </td>

      <td>
        Ask the host to authorize your signed-in Zoom account to join their meetings.
      </td>
    </tr>

    <tr>
      <td>
        `missing_webinar_license`
      </td>

      <td>
        The host of this zoom webinar is either missing a webinar license, or has a webinar license that is no longer valid
      </td>

      <td>

      </td>
    </tr>
  </tbody>
</Table>

<br />

### Google Meet Fatal Sub Codes

| Sub Code                                            | Message                                                                                                                                                                                | Recommended action                                                                                                                                        |
| :-------------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `google_meet_internal_error`                        | The bot errored due to a Google Meet internal issue.                                                                                                                                   |                                                                                                                                                           |
| `google_meet_sign_in_failed`                        | The bot was not able to sign in to google.                                                                                                                                             |                                                                                                                                                           |
| `google_meet_sign_in_captcha_failed`                | The bot was not able to sign in to google because of captcha.                                                                                                                          |                                                                                                                                                           |
| `google_meet_bot_blocked`                           | The bot was disallowed from joining the meeting.                                                                                                                                       | Review [Google Meet: FAQ](https://docs.recall.ai/docs/google-meet-faq#why-did-my-bot-fail-to-join-a-meeting-with-google_meet_bot_blocked-status-code) for common causes.          |
| `google_meet_sso_sign_in_failed`                    | The bot was not able to sign in to google with SSO.                                                                                                                                    |                                                                                                                                                           |
| `google_meet_sign_in_missing_login_credentials`     | The bot was not able to sign in to google because login credentials were not configured.                                                                                               | Create an [Authenticated Google Meet Bot](https://docs.recall.ai/docs/google-meet-login-getting-started) to allow your bots to join sign-in-only Google Meet meetings.            |
| `google_meet_sign_in_missing_recovery_credentials`  | The bot was not able to sign in to google because recovery credentials were not configured.                                                                                            |                                                                                                                                                           |
| `google_meet_sso_sign_in_missing_login_credentials` | The bot was not able to sign in to google with SSO because login credentials were not configured.                                                                                      |                                                                                                                                                           |
| `google_meet_sso_sign_in_missing_totp_secret`       | The bot was not able to sign in in to google with SSO because TOTP secret was missing from password.                                                                                   |                                                                                                                                                           |
| `google_meet_video_error`                           | The bot was not able to join the call due to Google Meet video error.                                                                                                                  |                                                                                                                                                           |
| `google_meet_meeting_room_not_ready`                | The bot was not able to join the call as the meeting room was not ready.                                                                                                               |                                                                                                                                                           |
| `google_meet_login_not_available`                   | There were not enough available logins (Google accounts) in the supplied `google_login_group_id` for the bot to use.                                                                   | Create additional Google logins as outlined [here](https://recallai.readme.io/docs/google-meet-login-getting-started#5-adding-more-logins).               |
| `google_meet_permission_denied_breakout`            | The bot tried to join an active Google Meet breakout room and was rejected.                                                                                                            | N/A                                                                                                                                                       |
| `google_meet_knocking_disabled`                     | The bot was unable to ask to join the meeting due to the settings of the host.                                                                                                         | Change meeting settings as mentioned [here](https://docs.recall.ai/docs/google-meet-faq#why-is-my-bot-failing-to-join-a-call-with-the-google_meet_knocking_disabled-status-code). |
| `google_meet_watermark_kicked`                      | The <Anchor target="_blank" href="https://support.google.com/meet/answer/15481228?hl=en">watermark feature</Anchor> in google meet was enabled mid-call and the bot was not signed in. | Create an [Authenticated Google Meet Bot](https://docs.recall.ai/docs/google-meet-login-getting-started)  and configure it to `always` sign-in.                                   |
| `google_meet_organisation_restricted`               | The bot was unable to join because the call is restricted to members of the host organization.                                                                                         | Change [Meet Safety Settings](https://support.google.com/a/answer/13722774) to allow users outside of the organization to join Google Meet calls.         |
| `sso_login_account_disabled`                        | The Google account used for SSO has been disabled.                                                                                                                                     | Re-enable the account or update your Google Meet SSO credentials.                                                                                         |
| `sso_login_invalid_recipient`                       | Invalid SSO workspace domain. Update your Google Meet SSO credentials.                                                                                                                 |                                                                                                                                                           |
| `sso_not_configured`                                | Your domain is not configured to use Single Sign On.                                                                                                                                   |                                                                                                                                                           |

<br />

### Teams Fatal Sub Codes

| Sub Code                                         | Message                                                                                                                                                                          | Recommended action                                                                                                                                                                                                                                                                                                                                                                                                                |
| :----------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `microsoft_teams_sign_in_credentials_missing`    | The bot failed to join a Microsoft Teams meeting requiring all participants to be signed-in. The bot was not signed in and thus was not able to join the call.                   |                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `microsoft_teams_call_dropped`                   | Microsoft Teams disconnected and the bot was unable to reconnect. This usually happens due to a transient server error from the Teams server.                                    |                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `microsoft_teams_sign_in_failed`                 | The bot failed to join a Microsoft Teams meeting requiring all participants to be signed-in. The bot was not signed in or failed to sign in to its configured Microsoft account. | If Teams credentials are not set up, follow the [Signed-In Teams Bot Setup](https://docs.recall.ai/docs/setting-up-signed-in-bots-for-microsoft-teams) guide.                                                                                                                                                                                                                                                                                             |
| `microsoft_teams_internal_error`                 | The bot errored due to a Microsoft Teams server error.                                                                                                                           |                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `microsoft_teams_captcha_error`                  | The bot failed to join due to captcha being enabled for anonymous participants.                                                                                                  | Use [Signed-In Microsoft Teams Bots](https://docs.recall.ai/docs/setting-up-signed-in-bots-for-microsoft-teams) to bypass this. The tenant must also have the domain of the account used to sign-in whitelisted as a [trusted organization](https://learn.microsoft.com/en-us/microsoftteams/trusted-organizations-external-meetings-chat?tabs=organization-settings#specify-trusted-microsoft-365-organizations) if required by their security settings. |
| `microsoft_teams_bot_not_invited`                | The bot failed to join a Microsoft Teams meeting as it was not the account that was invited.                                                                                     | Follow this [Sign in with a different account to join this meeting](https://learn.microsoft.com/en-us/microsoftteams/troubleshoot/meetings/external-participants-join-meeting-blocked#error-2-sign-in-with-a-different-account-to-join-this-meeting) guide to allow access to the bot.                                                                                                                                            |
| `microsoft_teams_breakout_room_unsupported`      | The bot was moved to a Microsoft Teams breakout room, but they are not supported by Recall.                                                                                      |                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `microsoft_teams_townhall_meeting_not_supported` | The bot was added to a Microsoft Teams townhall meeting, but this type of meeting is not supported by Recall.                                                                    |                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `microsoft_teams_event_not_started_for_external` | The bot failed to join a Microsoft Teams meeting that prevents external participants from joining before the event begins.                                                       |                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `microsoft_teams_2fa_required`                   |                                                                                                                                                                                  | Your signed-in Teams bot has 2FA configured. Disable this by [following this guide](https://docs.recall.ai/docs/setting-up-signed-in-bots-for-microsoft-teams#step-21-disabling-security-defaults) to resolve the error.                                                                                                                                                                                                          |
| `microsoft_teams_captions_failure`               | The host had meeting captions disabled so the bot wasn't able generate them.                                                                                                     | [Fallback to an AI transcription provider](https://docs.recall.ai/docs/meeting-caption-transcription#/why-did-my-meeting-not-produce-any-transcript-via-meeting-captions-when-the-video-includes-people-talking) for async or real-time transcription                                                                                                                                                                             |

<br />

### Webex Fatal Sub Codes

| Sub Code                                        | Message                                                                                                               | Recommended action                                                                                                                                                                |
| :---------------------------------------------- | :-------------------------------------------------------------------------------------------------------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `webex_join_meeting_error`                      | The bot failed to join a Webex meeting because the meeting was invalid, or Webex credentials are not set up properly. | If Webex credentials are not set up, follow the <Anchor target="_blank" href="doc:webex-bot-setup-service-app">Webex Bot Setup</Anchor> guide.                                    |
| `webex_pin_required`                            | The bot failed to join a Webex meeting because the meeting required a pin.                                            |                                                                                                                                                                                   |
| `webex_password_required`                       | The bot failed to join a Webex meeting because the meeting required a password.                                       |                                                                                                                                                                                   |
| `webex_user_has_excessive_device_registrations` | There are too many bots currently in use. Cisco sets a limit of 100 currently active meeting registrations            | You can reach out to Cisco to request an increase in the limit for your account, or you can use multiple workspaces with different Webex credentials and load balance across them |
| `webex_service_app_unauthorized`                | The service app is no longer authorized with Recall.ai                                                                | In the Recall.ai dashboard, re-authorize the Webex Service app                                                                                                                    |

<br />

### Zoom RTMS

| Sub Code                             | Message                                              | Recommended action                                                                                                                                                                                                                          |
| :----------------------------------- | :--------------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `rtms_signature_invalid`             | Zoom did not accept the provided signature           | Compare your signature calculation code to our <Anchor target="_blank" href="https://github.com/recallai/meeting-direct-connect/blob/de67fce47c4f5c0cc42cd950ff00f5835eb0f8af/src/index.ts#L119">Meeting Direct Connect sample app</Anchor> |
| `zoom_rtms_duplicate_signal_request` | There is already a connection for this RTMS stream   | Add an idempotency key on the `meeting_uuid` + the `rtms_stream_id` so that redundant `meeting.rtms_started` only create one connection                                                                                                     |
| `zoom_rtms_invalid_stream_id`        | The RTMS stream id was invalid                       | Ensure that the correct `rtms_stream_id`was provided                                                                                                                                                                                        |
| `zoom_rtms_internal_exception`       | There was an issue upstream from Zoom's RTMS service |                                                                                                                                                                                                                                             |

<br />

### Meet Media API

| Sub Code                           | Message | Recommended action                                                                   |
| ---------------------------------- | ------- | ------------------------------------------------------------------------------------ |
| `meet_media_space_not_found`       |         | Validate that you provided the correct 12 character space name (e.g. `xxx-xxxx-xxx`) |
| `meet_media_unauthenticated`       |         | Verify that OAuth token is valid an unexpired                                        |
| `meet_media_failed_precondition`   |         | The user is no longer in the meeting                                                 |
| `meet_media_sdp_negotiation_error` |         |                                                                                      |
| `meet_media_internal_error`        |         |                                                                                      |
