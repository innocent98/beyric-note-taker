# SOP: W0.5 Delete button on the meeting page (lead-designed, not in the designer's file)

## What shipped
The UI for `DELETE /api/meetings/{id}` ([W0.5 SOP](2026-11-05-w05-meeting-delete.md)): a **Delete meeting** control and its confirmation on the meeting page, a toast on the list afterwards, and a `can_delete` flag on the meeting detail. Branch `feature/w05-delete-button`, one PR (not merged). No migration.

**Label: lead-designed, not in the designer's file.** `docs/ui/` has no meeting delete or confirmation. Adebayo asked for it to be added ("There's no delete button yet"), so the lead session designed it from the designer's own parts and nothing new. It is on the designer-gap list: the designer should confirm or redraw it.

## How (each part reuses a drawn pattern)
| Part | Borrowed from | Measured (1440) |
|---|---|---|
| Red text button "Delete meeting", left, below the meeting | "Delete template" / "Delete task": 13 px, weight 500, `--danger`, no background, padding 6 px 0 | computed styles **identical** to the design's "Delete template" |
| Inline ask: "Delete this meeting? ... the notes, the transcript, any translations, the decisions and review items taken from it, and the recording. This cannot be undone." / "The hours it used stay counted." with Cancel and a red "Delete meeting" | the Decisions "Remove from log" ask (tinted box, radius 10, padding 12 14, Cancel + red button 32 px, radius 7) | the box and button follow the app's tokens exactly as the existing Decisions ask does; the design's literal tint (`#FDF3F2`) differs from the token (`#FDECEA`), as it already does on Decisions |
| Toast "Meeting deleted" on the list | the designer's toast (`useToast`/`Toast`) | shown once, via a `sessionStorage` flag (`lib/deleted-flag.ts`) because a toast does not survive navigation |
| Visibility | `can_delete` on `GET /api/meetings/{id}` = owner and not `bot_sent`/`processing` (same idea as `can_cancel`, `can_end`: hidden, not disabled) | |
| Errors | 409 `meeting_active` and 502 `recording_delete_failed` show the API's own text in the page alert and keep the page; 409 refetches; 404 counts as deleted | |
Task counts: the ask names none yet; the line arrives with W2 (not built here).

Rejected: a modal `Dialog` (the page uses one for Cancel, but the drawn destructive confirm for a record is the inline ask); a disabled button while working (the page hides its other actions).

## What's involved
`app/api_schemas.py`, `app/api.py` (`can_delete`), `docs/openapi.json`, `web/lib/api/schema.d.ts`, `web/lib/api/client.ts` (`meetings.remove`), `web/lib/deleted-flag.ts`, `web/components/app/meeting-view.tsx`, `web/components/app/meeting-list.tsx`, `web/styles/pages.css` (`.meeting-delete`, `.meeting-delete-ask`, tokens only), tests `web/tests/meeting-view.test.tsx`, `web/tests/screens.test.tsx`, `tests/test_api.py`, `tests/test_team_visibility.py`, mock `web/scripts/mock-api.mjs`, shots `web/scripts/parity-meeting-delete.mjs`, `docs/ui/parity/meeting-delete/`, captures `docs/fe-captures/meeting-delete/` (now include `can_delete`), `docs/fe-integration-guide-meeting-delete.md`.

## Verification
- vitest: the ask names what goes and that hours stay counted and calls nothing until confirmed; 204 goes to `/` and sets the flag; 404 counts as deleted; the real 409 and 502 texts show and the page stays; the button shows on a ready and a failed meeting and not for `bot_sent`, `processing` or `can_delete: false`; the list says "Meeting deleted" once.
- pytest: `can_delete` true for a finished meeting, false while active and for a teammate.
- Shots at 1440, 768 and 390 (`docs/ui/parity/meeting-delete/`, built app against the mock API, no console errors), with the designer's "Remove from log" ask beside for comparison.
- Not verified: the full flow against the real backend in a browser.

## Operate / roll back
Revert the PR; the API from #86 is unaffected. `can_delete` is additive.

## Follow-ups
Designer to confirm or redraw (button position, ask wording). W2: add the task count line to the ask.
