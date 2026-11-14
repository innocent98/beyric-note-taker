# SOP: the recording-consent box at sign-up, and a one-time card for existing accounts

**What shipped.** A required, never-pre-ticked second checkbox at sign-up (password, team invitation and Google), enforced by the API as well as the form; its time and wording version stored on the account (`users.recording_ack_at`, `users.recording_ack_version`, migration `0046`); and a one-time, non-blocking card on the Meetings page for accounts that were made before the box. Branch `feature/recording-consent-box`; PR number in the PR list (opened 2026-11-14, not merged by this work).

**Lead-designed, not in the designer's files.** `docs/ui/` has no such box or card. The box is the designer's own terms `Checkbox`; the example opens from the app's own link button; the card borrows the quiet bordered box of the calendar tip. The wording is the owner's, approved 2026-11-14, and must not be reworded. The designer can redraw it; the words and the placement are the part to keep.

## Why

The app records people who never agreed to it. Beyric cannot ask them; the person who sends the bot can. So the account holder confirms, once and in writing, that they will tell attendees and get consent where the law requires it, and we keep when and which wording (the way the terms are kept). Legal text is the owner's lawyer's: the privacy policy, the terms and `TERMS_VERSION` are untouched.

## How

| Decision | What and why |
|---|---|
| Exact words | Box: "I understand that I'm responsible for telling the people in my meetings that Beyric Note Taker will record and transcribe them, and for getting their consent wherever the law requires it." Link **How to tell attendees** shows: "One line in the invite or at the start of the call is enough, for example: 'I'm using Beyric to record this call and take notes.'" (`web/components/auth/recording-consent.tsx`) |
| Server rule | `POST /api/signup` takes `accept_recording` (default false, so a client that leaves it out gets the same plain `422 recording_ack_required` as one that sends false, not `validation_error`). Asked after `terms_required` and before the password check, before any lookup, so the answer is the same for an address that has an account |
| Google | The consent screen is Google's, so the box sits on `/signup` and the button is inert until both boxes are ticked. The start route takes `accept_recording=true`; the flow cookie carries flags (`<state>.t` terms, `.r` recording, both `.t.r`), so no new cookie and no state in the database. A NEW account without it is refused `/login?error=recording_ack_required`; an existing account signs in without either box and is NOT marked as having ticked it. A flow that started before the deploy (old `<state>.t` cookie, 10-minute life) is told the new code and starts again |
| Evidence | Same shape as the terms: `recording_ack_at` (timestamptz null), `recording_ack_version` (text null, `signup.RECORDING_ACK_VERSION = "2026-11-14"`, next to `TERMS_VERSION`). Set only at creation by a ticked sign-up (and when an unconfirmed password account is submitted again). A sign-up for an address that already has a verified account touches nothing |
| Existing accounts | A card on the Meetings page (`recording-ack-card.tsx`) with the same words and an **I understand** button: `POST /api/settings/recording-ack` (logged in, CSRF, no body), one conditional UPDATE so the first click is the evidence and a double click changes nothing. The session payload gets `user.recording_acknowledged` (boolean) so the page knows whether to draw it. **It gates nothing**: the bot, auto-join, send now and every route work with the flag false. Gating the few pilot accounts that already record could silently stop their meetings, so that was left for the owner to decide (decision for the lead to review) |
| Card stays until clicked | The card shows on every visit until the person clicks, not only the first one. Hiding it after one view would leave no evidence |
| Rejected | A pre-ticked box (the owner said no); a gate on existing accounts (see above); storing the flag in `user_settings` (the terms live on `users`, so the evidence sits beside them); a separate Google cookie (the flag suffix needs no new cookie) |

## What's involved

- Backend: `app/signup.py` (`RECORDING_ACK_VERSION`, the check, the stored fields), `app/google_signin.py` (flow-cookie flags, `_create`), `app/api_accounts.py` (the new query parameter and body field), `app/api_schemas.py` (`SignUp.accept_recording`, `UserOut.recording_acknowledged`, `RecordingAckOut`), `app/api.py` (`_session_state`), `app/api_settings.py` (`POST /api/settings/recording-ack`), `app/models.py`.
- Migration `alembic/versions/0046_recording_ack.py`: two nullable columns, additive. **Number 0046**, the next after the multi-team migrations 0044 (#182) and 0045 (#184). Its `down_revision` is `"0045"` (`0045_auto_share_team`): it was first written against 0043 while #182 (0044) and #184 (0045) were open, and moved to 0045 when they merged.
- Frontend: `web/components/auth/recording-consent.tsx` (words, `RecordingBox`, `HowToTell`), `web/components/auth/signup-flow.tsx` (both forms and the Google guard), `web/components/auth/google-button.tsx`, `web/lib/accounts.ts` (error code, field, login message, `googleStartUrl`), `web/components/app/recording-ack-card.tsx`, `web/app/page.tsx` (one line where the Meetings page is drawn; **#183 moves that page to `/meetings`, so this line moves with it**), `web/lib/api/client.ts`, `types.ts`, `schema.d.ts`; `docs/openapi.json` regenerated.
- Docs: `docs/fe-integration-guide-recording-ack.md`, edits to the pilot-accounts and team guides, captures in `docs/fe-captures/recording-ack/` (`scripts/capture_recording_ack.py`).
- Tools: `scripts/capture_*_api.py` now send the new field; `web/scripts/mock-api.mjs` refuses a sign-up without it and has `--recording-ack pending` to draw the card.

## Verification

On the Mac, bounded, on a scratch Postgres: `ruff check .` and `ruff format --check .`; `pytest tests/test_recording_ack.py tests/test_signup.py tests/test_google_signin.py tests/test_admin.py tests/test_api.py` plus `tests/test_migrations.py -k "0046 or single_head or matches_models"`; web `npm run lint`, `npm run typecheck`, and `vitest run` on `accounts-pages`, `accounts-api`, `accounts-server`, `recording-ack-card`, `api-client`, `welcome`, `meetings-calendar`, `meetings-recurring`. Not run: the full suites, the Next build, e2e (CI does).

`tests/test_recording_ack.py` proves: sign-up without the box refused (false, missing, terms missing too) and no row made; the same answer for an existing address; with the box the time and `2026-11-14` stored; Google new account refused without the flag and created with it; an existing Google account signs in unmarked; the cookie flags and the old `.t` cookie; the session flag false for an old account and true after the button; the button stores once and keeps the first time; it needs login and CSRF and writes only the caller's row; an account that never acknowledged can still create a meeting and send the bot. A migration round trip (0046 up, down, up) keeps older rows null.

Live: the real app was driven over HTTP and every exchange saved (`docs/fe-captures/recording-ack/`, guide section 7). **Not verified:** Google's own consent screen and token exchange (stood in); the sign-up page and the card were looked at in the in-app browser against the mock API, not against a real deployment; no real browser pass through `/login?error=recording_ack_required` against the real backend (the redirect itself is captured).

## Operate / roll back

Run `alembic upgrade head` (the deploy does). No setting, no switch. To roll back, revert the code; the two columns can stay (the old code ignores them). `alembic downgrade` drops them and loses the evidence. A person who pasted nothing wrong sees nothing different except the second box.

## Follow-ups

- Decision for the owner: whether existing accounts should ever be gated (today: never).
- The privacy policy and terms may want a sentence on this; that is the lawyer's, untouched here.
- Bot chat notice when the bot joins (C18, the next PR) is the other half of this.
