# SOP: Next.js scaffold (stream A of the migration)

## What shipped
`web/`, a Next.js 16 app (App Router, TypeScript, npm, Node 22, standalone output) that becomes the front door on host port `127.0.0.1:8020`. It renders the pages it owns (`/privacy`, `/terms` live; `/welcome` and `/styleguide` for review) and proxies every other path to FastAPI, which moves to `127.0.0.1:8021`. Branch `feature/nextjs-scaffold`, PR against `main` (not merged). Plan: `docs/superpowers/specs/2026-11-02-nextjs-migration-plan.md`.

## Why
Adebayo decided on 2026-11-02 to move the frontend to Next.js (designer parity, sleek app, server-rendered public pages). A strangler setup lets pages move one at a time with no nginx change, and the pilot stays up on the Jinja pages meanwhile.

## How
| Piece | Decision |
|---|---|
| Routing | `next.config.ts` `rewrites.fallback` sends any path Next does not own to FastAPI (`FASTAPI_INTERNAL_URL`, baked in at build, so it is a Docker build argument). `skipTrailingSlashRedirect`, so Next adds no redirect in front of FastAPI paths. `experimental.proxyTimeout` 310 s: nginx lets `/meetings/<id>/regenerate` run 300 s, and a 35 s upstream response passed in the check |
| Ownership list | `lib/routes.ts` `NEXT_PAGES` is the one list; `proxy.ts`'s matcher (a literal Next requires) and the pages under `app/` must equal it. `tests/routes.test.ts` fails if they drift |
| CSP | `proxy.ts` (Next 16's name for middleware) sets a per-request nonce CSP only on those pages, so proxied FastAPI responses keep FastAPI's own CSP untouched. `script-src 'self' 'nonce-…' 'strict-dynamic'`, `style-src 'self'`, fonts and images `'self'`, plus `connect-src 'self'` and `manifest-src 'self'` that Next needs. No `unsafe-inline`/`unsafe-eval` in production (dev relaxes both, tested). Pages render per request (`await connection()` in the root layout), the price of a nonce: no static caching for these pages |
| Docker | `web/Dockerfile`: three stages (deps, build, runtime), runtime is `node:22-alpine`, user `node`, 303 MB. Compose service `next`: `127.0.0.1:8020:3000`, `mem_limit: 512m`, healthcheck on `/healthz` (Next itself; `/health` is FastAPI's and passes through), `depends_on web healthy`, no env file, no secrets |
| Compose | `web` publishes `127.0.0.1:8021:8000` (operator and rollback). `--forwarded-allow-ips` now ends with `next`'s pinned address `172.28.1.10`, always, on top of `TRUSTED_PROXY_IPS` (the server's `.env` value `172.28.0.1` needs no change). New network `front` (172.28.1.0/24) holds only `next` and `web` |
| `deploy/deploy.sh` | Builds both images; healthy means `8020/health` (through Next to FastAPI and the database), `8020/healthz` (Next) and `8021/health` (FastAPI) all answer; with no `next` container (a commit from before this) it falls back to the old `/health` check. `up -d --remove-orphans` on both paths, so a rollback to a commit without `next` removes the container that holds port 8020 |
| Design system | `styles/tokens.css`, `components.css` copied unchanged except font URLs (`/fonts/…`, served from `public/fonts`); `pages.css` is the old `app.css` plus the landing. `components/ui/*` is one React component per Jinja macro (Logo, Button, Badge, Alert, Card, Field/SelectField/TextareaField, Segmented, Menu/MenuItem, Dialog, Tabs, NavLink, ListRow, EmptyState, PageHead). Menu and Dialog work as in Jinja (`<details>`, `<dialog>`) with the same delegated listeners in `components/behaviors.tsx` (the only client component) |
| Guards (vitest) | `tests/guards.test.ts`: no colour literal outside `tokens.css` (and `lib/brand.ts`, which the manifest needs and a test keeps equal to the tokens), no inline `style`/`on*` in components, every component shown in `/styleguide`, every file in `components/ui` exported |
| Legal text | `components/legal/*` was generated from `app/templates/{privacy,terms}.html`. `tests/legal.test.tsx` renders them and compares the visible text, every `id` (such as `#google`) and every `href` with the Jinja templates, and checks the entity name. Retention days come from `AUDIO_RETENTION_DAYS` (compose passes the app's value, default 7) |
| Public pages / SEO | Metadata API, canonical, Open Graph, JSON-LD (Organization, SoftwareApplication) on `/welcome`, `sitemap.ts` (`/`, `/privacy`, `/terms`), `robots.ts` (disallows the owner app paths), `llms.txt`, `manifest.ts`, SVG favicon and PNG icons (`scripts/make-icons.sh`). Landing is mounted at `/welcome`, noindex, with the words of the live pilot page (no Teams, no sign-up) in the designer's layout. `/` stays FastAPI's |
| CI | `.github/workflows/ci.yml`: a cheap `changes` job (git diff against the PR base) decides which jobs run; `test` (Python) unchanged; new `web` job (npm ci, lint, typecheck, vitest, build); an always-running `ci` job fails if any needed job failed, so required checks always report. Docs-only changes skip both, Python-only skip `web`, web-only skip `test` |

Rejected: `middleware.ts` (deprecated name in Next 16); `next/link` everywhere (a client navigation to a FastAPI path would only be proxied and fall back to a full load, so `AppLink` uses it only for Next-owned pages); a service worker (not trivial under the strict CSP; follow-up); a runtime-configurable rewrite target (Next bakes it at build).

## What's involved
`web/` (all new), `compose.yaml`, `deploy/deploy.sh`, `deploy/README.md`, `.env.example` (comment), `.github/workflows/ci.yml`, `docs/checklist/master-checklist.md` (Module 2b), `docs/spike/nextjs-scaffold/` (captured proof). No Python under `app/` or `tests/` changed.

## Verification
All run on this Mac against a production build and a real compose stack (`db`, `migrate`, `web`, `next`), 2026-11-02. Captures are in `docs/spike/nextjs-scaffold/`.

| Check | Result |
|---|---|
| `npm run lint`, `typecheck`, `npm test` (20 tests), `npm run build` | pass |
| `scripts/proxy-check.mjs`: 16 steps, each sent through Next and straight to FastAPI and compared (status, `Location`, CSP and cache headers, `Set-Cookie` attributes, body): login page and cookie, 303 to `/login`, 403 without CSRF, 401 wrong password, 303 on login, logged-in pages, POST `/templates` (303), duplicate (400), 300 KB body (400 from FastAPI), static file, `/health`, 404, query string, login limit (401 ×5 then 429, same on both), logout | 16/16, both on a local FastAPI and in compose (`proxy-check-compose.txt`) |
| Header pass-through to an echo stub (`proxy-passthrough-stub.txt`) | query string kept; two `Set-Cookie` headers kept; `X-Forwarded-For` and `-Proto` unchanged; **`Host` is rewritten to `web:8000`, the original arrives as `X-Forwarded-Host`**; a 35 s upstream response returns 200 |
| `scripts/browser-check.mjs` (Chromium via Playwright, light and dark) on `/welcome`, `/privacy`, `/terms`, `/styleguide` | 0 console errors or warnings, 0 CSP violations, hydrated, nonce on 11 scripts, Geist loaded, no inline style markup, client-side navigation keeps the document (`browser-check.txt`). Dialog opens and closes, menu closes on Escape, no horizontal scroll at 375 px (`interaction-check.txt`) |
| CSP is really enforced (`csp-negative-controls.txt`) | inline style attribute, inline `<style>` and a parser-inserted inline script are blocked. An `eval` control was invalid (Playwright bypasses it), so `unsafe-eval` absence is asserted on the header only |
| Memory, 240 requests (`memory-and-deploy.txt`) | `next`: **37 MiB idle, 65 MiB peak, 44 MiB after**; no OOM. `next build` peak 0.8 GB. Image 303 MB. **Measured on an arm64 Mac, not on the server** |
| deploy.sh pieces and rollback | health function passes with all up, fails with `web` stopped; `up -d --remove-orphans` with the previous commit's compose file removed `next` and put `web` back on 8020 (`memory-and-deploy.txt`). The whole script was not run (it requires a SHA on `origin/main`) |
| Not verified | CI on GitHub (runs on the PR); `/privacy` and `/terms` through the real nginx and Cloudflare; the x86_64 server's numbers; the `front` subnet on the server; Safari and Firefox |

## Operate / roll back
Server steps for Adebayo (none done by this session, which never touched the server):
1. Before the deploy: `docker network inspect $(docker network ls -q) | grep Subnet` must show nothing on `172.28.1.0/24`; `free -h` should show about 1.5 GB free for the one-time build.
2. Merging to `main` deploys through CD. Do it when no meeting is `bot_sent` or `processing`: port 8020 moves from `web` to `next`, so expect a few seconds of 502 while `web` is recreated on 8021 and `next` starts. `worker` is not recreated.
3. After: `docker compose ps` (db, web, next healthy); `curl -s http://127.0.0.1:8020/health`, `/healthz`, `http://127.0.0.1:8021/health` all `ok`; `docker stats --no-stream` for `next` memory on the real server (send the number back; the 512 MB cap is a ceiling, 256 MB would still hold the 65 MiB peak); outside: `curl -sI https://notes.beyrictech.com/privacy` has exactly one `content-security-policy` (with a nonce), plus nginx's HSTS and `x-frame-options`; log in through the site once.
4. `.env` needs no change (`TRUSTED_PROXY_IPS=172.28.0.1` stays; `next` is added by compose). Optional `AUDIO_RETENTION_DAYS` is shown on `/privacy` too.
5. Roll back: re-deploy the previous commit (`deploy/deploy.sh <sha>`; it removes `next`), or only drop the front door: point the three `proxy_pass` lines in `/etc/nginx/sites-available/beyric-notes` at `127.0.0.1:8021` and reload nginx.

## Follow-ups
- **X-Forwarded-For trust** (needs a decision, `docs/spike/nextjs-scaffold/forwarded-for-trust.md`): Docker lets any container reach `next` by IP, and Next passes the header through, so a compromised Vexa container could forge the address the login limit keys on. Options: `next` on the host network (needs a Linux test), or a shared secret FastAPI checks (Python). Do one before open sign-up. Message sent to the lead session.
- When `GET /api/session` exists (stream B): move the landing to `/`, show "Open the app" for a logged-in owner in the public header (it always says "Sign in" now), and make `/styleguide` owner-only in production. `STYLEGUIDE_PREVIEW=1` shows it in a local production build.
- `Host` reaches FastAPI as `web:8000`; use `X-Forwarded-Host` (or a configured base URL) for anything that builds absolute URLs, such as the Google OAuth redirect.
- A trailing slash is dropped by Next before proxying (`/login/` serves the login page; FastAPI alone answers 307 to `/login`).
- Stream B's `/api/*` (merged to `main` while this PR was open) goes through the same fallback and was not exercised through Next here: custom request headers such as `X-CSRF-Token` were seen passing in the echo capture, but run `scripts/proxy-check.mjs`-style checks against `/api/session` once both are deployed. A long `/api/meetings/<id>/regenerate` is covered by `proxyTimeout`; nginx's matching 300 s location for `/api/` is the lead's.
- Service worker and offline shell; PNG Open Graph image (`og:image`) and per-page images; Search Console submission of the sitemap.
- Re-measure memory on the server; revisit `mem_limit` when owner screens land (stream C).
- Tokens and fonts exist twice (`app/static`, `web/styles`) until the Jinja pages are retired.
- npm 11 prints a warning that esbuild's postinstall is not allow-listed; vitest runs without it. Revisit when npm enforces it.
