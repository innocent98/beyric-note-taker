# SOP: "Also join invites I haven't answered yet"

**What shipped.** A per-person checkbox under "All meetings with a video link" in Settings, Calendar: **"Also join invites I haven't answered yet"** (`user_settings.join_unanswered`, migration `0055`, **off by default for everyone**). When it is on, `will_join` no longer skips an unanswered invite from outside the person's organisation. The Upcoming rows now show the reason an Off switch is off as a visible muted line, and the first rule's sentence and the Upcoming header label say what the rule really does. Branch `feature/join-unanswered-invites`; PR: see the checklist entry. Alembic head: **0055**.

## Why

Adebayo chose "All meetings with a video link" and saw the switch **off** on invites he had not answered, with nothing on screen to explain it. `will_join` (`app/gcal_rules.py`) has always skipped an unanswered invite (`self_response == "needsAction"`, or no entry for the owner while someone else organises) from someone outside the person's organisation (`organizer_same_org` false; for a personal Gmail user that is every sender) with the reason `not_accepted` ("Waiting for you to accept the invite"). That rule is deliberate and stays the default: **every user's bot is the same Google account**, so anyone can send an invite with a meeting link, and if invites sent by strangers made the bot knock on their meetings it would get that one shared account refused or banned for everyone. The problem was that the card promised "Every event on your calendar with a meeting link", the header said "Joining all meetings", and the reason lived only in a tooltip.

## How, and what was decided

| Decision | Choice | Why (and what was rejected) |
|---|---|---|
| Where the choice lives | A boolean on `user_settings`, `NOT NULL`, **server default FALSE**; new `JoinSettings.join_unanswered` (default `False`) | Other people stay protected by default; existing callers and tests keep working. **Rejected: change the default for everyone**, because of the shared-Google-account risk above. **Rejected: join only invites from known senders**: we store no organiser addresses by design. |
| How `will_join` uses it | One extra condition on the `not_accepted` skip: `unanswered and not settings.join_unanswered and organizer_self is not True and not organizer_same_org` | Declined, cancelled, no link, too long, ended, all-day and event type are decided **before** the rule, so they are untouched; declined still never joins, even with the per-meeting switch. The per-meeting override still beats everything. Under `organizer` and `none` the unanswered check is never reached, so the box does nothing there. |
| Building the settings in one place | `JoinSettings.for_user(prefs, teams_join_enabled=...)` is now the only constructor used in `app/`; it reads `rule` and `join_unanswered` from the row (or the stand-in for a missing row) | The earlier code built `JoinSettings` in six places (`calendar_api`, `gcal_sync` x2, `join_runner`, `organizer_bot`, `mail_summary`); a new setting read in five of them would be a silent bug. The **runner's re-check at send time** (`join_runner.py`) matters most: without it a join made with the box on would be cancelled `not_accepted` the moment it was due. `DEFAULT_USER_SETTINGS` carries `join_unanswered=False`. |
| API | Extended the existing route: `PUT /api/calendar/settings` takes `join_unanswered` (strict bool, never null); `GET /api/calendar` and the PUT answer carry it. **No new route.** An absent field keeps the stored value; an unknown field is still 422 | Route-inventory tests are unchanged. The empty-body 422 message now names all three fields. |
| Re-running stored rows | A changed `join_unanswered` re-runs `gcal_sync.rerun_rules` over the connection's events exactly like a rule change (future events that become `will_join` get their scheduled join; events that stop get theirs cancelled with the reason; a join whose bot is in is never touched). **Unlike a rule change it does not clear the per-meeting switches.** | The switches are the person's own choices and the box does not invalidate them; a rule change clears them because the rule is the baseline they were set against. |
| Wording | `calendar_text.rule_text(rule, join_unanswered)`: for `all` with the box off, "Joining all meetings except unanswered invites from outside your organisation"; with it on, the designer's "Joining all meetings". The first rule's card sentence names the same exception while the box is off. `REASON_TEXT["not_accepted"]` is unchanged. | The old texts were untrue for the default. Both stay short and use the app's own word "organisation". |
| Visible reason | `calendar-upcoming.tsx` renders `reason_text` as a `meta-line` paragraph under the platform line whenever the switch is not "Will join"; the tooltip stays | The only text a person saw was "Off". No new CSS class. |

**LEAD-DESIGNED pieces (not in the designer's file), to be shown to the designer:** the checkbox and its label, its help text ("Off: the note taker waits until you accept an invite from someone outside your organisation.", "On: the note taker joins them without waiting for you to accept.", and "Only used with All meetings with a video link." when another rule is chosen), the changed first-rule sentence, the changed `rule_text` for `all`, and the visible reason line in the Upcoming row. They reuse the designer's `Checkbox`, `cal-rules`, `cal-rule-desc` and `meta-line`; no new style was added. When another rule is chosen the checkbox is **disabled** (still showing the stored value) rather than hidden, so the person sees why it does nothing.

### Safety (the shared Google account)

Ticking the box means the bot may knock on a meeting a **stranger** invited the person to. What still protects the account: the box is **off by default** and per person; the host still has to admit the bot (Google Meet waiting room); the per-person hours rule and bot limits (`placement`, `BOT_MAX_LOCAL`) are unchanged; a declined invite never joins; the person's own per-meeting switch still works. What it does **not** do: it does not stop a spammer from sending many invites to a person who ticked the box (each would send a join, up to that person's limits). If the shared account ever gets refused, the first lever is `AUTOJOIN_ENABLED=false` (the emergency stop) and then clearing `join_unanswered` for everyone with a one-line UPDATE (below). No percent rollout or shadow mode, as decided.

