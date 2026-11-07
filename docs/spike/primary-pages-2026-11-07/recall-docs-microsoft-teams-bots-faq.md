SOURCE: https://docs.recall.ai/docs/microsoft-teams-bots-faq.md
Fetched 2026-11-07 with curl -sL -A 'Mozilla/5.0'

---
updatedAt: 2026-09-29T01:47:28.000Z
agentTools:
  projectIndex: https://docs.recall.ai/llms.txt
---

# Microsoft Teams: FAQ

Find answers to common Microsoft Teams bot trust and file-sharing issues so your meetings work as expected.

## Why does the bot have Unverified after the name?

Microsoft released an [update](https://learn.microsoft.com/en-us/answers/questions/1534900/unverified-text-is-appearing-next-to-the-acs-user) February 2024 that affects how Teams participants are displayed depending on their account's relationship with the organization.

This only affects the new version of teams (teams.microsoft.com), and is not applicable for the old version (teams.live.com).

Below is a summary of these changes:

> **No label:** All participants who are part of the organizer’s organization.
>
> **External:** All participants who are external to the organizer’s organization but have a trusted relationship with the organizer or their organization.
>
> **Unverified:** All other participants will be seen with this label. This will include Microsoft Entra ID users who belong to organizations that do not have an explicit external access setup with the organizer’s organization, Microsoft Account (personal) users, users who are not using any Microsoft ID while joining meetings, and others.

This means that, by default, bot that join a Teams meetings hosted at teams.microsoft.com will have the `Unverified` suffix.

**How to remove the unverified tag**

If you wish to remove this tag in favor of the `(External)` tag, you can authenticate Teams bots as outlined in [Microsoft Teams Bot Login](https://docs.recall.ai/docs/setting-up-signed-in-bots-for-microsoft-teams).

Microsoft Tenants can specify trusted organizations by following [these steps](https://learn.microsoft.com/en-us/microsoftteams/trusted-organizations-external-meetings-chat?tabs=organization-settings).

## Why do some participants show as Unknown, or why are speaker names missing in transcripts?

On Microsoft Teams, participants can hide their identity in captions and transcripts. If Automatically identify me in meeting captions and transcripts is off, Teams may not send a real speaker identity with that person's speech. Recall may then label them as Unknown or attribute speech incorrectly.

Where to find it: **Teams → Settings → Accessibility → Captions and transcripts → Transcription → Automatically identify me in meeting captions and transcripts**

Microsoft documents this setting here: [Hide your identity in meeting captions and transcripts in Microsoft Teams](https://support.microsoft.com/en-us/office/hide-your-identity-in-meeting-captions-and-transcripts-in-microsoft-teams-a1095e1a-a2f0-453e-8101-eca76429ff04)

Org policy can override the user setting, so if a user says it's enabled, ask their IT admin to confirm it isn't disabled org-wide.

This most often affects meeting caption transcription (meeting\_captions). If the setting is enabled and attribution is still wrong, contact Recall support with the bot ID.

<br />