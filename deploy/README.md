# Deploying Beyric Note Taker

One Ubuntu 24.04 server runs everything. It may be shared with other apps (the first server is),
so nothing here takes a public port except through nginx.

| Piece | Where it runs | Reachable from |
|---|---|---|
| Front door: `next` (Next.js) | Docker Compose in this repo | `127.0.0.1:8020` only; this is the port nginx proxies to |
| App: `db`, `migrate`, `worker`, `web` (FastAPI) | Docker Compose in this repo | `web` on `127.0.0.1:8021` only (operator and rollback); visitors reach it through `next` |
| Vexa Lite: `vexa`, `vexa-db`, `vexa-storage` | the same Compose project | the app, as `vexa:8056`; the server only, on `127.0.0.1:18056` |
| nginx + certbot | **on the host**, not in Docker | the internet, ports 80 and 443 |

- Vexa is a ready-made image, `vexaai/vexa-lite:v012`; there is nothing to install or build. It
  runs from `compose.yaml` rather than Vexa's own Makefile because the Makefile publishes ports
  8056, 3001 and 8100 on every interface and mounts `~/.claude/.credentials.json` if it exists.
  Here Vexa's terminal and agent ports are not published at all, and no host file is mounted.
- `vexa` is capped at 3 GB of memory, so a bot running Chrome cannot starve other apps.
- Four networks: `default` (web, worker, vexa; has internet), `appdb` (the app database; no
  internet), `vexainternal` (Vexa's database and storage; no internet) and `front` (`next` and `web`
  only; pinned to `172.28.1.0/24`, so check the server has no other network on it: `docker network
  inspect $(docker network ls -q) | grep Subnet`). A bot runs Chrome on
  untrusted meeting pages, so the Vexa container cannot reach the app database at all.
- nginx connects to `127.0.0.1:8020`, which is `next`, and `next` connects to FastAPI as
  `http://web:8000`. `next` renders the few pages it owns (`/privacy`, `/terms` and some review
  pages) and proxies every other path to FastAPI unchanged, so the Jinja pages, `/health` and the
  login keep working as before. The visitor's address reaches FastAPI like this: nginx sets
  `X-Forwarded-For` (the address Cloudflare reports) and a secret header `X-Edge-Secret` on every
  request, `next` passes both on unchanged, and FastAPI believes the forwarded address **only on a
  request that carries the right secret** (`EDGE_SHARED_SECRET`, below). It never trusts a proxy
  by its IP address: Docker rewrites every container's and the host loopback's connection to the
  bridge gateway, so any container on the server could pose as nginx. Without the secret a request
  is identified by its socket peer, so a container that forges `X-Forwarded-For` cannot choose the
  address the login limit counts (`uvicorn` runs with `--no-proxy-headers` for this reason). Only
  processes on the server can reach ports 8020 and 8021; none of them can read the secret.
- `next` is capped at 512 MB of memory (`mem_limit`) and holds no secrets. Measured on a Mac it
  used 37 MB idle and 65 MB at peak under 240 requests, so the cap is a ceiling, not a need.
  Building its image (`next build`) peaked at about 0.8 GB for its largest process, once per
  deploy; measure on the server (SOP `2026-11-02-nextjs-scaffold.md`).
- Minimum server: **4 GB memory, 2 vCPUs** free for this app. Vexa used about 540 MB idle in a
  local run and about 1.4 GB with a bot in a call.

Paths below: the app lives in `/opt/beyric-note-taker` (like `/opt/haven`), backups in `~/backups/beyric-notes`, and the
domain is `notes.beyrictech.com`. Steps marked **(sudo)** need the server owner's password.

## 0. What the owner must supply

- [ ] SSH access as a user in the `docker` group, and someone who can run `sudo`.
- [ ] A DNS `A` record for the domain pointing at the server. Proxied through Cloudflare (orange
      cloud) is supported: `deploy/nginx.conf` restores each visitor's real address from
      `CF-Connecting-IP`. Set Cloudflare's SSL/TLS mode to **Full (strict)**; "Flexible" would
      loop forever against the HTTP-to-HTTPS redirect certbot adds.
