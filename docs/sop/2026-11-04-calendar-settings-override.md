# SOP: Calendar settings write and the per-meeting switch (C15a slice 2)

## What shipped
The rest of the controls the designer drew on the Calendar screens, with the backend they need: `PUT /api/calendar/settings` (the auto-join rule and the "Template for calendar meetings"), `PUT /api/calendar/events/{id}/override` (the switch on each Upcoming row), and the three pieces of the designer's UI that were missing: the **"Join automatically" radios** now write, the **"Template for calendar meetings" select** is drawn, and every **Upcoming row has the 36px switch** with the designer's state words and hints. Branch `feature/c15a-settings-override`, PR in the report. **No migration, no new setting, no new variable, no nginx change.** Everything stays dark behind `CALENDAR_ENABLED` (false in production until Adebayo turns it on; he has it on for the owner already, see the handoff). **No bot is sent by anything here**: the routes change what the rows say, C12 sends.

## Why
Adebayo compared the live Settings and Meetings screens with the designer's and found the template select and the per-event switch missing (plus the read-only radios). The rule and template could not be changed at all, and an invite that waits for acceptance had no one-click way in.

## How
| Piece | Decision | Why |
|---|---|---|
| Where the settings live | `user_settings.auto_join_rule` and `user_settings.calendar_template_id`, both already in the schema (migration 0006, sign-up gives every user a row). The route creates the row if it is missing (safety net) | No migration needed |
| One route, partial body | `PUT /api/calendar/settings` takes `{rule?, calendar_template_id?}`: a field left out stays; at least one is needed (422 otherwise); `rule: null` is refused; unknown fields refused. It answers with the **whole `CalendarOut`**, so the page redraws from one answer | The rule changes the Upcoming words too |
| `calendar_template_id` | Added to `CalendarOut` (GET and PUT). The person's **own** template, or `null` for the default. A foreign or unknown id is **404 `not_found`** (`services.template_or_404`, the same refusal as every other template route), checked **before** anything is stored, so a rule sent in the same body is not applied either. Sign-up already stores the General copy (migration 0007), so real accounts show a concrete id; `null` only appears after an explicit null | Never reveals that another user's id exists |
| The rules stay in one place | New `gcal_sync.rerun_rules(session, conn, user_settings, events, now=...)` builds the same `_Run` the sync uses and calls the existing `_reconcile` per event, then commits. **No rule logic is copied**: `will_join` (`gcal_rules.py`, untouched) decides, `_reconcile` creates, revives, moves or cancels the `scheduled_joins` row. C12 reads those rows | The C8 SOP named `_reconcile` as the function this would call |
| Rule change | If the rule **differs** from the stored one: clear `override` on the person's events that **have not started** (`starts_at > now`; the designer's prototype clears them on a rule click; a meeting in progress keeps its switch), then reconcile every event of the connection that is not cancelled and not over. Choosing the rule already set changes nothing. A join whose bot is in (`joined`, `completed`, `failed`) is never touched (existing `_reconcile` rule: the meeting is the truth) | Spec 7.7, 13 |
| Template change | New `gcal_sync.set_join_template`: records the template on the person's calendar joins in `scheduled`/`no_capacity` (no bot sent yet); a join whose bot is in keeps the template it was sent with. Written before the reconcile, so a join made by the same call gets it | The sync SOP noted the template was only captured when a join row was made |
| Per-meeting switch | `PUT /events/{id}/override` body `{"override": true\|false\|null}` (required key, strict booleans). The event must be the caller's **and** belong to their live calendar connection, else **404 `not_found`** (also after a disconnect, the events are gone). It answers with the event's new `UpcomingEvent` row | |
| 409 `not_joinable` | Decided by running `will_join(row, True, ...)`: with the switch on, only what the event itself is can still say no (declined, over, cancelled, all-day, other event type, too long, no link, Teams while `TEAMS_JOIN_ENABLED` is off). `message` is that reason in the server's words; nothing is stored. Applies to `true`, `false` and `null` alike | The spec's "declined, ended, Teams, no link" |
| The unanswered-invite rule is unchanged | `gcal_rules.py` was not touched. An unanswered invite joins only if the owner organised it or the organiser shares their domain; otherwise `Off` / "Waiting for you to accept the invite", and **the switch is the one-click way in** (override `true` beats the rule and creates the join). A test pins all three cases | The brief |
| Frontend: radios | Real `<input type="radio">` inside the designer's rule cards (the ring, dot, colours and sizes are the design's: `.cal-rule`, `.cal-radio`), keyboard operable, focus ring. Pressing one shows it chosen at once, holds the controls until the answer comes, then shows the answer's rule; on failure it goes back and says why. The same rule pressed again sends nothing | |
| Frontend: select | `SelectField` (the designer's 40px select, 320px wide, label "Template for calendar meetings") under the rules; options are the person's templates (`api.templates.list()`, loaded by `app/settings/page.tsx`); drawn only when connected and the person has templates. While no template is chosen it shows the person's **first (oldest)** template, which is not the first entry of the list (that is sorted by name) | |
| Frontend: switch | `calendar-upcoming.tsx`: a `button role="switch"` per row, holding the state words and the 36×20 track with the 16px knob (`.cal-up-switch`, `.cal-switch`). On for `Will join`, off for `Off`; **`Cannot join` is dimmed to 0.55, `aria-disabled`, does nothing, hint on hover** (the design's `u.hint`). Pressing shows the new position at once, then the row is replaced by the server's answer (words and reason included). Name for assistive tech: the title and the state words | |
| Errors in plain words | Network: "Could not reach the server. Check your connection and try again." A 409: the server's reason (e.g. "You declined this invite") and the list is **read again** so the row says so. 404 and 422 never show their developer text: "Something went wrong. Please try again." The switch or radio goes back to where it was | `errorMessage` in `lib/api/client.ts` |
| Rejected | Instant-Meet switch and 409 `consent_required` (C15a part 3, the Meet part lights up in I1); `GET /api/calendar/upcoming` (7 days plus 24 hours); an optimistic write without holding the control (two fast presses would race); a polling refresh | Not asked for / later |

## What's involved
| Path | Change |
|---|---|
| `app/calendar_api.py` | `PUT /settings` (`calendar_settings`, `SettingsIn`), `PUT /events/{event_id:id}/override` (`calendar_event_override`, `OverrideIn`), `calendar_template_id` on `CalendarOut`; `_calendar_out`, `_upcoming_item` and `_join_settings` factored out of the GET |
| `app/gcal_sync.py` | `rerun_rules`, `set_join_template` (no change to `apply_batch`, `_reconcile` or the rules) |
| `docs/openapi.json`, `web/lib/api/schema.d.ts` | regenerated |
| `web/lib/api/{client,types}.ts` | `api.calendar.settings`, `api.calendar.override`, `CalendarSettingsInput`, `AutoJoinRule` |
| `web/components/app/settings-calendar.tsx`, `web/app/settings/page.tsx` | radios, select, the templates read |
| `web/components/app/calendar-upcoming.tsx` | the switch |
| `web/styles/pages.css` | `.cal-rule-input`, `.cal-template`, `.cal-up-switch`, `.cal-switch`, `.cal-up-error` (tokens only: `--shadow-knob`, `--border-strong`, `--teal`) |
| `web/scripts/mock-api.mjs` | replays the write captures for the parity run |
| `scripts/capture_calendar_settings.py`, `docs/fe-captures/calendar-settings/` (44 files) | captures of the write routes; `capture_calendar_state.py` now sets the rule through the real PUT; `docs/fe-captures/calendar-state/` regenerated (every body now has `calendar_template_id`) |
| `tests/test_calendar_settings_api.py` (63), `tests/test_calendar_api.py` (one pinned body), `web/tests/{settings-calendar,calendar-upcoming,api-client,settings-page,helpers}` | tests |
| `docs/ui/parity/after/app-{settings-connected,meetings-calendar}-{1440,768,390}.jpg` | design left, built right |
| `docs/fe-integration-guide-calendar-settings.md`, `docs/sop/2026-11-04-calendar-settings-page.md` (gaps and follow-ups), `docs/checklist/master-checklist.md` | docs |

The route enumeration tests in `tests/test_api.py` (login, CSRF, no cross-wiring) pick both routes up by themselves.

## Parity with the designer's file
`docs/ui/parity-report.md` method: the built app (`next build`, standalone) against the mock API replaying the captures, beside `Beyric Note Taker.dc.html` in Chrome at 1440 (positions and computed styles diffed), 768 and 390 (images). Design and built numbers below are from that run.

| Element (1440) | Design | Built | Result |
|---|---|---|---|
| "Join automatically" label | 674 wide at x 311, 13/550, `#2E3532` | same | identical |
| Rule card, chosen / not chosen | 674×65, padding 12/14, 10px radius; border `#0E6B5E` and bg `#F3F9F7` / `#E3E7E5` and white; cursor pointer | same | identical |
| Radio ring and dot | ring 18×18 at (15, 14) in the card, 1px computed border `#0E6B5E` / `#C3C9C6`; dot 8px `#0E6B5E`, opacity 1 / 0 | same | identical |
| Rule label (14/550) and description | x 45 in the card | same x; line box 21px against the design's 18px (inherited `line-height`, no change to the card's 65px) | text lines within 1 px, as the earlier slice |
| "Template for calendar meetings" label | 13/550 at (311, 558), 320 wide | same position and size | identical |
| The select | 320×40 at (311, 584), padding 0/36/0/12, 1px `#D5DBD8`, 8px radius, 14px `#141917` | same box, padding, border, radius, colour; chevron from `/icons/chevron-down.svg` (the CSP allows no data URI) | identical (the drawn chevron is the same shape) |
| Upcoming switch, Will join | group 89×20, track 36×20 `#0E6B5E` radius 999, knob 16px at offset (18, 2), word 12px `#5B6460`, gap 10 | same | identical |
| Upcoming switch, Off | group 63×20, track `#D5DBD8`, knob offset (2, 2) | same | identical |
| Upcoming switch, Cannot join | group 108×20, opacity 0.55, cursor default, hint "Add a meeting link to this event to record it" | same | identical |
| Hint on an `Off` row | **none** (the design's hint is only for rows without a link) | the reason ("Waiting for you to accept the invite", "You switched this meeting off") on hover | **deviation, kept**: it is where an unaccepted invite is explained; designer to accept or drop |
| 768 and 390 | the prototype's app is not responsive | the app's engineered layout; the switch stays on the row's right | not comparable, as every screen |

Positions in the two rows above differ in y only because the prototype and the app have different content above the list (the design's own New meeting card and sample rows).

## Verification
| Check | Result |
|---|---|
| `tests/test_calendar_settings_api.py` | 63 tests on a real Postgres 17 (`nt-test-pg-c15`), written first (55 failed, the 8 that passed were the 401/404/CSRF ones answered by middleware). The plan's names: `test_rule_change_clears_overrides_on_events_not_started`, `test_foreign_event_id_is_404_for_override`, `test_override_on_declined_or_ended_or_teams_flag_off_is_409_not_joinable`; plus foreign and unknown template refused with nothing stored, the rule in the same body not applied, partial bodies, creating the settings row, joins cancelled and the same row revived, a join made for an event that never had one, a join whose bot is in left alone, template reaching `scheduled` and `no_capacity` joins but not `joined`, another user untouched, the unanswered-invite cases |
| Mutation checks (each broke a named test) | override clearing removed; the 409 check removed; the template ownership check removed; the event owner filter removed; override clearing widened to events already started; the template not propagated to joins; "rule changed" decided without comparing. **Survived by design:** dropping the owner filter inside `rerun_rules` (every caller already passes the person's own rows; it is a second guard) |
| `ruff check`, `ruff format --check` (pinned 0.17.3, own venv), `export_openapi.py --check` | clean |
| Python: `test_calendar_settings_api`, `test_calendar_api`, `test_calendar_connect`, `test_api` (enumeration), `test_gcal_sync`, `test_gcal_sync_job` | 407 pass |
| `npm run lint`, `npm run typecheck`, `npx vitest run` in `web/` | clean; 445 tests. New: radios (chosen, write, held while saving, no call for the same rule, network, 409-style, developer text hidden, keyboard), select (options, default, chosen, write, rollback, 404 words, not drawn without templates), switch (states, labels, write on and off, optimistic position, ignored second press, `Cannot join`, 409 with a re-read, 404 and network words, keyboard), client calls, the page's templates read. Fixtures are the real captures |
| Capture against the running app | `scripts/capture_calendar_settings.py` (44 files) and `capture_calendar_state.py` (21): the real sync job over `FakeGoogleCalendar`, real migrations, real HTTP. The `scheduled_joins` rows after each write are in the captures (cancelled with `rule_none`, the same row revived to `scheduled`, the template on `scheduled` joins) |
| Built app, real click flow | A keyboard press on a rule in the built app against the mock: the rule moved and the call went out |
| **Not verified** | A real Google calendar (everything above is a fake Google); Safari and Firefox; the live site; the web mutation checks (none run on the new components); that C12 reads the rows as written here (C12 is not built); a real bot |

## Operate / roll back
- **Server steps before merge: none.** No variable, migration or nginx edit. Merging deploys `web` (and the API); with `CALENDAR_ENABLED=true` (as Adebayo has it) the controls work at once for connected users. Changing the rule or a switch **changes `scheduled_joins` rows only**: no bot goes anywhere until C12.
- Roll back: revert the PR. The stored rule, template and overrides stay in the database and are read as before; nothing is migrated.

## Designer gaps (for the designer to review)
Only what this slice added; the older list is in `2026-11-04-calendar-settings-page.md`.
| Gap | What was built |
|---|---|
| The design's `Off` rows have no hint | The reason on hover (see the parity table) |
| No error or saving state for the radios, the select or the switch | The control is held while the call is out; a red inline message at the top of the card (Settings) or of the Upcoming card (Meetings) says why it went back |
| What "no template chosen" shows | The oldest template (General). The design has only a fixed "General" |
| A switch that the server refuses (409) | The red message with the server's reason, and the row is redrawn from a fresh read |

## Follow-ups
- **C12** must read `scheduled_joins` as written here (cancelled with the reason, revived rows) and resolve `template_id = null` to the person's first (oldest) template; the plan's `state_text` for scheduled-join states (hours used, in progress, not recorded) is still to come.
- The rest of C15a: `join_instant_meets` and the 409 `consent_required` (with the Meet part), `GET /api/calendar/upcoming`.
- An event that is already past its join deadline (start + 15 min) and still going shows `Will join` after a switch on, but `_reconcile` makes no join for it (spec 7.7: "In progress"). The Upcoming list does not say so.
- Web mutation checks on the new components; Safari and Firefox.
