# SOP: Outlook phase O, M2, the Microsoft OAuth client

## What shipped
`app/microsoft_oauth.py`: the Microsoft side of the calendar connect (spec `docs/superpowers/specs/2026-11-04-outlook-oauth-design.md` sections 4.1 to 4.4): the consent URL, redeeming the code, the ID token checks, refreshing an access token (the answer carries a NEW refresh token), and the error mapping. Plus the vault purpose `ms_refresh_token`, the `MICROSOFT_*` settings, and the production start-up checks. Branch `feature/outlook-m2-oauth-client`, PR to `main` (number in the PR list). **Nothing calls it yet**: M3 (connect, callback) and M4 (Graph source, token cache) use it; no route, no migration, no behaviour change. Nothing Microsoft is reachable and no Outlook wording appears anywhere.

**Fixtures are documented shapes, not captures.** The M0 run has not happened. Every body in `tests/fixtures/microsoft/` is named `documented_*__replace_with_m0_capture.json`, and the tests that depend on them say so. The list under "To confirm with the M0 run" is the contract for that replacement.

## Why
Same flow as Google (`app/google_oauth.py`) with three differences the design settles: tenants (`tid` and `oid` are the identity, never an email), **refresh tokens rotate on every use**, and the client secret expires (12 months) and must be watched.