- [ ] An email address for Let's Encrypt expiry notices.
- [ ] An OpenAI API key with a spend limit set in the OpenAI dashboard.
- [ ] The owner's login password for the app.

## 1. Ports: check from outside

Only 22, 80 and 443 should answer. Docker bypasses `ufw` for published ports, so the real guard
is that `compose.yaml` publishes nothing on a public interface. Check from **your laptop**:

```bash
for p in 22 80 443 8020 8021 18056 8056 3001 8100 5432; do
  printf '%s: ' "$p"; nc -z -G 3 SERVER_IP "$p" && echo open || echo closed
done
```

Expected: 22, 80, 443 open; the rest closed. (Another app's port may show open; that is that
app's to fix, not this one's.) Run it again in step 9.

## 2. Server preparation

Docker, the compose plugin, nginx and certbot must be installed. Then:

```bash
ls -l ~/.claude/.credentials.json    # expected: "No such file or directory"
```

### Read access to the repository

The server pulls the code with its own read-only deploy key.

```bash
ssh-keygen -t ed25519 -N '' -C beyric-notes-server -f ~/.ssh/beyric_notes_github
cat >> ~/.ssh/config <<'EOF'
Host github-beyric-notes
    HostName github.com
    User git
    IdentityFile ~/.ssh/beyric_notes_github
    IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
cat ~/.ssh/beyric_notes_github.pub
```

Add that public key to the GitHub repository as a **read-only deploy key**
(`gh repo deploy-key add <file> --repo innocent98/beyric-note-taker --title server`), then:

```bash
sudo mkdir -p /opt/beyric-note-taker && sudo chown "$USER": /opt/beyric-note-taker    # (sudo)
git clone git@github-beyric-notes:innocent98/beyric-note-taker.git /opt/beyric-note-taker
```

## 3. Secrets: `.env.vexa` and `.env`

Both files live only on the server, are git-ignored, and are never overwritten by a deploy.

```bash
cd /opt/beyric-note-taker
umask 077
T=$(openssl rand -hex 32)
cat > .env.vexa <<EOF
ADMIN_TOKEN=$T
ADMIN_API_TOKEN=$T
INTERNAL_API_SECRET=$(openssl rand -hex 32)
NEXTAUTH_SECRET=$(openssl rand -hex 32)
JWT_SECRET=$(openssl rand -hex 32)
TRANSCRIPTION_SERVICE_URL=https://api.openai.com
TRANSCRIPTION_SERVICE_TOKEN=PASTE_OPENAI_KEY
TRANSCRIPTION_MODEL=whisper-1
EOF
cp .env.example .env
```

- Live transcription (`TRANSCRIPTION_*`) must stay on: without it Vexa returns no speaker names.
  It costs about $0.36 per meeting hour.
- The secrets are minted so Vexa never falls back to values printed in its own source.
  `ADMIN_TOKEN` is the key to Vexa's admin API; treat it like a root password.
- Vexa's own database and storage get random passwords from `.env` (below), unlike Vexa's
  Makefile, which uses fixed ones.

Edit `.env`:

