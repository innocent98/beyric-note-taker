# FE integration guide: hours, warnings and the refusal (billing stage S2)

Every body below is pasted from `docs/fe-captures/hours-usage/` (made by `scripts/capture_hours_usage.py`:
real migrations, the real app under uvicorn with `HOURS_MODE=enforce`, real HTTP). Anything that could
**not** be exercised live is marked **NOT LIVE** inline with the reason. People, addresses and times in
the captures are placeholders from the capture run (the `+01:00` in a time is the capture machine's
database time zone; treat every time as an instant). Passwords in the login captures are placeholders.

## What this is, in the person's terms

Until now a person had a lifetime allowance of recorded hours. From stage S2 the hours a person has are
**held in pieces** (a trial, the old pilot allowance, a plan's month, a pack, a gift) and spent in a fixed
order. Nobody can buy anything yet, so for now the only person with a "plan" is one who was given hours.
What the screens see changes in three places, and **only while the server runs with `HOURS_MODE=enforce`**
(off by default; the owner switches it on). With it off, every existing body is exactly as before, plus the
new `usage` fields set to `null`.

1. **`usage` in the session** gets new fields (below). `used_seconds` and `cap_seconds` keep their names.
2. **The refusal** when hours stop a new recording (403 `hours_cap_reached`) gets a machine-readable
   `reason` and new words (no word about a pilot, no email address).
3. **Two new bell notices** (and a mail each): at 80% and at 100%, once per person and period.

## The `usage` object: `GET /api/session` (and the login response, same shape)

`usage` is `null` for a visitor who is not signed in, as before. Signed in, it is always an object with all
nine keys. **The seven new keys are present but `null` unless the server is in `enforce`** (present-but-null,
never absent).

| Key | Meaning (enforce) | Null when |
|---|---|---|
| `used_seconds` | hours used, running meetings counted. For a pilot account: out of the **original** pilot total (below) | never |
| `cap_seconds` | hours the person holds now: every grant in force, spent or not (the old "cap"); for a pilot account the original total | an admin; also `0` for a person who holds nothing |
| `state` | `trial`, `pilot`, `active` or `none` (`past_due` and `lapsed` arrive with subscriptions) | not enforce |
| `seconds_left` | seconds the person may still record, never below 0 | not enforce, or an admin |
| `warning` | `"low"` once 80% is used, `"out"` when nothing is left | no warning, or not enforce |
| `can_record` | whether a new recording would start now, **including the reserve** (below): false while a meeting is running and the hours cannot cover two | not enforce |
| `unlimited` | true only for the platform admin | not enforce |
| `period_ends_at` | when the newest plan period in force ends | no plan period in force |
| `trial_ends_at` | when the trial in force ends | no trial in force |

### A pilot account that had used 4 of its 5 hours (capture 06)

It holds the 1 hour that was left, but the numbers show the **original** 5-hour total: `used_seconds` 14400 of `cap_seconds` 18000, while `seconds_left` is the 3600 that can still be recorded. Use `seconds_left` for "what can I still record" and the two old fields for the bar.

```json
{
  "used_seconds": 14400,
  "cap_seconds": 18000,
  "state": "pilot",
  "seconds_left": 3600,
  "warning": null,
  "can_record": true,
  "unlimited": false,
  "period_ends_at": null,
  "trial_ends_at": null
}
```

### The same person with `HOURS_MODE` off (capture 09: a second copy of the app on the same database)

```json
{
  "used_seconds": 14400,
  "cap_seconds": 18000,
  "state": null,
  "seconds_left": null,
  "warning": null,
  "can_record": null,
  "unlimited": null,
  "period_ends_at": null,
  "trial_ends_at": null
}
```

The two old fields come from the old cap rule; every new field is `null`. **Do not read `state` or
`can_record` unless they are not null.**

### 80% used (capture 10: a 0.8-hour recording was counted, see "NOT LIVE" below)

80% is of the hour actually held, so `used_seconds` reads 17280 of 18000 on the bar.

```json
{
  "used_seconds": 17280,
  "cap_seconds": 18000,
  "state": "pilot",
  "seconds_left": 720,
  "warning": "low",
  "can_record": true,
  "unlimited": false,
  "period_ends_at": null,
  "trial_ends_at": null
}
```

### Out of hours (capture 18)

```json
{
  "used_seconds": 18000,
  "cap_seconds": 18000,
  "state": "none",
  "seconds_left": 0,
  "warning": "out",
  "can_record": false,
  "unlimited": false,
  "period_ends_at": null,
  "trial_ends_at": null
}
```

`state` is `none` here although the person holds a (spent) grant: **a used-up pilot or trial shows `none`**.
Use `warning` and `can_record`, not `state`, to decide what to show.

### A recording that was running when the hours ran out (capture 21)

A 2-hour recording was counted after the 5 hours were already used: it is counted in full and the numbers do
not move. `seconds_left` stays `0` and **never goes negative**.

```json
{
  "used_seconds": 18000,
  "cap_seconds": 18000,
  "state": "none",
  "seconds_left": 0,
  "warning": "out",
  "can_record": false,
  "unlimited": false,
  "period_ends_at": null,
  "trial_ends_at": null
}
```

### A plan period plus leftover pilot hours (capture 14)

```json
{
  "used_seconds": 0,
  "cap_seconds": 79200,
  "state": "active",
  "seconds_left": 79200,
  "warning": null,
  "can_record": true,
  "unlimited": false,
  "period_ends_at": "2026-12-13T19:48:01.052917+01:00",
  "trial_ends_at": null
}
```

A person with a plan period in force is `active`, even while pilot hours remain, and `period_ends_at` is the
plan period's end. 22 hours held (20 plan + 2 pilot).

### A trial (capture 17)

```json
{
  "used_seconds": 0,
  "cap_seconds": 10800,
  "state": "trial",
  "seconds_left": 10800,
  "warning": null,
  "can_record": true,
  "unlimited": false,
  "period_ends_at": null,
  "trial_ends_at": "2026-11-30T19:48:01.052917+01:00"
}
```

### Holds nothing at all (capture 25)

An account created after the hours model arrived and given nothing (today every new sign-up, until the trial
exists):

```json
{
  "used_seconds": 0,
  "cap_seconds": 0,
  "state": "none",
  "seconds_left": 0,
  "warning": null,
  "can_record": false,
  "unlimited": false,
  "period_ends_at": null,
  "trial_ends_at": null
}
```

`cap_seconds` is `0`, `state` `none`, `can_record` false, `warning` `null`. This is different from the
out-of-hours person above (cap greater than 0 and `warning` `out`).

### The platform admin (capture 03)

```json
{
  "used_seconds": 0,
  "cap_seconds": null,
  "state": "none",
  "seconds_left": null,
  "warning": null,
  "can_record": true,
  "unlimited": true,
  "period_ends_at": null,
  "trial_ends_at": null
}
```

**Read `unlimited` first.** `state` is `none` and means nothing for an admin.

### Field-nesting traps

* `usage` is nested at `body.usage` of the session response; the refusal's `reason` is nested at
  `body.error.reason` (not at the top level).
* `state` `none` means three different people: holds nothing (`cap_seconds` 0), has spent everything
  (`warning` `out`), or only holds a pack or a gift (hours left, `can_record` true). Never gate on `state`.
* The old test `cap_seconds != null && used_seconds >= cap_seconds` still means "cannot record" under
  enforce (a person with nothing has cap 0 and used 0). Prefer `can_record` when it is not null.

## The refusal: 403 `hours_cap_reached`

Same route as before (a pasted link, `POST /api/meetings`); the body is the standard error shape plus one
optional key.

Held hours, all used (capture 19):

```json
{
  "error": {
    "code": "hours_cap_reached",
    "message": "You have used all of your recorded hours, so the note taker will not join new meetings. Your notes, transcripts and exports stay as they are.",
    "reason": "no_hours"
  }
}
```

No hours held at all (capture 26):

```json
{
  "error": {
    "code": "hours_cap_reached",
    "message": "You do not have any recorded hours at the moment, so the note taker will not join new meetings. Your notes, transcripts and exports stay as they are.",
    "reason": "no_plan"
  }
}
```

* `error.reason` is `no_hours` (hours are held and used up), `no_plan` (none held) or `reserve` (hours are held but set aside for meetings already running; see above). **The key is absent
  unless the server is in `enforce`** (and absent on every other error), so read it defensively.
* `error.message` is the sentence to show as it is. It never names a pilot or an address.
* With `HOURS_MODE` off or `shadow` the old refusal is unchanged except that its words no longer say "for the
  pilot" or give `hello@`: the old text now reads "You have used your 5 recorded hours, so the note taker
  will not join new meetings. Your notes, transcripts and exports stay as they are." (from a run of
  `tests/test_usage.py`, not from a capture).