## What's involved

| Path | Change |
|---|---|
| `alembic/versions/0055_join_unanswered.py` | adds `user_settings.join_unanswered` boolean NOT NULL default false; downgrade drops it |
| `app/models.py:507` | `UserSettings.join_unanswered` |
| `app/gcal_rules.py:50` (`JoinSettings`, `for_user`) and `:117` (the `not_accepted` condition) | the setting and the rule |
| `app/gcal_sync.py:68,190,244` | `DEFAULT_USER_SETTINGS`; both builders use `for_user` |
| `app/join_runner.py` (final re-check), `app/organizer_bot.py`, `app/mail_summary.py` | read the stored box through `for_user` |
| `app/calendar_api.py` | `SettingsIn.join_unanswered`, `CalendarOut.join_unanswered`, the PUT re-run, `_join_settings` |
| `app/calendar_text.py` | `ALL_EXCEPT_UNANSWERED_TEXT`, `rule_text(rule, join_unanswered)` |
| `web/components/app/settings-calendar.tsx` | the checkbox, help line, first-rule sentence |
| `web/components/app/calendar-upcoming.tsx` | the visible reason line |
| `docs/openapi.json`, `web/lib/api/schema.d.ts` | regenerated (two fields; three unrelated docstrings the generator now emits differently) |
| `scripts/capture_join_unanswered.py`, `docs/fe-captures/join-unanswered/` | live captures |
| `docs/fe-integration-guide-calendar-settings.md` | section 3b, field table, verification rows |

## Verification

Local, scratch Postgres 17 (a throwaway `initdb` cluster on port 5433, removed afterwards; Docker was not running), only named files, never the full suite:

- `ruff check` and `ruff format --check` (the pinned 0.17.3 from the project venv) on the touched Python; `npm run lint` and `npm run typecheck` in `web/`.
- Backend: `test_gcal_rules` (the new truth table: rule x organiser x response x same-organisation x box x switch = 540 cases against an independently written expectation, plus the "box never lifts what the event forbids" and "no effect under organizer/none" tests), `test_calendar_settings_api` (read, write, absent field keeps the value, unknown/null/non-bool 422, set before a calendar is connected, **ticking flips an unanswered future invite to will_join and creates its scheduled join, clearing cancels it, a running bot is never touched, per-meeting switches kept, another user's events untouched, no effect under other rules**), `test_calendar_api`, `test_gcal_sync` (sync with the box, declined never), `test_join_runner` (the runner's re-check with the box off/on), `test_join_unanswered_migration` (one head, follows 0054, column NOT NULL, existing and new rows false, downgrade/upgrade round trip), plus `test_calendar_states`, `test_calendar_recurring`, `test_mail_summary` as the directly related calendar files.
- Frontend (vitest, only touched and directly related files): `settings-calendar-unanswered` (new), `calendar-upcoming`, `settings-calendar`, `settings-calendar-outlook`, `meetings-calendar`, `settings-page`, `settings-sections`.
- Live capture against the running app: `docs/fe-captures/join-unanswered/` (18 exchanges, the stored joins after each write).
- **Not verified:** the pages in a real browser (jsdom component tests only; the checkbox's help line is not indented under its label because no new style was allowed), a real Google calendar, the bot actually being admitted to a stranger's meeting, the server, and every test file CI runs and I did not.

## Operate / roll back

- Deploy: normal (`alembic upgrade head` adds the column; the code reads it). **Server step before merge: none** beyond the migration the deploy already runs.
- Turn it off for one person: `UPDATE user_settings SET join_unanswered = false WHERE user_id = <id>;` then they (or the next sync) pick it up; joins already planned are cancelled at the next send-time re-check (`not_accepted`). For everyone: the same UPDATE without the WHERE, after which the bot sends nothing new for those invites.
- Emergency stop for all auto-join: `AUTOJOIN_ENABLED=false` (unchanged).
- Roll back: `alembic downgrade 0054` drops the column; people who ticked lose the choice and fall back to the protected behaviour (the safe direction). Code from before the column ignores it.

## Follow-ups

- Show the designer the lead-designed pieces above; the help text's alignment under the checkbox label (it sits at the card's left edge) waits for a designer's style.
- `docs/fe-integration-guide-*.md` other than calendar-settings (bot-invite-tip, calendar-outlook, calendar-state captures) still print `rule_text: "Joining all meetings"` from captures made before this change; the calendar-settings guide says so at the top of section 2.
- If spam invites to people who ticked the box ever show up, the next step is a per-person cap on unanswered-invite joins per day, not a global switch.
