SOURCE: https://docs.recall.ai/docs/google-meet-skip-the-waiting-room.md
Fetched 2026-11-07 with curl -sL -A 'Mozilla/5.0'

---
updatedAt: 2026-11-02T19:20:00.000Z
agentTools:
  projectIndex: https://docs.recall.ai/llms.txt
---

# Google Meet: Skip the waiting room

Configure Google Meet access, invitations, waiting-room settings, and signed-in bot identity so a bot can enter without manual admission.

# Overview

Google Meet decides whether a bot enters a meeting directly or waits for a host to admit it. The decision depends on the meeting's access type, the bot's Google identity, the Calendar invitation, the host organization's policies, and the meeting's host controls.

**Meeting access** and Google's dedicated **Waiting room** are separate controls. When the Waiting room is disabled, there are two common ways for a bot to enter without manual admission:

* Set **Meeting access** to **Open**. Anyone with the meeting link can join without knocking, including an unsigned bot.
* For **Trusted** or **Restricted** meetings, use a [signed-in Google Meet bot](https://docs.recall.ai/reference/google-meet-login-getting-started) and add its Google account to the underlying Calendar event before it joins.

<Callout icon="🚧" theme="warn">
  ### Google makes the final admission decision

  No Recall or Google Meet configuration guarantees automatic admission. Google can still apply organization policies, waiting-room settings, host restrictions, or automated risk checks. Recall cannot override Google's admission decision.
</Callout>

# Before you begin

Make sure you can edit the underlying Calendar event and the Google Meet host controls. The person attending the call is not necessarily the organizer who owns the Meet link.

If the meeting is not Open, complete the [Signed-In Bots: Google Meet](https://docs.recall.ai/reference/google-meet-login-getting-started) guide first. This page covers admission requirements only; it does not cover configuring or troubleshooting Google sign-in.

If Recall selects from multiple Google accounts, create a Google Group containing every bot account and invite the group. This avoids depending on which account Recall selects from the login group.

# Configure the meeting

### Step 1: Choose the meeting access type

In Google Calendar, open the event, select **Video call options**, then open **Host controls > Meeting access**. You can also inspect these controls from within the meeting.

Choose the access type that matches your requirements:

* **Open:** Anyone with the link can join without knocking. Recall has verified that bots can enter Open meetings without manual admission when the separate Waiting room does not hold them.
* **Trusted:** Members of the host's organization and external Google accounts invited through the Calendar event can join without knocking. Other external participants must knock.
* **Restricted:** Google accounts invited through the Calendar event can join. Other participants must knock or be invited from within the meeting by a host.

See Google's [meeting access documentation](https://support.google.com/meet/answer/9302870) for the current behavior of each access type.

<Callout icon="📘" theme="info">
  ### Check the separate Waiting room setting

  When Google's Waiting room is enabled, participants are held outside the main meeting even when they are on the Calendar invite. If the bot must enter before a host or co-host joins, turn the Waiting room off. Google's **Bring in automatically** option only moves waiting participants into the meeting when a host joins. See [Use waiting rooms in Google Meet](https://support.google.com/meet/answer/16523457).
</Callout>

<Callout icon="🛑" theme="error">
  ### External bots cannot bypass organization-only policies

  A bot account from a dedicated Google Workspace is still external to the meeting host's organization. If an administrator permits only users from the host organization, the host organization must relax that policy. Signing in or adding the external account to the invitation does not override it.
</Callout>

### Step 2: Configure a signed-in bot when required

For Trusted or Restricted meetings, configure and verify a [signed-in Google Meet bot](https://docs.recall.ai/reference/google-meet-login-getting-started). The bot must join using the same Google account that you add to the Calendar event.

Signing in alone is not sufficient. The selected account, or a Google Group containing it, must also be invited.

### Step 3: Add the bot to the Calendar event

Add the bot's exact Google account email, or its Google Group, to the event's guest list. Save the event and confirm that the bot account or group appears as a guest before the bot attempts to join. Notifying guests by email is separate from adding them to the Calendar event.

For events managed by your application, update the attendee list through the calendar provider's API before the bot attempts to join. Confirm that the provider accepted the event update before continuing.

<Callout icon="📘" theme="info">
  ### An email sent during the meeting is not a Calendar invitation

  Emailing the bot or sharing the meeting link after the bot has started joining does not add its account to the underlying Calendar event. Do not rely on an in-meeting email to retroactively bypass admission. The join attempt may still require action from an eligible host.
</Callout>

### Step 4: Verify the complete flow

Before relying on automatic admission in production, run a real test meeting and verify that:

* The event uses the expected Meet link, access type, and Waiting room settings.
* The event guest list contains the exact bot account or Google Group.
* The bot signs in when required and joins without host action.

# Handle different scheduling models

### One-off meeting

Add the bot account or Google Group to that event, save it, and confirm it appears in the guest list before the bot joins.

### Recurring meeting

Add the bot account or Google Group to the recurring series when every occurrence should be recorded. Google applies the original meeting's access settings to the recurring meeting, but modified instances can differ, so verify any exceptions separately.

### Dynamically generated meeting

Create the event and Meet link, add the bot account or Google Group through the calendar provider API, and confirm the update succeeds before dispatching the bot. Do not rely on an invitation added after the join attempt has started.

# Troubleshooting

### The bot is still in the waiting room

1. Confirm the selected bot account or Google Group appears on the Calendar event, not only on an email.
2. Verify that **Meeting access** is set to the expected type. A meeting believed to be Open may still be set to Trusted.
3. Check whether the dedicated **Waiting room** is enabled.
4. Check the organizer's Workspace policy and host controls.

Google controls these meeting settings, and they may override the expected invitation behavior.

### Participants cannot see a request to admit the bot

The participant may not be the meeting's true host or may not have permission to admit guests. See [Google Meet waiting-room visibility troubleshooting](https://docs.recall.ai/v1.10/docs/troubleshooting-google-meet-waiting-room-visibility) and Google's [host controls documentation](https://support.google.com/meet/answer/16229038).

### The bot receives a Google Meet join failure

Use the bot's fatal status subcode to distinguish missing sign-in credentials, disabled knocking, and Google blocking the join. See [Bot status-change subcodes](https://docs.recall.ai/docs/sub-codes) and the [Google Meet FAQ](https://docs.recall.ai/reference/google-meet-faq).

### Google labels the bot as a potential risk

Google's automated risk classification is separate from the bot's sign-in and invitation state. A signed-in, invited bot may still be flagged, so neither sign-in nor an invitation guarantees automatic admission.

### The bot leaves before it is admitted

A bot can leave after waiting for the configured amount of time. See [Automatic Leaving Behavior](https://docs.recall.ai/reference/automatic-leaving-behavior) for timeout configuration.

# Recall and Google responsibilities

### Recall responsibilities

* Select which configured Google account the bot uses.
* Start the bot's join attempt at the requested or scheduled time.
* Apply the configured waiting-room timeout.

### Google responsibilities

* Apply the meeting access and Waiting room settings.
* Recognize Calendar invitations and enforce organization policies and host permissions.
* Run automated risk checks and make the final admission decision.

If a Google policy blocks an external bot, the meeting organizer or Workspace administrator must change that policy.

For general platform behavior, see the [Google Meet Overview](https://docs.recall.ai/reference/google-meet) and [Google Meet FAQ](https://docs.recall.ai/reference/google-meet-faq).
