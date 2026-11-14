# Recurring meetings follow the meetings, PR 1 of 3: core and backfill (dark)

PR 1 of the three in section 10 of `docs/superpowers/specs/2026-11-14-series-follow-meetings.md`
(approved by Adebayo 2026-11-14, answers to the eight questions: 1 yes, 2 yes, 3 yes, 4 no, 5 yes,
6 yes, 7 yes, 8 hide "next meeting" on personal rows). **PR 2** (readers and mail) and **PR 3**
(docs, FE guide, switch-on) come later. Branch `feature/series-follow-core`. PR link: see the
checklist (added when opened). **No schema migration.** Everything is dark: with
`SERIES_FOLLOW_MEETINGS` unset (off) nothing behaves differently from before.

## What shipped

A series row now belongs to a **team scope**, and a meeting **follows** its scope: the new flag
`SERIES_FOLLOW_MEETINGS`, the one scope rule in `app/visibility.py`, `series.follow_meeting` called
from every place a meeting changes team, the pipeline link and the sync made scope-aware, and an
idempotent backfill script (dry run first) that splits the rows that mix scopes today.

## Why

Adebayo's rule (2026-11-14): a series has no team of its own for display; a team's recurring page
lists a series if at least one of its meetings is in that team and shows only those meetings; the
personal space shows the series' private meetings. Today one row (the recorder's default team) holds
a person's shared, private and other-team meetings together. The readers (PR 2) cannot be switched
on until the rows are pure, so this PR makes the writers keep them pure and cleans the existing ones.

## How, and what was decided

