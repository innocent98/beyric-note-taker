# SOP: an optional title for a pasted-link meeting

**What shipped.** `POST /api/meetings` takes an optional `title`, and the "New meeting" form has an optional "Title (optional)" field. A pasted link used to be named only `Google Meet <code>`; now the owner can name it at creation. Branch `feature/meeting-optional-title` (PR opened 2026-11-14, merged by the lead). No migration. **The form field is lead-designed:** the designer's files in `docs/ui/` have no such field, so it reuses the existing `Field` and layout and invents nothing new.

## Why

A meeting made from a pasted link has no calendar event to take a title from (`Meeting.title` was documented as the calendar event's title), so every such meeting in the list reads "Google Meet abc-defg-hij". Adebayo asked for a way to say what a meeting is about.

## How

| Piece | What and why |
|---|---|
| Request | `NewMeeting.title: str | None = Field(None, max_length=1000)` in `app/api_schemas.py`. The 1000 only guards against a huge body (it is the calendar sync's own cap, `gcal_sync.TITLE_LIMIT`); the stored length is 200. `extra="forbid"` and `strict` stay, so a number or list is a 422 |
| Cleaning | The existing `titles.event_title` (`clean_title`, limit `titles.TITLE_LIMIT` = 200): control characters, newlines and tabs become spaces, space runs collapse, ends trimmed, cut with an ellipsis. Blank or only whitespace gives `None`, so today's fallback name shows. **No new cleaning code.** Calendar-created meetings go through the same function and are unchanged |
| Plumbing | `PlacementRequest` already had `title` (the calendar uses it) and `placement.place_bot` already stores `titles.event_title(req.title)`. So the change is only `body.title` -> `services.create_meeting(..., title)` -> `_create_meeting` -> `PlacementRequest(title=title)` |
| Markup | **Not stripped**, the same as calendar titles: stored and returned as typed. The pages render text (React escapes), and mail, Slack and the bell already pass names through `titles.alert_name`. Decision: stripping would be a second rule that the calendar path does not have |
| Form | `web/components/app/new-meeting-form.tsx`: a `Field` "Title (optional)", `maxLength` 200, placeholder "What is this meeting about? For example, Weekly planning", under the link row. Sent only when the trimmed value is non-empty (the key is left out otherwise); kept after a refusal like the link is |
| Types | `docs/openapi.json` regenerated with `PYTHONPATH=. python scripts/export_openapi.py`, `web/lib/api/schema.d.ts` with `npm run gen:api` (two lines: `title?: string | null`) |
| Rejected | Rejecting a title over 200 with a 422 (the form already stops at 200; a longer API call gets the calendar's cut instead, so a script is never refused for a long name). Editing the title after creation: not asked for |

## What's involved

`app/api_schemas.py` (`NewMeeting`), `app/api.py` (`create_meeting`), `app/services.py` (`create_meeting`, `_create_meeting`), `app/models.py` (comment on `Meeting.title`), `docs/openapi.json`, `web/lib/api/schema.d.ts`, `web/components/app/new-meeting-form.tsx`. Tests: `tests/test_api.py` (with a title, absent/null/empty/whitespace, cleaned, markup kept, 300 cut to 200, over 1000 and non-text refused, a refused link with a title stores nothing), `web/tests/screens.test.tsx` (sent when typed and trimmed, left out when blank, plain optional field with maxLength 200, kept after refusal). Capture script `scripts/capture_meeting_title.py`; captures `docs/fe-captures/meeting-title/01`-`14`. Guide: `docs/fe-integration-guide-owner-app.md` section 5.3a, UX item 20, two verification rows.

## Verification

On the Mac, bounded: `ruff check` and `ruff format --check` (0.17.3), `eslint` and `tsc --noEmit`, `export_openapi.py --check`, `pytest tests/test_api.py -k create` on a scratch Postgres 17, `vitest run tests/screens.test.tsx`. **Live:** `capture_meeting_title.py` ran the real migrations and the real app under uvicorn with Vexa and OpenAI faked, and saved the 14 exchanges; every body pasted into the guide is copied from them. **Not verified:** the field in a real browser, and the title in mail, Slack, the bell and exports (they read the stored title the same way calendar titles do).

## Operate / roll back

Nothing to run, no migration. Revert the PR: the field disappears and a `title` in a request becomes a 422 (unknown field) again. Titles already stored stay in `meetings.title` and keep showing.

## Follow-ups

- Renaming a meeting after it was made is not possible (calendar meetings neither); a rename would need its own route and design.
- The guide's other `POST /api/meetings` captures (`owner-app/33`, ...) predate `can_delete`, `shared_with_team` and the other newer fields; only the new section is from a fresh run.
