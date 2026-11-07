---
updatedAt: 2026-11-03T23:59:23.000Z
agentTools:
  projectIndex: https://docs.recall.ai/llms.txt
---

# Google Meet Signed-in Bots

Set up signed-in Google Meet bots to avoid guest limitations and allow joining meetings that require Google sign-in.

Use signed-in bots when:

* A meeting requires Google authentication.
* You want bots to appear with a Google account's identity (profile photo + display name).
* You want a bot to bypass the waiting room for a same-workspace meeting or if it's invited to a calendar event

# Integration guide

## Step 1. Set up the bot account

1. With an admin user, sign into your google workspace and create a new user for the bot

<Callout icon="🚧" theme="warn">
  The Google account the bot uses to login must not be an admin in the Google Workspace. If the bot tries to login with an admin account, the login flow will fail.
</Callout>

![](https://files.readme.io/da45a96d8d907625d7791a0be4c4180e2109026e0afc0c7e514c605ab366ea5e-CleanShot_2026-10-22_at_5.31.51_PM2x.png)

2. Sign into the bot account before enableing SSO. Complete any google setup prompts and confirm access to Google Meet.
3. Set the account's profile picture from the admin account, if desired. You can also rename the bot from the admin console. Google Meet uses these for the signed-in bot; the account name overrides Create Bot's `bot_name`.

4) Create a new organizational unit in Directory > Organizational units. Enter a name and press Create.

