# Microsoft publisher verification: everything needed to start now

Prepared 2026-11-20. Docs only; nothing here changes the app. Public repo: no secrets, no personal addresses.

**How to read the marks.** Every statement about what Microsoft requires carries a tag like `[M1]` pointing to the table in section 1 (URL and the day it was read). A statement about our app carries a file and line. **NOT VERIFIED** means a Microsoft page did not say it; it is never filled from memory. Unlike the Google pack, every Microsoft page below was fetched in full by me directly from Microsoft Learn (the Microsoft Learn docs fetch tool), not through a summary agent.

## 0. The short version

| Question | Answer |
|---|---|
| Is there a Microsoft review like Google's sensitive-scope review for our permissions? | None of the pages I read describes one. Publisher verification checks **who the publisher is**, not what the app does; the badge "doesn't imply or indicate quality criteria" [M1]. |
| What does verification give us? | A blue "verified" badge and the publisher name on the Microsoft consent prompt; without it the prompt shows "Unverified" [M4][M3]. For work or school users it also decides whether they may consent at all (section 3). |
| **Does it apply to an app that serves personal Microsoft accounts only?** | The pages do **not** say. They never mention a "consumer-only app" rule. Section 3 gives exactly what each page says. Short form: our registration is "work or school plus personal" (multitenant by Microsoft's definition), our sign-in URL is the personal-only `consumers` endpoint, the documented consent block concerns users "in tenants that aren't the tenant where the app is registered", and no page says what a personal-account user sees. **NOT VERIFIED.** |
| Do we need it before launch? | Not for personal accounts, as far as the pages show. **Yes before we open work or school accounts** (`MICROSOFT_AUTHORITY=common`), see section 3. It is free and slow, so start now (the reason you asked). |
| Cost | Microsoft does not charge for publisher verification and no licence is needed [M1]. Partner Center enrolment pages show no fee for enrolment itself; creating a **new** work account asks for a credit card ("no charges at the time of sign-up") [M12]. We already have a work tenant per the handoff, so a card should not be needed (open question 6). |
| How long? | After the Partner Center account is verified: "minutes" [M1]. The Partner Center verification: "in most cases ... three to five business days", longer if Microsoft asks for more [M6]. Nothing else is stated; none invented. |
| What only Adebayo can do | Sign in to Partner Center and Entra, accept Microsoft's agreements, scan the ID credential, add DNS records, press **Verify and save**. Section 7. |
| What blocks us | Facts only he has: company registration details, which account owns the Azure app registration, a business mailbox on `beyrictech.com`. `docs/launch/verification-open-questions.md`. |

## 1. Sources read

All fetched in full by me on 2026-11-20 through the Microsoft Learn docs fetch tool, unless a row says otherwise.

| Tag | URL | Used for |
|---|---|---|
| M1 | https://learn.microsoft.com/en-us/entra/identity-platform/publisher-verification-overview | What it is, the requirements list, cost, national clouds, the step-up consent note, FAQ |
| M2 | https://learn.microsoft.com/en-us/entra/identity-platform/mark-app-as-publisher-verified | The exact steps in the app registration |
| M3 | https://learn.microsoft.com/en-us/entra/identity-platform/howto-configure-publisher-domain | Publisher domain, the domain file, what the consent prompt shows, multitenant definition |
| M4 | https://learn.microsoft.com/en-us/entra/identity-platform/application-consent-experience | The consent prompt: "Unverified" versus the badge |
| M5 | https://learn.microsoft.com/en-us/entra/identity-platform/troubleshoot-publisher-verification | Error codes (`MSANotSupported`, `PublisherDomainMismatch`, ...), where to find the Partner One ID |
| M6 | https://learn.microsoft.com/en-us/partner-center/verification-responses (page title "Understand the verification process in Partner Center") | The five checks, documents, timing, roles |
| M7 | https://learn.microsoft.com/en-us/partner-center/enroll/mpn-create-a-partner-center-account (page title "Enrolling in the first partner program") | Enrolment steps |
| M8 | https://learn.microsoft.com/en-us/partner-center/account-settings/reg-number-id | Which countries must give a registration ID |
| M9 | https://learn.microsoft.com/en-us/partner-center/enroll/complete-identity-verification | Identity check with a government ID and the Authenticator app |
| M10 | https://learn.microsoft.com/en-us/entra/identity-platform/supported-accounts-validation | The four "supported account types" values |
| M11 | https://learn.microsoft.com/en-us/partner-center/account-settings/create-account | Prerequisites list and roles (Marketplace and Microsoft 365 enrolment page; used only for its prerequisites) |
| M12 | https://learn.microsoft.com/en-us/partner-center/enroll/create-work-account | Work account requirements, the credit card note |

**Not read:** Microsoft's page on adding a custom domain to Entra (`.../fundamentals/add-custom-domain`, linked from M1); the Partner Center "submit a verification appeal" page; any Microsoft page that says what a personal-account user sees when an app is unverified. Everything that depends on those is marked.

## 2. What Microsoft requires (the requirements list, M1)

1. A **Partner One ID** of a Microsoft AI Cloud Partner Program (CPP, formerly MPN) account that has **completed verification**, and it must be the **partner global account (PGA)**, not a partner location Partner One ID [M1][M5 `MPNGlobalAccountNotFound`].
2. The app must be registered with a **Microsoft Entra work or school account**. "Apps that are registered by using a Microsoft account can't be publisher verified" [M1]; the portal answers `MSANotSupported` [M5].
3. The Entra tenant where the app is registered must be **associated with the PGA** (if it is not the primary tenant of the PGA, there is a multitenant-account step) [M1].
4. The app must have a **publisher domain** set, which cannot be `*.onmicrosoft.com` [M1].
5. The **domain of the email address used during CPP account verification** must match the app's publisher domain, or be a DNS-verified custom domain added to the Entra tenant [M1]. Mismatch gives `PublisherDomainMismatch` [M5].
6. The person who starts it needs a role in **both** places: Entra **Application Administrator** or **Cloud Application Administrator**; Partner Center **CPP Partner Admin** or **Account Admin** [M1][M2].
7. That person must sign in with **multifactor authentication** (in the same session; MFA enabled but not performed fails with `InteractionRequired`) [M1][M5].
8. The publisher must accept the **Microsoft identity platform for developers Terms of Use** [M1].
9. Not supported in Azure AD B2C tenants or national clouds [M1].
Cost: none; no licence [M1].

## 3. The consumer-only question, answered only from the pages

Our setup (from our code and spec): the Azure app registration allows "work or school plus personal Microsoft accounts" (`AzureADandPersonalMicrosoftAccount`, spec decision D1, `docs/superpowers/specs/2026-11-04-outlook-oauth-design.md:17`); our authorize and token URLs use the `consumers` endpoint, so only personal accounts can sign in today (`app/config.py:218,699` default `consumers`; `app/microsoft_oauth.py:69`; the effective audience is the narrower of registration and endpoint, spec line 68).

What each page says:

| Page | Exact point | Bearing on us |
|---|---|---|
| M1 | "Publisher verification primarily is for developers who build multitenant apps that use OAuth 2.0 and OpenID Connect." | "Primarily" is not "only". It does not exclude personal-account apps, and it does not require them. |
| M3 | A multitenant app "supports user accounts that are outside a single organizational directory", and "might support both Microsoft Entra work or school accounts and personal Microsoft accounts". | By this definition our registration **is** multitenant, even though the endpoint we use admits personal accounts only. |
| M3 | "For multitenant apps that were created after November 30, 2020, only publisher verification status is shown in an app's consent prompt." If not verified the prompt "shows unverified". | Our registration (created later than that date) falls in this rule **for the consent prompt as the page describes it**. The page does not say whether personal-account prompts follow the same layout. |
| M4 | "If the app isn't publisher verified, 'Unverified' is displayed instead of a publisher name." The prompt is described for Microsoft Entra ID consent (work or school). | The page does not mention personal accounts. **NOT VERIFIED** that a personal-account user sees "Unverified". |
| M1 note | Since November 2020, if risk-based step-up consent is on, users can't consent to most newly registered multitenant apps that aren't verified, when the app asks beyond basic sign-in and profile and requests consent "from users in tenants that aren't the tenant where the app is registered"; the screen warns the app was created by an unverified publisher and is risky. | This concerns users **inside other tenants**. A personal account is not in a tenant, so the documented block does not describe it. That personal accounts are exempt is **my reading, not a sentence on the page**. |
| M2, M5 | `MSANotSupported`: "This feature isn't supported for Microsoft consumer accounts. Only applications registered in Microsoft Entra ID by a Microsoft Entra user are supported." | This is about **who registers the app**, not who signs in. Our registration must be made by an Entra work account (open question 2). It does **not** say that apps serving only consumers are exempt. |
| M10 | A fourth value exists, "Personal Microsoft accounts only" (`PersonalMicrosoftAccount`), with its own validation limits (for example no app roles, client secrets limited to two). | Changing our registration to that value is possible; no page connects that value to publisher verification either way. We do not recommend changing it: it would remove the route to work accounts (spec D2). |

**What this means, as plainly as the pages allow.**
- Publisher verification is **not documented as required** for an app that only personal accounts use.
- Whether a personal user sees an "Unverified" label is **not documented**. Our own spec says the same ("not documented (M0 S2)", spec line 437), and the capture that would show it (M0) has not been run (checklist: "Live captures after the M0 run" open).
- It **is** the documented gate for work or school users in tenants with the step-up policy [M1], and admins can write consent policies that use verification status [M1 benefits].
- So the safe order is: keep `consumers` now; finish verification; then switch the endpoint to `common`.
- Starting now costs nothing [M1] and takes days [M6]; waiting is the only way to be slow.

## 4. What we ask Microsoft for (from the code)

| Permission (delegated) | Where | Why |
|---|---|---|
| `Calendars.Read` | `app/microsoft_oauth.py:63`; used by `app/graph_client.py:351,376` (`GET /me/calendarView`, `GET /me/events/{id}`) | Read the main calendar so the note taker can join meetings with a video link |
| `User.Read` | `app/microsoft_oauth.py:62`; probe and label in `app/microsoft_connect.py` (`GET /me`, `GET /organization` for a work account's verified domains) | Account label and the stable user identity; for a work account the organisation's domains, used for a "same organisation" test |
| `offline_access` | `app/microsoft_oauth.py:61` | A refresh token so the sync keeps working; without it the connect is refused (`app/microsoft_connect.py:297`) |
| `openid`, `profile` | `app/microsoft_oauth.py:59-60` | Give the ID token its tenant id and user id; the code comment says they "expose no data" (`:56-57`) |

Nothing else is asked; the spec says a new scope later is a new consent for every user (`app/microsoft_oauth.py:56`). No admin consent is requested (spec decision "Admin consent consequences"). The app registration needs no admin consent for these delegated permissions per the spec's reading of Microsoft's permissions page (spec line 70; **that page not re-read here**).

**Data we keep from an Outlook event**: the same row as Google (`app/models.py:610-655`): title, times, meeting link and platform, organiser flag, attendee count (never addresses), own reply, status. Description and location are read only to find the link and dropped. Rows go two days after the event and on disconnect. The Microsoft refresh token is encrypted with AES-256-GCM (`purpose="ms_refresh_token"`, `app/graph_client.py:269`, `app/vault.py:1-12`). Disconnect deletes our copy and the credential; Microsoft gives an app no way to withdraw its own access, so the policy tells the person to remove Beyric in their Microsoft account's app permissions (`privacy-content.tsx:63`). Retention list: `privacy-content.tsx:96-109`.

**Public pages.** Privacy `https://notes.beyrictech.com/privacy` (section "Microsoft user data", `privacy-content.tsx:57-63`), Terms `https://notes.beyrictech.com/terms`, home `https://notes.beyrictech.com/`; all three returned 200 on 2026-11-20. The app registration should carry the privacy and terms URLs (spec line 314; **the portal field was not re-read here**).

**What reviewers or users could flag.** Microsoft's pages I read do not describe any review of the policy or the app text. Still, these are factual points worth fixing:
- Microsoft section is accurate to the code. The sentence "No Microsoft URL is named" for the permissions page was left because the page was unconfirmed (SOP M7); the standard link is `https://myapps.microsoft.com`, which M4 names on the consent prompt ("review and remove any non-Microsoft applications"). Adding that link to `privacy-content.tsx:63` is a one-line copy change [M4].
- Terms say the service joins "on Google Meet and Zoom" (`terms-content.tsx:26`) while Teams works (Teams wording T3 pending, checklist). Not a Microsoft issue.
- The landing says Outlook is "new, and still being tested" (`content.ts`), which is honest and should stay until the M0 live run exists.

## 5. The domain file, and one trap

`web/public/.well-known/microsoft-identity-association.json` exists and is **live**: GET `https://notes.beyrictech.com/.well-known/microsoft-identity-association.json` returned 200, `Content-Type: application/json; charset=UTF-8`, body with `associatedApplications` and the real application (client) ID `3f83e7a2-618d-4ef1-8bab-baa1bc5c3884` (public, not a secret; read 2026-11-20). Microsoft accepts `application/json`, `application/json; charset=utf-8` [M3]; a different type is rejected [M3].

**The trap.** M3 says to host the file at `https://<your-domain>/.well-known/microsoft-identity-association.json`, where the domain is **the domain you are verifying as the publisher domain**. Ours is hosted on `notes.beyrictech.com`. That works only if the publisher domain is `notes.beyrictech.com`. If the publisher domain is `beyrictech.com` (the apex, which is what the Partner Center mailbox will use), the file would have to be at `https://beyrictech.com/...`, **and we do not know that a site answers there**.

**The way around, per M3:** if the tenant has a DNS-verified custom domain, you choose it from "Select a verified domain" and no file is needed. M1 also lets the Partner Center mailbox's domain be "a DNS-verified custom domain that's added to the Microsoft Entra tenant". So the one DNS step that does both jobs is: add `beyrictech.com` as a custom domain in the Entra tenant (a DNS TXT record), then select it as the publisher domain. The handoff already says this (`docs/HANDOFF-master.md`, Microsoft block). M3 adds: "You're not required to maintain the resources that are used for verification after you verify a domain", so the file may stay or go.

## 6. Does Beyrichq LTD qualify, and what Microsoft asks for

What the pages say Microsoft verifies in Partner Center [M6]: the business "is legally registered with an active registration at the stated address" and account information is accurate. Five checks, all must pass:

| Check | What it needs [M6][M9] |
|---|---|
| Email | A business mailbox of the primary contact that can receive mail; "an employee business email address, not a personal or free email account" |
| Identity | At least one user proves identity with a **valid government-issued ID** and the **Microsoft Authenticator** app (a verified credential); the name on the ID must match the Partner Center account name exactly; finish within **30 days** of the request [M9] |
| Employment | The primary contact is an employee: "domain documentation" that matches the account: business name, address, domains, purchase and expiry dates, "from an authorized registrar" |
| Business | The business is registered with the proper authority in its region: formation documents "such as articles or certificate of incorporation, business license, registration"; name and address must match official records "exactly, without spelling errors or abbreviations" |
| Additional | Microsoft may run extra checks and ask for a questionnaire |

Enrolment [M7][M11]: start as a **Global admin** with a work account ("Don't use ... Microsoft Accounts (MSA, Hotmail, or Outlook.com)" [M7]); pick the legal entity; accept the MAICPP agreement ("the enrollee has the authority to sign agreements on the organization's behalf"); verification follows. Enrolment is active only when vetting shows "Authorized" [M7]. A business registration number is among the prerequisites listed on M11; M8 lists the countries where a registration ID is **mandatory**; **Nigeria is not in that table**, and the page says for countries not listed it is optional ("company registration ID. Don't enter a personal ID") [M8].

**Whether Beyrichq LTD qualifies:** the pages give no eligibility list by company type or country beyond the checks above. The Privacy Policy says Beyrichq LTD is in Lagos, Nigeria (`privacy-content.tsx:11`). Whether it holds an active registration at that address, and which documents it can produce, **only Adebayo knows** (open question 6); I cannot and do not assert it qualifies. Nothing on the pages says a Nigerian company is excluded; equally nothing says it is included; this is **NOT VERIFIED either way**.

One more fact worth knowing: Partner Center "currently supports logging in with only one tenant context" [M12], and publisher verification needs the app's tenant associated with the PGA [M1]. The simple path is to enrol Partner Center with the **same** work tenant that owns the app registration.

## 7. Adebayo does this himself

The lead cannot sign in to Microsoft, accept agreements, scan an ID or press Verify; these steps are his. Paths marked "(M#)" are from those Microsoft pages; paths marked **NOT VERIFIED** were not on a page I read.

1. **Confirm who owns the app registration.** Open https://entra.microsoft.com signed in with the work account of the Beyric tenant (the handoff names the tenant `beyric.onmicrosoft.com`), go to Entra ID, App registrations, find the app with client ID `3f83e7a2-618d-4ef1-8bab-baa1bc5c3884`. If you cannot see it, it was created with a personal account: M5 says apps missing from the list "may have been created using ... a personal/consumer account". A personal-account registration "can't be publisher verified" [M1]; recreate it under the work account and send the new client ID to the lead.
2. **Check your roles.** In Entra: Application Administrator or Cloud Application Administrator. In Partner Center: Microsoft AI Cloud Partner Program Admin or Account Admin. Turn on and use MFA [M1][M2].
3. **Create a mailbox on `beyrictech.com`** in your own name (not `info@` or `admin@`), the one you will use for Partner Center. M6 asks for "an employee business email address". Generic role addresses are rejected in the work-account page [M12].
4. **Add `beyrictech.com` to the tenant as a custom domain and verify it with a DNS TXT record** (M1 requires a DNS-verified custom domain or a matching publisher domain). Click path: **NOT VERIFIED** (the page on this was not read); the usual place is Entra admin center, Entra ID, Domain names, Add custom domain, then copy the TXT record to your DNS host.
5. **Set the publisher domain** (M3): Entra admin center, Entra ID, App registrations, your app, Overview, under Manage choose **Branding**, under **Publisher domain** choose **Configure a domain** (or **Update domain**), then **Select a verified domain** and pick `beyrictech.com`. It must not be `*.onmicrosoft.com` [M1].
6. **Enrol in Partner Center** (M7): https://partner.microsoft.com, join the program, choose **Partner**, sign in with the work account, select the legal entity, read the Microsoft AI Cloud Partner Program agreement, **Enroll**. Enter the company registration number if asked (optional for Nigeria per M8).
7. **Complete verification** (M6): Partner Center, Settings, Account settings, **Legal info**, **Verification Summary**. Upload the business documents (certificate of incorporation or similar) and the domain documentation; do the identity step with a government ID and the Microsoft Authenticator app (M9); confirm the email. Expect three to five business days; answer any email from Microsoft promptly (the primary contact receives them [M6]).
8. **Wait until the overall status says "Authorized"** [M6].
9. **Find the Partner One ID** that is of type **PartnerGlobal**: partner profile, Identifiers, Microsoft Cloud Partners Program tab [M5].
10. **Mark the app verified** (M2): Entra, App registrations, your app, **Branding & properties**, **Add Partner ID to verify publisher**, enter the PartnerGlobal Partner One ID, **Verify and save**. It runs "a few minutes"; success shows a blue verified badge next to the publisher display name [M2]. Fix errors with the code list in M5.
11. **Test it** (M2): sign in to Beyric through Connect Outlook with a test account; to force the consent prompt again add `prompt=consent` for testing only, never hard-coded [M2]. Tell the lead what the personal-account consent screen shows (this fills the NOT VERIFIED in section 3).
12. **Tell the lead.** Then, as server steps the lead will give you: set `MICROSOFT_PUBLISHER_VERIFIED=true` (`app/config.py:706`; it drives the in-app explainer) and, only after a test with a work account from another tenant, `MICROSOFT_AUTHORITY=common` (checklist: "Work accounts: Partner Center verification and publisher domain, then `MICROSOFT_AUTHORITY=common`").

Not needed for this: Microsoft 365 Publisher Attestation and App Certification are separate programs; M1 says publisher verification is "the first step" for them and they are optional.

## 8. Follow-ups

- Run the M0 live capture with a real personal account, to see and record what Microsoft shows a personal user (closes the NOT VERIFIED in section 3).
- Add `https://myapps.microsoft.com` to the Microsoft disconnect sentence (section 4).
- After verification: tick the checklist's Microsoft items; record the Partner One ID type (not the number) and the date.
- If a work-account customer reaches "needs administrator approval" before verification, the admin-consent link is a known follow-up in the Outlook spec's follow-ups list (`docs/superpowers/specs/2026-11-04-outlook-oauth-design.md:413`).

## 9. Verification table

| Part | State |
|---|---|
| Requirements, steps, Partner Center checks, timing (sections 2, 6, 7) | Read directly from Microsoft Learn on 2026-11-20 (M1 to M12), full pages |
| Consumer-only question (section 3) | The pages say what is quoted; they do not answer it. What a personal user sees: NOT VERIFIED; the M0 capture has not been run |
| Domain file live | Checked 2026-11-20 with a plain GET: 200, `application/json; charset=UTF-8` |
| Our permissions and data handling (section 4) | Read from the code on `main` at `817c2578`; not run against real Microsoft in this task (the Outlook SOPs say nothing is verified against real Microsoft yet) |
| Custom-domain click path | NOT VERIFIED (page not read) |
| Whether Beyrichq LTD qualifies | NOT VERIFIED: needs facts only Adebayo has |
| App registration owner (work account or personal) | Unconfirmed in the handoff; step 1 settles it |
