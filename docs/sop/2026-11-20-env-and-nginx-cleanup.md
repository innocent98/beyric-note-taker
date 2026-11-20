# SOP: clean `.env.example`, a trimmed `deploy/nginx.conf`, and a script that cleans the server's `.env`

**What shipped.** A rewritten `.env.example` (section headers, no prose), a comment-trimmed `deploy/nginx.conf` (the same directives), `scripts/clean_env.py` (rewrites the server's `.env` into the same layout, never prints a value) with `tests/test_clean_env.py`, and the explanations that used to sit in comments moved into `deploy/README.md`. Branch `chore/env-and-nginx-cleanup`; the PR number is in its description. No migration, no app code, no compose change.

## Why

Adebayo is preparing a production release and the configuration had become noise: the server's `.env` carried long prose about proxies and lines that only repeat a default, and `.env.example` (369 lines, 90 variables, most of them paragraphs) and `deploy/nginx.conf` repeated their rationale in comments. His words: remove variables that are not needed, no unnecessary comments, section headers to segment, keep it clean. The repository is public, so nothing real may be committed.

## How

1. **Audit first.** Every name on the server and every name in the old `.env.example` was traced to where it is read: `app/config.py` (`Settings`, `extra="ignore"`, `Field(validation_alias=...)`), `compose.yaml` (`${VAR}` and which service has `env_file`), `deploy/*.sh`, `web/` (`process.env`), `scripts/`, `alembic/`, tests. The table below is the result.
2. **`.env.example`:** `# ---- Section ----` headers are the only comments. Required and used variables are active lines; every secret and password is **empty**, because an empty value is refused by the app (`SESSION_SECRET`, `OWNER_EMAIL`/`OWNER_PASSWORD` in migration 0006, a Skribby provider with no key) or by compose (`${VAR:?}`), whereas a `CHANGE_ME` placeholder would have been accepted and could start a server with a published admin password; everything else (every setting that has a code default, and the provider keys that must stay unset until they exist) is **not in the file**: it has a row, with its default, in the `deploy/README.md` section 3 table. The first version carried those as 68 commented `# NAME=default` lines; Adebayo found that still too long, so they were removed (all 60 optional names were checked against the README rows first). Optional integrations (Google, Microsoft) are empty active lines, because a placeholder there would turn on a broken feature (`TOKEN_ENC_KEY=CHANGE_ME` stops startup: a set value must be valid).
3. **The explanations** were checked name by name against `deploy/README.md`. 25 names had no row (the whole Microsoft group, the model and limit settings, the mail caps, `ICS_ALLOWED_HOSTS`, `TEAMS_JOIN_ENABLED`, `BOT_NAME`); rows were added (section 3 table), and the nginx rationale is a new "What `deploy/nginx.conf` holds, and why" part of section 6. `tests/test_clean_env.py` fails if a name is in `.env.example` and not in the README.
4. **`deploy/nginx.conf`:** comments removed, six section headers kept. Proved identical by stripping comments and blank lines from the old and the new file and diffing (empty diff). `include /etc/nginx/snippets/beyric-edge-secret.conf;` is untouched and no secret is in the file.
5. **`scripts/clean_env.py`** (standard library only, runs on the host's `python3`): dry run by default, key names only. `--apply` saves `.env.bak-<UTC time>` (600), then writes the new file through a temp file in the same directory and `os.replace` (600, same owner). Values are copied byte for byte, quotes included. It parses the file the way docker compose does (checked with `docker compose config` on a scratch file: the last duplicate wins, quotes are stripped, an unquoted ` #` starts a comment, CRLF is fine).

Rejected: **deleting the Vexa variables now.** Adebayo starts Vexa once to clean up old recordings, so they stay in a section named `Vexa (to remove once its cleanup is done)`. **Removing `OWNER_PASSWORD`.** It is read only by migration `0006` (a first install), so it is not needed on this server, but a rebuilt database needs it and the plaintext is his call; the script keeps it and the README says how to drop the one line. **Removing every line equal to a default.** Identity and safety lines stay explicit even when equal (`APP_ENV`, `COOKIE_SECURE`, `PUBLIC_BASE_URL`, `MAIL_FROM`) and so do the switches, so the file still says what is on.

## The audit

Names on the server (54), grouped. "Read at" is the first reader found (`file:line`); the field's default is at the `config.py` line. Nothing in the 54 is UNUSED: the leftovers of the Vexa era are all still read, by the Vexa provider or by compose.

| Class | Names | Proof |
|---|---|---|
| **REQUIRED** (6) | `POSTGRES_PASSWORD`, `VEXA_DB_PASSWORD`, `VEXA_STORAGE_SECRET`, `SESSION_SECRET`, `OPENAI_API_KEY`, `TOKEN_ENC_KEY` (conditional) | The first three are `${NAME:?...}` in `compose.yaml` (lines 19, 175, 196); the Vexa two are checked **even with the `vexa` profile off** (`tests/test_compose_vexa_profile.py`, the `project` fixture says so). `SESSION_SECRET`: `app/auth.py` `check_start` refuses fewer than 32 characters outside `APP_ENV=test`. `OPENAI_API_KEY` (`config.py:597`, `app/ask.py:863`): default empty, the app starts but makes no transcript or notes. `TOKEN_ENC_KEY` (`config.py:682`): `app/vault.py` refuses to start in production when `GOOGLE_CLIENT_ID`, `MICROSOFT_CLIENT_ID` or `CALENDAR_ENABLED` is set without it |
| **VEXA-ONLY** (3) | `VEXA_IMAGE_TAG`, `VEXA_API_KEY`, `BOT_NAME` | `VEXA_IMAGE_TAG`: `${...:-v012}` for the `vexa` service only (`compose.yaml:224`). `VEXA_API_KEY` (`config.py:514`) and `BOT_NAME` (`config.py:515`): read only in `app/vexa.py:121-122`, the Vexa provider (a meeting Vexa ran, or `BOT_PROVIDER=vexa`). Kept in the Vexa section |
| **FIRST-INSTALL-ONLY** (2) | `OWNER_EMAIL`, `OWNER_PASSWORD` | Read only by migration `0006` (`alembic/versions/0006_users_and_auth.py:37-38`); `app/` never reads them. At head (0054) the migration does not run again; `deploy/deploy-gate-check.sh` passes its own dummies. The old comment's "refuses to start with them empty" is **not true of the running app** (no startup check); it is true of migration 0006 on a fresh database. Kept |
| **SET-TO-NON-DEFAULT** (31) | `EDGE_SHARED_SECRET`, `RESEND_API_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `CALENDAR_ENABLED`, `BOT_PROVIDER`, `SKRIBBY_API_KEY`, `SKRIBBY_ACCOUNT_ID`, `SKRIBBY_ACCOUNT_NAME`, `SKRIBBY_TRANSCRIPTION_MODEL`, `BOT_MAX_LOCAL`, `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_SECRET_EXPIRES_ON`, `MICROSOFT_CALENDAR_ENABLED`, `BOT_INVITE_EMAIL`, `SKRIBBY_BOT_NAME`, `BOT_CHAT_NOTICE_ENABLED`, `MULTI_TEAM_ENABLED`, `SERIES_FOLLOW_MEETINGS`, `TRANSCRIPT_SOURCE`, `PAYSTACK_SECRET_KEY`, `PAYSTACK_PUBLIC_KEY`, `FLUTTERWAVE_SECRET_KEY`, `FLUTTERWAVE_SECRET_HASH`, `FLUTTERWAVE_PUBLIC_KEY`, `BILLING_ENABLED`, `BILLING_ALLOWED_EMAILS`, `TRIAL_SECONDS`, `HOURS_MODE`, `FEATURE_GATES_MODE` | The code default is empty or "off", so the feature exists only because the server sets it (`config.py` lines 630, 638, 641-642, 688, 354, 358, 361, 364, 390, 558, 694-696, 703, 512, 370, 519, 731, 740, 395, 452-454, 461-463, 410, 422, 576, 400, 406). This class is by construction (no default turns the feature on); the **server's actual values were not read**. `BOT_MAX_LOCAL` is never removed even at its default: while it is absent the limit follows the provider, a line always wins (`config.py:332-337`). `PAYSTACK_PUBLIC_KEY` and `FLUTTERWAVE_PUBLIC_KEY` are read by nothing but the test-or-live key check at startup (`config.py:262`, `287`); kept |
| **EQUALS-DEFAULT, removable** (8) | `POLL_INTERVAL_SECONDS` (30), `TRANSCRIPTION_MODEL` (`gpt-transcribe`), `NOTES_MODEL` (`gpt-6-luna`), `AUDIO_RETENTION_DAYS` (7), `GOOGLE_OAUTH_TESTING` (false), `MICROSOFT_AUTHORITY` (`consumers`), `TRANSCRIPT_SHADOW_PERCENT` (0), `PAYSTACK_WEBHOOK_SECRET` (empty) | All are read (`app/worker.py:189`, `app/pipeline.py:1037`, `app/services.py:717`, `app/web.py:109`, `app/calendar_connect.py:371`, `app/microsoft_oauth.py:309`, `app/pipeline.py:1128`), so none is UNUSED; each has a plain constant default (`config.py` 544, 598, 599, 617, 645, 699, 507), not derived from another setting. The script removes the line **only when the server's value is that default**. `AUDIO_RETENTION_DAYS` is also in `compose.yaml:132` with the same `:-7`. `PAYSTACK_WEBHOOK_SECRET`: the adapter strips the value and tests it for truth (`app/billing/paystack.py:166`, `180`), and the config check rejects only a blank-but-not-empty value (`config.py:270`), so empty and absent behave the same |
| **EQUALS-DEFAULT, kept on purpose** (4) | `APP_ENV`, `COOKIE_SECURE`, `PUBLIC_BASE_URL`, `MAIL_FROM` | Code defaults are `production`, `true`, the public site address and the no-reply sender. Left explicit because they say what this server is |
| **UNUSED** (0) | none | The hints in the task (`POLL_INTERVAL_SECONDS`, `BOT_NAME`, `TRANSCRIPTION_MODEL`, `OWNER_PASSWORD`, `GOOGLE_OAUTH_TESTING`, `CALENDAR_ENABLED`, `TRANSCRIPT_SHADOW_PERCENT`, the `VEXA_*` group) were each grepped for the field name in lower case as well as the variable name, because pydantic reads `bot_name` from `BOT_NAME`: all are read, as above. `app/config.py` has no `Field(alias=...)`; the one `validation_alias` is `BOT_MAX_LOCAL` or the old `MAX_ACTIVE_BOTS` |

Other variables, from the old `.env.example`, that are not on the server: every Settings field is in the cleaner's `LAYOUT` and has a README row (tests enforce both). Nothing was dropped: `DATABASE_URL` and `VEXA_BASE_URL` are now described in their README row only (compose sets both for containers). `web/` reads only `AUDIO_RETENTION_DAYS` and `FASTAPI_INTERNAL_URL` (set by compose) of the server's names; the `next` service has no `env_file`.

## What's involved

| Path | Change |
|---|---|
| `.env.example` | rewritten: 10 section headers and the 40 live variables only, 369 lines to 59 |
| `deploy/nginx.conf` | comments removed (129 to 87 lines); code lines proven identical |
| `scripts/clean_env.py` | new; `LAYOUT`, `REQUIRED`, `DEFAULTS` tables at the top |
| `tests/test_clean_env.py` | new; also pins `LAYOUT` to `.env.example`, `DEFAULTS` to `Settings`, and the README rows |
| `deploy/README.md` | new section-3 rows (Microsoft, models, limits, mail caps, ICS hosts, `TEAMS_JOIN_ENABLED`, `BOT_NAME`), "What `deploy/nginx.conf` holds, and why", "Cleaning the server `.env`" |
| `docs/checklist/master-checklist.md` | one line |

## Verification

- `tests/test_clean_env.py`: 105 tests. They use fake files with values containing spaces, quotes, `#` and `=`, CRLF, no final newline, duplicates (the last wins), a missing and an empty required key, a symlink, a file owned by someone else (the effective uid is patched), an unclosed quote, text after a closing quote, idempotence (second run: "already clean", one backup only), mode 600 on the backup and the file, no leftover temp file, and unique fake secrets asserted **absent from stdout and stderr** in a dry run, an apply, a refusal and a forced write failure. One test per `DEFAULTS` entry builds `Settings` with and without the line and compares every field: removal changes nothing. Local run: the file alone, no other test.
- Compose's own parser, on a scratch copy of a server-shaped fake file before and after the cleaner: `docker compose config` shows the same environment except exactly the eight removed names.
- nginx: named tests in `tests/test_edge.py` that read `deploy/nginx.conf` (6 selected by `-k nginx or ...`) pass; code lines of old and new file diff empty. `deploy/nginx-limit-check.sh` (needs Docker, starts containers) was **not** run locally; CI does not run it either.
- CI is the authority for the full suite.

## Operate (on the server) and roll back

Adebayo runs it, because nothing here can read the server.

1. Wait until the CD deploy of the merge has printed `deployed <sha>`; the script is then in `/opt/beyric-note-taker/scripts/`. Do not `git pull` there (the deploy leaves the checkout detached).
2. Dry run, changes nothing: `python3 scripts/clean_env.py /opt/beyric-note-taker/.env`. Read the lists. `MISSING REQUIRED` means stop; the script has already refused.
3. If it reads right: `python3 scripts/clean_env.py /opt/beyric-note-taker/.env --apply`. It saves `.env.bak-<time>` and prints the next command.
4. Only when no meeting is being recorded and no join is due: `bash -c '. deploy/gate.sh; join_due_soon && echo "no join is due"'` (a time printed instead means a join is due; wait), then `docker compose up -d --force-recreate --no-deps web worker scheduler`. A restart does not re-read `.env`. `--no-deps` recreates exactly those three: without it compose runs `migrate` again (checked with `docker compose up --dry-run` on a scratch project; `db` was left running there); `next` needs nothing because no value changes.
5. Check `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8021/health` prints 200 and `docker compose ps` shows web, worker and scheduler up.
6. When satisfied, delete the backup (it holds every secret): `rm /opt/beyric-note-taker/.env.bak-*`.

Roll back: `cp /opt/beyric-note-taker/.env.bak-<time> /opt/beyric-note-taker/.env && chmod 600 /opt/beyric-note-taker/.env`, then the same gate check and recreate command.

For nginx nothing changes on the server: the live `/etc/nginx/sites-available/beyric-notes` is edited by hand and is not replaced by this file.

## Not verified

- **The script has not run on the real server.** It was run on a made-up file with the server's names and fake values, and on compose's parser, on this Mac.
- The server's actual values (which of the eight equal their default) are unknown; the dry run prints the answer by name.
- A server `.env` with syntax the tests did not imagine (a bare `KEY` with no `=`, a text after a closing quote) is refused, not guessed. **Documented limits:** a CRLF inside a quoted multi-line value is turned into LF, and a lone unclosed quote swallows the rest of the file and is refused.
- A kept value that contains `$` (compose expands `$NAME` in unquoted and double-quoted values) makes `--apply` refuse and names the key; single-quoted values are literal and do not block.
- `deploy/nginx-limit-check.sh` and a live `nginx -t` were not run against the trimmed file.

## Follow-ups

- When Vexa's cleanup is done: remove the `vexa` services, the two `${...:?}` checks, the Vexa section from `.env.example`, `LAYOUT` and `REQUIRED` in the script, and the Vexa rows in the README, in one change.
- `OWNER_PASSWORD`: delete the line by hand once no database rebuild is planned (the plaintext password is the thing to retire).
- `PAYSTACK_PUBLIC_KEY` and `FLUTTERWAVE_PUBLIC_KEY` guard test-versus-live keys only; they could go if that guard is not wanted.