![](https://files.readme.io/9ad6f4bfd3002e1cd793693dee4a7ca9a11dad0f324c45c1029414b98376f178-CleanShot_2026-10-22_at_5.59.25_PM2x.png)

5. Add the user to the newly created Organizational Unit. Hover over the user, click the **More Options&#x20;**&#x64;ropdown, select **Change organizational unit,&#x20;**&#x73;elect the new OU and press **Continue.&#x20;**&#x43;onfirm the pop up. It may ask you to reauthenticate as the admin user before the change goes through.

   This change may take up to 24 hours to take effect, but usually only takes a few minutes.

![](https://files.readme.io/ca11b7fbce4949d0fe635db13a6d418ded6e6bef0923510cb8fe28cc7643dbed-CleanShot_2026-10-22_at_6.17.29_PM2x.png)

### Generate a matching private key and certificate

Run the following command in a new directory so that you do not overwrite an existing key pair:

```bash
openssl req -x509 -newkey rsa:2048 \
  -keyout google-meet-sso-key.pem \
  -out google-meet-sso-cert.pem \
  -sha256 -days 3650 -nodes \
  -subj "/CN=google-meet-sso"
```

Recall uses `google-meet-sso-key.pem` to sign SAML responses; Google uses `google-meet-sso-cert.pem` to verify them.

**Save both files securely for as you will need it later in this guide**. You can also record which Workspace and Recall Logins use them for later reference if needed. Never include the private key in source control, support messages, or screenshots.

### Configure Google's Legacy SSO profile

<Callout icon="📘" theme="info">
  ### Use Legacy SSO

  Google's newer SAML/OIDC profiles are not supported for signed-in Google Meet bots at this time.
</Callout>

The administrator needs the **Security Settings** privilege.

1. Open **Google Admin → Security → Authentication → SSO with third party IdP**.

2. Click **Add SAML profile**.

   <Image src="https://files.readme.io/d6e7fa6c9c6049ed3898695f3d3ef624bc6dcf954414444799ea025958567e42-CleanShot_2024-11-27_at_11.01.102x.png" alt="Third-party SSO profiles in Google Admin" align="center" caption="Open Add SAML profile to reach the Legacy SSO settings." border={true} />

3. At the bottom of the IdP details page, click **Go to legacy SSO profile settings**.

4. Enter these redirect URLs, replacing RECALL\_REGION with the region of your workspace:

   | Setting           | Value                                                       |
   | ----------------- | ----------------------------------------------------------- |
   | Sign-in page URL  | `https://RECALL_REGION.recall.ai/api/v1/bot/gmeet-sign-in`  |
   | Sign-out page URL | `https://RECALL_REGION.recall.ai/api/v1/bot/gmeet-sign-out` |

5. Upload `google-meet-sso-cert.pem` as the verification certificate.

6. Enable **Use a domain-specific issuer** and save. For `meet-bots.example.com`, the issuer is `google.com/a/meet-bots.example.com`.

   <Image src="https://files.readme.io/a58080c9961d6d0fd613f90371d69134bc93aab6dad8156451e5d0cd5376f920-CleanShot_2024-11-27_at_10.48.132x.png" alt="Legacy SSO profile configuration in Google Admin" align="center" caption="Configure the Legacy SSO profile, verification certificate, and domain-specific issuer." border={true} />

7. Return to **Manage SSO profile assignments** and assign the Legacy SSO profile to the new organizational unit you created earlier.&#x20;

   Ensure the newly created organizational unit is selected, change the SSO profile from **None&#x20;**&#x74;o **Legacy SSO profile**.

   <Image src="https://files.readme.io/55f842b0bb43565197fe503b1d99c8b6d854fa0a2cb6bcd45147db9a8a8847b5-CleanShot_2026-10-22_at_6.07.36_PM2x.png" align="center" caption="Click manage under Manage SSO profile assignments" />

   <Image src="https://files.readme.io/08109f5e64b9f97d11d1b32d1e3429c7564387a6917778a4116d1393623c9219-CleanShot_2026-10-22_at_6.10.24_PM2x.png" align="center" caption="Ensure the newly create OU is selected" />

   Use the **Users** view to check the effective assignment for each bot user. Confirm that an inherited organizational-unit or group setting has not selected another profile or `None`.

## Step 2. Create a Google Login Group in Recall

A **Google Login Group** holds the accounts Recall can select and controls when bots sign in. Recall selects an active Login from the group for each bot; adding accounts helps distribute concurrent sign-ins.

Use the same Recall workspace and region for the group, Logins, and bots. Group IDs cannot be used across Recall workspaces.

Open **API Explorer → Google Logins** in your regional dashboard:

[us-east-1](https://us-east-1.recall.ai/dashboard/explorer/google-login) · [eu-central-1](https://eu-central-1.recall.ai/dashboard/explorer/google-login) · [us-west-2](https://us-west-2.recall.ai/dashboard/explorer/google-login) · [ap-northeast-1](https://ap-northeast-1.recall.ai/dashboard/explorer/google-login)

Select the groups view, click **Create Group**, and give it a name. Set `login_mode` to `always` so the setup test attempts sign-in. Save the group's `id`.

<Image src="https://files.readme.io/d12661871f84f11083a0591ad6cde3826504a720e93c23ed7c869fe0638f2c8f-CleanShot_2025-01-30_at_09.21.29.png" alt="Google Login Group login mode configuration in Recall" align="center" caption="Set the group's login mode to always for the setup test." border={true} />

For conditional sign-in, see [Which login mode should I use?](#which-login-mode-should-i-use)

## Step 3. Add Google Logins to the group

A **Google Login** connects an existing Google account to the group. Creating Recall resources does not create Google users or configure Workspace SSO.

In **API Explorer → Google Logins**, open the **Logins** tab and select **Create Login**.

Enter the bot account's email, select the Login Group from step 2, upload the private key and certificate from step 1, and click **Create**.

![](https://files.readme.io/7006e1fcae9de28c38ddf3b3f327b71a512337865c1ad978c05d7e4838a91925-CleanShot_2026-09-05_at_18.31.192x.png)

<Callout icon="📘" theme="info">
  ### Active does not confirm sign-in

  **Active** enables the Login for selection and background sign-in preparation. **Ready** means the required credential data is stored, not that a live sign-in has been verified.
</Callout>

Check Login Status before testing. If preparation fails, review the account and SSO settings, then click **Retry** after correcting any issues. See [Login statuses](#login-status-and-retries) for other states.

## Step 4. Send a test bot and verify its identity

Either create a meeting that requires participants to be signed-in or create a new Google Login Goup with `Login Mode` set to `always`.

Then send a request via the [Create Bot](https://docs.recall.ai/reference/bot_create) endpoint with your meeting URL and group ID. Replace the variables and meeting URL below with your own values:

```bash
RECALL_REGION="us-west-2"
RECALL_API_KEY="YOUR_RECALL_API_KEY"
GOOGLE_LOGIN_GROUP_ID="YOUR_GOOGLE_LOGIN_GROUP_ID"

curl --request POST \
  --url "https://${RECALL_REGION}.recall.ai/api/v1/bot/" \
  --header "Authorization: Token ${RECALL_API_KEY}" \
  --header "Content-Type: application/json" \
  --data "{
    \"meeting_url\": \"https://meet.google.com/YOUR-MEETING-CODE\",
    \"google_meet\": {
      \"google_login_group_id\": \"${GOOGLE_LOGIN_GROUP_ID}\"
    }
  }"
```

Pass the intended group ID explicitly in every bot-creation path.

To verify the test:

1. Save the bot ID and check its status changes to see if it failed to join the call.
2. Confirm that the actual request uses the intended group, Recall workspace, and region.
3. Compare the bot's name and profile picture with an account in the selected Login Group. A group with multiple accounts can select a different account for each bot.

Customer-defined metadata and Google warnings are not proof of authentication. If the test fails, start with the [sign-in error checks](#sign-in-errors).

# Troubleshooting

## Login status and retries

Creating a Login confirms only that the resource exists. **Active** enables selection and background sign-in preparation. **Login Status** describes stored preparation state, not a live check with Google.

| Status       | Meaning and next step                                                                                                                      |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Ready        | Required credential data is stored. Test a bot; its next sign-in is not guaranteed to succeed.                                             |
| Provisioning | Preparation is incomplete, with no recorded error. Confirm Active is enabled, then check again.                                            |
| Degraded     | Preparation is incomplete with a nonterminal error. Review the account and SSO configuration; do not assume the error will resolve itself. |
| Failed       | Preparation recorded a blocking error. Review the account and SSO configuration, then click Retry after correcting the cause.              |

After correcting settings or credentials, ensure **Active** is enabled, click **Retry** on the affected Login, check its status, and send a fresh test bot. Retry resets stored sign-in preparation; it does not change Google's settings or confirm successful authentication.

## Sign-in errors

Read the bot's status changes and final status: a retryable sign-in error can be followed by another attempt. The checks below are starting points, not confirmed causes or guaranteed fixes.

| Subcode                                                                                                | What to check                                                                                                                                                                             |
| ------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `meeting_requires_sign_in`                                                                             | Google requires authentication. Check that the bot uses the intended Login Group and that its accounts are configured.                                                                    |
| `google_meet_sign_in_missing_login_credentials` or `google_meet_sso_sign_in_missing_login_credentials` | These missing-credentials codes can appear on older bot versions. Check the actual bot request, selected group, and configured Google Logins.                                             |
| `google_meet_sign_in_failed` or `google_meet_sso_sign_in_failed`                                       | Check the Google account, effective SSO assignment, domain, key, certificate, and any Google account prompts. The code alone does not identify which check failed.                        |
| `sso_not_configured`                                                                                   | Check the primary domain, enabled Legacy profile, domain-specific issuer, and effective user assignment. If settings were corrected after an earlier failure, click Retry and test again. |
| `sso_login_invalid_recipient`                                                                          | Check `sso_v2_workspace_domain` against the dedicated Workspace's primary domain and its domain-specific SSO configuration.                                                               |
| `sso_login_account_disabled`                                                                           | Check the selected account's status in Google Admin. Restore the account if appropriate or use an available account in the group.                                                         |
| `google_meet_login_not_available`                                                                      | Check the group's active Logins and available account capacity; this code alone does not prove capacity is exhausted.                                                                     |

See [Bot Sub Codes](https://docs.recall.ai/docs/sub-codes) for the complete reference.

# FAQ

## How do I access the bot's Google Workspace Admin dashboard once Legacy SSO is enabled?

You can login using the super admin accounts email and password as [this is always an accessible option with Legacy SSO enabled](https://knowledge.workspace.google.com/admin/apps/super-administrator-sso).

## How do I access a bot's Google account?

If Google Admin redirects you to Recall's sign-in URL and returns `not_authenticated`, use the SSO login tool to establish a Google session first. Use an account that already has Google Admin permissions, not the bot's non-admin account.

1. Open an incognito window and visit the [SSO login tool](https://uaqbprbelz32kdxr2y6r7gzy7i0vhdsd.lambda-url.ap-southeast-2.on.aws/).
2. Enter the admin account's email in **Email**, and the Workspace's primary domain in **Workspace Domain / Tenant ID**. (Note: UPN and Relay State are optional, UPN must be non-empty).
3. Paste the complete SSO **Private Key** and **Cert** configured for that Workspace, select **Google** as the platform, and click **Submit**. Complete any required Google verification.

<Image src="https://files.readme.io/ac881a753fa33e641a4af9b6109b6a03885994a0e2577e1c197b0c413e9fcc75-CleanShot_2026-09-05_at_19.00.132x.png" alt="SSO login tool with Email, UPN, Workspace Domain, Private Key, Cert, optional Relay State, and Platform fields" align="center" width="500px" caption="SSO login tool. The values shown are examples; use your Workspace's credentials." border={true} />

4. Open the Google profile in a new tab in the **same incognito window**.

This signs you in; it does not change your Workspace's SSO settings or grant administrator privileges. Do not share screenshots of the form containing your private key.

## Can bots sign in as users from an end user's existing company Google Workspace?

No. This setup requires administrative control of a dedicated bot Workspace and changes to its SSO configuration; it does not connect a customer's existing Google account through individual consent.

**Do not apply this configuration to a customer's company Google Workspace: it can disrupt employees' access to Google services.** A customer-owned, separate Workspace dedicated to bots must meet the same requirements in this guide.

## Which login mode should I use?

* `only_if_required` is recommended and only signs in when Google requires authentication when a bot joins a meeting. Otherwise, the bot may join unsigned.
* `always` attempts sign-in for every Google Meet meeting using this Google Login Group. Use it for setup tests and when bots should use a Google account identity in every meeting.

## Why does Google warn that the bot may not be who it claims to be?

Google can show this identity warning (`This person isn't signed in with a Google Account. They might not be who they claim to be`) when a bot joins without signing in.&#x20;

You can implement signed-in Google Meet bots to make the bot sign-in for meetings instead of joining as an anonymous participant.

<Image src="https://files.readme.io/fc570d2-CleanShot_2024-03-25_at_21.52.542x.png" alt="Unsigned-participant identity warning" align="center" width="400px" caption="Identity warning for an unsigned participant." border={true} />

This is different from Google's [potential-risk classification](#why-does-a-signed-in-bot-still-show-a-google-warning), which can still appear for a signed-in bot.

## How do I change a signed-in bot's name or profile picture?

Update the Google account's display name or profile picture. Google Meet uses that account's identity and ignores Create Bot's `bot_name`. If the Login Group contains multiple accounts, update each account that should use the same identity.

## How do I add more accounts?

You can add more accounts to an existing login group or create a new login group. For each additional account:

1. [Create a standard user in the dedicated Google Workspace](https://support.google.com/a/answer/33310).
2. Confirm it is active, complete any required Google setup or verification, and check its effective Legacy SSO assignment.
3. Create a separate Recall Google Login with the user's email, group ID, and Workspace's matching SSO key and certificate.
4. Check each Login's status and test the account. A successful sign-in tests only the selected account, not every account in the group.

For testing, you can use different names/profile photos to verify whether the account works.

## How many Google accounts do I need?

One Google Login can support **30 concurrent bot sign-ins per Google account** (e.g., you can have 30 bots actively in calls sharing the same signed-in account at the same time). This is a platform-level limitation set by Google. We recommend starting with two logins for most developers, where the accounts are not admin users in the Google Workspace.

If a bot reports `google_meet_login_not_available`, check the intended group's enabled Logins and available account capacity.

## Why did a previously working account stop signing in?

Check for changes since the last successful test:

* Was the Google account suspended, disabled, renamed, or given administrator privileges?
* Are you able to sign-in to the bot's Google account?
* Did its effective SSO assignment change, including an inherited organizational-unit or group override?
* Did the primary domain or Recall `sso_v2_workspace_domain` change?
* Was a new certificate uploaded to Google without updating the matching Recall credentials?
* Did the bot request, Recall workspace, group ID, or group login mode change?

If you're still encountering issues after working through this list, reach out to `support@recall.ai` for additional help troubleshooting.

## Why does a signed-in bot still show a Google warning?

Signing in removes the standard unsigned-participant warning, not necessarily Google's separate potential-risk classification. Signed-in bots will not resolve this warning.

![](https://files.readme.io/aa6ab4a26318e5613e177adbf6880e75cef0a5c6eb2de2650657c35a79c028ac-image.png)

Please reach out to `support@recall.ai`.

## Can I verify bot sign-ins in Google Admin?

Two views can help:

* **Users → Last sign in** is approximate and can lag by several hours to three days.
* **Reporting → Audit and investigation → User log events** lets authorized administrators filter by user and time, login type, and event status.

Neither is a complete per-bot record: Google can combine session events and omit some non-browser sign-ins. Legacy SAML can show a failed Google Password event followed by a successful SAML event. Inspect the sequence before concluding that sign-in failed.

See [Last sign-in information](https://knowledge.workspace.google.com/admin/reports/view-your-users-last-sign-in) and [User log events](https://knowledge.workspace.google.com/admin/reports/user-log-events).

## How do I get the bot to bypass the Google Meet waiting room?

In order for a bot to bypass the waiting room:

1. The bot must be signed in to a Google account (following the guide above)
2. The email of the account the bot is signed into must be on the underlying calendar event

<Callout icon="📘" theme="info">
  ### **Tip:** Managing multiple logins

  If your Recall Google Login Group contains multiple bot accounts, you can create a **Google Workspace group** with those accounts as members. Add the group’s email address to the calendar event so you don’t need to invite each bot account individually.

  Google Calendar [supports group invitations and keeps the guest list in sync with group membership](https://support.google.com/calendar/answer/172013?hl=en). Google Meet [allows Calendar invitees to join without knocking under Trusted or Restricted meeting access](https://support.google.com/meet/answer/9302870?hl=en). Each bot still signs in using its own Google account.

  Google’s group invitation permissions and the host’s meeting settings still apply, including the separate Waiting room setting.
</Callout>