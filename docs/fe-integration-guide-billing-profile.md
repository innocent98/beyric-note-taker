# FE integration guide: billing profile and billing country (billing stage S5)

Every request and response body below is pasted from `docs/fe-captures/billing-profile/` (made by
`scripts/capture_billing_profile.py`: real migrations, the real app under uvicorn, real HTTP, three copies of
the app on one scratch database). Anything that could **not** be exercised live is marked **NOT LIVE** inline
with the reason, and the verification table at the end says how far to trust each section. The people,
addresses and company names in the captures are placeholders; the times are the capture machine's real clock
(UTC, as the API always sends them).

## What this is, in the person's terms

Before anyone can pay (the pay screens come with stage S8) the person tells us **who is billed and from which
country**. The country decides what they pay in and through whom:

| Billing country | Prices in | Paid through |
|---|---|---|
| Nigeria (`NG`) | naira (`NGN`) | Paystack (`paystack`) |
| any other country | US dollars (`USD`) | Flutterwave (`flutterwave`) |

(That table is the server's default. It is data on the server, so **never hard-code it: read `route` from the
profile and from the preview route below.**) The country is **chosen by the person from a list, never guessed**
from their address, browser or card. Only the person changes their own; there is no route for anyone else's.

**Nothing here is switched on yet.** All four routes answer a plain 404 while the server runs with
`BILLING_ENABLED` off (the default), and also for every account the operator has not allow-listed (the list is
closed by default; the operator can open it to everyone). The FE should treat a 404 `not_found` from any of them as "billing is not available to this person"
and show nothing, not an error. No screen can use these routes until the owner switches billing on for an
account.

There is no payment, plan or price on these routes: the profile is the first thing S8's checkout will read.

## The profile object

Returned by `GET /api/billing/profile`, by `PUT /api/billing/profile`, and inside `PUT /api/billing/profile/country`
(under `profile`). Always all nine keys, present-but-null, never absent.

| Key | Meaning | Null when |
|---|---|---|
| `name_or_company` | who is billed (at most 200 characters, one line) | not saved |
| `address` | the billing address (at most 500 characters, line breaks kept) | not saved |
| `tax_id` | the person's tax number (at most 64 characters, one line) | not saved |
| `billing_email` | where billing mail goes (plain address, lower-cased by the server) | not saved |
| `billing_country` | two-letter ISO country code the person chose, upper case | not chosen yet |
| `pending_billing_country` | a change that waits for the end of a paid period | nothing is waiting (always, until subscriptions exist) |
| `pending_effective_at` | when the waiting change takes effect | nothing is waiting |
| `route` | `{currency, provider}` that `billing_country` pays with | no country chosen, or none can be routed |
| `country_change_allowed_at` | the next moment the person may change country again (30 days after their last change) | a change is allowed now |

### Field-nesting traps

* `route` is **nested** (`route.currency`, `route.provider`) in the profile. The preview route below returns
  the same two names **flat**, beside `billing_country`. Same words, different level.
* `PUT /api/billing/profile/country` wraps the profile: `{"result": ..., "profile": {...}}`. `PUT /api/billing/profile`
  and `GET` return the profile itself, unwrapped.
* `billing_country`, `route` and `country_change_allowed_at` describe the **old** country while a change is
  waiting (`pending_*` set). The new country appears there only when it takes effect.

## `GET /api/billing/profile`

Auth: signed in. A person who has saved nothing gets every field null. **Reading creates nothing.**

```
GET /api/billing/profile
```

```json
// 200  (capture 08-profile-empty.json)
{
  "name_or_company": null,
  "address": null,
  "tax_id": null,
  "billing_email": null,
  "billing_country": null,
  "pending_billing_country": null,
  "pending_effective_at": null,
  "route": null,
  "country_change_allowed_at": null
}
```

## `GET /api/billing/route?country=NG`

Auth: signed in. What a country would pay with, so the screen can state it **before** the person commits
("Prices in naira, paid through Paystack" / "Prices in US dollars, paid through Flutterwave"). Stores nothing.
The code may be upper or lower case.

```
GET /api/billing/route?country=NG
```

```json
// 200  (capture 09-route-ng.json)
{
  "currency": "NGN",
  "provider": "paystack",
  "billing_country": "NG"
}
```

Another country (the request was `country=gb`, the answer is upper-cased):

```
GET /api/billing/route?country=gb
```

```json
// 200  (capture 10-route-gb.json)
{
  "currency": "USD",
  "provider": "flutterwave",
  "billing_country": "GB"
}
```

No country, and a name instead of a code:

```
GET /api/billing/route
```

```json
// 422  (capture 11-route-missing.json)
{
  "error": {
    "code": "billing_country_missing",
    "message": "Choose the country you are billed from."
  }
}
```

```
GET /api/billing/route?country=Nigeria
```