| Variable | Value |
|---|---|
| `POSTGRES_PASSWORD`, `VEXA_DB_PASSWORD`, `VEXA_STORAGE_SECRET` | each `openssl rand -hex 24` |
| `VEXA_API_KEY` | from step 4 |
| `DATABASE_URL`, `VEXA_BASE_URL` | leave commented; compose sets both |
| `OPENAI_API_KEY` | the OpenAI key |
| `OWNER_EMAIL`, `OWNER_PASSWORD` | The admin account's email and a long password. Migration `0006` creates the account from them (once; later changes to these two do nothing), and the sign-in is then email plus password. **Both must be in `.env` before the first deploy that contains migration `0006`**, or that migration fails and rolls back. `OWNER_PASSWORD` can be removed after it has run |
| `SESSION_SECRET` | `openssl rand -hex 48`. The app refuses to start if it is shorter than 32 characters (it is the only key behind every session cookie) |
| `APP_ENV`, `COOKIE_SECURE` | `production`, `true` |
| `EDGE_SHARED_SECRET` | `openssl rand -hex 32`, the **same value** nginx sends as `X-Edge-Secret` (section 6, "Edge secret"). Empty = not configured: the app starts and logs a warning, and every client is limited as the proxy's address (one shared login limit for all visitors, so set it). `X-Forwarded-For` is never believed without it |
| `BOT_LEFT_ALONE_SECONDS` | Optional, default `300`. How long the bot stays in a call after the last other person stops speaking before it leaves on its own (sent to Vexa as `automatic_leave.max_time_left_alone`, in milliseconds; Vexa's own default is 10 minutes, and `0` sends nothing so Vexa's default applies). **It is a silence rule, not a headcount**: a short window can cut a quiet meeting (a long screen-share with no talking, a reading break), and the notes then cover only what the bot heard. Change it by editing `.env` and recreating `web` and `worker` (`docker compose up -d --force-recreate web worker`, with no meeting `bot_sent` or `processing`): no image rebuild, no code deploy. It applies to bots sent after that; a bot already in a call keeps the window it was sent with |
| `PUBLIC_BASE_URL`, `MAIL_PROVIDER`, `RESEND_API_KEY`, `MAIL_FROM` | Pilot accounts, section 10. `RESEND_API_KEY` from Resend (the sending domain verified there); leave `MAIL_PROVIDER` empty. Without a key the app runs, no mail goes out and password sign-up stays off |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Optional until Sign in with Google is wanted (both empty = the feature is off). The OAuth client's redirect address is `https://notes.beyrictech.com/api/auth/google/callback` |
| `TOKEN_ENC_KEY` | **Required before the deploy once `GOOGLE_CLIENT_ID` is set** (or `CALENDAR_ENABLED=true`): the app refuses to start in production without it, so a server that has Google sign-in but no key crash-loops `web` (this took the site down for about 12 minutes on 2026-11-03). `openssl rand -base64 32`; it encrypts the calendar refresh tokens and pasted calendar links. Keep a copy somewhere safe (a password manager): it is not in the database or a backup, and **losing it means every user reconnects their calendar**. See "Before a deploy that needs a new setting" in section 8. `TOKEN_ENC_KEY_PREVIOUS`, `TOKEN_ENC_KEY_VERSION` (default `1`) are for rotation only |
| `MAX_ACTIVE_BOTS` | Optional, default `1`. Bots on meetings at once across all users; a new meeting past it answers `409 bot_busy` |
| `AUDIO_RETENTION_DAYS` | Optional, default `7`. The same value is shown on `/privacy` (compose passes it to `next`) |
| `SCHEDULER_DISPATCH_SECONDS`, `SCHEDULER_SYNC_SECONDS` | Optional, defaults `10` and `60`: how often the `scheduler` service's two loops run (and how often a standby retries the leader lock). Leave them alone unless told to |

## 4. Start Vexa and create the app's key

```bash
cd /opt/beyric-note-taker
docker compose up -d vexa              # also starts vexa-db and vexa-storage
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18056/health    # 200
deploy/vexa-api-key.sh                 # prints vxa_bot_... once; put it in .env as VEXA_API_KEY
```

The first start pulls images and takes a few minutes. Run `vexa-api-key.sh` once: a second run
fails because the Vexa user already exists.

## 5. Start the app

```bash
docker compose up -d --build           # migrate runs first, then web and worker, then next
docker compose ps
curl -s http://127.0.0.1:8020/health   # ok  (through next, to FastAPI and the database)
curl -s http://127.0.0.1:8020/healthz  # ok  (next itself)
curl -s http://127.0.0.1:8021/health   # ok  (FastAPI directly)
docker compose exec worker python -c "import urllib.request; print(urllib.request.urlopen('http://vexa:8056/health', timeout=5).status)"   # 200
```

