SOURCE: https://docs.recall.ai/docs/setting-up-signed-in-bots-for-microsoft-teams.md
Fetched 2026-11-07 with curl -sL -A 'Mozilla/5.0'

---
updatedAt: 2026-09-29T01:49:48.000Z
agentTools:
  projectIndex: https://docs.recall.ai/llms.txt
---

# Microsoft Teams Signed-in Bots

How to sign your bot into a Microsoft teams account

By default the Teams bot will join meetings as an anonymous guest participant. You can configure your Teams bots to sign into a Microsoft account before joining a meeting to allow a bot to join sign-in required meetings.

<Callout icon="⚠️" theme="warn">
  ### **Limitations**

  - _Signed-in bot names cannot be overridden_ - Signed-in Microsoft Teams bots get their name from the Microsoft Teams account used to sign-in the bot. This overrides the `bot_name` parameter in  <Anchor target="_blank" href="ref:bot_create">Create Bot endpoint</Anchor>
  - _Signed-in Microsoft Teams bots must be in their own Organization_ - Signed-in Microsoft Teams bots  must be in their own organization due to global organization-level permission changes
  - _Personal MS Teams not supported_ - Bots will fail to join Personal MS Teams links if mandatory login is enabled.
</Callout>

# Implementation

## Step 1: Set up a new Microsoft 365 Business account

<Callout icon="🛑" theme="error">
  ### Do not create the bot account in your existing Microsoft business organization

  Since the authenticated bot **requires global organization-level permission changes**, authenticated Teams bots should always use a **_new_** Microsoft account
</Callout>

1.1: Head to the <Anchor target="_blank" href="https://www.microsoft.com/en-us/microsoft-365/business/compare-all-microsoft-365-business-products-b">Microsoft 365 Business page</Anchor> to buy the **Microsoft 365 Business Basic** license

1.2: After clicking "**Buy Now**", proceed to login with your admin account

1.3: Microsoft will then ask you to set up your account and pay for the license

1.4: Once done, you will need to sign into the M365 admin dashboard

> Note: Microsoft may request you to set up MFA for this admin account

## Step 2: Create the bot's account

### Step 2.1: Create a new user for the bot

Navigate to: **Users** > **Active users**

> Note: you should see your admin in the active users

Click "**Add a user**" (see image below)

