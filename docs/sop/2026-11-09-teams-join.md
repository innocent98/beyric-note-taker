# SOP: Microsoft Teams meetings joinable through Skribby (T1 pasted link, T2 calendar events)

**What shipped.** Teams links are accepted wherever Meet and Zoom links are, and the Skribby bot joins them as a **guest**. PR #159, branch `feature/teams-join`. No migration, no new endpoint; the one API change is that `platform` may now be `"teams"` (`docs/openapi.json` and `web/lib/api/schema.d.ts` regenerated). **Vexa cannot join Teams and still refuses it, cleanly.**

**Not run against a real Teams meeting yet.** Everything is tested with fakes. Adebayo's live check: `PYTHONPATH=. .venv/bin/python scripts/skribby_live_check.py --meet-url '<whole Teams link>'`. Skribby's own page warns that guest joins depend on the host organisation's external access policy.

## Why

Skribby's docs (`docs/spike/primary-pages-2026-11-07/skribby-guide-meeting-urls.txt`, `-platforms.txt`, `-rest-create-bot.txt`) list service `teams`, links `https://teams.microsoft.com/l/meetup-join/...` and `https://teams.live.com/...`, and guest join. An authenticated Teams bot needs an account in the meeting's own tenant with MFA off: **not used**, so a Teams bot never gets `authentication`, even when `SKRIBBY_ACCOUNT_ID` is set (that account is a Google one).

## How

| Piece | Decision |
|---|---|
| Accepted links (`app/meeting_url.py` `_teams`) | `teams.microsoft.com/l/meetup-join/<thread id>[/<n>]`, `teams.microsoft.com/meet/<6-20 digits>`, `teams.live.com/meet/<6-20 digits>`. Same hygiene as Zoom and Meet (https or http, no user info, no port, no backslash, no whitespace or control characters, lookalike hosts refused), plus: whole URL at most 1500 characters, query limited to `A-Za-z0-9._~%:,@+=&/;!*()$-` (no quotes, angle brackets or braces). |
| What is sent on | The link as pasted: https, host lower-cased, **whole path and whole query kept** (`?context=` holds tenant and organiser, `?p=` is the passcode), fragment dropped. Nothing the join needs is removed. |
| Safe Links | `*.safelinks.protection.outlook.com/?url=<Teams link>` is read one level deep **only to get the code and the dedupe right**; the inner Teams link is what is sent. Anything else inside a wrapper (not Teams, another wrapper) is refused. Skribby could unwrap it too; we need the inner link for the code. |
| Meeting code (`native_meeting_id`) | Teams has no short code. It is `teams-` plus the first 24 hex digits of SHA-256 of the meeting's identity: for `meetup-join` the percent-decoded thread id, for `/meet/` the digits, each with the host (`teams.live.com` and `teams.microsoft.com` numbers are different namespaces). The query is **not** in it, so two links to one meeting (different `context`, different passcode, raw or encoded thread id) give one code and the "one bot per meeting code" rule and the join-runner dedupe keep working. 30 characters, matches the `[A-Za-z0-9-]+` check on `stop_bot`, fits the 200-char columns, and the thread id cannot be read back from it. A `/meet/` number is only about 40 bits, which is as guessable as the Zoom id stored in plain. |
| Skribby (`app/skribby.py`) | `SERVICES` gains `"teams": "teams"`; `stop_bot` checks `SERVICES`; `payload` sends `authentication` (the Google account) **for Meet only**. Zoom and Teams join as guests: a Zoom bot would need a ZAK token and Skribby refused the Google account on Adebayo's first live Zoom send (his guest bot joined fine). |
| Failure logs (`app/bot_provider.py` `describe_failure`) | The send-failure log lines (`services`, `join_runner`, `send_now`) now carry the provider's own reason, not just the exception type: Skribby's `message`/`error`/`code`/`detail` (300 characters), every web address replaced by `[link]`, the API key never present. |
| Vexa (`app/vexa.py`) | `send_bot` raises `UnsupportedMeetingUrl` for a Teams link before any request; `stop_bot` still raises `ValueError` for the platform. |
| Switch (`app/config.py`) | `teams_join_enabled` is now `bool | None`: unset follows the provider (**on with `BOT_PROVIDER=skribby`, off with Vexa**); `TEAMS_JOIN_ENABLED=false` turns it off under Skribby; `true` under Vexa is ignored (the bot could not join). Alternative rejected: a new setting, because every reader already uses this one. |
| Calendar (`app/gcal_events.py`) | The old "recognise Teams but give no code" fallback is gone: a Teams link is classified by `parse_meeting_url` like any other, so the event carries platform `teams` and a code, and `gcal_rules` stops answering `teams_unsupported` when the switch is on. The join runner then places it like a Meet or Zoom event. A Teams-looking link that does not parse is treated as no link. The Outlook (Graph) normaliser reuses the same classifier, so it too now carries a Teams code. |
| Hand retry | A Teams meeting retried while the provider cannot join Teams (made under Skribby, Vexa back in use) is refused 409 `unsupported_link` with the same sentence before anything is sent; `send_again` also turns `UnsupportedMeetingUrl` into a plain failed send instead of a 500. |
| Pasted link (`app/services.py`) | The "not a link" refusal now reads "That is not a Google Meet, Zoom or Microsoft Teams link. Paste the full meeting link." With the switch off, a Teams link is refused 400 `unsupported_link`, "Microsoft Teams meetings cannot be joined yet" (words that already exist in `app/calendar_text.py`), before any row is written. |
| Platform lists | `api_schemas.Platform`, `api_series._platform`, `series_mail` (label and `JOIN_HOSTS`), `api._platform_label`, `mail_templates._platform_label`, `titles._fallback` (a Teams meeting without a title is "Microsoft Teams", not the code), web `platformLabel`. |

