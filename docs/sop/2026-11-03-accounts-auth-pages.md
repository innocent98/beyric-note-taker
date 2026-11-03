# SOP: accounts pages in Next.js (stream D, auth screens)

## What shipped
The Next.js pages for the accounts flows the API already had: `/signup` (with `?invite=`), `/verify-email`, `/forgot-password`, `/reset-password`, `/pilot-full`, and the sign-in form with "Forgot password?", the Google button and the `/login?error=<code>` messages. Branch `feature/stream-d-auth-pages`, one PR (see the PR list). No backend, nginx or compose change. **Sign-up stays closed** (`signups_open` is false): the pages render the `closed` and `pilot_full` states from `GET /api/pilot`, and nothing here opens the pilot.

## Why
The mails the accounts backend sends link to `/verify-email?token=`, `/reset-password?token=` and `/signup?invite=`, and the Google callback redirects to `/pilot-full` and `/login?error=`. None of those pages existed, so a link in a mail would have 404'd. They had to exist before the admin opens sign-up (`docs/fe-integration-guide-pilot-accounts.md` section 3).

## How
| Piece | Decision |
|---|---|
| Source of truth | `Auth.dc.html` for layout and words, the API guide and its captures for behaviour. Where they disagree the API wins (list in `docs/ui/parity-report.md`, "Account screens (stream D)") |
| Routes | Added to `NEXT_PAGES`, the `proxy.ts` matcher (nonce CSP) and `APP_PATHS` (robots disallow, `noindex` on each page). nginx needs nothing: `location /` already goes to Next |
| Pilot state | A server component reads `GET /api/pilot` (`getPilot` in `lib/api/server.ts`, which forwards the visitor's cookie because `prefill_email` rides in one). `closed` and `full` draw their own screens (the design has neither) with the waiting list; `open` draws the form. If the pilot cannot be read the plain form is drawn and the API's own refusal decides. The API also decides at submit: a 403 `pilot_closed` or 409 `pilot_full` switches the screen and keeps the typed address |
| Invitations | `?invite=<token>` draws the form whatever the state (the API accepts an invited address while closed or full) and sends the token as `invite_token` |
| Sign-up copy | Never says whether an address is registered. After a 202 it says "We will email a confirmation link to X", with a 60 s wait before "resend the link" (the server drops a second mail inside a minute) |
| Confirm email | The page only draws a form; the button POSTs the token and the password the person chose (a GET must never confirm: mail scanners open links). It does not sign anyone in; "Go to sign in" follows. A dead link offers "Send a new link" (email field, `POST /api/signup/resend`); `pilot_full` shows the pilot-full screen and says the link was not used up |
| Reset | New password and confirm; a weak password leaves the form up (the link is not used up); a dead link sends the person to ask for another; "Password updated" says every device was signed out |
| Google | A full page load to `/api/auth/google/start?return_to=/`. On sign-up it is a button guarded by the Terms checkbox and sends `accept_terms=true` only when ticked; on sign-in it is a plain link (an existing account needs no terms; a new one comes back as `/login?error=terms_required` with a link to sign-up). Shown only when `GET /api/pilot` says `google_signin: true` |
| Errors | `lib/accounts.ts` shows the API's own `message` for the codes the guide marks user-written, **including the 422s** (`password_too_weak`, `terms_required`, `invalid_email`, `invalid_name`, `validation_error`) that `errorMessage` hides for the owner app; any other code gets the general line. Field-level codes appear under their field. nginx's own 429 page (not JSON) becomes `too_many_requests` in `lib/api/client.ts` |
| Password rules | The design's meter requires a number and an uppercase letter; the API requires neither. The screens show one live rule (8 to 128 characters) and "Not a common password" (only the server has the list). Drawing the designed rules would block valid passwords |
| Forms | Every account form (and the sign-in form) has `method="post"`: a submit before hydration (slow phone) used to be a GET that put the password in the URL and in nginx's log. A guard test checks every form |
| Components | `Field` gained `labelEnd`, new `Checkbox`, icons `mail` and `lock`, all in `/styleguide`; the account pieces are in `web/components/auth/`. `Google` mark and white tick are files in `public/icons/` (the guard test forbids colour literals in code) |
| Rejected | A disabled Microsoft button; faking a 6-digit code screen; showing "Confirm your email" as an answer to sign-in (the API deliberately gives none); a `terms_version` field in the client (the API stores the version server-side; the client sends `accept_terms: true`) |

## What's involved
`web/app/{signup,verify-email,forgot-password,reset-password,pilot-full}/page.tsx` (new), `web/app/login/page.tsx`, `web/components/auth/*` (new: `auth-head`, `password-field`, `google-button`, `mail-links`, `pilot-screen`, `waitlist-form`, `resend-link-form`, `signup-flow`, `verify-email-form`, `forgot-password-flow`, `reset-password-form`, `use-countdown`), `web/components/app/login-form.tsx`, `web/components/site/auth-shell.tsx` (header link), `web/components/ui/{field,checkbox,icon,index}.ts*`, `web/lib/accounts.ts` (new), `web/lib/api/{client,server,types}.ts`, `web/lib/routes.ts`, `web/proxy.ts`, `web/styles/{components,public}.css`, `web/public/icons/{google,check-white}.svg`, `web/scripts/mock-api.mjs`, `web/app/styleguide/page.tsx`, tests under `web/tests/accounts-*.test.ts*` plus `helpers.ts` and `screens.test.tsx`, and the docs (`docs/ui/parity-report.md`, `docs/checklist/master-checklist.md`, the scope line of the FE guide).

## Verification
Run on this Mac on 2026-11-03 in `web/`.
| Check | Result |
|---|---|
| `npm run lint`, `npm run typecheck` | clean |
| `npx vitest run` | 207 tests pass (79 new): the client calls against the captured responses, every screen state, the server pages, the guards (forms post, routes listed, writes carry the CSRF token) |
| `next build` | passes; the built app (`next start`) is what was looked at |
| Browser, 375 and 1280 px, against `web/scripts/mock-api.mjs` (replays `docs/fe-captures/pilot-accounts/`) | sign-up open (empty-submit errors, weak password from the API, success to "check your email"), closed, full with the waiting list (submit gives "You are on the list"), `?invite=`, `/pilot-full` pre-filled, `/verify-email` to the full-pilot screen, forgot, reset, sign-in with `?error=`, the Google link through the proxy to `/pilot-full`. No horizontal scroll at 375; no CSP violation |
| **Not verified** | CI on the pushed head (see the PR); a real mail; a real Google round trip; real FastAPI and nginx (the mock stands in, and the `prefill_email` cookie is not in it); Safari and Firefox; the live site |

## Operate / roll back
Nothing to configure and no new variable. Deploy as usual (rebuild the `next` image; not while a meeting is `bot_sent` or `processing`). To go back, revert the merge: `/signup` and the other new paths go back to being proxied to FastAPI (404). `node scripts/mock-api.mjs --pilot open|closed|full [--prefill a@b.test] [--no-google] [--google-outcome ok|pilot_full|<code>]` serves every state without Docker (magic values are in its header).

## Follow-ups
- **API gaps found (not changed here):**
  1. The invitation link carries only the token, and there is no lookup route, so the form cannot **lock the email to the invited address** as the guide asks (section 3). The field stays editable and says to use the address the invitation was sent to; a wrong address is a 400 `invite_invalid`. Fix on the API side: add the address to the invite link or a `GET` that returns it for a valid token.
  2. `GET /api/pilot` does not say whether password sign-up is enabled (`password_signup_enabled`, off until a real mail has been delivered), so the form cannot be hidden ahead of time (guide section 4). A visitor who submits gets 403 `password_signup_disabled` and its message. Suggest adding a `password_signup` field to `PilotOut`.
  3. The verify page and the pilot-full screen reached from it do not know the person's address (the link has only a token), so "Send a new link" and the waiting list ask for it again. Deliberate on the API side (no enumeration); noted so nobody looks for a bug.
  4. The brief mentioned a `terms_version` request field; the API takes `accept_terms: true` and stores the version itself.
- Designed and not built (no backend): "Set up your workspace", "Join your team" (team invite acceptance), Microsoft sign-in, "Keep me signed in", the 6-digit email code.
- Before sign-up opens (Adebayo): deploy this, deliver one real verification mail, turn `password_signup_enabled` on, and run a real Google round trip with a test account. `/signup` is `noindex` and disallowed in `robots.txt`; revisit when sign-up opens if it should be crawled.
- The landing page's sign-up buttons are still not drawn (Landing is outside this stream); the sign-in page now links to `/signup`.
- The landing and sign-in panel copy decision (keep as designed, revisit before Google verification) is unchanged.
