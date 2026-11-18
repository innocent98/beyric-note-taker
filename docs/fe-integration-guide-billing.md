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
| `state` | `trial`, `pilot`, `active`, `past_due`, `lapsed` or `none` (the last two since stage S8: a failed renewal's 3 days of grace, and a plan whose grace ended unpaid; see "Subscriptions, packs and the lapse") | not enforce |
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

An account created after the hours model arrived and given nothing (a new sign-up while `TRIAL_SECONDS` is
0; with the trial on, a new account holds a trial instead, see "The trial and the pilot's end" below):

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
  no hours; the call to action differs from `warning: "out"`. With `TRIAL_SECONDS` at 0 every new account is in this
  state under `enforce`; with the trial on (stage S10) a new account holds a trial from the moment its email
  is confirmed (section below).
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
* **The return page grants nothing and decides nothing.** It reads the payment's state (`pending`, `succeeded`, `failed`) from our database and polls. The webhook (or a verify by reference) is what settles it, so a payer who closes the tab will still get their hours once S8's grants exist (no grant handler is registered until then).
* **Paystack sends a webhook only for a payment that succeeded.** A declined or abandoned payment sends nothing: the page can learn it only by our asking Paystack. The screen must not wait for a "failed" event that never comes; offer "Try again" after a sensible wait.
* **A saved card may be refused or may need the payer again.** A renewal charge answered `failed` (for example insufficient funds: a Paystack test card exists for it) or `pending` with the card "paused" (the card's bank wants the payer to authorize again; the result carries `payer_action_url`, a Paystack page to send the payer to) is not an error in the adapter: the person must be sent a pay link (spec 5.3 fallback). A card Paystack does not mark reusable (the test card `5078 5078 5078 5078 53` creates no token) can be paid once but never renewed by us.
* **The amount charged can be higher than the amount asked** (Paystack's own sample shows 40333 paid for 30050 requested): at least what we asked is accepted. Show the person what we asked, not what the card was charged.
* **Naira only.** Amounts are whole kobo end to end (NGN 1,500.00 is `150000`); Paystack's documented minimum is NGN 50.00.
* **A foreign card pays, and is noted.** The card's country (`authorization.country_code`) is stored as `card_country`; a difference from the billing country sets `country_mismatch` for a human look and never refuses the payment (S5). What a real foreign card returns is not verified live.

### The checkout request the adapter makes (documented path, not an HTTP route of ours)

`PaystackProvider.start_checkout` sends `POST https://api.paystack.co/transaction/initialize` with `{"email": <payer>, "amount": "150000", "currency": "NGN", "reference": "bn-<payment id>-<random>", "callback_url": <return address>}` and returns `Checkout(url, reference)` from the documented answer `{"status": true, "message": "Authorization URL created", "data": {"authorization_url": "https://checkout.paystack.com/3ni8kdavz62431k", "access_code": "3ni8kdavz62431k", "reference": "re4lyvq3s3"}}` (the page's own sample, `tests/fixtures/paystack/initialize_response.json`). This is a shape from Paystack's page and the adapter's unit test, **not** a capture of any call to Paystack and not an answer of ours.

## Flutterwave checkout (billing stage S7): no new payload, and what to expect

**Nothing the frontend reads changed in S7.** The adapter adds no endpoint and changes no body: there is still no route that starts a payment (that is stage S8), and the only route S7 serves is the webhook that Flutterwave calls (`POST /api/billing/webhooks/flutterwave`, answers 404, 401, 400, 503 or 200, see the provider-layer SOP), which a screen never calls. So there is **no capture** for this section: a captured body here would be Flutterwave's, and none has been captured (no key). What follows is what the vendor's pages say and what the stage-S8 screens will have to do about it. **Every statement below is documented, not verified live** (the test-mode run is in `docs/sop/2026-11-17-billing-flutterwave-adapter.md`).

* **Where the payer goes.** S8 will give the screen a link on `checkout.flutterwave.com` (a hosted page) and send the payer there; after paying or cancelling Flutterwave redirects to our `redirect_url` with `status`, `tx_ref` and `transaction_id` in the query string. **Those query values grant nothing and must not be trusted:** the screen on return only reads the payment's state from our API (`pending`, `paid`, `failed`) and polls it; only the server's re-read of the payment moves it to `paid`.
* **A failed attempt does not end the checkout.** The hosted page stays open for another try until the payer pays or cancels; our payment stays `pending`/`failed` and can still become `paid` later. Show "not paid yet", never "payment lost".
* **Dollars only, and the payer's card must be able to pay in dollars.** A person billed outside Nigeria pays in US dollars. Tell them to use a card their bank lets spend in dollars (spec 5.2, item 5), or to change the billing country if they bill from Nigeria. Whether a Nigerian-issued card paying in dollars works is **not verified live**.
* **Plans and renewals ask for a card only**; a pack may offer other methods the account allows. The checkout page may ignore this if the dashboard setting "Disable preferred payment methods" is on.
* **A renewal by saved card can need the payer.** By default Flutterwave asks the payer to complete a 3D Secure step for every saved-card charge. The screen must expect that a renewal is `pending`: the charge stays open at Flutterwave waiting for the payer, who finishes it on **that charge's own link** (not a new checkout; two payments for one period would be a double charge). Surface it as "Your renewal needs one confirmation" with that link, **not** as a failure. (Whether this account is allowed to skip the step is a request to Flutterwave support; not verified.)
* **Refunds are slow.** A card refund is accepted at once but finishes in 3 to 15 days; show "refund started", never "refunded", until S9 says so.
* **A settlement in dollars is not promised.** Do not write "paid out in dollars" anywhere (spec 5.6).
* **The card country.** The payment's `card_country` will be `NG` for a Nigerian card whichever format Flutterwave used (`NIGERIA NG` in the verify answer, `NG` in the webhook) and empty for a bank transfer or mobile money; a mismatch note is the S5 rule.
* **Other Flutterwave events are ignored.** A webhook for something that is not a payment (a transfer, a subscription cancelled) is answered `200` `{"status":"ignored"}` and stored nowhere, as for Paystack; nothing on a screen changes.

## The trial and the pilot's end (billing stage S10)

Every body below is pasted from `docs/fe-captures/trial/`, made by `scripts/local_trial_e2e.py`: a scratch
database with the real migrations, the **real web server and the real scheduler as separate processes**, the
console mailer as the mail sink (`mail-sink.txt` is every mail the scheduler printed), and real HTTP, in the
order the features are switched on. `checks.txt` lists the 14 checks that script makes; `switch-on-proof.txt`
is the output of the dry-run and reconcile commands. **No route and no field is new**: the same `usage`
object, the same 403. What is new is which values a brand-new or a pilot account shows, and when.

**When a person gets the trial.** The moment their email is confirmed for the first time (the password link,
or Google), a `trial` grant of `TRIAL_SECONDS` (3 hours = 10800 s) is given, ending after 14 days or when the
hours are used, whichever is first. It is given in any `HOURS_MODE` (so a person who signs up before
`enforce` already holds their hours when it comes on), once per person, even if the account is deleted and
made again with the same address. Accounts that already existed when `TRIAL_SECONDS` was switched on get
theirs from the scheduler within a minute. **The sign-up and verification responses do not change** (trial
capture 09 is the same `{"status": "verified"}` as before); read `usage` from `GET /api/session` after the
person signs in.

A person whose trial was just given (trial capture 23, an account the scheduler's sweep gave one to):

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
  "trial_ends_at": "2026-12-01T15:20:27.527815Z"
}
```

The grant a real sign-up made (trial capture 10, read from the database): `{"kind": "trial", "seconds": 10800,
"starts_at": "2026-11-17T15:20:29.890001+00:00", "expires_at": "2026-12-01T15:20:29.890001+00:00"}`.

At exactly 80% (trial capture 17: 2.4 of 3 hours), `warning` is `"low"`, `seconds_left` 2160. Exactly 3 hours used
(trial capture 18), then the refusal when they start a meeting (trial capture 19):

```json
{
  "used_seconds": 10800,
  "cap_seconds": 10800,
  "state": "none",
  "seconds_left": 0,
  "warning": "out",
  "can_record": false,
  "unlimited": false,
  "period_ends_at": null,
  "trial_ends_at": "2026-12-01T15:20:29.890001Z"
}
```

```json
{"error": {"code": "hours_cap_reached", "message": "You have used all of your recorded hours, so the note taker will not join new meetings. Your notes, transcripts and exports stay as they are.", "reason": "no_hours"}}
```

The two notices (80% and 100%) are in the bell (trial capture 20) and were mailed (`mail-sink.txt`: "Most of your
recorded hours are used", "You are out of recorded hours").

**Field traps:**

* **`trial_ends_at` stays set after the hours are used** (trial capture 18): the trial grant is still in force
  until its 14 days are over, only spent. Do not read "trial over" from `trial_ends_at`; read `state` and
  `seconds_left`. A used-up trial shows `state: "none"`.
* **Once the 14 days pass with hours left,** the grant is no longer in force: `state` `none`, `cap_seconds` 0,
  `trial_ends_at` `null`, and a new recording is refused with `reason` `no_plan` (the shape of trial capture 33
  below). NOT LIVE as a 14-day wait: the boundary is proved by `tests/test_trial.py` with a fake clock (the last
  second before day 14 works, day 14 exactly does not).
* **Before `HOURS_MODE=enforce` the new `usage` fields are `null` even for a person who holds a trial** (trial
  capture 13: `HOURS_MODE=shadow`, the old cap decides, `cap_seconds` 18000). The trial's hours show up when
  `enforce` is switched on, and **what the person recorded before that is already taken off them**: trial
  capture 16 is the same person right after, 2 hours recorded in shadow, so `seconds_left` is 3600, not 10800.
  `used_seconds` 7200 of `cap_seconds` 10800.

**The pilot accounts' end.** Nothing changes for them until the owner sets `PAID_PLANS_OPEN_ON`. Then, 14 days
before the end, each person with pilot hours left gets **one** bell entry and **one** mail (trial capture 27 is the
bell entry, `mail-sink.txt` the mail the scheduler sent): kind `pilot.expiry`, title `Your pilot hours will
expire`. The body is the exact text in the SOP (a lawyer item until the owner confirms it). Before the end
nothing in the session changes (trial capture 28: still `state` `pilot`). After the end (trial capture 32) their
hours are no longer in force:

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

and a new recording is refused (trial capture 33) with `reason` `no_plan`:

```json
{"error": {"code": "hours_cap_reached", "message": "You do not have any recorded hours at the moment, so the note taker will not join new meetings. Your notes, transcripts and exports stay as they are.", "reason": "no_plan"}}
```

**UX consequences to surface:**

* **The end date is per person, not one global date.** Nobody's pilot hours end sooner than 7 days after their mail: someone first reached close to the end has their end moved out to at least a week after it, and the mail names that date. Unconfirmed accounts get no mail. The mail names only what is switched on (the Ask, translation and rewriting sentence appears only when the feature gates are on). The wording needs Adebayo's and the lawyer's confirmation before the date is set.
* A pilot person whose hours end is **not** told by the session payload (there is no `pilot_ends_at` field yet:
  see the SOP follow-ups); the bell and the mail carry the date. Do not invent a countdown from other fields.
* After the end, `warning` is `null` and `cap_seconds` is 0, **not** `out`: this is "holds nothing", the same
  call to action as a person who never had hours (take a plan), not "you used your hours".
* The pilot "4 of 5 h" display total ends with the grant: after the end there is no "of 5 h".
* Ask Beyric, new translations and rewriting notes are closed for these people once the feature gates are
  on (`FEATURE_GATES_MODE=enforce`); reading, search and export stay.

## Subscriptions, packs and the lapse (billing stage S8a)

Every body below is pasted from `docs/fe-captures/billing-subscriptions/` (made by
`scripts/capture_billing_subscriptions.py`: real migrations, the real app under uvicorn, real HTTP, the
real settlement rules and the real scheduler pass). **The payment provider in those captures is a
scripted test double (`FakeProvider`), not Paystack or Flutterwave**: its checkout address is
`https://fake.test/pay/<reference>`, and a real provider's page and its return address are not
verified (see the verification table). Nothing about a real vendor in this section is verified live.
People, times and amounts are the capture run's; amounts are whole minor units (cents, kobo).
`capture_40h` in the captures is a second plan the capture script inserted so that upgrade and
downgrade can be shown; the real catalog today is **one plan, `plan_20h`, 20 hours at 18.00 USD**, and one
pack, `pack_10h` (10 hours, 10.00 USD, valid 12 months). **Naira** (decided 2026-11-17): the plan is
NGN 25,000 a month (`amount_minor` 2500000 kobo) and the 10-hour pack is NGN 15,000 (`amount_minor` 1500000).

### What this is, in the person's terms

A person subscribes to a plan (hours each month), pays through the provider's own page, and gets
their hours the moment the payment is **verified**. The plan renews itself each month by charging
their saved card. If a renewal fails the plan goes into **3 days of grace** (the hours that were
left stay usable, no new month of hours is added until a payment goes through), and if it is still
unpaid it **lapses**. They can cancel (access to the end of the paid month, no refund of the unused
part), take a cancel back, move to a bigger plan now (they pay the difference for the days left) or to a
smaller one at the next renewal, and buy extra packs of hours while they have a plan.

**Dark.** Every route below answers a plain 404 `not_found` while the server's `BILLING_ENABLED` is
false and for any account that is not on `BILLING_ALLOWED_EMAILS` (capture 03 and 04),
before any body is read. Build the screens against an allow-listed test account.

### The two states you will see, and why they are different things

| Where | Values | Means |
|---|---|---|
| `state` (top level of `GET /subscription`, and `usage.state` in the session payload when the server is in `HOURS_MODE=enforce`) | `trial`, `pilot`, `active`, `past_due`, `lapsed`, `none` | what the **person** may do now. `past_due` and `lapsed` are new in this stage |
| `subscription.status` | `active`, `past_due`, `lapsed`, `canceled`, `ended` | the **subscription row** |

They do not always match. A person with a lapsed subscription whose pilot hours are not used up has
`state` `pilot`; a person who cancelled has `state` `active` and `subscription.cancel_at_period_end`
`true` until the period ends, then `state` `none` and `subscription.status` `canceled`. Use `state` for
"what can this person do", `subscription.status` and the `can_*` flags for the billing screen.

| `subscription.status` | What it is | What the screen shows |
|---|---|---|
| `active` | inside a paid period | the plan, the period's end, the hours; the buttons the `can_*` flags allow |
| `past_due` | the renewal failed: the 3 days of grace | a banner on every screen, `grace_ends_at`, a **Pay now** button (`can_pay_now`), the hours left (only what was left of the old period) |
| `lapsed` | the grace ended unpaid | the plan is over; offer a new checkout. Ask Beyric, new translations, rewriting notes and new recordings are closed unless a pack or pilot hours remain (recordings only) |
| `canceled` | the person stopped it and the period ended | the plan is over; offer a new checkout |
| `ended` | closed otherwise (a deleted account) | not seen by a signed-in person |

**`status` is shown by the clock.** A subscription whose renewal pass has not run yet (the scheduler
runs about every 60 seconds) is already shown as `past_due`, and as `lapsed` 72 hours after its due
moment, so the screen never needs to guess. Capture 59 is read before the scheduler's pass, capture
61 after it: the same.

### The routes

All are under `/api/billing/`, need a signed-in person, and the writes need the `X-CSRF-Token` header
like every other route (a missing one is 403 `csrf_rejected`, capture 15). Only the caller's own
data is ever read or written: no route takes a person's id.

| Route | What |
|---|---|
| `GET /plans` | what the caller can buy and the price in their currency |
| `GET /subscription` | their state, hours, subscription and the newest payment still waiting |
| `POST /checkout` | start a hosted checkout for a plan or a pack |
| `GET /payments` | their payments, newest first (the billing history) |
| `GET /payments/{reference}` | one of their payments' state (the page the payer returns to polls it) |
| `POST /subscription/cancel`, `POST /subscription/resume` | stop the renewal, take it back |
| `GET /subscription/change-plan/preview?plan_code=` | what a change would cost, before the person commits |
| `POST /subscription/change-plan` | upgrade (a checkout) or downgrade (at renewal) |
| `POST /subscription/pay-link` | the provider's page to pay what is overdue |

### What they can buy: `GET /plans`

`GET /api/billing/plans` (capture 12, status **200**)

Response body:

```json
{
  "route": {
    "currency": "USD",
    "provider": "flutterwave"
  },
  "plans": [
    {
      "code": "plan_20h",
      "name": "20 hours a month",
      "monthly_seconds": 72000,
      "price": {
        "currency": "USD",
        "amount_minor": 1800
      }
    },
    {
      "code": "capture_40h",
      "name": "40 hours a month",
      "monthly_seconds": 144000,
      "price": {
        "currency": "USD",
        "amount_minor": 3400
      }
    }
  ],
  "packs": [
    {
      "code": "pack_10h",
      "name": "10 extra hours",
      "seconds": 36000,
      "valid_months": 12,
      "price": {
        "currency": "USD",
        "amount_minor": 1000
      }
    }
  ]
}
```

*Nothing is trimmed. `plan_20h` and `pack_10h` are the real seeded rows; `capture_40h` is the plan only the capture script added.*
Naira (Nigeria), capture 69: the plan and the pack are priced in kobo.
`price` is **null** when a plan has no price in the person's currency (the capture's extra `capture_40h` plan has no naira price):

`GET /api/billing/plans` (capture 69, status **200**)

Response body:

```json
{
  "route": {
    "currency": "NGN",
    "provider": "paystack"
  },
  "plans": [
    {
      "code": "plan_20h",
      "name": "20 hours a month",
      "monthly_seconds": 72000,
      "price": {
        "currency": "NGN",
        "amount_minor": 2500000
      }
    },
    {
      "code": "capture_40h",
      "name": "40 hours a month",
      "monthly_seconds": 144000,
      "price": null
    }
  ],
  "packs": [
    {
      "code": "pack_10h",
      "name": "10 extra hours",
      "seconds": 36000,
      "valid_months": 12,
      "price": {
        "currency": "NGN",
        "amount_minor": 1500000
      }
    }
  ]
}
```

Before the person has chosen a billing country (`PUT /api/billing/profile/country`, stage S5), there is no route and
nothing is priced:

`GET /api/billing/plans` (capture 08, status **200**)

Response body:

```json
{
  "route": null,
  "plans": [],
  "packs": []
}
```

**Trap:** a person who already has a plan sees the prices in the currency the **plan was sold in**, even
if they have since asked to change their billing country (a change that crosses Nigeria and elsewhere waits for the
period's end): packs and upgrades are paid on the same route as the plan.

### Their state: `GET /subscription`

Never had a plan (capture 09):

```json
{
  "state": "none",
  "hours": {
    "seconds_left": 0,
    "holdings_seconds": 0,
    "warning": null
  },
  "subscription": null,
  "pending_payment": null
}
```

A checkout started and not paid yet (capture 19): no subscription, no hours, and `pending_payment` shows what is waiting:

```json
{
  "state": "none",
  "hours": {
    "seconds_left": 0,
    "holdings_seconds": 0,
    "warning": null
  },
  "subscription": null,
  "pending_payment": {
    "reference": "bn-1-401d29c8bdee865f",
    "kind": "plan",
    "status": "pending",
    "currency": "USD",
    "amount_minor": 1800,
    "created_at": "2026-11-17T16:27:28.526857Z",
    "paid_at": null,
    "pay_url": "https://fake.test/pay/bn-1-401d29c8bdee865f"
  }
}
```

Active, after the payment was verified (capture 26):

```json
{
  "state": "active",
  "hours": {
    "seconds_left": 72000,
    "holdings_seconds": 72000,
    "warning": null
  },
  "subscription": {
    "id": 1,
    "status": "active",
    "plan_code": "plan_20h",
    "plan_name": "20 hours a month",
    "currency": "USD",
    "amount_minor": 1800,
    "current_period_start": "2026-11-17T16:27:28.762167Z",
    "current_period_end": "2026-12-17T16:27:28.762167Z",
    "cancel_at_period_end": false,
    "pending_plan_code": null,
    "grace_ends_at": null,
    "next_retry_at": null,
    "can_cancel": true,
    "can_resume": false,
    "can_change_plan": true,
    "can_pay_now": false
  },
  "pending_payment": null
}
```

| Field | Meaning |
|---|---|
| `hours.seconds_left`, `hours.holdings_seconds` | what they may still record, and what they hold in all, in seconds; `warning` is `low` at 80% used, `out` at 100% |
| `subscription.amount_minor` | what the **next renewal** will charge (the plan they will be on, at today's price), **not** what they last paid |
| `subscription.cancel_at_period_end` | they cancelled: access to `current_period_end`, then it ends |
| `subscription.pending_plan_code` | a downgrade waiting for the renewal |
| `subscription.grace_ends_at`, `next_retry_at` | only while `past_due`; `next_retry_at` is null once they cancelled or no more attempts are left |
| `can_cancel`, `can_resume`, `can_change_plan`, `can_pay_now` | which buttons to show; **use these, do not work them out** |
| `pending_payment` | the newest payment still `pending` (a checkout they have not finished, or a charge waiting for their bank), else null |

### Buying a plan or a pack: `POST /checkout`

`POST /api/billing/checkout` (capture 17, status **200**)

Request body:

```json
{
  "kind": "plan",
  "code": "plan_20h"
}
```

Response body:

```json
{
  "payment": {
    "reference": "bn-1-401d29c8bdee865f",
    "kind": "plan",
    "status": "pending",
    "currency": "USD",
    "amount_minor": 1800,
    "created_at": "2026-11-17T16:27:28.526857Z",
    "paid_at": null,
    "pay_url": "https://fake.test/pay/bn-1-401d29c8bdee865f"
  },
  "checkout_url": "https://fake.test/pay/bn-1-401d29c8bdee865f"
}
```

Send the person to `checkout_url` (it is the same address as `payment.pay_url`). **Nothing is granted by this call or
by the page they come back to.** The page they return to must poll `GET /payments/{reference}` (capture 18):

```json
{
  "reference": "bn-1-401d29c8bdee865f",
  "kind": "plan",
  "status": "pending",
  "currency": "USD",
  "amount_minor": 1800,
  "created_at": "2026-11-17T16:27:28.526857Z",
  "paid_at": null,
  "pay_url": "https://fake.test/pay/bn-1-401d29c8bdee865f"
}
```

until `status` is no longer `pending`. The provider tells us when it is paid (or the server asks the provider
within about three minutes if its message never arrives); the hours appear then. The return address is
`PUBLIC_BASE_URL/billing/return` unless the server sets `BILLING_RETURN_URL`; **the page does not exist yet
(stage S8b) and must read OUR reference from the address the provider adds** (Paystack adds `reference` and
`trxref`; Flutterwave's names are not verified).

A pack (needs a live plan, capture 33) is the same call with `"kind": "pack"`; it is paid on the plan's route, and a plan in
its grace may still buy one:

```json
{
  "payment": {
    "reference": "bn-2-fa46fc6ca99d7635",
    "kind": "pack",
    "status": "pending",
    "currency": "USD",
    "amount_minor": 1000,
    "created_at": "2026-11-17T16:27:29.469010Z",
    "paid_at": null,
    "pay_url": "https://fake.test/pay/bn-2-fa46fc6ca99d7635"
  },
  "checkout_url": "https://fake.test/pay/bn-2-fa46fc6ca99d7635"
}
```

After the payment was verified, the hours are the plan's plus the pack's (capture 35: `hours.seconds_left` 108000 = 72000 + 36000).

**What a wrong or short payment does** (captures 20, 21): nothing. A payment that the provider reports with the
wrong amount, currency or reference is refused, the payment stays `pending`, and no hours are granted.
The same event delivered twice grants once (capture 24). The provider's message to us:

`POST /api/billing/webhooks/flutterwave` (capture 23, status **200**), sent by the provider, not by the browser:

```json
{
  "status": "ok"
}
```

### Billing history: `GET /payments`

The caller's own payments, newest first, at most 50 (capture 63); another account gets an empty list (capture 67).
`pay_url` is set only on a pending one.

```json
{
  "payments": [
    {
      "reference": "bn-7-206192995e94f44b",
      "kind": "renewal",
      "status": "pending",
      "currency": "USD",
      "amount_minor": 3400,
      "created_at": "2026-11-17T16:27:31.967339Z",
      "paid_at": null,
      "pay_url": "https://fake.test/3ds/open-charge"
    },
    {
      "reference": "bn-6-1fc3293b9a854155",
      "kind": "pack",
      "status": "pending",
      "currency": "USD",
      "amount_minor": 1000,
      "created_at": "2026-11-17T16:27:31.804361Z",
      "paid_at": null,
      "pay_url": "https://fake.test/pay/bn-6-1fc3293b9a854155"
    },
    {
      "reference": "bn-5-cc0a7f73fee1b704",
      "kind": "renewal",
      "status": "succeeded",
      "currency": "USD",
      "amount_minor": 3400,
      "created_at": "2026-11-17T16:27:31.741046Z",
      "paid_at": "2026-11-17T16:27:31.846924Z",
      "pay_url": null
    },
    {
      "reference": "bn-4-d828bcde666019c9",
      "kind": "renewal",
      "status": "failed",
      "currency": "USD",
      "amount_minor": 3400,
      "created_at": "2026-11-17T16:27:31.283611Z",
      "paid_at": null,
      "pay_url": null
    },
    {
      "reference": "bn-3-ea278de93a0ba827",
      "kind": "upgrade",
      "status": "succeeded",
      "currency": "USD",
      "amount_minor": 1599,
      "created_at": "2026-11-17T16:27:30.445612Z",
      "paid_at": "2026-11-17T16:27:30.620339Z",
      "pay_url": null
    },
    {
      "reference": "bn-2-fa46fc6ca99d7635",
      "kind": "pack",
      "status": "succeeded",
      "currency": "USD",
      "amount_minor": 1000,
      "created_at": "2026-11-17T16:27:29.469010Z",
      "paid_at": "2026-11-17T16:27:29.550150Z",
      "pay_url": null
    },
    {
      "reference": "bn-1-401d29c8bdee865f",
      "kind": "plan",
      "status": "succeeded",
      "currency": "USD",
      "amount_minor": 1800,
      "created_at": "2026-11-17T16:27:28.526857Z",
      "paid_at": "2026-11-17T16:27:28.762167Z",
      "pay_url": null
    }
  ]
}
```

### Cancel and resume

Cancel (capture 36) stops the renewal. The plan and the hours last to `current_period_end`; nothing is refunded. Asking twice is harmless (capture 37).

```json
{
  "id": 1,
  "status": "active",
  "plan_code": "plan_20h",
  "plan_name": "20 hours a month",
  "currency": "USD",
  "amount_minor": 1800,
  "current_period_start": "2026-11-17T16:27:28.762167Z",
  "current_period_end": "2026-12-17T16:27:28.762167Z",
  "cancel_at_period_end": true,
  "pending_plan_code": null,
  "grace_ends_at": null,
  "next_retry_at": null,
  "can_cancel": false,
  "can_resume": true,
  "can_change_plan": true,
  "can_pay_now": false
}
```

Resume (capture 38) takes it back while the period still runs; `409 not_canceled` if there is nothing to take back, and
`409 payment_overdue` in the grace (cancel in the grace stops the retries; it cannot be resumed).

### Change plan

**Upgrade** (a plan with more hours): the person pays the price difference for the days left, and the extra hours are
added to this period, **after the payment is verified**. **Downgrade**: stored, applied at the next renewal at the
smaller plan's price. Choosing the plan you are on takes a waiting downgrade back. **Show the price before the person
commits**: the upgrade's amount is charged by the checkout the call creates, so ask the preview first
(captures 41 and 45):

`GET /api/billing/subscription/change-plan/preview?plan_code=capture_40h` (capture 41, status **200**)

Response body:

```json
{
  "kind": "upgrade",
  "plan_code": "capture_40h",
  "currency": "USD",
  "amount_minor": 1599,
  "extra_seconds": 71640,
  "takes_effect": "now"
}
```

`GET /api/billing/subscription/change-plan/preview?plan_code=plan_20h` (capture 45, status **200**)

Response body:

```json
{
  "kind": "downgrade",
  "plan_code": "plan_20h",
  "currency": "USD",
  "amount_minor": 1800,
  "extra_seconds": null,
  "takes_effect": "next_renewal"
}
```

Then the change (capture 42, an upgrade, answers with a checkout):

```json
{
  "result": "checkout",
  "subscription": {
    "id": 1,
    "status": "active",
    "plan_code": "plan_20h",
    "plan_name": "20 hours a month",
    "currency": "USD",
    "amount_minor": 1800,
    "current_period_start": "2026-11-17T16:27:28.762167Z",
    "current_period_end": "2026-12-17T16:27:28.762167Z",
    "cancel_at_period_end": false,
    "pending_plan_code": null,
    "grace_ends_at": null,
    "next_retry_at": null,
    "can_cancel": true,
    "can_resume": false,
    "can_change_plan": true,
    "can_pay_now": false
  },
  "payment": {
    "reference": "bn-3-ea278de93a0ba827",
    "kind": "upgrade",
    "status": "pending",
    "currency": "USD",
    "amount_minor": 1599,
    "created_at": "2026-11-17T16:27:30.445612Z",
    "paid_at": null,
    "pay_url": "https://fake.test/pay/bn-3-ea278de93a0ba827"
  },
  "checkout_url": "https://fake.test/pay/bn-3-ea278de93a0ba827"
}
```

A downgrade (capture 46) answers `result` `scheduled`, `payment` null, `checkout_url` null; note `pending_plan_code`
and that `amount_minor` is now what the renewal will charge:

```json
{
  "result": "scheduled",
  "subscription": {
    "id": 1,
    "status": "active",
    "plan_code": "capture_40h",
    "plan_name": "40 hours a month",
    "currency": "USD",
    "amount_minor": 1800,
    "current_period_start": "2026-11-17T16:27:28.762167Z",
    "current_period_end": "2026-12-17T16:27:28.762167Z",
    "cancel_at_period_end": false,
    "pending_plan_code": "plan_20h",
    "grace_ends_at": null,
    "next_retry_at": null,
    "can_cancel": true,
    "can_resume": false,
    "can_change_plan": true,
    "can_pay_now": false
  },
  "payment": null,
  "checkout_url": null
}
```

And choosing the plan they are on again (capture 47) answers `result` `cleared`.

### The grace and the lapse (what the screen must do)

A failed renewal is learned by the server, not the browser: the scheduler charges the saved card at the due moment, then
tries again 24 and 48 hours later; at 72 hours the plan lapses. Capture 49 shows the rows after the first failed
attempt: **no new hours were granted** (the plan's grants are only the old ones). Capture 50:

```json
{
  "state": "past_due",
  "hours": {
    "seconds_left": 179640,
    "holdings_seconds": 179640,
    "warning": null
  },
  "subscription": {
    "id": 1,
    "status": "past_due",
    "plan_code": "capture_40h",
    "plan_name": "40 hours a month",
    "currency": "USD",
    "amount_minor": 3400,
    "current_period_start": "2026-10-18T15:27:31.265227Z",
    "current_period_end": "2026-11-17T15:27:31.265227Z",
    "cancel_at_period_end": false,
    "pending_plan_code": null,
    "grace_ends_at": "2026-11-20T15:27:31.265227Z",
    "next_retry_at": "2026-11-18T15:27:31.265227Z",
    "can_cancel": true,
    "can_resume": false,
    "can_change_plan": false,
    "can_pay_now": true
  },
  "pending_payment": null
}
```

What to do with it:

1. **Show a banner on every screen** while `status` is `past_due`: the payment did not go through, the date `grace_ends_at`,
   and that no new hours are added until a payment succeeds. The hours the person still has (`hours.seconds_left`) are
   what is left of the old period; Ask Beyric, translation and rewriting stay on during the grace.
2. **Offer Pay now** when `can_pay_now` is true: `POST /subscription/pay-link` (capture 52) returns the provider's page:

`POST /api/billing/subscription/pay-link` (capture 52, status **200**)

Response body:

```json
{
  "payment": {
    "reference": "bn-5-cc0a7f73fee1b704",
    "kind": "renewal",
    "status": "pending",
    "currency": "USD",
    "amount_minor": 3400,
    "created_at": "2026-11-17T16:27:31.741046Z",
    "paid_at": null,
    "pay_url": "https://fake.test/pay/bn-5-cc0a7f73fee1b704"
  },
  "checkout_url": "https://fake.test/pay/bn-5-cc0a7f73fee1b704"
}
```

   Calling it again returns the **same page** (capture 53), not a second checkout.
3. **Hide upgrade, downgrade and resume** (`can_change_plan` is false): the server answers 409 `payment_overdue` (capture 51).
   Cancel stays available and stops the retries. A pack may still be bought (capture 54).
4. When the payment is verified the plan is `active` again with a **new period starting from the payment date**
   (capture 56) and the old leftover hours end then (no rollover).

**A card that needs the bank's confirmation (3-D Secure).** The saved card's bank may ask the person to confirm.
The charge then waits for them and its own page is in `pending_payment.pay_url` (capture 57, `kind` `renewal`,
`status` `pending`). Send them to that address: `POST /subscription/pay-link` returns **exactly the same address**
(capture 58), never a second checkout **however old the charge is**: before it would make a new page the server asks the provider about every open charge of the period, and only when the provider says one failed does a new page exist (Paystack reports an abandoned charge as pending, so an abandoned charge keeps its page; whether a real paused-charge page expires is not verified live). An unpaid pay-link page is returned again however old it is. If the provider cannot be asked about an open charge the call is 502 `provider_error`, and while a queued charge has no page yet it is 409 `payment_in_progress`. A second charge is not made while it waits.

```json
{
  "state": "past_due",
  "hours": {
    "seconds_left": 180000,
    "holdings_seconds": 180000,
    "warning": null
  },
  "subscription": {
    "id": 1,
    "status": "past_due",
    "plan_code": "capture_40h",
    "plan_name": "40 hours a month",
    "currency": "USD",
    "amount_minor": 3400,
    "current_period_start": "2026-10-18T15:27:31.957608Z",
    "current_period_end": "2026-11-17T15:27:31.957608Z",
    "cancel_at_period_end": false,
    "pending_plan_code": null,
    "grace_ends_at": "2026-11-20T15:27:31.957608Z",
    "next_retry_at": "2026-11-18T15:27:31.957608Z",
    "can_cancel": true,
    "can_resume": false,
    "can_change_plan": false,
    "can_pay_now": true
  },
  "pending_payment": {
    "reference": "bn-7-206192995e94f44b",
    "kind": "renewal",
    "status": "pending",
    "currency": "USD",
    "amount_minor": 3400,
    "created_at": "2026-11-17T16:27:31.967339Z",
    "paid_at": null,
    "pay_url": "https://fake.test/3ds/open-charge"
  }
}
```

**Lapsed** (capture 61): `state` is `lapsed`, `can_*` are all false, and the person can start a new
checkout (`POST /checkout`, `kind` `plan`). `pay-link` answers 409 `no_subscription` (capture 62). **Trap:**
`hours.seconds_left` can still be above zero (pack hours are kept after a lapse and work for recordings only), and
`pending_payment` can still hold an open renewal charge: if the person pays it late the plan comes back (a late
payment brings a lapsed plan back, from the payment date).

```json
{
  "state": "lapsed",
  "hours": {
    "seconds_left": 36000,
    "holdings_seconds": 36000,
    "warning": null
  },
  "subscription": {
    "id": 1,
    "status": "lapsed",
    "plan_code": "capture_40h",
    "plan_name": "40 hours a month",
    "currency": "USD",
    "amount_minor": 3400,
    "current_period_start": "2026-10-15T08:27:32.280031Z",
    "current_period_end": "2026-11-14T08:27:32.280031Z",
    "cancel_at_period_end": false,
    "pending_plan_code": null,
    "grace_ends_at": null,
    "next_retry_at": null,
    "can_cancel": false,
    "can_resume": false,
    "can_change_plan": false,
    "can_pay_now": false
  },
  "pending_payment": {
    "reference": "bn-7-206192995e94f44b",
    "kind": "renewal",
    "status": "pending",
    "currency": "USD",
    "amount_minor": 3400,
    "created_at": "2026-11-17T16:27:31.967339Z",
    "paid_at": null,
    "pay_url": "https://fake.test/3ds/open-charge"
  }
}
```

### Errors the screens must handle (exact codes and sentences, copied from captures)

| Status | `code` | `message` | When |
|---|---|---|---|
| 404 | `not_found` | `Not found.` | billing is off for this account; also another person's payment (capture 66) |
| 401 | `not_authenticated` | `Log in first.` | not signed in |
| 403 | `csrf_rejected` | `Missing or wrong X-CSRF-Token header. Read it from GET /api/session.` | a write without the token |
| 422 | `billing_country_missing` | `Choose the country you are billed from.` | no billing country chosen |
| 422 | `price_not_available` | `That plan is not on sale in NGN yet.` (tests only, not captured: no seeded plan lacks a price today) | the plan has no price in the person's currency |
| 422 | `validation_error` | `Some fields are missing or not valid.` (with `fields`) | a bad body |
| 404 | `product_not_found` | `That pack is not on sale in your currency.` (tests only, not captured) | a pack code that is not on sale in the plan's currency |
| 404 | `plan_not_found` | `That plan does not exist.` | |
| 409 | `plan_required` | `You need a plan to buy extra hours.` | a pack without a live plan |
| 409 | `already_subscribed` | `You already have a plan.` | |
| 409 | `same_plan` | `You are already on that plan.` | |
| 409 | `nothing_due` | `There is nothing to pay right now.` | pay link inside a paid period |
| 409 | `not_canceled` | `Your plan is not set to end.` | |
| 409 | `payment_overdue` | `A plan cannot be changed while a payment is overdue.` | change or resume in the grace |
| 409 | `no_subscription` | `You do not have a plan.` | cancel, resume, change or pay link without a live plan |

Error codes that exist but were **not** captured (their wording is from the code, listed in `app/billing/subscriptions_api.py`):
409 `not_an_upgrade`, `period_over`, `period_almost_over` (an upgrade whose price rounds to nothing a moment before the
period ends: change plan at the renewal instead), 409 `payment_in_progress` (tests only, not captured: wait a moment and ask again; three causes with three sentences: a saved-card charge is queued and has no page yet, `A payment is already being processed. Wait a moment and check again.`; the page of a first click is still being opened by a double click, `A payment page is being opened. Wait a moment and try again.`; or the provider reports a payment paid that our rules refuse and an admin has been told, `We could not confirm your payment yet. We are looking into it.`), 502 `provider_error` (the provider's page could
not be opened: try again), 503 `provider_unavailable` (no provider is configured for the route).

### Field-nesting and shape traps

* `checkout_url` is at the **top level** of the checkout, pay-link and change-plan answers **and** inside `payment.pay_url`
  (the same text). In a change-plan answer that is `scheduled` or `cleared` both are null (`checkout_url` is present, null).
* `payment.pay_url` is null once the payment is no longer `pending`. Do not store it.
* `subscription` is **null** (present, null) for a person who never had a plan, and otherwise holds their live plan or the
  newest one that ended. `pending_payment` is present and null when nothing waits.
* `hours` here and `usage` in the session payload (the S2 section above) count the same hours; `usage` is only filled under `HOURS_MODE=enforce`.
* `amount_minor` is cents or kobo, a whole number, never a decimal.
* `state` and `subscription.status` are different vocabularies (above).

### Polling

* After the person returns from the provider: `GET /payments/{reference}` every 2 seconds for 30 seconds, then every 10
  seconds for up to 5 minutes, then stop and say "we are still confirming; your hours will appear when the payment is
  confirmed". Stop on any status other than `pending`.
* Refresh `GET /subscription` when the page gains focus, after any write, and every 60 seconds while a `pending_payment`
  or a `past_due` banner is showing.

### What the person must be told (surface these, do not show them silently)

1. **A price can change at renewal.** `subscription.amount_minor` is the next renewal's price. If it differs from what they paid, say so.
2. **No refund of the unused part on cancel**, and access to `current_period_end`.
3. **In the grace, no new hours are added until a payment succeeds**; the hours still shown are what was left of the old month, and they end at `grace_ends_at` if unpaid.
4. **An upgrade is charged the moment they press the button**: show the preview's amount and hours first.
5. **A plan not on sale in their currency** (`price` null, 422 `price_not_available`): say it is not available in their currency yet; do not hide the plan silently.
6. **After a lapse**, new recordings, Ask Beyric, translation and rewriting notes stop (pack hours, if any, still record); notes, transcripts and exports stay.
7. **Never say "paid" because the person came back from the provider.** Only `status: succeeded` means paid.

### The operator's proof commands (for whoever switches billing on)

Not an endpoint: `scripts/billing_test_purchase.py` (SOP, "Switch-on sequence"). Its output after a verified plan payment,
run for real against the capture database (capture 27):

```text
  [ok] a plan payment has succeeded
  [ok] one live subscription, active
  [ok] the subscription is on the plan that payment bought: plan plan_20h, USD via flutterwave
  [ok] exactly one plan-period grant came from that payment
  [ok] the grant has the plan's hours: 72000 s
  [ok] the grant ends with the period: ends 2026-12-17T16:27:28.762167+00:00
  [ok] saved card for renewals: yes
  state now: active, hours left: 20.00
```

### Not verified live

Everything here was run against the scripted provider. **No real Paystack or Flutterwave was called**: the hosted page,
the return address and what it carries, a real saved-card renewal, a real bank's refusal and 3-D Secure answer, the real
time to a webhook, and the raw body through nginx and the Next.js proxy are not verified. Mail: the notices are bell entries
and plain text mail (no link) through the existing alert path; none was sent. The scheduler's pass was run once by the
capture script at the real time after the stored dates were moved by SQL (it cannot wait a month).

### The billing screens built on this contract (stage S8b)

The screens exist now (`web/app/billing`, `docs/sop/2026-11-17-billing-screens.md`). They use only what is above and add **no field and no route**. What they taught about the contract, for whoever builds a second client or changes the API:

* **The return address.** The page reads OUR reference from `reference`, then `trxref`, then `tx_ref`, and falls back to the reference its own tab stored before sending the person away. The names Flutterwave adds are still **not verified**.
* **A payment has five statuses, nothing more.** `pending`, `succeeded`, `failed`, `refunded`, `partly_refunded`. An **abandoned checkout** and a payment the server **refused** (wrong amount, currency or reference: "What a wrong or short payment does") are both `pending` for ever, so a screen cannot tell "we are still waiting" from "this will never be credited". The return page says "still confirming" after 5 minutes and asks the person to keep the reference. If the product wants a better sentence, the payment needs a status or reason for it (**gap, reported**).
* **404 `not_found` from a billing route means "not available to this person"**, not a fault: the Settings card and the banner draw nothing, the billing pages say so plainly. **Except on `GET /payments/{reference}`**, where the same 404 `not_found` also means "no such payment, or not yours" (capture 66): the return page then reads `GET /subscription`, and only if that is a 404 too says billing is not available; otherwise it says it could not find that payment. The banner remembers a 404 for the browser tab, and a healthy answer for a minute, so it does not ask on every page (both are forgotten when this client signs someone in or out).
* **A charge waiting for the bank is a payment with its own page**: the banner and the billing page send the person to `pending_payment.pay_url` when `pending_payment.kind` is `renewal`, and never call `POST /subscription/pay-link` for it.
* **`hours.warning` drives the words**, not the bar's ratio; **`can_*` drive the buttons**.
* **A pay link asked while a saved-card charge is in flight** is 409 `payment_in_progress` (S8a review fix): the banner and the billing page show the server's sentence and stay where they are.
* **The brand and last four digits of the card** are still null, so no screen names the card.
* The billing routes are **not in `docs/openapi.json`**; the screens' types (`web/lib/billing.ts`) are written from this guide's captures.

## More plans: the 20-hour plan, Pro and Scale (migration 0054)

**What it is.** Three monthly plans now exist, all read from the database by the routes already described above; **no route, field or error is new**. In `GET /api/billing/plans` they come in `sort_order`: the first plan (`plan_20h`, name "20 hours a month"), **Pro** (`plan_50h`, 50 hours) and **Scale** (`plan_100h`, 100 hours), then the pack. The names are data (`plans.name`): show `name` as it comes, never a name typed in the screens. Captured by `scripts/capture_billing_plans.py` against the real migrations (the plans and prices are the seeded rows) with a scripted payment provider, over real HTTP: `docs/fe-captures/billing-plans/`.

| Plan | `code` | `monthly_seconds` | USD (`amount_minor`, cents) | NGN (kobo) |
|---|---|---|---|---|
| 20 hours a month | `plan_20h` | 72000 | 1800 | 2,500,000 |
| Pro | `plan_50h` | 180000 | 4000 | 5,600,000 |
| Scale | `plan_100h` | 360000 | 8000 | 11,200,000 |

**Rules the screens already follow and that matter more now:**

* `/billing/plans` lists every plan returned (three), each with its own **Choose this plan**: `POST /api/billing/checkout` with `{"kind": "plan", "code": <that plan's code>}`. Proven in a browser: Scale in dollars made a payment of 8000 USD cents and Pro in naira 5,600,000 kobo.
* A change between any two plans is the same two calls: `GET .../change-plan/preview?plan_code=` then `POST .../change-plan`. **More hours is an upgrade** (charged now, prorated), **fewer hours is a downgrade** (applied at the renewal): Starter to Pro, Starter to Scale, Pro to Scale are upgrades; Scale to Pro, Scale to Starter, Pro to Starter are downgrades. The server decides; the screens never compare prices.
* A downgrade shows `amount_minor` = the smaller plan's monthly price on the subscription (what the renewal charges), while `plan_code` is still the bigger plan until the renewal (capture 23).
* After an upgrade the person's last plan payment is a `kind: "upgrade"` (the prorated difference): the billing page must not say "the price changed" for it (fixed in this change: it compares only with a plan or renewal payment, and says nothing when the latest is an upgrade).

### The catalog, in dollars and in naira

Capture `04-plans-us` (GET /api/billing/plans, status 200):

```json
{
  "route": {
    "currency": "USD",
    "provider": "flutterwave"
  },
  "plans": [
    {
      "code": "plan_20h",
      "name": "20 hours a month",
      "monthly_seconds": 72000,
      "price": {
        "currency": "USD",
        "amount_minor": 1800
      }
    },
    {
      "code": "plan_50h",
      "name": "Pro",
      "monthly_seconds": 180000,
      "price": {
        "currency": "USD",
        "amount_minor": 4000
      }
    },
    {
      "code": "plan_100h",
      "name": "Scale",
      "monthly_seconds": 360000,
      "price": {
        "currency": "USD",
        "amount_minor": 8000
      }
    }
  ],
  "packs": [
    {
      "code": "pack_10h",
      "name": "10 extra hours",
      "seconds": 36000,
      "valid_months": 12,
      "price": {
        "currency": "USD",
        "amount_minor": 1000
      }
    }
  ]
}
```

Capture `08-plans-ng` (GET /api/billing/plans, status 200):

```json
{
  "route": {
    "currency": "NGN",
    "provider": "paystack"
  },
  "plans": [
    {
      "code": "plan_20h",
      "name": "20 hours a month",
      "monthly_seconds": 72000,
      "price": {
        "currency": "NGN",
        "amount_minor": 2500000
      }
    },
    {
      "code": "plan_50h",
      "name": "Pro",
      "monthly_seconds": 180000,
      "price": {
        "currency": "NGN",
        "amount_minor": 5600000
      }
    },
    {
      "code": "plan_100h",
      "name": "Scale",
      "monthly_seconds": 360000,
      "price": {
        "currency": "NGN",
        "amount_minor": 11200000
      }
    }
  ],
  "packs": [
    {
      "code": "pack_10h",
      "name": "10 extra hours",
      "seconds": 36000,
      "valid_months": 12,
      "price": {
        "currency": "NGN",
        "amount_minor": 1500000
      }
    }
  ]
}
```

### Upgrade previews (a read: nothing is written)

The clock ran during the capture, so the amounts are a second or two short of the whole-month difference (Pro 4000 - 1800 = 2200 cents; 50 - 20 = 30 hours = 108000 seconds).

Capture `12-preview-starter-to-pro` (GET /api/billing/subscription/change-plan/preview?plan_code=plan_50h, status 200):

```json
{
  "kind": "upgrade",
  "plan_code": "plan_50h",
  "currency": "USD",
  "amount_minor": 2199,
  "extra_seconds": 107640,
  "takes_effect": "now"
}
```

Capture `13-preview-starter-to-scale` (GET /api/billing/subscription/change-plan/preview?plan_code=plan_100h, status 200):

```json
{
  "kind": "upgrade",
  "plan_code": "plan_100h",
  "currency": "USD",
  "amount_minor": 6199,
  "extra_seconds": 287640,
  "takes_effect": "now"
}
```

Capture `17-preview-pro-to-scale` (GET /api/billing/subscription/change-plan/preview?plan_code=plan_100h, status 200):

```json
{
  "kind": "upgrade",
  "plan_code": "plan_100h",
  "currency": "USD",
  "amount_minor": 3999,
  "extra_seconds": 179640,
  "takes_effect": "now"
}
```

Capture `39-preview-ng-pro-to-scale` (GET /api/billing/subscription/change-plan/preview?plan_code=plan_100h, status 200):

```json
{
  "kind": "upgrade",
  "plan_code": "plan_100h",
  "currency": "NGN",
  "amount_minor": 5599997,
  "extra_seconds": 179640,
  "takes_effect": "now"
}
```

### Downgrade preview, scheduling it, taking it back

Capture `21-preview-scale-to-starter` (GET /api/billing/subscription/change-plan/preview?plan_code=plan_20h, status 200):

```json
{
  "kind": "downgrade",
  "plan_code": "plan_20h",
  "currency": "USD",
  "amount_minor": 1800,
  "extra_seconds": null,
  "takes_effect": "next_renewal"
}
```

Capture `22-change-scale-to-starter` (POST /api/billing/subscription/change-plan, status 200):

```json
{
  "result": "scheduled",
  "payment": null,
  "checkout_url": null,
  "subscription": {
    "plan_code": "plan_100h",
    "plan_name": "Scale",
    "amount_minor": 1800,
    "pending_plan_code": "plan_20h",
    "status": "active"
  }
}
```

Capture `23-subscription-downgrade-waiting` (GET /api/billing/subscription, status 200):

```json
{
  "state": "active",
  "hours": {
    "seconds_left": 359280,
    "holdings_seconds": 359280,
    "warning": null
  },
  "pending_payment": null,
  "subscription": {
    "id": 1,
    "status": "active",
    "plan_code": "plan_100h",
    "plan_name": "Scale",
    "currency": "USD",
    "amount_minor": 1800,
    "cancel_at_period_end": false,
    "pending_plan_code": "plan_20h",
    "can_change_plan": true
  }
}
```

Capture `24-change-back-to-scale` (POST /api/billing/subscription/change-plan, status 200):

```json
{
  "result": "cleared",
  "payment": null,
  "checkout_url": null,
  "subscription": {
    "plan_code": "plan_100h",
    "plan_name": "Scale",
    "amount_minor": 8000,
    "pending_plan_code": null,
    "status": "active"
  }
}
```

### The renewal applies the downgrade (clock moved by SQL)

Capture `25-change-scale-to-pro` (POST /api/billing/subscription/change-plan, status 200):

```json
{
  "result": "scheduled",
  "payment": null,
  "checkout_url": null,
  "subscription": {
    "plan_code": "plan_100h",
    "plan_name": "Scale",
    "amount_minor": 4000,
    "pending_plan_code": "plan_50h",
    "status": "active"
  }
}
```

Capture `26-subscription-after-renewal` (GET /api/billing/subscription, status 200):

```json
{
  "state": "active",
  "hours": {
    "seconds_left": 180000,
    "holdings_seconds": 180000,
    "warning": null
  },
  "pending_payment": null,
  "subscription": {
    "id": 1,
    "status": "active",
    "plan_code": "plan_50h",
    "plan_name": "Pro",
    "currency": "USD",
    "amount_minor": 4000,
    "cancel_at_period_end": false,
    "pending_plan_code": null,
    "can_change_plan": true
  }
}
```

Trimmed in the blocks above: the subscription objects keep only `id`, `status`, `plan_code`, `plan_name`, `currency`, `amount_minor`, `cancel_at_period_end`, `pending_plan_code`, `can_change_plan` (the rest of the shape is in "Subscriptions, packs and the lapse"); nothing else was changed.

### What was proven in a browser (not only over HTTP)

With the real app, the real Next.js screens and the scripted provider (`scripts/dev_billing_stack.py`; its dev-only second plan is gone, the real Pro and Scale rows are used): **Billing** as a person on the 20-hour plan: "See what changes" on Pro showed "You pay $21.99 today ... 29 hours 54 minutes extra", **Confirm and pay** went to the provider page, the payment went through, the return page and Billing showed Pro; then Pro to Scale the same way ($39.99); then Scale to the 20-hour plan showed "Nothing is charged now. You keep Scale and your hours until <date>. Then you move to 20 hours a month at $18.00 a month." and **Move to 20 hours a month at my next renewal** scheduled it ("You move to 20 hours a month on <date>") with **Keep Scale** to take it back. The database rows afterwards: plan payment 1800, upgrade 2199, upgrade 3999.

**Not verified live:** a real provider's page for any of these amounts, and a naira upgrade in the browser (the naira preview is over HTTP only: capture 39).

## Where billing is reachable from the signed-in app (2026-11-17): no new payload

No endpoint, field or error is new. Every entry point reads the answer of `GET /api/billing/subscription` already captured above (`docs/fe-captures/billing-subscriptions/09` never had a plan, `26` active, `50` grace, `61` lapsed) and nothing else:

| Entry point | Reads | Rule |
|---|---|---|
| **Billing** link (Configure) and the sidebar **hours card** | `state`, `hours.seconds_left`, `hours.holdings_seconds`, `hours.warning`, `subscription.status`, `subscription.plan_name` | drawn only on a 200; a 404 `not_found` draws nothing and is remembered for the tab; any other failure draws nothing this time |
| Where the card leads | `subscription.status` | `active` or `past_due`: `/billing`; anything else (`subscription` null, `lapsed`, `canceled`, `ended`, a trial or pilot with no plan): `/billing/plans` |
| Card text | `hours` | `holdings_seconds` 0: "No recorded hours"; `warning` `out` or nothing left: "Out of hours" (and "Add hours" with a live plan); else "<hours> left" with the bar; `seconds_left` or `holdings_seconds` null (no limit): no card |
| Notification links | the notice's `kind` (`usage.hours_low`, `usage.hours_out`, `pilot.expiry`) | the same rule for where it leads; a notice about a meeting still links to the meeting |
| "Not recorded" hours link | the calendar's `reason_text` | only for the two sentences "Your recording hours are used up." and "You have no recording hours at the moment." (pinned to `app/calendar_text.py` by a test) |

Trap: the card's `state` words are the **person's** state (`trial`, `pilot`, `none`, ...), not the subscription's `status`; a person in a trial has `subscription: null`. Verified live against the local app with the scripted provider (trial, no plan, active, lapsed, 90% used, all used); the "Not recorded" link only by tests, with the server's real sentence.

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
| `trial` state and its grant, made by confirming an email through the real sign-up (real web and scheduler processes) | live (`docs/fe-captures/trial/` 08 to 10, 16, 23) |
| The scheduler's sweep giving an account made before the switch-on its trial; the 80% and 100% bell entries and mails; one pilot notice per person and no repeat | live (trial captures 20, 23, 27 and `mail-sink.txt`; `checks.txt`) |
| Exactly 3 hours used, `no_hours` refusal; shadow keeps the new fields null; recordings made in shadow come off the trial at `enforce` | live (trial captures 13, 16, 17, 18, 19) |
| A trial given by Google sign-in (a new Google account, or Google confirming a password account) | tests only (`tests/test_trial.py`); the same function as the password link |
| The 14th day of a trial, the budget window, deleting an account and signing up again | tests only (fake clock, `tests/test_trial.py`); no account-delete route to capture |
| The pilot's end: the job, the one mail and bell entry, the grant given its end | live (trial captures 24, 27; the scheduler's own job on a real clock with the notice window open) |
| The session and refusal after the pilot end | live shape: the job ran in the capture process with an injected clock, then the app answered with its real clock after the end (trial captures 29, 32, 33) |
| `plan_period` state | live shape only: the grant was inserted by SQL because nothing creates it yet |
| 403 `plan_inactive` body for Ask, the free "my tasks" answer, a new translation, a rewrite and a share in French | live (captures 03 to 07, `FEATURE_GATES_MODE=enforce`) |
| Reading, the Ask scope line, Review and the meeting list stay open for a person with no plan | live (captures 08 to 11) |
| A person on a trial passes all gates (Ask 200, translation 202, rewrite 200) | live (captures 14 to 18; the trial grant was inserted by SQL; the notes model was a fake client and Ask was shown on a question that finds nothing, so no OpenAI call was made) |
| Shadow lets the call through and logs one line with ids only; off logs nothing | live (captures 19 to 23: the lines come from the real logger, with a handler the capture script attached itself; that web's own logging prints them is proved by `tests/test_logging_setup.py`, a fresh interpreter, not by the capture) |
| A failed or stale translation asked for again is refused (and left readable); a team admin is checked on their own plan | tests only (`tests/test_feature_gates.py`) |
| A cached or running translation is not refused after a plan ends; the worker finishes a queued translation; the legacy page route `POST /meetings/{id}/regenerate` is refused with 403 and the same sentence | tests only (`tests/test_feature_gates.py`) |
| The reader-only teammate: reads shared notes, Ask refused, Ask works once they hold a plan | tests only |
| `past_due` and `lapsed` states against the gates | tests only (`tests/test_billing_subscriptions.py`: `past_due` keeps Ask, translation and rewriting, `lapsed` closes them); the states themselves are live in the S8a captures |
| Export, decisions and the Review spelling step stay open | export and decisions: tests only (`tests/test_feature_gates.py`); Review list: live (capture 11); the spelling step: tests only. Search: not exercised in this stage (its code is untouched) |
| A `features` signal in the session payload | not built (see "What the frontend must do") |
| The webhook route's answers: 404 off, 200 paid, 200 replay, 401 bad signature, stale timestamp and no header, 200 `ignored`, 503 on a float amount then 200 on the retry with two signatures, 200 refused on a low amount | live over HTTP (`docs/fe-captures/paystack-webhook/`, 15 files), **with Paystack's side scripted from documented samples, not a real Paystack** |
| A `400` for a genuine but unreadable event | tests only |
| The checkout request and answer shape, saved-card charge, refund, verify | documented samples and unit tests only: **not verified live** (no Paystack test keys yet) |
| A real Paystack test-mode payment, the raw body through nginx and the Next.js proxy, what a foreign card returns in `country_code`, that real events match the documented samples | **not verified live**: the runbook in `docs/sop/2026-11-17-billing-paystack-adapter.md` |
| S7: the Flutterwave adapter: hosted checkout, verify, the `verif-hash` check, saved-card charge, refund | tests only, against a scripted fake Flutterwave built from the vendor's documented samples (`tests/test_billing_flutterwave.py`); **not verified live**: no Flutterwave key was available |
| S7: a USD payment is accepted by our Flutterwave account, and the currency and amounts it is settled in | **not verified (spec 5.6 stays unproven)**; the steps are in the S7 SOP |
| S7: decimal dollar amounts (9.99), `amount` against `charged_amount`, the card country of a foreign card, the 3D Secure answer of a saved-card charge, the refund answer | not verified live (documented only) |
| S7: the webhook reaching the route with `verif-hash` intact through nginx and the Next.js proxy | not verified live |
| Subscription routes dark with billing off (404 `not_found`), a visitor (401), a write without the token (403) | live over HTTP (`docs/fe-captures/billing-subscriptions/`, captures 03 to 15) |
| `GET /plans`: dollars, no country, naira with no price | live over HTTP, real migrated seed (captures 12, 08, 69), provider scripted |
| Checkout, pending payment, the return page's poll, the verified payment granting hours once, a wrong amount granting nothing, a replay granting once, a bad signature (401) | live over HTTP against the real route and settlement rules, **provider scripted, signature header is the fake's** (captures 17 to 26) |
| A pack, cancel, resume, the upgrade preview and checkout, the downgrade, taking a downgrade back | live over HTTP, provider scripted (captures 33 to 47) |
| `past_due` after a failed saved-card renewal, no new hours in the grace, change refused, pay link (the same page twice), a pack in the grace, a verified payment renewing from the payment date | live: HTTP plus **one real scheduler pass run by the capture script after the stored dates were moved by SQL**, provider scripted (captures 49 to 56) |
| A 3-D Secure charge keeps its own page, and the pay link returns the same address | live shape only: the open charge is the scripted provider's (captures 57, 58); the pay link asks the provider about the open charge first and keeps its page however old it is (tests); **a real bank's answer is not verified** |
| `lapsed` before and after the scheduler's pass, pay link refused | live: dates moved by SQL (captures 59 to 62) |
| The billing history route (own payments only, newest first) | live over HTTP (captures 63, 67) |
| The runbook script's `proof`, `state` and `preflight 1` | run for real against the capture database (captures 27 to 29); **not yet on the real server** |
| Another person's payment is a 404 | live over HTTP (capture 66) |
| A naira purchase end to end (seeded NGN 25,000, route `paystack`), a naira pack bought on top | live over HTTP, provider scripted (captures 69, 70 to 75) |
| `409 not_an_upgrade`, `period_over`, `period_almost_over`, `payment_in_progress`, 502 `provider_error`, 503 `provider_unavailable` | tests only (`tests/test_billing_subscriptions.py`, `tests/test_billing_subscriptions_api.py`) |
| Mail for the notices, and the notices' wording on screen | tests only (bell entries), mail not sent |
| A real provider's hosted page, return address, saved-card renewal, bank refusal, webhook timing; the raw body through nginx and the proxy | **not verified live**: the runbook in `docs/sop/2026-11-17-billing-subscriptions.md` is the test |
| S8b: the screens (plans, return page, billing page, banner, Settings card, gate links) | driven in a browser against a local app with the scripted provider (`scripts/dev_billing_stack.py`) in 13 account states at 1440, 768 and 390 px, and by vitest on the captured bodies; **not** against a real provider's page or return address |
| S8b: the return page's 5-minute "still confirming" | tests with fake timers and a browser run with the clock fast-forwarded; not waited in real time |
| S8b: "abandoned" and "mismatch" as states of a payment | **not states of the API** (both are `pending`); the screens use the provider's `status=cancelled` in the return address as a hint for the words only |
| Three plans in `GET /plans` (USD and NGN), each plan's own checkout, upgrades between all three, downgrades scheduled and taken back, a downgrade applied by the renewal | live over HTTP, provider scripted (`docs/fe-captures/billing-plans/`, captures 04 to 39) |
| The same in the screens: plans list, per-plan checkout (Scale in dollars, Pro in naira), Starter to Pro and Pro to Scale paid through the scripted provider page, Scale to the 20-hour plan scheduled | driven in a browser against the local app (`scripts/dev_billing_stack.py`), 2026-11-18; rows checked in the database |
| A naira upgrade or downgrade in the browser; any of this on a real provider's page | **not verified live** (naira preview over HTTP only, capture 39) |
