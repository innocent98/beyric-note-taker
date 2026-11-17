# FE integration guide: hours, warnings, the refusal and the feature gates (billing stages S2 and S3)

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

## Feature gates: Ask Beyric, new translations and rewriting notes (billing stage S3)

Captures: `docs/fe-captures/feature-gates/` (made by `scripts/capture_feature_gates.py`: real migrations, the
real app under uvicorn, real HTTP; run once in each of the three modes below). **Today nothing changes for
anyone**: the gates ship dark (`FEATURE_GATES_MODE` is `off`) and are switched on only at go-live, because the
pilot accounts have these three features now. Build the screens so they are ready, and do not expect a refusal
until the owner says the gates are on.

### What it is, in the person's terms

Three buttons cost money on every press (each calls the model): **Ask Beyric**, **translate** (a French or
Spanish copy of the notes, which includes sending notes by email in French or Spanish) and **rewrite the notes
with another template**. When the gates are on, a person needs a live plan or trial to use them. A person
with no plan (a teammate who only reads shared notes, an expired trial, a used-up trial) keeps everything
else: reading, search, export, tasks, decisions, an existing translation, the Review spelling step,
settings, billing, delete. The check is on the **person pressing the button**, not on who recorded the meeting.

A paid plan with its hours used up still passes: these three use no recorded hours.

### The one new error: 403 `plan_inactive` (captures 03 to 07)

The same body from every gated call, with the feature named in the sentence. Show `error.message` as it is.

```json
{"error": {"code": "plan_inactive", "message": "Asking Beyric needs an active plan or trial. You can still read, search and export your notes, and use your tasks and decisions."}}
```

| Call | When refused | `error.message` starts |
|---|---|---|
| `POST /api/ask` (capture 03) | caller has no live plan or trial. **The free "my tasks" answer is refused too** (capture 04): Ask is one switch | `Asking Beyric needs an active plan or trial.` |
| `POST /api/meetings/{id}/translations` (capture 05) | a NEW translation only | `Translating notes needs an active plan or trial.` |
| `POST /api/meetings/{id}/regenerate` (capture 06) | the notes are kept and the model is not called | `Rewriting notes needs an active plan or trial.` |
| `POST /api/meetings/{id}/shares` with `language` `fr` or `es` (capture 07) | the share needs a NEW translation | `Translating notes needs an active plan or trial.` (no share is made) |

All four messages end with the same sentence: `You can still read, search and export your notes, and use your tasks and decisions.` There is no `reason` field on this error (unlike `hours_cap_reached`). The message says nothing about a pilot and gives no address.

**Not refused, even for a person with no plan** (captures 08 to 11): `GET /api/meetings`, `GET /api/meetings/{id}`, `GET /api/ask/scope`, `GET /api/review`, export, search, tasks, decisions. A **cached** translation (`POST .../translations` answers 200) and one **already being made** (202) are never refused, and `GET /api/meetings/{id}/translations/{language}` is never gated: only asking for a new one is. Sending notes by email in English is not gated.

### Order of errors

The gate is checked after the request is known to be valid and before the model is called, so a person with no plan who sends an empty question still gets 422 `question_invalid`, and a rewrite on a meeting that is not ready still gets 409 `not_ready`. Translation: 422 `language_unsupported` and 409 `not_ready` come first; 429 `translation_limit` comes after the gate. Sharing notes by email: 403 `email_not_verified`, 409 `not_ready` and 429 `share_limit` (20 recipients a day) can come **before** `plan_inactive`, which is raised when the share needs a new translation.

### What a person who passes sees (captures 14 to 18)

Nothing new: the existing bodies. A person on a trial gets 200 from Ask (capture 14: `kind` `none` for a question that finds nothing; capture 15: `kind` `tasks`), 202 from a new translation (capture 16) and 200 with the usual meeting detail from a rewrite (capture 18).

```json
{"kind": "tasks", "lead": "You have no open tasks assigned to you.", "points": []}
```

### Shadow mode (captures 19 to 21) and off (captures 22 and 23)

`shadow` is for the server operator and changes nothing in any body: the person with no plan is answered as in capture 19 (200) and 20 (202), and the server logs one line per call the gate would have refused (capture 21). `off` (capture 22) is identical and logs nothing (capture 23). **The frontend cannot tell the three modes apart except by receiving the 403 in `enforce`.**

### What the frontend must do (the screens are not part of this stage)

