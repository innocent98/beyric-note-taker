# SOP: the organizer's bot is preferred (billing stage S12)

**What shipped.** When several accounts hold the **same calendar event with the same meeting link**, the account
that **organizes** it gets its bot in on time, and every other account's join runner holds back about two minutes
(`ORGANIZER_BOT_WAIT_SECONDS`, default 120) before it places; if a bot for that meeting code is in by then it stays
out, as it always did. It is **ON by default** (Adebayo: no dark shipping) with **one emergency kill switch**,
`PREFER_ORGANIZER_BOT` (default `true`). The skipped person's stale notice ("Sharing notes between accounts is not
available yet") is replaced by a true one. Branch `feature/organizer-bot-preferred`; the PR number is on the
checklist. **No migration, no new column, no endpoint, no screen, no FE guide change.**

## Why

Before this, "the first placed bot wins": two join runners race for the one-bot-per-code slot, whoever places first
records, **and that person's hours are charged** (billing spec 2.5). The organizer's bot could lose to a guest's, so a
guest paid for a meeting the organizer had set up. Adebayo decided on 2026-11-16 that the organizer's bot is the one
that records. The three-account live test (T1) that the spec put in front of this build was **dropped by Adebayo on
2026-11-19** ("we already know the fact, we have the rule in our code, just make the build straight"): the rule ships
on the strength of the tests below and is **not proved live** (see the last section).

## The rule, exactly (`app/organizer_bot.py`)

A guest's row waits (and only then) when **all** of these hold; every "no" means the guest's bot goes now, as before:

