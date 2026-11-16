# Evidence for "leave when only bots are left" (2026-11-16)

What the rule relied on, and how much of it is real. **Nothing here is a capture of a live Skribby
call's participant list taken by this build: the session that built it had no Skribby key and no
server access.** Three kinds of source, labelled.

## 1. Skribby's own pages (primary, read 2026-11-16)

Read in full the same day by `WebFetch` (summarised by a small model, so quote only from the
verbatim copies below):

| Page | Verbatim copy kept in `docs/spike/primary-pages-2026-11-07/` |
|---|---|
| https://skribby.io/docs/guides/bot-lifecycle | `skribby-guide-bot-lifecycle.txt` |
| https://skribby.io/docs/rest-api/bot-operations/getbot | `skribby-getbot.md`, `skribby-rest-get-bot.txt` |
| https://skribby.io/docs/rest-api/bot-operations/stopbot | `skribby-rest-stop-bot.txt` |
| https://skribby.io/docs/guides/speaker-timelines | not copied verbatim (read 2026-11-11 and 2026-11-16; summary in `docs/sop/2026-11-10-skribby-raw-segments.md`) |

The sentences that decided the design, from the verbatim copies:

- `stop_options.last_person_detection` (default 2, minutes): "How long the bot waits after it becomes
  the only participant after other participants were present." Counts participants, so another bot
  in the call keeps it from firing.
- `stop_options.silence_detection` (default 15): "How long the bot waits without hearing speech
  before leaving. This helps when other bots remain in the call and last-person detection cannot
  trigger." Skribby itself names the problem. We send `0` on purpose (a quiet stretch must not end a
  call), so this was not turned on; see the SOP, "rejected".
- `stop_options.empty_meeting_timeout` (default 10): "How long the bot waits after it joins an empty
  meeting and no other participants arrive." Already on for every bot (we never sent it).
- `POST /bot/{id}/stop` (200 `Bot stopped successfully`, 403 `Bot cannot be stopped`, 404): "Stop a
  running meeting bot. This will cause the bot to leave the meeting" (`skribby-rest-stop-bot.txt`).
  Our `SkribbyClient.stop` already uses it (the owner's "End now").
- `GET /bot/{id}`: `participants` is `Participant[]`, "List of meeting participants"; the page gives
  the field list `name, avatar, first_seen_at, last_seen_at, left_at, presence_intervals[{joined_at,
  left_at}], events[]` (`skribby-getbot.md`, the JSON skeleton) and `?with-speaker-events=true`
  "Include participant timeline events". The speaker-timelines guide: "By default, the GET /bot/{id}
  endpoint returns participant data without detailed event logs", and "Once a bot reaches the
  finished status, you can retrieve each participant's timeline via the API".

**Not documented anywhere I could read:** whether `participants` is filled **while the bot is still
recording**, the unit of the integer times (examples have 13-digit integers: Unix milliseconds), and
whether Google Meet fills `presence_intervals` as people leave. This is why the rule is built to fail
safe (the bot stays) when the list is missing, empty, or its times cannot be read.

## 2. The participant shape of meeting 38 (reported, not captured here)

Reported to this build by the lead session, from the final bot object of meeting 38 (2026-11-16,
1 h 29 m, every person left about 56 minutes in, a Fireflies note taker stayed 33 more minutes):

```
participants: [{name, first_seen_at, last_seen_at, avatar,
                presence_intervals:[{joined_at, left_at}], left_at}]
names: "Fireflies.ai Notetaker Adebayo Tosin", "MyWay's Assistant", plus real people
```

That is a **finished** bot. It matches the page's skeleton field for field and shows a bot's name is
what the platform shows. It does not show what the same list looked like at minute 60 of the call.
The unit tests use this shape with placeholder names (`tests/test_bot_presence.py`,
`test_meeting_38_shape_...`).

## 3. What the first live call must prove

After deploy, the worker logs one line when the rule fires:
`meeting N: the bot was told to leave (only_bots|nobody_came; K in the call)`, never a name. If no
call ever logs it while a real note taker lingers, the live list is not filled during the call and the
alternatives in the SOP's follow-ups apply. To read a live bot by hand (needs a Skribby key, run by
Adebayo on the server): `GET https://platform.skribby.io/api/v1/bot/{id}` with the bearer key while a
call is running, and look at `participants[].presence_intervals`.