## How
| Piece | Decision |
|---|---|
| Consent URL | `https://login.microsoftonline.com/{consumers\|common}/oauth2/v2.0/authorize`, authority from `MICROSOFT_AUTHORITY`; code flow, PKCE S256 (the same `google_oauth.new_pkce`), `response_mode=query`, `prompt=select_account`, no `login_hint`; scopes `openid profile offline_access` plus Graph `User.Read` and `Calendars.Read` (fixed tuple `SCOPES`, spec 4.2); redirect URI exactly `PUBLIC_BASE_URL + /api/calendar/microsoft/callback` |
| Redemption | `exchange_connect` posts to the same authority's token endpoint; secret, code and verifier only in the form body; `calendar_http` client (no redirects, no proxy from the environment, a timeout on every stage). Returns a `Grant`: identity, the scopes Microsoft SAYS were granted, the refresh token (or None) and the access token (kept as `Secret`, for M3's probe, never stored) |
| ID token (spec 4.4) | Not signature-checked (it came from the token endpoint over TLS in answer to our secret and verifier; OIDC Core 3.1.3.7; **recorded for review and to be revisited if Microsoft sign-in is ever added**). Checked: `aud` equals our client id, `exp` and `iat` with 60 s skew, `nonce` (constant-time), `tid` a GUID, `iss` exactly `https://login.microsoftonline.com/{tid}/v2.0` for that `tid`, an `oid` (else `sub`, at most 64 characters). Then the tenant: `MICROSOFT_ALLOWED_TENANTS` when set; and with authority `consumers` the `tid` must be the personal-account tenant (a work tid there is refused even though Microsoft cannot send one: defence in depth) |
| Identity | `Identity(tenant_id, subject, account_kind, name, preferred_username)`. Key is tenant plus `oid`; `preferred_username` and `name` are labels. `account_kind` is `personal` when `tid` is `9188040d-6c67-4c5b-b112-36a304b66dad`, else `work` |
| Refresh | `refresh(Secret, tenant_id=)` posts to the connection's own tenant endpoint (a GUID, never `common`; anything else is a `ValueError`, a bug). Returns `Refreshed(access_token, lifetime, refresh_token or None, scopes)`. The `id_token` in the answer is never read. It touches no database: the caller (M4's `MicrosoftTokenCache`) does the compare-and-swap save of the new token |
| Error mapping (spec 4.3) | `invalid_grant`, `interaction_required`, `consent_required` (HTTP 400 or 401) give `AuthRevoked`, with `expired=True` only when `error_codes` has 700082. `invalid_client` gives `ClientRejected` (a `TransientError`: reconnecting would not help the user) with `secret_expired` true for 7000222, which M4 turns into an admin alert. Everything else (429, 5xx, `temporarily_unavailable`, a malformed or non-JSON answer, a timeout) is `TransientError`. At redemption any failure is `MicrosoftError` (the callback shows `microsoft_failed`), `invalid_client` is `ClientMisconfigured(secret_expired)`, and a tenant refusal is `TenantNotAllowed` (`tenant_not_allowed`) |
| What messages carry | The HTTP status, the short `error` string only when it is a plain lowercase name, and the integer `error_codes`. Never `error_description` (free text, trace and correlation ids), never a body, URL, token, code or tenant id of someone else's organisation. Tested with sentinel strings through logs, `str`, `repr` |
| Dead-grant reason | `AuthRevoked` gained `expired: bool` (default false, so every Google path is unchanged); `calendar_states.reason_for` returns `expired` for it, `error` for an unreadable token, else the existing rules |
| Scopes | `has_calendar_scope` compares after stripping `https://graph.microsoft.com/`, case-insensitively, and exactly: `Calendars.ReadBasic`, `Calendars.ReadWrite`, `Calendars.Read.Shared` and look-alike hosts do not count. M3 puts it in the registry row's `granted_features` |
| Vault | New purpose `ms_refresh_token` in `vault.PURPOSES`: a Google blob copied into a Microsoft row (or the reverse, or another user's) fails to open. Same `TOKEN_ENC_KEY`; no new key |
| Settings | `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_CALENDAR_ENABLED` (false), `MICROSOFT_AUTHORITY` (`consumers`; only `consumers` or `common` load), `MICROSOFT_ALLOWED_TENANTS` (GUIDs, normalised), `MICROSOFT_SECRET_EXPIRES_ON` (`YYYY-MM-DD`, with `secret_expiry_due`: true from 30 days before), `MICROSOFT_PUBLISHER_VERIFIED` (false). All optional while the flag is false |
| Start-up (spec 8.1 #2) | `auth.check_start` also runs `microsoft_oauth.check_settings`: in production an id without a secret, or the flag on without a full client, refuses to start; `vault.check_settings` now also needs `TOKEN_ENC_KEY` once `MICROSOFT_CLIENT_ID` is set. Development and test start without the client |
| Rejected | Putting refresh in M4's cache module (the HTTP call and its error mapping belong beside the other Microsoft round trips; the cache only decides when to call and how to save); a JWT library for the ID token (see above); a subclass of `AuthRevoked` for the idle expiry (the sync records the exception type name, and `error_text` matches `AuthRevoked`); reading `error_description` for 700082 (free text) |

## What's involved
- New: `app/microsoft_oauth.py`, `tests/test_microsoft_oauth.py` (118 cases), `tests/fixtures/microsoft/documented_*__replace_with_m0_capture.json` (9 files).
- Changed: `app/config.py` (settings and validators), `app/vault.py` (purpose, key rule), `app/auth.py` (`check_start`), `app/calendar_source.py` (`AuthRevoked.expired`), `app/calendar_states.py` (`reason_for`).

## Verification
- Tests first: authorize URL parameters (exact dict), redemption request (method, URL, form body, nothing in the query), a personal and a work account, `oid` then `sub`, labels never the identity, 13 ID-token refusals (audience, expiry, issued-at, nonce, `tid`, three bad issuers, no subject, wrong types), tenant allow-list and the `consumers` rule, every redemption failure shape, 9 malformed token answers, rotation (the new refresh token comes back, differs from the old, the old is never reused), the refresh request URL and form, `common` and made-up tenants refused before any request, dead-grant and idle-expiry mapping, expired and wrong client secret, 14 transient shapes, redirects not followed, scope recognition (16 cases), settings validators, production start-up (6 cases), vault purposes, and sentinel strings absent from logs, `repr`, `str` and exception text.
- Suites run before the push: `ruff check .`, `ruff format --check .`, the new file, `test_vault`, `test_calendar_states`, `test_calendar_providers`, `test_calendar_connect`, `test_gcal_sync`, `test_edge`, `test_migrations`, `test_accounts`, `test_calendar_api`, `test_calendar_recurring`, `test_calendar_settings_api`, `test_gcal_client`, `test_gcal_events`, `test_gcal_rules`, `test_gcal_sync_job`, `test_join_runner`, and `pytest --collect-only -q tests` (5,020 tests, no collection error). CI runs the rest.
- **Not verified live:** nothing here has talked to Microsoft. Every answer is a documented shape.

## To confirm with the M0 run
Each line is an assumption this code makes; the captures either settle it or change the code.
1. The `scope` string of the token answer: short names (`Calendars.Read User.Read profile openid email`) for personal accounts, full Graph URLs for work (both handled; the captured form replaces the fixtures).
2. A personal account's ID token carries `oid`. If not, `sub` is used with `tid`, which is pairwise per app: confirm it is stable across two sign-ins.
3. `iss` for a personal account is `https://login.microsoftonline.com/9188040d-6c67-4c5b-b112-36a304b66dad/v2.0`, `tid` is that GUID, and `aud` is our client id as a plain string.
4. `token_type` is `Bearer` and `expires_in` is a number (a string is also read).
5. The real lengths of the access and refresh tokens (limits here: 16,384 and 8,192 characters; a longer one would fail the connect).
6. The refresh answer returns a new `refresh_token` every time, and refreshing against `https://login.microsoftonline.com/{consumer tenant GUID}/oauth2/v2.0/token` works for a personal account (M4 uses the connection's `tenant_id`, never `consumers`).
7. Error answers: the HTTP status and `error_codes` of an expired or removed grant (`invalid_grant`, 400 or 401?), of 700082 (not reproducible in an afternoon: marked "not verified" if so), of `interaction_required`, and of a wrong client secret (7000215) versus an expired one (7000222, not reproducible without waiting).
8. `prompt=select_account` and `response_mode=query` are honoured for a personal account, and a work account sent to the `consumers` endpoint is refused by Microsoft before it reaches us.
9. Whether the consent screen for a personal account says "unverified" (decides `MICROSOFT_PUBLISHER_VERIFIED` and the explainer; spec S2).

## Operate / roll back
**Server steps before merge: none.** The six new variables are optional while `MICROSOFT_CALENDAR_ENABLED` is false and nothing reads them yet; set them (names only: `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_AUTHORITY`, `MICROSOFT_CALENDAR_ENABLED`, `MICROSOFT_ALLOWED_TENANTS`, `MICROSOFT_SECRET_EXPIRES_ON`) only at switch-on. A mistyped `MICROSOFT_AUTHORITY`, `MICROSOFT_ALLOWED_TENANTS` or `MICROSOFT_SECRET_EXPIRES_ON` stops the app at start with a message naming the variable. Roll back by reverting the PR: no data was written.

## Opus review fixes (2026-11-09, review of #150)
- **Secrets out of `repr` and start-up errors:** `microsoft_client_secret`, `google_client_secret`, `session_secret`, `token_enc_key`, `token_enc_key_previous`, `owner_password`, `edge_shared_secret`, `resend_api_key`, `openai_api_key` and `vexa_api_key` are `Field(default="", repr=False)`, and the settings model sets `hide_input_in_errors=True`. Tested with sentinel values through `repr`, `str` and a forced start-up `ValidationError`.
- The nonce check fails closed on an empty expected nonce; `tid="consumers"` with a matching `iss` is refused (the GUID check has its own case); the `_authority_url` guard has a test; `Identity.name` and `preferred_username` are out of `repr`; `check_id_token` is private (`_check_id_token`), so only `exchange_connect` reaches it.
- Notes kept: M3 reuses `google_oauth.new_pkce`; a refresh answer with no new refresh token is M4's (keep the old one, no swap).

## Follow-ups
- M3 (connect, callback, disconnect, probe, flag), M4 (Graph source, `MicrosoftTokenCache` compare-and-swap, the expired-secret alert), M5 (event normaliser).
- The 30-day secret reminder (`secret_expiry_due`) is a helper only; M4 wires it to the admin alert.
- Replace the nine fixtures with the redacted M0 captures and settle the list above.
