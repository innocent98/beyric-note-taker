# SOP: sign-in and account screens wear the shared public header and footer

**What shipped.** Every screen drawn through `AuthShell` (sign-in, sign-up, forgot and reset password, confirm email, accept invite, stop emails, pilot full, and the signed-in "Set up your workspace" step) now sits inside `PublicShell`: the same header and footer as the landing page, `/pricing`, `/privacy`, `/terms` and the 404 page. Branch `feature/auth-shared-header`; PR #230.

## Why

PR #224 made the landing page's header and footer the one shell for the public pages and left the account screens outside it, asking Adebayo. His answer (2026-11-17): yes, login and sign-up join the shared header. No redesign, no new copy.

## How

| Decision | Detail |
|---|---|
| One change point | `web/components/site/auth-shell.tsx` now returns `<PublicShell>` around the split layout. The nine pages that use it needed only the removal of the `action` prop. |
| Old top row and footer removed | The logo-plus-link bar (`auth-bar`) and the small footer (`auth-foot`: copyright, Privacy, Terms, Help) are gone; the shared header has the logo, Sign in and Get started, and the shared footer has Privacy, Terms and Contact. The `action` prop ("New to Beyric? Create account", "Back to sign in") was dropped with the bar, so those sentences no longer appear; the header's Sign in and Get started cover both directions. |
| Landmarks | `PublicShell` supplies the one `<main id="main">` and the skip link; the form column's `<main>` became a `<div>` (two `main` landmarks would be invalid). |
| Layout | `.auth-split` is `min-height: calc(100vh - 69px)` (header 68 px + 1 px border), so the form stays centred in the window under the header; the form column and the teal panel lost their own `100vh`. The teal product panel is kept as designed. |
| Signed-in owner | `/setup` is for a signed-in owner, so its header reads "Open the app" as everywhere else. |

Rejected: a second header variant for the account screens (the point of #224 is one); keeping `action` as a link under the form (new placement, new design).

## What's involved

- `web/components/site/auth-shell.tsx`, `web/styles/public.css` (auth rules), the nine pages under `web/app/` that use `AuthShell`.
- Tests: `web/tests/public-shell.test.tsx` renders each account screen and compares its header and footer to the shell's, checks one `main`, one header, one footer and no `auth-bar`/`auth-foot`, and checks that every `AuthShell` user is listed.
- No Python, migrations, billing files or config.

## Verification

- Lint and type-check (CI's commands) and `web/tests/public-shell.test.tsx` run locally; full suite left to CI.
- Dev server, screenshots at desktop and 390 px of `/login`, `/signup` and the other account screens against the landing header, in the session notes folder `auth-header/`.

## Operate / roll back

Nothing to run. Revert the PR to restore the old split layout with its own top row and footer.

## Follow-ups

- Decide whether the teal product panel stays now that the header carries the sign-in links (kept here).
- `web/app/error.tsx` stays outside the shell: a client error boundary that has to render even when the shell's session call fails.
