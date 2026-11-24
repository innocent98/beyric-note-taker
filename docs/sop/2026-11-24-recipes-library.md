# SOP: Meeting recipes, slice S3 (starters, recipe library, team brief, file import)

**What shipped.** The eighteen starter recipes (ten General, eight School) as code constants; the recipe library routes (`GET/POST/PUT/DELETE /api/recipes`: personal recipes, team recipes written by owners and admins only, delete = archive); the team brief routes (`GET/PUT /api/briefs`); and the brief file import (`POST /api/briefs/import`: Word, PDF, text and Markdown into text for review, never stored). Backend part (T10 and T11): PR into `feature/recipes` (number in the PR list there). The web part (T12: Make dialog, library page, brief editor) lands after the engine PR (#265) merges: see "T12" below. Spec `docs/superpowers/specs/2026-11-24-meeting-recipes-design.md` (5.1 to 5.4, 5.3a, 5.6), plan `docs/superpowers/plans/2026-11-24-meeting-recipes.md` tasks 10 to 12. No migration: the tables came with `0060` (slice S1).

## Why

The engine (S1) makes a draft from a recipe, but nothing says what the recipes are, lets a team write its own, or gives the model the team's own prices and terms. Adebayo wants the follow-up email and proposal to come out in the team's own words and numbers, and students and lecturers to get the class documents they need, without any draft inventing a name, price, date or answer.

## How, and what was decided

| Decision | Choice | Why (and what was rejected) |
|---|---|---|
| Starters | `app/recipes/library.py`: a frozen `Starter` per recipe in `STARTERS` (the name `service.starter_for` already reads). Not rows: a starter can be improved by a deploy and needs no seeding or per-team copy until someone edits it | Seeded rows would drift from the code and need a migration per prompt change |
| Prompts | Each instruction states what the document is, the headings in order, which facts come from where, then one shared grounding paragraph (`GROUND`): use only the meeting material and the brief, never invent a name, number, date, price, commitment or answer, write `[need: <what>]` instead. The engine's system prompt already carries the truthfulness rules and the fence; the instruction adds the document | The rules live in two places on purpose: the system prompt is the author's guard, the instruction keeps a copied recipe grounded even when its author rewrites the rest |
| Dates in school recipes | `assignments` and `revision_plan` tell the model to write a date **as said** ("next Friday") and never to work out a calendar date; `revision_plan` labels days "Day 1, Day 2" and ends on the date the student entered | A date the model computes is a figure that is in no source: the number audit would flag it, and it could be wrong |
| Scope of `class_faq` | **`one`**, although the spec table says "1 class or several" | The engine sends the whole transcript only for scope `one` (`service.py`: `whole_transcript = scope == "one" and len(meetings) == 1`); a FAQ of what students asked and the lecturer answered needs the transcript. **Decision for the lead:** to allow several classes, change that rule to `len(meetings) == 1` regardless of scope, then set this starter to `many` |
| `options` | A recipe's `options` are the **choices the Make dialog draws**: `{key: {label, kind: "text"\|"choice", choices, default}}`, checked by `library.clean_options` (at most six, keys like `tone`, choices 1 to 6 short words, unique). What the person picks goes in `POST /api/documents` `options`; the instruction names a choice by its key and says what to do when it is missing | The schema column was an open dict; the dialog needs a shape to draw. The API always returns `choices` and `default` (empty and `null` for text) so the FE types are flat |
| Who writes what | A personal recipe is private; a recipe with `visibility: "team"` is the **active team's**. Writing one (create, edit, archive) needs `team_admin.require_manager`, the Team screen's own rule, so a member gets 403 `team_forbidden`; members still read, use and copy it, and can write personal ones. A recipe or team that is not the caller's is 404, never 403 | Spec 5.4 item 6: a team recipe's author is trusted staff, never an arbitrary member (that settles prompt injection through a shared recipe) |
| Edit and move | `PUT` changes only the fields sent; **personal vs team never changes** | Moving a recipe to a team would let a member's text become team policy without an owner's review |
| Copy a starter | `POST /api/recipes` with `key`: anything left out is taken from the starter; the row keeps `key` ("copied from") | The plan's "a copy of a starter is editable" with one call |
| Delete | Archive (`archived_at`): not listed, not usable, drafts keep their `recipe_id` | Plan; drafts already reference it |
| Bounds | name 100 characters, instruction 4000, 100 live recipes per person and per team (`too_many_recipes`) | Keeps one account from filling the table; the model prompt stays bounded |
| The brief | `team_briefs`, one row per team (the personal team has its own); `PUT` upserts (`ON CONFLICT (team_id)`), owners and admins only, members read. Text is stored as typed except control and hidden-direction characters and `\r\n`; the cap is `brief_max_chars` read from settings at call time, 422 `brief_too_long` | Spec 5.4: members see its prices in the drafts they make anyway. The cap is checked after cleaning so the number shown to the person is what is stored |
| Import: where the file goes | **In memory only.** The route is `async`, reads the body itself with a running size limit, and parses the multipart body with `python_multipart`'s low-level parser into bytes. Starlette's own form parsing would spill a file over 1 MB to a temporary file on disk, so `UploadFile` is not used | The decision was "never stores the file"; the test makes any temp-file creation fail and uploads 2.4 MB |
| Import: parsing | Word and PDF files are opened in a **child process** (`app/recipes/import_child.py`, as `app/export.py` does for PDF export) with a 20 s hard time limit, at most two at once (else 503 `import_busy`), PATH-only environment, and on Linux a memory and CPU cap. Plain text is read in the web process | A file made to hang or eat memory is killed and its memory returned; pypdf and python-docx are not trusted with hostile input in the long-lived process |
| Import: zip bombs | Before any inflating, the zip directory is checked: at most 500 members, 40 MB declared in all, no member over 25 MB, and no member over 1 MB that squeezes more than 100 to 1 (`too_big`). Python's `zipfile` stops reading a member at its declared size. python-docx's parser does not resolve XML entities (a billion-laughs docx is a test) | Spec: size, page and member limits. Mutation-checked: removing the ratio rule fails its test |
| Import: reading | Word: body paragraphs and tables in document order; a table row is one line with the cells joined by ` \| `, a merged cell once. PDF: `pypdf`, text of each page, stops at twice the cap, `no_text_layer` when nothing comes out, password-locked is `unreadable` (empty password tried first) | Only the new dependency `pypdf` (pinned `>=7.1`); `lxml` and `python-docx` were already there |
| Import: who and what it returns | Owners and admins only (403 `team_forbidden`): they are the only people who can save a brief. Returns `{text, truncated, pages}`, saves nothing; the text is cut at `brief_max_chars` and says so | Spec 5.2: "returns text for the person to review"; a member's upload would only cost CPU |
| nginx | `deploy/nginx.conf`: one `location ~ ^/api/briefs/import/?$` with `client_max_body_size 6m` (the server default stays 1m), `proxy_request_buffering off` (no temp file at the edge) and its own rate zone `notes_import` (20 a minute, burst 5); no `proxy_set_header` inside it (that would drop the edge secret, as `tests/test_edge.py` warns). The guard test parses the file and checks the limit covers `IMPORT_MAX_BYTES` plus the envelope, that no other location raises the limit, and what the pattern matches | Spec and plan: raise the limit for that one route only |
| Imported and typed text is data | Never interpreted on the way in or stored differently; the brief reaches the model only through the engine's fence (`generate._fence` neutralises every fence tag). A test pins that a line saying "ignore all rules" is stored and returned unchanged | Review Focus item 2 (T4 owns the fence test against the model prompt: it needs the engine's `generate`, which this branch does not have yet; see Follow-ups) |

