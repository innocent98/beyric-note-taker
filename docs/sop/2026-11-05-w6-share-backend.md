# SOP: Share notes by email, backend (AI workspace phase W6, PR 1 of 2)

## What shipped
`POST` and `GET /api/meetings/{id}/shares` and the public `POST /api/mail/opt-out`; the `meeting_shares` and `meeting_share_recipients` tables (migration **0029**); the "Notes shared with you" mail in the designer's layout with PDF, Word and transcript attachments; the sweep that settles shares waiting for a translation; privacy-policy and terms rows (spec 13.1). The screens are PR 2. PR: see the PR list (branch `feature/w6-share-backend`).

## Why
Spec section 10: a pilot user mails the notes of a meeting to colleagues and clients. The risk is that **a pilot account can make our sending domain mail anyone**, so every control in the spec's abuse table is in this PR, and the route was reviewed by Opus (findings below).

## ML1 spike: Resend limits (read 2026-11-05, no live send)
Read again on 2026-11-05 from Resend's own pages; it agrees with the first reading (`docs/spike/resend-limits-2026-11-04.md`), so **no cap changed**.

| Fact | Value | Source |
|---|---|---|
| Largest mail | **40 MB including attachments after Base64 encoding**; no separate per-file limit, no attachment-count limit stated | [send-email API reference](https://resend.com/docs/api-reference/emails/send-email), [attachments](https://resend.com/docs/dashboard/emails/attachments) |
| Unsupported attachment types | a published list of blocked extensions (executables and scripts such as `.exe`, `.js`, `.bat`, `.vbs`, `.msi`); PDF, Word (`.docx`) and text are not on it | [what attachment types are not supported](https://resend.com/docs/knowledge-base/what-attachment-types-are-not-supported) |
| Recipients in `to` | at most 50 (we send one mail per person, so 1) | send-email reference |
| API rate | 10 requests a second per team, shared by all keys; over it answers 429 | [rate limit](https://resend.com/docs/api-reference/rate-limit) |
| Free plan | 3,000 mails a month and **100 a day**; the daily quota is the UTC calendar day, resets at 00:00 UTC; over it answers 429 `daily_quota_exceeded` | rate-limit page, [pricing](https://resend.com/pricing) |
| Pro plan | 50,000 a month for $20, **no daily limit** | pricing |
| Batch endpoint | does not accept attachments (we do not use it) | attachments page |

**Caps in this PR, sized to that:** attachments together at most **10 MB** and 3 files (`MAX_ATTACHMENT_BYTES`, a quarter of what Resend takes after encoding and below Gmail's 25 MB and Outlook's 20 MB, which are from memory, not re-read); **80 mails a day** overall (`MAIL_DAILY_CAP`, the Free plan's 100 less 20 kept for sign-in mail) and **5 notes mails to one address in 24 hours** (`MAIL_RECIPIENT_DAILY_CAP`), both unchanged; **10 recipients a share and 20 recipients a day per account** (the spec's D15), which for ten pilot accounts means up to 200 a day if everyone maxed out. That is above the 80 a day cap on the Free plan, so **on Free the 80-a-day cap, not the 20-a-day account limit, is what stops a busy day**; mail over it waits (pending, up to 24 hours) and goes after midnight UTC. A held share shows its recipients as `pending`, never as failed.

**Not verified (needs a live send):** which plan the account is on (Free or Pro); that Resend accepts a PDF and a `.docx` from `notes.beyrictech.com`; the exact attachment field name for the content type (the extracted docs text did not show it; `ResendMailer` sends `content_type`, which is what Resend's API accepts, but this was not re-confirmed). One command for Adebayo, after this is deployed (it sends one mail to the address you give, with a PDF and a Word file, from the real domain and key, and prints the status, the file sizes and any quota headers Resend returns):

```bash
ssh haven-respite 'cd /opt/beyric-note-taker && docker compose exec -T web python scripts/send_test_mail.py YOUR-ADDRESS@example.com' </dev/null
```

## How
| Piece | Decision |
|---|---|
| Tables hold ids and the sender's words only | `meeting_shares` (meeting, sender, language, attach, transcript tick, the 500-character message, status) and `meeting_share_recipients` (address, optional name, teammate id, status). Neither holds notes. Both cascade with the meeting. The `mail_outbox` payload is `{share_id, recipient_id, meeting_id}` (integers: `enqueue` refuses anything else for a notes template), so the notes are read **when each mail is sent**. |
| One mail per recipient | Each person is checked against suppression, the caps and the send-time guards on their own, and nobody sees another's address. A unique index on `(share_id, recipient_id)` of `notes_shared` outbox rows makes it once. |
| Send-time re-checks (`_notes_shared`, `app/mail_templates.py`) | Share and recipient exist and match; share not revoked and `queued`; the address on the row is the recipient's; the meeting exists and is ready; its owner is active **and** still the sender **and** confirmed; a translated share still has a **current** translation (else `skipped: translation_missing`, never the wrong words). Every `Skip` becomes `not_sent` for the recipient. |
| Limits | 10 recipients a share (`422 too_many_recipients`), 20 a day per account counted from the `share_usage` ledger (a count and a time per account, no address; **not** from the shares, which go with their meeting, so deleting a meeting gives nothing back), taken under an advisory lock per account so two requests cannot both pass (`429 share_limit`). Failed shares count. |
| Verified senders only | `403 email_not_verified` when `users.email_verified_at` is null; also checked at send time. |
| Message | `clean_line(message, 500)`: invisible and control characters removed, one line, 500 characters. Shown as a quote, escaped by the layout, never a link (a `Quote` block has no href). |
| Sender identity | Footer "Sent by <name> (<email>) using Beyric Note Taker."; `Reply-To` is the owner's address; subject "<name> shared meeting notes: <title>". The `From` stays our domain. |
| No links for outside people (D7) | The only `href` in an outside recipient's mail is the opt-out link. A teammate gets "Open notes" (`/meetings/{id}`, sign-in required) **only if** the meeting is shared with their team; for a private meeting the link would be a 404, so there is none. |
| Opt-out | `auth_tokens` purpose `mail_optout` (migration extends the check constraint); minted **at send time** for the recipient's address (hash only stored, 180 days); the footer link is `/unsubscribe?token=`; the page POSTs `POST /api/mail/opt-out` (public, CSRF header as for sign-up), which consumes the token once and writes `mail_suppressions` `unsubscribed` for **the address on the token**. That stops every notes mail to that address, and not sign-in mail (existing rule). I did not add `List-Unsubscribe` headers: a mail provider's one-click POST cannot send our CSRF header. |
| Translation first, then the mail | A share in `fr`/`es` that is not translated calls `translation.request_translation` (which counts against the 20 translations a day and may answer `429 translation_limit`) and waits as `waiting_translation`. The translation worker calls `shares.on_translation_finished` after it commits; it queues the mails or fails the share (bell notification). The hourly `sweep_shares` settles anything the hook missed, fails a share after 2 hours, and marks a recipient `failed` whose mail expired a day after being queued. |
| Attachments | Built in the worker at send time from the same text the mail shows, with `export.build_file` (the W5 child process): PDF or Word of every section, transcript inside when ticked; with `attach: none` and the tick, the transcript is a `.txt`. A meeting with no transcript attaches nothing and the mail does not claim it. |
| Mail body | The designer's "Notes shared with you": sender line, sender's quote, Summary, Decisions, Action items, Open questions (abridged), "Open notes" for eligible teammates, "The full transcript is attached." / "The notes are attached.". Spec row 10 (the four sections) and row 11 (translation, then the original below a divider with "Original notes (English)"). Deviation: the designer's mock shows Summary and Open questions only; the spec (section 2, row 10) overrides to four sections, and the designer's "Open notes" is dropped for outside people (D7). |
| Rejected | Storing the notes in the share row (a second copy of meeting text that outlives edits and deletion); one mail to all recipients (shows addresses, one suppression or cap would block everyone); a public link or a GET opt-out (D7; mail scanners would opt people out); a share `sent` status (it would need a second writer; the recipient rows already say it). |

## What's involved
- New: `app/shares.py`, `app/api_shares.py`, `alembic/versions/0029_meeting_shares.py` (down_revision 0026), `tests/test_shares.py` (43), `scripts/capture_share_api.py`, `scripts/send_test_mail.py`, `docs/fe-captures/share/`, `docs/fe-integration-guide-share.md`.
- Changed: `app/models.py` (two models, `mail_optout` purpose, outbox index), `app/mail_templates.py` (`notes_shared`, `abridged_blocks(names)`), `app/mail_layout.py` (`Divider`), `app/mail.py` (`_finish` tells the share), `app/translation.py` (`_release_shares`), `app/sweeps.py` and `app/maintenance.py` (`shares.sweep`), `app/auth.py` (`POST /api/mail/opt-out` public), `app/api.py`, `app/api_schemas.py`, `docs/openapi.json`, `web/lib/api/schema.d.ts`, the privacy and terms pages in both `app/templates/` and `web/components/legal/`, `tests/test_accounts.py` (the foreign-id enumeration now covers `/shares`), `tests/test_api.py` (public list), `tests/test_sweeps.py`, `tests/test_migrations.py`, `tests/test_legal.py`.
- Policy text (spec 13.1): collect (names and addresses of people you share with, the message), use (shared-notes email), who we share with (Resend delivers the notes extract, message and files to the people you choose), retention (the record of who you shared with goes with the meeting), your rights (the opt-out link), terms (you are responsible for who you send to; no unsolicited messages). **"Last updated" is unchanged** (4 November 2026) as instructed; policy changes carry no notice.

## Verification
- `tests/test_shares.py`: the request and its limits (10, 20 a day, 24-hour roll-off, language, bad address, message length, unverified sender), ids-only outbox payload, owner-only (stranger and teammate get 404 on both routes), translation first (hook, failure, cached, sweep, timeout), the rendered mail (sender, Reply-To, escaped message, no link for outsiders, teammate link only when shared, attachments, French then original), suppression and opt-out (once, only the hash stored), send-time checks (revoked, meeting deleted, owner deleted, notes rewritten, stale translation), account purge.
- Route enumeration: `tests/test_accounts.py` calls every route with an id as another user (now including `/shares`); `tests/test_api.py` proves the login rule, the CSRF rule and that only `/api/mail/opt-out` is public.
- Captured live against the running app: `docs/fe-captures/share/` (63 files); the guide `docs/fe-integration-guide-share.md` quotes them.
- Not verified: a real Resend send (see ML1), real translation quality (fake model), a browser opening the unsubscribe link (PR 2).

## Operate / roll back
- No new `.env` variable. Migration 0029 only adds tables, an index and a check value; downgrade drops them (and deletes `mail_optout` tokens). Reverting the code with 0029 applied is safe: nothing else reads the new tables.
- Until PR 2 merges nothing in the web app calls the share route, so no mail is sent by users. The opt-out link in a mail points to `/unsubscribe`, which PR 2 builds.
- An operator can stop one address with `scripts/suppress_email.py`.

## Follow-ups
- **Opus review (2026-11-05, foreground-equivalent; no Critical or High).** Fixed here: (1, Medium) the message, the sender's name and the notes bullets now have web addresses removed and bare domains defanged (`titles.mail_line`); (2, Medium) the daily limit survived a meeting delete only by luck, now a ledger table; (3, Medium) shared-notes mail had shared the per-address cap with the address's own notes-ready mail, so it now counts apart (`_sent_since(shared=...)`); (4, Low) a share could wait up to an hour for the sweep if the translation committed between its check and its commit, now re-checked after commit; (5, Low) the give-up margin for a held mail widened to 30 hours. **Left:** (3b) all notes mail still shares the global 80-a-day quota, so a busy day of shares delays everyone's notes-ready mail to the next UTC day (a separate budget would be a design decision); (6, Low) no `List-Unsubscribe` header (needs a CSRF-free endpoint).
- A way to cancel a share still queued (`revoked_at` and the send-time check exist; no route sets it).
- Resend webhook for bounces and complaints into `mail_suppressions` (already a follow-up of ML1).
- `List-Unsubscribe` one-click needs a CSRF-free endpoint: its own design.
- "Last updated" date on the legal pages when Adebayo decides.
