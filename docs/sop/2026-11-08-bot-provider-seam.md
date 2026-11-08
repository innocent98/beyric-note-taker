# SOP: the `BotProvider` seam (plan task B1; also the Phase D seam)

Plan: `docs/superpowers/plans/2026-11-03-meet-signed-in-bot-and-vendor.md` (section 3.3 and task B1). Why now: `docs/spike/bot-hosting-cost-stress-infra-2026-11-08.md` (headline: "build the seam (B1) regardless"; it turns every later switch from a rewrite into a configuration change). **Branch:** `feature/bot-provider-seam`, PR #144. **No migration, no endpoint, no UI, no behaviour change.**

## What shipped
One interface, `app.bot_provider.BotProvider`, between the app and whatever puts a bot in a meeting; `VexaProvider` (the self-hosted Vexa, wrapping the unchanged `VexaClient`); `FakeProvider` for tests; a `BOT_PROVIDER` setting (default `vexa`) that picks the provider; every call site moved from `VexaClient` to the interface; contract tests that run the same scenarios against the fake and the Vexa wrapper.

## Why
There was no bot-layer boundary: `VexaClient()` was constructed in eight places (app and scripts) and Vexa's client, errors and result type were named 73 times across `app/` outside `app/vexa.py`. Google refuses the anonymous guest about a quarter of the time and a bake-off against hosted vendors is running (`docs/sop/2026-11-07-bot-bakeoff-tool.md`); whichever way it goes, a second backend must be one more class, not a rewrite of the worker, pipeline, `place_bot`, join runner and deletion. The same seam is what Phase D (several bot hosts) needs for `vexa_for(host)`.

## The interface (`app/bot_provider.py`)
Exactly the calls the app made to Vexa before this change, nothing added:

| Call | The rule callers rely on |
|---|---|
| `send_bot(meeting_url) -> int` | Puts a bot in the meeting; returns the provider's id. Raises `UnsupportedMeetingUrl` for a link that is not a meeting. **Returns or raises within `SEND_DEADLINE_SECONDS` (30 s)**: it runs under the placement lock |
| `get_meeting(platform, code)` / `get_meeting_by_id(id) -> BotMeeting` | Live status, segments, recording ids, completion and failure text, `reached_lobby`, the provider's id. By id is exact; by code can match an older meeting |
| `stop_bot(platform, code)` | Takes out whichever bot holds the code. Nothing there is success. `ValueError` for a platform or code it would never accept |
| `delete_recording(recording_id)` | Already gone is success |
| `download_audio(recording_id, media_id, path)` | Writes the audio to `path`; on failure no file is left behind |

**Typed errors**, all `BotProviderError` (retryable): `BotDuplicate` (the code already has a bot that is not ours), `BotSendUncertain` (a send got no clear answer: the bot MAY arrive, so `place_bot` keeps the code as an unconfirmed start), `BotNotFound` (the provider does not know the meeting; deletion reads it as "nothing to wait for"). A connection that was never made is a plain `BotProviderError`, never `BotSendUncertain`.

**One bot per meeting code** is a rule of the provider, not of the app: a second send for a live code is `BotDuplicate`; the code is free again once the bot is over.

There is **no health or capacity read** (capacity is counted in our database by `app/placement.py`) and no user, key or account call: the app never made one.

