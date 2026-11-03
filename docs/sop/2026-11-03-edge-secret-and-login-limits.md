# SOP: edge secret for the client address, and tighter login limits

## What shipped
FastAPI now believes `X-Forwarded-For` only on a request that carries a shared secret (`X-Edge-Secret`) that nginx adds; trusting a proxy by IP address is gone. The login limits are 5 failed attempts per 5 minutes per client address plus 10 failed attempts per hour per account. Branch `fix/login-limits-and-edge-secret`, one PR against `main` (not merged, not deployed). Closes the blocker in `docs/spike/nextjs-scaffold/forwarded-for-trust.md`.

## Why
The limiter keyed on `X-Forwarded-For`, which uvicorn applied when the peer was a "trusted proxy" (the Docker bridge gateway `172.28.0.1`, and `next` at `172.28.1.10`). Docker rewrites the source of any connection from another network, or from the host's loopback, to that gateway, so the peer address cannot tell nginx from a hostile container. Any container on the shared server (the worst case is a compromised Vexa container running Chrome on untrusted meeting pages) could send a forged `X-Forwarded-For`, reset its own limit, or burn the owner's attempts. The meetings hold confidential transcripts and open sign-up (10 pilot accounts) comes next, so the gap had to close first. Adebayo chose, on 2026-11-03, "do what's right from the start": an edge secret (option 2 in the spike note).

## How
| Piece | Decision |
|---|---|
| Who adds the secret | **nginx**, on every proxied request, from a root-only file (`/etc/nginx/snippets/beyric-edge-secret.conf`) that `deploy/nginx.conf` includes. `proxy_set_header` replaces any `X-Edge-Secret` or `X-Forwarded-For` a client sent. The value is in no git repo. |
| Next | Passes the headers on unchanged: the fallback rewrite forwards them as received (checked live), and `web/lib/api/server.ts` adds `x-edge-secret` to the headers its server-side calls forward. Next holds **no secret and no check** (compose gives it no env file): validation lives in one place, FastAPI. A request that reaches Next without passing nginx has no valid secret to forward, so FastAPI ignores its address. |
| FastAPI | New `app/edge.py`: `client_address(request, settings)`. With `EDGE_SHARED_SECRET` set: believe the forwarded address only if `X-Edge-Secret` matches (`hmac.compare_digest` on bytes, so a non-ASCII header is a mismatch, not a crash). The forwarded address is the **last** `X-Forwarded-For` entry (the one the trusted edge wrote), must parse as an IP, and is normalised; anything else falls back to the socket peer. |
| uvicorn | Runs with `--no-proxy-headers` (`compose.yaml`) so it never rewrites the peer from `X-Forwarded-For` before the app looks. `--forwarded-allow-ips` is gone. A test reads `compose.yaml` to keep it that way. |
| Rollout safety | `EDGE_SHARED_SECRET` unset: the app starts, logs a **warning** at startup (production only), and falls back to the old rule, now inside the app: believe `X-Forwarded-For` from the peers in `TRUSTED_PROXY_IPS` (default `172.28.0.1`) plus `172.28.1.10`. That fallback is forgeable and exists only until the secret is set; with the secret set it is ignored entirely. A secret shorter than 32 characters also warns. The app never refuses to start. |
| Limiter | `LoginLimiter` has two sliding windows: per client address (5 / 300 s) and per account (10 / 3600 s), checked and counted together under one lock before the password is compared. A refused attempt is not counted, so hammering while locked does not extend the wait, and an address over its limit does not spend the account's budget. Account names are compared lower-cased and trimmed, capped at 254 characters. Unknown accounts are limited exactly like known ones (the key is only the name given). |
| Success | A good login gives back **its own** address attempt and clears that account's failures. It no longer clears all of the address's failures: once accounts exist, a visitor with an account of their own could otherwise log in to it between guesses at someone else's. (The old code reset the address on success.) |
| Account for now | The login has only a password, so the account key is the constant `owner` (`services.OWNER_ACCOUNT`). The accounts work passes the email instead (`services.log_in(..., account=email)`). |
| 429 message | Both limits answer with the same body: `{"error": {"code": "too_many_attempts", "message": "Too many failed attempts. Try again later."}}` (was "...Try again in 15 minutes."). The lockouts are now 5 minutes or up to an hour, and saying which would leak which counter tripped. `code` is unchanged; the FE guide says to switch on `code`. |
| Kept | nginx's edge limit on POST `/login` and `/api/session` (6 a minute, burst 3): it sits in front of, and does not conflict with, the app's 5 per 5 minutes. |
| Rejected | Option 1 (`next` on host loopback, `network_mode: host`): closes only the `next` path, cannot be tested on Docker Desktop, and does not help a Vercel deployment later. Next validating the secret itself: puts a secret in a second service for no gain. Refusing to start without the secret: turns a missed deploy step into an outage. |

