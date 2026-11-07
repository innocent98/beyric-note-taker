SOURCE: https://docs.recall.ai/docs/zoom-signed-in-bots.md
Fetched 2026-11-07 with curl -sL -A 'Mozilla/5.0'

---
updatedAt: 2026-10-19T05:54:55.000Z
agentTools:
  projectIndex: https://docs.recall.ai/llms.txt
---

# Zoom Signed-in Bots 

Set up signed-in Zoom bots so bots can join meetings that require Zoom sign-in.

Zoom bots will emit a `fatal`error with the subcode `meeting_requires_sign_in` indicating "Sign In Required" if they attempt to join a Zoom meeting where the host has enabled ["Only Authenticated Users can Join"](https://support.zoom.com/hc/en/article?id=zm_kb\&sysparm_article=KB0063837):

<Image src="https://files.readme.io/5df8b2a031760d2b49997b5de87ef585f013fa27f7a4b20a0e0c62ffb63f631a-CleanShot_2025-01-16_at_08.52.54.png" align="center" border={true} />

Enabling this setting means that only users who are logged in to a Zoom account are permitted to join the meeting. Because the bot is not logged in by default, it will not be able to join the meeting.

To enable the bot to join these meetings, reach out to `support@recall.ai` for more details on what's available based on your use case.

# FAQs

## Can signed-in bots start a meeting?

Yes, a bot who is signed-in on behalf of the host can start a meeting.&#x20;

## Can I use a signed-in Zoom bot to skip the waiting room?

Yes, a bot who is signed-in on behalf of the host can skip the waiting room.

## Can I allowlist the email domain associated with the signed-in Zoom bot?

Yes, a bot who is signed-in on behalf of a user will also identify using their email domain. Have the bot sign-in from a user who has the allowlisted email domain to let the bot join the meeting.

## Can I have a single Zoom account join multiple meetings at the same time?

Yes, though note that Zoom free accounts have limits on simultaneous meeting joins. Keep this in mind when having the bot sign-in from a single account.

## Can I have the bot sign-in on behalf of any Zoom account to join sign-in-required meetings?

Yes - the bot can join meetings that have sign-in-required as long as the bot is signed-in. You can use a single Zoom service account to sign-in bots or allow the bot to sign-in on behalf of your users to gain their identity.

## Is there any additional cost for using signed-in Zoom bots?

There is no additional cost to use this from Recall and this feature is available for developers on the PAYG plan, though you can purchase additional Zoom seats for the accounts the bots use to sign in to get increased concurrency active meeting limits for the Zoom account.