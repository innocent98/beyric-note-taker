# SOP: mails to the designer's file (UI parity batch 2, mails)

## What shipped
The reminder mails (1 hour and 30 minutes before), a new **"Could not join"** mail (template built; **not sent by anything yet**, see "Could not join: cause mapping"), and the wording of the verify, reset and notes-shared mails now follow `docs/ui/Beyric Note Taker redesign/Emails.dc.html`. Python only: no migration, no API shape, no screen. Branch `feature/mails-parity`, PR to `main` (number on the PR page; not merged by the author). Source of the differences: `docs/ui/audit-2026-11-07-pages-emails.md` rows E01 to E09 and E13.

Built partly, on purpose: the reminders' grey summary box and the "· Sprint 13 opens" meta text are **not** built (see "Proposed rules, awaiting Adebayo's OK"), and the Could-not-join mail has **no "Send it again" button** until C15b exists (no dead buttons).

## Why
The audit found the mails differ from the designer's cards in ways a reader sees at once: a grey meta line instead of the teal pill, bold section names instead of the small spaced capitals, owner and task on one line, no closing "Read the full notes" in the 30-minute mail, no "Could not join" mail at all (the failed join sent the generic alert, which has no preferences link), and wording changed from the file in three mails.

## How
| Piece | Decision |
|---|---|
| Layout blocks | New blocks in `app/mail_layout.py` for what only the reminders draw: `Pill`, `Subline`, `Section` (capitals, 12px/600, `#6E7772`, 0.04em spacing, shown upper-case), `TaskRows` (owner under the title, due text right-aligned, red `#B42318` when overdue, grey for "No due date", amber `#9A5B00` for a future date as in "New tasks"), `ButtonRow` (Join meeting filled, View agenda outlined). Existing blocks gained optional spacing fields (`Heading.after`, `Para.after/before/link/strong`, `Bullets.after/gap`, `Meta.after`) whose defaults keep their old output, so mails nobody asked to change are byte-for-byte the same |
| Task rows | One small table per row (not one shared table): a shared table gives every row the width of the widest due text and wraps titles the design keeps on one line |
| Reminder body | `app/series_mail.py` `_series_reminder`. 1 hour: pill, heading (4px under), "Mon 9 Nov, 14:00 to 14:45 UTC · Google Meet", two buttons, then LAST TIME / DECISIONS MADE / OPEN QUESTIONS TO SETTLE / (ALSO ON THE AGENDA) / STILL OPEN FROM LAST TIME. 30 minutes: start time only (the design has no end time), one button, ON THE AGENDA, then the closing paragraph. `_task_rows` returns structured rows; `_tasks_rows` keeps the old "title · owner" strings for the agenda mail, which is untouched |
| 30-minute closing paragraph | `_agreed_line`: "Last time the team agreed: " + the **first two decisions as written** + a teal "Read the full notes" link to `/meetings/{last meeting}`. Nothing is paraphrased (the mails are assembled from stored rows, never generated), which is why it is not the designer's own wording ("agreed to move work updates to Slack ..."). Omitted when there is no readable last meeting or no decision. Per-recipient visibility is unchanged: the last meeting comes from `series.meetings_of(session, series, viewer)` |
| Agenda tail | "Check on 3 unfinished tasks, including {first task's title}." when a task title is known (the design shows a short phrase; we show the task's own title, up to 120 characters) |
| Could not join | New **link template** `meeting_not_joined` (`mail_templates.NOT_JOINED`, `_not_joined`): heading "The note taker could not join your meeting"; "We tried to join **{Google Meet btp-tkio-gtb}** at {HH:MM zone} but were not admitted. This usually happens when the host does not let the note taker in from the waiting room."; "If the meeting is still running, you can send it again."; footer "You received this because you sent the note taker to this meeting." + "Email preferences" (`/settings#email-preferences`). The meeting name is `titles.alert_name(None, platform, native_id)`: platform and code only ("Google Meet", "Zoom", "Microsoft Teams"; never a calendar title or link). The time comes from `mail_templates.not_joined_payload(session, user_id, platform, native_id, at)`: the owner's stored time zone, UTC when none is stored or the stored name is unknown. Subject "Meeting not recorded" (the old alert mail's subject; the designer drew none) |
| The button | `mail_templates.SEND_AGAIN_PATH = None`. **C15b turns it on in one line**: set it to the page path that sends the note taker again; the "Send it again" button then appears, with 24px above it as drawn. Until then the second sentence stays (it is in the file), and the card closes under it |
| Wiring | **None yet, on purpose** (review finding, see below). `alerts.notify_user` gained an optional `email_payload` for templates that read fields of their own; a template in `mail_templates.OWN_PAYLOAD_TEMPLATES` queued with no payload is sent as the plain alert instead, with a warning in the log (never a row that fails as unknown). `join_runner._expire` is unchanged in behaviour: it still sends the plain alert (an earlier draft of this PR sent the new mail from there; reverted) |
| Opt-out | When it is wired, same as the alert it replaces: `alerts.notify_user` queues it only if the account is active, `notify_email` is on, a provider is configured and the address is not suppressed (bounce, complaint or unsubscribe); the sender stops a bounce or complaint at send time. Link template, so not counted in the daily notes caps and scrubbed after sending |
| Mail priority | `mail.ALERT_LIKE = (alert, meeting_not_joined)`: the new mail is loaded in the privileged group ahead of verification mail and keeps the alert's hourly-budget exemption for an admin owner, exactly as the alert it replaces (review item 5: chosen over accepting the change). It is still a link template, so an unsent row is failed after an hour, as an unsent alert already was |
| Wording | Verify: "finish **setting up** your Beyric Note Taker account", "This link expires in 24 hours. If you did not create an account, you can ignore this email." Reset: "This link expires in 30 minutes. If you did not ask for this, your password will stay the same." Notes shared footer: "Sent by {sender email} using Beyric Note Taker." (email only; the unsubscribe link stays, it is needed) |
| Spacing fixed along the way | Link mails: 10px under the heading, 24px under the sentence, note 24px under the button (was 12/20/4: the note sat tight under the button). Shared notes: heading first (6px under), then the meeting line (20px under), as drawn. Whole mail: `font-size:15px;line-height:1.6` on the body, as the design's page (headings, labels and buttons had `line-height: normal`) |
| Not changed (decided) | The "Beyrichq LTD" footer on invite, verify and reset; the HTML "B" tile logo and system font stack (email clients, documented in `mail_layout.py`); every subject line |

