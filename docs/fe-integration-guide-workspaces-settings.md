# FE guide: workspace settings (templates, vocabulary, per-workspace preferences, born-shared meetings)

Slice W2 of the workspaces release. Every body below is copied from a real response of the running app, captured by `scripts/capture_workspaces_settings.py` (real migrations, the real app under uvicorn, real HTTP) into `docs/fe-captures/workspaces/settings-NN-*.json`; the capture number is given for each. What the script could not do is in the verification table at the end. Every person, id and address is a placeholder (ids are those of a scratch database). Read `docs/fe-integration-guide-workspaces.md` first: the active workspace is the `X-Team-Id` header on every route (no header: the person's default workspace), and another workspace's data is a 404.

Cast of the captures: Ada owns team X ("Platform team"), Pat is a member, Quinn an admin; each has a personal workspace.

## 1. What changes for the screens

| Thing | Rule |
|---|---|
| Templates, vocabulary | belong to the ACTIVE workspace. Owners and admins write; members read and use. |
| New workspace | starts with the four seeded templates (General, Client call, Team update, Interview). |
| Note taker name, auto-join rule, the two auto-join boxes, calendar template | per person per workspace: `GET/PUT /api/workspace-prefs`. A workspace the person never changed shows their own defaults. |
| Name, time zone, language, email, billing, "your default note taker name" (`/api/settings/bot-name`) | personal, never switch. |
| A meeting born in a team workspace | is shared with the workspace from the first second (if the team's "share new meetings" switch is on, which it is for every team made from now on). "Keep private" is the existing `DELETE /api/meetings/{id}/share`. The personal workspace stays private. |

## 2. Templates

`GET /api/templates` (any member; header names the workspace). Capture 07 lists a new workspace's templates (the seeded ones); ids are scratch ids.

`POST /api/templates` body `{"name","instructions"}` answers 201 with `{"id","name","instructions"}` (capture 10, as the owner).

`PUT /api/templates/{id}` replaces name and instructions (capture 11, as an admin: 200). `DELETE /api/templates/{id}` is 204; the only template of a workspace is 409 `last_template` (as before, per workspace).

Errors the screen must handle, verbatim:

| When | Status | Body | Capture |
|---|---|---|---|
| a member writes (POST, PUT, DELETE) | 403 | `{"error": {"code": "team_forbidden", "message": "Only an owner or admin of the team can do that."}}` | 08, 09 |
| a name already live in the workspace (whoever wrote it) | 400 | `{"error": {"code": "duplicate_name", "message": "A template with that name already exists."}}` | 12 |
| a template of ANOTHER workspace addressed with this header | 404 | `{"error": {"code": "not_found", "message": "No such template"}}` | 15 |
| a workspace the caller is not in | 404 | `{"error": {"code": "not_found", "message": "No such team."}}` | 16 |

The 404 comes before the 403: a member addressing a template that is not the workspace's gets 404, never 403.

**Copy from my personal workspace** — `POST /api/templates/copy-from-personal`, no body, header = the TARGET workspace. Owner or admin of a team workspace only. Capture 18 (Ada, who had one personal template that X lacked):

```json
{"copied": 1, "skipped": 4, "templates": [ ...the workspace's six templates: Board meeting, Client call, General, Interview, One to one, Team update... ]}
```

Again (capture 19) gives `copied` 0 (nothing is overwritten or doubled). A member: 403 `team_forbidden` (capture 20). Into the personal workspace: 409, `{"error": {"code": "personal_team", "message": "This is your personal workspace: there is nothing to copy into."}}` (capture 21).

**UX instructions.** A member must see the list and the chosen template read-only, with no Add, Save or Delete (the web does this from `GET /api/team` `can_manage`). A rewrite on a meeting offers the templates of the MEETING's workspace (send the meeting's workspace in the header); choosing a personal template of the recorder is 400 `template_required`.

## 3. Vocabulary

`GET /api/vocabulary` → `{"terms":[{"id","term"}]}` (alphabetical). `POST` `{"term"}` → 201 `{"id","term"}` (capture 23, owner). `DELETE /api/vocabulary/{id}` → 204.

| When | Status | Body | Capture |
|---|---|---|---|
| a member adds or removes | 403 | `{"error": {"code": "team_forbidden", "message": "Only an owner or admin of the team can do that."}}` | 25, 26 |

`POST /api/vocabulary/copy-from-personal`: the second call (capture 28) answers `{"copied": 0, "skipped": 2, "terms": [{"id": 3, "term": "Beyric"}, {"id": 2, "term": "Luran"}]}` (ids are scratch ids). It is the vocabulary used for every meeting recorded in that workspace, not the recorder's.

**UX consequence.** A member's "Add to vocabulary" on a Review item in a team workspace is now 403 `team_forbidden` (only owners and admins write the list): hide or explain it, do not show a bare error. (Not captured live; the same rule as the routes above, see the verification table.)

## 4. Per-workspace preferences

`GET /api/workspace-prefs` (header = the workspace). Capture 29, a person who never changed anything in X:

```json
{"team_id": 4, "bot_name": null, "bot_name_default": "Beyric Meeting Support", "warning": null, "auto_join_rule": "all", "join_unanswered": false, "skip_one_on_ones": false, "calendar_template_id": null}
```

`bot_name` is the name set for THIS workspace (null: none, the default applies); `bot_name_default` is what then applies (the person's own default from Personal settings, else the server's).

`PUT /api/workspace-prefs`, body any of `bot_name`, `auto_join_rule` (`all`|`organizer`|`none`), `join_unanswered`, `skip_one_on_ones`, `calendar_template_id`; a field left out stays, `bot_name: null` clears. Capture 30 (`{"auto_join_rule": "none", "bot_name": "Pat from Platform"}`) answers the whole state:

```json
{"team_id": 4, "bot_name": "Pat from Platform", "bot_name_default": "Beyric Meeting Support", "warning": null, "auto_join_rule": "none", "join_unanswered": false, "skip_one_on_ones": false, "calendar_template_id": null}
```

Captures 32 and 33 show the personal workspace holding a different rule and X untouched. Clearing the name (capture 34) keeps the rule: `bot_name` null, `auto_join_rule` still `none`.

| When | Status | Body | Capture |
|---|---|---|---|
| a name with a web address (or other refused name) | 422 | `{"error": {"code": "bot_name_invalid", "message": "Don't put a web address in the name."}}` | 35 |
| `calendar_template_id` of another workspace | 404 | `{"error": {"code": "not_found", "message": "No such template"}}` | 36 |

**Trap.** `PUT /api/calendar/settings` and this route write the same rows (the connection's workspace for the calendar route, the active one here). `GET /api/settings/bot-name` is the PERSONAL default and does not switch.

## 5. Born shared (D4)

`POST /api/meetings` into a team workspace (header + a template of that workspace): the meeting is born in the workspace and shared with it. Capture 38 (Pat, a plain member; stand-in bot provider, no bot sent): 201 with `"workspace_team_id": 4`, `"shared_with_team": true`, `"can_share": true`. Ada reads it at once (capture 39: 200, `shared_with_team` true, `shared_by` "Pat Example"). After `DELETE /api/meetings/{id}/share` ("keep private", capture 40: 200, `shared_with_team` false) Ada gets 404 (capture 41: `{"error": {"code": "not_found", "message": "No such meeting"}}`). The meeting stays filed in X (`workspace_team_id` unchanged). A meeting born in the personal workspace is private (capture 43: `workspace_team_id` 2, `shared_with_team` false). A personal template in a team workspace is refused (capture 44): `{"error": {"code": "template_required", "message": "Choose a template for the notes."}}`.

**UX consequence the FE must surface.** In a team workspace the "Share with team" switch on a new meeting is ON by default, so say so where the meeting is shown ("Shared with <workspace>. Keep private") and keep "Keep private" one click away. Existing meetings are never changed by this.

## 6. Verification

| Behaviour | Verified |
|---|---|
| Templates: list, create, update, 403 for a member, duplicate name, 404 other workspace and other team | live, captures 07 to 16 |
| Copy templates and vocabulary, skip, 403, 409 | live, captures 18 to 21, 27, 28 |
| Vocabulary routes and 403 | live, captures 23 to 26 |
| `/api/workspace-prefs` get, put, per-workspace separation, clear, 422, 404 | live, captures 29 to 36 |
| Born shared, keep private, personal private, template of another workspace | live, captures 38 to 44 (stand-in bot provider: no real bot joined) |
| Review item "Add to vocabulary" 403 for a member | NOT captured live; covered by the service rule `add_term_from_review` and the manager check shared with the routes above |
| A rewrite of a moved meeting by an admin, the pipeline using the workspace's vocabulary | NOT captured live (needs a model call); proved by `tests/test_workspace_pipeline.py` with a recording fake model |
| The calendar settings route writing the connection's workspace row | NOT captured live (needs a Google connection); proved by `tests/test_workspace_prefs.py` |
| Mail (reminder join sentence reads the series' workspace) | NOT captured live (no mail leaves the scratch environment); proved by `tests/test_workspace_pipeline.py` |
