# Spike ML1: Resend attachment size and send limits

**Date:** 2026-11-04. **Question (AI workspace spec section 10):** how big may a mail with attachments be, and how many mails a day may the pilot send, so the caps in `app/mail.py` and `app/mail_message.py` are sized to the plan? **Method:** Resend's published documentation, read on 2026-11-04. **Nothing was tried live**: no mail was sent from the production account for this spike.

## What Resend's documentation says

| Fact | Value | Source |
|---|---|---|
| Largest mail | **40 MB including attachments after Base64 encoding** (Base64 adds a third, so about 30 MB of files) | [Send Email API reference](https://resend.com/docs/api-reference/emails/send-email), [Attachments](https://resend.com/docs/dashboard/emails/attachments) |
| Attachments per mail | not stated | same two pages |
| Allowed file types | "Not all file types are supported"; the list is on a separate page that was not read | [Attachments](https://resend.com/docs/dashboard/emails/attachments) |
| Recipients in `to` | at most 50 | [Send Email API reference](https://resend.com/docs/api-reference/emails/send-email) |
| Attachments in the batch endpoint | not allowed | [Attachments](https://resend.com/docs/dashboard/emails/attachments) |
| API rate | **10 requests a second per team**, shared by all keys | [Rate limit](https://resend.com/docs/api-reference/rate-limit) |
| Free plan | **3,000 mails a month and 100 a day**, 3 domains | [Pricing](https://resend.com/pricing) |
| Daily quota | Free plan only; counted on the **UTC calendar day**, resets at 00:00 UTC (not a rolling 24 hours); inbound and outbound both count; going over answers 429 `daily_quota_exceeded` (or `monthly_quota_exceeded`) | [Rate limit](https://resend.com/docs/api-reference/rate-limit) |
| Pro plan | 50,000 mails a month for $20, **no daily limit** | [Pricing](https://resend.com/pricing) |
| Idempotency key | 1 to 256 characters, kept 24 hours; the same key with a different body answers 409 `invalid_idempotent_request`; the same key while the first is running answers 409 `concurrent_idempotent_requests` | [Idempotency keys](https://resend.com/docs/dashboard/emails/idempotency-keys) |
| Suppression | Resend adds an address to its own suppression list after a hard bounce or a spam complaint; events `email.bounced`, `email.complained`, `email.suppressed`, `suppression.added` exist for webhooks | [Event types](https://resend.com/docs/dashboard/webhooks/event-types) |
| `reply_to`, `headers` | accepted by the send call | [Send Email API reference](https://resend.com/docs/api-reference/emails/send-email) |

## Caps chosen

| Cap | Value | Reasoning |
|---|---|---|
| Attachments together | **10 MB of files** (`MAX_ATTACHMENT_BYTES`), at most 3 files, only PDF, Word and plain text | A tenth of what Resend takes after encoding, and below what a recipient's mailbox takes: Gmail refuses mail over 25 MB and Outlook over 20 MB (their documentation, not re-read here), counted after Base64 (about 13.3 MB for 10 MB). Notes exports are a few hundred KB; a transcript of a long meeting is a few MB at most |
| Mails a day, all kinds | **80** for notes mail (`MAIL_DAILY_CAP`), counted since 00:00 UTC across every mail sent, sign-in mail included | The Free plan stops at 100; the 20 left are for sign-in mail, which is never held back. **Assumed that the account is on the Free plan**; on Pro set `MAIL_DAILY_CAP=0` |
| Notes mails to one address | **5 in 24 hours** (`MAIL_RECIPIENT_DAILY_CAP`) | Stops one address being mailed repeatedly (by a share loop or a bug). Assumed number |
| Recipients per mail | 1 (one API call per recipient) | Resend allows 50 in `to`, but each person must get their own mail (each is checked against suppression and the caps, and no one sees another's address) |

At the pilot's size (10 accounts) 80 notes mails a day is far above the expected load. The API rate (10 a second) is far above the worker's pace (a batch of 25 a pass, one call at a time).

## Findings that change how the code behaves

1. **A mail held for the daily quota is not failed.** The Free plan's daily limit resets at 00:00 UTC, so a notes mail over the cap stays `pending` (no attempt used) and goes after midnight UTC; it expires after 24 hours (`HOLD_NOTES_MAIL_FOR`). Sign-in mail (verify, reset) is never held, because it is the sign-in path.
2. **Retrying a notes mail can answer 409.** The mail is built from the live notes each time it is tried, and the idempotency key is stable per row (`mail-<id>`). If the first try reached Resend but the answer was lost, and the notes changed before the retry, Resend answers 409 `invalid_idempotent_request`, which our sender treats as permanent (4xx). The mail then shows `failed` although the first copy may have gone. Accepted: it needs a lost answer and an edit inside the same minutes.
3. **Resend keeps its own suppression list**, filled after a hard bounce or a complaint. Our `mail_suppressions` table is separate: it also holds unsubscribes, and it lets the sender skip a mail before the notes are even read. **Nothing yet copies Resend's events into it**: see Follow-ups.

## What could not be verified live (and why)

- **The plan the account is on** (Free or Pro): the dashboard shows it; I have no access. The 80 a day cap assumes Free.
- **That a mail near the limit is accepted or refused as documented**: nothing was sent. The 10 MB cap sits far below the documented limit, so a wrong reading costs nothing.
- **Which file types Resend refuses**: the unsupported-types page was not read; PDF, Word and plain text are the common types and are unlikely to be on it. A test mail with a PDF and a `.docx` from the real account should be sent once before W6 ships.
- **The mailbox limits** quoted for Gmail and Outlook come from memory of their public documentation, not from a page read today.
- **Webhook delivery** of bounces and complaints, and the signature scheme of Resend's webhooks.

## Follow-ups

- A Resend webhook endpoint (`email.bounced`, `email.complained`) that writes `mail_suppressions` after verifying Resend's signature; it needs a signing secret (new server variable) and one unauthenticated route, so it gets its own design and an Opus review. Until then an operator adds addresses with `scripts/suppress_email.py`.
- Send one real mail with a PDF and a Word attachment from the production domain, and read the daily quota headers (`x-resend-daily-quota`) once to confirm the plan.
