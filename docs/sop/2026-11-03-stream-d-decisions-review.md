# SOP: Decisions and Review screens (stream D, D3 and D4)

## What shipped
The two screens the AI workspace API (W1, PR #49) had no face for: **Decisions** (`/decisions`) and **Review** (`/review`), with their links in the sidebar, the **Review count** beside the link (`badges.review_open`), the extraction state on the meeting page, and a transcript tab the Review screen can open (`/meetings/<id>?tab=transcript`). Branch `feature/stream-d-decisions-review`, one PR (see the PR list). No backend, nginx, compose or migration change; `npm run gen:api` produced no difference (the generated types were already current).

## Why
After PR #49 the dashboard looked the same: the API existed and nothing drew it. Adebayo decided on 2026-11-03 that every designed screen is pilot work, and these two have their API merged and no team layer in the way.

## How
| Piece | Decision |
|---|---|
| Source of truth | `Beyric Note Taker.dc.html` (screens "Decisions" and "Review") for layout and words, `docs/fe-integration-guide-ai-workspace.md` and its captures for behaviour. Where the prototype is silent the simplest honest version in the existing components was built and is listed under "Designer gaps" |
| Routes | `/decisions` and `/review` added to `NEXT_PAGES`, the `proxy.ts` matcher (nonce CSP) and `APP_PATHS` (robots). Both pages are `noindex`, dynamic (they read the visitor's cookie, so `Cache-Control: private, no-store` comes from Next: checked on the built app), and send a visitor who is logged out to `/login`. nginx needs nothing: `location /` already goes to Next |
| Writes | All through `lib/api/client.ts`, which sends the CSRF token and retries once on `csrf_rejected`. Added `decisions.list/hide` and `review.list/check/uncheck/addToVocabulary` |
| Decisions: search | The API searches (`q` matches the decision's words, or the meeting's code, in which case all of that meeting's decisions show). Typing waits 300 ms; only the newest search may change the screen (a slow older answer is dropped); `maxLength` 200 as the API says. Not a client-side filter as in the prototype: the list is paged by meeting and only the first 20 meetings are on the page |
| Decisions: remove | "Remove from log" asks first ("Remove it from this log? The notes keep it.") because **there is no way back from the screen** (the API has no unhide). A 404 (already gone, or replaced by a rewrite) counts as removed, like deleting a vocabulary term |
| Review: lists | The server render reads `open` and `checked` in parallel. Checked starts closed ("Checked (n)") as the spec says. A check or undo moves the item between the lists by the API's own order (newest meeting first, then the notes' order) and calls `router.refresh()`, which re-renders the sidebar so the badge follows without losing what is typed |
| Review: spelling box | Drawn only when `heard_as` is not null (a number or a price never gets one). The box is empty at first, empties again after a successful add (found in the browser: after Undo the old text came back), and Enter in the box adds |
| Review: add to vocabulary | 201 moves the item to Checked. **409 `duplicate_term` is treated as done**: the API still marks the item checked, so the item moves and the screen says the term is already there. **400 `invalid_term`** keeps the item open and shows the API's own words under the box; an empty box shows the same sentence without calling the API |
| Review: rewrite offer | After an add (and after a 409) a notice offers "Rewrite the notes with the corrected spelling": it reads the meeting for its `template_id` and `can_regenerate`, then calls the same `regenerate` the meeting page uses (it waits minutes; a gateway or network error says the rewrite may still finish). The notice says items you removed or checked may come back if the wording changes (FE guide section 6) |
| Meeting page | A "Decisions and review" card from `extraction_status`: `pending`/`running` "Reading the notes..." with polling every 5 s for 2 minutes (24 polls) and then a "taking longer than usual" line; `done` links to both screens; `failed` says only the name hints are missing and to rewrite; `skipped` says it is an older meeting with decisions only; null says nothing. The rewrite card gained the line about removed and checked items. The page takes `?tab=transcript` |
| Sidebar | `AppShell` takes `reviewOpen` (from `getReviewOpen()`, which reuses the session this request already read, so no extra call). No badge for 0 or unknown; "99+" above 99; a screen reader hears "Review, 3 open". `NavLink` has a `badge` prop, shown in `/styleguide` |
| Rejected | Optimistic badge updates (the refresh is a round trip but exact); a modal for Remove (inline is enough and testable); a `loading.tsx` (it would draw without the sidebar); re-fetching both lists after every action |

## Designer gaps (for the designer to review)
| Gap | What was built |
|---|---|
| Decisions: "Remove from log" (spec row 14) | A quiet text button on each decision and an inline two-step ask ("Remove it from this log? The notes keep it." Remove / Keep) |
| Decisions: no decisions at all | "No decisions yet." with "Decisions from your meeting notes appear here, usually a minute after the notes are written." The designed "No decisions match your search." is used for a search with no result |
| Decisions: waiting and failing | "Searching..." over the dimmed list; "Could not load the decisions. Check your connection and try again." with Try again; "Show older meetings" (like the Meetings list) and "Could not load older meetings. Try again."; a failed remove shows the API's error above the list |
| Review: no item at all | "Nothing to review yet." with "Parts of a transcript that may have been misheard appear here after each meeting. Meetings from before this feature were not scanned." The designed "All caught up" is used when items exist but all are checked |
| Review: rewrite offer (spec row 13) | A teal notice at the top (the design's info alert) with the primary "Rewrite the notes with the corrected spelling", "Not now", a "Rewriting the notes. This can take a few minutes; keep this page open." state, a done state with "Open notes", and failure lines |
| Review: add and check errors | The API's message under the spelling box (400), "<term> is already in your vocabulary, so nothing was added." (409), a red line inside the card for any other failure, "This item is no longer there. It may have been replaced when the notes were rewritten." (404), "Showing the 200 newest items. Check some off and reload the page to see the rest." |
| Review: Checked rows | The design shows the text and Undo; each row also shows its meeting and day and an "Open transcript" link, and the group opens and closes with a chevron (collapsed at first) |
| Meeting page: extraction state | The "Decisions and review" side card and the added sentence on the rewrite card (words above) |

**Words changed from the prototype:** the Decisions subtitle says "Everything agreed in your meetings, newest first." (the design says "your team agreed", and there are no teams yet). Group titles are the API's meeting label ("Google Meet nat-aaaa-bbb"), not a calendar title (the API does not have one yet). Days show the year ("Mon 2 Nov 2026").

## What's involved
`web/components/app/{decisions-view,review-view}.tsx` (new), `web/app/{decisions,review}/page.tsx` (new), `web/components/app/{shell,meeting-view}.tsx`, `web/components/ui/{nav-link,icon}.tsx`, `web/app/meetings/[id]/page.tsx` (`?tab=`), `web/app/{page,templates/page,vocabulary/page}.tsx` (the badge), `web/lib/api/{client,server,types}.ts`, `web/lib/{routes,format}.ts`, `web/proxy.ts`, `web/styles/{components,pages}.css`, `web/app/styleguide/page.tsx`, `web/README.md`, tests `web/tests/{decisions-view,review-view,meeting-extraction,decisions-review-shell}.test.tsx` and `helpers.ts`, and the docs (`docs/ui/parity-report.md`, the scope line of `docs/fe-integration-guide-ai-workspace.md`, `docs/checklist/master-checklist.md`).

## Verification
| Check | Result |
|---|---|
| `npm run lint`, `npm run typecheck` in `web/` | clean |
| `npx vitest run` | 276 tests pass (53 new): each screen against the bodies captured in `docs/fe-captures/ai-workspace-w1/` (list, groups, search by words and by code, empty, paging, hide, 404, check, uncheck, vocabulary 201, 400, 409, the 200-item notice), the meeting states `pending`, `done`, `failed`, `skipped`, the 5 s polling and the stop after 24 polls, the sidebar badge, the two pages' server half |
| Real browser, built app (`next build`, standalone server, nonce CSP) against a **real local FastAPI**, real migrations, a real Postgres 17 container, real HTTP, login with a placeholder account | **Passed:** sign-in; Review list from the API with the badge at 3; check (badge 2), open Checked, Undo (badge 3); empty term shows the message with no call; add "Metrika" (201: item moves, badge follows, offer shown); the same term again (409: item checked, "already in your vocabulary" and the offer); the box empty after Undo; Open transcript link (`?tab=transcript` opens the Transcript tab); rewrite pressed: the API failed (no OpenAI key here) and the notice showed "Something went wrong on our side." with the buttons still there; Decisions: grouped list, search by meeting code and by an unmatched word ("No decisions match your search."), Remove from log with the ask, the decision gone after a reload; meeting pages for `pending` (and the "taking longer than usual" line after exactly 24 polls 5 s apart), `failed`, `skipped` and `done`; phone width (375 px) for both screens; no CSP violation in the console |
| **Not verified** | A **successful** rewrite from the Review offer (it needs OpenAI; covered by unit tests with a mocked call and by the earlier live capture #46 of the same route); the **worker** doing the extraction (`running` seen from outside, time from `ready` to `done`): the extraction step ran with a stand-in model reply that gives "Metrika" as `heard_as`, so the heard-as hints in the browser are that stand-in, not luna's; Safari and Firefox; the live site (nothing here touches production); a Review list at the 200-item limit and a very long Decisions list; the style guide page was not opened |

How the local stack was made (not committed; a copy is in the shared notes folder): a scratch Postgres (`docker run ... postgres:17`), the project's migrations, `create_app()` under uvicorn, one placeholder user and the synthetic meetings of `tests/eval/extraction/`, `workspace.process_next(client=<stand-in>)` for the extraction, then `FASTAPI_INTERNAL_URL=http://127.0.0.1:8041 npm run build` and the standalone server on port 3041 (copy `.next/static` and `public` into `.next/standalone` first).

## Operate / roll back
**Server steps before merge: none.** Merging deploys the web image like any web change. Roll back by reverting the PR: the screens disappear, the API is unaffected, nothing is migrated.

## Follow-ups
- Tick D3 and D4 for the live site only after Adebayo looks at them once with real data (the worker's real extraction fills the lists after a real meeting).
- A per-meeting "Reading the notes..." hint on the Decisions and Review screens needs the lists to carry the extraction state; today only the meeting page shows it.
- Open questions have no endpoint yet (series screens).
- Bring a removed decision back: the API has no unhide; if people regret removals this needs an endpoint and an "Undo" toast (the toast component is D16).
- The calendar title as the group title, once the calendar spec stores it.
