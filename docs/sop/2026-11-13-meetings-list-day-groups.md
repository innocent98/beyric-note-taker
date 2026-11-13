# SOP: the Meetings list is split by day

**What shipped.** A heading above each run of meetings from one calendar day on the Meetings page (`Today`, `Yesterday`, then `Mon 9 Nov`, with the year only when it is not this year), in the owner's own time zone. Branch `feature/meeting-list-day-groups` (PR opened 2026-11-13, not merged by this work). Web only: no API, migration or server step, no FE guide change.

## Why

Adebayo found the list a single run of rows with nothing to tell one day from the next.

## How

| Decision | What and why |
|---|---|
| Which time | `created_at`, the only time `MeetingSummary` carries and the one the row already shows (`LocalTime iso={m.created_at}`). A meeting is under the day its own row says |
| Which zone | `useDisplayTimeZone()` (new, in `components/app/local-time.tsx`; `LocalTime` now uses it too, behaviour unchanged): the zone chosen on Settings (`TimeZoneProvider`, from `user.time_zone`, #82), else the browser's once in the browser, UTC until then. So a heading can never disagree with the times under it, and neither the server's zone nor the browser's is used by accident |
| The rule | `web/lib/day-groups.ts`, a pure function: `groupByDay(items, timeZone, now)`. The calendar day is read from numbers (`Intl` parts, as `format.ts` already does), not words; "Yesterday" is calendar arithmetic, not "24 hours ago", so a 23 or 25 hour day (clock change) is right. One group per day, kept in the order the days first appear. A day therefore has one heading and one React key even if two of its meetings were ever apart in the list (ids and times can differ by a moment around midnight). A timestamp that is not a date stays with the group before it, or heads "Date unknown" if first (the API never sends one; this only means nothing is lost) |
| Across pages | The groups are made from the whole list (first page, then every older page), so a day that runs over a page boundary continues under its heading. A live refresh swaps the first page; groups are keyed by day, so headings that were there stay and a new top meeting adds `Today` only if there is none |
| "Today" moving on | `useNow()` renews the clock each minute and when the tab comes back into view, so a page left open overnight moves on. The heading has `suppressHydrationWarning`: the server and the browser read the clock a moment apart and could differ only if midnight falls between the two |
| Markup and style | Design: the designer's file has no date groups on the Meetings list (searched), so this is **lead-designed at Adebayo's request**. A real `<h3>` (under the card's `<h2>Recent`) and the day's `<ul>` named by it (`aria-labelledby`), no landmark per day; keyboard order is the same as before (headings are not focusable; rows are in the API's order). `.day-head` in `styles/pages.css` is the look of the existing `.count-line` (soft bar, small muted text) a notch stronger; tokens only, no new colour or component |

## What's involved

`web/lib/day-groups.ts` (new), `web/components/app/meeting-list.tsx`, `web/components/app/local-time.tsx`, `web/lib/format.ts` (exports `zoned`, `DAYS`, `MONTHS`), `web/styles/pages.css`. Tests: `web/tests/day-groups.test.ts` (the rule: same day, midnight in Lagos and New York a second apart, the 25-hour day of 1 Nov in New York, Today and Yesterday decided in the owner's zone, month, year and leap-day boundaries, year only when different, a day split across two pages, a refresh with a new top meeting, a day with two apart meetings, an unreadable time), `web/tests/meeting-list-days.test.tsx` (the component: headings, lists named by them, link order, stored zone against the row's own time, Show older meetings continuing a day, refresh keeping the older headings).

## Verification

On the Mac, bounded: `npm run lint` (0 errors; 3 old warnings in `scripts/parity-meeting-delete.mjs`), `npm run typecheck`, `vitest run` on `day-groups`, `meeting-list-days`, `screens`, `local-time`, `guards`, `parity`, `decisions-view`, `review-view`, `meetings-calendar`, `meetings-recurring`. **Looked at in a real browser:** a throwaway page (not committed) rendered `MeetingList` with fixtures and the Lagos zone under `next dev`, at desktop width and at 375 px: the headings read Today, Yesterday, Mon 9 Nov, Sun 4 Oct, Tue 1 Jul 2025, the long titles wrap and nothing overflows. **Not looked at:** the real Meetings page with the real API and a signed-in session; a screen reader; dark mode (the colours are existing tokens, which the dark theme already defines).

## Operate / roll back

Nothing to run. Revert the PR to go back.

## Follow-ups

- **Not caused by this, found while reading the list:** when a first-page refresh arrives after "Show older meetings" was used, a new meeting pushes the first page's oldest row out of the first page, and the older pages start below it, so that one row drops out of the list until the page is reloaded (`meeting-list.tsx` merges `initial` and `older` without closing the gap). The headings stay right; the row is missing. A small fix (re-fetch from the first page's new `next_before_id`, or clear the older pages on refresh) is a separate change.
- Calendar "Upcoming" and Decisions have their own day wording (`formatEventDay`, `formatDay`); they were not changed.