* **Handle 403 `plan_inactive` on all four calls** and show `error.message`. It is not a transient error: do not retry. Leave the person where they are and, where there is a billing screen, link to it.
* **There is no signal yet that says which of the three is open for this person** (no `features` field in `GET /api/session`; `usage.state` exists only while `HOURS_MODE=enforce`, and it does not follow `FEATURE_GATES_MODE`). Until a follow-up adds one, the buttons can only learn it by being refused. Do not build a screen that hides them from `usage.state`: the two switches are independent and `usage.state` is `null` when the hours rule is off.
* **A teammate with no plan** (reader only) can open every shared meeting and read it; Ask, translate and rewrite are refused for them. Show those three as unavailable with the server's sentence rather than as an error.
* **A running or cached translation can be shown to a person with no plan.** Only the "Translate" button that starts a new one is refused.
* **"Try again" on a failed translation, and translating again after the notes were rewritten (the translation is `stale`), start a new model call, so they are refused with 403 `plan_inactive` for a person with no plan.** The failed or stale translation stays as it is and can still be read (`GET .../translations/{language}`). Treat these two buttons like "Translate".
* **A team admin who rewrites or translates a meeting moved to their team is checked on their own plan**, not on the recorder's: a person with no plan gets the 403 even when the recorder pays.
* **Sending notes in French or Spanish is a translation.** The share dialog can get this 403 for a person with no plan; English sharing still works for them.
* **Rewriting is also a button on the old page**: `POST /meetings/{id}/regenerate` (a form post, not JSON) is gated the same way and shows the same sentence on the page with status 403.

## Paystack adapter, naira (billing stage S6)

**Nothing the frontend calls changed in this stage.** No route the browser uses was added or changed (there is still no checkout route: stage S8 adds the screens and the routes behind them). The only route S6 touches is `POST /api/billing/webhooks/paystack`, which **Paystack calls, never the browser**; it is written down here so the answers are copied, not remembered, and so S8's author knows what the payment pages will see. Every file is in `docs/fe-captures/paystack-webhook/` (made by `scripts/capture_paystack_webhook.py`: the real app under uvicorn with real migrations, real HTTP to the real route, the real settlement rules and the real adapter).

**What is NOT real in those captures: Paystack itself.** There are no Paystack test keys yet, so the adapter's calls to Paystack were answered by a script that returns Paystack's *documented sample bodies* (`tests/fixtures/paystack/README.md` names the page of each), changed only in our reference, the amount and, in one case, a float. **Not verified live:** that a real Paystack sends and answers these shapes.

### What the webhook route answers (captured)

| Capture | Request | Answer |
|---|---|---|
| `01-billing-off` | `BILLING_ENABLED` false | `404` `{"error":{"code":"not_found","message":"Not found."}}` |
| `02-paid` | signed `charge.success`, Paystack's verify answer matches | `200` `{"status":"ok"}`; the payment becomes `succeeded` (`03-paid-rows`: `provider_tx_id` `"4099260516"`, `card_country` `"NG"`, `country_mismatch` false) |
| `04-replay` | the same event again | `200` `{"status":"ok"}`; no second call to Paystack, still one event row (`05-replay-rows`) |
| `06-tampered`, `08-unsigned`, `07-stale-timestamp` | one changed byte; no signature header; a correct signature 400 seconds old | `401` `{"error":{"code":"not_authenticated","message":"The signature is not valid."}}`; nothing stored, Paystack not asked |
| `09-ping-ignored` | a genuine `ping` (also invoices, transfers, subscriptions) | `200` `{"status":"ignored"}`; stored nowhere |
| `10-float-amount` | Paystack's answer says the amount is `150000.0` | `503` `{"error":{"code":"error","message":"Try again later."}}`; the payment stays `pending`, the event stays waiting (`11-float-amount-rows`: `decided` false, `error` `ProviderError: malformed Paystack answer: amount is not a whole number of kobo`) |
| `12-retry-two-signatures` | Paystack's resend, two `v1` signatures (a secret rotation), a well-formed answer this time | `200` `{"status":"ok"}`; the payment is `succeeded` (`13-retry-rows`) |
| `14-amount-too-low` | Paystack's answer says 149999 kobo for 150000 asked | `200` `{"status":"ok"}`; the payment stays `pending` and the event is decided with `error` `amount too low: asked 150000, provider reports 149999 NGN` (`15-amount-too-low-rows`) |

A `400` (genuine signature, a body that is not an event we can read) is **not captured live**: tests only (`tests/test_billing_paystack.py`, `tests/test_billing_paystack_flow.py`).

### What the payment pages (stage S8) must know

