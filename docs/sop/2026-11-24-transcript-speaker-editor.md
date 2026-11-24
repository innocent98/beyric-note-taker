# SOP: Transcript and speaker editor

**What shipped.** On the meeting page's transcript tab, the person who may change the meeting can press **Edit transcript** and then **rename a speaker everywhere** ("Speaker 2" becomes "Daniel"; a name already in the meeting merges the two, after a confirm), **fix the words of one line**, and **move one line to another speaker**. **Restore original** puts the transcript back. Two routes (`PATCH /api/meetings/{id}/transcript`, `POST /api/meetings/{id}/transcript/restore`), four columns on `meetings` (migration `0058`), a notice on the page when the notes are older than the edit. Branch `feature/transcript-speaker-editor`; PR: see the checklist entry. Alembic head: **0058** (it was 0057 until the search-index PR #262 took that number and merged first; this one was renumbered on top of it).

## Why

Anthropic's Claude Meetings lets a person fix a wrong speaker or a misheard line. Beyric's only answers were the vocabulary list (changes future meetings) and the Review list. A wrong name ("Speaker 2", or two people merged under one tag, see `docs/sop/2026-11-23-speaker-name-chain.md`) stayed wrong in the page, Ask Beyric, the exports and the mail. Adebayo chose to close the gap in a small, safe v1.

## How, and what was decided

