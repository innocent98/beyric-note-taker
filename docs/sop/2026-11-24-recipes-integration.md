# SOP: Meeting recipes, slice S7 (integration: sharing, deletion, stale, privacy, live pass)

**What shipped.** The seams between the recipes slices, and the one slice that was missing. **Slice S4 (sharing, plan tasks 13 and 14) had never been built**: no `visibility.py`, no share routes, no Documents page. It is built here. Also: deleting a meeting deletes the single-meeting drafts made from it, the stale label and regenerate are pinned by tests, a privacy and terms sentence, the Documents page and its sidebar entry, a wait-your-turn for the one-at-a-time export renderer, three browser-found seam fixes, a live pass of all eighteen starters and the board (with a **stand-in model**: see below), an export-load measurement and the one FE guide for the whole feature. Branch `feature/recipes-integration`, PR into `feature/recipes` (not `main`). Spec `docs/superpowers/specs/2026-11-24-meeting-recipes-design.md` (5.4 item 6, 5.4a, 5.6, 7), plan task 17 and tasks 13 and 14. FE guide `docs/fe-integration-guide-recipes.md`, captures `docs/fe-captures/recipes/integration/` and `docs/fe-captures/recipes/live-pass/`. No migration.

## Why

The release is only complete when a draft can be shared safely, when deleting a meeting does not leave its words behind, when a person can tell a draft is older than the meeting, and when every starter has actually gone through the real path. Five slices had been built by different sessions; nobody had walked the whole journey.

## How, and what was decided