## Could not join: cause mapping
The mail's words ("were not admitted ... the host does not let the note taker in from the waiting room") are true for **one** kind of failure: the bot was sent and the room refused it or kept it waiting until Vexa gave up. Where each cause is known:

| Cause | Where it is known | Gets the "Could not join" mail? |
|---|---|---|
| Waiting room not admitted (timeout) or host declined | The **meeting** fails in `app/pipeline.py` (`bot_failure_reason`: Vexa `completion_reason` `awaiting_admission_timeout` / `awaiting_admission_rejected`, `REASON_NOT_LET_IN`); Google refusing the bot's ask to join (`MEET_REFUSED`, `REASON_JOIN_REFUSED`) is the same family | **Yes, when wired** (nothing sends it today: this path sends no bell and no mail) |
| Host full, own bot limit, a note taker already on the code, send failed, event ended early, scheduler down | The **join row** in `app/join_runner.py` `_expire` (`skip_reason` `user_limit`, `code_in_use`, `not_recorded` after `no_capacity` or send failures; `ended`) | **No**: plain alert as before. None of these means "not admitted" |
| Zoom blocked the bot, bot failed, recording failed, needs help | Pipeline (`REASON_*`) | No (different wording needed; not designed) |

Why it is not wired: a join row that sent its bot is `joined` and never reaches `_expire`, so `join.last_error` / `skip_reason` cannot say "waiting room". Sending the mail means adding a notification (a bell entry of a new kind with a per-meeting dedupe key, and a mail for manual sends too) to the pipeline's failed-meeting branch. That is a product decision nobody has approved, so it is left for the lead and Adebayo. When wired, pass the moment the failure was seen as `at` (not a later deadline) and build the payload inside the same guard as the alert call, so a mail problem can never stop the bell or the state change.

## Parity table (designer's value vs built value)
Computed styles were compared in a browser at 640px, text by text (see Verification); "same" means every compared property matched.

