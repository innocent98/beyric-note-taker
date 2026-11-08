# SOP: Ask Beyric screens, the sidebar item and the transcript row highlight

PR: branch `feature/ask-beyric-screens` (number in the PR list). Front end only, **no migration, no server step** (a web-only merge restarts `next` only). Spec: `docs/superpowers/specs/2026-11-08-designer-v2-features-design.md` section 1 (1.6, 1.7, 1.8; PR #132). Audit rows B9, B14 to B18b, B25 and undrawn U1 to U4, U7 (`docs/ui/audit-2026-11-07-main-v2.md`). Checklist: 3F screens, 3B transcript highlight, B9 sidebar. The API is built in parallel by the `ask-backend` session; see "Contract status" below.

## What shipped
1. **`/ask`**, the Ask Beyric page (`web/app/ask/page.tsx`, `web/components/app/ask-view.tsx`): title, subtitle, question box with the magnifier and **Ask** (Enter asks), the suggestion chips, the scope line, the thread (newest first, page state only), the person's initials circle, "Searching your meetings...", the lead sentence, bullet points with source chips, "I could not find that in your meetings." with "Try asking about a person, a project, a decision or a date.".
2. **Sidebar item "Ask Beyric"** between Meetings and Tasks, enabled (`web/components/app/shell.tsx`, `NavSection` gains `"ask"`).
3. **Meeting page `?tab=transcript&line=N`**: opens the Transcript tab; row N (1-based, the Nth row of `parseTranscript`, that is the Nth `[Name]:` line, continuation lines merged into the row above) gets the pale yellow `#FFF4D6` (token `--highlight-row`), and about 300 ms after render the page scrolls to it smoothly with a 140 px offset. No time is shown anywhere. `line` out of range, missing, or not a positive integer: the tab opens with no highlight and no scroll. `?line` with the Notes tab, or a meeting without a transcript: ignored.
4. **Source chips**: label `{title} · {date_label}` plus ` · {owner_name}` when `owner_name` is set; they link to `/meetings/{id}` (target `notes`) or `/meetings/{id}?tab=transcript&line=N` (target `transcript`).
5. **Toast "That meeting is no longer available"** when a chip click lands on a meeting that answers 404 (see decision below).

## Why
The designer drew Ask Beyric and "Show in transcript"; the audit found no route, no component, no row anchor and no deep link. The spec fixed what each piece does and what the API sends; this is the screen half.

## How: decisions
| Topic | Decision |
|---|---|
| Row address | `id="tl-N"` on every transcript row, N from 1 in `parseTranscript` order (the designer used `tl-{time}`; no time is stored). The backend's `cite.line` must be the same count (told to `ask-backend`). |
| Highlight look | Designer's row: `padding 12px 10px; margin 0 -10px; border-radius 8px` on **every** row, background only on the cited one. The existing rows had `padding 12px 0`; the page now uses the designer's box, with the same visual result (the negative margin cancels the padding). |
| Deleted or unshared meeting | The spec says the chip opens the normal 404 page and the toast shows there. A chip click writes `sessionStorage["beyric:ask-chip"] = "/meetings/{id}"`; `app/not-found.tsx` renders `AskGoneToast`, which reads it once, compares it with the path, and flashes the toast. A flag for another path is cleared without a message. **Differs from the designer's file**, whose toast appears on the Ask page without leaving it (it checks its own list before navigating). Rejected alternative: a pre-flight `GET /api/meetings/{id}` before every chip click (an extra request on every click and a race, for a rare case). |
| Errors | `429` and `422` show the API's own message in the red card with no button; every other failure and a lost connection show "Beyric could not answer that." with **Try again**, which re-asks the same question in the same card. Never the "could not find" words. |
| Chips | `GET /api/ask/scope` returns `suggestions`; the page draws exactly those (the backend builds the name chip from the person's speakers and leaves it out when there is none). |
| Scope load fails | The page still draws the box; the chips and the scope line are left out (no invented numbers). A 401 goes to `/login`. |
| Thread | Page state only: a refresh clears it. Nothing is stored in the browser except the one-shot chip flag above. |
| Types | `AskScope`, `AskAnswer`, `AskCite` in `web/lib/api/types.ts` are **hand-typed to spec 1.7** (see "Contract status"). The error envelope is the one the real API sends (`{"error": {"code", "message"}}`, `web/lib/api/client.ts`), not the spec's `{"detail": ...}` sketch, which section 1.7 itself says the builder will match. |
| Routes | `/ask` added to `NEXT_PAGES`, the `proxy.ts` matcher and `APP_PATHS` (robots), as every Next page. |

## Lead-designed, not in the designer's file
(Listed under the spec's label. The designer drew nothing for these; they are built from the file's own patterns and tokens, and need Adebayo's eye in the PR.)
1. **Generated name chip** ("What did {Name} say?" from the most frequent speaker, left out when none): built by the API; the page only draws the list it is given (spec 1.8 item 1).
2. **No-meetings line** under the drawn scope line with zeros: "Nothing to search yet. Meetings appear here after they end." (grey, the scope line's size) (item 2).
3. **Error state**: red-bordered line "Beyric could not answer that." with **Try again** (same `state-fail` classes and tokens as Decisions "Could not load decisions") (item 3).
4. **The cap message** (and a refused question): the API's message in the same red card, no button (item 3).
5. **"Answers can be wrong. Check the sources."** one quiet grey line under each answer with points; none under "could not find" (that card has its own hint). No thumbs, no report (item 4).
6. **History**: none stored, as drawn (item 5).
7. **Phone and tablet: one column** (below 640 px: the box, chips and source chips wrap, the thread is full width, tighter card padding). The designer's file has no `@media` (item 6).
8. **Sidebar link** enabled with the page (item 7; the item itself is drawn).
9. **Owner on a teammate's chip** (` · {owner_name}`): the spec's lead-designed wording.
10. **Smaller lead-designed details of this PR:** singular words in the scope line ("1 meeting, 1 decision and 1 task"; the design prints "1 meetings"); a focus ring on the question box (teal border and `--teal-ring`, the app's input focus; the design has `outline: none` and no replacement); the toast on the 404 page instead of the Ask page (decision above); the chips and scope line hidden when the scope call fails.

## What's involved
| Area | Files |
|---|---|
| Page and view | `web/app/ask/page.tsx`, `web/components/app/ask-view.tsx`, `web/lib/ask.ts` (chip label, link, `parseLine`, the flag) |
| Meeting page | `web/app/meetings/[id]/page.tsx` (`line`), `web/components/app/meeting-view.tsx`, `web/components/app/transcript-panel.tsx` (row ids and highlight), `web/lib/transcript-scroll.ts` (300 ms, 140 px) |
| 404 toast | `web/app/not-found.tsx`, `web/components/app/ask-gone-toast.tsx` |
| Shell and routes | `web/components/app/shell.tsx`, `web/lib/routes.ts`, `web/proxy.ts` |
| API client and types | `web/lib/api/client.ts` (`ask.scope`, `ask.ask`), `web/lib/api/types.ts` |
| Styles | `web/styles/tokens.css` (`--highlight-row`), `web/styles/pages.css` (`.ask*`, `.transcript-line*`) |
| Mock and shots | `web/scripts/mock-api.mjs` (the two endpoints, `--ask live|empty|scope-failed`, questions by their words), `web/scripts/parity-shots.mjs`, `web/scripts/parity-ask.mjs`, `docs/ui/parity/after/app-ask*-{1440,768,390}.jpg`, `app-detail-line*`, `ask-styles.json` |
| Tests | `web/tests/ask.test.tsx` (33: the page, chips, errors, highlight, toast, sidebar), `web/tests/ask-page.test.tsx` |

## Contract status
The two endpoints are the spec's (section 1.7): `GET /api/ask/scope`, `POST /api/ask`. When this was written the `ask-backend` PR had **not** landed on `origin/main`, so `web/lib/api/schema.d.ts` was **not** regenerated and the three types above are hand-typed; the mock answers to the same shapes. After the backend merges: `npm run gen:api`, then replace the hand-typed types with `Schemas["..."]` names (the spec's Cite is `{meeting_id, title, date_label, owner_name, shared, target, line}`). Not exercised against the real API.

## Parity proof (full parity, nothing invented)
Method as `docs/ui/parity-report.md`: the designer's file served read-only beside the built Next app (standalone server) on the mock API, a real Chromium. `web/scripts/parity-ask.mjs` compares computed style and size of the same element on both pages at 1440 px after asking "What did we decide about pricing?" on each (result: `docs/ui/parity/after/ask-styles.json`, exit 0). Sizes within 1 px count as equal (the same text measures a fraction of a pixel apart); fields that follow the words are named, not hidden.

| Drawn element (design lines) | Result at 1440 |
|---|---|
| Title and subtitle (D:566-569) | same |
| Question bar: border, radius 14, padding, shadow, magnifier (D:571-573) | same |
| Input (D:574) and **Ask** button (D:575) | same (font, size, weight, colours, height) |
| Five chips (D:578-580) | same |
| Scope line (D:581) | same |
| Card, question row, initials circle (D:583-587) | same (card height differs only by our caveat line) |
| "Searching your meetings..." (D:588) | shot at 3 widths (`app-ask-loading-*`) |
| Lead, bullet dot, point text (D:591-601) | same (lead height follows the words) |
| Source chip with the video icon (D:602) | same (width follows the label: a day, not `mm:ss`) |
| "I could not find that in your meetings." and its hint (D:603-604) | shot at 3 widths (`app-ask-none-*`) |
| Transcript row highlight `#FFF4D6`, 8 px radius, `12px 10px` / `0 -10px` (D:287) | same |
| Sidebar order and item (D:2185) | same order; shown in every shot |

Screens shot against the designer at 1440 / 768 / 390 in `docs/ui/parity/after/` (design left, page right): `app-ask`, `app-ask-loading`, `app-ask-answer`, `app-ask-tasks`, `app-ask-none`, `app-ask-open-transcript`. Lead-designed, shot alone: `app-ask-failed`, `app-ask-retried`, `app-ask-limit`, `app-ask-long`, `app-ask-empty`, `app-ask-noscope`, `app-ask-gone` (the toast on the 404 page), `app-detail-line` (row 12 of 20 highlighted), `app-detail-line-out-of-range` (tab open, no highlight).

## Gaps
* **Words differ in the side-by-side shots**: the designer's answers are its own sample sentences with times; ours come from the mock in the spec's shape. Layout, blocks and states are what is compared.
* **The designer's file is not responsive**: its 768 and 390 shots are the file's own broken layout; the phone and tablet layout is ours (lead-designed 7).
* **The 404 toast is on the 404 page**, not on the Ask page as drawn (decision above).
* **Not run against the real API**, no FE integration guide from this PR: the guide holds captured responses, which `ask-backend` captures.
* The privacy page needs two sentences (the legal pages are not edited here): questions and the meeting text they need are sent to the model provider; we do not keep the question or the answer.

## Verification
* `npm run lint` (3 pre-existing warnings in `scripts/parity-meeting-delete.mjs`), `npm run typecheck`, `npx vitest run` in `web/`: 59 files, 893 tests passed (the new `ask.test.tsx`, `ask-page.test.tsx` among them; `routes.test.ts` checks `/ask` is in `NEXT_PAGES` and the proxy matcher; `guards.test.ts` checks no colour literal or inline style).
* Built app against the mock, shots and `parity-ask.mjs` as above; console errors seen in the shots are the mock's deliberate 502 and 429 answers.

## Operate / roll back
No config, no migration. Revert the PR; `/ask` goes back to FastAPI (404) and the sidebar loses the item. The not-found toast flag in `sessionStorage` is harmless without the page. The page works only with the backend's two endpoints; before they exist the scope call fails and the page shows the box alone (questions then fail with "Beyric could not answer that.").

## Follow-ups
* Regenerate `schema.d.ts` and switch to generated types after the backend PR lands.
* Privacy page: the two sentences above (lawyer read first).
* Suggested tasks "Show source" and Decisions "Show in transcript" reuse the same link (`?tab=transcript&line=N`); `citeHref` is the one place that builds it for Ask.
