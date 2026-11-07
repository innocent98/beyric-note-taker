# Vendor pages read as raw text, 2026-11-07

Why: the vendor notes in `docs/spike/` were partly second-hand (summaries made by a model that read
the pages). The bot bake-off tool (`tools/bakeoff/`) was built from these raw pages instead. Where
a page and an earlier note disagree, the page wins; the list is in
`docs/sop/2026-11-07-bot-bakeoff-tool.md`.

How they were taken: `curl -sL -A "Mozilla/5.0" <url>`, tags stripped with a small script (only the
visible text kept; site navigation cut off). Recall's pages are served as Markdown (`<url>.md`) and
are saved as they came. Each file starts with its `SOURCE:` line.

## Skribby (`skribby-*`)

| File | Page |
|---|---|
| `skribby-guide-quickstart.txt` | https://skribby.io/docs/guides/quickstart (where the API key is made) |
| `skribby-guide-bot-lifecycle.txt` | .../guides/bot-lifecycle (statuses, stop reasons, stop timers) |
| `skribby-guide-webhook-events.txt` | .../guides/webhook-events |
| `skribby-guide-meeting-urls.txt` | .../guides/meeting-urls |
| `skribby-guide-troubleshooting.txt` | .../guides/troubleshooting |
| `skribby-guide-bot-detection.txt` | .../guides/bot-detection (3 retries on `bot_detected`) |
| `skribby-guide-bot-authentication.txt` | .../guides/bot-authentication (accounts, `always_authenticate`, capacity) |
| `skribby-guide-bot-naming.txt` | .../guides/bot-naming |
| `skribby-guide-billing.txt` | .../guides/billing (Free plan limits, what counts as billable time) |
| `skribby-guide-platforms.txt` | .../guides/platforms |
| `skribby-guide-transcription-models.txt` | .../guides/transcription-models (`none` = no transcription) |
| `skribby-rest-api-overview.txt` | https://skribby.io/docs/rest-api (base URLs, Bearer token, endpoint list) |
| `skribby-rest-create-bot.txt` | .../rest-api/bot-operations/createbot |
| `skribby-rest-get-bot.txt`, `skribby-getbot.md` | .../rest-api/bot-operations/getbot (the `.md` twin shows `events[]`) |
| `skribby-rest-stop-bot.txt` | .../rest-api/bot-operations/stopbot |
| `skribby-docs-index.md` | https://skribby.io/docs.md |

Not saved: the Skribby pricing page (prices are not used by the tool; the billing guide is).

## Recall (`recall-*`)

| File | Page |
|---|---|
| `recall-docs-bot-status-change-events.md` | https://docs.recall.ai/docs/bot-status-change-events |
| `recall-docs-sub-codes.md` | .../docs/sub-codes (fatal and call-ended sub codes) |
| `recall-docs-regions.md` | .../docs/regions (all four regions are pay-as-you-go) |
| `recall-docs-google-meet.md`, `-google-meet-faq.md`, `-google-meet-skip-the-waiting-room.md` | the Google Meet pages |
| `recall-docs-automatic-leaving-behavior.md` | .../docs/automatic-leaving-behavior (`waiting_room_timeout`) |
| `recall-docs-bot-detection.md` | .../docs/bot-detection (names such as "notetaker" make bots leave) |
| `recall-docs-meeting-urls.md` | .../docs/meeting-urls (Meet link pattern) |
| `recall-docs-calculating-usage.md` | .../docs/calculating-usage (billing starts at `joining_call`) |
| `recall-reference-authentication.md`, `recall-reference-errors.md` | .../reference/authentication, .../reference/errors |
| `recall-reference-bot-create.md`, `-bot-retrieve.md`, `-bot-leave-call.md` | the three API reference pages. Each is 580 to 790 KB of OpenAPI JSON on the site, so the file keeps the page's own text and an excerpt (only the schemas the tool uses), marked inside the file. |

Not read raw: Recall's pricing page and the signed-in-bot guides (signed-in Recall is out of scope
for the bake-off).
