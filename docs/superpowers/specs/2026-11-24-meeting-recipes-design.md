# Meeting recipes: turn a meeting into the documents a team needs

**Written and revised 2026-11-24. Status: APPROVED by Adebayo 2026-11-24 ("Approve both": this design and the 2 to 3 week estimate). Nothing is built yet.** Project-size design. Next: the implementation plan (`superpowers:writing-plans`), then sessions. Still owed by him: who provides at least five real sales meetings and who rates the drafts (section 5.8).

**Direction from Adebayo (2026-11-24, his words):** "I do not want us to build placeholder as phase 1"; "lets just build the full functionality this once"; "I do not want half build … build all relevant phases that makes the app what it is." So this is **one complete release**. The build is sequenced inside (section 5.7) but **nothing reaches users until all of it works** (automated checks, plus a live run on Adebayo's own meetings). His later decision, 2026-11-24: **the release does not wait for sales users to rate the drafts**; he onboards those users after release and the recipes are tuned from their use.

**Inputs:** his words in chat, the gap analysis `docs/competitive/claude-meetings-gap-analysis-2026-11-24.md`, and the code (facts cite `file:line`). **My call** marks a choice he handed me ("you should know better") and has not seen; he can overrule it.

---

## 1. Intent and why

**What he wants.** Beyric is not "another AI note taker". It is a platform where *everything starts from a meeting*: one meeting becomes the follow-up email, the proposal, the requirements document, the marketing post, the status round-up, so a team does not leave Beyric to do the work that follows. "We are not competing with Claude … I want a platform … it all starts from a meeting."

**Who it is for.** Teams with different jobs: his own technical team (backlogs, next steps, requirements), sales (follow-ups, proposals), marketing (content). No single routine fits all, so the design is a general engine plus a library of recipes, not one department's workflow.

**His first two outputs:** **sales follow-up email** and **proposal**. They ship together with the rest.

**Why now.** Anthropic's Claude Meetings wins on live participation. As far as its pages show, it does not own what a team does after the meeting. Beyric already has the after-meeting base: structured notes, tasks with owners and quotes, decisions, open questions, recurring-meeting memory, team sharing, PDF and Word export. Generating the next document from that base, grounded in the team's own meeting, is the natural layer.

## 2. Goals, non-goals, anti-goals

**Goals (all in the one release)**
1. From one finished meeting, or from several, a good first draft of the document a team needs, from **ten starter recipes** (5.3), including the sales follow-up email and the proposal.
2. **Teams write their own recipes**, personal or shared with the team, so no department is forced into our shapes. The starters use the same mechanism.
3. **Grounded by default:** parts of a draft show the lines or meetings they came from; facts that neither the meeting nor the team's brief supports become visible blanks, never guesses; a code check flags figures that appear in neither (5.4).
4. **Real documents:** formatted text **and real tables**. Edit in place. Export: copy, Word, PDF, Markdown, CSV and Excel (tables), PNG and PDF (boards), and "Open in your mail app" for emails.
5. **Share with the team:** a draft is private to its creator until they share it read-only with their team; recipes are personal or team.
6. **Cross-meeting recipes:** a status report over a sprint, a client briefing before the next call, built from several meetings.
7. **A board:** a static, editable, non-live one-page picture of a meeting (decisions, actions, open questions grouped by theme), the after-the-meeting counterpart of Claude's whiteboard.
8. Cost is bounded and visible: one ledger row per attempt, a daily cap, the same feature gate as Ask.
9. **Import a file into the team's brief** (a past proposal, a price list, a style guide): upload a `.docx`, `.pdf`, `.txt` or `.md`, Beyric extracts the text into the brief (editable afterwards), and **the uploaded file itself is not stored**. Size and page caps; a scanned PDF with no text layer says so plainly instead of importing nothing. (My call on the mechanics: text extraction only, in the web process with a hard time and size limit, no storage of the original.)

**Non-goals**
- **Beyric never sends** an email or proposal on the user's behalf. "Open in your mail app" (a `mailto:` link with subject and body) and Copy cover the hand-off. A Gmail draft or send needs Google's sensitive Gmail scope and a verification round; it is not part of this release.
- No live co-editing or real-time whiteboard (his words: "we don't advise live whiteboard").
- (File import was a non-goal in the approved draft; Adebayo asked why, and it is now IN scope: goal 9. It was only a time-saving boundary of mine, not a necessity.)
- No CRM sync (the CRM question to pilot users is still parked).
- Ask Beyric's behaviour does not change.
- No new plan or price: generation is gated like Ask. Whether it is later metered or priced is a billing decision.

**Anti-goals (a failure even if it technically works)**
- A sales document with an **invented number, date, price, name or promise**.
- Leaking a meeting to someone who may not read it.
- A polished but generic document any chatbot would write.
- An empty box the first-time user stares at.
- **Anything half-finished that a user can see:** a control that does nothing, a recipe that fails on a table, a share button without permissions behind it.

## 3. How it works today (from the code), and what is missing

| Piece | Today | Consequence |
|---|---|---|
| Notes templates | `Template`: a user's name plus free-text `instructions` (`app/models.py:341`), personal | The shape of a recipe exists; recipes add a team scope, an output type and options |
| Notes generation | One model call, whole transcript fenced as data, strict truthfulness rules, Markdown out (`app/notes.py:61-90, 333-357`) | The prompting pattern to copy |
| Ask Beyric | 12 passages of 600 characters, at most 6 plain-text points, every point must cite a real passage, nothing stored, 30 a day (`app/ask.py:53-62, 654-696`) | Deliberately not a writer. It is the reference for citations and validation, not the place to add writing |
| Extracted data | Tasks with owners and verified quotes, decisions, open questions, milestones (`app/tasks.py`, `app/extraction.py`) | Cheap structured inputs; the only inputs cross-meeting recipes can afford |
| Long-running model work | Translation: a row with a status, a worker claims it, the page polls (`MeetingTranslation`, `app/translation.py`) | The pattern for generation that may take a minute or more |
| Export | `NotesDocument` is built from notes and rendered to PDF/Word in an isolated child process, one at a time per web process (`app/export.py`, `app/notes_document.py`); no tables | Needs table support in Word and PDF, and a measurement of the serial renderer's load |
| Permissions | `visible_meetings`, `can_read_clause` (`app/visibility.py`); team roles (`app/team_admin.py`); meeting sharing (`app/shares.py`) | A run is allowed exactly when the caller may read every meeting it uses |
| Cost and gates | `ai_runs` ledger with a `kind` CHECK constraint (adding `ask` needed a migration), `ai_versions.py`, `feature_gates` (`app/feature_gates.py`) | New kind `recipe` and gate `make` are small, known steps |
| Storage of a generated document, a place for a team's own facts, recipes with a team scope | **Nothing exists** | New tables |

## 4. Approaches considered

**A. A "Make" mode inside Ask Beyric (chat-first).** One entry point, flexible; but Ask's contract is no storage, plain text, short, cited. Writing a proposal needs long structured output and a saved, editable draft. Mixing them weakens what Ask promises, and a team has no repeatable library.

**B. A "Make" button on the meeting page, with recipes (chosen).** Documents belong to a meeting; repeatable, so a team standardises; a first-time user sees named choices, not a blank box; Ask stays pure. Needs storage, a library UI and a generation pipeline.

**C. A free-text prompt box on the meeting page, no recipes.** Smallest build, but every user rewrites the prompt, nothing is shared, and it is a general chatbot with the transcript pasted in.

**Chosen: B.** (My call.) A recipe whose instruction is typed once is C, so C is covered. A shortcut from an Ask answer to B's dialog ("turn this into…") is a small addition inside the build.

## 5. The design

### 5.1 Concepts

- **Recipe:** a name, an instruction, an output type (`document`, `email`, `table`, `board`), options (tone, audience, length), a scope (one meeting or several), and which brief it reads. Personal or team-owned. Ten starters ship; teams copy and edit them or write new ones.
- **Brief:** a team's own reference text the model may use besides the meeting (services, price list, terms, house style, a past proposal pasted in). One free-text brief per team; the personal team has its own.
- **Draft:** the generated, editable result, attached to a meeting (or, for cross-meeting recipes, to the set it was made from) and one creator. Many drafts per meeting.
- **Board:** a draft of output type `board` (5.3c).

### 5.2 The flow (what a person sees)

On a ready meeting page, next to the notes actions: **Make…**

```
Make from this meeting
  Recipes   [ Follow-up email ] [ Proposal ] [ Requirements doc ] [ Marketing post ]
            [ Decision memo ] [ SOP ] [ Action tracker ] [ Board ] [ My recipes ▾ ]
  Recipient or client name  [__________]       (optional)
  Tone  (o) Friendly  ( ) Formal
  [ Make draft ]                          uses your team's brief: "Acme services" ▾
```

For cross-meeting recipes (Status report, Client briefing) the dialog starts from the Make page with a meeting picker (a series, a project, a sprint, a date range or hand-picked; at most 12).

Pressing Make creates a draft in "Writing…" (the page polls like translation). When ready it opens as a document:

```
Follow-up email        [Copy] [Open in mail app] [Word] [PDF] [Markdown] [Share with team] [Discard]
Subject: Thanks for today, next steps on the onboarding
Hi Daniel,                                ⓘ from transcript lines 12 to 15
Thank you for your time today ...
Here is what we agreed: ...               ⓘ lines 40 to 52
Open: [need: start date]                  ⚠ 1 blank to fill, 1 figure to check
```

- **ⓘ** opens the exact lines (the `?tab=transcript&line=N` mechanism Ask uses); for cross-meeting drafts it opens the meeting, decision or task.
- **Blanks** like `[need: price]` are highlighted. Copy and exports warn "1 blank still to fill".
- **Figures** in the draft that appear in neither the transcript, the notes nor the brief are marked "check this" (5.4).
- The person edits in place (paragraphs, lists, tables), regenerates (a **new** draft), or discards.

### 5.3 The starter recipes (ten)

| Recipe | Scope | Output | What it does |
|---|---|---|---|
| **Follow-up email** | 1 meeting | email | Subject, greeting, thanks, short recap, what each side agreed (from the action items, with owners), next step and date only if the meeting set one. Never promises price, delivery or discounts that are not in the transcript or brief |
| **Proposal** | 1 meeting + brief | document with tables | Background; What we heard; Proposed solution; Scope and deliverables; Timeline (table); Investment (table); Terms and assumptions; Next steps. Prices and terms come from the brief; where neither has a fact, a blank |
| **Requirements document** | 1 meeting | document with tables | Goals, users, requirements (table with priority and owner), open questions, out of scope, risks, acceptance |
| **Marketing post** | 1 meeting | document | A LinkedIn post or blog draft from a product or customer meeting; options for audience and length; no claims not in the meeting or brief |
| **Decision memo** | 1 meeting | document | Context, options discussed, the decision, who decided, consequences, follow-ups; built from the decisions record |
| **SOP / how-to** | 1 meeting | document | Steps, owners, checks, from a walkthrough meeting |
| **Action tracker** | 1 meeting | table | Task, owner, due date, status, from the meeting's tasks; exports to CSV and Excel |
| **Board** | 1 meeting | board | See 5.3c |
| **Status report** | several | document with tables | Progress, decisions, blockers, next, over a sprint, project, series or date range |
| **Client briefing** | several | document | Before the next call with the same client: history, what was promised, open items and questions, from earlier meetings |

(My call on the list, from his examples: follow-up, proposal, requirements, marketing, round-up. The builder may drop or merge a starter that tests show is weak, and must tell Adebayo, but must not ship a weak one silently.)

### 5.3b Cross-meeting inputs

Cross-meeting recipes send **notes, decisions, tasks and open questions of each meeting, never several full transcripts** (cost and context). At most 12 meetings; the caller must be able to read **every** meeting chosen, or that meeting is left out and the draft says so. Sources link to the meeting, decision or task.

### 5.3c The board

A static, one-page picture of a meeting, editable by one person at a time, not live: columns by theme (the model groups decisions, action items, open questions and key points into themes), cards with owner and source, a title. The person can drag a card to another column, rename or add a column, add or edit a card, and recolour by type. Export PNG and PDF (and Markdown as a list). It reuses the meeting's extracted data. Not a drawing canvas: no free shapes, no arrows. (My call: this is what "something similar to the whiteboard, not live, that everybody will use" can honestly be with our resources.)

### 5.4 Grounding and safety

1. **Transcript as numbered lines.** The model receives the transcript with line numbers matching the displayed transcript (the numbering Ask uses, `web/lib/transcript.ts`), plus notes, tasks and decisions as context.
2. **Structured output, not free Markdown.** The model returns blocks (`heading`, `paragraph`, `bullets`, `table`, and for boards `column` and `card`), each with the line or meeting ids it used. Code validates the JSON, drops an id that was not sent, bounds sizes, makes text plain (no HTML, no links), as `check_reply` in `app/ask.py` does. Each renderer (screen, Word, PDF, Markdown, CSV, Excel, PNG) escapes for itself. A malformed reply fails the draft cleanly; it never half-shows. **Spreadsheet formula injection:** any exported cell starting with `=`, `+`, `-`, `@`, tab or CR is neutralised.
3. **Meeting text is data, not instructions.** Same fence and system-prompt rule as notes and Ask (`app/notes.py:_fence`). The same goes for the brief and for text in a shared recipe: a recipe's instruction is the author's, meeting text is not.
4. **Blanks over guesses.** The prompt forbids inventing names, numbers, dates, prices, commitments; missing facts become `[need: …]` tokens that code detects.
5. **Number audit (code, no model).** Code extracts every figure (digits, currency amounts, percentages, dates) from the draft, including table cells, and checks it appears in the transcript, the notes, the tasks, or the brief. A figure that does not is flagged. It re-runs on whatever text is there when the person presses Copy or Export, so figures a person typed are checked too. Flags are advisory (a warning with the list, never a block) and a person can mark one "checked". v1 over-flags rather than under-flags: digits, "50k", "fifty thousand", several date formats are normalised; line numbers and list ordinals are ignored; a figure that looks derived (a sum, "two weeks" from "14 days") is flagged "derived, check". Precision is tuned from the real-meeting rating set (5.8); the test oracle is that every seeded invented price or date is flagged.
6. **Permissions.** Running a recipe needs the caller to be able to read every meeting it uses (`visible_meeting_or_404` per meeting). A draft is **private to its creator** until they press "Share with team", which makes it **read-only** for the members of the active team. **Sharing a draft does not give anyone access to its meetings**: a reader who cannot read a source meeting sees the draft text but the source links read "not available" (the behaviour Ask already has). The share dialog says so plainly ("Teammates will read this draft even if they cannot open the meeting"), because the draft's text reveals meeting content. Unsharing removes access at once. Only the creator edits, regenerates or discards. **Team recipes are created and edited by team owners and admins only**; members use them and can read their instruction, and can still write personal recipes. That also settles the injection question: a team recipe's author is trusted staff, never an arbitrary member. **The team brief** is edited by owners and admins and is **readable (not editable) by members**, since its prices and terms appear in the drafts they make anyway. A person in several teams uses the **active team's** brief and team recipes (the switcher `MULTI_TEAM_ENABLED` already provides), shown and changeable in the Make dialog. (My call.)
7. **Privacy of generation.** Same vendors and the same statement as notes; the draft is stored in our database like notes. The privacy page and terms need a sentence about generated documents: a follow-up for Adebayo and his lawyer, **not blocking** (his 2026-11-18 rule).

### 5.4a Editing, source drift, cross-meeting drafts, locale (calls from the second builder check)

- **Editor:** a **structured block editor** (not a rich-text editor): each block's text is editable in place, blocks can be added, deleted and reordered, tables have row and column controls. Splitting or merging a block is not offered. Edited blocks **keep their sources**, worded "drafted from…", a new block has none. **Every export (Copy, Word, PDF, Markdown, CSV, Excel, mail app) uses the current edited blocks, never the original generation.**
- **Source drift:** each source stores the line number **and the first words of that line**, as `Task.quote` does (`app/ask.py:_quote_row`). When a draft is opened, a source whose words no longer match its line is looked for nearby; if it cannot be found it degrades to "open the meeting". The transcript editor keeps line count and order stable, and speaker correction changes labels, not lines; a rebuild that changes the transcript is what drift guards against. A stale draft (meeting text or brief changed) is labelled; regenerating always makes a new draft.
- **Cross-meeting drafts:** a draft has a set of meetings (a join table). A reader of a shared draft sees only the sources they may read; others read "not available". Deleting a meeting **deletes the single-meeting drafts made from it** (consistent with "delete means gone", audio included) but **keeps cross-meeting drafts**, whose link to that meeting reads "not available"; the SOP and the privacy sentence say so. Revoking a share or leaving a team ends access at once.
- **Audit tolerance and locale:** target at most about **three "check this" flags per draft on the rated real meetings, and most of them real**, tuned from the rating set; recall on seeded invented figures stays 100%. Currencies: naira (₦, "N", NGN, "naira"), dollars, euros, pounds, symbols and codes; "k", "m" and English number words; dates in ISO, US, UK and "15th Nov" forms. Number words and month names are English only in the audit; digits and currency symbols work in any language, and a draft in another language is still audited on its digits.
- **Board interactions:** edited by its creator only. Drag works with mouse and touch, and every card also has a "Move to column…" menu (keyboard and accessibility). **PNG is produced in the browser** (no new server load); PDF goes through the existing renderer as columns-as-sections, the builder deciding how it looks. Phones can view and use the menu; dragging on a phone is best effort.

### 5.5 Data (left to the builder, with my proposal)

- `documents`: id, owner user, team (nullable), meeting id or the set of meeting ids, recipe id and its version at generation, title, output type, status (`writing`, `ready`, `failed`), `blocks` (JSONB: the validated structure with sources), edited flag and time, source hash of the notes and transcript at generation (so a later transcript edit or notes rewrite shows "older than the meeting text"), model, prompt version, failure code, attempts and started-at (the worker claim), created and updated times.
- `document_shares`: document id, team id (read-only), who shared and when (modelled on `MeetingShare`).
- `recipes`: id, owner user, team (nullable), name, instruction, output type, options, scope, brief use, starter key (nullable for custom), archived. Starters are seeded rows or code constants; teams copy them.
- `team_briefs`: team id (or personal), text (bounded, e.g. 12,000 characters), updated by and when.
- `ai_runs`: new kind `recipe`; `ai_versions`: `RECIPE_PROMPT_VERSION`; the `kind` CHECK needs a migration.
- Queue: follow the translation pattern (request creates the row and answers 202, the worker claims and fills it, the page polls).

### 5.6 Cost and limits

- New settings: `make_model` (default the notes model), `make_daily_limit` (default **20** per person per rolling 24 hours, **across all recipes; every attempt that calls the model counts, failures and regenerations included**, as Ask does; one ledger row per attempt; a request refused before any model call does not count), a draft size cap, a brief size cap.
- New feature gate `make` beside `ask`, `translate`, `rewrite` (billing is ON in test mode, so a person without a live plan or trial gets 403 `plan_inactive`). Inside every plan (the pricing table says every feature is on every plan); to confirm in the billing go-live review. (My call.)
- Export load: the Word/PDF renderer is one-at-a-time per web process and has no tables. The builder measures real export times with tables and decides whether to raise concurrency or queue exports; the doc does not assume the serial renderer is enough for several teammates exporting at once.

### 5.7 Build order inside the one release

Nothing here reaches users before the whole list works. The slices are built on one integration branch (stacked or sequential PRs into it, CI green on each) and merged to `main` together at the end, so no half-finished control is ever deployed. The integration branch must take `main` regularly so it does not drift.

| Slice | Contents | Depends on |
|---|---|---|
| S1 Engine | Storage, queue and worker, gate and cap, structured generation with line sources, blanks, the number audit | none |
| S2 Documents | Document view and editor (paragraphs, lists, tables), regenerate, discard, all exports (Copy, mail app, Word, PDF, Markdown, CSV, Excel), table support in the renderers | S1 |
| S3 Recipes | The ten starters (one-meeting ones), the team brief and its editor, file import into the brief (goal 9), custom recipes, the recipe library and "My recipes" | S1 (parallel with S2) |
| S4 Sharing | Share a draft read-only with the team, recipe sharing and edit rights, permission tests | S2, S3 |
| S5 Cross-meeting | Meeting picker, Status report, Client briefing, cross-meeting sources | S1 (parallel with S4) |
| S6 Board | Board generation, drag-and-edit view, PNG and PDF export | S2 |
| S7 Integration | Privacy and terms sentence, FE guides from live captures, performance and export-load measurements, a live run of every recipe on his own team's meetings (5.8), an Opus review of the permission and export code, SOP and checklist | all |

**Estimate (honest range, not a promise), revised after Adebayo's instruction to build aggressively and complete it in days:** a cold review judged "about a week" optimistic and estimated 3 to 4 weeks if everything had to be tuned on real meetings first. Three decisions change that: (1) **the release no longer waits for the real-meeting rating** (the biggest schedule risk, which depended on other people); (2) **up to four sessions run at once** (he said work aggressively; the old 2-to-3 cap was a cost default, and rate limits will tell us if four is too many); (3) **tuning on real sales meetings moves after release**. With those, the target is **about one week of calendar time, honest range 5 to 10 working days**. The risks that could push it past that, in order: **S2** (the editor plus table renderers in Word, PDF and Excel), **S6** (the board is a small app of its own), and the number-audit rules. If a slice hits a wall I tell him which one and why the same day; the only candidate to ship a little after the rest is the board, and only with his agreement.

### 5.8 How we will know it works (automated checks and a live run before release; the sales rating after)

- **Real sales meetings:** at least five real meetings from Adebayo's pilot users (with their permission), run through the follow-up email and the proposal; a sales person rates each draft "I would send this after light edits" or not. Plus the other recipes run on real meetings of his own team (technical meetings give requirements, decision memo, SOP, action tracker, board; a sprint's meetings give the status report). **Adebayo's decision (2026-11-24): the release does NOT wait for this.** Before release every recipe is run on his own team's real meetings and the automated tests below pass; the sales rating happens after release with users he onboards, and is how the recipes are tuned. The targets below are the **post-release bar** (my call; he can change it): for each of the two sales recipes, at least **4 of 5** real meetings rated "send after light edits", and **no draft that carries an unflagged invented figure**; for every other recipe, at least **2 of 3** real meetings rated usable. A recipe that misses the bar is **fixed in the next tuning pass, or hidden until it is fixed**.
- **Number audit recall:** seeded drafts with an invented price and date must be flagged 100% of the time (an automated test).
- **Permission tests:** a person who cannot read a meeting cannot run a recipe on it or on a set containing it (that meeting is left out and the draft says so); a team member never sees another person's unshared draft; a shared draft never grants access to its meetings; unsharing takes effect at once; a member cannot edit a team recipe.
- **Injection tests:** a transcript line, a brief line, or a shared recipe's meeting text telling the model to ignore rules changes nothing.
- **Export tests:** Word, PDF, Markdown, CSV, Excel and PNG built from the same table-bearing draft agree; a cell starting with `=` is neutralised; a draft with blanks warns.
- Cost per draft per recipe measured from the ledger on real meetings and written into the SOP.