## What's involved
| Path | Change |
|---|---|
| `app/edge.py` (new) | `client_address`, `warn_if_unsafe`, header names |
| `app/config.py` | `edge_shared_secret`; `trusted_proxy_ips` now a transitional app setting (default `172.28.0.1`) |
| `app/auth.py` | `LoginLimiter` (two windows, `succeeded`), new constants |
| `app/services.py` | `log_in` uses `edge.client_address`, the account key, the new message |
| `app/main.py` | startup warning |
| `app/api.py`, `docs/openapi.json`, `web/lib/api/schema.d.ts` | docstring says the new limits (schema file regenerated with `npm run gen:api`; only a comment changed) |
| `compose.yaml` | `--no-proxy-headers`, header comment rewritten |
| `deploy/nginx.conf` | `include .../beyric-edge-secret.conf`, comments |
| `deploy/nginx-limit-check.sh` | dummy snippet; new checks that nginx replaces a client's `X-Edge-Secret` and `X-Forwarded-For` |
| `deploy/README.md`, `.env.example` | `EDGE_SHARED_SECRET`, "Edge secret" section with the operator order, `TRUSTED_PROXY_IPS` marked transitional |
| `web/lib/api/server.ts`, `web/tests/server-bits.test.tsx` | forward `x-edge-secret` on server-side calls (proxy code only; no UI files) |
| `tests/test_edge.py` (new), `tests/test_web.py`, `tests/test_api.py` | see Verification |
| `docs/spike/edge-secret/` | `compose.e2e.yaml`, `e2e.sh`, `client.py`, `capture_limits.py`, `e2e-output.txt` |
| `docs/fe-integration-guide-owner-app.md`, `docs/fe-captures/owner-app/80,81` | new limit text and messages, recaptured live |

