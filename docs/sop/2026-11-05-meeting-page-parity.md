# SOP: Meeting detail page to parity with the designer's file (D12, meeting page)

## What shipped
Branch `feature/meeting-page-parity`, PR opened against `main` (not merged). The live meeting page now has the designer's header duration, the full **Details** block (Date, Platform, **Duration**, Template), **Participants**, and the **Decisions and review** side card with counts. Four read-only fields were added to `GET /api/meetings/{id}`. No migration (0018 stays with the time zone stream, 0019 was not needed).

**The recurring meeting (series) page was not built.** See "Waits for W2 / W8".

## Why
Adebayo (2026-11-04) saw the live page differ from `docs/ui/Beyric Note Taker redesign/`. Rule: full parity, nothing invented, no dead buttons on the meeting page.

## Difference table (live before, designer's file, at 1440 / 768 / 390)
| Part | Before | Designer | Now |
|---|---|---|---|
| Header line | date, platform | date, platform, duration | date, platform, duration ("46 min", "1 hr 5 min", "In progress" while working, "0 min" if nothing was recorded) |
| Details | Date, Platform, Template | + Duration, Series, Milestone | + Duration. Series and Milestone: W8 |
| Participants | none | list with initials circles | list from the speaker names in the transcript; block hidden when there are none |
| Decisions and review | one sentence of text | two rows (count + View, count + Review) | the two rows with the designer's words; View goes to `/decisions`, Review to `/review` |
| Action items | notes bullets | task rows (box, due, owner, "View board") or "None noted." | notes bullets kept (that is the only data); an empty section is a dashed "None noted." box. Task rows: W2 |
| Open questions | notes bullets | bullets with "Add to next agenda" toggle in a series | bullets; the toggle: W8 |
| Share notes button | none | green button | not mine (W6) |

## How
- `app/api_schemas.py` `MeetingDetail`: `duration_seconds` (the meeting's `recorded_seconds`), `participants` (names in `[Name]:` lines of `speaker_transcript`, first-spoken order, from the same parser the exports use), `decisions_count` (decisions not hidden), `review_open_count` (review items not checked). Both counts are 0 unless the meeting is ready.
- `app/api.py` `_detail`: `_participants`, `_workspace_counts` (two count queries, only for ready meetings).
- `web/components/app/meeting-view.tsx`: header, Details, Participants (avatar tones reuse the transcript's), `ExtractionCard` is now the designer's card. While the notes are still being read, or the reading failed, the counts are unknown, so the card keeps the earlier plain sentence instead of showing a false "No decisions" (designer gap).
- `web/lib/format.ts` `durationLabel`; `web/styles/pages.css` side-card rules; `web/components/ui/icon.tsx` `eye` icon (the designer's).
- The older "An older meeting: ..." sentence is gone: a skipped meeting now shows its counts.

## Verification
- pytest `tests/test_api.py -k detail`, `tests/test_accounts.py`, the route enumeration and OpenAPI tests; ruff check and format; `npm run lint`, `typecheck`, `vitest` (543 pass; older captures get the new fields' empty values in `tests/helpers.ts`).
- Parity shots (`web/scripts/parity-shots.mjs` against `mock-api.mjs`, mock values are the mock's own) at 1440/768/390: `docs/ui/parity/after/app-detail-*.jpg` (plus processing and failed). Side by side at 1440: header, Details, Participants, Decisions and review match in position, size, colour and words. Differences left: dates are in the zone stored in Settings (the page passes `timeZone={await getTimeZone()}` to `AppShell`, like the other pages; the file says UTC); avatar colours go by order, the file gives each sample person a colour; the "Decisions you removed..." line under Rewrite notes is earlier wording, not in the file.
- Not done: the FE integration guide was not touched (no live capture of the new fields was taken; shapes are in the OpenAPI file and the tests).

## Waits for W2 / W8 (not drawn, no dead buttons)
Action items as task rows (done box, due date, owner initials or "Needs owner", "N tasks created... · View board"): **W2 Tasks**. Open questions "Add to next agenda": **W8**. Details "Series" and "Milestone": **W8**. Share notes and "Shared with ..." note in Participants: **W6**.
**Recurring meeting page** (`Recurring` cards open it: next meeting, unfinished tasks, finished since last meeting, questions carried over with Mark resolved, other agenda items with an add input, past meetings, attendees): every block needs tasks, carried questions or an agenda (W2/W8), and nothing links a meeting to a recurring series yet (the cards come from calendar events and are not links). Only "next meeting" and a title exist, so nothing was built.

## Operate / roll back
Deploy as usual. Server steps: none. Revert the PR; the API fields are additive.

## Follow-ups for the designer
Pending/failed reading state of the card; Participants when there are no speaker names; per-person avatar colour rule; whether the "Decisions you removed..." line stays.