## How: decisions and trade-offs
| Topic | Decision |
|---|---|
| Same classes, Vexa names kept | `VexaError`, `VexaSendUncertain`, `VexaDuplicateBot`, `VexaNotFound` are **subclasses** of the neutral errors, so a log line still says `VexaSendUncertain` (the worker and services log `type(exc).__name__`) and callers catch the neutral class. `VexaMeeting` is the same class as `BotMeeting` (an alias). Alternative rejected: renaming the classes everywhere, which would change log text and every operator's grep |
| Wrapper, not a rename | `VexaProvider` holds a `VexaClient` and forwards each call untouched: same HTTP calls, same retry (none) and timeouts, same send deadline, same error mapping. `VexaClient` stays the one object that knows Vexa's URLs |
| Link parsing moved | `parse_meeting_url`, `canonical_meeting_url`, `UnsupportedMeetingUrl` are provider-independent: they now live in `app/meeting_url.py` and `app/vexa.py` re-exports them (`gcal_events` and the fakes import from the new place). `run_within` and `SEND_DEADLINE_SECONDS` moved to `app/bot_provider.py` the same way (`app.vexa._within` and `app.vexa.SEND_DEADLINE_SECONDS` still resolve) |
| Selection | `BOT_PROVIDER` (default `vexa`). `app/config.py` validates it against `PROVIDER_FACTORIES`, so an unknown value stops web, worker and scheduler **at startup**: `BOT_PROVIDER is 'x'; it must be one of: vexa`. `build_provider()` builds the chosen one (`get_bot_provider` for routes). A new provider adds one factory to `PROVIDER_FACTORIES` and nothing else |
| The fake is not selectable in production | `FakeProvider` lives in `tests/fakes/provider.py`, so no env value can turn recording off by accident. Tests register it with `monkeypatch.setitem(PROVIDER_FACTORIES, "fake", ...)` and set `BOT_PROVIDER=fake` (`tests/test_bot_provider_fake_e2e.py`) |
| Names kept so nothing else changes | Parameters are still called `vexa` (`place_bot(session, vexa, req)`, `advance_meeting(..., vexa=...)`) because tests pass them by keyword; `services.get_vexa` is the same function object as `get_bot_provider`, so `app.dependency_overrides[get_vexa]` still works; `BotMeeting.vexa_id` and `meetings.vexa_meeting_id` keep their names (the plan's `provider_ref` columns need a migration, which this task excludes) |
| Status vocabulary unchanged | `BotMeeting.status` is still Vexa's words (`requested`, `joining`, `awaiting_admission`, `active`, `needs_help`, `stopping`, `completed`, `failed`) and the pipeline still reads Vexa's `completion_reason` and `failure_reason` texts (`awaiting_admission_rejected`, `google_meet_join_refused`, `zoom_requires_rtms`). Changing that is a behaviour change; it is the next step (below) |

## What's involved
| Area | Files |
|---|---|
| New | `app/bot_provider.py` (interface, errors, `BotMeeting`, `run_within`, registry, `build_provider`), `app/meeting_url.py` |
| `app/vexa.py` | errors subclass the neutral ones; re-exports; `VexaProvider` (about 25 lines at the end); `VexaClient` untouched |
| Call sites moved to `BotProvider` / `build_provider()` / the neutral errors | `app/pipeline.py`, `app/placement.py`, `app/join_runner.py`, `app/services.py`, `app/worker.py`, `app/gcal_sync.py`, `app/deletion.py` (catches `BotNotFound`), `app/api.py` and `app/web.py` (`Depends(get_bot_provider)`), `app/gcal_events.py` (link parsing), `scripts/delete_user.py` |
| Config | `app/config.py` (`bot_provider`, validator), `.env.example`, `deploy/README.md` (one row) |
| Tests | `tests/fakes/provider.py`, `tests/test_provider_contract.py`, `tests/test_bot_provider_fake_e2e.py`; `pyproject.toml` (F811 ignore for the e2e file, which imports fixtures like its siblings) |

**Deliberately left on `app.vexa`:** `app/deletion.py` still imports `VexaError` for the four "not settled yet" errors it raises itself (existing tests expect that exact class); `scripts/capture_*.py` import `VexaMeeting` and `VexaError` (aliases, unchanged). Neither touches the client.

**Existing tests edited: two lines**, in `tests/test_admin.py` (`test_the_script_refuses_to_delete_the_last_admin_and_asks_first` and the test after it): `monkeypatch.setattr(delete_user, "VexaClient", ...)` became `"build_provider"`. `scripts/delete_user.py` now gets its provider from `build_provider()` like every other caller (a script that built a `VexaClient` itself would delete recordings at the wrong host once `BOT_PROVIDER` changes); the test replaced the name the script used to import. Every other existing test file is byte-for-byte unchanged.

## Verification
See the PR body for the run. In short: the existing Vexa, join runner, join refusal, pipeline, placement, end-meeting, meeting-delete, bot live state, scheduler and sweep tests pass unchanged, as do the admin, accounts, API, web, calendar, ask and maintenance suites. New:
- **Contract (`tests/test_provider_contract.py`, 49 cases):** each scenario runs against `FakeProvider` and against `VexaProvider` over `pytest-httpx` (admitted, knocked-then-admitted, host declined, Google's refusal, never joined, lookup by code and by id, not found, duplicate, code freed after the bot is over, send never connected, send uncertain, bad link, stop of nothing, bad platform or code, delete of a gone recording, download and failed download). Expected results are written out by hand, not taken from either implementation. Mutating `_reached_lobby` in `app/vexa.py` fails exactly the three Vexa cases that read the lobby and none of the fake's, which is how the suite was shown to test the wrapper.
- **End to end with the fake (`tests/test_bot_provider_fake_e2e.py`, 11 cases):** `place_bot` then the real `worker.run_pass` loop take a meeting from send to notes with the provider chosen by `BOT_PROVIDER=fake`; a lobby wait; a host who declines (failed with the not-let-in reason and the bell entry); Google's refusal (stop, pause, second bot); never joined (given up after `max_poll_hours`); a send error (no row), an uncertain send (unconfirmed start), a duplicate (`meeting_has_note_taker`); cancel stops the bot; the join runner sends a calendar bot and the worker takes it to notes.
- **Selection:** default is `vexa`; an unknown value fails `get_settings()` with the setting's name and the choices; a registered factory is picked by the setting.

## Operate / roll back
- **Deploy:** a Python change: restarts web, worker and scheduler, so deploy in a no-meetings window (a restart drops the worker's in-memory counters, which only gives each meeting a fresh grace). No `.env` change is needed; unset means `vexa`.
- **Roll back:** revert the PR; nothing is stored differently (no migration, no new column, no new value in any row).
- **Check after deploy:** `/health` 200; worker and scheduler logs show no `BOT_PROVIDER` error; one test meeting reaches notes.

## How a third provider (Skribby, Recall) plugs in
1. Write `app/<name>.py` with a client for the vendor's HTTP and a class with the six methods of `BotProvider` (the structural check is `isinstance(obj, BotProvider)`). Map its failures to the typed errors: a 4xx or a refused connection is `BotProviderError`; a timeout or 5xx **after the request was sent** is `BotSendUncertain`; an "already in that meeting" answer is `BotDuplicate`; an unknown id is `BotNotFound`.
2. Map its states into the status vocabulary above and fill `BotMeeting` (segments as `{"speaker", "text"}`, `recording_id` and `media_id` for the audio, `reached_lobby`, the vendor's reason text in `failure_reason`; use `completion_reason` `awaiting_admission_rejected` for a host who declined, and a `failure_reason` starting `google_meet_join_refused` for Google's refusal, because the pipeline's retry and bell logic read exactly those).
3. Keep the rules: `send_bot` returns or raises within `SEND_DEADLINE_SECONDS` (use `run_within`); one live bot per code (vendors that allow several must refuse the second); `stop_bot` of nothing and `delete_recording` of a gone id succeed.
4. Register it: `PROVIDER_FACTORIES["skribby"] = ...` in `app/bot_provider.py` (the factory imports lazily, like `_vexa`), and add its settings to `app/config.py` and `.env.example`.
5. Add one `World` class to `tests/test_provider_contract.py` (how each scenario is arranged behind its HTTP) and it inherits every scenario; the same beats drive `FakeProvider`, so no scenario is written twice.

## What the interface deliberately does NOT cover yet
| Not covered | Why and what it needs |
|---|---|
| **Signed-in bot accounts** (plan Track A) | A bot that joins as a Google account needs a credential per host, a login-health check and an alert; none of that is a call the app makes today. It belongs to the host (`bot_hosts`, Phase D), not to the six calls |
| **The invited-event path** (`bot_invited`, Upcoming row, Settings address) | A vendor or account bot that is invited to an event instead of sent a link has no `send_bot(url)`; it needs a different way to say "this bot will attend that event" and a way to know it accepted |
| **A provider-neutral state enum and `FailureCode`** | Today a provider speaks Vexa's words. The plan's `BotInfo.state` and `FailureCode` (refused, platform blocked, not admitted) are the right end state and a behaviour change in the pipeline; do them when a second provider forces the question |
| **Provider-neutral columns** (`meetings.provider_ref`, `provider_media_ref`, `bot_hosts.kind`, `meetings.provider`) | Need a migration (expand then contract); `meetings.vexa_meeting_id` is a text column that holds any provider's id, and `vexa_recording_id` is a BigInteger that will not hold a UUID |
| **Choosing a provider per meeting or per host** | `build_provider()` returns the one global provider. Phase D D2 turns it into `build_provider(host)`; the vendor-as-fallback rule (plan 3.3) needs placement to try a second provider |
| **Webhooks and scheduled bots** (Recall) | The seam is poll-only, as the app is |
| **Cost and spend ledger** | Not a bot call |

## Follow-ups
- Rename the `vexa` parameters and `vexa_id` once the tests that pass them by keyword move (a mechanical change across about 16 test files).
- Move `deletion`'s own `VexaError` raises to a neutral error that is also a `VexaError` once its tests are updated.
- B2 (`RecallProvider`) or a Skribby provider, by the bake-off result; then the neutral status enum.
