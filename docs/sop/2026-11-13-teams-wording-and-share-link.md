# SOP: the app says Microsoft Teams where it already works, and the phone share finds Teams links

**What shipped.** The app's own words now name Microsoft Teams next to Google Meet and Zoom, and the phone share target (`web/lib/share.ts`) finds a Teams link in shared text. Branch `fix/teams-wording-and-share-link` (PR opened 2026-11-13, not merged by this work). Web only, plus one label in the old Jinja form. No API, migration or server step, so no FE guide change.

## Why

Teams links have joined end to end since 2026-11-09 (#159: pasted link, Google Calendar events, guest join through Skribby, proved live). The app still said "Google Meet or Zoom" in five places, and sharing a Teams invitation from a phone to the installed app answered "could not find a meeting link", because the finder only knew Meet and Zoom.

## How

| Piece | What and why |
|---|---|
| Wording | "Google Meet, Microsoft Teams or Zoom", the designer's own order (their placeholder is "Paste a Google Meet, Microsoft Teams or Zoom link", `docs/ui/parity-report.md`, which had kept Meet and Zoom only because the backend then refused Teams: that row is updated). Changed: the new-meeting placeholder, the Calendar sentence in Settings, the share page's "could not find" message, `llms.txt`, two code comments, and the label in `app/templates/index.html` |
| Finder | `findMeetingLink` mirrors `_parse` / `_teams` in `app/meeting_url.py` (the brief named `app/teams.py`, which is the team-accounts feature, not this). Hosts `teams.microsoft.com` and `teams.live.com` only, exact match, no user info, no port. Paths `/l/meetup-join/<thread>[/<n>]` (teams.microsoft.com only) and `/meet/<6 to 20 digits>`. The thread, once percent-decoded, must look like `19:<token>@thread.<x>`. The query keeps exactly as written but only the characters `TEAMS_QUERY` allows; the whole link is at most 1500 characters. The result is `https://<host><path>?<query>`, fragment dropped, so a link read back from the address or from storage finds itself again |
| Path and query | Read from the text as written, not from the parsed `URL`, which would tidy away a `/../` that the server refuses |
| Safe Links | An Outlook Safe Links wrapper (`<region>.safelinks.protection.outlook.com/?url=...`) around a Teams link is read one level deep and the inner link is returned, as the server does; around anything else, or around another wrapper, it is refused |
| Platform | `SharedLink.platform` gains `"teams"`. `describeLink` says "Microsoft Teams meeting" and shows nothing of the link: its path and query carry the thread, the tenant and the passcode |
| Left alone on purpose | Landing (`app/templates/landing.html`, `web/components/landing/*`), privacy and terms (`web/components/legal/*`, `app/templates/{privacy,terms}.html`), `web/lib/site.ts` and `web/components/site/json-ld.tsx` and `public_base.html` (the site description): the owner keeps the designer's landing words and his lawyer reads the legal text; this is still T3 in the checklist. The docstring of `create_meeting` in `app/api.py` says "Google Meet or Zoom": editing it needs `docs/openapi.json` and `web/lib/api/schema.d.ts` regenerated, so it is left |

## What's involved

`web/lib/share.ts`, `web/components/app/new-meeting-form.tsx`, `web/components/app/settings-calendar.tsx`, `web/components/share/share-flow.tsx`, `web/app/llms.txt/route.ts`, `web/app/manifest.ts`, `web/app/share-target/route.ts` (comments), `app/templates/index.html` (`GET /` of the FastAPI app, still routed by `app/web.py` for a signed-in user; the live site is served by Next.js), `web/scripts/owner-browser-check.mjs` (its expected error text was already stale: the server has said "Google Meet, Zoom or Microsoft Teams" since #159). Tests: `web/tests/share-link.test.ts` (new Teams block), `share-page.test.tsx`, `settings-calendar.test.tsx` (its "no Teams anywhere" assertion, written when Outlook was drawn off, now excludes only the Calendar sentence).

## Verification

On the Mac, bounded: `npm run lint` (0 errors; 3 warnings that were already there in `scripts/parity-meeting-delete.mjs`), `npm run typecheck`, and `vitest run` on `tests/share-link.test.ts`, `share-page.test.tsx`, `share-target.test.ts`, `settings-calendar.test.tsx`, `screens.test.tsx`, `guards.test.ts`. The finder tests use the placeholder links of `tests/test_meeting_url.py`: each form alone, in an invitation in angle brackets and at the end of a sentence, scheme and host case tidied, no scheme, a Meet link before and after a Teams one, the same link twice, a Safe Links wrapper, a read-back round trip, and a refusal list copied from the server's own refusals (look-alike and subdomain hosts, user info, port, backslash, other scheme, chat and home paths, short or lettered ids, extra or climbing paths, bad or undecodable thread, `teams.live.com` with `meetup-join`, a pipe in the query, an over-long link, wrappers around other sites, Meet, nothing, a look-alike host or another wrapper). Not run: the full suite and the build (CI does).

**Not looked at in a real browser,** and not tried from a real phone share sheet: the Teams share from Android needs the installed app and a Teams invitation, which only Adebayo can do (checklist: "Adebayo's phone test"). Whether Outlook's own "Share" puts the link in `text` or `url` is not known; the finder reads both.

## Operate / roll back

Nothing to run. Revert the PR to go back. Teams still joins only with the Skribby provider; with Vexa the server answers "Microsoft Teams meetings cannot be joined yet" (`TEAMS_JOIN_ENABLED`), so the new wording is true only while Skribby is the provider.

## Follow-ups

- T3: Teams wording in the landing, privacy, terms and the site description (owner's word first).
- `create_meeting`'s docstring in `app/api.py` (with `docs/openapi.json` and `web/lib/api/schema.d.ts` regenerated).
- The share target on a real phone with a real Teams invitation.
