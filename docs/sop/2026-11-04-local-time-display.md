# SOP: Times on the web pages in the viewer's own time zone

## What shipped
Every date and time the Next.js pages show is now written in the viewer's browser time zone, with a short zone label, instead of UTC. Branch `feature/local-time-display`, PR against `main` (not merged). In Lagos "02 Nov 2026, 21:16 UTC" now reads "02 Nov 2026, 22:16 GMT+1". No design change: same words, same layout, only the zone label and the hour differ. No API, Python or migration change.

## Why
Adebayo (Lagos, UTC+1) saw every time one hour behind his clock. `formatTime` and `formatDay` in `web/lib/format.ts` read `getUTC*` on purpose so the server render and the browser agreed; the price was UTC for everyone. The Upcoming list was already local.

## How
| Piece | Decision | Why |
|---|---|---|
| Formatters | `formatTime(iso, timeZone = "UTC")` and `formatDay(iso, timeZone = "UTC")` read the wall-clock fields through `Intl.DateTimeFormat` with `timeZone`, as numbers, and map month and weekday through the existing `MONTHS` and `DAYS` tables. The zone label is `timeZoneName: "short"` in `en-GB`: "UTC", "GMT+1", "GMT-5", "GMT+5:30" | Same words as before; ICU's own month words are never used ("Sept" in en-GB would have crept in). One label style for all zones, never "EST" for some and "GMT+1" for others |
| Day-only form | "Mon 2 Nov 2026", no zone label, as before | A day carries no zone in the Jinja pages or the design |
| Hydration | New client component `web/components/app/local-time.tsx`: `<LocalTime iso kind="datetime"\|"day" />` renders `<time dateTime={iso}>`. It uses `useSyncExternalStore(subscribe, () => true, () => false)`: React answers `false` while hydrating (the server's UTC text, so no mismatch) and `true` right after (text rewritten in the browser's zone). `browserTimeZone()` is `Intl.DateTimeFormat().resolvedOptions().timeZone`, UTC if the browser will not say | One approach everywhere; the same hook pattern `calendar-upcoming.tsx` already used. Rejected: `suppressHydrationWarning` (hides real bugs), a `useEffect` + `useState` swap (an extra render and a lint rule against setting state in effects), a cookie with the zone (extra moving part, first visit still wrong) |
| Day comparisons | Only the Upcoming list compares days ("Today", "Tomorrow"); it already used the viewer's zone for both the label and the time, and now takes its weekday and month from the same helper. Decisions and Review group by meeting (the API does that), so the day is a label per meeting, local like the time | A meeting at 00:30 Lagos is under its Lagos day in both the label and the time |
| Tests | `vitest.config.mts` sets `process.env.TZ = "UTC"` so no test depends on the machine's zone; `tests/local-time.test.tsx` sets `TZ` itself for the zone it is about | The suite passed in UTC and `Asia/Tokyo` machine zones when checked |
| Left as UTC on purpose | `/styleguide` sample row text; mail and the old Jinja pages (`app/web.py` `local_time`) | The styleguide text is a fixed specimen; mail and Jinja are Python, out of scope |

## What's involved
| Path | Change |
|---|---|
| `web/lib/format.ts` | `formatTime`, `formatDay` take a zone; `browserTimeZone`, `SERVER_ZONE`; `formatEventDay` weekday and month through the same helper |
| `web/components/app/local-time.tsx` (new) | the `LocalTime` component |
| `web/components/app/{meeting-list,meeting-view,notifications-view,decisions-view,review-view,settings-calendar}.tsx` | `formatTime` / `formatDay` calls replaced by `<LocalTime>` (settings: "Last updated" followed by the element) |
| `web/tests/local-time.test.tsx` (new), `meeting-view`, `settings-calendar`, `server-bits` tests | new and adjusted tests |
| `web/vitest.config.mts` | `TZ=UTC` |
| `web/scripts/parity-shots.mjs`, `parity-local-time.mjs` (new) | `--tz` option and console-error printing; Lagos-versus-UTC layout check |
| `docs/ui/parity/after/local-time-{lagos,utc}/` | 24 screenshots each |

## Parity
Built app (`next build`, standalone) against the mock API (`--calendar connected`), the designer's file beside it, browser zone forced with Playwright `timezoneId`. Screens: Meetings, meeting detail (ready, processing, waiting, failed), Meetings with calendar, Settings (connected), Notifications; each at 1440, 768 and 390 px.

| Check | Result |
|---|---|
| Lagos run versus UTC run, 7 pages x 3 widths (`parity-local-time.mjs`) | Every `<time>` shows the right zone ("15:30 UTC" becomes "16:30 GMT+1"); no element moves down or grows taller; widths of boxes that shrink-wrap a time change by a few px, as text does |
| One exception | Notifications at 390 px: the long line "Wed 4 Nov 2026, 15:57 GMT+1 · Open meeting" reflows (the link sits on one line instead of two). Plain text wrapping, not a layout change |
| Console on every page in both zones | No errors, no React hydration (#418/#423) message |
| Versus the designer's layout | Unchanged: the shots in `local-time-*` are the design on the left and the page on the right, as in the earlier parity report; the only difference this change adds is the zone label text |
| Not covered | Decisions and Review (the mock has no fixtures for them): covered by `decisions-view` and `review-view` unit tests and by the same component |

## Verification
| Check | Result |
|---|---|
| `npm run lint`, `npm run typecheck` in `web/` | clean |
| `npx vitest run` | 536 tests pass, also with the machine zone set to `Asia/Tokyo` |
| Formatter tests | UTC; `Africa/Lagos`; `America/New_York` either side of the 2026-11-01 clock change (GMT-4 then GMT-5); `Asia/Kolkata` (GMT+5:30, next day); 23:30 UTC is already the next day in Lagos; year boundary; "Sep"; midnight as 00:00 |
| Component tests | `<time datetime>` keeps the instant; UTC on the server render, hydration with no `console.error`, then the browser's zone |
| Browser | parity run above, two zones, three widths |

## Operate / roll back
Nothing to deploy beyond the normal web image; no server step, no env. Revert the PR to go back to UTC everywhere.

## Follow-ups
- Mail (reminders, receipts, notices) and the old Jinja pages still say UTC. Fixing them needs a stored per-user time zone (an API field and a mail change), which is Python work and a decision for the designer.
- The designer has not drawn a "Time zone" row in Settings; do not add one until they do. A stored zone would also let a person see another zone than their browser's.
- The zone is the browser's: someone on a shared computer or a VPN sees that machine's zone. Not a bug, but the reason a stored setting may be asked for.
