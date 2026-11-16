# The simpler recurring model: calendar cards in Personal, team cards by owners and admins

**What shipped.** Branch `feature/series-personal-calendar-owner-admin-teams`, stacked on #205 (`feature/remove-recurring-meeting`); the PR number is on the checklist.
**No migration** (the table `series_ignored` is #205's 0048). Adebayo, 2026-11-15/16: "I just don't want us to complicate things" ... "Yes, go ahead." Applies while
`SERIES_FOLLOW_MEETINGS` is on (as in production); with it off every rule below is as before.

## Why

His live data: his three calendar-made cards (series 1, 2, 3) sat EMPTY in his ordinary team 1. The hourly sync made the placeholder in the person's DEFAULT team, which for a one-team person is the
ordinary team; only recorded meetings followed the Personal rule. And any member could make a team's card, so cards appeared in a team that nobody who runs the team had asked for.

## The model

| What | Rule |
|---|---|
| A recurring card from a person's **own calendar** | always in their **Personal space**, never in a team (`sync_from_joins` now uses the person's personal team when the flag is on). Nothing overrides it |
| A **team's** card | made only by an **owner or admin** of that team (`series.may_make_row`): when they share or move a meeting of a recurring calendar event to the team; by the automatic link when their recording is auto-shared; or by hand |
| A plain member **shares** a meeting | the share works as ever and makes **no card** (the meeting is left in no recurring meeting). If the team already has the card, the meeting **joins** it |
| By hand: create (`POST /api/series`), add a meeting (`PUT /api/meetings/{id}/series`), "Make this a recurring meeting?" (`POST /api/meetings/{id}/recurring`) | owner or admin for a team's card (**403 `team_forbidden`**, the project's real envelope); anyone for their own Personal card. Taking a meeting **out** stays its owner's |
| An owner or admin makes the team's card | the meetings of the **same recurring event** that the team already has (shared or moved to it) and that no card holds yet **join it** |
| An owner's or admin's **explicit** share or move (after a removal) | makes the card again and **clears the "removed" mark** for that scope. The **automatic** link after a recording still respects the mark |
| A plain creator removing an empty legacy row (#205's exception) | allowed, but writes **no** team-wide mark; an owner's or admin's removal does |

## Decisions I made (flag for Adebayo where marked)

| Decision | Choice | Product choice? |
|---|---|---|
| Manual "add to recurring" on an existing team card | owner/admin only, as the lead read it (it is making a grouping). A plain member still may take a meeting **out**, and put a **private** meeting into their own Personal card | agree with the lead's reading |
| When an owner/admin makes the team card, already-shared meetings of the same event | **they join it** (`series._adopt_shared`). Found through the join that made each meeting to the recorder's own event row, so only meetings whose event row still exists (about two days after the event) can be found. They are meetings the team can already read, so nothing new is shown to anyone; it only groups them | **real choice, low risk**: the alternative is a card that starts with one meeting while the team already has more |
| The "system" (backfill, a person leaving a team, a deleted team) | may still make a row where a meeting has to go (`actor` None). Moving back to Personal is always allowed | needed so those paths keep working |
| The #205 empty-row exception | **kept**, for **legacy** rows: an empty hand-made team card a plain member made before this change (only its creator sees an empty hand-made card, so nobody else could ever remove it), and an empty calendar card until the cleanup below has run. New team cards are made by owners and admins, so it does not widen. Its mark: none for a plain creator (the security review's point) | **real choice**: could be dropped once no legacy empty rows remain |
| Empty hand-made team cards in the cleanup | **not removed by the script** (someone made them on purpose). Series 4 in his data is one: he is the owner, so he removes it with the "Remove this recurring meeting" button | yes |
| What a plain member sees instead of "Add to recurring meeting" / "Make this a recurring meeting?" | **nothing** (no disabled button, no message). The Series row still shows the link to the team's recurring meeting when the meeting is in one, without "Change". New field `can_make_recurring` on the meeting page drives it; `suggest_recurring` is also false | plain words, see the guide |
| `place_meeting` for a plain member's share with no team card | the meeting is **cut loose** (no series), not left pointing at a Personal card (a shared meeting is not Personal-scope and would be hidden by the readers) | internal |

## What's involved

| File | Change |
|---|---|
| `app/series.py` | `may_make_row`, `non_empty_reason`, `can_group`; `sync_from_joins` (Personal, with the personal-mark check); `link_from_calendar` (no team card by a plain member, adopts); `_row_for_scope` (why-codes, `actor`, `explicit`, mark clearing), `_adopt_shared`, `place_meeting`/`follow_meeting` (`actor`, `explicit`), `create`, `attach`, `remove` (mark only for managers), `_rekey_empty_card` guard |
| `app/meeting_teams.py` | `share` and `move` pass the user as the actor (an explicit act); unshare and move back go to Personal |
| `app/api.py`, `app/api_schemas.py`, `app/api_series.py` | `can_make_recurring` on the meeting page; `suggest_recurring` needs it; `PUT /api/meetings/{id}/series` answers 403 `team_forbidden` (was always 404) |
| `app/series_backfill.py`, `scripts/remove_empty_team_cards.py` | the cleanup (`remove_empty_team_cards`, `EmptyCardsReport`) |
| `docs/openapi.json`, `web/lib/api/schema.d.ts` | regenerated |
| `web/components/app/meeting-view.tsx`, `web/tests/series-lead-designed.test.tsx` | the two controls drawn only when `can_make_recurring` |
| `scripts/capture_series_follow.py`, `docs/fe-captures/series-follow/52` to `106` | live captures (parts 5 and 6); `--keep 51` rewrites 52 and up (they were re-made: ids and dates differ from the #205 captures) |
| `docs/fe-integration-guide-series-teams.md` (section 11, section 10 patched, verification table) | the guide |
| `tests/test_series_personal_model.py` (35), `tests/test_series_follow.py`, `tests/test_series_remove.py` | tests first; old tests that built team cards with a plain member now promote Pat or expect no card |

## Verification

* **Tests first** (35 new, `tests/test_series_personal_model.py`): the sync makes the card in Personal for a one-team and a two-team person; flag off unchanged; a removed Personal mark stops the sync; a plain
  member's shared meeting makes no card and joins an existing one; an owner's and an admin's share, move and auto-link make it; unshare goes back to Personal; adoption (by share and by the auto-link) and what it does not
  take (Y's, private, another event); the mark cleared by an explicit share, kept by the automatic link and by a plain member's share; 403 and 201 on the hand routes; `can_make_recurring`; flag off; the plain-creator
  removal without a mark; reminders with a fake clock; the cleanup (counts, what stays, no marks, idempotent, counts only, the next sync makes the Personal card).
* **Mutation checks** (revert one rule, a test fails): sync in the default team (4 fail), anyone makes a row (9), hand create ungated (2), attach ungated (1), mark never cleared (1), adoption by the auto-link
  removed (1), adoption by share removed (1), plain creator writes the mark (1).
* **Bounded runs, CI runs the rest:** `ruff check .`, `ruff format --check .`, `tests/test_series*.py`, `test_multi_team_moved.py`, `test_multi_team_derived.py`, the openapi test; web `typecheck`, `lint`, `vitest` of
  the series, meeting and recurring files.
* **Live:** captures 52 to 106 against the running app; the guide's blocks were checked against their captures by script (29 blocks, 0 differences).
* **Not verified:** the reminders end to end against a real calendar and the real mail (fake-clock tests only; "do not change what the mail says" was kept); the frontend in a real browser; flag off live.

## Reminders: why he received none, and what the mail needs

Reading verified in the code: `series_mail.queue_due_reminders` looks only at teams whose `reminders_on` is true and at series with a `calendar_recurring_id` (it joins calendar events to the series by that id), and
`_queue_for` sends nothing for a card with no past meeting **and** no waiting agenda line. His data: 0 reminders, 0 mails. His only card with meetings (series 6, Personal) is **hand-made**, so it has no recurring id, no
next occurrence and can never remind; his calendar cards (1, 2, 3) sat in team 1 with no meeting. After the change a calendar-linked Personal card with one recorded meeting **does** queue the 1-hour and 30-minute mails
(fake-clock test, `test_a_calendar_linked_personal_card_with_one_recorded_meeting_queues_both_reminders`). **The mail needs:** a card made from a calendar event; one recorded meeting in it (or a waiting agenda line);
the event on the person's own calendar inside the window (the 1-hour mail goes from 60 to 35 minutes before, the 30-minute one from 30 to 5); `reminders_on` on for the card's team (**it is off by default for every
team, Personal included**; his is on); the person not having switched reminder mail off. **A hand-made card cannot remind**, with or without meetings. Nothing about what the mail says changed.

## Server steps for Adebayo (after this PR is merged and deployed; no meeting recording)

**What this is for, in plain words.** The old sync made an empty card in your team for each recurring event on your calendar (your cards 1, 2 and 3). From now on those cards live in your Personal space. This removes
the old empty team cards. It does not delete a meeting, a task, a note or a card with anything in it, and it writes no "removed" mark, so the next sync (within the hour) makes your Personal cards. Your card 4 (made
by hand, empty) is **not** touched: you are the owner, so remove it with the button on its page if you want it gone.

### Step 0. Look first (changes nothing)

**A good result:** line 1 says `OK: the cleanup script is in the deployed code`; both switches are `true`; the last line is `0 0`. If it is not `0 0`, wait and run it again.

```bash
ssh haven-respite 'bash -s' <<'EOF'
cd /opt/beyric-note-taker
echo "--- 1. the code that is running:"
git log -1 --oneline
test -f scripts/remove_empty_team_cards.py && echo "OK: the cleanup script is in the deployed code" || echo "NOT DEPLOYED YET: the merge is not on the server; stop here"
echo "--- 2. the two switches (both must be true):"
grep -E '^(SERIES_FOLLOW_MEETINGS|MULTI_TEAM_ENABLED)=' .env || echo "(neither is set in .env)"
echo "--- 3. is anything being recorded or processed right now? (two numbers, both must be 0):"
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -F " " -v ON_ERROR_STOP=1' <<'SQL'
select
  (select count(*) from meetings where status in ('bot_sent', 'processing')),
  (select count(*) from scheduled_joins
     where state in ('dispatching', 'no_capacity', 'joined')
        or (state = 'scheduled' and run_at <= now() + interval '10 minutes' and deadline_at >= now()));
SQL
EOF
```

### Step 1. Dry run (changes nothing)

**A good result:** the first line is `DRY RUN: nothing was changed. Run again with --apply to do it.`, then: `Calendar-made recurring cards of teams looked at: N`, `Empty ones removed: M` (**for you
expect 3**: series 1, 2 and 3, plus any teammate's empty ones), `Kept, because something is in them: K`, and four lines saying why (a meeting, an agenda line, a project, a mail). If "Empty ones removed" is far above
what you expect, stop and tell the lead.

```bash
ssh haven-respite 'bash -s' <<'EOF'
cd /opt/beyric-note-taker
docker compose exec -T web python scripts/remove_empty_team_cards.py </dev/null
EOF
```

### Step 2. Do it, then check it is finished

**A good result:** the first report starts `APPLIED: the database was changed.` with the same numbers as step 1; the second starts `DRY RUN` and says **`Empty ones removed: 0`**.

```bash
ssh haven-respite 'bash -s' <<'EOF'
cd /opt/beyric-note-taker
echo "--- applying:"
docker compose exec -T web python scripts/remove_empty_team_cards.py --apply </dev/null
echo "--- checking: a second pass must remove nothing:"
docker compose exec -T web python scripts/remove_empty_team_cards.py </dev/null
EOF
```

### Step 3. What to look at on the site

1. **Meetings, in your team:** the Recurring cards for your three calendar events are gone from the team's list.
2. **Switch to Personal:** within the hour the Recurring cards for the same events appear there (an empty card with `0 unfinished tasks`, `0 open questions`). Series 6 (hand-made) is still there.
3. After a meeting of one of them is recorded, its card holds that meeting. Share it to your team as owner: the team's card is made then.
4. Optional: series 4 (empty, hand-made, in your team): open it and use "Remove this recurring meeting" if you do not want it.

### Roll back

There is nothing to switch back: the removed cards were empty, and a card is made again by sharing a meeting of the event as owner or admin. Reverting the code makes the sync put the placeholders in the default team again.

## Follow-ups

* A "Removed recurring meetings" list with "Bring back" (still not built, see #205's SOP).
* Drop the empty-row exception once no legacy empty rows remain.
* Meetings shared by a plain member **before** an owner makes the card whose event row is already gone are not adopted (nothing links them to the event any more).
* A way for a plain member to ask an owner or admin to make the team's card (not designed).
