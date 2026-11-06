# SOP: W8 Series, screens (PR 2 of 2, stacked on the backend PR #104)

## What shipped
The Next screens for recurring meetings, all built from the designer's file (`docs/ui/Beyric Note Taker redesign/Beyric Note Taker.dc.html`, the `isSeries` screen, the Recurring cards, the meeting page's Series row and question pills, the "Sprints and reminders" card): the **Recurring cards** on Meetings (now live, linking to the page, with the unfinished-tasks and open-questions chips), the **Series page** `/series/{id}`, the **Series row** in the meeting's Details, the **"Add to next agenda / On next agenda / Resolved" pills** on a recurring meeting's open questions, and the live **reminders row** of Settings. Backend: SOP `2026-11-06-w8-series-backend.md`. Branch `feature/w8-series-screens`.

## Why
Spec section 7, checklist D8, D12 (the "recurring meeting detail page" Adebayo reported missing), D13 (Series row). The backend gave the data; nobody could see or use it.

## How
| Piece | What it does |
|---|---|
| `components/app/recurring-cards.tsx` | `GET /api/series` replaces the calendar-only list. A card is a link (`/series/{id}`) with "Next: ...", "N unfinished tasks", "N open questions"; nothing is drawn when empty, loading or failing, as before. Cadence and dates are written by the server in the viewer's saved zone |
| `app/series/[id]/page.tsx`, `components/app/series-view.tsx` | The Series page: Next meeting and **Send agenda to attendees**; Unfinished from last time; Finished since last meeting; Open questions carried over with **Mark resolved**; Other agenda items (add on Enter or Add, remove); Past meetings and Attendees. A task row opens the same `TaskDrawer` as the board. Lists are the viewer's: two teammates see different pages (server rule) |
| `components/app/open-questions.tsx` + `notes-html.tsx` | On a meeting that is in a series, the "Open questions" part of the notes is replaced by the question list with the designer's pill; a meeting in no series keeps the notes' own list (the prototype draws no pill there) |
| `meeting-view.tsx` | "Series" row in Details linking to the page |
| `settings-reminders.tsx` | The reminders row (switch, timing select) is live for owners and admins (`PATCH /api/team`); anyone else sees the real value with the controls off. The sprint rows stay off with one line: "Sprint settings are not available yet." |
| `lib/api/*`, `lib/routes.ts`, `proxy.ts` | client methods, types from the regenerated schema, `/series/[id]` as a Next page |

**Lead-designed, not in the designer's file** (Adebayo said "Yes, design them", 2026-11-06; built from the file's own dialog, select, amber-pill and settings-row patterns, for the designer to confirm or redraw):
1. **"Add to recurring meeting"** (`series-picker.tsx`, a native dialog from the Series row of Details; owner of a ready meeting only; "Change" beside the link when it is already in one). Pick one of the team's recurring meetings, "New recurring meeting" and a name, or "Not in a recurring meeting". Reads the list when the dialog opens.
2. **"Make this a recurring meeting?" chip** (`recurring-hint.tsx`, above the notes and details): the amber pill with "Make recurring" and "Not now" (hidden per meeting in this browser only). Drawn when the backend's new `suggest_recurring` is true (the owner reused a link). Backend added to #104 (`POST /api/meetings/{id}/recurring`).
3. **"Email me agendas and reminders"** (`settings-reminder-mail.tsx`): the person's own switch, a row in the Sprints and reminders card under the team's reminders row, live for everyone (`GET/PUT /api/settings/reminders`).

## Parity table (screenshots `docs/ui/parity/after/app-series-*`, `app-detail-*`, `app-settings-*`, `app-meetings-*`, design left and ours right, at 1440, 768 and 390)
| Part | Design | Built | Note |
|---|---|---|---|
| Header: back link, name, "cadence · platform" | yes | yes | platform omitted for a series with none |
| Next meeting + Send agenda button | yes | yes | hidden when no next date; the button is off with a reason when nobody would receive it (`agenda_recipients` 0) |
| Sprint line under Next meeting ("Opens Sprint 13") | yes | **no** | needs Sprints (W9); nothing invented |
| Unfinished / Finished / Carried questions / Agenda / Past meetings / Attendees | yes | yes | empty lines use the designer's words |
| "Mark resolved" | always enabled | off when the viewer may only read | `can_update`; a title says why |
| Task row actions | opens the task | opens the board's drawer | |
| Toast "Agenda sent to N attendees" | yes | yes | singular for one |
| Recurring card: name, cadence, three chips, chevron, click | yes | yes | |
| Meeting Details: Series row (link) | yes | yes | Milestone row waits for W4 |
| Question pill, three states | yes | yes | off for non-owners |
| Settings reminders toggle and 3-timing select | yes | yes, live | select shown only when on, as designed |
| Sprint length and sprint toggle | yes | off | W9 |
| **Add to recurring meeting** (Series row entry and dialog) | **no** | built, **lead-designed, not in the designer's file** | shots `app-meeting-add-series-*` |
| **Make this a recurring meeting?** chip | **no** | built, **lead-designed, not in the designer's file** | shots `app-meeting-recurring-hint-*` |
| **Email me agendas and reminders** switch | **no** | built, **lead-designed, not in the designer's file** | row in `app-settings-*` |

## Gaps for the designer
- **Please confirm or redraw the three lead-designed pieces** (Add to recurring meeting dialog, the "Make this a recurring meeting?" chip, the personal reminder mail switch): none is in the file.
- No state for a series nobody on the team can read yet, a failed "Send agenda" (the error shows in the page's alert), or a 0-recipient button (drawn off with a title).
- The prototype's sidebar and date formats differ from the live ones (earlier SOPs).

## Verification
`npm run lint` (0 errors), `npm run typecheck`, `npx vitest run` (all of `web/`, 672 tests: new `series-view`, `open-questions`, `settings-reminders`, `meeting-series`, rewritten `recurring-cards`; routes and guards updated). Screens were driven against `web/scripts/mock-api.mjs`, which replays the real captures of `docs/fe-captures/series-w8/`. **Not verified:** a real browser against the real API, Safari and Firefox, dark mode, keyboard use of the page beyond the tests, the live mail.

## Operate / roll back
No server step. Revert the PR; the backend stays valid. Recurring calendar events appear as cards about an hour after the sync sees them (or when their notes are written): until then the Meetings page shows no card for them, where the old list showed every recurring calendar event.

## Follow-ups
Designer confirmation of the lead-designed pieces; Sprints (W9) for the sprint line; milestone row (W4).