| Row | Mail | Designed | Built now | Status |
|---|---|---|---|---|
| E01 | Reminder 1 hour: pill | teal pill `#E6F1EE` / `#0A5248`, 12px/600, radius 999, padding 3px 10px | same | same |
| E01 | labels | LAST TIME, MON 2 NOV / DECISIONS MADE / OPEN QUESTIONS TO SETTLE / STILL OPEN FROM LAST TIME: 12px/600 `#6E7772`, 0.04em, margin 0 0 8px | same | same |
| E01 | Still open rows | owner under title in 12px grey, due text right; red when overdue; "Needs owner"; "No due date" | same | same |
| E01 | meta line | "Mon 9 Nov, 14:00 to 14:45 UTC · Google Meet · Sprint 13 opens" | "... · Google Meet" | **pending OK** (Sprint line) |
| E01 | grey summary box | "Sprint 12 closed with 7 of 11 tasks done. Client Portal launch is in 29 days. The note taker will join this meeting automatically." | not built | **pending OK** |
| E02 | Reminder 30 min: pill, labels, button | as E01, "ON THE AGENDA" | same (the "Join meeting" button's 26px sits on its wrapper, same space) | same |
| E02 | agenda tail | "Check on 3 unfinished tasks, including the operations and pilots lead hire." | "..., including {title of the first unfinished task}." | built (own title, not a short phrase) |
| E02 | closing paragraph | "Last time the team agreed to move work updates to Slack ... Read the full notes" (13px `#6E7772`, teal link) | "Last time the team agreed: {first two decisions as written} Read the full notes" (link line-height 20.8px vs 20.15px) | built, different words |
| E06 | Notes shared footer | "Sent by adebayo@beyrictech.com using Beyric Note Taker." | "Sent by {email} using Beyric Note Taker." + the unsubscribe link | built (link kept) |
| E07 | Could not join | heading, two paragraphs, "Send it again" button, footer + Email preferences | all but the button | **button waits for C15b** |
| E08 | Verify | "finish setting up ...", "This link expires in 24 hours. ..." | same | same |
| E09 | Reset | "This link expires in 30 minutes. If you did not ask for this, your password will stay the same." | same | same |
| E04, E08, E09 | footer "Beyric Technologies" | designer's | "Beyric Note Taker, a product of Beyrichq LTD" | decided, unchanged |
| E10 | logo and font | b-mark SVG, Geist | "B" tile, system fonts | decided, unchanged |

## Proposed rules, awaiting Adebayo's OK
The summary box and the "· Sprint 13 opens" text need numbers and rules nobody has approved. Proposed (not built):
1. **"· Sprint N opens" in the 1-hour meta line.** Data: the series' team has a sprint (`sprints.start_date`, W9) whose start date is the meeting's day, or the first working day after the previous sprint's `end_date`. Shown only then, with the name of the sprint that opens. Omitted when sprints are not in use, the team has no next sprint, or the meeting is not the first one of that sprint.
2. **Grey summary box, first sentence ("Sprint 12 closed with 7 of 11 tasks done.").** Data: the sprint that closed most recently for the team (`closed_at` set), its done count over its task count at close. Shown only in the 1-hour mail, only when a sprint closed within the last 14 days and had at least one task. Omitted otherwise.
3. **Second sentence ("Client Portal launch is in 29 days.").** Data: the nearest future confirmed milestone (`milestones.due_date`, `confirmed_at` set) of the series' project, in whole days from the recipient's today. Shown only when there is a milestone with a due date in the future; omitted otherwise (never "0 days" or a past date).
4. **Third sentence ("The note taker will join this meeting automatically.").** True only when the recipient's own auto-join rule would join this meeting and their calendar is connected. Shown only then, and never to a teammate whose bot will not join.
5. **The box as a whole** is left out when none of the three sentences applies; it is never drawn with one sentence that is not about the sprint or milestone unless it is the auto-join one. Per-recipient visibility: sprint and milestone counts are team data; the task counts must only count tasks the recipient may see.
Questions for him: is 14 days right; should the box appear in the 30-minute mail too (the design shows it only in the 1-hour mail); is the auto-join sentence wanted for people whose bot will not join (proposal: no).

## What's involved
- `app/mail_layout.py` (new blocks, spacing fields, body line-height); `app/series_mail.py` (`_task_rows`, `_agreed_line`, `_series_reminder`); `app/mail_templates.py` (`NOT_JOINED`, `SEND_AGAIN_PATH`, `not_joined_payload`, `_not_joined`, link-mail spacing, verify and reset wording, shared footer and heading order); `app/alerts.py` (`notify_user(email_payload=...)`, `_queue_mail(payload=...)`, plain-alert fallback); `app/mail.py` (`ALERT_LIKE`); `app/titles.py` (Teams label). `app/join_runner.py` keeps only a comment in `_expire`.
- Tests: `tests/test_series_mail.py` (reminder look, closing paragraph, privacy), `tests/test_join_runner.py` (not-recorded stays the plain alert, once; bell unchanged), `tests/test_mail_upgrade.py` (button flag, escaping), `tests/test_shares.py` (footer).
- Evidence: `docs/ui/parity/after/mail-reminder-1h.png`, `mail-reminder-30m.png`, `mail-not-joined.png`, `mail-verify.png`, `mail-reset.png`, `mail-shared.png` (designer's card left, built HTML right, 640px); computed-style result in `docs/ui/parity/after/mail-style-diff.md`.
- No FE integration guide: no API route or response changed (the mails are not an API).

## Verification
- Fast local checks with the project's pinned toolchain: `ruff check .`, `ruff format --check .`, and pytest on `tests/test_mail.py`, `test_mail_upgrade.py`, `test_series_mail.py`, `test_join_runner.py`, `test_shares.py` plus the other modules that import the layout, alerts or templates (`test_alerts`, `test_calendar_states`, `test_email_prefs`, `test_mail_budget`, `test_tasks`, `test_titles`, `test_signup`, `test_team`), against a scratch Postgres. The full suite is left to CI.
- Parity: each changed mail was rendered from the app's own template code (reminders from a seeded database with the design's wording), put beside the designer's card in a browser and compared text by text on font size, weight, colour, letter spacing, vertical margins, background, radius and line height. Result in `mail-style-diff.md`.
- **Not verified:** Gmail, Outlook and Apple Mail rendering; a real Resend send; the live site; dark mode in mail clients; the Geist font (the compare page loads it from Google Fonts, which the test browser may have blocked, so text widths are the system font's on both sides).

## Operate / roll back
- **Server steps before merge: none.** No migration, no environment variable, no nginx line. The `mail_outbox.template` column takes the new name `meeting_not_joined` as text; rows already queued as `alert` still render as before.
- Roll back: revert the PR. A `meeting_not_joined` row queued before a revert would fail with "unknown template" (harmless; the bell entry stands).
- C15b: set `mail_templates.SEND_AGAIN_PATH` to turn the button on.

## Follow-ups
- Adebayo's OK on the five proposed rules, then the sprint line and summary box.
- **Wire the Could-not-join mail** into the pipeline's not-let-in failure (see the cause mapping): needs the lead's and Adebayo's OK for the new bell entry and mail. The time to show is the moment the failure was seen.
- C15b (send now), then flip `SEND_AGAIN_PATH`. Until then the mail says "you can send it again" with no button (the sentence is in the file; if he prefers, drop it until C15b).
- Not designed by the designer, left as built: "Also on the agenda" (1-hour mail, typed agenda lines) uses the same capitals style; a future due date in the reminder rows uses the amber of "New tasks".
- Opus review of #117 (head fb64015), "merge after fixes", and what was done: (1) payload built outside a guard in `_expire` and (2) the mail sent for causes that are not a refusal: both removed by reverting the wiring; (3) a Teams join read "Google Meet": `titles._fallback` now names Microsoft Teams; (4) the time shown was the deadline: the helper takes the moment the news arrived, from the caller; (5) `mail.ALERT_LIKE`, above; (6) a payload-less queue of the template now sends the plain alert with a log line. Tests added for each (`tests/test_alerts.py`, `tests/test_mail_upgrade.py`, `tests/test_join_runner.py`). Not added, because the code path no longer exists: "payload builder raises, bell and state still written" (nothing in `_expire` builds a payload now; add it with the wiring).
