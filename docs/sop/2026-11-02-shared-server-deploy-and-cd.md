# SOP: Shared-server deploy and continuous deployment

## What shipped
Deployment reworked for the first real server, a Contabo VPS shared with Haven (production and staging) and Novex, plus a simple CD pipeline. Branch `feature/note-taker-mvp`, PR innocent98/beyric-note-taker#1. Commit: `feat: run Vexa in compose and deploy main over SSH`.

## Why
A read-only check of the server (2026-11-02) found:

| Finding | Problem for the old plan |
|---|---|
| `haven_api` holds `127.0.0.1:8000`; Haven staging holds 8001 and 3001 | The app's port and Vexa's terminal port would collide |
| No provider firewall; Novex's `0.0.0.0:6432` was reachable from the internet | Vexa's Makefile publishes 8056, 3001 and 8100 on every interface, so anyone could have driven bots on our OpenAI key |
| `sudo` needs a password | Only the nginx and certbot steps can need it |
| 4.8 GB of 7.8 GB free, shared with production | An unbounded Chrome bot could starve Haven |

## How
- **Vexa inside our compose project** (`vexa`, `vexa-db`, `vexa-storage`, one-shot `vexa-storage-init`): the same images, environment and bucket setup as Vexa's `deploy/lite/Makefile` at `31122f9`, but the gateway is published only on `127.0.0.1:18056`, the terminal and agent ports not at all, `~/.claude/.credentials.json` is never mounted, and `vexa` has `mem_limit: 3g`. The app calls `http://vexa:8056`. Rejected: running Vexa's Makefile with `HOST_*_PORT` overrides (it still binds all interfaces) and relying on a firewall the provider does not give us.
- **Network split and random Vexa secrets** (from an automated security review of the first version): `appdb` and `vexainternal` are internal networks, so the Vexa container, which runs Chrome on untrusted meeting pages, cannot reach the app database, and the app cannot reach Vexa's database or storage. `VEXA_DB_PASSWORD` and `VEXA_STORAGE_SECRET` in `.env` replace Vexa's fixed `postgres` / `vexa-secret-key`. `web` publishes through `default` only (internal networks cannot publish), so nginx still arrives as `172.28.0.1`.
- **App port 8020** on `127.0.0.1`; the compose network stays `172.28.0.0/24` (free on the server).
- **nginx** follows the server's convention (Adebayo, 2026-11-02): `sites-available/beyric-notes` linked into `sites-enabled`, HTTP only in the repo, and `certbot --nginx` adds HTTPS. It shares `http {}` with other sites, so zones are prefixed (`notes_login`, `notes_general`), `limit_req_status` sits in our server block, and nothing turns on HTTP/2 for the shared 443 address.
- **Cloudflare proxied** (Adebayo's choice): `set_real_ip_from` Cloudflare's published ranges plus `real_ip_header CF-Connecting-IP`, inside our server block, so rate limits and the app's login limit see visitors' real addresses. Direct hits on the server IP keep their own address, so the header cannot be forged from outside Cloudflare. Cloudflare SSL mode must be Full (strict). Cloudflare's 100 s limit can cut off a long notes rewrite (524).
- **Path** `/opt/beyric-note-taker`, matching `/opt/haven` and the others.
- **CD key**: a dedicated key, not the owner's existing one, because a key stored in GitHub is readable by any workflow change and the owner's key opens a full shell on a server running Haven and Novex. The forced command limits it to `deploy/deploy.sh <sha>`; deleting its line revokes it.
- **CD, kept simple** (rejected the Timpbills setup of GHCR images, staging and encrypted env files as too much for one internal user): `ci` passes on a push to `main` → `.github/workflows/deploy.yml` SSHes in with a key whose forced command is `deploy/deploy.sh`, passing only the SHA. The script checks the SHA is on `origin/main`, takes a backup, builds on the server, starts, waits for `/health`, and starts the previous commit again on failure. Secrets stay in `.env` and `.env.vexa` on the server and are never overwritten.
- **Key creation** is `deploy/vexa-api-key.sh`: curl inside the container (the Lite image has no `python3` on its path, so the old README's snippet would have failed), with retries because the admin API comes up after the gateway's health check.

## What's involved

| Path | Change |
|---|---|
| `compose.yaml` | Vexa services and volumes, `127.0.0.1:8020`, `VEXA_BASE_URL=http://vexa:8056` |
| `deploy/vexa.env.example` | Template for the server-only `.env.vexa` |
| `deploy/vexa-api-key.sh` | Creates the Vexa user and `vxa_bot_` key |
| `deploy/deploy.sh` | Server-side deploy with rollback to the previous commit |
| `.github/workflows/deploy.yml` | CD after `ci` on `main` |
| `deploy/nginx.conf` | Port 8020, prefixed zones, no `http2` |
| `deploy/README.md` | Rewritten for this layout |
| `.env.example` | `VEXA_IMAGE_TAG` |

## Verification
Local, 2026-11-02: the whole stack came up from the new `compose.yaml` (Vexa healthy, storage bucket created, migrate exited 0, `web` healthy on `127.0.0.1:8020`, the worker reached `http://vexa:8056/meetings` with a minted key, 200). `deploy/vexa-api-key.sh` printed a `vxa_bot_` key against a cold Vexa. `nginx -t` passed in `nginx:1.24` (the server's version) with a stub certificate. `shellcheck` clean on all three scripts. Vexa used about 540 MB idle. After the network split, also local: `vexa` cannot resolve `db` but still reaches `api.openai.com`; `web` cannot resolve `vexa-db`; the worker still reaches both `db` and `vexa`. **Not yet verified**: anything on the server, a bot call through this layout, the CD workflow end to end.

## Operate / roll back
See `deploy/README.md` steps 4, 8 and 9. Stop Vexa alone: `docker compose stop vexa`. Remove everything this app put on the server: `docker compose down` (add `-v` to delete its data), delete `/etc/nginx/conf.d/beyric-notes.conf` and reload nginx, and remove the `beyric-notes-cd` line from `~/.ssh/authorized_keys`.

## Follow-ups
- Backups stay on the same server.
- The Novex port 6432 exposure is handled in its own session, outside this repo.
