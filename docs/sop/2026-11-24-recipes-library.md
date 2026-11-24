# SOP: Meeting recipes, slice S3 (starters, recipe library, team brief, file import)

**What shipped.** The eighteen starter recipes (ten General, eight School) as code constants; the recipe library routes (`GET/POST/PUT/DELETE /api/recipes`: personal recipes, team recipes written by owners and admins only, delete = archive); the team brief routes (`GET/PUT /api/briefs`); and the brief file import (`POST /api/briefs/import`: Word, PDF, text and Markdown into text for review, never stored). Backend part (T10 and T11): PR into `feature/recipes` (number in the PR list there). The web part (T12: Make button and dialog on the meeting page, the Recipes page with the recipe editor and the brief editor with file import, a Recipes link in the sidebar) is in the same PR: see "T12" below. Spec `docs/superpowers/specs/2026-11-24-meeting-recipes-design.md` (5.1 to 5.4, 5.3a, 5.6), plan `docs/superpowers/plans/2026-11-24-meeting-recipes.md` tasks 10 to 12. No migration: the tables came with `0060` (slice S1).

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

- Check a real upload **through nginx and the production Next.js build** at the first staging run. Done here: a 2.4 MB upload through the Next *dev* server to the real API (see T12 verification); not done: nginx, and the standalone production server.
- The draft page `/documents/{id}` (slice S2, T9) is where the Make dialog navigates; until S2 merges it is a 404 page.
- The meeting picker (slice S5) mounts in the Make dialog through `renderMeetingPicker`.
- `class_faq` over several classes (see the decision above).
- Option choices are English words stored as typed; labels are not translated.

## T12 (web)

