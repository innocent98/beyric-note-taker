# Recurring meetings follow the meetings, PR 3 of 3: docs, screens and the switch-on

PR 3 of the three in section 10 of `docs/superpowers/specs/2026-11-14-series-follow-meetings.md`
(approved by Adebayo 2026-11-14). **PR 1** (`docs/sop/2026-11-14-series-follow-core.md`, #199, `dfbe741d`)
made the writers keep a row to one scope and wrote the backfill; **PR 2**
(`docs/sop/2026-11-14-series-follow-readers.md`, #202, `ca7be4ca`) scoped every reader and the mail. Branch
`feature/series-follow-docs-and-screens`; the PR number is on the checklist. **No schema migration.** The
flag `SERIES_FOLLOW_MEETINGS` is still **off**: merging this changes nothing on the server until Adebayo runs
the steps below. With the flag off the only differences are the additive `team.is_personal` and the team
label the frontend shows while multi-team is on.

## What shipped

1. The FE integration guide `docs/fe-integration-guide-series-teams.md`, every body copied from the real
   captures in `docs/fe-captures/series-follow/` (49 exchanges made by `scripts/capture_series_follow.py`
   against the running app on a scratch Postgres 17, including the real backfill script's output).
2. The small frontend changes the readers need: the Recurring cards and the Series page say which team (or
   "Personal") a row is; a personal row draws no "Next meeting" and no "Send agenda"; empty rows say so in
   plain words; the task drawer's people are asked for in the row's team. LEAD-DESIGNED (the designer drew no
   team on these screens), marked in the code.
3. Three one-line leftovers of the PR 2 Opus review (below), and `team.is_personal` on the series payload.
4. The exact server steps (below), with guards that stop the script instead of trusting the person.
5. Tidied SOPs and the checklist.

## Why

PR 1 and PR 2 are dark. Turning them on needs a frontend that tells the person why a recurring meeting shows
fewer meetings than before, a contract the frontend can build against that is true, and a runbook Adebayo can
run without guessing what a good result looks like.

## How, and what was decided

| Piece | Decision |
|---|---|
| Captures first | The guide is generated from the capture files, not written from the schema. The script tells the story in the order the server steps run: legacy rows with the flag off, the real backfill (dry run, apply, dry run), the flag on, every list and page for three people, the errors, a share and an unshare, and a "straggler" round (an out-of-scope meeting put back by hand: readers hide it, the flag-on backfill moves it, the next dry run says 0). The flag is flipped inside the running process with `get_settings.cache_clear()`; that is the one place it differs from the containers |
| `team.is_personal` | New `SeriesTeam(TeamRef)` on the series card and page only (NIT 6): a personal team is named "My team", so the frontend needs a flag to say "Personal", hide "Send agenda" and tell "no next date" from "personal row". The meeting object's `team` (`TeamRef`, no flag) is untouched |
| Where the team shows | Only while the session says `multi_team` is on (the page already knows it from the teams list). With it off every page is what it was: asserted in `web/tests/series-follow.test.tsx` |
| Personal row | No "Next meeting" box and no "Send agenda" button (it has no next date and nobody else on it: 409 `no_attendees`, captured). Not hidden when multi-team is off (a pilot user's solo team) |
| Task drawer team | `api.tasks.list({limit: 1}, row team)` while multi-team is on: the tab's team may differ from the row's (F1 of the PR 2 SOP) |
| Runbook shape | Six blocks, each one `ssh haven-respite 'bash -s' <<'EOF'`, `</dev/null` on every `docker` call except the psql check that has its own heredoc. Step 3 (and the roll back) **refuse to recreate while a meeting is being recorded or processed or a join is live or due within ten minutes**; step 4 **refuses to run the backfill unless all three services print `true`**. Rejected: one giant block (he could not tell which step failed); a pre-recreate backup of `.env` (a second copy of the secrets on disk; the roll back is flipping one line) |
| Empty card left as it is | The ghost empty card (a row that loses its last meeting) is **not** changed: it is still an open decision; the frontend renders it cleanly and the guide shows the real response |

### The 11 NITs of the PR 2 Opus review

| # | NIT | Disposition |
|---|---|---|
| 1 | cadence read joined meetings of the whole row | **Fixed**: `api_series._timing` uses `series.in_row`; test `test_the_cadence_reads_only_the_start_times_of_meetings_in_the_rows_scope` |
| 2 | `_seen_by` ignores `status == "ready"` | **Not changed, on purpose**: a card appears when a join is scheduled or the bot has joined (the frontend says so), before the notes are ready; requiring `ready` would hide a card for a meeting in progress. The spec's "ready" is looser than the code's reason |
| 3 | `_resolve` scopes the twins but not the source | **Fixed**: twins are looked for only when the question's own meeting is `in_row`; test `test_resolving_the_question_of_a_meeting_outside_the_rows_scope_closes_no_twin_in_scope` |
| 4 | hand-made twin also matched by the meeting's name | **Not changed**: a misgrouping, not a leak, and the rule was an answer of Adebayo's (question 6); stays listed |
| 5 | title fix cannot leak | nothing to do (the review checked it) |
| 6 | no `is_personal` on the series `team` | **Fixed** (above); test `test_the_team_on_the_payload_says_whether_it_is_personal`, openapi and `schema.d.ts` regenerated |
| 7 | personal rows still claimed by the topics job | **Not changed**: needs a join in the claim query, not a one-liner; harmless (cleared at once) |
| 8 | five tests with weak assertions | **Not changed**: rewriting tests is not a one-liner; the rules they name are covered elsewhere |
| 9 | SOP says 25 tests and "13 of its 24" | **Fixed in the PR 2 SOP** (28 tests now, with the three new ones) |
| 10 | `openapi.json` gained two unrelated `maxLength` lines | **Noted in the PR body**: correct, harmless, the file on main was stale |
| 11 | `send_agenda` on a personal row | **Done in the frontend**: the button is not drawn on a personal row (`team.is_personal`) |

## What's involved

| File | Change |
|---|---|
| `app/api_series.py`, `app/series_schemas.py` | `SeriesTeam` with `is_personal`; `_timing` scoped |
| `app/series.py` | `_resolve` skips the twins when the source is out of scope |
| `docs/openapi.json`, `web/lib/api/schema.d.ts` | regenerated (`SeriesTeam`) |
| `tests/test_series_readers.py` | three new tests, one expectation widened (now 28) |
| `scripts/capture_series_follow.py`, `docs/fe-captures/series-follow/` | the live capture and its 49 files |
| `docs/fe-integration-guide-series-teams.md` | the guide |
| `web/lib/series-team.ts`, `web/components/app/series-view.tsx`, `recurring-cards.tsx`, `web/app/page.tsx`, `web/app/series/[id]/page.tsx`, `web/styles/pages.css` | the team label, the personal row, the empty words |
| `web/tests/series-follow.test.tsx`, `web/scripts/mock-api.mjs` | 13 tests on the captures; the mock sends `team` |
| `docs/sop/2026-11-14-series-follow-core.md`, `...-readers.md`, `deploy/README.md`, `docs/checklist/master-checklist.md`, `docs/ui/parity-report.md` | pointers, counts, checklist, parity note |

## Verification

* **The captures** are real responses of the real app; section 9 of the guide marks each behaviour verified live
  or not.
* **Backend, bounded** (CI runs the rest): `ruff check`, `ruff format --check`, `tests/test_series_readers.py`
  (28), `tests/test_series_follow.py`, `tests/test_series.py` and `tests/test_api.py::test_the_committed_openapi_file_matches_the_routes`.
  The three new tests were run before the fixes and failed (3 failed), then passed with them.
* **Frontend, bounded:** `npm run typecheck`, `npm run lint`, `npx vitest run` of `series-follow`, `series-view`,
  `recurring-cards` and `projects-view`.
* **The runbook:** each block passes `bash -n`; the guard and `.env` logic was run against a stub `docker`
  (quiet, busy, flag missing, flag false, flag commented out, roll back); the SQL of the guard was run on the
  real schema (`0 0`, and `0 1` after one join was set live). **Not verified:** any block against the real
  server, `docker compose up --force-recreate` and `printenv` on the real containers, `sed -i` on the real
  `.env` (GNU sed is assumed; the stub run used macOS sed), and the real timing of the recreate.
* **Not verified:** the frontend in a browser or against a live server (tested against captures only; no
  screenshots), a real Google or Outlook calendar, the mail (not built in the capture; the scope and order are
  tests only), "Keeps coming up" (the model is not called; `topics` is `[]` in every capture).

## Server steps for Adebayo (run after this PR is merged and deployed)

**What this is for, in plain words.** Today a recurring meeting (say your Monday Standup) is one entry per
person that mixes the meetings you shared with your team and the ones you kept private. After this, each
team's Recurring page shows only the meetings shared with that team, and your Personal space shows only
your private ones. Two things have to happen on the server for that: a one-off tidy-up of the existing
entries (the **backfill**), and turning on the switch `SERIES_FOLLOW_MEETINGS`. The switch only takes effect
when the services are **recreated** (not `restart`: a restart does not re-read `.env`, so the file would say
true while the services still run with it off). Nothing here deletes a meeting, a task or a note; it only
moves a meeting to the right Standup entry.

**Order, and why.** Steps 2, 3 and 4 belong in **one sitting, a few minutes apart**. Between step 2 and the
recreate in step 3 the old code is still running and can mix an entry again (a share does it), which is why
step 4 runs the backfill a **second time** and expects nothing to move. **No meeting may be recording or
being processed during step 3**: the recreate restarts the worker and the scheduler. Step 3 checks that
itself and stops without changing anything if one is.

Every block is one command to paste into your terminal. It goes to the server with `ssh`, runs there, and
prints what it did; each `docker` call has `</dev/null` so it cannot swallow the rest of the script.

### Step 0. Look first (changes nothing)

Checks that the right code is running, the switch is not on yet, and that nothing is being recorded.
**A good result:** the commit is the merge of this PR or later; `SERIES_FOLLOW_MEETINGS` is missing or
`false` and `MULTI_TEAM_ENABLED=true`; web, worker and scheduler each say `running` (or `Up`); the last line
is two numbers, `0 0` (meetings being recorded or processed, then calendar joins live or due within ten
minutes). If the last line is not `0 0`, wait and run step 0 again.

```bash
ssh haven-respite 'bash -s' <<'EOF'
cd /opt/beyric-note-taker
echo "--- 1. the code that is running (the commit must be the merge of the PR that carries this runbook, or later):"
git log -1 --oneline
echo "--- 2. the two switches (SERIES_FOLLOW_MEETINGS must be missing or false; MULTI_TEAM_ENABLED must be true):"
grep -E '^(SERIES_FOLLOW_MEETINGS|MULTI_TEAM_ENABLED)=' .env || echo "(neither is set in .env)"
echo "--- 3. the services (web, worker, scheduler must each say running, or Up):"
docker compose ps web worker scheduler </dev/null
echo "--- 4. is anything being recorded or processed right now? (two numbers, both must be 0):"
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -F " " -v ON_ERROR_STOP=1' <<'SQL'
select
  (select count(*) from meetings where status in ('bot_sent', 'processing')) as meetings_in_progress,
  (select count(*) from scheduled_joins
     where state in ('dispatching', 'no_capacity', 'joined')
        or (state = 'scheduled' and run_at <= now() + interval '10 minutes' and deadline_at >= now())) as joins_live_or_due_within_10_min;
SQL
EOF
```

### Step 1. Dry run of the backfill (changes nothing)

Counts how many recurring entries mix teams and how many meetings would move. **A good result:** the first
line is `DRY RUN: nothing was changed. Run again with --apply to do it.`, then nine lines of numbers. Read
them like this:

| Line | What it means | Expect |
|---|---|---|
| Recurring-meeting rows looked at | all entries | any number |
| Rows that held a meeting of another team or of the personal space | entries that mix | any number: this is the work |
| Meetings moved to the row of their own team or personal space | meetings that will move | any number |
| New rows made for them | new entries (a team's or a person's personal one) | any number |
| Meetings taken out of their series (the team had no room) | meetings dropped from a series because the team is at its limit of recurring entries | **0**. If not 0, stop and tell the lead the number |
| Meetings left alone (their recorder's account is gone) | private meetings of a deleted account | any number; not a failure; it is the same on every run |
| Personal spaces made for people who had none | | usually 0 |
| Rows left with no meeting after the split | entries that will show as an empty card | any number: information for the "empty card" decision |
| ...of which still hold waiting agenda lines | | information only |

```bash
ssh haven-respite 'bash -s' <<'EOF'
cd /opt/beyric-note-taker
echo "--- dry run, changes nothing:"
docker compose exec -T web python scripts/backfill_series_follow.py </dev/null
EOF
```

### Step 2. The backfill (switch still off), then a check that it is finished

Saves the split, then runs the dry run again. **A good result:** the first report starts
`APPLIED: the database was changed.` with the same numbers as step 1; the second starts `DRY RUN` and says
**`Rows that held a meeting of another team or of the personal space: 0`** and
**`Meetings moved to the row of their own team or personal space: 0`**. (If the run stops with a
deadlock error, run the block again: it is one transaction, nothing was saved, and a second run is safe.)

```bash
ssh haven-respite 'bash -s' <<'EOF'
cd /opt/beyric-note-taker
echo "--- applying the split:"
docker compose exec -T web python scripts/backfill_series_follow.py --apply </dev/null
echo "--- checking: a second pass must find nothing left to move:"
docker compose exec -T web python scripts/backfill_series_follow.py </dev/null
EOF
```

### Step 3. Turn the switch on and recreate the services

Stops by itself, changing nothing, if a meeting is being recorded or processed (it prints the two numbers).
Otherwise it sets `SERIES_FOLLOW_MEETINGS=true` in `.env`, recreates `web`, `worker` and `scheduler` (the
site is unreachable for a few seconds), waits, and asks **each running service** what it sees.
**A good result:** the `.env` lines read `SERIES_FOLLOW_MEETINGS=true` and `MULTI_TEAM_ENABLED=true`; each of
web, worker and scheduler prints `true` and `true`; the services list shows all three running (web may say
`health: starting` for up to a minute: it must not say `unhealthy` or `restarting` after that). It may also
run the one-off `migrate` container again: with nothing to migrate it exits at once.

```bash
ssh haven-respite 'bash -s' <<'EOF'
cd /opt/beyric-note-taker
echo "--- is anything being recorded or processed right now? (must be 0 and 0, or this stops):"
counts=$(docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -F " " -v ON_ERROR_STOP=1' <<'SQL'
select
  (select count(*) from meetings where status in ('bot_sent', 'processing')),
  (select count(*) from scheduled_joins
     where state in ('dispatching', 'no_capacity', 'joined')
        or (state = 'scheduled' and run_at <= now() + interval '10 minutes' and deadline_at >= now()));
SQL
)
echo "meetings in progress, joins live or due within 10 minutes: $counts"
if [ "$counts" != "0 0" ]; then
  echo "STOPPED: a meeting is being recorded or processed, or one starts within 10 minutes. Nothing was changed. Run this again when the numbers are 0 0."
  exit 1
fi
echo "--- turning the switch on in .env:"
if grep -q '^SERIES_FOLLOW_MEETINGS=' .env; then
  sed -i 's/^SERIES_FOLLOW_MEETINGS=.*/SERIES_FOLLOW_MEETINGS=true/' .env
else
  printf '\nSERIES_FOLLOW_MEETINGS=true\n' >> .env
fi
grep -E '^(SERIES_FOLLOW_MEETINGS|MULTI_TEAM_ENABLED)=' .env
echo "--- recreating web, worker and scheduler (the site is unreachable for a few seconds):"
docker compose up -d --force-recreate web worker scheduler </dev/null
sleep 20
echo "--- the switch as each running service sees it (SERIES_FOLLOW_MEETINGS first, then MULTI_TEAM_ENABLED: true and true, three times):"
for s in web worker scheduler; do
  echo "$s:"
  docker compose exec -T $s printenv SERIES_FOLLOW_MEETINGS MULTI_TEAM_ENABLED </dev/null
done
echo "--- the services:"
docker compose ps web worker scheduler </dev/null
EOF
```

### Step 4. The backfill again, now that the switch is on

Proves the new code keeps entries pure: it must find nothing to move. The block first checks the switch
inside all three services and **stops by itself** if any says anything but `true` (a pass then would prove
nothing, because the old code would still be mixing entries); if so, run step 3 again. **A good result:** `true`
three times, then `APPLIED` with **0** in the "moved" and "rows that held a meeting" lines, then a `DRY RUN`
that also says 0. A number above 0 in the first report means a meeting slipped in between step 2 and the
recreate: the run has already fixed it, and the second report must say 0. Tell the lead the number. "Personal
spaces made" can be above 0 only if somebody signed up during the run. A deadlock error: run the block again.

```bash
ssh haven-respite 'bash -s' <<'EOF'
cd /opt/beyric-note-taker
echo "--- the switch as each running service sees it (must be true, three times, or this stops):"
for s in web worker scheduler; do
  value=$(docker compose exec -T $s printenv SERIES_FOLLOW_MEETINGS </dev/null)
  echo "$s: $value"
  if [ "$value" != "true" ]; then
    echo "STOPPED: $s does not have the switch on, so a pass now would prove nothing. Run step 3 again."
    exit 1
  fi
done
echo "--- applying, fixes any straggler (a deadlock error: just run this block again):"
docker compose exec -T web python scripts/backfill_series_follow.py --apply </dev/null
echo "--- checking: this must say 0 moved and 0 split:"
docker compose exec -T web python scripts/backfill_series_follow.py </dev/null
EOF
```

### Step 5. What to look at on the site

In a browser, signed in as yourself (use the team switcher at the top of the sidebar):

1. **Meetings page in your team**: the Recurring cards now say the team under the title:
   `Weekly on Mondays, 14:00 · <your team's name>`.
2. **Switch to Personal**: Recurring shows only recurring meetings you have **private** meetings of; the
   cards say `· Personal` and have **no "Next:" chip**.
3. **Open a team's recurring meeting**: under the title it says the team and, below it,
   `The meetings of this series that are shared with <team>.`; "Past meetings" lists only meetings shared with
   that team.
4. **Open the same meeting from Personal**: it says `Personal` and
   `Your private meetings of this series. The ones you share with a team are on that team's page.`; there is
   **no "Next meeting" box and no "Send agenda to attendees" button**.
5. **Expected, not a bug:** if you are on one team only, your **private** recurring meetings left the team's
   list and are under Personal now (that is the rule: a team's page shows what is shared with it).
6. A card or page that says `None of your private meetings are in this series right now.` is a recurring entry
   whose last private meeting you shared with a team. It stays as an empty card; whether it should disappear
   is an open decision, not a fault.
7. Optional: open a recurring meeting's page, share it to a team, and check it appears on the team's
   recurring page; unshare it, and check it is back under Personal.

### Roll back

Turn the switch off and recreate; it stops by itself if a meeting is being recorded or processed. **The split
entries stay split** (there is no merge-back script). With the switch off the old readers list by the person's
default team, so a one-team person's private recurring meetings, which now sit under their Personal entry,
can show on their team's page as an empty card (the real meetings are under Personal) until the switch is on
again; nothing is lost. **A good
result:** `false` three times.

```bash
ssh haven-respite 'bash -s' <<'EOF'
cd /opt/beyric-note-taker
echo "--- is anything being recorded or processed right now? (must be 0 and 0, or this stops):"
counts=$(docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -F " " -v ON_ERROR_STOP=1' <<'SQL'
select
  (select count(*) from meetings where status in ('bot_sent', 'processing')),
  (select count(*) from scheduled_joins
     where state in ('dispatching', 'no_capacity', 'joined')
        or (state = 'scheduled' and run_at <= now() + interval '10 minutes' and deadline_at >= now()));
SQL
)
echo "meetings in progress, joins live or due within 10 minutes: $counts"
if [ "$counts" != "0 0" ]; then
  echo "STOPPED: a meeting is being recorded or processed, or one starts within 10 minutes. Nothing was changed. Run this again when the numbers are 0 0."
  exit 1
fi
echo "--- turning the switch off in .env:"
sed -i 's/^SERIES_FOLLOW_MEETINGS=.*/SERIES_FOLLOW_MEETINGS=false/' .env
grep -E '^(SERIES_FOLLOW_MEETINGS|MULTI_TEAM_ENABLED)=' .env
echo "--- recreating web, worker and scheduler (the site is unreachable for a few seconds):"
docker compose up -d --force-recreate web worker scheduler </dev/null
sleep 20
echo "--- the switch as each running service sees it (false, three times):"
for s in web worker scheduler; do
  echo "$s:"
  docker compose exec -T $s printenv SERIES_FOLLOW_MEETINGS </dev/null
done
EOF
```


## Follow-ups

* **Decision (Adebayo or the lead): the ghost empty card.** A row that loses its last meeting stays as an empty
  card. Captured live (`40-cards-ada-personal-after-share.json`). Small to fix (re-key or delete an emptied row
  with no waiting lines and no project) if wanted.
* Not changed from the PR 2 review: NITs 2, 4, 7, 8 (dispositions above); a merge-back script for the split,
  only if wanted.
* A browser parity pass (screenshots at 1440, 768 and 390 px) of the Recurring cards and the Series page in
  both modes was not done here.