| Decision | Choice | Why (and what was rejected) |
|---|---|---|
| Which column is edited | The one every reader shows: `speaker_transcript`, and when that is empty `transcript` (`transcript_edit.shown_column`). **The plain `transcript` is left alone when the labelled copy is shown.** | The page (`api.py` detail), Ask (`ask.transcript_of`), exports, translations (`translation.transcript_of`), the rewrite (`services.regenerate_notes`) and the mail all read `speaker_transcript or transcript`. Nothing reads `transcript` once `speaker_transcript` exists, except the pipeline while a meeting is being made (before an edit is possible). For Skribby meetings the two columns hold the same text at first; for our own transcription `transcript` is the plain model text and the labelled copy is a separate string, so the model's words stay on the row as the machine record. **Rejected:** editing both (the unlabelled one has no labels to edit, and a second copy to keep equal buys nothing). |
| Lines | A "line" is a row of the page: its 1-based position is what Ask cites and what a quote points at. `web/lib/transcript.ts`, `ask.transcript_rows` and `extraction.transcript_turns` are three copies of the split. **No add, delete, split or merge in v1.** | The row count and order must never change. Enforced in code, not only by the UI: every edit is re-parsed with `extraction.transcript_turns` and refused (422 `invalid_edit`, nothing saved) unless the number of rows is the same, every row the edit did not name is unchanged, and the named row has the speaker and words expected (`_same_shape`). |
| What a person may type | A **speaker name** goes through `notes.clean_name` (new, factored out of `notes._speaker`, which uses it too): drops `[ ] : < >`, control characters and line breaks, collapses whitespace, 80 characters (`MAX_NAME_CHARS`). A **line's text** is one plain line: line breaks, `\r`, U+2028/2029, whitespace runs and control characters become one space, 5000 characters at most. | So a label can never break the `[Name]:` format or inject a fake line: `Bob]: injected` plus a line break and `[Admin` is saved as `Bob injected Admin` (tests, live #12), and `ok` plus a break plus `[Admin]: injected` is saved as `ok [Admin]: injected`, one row (#14). A row that had several paragraphs is joined into one when its words are saved (the editor says so). **Rejected:** keeping line breaks inside a row (a continuation line that looks like a label would start a new row). |
| Transcripts with no speaker names | Not editable in v1: 422 `no_speakers`; the page hides the controls. | With no `[Name]:` line the whole text is one "line" for the page and Ask. Editing it would mean one huge text box. A follow-up. |
| Renaming into an existing name | Merges, only with `merge: true`; without it 409 `speaker_exists`. The page asks first (it checks its own speaker list) and the 409 covers a race. Matching is **exact** (case counts): "daniel" and "Daniel" are two speakers. | Merging two people is not undoable per line, and the page must not do it on a typo. |
| Concurrency | Every request carries `version`, a hash of the shown text (`transcript_edit.version_of`, 16 hex characters, exposed as `transcript_version`); a request from an older page is 409 `transcript_changed`. The meeting row is locked for the edit (`services._lock_meeting`, as delete and rewrite do). **Decision: the version token, not a line count.** | A line count only catches a changed number of lines, which the editor can never produce. The hash changes with every edit, so it also catches two editors changing the same line, and a stale line index. No column is needed. It returns to its first value after a restore. |
| Original and restore | `transcript_original` holds the text of the shown column from before the **first** edit, once. `transcript_edited_at` and `transcript_edited_by_user_id` say when and by whom the last edit was made. `POST .../restore` writes the original back and nulls all three. Nobody can restore twice (409 `nothing_to_restore`). | One column for the original, because an edit can only change the one shown column and that column's old text is all there is to put back. A per-edit history is a follow-up. `ON DELETE SET NULL` on the user: an account's deletion leaves the edit, without the person (the meeting is deleted with its row anyway). |
| Notes out of date | `notes_outdated` on the detail. `meetings.notes_source_version` is the version (`transcript_text.version_of`, a hash) of the exact text the current notes were written from. `notes_outdated` = that version (or, when none is recorded, the version of the transcript before any edit) differs from the version of the shown text. `services.regenerate_notes` records the version of the text **it sent to the model, taken before the call**; the rebuild script records the version of the text it wrote notes from (and `--restore` clears it). | An earlier version kept a boolean ("notes written from an edited text") compared with "is edited". Review found it wrong after a second edit (edit, Rewrite, edit again: True and True read as current). Comparing hashes is exact in every order: edit, edit again, edit-rewrite-edit, restore-then-edit, restore after a rewrite (tests, live #36, #38, #43). A meeting nobody rewrote needs no value (null). **The race:** a rewrite reads the text, waits minutes for the model, then writes; with the version taken before the call an edit that lands meanwhile leaves the notes out of date, as they are (test: an edit during a rewrite), and the rewrite now refreshes the meeting after its commit so the page gets the edited text with it. **Rejected:** a timestamp (no notes time exists); a lock held for the whole model call. |
| Nothing is regenerated | The edit rewrites no notes, starts no extraction, translates nothing. The page shows "The transcript was edited after these notes were written. Rewrite notes to update them." with the existing Rewrite action (a reader who cannot rewrite sees the sentence without the button). | Cost and surprise (Adebayo's rule). A rewrite already marks the lists pending (`workspace.mark_pending`). |
| Who may edit | `visibility.owns` (`app/visibility.py:130`): the recorder; for a team-owned meeting (`owner_team_id`) its recorder while still a member, and the team's owner and admins. **This is the rule `DELETE /api/meetings/{id}` asks** (`services.owned_meeting_or_404`). Only `ready`. | One rule, not a second. See "Where the permission check is". |
| Status code for a reader | **403 `team_forbidden`** for someone who can read the meeting but not change it; 404 for someone who cannot read it. | The task asked for 403 for read-only people. The older meeting routes (cancel, delete, rewrite...) answer such a reader 404 (`owned_meeting_or_404`: "the answer must not tell a reader that the id is real"), but a reader of a shared meeting already knows it exists, and `move-back` already answers 403 `team_forbidden` for the same kind of person. The page is told `can_edit_transcript` and never offers the control. **Check this choice**; the one-line alternative is to call `services.owned_meeting_or_404` instead (404 for readers), and add the two routes to `OWNER_ONLY` in `tests/test_team_visibility.py`. |
| Machinery that rewrites a transcript | `scripts/rebuild_meeting_from_evidence.py` **refuses an edited meeting** in its shared `_check_state` (dry run, `--apply`, `--force` and `--restore FILE`), with a line that says `SKIPPED meeting N: a person edited its transcript ...` and exits non-zero without a backup or a change. `app/speaker_correction.py` is pure (no database, no writes); the rebuild script is its only caller that changes a stored meeting, so it is covered there. | See "Every writer of the transcript columns". |

### What happens to each derived thing after an edit

| Thing | What it does | Proof |
|---|---|---|
| Notes | Unchanged. `notes_outdated` is true; the notice shows; a Rewrite reads the edited text (`regenerate_notes` uses `speaker_transcript or transcript`) and clears it. | `tests/test_transcript_edit.py`, live #05, #36 |
| Translations | `translation.is_stale` hashes `transcript_of(meeting)`, which is the column edited, so a finished translation goes **stale** on any edit and fresh again after a restore; the existing stale banner shows. Nothing is re-translated. | tests, live #21 |
| Tasks (`Task.quote`) | The stored quote text is untouched. Where it is found is **computed on every read** (`api_tasks.present` via `extraction.TranscriptIndex.locate`), so after the line's words change the quote is not found and `quote_line` and `quote_speaker` are `null` (no link, no crash). After a rename the same line is found with the new name. | test (a task read through the API) |
| Decisions (`Decision.quote`) | Same: `line` and `quote_speaker` are computed per read; `null` when the quote is gone; the quote text stays. | test (`GET /api/decisions`) |
| Open questions, review items | Read from the notes, not from the transcript; unchanged until the notes are rewritten. | n/a |
| Ask Beyric | Passages and cites are built from the shown text per question; a transcript cite is the row's position, which an edit never changes. A task's cite uses `_quote_row`: when the quote is no longer in any row it falls back to the **notes** target, as it already did for an unfound quote. A rename changes the speaker names Ask reads. | tests (`ask.transcript_rows` counts, `_quote_row`) |
| Exports (PDF, Word), copy, mail sent later | Read the shown text at the time. **Mail and shares already sent keep what they were sent.** | n/a |
| Series, "participants" | Read from `speaker_transcript` per request. | live #05 |

### LEAD-DESIGNED (the designer's files have no transcript editor), to be shown to the designer

All use existing components and tokens. New elements, each named here so none is missed:

1. The **"Edit transcript" / "Done editing"** button and the line above the transcript it sits in (`.transcript-edited`: "Edited by NAME on DATE." and **"Restore original"**).
2. The **Restore** confirm ("Restore the original transcript? Every speaker name and line you changed goes back to what it was. The notes are not changed."), the existing `meeting-delete-ask` box.
3. The **edit hint** under the bar ("Fix a speaker or a line. Lines can't be added, removed or split. The notes are not rewritten until you choose Rewrite notes.").
4. The **speaker strip** (`.transcript-speakers`): each speaker with a **Rename** link; the **rename form** (a field, Rename and Cancel) and the **merge confirm** ("NAME is already in this meeting. Merge A into NAME? Every line from both will be labelled NAME.").
5. The **pencil** on each line (a new `pencil` icon in `components/ui/icon.tsx`; the designer's set has none) and the **line editor**: a text box, a speaker menu (the meeting's speakers plus "Someone else..." with a name field; "No speaker" for a nameless first line), Save line and Cancel, and the one-paragraph note.
6. The **"notes are out of date" notice** above the page ("The transcript was edited after these notes were written. Rewrite notes to update them. Nothing was rewritten for you: tasks, decisions and translations still come from the earlier text." with a **Rewrite notes** link that is the existing Rewrite action; a reader sees "The transcript was edited after these notes were written, so they may not match it.").
7. Behaviour of the forms: Escape closes a rename or line form, Save, Cancel and Escape return focus to the pencil or Rename button, a refusal is shown inside the form that asked (an `Alert`, announced), and the merge question is asked of the name **as the server will save it** (`cleanName` mirrors `notes.clean_name`: "Ana:" is the speaker Ana).
8. CSS in `web/styles/pages.css` under "Transcript editor": `.transcript-edit-hint`, `.transcript-speakers`, `.transcript-speaker`, `.transcript-head`, `.transcript-edit-form`, `.transcript-edit-row`, `.transcript-edited`. Tokens and the existing `icon-btn`, `det-link`, `btn`, `field` classes only.

## Where the permission check is (for review)

- **The check:** `editable_meeting` in `app/transcript_edit.py:249`: `visible_meeting_or_404` (404 when the caller cannot read it) then `visibility.owns(session, user, meeting)` (`app/visibility.py:130`, the rule the delete route uses through `services.owned_meeting_or_404`), else `team_admin.forbidden(...)` = 403 `team_forbidden`.
- **Both routes call it first:** `edit_transcript` and `restore_transcript` in `app/api.py` (around lines 930 and 976).
- **`ready` and the version are checked after the row lock:** `_locked_ready` in `app/transcript_edit.py`: 409 `not_ready`, then 409 `transcript_changed`. A meeting deleted between the read and the lock (`_lock_meeting(...).one()` raising `NoResultFound`) is answered with the normal 404 on these two routes; the older locked routes (cancel, rewrite, delete) still 500 in that window.
- **The page flag:** `can_edit_transcript=owner and ready` in `_detail` (`app/api.py:450`), `owner` being the same `visibility.owns`.
- **Tests:** `tests/test_transcript_edit.py` (owner; a team admin on a team-owned meeting; a plain member of the owning team; the recorder who left the team; a teammate of a shared meeting; an outsider; a teammate of a private meeting; not ready; csrf), plus `test_the_edit_follows_the_same_rule_as_deleting_a_meeting`, which compares the route's answer with `visibility.owns` for four people; and the route-inventory test in `tests/test_team_visibility.py` (`TRANSCRIPT_EDIT`).

## Every writer of the transcript columns (proof none is missed)

`grep -rnE "(meeting|locked|m)\.(speaker_)?transcript *=|setattr\(" app scripts`, kept as a test (`test_every_writer_of_the_transcript_columns_is_known`, which fails when a new assignment appears):

| Writer | When it runs | Why it cannot touch an edited transcript |
|---|---|---|
| `app/pipeline.py` (5 assignments: the Skribby path, our own transcription, `_step_speakers`, the speaker give-up) | while a meeting is `processing` | An edit needs `ready`. `_step_speakers` also writes only when `speaker_transcript is None`, and a `ready` meeting is never re-processed (retry is for `failed` meetings, which cannot be edited). |
| `scripts/rebuild_meeting_from_evidence.py` (`_apply` and `restore`) | by hand, on a `ready` meeting | **Skips** an edited meeting: `_check_state` (`scripts/rebuild_meeting_from_evidence.py:184`) is called by `load`, by `_apply` after taking the lock and by `restore` twice. |
| `app/transcript_edit.py` | the editor | This feature. |
| `scripts/capture_calendar_titles.py` | a scratch database fixture | Not a runtime writer. |
| `app/translation.py:591` (`row.transcript`) | resets a **translation** row | A different table. |

Also checked: `app/speaker_correction.py` (pure functions, no assignment: its results reach a stored transcript only through the pipeline above or the rebuild script), `update(Meeting)` statements (`teams.py`, `series.py`, `workspace.py`: none sets a transcript), and no job nulls or re-labels a ready meeting's transcript.

## What's involved

| Path | Change |
|---|---|
| `alembic/versions/0058_transcript_edit.py` | `meetings.transcript_original` (text), `transcript_edited_at` (timestamptz), `transcript_edited_by_user_id` (FK users, `ON DELETE SET NULL`), `notes_source_version` (varchar(16), nullable) |
| `app/models.py` | the four columns on `Meeting` |
| `app/transcript_edit.py` (new) | `rename_speaker`, `edit_line`, `_same_shape`, `clean_line`, `version_of`, `notes_outdated`, `editable_meeting`, `rename`, `change_line`, `restore` |
| `app/notes.py` | `clean_name` (the cleaning `_speaker` already did, now shared) |
| `app/api.py`, `app/api_schemas.py` | the two routes; `SpeakerRename`, `TranscriptEdit`, `TranscriptRestore`; six new `MeetingDetail` fields |
| `app/transcript_text.py` (new) | `version_of`, shared by the editor, the rewrite and the rebuild script |
| `app/services.py` | `regenerate_notes` records `notes_source_version` from the text it sent, and refreshes the meeting after its commit |
| `scripts/rebuild_meeting_from_evidence.py` | the skip; records `notes_source_version` (`--restore` clears it) |
| `web/components/app/transcript-panel.tsx`, `transcript-edit.tsx` (new: the forms, `cleanName`, `transcriptMessage`), `meeting-view.tsx`; `web/components/ui/icon.tsx` (pencil); `web/lib/api/client.ts`, `types.ts`; `web/styles/pages.css` | the editor |
| `docs/openapi.json`, `web/lib/api/schema.d.ts` | regenerated (`PYTHONPATH=. python scripts/export_openapi.py`, else it imports the main checkout's `app`; `npm run gen:api`). **The diff only adds the transcript routes and fields; it did not add `/api/billing` routes** (the generator emitted none). |
| `pyproject.toml` | `F811` ignored for `tests/test_transcript_edit.py` (it reuses fixtures by import, like the other API tests) |
| `scripts/capture_transcript_edit_api.py`, `docs/fe-captures/transcript-edit/` | 43 live exchanges |
| `docs/fe-integration-guide-transcript-edit.md` | the FE guide; a pointer in `docs/fe-integration-guide-owner-app.md` |

## Verification

Local, scratch Postgres 16 in a throwaway container on port 5440 (removed afterwards), only named files, never the full suite:

- `ruff check .`, `ruff format --check .` (the project venv's pinned 0.17.3); in `web/`: `npm run lint` (0 errors), `npm run typecheck`.
- Backend: `tests/test_transcript_edit.py` (52: rename with and without merge, a label is matched exactly, line text, a row with continuation lines keeps its place, move a line, text and speaker together, no-change and out-of-range, a strict body (12 bad bodies), **six name-injection and six text-injection strings** (rows and lines counted with `extraction.transcript_turns` and `ask.transcript_rows`), a nameless first row cannot become a speaker by its words, no names is refused, the **permission matrix** (owner, shared teammate 403, outsider 404, private teammate 404, team admin, plain member of the owning team, the recorder who left the team, same-rule-as-delete, not ready for three states, csrf), a stale version, the version round trip, the original kept once and restored whole, the plain column edited when it is the shown one, no regeneration (fake OpenAI saw no call), `notes_outdated` through edit, a second edit, edit-rewrite-edit, restore-then-edit, restore and rewrite, an edit during a rewrite, a delete between the read and the lock, a translation going stale, Ask's rows unchanged after every kind of edit, a quote losing its link without a crash, a task and a decision read through the API, **the writers list**), `tests/test_transcript_edit_migration.py` (one head, follows 0056, nothing changes for old rows, the FK sets null, downgrade and upgrade round trip), `tests/test_rebuild_meeting_from_evidence.py -k edited` (dry run, apply, force and `--restore` are skipped and change nothing), `tests/test_team_visibility.py -k classified`, and, as directly related files, `tests/test_notes.py` and `tests/test_api.py` (the committed openapi matches).
- Frontend (vitest, touched and directly related files only): `tests/transcript-edit.test.tsx` (16, fed the captured bodies), `meeting-view`, `meeting-extraction`, `meeting-team-card`, `meeting-series`, `translation-view`, `ask`.
- Live capture over real HTTP against the real app: `docs/fe-captures/transcript-edit/`.
- **Looked at in a real browser** (Next dev server over the real backend and the capture database) at 1280 and 390 px: edit mode, the line editor (a real save changed the line and showed "Edited by owner ... Restore original"), the rename form. No horizontal overflow at 390. An early version put the Edit button in the tab bar and squeezed the tabs at 1280; it was moved to its own bar.
- **Not verified:** the server and a real meeting; the merge confirm in a real browser (a typo in my test typed over the field, so it was checked only by the vitest test); the Restore confirm in a real browser (vitest only); editing a transcript with a real two-column Skribby meeting; other widths, dark mode, a screen reader; every test file CI runs and I did not.

## Search and Ask read the column that is edited

`feature/meeting-search-index` (#262) builds its index on `meeting_search_text(notes, speaker_transcript, transcript)` (`app/meeting_search.py`), which falls back from the labelled copy to the plain one exactly like `transcript_of`. An edit writes `speaker_transcript` (else `transcript`), the same column, so the index follows an edit (a GIN expression index is maintained on every update) and Ask and the Meetings search find the edited words. Nothing writes a different column.

## Operate / roll back

- Deploy: normal; `alembic upgrade head` adds four nullable or defaulted columns (instant). **Server step before merge: none** beyond the migration the deploy runs.
- See who edited what: `SELECT id, transcript_edited_at, transcript_edited_by_user_id FROM meetings WHERE transcript_original IS NOT NULL;`
- Undo one person's edit by hand (the Restore button does the same): `UPDATE meetings SET speaker_transcript = transcript_original, transcript_original = NULL, transcript_edited_at = NULL, transcript_edited_by_user_id = NULL WHERE id = <id>;` (use `transcript` instead of `speaker_transcript` for the rare meeting whose labelled copy is empty).
- Let the rebuild script run on an edited meeting: restore the original first (the button or the SQL above); the script never overrides an edit.
- Roll back: `alembic downgrade 0057` drops the four columns. **The edits stay in the text; the originals are lost**, so restore the originals first if they matter. Code from before the columns ignores them.

## Follow-ups

- Editing a transcript with no speaker names (v1 refuses it).
- Adding, splitting or merging lines: needs a way to keep Ask's line numbers and the quote links (a rewritten line map).
- A per-edit history instead of one original.
- Show the designer the lead-designed pieces.
- Ask whether a reader of a shared meeting should rather get 404 (the older routes' answer); see "Status code for a reader".