## Verification
| Claim | How | Result |
|---|---|---|
| Forged `X-Forwarded-For` without the secret, or with a wrong one, is ignored; with the right secret it is honoured; the gateway peer is not trusted once a secret is set | `tests/test_edge.py` (unit and through `/login`) | pass |
| Window sizes, per-account counter across addresses, unknown accounts, case folding, refused attempts not counted, success refunds one | `tests/test_edge.py` limiter tests | pass |
| Unset secret: warning logged, app still starts, old trust rule still applies | `tests/test_edge.py` | pass |
| Full stack: nginx (the real `deploy/nginx.conf`, minus the edge login limit so the app's own limits answer) -> Next -> FastAPI -> Postgres in Docker, visitors and a "hostile container" at separate addresses | `docs/spike/edge-secret/e2e.sh`, output `e2e-output.txt` | **live, all PASS** |
| Through nginx two visitors have separate limits; a client-sent `X-Forwarded-For` / `X-Edge-Secret` changes nothing | e2e A | live |
| A hostile container going to `next:3000` or `web:8000` directly, with a new forged address each time (no secret, or a wrong one), is limited as one client (`401 x5, 429`) | e2e A | live (this is the gap, closed) |
| Account limit: 3 visitors x 4 wrong passwords: the 10th failure locks the account; a fresh visitor with the **right** password gets 429 | e2e A | live |
| After a restart the owner logs in through nginx (200); the secret never appears in a response (`/`, `/login`, `/privacy`, `/api/session`) | e2e A | live |
| Unset secret: startup warning appears; behaviour as before, including the forgeable address (a hostile container is never limited) | e2e B | live |
| nginx replaces a client's `X-Edge-Secret` and `X-Forwarded-For`; login edge limit unchanged | `deploy/nginx-limit-check.sh` | live (real nginx container) |
| `compose.yaml` renders with `--no-proxy-headers` and no `--forwarded-allow-ips` | `docker compose config` | live |
| Not exercised: the real server's nginx, Cloudflare (`CF-Connecting-IP` handling is unchanged), Docker on Linux (the e2e ran on Docker Desktop; the masquerade behaviour that makes the gateway untrustworthy was measured earlier, see the spike note), and Vercel as a host for Next | | reasoned only |

## Operate / roll back
**Order matters; the exact commands are in `deploy/README.md`, section 6, "Edge secret".**
1. Generate once: `SECRET=$(openssl rand -hex 32)`. Write the nginx snippet (`/etc/nginx/snippets/beyric-edge-secret.conf`, mode 600, root) holding `proxy_set_header X-Edge-Secret "<value>";`.
2. Add `include /etc/nginx/snippets/beyric-edge-secret.conf;` to the live site file `/etc/nginx/sites-available/beyric-notes` next to the other `proxy_set_header` lines (edit by hand; the live file has certbot's lines). `sudo nginx -t && sudo systemctl reload nginx`. Until the app has the secret nothing changes for visitors.
3. Add `EDGE_SHARED_SECRET=<same value>` to `/opt/beyric-note-taker/.env`.
4. Deploy (the merge to `main` deploys), or `docker compose up -d --force-recreate web`, outside any `bot_sent` or `processing` meeting.
5. Check: `docker compose logs web | grep EDGE_SHARED_SECRET` shows no warning; the README spoof test still ends in `429`; log in once.

If the PR deploys **before** steps 1 to 3, nothing breaks: the app logs the warning and trusts the old addresses, as today. If steps 1 and 2 are done but 3 is not, the same. If nginx has the include but the file is missing, `nginx -t` fails and nginx keeps the old config: fix the file, do not skip the test. If `.env` has the secret but nginx does not send it, every visitor shares the address of `next` (one bucket of 5 failures per 5 minutes for everyone; no one is wrongly let in): do step 2. **Roll back:** revert the PR and redeploy; the nginx include line can stay (it is only a header the old app ignores) and `EDGE_SHARED_SECRET` in `.env` is ignored by old code. Rotate the secret by repeating steps 1 to 4 with a new value.

## Residual risk
- **Account lockout is a denial-of-service lever.** The account counter locks the owner out of *new* logins for up to an hour after 10 failures from anywhere, and anyone who knows the URL can cause that with about 10 requests (2 addresses are enough). It cannot be used to guess the password, and an existing session cookie (30 days) keeps working, but the owner can be kept out of a fresh login. This is the standard trade-off of a per-account limit and the one Adebayo chose; with pilot accounts it applies per account. Options if it bites: let a device holding a previously valid session or a "remembered device" cookie bypass the account counter, and email the owner on a lockout.
- Until step 3 of the rollout, and while `TRUSTED_PROXY_IPS` is honoured, the old forgeable trust still applies. Removing the fallback is a follow-up.
- A process that can read the server `.env` or nginx's root-only file has the secret; at that point it already has the owner password and the database password.
- uvicorn's access log now shows the peer (Next's address), not the visitor; nginx's access log has the real address. The app does not log the resolved address.
- In-memory counters reset on restart and are per process (one `web` process today). With more workers, or a restart loop, the limit weakens; move them to the database or Redis with the accounts work.

## Follow-ups
- Remove the transitional `trusted_proxy_ips` fallback and the startup warning's second branch after the secret is live (one small PR).
- Accounts work: pass the email as `account` to `log_in`; run the password hash against a dummy hash for unknown accounts so response time does not reveal whether an account exists (today there is only the owner, so there is nothing to enumerate); min password length 8 with accounts (Adebayo: later).
- `web/components/app/login-form.tsx` has a comment saying "15 minutes" (UI file, owned by the parity branch): update it there.
- Optionally log the resolved client address for failed logins.
