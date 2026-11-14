# SOP: hardening the join message and the name settings (follow-up to the per-user note taker name)

**What shipped.** Small fixes left open by the Opus review of PR #189 (`docs/sop/2026-11-14-per-user-bot-name.md`, `docs/sop/2026-11-14-bot-chat-notice.md`): the consent line survives a name fallback on every platform, joiners hidden anywhere in a web address are caught, `@` is written out in the chat line, the two name models have a `max_length`, and a wrong docstring is corrected. Branch `fix/join-message-name-hardening`. PR: see the checklist line.

## Why

The review of #189 found: (1) the chat notice was dropped after a refused name by a substring match on the typed name, which is wrong both ways (the notice holds the cleaned, defanged name; "A" is inside "Ada"); (2) a zero-width joiner or non-joiner kept inside a domain label or TLD (`evil.c<ZWJ>om`) split the word for the regexes while a person still reads "evil.com"; (3) a typed `@` could become a mention in a chat client; (4) no `max_length` on the name bodies, and a docstring claiming the line is bounded by 250.

## State found before starting

Most of items 1 and 2 had already landed in #189 (it merged after the review): `SkribbyClient.create` takes `fallback_notice`, built by `chat_notice.send_options` as `chat_notice_fallback` and passed by `place_bot` and `send_again` with the name the host sees for the default; the owner path already translated dot look-alikes and removed joiners next to a dot. This change only closes what was still open and adds the missing platform tests.

## How

| Item | What changed |
|---|---|
| 1. Fallback | Already rebuilt from the default name in #189. Remaining hole: a direct caller of `SkribbyClient.create` with no `fallback_notice` used `custom in notice`. Now `chat_notice.names_the_bot(notice, name)` (the line opens "Hi, I'm {the defanged name}. ") decides, so `Alex.Coach` is recognised and "A" no longer drops a notice that only contains the letter. Tests added for Meet, Zoom and Teams through `place_bot` (they pass on the existing mechanism: characterisation). |
| 2. Dots and joiners | `titles.without_joiners` removes every U+200C and U+200D. `looks_like_link` checks the text as typed and that copy; `defang_name_domains` and `defang_text` (owner path, `mail_line`) defang the copy, and only when it held something to defang is the joiner-free text used, so an emoji sequence (`man ZWJ woman ZWJ girl`) in a name without a domain keeps its joiners. Dot look-alikes on the owner path were already done in #189 (tests kept). |
| 3. `@` | `chat_notice._write_out_at`: `@`, U+FF20 and U+FE6B become `(at)` in the notice text only, for both names. The stored name and `clean_bot_name` are untouched. |
| 4. Cosmetics | `max_length=2000` on `DisplayNameIn.name` and `BotNameIn.bot_name` (`app/api_schemas.py`). Far above the 100 and 50 rules, so every ordinary too-long name still gets 422 `name_invalid` or `bot_name_invalid` with its words; only an absurd body gets the generic 422 `validation_error`, before any cleaning runs. Docstring: the line is not bounded by 250; measured 130 for ordinary names, 340 at the very worst (a 60-character bot name of `ab.ab.` pieces grows to 98 after defanging; the owner name is cut to 60 after defanging and appears twice). |

Rejected or out of scope (his call, not built): a reserved-word or impersonation list; teammate-name uniqueness. A choice not made: keeping "drops both" when Skribby's refusal names neither the name nor the notice (the notice goes with the optional fields, as before).

## What's involved

`app/titles.py` (`without_joiners`, `looks_like_link`, `defang_text`, `defang_name_domains`), `app/chat_notice.py` (`_bot_part`, `names_the_bot`, `_write_out_at`, length comment), `app/skribby.py` (`create`, the no-fallback branch), `app/api_schemas.py`, `tests/test_bot_name.py` (new section "hardening round 2"). No migration, no config, no change to a successful response, so `docs/fe-integration-guide-settings-bot-name.md` is unchanged. One new FE-visible case: a body over 2000 characters for either name is 422 `validation_error` (not captured live; the shape is the same generic one captured for a wrong type).

## Verification

Tests first: six new tests failed for the right reasons before the code. Then `tests/test_bot_name.py`, `test_bot_name_paths.py`, `test_chat_notice.py`, `test_display_name.py`, `test_titles.py`, `test_skribby_provider.py`, `test_pipeline_not_joined.py` pass (487 + 24) on a scratch Postgres; `ruff check .` and `ruff format --check .` clean. CI is the authority for the full suite.

## Operate / roll back

Nothing to deploy beyond the normal release; revert the PR to roll back.

## Follow-ups

- Whether Meet, Zoom or Teams link a dot look-alike or a joiner-split domain, render markdown or mention on `@` is unverified (no live meeting); this only removes the cases we can see.
- The carve-out for `J.Cn` style names in `defang_name_domains` is a documented choice, unchanged.
