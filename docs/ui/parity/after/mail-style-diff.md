# Mail parity: computed-style comparison (2026-11-07)

Method: each changed mail was rendered from the app's template code, placed beside the designer's card
(`docs/ui/Beyric Note Taker redesign/Emails.dc.html`) in a browser at 640 px, and every text node that
appears in both was compared on font size, weight, colour, letter spacing, top and bottom margin,
background, radius and line height (text matched case-insensitively; the labels are literal upper-case
text in both, so they compare exactly too). Screenshots:
`mail-*.png` in this folder (designer left, built right). Sample data was seeded to use the design's words,
so the text nodes line up.

| Mail | Text nodes identical on every property | Differences left |
|---|---|---|
| Reminder 1 hour (E01) | 27 | none on shared text; the meta suffix and the grey box were added afterwards, see "Summary box and Sprint line" below |
| Reminder 30 minutes (E02) | 8 | "Join meeting" bottom margin 26px (design, on the link) vs 0 (built: the 26px is on its wrapper, same space); "Read the full notes" line height 20.15px vs 20.8px. Texts that differ on purpose: agenda lines (own data), closing sentence (first two decisions as written) |
| Could not join (E07) | 8 | the paragraph above the button has margin 24px in the design and 0 built, because the button is not built (C15b); "Send it again" not built |
| Verify (E08) | 6 | none. "Beyric Technologies" footer: Beyrichq LTD footer kept (decided) |
| Reset password (E09) | 6 | none. Same footer note |
| Notes shared (E06) | 6 | none on shared text (heading 6px, meta 20px as drawn). The footer carries the unsubscribe link after the sender line (kept, needed) |

Structure differences that do not show (ignored by the comparison on purpose): the mails use tables for
layout, so `display` and padding on cells differ from the design's flex rows; the logo is an HTML "B"
tile (decided).

Not checked: Gmail, Outlook, Apple Mail, a real Resend send, the Geist web font (the system font stack is
used by the mails; the designer's page may have fallen back to the system font here too).

## Summary box and Sprint line (2026-11-07, PR feature/mails-summary-box)

Same method. The 1-hour mail was rendered from the app's own template code with the design's words
(`mail-reminder-1h-summary.png`: designer left, built right, 640 px). The seeded data produced exactly the
designer's three sentences ("Sprint 12 closed with 7 of 11 tasks done. Client Portal launch is in 29 days.
The note taker will join this meeting automatically.") and the meta line ("Mon 9 Nov, 14:00 to 14:45 UTC ·
Google Meet · Sprint 13 opens").

| Element | Compared on | Result |
|---|---|---|
| Grey box | font size, weight, colour, letter spacing, margins, background, radius, line height, padding (4 sides) | all 13 identical: 13px / 400 / rgb(74,82,78) / background rgb(246,247,246) / radius 10px / line-height 20.15px / padding 12px 14px |
| Meta line (with "· Sprint 13 opens") | same 13 properties | all identical: 14px / rgb(91,100,96) / margin-bottom 22px / line-height 22.4px |
| Rest of the card | same, per text node | 22 text nodes identical; the 6 differing nodes are the task-row cells' padding (design: flex rows with padding on the row, built: table cells), the known structure difference above. Two names differ because the seed used other people |

Not checked: Gmail, Outlook, Apple Mail, a real send (rendering was the app's HTML in one desktop browser).
