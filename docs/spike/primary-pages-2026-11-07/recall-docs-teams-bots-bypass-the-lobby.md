SOURCE: https://docs.recall.ai/docs/teams-bots-bypass-the-lobby.md
Fetched 2026-11-07 with curl -sL -A 'Mozilla/5.0'

---
updatedAt: 2026-11-02T19:19:55.000Z
agentTools:
  projectIndex: https://docs.recall.ai/llms.txt
---

# Microsoft Teams: Skip the waiting room

See which Teams lobby settings let a bot bypass the waiting room so it can join meetings without manual admission.

Microsoft Teams has several different host settings that will let participants with [different roles](https://learn.microsoft.com/en-us/entra/external-id/user-properties) bypass the waiting room.

![](https://files.readme.io/0e688f96ac999f6ace37a0d9176a4581afe2c6d01bcb43f863b1740627138d7d-CleanShot_2025-10-21_at_14.28.062x.png)

There isn't one solution to make a Teams bot skip the waiting room for all meetings but below outlines the conditions that would allow a bot to bypass the waiting room for each setting:

* `Everyone` - the bot can skip the waiting room without conditions
* `Everyone in my org, trusted orgs, and guests` - the bot needs to be signed into a Microsoft Teams account, where:
  * The bot's email domain is added as a [trusted tenant](https://learn.microsoft.com/en-us/microsoftteams/trusted-organizations-external-meetings-chat?tabs=organization-settings#specify-trusted-microsoft-365-organizations) in the host's Microsoft Teams tenant
  * The bot's email has been [added as a guest](https://support.microsoft.com/en-us/office/add-guests-to-a-team-in-microsoft-teams-fccb4fa6-f864-4508-bdde-256e7384a14f) in the host's Microsoft Teams tenant
* `People in my org and guests` - the bot needs to be signed into a Microsoft Teams account, where:
  * The bot email belongs to the same Microsoft tenant as the host's tenant
  * The bot's email has been [added as a guest](https://support.microsoft.com/en-us/office/add-guests-to-a-team-in-microsoft-teams-fccb4fa6-f864-4508-bdde-256e7384a14f) in the host's Microsoft Teams tenant
* `People in my org` - the bot needs to be signed into a Microsoft Teams account, where the bot's email belongs to the same Microsoft tenant as the host's tenant
* `People who were invited` - the bot needs to be signed into a Microsoft Teams account, where the bot's email has been added to the calendar invite
* `Only organizers and co-organizers` - the bot needs to be signed into a Microsoft Teams account, where the bot's account has been [added as a co-organizer](https://support.microsoft.com/en-us/office/add-co-organizers-to-a-meeting-in-microsoft-teams-0de2c31c-8207-47ff-ae2a-fc1792d466e2)

To authenticate a teams bot, see the [Signed-In Microsoft Teams Bots](https://docs.recall.ai/docs/setting-up-signed-in-bots-for-microsoft-teams) guide.

<Callout icon="📘" theme="info">
  #### There isn't a single solution to make a bot skip the waiting room for all teams meetings

  The default setting is `People in my org and guests` so your bot will need to be signed into the same account as the host's org for the majority of meetings
</Callout>

The recommended solution is to:

* Have the host set the lobby settings to `Everyone`
* Have the host set the lobby settings to `People who were invited`, have the bot sign into an account, and to add the bot's email added to the calendar invite