| Condition | Where | Why |
|---|---|---|
| The switch is on and `now` is before the guest's `run_at` + wait | `guest_should_wait`, `wait_ends_at` | the wait is bounded by the clock, whatever the organizer does |
| The guest's own event is a calendar one and `organizer_self` is **not true** (false, or unknown) | same | an organizer never waits; an event with no organizer flag is "not known to be mine" |
| Another account holds the same event: same platform and code, start within **5 minutes**, that account's event says `organizer_self = true`, status confirmed, and its join row is `scheduled`, `dispatching` or `no_capacity` | same, `BEFORE_THE_BOT`, `SAME_EVENT_START_TOLERANCE` | `joined` means its bot is in (placement refuses the guest by itself); `skipped`, `cancelled`, `failed`, `completed` will not place. The code alone is not enough: a recurring series or a personal Zoom room reuses one code |
| That account is **active**, its calendar connection is good, and its join rule and event still say "join" (the runner's own `will_join` decision) | `_can_place` | auto-join off, an override off, a decline, a cancel, a suspended or disconnected organizer never cause a wait |
| Placement would not refuse that account for its **hours, plan or trial, or its own bot limit** | `placement.could_be_placed` (`app/placement.py:274`) | the same entitlement rule `reserve_slot` applies (`HOURS_MODE` off or enforce, admins unlimited, trial grants), read without a lock or shadow logging |

So: a **lone guest**, an **organizer with no account here**, **auto-join off**, **no plan or hours**, **a calendar that
needs reconnecting**, **two calendars that both say "I organize"**, an event with **no organizer flag anywhere**: nobody
waits and the first placed bot records exactly as today. **Hours are charged to the account whose bot recorded**
(`placement.py` and `usage.py` are unchanged); if the organizer's bot records, the organizer is charged.

## How

| Piece | What and why |
|---|---|
| The wait is a **scheduling delay, not a sleep** | `join_runner._place` asks `guest_should_wait` just before it builds the placement request (`app/join_runner.py:620`). If yes, `_hold_for_the_organizer` (`:682`) returns without placing: the row stays `scheduled`, nothing is sent, **nothing is said to the guest**. The dispatch tick (10 s) looks again. No thread, no sleep (a test makes `time.sleep` raise) |
| **Nothing is stored that a restart could lose** | the wait is the clock (`run_at` + wait), read from the database row every time. A runner that dies and comes back at the end of the wait places (tested with a fresh session and with the organizer's runner held busy) |
| The hold **steps the row out of the due query** | each hold adds 1 to `attempts` and stamps `updated_at` with `min(now, wait_end - 15 s)`, so the row is not due again for the 15 s retry gap and is due **exactly when the wait ends**, never a gap late. Without this a crowd of waiting guests (an all-hands with 200 accounts) would fill the 25-row batch every tick and starve other rows |
| The **organizer's rows come first** in each tick | `_due_ids` (`:321`) orders `organizer_self` rows ahead of the rest while the switch is on, so the guests can never fill the batch ahead of the row they wait for. Tested with the organizer's row made last by id and a batch of 2 |
| The loser of a race is as today | after the wait, organizer and guest both try; the placement lock and `uq_scheduled_joins_live_code` (`app/models.py:655`) let one in and the other gets `meeting_has_note_taker`, final, with the notice (a threaded test with a 0.2 s send) |
| **The notice** | `_NOTICES["meeting_has_note_taker"]` (`app/join_runner.py:875`): "A note taker is already in "{name}", so yours did not join. It belongs to someone else, and its notes stay private to them unless they share or move the meeting to a team you are in." Words reuse the app's own ("private", "share or move ... to a team": Terms, `web/components/legal`, the meeting page's "Share with a team"). It does not say "teammate": the other account may not be in the person's team, and nobody is named |
| Settings | `app/config.py:499` `prefer_organizer_bot: bool = True` (a value that is not `true`/`false` stops startup, like `SPEAKER_LINE_CORRECTION`); `:503` `organizer_bot_wait_seconds = 120`, validated 0 to 600 at `:87` (above 600 could eat the 15-minute join window; outside the range stops startup; a blank line means the default) |
| Outlook | `isOrganizer` is asked for (`app/graph_client.py:81`), parsed into `organizer_self` (`app/graph_events.py:176-182`) and stored by the same sync code as Google (`app/gcal_sync.py:315`): **already wired, nothing added**. A test runs two Outlook accounts through `apply_batch` |

### Rejected alternatives

* **`time.sleep` in the runner.** It would hold the dispatch loop (one thread, 20 s budget per run) for two minutes.
* **A new column or migration (`place_after`).** The clock plus the existing `updated_at` gap does the same with nothing to migrate or roll back.
* **Waiting until the organizer's row is `joined` (unbounded).** If the organizer's bot never goes (crash, host refusal) the meeting would go unrecorded. The wait is capped at the run time plus 120 s.
* **Ending the guest's row as "skipped" at once when an organizer exists.** The same loss if the organizer cannot place; also needs the organizer's state to be certain, which only the wait gives.
* **Percent rollout, shadow mode, default off.** Adebayo's rule: no dark shipping. One kill switch only.
* **Stopping or adopting a bot** that the guest's runner placed first. Nobody's bot is stopped (as before).
* **Matching the same event by calendar UID.** The sync keeps no iCalUID; the code plus a 5-minute start tolerance is what is stored.
* **Reusing `reserve_slot` to ask whether the organizer could place.** It takes the placement lock; the new `could_be_placed` is a read-only twin, and a test runs both over the same cases (modes `off` and `enforce`, a plan, no plan, a trial, used up, suspended, an admin, at the bot limit) so they cannot drift.

## What's involved

`app/organizer_bot.py` (new: `guest_should_wait`, `_can_place`), `app/join_runner.py` (`_place`, `_hold_for_the_organizer`,
`_due_ids`, the notice), `app/placement.py` (`could_be_placed`), `app/config.py` (two settings and the range check),
`.env.example`, `deploy/README.md` (next to `SPEAKER_LINE_CORRECTION`), `tests/test_organizer_bot.py` (new, 60 tests),
the billing spec 2.5 and S12 rows, the master checklist. **Not touched:** `app/billing/cancel_checkout.py`,
`scripts/speaker_reattribution_report.py`, `.github/workflows/deploy.yml` (other PRs are in them).

## Verification

* `tests/test_organizer_bot.py`, 60 tests, real Postgres, real `apply_batch`, `run_due_joins` and `place_bot`, fake clock
  (the `now` of each run), three fake accounts, `HOURS_MODE=enforce`:
  the organizer records and both guests are told; guests **hold while the organizer's runner is busy** (row locked by
  another session) and stay out when it arrives inside the wait; the organizer **never arrives**: the first guest records
  at the end of the wait and the organizer, arriving late, is refused (exactly today); a **restarted** runner
  (new session) places at the end of the wait; the guest is due again **exactly** at the end of the wait; organizer with
  **no plan** (guest records at once); **lone guest**; organizer **with no account here**; **two organizers**; switch off;
  wait 30 s and 0; each cause of "no wait" one at a time (no grant, hours used up, suspended, needs reconnect,
  disconnected, rule none, override off, declined, event cancelled, flag false or unknown, join `joined`, `skipped`,
  `cancelled`, `failed`, `completed`, at the bot limit, a different code, a start 10 minutes apart); a **trial**, an
  **admin** and a **`no_capacity`** organizer do cause a wait; a start 4 minutes apart is the same event; a guest with an
  **unknown flag** waits for a known organizer, and nobody waits when neither side knows; the batch-ordering test; the
  **placement lock / unique index race** (two threads, 0.2 s send, exactly one bot, peak one in flight, final states
  `joined`, `skipped`, `skipped`); the notice words; Outlook end to end; the setting's parsing (`maybe`, empty, -1, 601 stop
  startup).
