# Frontend integration guide: Share notes by email (W6)

Every body, status code and error string below is pasted from a response captured from the running app by `scripts/capture_share_api.py`: real HTTP, a scratch Postgres with the real migrations, the real app under uvicorn. The files are in `docs/fe-captures/share/` (`NN-name.json` for each call, `mail-NN-*.json` for each rendered mail). **No mail left the machine** and the translating model is a fake that answers from the designer's own sample sentences: see the verification table at the end for what that means. Addresses in the captures are placeholders (`@example.test`).

Auth, CSRF and the error shape are as in `docs/fe-integration-guide-owner-app.md`: session cookie, `X-CSRF-Token` on every non-GET, errors `{"error": {"code", "message", "fields?"}}`.

## 1. What it is, and when it happens

On a ready meeting the owner opens **Share** and mails the notes to up to **10 people**. Each person gets their own mail (nobody sees another's address), sent as the owner: `Reply-To` is the owner and the footer says "Sent by <name> (<email>) using Beyric Note Taker."

- The mail holds Summary, Decisions, Action items and Open questions, abridged. An optional **PDF or Word** file holds every section; with the **transcript** tick it holds the transcript too (with no file chosen, the transcript is its own `.txt`).
- **Language:** `en` (the original), `fr` or `es`. A language that is not translated yet is translated first and the mails go by themselves afterwards: the owner asks once.
- **No link to the notes for outside people** (decision D7): the mail carries the content. A **teammate who may read the meeting** (it is shared with the team) gets an "Open notes" button to `/meetings/{id}`, which needs sign-in. A teammate on a private meeting gets no button.
- **Participants are names, not addresses.** Only a member of the owner's team has an email the app knows. For every other participant the dialog asks for an address ("Add email", lead-designed). The API only ever sees addresses.
- Limits: **10 recipients a share, 20 recipients a day per account** (24 hours rolling). Only the person who recorded the meeting may share it or list its shares.
- Every mail has "Don't want these? Stop mail from Beyric Note Taker": a link to `/unsubscribe?token=...` on our site.

## 2. Endpoints

### 2.1 `POST /api/meetings/{id}/shares`: share

Body: `recipients` (1 to 10 of `{email, name?}`), `language` (`en`|`fr`|`es`), `attach` (`none`|`pdf`|`docx`, default `none`), `include_transcript` (default `false`), `message` (optional, at most 500 characters, plain text). Unknown fields are refused. Answers **202** with `{share_id, status}`; `status` is `queued` or `waiting_translation`.

`POST /api/meetings/1/shares`  request body:

```json
{
  "recipients": [
    {
      "email": "client@example.test",
      "name": "Client Example"
    },
    {
      "email": "pilot@example.test"
    }
  ],
  "language": "en",
  "attach": "docx",
  "include_transcript": true,
  "message": "Sharing the notes from today's call. Let me know if anything looks off."
}
```

→ **202**

```json
{
  "share_id": 1,
  "status": "queued"
}
```

A language that is not translated yet:

`POST /api/meetings/1/shares`  request body:

```json
{
  "recipients": [
    {
      "email": "client@example.test"
    }
  ],
  "language": "fr",
  "attach": "none"
}
```

→ **202**

```json
{
  "share_id": 2,
  "status": "waiting_translation"
}
```

_Capture note: 202 with `waiting_translation`: the translation is queued and the mail follows._

### 2.2 `GET /api/meetings/{id}/shares`: "Shared with ..."

Newest share first. Owner only. Nothing shared yet:

`GET /api/meetings/1/shares`

→ **200**

```json
{
  "shares": []
}
```

Right after the request, before the worker's next send pass (`pending`):

`GET /api/meetings/1/shares`

→ **200**

```json
{
  "shares": [
    {
      "share_id": 1,
      "status": "queued",
      "language": "en",
      "attach": "docx",
      "include_transcript": true,
      "message": "Sharing the notes from today's call. Let me know if anything looks off.",
      "created_at": "2026-11-05T22:01:59.098118Z",
      "recipients": [
        {
          "email": "client@example.test",
          "name": "Client Example",
          "status": "pending",
          "is_team_member": false
        },
        {
          "email": "pilot@example.test",
          "name": "Daniel Example",
          "status": "pending",
          "is_team_member": true
        }
      ]
    }
  ]
}
```

_Capture note: Before the worker has sent anything: every recipient is `pending`._

After the send pass (`sent`; the teammate's `name` is their account name, `is_team_member` is true):

`GET /api/meetings/1/shares`

→ **200**

```json
{
  "shares": [
    {
      "share_id": 1,
      "status": "queued",
      "language": "en",
      "attach": "docx",
      "include_transcript": true,
      "message": "Sharing the notes from today's call. Let me know if anything looks off.",
      "created_at": "2026-11-05T22:01:59.098118Z",
      "recipients": [
        {
          "email": "client@example.test",
          "name": "Client Example",
          "status": "sent",
          "is_team_member": false
        },
        {
          "email": "pilot@example.test",
          "name": "Daniel Example",
          "status": "sent",
          "is_team_member": true
        }
      ]
    }
  ]
}
```

_Capture note: After the worker's send pass. The teammate's name comes from their account._

A French share waiting for its translation:

`GET /api/meetings/1/shares`

→ **200**

```json
{
  "shares": [
    {
      "share_id": 2,
      "status": "waiting_translation",
      "language": "fr",
      "attach": "none",
      "include_transcript": false,
      "message": "",
      "created_at": "2026-11-05T22:02:01.586963Z",
      "recipients": [
        {
          "email": "client@example.test",
          "name": null,
          "status": "pending",
          "is_team_member": false
        }
      ]
    },
    {
      "share_id": 1,
      "status": "queued",
      "language": "en",
      "attach": "docx",
      "include_transcript": true,
      "message": "Sharing the notes from today's call. Let me know if anything looks off.",
      "created_at": "2026-11-05T22:01:59.098118Z",
      "recipients": [
        {
          "email": "client@example.test",
          "name": "Client Example",
          "status": "sent",
          "is_team_member": false
        },
        {
          "email": "pilot@example.test",
          "name": "Daniel Example",
          "status": "sent",
          "is_team_member": true
        }
      ]
    }
  ]
}
```

An address that opted out shows `not_sent` (the reason is never named):

`GET /api/meetings/2/shares`

→ **200**

```json
{
  "shares": [
    {
      "share_id": 3,
      "status": "queued",
      "language": "en",
      "attach": "none",
      "include_transcript": false,
      "message": "",
      "created_at": "2026-11-05T22:02:01.996432Z",
      "recipients": [
        {
          "email": "client@example.test",
          "name": null,
          "status": "not_sent",
          "is_team_member": false
        },
        {
          "email": "other@example.test",
          "name": null,
          "status": "sent",
          "is_team_member": false
        }
      ]
    }
  ]
}
```

_Capture note: The address that opted out shows `not_sent`; the reason is never named._

### 2.3 `POST /api/mail/opt-out`: stop mail (public)

Called by the page at `/unsubscribe?token=...` when the person presses a button. **A GET must not do it** (mail scanners open links). No login; the CSRF header is still required, so the page first calls `GET /api/session` as the sign-up pages do. The token works once and for 180 days.

`POST /api/mail/opt-out`  request body:

```json
{
  "token": "<opt-out-token>"
}
```

→ **200**

```json
{
  "status": "unsubscribed"
}
```

_Capture note: Public. The token is the one in the footer link of the first mail (`/unsubscribe?token=...`). It is used up by this call, so the captured mail files show `<opt-out-token>` instead of it._

`POST /api/mail/opt-out`  request body:

```json
{
  "token": "<opt-out-token>"
}
```

→ **400**

```json
{
  "error": {
    "code": "token_invalid_or_expired",
    "message": "This link is not valid any more."
  }
}
```

`POST /api/mail/opt-out`  request body:

```json
{
  "token": "not-a-token"
}
```

→ **400**

```json
{
  "error": {
    "code": "token_invalid_or_expired",
    "message": "This link is not valid any more."
  }
}
```

## 3. Errors the dialog must handle

| When | Status | `code` | `message` (verbatim) |
|---|---|---|---|
| language not offered | 422 | `language_unsupported` | Notes can be translated into French or Spanish. The original is the English version. |
| 11 recipients | 422 | `too_many_recipients` | You can send to at most 10 people at a time. |
| bad address | 422 | `invalid_recipient` | One of the email addresses is not valid. |
| empty list | 422 | `validation_error` | Some fields are missing or not valid. |
| 501 characters | 422 | `validation_error` | Some fields are missing or not valid. |
| attach not none/pdf/docx | 422 | `validation_error` | Some fields are missing or not valid. |
| notes not written | 409 | `not_ready` | The notes are not ready yet. |
| not the caller's meeting | 404 | `not_found` | No such meeting |
| 21st recipient of the day | 429 | `share_limit` | You can send notes to 20 people a day. Try again tomorrow. |
| opt-out token used twice | 400 | `token_invalid_or_expired` | This link is not valid any more. |
| opt-out without token | 422 | `validation_error` | Some fields are missing or not valid. |

Not captured live (see the table at the end): `403 email_not_verified` ("Confirm your email address before sharing notes."), `503 mail_unavailable` ("Email is not available right now. Try again later."), `429 translation_limit` (from the translate route, 20 a day). Their text is from the code.

Validation errors from the body shape (an unknown `attach`, a missing field, a message over 500 characters) have `code: "validation_error"` and a `fields` list:

`POST /api/meetings/1/shares`  request body:

```json
{
  "recipients": [
    {
      "email": "client@example.test"
    }
  ],
  "language": "en",
  "attach": "zip"
}
```

→ **422**

```json
{
  "error": {
    "code": "validation_error",
    "message": "Some fields are missing or not valid.",
    "fields": [
      {
        "field": "attach",
        "message": "Input should be 'none', 'pdf' or 'docx'"
      }
    ]
  }
}
```

A teammate who can read the meeting gets **404** on both routes, the same as a stranger:

`POST /api/meetings/1/shares`  request body:

```json
{
  "recipients": [
    {
      "email": "client@example.test"
    }
  ],
  "language": "en"
}
```

→ **404**

```json
{
  "error": {
    "code": "not_found",
    "message": "No such meeting"
  }
}
```

## 4. Field-nesting traps

- `status` exists at **two levels**: on the share (`waiting_translation` | `queued` | `failed` | `revoked`) and on each recipient (`pending` | `sent` | `not_sent` | `failed`). The share stays **`queued` after every mail has gone**: it means "the mails are in the outbox". "Sent" is a recipient state. Show "Shared with ..." from `recipients[].status`, never from the share's status alone.
- `name` is `null` for an outside address given without a name; a teammate's `name` is filled from their account.
- `message` on a share is `""` (empty string), not `null`, when none was written. On the request it may be omitted or `null`.
- `POST` answers `{share_id, status}` only; read the recipients from the `GET`.

## 5. State machines and polling

Share: `waiting_translation` → `queued` (translation finished) or `failed` (translation failed, went stale, or 2 hours passed). `queued` → nothing further. A `failed` share also puts a notification in the bell ("Your notes were not sent").

Recipient: `pending` → `sent` | `not_sent` (opted out, mailbox daily limit, share revoked, meeting deleted...) | `failed` (provider refused, or the mail expired after a day).

- After `POST`, show the toast at once ("Notes sent in French to 3 people" for `queued`; for `waiting_translation` say the notes are being translated and will be sent).
- While a share is `waiting_translation`, ask `GET` every **2 seconds** (as for translations) up to a few minutes, then every 30 seconds. Recipients go `pending` → `sent` within about a minute of `queued`; poll every 5 seconds for 30 seconds, then stop and show what you have.

## 6. What the dialog must tell the person (UX consequences)

- **The limit is 20 recipients a day, counting every share, including ones that failed.** Show the recipient counter against 10 and, on `share_limit`, the message above. It does not give capacity back when a meeting is deleted.
- **A translation may take minutes.** A `waiting_translation` share is not an error. It uses one of the account's 20 daily translations if the language was not translated yet.
- **A mail can be held up to a day** when our provider's daily sending quota is used; `pending` can therefore last. Do not call it failed.
- **`not_sent` is not a bug** and is shown only as "not sent". Do not guess why.
- **Nobody outside the team gets a link.** Do not promise "they can open the notes online". The "Add email" copy should say the notes are in the mail itself.
- Notes are read **when each mail is sent**, not when Share is pressed: a rewrite of the notes in between is what goes out. A translation that went stale in between is not sent.
- The person's **own confirmed email** is required (`403 email_not_verified`).

## 7. The mail, as rendered (capture `mail-01-client.json`)

Subject: `Adebayo Example shared meeting notes: Google Meet abc-defg-hij`. `Reply-To`: owner. Attachments: `[{"filename": "Google Meet abc-defg-hij.docx", "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "bytes": 38882}]`. Plain-text part:

```
Google Meet abc-defg-hij · 05 Nov 2026, 22:01 UTC · 21 min

Adebayo shared meeting notes with you

“Sharing the notes from today's call. Let me know if anything looks off.”

Summary

The meeting introduced Beyric Note Taker, an app being developed for internal use. Adebayo described problems with inaccurate transcripts from another note-taking app and said he wanted to test how well the new app captures this meeting. Automation and the app's effectiveness remain to be explored.

Decisions

- Beyric Note Taker is intended for internal use.
- The app would be tested during this meeting.

Action items

None noted

Open questions

- How can the app be automated?
- How well will it capture and summarize meetings?
- Could Adebayo hear Grace after she raised the network issue?

The full transcript is attached.

Sent by Adebayo Example (owner@example.test) using Beyric Note Taker.
Don't want these? Stop mail from Beyric Note Taker: https://notes.example.test/unsubscribe?token=<opt-out-token>
```

A French share (`mail-03-client.json`) puts the translation first, then a rule, then "Original notes (English)":

```
Google Meet abc-defg-hij · 05 Nov 2026, 22:01 UTC · 21 min

Adebayo shared meeting notes with you

Résumé

La réunion a présenté Beyric Note Taker, une application développée pour un usage interne. Adebayo a décrit des problèmes de transcriptions inexactes avec une autre application de prise de notes et a voulu tester la qualité de capture de la nouvelle application lors de cette réunion. L'automatisation et l'efficacité de l'application restent à explorer.

Décisions

- Beyric Note Taker est destiné à un usage interne.
- L'application serait testée pendant cette réunion.

Actions à mener

Aucune action notée.

Questions ouvertes

- Comment automatiser l'application ?
- Dans quelle mesure capturera-t-elle et résumera-t-elle les réunions ?
- Adebayo a-t-il entendu Grace après qu'elle a signalé le problème de réseau ?

----

Original notes (English)

Summary

The meeting introduced Beyric Note Taker, an app being developed for internal use. Adebayo described problems with inaccurate transcripts from another note-taking app and said he wanted to test how well the new app captures this meeting. Automation and the app's effectiveness remain to be explored.

Decisions

- Beyric Note Taker is intended for internal use.
- The app would be tested during this meeting.

Action items

None noted

Open questions

- How can the app be automated?
- How well will it capture and summarize meeti
...
```

## 8. Verification

| Claim | Verified |
|---|---|
| Every request/response body, status and error string in sections 2-3 | **live**: captured by `scripts/capture_share_api.py` against the running app |
| Rendered mail subject, text, attachments, Reply-To | **live** as rendered by the app; the mail was **not** delivered |
| Resend accepts the mail, the attachment size and types, the daily quota | **not verified live**: needs one real send (command in the SOP) |
| Translation content | **fake model** (the designer's sample): the flow is verified, the quality is not |
| `403 email_not_verified`, `503 mail_unavailable`, `429 translation_limit` | **not captured**; strings from the code (covered by tests/test_shares.py for the first) |
| The `/unsubscribe` page and the Share dialog | built in the screens PR (`docs/sop/2026-11-06-w6-share-screens.md`); the API behind them is verified live |
| `share_limit` after 10 + 10 recipients | **live** (capture 37) |
