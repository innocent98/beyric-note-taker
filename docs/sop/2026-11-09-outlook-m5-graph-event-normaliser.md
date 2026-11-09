# SOP: Outlook phase O, M5, the Graph event normaliser

## What shipped
`app/graph_events.py`: one raw Microsoft Graph calendar event to the shared `ParsedEvent` (Outlook spec section 5), so the sync, the rules (`gcal_rules.will_join`), the joins and the screens read Microsoft rows exactly as they read Google ones. Branch `feature/outlook-m5-graph-events`, PR to `main` (number in the PR list). Pure module: no database, no network, no logging, no route, no migration, no behaviour change, no Outlook wording. **Nothing registers it yet:** the sync already picks the parser by `conn.provider` through `calendar_providers.parse`, so the `microsoft` registry row (added by M3 with its routes and source) is the only dispatch change left. A test registers a temporary row and runs a Graph batch through the real `apply_batch`.

**Fixtures are documented shapes, not captures.** The M0 run has not happened. Every event in `tests/fixtures/graph/` is `documented_*__replace_with_m0_capture.json`; the test module says so at the top. Assumptions are listed under "To confirm with the M0 run".

## Why
Anyone can send an invite, so the title, body and locations are attacker-controlled; the shared event shape must come out the same whatever the provider, because the unanswered-invite safeguard and the "cannot join" words depend on it.

## How (spec 5.3 to 5.5)
| Topic | Rule |
|---|---|
| Times | `start` and `end` must say `timeZone: "UTC"` (the sync asks for it with `Prefer: outlook.timezone="UTC"`). Any other zone, a missing `dateTime`, an unparseable date or a non-zero offset raises `EventParseError` (the sync's existing "unreadable" path: logged by type, never cancels a stored meeting). **No Windows zone name is ever mapped.** The seven-digit fraction (`2026-11-05T10:00:00.0000000`) is tested with the exact string |
| All-day | `isAllDay` true gives `all_day=True` with the midnight instants as sent; `will_join` answers `all_day`. Never joined, so the documented "zone not honoured" quirk cannot reach a join |
| Fields | `id` to `source_event_id` (immutable ids, requested by M4); `seriesMasterId` to `recurring_event_id`; `subject` to `title` (the sync cleans and cuts it); `isCancelled` true to `cancelled`; `event_type` always `default` (every Graph `type`); `isOrganizer` to `organizer_self` (a missing flag on an event with other people reads as False, the cautious side); `isDraft`, `sensitivity`, `showAs` ignored (parity with Google) |
| Reply | `accepted` to `accepted`; `tentativelyAccepted` to `tentative`; `declined`; `notResponded` to **`needsAction`**; `organizer` to `accepted`; `none` and anything unknown or unreadable to None. A reply we cannot read is never "accepted" |
| Attendee count | `len(attendees) + 1` when `attendees` is non-empty, else 0 (ASSUMED: Graph leaves the organiser out) |
| Same organisation | The organiser's address domain is one of the connection's `tenant_domains` (whitespace, comma or semicolon separated, case-insensitive, exact domain only: no subdomain, no look-alike). The caller passes domains only for a work account; always false when empty. The address is never kept. **Review focus 8 (an organiser address inside the tenant's domain is only as trustworthy as Exchange Online's anti-spoofing, unconfirmed): M3 reads `tenant_domains` only when `MICROSOFT_ORGANIZER_DOMAIN_TRUST` is on (default off), so in production this stays false and an unanswered colleague invite waits for "accept" until the M0 run (S4, S5) says otherwise** |
| Meeting link, first usable wins | (1) `onlineMeeting.joinUrl` when `isOnlineMeeting` is the boolean true; (2) `location.displayName` and up to 20 `locations[].displayName`; (3) `body.content` (HTML kept so a hidden `href` is found; at most 200,000 characters scanned). Each goes through the existing `_classify` (Zoom, Google Meet, Teams hosts; https; no credentials; no port). The provider string never decides the platform |
| Safe Links | A link whose host is under `.safelinks.protection.outlook.com` (https only) is unwrapped ONE level from its `url` query value, and the target must pass `_classify` too; a look-alike host, `http`, a double wrap, or a target that is not a meeting link is ignored |
| Rejected | Converting Windows zone names; unwrapping Safe Links anywhere but the exact suffix; reading `text` bodies (they drop the hidden `href`); marking `showAs` free or `oof` as a skip type (it would hide real meetings); trusting `isOrganizer` when it is absent |

## What's involved
New: `app/graph_events.py`, `tests/test_graph_events.py` (151 cases), `tests/fixtures/graph/documented_*__replace_with_m0_capture.json` (12 files). No other file changes besides the checklist.

## Verification
- The table of spec 5.4 against `will_join` (26 rows: rule `all`, `organizer`, `none`; Teams switch on and off; an unanswered stranger invite waits (`not_accepted`), a colleague's joins when the organiser's domain is in `tenant_domains`, a look-alike domain does not, an unreadable reply or a lost organiser flag never becomes "mine"), reply mapping (8 values), time refusals (zones, shapes, offsets), link order, 10 rejected links, the scan limit, 12 odd field shapes, 11 Safe Links look-alikes, and a Graph batch through `apply_batch` (a Zoom-in-body event joined; a stranger's unanswered invite stored but not joined; Teams "cannot join"; all-day not joined; an event in a Windows zone skipped as unreadable while the rest are applied).
- `ruff check .`, `ruff format --check .`, `test_graph_events`, `test_gcal_events`, `test_gcal_sync`, `test_gcal_rules`, `test_calendar_providers`, and `pytest --collect-only -q tests`. CI runs the rest.
- **Not verified live:** no real Graph event has been read.

## To confirm with the M0 run
1. `start` and `end` come back as `{"dateTime": "...0000000", "timeZone": "UTC"}` with BOTH `Prefer` values in one request (spec S3), including for all-day events and for events created in another zone.
2. `attendees` omits the organiser (the `+ 1` rule): count a meeting with known people.
3. The reply values for each case: invitee who has not answered (`notResponded`), the organiser's own event (`organizer` or `none`), a meeting with no invitees (`none`), a declined invite.
4. Where Zoom and Meet links really sit (body, `location`, `locations`, or `onlineMeeting`); whether `Calendars.ReadBasic` also returns them (the scope decision, S3).
5. Teams join-URL formats, work and personal (`teams.microsoft.com/l/meetup-join/...`, `teams.live.com/meet/...`), and whether `isOnlineMeeting` is true for a personal Teams link.
6. Safe Links in a real work event (S5): host, the `url` parameter, HTML-escaped `&amp;`.
7. `$select` returns every field used here (`isOnlineMeeting`, `onlineMeeting`, `locations`, `seriesMasterId`, `type`).
8. Review focus 8: whether to keep the same-organisation test for work accounts (relies on Exchange Online's anti-spoofing of the organiser address) or leave `organizer_same_org` false for Microsoft; needs `/organization` `verifiedDomains` with `User.Read` only (S4).

## Operate / roll back
**Server steps before merge: none** (a pure module nothing calls yet). Roll back by reverting the PR.

## Follow-ups
- M3 adds the `microsoft` registry row whose `parse_event` calls this with `tenant_domains` for a work connection only; M4 sends the `Prefer` headers and the `$select` this module reads.
- Replace the 12 fixtures with the redacted M0 captures and settle the list above.