* **The payer pays on Paystack's own page** (a link on `checkout.paystack.com`); the adapter refuses any other address. We never see a card number: only a reusable token, kept encrypted by S8. Send the payer to `Checkout.url` and back to the callback address.
* **The return page grants nothing and decides nothing.** It reads the payment's state (`pending`, `succeeded`, `failed`) from our database and polls. The webhook (or a verify by reference) is what settles it, so a payer who closes the tab still gets their hours.
* **Paystack sends a webhook only for a payment that succeeded.** A declined or abandoned payment sends nothing: the page can learn it only by our asking Paystack. The screen must not wait for a "failed" event that never comes; offer "Try again" after a sensible wait.
* **A saved card may be refused or may need the payer again.** A renewal charge answered `failed` (for example insufficient funds: a Paystack test card exists for it) or `pending` with the card "paused" (the card's bank wants the payer to authorize again) is not an error in the adapter: the person must be sent a pay link (spec 5.3 fallback). A card Paystack does not mark reusable (the test card `5078 5078 5078 5078 53` creates no token) can be paid once but never renewed by us.
* **The amount charged can be higher than the amount asked** (Paystack's own sample shows 40333 paid for 30050 requested): at least what we asked is accepted. Show the person what we asked, not what the card was charged.
* **Naira only.** Amounts are whole kobo end to end (NGN 1,500.00 is `150000`); Paystack's documented minimum is NGN 50.00.
* **A foreign card pays, and is noted.** The card's country (`authorization.country_code`) is stored as `card_country`; a difference from the billing country sets `country_mismatch` for a human look and never refuses the payment (S5). What a real foreign card returns is not verified live.

### The checkout request the adapter makes (documented path, not an HTTP route of ours)

`PaystackProvider.start_checkout` sends `POST https://api.paystack.co/transaction/initialize` with `{"email": <payer>, "amount": "150000", "currency": "NGN", "reference": "bn-<payment id>-<random>", "callback_url": <return address>}` and returns `Checkout(url, reference)` from the documented answer `{"status": true, "message": "Authorization URL created", "data": {"authorization_url": "https://checkout.paystack.com/3ni8kdavz62431k", "access_code": "3ni8kdavz62431k", "reference": "re4lyvq3s3"}}` (the page's own sample, `tests/fixtures/paystack/initialize_response.json`). This is a shape from Paystack's page and the adapter's unit test, **not** a capture of any call to Paystack and not an answer of ours.

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
| 403 `plan_inactive` body for Ask, the free "my tasks" answer, a new translation, a rewrite and a share in French | live (captures 03 to 07, `FEATURE_GATES_MODE=enforce`) |
| Reading, the Ask scope line, Review and the meeting list stay open for a person with no plan | live (captures 08 to 11) |
| A person on a trial passes all gates (Ask 200, translation 202, rewrite 200) | live (captures 14 to 18; the trial grant was inserted by SQL; the notes model was a fake client and Ask was shown on a question that finds nothing, so no OpenAI call was made) |
| Shadow lets the call through and logs one line with ids only; off logs nothing | live (captures 19 to 23: the lines come from the real logger, with a handler the capture script attached itself; that web's own logging prints them is proved by `tests/test_logging_setup.py`, a fresh interpreter, not by the capture) |
| A failed or stale translation asked for again is refused (and left readable); a team admin is checked on their own plan | tests only (`tests/test_feature_gates.py`) |
| A cached or running translation is not refused after a plan ends; the worker finishes a queued translation; the legacy page route `POST /meetings/{id}/regenerate` is refused with 403 and the same sentence | tests only (`tests/test_feature_gates.py`) |
| The reader-only teammate: reads shared notes, Ask refused, Ask works once they hold a plan | tests only |
| `past_due` and `lapsed` states against the gates | tests only (the states arrive with subscriptions in stage S8) |
| Export, decisions and the Review spelling step stay open | export and decisions: tests only (`tests/test_feature_gates.py`); Review list: live (capture 11); the spelling step: tests only. Search: not exercised in this stage (its code is untouched) |
| A `features` signal in the session payload | not built (see "What the frontend must do") |
| The webhook route's answers: 404 off, 200 paid, 200 replay, 401 bad signature, stale timestamp and no header, 200 `ignored`, 503 on a float amount then 200 on the retry with two signatures, 200 refused on a low amount | live over HTTP (`docs/fe-captures/paystack-webhook/`, 15 files), **with Paystack's side scripted from documented samples, not a real Paystack** |
| A `400` for a genuine but unreadable event | tests only |
| The checkout request and answer shape, saved-card charge, refund, verify | documented samples and unit tests only: **not verified live** (no Paystack test keys yet) |
| A real Paystack test-mode payment, the raw body through nginx and the Next.js proxy, what a foreign card returns in `country_code`, that real events match the documented samples | **not verified live**: the runbook in `docs/sop/2026-11-17-billing-paystack-adapter.md` |