## 6. Left to the builder

Table and column names and sizes, the exact JSON schema and prompt wording per recipe, the editor components (a structured block editor with a table editor), the board's drag interaction, how the worker claims and retries, the renderers, the precise number-audit rules, the brief editor's screen, the migrations (several; numbers are assigned at merge time), and how the integration branch is kept current. Anything not listed is the builder's to decide within the goals and anti-goals, and every choice that changes what a user sees must be listed in the SOP as lead-designed (the designer's files draw none of this).

## 7. Decisions and what is still his

**His direction, recorded:** one complete release, no placeholder first phase; tables, team sharing, custom recipes, the other starters, cross-meeting recipes and the board are all in scope.

**My calls that stand (he can overrule):** approach B; structured blocks with sources; blanks over guesses; the number audit and its rules (5.4); drafts private until shared, read-only to the team, sharing never grants access to the meetings; team brief edited by owners and admins and chosen as the active team's; regenerate makes a new draft, discard deletes; the daily cap and what counts; the `make` gate inside every plan; the ten starters; the board as a non-live editable picture; "Open in your mail app" instead of a Gmail draft; the privacy sentence is a non-blocking follow-up.

**Still his (needed before the build can finish, not before it starts):**
Nothing. His decisions after approval, 2026-11-24: the release is **not** gated on the real-meeting rating (he onboards users after release); **file import is in** (goal 9); the build runs aggressively with up to four sessions. **One boundary remains by necessity:** Beyric never sends an email or creates a Gmail draft itself, because that needs Google's sensitive Gmail scope and a verification review (the calendar verification is still open); "Copy" and "Open in your mail app" cover the hand-off.
