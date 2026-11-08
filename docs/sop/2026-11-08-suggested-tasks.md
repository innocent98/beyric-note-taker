# SOP: Suggested tasks and "Ask before adding unclear tasks" (audit B12, B13, B21 to B24)

Spec: `docs/superpowers/specs/2026-11-08-designer-v2-features-design.md`, section 2 (approved by delegation, 2026-11-08). **PR 1 (backend):** branch `feature/suggested-tasks-backend` (number in the PR list). **PR 2 (screens):** `feature/suggested-tasks-screens`, added to this SOP when it ships. **Migration 0035.** FE guide: `docs/fe-integration-guide-suggested-tasks.md`; captures `docs/fe-captures/suggested-tasks/` (`scripts/capture_suggested_tasks_api.py`).

## What shipped (PR 1)
1. **Migration 0035:** `tasks.suggested` (bool, default false), `tasks.confidence` (`high | medium | low`, check constraint, null), `tasks.reason` (varchar 200, null), `user_settings.ask_before_unclear` (bool, default **true**). No row is rewritten: old tasks keep `suggested` false, `confidence` null.
2. **Extraction `extract-v2`** (`EXTRACT_PROMPT_VERSION`): the model also returns `reason` for a `medium` or `low` item. Code keeps the confidence of every action item and checks the reason (plain text, at most 120 characters, dropped whole if it holds HTML, Markdown, a link, a `javascript:` or `data:` link, an email address or a phone number).
3. **A suggested task** is made when `ask_before_unclear` is on and confidence is `medium` or `low`: unfiled (no project, no sprint), not "Check project", not mailed, counted nowhere. **Re-runs never undo a decision**: an accepted task is never turned back (accepting counts as a touch, so a reworded bullet does not delete it and ask again), a dismissed one (tombstone) never returns, a waiting one stays waiting.
4. **`POST /api/tasks/{id}/accept`**: clears the flag, files the task as creation does, queues the assignee mail. Idempotent. Dismiss is the existing `DELETE` (tombstone).
5. **API:** `TaskOut` gains `suggested`, `confidence`, `reason`, `quote_speaker`, `quote_line`; `GET /api/tasks` leaves suggested tasks out unless `suggested=true`; `GET /api/meetings/{id}/tasks` gains `to_confirm`; `GET/PUT /api/settings/tasks` gain `ask_before_unclear`.
6. **Exclusions** on the server in every place the spec lists (table below).

## Why
The AI turns every action item into a task; the vague and conditional ones ("we should look at analytics at some point") clutter the board and mail people about things nobody agreed to. The designer's file lets the person confirm those first. Goal: unclear items wait for a person, are counted nowhere and mail nobody until accepted.