**LEAD-DESIGNED pieces (not in the designer's files):** the Make dialog (grouped starters, My recipes, options, active team's brief with a switch), the recipe library page and recipe editor, the brief editor with file import, and the nav entry. Listed in full in the T12 section when it lands. The wording the FE shows for import failures comes from the API (`importer.MESSAGES`), written for the person.

## What's involved

| Path | Change |
|---|---|
| `app/recipes/library.py` | `Starter`, `STARTERS` (18), `starter_by_key`, `clean_options`, bounds (`MAX_INSTRUCTION_CHARS`, `MAX_RECIPES`, ...) |
| `app/api_recipe_library.py` | `GET/POST /recipes`, `PUT/DELETE /recipes/{id}`, `GET/PUT /briefs`, `POST /briefs/import`; its own request and response models (nothing added to `app/api_schemas.py`, to keep this slice's files apart from the engine's); included from `app/api.py` |
| `app/recipes/briefs.py` | `read`, `save` (cleaning, cap, upsert), `max_chars` |
| `app/recipes/importer.py` | `extract_text`, `ExtractedText`, `ImportRefused` and its messages, the child runner, concurrency slots |
| `app/recipes/import_child.py` | the child: `read_docx`, `read_pdf`, `check_zip`, `clean_text`; imports nothing from the app |
| `deploy/nginx.conf`, `deploy/README.md` | the import location and zone; the README paragraph and the hand-edit instruction for an installed server |
| `pyproject.toml` | `pypdf>=7.1` |
| `docs/openapi.json` | regenerated with `PYTHONPATH=. python scripts/export_openapi.py` (without `PYTHONPATH=.` it imports the main checkout's `app`) |
| `tests/test_accounts.py` | the IDOR guard gains `recipe_id` (a stranger's PUT and DELETE of the owner's recipe are 404 and change nothing) |
| `tests/test_recipe_library.py`, `tests/test_recipe_briefs.py`, `tests/test_recipe_importer.py` | 80 tests |
| `scripts/capture_recipes_library.py`, `docs/fe-captures/recipes-library/`, `docs/fe-integration-guide-recipes-library.md` | live captures (60 exchanges) and the guide |

## Verification

- Local: `ruff check .`, `ruff format --check .`, and only the touched test files (80 tests in the three new files), the IDOR guard, and the route-walking guards in `tests/test_api.py` (login, CSRF, openapi file). CI runs the rest.
- Mutation checks: removing the merged-cell rule and removing the compression-ratio rule each fail their test.
- Live: `scripts/capture_recipes_library.py` drove the real app over HTTP (real migrations, real child process for each file kind). Not live: nginx and the Next.js front door (see the guide's verification table), the 100-recipe cap, a password-locked PDF, the 20 s limit.

## Operate / roll back

- **Server step before this reaches `main`:** edit the live nginx file by hand (never copy over it): add `limit_req_zone $binary_remote_addr zone=notes_import:1m rate=20r/m;` beside the other zones and the `location ~ ^/api/briefs/import/?$ { ... }` block (copy from `deploy/nginx.conf`) beside the `regenerate` one, then `sudo nginx -t && sudo systemctl reload nginx`. Without it the import answers 413 from nginx for any file over 1 MB, and the front door is untouched.
- **New dependency:** `pypdf` (the image rebuild installs it).
- Settings it reads: `BRIEF_MAX_CHARS`, `IMPORT_MAX_BYTES`, `IMPORT_MAX_PAGES` (added in S1). Roll back by reverting the PR; no data migration.

## Follow-ups

- **The fence test against the real prompt** ("an imported line 'ignore all rules' is fenced at generation", plan T11/T4) needs `app/recipes/generate.py` from PR #265; add it once #265 is merged into this branch.
- Check a real upload **through nginx and the Next.js front door** (the front door buffers a proxied body up to its own limit, 10 MB by default; ours is below it) at the first staging run.
- `class_faq` over several classes (see the decision above).
- Option choices are English words stored as typed; labels are not translated.

## T12 (web)

Pending: starts after #265 merges (regenerated types needed). This section is completed then.
