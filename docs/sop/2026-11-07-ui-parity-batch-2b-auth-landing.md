# SOP: UI parity batch 2, PR B (Auth and landing)

PR: `feature/ui-parity-batch-2b` (number in the PR list). From the 2026-11-07 audits (`docs/ui/audit-2026-11-07-pages-emails.md` rows A06, A14, A15, L02, L04, L12; `docs/ui/audit-2026-11-07-app.md` A69, A70).

## What shipped
1. **"Set up your workspace"** (`Auth.dc.html:142-169`) at `/setup`: workspace name, optional calendar rows, "Continue to Beyric", "Skip for now".
2. **"Join your team"** (`Auth.dc.html:171-208`): the presentation of the invitation screens rebuilt to the file (banner, locked email, name, password, "Join Beyric", header "Have an account? Sign in").
3. **Sign-in field errors** and the valid-email check as drawn (`Auth.dc.html:370-371`).
4. **Landing sign-up buttons** "Get started" (header), "Get started free" (hero) and "Create your account" (closing band), all to `/signup`.
5. **Labels:** the sign-up, confirm-email, resend, pilot-closed and waitlist pages are marked lead-designed in their SOPs (see "Labels").

## Why
The audit found the workspace step and the join screen drawn but unbuilt or different, sign-in errors shown as one alert, and the landing without the sign-up buttons (the old reason, "sign-up is closed", is stale: `/signup` exists and draws the closed or full screen with the waiting list).

## How: the decisions
| Topic | Decision |
|---|---|
| Does sign-up create a team? | Yes: `accounts.add_user_defaults` calls `teams.ensure_team`, so every account has a team of one called "My team" (`app/models.py` `Team.name`). The step therefore only renames it (`PATCH /api/team {name}`, owner or admin). No backend change |
| When it appears | Email confirmation does not sign the person in. The confirmation page marks the tab (`lib/setup-pending.ts`, sessionStorage, same pattern as `share-pending.ts`), and the sign-in form, on success, sends that tab to `/setup` instead of `/` and removes the mark. So it shows once, after the first sign-in, in the tab that confirmed. Confirm on another device, or close the tab: the person lands in the app as before. `/setup` is never a gate: "Skip for now" goes to `/`; a member who cannot rename (`can_manage` false) is redirected to `/` by the page |
| Calendar rows | Google: a real Connect (saves a typed name first, then the same `POST /api/calendar/connect` as Settings, with the unverified-app notice reused from Settings; Google's callback returns to Settings with its usual message). Shown "Connected" and not clickable when already connected. The whole block is left out while the server has the calendar off. Outlook: drawn off with one "Not available yet." line (project rule) |
| Step label | "STEP 2 OF 2" as drawn (sign-up is step 1) |
| Join your team | `signup-flow.tsx` draws the file's form when the invitation is a team's: banner with the inviter's avatar and "{name} invited you to the {team} workspace", "Join your team", locked Email (text with a lock, not an input), Full name "Your name", Password "Create a password" with the app's rules, "Join Beyric" / "Joining...". Same state, same `signup` call with `invite_token`, same errors. `accept-invite.tsx` keeps every branch and error and takes the same banner, title and locked email; its signed-in button is "Join Beyric" |
| Header link | `/signup?invite=` shows "Have an account? Sign in" as drawn (any invitation link, team or waiting list, because the server page only knows there is a token) |
| Sign-in errors | "Enter your email." / "Enter a valid email, like you@company.com." under the email, "Enter your password." under the password; the API's answers (wrong password, lockout) stay in the alert |
| Landing | `.ld-get-started` (dark), hero Button, `.ld-create` (white). "See the app" is not built: it links to the prototype file, which is not a page |

## What's involved
- New: `app/setup/page.tsx`, `components/auth/{setup-workspace,invite-banner}.tsx`, `lib/setup-pending.ts`, tests `setup-workspace.test.tsx`; `/setup` added to `lib/routes.ts` (pages and robots) and the `proxy.ts` matcher.
- Changed: `components/app/login-form.tsx`, `components/auth/{signup-flow,accept-invite,verify-email-form}.tsx`, `components/app/settings-calendar.tsx` (exports `UnverifiedNotice`), `components/landing/landing.tsx`, `app/signup/page.tsx`, `styles/public.css`, tests (`accept-invite`, `accounts-pages`, `screens`, `parity`), `scripts/parity-shots.mjs`.
- No API, schema or migration change; no FE guide shape change.

## Verification
- `npm run lint` (0 errors), `npm run typecheck`, `npx vitest run`: green.
- Parity: `web/scripts/parity-shots.mjs` against `mock-api.mjs`, 1440 / 768 / 390, design left and page right, in `docs/ui/parity/after/`: `landing`, `signin`, `signin-errors`, `setup-workspace`, `signup-team-invite`, `invite-new-person`, `invite-signin`, `invite-accept`.
- Not run against the real backend or a real Google connect.

## Parity table
| Screen | Matches the design | Differences |
|---|---|---|
| Set up your workspace | step label, title, sub, Workspace name "e.g. Beyric", "Connect your calendar (optional)" and its line, Google row, "Continue to Beyric", "Skip for now" | Outlook shows "Not available yet." instead of "Connect"; no header link, as drawn |
| Join your team (sign-up with a team link) | banner, title, sub, locked Email, Full name, Password, "Join Beyric", "Have an account? Sign in" | one avatar (an invitation names only the inviter); the app's two password rules, not the file's three; **a terms checkbox stays** (the API needs `accept_terms`); no Google button (that sign-up does not carry the invitation) |
| `/accept-invite` | same banner, title, locked email | a new person's button says "Create your account" and leads to the form above (the file has one form, ours two pages) |
| Sign in | field errors under the inputs, valid-email check | the API's wrong-password words (a documented decision) |
| Landing | header "Get started", hero "Get started free", closing "Create your account" | "See the app" not built |

## Labels (lead-designed, not in the designer's file)
The designer drew no screen for these; they are ours: sign-up's closed and full screens and the waiting-list form, the "Check your email" screen after sign-up (the file draws a 6-digit code screen instead; the mail it follows is a link), the confirm-email page `/verify-email` and "Send a new link", the `/login?error=` messages. Marked in `2026-11-03-accounts-auth-pages.md` and `2026-11-03-pilot-accounts-signup.md`. The W3 SOP's wrong claim about `/accept-invite` is corrected there.

## Operate / roll back
Server steps before merge: none. Revert the PR to roll back.

## Follow-ups
- Google first sign-in for a new account does not pass through `/setup` (the callback redirects to `/` on the server); decide whether it should.
- Designer to confirm the "Not available yet." Outlook row and the terms box on the join screen.
- "See the app" (hero) has no target.
