# SOP: the live landing, llms.txt and JSON-LD stop claiming encryption we do not have

**What shipped.** A copy-only hotfix on the current live public pages. The landing's first security card no longer says "Encrypted end to end ... at rest with AES-256"; `llms.txt` and the JSON-LD no longer say "no public sign-up" or "coming soon: calendar". Branch `fix/landing-security-copy` (PR opened 2026-11-14, not merged by this work). Web only: no API, migration, config or server step, so no FE guide change.

## Why

The public-pages audit (`docs/superpowers/specs/2026-11-14-public-pages-audit.md`, gaps 1 and 3, risk R2) found statements that are not true. Adebayo said "Yes, fix both" on 2026-11-14. A security claim we cannot back is the classic complaint and a risk for Google's OAuth verification, which reads the live pages.

## What is true (checked, not assumed)

| Claim | Finding | Source |
|---|---|---|
| Encrypted in transit | **True.** Probed the live site on 2026-11-14: TLS 1.2 and TLS 1.3 handshakes succeed, TLS 1.0 and 1.1 are refused (TLS ends at our own nginx, no CDN in front). The card says "TLS" without a version number, like the Privacy Policy, so a later nginx change cannot make it stale | `curl --tlsv1.x`, `openssl s_client -tls1_3`; `deploy/README.md` section 6 (certbot) |
| Encrypted at rest (AES-256) | **False for recordings, transcripts and notes.** No disk or database encryption is set up | `docs/sop/2026-11-02-public-pages.md:21` |
| What *is* encrypted at rest | AES-256-GCM on **secrets only**: calendar refresh tokens (Google, Microsoft), pasted calendar links, the Slack webhook address, the Linear key | `app/vault.py`, `app/integrations.py:95,288`, `app/calendar_connect.py:377`, `app/microsoft_connect.py:353` |
| End to end | **False.** The server reads the audio, transcript and notes to produce them | the product itself |
| Sign-up | Not "none": opened by Adebayo 2026-11-13 (`signups_open`, `password_signup_enabled`), capped at 10 accounts and 5 hours each. It is a runtime switch (`app_state`), so public copy says a *limited pilot* and "the sign-up page says whether places are open; otherwise a waiting list", not "open" | `docs/HANDOFF-master.md` (2026-11-14 block A), `web/components/auth/pilot-screen.tsx` |
| Calendar | Built and live (auto-join since 2026-11-06). Outlook is built but untested against real Microsoft, so `llms.txt` names Google Calendar only | handoff; audit L5 |

## Before and after

| File | Before | After |
|---|---|---|
| `web/components/landing/content.ts:15` (card title) | Encrypted end to end | Encrypted in transit |
| `web/components/landing/content.ts:15` (card body) | Data is encrypted in transit with TLS 1.2 or higher and at rest with AES-256. | Traffic to the site is encrypted with TLS, and the sign-in tokens for connected calendars are stored encrypted. |
| `web/app/llms.txt/route.ts` | It is in a private pilot with a small group of testers; there is no public sign-up yet. | It is in a pilot with a limited number of accounts, free during the pilot, with an hours limit on each account. The sign-up page says whether places are open; when the pilot is full or closed there is a waiting list. |
| `web/app/llms.txt/route.ts` | Coming soon: connect Google Calendar so the note taker joins chosen meetings by itself. | Connect Google Calendar so the note taker joins the meetings your rules choose by itself. |
| `web/components/site/json-ld.tsx` offer | Free during the private pilot | Free during the pilot, with an hours limit on each account |

Comments in those three files now point at this SOP. The Privacy Policy's Security paragraph (`web/components/legal/privacy-content.tsx:109`, "Traffic to the site is encrypted with TLS ... Calendar access credentials are encrypted before they are stored") was already true and is untouched; the new card says the same thing.

## What's involved

`web/components/landing/content.ts`, `web/app/llms.txt/route.ts`, `web/components/site/json-ld.tsx`, test `web/tests/landing-security-copy.test.tsx` (new). The designer's files under `docs/ui/` still hold the original lines; they are reference material and were not edited.

## Verification

On the Mac, bounded: `npm run lint` (0 errors; the 3 old warnings in `scripts/parity-meeting-delete.mjs`), `npm run typecheck`, and `vitest run` on `tests/landing-security-copy.test.tsx`, `tests/parity.test.tsx`, `tests/routes.test.ts`. The new test pins the card text, fails if `AES`, "end to end" or "at rest" appears anywhere in the rendered landing, FAQ or `llms.txt`, and pins the `llms.txt` and JSON-LD wording. CI runs the rest. Not looked at in a browser: the change is words only.

## Operate / roll back

Merge deploys by itself (web image). Revert the PR to undo. No server step.

## Follow-ups (found, deliberately not changed here: owner's call, listed in the audit)

- Live FAQ "Can we keep using Jira, Linear or Asana? Yes. Tasks, projects and sprints can be sent" and the tools strip: only tasks go to Linear; Jira and Asana are not built (audit L13, L17).
- "Visible to everyone ... under a clear name" while users can type any bot name and the chat notice is off (audit R3); "Never used for training" rests on OpenAI's terms only (R7); "Reminders ... attendees get a brief" is an opt-in mail to team members only (L12).
- Hero and closing band still say "Sign in" now that sign-up can be open (handoff, 2026-11-14 block).
- `SITE_DESCRIPTION` (`web/lib/site.ts`, also in `app/templates/public_base.html`) names Meet and Zoom only; Teams works. Understates, so not a risk; left for T3.
- The Privacy Policy line "no other company receives the recording" and the missing Skribby sub-processor are a separate, lawyer-read PR.
- Real infrastructure for "at rest" (disk or database encryption) is not built; if it is built, a card can say so then.
