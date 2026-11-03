# SOP: Deployment files (Task 7, files only)

> **Update 2026-11-03:** `TRUSTED_PROXY_IPS` and the trusted-proxy rule described below no longer exist. The client address now comes from an edge secret (`docs/sop/2026-11-03-edge-secret-and-login-limits.md`); the setting is ignored if still present in a `.env`.

## What shipped
Everything needed to deploy on one Ubuntu 24.04 server, written and validated locally: `deploy/nginx.conf`, `deploy/backup.sh`, `deploy/README.md`, and `compose.yaml`, `Dockerfile`, `.env.example` changes. Commit: `chore: add deployment setup`. **The server steps have not been run**; Task 7 stays open in the checklist.

## Why
The app needs HTTPS, a trusted proxy for the login limit, a daily database backup, and a safe way to reach Vexa, whose ports are published on all interfaces.

## How
- **nginx on the host**, not in Docker: certbot stays a plain package with a systemd renewal timer. Certificate issued with the `webroot` method from a temporary HTTP-only site, so renewal works with nginx running.
- **`X-Forwarded-For` is overwritten** (`proxy_set_header X-Forwarded-For $remote_addr`), not appended, so a client cannot spoof the address the app rate-limits on. `Host` and `X-Forwarded-Proto` are set too.
- **Trusted proxy**: `web` is published on `127.0.0.1:8020` only (8000 was taken on the shared server; see the shared-server SOP). Connections from host nginx arrive at the container from the Docker bridge gateway, so `compose.yaml` pins the project network to `172.28.0.0/24` (gateway `172.28.0.1`) and defaults `TRUSTED_PROXY_IPS` to it with `${TRUSTED_PROXY_IPS-172.28.0.1}`: unset uses the gateway, set but empty trusts no proxy (checked: uvicorn ignores `X-Forwarded-For` then). Because `web` binds only to loopback, the trusted gateway is reachable only from the server itself. `db` now has `restart: unless-stopped`, and `/health` returns 503 when the database is down. Rejected: running nginx as a container (more moving parts for certbot) and `network_mode: host`.
- **Regenerate timeout**: `location ~ ^/meetings/[0-9]+/regenerate$` has `proxy_read_timeout 300s`; everything else keeps 60 s. `nginx -t` passed in a throwaway `nginx:stable` container.
- **Edge limits**: POST `/login` 6/min per address (burst 3, 429), 20 req/s overall, 1 MB body cap. The app's own 5-in-15-minutes limit remains.
- **Headers**: nginx adds only HSTS (no subdomains/preload), `X-Frame-Options: DENY` and `Permissions-Policy`. It does not repeat the app's CSP, `nosniff`, `Referrer-Policy` or `Cache-Control`, to avoid duplicates. `listen 443 ssl http2;` is used because Ubuntu 24.04 ships nginx 1.24, which lacks the standalone `http2 on;`; newer nginx only warns.
- **Vexa**: superseded 2026-11-02. Vexa now runs inside the same compose project and `web` and `worker` reach it as `http://vexa:8056`; see `2026-11-02-shared-server-deploy-and-cd.md`.
- **Worker non-root** (deferred from Task 6): `worker` now has `user: app`. The Dockerfile creates `/srv/data/audio` owned by `app`, so a new `audiodata` volume is writable; an old root-owned volume needs a one-time `chown` (documented).
- **Backup**: `deploy/backup.sh` runs `pg_dump` in the `db` container, gzips, tests the archive, writes mode 600 in a 700 directory via a temp file, keeps 14 days, `set -euo pipefail`.

## What's involved
`deploy/nginx.conf`, `deploy/backup.sh`, `deploy/README.md`, `compose.yaml` (network, `worker.user`, `extra_hosts`, `VEXA_BASE_URL`, forwarded-ips default, comment), `Dockerfile` (audio dir), `.env.example` (`TRUSTED_PROXY_IPS`).

## Verification
Local only (2026-11-02): `docker build` succeeded; as user `app` the image can write `/srv/data/audio`; `docker compose config` renders (published `127.0.0.1:8000`, `host-gateway`, `172.28.0.1`, `user: app` on web and worker); `nginx -t` passes in a throwaway `nginx:alpine` with a stub certificate (only the `http2` deprecation warning from a newer nginx); `bash -n` and `shellcheck` clean on `backup.sh`. **Not verified**: anything on a server, certbot issuance, that the container sees `172.28.0.1` as nginx's address, the Vexa key-minting commands against the pinned commit, `backup.sh` end to end, restore, container-to-host Vexa reach.

## Operate / roll back
See `deploy/README.md` (steps 5 to 8). Rollback of this change: revert the commit; the old stack ran `worker` as root with no Vexa host mapping.

## Follow-ups
Run the README on a real server and fix whatever it gets wrong; off-server backup copies; uptime monitoring; key rotation procedure.
