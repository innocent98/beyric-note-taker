# C14 UI and mail: the "calendar disconnected" banner and email

**What shipped:** a banner on Meetings and one email per disconnect, both **lead-designed, not in the designer's file** (Adebayo, 2026-11-05: "can we do this ourselves? if yes, proceed"). The designer should redraw them; until then they copy the designer's own patterns. Follows `2026-11-05-phase-c-connection-states.md` (the states and the bell).

## Banner
| | |
|---|---|
| Where | Meetings (`web/components/app/calendar-upcoming.tsx`, `DisconnectedBanner`), in place of the "Join meetings automatically" band, only while `GET /api/calendar` says `needs_reconnect`. Settings already shows the state with "Connect Google Calendar again" |
| Pattern copied | the designer's amber "N tasks need attention" banner on the Tasks screen: `--warning-wash` fill, `--warning-line` border, 14/600 title, 13 sub, secondary button; CSS `.cal-alert` in `web/styles/pages.css` |
| Words | "Your calendar is disconnected" / "The note taker will not join meetings from your calendar until you connect again." / button "Reconnect" (links to `/settings`, the existing connect flow). Not dismissible: the designer's banner has no close |
| Proof | `docs/ui/parity/after/app-meetings-calendar-disconnected-{1440,768,390}.jpg` (Next page against the mock API, `--calendar needs_reconnect`). There is nothing to put beside it in the designer's file, so no side-by-side |

## Email (`calendar_disconnected`)
Built from "Could not join" in `Emails.dc.html` (heading, paragraphs, one button, footer with Email preferences) in `app/mail_templates.py`. Subject and heading "Your calendar is disconnected"; body says Beyric can no longer read the calendar; button "Reconnect" to `/settings`. Render capture: `docs/ui/parity/after/email-calendar-disconnected.{html,txt}`.
- **Once per disconnect:** queued only by the call that writes the bell entry (`cal_disc:{connection}:{connected_at}`), through `alerts.notify_user(email_template=...)`. A reconnect and a later disconnect is a second mail.
- **Only for `needs_reconnect`**, never `degraded`.
- Not queued when `notify_email` is off, the address is suppressed (bounce, complaint, unsubscribe), the user is not active, or no mail provider is set. It goes through the outbox and so the global hourly mail budget.
- Payload carries no text that is shown; the template ignores it.

## Verification
`tests/test_calendar_states.py` (17): one mail per disconnect and none for repeats, a second mail after reconnect, none for degraded, none with `notify_email` off or a suppressed address, render has Reconnect and the preferences link. `tests/calendar-upcoming.test.tsx`: banner words, Reconnect to `/settings`, no close button, replaces the band. Web: lint, typecheck, vitest 559 passed. Not tested live: a real send.

## Review (one Opus round, foreground)
No High or Medium. Fixed the Lows: the mail names no cause (the token may be unreadable on our side), and the banner text uses `--warning-text` like the existing notice. Left: a mail held over the hourly budget is scrubbed after about an hour (the bell and banner still show).

## Operate / roll back
No migration. Revert the PR to remove both.

## Designer gaps (for the designer to redraw)
The disconnect banner (placement, whether other pages show it, dismissal) and the disconnect email. Also open: where a degraded state should show, if anywhere.
