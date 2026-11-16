# SOP: rebuild a finished meeting from the Skribby segments we kept (one-off, server-run script)

**What shipped.** `scripts/rebuild_meeting_from_evidence.py`: given a meeting id it reads the Skribby
segments saved in `skribby_segment_evidence` (kept 7 days), builds the transcript and the speaker-labelled
transcript through the **same functions the live `TRANSCRIPT_SOURCE=skribby` path uses** (so the
`sole_candidate` naming of #208 applies), writes the notes again from the new text, and queues the lists
(tasks, decisions, open questions, review items) to be read again **quietly**. **Dry run by default**;
`--apply` makes the change in one transaction after saving the old text to a private file; `--restore FILE
--apply` puts it back. Branch `feature/rebuild-meeting-from-evidence`; the PR number is on the checklist.
**No migration, no endpoint, no `.env` change, no screen.** Nothing runs by itself: merging changes nothing
on the server until Adebayo runs the steps below.

## Why

Meeting 37 (today, 1 hour, three speakers) was made by our own transcription with Skribby's tags laid on
top (`TRANSCRIPT_SOURCE=own`). One tag stayed "Unknown speaker" and some lines are credited to the wrong
person. Since 2026-11-16 12:20 WAT new meetings take Skribby's own text and the naming fix (#208) names the
tag. Meeting 37 itself was made before that. Its Skribby segments are still saved, so the same build can be
run on them. Meetings 34 and 35 are in the same position while their evidence lasts.

## Deadlines (the evidence is deleted 7 days after it was saved)

| Meeting | Evidence kept until (about) | Order |
|---|---|---|
| 34 | 2026-11-20 | first, if wanted |
| 35 | 2026-11-21 | if wanted |
| 37 | 2026-11-23 | then 37 |

Step 0 prints the exact `kept until` of every meeting that still has evidence. After that moment the script
refuses ("expired" or "no saved evidence"): there is nothing to rebuild from and nothing else we keep.

## How, and what was decided

| Piece | What it does |
|---|---|
| Reading | `SkribbySegmentEvidence.items` (tag, `speaker_name`, the candidate names, start, end, text) is turned back into the shape of Skribby's answer and read by `skribby._segments` (names the tags, with the **current** `SKRIBBY_NAME_BY_ELIMINATION` setting and the meeting's own `bot_name`) and `transcript_source.candidate` (the `[Name]: text` turns). Nothing is duplicated: a change to the live naming changes the rebuild too. The tags saved at the time are **not** reused (meeting 37's say `unresolved`); they are read again |
| Refusals | Stops with a plain sentence, changing nothing, when: the meeting does not exist; is not `ready` (being recorded, processing, failed); its lists are being read right now; was not run by `skribby`; has no saved evidence; the evidence is older than 7 days; the evidence is unreadable; or the text is too short for the recording (`transcript_source.fallback_reason`, the same four checks as the live path: words a minute, a loop, no times, stops before 70% of the recording; the recording length is the meeting's `recorded_seconds`) |
| Vocabulary and notes | `pipeline._apply_vocabulary` (the owner's terms, only for a model Skribby cannot give the vocabulary to) then `notes.generate_notes` with the meeting's own template (`meeting.template_id`, not changed). One notes call, written to `ai_runs` as an ordinary `notes` call with `+rebuild` after the prompt version (so the cost report still counts it and a second rebuild does not mistake it for the owner's own rewrite). The `vocabulary` call, when there is one, is the live path's own |
| What is written | `transcript`, `speaker_transcript` (both the same text, as the live path writes them), `speakers_incomplete`, `transcript_source` = **`skribby-rebuilt`**, `notes`, and the quiet re-read (`extraction_status=pending`, `extraction_quiet=true`). Nothing else on the meeting |
| What is never touched | any other meeting; `recorded_seconds` and the hours ledger (`usage_entries`); sharing, team, series; `status`/`step`; `template_id`. No "notes ready" mail (that is queued only by the pipeline's first notes), no share mail, no Slack or Linear post, no call to Skribby, nothing deleted at Skribby, no shadow comparison |
| One transaction | The meeting row is locked first (a few seconds' wait at most, else a plain "try again"), the backup is saved, the model is asked (vocabulary, notes), the touched-items check runs, and only then are the rows written and committed once. A refusal or a failed notes call rolls back and leaves the meeting exactly as it was (the cost of the call that was made is not in the ledger then) |
| Idempotent | A meeting already `skribby-rebuilt` says "already rebuilt" and does nothing (no model call, no second backup). A meeting already on the `skribby` path whose speaker labels equal what the evidence gives today says "already Skribby's own text with the same speakers" and does nothing. A `skribby` meeting made before the naming fix (an unnamed tag) is rebuilt like an `own` one. To redo a rebuilt meeting: restore it first |

**Decisions.** (1) `transcript_source` gets a new value, `skribby-rebuilt`, instead of `skribby`: it says what
the text is made from, is the marker that makes a second run a no-op, and nothing in the app branches on the value
(only `scripts/transcript_shadow_report.py` groups by it and will list it as its own row). (2) The script runs in
the **worker** container, not `web`: the worker mounts the persistent audio volume (`audiodata`), `web` mounts
nothing, so a backup written from `web` would vanish at the next recreate. (3) The backup is a JSON file, not a
table: no migration, and the private text sits in one place that the person deletes. (4) Rejected: keeping the old text in
a new column (a migration for a one-off); printing the old text to stdout (it would land in terminal history and
chat); re-running extraction inline (the worker already does it, with its claim and retry rules).

## What the existing "rewrite the notes" does with tasks and decisions (read from the code, then designed around)

`POST /meetings/{id}/regenerate` (`services.regenerate_notes`) writes new notes and calls
`workspace.mark_pending(meeting)` (**not quiet**). The worker then runs `workspace.run_extraction`, which reads
the lists out of the new notes **by the wording of each bullet** (a fingerprint of the text):

| List | Same wording | Wording gone | New wording |
|---|---|---|---|
| Tasks (`tasks.reconcile`) | kept (same row); a task a person touched (edited, moved, assigned, ticked) or deleted is left exactly as it is | removed if untouched; a touched one or a deleted one **stays** | created, **"New"**, owner matched, and **the assignee is mailed** and Slack/Linear posted (unless the run is quiet) |
| Decisions, open questions, review items (`workspace._reconcile`) | kept (a hidden decision stays hidden, a checked review item stays checked) | **removed even if a person hid, resolved or checked it** | created fresh |

Two things follow for a model rewrite, where almost every bullet is reworded: (a) a touched decision, resolved
question or checked review item whose bullet is reworded is **lost**; (b) a touched or deleted task whose bullet is
reworded **stays and a second task for the same action item is made beside it** (a duplicate), and a task a person
deleted can come back under its new wording. Ordinary rewrites by the owner have always done this.

**What this script does instead:**

1. The re-read is **quiet** (`mark_pending(meeting, quiet=True)`, the same switch the backfill uses): new tasks are not
   "New", no assignee mail, no Slack or Linear post. (The mail outbox also has unique rows per meeting for "notes
   ready" and per assignee for "tasks assigned", so a repeat could not mail twice anyway.)
2. **Before it writes anything** it asks what a person has touched, from the rows: a decision hidden, edited
   (`edited_at`) or with a status a person set; an open question resolved or taken off the next agenda; a review item
   checked; an AI task edited, moved, ticked (`tasks.touched`) or deleted. It then reads the **new** notes with the
   same parser the worker uses and counts the touched items whose wording is not in them. If there are any, it
   **stops, changes nothing and says how many of each kind**, unless `--force`. With `--force` they are given up
   exactly as a normal rewrite gives them up (the old notes are in the backup, but the rows are not: a restore
   re-reads the lists from the old notes and cannot bring back who hid or ticked what).
3. The dry run prints the number of touched items per kind (before any model call), so the person knows whether the
   stop is likely.

**Hand-edited notes.** The app has no way to edit notes by hand: the only writers of `meeting.notes` are the
pipeline's first writing (`pipeline._step_notes`) and the owner's rewrite with a template
(`services.regenerate_notes`; both leave a `notes` row in `ai_runs`). So the signal for "the person has changed the
notes" is a **second successful `notes` row** that this script did not write. If there is one, `--apply` stops and
says the owner rewrote the notes, unless `--force` (the old notes are saved in the backup). Rewrites before
`ai_runs` existed would not show, which no 2026-11 meeting is.

## The backup

* **Where.** `/srv/data/audio/rebuild-backups/meeting-<id>-<UTC time>.json` inside the `worker` container, that is
  the persistent `audiodata` volume (`--backup-dir DIR` changes it). The folder is made `0700` and the file `0600`,
  written, flushed and read back **before** anything changes; if it cannot be saved the script stops with nothing
  changed.
* **What is in it.** The old `transcript`, `speaker_transcript`, `speakers_incomplete`, `transcript_source`, `notes`
  and `template_id`, the meeting id and the time. **It holds what people said.** It lives only on the server, must
  **not be posted or pasted anywhere public**, and is **not removed when the meeting or the account is deleted**
  (the audio retention job only removes the per-meeting audio folders), so Step 5 deletes it once the result is checked.
* **Putting it back.** `--restore FILE --apply` writes those five fields back and queues the quiet re-read of the lists
  from the old notes. It refuses a meeting that has changed since the rebuild (its `transcript_source` is not
  `skribby-rebuilt` any more) and one where the old notes would lose items a person touched since, unless `--force`;
  without `--apply` it only says what it would do. The same thing by hand, if the script were ever unavailable:
  `update meetings set transcript = ..., speaker_transcript = ..., speakers_incomplete = ..., transcript_source = ...,
  notes = ..., extraction_status = 'pending', extraction_quiet = true where id = N;` with the values from the file's
  keys of the same names (`extraction_attempts = 0` too, as `workspace.mark_pending` does).

## What's involved

| File | Change |
|---|---|
| `scripts/rebuild_meeting_from_evidence.py` | new: `load`, `describe`, `touched`, `would_lose`, `save_backup`, `rebuild`, `restore`, `run` |
| `tests/test_rebuild_meeting_from_evidence.py` | 46 tests on made-up fixtures (invented names and words) |
| `pyproject.toml` | `F811` ignore for the new test file (it imports fixtures, like the other evidence tests) |
| `docs/sop/2026-11-16-rebuild-meeting-from-evidence.md`, `docs/checklist/master-checklist.md` | this SOP, one checklist line |

Reused, unchanged: `app/skribby.py::_segments/_resolve_tags/_last_segment_end`, `app/transcript_source.py`,
`app/pipeline.py::_apply_vocabulary/_terms/MIN_WORDS/EVIDENCE_RETENTION_DAYS`, `app/notes.py::generate_notes`,
`app/workspace.py::mark_pending`, `app/extraction.py::parse_notes/fingerprint`, `app/tasks.py::touched`.

## Verification

* **Tests, bounded** (`tests/test_rebuild_meeting_from_evidence.py` 46 and `tests/test_dockerfile_scripts.py`, on a
  scratch Postgres 17 that was stopped afterwards; CI runs everything else): refusals (unknown, not ready, extraction
  running, other provider, no evidence, expired, too short, stops early, unreadable) each leave the row unchanged and
  ask the model nothing; the dry run writes nothing, asks the model nothing, prints the counts and **none of the
  words or names**; apply gives exactly the text `skribby._segments` + `transcript_source.candidate` give (the
  meeting-37 shape: two named tags and one with one candidate on every segment becomes three named speakers, no
  "Unknown speaker"); notes rewritten once, ledger row marked `+rebuild`; the owner's vocabulary pass is asked;
  hours, `recorded_seconds`, sharing, status and every other column unchanged; no mail row; the evidence row is not
  changed; another meeting is not touched; a failed notes call rolls everything back; the backup is on disk before
  the model is asked, `0600`/`0700`, holds the old text and is never printed; an unwritable backup folder stops it;
  restore (put back, dry run, refuses a changed meeting, forced, only that meeting, refuses a foreign file, stops on
  touched items); run twice (second is a no-op: no model call, no ledger row, no second backup); an already-skribby
  meeting with the same speakers is left alone and one with an unnamed tag is rebuilt; touched decision, review
  item, open question, task and a deleted task each stop it, wording that survives does not, `--force` goes on; two
  notes rows stop it, a failed one does not count; the worker's re-read after the rebuild makes the new task not
  "New", no mail and **no second task** beside the replaced one.
* **Mutation check (run by hand, restored after):** quiet turned off, the touched-items stop removed, the
  rewrite stop removed, the "extraction running" refusal removed and the "ready" refusal removed each made tests
  fail (4, 3, 1, 1 and 3 failed), so the guards are tested, not decorative.
* `ruff check .` and `ruff format --check .` with the pinned `ruff==0.17.3`, as the CI lint job runs them.
* The runbook blocks pass `bash -n`; the SQL of steps 0, 2 and 3 was run on a scratch database built from the models
  (columns and joins exist).
* **Not verified.** Not run on the real server, so not proven: the real evidence rows of 34, 35 and 37 (a real
  Skribby answer was never fed to it, only the meeting-37 shape rebuilt with made-up names); the real model's notes
  (a fake model was used) and whether the real notes keep any touched wording (so how often the stop fires); the
  worker container's permission to create `/srv/data/audio/rebuild-backups` (assumed from the Dockerfile's `chown -R
  app:app /srv/data` and the worker already writing audio there); the blocks against the real server (`ssh`,
  `docker compose exec`, GNU tools); that the worker's re-read really runs quietly on the live database (tested on
  the scratch one); the vocabulary pass on a real model.

## Server steps (after this PR is merged and deployed)

**What this is for, in plain words.** The text and notes of meeting 37 (and 34, 35) were made from our own
transcription with Skribby's speaker tags laid over it, so one speaker is "Unknown speaker" and some lines are under the
wrong person. This rebuilds them from **Skribby's own text, with the speaker names the new rule gives**, using the
copy of Skribby's answer we saved. It changes only that meeting's transcript and notes, saves the old ones first in a
private file, and can put them back. It sends no mail, does not change anyone's hours or sharing, and does not talk to
Skribby. The tasks, decisions and questions are read again from the new notes by the worker a minute later, quietly. If
someone ticked, hid or resolved something whose wording the new notes lose, the script **stops and tells you**; it never
quietly drops it.

**Order.** Steps 0 and 1 change nothing: do them first, for the oldest deadline (34, if you want it, then 35, then
37). Run step 2 for one meeting at a time. It takes a minute or two (one model call) and holds that meeting's row
meanwhile, so do not press "rewrite notes" on it then. Every block is one command to paste; it runs on the server
with `ssh`, and every `docker` call has `</dev/null` so it cannot swallow the rest of the script.

### Step 0. Look first (changes nothing)

Says whether the script is deployed, which meetings still have evidence and **until when**, and whether anything is
being recorded. **A good result:** line 1 says `OK: the script is deployed`; the table lists the meetings you expect
(34, 35, 37) with a `kept until` in the future; the services are `running`/`Up`. If line 1 says `NOT DEPLOYED YET`, the
merge has not reached the server: stop. If a meeting you want is missing from the table, its evidence has already been
deleted and it cannot be rebuilt.

```bash
ssh haven-respite 'bash -s' <<'EOF'
cd /opt/beyric-note-taker
echo "--- 1. is the script in the running worker? (must say OK):"
docker compose exec -T worker sh -c 'test -f scripts/rebuild_meeting_from_evidence.py && echo "OK: the script is deployed" || echo "NOT DEPLOYED YET: stop here"' </dev/null
echo "--- 2. the services (web, worker, scheduler must each say running, or Up):"
docker compose ps web worker scheduler </dev/null
echo "--- 3. meetings that still have saved evidence (id, status, provider, transcript from, segments, kept until UTC):"
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' <<'SQL'
select e.meeting_id, m.status, m.bot_provider, m.transcript_source,
       jsonb_array_length(e.items) as segments,
       to_char(e.created_at + interval '7 days', 'YYYY-MM-DD HH24:MI') as kept_until
from skribby_segment_evidence e
join meetings m on m.id = e.meeting_id
order by e.meeting_id desc
limit 15;
SQL
EOF
```

### Step 1. Dry run for one meeting (changes nothing, prints no names and no words)

Put the meeting number in `ID`. **A good result:** no `REFUSED`; the tag lines show each tag `named [given]` or
`named [sole_candidate]` (for 37 the tag that was `unknown` in "saved with the evidence at the time" now says
`sole_candidate`); `speakers named: before 2, after 3` (the numbers for your meeting); `lines credited to no one`
goes down; `words` is about what you expect (Skribby's text and ours can differ by some percent); `touched by a person`
says how many items someone has touched. The last line says `DRY RUN: nothing was changed`. A `REFUSED` line says why in
plain words (expired, not finished, text too short): do not go on for that meeting.

```bash
ssh haven-respite 'bash -s' <<'EOF'
cd /opt/beyric-note-taker
ID=37   # the meeting number
case "$ID" in ''|*[!0-9]*) echo "STOPPED: ID must be a number."; exit 1;; esac
docker compose exec -T worker python scripts/rebuild_meeting_from_evidence.py "$ID" </dev/null
EOF
```

### Step 2. Apply to that meeting

Stops by itself, changing nothing, if the dry run would have been refused, if the new notes lose something a person
touched (it says how many of each kind; tell the lead, **do not add `--force` without asking**), or if the owner
rewrote the notes before. Otherwise: saves the backup, rebuilds, and prints the path of the backup. **A good result:**
line `APPLIED to meeting N`; a backup path under `/srv/data/audio/rebuild-backups/`; the second table says
`skribby-rebuilt`, `speakers_incomplete` `f` (false) when every speaker has a name, `extraction_status` `pending`
(then `done` after a minute or two, Step 3), `extraction_quiet` `t`; the **mail rows before and after are identical**
(no new mail). It takes a minute or two. Keep the backup path: it is the way back.

```bash
ssh haven-respite 'bash -s' <<'EOF'
cd /opt/beyric-note-taker
ID=37   # the meeting number, the same one you looked at in step 1
case "$ID" in ''|*[!0-9]*) echo "STOPPED: ID must be a number."; exit 1;; esac
psql_run() { docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1'; }
echo "--- mail rows about this meeting BEFORE (template, status, count):"
psql_run <<SQL
select template, status, count(*) from mail_outbox where payload->>'meeting_id' = '$ID' group by 1, 2 order by 1, 2;
SQL
echo "--- the rebuild:"
docker compose exec -T worker python scripts/rebuild_meeting_from_evidence.py "$ID" --apply </dev/null
rc=$?
echo "--- exit code: $rc (0 is good; anything else means it refused or stopped, and said why above)"
echo "--- the meeting now (no text printed):"
psql_run <<SQL
select id, status, transcript_source, speakers_incomplete, extraction_status, extraction_quiet,
       length(transcript) as transcript_chars, length(notes) as notes_chars
from meetings where id = $ID;
SQL
echo "--- mail rows about this meeting AFTER (must equal BEFORE):"
psql_run <<SQL
select template, status, count(*) from mail_outbox where payload->>'meeting_id' = '$ID' group by 1, 2 order by 1, 2;
SQL
echo "--- the backups on the server (private):"
docker compose exec -T worker ls -l /srv/data/audio/rebuild-backups </dev/null
EOF
```

### Step 3. A few minutes later (changes nothing)

Proves the worker read the lists again. **A good result:** `extraction_status` is `done` (if it still says `pending` after
ten minutes, or `failed`, tell the lead; the notes are fine, only the lists are late), and the counts are what you expect
from the new notes. Then open the meeting in the app and read the transcript and notes yourself: the speakers should be right.

```bash
ssh haven-respite 'bash -s' <<'EOF'
cd /opt/beyric-note-taker
ID=37   # the meeting number
case "$ID" in ''|*[!0-9]*) echo "STOPPED: ID must be a number."; exit 1;; esac
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' <<SQL
select m.id, m.extraction_status, m.extraction_quiet,
       (select count(*) from decisions where meeting_id = m.id) as decisions,
       (select count(*) from open_questions where meeting_id = m.id) as open_questions,
       (select count(*) from review_items where meeting_id = m.id) as review_items,
       (select count(*) from tasks where meeting_id = m.id and deleted_at is null) as tasks
from meetings m where m.id = $ID;
SQL
EOF
```

### Step 4 (only if needed). Roll back: put the old text and notes back

First list the backups and pick the file of that meeting (the name has the meeting number and the time). Then put
its name in `FILE` and run the second block. **A good result:** `RESTORED meeting N`. It stops by itself, changing
nothing, if the meeting changed since the rebuild or if putting the old notes back would remove something a person touched
since (it says how many; tell the lead). The lists are read again by the worker, quietly.

```bash
ssh haven-respite 'bash -s' <<'EOF'
cd /opt/beyric-note-taker
docker compose exec -T worker ls -l /srv/data/audio/rebuild-backups </dev/null
EOF
```

```bash
ssh haven-respite 'bash -s' <<'EOF'
cd /opt/beyric-note-taker
FILE=meeting-37-REPLACE_WITH_THE_TIME_FROM_THE_LIST.json   # the file name from the list above
case "$FILE" in meeting-[0-9]*.json) ;; *) echo "STOPPED: FILE must be a name like meeting-37-20261116T120000000000Z.json from the list."; exit 1;; esac
case "$FILE" in *REPLACE*|*/*) echo "STOPPED: put the real file name from the list in FILE."; exit 1;; esac
docker compose exec -T worker python scripts/rebuild_meeting_from_evidence.py --restore "/srv/data/audio/rebuild-backups/$FILE" --apply </dev/null
EOF
```

### Step 5. When you are happy with the result: delete the backup

It holds what people said and is not deleted with the meeting or the account. Run this only when the rebuilt meeting reads
right (and you do not want the way back any more). Same `FILE` as above.

```bash
ssh haven-respite 'bash -s' <<'EOF'
cd /opt/beyric-note-taker
FILE=meeting-37-REPLACE_WITH_THE_TIME_FROM_THE_LIST.json   # the file name from the list
case "$FILE" in *REPLACE*|*/*|'') echo "STOPPED: put the real file name in FILE."; exit 1;; esac
case "$FILE" in meeting-[0-9]*.json) ;; *) echo "STOPPED: not a backup file name."; exit 1;; esac
docker compose exec -T worker rm -v "/srv/data/audio/rebuild-backups/$FILE" </dev/null
docker compose exec -T worker ls -l /srv/data/audio/rebuild-backups </dev/null
EOF
```

## Follow-ups

* The touched-items stop is all or nothing; if it fires on a real meeting a smarter carry-over (keep a touched item by
  matching reworded bullets) would need design. Wait for evidence of how often the real model keeps the wording.
* A person's hide/resolve/tick is lost by `--force` and not by a restore: the rows are rebuilt from the notes' wording.
  Say so to the lead before using `--force`.
* `scripts/transcript_shadow_report.py` will list `skribby-rebuilt` as its own source row; harmless, nothing branches on it.
* After 2026-11-23 the evidence of 37 is gone for good; if the rebuild is wanted for later meetings, run it within 7 days
  or leave `TRANSCRIPT_SOURCE=skribby` on (then the live path already builds them this way).
