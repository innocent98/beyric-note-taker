# SOP: Decisions and Review states, and the Settings time zone

## What shipped
The states the designer added in PR #80 to **Decisions** and **Review** (loading, failed, empty, the remove ask and toast, the rewrite offer, per-row errors), the **Settings > Time zone** card, and a stored per-person zone that the notes-ready mail and the web pages use. Branch `feature/decisions-review-timezone`, one PR against `main` (not merged). Migration **0018**.

## Why
PR #56 asked the designer for the missing states; #80 drew them and the Time zone row. Before this, mail was always UTC and the pages used the browser's zone, with nothing the person could choose.

## How
| Piece | Decision |
|---|---|
| Storage | `user_settings.time_zone`, nullable `varchar(64)`, null = UTC as before (`alembic/versions/0018_user_time_zone.py`). No backfill, round trip tested |
| API | `GET` and `PUT /api/settings/time-zone` (`app/api_settings.py`); any IANA name `zoneinfo` knows, else 422 `time_zone_unsupported`. `user.time_zone` (null until chosen) added to `GET /api/session` so pages know it without another call |
| Mail | `app/timezones.py` (`localise`, `label`); `mail_templates.when_line(meeting, zone)` writes "06 Nov 2026, 00:30 GMT+1". Labels are "UTC", "GMT", "GMT+1", "GMT+5:30", the same style as the pages |
| Pages | `AppShell` takes `timeZone` and wraps children in `TimeZoneProvider`; `LocalTime` uses it when set (also on the server render), else the browser's zone as before. Passed by Meetings, Notifications, Decisions, Review, Settings pages. The design says "Meeting times ... use this time zone", so the stored zone wins over the browser |
| Settings card | `web/components/app/settings-time-zone.tsx`: the designer's 18 zones plus the current one if absent, "(UTC+1) Lagos" labels, local time, "Use my device", the two toasts, `router.refresh()` after a save |
| Decisions | Subtitle, "Loading decisions..." in place of the list, failed state with Try again (also when the first server load fails: the page no longer shows the error page), new empty text, inline ask "Remove this decision from the log? It stays in the meeting notes." with Cancel / Remove, toast. The hide API already existed (`POST /api/decisions/{id}/hide`, owner-scoped, notes untouched), so none was added |
| Review | Loading, failed (also on first load), new empty and subtitle texts, single rewrite offer (Not now / Rewrite notes, working and done texts, Dismiss), per-row red lines with the red input border, "Remove from list" for a gone item, checked rows with meeting, day and Open transcript |
| Rejected | Reading the zone in the root layout (would make every public page dynamic); a cookie for the zone (extra moving part) |

## What's involved
`alembic/versions/0018_user_time_zone.py`, `app/{models,timezones,api,api_schemas,api_settings,mail_templates}.py`, `docs/openapi.json`, `web/lib/api/{client,server,types}.ts`, `web/lib/api/schema.d.ts`, `web/components/app/{settings-time-zone,time-zone-context,local-time,shell,decisions-view,review-view}.tsx`, `web/app/{settings,decisions,review,notifications}/page.tsx`, `web/app/page.tsx`, `web/styles/pages.css`, `web/components/ui/icon.tsx` (spinner, rewrite), mock API and parity scripts, tests `tests/test_time_zone.py`, `tests/test_migrations.py`, `web/tests/{settings-time-zone,decisions-view,review-view,decisions-review-shell}.test.tsx`, capture script `scripts/capture_time_zone.py`, FE guide `docs/fe-integration-guide-settings-time-zone.md`.

## Parity
`web/scripts/parity-shots.mjs` against `mock-api.mjs`, design (left) beside the page (right), 1440 / 768 / 390 px, in `docs/ui/parity/after/decisions-review-timezone/{live,failed,empty,loading}/`. The designer's Failed, Empty and Loading states are reached by serving a copy of the design with the "Preview states" prop default changed.

| Screen | Result |
|---|---|
| Decisions: live, remove ask, removed + toast | matches layout, words and dates with year |
| Decisions: failed, empty, loading | matches |
| Review: live, offer, empty term, duplicate, gone, loading, failed, empty | matches (see gaps) |
| Settings Time zone, after choosing a zone | matches; the offsets and clock appear after load |

## Gaps for the designer
| Gap | What was built |
|---|---|
| The design's Review row differs from the API: the design's sample items link to meetings in its own data | Real data shown; layout identical |
| The offer in the design has no line about removed decisions and checked items coming back (FE guide AI-workspace section 6) | Kept as a second line in the offer: a real data-loss warning. Designer to place or drop it |
| Rewrite failure and "cannot be rewritten now" texts | Not drawn; the API's message or "These notes cannot be rewritten right now." shows in the offer |
| Duplicate term: the API has already checked the item | The design shows only the red line; the item moves to Checked after a reload |
| First run: nothing chosen yet | The select shows UTC (what mail uses) while pages use the browser zone. Designer to say what a new account should see |
| Time zone save failure | General error line under the select |
| Decisions search with no match | "No decisions match your search." (earlier gap, unchanged) |

## Verification
| Check | Result |
|---|---|
| `ruff check`, `ruff format --check` | clean |
| pytest | `tests/test_time_zone.py` (8), `test_migrations.py`, `test_accounts.py`, `test_mail_upgrade.py`, `test_email_prefs.py`, `test_api.py` pass; `alembic heads` one head (0018) |
| web `lint`, `typecheck`, `vitest run` | clean; all tests pass |
| Live capture | `scripts/capture_time_zone.py`: real app, Postgres 17, mail in UTC, Lagos, Kolkata |
| Not verified | Pages in a browser against a live FastAPI (mock API only); mail in a real client; a successful rewrite from the offer |

## Operate / roll back
**Server steps before merge:** migration 0018 runs on deploy (additive, nullable column); nothing else. Roll back by reverting the PR and `alembic downgrade 0017` (drops the column; chosen zones are lost).

## Follow-ups
- The meeting detail page (parallel session) must pass `timeZone={await getTimeZone()}` to `AppShell`, otherwise its times keep the browser's zone.
- In-app notification texts written by the join runner ("... by 14:00 UTC") are still UTC.
- Reminder mails of future features (sprints, tasks) should call `timezones.localise`.
