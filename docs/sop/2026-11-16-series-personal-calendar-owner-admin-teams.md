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
| A recurring card from a person's **own calendar** | always in their **Personal space**, never in a team (`sync_from_joins` now uses the person's personal team when the flag is on). Nothing overrides it, and **it is never re-keyed into a team** (review S1: the old re-key of an empty card was removed, so a one-team owner no longer gets a stranded duplicate and a two-team owner no longer gets the card moved). When an owner or admin shares a meeting of the event to a team, that makes a SEPARATE team card; the Personal card stays, with any lines typed into it |
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
| Who acts on a placement (review S5) | `actor` is now a **required** argument: a person, or `series.SYSTEM`. Leaving it out is a `TypeError`. `SYSTEM` (the backfill, a person leaving a team, a deleted team, unshare, move back) makes a row **only in a Personal team**; in a team it attaches to a row that exists and otherwise leaves the meeting ungrouped. Removal's stray path also passes `SYSTEM` (review SF2: it first passed the remover, which left another person's private stray ungrouped and let a remover who manages Y make a Y card). So the backfill no longer makes team cards for strays (the report prints `Meetings left ungrouped (their team has no card for them): N` only when N is above 0) | my choice (the other option was to make `actor` mandatory and let the system create anywhere) |
| `place_meeting` for a plain member's share with no team card | the meeting is **cut loose** (no series), not left pointing at a Personal card (a shared meeting is not Personal-scope and would be hidden by the readers) | internal |

## What's involved