```json
// 422  (capture 12-route-name.json)
{
  "error": {
    "code": "billing_country_invalid",
    "message": "That is not a country code we know. Pick one from the list."
  }
}
```

## `PUT /api/billing/profile`

Auth: signed in, CSRF header. **Replaces all four details**: a field that is omitted, null, empty or only
spaces is cleared (send every field the person still wants kept). Text is trimmed and the email lower-cased.
This route never sets or changes the country (the country has its own route and its own rules), and a body
with a `billing_country` key is refused.

```
PUT /api/billing/profile
{
  "name_or_company": "  Placeholder Company Ltd ",
  "address": "1 Placeholder Road\nLagos",
  "tax_id": "TIN-PLACEHOLDER-0001",
  "billing_email": " Billing@Example.TEST "
}
```

```json
// 200  (capture 13-profile-save.json)
{
  "name_or_company": "Placeholder Company Ltd",
  "address": "1 Placeholder Road\nLagos",
  "tax_id": "TIN-PLACEHOLDER-0001",
  "billing_email": "billing@example.test",
  "billing_country": null,
  "pending_billing_country": null,
  "pending_effective_at": null,
  "route": null,
  "country_change_allowed_at": null
}
```

A bad billing email:

```
PUT /api/billing/profile
{
  "billing_email": "not-an-address"
}
```

```json
// 422  (capture 14-profile-save-bad-email.json)
{
  "error": {
    "code": "billing_email_invalid",
    "message": "That is not an email address we can send to."
  }
}
```