## Known gap (M1, not fixed)

A `meetup-join` link and a `/meet/<digits>` link of the **same** Teams meeting give **different** codes (`tests/test_meeting_url.py::test_the_long_and_the_short_link_of_one_meeting_give_different_codes` records it). Two calendars holding different forms could send two bots, and the one-bot-per-code check cannot see it. Closing it needs a real Microsoft Graph capture (the `onlineMeeting` object that ties the two forms together): do it with the Outlook M0 run.

## Verification

New and changed tests, all fakes, no network: `tests/test_meeting_url.py` (good links, 20-odd hostile ones, short and `teams.live.com` links, Safe Links, one code per meeting, different codes for different meetings and hosts); `tests/test_skribby_provider.py` (payload for Teams, guest even with an account, duplicate refused whatever the link says, stop by code); `tests/test_skribby_live_check.py`; `tests/test_vexa.py` (refuses Teams, no request); `tests/test_gcal_rules.py` (config table, rule joins a Teams event); `tests/test_join_runner.py` (a Teams event is joined with Skribby settings, not scheduled with Vexa); `tests/test_api.py` (paste refused with Vexa, accepted with Skribby, second paste of the same meeting is 409); gcal and graph event tests read the derived code. `ruff`, the full pytest suite, `npm run lint`, `typecheck`, and the web vitest suite pass.

## Operate / roll back

Nothing to deploy beyond the merge when `BOT_PROVIDER=skribby` (Teams is then on). To turn Teams off without a deploy: `TEAMS_JOIN_ENABLED=false`. To roll back, revert the PR; Teams events already stored keep their derived code and are skipped as before once the switch is off.

## Follow-ups

- Live Teams join by Adebayo (guest admission from the lobby; his org's external access policy decides).
- Wording that still lists only Meet and Zoom, **not edited** (owner's rule for legal pages; new words are the owner's call for the rest): privacy `app/templates/privacy.html:74` and `web/components/legal/privacy-content.tsx:75` ("Google and Zoom"); terms `app/templates/terms.html:23`, `web/components/legal/terms-content.tsx:26`; landing and meta text; "Paste a Google Meet or Zoom link" in `web/components/app/new-meeting-form.tsx:53`; the Settings Calendar intro `web/components/app/settings-calendar.tsx:239`; the PWA share target (`web/lib/share.ts`) still finds only Meet and Zoom links; the "turned the bot away" notice in the meeting view says "Google" for any non-Zoom platform (a Teams join is not retried, so it does not show today).
- Browser tab title of a meeting page reads "Meeting <code>"; for Teams that is the derived code.