* **NOT LIVE:** Send now (`POST /api/scheduled-joins/{id}/send-now`) and the shared-link route use the same
  refusal function, so they carry `reason` too; they were not captured under `enforce`.

### A second meeting while one is running: the reserve (captures 29 and 30)

While a person has a meeting running or just placed, a **second** one is allowed only if their hours cover a
reserve for each (30 minutes per meeting, or the calendar event's length). A first meeting is never held to it.
Capture 29: 40 minutes held and one meeting just placed. `seconds_left` is still about 2400 but `can_record`
is false.

```json
{
  "used_seconds": 0,
  "cap_seconds": 2400,
  "state": "active",
  "seconds_left": 2400,
  "warning": null,
  "can_record": false,
  "unlimited": false,
  "period_ends_at": null,
  "trial_ends_at": null
}
```

The refusal (capture 30), same status and code, `reason` `reserve`:

```json
{
  "error": {
    "code": "hours_cap_reached",
    "message": "You do not have enough recorded hours left to record another meeting at the same time: hours are set aside for each meeting that is running, and the ones a meeting does not use come back when it ends. Try again once a meeting has finished.",
    "reason": "reserve"
  }
}
```

Show `message` as it is. It is not "out of hours": do not show the out-of-hours state or the buy prompt for
`reserve`; it clears when a running meeting ends and its unused hours come back.

## The notices (bell, `GET /api/notifications`)

After the 0.8-hour recording (80%), capture 11 has one entry; after the last hour (100%) capture 20 has two;
after a further 2-hour recording past zero, capture 22 is **unchanged** (no second notice). Capture 22,
verbatim:

```json
[
  {
    "id": 2,
    "kind": "usage.hours_out",
    "severity": "warning",
    "title": "You are out of recorded hours",
    "body": "You have used all of your recorded hours, so the note taker will not join new meetings. A meeting that is recording now will finish. Your notes, transcripts and exports stay as they are.",
    "meeting_id": null,
    "created_at": "2026-11-16T18:48:01.632562Z",
    "read_at": null
  },
  {
    "id": 1,
    "kind": "usage.hours_low",
    "severity": "info",
    "title": "Most of your recorded hours are used",
    "body": "You have used 4.8 of your 5 recorded hours. When they run out the note taker will not join new meetings.",
    "meeting_id": null,
    "created_at": "2026-11-16T18:48:01.390531Z",
    "read_at": null
  }
]
```

* `usage.hours_low` (severity `info`) once per person and period at 80%; `usage.hours_out` (severity
  `warning`) once per person and period at 100%. A jump straight to 100% sends only `usage.hours_out`.
* Neither carries a `meeting_id`, so neither has a link target.
* Each also queues one email to the person (unless they switched email notices off).
  **NOT LIVE:** the capture does not read the outbox; the mail's text is the notice's `title` and `body`
  (tests: `tests/test_hours_enforce.py`).

## Calendar auto-join (NOT LIVE over HTTP)

When the person's hours stop a calendar meeting, the join runner skips it for the same reason: skip reason
`hours_cap` (hours held, all used) or the new `no_plan` (none held). Both end the row "skipped" (retried until
the 15-minute deadline, like `hours_cap` before), and both list as **Not recorded** with a Send now button.
The "Not recorded" reason sentences (`calendar_text.NOT_RECORDED_REASON_TEXT`):

* `hours_cap`: "Your recording hours are used up."
* `no_plan`: "You have no recording hours at the moment."
* `hours_reserved` (the reserve, above): "Your hours were set aside for another meeting that was running." It ends "skipped" like the others, is tried again when the other meeting ends, and raises a per-meeting bell notice, not the out-of-hours one.

Under `enforce` the person gets **one** `usage.hours_out` notice per period instead of one `calendar.hours_cap`
notice per skipped meeting. This path was exercised by the real join runner in tests, **not captured over
HTTP**.

## UX consequences to surface

* **A running meeting always finishes and is counted in full.** When it ends past zero the person is
  simply out of hours: show 0 left, never a negative, and do not blame them.
* **The warning at 80% is once per period.** Show the bar in a warning colour from `warning: "low"`; do not
  pop a second dialog because the bell already told them.
* **Out of hours stops every way of starting a recording** (pasted link, Send now, shared link, calendar
  auto-join). Reading, search, export, tasks, decisions and delete are untouched: say so (the server's message
  already does).
* **A person who holds nothing is not "out of hours".** `cap_seconds` 0 with `warning` null is a person who has
  no hours; the call to action differs from `warning: "out"`. Until sign-up gives a trial (stage S10) every new
  account is in this state under `enforce`.
* **Nothing can be bought yet.** Do not add a Buy button from these fields; the billing screens come with
  later stages.
* **Pilot people keep their original numbers.** A pilot account's grant holds only what was left of the old
  allowance, so the server adds the usage counted before the grant to both numbers: "4 of 5 h", not "0 of 1 h"
  (capture 06). The 80% notice says "4.8 of your 5 recorded hours" (capture 11). Once the pilot grant ends
  the bar changes to the hours then held. An account that had used more than 5 hours shows e.g. "6 of 6".
* **A second meeting can be refused while the first runs** (`reason` `reserve`, `can_record` false) even
  with hours left. It is a wait, not a limit: say so and let it clear when the other meeting ends.
* **Drive the warning from `warning`, never from the bar ratio.** For a pilot person the bar shows the original
  total, but 80% is of the hours actually held: capture 06 is at 80% of the bar with `warning` null, and `low`
  first comes at 96% of it (capture 10: 17280 of 18000). The 80% mail goes out at the same moment as `low`.
* The share page (`web/app/share/page.tsx`) shows the same plain words as the refusal (all used, or none held),
  built from `can_record`, `seconds_left` and `cap_seconds`; it no longer says "pilot" or gives an address. **A
  `reserve` refusal does not lock the page** (`ApiError.reason` is `reserve`): the person may try again, and
  hours set aside for a running meeting are not shown as "used all" up front.

## Polling

No new endpoint and nothing to poll on a timer. Re-read `GET /api/session` when the person starts a meeting,
when a meeting becomes ready (its recording was counted then), and when they open the sidebar; the bell count
(`unread_notifications`) already arrives with it.

## Verification

| Behaviour | Verified |
|---|---|
| Session `usage` shape: admin, pilot (original total), 80%, out, past zero, plan plus pilot, trial, holds nothing | live (captures 03, 06, 10, 14, 17, 18, 21, 25) |
| The reserve: `can_record` false and the 403 with `reason` `reserve` | live (captures 29, 30; the running meeting row was inserted by SQL as the placement would make it) |
| The new fields are all `null` with the flag off, old fields unchanged | live (capture 09) |
| 403 `hours_cap_reached` body with `reason` `no_hours` and `no_plan` | live (captures 19, 26) |
| `reason` absent when the flag is off | tests only (`tests/test_usage.py`) |
| The 80% and 100% notices, once each, none extra after a count past zero | live (captures 11, 20, 22) |
| A recording past zero is counted in full and never below zero | live (capture 21; the recording is counted by `usage.record_usage`, not by a real bot and pipeline) |
| The mail for each notice | tests only |
| Send now and shared-link refusal carry `reason` | not verified live (same function) |
| Calendar skip reasons `hours_cap` and `no_plan`, one notice for many skipped meetings, the Not recorded sentences | tests only (real join runner, no HTTP capture) |
| `trial` and `plan_period` states | live shape only: the grants were inserted by SQL because nothing creates them yet |
