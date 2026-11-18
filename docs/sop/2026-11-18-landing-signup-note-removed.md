# SOP: the "invite-only" sign-up note is gone from the public pages

**What shipped.** The line "Sign-up is invite-only for now. If you have an invitation, create your account; if not, join the waiting list." no longer appears on the landing page (under the hero button and in the closing band) or on `/pricing` (hero and closing). `/llms.txt` now says sign-up is open. Branch `fix/landing-signup-note`; PR number in the PR list. No migration, no API change.

## Why

Sign-up has been open since 2026-11-13 22:02 WAT (`signups_open` and `password_signup_enabled` true), and billing is on for everyone with a trial for new accounts. The note was written for the closed pilot and had become false. Adebayo spotted it on 2026-11-18. The handoff already listed the stale hero copy as an open item.

## How

| Choice | Reason |
|---|---|
| Delete the note instead of rewording it | Any replacement sentence (a trial, a number of hours) would be a new claim that can go stale. The buttons say "Get started" and "Create your account". |
| The waiting list is not lost | `/signup` draws the form, or the closed or full screen with the waiting list, from `GET /api/pilot`. The landing page never needed to say it. |
| `/llms.txt` states only "Sign-up is open at /signup", a trial then a monthly plan, prices on `/pricing` | True today; the test refuses the words "invite-only", "waiting list" and "limited number of accounts". |

## What's involved

`web/components/landing/landing.tsx` (the `SignupNote` component and its two uses removed), `web/components/landing/content.ts` (`SIGNUP_NOTE` removed, header comment), `web/components/pricing/pricing.tsx` (two uses and the import removed), `web/styles/public.css` (the `.ld-signup-note*` rules), `web/app/llms.txt/route.ts`, `web/tests/landing-v2.test.tsx`, `web/tests/landing-security-copy.test.tsx`.

## Verification

`tsc --noEmit` and `eslint` on the touched folders: clean. `vitest run` on `landing-v2`, `landing-security-copy`, `pricing-real`: 38 tests pass. The page itself was not opened in a browser by this work. CI runs the rest.

## Operate / roll back

Revert the PR. Nothing on the server changes.

## Follow-ups

- Terms still say "free during the pilot" and the Privacy Policy still mentions the pilot's waiting list: lawyer text, not touched here.
- If sign-up is ever closed again, the landing page will not say so; `/signup` will.
