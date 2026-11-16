# SOP: billing stage S3, the feature gates (dark: `FEATURE_GATES_MODE`)

**What shipped.** Ask Beyric, a NEW translation and rewriting notes with a template can now be gated on the **caller's** state (a live plan or trial). A new setting `FEATURE_GATES_MODE` (`off` by default, `shadow`, `enforce`) switches it: `off` changes nothing and reads nothing, `shadow` lets every call through and logs one line when the gate would have refused, `enforce` refuses with **403 `plan_inactive`**. It ships dark and is switched on only at go-live (S11). Branch `feature/feature-gates-shadow`, PR in the report (stage S3 of `docs/superpowers/specs/2026-11-15-billing-and-plans-design.md`, sections 2.2, 2.3 and 8). No migration. FE guide: `docs/fe-integration-guide-billing.md` (section "Feature gates", captures in `docs/fe-captures/feature-gates/`).

## Why

The spec's per-person model (section 1.4) says what a plan decides is what a *person* may do: record, auto-join, ask, translate, rewrite. Ask, translation and rewriting call OpenAI on every press; reading, search and export cost almost nothing and stay open. S3 builds the gates and the shadow/enforce switch, so a teammate who only reads shared notes keeps reading and a friend who wants the features subscribes. The pilot accounts have all three today, which is why it is a separate flag from `HOURS_MODE` and ships off: a gate switched on before plans can be bought would take the features away from people who cannot yet pay.

## How, and what was decided

| Piece | What it does |
|---|---|
| `entitlements.can` / `gate` (`app/entitlements.py:320-355`) | `gate(session, user, feature, now)` reads `status` (S1/S2) and answers `GateDecision(allowed, state)`; `can` is `gate(...).allowed`. The rule is one line: the admin (`unlimited`) always, anyone else by `state in OPEN_STATES`. An unknown feature name raises `ValueError`, so a typo can never silently open or close a gate. Pure reading |
| `app/feature_gates.py` (new) | `require(session, user, feature, now=, settings=)`: `off` returns at once (no query, tested with a statement counter); `shadow` runs the check in a savepoint, logs `feature_gates_shadow would have refused feature=<f> user=<id> state=<s>` (ids and state only) and lets the call go on; a failure of the check is logged by class and user id and never stops the call; `enforce` raises `Refused(403, "plan_inactive", ...)` and **fails closed** (an error in the check is not an open gate). The message names the feature ("Asking Beyric / Translating notes / Rewriting notes needs an active plan or trial. You can still read, search and export your notes, and use your tasks and decisions.") and says nothing about a pilot or an address |
| Where the check sits | **inside the function that calls the model, never only in a route**: `ask.answer` (`app/ask.py:849`), `translation.request_translation` (`app/translation.py:576`), `services.regenerate_notes` (`app/services.py:696`). That closes every way in (see the bypass table) |
| `services.regenerate_notes` takes `user` | its signature became `(session, user, meeting, template_id, openai_client)`; `user` is required so a caller cannot forget it. Both the JSON route (`app/api.py`) and the legacy page route (`app/web.py`) pass the caller. The page shows the 403 sentence on the meeting page |

**States that pass (spec 2.2), taken from what `status` returns, none invented.** Open: `trial`, `pilot`, `active` (a paid plan, **even with its hours used up**: these three use no recorded hours), `past_due` (the 3 days after a failed renewal keep Ask, translate and rewrite), and the admin. Closed: `none` (never subscribed, no grant in force, a pack or an admin gift alone), a trial that is used up or ended, a pilot grant with no hours left, `lapsed`, and **any state not in the list** (a state added later is closed until someone opens it on purpose). `past_due` and `lapsed` do not exist in `status` until S8, so they are tested by replacing `status`; the table test lists all twelve states.

**Decisions where the spec was ambiguous (the safer reading, recorded for the lead):**

| Question | Chosen | Why |
|---|---|---|
| A **used-up trial** or a **pilot grant with no hours left**: the spec's trial row says "everything" only until the 3 hours are used, and the last row names "a used-up or expired trial" as closed; the pilot row says "while a pilot grant has hours" | both are `none` and **closed**, which is what `status` already returns (it shows a trial or pilot state only while the grant has hours) | reading the spec literally and not re-deriving a second definition of "state" in the gate; one source of truth for who is on a plan. **Consequence to know before go-live: a pilot account that has used all its hours loses Ask, translate and rewrite at `enforce`.** |
| The **free "my tasks" answer** of Ask (`kind` `tasks`, no model) | **gated too** | the spec gates the route `POST /ask`, and one switch is simpler for the screens. The scope line and chips (`GET /ask/scope`, no model) stay open |
| **Sending notes by email in French or Spanish** (`shares.create_share`) | **gated** (it calls `translation.request_translation`) | not in the spec's table, but it makes a new translation, which costs OpenAI exactly like the button. It was a way round the gate otherwise. English sharing is not gated. **The lead should confirm this addition** |
| A **cached** or an **already running** translation requested again | not gated (the gate is after those two answers) | spec 2.3: reading an existing translation is never gated; asking again costs nothing |
| The **translation worker** | not gated | a queued translation was allowed when it was asked for; letting it finish is not a new use |
| Order against other errors | the gate comes after input checks (422 `question_invalid`, 409 `not_ready`, 400 `template_required`) and, for translation, before 429 `translation_limit` | a refusal is about the person, not the request, and shadow does not log for a request that would have failed anyway |
| Who is checked | the **caller** (`user`), never `meeting.owner_user_id` | spec 2.3 |

