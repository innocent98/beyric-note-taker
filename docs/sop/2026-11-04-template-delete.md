# SOP: Delete a template

## What shipped
`DELETE /api/templates/{id}` and the designer's red **Delete template** button on the Templates screen, with an inline two-step ask. Branch `feature/template-delete`, PR in the report. **One migration (0014), no new variable, no nginx change.** A new field `template_name` is on every meeting body (list item and detail).

## Why
Adebayo noticed the button the designer draws (`Beyric Note Taker.dc.html`, Templates screen) was missing. The parity report listed it under "needs backend": the API had no delete, and a plain delete could not work, because `meetings.template_id` is `NOT NULL` with a foreign key and the Meeting page builds its "Template" row from the id (no name is stored on the meeting).

## How
| Decision | Why | Rejected |
|---|---|---|
| **Soft delete**: `templates.deleted_at`; the row stays. Every way to *choose or list* a template filters it out (`services._own_template`, `list_templates`, the duplicate-name check) | A meeting keeps its notes **and** the id it was written with; a meeting still `bot_sent`/`processing` finishes with its template (`pipeline._step_notes` does `session.get(Template, meeting.template_id)`); a "Rewrite notes" in flight finishes; nothing needs a nullable foreign key or touches the join runner or `placement.py`, which read template ids | Hard delete with `meetings.template_id` nullable and `ON DELETE SET NULL`: the in-flight worker would get no template, `place_bot` raises on a missing one, the API type of `template_id` would change for every consumer |
| Name unique **among live rows**: the constraint `uq_templates_owner_name` becomes a partial unique index (`where deleted_at is null`, same name) | A deleted name can be used again | Keep the plain constraint: "Interview" could never be re-added |
| `template_name` on `MeetingSummary` (so on list and detail): the template's current name, or the name it had when deleted (`services.template_names`, one query per list) | The design stores the name on the meeting, the API did not; the FE looked names up in the live list, which loses deleted ones. A snapshot column would go stale on rename | A name column on `meetings`: needs a backfill and every place that sets `template_id` (including other sessions' code) |
| **The last template cannot be deleted**: 409 `last_template`, "Keep at least one template. Add another one first, then delete this." | Notes need a template: a person with none could not send a bot (`template_required`) and the calendar has nothing to fall back on. The design's `deleteTemplate` filters the list and does not say whether an empty list is allowed (its own forms never handle one) | Allow it |
| Built-in templates | They are per-user rows (migration 0007, `accounts.add_user_defaults`), not shared, and carry no flag: any of them can be deleted like the person's own, down to the last | A protected "General" |
| **Calendar setting**: if `user_settings.calendar_template_id` is the deleted one it becomes the person's **oldest remaining** template (a concrete id, not `null`), and `gcal_sync.set_join_template(..., commit=False)` moves the joins that have not sent a bot (`scheduled`, `no_capacity`); a join whose bot is in keeps its template. One commit | `null` also means "default (oldest)" (#68), but `placement._resolve` raises on a `null` template unless the caller resolved it; a concrete id cannot 500 whatever C12 does. The select shows the same template either way | `null` |
| **Race**: the person's live templates are read `FOR NO KEY UPDATE` (does not stop a meeting being created with one of them), then decided: not in the set is 404, the set has one row is 409. Two deletes at once are decided one after the other: a double delete is 204 then 404, the last two templates deleted at once leave one | A count without a lock lets two requests each see two templates and both delete | A lock on the user row |
| Frontend: the button is the designer's (`font 13/500`, `#B42318`, `padding 6px 0`, no border) **left of Save in the foot row** (`space-between`). A saved template **asks first** inline, in the Decisions style: "Delete this template? Meetings already written with it keep their notes." with **Delete** (secondary) and **Cancel** (quiet). An unsaved new template is only discarded (no ask, no call). After a delete the **first template of the list** is selected, as the design's `selectTpl(0)` does | The design deletes at once; a delete cannot be undone. The brief said "neighbouring template"; the design selects the first, and the design wins | |
| Errors: 409 shows the server's sentence; 404 removes the template from the list and says "That template was already deleted."; network and others use `errorMessage` | | |

## What's involved
| Path | Change |
|---|---|
| `alembic/versions/0015_template_soft_delete.py` | `deleted_at`; partial unique index; downgrade renames deleted rows (" (deleted <id>)") so the plain constraint can return |
| `app/models.py` | `Template.deleted_at`, index replaces the unique constraint |
| `app/services.py` | `delete_template`, `template_names`; live filters in `_own_template`, `list_templates`, `_save_template` |
| `app/api.py`, `app/api_schemas.py` | `DELETE /templates/{template_id:id}` (204), `template_name` on `MeetingSummary`; `_summary` takes the name |
| `docs/openapi.json`, `web/lib/api/schema.d.ts` | regenerated |
| `web/lib/api/client.ts` | `api.templates.remove` |
| `web/components/app/templates-editor.tsx`, `web/styles/pages.css` | button, ask, `.tpl-foot*`, `.tpl-confirm`, `.tpl-delete` (shares the red text rules of `.cal-text-danger`) |
| `web/components/app/meeting-view.tsx`, `meeting-list.tsx` | name falls back to `template_name`; the rewrite select starts on a live template |
| `scripts/capture_template_delete.py`, `docs/fe-captures/template-delete/` (26 files) | real-HTTP captures |
| `web/scripts/{mock-api,parity-shots}.mjs`, `parity-template-delete.mjs` | mock replays the delete; ask shot; position and style diff |
| `tests/test_template_delete.py` (34), `tests/test_accounts.py`, `test_api.py`, `test_migrations.py`, `test_calendar_settings_api.py`, `web/tests/*` | tests |
| `docs/fe-integration-guide-owner-app.md` 5.6a, `docs/ui/parity-report.md`, checklist | docs |

