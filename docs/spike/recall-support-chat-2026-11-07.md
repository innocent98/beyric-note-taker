# Recall support chat (AI assistant), 2026-11-07, pasted by Adebayo

**Provenance:** pasted by Adebayo from Recall's support chat, which is an AI assistant, not a person. The lead did not see the chat itself. Read it as "what Recall's assistant said", not as Recall's policy. Compare it with the docs pages read by the fourteenth lead the same evening (`primary-pages-2026-11-07/recall-docs-google-meet-login-getting-started.md`, `...-skip-the-waiting-room-md-2026-11-07-lead14.md`, `recall-docs-bot-overview.md`: `curl` of the `.md` version of each page, saved untouched).

## Question 1 (Adebayo)
For my bot to skip the waiting room, do I need a Google Workspace account, or can a normal Gmail be used?

## Answer 1 (assistant)
A regular Gmail account can be used for the bot. The bot must be signed in and its email added to the Calendar event as a guest. It cited the "Skip the waiting room" page.

## Question 2 (Adebayo)
The `google-meet-login-getting-started` document mentions Workspace. I build my bot with the Recall API and want it to skip the waiting room for users whose email is on the invite, but I keep hearing a Workspace is needed.

## Answer 2 (assistant)
Two different things. For skipping the waiting room: a regular Gmail works if signed in and invited. For configuring Google SSO (the sign-in backend): a Google Workspace is needed (domain, private keys, certificates). "Once you've configured the Google Workspace SSO backend, the individual Google accounts your bots sign into can be regular Gmail accounts." It said the login guide mentions Workspace because it walks through SSO setup.

## What the docs pages say (read by the lead, 2026-11-07, `curl` of the `.md` versions)
- **Skip-the-waiting-room page:** a signed-in bot skips the knock on Trusted or Restricted meetings if its Google account is on the Calendar event; "Open" meetings let anyone in without knocking (when the separate Waiting room is off). It says the signed-in bot is configured by the login guide and that this page "does not cover configuring or troubleshooting Google sign-in". It says nothing about Gmail. Its warning: "No Recall or Google Meet configuration guarantees automatic admission."
- **Login guide:** step 1 is "With an admin user, sign into your google workspace and create a new user for the bot"; the bot account "must not be an admin in the Google Workspace"; then a Legacy SSO profile with your certificate, assigned to an organisational unit of that Workspace; then a Login Group and a Login in Recall (upload private key and certificate). "Creating Recall resources does not create Google users or configure Workspace SSO." It says a customer's existing Workspace cannot be used and that a separate Workspace dedicated to bots must meet the same requirements. **The guide never says a Gmail account can be a bot Login.**
- **So the assistant's "regular Gmail accounts" claim is not on either page**, and it conflicts with the login guide's own steps (the SSO profile is assigned to a Workspace organisational unit). Unresolved until Recall answers in writing.

## Questions to put to Recall support (next message)
1. Can a bot Login in Recall (the `google_login_group_id` path) use a personal Google account (`@gmail.com`, or a free Google Account created on a custom-domain address), with no Google Workspace and no Legacy SSO? Yes or no, and where is that written in the docs?
2. If yes: how is such an account registered (the Login API asks for a private key and certificate that only make sense with a Workspace SSO profile)? If no: please confirm that every signed-in Meet bot needs a dedicated Workspace with Legacy SSO and a non-admin user.
3. Is a signed-in bot, whose account email is on the Calendar event and the meeting is Trusted, admitted without knocking in the cases you have measured? Do you publish or can you share a join success rate for signed-in versus guest bots on Google Meet?
4. What does Google do when a bot account's Workspace is a free or trial Workspace? Are there Workspace plans or settings (a Business Starter seat, domain verification) that you know to work or to fail?
5. How many Workspace users does a pilot with about 50 recorded hours a month need, and does Recall bill anything for signed-in bots beyond the recording hour?