| Decision | Choice | Why (and what was rejected) |
|---|---|---|
| Who may read a draft | The creator, or a current member of a team the draft is shared with **by someone who is still in that team** (`app/recipes/visibility.py` `readable_clause`). Looked up on every request, never remembered | Leaving a team, unsharing and the creator leaving all end access at once, with no cleanup job. Rejected: copying the member list at share time |
| Writes | Edit, discard, share, unshare and the board's operations look the draft up through `api_recipes.document_or_404`, which stays **creator-only**; reads (the draft, its exports, the list) use `readable_document_or_404` | Two functions, so a later change cannot widen a write by accident. A reader's write is a 404, not a 403 |
| What anyone sees of sources | A source of a meeting the viewer may not read becomes `{"kind": "hidden"}` (`meeting_id`, `line`, `quote`, `ref` null), **for the creator too** (a meeting unshared from the team, left with the team, or deleted: no dead link, no quote). `meeting_ids` is cut to the readable ones for a reader (the creator's list is left whole). `left_out_count` is 0 and `options` and `use_brief` are blank for a reader. `hidden` is a new `Source.kind` that the model's schema, the parser and an edit can never produce | The plan's `{"kind":"hidden"}`; Review Focus 1; the Opus review's second finding (the creator's view of a lost meeting leaked its id, line and quote and linked to a 404) |
| The draft's title and words | The **words** are shared; the **title** is the model's for the creator and for a reader who can read EVERY source meeting, and a generic "Proposal, 24 Nov" (recipe name and the day it was made) for any reader who cannot (`api_recipes._generic_title`, in the list rows too). The share dialog says the title and text can reveal what was said | The model's title can carry a hidden meeting's title (lead's decision 3). Redacting model text itself was rejected: it cannot be done reliably |
| Checks and `stale` for a reader | Worked out from **what the reader can read** (`service.reading(session, reader, doc)`), so a figure that only a hidden meeting contains is flagged "check this" for them; `stale` is **false** for a reader who cannot read every source meeting (the hash they could compute would always differ) | Lead's decision 5. This replaces my first design (the creator's view), which leaked one bit |
| Which team | A draft is shared with **the team it was made in** (`doc.team_id`), never the active one; `POST .../share` takes no team and ignores `X-Team-Id`. A draft made in the personal space answers 409 `personal_team` ("This draft was made in your own space. Make it in a team workspace to share it with that team."); a creator who left the team answers 409 `team_left`; only a `ready` draft can be shared. The creator's draft carries `team` and `share_blocked` (the sentence, or null) so the dialog says it in advance. `DELETE .../share` removes every share | Lead's decision 4: the brief and meetings the draft used belong to that team. Tested with a person in two teams |
| A share must not come back | `teams.leave` (and so `remove_member`) deletes the leaver's `DocumentShare` rows for that team; `visibility.shared_team_ids` and `_shared_to` also ignore a share whose sharer is not currently a member | Review bug 1: only the membership row was deleted, so rejoining revived the share. Test: share, leave, rejoin: not readable (mutation-checked) |
| Discarding before the worker starts | `DELETE` locks the draft row; if it is still `writing` with no `started_at`, the pending `ai_runs` row is deleted so it does not count against the day's limit; once started, it counts | Spec 5.6: only attempts that call the model count. The worker claims with `FOR UPDATE SKIP LOCKED`, so it either already has the draft or skips it |
| A board is saved only by its own route | Plain `PATCH /api/documents/{id}` on a board is 409 `use_board` | The board route has the revision check and row lock; a whole-document save from an old screen could undo a move |
| Excel sheet names | A table under the heading "History" (any case) gets `Table N` | Excel reserves "History" |
| Export by a reader | Allowed (text only, no sources in any export). A reader who cannot read every source meeting gets the **generic title** in the file name and the document heading (`exporters.export(..., title=)`, found by the automated security review), as on the screen | A reader was shared the words; blocking export would only push them to copy and paste |
| Deleting a meeting | `app/recipes/cleanup.py` `delete_drafts_of`: deletes every draft **all of whose meetings are going**; called by `services.delete_meeting` (before the meeting row goes) and `deletion.purge_user` (account deletion). A cross-meeting draft stays while one of its meetings does. `ai_runs` rows stay (they hold no text and still count toward the day's limit) | Spec 5.4a "delete means gone", cross-meeting drafts kept. The meeting page's confirmation now says how many documents go and that several-meeting ones stay |
| Stale | Nothing new: `source_hash` covers each meeting's notes and the **shown transcript column** (`speaker_transcript`, else `transcript`) and the brief. A notes rewrite and the transcript editor (which writes that column) both change it. A speaker rename changes the text too, so it also marks a draft stale | Pinned by tests. The transcript editor is on `main`, not on this branch (see Follow-ups) |
| Export renderer load | A second Word or PDF request waits for its turn up to about 3.5 s (8 tries, 0.4 s apart) before 503 `export_busy` (`app/api_recipe_exports.py`) | **Measured**: two exports started together made one of them a 503 every time (5 of 10). Now both succeed (the second after about 1.4 s). Acceptable at this scale; the meeting export keeps its immediate 503 |
| Privacy and terms | See "Privacy and terms" below | Spec 5.4 item 7 |

**LEAD-DESIGNED pieces added in this slice (the designer's files draw none of recipes):** the share dialog (frame of the Make dialog; the sentence "Teammates will read this draft even if they cannot open the meeting.", then "The draft's title and text can reveal what was said in it. A teammate who cannot open all its meetings sees a generic title instead of this one."; it names the draft's own team and shows `share_blocked` instead of a Share button when set), the toolbar's **Share with team** / **Shared** button and the "Shared with <teams>" line, the reader's read-only screen ("Shared by <name>", "Back to documents", no Edit, Share, Regenerate or Discard), the "not available" chip for a hidden source and the reader's notice ("You cannot open the meetings this draft was made from, so its sources are not available to you."), the **Documents** page (`/documents`, tabs **Mine** and **Shared with me**, rows, Load more), the sidebar **Documents** entry after Recipes, the **Documents from this meeting** card on the meeting page, and the document sentence inside the delete-meeting confirmation. Existing components and tokens only; new CSS is the two `.side-doc*` classes and one phone rule for the Download panel.

## Privacy and terms

Edited in `web/components/legal/privacy-content.tsx` and `terms-content.tsx` **and the Jinja twins** `app/templates/privacy.html`, `terms.html` (a test requires the two to match word for word). Both pages now say "Last updated 24 November 2026" and `signup.TERMS_VERSION` is `2026-11-24` (a test ties them; if the workspaces release bumps it too, keep the later date on both pages).

- Privacy, "Meeting content": documents made from meetings are stored in our database like notes; the draft text, its sources, edits and who it was shared with; a draft shared with a team can be read by every member **even one who cannot open the meeting**.
- Privacy, "Information you give us": recipes and team briefs; a file imported into a brief is read in memory, shown for review, and **not stored**.
- Privacy, OpenAI (same vendor as notes): it also receives the transcript, or the notes, decisions, tasks and questions, of the chosen meetings and the brief, to write documents.
- Privacy, retention: documents are kept until discarded or the account closes; drafts made from one meeting go with it; a several-meeting document stays while one of its meetings does (**its words, which may repeat what a deleted meeting said, stay until discarded**); unsharing or leaving a team ends access at once.
- Terms, "Accuracy of notes and documents": drafts are automatic first drafts, the checks are not complete, the person must read and correct before sending, Beyric never sends a document.

**FOR ADEBAYO'S LAWYER (not blocking, his 2026-11-18 rule):** the retention line about a cross-meeting draft keeping words from a deleted meeting is a real privacy trade-off (a person who deletes a meeting may expect every trace gone). The alternative is to delete cross-meeting drafts too or to redact; it was not chosen (spec 5.4a) and the page says so plainly. Also the share sentence: sharing a draft reveals its meetings' content to teammates who cannot read the meetings.

## What's involved

| Path | Change |
|---|---|
| `app/recipes/visibility.py` | **new**: `readable_clause`, `shared_clause`, `can_read_document`, `readable_sources`, `redact`, `shared_team_ids` |
| `app/api_recipes.py` | `_brief` and `_out` take the viewer and always redact; `_generic_title`, `_share_refusal`; discard gives the place back; plain `PATCH` of a board is refused; `document_or_404` (creator) and `readable_document_or_404`; `POST` and `DELETE /api/documents/{id}/share`; list scopes `mine` and `shared` |
| `app/api_schemas.py`, `app/recipes/blocks.py` | `access`, `owner_name`, `shared_with`, `can_edit`, `SharedTeam`; `Source.kind` may be `hidden`, `meeting_id` nullable (the model's JSON schema still lists only the four real kinds) |
| `app/teams.py` | `leave` deletes the leaver's shares for that team |
| `app/recipes/exporters.py` | reserved Excel sheet name |
| `app/recipes/cleanup.py`, `app/services.py`, `app/deletion.py` | deleting a meeting or an account deletes drafts made only from it |
| `app/api_recipe_exports.py` | reads through `readable_document_or_404`; waits for the renderer |
| `app/templates/privacy.html`, `terms.html`, `web/components/legal/*`, `app/signup.py` | the sentences and the version |
| `web/components/app/document-share-dialog.tsx`, `documents-view.tsx`, `meeting-documents.tsx`, `document-view.tsx`, `document-body.tsx`, `document-parts.tsx`, `board-view.tsx`, `shell.tsx`, `meeting-view.tsx`, `web/app/documents/page.tsx`, `web/lib/documents.ts`, `web/lib/api/client.ts`, `types.ts`, `schema.d.ts`, `web/lib/routes.ts`, `web/proxy.ts`, `web/styles/pages.css` | the screens above |
| `scripts/recipes_live.py` | the stack (real app and worker, stand-in model, seeded people and meetings) with `serve`, `pass`, `load` and `capture` |
| `docs/openapi.json`, `docs/fe-integration-guide-recipes.md`, `docs/fe-captures/recipes/integration/`, `docs/fe-captures/recipes/live-pass/` | regenerated, guide, captures |
| Tests | `tests/test_recipe_visibility.py` (23), `tests/test_recipe_integration.py` (14), `tests/test_api_recipe_board.py` (+1), `tests/test_recipe_exports.py` (+3), `web/tests/document-share.test.tsx` (19), `documents-page.test.tsx` (12), `meeting-delete-documents.test.tsx` (5) and neighbours |

## Where the permission and export code lives (for the Opus review)

| Rule | Code |
|---|---|
| Who may read a draft (creator, team share by a current member, reader a current member) | `app/recipes/visibility.py:31-62` (`_shared_to`, `readable_clause`, `shared_clause`, `can_read_document`) |
| Reads vs writes | `app/api_recipes.py:139` `document_or_404` (creator only: every write) and `:145` `readable_document_or_404` (reads); `get_document` `:236`, `list_documents` `:251`, `edit_document` `:306`, `delete_document` `:344`, `share_document` `:368`, `unshare_document` `:404`; the board's own owner lookup `app/api_recipe_board.py:90` |
| The unreadable-meeting rule (every viewer) | `visibility.readable_sources` and `redact` in `app/recipes/visibility.py`, applied in `api_recipes._out` (blocks, `stale`, `sources_available`) and `_brief` (meeting ids and the generic title); the creator's own request reads meetings through `service._readable_meetings` (`app/recipes/service.py:161`) and the picker (`app/recipes/picker.py`, `can_read_clause`) |
| The share visibility rule | `share_document` and `_share_refusal` in `app/api_recipes.py` (ready, the draft's own team, not personal, creator still in it) and `readable_clause`; a share counts only while its sharer is in the team; `teams.leave` deletes it |
| Worker-time access (left the team, recipe archived) | `app/recipes/service.py:_read_job` (`teams.membership` check `:356`) and `reading` `:558` (brief only while a member) |
| Team recipes and the brief (owners and admins write) | `app/api_recipe_library.py` `team_admin.require_manager` at `:274`, `:336`, `:360`, `:422`, `:561` |
| Prompt fencing, injection | `app/recipes/generate.py` `SYSTEM` `:72`, `_fence` `:163` |
| Exports | route `app/api_recipe_exports.py:66` (`export_document`), builders `app/recipes/exporters.py` (`neutralise` `:44`, `_csv` `:73`, `_xlsx` `:87`, `export` `:114`), the render child `app/export.py` (one at a time, `build_file`), tables in `app/export_render.py`, boards `app/recipes/board.py:to_notes_document` |
| Deleting a meeting | `app/recipes/cleanup.py:21`, called from `app/services.py` `delete_meeting` and `app/deletion.py` `purge_user` |

## Verification

- Locally (bounded; scratch Postgres 16 `recipes-integration-pg` on port 55621, removed afterwards): `tests/test_recipe_visibility.py`, `tests/test_recipe_integration.py`, `tests/test_api_recipes.py`, `tests/test_recipe_exports.py`, `tests/test_meeting_delete.py`, `tests/test_legal.py`, and the IDOR guard (`tests/test_accounts.py -k "delet or purge or idor"`); `ruff check` and `ruff format --check` clean. Web: `npm run lint` (0 errors, 3 old warnings), `npm run typecheck`, and only the touched files and neighbours. **The full suite, shards and e2e were left to CI.**
- **Mutation checks:** making a share count without its sharer still being a team member fails `test_the_creator_leaving_the_team_ends_the_teams_access`; removing the redaction fails the reader's and the creator's hidden-source tests; leaving without deleting the shares fails `test_a_share_does_not_come_back_when_its_creator_leaves_and_rejoins`.
- **Looked at in a real browser** (the built-in browser against the Next dev server in front of the real API, a stand-in model, seeded people), signed in as the creator and as a teammate: Make dialog (all eighteen starters grouped General and School, team and brief line, the picker mounted for Status report), a proposal made, its blank and its invented 450,000 naira flagged "check this", source chips link to `/meetings/1?tab=transcript&line=N` and the Transcript tab opens, Share dialog and the toolbar after sharing, the teammate's reader screen (sources "not available" for a private meeting, "Shared by Ada Lovelace"), the Documents page and its Shared tab, a board, a status report and an action tracker made through the app. At **390 px**: no horizontal page scroll on the document page, the Documents page, the reader screen, the board, the status report, the table draft (the table scrolls inside its box), Recipes (18 starters) and the Make dialog. **Found and fixed by looking:** the Download panel ran 4 px off the right edge on a phone (now opens under the toolbar); the reader's notice said "no longer available to you" for a meeting they never had; the delete-meeting confirmation did not mention documents.
- **Not run live** (and why): a **real model** (no OpenAI key on this machine); a finger drag of a board card on a real phone; the Make dialog as a team **member** and as an owner/admin in the browser (vitest and API tests only); the `plan_inactive` and `recipe_limit` messages in the browser (vitest and API tests); Word and PDF opened in Word and a PDF viewer (read back as files, not looked at); nginx and the production Next build; the transcript editor (not on this branch); a second team in the browser; the Make dialog's recipe editor flows for a custom recipe in the browser (S3's vitest only).

## Live pass of every starter (stand-in model, NOT a real one)

`scripts/recipes_live.py pass` runs all eighteen starters through the real request, worker, context, ledger, audit and exports against synthetic seeded meetings (a sales call, a sprint planning, a lecture, short series). **The model was a stand-in** (`LiveModel`): no OpenAI key is configured on this machine, so **no recipe was run against a real model**. The stand-in copies real lines of the real prompt, so sources, blanks and flags behave like a grounded draft; it plants a `[need: ...]` where the meeting has no date and one invented figure (450,000 naira) in the proposal. So the columns below measure the **pipeline**, not the model's quality: "output sanity" for a real model is **unknown** for all eighteen; the number audit **did** catch the one planted figure (`proposal`, kind `figure`), and blanks appeared exactly where the stand-in wrote them. Token counts are the prompt actually sent (estimated at 4 characters a token) and the stand-in's output; cost is those tokens at the repo's own price constants for `gpt-6-luna` (0.10 and 0.50 USD per million tokens, `scripts/ai_cost_report.py`). The seeded meetings are short (about 25 turns), so real meetings cost more: a one-hour transcript is roughly 10,000 to 15,000 tokens, which at those prices is **well under one US cent a draft** (about 0.0015 USD of input plus about 0.001 of output), still an estimate until a real run.

| Recipe | Scope, output | Blocks | Blanks (what) | Flags | Prompt tok. (est.) | Output tok. (est.) | Cost USD (est.) | Word / PDF / MD (+CSV, Excel) |
|---|---|---|---|---|---|---|---|---|
| `follow_up_email` | one, email | 6 | 0: none | none | 1832 | 412 | 0.00039 | 200 / 200 / 200 |
| `proposal` | one, document | 16 | 2: when | 450,000 naira (figure) | 1813 | 1322 | 0.00084 | 200 / 200 / 200 (+200, 200) |
| `requirements` | one, document | 14 | 2: owner | none | 1590 | 1187 | 0.00075 | 200 / 200 / 200 (+200, 200) |
| `marketing_post` | one, document | 2 | 0: none | none | 1782 | 122 | 0.00024 | 200 / 200 / 200 |
| `decision_memo` | one, document | 10 | 0: none | none | 1453 | 802 | 0.00055 | 200 / 200 / 200 |
| `sop` | one, document | 6 | 0: none | none | 1460 | 503 | 0.00040 | 200 / 200 / 200 |
| `action_tracker` | one, table | 2 | 2: due date | none | 1453 | 525 | 0.00041 | 200 / 200 / 200 (+200, 200) |
| `board` | one, board | 4 | 1: date | none | 1655 | 452 | 0.00039 | 200 / 200 / 200 |
| `status_report` | many, document | 8 | 4: owner, due | none | 1383 | 788 | 0.00053 | 200 / 200 / 200 (+200, 200) |
| `client_briefing` | many, document | 8 | 0: none | none | 1382 | 730 | 0.00050 | 200 / 200 / 200 |
| `study_guide` | one, document | 8 | 0: none | none | 1459 | 733 | 0.00051 | 200 / 200 / 200 |
| `flashcards` | one, table | 2 | 0: none | none | 1414 | 607 | 0.00044 | 200 / 200 / 200 (+200, 200) |
| `practice_quiz` | one, document | 4 | 0: none | none | 1451 | 417 | 0.00035 | 200 / 200 / 200 |
| `glossary` | one, table | 2 | 0: none | none | 1423 | 874 | 0.00058 | 200 / 200 / 200 (+200, 200) |
| `assignments` | one, table | 2 | 2: due date | none | 1443 | 680 | 0.00048 | 200 / 200 / 200 (+200, 200) |
| `revision_plan` | many, document | 4 | 0: none | none | 1091 | 389 | 0.00030 | 200 / 200 / 200 (+200, 200) |
| `class_recap` | one, email | 6 | 0: none | none | 1477 | 409 | 0.00035 | 200 / 200 / 200 |
| `class_faq` | one, document | 4 | 0: none | none | 1431 | 420 | 0.00035 | 200 / 200 / 200 |

Total for the 18 drafts: about 0.008 USD (estimate). Ledger: one `recipe` row per draft, status `ok`, the tokens above (`ledger_status` in `results.json`). Raw results: `docs/fe-captures/recipes/live-pass/results.json`.

**What a real run must still check (owed, needs a key):** that the real model returns the strict JSON for each of the eighteen without `model_reply` or `structure` failures (the schema is the one `ask` already uses), that it writes `[need: ...]` instead of guessing in the school recipes (dates, answers), that the proposal takes prices only from the brief, and the true latency (the worker allows 3 minutes a call). `scripts/recipes_live.py` is not the tool for that (it always uses the stand-in): set `OPENAI_API_KEY` in the local `.env` and make each starter through the app on Adebayo's own meetings.

## Export load (one-at-a-time renderer)

Measured with `scripts/recipes_live.py load` (developer Mac, five runs each, two signed-in sessions): a 40-row table draft and a board.

| Case | Median | Worst | Result |
|---|---|---|---|
| Table PDF alone | 0.61 s | 0.62 s | 200 |
| Table Word alone | 0.19 s | 0.23 s | 200 |
| Board PDF alone | 0.55 s | 0.56 s | 200 |
| **Before the fix:** two exports started together (table PDF + board PDF, same PDF twice, two Word files) | 0.54 to 0.61 s | | **one of the two got 503 `export_busy` every time (5 of 10)** |
| After the fix: table PDF + board PDF together | 1.38 s | 1.41 s | both 200 |
| After the fix: the same PDF twice together | 1.46 s | 1.50 s | both 200 |
| After the fix: two Word files together | 0.62 s | 0.64 s | both 200 |

**Is the one-at-a-time renderer acceptable?** Yes, for the pilot, with the wait added: a build is 0.2 to 0.7 s, so two people exporting at the same moment wait about a second, and the 503 only returns when about five or more exports queue behind one another. The production server is smaller and shared, so treat these as lower bounds; measure on the server at the first staging run. If teams grow, the next step is a small queue or a second renderer slot, not a different design. `docs/fe-captures/recipes/live-pass/export-load.json` holds the **after** run (the before numbers are the ones in this table).

## Operate / roll back

- **Nothing deploys until the one PR from `feature/recipes` to `main`.** There is no feature flag: the screens appear when the code deploys. The gate for making documents follows the same `FEATURE_GATES_MODE` as Ask: `off` (everyone), `shadow` (logs), `enforce` (a person without a live plan or trial gets 403 `plan_inactive`). Billing is live on the server, so confirm the mode there. Other switches: `RECIPE_DAILY_LIMIT` (default 20; `0` refuses everyone), `RECIPE_MODEL` (default `gpt-6-luna`), `BRIEF_MAX_CHARS` 12000, `DRAFT_MAX_BLOCKS` 60, `IMPORT_MAX_BYTES` 5 MB, `IMPORT_MAX_PAGES` 40.
- **Server steps before it reaches `main`:** the nginx import location from S3 (one hand edit; see `docs/sop/2026-11-24-recipes-library.md`); new dependencies `openpyxl` and `pypdf` come with the image build. Migrations are `0059` and `0060`, one chain after `main`'s `0058` (renumbered from the placeholders `0060` and `0061`); if the workspaces release lands first, renumber again.
- **Roll back:** `alembic downgrade` to the revision before the recipes tables (deletes every draft, share, recipe and brief and the `recipe` ledger rows), or revert the merge; nothing else depends on them. Stopping sharing for everyone: `delete from document_shares` (access ends at once).

## Follow-ups

- **A real-model run of all eighteen starters and the board, on Adebayo's own meetings, before release** (needs `OPENAI_API_KEY`): the one thing this slice could not do.
- After the rebase onto `main`, re-run the stale tests with the transcript editor present (`transcript_edit` writes `speaker_transcript`/`transcript`, which the hash already reads; `notes_source_version` is a different label, "notes older than the transcript", and is not used here). Confirm `signup.TERMS_VERSION` and the page dates against the workspaces release.
- An owner who edits a draft that has hidden sources (a meeting they lost or that was deleted) saves it without those links (the editor never sends a `hidden` source back); regaining access later does not bring the links back.
- The Documents page's "Load more" shows when a page comes back full (`DocumentList` has no `has_more`).
- A draft is shared with one team (the one it was made in); Stop sharing removes it.
- Mobile: the Download panel fix is in the document toolbar only; the meeting page's own Download menu has not been looked at at 390 px.
