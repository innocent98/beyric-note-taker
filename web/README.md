# web: the Next.js front door

Next.js (App Router, TypeScript) in front of the FastAPI app. It renders the pages in `app/` and
proxies every other path to FastAPI (`rewrites.fallback` in `next.config.ts`), so the Jinja pages
keep working while pages move over one at a time. Plan:
`docs/superpowers/specs/2026-11-02-nextjs-migration-plan.md`. What shipped and how to operate it:
`docs/sop/2026-11-02-nextjs-scaffold.md`.

```bash
cd web
npm ci
npm run lint && npm run typecheck && npm test   # the CI checks
npm run build                                    # standalone output in .next/standalone
```

Run it against a local FastAPI (`uvicorn app.main:create_app --factory`, port 8000):

```bash
FASTAPI_INTERNAL_URL=http://127.0.0.1:8000 npm run dev      # http://localhost:3000
```

| Where | What |
|---|---|
| `app/` | Pages Next owns (`/`, `/login`, `/meetings/[id]`, `/templates`, `/vocabulary`, `/privacy`, `/terms`, `/styleguide`), the `/logout` handler, `sitemap.ts`, `robots.ts`, `manifest.ts`, `llms.txt`, `healthz` |
| `lib/api/` | Typed client for the JSON API: `schema.d.ts` is generated (`npm run gen:api`), `client.ts` is the one fetch wrapper, `server.ts` and `browser.ts` are the two ways to call it |
| `components/app/` | The owner app screens and their forms |
| `proxy.ts` | Nonce Content-Security-Policy, only for the pages Next owns (its matcher) |
| `components/ui/` | React versions of the Jinja macros in `app/templates/components.html`; each is shown in `/styleguide` |
| `styles/` | `tokens.css` (the only colour literals), `components.css`, `pages.css` |
| `lib/routes.ts` | The list of pages Next owns; add a page here, in `proxy.ts`'s matcher, and under `app/` (a test checks the three agree). Remove a path from all three to hand it back to the Jinja page |

Adding a page: create `app/<path>/page.tsx`, add `"/<path>"` to `NEXT_PAGES` and to the matcher in
`proxy.ts`. Until then the path is served by FastAPI.

Owner screens: `docs/sop/2026-11-03-nextjs-owner-screens.md`. To try them against a scratch compose stack:
`node scripts/owner-e2e.mjs --next http://127.0.0.1:8020 --password "$OWNER_PASSWORD"` (HTTP flow) and
`scripts/owner-browser-check.mjs` (Chromium, screenshots); seed the states with `scripts/seed-owner-states.sql`.
Never against the pilot: both write to the database.