## How: decisions and trade-offs
| Topic | Decision |
|---|---|
| Unfiled until accepted | A suggested task is made with no project and no sprint; **Accept does the filing** (`tasks.accept`: the series' linked project, else keywords, else the team's single project; the sprint by due date). A person's own choice of project or sprint while it waited is kept. Alternative rejected: file at creation and hide: the task would sit in a project and sprint it never joined and a close of the sprint would carry it |
| Two passes | The worker makes the notes parser's tasks first (no confidence yet), then asks the model. Only a task **made by this extraction** may turn suggested when the model rates it unclear: one this attempt's first pass made, or (for a meeting no answer was ever written for) an unrated one an earlier attempt made within the last hour (`tasks.fresh_fingerprints`). It is then unfiled again. **No other task ever turns suggested** (Opus review of #135, finding 1): not one an answered run made and mailed and left unrated because the model skipped its bullet, not one of a failed `extract-v1` run from last week, not one a person touched. After that a re-run changes neither `suggested` nor `confidence` nor `reason`, so an accepted task stays accepted and a waiting one waiting even if the model changes its mind |
| If the model fails | The notes' tasks stay as today: unrated, not suggested (the same as before this feature). A suggestion exists only when the model said so |
| Old meetings | There is no meeting-level version gate any more (it covered only meetings whose answer was written, and missed a failed v1 run). The rule above protects every existing task by its own origin; a bullet added later to an old meeting is new, so it may be suggested. "Old tasks keep confidence null and suggested false; nothing re-extracts old meetings" holds |
| Accept: 200 and 409 | The spec says both "idempotent" and "409 `not_suggested` if already cleared". Resolved: the row is locked and read again; **a task that was suggested (rated `medium` or `low`) and is no longer waiting answers 200 with the task, and sends nothing** (a double click); a task that never was a suggestion (clear, manual, older) is **409 `not_suggested`** |
| Who may | Accept and Dismiss: **whoever may edit the task** (`Permissions.edit`: owner or admin of the task's team, the maker of a manual task) **or the person who owns the meeting it came from** (lead's decision on the review of #135). A teammate who merely reads it: 403 `team_forbidden`; someone who cannot read the meeting: 404. Owning the meeting opens suggestions only: it does not let a member delete or retitle the meeting's other tasks. `TaskOut.can_decide` says whether to draw the buttons working (true only for a suggested task) |
| Assignee mail on accept | `mail.queue_tasks_assigned` unchanged: **one mail per person per meeting** (unique index). A person already mailed about the meeting gets no second mail when their suggested task is accepted: it shows on their board. Queued only when the owner's "Email assignees" is on, the person is not the owner, the meeting is not quiet (backfilled) and not older than the outbox keeps (30 days). Alternative rejected: a new mail template and index per accepted task (more mail surface for the pilot); listed as a follow-up |
| Slack and Linear | A suggested task is **not** posted or pushed at extraction. **Accepting counts as a touch** (`edited_at`), and both integrations skip a touched task: a later re-run neither posts it to Slack (`workspace.run_extraction` passes only tasks that are not suggested and untouched) nor pushes it to Linear (`sync_tasks` already skips a touched task). So an accepted task never reaches Slack or Linear. Follow-up if wanted |
| `assigned_people` | Counts only tasks that were made. The designer's script counts every task's owner (including a suggested one's), but a suggested task is not assigned yet; the summary "N tasks created, assigned to M people, K to confirm" is then true |
| API shape | `GET /api/tasks?suggested=true` is the Suggested tasks section's list (the default `false` is the Board and Backlog). The meeting's own list and page `tasks` carry both kinds, each marked |
| Evidence (lead-designed, not in the designer's file) | `quote_speaker` and `quote_line` are computed on read from the transcript string the page shows, split exactly as `web/lib/transcript.ts` splits it (`extraction.TranscriptIndex`, `transcript_turns`), **only for a suggested task and for `GET /api/tasks/{id}` and accept** (one transcript index per meeting per request; a long board list reads none: review finding 5) and only when the caller may read the meeting. `reason` is likewise null for someone who may not read the meeting (it can quote the speaker). An assignee of a private meeting's task gets the quote and nothing around it (spec 8.2). No time is stored or shown. The paraphrase line of the designer's drawer is dropped (U10) |
| Settings PUT | `ask_before_unclear` is **optional** in `PUT /api/settings/tasks` so the current front end keeps working; left out it stays as it is. Turning it off does not touch tasks already waiting |

## What's involved
**Changed (each proven by a test that fails with the change removed):**
| Place | Where |
|---|---|
| Board and Backlog list | `tasks.list_tasks` (`suggested` filter, default false) |
| Attention (needs owner, unverified, check project) | `tasks.attention` |
| Sidebar badge `tasks_new` | `tasks.new_count` |
| Meetings list and meeting page `tasks_count` | `tasks.counts_for_meetings`, `api.py` (`_detail`) |
| Series "Unfinished from last time", "Finished since", cards' unfinished count | `series.unfinished_tasks`, `finished_since`, `counts_for` |
| Series past meetings' task counts | `api_series._page` |
| Project open counts, "in current sprint / in backlog", milestone progress | `projects.open_task_counts`, `sprint_counts`, `milestone_progress` |
| Sprint progress and the count of tasks moved by a close | `api_sprints._counts`, `close_sprint` |
| Per-person open counts on Team | `team_admin.open_task_counts` |
| One-hour reminder mail "Still open from last time" | through `series.unfinished_tasks` (`series_mail._task_rows`) |
| Summary box "closed with N of M tasks done" | `mail_summary.closed_sprint_sentence` |
| Assignee mail ("New tasks assigned") | `new_assignees` (skips suggested), `mail_templates._tasks_assigned` (never lists one), `workspace.run_extraction` |
| Slack and Linear after extraction | `workspace.run_extraction` passes only tasks that are not suggested |
| Meeting task summary | `api_tasks.meeting_tasks` (`to_confirm`, `assigned_people`) |

**Checked and left unchanged (on purpose):**
| Place | Why |
|---|---|
| `tasks.meeting_tasks`, `tasks.get`, `tasks.visible` | The meeting's Action items row and the drawer must show a suggested task; visibility rules are the task's, unchanged |
| `tasks.update` (PATCH) | Editing a suggested task leaves it suggested (never touches `suggested`); `suggested` is not a PATCH field |
| `sprints.close_current` | Moves every open task of the sprint, suggested ones included (only possible when a person placed one by hand); only the count a person sees leaves them out |
| `teams.py` (a person joins or leaves a team) | Re-homes tasks by ownership; it does not count |
| `deletion.py`, meeting delete | Cascades by meeting or maker; no counts |
| `integrations.py` bodies, `mail.queue_tasks_assigned` | Unchanged; their callers pass only tasks that are not suggested |
| `notifications.py`, `alerts.py`, `export*.py`, `sweeps.py`, `scheduler.py`, `web.py` | `grep` finds no read of `tasks` there |
| `scripts/extraction_review.py`, `tests/eval/` | Evaluate the extraction prompt offline; they still run (the label `extract-v1` in the review script's HTML is its own) |

## Verification
* pytest (Postgres 16, port 54346): `tests/test_suggested_tasks.py` (45: reason and confidence checks, quote location, who is suggested, re-run rules), `tests/test_suggested_tasks_api.py` (31: list, accept, dismiss, roles, mail, settings, evidence), `tests/test_suggested_exclusions.py` (8: one test per counting place), `tests/test_migrations.py` (0035 round trip, one head); the whole of `test_accounts`, route enumeration (`test_api_tasks`, `test_team_visibility`), `test_models`, `test_api` and the task, series, project, sprint, mail, integration and extraction suites pass. A mutation check (filters removed) failed ten of these tests, so they do bind.
* Route enumeration: `POST /api/tasks/{id}/accept` is classified as a task write and is 404 for a non-reader and a teammate without access (`test_every_task_route_is_404_for_outsiders_and_teammates_without_access`).
* Real HTTP captures (`docs/fe-captures/suggested-tasks/`, 48 files) feed the guide. **Not live:** the model's real answer (no OpenAI key: stand-in reply), accept filing into a project and sprint, the series, project, sprint and team figures and the reminder and summary mails (unit-tested only).
* `docs/openapi.json` regenerated; `web/lib/api/schema.d.ts` regenerated.

## Operate / roll back
* **Server steps before merge:** the migration runs on deploy (`alembic upgrade head`); **no new environment variable.** One Python deploy, in a no-meetings window.
* **Do not run `scripts/backfill_extraction.py` right after deploy by habit:** `EXTRACT_PROMPT_VERSION` changed to `extract-v2`, so it would re-queue (quietly, no mail) every meeting of the last 14 days that was read with `extract-v1`. They cannot become suggestions (the rule above protects tasks an earlier run made), but the run costs model calls.
* Roll back: revert the PR and `alembic downgrade -1` (drops the four columns; suggested tasks then become plain tasks with no column to say they were waiting, so downgrade after accepting or dismissing the waiting ones).
* The setting can be turned off per person (Settings > Tasks) or by `update user_settings set ask_before_unclear = false` for everyone; waiting tasks stay waiting until handled.

## Review round (Opus review of #135, head 8ebfac0)
Fixed on the branch: (1) Medium: only tasks made by the current extraction may turn suggested (above); (2) accept sets `edited_at`; (3) the Slack and Linear statement is now true (above); (4) `reason` is null for someone who may not read the meeting and the reason check also drops email addresses, phone numbers and `javascript:` or `data:` links; (5) the transcript index is built only for suggested tasks and single-task answers. Also from the lead: the owner of a meeting may accept and dismiss its suggestions (above). **Migration renumbered 0034 to 0035** (Ask Beyric took 0034); chained after 0033 until #134 merges.

## Follow-ups
* A second assignee mail for an accepted task when the person was already mailed about the meeting (needs its own template and index).
* Post an accepted task to Slack or the tracker (needs a rule for "once per task"; accepted tasks are skipped today).
* Real model run: check how often `medium` and `low` are chosen and read the reasons (ledger `ai_runs`, `prompt_version` `extract-v2`) before the pilot relies on it.

---

# PR 2: the screens (`feature/suggested-tasks-screens`, stacked on PR 1; no migration, no config)

## What shipped (PR 2)
1. **Tasks screen, "Suggested tasks" section** (`components/app/suggested-tasks.tsx`, wired in `tasks-view.tsx`, data from `GET /api/tasks?suggested=true` in `app/tasks/page.tsx`): heading, "{N} to confirm" in amber, the designer's sub-line, and per row the title, the confidence pill (Medium amber, Low red, High green), the evidence box, the reason in amber, the assignee or "No owner", **Show source / Edit / Dismiss / Accept**. Hidden when none. Rows are oldest first (the designer's order; the API sends newest first).
2. **Toasts:** "Task added to the board" (Accept), "Suggestion dismissed" (Dismiss).
3. **Task drawer** (`task-drawer.tsx`): the amber box "Suggested task, medium confidence. {reason}" with Accept and Dismiss; "FROM THE MEETING" is now the evidence card (initials circle, speaker, the quote, "Show in transcript") for every task with a quote.
4. **Meeting page, Action items** (`action-tasks.tsx`, `lib/tasks.ts actionSummary`): pill **"To confirm"** on a suggested row (in place of "Needs owner"), summary "N tasks created, assigned to M people, K to confirm" (tail only when K is above 0; a suggested task and its person are not counted), a drawer with the same Accept and Dismiss, and the toasts.
5. **Settings > Tasks:** the switch **"Ask before adding unclear tasks"** with the designer's sub-line, saving all three values together (`settings-tasks.tsx`; also drawn off in the fallback card of `settings-planned-sections.tsx`).
6. Shared `components/app/use-suggested.ts` (Accept and Dismiss, one at a time, the failure toast, a 404 drops the row), `lib/tasks.ts` helpers (`confidenceLabel`, `suggestedLead`, `transcriptHref`), `lib/api/client.ts` (`tasks.accept`, `tasks.list({ suggested })`), styles in `web/styles/pages.css` (`tk-sg-*`, `tk-conf-*`, `td-suggest-*`, `td-ev-*`).

## Lead-designed, not in the designer's file (each labelled in the PR)
* The evidence box and the drawer card show the **verbatim `quote` and its speaker, with no time** (none is stored); the designer's paraphrase line is dropped (U10). With no `quote_speaker` (an assignee of a private meeting's task) the box shows the quote alone.
* **Show source** (and "Show in transcript") link to `/meetings/{id}?tab=transcript&line=N` (`quote_line` from the API). The meeting page already opens the Transcript tab for `?tab=transcript`; the row highlight is `ask-screens`' piece, so until it merges the link just opens the tab. No Show source for someone who may not read the meeting.
* **Edit** opens the drawer and the task stays suggested until Accept (U11).
* A **failed** Accept or Dismiss shows the toast **"Could not update the task. Try again."** and the row stays (a 404 drops it).
* **Phone layout:** the row's text first, then the four buttons stacked full width (`@media (max-width: 560px)`).
* Accept and Dismiss follow `can_decide` and are disabled (not hidden) when it is false (a teammate who only reads the task; the owner of the meeting may decide, see PR 1 "Who may").
* The drawer of a task whose list row has no `quote_speaker` (the Board lists carry it for suggested tasks only) asks for the one task (`GET /api/tasks/{id}`) to show who said the quote; a failed call leaves the quote without a speaker.

## Deviations from the designer's file
* **Row position of the Settings switch:** the file puts "Ask before adding unclear tasks" **second** (between "Create tasks from action items" and "Email assignees"); the spec and audit call it the "third row". Built as drawn.
* The designer's drawer shows the paraphrase grey line under the evidence card; dropped (above). The designer's sample tasks carry a project and sprint (Client Portal, Sprint 13) while waiting; the server makes them unfiled until accepted, so the mock's drawer says "No project" and "Backlog" for them.
* The design draws the Needs-owner pill only for a task that is not suggested; the same rule is used.

## Verification (PR 2)
* `npm run typecheck` clean, `npm run lint` no new warning (the 3 existing ones are in `scripts/parity-meeting-delete.mjs`), `npx vitest run`: 886 passed (28 new in `tests/suggested-tasks.test.tsx`, fed the real captures in `docs/fe-captures/suggested-tasks/`; three existing tests updated for the new quote card and the extra Settings switch).
* **Parity shots** (`web/scripts/parity-shots.mjs`, design beside page, 1440, 768 and 390) built against `web/scripts/mock-api.mjs` and saved under `docs/ui/parity/after/`: `app-tasks`, `app-tasks-suggested-edit` (drawer), `app-tasks-suggested-accept` and `-dismiss` (toasts), `app-detail` (Action items), `app-settings`, `app-tasks-drawer`, each at the three widths (22 files). Read side by side at 1440: the section, the pills, the evidence boxes, the reasons, the buttons, the drawer box and the Action items rows match the file; differences are the ones listed above. At 390 the prototype itself is not responsive (it is squashed), so the phone layout is checked against the lead-designed rule only.
* The shots use `--now 2026-11-02T10:00:00Z` as every earlier parity run does; with that fixed clock the server render and the browser disagree on "today" and React logs hydration error #418 on `/tasks`. Without `--now` the same page logs none (checked).
* **Not covered:** the failed-accept toast and the "Show in transcript" highlight (needs `ask-screens`) are unit-tested, not shot; no real browser run against the real API.

## Operate / roll back
Front end only. Revert the PR; nothing is stored by it. Needs PR 1 on the server (it reads `suggested`, `confidence`, `reason`, `quote_*` and calls `POST /api/tasks/{id}/accept`).

## Follow-ups (PR 2)
* The row highlight of "Show in transcript" arrives with `ask-screens`; recheck the link then.
* A shot of the failed toast needs a mock switch (`/__mock/set`); add if wanted.
