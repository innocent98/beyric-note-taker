# Answer key: planted_meeting.txt

A made-up meeting with known facts and traps, for comparing notes models. All names are fictional.

| Check | Expected in the notes |
|---|---|
| Launch date | Moved to 14 March (agreed by Adebayo and Grace) |
| Budget | Capped at £4,000; anything above needs Grace's sign-off (decision) |
| Email provider | **Not decided.** Postmark vs Resend is a test, not a decision. Inventing a choice is a failure |
| Contract | Tunde sends the revised contract by Friday |
| Email test | Amaka tests both providers with the domain by Wednesday |
| Venue | Amaka books it; Grace prefers Liverpool Street |
| Welcome packs | 1,500 packs (30 boxes). "Fifty" was a slip; reporting 50 packs is a failure |
| VAT | Must be **unassigned**; giving it an owner is a failure |
| Contractor | Parked or open, not decided |
| Typo | "Burek" on the login page should be "Beyric"; Amaka fixes it today |
| Travel | Grace away next week; urgent items before Thursday |
| "Please check" | Only genuinely unclear items; restating clear facts there is a minor fault |

## Owners and dates (the extraction gate, spec section 3.1)

The meeting is dated **Monday 2027-02-01** (`Europe/London`, as in `extraction/planted.json`, which
scores this table). A name that is only mentioned is never an owner; a vague timing has no date.

| Action item | Owner (as said) | Due phrase | Due date |
|---|---|---|---|
| Send the revised contract | Tunde | "by Friday" | 2027-02-05 |
| Test both email providers with the domain | Amaka | "by Wednesday" | 2027-02-03 |
| Book the launch venue | Amaka | none | none |
| Check whether VAT applies to international customers | **none** (nobody took it) | none | none |
| Fix "Burek" on the login page | Amaka | "today" | 2027-02-01 |
| Pay the venue deposit | Amaka | "by the nineteenth of February" | 2027-02-19 |
| Mobile app estimate | Tunde | "in the coming weeks" | **none** (vague) |
| Daniel (payment provider) confirms sandbox settings | **Daniel is never an owner**: he was only mentioned | none | none |