Too long (the `fields` list names the field; these `message` strings are the server's, in English):

```
PUT /api/billing/profile
{
  "tax_id": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
}
```

```json
// 422  (capture 15-profile-save-too-long.json)
{
  "error": {
    "code": "validation_error",
    "message": "Some fields are missing or not valid.",
    "fields": [
      {
        "field": "tax_id",
        "message": "String should have at most 64 characters"
      }
    ]
  }
}
```

A `billing_country` key on this route (also what any unknown key gets):

```
PUT /api/billing/profile
{
  "billing_country": "NG"
}
```

```json
// 422  (capture 16-profile-save-with-country.json)
{
  "error": {
    "code": "validation_error",
    "message": "Some fields are missing or not valid.",
    "fields": [
      {
        "field": "billing_country",
        "message": "Extra inputs are not permitted"
      }
    ]
  }
}
```

No CSRF header (any write without the `X-CSRF-Token` header gets this):

```
PUT /api/billing/profile
{}
```

```json
// 403  (capture 17-profile-save-no-csrf.json)
{
  "error": {
    "code": "csrf_rejected",
    "message": "Missing or wrong X-CSRF-Token header. Read it from GET /api/session."
  }
}
```

Other `validation_error` cases, covered by tests, not captured: a name or tax id with a line break or any
control character, a non-string value, and an email over 254 characters.

## `PUT /api/billing/profile/country`

Auth: signed in, CSRF header. Body: `{"billing_country": "NG"}`: a two-letter ISO 3166-1 code, any case. Always
send a code from the list the screen offers, never a typed name. The answer is 200 with a `result`:

| `result` | Meaning |
|---|---|
| `applied` | the country is now this one; the next checkout uses its currency and provider |
| `scheduled` | **NOT LIVE** (see below) the change waits for the end of the paid period; `profile.pending_*` say what and when |
| `unchanged` | it is already this country; nothing was written |

The first choice (not counted as a change for the 30-day rule, so a wrong first pick can be fixed once straight away):

```
PUT /api/billing/profile/country
{
  "billing_country": "ng"
}
```

```json
// 200  (capture 18-country-first.json)
{
  "result": "applied",
  "profile": {
    "name_or_company": "Placeholder Company Ltd",
    "address": "1 Placeholder Road\nLagos",
    "tax_id": "TIN-PLACEHOLDER-0001",
    "billing_email": "billing@example.test",
    "billing_country": "NG",
    "pending_billing_country": null,
    "pending_effective_at": null,
    "route": {
      "currency": "NGN",
      "provider": "paystack"
    },
    "country_change_allowed_at": null
  }
}
```

The same country again:

```
PUT /api/billing/profile/country
{
  "billing_country": "NG"
}
```

```json
// 200  (capture 19-country-same.json)
{
  "result": "unchanged",
  "profile": {
    "name_or_company": "Placeholder Company Ltd",
    "address": "1 Placeholder Road\nLagos",
    "tax_id": "TIN-PLACEHOLDER-0001",
    "billing_email": "billing@example.test",
    "billing_country": "NG",
    "pending_billing_country": null,
    "pending_effective_at": null,
    "route": {
      "currency": "NGN",
      "provider": "paystack"
    },
    "country_change_allowed_at": null
  }
}
```

A second, real change with no paid subscription applies at once. `country_change_allowed_at` now shows when
the next one is allowed:

```
PUT /api/billing/profile/country
{
  "billing_country": "GB"
}
```

```json
// 200  (capture 22-country-second.json)
{
  "result": "applied",
  "profile": {
    "name_or_company": "Placeholder Company Ltd",
    "address": "1 Placeholder Road\nLagos",
    "tax_id": "TIN-PLACEHOLDER-0001",
    "billing_email": "billing@example.test",
    "billing_country": "GB",
    "pending_billing_country": null,
    "pending_effective_at": null,
    "route": {
      "currency": "USD",
      "provider": "flutterwave"
    },
    "country_change_allowed_at": "2026-12-16T22:02:15.248161Z"
  }
}
```

### Errors

The country is empty:

```
PUT /api/billing/profile/country
{
  "billing_country": ""
}
```

```json
// 422  (capture 20-country-empty.json)
{
  "error": {
    "code": "billing_country_missing",
    "message": "Choose the country you are billed from."
  }
}
```

Not a country code (`UK` is not one; the United Kingdom is `GB`):

```
PUT /api/billing/profile/country
{
  "billing_country": "UK"
}
```

```json
// 422  (capture 21-country-not-a-code.json)
{
  "error": {
    "code": "billing_country_invalid",
    "message": "That is not a country code we know. Pick one from the list."
  }
}
```

Another change inside 30 days (the refusal writes nothing; read `country_change_allowed_at` from the profile to
tell the person when):

```
PUT /api/billing/profile/country
{
  "billing_country": "FR"
}
```

```json
// 409  (capture 23-country-too-soon.json)
{
  "error": {
    "code": "country_change_too_soon",
    "message": "You can change your billing country once every 30 days."
  }
}
```

A country that the server has no route for, when an operator has switched one provider off (captured on a
server started with `BILLING_COUNTRY_ROUTES=NG=NGN:paystack`):

```
PUT /api/billing/profile/country
{
  "billing_country": "US"
}
```

```json
// 422  (capture 33-narrow-unsupported.json)
{
  "error": {
    "code": "billing_country_unsupported",
    "message": "We cannot take payments from that country yet."
  }
}
```

Also covered by tests, not captured: `validation_error` (422) for a missing `billing_country` key, or a value
that is not a string.

### Scheduled changes (**NOT LIVE**)

When a person who has a **live paid subscription** moves between Nigeria and elsewhere, the change cannot take
effect at once (a saved card belongs to one provider): it waits for the end of the paid period. **No
subscription can exist before stage S8, so no request can make this happen yet, and the shapes below are not
from a live journey.** The waiting state in the capture was written into the profile row by SQL; the read and
the refusal that follow are real HTTP against that row.

```
GET /api/billing/profile
```

```json
// 200  (capture 26-pending-profile.json)
{
  "name_or_company": "Placeholder Company Ltd",
  "address": "1 Placeholder Road\nLagos",
  "tax_id": "TIN-PLACEHOLDER-0001",
  "billing_email": "billing@example.test",
  "billing_country": "GB",
  "pending_billing_country": "NG",
  "pending_effective_at": "2026-11-28T22:02:15.275977Z",
  "route": {
    "currency": "USD",
    "provider": "flutterwave"
  },
  "country_change_allowed_at": "2026-12-16T22:02:15.248161Z"
}
```

While a change waits, any further change (even back to the current country) is refused:

```
PUT /api/billing/profile/country
{
  "billing_country": "US"
}
```

```json
// 409  (capture 27-pending-change-refused.json)
{
  "error": {
    "code": "country_change_pending",
    "message": "A change of billing country is already waiting for the end of your paid period."
  }
}
```

When the period ends, the next `GET /api/billing/profile` already shows the change as applied (`billing_country`
is the pending one and the `pending_*` keys are null). The read writes nothing; the database row is updated by
the person's next change or by the renewal. Proved by tests (`tests/test_billing_profile.py`), not captured.

## Every error this area returns

| Status | `code` | When | Message (as returned) |
|---|---|---|---|
| 401 | `not_authenticated` | not signed in | `Log in first.` |
| 403 | `csrf_rejected` | a write without the CSRF header | `Missing or wrong X-CSRF-Token header. Read it from GET /api/session.` |
| 404 | `not_found` | billing is off, or the account is not allowed (the allow-list is closed by default) | `Not found.` |
| 422 | `billing_country_missing` | the country is empty | `Choose the country you are billed from.` |
| 422 | `billing_country_invalid` | not a country code | `That is not a country code we know. Pick one from the list.` |
| 422 | `billing_country_unsupported` | a real country with no route | `We cannot take payments from that country yet.` |
| 422 | `billing_email_invalid` | the billing email is not a plain address | `That is not an email address we can send to.` |
| 422 | `validation_error` | a field is missing, too long, unknown or of the wrong type | `Some fields are missing or not valid.` (with `fields`) |
| 409 | `country_change_too_soon` | a change within 30 days of the last one | `You can change your billing country once every 30 days.` |
| 409 | `country_change_pending` | a change is already waiting for the end of a period | `A change of billing country is already waiting for the end of your paid period.` |

The 401 and 404 bodies (the 404 is the same whether billing is off or the account is not allowed):

```json
// 401  (capture 05-visitor-profile.json)
{
  "error": {
    "code": "not_authenticated",
    "message": "Log in first."
  }
}
```

```json
// 404  (capture 03-off-profile.json)
{
  "error": {
    "code": "not_found",
    "message": "Not found."
  }
}
```

## UX consequences the FE must surface

* **State the result before the person commits.** Use `GET /api/billing/route?country=XX` as the country
  select changes and show the currency and provider in words; the person chooses a country knowing what it
  means. Do not name a provider unless you read it from `route`.
* **Tell the person the country is where they are established.** The country decides naira or dollars; the
  Terms will say it must be where the customer is established, and a card from another country is flagged for
  a human look (never refused). Do not call it "where you live today" or offer to detect it.
* **A change is rate-limited, say so before they try.** When `country_change_allowed_at` is not null, disable
  the change control and show the date; the 409 `country_change_too_soon` is the backstop, not the plan.
* **A wrong first choice can be fixed once, straight away,** because the first choice does not count. After
  that, one change in 30 days.
* **While `pending_billing_country` is set,** show that the change is waiting and when (`pending_effective_at`),
  keep showing the old route, and offer no change control. **There is no way to cancel a waiting change:**
  even a request for the current country gets 409 `country_change_pending`, so do not offer "undo".
  (Never produced before stage S8.)
* **A 404 on any of these means "not available to this person"** (billing off, or not allowed), not
  a fault: hide the billing profile section.
* **Billing email is not the sign-in email.** It may be empty; mail about payments will use it when set.

## State machine and polling

There is nothing to poll. The profile is read when a billing screen opens and after each write. The only
state that changes without the person doing anything is a scheduled change taking effect at the end of a
paid period (stage S8); re-read the profile whenever the billing screen is opened.

```
no country --PUT country--> applied (first choice, free)
applied --PUT other country, no live subscription or same currency--> applied (30-day clock starts)
applied --PUT other country, live subscription, Nigeria <-> elsewhere--> pending (NOT LIVE until S8)
pending --end of the paid period--> applied     (any PUT meanwhile: 409 country_change_pending)
```

## What the card's country does (no screen yet)

When a payment is settled, the server records the country of the card the provider reports (`card_country`)
and flags `country_mismatch` when it differs from the billing country. It never refuses, reverses or delays
the payment. **There is no route that returns these fields yet**, so there is nothing for the FE to build
against; the plain note to the person ("Your card was issued in X; your billing country is Y"), the admin list
and the mail after two mismatches in a row are later work. It is proved by tests through the webhook route
with a fake provider, not by a live capture.

## Verification

| Behaviour | Verified |
|---|---|
| Empty profile shape; reading creates nothing | live (capture 08; row count in `tests/test_billing_profile.py`) |
| Save details: trimming, email lower-cased, replace semantics | live (capture 13) for trimming and email; the "omitted field is cleared" rule by tests only |
| 422 `billing_email_invalid`, `validation_error` (too long, unknown key) | live (captures 14, 15, 16); control characters and wrong types by tests only |
| Route preview for `NG`, `gb`, missing, a name | live (captures 09 to 12) |
| First choice applied, same country unchanged, second change applied, `country_change_allowed_at` | live (captures 18, 19, 22, 24) |
| 409 `country_change_too_soon`, and that it writes nothing | live (capture 23; history rows in capture 25) |
| 422 `billing_country_missing`, `billing_country_invalid` | live (captures 20, 21) |
| 422 `billing_country_unsupported` (a provider switched off) | live (capture 33) |
| 404 with billing off, with an account off the allow-list, and with billing on but the allow-list empty (closed by default) | live (captures 03, 04, 36, 39) |
| 401 for a visitor, 403 without CSRF | live (captures 05, 17) |
| One person never sees or changes another's profile | live for the read (capture 30); the writes by tests |
| `scheduled` result, pending shape, 409 `country_change_pending` | **NOT LIVE**: needs a live subscription (stage S8). The waiting row was written by SQL; the read and the refusal are real HTTP (captures 26, 27). The `scheduled` response itself is tests only |
| A pending change taking effect at the period's end | tests only |
| The card-country note on a payment | tests only (fake provider through the webhook route); no route exposes it |
| Any real provider (Paystack, Flutterwave) | not involved in this stage; nothing here talks to a vendor |