| Piece | What it does | Decision / alternative |
|---|---|---|
| Flag `SERIES_FOLLOW_MEETINGS` | default `false`; `series.follow_enabled()` needs it **and** `MULTI_TEAM_ENABLED` | Riding on `MULTI_TEAM_ENABLED` alone was rejected: it is already on in production, so behaviour would flip before the backfill |
| The scope rule | `visibility.scope_team_of(meeting)` (a team id, or None = recorder's personal space) and `visibility.series_scope_clause(team)` (the same rule as a query): ordinary team = shared to or owned by it; personal team = its owner's meetings that no team has | Both in `visibility.py`, whose invariant (nothing else compares `shared_team_id` / `owner_team_id`) is kept. A test pins that the two agree on every state |
| `series.follow_meeting(session, meeting)` | flag on: moves the meeting to the row of its scope for the same recurring meeting (found, else made), marks **both** rows' topics stale. A meeting in no series is left alone. Off: does nothing | One row per recurring event per team (the note's decision 1). A new row copies title and platform, is made by the recorder, **never copies the project link or the agenda lines** |
| Hand-made rows (no recurring id) | follow a same-titled **hand-made** row in the new scope, else make one | Open question 6, yes. A calendar row is never a hand-made row's twin |
| A full team | if the scope's team already has `MAX_SERIES_PER_TEAM` rows, the meeting is **cut loose** from its series (never left in a row of another scope); the backfill counts these | A silent mix would be the leak; a missing series is only a gap |
| Hooks | `meeting_teams._touched` (share, unshare, move, move back); `series.forget_meetings_of` (a person leaves: their meetings now follow them to their personal row instead of losing their series); `deletion._give_back_meetings_of_doomed_teams` (a team is deleted: the meeting lands in its live recorder's personal row, not nulled by the cascade); the pipeline link `link_from_calendar` (scope team, not default team); `meeting_team_id` (hand attach, "Make this a recurring meeting?": a private meeting joins only a personal series) | A **moved** meeting never leaves its owning team's row (its scope is the owning team): leaving the team, or the recorder deleting their account, changes nothing |
| Empty pre-recording cards | the hourly sync does not remake a card for a person who already has a row of that event with meetings; the recorder's first meeting **re-keys** their own empty card into its scope, only if the card has no meeting, no waiting agenda line, no linked project, was made by the recorder, and the scope has no row yet; `_seen_by` shows a card to its creator or calendar owner only while it has no meetings | Re-keying a teammate's card, or one holding the team's lines or project, was rejected: those are the team's |
| Backfill | `app/series_backfill.py::split_mixed_series`, run by `scripts/backfill_series_follow.py` (default a DRY RUN; `--apply` saves). Uses the same `place_meeting` as the hooks, for every meeting that sits in a row of another scope. The dry run does the identical work inside the transaction and rolls it back, so its counts are exactly what `--apply` will do. Idempotent; counts only (no title, name or address); does not read the flag | The report states rows looked at, rows split, meetings moved, rows made, meetings cut loose, meetings left alone |

## What's involved

| File | Change |
|---|---|
| `app/config.py`, `.env.example`, `deploy/README.md` | the flag, default off |
| `app/visibility.py` | `scope_team_of`, `series_scope_clause` |
| `app/series.py` | `follow_enabled`, `scope_team_id`, `place_meeting`, `follow_meeting`, `_row_for_scope`, `_rekey_empty_card`, `_has_row_with_meetings`; `link_from_calendar`, `sync_from_joins`, `meeting_team_id`, `forget_meetings_of`, `_seen_by` branch on the flag |
| `app/meeting_teams.py` | `_touched` calls `follow_meeting` |
| `app/deletion.py` | `_give_back_meetings_of_doomed_teams` calls `follow_meeting` |
| `app/series_backfill.py`, `scripts/backfill_series_follow.py` | the backfill |
| `tests/test_series_follow.py` | 35 tests |

## Verification

The test file was written before the code (`tests/test_series_follow.py`, 35 tests on a scratch
Postgres 17); the bar for "do they bite" is the mutation check below, not a red run. Leak cases of
section 9 of the note that this PR touches: 1 (one row per scope, each page its own meeting), 10 (a
person who left X), 11 (a moved meeting whose recorder left), the deleted-account half of 12, 13 (a
team deleted, the meeting given back), 14 (same recurring id in two organisations), 15 (no project
or lines copied), 17 (backfill: dry run changes nothing, idempotent, never moves a moved meeting,
never touches lines), 18 (flag off: share, unshare, leave, link, sync and give-back behave exactly
as before), 19 (the empty-card rule). Cases 2 to 9, the readers half of 12, and 16 are PR 2.

**Mutation check** (17 mutations, the new rule put back or broken, one at a time, run against
`tests/test_series_follow.py`): 16 killed. The one survivor is an equivalent mutant: the backfill's
pre-filter of out-of-scope meetings is redundant with `place_meeting`'s own check, so removing it
changes nothing (the check that matters, `scope_team_of` ignoring a moved meeting's owner, is
killed). Killed: follow does nothing; private scope = default team; project link copied; leave
nulls instead of following; give-back does not follow; personal scope ignores shared; old
`_seen_by`; moved meeting scope ignores its team; card re-key ignores waiting lines; sync remakes
the card; only the new row marked stale; a full team leaves the meeting in the old row; hand-made
match ignores the recurring id; the link uses the default team; the attach rule uses the default
team; the dry run commits; the flag ignores multi-team.

Locally, bounded (CI runs the rest): `ruff check`, `ruff format --check`,
`tests/test_series_follow.py`, `tests/test_series.py`, `test_series_mail.py`,
`test_series_project_link.py`, `test_topics.py` and
`test_multi_team_{derived,leaks,moved,flag_off,review_fixes,rules}.py` (259 tests, green). The
script was also run once (dry run, then `--apply`) against an empty scratch database.
**Not verified:** against production data (the counts are unknown; the dry run on the server says),
and not in a browser or against a real calendar.

## Operate: the server command (Adebayo, after this PR is merged and deployed)

Do this **just before** the flag is switched on (PR 3), not days earlier; see "Flag off after the
backfill" below. The flag stays off through both steps.

**Step 1, the dry run.** It prints the deployed commit, the two switches, and what the backfill
would do. It changes nothing. A good result: the commit is the merge of this PR (or later),
`SERIES_FOLLOW_MEETINGS` is missing or `false`, `MULTI_TEAM_ENABLED=true`, and the counts start with
`DRY RUN: nothing was changed.` followed by six lines of numbers.

```bash
ssh haven-respite 'bash -s' <<'EOF'
cd /opt/beyric-note-taker
echo "--- deployed commit:"
git log -1 --oneline
echo "--- the two switches (SERIES_FOLLOW_MEETINGS should be missing or false, MULTI_TEAM_ENABLED true):"
grep -E '^(SERIES_FOLLOW_MEETINGS|MULTI_TEAM_ENABLED)=' .env || echo "(neither is set)"
echo "--- dry run, changes nothing:"
docker compose exec -T web python scripts/backfill_series_follow.py </dev/null
EOF
```

**Step 2, do it, then prove it is finished.** It saves the split, then runs the dry run again. A
good result: the first report starts `APPLIED: the database was changed.`; the second report starts
`DRY RUN` and says **"Rows that held a meeting of another team or of the personal space: 0"** and
"Meetings moved to the row of their own team or personal space: 0" (nothing left to move).
"Meetings taken out of their series (the team had no room)" should be 0; if it is not, tell the
lead the number.

```bash
ssh haven-respite 'bash -s' <<'EOF'
cd /opt/beyric-note-taker
echo "--- applying the split:"
docker compose exec -T web python scripts/backfill_series_follow.py --apply </dev/null
echo "--- checking: a second pass must find nothing left to move:"
docker compose exec -T web python scripts/backfill_series_follow.py </dev/null
EOF
```

**Roll back.** Nothing needs undoing while the flag is off. The split rows stay split; there is no
merge-back script (a follow-up only if wanted). Switching the flag off restores today's readers.

### Flag off after the backfill (read before running step 2 early)

Today's readers list a team's Recurring cards by the person's default team. After the backfill, a
one-team person's **private** recurring meetings sit under their personal row, so until the flag
(and PR 2's readers) are on, a recurring card whose meetings were all private shows **empty** on
their team's list while the real meetings are under the personal space. The data is right, the
display catches up at switch-on. That is why step 2 belongs right before the switch-on.

## Follow-ups

- **Ghost empty cards.** A row that loses its last meeting (the person unshares their only
  Standup, or the backfill moves every meeting of an all-private row) stays as an empty card and,
  by the note's rule, shows to its creator while it has no meetings. For a one-team person with an
  all-private recurring call that is a lasting empty card in the team list. Not built: the note
  says empty cards stay, and this case is not what that sentence was written for. The fix is small
  if wanted (re-key the emptied row into the new scope when it has no waiting lines and no project,
  else delete it). Decision for Adebayo or the lead before PR 3.
- PR 2: scoped readers, topic source, deterministic reminder order, `team` on the series payload,
  "next meeting" hidden on personal rows. PR 3: docs, FE guide with live captures, the switch-on.
- A merge-back script, only if the split has to be undone.