## Parity with the designer's file
Method of `docs/ui/parity-report.md`: built app (`next build`, standalone) on the mock, beside `Beyric Note Taker.dc.html` in Chrome; positions measured inside the editor card (`web/scripts/parity-template-delete.mjs`). Screenshots: `docs/ui/parity/after/app-templates-{1440,768,390}.jpg` (design left, built right) and `app-templates-ask-*` (built only, the ask).

| Element (1440) | Design | Built | Result |
|---|---|---|---|
| "Delete template" box in the card | x 23, y 332, 98×29 | same | identical |
| Its styles | 13px / 500, `rgb(180, 35, 24)`, transparent, no border, padding 6px 0, pointer | same | identical |
| Foot row | x 23, y 325, 730×40, `space-between`, centre, gap 12, padding-top 4 | same | identical |
| Save | 36px high, 13/550, padding 0 18, `#0E6B5E` | same, **68 wide against 67, 1px border** | **pre-existing**: the shared `.btn` has a 1px transparent border; not changed here |
| 768 and 390 | the prototype's app is not responsive | the app's engineered layout; the button stays left, Save right; the ask wraps above Save on a phone | not comparable, as every screen |

## Verification
| Check | Result |
|---|---|
| `tests/test_template_delete.py` | 34 on a real Postgres 17 (`nt-test-pg-td`), written first (28 failed). Covers: 204 and list; 401, CSRF; foreign, unknown, huge ids 404; second delete 404; edit of a deleted one 404; name reuse and duplicate; last template (also with deleted ones, per user); the four seeded templates; meeting keeps notes and name, live rename; rewrite and new meeting with a deleted id 400; meeting in `processing` finishes with it; **rewrite in flight deleted meanwhile finishes (200, no 500)**; calendar setting (oldest left, skips the deleted oldest, left alone elsewhere, `null` stays, other user untouched); planned joins move, sent join keeps; two deletes at once (**real threads** against the row lock) |
| Enumeration test | `test_every_api_route_with_an_id_returns_404_for_another_users_row` now classifies and **requests `DELETE /api/templates/{id}` as the stranger with the owner's id (404 `not_found`)** and checks the template is still live. Mutation: removing the owner filter from the delete query makes it fail (204) |
| Migration | upgrade, a deleted row may share a live name, a live duplicate is refused, downgrade to 0014 renames deleted rows and restores the constraint, upgrade again |
| Mutation checks | owner filter in the delete (enumeration test fails). Not run: the lock, the calendar move (their tests assert the outcomes) |
| Python | full suite 3067 passed; `ruff check`, `ruff format --check`, `export_openapi.py --check` clean |
| Web | `npm run lint`, `typecheck`, `npx vitest run` (500 tests) clean; new tests use the captured messages (`capturedDelete`) |
| Live capture | 26 files from the running app over real HTTP, including the table rows before and after |
| **Not verified** | A real browser session against real FastAPI (the browser run used the mock); Safari and Firefox; the live site; the web mutation checks; the Review page's offer for a meeting whose template was deleted |

## Operate / roll back
- **Server steps before merge: none** (no variable, nginx edit). Merging runs migration 0015 (additive; takes a moment on the `templates` table, a few rows). Check there is no meeting `bot_sent` or `processing` before merging, as always.
- Roll back: revert the PR and run `alembic downgrade 0014` (deleted templates are renamed, not lost). Without the downgrade the extra column is harmless.
- A deleted template's text stays in the database (the row is kept). Account deletion removes all of a person's templates (`app/deletion.py` deletes by owner).

## Designer gaps (for the designer to review)
| Gap | What was built |
|---|---|
| The design deletes at once, no confirmation | An inline ask: "Delete this template? Meetings already written with it keep their notes." Delete / Cancel (the Decisions style) |
| The design does not say if the last template may go (it filters the list; its forms never handle an empty one) | Refused with a plain message (409); the button stays visible |
| No error line or "deleting" state | A red alert at the top of the editor card (as for Save) and "Deleting..." on the ask's button |
| Deleting an unsaved new template | Just discards it, no ask |

## Follow-ups
- The Review page's "Rewrite notes with the corrected spelling" offer uses the meeting's own template id: for a meeting whose template was deleted it answers 400 "Choose a template." (the meeting page's select handles it). Could fall back to the first template.
- `placement.py` and the join runner still accept a template id that is soft-deleted if one is passed explicitly (nothing passes one: the setting and joins are moved). C12 should read `scheduled_joins.template_id`/`calendar_template_id` as live ids (`deleted_at is null`) if it ever resolves one itself.
- Designer to rule on the ask and on the last template.