![](https://files.readme.io/501cd55d276959ffe131afb18fd6ea42c5092ae804327cc9730914b7226830b4-CleanShot_2025-09-21_at_23.29.372x.png)

Next, fill out these fields for the bot (make sure to write them down in a separate place):

* Email
* Display name
* Password
  * > Note: If you automatically create a password, make sure to "reveal" the password in the final step and write it down
* You can auto-assign a user license in this step OR you can transfer the license in ***Step 2.2****&#x20;*&#x62;elow, it makes no difference.  You do not need to buy an additional license for the bot.
* You do not need to apply roles/permissions or fill out any additional details

Again, **you will need to save the bot's email and password for later, make sure to write this info in a separate note**

<Callout icon="🚧" theme="warn">
  ### The bot display name and profile picture cannot be overridden

  Regardless of what you specify in your [Create Bot](https://docs.recall.ai/reference/bot_create) request, the display name and profile picture will always be taken from the Microsoft account details defined in this section
</Callout>

### Step 2.2: Reassign the Teams license to the bot user

Head to **Billing** > **Licenses** > **Microsoft 365 Business Basic** and select your admin account. &#x20;

Click "**Unassign licenses**" and then click "**Unassign**" on the confirm dialog pop up:

![](https://files.readme.io/9e5a24ae990f9725065ed06a62c4b031622451a1305f42bbf79292f82ee75b4b-CleanShot_2025-09-21_at_23.49.032x.png)

<br />

After, reload the page and then you can reassign the license to the bot user.&#x20;

> If you are prompted to buy another license after unassigning, wait a few minutes before refreshing the page. Microsoft may need some time to free up the license before you can reassign it

![](https://files.readme.io/656042a586c422e489d7a840bcb576666fe5e08bc13a700b871b695956a81a7a-CleanShot_2025-09-21_at_23.50.522x.png)

## Step 3: Add the bot's sign-in credentials in the Recall dashboard

Head to the **Meeting Bot Setup** > **Microsoft Teams setup dashboard** page

Add the bot's email & password

<Callout icon="📘" theme="info">
  ### View the Microsoft Teams setup dashboard

  - <Anchor target="_blank" href="https://us-east-1.recall.ai/dashboard/bot-setup/microsoft-teams">(US) us-east-1</Anchor>
  - <Anchor target="_blank" href="https://us-west-2.recall.ai/dashboard/bot-setup/microsoft-teams">(US) us-west-2</Anchor>
  - <Anchor target="_blank" href="https://eu-central-1.recall.ai/dashboard/bot-setup/microsoft-teams">(EU) eu-central-1</Anchor>
  - <Anchor target="_blank" href="https://ap-northeast-1.recall.ai/dashboard/bot-setup/microsoft-teams">(JP) ap-northeast-1</Anchor>
</Callout>

![](https://files.readme.io/042e9d967654482409016e52cefb5374ccfcbfda524b590dabcdfcfc745bb536-CleanShot_2025-09-21_at_23.57.212x.png)

<Callout icon="🚧" theme="warn">
  ### We recommend keeping Login Mandatory option **_"Disabled"_**

  #### Login Mandatory (Disabled)

  The bot will only attempt to login for calls where signed in participants are mandatory

  #### Login Mandatory (Enabled)

  Forces the bot to always login before joining the call. This means the bot's profile (name and picture) will be used every time the bot joins the call.&#x20;

  This will override the `bot_name` setting you have set
</Callout>

## Step 4: Update the Microsoft tenant's security settings

### Step 4.1: Create a pay-as-you-go azure account

Sign into the <Anchor target="_blank" href="https://azure.microsoft.com/en-us/pricing/purchase-options/azure-account">Azure admin portal</Anchor> using the admin account

### Step 4.2: Disabling Security Defaults

> All tabs and fields for Step 4.2 can be seen in the image below

Search for the "**Microsoft Entra ID"** product in the top search bar inside the Azure admin portal

Inside the **Microsoft Entra Id** product, disable the security defaults found in:

* **Overview** > **Properties** > **Manage Security Defaults**
* Set **Security Defaults** to "**Disabled (not recommended)"&#x20;**&#x69;nside the right sidebar
  * If asked to fill out the **Reason for disabling** field, simply select "**Other**" and write "**Preference**" in the text box
* Click **Save** at the bottom

![](https://files.readme.io/5fc0a5daa8c56200a599ce94fab5c0b409953d1b0f902fd89243e2f171965ccd-CleanShot_2025-09-21_at_23.43.142x.png)

### Step 4.3: Disable "Show keep user signed in"

> All tabs and fields for Step 4.3 can be seen in the image below

Search for the "**Users"** product in the top search bar

Once in the **Users** product:

* Click on **User Settings** in the sidebar and *disable* the "**Show keep users signed in**" toggle

![](https://files.readme.io/46514cb52eef7c8087b713573e0b6993a11a1136174388caf5f3123466d13551-CleanShot_2025-09-21_at_23.45.312x.png)

### Step 4.4: Disable "Self-Service Password Reset (SSPR) policy"

> All tabs and fields for Step 4.4 can be seen in the image below

Search for the "**Password reset**" product

Once in the **Password reset** product:

* Click on **Properties** in the sidebar
* Set the "**Self service password reset enabled**" option to "**None**" (this is generally the default)

![](https://files.readme.io/3916c6726934d8c514ae03c2da804009c5d0c2b3053e0d40c22679c36e2a5b71-CleanShot_2025-12-14_at_21.04.522x.png)

### \[Optional] Step 4.5: Set the bot's profile picture

> All tabs and fields for Step 4.5 can be seen in the image below

Search for the "**Users"** product in the top search bar (same as step 4.3 above)

Once in the **Users** product:

* Click on your bot's name in the **Display Name** column
  * > If you don't see the bot's profile, wait a couple minutes and reload the page
  * This navigates to the bot's profile
* Click on **Overview** > ***\[Camera Icon]&#x20;***&#x74;o set the bot's profile picture

![](https://files.readme.io/67b6edae01a187a39eb08c145586c3104fec594246d0e4aa5ed2b2d8509876f9-CleanShot_2025-09-21_at_23.46.522x.png)

## Step 5: Test the bot

Now that your new bot account is set up, you can try sending your bot to a new Microsoft Teams meeting

You can quickly test this by sending a bot to the meeting using the interactive <Anchor target="_blank" href="ref:bot_create">Create Bot</Anchor> api docs. Make sure you use a new Teams Meeting URL and the API key from the Recall account with your Microsoft Teams Business Account login credentials

When testing, enable the "**Login Mandatory**" option in the Recall dashboard's **Meeting Bot Setup** > **Teams** > **Signed-in Microsoft Teams credentials**. Then you can send a bot to a Teams meeting to see it sign-in.&#x20;

> ***Don't forget to disable "Login Mandatory" again after your test***

<Image src="https://files.readme.io/cbed8e290271a98102d6a477baf2576800386781140517aa434f9f416db8f983-CleanShot_2025-09-22_at_00.10.452x.png" align="center" />

Note that the bot will only sign in for meetings that require signed-in participants

***

# FAQs

## Why does the bot sometimes have `(Guest)` and others `(External)` after the display name?

<Callout icon="📘" theme="info">
  ### Bot **_is not_** signed in

  A bot has `(Guest)` after the display name

  ### Bot **_is_** signed in

  A bot has `(External)` after the display name
</Callout>

If you want the bot to always show `(External)` instead of `(Guest)`, you can enable the "Login Mandatory" checkbox in the Teams Web Credentials. **Note that logging in the bots take a significantly longer time to join calls**

## Why does my Teams bot’s participant label or profile picture vary between meetings?

Teams determines how your bot appears based on its sign-in status and its relationship with the meeting host’s organization. **Signing in does not guarantee a particular participant label or a visible profile picture.**

### What do the participant labels mean?

| Label          | Meaning                                                                                                                                                                        |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **No label**   | The participant belongs to the host’s organization.                                                                                                                            |
| **Guest**      | The participant is signed in using a guest account in the host’s organization. This label does not mean the participant is unsigned.                                           |
| **External**   | The participant is outside the host’s organization. Teams distinguishes between familiar and unfamiliar external participants based on whether they are recognized or trusted. |
| **Unverified** | Teams treats the participant as anonymous and cannot verify their identity for the meeting.                                                                                    |

Being signed in with a Microsoft Entra ID or personal Microsoft account does not automatically establish a trusted relationship. Microsoft’s [meeting guidance](https://learn.microsoft.com/en-us/microsoftteams/plan-meetings-external-participants) explains that signed-in participants without the required trust can still be treated as anonymous. Its [current trust-indicator documentation](https://learn.microsoft.com/en-us/microsoftteams/trust-indicators) describes the additional familiar and unfamiliar external categories.

### How do I configure trust and show the bot’s profile picture?

When the bot’s camera is off, displaying its Microsoft account profile picture requires a trusted relationship with the host’s organization. Uploading a profile picture to the bot’s account is not sufficient on its own.

Teams administrators should check **Users → External access** in the [Teams admin center](https://admin.teams.microsoft.com/company-wide-settings/external-communications) for **both organizations**:

1. Ensure the host’s organization allows the bot’s account domain.
2. Ensure the bot’s organization allows the host’s domain.
3. Ensure the applicable user policies enable External Access for the bot and the meeting organizer.

If an organization uses an allowlist, add the other organization’s domain. Allowing all external domains can permit communication, but Microsoft’s newer indicators may still classify a participant as unfamiliar. See Microsoft’s [External Access configuration guide](https://learn.microsoft.com/en-us/microsoftteams/trusted-organizations-external-meetings-chat) and [trust-indicator definitions](https://learn.microsoft.com/en-us/microsoftteams/trust-indicators).

![Microsoft Teams External Access settings](https://files.readme.io/f9835ea0fa4ee6f94a02a9b091c20b337d7b575d1cda14733f9e5bb9609cf4ae-CleanShot_2025-07-25_at_14.24.25.png)

*Check External Access settings in both organizations. If either uses an allowlist, include the other organization’s domain.*

## Would Microsoft Teams Essentials plan also work for setting this up or does this requires a Microsoft 365 Business Basic?

We haven't tested the Microsoft Teams Essentials plan ourselves so we can't say for certain but we recommend our devs use the Microsoft 365 Business Basic plan as this is what we used for setup and it contains all the required security settings and configurations needed for the bot to join calls

## Is there a way to detect beforehand when a Signed-in Teams bot will be required for a user meeting

There is ***not*** a way for you to detect what kind of bot will be required for a meeting beforehand because the bot will need to attempt to sign in and encounter the "you need to sign in" page for us to know

That being said, we also have an option in the dashboard to make the bot sign in for every meeting (called "Login Mandatory", referenced in **Step 3** above). If you leave this bot unchecked, it will only sign in for meetings that require participant sign-in

We generally recommend developers leave this box unchecked (do not sign in for every meeting, only when required) to keep the bot's join time as low as possible

## Can we use our own Teams organization or do we need a new Teams organization for every customer?

We recommend creating a new organization for the bot, then all of your customers can invite this bot to their org (so you only need one new org for all your customers). We recommend creating a new organization for your bot because you need to update the org's security settings which you typically don't want to apply to your whole org

We also recommend having your customers tenants add the bots domain as a "trusted organization" in their security settings

## Will Signed-in Teams bots work for Calendar V1, V2, scheduled bots, or ad-hoc bots?

This will work for all meetings (Calendar V1 / V2 meetings, scheduled meetings, or ad hoc meetings). Once you configure signed-in Teams bots in the Recall dashboard, there is no additional configuration required to make them work for all meeting types.

## Why doesn't my Teams account profile photo show?

Teams will only show an avatar when the bot’s account is in a trusted organization for the meeting host’s tenant ([details here](https://docs.recall.ai/docs/signed-in-teams-bots-overview)). To see the bot’s profile picture, the Teams admins on both sides need to configure a trusted relationship / External Access by whitelisting each other’s domains ([Microsoft docs](https://learn.microsoft.com/en-us/microsoftteams/trusted-organizations-external-meetings-chat?tabs=organization-settings)).

## Why do two bots signed in with the same account show up as only one attendee in Teams?

Teams merges participants that share the same email, so multiple bots using one account appear as a single attendee. Give each bot its own account to avoid this if you are testing with multiple signed-in teams bots

## How can users disable the sign-in required setting on Teams?

Users will need to modify their "[Anonymous users can join a meeting](https://learn.microsoft.com/en-us/microsoftteams/anonymous-users-in-meetings#anonymous-users-can-join-a-meeting)" setting in their MS Teams settings.

## Will the bot *always* sign into teams calls?

This behavior is configurable according to your use case.

When settings up the credentials in the Recall dashboard, you can toggle this behavior using the **Login mandatory** checkbox:

<Image src="https://files.readme.io/3f7d65e55932c1f0a3a626db62778e0a240a131fcac1170c7b7a638020aaac68-CleanShot_2025-10-17_at_11.05.342x.png" align="center" caption="Login mandatory checkbox" border={true} />

## Why can’t participants attach files when a Teams bot joins, even when it’s signed in?

Bot sign-in and file-sharing permissions are separate. Under the separate-organization setup, signing in doesn’t make the bot an internal member of the meeting organizer’s organization. Trusting the bot’s domain also doesn’t enable file sharing by itself.

Ask your Teams administrator to check:

* If the Attach button is missing: For chats using external access (federation), check `FileSharingInChatsWithExternalUsers` in the Teams files policy applied to the person uploading. It is disabled by default.
* If a file uploads but recipients cannot open it: Check the file’s OneDrive/SharePoint permissions and the organization’s sharing restrictions.

[Microsoft’s external file-sharing configuration guide](https://learn.microsoft.com/en-us/microsoftteams/share-files-loop-in-external-chats) covers enabling attachments for federated meeting chats. If the problem continues, contact Recall support with the bot ID, meeting time, and a screenshot or exact error.

As a workaround, upload the file to OneDrive or SharePoint, give the intended recipients permission to access it, and paste its link into the meeting chat. This shares a link to the file; it doesn’t enable direct attachments or bypass your organization’s sharing restrictions.