**The bypass table (checked in the code, and each row has a test).**

| Way in | Covered by |
|---|---|
| `POST /api/ask` | the gate in `ask.answer` (the only caller) |
| `POST /api/meetings/{id}/translations` | the gate in `request_translation` |
| `POST /api/meetings/{id}/shares` in French or Spanish | the same gate (`shares.create_share` calls `request_translation`) |
| `POST /api/meetings/{id}/regenerate` | the gate in `services.regenerate_notes` |
| the legacy page `POST /meetings/{id}/regenerate` (`app/web.py`) | the same function, so it cannot differ |
| the translation worker, the pipeline's first notes, extraction, topics | **not gated by design**: the worker only finishes what was allowed; the pipeline's notes belong to a recording, which `HOURS_MODE` governs |

Everything else that calls a model (transcription, extraction, topics) is part of the recording and the worker, not a button a person presses. Nothing else calls `generate_notes`, `ask.answer` or `request_translation` (grepped).

## What's involved

| File | Change |
|---|---|
| `app/feature_gates.py` (new) | the mode switch, the log lines, the refusal and its words |
| `app/entitlements.py:290-355` | `FEATURES`, `OPEN_STATES`, `state_allows`, `gate`, `can`, `GateDecision` |
| `app/config.py:82-95, 275-280` | `feature_gates_mode` and its validators (blank is off, case ignored, any other value stops startup naming the setting) |
| `app/ask.py`, `app/translation.py`, `app/services.py` | the three call sites; `translation.request_translation` rolls back before it raises so the account lock is released |
| `app/api.py`, `app/web.py` | the two regenerate routes pass `user` |
| `app/logging_setup.py` (new), `app/main.py` | web's own INFO lines reach its log (review M1): `configure()` is called first in `create_app`; the `app` logger only, so httpx and openai stay quiet; a no-op when the root logger already has a handler (pytest, a `--log-config`) |
| `app/api_ask.py`, `app/api_translation.py`, `app/api_shares.py`, `app/api.py` | the 403 added to the routes' documented responses (the CSRF 403 text is kept), `docs/openapi.json` and `web/lib/api/schema.d.ts` regenerated (descriptions only: 8 changed lines each) |
| `app/shares.py` | docstring names the new refusal |
| `.env.example`, `deploy/README.md` | the new setting, like `HOURS_MODE` |
| `tests/test_feature_gates.py` (new, 98 tests), `tests/test_logging_setup.py` (new, 2 tests), `pyproject.toml` (an `F811` ignore for it, as for the other fixture-importing tests) | |
| `scripts/capture_feature_gates.py`, `docs/fe-captures/feature-gates/` | the live captures |
| `docs/fe-integration-guide-billing.md` | the "Feature gates" section and verification rows |

No route and no id was added, so `tests/test_accounts.py`, `tests/test_api.py` and `tests/test_google_signin.py` inventories are unchanged (the existing route and OpenAPI-drift tests were run and pass).

## Verification