`web` and `worker` run as the non-root user `app`; the image makes `/srv/data/audio` theirs. A
volume left by an older version is owned by root and needs, once:
`docker compose run --rm --user root worker chown -R app:app /srv/data/audio`.

## 6. HTTPS with nginx and Let's Encrypt (sudo)

Same layout as the server's other sites: a file in `sites-available`, linked into `sites-enabled`,
then `certbot --nginx` adds HTTPS to it.

```bash
cd /opt/beyric-note-taker
sudo cp deploy/nginx.conf /etc/nginx/sites-available/beyric-notes
sudo ln -s /etc/nginx/sites-available/beyric-notes /etc/nginx/sites-enabled/beyric-notes
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d notes.beyrictech.com
```

`nginx -t` checks every site on the server, so a mistake stops before anything reloads. The file
shares `http {}` with other sites, so its zone names are prefixed (`notes_login`,
`notes_general`), and `limit_req_status` and the Cloudflare real-IP settings sit inside its own
server block, so they change nothing for Haven or Novex.

If certbot's challenge fails while the record is proxied (usually Cloudflare's "Always Use HTTPS"
redirecting the challenge), switch the record to DNS only, run certbot, and switch it back.

After a later change to `deploy/nginx.conf`, do not copy it over the live file (that would drop
certbot's lines): edit `/etc/nginx/sites-available/beyric-notes` by hand, then `sudo nginx -t &&
sudo systemctl reload nginx`.

### Edge secret (one-time, and the order matters)

nginx proves to the app that a request came through it by adding `X-Edge-Secret` to every
request. The value is a random string kept in two places and in no git repository: the app's
`.env` (`EDGE_SHARED_SECRET`) and a root-only nginx snippet that `deploy/nginx.conf` includes.
**Never print it**: it must not reach the terminal, shell history, a chat or a log. The commands
below keep it in a shell variable and write it straight to the two files. Run them in bash, as
the deploy user, in this order, so nothing breaks half way (until step 4 the app trusts addresses
the old way; nothing about login changes for the owner):

```bash
# 1. Generate once; write the root-only nginx snippet and the .env line from the same variable.
SECRET=$(openssl rand -hex 32)
sudo mkdir -p /etc/nginx/snippets
sudo install -m 600 -o root -g root /dev/null /etc/nginx/snippets/beyric-edge-secret.conf
printf 'proxy_set_header X-Edge-Secret "%s";\n' "$SECRET" \
  | sudo tee /etc/nginx/snippets/beyric-edge-secret.conf >/dev/null
# Replace any existing EDGE_SHARED_SECRET line (an empty one from .env.example, or an old value
# when rotating) with a fresh one; neither command prints a value.
sed -i '/^EDGE_SHARED_SECRET=/d' /opt/beyric-note-taker/.env
[ -n "$(tail -c1 /opt/beyric-note-taker/.env)" ] && echo >> /opt/beyric-note-taker/.env  # last line needs its newline
printf 'EDGE_SHARED_SECRET=%s\n' "$SECRET" >> /opt/beyric-note-taker/.env
chmod 600 /opt/beyric-note-taker/.env
unset SECRET

# 2. Prove both files hold the same value, without showing either (prints "same"):
cmp -s <(sed -n 's/^EDGE_SHARED_SECRET=//p' /opt/beyric-note-taker/.env) \
       <(sudo sed -n 's/.*X-Edge-Secret "\(.*\)";/\1/p' /etc/nginx/snippets/beyric-edge-secret.conf) \
  && echo same || echo DIFFERENT

# 3. Put the include line in the live site file (once), under the other proxy_set_header lines:
#      include /etc/nginx/snippets/beyric-edge-secret.conf;
#    (it is in deploy/nginx.conf; edit /etc/nginx/sites-available/beyric-notes by hand, see above)
sudo nginx -t && sudo systemctl reload nginx

# 4. Deploy (or `docker compose up -d --force-recreate web`) once no meeting is bot_sent or
#    processing. The app reads the secret at startup.
```

`printf` is a shell builtin, so the value is not in any process's command line either. Do not
`cat`, `echo` or `diff` either file; to look at one, use `sudo grep -c X-Edge-Secret <file>` (a count).

Check it after the deploy:

```bash
docker compose logs web | grep EDGE_SHARED_SECRET           # no output: the startup warning is gone
curl -sI https://notes.beyrictech.com/health | grep -i x-edge-ok     # x-edge-ok: true
```

`X-Edge-Ok` says whether *that request* carried the right secret, so `true` through the public
URL means nginx, Next and the app agree on it; it never contains the secret. (`deploy.sh` checks
`/health` on `127.0.0.1:8020`, which skips nginx, so it reads `false` there: that is expected.)
Afterwards the spoof test in section 9 still ends in `429`. If `docker compose logs web` later
shows `arrived without a valid X-Edge-Secret header`, a request reached `next` without going
through nginx, or nginx lost the include: look at the site file first.

The app never refuses to start without the secret; it logs `EDGE_SHARED_SECRET is not set ...`
at startup, and until the secret is set every visitor is limited as the proxy's address (one shared
login limit; the forwarded address is never believed). Rotate
by repeating steps 1 to 4 with a new value, replacing the old `.env` line and snippet (a short
window of shared login limits between steps 3 and 4 is harmless). `deploy/nginx-limit-check.sh`
proves the nginx half (a client-sent `X-Edge-Secret` or `X-Forwarded-For` is replaced) without a
server.

What the site does: proxies to `127.0.0.1:8020`, the Next.js front door (60 s, or 300 s for
`/meetings/<id>/regenerate`); takes the visitor's address from `CF-Connecting-IP`, trusted only
from Cloudflare's published ranges; **overwrites** `X-Forwarded-For` with that address and adds the `X-Edge-Secret` header, so a client
cannot dodge the login limit; limits POST `/login` to 6 a minute per address (burst 3, then 429)
and all traffic to 20 requests a second; adds HSTS (1 year), `X-Frame-Options: DENY` and a
`Permissions-Policy`. The app sets its own CSP, `nosniff`, `Referrer-Policy` and `Cache-Control`.

Cloudflare closes a request after 100 s (free plan). Only rewriting notes can take that long; the
page then shows Cloudflare's 524 error, and the rewrite is likely, but not verified, to finish on
the server. Reload the meeting page.

## 7. Daily backup

```bash
BACKUP_DIR=~/backups/beyric-notes /opt/beyric-note-taker/deploy/backup.sh     # once by hand
crontab -e
```

```
17 3 * * * BACKUP_DIR=$HOME/backups/beyric-notes /opt/beyric-note-taker/deploy/backup.sh >> $HOME/backups/beyric-notes/backup.log 2>&1
```

The script dumps the app database inside the `db` container, gzips and tests it, writes mode 600,
and deletes dumps older than 14 days. Recordings are temporary and not backed up; Vexa's own
database is not backed up either (it only holds meeting records the app has already copied).

### Restore (practise once)

```bash
cd /opt/beyric-note-taker
FILE=~/backups/beyric-notes/notetaker-YYYYMMDDTHHMMSSZ.sql.gz
docker compose exec -T db psql -U postgres -c 'CREATE DATABASE notetaker_restore'
gzip -dc "$FILE" | docker compose exec -T db psql -U postgres -d notetaker_restore -v ON_ERROR_STOP=1 -q
docker compose exec -T db psql -U postgres -d notetaker_restore -c 'SELECT count(*) AS meetings, count(notes) AS with_notes FROM meetings'
docker compose exec -T db psql -U postgres -c 'DROP DATABASE notetaker_restore'
```

Over the live database (disaster recovery only):

```bash
docker compose stop web worker
docker compose exec -T db psql -U postgres -c 'DROP DATABASE notetaker' -c 'CREATE DATABASE notetaker'
gzip -dc "$FILE" | docker compose exec -T db psql -U postgres -d notetaker -v ON_ERROR_STOP=1 -q
docker compose up -d web worker
```

## 8. Continuous deployment, updating and rolling back

Merging a PR into `main` deploys it: `ci` runs, and when it passes `.github/workflows/deploy.yml`
connects over SSH and runs `deploy/deploy.sh <sha>`. That script refuses any commit not on
`origin/main`, backs up the database, checks out the commit, builds (both images: FastAPI and
Next.js), starts, and waits up to two minutes until all three checks pass: `127.0.0.1:8020/health`
(through `next` to FastAPI and the database), `127.0.0.1:8020/healthz` (`next` itself) and
`127.0.0.1:8021/health` (FastAPI directly). If the new version never turns healthy it starts the
previous commit again (with `--remove-orphans`, so a commit from before `next` existed does not
leave the `next` container holding port 8020) and the workflow fails.
A failing `docker compose up` (compose exits non-zero when a dependency is unhealthy, for example a
crash-looping `web`) does not stop the script: the health check decides, and the rollback runs when
the services do not come up. A build that fails restores the previous checkout and starts nothing.
The rollback also starts any container compose left in the `Created` state (`next`, waiting on a
dependency), since a stuck one would leave the site down. `tests/test_deploy_script.py` runs the
script against stand-ins for `docker`, `git` and `curl` to prove each case.

**The deploy that introduces `next`** moves port 8020 from `web` to `next`, so for a few seconds
the site answers 502 while `web` is recreated on 8021 and `next` starts. Do it when no meeting is
`bot_sent` or `processing` (the worker is not restarted, but the pilot should not be mid-call).
Before it, check the server has about 1.5 GB of free memory for the Next.js build (`free -h`).

### Before a deploy that needs a new setting

The app refuses to start on a missing or unusable required setting (that is the safe behaviour), and
`deploy/deploy.sh` then rolls back, so a forgotten setting costs a failed deploy rather than an
outage, but it still blocks the release. A PR that adds a required environment variable lists it
under **"Server steps before merge"** in its body; do those steps on the server first.

The current one: with `GOOGLE_CLIENT_ID` set in `.env`, add `TOKEN_ENC_KEY` before the deploy that
contains it. This appends a fresh key without printing it, then recreates the two services that
read it:

```bash
cd <the project directory on the server>   # where compose.yaml and .env are
grep -q '^TOKEN_ENC_KEY=.' .env || { [ -z "$(tail -c1 .env)" ] || echo >> .env; \
  printf 'TOKEN_ENC_KEY=%s\n' "$(openssl rand -base64 32)" >> .env; }
docker compose up -d --force-recreate web worker
```

Then copy the value from `.env` into your password manager (`grep '^TOKEN_ENC_KEY=' .env`, in a
private terminal). Losing it means every user reconnects their calendar.

### Deploy key setup (once)

GitHub needs a private key to log in. It gets its own key, not the owner's: a key stored in
GitHub can be read by any workflow change, and the owner's key opens a full shell on a server that
also runs Haven and Novex. This key can do one thing, run `deploy/deploy.sh` with a commit SHA,
and removing its one line from `authorized_keys` revokes it without touching anyone's access.
On the server:

```bash
ssh-keygen -t ed25519 -N '' -C beyric-notes-cd -f ~/beyric-notes-cd
echo "command=\"/opt/beyric-note-taker/deploy/deploy.sh\",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty $(cat ~/beyric-notes-cd.pub)" >> ~/.ssh/authorized_keys
```

Store these repository secrets, then delete `~/beyric-notes-cd*` from the server:

| Secret | Value |
|---|---|
| `DEPLOY_SSH_KEY` | contents of `~/beyric-notes-cd` (the private key) |
| `DEPLOY_HOST` | the server IP |
| `DEPLOY_USER` | the SSH user |
| `DEPLOY_KNOWN_HOSTS` | `ssh-keyscan -t ed25519 SERVER_IP` output |

### By hand

```bash
cd /opt/beyric-note-taker && git fetch origin main
deploy/deploy.sh "$(git rev-parse origin/main)"
```

Roll back: re-deploy an older commit of `main` with `deploy/deploy.sh <sha>`, or revert the PR on
GitHub so CD deploys the revert. To take the Next.js front door out of the path alone (and keep the
rest of the release), point nginx at FastAPI directly: change `proxy_pass http://127.0.0.1:8020`
to `http://127.0.0.1:8021` in `/etc/nginx/sites-available/beyric-notes` (three places),
`sudo nginx -t && sudo systemctl reload nginx`. The Jinja pages, including `/privacy` and `/terms`,
then answer from FastAPI as before. If the bad version ran a migration, older code may not run on the
new schema: `docker compose run --rm migrate alembic downgrade -1` first, or restore the backup the
deploy took (step 7).

Update Vexa: change `VEXA_IMAGE_TAG` (in `.env`, default `v012`) only after a test call on the new
tag; `docker compose up -d vexa` applies it, and setting the old tag back rolls it back.
After the upgrade, check the language column is still wide: `docker compose up -d vexa-schema-fix`
runs the idempotent fix again, and `docker compose logs vexa-schema-fix` should say "language is
already varchar(32)" or "widened". Vexa's own migrations may touch that table, so keep this step.

## 9. Verification checklist

From **outside** the server:

```bash
curl -sI http://notes.beyrictech.com/ | head -3                                   # 301 to https
curl -sI https://notes.beyrictech.com/login | grep -i -E 'HTTP|strict|x-frame|content-security'
curl -s -o /dev/null -w '%{http_code}\n' https://notes.beyrictech.com/health      # 200
```

- [ ] Ports as in step 1.
- [ ] HTTPS shows the login page with a valid certificate; one `Content-Security-Policy`, plus HSTS
      and `X-Frame-Options`.
- [ ] Login works and the cookie is `Secure`.
- [ ] Spoof test: wrong passwords with a different made-up `X-Forwarded-For` each time go `401`,
      then `429`, and changing the header does not bring `401` back:

  ```bash
  HOST=https://notes.beyrictech.com; JAR=$(mktemp)
  TOKEN=$(curl -s -c "$JAR" "$HOST/api/session" | sed -n 's/.*"csrf_token": *"\([^"]*\)".*/\1/p')
  for i in 1 2 3 4 5 6 7 8; do
    curl -s -b "$JAR" -o /dev/null -w '%{http_code}\n' -X POST "$HOST/api/session" \
      -H 'Content-Type: application/json' -H "X-CSRF-Token: $TOKEN" \
      -H "X-Forwarded-For: 10.9.8.$i" -d '{"password":"wrong"}'
  done; rm -f "$JAR"
  ```

  Run it in bash. To test the Cloudflare header, send `-H "CF-Connecting-IP: 10.7.7.$i"` straight
  to the server (`--resolve notes.beyrictech.com:443:SERVER_IP`); through Cloudflare that header
  gets a 403 from Cloudflare itself. Expect your own address in `docker compose logs web`. The test
  locks your own address out of login for 5 minutes (the app's per-address limit), and each failed
  attempt also counts against the account's 10 an hour, so do not repeat it within the hour.
- [ ] `docker compose ps`: `db`, `web`, `vexa` healthy; `worker` up; no Vexa errors in
      `docker compose logs worker`.
- [ ] Other apps on the server still answer after the install.
- [ ] After a reboot, the stack and nginx come back.
- [ ] A backup exists and the scratch restore shows meetings and notes.
- [ ] A merge to `main` deploys through CD.
- [ ] Task 8 acceptance: a real meeting end to end.

## 10. Pilot accounts: deploy and open

Sign-up exists in the code but **stays closed**: `app_state.signups_open` is `false` and nothing in
a deploy changes it. The order matters:

1. **Ownership scoping must already be live** (accounts core, migrations 0006 and 0007). Check:
   `GET /api/session` as the admin shows `user.is_admin: true`.
2. **Add the mail and Google variables to `.env`** (section 3) *before* the deploy. A missing
   `RESEND_API_KEY` is not an error; it only means no mail and no password sign-up. Never put these
   values in a message or a commit.
3. **Migration `0008`** (one nullable column on `auth_tokens`) runs with the deploy. It is
   additive: old code ignores the column. Roll back with
   `docker compose run --rm migrate alembic downgrade 0007` before starting old code (it only
   drops that column; verification links not yet used then stop working).
4. **nginx, by hand** (never copy `deploy/nginx.conf` over the live file: it would drop certbot's
   lines). In `/etc/nginx/sites-available/beyric-notes`, add the two `limit_req_zone` lines next to
   the existing ones (they sit above `server {`), and the two `location` blocks for the account
   routes and the waitlist next to the login location, exactly as in `deploy/nginx.conf`
   (the comments there say what each does). Then `sudo nginx -t && sudo systemctl reload nginx`.
   Prove it without touching the server: `deploy/nginx-limit-check.sh` runs the repository's file in
   a throwaway container (the account routes share one counter of 11 requests, then 429; the
   waitlist 6; the Google callback and `GET /api/pilot` are not counted).
5. **Check**: `curl -s https://notes.beyrictech.com/api/pilot` answers
   `{"state":"closed","prefill_email":null,"google_signin":false}` (`true` once the Google variables
   are set).
6. **Open the pilot** only when you decide to, as the admin, with a signed-in session. Until the
   admin screens exist, use curl with a cookie jar (the CSRF token comes from `GET /api/session`;
   placeholders in capitals):

   ```bash
   J=$(mktemp); H=https://notes.beyrictech.com
   CSRF=$(curl -s -c $J -b $J $H/api/session | python3 -c 'import json,sys;print(json.load(sys.stdin)["csrf_token"])')
   CSRF=$(curl -s -c $J -b $J -H "X-CSRF-Token: $CSRF" -H 'content-type: application/json' \
     -d '{"email":"ADMIN_EMAIL","password":"ADMIN_PASSWORD"}' $H/api/session \
     | python3 -c 'import json,sys;print(json.load(sys.stdin)["csrf_token"])')
   curl -s -b $J -H "X-CSRF-Token: $CSRF" -H 'content-type: application/json' -X PUT $H/api/admin/pilot \
     -d '{"signups_open":true,"max_accounts":10,"default_hours_cap_seconds":18000,"password_signup_enabled":false}'
   ```

   Keep `password_signup_enabled` `false` until a real verification mail has been delivered once
   (sign up with Google, or hand out reset links, until then). Do not invite more than one or two
   testers until several bots at once are supported (spec section 15, phase D).
7. **Close it again** with the same call and `"signups_open":false`, or in the database:
   `update app_state set signups_open = false;`.

Operating notes: the worker sends the mail and removes deleted accounts (`docker compose logs
worker`; a mail provider that is down only delays mail, and `GET /api/admin/mail-failures` lists
what failed). Delete one account for good with
`docker compose exec web python scripts/delete_user.py someone@example.com` (it asks first).

The `scheduler` service (`python -m app.scheduler`, same image as `worker`, limited to 256 MB) does
the time-critical work of the calendar features; see `docs/sop/2026-11-03-phase-c-scheduler.md`.
Only one copy acts, through a Postgres advisory lock on its own connection, so a second replica
is a standby and takes over within about 10 seconds. It needs no setting and nothing depends on
it: if it cannot start, `web` and `next` are unaffected. `deploy/deploy.sh` waits up to 60
seconds after `up` for a heartbeat newer than the deploy (`app_state.scheduler_heartbeat_at`) and
rolls back when there is none. Check it by hand with `docker compose logs --tail 20 scheduler` and
`docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA' <<< "select now() - scheduler_heartbeat_at from app_state;"`
(a few seconds when healthy).

## Follow-ups

- Backups stay on the same server. Copy them elsewhere once the owner picks a place.
- No rotation procedure for the Vexa key: mint a new one with the admin API, update `.env`,
  `docker compose up -d web worker`, then delete the old key.
- Nothing monitors the stack. Add an uptime check on `/health` (it returns 503 when the database
  is down).