* **Mutation checks:** making `guest_should_wait` always false fails 30 of the 60; turning the batch ordering off fails
  the end-to-end rule tests (the guests' rows have the lower ids on purpose).
* Existing related tests unchanged and green: `tests/test_join_runner.py`, `tests/test_placement.py`,
  `tests/test_join_refusal.py` (212 passed). Those tests' calendars have two accounts that both say "I organize", which
  is the "nobody waits" case.
* Locally only: `ruff check` and `ruff format --check` (the commands CI runs), and the files above, on a scratch
  Postgres 17 started with `initdb`/`pg_ctl` on port 55432 (no Docker daemon was running) and removed afterwards. CI runs the rest.

## Operate / roll back

* **Kill switch:** `PREFER_ORGANIZER_BOT=false` in `.env`, then
  `docker compose up -d --force-recreate web worker scheduler`. The first placed bot records again. Nothing to clean up:
  nothing is stored. Shorten or lengthen the wait with `ORGANIZER_BOT_WAIT_SECONDS` (0 to 600).
* **Revert:** revert the PR; no migration. A guest row held at that moment is simply placed on the next tick.
* **To see it work:** the guests' rows stay `scheduled` for up to the wait; the organizer's meeting shows `bot_sent`;
  a guest's bell entry "A note taker is already there" appears after the organizer's bot is in (up to one tick later,
  about 15 s, because the held row steps out of the due query). The scheduler log has no line for a hold (a debug line
  only, `app.organizer_bot`).

## Follow-ups and known limits

* **Not proved live:** no real meeting has had three real calendars on it under this rule. T1 was dropped by Adebayo;
  the **first live shared event** is the proof. What to look at then: the organizer's meeting is the one recorded, the
  guests' notices, and who was charged (the recorder). Not proved either: the **Outlook organizer flag against a
  real Graph response** (the fixture is the documented shape, not a capture: `tests/fixtures/graph/documented_*`).
* If the organizer's calendar has **not synced the event yet** (no row), the guest cannot know and does not wait: the old
  race for that event. Calendars sync every minute and events are read days ahead, so this is the late-invite case only.
* The organizer's state is read **without a lock** and can change a moment later (the wait is bounded, so the worst case
  is a guest that waited two minutes for nothing and then recorded).
* Any account in the system that holds the event counts, not only teammates in the same team: the skipped person's notice
  says "someone else" for that reason.
* A guest who was **on hold when the scheduler restarts after the wait** places at once (late join), as any due row does.
* The held row's `attempts` counts holds (internal; nothing reads it but the retry gap).
* Hours still follow the recorder, so an organizer whose bot records is charged even if a guest asked for the meeting.
  That is the decision, not a gap.