| File | Change |
|---|---|
| `app/series.py` | `may_make_row`, `non_empty_reason`, `can_group`; `sync_from_joins` (Personal, with the personal-mark check); `link_from_calendar` (no team card by a plain member, adopts); `_row_for_scope` (why-codes, `actor`, `explicit`, mark clearing), `_adopt_shared` (locks the meetings it takes in, ordered, `FOR UPDATE`), `SYSTEM`/`Actor`, `place_meeting`/`follow_meeting` (required `actor`, `explicit`), `create`, `attach`, `remove` (mark only for managers; its strays use the remover as the actor). `_rekey_empty_card` is **removed** |
| `app/meeting_teams.py`, `app/deletion.py`, `app/series_backfill.py` | `share` and `move` pass the user as the actor (an explicit act); unshare, move back, a deleted team and the backfill pass `SYSTEM` |
| `app/api.py`, `app/api_schemas.py`, `app/api_series.py` | `can_make_recurring` and `recurring_team_id` on the meeting page (the team the picker names: the meeting's own, else the recorder's Personal space; read-only, with the flag off the default team as before); 403 `team_forbidden` or a missing CSRF token is named in the OpenAPI file; `suggest_recurring` needs it; `PUT /api/meetings/{id}/series` answers 403 `team_forbidden` (was always 404) |
| `app/series_backfill.py`, `scripts/remove_empty_team_cards.py` | the cleanup (`remove_empty_team_cards`, `EmptyCardsReport`) |
| `docs/openapi.json`, `web/lib/api/schema.d.ts` | regenerated |
| `web/components/app/meeting-view.tsx`, `web/tests/series-lead-designed.test.tsx` | the two controls drawn only when `can_make_recurring`; the picker names `recurring_team_id` from the meeting page (review M1 and SF1: with no team named the server used the default team, an ordinary team for a one-team person, so a plain member got a 403 and an owner an orphan empty team card; and a web-only fix would have broken the picker with the flag off, where the attach checks the default team). `POST /api/series` with no header still means the default team: the picker no longer relies on it |
| `scripts/capture_series_follow.py`, `docs/fe-captures/series-follow/52` to `106` | live captures (parts 5 and 6); `--keep 51` rewrites 52 and up (they were re-made: ids and dates differ from the #205 captures) |
| `docs/fe-integration-guide-series-teams.md` (section 11, section 10 patched, verification table) | the guide |
| `tests/test_series_personal_model.py` (43), `tests/test_series_follow.py`, `tests/test_series_remove.py` | tests first; old tests that built team cards with a plain member now promote Pat or expect no card |

## Verification

* **Tests first** (43 new, `tests/test_series_personal_model.py`): the sync makes the card in Personal for a one-team and a two-team person; flag off unchanged; a removed Personal mark stops the sync; a plain
  member's shared meeting makes no card and joins an existing one; an owner's and an admin's share, move and auto-link make it; unshare goes back to Personal; adoption (by share and by the auto-link) and what it does not
  take (Y's, private, another event); the mark cleared by an explicit share, kept by the automatic link and by a plain member's share; 403 and 201 on the hand routes; `can_make_recurring`; flag off; the plain-creator
  removal without a mark; reminders with a fake clock; the cleanup (counts, what stays, no marks, idempotent, counts only, the next sync makes the Personal card).
* **Mutation checks** (revert one rule, a test fails): sync in the default team (4 fail), anyone makes a row (9), hand create ungated (2), attach ungated (1), mark never cleared (1), adoption by the auto-link
  removed (1), adoption by share removed (1), plain creator writes the mark (1); after the review: the plain-member check in the link removed (6), the system allowed to make team cards (3), the adoption lock weakened (1); web: the picker asking for the meeting's team instead of Personal (1).
* **Bounded runs, CI runs the rest:** `ruff check .`, `ruff format --check .`, `tests/test_series*.py`, `test_multi_team_moved.py`, `test_multi_team_derived.py`, the openapi test; web `typecheck`, `lint`, `vitest` of
  the series, meeting and recurring files.
* **Live:** captures 52 to 106 against the running app; the guide's blocks were checked against their captures by script (29 blocks, 0 differences).
* **Not verified:** the adoption lock under real concurrency (the test checks the statement says `FOR UPDATE`); the reminders end to end against a real calendar and the real mail (fake-clock tests only; "do not change what the mail says" was kept); the frontend in a real browser; flag off live.

## Reminders: why he received none, and what the mail needs

Reading verified in the code: `series_mail.queue_due_reminders` looks only at teams whose `reminders_on` is true and at series with a `calendar_recurring_id` (it joins calendar events to the series by that id), and
`_queue_for` sends nothing for a card with no past meeting **and** no waiting agenda line. His data: 0 reminders, 0 mails. His only card with meetings (series 6, Personal) is **hand-made**, so it has no recurring id, no
next occurrence and can never remind; his calendar cards (1, 2, 3) sat in team 1 with no meeting. After the change a calendar-linked Personal card with one recorded meeting **does** queue the 1-hour and 30-minute mails
(fake-clock test, `test_a_calendar_linked_personal_card_with_one_recorded_meeting_queues_both_reminders`). **The mail needs:** a card made from a calendar event; one recorded meeting in it (or a waiting agenda line);
the event on the person's own calendar inside the window (the 1-hour mail goes from 60 to 35 minutes before, the 30-minute one from 30 to 5); `reminders_on` on for the card's team (**it is off by default for every
team, Personal included**; his is on); the person not having switched reminder mail off. **A card in the Personal space reminds only when the Personal space's own `reminders_on` is on** (it is off by default for every team, Personal included; **the default stays off** because the mail uses calendar data for a purpose the privacy policy names only after Google verification). A person whose TEAM has reminders on does not get them for a calendar card that now lives in Personal until they switch it on there. **A hand-made card cannot remind**, with or without meetings. Nothing about what the mail says changed.

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

**Reminders for the Personal cards (read-only check, then one switch in Settings).** The cards the sync makes in Personal remind only if the Personal space has reminders on. Run this and read the line:

```bash
ssh haven-respite 'bash -s' <<'EOF'
cd /opt/beyric-note-taker
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -F " " -v ON_ERROR_STOP=1' <<'SQL'
select 'personal spaces with reminders on / all:', count(*) filter (where reminders_on), count(*) from teams where is_personal;
SQL
EOF
```

To see yours, replace the select with `select reminders_on, reminder_timing from teams t join users u on u.id = t.owner_user_id where t.is_personal and u.email = '<your email>';` (`t` = on). If it says `f`: in the site, switch to **Personal** (the team switcher at the top of the sidebar), open **Settings**, find **Remind attendees before recurring meetings** and switch it on (choose the timing next to it). A good result: the row shows the switch on and a timing select. Nothing else is needed.

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

* A "Removed recurring meetings" list with "Bring back": built, `docs/sop/2026-11-16-bring-back-removed-recurring-meeting.md`.
* Drop the empty-row exception once no legacy empty rows remain.
* Meetings shared by a plain member **before** an owner makes the card whose event row is already gone are not adopted (nothing links them to the event any more).
* A way for a plain member to ask an owner or admin to make the team's card (not designed).
* **F1 An owner's share of an UNGROUPED calendar meeting makes no team card** (`place_meeting` returns at once when the meeting has no card). Happens when the owner removed her Personal card or her Personal space was full: her private recording is then in no card, and sharing it as owner makes no card and clears no mark. Fix when wanted: in `_touched`, when the meeting is still ungrouped and the actor manages the new scope, run the calendar link.
* **F2 The cleanup also removes an EMPTY calendar team card an owner made on purpose** (made by a share, the meeting then unshared or moved). It is meant to be run **once, right after the deploy**; a later run would remove such a card (no mark stops anything). The script's docstring now says so.
* **F3 Re-running `scripts/backfill_series_follow.py`** no longer makes team cards for strays (the system makes none): a stray whose team has no card is left ungrouped (counted in the new report line). It used to make the card.
* **F4 A plain member cannot take their meeting OUT of a team card in the UI** (the "Change" button is hidden with `can_make_recurring` false). The API still allows it (`series_id` null).
* **F5 `_adopt_shared` also takes in meetings that someone deliberately took out of a card, and meetings ungrouped by a removal** (a null `series_id` cannot tell "never grouped" from "taken out"). Product call.
* **F6 An owner's or admin's explicit share deletes the "removed" mark even when the team is full** (the mark goes before the room check), so no card is made and the next automatic link makes it without asking. Move the room check above the delete.
* Review nits fixed: the script's usage lines, the new 403 in `docs/openapi.json` for `POST /api/series`, `PUT /api/meetings/{id}/series` and `POST /api/meetings/{id}/recurring`, two formatter leftovers in `app/series.py`.
* **Lines typed in a Personal card are stranded once a team card exists** (by Adebayo's rule: the calendar card "stays yours"). Her recordings are then auto-shared into the team card, so the Personal card's lines are never consumed, and when the team has reminders on the Personal card stays quiet (`_already_reminded`), so the lines are never mailed either. When the team's reminders are off and Personal's are on, the Personal card reminds weekly with the same lines. The hint is built (`SeriesPage.team_card`, `docs/sop/2026-11-16-bring-back-removed-recurring-meeting.md`, guide 12.5).
* **Two rare deadlocks, nothing corrupted (one request gets a 500):** (a) two owners or admins move or share two meetings of the same event at once, both already shared and ungrouped, with no team card: the adoption's lock and the other's card insert wait on each other. (b) `remove` locks the row and then its strays; a share locks the meeting and then marks the row's topics stale: a legacy stray being shared while its row is removed. Fix later: lock the strays first, in id order.