**What shipped.** The Make button and dialog on a ready meeting, the Recipes page (the brief editor, the library list and the recipe editor), and a Recipes link in the sidebar. Plan task 12, tests-first; the API is the one above and `POST /api/documents` from the engine (PR #265).

### Lead-designed pieces (the designer's files draw none of recipes; existing components and tokens only)

| Piece | Where | Built from |
|---|---|---|
| **Make…** button | `web/components/app/meeting-view.tsx`, in the `trailing` slot of the header actions, before Share notes; drawn on a ready meeting with notes for anyone who can read it (not only the owner, unlike Share) | `Button` secondary, `Icon plus` |
| **Make dialog** ("Make from this meeting") | `web/components/app/make-dialog.tsx` | the Share dialog's frame (`dialog tm-dialog sh-dialog`, head, body, foot) |
| recipe chooser: General, School, Team recipes, My recipes, each recipe a radio card (name and output type, "several meetings" for scope many); the list scrolls inside the dialog (252 px) so choices and buttons stay in view; the foot is sticky | same | new classes `mk-*` in `web/styles/pages.css` from existing tokens |
| the recipe's **choices** (a text field, or the segmented word control) | same | `Field`, `.segmented` (the Share dialog's Attach control) |
| **Team switch** (only with more than one team) and **brief line** ("Use the brief of <team>" tick with its length; a team with no brief says so and, to an owner or admin, links "Write one") | same | `SelectField`, the Share dialog's tick row |
| marked **place for the meeting picker** when the recipe's scope is `many` (`mk-slot`, dashed box labelled Meetings; text "This recipe is made from several meetings. Right now it uses this one." until slice S5 passes `renderMeetingPicker`) | same | one new class |
| **Recipes page** (`/recipes`, `?team=`): page head with New recipe; brief card; the list beside the editor | `web/app/recipes/page.tsx`, `recipes-view.tsx` | the Templates screen's split layout (`split`, `split-list`, `pick`) |
| **Recipe editor** (name, instruction, output, made from, use the brief, choices editor, who can use it, archive with confirmation, "Make a copy to change" for a starter or a member's view of a team recipe) | `recipe-editor.tsx` | `Field`, `TextareaField`, `SelectField`, the Templates editor's foot and inline confirm |
| **Brief editor** (counter against the cap, Save brief, Import from a file with a review panel: Replace the brief, Add to the end, Discard; read-only for a member) | `brief-editor.tsx` | `Card`, `TextareaField`, `Alert`, `Button` |
| **Recipes** sidebar link, in Configure after Templates, team-aware (carries `?team=`) | `web/components/app/shell.tsx` | `NavLink` / `TeamNavLink` |

### How, and what was decided

| Decision | Choice | Why |
|---|---|---|
| Where the team comes from | `X-Team-Id` rides on `/api/recipes*`, `/api/briefs*` and `POST /api/documents` (`isTeamScoped`); the dialog's switch passes `team` per call; the page follows `?team=` | The recipes, the brief and a draft's brief are the active team's (spec 5.4 item 6) |
| One meeting or several | A recipe of scope `one` always sends the page's own meeting id; for scope `many` the dialog sends `meetingIds`, which starts as this meeting and is what `renderMeetingPicker` (a prop; slice S5) changes. Switching recipe resets it | The lead's request: extensible, never a single-meeting assumption for a cross-meeting recipe |
| Starters vs rows | A starter is addressed by `recipe_key`, a row by `recipe_id` (`recipeRef`), never both | `POST /api/documents` rejects both (`recipe_invalid`) |
| Copying a starter | The editor opens prefilled ("... (copy)", the starter's instruction and choices) and `POST /api/recipes` carries `key`; nothing is saved until Save | A copy that saved at once would litter the person's list |
| Choices editor | A row is a name, a kind (Text or Word choice) and, for words, comma-separated words; at most six; a renamed existing choice keeps its key; a new one gets a key made from its label (`slug`) | The API's shape (`options`) is keys with labels; a person should only see labels |
| Messages | The server's sentence is shown for refusals written for the person (`PERSON_CODES`: import failures, `brief_too_long`, `too_many_recipes`, `options_invalid`) and for the daily limit and plan refusals; `errorMessage` still hides other 422s | The FE guide: "show the server's message" |
| Two Save buttons on one page | The brief's is "Save brief" | Two buttons named Save are ambiguous to a screen reader |
| Import | The file input is hidden behind an "Import from a file" button; the same file can be chosen again; nothing is saved until the person puts the text in the brief and presses Save brief | Review before save, as the API's contract says |

### Files

`web/lib/api/client.ts` (`api.recipes`, `api.brief`, `api.documents.make`, multipart `form` calls, `isTeamScoped`), `web/lib/api/types.ts`, `web/lib/api/schema.d.ts` (regenerated with `npm run gen:api`), `web/lib/recipes.ts`, `web/lib/routes.ts` and `web/proxy.ts` (`/recipes`), `web/components/app/{make-dialog,recipes-view,recipe-editor,brief-editor,meeting-view,shell}.tsx`, `web/app/recipes/page.tsx`, `web/styles/pages.css`, tests `web/tests/{recipes-api,make-dialog,meeting-make,recipes-view,brief-editor,recipes-nav}` and the capture helpers in `web/tests/helpers.ts`. `docs/openapi.json` was regenerated after merging `feature/recipes`.

### Verification

- Vitest, only the files above plus the neighbours that share the client and the shell (16 files, 350 tests), with the real captured bodies as fixtures (`docs/fe-captures/recipes-library/`, and `docs/fe-captures/recipes/07-create.json` and `39-create-over-the-cap.json` for the draft request). `npm run lint` (0 errors; 3 warnings in an old script) and `npm run typecheck`.
- **In a real browser** (the built-in browser against the Next dev server in front of the real API on a seeded scratch database), at 1280 and 390 px: the Make button beside Download and Share notes; the dialog (recipes, choices, brief tick); Make draft sent `POST /api/documents` through Next and got 202 and the page navigated to `/documents/1` (404 until slice S2); the Recipes page; a 2.4 MB text file handed to the import input went through Next to the API (200, truncated at 12,000 characters, review panel shown, nothing saved); no horizontal scroll at 390.
- Found by looking: the long recipe list pushed the choices and the buttons off the screen (now a bounded scroll area and a sticky foot); the editor sat out of view under the list on a phone (now scrolled to on choosing).
- **Not run**: the dialog and page as a member or admin in the browser (covered in vitest only); the team switch with two real teams (vitest only; the seeded database has one team); a Word or PDF file through Next (only text); nginx and the production Next build; the plan-inactive sentence and a full recipe shelf over HTTP (vitest, from the guide's text).