* **Run locally** (lint, format, and the named files only; CI runs the rest, and CI has no type-checker for Python): `ruff check .` and `ruff format --check .` clean (ruff 0.17.3, the pin). `tests/test_feature_gates.py` 98 passed and `tests/test_logging_setup.py` 2 passed. Also run and passing: `test_api_translation`, `test_shares`, `test_entitlements_status`, `test_ask`, `test_translation`, and `test_api` + `test_accounts` selected with `-k "regenerate or openapi or route"` (the route inventory, the regenerate tests, the committed OpenAPI file equals the routes).
* **What the 98 tests prove:** a table of **every state of spec 2.2** (thirteen: trial, pilot, active, active with hours spent, admin, past_due, none, trial used up, trial ended, pilot used up, pack only, admin gift only, lapsed) against **every gate** in **every mode** (off, shadow, enforce), with the exact outcome: nothing in off, one log line in shadow when closed, 403 `plan_inactive` in enforce when closed; the pure rule per state; each of the three call sites in each mode and with the open states; the reader-only teammate (reads a shared meeting, lists, exports; Ask refused; Ask works and cites the shared meeting once they hold a plan); the legacy page route cannot rewrite (403, no model call, notes unchanged); the gate is inside `regenerate_notes` for any caller; the share in French is refused and queues nothing while English passes; a cached, a running and a worker-finished translation survive the end of a plan; flag off reads **zero** SQL statements and logs nothing; shadow logs once, with ids only (no name, no email), and a failing check is logged by class and lets the call go on; enforce fails closed when the check itself fails; the refusal words mention no pilot and no address; 422 and 409 come before the gate; reading, export, decisions, the Review list and the Review spelling step stay open for a person with no plan. Added after review: a team admin is checked against their OWN plan, not the recorder's (spec 2.3: refused when only the recorder has a plan, allowed when only the admin has one); "try again" on a failed translation and re-translating after the notes changed are gated; a real SQL error (`select 1/0`) inside the shadow savepoint leaves the translation queued; and a **fresh interpreter** runs `create_app` and shows that the `app.feature_gates` and `app.placement` INFO lines are printed and a library's are not (this test fails when the call in `create_app` is removed; `caplog` could not show it because it forces the level). **Mutation checks:** removing the gate call at each of the three sites fails the tests that should catch it (three fail each time).
* **Live capture:** the real app under uvicorn with real migrations, in `enforce`, then `shadow`, then `off` (`scripts/capture_feature_gates.py`, 23 files in `docs/fe-captures/feature-gates/`): the four 403 bodies, the open calls, the shadow log lines from the real logger. The trial grant was inserted by SQL (sign-up gives no trial until S10); the notes model in the rewrite capture is a fake client and Ask was shown on a question that finds nothing (no model call), so **no OpenAI call was made**.
* **Not verified:** nothing ran against production data, a real model or a real browser; `FEATURE_GATES_MODE=enforce` has not been on in a deployed server; `past_due` and `lapsed` are tested only through a replaced `status`; the web typecheck was not run locally (the only web change is descriptions in the generated `schema.d.ts`; CI's web job and the OpenAPI drift tests are the check). The full suite was not run; CI is the authority.

## Operate / roll back

Deploy with `FEATURE_GATES_MODE` unset (nothing changes). To watch what it would do, set `shadow` in `.env` and recreate web, worker and scheduler (a restart does not re-read `.env`), then read the lines: `docker compose logs web | grep feature_gates_shadow` (feature, user id, state). **This line can appear only in `web`**: Ask, new translations, rewriting and sending notes in French or Spanish are all web requests, and the translation worker is not gated. It is an INFO line, and web had no log configuration, so before this PR web would have printed nothing (review finding M1): `app/logging_setup.py`, called from `create_app`, now gives the `app` logger an INFO stderr handler in the web process (and only when nothing else configured logging). The same fix makes the existing `hours_shadow` line (`app/placement.py`) show up in web, where a pasted link and Send now place bots; calendar joins place bots in the join runner, so look for `hours_shadow` with `docker compose logs web worker scheduler | grep hours_shadow`. Check it on the server before trusting an empty result. The check that proves the running web: with `FEATURE_GATES_MODE=shadow` set and web recreated, sign in as an account that holds no plan, press Ask once (any question), then run `docker compose logs web | grep feature_gates_shadow`: one `would have refused feature=ask user=<id> state=none` line must appear. A second check, of a fresh process only (it builds the app the way uvicorn does, so it proves the code in the image, not the running container): `docker compose exec -T web python -c "import logging; from app.main import create_app; create_app(); print(logging.getLogger('app').level)"` prints `20` (INFO) among its startup lines. Expect the people without a plan today (accounts made after migration 0049, a used-up pilot) to show as `state=none`. **Do not set `enforce` before go-live (S11).** Roll back by setting `off` (or removing the line) and recreating; nothing is stored, so nothing needs undoing. Revert the PR to remove the code; there is no migration.

### Before `enforce` (for Adebayo; not done by the deploy)

1. The people who would be refused are exactly the log lines under `shadow`: read them first. A pilot account that has used all its hours, and every account made after migration 0049, is `none` until S10 gives a trial or S8 sells a plan.
2. Subscriptions (S8) and the trial (S10) must exist, or the people refused have no way to become allowed.
3. The frontend must handle 403 `plan_inactive` on the four calls (the guide says how).
4. Set `FEATURE_GATES_MODE=enforce`, recreate web, worker and scheduler.

## Follow-ups

| Item | Why it matters |
|---|---|
| A signal in the session for the screens: which of the three features are open to this person (a `features` object, or `usage.can_ask`-style booleans) | without it the buttons learn they are closed only by being refused; `usage.state` exists only under `HOURS_MODE=enforce` and does not follow this flag. Not built because the spec does not call for it in S3 (2.4 says to add fields when the FE is ready); needs the lead's decision and an FE guide update |
| A person's trial or pilot grant is `none` the moment the running-meeting estimate takes the last hours (`status` subtracts it): someone recording their last minutes sees Ask refused until the meeting is counted | small and matches the hours rule; if it matters at go-live, the gate can read the state without the estimate |
| A pilot account with no hours left loses the three features at `enforce` (decision table above) | the lead should say whether pilot accounts keep them until their grant expires (S10) |
| Sending notes in French or Spanish is gated (not in the spec's table) | confirm; if not wanted, move the gate out of `request_translation` into the translate route |
| `past_due` and `lapsed` **pass by construction** until S8: the table replaces `status` for them, which only restates `OPEN_STATES`; no real subscription is read | re-run the table with real subscriptions in S8 |
| Terms and Privacy still describe the features as free during the pilot | S11 |
