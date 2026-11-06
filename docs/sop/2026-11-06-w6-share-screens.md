# SOP: Share notes by email, screens (AI workspace phase W6, PR 2 of 2)

## What shipped
The designer's **Share notes** dialog on the meeting page (participants with ticks, other people as chips, Language, Attach a copy, transcript tick, Message, recipient count, Cancel, Send notes), the toast ("Notes sent in French to 3 people"), the green **"Shared with <names>"** note above the participants, and the **`/unsubscribe`** page the mail's footer link opens. Stacked on PR 1 (`feature/w6-share-backend`, the API, migration 0029). PR: see the PR list.

## Why
PR 1 shipped the API and the mail; this is the part a person touches. The design drew the dialog, the toast and the "Shared with" line; it did not draw an address for a participant who is not on the team, or the opt-out page.

## How
| Piece | Decision |
|---|---|
| Dialog | A native `<dialog>` in the same style as the Team invite dialog (`components/app/share-dialog.tsx`, classes `tm-dialog` + `sh-*` in `styles/pages.css`). The form is mounted only while open, so a closed dialog adds nothing to the page. Opening resets it: every participant that has an address is ticked, language starts as the one the page shows (the designer's `shownLang`). |
| Participants | A participant is a **name** from the speaker labels. The page server-renders the team (`GET /api/team/members`); a participant whose name equals a member's name or one of their aliases (case blind) gets that member's address (spec section 2, row 5). Everyone else has none. |
| **Add email (LEAD-DESIGNED, not in the designer's file)** | Approved by Adebayo on 2026-11-05. A participant with no address shows an "Add email" link where the address would be; it opens one input in the row (the dialog's own input style, Enter or blur commits, Escape cancels). A valid address ticks the person and the address is sent with their name; an invalid one is refused and leaves them unticked. Built only from patterns already in the dialog. |
| Recipients | Chips for other people: Enter, comma or blur adds, as in the designer's file and the Invite dialog. Duplicates (a participant and a chip) are sent once. More than 10 shows "11 recipients: send to 10 at most" and disables Send (the API also refuses: `too_many_recipients`). |
| Language hint | **Deviation from the designer:** the drawn hint "A link to the original is included." is not true (no public links, decision D7; spec row 11). It reads "Recipients get the notes translated, with the original below." |
| Transcript tick | Drawn only when the meeting has a transcript (a tick that does nothing is a dead control). |
| Message | 500 characters (`maxLength`), trimmed. |
| Send | `POST /api/meetings/{id}/shares`. 202 closes the dialog and toasts. A refusal (`share_limit`, `translation_limit`, `mail_unavailable`, ...) stays in the dialog as the API's own words. |
| Toast | The designer's words. **Lead-designed variant:** when the share is `waiting_translation` it says "Translating the notes to French. They will be sent to 2 people when ready", because saying "sent" would be false. |
| Shared with | `GET /api/meetings/{id}/shares` (server-rendered, then refreshed after a send; while a share is waiting or a mail is `pending`, every 5 s for 5 minutes). The note lists names (or addresses), once each, and leaves out anyone whose mail did not go (`not_sent`, `failed`) and failed shares. It sits above the participants as drawn. |
| Who sees the button | The owner of a ready meeting (`can_share`). A teammate reading a shared meeting sees none (the API would 404). |
| **Unsubscribe page (LEAD-DESIGNED, not in the designer's file)** | `/unsubscribe?token=...`, built from the verify-email page (same shell, head, button). Opening does nothing; the button posts the token. Says what stops and what does not (sign-in and password mail still arrive). Used, expired or missing token: "This link does not work" with where to ask. Added to the Next page list and the proxy matcher. |

## What's involved
- New: `web/components/app/share-dialog.tsx`, `web/app/unsubscribe/page.tsx`, `web/components/auth/unsubscribe-form.tsx`, `web/tests/share-dialog.test.tsx` (22), icons `send` and `close`.
- Changed: `meeting-view.tsx` (button, dialog, toast, "Shared with"), `download-menu.tsx` (a `trailing` slot so the button sits after Download), `app/meetings/[id]/page.tsx` (team members and shares), `lib/api/client.ts` and `types.ts`, `lib/routes.ts` and `proxy.ts` (`/unsubscribe`), `styles/pages.css`, `scripts/mock-api.mjs` (shares, opt-out, meeting 9 with a non-member participant), `scripts/parity-shots.mjs` (five new screens), `tests/helpers.ts`.
- FE guide: `docs/fe-integration-guide-share.md` (the page is now built).

## Verification
- `npm run lint`, `typecheck` and `vitest run` (659 tests) pass. The dialog and page tests are fed the real captures in `docs/fe-captures/share/`.
- **Parity proof:** `web/scripts/parity-shots.mjs` against `mock-api.mjs`, design on the left and Next on the right, at 1440, 768 and 390, in `docs/ui/parity/after/`: `app-share-dialog-*`, `app-share-sent-*` (the toast and "Shared with"), `app-detail-*` (the new header button), `app-translate-download-*` (the Download menu, unchanged), and Next alone for the lead-designed screens `app-share-add-email-*`, `app-unsubscribe-*`, `app-unsubscribe-done-*`.

| Part | Design | Next | Status |
|---|---|---|---|
| Share notes button (primary, send icon, after Download) | drawn | same | match |
| Dialog: title, sub-title, close, card width 520, rows, chips, select, segmented control, tick, textarea, footer | drawn | same, 72 px from the top | match |
| Language select with globe | drawn | same globe as the notes toolbar | match |
| Recipient counter and "Choose at least one recipient" | drawn | same, plus the 10-person limit | match + addition |
| Send button disabled colour | pale green | disabled button of the app | same intent |
| Language hint for another language | "A link to the original is included." | "...translated, with the original below." | **deviation** (D7, spec row 11) |
| Toast | drawn | same | match |
| "Shared with ..." above the participants | drawn | same | match |
| Participant avatar initials | two letters for the one-word name "Adebayo" (AD) | one letter (A), as everywhere in the app | existing difference of the Participants card, not new |
| Participant with no address | not drawn (all have addresses) | "Add email" | **lead-designed** |
| Unsubscribe page, translating toast variant | not drawn | built | **lead-designed** |

**For the designer (gaps):** a participant without an address, the opt-out page, a toast for a share that waits for its translation, the 10-person limit message, and a "Shared with" state when some mail did not go.
- The mock does not run a worker, so "pending" and "waiting_translation" screens are covered by the component tests, not by shots.

## Operate / roll back
- No new `.env` variable, no migration. Revert the PR to remove the screens; PR 1's API stays.
- **Merge order:** PR 1 first (it needs one nginx edit after its merge, see its body), then this one. Until this PR merges no user can send a share, so no mail goes out.

## Follow-ups
- A cancel link for a share still queued (the API has no route yet).
- A real send is still **not verified live** (PR 1, ML1).
